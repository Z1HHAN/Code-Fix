"""Server-owned, explicitly approved model profiles. No automatic fallback."""
import os
import re
from urllib.parse import urlsplit

from .config import ConfigError, read_json
from .live_model import LiveModel, LiveSettings, ModelBlocked, positive_decimal
from .role_config import checked_file, digest

FIELDS = {'provider_id', 'model', 'base_url', 'api_key_env', 'approval_env',
          'input_price_env', 'output_price_env', 'request_seconds_env',
          'price_version', 'max_input_tokens', 'max_output_tokens', 'token_limit_parameter'}


def load_profiles(root):
    data = read_json(checked_file(root, 'configs/model_profiles.json'))
    if (set(data) != {'schema_version', 'profiles'} or data['schema_version'] != '1.0.0'
            or not isinstance(data['profiles'], dict)):
        raise ConfigError('模型档案格式无效')
    for ref, profile in data['profiles'].items():
        if not re.fullmatch(r'[a-z][a-z0-9_\-]{0,63}', ref) or not isinstance(profile, dict) or set(profile) != FIELDS:
            raise ConfigError('模型档案ID或字段无效')
        if any(not isinstance(profile[k], str) or not profile[k].strip()
               for k in FIELDS - {'max_input_tokens', 'max_output_tokens'}):
            raise ConfigError('模型档案字符串字段无效')
        if not re.fullmatch(r'[a-z][a-z0-9_\-]{0,63}', profile['provider_id']):
            raise ConfigError('服务商ID无效')
        for field in ('api_key_env', 'approval_env', 'input_price_env', 'output_price_env', 'request_seconds_env'):
            if not re.fullmatch(r'CODE_FIX_[A-Z][A-Z0-9_]+', profile[field]):
                raise ConfigError('档案只允许CODE_FIX命名空间的环境变量引用')
        url = urlsplit(profile['base_url'])
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment):
            raise ConfigError('档案地址须为无凭证及查询参数的HTTPS地址')
        if profile['token_limit_parameter'] not in {'max_tokens', 'max_completion_tokens'}:
            raise ConfigError('未实现的输出长度参数')
        if any(type(profile[k]) is not int or not 1 <= profile[k] <= 1_000_000
               for k in ('max_input_tokens', 'max_output_tokens')):
            raise ConfigError('档案输入输出上限无效')
    return data


def profile_settings(profile):
    if os.environ.get('CODE_FIX_LIVE_APPROVED') != '1' or os.environ.get(profile['approval_env']) != '1':
        raise ModelBlocked('队伍或模型档案费用授权未批准')
    key = os.environ.get(profile['api_key_env'])
    if not key:
        raise ModelBlocked('模型档案密钥未配置')
    timeout = positive_decimal(os.environ.get(profile['request_seconds_env']))
    if timeout > 120:
        raise ModelBlocked('模型单请求时限不能超过120秒')
    return LiveSettings(api_key=key, model=profile['model'], base_url=profile['base_url'].rstrip('/'),
        timeout=float(timeout), daily_limit=positive_decimal(os.environ.get('CODE_FIX_DAILY_USD')),
        input_per_million=positive_decimal(os.environ.get(profile['input_price_env'])),
        output_per_million=positive_decimal(os.environ.get(profile['output_price_env'])),
        max_input_tokens=profile['max_input_tokens'], max_output_tokens=profile['max_output_tokens'],
        provider_id=profile['provider_id'], price_version=profile['price_version'],
        token_limit_parameter=profile['token_limit_parameter'])


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
                settings[ref] = profile_settings(profile)
        return settings, registry

    def __init__(self, config, ledger, resolved=None, *, transports=None):
        settings, registry = resolved or self.resolve(config)
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
        return {'model_ref': ref, **self.config.snapshot['models'][ref], 'mode': self.mode,
                'provider_id': s.provider_id, 'model': s.model, 'price_version': s.price_version,
                'base_url': s.base_url,
                'profiles_sha256': self.registry_sha256,
                'effective_parameters': {s.token_limit_parameter: s.max_output_tokens},
                'request_seconds': s.timeout, 'daily_shared_usd': str(s.daily_limit),
                'input_usd_per_million': str(s.input_per_million),
                'output_usd_per_million': str(s.output_per_million)}

    def call_for_role(self, role, messages, **kwargs):
        ref = self.config.require(role)['model_ref']
        return self.redact(self.clients[ref].call(messages, **kwargs))

    def redact(self, value):
        # A response from A could accidentally echo B's key: redact all bindings.
        for client in self.clients.values():
            value = client.redact(value)
        return value
