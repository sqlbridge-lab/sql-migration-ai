# SQLBridge AI — Claude 세팅 설계

## Status: Approved

## 배경

이 프로젝트(`sql-migration-ai`)는 MySQL SQL을 PostgreSQL SQL로 변환하고, 변환 결과의
정확성과 성능을 검증하는 **학습용 Python 프로젝트**다. 현재 저장소에는 `README.md`만 있는
빈 상태다.

세팅의 모범 사례로 `/Users/imjunhyeon/ssogssog/ssogssog_spring`의 Claude 세팅
(`CLAUDE.md`, `.githooks/`, `.claude/skills`, `.claude/commands`,
`.claude/settings.local.json`)을 참고한다. 단, 모범 세팅은 이미 아키텍처가 확정된 Spring
프로젝트를 위한 실행 중심 스킬인 반면, 이 프로젝트는 Java 개발자가 Python·RAG를 학습하며
단계적으로 만들어가는 성격이라는 점을 반영한다.

## 설계 원칙

- **워크플로 단계 기반 스킬**: 스킬은 "Phase별 지식"이 아니라 "모든 Phase에서 반복되는
  작업 흐름"을 담는다. Phase별 지식(SQLGlot 사용법, RAG 설계 등)은 스킬이 아니라 그때그때
  `docs/specs/`의 스펙 문서에 들어간다.
- **미리 만들지 않는다**: 지금 당장 쓰지 않을 Phase별 스킬을 미리 만들지 않는다.
  (SQLBridge 프롬프트 20번 원칙)
- **학습 멘토링 내장**: Java 개발자를 위한 설명은 별도 스킬로 분리하지 않고 코드 작성 스킬에
  녹인다. 코드를 쓸 때마다 발동돼야 하는 원칙이기 때문이다.
- **리뷰는 Codex 담당**: 스펙 리뷰·코드 리뷰의 1차 주체는 Codex(사용자가 외부에서 트리거)다.
  Claude는 `/review` 커맨드로 보조 2차 리뷰만 제공한다.

## 워크플로

```text
1. 스펙 작성   → Claude: write-spec 스킬 (docs/specs, 태스크 분해 포함)
2. 스펙 리뷰   → Codex (외부, 사용자 트리거)
3. 코드 작성   → Claude: implement-python 스킬
4. 코드 리뷰   → Codex (외부) + 필요시 Claude /review
5. 디버깅      → Claude: debug-python 스킬
6. 커밋/푸시   → Claude: pre-commit-check / pre-push-check 스킬
```

## 파일 구조

```text
sql-migration-ai/
├── CLAUDE.md                    # 프로젝트 가이드 + 학습 멘토링 원칙 + 워크플로
├── .githooks/
│   ├── pre-commit               # 비밀값 차단 (ssogssog 패턴 재사용)
│   └── pre-push                 # ruff + pyright + pytest 실행
└── .claude/
    ├── settings.local.json      # 최소 권한 allowlist
    ├── commands/
    │   └── review.md            # Codex 1차 후 Claude 2차 리뷰
    └── skills/
        ├── write-spec/          # 스펙 문서 작성 + 태스크 분해 → docs/specs
        ├── implement-python/    # 코드 작성 + Java↔Python 학습 멘토링
        ├── debug-python/        # 체계적 디버깅 → 최소 수정
        ├── pre-commit-check/    # 커밋 게이트
        └── pre-push-check/      # 푸시 게이트
```

## 각 구성요소 명세

### CLAUDE.md

- **프로젝트 개요**: MySQL → PostgreSQL SQL 변환·검증 도구. 하이브리드 구조 원칙
  (Parser가 구조 파악 / Rule Engine이 결정적 변환 / RAG가 지식·사례 검색 / LLM이 복합·미지원
  변환 보조 / Validator가 정확성 검증 / Performance Analyzer가 성능 분석). LLM을 모든 작업의
  중심에 두지 않고, 결정적 코드로 처리 가능한 것은 코드로 처리한다.
- **기술 스택**: Python 3.12+, uv(패키지 관리), Ruff(포맷·린트), Pyright(타입 체크),
  pytest(테스트). SQLGlot(파싱·transpile). RAG/LLM 스택은 해당 Phase에서 확정.
- **Python 코딩 규칙**: 타입 힌트 사용, dataclass로 도메인 모델, 작은 함수, 예외를 숨기지
  않음, 구조화된 로깅, 과도한 추상화·불필요한 디자인 패턴 금지, MVP 단계에서는 이해하기 쉬운
  코드 우선.
