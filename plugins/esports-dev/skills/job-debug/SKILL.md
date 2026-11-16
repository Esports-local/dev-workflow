---
name: job-debug
description: esports 분석 작업이 실패했거나 멈췄거나 끝나지 않을 때 원인을 찾는다 — api 의 세트 영상 상태, analyzer GET /jobs/{id}, outputs/<영상명>/work/job.log · run_log.json, 컨테이너 로그를 순서대로 보고 어느 단계에서 왜 멈췄는지 정리한다. "분석이 안 끝나", "분석 실패했어 왜 그래", "화면에 계속 분석 중으로 나와", "409 이미 분석 중이래", "작업 취소가 안 돼" 처럼 분석 작업 문제를 볼 때 사용한다. 서버 자체가 안 뜨는 문제는 local-up, 단계를 다시 실행하는 것은 pipeline-run.
---

# job-debug — 분석 작업 문제 확인

흐름: ui → api `POST /games`·`/analyze` → analyzer `POST /jobs` → 파이프라인 프로세스. api 는 세트 영상을 **조회할 때** analyzer 에 상태를 묻고, 끝났으면 결과 폴더를 읽어 저장한다.

## 순서 (읽기만 한다)

1. **api 상태.** `curl -s localhost:8002/games/<id>` — 분석 상태, `job_id`(`game<id>-<시각>`), `error`, 단계별 상태를 본다.
2. **analyzer 상태.** `curl -s localhost:8001/jobs/<job_id>` — `status`(`queued` · `running` · `succeeded` · `failed` · `cancelled`), `current_stage`, `elapsed_sec`, `error`.
   - 404 면 analyzer 가 그 작업을 모른다 → 아래 "analyzer 를 다시 띄운 경우".
3. **작업 로그.** `esports-analyzer/outputs/<영상명>/work/job.log`의 끝부분을 본다. 파이썬 예외가 있으면 그 줄과 단계를 찾는다.
4. **단계별 시간.** 같은 폴더 `run_log.json`으로 어느 단계까지 끝났고 얼마나 걸렸는지 본다. 오래 걸릴 뿐인지(17분 영상에 roi_ocr 약 12분, asr 약 13분) 멈춘 건지 구분한다.
5. **컨테이너 로그.** 위에서 안 나오면 `esports-api` 폴더에서 `docker compose logs --tail=100 analyzer` (api 쪽 문제면 `api`).
6. **정리.** 어느 단계에서 무엇 때문에 멈췄는지, 고칠 방법 후보를 알려 준다. 재실행 · 취소 · 코드 수정은 사용자가 정한 뒤에 한다.

## 자주 나오는 경우

| 증상 | 원인 | 처리 |
|---|---|---|
| 화면이 계속 "분석 중", analyzer 는 404 | analyzer 컨테이너를 다시 띄워 작업 기록이 사라짐 (큐가 없다) | 세트 화면을 다시 열면 api 가 상태를 갱신한다. 그 뒤 다시 실행 |
| `409 이미 분석 중입니다` | 그 영상이 api 에서 running | 끝나길 기다리거나 `POST /games/<id>/cancel` |
| analyzer `409` | 같은 `job_id`를 다른 내용으로 보냄, 또는 다른 작업이 실행 중 (한 번에 하나) | 실행 중인 작업을 먼저 확인 |
| `분석할 수 없는 영상입니다` | 1920x1080 이 아니거나 소리가 없음 (`GET /sources`의 `reason`) | 영상을 다시 준비 |
| `원본 영상을 찾을 수 없습니다` | `VIDEOS_DIR`에 그 파일이 없음 | `esports-api/.env`의 `VIDEOS_DIR` 확인 |
| translate 에서 실패 | `GEMINI_API_KEY` 없음, 앞 단계 결과(`asr.json`) 없음, API 오류 | `esports-analyzer/.env` 유무와 `job.log` 확인 |
| `FileNotFoundError` | 앞 단계 결과 파일 없음 | 앞 단계부터 다시 실행 (pipeline-run) |

## 알아 둘 것

- 취소는 `POST localhost:8002/games/<id>/cancel`(api 경유)로 한다. analyzer 는 프로세스 그룹째 멈춘다 (ffmpeg 포함).
- analyzer 는 끝난 작업 기록을 최근 100개만 갖고 있다.
- 조사 중에 컨테이너를 다시 띄우지 않는다. 실행 중인 다른 작업 기록까지 사라진다.
- `.env` 내용이나 키 값을 출력하지 않는다. 있는지 없는지만 확인한다.
