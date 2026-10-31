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
TARGET_RELATIVE = Path("leap_app/leap_year.py")
TEST_TIMEOUT_SECONDS = 60
MAX_REPAIR_ROUNDS = 3


def timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def add_event(events_file, **data):
    event = {"ts": timestamp(), **data}
    with events_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_counts(output):
    # pytest -v 的末尾通常包含「3 passed, 1 failed」等摘要。
    passed_matches = re.findall(r"(\d+)\s+passed\b", output)
    failed_matches = re.findall(r"(\d+)\s+failed\b", output)

    return {
        "passed": int(passed_matches[-1]) if passed_matches else 0,
        "failed": int(failed_matches[-1]) if failed_matches else 0,
    }


def run_tests(work_dir, log_file, call_id):
    start = time.monotonic()

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-v"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TEST_TIMEOUT_SECONDS,
        )
        output = result.stdout + result.stderr
        status = "passed" if result.returncode == 0 else "failed"
        exit_code = result.returncode

    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or b""
        stderr = error.stderr or b""

        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")

        output = stdout + stderr + "\n测试超过 60 秒，已终止。\n"
        status = "timeout"
        exit_code = None

    duration_ms = round((time.monotonic() - start) * 1000)
    log_file.write_text(output, encoding="utf-8")
    counts = read_counts(output)

    tool_run = {
        "call_id": call_id,
        "tool": "run_tests",
        "status": status,
        "exit_code": exit_code,
        "counts": counts,
        "log_path": log_file.relative_to(ROOT).as_posix(),
        "duration_ms": duration_ms,
    }

    return tool_run, output


def extract_python_code(response_text):
    text = response_text.strip()

    # 兼容模型用 Markdown 代码块返回的情况。
    match = re.fullmatch(
        r"```(?:python)?\s*\n(.*?)\n```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match:
        text = match.group(1).strip()

    if not text:
        raise ValueError("模型返回了空内容")

    # 写入副本前先检查 Python 语法；语法错误也会保留原始回复作为证据。
    compile(text, str(TARGET_RELATIVE), "exec")
    return text + "\n"


def request_repair(client, model_name, test_output, current_code):
    prompt = f"""请修复闰年判断函数 is_leap。

允许修改的唯一文件：leap_app/leap_year.py
不能修改 tests/ 下的文件，也不能修改其他文件。

失败测试的完整输出：
{test_output}

当前 leap_app/leap_year.py 的完整内容：
{current_code}

请只返回 leap_app/leap_year.py 的完整 Python 代码。
不要返回解释、文件名或其他文件的内容。
"""

    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
    )

    content = response.choices[0].message.content
    if not content:
        raise ValueError("模型没有返回文本内容")
    return content


