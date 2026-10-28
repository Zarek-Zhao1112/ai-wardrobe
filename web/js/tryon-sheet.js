/** §30：真实摄像头玻璃面板；实时换装服务未配置时不伪造 AI 画面。 */
import { api } from './api.js';
import { esc, icon } from './ui.js';
import { createTryonCamera } from './tryon-camera.js';
let active = null;
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const focusable = 'button,a[href],input,select,summary,[tabindex]';
const photo = item => { const url = api.presentationUrl(item); return url ? `<img src="${esc(url)}" data-flatlay-source="${esc(url)}" alt="${esc(item.name || item.type_label || '已有衣物')}">` : icon('wardrobe'); };
function stopCamera(entry) { entry.camera?.stop(); }
function background(entry, hide) {
  if (hide) {
    entry.background = [document.querySelector('.topbar'), entry.root, document.querySelector('#tabbar')].filter(Boolean).map(node => ({ node, aria: node.getAttribute('aria-hidden'), filter: node.style.filter }));
    entry.focus = entry.background.flatMap(({ node }) => [...node.querySelectorAll(focusable)].map(el => [el, el.getAttribute('tabindex')]));
    entry.background.forEach(({ node }) => node.setAttribute('aria-hidden', 'true')); entry.focus.forEach(([el]) => el.tabIndex = -1);
    document.body.classList.add('tryon-sheet-open');
  } else {
    entry.background.forEach(({ node, aria, filter }) => { node.style.filter = filter; aria === null ? node.removeAttribute('aria-hidden') : node.setAttribute('aria-hidden', aria); });
    entry.focus.forEach(([el, value]) => value === null ? el.removeAttribute('tabindex') : el.setAttribute('tabindex', value));
    document.body.classList.remove('tryon-sheet-open');
  }
}
function controls(entry, snapshot = entry.camera.snapshot()) {
  if (active !== entry || entry.phase === 'closing') return;
  const { state, error, facing } = snapshot, opened = state === 'preview', opening = state === 'opening';
  entry.video.hidden = !opened;
  entry.empty.hidden = opened;
  entry.empty.innerHTML = `${icon('camera')}<strong>${opening ? '正在开启摄像头…' : '像照镜子一样，试试衣服。'}</strong><p>${opening ? '允许摄像头访问后，预览会显示在这里。' : '开启摄像头，站到画面里。<br>选一件衣服，看看整体感觉。'}</p>`;
  entry.badge.textContent = opened ? '原始画面' : opening ? '正在开启' : '摄像头未开启';
  entry.video.classList.toggle('is-mirrored', facing === 'user');
  entry.layer.querySelector('[data-tryon-flip]').hidden = !opened;
  entry.layer.querySelector('[data-tryon-flip]').disabled = opening;
  const main = entry.layer.querySelector('[data-tryon-camera]');
  main.disabled = opening || opened;
  main.innerHTML = icon(opened ? 'spark' : 'camera') + `<span>${opened ? '实时试衣暂不可用' : opening ? '正在开启…' : '开启摄像头'}</span>`;
  entry.layer.querySelector('[data-tryon-stop]').disabled = !opened && !opening;
  entry.status.textContent = error || (opened ? '实时试衣服务尚未连接，当前显示原始摄像头画面。' : '先开启摄像头预览；实时试衣服务尚未连接。');
  entry.status.classList.toggle('error', !!error);
}
function garments(entry) {
  const set = entry.outfits[entry.index], items = (set.items || []).filter(Boolean);
  entry.piece = Math.min(entry.piece, Math.max(0, items.length - 1));
  entry.layer.querySelector('.tryon-pieces').innerHTML = items.map((item, index) => `<button type="button" data-tryon-piece="${index}" aria-pressed="${index === entry.piece}" aria-label="选择${esc(item.name || item.type_label || '这件衣物')}作为试穿参考">${photo(item)}<span>${esc(item.name || item.type_label || '未命名衣物')}</span></button>`).join('');
  entry.layer.querySelector('.tryon-current').textContent = `当前参考：${items[entry.piece]?.name || items[entry.piece]?.type_label || '暂无衣物'}`;
  entry.layer.querySelector('[data-tryon-next]').hidden = entry.outfits.length < 2;
}
async function close(instant = false) {
  const entry = active; if (!entry) return;
  if (entry.closing) return entry.closing;
  entry.phase = 'closing'; entry.camera.dispose();
  document.removeEventListener('focusin', entry.focusin);
  document.removeEventListener('keydown', entry.key);
  entry.layer.classList.remove('is-open');
  entry.layer.style.pointerEvents = 'none';
  entry.background.forEach(({ node, filter }) => node.style.filter = filter);
  entry.closing = (async () => {
    if (!instant && !reduced.matches) await new Promise(resolve => setTimeout(resolve, 420));
    if (active !== entry) return;
    entry.removers.forEach(remove => remove()); background(entry, false); entry.layer.remove(); active = null;
    if (location.hash === entry.hash && history.state?.tryonSheet === entry.id) { const state = { ...history.state }; delete state.tryonSheet; history.replaceState(state, '', location.href); }
    const anchor = entry.anchor?.isConnected ? entry.anchor : entry.root.querySelector('[data-tryon],#o-go');
    if (anchor?.isConnected) anchor.focus({ preventScroll: true });
  })(); return entry.closing;
}
export const closeTryonSheet = () => close(true);
export function openTryonSheet({ root, anchor, outfits, index = 0, onSelect }) {
  if (active || !outfits?.length) return;
  const layer = document.createElement('div'); layer.className = 'tryon-layer';
  layer.innerHTML = `<div class="tryon-veil"></div><section class="tryon-panel" role="dialog" aria-modal="true" aria-label="实时试衣" tabindex="-1"><header class="tryon-header"><span>${icon('camera')}实时试衣</span><button type="button" class="tryon-glass" data-tryon-close aria-label="关闭实时试衣">${icon('close')}</button></header><div class="tryon-scroll"><div class="tryon-frame"><video autoplay muted playsinline hidden aria-label="原始摄像头画面"></video><div class="tryon-empty"></div><span class="tryon-badge">摄像头未开启</span><button type="button" class="tryon-compare" disabled title="实时试衣开始后可比较原始画面">${icon('compare')}查看原始画面</button><button type="button" data-tryon-flip class="tryon-glass tryon-flip" hidden aria-label="切换前后摄像头">${icon('flip')}</button></div><div class="tryon-piece-heading"><strong>这套的单品</strong><button type="button" data-tryon-next>${icon('replay')}换一套</button></div><div class="tryon-pieces"></div><p class="tryon-current"></p><p class="tryon-status" role="status" aria-live="polite"></p><div class="tryon-actions"><button type="button" class="primary" data-tryon-camera>${icon('camera')}开启摄像头</button><button type="button" data-tryon-stop disabled>${icon('stop')}停止</button></div><p class="tryon-note">点击停止或关闭面板，摄像头随即关闭。</p></div></section>`;
  const entry = { id: String(Date.now()), root, anchor, outfits, index: Math.min(Math.max(0, index), outfits.length - 1), piece: 0, layer, phase: 'opening', hash: location.hash, removers: [], onSelect };
  active = entry; document.body.append(layer); entry.panel = layer.querySelector('.tryon-panel'); entry.video = layer.querySelector('video'); entry.empty = layer.querySelector('.tryon-empty'); entry.badge = layer.querySelector('.tryon-badge'); entry.status = layer.querySelector('.tryon-status');
  entry.camera = createTryonCamera({ video: entry.video, onChange: snapshot => controls(entry, snapshot) });
  background(entry, true); garments(entry); controls(entry); history.pushState({ ...history.state, tryonSheet: entry.id }, '', location.href);
  function listen(target, name, listener) { target?.addEventListener(name, listener); entry.removers.push(() => target?.removeEventListener(name, listener)); }
  function requestClose() { if (active !== entry || entry.phase === 'closing' || entry.returning) return; stopCamera(entry); entry.returning = true; if (history.state?.tryonSheet === entry.id && location.hash === entry.hash) history.back(); else void close(); }
  entry.key = event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); requestClose(); } if (event.key === 'Tab') { const nodes = [...entry.panel.querySelectorAll(focusable)].filter(node => !node.disabled && node.getClientRects().length); if (!nodes.length) { event.preventDefault(); entry.panel.focus(); } else if (event.shiftKey && (document.activeElement === nodes[0] || document.activeElement === entry.panel)) { event.preventDefault(); nodes.at(-1).focus(); } else if (!event.shiftKey && document.activeElement === nodes.at(-1)) { event.preventDefault(); nodes[0].focus(); } } };
  entry.focusin = event => { if (active === entry && !entry.panel.contains(event.target)) entry.panel.focus({ preventScroll: true }); };
  listen(document, 'keydown', entry.key); listen(document, 'focusin', entry.focusin);
  listen(window, 'popstate', () => { if (history.state?.tryonSheet !== entry.id) void close(); });
  listen(window, 'hashchange', () => { if (location.hash !== entry.hash) void close(true); });
  listen(document, 'visibilitychange', () => { if (document.hidden) stopCamera(entry); });
  listen(window, 'pagehide', () => stopCamera(entry)); listen(window, 'wardrobe-camera-stop', () => stopCamera(entry));
  const resize = () => { layer.style.setProperty('--tryon-vh', (visualViewport?.height || innerHeight) + 'px'); layer.style.setProperty('--tryon-top', (visualViewport?.offsetTop || 0) + 'px'); };
  resize(); listen(window, 'resize', resize); listen(visualViewport, 'resize', resize);
  layer.querySelector('[data-tryon-close]').onclick = requestClose; layer.querySelector('.tryon-veil').onclick = requestClose;
  layer.querySelector('[data-tryon-camera]').onclick = () => { void entry.camera.start(); };
  layer.querySelector('[data-tryon-stop]').onclick = () => stopCamera(entry);
  layer.querySelector('[data-tryon-flip]').onclick = () => { void entry.camera.switch(); };
  layer.querySelector('[data-tryon-next]').onclick = () => { entry.index = (entry.index + 1) % entry.outfits.length; entry.piece = 0; garments(entry); entry.onSelect?.(entry.index); };
  layer.querySelector('.tryon-pieces').onclick = event => { const button = event.target.closest('[data-tryon-piece]'); if (!button) return; entry.piece = Number(button.dataset.tryonPiece); garments(entry); layer.querySelector(`[data-tryon-piece="${entry.piece}"]`)?.focus({ preventScroll: true }); };
  entry.panel.focus({ preventScroll: true });
  requestAnimationFrame(() => { if (active !== entry || entry.phase === 'closing') return; entry.layer.classList.add('is-open'); entry.background.forEach(({ node }) => node.style.filter = 'blur(8px)'); entry.phase = 'open'; });
}
