import os
import shutil
import sys

from .live_model import LiveSettings, ModelBlocked


def readiness():
    blockers = []
    try:
        LiveSettings.from_env()
        model_configured = True
    except ModelBlocked as exc:
        model_configured = False
        blockers.append(str(exc))
    if not shutil.which('docker'):
        blockers.append('找不到Docker；候选不会回退到宿主执行')
    if os.environ.get('CODE_FIX_EXECUTION_APPROVED') != '1':
        blockers.append('不可信代码执行授权未配置')
    return {'python': sys.version.split()[0], 'baseline_available': sys.version_info[:2] == (3, 14),
            'model_configured': model_configured, 'docker_available': bool(shutil.which('docker')),
            'isolation_verified': False, 'note': '每次修复任务启动时运行独立隔离探针；配置存在不等于验证通过',
            'blockers': blockers}
