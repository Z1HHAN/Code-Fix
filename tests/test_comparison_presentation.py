import json
from fastapi.testclient import TestClient
from app.api import create_app
from app.run_store import RunStore
from app.comparisons import ComparisonStore
from test_workflow_v3 import execute


def record(tmp_path):
    single=execute(tmp_path,['assertion_failed','passed','passed'],{'strategy':'single','max_rounds':1})[-1]
    multi=execute(tmp_path,['assertion_failed','assertion_failed'],{'strategy':'dynamic','max_rounds':1})[-1]
    return ComparisonStore(RunStore(tmp_path)).create({'kind':'agents','title':'offline presentation',
        'scope':'offline','pairs':[{'project':'leap','single':single,'dynamic':multi,'not_run_reason':{}}]})


def test_listing_supplies_type_and_completeness_without_changing_snapshot(tmp_path):
    c=record(tmp_path);store=RunStore(tmp_path);comparisons=ComparisonStore(store)
    before=[store.snapshot(r['run_id']) for r in store.list()]
    row=comparisons.list()[0]
    assert row['kind']=='agents' and row['planned']==row['ran']==2
    with TestClient(create_app(tmp_path)) as client:
        metrics=client.get('/api/v3/comparisons/'+c['comparison_id']+'/metrics').json()
        assert metrics['comparison_id']==c['comparison_id']
        assert metrics['groups']['single']['handoff_packages']==0
        assert metrics['groups']['dynamic']['handoff_packages']==1
        assert metrics['groups']['dynamic']['handoff_rate']==1
        assert metrics['groups']['dynamic']['human_acceptance_records']==0
    assert comparisons.get(c['comparison_id'])==c
    assert [store.snapshot(r['run_id']) for r in store.list()]==before


def test_missing_or_mismatched_handoff_stays_unknown_not_zero(tmp_path):
    c=record(tmp_path);store=RunStore(tmp_path);comparisons=ComparisonStore(store)
    path=store.directory(c['pairs'][0]['dynamic']['run_id'])/'handoff.json'
    proof=json.loads(path.read_text(encoding='utf-8'));proof['run_id']='wrong'
    path.write_text(json.dumps(proof),encoding='utf-8')
    m=comparisons.metrics(c['comparison_id'])['groups']['dynamic']
    assert m['handoff_packages'] is None and m['handoff_rate'] is None
    assert m['handoff_missing_evidence']==1


def test_explicit_followup_updates_latest_only_and_counts_all_attempts(tmp_path):
    from app.comparison_updates import register
    c=record(tmp_path);store=RunStore(tmp_path);comparisons=ComparisonStore(store)
    rid=execute(tmp_path,['assertion_failed','passed','passed'],{'strategy':'dynamic','max_rounds':1})[-1]
    view=register(comparisons,c['comparison_id'],[rid])
    assert view['summary']['dynamic']['success']==1
    assert view['summary']['dynamic']['failed']==0
    assert view['pairs'][0]['dynamic']['original_run_id']==c['pairs'][0]['dynamic']['run_id']
    assert view['summary']['dynamic']['requests']==c['summary']['dynamic']['requests']+view['followups'][0]['requests']
    assert comparisons.get(c['comparison_id'])==c
    assert register(comparisons,c['comparison_id'],[rid])==view
    with TestClient(create_app(tmp_path)) as client:
        exported=client.get('/api/v3/comparisons/'+c['comparison_id']+'/export?format=json&view=latest').json()
        assert exported==view
        assert client.get('/api/v3/comparisons/'+c['comparison_id']+'/export?view=bad').status_code==422


def test_followup_rejects_successful_original_and_changed_test_identity(tmp_path,monkeypatch):
    import pytest
    from app.comparison_updates import register
    from app.errors import InputValidationError
    c=record(tmp_path);comparisons=ComparisonStore(RunStore(tmp_path))
    rid=execute(tmp_path,['assertion_failed','passed','passed'],{'strategy':'single','max_rounds':1})[-1]
    with pytest.raises(InputValidationError,match='未成功'):
        register(comparisons,c['comparison_id'],[rid])
    with pytest.raises(InputValidationError,match='不重复'):
        register(comparisons,c['comparison_id'],[rid,rid])
    rid=execute(tmp_path,['assertion_failed','passed','passed'],{'strategy':'dynamic','max_rounds':1})[-1]
    method=comparisons._run
    def changed(*args):
        row=method(*args);row['frozen']['identity']['test_suite_version']='different';return row
    monkeypatch.setattr(comparisons,'_run',changed)
    with pytest.raises(InputValidationError,match='冻结测试'):
        register(comparisons,c['comparison_id'],[rid])


def test_known_costs_and_unknown_reserve_remain_separate(tmp_path):
    import sqlite3
    from types import SimpleNamespace
    from app.comparison_updates import costs
    with sqlite3.connect(tmp_path/'model-ledger.sqlite3') as db:
        db.execute('CREATE TABLE personal_calls (run_id,currency,actual,reserved)')
        db.executemany('INSERT INTO personal_calls VALUES (?,?,?,?)', [('a','CNY',100000,200000),('a','CNY',None,80000),('b','CNY',50000,100000)])
    rows=[{'run_id':'a','cost':{'amount_cny':None}},{'run_id':'b','cost':{'amount_cny':'0.05'}}]
    result=costs(SimpleNamespace(root=tmp_path),rows)
    assert result=={'known_subtotal':'0.15','unknown_requests_or_runs':1,'reserved_cny':'0.08','total':None}
