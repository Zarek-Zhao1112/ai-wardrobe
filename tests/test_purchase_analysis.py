"""§37：候选先配图、选择后零搜索、未知只查；全部使用合成衣橱。"""
import hashlib
from uuid import uuid4
import pytest
from app import db
from app.services import purchase, purchase_intent as intent, purchase_analysis as svc, purchase_reference as refs
from test_purchase_intent import Model, owner, old_result


@pytest.fixture
def photos(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'IMAGES_DIR', tmp_path)
    calls, modes = [], {}
    def start(uid, rid, did):
        record = svc.record(uid, rid)
        direction = next(d for d in record['directions'] if d['id'] == did)
        calls.append(direction['type'])
        data = b'\x89PNG\r\n\x1a\n' + direction['type'].encode()
        digest = hashlib.sha256(data).hexdigest()
        name = 'reference-' + digest + '.png'
        (tmp_path/name).write_bytes(data)
        origin = record.get('image_origin', 'product_photo')
        signature = refs._signature({k:direction[k] for k in refs._FIELDS}, origin)
        row = {'job_id': record['jobs'][did], 'signature':signature, 'direction_id': did, 'plan_id': record['plan_id'], 'issued_at': 1,
            'image_origin': origin, 'status': modes.get(direction['type'], 'complete'), 'references': [{'id': digest[:16], 'image_path': name, 'name': '合成参考', **({'origin':'generated','image_model':refs.STYLE_IMAGE_MODEL} if origin == 'generated' else {'source_url':'https://shop.example.com/product'})}]}
        refs._bind(uid, row)
        return row
    monkeypatch.setattr(refs, 'start_analysis', start)
    monkeypatch.setattr(refs, 'status', lambda uid, jid: refs._read(uid, 'job:' + jid))
    monkeypatch.setattr(intent, 'POLL_SECONDS', 0)
    monkeypatch.setattr(intent, 'MAX_WAIT_SECONDS', .015)
    return calls, modes


def analyze(uid, model=None, **kwargs):
    return svc.start(uid, str(uuid4()), 'natural', client=model or Model(), background=False, **kwargs)


def choose(uid, draft, **kwargs):
    return intent.prepare(uid, str(uuid4()), draft['id'], [draft['directions'][0]['id']], background=False, **kwargs)


def test_ready_analysis_searches_before_choices_and_keeps_formal_result(owner, photos):
    old = old_result(owner)
    state = analyze(owner)
    draft = state['analysis']
    assert state['analysis_request']['status'] == 'completed'
    assert draft['workflow_version'] == 2 and len(draft['directions']) == 3
    assert photos[0] == ['casual_pants', 'shirt', 'cardigan'] and state['result'] == old
    assert all(state['references'][d['id']]['status'] == 'complete' for d in draft['directions'])
    assert intent.analysis(owner, draft['id'])['references']


def test_partial_ready_excludes_failed_priority_from_headline(owner, photos):
    photos[1]['casual_pants'] = 'empty'
    state = analyze(owner)
    assert len(state['analysis']['directions']) == 2
    assert '米白休闲长裤' not in state['analysis']['headline'] + state['analysis']['summary']
    assert state['analysis']['decision'] == 'recommend'


def test_all_empty_preserves_previous_draft_and_never_means_enough(owner, photos):
    previous = analyze(owner)['analysis']
    photos[1].update(casual_pants='empty', shirt='empty', cardigan='empty')
    state = analyze(owner)
    assert state['analysis_request']['status'] == 'failed' and state['analysis'] == previous
    assert state['result'] is None


def test_explicit_no_buy_is_allowed_without_search(owner, photos):
    model = Model(lambda plan, context: {**plan, 'decision': 'enough', 'directions': [], 'headline': '先搭已有衣物', 'summary': '当前衣橱可以先形成搭配。'})
    state = analyze(owner, model)
    assert state['analysis']['decision'] == 'enough' and photos[0] == []


def test_choose_only_copies_ready_references_and_uses_new_direction_id(owner, photos, monkeypatch):
    draft = analyze(owner)['analysis']
    monkeypatch.setattr(refs, '_start', lambda *a, **kw: pytest.fail('选择后不得再搜图'))
    first = choose(owner, draft)
    second = choose(owner, draft)
    assert first['preparation']['status'] == second['preparation']['status'] == 'completed'
    a, b = first['result']['directions'][0], second['result']['directions'][0]
    assert a['id'] != b['id'] != draft['directions'][0]['id']
    assert first['references'][a['id']]['references'] == second['references'][b['id']]['references']
    assert len(photos[0]) == 3


def test_lost_cache_does_not_start_search_or_replace_result(owner, photos, monkeypatch):
    draft = analyze(owner)['analysis']
    old = old_result(owner)
    row = refs.snapshots(owner, [draft])[draft['directions'][0]['id']]['references'][0]
    (db.IMAGES_DIR/row['image_path']).unlink()
    monkeypatch.setattr(refs, '_start', lambda *a, **kw: pytest.fail('缓存失效不得隐式收费'))
    state = choose(owner, draft)
    assert state['preparation']['status'] == 'failed' and state['result'] == old


