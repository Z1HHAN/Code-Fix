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
