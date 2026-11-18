"""Personal desktop configuration, isolated from historical team profiles."""
import json
import hashlib
import os
from pathlib import Path

from .live_model import LiveSettings, ModelBlocked, positive_decimal


def settings_for_profile(profile, key, base_url):
    if os.environ.get('CODE_FIX_PERSONAL_MODE') != '1':
        raise ModelBlocked('个人模式尚未启用')
    usage = os.environ.get('CODE_FIX_PERSONAL_BUDGET') == 'usage'
    price = profile.get('personal_pricing')
    authorization_id = os.environ.get('CODE_FIX_PERSONAL_AUTHORIZATION_ID')
    authorization_limit = os.environ.get('CODE_FIX_PERSONAL_AUTHORIZATION_LIMIT')
    if bool(authorization_id) != bool(authorization_limit):
        raise ModelBlocked('累计授权编号和金额上限须同时配置')
    if authorization_id and (usage or not price or price.get('currency') != 'CNY'):
        raise ModelBlocked('原累计授权须使用人民币金额预算及对应模型价格')
    if price and not usage:
        from .personal_pricing import validate_price
        from urllib.parse import urlsplit
        price = validate_price(price)
        if price['model'] != profile['model'] or price['endpoint_host'] != urlsplit(base_url).hostname:
            raise ModelBlocked('价格型号或服务商地址与实际调用不匹配，请重新导入')
        return LiveSettings(api_key=key, model=profile['model'], base_url=base_url, timeout=60,
            daily_limit=positive_decimal(os.environ.get('CODE_FIX_PERSONAL_DAILY_LIMIT')),
            input_per_million=None, output_per_million=None,
            max_input_tokens=min(profile['max_input_tokens'], price['tiers'][-1]['max_input_tokens']), max_output_tokens=profile['max_output_tokens'],
            billable_output_tokens=(profile['max_output_tokens'] + 81920 if price['mode'] == 'thinking' and profile['model'].startswith(('qwen-plus', 'qwen3.5-plus')) else None),
            provider_id='personal', price_version=price['version'], token_limit_parameter=profile['token_limit_parameter'],
            currency=price['currency'], personal_pricing=price,
            request_parameters=({'enable_thinking': price['mode'] == 'thinking'}
                if profile['model'].startswith(('qwen-plus', 'qwen3.5-plus')) else None),
            authorization_id=authorization_id,
            authorization_limit=positive_decimal(authorization_limit) if authorization_id else None)
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
    models = value.get('models', [value['model']])
    if (not isinstance(models, list) or len(models) > 12 or any(
            not isinstance(m, str) or not m.strip() or len(m) > 200 or any(c in m for c in '\r\n\0') for m in models)):
        raise ValueError('个人模型列表无效')
    profiles = {'personal_model': profile}
    for model in dict.fromkeys(models):
        if model == value['model']:
            continue
        ref = 'personal_model_' + hashlib.sha256(model.encode()).hexdigest()[:16]
        profiles[ref] = {**profile, 'model': model}
        template['models'][ref] = {'binding': 'approved_profile', 'profile_ref': ref}
    prices = value.get('model_prices') or {}
    for p in profiles.values():
        if p['model'] in prices:
            from .personal_pricing import validate_price
            p['personal_pricing'] = validate_price(prices[p['model']])
            p['price_version'] = p['personal_pricing']['version']
            p['currency'] = p['personal_pricing']['currency']
        elif prices and value.get('monetary_budget'):
            raise ModelBlocked('启用逐模型价格后，每个登记模型均须导入价格：' + p['model'])
    for name, payload in {
        'agents.json': {'runtime_v3': template},
        'model_profiles.json': {'schema_version': '1.0.0', 'profiles': profiles},
        'account_labels.json': {'schema_version': 1, 'labels': {}},
    }.items():
        (root / 'configs' / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
