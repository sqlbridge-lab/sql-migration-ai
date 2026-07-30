# SQLBridge AI — 실험 기반 & 검증 코퍼스 설계

## Status: Request changes 반영 (재리뷰 대기)

> [!NOTE]
> **Codex 1차 리뷰([required] 3 + [recommended] 3)** 반영:
> (1) 비교 계약("동일함" 정의: 정렬/multiset/타입정규화/오차/tz/비결정성)을 명세,
> (2) 케이스 스키마를 dql/dml/ddl로 확장(setup/statement/post_query/isolation)해 상태 변경
> 케이스 표현, (3) PG 스키마·시드를 변환 엔진 산출물이 아닌 **독립 fixture**로 재정의(순환
> 의존 회피), (4) `assert_plan` 자유문자열 → 구조화 perf 룰 + JSON EXPLAIN 해석, (5) 이미지
> 버전 핀·charset·tz·sql_mode·시드 결정성·ANALYZE 등 재현성 명세, (6) `validate_corpus.py`
> 정적 검증 + TINYINT(1)/UNSIGNED 씨드 케이스 추가. 구현 대상(하니스·엔진)은 여전히 다음 스펙.

## 배경

이 프로젝트는 MySQL SQL을 PostgreSQL SQL로 변환하고, 변환 결과의 **정확성**과 **성능**을
검증한다. 변환 엔진을 만들기 전에, 변환을 검증할 수 있는 두 가지 기반이 먼저 필요하다.

1. **실험 무대**: MySQL과 PostgreSQL을 실제로 돌려 쿼리를 실행·비교할 수 있는 환경.
2. **시험 문제지(코퍼스)**: Real MySQL 개념을 골고루 담은 테스트 쿼리 모음.

정확성 검증의 본질은 "손으로 적은 변환 정답과 비교"가 아니라 **양쪽 DB 실행을 비교**하는
것이다 — 조회(dql)는 결과를, 상태 변경(dml/ddl)은 사후 상태를 비교한다. 성능 검증은
**실행계획·실측치 비교**다. 따라서 케이스에는 사람이 쿼리 변환 정답을 미리 적지 않는다. 정답은
실행이 만든다. 단 "결과가 같다"는 판정이 성립하려면 (a) **동일함의 계약**과 (b) 변환 엔진과
독립적인 **신뢰 가능한 PostgreSQL 기준 데이터**가 필요하다 — 이 둘을 이번 스펙이 함께 세운다.

이 스펙은 그 첫 단계 — **실험 환경 + 코퍼스 구조 + 대표 씨드 케이스**까지다.

## 범위

- **Docker 실험 환경**: `docker compose up` 하면 MySQL 8과 PostgreSQL 16이 뜨고, MySQL은
  기동 시 스키마·시드가 자동 주입되는 상태.
- **이커머스 ERD (MySQL측)**: 7개 테이블로 Real MySQL 개념(인덱스·조인·페이징·집계·MySQL
  고유 타입)이 다 올라갈 수 있는 스키마 + 시드 데이터.
- **PostgreSQL측 독립 fixture (스키마·시드)**: 변환 엔진의 산출물이 **아니라**, Validator가
  신뢰의 기준으로 삼는 손으로 관리하는 PG 스키마·시드. MySQL 시드와 **논리적으로 동일한
  데이터셋**을 담는다. (근거: 아래 "독립 PG fixture" 참조)
- **YAML 코퍼스 구조**: 케이스 스키마 정의(DQL/DML/DDL 구분 포함) + `syntax/`·`performance/`
  폴더 구성.
- **대표 씨드 케이스 10~15개**: 개념을 골고루 커버하는 손으로 작성한 golden 템플릿.
- **`corpus/schema.md`**: ERD 설명 + 개념 매핑 표 + 케이스별 개념 커버리지 매핑.
- **비교 계약(comparison contract) 명세**: "결과가 동일하다"의 정의. (아래 "비교 계약" 참조.
  실제 비교 구현은 다음 스펙.)
- **코퍼스 정적 검증**: YAML 케이스의 스키마/필수필드/ID 유일성/concepts 값 검증 도구.

## 비범위 (다음 스펙들)

- **변환 엔진** (SQLGlot 기반 MySQL→PG transpile).
- **결과비교 검증 하니스** — 비교 계약을 실제로 구현해 두 DB에 쿼리를 돌리고 결과·성능을
  비교·판정하는 실행기. 이번 스펙은 그 **계약을 문서로 명세**만 하고 구현은 하지 않는다.
