"""Expand exact, unambiguous source edits before writing any candidate bytes."""
from .repair_output import InvalidModelOutput
from .security import validate_changes, PermissionViolation


def expand_changes(changes, *, work, source, forbidden):
    if not isinstance(changes, list) or not changes:
        raise PermissionViolation('changes必须是非空数组')
    result = []
    for change in changes:
        if not isinstance(change, dict) or set(change) not in ({'path', 'new_content'}, {'path', 'edits'}):
            raise PermissionViolation('change只能包含path及new_content或edits')
        if 'edits' not in change:
            result.append(change)
            continue
        # Validate the path BEFORE reading; no evaluation/reference/test access.
        paths = validate_changes([{'path': change['path'], 'new_content': ''}],
            allowed_paths=(source,), forbidden_paths=forbidden, work_root=work)
        text = (work / paths[0]).read_text(encoding='utf-8')
        edits = change['edits']
        if not isinstance(edits, list) or not 1 <= len(edits) <= 20:
            raise InvalidModelOutput('edits必须包含1至20项精确替换')
        ranges = []
        for edit in edits:
            if (not isinstance(edit, dict) or set(edit) != {'old', 'new'}
                    or not isinstance(edit['old'], str) or not edit['old']
                    or not isinstance(edit['new'], str)):
                raise InvalidModelOutput('每项edits须包含非空old字符串和new字符串')
            old = edit['old']
            start = text.find(old)
            if start < 0 or text.find(old, start + 1) >= 0:
                raise InvalidModelOutput('old必须在当前候选源码中精确且唯一匹配')
            ranges.append((start, start + len(old), edit['new']))
        ranges.sort()
        if any(left[1] > right[0] for left, right in zip(ranges, ranges[1:])):
            raise InvalidModelOutput('edits不能重叠')
        for start, end, replacement in reversed(ranges):
            text = text[:start] + replacement + text[end:]
        result.append({'path': change['path'], 'new_content': text})
    return result