def main():
    source_file = ROOT / TARGET_RELATIVE
    source_tests = ROOT / "tests"

    if not source_file.is_file():
        raise FileNotFoundError(f"找不到 {source_file}")
    if not source_tests.is_dir():
        raise FileNotFoundError(f"找不到 {source_tests}")

    api_key = os.getenv("OPENAI_API_KEY")
    model_name = os.getenv("OPENAI_MODEL")
    base_url = os.getenv("OPENAI_BASE_URL")

    if not api_key:
        raise RuntimeError("请先设置环境变量 OPENAI_API_KEY")
    if not model_name:
        raise RuntimeError("请先设置环境变量 OPENAI_MODEL")

    client_options = {"api_key": api_key}
    if base_url:
        client_options["base_url"] = base_url
    client = OpenAI(**client_options)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / "runs" / run_id
    work_dir = run_dir / "work"
    work_dir.mkdir(parents=True)

    # 只在 work 中运行和修改；项目原始目录保持不变。
    shutil.copytree(ROOT / "leap_app", work_dir / "leap_app")
    shutil.copytree(source_tests, work_dir / "tests")

    target_file = work_dir / TARGET_RELATIVE
    events_file = run_dir / "events.jsonl"

    add_event(
        events_file,
        event="run_started",
        run_id=run_id,
        target_file=TARGET_RELATIVE.as_posix(),
    )

    try:
        # 证据①：修复前的真实测试结果。
        call_number = 1
        log_file = run_dir / "test_before.log"
        tool_run, test_output = run_tests(
            work_dir, log_file, f"t{call_number:03d}"
        )
        (run_dir / "toolrun_before.json").write_text(
            json.dumps(tool_run, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        add_event(
            events_file,
            event="test_run",
            call_id=tool_run["call_id"],
            status=tool_run["status"],
            passed=tool_run["counts"]["passed"],
            failed=tool_run["counts"]["failed"],
            log_path=log_file.name,
        )

        if tool_run["status"] == "passed":
            add_event(
                events_file,
                event="final",
                result="success",
                rounds=0,
                reason="初始测试已全部通过",
            )
            print(f"初始测试已通过；运行记录：{run_dir}")
            return

        if tool_run["status"] == "timeout":
            add_event(
                events_file,
                event="final",
                result="stopped",
                rounds=0,
                reason="初始测试超时",
            )
            print(f"初始测试超时；运行记录：{run_dir}")
            return

        for round_number in range(1, MAX_REPAIR_ROUNDS + 1):
            current_code = target_file.read_text(encoding="utf-8")

            # 证据②：保留模型未经修改的原始回复。
            model_response = request_repair(
                client, model_name, test_output, current_code
            )
            response_file = run_dir / f"model_response_round_{round_number}.txt"
            response_file.write_text(model_response, encoding="utf-8")

            add_event(
                events_file,
                event="model_call",
                round=round_number,
                response_path=response_file.name,
            )

            try:
                new_code = extract_python_code(model_response)
            except (SyntaxError, ValueError) as error:
                # 模型给出无效代码时，记录原因并进入下一轮。
                test_output = f"模型上一次返回的代码无法使用：{error}\n\n{model_response}"
                add_event(
                    events_file,
                    event="decision",
                    round=round_number,
                    failure_class="语法错误或空回复",
                    action="重修",
                    reason=str(error),
                    rounds_left=MAX_REPAIR_ROUNDS - round_number,
                )
                continue

            target_file.write_text(new_code, encoding="utf-8")
            add_event(
                events_file,
                event="patch_applied",
                round=round_number,
                file=TARGET_RELATIVE.as_posix(),
            )

            # 证据③：修复后的真实测试结果。
            call_number += 1
            log_file = run_dir / f"test_after_round_{round_number}.log"
            tool_run, test_output = run_tests(
                work_dir, log_file, f"t{call_number:03d}"
            )
            (run_dir / f"toolrun_after_round_{round_number}.json").write_text(
                json.dumps(tool_run, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            add_event(
                events_file,
                event="test_run",
                round=round_number,
                call_id=tool_run["call_id"],
                status=tool_run["status"],
                passed=tool_run["counts"]["passed"],
                failed=tool_run["counts"]["failed"],
                log_path=log_file.name,
            )

            if tool_run["status"] == "passed":
                add_event(
                    events_file,
                    event="final",
                    result="success",
                    rounds=round_number,
                )
                print(f"修复成功；运行记录：{run_dir}")
                return

            if tool_run["status"] == "timeout":
                add_event(
                    events_file,
                    event="final",
                    result="stopped",
                    rounds=round_number,
                    reason="修复后测试超时",
                )
                print(f"修复后测试超时；运行记录：{run_dir}")
                return

            add_event(
                events_file,
                event="decision",
                round=round_number,
                failure_class="测试失败",
                action="重修" if round_number < MAX_REPAIR_ROUNDS else "停止",
                reason=f"测试仍失败，失败数：{tool_run['counts']['failed']}",
                rounds_left=MAX_REPAIR_ROUNDS - round_number,
            )

        add_event(
            events_file,
            event="final",
            result="stopped",
            rounds=MAX_REPAIR_ROUNDS,
            reason="达到最大修复轮数",
        )
        print(f"达到最大修复轮数；运行记录：{run_dir}")

    except Exception as error:
        add_event(
            events_file,
            event="final",
            result="stopped",
            reason=f"{type(error).__name__}: {error}",
        )
        print(f"运行中断：{error}")
        print(f"已保留运行记录：{run_dir}")
        raise


if __name__ == "__main__":
    main()