- **Performance Analyzer** — EXPLAIN(JSON)을 결정적으로 해석해 성능 룰을 판정하는 도구.
- **케이스 생성기** — 스키마 기반 조합/퍼징으로 케이스를 대량 생성하는 도구.
- **1000+ 케이스** — 손이 아니라 위 하니스+생성기로 도달한다. 이번 씨드는 생성기가 따라 만들
  참조 템플릿 역할이다.
- **RAG / LLM 스택**.

## 설계

### 파일 구조

```text
sql-migration-ai/
├── docker/
│   ├── compose.yaml              # mysql:8, postgres:16
│   ├── mysql/init/               # 기동 시 자동 실행 (docker-entrypoint-initdb.d)
│   │   ├── 01-schema.sql         # 이커머스 DDL (MySQL)
│   │   └── 02-seed.sql           # 시드 데이터 (MySQL)
│   └── postgres/init/            # 독립 fixture (변환 엔진 산출물 아님, 손으로 관리)
│       ├── 01-schema.sql         # 동일 논리 스키마 (PostgreSQL DDL)
│       └── 02-seed.sql           # MySQL과 논리적으로 동일한 시드
├── corpus/
│   ├── schema.md                 # ERD 설명 + 개념 매핑 표 + 케이스 커버리지
│   ├── comparison-contract.md    # "결과 동일" 정의 + 성능 룰 판정 규칙
│   ├── case-schema.md            # YAML 케이스 스키마 정의 (DQL/DML/DDL)
│   └── cases/
│       ├── syntax/               # 문법 변환 케이스 *.yaml
│       └── performance/          # 성능 개념 케이스 *.yaml
├── tools/
│   └── validate_corpus.py        # 코퍼스 정적 검증 (스키마·ID 유일성·커버리지)
└── docs/superpowers/specs/2026-07-31-experiment-foundation-design.md
```

### 이커머스 ERD (개념 무대)

ERD 자체는 목적이 아니다. Real MySQL 개념이 다 올라갈 수 있는 "무대"면 충분하다.

| 테이블 | 주요 컬럼 | 담는 개념 |
|--------|-----------|-----------|
| `users` | id(AUTO_INCREMENT PK), email(UNIQUE), created_at(DATETIME) | 세컨더리 인덱스(email), MySQL 타입 |
| `categories` | id, name, parent_id(self FK) | 자기참조, 계층 |
| `products` | id, category_id(FK), name, price, stock(UNSIGNED) | 복합/커버링 인덱스(name+price), UNSIGNED |
| `orders` | id, user_id(FK), status(ENUM), total, ordered_at(DATETIME) | ENUM 타입, 대량 행(페이징), 인덱스 |
| `order_items` | id, order_id(FK), product_id(FK), qty, unit_price | 다중 조인의 중심 |
| `payments` | id, order_id(FK), method(ENUM), paid(TINYINT(1)), paid_at | TINYINT(1)=bool, ENUM |
| `reviews` | id, product_id(FK), user_id(FK), rating, content(TEXT), created_at | non-sargable LIKE, TEXT |

**개념 커버리지**:
- 세컨더리/커버링 인덱스: `products(name, price)`, `users(email)`
- 복합 인덱스: `orders(user_id, ordered_at)`
- 다중 조인: `orders → order_items → products → categories`
- 페이징: `orders`를 대량 시드해 offset vs keyset 비교 가능
- non-sargable: `reviews.content LIKE '%키워드%'`, 함수 적용 조건
- 집계 + GROUP BY: 카테고리별 매출, 사용자별 주문 수
- MySQL 고유 타입: `AUTO_INCREMENT`, `ENUM`, `TINYINT(1)`, `UNSIGNED`, `DATETIME`

시드는 개념 실험이 가능한 최소 규모로 한다. 참조 규모(구현 시 조정 가능): `users` ~1천,
`products` ~1천, `orders` ~5만, `order_items` ~15만(주문당 평균 3행), `reviews` ~2만.
`orders`/`order_items`를 이 규모로 넣어야 페이징(offset vs keyset)·조인 성능 차이가 드러난다.
나머지(categories/payments)는 소규모.

### 재현성 (환경·데이터 고정)

성능·정확성 비교가 재현되려면 행 개수만으로는 부족하다. 실행계획은 통계·분포·설정에 좌우된다.
다음을 고정한다.

- **이미지 버전 핀**: `mysql:8.4.x`, `postgres:16.x` — 부동 태그가 아니라 패치까지 고정.
- **문자셋/정렬**: MySQL `utf8mb4` + 명시적 collation, PostgreSQL `UTF8` + 명시적 locale.
- **timezone**: 두 컨테이너 모두 `UTC` 고정.
- **MySQL `sql_mode`**: 명시적으로 고정(예: `STRICT_TRANS_TABLES` 포함).
- **시드 결정성**: 고정 난수 seed와 고정 기준 timestamp로 데이터를 생성해, 재기동해도 동일한
  분포가 나오게 한다. 분포(카디널리티·치우침)도 시드 스크립트 주석에 명시한다.
