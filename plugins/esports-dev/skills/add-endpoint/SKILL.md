---
name: add-endpoint
description: esports 레포에 HTTP 엔드포인트를 새로 만들거나 요청 · 응답 모양을 바꿀 때 같이 고쳐야 하는 곳을 순서대로 처리한다 — 스펙 확인, esports-api 의 api/<엔드포인트>/ 폴더 · urls · tests, esports-ui 의 web/api_client.py · 화면, analyzer 의 api/<엔드포인트>/schemas.py 와 api 쪽 analyzer.py. "엔드포인트 추가해 줘", "/cases API 만들어 줘", "응답에 필드 하나 더 넣어 줘", "analyzer /jobs 요청 모양 바꿔" 처럼 레포 사이 연동 스펙이 바뀌는 작업에 사용한다. 화면만 고치는 작업이나 파이프라인 단계 추가(add-stage)에는 쓰지 않는다.
---

# add-endpoint — 엔드포인트 추가 · 변경

연동은 두 구간이고 기준 문서가 다르다.

| 구간 | 스펙 기준 |
|---|---|
| ui → api | 구글 스펙 시트 `spec_sheet_lol` (링크는 `esports-analyzer/docs/PLAN.md` 6장) |
| api → analyzer | `esports-analyzer/api/<엔드포인트>/schemas.py` + PLAN 6장 |

## 순서

1. **스펙 확인.** ui → api 호출이면 스펙 시트에 그 번호가 있는지 확인한다. 시트에 없거나 시트와 다르게 만들어야 하면 **먼저 사용자에게 확인**한다. 스펙 내용을 `CLAUDE.md`에 복사하지 않는다.
2. **api (`esports-api`).**
   - `api/<엔드포인트>/views.py`를 만든다. 그 엔드포인트만 쓰는 코드는 같은 폴더에 둔다. 모델은 앱(`matches` 등)에 두고, 앱과 관리 명령은 `api`를 import 하지 않는다.
   - `config/urls.py`에 경로를 추가한다.
   - GET / POST 만 쓴다. 에러는 `api/exceptions.py`의 `ApiError` · `get_or_404`로 `{"error": "메시지"}` 형식.
   - `@extend_schema(tags=[...], summary="<스펙 번호>. <설명>")`를 붙인다.
   - 시간 필드는 초(float), 이름은 `start_sec` / `end_sec`.
3. **테스트.** `tests/test_<이름>.py`에 정상 · 에러 경우를 넣고 돌린다.
   ```
   docker compose exec api python manage.py test
   ```
   모델을 바꿨으면 `docker compose exec api python manage.py makemigrations` 후 마이그레이션 파일도 같이 커밋한다.
4. **ui (`esports-ui`).** 호출 함수는 `web/api_client.py`에만 추가한다 (docstring 에 `GET /경로`). 뷰는 `web/views.py`, 템플릿은 `web/templates/web/`. ui 에 목업 JSON 을 두지 않는다.
5. **analyzer 스펙이 바뀌는 경우.** `esports-analyzer/api/<엔드포인트>/schemas.py`(요청 · 응답 모양)와 `views.py`를 고치고, 새 엔드포인트면 `app/main.py`에 router 를 추가한다. 그리고 **같은 작업에서** `esports-api/api/games/analyzer.py`의 호출과 PLAN 6장을 맞춘다.
6. **직접 불러 보기.** `curl -s localhost:8002/<경로>` 와 화면에서 확인한다 (안 떠 있으면 local-up).
7. **문서.** README · `CLAUDE.md` · PLAN 6장 반영은 docs-sync 로 한다. 스펙 시트 갱신은 사용자와 정한 사람이 한다.

## 알아 둘 것

- 커밋은 레포마다 따로, `feat/내용 (스펙 N번)` 한 줄. 문서는 `docs/` 커밋으로 나눈다.
- PUT · PATCH · DELETE 를 쓰지 않는다. 지우거나 고치는 동작도 POST 로 만든다.
- 새 패키지가 필요하면 `requirements.txt`에 `==`로 고정하고 이미지를 다시 빌드한다.
