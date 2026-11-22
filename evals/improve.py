"""스킬 설명(description) 자동 개선.

스킬 하나에 대해: 개선용 실패 요청을 LLM 에 보여 주고 원인 가설 + 수정안을 받는다 → 플러그인 복사본에 적용해
다시 평가한다 → 요청별로 고쳐짐/새로 깨짐을 적는다 → 검증용 점수가 오르고 다른 스킬이 깨지지 않으면 채택한다.
기각되면 그 결과를 다음 시도에 알려 주고 다시 한다.

빠른 검사는 두 단계다: 기준에서 틀렸던 개선용 요청만 먼저 1번씩 돌려 하나도 안 고쳐지면 바로 기각하고
(대부분의 기각이 여기서 끝나 한도를 아낀다), 고쳐진 것이 있으면 나머지 개선용을 돌려 전체가 올랐는지 본다.

    python3 evals/improve.py --skill add-endpoint                # 최대 3번 시도, 채택되면 SKILL.md 를 고친다
    python3 evals/improve.py --skill add-endpoint --pr           # 채택되면 브랜치·PR 까지 만든다
    python3 evals/improve.py --skill add-endpoint --propose-only # 수정안만 받아 보고 평가는 하지 않는다
    python3 evals/improve.py --skill add-endpoint --dry-run      # 모델 호출 없이 실행 계획과 프롬프트만 본다
    python3 evals/improve.py --skill add-endpoint --post-history --post http://localhost:8002  # 시도 기록만 화면에

- 수정안을 만들 때는 개선용(train) 요청만 보여 준다. 검증용(val) 요청 문장은 보여 주지 않는다
- 기준 결과(--baseline, 기본 evals/results/baseline-<모델>.jsonl)에 없는 실행은 먼저 지금 설명으로 돌려 채운다
- 시도 기록: evals/history/<스킬>/log.jsonl, 시도별 평가 기록: evals/history/<스킬>/try<N>.jsonl
- 채택된 수정안의 평가 기록은 evals/results/after-1-<모델>.jsonl 에도 쌓는다 (화면의 "수정 후 1차")
- LLM 은 API 키 없이 `claude -p` 로 부른다 (구독 한도)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import run as evalrun

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PLUGIN = evalrun.PLUGIN_NAME
MAX_CHARS = 1024  # 스킬 설명 길이 제한. 넘으면 잘린다
AIM_CHARS = 600


class LimitHit(Exception):
    """사용 한도에 걸림."""


# ---------------------------------------------------------------- 설명 읽기·쓰기

def skill_file(plugin_dir: Path, name: str) -> Path:
    return plugin_dir / "skills" / name / "SKILL.md"


def read_description(plugin_dir: Path, name: str) -> str:
    text = skill_file(plugin_dir, name).read_text(encoding="utf-8")
    line = re.search(r"^description: (.*)$", text, re.M).group(1).strip()
    return json.loads(line) if line.startswith('"') else line


def write_description(plugin_dir: Path, name: str, description: str) -> None:
    """SKILL.md 머리말의 description 한 줄을 바꾼다. YAML 이 헷갈릴 글자가 있으면 따옴표로 감싼다."""
    path = skill_file(plugin_dir, name)
    risky = ": " in description or " #" in description or description[:1] in "\"'[{>|*&!%@`-?"
    value = json.dumps(description, ensure_ascii=False) if risky else description
    text = re.sub(r"^description: .*$", lambda _: f"description: {value}", path.read_text(encoding="utf-8"),
                  count=1, flags=re.M)
    path.write_text(text, encoding="utf-8")


def all_descriptions(plugin_dir: Path) -> dict[str, str]:
    return {p.parent.name: read_description(plugin_dir, p.parent.name)
            for p in sorted((plugin_dir / "skills").glob("*/SKILL.md"))}


# ---------------------------------------------------------------- 평가 실행

def load_rows(path: Path) -> list[dict]:
    """결과 파일을 읽어 지금 테스트 요청의 정답으로 다시 판정한다 (뺀 요청은 버림)."""
    if not path.exists():
        return []
    rows = [r for x in path.read_text(encoding="utf-8").splitlines() if x.strip()
            for r in [json.loads(x)] if not r.get("error")]
    return evalrun.rejudge(rows, evalrun.load_cases(HERE / "cases", None, None))


def ensure_runs(cases: list[dict], repeat: int, path: Path, plugin_dir: Path, args) -> list[dict]:
    """cases 를 요청마다 repeat 번까지 채워 돌리고(이미 path 에 있는 실행은 건너뜀) 그 실행 기록을 돌려준다.

    Raises:
        LimitHit: 사용 한도에 걸림. 그때까지의 기록은 path 에 남는다.
    """
    rows = load_rows(path)
    done = {(r["id"], r["run"]) for r in rows}
    jobs = [(c, n) for n in range(1, repeat + 1) for c in cases if (c["id"], n) not in done]
    path.parent.mkdir(parents=True, exist_ok=True)

    def work(job):
        case, n = job
        result = evalrun.run_once(case, args.model, plugin_dir, args.root)
        return {**case, "run": n, "model": args.model, **result,
                "verdict": evalrun.judge(case["expect"], result["got"]),
                "at": datetime.now().isoformat(timespec="seconds")}

    with path.open("a", encoding="utf-8") as f, ThreadPoolExecutor(args.workers) as pool:
        for row in pool.map(work, jobs):
            if row["error"]:
                if "limit" in row["error"].lower():
                    raise LimitHit(row["error"])
                print(f"  실행 실패 {row['id']}: {row['error']}", file=sys.stderr)
                continue
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            rows.append(row)
    ids = {c["id"] for c in cases}
    return [r for r in rows if r["id"] in ids and r["run"] <= repeat]


def rates(rows: list[dict]) -> dict[str, float]:
    """요청별 정확 비율 (0~1)."""
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["id"], []).append(r["verdict"] == "correct")
    return {k: sum(v) / len(v) for k, v in by.items()}


def score(rate: dict[str, float], cases: list[dict]) -> float | None:
    """cases 의 평균 정확 비율."""
    values = [rate[c["id"]] for c in cases if c["id"] in rate]
    return sum(values) / len(values) if values else None


def ledger(before: dict[str, float], after: dict[str, float], cases: list[dict]) -> list[dict]:
    """요청별 전후 비교: fixed(고쳐짐) / broken(새로 깨짐) / same."""
    out = []
    for c in cases:
        b, a = before.get(c["id"]), after.get(c["id"])
        if b is None or a is None:
            continue
        status = "fixed" if a > b else "broken" if a < b else "same"
        out.append({"id": c["id"], "split": c["split"], "kind": c["kind"], "prompt": c["prompt"],
                    "expect": c["expect"], "before": b, "after": a, "status": status})
    return out


# ---------------------------------------------------------------- 수정안 만들기

def what_happened(rows: list[dict]) -> str:
    bad = [r for r in rows if r["verdict"] != "correct"]
    did = sorted({(r["got"] or f"스킬 없이 {r['first_tool'] or '바로 답함'}"
                   + (f" `{r['first_input']}`" if r.get("first_input") else "")) for r in bad})
    return ", ".join(did)


def build_prompt(name: str, descriptions: dict[str, str], train: list[dict], rows: list[dict],
                 history: list[dict]) -> str:
    """수정안을 요청하는 프롬프트. 개선용 요청만 넣는다."""
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["id"], []).append(r)
    failed, passed = [], []
    for c in train:
        runs = by.get(c["id"], [])
        wrong = sum(1 for r in runs if r["verdict"] != "correct")
        expect = c["expect"] or "esports-dev 스킬을 쓰지 않음"
        if wrong:
            failed.append(f'- "{c["prompt"]}" (레포 {c["repo"]}) → 기대: {expect} / 실제: {what_happened(runs)} '
                          f'({wrong}/{len(runs)}번 틀림)')
        else:
            passed.append(f'- "{c["prompt"]}" → 기대: {expect}')
    others = "\n".join(f"- {PLUGIN}:{k}: {v}" for k, v in descriptions.items() if k != name)
    tries = ""
    for h in history:
        if h.get("invalid") or h.get("proposed"):
            continue  # 평가 환경이 달랐던 시도, 평가 전 수정안은 근거로 쓰지 않는다
        fixed = [x["prompt"] for x in h.get("ledger", []) if x["status"] == "fixed" and x["split"] == "train"]
        broken = [x["prompt"] for x in h.get("ledger", []) if x["status"] == "broken" and x["split"] == "train"]
        cause = f' · 원인 종류 {h["cause"]}' if h.get("cause") else ""
        tries += (f'\n### 시도 {h["try"]} ({"채택" if h["accepted"] else "기각"}: {h["reason"]}{cause})\n'
                  f'가설: {h["hypothesis"]}\n설명: {h["description"]}\n'
                  f'개선용에서 고쳐진 요청: {fixed or "없음"}\n개선용에서 새로 깨진 요청: {broken or "없음"}\n'
                  f'검증용에서 새로 깨진 요청 수: {h.get("val_broken", 0)}, 다른 스킬에서 새로 깨진 요청 수: {h.get("others_broken", 0)}\n')
    return f"""Claude Code 스킬 "{PLUGIN}:{name}"의 설명(description)을 고치려고 합니다.

