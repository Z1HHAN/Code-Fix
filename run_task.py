import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "task_spec.json"
TEST_TIMEOUT_SECONDS = 60


class RunBlocked(RuntimeError):
    pass


def load_task_spec():
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    allowed = data.get("allowed_paths")
    if not isinstance(allowed, list) or len(allowed) != 1:
        raise RunBlocked("task_spec.json 必须提供且只允许一个目标文件")
    limits = data.get("limits") or {}
    return {
        "task_id": str(data["task_id"]),
        "description": str(data["description"]),
        "allowed_paths": tuple(str(x).replace("\\", "/") for x in allowed),
        "max_repair_rounds": int(limits.get("max_repair_rounds", data.get("max_repair_rounds", 3))),
        "timeout_seconds": int(limits.get("test_timeout_seconds", TEST_TIMEOUT_SECONDS)),
        "max_tool_calls": limits.get("max_tool_calls"),
        "daily_cost_limit": limits.get("daily_cost_limit"),
        "expected_test_count": data.get("expected_test_count"),
        "test_command": tuple(str(data.get("test_command", "python -m pytest tests/ -v")).split()),
    }


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def add_event(path, **data):
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps({"ts": now(), **data}, ensure_ascii=False) + "\n")


def code_version(work_dir, relative_path):
    import hashlib
    digest = hashlib.sha256((work_dir / relative_path).read_bytes()).hexdigest()
    return "sha256:" + digest[:16]


