#!/usr/bin/env python3
"""esports-dev 스킬 사용 기록 훅.

스킬 이름·성공 여부·실행 시간·토큰 수·시각만 남긴다. 요청 문장과 대화 내용은 읽어도 저장하지 않는다.

- PostToolUse(Skill): esports-dev 스킬이 불리면 기록을 시작한다
- Stop: 그 턴이 끝나면 걸린 시간과 토큰을 채워 "성공"으로 닫는다
- UserPromptSubmit: 닫히지 않은 기록이 있는데 새 요청이 오면 (중간에 끊긴 것) "실패"로 닫는다

기록은 항상 ~/.claude/esports-dev/usage.jsonl 에 먼저 적고, esports-api 가 켜져 있으면 안 보낸 줄을 모아
POST /skills/usage 로 보낸다 (주소는 ESPORTS_API, 기본 http://localhost:8002). 같은 id 를 다시 보내면 api 가 덮어쓴다.
훅이 실패해도 Claude Code 작업을 막지 않도록 항상 0 으로 끝난다.
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PLUGIN = "esports-dev"
HOME = Path(os.environ.get("ESPORTS_DEV_HOME", Path.home() / ".claude" / PLUGIN))
LOG, SENT, OPEN = HOME / "usage.jsonl", HOME / "usage.sent", HOME / "open"
API = os.environ.get("ESPORTS_API", "http://localhost:8002")


def open_path(session_id: str) -> Path:
    return OPEN / f"{session_id}.json"


def load_open(session_id: str) -> list[dict]:
    try:
        return json.loads(open_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def write(record: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def start(event: dict) -> None:
    """스킬 호출 직후: 열린 기록을 만든다."""
    skill = (event.get("tool_input") or {}).get("skill") or ""
    if not skill.startswith(PLUGIN + ":"):
        return
    record = {"id": event.get("tool_use_id") or f"{event.get('session_id')}-{time.time()}",
              "skill": skill, "repo": Path(event.get("cwd") or "").name,
              "used_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": None}
    session = event.get("session_id") or "unknown"
    OPEN.mkdir(parents=True, exist_ok=True)
    items = load_open(session) + [{**record, "t0": time.time()}]
    open_path(session).write_text(json.dumps(items), encoding="utf-8")
    write(record)


def measure(transcript: str | None, tool_use_id: str) -> dict:
    """세션 기록에서 스킬 호출 뒤의 토큰 수와 도구 호출·오류 수만 센다 (내용은 읽지 않고 버린다)."""
    usage, calls, errors, seen = {}, 0, 0, False
    try:
        lines = Path(transcript).read_text(encoding="utf-8").splitlines()
    except (OSError, TypeError):
        return {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") or {}
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        if entry.get("type") == "assistant":
            ids = [b.get("id") for b in blocks if b.get("type") == "tool_use"]
            if tool_use_id in ids:
                seen = True
            if seen:
                calls += sum(1 for i in ids if i != tool_use_id)
                if message.get("id") and message.get("usage"):
                    usage[message["id"]] = message["usage"]  # 같은 응답이 여러 줄이면 마지막 값
        elif seen and entry.get("type") == "user":
            errors += sum(1 for b in blocks if b.get("type") == "tool_result" and b.get("is_error"))
    if not seen:
        return {}
    total_in = sum(u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                   + u.get("cache_creation_input_tokens", 0) for u in usage.values())
    return {"input_tokens": total_in, "output_tokens": sum(u.get("output_tokens", 0) for u in usage.values()),
            "tool_calls": calls, "tool_errors": errors}


def close(event: dict, ok: bool) -> None:
    """열린 기록을 닫는다: 걸린 시간·토큰을 채워 같은 id 로 한 줄 더 적는다."""
    session = event.get("session_id") or "unknown"
    items = load_open(session)
    if not items:
        return
    for item in items:
        t0 = item.pop("t0", None)
        item.update(measure(event.get("transcript_path"), item["id"]))
        item["ok"] = ok
        item["duration_sec"] = round(time.time() - t0, 1) if t0 else None
        write(item)
    open_path(session).unlink(missing_ok=True)


def send() -> None:
    """안 보낸 줄을 api 로 보낸다. api 가 꺼져 있으면 다음에 다시 시도한다."""
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
        sent = int(SENT.read_text()) if SENT.exists() else 0
    except (OSError, ValueError):
        return
    pending = [json.loads(x) for x in lines[sent:sent + 200] if x.strip()]
    if not pending:
        return
    req = urllib.request.Request(API.rstrip("/") + "/skills/usage", data=json.dumps({"records": pending}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=1.5).read()
    except OSError:
        return
    SENT.write_text(str(min(len(lines), sent + 200)))


def main() -> None:
    if os.environ.get("ESPORTS_EVAL"):  # 선택 평가(evals/run.py)가 띄운 세션은 실제 사용이 아니다
        return
    event = json.load(sys.stdin)
    name = event.get("hook_event_name")
    if name == "PostToolUse" and event.get("tool_name") == "Skill":
        start(event)
    elif name == "Stop":
        close(event, ok=True)
    elif name == "UserPromptSubmit":
        close(event, ok=False)
    else:
        return
    send()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 — 기록 실패가 작업을 막으면 안 된다
        pass
    sys.exit(0)
