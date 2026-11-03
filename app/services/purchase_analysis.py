"""§37：异步分析与前置实物检索；已配图的方向才进入用户可选草稿。"""
from __future__ import annotations

from copy import deepcopy
import threading
import time
from uuid import uuid5, NAMESPACE_URL

from .. import db
from . import aesthetics, purchase, purchase_intent as intent, purchase_reference as refs
from .llm.client import LLMError


def record(uid, rid):
    value = intent._read(uid, 'request:' + intent._identifier(rid))
    if not value or value.get('kind') != 'analyze' or value.get('workflow_version') != 2:
        raise purchase.PurchaseError('没有找到这次分析记录。', 404)
    return value


def _snapshots(uid, value):
    return refs.snapshots(uid, [{'id': value['plan_id'], 'directions': value.get('directions', [])}])


def start(uid, request_id, aesthetic, *, preference='', source_draft_id=None, direction_ids=None, client=None, background=True):
    rid = intent._identifier(request_id)
    try:
        profile = aesthetics.get_profile(aesthetic)
    except ValueError as error:
        raise purchase.PurchaseError('请选择一个审美方向。', 422) from error
    if not isinstance(preference, str) or len(preference) > 160 or any(ord(c) < 32 and c not in '\n\t' for c in preference):
        raise purchase.PurchaseError('补充偏好格式不正确。', 422)
    preference = preference.strip()
    source_draft_id = intent._identifier(source_draft_id) if source_draft_id else None
    direction_ids = direction_ids or []
    if not isinstance(direction_ids, list) or len(direction_ids) > 3 or any(not isinstance(v, str) for v in direction_ids) or len(set(direction_ids)) != len(direction_ids):
        raise purchase.PurchaseError('所选方向不正确。', 422)
    signature = intent._signature({'kind': 'analyze', 'version': 2, 'aesthetic': aesthetic,
        'preference': preference, 'source': source_draft_id, 'directions': sorted(direction_ids), 'image_origin': 'generated'})
    with intent._lock:
        if intent._existing(uid, rid, signature):
            return intent._response(uid, rid)
        previous_id = intent._read(uid, 'analysis-latest')
        previous = intent._read(uid, 'request:' + previous_id) if previous_id else None
        if previous and ((uid, previous_id) in intent._active or previous.get('workflow_version') == 2 and
                (previous['status'] in intent.PENDING or any(r.get('status') in refs.PENDING for r in _snapshots(uid, previous).values()))):
            raise purchase.PurchaseError('上次分析尚未确认，请先查询原任务。', 409)
        pool = purchase.inventory(uid)
        if not pool:
            raise purchase.PurchaseError('先录入几件衣物，让分析更有依据。', 409)
        fingerprint = purchase._fingerprint(pool)
        source = intent._read(uid, 'draft:' + source_draft_id) if source_draft_id else None
        if source_draft_id and (not source or source.get('workflow_version') != 2 or source['fingerprint'] != fingerprint or source['aesthetic']['id'] != aesthetic):
            raise purchase.PurchaseError('原分析与衣橱或审美已不一致，请重新分析。', 409)
        if source and (not preference or not direction_ids or not set(direction_ids) <= {d['id'] for d in source['directions']}):
            raise purchase.PurchaseError('请确认要调整的衣物方向和偏好。', 422)
        if not source and (preference or direction_ids):
            raise purchase.PurchaseError('请先分析并选择要调整的方向。', 422)
        value = {'id': rid, 'kind': 'analyze', 'workflow_version': 2, 'signature': signature, 'status': 'running',
            'image_origin': 'generated',
            'phase': 'refining' if source else 'analyzing', 'created_at': db.now_iso(), 'plan_id': rid,
            'fingerprint': fingerprint, 'aesthetic': {'id': profile['id'], 'name': profile['name']},
            'preference': preference, 'source_draft_id': source_draft_id, 'directions': [], 'jobs': {}, 'completed': 0, 'total': 0}
        if source:
            value.update(analyzed=source['analyzed'], total_inventory=source['total'], decision='recommend')
            value['directions'] = [{**deepcopy(d), 'id': db.new_id('pd'), 'source_direction_id': d['id']}
                for d in source['directions'] if d['id'] in direction_ids]
        with db.conn(immediate=True) as connection:
            intent._write(uid, 'request:' + rid, value, connection)
            intent._write(uid, 'analysis-latest', rid, connection)
        intent._active.add((uid, rid))
    if background:
        threading.Thread(target=_run, args=(uid, rid, pool, profile, client), daemon=True).start()
    else:
        _run(uid, rid, pool, profile, client)
    return intent._response(uid, rid)


