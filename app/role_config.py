"""One explicit production protocol; legacy Day1 is deliberately not executable here."""
import hashlib
import json
from pathlib import Path

from .config import ConfigError, read_json

PROTOCOL = 'codefix-json-v1'
PARSER_VERSION = '1.0.0'
SCHEMAS = {'locator': 'LocatorOutput', 'repairer': 'RepairOutput',
           'detector': 'DetectorOutput', 'scheduler': 'SchedulerOutput'}
TOOLS = {'read_file', 'search_knowledge'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def checked_file(root, relative):
    root = Path(root).resolve()
    path = root
    for part in Path(relative).parts:
        if part in {'.', '..'}:
            raise ConfigError('配置路径不允许跳级')
        path = path / part
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ConfigError('配置材料路径不允许链接')
    if not path.is_relative_to(root) or not path.is_file():
        raise ConfigError('配置材料不存在或越界: ' + relative)
    return path


def string_list(value, name):
    if (not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value)
            or len(set(value)) != len(value)):
        raise ConfigError(name + '须为不重复字符串数组')
    return value


class RoleConfig:
    def __init__(self, root, snapshot=None):
        self.root = Path(root)
        document = read_json(checked_file(root, 'configs/agents.json'))
        data = document.get('runtime_v3') if snapshot is None else snapshot
        if not isinstance(data, dict) or set(data) != {'config_version', 'protocol', 'models', 'agents'}:
            raise ConfigError('runtime_v3字段不完整或存在未知字段')
        if data['protocol'] != PROTOCOL or data['config_version'] not in {'1.0.0', '1.1.0'}:
            raise ConfigError('不支持的生产协议或配置版本；Day1仅供历史查阅')
        # This release binds one shared live client via LiveSettings.from_env.
        # No credentials, URLs or executable import paths are accepted in JSON.
        if data['config_version'] == '1.0.0':
            if data['models'] != {'primary': {'binding': 'live_env'}}:
                raise ConfigError('1.0.0仅支持primary/live_env')
        else:
            from .model_profiles import load_profiles
            profiles = load_profiles(root)['profiles']
            if not isinstance(data['models'], dict) or not data['models']:
                raise ConfigError('1.1.0至少需要一个模型绑定')
            for ref, binding in data['models'].items():
                if (not isinstance(ref, str) or not ref.replace('_', '').isalnum()
                        or not isinstance(binding, dict) or set(binding) != {'binding', 'profile_ref'}
                        or binding['binding'] != 'approved_profile'
                        or not isinstance(binding['profile_ref'], str) or binding['profile_ref'] not in profiles):
                    raise ConfigError('1.1.0模型必须绑定已登记的受控档案')
        if not isinstance(data['agents'], list):
            raise ConfigError('runtime_v3.agents须为数组')
        self.agents, self.prompts = {}, {}
        fields = {'agent_id', 'enabled', 'role_type', 'model_ref', 'prompt_ref',
                  'tool_allowlist', 'knowledge_ids', 'output_schema'}
        for agent in data['agents']:
            if not isinstance(agent, dict) or set(agent) != fields:
                raise ConfigError('生产角色字段不完整或存在未知字段')
            role = agent['agent_id']
            if not isinstance(role, str) or role not in SCHEMAS or role in self.agents:
                raise ConfigError('生产角色ID未知或重复')
            if type(agent['enabled']) is not bool or agent['role_type'] != 'llm':
                raise ConfigError(role + ': enabled/role_type无效')
            if not isinstance(agent['model_ref'], str) or agent['model_ref'] not in data['models']:
                raise ConfigError(role + ': model_ref未绑定')
            if agent['output_schema'] != SCHEMAS[role] or agent['prompt_ref'] != f'prompts/{role}.md':
                raise ConfigError(role + ': 提示词或输出schema与协议不匹配')
            tools = string_list(agent['tool_allowlist'], role + '.tool_allowlist')
            if not set(tools) <= TOOLS:
                raise ConfigError(role + ': 禁止未知工具或控制端工具授权')
            for kid in string_list(agent['knowledge_ids'], role + '.knowledge_ids'):
                if not kid.replace('_', '').isalnum():
                    raise ConfigError(role + ': 知识ID非法')
                checked_file(root, f'knowledge/demo_project/{kid}.md')
            raw = checked_file(root, agent['prompt_ref']).read_bytes()
            prompt = raw.decode('utf-8').split('任务与路径约束：', 1)[0]
            self.prompts[role] = (prompt, hashlib.sha256(raw).hexdigest())
            self.agents[role] = agent
        if set(self.agents) != set(SCHEMAS):
            raise ConfigError('生产角色集合必须完整；停用请设置enabled=false')
        self.snapshot = data
        self.sha256 = digest(data)

    def require(self, role):
        agent = self.agents.get(role)
        if not agent or agent['enabled'] is not True:
            raise ConfigError('角色未启用: ' + role)
        if 'read_file' not in agent['tool_allowlist']:
            raise ConfigError(role + ': 缺少读取当前代码的read_file权限')
        return agent

    def preflight(self):
        # Dynamic dispatch may select any of the four roles in this workflow.
        for role in SCHEMAS:
            self.require(role)

    def provenance(self, role):
        return {'protocol': PROTOCOL, 'parser_version': PARSER_VERSION,
                'config_version': self.snapshot['config_version'],
                'effective_config': self.snapshot, 'config_sha256': self.sha256,
                'prompt_ref': self.agents[role]['prompt_ref'],
                'prompt_sha256': self.prompts[role][1],
                'system_prompt_sha256': hashlib.sha256(self.prompts[role][0].encode()).hexdigest()}
