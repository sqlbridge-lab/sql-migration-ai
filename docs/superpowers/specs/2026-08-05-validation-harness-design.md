# SQLBridge AI — 결과 비교 검증 하니스 설계

## Status: Codex 2차 리뷰(PG 시각·트랜잭션) 반영 (재리뷰 대기)

> [!NOTE]
> **Codex 리뷰 반영**:
> - (P1-1 비결정성) 비범위였던 비결정성을 범위에 포함. 코퍼스에 이미 `fixed_clock` 케이스가
>   있어 미지원 시 검증 불가였다. 세 전략 계약 명시 + fixed_clock/exclude_columns 구현.
> - (P1-2 DDL 고유명) kebab-case case_id의 하이픈이 식별자 문법 오류를 냈다. `safe_id`로
>   정규화 + 길이 제한·해시 접미사 + 실행 전 DROP 추가.
> - (P1-3 fail/error) `stage`로 세분화(transform/mysql.statement/pg.statement/control/
>   infrastructure/compare). 피검증 SQL 실패만 fail, 제어·인프라는 error.
> - (P1-4 float+multiset) Counter는 근사 float를 못 다뤄, 탐욕적 multiset 매칭으로 교체.
> - (P2-5 흐름 누락) 공통 실행 순서 setup→statement→exercise→post_query로 통일(dml exercise·
>   ddl setup 반영).
> - (P2-6 E2E 약함) 기대 pass/fail 목록·error==0·stage/reason·격리 불변·CLI non-zero로 강화.
>
> **Codex 2차 리뷰 반영**:
> - (P1-1 PG now() 고정) PostgreSQL은 `SET`으로 `now()`를 못 고정한다. `fixed_clock`을 세션
>   주입에서 **현재시각 함수 → 고정 리터럴 SQLGlot AST 치환**으로 변경(양 DB 동일 처리).
> - (P1-2 aborted 트랜잭션 정리) PG는 트랜잭션 abort 시 finally DROP도 실패한다. DDL 정리에
>   **트랜잭션 경계 명시**(실행 전 DROP 독립 COMMIT, finally는 ROLLBACK→DROP→COMMIT).

> [!NOTE]
> 상위 스펙:
> - `2026-07-31-experiment-foundation-design.md` — 실험 환경·비교 계약의 큰 그림.
> - `2026-07-31-corpus-implementation-design.md` — 코퍼스(비교 계약·케이스 스키마·씨드).
>
> 이 스펙은 코퍼스가 **문서로 명세**한 비교 계약·케이스 스키마를 **실제로 실행하는 하니스**로
> 구현하는 스펙이다. 계약의 근거·트레이드오프는 상위 스펙에 있으므로 반복하지 않는다.

## 배경