def safe_environment():
    blocked = {
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN", "GH_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    }
    env = {key: value for key, value in os.environ.items() if key not in blocked}
    env.update({"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def parse_count(output, word):
    matches = re.findall(rf"(\d+)\s+{word}\b", output)
    return int(matches[-1]) if matches else None


def classify(returncode, output, expected_count, timed_out=False):
    lower = output.lower()
    passed = parse_count(output, "passed")
    failed = parse_count(output, "failed")
    skipped = parse_count(output, "skipped")
    if timed_out:
        return "timeout", "inconclusive", "timeout", passed, failed, skipped
    if "error collecting" in lower or "modulenotfounderror" in lower or "no tests ran" in lower:
        return "error", "inconclusive", "environment_error", passed, failed, skipped
    if "syntaxerror" in lower:
        return "failed", "failed", "syntax_error", passed, failed, skipped
    if returncode == 0 and passed and failed == 0 and skipped in (None, 0) and (expected_count is None or passed == expected_count):
        return "ok", "passed", "passed", passed, failed, skipped
    if returncode == 0:
        return "error", "inconclusive", "not_reproduced", passed, failed, skipped
    if "assert" in lower or failed:
        return "failed", "failed", "assertion_failed", passed, failed, skipped
    return "error", "inconclusive", "code_runtime_error", passed, failed, skipped


def run_tests(work_dir, log_file, call_id, spec):
    started = time.monotonic()
    timed_out = False
    try:
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        process = subprocess.Popen(list(spec["test_command"]), cwd=work_dir, env=safe_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=creationflags)
        try:
            stdout, stderr = process.communicate(timeout=spec["timeout_seconds"])
            returncode = process.returncode
        except subprocess.TimeoutExpired as error:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, text=True)
            else:
                process.kill()
            stdout = error.stdout or ""
            stderr = (error.stderr or "") + "\nTIMEOUT after " + str(spec["timeout_seconds"]) + "s\n"
            returncode = None
            timed_out = True
        output = stdout + stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        output = stdout + stderr + f"\nTIMEOUT after {spec['timeout_seconds']}s\n"
        returncode = None

    log_file.write_text(output, encoding="utf-8")
    status, verification, failure, passed, failed, skipped = classify(
        returncode, output, spec["expected_test_count"], timed_out
    )
    return {
        "call_id": call_id,
        "tool": "run_tests",
        "status": status,
        "verification_status": verification,
        "failure_class": failure,
        "exit_code": returncode,
        "counts": {"passed": passed, "failed": failed, "skipped": skipped},
        "log_path": log_file.relative_to(ROOT).as_posix(),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "output": output,
    }


def extract_code(response):
    text = response.strip()
    match = re.fullmatch(r"```(?:python)?\s*\n(.*?)\n```", text, re.S | re.I)
    if match:
        text = match.group(1).strip()
    if not text:
        raise ValueError("模型返回空代码")
    compile(text, "target_file.py", "exec")
    return text + "\n"


def request_repair(client, model, spec, test_output, current_code):
    prompt = f"""请修复任务：{spec['description']}\n\n只允许修改：{spec['allowed_paths'][0]}\n禁止修改 tests/。\n\n测试输出：\n{test_output}\n\n当前代码：\n{current_code}\n\n只返回目标文件的完整 Python 代码，不要解释。"""
    response = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
    content = response.choices[0].message.content
    if not content:
        raise ValueError("模型没有返回内容")
    return content


def main():
    spec = load_task_spec()
    if os.getenv("CODE_FIX_ISOLATION_READY", "").lower() not in {"1", "true", "yes"}:
        raise RunBlocked("隔离未就绪：请设置 CODE_FIX_ISOLATION_READY=true，并确认容器或可丢弃独立虚拟机")
    if not spec["max_tool_calls"]:
        raise RunBlocked("task_spec.json 未配置 max_tool_calls")
    if spec["daily_cost_limit"] is None:
        raise RunBlocked("daily_cost_limit 尚未由 A/B/C 确认，不能自动调用模型")
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL")
    if not api_key or not model:
        raise RunBlocked("请设置 OPENAI_API_KEY 和 OPENAI_MODEL")
    target = Path(spec["allowed_paths"][0])
    source_file = ROOT / target
    if not source_file.is_file():
        raise RunBlocked(f"目标文件不存在: {target}")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / "runs" / run_id
    work_dir = run_dir / "work"
    run_dir.mkdir(parents=True)
    source_package = Path(spec["allowed_paths"][0]).parts[0]
    shutil.copytree(ROOT / source_package, work_dir / source_package)
    shutil.copytree(ROOT / "tests", work_dir / "tests")
    events = run_dir / "events.jsonl"
    add_event(events, event="run_started", run_id=run_id, task_id=spec["task_id"], isolation_ready=True, target_file=target.as_posix())
    calls = 0
    before = run_tests(work_dir, run_dir / "test_before.log", "t001", spec)
    calls += 1
    before_public = {k: v for k, v in before.items() if k != "output"}
    (run_dir / "toolrun_before.json").write_text(json.dumps(before_public, ensure_ascii=False, indent=2), encoding="utf-8")
    add_event(events, event="test_run", run_id=run_id, task_id=spec["task_id"], **before_public)
    if before["failure_class"] in {"environment_error", "timeout", "not_reproduced"}:
        add_event(events, event="final", run_id=run_id, result="stopped", reason=before["failure_class"], final_result="not_run")
        print(f"运行暂停/停止：{before['failure_class']}，记录：{run_dir}")
        return
    client_options = {"api_key": api_key}
    if os.getenv("OPENAI_BASE_URL"):
        client_options["base_url"] = os.getenv("OPENAI_BASE_URL")
    client = OpenAI(**client_options)
    target_file = work_dir / target
    for round_number in range(1, spec["max_repair_rounds"] + 1):
        if calls + 2 > spec["max_tool_calls"]:
            add_event(events, event="final", run_id=run_id, result="stopped", reason="达到工具调用上限", final_result="not_run")
            print(f"达到工具调用上限，记录：{run_dir}")
            return
        current = target_file.read_text(encoding="utf-8")
        model_response = request_repair(client, model, spec, before["output"], current)
        calls += 1
        response_file = run_dir / f"model_response_round_{round_number}.txt"
        response_file.write_text(model_response, encoding="utf-8")
        add_event(events, event="model_call", run_id=run_id, round=round_number, response_path=response_file.name)
        try:
            target_file.write_text(extract_code(model_response), encoding="utf-8")
        except (SyntaxError, ValueError) as error:
            add_event(events, event="decision", run_id=run_id, round=round_number, action="repair", failure_class="invalid_model_output", reason=str(error), rounds_left=spec["max_repair_rounds"] - round_number)
            before["output"] = str(error)
            continue
        add_event(events, event="patch_applied", run_id=run_id, round=round_number, file=target.as_posix(), code_version=code_version(work_dir, (target.as_posix(),)))
        after = run_tests(work_dir, run_dir / f"test_after_round_{round_number}.log", f"t{calls + 1:03d}", spec)
        calls += 1
        after_public = {k: v for k, v in after.items() if k != "output"}
        (run_dir / f"toolrun_after_round_{round_number}.json").write_text(json.dumps(after_public, ensure_ascii=False, indent=2), encoding="utf-8")
        add_event(events, event="test_run", run_id=run_id, task_id=spec["task_id"], round=round_number, **after_public)
        if after["verification_status"] == "passed":
            add_event(events, event="final", run_id=run_id, status="stopped", dev_result="passed", final_result="not_run", reason="开发测试通过，等待独立验收", rounds=round_number)
            print(f"开发测试通过；独立验收仍未运行，记录：{run_dir}")
            return
        if after["failure_class"] in {"environment_error", "timeout", "not_reproduced"}:
            add_event(events, event="final", run_id=run_id, result="stopped", reason=after["failure_class"], final_result="not_run")
            print(f"运行暂停/停止：{after['failure_class']}，记录：{run_dir}")
            return
        before = after
    add_event(events, event="final", run_id=run_id, result="stopped", reason="达到最大修复轮数", final_result="not_run")
    print(f"达到最大修复轮数，记录：{run_dir}")


if __name__ == "__main__":
    try:
        main()
    except RunBlocked as error:
        print(f"运行已阻止：{error}")
        raise SystemExit(2)