def test_preference_reanalyzes_only_selected_types_then_searches_updated_spec(owner, photos):
    draft = analyze(owner)['analysis']
    photos[0].clear()
    state = analyze(owner, source_draft_id=draft['id'], direction_ids=[draft['directions'][1]['id']], preference='想宽松一点')
    assert photos[0] == ['shirt'] and state['analysis']['directions'][0]['fit'] == '按偏好选择宽松余量'
    assert state['analysis']['id'] != draft['id']
    with pytest.raises(purchase.PurchaseError, match='重新分析'):
        choose(owner, draft, preference='换版型')


def test_request_deduplication_includes_preference_and_never_reposts(owner, photos):
    model = Model(); rid = str(uuid4())
    first = svc.start(owner, rid, 'natural', client=model, background=False)
    assert svc.start(owner, rid, 'natural', client=model, background=False)['analysis'] == first['analysis']
    assert model.calls == 1 and len(photos[0]) == 3
    with pytest.raises(purchase.PurchaseError, match='同一次请求'):
        svc.start(owner, rid, 'urban', client=model, background=False)


def test_wait_limit_keeps_ready_subset_and_unknown_jobs_block_new_analysis(owner, photos):
    photos[1]['shirt'] = 'unknown'
    state = analyze(owner)
    assert state['analysis_request']['status'] == 'completed'
    assert {d['type'] for d in state['analysis']['directions']} == {'casual_pants', 'cardigan'}
    assert state['analysis_request']['pending_reference_count'] == 1
    with pytest.raises(purchase.PurchaseError, match='先查询'):
        analyze(owner)


def test_old_request_response_keeps_its_own_reference_projection(owner, photos):
    rid = str(uuid4()); model = Model()
    old = svc.start(owner, rid, 'natural', client=model, background=False)['analysis']
    analyze(owner)
    result = svc.start(owner, rid, 'natural', client=model, background=False)
    assert result['analysis']['id'] == old['id'] and model.calls == 1
    assert all(d['id'] in result['references'] for d in old['directions'])


def test_unknown_only_queries_original_jobs(owner, photos):
    photos[1].update(casual_pants='unknown', shirt='unknown', cardigan='unknown')
    model = Model(); state = analyze(owner, model)
    rid = state['analysis_request']['id']; before = list(photos[0])
    assert state['analysis_request']['status'] == 'unknown'
    svc.query(owner, rid)
    assert model.calls == 1 and photos[0] == before


def test_restarted_model_phase_never_resumes_model(owner, monkeypatch):
    rid = str(uuid4())
    intent._write(owner, 'request:' + rid, {'id':rid,'kind':'analyze','workflow_version':2,'status':'running','phase':'analyzing','plan_id':rid,'directions':[],'jobs':{}})
    intent._write(owner, 'analysis-latest', rid)
    monkeypatch.setattr(intent, 'get_client', lambda: pytest.fail('恢复不得重新调用模型'))
    assert svc.query(owner, rid)['analysis_request']['status'] == 'interrupted'


def test_user_cannot_choose_foreign_analysis(owner, photos):
    draft = analyze(owner)['analysis']
    with pytest.raises(purchase.PurchaseError, match='没有找到'):
        svc.query('other-user', draft['id'])


def test_incomplete_restart_manifest_becomes_interrupted_without_reposting(owner, photos):
    draft = analyze(owner)['analysis']; rid = str(uuid4())
    intent._write(owner, 'request:' + rid, {'id':rid,'kind':'analyze','workflow_version':2,'status':'running',
        'phase':'searching','plan_id':rid,'decision':'recommend','directions':draft['directions'],'jobs':{}})
    intent._write(owner, 'analysis-latest', rid)
    calls = list(photos[0])
    assert svc.query(owner, rid)['analysis_request']['status'] == 'interrupted'
    assert photos[0] == calls
    assert analyze(owner)['analysis_request']['status'] == 'completed'


def test_search_color_does_not_take_owned_garment_color_from_description():
    from scripts.bridge.reference_bridge import query_of
    spec = {'type':'shirt','primary_color':'white','title':'纯白衬衫','color':'白色，与藏蓝色长裤呼应','fit':'肩身合适','pattern':'纯色','texture':'低光泽'}
    query = query_of(spec)
    assert query.startswith('白色 ') and '藏蓝' not in query
    assert '肩身合适' not in query and '平铺' not in query


def test_completed_subset_exposes_and_can_drain_original_pending_jobs(owner, photos):
    photos[1]['shirt'] = 'unknown'
    state = analyze(owner); rid = state['analysis_request']['id']; draft = state['analysis']
    request = svc.record(owner, rid)
    pending = next(d for d in request['directions'] if d['type'] == 'shirt')
    job = refs._read(owner, 'job:' + request['jobs'][pending['id']])
    job['status'] = 'complete'; refs._write(owner, 'job:' + job['job_id'], job)
    queried = svc.query(owner, rid)
    assert queried['analysis_request']['pending_reference_count'] == 0
    assert queried['analysis'] == draft
    assert analyze(owner)['analysis_request']['status'] == 'completed'
