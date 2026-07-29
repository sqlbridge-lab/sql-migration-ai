# SQLBridge AI — Claude 세팅 설계

## Status: Request changes 반영 (재검토 대기)

> [!NOTE]
> **1차 리뷰([required] 5건)**: settings scope, allowlist 정의, 리뷰 커맨드 이름 충돌,
> pre-push fail-open, Ruff format 검사 누락 → 모두 반영.
>
> **2차 리뷰([required] 3건 + [recommended] 1건)**: allowlist를 wildcard 없는 정확 열거로
> 교체(hook과 `--locked`까지 일치, `ruff format --check` 추가, credential/secret deny 실물
> 패턴), context7는 `.mcp.json` 신규 설치가 아니라 **이미 설치된 플러그인 전제**로 정정(권한
> 이름 `mcp__plugin_context7_context7__*` 유지), secondary-review 호출 계약·자동 호출 방지
> 고정, settings.local.json은 파일 구조에서 제외하고 `.gitignore`에 명시 → 모두 반영.
> 재리뷰 후 Approved로 승격한다.

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
  Claude는 `/secondary-review` 스킬로 보조 2차 리뷰만 제공한다. (Claude Code 기본 제공
  `/review` 명령과 이름이 충돌하고, 동일 이름이면 skill이 우선하므로 별도 이름을 쓴다.)

## 워크플로

```text
1. 스펙 작성   → Claude: write-spec 스킬 (docs/specs, 태스크 분해 포함)
2. 스펙 리뷰   → Codex (외부, 사용자 트리거)
3. 코드 작성   → Claude: implement-python 스킬
4. 코드 리뷰   → Codex (외부) + 필요시 Claude: secondary-review 스킬
5. 디버깅      → Claude: debug-python 스킬
6. 커밋/푸시   → Claude: pre-commit-check / pre-push-check 스킬
```

## 파일 구조

```text
sql-migration-ai/
├── CLAUDE.md                        # 프로젝트 가이드 + 학습 멘토링 원칙 + 워크플로
├── pyproject.toml                   # 프로젝트 메타 + dev 의존성(ruff/pyright/pytest)
├── uv.lock                          # 고정된 의존성 잠금 파일
├── .gitignore                       # **/.claude/settings.local.json 등 제외
├── .githooks/
│   ├── pre-commit                   # 비밀값 차단 (ssogssog 패턴 재사용)
│   └── pre-push                     # uv run --locked 로 ruff/pyright/pytest 실행
└── .claude/
    ├── settings.json                # [공유·커밋됨] 최소 권한 allowlist + deny
    │                                # (settings.local.json = 개인 override, gitignore, 커밋 안 함)
    └── skills/
        ├── secondary-review/        # Codex 1차 후 Claude 2차 리뷰 (기본 /review 와 이름 분리)
        ├── write-spec/              # 스펙 문서 작성 + 태스크 분해 → docs/specs
        ├── implement-python/        # 코드 작성 + Java↔Python 학습 멘토링
        ├── debug-python/            # 체계적 디버깅 → 최소 수정
        ├── pre-commit-check/        # 커밋 게이트
        └── pre-push-check/          # 푸시 게이트
```

## 각 구성요소 명세

### CLAUDE.md

- **프로젝트 개요**: MySQL → PostgreSQL SQL 변환·검증 도구. 하이브리드 구조 원칙
  (Parser가 구조 파악 / Rule Engine이 결정적 변환 / RAG가 지식·사례 검색 / LLM이 복합·미지원
  변환 보조 / Validator가 정확성 검증 / Performance Analyzer가 성능 분석). LLM을 모든 작업의
  중심에 두지 않고, 결정적 코드로 처리 가능한 것은 코드로 처리한다.
- **기술 스택**: Python 3.12+, uv(패키지 관리·잠금 실행). Ruff는 **린트(`ruff check`)와
  포맷(`ruff format`) 둘 다** 담당하며 게이트에서 각각 별도로 검사한다. Pyright(타입 체크),
  pytest(테스트). 이 4개 도구는 `pyproject.toml`의 dev 의존성으로 선언하고 `uv.lock`에
  고정한다. SQLGlot(파싱·transpile). RAG/LLM 스택은 해당 Phase에서 확정.
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

