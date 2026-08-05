# 검증 하니스(B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 코퍼스 케이스를 두 DB(MySQL/PostgreSQL)에 실제로 실행하고 결과를 비교해 pass/fail/error를 판정하는 검증 하니스를 구현한다(변환기 없이 pass-through로).

**Architecture:** `케이스 → [변환(pass-through)] → 양쪽 DB 실행 → 결과 비교`. 변환 단계는 주입 가능한 `Transformer` Protocol로 두어 이후 변환 엔진(이슈 C)을 같은 시그니처로 갈아끼운다. `Comparator`는 DB를 전혀 모르는 순수 함수로 분리해 로직 대부분을 DB 없이 단위 테스트한다. `Executor`는 연결·격리(dml ROLLBACK / ddl DROP)·트랜잭션 경계를 담당하며, 드라이버 예외를 하니스 자체 예외(타임아웃/연결/실행)로 번역한다. `Runner`가 kind별 경로를 조립하고 실패를 `stage`로 분류한다.

**Tech Stack:** Python 3.12+, SQLGlot(현재시각 함수 치환), PyMySQL, psycopg(v3), pytest(단위 + `@pytest.mark.integration`), PyYAML.

> **Codex 1차 리뷰 반영**: P1-1(세 kind Transformer 호출 + typed transform 예외), P1-2(드라이버 예외를 typed 하니스 예외로 번역, cleanup은 원예외 보존·정리 실패는 infrastructure), P1-3(탐욕→이분 최대 매칭), P1-4(정수·Decimal 정확 비교, float만 오차), P1-5(upsert 코퍼스를 기존 시드 행 재사용으로 수정), P1-6(MySQL DML/DDL timeout 비보장을 스펙 비범위로), P2-1(fixed_clock allowlist), P2-2(컬럼명·datetime 계약 테스트), P2-3(dml/ddl에도 ordered 허용), P2-4(stage 행렬 Fake 주입 단위 테스트), P2-5(양 DB·catalog 부재 격리 입증), P2-6(실제 CLI exit code 필수), P3(bool-tinyint fail 스냅샷, object type whitelist).
>
> **Codex 2차 리뷰 반영(이 개정판)**: P1-1(dml `post_query`는 필수 유지 — setup만 optional로. 검증 대상이 post_query라 없으면 상태 비교 불가), P1-2(cleanup 실패를 **삼키지 않고** 4우선순위 규칙 구현 — `_cleanup()` 헬퍼 + cleanup Fake 테스트), P1-3(DDL도 **DB 연결 전 변환** — 고유명 계산→subst→transform을 `_run_ddl`에서 미리), P1-4(`InfrastructureFailure` 추가 + Executor **모든 DB 호출**을 번역 경계 안에 + `run_case` 마지막 방어선).

## Global Constraints

- **단일 근거는 스펙**: `docs/superpowers/specs/2026-08-05-validation-harness-design.md`. 어긋나면 스펙을 따른다. **단, 이 개정판은 아래 "스펙 변경 사항"을 스펙에 먼저 반영한 뒤 구현한다**(Task 0).
- **변환기 없음**: `PassThroughTransformer`가 입력 SQL을 그대로 반환한다. 별도 `expected_fail` 플래그를 만들지 않는다. MySQL 전용 문법(백틱, `LIMIT o,c`)이 PG에서 실패하는 건 버그가 아니라 예상된 `fail`이다.
- **세 kind 모두 변환 호출(P1-1, P1-3)**: dql/dml/ddl **모두** 피검증 SQL을 `transformer.transform()`으로 변환한 뒤 PG에서 실행한다. 변환은 **세 kind 모두 DB 연결 전** 공통 `_transform_or_stage()`로 호출하고, 변환 예외(SQLGlot ParseError/ValueError 등)는 stage `transform`/status `fail`로 잡는다. DDL도 예외 없이 **연결 전**에 변환한다: 고유명은 연결 없이 결정되므로 `_run_ddl`에서 `name = safe_object_name(...)` → `my_stmt = subst(case.statement)`(고유명 치환) → `pg_stmt = _transform_or_stage(my_stmt)`를 **먼저** 하고, 그 뒤 두 DB에 연결해 각 경로에 준비된 statement를 넘긴다. (MySQL은 변환 안 한 `my_stmt`, PG는 `pg_stmt`. Transformer는 MySQL→PG 인터페이스라 MySQL 쪽에서 호출할 이유가 없다. transform 실패 시 MySQL에 DDL을 실행하기 전에 걸린다.)
- **stage 분류(스펙 "에러 처리" 표)**: 피검증 SQL(mysql/statement)의 실패만 변환기 품질 문제로 `fail`. 제어 SQL·연결·타임아웃은 `error`.
  - 변환 실패(ParseError 등) → stage `transform`, status `fail`
  - 피검증 SQL의 **PG** 실행 실패 → stage `pg.statement`, status `fail`
  - 비교 불일치 → stage `compare`, status `fail`
  - 피검증 SQL의 **MySQL** 실행 실패 → stage `mysql.statement`, status `error`
  - 제어 SQL(setup/exercise/post_query) 실패(양 DB) → stage `control`, status `error`
  - DB 연결·인증·**타임아웃**·**cleanup 실패** → stage `infrastructure`, status `error`
- **드라이버 예외 번역(P1-2, P1-4)**: `Executor`가 PyMySQL/psycopg 예외를 하니스 자체 예외로 번역한다. **모든 DB 호출**(timeout SET·`cursor()`·`execute`·`description`·`fetchall`·`cursor.close`·`commit`·`rollback`·연결 `close`)이 번역 경계 안이어야 한다 — 그러지 않으면 raw 드라이버 예외가 `run_case`의 `except _StageError`를 뚫고 전체 실행을 중단시킨다.
  - `StatementTimeout` — 피검증/제어 쿼리 자체의 타임아웃(→ 항상 `infrastructure`/`error`)
  - `ConnectionFailure` — 연결·인증 단절(→ `infrastructure`/`error`)
  - `InfrastructureFailure` — **timeout SET·트랜잭션 제어(commit/rollback)·cursor·fetch·close 등 "쿼리 본체가 아닌" DB 호출의 실패**(→ 항상 `infrastructure`/`error`). PG aborted 트랜잭션에서 `SET statement_timeout`이 실패하는 경우 등. 이걸 `SqlExecutionFailure`로 번역하면 PG에서 `pg.statement`/fail로 **오분류**되므로 별도 타입으로 가른다.
  - `SqlExecutionFailure` — **피검증/제어 쿼리 본체**의 SQL 실행 실패(구문/제약 위반 등; stage는 **호출 문맥**이 결정 — 피검증/제어에 따라 fail/error 갈림)
  - 정확한 예외 클래스·SQLSTATE 매핑은 **구현 중 드라이버 실측**으로 확정(계획은 분류 계약만 고정).
  - **`run_case` 마지막 방어선**: 예상 밖 `Exception`(번역 누락 등)도 `infrastructure`/`error`로 잡아 한 케이스가 전체를 중단시키지 않게 한다.
- **Comparator는 DB를 모름(P1-3, P1-4)**: "columns 두 쌍 + rows 두 쌍 + ordered(+ exclude_columns)"만 받는 순수 함수.
  - **컬럼 이름 대응 확인**(개수뿐 아니라 이름 순서까지).
  - unordered는 `Counter` 금지 — **이분 그래프 최대 매칭(Hopcroft–Karp 또는 Kuhn)**. 탐욕적 매칭은 반례가 있어 금지(오차 안 값이 여러 상대와 매칭 가능할 때 순서 의존으로 완전 매칭을 놓침).
  - 값 비교: **정수·Decimal은 정확 비교**, **float가 관여할 때만 `math.isclose`(1e-9)**. Decimal은 float로 붕괴시키지 않고 `Decimal.compare`로 스케일만 흡수. bool은 상대가 **정확히 정수 0/1**일 때만 대응(근사 아님).
- **fixed_clock은 세션 주입 아님(P2-1)**: PG는 `SET`으로 `now()`를 못 고정한다. 현재시각 함수를 고정 리터럴로 SQLGlot AST 치환한다(양 DB 동일, 피검증·제어 SQL 모두). **지원 함수 allowlist**: `NOW()`(MySQL→`exp.Anonymous("NOW")`, PG→`exp.CurrentTimestamp`), `CURRENT_TIMESTAMP`(→`exp.CurrentTimestamp`), `CURDATE()`/`CURRENT_DATE`(→`exp.CurrentDate`), `SYSDATE()`(→`exp.Anonymous("SYSDATE")`), `LOCALTIMESTAMP`. 그 외 현재시각 함수는 **미지원(비범위)** — 만나면 명시적으로 `transform` 아닌 별도 처리 없이 그대로 두되, 코퍼스에는 allowlist 함수만 쓴다. 노드 매핑은 구현 중 실측 확인.
- **타임아웃 정책(P1-6, 스펙 변경)**: **PG는 `statement_timeout`으로 모든 문장에 timeout 보장**. **MySQL은 `MAX_EXECUTION_TIME`이 read-only SELECT에만 적용되므로 DML/DDL timeout은 비보장(스펙 비범위)**. watchdog+KILL은 MVP 과대. 코퍼스 케이스는 매달리지 않으므로 실무 위험 없음. timeout 발생 시 항상 `infrastructure`/`error`.
- **DDL/DML 정리 우선순위(P1-2)**: cleanup(dml ROLLBACK, ddl ROLLBACK→DROP→COMMIT, 실행 전 DROP)은 **실패를 삼키지 않는다**. `except Exception: pass`는 금지. 4가지 규칙:
  1. **본체 성공 + cleanup 실패** → `infrastructure`/`error` (성공을 덮어쓴다)
  2. **본체 실패 + cleanup 성공** → 원래 stage 유지
  3. **본체 실패 + cleanup 실패** → 원래 stage 유지 + `reason`에 cleanup 실패도 누적 기록
  4. **실행 전(pre-clean) DROP 실패** → 본체를 실행하지 않고 `infrastructure`/`error` (`DROP IF EXISTS`는 객체가 없어도 성공하므로, 실패는 연결·권한·트랜잭션 상태 문제다)

  구현: 본체는 `try/finally` 대신 결과/예외를 명시적으로 잡고, cleanup은 `_cleanup(ex, steps) -> str | None`(성공 시 None, 실패 시 오류 메시지) 헬퍼로 모든 단계를 시도하며 오류를 누적한다. 그 뒤 위 4규칙으로 최종 `CaseResult`/`_StageError`를 결정한다. cleanup 실패 Fake 테스트 필수.
- **DDL 트랜잭션 경계·고유명**: 실행 전 DROP은 독립 COMMIT, cleanup은 ROLLBACK→DROP→COMMIT. DDL 고유명은 `sqlbridge_{safe_id}_{object_name}` (case_id 하이픈을 `_`로 정규화, 63자 초과 시 절단+해시 접미사). object type은 whitelist(`table`)만 허용.
- **공통 실행 순서**: `setup → statement → exercise → post_query → (cleanup/rollback)`. 검증 대상은 statement가 아니라 **post_query 결과**(dql은 mysql SELECT 자체). 케이스가 안 적은 단계는 건너뛴다.
- **테스트 분리**: 단위 테스트는 DB 없이 통과(pre-push 게이트가 이걸 돎). DB 필요한 건 `@pytest.mark.integration`으로 빼고 `pytest -m integration`으로만 돌린다. **stage 분류 행렬은 Fake Transformer/Fake Executor 주입 단위 테스트**로 고정(P2-4), 드라이버 timeout/연결만 통합.
- **게이트**: `ruff check` / `ruff format --check` / `pyright` / `pytest` 모두 통과.
- **커밋 규칙**: `{purpose}({scope}): {desc}`, 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. 커밋은 사용자가 지시할 때만 — 기능 단위로.
- **Java 개발자 대상**: Python 관용구가 Java와 다르면 비교 설명(Protocol↔interface, dataclass↔record 등).

### DB 접속 (통합 테스트용, 스펙 고정값)

- MySQL: `host=127.0.0.1 port=13306 user=root pass=root db=shop`
- Postgres: `host=127.0.0.1 port=15432 user=postgres pass=postgres db=shop`
- 시드는 결정적: users 1000(`id=1`→`user1@example.com`,`User 1`), products 1000('Product 0001'~'Product 1000'), orders 50000, payments 50000(paid는 boolean), reviews 20000.

### 스펙 변경 사항 (Task 0에서 스펙에 먼저 반영)

리뷰가 스펙 자체의 오류/모호를 짚었다. 아래를 스펙 문서에 반영한 뒤 구현한다(스펙이 단일 근거이므로 코드만 고치면 계약과 어긋남).

1. **행 집합 비교 알고리즘**: "탐욕적 multiset 매칭" → **"이분 그래프 최대 매칭"**. 탐욕은 오차 내 값이 여러 상대와 매칭될 때 완전 매칭을 놓치는 반례가 있다(예: A=[0.9e-9, 0], B=[0, 1.8e-9], tol=1e-9). 스펙 264~275줄·결정 근거의 "탐욕적 매칭" 문구 교체.
2. **타입 정규화**: "float 오차"를 **정수·Decimal에 적용하지 않는다** 명시. Decimal은 스케일만 흡수(값 붕괴 금지), float가 관여할 때만 1e-9 오차. 스펙 257~263줄 보강.
3. **MySQL DML/DDL timeout 비보장**: 스펙 "타임아웃"(214줄)에 "MySQL `MAX_EXECUTION_TIME`은 SELECT 전용이라 MySQL DML/DDL은 timeout 비보장(비범위), PG는 전체 보장"을 명시.
4. **ordered 계약 통일**: dml/ddl에도 `ordered` 필드를 허용(현재 validator는 dql만 허용)하고, ORDER BY가 있는 대상 쿼리는 `ordered`를 명시(추론 대신 명시). date-function에 `ordered: true` 추가.
   - **dml pairable 규칙(P1-2차)**: `setup`만 optional로 내리고 `post_query`는 **required 유지**. dml은 statement 반환값을 비교하지 않으므로 post_query가 없으면 양 DB의 최종 상태가 달라도 pass하게 된다(검증 대상이 post_query라서). → "setup 없는 dml은 통과, post_query 없는 dml은 실패".
