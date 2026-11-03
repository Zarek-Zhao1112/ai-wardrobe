"""§32：添置图文配齐，显式补齐入口仅登记从未开始的方向，失败须手动重试。"""
from __future__ import annotations
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from .. import db
from . import images, purchase
from .llm.desktop import bridge_request, load_settings
from .llm.client import LLMError

_lock = threading.RLock()
_posting = set()
_polling = set()
_submission_lock = threading.Lock()
_FIELDS = ('type', 'primary_color', 'title', 'color', 'fit', 'pattern', 'texture')
PENDING = ('queued', 'running', 'unknown')
STYLE_IMAGE_MODEL = 'hunyuan-image-alpha'


def _signature(spec, origin='product_photo'):
    value = {'direction': spec, 'image_origin': 'generated', 'image_model': STYLE_IMAGE_MODEL, 'prompt_version': 1} if origin == 'generated' else spec
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _read(uid, key):
    value = db.get_meta('purchase-ref:v1:'+uid+':'+key)
    return json.loads(value) if value else None


def _write(uid, key, value): db.set_meta('purchase-ref:v1:'+uid+':'+key, json.dumps(value, ensure_ascii=False))
def _pointer(plan_id, direction_id): return 'direction:'+plan_id+':'+direction_id

def _bind(uid, value):
    # §37：任务和归属同事务登记，写完才能提交网络请求。
    with db.conn(immediate=True) as connection:
        for key, row in [('job:' + value['job_id'], value), (_pointer(value['plan_id'], value['direction_id']), value['job_id'])]:
            connection.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                ('purchase-ref:v1:' + uid + ':' + key, json.dumps(row, ensure_ascii=False)))
def _public(record): return {key: record[key] for key in ('job_id', 'plan_id', 'direction_id', 'issued_at', 'status', 'references', 'error', 'image_origin') if key in record}


def _direction(uid, plan_id, direction_id, *, with_origin=False):
    latest = purchase._read(uid, 'latest', None)
    saved = purchase._read(uid, 'choices', {'saved': []})['saved']
    for plan in [latest, *saved, *purchase._read(uid, 'history', [])]:
        if plan and plan.get('id') == plan_id:
            for direction in plan['directions']:
                if direction['id'] == direction_id:
                    value = {key: direction[key] for key in _FIELDS}
                    if with_origin: value['image_origin'] = direction.get('image_origin', 'product_photo')
                    return value
    raise purchase.PurchaseError('这个添置方向已更新，请重新读取。', 404)


def snapshots(uid, plans):
    result = {}
    for plan in plans:
        if not plan: continue
        for direction in plan.get('directions', []):
            key = _read(uid, _pointer(plan['id'], direction['id']))
            record = _read(uid, 'job:'+key) if key else None
            if record: result[direction['id']] = _public(record)
    return result


def _source_url(value):
    if not isinstance(value, str): raise ValueError('参考来源不正确')
    p = urlsplit(value)
    if not isinstance(value, str) or len(value) > 3000 or any(ord(c) < 33 for c in value) or '\\' in value or p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password or p.fragment or p.port not in (None, 443 if p.scheme == 'https' else 80):
        raise ValueError('参考来源不正确')
    if '.' not in p.hostname or p.hostname.endswith(('.localhost', '.local', '.internal')): raise ValueError('参考来源不正确')
    try:
        address = ipaddress.ip_address(p.hostname)
    except ValueError: pass
    else:
        if not address.is_global or address.is_multicast: raise ValueError('参考来源不正确')
    return value