push 전 품질 게이트. `uv`로 잠금된 환경에서 다음을 실행하고 하나라도 실패하면 push를 차단한다.

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest
```

우회는 `git push --no-verify`.

**fail-open 금지**: 이전 설계는 "도구 미설치 시 건너뛰기"였으나, 그러면 도구가 하나도 없는
환경에서도 push가 통과해 게이트 이름과 동작이 모순된다. 따라서 방어 방향을 뒤집는다 —
`uv`가 없거나 `uv.lock`이 없으면 **건너뛰지 않고 설정 안내 메시지와 함께 실패**시킨다
(fail-closed). ruff/pyright/pytest는 `pyproject.toml`의 dev 의존성으로 선언하고 `uv.lock`에
고정하므로 `uv run --locked` 시점에 항상 존재한다.

### .claude/settings.json (공유·커밋됨)

팀·브랜치에 공유되는 최소 권한 정책. 공유 allowlist는 반드시 이 파일에 둔다.
(`settings.local.json`은 전역 gitignore에 걸려 커밋되지 않으므로 여기에 두면 세팅이 재현되지
않는다.)

**allowlist는 정확히 열거**한다. Claude Code의 `*`는 **공백을 포함한 임의 문자열**을 매칭하므로
(`Bash(git *)`가 `git log --oneline --all`을 매칭) `Bash(uv run ruff check *)` 같은 규칙은
`uv run ruff check --fix .`(파일을 실제 수정)까지 자동 승인한다. 따라서 읽기·조회성 명령만
**wildcard 없이 정확한 문자열로** 허용하고, 상태를 바꾸는 명령은 승인 대상으로 남긴다. hook이
실행하는 명령과 **문자열이 정확히 일치**해야 매칭되므로 `--locked`까지 포함한다.

- **allow (최종 JSON에 들어갈 정확한 규칙)**:
  ```text
  Bash(git status)
  Bash(git diff)
  Bash(git diff --cached)
  Bash(git diff origin/main...HEAD)
  Bash(git log)
  Bash(uv run --locked ruff check .)
  Bash(uv run --locked ruff format --check .)
  Bash(uv run --locked pyright)
  Bash(uv run --locked pytest)
  mcp__plugin_context7_context7__resolve-library-id
  mcp__plugin_context7_context7__query-docs
  ```
- **가변 인자가 필요한 명령**: 파일별 리뷰(`ruff check <path>`)처럼 인자가 매번 바뀌는 명령은
  wildcard 자동 승인 대신 **실행 시 승인**을 받는다. 굳이 자동화하려면 허용 형태를 별도 규칙으로
  좁게 고정한다.
- **승인 대상으로 남김 (allow 안 함)**: `git add`/`git commit`/`git push`/`git reset` 등
  변경·삭제·push 명령, `ruff --fix`, 파일 쓰기·삭제.
- **deny (실물 패턴)**:
  ```text
  Read(./.env)
  Read(./.env.*)
  Read(./**/*credential*)
  Read(./**/*secret*)
  ```

### .claude/settings.local.json (개인·gitignore)

개인 override 전용. 공유가 필요한 규칙은 여기에 두지 않는다.

> [!NOTE]
> Claude Code가 **자신이 설정을 저장할 때는** 이 파일을 전역 git excludes에 자동 추가하지만,
> 우리가 Write 도구로 직접 만들 경우 그 자동 처리가 보장되지 않고, 다른 개발자 환경에 전역
> ignore가 없으면 실수로 커밋될 수 있다. 따라서 이 파일은 **파일 구조 산출물에서 제외**하고,
> 저장소 `.gitignore`에 `**/.claude/settings.local.json`을 명시해 어느 환경에서든 제외되도록
> 한다.

### context7 MCP

이 프로젝트에는 context7가 **플러그인으로 이미 설치**되어 있어 별도 `.mcp.json` 설치가 필요
없다. 실제 노출되는 MCP tool 이름은 `mcp__plugin_context7_context7__resolve-library-id`,
`mcp__plugin_context7_context7__query-docs`이며 위 allowlist가 이 이름을 그대로 쓴다.
(`.mcp.json`으로 새로 설치하면 서버 이름이 달라져 `mcp__context7__...` 형태가 되므로 권한
이름이 어긋난다. 그래서 신규 설치가 아니라 기존 플러그인 전제를 명시한다.) 인증은 익명 tier로
동작하며, rate limit 상향이 필요하면 `CONTEXT7_API_KEY`를 개인 환경에 설정한다.

### .claude/skills/secondary-review

Codex가 1차 리뷰어이고, 이 스킬은 Claude의 2차 로컬 리뷰다. **기본 제공 `/review` 명령과
이름이 충돌하지 않도록 `secondary-review`로 명명**한다(동일 이름이면 skill이 우선하고
`.claude/commands/`는 skills로 통합됐으므로, skill 형식으로 둔다).

**호출 계약**:
- **인자 없음**: `git diff origin/main...HEAD` (현재 브랜치가 `origin/main`에서 분기한 이후의
  커밋된 변경). 미커밋(working-tree) 변경은 기본 리뷰에 **포함하지 않는다.**
- **`--staged`**: `git diff --cached` (staged diff).
- **`--file <path>`**: 지정 파일을 `origin/main...HEAD` 기준과 비교한 diff. 커밋 전 파일을
  보려면 `--staged`와 함께 쓴다.

**자동 호출 방지**: 원칙이 "Codex 1차 후 필요시 Claude 2차"이므로 SKILL frontmatter에
`disable-model-invocation: true`를 넣어 **수동 호출 전용**으로 만들고, `argument-hint`로 호출
계약(`[--staged] [--file <path>]`)을 노출한다.

심각도 순으로 `[required]`/`[recommended]`/`[note]` 태그로 보고. 리뷰 렌즈: 하이브리드 구조
역할 분리 준수, Python 코드 품질, 타입 힌트, 테스트 커버리지, 비밀값 노출.

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

push 직전 게이트. Check 1: `uv run --locked` 로 `ruff check` + `ruff format --check` +
`pyright` + `pytest` 통과 여부(hook이 자동화하는 방어선과 동일). Check 2: docs 동기화
여부(자동화 불가, 스킬로 판단).

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