5. **upsert 코퍼스 수정**: setup에서 신규 행을 삽입하지 않고 **기존 시드 행(id=1)을 재사용**한다(PG NOT NULL·MySQL AUTO_INCREMENT 카운터 전진 회피).
6. **fixed_clock allowlist**: 지원 현재시각 함수 목록을 스펙 비결정성 절에 명시.

### 코퍼스 기대 결과 (pass-through 기준 스냅샷 — Task 9 근거)

pass-through라서 MySQL 전용 문법은 PG에서 fail. **`error == 0`은 불변식**(케이스/환경 문제 없음). 아래는 설계 예상이며 Task 9 실행으로 확정.

| case_id | kind | 예상 status/stage | 근거 |
|---------|------|------|------|
| limit-pagination | dql | fail / pg.statement | `LIMIT 10, 5` PG 문법 오류 |
| ifnull-coalesce | dql | fail / pg.statement | `IFNULL` PG 미존재 |
| backtick-identifier | dql | fail / pg.statement | 백틱 PG 미지원 |
| date-function | dql | fail / pg.statement | `DATE_ADD`/`INTERVAL 7 DAY` MySQL 문법(NOW()는 fixed_clock 치환) |
| enum-type | dql | pass | 표준 SELECT/WHERE |
| bool-tinyint | dql | **fail / pg.statement** | PG `paid`가 boolean → `WHERE paid = 1` 타입 오류(P3 반영) |
| unsigned-type | dql | pass | 표준 SELECT/WHERE |
| upsert-on-duplicate | dml | fail / pg.statement | `ON DUPLICATE KEY UPDATE` PG 미지원(수정된 코퍼스로도 문법은 여전히 PG 실패) |
| auto-increment | ddl | fail / pg.statement | `AUTO_INCREMENT` PG 미지원 |
| covering-index | dql | pass | 표준 SELECT/WHERE |
| multi-join | dql | pass | 표준 JOIN |
| keyset-vs-offset | dql | pass | 표준 SELECT/WHERE/ORDER BY |
| non-sargable-like | dql | pass | 표준 LIKE |
| groupby-aggregate | dql | pass | 표준 GROUP BY |

---

## 파일 구조

```
harness/
  __init__.py       (빈 패키지 마커)
  errors.py         StatementTimeout / ConnectionFailure / InfrastructureFailure / SqlExecutionFailure
  transform.py      Transformer Protocol + PassThroughTransformer + fix_clock(sql, ts, dialect)
  compare.py        Comparator (row_equal / 이분 최대 매칭 / 컬럼명 / exclude_columns) — DB 무지 순수
  loader.py         CaseLoader (YAML → Case, 제어 SQL 쌍 정규화, nondeterministic·ordered 로드)
  executor.py       Executor (연결·실행·격리·타임아웃·DDL 고유명·트랜잭션 경계·예외 번역)
  runner.py         Runner (kind별 경로 조립, _transform_or_stage, fixed_clock 전처리, stage 분류)
  report.py         CaseResult[] → 터미널 요약 + exit code
  __main__.py       CLI 진입점

tests/
  test_transform.py       단위 (pass-through, fix_clock allowlist)
  test_compare.py         단위 (Comparator 다수: 최대 매칭·정수/Decimal·컬럼명·datetime)
  test_loader.py          단위 (제어 SQL 정규화, ordered)
  test_runner_stage.py    단위 (Fake 주입 stage 행렬 — DB 불필요)
  test_report.py          단위 (요약·exit code)
  test_executor.py        통합 (@pytest.mark.integration)
  test_runner.py          통합 (@pytest.mark.integration)
  test_end_to_end.py      통합 (@pytest.mark.integration)
  conftest.py             통합용 커넥션 fixture + 컨테이너 미기동 시 skip

pyproject.toml            PyMySQL, psycopg 추가 + integration 마커 등록
```

**의존 순서**: (스펙/코퍼스/validator 수정 = Task 0) → 의존성(1) → errors+transform(2) → compare(3) → loader(4) → executor(5) → runner(6) → report/CLI(7) → e2e(8).

**데이터 모델(여러 Task 공유)**:

```python
# harness/loader.py
@dataclass
class Case:
    id: str
    kind: str  # dql | dml | ddl
    concepts: list[str]
    mysql: str | None  # dql 피검증 SQL
    statement: str | None  # dml/ddl 피검증 SQL
    ordered: bool
    isolation: str | None
    object: dict | None  # ddl: {type, name}
    nondeterministic: dict | None  # {strategy, columns?}
    control_mysql: dict[str, str]  # setup/exercise/post_query (MySQL용, 정규화 후)
    control_postgres: dict[str, str]


# harness/executor.py
@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]  # 드라이버 원본 타입 (Decimal/int/datetime/None)


# harness/compare.py
@dataclass
class Comparison:
    equal: bool
    reason: str | None


# harness/runner.py
@dataclass
class CaseResult:
    case_id: str
    status: str  # pass | fail | error
    stage: (
        str | None
    )  # transform | mysql.statement | pg.statement | control | infrastructure | compare
    reason: str | None
```

---

## Task 0: 스펙·코퍼스·validator 수정 (계약 확정)

**Files:**
- Modify: `docs/superpowers/specs/2026-08-05-validation-harness-design.md`
- Modify: `corpus/cases/syntax/syntax.yaml` (upsert-on-duplicate, date-function)
- Modify: `tools/validate_corpus.py` (dml/ddl에 `ordered` optional 허용)
- Modify: `corpus/case-schema.md` (ordered를 dml/ddl에도 허용 명시)
- Test: `tools/tests/` 기존 validate_corpus 테스트가 통과해야 함

**Interfaces:**
- Consumes: 없음
- Produces: 수정된 스펙·코퍼스·validator. 이후 모든 Task의 계약 근거.

- [ ] **Step 1: 스펙에 "스펙 변경 사항" 6개 반영**

`docs/superpowers/specs/2026-08-05-validation-harness-design.md`에서:
- 264~275줄 "탐욕적 multiset 매칭" → "이분 그래프 최대 매칭"으로 교체하고 반례(A=[0.9e-9,0], B=[0,1.8e-9])를 근거로 명시.
- 257~263줄 타입 정규화에 "정수·Decimal은 정확 비교, float가 관여할 때만 1e-9 오차. Decimal은 스케일만 흡수(값 붕괴 금지)" 추가.
- 214줄 타임아웃에 "MySQL `MAX_EXECUTION_TIME`은 read-only SELECT 전용 → MySQL DML/DDL timeout 비보장(비범위), PG는 `statement_timeout`으로 전체 보장" 추가.
- 비결정성 절에 fixed_clock 지원 함수 allowlist 추가.
- 결정 근거의 "unordered 비교는 Counter 대신 탐욕적 매칭" 문구를 "이분 최대 매칭"으로 교정.

- [ ] **Step 2: upsert-on-duplicate 케이스 수정 (기존 시드 행 재사용)**

`corpus/cases/syntax/syntax.yaml`의 upsert-on-duplicate를 다음으로 교체(setup 제거, 기존 id=1 행 재사용):

```yaml
  - id: upsert-on-duplicate
    kind: dml
    concepts: [upsert-on-duplicate]
    isolation: fresh
    note: >
      INSERT ... ON DUPLICATE KEY UPDATE → PG ON CONFLICT. users.email이 UNIQUE라
      기존 시드 행(id=1, user1@example.com)과 email이 충돌해 name을 갱신한다.
      기존 행을 재사용해 새 id/AUTO_INCREMENT를 소비하지 않는다(공유 fixture 불변).
      전체가 트랜잭션 안에서 ROLLBACK되어 시드가 복원된다.
    statement: |
      INSERT INTO users (id, email, name, created_at)
      VALUES (1, 'user1@example.com', 'Upserted', TIMESTAMP '2025-01-01 00:01:00')
      ON DUPLICATE KEY UPDATE name = VALUES(name)
    post_query: |
      SELECT name FROM users WHERE id = 1
```

> 근거: 기존 시드는 `id=1 → email='user1@example.com'`. INSERT의 email이 UNIQUE 충돌 → ON DUPLICATE KEY UPDATE로 `name='Upserted'`. id=1을 명시해 새 AUTO_INCREMENT 미소비. ROLLBACK으로 원복. (pass-through라 PG에선 `ON DUPLICATE KEY` 문법 실패 → fail/pg.statement가 정상.)

- [ ] **Step 3: date-function에 ordered 명시**

`corpus/cases/syntax/syntax.yaml`의 date-function에 `ordered: true` 추가(대상 쿼리에 `ORDER BY id`가 있음):

```yaml
  - id: date-function
    kind: dql
    concepts: [date-function]
    ordered: true
    nondeterministic:
      strategy: fixed_clock
    note: ...(기존 유지)
    mysql: ...(기존 유지)
```

- [ ] **Step 4: validator KIND_SPECS 수정 (ordered 허용 + dml pairable 재분류, P1-2차)**

`tools/validate_corpus.py`의 `KIND_SPECS`를 아래로 수정한다. 핵심은 **dml `setup`만 optional로 내리고 `post_query`는 required 유지**(2차 리뷰 P1-1). setup을 요구하면 수정된 upsert(setup 없음)가 막히고, post_query를 optional로 내리면 검증 안 된 dml이 통과한다:

```python
    "dql": KindSpec(
        required={"mysql"},
        optional={"ordered", "nondeterministic", "perf"},
    ),
    "dml": KindSpec(
        required={"statement", "isolation"},
        optional={"nondeterministic", "exercise", "ordered"},
        pairable_required={"post_query"},   # 검증 대상 — 없으면 상태 비교 불가
        pairable_optional={"setup"},         # 기존 시드 재사용 케이스는 setup 불필요
    ),
    "ddl": KindSpec(
        required={"statement", "isolation", "object"},
        optional={"exercise", "ordered"},
        pairable_optional={"setup", "post_query"},
    ),
```

> ddl은 post_query가 원래도 optional이라 그대로 둔다(auto-increment는 post_query가 있음). dql은 `ordered`가 이미 optional에 있으면 그대로.

- [ ] **Step 4b: validator 테스트에 dml pairable 규칙 명시 (P1-2차)**

기존 `tests/test_validate_corpus.py`에 다음을 추가해 계약을 고정한다(기존 파일의 import·헬퍼 관행을 따른다):

```python
def test_dml_without_setup_passes():
    case = {
        "id": "u",
        "kind": "dml",
        "isolation": "fresh",
        "concepts": ["upsert-on-duplicate"],
        "statement": "X",
        "post_query": "SELECT 1",
    }
    assert validate_case(case, {"upsert-on-duplicate"}).ok


def test_dml_without_post_query_fails():
    case = {
        "id": "u",
        "kind": "dml",
        "isolation": "fresh",
        "concepts": ["upsert-on-duplicate"],
        "statement": "X",
    }
    assert not validate_case(case, {"upsert-on-duplicate"}).ok
```

- [ ] **Step 5: case-schema.md에 ordered 허용 반영**

`corpus/case-schema.md`의 필드 표/설명에서 `ordered`를 dql 전용이 아니라 전 kind 허용으로 수정하고, "ORDER BY 있는 대상 쿼리는 ordered 명시" 문구 추가.

- [ ] **Step 6: validator·정적 검증 통과 확인**

Run:
```bash
pytest tests/test_validate_corpus.py -q
python tools/validate_corpus.py
```
Expected: 기존 validate_corpus 단위 테스트 + 신규 dml pairable 테스트 통과 + 코퍼스 14개 정적 검증 통과("OK: 케이스 14개"). setup 없는 upsert·ordered 붙은 date-function이 통과해야 한다.

- [ ] **Step 7: Commit (사용자 지시 시)**

```bash
git add docs/superpowers/specs/2026-08-05-validation-harness-design.md corpus/ tools/validate_corpus.py
git commit -m "docs(harness): 비교 알고리즘·타임아웃·ordered·upsert 코퍼스 계약 수정 (Codex 리뷰 반영)"
```

---

## Task 1: 의존성 + 마커 등록

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: 없음
- Produces: `PyMySQL`, `psycopg[binary]` import 가능. pytest `integration` 마커 등록.

- [ ] **Step 1: dev 의존성에 드라이버 추가**

`[project.optional-dependencies]`의 `dev` 리스트에 추가(`PyYAML`은 이미 있음):

```toml
    "PyMySQL>=1.1.0",
    "psycopg[binary]>=3.1.0",
```

- [ ] **Step 2: integration 마커 + 기본 제외 등록**

`[tool.pytest.ini_options]`에 추가:

```toml
markers = [
    "integration: 실제 DB 컨테이너가 필요한 통합 테스트 (pytest -m integration으로만 실행)",
]
addopts = "-m 'not integration'"
```

(`pytest`=단위만, `pytest -m integration`=통합만, `pytest -m ''`=전체.)

- [ ] **Step 3: 설치 후 import 검증**

Run: `pip install -e ".[dev]" && python -c "import pymysql, psycopg; print('drivers ok')"`
Expected: `drivers ok`

- [ ] **Step 4: 마커 인식 확인**

Run: `pytest --markers | grep integration`
Expected: 등록한 integration 마커 설명 출력.

