# 이커머스 스키마 & 개념 커버리지

코퍼스 케이스가 돌아가는 무대인 이커머스 7테이블 ERD를 설명하고, 각 개념이 어느 케이스로
덮이는지 정리한다.

- 스키마 원본: `docker/mysql/init/01-schema.sql`, `docker/postgres/init/01-schema.sql`
- 시드 원본: `docker/mysql/init/02-seed.sql`, `docker/postgres/init/02-seed.sql`
- **개념 id 원본은 `corpus/concepts.yaml`** — 아래 표는 사람이 보는 설명이다.

## ERD (7 테이블)

| 테이블 | 핵심 컬럼 | 인덱스 | 다루는 개념 무대 |
|--------|-----------|--------|------------------|
| `users` | id, email(UNIQUE), name, created_at | `uq_users_email` | 세컨더리 유니크 인덱스, DATETIME |
| `categories` | id, name, parent_id(자기참조 FK) | `idx_categories_parent` | 계층, 자기참조 |
| `products` | id, category_id(FK), name, price DECIMAL, stock **UNSIGNED** | `idx_products_name_price(name, price)`, `idx_products_category` | 커버링 인덱스, UNSIGNED |
| `orders` | id, user_id(FK), status **ENUM**, total, ordered_at | `idx_orders_user_ordered(user_id, ordered_at)`, `idx_orders_ordered` | ENUM, 페이징(keyset/offset), 복합 인덱스 |
| `order_items` | id, order_id(FK), product_id(FK), qty UNSIGNED, unit_price | `idx_order_items_order`, `idx_order_items_product` | 다중 조인의 중심, 집계 |
| `payments` | id, order_id(FK), method **ENUM**, paid **TINYINT(1)=bool**, paid_at | `idx_payments_order` | TINYINT(1) 불리언, ENUM, nullable DATETIME |
| `reviews` | id, product_id(FK), user_id(FK), rating, content TEXT | `idx_reviews_product`, `idx_reviews_user` | non-sargable LIKE, TEXT |

관계: `categories`는 자기참조. `products→categories`, `orders→users`,
`order_items→orders/products`, `payments→orders`, `reviews→products/users`.

## 시드 규모 (결정적)

| 테이블 | 행 수 | 비고 |
|--------|-------|------|
| users | 1,000 | email 유니크, created_at = 기준시각 + n분 |
| categories | 20 | 루트 5 + 하위 15 |
| products | 1,000 | name = `Product 0001`..`Product 1000`(zero-pad), stock 0~999(0 포함) |
| orders | 50,000 | status 편향(paid/delivered 다수, cancelled 소수) |
| order_items | ~150,000 | 주문당 1~5행 |
| payments | 50,000 | 주문당 1건, paid는 status가 paid 이상이면 1 |
| reviews | 20,000 | 일부 행(n%7==0)에만 'excellent' 키워드 |

- 난수 없음. 모든 값이 행 번호(n) 기반 결정적 산술 → `down -v && up` 재기동 후에도 완전 동일.
- 시각 앵커: `2025-01-01 00:00:00` UTC. 시드에 `NOW()` 미사용.
- 시드 문자열은 전부 ASCII(`Product ...`, `user...@example.com`, `root-/sub-` 등)라 collation
  비범위 제약(문자열=바이트 동일)에 안전하다.

## 케이스 → 개념 커버리지

`concepts.yaml`의 모든 개념이 최소 1개 케이스로 덮여야 한다(validate_corpus 커버리지 검사).

| 개념 | 덮는 케이스 | 파일 |
|------|-------------|------|
| limit-pagination | `limit-pagination` | syntax/ |
| ifnull-coalesce | `ifnull-coalesce` | syntax/ |
| backtick-identifier | `backtick-identifier` | syntax/ |
| date-function | `date-function` | syntax/ |
| enum-type | `enum-type` | syntax/ |
| tinyint-bool | `bool-tinyint` | syntax/ |
| unsigned-type | `unsigned-type` | syntax/ |
| upsert-on-duplicate | `upsert-on-duplicate` | syntax/ |
| auto-increment | `auto-increment` | syntax/ |
| covering-index | `covering-index` | performance/ |
| multi-join | `multi-join` | performance/ |
| keyset-pagination | `keyset-vs-offset` | performance/ |
| offset-pagination | `keyset-vs-offset` | performance/ |
| non-sargable-like | `non-sargable-like` | performance/ |
| groupby-aggregate | `groupby-aggregate` | performance/ |
