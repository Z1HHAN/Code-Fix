"""Server-owned, explicitly approved model profiles. No automatic fallback."""
import os
import re

from .config import ConfigError, read_json
from .live_model import LiveModel, LiveSettings, ModelBlocked, positive_decimal
from .providers import registered, validate_url
from .role_config import checked_file, digest

FIELDS = {'provider_id', 'model', 'base_url', 'api_key_env', 'approval_env',
          'input_price_env', 'output_price_env', 'request_seconds_env',
          'price_version', 'max_input_tokens', 'max_output_tokens', 'token_limit_parameter'}
CNY_FIELDS = {'currency', 'account_ref', 'billable_output_tokens', 'request_parameters'}
BASE_URL_ENV_PREFIX = 'env:'
ENV_NAME = re.compile(r'CODE_FIX_[A-Z][A-Z0-9_]+')


def config_base_url(profile):
    """载入期校验：字面地址立即校验；env: 引用只校验变量名，取值在使用前校验。

    主机白名单在 app/providers.py，改配置无法把密钥指向别家域名。
    """
    # 服务商必须先登记允许主机；未登记的服务商连档案都无法载入（env: 引用同样受限）。
    if not registered(profile.get('provider_id')):
        raise ConfigError('服务商未在 app/providers.py 登记允许主机')
    value = profile.get('base_url')
    if isinstance(value, str) and value.startswith(BASE_URL_ENV_PREFIX):
        name = value[len(BASE_URL_ENV_PREFIX):]
        if not ENV_NAME.fullmatch(name):
            raise ConfigError('服务商地址只允许 CODE_FIX 命名空间的环境变量引用')
        return name
    url = value.rstrip('/') if isinstance(value, str) else ''
    if not validate_url(profile.get('provider_id'), url):
        raise ConfigError('档案地址须为无凭证及查询参数的HTTPS地址，且主机属于该服务商白名单')
    return url


def resolved_base_url(profile):
    """使用期解析：env: 引用在此取值，并再次过服务商主机白名单（早于读取密钥）。"""
    value = profile.get('base_url')
    if isinstance(value, str) and value.startswith(BASE_URL_ENV_PREFIX):
        name = value[len(BASE_URL_ENV_PREFIX):]
        if not ENV_NAME.fullmatch(name):
            raise ModelBlocked('服务商地址只允许 CODE_FIX 命名空间的环境变量引用')
        raw = (os.environ.get(name) or '').strip()
        if not raw:
            raise ModelBlocked('服务商地址环境变量 ' + name + ' 未设置')
        value = raw
    url = str(value or '').rstrip('/')
    if not validate_url(profile.get('provider_id'), url):
        raise ModelBlocked('服务商地址须为无凭证及查询参数的HTTPS地址，且主机属于该服务商白名单')
    return url


def _amount(value):
    try:
        return positive_decimal(value)
    except ModelBlocked as exc:
        raise ConfigError(str(exc)) from None


