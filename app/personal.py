"""Personal desktop configuration, isolated from historical team profiles."""
import json
import os
from pathlib import Path

from .live_model import LiveSettings, ModelBlocked, positive_decimal


def settings_for_profile(profile, key, base_url):
    if os.environ.get('CODE_FIX_PERSONAL_MODE') != '1':
        raise ModelBlocked('个人模式尚未启用')
    usage = os.environ.get('CODE_FIX_PERSONAL_BUDGET') == 'usage'
    return LiveSettings(
        api_key=key, model=profile['model'], base_url=base_url, timeout=60,
        daily_limit=None if usage else positive_decimal(os.environ.get('CODE_FIX_PERSONAL_DAILY_USD')),
        input_per_million=None if usage else positive_decimal(os.environ.get('CODE_FIX_PERSONAL_INPUT_PRICE')),
        output_per_million=None if usage else positive_decimal(os.environ.get('CODE_FIX_PERSONAL_OUTPUT_PRICE')),
        max_input_tokens=profile['max_input_tokens'], max_output_tokens=profile['max_output_tokens'],
        provider_id='personal', price_version=profile['price_version'],
        token_limit_parameter=profile['token_limit_parameter'], currency='USAGE' if usage else 'USD')


def configure_runtime(root, value):
    """Called by the desktop child before the HTTP server is created; no calls."""
    from .providers import validate_url
    if (os.environ.get('CODE_FIX_PERSONAL_MODE') != '1'
            or not validate_url('personal', value['base_url'])
            or not isinstance(value['model'], str) or not 1 <= len(value['model']) <= 200
            or type(value['max_output_tokens']) is not int or not 256 <= value['max_output_tokens'] <= 32768
            or value['token_parameter'] not in {'max_tokens', 'max_completion_tokens'}):
        raise ValueError('个人模型配置无效')
    root = Path(root)
    template = json.loads((root / 'configs/desktop-roles.json').read_text())
    template['models'] = {'primary': {'binding': 'approved_profile', 'profile_ref': 'personal_model'}}
    template['config_version'] = '1.1.0'
    profile = {
        'provider_id': 'personal', 'model': value['model'], 'base_url': value['base_url'],
        'api_key_env': 'CODE_FIX_PERSONAL_KEY', 'approval_env': 'CODE_FIX_PERSONAL_APPROVED',
        'input_price_env': 'CODE_FIX_PERSONAL_INPUT_PRICE', 'output_price_env': 'CODE_FIX_PERSONAL_OUTPUT_PRICE',
        'request_seconds_env': 'CODE_FIX_PERSONAL_TIMEOUT', 'price_version': value['price_version'],
        'max_input_tokens': 64000, 'max_output_tokens': value['max_output_tokens'],
        'token_limit_parameter': value['token_parameter'],
    }
    for name, payload in {
        'agents.json': {'runtime_v3': template},
        'model_profiles.json': {'schema_version': '1.0.0', 'profiles': {'personal_model': profile}},
        'account_labels.json': {'schema_version': 1, 'labels': {}},
    }.items():
        (root / 'configs' / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
