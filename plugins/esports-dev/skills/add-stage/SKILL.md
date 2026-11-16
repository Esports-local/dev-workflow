---
name: add-stage
description: esports-analyzer 파이프라인에 분석 단계를 새로 만들거나 예정 단계(evaluate 등)를 구현한다 — pipeline/<단계>.py 의 run · DEFAULT_PARAMS, schema.py 결과 모델, stages.py 등록, 그리고 그 결과를 api(matches/outputs.py)와 ui(단계 이름표 · 원본 JSON 탭)에 잇는다. "evaluate 단계 구현해 줘", "챔피언 OCR 단계 추가", "새 분석 단계 만들자" 처럼 파이프라인 단계 자체를 만들 때 사용한다. 이미 있는 단계를 실행만 하는 것은 pipeline-run, HTTP 엔드포인트 추가는 add-endpoint.
---

# add-stage — 파이프라인 단계 추가

단계는 `run(video, work_dir, params)` 함수 하나를 가진 모듈이고, 결과를 `outputs/<영상명>/work/<단계>.json`에 남긴다. 기존 단계(`pipeline/asr.py`, `pipeline/roi_ocr.py`)를 먼저 읽고 같은 모양으로 만든다.

## 순서

1. **자리 정하기.** `pipeline/stages.py`의 `STAGES` 순서에서 어디에 들어가는지, 어떤 앞 단계 결과를 읽는지 정한다. 순서가 바뀌면 사용자에게 확인한다.
2. **결과 모델.** `pipeline/schema.py`에 `<이름>Result`(pydantic)를 추가한다. 시간은 초(float)이고 구간은 `TimeSpan`(`start_sec` / `end_sec`)을 쓴다.
3. **단계 모듈.** `pipeline/<단계>.py`:
   - 맨 위에 `DEFAULT_PARAMS`(설정 이름과 기본값, 주석으로 뜻)
   - `run(video: Path, work_dir: Path, params: dict | None = None) -> Path`
   - 앞 단계 결과는 `pipeline/io.py`의 `load_stage`로 읽고, 결과는 `save_stage(work_dir, "<단계>", result, p)`로 저장한다 (`schema_version`이 같이 붙는다)
   - 함수마다 독스트링(Args · Returns · Raises)
4. **등록.** `pipeline/stages.py`의 `STAGES`(없으면 추가)와 `IMPLEMENTED`에 넣는다.
5. **실행 확인.** 짧은 영상으로 그 단계만 돌린다.
   ```
   docker compose exec analyzer python -m pipeline /data/<영상 파일> --only <단계>
   ```
6. **api 에 잇기 (`esports-api`).** `matches/outputs.py`의 `STAGES`에 있어야 결과가 DB 에 저장된다. 결과를 가공해서 주는 엔드포인트가 필요하면 add-endpoint 로 만든다.
7. **ui 에 잇기 (`esports-ui`).** `web/views.py`의 `STAGE_LABELS`(단계 한글 이름), 원본 JSON 탭에 보이려면 `JSON_STAGES`, 영상 추가 · 재실행 화면에서 고를 수 있게 하려면 `RUN_STAGES`.
8. **문서.** PLAN 4장 단계 표, analyzer `CLAUDE.md` 구조, CHECKLIST 는 docs-sync 로 맞춘다.

## 알아 둘 것

- 새 패키지 · 모델이 필요하면 `requirements.txt`에 `==`로 고정하고 이미지를 다시 빌드한다. 로컬 가상환경을 만들지 않는다.
- 외부 API 를 부르는 단계는 translate 처럼 `dry_run`과 기존 결과 재사용을 넣어 비용을 미리 볼 수 있게 한다.
- 화면 좌표를 쓰면 1920x1080 기준으로 맞춘다.
- `work/`는 중간 결과라 외부에 공개하지 않는다. 재생용 파일만 `hls/`에 둔다.
- 결과 모양을 바꾸는 변경은 그 결과를 읽는 api(`matches/outputs.py`, `matches/screen.py`)와 ui 까지 같이 본다.
