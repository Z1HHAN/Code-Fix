"""V3 role contracts; old v0.2 role configuration remains unchanged."""
import json
from pathlib import Path

from .knowledge import search_knowledge
from .repair_output import InvalidModelOutput, _object, _fields, _citations, parse_repair_output
from .security import validate_changes
from .tasks_v3 import FORBIDDEN, get_task

ROOT = Path(__file__).resolve().parents[1]
ROLES = {'repairer', 'detector', 'scheduler', 'locator'}


def parse_role(role, raw, evidence_ids, knowledge_ids):
    if role == 'repairer':
        return parse_repair_output(raw, allowed_evidence_ids=evidence_ids, allowed_knowledge_ids=knowledge_ids)
    if role == 'locator':
        from .repair_output import parse_locator_output
        return parse_locator_output(raw, allowed_evidence_ids=evidence_ids, allowed_knowledge_ids=knowledge_ids)
    value = _object(raw)
    if role == 'detector':
        _fields(value, {'analysis', 'failure_class', 'suggested_checks', 'evidence_ids', 'knowledge_citations'})
        if not isinstance(value['suggested_checks'], list) or any(not isinstance(x, str) for x in value['suggested_checks']):
            raise InvalidModelOutput('补充验证建议须为字符串数组')
        if not isinstance(value['failure_class'], str) or not isinstance(value['analysis'], str) or not value['analysis'].strip():
            raise InvalidModelOutput('检测员输出缺少分析或分类')
    elif role == 'scheduler':
        _fields(value, {'action', 'reason', 'evidence_ids', 'knowledge_citations'})
        if not isinstance(value['action'], str) or value['action'] not in {'repair', 'locate', 'final_check', 'pause', 'stop'} or not isinstance(value['reason'], str) or not value['reason'].strip():
            raise InvalidModelOutput('调度建议非法')
    else:
        raise InvalidModelOutput('未知角色')
    _citations(value, evidence_ids, knowledge_ids)
    if not value['evidence_ids']:
        raise InvalidModelOutput('检测和调度必须引用当前证据')
    return value


class AgentGateway:
    def __init__(self, model, store):
        self.model, self.store = model, store

    def invoke(self, role, *, run_id, round_number, code, evidence, version, work, task, deadline, extra=None):
        if role not in ROLES or evidence['phase'] != 'development' or evidence['code_version'] != version:
            raise InvalidModelOutput('角色或当前开发证据不匹配')
        definition = get_task(task.get('project', 'leap'))
        source = definition.source
        knowledge = [x.to_dict() for x in search_knowledge(ROOT, tuple(definition.data['knowledge_ids']), definition.data['knowledge_query'])]
        prompt = (ROOT / 'prompts' / f'{role}.md').read_text(encoding='utf-8').split('任务与路径约束：', 1)[0]
        material = {'task': {'project': definition.project, 'description': task['description'], 'allowed_paths': [source],
                             'forbidden_paths': list(FORBIDDEN), 'round': round_number, 'max_rounds': task['max_rounds']},
                    'current_code': {source: code}, 'dev_evidence': [evidence],
                    'knowledge_context': knowledge, 'location_json': extra if role == 'repairer' else None,
                    'previous_analysis': extra if role != 'repairer' else None}
        # Only explicitly registered interface helpers, never reference answers,
        # whole test files, evaluation workspaces or the complete dataset bundle.
        context = definition.data.get('context_paths', [])
        if any(path not in definition.profile['approved_files'] or path == source or not path.endswith('/node.py') for path in context):
            raise InvalidModelOutput('只读接口材料未登记')
        if context:
            material['readonly_code'] = {path: (work / path).read_text(encoding='utf-8') for path in context}
        messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps(material, ensure_ascii=False)}]
        import uuid
        call_id = 'm-' + uuid.uuid4().hex
        folder = self.store.directory(run_id) / 'calls' / call_id
        folder.mkdir(parents=True)
        safe = getattr(self.model, 'redact', lambda x: x)
        (folder / 'input.json').write_text(json.dumps(safe({'role': role, 'messages': messages}), ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            record = self.model.call(messages, run_id=run_id, call_id=call_id, max_calls=task['max_calls'], deadline=deadline)
        except Exception as exc:
            record = {'status': 'blocked', 'failure_class': 'model_blocked', 'error_type': type(exc).__name__,
                      'model_api_called': False, 'mode': getattr(self.model, 'mode', 'unknown')}
            from .live_model import ModelBlocked
            if isinstance(exc, ModelBlocked):
                record['reason'] = str(exc)
        record.update(role=role, code_version=version, call_id=call_id, round=round_number)
        if record['status'] == 'ok':
            try:
                record['parsed_output'] = parse_role(role, record['raw_text'], [evidence['evidence_id']], [k['citation_id'] for k in knowledge])
                if role == 'repairer':
                    validate_changes(record['parsed_output']['changes'], allowed_paths=(source,), forbidden_paths=FORBIDDEN, work_root=work)
                if role == 'locator' and record['parsed_output']['file'] not in {'', source}:
                    raise InvalidModelOutput('定位文件必须属于当前任务')
                if role == 'detector' and record['parsed_output']['failure_class'] != evidence['failure_class']:
                    raise InvalidModelOutput('检测建议不能覆盖真实分类')
            except (ValueError, OSError) as exc:
                record.update(status='failed', failure_class='invalid_model_output', error_type=type(exc).__name__)
        (folder / 'output.json').write_text(json.dumps(safe(record), ensure_ascii=False, indent=2), encoding='utf-8')
        self.store.event(run_id, {'event': 'agent_call', **safe(record)})
        return record
