"""Import pinned public assets as data, without executing or collecting them."""
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from c_runner.runner import _no_links, _snapshot

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '4257f44b0ff1181dedaedee6a447e133219fcebf'
LOCK = ROOT / 'datasets/quixbugs/upstream-lock.json'
BUNDLE = ROOT / 'datasets/quixbugs/source-bundle.json'
TITLES = dict(bitcount='二进制位计数', breadth_first_search='广度优先搜索', bucketsort='桶排序',
              depth_first_search='深度优先搜索', detect_cycle='环检测', find_first_in_sorted='查找首个匹配',
              find_in_sorted='有序查找', flatten='列表展平', gcd='最大公约数', get_factors='因数分解',
              hanoi='汉诺塔', is_valid_parenthesization='括号合法性', kheapsort='堆排序', knapsack='背包问题',
              kth='第K小元素', lcs_length='公共子串长度', levenshtein='编辑距离', lis='最长递增子序列',
              longest_common_subsequence='最长公共子序列', max_sublist_sum='最大连续子数组和', mergesort='归并排序',
              minimum_spanning_tree='最小生成树', next_palindrome='下一个回文', next_permutation='下一个排列',
              pascal='杨辉三角', possible_change='零钱组合', powerset='幂集', quicksort='快速排序',
              reverse_linked_list='链表反转', rpn_eval='逆波兰表达式', shortest_path_length='两点最短路径',
              shortest_path_lengths='全点对最短路径', shortest_paths='单源最短路径', shunting_yard='中缀转后缀',
              sieve='素数筛', sqrt='平方根', subsequences='子序列', to_base='进制转换',
              topological_ordering='拓扑排序', wrap='文本换行')


def load_skip_approvals(root=ROOT):
    """Reviewed native pytest.skip allowlist, keyed by project.

    The import may only register skips that a human explicitly approved here;
    every approved nodeid must also exist in the pre-registered native ids.
    A missing file means no task has an approved native skip.
    """
    path = Path(root) / 'configs/tasks/native-skip-approvals.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not isinstance(data.get('approvals'), dict):
        raise ValueError('原生 skip 批准文件格式不支持')
    approvals = {}
    for project, entries in data['approvals'].items():
        if not isinstance(project, str) or not isinstance(entries, dict) or not entries:
            raise ValueError('原生 skip 批准条目格式不支持')
        for nodeid, reason in entries.items():
            if not isinstance(nodeid, str) or not nodeid:
                raise ValueError('原生 skip 批准条目格式不支持')
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError('原生 skip 批准条目格式不支持')
        approvals[project] = dict(entries)
    unknown = set(approvals) - {'qb-' + name for name in TITLES}
    if unknown:
        raise ValueError('原生 skip 批准指向未登记题目：' + ', '.join(sorted(unknown)))
    return approvals


def load_bundle(path=BUNDLE):
    path = Path(path)
    if path.stat().st_size > 1_048_576:
        raise ValueError('数据包超过1MiB限制')
    raw = path.read_bytes()
    bundle = json.loads(raw)
    lock = json.loads(LOCK.read_bytes())
    if not isinstance(bundle, dict) or bundle.get('commit') != COMMIT or lock['commit'] != COMMIT:
        raise ValueError('必须使用固定上游版本')
    if not isinstance(bundle.get('files'), list):
        raise ValueError('数据包文件列表无效')
    result = {}
    for entry in bundle['files']:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            raise ValueError('数据包文件记录无效')
        name = entry['path']
        if name not in lock['files'] or name in result:
            raise ValueError('包内存在非白名单或重复文件')
        if ('content' in entry) == ('content_base64' in entry):
            raise ValueError('文件必须有一种内容编码')
        if not isinstance(entry.get('content', entry.get('content_base64')), str):
            raise ValueError('文件内容必须为字符串')
        data = (entry['content'].encode('utf-8') if 'content' in entry else
                base64.b64decode(''.join(entry['content_base64'].split()), validate=True))
        expected = lock['files'][name]
        git_hash = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if (len(data) != expected['size'] or git_hash != expected['git_blob_sha']
                or entry['git_blob_sha'] != expected['git_blob_sha'] or entry['byte_size'] != len(data)):
            raise ValueError('上游Git对象哈希或大小不匹配：' + name)
        result[name] = data
    if set(result) != set(lock['files']):
        raise ValueError('数据包缺少固定上游文件')
    return result, hashlib.sha256(raw).hexdigest()