- [ ] **Step 5: Commit (사용자 지시 시)**

```bash
git add pyproject.toml
git commit -m "chore(harness): PyMySQL·psycopg dev 의존성과 integration 마커 추가"
```

---

## Task 2: 하니스 예외 + Transformer + fix_clock

**Files:**
- Create: `harness/__init__.py` (빈 파일)
- Create: `harness/errors.py`
- Create: `harness/transform.py`
- Test: `tests/test_transform.py`

**Interfaces:**
- Consumes: SQLGlot(`sqlglot`)
- Produces:
  - `harness/errors.py`: `class HarnessError(Exception)`, `class StatementTimeout`, `class ConnectionFailure`, `class InfrastructureFailure`, `class SqlExecutionFailure` (모두 HarnessError 상속)
  - `class Transformer(Protocol)` — `def transform(self, mysql_sql: str) -> str`
  - `class PassThroughTransformer` — `transform`이 입력 그대로 반환
  - `def fix_clock(sql: str, ts: str, *, dialect: str) -> str` — allowlist 현재시각 함수를 고정 리터럴로 치환

**Python↔Java 설명**: `Protocol`은 Java의 interface에 해당하되 **구조적 타이핑**이다(`implements` 선언 없이 시그니처만 맞으면 만족). 예외 계층은 Java의 커스텀 Exception 클래스 상속과 동일하다.

- [ ] **Step 1: 빈 패키지 마커 + errors.py**

`harness/__init__.py`를 빈 파일로. `harness/errors.py`:

```python
"""하니스 자체 예외. 드라이버(PyMySQL/psycopg) 예외를 이 타입으로 번역해,
Runner가 드라이버를 몰라도 stage를 분류할 수 있게 한다.
"""

from __future__ import annotations


class HarnessError(Exception):
    """하니스 실행 중 발생하는 모든 예외의 베이스."""


class StatementTimeout(HarnessError):
    """쿼리 statement timeout 초과. 항상 infrastructure/error로 분류."""


class ConnectionFailure(HarnessError):
    """DB 연결·인증 단절. 항상 infrastructure/error로 분류."""


class InfrastructureFailure(HarnessError):
    """쿼리 본체가 아닌 DB 호출(timeout SET·commit/rollback·cursor·fetch·close 등)의
    실패. 항상 infrastructure/error로 분류. SqlExecutionFailure로 번역하면 PG에서
    pg.statement/fail로 오분류되므로 별도 타입으로 가른다.
    """


class SqlExecutionFailure(HarnessError):
    """피검증/제어 쿼리 '본체'의 SQL 실행 실패(구문·제약 위반 등).
    stage는 호출 문맥(피검증/제어)이 결정한다.
    """
```

- [ ] **Step 2: pass-through 실패 테스트**

`tests/test_transform.py`:

```python
from harness.transform import PassThroughTransformer, fix_clock


def test_passthrough_returns_input_unchanged():
    sql = "SELECT `id` FROM `products` LIMIT 10, 5"
    assert PassThroughTransformer().transform(sql) == sql
```

- [ ] **Step 3: 실패 확인**

Run: `pytest tests/test_transform.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 4: Transformer/PassThroughTransformer 구현**

`harness/transform.py`:

```python
"""피검증 SQL 변환 계층 + fixed_clock 전처리.

이번 단계엔 실제 변환 엔진이 없어 PassThroughTransformer가 입력을 그대로 돌려준다.
변환 엔진(이슈 C)이 나오면 같은 Transformer Protocol로 갈아끼운다.
fix_clock은 변환기와 무관한 '오라클 고정' 전처리다(비결정 시각 함수 → 고정 리터럴).
"""

from __future__ import annotations

from typing import Protocol

import sqlglot
from sqlglot import exp


class Transformer(Protocol):
    """MySQL SQL을 PostgreSQL SQL로 바꾸는 계약(Java의 interface에 해당)."""

    def transform(self, mysql_sql: str) -> str: ...


class PassThroughTransformer:
    """변환 엔진이 없을 때 입력을 그대로 반환하는 자리표시 구현."""

    def transform(self, mysql_sql: str) -> str:
        return mysql_sql
```

- [ ] **Step 5: pass-through 통과 확인**

Run: `pytest tests/test_transform.py -v`
Expected: PASS

- [ ] **Step 6: fix_clock allowlist 테스트 작성**

`tests/test_transform.py`에 추가:

```python
def test_fix_clock_replaces_mysql_now():
    out = fix_clock("SELECT NOW()", "2025-06-01 12:00:00", dialect="mysql")
    assert "NOW" not in out.upper()
    assert "2025-06-01 12:00:00" in out


def test_fix_clock_replaces_current_timestamp():
    out = fix_clock(
        "SELECT CURRENT_TIMESTAMP", "2025-06-01 12:00:00", dialect="postgres"
    )
    assert "CURRENT_TIMESTAMP" not in out.upper()
    assert "2025-06-01 12:00:00" in out


def test_fix_clock_replaces_curdate():
    out = fix_clock("SELECT CURDATE()", "2025-06-01 12:00:00", dialect="mysql")
    assert "CURDATE" not in out.upper()
    assert "2025-06-01" in out


def test_fix_clock_in_where_clause():
    out = fix_clock(
        "SELECT id FROM orders WHERE ordered_at < NOW()",
        "2025-06-01 12:00:00",
        dialect="mysql",
    )
    assert "NOW" not in out.upper()
    assert "2025-06-01 12:00:00" in out


def test_fix_clock_leaves_non_clock_unchanged():
    out = fix_clock(
        "SELECT id FROM products ORDER BY id", "2025-06-01 12:00:00", dialect="postgres"
    )
    assert "2025-06-01" not in out
```

- [ ] **Step 7: fix_clock 실패 확인**

Run: `pytest tests/test_transform.py -k fix_clock -v`
Expected: FAIL (ImportError: cannot import name 'fix_clock')

- [ ] **Step 8: fix_clock 구현 (allowlist AST 치환)**

`harness/transform.py`에 추가. SQLGlot 실측(리뷰 P2-1) 기준: MySQL `NOW()`→`exp.Anonymous("NOW")`, `CURRENT_TIMESTAMP`→`exp.CurrentTimestamp`, `CURDATE()`→`exp.CurrentDate`, `SYSDATE()`→`exp.Anonymous("SYSDATE")`. Anonymous는 함수명으로 판별한다:

```python
# 고정할 현재시각 함수 allowlist (소문자 함수명)
_CLOCK_ANON_NAMES = {"now", "sysdate"}


def fix_clock(sql: str, ts: str, *, dialect: str) -> str:
    """현재시각 함수(allowlist)를 고정 리터럴로 치환한다.

    PostgreSQL은 SET으로 now()를 고정할 수 없어, 양 DB를 동일하게 다루려고
    실행 전 SQL 자체를 치환한다. dialect로 파싱·재생성 방언을 맞춘다.
    지원: NOW/CURRENT_TIMESTAMP/LOCALTIMESTAMP(→timestamp), CURDATE/CURRENT_DATE(→date).
    """
    tree = sqlglot.parse_one(sql, read=dialect)
    ts_literal = exp.cast(exp.Literal.string(ts), "TIMESTAMP")
    date_literal = exp.cast(exp.Literal.string(ts[:10]), "DATE")

    def _replace(node: exp.Expression) -> exp.Expression:
        if isinstance(node, (exp.CurrentTimestamp,)):
            return ts_literal.copy()
        if isinstance(node, exp.CurrentDate):
            return date_literal.copy()
        if isinstance(node, exp.Anonymous):
            name = (node.this or "").lower() if isinstance(node.this, str) else ""
            if name in _CLOCK_ANON_NAMES:
                return ts_literal.copy()
        return node

    return tree.transform(_replace).sql(dialect=dialect)
```

> 구현 주의: SQLGlot 버전에 따라 `NOW()`가 `exp.CurrentTimestamp`로 파싱될 수도 있다(방언별). Step 9에서 실패하면 print로 실제 노드 타입을 확인해 분기를 맞춘다. `exp.Anonymous.this`가 함수명 문자열인지도 확인.

- [ ] **Step 9: fix_clock 통과 확인 + 노드 실측**

Run: `pytest tests/test_transform.py -v`
Expected: 전부 PASS. 실패 시:
```python
python -c "import sqlglot; print(repr(sqlglot.parse_one('SELECT NOW()', read='mysql')))"
```
로 노드 타입 확인 후 `_replace` 분기 보강.

- [ ] **Step 10: Commit (사용자 지시 시)**

```bash
git add harness/__init__.py harness/errors.py harness/transform.py tests/test_transform.py
git commit -m "feat(harness): 하니스 예외·Transformer·fix_clock(allowlist) 추가"
```

---

## Task 3: Comparator (핵심 로직, P1-3·P1-4·P2-2)

**Files:**
- Create: `harness/compare.py`
- Test: `tests/test_compare.py`

**Interfaces:**
- Consumes: 없음(순수). `QueryResult`를 받지 않고 columns·rows를 분리 인자로.
- Produces:
  - `@dataclass class Comparison: equal: bool; reason: str | None`
  - `def compare(columns_a, rows_a, columns_b, rows_b, *, ordered, exclude_columns=None) -> Comparison`
  - `def row_equal(a: tuple, b: tuple) -> bool` (테스트에서 직접 사용)

**핵심(P1-3, P1-4)**: unordered는 **이분 그래프 최대 매칭**(탐욕 금지). 값 비교는 **정수·Decimal 정확, float만 오차**.

- [ ] **Step 1: 동일 결과 + 컬럼명 대응 테스트**

`tests/test_compare.py`:

```python
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from harness.compare import compare, row_equal


def test_identical_rows_equal():
    cols = ["id", "name"]
    rows = [(1, "a"), (2, "b")]
    r = compare(cols, rows, cols, list(rows), ordered=True)
    assert r.equal and r.reason is None


def test_column_name_mismatch_fails():
    r = compare(["id"], [(1,)], ["other"], [(1,)], ordered=True)
    assert not r.equal
    assert "컬럼" in (r.reason or "")
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_compare.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Comparator 구현 (정확 비교 + 이분 매칭)**

`harness/compare.py`:

```python
"""두 결과셋 동일성 비교. DB·드라이버를 전혀 모르는 순수 함수.

float 근사 비교 때문에 '행 동등성'이 정확한 해시 동등성이 아니다. 그래서
unordered 비교에 Counter(해시)나 탐욕적 매칭을 쓸 수 없다(탐욕은 반례가 있다:
A=[0.9e-9, 0], B=[0, 1.8e-9], tol=1e-9 → 0.9e-9가 먼저 0을 소비하면 실패).
완전 매칭 존재 여부를 이분 그래프 최대 매칭으로 판정한다.

값 비교: 정수·Decimal은 정확 비교(오차 없음), float가 관여할 때만 1e-9 오차.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

FLOAT_TOL = 1e-9


@dataclass
class Comparison:
    equal: bool
    reason: str | None


def _value_equal(a: object, b: object) -> bool:
    # NULL
    if a is None or b is None:
        return a is None and b is None

    # bool ↔ 정수 0/1 (근사 아님: 상대가 정확히 0/1인 정수/bool일 때만)
    if isinstance(a, bool) or isinstance(b, bool):
        av = _bool_to_int(a)
        bv = _bool_to_int(b)
        if av is None or bv is None:
            return False
        return av == bv

    # datetime: 두 컨테이너 UTC 고정 → 같은 순간이면 동일
    if isinstance(a, datetime) and isinstance(b, datetime):
        return _same_instant(a, b)

    # Decimal: 스케일만 흡수(값 붕괴 금지) — Decimal끼리 또는 Decimal↔정수 정확 비교
    if isinstance(a, Decimal) and isinstance(b, Decimal):
        return a == b  # Decimal ==는 스케일 무시 수치 비교(10.00 == 10.0)
    if isinstance(a, Decimal) and isinstance(b, int):
        return a == b
    if isinstance(b, Decimal) and isinstance(a, int):
        return a == b

    # float가 관여하면 오차 비교
    if isinstance(a, float) or isinstance(b, float):
        if isinstance(a, (int, float, Decimal)) and isinstance(
            b, (int, float, Decimal)
        ):
            return math.isclose(
                float(a), float(b), rel_tol=FLOAT_TOL, abs_tol=FLOAT_TOL
            )
        return False

    # 정수 등 나머지는 정확 비교
    return a == b


def _bool_to_int(v: object) -> int | None:
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, int):
        return v if v in (0, 1) else None
    return None


def _same_instant(a: datetime, b: datetime) -> bool:
    # naive는 UTC로 간주(두 컨테이너 UTC 고정). aware는 그대로.
    from datetime import timezone

    aa = a if a.tzinfo else a.replace(tzinfo=timezone.utc)
    bb = b if b.tzinfo else b.replace(tzinfo=timezone.utc)
    return aa == bb


def row_equal(a: tuple, b: tuple) -> bool:
    return len(a) == len(b) and all(_value_equal(x, y) for x, y in zip(a, b))


def compare(
    columns_a: list[str],
    rows_a: list[tuple],
    columns_b: list[str],
    rows_b: list[tuple],
    *,
    ordered: bool,
    exclude_columns: list[str] | None = None,
) -> Comparison:
    if columns_a != columns_b:
        return Comparison(False, f"컬럼 불일치: {columns_a} vs {columns_b}")

    if exclude_columns:
        rows_a, cols = _drop_columns(columns_a, rows_a, exclude_columns)
        rows_b, _ = _drop_columns(columns_b, rows_b, exclude_columns)

    if len(rows_a) != len(rows_b):
        return Comparison(False, f"행 개수 다름: {len(rows_a)} vs {len(rows_b)}")

    if ordered:
        for i, (ra, rb) in enumerate(zip(rows_a, rows_b)):
            if not row_equal(ra, rb):
                return Comparison(False, f"{i}행 불일치: {ra!r} != {rb!r}")
        return Comparison(True, None)

    return _multiset_equal(rows_a, rows_b)


def _drop_columns(
    columns: list[str], rows: list[tuple], exclude: list[str]
) -> tuple[list[tuple], list[str]]:
    keep = [i for i, c in enumerate(columns) if c not in exclude]
    return [tuple(r[i] for i in keep) for r in rows], [columns[i] for i in keep]


def _multiset_equal(rows_a: list[tuple], rows_b: list[tuple]) -> Comparison:
    """이분 그래프 최대 매칭(Kuhn). rows_a[i]↔rows_b[j]가 row_equal이면 간선.
    완전 매칭(모든 a가 매칭)이면 equal(개수는 이미 같음).
    """
    n = len(rows_a)
    match_b: list[int] = [-1] * n  # rows_b[j]에 매칭된 rows_a 인덱스

    def try_augment(i: int, seen: list[bool]) -> bool:
        for j in range(n):
            if row_equal(rows_a[i], rows_b[j]) and not seen[j]:
                seen[j] = True
                if match_b[j] == -1 or try_augment(match_b[j], seen):
                    match_b[j] = i
                    return True
        return False

    for i in range(n):
        if not try_augment(i, [False] * n):
            return Comparison(False, f"매칭 안 되는 행: {rows_a[i]!r}")
    return Comparison(True, None)
```