- **통계 갱신**: 시드 후 MySQL `ANALYZE TABLE` / PostgreSQL `ANALYZE`를 실행해 옵티마이저
  통계를 확정한 상태에서 실험한다.
- **초기화 방법**: 깨끗한 재현은 `docker compose down -v && up`로 볼륨까지 초기화.

### 독립 PostgreSQL fixture (순환 의존 회피)

PostgreSQL측 스키마·시드는 **변환 엔진의 산출물이 아니라 손으로 관리하는 독립 fixture**다.
검증 기준(PG 데이터)을 피검증 대상(변환 엔진)이 만들면, DDL/데이터 변환 버그와 쿼리 변환
버그를 구분할 수 없고 모든 코퍼스 결과가 함께 오염된다. 따라서 Validator가 신뢰하는 PG
fixture를 별도로 둔다.

- `docker/postgres/init/01-schema.sql`, `02-seed.sql`은 MySQL fixture와 **논리적으로 동일한**
  스키마·데이터를 담는다(같은 행, 같은 값). 물리 타입은 각 DB 관용에 맞춘다
  (예: `AUTO_INCREMENT`↔`GENERATED ... AS IDENTITY`, `TINYINT(1)`↔`boolean`, `ENUM`↔
  `text + CHECK` 또는 enum 타입).
- "손으로 변환 정답을 적지 않는다"는 결정과 충돌하지 않는다. 그건 **쿼리** 정답을 안 적는다는
  뜻이고, 여기서 손으로 두는 건 **검증 기준이 되는 데이터셋**이다.
- 두 fixture가 논리적으로 같은지는 코퍼스 정적 검증과 별개로, 이후 하니스가 행 수·핵심 집계로
  교차 확인한다(다음 스펙).

### 비교 계약 (correctness의 "동일함" 정의)

"두 DB 결과가 동일하면 pass"는 그대로 두되, **동일함의 정의**를 명세한다. SQL 결과는
`ORDER BY` 없이는 순서가 보장되지 않고, 두 DB는 타입·정렬·NULL 처리 표현이 다르므로 단순
행 배열 비교로는 정상 변환이 실패하거나 실제 오류를 놓친다. 계약은 `comparison-contract.md`에
문서화하고, 이번 스펙은 계약을 **명세만** 한다(비교 구현은 다음 스펙).

- **정렬**: 쿼리에 `ORDER BY`가 있으면 **순서 있는(list) 비교**, 없으면 **순서 무시 multiset
  비교**. 케이스는 `ordered: true|false`로 이를 명시한다(기본은 쿼리에 ORDER BY 유무로 추론,
  모호하면 명시).
- **중복**: 행 중복을 보존하는 multiset 비교(집합 비교로 뭉개지 않음).
- **타입 정규화**: `DECIMAL`은 스케일 맞춰 비교, boolean(`TINYINT(1)`↔`bool`)은 0/1↔
  false/true 정규화, `DATETIME`/`timestamp`는 UTC 기준 동일 순간으로 정규화, 문자열은 계약된
  collation 기준.
- **숫자 허용 오차**: 부동소수/집계는 절대·상대 허용 오차(예: 1e-9) 안이면 동일.
- **NULL 정렬**: NULL 위치 차이는 정렬 계약(위 `ordered`)과 NULL 정규화 규칙으로 흡수.
- **비결정적 함수**: `NOW()`, `RAND()` 등은 그대로 비교하면 항상 불일치한다. 케이스에
  `nondeterministic: true`를 두고, 하니스가 고정 시각/seed를 주입하거나 해당 컬럼을 비교에서
  제외하는 방식을 계약에 정의한다.

### YAML 케이스 스키마

각 케이스는 **입력만** 담는다. 실행 결과(시간·행·실행계획·통과여부)는 케이스에 적지 않고,
이후 하니스가 리포트로 따로 생성한다. 케이스는 검증 방식이 다른 세 종류로 나뉜다.

- **`kind: dql`** — 조회. 결과를 비교 계약으로 판정. (대부분의 syntax·performance 케이스)
- **`kind: dml`** — `INSERT`/`UPDATE`/`DELETE`/upsert. 결과 행이 아니라 **변경된 DB 상태**를
  검증한다. 공유 시드를 오염시키므로 격리해서 실행한다.
