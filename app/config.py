from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

class ConfigError(ValueError):
    pass

def read_json(path: Path) -> dict[str, Any]:
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise ConfigError(f'配置文件不存在: {path}') from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f'配置文件不是有效 JSON: {path}: {exc}') from exc
    if not isinstance(value,dict): raise ConfigError(f'配置根节点必须是对象: {path}')
    return value

@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    description: str
    allowed_paths: tuple[str,...]
    test_command: tuple[str,...]
    max_repair_rounds: int
    expected_test_count: int | None

def load_task_spec(path: Path)->TaskSpec:
    data=read_json(path)
    missing=[k for k in ('task_id','description','allowed_paths','test_command') if k not in data]
    if missing: raise ConfigError('TaskSpec 缺少字段: '+', '.join(missing))
    cmd=data['test_command']; cmd=tuple(cmd.split()) if isinstance(cmd,str) else tuple(map(str,cmd))
    if not cmd or not isinstance(data['allowed_paths'],list): raise ConfigError('TaskSpec 命令或允许路径无效')
    limits=data.get('limits') or {}
    return TaskSpec(str(data['task_id']),str(data['description']),tuple(map(str,data['allowed_paths'])),cmd,int(limits.get('max_repair_rounds',data.get('max_repair_rounds',3))),int(data['expected_test_count']) if data.get('expected_test_count') is not None else None)

def validate_draft_configs(root: Path)->list[str]:
    agents=read_json(root/'configs/agents.json'); rules=read_json(root/'configs/decision_rules.json'); errors=[]
    for name,data in (('agents.json',agents),('decision_rules.json',rules)):
        if data.get('status')!='pending_BC_confirmation': errors.append(f'{name} 状态不是 pending_BC_confirmation')
    if not isinstance(agents.get('agents'),list): errors.append('agents.json.agents 必须是数组')
    if not isinstance(rules.get('rules'),list) or not rules['rules']: errors.append('decision_rules.json.rules 必须是非空数组')
    if not rules.get('preconditions',{}).get('required_context_fields'): errors.append('decision_rules.json 缺少 required_context_fields')
    return errors
