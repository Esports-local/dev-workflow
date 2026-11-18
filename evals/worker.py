"""화면(esports-ui /skills/)에서 요청한 선택 평가를 맥에서 돌리는 실행기.

api 는 Docker 안에 있어서 `claude` 를 실행할 수 없다 (로그인과 레포가 맥에 있다).
dev.sh up 이 이 실행기를 백그라운드로 같이 켜고 down 이 끈다. 화면에서 "평가 실행 허용"을 켜야 요청을 가져간다. 이 실행기가 맥에서 api 에 대기 중인 평가 요청을 물어보고,
있으면 run.py 를 돌려 진행 상황과 결과를 api 로 보낸다. 한 번에 하나만 돈다.

    python3 evals/worker.py                              # http://localhost:8002 에 5초마다 물어봄
    python3 evals/worker.py --api http://localhost:8002 --once

결과는 evals/results/<묶음>.jsonl 에 쌓이고, 같은 묶음에서 이미 돈 실행은 건너뛴다.

테스트 요청의 기준은 evals/cases/*.jsonl 이다. 실행기는 이 파일들을 api 에 알려 주고, 화면에서 추가한 요청을 받아
해당 스킬 파일 끝에 옮겨 적는다. 화면에서 정답을 바꾸거나 뺀 요청도 파일에 반영한다 (커밋은 사람이 한다).

    python3 evals/worker.py --sync                       # 평가 없이 테스트 요청만 맞춤
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("ESPORTS_ROOT", HERE.parent.parent))  # esports 세 레포가 있는 폴더
REPOS = ("esports-analyzer", "esports-api", "esports-ui")
PLUGIN = "esports-dev"
ROUTE_HEAD = "## 스킬 먼저"
PROGRESS = re.compile(r"^\[(\d+)/(\d+)\]")
PLANNED = re.compile(r"중 (\d+)번 실행")
CASE_KEYS = ("id", "skill", "kind", "split", "repo", "prompt", "expect")
SYNC_EVERY = 6  # 대기 중일 때 몇 번에 한 번 테스트 요청을 맞출지


def call(api: str, path: str, body: dict | None = None) -> dict:
    """api 에 POST 한다. 연결이 안 되면 OSError."""
    req = urllib.request.Request(api.rstrip("/") + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read())


def get(api: str, path: str) -> dict:
    """api 에 GET 한다. 연결이 안 되면 OSError."""
    with urllib.request.urlopen(api.rstrip("/") + path, timeout=10) as res:
        return json.loads(res.read())


def sync_cases(api: str) -> None:
    """evals/cases 의 요청을 api 에 알리고, 화면에서 추가한 요청을 파일에 옮겨 적는다."""
    def read() -> list[dict]:
        return [json.loads(x) for p in sorted((HERE / "cases").glob("*.jsonl"))
                for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

    d = call(api, "/skills/cases/sync", {"cases": read()})
    pending, changes = d["pending"], d.get("changes") or []
    if not pending and not changes:
        return
    for case in pending:
        path = HERE / "cases" / f"{case['skill'].split(':')[-1]}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({k: case[k] for k in CASE_KEYS}, ensure_ascii=False) + "\n")
        print(f"화면에서 추가한 요청을 옮겼습니다: {path.name} [{case['id']}] {case['prompt']}")
    applied = [case["id"] for case in changes if apply_change(case)]
    call(api, "/skills/cases/sync", {"cases": read(), "applied": applied})


def apply_change(case: dict) -> bool:
    """화면에서 바꾼 정답(edit)이나 뺀 요청(remove)을 evals/cases/<스킬>.jsonl 에 반영한다."""
    path = HERE / "cases" / f"{case['skill'].split(':')[-1]}.jsonl"
    if not path.exists():
        return False
    out, found = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["id"] == case["id"]:
            found = True
            if case["change"] == "remove":
                continue
            row.update(kind=case["kind"], expect=case["expect"])
        out.append(json.dumps(row, ensure_ascii=False))
    path.write_text("".join(x + "\n" for x in out), encoding="utf-8")
    what = "뺐습니다" if case["change"] == "remove" else f"정답을 {case['expect'] or '스킬 없음'} 으로 바꿨습니다"
    print(f"화면에서 바꾼 요청을 반영했습니다: {path.name} [{case['id']}] {what}")
    return found or case["change"] == "remove"


def report(api: str, job: dict, **body) -> dict | None:
    try:
        return call(api, f"/skills/evals/jobs/{job['id']}/progress", body)
    except OSError:
        return None  # api 가 잠깐 안 되면 다음에 다시 보낸다


def run_job(api: str, job: dict) -> None:
    """화면에서 요청한 작업 하나를 종류별로 돌린다. 끝나면 고칠 방법 진단을 새로 보낸다."""
    kind, p = job.get("kind") or "eval", job.get("params") or {}
    print(f"작업 #{job['id']} 시작: {kind} {p or ''}")
    error = ""
    try:
        if kind == "eval":
            error = run_eval(api, job, HERE / "results" / f"{job['batch']}.jsonl", job.get("case_ids") or [], job["skills"])
        elif kind == "apply_line":
            add_route_line(p["repo"], p["line"])
            out = HERE / "results" / f"recheck-{job['model']}.jsonl"
            error = run_eval(api, job, out, job.get("case_ids") or [], [], replace=True) if job.get("case_ids") else ""
        elif kind == "propose":
            error = run_cmd([sys.executable, str(HERE / "improve.py"), "--skill", p["skill"].split(":")[-1], "--propose-only",
                             "--model", job["model"], "--post", api, *baseline_arg(p)])
        elif kind == "evaluate_try":
            error = run_cmd([sys.executable, str(HERE / "improve.py"), "--skill", p["skill"].split(":")[-1],
                             "--use-try", str(p["try"]), "--max-tries", "1", "--repeat", "1", "--pr", "--model", job["model"],
                             "--post", api, *baseline_arg(p)])
        elif kind == "commit_line":
            repo = ROOT / p["repo"]
            name = p["skill"].split(":")[-1]
            error = run_cmd(["git", "-C", str(repo), "add", "CLAUDE.md"]) or run_cmd(
                ["git", "-C", str(repo), "commit", "-q", "-m", f"docs/CLAUDE.md 에 {name} 스킬 안내 추가"])
        else:
            error = f"모르는 작업 종류: {kind}"
    except (OSError, ValueError, KeyError) as e:
        error = str(e)
    if kind != "eval":  # eval 은 run_eval 안에서 끝을 알린다
        report(api, job, done=1, total=1, status="failed" if error else "done", error=error)
    print(f"작업 #{job['id']} 끝" + (f": {error}" if error else ""))
    post_fixes(api)


def baseline_arg(p: dict) -> list[str]:
    """화면에서 보던 결과 이름을 improve.py 의 기준 결과로 쓴다."""
    path = HERE / "results" / f"{p['baseline']}.jsonl" if p.get("baseline") else None
    return ["--baseline", str(path)] if path and path.exists() else []


def run_cmd(cmd: list[str]) -> str:
    """명령을 돌리고 실패하면 이유(마지막 줄)를 돌려준다."""
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode:
        tail = (res.stderr or res.stdout).strip().splitlines()
        return tail[-1][:300] if tail else f"실패 (종료 코드 {res.returncode})"
    return ""


def run_eval(api: str, job: dict, out: Path, case_ids: list[str], skills: list[str], replace: bool = False) -> str:
    """run.py 로 평가하고 진행 상황을 보고한다. 화면에서 중지하면 멈춘다. 오류 메시지를 돌려준다 (없으면 빈칸)."""
    cmd = [sys.executable, str(HERE / "run.py"), "--resume", str(out), "--repeat", str(job["repeat"]),
           "--model", job["model"], "--post", api] + (["--replace"] if replace else [])
    if case_ids:  # 요청을 골랐으면 그것만. 다른 스킬 파일의 요청(정답이 이 스킬)도 있어서 --skill 은 안 준다
        for case_id in case_ids:
            cmd += ["--case", case_id]
    else:
        for skill in skills:
            cmd += ["--skill", skill]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    done = total = 0
    error = ""
    for line in proc.stderr:
        line = line.strip()
        planned, step = PLANNED.search(line), PROGRESS.match(line)
        if planned:
            total = int(planned.group(1))
        elif step:
            done = int(step.group(1))
        elif "한도" in line or "없습니다" in line:
            error = line
        else:
            continue
        state = report(api, job, done=done, total=total)
        if state and state["status"] != "running":  # 화면에서 중지함
            proc.terminate()
            print(f"작업 #{job['id']} 중지됨")
            break
    code = proc.wait()
    if code and not error:
        error = f"run.py 가 실패했습니다 (종료 코드 {code})"
    report(api, job, done=done, total=total, status="failed" if error else "done", error=error)
    return error


# ---------------------------------------------------------------- 고칠 방법 진단

def read_routes() -> dict[str, dict]:
    """레포마다 CLAUDE.md "스킬 먼저" 목록: 그 줄들과 안내가 있는 스킬, 커밋 안 한 변경이 있는지."""
    out = {}
    for repo in REPOS:
        path = ROOT / repo / "CLAUDE.md"
        lines, inside = [], False
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("## "):
                    inside = line.startswith(ROUTE_HEAD)
                elif inside and line.startswith("- "):
                    lines.append(line)
        has = {m for line in lines for m in re.findall(rf"{PLUGIN}:[\w-]+", line)}
        dirty = bool(run_out(["git", "-C", str(ROOT / repo), "status", "--porcelain", "CLAUDE.md"]))
        out[repo] = {"lines": lines, "has": has, "dirty": dirty}
    return out


def run_out(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def add_route_line(repo: str, line: str) -> None:
    """레포 CLAUDE.md 의 "스킬 먼저" 목록 끝에 한 줄을 넣는다. 목록이 없으면 만든다. 이미 있으면 그대로 둔다."""
    if repo not in REPOS or not line.startswith("- "):
        raise ValueError("레포 이름이나 줄 모양이 맞지 않습니다")
    path = ROOT / repo / "CLAUDE.md"
    text = path.read_text(encoding="utf-8").splitlines()
    if line in text:
        return
    head = next((i for i, x in enumerate(text) if x.startswith(ROUTE_HEAD)), None)
    if head is None:
        first = next((i for i, x in enumerate(text) if x.startswith("## ")), len(text))
        text[first:first] = [f"{ROUTE_HEAD} ({PLUGIN} 플러그인)",
                             "아래 작업은 코드를 보거나 명령을 치기 전에 해당 스킬을 먼저 부른다.", line, ""]
    else:
        end = head + 1
        while end < len(text) and not text[end].startswith("## "):
            end += 1
        last = max((i for i in range(head + 1, end) if text[i].startswith("- ")), default=head + 1)
        text.insert(last + 1, line)
    path.write_text("\n".join(text) + "\n", encoding="utf-8")
    print(f"{repo}/CLAUDE.md 에 한 줄을 넣었습니다: {line}")


def post_fixes(api: str) -> None:
    """정답이 맞는데도 틀린 요청마다 진단(CLAUDE.md 안내 유무)과 고칠 방법을 api 로 보낸다. 모델은 부르지 않는다.

    기준은 가장 최근의 다시 재기 아닌 결과. 다시 재기(recheck-*)에서 그 요청을 새로 쟀으면 그 결과도 붙인다.
    """
    try:
        batches = [b["batch"] for b in get(api, "/skills")["batches"]]
        main = next((b for b in batches if not b.startswith("recheck")), None)
        if not main:
            return
        failed = get(api, f"/skills/evals?batch={main}&only=failed")["cases"]
        recheck = {}
        for b in (x for x in batches if x.startswith("recheck")):
            for c in get(api, f"/skills/evals?batch={b}&only=all")["cases"]:
                recheck.setdefault(c["id"], {"batch": b, "runs": c["runs"], "correct": c["correct"]})
    except (OSError, urllib.error.HTTPError, KeyError):
        return
    routes = read_routes()
    fixes = []
    for c in failed:
        skill = c["expect"] if c["miss"] else next((d["name"] for d in c["did"] if d["name"].startswith(PLUGIN)), c["expect"])
        if not skill:
            continue  # 쓰면 안 되는 요청에서 다른 스킬을 고른 경우는 정답부터 본다
        repo = c["repo"]
        r = routes.get(repo, {"lines": [], "has": set(), "dirty": False})
        others = [x for x in REPOS if x != repo and skill in routes[x]["has"]]
        line = next((ln for x in others for ln in routes[x]["lines"] if skill in ln), None) or \
            f"- {skill.split(':')[-1]} 작업 → `{skill}`"
        has_line = skill in r["has"]
        fixes.append({
            "case_id": c["id"], "skill": skill, "repo": repo, "verdict": "miss" if c["miss"] else "wrong",
            "batch": main, "has_line": has_line, "others": others, "line": line,
            "applied_uncommitted": has_line and r["dirty"],  # 화면에서 적용했고 아직 커밋 안 함
            "advice": "description" if has_line else "claudemd",
            "recheck": recheck.get(c["id"]),
        })
    try:
        call(api, "/skills/fixes", {"fixes": fixes})
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description="화면에서 요청한 선택 평가를 돌리는 실행기")
    parser.add_argument("--api", default=os.environ.get("ESPORTS_API", "http://localhost:8002"))
    parser.add_argument("--interval", type=float, default=5, help="대기 중인 요청을 물어보는 간격(초)")
    parser.add_argument("--once", action="store_true", help="대기 중인 요청 하나만 돌리고 끝낸다")
    parser.add_argument("--sync", action="store_true", help="테스트 요청만 맞추고 끝낸다 (평가는 안 돌림)")
    args = parser.parse_args()
    if args.sync:
        sync_cases(args.api)
        post_fixes(args.api)
        print("테스트 요청을 맞추고 고칠 방법 진단을 보냈습니다")
        return
    print(f"실행기 시작: {args.api} (끄려면 Ctrl+C)")
    warned, tick, paused = False, 0, False
    while True:
        try:
            if tick % SYNC_EVERY == 0:
                sync_cases(args.api)
                post_fixes(args.api)
            claimed = call(args.api, "/skills/evals/jobs/claim")
            job = claimed["job"]
            if claimed.get("enabled") is False and not paused:
                print("화면에서 '평가 실행 허용'이 꺼져 있어 요청을 가져가지 않고 기다립니다")
            paused = claimed.get("enabled") is False
            if job:
                sync_cases(args.api)  # 방금 추가한 요청까지 넣고 돌린다
            warned = False
        except (OSError, urllib.error.HTTPError) as e:
            if not warned:
                print(f"api 에 연결하지 못했습니다. 계속 시도합니다: {e}")
                warned = True
            job = None
        if job:
            run_job(args.api, job)
        if args.once:
            break
        tick += 1
        if not job:
            time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n실행기를 끕니다")
