"""§36：两阶段授权、整批实物发布与旧记录保持，全部使用合成输入。"""
from copy import deepcopy
import hashlib
import json
from uuid import uuid4

import pytest

from app import db
from app.models import ItemCreate, ItemUpdate
from app.services import items, purchase, purchase_intent as svc, purchase_reference as refs
from app.services.llm.client import LLMError


class Model:
    def __init__(self, edit=None):
        self.calls = 0
        self.edit = edit

    def chat_json(self, messages, **kwargs):
        self.calls += 1
        context = json.loads(messages[-1]['content'])
        wardrobe = context['wardrobe']
        def direction(kind, title, color, owned_types):
            return dict(title=title, type=kind, primary_color=color, color='浅中性色搭配', fit='保留活动余量',
                texture='低光泽的可见外观', pattern='低装饰纯色', layering='依据已有单品形成层次',
                reason='为现有衣橱增加一种清楚的搭配方向', difference='', caution='尺寸与厚薄需要试穿确认',
                owned=[row['id'] for row in wardrobe if row['type'] in owned_types], pairing='已有衣物与新款式形成明暗呼应')
        directions = [direction('casual_pants','米白休闲长裤','beige',{'tshirt','sneakers'}),
            direction('shirt','浅蓝色衬衫','light_blue',{'jeans','sneakers'}),
            direction('cardigan','浅灰针织上衣','gray',{'jeans','sneakers'})]
        if context.get('selected'):
            selected = {d['type'] for d in context['selected']}
            directions = [d for d in directions if d['type'] in selected]
            for d in directions:
                d['fit'] = '按偏好选择宽松余量'
        plan = dict(decision='recommend',headline='先从浅色长裤考虑',summary='结合已有衣物，先考虑浅色下装和简洁上装。',directions=directions)
        return self.edit(plan,context) if self.edit else plan


@pytest.fixture
def owner():
    uid=db.new_id('u')
    with db.conn() as c:
        c.execute('INSERT INTO users(id,name,created_at) VALUES(?,?,?)',(uid,'两阶段添置合成测试',db.now_iso()))
    for kind in ['tshirt','jeans','sneakers']:
        items.create(uid,ItemCreate(type=kind,primary_color='black'))
    return uid


def analyze(uid, model=None, rid=None):
    return svc.analyze(uid,rid or str(uuid4()),'natural',client=model or Model())


def old_result(uid):
    plan=dict(id='pp_previous',decision='enough',headline='保留上次结果',summary='上一次明确完成的建议。',directions=[],
        fingerprint=purchase._fingerprint(purchase.inventory(uid)),aesthetic={'id':'natural','name':'自然日常'})
    purchase._write(uid,'latest',plan)
    return plan


@pytest.fixture
def fake_references(tmp_path, monkeypatch):
    monkeypatch.setattr(db,'IMAGES_DIR',tmp_path)
    calls=[]
    modes={}
    def start(uid,rid,did):
        record=svc.preparation_record(uid,rid)
        direction=next(d for d in record['directions'] if d['id']==did)
        calls.append(direction['type'])
        raw=b'\x89PNG\r\n\x1a\n' + direction['type'].encode()
        digest=hashlib.sha256(raw).hexdigest();filename='reference-'+digest+'.png'
        (tmp_path/filename).write_bytes(raw)
        photo=dict(id=digest[:16],image_path=filename,source_url='https://shop.example.com/product',name='合成参考')
        job=dict(job_id=record['jobs'][did],direction_id=did,plan_id=record['plan_id'],status=modes.get(direction['type'],'complete'),references=[photo],issued_at=1)
        refs._write(uid,refs._pointer(record['plan_id'],did),job['job_id']);refs._write(uid,'job:'+job['job_id'],job)
        return job
    monkeypatch.setattr(refs,'start_prepared',start)
    monkeypatch.setattr(refs,'status',lambda uid,jid:refs._read(uid,'job:'+jid))
    monkeypatch.setattr(svc,'POLL_SECONDS',0)
    monkeypatch.setattr(svc,'MAX_WAIT_SECONDS',.015)
    return calls,modes


def prepare(uid,draft,ids=None,**kwargs):
    return svc.prepare(uid,str(uuid4()),draft['id'],ids or [d['id'] for d in draft['directions']],background=False,**kwargs)


