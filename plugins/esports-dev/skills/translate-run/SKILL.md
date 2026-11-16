---
name: translate-run
description: 중계 대사 영어 번역(translate 단계, Gemini)을 실행한다 — 조건 A(대사만) · B(+화면 OCR) · C(+영상 프레임)를 고르고, dry_run 으로 입력 토큰을 먼저 세어 비용을 확인한 뒤 실제 번역을 돌린다. "번역 돌려 줘", "조건 C 까지 번역해", "번역 비용 얼마나 나와?", "20줄만 번역해 봐", "고친 대사로 다시 번역" 처럼 번역을 실행하거나 예상 토큰을 볼 때 사용한다. 번역이 아닌 분석 단계(asr · roi_ocr 등) 실행은 pipeline-run.
---

# translate-run — 조건 고르기 → 예상 토큰 → 번역

번역은 Gemini API 비용이 든다. **반드시 dry_run 으로 토큰을 먼저 보여 주고 사용자가 확인한 뒤 실제 실행한다.**

필요한 것: `esports-analyzer/.env`의 `GEMINI_API_KEY`, 그 영상의 `work/asr.json`(조건 B 는 `roi_ocr.json`도). 없으면 pipeline-run 으로 먼저 만든다.

## 순서

1. **조건 정하기.** 기본은 A · B. C 는 프레임을 보내서 토큰이 훨씬 많다. 사용자가 조건을 말하지 않았으면 A · B 로 예상부터 보여 준다.
2. **예상 (무료).** `esports-api` 폴더에서:
   ```
   docker compose exec analyzer python -m pipeline /data/<영상 파일> --only translate \
     --set translate.dry_run=true --set 'translate.conditions=["A","B"]'
   ```
   로그의 `translate (dry_run): 대사 N줄, 입력 토큰 {...}`를 조건별로 정리해 보여 준다. 출력 토큰은 실행 전에는 알 수 없다고 같이 말한다.
3. **확인받기.** 대사 수 · 조건별 입력 토큰을 보여 주고 진행할지 묻는다.
4. **실행.** 같은 명령에서 `translate.dry_run=true`만 뺀다. 오래 걸리면 백그라운드로 돌린다.
5. **결과.** `outputs/<영상명>/work/translate.json`의 `usage`(요청 수, 입력 · 출력 토큰)를 알려 준다. 화면에서 보려면 import-game 으로 다시 넣는다.

## 자주 쓰는 설정 (`--set translate.키=값`)

| 키 | 기본 | 뜻 |
|---|---|---|
| `conditions` | `["A","B"]` | 번역 조건 |
| `max_lines` | 전부 | 앞에서부터 이만큼만 (작게 시험할 때) |
| `line_ids` | 전부 | 이 대사만 |
| `reuse` | `true` | 이미 번역된 대사 · 조건은 다시 요청하지 않음 |
| `dry_run` | `false` | 입력 토큰만 세고 끝 (저장 안 함) |

## 화면 · api 로 할 때

- 화면 세트 페이지에서 조건을 고르면 예상 비용이 나오고 "번역 시작"으로 실행된다.
- api: `GET /games/{id}/estimate?conditions=A,B` (20줄 표본으로 계산) → `POST /games/{id}/analyze` 에 `{"stages": ["translate"], "conditions": ["A","B"]}`.
- api 로 실행하면 사람이 고친 대사(대사 검수)가 `text_overrides`로 넘어가 고친 문장으로 번역된다. CLI 로 돌리면 ASR 원문으로 번역되므로, 검수한 영상은 api 쪽으로 돌린다.

## 하지 않는 것

- 예상을 건너뛰고 바로 전체 번역.
- 조건마다 모델 · 프롬프트 · 온도를 다르게 주기 (실험 조건이 깨진다, `docs/PLAN.md` 2장).
- `reuse=false`로 전부 다시 번역 (사용자가 요청할 때만. 비용이 처음부터 다시 든다).
- `GEMINI_API_KEY` 값을 출력하기.