코퍼스(이슈 #2)가 완성돼 "무엇을·어떤 기준으로 검증할지"가 데이터·규칙으로 고정됐다. 다음으로
필요한 건 그 케이스를 **실제로 두 DB에 돌려 결과를 비교하고 통과/실패를 판정하는 실행기** =
검증 하니스다.

변환 정답(`expect`)을 케이스에 손으로 적지 않는 게 이 프로젝트의 전제다. 대신 **MySQL 원본
결과와 PostgreSQL 변환본 결과를 비교**해 "같으면 통과"로 판정한다(오라클 방식). 이 판정기가
있어야 앞으로 만들 대량 케이스(생성기)와 변환 엔진의 품질을 측정하며 개선할 수 있다.

## 변환기 없이 만드는 하니스 (핵심 전제)

하니스의 정상 흐름은 `케이스 → [피검증 SQL 변환] → 양쪽 실행 → 비교`다. 변환 엔진(이슈 C)은
아직 없다. 하지만 하니스의 알맹이는 **실행·비교·격리·판정**이고, 변환은 그중 한 단계일 뿐이다.

그래서 변환 단계를 **주입 가능한 인터페이스(`Transformer`)**로 두고, 이번엔 입력 SQL을 그대로
반환하는 **pass-through 구현**을 끼운다. 실행·비교·격리 골격을 먼저 검증하고, 나중에 변환 엔진이
완성되면 **같은 인터페이스로 갈아끼우기만** 하면 된다(하니스 코드는 안 바뀐다).

- **부수 효과(예상된 동작)**: pass-through 단계에서는 MySQL 전용 문법(백틱, `LIMIT offset,count`
  등)이 PostgreSQL에서 실행 실패한다. 이는 버그가 아니라 "아직 변환기가 없어서"이며, 하니스는
  이를 **`fail`로 정직하게 리포트**한다. 변환 엔진이 나오면 fail→pass로 바뀌는 것이 곧 변환
  엔진의 진행도 지표가 된다. (별도 `expected_fail` 플래그는 두지 않는다.)

## 범위

- `harness/loader.py` — `CaseLoader`. 케이스 YAML을 `Case` 객체로. 제어 SQL 공통형·DB별 쌍을
  DB별로 정규화. 형식 검증은 기존 `validate_corpus`를 재사용.
- `harness/transform.py` — `Transformer` Protocol + `PassThroughTransformer`.
- `harness/executor.py` — `Executor`. 한 DB에 SQL 실행, 격리(트랜잭션/DROP), DDL 고유명 생성,
  fixed_seed 세션 주입(생기면) 담당. (fixed_clock은 실행 전 SQL 치환이라 Runner 전처리.)
- `harness/compare.py` — `Comparator`. 두 결과셋을 비교(정렬/이분 최대 매칭/타입정규화/
  오차/NULL/exclude_columns).
- `harness/runner.py` — `Runner`. 케이스를 로드→변환→양쪽 실행→비교해 `CaseResult` 생성.
- `harness/report.py` — `CaseResult` 리스트를 터미널 요약으로, exit code 산출.
- `harness/__main__.py` — CLI 진입점.
- `pyproject.toml` — `PyMySQL`, `psycopg` dev 의존성 추가.
- `tests/` — 단위 테스트(Comparator 중심) + 통합 테스트(`@pytest.mark.integration`).

## 비범위 (다음 스펙들)

- **변환 엔진** (SQLGlot MySQL→PG). 이번엔 pass-through로 자리만 비워둔다.
- **Performance Analyzer / perf 룰 판정** — TREE/JSON plan adapter는 별도 스펙.
- **collation** — 코퍼스가 씨드로 회피했으므로(문자열=바이트 동일) 이번 비교기는 별도 처리 안 함.
- **`fixed_seed` 비결정성 구현** — 코퍼스에 케이스가 없어 계약만 명시하고 구현은 생기면.
  (`fixed_clock`·`exclude_columns`는 코퍼스에 쓰이므로 이번에 구현 — 비범위 아님.)
- **케이스 생성기 / 1000+ 케이스**.

## 설계

### 아키텍처 & 컴포넌트

```
harness/
  loader.py       CaseLoader    케이스 YAML → Case
  transform.py    Transformer   피검증 SQL 변환 (Protocol + PassThrough)
  executor.py     Executor      DB 연결·실행·격리
  compare.py      Comparator    두 결과셋 비교 → 동일 여부 + 사유
  runner.py       Runner        조립, 케이스별 CaseResult
  report.py       리포트        CaseResult[] → 터미널 요약 + exit code
  __main__.py     CLI
```

| 컴포넌트 | 하는 일 | 의존 | 테스트 |
|----------|---------|------|--------|
| `CaseLoader` | YAML → `Case`. 제어 SQL 쌍 정규화 | PyYAML, validate_corpus | dict 단위 |
| `Transformer` | MySQL SQL → PG SQL (Protocol) | 없음 | 껍데기 |
| `Executor` | 한 DB에 실행 + 격리 | 드라이버 | 통합(DB) |
| `Comparator` | 두 결과셋 비교. **DB를 모름** | 없음 | 단위(다수) |
| `Runner` | 케이스 하나 조립 실행 | 위 전부 | 통합(DB) |
| `report` | 요약·exit code | 없음 | 단위 |

**핵심 경계**: `Comparator`는 드라이버·DB를 전혀 모르고 "행 리스트 두 개 + ordered"만 받는다.
로직이 가장 몰리는 곳(타입정규화·NULL·오차)을 DB 없이 단위 테스트하기 위한 분할이다.
`Transformer`는 Protocol이라 이후 변환 엔진을 같은 시그니처로 끼우면 Runner는 안 바뀐다.

### 데이터 흐름 (kind별 경로)

`CaseLoader`가 `Case`를 만들고, `Runner`가 kind로 분기한다. 세 경로 모두 마지막에
`Comparator`로 수렴한다.

**dql (조회)**
```
1. Transformer로 mysql SQL 변환 → pg_sql
2. Executor(MySQL): mysql 실행 → rows_mysql
3. Executor(PG):    pg_sql 실행 → rows_pg
   └ 변환/실행 실패 시 CaseResult(에러 상태, 사유) 반환
4. Comparator(rows_mysql, rows_pg, ordered) → 동일 여부 + 사유
5. CaseResult
```

**공통 실행 순서**: dml·ddl은 같은 순서를 따른다. 케이스가 해당 필드를 안 적었으면 그 단계는
건너뛴다(있는 필드는 절대 조용히 무시하지 않는다 — 검증기가 dml `exercise`·ddl `setup`을
허용하므로 Runner도 실행해야 한다).

```
setup → statement → exercise → post_query → (cleanup/rollback)
        └피검증┘    └────── 제어 SQL(변환 안 함) ──────┘
```

검증 대상은 statement 결과가 아니라 **post_query 결과**다.

**dml (변경 → 상태 검증)**: 한 커넥션의 한 트랜잭션에서 실행 후 ROLLBACK으로 공유 시드 복원.
```
각 DB에서 (같은 트랜잭션):
  START TRANSACTION
  setup 실행      (있으면, 제어 SQL)
  statement 실행  (피검증 — MySQL 원본 / PG 변환본)
  exercise 실행   (있으면, 제어 SQL)
  post_query 실행 → rows (있으면, 제어 SQL)
  ROLLBACK              ← finally 보장
Comparator(rows_mysql, rows_pg)
```

**ddl (스키마 → 상태 검증)**: `{{object_name}}`을 고유명으로 치환. 정리는 실행 전·후 DROP.

**PG 트랜잭션 abort 주의**: PostgreSQL은 트랜잭션 안에서 문장 하나가 실패하면 트랜잭션 전체가
aborted가 되어, 이후 모든 문장(정리 DROP 포함)이 거부된다. 그래서 정리 DROP을 실패한 트랜잭션
안에서 돌리면 안 된다. 아래처럼 **트랜잭션 경계를 명시**한다.

```
각 DB에서:
  name = 고유명(아래 "DDL 고유명 규칙")

  # ① 실행 전 정리 — 독립 트랜잭션으로 커밋(롤백에 휩쓸리지 않게)
  DROP <object.type> IF EXISTS name;  COMMIT

  try:
    setup 실행      (있으면, 치환, 제어 SQL)
    statement 실행  ({{object_name}}→name, 피검증)
    exercise 실행   (있으면, 치환, 제어 SQL)
    post_query 실행 → rows (있으면, 치환, 제어 SQL)
  finally:
    # ② 본체가 실패했으면 트랜잭션이 aborted → 먼저 ROLLBACK으로 깨끗이 하고
    ROLLBACK
    # ③ 새 트랜잭션에서 정리 DROP 후 COMMIT (trusted, 변환 안 함)
    DROP <object.type> IF EXISTS name;  COMMIT
Comparator(rows_mysql, rows_pg)
```
(MySQL은 DDL이 implicit commit이라 abort 문제가 없지만, 경로를 통일해 양 DB 동일 코드로 둔다.)

**DDL 고유명 규칙**: `sqlbridge_{safe_id}_{object_name}` — `safe_id`는 case_id의 하이픈 등
식별자로 못 쓰는 문자를 `_`로 바꾼 값이다(예: `auto-increment` → `auto_increment`). 그대로
치환하면 하이픈 때문에 MySQL/PG 문법 오류가 나기 때문이다. 이름이 DB 식별자 길이 제한(MySQL
64자)을 넘으면 뒤를 잘라내고 case_id 해시 접미사를 붙여 충돌을 막는다. 이름이 결정적이라
실행 전 DROP으로 이전 실행 잔재도 정리된다.

제어 SQL(setup/exercise/post_query)은 공통형 또는 `_mysql`/`_postgres` 쌍으로 적혀 있다.
`CaseLoader`가 DB별로 정규화하고, Runner는 각 DB에서 해당 DB용 SQL을 꺼내 쓴다.

### 에러 처리

케이스 하나의 실패가 전체 실행을 멈추면 안 된다. 예외를 `CaseResult`로 변환한다.

**상태 3종**:
- `pass` — 실행되고 결과가 같음.
- `fail` — **변환기 품질** 문제. 피검증 SQL의 변환·PG 실행 실패, 결과 불일치. 변환 엔진이
  나오면 고쳐질 것.
- `error` — **케이스/제어/인프라** 문제. 지금 사람이 고쳐야 할 것(하니스나 케이스나 환경).

핵심은 **변환기 품질(fail)과 그 외(error)를 가르는 것**이다. 그래야 pass-through 통과율이
변환 엔진의 진행도 지표로 오염 없이 쓰인다. `stage`로 어디서 갈렸는지 남긴다.

| 실패 지점 | stage | 상태 | 사유 |
|-----------|-------|------|------|
| 변환기 unsupported/parse 실패 | `transform` | `fail` | 변환기가 아직 이 SQL을 못 바꿈 (품질) |
| **피검증** SQL의 PG 실행 실패 | `pg.statement` | `fail` | 변환 결과가 PG에서 안 돎 (품질) |
| 비교 불일치 | `compare` | `fail` | 첫 불일치 요약 |
| **피검증** SQL의 MySQL 실행 실패 | `mysql.statement` | `error` | 케이스 SQL이 틀림 |
| **제어** SQL 실행 실패(양 DB) | `control` | `error` | setup/exercise/post_query 오류 |
| DB 연결·인증·타임아웃 | `infrastructure` | `error` | 환경/하니스 문제 |
| 통과 | — | `pass` | — |

- **피검증 SQL(mysql/statement)의 실패만** 변환기 품질(fail)로 본다. MySQL 원본이 실패하면
  변환 이전 문제라 `error`, PG에서 변환본이 실패하면 변환 품질이라 `fail`.
- **제어 SQL(setup/exercise/post_query)의 실패는 양 DB 모두 `error`**다. 변환하지 않는
  검증 오라클이라, 여기서 나는 오류는 변환기 품질과 무관하다.
- **연결·인증·타임아웃은 `error`(infrastructure)**다. 변환 실패로 오집계하지 않는다.

**격리 보장**: dml ROLLBACK·ddl DROP을 `finally`에 둔다(피검증 SQL이 던져도 정리). 커넥션은
context manager(`with`)로 열어 항상 닫는다.

**타임아웃**: 잘못된 케이스가 매달리지 않게 실행 쿼리에 statement timeout(예: 30초)을 건다.
초과 시 `error`(infrastructure). MVP라 고정 상수 하나. **PG는 `statement_timeout`으로 모든
문장에 timeout을 보장**하지만, **MySQL `MAX_EXECUTION_TIME`은 read-only SELECT 전용**이라
**MySQL DML/DDL은 timeout 비보장(비범위)**이다. watchdog+KILL은 MVP 과대이고, 코퍼스 케이스는
매달리지 않으므로 실무 위험이 없다.

### 핵심 데이터 모델

```python
@dataclass
class Case:
    id: str
    kind: str                    # dql | dml | ddl
    concepts: list[str]
    mysql: str | None            # dql 피검증 SQL
    statement: str | None        # dml/ddl 피검증 SQL
    ordered: bool
    isolation: str | None
    object: dict | None          # ddl: {type, name}
    nondeterministic: dict | None   # {strategy, columns?} — 아래 "비결정성 처리"
    control_mysql: dict[str, str]   # setup/exercise/post_query (MySQL용, 정규화 후)
    control_postgres: dict[str, str]

@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]            # 드라이버 원본 타입 (Decimal/int/datetime/None)

@dataclass
class Comparison:
    equal: bool
    reason: str | None

@dataclass
class CaseResult:
    case_id: str
    status: str                  # pass | fail | error
    stage: str | None            # transform | mysql.statement | pg.statement | control | infrastructure | compare
    reason: str | None
```

**설계 포인트**:
1. `Case.control_*` — 제어 SQL 쌍 처리 복잡도를 Loader가 흡수해 Runner를 단순화한다.
2. `QueryResult.rows`는 **드라이버 원본 타입**이다(문자열 아님). Comparator의 타입 정규화가
   성립하는 전제다.

**행 동등성 판정(row_equal)** — 두 행이 같은지는 값별로 판정한다:
- 정수 → **정확 비교**(오차 없음). 큰 정수를 float로 붕괴시키지 않는다.
- Decimal → **스케일만 흡수한 정확 수치 비교**(`10.00 == 10.0`). 값 붕괴 금지 — float로
  바꾸지 않고 Decimal끼리(또는 Decimal↔정수) 정확 비교한다.
- MySQL 0/1(int) ↔ PG bool → 통일(상대가 **정확히 0/1**일 때만; 근사 아님).
- datetime → UTC 순간
- float → **float가 관여할 때만 1e-9 절대·상대 오차 안이면 같음**(근사). 정수·Decimal에는
  이 오차를 적용하지 않는다.
- None → NULL로 비교

**행 집합 비교** — float 근사 때문에 "행 동등성"이 **정확한 해시 동등성이 아니다.** 그래서
unordered에서 `Counter`(해시 기반)를 쓸 수 없다(`3.0000000001`과 `3.0000000002`는 오차 안이지만
해시 키가 다르다). 대신:
```
1. columns 개수·이름 대응 확인
2. ordered=true → 두 리스트를 index별로 row_equal
3. ordered=false → 이분 그래프 최대 매칭:
     rows_a[i]와 rows_b[j]가 row_equal이면 간선을 두고, 완전 매칭(모든 a가
     매칭)이 존재하는지 증가 경로 탐색(Kuhn/Hopcroft–Karp)으로 판정한다.
     행 개수는 먼저 비교하므로, 완전 매칭이 있으면 양쪽이 같은 multiset이다.
```
**탐욕적 매칭은 반례가 있어 금지한다.** 오차 안의 값이 여러 상대와 매칭 가능할 때,
소비 순서에 따라 완전 매칭을 놓칠 수 있다. 예: A=[0.9e-9, 0], B=[0, 1.8e-9], tol=1e-9
→ 0.9e-9가 먼저 0을 소비하면 남은 0이 1.8e-9와 매칭 못 해 실패하지만, 실제로는
0.9e-9↔1.8e-9·0↔0으로 완전 매칭이 존재한다. 최대 매칭은 중복 float 행·경계값도 개수까지
정확히 판정한다.

collation은 코퍼스가 씨드로 회피했으므로(문자열=바이트 동일) 별도 정규화하지 않는다.

### 비결정성 처리

코퍼스에 이미 `nondeterministic` 케이스가 있다(`date-function`이 `fixed_clock`). 하니스가 이를
처리하지 않으면 이 케이스를 계약대로 검증할 수 없다. 세 전략의 **계약을 모두 명시**하되,
이번 구현은 코퍼스에 실제 쓰이는 것만 한다.

| strategy | 처리 | 이번 구현 |
|----------|------|-----------|
| `fixed_clock` | 실행 전 양 DB SQL의 `NOW()`/`CURRENT_TIMESTAMP` 등 현재시각 함수를 **같은 고정 timestamp 리터럴로 치환**(SQLGlot AST 전처리). | **구현**(코퍼스에 있음) |
| `exclude_columns` | Comparator가 `columns`에 적힌 열을 **비교에서 제외**하고 나머지로 판정. | **구현**(간단, 값 비교 전 열 드롭) |
| `fixed_seed` | 실행 전 양 DB에 **같은 난수 시드**를 설정해 난수 수열을 일치. | 계약만 명시(코퍼스에 케이스 없음 — 생기면 구현) |

- **`fixed_clock`은 세션 주입이 아니라 SQL 치환이다.** PostgreSQL은 `SET`으로 `now()`를 고정할
  수 없다(타임존만 설정 가능, 현재시각 자체는 못 덮어씀). MySQL만 `SET TIMESTAMP`가 되는
  비대칭이라, 양 DB를 동일하게 처리하려고 **현재시각 함수를 고정 리터럴로 AST 치환**한다.
  치환은 피검증·제어 SQL 모두에 적용하고, 실행 전 별도 전처리 단계로 둔다(변환기 C와는 무관한
  오라클 고정 처리). SQLGlot을 쓴다(이미 프로젝트 핵심 의존성).
  - **지원 현재시각 함수 allowlist**: `NOW()`, `CURRENT_TIMESTAMP`, `LOCALTIMESTAMP`
    (→ 고정 timestamp 리터럴), `CURDATE()`/`CURRENT_DATE`(→ 고정 date 리터럴), `SYSDATE()`
    (→ 고정 timestamp 리터럴). 그 외 현재시각 함수는 **미지원(비범위)**이며, 코퍼스에는
    allowlist 함수만 쓴다.
- `exclude_columns`는 Comparator가 열을 뺀다(Runner가 `Case.nondeterministic`을 Comparator에 전달).
- `fixed_seed`는 생길 때 Executor 세션 설정으로 주입한다(이번 미구현).
- 어느 전략이든 `Case.nondeterministic`이 있으면 Runner가 해당 경로로 태운다.

### 테스트 전략

**단위 테스트 (DB 불필요, 대다수)** — `Comparator`에 집중:
- 같은 결과 → equal
- 순서 다름: ordered=true fail / false pass
- 중복 개수 다름 → fail
- DECIMAL `10.00` vs `10.0` → equal
- MySQL `1`(int) vs PG `True`(bool) → equal
- datetime 표현 다르나 같은 순간 → equal
- float 1e-9 안 equal / 밖 fail
- **float 근사 + unordered**: 오차 안의 서로 다른 float 행이 매칭됨(Counter였으면 실패할 케이스)
- **중복 float 행**: 같은 근사값이 2개면 양쪽 2개일 때만 equal(개수까지)
- **경계값**: 오차 임계 바로 안/밖에서 equal/fail이 갈림
- NULL 포함 행 정상 비교
- 컬럼 개수 다름 → fail
- `exclude_columns`: 지정 열을 빼고 비교(그 열 값이 달라도 나머지 같으면 equal)

`CaseLoader`(제어 SQL 정규화: 공통형→양쪽, 쌍→분리; dml `exercise`·ddl `setup`도 실린다),
`report`(요약·exit code), `PassThroughTransformer`(입력 그대로)도 단위 테스트.

**통합 테스트 (실제 DB 필요, 소수, `@pytest.mark.integration`)**:
- `Executor`: SELECT 실행 → 행 반환
- dml 격리: statement 후 ROLLBACK → 시드 행 수 불변
- ddl 격리: 실행 전·후 DROP, TEMPORARY TABLE 생성 후 잔여 없음, 하이픈 case_id도 유효 식별자,
  **PG statement 실패 시에도 정리 DROP 성공**(ROLLBACK→DROP→COMMIT)
- `fixed_clock`: 현재시각 함수를 고정 리터럴로 치환한 뒤 양 DB가 같은 값
- **end-to-end (강한 성공 조건)**: 코퍼스 14개를 Runner로 실행하고 아래를 모두 고정 검증:
  - **케이스별 기대 pass/fail 목록**과 실제가 일치(pass-through 기준 스냅샷)
  - **`error == 0`** (하나라도 error면 케이스/환경 문제 → 실패)
  - 모든 `fail`은 `stage`와 `reason`이 채워져 있다
  - 실행 후 **dml 시드 행 수·ddl 객체가 불변**
  - CLI가 fail/error 존재 시 **non-zero 종료**

**분리 이유**: pre-push 게이트의 `pytest`가 DB 없이 통과해야 한다(개발 머신에 컨테이너가 항상
떠 있지 않음). DB 필요한 건 마커로 갈라 기본 실행(`pytest`)에서 빼고, `pytest -m integration`은
컨테이너가 떠 있을 때만 돌린다.

## 태스크

- [ ] `pyproject.toml` — `PyMySQL`, `psycopg` dev 의존성 + pytest `integration` 마커 등록 →
      검증: `pip install -e ".[dev]"` 후 두 드라이버 import 성공
- [ ] `harness/transform.py` — `Transformer` Protocol + `PassThroughTransformer` +
      `fix_clock(sql, ts)`(NOW()/CURRENT_TIMESTAMP → 고정 리터럴 SQLGlot 치환) →
      검증: pass-through가 입력 그대로 반환, fix_clock이 현재시각 함수를 리터럴로 바꾸는 단위 테스트
- [ ] `harness/compare.py` — `Comparator`(정렬/**이분 최대 매칭**/타입정규화/오차/NULL/
      `exclude_columns`) → 검증: 위 단위 테스트 목록 전부 통과(float 근사+unordered·중복·경계 포함)
- [ ] `harness/loader.py` — `CaseLoader`(제어 SQL 쌍 정규화, `nondeterministic` 로드) →
      검증: 공통형·쌍이 control_mysql/postgres로 갈리고, dml exercise·ddl setup도 실린다
- [ ] `harness/executor.py` — `Executor`(연결·실행·격리·타임아웃·**DDL 고유명**·**트랜잭션 경계**) →
      검증(통합): SELECT 실행, dml ROLLBACK·ddl 실행 전후 DROP 후 시드/스키마 불변,
      하이픈 case_id가 유효 식별자, **PG에서 statement 실패해도 정리 DROP이 성공**(rollback 후 DROP)
- [ ] `harness/runner.py` — `Runner`(kind별 경로 조립, 공통 실행 순서, fixed_clock 전처리·
      비결정성 분기, stage 분류) → 검증(통합): dql/dml/ddl 각 경로가 CaseResult를 만들고
      stage가 올바로 붙으며, fixed_clock 케이스가 양 DB 같은 시각으로 실행된다
- [ ] `harness/report.py` + `__main__.py` — 터미널 요약·exit code·CLI →
      검증: CaseResult 리스트로 요약·exit code(fail/error면 non-zero) 확인(단위) + CLI 실행(통합)
- [ ] end-to-end 통합 테스트 — 코퍼스 14개를 Runner로 실행 →
      검증: 기대 pass/fail 목록 일치, error==0, fail은 stage·reason 존재, dml/ddl 격리 불변,
      CLI non-zero 종료

## 결정 근거 (trade-off)

- **변환기를 Protocol로 두고 pass-through**: 하니스의 알맹이는 실행·비교·격리라 변환 없이도
  검증할 수 있다. 변환을 인터페이스로 끊으면 변환 엔진(C)이 나올 때 하니스를 안 고치고
  갈아끼운다. B가 C의 선제조건인 이유가 이 실행·비교 골격이다.
- **Python 드라이버(원본 타입 유지)**: CLI 텍스트 출력으로는 DECIMAL 스케일·bool 0/1·datetime을
  제대로 비교할 수 없다. 드라이버가 주는 실제 타입이 Comparator 정규화의 전제다.
- **Comparator를 DB 무지(순수)로 분리**: 로직이 가장 몰리는 곳을 DB 없이 단위 테스트해 신뢰를
  얻는다. kind로 컴포넌트를 나누지 않은 것도, dml/ddl이 결국 같은 비교기를 재사용하기 때문이다.
- **pass/fail/error를 stage로 세분화**: 상태만으론 부족하다. 피검증 SQL의 변환·PG 실행 실패만
  변환기 품질(fail)이고, 제어 SQL 오류·연결/인증/타임아웃은 error다. 이걸 안 가르면 PG 연결
  실패가 변환 실패로, 변환기 unsupported가 케이스 오류로 오집계돼 통과율 지표가 오염된다.
- **비결정성을 이번 범위에 포함**: 코퍼스에 이미 `fixed_clock` 케이스가 있어, 하니스가 이를
  처리하지 않으면 그 케이스를 계약대로 검증할 수 없다. 세 전략 계약을 명시하고 쓰이는
  것(fixed_clock/exclude_columns)을 구현한다(fixed_seed는 케이스가 없어 계약만).
- **fixed_clock은 세션 주입이 아니라 SQL 치환**: PostgreSQL은 `SET`으로 `now()`를 고정할 수
  없어(타임존만 가능) 세션 주입이 양 DB 비대칭이다. 그래서 현재시각 함수를 고정 리터럴로 AST
  치환해 양쪽을 동일 방식으로 처리한다(확장 불필요). SQLGlot은 이미 프로젝트 핵심 의존성이다.
- **DDL 고유명은 안전 식별자로 정규화 + 실행 전 DROP**: `sqlbridge_{case_id}_...`을 그대로 쓰면
  kebab-case id의 하이픈 때문에 문법 오류가 난다. case_id를 `_`로 정규화하고 길이 제한·해시
  접미사를 둔다. stale object는 finally뿐 아니라 실행 전 DROP으로도 지운다.
- **DDL 정리는 PG 트랜잭션 경계를 명시**: PostgreSQL은 트랜잭션 안 한 문장이 실패하면 전체가
  aborted라, 실패한 트랜잭션 안의 정리 DROP도 거부된다. 그래서 finally에서 먼저 ROLLBACK 후
  새 트랜잭션에서 DROP+COMMIT하고, 실행 전 DROP도 독립 COMMIT해 정리가 롤백에 휩쓸리지 않게 한다.
- **unordered 비교는 Counter 대신 이분 최대 매칭**: float를 근사 비교하면 행 동등성이 정확한
  해시 동등성이 아니다. Counter는 오차 안의 다른 float를 다른 키로 봐 틀린다. 탐욕적 매칭도
  소비 순서 의존으로 완전 매칭을 놓치는 반례가 있어(위 "행 집합 비교" 참조) 쓰지 않는다.
  row_equal 간선의 이분 그래프에서 완전 매칭 존재를 증가 경로 탐색(Kuhn)으로 판정해 중복·경계값까지
  개수 정확히 판정한다.
- **격리는 코퍼스 계약 그대로(dml 롤백/ddl DROP)**: MySQL DDL은 implicit commit이라 롤백이 안
  돼 종류를 나눈다. 매 케이스 DB 재생성은 5만 행 재주입이라 느리고 계약과도 어긋난다.
- **통합 테스트를 마커로 격리**: pre-push의 pytest가 DB 없이 통과해야 한다. DB 필요한 건 갈라
  기본 실행에서 뺀다.
- **스펙 파일 위치**: 기존 스펙들이 `docs/superpowers/specs/`에 있어 관행을 따른다.
