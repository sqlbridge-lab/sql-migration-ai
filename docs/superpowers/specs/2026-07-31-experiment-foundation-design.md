# SQLBridge AI — 실험 기반 & 검증 코퍼스 설계

## Status: Draft (사용자 리뷰 대기)

## 배경

이 프로젝트는 MySQL SQL을 PostgreSQL SQL로 변환하고, 변환 결과의 **정확성**과 **성능**을
검증한다. 변환 엔진을 만들기 전에, 변환을 검증할 수 있는 두 가지 기반이 먼저 필요하다.

1. **실험 무대**: MySQL과 PostgreSQL을 실제로 돌려 쿼리를 실행·비교할 수 있는 환경.
2. **시험 문제지(코퍼스)**: Real MySQL 개념을 골고루 담은 테스트 쿼리 모음.

정확성 검증의 본질은 "손으로 적은 변환 정답과 비교"가 아니라 **"MySQL 실행 결과 ==
PostgreSQL 실행 결과"**이고, 성능 검증은 **실행계획·실측치 비교**다. 따라서 케이스에는
사람이 변환 정답을 미리 적지 않는다. 정답은 실행이 만든다.

이 스펙은 그 첫 단계 — **실험 환경 + 코퍼스 구조 + 대표 씨드 케이스**까지다.

## 범위

- **Docker 실험 환경**: `docker compose up` 하면 MySQL 8과 PostgreSQL 16이 뜨고, MySQL은
  기동 시 스키마·시드가 자동 주입되는 상태.
- **이커머스 ERD (MySQL측)**: 7개 테이블로 Real MySQL 개념(인덱스·조인·페이징·집계·MySQL
  고유 타입)이 다 올라갈 수 있는 스키마 + 시드 데이터.
- **YAML 코퍼스 구조**: 케이스 스키마 정의 + `syntax/`·`performance/` 폴더 구성.
- **대표 씨드 케이스 10~15개**: 개념을 골고루 커버하는 손으로 작성한 golden 템플릿.
- **`corpus/schema.md`**: ERD 설명 + 개념 매핑 표.

## 비범위 (다음 스펙들)

- **변환 엔진** (SQLGlot 기반 MySQL→PG transpile).
- **PostgreSQL측 스키마·시드** — 변환 엔진이 생기면 그 산출물로 채운다. 이번엔 `postgres/init/`
  자리만 비워둔다.
- **결과비교 검증 하니스** — 두 DB에 쿼리를 돌려 결과·성능을 비교·판정하는 실행기.
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
│   │   ├── 01-schema.sql         # 이커머스 DDL
│   │   └── 02-seed.sql           # 시드 데이터
│   └── postgres/init/            # 다음 스펙에서 채움 (이번엔 .gitkeep)
├── corpus/
│   ├── schema.md                 # ERD 설명 + 개념 매핑 표
│   └── cases/
│       ├── syntax/               # 문법 변환 케이스 *.yaml
│       └── performance/          # 성능 개념 케이스 *.yaml
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
나머지(categories/payments)는 소규모. 재현 가능하도록 생성 규칙을 시드 스크립트에 남긴다.

### YAML 케이스 스키마

각 케이스는 **입력만** 담는다. 실행 결과(시간·행·실행계획·통과여부)는 케이스에 적지 않고,
이후 하니스가 리포트로 따로 생성한다.

```yaml
# 파일 하나 = 한 개념, 그 안에 관련 케이스 여러 개
cases:
  - id: <고유 id>              # 예: limit-basic
    concepts: [<태그>, ...]    # 예: [pagination, limit-offset]
    mysql: |
      <MySQL 쿼리>
    note: "<이 케이스가 겨냥하는 것>"
    assert_plan: "<선택: 성능 케이스만. 예 'index-only scan'>"
```

- **정확성 판정**: 기본 규칙은 "두 DB 실행 결과가 동일하면 pass". 케이스에 별도로 적지 않는다.
- **성능 판정**: 성능 케이스만 `assert_plan`으로 실행계획에 대한 기대를 선택적으로 명시한다
  (예: PG에서 index-only scan이어야 함, Seq Scan이면 주목).