Claude 는 사용자의 요청을 받으면 스킬 목록의 이름과 설명만 보고 스킬을 쓸지 정합니다. 스킬 본문은 고른 뒤에야 읽습니다.
지금 문제는 대부분 "써야 하는 요청인데 스킬을 부르지 않고 바로 Bash·Read 로 작업을 시작하는 것"입니다.

## 지금 설명
{descriptions[name]}

## 같은 플러그인의 다른 스킬 (경계가 겹치면 안 됩니다)
{others}

## 틀린 요청 (개선용)
{chr(10).join(failed) or "없음"}

## 맞힌 요청 (개선용, 깨뜨리면 안 됩니다)
{chr(10).join(passed) or "없음"}
{"## 지난 시도" + tries if tries else ""}
## 원인 종류
틀린 원인은 보통 아래 중 하나입니다.
- 범위: 틀린 요청의 유형이 지금 설명에 아예 없다.
- 우선순위: 유형은 설명에 있는데(예시나 비슷한 말이 이미 있다), Claude 가 "간단해 보이니 코드부터 보자"며 스킬을 건너뛴다.
  → 범위를 더 늘려도 소용없다. 코드를 보기 전에 먼저 불러야 한다는 것과 그 이유(스킬 없이 하면 무엇을 빠뜨리는지)를 적는다.
