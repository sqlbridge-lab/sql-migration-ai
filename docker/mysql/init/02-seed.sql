-- SQLBridge AI — MySQL 결정적 시드
--
-- 재현성 (스펙 "재현성" / "시드 결정성"):
--   난수(RAND())를 쓰지 않는다. 모든 값을 행 번호(n) 기반의 결정적 산술로 만든다.
--   따라서 `down -v && up` 재기동 후에도 row count·분포·집계가 완전히 동일하다.
--
-- 기준 시각 (고정 앵커):
--   BASE_TS = '2025-01-01 00:00:00' (UTC). 모든 timestamp는 여기서 오프셋으로 계산.
--   NOW()/CURRENT_TIMESTAMP를 시드에 쓰지 않는다(비결정성 제거).
--
-- 규모 (스펙 참조 규모):
--   users      1,000
--   categories    20  (루트 5 + 하위 15)
--   products   1,000
--   orders    50,000
--   order_items ~150,000  (주문당 1~5행, n 기반 결정적 → 평균 ~3)
--   payments  50,000  (주문당 1건)
--   reviews   20,000
--
-- 분포/치우침 (옵티마이저 통계가 의미를 갖도록 의도한 편향):
--   orders.status : n % 10 으로 분포 (paid/delivered에 치우침, cancelled 소수)
--   payments.paid : status가 paid 이상이면 1, 아니면 0 → bool 필터 실험용
--   products.stock: n 기반 0~999 (경계값 0 포함) → UNSIGNED 경계 실험용
--   reviews.content: 일부 행에만 'excellent' 키워드 → non-sargable LIKE 실험용

USE shop;

-- 재귀 CTE로 최대 5만 행을 만들려면 기본 깊이 제한(1000)을 올려야 한다.
SET SESSION cte_max_recursion_depth = 1000000;

-- ---------------------------------------------------------------------------
-- categories: 루트 5 + 하위 15 = 20
-- ---------------------------------------------------------------------------
-- 루트 카테고리 5개 (parent_id = NULL)
INSERT INTO categories (id, name, parent_id)
WITH RECURSIVE seq (n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM seq WHERE n < 5
)
SELECT n, CONCAT('root-', n), NULL FROM seq;

-- 하위 카테고리 15개 (parent_id = 1..5 순환)
INSERT INTO categories (id, name, parent_id)
WITH RECURSIVE seq (n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM seq WHERE n < 15
)
SELECT 5 + n, CONCAT('sub-', n), 1 + (n % 5) FROM seq;

-- ---------------------------------------------------------------------------
-- users: 1,000
--   email 유니크, created_at = BASE_TS + n 분
-- ---------------------------------------------------------------------------
INSERT INTO users (id, email, name, created_at)
WITH RECURSIVE seq (n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM seq WHERE n < 1000
)
SELECT
    n,
    CONCAT('user', n, '@example.com'),
    CONCAT('User ', n),
    TIMESTAMPADD(MINUTE, n, TIMESTAMP '2025-01-01 00:00:00')
FROM seq;

-- ---------------------------------------------------------------------------
-- products: 1,000
--   category_id = 하위 카테고리(6..20) 순환, price 결정적, stock 0~999(0 포함)
-- ---------------------------------------------------------------------------
INSERT INTO products (id, category_id, name, price, stock)
WITH RECURSIVE seq (n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM seq WHERE n < 1000
)
SELECT
    n,
    6 + (n % 15),                          -- 하위 카테고리 6..20
    CONCAT('Product ', LPAD(n, 4, '0')),   -- 이름 정렬이 인덱스와 맞물리게 zero-pad
    ROUND(10 + (n % 500) + (n % 100) / 100, 2),
    n % 1000                               -- stock: 0..999 (n=1000k 지점 0 포함)
FROM seq;

-- ---------------------------------------------------------------------------
-- orders: 50,000
--   user_id = 1..1000 순환, status는 n % 10 편향, ordered_at = BASE_TS + n 분
--   total은 뒤에서 order_items 합으로 갱신하지 않고 결정적 근사값을 넣는다
--   (검증은 양쪽 DB 동일 값이면 되므로 물리적 정확성보다 결정성이 중요).
-- ---------------------------------------------------------------------------
INSERT INTO orders (id, user_id, status, total, ordered_at)
WITH RECURSIVE seq (n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM seq WHERE n < 50000
)
SELECT
    n,
    1 + (n % 1000),
    -- status 편향: pending 20%, paid 30%, shipped 20%, delivered 20%, cancelled 10%
    CASE
        WHEN n % 10 < 2 THEN 'pending'
        WHEN n % 10 < 5 THEN 'paid'
        WHEN n % 10 < 7 THEN 'shipped'
        WHEN n % 10 < 9 THEN 'delivered'
        ELSE 'cancelled'
    END,
    ROUND(100 + (n % 9000) + (n % 100) / 100, 2),
    TIMESTAMPADD(MINUTE, n, TIMESTAMP '2025-01-01 00:00:00')
