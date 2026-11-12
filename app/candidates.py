"""Apply only validated source replacements; tests and frozen inputs never change."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil

from .runner import code_version
from .security import validate_changes
from .tasks_v3 import FORBIDDEN, get_task


def apply_candidate(base, destination, changes, evidence_dir, *, project='leap'):
    base, destination, evidence_dir = Path(base), Path(destination), Path(evidence_dir)
    task = get_task(project)
    source = task.source
    from .source_edits import expand_changes
    changes = expand_changes(changes, work=base, source=source, forbidden=FORBIDDEN)
    validate_changes(changes, allowed_paths=(source,), forbidden_paths=FORBIDDEN, work_root=base)
    if len(changes) != 1:
        raise ValueError('当前单文件项目只接受一项替换')
    task.verify(base, candidate=True)
    if destination.exists():
        raise ValueError('不覆盖已有候选工作区')
    shutil.copytree(base, destination)
    old = (base / source).read_text(encoding='utf-8')
    new = changes[0]['new_content']
    (destination / source).write_text(new, encoding='utf-8', newline='\n')
    task.verify(destination, candidate=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    patch = ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile='a/'+source, tofile='b/'+source))
    (evidence_dir / 'patch.diff').write_text(patch, encoding='utf-8')
    record = {'project': project, 'base_version': code_version(base, (source,)), 'candidate_version': code_version(destination, (source,)),
              'test_suite_version': task.summary()['test_suite_version'],
              'frozen_file_sha256': {p: h for p, h in task.profile['approved_files'].items() if p != source},
              'changes': changes}
    # test_paths may name files or directories. Only hash registered test files.
    record['test_sha256'] = {
        path: hashlib.sha256((destination / path).read_bytes()).hexdigest()
        for path in sorted(task.profile['approved_files'])
        if any(path == test or path.startswith(test.rstrip('/') + '/')
               for test in task.profile['test_paths'])
    }
    (evidence_dir / 'candidate.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record