> Java 비유: `try_augment`는 이분 매칭의 증가 경로 탐색(Kuhn's algorithm). n이 작아(코퍼스 결과셋은 수십 행) O(V·E)로 충분하다.

- [ ] **Step 4: 동일/컬럼명 테스트 통과**

Run: `pytest tests/test_compare.py -v`
Expected: PASS

- [ ] **Step 5: 스펙 + 리뷰 지적 단위 테스트 전부 작성**

`tests/test_compare.py`에 추가:

```python
def test_ordered_true_order_matters():
    assert not compare(["id"], [(1,), (2,)], ["id"], [(2,), (1,)], ordered=True).equal


def test_ordered_false_order_ignored():
    assert compare(["id"], [(1,), (2,)], ["id"], [(2,), (1,)], ordered=False).equal


def test_duplicate_count_mismatch_fails():
    assert not compare(["id"], [(1,), (1,)], ["id"], [(1,), (2,)], ordered=False).equal


# P1-4: 정수·Decimal 정확 비교
def test_large_ints_not_approximated():
    assert not row_equal((10**12,), (10**12 + 1,))  # 다른 큰 정수는 다름


def test_decimal_scale_equal_but_value_exact():
    assert row_equal((Decimal("10.00"),), (Decimal("10.0"),))  # 스케일만 흡수
    assert not row_equal(
        (Decimal("9007199254740992"),), (Decimal("9007199254740993"),)
    )  # float 붕괴 안 함


def test_int_one_vs_bool_true_equal():
    assert row_equal((1,), (True,))
    assert not row_equal((2,), (True,))  # 2는 True 아님
    assert not row_equal((True,), (1.0000000005,))  # bool은 float 근사 안 함


def test_datetime_same_instant_diff_repr_equal():
    utc = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    kst = datetime(2025, 6, 1, 21, 0, 0, tzinfo=timezone(timedelta(hours=9)))
    assert row_equal((utc,), (kst,))  # 같은 순간


def test_datetime_naive_treated_utc():
    naive = datetime(2025, 6, 1, 12, 0, 0)
    aware = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert row_equal((naive,), (aware,))


def test_float_within_and_outside_tolerance():
    assert row_equal((3.3333333331,), (3.3333333329,))
    assert not row_equal((3.30,), (3.31,))


# P1-3: 탐욕이면 실패하지만 최대 매칭이면 성공하는 반례
def test_bipartite_matching_beats_greedy():
    cols = ["v"]
    a = [(0.9e-9,), (0.0,)]
    b = [(0.0,), (1.8e-9,)]  # 완전 매칭: 0.9e-9↔1.8e-9, 0↔0
    assert compare(cols, a, cols, b, ordered=False).equal


def test_float_approx_unordered_matches():
    cols = ["v"]
    a = [(3.0000000001,), (5.0,)]
    b = [(5.0,), (3.0000000002,)]
    assert compare(cols, a, cols, b, ordered=False).equal


def test_duplicate_float_rows_count_matters():
    cols = ["v"]
    a = [(3.0000000001,), (3.0000000002,)]
    b = [(3.0,), (3.0,)]
    assert compare(cols, a, cols, b, ordered=False).equal
    assert not compare(cols, [(3.0000000001,)], cols, b, ordered=False).equal


def test_null_rows_compared():
    assert compare(["a", "b"], [(None, 1)], ["a", "b"], [(None, 1)], ordered=True).equal
    assert not row_equal((None,), (1,))


def test_column_count_mismatch_fails():
    assert not compare(["a"], [(1,)], ["a", "b"], [(1, 2)], ordered=True).equal


def test_exclude_columns_drops_column():
    cols = ["id", "created_at"]
    a = [(1, "2025-06-01"), (2, "2025-06-02")]
    b = [(1, "2099-01-01"), (2, "2099-01-02")]
    assert compare(cols, a, cols, b, ordered=True, exclude_columns=["created_at"]).equal


def test_exclude_columns_missing_target_is_noop():
    # 없는 열 제외는 무해(전체 비교)
    cols = ["id"]
    assert compare(
        cols, [(1,)], cols, [(1,)], ordered=True, exclude_columns=["nope"]
    ).equal
```

- [ ] **Step 6: 전체 통과**

Run: `pytest tests/test_compare.py -v`
Expected: 전부 PASS. 실패하면 구현을 고친다(테스트 약화 금지).

- [ ] **Step 7: Commit (사용자 지시 시)**

```bash
git add harness/compare.py tests/test_compare.py
git commit -m "feat(harness): 결과셋 비교기(이분 최대 매칭·정확 타입비교·컬럼명·exclude) 추가"
```

---

## Task 4: CaseLoader

**Files:**
- Create: `harness/loader.py`
- Test: `tests/test_loader.py`

**Interfaces:**
- Consumes: `validate_corpus`(`tools/validate_corpus.py`), PyYAML
- Produces:
  - `@dataclass class Case`(위 데이터 모델)
  - `def load_case(raw: dict) -> Case`
  - `def load_corpus(cases_dir: Path, concepts_path: Path) -> list[Case]`

**제어 SQL 정규화(스펙)**: setup/post_query는 공통형 또는 DB별 쌍. exercise는 공통형만. 공통형은 양쪽 복사, 쌍은 분리. dml exercise·ddl setup도 반드시 실린다. `ordered`는 dml/ddl에서도 로드(기본 False, ORDER BY면 코퍼스가 명시).

- [ ] **Step 1: 공통형 정규화 + ordered 테스트**

`tests/test_loader.py`:

```python
from harness.loader import load_case


def test_dql_basic_and_ordered():
    c = load_case(
        {
            "id": "x",
            "kind": "dql",
            "concepts": ["limit-pagination"],
            "mysql": "SELECT 1",
            "ordered": True,
        }
    )
    assert c.id == "x" and c.kind == "dql" and c.mysql == "SELECT 1"
    assert c.ordered is True
    assert c.control_mysql == {} and c.control_postgres == {}


def test_ordered_defaults_false_when_absent():
    c = load_case(
        {"id": "x", "kind": "dql", "concepts": ["limit-pagination"], "mysql": "S"}
    )
    assert c.ordered is False


def test_dml_ordered_loaded():
    c = load_case(
        {
            "id": "u",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "X",
            "post_query": "SELECT 1",
            "ordered": True,
        }
    )
    assert c.ordered is True


def test_common_control_copied_to_both():
    c = load_case(
        {
            "id": "u",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "UPDATE t SET x=2",
            "post_query": "SELECT x FROM t",
        }
    )
    assert c.control_mysql["post_query"] == "SELECT x FROM t"
    assert c.control_postgres["post_query"] == "SELECT x FROM t"
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_loader.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Case + load_case 구현**

`harness/loader.py`:

```python
"""케이스 YAML을 Case 객체로. 제어 SQL 공통형/DB별 쌍을 DB별로 정규화한다.

형식 검증은 tools/validate_corpus.py를 재사용한다(중복 규칙 금지).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from validate_corpus import (  # noqa: E402
    load_cases,
    load_concepts,
    validate_corpus,
)

_PAIRABLE = ("setup", "post_query")  # 공통형 또는 _mysql/_postgres 쌍
_COMMON_ONLY = ("exercise",)  # 항상 공통형


@dataclass
class Case:
    id: str
    kind: str
    concepts: list[str]
    mysql: str | None
    statement: str | None
    ordered: bool
    isolation: str | None
    object: dict | None
    nondeterministic: dict | None
    control_mysql: dict[str, str] = field(default_factory=dict)
    control_postgres: dict[str, str] = field(default_factory=dict)


def _normalize_control(raw: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    mysql: dict[str, str] = {}
    postgres: dict[str, str] = {}
    for base in _PAIRABLE:
        if base in raw:
            mysql[base] = raw[base]
            postgres[base] = raw[base]
        elif f"{base}_mysql" in raw:
            mysql[base] = raw[f"{base}_mysql"]
            postgres[base] = raw[f"{base}_postgres"]
    for base in _COMMON_ONLY:
        if base in raw:
            mysql[base] = raw[base]
            postgres[base] = raw[base]
    return mysql, postgres


def load_case(raw: dict[str, Any]) -> Case:
    control_mysql, control_postgres = _normalize_control(raw)
    return Case(
        id=raw["id"],
        kind=raw["kind"],
        concepts=raw["concepts"],
        mysql=raw.get("mysql"),
        statement=raw.get("statement"),
        ordered=raw.get("ordered", False),
        isolation=raw.get("isolation"),
        object=raw.get("object"),
        nondeterministic=raw.get("nondeterministic"),
        control_mysql=control_mysql,
        control_postgres=control_postgres,
    )
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_loader.py -v`
Expected: PASS

- [ ] **Step 5: 쌍·exercise·ddl setup 테스트 추가**

```python
def test_db_specific_pair_split():
    c = load_case(
        {
            "id": "p",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "X",
            "setup_mysql": "SET @x=1",
            "setup_postgres": "SELECT 1",
        }
    )
    assert c.control_mysql["setup"] == "SET @x=1"
    assert c.control_postgres["setup"] == "SELECT 1"


def test_dml_exercise_loaded_both():
    c = load_case(
        {
            "id": "e",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "X",
            "exercise": "INSERT INTO t VALUES (9)",
        }
    )
    assert c.control_mysql["exercise"] == "INSERT INTO t VALUES (9)"
    assert c.control_postgres["exercise"] == "INSERT INTO t VALUES (9)"


def test_ddl_setup_and_object():
    c = load_case(
        {
            "id": "d",
            "kind": "ddl",
            "isolation": "fresh",
            "concepts": ["auto-increment"],
            "statement": "CREATE ...",
            "object": {"type": "table", "name": "tmp"},
            "setup": "SELECT 1",
        }
    )
    assert c.control_mysql["setup"] == "SELECT 1"
    assert c.object == {"type": "table", "name": "tmp"}
```

- [ ] **Step 6: load_corpus 구현**

`harness/loader.py`에 추가:

```python
def load_corpus(cases_dir: Path, concepts_path: Path) -> list[Case]:
    """코퍼스 디렉터리를 읽어 형식 검증 후 Case 리스트로 반환."""
    whitelist = load_concepts(concepts_path)
    case_files = sorted(cases_dir.rglob("*.yaml"))
    raws, load_result = load_cases(case_files)
    result = validate_corpus(raws, whitelist)
    errors = load_result.errors + result.errors
    if errors:
        raise ValueError("코퍼스 형식 검증 실패:\n" + "\n".join(errors))
    return [load_case(r) for r in raws]
```

- [ ] **Step 7: 실제 코퍼스 로드 테스트**

```python
def test_load_real_corpus():
    from pathlib import Path
    from harness.loader import load_corpus

    root = Path(__file__).resolve().parent.parent
    cases = load_corpus(root / "corpus" / "cases", root / "corpus" / "concepts.yaml")
    assert len(cases) == 14
    date_case = next(c for c in cases if c.id == "date-function")
    assert date_case.nondeterministic == {"strategy": "fixed_clock"}
    assert date_case.ordered is True  # Task 0에서 명시함
    upsert = next(c for c in cases if c.id == "upsert-on-duplicate")
    assert "setup" not in upsert.control_mysql  # Task 0에서 setup 제거
    assert upsert.control_mysql["post_query"]
```

Run: `pytest tests/test_loader.py -v`
Expected: 전부 PASS (Task 0 코퍼스 수정이 선행돼야 함)

- [ ] **Step 8: Commit (사용자 지시 시)**

```bash
git add harness/loader.py tests/test_loader.py
git commit -m "feat(harness): CaseLoader(제어 SQL 정규화·ordered·형식 검증 재사용) 추가"
```

---

## Task 5: Executor (연결·격리·예외 번역, P1-2·P1-6)

**Files:**
- Create: `harness/executor.py`
- Test: `tests/conftest.py`, `tests/test_executor.py`

**Interfaces:**
- Consumes: PyMySQL, psycopg, `harness.errors`
- Produces:
  - `@dataclass class QueryResult: columns: list[str]; rows: list[tuple]`
  - `@dataclass class ConnectionConfig` + `MYSQL_CONFIG`/`POSTGRES_CONFIG`
  - `def safe_object_name(case_id: str, object_name: str) -> str`
  - `ALLOWED_OBJECT_TYPES = {"table"}`
  - `class Executor`:
    - `classmethod connect(config, dialect) -> Executor` (ConnectionFailure로 번역, context manager)
    - `run_query(sql) -> QueryResult` / `run_statement(sql) -> None` (쿼리 본체는 StatementTimeout/SqlExecutionFailure, 그 외 DB 호출은 InfrastructureFailure로 번역)
    - `begin() / rollback() / commit()` (rollback/commit 실패는 InfrastructureFailure)
    - `drop_object(object_type, name) -> None`
  - `STATEMENT_TIMEOUT_SECONDS = 30`

**예외 번역(P1-2, P1-4)**: **모든 DB 호출**을 두 번역 컨텍스트로 감싼다. 쿼리 본체(`_translate_query`)는 timeout→`StatementTimeout`, 연결→`ConnectionFailure`, 그 외→`SqlExecutionFailure`. 본체가 아닌 호출(timeout SET·commit·rollback·cursor·fetch·close, `_translate_infra`)은 무조건 `InfrastructureFailure`(PG statement/fail 오분류 방지). **정확한 예외 클래스·에러코드는 구현 중 드라이버 실측**.

- [ ] **Step 1: safe_object_name + object type 단위 테스트(DB 불필요)**

`tests/test_executor.py`:

```python
import pytest
from harness.executor import safe_object_name, ALLOWED_OBJECT_TYPES


def test_safe_object_name_normalizes_hyphen():
    assert (
        safe_object_name("auto-increment", "tmp_ai")
        == "sqlbridge_auto_increment_tmp_ai"
    )


def test_safe_object_name_truncates_long():
    name = safe_object_name("a" * 100, "tmp")
    assert len(name) <= 63


def test_only_table_object_type_allowed():
    assert ALLOWED_OBJECT_TYPES == {"table"}
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_executor.py -k "safe_object or object_type" -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: QueryResult·config·safe_object_name 구현**

`harness/executor.py`:

```python
"""한 DB에 SQL을 실행하고 격리(트랜잭션/DROP)를 담당하는 Executor.

MySQL은 PyMySQL, PostgreSQL은 psycopg(v3). 드라이버 예외를 harness.errors의
타입(StatementTimeout/ConnectionFailure/SqlExecutionFailure)으로 번역해,
Runner가 드라이버를 몰라도 stage를 분류할 수 있게 한다.
QueryResult.rows는 드라이버 원본 타입을 유지한다(Comparator 타입 정규화 전제).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pymysql
import psycopg

from harness.errors import (
    ConnectionFailure,
    InfrastructureFailure,
    SqlExecutionFailure,
    StatementTimeout,
)

STATEMENT_TIMEOUT_SECONDS = 30
_MAX_IDENT = 63
ALLOWED_OBJECT_TYPES = {"table"}


@dataclass(frozen=True)
class ConnectionConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


MYSQL_CONFIG = ConnectionConfig("127.0.0.1", 13306, "root", "root", "shop")
POSTGRES_CONFIG = ConnectionConfig("127.0.0.1", 15432, "postgres", "postgres", "shop")


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]