- **`kind: ddl`** — 스키마 변경/메타데이터. `INSERT`/DDL/메타조회 중 무엇을 검증하는지에 따라
  판정이 달라지므로 `post_query`로 확인 대상을 명시한다.

```yaml
# 파일 하나 = 한 개념, 그 안에 관련 케이스 여러 개
cases:
  # --- DQL 예시 ---
  - id: limit-basic
    kind: dql
    concepts: [pagination, limit-offset]
    mysql: |
      SELECT id, name FROM products ORDER BY id LIMIT 20, 10;
    ordered: true            # ORDER BY 있음 → 순서 있는 비교
    note: "MySQL LIMIT offset,count"

  # --- DML(upsert) 예시: 상태 검증 ---
  - id: upsert-on-duplicate
    kind: dml
    concepts: [upsert, on-duplicate-key]
    isolation: fresh         # 격리된 환경/트랜잭션에서 실행, 실행 후 롤백/리셋
    setup: |
      INSERT INTO products (id, category_id, name, price, stock)
      VALUES (1, 1, 'A', 100, 5);
    statement: |
      INSERT INTO products (id, category_id, name, price, stock)
      VALUES (1, 1, 'A', 999, 5)
      ON DUPLICATE KEY UPDATE price = VALUES(price);
    post_query: |            # 변경된 상태를 이 조회로 비교
      SELECT id, price FROM products WHERE id = 1;
    note: "upsert는 결과 행이 아니라 사후 상태를 검증"

  # --- 성능 룰(구조화) 예시 ---
  - id: covering-index-01
    kind: dql
    concepts: [covering-index]
    mysql: |
      SELECT id, name FROM products WHERE name = 'A';
    perf:
      must_use_index: true
      index_name: idx_products_name_price
      forbid_full_scan: true
      max_rows_examined: 100
    note: "커버링 인덱스로 index-only scan 기대"
```

- **정확성 판정(dql)**: 비교 계약(위 섹션)에 따라 판정. 케이스에 변환 정답을 적지 않는다.
- **상태 검증(dml/ddl)**: `setup`으로 준비, `statement`로 변경, `post_query`로 사후 상태를
  두 DB에서 각각 조회해 비교. `isolation: fresh`면 공유 시드를 건드리지 않도록 격리·리셋한다.
- **성능 판정(perf)**: 자유 문자열 `assert_plan` 대신 **구조화 조건**을 쓴다. `must_use_index`,
  `index_name`, `forbid_full_scan`, `max_rows_examined` 등. Performance Analyzer가 각 DB의
  **JSON EXPLAIN**을 결정적으로 해석해 판정한다(엔진별 연산자 이름 차이를 흡수). 측정은
  워밍업·반복 후 대푯값을 쓴다(구체 규칙은 성능 하니스 스펙에서).
- **손으로 적는 변환 정답(`expect`)은 없다.**

### 씨드 케이스 (10~15개, 두 축 골고루)

각 씨드 케이스는 `concepts` 태그가 ERD의 개념 하나 이상에 매핑돼야 하며, 아래 목록은 ERD가
선언한 모든 개념(특히 `TINYINT(1)`, `UNSIGNED` 포함)을 최소 1개 케이스로 덮는다. 이 매핑은
`schema.md`의 커버리지 표로 추적하고 `validate_corpus.py`가 미커버 개념을 경고한다.

