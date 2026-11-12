"""Bound source material for long single-file repairs without executing code."""
import ast


def source_context(code, location=None):
    if len(code.encode('utf-8')) <= 12000:
        return code, {'partial': False}
    lines = code.splitlines(True)
    try:
        tree = ast.parse(code)
        nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    except (SyntaxError, RecursionError):
        nodes = []
    name = location.get('function') if isinstance(location, dict) else None
    selected = next((n for n in nodes if n.name == name), nodes[0] if nodes else None)
    start = selected.lineno - 1 if selected else 0
    end = selected.end_lineno if selected else len(lines)
    # Keep imports/module context and one focused window; never append tests or
    # history to compensate for omitted source. Matching still uses full bytes.
    chosen = sorted(set(range(min(20, len(lines)))) | set(range(start, min(end, start + 160))))
    chunks, used, previous = [], 0, -1
    for index in chosen:
        line = lines[index]
        if used + len(line.encode('utf-8')) > 7000:
            break
        if index != previous + 1:
            chunks.append('# [CodeFix: intervening source omitted]\n')
        chunks.append(line)
        used += len(line.encode('utf-8'))
        previous = index
    chunks.append('\n# [CodeFix: partial context; use exact edits only]\n')
    return ''.join(chunks), {'partial': True, 'total_lines': len(lines),
        'focused_function': selected.name if selected else None,
        'symbols': [{'name': n.name, 'start': n.lineno, 'end': n.end_lineno} for n in nodes[:48]]}
