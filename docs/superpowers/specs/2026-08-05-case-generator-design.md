# SQLBridge AI — 케이스 생성기 설계

## Status: Codex 2차 리뷰(Request changes) 반영 (재리뷰 대기)

> [!NOTE]
> 상위 스펙:
> - `2026-07-31-corpus-implementation-design.md` — 코퍼스(케이스 스키마·비교 계약·씨드·정적 검증).
>   생성기가 **따라야 할 계약**이다.
> - `2026-08-05-validation-harness-design.md` — 검증 하니스(생성한 케이스를 실제 두 DB에 돌려
>   채점하는 실행기). 생성물의 **실행 유효성 채점기**다.
>
> 이 스펙은 위 두 스펙이 세운 계약을 **깨지 않으면서**, 템플릿 14개를 값·조건·컬럼 파라미터로
> 벌려 1000개 이상의 케이스를 자동 생성하는 도구의 설계다. 계약의 근거·트레이드오프는 상위
> 스펙에 있으므로 반복하지 않고 링크로 대신한다.

> [!NOTE]
> **Codex 1차 리뷰(Request changes) 반영**:
> - (P1-1 placeholder 토큰) f-string `f"{{object_name}}"`은 `{object_name}`을 낸다 —
>   하니스가 치환하는 토큰(`{{object_name}}`, `runner.py:294`)과 다르다. `OBJECT_NAME_PLACEHOLDER`
>   상수로 정확한 토큰을 삽입하고, 산출 DDL에 그 토큰이 그대로 남는지 전용 테스트를 둔다.
> - (P1-2 perf 채점 불가) 하니스는 perf를 로드·채점하지 않는다(`loader.py`에 perf 없음,
>   하니스 스펙 비범위). error==0은 쿼리 실행성만 본다. **생성 perf 케이스를 이번 범위에서
>   제외**하고 golden perf 5개만 유지한다(Performance Analyzer는 선행 조건 아님).
> - (P1-3 무조건 곱집합) 순수 데카르트 곱은 `orders.name`처럼 존재하지 않는 조합·이중 충돌을
>   만든다. 상호 의존 값을 **구조화 축(묶음 튜플)**으로 묶고, 필요 시 predicate로 무효 조합을
>   버린다.
> - (P1-4 분포 fail-open) "미달 표기만 하면 통과"는 fail-open이다. **개념별 기계 판독 하한을
>   코드 상수로 확정**하고, 미달 시 생성 실패(fail-closed). 분포 표를 파일로 남긴다.
> - (P1-5 원자성) 인메모리 dict 검증은 직렬화·최상위 `cases:` 구조를 안 거친다. **temp dir
>   전개 → `load_cases`로 재로드 → `validate_corpus(...).ok` → 디렉터리 단위 교체** 순서로
>   계약하고, `--out`이 golden 경로를 덮지 못하게 가드한다.
> - (P2-6 0행) 씨드 전역 범위 안이라도 특정 조합은 0행일 수 있다. "SQL 오류 없음"과 "유효
>   비어있지 않은 표본"을 분리 정의하고, 후자는 **실제 DB seed preflight**로 확인한다.
> - (P2-7 단일 원본) golden YAML을 안 읽으면 golden·Python 두 원본이 생긴다. **golden을
>   `base_id`로 로드**해 kind·concepts·기본 SQL을 재사용하고, `build`는 SQL·제어 필드만 낸다.
> - (P2-8 error==0 게이트) 하니스 CLI exit code는 fail에도 1이라 성공 게이트로 못 쓴다.
>   통합 게이트는 **Runner 결과를 직접 받아 `all(status != "error")`**로 검사한다.
> - (전역 유일 범위) id 유일성은 **golden + generated 합집합** 기준으로 검사한다.

