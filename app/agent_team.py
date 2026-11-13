"""Role-bound agent contracts and candidate-scoped collaboration inputs."""
from dataclasses import dataclass
from copy import deepcopy

from .repair_output import InvalidModelOutput
from .role_config import SCHEMAS, digest
from .config import ConfigError


class AgentHandoffBlocked(ConfigError):
    """A mandatory collaboration dependency is absent or no longer valid."""


@dataclass(frozen=True)
class RoleAgent:
    role: str
    title: str
    receives_from: tuple[str, ...]
    produces: str
    permitted_action: str

    def contract(self, config):
        registered = config.require(self.role)
        return {'contract_version': '1.0.0', 'agent_id': self.role, 'title': self.title,
                'input_scope': 'current_candidate_development_evidence_only',
                'receives_from': list(self.receives_from), 'produces': self.produces,
                'collaboration_task': {
                    'repairer': '根据当前工具证据与已交接的分析、调度、定位结果提出源文件补丁；不执行代码。',
                    'detector': '分析本候选的隔离测试证据，保持工具失败分类原义；输出分析，不修改代码。',
                    'scheduler': '依据当前检测员分析和工具证据提出动作与理由；程序裁决才有执行权。',
                    'locator': '同时依据检测员失败分析与dispatch_instruction定位；不修改代码或宣布通过。',
                }[self.role],
                'output_schema': SCHEMAS[self.role], 'permitted_action': self.permitted_action,
                'tools': list(registered['tool_allowlist']),
                'can_execute_code': False, 'can_modify_tests': False,
                'can_approve_success': False, 'can_override_guards': False}

    def inputs(self, handoffs, evidence, version):
        """Reject stale or misrouted outputs before they can enter a model request."""
        result, seen = [], set()
        for handoff in handoffs:
            required = {'agent_id', 'call_id', 'step_id', 'artifact_path', 'code_version',
                        'evidence_id', 'output', 'output_sha256'}
            error = AgentHandoffBlocked if self.role == 'locator' else InvalidModelOutput
            if not isinstance(handoff, dict) or not required <= handoff.keys():
                raise error('Agent交接缺少调用、步骤或证据引用')
            sender = handoff.get('agent_id')
            if (sender not in self.receives_from or sender in seen
                    or handoff.get('code_version') != version
                    or handoff.get('evidence_id') != evidence['evidence_id']
                    or handoff.get('output_sha256') != digest(handoff.get('output'))):
                raise error('Agent交接角色、版本、证据或输出哈希不匹配')
            seen.add(sender)
            result.append(deepcopy(handoff))
        if self.role == 'locator':
            if seen != {'detector', 'scheduler'}:
                raise AgentHandoffBlocked('定位员缺少当前检测员结果或调度分发指令，禁止调用模型')
            detected = next(h for h in result if h['agent_id'] == 'detector')
            scheduled = next(h for h in result if h['agent_id'] == 'scheduler')
            instruction = scheduled.get('dispatch_instruction', {})
            if (detected['output'].get('failure_class') != evidence.get('failure_class')
                    or instruction.get('effective_action') != 'locate'
                    or instruction.get('code_version') != version
                    or instruction.get('evidence_id') != evidence['evidence_id']):
                raise AgentHandoffBlocked('定位员检测证据与程序批准的调度指令不一致，禁止调用模型')
        return result


def create_team():
    # These are four role-bound runtime instances, not four processes/models.
    return {agent.role: agent for agent in (
        RoleAgent('repairer', '修复员', ('detector', 'scheduler', 'locator'),
                  'candidate_changes', 'propose_source_patch'),
        RoleAgent('detector', '检测员', (), 'failure_analysis', 'analyse_tool_evidence'),
        RoleAgent('scheduler', '模型调度员', ('detector',), 'action_advice', 'advise_only'),
        RoleAgent('locator', '定位员', ('detector', 'scheduler'), 'source_location', 'locate_only'),
    )}