def _photos(payload, origin='product_photo'):
    rows = payload.get('references', [])
    if not isinstance(rows, list) or len(rows) > 3: raise ValueError('参考照片返回不完整')
    result = []; seen = set()
    for row in rows:
        limit = 10 * 1024 * 1024 if origin == 'generated' else 2_000_000
        if not isinstance(row, dict) or not isinstance(row.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', row['sha256']) or not isinstance(row.get('data'), str) or len(row['data']) > (limit + 2) // 3 * 4:
            raise ValueError('参考照片返回不完整')
        data = base64.b64decode(row['data'], validate=True); kind = images.sniff(data)
        if not kind or kind[0] not in ('image/jpeg', 'image/png', 'image/webp') or kind[0] != row.get('mime') or len(data) > limit or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('参考照片校验未通过')
        if origin == 'generated':
            if row.get('origin') != 'generated' or row.get('image_model') != STYLE_IMAGE_MODEL or row.get('source_url'):
                raise ValueError('款式示意来源不正确')
            provenance = {'origin': 'generated', 'image_model': STYLE_IMAGE_MODEL}
        else:
            if row.get('origin') == 'generated': raise ValueError('实物任务不能接收生成图片')
            provenance = {'source_url': _source_url(row.get('source_url'))}
        if row['sha256'] in seen: continue
        seen.add(row['sha256']); name = 'reference-'+row['sha256']+kind[1]
        target = db.IMAGES_DIR/name
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != row['sha256']:
            temporary = target.with_name(name+'.'+uuid4().hex+'.tmp'); temporary.write_bytes(data); os.replace(temporary, target)
        result.append({'id': row['sha256'][:16], 'image_path': name, **provenance, 'name': str(row.get('name') or '款式参考')[:180]})
    return result


def _cache_usable(rows):
    if not rows: return False
    try:
        for row in rows:
            name = row.get('image_path', '')
            if not re.fullmatch(r'reference-[0-9a-f]{64}\.(jpg|png|webp)', name): return False
            data = (db.IMAGES_DIR/name).read_bytes()
            limit = 10 * 1024 * 1024 if row.get('origin') == 'generated' else 2_000_000
            if len(data) > limit or hashlib.sha256(data).hexdigest() != name[10:74] or not images.sniff(data): return False
        return True
    except (OSError, KeyError, TypeError): return False


def _apply(uid, record, response):
    if response.get('job_id') != record['job_id'] or response.get('status') not in ('queued', 'running', 'complete', 'empty', 'failed', 'interrupted', 'missing'):
        raise ValueError('搜图任务返回不正确')
    state = response['status']
    if state == 'missing':
        # 提交结果未知时先查询，避免上传仍在路上就重新收费。
        if time.time() - record['issued_at'] < 60: return record
        state = 'failed'; response = {'error': '电脑未保留这次搜图任务，可以重新寻找。'}
    if state == 'complete':
        rows = _photos(response, record.get('image_origin', 'product_photo'))
        if not rows: raise ValueError('本次参考照片暂时无法读取')
        record['references'] = rows
        _write(uid, 'cache:'+record['signature'], {'references': rows, 'created_at': time.time()})
    record.update(status=state, error=str(response.get('error') or '')[:120])
    _write(uid, 'job:'+record['job_id'], record)
    return record


def _submit(uid, record, spec):
    try:
        settings = load_settings()
        if not settings: raise LLMError('请先连接电脑搜图服务。', code='reference_not_configured', retryable=False)
        endpoint = '/style/jobs' if record.get('image_origin') == 'generated' else '/reference/jobs'
        # 等待提交锁时不持状态锁；模型执行在电脑排队，手机只串行登记任务。
        with _submission_lock:
            response = bridge_request(settings, endpoint, payload={'job_id': record['job_id'], 'direction': spec})
        with _lock: _apply(uid, record, response)
    except (LLMError, ValueError, OSError) as error:
        code = getattr(error, 'code', '')
        known = code in ('reference_not_configured', 'reference_paused', 'bridge_login', 'bridge_pairing', 'insufficient_balance', 'bridge_busy')
        with _lock:
            record.update(status='failed' if known else 'unknown', error=(str(error).replace('电脑识别', '电脑搜图') if isinstance(error, LLMError) else '暂未确认搜图结果，请查询这次任务。')[:120])
            _write(uid, 'job:'+record['job_id'], record)
    finally:
        with _lock: _posting.discard((uid, record['job_id']))


def start(uid, plan_id, direction_id, job_id, *, retry=False):
    value = _direction(uid, plan_id, direction_id, with_origin=True)
    origin = value.pop('image_origin')
    return _start(uid, plan_id, direction_id, job_id, value, retry=retry, image_origin=origin)


def start_prepared(uid, request_id, direction_id):
    from . import purchase_intent
    record = purchase_intent.preparation_record(uid, request_id)
    direction = next((d for d in record['directions'] if d['id'] == direction_id), None)
    if record['status'] != 'running' or not direction or direction_id not in record['jobs']:
        raise purchase.PurchaseError('这次准备未授权该方向。', 409)
    return _start(uid, record['plan_id'], direction_id, record['jobs'][direction_id],
        {key: direction[key] for key in _FIELDS}, image_origin=record.get('image_origin', 'product_photo'))


def start_analysis(uid, request_id, direction_id):
    """§37：仅从已登记分析快照解析候选，不接受前端提交搜索规格。"""
    from . import purchase_analysis
    record = purchase_analysis.record(uid, request_id)
    direction = next((d for d in record['directions'] if d['id'] == direction_id), None)
    if record['status'] != 'running' or not direction or direction_id not in record['jobs']:
        raise purchase.PurchaseError('这次分析未授权该方向。', 409)
    return _start(uid, record['plan_id'], direction_id, record['jobs'][direction_id],
        {key: direction[key] for key in _FIELDS}, image_origin=record.get('image_origin', 'product_photo'))


def copy_ready(uid, source_plan, source, target_plan, target, job_id):
    """§37：选择后只复制已核验引用，缓存缺失绝不回退到付费搜图。"""
    with _lock:
        original = snapshots(uid, [{'id': source_plan, 'directions': [source]}]).get(source['id'])
        full = _read(uid, 'job:' + original['job_id']) if original else None
        fields = {key: source[key] for key in _FIELDS}
        origin = source.get('image_origin', 'product_photo')
        signature = _signature(fields, origin)
        if fields != {key: target[key] for key in _FIELDS} or not original or not full or full.get('signature') != signature or full.get('plan_id') != source_plan or full.get('direction_id') != source['id'] or original['status'] != 'complete' or not _cache_usable(original.get('references')):
            raise purchase.PurchaseError('这组款式图片已不可用，请重新分析；已有建议仍保留。', 409)
        if origin != target.get('image_origin', 'product_photo') or origin != full.get('image_origin', 'product_photo') or any(row.get('origin', 'product_photo') != origin for row in original['references']):
            raise purchase.PurchaseError('款式图片来源与方向不一致，请重新分析。', 409)
        existing = _read(uid, 'job:' + job_id)
        if existing:
            if existing['signature'] != signature or existing['plan_id'] != target_plan or existing['direction_id'] != target['id']:
                raise purchase.PurchaseError('这次款式选择的引用不一致。', 409)
            return _public(existing)
        value = {'job_id': job_id, 'plan_id': target_plan, 'direction_id': target['id'], 'signature': signature,
            'issued_at': time.time(), 'status': 'complete', 'references': original['references'], 'error': '', 'image_origin': origin}
        _bind(uid, value)
        return _public(value)


def _start(uid, plan_id, direction_id, job_id, spec, *, retry=False, image_origin='product_photo'):
    try: job_id = str(UUID(job_id))
    except (ValueError, TypeError, AttributeError): raise purchase.PurchaseError('搜图请求编号不正确。', 422)
    with _lock:
        signature = _signature(spec, image_origin)
        existing = _read(uid, 'job:'+job_id)
        if existing:
            if existing['signature'] != signature or existing['direction_id'] != direction_id or existing['plan_id'] != plan_id: raise purchase.PurchaseError('同一次搜图不能更换方向。', 409)
            return _public(existing)
        previous_id = _read(uid, _pointer(plan_id, direction_id))
        previous = _read(uid, 'job:'+previous_id) if previous_id else None
        if previous and previous.get('image_origin', 'product_photo') != image_origin:
            raise purchase.PurchaseError('旧任务来源与本次款式不同，请重新分析。', 409)
        if previous and (previous['status'] in PENDING or previous['status'] == 'complete' and not retry): return _public(previous)
        cached = _read(uid, 'cache:'+signature) if not retry else None
        cached_rows = (cached or {}).get('references', [])
        cache_ok = _cache_usable(cached_rows) and all(row.get('origin', 'product_photo') == image_origin for row in cached_rows)
        record = {'job_id': job_id, 'plan_id': plan_id, 'direction_id': direction_id, 'signature': signature, 'issued_at': time.time(), 'status': 'complete' if cache_ok else 'queued', 'references': cached_rows if cache_ok else (previous or {}).get('references', []), 'error': '', 'image_origin': image_origin}
        _bind(uid, record)
        if not cache_ok:
            _posting.add((uid, job_id)); threading.Thread(target=_submit, args=(uid, dict(record), spec), daemon=True).start()
        return _public(record)


def launch_for_plan(uid, plan):
    """只从用户显式提交的新推荐调用；回看、GET与重启不进入这里。"""
    if not load_settings(): return
    ensure_for_plan(uid, plan['id'])


def ensure_for_plan(uid, plan_id):
    """用户确认§32的默认配图流程；重复进入不会重放终态或未知任务。GET仍只读。"""
    with _lock:
        plans = [purchase._read(uid, 'latest', None), *purchase._read(uid, 'choices', {'saved': []})['saved']]
        matches = [p for p in plans if p and p.get('id') == plan_id]
        if not matches: raise purchase.PurchaseError('这个添置方向已更新，请重新读取。', 404)
        directions = {d['id']: d for p in matches for d in p.get('directions', [])}
        result = {}
        for direction in directions.values():
            identifier = _read(uid, _pointer(plan_id, direction['id']))
            record = _read(uid, 'job:'+identifier) if identifier else None
            if record:
                if record['status'] == 'complete' and not _cache_usable(record.get('references', [])):
                    record.update(status='failed', references=[], error='商品图暂时无法读取，请重试。')
                    _write(uid, 'job:'+record['job_id'], record)
                result[direction['id']] = _public(record)
            else:
                identifier = str(uuid5(NAMESPACE_URL, 'wardrobe-required-reference:'+uid+':'+plan_id+':'+direction['id']))
                result[direction['id']] = start(uid, plan_id, direction['id'], identifier)
        return {'references': result}


def status(uid, job_id):
    try: job_id = str(UUID(job_id))
    except (ValueError, TypeError, AttributeError): raise purchase.PurchaseError('搜图请求编号不正确。', 422)
    with _lock:
        record = _read(uid, 'job:'+job_id)
        if not record: raise purchase.PurchaseError('没有找到这次搜图记录。', 404)
        if record['status'] not in PENDING or (uid, job_id) in _posting or (uid, job_id) in _polling: return _public(record)
        _polling.add((uid, job_id))
    try:
        settings = load_settings()
        if not settings: raise LLMError('电脑搜图尚未连接。', code='reference_not_configured')
        endpoint = '/style/jobs/' if record.get('image_origin') == 'generated' else '/reference/jobs/'
        response = bridge_request(settings, endpoint+job_id)
        with _lock: record = _apply(uid, record, response)
    except (LLMError, ValueError, OSError) as error:
        # 查询失败继续保留原任务号与已有图，不因断网新建收费任务。
        if not isinstance(error, LLMError): record['status'] = 'failed'
        record['error'] = (str(error).replace('电脑识别', '电脑搜图') if isinstance(error, LLMError) else '这次照片暂时读不到，可以重新寻找。')[:120]
        with _lock: _write(uid, 'job:'+job_id, record)
    finally:
        with _lock: _polling.discard((uid, job_id))
    return _public(record)