FROM seq;

-- ---------------------------------------------------------------------------
-- order_items: 주문당 1~5행 (n 기반 결정적, 평균 ~3 → 약 150,000행)
--   먼저 (order_id, slot) 조합을 만들고, slot이 그 주문의 아이템 수 이하일 때만 남긴다.
--   item 수 = 1 + (order_id % 5)  → 1..5, 평균 3.
-- ---------------------------------------------------------------------------
-- id를 (order_id, slot) 순서의 row_number로 명시 부여한다(PG fixture와 id 일치
-- 보장, AUTO_INCREMENT/조인 순서 의존 제거).
INSERT INTO order_items (id, order_id, product_id, qty, unit_price)
WITH RECURSIVE
    ord (n) AS (
        SELECT 1
        UNION ALL
        SELECT n + 1 FROM ord WHERE n < 50000
    ),
    slot (s) AS (
        SELECT 1
        UNION ALL
        SELECT s + 1 FROM slot WHERE s < 5
    )
SELECT
    ROW_NUMBER() OVER (ORDER BY o.n, sl.s)  AS id,
    o.n                                    AS order_id,
    1 + ((o.n * 7 + sl.s * 13) % 1000)     AS product_id,   -- 결정적 상품 분산
    1 + ((o.n + sl.s) % 5)                 AS qty,           -- 1..5
    ROUND(10 + ((o.n + sl.s) % 500), 2)    AS unit_price
FROM ord o
JOIN slot sl ON sl.s <= 1 + (o.n % 5);     -- 주문당 아이템 수 = 1..5

-- ---------------------------------------------------------------------------
-- payments: 주문당 1건 (50,000)
--   method는 n % 3, paid는 주문 status가 paid 이상이면 1 (bool 실험용)
--   paid_at은 paid=1일 때만 값, 아니면 NULL
-- ---------------------------------------------------------------------------
-- id를 명시적으로 order_id와 같게 부여한다(PG fixture와 id 일치 보장,
-- AUTO_INCREMENT 순서 의존 제거).
INSERT INTO payments (id, order_id, method, paid, paid_at)
SELECT
    o.id,
    o.id,
    CASE o.id % 3
        WHEN 0 THEN 'card'
        WHEN 1 THEN 'bank_transfer'
        ELSE 'paypal'
    END,
    CASE WHEN o.status IN ('paid', 'shipped', 'delivered') THEN 1 ELSE 0 END,
    CASE
        WHEN o.status IN ('paid', 'shipped', 'delivered')
        THEN TIMESTAMPADD(MINUTE, o.id, TIMESTAMP '2025-01-01 00:00:00')
        ELSE NULL
    END
FROM orders o;

-- ---------------------------------------------------------------------------
-- reviews: 20,000
--   product_id = 1..1000 순환, user_id = 1..1000 순환, rating 1..5
--   content는 일부 행에만 'excellent' 포함 → non-sargable LIKE '%excellent%' 실험용
-- ---------------------------------------------------------------------------
INSERT INTO reviews (id, product_id, user_id, rating, content, created_at)
WITH RECURSIVE seq (n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM seq WHERE n < 20000
)
SELECT
    n,
    1 + (n % 1000),
    1 + ((n * 3) % 1000),
    1 + (n % 5),
    CASE
        WHEN n % 7 = 0 THEN CONCAT('This product is excellent, review #', n)
        ELSE CONCAT('An ordinary review number ', n)
    END,
    TIMESTAMPADD(MINUTE, n, TIMESTAMP '2025-01-01 00:00:00')
FROM seq;

-- ---------------------------------------------------------------------------
-- 통계 확정: 옵티마이저가 시드 분포를 반영하도록 ANALYZE
-- ---------------------------------------------------------------------------
ANALYZE TABLE users, categories, products, orders, order_items, payments, reviews;
