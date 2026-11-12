import json
import pytest
from app.source_edits import expand_changes
from app.source_context import source_context
from app.repair_output import InvalidModelOutput, parse_repair_output
from app.security import PermissionViolation


def test_two_edits_apply_to_original_positions_without_chaining(tmp_path):
    (tmp_path/'code.py').write_text('a = 1\nb = 2\n', encoding='utf-8')
    changes = [{'path': 'code.py', 'edits': [{'old': 'a = 1', 'new': 'a = 2'}, {'old': 'b = 2', 'new': 'b = 3'}]}]
    parsed = parse_repair_output(json.dumps({'changes': changes, 'explanation': 'two fixes',
        'evidence_ids': [], 'knowledge_citations': []}))
    expanded = expand_changes(parsed['changes'], work=tmp_path, source='code.py', forbidden=())
    assert expanded == [{'path': 'code.py', 'new_content': 'a = 2\nb = 3\n'}]
    assert (tmp_path/'code.py').read_text() == 'a = 1\nb = 2\n'


@pytest.mark.parametrize('edits', [
    [{'old': 'missing', 'new': 'x'}], [{'old': 'a', 'new': 'x'}],
    [{'old': 'abc', 'new': 'x'}, {'old': 'bcd', 'new': 'y'}],
    [{'old': '', 'new': 'x'}]])
def test_ambiguous_missing_empty_or_overlapping_edits_do_not_write(tmp_path, edits):
    (tmp_path/'code.py').write_text('abcd\nabc\n')
    with pytest.raises(InvalidModelOutput):
        expand_changes([{'path': 'code.py', 'edits': edits}], work=tmp_path, source='code.py', forbidden=())
    assert (tmp_path/'code.py').read_text() == 'abcd\nabc\n'


def test_edit_path_checked_before_reading_file(tmp_path):
    with pytest.raises(PermissionViolation):
        expand_changes([{'path': '../secret', 'edits': [{'old': 'a', 'new': 'b'}]}],
            work=tmp_path, source='code.py', forbidden=())


def test_unique_but_overlapping_edits_are_rejected(tmp_path):
    (tmp_path/'code.py').write_text('abcd\n', encoding='utf-8')
    with pytest.raises(InvalidModelOutput, match='重叠'):
        expand_changes([{'path': 'code.py', 'edits': [
            {'old': 'abc', 'new': 'x'}, {'old': 'bcd', 'new': 'y'}]}],
            work=tmp_path, source='code.py', forbidden=())
    assert (tmp_path/'code.py').read_text(encoding='utf-8') == 'abcd\n'


def test_long_context_is_explicit_and_focuses_requested_function():
    code = ''.join(f'def f{i}():\n    return {i}\n\n' for i in range(1000))
    shown, info = source_context(code, {'function': 'f990'})
    assert info['partial'] and info['focused_function'] == 'f990'
    assert 'def f990()' in shown and len(shown.encode()) < 7500
    assert len(shown) < len(code) / 2


def test_short_context_is_preserved_byte_for_byte():
    code = 'def f():\n    return 1\n'
    assert source_context(code) == (code, {'partial': False})
