# 비교 계약 (comparison contract)

MySQL 쿼리를 PostgreSQL로 변환해 양쪽에서 실행했을 때 **"결과가 같다"를 무엇으로 정의하는지**,
그리고 성능 케이스의 **perf 룰을 어떻게 판정하는지**를 정한다. 검증 하니스와 Performance
Analyzer는 아직 없다. 이 문서는 그것들이 구현될 때 지킬 **계약(입력 규격)**이다.

상위 설계 근거는 스펙 `docs/superpowers/specs/2026-07-31-corpus-implementation-design.md`에 있다.

---

## 1. 결과 동일성 (result equality)

### 1.1 정렬 (ordered)

- 케이스의 `ordered` 필드로 결정한다.
- `ordered: true` → **list 비교**(순서까지 동일해야 통과). 단순히 ORDER BY가 있다고 되는 게
  아니라, **동률까지 가르는 total order**를 요구한다. 정렬 키가 유일하도록 케이스를 작성한다
  (예: 마지막 정렬 키에 PK를 넣어 tie를 없앤다).
- `ordered: false` → **multiset 비교**(순서 무시). 아래 중복 규칙과 함께 본다.

### 1.2 중복 (multiset)

- 항상 **multiset**으로 본다. 즉 중복 행을 보존해서 개수까지 일치해야 한다.
- `ordered: false`면 "순서 무시 + 중복 개수 일치", `ordered: true`면 "순서까지 일치".

### 1.3 타입 정규화 (type normalization)

비교 전, 아래 축은 표현 차이를 흡수한다.

| 축 | 정규화 방법 |
|----|------------|
| DECIMAL 스케일 | 스케일 차이를 맞춰 같은 수치면 동일(예: `10.00` == `10.0`). |
| 불리언 | MySQL TINYINT(1)의 `0/1`과 PostgreSQL BOOLEAN의 `false/true`를 대응(`0`==false, `1`==true). |
| datetime | 양쪽을 UTC 동일 순간으로 보고 비교(문자열 표현이 달라도 같은 시각이면 동일). |

**문자열 collation은 정규화 대상이 아니다** — 아래 1.7 참조.

### 1.4 숫자 허용 오차 (numeric tolerance)

- 부동소수/집계 결과는 절대·상대 오차 안이면 동일로 본다. 기본 임계값 **1e-9**.
- 예: `AVG(...)`가 `3.3333333331`과 `3.3333333329`로 갈려도 오차 안이면 통과.

### 1.5 시간대 (timezone)

- 두 컨테이너 모두 UTC로 고정돼 있다(`docker/compose.yaml`).
- datetime은 UTC 동일 순간으로 비교한다(1.3의 datetime 축과 같은 규칙).

### 1.6 NULL 정렬 (NULL ordering)

- MySQL과 PostgreSQL의 **기본 NULL 위치가 반대**일 수 있다(MySQL은 NULL을 가장 작게, PG는 가장
  크게 취급 — ASC 기준).
- 변환이 `NULLS FIRST`/`NULLS LAST`를 **보존하는지**가 검증 대상이다.
- nullable 정렬 키를 쓰는 `ordered: true` 케이스는 NULL의 순서 의미까지 보존됐는지 판정한다.
  (NULL 위치가 결과를 가르지 않도록 하려면 정렬 키를 NOT NULL 컬럼으로 잡는다.)

### 1.7 collation (비범위)

현재 환경은 collation 의미가 다르다.

- MySQL: `utf8mb4_0900_ai_ci` — case/accent **둔감**. `'café' = 'CAFE'`가 참.
- PostgreSQL: `C.UTF-8` — **바이트 엄격**. `'café' = 'CAFE'`가 거짓.

이 차이는 결과가 나온 **뒤**에 정규화할 수 있는 게 아니다. `WHERE`·`JOIN`·`LIKE`·`GROUP BY`·
`DISTINCT`·`UNIQUE`·`ORDER BY` 단계에서 **어떤 행이 선택·그룹핑되는지 자체를 바꾸므로**, 비교
시점에는 이미 복구 불가능하다.

그래서 코퍼스는 이 차이에 의존하는 케이스를 **쓰지 않는다**(계약상 비범위).

- 문자열 등가/조인/유일성 비교는 **바이트 동일**한 값만 매칭하도록 케이스를 작성한다
  (`'café'`와 `'CAFE'`를 같게 취급해야 통과하는 케이스 금지).
- `GROUP BY`/`DISTINCT`의 문자열 키는 case/accent만 다른 중복이 없도록 씨드 데이터를 짠다.
- `ORDER BY`의 문자열 정렬 순서 자체를 판정하는 `ordered: true` 케이스는 두지 않는다(순서가
  관심사면 숫자·날짜 등 collation 무관 키로 total order를 만든다).
- **문자열 비교의 정의는 "바이트 동일"**이며, collation 정규화는 하지 않는다.

collation-민감 변환을 다루려면 PG를 ai_ci 유사 ICU collation으로 맞추거나 케이스가 collation을
선언하는 방식이 필요하다. 둘 다 이번 범위 밖이며, 필요해지면 별도 스펙에서 다룬다.