def static_test_ids(name, files):
    """Parse AST/JSON and format IDs only. No pytest collection or source import."""
    import pytest
    from _pytest.mark import ParameterSet
    from _pytest.python import IdMaker
    if pytest.__version__ != '8.3.5':
        raise ValueError('静态参数ID登记要求固定pytest8.3.5；不能猜测其他版本的收集结果')
    test_path = f'python_testcases/test_{name}.py'
    tree = ast.parse(files[test_path], filename=test_path)
    ids = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith('test'):
            continue
        if not fn.decorator_list:
            if fn.args.args:
                raise ValueError('暂不支持带未知fixture参数的原生测试')
            ids.append(test_path + '::' + fn.name)
            continue
        decorators = fn.decorator_list
        if len(decorators) != 1:
            raise ValueError('暂不支持复杂原生测试装饰器')
        call = decorators[0]
        if (not isinstance(call, ast.Call) or ast.unparse(call.func) != 'pytest.mark.parametrize'
                or len(call.args) != 2 or call.keywords or not isinstance(call.args[0], ast.Constant)
                or call.args[0].value != 'input_data,expected' or not isinstance(call.args[1], ast.Name)
                or call.args[1].id != 'testdata'):
            raise ValueError('原生测试参数形式未支持')
        rows = [json.loads(line) for line in files[f'json_testcases/{name}.json'].decode('utf-8').splitlines()]
        if not rows or any(not isinstance(row, list) or len(row) != 2 for row in rows):
            raise ValueError('原生JSON参数必须为两列且非空')
        parameters = [ParameterSet(tuple(row), (), None) for row in rows]
        maker = IdMaker(['input_data', 'expected'], parameters, None, None, None, None, fn.name)
        ids.extend(test_path + '::' + fn.name + '[' + value + ']' for value in maker.make_unique_parameterset_ids())
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('静态测试ID缺失或重复')
    has_skip = any(isinstance(node, ast.Call) and ast.unparse(node.func).startswith(('pytest.skip', 'pytest.mark.skip', 'pytest.xfail'))
                   for node in ast.walk(tree))
    node_helper = any(isinstance(node, ast.ImportFrom) and node.module == 'node' for node in ast.walk(tree))
    return ids, has_skip, node_helper


def _publish(operation, source, destination):
    # Windows may temporarily deny moves of newly written files. Retry only
    # permission failures, with a fixed bound; persistent denial still fails.
    for attempt in range(6):
        try:
            return operation(source, destination)
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05 * 2**attempt)


