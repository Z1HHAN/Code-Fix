"""Freeze comparison inputs before any candidate execution or model request."""
import hashlib
import os
import subprocess

from .model_profiles import load_profiles, resolved_base_url
from .config import ConfigError
from .live_model import ModelBlocked, positive_decimal
from .role_config import digest
from .role_overrides import effective_parameters

SOURCE_PATHS = ('app', 'c_runner', 'docker', 'configs', 'prompts', 'knowledge', 'ui',
                'tests', 'scripts', 'acceptance_tests', 'requirements.txt', 'pyproject.toml')


def code_identity(root):
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True,
                              capture_output=True, timeout=15).stdout
    try:
        commit = git('rev-parse', 'HEAD').decode().strip()
        names = set(git('ls-files', '-z', '--', *SOURCE_PATHS).split(b'\0'))
        names.update(git('ls-files', '-z', '--others', '--exclude-standard', '--', *SOURCE_PATHS).split(b'\0'))
        files = {name.decode('utf-8'): hashlib.sha256((root / name.decode('utf-8')).read_bytes()).hexdigest()
                 for name in names if name and (root / name.decode('utf-8')).is_file()}
        return {'commit': commit, 'source_sha256': digest(files),
                'dirty': bool(git('status', '--porcelain', '--untracked-files=all', '--', *SOURCE_PATHS))}
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return {'commit': None, 'source_sha256': None, 'dirty': None}


def freeze(root, task, definition, config, runner, model):
    from .split_runner import SplitDockerRunner
    from .model_profiles import ModelRouter
    registry = load_profiles(root)
    profiles = registry['profiles']
    def numeric_env(name):
        try:
            return str(positive_decimal(os.environ.get(name)))
        except ModelBlocked:
            return None
    bindings = {}
    for role, agent in config.agents.items():
        ref = config.snapshot['models'][agent['model_ref']].get('profile_ref')
        p = profiles.get(ref)
        try:
            endpoint = resolved_base_url(p) if p else None
        except (ConfigError, ModelBlocked):
            endpoint = None
        bindings[role] = {'profile_ref': ref,
                         'base_url': endpoint,
                         'currency': p.get('currency', 'USD') if p else None,
                         'parameters': effective_parameters(p, config.role_overrides.get(role, {})) if p else None,
                         'input_price': numeric_env(p['input_price_env']) if p else None,
                         'output_price': numeric_env(p['output_price_env']) if p else None,
                         'request_seconds': numeric_env(p['request_seconds_env']) if p else None}
    identity = {'code': code_identity(root), 'project': task['project'], 'description': task['description'],
                'task_limits': {k: task[k] for k in ('max_rounds', 'max_calls', 'total_seconds')},
                'approved_files': definition.profile['approved_files'],
                'test_suite_version': definition.summary()['test_suite_version'],
                'roles': config.snapshot, 'role_overrides': config.role_overrides,
                'prompts_sha256': {r: p[1] for r, p in config.prompts.items()},
                'profiles_sha256': digest(registry), 'bindings': bindings,
                'image': getattr(runner, 'image', None)}
    # Type and transport originate in the running process, not a submitted label.
    real = type(runner) is SplitDockerRunner and (model is None or type(model) is ModelRouter and model.mode == 'live')
    return {'schema_version': 1, 'strategy': task.get('strategy', 'dynamic'),
            'execution_kind': 'real' if real else 'offline', 'identity': identity,
            'identity_sha256': digest(identity)}