def safe_object_name(case_id: str, object_name: str) -> str:
    safe_id = re.sub(r"[^0-9a-zA-Z_]", "_", case_id)
    name = f"sqlbridge_{safe_id}_{object_name}"
    if len(name) <= _MAX_IDENT:
        return name
    suffix = "_" + hashlib.sha1(case_id.encode()).hexdigest()[:8]
    return name[: _MAX_IDENT - len(suffix)] + suffix
```

- [ ] **Step 4: safe_object_name 통과**

Run: `pytest tests/test_executor.py -k "safe_object or object_type" -v`
Expected: PASS

- [ ] **Step 5: Executor + 예외 번역 구현 (P1-4: 모든 DB 호출 번역)**

`harness/executor.py`에 추가(import는 Step 3 상단에 이미 포함). **핵심(P1-4)**: 쿼리 본체(`_translate_query`)와 그 외 DB 호출(`_translate_infra`)의 번역을 나눈다. timeout SET·cursor·fetch·commit·rollback·close는 실패 시 `InfrastructureFailure`(PG statement/fail 오분류 방지), 쿼리 본체만 timeout/execution으로 번역. **예외 클래스·에러코드는 구현 중 실측 확정**.

```python
class Executor:
    def __init__(self, conn: object, dialect: str) -> None:
        self._conn = conn
        self.dialect = dialect

    @classmethod
    def connect(cls, config: ConnectionConfig, dialect: str) -> "Executor":
        try:
            if dialect == "mysql":
                conn = pymysql.connect(
                    host=config.host,
                    port=config.port,
                    user=config.user,
                    password=config.password,
                    database=config.database,
                    autocommit=False,
                )
            elif dialect == "postgres":
                conn = psycopg.connect(
                    host=config.host,
                    port=config.port,
                    user=config.user,
                    password=config.password,
                    dbname=config.database,
                    autocommit=False,
                )
            else:
                raise ValueError(f"알 수 없는 dialect: {dialect}")
        except (pymysql.err.OperationalError, psycopg.OperationalError) as e:
            raise ConnectionFailure(f"{dialect} 연결 실패: {e}") from e
        return cls(conn, dialect)

    # --- 예외 번역 경계 (P1-4) ---

    @contextmanager
    def _translate_query(self) -> Iterator[None]:
        """쿼리 '본체' 실행용. timeout→StatementTimeout, 연결→ConnectionFailure,
        그 외 SQL 실패→SqlExecutionFailure(stage는 호출측이 결정).
        """
        try:
            yield
        except psycopg.errors.QueryCanceled as e:  # PG statement_timeout
            raise StatementTimeout(str(e)) from e
        except pymysql.err.OperationalError as e:  # MySQL: 3024=timeout
            code = e.args[0] if e.args else None
            if code == 3024:
                raise StatementTimeout(str(e)) from e
            raise SqlExecutionFailure(str(e)) from e
        except psycopg.OperationalError as e:  # PG 연결 단절
            raise ConnectionFailure(str(e)) from e
        except (pymysql.err.MySQLError, psycopg.Error) as e:  # 구문/제약 등
            raise SqlExecutionFailure(str(e)) from e

    @contextmanager
    def _translate_infra(self) -> Iterator[None]:
        """쿼리 본체가 아닌 DB 호출용(timeout SET·commit·rollback·cursor·fetch·close).
        어떤 드라이버 예외든 InfrastructureFailure로 번역(항상 infrastructure/error).
        """
        try:
            yield
        except (pymysql.err.MySQLError, psycopg.Error) as e:
            raise InfrastructureFailure(str(e)) from e

    def __enter__(self) -> "Executor":
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self._conn.close()
        except Exception:
            pass  # 닫기 실패는 무시(이미 종료 경로 — 여기서 던지면 원 예외를 가림)

    def _apply_timeout(self, cur: object) -> None:
        ms = STATEMENT_TIMEOUT_SECONDS * 1000
        # timeout SET 자체 실패는 인프라(예: PG aborted 트랜잭션). _translate_infra로 감쌈.
        with self._translate_infra():
            if self.dialect == "postgres":
                cur.execute(f"SET statement_timeout = {ms}")  # 모든 문장 적용(보장)
            else:
                cur.execute(
                    f"SET SESSION MAX_EXECUTION_TIME = {ms}"
                )  # SELECT만(DML/DDL 비보장)

    def run_query(self, sql: str) -> QueryResult:
        with self._translate_infra():
            cur = self._conn.cursor()
        try:
            self._apply_timeout(cur)
            with self._translate_query():  # 쿼리 본체만 query 번역
                cur.execute(sql)
            with self._translate_infra():  # description·fetch는 인프라
                columns = [d[0] for d in cur.description]
                rows = [tuple(r) for r in cur.fetchall()]
            return QueryResult(columns, rows)
        finally:
            with self._translate_infra():
                cur.close()

    def run_statement(self, sql: str) -> None:
        with self._translate_infra():
            cur = self._conn.cursor()
        try:
            self._apply_timeout(cur)
            with self._translate_query():
                cur.execute(sql)
        finally:
            with self._translate_infra():
                cur.close()

    def begin(self) -> None:
        pass  # autocommit=False라 첫 실행 시 트랜잭션이 열린다.

    def rollback(self) -> None:
        with self._translate_infra():
            self._conn.rollback()

    def commit(self) -> None:
        with self._translate_infra():
            self._conn.commit()

    def drop_object(self, object_type: str, name: str) -> None:
        if object_type not in ALLOWED_OBJECT_TYPES:
            raise ValueError(f"지원하지 않는 object type: {object_type}")
        self.run_statement(f"DROP {object_type} IF EXISTS {name}")
```

> 구현 주의(실측): psycopg v3의 timeout은 `psycopg.errors.QueryCanceled`. PyMySQL의 MAX_EXECUTION_TIME 초과는 에러코드 3024(`ER_QUERY_TIMEOUT`). 연결 단절 예외 클래스는 드라이버 버전마다 다르니 Step 8 통합 테스트에서 실제 예외를 찍어 확정한다. except 순서(구체→일반)에 주의. `finally`의 `cur.close()`는 `_translate_infra`로 감싸되, close 실패가 본체 예외를 가리지 않도록 주의(본체에서 이미 예외 발생 시 finally 예외는 원칙적으로 원 예외를 덮으나, close 실패는 드묾 — 필요하면 close는 best-effort로 예외 무시).

- [ ] **Step 6: conftest (컨테이너 미기동 시 skip)**

`tests/conftest.py`:

```python
import socket
import pytest


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def mysql_up() -> None:
    if not _reachable("127.0.0.1", 13306):
        pytest.skip("MySQL 컨테이너 미기동 (docker compose up -d)")


@pytest.fixture(scope="session")
def postgres_up() -> None:
    if not _reachable("127.0.0.1", 15432):
        pytest.skip("PostgreSQL 컨테이너 미기동 (docker compose up -d)")
```

- [ ] **Step 7: Executor 통합 테스트(SELECT·격리·예외 번역·DDL cleanup)**

`tests/test_executor.py`에 추가:

```python
from harness.executor import Executor, MYSQL_CONFIG, POSTGRES_CONFIG
from harness.errors import SqlExecutionFailure, ConnectionFailure


@pytest.mark.integration
def test_mysql_select(mysql_up):
    with Executor.connect(MYSQL_CONFIG, "mysql") as ex:
        r = ex.run_query("SELECT id, name FROM products ORDER BY id LIMIT 3")
        assert r.columns == ["id", "name"]
        assert r.rows[0] == (1, "Product 0001")


@pytest.mark.integration
def test_postgres_select(postgres_up):
    with Executor.connect(POSTGRES_CONFIG, "postgres") as ex:
        r = ex.run_query("SELECT id, name FROM products ORDER BY id LIMIT 3")
        assert r.rows[0] == (1, "Product 0001")


@pytest.mark.integration
def test_sql_error_translated(postgres_up):
    with Executor.connect(POSTGRES_CONFIG, "postgres") as ex:
        with pytest.raises(SqlExecutionFailure):
            ex.run_query("SELECT * FROM no_such_table_xyz")


@pytest.mark.integration
def test_connection_failure_translated():
    bad = ConnectionConfig_bad()
    with pytest.raises(ConnectionFailure):
        Executor.connect(bad, "postgres")


def ConnectionConfig_bad():
    from harness.executor import ConnectionConfig

    return ConnectionConfig("127.0.0.1", 1, "x", "x", "x")  # 닫힌 포트


@pytest.mark.integration
def test_dml_rollback_restores_seed(postgres_up):
    with Executor.connect(POSTGRES_CONFIG, "postgres") as ex:
        before = ex.run_query("SELECT COUNT(*) FROM users").rows[0][0]
        ex.run_statement(
            "INSERT INTO users (id, email, name, created_at) "
            "VALUES (999999, 'rollback@x.com', 'X', TIMESTAMP '2025-01-01 00:00:00')"
        )
        ex.rollback()
        after = ex.run_query("SELECT COUNT(*) FROM users").rows[0][0]
        assert before == after


@pytest.mark.integration
def test_ddl_cleanup_after_pg_abort(postgres_up):
    """PG statement 실패로 트랜잭션이 aborted여도 ROLLBACK→DROP→COMMIT로 정리된다."""
    name = safe_object_name("cleanup-test", "tmp")
    with Executor.connect(POSTGRES_CONFIG, "postgres") as ex:
        ex.drop_object("table", name)
        ex.commit()
        ex.run_statement(f"CREATE TABLE {name} (id int)")
        ex.commit()  # 영구 테이블
        try:
            ex.run_statement("SELECT * FROM no_such_xyz")  # 실패 → abort
        except SqlExecutionFailure:
            pass
        ex.rollback()
        ex.drop_object("table", name)
        ex.commit()
        # catalog에서 부재 확인
        r = ex.run_query(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = %s"
            % f"'{name}'"
        )
        assert r.rows[0][0] == 0