- 앞부분: 언제 쓰는지가 파일 목록 같은 세부 뒤에 묻혀 있다. → 첫 문장에서 바로 말하고 세부는 뒤로 보내거나 뺀다.
- 경계: 다른 스킬이나 "스킬 없이 해도 되는 일"과 헷갈린다.

## 할 일
1. 틀린 요청마다 그 유형이 지금 설명에 이미 들어 있는지 먼저 확인합니다. 이미 들어 있는데 틀렸다면 원인은 범위가 아닙니다.
2. 원인 종류를 **한 가지** 고르고, 근거를 들어 가설을 씁니다.
3. 지난 시도가 있으면 그 결과를 근거로 삼습니다. 기각됐는데 고쳐진 요청이 하나도 없던 시도와 **같은 원인 종류는 다시 고르지 않습니다**.
4. 가설에 맞게 설명을 고칩니다. 필요하면 문장 순서와 길이까지 바꿔 설명 전체를 다시 써도 됩니다.

## 지켜야 할 것
- 틀린 요청의 문장을 그대로 베껴 넣지 않습니다. 그 요청이 속한 **유형**을 일반적인 말로 적습니다 (처음 보는 요청에도 통해야 합니다).
- 사용자의 의도(무엇을 하려는가)를 중심으로 적고, 언제 써야 하는지와 쓰면 안 되는 경우를 분명히 합니다.
- 한 줄, {AIM_CHARS}자 안쪽 (절대 {MAX_CHARS}자를 넘지 않음), 한국어, 줄바꿈 없음. 길다고 잘 골라지는 것이 아닙니다. 짧고 분명할수록 좋습니다.
- 다른 스킬의 영역을 침범하지 않습니다. 맞힌 요청을 깨뜨리지 않습니다.

