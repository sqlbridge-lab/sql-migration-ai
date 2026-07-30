---
name: write-issue-pr
description: Use when writing GitHub issue or PR body text for this project — before drafting the description for an issue, feature/bug/chore ticket, or pull request.
---

# write-issue-pr

이슈·PR 본문을 작성할 때 쓴다. **읽는 사람은 사람이다** — AI가 아니라 사람이 훑어보고 바로
이해할 수 있게 쓴다.

## 핵심 규칙

1. **사람이 읽는 글로 쓴다.** 훑어서 바로 이해되게. 스펙 문서를 그대로 옮기지 않는다.
2. **어려운 말·전문용어를 뺀다.** 꼭 필요한 게 아니면 쉬운 말로.
3. **괄호 안 근거 설명을 뺀다.** `(순환 의존 회피)`, `(fail-open 방지)` 같은 "왜 그런지"
   해설은 넣지 않는다. 무엇을 하는지만 적는다.
4. **딱 필요한 말만, 흐름만.** 상세 근거·트레이드오프는 스펙 문서에 있으니 링크로 대신한다.

## 제목

본문과 함께 **제목도 항상 준다.** 짧고 사람이 바로 알아보게. 커밋 규칙과 결을 맞춘다:
`{purpose}: {무엇}` (purpose 예: feat, fix, chore, docs). 예:
- `feat: MySQL·PostgreSQL 도커 세팅`
- `fix: 페이징 쿼리 변환 오류`

## 이슈 본문 형식

프로젝트 이슈 템플릿(`.github/ISSUE_TEMPLATE/`)을 따른다. 흐름:

```markdown
## 목적
무엇을, 왜. 한두 줄.

## 관련 스펙
docs/superpowers/specs/... (있으면)

## 작업 항목
- [ ] 할 일 (짧게, 흐름만)

## 완료 기준
- 사람이 확인할 수 있는 결과 (돌려보면 되는 것)
```

## PR 본문 형식

프로젝트 PR 템플릿(`.github/pull_request_template.md`)을 따른다. 요약·관련 이슈·변경 내용·
확인 체크리스트. 변경 내용은 "무엇을 바꿨는지"만, 근거 해설은 넣지 않는다.

## 좋은 예 / 나쁜 예

나쁜 예 (근거 해설·전문용어):
```markdown
- [ ] PostgreSQL 시드 — 변환 엔진 산출물이 아니라 Validator가 신뢰하는 독립 fixture
      (순환 의존 회피). MySQL 시드와 논리적으로 동일한 데이터셋을 손으로 관리한다.
```

좋은 예 (흐름만):
```markdown
- [ ] PostgreSQL 스키마 + 시드 — MySQL과 같은 데이터
```

## 하지 않는 것

- 이슈·PR을 직접 생성하지 않는다. **본문만 작성**해 전달하고, 생성은 사용자가 한다.
