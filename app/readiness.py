import os
import shutil
import sys
from pathlib import Path

from .live_model import ModelBlocked
from .model_profiles import ModelRouter
from .role_config import RoleConfig
from .config import ConfigError


def readiness():
    blockers = []
    try:
        config = RoleConfig(Path(__file__).resolve().parents[1])
        config.preflight()
        ModelRouter.resolve(config)
        model_configured = True
    except (ModelBlocked, ConfigError) as exc:
        model_configured = False
        blockers.append(str(exc))
    remote = bool(os.environ.get('CODE_FIX_EXECUTION_URL'))
    if remote:
        from .remote_runner import RemoteRunner
        from .docker_runner import IsolationBlocked
        try:
            runner = RemoteRunner()
            runner.close()
        except (IsolationBlocked, ValueError) as exc:
            blockers.append(str(exc))
    if not remote and not shutil.which('docker'):
        blockers.append('找不到Docker；候选不会回退到宿主执行')
    if os.environ.get('CODE_FIX_EXECUTION_APPROVED') != '1':
        blockers.append('不可信代码执行授权未配置')
    return {'python': sys.version.split()[0], 'baseline_available': sys.version_info[:2] == (3, 14),
            'model_configured': model_configured, 'docker_available': bool(shutil.which('docker')),
            'isolation_verified': False, 'note': '本次环境概览未执行隔离探针；历史结果请查看所选任务的探针记录，每次修复任务启动仍须重新验证',
            'execution_mode': 'remote-docker' if remote else 'local-docker',
            'execution_endpoint': os.environ.get('CODE_FIX_EXECUTION_URL') if remote else None,
            'blockers': blockers}