아래 형식으로만 답합니다.
<cause>범위 | 우선순위 | 앞부분 | 경계 중 하나</cause>
<hypothesis>원인 가설 한두 문장 (틀린 요청의 유형이 지금 설명에 있는지 포함)</hypothesis>
<change>무엇을 바꿨는지 한 문장</change>
<new_description>새 설명</new_description>"""


def ask_llm(prompt: str, model: str) -> str:
    """claude -p 로 답을 받는다 (도구 없이, 빈 폴더에서)."""
    with tempfile.TemporaryDirectory() as cwd:
        res = subprocess.run(["claude", "-p", prompt, "--model", model, "--output-format", "json", "--tools", "",
                              "--no-session-persistence"], cwd=cwd, capture_output=True, text=True, timeout=300,
                             env={**os.environ, "ESPORTS_EVAL": "1"})
    try:
        out = json.loads(res.stdout)
    except ValueError as e:
        raise RuntimeError(f"claude 응답을 읽지 못했습니다: {res.stdout[:200] or res.stderr[:200]}") from e
    text = str(out.get("result") or "")
    if out.get("is_error"):
        if "limit" in text.lower():
            raise LimitHit(text)
        raise RuntimeError(f"claude 오류: {text[:200]}")
    return text


def propose(name, descriptions, train, rows, history, model) -> dict:
    """원인 가설과 새 설명을 받는다. 너무 길면 한 번 줄여 달라고 한다."""
    text = ask_llm(build_prompt(name, descriptions, train, rows, history), model)

    def tag(t, source=None):
        m = re.search(rf"<{t}>(.*?)</{t}>", source or text, re.S)
        return " ".join(m.group(1).split()) if m else ""

    new = tag("new_description").strip('"')
    if len(new) > MAX_CHARS:
        shorter = ask_llm(f"아래 스킬 설명이 {len(new)}자라 제한({MAX_CHARS}자)을 넘습니다. 뜻을 유지하고 {AIM_CHARS}자 안쪽 한 줄로 "
                          f"줄여 <new_description> 태그 안에만 답하세요.\n\n{new}", model)
        new = tag("new_description", shorter).strip('"')
    if not new or len(new) > MAX_CHARS:
        raise RuntimeError("쓸 수 있는 수정안을 받지 못했습니다")
    return {"cause": tag("cause"), "hypothesis": tag("hypothesis"), "change": tag("change"), "description": new}


# ---------------------------------------------------------------- 루프

def pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def improve(args) -> dict | None:
    """스킬 하나를 개선한다. 채택된 시도(없으면 None)를 돌려준다."""
    name = args.skill
    full = f"{PLUGIN}:{name}"
    cases = evalrun.load_cases(args.cases, None, None)
    mine = [c for c in cases if c["skill"] == full]
    train = [c for c in mine if c["split"] == "train"]
    val = [c for c in mine if c["split"] == "val"]
    others = [c for c in cases if c["skill"] != full and c["split"] == "val"]  # 다른 스킬이 깨지는지 볼 요청
    if not mine:
        sys.exit(f"테스트 요청이 없습니다: {name}")
    real = REPO / "plugins" / PLUGIN
    hist_dir = HERE / "history" / name
    hist_dir.mkdir(parents=True, exist_ok=True)
    log_path = hist_dir / "log.jsonl"
    history = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()] if log_path.exists() else []

    if args.propose_only:  # 평가를 돌리지 않고 지금 있는 기준 결과만 쓴다
        ids = {c["id"] for c in mine}
        base_rows, base_others = [r for r in load_rows(args.baseline) if r["id"] in ids], []
    else:
        print(f"[기준] 지금 설명으로 빠진 실행을 채웁니다 ({args.baseline.name})", file=sys.stderr)
        base_rows = ensure_runs(mine, args.repeat, args.baseline, real, args)
        base_others = ensure_runs(others, 1, args.baseline, real, args)
    base = rates(base_rows)
    base_o = rates(base_others)
    print(f"[기준] {name}: 개선용 {pct(score(base, train))} · 검증용 {pct(score(base, val))} · "
          f"다른 스킬 검증용 {pct(score(base_o, others))}", file=sys.stderr)
    if score(base, mine) == 1.0:
        print("틀린 요청이 없어 고칠 것이 없습니다", file=sys.stderr)
        return None

    start = len(history)
    for n in range(start + 1, start + args.max_tries + 1):
        descriptions = all_descriptions(real)
        if args.use_try:  # 화면에서 받아 둔 수정안을 그대로 평가한다 (LLM 을 다시 부르지 않음)
            saved = next((h for h in history if h.get("try") == args.use_try), None)
            if not saved:
                sys.exit(f"시도 {args.use_try} 이 기록에 없습니다")
            idea = {k: saved.get(k, "") for k in ("cause", "hypothesis", "change", "description")}
        else:
            idea = propose(name, descriptions, train, [r for r in base_rows if r["split"] == "train"], history, args.model)
        print(f"\n[시도 {n}] 원인 종류: {idea['cause'] or '-'}\n  가설: {idea['hypothesis']}\n  변경: {idea['change']}\n  설명({len(idea['description'])}자): "
              f"{idea['description']}", file=sys.stderr)
        if args.propose_only:  # 수정안만 기록해 둔다 (화면의 "고칠 방법"에서 보고 평가할지 정함)
            entry = {"try": n, "at": datetime.now().isoformat(timespec="seconds"), "skill": full, "model": args.model,
                     "before_description": descriptions[name], **idea, "accepted": False, "proposed": True,
                     "reason": f"수정안 (평가 전, 기준 {args.baseline.name})"}
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return None
        entry = {"try": n, "at": datetime.now().isoformat(timespec="seconds"), "skill": full, "model": args.model,
                 "before_description": descriptions[name], **idea, "accepted": False,
                 **({"from_try": args.use_try} if args.use_try else {})}
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / PLUGIN
            shutil.copytree(real, candidate)
            write_description(candidate, name, idea["description"])
            try_path = hist_dir / f"try{n}.jsonl"
            if try_path.exists():  # 전에 한도 등으로 멈춰 로그에 없는 시도의 평가 기록. 다른 수정안으로 잰 것이라 쓰지 않는다
                stopped = try_path.with_name(f"try{n}-stopped-{datetime.now():%m%d%H%M}.jsonl")
                try_path.rename(stopped)
                print(f"  멈춘 시도의 옛 평가 기록을 {stopped.name} 로 옮김", file=sys.stderr)

            # 빠른 확인 1단계: 기준에서 틀렸던 개선용 요청만. 하나도 안 고쳐지면 나머지는 돌리지 않는다
            failing = [c for c in train if base.get(c["id"], 1.0) < 1.0]
            first = rates(ensure_runs(failing, 1, try_path, candidate, args))
            fixed_first = [c for c in failing if first.get(c["id"], 0.0) > base[c["id"]]]
            entry["train_before"] = score(base, train)
            if failing and not fixed_first:
                entry.update(train_quick=None, ledger=ledger(base, first, failing),
                             reason=f"틀렸던 개선용 요청 {len(failing)}개가 하나도 안 고쳐짐 (나머지 개선용은 안 돌림)")
                quick = None
            else:  # 2단계: 나머지 개선용 1번 (1단계에서 돈 것은 건너뜀)
                quick = rates(ensure_runs(train, 1, try_path, candidate, args))
                entry["train_quick"] = score(quick, train)
            if quick is None:
                pass
            elif entry["train_quick"] <= entry["train_before"]:
                entry.update(reason=f"개선용이 안 오름 ({pct(entry['train_before'])} → {pct(entry['train_quick'])})",
                             ledger=ledger(base, quick, train))
            else:
                after = rates(ensure_runs(mine, args.repeat, try_path, candidate, args))
                after_o = rates(ensure_runs(others, 1, try_path, candidate, args))
                broken = [x for x in ledger(base_o, after_o, others) if x["status"] == "broken"]
                if broken:  # 한 번 틀린 것은 흔들림일 수 있어 한 번 더 돌려 둘 다 틀릴 때만 깨진 것으로 본다
                    again = [c for c in others if c["id"] in {x["id"] for x in broken}]
                    after_o.update(rates(ensure_runs(again, 2, try_path, candidate, args)))
                    broken = [x for x in ledger(base_o, after_o, again) if x["after"] == 0]
                entry.update(
                    ledger=ledger(base, after, mine), others_broken=len(broken),
                    others_broken_cases=[{"id": x["id"], "prompt": x["prompt"]} for x in broken],
                    train_after=score(after, train), val_before=score(base, val), val_after=score(after, val),
                    others_before=score(base_o, others), others_after=score(after_o, others))
                entry["val_broken"] = sum(1 for x in entry["ledger"] if x["status"] == "broken" and x["split"] == "val")
                val_up = entry["val_after"] > entry["val_before"] or (entry["val_before"] == entry["val_after"] == 1.0)
                if broken:
                    entry["reason"] = f"다른 스킬의 요청 {len(broken)}개가 새로 깨짐"
                elif not val_up:
                    entry["reason"] = f"검증용이 안 오름 ({pct(entry['val_before'])} → {pct(entry['val_after'])})"
                elif entry["train_after"] <= entry["train_before"]:
                    entry["reason"] = "개선용이 다시 재 보니 안 오름"
                else:
                    entry.update(accepted=True, reason=f"개선용 {pct(entry['train_before'])} → {pct(entry['train_after'])}, "
                                                       f"검증용 {pct(entry['val_before'])} → {pct(entry['val_after'])}, "
                                                       f"다른 스킬 깨짐 없음")
        history.append(entry)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        fixed = sum(1 for x in entry["ledger"] if x["status"] == "fixed")
        lost = sum(1 for x in entry["ledger"] if x["status"] == "broken")
        print(f"  → {'채택' if entry['accepted'] else '기각'}: {entry['reason']} (고쳐짐 {fixed}, 새로 깨짐 {lost})",
              file=sys.stderr)
        if entry["accepted"]:
            write_description(real, name, idea["description"])
            after_path = HERE / "results" / f"after-1-{args.model}.jsonl"
            kept = {(r["id"], r["run"]): r for r in load_rows(after_path)}
            kept.update({(r["id"], r["run"]): r for r in load_rows(try_path)})
            after_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept.values()),
                                  encoding="utf-8")
            if args.post:
                failed = evalrun.post_rows(args.post, after_path.stem, list(kept.values()))
                print(f"  api 전송 실패: {failed}" if failed else f"  api 로 보냄 (결과 이름 {after_path.stem})", file=sys.stderr)
            return entry
    return None


# ---------------------------------------------------------------- PR

def pr_body(name: str, entry: dict, history: list[dict]) -> str:
    def mark(x):
        return {"fixed": "고쳐짐", "broken": "새로 깨짐", "same": ""}[x["status"]]

    rows = "\n".join(f'| {x["prompt"]} | {"검증용" if x["split"] == "val" else "개선용"} | '
                     f'{(x["expect"] or "스킬 없음").split(":")[-1]} | {pct(x["before"])} | {pct(x["after"])} | {mark(x)} |'
                     for x in entry["ledger"])
    tries = "\n".join(f'{h["try"]}. **{"채택" if h["accepted"] else "기각"}** — {h["reason"]}\n'
                      f'   - 가설: {h["hypothesis"]}\n   - 변경: {h["change"]}' for h in history)
    return f"""`{PLUGIN}:{name}` 스킬 설명을 고칩니다. `evals/improve.py` 가 개선용 실패 요청을 분석해 만든 수정안이고, 검증용 요청으로 전후를 비교했습니다.

