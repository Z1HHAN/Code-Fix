"""Run the Day 1 C checks and save real, independently inspectable evidence.

Run with the Python interpreter in which pytest is installed:
    python scripts/run_c_checks.py

This is reference verification only. It does not call a model or claim an
automated repair. All test runs happen in new copies under runs/.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET


SHANGHAI = timezone(timedelta(hours=8))
TIMEOUT_SECONDS = 60


def now() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="milliseconds")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def hashes(directory: Path) -> dict[str, str]:
    """Hash source files, excluding caches created by Python or pytest."""
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
        and not any(part in {"__pycache__", ".pytest_cache"} for part in path.parts)
        and path.suffix != ".pyc"
    }


def read_junit(path: Path) -> dict:
    result = {"passed": 0, "failed": 0, "skipped": 0, "failures": [], "errors": []}
    if not path.exists():
        result["parse_error"] = "JUnit XML was not produced."
        return result
    try:
        cases = ET.parse(path).getroot().findall(".//testcase")
    except (OSError, ET.ParseError) as error:
        result["parse_error"] = str(error)
        return result
    for case in cases:
        name = case.get("name", "")
        if case.find("failure") is not None:
            result["failed"] += 1
            result["failures"].append(name)
        elif case.find("error") is not None:
            result["failed"] += 1
            result["errors"].append(name)
        elif case.find("skipped") is not None:
            result["skipped"] += 1
        else:
            result["passed"] += 1
    return result


def run_tests(root: Path, run_dir: Path, kind: str, call_id: str) -> dict:
    work_dir = run_dir / f"work_{kind}"
    junit_path = run_dir / f"{kind}.junit.xml"
    command = [
        sys.executable, "-m", "pytest", "tests/", "-v",
        f"--junitxml=../{junit_path.name}", "--color=no",
        "-p", "no:debugging",
    ]
    environment = os.environ.copy()
    overrides = {
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_ADDOPTS": "",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    environment.update(overrides)
    started_at = now()
    started = time.perf_counter()
    exit_code = None
    problem = None
    timed_out = False
    try:
        process = subprocess.run(
            command, cwd=work_dir, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=TIMEOUT_SECONDS, check=False,
        )
        stdout, stderr = process.stdout, process.stderr
        exit_code = process.returncode
    except subprocess.TimeoutExpired as error:
        stdout, stderr = error.stdout or b"", error.stderr or b""
        timed_out = True
        problem = f"Test process exceeded {TIMEOUT_SECONDS} seconds and was terminated."
    except OSError as error:
        stdout, stderr = b"", b""
        problem = f"Could not start test process: {error}"
    duration_ms = round((time.perf_counter() - started) * 1000)
    finished_at = now()

    stdout_path = run_dir / f"{kind}.stdout.txt"
    stderr_path = run_dir / f"{kind}.stderr.txt"
    log_path = run_dir / f"{kind}.log.txt"
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    log_path.write_text(
        "STDOUT\n" + stdout.decode("utf-8", errors="replace")
        + "\nSTDERR\n" + stderr.decode("utf-8", errors="replace")
        + (f"\nRUNNER NOTE\n{problem}\n" if problem else ""),
        encoding="utf-8",
    )
    parsed = read_junit(junit_path)
    if timed_out:
        status = "timeout"
    elif problem or "parse_error" in parsed:
        status = "error"
    else:
        status = "passed" if exit_code == 0 else "failed"
    tool_run = {
        "call_id": call_id,
        "tool": "run_tests",
        "status": status,
        "exit_code": exit_code,
        "counts": {"passed": parsed["passed"], "failed": parsed["failed"]},
        "log_path": log_path.relative_to(root).as_posix(),
        "duration_ms": duration_ms,
    }
    tool_run_path = run_dir / f"{kind}.toolrun.json"
    write_json(tool_run_path, tool_run)
    return {
        "kind": kind,
        "started_at": started_at,
        "finished_at": finished_at,
        "command": command,
        "cwd": work_dir.relative_to(root).as_posix(),
        "environment_overrides": overrides,
        "timeout_seconds": TIMEOUT_SECONDS,
        "tool_run": tool_run,
        "tool_run_path": tool_run_path.relative_to(root).as_posix(),
        "stdout_path": stdout_path.relative_to(root).as_posix(),
        "stderr_path": stderr_path.relative_to(root).as_posix(),
        "junit_path": junit_path.relative_to(root).as_posix(),
        "junit": parsed,
        "runner_problem": problem,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 14):
        raise SystemExit("Python 3.14 is required.")
    root = args.root.resolve()
    required = ["code/__init__.py", "code/leap_year.py", "tests/test_leap.py", "reference/leap_year.py"]
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        print("Missing required files: " + ", ".join(missing), file=sys.stderr)
        return 1
    try:
        pytest_version = importlib.metadata.version("pytest")
    except importlib.metadata.PackageNotFoundError:
        print("pytest is not installed in this Python interpreter.", file=sys.stderr)
        return 1

    originals_before = {name: hashes(root / name) for name in ("code", "tests", "reference")}
    run_id = "C-" + datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    for kind in ("bad", "reference"):
        work_dir = run_dir / f"work_{kind}"
        for name in ("code", "tests"):
            shutil.copytree(
                root / name, work_dir / name,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
            )
    shutil.copyfile(root / "reference/leap_year.py", run_dir / "work_reference/code/leap_year.py")

    events_path = run_dir / "events.jsonl"

    def event(event_name: str, **details: object) -> None:
        with events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"ts": now(), "event": event_name, "scope": "c_reference_verification", **details}, ensure_ascii=False) + "\n")

    event("c_check_started", run_id=run_id, model_api_called=False)
    results = {}
    for kind, call_id in (("bad", "c001"), ("reference", "c002")):
        event("c_test_started", kind=kind)
        result = run_tests(root, run_dir, kind, call_id)
        results[kind] = result
        event("c_test_finished", kind=kind, **result["tool_run"])

    originals_after = {name: hashes(root / name) for name in ("code", "tests", "reference")}
    copy_hashes = {
        kind: {name: hashes(run_dir / f"work_{kind}" / name) for name in ("code", "tests")}
        for kind in ("bad", "reference")
    }
    bad, reference = results["bad"], results["reference"]
    validations = {
        "bad_counts_are_3_passed_1_failed": bad["tool_run"]["counts"] == {"passed": 3, "failed": 1},
        "bad_exit_code_is_1": bad["tool_run"]["exit_code"] == 1,
        "bad_failure_is_1900_only": bad["junit"]["failures"] == ["test_1900_is_not_leap"],
        "reference_counts_are_4_passed_0_failed": reference["tool_run"]["counts"] == {"passed": 4, "failed": 0},
        "reference_exit_code_is_0": reference["tool_run"]["exit_code"] == 0,
        "no_errors_skips_or_missing_junit": all(
            not result["junit"]["errors"] and result["junit"]["skipped"] == 0
            and "parse_error" not in result["junit"] and result["runner_problem"] is None
            for result in results.values()
        ),
        "original_files_unchanged": originals_before == originals_after,
        "both_test_copies_unchanged": all(copy_hashes[kind]["tests"] == originals_before["tests"] for kind in copy_hashes),
        "bad_code_copy_unchanged": copy_hashes["bad"]["code"] == originals_before["code"],
        "reference_code_matches_provided_reference": copy_hashes["reference"]["code"].get("leap_year.py") == originals_before["reference"]["leap_year.py"],
    }
    succeeded = all(validations.values())
    manifest = {
        "run_id": run_id,
        "created_at": bad["started_at"],
        "completed_at": now(),
        "timezone": "Asia/Shanghai (+08:00)",
        "c_environment_reference_verification": True,
        "model_api_called": False,
        "reference_source": "manual_provided_reference",
        "result": "passed" if succeeded else "failed",
        "python_version": sys.version,
        "python_executable": sys.executable,
        "pytest_version": pytest_version,
        "paths_relative_to": "project_root",
        "run_dir": run_dir.relative_to(root).as_posix(),
        "reference_path": "reference/leap_year.py",
        "original_hashes_before": originals_before,
        "original_hashes_after": originals_after,
        "copy_hashes_after": copy_hashes,
        "runs": results,
        "validations": validations,
    }
    write_json(run_dir / "manifest.json", manifest)
    event("c_check_finished", result=manifest["result"], model_api_called=False)
    report_lines = [
        "# C environment and reference checks", "",
        f"Run ID: `{run_id}`", f"Completed: {manifest['completed_at']}", "",
        "These are real local test runs. No model API was called. The passing code is the manual's provided reference answer.", "",
        "| Copy | Passed | Failed | Exit code | Status |", "| --- | ---: | ---: | ---: | --- |",
    ]
    for kind, result in results.items():
        record = result["tool_run"]
        report_lines.append(f"| {kind} | {record['counts']['passed']} | {record['counts']['failed']} | {record['exit_code']} | {record['status']} |")
    report_lines.extend(["", "Validation checks:", ""])
    report_lines.extend(f"- {'PASS' if passed else 'FAIL'}: {name}" for name, passed in validations.items())
    report_lines.extend(["", "See manifest.json for commands, source hashes and artifact paths. Each ToolRun points to its real test log.", ""])
    (run_dir / "report.md").write_text("\n".join(report_lines), encoding="utf-8")
    print(f"run_id: {run_id}")
    for kind, result in results.items():
        record = result["tool_run"]
        print(f"{kind}: {record['counts']['passed']} passed, {record['counts']['failed']} failed; exit_code={record['exit_code']}")
    print(f"C checks: {manifest['result']}")
    print(f"Evidence: {run_dir}")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
