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
| `pipeline-run` | 분석 단계를 골라 실행 · 중간부터 재실행 |
| `translate-run` | 번역 조건 선택 → 예상 토큰 → 실행 |
| `roi-config` | 중계 화면 OCR 영역 설정 추가 · 수정 |
| `add-endpoint` | 엔드포인트 추가 · 변경 때 api · ui · analyzer 를 같이 고치기 |
| `add-stage` | 파이프라인 단계 추가 |
| `docs-sync` | 구현 뒤 README · CLAUDE.md · PLAN 맞추기 |
| `job-debug` | 분석 작업 실패 · 멈춤 원인 찾기 |

커밋 규칙처럼 항상 적용되는 내용은 스킬이 아니라 각 레포 `CLAUDE.md`에 둔다.

## 구조

```
.claude-plugin/marketplace.json
plugins/esports-dev/
  .claude-plugin/plugin.json
  skills/<스킬>/SKILL.md
  scripts/dev.sh             # local-up 이 부르는 실행 스크립트
evals/cases/<스킬>.jsonl     # 테스트 요청 (스킬마다 써야 하는 요청 6 + 쓰면 안 되는 요청 6, train 8 · val 4)
evals/run.py                 # 선택 평가
evals/results/               # 평가 실행 기록
```

## 선택 평가

```
python3 evals/run.py                      # 전체 108개 × 2번, sonnet
python3 evals/run.py --split val --repeat 3
python3 evals/run.py --report evals/results/<파일>.jsonl
```

요청마다 `claude -p`를 해당 레포 폴더에서 실행해 첫 도구 호출만 읽고 끊는다. 첫 호출이 Skill 이 아니면 스킬을 안 쓴 것으로 본다.
esports 세 레포는 이 레포와 같은 폴더에 나란히 있어야 한다 (다르면 `--root` 또는 `ESPORTS_ROOT`).