## 전후 비교 ({entry["model"]})

| | 수정 전 | 수정 후 |
|---|---|---|
| 개선용 정확도 | {pct(entry["train_before"])} | {pct(entry["train_after"])} |
| 검증용 정확도 | {pct(entry["val_before"])} | {pct(entry["val_after"])} |
| 다른 스킬 검증용 정확도 | {pct(entry["others_before"])} | {pct(entry["others_after"])} |

다른 스킬에서 새로 깨진 요청: {entry["others_broken"]}개

## 설명

수정 전:
> {entry["before_description"]}

수정 후:
> {entry["description"]}

## 요청별 결과

| 요청 | 구분 | 기대 | 수정 전 | 수정 후 | |
|---|---|---|---|---|---|
{rows}

## 시도 기록

{tries}

수정안은 개선용 요청만 보고 만들었고, 검증용 요청 문장은 수정안을 만드는 쪽에 보여 주지 않았습니다.
시도별 평가 기록은 `evals/history/{name}/` 에 있습니다.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""


def open_pr(name: str, entry: dict) -> None:
    """브랜치를 만들어 SKILL.md·시도 기록·평가 기록을 커밋하고 PR 을 연다."""
    def git(*a):
        subprocess.run(["git", *a], cwd=REPO, check=True)

    history = [h for x in (HERE / "history" / name / "log.jsonl").read_text(encoding="utf-8").splitlines()
               for h in [json.loads(x)] if not h.get("invalid") and not h.get("proposed")]
    branch = f"improve/{name}-{datetime.now():%m%d-%H%M}"
    git("checkout", "-q", "-b", branch)
    git("add", f"plugins/{PLUGIN}/skills/{name}/SKILL.md", f"evals/history/{name}", "evals/results")
    git("commit", "-q", "-m", f"fix/{name} 스킬 설명 수정 (검증용 {pct(entry['val_before'])} → {pct(entry['val_after'])})")
    git("push", "-q", "-u", "origin", branch)
    subprocess.run(["gh", "pr", "create", "--title", f"fix/{name} 스킬 설명 수정 (자동 개선)",
                    "--body", pr_body(name, entry, history), "--base", "main", "--head", branch], cwd=REPO, check=True)
    git("checkout", "-q", "main")


