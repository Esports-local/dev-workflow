"""스킬 유지·개선·비활성화 후보 분류. 기준은 POLICY.md, 숫자는 evals/policy.json.

사용 기록(호출 수·성공률·시간·토큰), 선택 평가(재현율·정밀도), 개선 시도 기록을 모아 스킬마다 하나로 분류한다.
아무것도 바꾸지 않고 보고서만 만든다. 비활성화는 사람이 보고서를 보고 PR 로 한다.

    python3 evals/classify.py                                   # 화면에 보고서
    python3 evals/classify.py --out evals/reports/classify.md   # 파일로 저장
    python3 evals/classify.py --results evals/results/after-1-sonnet.jsonl --json

- 사용 기록: ~/.claude/esports-dev/usage.jsonl (훅이 적는 파일). 같은 id 는 마지막 줄만 쓴다
- 선택 평가: evals/results/baseline-sonnet.jsonl (--results 로 바꿈)
- 개선 시도: evals/history/<스킬>/log.jsonl
- 스킬 나이: git 에서 SKILL.md 가 처음 커밋된 날
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run as evalrun

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PLUGIN = "esports-dev"
KEEP, IMPROVE, RETIRE, HOLD = "유지", "개선", "비활성화 후보", "보류"
ORDER = [RETIRE, IMPROVE, HOLD, KEEP]


def pct(x) -> str:
    return "-" if x is None else f"{x:.0%}"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def usage_stats(path: Path, since: datetime) -> dict[str, dict]:
    """사용 기록을 스킬별로 모은다 (최근 window 안, 같은 id 는 마지막 줄)."""
    last = {}
    for r in load_jsonl(path):
        if r.get("id") and r.get("skill"):
            last[r["id"]] = r
    by: dict[str, list] = {}
    for r in last.values():
        try:
            at = datetime.fromisoformat(r["used_at"])
        except (KeyError, ValueError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at >= since:
            by.setdefault(r["skill"], []).append(r)
    out = {}
    for skill, rows in by.items():
        known = [r for r in rows if isinstance(r.get("ok"), bool)]
        secs = [r["duration_sec"] for r in rows if r.get("duration_sec") is not None]
        tokens = [(r.get("input_tokens") or 0) + (r.get("output_tokens") or 0) for r in rows if r.get("input_tokens")]
        out[skill] = {"calls": len(rows),
                      "success_rate": sum(r["ok"] for r in known) / len(known) if known else None,
                      "finished": len(known),
                      "avg_sec": sum(secs) / len(secs) if secs else None,
                      "avg_tokens": sum(tokens) / len(tokens) if tokens else None}
    return out


def eval_stats(rows: list[dict]) -> dict[str, dict]:
    """평가 기록을 스킬별 재현율·정밀도로 모은다."""
    rows = [r for r in rows if not r.get("error")]
    out = {}
    for skill in {r["skill"] for r in rows} | {r["expect"] for r in rows if r.get("expect")}:
        should = [r for r in rows if r.get("expect") == skill]
        picked = [r for r in rows if r.get("got") == skill]
        hit = sum(1 for r in should if r.get("got") == skill)
        out[skill] = {"should": len(should), "picked": len(picked), "hit": hit,
                      "recall": hit / len(should) if should else None,
                      "precision": hit / len(picked) if picked else None}
    return out


def failed_tries(name: str) -> int:
    """마지막 채택 뒤로 기각된 개선 시도 수."""
    count = 0
    for entry in load_jsonl(HERE / "history" / name / "log.jsonl"):
        if entry.get("invalid") or entry.get("proposed"):
            continue  # 평가 환경이 달랐던 시도, 평가 전 수정안은 세지 않는다
        count = 0 if entry.get("accepted") else count + 1
    return count


def age_days(path: Path, now: datetime) -> int | None:
    """SKILL.md 가 git 에 처음 들어온 뒤 지난 날 수 (git 기록이 없으면 None)."""
    res = subprocess.run(["git", "log", "--diff-filter=A", "--follow", "--format=%aI", "--", str(path)],
                         cwd=REPO, capture_output=True, text=True)
    dates = res.stdout.split()
    if not dates:
        return None
    return (now - datetime.fromisoformat(dates[-1])).days


def classify(skill: dict, policy: dict, median_tokens: float | None) -> tuple[str, list[str]]:
    """스킬 하나를 분류한다. (분류, 이유 목록)을 돌려준다. 규칙의 순서와 뜻은 POLICY.md."""
    u, e, p = skill["usage"], skill["eval"], policy
    improve, notes = [], []

    evaluated = e and e["should"] >= p["min_should_runs"]
    low_recall = evaluated and e["recall"] < p["min_recall"]
    low_precision = evaluated and e["precision"] is not None and e["precision"] < p["min_precision"]
    if low_recall:
        improve.append(f"재현율 {pct(e['recall'])} (기준 {pct(p['min_recall'])}): 써야 하는 요청에서 안 골라짐 → 설명 개선")
    if low_precision:
        improve.append(f"정밀도 {pct(e['precision'])} (기준 {pct(p['min_precision'])}): 쓰면 안 되는 요청에서 골라짐 → 설명 개선")

    enough_calls = u and u["finished"] >= p["min_calls_for_rates"]
    if enough_calls and u["success_rate"] < p["min_success_rate"]:
        improve.append(f"실행 성공률 {pct(u['success_rate'])} (기준 {pct(p['min_success_rate'])}) → 본문 절차 개선")
    if enough_calls and median_tokens and u["avg_tokens"] and u["avg_tokens"] > p["cost_ratio"] * median_tokens:
        improve.append(f"호출당 토큰 {u['avg_tokens']:,.0f} (다른 스킬 중앙값의 {u['avg_tokens'] / median_tokens:.1f}배) → 본문 줄이기")
    if skill["description_chars"] > p["max_description_chars"]:
        improve.append(f"설명 {skill['description_chars']}자 (기준 {p['max_description_chars']}자): 모든 세션에 실리는 비용 → 설명 줄이기")

    # 비활성화 후보
    observed = skill["age_days"] is not None and skill["age_days"] >= p["window_days"]
    if observed and not u:
        return RETIRE, [f"최근 {p['window_days']}일 호출 0회 (스킬을 만든 지 {skill['age_days']}일)"] + improve
    if (low_recall or low_precision) and skill["failed_tries"] >= p["max_failed_tries"]:
        return RETIRE, [f"개선 시도 {skill['failed_tries']}번이 연속 기각됐는데도 기준 미달"] + improve

    if improve:
        return IMPROVE, improve
    if not evaluated:
        runs = e["should"] if e else 0
        return HOLD, [f"평가 부족: 써야 하는 요청 실행 {runs}번 (최소 {p['min_should_runs']}번)"]
    if not observed:
        age = "기록 없음" if skill["age_days"] is None else f"{skill['age_days']}일"
        notes.append(f"사용 빈도는 아직 판단 안 함: 만든 지 {age} (관찰 기간 {p['window_days']}일)")
    return KEEP, [f"재현율 {pct(e['recall'])}, 정밀도 {pct(e['precision'])}"] + notes


def collect(args, policy: dict, now: datetime) -> list[dict]:
    since = now - timedelta(days=policy["window_days"])
    usage = usage_stats(args.usage, since)
    # 결과 파일의 옛 정답 대신 지금 테스트 요청의 정답으로 판정한다 (화면에서 바꾸거나 뺀 것 반영)
    evals = eval_stats(evalrun.rejudge(load_jsonl(args.results), evalrun.load_cases(HERE / "cases", None, None)))
    skills = []
    for path in sorted((REPO / "plugins" / PLUGIN / "skills").glob("*/SKILL.md")):
        name = path.parent.name
        full = f"{PLUGIN}:{name}"
        text = path.read_text(encoding="utf-8")
        description = re.search(r"^description: (.*)$", text, re.M).group(1)
        skills.append({"skill": full, "name": name, "usage": usage.get(full), "eval": evals.get(full),
                       "description_chars": len(description), "body_chars": len(text),
                       "failed_tries": failed_tries(name), "age_days": age_days(path, now)})
    tokens = [s["usage"]["avg_tokens"] for s in skills
              if s["usage"] and s["usage"]["avg_tokens"] and s["usage"]["finished"] >= policy["min_calls_for_rates"]]
    median = statistics.median(tokens) if len(tokens) >= 3 else None  # 비교할 스킬이 3개는 있어야 한다
    for s in skills:
        s["class"], s["reasons"] = classify(s, policy, median)
    return skills


def render(skills: list[dict], policy: dict, args, now: datetime) -> str:
    lines = [f"# 스킬 분류 보고서 ({now:%Y-%m-%d})", "",
             f"평가 기록 `{args.results.name}` · 사용 기록 최근 {policy['window_days']}일 · 기준은 `POLICY.md`", ""]
    counts = {c: sum(1 for s in skills if s["class"] == c) for c in ORDER}
    lines += [" · ".join(f"{c} {n}" for c, n in counts.items() if n), "",
              "| 스킬 | 분류 | 호출 | 성공률 | 평균 시간 | 호출당 토큰 | 재현율 | 정밀도 | 설명 길이 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for s in sorted(skills, key=lambda s: (ORDER.index(s["class"]), s["name"])):
        u, e = s["usage"] or {}, s["eval"] or {}
        lines.append(f"| {s['name']} | {s['class']} | {u.get('calls', 0)} | {pct(u.get('success_rate'))} | "
                     f"{'-' if u.get('avg_sec') is None else f'{u["avg_sec"]:.0f}초'} | "
                     f"{'-' if not u.get('avg_tokens') else f'{u["avg_tokens"]:,.0f}'} | "
                     f"{pct(e.get('recall'))} | {pct(e.get('precision'))} | {s['description_chars']}자 |")
    for c in ORDER:
        group = [s for s in skills if s["class"] == c]
        if not group:
            continue
        lines += ["", f"## {c}"]
        for s in sorted(group, key=lambda s: s["name"]):
            lines.append(f"- **{s['name']}**")
            lines += [f"  - {r}" for r in s["reasons"]]
    lines += ["", "분류는 제안입니다. 비활성화는 자동으로 하지 않고, 사람이 확인한 뒤 PR 로 합니다."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="스킬 유지·개선·비활성화 후보 분류")
    parser.add_argument("--results", type=Path, default=HERE / "results" / "baseline-sonnet.jsonl", help="선택 평가 기록")
    parser.add_argument("--usage", type=Path, help="사용 기록 파일 (기본 ~/.claude/esports-dev/usage.jsonl)")
    parser.add_argument("--policy", type=Path, default=HERE / "policy.json")
    parser.add_argument("--out", type=Path, help="보고서를 이 파일로 저장")
    parser.add_argument("--json", action="store_true", help="보고서 대신 JSON 으로 출력")
    args = parser.parse_args()
    args.usage = args.usage or Path(os.environ.get("ESPORTS_DEV_HOME", Path.home() / ".claude" / PLUGIN)) / "usage.jsonl"
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    skills = collect(args, policy, now)
    if args.json:
        print(json.dumps(skills, ensure_ascii=False, indent=1))
        return
    text = render(skills, policy, args, now)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"보고서를 저장했습니다: {args.out}", file=sys.stderr)
    print(text)


if __name__ == "__main__":
    main()
