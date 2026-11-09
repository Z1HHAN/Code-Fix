"""SQLite task state shared by CLI and HTTP workers; no automatic paid replay."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid
from .errors import InputValidationError, RunNotFound


class StateConflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_task(value):
    fields = {'description', 'project', 'mode', 'max_rounds', 'max_calls', 'total_seconds', 'config_id'}
    if not isinstance(value, dict) or set(value) - fields:
        raise InputValidationError('未知任务字段；不能提交路径、命令或密钥')
    task = dict(description='修复闰年判断', project='leap', mode='baseline',
                max_rounds=3, max_calls=12, total_seconds=300)
    task.update(value)
    if 'config_id' in task and (not isinstance(task['config_id'], str) or not re.fullmatch(r'cfg-[a-f0-9]{64}', task['config_id'])):
        raise InputValidationError('非法配置编号')
    from .tasks_v3 import get_task
    definition = get_task(task['project'])
    if 'description' not in value:
        task['description'] = definition.data['description']
    if not isinstance(task['mode'], str) or task['mode'] not in {'baseline', 'repair'}:
        raise InputValidationError('只支持baseline/repair模式')
    if not isinstance(task['description'], str) or not 1 <= len(task['description'].strip()) <= 2000:
        raise InputValidationError('任务描述长度必须为1至2000')
    from .runtime_limits import runtime_limits
    limits = runtime_limits()
    for field in ('max_rounds', 'max_calls', 'total_seconds'):
        maximum = limits[field]
        if type(task[field]) is not int or not 1 <= task[field] <= maximum:
            raise InputValidationError(f'{field}须为1至{maximum}的整数')
    return task


class RunStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / 'tasks.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, task TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, result TEXT, control TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS run_archives (
                    run_id TEXT PRIMARY KEY, archived_at TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, value, key=None):
        task = validate_task(value)
        if 'config_id' in task:
            from .configuration import ConfigurationStore
            configuration = ConfigurationStore(self).get(task['config_id'])
            if any(task[k] != configuration['parameters'][k] for k in configuration['parameters']):
                raise InputValidationError('任务参数与保存配置不一致；请保存新配置')
        if key is not None and (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key)):
            raise InputValidationError('幂等键须为1至100位字母数字、下划线或短横线')
        encoded = json.dumps(task, ensure_ascii=False, sort_keys=True)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        run_id = 'run-' + uuid.uuid4().hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if key:
                old = db.execute('SELECT r.*,a.archived_at FROM runs r LEFT JOIN run_archives a ON a.run_id=r.run_id WHERE r.idempotency_key=?', (key,)).fetchone()
                if old:
                    if old['fingerprint'] != fingerprint:
                        raise StateConflict('同一幂等键不能对应不同任务')
                    return self._decode(old)
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)',
                       (run_id, encoded, fingerprint, key, 'created', now(), now(), None, None))
        return self.get(run_id)

    @staticmethod
    def _decode(row):
        if row is None:
            raise RunNotFound('任务不存在')
        value = dict(row)
        value.setdefault('archived_at', None)
        for name in ('task', 'result'):
            value[name] = json.loads(value[name]) if value[name] else None
        return {k: v for k, v in value.items() if k not in {'fingerprint', 'idempotency_key'}}

    def get(self, run_id):
        self.directory(run_id)
        with self.connect() as db:
            return self._decode(db.execute('SELECT r.*,a.archived_at FROM runs r LEFT JOIN run_archives a ON a.run_id=r.run_id WHERE r.run_id=?', (run_id,)).fetchone())

    def list(self, *, include_archived=False):
        with self.connect() as db:
            return [self._decode(r) for r in db.execute('''SELECT r.*,a.archived_at FROM runs r
                LEFT JOIN run_archives a ON a.run_id=r.run_id
                WHERE (? OR a.run_id IS NULL) ORDER BY r.created_at DESC LIMIT 100''', (include_archived,))]

    def set_archived(self, run_id, archived):
        self.directory(run_id)
        if type(archived) is not bool:
            raise InputValidationError('归档状态须为布尔值')
        with self.connect() as db:
            # Serialize against claim: a running task must never become hidden.
            db.execute('BEGIN IMMEDIATE')
            run = db.execute('SELECT status FROM runs WHERE run_id=?', (run_id,)).fetchone()
            if run is None:
                raise RunNotFound('任务不存在')
            if archived and run['status'] == 'running':
                raise StateConflict('运行中的任务不能归档')
            if archived:
                changed = db.execute('INSERT OR IGNORE INTO run_archives VALUES (?,?)', (run_id, now())).rowcount
            else:
                changed = db.execute('DELETE FROM run_archives WHERE run_id=?', (run_id,)).rowcount
            if changed:
                self._event(db, run_id, {'event': 'archived' if archived else 'restored'})
        return self.get(run_id)

    def directory(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r'run-[a-f0-9]{32}', run_id):
            raise InputValidationError('非法运行编号')
        path = self.root / run_id
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('运行目录不能为链接')
        return path

    def claim(self, run_id):
        directory = self.directory(run_id)
        with self.connect() as db:
            changed = db.execute("UPDATE runs SET status='running',updated_at=? WHERE run_id=? AND status='created' AND NOT EXISTS (SELECT 1 FROM run_archives WHERE run_archives.run_id=runs.run_id)",
                                 (now(), run_id)).rowcount
            if changed != 1:
                raise StateConflict('任务已归档、已启动或已结束；已归档任务须先恢复')
            directory.mkdir(exist_ok=True)
            self._event(db, run_id, {'event': 'started'})

    def finish(self, run_id, status, result):
        if status not in {'stopped', 'blocked', 'failed', 'success', 'paused', 'interrupted'}:
            raise ValueError('非法终态')
        with self.connect() as db:
            changed = db.execute('UPDATE runs SET status=?,result=?,updated_at=? WHERE run_id=? AND status=\'running\'',
                                 (status, json.dumps(result, ensure_ascii=False), now(), run_id)).rowcount
            if not changed:
                raise StateConflict('仅运行中的任务可以写入终态')
            self._event(db, run_id, {'event': 'finished', 'status': status, 'result': result})

    def request_control(self, run_id, action):
        if not isinstance(action, str) or action not in {'pause', 'stop'}:
            raise InputValidationError('非法控制操作')
        self.get(run_id)
        with self.connect() as db:
            # Stop is irreversible within this run; a later pause cannot weaken it.
            if not db.execute("UPDATE runs SET control=CASE WHEN control='stop' THEN 'stop' ELSE ? END,updated_at=? WHERE run_id=? AND status='running'",
                              (action, now(), run_id)).rowcount:
                raise StateConflict('只能控制运行中的任务')
            effective = db.execute('SELECT control FROM runs WHERE run_id=?', (run_id,)).fetchone()[0]
            self._event(db, run_id, {'event': 'control_requested', 'action': action, 'effective_action': effective})

    @staticmethod
    def _event(db, run_id, payload):
        db.execute('INSERT INTO events (run_id,created_at,payload) VALUES (?,?,?)',
                   (run_id, now(), json.dumps(payload, ensure_ascii=False)))

    def event(self, run_id, payload):
        with self.connect() as db:
            self._event(db, run_id, payload)

    @staticmethod
    def _events(db, run_id, after_sequence=0):
        return [{'sequence': r['sequence'], 'created_at': r['created_at'], **json.loads(r['payload'])}
                for r in db.execute('SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence',
                                    (run_id, after_sequence))]

    def events(self, run_id, after_sequence=0, limit=200):
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError('事件游标须为非负整数')
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("事件页大小须为1至500")
        self.get(run_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?", (run_id, after_sequence, limit))
            def trim(value):
                if isinstance(value, dict): return {k: trim(v) for k,v in value.items() if k not in {"raw_text", "messages", "api_key"}}
                if isinstance(value, list): return [trim(v) for v in value]
                if isinstance(value, str): return value[:6000]
                return value
            return [{"sequence": r["sequence"], "created_at": r["created_at"], **trim(json.loads(r["payload"]))} for r in rows]

    def snapshot(self, run_id):
        """Read status and events from one SQLite snapshot, including during finish."""
        self.directory(run_id)
        with self.connect() as db:
            db.execute('BEGIN')
            run = self._decode(db.execute('SELECT r.*,a.archived_at FROM runs r LEFT JOIN run_archives a ON a.run_id=r.run_id WHERE r.run_id=?', (run_id,)).fetchone())
            return {'run': run, 'events': self._events(db, run_id)}

    def interrupt(self, run_id):
        """Persist stop; late worker results cannot overwrite this terminal state."""
        self.request_control(run_id, 'stop')
        self.finish_if_running(run_id, 'interrupted', {'final_result': 'not_run',
            'reason': '操作员请求中断，禁止后续派发；在途工具清理与HTTP费用仍需确认，不自动重放',
            'handling_status': 'pending_human'})

    def finish_if_running(self, run_id, status, result):
        """Worker termination cannot overwrite an operator's terminal record."""
        try:
            self.finish(run_id, status, result)
            return True
        except StateConflict:
            if self.get(run_id)['status'] in {'stopped','blocked','failed','success','paused','interrupted'}:
                return False
            raise
