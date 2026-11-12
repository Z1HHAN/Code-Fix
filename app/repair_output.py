"""Validate model JSON without applying changes or trusting model citations."""
from __future__ import annotations

import json
import re
from collections.abc import Collection


class InvalidModelOutput(ValueError):
    pass


# 仅匹配“整段响应恰好被一层 ```json 围栏包裹”的情况；其余形态一律不放宽。
_FENCED_JSON = re.compile(
    r"\A```[ \t]*json[ \t]*\r?\n(.*?)\r?\n```\Z",
    re.IGNORECASE | re.DOTALL,
)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidModelOutput('JSON 字段重复')
        result[key] = value
    return result


def _object(raw: str) -> dict:
    if not isinstance(raw, str):
        raise InvalidModelOutput('模型输出必须是文本')
    text = raw.strip()
    fenced = _FENCED_JSON.fullmatch(text)
    if fenced is not None:
        text = fenced.group(1)
    try:
        value = json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        raise InvalidModelOutput('模型输出不是有效 JSON') from exc
    if not isinstance(value, dict):
        raise InvalidModelOutput('模型输出根节点必须是对象')
    return value


def _fields(value, required):
    if set(value) != required:
        raise InvalidModelOutput('模型输出字段不符合协议')


def _references(value, field, allowed):
    refs = value[field]
    if not isinstance(refs, list) or any(not isinstance(x, str) or not x.strip() for x in refs):
        raise InvalidModelOutput(f'{field} 必须是非空字符串组成的数组')
    if len(set(refs)) != len(refs):
        raise InvalidModelOutput(f'{field} 不允许重复')
    if allowed is not None and not set(refs).issubset(allowed):
        raise InvalidModelOutput(f'{field} 引用了未提供的 ID')


def _citations(value, evidence, knowledge):
    _references(value, 'evidence_ids', evidence)
    _references(value, 'knowledge_citations', knowledge)


def parse_locator_output(raw: str, *, allowed_evidence_ids: Collection[str] | None = None,
                         allowed_knowledge_ids: Collection[str] | None = None) -> dict:
    value = _object(raw)
    _fields(value, {'file', 'function', 'hypothesis', 'evidence_ids', 'knowledge_citations'})
    if any(not isinstance(value[x], str) for x in ('file', 'function', 'hypothesis')):
        raise InvalidModelOutput('定位输出的 file/function/hypothesis 必须为字符串')
    if not value['hypothesis'].strip():
        raise InvalidModelOutput('定位结论不能为空')
    _citations(value, allowed_evidence_ids, allowed_knowledge_ids)
    return value


def parse_repair_output(raw: str, *, allowed_evidence_ids: Collection[str] | None = None,
                        allowed_knowledge_ids: Collection[str] | None = None) -> dict:
    value = _object(raw)
    _fields(value, {'changes', 'explanation', 'evidence_ids', 'knowledge_citations'})
    if not isinstance(value['explanation'], str) or not value['explanation'].strip():
        raise InvalidModelOutput('explanation 必须是非空字符串')
    if not isinstance(value['changes'], list) or not value['changes']:
        raise InvalidModelOutput('changes 必须是非空数组')
    paths = []
    for change in value['changes']:
        if not isinstance(change, dict) or set(change) not in ({'path', 'new_content'}, {'path', 'edits'}):
            raise InvalidModelOutput('changes项须包含path及new_content或edits，二者不能混用')
        if (not isinstance(change['path'], str) or not change['path'].strip()
                or ('new_content' in change and not isinstance(change['new_content'], str))):
            raise InvalidModelOutput('path 必须是非空字符串，new_content 必须是字符串')
        paths.append(change['path'])
        if 'edits' in change:
            edits = change['edits']
            if (not isinstance(edits, list) or not 1 <= len(edits) <= 20
                    or any(not isinstance(e, dict) or set(e) != {'old', 'new'}
                        or not isinstance(e['old'], str) or not e['old'] or not isinstance(e['new'], str)
                        for e in edits)):
                raise InvalidModelOutput('edits须为1至20项old/new精确替换，old不能为空')
    if len(set(paths)) != len(paths):
        raise InvalidModelOutput('changes 路径不能重复')
    _citations(value, allowed_evidence_ids, allowed_knowledge_ids)
    return value
