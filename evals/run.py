"""스킬 선택 평가.

테스트 요청마다 `claude -p`를 해당 레포 폴더에서 실행하고, 첫 도구 호출을 읽은 뒤 바로 끊는다
(스킬을 고른 뒤의 실제 작업은 돌지 않는다). 첫 호출이 Skill 이면 그 스킬을, 아니면 "스킬 없음"으로 본다.

    python3 evals/run.py                         # 전체, sonnet, 요청마다 2번
    python3 evals/run.py --repeat 3 --split val
    python3 evals/run.py --skill local-up --limit 4 --repeat 1
    python3 evals/run.py --report evals/results/<파일>.jsonl   # 다시 실행하지 않고 집계만
    python3 evals/run.py --resume evals/results/<파일>.jsonl   # 실패했거나 안 돈 실행만 이어서

사용 한도에 걸리면 남은 실행을 건너뛰고 멈춘다. 한도가 풀린 뒤 --resume 으로 이어서 돌린다.

결과는 evals/results/<시각>-<모델>.jsonl 에 실행 한 번당 한 줄로 쌓인다.
요청 문장과 고른 도구 이름만 남기고 모델의 답변 내용은 저장하지 않는다.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PLUGIN_NAME = "esports-dev"
TIMEOUT_SEC = 120


def load_cases(cases_dir: Path, skills: list[str] | None, split: str | None) -> list[dict]:
    """evals/cases/*.jsonl 을 읽는다. skills 는 스킬 이름(접두어 없이), split 은 train | val."""
    cases = []
    for path in sorted(cases_dir.glob("*.jsonl")):
        if skills and path.stem not in skills:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                case = json.loads(line)
                if not split or case["split"] == split:
                    cases.append(case)
    return cases


def normalize(skill: str | None) -> str | None:
    """Skill 도구에 넘어온 이름을 `플러그인:스킬` 모양으로 맞춘다."""
    if not skill:
        return None
    skill = skill.lstrip("/")
    if ":" not in skill and (REPO / "plugins" / PLUGIN_NAME / "skills" / skill).is_dir():
        return f"{PLUGIN_NAME}:{skill}"
    return skill


def run_once(case: dict, model: str, plugin_dir: Path, root: Path) -> dict:
    """요청 하나를 한 번 실행하고 첫 도구 호출을 돌려준다.

    Returns:
        {"got": 고른 스킬 | None, "first_tool": 첫 도구 이름 | None, "input_tokens", "sec", "error"}
    """
    cmd = ["claude", "-p", case["prompt"], "--model", model, "--output-format", "stream-json", "--verbose",
           "--plugin-dir", str(plugin_dir), "--no-session-persistence"]
    started = time.time()
    got = first_tool = error = None
    tokens = 0
    proc = subprocess.Popen(cmd, cwd=root / case["repo"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True)
    try:
        for line in proc.stdout:
            if time.time() - started > TIMEOUT_SEC:
                error = "timeout"
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "result":
                if event.get("is_error"):
                    error = str(event.get("result") or event.get("subtype"))[:200]
                break
            if event.get("type") != "assistant":
                continue
            message = event["message"]
            usage = message.get("usage") or {}
            tokens = max(tokens, usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                         + usage.get("cache_creation_input_tokens", 0))
            call = next((b for b in message.get("content", []) if b.get("type") == "tool_use"), None)
            if call:
                first_tool = call["name"]
                if first_tool == "Skill":
                    got = normalize(call.get("input", {}).get("skill"))
                break
    finally:
        proc.kill()
        proc.wait()
    return {"got": got, "first_tool": first_tool, "input_tokens": tokens,
            "sec": round(time.time() - started, 1), "error": error}


def judge(expect: str | None, got: str | None) -> str:
    """실행 한 번을 판정한다.

    correct: 기대한 스킬을 골랐거나, 기대가 없는데 esports-dev 스킬을 고르지 않음
    miss:    기대한 스킬이 있는데 아무 esports-dev 스킬도 고르지 않음 (누락)
    wrong:   기대와 다른 esports-dev 스킬을 고름 (오선택)
    """
    ours = got if got and got.startswith(PLUGIN_NAME + ":") else None
    if ours == expect:
        return "correct"
    return "miss" if ours is None else "wrong"


def report(rows: list[dict]) -> str:
    """실행 기록을 스킬별 정밀도·재현율과 누락·오선택 목록으로 정리한다."""
    rows = [r for r in rows if not r.get("error")]
    if not rows:
        return "집계할 실행이 없습니다"
    skills = sorted({x for r in rows for x in (r["skill"], r["expect"], r["got"])
                     if x and x.startswith(PLUGIN_NAME + ":")})
    lines = []
    total = Counter(r["verdict"] for r in rows)
    lines.append(f"실행 {len(rows)}번 · 정확 {total['correct']} · 누락 {total['miss']} · 오선택 {total['wrong']}"
                 f" · 정확도 {total['correct'] / len(rows):.0%}")
    lines.append("")
    lines.append(f"{'스킬':<28}{'정밀도':>8}{'재현율':>8}{'골라야 함':>10}{'고름':>6}")
    for skill in skills:
        should = [r for r in rows if r["expect"] == skill]
        picked = [r for r in rows if r["got"] == skill]
        hit = sum(1 for r in should if r["got"] == skill)
        precision = f"{hit / len(picked):.0%}" if picked else "-"
        recall = f"{hit / len(should):.0%}" if should else "-"
        lines.append(f"{skill:<28}{precision:>10}{recall:>10}{len(should):>12}{len(picked):>8}")

    by_case = defaultdict(list)
    for r in rows:
        by_case[r["id"]].append(r)
    for verdict, title in (("miss", "누락 (써야 하는데 안 씀)"), ("wrong", "오선택 (다른 스킬을 고름)")):
        found = []
        for case_id, runs in by_case.items():
            bad = [r for r in runs if r["verdict"] == verdict]
            if bad:
                first = Counter(r["got"] or f"({r['first_tool'] or '도구 없이 답함'})" for r in bad)
                found.append((len(bad) / len(runs), case_id, runs[0], len(bad), len(runs), first))
        lines.append("")
        lines.append(f"## {title}: 요청 {len(found)}개")
        for _, case_id, r, bad, n, first in sorted(found, key=lambda x: (-x[0], x[1])):
            what = ", ".join(f"{k} {v}번" for k, v in first.most_common())
            lines.append(f"- [{case_id}] {bad}/{n} · {r['split']} · {r['repo']} · \"{r['prompt']}\"")
            lines.append(f"    기대 {r['expect'] or '스킬 없음'} → {what}")
    tokens = sum(r.get("input_tokens", 0) for r in rows)
    lines.append("")
    lines.append(f"입력 토큰 합계 {tokens:,} (실행당 평균 {tokens // len(rows):,})")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="스킬 선택 평가")
    parser.add_argument("--model", default="sonnet", help="기본 sonnet. 기본 모델(Fable)로 돌지 않게 항상 지정한다")
    parser.add_argument("--repeat", type=int, default=2, help="요청마다 실행 횟수")
    parser.add_argument("--skill", action="append", help="이 스킬의 테스트만 (여러 번 쓸 수 있음, 접두어 없이)")
    parser.add_argument("--split", choices=["train", "val"])
    parser.add_argument("--limit", type=int, help="앞에서부터 이만큼의 요청만 (시험 실행용)")
    parser.add_argument("--workers", type=int, default=2, help="동시에 실행할 수")
    parser.add_argument("--cases", type=Path, default=HERE / "cases")
    parser.add_argument("--plugin-dir", type=Path, default=REPO / "plugins" / PLUGIN_NAME,
                        help="평가할 플러그인 폴더 (설명 수정안을 설치 없이 평가할 때 바꾼다)")
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("ESPORTS_ROOT", REPO.parent)),
                        help="esports 세 레포가 있는 폴더")
    parser.add_argument("--out", type=Path, help="결과 파일 (기본 evals/results/<시각>-<모델>.jsonl)")
    parser.add_argument("--report", type=Path, help="이 결과 파일을 집계만 한다")
    parser.add_argument("--resume", type=Path, help="이 결과 파일에서 실패했거나 안 돈 실행만 이어서 돌린다")
    args = parser.parse_args()

    if args.report:
        rows = [json.loads(x) for x in args.report.read_text(encoding="utf-8").splitlines() if x.strip()]
        print(report(rows))
        return

    cases = load_cases(args.cases, args.skill, args.split)
    if args.limit:
        cases = cases[:args.limit]
    if not cases:
        sys.exit("테스트 요청이 없습니다")
    missing = sorted({c["repo"] for c in cases if not (args.root / c["repo"]).is_dir()})
    if missing:
        sys.exit(f"레포 폴더가 없습니다: {missing} (--root 또는 ESPORTS_ROOT)")

    out = args.resume or args.out or HERE / "results" / f"{datetime.now():%Y%m%d-%H%M%S}-{args.model}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if args.resume:  # 성공한 실행만 남기고, 실패한 줄은 지운 뒤 이어서 쓴다
        rows = [r for x in out.read_text(encoding="utf-8").splitlines() if x.strip()
                for r in [json.loads(x)] if not r.get("error")]
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    done = {(r["id"], r["run"]) for r in rows}
    jobs = [(case, n) for n in range(1, args.repeat + 1) for case in cases if (case["id"], n) not in done]
    print(f"요청 {len(cases)}개 × {args.repeat}번 중 {len(jobs)}번 실행, 모델 {args.model} → {out}", file=sys.stderr)
    limit_hit = threading.Event()

    def work(job):
        case, n = job
        if limit_hit.is_set():
            return None
        result = run_once(case, args.model, args.plugin_dir, args.root)
        if result["error"] and "limit" in result["error"].lower():
            limit_hit.set()
            print(f"사용 한도에 걸려 멈춥니다: {result['error']}", file=sys.stderr)
            return None
        return {**case, "run": n, "model": args.model, **result, "verdict": judge(case["expect"], result["got"]),
                "at": datetime.now().isoformat(timespec="seconds")}

    with out.open("a", encoding="utf-8") as f, ThreadPoolExecutor(args.workers) as pool:
        for i, row in enumerate(pool.map(work, jobs), 1):
            if row is None:
                continue
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            mark = "실패 " + row["error"] if row["error"] else row["verdict"]
            print(f"[{i}/{len(jobs)}] {row['id']} #{row['run']}: {mark} ({row['got'] or row['first_tool']})",
                  file=sys.stderr)
    if limit_hit.is_set():
        left = len(cases) * args.repeat - len({(r["id"], r["run"]) for r in rows})
        print(f"\n사용 한도에 걸려 {left}번이 남았습니다. 한도가 풀리면 --resume {out} 으로 이어서 돌리세요")
    errors = [r for r in rows if r["error"]]
    if errors:
        print(f"\n실행 실패 {len(errors)}번은 집계에서 뺐습니다: {Counter(r['error'] for r in errors)}")
    print(report(rows))


if __name__ == "__main__":
    main()
