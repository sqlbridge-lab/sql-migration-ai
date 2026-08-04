# 케이스 스키마 (case schema)

코퍼스 케이스를 적는 YAML 형식을 정한다. `validate_corpus.py`가 이 규격대로 정적 검증한다.

## 파일 구조

- 각 YAML 파일의 최상위는 정확히 `cases:` 리스트 하나다.
- 한 파일에 여러 케이스를 담을 수 있다.

```yaml
cases:
  - id: ...
    kind: ...
    concepts: [...]
    # kind별 필드 (아래)
```

## 피검증 SQL vs 제어 SQL

- **피검증 SQL** — `mysql`(dql), `statement`(dml/ddl). MySQL로 적고, **하니스가
  MySQL→PostgreSQL로 변환**해 양쪽에서 실행·비교한다. **변환이 검증 대상**이다.
- **제어 SQL** — `setup`, `exercise`, `post_query`. 준비·관찰용 검증 오라클이다.
  **변환하지 않는다.** 기본은 양 DB 공통 SQL로 적고, 문법이 갈리면 `setup_mysql`/
  `setup_postgres`처럼 **DB별 쌍**으로 적는다.
- **정리(DROP)는 작성자가 적지 않는다.** ddl의 객체 정리는 하니스가 전담한다(아래 "DDL 4단계").

## 공통 필드 (전 kind)

| 필드 | 필수 | 의미 |
|------|------|------|
| `id` | ✔ | 전역 유일. kebab-case. |
| `kind` | ✔ | `dql` \| `dml` \| `ddl`. |
| `concepts` | ✔ | 비어있지 않은 문자열 리스트. `concepts.yaml`의 id만 허용. |
| `note` | | 사람이 읽는 메모(선택). |

## kind별 필드

| kind | 피검증 SQL | 제어 SQL | 기타 필수 | 선택 | 금지 |
|------|-----------|----------|----------|------|------|
| `dql` | `mysql` | — | — | `ordered`, `nondeterministic`, `perf` | `setup*`, `statement`, `exercise`, `post_query*`, `object`, `params`, `teardown*` |
| `dml` | `statement` | `setup*`, `post_query*` | `isolation` | `nondeterministic`, `exercise` | `mysql`, `perf`, `object`, `params`, `teardown*` |
| `ddl` | `statement` | `post_query*` | `isolation`, `object` | `setup*`, `exercise` | `mysql`, `perf`, `params`, `teardown*` |

- `*`가 붙은 제어 SQL 필드는 **공통형(`setup`) 또는 완전한 DB별 쌍(`setup_mysql`+
  `setup_postgres`)** 둘 중 하나로 적는다. 둘 다 적거나 쌍 중 한쪽만 적으면 검증 실패.
- **unknown field는 실패**(오타 차단). 위 표에 없는 필드가 있으면 검증 실패.

### 필드 설명

- `isolation`: 허용값 `fresh`뿐. dml/ddl 필수. 매 케이스를 깨끗한 상태에서 실행함을 뜻한다.
- `ordered`: bool. total order를 요구(비교 계약 1.1). 기본은 ORDER BY 유무로 추론하되, 모호하면
  명시한다.
- WHERE 등 리터럴은 **SQL 안에 구체 값으로 직접 적는다**(별도 `params` 바인딩 없음 — `params`는
  전 kind 금지). 컨테이너에서 그대로 실행돼 문법 유효성을 확인할 수 있어야 한다. 문자열 리터럴은
  collation 비범위 제약을 따른다(비교 계약 1.7).
- `nondeterministic`: 객체
  `{ strategy: fixed_clock | fixed_seed | exclude_columns, columns: [..] }`.
  `exclude_columns` 전략이면 `columns` 필수. 각 전략의 처리는 비교 계약 1.8이 정의한다.
- `object`: **ddl 필수, 그 외 금지.** `{ type, name }`(논리명). 정리는 하니스 전담이라 작성자
  `teardown*`은 없다. 아래 "DDL 4단계" 참조.

## perf 룰 (dql 전용, relation 단위)

```yaml
perf:
  relations:
    - name: products            # 검사 대상 relation (필수)
      access: index_only        # index_only | index | any  (index_only = covering)
      mysql_index_name: idx_products_name_price   # MySQL에만 강제(선택)
      forbid_full_scan: true    # 대상 relation에 full scan 금지(선택)
      max_examined_rows: 100    # 실측 examined 행 상한(선택, PG metric 기준)
```

- `name`은 필수. 나머지는 선택. 판정 규칙은 비교 계약 2장이 정의한다.

## DDL 4단계 생명주기

auto-increment처럼 CREATE→INSERT→관찰→정리가 필요한 케이스를 위한 규격이다.

1. `statement` — 변환 대상 CREATE (피검증 SQL).
2. `exercise` — CREATE 후 실행할 INSERT 등 (제어 SQL).
3. `post_query` — 상태 확인 SELECT 등 (제어 SQL).
4. **정리** — 하니스 전담(작성자가 적지 않음).

- **객체명은 논리명 + placeholder.** 케이스는 `object: { type, name }`으로 논리명만 선언하고,
  statement·exercise·post_query 안에서는 실제 객체를 `{{object_name}}` placeholder로 참조한다.
- **하니스가 고유명 생성·주입.** 실행 직전 하니스가 `sqlbridge_{case_id}_{object_name}` 규칙으로
  고유명을 만들어 `{{object_name}}`을 치환한다. 이름은 결정적이라 재실행 시 이전 실행이 남긴
  stale object도 같은 이름으로 정리할 수 있다.
- **finally trusted DROP.** 하니스가 `object.type` 기준 `DROP <type> IF EXISTS <고유명>`을
  finally에서 실행(변환기 비의존). 변환·실행 실패로 객체가 남지 않게 한다.
- object는 케이스당 **하나**. TEMPORARY TABLE을 쓰면 4단계를 **같은 커넥션**에서 수행한다.

예시:

```yaml
cases:
  - id: auto-increment
    kind: ddl
    concepts: [auto-increment]
    isolation: fresh
    object: { type: table, name: tmp_ai }
    statement: |
      CREATE TEMPORARY TABLE {{object_name}} (
        id INT AUTO_INCREMENT PRIMARY KEY,
        label VARCHAR(20) NOT NULL
      )
    exercise: |
      INSERT INTO {{object_name}} (label) VALUES ('a'), ('b')
    post_query: |
      SELECT id, label FROM {{object_name}} ORDER BY id
```
