from dataclasses import replace
from decimal import Decimal
import json
import sqlite3
import time

import httpx
import pytest

from app.live_model import Ledger, LiveModel, LiveSettings, ModelBlocked
from app.personal_pricing import catalog
from app.transport_accounting import TransportTrace, settle_failure


def settings(**extra):
    p = next(p for p in catalog() if p['model']=='qwen-plus' and p['mode']=='non_thinking')
    return LiveSettings('fixture-secret-do-not-log','qwen-plus','https://example.test/v1',
                        2,Decimal('5'),None,None,currency='CNY',personal_pricing=p,
                        authorization_id='approved-five',authorization_limit=Decimal('5'),**extra)


def call(model, cid='one'):
    return model.call([{'role':'user','content':'fixture'}],run_id=cid,call_id=cid,
                      max_calls=3,deadline=time.monotonic()+3)


async def event(req, name):
    await req.extensions['trace'](name, {'request':req,'secret':'must-not-be-saved'})


def good():
    return httpx.Response(200,json={'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}],
                                  'usage':{'prompt_tokens':10,'completion_tokens':1}})


def test_connect_failure_retries_once_then_releases_without_provider_usage(tmp_path):
    seen=[]
    async def handle(req):
        seen.append(1)
        await event(req,'connection.connect_tcp.started')
        raise httpx.ConnectError('fixture-secret-do-not-log',request=req)
    ledger=Ledger(tmp_path)
    v=call(LiveModel(settings(),ledger,transport=httpx.MockTransport(handle)))
    assert len(seen)==v['attempts']==2
    assert v['cost']['status']=='not_sent' and v['usage'] is None
    assert not v['transport_request_started'] and v['cost']['amount_cny']=='0'
    assert ledger.summary('one')['status']=='not_sent'
    ledger.reserve('next','next',settings(),3,100)
    with sqlite3.connect(ledger.path) as db:
        receipt=db.execute('select receipt from transport_settlements').fetchone()[0]
    assert 'must-not-be-saved' not in receipt and 'fixture-secret-do-not-log' not in json.dumps(v)


def test_single_connect_recovery_sends_only_one_model_request(tmp_path):
    seen=[]
    async def handle(req):
        seen.append(1)
        if len(seen)==1:
            await event(req,'connection.start_tls.started')
            raise httpx.ConnectTimeout('fixture',request=req)
        await event(req,'http11.send_request_headers.started')
        return good()
    ledger=Ledger(tmp_path);v=call(LiveModel(settings(),ledger,transport=httpx.MockTransport(handle)))
    assert v['status']=='ok' and v['attempts']==2 and v['cost']['status']=='estimated'
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('select count(*) from personal_calls').fetchone()[0]==1


def test_read_failure_holds_upper_bound_never_retries_or_becomes_zero(tmp_path):
    seen=[]
    async def handle(req):
        seen.append(1);await event(req,'http11.send_request_headers.started')
        raise httpx.ReadTimeout('fixture',request=req)
    ledger=Ledger(tmp_path);s=settings()
    v=call(LiveModel(s,ledger,transport=httpx.MockTransport(handle)))
    assert len(seen)==1 and v['cost']['status']=='reserved_unknown'
    assert v['cost']['amount_cny'] is None and Decimal(v['cost']['reserved_cny'])>0
    summary=ledger.summary('one')
    assert summary['status']=='unknown' and summary['amount_cny'] is None
    assert summary['budget_handling']=='reserved_upper_bound'
    ledger.reserve('next','next',s,3,100)


def test_hold_counts_full_reservation_toward_cap_before_next_request(tmp_path):
    s=replace(settings(),max_output_tokens=20,authorization_limit=Decimal('.00015'))
    ledger=Ledger(tmp_path);ledger.reserve('one','one',s,3,100)
    settle_failure(ledger,'one',s,{'outcome':'possibly_sent'})
    with pytest.raises(ModelBlocked,match='累计费用授权'):
        ledger.reserve('next','next',s,3,100)


@pytest.mark.parametrize('change',[{'authorization_id':'other'}, {'authorization_id':None,'authorization_limit':None}])
def test_cannot_escape_hold_by_changing_authorization(tmp_path,change):
    ledger=Ledger(tmp_path);s=settings();ledger.reserve('one','one',s,3,100)
    settle_failure(ledger,'one',s,{'outcome':'possibly_sent'})
    with pytest.raises(ModelBlocked,match='跨授权'):
        ledger.reserve('two','two',replace(s,**change),3,100)


def test_old_unknown_is_not_automatically_reclassified(tmp_path):
    ledger=Ledger(tmp_path);s=settings();ledger.reserve('old','old',s,3,100)
    ledger.settle('old',None,s)
    with pytest.raises(ValueError,match='历史未知'):
        settle_failure(ledger,'old',s,{'outcome':'not_sent'})
    with pytest.raises(ModelBlocked,match='未知'):
        ledger.reserve('two','two',s,3,100)
    assert ledger.summary('old')['amount_cny'] is None