def import_dataset(*, source_bundle=BUNDLE, root=ROOT, write=False):
    root = Path(root).resolve()
    files, bundle_hash = load_bundle(source_bundle)
    catalog_path = root / 'configs/tasks/catalog-v3.json'
    _no_links(catalog_path)
    old = json.loads(catalog_path.read_bytes())
    updated = json.loads(json.dumps(old))
    approvals = load_skip_approvals(root)
    plans = []
    blocked = []
    total_tests = 0
    for name in TITLES:
        ids, slow, helper = static_test_ids(name, files)
        total_tests += len(ids)
        source = f'python_programs/{name}.py'
        test_path = f'python_testcases/test_{name}.py'
        selected = ['LICENSE', 'conftest.py', source, test_path]
        if f'json_testcases/{name}.json' in files:
            selected += [f'json_testcases/{name}.json', 'python_testcases/load_testdata.py']
        if helper:
            selected.append('python_testcases/node.py')
        approved_files = {path: hashlib.sha256(files[path]).hexdigest() for path in selected}
        relative = 'task_assets/quixbugs/' + name
        destination = root / relative
        _no_links(destination)
        existed = destination.exists()
        if existed and _snapshot(destination) != approved_files:
            raise ValueError('拒绝覆盖已有且字节不同的任务资产：' + name)
        project = 'qb-' + name
        approved_skips = approvals.get(project, {})
        if approved_skips:
            if not slow:
                raise ValueError('题目未静态检测到原生 skip，不能登记批准：' + project)
            missing = set(approved_skips) - set(ids)
            if missing:
                raise ValueError('批准的原生 skip nodeid 不在预登记集合中：' + project)
        reason = '原生测试含慢测跳过；需确认完整原生测试策略后再准入' if slow and not approved_skips else None
        if reason:
            blocked.append(project)
        updated['projects'][project] = {
            'title': 'QuixBugs · ' + TITLES[name],
            'description': f'修复{name}缺陷，使预登记的原生测试通过；不修改测试。',
            'requires_isolation': True, 'admission_blocker': reason,
            'qualification_status': 'pending_docker_collection_and_baseline',
            'context_paths': ['python_testcases/node.py'] if helper else [],
            'source': {'kind': 'public_dataset', 'repository': 'https://github.com/jkoppel/QuixBugs',
                       'commit': COMMIT, 'license': 'MIT', 'imported_from': 'datasets/quixbugs/source-bundle.json',
                       'source_bundle_sha256': bundle_hash, 'upstream_redownloaded': True,
                       'retrieved_via': 'GitHub connector pinned tree and Git blobs; hashes verified',
                       'test_ids_source': 'static native AST/JSON + pytest8.3.5 ID formatter; not actual collection'},
            'profile': {'fixture_dir': relative, 'source_paths': [source], 'test_paths': [test_path],
                        'expected_nodeids': ids, 'approved_files': approved_files,
                        'allowed_skips': dict(approved_skips)},
            'knowledge_ids': ['public_tasks', 'testing'], 'knowledge_query': '公开 Python 算法 测试'}
        plans.append((destination, selected, existed))
    changed = updated != old
    if write:
        # Prepare complete new directories first. Registry is replaced only last.
        # A failed registry write leaves complete, unregistered assets for a safe retry.
        cache = root / 'runs'
        _no_links(cache)
        cache.mkdir(parents=True, exist_ok=True)
        # Windows gives mkdir(mode=0700)/TemporaryDirectory a private DACL.
        # Inherit the workspace ACL so moved public assets remain readable by
        # the developer's Git/server process outside the coding sandbox.
        stage = cache / ('qb-import-' + uuid.uuid4().hex)
        if not stage.resolve().is_relative_to(cache.resolve()):
            raise ValueError('导入临时目录越界')
        stage.mkdir(mode=0o777)
        try:
            for destination, selected, existed in plans:
                if existed:
                    continue
                prepared = stage / destination.name
                for path in selected:
                    target = prepared / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(files[path])
                destination.parent.mkdir(parents=True, exist_ok=True)
                _no_links(destination)
                if destination.exists():
                    raise ValueError('导入期间目标目录发生变化，已停止')
                _publish(Path.rename, prepared, destination)
            if changed:
                staged_catalog = stage / 'catalog.json'
                staged_catalog.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                _publish(os.replace, staged_catalog, catalog_path)
        finally:
            # Remove only our empty staging directory. Failed preparation is
            # retained for diagnosis; no recursive deletion or ACL edits.
            _no_links(stage)
            if not any(stage.iterdir()):
                stage.rmdir()
    return {'commit': COMMIT, 'write_requested': write, 'upstream_files_verified': len(files),
            'public_tasks': len(plans), 'new_asset_directories': sum(not existed for _, _, existed in plans),
            'catalog_changed': changed, 'pre_registered_native_test_count': total_tests,
            'eligible_for_docker_qualification': len(plans)-len(blocked), 'blocked_native_skip_policy': blocked,
            'public_code_executed': False, 'actual_test_collection_performed': False, 'model_api_called': False}