- **손으로 적는 변환 정답(`expect`)은 없다.**

### 씨드 케이스 (10~15개, 두 축 골고루)

**syntax/** (문법 변환 → 결과 정확성)
- `limit-pagination.yaml` — `LIMIT offset,count`
- `ifnull-coalesce.yaml` — `IFNULL` → `COALESCE`
- `backtick-identifier.yaml` — `` `col` `` → `"col"`
- `auto-increment.yaml` — `AUTO_INCREMENT` 관련 조회
- `on-duplicate-key.yaml` — `INSERT ... ON DUPLICATE KEY UPDATE`
- `date-function.yaml` — `NOW()`, `DATE_FORMAT`
- `enum-type.yaml` — `ENUM` 컬럼 필터

**performance/** (성능 개념 → 실행계획 비교)
- `covering-index.yaml` — 커버링 인덱스 활용 조회
- `multi-join.yaml` — 4-테이블 조인
- `keyset-vs-offset.yaml` — 대량 페이징 두 방식
- `non-sargable-like.yaml` — 선행 와일드카드 `LIKE '%x'`
- `groupby-aggregate.yaml` — 카테고리별 집계

## 태스크

- [ ] `docker/compose.yaml` 작성 (mysql:8, postgres:16, 헬스체크, 볼륨) → 검증:
      `docker compose up` 후 두 컨테이너 healthy
- [ ] `docker/mysql/init/01-schema.sql` — 7개 테이블 DDL + 인덱스 → 검증: MySQL 기동 시
      에러 없이 스키마 생성, `SHOW TABLES` 7개
- [ ] `docker/mysql/init/02-seed.sql` — 시드 데이터(orders ~5만/order_items ~15만) → 검증:
      각 테이블 row count 확인, 조인·페이징 쿼리 수동 실행 성공
- [ ] `docker/postgres/init/.gitkeep` — 자리만 → 검증: 폴더 존재
- [ ] `corpus/schema.md` — ERD 설명 + 개념 매핑 표 → 검증: 7개 테이블·개념 다 문서화
- [ ] `corpus/cases/syntax/*.yaml` — 문법 케이스 7종 → 검증: YAML 파싱 성공, 각 mysql
      쿼리를 MySQL 컨테이너에서 수동 실행해 문법 유효성 확인
- [ ] `corpus/cases/performance/*.yaml` — 성능 케이스 5종 → 검증: 동일하게 수동 실행 확인
- [ ] 케이스 스키마 문서화 (README 또는 schema.md 내 섹션) → 검증: 필드 정의·판정 규칙 명시

## 결정 근거 (trade-off)

- **환경·코퍼스 먼저, 검증 하니스는 다음**: 검증(결과비교)은 변환 엔진과 PG 시드가 있어야
  성립한다. 무대와 문제지를 먼저 세워야 이후 단계가 실제로 돌아간다.
- **케이스에 변환 정답(`expect`) 안 적음**: 손으로 수백 개 정답을 적는 건 불가능·무의미하고,
  진짜 정답은 두 DB 실행 결과 비교가 만든다. 케이스는 입력만, 결과는 리포트로 분리한다.
- **1000+는 손이 아니라 생성기로**: 손으로 대량 작성하면 케이스 자체가 미검증 상태로 쌓이고
  (엔진·하니스가 없어 유효성 확인 불가), 시간도 오래 걸린다. 히든/엣지 케이스는 사람이 아니라
  스키마 기반 조합·퍼징 생성기가 더 잘 찾는다. 이번 씨드는 그 생성기가 따라 만들 golden
  템플릿이다.
- **개념별 파일에 여러 케이스**: 파일 수가 적고 관련 케이스를 한눈에 비교하기 쉽다.
- **이커머스 도메인, ERD는 얇게**: 친숙한 도메인이라 개념 매핑이 직관적이다. 완벽한 도메인
  모델링이 아니라 "개념이 다 올라가는 무대"가 목표다.
