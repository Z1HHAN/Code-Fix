"""Registered Python tasks. Asset preparation copies bytes; it never runs code."""
from dataclasses import dataclass
import json
from pathlib import Path
import shutil

from c_runner.runner import _snapshot, compute_test_suite_version
from .security import normalise_relpath
from .errors import InputValidationError

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ('tests', 'python_testcases', 'json_testcases', 'conftest.py', 'LICENSE',
             'correct_python_programs', '.git')


@dataclass(frozen=True)
class RegisteredTask:
    project: str
    data: dict

    @property
    def profile(self):
        return self.data['profile']

    @property
    def source(self):
        return self.profile['source_paths'][0]

    @property
    def requires_isolation(self):
        return self.data['requires_isolation']

    @property
    def admission_blocker(self):
        return self.data.get('admission_blocker')

    def summary(self):
        return {'project': self.project, 'title': self.data['title'], 'description': self.data['description'],
                'source_paths': self.profile['source_paths'], 'test_paths': self.profile['test_paths'],
                'expected_test_count': len(self.profile['expected_nodeids']),
                'test_suite_version': compute_test_suite_version(self.profile),
                'requires_isolation': self.requires_isolation, 'source': self.data['source'],
                'admission_blocker': self.admission_blocker,
                **({'registered_contract': self.data['registered_contract']} if self.data.get('registered_contract') else {}),
                'qualification_status': self.data.get('qualification_status', 'reviewed_fixture_or_legacy_registration')}

    def verify(self, workspace, *, candidate=False):
        actual = _snapshot(workspace)
        expected = self.profile['approved_files']
        if set(actual) != set(expected):
            raise ValueError('任务工作区包含额外文件或缺少登记文件')
        for name, digest in expected.items():
            if not (candidate and name == self.source) and actual[name] != digest:
                raise ValueError('任务原生测试、辅助文件或缺陷起点哈希不匹配：' + name)

    def prepare(self, destination):
        source = ROOT / normalise_relpath(self.profile['fixture_dir'])
        self.verify(source)
        destination = Path(destination)
        for path in (destination, *destination.absolute().parents):
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise ValueError('任务目录不能经过链接')
        shutil.copytree(source, destination)
        self.verify(destination)
        return destination.resolve()


def catalog():
    data = json.loads((ROOT / 'configs/tasks/catalog-v3.json').read_text(encoding='utf-8'))
    if data['schema_version'] != 1:
        raise ValueError('任务登记版本不支持')
    return data['projects']


def get_task(project='leap'):
    projects = catalog()
    if not isinstance(project, str) or project not in projects:
        raise InputValidationError('未知项目；请从已登记题目中选择')
    task = RegisteredTask(project, projects[project])
    profile = task.profile
    # Current V3 candidate protocol permits one source replacement. Keep the
    # shared profile list schema; multi-file repair needs separate approval.
    if len(profile['source_paths']) != 1 or task.source not in profile['approved_files']:
        raise ValueError('当前登记项目必须有一个允许修改的源文件')
    for path in (*profile['approved_files'], *profile['test_paths']):
        normalise_relpath(path)
        if path.startswith('-'):
            raise ValueError('登记测试路径不能为命令选项')
    if not profile['expected_nodeids'] or len(set(profile['expected_nodeids'])) != len(profile['expected_nodeids']):
        raise ValueError('预登记测试身份缺失或重复')
    return task


def list_tasks():
    return [get_task(project).summary() for project in catalog()]