def _run(uid, rid, pool, profile, client):
    try:
        value = record(uid, rid)
        if value['preference']:
            intent._refine(uid, value, client, persist=False)
        else:
            choices = purchase._read(uid, 'choices', {'dismissed': []})
            chosen = purchase._sample(pool)
            messages, aliases = purchase._messages(chosen, profile, choices['dismissed'], pool)
            messages[0]['content'] += '\n给出最多三个不同品类的添置方向；后续将生成AI单品款式示意再供用户选择，不搜索实际商品、不虚构品牌价格和购买链接。标题采用规范品类、常见颜色和可见版型，避免生造诗意名称。每个type只出现一次，允许判断暂时不用添置。'
            plan = purchase._validate((client or intent.get_client()).chat_json(messages,
                schema=purchase._response_schema(aliases), timeout=90, temperature=.2), aliases, pool)
            if len({d['type'] for d in plan['directions']}) != len(plan['directions']):
                raise purchase.PurchaseError('AI 给出的衣物方向重复，本次分析未采用。', 502)
            value.update(directions=plan['directions'], decision=plan['decision'], headline=plan['headline'], summary=plan['summary'], analyzed=len(chosen), total_inventory=len(pool))
        for direction in value['directions']:
            direction['image_origin'] = 'generated'
        if value['fingerprint'] != purchase._fingerprint(purchase.inventory(uid)):
            raise purchase.PurchaseError('衣橱信息已更新，请重新分析。', 409)
        value.update(phase='searching', total=len(value['directions']))
        value['jobs'] = {d['id']: str(uuid5(NAMESPACE_URL, 'purchase-analysis:' + rid + ':' + d['id'])) for d in value['directions']}
        intent._write(uid, 'request:' + rid, value)
        for direction in value['directions']:
            refs.start_analysis(uid, rid, direction['id'])
        deadline = time.monotonic() + intent.MAX_WAIT_SECONDS
        while time.monotonic() < deadline:
            for job in value['jobs'].values():
                refs.status(uid, job)
            if _finish(uid, rid):
                return
            time.sleep(intent.POLL_SECONDS)
        if not _finish(uid, rid, allow_partial=True):
            intent._fail(uid, rid, '尚未确认这次分析的最终结果，请查询原任务。', status='unknown')
    except (purchase.PurchaseError, LLMError) as error:
        intent._fail(uid, rid, str(error))
    except Exception:
        intent._fail(uid, rid, '这次分析暂未能完成，已有建议仍然保留。')
    finally:
        with intent._lock:
            intent._active.discard((uid, rid))


def _finish(uid, rid, *, allow_partial=False):
    with intent._lock:
        value = record(uid, rid)
        if value['status'] not in intent.PENDING:
            return True
        snapshots = _snapshots(uid, value)
        ready = [d for d in value['directions'] if (snapshots.get(d['id']) or {}).get('status') == 'complete' and refs._cache_usable(snapshots[d['id']].get('references'))]
        value['completed'] = len(ready)
        pending = any(d['id'] not in snapshots or snapshots[d['id']]['status'] in refs.PENDING for d in value['directions'])
        if pending and (not allow_partial or not ready):
            intent._write(uid, 'request:' + rid, value)
            return False
        if value['decision'] != 'enough' and not ready:
            intent._fail(uid, rid, '这次款式示意未能确认生成成功，已有建议仍保留，请先查看生成进度与用量。')
            return True
        draft = {'id': rid, 'request_id': rid, 'workflow_version': 2, 'created_at': db.now_iso(),
            'decision': value['decision'], 'directions': ready, 'aesthetic': value['aesthetic'],
            'fingerprint': value['fingerprint'], 'analyzed': value['analyzed'], 'total': value['total_inventory'], 'image_origin': value.get('image_origin', 'product_photo')}
        if draft['decision'] == 'enough':
            draft.update(headline=value['headline'], summary=value['summary'])
        else:
            titles = [d['title'] for d in ready]
            draft.update(headline='可以先看看' + titles[0], summary='结合已有衣橱，先从' + '、'.join(titles) + '这些方向考虑。')
        with db.conn(immediate=True) as connection:
            if value['fingerprint'] != purchase._fingerprint(purchase.inventory(uid)) or intent._read(uid, 'analysis-latest') != rid:
                intent._write(uid, 'request:' + rid, {**value, 'status': 'failed', 'phase': 'finished', 'error': '衣橱或分析已更新，已有建议仍然保留。'}, connection)
                return True
            intent._write(uid, 'draft:' + rid, draft, connection)
            intent._write(uid, 'draft-latest', rid, connection)
            intent._write(uid, 'request:' + rid, {**value, 'status': 'completed', 'phase': 'finished', 'draft_id': rid, 'error': ''}, connection)
        return True


def query(uid, request_id):
    rid = intent._identifier(request_id)
    value = record(uid, rid)
    if (uid, rid) in intent._active:
        return intent._response(uid, rid)
    if value['status'] not in intent.PENDING and not any(row.get('status') in refs.PENDING for row in _snapshots(uid, value).values()):
        return intent._response(uid, rid)
    if value['phase'] in ('analyzing', 'refining'):
        intent._fail(uid, rid, '上次分析已中断，请明确重新开始。', status='interrupted')
        return intent._response(uid, rid)
    missing = set(value.get('jobs', {})) != {d['id'] for d in value.get('directions', [])} or 'decision' not in value
    for job in value['jobs'].values():
        try:
            refs.status(uid, job)
        except purchase.PurchaseError as error:
            if error.status != 404:
                raise
            missing = True
    if missing:
        intent._fail(uid, rid, '上次分析未完整提交，未自动重发，请重新分析。', status='interrupted')
    else:
        _finish(uid, rid)
    return intent._response(uid, rid)