def test_over_limit_usage_cannot_be_converted_to_a_safe_hold(tmp_path):
    transport=httpx.MockTransport(lambda req:httpx.Response(200,json={
        'usage':{'prompt_tokens':1000000,'completion_tokens':1000000}}))
    ledger=Ledger(tmp_path);v=call(LiveModel(settings(),ledger,transport=transport))
    assert v['cost']['status']=='unknown'
    with pytest.raises(ModelBlocked):ledger.reserve('two','two',settings(),3,100)


def test_missing_usage_holds_only_with_cumulative_authorization(tmp_path):
    transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'choices':[]}))
    ledger=Ledger(tmp_path);v=call(LiveModel(settings(),ledger,transport=transport))
    assert v['failure_class']=='cost_unknown' and v['cost']['status']=='reserved_unknown'
    ledger.reserve('next','next',settings(),3,100)


def test_personal_daily_budget_can_continue_while_retaining_full_hold(tmp_path):
    ledger=Ledger(tmp_path);s=replace(settings(),authorization_id=None,authorization_limit=None)
    ledger.reserve('one','one',s,3,100)
    assert settle_failure(ledger,'one',s,{'outcome':'possibly_sent'})['status']=='reserved_unknown'
    ledger.reserve('two','two',s,3,100)
    assert ledger.summary('one')['amount_cny'] is None


def test_previous_day_hold_still_occupies_today_daily_limit(tmp_path):
    ledger=Ledger(tmp_path);s=replace(settings(),max_output_tokens=20)
    ledger.reserve('one','one',s,3,100);settle_failure(ledger,'one',s,{'outcome':'possibly_sent'})
    with sqlite3.connect(ledger.path) as db:
        db.execute("update personal_calls set day='2000-01-01'")
    with pytest.raises(ModelBlocked,match='每日费用'):
        ledger.reserve('two','two',replace(s,daily_limit=Decimal('.00015')),3,100)


def test_reconcile_held_request_keeps_before_and_releases_only_its_reservation(tmp_path):
    from app.reconciliation import reconcile
    ledger=Ledger(tmp_path);s=settings();ledger.reserve('one','one',s,3,100)
    settle_failure(ledger,'one',s,{'outcome':'possibly_sent'})
    proof=tmp_path/'receipt.txt';proof.write_text('provider per-request fixture')
    receipt=reconcile(ledger,call_id='one',currency='CNY',amount='.0001',evidence=proof,
                      operator='fixture',reason='test',no_inflight=True)
    assert receipt['before']['status']=='reserved_unknown'
    assert ledger.summary('one')['status']=='reconciled'
    ledger.reserve('next','next',s,3,100)


def test_connect_exception_after_send_is_not_mistaken_for_not_sent(tmp_path):
    async def handle(req):
        await event(req,'http11.send_request_headers.started')
        raise httpx.ConnectError('late error',request=req)
    v=call(LiveModel(settings(),Ledger(tmp_path),transport=httpx.MockTransport(handle)))
    assert v['attempts']==1 and v['cost']['status']=='reserved_unknown'


def test_legacy_cannot_ignore_personal_held_cost(tmp_path):
    ledger=Ledger(tmp_path);s=settings();ledger.reserve('one','one',s,3,100)
    settle_failure(ledger,'one',s,{'outcome':'possibly_sent'})
    legacy=replace(s,personal_pricing=None,currency='USD',input_per_million=Decimal(1),output_per_million=Decimal(1))
    with pytest.raises(ModelBlocked):ledger.reserve('two','two',legacy,3,100)


def test_explicit_historical_carry_preserves_unknown_receipt_and_never_sets_zero(tmp_path):
    from app.transport_accounting import carry_unknown_reservation
    s=settings();ledger=Ledger(tmp_path);ledger.reserve('old','old',s,3,100);ledger.settle('old',None,s)
    p=tmp_path/'original.json';p.write_text(json.dumps({'call_id':'old','model':s.model,'usage':None,
        'error_type':'ConnectError','price_version':s.personal_pricing['version']}))
    receipt=carry_unknown_reservation(ledger,call_id='old',evidence=p,operator='fixture',reason='retain original cap')
    assert receipt['before']['status']=='unknown' and receipt['before']['actual'] is None
    assert ledger.summary('old')['amount_cny'] is None
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('select status,actual from personal_calls').fetchone()==('reserved_unknown',None)
    ledger.reserve('new','new',s,3,100)


@pytest.mark.parametrize('field,value',[('error_type','InvalidModelOutput'),('usage',{'prompt_tokens':999}),('price_version','different')])
def test_historical_carry_rejects_unsupported_evidence(tmp_path,field,value):
    from app.transport_accounting import carry_unknown_reservation
    s=settings();ledger=Ledger(tmp_path);ledger.reserve('old','old',s,3,100);ledger.settle('old',None,s)
    evidence={'call_id':'old','model':s.model,'usage':None,'error_type':'ConnectError','price_version':s.personal_pricing['version']}
    evidence[field]=value;p=tmp_path/'original.json';p.write_text(json.dumps(evidence))
    with pytest.raises(ValueError):carry_unknown_reservation(ledger,call_id='old',evidence=p,operator='fixture',reason='test')
    assert ledger.summary('old')['status']=='unknown'