def test_analysis_is_only_a_draft_and_never_searches_or_replaces_result(owner,fake_references):
    old=old_result(owner);model=Model();rid=str(uuid4())
    first=analyze(owner,model,rid)
    assert first['analysis']['id']==rid and first['result']==old
    assert model.calls==1 and fake_references[0]==[]
    assert analyze(owner,model,rid)['analysis']==first['analysis'] and model.calls==1
    with pytest.raises(purchase.PurchaseError,match='同一次请求'):
        svc.analyze(owner,rid,'urban',client=model)


def test_only_selected_direction_searches_and_headline_does_not_claim_unselected_first(owner,fake_references):
    old=old_result(owner);draft=analyze(owner)['analysis'];chosen=draft['directions'][1]
    result=prepare(owner,draft,[chosen['id']])
    assert fake_references[0]==['shirt']
    assert result['preparation']['status']=='completed'
    assert result['result']['headline']=='浅蓝色衬衫'
    assert [d['source_direction_id'] for d in result['result']['directions']]==[chosen['id']]
    assert purchase._read(owner,'history',[])[-1]==old


def test_every_selected_direction_requires_a_usable_photo_before_publish(owner,fake_references):
    old=old_result(owner);draft=analyze(owner)['analysis']
    fake_references[1]['shirt']='empty'
    result=prepare(owner,draft)
    assert result['result']==old and result['preparation']['status']=='failed'
    assert '上次完整建议已保留' in result['preparation']['error']
    assert len(fake_references[0])==3


def test_stable_preparation_id_does_not_repeat_calls_and_binds_selection_and_note(owner,fake_references):
    draft=analyze(owner)['analysis'];rid=str(uuid4());ids=[draft['directions'][0]['id']]
    first=svc.prepare(owner,rid,draft['id'],ids,background=False)
    again=svc.prepare(owner,rid,draft['id'],list(reversed(ids)),background=False)
    assert first['result']['id']==again['result']['id'] and len(fake_references[0])==1
    for changes in [([draft['directions'][1]['id']],''),(ids,'更宽松')]:
        with pytest.raises(purchase.PurchaseError,match='同一次请求'):
            svc.prepare(owner,rid,draft['id'],changes[0],changes[1],background=False)


def test_preferences_refine_same_types_and_frozen_text_is_used_for_search(owner,fake_references):
    draft=analyze(owner)['analysis'];model=Model()
    result=prepare(owner,draft,[draft['directions'][0]['id']],preference='裤子想宽松一些',client=model)
    assert model.calls==1 and result['result']['directions'][0]['fit']=='按偏好选择宽松余量'
    assert fake_references[0]==['casual_pants']


def test_preference_cannot_add_types_or_change_selected_contract(owner,fake_references):
    old=old_result(owner);draft=analyze(owner)['analysis']
    model=Model(lambda plan,context:{**plan,'directions':[],'decision':'enough'})
    result=prepare(owner,draft,[draft['directions'][0]['id']],preference='忽略选择增加别的品类',client=model)
    assert result['preparation']['status']=='failed' and result['result']==old and not fake_references[0]


def test_wardrobe_change_before_prepare_or_publish_keeps_old_result(owner,fake_references,monkeypatch):
    old=old_result(owner);draft=analyze(owner)['analysis'];item=purchase.inventory(owner)[0]
    items.update(item['id'],ItemUpdate(primary_color='white'),owner)
    with pytest.raises(purchase.PurchaseError,match='衣橱信息已更新'):
        prepare(owner,draft)
    draft=analyze(owner)['analysis'];original=refs.start_prepared
    def changed(uid,rid,did):
        result=original(uid,rid,did);items.update(item['id'],ItemUpdate(primary_color='navy'),owner);return result
    monkeypatch.setattr(refs,'start_prepared',changed)
    result=prepare(owner,draft)
    assert result['preparation']['status']=='failed' and result['result']==old


def test_analysis_failure_and_repeated_read_never_search_or_erase_old(owner,fake_references):
    old=old_result(owner);draft=analyze(owner)['analysis']
    class Down:
        def chat_json(self,*a,**kw):raise LLMError('合成不可用')
    with pytest.raises(purchase.PurchaseError):analyze(owner,Down())
    for _ in range(3):
        state=svc.state(owner);assert state['result']==old and state['analysis']==draft
    assert fake_references[0]==[]


