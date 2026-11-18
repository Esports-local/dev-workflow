---
name: local-up
description: esports 세 레포(esports-ui · esports-api · esports-analyzer)와 MinIO 를 docker compose 로 한 번에 띄우고 /health 로 확인한다. 끄기 · 다시 띄우기 · 로그 보기 · 상태 확인도 여기서 한다. esports 레포에서 "로컬에서 띄워 줘", "서버 켜 줘", "api 띄워 줘", "화면 확인하게 올려 줘", "다 꺼 줘", "analyzer 로그 보여 줘" 처럼 로컬 개발 서버를 올리거나 내릴 때 사용한다. 분석 작업이 실패 · 멈춘 원인을 찾는 데는 쓰지 않는다.
---

# local-up — ui · api · analyzer · MinIO 한 번에 띄우기

실제 일은 이 플러그인의 `scripts/dev.sh`(`${CLAUDE_PLUGIN_ROOT}/scripts/dev.sh`, 아래 `$S`)가 한다. compose 명령을 스크립트 밖에서 다시 짜지 않는다.

구성은 `esports-api/docker-compose.yml` 하나다. 세 레포가 나란히 있는 폴더(또는 그 안의 레포)에서 부른다. 다른 곳이면 `ESPORTS_ROOT=<세 레포가 있는 폴더>`를 준다.

| 서비스 | 주소 |
|---|---|
| ui | http://localhost:8000 |
| api | http://localhost:8002 (문서 `/api/docs/`) |
| analyzer | http://localhost:8001 |
| MinIO | http://localhost:9000 (콘솔 :9001) |

## 순서

1. **띄우기.** `bash $S up` — Docker(OrbStack)가 꺼져 있으면 켜고, `esports-api/.env`가 없으면 `.env.example`을 복사하고, 빌드 후 백그라운드로 띄운 뒤 analyzer · api · ui 응답을 기다린다.
2. **확인.** `curl -s localhost:8002/health` 가 200 인지 본다. 503 이면 db · storage 중 무엇이 실패인지 응답에 나온다.
3. **안내.** 화면 주소, api 문서 주소, 끄는 법을 알려 준다.

## 다른 명령

- `bash $S down` 전부 끔 (데이터 볼륨은 유지)
- `bash $S restart` 다시 띄움
- `bash $S logs [서비스]` 로그 따라보기 (`api` · `ui` · `analyzer` · `minio`)
- `bash $S ps` 상태

## 알아 둘 것

- 코드는 볼륨으로 붙어 있어 고치면 자동으로 다시 읽는다. `requirements.txt`나 Dockerfile 을 바꿨을 때만 다시 빌드한다 (`up`이 `--build`로 한다).
- `esports-analyzer/.env`가 없으면 번역(`GEMINI_API_KEY`)만 안 된다. 나머지는 뜬다.
- 분석할 영상 폴더는 `esports-api/.env`의 `VIDEOS_DIR`이다 (analyzer 컨테이너 `/data`).
- analyzer 컨테이너를 다시 띄우면 진행 중이던 분석 작업 기록이 사라진다. 분석 중이면 `restart` 전에 사용자에게 확인한다.

## 하지 않는 것

- `docker compose down -v` (DB · MinIO 데이터가 지워진다). 사용자가 명시적으로 요청할 때만.
- `.env`에 비밀값을 대신 적기.
- 로컬 가상환경(venv)으로 띄우기. 이 프로젝트는 Docker 로만 띄운다.
