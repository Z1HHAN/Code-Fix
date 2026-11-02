"""B's public facade over C's reviewed-fixture executor.

No arbitrary argv or generated-code execution is exposed by this interface.
Historical short hashes remain historical; new calls share C's full hash.
"""
from __future__ import annotations

import json
from pathlib import Path

import c_runner

CODE_VERSION_SCHEME = 'sha256-canonical-files-v1'


def code_version(root: Path, paths) -> str:
    return c_runner.compute_code_version(root, paths)


def prepare_fixture(profile_id: str, destination: Path) -> Path:
    return c_runner.prepare_fixture(profile_id, destination)


def run_tests(*, run_id: str, task_id: str, workspace: Path, test_profile: str,
              timeout_seconds: float, code_version: str, artifact_dir: Path,
              phase: str = 'development', execution_mode: str = 'reviewed_fixture') -> dict:
    result = c_runner.run_tests(
        run_id=run_id, task_id=task_id, workspace=workspace, test_profile=test_profile,
        timeout_seconds=timeout_seconds, code_version=code_version,
        artifact_dir=artifact_dir, phase=phase, execution_mode=execution_mode,
    )
    return {**result, 'code_version_scheme': CODE_VERSION_SCHEME}


def append_event(path: Path, event: dict) -> None:
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