- **Java 개발자 학습 지원 원칙**: Java와 다른 Python 문법·관용구는 비교해서 설명한다.
- **브랜치/커밋 규칙**: 브랜치 `{name}/{purpose}/{desc}`, 커밋 `{purpose}({scope}): {desc}`.
  purpose 예: feat, fix, refactor, chore, docs, test.
- **git hook 활성화 안내**: `git config core.hooksPath .githooks`.
- **워크플로 섹션**: 위 6단계 흐름.

### .githooks/pre-commit

ssogssog 패턴을 재사용한다. staged diff의 추가 라인에서 하드코딩된 비밀값(api-key, secret,
password, token, credential 등) 패턴을 탐지해 커밋을 차단한다. `${ENV}`, `<PLACEHOLDER>`,
더미값(test/dummy/example 등)은 통과. 우회는 `git commit --no-verify`.

### .githooks/pre-push

push 전에 `ruff check`, `pyright`, `pytest`를 실행하고 하나라도 실패하면 push를 차단한다.
우회는 `git push --no-verify`. (도구가 아직 설치 전이라면 없는 도구는 건너뛰도록 방어.)

### .claude/settings.local.json

최소 권한 allowlist. context7 MCP(문서 조회), 기본 git/python 명령 정도. 실제 필요에 따라
점진적으로 추가.

### .claude/commands/review.md

Codex가 1차 리뷰어이고, 이 커맨드는 Claude의 2차 로컬 리뷰다. 심각도 순으로
`[required]`/`[recommended]`/`[note]` 태그로 보고. 리뷰 렌즈: 하이브리드 구조 역할 분리
준수, Python 코드 품질, 타입 힌트, 테스트 커버리지, 비밀값 노출.

### .claude/skills/write-spec

Phase/태스크마다 `docs/specs/`에 스펙 문서를 작성한다. MVP 범위·비범위를 명시하고, 태스크
체크리스트를 포함한다(계획 분해 기능 흡수). "코드를 한 번에 쏟지 말고 단계적으로" 원칙 내장.
스펙 작성 후 Codex 리뷰로 넘긴다.

### .claude/skills/implement-python

Python다운 코드를 작성하면서 Java와 다른 점을 설명한다(dataclass, Protocol/ABC, 타입 힌트,
list/dict comprehension, iterator/generator, context manager, 예외 처리, mutable default
argument 주의 등). 과도한 추상화 금지, MVP 우선, 테스트 가능한 의존성 구조. 코드 작성 후
테스트를 함께 제공.

### .claude/skills/debug-python

체계적 디버깅. 전체 코드를 다시 쓰기 전에 (1) 현재 문제 (2) 발생 원인 (3) 최소 수정안
(4) 구조 개선안 순서로 접근한다. (SQLBridge 프롬프트 19번 반영)

### .claude/skills/pre-commit-check

커밋 직전 staged diff만 검사. Check 1: 비밀값/민감정보 노출. Check 2: 커밋 메시지 형식
(`{purpose}({scope}): {desc}`). 테스트 실행·파일 수정·언스테이징은 하지 않는다.

### .claude/skills/pre-push-check

push 직전 게이트. Check 1: `ruff check` + `pyright` + `pytest` 통과 여부(hook이 자동화하는
방어선). Check 2: docs 동기화 여부(자동화 불가, 스킬로 판단).

## 범위

이번 세팅 작업의 범위는 Phase 0(개발 환경)과 Phase 1(SQLGlot 파싱 실험)을 지원하는 데 필요한
공통 세팅까지다. RAG/LLM 관련 Phase별 스킬은 해당 Phase에 진입할 때 각각 만든다.

## 결정 근거 (trade-off)

- **Phase별 스킬 대신 워크플로 스킬**: Phase 내용은 매번 바뀌지만 작업 흐름은 동일하다.
  Phase 지식을 스킬에 넣으면 구현 스킬이 매 Phase마다 늘어나 유지보수 부담·구형화 위험이 크다.
- **`/dev-plan` 제거**: 이 프로젝트는 매 Phase가 브레인스토밍→스펙→구현이라 "계획 분해"와
  "스펙 작성"이 같은 단계에서 일어난다. write-spec 스킬이 태스크 체크리스트를 포함해 계획
  기능을 흡수한다.
- **Ruff + Pyright + pytest**: 현재 Python 생태계에서 가장 빠르고 통합된 도구 조합.
  Black+Flake8+mypy 대비 도구 수가 적고 속도가 빠르다.
