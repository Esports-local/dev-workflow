---
name: pipeline-run
description: esports-analyzer 분석 파이프라인(preprocess · segments · hls · roi_ocr · asr)을 CLI 로 실행한다 — 단계를 골라 실행(--only), 구간 실행(--from · --until), 중간부터 재실행, 단계 설정 덮어쓰기(--set 단계.키=값). "이 영상 분석 돌려 줘", "asr 만 다시 돌려", "roi_ocr 부터 다시", "sample_fps 바꿔서 돌려 봐" 처럼 영상에 분석 단계를 실행할 때 사용한다. 번역(translate) 단계는 비용 확인이 필요하므로 translate-run 을 쓴다.
---

# pipeline-run — 분석 단계 실행

단계 순서: `preprocess → segments → hls → roi_ocr → asr → translate → evaluate(예정)`. 각 단계는 `esports-analyzer/outputs/<영상명>/work/<단계>.json`을 남기므로 앞 단계 결과가 있으면 중간부터 다시 돌릴 수 있다.

## 순서

1. **영상 확인.** 영상은 `esports-api/.env`의 `VIDEOS_DIR` 폴더에 있어야 한다 (컨테이너 안 `/data`). 화면 좌표가 1920x1080 기준이므로 그 해상도가 아니면 먼저 알린다.
2. **실행.** compose 가 떠 있으면 `esports-api` 폴더에서:
   ```
   docker compose exec analyzer python -m pipeline /data/<영상 파일> --only preprocess,roi_ocr,asr
   ```
   compose 없이 돌리려면 `esports-analyzer` 폴더에서:
   ```
   docker run --rm -v "$PWD":/app -v <영상 폴더>:/data esports-analyzer python -m pipeline /data/<영상 파일> --only preprocess,roi_ocr,asr
   ```
3. **결과 확인.** `outputs/<영상명>/work/`의 단계 결과와 `run_log.json`(단계별 시간 · 메모리)을 보고 요약한다.
4. **화면에 반영.** CLI 로 돌린 결과는 api DB 에 자동으로 안 들어간다. 화면에서 봐야 하면 import-game 으로 넣는다.

## 옵션

| 옵션 | 뜻 |
|---|---|
| `--only a,b` | 고른 단계만 (실행 순서는 항상 단계 순서) |
| `--from a --until b` | a 부터 b 까지 (포함) |
| `--set 단계.키=값` | 단계 설정 덮어쓰기. 값은 JSON 으로 읽고 안 되면 문자열. 여러 번 쓸 수 있다 |
| `--work-dir` | 결과 폴더 (기본 `outputs`) |

`--set` 예: `--set roi_ocr.sample_fps=1`, `--set 'roi_ocr.crop=[0,60,1920,1080]'`. 단계별 설정 이름과 기본값은 `pipeline/<단계>.py`의 `DEFAULT_PARAMS`에서 확인한다.

## 알아 둘 것

- 17분 영상 기준 roi_ocr 약 12분, asr(small) 약 13분 걸린다 (CPU). 오래 걸리는 실행은 백그라운드로 돌린다.
- 뒤 단계는 앞 단계 결과 파일이 있어야 한다 (예: asr 은 preprocess 의 wav). 없으면 `FileNotFoundError`가 난다.
- analyzer HTTP 작업(`/jobs`)이 같은 영상을 분석 중일 때 CLI 로 같은 영상을 돌리지 않는다. 결과 파일이 섞인다.
- 화면의 "분석 다시 실행"(api `POST /games/{id}/analyze`)도 같은 파이프라인을 부른다. 사용자가 화면에서 하겠다고 하면 그쪽을 안내한다.
- 영상과 결과(`*.mp4`, `outputs/`)는 git 에 넣지 않는다.
