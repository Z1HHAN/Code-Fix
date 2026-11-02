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


class StateConflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_task(value):
    fields = {'description', 'project', 'mode', 'max_rounds', 'max_calls', 'total_seconds'}
    if not isinstance(value, dict) or set(value) - fields:
        raise ValueError('未知任务字段；不能提交路径、命令或密钥')
    task = dict(description='修复闰年判断', project='leap', mode='baseline',
                max_rounds=3, max_calls=12, total_seconds=300)
    task.update(value)
    if task['project'] != 'leap' or not isinstance(task['mode'], str) or task['mode'] not in {'baseline', 'repair'}:
        raise ValueError('只支持已登记的leap项目及baseline/repair模式')
    if not isinstance(task['description'], str) or not 1 <= len(task['description'].strip()) <= 2000:
        raise ValueError('任务描述长度必须为1至2000')
    for field, maximum in [('max_rounds', 3), ('max_calls', 30), ('total_seconds', 1800)]:
        if type(task[field]) is not int or not 1 <= task[field] <= maximum:
            raise ValueError(f'{field}须为1至{maximum}的整数')
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
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, value, key=None):
        task = validate_task(value)
        if key is not None and (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key)):
            raise ValueError('幂等键须为1至100位字母数字、下划线或短横线')
        encoded = json.dumps(task, ensure_ascii=False, sort_keys=True)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        run_id = 'run-' + uuid.uuid4().hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if key:
                old = db.execute('SELECT * FROM runs WHERE idempotency_key=?', (key,)).fetchone()
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
            raise KeyError('任务不存在')
        value = dict(row)
        for name in ('task', 'result'):
            value[name] = json.loads(value[name]) if value[name] else None
        return {k: v for k, v in value.items() if k not in {'fingerprint', 'idempotency_key'}}

    def get(self, run_id):
        self.directory(run_id)
        with self.connect() as db:
            return self._decode(db.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone())

    def list(self):
        with self.connect() as db:
            return [self._decode(r) for r in db.execute('SELECT * FROM runs ORDER BY created_at DESC LIMIT 100')]

    def directory(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r'run-[a-f0-9]{32}', run_id):
            raise ValueError('非法运行编号')
        path = self.root / run_id
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('运行目录不能为链接')
        return path

    def claim(self, run_id):
        directory = self.directory(run_id)
        with self.connect() as db:
            changed = db.execute("UPDATE runs SET status='running',updated_at=? WHERE run_id=? AND status='created'",
                                 (now(), run_id)).rowcount
            if changed != 1:
                raise StateConflict('任务已启动或已结束，不能重复执行')
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
            raise ValueError('非法控制操作')
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
    def _events(db, run_id):
        return [{'sequence': r['sequence'], 'created_at': r['created_at'], **json.loads(r['payload'])}
                for r in db.execute('SELECT * FROM events WHERE run_id=? ORDER BY sequence', (run_id,))]

    def events(self, run_id):
        self.get(run_id)
        with self.connect() as db:
            return self._events(db, run_id)

    def snapshot(self, run_id):
        """Read status and events from one SQLite snapshot, including during finish."""
        self.directory(run_id)
        with self.connect() as db:
            db.execute('BEGIN')
            run = self._decode(db.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone())
            return {'run': run, 'events': self._events(db, run_id)}

    def interrupt(self, run_id):
        """Explicit operator action after confirming the old worker has stopped."""
        self.finish(run_id, 'interrupted', {'final_result': 'not_run', 'reason': '操作员确认旧进程已停止；不自动重放付费任务'})