def load_profiles(root):
    data = read_json(checked_file(root, 'configs/model_profiles.json'))
    cny = data.get('schema_version') == '1.1.0'
    expected = {'schema_version', 'profiles', 'accounts', 'currency', 'team_total_limit_cny'} if cny else {'schema_version', 'profiles'}
    if (set(data) != expected or data['schema_version'] not in {'1.0.0', '1.1.0'} or not isinstance(data['profiles'], dict)):
        raise ConfigError('模型档案格式无效')
    if cny:
        if data['currency'] != 'CNY' or not isinstance(data['accounts'], dict) or not data['accounts']:
            raise ConfigError('人民币账号配置无效')
        team = _amount(data['team_total_limit_cny'])
        total = 0
        for aid, account in data['accounts'].items():
            if (not re.fullmatch(r'[a-z][a-z0-9_\-]{0,63}', aid) or not isinstance(account, dict)
                    or set(account) != {'provider_id', 'api_key_env', 'total_limit_cny', 'warning_balance_cny'}):
                raise ConfigError('账号ID或字段无效')
            if not isinstance(account['provider_id'], str) or not account['provider_id']:
                raise ConfigError('账号服务商无效')
            if not isinstance(account['api_key_env'], str) or not re.fullmatch(r'CODE_FIX_[A-Z][A-Z0-9_]+', account['api_key_env']):
                raise ConfigError('账号密钥只允许环境变量引用')
            limit, warning = _amount(account['total_limit_cny']), _amount(account['warning_balance_cny'])
            if warning >= limit:
                raise ConfigError('提醒余额须小于账号额度')
            total += limit
        if team > total:
            raise ConfigError('队伍总额度不能超过账号累计额度之和')
    for ref, profile in data['profiles'].items():
        if not re.fullmatch(r'[a-z][a-z0-9_\-]{0,63}', ref) or not isinstance(profile, dict) or set(profile) != FIELDS | (CNY_FIELDS if cny else set()):
            raise ConfigError('模型档案ID或字段无效')
        if any(not isinstance(profile[k], str) or not profile[k].strip()
               for k in FIELDS - {'max_input_tokens', 'max_output_tokens'}):
            raise ConfigError('模型档案字符串字段无效')
        if not re.fullmatch(r'[a-z][a-z0-9_\-]{0,63}', profile['provider_id']):
            raise ConfigError('服务商ID无效')
        for field in ('api_key_env', 'approval_env', 'input_price_env', 'output_price_env', 'request_seconds_env'):
            if not re.fullmatch(r'CODE_FIX_[A-Z][A-Z0-9_]+', profile[field]):
                raise ConfigError('档案只允许CODE_FIX命名空间的环境变量引用')
        config_base_url(profile)
        if profile['token_limit_parameter'] not in {'max_tokens', 'max_completion_tokens'}:
            raise ConfigError('未实现的输出长度参数')
        if any(type(profile[k]) is not int or not 1 <= profile[k] <= 1_000_000
               for k in ('max_input_tokens', 'max_output_tokens')):
            raise ConfigError('档案输入输出上限无效')
        if cny:
            aid = profile['account_ref']
            if profile['currency'] != 'CNY' or not isinstance(aid, str) or aid not in data['accounts']:
                raise ConfigError('档案未绑定已登记人民币账号')
            account = data['accounts'][aid]
            if profile['api_key_env'] != account['api_key_env'] or profile['provider_id'] != account['provider_id']:
                raise ConfigError('模型与账号服务商/密钥引用不匹配')
            bound = profile['billable_output_tokens']
            if bound is not None and (type(bound) is not int or not profile['max_output_tokens'] <= bound <= 2_097_152):
                raise ConfigError('计费输出上限须覆盖可见回复与思考token')
            params = profile['request_parameters']
            if not isinstance(params, dict) or set(params) - {'enable_thinking'} or any(type(v) is not bool for v in params.values()):
                raise ConfigError('未实现的供应商参数')
    return data


def profile_settings(profile, registry=None):
    if os.environ.get('CODE_FIX_LIVE_APPROVED') != '1' or os.environ.get(profile['approval_env']) != '1':
        raise ModelBlocked('本进程尚未启用已批准模型调用配置')
    # 端点白名单校验早于读取密钥：配错的地址不会触发任何凭证读取。
    base_url = resolved_base_url(profile)
    from .local_credentials import read_secret
    try:
        key = read_secret(profile['api_key_env'])
    except (ValueError, OSError):
        raise ModelBlocked('本机加密凭证不可读取；需原Windows用户或本机环境变量配置') from None
    if not key:
        raise ModelBlocked('模型档案密钥未配置')
    if profile['provider_id'] == 'personal':
        from .personal import settings_for_profile
        return settings_for_profile(profile, key, base_url)
    timeout = positive_decimal(os.environ.get(profile['request_seconds_env']))
    if timeout > 120:
        raise ModelBlocked('模型单请求时限不能超过120秒')
    native = {}
    if profile.get('currency') == 'CNY':
        if profile['billable_output_tokens'] is None:
            raise ModelBlocked('模型计费输出上限未核实，拒绝未经预留的思考费用')
        account = registry['accounts'][profile['account_ref']]
        native = dict(currency='CNY', account_id=profile['account_ref'],
                      account_limit=positive_decimal(account['total_limit_cny']),
                      warning_balance=positive_decimal(account['warning_balance_cny']),
                      team_limit=positive_decimal(registry['team_total_limit_cny']),
                      billable_output_tokens=profile['billable_output_tokens'],
                      request_parameters=profile['request_parameters'])
    return LiveSettings(api_key=key, model=profile['model'], base_url=base_url,
        timeout=float(timeout), daily_limit=native.get('team_limit') or positive_decimal(os.environ.get('CODE_FIX_DAILY_USD')),
        input_per_million=positive_decimal(os.environ.get(profile['input_price_env'])),
        output_per_million=positive_decimal(os.environ.get(profile['output_price_env'])),
        max_input_tokens=profile['max_input_tokens'], max_output_tokens=profile['max_output_tokens'],
        provider_id=profile['provider_id'], price_version=profile['price_version'],
        token_limit_parameter=profile['token_limit_parameter'], **native)


