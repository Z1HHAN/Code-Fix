from __future__ import annotations
import json
class InvalidModelOutput(ValueError): pass
def _object(raw:str)->dict:
    try: value=json.loads(raw)
    except json.JSONDecodeError as exc: raise InvalidModelOutput(f'模型输出不是 JSON: {exc}') from exc
    if not isinstance(value,dict): raise InvalidModelOutput('模型输出根节点必须是对象')
    return value
def parse_locator_output(raw:str)->dict:
    value=_object(raw); missing={'file','function','hypothesis','evidence_ids','knowledge_citations'}-set(value)
    if missing: raise InvalidModelOutput('定位输出缺少字段: '+', '.join(sorted(missing)))
    return value
def parse_repair_output(raw:str)->dict:
    value=_object(raw); missing={'changes','explanation','evidence_ids','knowledge_citations'}-set(value)
    if missing: raise InvalidModelOutput('修复输出缺少字段: '+', '.join(sorted(missing)))
    if not isinstance(value['changes'],list) or not value['changes']: raise InvalidModelOutput('changes 必须是非空数组')
    for change in value['changes']:
        if not isinstance(change,dict) or set(change)!={'path','new_content'}: raise InvalidModelOutput('changes 项必须只包含 path 和 new_content')
    return value