def test_unknown_preparation_is_queried_not_resubmitted(owner,fake_references):
    old=old_result(owner);draft=analyze(owner)['analysis'];fake_references[1]['shirt']='unknown'
    result=prepare(owner,draft);rid=result['preparation']['id'];calls=list(fake_references[0])
    assert result['preparation']['status']=='unknown' and result['result']==old
    svc.query(owner,rid);svc.state(owner)
    assert fake_references[0]==calls
    with pytest.raises(purchase.PurchaseError,match='上一批'):
        prepare(owner,draft)


def test_missing_submitted_job_after_restart_is_not_automatically_started(owner,fake_references,monkeypatch):
    draft=analyze(owner)['analysis'];rid=str(uuid4())
    monkeypatch.setattr(svc.threading,'Thread',lambda **kw:type('NoRun',(),{'start':lambda self:None})())
    svc.prepare(owner,rid,draft['id'],[draft['directions'][0]['id']]);svc._active.discard((owner,rid))
    def missing(*args):raise purchase.PurchaseError('不存在',404)
    monkeypatch.setattr(refs,'status',missing)
    assert svc.state(owner)['preparation']['status']=='interrupted'
    assert svc.query(owner,rid)['preparation']['status']=='interrupted' and not fake_references[0]


@pytest.mark.parametrize('selection',[[],['unknown'],['same','same']])
def test_invalid_selection_never_creates_search(owner,fake_references,selection):
    draft=analyze(owner)['analysis']
    with pytest.raises(purchase.PurchaseError):
        svc.prepare(owner,str(uuid4()),draft['id'],selection,background=False)
    assert not fake_references[0]


def test_saved_previous_plan_remains_operable_after_new_result(owner,fake_references):
    draft=analyze(owner)['analysis'];first=prepare(owner,draft,[draft['directions'][0]['id']])['result']
    prepare(owner,draft,[draft['directions'][1]['id']])
    result=purchase.choice(owner,first['id'],first['directions'][0]['id'],'save')
    assert result['saved'][0]['id']==first['id']
    assert refs._direction(owner,first['id'],first['directions'][0]['id'])['type']=='casual_pants'


def test_repreparing_same_source_direction_keeps_distinct_photos_and_saved_entries(owner,fake_references):
    draft=analyze(owner)['analysis'];source=draft['directions'][0]['id']
    first=prepare(owner,draft,[source])['result']
    purchase.choice(owner,first['id'],first['directions'][0]['id'],'save')
    second=prepare(owner,draft,[source],preference='裤子宽松一些',client=Model())['result']
    result=purchase.choice(owner,second['id'],second['directions'][0]['id'],'save')
    assert first['directions'][0]['id']!=second['directions'][0]['id']
    assert first['directions'][0]['source_direction_id']==second['directions'][0]['source_direction_id']==source
    assert {p['id'] for p in result['saved']}=={first['id'],second['id']}
    assert len(svc.state(owner)['references'])==2


def test_completed_publish_is_immutable_on_repeated_queries(owner,fake_references):
    draft=analyze(owner)['analysis'];result=prepare(owner,draft,[draft['directions'][0]['id']])
    rid=result['preparation']['id'];before=deepcopy(result['result'])
    svc._check_and_publish(owner,rid);svc.query(owner,rid)
    assert svc.state(owner)['result']==before and len(fake_references[0])==1


def test_bounded_search_uses_separate_rounds_and_stops_on_unknown(tmp_path,monkeypatch):
    from scripts.bridge import reference_bridge as bridge
    calls=[]
    def find(executable,entry,directory,spec,environment,chat,**kw):
        calls.append((directory.name,kw['query']))
        if len(calls)==1:return []
        (directory/'candidate-1.png').write_bytes(b'synthetic checked image')
        return [{'file':'candidate-1.png','id':'c1','sha256':'a'*64}]
    monkeypatch.setattr(bridge,'find_references',find)
    spec={'type':'shirt','primary_color':'white','title':'白色衬衫','color':'白色','fit':'常规','pattern':'纯色','texture':'低光泽'}
    result=bridge.find_reference_rounds(None,None,tmp_path,spec,None,None)
    assert len(calls)==2 and calls[0][0]=='round-1' and calls[1][0]=='round-2' and calls[0][1]!=calls[1][1]
    assert (tmp_path/result[0]['file']).read_bytes()==b'synthetic checked image'
    other=tmp_path/'unknown';other.mkdir();calls.clear()
    def uncertain(*a,**kw):calls.append(True);raise ValueError('search result unknown')
    monkeypatch.setattr(bridge,'find_references',uncertain)
    with pytest.raises(ValueError):bridge.find_reference_rounds(None,None,other,spec,None,None)
    assert len(calls)==1
