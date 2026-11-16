"""V3 JSON contracts, configured capabilities and versioned call evidence."""
import json
import hashlib
from pathlib import Path

from .knowledge import search_knowledge
from .repair_output import InvalidModelOutput, _object, _fields, _citations, parse_repair_output
from .security import validate_changes, PermissionViolation, normalise_relpath
from .tasks_v3 import FORBIDDEN, get_task
from .role_config import RoleConfig
from .diagnostics import record_exception


def readonly_material(definition, work):
    """Only reviewed, hash-bound interface files may enter agent context."""
    result = {}
    context_paths = definition.data.get('context_paths', [])
    if not isinstance(context_paths, list):
        raise InvalidModelOutput('只读接口材料登记须为路径数组')
    for path in context_paths:
        try:
            relative = normalise_relpath(path)
            parts = Path(relative).parts
            if (relative != path or path not in definition.profile['approved_files']
                    or path == definition.source or path in definition.profile['test_paths']
                    # QuixBugs registers node.py under python_testcases; that
                    # directory alone is not a denial for reviewed interfaces.
                    or any(part in FORBIDDEN and part != 'python_testcases' for part in parts)
                    or Path(path).name.startswith('test_')
                    or Path(path).name in {'conftest.py', 'load_testdata.py'}
                    or not path.endswith('.py')):
                raise ValueError('不是已登记的只读Python接口材料')
            target = Path(work)
            for part in parts:
                target = target / part
                if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
                    raise ValueError('接口材料路径包含链接')
            if not target.resolve().is_relative_to(Path(work).resolve()):
                raise ValueError('接口材料路径越界')
            raw = target.read_bytes()
            if hashlib.sha256(raw).hexdigest() != definition.profile['approved_files'][path]:
                raise ValueError('接口材料哈希不匹配')
            result[path] = raw.decode('utf-8')
        except (ValueError, OSError) as exc:
            raise InvalidModelOutput(f'只读接口材料被拒绝：{path!r}；{exc}') from exc
    return result

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
    def __init__(self, model, store, config=None):
        self.model, self.store = model, store
        self.config = config or RoleConfig(ROOT)
        from .agent_team import create_team
        self.agents = create_team()

    def invoke(self, role, *, run_id, round_number, code, evidence, version, work, task, deadline, extra=None, trace=None, handoffs=()):
        if role not in ROLES or evidence['phase'] != 'development' or evidence['code_version'] != version:
            raise InvalidModelOutput('角色或当前开发证据不匹配')
        allowed_evidence = {'evidence_id', 'phase', 'code_version', 'failure_class', 'summary'}
        if set(evidence) - allowed_evidence:
            raise InvalidModelOutput('开发证据包含未批准字段')
        agent = self.config.require(role)
        runtime_agent = self.agents[role]
        contract = runtime_agent.contract(self.config)
        upstream = runtime_agent.inputs(handoffs, evidence, version)
        definition = get_task(task.get('project', 'leap'))
        source = definition.source
        ids = tuple(k for k in definition.data['knowledge_ids'] if k in agent['knowledge_ids'])
        knowledge = ([x.to_dict() for x in search_knowledge(self.config.root, ids, definition.data['knowledge_query'])]
                     if 'search_knowledge' in agent['tool_allowlist'] else [])
        prompt = self.config.prompts[role][0]
        from .source_context import source_context
        shown_code, context_info = source_context(code, extra)
        material = {'task': {'project': definition.project, 'description': task['description'], 'allowed_paths': [source],
                             'forbidden_paths': list(FORBIDDEN), 'round': round_number, 'max_rounds': task['max_rounds']},
                    'current_code': {source: shown_code}, 'source_context': context_info, 'dev_evidence': [evidence],
                    'knowledge_context': knowledge, 'location_json': extra if role == 'repairer' else None,
                    'previous_analysis': extra if role != 'repairer' else None}
        material['agent_contract'] = contract
        material['upstream_results'] = upstream
        if definition.data.get('registered_contract'):
            material['task']['registered_contract'] = definition.data['registered_contract']
        # Only explicitly registered interface helpers, never reference answers,
        # whole test files, evaluation workspaces or the complete dataset bundle.
        context = readonly_material(definition, work)
        if context:
            material['readonly_code'] = context
        messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps(material, ensure_ascii=False)}]
        override = self.config.role_overrides.get(role, {})
        if override.get('task_instruction'):
            material['role_task_instruction'] = override['task_instruction']
            messages[1]['content'] = json.dumps(material, ensure_ascii=False)
        import uuid
        call_id = 'm-' + uuid.uuid4().hex
        folder = self.store.directory(run_id) / 'calls' / call_id
        folder.mkdir(parents=True)
        safe = getattr(self.model, 'redact', lambda x: x)
        provenance = self.config.provenance(role)
        from .role_config import digest
        provenance['agent_instance_id'] = f'{run_id}:{role}'
        provenance['agent_contract'] = contract
        provenance['agent_contract_sha256'] = digest(contract)
        provenance['handoffs'] = [{k: item[k] for k in ('agent_id', 'call_id', 'step_id',
            'artifact_path', 'code_version', 'evidence_id', 'output_sha256')} for item in upstream]
        for reference, item in zip(provenance['handoffs'], upstream):
            if 'dispatch_instruction' in item:
                reference['dispatch_instruction'] = item['dispatch_instruction']
        provenance['messages_sha256'] = digest(messages)
        provenance['task_instruction_sha256'] = digest(override.get('task_instruction', ''))
        provenance['model_binding'] = {'model_ref': agent['model_ref'], **self.config.snapshot['models'][agent['model_ref']],
                                       'mode': getattr(self.model, 'mode', 'unknown'),
                                       'model': getattr(getattr(self.model, 'settings', None), 'model', None)}
        if hasattr(self.model, 'binding'):
            provenance['model_binding'] = self.model.binding(role)
        (folder / 'input.json').write_text(json.dumps(safe({'role': role, 'messages': messages, 'provenance': provenance}), ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            kwargs = dict(run_id=run_id, call_id=call_id, max_calls=task['max_calls'], deadline=deadline)
            record = (self.model.call_for_role(role, messages, **kwargs) if hasattr(self.model, 'call_for_role')
                      else self.model.call(messages, **kwargs))
        except Exception as exc:
            record = {'status': 'blocked', 'failure_class': 'model_blocked', 'error_type': type(exc).__name__,
                      'model_api_called': False, 'mode': getattr(self.model, 'mode', 'unknown')}
            from .live_model import ModelBlocked
            if isinstance(exc, ModelBlocked):
                record['reason'] = str(exc)
                from .live_model import BudgetBlocked
                if isinstance(exc, BudgetBlocked):
                    record['budget_check'] = {'status': 'blocked', 'reason': str(exc)}
            else:
                record.update(record_exception(self.store.directory(run_id), exc, model=self.model))
        record.update(role=role, code_version=version, call_id=call_id, round=round_number, provenance=provenance)
        record.update(trace or {})
        if record.get('budget_check'):
            self.store.event(run_id, {'event': 'guard_check', 'step_id': call_id + '-budget',
                'parent_step_id': (trace or {}).get('step_id'), 'node_id': 'budget_check',
                'role': role, 'round': round_number, 'code_version': version, **record['budget_check']})
        if record['status'] == 'ok':
            try:
                record['parsed_output'] = parse_role(role, record['raw_text'], [evidence['evidence_id']], [k['citation_id'] for k in knowledge])
                if role == 'repairer':
                    if context_info['partial'] and any('edits' not in c for c in record['parsed_output']['changes']):
                        raise InvalidModelOutput('当前只提供部分源码，必须使用edits，不能重写完整文件')
                    from .source_edits import expand_changes
                    record['parsed_output']['changes'] = expand_changes(record['parsed_output']['changes'],
                        work=work, source=source, forbidden=FORBIDDEN)
                    validate_changes(record['parsed_output']['changes'], allowed_paths=(source,), forbidden_paths=FORBIDDEN, work_root=work)
                if role == 'locator' and record['parsed_output']['file'] not in {'', source}:
                    raise InvalidModelOutput('定位文件必须属于当前任务')
                if role == 'detector' and record['parsed_output']['failure_class'] != evidence['failure_class']:
                    raise InvalidModelOutput('检测建议不能覆盖真实分类')
            except PermissionViolation as exc:
                record.update(status='failed', failure_class='permission_violation', error_type=type(exc).__name__, reason=str(exc)[:1000])
            except (ValueError, OSError) as exc:
                record.update(status='failed', failure_class='invalid_model_output', error_type=type(exc).__name__, reason=str(exc)[:1000],
                    output_diagnostics={'kind': 'invalid_format', 'validation_error': str(exc)[:1000],
                        'suggested_action': '按校验错误纠正JSON字段和路径；系统仅在剩余轮次、调用及费用内重试。'})
        if role == 'repairer' and record.get('failure_class') != 'invalid_model_output':
            self.store.event(run_id, {'event': 'guard_check', 'check': 'patch_permission',
                'step_id': call_id + '-permission', 'parent_step_id': (trace or {}).get('step_id'),
                'node_id': 'permission_check', 'round': round_number, 'code_version': version,
                'status': 'blocked' if record.get('failure_class') == 'permission_violation' else 'ok' if record['status'] == 'ok' else 'not_run',
                'reason': record.get('reason') or ('补丁路径及结构检查通过' if record['status'] == 'ok' else '未取得有效候选，未检查补丁权限')})
        (folder / 'output.json').write_text(json.dumps(safe(record), ensure_ascii=False, indent=2), encoding='utf-8')
        self.store.event(run_id, {'event': 'agent_call', **safe(record)})
        return record
