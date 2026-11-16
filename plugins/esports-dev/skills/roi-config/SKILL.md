---
name: roi-config
description: 중계 화면 고정 영역 OCR 설정(esports-analyzer configs/<종목>/<레이아웃>.json)을 새로 만들거나 고친다 — 영역 좌표(ocr_roi), 허용 문자, 검증 정규식 · 신뢰도, 사전(lexicon), static 을 정하고 프레임을 잘라 눈으로 확인한 뒤 짧게 돌려 본다. "새 대회 화면 레이아웃 추가", "LCK 화면용 ROI 만들어 줘", "킬 수 영역 좌표가 어긋나", "팀 이름이 잘못 읽혀" 처럼 OCR 영역 설정을 다룰 때 사용한다. 이미 있는 설정으로 OCR 을 돌리기만 하는 것은 pipeline-run.
---

# roi-config — 중계 화면 OCR 영역 설정

설정 파일은 영역 목록(JSON 배열)이다. 기준 예시는 `esports-analyzer/configs/lol/worlds_2026.json`(35개 영역). 새 레이아웃은 이 파일을 복사해서 시작한다.

## 영역 하나의 항목

| 항목 | 뜻 |
|---|---|
| `tag`, `slot` | 무엇(`team`, `kills`, `player`, `kda`, `game_time` …)과 어느 자리(`blue`, `red`, 포지션 …) |
| `kind` | 값 종류 (`team`, `time` …) |
| `ocr_roi` | `[x1, y1, x2, y2]` **1920x1080 기준** 픽셀 |
| `ocr_scale` | 읽기 전에 키우는 배율 (작은 글자는 3) |
| `allowed_text` | 나올 수 있는 문자 |
| `validation` | `regex`, `min_confidence`. 통과 못 하면 버린다 (리플레이 · 캠 화면이 여기서 걸러진다) |
| `lexicon` | 사전 이름. `configs/<종목>/lexicon/<이름>.json`의 가장 가까운 정식 이름으로 고친다 |
| `static` | 경기 중 안 바뀌는 값 (팀 이름 등). 같은 값이 5초 이어지면 확정하고 더 안 읽는다 |

## 순서

1. **기준 프레임 뽑기.** 경기 화면(리플레이 · 캠 아님) 시점을 골라 1920x1080 으로 한 장 뽑는다. 뽑은 그림은 레포 밖 임시 폴더에 둔다.
   ```
   ffmpeg -ss <초> -i <영상> -frames:v 1 -vf scale=1920:1080 frame.png
   ```
2. **좌표 정하기.** 그림을 보고 영역마다 `ocr_roi`를 잡는다. 글자에 딱 붙이지 말고 몇 픽셀 여유를 둔다.
3. **눈으로 확인.** 영역을 잘라서 글자가 온전히 들어오는지 본다. 좌우 진영처럼 대칭인 영역은 양쪽 다 확인한다.
   ```
   ffmpeg -i frame.png -vf crop=<x2-x1>:<y2-y1>:<x1>:<y1> roi_<tag>_<slot>.png
   ```
4. **설정 쓰기.** `configs/<종목>/<레이아웃>.json`에 영역을 적는다. 허용 문자와 정규식은 실제로 나올 수 있는 값에 맞춰 좁게 잡는다.
5. **짧게 돌려 보기.** 1~2분짜리 구간이나 짧은 영상으로 (`esports-api` 폴더, compose 가 떠 있을 때):
   ```
   docker compose exec analyzer python -m pipeline /data/<영상 파일> --only roi_ocr --set roi_ocr.layout=configs/<종목>/<레이아웃>.json
   ```
6. **결과 확인.** `outputs/<영상명>/work/roi_ocr.json`의 `runs[]`에서 영역마다 값이 읽혔는지, 리플레이 구간에서 값이 통과하지 않았는지 본다. 안 읽힌 영역 · 잘못 읽힌 값을 표로 보여 준다.

## 알아 둘 것

- 좌표는 항상 1920x1080 기준이다. 화면 녹화본처럼 중계 화면이 영상의 일부면 좌표를 바꾸지 말고 `--set 'roi_ocr.crop=[x,y,w,h]'`(원본 픽셀)로 그 부분만 잘라 쓴다.
- 팀 로고 글꼴처럼 글자가 계속 틀리게 읽히면 좌표가 아니라 사전으로 고친다 (`lexicon/teams.json` 참고).
- 기본 레이아웃은 `pipeline/roi_ocr.py`의 `DEFAULT_PARAMS["layout"]`이다. 새 레이아웃을 기본으로 바꾸는 것은 사용자에게 확인한 뒤에 한다.
- 템플릿 매칭으로 화면 종류를 판별하는 방식은 쓰지 않는다 (`docs/PLAN.md` 4장).
- 프레임 그림 · 영상은 git 에 넣지 않는다.
