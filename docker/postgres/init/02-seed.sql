-- SQLBridge AI — PostgreSQL 독립 fixture 시드
--
-- 스펙 "독립 PostgreSQL fixture": MySQL 시드와 **논리적으로 동일한 데이터셋**
-- (같은 행, 같은 값)을 담는다. 그래서 결정적 산술을 MySQL 02-seed.sql과
-- 1:1로 재현한다. 값이 하나라도 어긋나면 검증 기준이 무너지므로 주의.
--
-- MySQL SQL → PostgreSQL SQL 대응 (동일 값을 얻기 위한 핵심):
--   재귀 CTE / seq       → generate_series(1, N)
--   TIMESTAMPADD(MIN,n,T)→ T + n * INTERVAL '1 minute'
--   LPAD(n, 4, '0')      → lpad(n::text, 4, '0')
--   ROUND(x, 2)          → round(x::numeric, 2)
--   (n % 100) / 100      → (n % 100) / 100.0   ← MySQL '/'는 실수나눗셈, PG는 정수나눗셈
--                          이라 반드시 100.0으로 나눠 소수부를 맞춘다.
--   문자열 CONCAT(...)   → '...' || ... (또는 format/concat)
--
-- 기준 시각 앵커: TIMESTAMP '2025-01-01 00:00:00' (MySQL과 동일)

-- categories: 루트 5 + 하위 15 = 20 -------------------------------------------
INSERT INTO categories (id, name, parent_id)
SELECT n, 'root-' || n, NULL
FROM generate_series(1, 5) AS g(n);

INSERT INTO categories (id, name, parent_id)
SELECT 5 + n, 'sub-' || n, 1 + (n % 5)
FROM generate_series(1, 15) AS g(n);

-- users: 1,000 ----------------------------------------------------------------
INSERT INTO users (id, email, name, created_at)
SELECT
    n,
    'user' || n || '@example.com',
    'User ' || n,
    TIMESTAMP '2025-01-01 00:00:00' + n * INTERVAL '1 minute'
FROM generate_series(1, 1000) AS g(n);

-- products: 1,000 -------------------------------------------------------------
INSERT INTO products (id, category_id, name, price, stock)
SELECT
    n,
    6 + (n % 15),
    'Product ' || lpad(n::text, 4, '0'),
    round((10 + (n % 500) + (n % 100) / 100.0)::numeric, 2),
    n % 1000
FROM generate_series(1, 1000) AS g(n);

-- orders: 50,000 --------------------------------------------------------------
INSERT INTO orders (id, user_id, status, total, ordered_at)
SELECT
    n,
    1 + (n % 1000),
    (CASE
        WHEN n % 10 < 2 THEN 'pending'
        WHEN n % 10 < 5 THEN 'paid'
        WHEN n % 10 < 7 THEN 'shipped'
        WHEN n % 10 < 9 THEN 'delivered'
        ELSE 'cancelled'
    END)::order_status,
    round((100 + (n % 9000) + (n % 100) / 100.0)::numeric, 2),
    TIMESTAMP '2025-01-01 00:00:00' + n * INTERVAL '1 minute'
FROM generate_series(1, 50000) AS g(n);

-- order_items: 주문당 1~5행 (MySQL과 동일한 결정적 분산) ---------------------------
--   MySQL은 id를 AUTO_INCREMENT로 부여했고 여기서도 삽입 순서가 동일해야
--   id가 일치한다. (order_id, slot) 순서를 MySQL의 JOIN 결과 순서와 맞추기 위해
--   order_id, slot 순으로 정렬해 row_number로 id를 부여한다.
INSERT INTO order_items (id, order_id, product_id, qty, unit_price)
SELECT
    row_number() OVER (ORDER BY o.n, sl.s)      AS id,
    o.n                                         AS order_id,
    1 + ((o.n * 7 + sl.s * 13) % 1000)          AS product_id,
    1 + ((o.n + sl.s) % 5)                       AS qty,
    round((10 + ((o.n + sl.s) % 500))::numeric, 2) AS unit_price
FROM generate_series(1, 50000) AS o(n)
JOIN generate_series(1, 5)     AS sl(s) ON sl.s <= 1 + (o.n % 5);

-- payments: 주문당 1건 (50,000) ------------------------------------------------
--   MySQL은 payments.id를 AUTO_INCREMENT(=orders 순서)로 부여 → id = order_id.
INSERT INTO payments (id, order_id, method, paid, paid_at)
SELECT
    o.id,
    o.id,
    (CASE o.id % 3
        WHEN 0 THEN 'card'
        WHEN 1 THEN 'bank_transfer'
        ELSE 'paypal'
    END)::payment_method,
    (o.status IN ('paid', 'shipped', 'delivered'))                AS paid,
    CASE
        WHEN o.status IN ('paid', 'shipped', 'delivered')
        THEN TIMESTAMP '2025-01-01 00:00:00' + o.id * INTERVAL '1 minute'
        ELSE NULL
    END                                                            AS paid_at
FROM orders o;

-- reviews: 20,000 -------------------------------------------------------------
INSERT INTO reviews (id, product_id, user_id, rating, content, created_at)
SELECT
    n,
    1 + (n % 1000),
    1 + ((n * 3) % 1000),
    1 + (n % 5),
    CASE
        WHEN n % 7 = 0 THEN 'This product is excellent, review #' || n
        ELSE 'An ordinary review number ' || n
    END,
    TIMESTAMP '2025-01-01 00:00:00' + n * INTERVAL '1 minute'
FROM generate_series(1, 20000) AS g(n);

-- 통계 확정 -------------------------------------------------------------------
ANALYZE users, categories, products, orders, order_items, payments, reviews;
