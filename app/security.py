from __future__ import annotations
import os
from pathlib import Path, PurePosixPath
class PermissionViolation(ValueError): pass
def normalise_relpath(value:str)->str:
    if not isinstance(value, str) or not value.strip() or ':' in value:
        raise PermissionViolation(f'非法路径: {value}')
    value=value.replace('\\','/'); path=PurePosixPath(value)
    if path.is_absolute() or not path.parts or '..' in path.parts: raise PermissionViolation(f'非法路径: {value}')
    return path.as_posix()
def validate_changes(changes:list[dict],*,allowed_paths:tuple[str,...],forbidden_paths:tuple[str,...],work_root:Path)->list[str]:
    if not isinstance(changes,list) or not changes: raise PermissionViolation('changes 必须是非空数组')
    allowed={normalise_relpath(x) for x in allowed_paths}; forbidden={normalise_relpath(x) for x in forbidden_paths}; result=[]
    for change in changes:
        if not isinstance(change,dict) or set(change)!={'path','new_content'}: raise PermissionViolation('每个 change 必须只包含 path 和 new_content')
        rel=normalise_relpath(change['path'])
        if rel in forbidden or any(rel.startswith(x.rstrip('/')+'/') for x in forbidden): raise PermissionViolation(f'禁止修改路径: {rel}')
        if rel not in allowed: raise PermissionViolation(f'路径不在允许范围: {rel}')
        unresolved=work_root
        for part in PurePosixPath(rel).parts:
            unresolved=unresolved/part
            if unresolved.is_symlink() or getattr(unresolved, 'is_junction', lambda: False)():
                raise PermissionViolation(f'路径包含软链接或联接: {rel}')
        target=unresolved.resolve(); root=work_root.resolve()
        if not target.is_relative_to(root): raise PermissionViolation(f'路径解析后越界: {rel}')
        if not isinstance(change['new_content'],str): raise PermissionViolation(f'new_content 必须是字符串: {rel}')
        result.append(rel)
    return result
