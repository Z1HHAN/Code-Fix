"""Build a bounded model input from caller-reviewed development materials only.

The only file read is the configured role's fixed prompt. Code, evidence and
knowledge text must be supplied explicitly by the trusted control program.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .repair_output import InvalidModelOutput, parse_locator_output
from .security import PermissionViolation, normalise_relpath, validate_changes


class ContextError(ValueError):
    pass


@dataclass(frozen=True)
class ModelContext:
    role: str
    messages: tuple[dict[str, str], ...]
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    evidence_ids: frozenset[str]
    knowledge_ids: frozenset[str]
    work_root: Path
    code_version: str


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _items(value, name):
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise ContextError(f'{name} 必须是对象数组')


def _unique_ids(items, field):
    ids = [item.get(field) for item in items]
    if any(not _text(x) for x in ids) or len(set(ids)) != len(ids):
        raise ContextError(f'{field} 必须是唯一非空字符串')
    return frozenset(ids)


def _check_path(path, *, allowed_paths, forbidden_paths, work_root):
    try:
        validate_changes([{'path': path, 'new_content': ''}], allowed_paths=allowed_paths,
                         forbidden_paths=forbidden_paths, work_root=work_root)
    except PermissionViolation as exc:
        raise ContextError('上下文路径超出允许范围') from exc


def build_model_context(*, project_root: Path, agents_config: dict, role: str,
                        task: dict, code: dict[str, str], evidence: list[dict],
                        knowledge: list[dict], work_root: Path, code_version: str,
                        location: dict | None = None) -> ModelContext:
    if role not in ('locator', 'repairer'):
        raise ContextError('本轮只允许 locator 和 repairer 模型角色')
    if not isinstance(agents_config, dict) or not isinstance(agents_config.get('agents'), list):
        raise ContextError('角色配置无效')
    matches = [x for x in agents_config['agents'] if isinstance(x, dict) and x.get('agent_id') == role]
    if len(matches) != 1:
        raise ContextError('角色必须唯一')
    agent = matches[0]
    if agent.get('enabled') is not True or agent.get('role_type') != 'llm':
        raise ContextError('角色未启用或不是模型角色')
    prompt_ref = f'prompts/{role}.md'
    if agent.get('prompt_ref') != prompt_ref:
        raise ContextError('提示词引用不在本轮白名单')
    if not _text(code_version):
        raise ContextError('缺少当前代码版本')
    if not isinstance(task, dict) or not set(task).issubset({'task_id', 'description', 'allowed_paths', 'forbidden_paths'}):
        raise ContextError('任务上下文字段不在白名单')
    if not _text(task.get('task_id')) or not _text(task.get('description')):
        raise ContextError('任务 ID 与描述必须为非空字符串')
    allowed = task.get('allowed_paths')
    forbidden = task.get('forbidden_paths', [])
    if not isinstance(allowed, list) or not allowed or not isinstance(forbidden, list):
        raise ContextError('允许与禁止路径必须为数组，允许路径不能为空')
    try:
        allowed_paths = tuple(normalise_relpath(x) for x in allowed)
        forbidden_paths = tuple(dict.fromkeys(('tests',) + tuple(normalise_relpath(x) for x in forbidden)))
    except PermissionViolation as exc:
        raise ContextError('任务路径无效') from exc
    if len(set(allowed_paths)) != len(allowed_paths):
        raise ContextError('允许路径不能重复')
    work_root = Path(work_root)
    if not isinstance(code, dict) or not code:
        raise ContextError('必须明确提供已审查代码')
    for path, content in code.items():
        if not isinstance(content, str):
            raise ContextError('代码必须为文本')
        _check_path(path, allowed_paths=allowed_paths, forbidden_paths=forbidden_paths, work_root=work_root)
    _items(evidence, 'evidence')
    _items(knowledge, 'knowledge')
    if not evidence:
        raise ContextError('必须提供当前开发证据')
    for item in evidence:
        if set(item) != {'evidence_id', 'phase', 'code_version', 'summary'}:
            raise ContextError('开发证据字段不在白名单')
        if item['phase'] != 'development' or item['code_version'] != code_version or not _text(item['summary']):
            raise ContextError('只接收当前版本的开发证据')
    evidence_ids = _unique_ids(evidence, 'evidence_id')
    available_knowledge = agent.get('knowledge_ids')
    if not isinstance(available_knowledge, list) or any(not _text(x) for x in available_knowledge):
        raise ContextError('角色知识配置无效')
    for item in knowledge:
        if set(item) != {'citation_id', 'knowledge_id', 'source_path', 'excerpt'}:
            raise ContextError('知识字段不在白名单')
        kid = item['knowledge_id']
        if kid not in available_knowledge or not _text(item['excerpt']):
            raise ContextError('知识不在该角色白名单')
        if item['source_path'] != f'knowledge/demo_project/{kid}.md':
            raise ContextError('知识来源不在登记路径')
    knowledge_ids = _unique_ids(knowledge, 'citation_id')
    if location is not None:
        if role != 'repairer':
            raise ContextError('仅修复员接收定位结论')
        try:
            location = parse_locator_output(json.dumps(location), allowed_evidence_ids=evidence_ids,
                                            allowed_knowledge_ids=knowledge_ids)
        except (InvalidModelOutput, TypeError, ValueError) as exc:
            raise ContextError('定位结论不符合当前上下文') from exc
        if location['file']:
            _check_path(location['file'], allowed_paths=allowed_paths, forbidden_paths=forbidden_paths, work_root=work_root)
    # Refuse links before resolving. Never enumerate the repository or load code.
    root = Path(project_root).resolve()
    prompt = root
    for part in Path(prompt_ref).parts:
        prompt = prompt / part
        if prompt.is_symlink() or getattr(prompt, 'is_junction', lambda: False)():
            raise ContextError('提示词路径不能包含链接')
    try:
        instructions = prompt.read_text(encoding='utf-8').split('任务与路径约束：', 1)[0].strip()
    except OSError as exc:
        raise ContextError('无法加载白名单提示词') from exc
    material = {'task': task, 'current_code': code, 'dev_evidence': evidence,
                'knowledge_context': knowledge, 'location_json': location}
    messages = ({'role': 'system', 'content': instructions},
                {'role': 'user', 'content': json.dumps(material, ensure_ascii=False)})
    return ModelContext(role, messages, allowed_paths, forbidden_paths, evidence_ids,
                        knowledge_ids, work_root, code_version)
