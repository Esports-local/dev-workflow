---
name: import-game
description: analyzer 분석 결과 폴더(경기 정보 header 포함)를 esports-api DB 에 넣는다 — 경기 정보가 없으면 analyzer header 단계(op.gg 번호 · 세트)를 먼저 돌리고, 관리 명령 import_outputs(결과 폴더 이름, --match · --set · --lang · --title). "이 경기 등록해 줘", "op.gg 32706 가져와", "분석 결과를 api 에 넣어 줘", "CLI 로 돌린 결과가 화면에 안 보여" 처럼 경기나 세트 영상을 DB 에 등록할 때 사용한다. 분석 자체를 돌리는 데는 쓰지 않는다.
---

# import-game — 세트 영상과 경기 정보 등록

api 는 op.gg 를 부르지 않는다. 경기 정보(header)는 analyzer 가 만들고, api 는 결과 폴더를 그대로 저장한다. compose 가 떠 있어야 한다 (안 떠 있으면 local-up).

명령은 `esports-api` 폴더에서 실행한다.

## 순서

1. **결과 폴더 확인.** `../esports-analyzer/outputs/<이름>/work/`에 단계 결과(`header.json`, `preprocess.json`, `roi_ocr.json`, `asr.json` …)가 있는지 본다. 이름은 영상 파일 이름에서 확장자를 뺀 것이다. 분석 결과가 없으면 analyzer 로 먼저 분석한다 (실행 방법은 analyzer `CLAUDE.md`).
2. **경기 정보(header)가 없으면 만든다.** op.gg 경기 번호와 세트로 analyzer `header` 단계만 돌린다 (영상은 읽지 않아 1~2초).
   ```
   docker compose exec analyzer python -m pipeline /data/<영상 파일> --only header --set header.match=32706 --set header.set=2
   ```
3. **세트 영상 등록.**
   ```
   docker compose exec api python manage.py import_outputs words_test_1080 --title "BLG vs T1 2세트 (한국어)" --lang ko --match 32706 --set 2
   ```
   - `--lang`: 해설 언어 `ko` | `en` (기본 `ko`)
   - `--match`·`--set`: op.gg 경기 번호와 세트. 기록만 해 두고, 화면에서 "경기 정보" 단계를 다시 돌릴 때 analyzer 에 넘긴다
   - `--title`을 안 주면 폴더 이름을 쓴다
   - `--video`: 원본 영상 파일 이름 (예: `lck_final_1080.mp4`). 구간만 분석한 폴더(`<영상>_900-1500s`)처럼 폴더 이름으로 원본을 알 수 없을 때 줘야 화면(컷 탭)에서 재생된다
   - 출력 끝의 "경기 정보 있음/없음"으로 header 가 들어갔는지 본다
4. **확인.** `curl -s localhost:8002/games/<id>` 의 `match` 에 팀·선수·챔피언이 보이는지, 화면 http://localhost:8000 에 나오는지 본다.

## 알아 둘 것

- 사용자가 경기 번호 · 세트 · 언어를 말하지 않았으면 묻는다. 짐작해서 넣지 않는다.
- op.gg 코드는 analyzer `pipeline/opgg.py`에만 둔다 (경기 페이지 안 JSON + 세트 정보용 `/matches/graphql`의 `GetGameByMatch` 하나). api 에 op.gg 를 부르는 코드를 넣지 않는다.
- 화면의 "영상 추가"(`POST /games`)는 경기·세트를 주면 header 부터 분석까지 한 번에 한다. 이 스킬은 CLI 로 이미 만든 결과를 넣을 때 쓴다.
- 같은 결과 폴더를 다시 넣으면 그 영상의 분석 결과가 새로 덮인다. 대사 검수(LineReview)는 따로 저장돼 남는다. header.json 에 error 가 있으면(op.gg 실패) 이전 경기 정보를 둔다.