> [!NOTE]
> **Codex 2차 리뷰(Request changes) 반영**:
> - (#1 하니스 로드 불가) `load_corpus`는 커버리지 15개 전체를 요구해(`loader.py:76`) syntax
>   9개만 든 generated를 `python -m harness --cases-dir corpus/generated`로 못 돌린다(로딩 실패).
>   하니스 수정은 비범위이므로, 검수는 CLI가 아니라 **통합 테스트가 low-level 로드**
>   (`load_cases`→`validate_corpus(..., allow_incomplete_coverage=True)`→`load_case`)로 한다.
>   "CLI 독립 실행 가능" 서술을 제거했다.
> - (#2 하한 미확정) `MIN_PER_CONCEPT`를 "예시"에서 **확정값**으로 못 박고(합 480), 각 하한을
>   실제 축 크기로 산정했다. "임의 변경 금지, 변경 시 스펙 재리뷰"로 고정(fail-open 완전 차단).
> - (#3 golden 재사용 계약 불일치) `build` 시그니처를 `build(base_case, combination)`으로 바꾸고,
>   계약을 **"golden은 kind·concepts의 단일 원본, 생성 SQL은 코드 원본"**으로 좁혔다.
> - (#4 placeholder 테스트 모순) `"{{object_name}}"`은 `"{object_name}"`을 부분 문자열로 포함해
>   "2겹 포함 & 1겹 미포함"은 동시 불가. **정확 토큰 제거 후 잔여에 홑중괄호 없음** 또는
>   negative-lookaround 정규식으로 검사하도록 고쳤다.
> - (`--out` 가드 보강) golden 경로의 **하위 경로도** 거부(`resolve()`+`is_relative_to` 양방향).

## 배경

코퍼스(이슈 #2)에 golden 템플릿 14개가 있고, 검증 하니스(이슈 #B)가 그 케이스를 실제 MySQL·
PostgreSQL에 돌려 pass/fail/error로 채점할 수 있게 됐다. 하지만 14개로는 변환 엔진·RAG를
개선할 때 **회귀 테스트 표본이 턱없이 부족**하다. 값 하나 바꾼 SQL, 컬럼 조합이 다른 SQL,
경계값(stock=0, NULL, ENUM 각 값)을 훑는 SQL 수백 개가 있어야 변환기가 어디서 깨지는지
넓게 잡을 수 있다.

이 이슈는 손으로 수백 개를 채우는 대신, **템플릿 14개에서 바꿀 부분을 파라미터로 뽑고, 그
파라미터를 조합해 케이스를 찍어내는 생성기**를 만든다. 생성물은 (1) 기존 `validate_corpus.py`
정적 검증을 통과하고, (2) 검증 하니스로 두 DB에서 실제 실행돼 `error == 0`이어야 한다.

**핵심 오해 방지**: 생성 목표는 케이스가 하니스에서 **`pass`하는 것이 아니다.** 현재 하니스의
변환기는 pass-through(입력 SQL 그대로)라, MySQL 전용 문법(백틱·`LIMIT o,c`·`IFNULL` 등)은
PostgreSQL에서 실행 실패해 `fail`이 나는 게 **정상**이다(하니스 스펙 "부수 효과" 참조). 생성기의
성공 조건은 **`error == 0`**(케이스·제어·인프라 오류 없음)이다. `fail`↔`pass`는 나중에 변환
엔진이 채울 지표다.

**단, `error == 0`의 의미는 좁다(P2-8).** 하니스는 dql에서 PG 피검증 SQL이 실패하면 `fail`로
멈추고, dml/ddl은 PG statement 실패 뒤 이후 제어 SQL이 실행되지 않을 수 있다. 그래서
`error == 0`은 "양 DB의 **모든** SQL이 실행 유효"를 뜻하지 않고, **"MySQL 원본 SQL·실제로 도달한
제어 SQL·인프라·cleanup 어디에도 error가 없다"**만 보증한다. 이는 생성기의 성공 조건으로
충분하다(생성기가 책임질 부분 = MySQL 유효성·제어 SQL 유효성·격리이고, PG 변환본 유효성은
변환 엔진의 몫이다).

## 범위

- `tools/generate_cases.py` — 케이스 생성 CLI. 템플릿 축 정의(코드) + 파라미터 조합 → 케이스
  dict 생성 → `validate_corpus`로 자기검증 → YAML 파일로 출력. 표준 라이브러리 + PyYAML만 사용.
- `corpus/generated/` — 생성 케이스 출력 디렉터리. **기존 템플릿 14개(`corpus/cases/`)와 물리적으로
  분리**한다(아래 "산출물 위치" 참조).
- `tests/test_generate_cases.py` — 생성기 pytest(축 정의의 결정성·유일성·정적 검증 통과·개념
  분포·1000개 하한).
- (문서) 이 스펙 자체.

## 비범위 (다음 스펙들 / 이 이슈 밖)

- **변환 엔진**(SQLGlot MySQL→PG). 생성기는 케이스(피검증 MySQL SQL + 제어 SQL)만 만든다.
  변환·정답(`expect`)은 적지 않는다(오라클 방식 그대로).
- **RAG / 지식 검색**.
- **하니스·정적 검증기 자체의 수정.** 생성물이 **기존** 계약을 지키게 만든다. 생성기가 검증기를
  바꿔야 통과하는 케이스는 만들지 않는다(계약을 못 지키는 축은 버린다).
- **새 개념·새 테이블·새 인덱스 추가.** 15개 개념·7테이블·기존 인덱스 안에서만 벌린다.
- **perf 케이스 생성 전면 제외(P1-2).** perf 룰(`access`/`forbid_full_scan`/`max_examined_rows`)은
  실행 계획을 채점해야 의미가 있는데, **현재 하니스는 perf를 로드·채점하지 않는다**(`loader.py`의
  `Case`에 perf 필드 없음, 하니스 스펙이 Performance Analyzer를 비범위로 둠). 따라서 생성한 perf
  변형은 어떤 게이트로도 "실행 계획이 안 깨졌음"을 보증할 수 없다(하니스 error==0은 쿼리 실행성만
  본다). **생성 perf 케이스를 이번 범위에서 뺀다** — perf 6개 개념(covering/multi-join/keyset/
  offset/non-sargable/groupby)은 **golden 5개 케이스로만 커버**한다. perf 대량 생성은 Performance
  Analyzer가 생긴 뒤 별도 스펙에서 EXPLAIN ANALYZE 판정 게이트와 함께 다룬다. 이번 대량 생성은
  **문법(syntax) 9개 개념 축에 집중**한다.
- **비영어/비ASCII·collation 의존 값.** 상위 계약이 문자열=바이트 동일로 못 박았으므로(코퍼스
  스펙 collation 비범위), 생성값도 ASCII·collation 무관으로 제한한다.

## 설계

이 도구는 하이브리드 아키텍처의 어느 컴포넌트도 아니다 — **코퍼스(데이터 자산)를 늘리는
오프라인 생성 도구**다. Validator/Performance Analyzer의 **입력을 대량 생산**할 뿐이므로,
검증도 "생성물이 계약을 지키는가"(정적 검증 통과 + 하니스 error==0)로 한다.

### 파라미터화 방식: **코드 축(axis) 조립** (택1)

세 후보를 놓고 **코드 축 조립**을 택한다.

| 방식 | 내용 | 장점 | 단점 |
|------|------|------|------|
| (A) 템플릿 YAML placeholder | 템플릿 SQL에 `{{price}}` 같은 자리표시자를 두고 값 목록으로 치환 | 템플릿이 데이터로 보임 | placeholder는 `{{object_name}}`(하니스 예약)과 충돌·혼동. 조합 규칙(어떤 값끼리 같이 쓰나, 컬럼별 허용 연산자)을 YAML에 못 담아 결국 코드가 필요 |
| (B) 코드 축(axis) 조립 | 템플릿마다 "바꿀 축"(컬럼·값·연산자)을 파이썬으로 정의하고, 축의 곱집합으로 SQL 문자열을 조립 | 조합 규칙·씨드 범위 제약·유효성을 코드에서 강제. 결정성·유일성·분포 제어 쉬움 | SQL이 f-string으로 코드에 박힘(템플릿 YAML보다 덜 "데이터"스러움) |
| (C) 혼합 | placeholder + 코드 조합 규칙 | — | 두 곳(YAML·코드)에 규칙이 흩어져 유지보수 최악. 단일 원본 원칙 위배 |

**택: (B) 코드 축 조립.** 근거:
- 이 프로젝트의 상위 스펙들이 일관되게 **단일 원본 + fail-closed + 결정성**을 선호한다
  (concepts.yaml 단일 원본, unknown field 실패 등). 파라미터의 **씨드 범위 제약**(예: `WHERE
  id = ?`의 `?`가 실제 존재하는 id여야 실행이 유효)과 **조합 규칙**(어떤 컬럼에 어떤 연산자가
  말이 되나, 어떤 값끼리 함께 써야 유효한가 — 아래 P1-3 구조화 축)은 본질적으로 코드 로직이라,
  YAML placeholder로는 절반만 표현되고 나머지 절반이 코드로 새어 나온다(=C의 최악). (B)는 규칙을
  한 곳(코드)에 모은다.
- **`{{object_name}}` 토큰 정확 보존(P1-1)**: `{{object_name}}`은 하니스 예약 placeholder이고,
  하니스는 정확히 이 토큰만 치환한다(`runner.py:294`, `statement.replace("{{object_name}}", name)`).
  주의: 파이썬 f-string에서 `f"{{object_name}}"`는 `{object_name}`(중괄호 1겹)을 내므로 **토큰이
  깨진다.** 그래서 생성기는 `OBJECT_NAME_PLACEHOLDER = "{{object_name}}"` 상수를 두고, DDL
  statement·제어 SQL 조립 시 이 상수를 문자열로 삽입한다(f-string 안에 쓰면 `{{{{object_name}}}}`로
  이스케이프). 산출 DDL에 정확한 토큰이 남는지는 **전용 테스트**로 고정한다(아래 태스크).

> **단일 원본 유지(P2-7)**: golden 14개 YAML과 Python 축 정의가 kind·concepts·기본 SQL을 각각
> 적으면 **두 원본**이 생긴다(golden을 고쳐도 생성기가 안 따라옴). 그래서 생성기는 golden YAML을
> `base_id`로 로드해 **kind·concepts·기본 SQL 골격을 golden에서 재사용**하고, Python `build`는
> **바뀌는 SQL·제어 필드만** 만든다. kind·concepts는 golden에서 온 값을 생성기가 한 번만 주입한다
> (`build`가 다시 채우지 않는다 — 내부 불일치 차단). 이로써 golden이 여전히 개념·kind의 단일
> 원본이고, 코드 축은 "무엇을 어떻게 벌릴지"만 담당한다.

> **Java 개발자 참고**: 파이썬의 "축(axis) 곱집합"은 자바로 치면 중첩 `for` 대신
> `itertools.product(colAxis, valAxis, opAxis)`로 데카르트 곱을 한 번에 순회하는 것이다
> (자바 스트림의 `flatMap` 중첩과 비슷). 각 축은 그냥 `list`(자바 `List<T>`)이고, 곱집합의
> 각 튜플이 케이스 하나가 된다. **단, 무조건 곱집합은 무효 조합을 낳으므로(P1-3) 상호 의존
> 값은 하나의 "구조화 축"(튜플의 리스트)으로 묶는다.**

### 케이스 생성 모델: golden 로드 → 축 → 변형(variant)

각 golden 템플릿을 `base_id`로 로드해 kind·concepts를 재사용하고(P2-7), "바꿀 부분"을
**축(axis)**으로 뽑는다.

```python
@dataclass(frozen=True)
class Axis:
    name: str  # 축 이름 (id 생성·디버깅용)
    values: list[Any]  # 이 축이 취할 값들. 각 값은 스칼라 또는 튜플(구조화 축)


@dataclass(frozen=True)
class Template:
    base_id: str  # golden 케이스 id (예: "limit-pagination").
    # 여기서 kind·concepts만 로드해 재사용(아래 단일 원본 계약)
    axes: list[Axis]  # 바꿀 축들
    build: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    # build(base_case, combination) → 케이스의 "바뀌는 필드만".
    # base_case=golden 케이스 dict(참조용), combination=축 값 조합.
    # SQL·제어 SQL·ordered 등만 반환. kind·concepts·id는 만들지 않는다.
    valid: Callable[[dict[str, Any]], bool] | None = None
    # 곱집합 조합의 유효성 predicate(P1-3). None이면 전부 유효.
```

- **단일 원본 계약 — 정확히 무엇이 어디 원본인가(리뷰 2차 #3)**: golden이 **kind·concepts의
  단일 원본**이고, **생성 SQL은 코드(build)가 원본**이다. 즉 build는 golden SQL을 문자열로
  재사용하지 않는다(축마다 SQL이 달라져 재사용이 불가능하다) — 대신 `base_case`를 **참조**로만
  받아, 필요하면 golden의 테이블·컬럼·리터럴 형태를 코드에서 따라 적되 SQL 자체는 코드가 만든다.
  P2-7이 막으려던 것은 "kind·concepts가 두 곳에 적혀 어긋나는 것"이었고, SQL은 애초에 벌리는
  대상이라 코드 원본이 맞다. 그래서 계약을 이렇게 좁힌다:
  - 생성기가 `base_id`로 golden 케이스를 로드 → `kind`·`concepts`를 **생성기가 케이스 dict에 한
    번 주입**. build는 이 둘을 채우지 않는다(내부 불일치 차단).
  - build는 `base_case`(참조)와 `combination`(축 값)을 받아 **피검증 SQL·제어 SQL·`ordered` 등
    바뀌는 필드만** 반환. id는 생성기가 축 조합으로 결정적 부여(아래).
  - SQL은 f-string으로 조립하되 `{{object_name}}`은 `OBJECT_NAME_PLACEHOLDER` 상수로 정확 보존(P1-1).
- **구조화 축(P1-3)**: 무조건 곱집합은 존재하지 않는 조합을 만든다 — 예: `(table)`×`(column)`을
  독립 축으로 벌리면 `orders.name`(orders에 name 없음)이 나온다. 그래서 **상호 의존 값은 하나의
  구조화 축(튜플의 리스트)으로 묶는다**:
  - backtick: `(table, projection, order_col)` 튜플 축 — `("products","id, name","id")`,
    `("orders","id, status","id")` 처럼 **실재하는 조합만** 나열.
  - upsert(dml): `(id, email, name)` 튜플 축 — 같은 시드 행 하나를 가리키는 3값을 함께 묶어,
    두 시드 행의 PK·UNIQUE를 동시에 건드리는 불가능 조합을 원천 차단.
  - keyset(제외됨 — perf) 대신 syntax 계열에서 상관 값(user_id ↔ ordered_at 경계)이 필요하면
    `(user_id, ordered_at)` 튜플로 묶는다.
- **predicate `valid`(P1-3 보조)**: 튜플로 못 묶는 잔여 무효 조합(예: 특정 stock 임계 × 특정
  연산자가 0행)이 있으면 `valid(combo)`로 걸러 버린다. 튜플 축이 1차 방어, predicate가 2차.
- 한 템플릿의 케이스 수 = 곱집합 중 `valid`를 통과한 조합 수.

### 씨드 범위에 파라미터 묶기 (실행 유효성의 핵심)

생성한 SQL이 **실제로 실행돼야**(error==0) 한다. 그래서 각 축의 값은 **결정적 씨드 범위 안**으로
제한한다(씨드는 난수 없이 행번호 n 기반이라 완전 결정적 — 코퍼스 씨드 스펙).

| 대상 | 씨드 사실(원본 확인) | 파라미터 축 제약 |
|------|----------------------|------------------|
| `users.id` | 1..1000 | id 필터는 1..1000 |
| `products.id` | 1..1000 | id 필터는 1..1000 |
| `products.name` | `Product 0001`..`Product 1000` (zero-pad) | 이름 리터럴은 `Product %04d` 형식, 1..1000 |
| `products.stock` | `n % 1000` → 0..999 (0 포함) | 임계값 0..999, 경계 0 반드시 포함 |
| `products.price` | `10 + n%500 + (n%100)/100` | 범위 필터는 10~510 사이 리터럴 |
| `orders.id` | 1..50000 | id 필터는 1..50000 |
| `orders.user_id` | `1 + n%1000` → 1..1000 | user_id 필터는 1..1000 |
| `orders.status` | ENUM 5값, `n%10` 편향 | status 리터럴은 5개 ENUM 값에서만 |
| `orders.ordered_at` | `2025-01-01 + n분`, n=1..50000 | 시각 리터럴은 이 범위 안 (약 2025-01-01 ~ 2025-02-04) |
| `payments.method` | ENUM 3값 | method 리터럴은 3개 값에서만 |
| `payments.paid` | 0/1 | bool 필터는 0/1 |
| `reviews.content` | `n%7==0`에만 `excellent` | LIKE 키워드는 `excellent`/`ordinary` 등 실제 존재 토큰 |
| `reviews.rating` | `1 + n%5` → 1..5 | rating 필터는 1..5 |

**축 값은 코드 상수로 이 표를 반영**한다. 씨드 파일을 파싱하지 않는다 — 씨드가 바뀌면 이
표(=코드 상수)를 같이 고쳐야 하는 결합이지만, 씨드는 실험 기반이라 자주 바뀌지 않고, 파싱은
과설계다(단순성 우선).

**"SQL 오류 없음"과 "유효 비어있지 않은 표본"은 다르다(P2-6).** 씨드 전역 범위 안의 값이라도
특정 조합은 0행을 낸다 — 예: `products`는 정확히 1000행이라 `LIMIT ... OFFSET 1000`은 0행이고,
`WHERE user_id = 7 AND ordered_at > <경계>`도 경계를 잘못 잡으면 0행이다. 0행은 `error`는
아니지만(SQL은 유효) 회귀 표본으로선 대개 무의미하다. 그래서 두 성공 조건을 분리한다:

- **최소 조건(필수)**: 생성 SQL이 실행상 유효(error 없음). 정적 검증 + 하니스 error==0이 게이트.
- **표본 품질 조건(권장)**: 결과가 비어있지 않음. **코드 상수 리뷰로는 보증 못 한다**(씨드가
  바뀌면 상수 표가 낡아도 pytest는 못 잡는다). 그래서 **실제 DB seed preflight**를 통합 테스트에
  둔다 — 컨테이너가 떠 있을 때 대표 케이스들의 결과 행 수가 0이 아닌지(템플릿별 최소 cardinality
  불변식) 실측한다. offset 축은 씨드 행 수(예: products 1000) **미만** 값만 쓰고, `(user_id,
  ordered_at)` 상관 축은 preflight로 비어있지 않음을 확인한다.

씨드 범위를 벗어나는 값은 축에 넣지 않는다(경계 검증에는 씨드 안 경계값 0·999·1000 등을 쓴다).

### 고유성·결정성

- **id 규칙**: `{base_id}-{axis1}-{axis2}-...`. 각 축 값을 kebab-case로 안전화(소문자화,
  비영숫자→`-`, 연속 `-` 축약)해 이어붙인다. 예: `limit-pagination-c5-o10`. 구조화 축(튜플)은
  튜플 성분을 순서대로 이어 안전화한다.
- **전역 유일 범위(추가 지적)**: 유일성은 **golden 14개 + generated 합집합** 기준으로 검사한다
  (생성 id가 golden id와 겹치면 안 된다). 생성기가 golden 케이스 id를 먼저 로드해 seen 집합에
  넣고, 생성 id를 그 집합에 대해 검사한다. 충돌 시 **에러로 실패**(조용히 접미사 붙이지 않음 —
  충돌은 축 정의 버그라 드러내야 한다). base_id가 golden id와 같아도, 생성 id는 축 접미사가
  붙어 달라진다(예: golden `limit-pagination` vs 생성 `limit-pagination-c5-o10`).
- **결정성**: 난수를 쓰지 않는다. 축 값은 코드 상수, 조합은 `itertools.product`의 결정적 순서.
  **같은 코드는 항상 같은 케이스 집합**을 같은 순서로 낸다(재실행 diff 없음 → git에 커밋 가능).
  난수 시드 고정 논쟁 자체가 없다(난수 미사용).
- **kebab-case 보증**: id는 `validate_corpus`의 `ID_PATTERN`(`^[a-z0-9]+(-[a-z0-9]+)*$`)을
  통과해야 한다. 안전화 함수가 이 패턴을 보증하고, 생성기가 산출 전 각 id를 이 정규식으로
  자기검사한다.

### 개념 분포 (기계 판독 하한 + fail-closed)

이 이슈의 대량 생성 대상은 **syntax 9개 개념**이다(perf 6개는 golden으로만 커버 — P1-2).
"미달이면 표만 찍고 통과"는 fail-open이라 금지한다(P1-4). 개념별 **하한을 코드 상수로 확정**하고,
미달 시 **생성 실패**한다.

- **하한 상수 — 확정값(리뷰 2차 #2)**: 아래 숫자는 **스펙이 확정한 계약**이다. 구현 중 임의
  조정 금지 — 값을 바꾸려면 **스펙을 고쳐 재리뷰**를 받아야 한다(fail-open을 완전히 닫기 위함).
  ```python
  # syntax 개념별 최소 케이스 수 (fail-closed 하한). 확정값 — 임의 변경 금지.
  MIN_PER_CONCEPT: dict[str, int] = {
      "limit-pagination": 66,
      "ifnull-coalesce": 66,
      "backtick-identifier": 40,
      "date-function": 66,
      "enum-type": 66,
      "tinyint-bool": 40,
      "unsigned-type": 66,
      "upsert-on-duplicate": 40,
      "auto-increment": 30,
  }  # 합 480 (하한). 실산출은 곱집합으로 이보다 커 총 --min-cases(1000) 이상.
  # perf 6개는 golden 커버(생성 0). 대량 생성에서 제외.
  PERF_CONCEPTS = {
      "covering-index",
      "multi-join",
      "keyset-pagination",
      "offset-pagination",
      "non-sargable-like",
      "groupby-aggregate",
  }
  ```
  값 축이 빈약한 개념(backtick·tinyint-bool·upsert 40, auto-increment 30)은 하한을 낮게 잡았다.
  각 하한의 근거(어떤 축으로 그 수를 넘기는지)는 아래 "축 산정"에 적는다.
- **축 산정(각 하한의 근거)**: 곱집합/구조화 축 크기가 하한을 넘김을 보증한다.
  - `limit-pagination`: count(6값) × offset(씨드 행수 미만 12값) = 72 ≥ 66.
  - `ifnull-coalesce`: nullable 컬럼(2) × 대체 리터럴(6) × LIMIT(6) = 72 ≥ 66.
  - `backtick-identifier`: `(table,projection,order_col)` 실재 조합(≥8) × LIMIT(6) = ≥48 ≥ 40.
  - `date-function`: 날짜함수(3) × INTERVAL(5) × LIMIT(5) = 75 ≥ 66 (fixed_clock 유지).
  - `enum-type`: status 값(5) × 비교연산(2) × LIMIT(7) = 70 ≥ 66.
  - `tinyint-bool`: paid(2) × 대상컬럼(3) × LIMIT(7) = 42 ≥ 40.
  - `unsigned-type`: stock 임계(경계 0·999 포함 12값) × 연산자(≤/</=/>, 유효 6) = 72 ≥ 66.
  - `upsert-on-duplicate`: `(id,email,name)` 시드행(10) × 갱신컬럼(4) = 40 ≥ 40.
  - `auto-increment`: 전용 테이블 컬럼 타입·개수 변형(30 조합) = 30 ≥ 30.
  실산출 합은 하한 합(480)을 크게 웃돌아 총 ≥ 1000을 만족한다. 구현이 이 축 크기를 줄여 하한
  미달이 되면 fail-closed 검사가 실패시킨다.
- **하한 vs 총 목표의 관계**: `MIN_PER_CONCEPT` 합(확정 480)은 **개념별 최소 보증선**이지
  실제 산출량이 아니다. 실제 산출은 곱집합/구조화 축 크기로 이보다 크게 나와 **총합 ≥ 1000**을
  달성한다(두 게이트가 독립: 개념별 하한 + 총 `--min-cases`). 즉 하한은 "한 개념도 굶지 않음"을,
  `--min-cases`는 "전체 표본 규모"를 각각 보증한다. 구현 시 축 크기를 조절해 둘 다 만족시킨다.
- **fail-closed 검사(P1-4)**: 생성 후 개념별 실제 산출량을 세어, `MIN_PER_CONCEPT`의 모든 개념이
  하한 이상인지 확인한다. **하나라도 미달이면 `--check-only`도 실패**(exit non-zero, 파일 미기록).
  총합이 `--min-cases`(기본 1000) 미만이어도 실패. 한두 개념에 몰려도 개념별 하한이 이를 막는다.
- **분포 표 파일(범위 포함)**: 생성기가 개념별 실제 산출량을 `corpus/generated/DISTRIBUTION.md`로
  쓴다(리뷰·추적용). 이 파일은 범위에 포함한다.
- **축 부족 개념 처리**: backtick 같은 개념은 값 축이 아니라 **직교 축**(인용할 컬럼 조합·테이블·
  LIMIT)으로 벌린다 — 단 P1-3대로 `(table, projection, order_col)` **구조화 축**으로 실재 조합만
  나열한다(`orders.name` 같은 무효 조합 금지). 그래도 하한에 못 미치면 그건 **버그이자 실패**이지
  "표기 후 통과"가 아니다(하한을 낮추려면 상수를 명시적으로 낮춰 리뷰받는다).

### 하니스 통과 보장 (error==0, pass 아님)

생성기는 산출 전 **자기검증**을 한다. 원자성(P1-5)과 게이트 정확성(P2-8)을 못 박는다.

1. **원자적 산출 + 재직렬화 검증(P1-5)**: `validate_corpus.validate_corpus`는 예외를 던지지 않고
   `ValidationResult`를 반환하며(`tools/validate_corpus.py:312`), 인메모리 dict 검증은 최상위
   `cases:` 구조·YAML 직렬화를 거치지 않는다. 그래서 **실제 산출물**을 검증하도록 순서를 계약한다:
   ```
   1. temp 디렉터리에 전체 YAML을 dump (파일 순서·dump 옵션 고정 — 아래)
   2. load_cases(sorted(temp.rglob("*.yaml")))로 재로드 (직렬화·최상위 구조 통과)
   3. validate_corpus(재로드된 cases, whitelist).ok 확인 + 개념 하한 검사
   4. 성공 시에만 --out을 temp 내용으로 디렉터리 단위 교체 (기존에서 사라진 파일 제거 포함)
   5. 실패 시 temp 폐기, --out 무손상 (부분 산출물 없음)
   ```
   이로써 "인메모리는 통과했는데 직렬화 후 깨짐", "쓰기 중간 실패로 반쪽 산출", "이전 실행이
   남긴 stale 파일"을 모두 막는다.
2. **`--out` 가드(P1-5)**: `--out`이 `corpus/cases`(golden) **자체·상위 경로·하위 경로 어디든**
   해당하면 **거부**한다. 즉 `--out`을 절대경로로 정규화(`Path.resolve()`)해 golden 루트와
   `is_relative_to` 양방향으로 겹치는지 검사하고, 겹치면 실패시킨다. 생성기가 golden을 덮어쓰지
   못하게 한다(디렉터리 단위 교체가 golden을 지우면 재앙).
3. **YAML dump·파일 순서 고정**: `yaml.safe_dump(..., sort_keys=False, allow_unicode=False,
   default_flow_style=False)`로 결정적 직렬화. 케이스는 생성 순서(결정적) 유지, 파일은 개념별
   고정 이름. 재실행 diff가 없어야 한다(결정성 계약).
4. **하니스 로드 경로(리뷰 2차 #1 — 중요)**: 현재 하니스 `load_corpus`는 `validate_corpus`를
   `allow_incomplete_coverage` 없이(기본 15개 전체 커버리지 요구) 호출한다(`harness/loader.py:76`).
   그래서 syntax 9개만 든 `corpus/generated`를 `python -m harness --cases-dir corpus/generated`로
   돌리면 **perf 6개 미커버로 `ValueError`가 나 로딩 단계에서 실패**한다. 하니스 수정은 이 이슈
   **비범위**이므로, 생성물 검수는 **CLI를 쓰지 않고** 통합 테스트가 아래 경로로 직접 로드한다:
   ```
   raws, _ = load_cases(sorted(Path("corpus/generated").rglob("*.yaml")))
   validate_corpus(raws, whitelist, allow_incomplete_coverage=True).ok  # 커버리지 경고 허용
   cases = [load_case(r) for r in raws]                                  # loader의 low-level 재사용
   ```
   (`load_corpus`가 아니라 그 구성요소 `load_cases`/`validate_corpus`/`load_case`를 조립한다.
   `load_case`는 이미 public이다 — `harness/loader.py:59`.) **"CLI로 독립 실행 가능"이라던 이전
   서술은 틀렸으므로 제거**한다.
5. **하니스 게이트 = Runner 결과 직접 검사(P2-8)**: 하니스 CLI exit code는 `fail` 하나만 있어도
   1이라(`report.py:28`, `all(status == "pass")`) 성공 게이트로 못 쓴다. 그래서 통합 테스트는
   위 경로로 로드한 `Case`들을 **`Runner.run_case`에 직접 넣어 `CaseResult`의
   `all(r.status != "error")`**로 검사한다(`fail`은 허용). 컨테이너가 떠 있을 때만 도는 통합
   테스트다(`@pytest.mark.integration`). error==0의 의미는 배경에서 좁힌 대로 "MySQL 원본·도달한
   제어 SQL·인프라·cleanup에 error 없음".
6. **격리 계약 준수**: 생성하는 dml/ddl은 **공유 시드·스키마를 바꾸지 않는다.**
   - dml: statement는 트랜잭션 롤백으로 격리된다(하니스가 보장). 하지만 생성 축이 **새 id를
     소비하거나(AUTO_INCREMENT) 시드 행을 물리 삭제**하는 값을 만들면, 롤백돼도 케이스 의미가
     흐려진다. 그래서 dml 축은 **기존 시드 행을 재사용**하는 값만 쓴다(upsert 템플릿이 id=1의
     email 충돌을 재사용하듯). DELETE/TRUNCATE류는 만들지 않는다.
   - ddl: 전용 TEMPORARY TABLE + `{{object_name}}` placeholder만 쓰고, 정리는 하니스 finally
     DROP에 맡긴다(작성자 teardown 없음). ddl 축은 컬럼 타입·개수 등 **전용 테이블 내부**만
     벌린다. 공유 테이블을 건드리는 DDL은 만들지 않는다.

**MySQL 전용 문법이 PG에서 fail 나는 건 목표**다(변환 진행도 지표). 생성기는 fail을 피하려
문법을 순화하지 않는다 — 오히려 MySQL 고유 문법을 값별로 벌려, 변환 엔진이 나중에 넓게
검증되도록 한다.

### 산출물 위치·형식

- **위치**: `corpus/generated/syntax/*.yaml` + `corpus/generated/DISTRIBUTION.md`. perf는 생성하지
  않으므로(P1-2) `performance/` 하위는 두지 않는다. 기존 `corpus/cases/`(golden 14개)와
  **디렉터리를 분리**한다. 근거:
  - golden 템플릿은 사람이 관리하는 원본이고, 생성물은 도구가 덮어쓰는 파생물이라 **생명주기가
    다르다**. 섞으면 재생성이 golden을 훼손한다(P1-5의 디렉터리 단위 교체가 golden을 지운다).
  - 정적 검증기(`tools/validate_corpus.py`)는 `--cases-dir`와 `--allow-incomplete-coverage`를
    받으므로 generated를 단독 검증할 수 있다. **단 하니스 CLI(`python -m harness`)는 generated를
    단독으로 못 돌린다**(위 "하니스 로드 경로" — 커버리지 미달로 로딩 실패). 하니스 검수는
    통합 테스트가 low-level 로더로 직접 로드해 수행한다.
- **파일 단위**: 개념(=템플릿)별로 한 파일. 예: `generated/syntax/limit-pagination.yaml`.
  한 파일에 수십~수백 케이스가 `cases:` 리스트로 들어간다. 파일이 개념별로 갈려 있어 어떤
  개념이 얼마나 나왔는지 눈으로 보이고, diff도 개념 단위로 격리된다.
- **커버리지 검사 상호작용(중요, P1-2 후속)**: `validate_corpus`의 커버리지 검사는 `--cases-dir`
  아래 케이스가 **15개 개념 전부**를 덮는지 본다(기본 실패). 하지만 generated는 syntax 9개 개념만
  생성하므로, `corpus/generated`를 **기본 커버리지 검사로 단독 실행하면 perf 6개 미커버로 실패**한다.
  두 가지로 대응:
  - 생성기의 **자기 정적 검증**은 `validate_corpus(..., allow_incomplete_coverage=True)`로 호출해
    커버리지 미달을 경고로 낮춘다(생성기가 검증하는 대상은 syntax 9개뿐이라 이게 옳다).
  - 대신 생성기는 **자체 개념 하한 검사(fail-closed)**로 syntax 9개가 각각 하한 이상인지 확인한다
    (커버리지 검사보다 강한 게이트).
  - 15개 전부의 커버리지는 **golden(`corpus/cases`)이 이미 100% 충족**한다(golden 14개가 15개
    개념 전부 덮음 — schema.md 커버리지 표). golden과 generated를 한 디렉터리로 합쳐 검증하지
    않는다(생명주기 분리).

### CLI

```
python tools/generate_cases.py [--out corpus/generated] [--min-cases 1000] [--check-only]
```

- 기본: 축 조합을 펼쳐 케이스를 만들고, temp에 dump→재로드→정적 검증→개념 하한 검사(P1-5)를
  통과하면 `--out`을 디렉터리 단위 교체한다.
- `--out` 가드: `corpus/cases`(golden)나 그 상위 경로면 **거부**(P1-5).
- `--min-cases`: 총 케이스 수가 이 값 미만이면 **실패**(표본 부족 회귀 방지).
- `--check-only`: 파일을 쓰지 않고 생성·검증만. **개념 하한 미달·min-cases 미달이면 non-zero
  종료**(fail-closed — P1-4). CI/테스트용.
- 항상 `DISTRIBUTION.md`용 **개념별 산출량 표**를 출력·기록한다.
- 하니스 error==0 검사는 이 CLI가 아니라 **통합 테스트가 Runner 결과로 직접** 한다(P2-8).

## 태스크

- [ ] `tools/generate_cases.py` 골격 — `Axis`/`Template` 모델(구조화 축·`valid` predicate 포함),
      `OBJECT_NAME_PLACEHOLDER` 상수, 안전 id/kebab-case 유틸, golden 로드(base_id→kind·concepts),
      곱집합+predicate 전개 → 검증: 축 2개짜리 더미 템플릿이 유효 조합 수만큼 케이스를 내고, id가
      전부 `ID_PATTERN` 통과, golden에서 kind·concepts를 가져옴(pytest, DB 불필요)
- [ ] syntax 9개 템플릿 축 정의(`build`는 SQL·제어 필드만) — limit/ifnull/backtick(구조화 축
      `(table,projection,order_col)`)/date-function(fixed_clock 유지)/enum/tinyint-bool/unsigned/
      upsert(dml, `(id,email,name)` 구조화 축)/auto-increment(ddl, `{{object_name}}` 토큰 보존) →
      검증: 각 산출이 `validate_corpus` 통과, 씨드 범위 밖 값 없음, `orders.name` 같은 무효 조합
      없음(축 상수 리뷰 + pytest)
- [ ] `{{object_name}}` 토큰 전용 테스트(P1-1, 리뷰 2차 #4) — ddl 산출 statement·제어 SQL에
      정확히 `{{object_name}}`(중괄호 2겹) 토큰이 남는지 → 검증: `"{{object_name}}"`은 `"{object_name}"`을
      부분 문자열로 포함하므로 "2겹 포함 & 1겹 미포함"은 **동시 만족 불가**다. 대신 (a) 2겹 토큰을
      전부 제거한 잔여 문자열에 `{`·`}` 중괄호가 남지 않는지 확인하거나, (b) 정규식
      `(?<!\{)\{[^{}]*\}(?!\})`(2겹으로 안 감싸인 홑중괄호)가 **매치되지 않는지**로 검사한다(pytest)
- [ ] 개념 분포 fail-closed(P1-4) — `MIN_PER_CONCEPT` 코드 상수, 생성 후 개념별 하한 검사, 미달
      시 실패 → 검증: `--check-only`가 하한 미달 시 non-zero, 정상 시 9개 개념 각각 하한 이상,
      총합 ≥ `--min-cases`; `DISTRIBUTION.md` 산출량 표 생성(pytest)
- [ ] 전역 유일성(golden+generated)·결정성 — golden id 로드 후 생성 id 충돌 시 실패, 난수 미사용
      → 검증: 두 번 생성이 byte-identical, golden id와 겹치는 축을 넣으면 실패, 의도적 생성 id
      충돌도 실패(pytest)
- [ ] 원자적 산출 + 재직렬화 검증 + `--out` 가드(P1-5) — temp dump→`load_cases` 재로드→
      `validate_corpus(..., allow_incomplete_coverage=True).ok`→디렉터리 교체, 실패 시 무손상,
      golden 경로 `--out` 거부(자체·상위·하위 모두 — `resolve()`+`is_relative_to` 양방향) → 검증:
      검증 실패 케이스를 섞으면 `--out` 미변경(부분 산출 없음), `--out corpus/cases`·
      `corpus/cases/syntax`(하위)·`corpus`(상위)가 모두 거부됨(pytest)
- [ ] 생성 실행 + 하니스 게이트(P2-8, 리뷰 2차 #1) — `generate_cases.py`로 `corpus/generated`
      산출 후, 통합 테스트가 **low-level 로드**(`load_cases`→`validate_corpus(...,
      allow_incomplete_coverage=True)`→`load_case`; `load_corpus`/`python -m harness`는 커버리지
      미달로 못 씀)로 `Case`를 만들고 `Runner.run_case`로 돌려 **`all(status != "error")`** 확인
      → 검증(통합, DB 필요): 1000+ 케이스 error 0, dml/ddl 격리로 시드·스키마 불변
- [ ] seed preflight(P2-6, 표본 품질) — 대표 케이스의 결과 행 수가 0이 아닌지 실측(템플릿별 최소
      cardinality 불변식) → 검증(통합, DB 필요): offset·상관 축 케이스가 비어있지 않음
- [ ] `tests/test_generate_cases.py` — 위 DB 불필요 검증을 pytest로 묶음 → 검증: `pytest`(DB
      불필요)에서 결정성·유일성·토큰 보존·정적 검증·분포 하한·원자성·`--out` 가드 모두 확인

## 결정 근거 (trade-off)

- **파라미터화 = 코드 축 조립(B)**: 씨드 범위 제약과 조합 규칙이 본질적으로 코드 로직이라,
  YAML placeholder(A)로는 절반만 표현되고 나머지가 코드로 새어 나온다(혼합 C의 최악). 규칙을
  코드 한 곳에 모아 단일 원본·fail-closed(상위 스펙 기조)를 지킨다. 대가는 SQL이 f-string으로
  코드에 박히는 것인데, golden 템플릿이 YAML로 별도 존재하므로 "데이터로서의 케이스"는 보존된다.
- **`{{object_name}}` 토큰 정확 보존(P1-1)**: 하니스는 정확히 `{{object_name}}`(중괄호 2겹)만
  치환한다(`runner.py:294`). 파이썬 f-string은 `f"{{object_name}}"`를 `{object_name}`(1겹)으로
  낳아 토큰을 깬다. 그래서 `OBJECT_NAME_PLACEHOLDER` 상수를 문자열로 삽입하고 전용 테스트로
  토큰을 고정한다(f-string 안이면 `{{{{...}}}}`로 이스케이프).
- **단일 원본 계약 좁힘(P2-7 + 리뷰 2차 #3)**: golden이 **kind·concepts의 단일 원본**, 생성
  **SQL은 코드(build) 원본**이다. SQL은 축마다 달라져 golden 문자열을 그대로 재사용할 수 없으므로,
  "golden SQL 재사용"이 아니라 "golden을 `base_id`로 로드해 kind·concepts만 생성기가 한 번 주입,
  build는 `base_case`를 참조로 받아 바뀌는 필드만 반환"으로 계약을 좁혔다. P2-7이 막으려던 것은
  kind·concepts가 두 곳에 적혀 어긋나는 것이지, SQL을 코드로 만드는 것 자체가 아니다.
- **구조화 축 + predicate(P1-3)**: 무조건 데카르트 곱은 `orders.name`처럼 없는 조합·두 시드 행
  동시 충돌 같은 무효 케이스를 만든다. 상호 의존 값을 튜플로 묶어 실재 조합만 나열하고, 잔여
  무효는 `valid` predicate로 버린다.
- **씨드 범위에 축을 묶음(파싱 대신 코드 상수)**: 생성 SQL이 실제 실행돼야(error==0) 하므로
  축 값은 존재하는 id·ENUM 값·경계값이어야 한다. 씨드 파일을 파싱해 범위를 자동 추출하는 건
  과설계(단순성 우선)이고, 씨드는 실험 기반이라 자주 안 바뀐다. 대신 축 상수 표를 스펙에 박고,
  코드 상수가 낡는 것은 **DB seed preflight(P2-6)**로 잡는다("0행 아님"은 코드 리뷰로 못 봄).
- **목표는 error==0이지 pass 아님(의미 좁힘 — P2-8)**: pass-through 하니스에서 MySQL 전용 문법의
  PG fail은 정상(변환 진행도 지표)이라 pass를 목표로 삼지 않는다. 단 하니스 CLI exit code는 fail에도
  1이라 게이트로 못 쓰므로, 통합 테스트가 `Runner` 결과로 `all(status != "error")`를 직접 본다.
  error==0의 의미도 "도달한 SQL·인프라·cleanup에 error 없음"으로 좁힌다.
- **원자적 산출(P1-5)**: `validate_corpus`는 예외를 안 던지고 인메모리 dict는 직렬화·최상위
  구조를 안 거친다. 그래서 temp에 실제 산출→`load_cases` 재로드→`.ok` 확인→디렉터리 교체로
  "직렬화 후 깨짐·반쪽 산출·stale 파일"을 막고, `--out` 가드로 golden 훼손을 막는다.
- **생성물 디렉터리 분리(`corpus/generated`)**: golden 14개는 사람 원본, 생성물은 도구 파생물로
  생명주기가 다르다. 섞으면 재생성(디렉터리 교체)이 golden을 덮어쓴다. 하니스·검증기가
  `--cases-dir`를 받아 분리 검증이 자연스럽다.
- **개념 분포는 기계 판독 하한 + fail-closed(P1-4)**: "미달 표기만 하고 통과"는 fail-open이라
  어떤 편향된 분포도 통과시킨다. 개념별 하한을 코드 상수로 확정하고 미달 시 실패시켜, 한두
  개념에 몰리는 것을 막는다. 억지 무의미 변형 대신 하한을 명시적으로 낮춰 리뷰받는다.
- **perf 생성 제외(P1-2)**: perf 룰은 실행 계획을 채점해야 의미가 있는데 현재 하니스는 perf를
  로드·채점하지 않는다. "축 상수 리뷰"로는 옵티마이저 실행 계획을 보증할 수 없으므로, 대량 perf
  생성을 이번 범위에서 빼고 golden 5개로만 커버한다. perf 대량 생성은 Performance Analyzer와
  EXPLAIN 게이트가 생긴 뒤 별도 스펙에서 다룬다.
- **결정성(난수 미사용)**: 축 값이 코드 상수 + `itertools.product` 결정적 순서라 재실행 diff가
  없다. 생성물을 git에 커밋해도 노이즈 없고, "난수 시드 고정" 논쟁이 아예 없다.
- **표준 라이브러리 + PyYAML만**: `validate_corpus`와 같은 의존성 정책. 생성기가 무거운 스택을
  끌어오면 코퍼스 검증 파이프라인과 어긋난다.
- **스펙 파일 위치**: 기존 스펙들이 `docs/superpowers/specs/`에 있어 관행을 따른다.