### 1.8 비결정적 함수 (nondeterministic)

`NOW()`, `RAND()` 등 실행마다 값이 바뀌는 함수는 그대로 비교하면 항상 불일치다. 케이스가
`nondeterministic` 객체로 처리 전략을 선언한다.

| strategy | 의미 | 하니스 처리 |
|----------|------|------------|
| `fixed_clock` | 시계 고정 | 양 DB에 동일한 고정 시각을 주입(세션 타임존/기준시각 고정)해 실행. |
| `fixed_seed` | 난수 시드 고정 | 양 DB에 동일 시드를 설정해 난수 수열을 일치시킴. |
| `exclude_columns` | 특정 컬럼 제외 | `columns`에 적은 열을 비교에서 뺀다(값은 다를 수 있어도 나머지 행이 같으면 통과). |

- `exclude_columns` 전략이면 `columns`(비교에서 뺄 컬럼 이름 리스트)가 **필수**다.

---

## 2. perf 룰 판정 (dql 전용)

성능 케이스의 `perf.relations`를 실측 실행 계획으로 판정하는 규칙이다.

### 2.1 plan adapter (DB별 입력)

- MySQL: `EXPLAIN ANALYZE FORMAT=TREE` (MySQL 8.4는 EXPLAIN ANALYZE의 JSON 미지원 — 실측 확인).
- PostgreSQL: `EXPLAIN (ANALYZE, FORMAT JSON)`.

두 형식은 필드 이름·구조가 다르므로 adapter가 각각을 **동일한 normalized metric**으로 환산한다.

| normalized metric | PostgreSQL JSON에서 | MySQL TREE에서 |
|-------------------|--------------------|----------------|
| `access_kind` | 노드 `Node Type`(`Seq Scan`/`Index Scan`/`Index Only Scan`/`Bitmap …`) | 노드 라벨(`Table scan`/`Index lookup`/`Covering index …`)과 접근 방식 |
| `returned_rows` | `Actual Rows` | TREE의 `(actual … rows=…)` |
| `filtered_out` | `Rows Removed by Filter` + `Rows Removed by Index Recheck`(없으면 0) | **TREE에 없음** → 0으로 둔다(아래 2.3 주 참조) |
| `loops` | `Actual Loops` | TREE의 `(… loops=…)` |

### 2.2 판정 필드

| 필드 | 판정 |
|------|------|
| `access: index_only` | covering(heap fetch 없음). PG `Index Only Scan`, MySQL covering index. |
| `access: index` | 인덱스 접근이면 통과(Index/Index Only/Bitmap 허용). |
| `access: any` | 접근 형태 제약 없음. |
| `mysql_index_name` | 기대 인덱스명. **MySQL에만 강제**(PG는 인덱스명이 다르므로 접근 형태만 확인). |
| `forbid_full_scan` | 대상 relation에 full scan 금지(MySQL `type=ALL`류 라벨 / PG `Seq Scan`). |
| `max_examined_rows` | 실측 examined 행 상한. 아래 2.3 계산식(PG metric 기준). |

### 2.3 examined 행 계산

대상 relation의 **접근 노드 하나**에 대해:

```
examined = (returned_rows + filtered_out) × loops
```

- 반환 행만 세면 필터로 걸러진 행을 놓친다(예: 2만 행을 훑어 1행 반환한 Seq Scan을 1로 오판).
- 부모·자식 노드를 합산하면 같은 행을 중복 집계하므로 **대상 relation의 접근 노드만** 계산한다.

**MySQL TREE 비대칭**: MySQL TREE는 필터로 걸러진 행 수(`filtered_out`)를 노드별로 노출하지
않는다. 그래서 `max_examined_rows`는 **PG normalized metric에만 적용**하고, MySQL 쪽은
`forbid_full_scan`(대량 스캔 아님)과 `access`(기대 접근 형태)로 "대량 스캔이 아님"을 보장한다.
케이스 `note`에 이 비대칭을 적는다.

**단일 접근 노드 전제**: 위 계산은 대상 relation이 플랜에 **접근 노드 하나로만** 나타난다고
전제한다. self-join처럼 같은 relation이 여러 노드로 등장하면 어느 노드를 셀지 모호하므로, 씨드는
그런 케이스를 **두지 않는다**. adapter가 대상 relation의 접근 노드를 0개 또는 2개 이상 발견하면
**판정 불가로 실패(fail-closed)**한다. (alias로 노드를 지목하는 방식은 비범위.)

### 2.4 index_only 전처리 (VACUUM)

PostgreSQL의 Index Only Scan 실효성은 visibility map에 의존하고, 그 비트는 VACUUM이 설정한다.
현재 fixture는 ANALYZE만 한다. `access: index_only` 케이스를 실측하려면 하니스가 대상 테이블에
**`VACUUM ANALYZE`를 전처리**해야 한다. (성능 하니스가 이 전처리를 수행한다고 계약한다.)