```

- [ ] **Step 8: 통합 실행 + 예외 매핑 실측**

Run:
```bash
cd docker && docker compose up -d && cd ..
# MySQL healthy 대기 후
pytest -m integration tests/test_executor.py -v
```
Expected: PASS. 예외 번역 테스트가 실패하면 실제 드라이버 예외를 찍어 `_execute`/`connect`의 except를 실측값으로 교정.

- [ ] **Step 9: 단위(DB 없이) 통과 확인**

Run: `pytest tests/test_executor.py -v`
Expected: safe_object_name·object_type만 PASS, integration은 deselected.

- [ ] **Step 10: Commit (사용자 지시 시)**

```bash
git add harness/executor.py tests/conftest.py tests/test_executor.py
git commit -m "feat(harness): Executor(격리·타임아웃·예외 번역·DDL 고유명·트랜잭션 경계) 추가"
```

---

## Task 6: Runner (kind별 경로·stage 분류, P1-1·P1-2·P2-4)

**Files:**
- Create: `harness/runner.py`
- Test: `tests/test_runner_stage.py` (단위, Fake 주입), `tests/test_runner.py` (통합)

**Interfaces:**
- Consumes: `Case`(4), `Transformer`/`PassThroughTransformer`/`fix_clock`(2), `Executor`/`QueryResult`(5), `compare`(3), `harness.errors`(2)
- Produces:
  - `@dataclass class CaseResult`
  - `class Runner(__init__(mysql_config, postgres_config, transformer, executor_factory=Executor.connect))` — **executor_factory 주입 가능**(Fake로 stage 테스트)
  - `def run_case(self, case) -> CaseResult`
  - `FIXED_CLOCK_TS = "2025-06-01 12:00:00"`
  - `def _transform_or_stage(self, mysql_sql) -> str` (변환 예외를 transform/fail로)

**stage 분류(P1-2, P1-4)**: `StatementTimeout`/`ConnectionFailure`/`InfrastructureFailure` → 항상 `infrastructure`/`error`. `SqlExecutionFailure`는 **호출 문맥**으로 갈림: 피검증 MySQL→`mysql.statement`/error, 피검증 PG→`pg.statement`/fail, 제어→`control`/error. cleanup은 `_cleanup`+`_finalize`의 4우선순위(본체 성공+cleanup 실패→infrastructure, 본체 실패+cleanup 실패→원 stage+reason 누적).

- [ ] **Step 1: Fake 주입 stage 행렬 단위 테스트 (DB 불필요, P2-4)**

`tests/test_runner_stage.py`:

```python
import pytest
from harness.loader import load_case
from harness.runner import Runner, CaseResult
from harness.executor import QueryResult
from harness.errors import (
    StatementTimeout,
    ConnectionFailure,
    InfrastructureFailure,
    SqlExecutionFailure,
)


class FakeExecutor:
    """스크립트대로 응답/예외를 내는 가짜 Executor. dialect별로 다르게 주입.
    rollback_error/commit_error로 cleanup 실패도 주입한다(P1-2 테스트).
    """

    def __init__(self, script, *, rollback_error=None, commit_error=None):
        self.script = script  # {sql_substring: result|exception}
        self.rollback_error = rollback_error
        self.commit_error = commit_error
        self.dialect = "fake"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def begin(self):
        pass

    def rollback(self):
        if self.rollback_error:
            raise self.rollback_error

    def commit(self):
        if self.commit_error:
            raise self.commit_error

    def drop_object(self, t, n):
        pass

    def _resolve(self, sql):
        for key, val in self.script.items():
            if key in sql:
                if isinstance(val, Exception):
                    raise val
                return val
        return QueryResult(["c"], [(1,)])  # 기본

    def run_query(self, sql):
        return self._resolve(sql)

    def run_statement(self, sql):
        self._resolve(sql)


def make_runner(my_script, pg_script, **my_kwargs):
    def factory(config, dialect):
        if dialect == "mysql":
            return FakeExecutor(my_script, **my_kwargs)
        return FakeExecutor(pg_script)

    class RaisingTransformer:
        def transform(self, s):
            return s

    return Runner(None, None, RaisingTransformer(), executor_factory=factory)


def _dql(mysql="SELECT 1"):
    return load_case(
        {
            "id": "c",
            "kind": "dql",
            "concepts": ["limit-pagination"],
            "mysql": mysql,
            "ordered": True,
        }
    )


def test_stage_pg_statement_fail():
    r = make_runner({}, {"SELECT 1": SqlExecutionFailure("syntax")}).run_case(_dql())
    assert r.status == "fail" and r.stage == "pg.statement"


def test_stage_mysql_statement_error():
    r = make_runner({"SELECT 1": SqlExecutionFailure("bad")}, {}).run_case(_dql())
    assert r.status == "error" and r.stage == "mysql.statement"


def test_stage_infrastructure_on_timeout():
    r = make_runner({"SELECT 1": StatementTimeout("t")}, {}).run_case(_dql())
    assert r.status == "error" and r.stage == "infrastructure"


def test_stage_infrastructure_on_connection():
    def factory(config, dialect):
        raise ConnectionFailure("down")

    class T:
        def transform(self, s):
            return s

    r = Runner(None, None, T(), executor_factory=factory).run_case(_dql())
    assert r.status == "error" and r.stage == "infrastructure"


def test_stage_transform_fail():
    class BadT:
        def transform(self, s):
            raise ValueError("parse error")

    def factory(config, dialect):
        return FakeExecutor({})

    r = Runner(None, None, BadT(), executor_factory=factory).run_case(_dql())
    assert r.status == "fail" and r.stage == "transform"


def test_stage_compare_fail():
    my = {"SELECT 1": QueryResult(["c"], [(1,)])}
    pg = {"SELECT 1": QueryResult(["c"], [(2,)])}
    r = make_runner(my, pg).run_case(_dql())
    assert r.status == "fail" and r.stage == "compare"


def test_stage_control_error_dml():
    case = load_case(
        {
            "id": "u",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "UPDATE x",
            "post_query": "SELECT name",
        }
    )
    my = {"SELECT name": SqlExecutionFailure("bad control")}
    r = make_runner(my, {}).run_case(case)
    assert r.status == "error" and r.stage == "control"


def test_stage_pass():
    my = {"SELECT 1": QueryResult(["c"], [(7,)])}
    pg = {"SELECT 1": QueryResult(["c"], [(7,)])}
    r = make_runner(my, pg).run_case(_dql())
    assert r.status == "pass" and r.stage is None


def test_stage_infrastructure_failure_maps_to_infra():
    # 제어 SQL에서 InfrastructureFailure(예: timeout SET 실패)는 control이 아니라 infra.
    case = load_case(
        {
            "id": "u",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "UPDATE x",
            "post_query": "SELECT name",
        }
    )
    my = {"SELECT name": InfrastructureFailure("timeout set failed")}
    r = make_runner(my, {}).run_case(case)
    assert r.status == "error" and r.stage == "infrastructure"


# --- cleanup 4우선순위(P1-2) ---


def _dml_case():
    return load_case(
        {
            "id": "u",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "UPDATE x",
            "post_query": "SELECT name",
        }
    )


def test_cleanup_fail_on_body_success_becomes_infra():
    # ① 본체 성공 + rollback(cleanup) 실패 → infrastructure/error
    my = {"SELECT name": QueryResult(["name"], [("a",)])}
    r = make_runner(
        my,
        {"SELECT name": QueryResult(["name"], [("a",)])},
        rollback_error=InfrastructureFailure("rollback boom"),
    ).run_case(_dml_case())
    assert r.status == "error" and r.stage == "infrastructure"
    assert "cleanup" in (r.reason or "")


def test_cleanup_fail_on_body_fail_keeps_stage_and_appends_reason():
    # ③ 본체 실패(PG) + cleanup 실패 → 원 stage(pg.statement) 유지 + reason 누적
    my = {"SELECT name": QueryResult(["name"], [("a",)])}
    pg = {"UPDATE x": SqlExecutionFailure("pg syntax")}
    r = make_runner(my, pg).run_case(_dml_case())  # my는 정상, pg 본체 실패
    # pg 경로의 rollback 실패를 주입하려면 pg FakeExecutor에 넣어야 하므로,
    # 여기선 pg 본체 실패만 확인(원 stage 유지). cleanup 누적은 아래 별도 검증.
    assert r.status == "fail" and r.stage == "pg.statement"


def test_cleanup_success_on_body_fail_keeps_original_stage():
    # ② 본체 실패 + cleanup 성공 → 원 stage 그대로
    my = {"UPDATE x": SqlExecutionFailure("mysql bad")}
    r = make_runner(my, {}).run_case(_dml_case())
    assert r.status == "error" and r.stage == "mysql.statement"
    assert "cleanup" not in (r.reason or "")
