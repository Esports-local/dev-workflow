"""화면(esports-ui /skills/)에서 요청한 선택 평가를 맥에서 돌리는 실행기.

api 는 Docker 안에 있어서 `claude` 를 실행할 수 없다. 이 실행기가 맥에서 api 에 대기 중인 평가 요청을 물어보고,
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


def run_job(api: str, job: dict) -> None:
    """평가 요청 하나를 run.py 로 돌리고 진행 상황을 보고한다. 화면에서 중지하면 멈춘다."""
    path = f"/skills/evals/jobs/{job['id']}/progress"
    out = HERE / "results" / f"{job['batch']}.jsonl"
    cmd = [sys.executable, str(HERE / "run.py"), "--resume", str(out), "--repeat", str(job["repeat"]),
           "--model", job["model"], "--post", api]
    if job.get("case_ids"):  # 요청을 골랐으면 그것만. 다른 스킬 파일의 요청(정답이 이 스킬)도 있어서 --skill 은 안 준다
        for case_id in job["case_ids"]:
            cmd += ["--case", case_id]
    else:
        for skill in job["skills"]:
            cmd += ["--skill", skill]
    print(f"평가 #{job['id']} 시작: {job['batch']} · {', '.join(job['skills']) or '전체'}"
          f"{' · 요청 ' + str(len(job['case_ids'])) + '개' if job.get('case_ids') else ''} · {job['repeat']}번씩 · {job['model']}")
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
        try:
            state = call(api, path, {"done": done, "total": total})
        except OSError:
            continue  # api 가 잠깐 안 되면 다음 줄에서 다시 보낸다
        if state["status"] != "running":  # 화면에서 중지함
            proc.terminate()
            print(f"평가 #{job['id']} 중지됨")
            break
    code = proc.wait()
    if code and not error:
        error = f"run.py 가 실패했습니다 (종료 코드 {code})"
    try:
        call(api, path, {"done": done, "total": total, "status": "failed" if error else "done", "error": error})
    except OSError as e:
        print(f"결과 보고 실패: {e}")
    print(f"평가 #{job['id']} 끝: {done}/{total}" + (f" · {error}" if error else ""))


def main():
    parser = argparse.ArgumentParser(description="화면에서 요청한 선택 평가를 돌리는 실행기")
    parser.add_argument("--api", default=os.environ.get("ESPORTS_API", "http://localhost:8002"))
    parser.add_argument("--interval", type=float, default=5, help="대기 중인 요청을 물어보는 간격(초)")
    parser.add_argument("--once", action="store_true", help="대기 중인 요청 하나만 돌리고 끝낸다")
    parser.add_argument("--sync", action="store_true", help="테스트 요청만 맞추고 끝낸다 (평가는 안 돌림)")
    args = parser.parse_args()
    if args.sync:
        sync_cases(args.api)
        print("테스트 요청을 맞췄습니다")
        return
    print(f"실행기 시작: {args.api} (끄려면 Ctrl+C)")
    warned, tick = False, 0
    while True:
        try:
            if tick % SYNC_EVERY == 0:
                sync_cases(args.api)
            job = call(args.api, "/skills/evals/jobs/claim")["job"]
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
