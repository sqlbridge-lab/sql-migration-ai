-- SQLBridge AI — PostgreSQL 독립 fixture 스키마 (7 테이블)
--
-- 스펙 "독립 PostgreSQL fixture": 이 스키마는 변환 엔진의 산출물이 아니라,
-- Validator가 신뢰의 기준으로 삼는 손으로 관리하는 fixture다. MySQL 스키마와
-- 논리적으로 동일하되, 물리 타입은 PostgreSQL 관용을 따른다.
--
-- MySQL ↔ PostgreSQL 타입 매핑:
--   AUTO_INCREMENT      → 시드에서 id를 명시 삽입하므로 identity 없이 bigint 사용
--                          (결정성 우선: 양쪽 DB가 완전히 동일한 id를 갖게 한다)
--   BIGINT UNSIGNED     → bigint  (음수 없음은 시드가 보장; PG엔 unsigned 없음)
--   INT UNSIGNED        → integer + CHECK (>= 0)   ← UNSIGNED 경계 의미 보존
--   ENUM(...)           → CREATE TYPE ... AS ENUM  ← 네이티브 enum 타입
--   TINYINT(1)          → boolean                  ← paid 0/1 ↔ false/true
--   DATETIME            → timestamp (without tz)   ← UTC 앵커 기준 동일 순간
--   DECIMAL(p, s)       → numeric(p, s)
--
-- POSTGRES_DB=shop 로 생성된 DB에 연결된 상태에서 실행된다.

-- ENUM 타입 (MySQL ENUM 대응) --------------------------------------------------
CREATE TYPE order_status AS ENUM ('pending', 'paid', 'shipped', 'delivered', 'cancelled');
CREATE TYPE payment_method AS ENUM ('card', 'bank_transfer', 'paypal');

-- users -----------------------------------------------------------------------
CREATE TABLE users (
    id         bigint       NOT NULL,
    email      varchar(255) NOT NULL,
    name       varchar(100) NOT NULL,
    created_at timestamp    NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_users_email UNIQUE (email)
);

-- categories: 자기참조 FK --------------------------------------------------------
CREATE TABLE categories (
    id        bigint       NOT NULL,
    name      varchar(100) NOT NULL,
    parent_id bigint       NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_categories_parent
        FOREIGN KEY (parent_id) REFERENCES categories (id)
);
CREATE INDEX idx_categories_parent ON categories (parent_id);

-- products: 복합/커버링 인덱스(name, price), stock UNSIGNED → CHECK ----------------
CREATE TABLE products (
    id          bigint         NOT NULL,
    category_id bigint         NOT NULL,
    name        varchar(200)   NOT NULL,
    price       numeric(10, 2) NOT NULL,
    stock       integer        NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    CONSTRAINT ck_products_stock_unsigned CHECK (stock >= 0),
    CONSTRAINT fk_products_category
        FOREIGN KEY (category_id) REFERENCES categories (id)
);
CREATE INDEX idx_products_name_price ON products (name, price);
CREATE INDEX idx_products_category ON products (category_id);

-- orders: enum status, 복합 인덱스(user_id, ordered_at) ---------------------------
CREATE TABLE orders (
    id         bigint         NOT NULL,
    user_id    bigint         NOT NULL,
    status     order_status   NOT NULL,
    total      numeric(12, 2) NOT NULL,
    ordered_at timestamp      NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_orders_user
        FOREIGN KEY (user_id) REFERENCES users (id)
);
CREATE INDEX idx_orders_user_ordered ON orders (user_id, ordered_at);
CREATE INDEX idx_orders_ordered ON orders (ordered_at);

-- order_items: 다중 조인의 중심 ---------------------------------------------------
CREATE TABLE order_items (
    id         bigint         NOT NULL,
    order_id   bigint         NOT NULL,
    product_id bigint         NOT NULL,
    qty        integer        NOT NULL,
    unit_price numeric(10, 2) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_order_items_qty_unsigned CHECK (qty >= 0),
    CONSTRAINT fk_order_items_order
        FOREIGN KEY (order_id) REFERENCES orders (id),
    CONSTRAINT fk_order_items_product
        FOREIGN KEY (product_id) REFERENCES products (id)
);
CREATE INDEX idx_order_items_order ON order_items (order_id);
CREATE INDEX idx_order_items_product ON order_items (product_id);

-- payments: paid boolean(TINYINT(1) 대응), method enum --------------------------
CREATE TABLE payments (
    id       bigint         NOT NULL,
    order_id bigint         NOT NULL,
    method   payment_method NOT NULL,
    paid     boolean        NOT NULL DEFAULT false,
    paid_at  timestamp      NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_payments_order
        FOREIGN KEY (order_id) REFERENCES orders (id)
);
CREATE INDEX idx_payments_order ON payments (order_id);

-- reviews: non-sargable LIKE(content), TEXT -------------------------------------
CREATE TABLE reviews (
    id         bigint    NOT NULL,
    product_id bigint    NOT NULL,
    user_id    bigint    NOT NULL,
    rating     smallint  NOT NULL,
    content    text      NOT NULL,
    created_at timestamp NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_reviews_rating_unsigned CHECK (rating >= 0),
    CONSTRAINT fk_reviews_product
        FOREIGN KEY (product_id) REFERENCES products (id),
    CONSTRAINT fk_reviews_user
        FOREIGN KEY (user_id) REFERENCES users (id)
);
CREATE INDEX idx_reviews_product ON reviews (product_id);
CREATE INDEX idx_reviews_user ON reviews (user_id);