class ModelRouter:
    mode = 'live'

    @staticmethod
    def resolve(config):
        """Validate every reachable binding before any API request or execution."""
        settings = {}
        registry = None
        for ref, binding in config.snapshot['models'].items():
            if binding['binding'] == 'live_env':
                settings[ref] = LiveSettings.from_env()
            else:
                registry = registry or load_profiles(config.root)
                profile = registry['profiles'].get(binding['profile_ref'])
                if profile is None:
                    raise ConfigError('角色引用的模型档案不存在')
                settings[ref] = profile_settings(profile, registry)
        accounts = {}
        for setting in settings.values():
            if setting.account_id:
                previous = accounts.setdefault(setting.api_key, setting.account_id)
                if previous != setting.account_id:
                    raise ModelBlocked('同一密钥不能登记为两个账号并重复额度')
        return settings, registry

    def __init__(self, config, ledger, resolved=None, *, transports=None):
        settings, registry = resolved or self.resolve(config)
        settings = dict(settings)
        from dataclasses import replace
        # Saved configurations allocate a separate model_ref for each role.
        for role, override in config.role_overrides.items():
            ref = config.require(role)['model_ref']
            if sum(a['model_ref'] == ref for a in config.agents.values()) != 1:
                raise ConfigError('角色覆盖须使用独立模型引用；请保存新配置')
            generation = override.get('generation', {})
            current = settings[ref]
            settings[ref] = replace(current,
                max_output_tokens=generation.get('max_output_tokens', current.max_output_tokens),
                request_parameters={**(current.request_parameters or {}),
                                    **{k: v for k, v in generation.items() if k != 'max_output_tokens'}})
        self.registry = registry
        self.config, self.ledger = config, ledger
        if transports is not None and (set(transports) != set(settings) or any(t is None for t in transports.values())):
            raise ConfigError('离线传输必须覆盖全部模型绑定，禁止回退真实网络')
        self.clients = {ref: LiveModel(s, ledger, transport=(transports or {}).get(ref))
                        for ref, s in settings.items()}
        self.mode = 'test_transport' if transports is not None else 'live'
        self.registry_sha256 = digest(registry) if registry else None

    def binding(self, role):
        ref = self.config.require(role)['model_ref']
        s = self.clients[ref].settings
        binding = {'model_ref': ref, **self.config.snapshot['models'][ref], 'mode': self.mode,
                'provider_id': s.provider_id, 'model': s.model, 'price_version': s.price_version,
                'base_url': s.base_url,
                'profiles_sha256': self.registry_sha256,
                'effective_parameters': {s.token_limit_parameter: s.max_output_tokens},
                'request_seconds': s.timeout, 'daily_shared_usd': str(s.daily_limit),
                'input_usd_per_million': str(s.input_per_million),
                'output_usd_per_million': str(s.output_per_million)}
        if s.currency == 'CNY':
            for old in ('daily_shared_usd', 'input_usd_per_million', 'output_usd_per_million'):
                binding.pop(old)
            binding.update(currency='CNY', account_id=s.account_id, total_account_cny=str(s.account_limit),
                           total_team_cny=str(s.team_limit), warning_balance_cny=str(s.warning_balance),
                           input_cny_per_million=str(s.input_per_million), output_cny_per_million=str(s.output_per_million),
                           billable_output_tokens=s.billable_output_tokens,
                           effective_parameters={s.token_limit_parameter: s.max_output_tokens, **(s.request_parameters or {})})
        if s.currency == 'USAGE':
            binding.update(currency=None, monetary_budget_enabled=False, daily_shared_usd=None,
                           input_usd_per_million=None, output_usd_per_million=None)
        binding['effective_parameters_sha256'] = digest(binding['effective_parameters'])
        return binding

    def call_for_role(self, role, messages, **kwargs):
        ref = self.config.require(role)['model_ref']
        return self.redact(self.clients[ref].call(messages, **kwargs))

    def redact(self, value):
        # A response from A could accidentally echo B's key: redact all bindings.
        for client in self.clients.values():
            value = client.redact(value)
        return value
