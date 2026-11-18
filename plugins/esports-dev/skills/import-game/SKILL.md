---
name: import-game
description: op.gg 경기 정보와 analyzer 분석 결과 폴더를 esports-api DB 에 넣는다 — 관리 명령 import_match(op.gg 번호 · 주소) 다음 import_outputs(결과 폴더 이름, --match · --set · --lang · --title) 순서. "이 경기 등록해 줘", "op.gg 32706 가져와", "분석 결과를 api 에 넣어 줘", "CLI 로 돌린 결과가 화면에 안 보여" 처럼 경기나 세트 영상을 DB 에 등록할 때 사용한다. 분석 자체를 돌리는 데는 쓰지 않는다.
---

# import-game — 경기 정보와 세트 영상 등록

`esports-api`의 관리 명령 두 개를 순서대로 돌린다. compose 가 떠 있어야 한다 (안 떠 있으면 local-up).

명령은 `esports-api` 폴더에서 실행한다.

## 순서

1. **경기 정보.** op.gg 경기 번호나 주소(`esports.op.gg/matches/<번호>`)로 가져온다. 여러 개를 한 번에 줄 수 있다.
   ```
   docker compose exec api python manage.py import_match 32706
   ```
2. **결과 폴더 확인.** `../esports-analyzer/outputs/<이름>/work/`에 단계 결과(`preprocess.json`, `roi_ocr.json`, `asr.json` …)가 있는지 본다. 이름은 영상 파일 이름에서 확장자를 뺀 것이다. 없으면 analyzer 로 먼저 분석한다 (실행 방법은 analyzer `CLAUDE.md`).
3. **세트 영상 등록.**
   ```
   docker compose exec api python manage.py import_outputs words_test_1080 --title "BLG vs T1 2세트 (한국어)" --lang ko --match 32706 --set 2
   ```
   - `--lang`: 해설 언어 `ko` | `en` (기본 `ko`)
   - `--match`: 1번에서 넣은 op.gg 경기 번호. `--set`: 세트 번호
   - `--title`을 안 주면 폴더 이름을 쓴다
4. **확인.** `curl -s localhost:8002/games` 에 영상이 보이는지, 화면 http://localhost:8000 목록에 나오는지 본다.

## 알아 둘 것

- 사용자가 경기 번호 · 세트 · 언어를 말하지 않았으면 묻는다. 짐작해서 넣지 않는다.
- 세트별 진영 · 선수 이름은 op.gg 페이지에 없다. `import_outputs`가 화면 OCR(roi_ocr) 결과의 팀 · 선수 이름으로 채운다. 그래서 roi_ocr 결과가 없으면 선수 칸이 빈다.
- op.gg 는 경기 페이지 안의 JSON 만 읽는다. op.gg 내부 API 를 부르는 코드를 넣지 않는다.
- 화면의 "영상 추가"(`POST /games`)는 등록과 분석을 한 번에 한다. 이 스킬은 CLI 로 이미 만든 결과를 넣거나, 경기 정보만 먼저 넣을 때 쓴다.
- 같은 결과 폴더를 다시 넣으면 그 영상의 분석 결과가 새로 덮인다. 대사 검수(LineReview)는 따로 저장돼 남는다.
