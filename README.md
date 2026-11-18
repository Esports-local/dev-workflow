# dev-workflow

Esports-local 조직(`esports-analyzer` · `esports-api` · `esports-ui`)의 Claude Code 플러그인과, 그 스킬의 선택 정확도를 평가 · 개선하는 도구.

## 설치

```
claude plugin marketplace add Esports-local/dev-workflow
claude plugin install esports-dev@esports
```

## 스킬 (`plugins/esports-dev/skills/`)

| 스킬 | 하는 일 |
|---|---|
| `local-up` | ui · api · analyzer · MinIO 를 한 번에 띄우고 끄기 |
| `import-game` | op.gg 경기 정보와 analyzer 결과 폴더를 api DB 에 등록 |
| `add-endpoint` | 엔드포인트 추가 · 변경 때 api · ui · analyzer 를 같이 고치기 |

커밋 규칙처럼 항상 적용되는 내용은 스킬이 아니라 각 레포 `CLAUDE.md`에 둔다.

## 구조

```
.claude-plugin/marketplace.json
plugins/esports-dev/
  .claude-plugin/plugin.json
  skills/<스킬>/SKILL.md
  scripts/dev.sh             # local-up 이 부르는 실행 스크립트
  hooks/usage.py             # 스킬 사용 기록 훅 (hooks.json)
evals/cases/<스킬>.jsonl     # 테스트 요청 (스킬마다 써야 하는 요청 6 + 쓰면 안 되는 요청 6, train 8 · val 4)
evals/run.py                 # 선택 평가
evals/worker.py              # 화면에서 요청한 평가를 맥에서 돌리는 실행기
evals/improve.py             # 설명 자동 개선 (실패 분석 → 수정안 → 전후 비교 → PR)
evals/history/<스킬>/        # 개선 시도 기록
evals/classify.py            # 유지 · 개선 · 비활성화 후보 분류
evals/policy.json            # 분류 기준값
evals/reports/               # 분류 보고서
POLICY.md                    # 분류 기준
evals/results/<묶음>.jsonl   # 평가 실행 기록
```

## 선택 평가

```
python3 evals/run.py                      # 전체 36개 × 2번, sonnet
python3 evals/run.py --split val --repeat 3
python3 evals/run.py --report evals/results/<파일>.jsonl
```

요청마다 `claude -p`를 해당 레포 폴더에서 실행해 첫 도구 호출만 읽고 끊는다. 첫 호출이 Skill 이 아니면 스킬을 안 쓴 것으로 본다.
첫 도구의 입력(Bash 명령, Read 파일 경로 등, 200자까지, 홈 경로는 `~`)을 `first_input`으로 남겨 스킬 대신 무엇을 하려 했는지 본다.
esports 세 레포는 이 레포와 같은 폴더에 나란히 있어야 한다 (다르면 `--root` 또는 `ESPORTS_ROOT`).

`--post http://localhost:8002` 를 주면 실행 기록을 esports-api 로 보내고, esports-ui 의 `/skills/` 화면에서 볼 수 있다.

## 화면에서 평가 실행

`/skills/` 화면의 "평가 실행"은 요청만 등록한다. api 는 Docker 안이라 `claude` 를 실행할 수 없어서, 맥에서 실행기를 켜 둬야 돈다.

```
python3 evals/worker.py        # 끄려면 Ctrl+C
python3 evals/worker.py --sync # 평가 없이 테스트 요청만 맞춤
```

화면에서 "틀린 요청만"을 고르면 그 요청 id 만 `run.py --case`로 돌린다. `improve.py --post <api>`는 시도 기록(`evals/history/<스킬>/log.jsonl`)도 보내 화면의 "설명 개선 시도"에 나온다 (기록만 보낼 때 `--post-history`).

화면에서 추가한 테스트 요청, "틀린 요청" 표에서 바꾼 정답·뺀 요청은 실행기가 `evals/cases/*.jsonl`에 반영한다 (커밋은 사람이).
결과 파일에는 돌릴 때의 정답이 남아 있지만 `run.py --report`, `improve.py`, `classify.py`는 지금 테스트 요청의 정답으로 다시 판정하고, 뺀 요청은 버린다.

## 사용 기록

플러그인을 설치하면 훅(`plugins/esports-dev/hooks/usage.py`)이 esports-dev 스킬 호출을 기록한다.

- 남기는 것: 스킬 이름, 부른 레포 폴더 이름, 성공 여부, 걸린 시간, 토큰 수, 도구 호출·오류 수, 시각. 요청 문장과 대화 내용은 남기지 않는다
- 성공 = 스킬을 부른 턴이 끝까지 감. 실패 = 끝나기 전에 끊기고 새 요청이 들어옴
- 항상 `~/.claude/esports-dev/usage.jsonl` 에 먼저 적고, esports-api 가 켜져 있으면 안 보낸 줄을 `POST /skills/usage` 로 보낸다 (주소는 `ESPORTS_API`, 기본 `http://localhost:8002`)
- 선택 평가가 띄운 세션은 기록하지 않는다

## 설명 자동 개선

```
python3 evals/improve.py --skill add-endpoint --propose-only   # 수정안만 받아 보기 (평가 안 돌림)
python3 evals/improve.py --skill add-endpoint                  # 최대 3번 시도, 채택되면 SKILL.md 를 고침
python3 evals/improve.py --skill add-endpoint --pr             # 채택되면 브랜치와 PR 까지
```

1. 개선용(train) 실패 요청과 지난 시도 기록을 `claude -p`에 주고 원인 가설 하나와 새 설명을 받는다
2. 플러그인 복사본에 적용해 개선용 요청을 1번씩 돌린다. 안 오르면 기각
3. 그 스킬의 요청 전부를 2번씩, 다른 스킬의 검증용(val) 요청을 1번씩 돌린다
4. 검증용 점수가 오르고 다른 스킬이 깨지지 않으면 채택한다. 기각이면 결과를 다음 시도에 알려 준다

수정안을 만드는 쪽에는 검증용 요청 문장을 보여 주지 않는다. 시도마다 가설·변경·요청별 전후 결과가 `evals/history/<스킬>/log.jsonl`에 남는다.
채택된 결과는 `evals/results/after-1-<모델>.jsonl`에 쌓여 화면의 "수정 후 1차"로 보인다.

## 유지 · 정리 분류

```
python3 evals/classify.py --out evals/reports/classify-<날짜>.md
```

사용 기록, 선택 평가, 개선 시도 기록으로 스킬을 유지 / 개선 / 비활성화 후보 / 보류로 나눈 보고서를 만든다. 아무것도 바꾸지 않는다. 기준은 [POLICY.md](POLICY.md).
