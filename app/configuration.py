"""Local, immutable task configuration. Accept profile IDs, never credentials or URLs."""
from copy import deepcopy
import json
import re
from pathlib import Path

from .config import ConfigError
from .errors import InputValidationError
from .model_profiles import load_profiles, profile_settings
from .live_model import ModelBlocked
from .role_config import RoleConfig, digest, SCHEMAS
from .run_store import now, validate_task
from .runtime_limits import runtime_limits

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationStore:
    def __init__(self, store, root=ROOT):
        self.store, self.root = store, Path(root)
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS configurations (config_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')

    def catalog(self):
        registry = load_profiles(self.root)
        current = RoleConfig(self.root)
        rows = []
        for ref, profile in registry['profiles'].items():
            try:
                profile_settings(profile, registry)
                ready, reason = True, '本机调用配置可解析；未发请求验证'
            except (ModelBlocked, ConfigError, OSError, ValueError):
                ready, reason = False, '授权、凭据、价格或计费上限未就绪；未发请求'
            rows.append(dict(profile_ref=ref, provider=profile['provider_id'], model=profile['model'],
                             account=profile.get('account_ref'), configured=ready, note=reason,
                             billing_bound_known=profile.get('billable_output_tokens') is not None))
        return {'profiles': rows, 'bindings': {role: current.snapshot['models'][agent['model_ref']].get('profile_ref')
                for role, agent in current.agents.items()},
                'limits': runtime_limits(),
                'strategies': [{'value': 'dynamic', 'label': '动态编排', 'enabled': True},
                               {'value': 'fixed', 'label': '固定顺序（尚未开放）', 'enabled': False}]}

    def validate(self, value):
        if not isinstance(value, dict) or set(value) != {'bindings', 'parameters', 'strategy'}:
            raise InputValidationError('仅接受角色绑定、任务参数和策略')
        if value['strategy'] != 'dynamic':
            raise InputValidationError('当前仅支持动态编排；固定顺序尚未开放')
        if not isinstance(value['bindings'], dict) or set(value['bindings']) != set(SCHEMAS):
            raise InputValidationError('必须保留四个角色的模型绑定')
        params = value['parameters']
        if not isinstance(params, dict) or set(params) != {'max_rounds', 'max_calls', 'total_seconds'}:
            raise InputValidationError('任务参数字段不完整')
        validate_task(params)
        registry = load_profiles(self.root)
        runtime = deepcopy(RoleConfig(self.root).snapshot)
        runtime['config_version'], runtime['models'] = '1.1.0', {}
        for agent in runtime['agents']:
            role = agent['agent_id']
            ref = value['bindings'][role]
            if not isinstance(ref, str) or ref not in registry['profiles']:
                raise InputValidationError('只能选择已登记的模型档案')
            runtime['models'][role] = {'binding': 'approved_profile', 'profile_ref': ref}
            agent['model_ref'] = role
        config = RoleConfig(self.root, runtime)
        config.preflight()
        return {'bindings': deepcopy(value['bindings']), 'parameters': deepcopy(params), 'strategy': 'dynamic',
                'roles': config.snapshot, 'profiles_sha256': digest(registry),
                'prompts_sha256': {r: p[1] for r, p in config.prompts.items()},
                'note': '结构校验通过；不代表模型请求、费用预留或隔离资格通过'}

    def save(self, value):
        snapshot = self.validate(value)
        identifier = 'cfg-' + digest(snapshot)
        record = {**snapshot, 'config_id': identifier, 'created_at': now()}
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO configurations VALUES (?,?)',
                       (identifier, json.dumps(record, ensure_ascii=False)))
        return self.get(identifier)

    def get(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'cfg-[a-f0-9]{64}', identifier):
            raise InputValidationError('非法配置编号')
        with self.store.connect() as db:
            row = db.execute('SELECT payload FROM configurations WHERE config_id=?', (identifier,)).fetchone()
        if row is None:
            raise InputValidationError('配置不存在')
        record = json.loads(row[0])
        content = {k: v for k, v in record.items() if k not in {'config_id', 'created_at'}}
        if 'cfg-' + digest(content) != identifier:
            raise InputValidationError('配置内容校验失败')
        return record

    def runtime(self, identifier):
        snapshot = self.get(identifier)
        config = RoleConfig(self.root, snapshot['roles'])
        if (digest(load_profiles(self.root)) != snapshot['profiles_sha256'] or
                {r: p[1] for r, p in config.prompts.items()} != snapshot['prompts_sha256']):
            raise ConfigError('模型档案或提示词已变更；请校验并保存新配置后创建新任务')
        return config
