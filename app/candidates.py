"""Apply only validated source replacements; tests and frozen inputs never change."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil

from .runner import code_version
from .security import validate_changes

SOURCE = 'code/leap_year.py'


def apply_candidate(base, destination, changes, evidence_dir):
    base, destination, evidence_dir = Path(base), Path(destination), Path(evidence_dir)
    validate_changes(changes, allowed_paths=(SOURCE,), forbidden_paths=('tests',), work_root=base)
    if len(changes) != 1:
        raise ValueError('当前单文件项目只接受一项替换')
    # Never follow links when copying material into the execution workspace.
    for item in [base, *base.rglob('*')]:
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise ValueError('候选起点不能包含链接')
    if destination.exists():
        raise ValueError('不覆盖已有候选工作区')
    shutil.copytree(base, destination)
    old = (base / SOURCE).read_text(encoding='utf-8')
    new = changes[0]['new_content']
    (destination / SOURCE).write_text(new, encoding='utf-8', newline='\n')
    if (base / 'tests/test_leap.py').read_bytes() != (destination / 'tests/test_leap.py').read_bytes():
        raise ValueError('测试快照被修改')
    evidence_dir.mkdir(parents=True, exist_ok=True)
    patch = ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile='a/'+SOURCE, tofile='b/'+SOURCE))
    (evidence_dir / 'patch.diff').write_text(patch, encoding='utf-8')
    record = {'base_version': code_version(base, (SOURCE,)), 'candidate_version': code_version(destination, (SOURCE,)),
              'test_sha256': hashlib.sha256((destination / 'tests/test_leap.py').read_bytes()).hexdigest(),
              'changes': changes}
    (evidence_dir / 'candidate.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record
