"""§25：添置的收费边界、真实引用和持久结果，全部使用假模型。"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.models import ItemCreate, ItemUpdate
from app.services import items, purchase
from app.services.llm.client import LLMError


@pytest.fixture
def owner():
    uid=db.new_id('u')
    with db.conn() as c:
        c.execute('INSERT INTO users(id,name,created_at) VALUES(?,?,?)',(uid,'添置合成测试',db.now_iso()))
    for key,kind,color in [('a','tshirt','navy'),('b','jeans','black'),('c','sneakers','black')]:
        items.create(uid,ItemCreate(type=kind,primary_color=color,name='合成'+key),item_id=uid+key)
    return uid


class Fake:
    model='fake'
    def __init__(self,edit=None):self.calls=[];self.edit=edit
    def chat_json(self,messages,**kwargs):
        self.calls.append((messages,kwargs))
        rows=json.loads(messages[-1]['content'])['wardrobe']
        owned=[r['id'] for r in rows if r['type'] in ('tshirt','sneakers')]
        d={'title':'米白色直筒长裤','type':'casual_pants','primary_color':'beige','color':'柔和的米白色',
           'fit':'适中裤宽，裤长至鞋面','texture':'选购平整表面的款式','pattern':'低装饰的纯色',
           'layering':'用深浅变化连接上下装','reason':'尝试和已有藏蓝上衣形成明暗变化',
           'difference':'','caution':'试穿确认裤脚长度','owned':owned,'pairing':'藏蓝上衣和黑色鞋配浅裤'}
        plan={'decision':'recommend','headline':'先补一条浅色长裤','summary':'从深浅配色出发，扩展已有衣服的组合。','directions':[d]}
        return self.edit(plan,rows) if self.edit else plan


def generate(uid,client=None,aesthetic='auto',token=None):
    return purchase.generate(uid,token or str(uuid4()),aesthetic,client=client or Fake())


def test_read_does_not_call_model_and_inventory_stays(owner):
    before=purchase.inventory(owner)
    assert purchase.get_state(owner)['result'] is None
    result=generate(owner)
    assert result['result']['directions'][0]['complete']
    assert purchase.inventory(owner)==before
    assert not result['stale']


def test_idempotency_and_parameter_mismatch(owner):
    fake=Fake();token=str(uuid4())
    a=generate(owner,fake,token=token);b=generate(owner,fake,token=token)
    assert a['result']['id']==b['result']['id'] and len(fake.calls)==1
    with pytest.raises(purchase.PurchaseError,match='更换审美'):generate(owner,fake,'clean',token)


def test_failed_request_is_not_recharged_and_old_result_survives(owner):
    old=generate(owner)['result']
    class Down(Fake):
        def chat_json(self,*args,**kw):self.calls.append(1);raise LLMError('sensitive upstream data')
    fake=Down();token=str(uuid4())
    with pytest.raises(purchase.PurchaseError,match='暂时无法'):generate(owner,fake,token=token)
    result=generate(owner,fake,token=token)
    assert len(fake.calls)==1 and result['result']==old and result['request']['status']=='failed'
    assert 'sensitive' not in result['request']['error']


def test_empty_wardrobe_has_no_paid_call():
    fake=Fake()
    with pytest.raises(purchase.PurchaseError,match='先录入'):generate('nobody',fake)
    assert not fake.calls


def test_interrupted_request_only_queries(owner):
    token=str(uuid4());purchase._write(owner,'pending',token)
    purchase._write(owner,'request:'+token,{'status':'running','aesthetic':'auto','created_at':db.now_iso()})
    fake=Fake();result=generate(owner,fake,token=token)
    assert result['request']['status']=='interrupted' and not fake.calls
    assert generate(owner,fake)['request']['status']=='completed' and len(fake.calls)==1


def test_running_request_prevents_parallel_charge(owner):
    entered=threading.Event();release=threading.Event();token=str(uuid4())
    class Slow(Fake):
        def chat_json(self,*args,**kw):entered.set();assert release.wait(5);return super().chat_json(*args,**kw)
    fake=Slow()
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(generate,owner,fake,'auto',token)
        assert entered.wait(3)
        try:
            assert generate(owner,fake,token=token)['request']['status']=='running'
            with pytest.raises(purchase.PurchaseError,match='正在分析'):generate(owner,Fake())
        finally:release.set()
        assert future.result()['request']['status']=='completed'
    assert len(fake.calls)==1


@pytest.mark.parametrize('change',[
    lambda p,r:p['directions'][0].update(owned=['nonexistent']),
    lambda p,r:p['directions'][0].update(owned=[r[0]['id'],'w999']),
    lambda p,r:p['directions'][0].update(type='invented'),
    lambda p,r:p['directions'][0].update(primary_color='invented'),
    lambda p,r:p.update(decision='enough'),
    lambda p,r:p.update(directions=[]),
    lambda p,r:p['directions'].append(deepcopy(p['directions'][0])),
    lambda p,r:p['directions'][0].update(owned=[x['id'] for x in r]),
    lambda p,r:p['directions'][0].update(reason=''),
])
def test_invalid_model_results_not_published(owner,change):
    def edit(plan,rows):change(plan,rows);return plan
    with pytest.raises(purchase.PurchaseError):generate(owner,Fake(edit))
    assert purchase.get_state(owner)['result'] is None


def test_similar_owned_clothes_require_new_value(owner):
    items.create(owner,ItemCreate(type='casual_pants',primary_color='beige'))
    with pytest.raises(purchase.PurchaseError,match='已有相似'):generate(owner)
    def different(p,r):p['directions'][0]['difference']='选择较现有裤子更直的线条，试穿确认确有不同';return p
    assert generate(owner,Fake(different))['result']['directions']


def test_no_buy_and_aesthetic_five_dimensions(owner):
    def enough(p,r):p.update(decision='enough',directions=[],headline='这次可以先不买');return p
    fake=Fake(enough);result=generate(owner,fake,'tonal')
    assert result['result']['decision']=='enough' and result['result']['aesthetic']['name']=='同色层次'
    context=json.loads(fake.calls[0][0][-1]['content'])
    assert len(context['aesthetic']['dimensions'])==5
    assert all('scene' not in r and 'wear_count' not in r for r in context['wardrobe'])


def test_saved_choices_survive_new_results_and_multiple_same_plan(owner):
    def two(p,r):
        d=deepcopy(p['directions'][0]);d.update(title='白色短裤',type='shorts',primary_color='white');p['directions'].append(d);return p
    result=generate(owner,Fake(two));plan=result['result'];a,b=plan['directions']
    for d in (a,b):purchase.choice(owner,plan['id'],d['id'],'save')
    assert len(purchase.get_state(owner)['saved'])==2
    generate(owner)
    purchase.choice(owner,plan['id'],a['id'],'unsave')
    state=purchase.get_state(owner)
    assert len(state['saved'])==1 and state['saved'][0]['directions'][0]['id']==b['id']
    with pytest.raises(purchase.PurchaseError):purchase.choice('someone-else',plan['id'],b['id'],'save')


def test_dismiss_in_next_prompt_and_stale_inventory(owner):
    state=generate(owner);plan=state['result'];d=plan['directions'][0]
    purchase.choice(owner,plan['id'],d['id'],'dismiss')
    fake=Fake();generate(owner,fake)
    assert json.loads(fake.calls[0][0][-1]['content'])['dismissed'][0]['title']==d['title']
    row=purchase.inventory(owner)[0];items.update(row['id'],ItemUpdate(primary_color='red'),owner)
    assert purchase.get_state(owner)['stale']


def test_routes_validate_uuid_and_return_real_model_errors(owner,monkeypatch):
    monkeypatch.setattr('app.routes.wardrobe._uid',lambda:owner)
    with TestClient(app) as client:
        assert client.post('/wardrobe/purchase',json={'request_id':'bad'}).status_code==422
        assert client.get('/wardrobe/purchase').json()['result'] is None
        assert client.post('/wardrobe/purchase',json={'request_id':str(uuid4()),'aesthetic':'bad'}).status_code==422
        monkeypatch.setattr(purchase,'get_client',lambda:Fake())
        response=client.post('/wardrobe/purchase',json={'request_id':str(uuid4())})
        assert response.status_code==200 and response.json()['result']['directions']


def test_balanced_sampling_preserves_small_roles():
    pool=[{'id':str(i),'slot':'top'} for i in range(500)]+[{'id':'shoe','slot':'shoes'},{'id':'pants','slot':'bottom'}]
    sample=purchase._sample(pool)
    assert len(sample)==200 and {'shoe','pants'}.issubset({r['id'] for r in sample})


def test_unsampled_similar_item_still_requires_difference(owner):
    fake=Fake()
    pool=purchase.inventory(owner)
    messages,aliases=purchase._messages(pool,{'name':'合成'},[])
    raw=fake.chat_json(messages)
    extra={'id':'not-in-context','type':'casual_pants','primary_color':'beige'}
    with pytest.raises(purchase.PurchaseError,match='已有相似'):
        purchase._validate(raw,aliases,[*pool,extra])
    context=json.loads(purchase._messages(pool,{'name':'合成'},[],[*pool,extra])[0][-1]['content'])
    assert ['casual_pants','beige'] in context['all_owned_type_colors']


def test_name_changes_mark_result_stale(owner):
    generate(owner)
    row=purchase.inventory(owner)[0]
    items.update(row['id'],ItemUpdate(name='只修改名称'),owner)
    assert purchase.get_state(owner)['stale']


def test_user_text_replaces_known_alias_and_rejects_unknown(owner):
    def known(p,r):p['directions'][0]['pairing']='搭配'+r[0]['id']+'更加清爽';return p
    result=generate(owner,Fake(known))
    text=result['result']['directions'][0]['pairing']
    assert 'w1' not in text and '合成' in text
    def unknown(p,r):p['directions'][0]['reason']='搭配w999会更清爽';return p
    with pytest.raises(purchase.PurchaseError,match='未知衣物'):generate(owner,Fake(unknown))


def test_blank_fields_are_invalid(owner):
    def edit(p,r):p['directions'][0]['texture']='    ';return p
    with pytest.raises(purchase.PurchaseError):generate(owner,Fake(edit))


def test_top_level_aliases_are_sanitized_even_for_no_buy(owner):
    def known(p,r):p.update(headline='围绕'+r[0]['id']+'尝试新的搭配');return p
    assert 'w1' not in generate(owner,Fake(known))['result']['headline']
    def unknown(p,r):p.update(decision='enough',directions=[],summary='围绕w999暂时不需要添置');return p
    with pytest.raises(purchase.PurchaseError,match='未知衣物'):generate(owner,Fake(unknown))


def test_unsave_keeps_siblings_in_older_grouped_saved_plan(owner):
    state=generate(owner);plan=state['result'];a=plan['directions'][0]
    b={**a,'id':'pd_sibling','title':'另一条合成方向'}
    purchase._write(owner,'choices',{'saved':[{**plan,'directions':[a,b]}],'dismissed':[]})
    state=purchase.choice(owner,plan['id'],a['id'],'unsave')
    assert state['saved'][0]['directions']==[b]


def test_owned_schema_matches_only_this_requests_inventory(owner):
    fake=Fake();generate(owner,fake)
    messages,options=fake.calls[0]
    context=json.loads(messages[-1]['content'])
    schema=options['schema']['$defs']['Direction']['properties']
    assert schema['owned']['items']['enum']==context['allowed_owned']==[row['id'] for row in context['wardrobe']]
    assert 'other' not in schema['type']['enum']
    assert 'beige' in schema['primary_color']['enum']
    assert purchase._response_schema({'w1':{}})['$defs']['Direction']['properties']['owned']['items']['enum']==['w1']
    assert len(schema['owned']['items']['enum'])==3


def test_same_owned_item_can_be_repeated_without_becoming_a_new_garment(owner):
    def repeated(p,r):
        top=next(row['id'] for row in r if row['type']=='tshirt')
        shoe=next(row['id'] for row in r if row['type']=='sneakers')
        p['directions'][0]['owned']=[top,' '+top.upper()+' ',shoe,shoe]
        return p
    state=generate(owner,Fake(repeated));d=state['result']['directions'][0]
    real=purchase.inventory(owner)
    assert d['owned']==[next(row['id'] for row in real if row['type']=='tshirt'),next(row['id'] for row in real if row['type']=='sneakers')]
    assert d['complete'] and len(set(d['owned']))==2


@pytest.mark.parametrize('invalid',['w999','w1x','w01','藏蓝色 T 恤','it_actual_but_not_provided'])
def test_unknown_reference_is_not_guessed_and_diagnostics_are_safe(owner,invalid):
    token=str(uuid4())
    def bad(p,r):p['directions'][0]['owned']=[invalid];return p
    with pytest.raises(purchase.PurchaseError,match='编号与本次衣橱不符'):
        generate(owner,Fake(bad),token=token)
    state=purchase.get_state(owner,token)
    assert state['request']['code']=='invalid_owned_references'
    assert state['request']['diagnostics']=={'direction':1,'reference_count':1,'unknown_count':1,'available_count':3}
    assert invalid not in json.dumps(state['request'],ensure_ascii=False)
    assert state['result'] is None


@pytest.mark.parametrize('owned',[[],['w1']*5,'w1',[1]])
def test_reference_normalization_does_not_accept_invalid_shapes(owner,owned):
    def bad(p,r):p['directions'][0]['owned']=owned;return p
    with pytest.raises(purchase.PurchaseError,match='建议不完整'):generate(owner,Fake(bad))
    assert purchase.get_state(owner)['result'] is None


@pytest.mark.parametrize('length,valid',[(42,True),(43,False),(80,False)])
def test_alias_expansion_keeps_final_text_length_contract(owner,length,valid):
    row=purchase.inventory(owner)[0]
    items.update(row['id'],ItemUpdate(name='衣'*length),owner)
    def named(p,r):p['headline']=r[0]['id'];return p
    if valid:
        assert len(generate(owner,Fake(named))['result']['headline'])==42
    else:
        token=str(uuid4())
        with pytest.raises(purchase.PurchaseError,match='不符合展示要求'):generate(owner,Fake(named),token=token)
        failed=purchase.get_state(owner,token)['request']
        assert failed['code']=='invalid_plan_text' and failed['diagnostics']['validation_errors']>=1
        assert '衣'*length not in json.dumps(failed,ensure_ascii=False)


def test_desktop_wire_carries_this_request_reference_schema(owner,monkeypatch):
    from app.services.llm.desktop import DesktopClient,MODEL
    seen=[]
    def bridge_request(settings,path,*,payload):
        seen.append(payload)
        context=json.loads(payload['messages'][-1]['content'])
        enum=payload['schema']['$defs']['Direction']['properties']['owned']['items']['enum']
        assert path=='/chat' and payload['model']==MODEL and payload['json_output']
        assert enum==context['allowed_owned']
        return {'model':MODEL,'text':json.dumps(Fake().chat_json(payload['messages']))}
    monkeypatch.setattr('app.services.llm.desktop.bridge_request',bridge_request)
    state=generate(owner,DesktopClient({'url':'http://127.0.0.1:9999','token':'synthetic-only'}))
    assert len(seen)==1 and state['result']['directions'][0]['complete']
