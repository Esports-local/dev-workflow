---
name: docs-sync
description: esports 레포에서 기능을 만들거나 고친 뒤 문서를 코드에 맞춘다 — 각 레포 README.md · CLAUDE.md, 기준 문서 esports-analyzer/docs/PLAN.md(단계 상태 · 연동 · 결과 · 다음 할 일), analyzer CHECKLIST.md 중 바뀐 내용이 걸리는 곳만 찾아 고치고 docs/ 커밋으로 나눈다. "문서 반영해 줘", "README 업데이트", "PLAN 에 지금 상태 반영", "CLAUDE.md 가 코드랑 안 맞아" 처럼 구현 뒤 문서를 정리할 때 사용한다. 새 문서를 쓰거나 보고서 · 발표 자료를 만드는 데는 쓰지 않는다.
---

# docs-sync — 구현 뒤 문서 맞추기

문서마다 역할이 다르다. 같은 내용을 여러 곳에 복사하지 않고, 기준 문서 한 곳에 적고 나머지는 가리킨다.

| 문서 | 담는 것 |
|---|---|
| `esports-analyzer/docs/PLAN.md` | 세 레포의 기준. 목표 · 실험 조건 · 단계 상태(4장) · 레포 연동(6장) · 지금까지 결과(7장) · 미정(8장) · 다음 할 일(9장) |
| 각 레포 `CLAUDE.md` | 그 레포의 역할 · 구조(파일이 하는 일) · 실행 명령 · 규칙. Claude 가 작업 전에 읽는 내용 |
| 각 레포 `README.md` | 사람이 읽는 소개 · 실행 방법 · 엔드포인트 / 화면 표 |
| `esports-analyzer/CHECKLIST.md` | analyzer 작업 체크리스트 |

## 순서

1. **바뀐 것 파악.** 세 레포에서 마지막 `docs/` 커밋 이후의 변경을 본다 (`git log --oneline`, `git diff <그 커밋>..HEAD --stat`). 커밋 전 변경도 포함한다.
2. **걸리는 문서 찾기.** 바뀐 파일 이름 · 엔드포인트 · 단계 · 명령 · 포트를 문서에서 검색해, 틀려졌거나 빠진 곳만 고른다.
   - 엔드포인트 추가 · 변경 → 그 레포 README 표, `CLAUDE.md` 구조, PLAN 6장
   - 파이프라인 단계 · 설정 → analyzer `CLAUDE.md`, PLAN 4장 표, CHECKLIST
   - 화면 추가 → ui `CLAUDE.md` 화면 목록, README
   - 실행 방법 · 포트 · 환경변수 → 세 레포 README · `CLAUDE.md` 전부
   - 실험 결과 → PLAN 7장. 끝난 일 → PLAN 9장 · CHECKLIST 체크
3. **고치기.** 지금 코드가 하는 일만 적는다. 고친 과정이나 이전 상태는 적지 않는다.
4. **보여 주기.** 문서별로 무엇을 고쳤는지 요약한다.
5. **커밋 (요청받았을 때).** 레포마다 따로, 코드 커밋과 나눠서 `docs/내용` 한 줄. 예: `docs/README·PLAN 에 사람이 고친 대사로 번역 반영`.

## 알아 둘 것

- ui → api 스펙(요청 · 응답 필드)은 스펙 시트가 기준이다. 문서에는 번호와 경로만 적고 필드를 복사하지 않는다.
- 문서 파일(md)은 `docs/`에 둔다 (`README.md` · `CLAUDE.md` · `CHECKLIST.md` 제외).
- 아직 안 만든 것은 "예정"으로, 만들었지만 전체 실행을 안 한 것은 그렇게 적는다. 확인하지 않은 수치를 적지 않는다.
- 커밋 작성자는 사용자 본인만. `Co-Authored-By`를 넣지 않는다.
