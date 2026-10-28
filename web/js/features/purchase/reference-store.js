/** §32：可见旧建议补齐首次配图；已有终态和未知提交不因切页重放。 */
import { api } from '../../core/api.js';
const states = new Map(), selections = new Map();
const listeners = new Set();
const rejectedPhotos = new Map();
const pending = state => ['queued', 'running', 'unknown'].includes(state?.status);
export function referenceMediaChanged() { listeners.forEach(fn => fn()); }
export function referenceState(direction) { return states.get(direction?.id) || { status: 'idle', references: [] }; }
export function referenceIndex(direction) {
  const state = referenceState(direction), key = state.job_id + ':' + (state.references || []).map(r => r.id).join(',');
  return Math.min(selections.get(direction?.id)?.key === key ? selections.get(direction.id).index : 0, Math.max(0, (state.references?.length || 0) - 1));
}
export function selectReference(direction, index) {
  const state = referenceState(direction);
  if (!Number.isInteger(index) || index < 0 || index >= (state.references?.length || 0)) return;
  selections.set(direction.id, { key: state.job_id + ':' + state.references.map(r => r.id).join(','), index });
}
export function seedReferences(snapshot = {}) {
  for (const [id, state] of Object.entries(snapshot)) {
    const old = states.get(id);
    const stamp = value => Number(value?.issuedAt || (value?.issued_at || 0) * 1000);
    if (old && old.job_id !== state.job_id && stamp(old) > stamp(state)) continue;
    if (old?.job_id === state.job_id && !pending(old) && pending(state)) continue;
    if (old?.job_id === state.job_id && pending(old) && state.status === 'complete') rejectedPhotos.delete(id);
    const rows = (state.references || []).filter(row => !rejectedPhotos.get(id)?.has(row.id));
    states.set(id, rows.length !== (state.references || []).length ? { ...state, references: rows, ...(rows.length || pending(state) ? {} : { status: 'failed', error: '商品图暂时无法读取，请重试。' }) } : state);
  }
}
export function invalidateReference(directionId, photoId) {
  const state = states.get(directionId); if (!state) return;
  const rows = (state.references || []).filter(row => row.id !== photoId);
  if (rows.length === (state.references || []).length) return;
  if (!rejectedPhotos.has(directionId)) rejectedPhotos.set(directionId, new Set());
  rejectedPhotos.get(directionId).add(photoId);
  states.set(directionId, { ...state, references: rows, ...(rows.length || pending(state) ? {} : { status: 'failed', error: '商品图暂时无法读取，请重试。' }) });
  listeners.forEach(fn => fn());
}
export function createReferenceController(onChange) {
  let disposed = false, timer = null, polling = false;
  const watching = new Set(), sending = new Set(), ensured = new Set(), ensuring = new Set();
  const notify = () => { if (!disposed) onChange(); };
  listeners.add(notify);
  const schedule = () => { clearTimeout(timer); if (!disposed && [...watching].some(id => pending(states.get(id)))) timer = setTimeout(poll, document.hidden ? 12000 : 1800); };
  async function poll() {
    if (disposed || polling) return;
    polling = true;
    try {
      for (const id of watching) {
        const state = states.get(id);
        if (disposed || !pending(state) || sending.has(id)) continue;
        try {
          const next = await api.purchaseReferenceState(state.job_id);
          if (disposed || states.get(id)?.job_id !== state.job_id) continue;
          if (!pending(states.get(id)) && pending(next)) continue;
          seedReferences({ [id]: next }); notify();
        } catch (error) {
          if (!disposed && states.get(id)?.job_id === state.job_id) {
            const absent = error.status === 404 && Date.now() - (state.issuedAt || 0) > 60000;
            states.set(id, { ...state, status: absent ? 'failed' : state.status, error: absent ? '没有找到这次搜图记录，可以重新寻找。' : '暂时读不到找图进度，可稍后查询。' }); notify();
          }
        }
      }
    } finally { polling = false; schedule(); }
  }
  return {
    seed(snapshot, ids = []) { seedReferences(snapshot); watching.clear(); [...Object.keys(snapshot || {}), ...ids].forEach(id => watching.add(id)); schedule(); },
    async ensure(plan, confirm = false) {
      if (disposed || !plan?.directions?.length || ensuring.has(plan.id) || ensured.has(plan.id) && !confirm) return;
      ensuring.add(plan.id);
      ensured.add(plan.id); plan.directions.forEach(d => watching.add(d.id));
      try {
        const result = await api.purchaseReferenceEnsure(plan.id);
        if (disposed) return;
        seedReferences(result.references); notify();
      } catch {
        if (disposed) return;
        // 补齐接口幂等；响应未知先读现有状态，不再自动创建另一轮搜索。
        try { const result = await api.purchaseState(); if (disposed) return; seedReferences(result.references); } catch {}
        if (disposed) return;
        plan.directions.forEach(d => { if (!states.has(d.id)) states.set(d.id, { status: 'failed', needsEnsure: true, references: [], error: '暂未确认配图进度，重试会先确认原任务。' }); });
        notify();
      } finally { ensuring.delete(plan.id); schedule(); }
    },
    async start(plan, direction, retry = false) {
      if (disposed || sending.has(direction.id) || pending(states.get(direction.id)) || states.get(direction.id)?.needsEnsure) return;
      const jobId = crypto.randomUUID?.() || '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, c => (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16));
      const previous = states.get(direction.id);
      if (retry) rejectedPhotos.delete(direction.id);
      const issuedAt = Date.now();
      states.set(direction.id, { ...(previous || {}), job_id: jobId, issuedAt, status: 'queued', error: '', references: previous?.references || [] });
      sending.add(direction.id); watching.add(direction.id); notify();
      try {
        const next = await api.purchaseReferenceStart({ plan_id: plan.id, direction_id: direction.id, job_id: jobId, retry });
        if (!disposed && states.get(direction.id)?.job_id === jobId) { seedReferences({ [direction.id]: next }); notify(); }
      } catch (error) {
        if (!disposed && states.get(direction.id)?.job_id === jobId) {
          const rejected = [400, 401, 403, 404, 409, 422].includes(error.status);
          states.set(direction.id, { ...(previous || {}), job_id: jobId, issuedAt, status: rejected ? 'failed' : 'unknown', references: previous?.references || [], error: rejected ? error.message : '暂未确认这次搜图是否提交，正在查询。' }); notify();
        }
      } finally { sending.delete(direction.id); schedule(); }
    },
    query() { return poll(); },
    dispose() { disposed = true; clearTimeout(timer); watching.clear(); listeners.delete(notify); },
  };
}
