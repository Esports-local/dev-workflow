"""플러그인과 테스트 요청 검사 (모델을 부르지 않는다). CI 가 푸시·PR 마다 돌린다.

    python3 evals/check.py

- 매니페스트(marketplace.json · plugin.json · hooks.json)가 JSON 이고 경로가 맞는가
- 스킬마다 SKILL.md 머리말의 name 이 폴더 이름과 같고, description 이 있고 1,024자를 넘지 않는가
- 테스트 요청(evals/cases/*.jsonl): 필드, id 중복, 파일 이름과 skill, kind 와 expect 의 짝, 정답 스킬이 실제로 있는가
- policy.json 이 POLICY.md 에 적힌 이름을 모두 갖는가
- 훅(usage.py)이 평가 실행(ESPORTS_EVAL=1)에서 아무것도 안 하고 0 으로 끝나는가, dev.sh 문법
"""

import json
import os
import py_compile
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PLUGIN = "esports-dev"
PLUGIN_DIR = REPO / "plugins" / PLUGIN
MAX_CHARS = 1024
CASE_KEYS = {"id", "skill", "kind", "split", "repo", "prompt", "expect"}
REPOS = {"esports-analyzer", "esports-api", "esports-ui"}

errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        fail(f"{path.relative_to(REPO)}: JSON 을 읽지 못함 ({e})")
        return None


def check_manifests() -> None:
    market = load_json(REPO / ".claude-plugin" / "marketplace.json")
    if market:
        for p in market.get("plugins", []):
            if not (REPO / p.get("source", "")).is_dir():
                fail(f"marketplace.json: 플러그인 {p.get('name')} 의 source 폴더가 없음: {p.get('source')}")
    plugin = load_json(PLUGIN_DIR / ".claude-plugin" / "plugin.json")
    if plugin and plugin.get("name") != PLUGIN:
        fail(f"plugin.json: name 이 {PLUGIN} 이 아님")
    load_json(PLUGIN_DIR / "hooks" / "hooks.json")


def check_skills() -> set[str]:
    names = set()
    for path in sorted((PLUGIN_DIR / "skills").glob("*/SKILL.md")):
        folder = path.parent.name
        text = path.read_text(encoding="utf-8")
        head = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not head:
            fail(f"{folder}/SKILL.md: 머리말(---)이 없음")
            continue
        meta = dict(re.findall(r"^(\w+): (.*)$", head.group(1), re.M))
        if meta.get("name") != folder:
            fail(f"{folder}/SKILL.md: name({meta.get('name')})이 폴더 이름과 다름")
        desc = meta.get("description", "").strip()
        if desc[:1] in (">", "|"):
            fail(f"{folder}/SKILL.md: description 은 한 줄로 쓴다 (여러 줄 YAML 은 improve.py 가 고치지 못함)")
            continue
        if desc.startswith('"'):
            try:
                desc = json.loads(desc)
            except ValueError:
                fail(f"{folder}/SKILL.md: description 따옴표가 깨짐")
                continue
        if not desc:
            fail(f"{folder}/SKILL.md: description 이 없음")
        elif len(desc) > MAX_CHARS:
            fail(f"{folder}/SKILL.md: description 이 {len(desc)}자 (제한 {MAX_CHARS}자)")
        names.add(f"{PLUGIN}:{folder}")
    if not names:
        fail("스킬이 하나도 없음")
    return names


def check_cases(skills: set[str]) -> int:
    seen, prompts, count = set(), set(), 0
    for path in sorted((HERE / "cases").glob("*.jsonl")):
        owner = f"{PLUGIN}:{path.stem}"
        if owner not in skills:
            fail(f"cases/{path.name}: 그런 스킬이 없음 (스킬을 뺐으면 테스트 요청 파일도 지운다)")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            where = f"cases/{path.name}:{n}"
            try:
                c = json.loads(line)
            except ValueError:
                fail(f"{where}: JSON 이 아님")
                continue
            count += 1
            missing = CASE_KEYS - set(c)
            if missing:
                fail(f"{where}: 빠진 필드 {sorted(missing)}")
                continue
            if c["id"] in seen:
                fail(f"{where}: id 중복 {c['id']}")
            seen.add(c["id"])
            if c["prompt"] in prompts:
                fail(f"{where}: 같은 문장의 요청이 이미 있음")
            prompts.add(c["prompt"])
            if c["skill"] != owner:
                fail(f"{where}: skill({c['skill']})이 파일 이름과 다름")
            if c["kind"] not in ("should", "should_not") or c["split"] not in ("train", "val"):
                fail(f"{where}: kind 또는 split 값이 잘못됨")
            if (c["kind"] == "should") != (c["expect"] == c["skill"]):
                fail(f"{where}: kind={c['kind']} 인데 expect={c['expect']}")
            if c["expect"] is not None and c["expect"] not in skills:
                fail(f"{where}: 정답 스킬이 없음 ({c['expect']})")
            if c["repo"] not in REPOS:
                fail(f"{where}: repo({c['repo']})가 잘못됨")
    return count


def check_policy() -> None:
    policy = load_json(HERE / "policy.json") or {}
    documented = set(re.findall(r"^\| `(\w+)` \|", (REPO / "POLICY.md").read_text(encoding="utf-8"), re.M))
    if documented - set(policy):
        fail(f"policy.json 에 POLICY.md 의 기준값이 빠짐: {sorted(documented - set(policy))}")


def check_code() -> None:
    for path in sorted(HERE.glob("*.py")) + [PLUGIN_DIR / "hooks" / "usage.py"]:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as e:
            fail(f"{path.relative_to(REPO)}: 문법 오류 {e.msg}")
    res = subprocess.run(["bash", "-n", str(PLUGIN_DIR / "scripts" / "dev.sh")], capture_output=True, text=True)
    if res.returncode:
        fail(f"dev.sh 문법 오류: {res.stderr.strip()}")
    hook_input = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Skill",
                             "tool_input": {"skill": f"{PLUGIN}:local-up"}, "session_id": "ci"})
    res = subprocess.run([sys.executable, str(PLUGIN_DIR / "hooks" / "usage.py")], input=hook_input, text=True,
                         capture_output=True, env={**os.environ, "ESPORTS_EVAL": "1", "ESPORTS_DEV_HOME": "/nonexistent"})
    if res.returncode:
        fail(f"usage.py 가 0 으로 끝나지 않음 ({res.returncode}): {res.stderr.strip()[:200]}")


def main():
    check_manifests()
    skills = check_skills()
    cases = check_cases(skills)
    check_policy()
    check_code()
    if errors:
        print(f"검사 실패 {len(errors)}건:")
        print("\n".join(f"- {e}" for e in errors))
        sys.exit(1)
    print(f"통과: 스킬 {len(skills)}개, 테스트 요청 {cases}개")


if __name__ == "__main__":
    main()