def post_history(api: str, name: str) -> None:
    """evals/history/<스킬>/log.jsonl 을 통째로 api 에 보낸다 (화면의 "설명 개선 시도"). 무효 표시도 같이 간다."""
    path = HERE / "history" / name / "log.jsonl"
    if not path.exists():
        return
    entries = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    req = urllib.request.Request(api.rstrip("/") + "/skills/improves",
                                 data=json.dumps({"tries": entries}, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        print(f"  시도 기록 {len(entries)}개를 api 로 보냄", file=sys.stderr)
    except OSError as e:
        print(f"  시도 기록 전송 실패: {e}", file=sys.stderr)


def dry_run(args) -> None:
    """모델을 한 번도 부르지 않고 실행 계획과 LLM 에 보낼 수정안 프롬프트를 보여 준다."""
    name, full = args.skill, f"{PLUGIN}:{args.skill}"
    cases = evalrun.load_cases(args.cases, None, None)
    mine = [c for c in cases if c["skill"] == full]
    if not mine:
        sys.exit(f"테스트 요청이 없습니다: {name}")
    train = [c for c in mine if c["split"] == "train"]
    others = [c for c in cases if c["skill"] != full and c["split"] == "val"]
    rows = load_rows(args.baseline)
    done = {(r["id"], r["run"]) for r in rows}
    missing = sum(1 for c in mine for n in range(1, args.repeat + 1) if (c["id"], n) not in done)
    missing += sum(1 for c in others if (c["id"], 1) not in done)
    base_rate = rates([r for r in rows if r["id"] in {c["id"] for c in train}])
    failing = sum(1 for c in train if base_rate.get(c["id"], 1.0) < 1.0)
    quick = len(train)
    full_check = len(mine) * args.repeat - len(train) + len(others)  # 빠른 검사에서 돈 개선용 1번째는 건너뛴다
    worst = missing + args.max_tries * (quick + full_check)
    log_path = HERE / "history" / name / "log.jsonl"
    history = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()] if log_path.exists() else []
    ids = {c["id"] for c in train}
    prompt = build_prompt(name, all_descriptions(REPO / "plugins" / PLUGIN), train,
                          [r for r in rows if r["id"] in ids], history)
    print(f"""# 실행 계획 ({name}, 모델 {args.model}, 요청마다 {args.repeat}번, 최대 {args.max_tries}번 시도)

테스트 요청: 이 스킬 {len(mine)}개 (개선용 {len(train)} · 검증용 {len(mine) - len(train)}), 다른 스킬 검증용 {len(others)}개
지난 시도: {len(history)}번 (다음은 시도 {len(history) + 1})

| 단계 | 실행 수 |
|---|---|
| 기준 채우기 ({args.baseline.name}에 빠진 것) | {missing} |
| 시도마다 빠른 검사 1단계 (틀렸던 개선용만 × 1, 하나도 안 고쳐지면 여기서 기각) | {failing} |
| 시도마다 빠른 검사 2단계 (나머지 개선용 × 1, 안 오르면 여기서 기각) | {quick - failing} |
| 시도마다 전체 검사 (이 스킬 × {args.repeat} + 다른 스킬 검증용 × 1) | {full_check} (+ 깨진 요청 다시 확인 2번씩) |
| 수정안 만들기 (LLM) | 시도마다 1 |

가장 적게: {missing + (failing or quick)}번 (첫 시도가 빠른 검사 1단계에서 기각) · 가장 많이: 약 {worst}번
선택 평가 1번에 입력 약 3만 토큰 → 가장 많을 때 약 {worst * 30_000 / 1_000_000:.1f}백만 토큰

# 수정안 프롬프트 (시도 {len(history) + 1}에 보낼 내용, 기준 결과 기준)

{prompt}""")


def main():
    parser = argparse.ArgumentParser(description="스킬 설명 자동 개선")
    parser.add_argument("--skill", required=True, help="개선할 스킬 (접두어 없이, 예: add-endpoint)")
    parser.add_argument("--max-tries", type=int, default=3)
    parser.add_argument("--model", default="sonnet", help="평가와 수정안 생성에 쓰는 모델")
    parser.add_argument("--repeat", type=int, default=2, help="대상 스킬 요청의 실행 횟수 (전후 비교용)")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--baseline", type=Path, help="기준 결과 파일 (기본 evals/results/baseline-<모델>.jsonl)")
    parser.add_argument("--cases", type=Path, default=HERE / "cases")
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("ESPORTS_ROOT", REPO.parent)))
    parser.add_argument("--post", metavar="API", default=os.environ.get("ESPORTS_API"), help="결과를 보낼 esports-api 주소")
    parser.add_argument("--propose-only", action="store_true", help="수정안만 받아 보고 끝낸다 (LLM 1번)")
    parser.add_argument("--use-try", type=int, help="기록에 있는 이 시도의 수정안을 그대로 평가한다 (화면에서 받아 둔 수정안)")
    parser.add_argument("--post-history", action="store_true", help="시도 기록만 api 로 보내고 끝낸다 (--post 필요)")
    parser.add_argument("--dry-run", action="store_true", help="모델을 부르지 않고 실행 계획과 수정안 프롬프트만 보여 준다")
    parser.add_argument("--pr", action="store_true", help="채택되면 브랜치와 PR 을 만든다")
    args = parser.parse_args()
    args.baseline = args.baseline or HERE / "results" / f"baseline-{args.model}.jsonl"
    if args.post_history:
        if not args.post:
            sys.exit("--post-history 에는 --post <api 주소> (또는 ESPORTS_API)가 필요합니다")
        post_history(args.post, args.skill)
        return
    if args.dry_run:
        dry_run(args)
        return
    try:
        entry = improve(args)
    except LimitHit as e:
        if args.post:
            post_history(args.post, args.skill)
        sys.exit(f"사용 한도에 걸려 멈췄습니다: {e}\n한도가 풀린 뒤 같은 명령으로 다시 돌리면 이미 돈 실행은 건너뜁니다")
    if args.post:  # 수정안만 받은 경우도 화면에 보낸다
        post_history(args.post, args.skill)
    if entry is None:
        if not args.propose_only:
            print("\n채택된 수정안이 없습니다. 시도 기록은 evals/history/ 에 남았습니다")
        return
    print(f"\n채택: plugins/{PLUGIN}/skills/{args.skill}/SKILL.md 의 설명을 고쳤습니다")
    if args.pr:
        open_pr(args.skill, entry)


if __name__ == "__main__":
    main()
