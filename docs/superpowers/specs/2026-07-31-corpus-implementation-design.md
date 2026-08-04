# SQLBridge AI — 검증 코퍼스 구현 설계

## Status: Codex 3차 리뷰(#1 teardown 객체명, #2 params, #3 examined-row, #4 collation) 반영 (재리뷰 대기)

> [!NOTE]
> 상위 스펙 `2026-07-31-experiment-foundation-design.md`가 코퍼스의 **설계**(파일 구조,
> 비교 계약, 케이스 스키마, 씨드 케이스 목록, 정적 검증 범위)를 이미 정의했다. 이 스펙은
> 그 설계를 **실제 파일과 검증 절차로 분해**하는 구현 스펙이다. 설계 근거·트레이드오프는
> 상위 스펙에 있으므로 반복하지 않고 링크로 대신한다.

> [!NOTE]
> **Codex 1차 리뷰 반영**: 실행 모델·DDL 격리·auto-increment 임시 테이블·concepts.yaml
> 단일 원본·unknown field 실패·PyYAML 의존성·perf 룰 구체화. (아래는 2차 반영으로 일부
> 재수정됨.)
>
> **Codex 2차 리뷰(Request changes) 반영**:
> - (#1 오라클 순환) 피검증 SQL(`mysql`/`statement`)만 변환한다. 제어 SQL(`setup`/
>   `exercise`/`post_query`/`teardown`)은 **양 DB 공통 SQL을 기본**으로 하고, 문법이 갈리면
>   `_mysql`/`_postgres` 쌍으로 분리해 적는다(변환기에 의존하지 않는 검증 오라클).
> - (#2 DDL 생명주기) DDL 필드에 `exercise`·`teardown`을 추가해 CREATE→INSERT→SELECT→DROP
>   4단계를 분리. teardown은 하니스가 고유 객체명으로 finally에서 **trusted DROP**(변환기
>   비의존), 재실행 전 stale object도 정리. TEMPORARY TABLE이면 4단계가 같은 커넥션에서.
>   *(→ 3차에서 `teardown` 필드 제거·`object`+`{{object_name}}`으로 대체. 아래 3차 참조.)*
> - (#3 MySQL EXPLAIN 형식) MySQL 8.4는 EXPLAIN ANALYZE의 JSON을 지원하지 않는다(실측 확인:
>   `ERROR 1235 ... 'EXPLAIN ANALYZE with JSON format'`). **MySQL은 `EXPLAIN ANALYZE
>   FORMAT=TREE`, PostgreSQL은 `EXPLAIN (ANALYZE, FORMAT JSON)`** — DB별 plan adapter.
> - (#4 examined-row·relation scope) perf 룰에 **대상 relation**을 명시. examined 행은
>   접근 노드의 반환 행 + Rows Removed by Filter/Index Recheck를 loops 반영해 계산하고,
>   **대상 relation의 접근 노드만** 센다(부모·자식 합산 금지). *(→ 3차에서 이 필드가 PG JSON
>   전용임을 반영해 DB별 normalized metric·단일 노드 전제로 보강. 아래 3차 #3 참조.)*
> - (#5 covering 구조화) `access: index_only`(=covering) 룰 추가. PG의 index-only 실효성은
>   visibility map에 의존하므로 대상 테이블에 `VACUUM ANALYZE` 전처리를 계약. WHERE 리터럴은
>   `params` 필드 또는 구체 리터럴로 고정. *(→ 3차에서 `params` 필드 제거, 구체 리터럴로 단일화.)*
> - (#6 nondeterministic) `nondeterministic: bool` → `{strategy, columns}` 객체.
> - (covering-index 정렬) `ordered: false`로 확정(순서는 관심사 아님, multiset 비교).
>
> **Codex 3차 리뷰(#1 teardown 책임·객체명 충돌) 반영**:
> - 이전 스펙은 작성자가 `teardown` SQL로 DROP을 적으면서, 동시에 하니스가 "고유 객체명
>   기준 trusted DROP"을 한다고 해 **작성자가 쓴 이름과 하니스 고유명을 잇는 메타데이터가
>   없었다**(둘 중 하나가 죽은 규정). **하니스 전담**으로 통일한다: ddl 케이스는 `object:
>   {type, name}`(논리명)만 선언하고, statement·exercise·post_query 안에서 `{{object_name}}`
>   placeholder로 실제 객체를 참조한다. **작성자 `teardown*` 필드는 제거.** 하니스가
>   `sqlbridge_{case_id}_{object_name}` 규칙으로 고유명을 만들어 주입하고, finally에서 그
>   이름으로 trusted DROP(IF EXISTS)을 돌린다. 이름이 결정적이라 재실행 전 stale object도 같은
>   DROP으로 정리된다. object는 케이스당 **하나**(다중 객체는 비범위).
> - (#2 params) `:p` 같은 바인딩 문법은 MySQL CLI에서 그대로 실행되지 않고, 자료형·바인딩
>   순서·PG placeholder 변환도 정의된 적이 없어 "컨테이너에서 실제 실행해 문법 확인" 태스크와
>   충돌했다. 하니스가 아직 없어 바인딩 규약을 세울 자리도 아니다. **`params` 필드를 제거**하고
>   (전 kind 금지), WHERE 등 리터럴은 **SQL에 구체 값으로 직접 적는다**. covering-index 씨드도
>   `WHERE name = 'Product 0042'`로 고정. 바인딩이 실제로 필요해지면 하니스 스펙에서 다룬다.
> - (#3 examined-row 정규화) 계산식이 PG JSON 필드명(`Rows Removed by Filter/Index Recheck`)을
>   그대로 써서 MySQL TREE엔 없는 값을 전제했다. adapter가 두 형식을 **normalized metric**으로
>   환산하는 규칙을 DB별로 표에 명시했다. MySQL TREE는 필터 제거분을 노출하지 않으므로
>   `max_examined_rows`는 **PG metric 기준**으로 두고, MySQL은 `forbid_full_scan`+`access`로
>   대량 스캔을 막는다(`type=ALL`은 TREE 라벨 기준으로 판정). 또 "접근 노드 하나" 전제를
>   명시하고, self-join처럼 대상 relation이 여러 노드로 나오는 케이스는 **씨드에서 배제**하며
>   adapter가 0개/2개+를 만나면 판정 불가로 실패(fail-closed)한다.
> - (#4 collation) 이전 계약은 "문자열 collation을 타입 정규화"한다고 했으나, MySQL
>   `utf8mb4_0900_ai_ci`와 PG `C.UTF-8`의 collation 차이는 결과가 나온 뒤가 아니라 **선택·
>   그룹핑 단계에서 이미 다른 행을 만든다**(정규화로 복구 불가). collation을 타입 정규화 목록에서
>   빼고, 코퍼스가 **collation 차이에 의존하는 케이스를 쓰지 않는다**(비범위)로 계약을 바꿨다.
>   문자열 비교의 정의는 "바이트 동일". 씨드는 이 제약을 지킨다.

## 배경

도커 실험 환경(이슈 1)이 완성돼 MySQL·PostgreSQL을 실제로 돌려 쿼리를 비교할 수 있게 됐다.
다음으로 필요한 건 **시험 문제지 = 검증 코퍼스**다. 변환 엔진과 검증 하니스는 아직 없지만,
그것들이 무엇을 기준으로 판정할지를 먼저 못 박아야 한다. 이 코퍼스가 그 기준이다.

코퍼스는 세 가지로 구성된다. (1) "결과가 같다"의 정의를 적은 **비교 계약**, (2) 케이스를
어떻게 적는지 정한 **케이스 스키마**, (3) Real MySQL 개념을 골고루 덮는 **씨드 케이스**.
여기에 케이스가 스키마를 지키는지 자동으로 확인하는 **정적 검증 도구**를 더한다.

이 코퍼스는 손으로 수백 개를 채우려는 게 아니다. 이후 케이스 생성기가 따라 만들 **golden
템플릿** 10~15개와, 그 케이스들이 지켜야 할 **규칙 문서**를 세우는 게 목적이다.

## 실행 모델 (오라클 순환 회피)

케이스의 SQL 필드는 역할이 둘로 갈린다.

- **피검증 SQL** — `mysql`(dql), `statement`(dml/ddl). **MySQL로 적고 하니스가
  MySQL→PostgreSQL로 변환**해 양쪽에서 실행·비교한다. 변환이 검증 대상이다.
- **제어 SQL** — `setup`, `exercise`, `post_query`. 준비·관찰을 위한 검증 오라클이다.
  **변환하지 않는다.** 기본은 양 DB 공통 SQL로 적고, 문법이 갈리면 `setup_mysql`/
  `setup_postgres`처럼 DB별 쌍으로 적는다. (독립 PG fixture와 같은 층위 — 검증 오라클을 손으로
  관리하는 것이지 "쿼리 변환 정답"을 적는 게 아니다.) **정리(DROP)는 작성자가 적지 않고
  하니스가 전담**한다(ddl의 DDL 4단계 참조).

제어 SQL을 피검증 변환기로 돌리면 statement 변환 실패와 setup/post_query 변환 실패를 구분할
수 없고, 같은 변환 버그가 준비·관찰에 반복돼 잘못된 통과가 생긴다. 그래서 제어 SQL은 변환
경로 밖에 둔다.

## 범위

- `corpus/concepts.yaml` — 개념 화이트리스트의 **기계 판독 단일 원본**(concept id·설명).
- `corpus/comparison-contract.md` — "결과 동일"의 정의(정렬/multiset/타입정규화/오차/tz/
  비결정성/NULL 정렬)와 성능 룰 판정 규칙을 문서로 명세.
- `corpus/case-schema.md` — YAML 케이스 스키마 정의(dql/dml/ddl, 피검증/제어 SQL 구분,
  필수·선택·금지 필드, perf 룰, 허용값).
- `corpus/schema.md` — 이커머스 ERD 설명 + 개념 매핑 표 + **케이스별 개념 커버리지 표**.
- `corpus/cases/syntax/*.yaml`, `corpus/cases/performance/*.yaml` — 씨드 케이스 10~15개.
- `tools/validate_corpus.py` — 코퍼스 정적 검증 CLI. PyYAML 사용.
- `pyproject.toml` — PyYAML을 dev 의존성으로 추가.
- `tests/test_validate_corpus.py` — 검증 도구의 pytest.

## 비범위 (다음 스펙들)

- **변환 엔진** (SQLGlot MySQL→PG transpile). 위 "실행 모델"의 변환 주체가 이것이다.
- **결과비교 검증 하니스** — 비교 계약·격리·제어 SQL 실행을 실제로 구현하는 실행기. 이번엔
  계약·스키마를 **문서로 명세**만 한다.
- **Performance Analyzer** — plan adapter(TREE/JSON)로 perf 룰을 판정하는 도구. 이번엔
  perf 룰의 **입력 규격과 판정 규칙을 명세**만 하고 구현하지 않는다.
- **케이스 생성기**, **1000+ 케이스**.

## 설계

이 코퍼스는 **Validator / Performance Analyzer의 입력 규격**이다. 코드가 아니라 데이터·규칙
자산이라, 검증도 "규격 준수"와 "SQL 문법 유효성"으로 한다.

### concepts.yaml (개념 단일 원본)

concept 화이트리스트를 기계 판독 YAML로 둔다. schema.md 표 파싱은 편집에 깨지기 쉬우므로
**concept 원본은 이 파일**이고 schema.md는 설명한다. 검증기와 커버리지 판정은 이 파일만 읽는다.

### comparison-contract.md

- **정렬**: `ordered: true`면 list 비교, 단순 ORDER BY 존재가 아니라 **동률까지 가르는 total
  order**를 요구(정렬 키가 유일하도록 케이스 작성). `false`면 multiset 비교.
- **중복**: multiset(중복 보존).
- **타입 정규화**: DECIMAL 스케일, bool 0/1↔false/true, datetime UTC 동일 순간. (문자열
  collation은 정규화 대상이 **아니다** — 아래 "collation 비범위" 참조.)
- **숫자 허용 오차**: 절대·상대 오차(예: 1e-9) 안이면 동일.
- **NULL 정렬**: MySQL·PG의 기본 NULL 위치가 반대일 수 있다. 변환이 `NULLS FIRST/LAST`를
  보존하는지 계약에 명시. nullable 정렬 키의 ordered 케이스는 순서 의미 보존까지 판정.
- **비결정적 함수**: `nondeterministic` 객체(아래)로 처리 전략을 케이스가 선언.
- **collation 비범위**: 현재 환경은 MySQL `utf8mb4_0900_ai_ci`(case/accent 둔감)와 PG
  `C.UTF-8`(바이트 엄격)로 collation 의미가 다르다. 이 차이는 `WHERE`·`JOIN`·`LIKE`·
  `GROUP BY`·`DISTINCT`·`UNIQUE`·`ORDER BY` 단계에서 **선택·그룹핑되는 행 자체를 바꾸므로,
  결과가 나온 뒤 정규화로 복구할 수 없다.** 그래서 코퍼스는 이 차이에 의존하는 케이스를 **쓰지
  않는다**(계약상 비범위):
  - 문자열 등가/조인/유일성 비교는 **바이트 동일**한 값만 매칭하도록 케이스를 작성한다
    (예: `'café'`와 `'CAFE'`를 같게 취급해야 통과하는 케이스 금지).
  - `GROUP BY`/`DISTINCT`의 문자열 키는 case/accent만 다른 중복이 없도록 씨드 데이터를 짠다.
  - `ORDER BY`의 문자열 정렬 순서 자체를 판정하는 ordered 케이스는 두지 않는다(순서가 관심사면
    숫자·날짜 등 collation 무관 키로 total order를 만든다).
  - 따라서 **문자열 비교의 정의는 "바이트 동일"**이며, collation 정규화는 하지 않는다.

  (collation-민감 변환을 다루려면 PG를 ai_ci 유사 ICU collation으로 맞추거나 케이스가 collation을
  선언하는 방식이 필요하다. 둘 다 이번 스펙 비범위이며, 필요해지면 별도 스펙에서 다룬다.)

### case-schema.md

**공통 필드**: `id`, `kind`, `concepts`, `note`.

| kind | 피검증 SQL | 제어 SQL | 기타 필수 | 선택 | 금지 |
|------|-----------|----------|----------|------|------|
| `dql` | `mysql` | — | — | `ordered`, `nondeterministic`, `perf` | `setup*`, `statement`, `exercise`, `post_query*`, `object`, `params`, `teardown*` |
| `dml` | `statement` | `setup*`, `post_query*` | `isolation` | `nondeterministic`, `exercise` | `mysql`, `perf`, `object`, `teardown*` |
| `ddl` | `statement` | `post_query*` | `isolation`, `object` | `setup*`, `exercise` | `mysql`, `perf`, `teardown*` |

- `*`가 붙은 제어 SQL 필드는 **공통형(`setup`) 또는 DB별 쌍(`setup_mysql`+`setup_postgres`)**
  둘 중 하나로 적는다. 둘 다 적거나, 쌍 중 한쪽만 적으면 검증 실패.
- 피검증 SQL(`mysql`/`statement`)만 변환. 제어 SQL은 변환하지 않는다.
- `object`: **ddl 필수, 그 외 금지**. `{ type, name }`(논리명). 정리는 하니스 전담이라
  작성자 `teardown*`은 없다(전 kind에서 금지). 아래 "DDL 4단계" 참조.
- `isolation`: 허용값 `fresh`뿐. dml/ddl 필수.
- `ordered`: bool. total order 요구. 기본은 ORDER BY 유무 추론, 모호하면 명시.
- WHERE 등 리터럴은 **SQL 안에 구체 값으로 직접 적는다**(별도 `params` 바인딩 없음 — `params`는
  전 kind 금지). 컨테이너에서 그대로 실행돼 문법 유효성을 확인할 수 있어야 한다. 문자열 리터럴은
  collation 비범위 제약을 따른다(비교 계약 참조).
- `nondeterministic`: 객체 `{ strategy: fixed_clock|fixed_seed|exclude_columns, columns: [..] }`.
  `exclude_columns` 전략이면 `columns` 필수. 비교 계약이 각 전략의 처리를 정의한다.

**DDL 4단계 생명주기** (auto-increment 등): `statement`(변환 대상 CREATE) → `exercise`
(CREATE 후 실행할 INSERT 등, 제어 SQL) → `post_query`(상태 확인, 제어 SQL) → **정리(하니스
전담)**.

- **객체명은 논리명 + placeholder**: 케이스는 `object: { type, name }`으로 논리명만 선언하고,
  statement·exercise·post_query 안에서는 실제 객체를 `{{object_name}}` placeholder로 참조한다.
  작성자는 정리 SQL을 적지 않는다(`teardown*` 없음).
- **하니스가 고유명 생성·주입**: 실행 직전 하니스가 `sqlbridge_{case_id}_{object_name}` 규칙으로
  고유명을 만들어 세 SQL의 `{{object_name}}`을 치환한다. 고유명은 **결정적**(같은 케이스는 항상
  같은 이름)이라 재실행 시 이전 실행이 남긴 stale object를 같은 이름으로 지목해 정리할 수 있다.
- **finally trusted DROP**: 하니스가 `object.type` 기준 `DROP <type> IF EXISTS <고유명>`을
  finally에서 실행(변환기 비의존). 변환·실행 실패로 객체가 남지 않게 하고, 재실행 전 stale
  object도 이 DROP으로 정리한다.
- object는 케이스당 **하나**. TEMPORARY TABLE을 쓰면 4단계를 **같은 커넥션**에서 수행한다.

**perf 룰 (dql 전용, relation 단위)**

```yaml
perf:
  relations:
    - name: products            # 검사 대상 relation (필수)
      access: index_only        # index_only | index | any  (index_only = covering)
      mysql_index_name: idx_products_name_price   # MySQL에만 강제(선택)
      forbid_full_scan: true    # 대상 relation에 full scan 금지
      max_examined_rows: 100    # 실측 examined 행 상한
```

| 필드 | 의미 |
|------|------|
| `name` | 검사할 relation. 다중 조인에서 어느 scan node를 볼지 결정(없으면 판정 불가). |
| `access` | `index_only`=covering(heap fetch 없음), `index`=인덱스 접근(Index/Index Only/Bitmap 허용), `any`=제약 없음. |
| `mysql_index_name` | 기대 인덱스명. **MySQL에만 강제**(PG는 인덱스명이 다르므로 접근 형태만 확인). |
| `forbid_full_scan` | 대상 relation에 full scan(MySQL `type=ALL`/PG `Seq Scan`) 금지. |
| `max_examined_rows` | **실측 examined 행 상한**. 아래 계산식으로 산출(PG normalized metric 기준 — MySQL TREE는 필터 제거분 미노출, 아래 adapter 표 참조). |

**plan adapter (DB별 입력)**: MySQL `EXPLAIN ANALYZE FORMAT=TREE`, PostgreSQL
`EXPLAIN (ANALYZE, FORMAT JSON)`. (MySQL 8.4는 EXPLAIN ANALYZE의 JSON 미지원 — 실측 확인.)
두 형식은 필드 이름·구조가 다르므로 adapter가 각각을 **동일한 normalized metric**으로 환산한다.
아래 계산식은 이 normalized metric으로만 쓰고, 원천 필드는 DB별로 다음처럼 뽑는다.

| normalized metric | PostgreSQL JSON에서 | MySQL TREE에서 |
|-------------------|--------------------|----------------|
| `access_kind` | 노드 `Node Type`(`Seq Scan`/`Index Scan`/`Index Only Scan`/`Bitmap …`) | 노드 라벨(`Table scan`/`Index lookup`/`Covering index …`)과 접근 방식 |
| `returned_rows` | `Actual Rows` | `actual rows`(TREE의 `(actual … rows=…)`) |
| `filtered_out` | `Rows Removed by Filter` + `Rows Removed by Index Recheck`(없으면 0) | TREE에는 이 필드가 **없다**. `<노드의 스캔 행 추정 없음>` → **필터 제거분은 0으로 두고**, 대신 `forbid_full_scan`·`access` 판정으로 접근 형태를 강제(아래 주 참조). |
| `loops` | `Actual Loops` | `(… loops=…)` |

MySQL TREE는 필터로 걸러진 행 수를 노드별로 노출하지 않는다. 그래서 examined 상한을 **양 DB
동일 방식으로 실측 비교하는 건 PG 기준**으로만 하고, MySQL 쪽은 `forbid_full_scan`(=`type=ALL`
아님)과 `access`(기대 접근 형태) 판정으로 "대량 스캔 아님"을 보장한다. `max_examined_rows`는
**PG normalized metric에 적용**한다(케이스 note에 이 비대칭을 적는다).

**examined 행 계산 (PG normalized metric)**: 대상 relation의 **접근 노드 하나**에 대해
`(returned_rows + filtered_out) × loops`.
반환 행만 세면 필터로 걸러진 행을 놓친다(예: 2만 행 스캔해 1행 반환한 Seq Scan을 1로 오판).
부모·자식 노드를 합산하면 같은 행을 중복 집계하므로 **대상 relation의 접근 노드만** 계산한다.

**단일 접근 노드 전제**: 위 계산은 대상 relation이 플랜에 **접근 노드 하나로만** 나타난다고
전제한다. self-join처럼 같은 relation이 여러 노드로 등장하면 어느 노드를 셀지 모호하므로, 이번
씨드는 그런 케이스를 **두지 않는다**. adapter가 대상 relation의 접근 노드를 0개 또는 2개 이상
발견하면 **판정 불가로 실패**(fail-closed)한다. (alias로 노드를 지목하는 방식은 비범위 —
self-join perf 케이스가 필요해지면 별도 스펙에서 다룬다.)

**index_only 전처리**: PostgreSQL의 Index Only Scan은 visibility map에 의존하고 그 비트는
VACUUM이 설정한다. 현재 fixture는 ANALYZE만 한다. `access: index_only` 케이스를 실측하려면
하니스가 대상 테이블에 **`VACUUM ANALYZE`를 전처리**해야 한다(비교 계약/성능 하니스가 이
전처리를 수행한다고 명세).

### schema.md

이커머스 7테이블 ERD 설명 + 개념 매핑 표 + **케이스 → 개념 매핑 표**. concept 원본은
`concepts.yaml`, 이 표는 사람이 보는 설명이다.

### 씨드 케이스 (10~15개)

**syntax/** — `limit-pagination`, `ifnull-coalesce`, `backtick-identifier`,
`date-function`(nondeterministic), `enum-type`, `bool-tinyint`, `unsigned-type`,
`upsert-on-duplicate`(dml), `auto-increment`(**ddl 4단계**: `object`로 전용 TEMPORARY TABLE
논리명 선언, `{{object_name}}`으로 CREATE→INSERT→id 확인, 정리는 하니스 finally DROP —
공유 fixture 불변).

**performance/** — `covering-index`(`SELECT name, price FROM products WHERE name = 'Product
0042'`, `access: index_only`, `ordered: false`; 리터럴은 ASCII라 collation 무관), `multi-join`,
`keyset-vs-offset`,
`non-sargable-like`, `groupby-aggregate`.

각 SQL은 도커 컨테이너에서 실제 실행해 문법 유효성 확인(dml은 격리 실행 후, ddl은 하니스
finally DROP 후 공유 fixture 불변 확인). 변환 정답(`expect`)은 적지 않는다.

### validate_corpus.py

`corpus/cases/**/*.yaml`을 읽어 검증하는 CLI(표준 라이브러리 + PyYAML).

- **최상위 구조**: 최상위가 정확히 `cases:` 리스트.
- **스키마 준수**: kind별 필수 필드 존재, **unknown field는 실패**(오타 차단), kind별 금지
  필드 사용 시 실패. 제어 SQL은 공통형 또는 완전한 DB별 쌍(한쪽만 있으면 실패).
- **필드 값**: SQL 문자열 비어있지 않음, `concepts` 비어있지 않은 문자열 리스트, `id` 형식
  (kebab-case), `kind`∈{dql,dml,ddl}, `isolation`∈{fresh}, `ordered` bool, `perf.relations`
  각 항목의 `name` 필수·`access`∈{index_only,index,any}·나머지 타입, `nondeterministic`이
  `exclude_columns`면 `columns` 필수. **ddl은 `object.{type,name}` 필수**(그 외 kind는 금지);
  `object`가 있으면 statement의 `{{object_name}}` placeholder 참조를 함께 확인.
- **전역 ID 유일성**, **concepts 화이트리스트**(concepts.yaml id인지).
- **개념 커버리지**: concepts.yaml의 모든 개념이 최소 1개 케이스로 덮임. **기본 실패**(CI),
  `--allow-incomplete-coverage`일 때만 경고.

## 태스크

- [ ] `pyproject.toml` — PyYAML dev 의존성 추가 → 검증: 깨끗한 venv에서 `pip install -e
      ".[dev]"` 후 `import yaml` 성공
- [ ] `corpus/concepts.yaml` — 개념 단일 원본 → 검증: ERD 선언 개념(tinyint-bool/unsigned
      포함) 전부 id로 존재
- [ ] `corpus/comparison-contract.md` — 정렬(total order)/multiset/타입정규화/오차/tz/
      NULL(NULLS FIRST/LAST 보존)/비결정성 전략/**collation 비범위**(문자열 비교=바이트 동일) +
      perf 판정(examined 행 계산·relation scope·index_only+VACUUM·TREE/JSON adapter) → 검증:
      각 축과 perf 규칙이 모두 정의됨, 씨드 케이스가 collation 비범위 제약을 지킴
- [ ] `corpus/case-schema.md` — 피검증/제어 SQL 구분, dql/dml/ddl 필드(제어 SQL 공통/쌍,
      DDL 4단계), perf 룰(relation 단위), 허용값 → 검증: 모든 필드·허용값·판정 규칙 명시
- [ ] `corpus/schema.md` — ERD + 개념 매핑 + 케이스 커버리지 표 → 검증: 7테이블·concepts.yaml
      모든 개념이 케이스로 매핑
- [ ] `corpus/cases/syntax/*.yaml` — 씨드(dql·dml·ddl 4단계) → 검증: 각 SQL을 MySQL
      컨테이너에서 실행해 문법 유효, dml 격리 실행 후·ddl은 `{{object_name}}` 치환→하니스
      finally DROP 후 공유 fixture 불변
- [ ] `corpus/cases/performance/*.yaml` — perf 케이스 → 검증: 각 SQL 문법 유효, perf 룰이
      스키마 준수, covering-index는 `SELECT name, price`로 양쪽 index-only 성립 확인(PG는
      VACUUM ANALYZE 후)
- [ ] `tools/validate_corpus.py` — 정적 검증 CLI → 검증: 정상 통과 + 의도적 오류(중복 ID·
      누락 필드·오타 필드·금지 필드·불완전 DB별 쌍·미커버 개념·미등록 concept) 검출, 커버리지
      기본 실패/`--allow-incomplete-coverage` 경고
- [ ] `tests/test_validate_corpus.py` — 검증 도구 pytest → 검증: 통과·각 오류 검출이 테스트로 확인

## 결정 근거 (trade-off)

- **피검증 SQL만 변환, 제어 SQL은 오라클**: 제어 SQL까지 변환하면 statement 변환 실패와
  준비·관찰 변환 실패가 뒤섞여 잘못된 통과가 생긴다. 제어 SQL을 변환 밖에 두고, 갈리면 DB별
  쌍으로 손 관리한다(독립 PG fixture와 같은 층위).
- **DDL 정리는 하니스 전담 (작성자 teardown 제거)**: MySQL DDL은 implicit commit이라 rollback
  격리가 안 된다. 작성자가 teardown SQL을 적게 하면 "작성자가 쓴 객체명"과 "하니스 고유명"을
  잇는 메타데이터가 없어 둘 중 하나가 죽은 규정이 된다. 그래서 케이스는 `object` 논리명만
  선언하고 `{{object_name}}`으로 참조하며, 하니스가 `sqlbridge_{case_id}_{object_name}`
  결정적 고유명을 주입하고 그 이름으로 finally trusted DROP(변환기 비의존)을 돌린다. 이름이
  결정적이라 재실행 전 stale object도 같은 DROP으로 정리된다.
- **DB별 plan adapter (TREE/JSON)**: MySQL 8.4는 EXPLAIN ANALYZE JSON을 지원하지 않는다(실측
  확인). 억지로 양쪽 JSON을 맞추면 MySQL은 추정치만 남아 "실측 통일"이 깨진다. 그래서 MySQL
  TREE·PG JSON을 각각 파싱해 공통 지표로 정규화한다.
- **examined 행 = 반환+필터제거, relation scope, PG 기준**: rows×loops(반환 행)만으로는 필터로
  걸러진 스캔을 놓친다. 필터 제거분을 더하고 대상 relation 접근 노드만 세 중복·누락을 막는다.
  단 MySQL TREE는 필터 제거분을 노출하지 않아 양 DB 동일 실측이 불가능하므로, 상한은 PG
  normalized metric 기준으로 두고 MySQL은 `forbid_full_scan`+`access`로 대량 스캔을 막는다.
  self-join 등 대상 relation이 다중 노드로 나오는 케이스는 씨드에서 배제(다중 노드 fail-closed).
- **index_only는 VACUUM 전처리 필요**: PG Index Only Scan 실효성은 visibility map(=VACUUM)에
  달려 있어, ANALYZE만 한 현재 fixture로는 heap fetch가 생길 수 있다. covering 케이스는 실측
  전 VACUUM ANALYZE를 계약한다.
- **collation은 정규화 불가라 케이스 데이터로 회피**: MySQL ai_ci와 PG C의 collation 차이는
  비교 이전(선택·그룹핑) 단계에서 다른 행을 만들어 사후 정규화가 불가능하다. PG를 ai_ci 유사
  ICU로 맞추는 길은 실험 환경 재구축·재현성 검증 부담이 커 MVP에 과하다. 그래서 이번엔 코퍼스가
  collation 차이에 의존하지 않도록 케이스를 짜는 것으로 회피하고(계약상 비범위), 문자열 비교는
  바이트 동일로 정의한다.
- **concept 원본은 concepts.yaml / unknown·커버리지 미달은 실패**: 생성기가 따라 만들 golden
  template이라 오타·누락이 대량 복제된다. 기계 판독 원본 + fail-closed 검증으로 초기에 잡는다.
- **validate_corpus는 표준 라이브러리 + PyYAML만**: 정적 검증에 무거운 스택은 과하다.
- **스펙 파일 위치**: 기존 스펙 2개가 `docs/superpowers/specs/`에 있어 관행을 따른다.