```

> `test_cleanup_fail_on_body_fail...`에서 본체·cleanup 둘 다 실패하는 조합(③의 reason 누적)은 pg FakeExecutor에 `rollback_error`를 주입해야 완전히 검증된다. 구현 시 `make_runner`가 pg_kwargs도 받도록 확장해 케이스를 추가한다(계획은 골격 제시, 구현 중 보강).

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_runner_stage.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: CaseResult + Runner 골격 + _transform_or_stage 구현**

`harness/runner.py`:

```python
"""케이스를 로드→변환→양쪽 실행→비교해 CaseResult를 만드는 조립기.

kind별 경로(dql/dml/ddl)를 조립하고, 예외를 CaseResult로 변환하며 stage로 분류한다.
핵심은 '변환기 품질(fail)과 그 외(error)'를 가르는 것이다(스펙 에러 처리 표).
executor_factory를 주입받아(기본 Executor.connect) Fake로 stage 행렬을 단위 테스트한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from harness.compare import compare
from harness.errors import (
    ConnectionFailure,
    InfrastructureFailure,
    SqlExecutionFailure,
    StatementTimeout,
)
from harness.executor import (
    ALLOWED_OBJECT_TYPES,
    ConnectionConfig,
    Executor,
    QueryResult,
    safe_object_name,
)
from harness.loader import Case
from harness.transform import Transformer, fix_clock

FIXED_CLOCK_TS = "2025-06-01 12:00:00"


@dataclass
class CaseResult:
    case_id: str
    status: str
    stage: str | None
    reason: str | None


class _StageError(Exception):
    def __init__(self, stage: str, status: str, reason: str) -> None:
        super().__init__(reason)
        self.stage = stage
        self.status = status
        self.reason = reason


class Runner:
    def __init__(
        self,
        mysql_config: ConnectionConfig | None,
        postgres_config: ConnectionConfig | None,
        transformer: Transformer,
        executor_factory: Callable[
            [ConnectionConfig, str], Executor
        ] = Executor.connect,
    ) -> None:
        self._mysql_config = mysql_config
        self._postgres_config = postgres_config
        self._transformer = transformer
        self._factory = executor_factory

    def run_case(self, case: Case) -> CaseResult:
        try:
            if case.kind == "dql":
                return self._run_dql(case)
            if case.kind == "dml":
                return self._run_dml(case)
            if case.kind == "ddl":
                return self._run_ddl(case)
            raise _StageError("control", "error", f"알 수 없는 kind: {case.kind}")
        except _StageError as e:
            return CaseResult(case.id, e.status, e.stage, e.reason)
        except Exception as e:  # 마지막 방어선(P1-4): 번역 누락 등 예상 밖 예외
            return CaseResult(case.id, "error", "infrastructure", f"예상 밖 오류: {e}")

    # --- 공통 헬퍼 ---

    def _transform_or_stage(self, mysql_sql: str) -> str:
        """변환 예외(ParseError 등)를 transform/fail로 잡는다(P1-1)."""
        try:
            return self._transformer.transform(mysql_sql)
        except Exception as e:
            raise _StageError("transform", "fail", f"변환 실패: {e}") from e

    def _maybe_fix_clock(self, case: Case, sql: str, dialect: str) -> str:
        nd = case.nondeterministic
        if nd and nd.get("strategy") == "fixed_clock":
            return fix_clock(sql, FIXED_CLOCK_TS, dialect=dialect)
        return sql

    def _connect(self, config: ConnectionConfig | None, dialect: str) -> Executor:
        try:
            return self._factory(config, dialect)  # type: ignore[arg-type]
        except ConnectionFailure as e:
            raise _StageError("infrastructure", "error", str(e)) from e

    def _run_verified(self, ex: Executor, dialect: str, sql: str) -> None:
        """피검증 statement 실행. MySQL 실패=error, PG 실패=fail. timeout/infra=infra."""
        try:
            ex.run_statement(sql)
        except (StatementTimeout, ConnectionFailure, InfrastructureFailure) as e:
            raise _StageError(
                "infrastructure", "error", f"{type(e).__name__}: {e}"
            ) from e
        except SqlExecutionFailure as e:
            if dialect == "mysql":
                raise _StageError(
                    "mysql.statement", "error", f"MySQL 피검증 실패: {e}"
                ) from e
            raise _StageError("pg.statement", "fail", f"PG 피검증 실패: {e}") from e

    def _run_control(
        self, ex: Executor, dialect: str, sql: str, *, query: bool = False
    ):
        """제어 SQL 실행. 본체 실패=control/error, timeout/infra=infrastructure/error."""
        try:
            return ex.run_query(sql) if query else ex.run_statement(sql)
        except (StatementTimeout, ConnectionFailure, InfrastructureFailure) as e:
            raise _StageError(
                "infrastructure", "error", f"{type(e).__name__}: {e}"
            ) from e
        except SqlExecutionFailure as e:
            raise _StageError(
                "control", "error", f"{dialect} 제어 SQL 실패: {e}"
            ) from e

    def _compare_results(
        self, case, my_res: QueryResult, pg_res: QueryResult
    ) -> CaseResult:
        exclude = None
        nd = case.nondeterministic
        if nd and nd.get("strategy") == "exclude_columns":
            exclude = nd.get("columns")
        cmp = compare(
            my_res.columns,
            my_res.rows,
            pg_res.columns,
            pg_res.rows,
            ordered=case.ordered,
            exclude_columns=exclude,
        )
        if cmp.equal:
            return CaseResult(case.id, "pass", None, None)
        return CaseResult(case.id, "fail", "compare", cmp.reason)
```

- [ ] **Step 4: dql 경로(_run_dql) 구현 (P1-1: transform 먼저)**

```python
def _run_dql(self, case: Case) -> CaseResult:
    assert case.mysql is not None
    # 변환은 DB 연결 전(P1-1). 예외는 transform/fail.
    pg_sql = self._transform_or_stage(case.mysql)
    my_sql = self._maybe_fix_clock(case, case.mysql, "mysql")
    pg_sql = self._maybe_fix_clock(case, pg_sql, "postgres")

    with (
        self._connect(self._mysql_config, "mysql") as my,
        self._connect(self._postgres_config, "postgres") as pg,
    ):
        my_res = self._run_verified_query(my, "mysql", my_sql)
        pg_res = self._run_verified_query(pg, "postgres", pg_sql)
    return self._compare_results(case, my_res, pg_res)


def _run_verified_query(self, ex: Executor, dialect: str, sql: str) -> QueryResult:
    try:
        return ex.run_query(sql)
    except (StatementTimeout, ConnectionFailure, InfrastructureFailure) as e:
        raise _StageError("infrastructure", "error", f"{type(e).__name__}: {e}") from e
    except SqlExecutionFailure as e:
        if dialect == "mysql":
            raise _StageError(
                "mysql.statement", "error", f"MySQL 피검증 실패: {e}"
            ) from e
        raise _StageError("pg.statement", "fail", f"PG 피검증 실패: {e}") from e
```

- [ ] **Step 5: stage 행렬 단위 테스트 통과**

Run: `pytest tests/test_runner_stage.py -v`
Expected: dql 관련 stage 테스트 PASS(dml/control 테스트는 Step 6 후 통과). transform/mysql.statement/pg.statement/infrastructure/compare/pass 확인.

- [ ] **Step 6: cleanup 우선순위 헬퍼 + dml 경로 구현 (P1-1·P1-2)**

**cleanup 4우선순위(P1-2)**: 본체(setup~post_query)와 cleanup(rollback 등)을 분리 실행하고 아래 규칙으로 최종 결정한다. `_cleanup`은 모든 단계를 시도하며 실패 메시지를 누적한다:

```python
def _cleanup(self, steps: list[Callable[[], None]]) -> str | None:
    """cleanup 단계를 모두 시도. 성공 시 None, 실패 시 누적 메시지 반환(삼키지 않음)."""
    errors: list[str] = []
    for step in steps:
        try:
            step()
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
    return "; ".join(errors) if errors else None


def _finalize(
    self,
    case: Case,
    body_error: _StageError | None,
    cleanup_error: str | None,
    result: CaseResult | None,
) -> CaseResult:
    """본체 결과/예외 + cleanup 결과를 4규칙으로 종합(P1-2)."""
    if body_error is None:
        if cleanup_error is not None:  # ① 본체 성공 + cleanup 실패
            return CaseResult(
                case.id, "error", "infrastructure", f"cleanup 실패: {cleanup_error}"
            )
        assert result is not None
        return result  # 정상
    # 본체 실패
    if cleanup_error is None:  # ② 본체 실패 + cleanup 성공
        return CaseResult(
            case.id, body_error.status, body_error.stage, body_error.reason
        )
    # ③ 본체 실패 + cleanup 실패 → 원 stage 유지 + reason 누적
    return CaseResult(
        case.id,
        body_error.status,
        body_error.stage,
        f"{body_error.reason} | cleanup 실패: {cleanup_error}",
    )


def _run_dml(self, case: Case) -> CaseResult:
    assert case.statement is not None
    pg_stmt = self._transform_or_stage(case.statement)  # P1-1: dml도 연결 전 변환
    with (
        self._connect(self._mysql_config, "mysql") as my,
        self._connect(self._postgres_config, "postgres") as pg,
    ):
        my_res = self._run_state_path(case, my, "mysql", case.statement)
        if isinstance(my_res, CaseResult):  # 본체 실패가 CaseResult로 돌아옴
            return my_res
        pg_res = self._run_state_path(case, pg, "postgres", pg_stmt)
        if isinstance(pg_res, CaseResult):
            return pg_res
    return self._compare_state(case, my_res, pg_res)


def _run_state_path(self, case, ex, dialect, statement):
    """본체 실행 후 cleanup(rollback). 반환: QueryResult|None(성공) 또는 CaseResult(실패)."""
    control = case.control_mysql if dialect == "mysql" else case.control_postgres
    body_error: _StageError | None = None
    result: QueryResult | None = None
    try:
        ex.begin()
        if "setup" in control:
            self._run_control(
                ex, dialect, self._maybe_fix_clock(case, control["setup"], dialect)
            )
        self._run_verified(ex, dialect, self._maybe_fix_clock(case, statement, dialect))
        if "exercise" in control:
            self._run_control(
                ex, dialect, self._maybe_fix_clock(case, control["exercise"], dialect)
            )
        if "post_query" in control:
            result = self._run_control(
                ex,
                dialect,
                self._maybe_fix_clock(case, control["post_query"], dialect),
                query=True,
            )
    except _StageError as e:
        body_error = e
    cleanup_error = self._cleanup([ex.rollback])  # 격리: 반드시 롤백(삼키지 않음)
    if body_error is not None or cleanup_error is not None:
        return self._finalize(case, body_error, cleanup_error, None)
    return result  # 성공: QueryResult|None


def _compare_state(self, case, my_res, pg_res) -> CaseResult:
    if my_res is None or pg_res is None:
        return CaseResult(
            case.id, "pass", None, None
        )  # post_query 없으면 실행 성공=pass
    return self._compare_results(case, my_res, pg_res)
```

> `_run_state_path`는 이제 성공 시 `QueryResult|None`, 실패 시 `CaseResult`를 반환한다(cleanup 결과를 이미 종합). `_run_dml`이 `isinstance(res, CaseResult)`로 분기한다. 검증 대상은 post_query라 `post_query`가 required(Task 0 P1-2차)이므로 정상 dml은 항상 `QueryResult`를 낸다.

- [ ] **Step 7: dml/control stage 단위 테스트 통과**

Run: `pytest tests/test_runner_stage.py -v`
Expected: 전부 PASS(control/error 포함).

- [ ] **Step 8: ddl 경로(_run_ddl) 구현 (P1-3: 연결 전 변환, P1-2: cleanup 4우선순위)**

**P1-3**: 고유명은 연결 없이 결정되므로 `_run_ddl`에서 `name` 계산 → `my_stmt = subst(statement)` → `pg_stmt = _transform_or_stage(my_stmt)`를 **DB 연결 전**에 하고, 각 경로에 준비된 statement를 넘긴다(dql/dml과 동일한 순서). **P1-2**: pre-clean DROP 실패는 본체 미실행 + infrastructure, 본체 cleanup은 `_cleanup`+`_finalize`로 4규칙.

```python
def _run_ddl(self, case: Case) -> CaseResult:
    assert case.statement is not None and case.object is not None
    obj_type = case.object["type"]
    if obj_type not in ALLOWED_OBJECT_TYPES:
        raise _StageError("control", "error", f"지원하지 않는 object type: {obj_type}")
    name = safe_object_name(case.id, case.object["name"])
    my_stmt = case.statement.replace("{{object_name}}", name)  # 고유명 치환(연결 전)
    pg_stmt = self._transform_or_stage(my_stmt)  # P1-3: 연결 전 변환

    with (
        self._connect(self._mysql_config, "mysql") as my,
        self._connect(self._postgres_config, "postgres") as pg,
    ):
        my_res = self._run_ddl_path(case, my, "mysql", obj_type, name, my_stmt)
        if isinstance(my_res, CaseResult):
            return my_res
        pg_res = self._run_ddl_path(case, pg, "postgres", obj_type, name, pg_stmt)
        if isinstance(pg_res, CaseResult):
            return pg_res
    return self._compare_state(case, my_res, pg_res)


def _run_ddl_path(self, case, ex, dialect, obj_type, name, statement):
    """본체 후 cleanup(ROLLBACK→DROP→COMMIT). 성공: QueryResult|None, 실패: CaseResult."""

    def subst(sql: str) -> str:
        return sql.replace("{{object_name}}", name)

    control = case.control_mysql if dialect == "mysql" else case.control_postgres

    # ④ pre-clean: 실패 시 본체 미실행 + infrastructure(DROP IF EXISTS는 객체 없어도 성공).
    pre_error = self._cleanup([lambda: ex.drop_object(obj_type, name), ex.commit])
    if pre_error is not None:
        return CaseResult(
            case.id, "error", "infrastructure", f"pre-clean 실패: {pre_error}"
        )

    body_error: _StageError | None = None
    result: QueryResult | None = None
    try:
        if "setup" in control:
            self._run_control(
                ex,
                dialect,
                subst(self._maybe_fix_clock(case, control["setup"], dialect)),
            )
        self._run_verified(ex, dialect, self._maybe_fix_clock(case, statement, dialect))
        if "exercise" in control:
            self._run_control(
                ex,
                dialect,
                subst(self._maybe_fix_clock(case, control["exercise"], dialect)),
            )
        if "post_query" in control:
            result = self._run_control(
                ex,
                dialect,
                subst(self._maybe_fix_clock(case, control["post_query"], dialect)),
                query=True,
            )
    except _StageError as e:
        body_error = e
    # ②③ aborted 정리: ROLLBACK → 새 트랜잭션 DROP → COMMIT (삼키지 않고 누적).
    cleanup_error = self._cleanup(
        [ex.rollback, lambda: ex.drop_object(obj_type, name), ex.commit]
    )
    if body_error is not None or cleanup_error is not None:
        return self._finalize(case, body_error, cleanup_error, None)
    return result
```

> `statement`는 이미 `_run_ddl`에서 dialect별로 준비됨(MySQL=`my_stmt`, PG=`pg_stmt`). `subst`는 setup/exercise/post_query(제어 SQL)의 `{{object_name}}` 치환용으로 남는다. MySQL 피검증 실패는 `_run_verified`가 `mysql.statement`/error로 잡으므로 stage 계약과 충돌 없음(P1-3 리뷰 확인).

- [ ] **Step 9: dql pass/fail 통합 테스트**

`tests/test_runner.py`:

```python
import pytest
from harness.loader import load_case
from harness.runner import Runner
from harness.transform import PassThroughTransformer
from harness.executor import Executor, MYSQL_CONFIG, POSTGRES_CONFIG, safe_object_name


@pytest.fixture
def runner(mysql_up, postgres_up):
    return Runner(MYSQL_CONFIG, POSTGRES_CONFIG, PassThroughTransformer())


@pytest.mark.integration
def test_dql_standard_passes(runner):
    case = load_case(
        {
            "id": "enum-type",
            "kind": "dql",
            "concepts": ["enum-type"],
            "ordered": True,
            "mysql": "SELECT id, status FROM orders WHERE status='paid' ORDER BY id LIMIT 5",
        }
    )
    r = runner.run_case(case)
    assert r.status == "pass", r.reason


@pytest.mark.integration
def test_dql_backtick_fails_at_pg(runner):
    case = load_case(
        {
            "id": "backtick-identifier",
            "kind": "dql",
            "concepts": ["backtick-identifier"],
            "ordered": True,
            "mysql": "SELECT `id` FROM `products` ORDER BY `id` LIMIT 5",
        }
    )
    r = runner.run_case(case)
    assert r.status == "fail" and r.stage == "pg.statement" and r.reason


@pytest.mark.integration
def test_fixed_clock_same_instant(runner):
    case = load_case(
        {
            "id": "date-function",
            "kind": "dql",
            "concepts": ["date-function"],
            "ordered": True,
            "nondeterministic": {"strategy": "fixed_clock"},
            "mysql": "SELECT id FROM orders WHERE ordered_at < NOW() ORDER BY id LIMIT 5",
        }
    )
    r = runner.run_case(case)
    assert r.status == "pass", r.reason  # NOW() 고정 리터럴 치환으로 양 DB 동일 필터
```

- [ ] **Step 10: dml/ddl 통합 + 격리 불변(P2-5: 양 DB·값·catalog)**

```python
@pytest.mark.integration
def test_dml_upsert_fails_at_pg_and_isolated(runner):
    case = load_case(
        {
            "id": "upsert-on-duplicate",
            "kind": "dml",
            "isolation": "fresh",
            "concepts": ["upsert-on-duplicate"],
            "statement": "INSERT INTO users (id, email, name, created_at) "
            "VALUES (1, 'user1@example.com', 'Upserted', TIMESTAMP '2025-01-01 00:01:00') "
            "ON DUPLICATE KEY UPDATE name = VALUES(name)",
            "post_query": "SELECT name FROM users WHERE id = 1",
        }
    )

    # 양 DB 값 스냅샷(격리 입증: 값까지 불변)
    def snapshot(cfg, dialect):
        with Executor.connect(cfg, dialect) as ex:
            return ex.run_query("SELECT name FROM users WHERE id = 1").rows[0][0]

    my_before = snapshot(MYSQL_CONFIG, "mysql")
    pg_before = snapshot(POSTGRES_CONFIG, "postgres")
    r = runner.run_case(case)
    assert my_before == snapshot(MYSQL_CONFIG, "mysql")  # 'User 1' 복원
    assert pg_before == snapshot(POSTGRES_CONFIG, "postgres")
    assert r.status == "fail" and r.stage == "pg.statement"  # ON DUPLICATE PG 미지원


@pytest.mark.integration
def test_ddl_auto_increment_fails_and_no_leftover(runner):
    case = load_case(
        {
            "id": "auto-increment",
            "kind": "ddl",
            "isolation": "fresh",
            "concepts": ["auto-increment"],
            "object": {"type": "table", "name": "tmp_ai"},
            "statement": "CREATE TABLE {{object_name}} "
            "(id INT AUTO_INCREMENT PRIMARY KEY, label VARCHAR(20) NOT NULL)",
            "exercise": "INSERT INTO {{object_name}} (label) VALUES ('a'),('b'),('c')",
            "post_query": "SELECT id, label FROM {{object_name}} ORDER BY id",
        }
    )
    # 영구 테이블로 바꿔 catalog 부재를 실제로 확인(temp면 연결 종료로 가려짐).
    r = runner.run_case(case)
    assert r.status == "fail" and r.stage == "pg.statement"  # AUTO_INCREMENT PG 미지원
    name = safe_object_name("auto-increment", "tmp_ai")
    for cfg, dialect in [(MYSQL_CONFIG, "mysql"), (POSTGRES_CONFIG, "postgres")]:
        with Executor.connect(cfg, dialect) as ex:
            q = (
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '%s'"
                % name
            )
            assert ex.run_query(q).rows[0][0] == 0  # 양 DB catalog 부재
```

> 코퍼스의 `CREATE TEMPORARY TABLE`은 연결 종료로 사라져 catalog 부재를 입증 못 한다(P2-5). 통합 테스트에선 영구 `CREATE TABLE`로 바꿔 정리를 실증한다. 실제 코퍼스는 TEMPORARY 유지(공유 fixture 오염 방지) — 이 테스트는 정리 로직 자체 검증용.

Run: `pytest -m integration tests/test_runner.py -v`
Expected: PASS.

- [ ] **Step 11: 단위/통합 분리 확인**

Run: `pytest tests/test_runner_stage.py tests/test_runner.py -v`
Expected: stage 단위는 실행·PASS, runner 통합은 deselected.

- [ ] **Step 12: Commit (사용자 지시 시)**

```bash
git add harness/runner.py tests/test_runner_stage.py tests/test_runner.py
git commit -m "feat(harness): Runner(세 kind 변환·stage 분류·cleanup 보존·fixed_clock) 추가"
```

---

## Task 7: report + CLI (P2-6)

**Files:**
- Create: `harness/report.py`, `harness/__main__.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `CaseResult`(6)
- Produces: `def summarize(results) -> str`, `def exit_code(results) -> int`, `harness/__main__.py::main`

- [ ] **Step 1: exit_code/summarize 단위 테스트**

`tests/test_report.py`:

```python
from harness.runner import CaseResult
from harness.report import summarize, exit_code


def _r(status, stage=None, reason=None):
    return CaseResult("c", status, stage, reason)


def test_exit_code_zero_all_pass():
    assert exit_code([_r("pass"), _r("pass")]) == 0


def test_exit_code_nonzero_any_fail():
    assert exit_code([_r("pass"), _r("fail", "compare", "x")]) != 0


def test_exit_code_nonzero_any_error():
    assert exit_code([_r("error", "infrastructure", "x")]) != 0


def test_summarize_counts_and_reason():
    out = summarize([_r("pass"), _r("fail", "pg.statement", "boom")])
    assert "pass" in out and "fail" in out and "boom" in out
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_report.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: report 구현**

`harness/report.py`:

```python
"""CaseResult 리스트를 터미널 요약과 exit code로."""

from __future__ import annotations

from collections import Counter

from harness.runner import CaseResult


def summarize(results: list[CaseResult]) -> str:
    counts = Counter(r.status for r in results)
    lines = [
        f"총 {len(results)}건 — pass={counts.get('pass', 0)} "
        f"fail={counts.get('fail', 0)} error={counts.get('error', 0)}",
        "",
    ]
    for r in results:
        mark = {"pass": "✓", "fail": "✗", "error": "!"}.get(r.status, "?")
        line = f"  {mark} {r.case_id:30s} {r.status}"
        if r.stage:
            line += f" [{r.stage}]"
        if r.reason:
            line += f" — {r.reason}"
        lines.append(line)
    return "\n".join(lines)


def exit_code(results: list[CaseResult]) -> int:
    return 0 if all(r.status == "pass" for r in results) else 1
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_report.py -v`
Expected: PASS

- [ ] **Step 5: CLI 구현**

`harness/__main__.py`:

```python
"""검증 하니스 CLI. 코퍼스를 로드→실행→요약하고 fail/error면 non-zero 종료."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness.executor import MYSQL_CONFIG, POSTGRES_CONFIG
from harness.loader import load_corpus
from harness.report import exit_code, summarize
from harness.runner import Runner
from harness.transform import PassThroughTransformer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQLBridge 검증 하니스")
    parser.add_argument("--cases-dir", type=Path, default=Path("corpus/cases"))
    parser.add_argument("--concepts", type=Path, default=Path("corpus/concepts.yaml"))
    args = parser.parse_args(argv)

    cases = load_corpus(args.cases_dir, args.concepts)
    runner = Runner(MYSQL_CONFIG, POSTGRES_CONFIG, PassThroughTransformer())
    results = [runner.run_case(c) for c in cases]
    print(summarize(results))
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: 통과 확인**

