"""Offline trust-boundary, cancellation, settlement and pagination contracts."""
import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from app.run_store import RunStore, validate_task
from app.live_model import Ledger
from app.reconciliation import reconcile
from app.split_runner import SplitDockerRunner
from app.runner import code_version

def test_split_container_mounts_exclude_tests_and_reports_from_candidate(tmp_path, monkeypatch):
    runner = SplitDockerRunner(); runner.ready=True; runner.executable='docker-stub'; runner.image='sha256:'+'a'*64
    work = runner.task.prepare(tmp_path/'work')
    attack = b"raise RuntimeError('must never execute on host or trusted container')\n"
    (work/runner.task.source).write_bytes(attack)
    commands=[]
    def execute(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command,0,b'',b'')
    def trusted(self, snapshot, output, extra, timeout, folder):
        assert attack not in (snapshot/runner.task.source).read_bytes()
        assert 'return invoke(' in (snapshot/runner.task.source).read_text()
        assert (snapshot/'tests/test_leap.py').is_file()
        command = self.command('trusted',snapshot,output,Path('entry.py'),extra)
        assert any('target=/rpc,readonly' in str(x) for x in command)
        return dict(timed_out=True,cleanup_passed=True,artifact_channel=None,exit_code=None)
    monkeypatch.setattr('app.split_runner.subprocess.run',execute)
    class LogDouble:
        def __init__(self, executable, name, folder):
            for stream in ('stdout', 'stderr'):
                (folder / f'candidate-{stream}.txt').write_bytes(b'')
        def finish(self):
            return True, False
    monkeypatch.setattr('app.candidate_logs.CandidateLogs', LogDouble)
    monkeypatch.setattr('app.docker_runner.DockerRunner._execute',trusted)
    result=runner.run(work,tmp_path/'evidence',phase='development',timeout=10,expected_version=code_version(work,(runner.task.source,)))
    command=next(c for c in commands if 'run' in c)
    assert '-d' in command
    candidate=tmp_path/'evidence/candidate-source'
    assert sorted(p.relative_to(candidate).as_posix() for p in candidate.rglob('*') if p.is_file())==[runner.task.source]
    assert result['verification_status']=='inconclusive'
    assert commands[-1][1:3]==['volume','rm']

def test_interrupt_is_terminal_and_late_worker_cannot_overwrite(tmp_path):
    store=RunStore(tmp_path); run=store.create({}); rid=run['run_id']; store.claim(rid)
    store.interrupt(rid)
    assert store.get(rid)['control']=='stop'
    assert not store.finish_if_running(rid,'success',{'final_result':'passed'})
    assert store.get(rid)['status']=='interrupted'
    assert store.get(rid)['result']['final_result']=='not_run'

def test_concurrent_events_paginate_without_payload_leak(tmp_path):
    store=RunStore(tmp_path); rid=store.create({})['run_id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: store.event(rid,{'event':'sample','n':n,'nested':{'raw_text':'secret'}}),range(40)))
    first=store.events(rid,limit=17); second=store.events(rid,first[-1]['sequence'],limit=30)
    assert len(first)==17 and len(second)==23
    assert len({r['n'] for r in first+second})==40
    assert all('raw_text' not in r['nested'] for r in first+second)
    with store.connect() as db: assert db.execute('PRAGMA journal_mode').fetchone()[0]=='wal'

@pytest.mark.parametrize('currency',['USD','CNY'])
def test_reconciliation_preserves_receipt_and_disallows_overwrite(tmp_path,currency):
    ledger=Ledger(tmp_path); proof=tmp_path/'invoice.txt'; proof.write_text('provider invoice fixture')
    with sqlite3.connect(ledger.path) as db:
        if currency=='USD':
            db.execute("INSERT INTO calls VALUES ('call','run','old-day',100,'unknown',NULL,'p','m','v')")
        else:
            db.execute("INSERT INTO cny_calls VALUES ('call','run','account','provider','m','v','old-day',100,NULL,'unknown')")
    args=dict(call_id='call',currency=currency,amount='0.0002',evidence=proof,operator='A-test',reason='provider invoice',no_inflight=True)
    receipt=reconcile(ledger,**args)
    assert receipt['before']['status']=='unknown'
    assert len(receipt['evidence_sha256'])==64
    with pytest.raises(ValueError): reconcile(ledger,**args)
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('SELECT count(*) FROM reconciliations').fetchone()[0]==1

def test_engineering_caps_do_not_expand(tmp_path):
    assert validate_task({'max_calls':30,'total_seconds':1800})['max_calls']==30
    with pytest.raises(ValueError): validate_task({'max_calls':31})

def test_worker_after_interrupt_returns_without_terminal_exception(tmp_path,monkeypatch):
    from app.service import TaskService
    service=TaskService(tmp_path)
    rid=service.store.create({'mode':'repair'})['run_id'];service.store.claim(rid)
    def run(self,run_id):
        service.store.interrupt(run_id)
        return 'success',{'final_result':'passed'}
    monkeypatch.setattr('app.workflow_v3.Workflow.run',run)
    service.run_claimed(rid)
    assert service.store.get(rid)['status']=='interrupted'
    assert len([e for e in service.store.events(rid) if e['event']=='finished'])==1

@pytest.mark.parametrize('value',['NaN','-1','0.0000001','Infinity','1000000000000000000'])
def test_invalid_reconciliation_cannot_release_unknown(tmp_path,value):
    ledger=Ledger(tmp_path)
    proof=tmp_path/'proof';proof.write_text('invoice')
    with pytest.raises(ValueError):
        reconcile(ledger,call_id='none',currency='CNY',amount=value,evidence=proof,operator='A',reason='test',no_inflight=True)

def test_http_event_page_cursor_and_validation(tmp_path):
    from fastapi.testclient import TestClient
    from app.api import create_app
    store=RunStore(tmp_path);rid=store.create({})['run_id']
    for n in range(4):store.event(rid,{'event':'test','n':n,'raw_text':'private'})
    with TestClient(create_app(tmp_path)) as client:
        base=f'/api/v3/tasks/{rid}/events'
        first=client.get(base+'?limit=2').json()
        second=client.get(base+f'?limit=2&after_sequence={first[-1]["sequence"]}').json()
        assert [x['n'] for x in first+second]==[0,1,2,3]
        assert all('raw_text' not in x for x in first+second)
        assert client.get(base+'?limit=501').status_code==422
