/** §31：仅展示搜图服务核验并缓存的真实照片；不采用模型自行输出的网址。 */
import { esc, icon } from '../../core/ui.js';
import { api } from '../../core/api.js';
import { referenceState, referenceIndex, selectReference, invalidateReference, referenceMediaChanged } from './reference-store.js';
import { bindHorizontalSwipe } from '../../components/horizontal-swipe.js';
const photoLoads = new Map();
function photoKey(direction, row) { const state = referenceState(direction); return state.job_id + ':' + (state.status === 'complete' ? 'complete' : 'retained') + ':' + row.image_path; }
function candidates(direction) {
  return (referenceState(direction).references || []).filter(row => {
    if (!/^reference-[a-f0-9]{64}\.(png|jpg|webp)$/.test(row.image_path || '')) return false;
    const state = referenceState(direction);
    if (state.image_origin === 'generated' || direction.image_origin === 'generated') return state.image_origin === 'generated' && direction.image_origin === 'generated' && row.origin === 'generated' && row.image_model === 'hunyuan-image-alpha' && !row.source_url;
    if (row.origin === 'generated') return false;
    try { const u = new URL(row.source_url); return ['http:', 'https:'].includes(u.protocol) && u.hostname.includes('.') && !u.username && !u.password; } catch { return false; }
  }).slice(0, 3);
}
// §32：先确认本机图像已解码，再让图片与正文一起进入正式卡；不触发搜图。
function warm(direction, rows) {
  if (typeof Image === 'undefined') return;
  for (const row of rows) {
    const key = photoKey(direction, row);
    if (photoLoads.has(key)) continue;
    const image = new Image(), entry = { state: 'loading' }; photoLoads.set(key, entry);
    const finish = ok => {
      if (entry.state !== 'loading') return;
      clearTimeout(entry.timer); entry.state = ok ? 'ready' : 'failed';
      if (photoKey(direction, row) !== key) return;
      if (!ok) invalidateReference(direction.id, row.id);
      else referenceMediaChanged();
    };
    entry.timer = setTimeout(() => finish(false), 15000);
    image.onload = () => { if (image.decode) image.decode().then(() => finish(true), () => finish(false)); else finish(image.naturalWidth > 0); };
    image.onerror = () => finish(false); image.src = api.imageUrl(row.image_path);
  }
}
export function referenceRows(direction, { preload = true } = {}) {
  const rows = candidates(direction);
  if (preload) warm(direction, rows);
  return rows;
}
function decoding(direction) {
  const rows = referenceRows(direction);
  return typeof Image !== 'undefined' && rows.some(row => photoLoads.get(photoKey(direction, row))?.state === 'loading');
}
export function purchaseReference(direction) { const rows = referenceRows(direction); if (decoding(direction) || rows.some(row => photoLoads.get(photoKey(direction, row))?.state === 'failed')) return null; return rows[referenceIndex(direction)] || rows[0] || null; }
export function referenceLabel(direction) { return direction?.image_origin === 'generated' ? 'AI 款式示意' : '实物款式参考'; }
export function referenceStatus(direction) {
  const state = referenceState(direction), busy = ['queued', 'running', 'unknown'].includes(state.status) || decoding(direction);
  const generated = direction?.image_origin === 'generated';
  const label = decoding(direction) ? '正在载入参考图' : generated ? {idle:'正在准备款式示意',queued:'正在排队生成',running:'正在生成款式示意',unknown:'正在确认生成进度',complete:'AI 款式示意',empty:'款式图暂未生成',failed:'款式图暂未生成',interrupted:'生成结果待确认'}[state.status] || '正在准备款式示意' : { idle: '正在匹配商品图', queued: '正在匹配商品图', running: '正在匹配商品图', unknown: '正在确认配图进度', complete: '已有实物商品图', empty: '商品图匹配未成功', failed: '商品图匹配未成功', interrupted: '配图已中断' }[state.status] || '正在匹配商品图';
  return { ...state, busy, label };
}
export function referencePhoto(direction) {
  const ref = purchaseReference(direction), state = referenceStatus(direction);
  if (!ref) return `<span class="purchase-reference-empty ${state.busy || state.status === 'idle' ? 'is-searching' : ''}">${icon(state.busy || state.status === 'idle' ? 'search' : 'photo')}<span>${esc(state.label)}</span></span>`;
  return `<span class="purchase-reference-photo"><img src="${esc(api.imageUrl(ref.image_path))}" data-purchase-reference="${esc(ref.id)}" data-reference-owner="${esc(direction.id)}" alt="${esc(ref.name)}，${referenceLabel(direction)}"></span>`;
}
export function referenceCredit(direction) {
  const ref = purchaseReference(direction);
  return ref ? `<div class="purchase-reference-credit"><span>${direction?.image_origin === 'generated' ? 'AI 生成的款式示意，供选购方向参考。' : '看款式方向，具体商品由你选。'}</span></div>` : '';
}
export function referenceActions(direction, plan) {
  const state = referenceStatus(direction);
  if (direction?.image_origin !== 'generated') return '';
  if (purchaseReference(direction) || state.busy && !state.error || state.status === 'idle') return '';
  return `<button type="button" class="purchase-reference-retry" data-reference-action="retry" data-reference-direction="${esc(direction.id)}" data-reference-plan="${esc(plan.id)}">${icon('replay')}重试</button>`;
}
export function referencePreparation(direction, plan, compact = false) {
  const state = referenceStatus(direction), busy = (state.busy || state.status === 'idle') && !state.error;
  return `<section class="purchase-preparation ${compact ? 'is-compact' : ''} ${busy ? 'is-preparing' : 'is-failed'}" data-reference-status="${esc(direction.id)}" role="status"><div class="purchase-preparation-symbol">${icon(busy ? 'wardrobe' : 'photo')}</div><h2>${esc(state.label)}</h2><p>${busy ? '准备颜色、轮廓合适的款式参考，<br>连同选购要点一起呈现。' : '这条建议的参考图暂时不可用。<br>已有完整建议仍然保留。'}</p>${busy ? '<div class="purchase-preparation-steps"><span>✓ 分析衣橱</span><span>准备款式参考</span><span>整理建议</span></div>' : referenceActions(direction, plan)}${!busy && state.error ? `<small>${esc(state.error)}</small>` : ''}</section>`;
}
export function referenceGallery(direction, plan) {
  const rows = referenceRows(direction), index = referenceIndex(direction);
  return `<div class="purchase-reference-gallery"><div class="purchase-reference-main" data-reference-swipe>${referencePhoto(direction)}${rows.length ? `<span class="purchase-reference-counter">${index + 1} / ${rows.length}</span>` : ''}</div>${rows.length > 1 ? `<div class="purchase-reference-options" role="group" aria-label="选择款式参考图片">${rows.map((row, i) => `<button type="button" data-reference-index="${i}" aria-label="查看参考图${i + 1}" aria-pressed="${i === index}"><img src="${esc(api.imageUrl(row.image_path))}" data-purchase-reference="${esc(row.id)}" data-reference-owner="${esc(direction.id)}" alt=""><span>参考 ${String(i + 1).padStart(2, '0')}</span></button>`).join('')}</div>` : ''}${referenceCredit(direction)}</div>`;
}
export function bindReferenceGallery(host, direction, plan, onSelect = () => {}) {
  function select(index, focus = false) {
    selectReference(direction, index); const old = host.querySelector('.purchase-reference-gallery');
    if (!old) return;
    const focused = old.contains(document.activeElement); old.outerHTML = referenceGallery(direction, plan);
    if (focus || focused) host.querySelector(`[data-reference-index="${referenceIndex(direction)}"]`)?.focus({ preventScroll: true });
    onSelect();
  }
  const click = event => { const button = event.target.closest('[data-reference-index]'); if (button) select(Number(button.dataset.referenceIndex), true); };
  host.addEventListener('click', click);
  const swipe = bindHorizontalSwipe(host, { selector: '[data-reference-swipe]', enabled: () => referenceRows(direction).length > 1,
    onSwipe: step => select((referenceIndex(direction) + step + referenceRows(direction).length) % referenceRows(direction).length),
    onDrag: (node, offset) => { node.style.transform = offset ? `translateX(${offset}px)` : ''; },
  });
  return { refresh() { const old = host.querySelector('.purchase-reference-gallery'); if (old) { swipe.cancel(); const scroll = host.scrollTop, focused = old.contains(document.activeElement), selected = document.activeElement?.dataset.referenceIndex, sourceFocused = document.activeElement?.matches('.purchase-reference-credit a');
      old.outerHTML = referenceGallery(direction, plan); host.scrollTop = scroll;
      if (focused) (host.querySelector(selected !== undefined ? `[data-reference-index="${referenceIndex(direction)}"]` : sourceFocused ? '.purchase-reference-credit a' : '[data-reference-action]') || host.closest('[role="dialog"]')?.querySelector('.purchase-close'))?.focus({ preventScroll: true }); } }, dispose() { swipe.dispose(); host.removeEventListener('click', click); } };
}
if (typeof document !== 'undefined') document.addEventListener('error', event => {
  const img = event.target;
  if (!img?.matches?.('img[data-purchase-reference]')) return;
  invalidateReference(img.dataset.referenceOwner, img.dataset.purchaseReference);
}, true);