**syntax/** (문법 변환 → 결과 정확성, kind:dql 기본)
- `limit-pagination.yaml` — `LIMIT offset,count`
- `ifnull-coalesce.yaml` — `IFNULL` → `COALESCE`
- `backtick-identifier.yaml` — `` `col` `` → `"col"`
- `date-function.yaml` — `NOW()`(nondeterministic), `DATE_FORMAT`
- `enum-type.yaml` — `ENUM` 컬럼 필터
- `bool-tinyint.yaml` — `payments.paid` (`TINYINT(1)`↔bool) 필터·비교
- `unsigned-type.yaml` — `products.stock` (`UNSIGNED`) 경계값 조회
- `upsert-on-duplicate.yaml` — `INSERT ... ON DUPLICATE KEY UPDATE` (**kind:dml**, 상태 검증)
- `auto-increment.yaml` — `AUTO_INCREMENT` INSERT 후 id/메타데이터 확인 (**kind:dml/ddl**)

**performance/** (성능 개념 → 구조화 perf 룰)
- `covering-index.yaml` — 커버링 인덱스 활용 조회
- `multi-join.yaml` — 4-테이블 조인
- `keyset-vs-offset.yaml` — 대량 페이징 두 방식
- `non-sargable-like.yaml` — 선행 와일드카드 `LIKE '%x'`
- `groupby-aggregate.yaml` — 카테고리별 집계

## 태스크

- [ ] `docker/compose.yaml` — mysql 8.4.x·postgres 16.x **패치까지 핀**, UTC·charset/collation·
      sql_mode·locale 고정, 헬스체크, 볼륨 → 검증: `docker compose up` 후 두 컨테이너 healthy,
      두 DB에서 timezone/charset/collation 설정값 조회로 확인
- [ ] `docker/mysql/init/01-schema.sql` — 7개 테이블 DDL + 인덱스 → 검증: MySQL 기동 시
      에러 없이 스키마 생성, `SHOW TABLES` 7개
- [ ] `docker/mysql/init/02-seed.sql` — 결정적 시드(고정 seed·기준 timestamp, orders ~5만/
      order_items ~15만) + `ANALYZE TABLE` → 검증: row count·핵심 집계가 재기동 후에도 동일
- [ ] `docker/postgres/init/01-schema.sql`,`02-seed.sql` — **독립 PG fixture** (MySQL과 논리적
      동일 데이터, 물리 타입은 PG 관용) + `ANALYZE` → 검증: PG 기동 성공, 주요 테이블 row
      count·핵심 집계가 MySQL fixture와 일치
- [ ] `corpus/comparison-contract.md` — 정렬/multiset/타입정규화/오차/tz/비결정성 계약 명세 →
      검증: 위 6개 축이 모두 규칙으로 정의됨
- [ ] `corpus/case-schema.md` — YAML 케이스 스키마(dql/dml/ddl, 필수·선택 필드, perf 룰) 정의 →
      검증: 모든 필드와 판정 규칙 명시
- [ ] `corpus/schema.md` — ERD 설명 + 개념 매핑 표 + **케이스별 개념 커버리지 표** → 검증:
      7개 테이블·모든 선언 개념(TINYINT(1)/UNSIGNED 포함)이 케이스로 매핑됨
- [ ] `corpus/cases/syntax/*.yaml`, `performance/*.yaml` — 씨드 케이스(dql/dml 포함) → 검증:
      각 mysql/statement/post_query를 해당 컨테이너에서 수동 실행해 문법 유효성 확인, dml
      케이스는 격리 실행 후 리셋 확인
- [ ] `tools/validate_corpus.py` — 코퍼스 정적 검증(스키마 준수·필수필드·전역 ID 유일성·
      concepts 값 화이트리스트·개념 커버리지) → 검증: 정상 코퍼스 통과, 의도적 오류(중복 ID·
      누락 필드·미커버 개념) 검출

## 결정 근거 (trade-off)

- **환경·코퍼스 먼저, 검증 하니스는 다음**: 검증(결과비교)은 변환 엔진이 있어야 성립한다.
  무대와 문제지를 먼저 세워야 이후 단계가 실제로 돌아간다. 단 비교 계약은 이번에 **명세**해
  둔다(구현만 다음). 계약이 없으면 코퍼스 케이스를 뭘 기준으로 짜야 할지 정할 수 없기 때문.
- **PG fixture는 독립 (변환 엔진 산출물 아님)**: 검증 기준을 피검증 대상이 만들면 순환이라,
  엔진 버그와 쿼리 변환 버그가 뒤섞이고 코퍼스 전체가 오염된다. 그래서 PG 스키마·시드를 손으로
  관리하는 독립 fixture로 둔다. 이는 "쿼리 변환 정답을 손으로 안 적는다"와 층위가 다르다
  (전자는 검증 기준 데이터, 후자는 쿼리 정답).
- **케이스에 변환 정답(`expect`) 안 적음**: 손으로 수백 개 정답을 적는 건 불가능·무의미하고,
  진짜 정답은 두 DB 실행 결과 비교가 만든다. 케이스는 입력만, 결과는 리포트로 분리한다.
- **1000+는 손이 아니라 생성기로**: 손으로 대량 작성하면 케이스 자체가 미검증 상태로 쌓이고
  (엔진·하니스가 없어 유효성 확인 불가), 시간도 오래 걸린다. 히든/엣지 케이스는 사람이 아니라
  스키마 기반 조합·퍼징 생성기가 더 잘 찾는다. 이번 씨드는 그 생성기가 따라 만들 golden
  템플릿이다.
- **개념별 파일에 여러 케이스**: 파일 수가 적고 관련 케이스를 한눈에 비교하기 쉽다.
- **이커머스 도메인, ERD는 얇게**: 친숙한 도메인이라 개념 매핑이 직관적이다. 완벽한 도메인
  모델링이 아니라 "개념이 다 올라가는 무대"가 목표다.
