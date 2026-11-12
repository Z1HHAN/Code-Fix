"""Reviewed parameter capabilities; no endpoint, credential or permission overrides."""
from copy import deepcopy
from urllib.parse import urlsplit

from .errors import InputValidationError

SOURCE = 'https://api-docs.siliconflow.cn/docs/api/chat-completions-post'
MODEL_SOURCE = 'https://www.siliconflow.com/blog/deepseek-v3-2-now-on-siliconflow-reasoning-first-model-built-for-agents'


def capabilities(profile):
    # Sampling was reviewed for this registered non-thinking deployment only.
    sampling = (profile['provider_id'] == 'siliconflow'
                and profile['model'] == 'Pro/deepseek-ai/DeepSeek-V3.2'
                and profile.get('request_parameters', {}).get('enable_thinking') is False)
    result = {
        'max_output_tokens': dict(supported=True, minimum=1, maximum=profile['max_output_tokens'],
                                  default=profile['max_output_tokens'], wire_name=profile['token_limit_parameter'],
                                  reason='沿用已审核档案的可见输出上限'),
        **{k: dict(supported=sampling, minimum=0 if k == 'temperature' else .01,
                   maximum=1, default=None, wire_name=k,
                   reason='空值沿用服务商默认；平台保守范围' if sampling else '此模型/模式尚未核实采样参数支持',
                   sources=[SOURCE, MODEL_SOURCE] if sampling else []) for k in ('temperature', 'top_p')}}
    if (profile['provider_id'] == 'personal'
            and urlsplit(profile['base_url']).hostname == 'maas.qianwenaiapi.com'
            and profile['model'] in {'deepseek-v4.1-flash', 'deepseek-v4.1-flash-1017'}):
        source = 'https://platform.qianwenai.com/docs/api-reference/chat/openai-chat'
        result.update(enable_thinking={'supported': True, 'type': 'boolean', 'default': None,
            'wire_name': 'enable_thinking', 'reason': '已核实当前服务商的思考开关；空值沿用服务商默认', 'sources': [source]},
            reasoning_effort={'supported': True, 'type': 'enum', 'values': ['low', 'high', 'max'],
                'default': None, 'wire_name': 'reasoning_effort', 'reason': '已核实轻度、高度及最大思考；不保证正文Token保底', 'sources': [source]})
    return result


def validate_overrides(value, bindings, registry):
    if not isinstance(value, dict) or set(value) - set(bindings):
        raise InputValidationError('角色覆盖只能引用已绑定角色')
    result = {}
    for role, override in value.items():
        if not isinstance(override, dict) or set(override) - {'task_instruction', 'generation'}:
            raise InputValidationError('仅接受角色任务指令和生成参数')
        instruction = override.get('task_instruction', '')
        if not isinstance(instruction, str) or len(instruction) > 4000 or '\x00' in instruction:
            raise InputValidationError('角色任务指令须为最多4000字符的文本')
        generation = override.get('generation', {})
        caps = capabilities(registry['profiles'][bindings[role]])
        if not isinstance(generation, dict) or set(generation) - set(caps):
            raise InputValidationError('未知生成参数')
        for key, number in generation.items():
            cap = caps[key]
            if cap.get('type') in {'boolean', 'enum'}:
                if ((cap['type'] == 'boolean' and type(number) is not bool)
                        or (cap['type'] == 'enum' and (not isinstance(number, str) or number not in cap['values']))):
                    raise InputValidationError(f'{role}.{key}不受支持或超出登记范围')
                continue
            if (not cap['supported'] or type(number) not in (int, float)
                    or not cap['minimum'] <= number <= cap['maximum']
                    or (key == 'max_output_tokens' and type(number) is not int)):
                raise InputValidationError(f'{role}.{key}不受支持或超出登记范围')
        result[role] = {'task_instruction': instruction, 'generation': deepcopy(generation)}
        if generation.get('enable_thinking') is False and 'reasoning_effort' in generation:
            raise InputValidationError('关闭思考时不能同时设置思考强度')
    return result


def effective_parameters(profile, override):
    parameters = {profile['token_limit_parameter']: profile['max_output_tokens'],
                  **profile.get('request_parameters', {})}
    if profile.get('personal_pricing') and profile['model'].startswith(('qwen-plus', 'qwen3.5-plus')):
        parameters['enable_thinking'] = profile['personal_pricing']['mode'] == 'thinking'
    for key, value in override.get('generation', {}).items():
        parameters[profile['token_limit_parameter'] if key == 'max_output_tokens' else key] = value
    return parameters
