-- SQLBridge AI — MySQL 이커머스 스키마 (7 테이블)
--
-- 스펙 "이커머스 ERD" 표를 그대로 따른다. ERD는 목적이 아니라 Real MySQL 개념
-- (인덱스·조인·페이징·집계·MySQL 고유 타입)이 다 올라갈 수 있는 무대다.
--
-- MySQL 고유 타입 커버리지:
--   AUTO_INCREMENT (모든 PK), ENUM (orders.status, payments.method),
--   TINYINT(1)=bool (payments.paid), UNSIGNED (products.stock), DATETIME (created_at 등)
--
-- 문자셋/정렬은 compose.yaml에서 서버 기본값(utf8mb4 / utf8mb4_0900_ai_ci)으로
-- 고정하므로 테이블마다 반복 지정하지 않는다.

-- 안전을 위해 명시적으로 대상 DB 선택 (compose의 MYSQL_DATABASE와 동일).
USE shop;

-- users: 세컨더리 인덱스(email), 기본 MySQL 타입
CREATE TABLE users (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    email      VARCHAR(255)    NOT NULL,
    name       VARCHAR(100)    NOT NULL,
    created_at DATETIME        NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_email (email)
) ENGINE = InnoDB;

-- categories: 자기참조 FK(parent_id), 계층
CREATE TABLE categories (
    id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name      VARCHAR(100)    NOT NULL,
    parent_id BIGINT UNSIGNED NULL,
    PRIMARY KEY (id),
    KEY idx_categories_parent (parent_id),
    CONSTRAINT fk_categories_parent
        FOREIGN KEY (parent_id) REFERENCES categories (id)
) ENGINE = InnoDB;

-- products: 복합/커버링 인덱스(name, price), UNSIGNED(stock)
CREATE TABLE products (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    category_id BIGINT UNSIGNED NOT NULL,
    name        VARCHAR(200)    NOT NULL,
    price       DECIMAL(10, 2)  NOT NULL,
    stock       INT UNSIGNED    NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    -- 커버링 인덱스 실험용: SELECT id, name ... WHERE name = ? 를 index-only로.
    KEY idx_products_name_price (name, price),
    KEY idx_products_category (category_id),
    CONSTRAINT fk_products_category
        FOREIGN KEY (category_id) REFERENCES categories (id)
) ENGINE = InnoDB;

-- orders: ENUM(status), 대량 행(페이징), 복합 인덱스(user_id, ordered_at)
CREATE TABLE orders (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id    BIGINT UNSIGNED NOT NULL,
    status     ENUM('pending', 'paid', 'shipped', 'delivered', 'cancelled') NOT NULL,
    total      DECIMAL(12, 2)  NOT NULL,
    ordered_at DATETIME        NOT NULL,
    PRIMARY KEY (id),
    -- 복합 인덱스: 사용자별 최근 주문 페이징(keyset)·범위 조회에 사용.
    KEY idx_orders_user_ordered (user_id, ordered_at),
    KEY idx_orders_ordered (ordered_at),
    CONSTRAINT fk_orders_user
        FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE = InnoDB;

-- order_items: 다중 조인의 중심
CREATE TABLE order_items (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    order_id   BIGINT UNSIGNED NOT NULL,
    product_id BIGINT UNSIGNED NOT NULL,
    qty        INT UNSIGNED    NOT NULL,
    unit_price DECIMAL(10, 2)  NOT NULL,
    PRIMARY KEY (id),
    KEY idx_order_items_order (order_id),
    KEY idx_order_items_product (product_id),
    CONSTRAINT fk_order_items_order
        FOREIGN KEY (order_id) REFERENCES orders (id),
    CONSTRAINT fk_order_items_product
        FOREIGN KEY (product_id) REFERENCES products (id)
) ENGINE = InnoDB;

-- payments: TINYINT(1)=bool(paid), ENUM(method)
CREATE TABLE payments (
    id       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    order_id BIGINT UNSIGNED NOT NULL,
    method   ENUM('card', 'bank_transfer', 'paypal') NOT NULL,
    paid     TINYINT(1)      NOT NULL DEFAULT 0,
    paid_at  DATETIME        NULL,
    PRIMARY KEY (id),
    KEY idx_payments_order (order_id),
    CONSTRAINT fk_payments_order
        FOREIGN KEY (order_id) REFERENCES orders (id)
) ENGINE = InnoDB;

-- reviews: non-sargable LIKE 실험(content), TEXT
CREATE TABLE reviews (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_id BIGINT UNSIGNED NOT NULL,
    user_id    BIGINT UNSIGNED NOT NULL,
    rating     TINYINT UNSIGNED NOT NULL,
    content    TEXT            NOT NULL,
    created_at DATETIME        NOT NULL,
    PRIMARY KEY (id),
    KEY idx_reviews_product (product_id),
    KEY idx_reviews_user (user_id),
    CONSTRAINT fk_reviews_product
        FOREIGN KEY (product_id) REFERENCES products (id),
    CONSTRAINT fk_reviews_user
        FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE = InnoDB;
