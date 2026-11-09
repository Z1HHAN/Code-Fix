"""Local, immutable task configuration. Accept profile IDs, never credentials or URLs."""
from copy import deepcopy
import json
import re
import os
from pathlib import Path

from .config import ConfigError, read_json
from .errors import InputValidationError
from .model_profiles import load_profiles, profile_settings
from .live_model import ModelBlocked
from .role_config import RoleConfig, digest, SCHEMAS
from .run_store import now, validate_task
from .runtime_limits import runtime_limits
from .role_overrides import capabilities, validate_overrides, effective_parameters


def profile_checks(profile):
    """Local diagnostics only. Never expose secrets or exception text to the browser."""
    from .local_credentials import read_secret
    from .live_model import positive_decimal
    checks = []
    approved = os.environ.get('CODE_FIX_LIVE_APPROVED') == '1' and os.environ.get(profile['approval_env']) == '1'
    checks.append({'name': '调用授权', 'ok': approved, 'status': '已启用' if approved else '未启用'})
    try:
        credential = bool(read_secret(profile['api_key_env']))
        status = '可读取' if credential else '未配置'
    except (ValueError, OSError):
        credential, status = False, '无法读取'
    checks.append({'name': '凭据读取', 'ok': credential, 'status': status})
    try:
        positive_decimal(os.environ.get(profile['input_price_env']))
        positive_decimal(os.environ.get(profile['output_price_env']))
        priced = True
    except (ModelBlocked, ValueError):
        priced = False
    checks.append({'name': '计费价格', 'ok': priced, 'status': '已配置' if priced else '未正确配置'})
    bounded = profile.get('currency') != 'CNY' or profile.get('billable_output_tokens') is not None
    checks.append({'name': '计费输出上限', 'ok': bounded, 'status': '已登记' if bounded else '待核实'})
    return checks

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationStore:
    def __init__(self, store, root=ROOT):
        self.store, self.root = store, Path(root)
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS configurations (config_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')

    def catalog(self):
        registry = load_profiles(self.root)
        # Display metadata is separate from immutable billing/profile hashes.
        labels_path = self.root / 'configs/account_labels.json'
        labels = read_json(labels_path) if labels_path.exists() else {'schema_version': 1, 'labels': {}}
        if (not isinstance(labels, dict) or set(labels) != {'schema_version', 'labels'}
                or labels['schema_version'] != 1 or not isinstance(labels['labels'], dict)
                or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 80
                       or any(ord(c) < 32 for c in v) for v in labels['labels'].values())):
            raise ConfigError('账号显示名称配置无效')
        accounts, counts = [], {}
        for aid, account in registry.get('accounts', {}).items():
            provider = account['provider_id']
            counts[provider] = counts.get(provider, 0) + 1
            accounts.append({'account_id': aid, 'provider': provider,
                             'display_name': labels['labels'].get(aid, f'账号{counts[provider]}')})
        current = RoleConfig(self.root)
        rows = []
        for ref, profile in registry['profiles'].items():
            checks = profile_checks(profile)
            try:
                profile_settings(profile, registry)
                ready, reason = True, '本机调用配置可解析；未发请求验证'
            except (ModelBlocked, ConfigError, OSError, ValueError):
                ready, reason = False, '授权、凭据、价格或计费上限未就绪；未发请求'
            rows.append(dict(profile_ref=ref, provider=profile['provider_id'], model=profile['model'],
                             account=profile.get('account_ref'), configured=ready, note=reason,
                             billing_bound_known=profile.get('billable_output_tokens') is not None,
                             checks=checks, capabilities=capabilities(profile)))
        return {'accounts': accounts, 'profiles': rows, 'bindings': {role: current.snapshot['models'][agent['model_ref']].get('profile_ref')
                for role, agent in current.agents.items()},
                'limits': runtime_limits(),
                'strategies': [{'value': 'dynamic', 'label': '动态编排', 'enabled': True},
                               {'value': 'fixed', 'label': '固定顺序', 'enabled': True}]}

    def validate(self, value):
        if (not isinstance(value, dict) or not {'bindings', 'parameters', 'strategy'} <= set(value)
                or set(value) - {'bindings', 'parameters', 'strategy', 'role_overrides'}):
            raise InputValidationError('仅接受角色绑定、任务参数和策略')
        if not isinstance(value['strategy'], str) or value['strategy'] not in {'dynamic', 'fixed'}:
            raise InputValidationError('策略须为fixed/dynamic')
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
        result = {'bindings': deepcopy(value['bindings']), 'parameters': deepcopy(params), 'strategy': value['strategy'],
                'roles': config.snapshot, 'profiles_sha256': digest(registry),
                'prompts_sha256': {r: p[1] for r, p in config.prompts.items()},
                'note': '结构校验通过；不代表模型请求、费用预留或隔离资格通过'}
        if 'role_overrides' in value:
            overrides = validate_overrides(value['role_overrides'], value['bindings'], registry)
            result.update(schema_version=2, role_overrides=overrides,
                          capabilities_sha256=digest({ref: capabilities(p) for ref, p in registry['profiles'].items()}),
                          effective_roles={role: {'task_instruction': overrides.get(role, {}).get('task_instruction', ''),
                              'parameters': effective_parameters(registry['profiles'][ref], overrides.get(role, {}))}
                              for role, ref in value['bindings'].items()})
        return result

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
        config = RoleConfig(self.root, snapshot['roles'], role_overrides=snapshot.get('role_overrides'))
        if (digest(load_profiles(self.root)) != snapshot['profiles_sha256'] or
                {r: p[1] for r, p in config.prompts.items()} != snapshot['prompts_sha256']):
            raise ConfigError('模型档案或提示词已变更；请校验并保存新配置后创建新任务')
        if ('capabilities_sha256' in snapshot and snapshot['capabilities_sha256'] !=
                digest({ref: capabilities(p) for ref, p in load_profiles(self.root)['profiles'].items()})):
            raise ConfigError('参数能力已变更；请保存新配置')
        return config