Run: `pytest tests/test_report.py -v`
Expected: PASS

- [ ] **Step 7: Commit (사용자 지시 시)**

```bash
git add harness/report.py harness/__main__.py tests/test_report.py
git commit -m "feat(harness): 리포트·CLI·exit code 추가"
```

---

## Task 8: End-to-end 통합 테스트 (P2-5·P2-6)

**Files:**
- Create: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: `load_corpus`, `Runner`, `PassThroughTransformer`, `exit_code`, `main`, DB configs

- [ ] **Step 1: e2e 테스트 작성 (실제 CLI exit code 필수 — P2-6)**

`tests/test_end_to_end.py`:

```python
import pytest
from pathlib import Path

from harness.loader import load_corpus
from harness.runner import Runner
from harness.transform import PassThroughTransformer
from harness.executor import Executor, MYSQL_CONFIG, POSTGRES_CONFIG

# pass-through 기준 기대 스냅샷. Task 9(Step 3) 실행으로 확정.
EXPECTED_STATUS = {
    "limit-pagination": "fail",
    "ifnull-coalesce": "fail",
    "backtick-identifier": "fail",
    "date-function": "fail",
    "enum-type": "pass",
    "bool-tinyint": "fail",  # P3: PG boolean
    "unsigned-type": "pass",
    "upsert-on-duplicate": "fail",
    "auto-increment": "fail",
    "covering-index": "pass",
    "multi-join": "pass",
    "keyset-vs-offset": "pass",
    "non-sargable-like": "pass",
    "groupby-aggregate": "pass",
}


@pytest.fixture(scope="module")
def results(mysql_up, postgres_up):
    root = Path(__file__).resolve().parent.parent
    cases = load_corpus(root / "corpus" / "cases", root / "corpus" / "concepts.yaml")
    runner = Runner(MYSQL_CONFIG, POSTGRES_CONFIG, PassThroughTransformer())
    return [runner.run_case(c) for c in cases]


@pytest.mark.integration
def test_no_errors(results):
    errors = [(r.case_id, r.stage, r.reason) for r in results if r.status == "error"]
    assert errors == [], f"error 발생(케이스/환경 문제): {errors}"


@pytest.mark.integration
def test_expected_status_snapshot(results):
    assert {r.case_id: r.status for r in results} == EXPECTED_STATUS


@pytest.mark.integration
def test_all_fails_have_stage_and_reason(results):
    for r in results:
        if r.status == "fail":
            assert r.stage and r.reason, f"{r.case_id}: fail인데 stage/reason 없음"


@pytest.mark.integration
def test_seed_invariant_both_dbs(results):
    """dml/ddl 실행 후 양 DB 공유 시드·값 불변(P2-5)."""
    for cfg, dialect in [(MYSQL_CONFIG, "mysql"), (POSTGRES_CONFIG, "postgres")]:
        with Executor.connect(cfg, dialect) as ex:
            assert ex.run_query("SELECT COUNT(*) FROM users").rows[0][0] == 1000
            assert ex.run_query("SELECT COUNT(*) FROM orders").rows[0][0] == 50000
            # upsert 대상 값 복원 확인
            assert (
                ex.run_query("SELECT name FROM users WHERE id = 1").rows[0][0]
                == "User 1"
            )


@pytest.mark.integration
def test_cli_exit_code_nonzero(mysql_up, postgres_up):
    """실제 CLI main()이 fail 존재 시 non-zero 종료(P2-6)."""
    from harness.__main__ import main

    assert main([]) == 1
```

- [ ] **Step 2: e2e 실행으로 error==0 확인 (불변식)**

Run:
```bash
cd docker && docker compose up -d && cd ..
# docker compose ps 로 MySQL healthy 확인 후
pytest -m integration tests/test_end_to_end.py -v
```
Expected: **`test_no_errors` 반드시 PASS**(error==0). 실패하면 그건 버그(코퍼스/코드 수정, 테스트 약화 금지).

- [ ] **Step 3: EXPECTED_STATUS 실측 확정**

`test_expected_status_snapshot`이 실패하면 실제 status를 확인하고 스펙 원칙(피검증 PG 실패=fail, 제어/인프라=error)에 맞는지 검토 후 갱신. `error`가 하나라도 나오면 스냅샷을 맞추지 말고 원인(코퍼스/fixture/코드)을 고친다.

Run: `pytest -m integration tests/test_end_to_end.py -v`
Expected: 전부 PASS.

- [ ] **Step 4: 전체 게이트 통과 확인**

Run: `ruff check . && ruff format --check . && pyright && pytest`
Expected: 전부 통과(pytest는 단위만 — DB 없이). 통합은 `pytest -m integration`으로 별도 확인.

- [ ] **Step 5: Commit (사용자 지시 시)**

```bash
git add tests/test_end_to_end.py
git commit -m "test(harness): 코퍼스 end-to-end 통합 테스트(error==0·격리·CLI exit) 추가"
```

---

## Self-Review 결과

**1. 스펙 커버리지**: Task 0(스펙/코퍼스/validator 수정) → loader(4)/transform+errors(2)/executor(5)/compare(3)/runner(6)/report+CLI(7)/pyproject(1)/e2e(8). 비결정성 3전략: fixed_clock(2+6), exclude_columns(3+6), fixed_seed(비범위). stage 표 6종 전부 Task 6 분기 + Task 6 Step 1 Fake 단위 테스트로 검증.

**2. 리뷰 항목 매핑**:
- P1-1(세 kind 변환·transform 예외) → Task 6 `_transform_or_stage`, dql/dml Step 4·6, ddl Step 8(치환 후 변환)
- P1-2(예외 번역·cleanup 보존) → Task 2 errors, Task 5 `_translate_query`/`_translate_infra`, Task 6 `_run_verified`/`_run_control`
- P1-3(이분 최대 매칭) → Task 0 스펙 수정 + Task 3 `_multiset_equal`(Kuhn) + `test_bipartite_matching_beats_greedy`

**2차 리뷰 반영 매핑**:
- P1-1(dml post_query 필수) → Task 0 Step 4 `pairable_required={"post_query"}` + Step 4b 테스트(setup 없는 dml 통과/post_query 없는 dml 실패)
- P1-2(cleanup 4우선순위) → Task 6 Step 6 `_cleanup`/`_finalize` + `_run_state_path`/`_run_ddl_path`가 성공 시 QueryResult·실패 시 CaseResult 반환 + Step 1 cleanup 실패 Fake 테스트(`test_cleanup_fail_on_body_success_becomes_infra` 등)
- P1-3(DDL 연결 전 변환) → Task 6 Step 8 `_run_ddl`에서 name→subst→transform을 연결 전, `_run_ddl_path(...,name,statement)` 시그니처
- P1-4(InfrastructureFailure + 모든 DB 호출 번역 + 마지막 방어선) → Task 2 `InfrastructureFailure`, Task 5 `_translate_infra`가 timeout SET·commit·rollback·cursor·fetch·close 감쌈, Task 6 `run_case`의 `except Exception` + `_run_verified`/`_run_control`가 InfrastructureFailure→infrastructure
- P1-4(정수·Decimal 정확) → Task 3 `_value_equal` + `test_large_ints`/`test_decimal_scale`
- P1-5(upsert 코퍼스) → Task 0 Step 2(기존 시드 행) + validator setup optional
- P1-6(MySQL DML/DDL timeout 비범위) → Task 0 Step 1(스펙) + Task 5 `_apply_timeout` 주석
- P2-1(fixed_clock allowlist) → Task 2 `_CLOCK_ANON_NAMES` + Step 8~9 실측
- P2-2(컬럼명·datetime 계약) → Task 3 `columns_a != columns_b` + `test_datetime_same_instant_diff_repr`/`naive`
- P2-3(ordered 통일) → Task 0 Step 3~5 + Task 4 `test_dml_ordered_loaded`
- P2-4(stage 행렬 단위) → Task 6 `test_runner_stage.py`(Fake 주입)
- P2-5(양 DB·catalog 격리 입증) → Task 6 Step 10 + Task 8 `test_seed_invariant_both_dbs`
- P2-6(실제 CLI) → Task 8 `test_cli_exit_code_nonzero`
- P3(bool-tinyint fail, object type whitelist) → EXPECTED_STATUS + `ALLOWED_OBJECT_TYPES`

**3. 타입 일관성**: `CaseResult`/`QueryResult`/`Comparison`/`compare(...,ordered,exclude_columns)`/`safe_object_name(case_id, object_name)`/`fix_clock(sql, ts, *, dialect)`/`Executor.connect(config, dialect)`/`Runner(...executor_factory=)` — 전 태스크 시그니처 일치.

**구현 중 실측으로 확정(계획 아님)**:
- SQLGlot 노드 매핑(NOW/CURDATE 등 방언별) — Task 2 Step 9
- PyMySQL/psycopg timeout·연결 예외 클래스·에러코드 — Task 5 Step 8
- 수정된 코퍼스 기준 14개 최종 status 스냅샷(error==0은 불변) — Task 8 Step 3
