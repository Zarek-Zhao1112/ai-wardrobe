/** §59：添置选衣、选择形式与结果页。生成进度使用原请求编号。 */
import {api} from '../../core/api.js';
import {esc, icon} from '../../core/ui.js';
import {photo} from './intent-ui.js';
import {purchaseReference, referencePhoto, referenceStatus} from './reference.js';
import {seedReferences} from './reference-store.js';

const formats = [
  ['single', '一件单品', '推荐一件能和已选衣物搭配的单品', 'wardrobe'],
  ['multi', '多件单品', '按搭配需要推荐几件单品', 'layers'],
  ['outfit', '一整套穿搭', '把已有衣物和建议添置的单品搭成一套', 'outfits'],
];
const maxSelected = 12;
const pendingKey = 'purchaseBuilderPending:v1';
const formKey = 'purchaseBuilderForm:v1';
const knownRejection = error => [400, 401, 403, 409, 422].includes(error.status);
const newId = () => crypto.randomUUID?.() || '10000000-1000-4000-8000-100000000000'.replace(
  /[018]/g, c => (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16)
);
const action = (name, text, {primary = false, glyph, disabled = false} = {}) =>
  `<button type="button" class="${primary ? 'purchase-primary primary' : 'btn quiet blue'}" data-builder-action="${name}" ${disabled ? 'disabled' : ''}>${glyph ? icon(glyph) : ''}<span>${text}</span></button>`;

function read(storage, key) {
  try { return JSON.parse(window[storage].getItem(key) || 'null'); } catch { return null; }
}

export function createPurchaseBuilder({alive, onChange, onSaved}) {
  let wardrobe = null, loadingWardrobe = false, request = null, result = null;
  let pollTimer, refreshing = false, busy = '', message = '', querying = false, showPrior = false, revision = 0, loaded = false;
  let form = read('sessionStorage', formKey) || {aesthetic: '', item_ids: [], mode: ''};
  let pending = read('localStorage', pendingKey);

  const update = () => { if (alive()) onChange(); };
  const setForm = next => {
    form = next;
    try { sessionStorage.setItem(formKey, JSON.stringify(form)); } catch {}
  };
  const clearPending = () => {
    if (read('localStorage', pendingKey)?.id === pending?.id) {
      try { localStorage.removeItem(pendingKey); } catch {}
    }
    pending = null;
  };
  const selectedPlan = () => !pending && busy !== 'generate' &&
    (showPrior || request?.status === 'completed' && request.id === result?.id) ? result : null;
  const statusSource = () => showPrior ? result : pending || request;
  const uncertain = () => Boolean(pending || request?.pending_reference_count && request.status !== 'running' ||
    request?.status === 'unknown' || request?.status === 'interrupted' && request.phase !== 'finished');

  function schedule() {
    clearTimeout(pollTimer);
    if (alive() && (request?.status === 'running' || pending)) {
      pollTimer = setTimeout(() => void refresh(), document.hidden ? 12000 : 2200);
    }
  }

  function accept(next) {
    loaded = true;
    request = next.request;
    result = next.result;
    seedReferences(next.references);
    if (pending?.id === request?.id) clearPending();
    schedule();
  }

  async function refresh() {
    if (!alive() || refreshing || busy) return;
    refreshing = true;
    const watched = pending?.id;
    const stamp = revision;
    try {
      const next = watched ? await api.purchaseBuilderRequest(watched) : await api.purchaseBuilderState();
      if (alive() && stamp === revision) accept(next);
    } catch (error) {
      if (!alive() || stamp !== revision) return;
      if (pending && error.status === 404 && Date.now() - pending.createdAt > 60000) {
        clearPending();
        message = '原请求未登记，可以重新生成。';
        try {
          const next = await api.purchaseBuilderState();
          if (alive() && stamp === revision) accept(next);
        } catch {}
      } else if (request?.status === 'running' || pending) {
        message = '暂时读不到生成进度，可以稍后查询原任务。';
      } else if (!loaded) {
        message = '暂时读不到生成进度，请重新读取。';
      }
    } finally {
      if (stamp === revision) loaded = true;
      refreshing = false;
      schedule();
      update();
    }
  }

  async function loadWardrobe() {
    if (loadingWardrobe) return;
    loadingWardrobe = true;
    wardrobe = null;
    update();
    try {
      const next = await api.purchaseBuilderState(true);
      if (!alive()) return;
      wardrobe = (next.wardrobe || []).sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
      setForm({...form, item_ids: form.item_ids.filter(id => wardrobe.some(item => item.id === id))});
    } catch (error) {
      if (alive()) message = error.message || '衣橱暂时无法读取。';
    } finally {
      loadingWardrobe = false;
      update();
    }
  }

  async function submit(fromStatus = false) {
    const source = fromStatus ? statusSource() : form;
    if (busy || !source?.item_ids?.length || !source.mode) return false;
    if (request?.status === 'running' || uncertain()) {
      message = '上一轮生成尚未确认，请先查看原任务。';
      update();
      return false;
    }
    const id = newId();
    const input = {aesthetic: source.aesthetic?.id || source.aesthetic, item_ids: source.item_ids, mode: source.mode};
    pending = {id, createdAt: Date.now(), ...input};
    try { localStorage.setItem(pendingKey, JSON.stringify(pending)); }
    catch { pending = null; message = '暂时无法记录这次任务，请检查本机存储。'; update(); return false; }
    busy = 'generate';
    ++revision;
    message = '';
    showPrior = false;
    update();
    try {
      const next = await api.purchaseBuilderStart({request_id: id, ...input});
      if (alive()) accept(next);
      return true;
    } catch (error) {
      if (knownRejection(error)) clearPending();
      if (alive()) message = error.message || '暂时无法确认生成结果。';
      return !knownRejection(error);
    } finally {
      busy = '';
      schedule();
      update();
      if (!pending && request?.id !== id) void refresh();
    }
  }

  async function query() {
    if (busy) return;
    const id = pending?.id || request?.id;
    if (!id) return;
    busy = 'query';
    ++revision;
    querying = true;
    message = '';
    update();
    try {
      const next = await api.purchaseBuilderQuery(id);
      if (!alive()) return;
      accept(next);
      message = next.query_error || (next.request?.status === 'running' ? '原任务还在生成，请稍等。' : '');
    } catch (error) {
      if (alive()) message = error.status === 404 && pending ? '原请求暂未登记，请稍后再查。' : error.message;
    } finally {
      busy = '';
      querying = false;
      schedule();
      update();
    }
  }

  async function save() {
    const plan = selectedPlan();
    if (busy || !plan?.directions.length) return;
    const pairs = plan.directions.map(direction => [direction.id, purchaseReference(direction)?.id]);
    if (pairs.some(([, ref]) => !ref)) return;
    busy = 'save';
    message = '';
    update();
    try { onSaved(await api.purchaseBuilderSave(plan.id, Object.fromEntries(pairs))); }
    catch (error) { if (alive()) message = error.message || '暂时无法保存，请重试。'; }
    finally { busy = ''; update(); }
  }

  function picker() {
    if (!wardrobe) return loadingWardrobe ? '<p class="pi-analysis-status" role="status">正在读取衣橱…</p>'
      : action('load', '重新读取衣橱', {glyph:'refresh'});
    if (!wardrobe.length) return '<div class="pi-empty"><h2>衣橱还没有衣服</h2><p>先录入一件你想搭配的衣物。</p><a class="btn primary" href="#add">去录入衣物</a></div>';
    const cards = wardrobe.map(item => {
      const selected = form.item_ids.includes(item.id);
      return `<button type="button" class="pb-item" data-builder-item="${esc(item.id)}" aria-pressed="${selected}">
        <span class="pb-item-image">${photo(item,{lazy:true})}</span><span class="pb-item-check" aria-hidden="true">${selected ? icon('check') : ''}</span>
        <span class="pb-item-name">${esc(item.name || item.type_label || '衣物')}</span></button>`;
    }).join('');
    return `<p class="section-eyebrow">第 1 步，共 2 步</p><h2 class="screen-title">想用哪几件来搭？</h2>
      <p class="assistant-copy">从衣橱里选一件或多件衣物。</p>
      <div class="pb-grid" role="group" aria-label="衣橱衣物">${cards}</div>
      <div class="pb-actions pb-pick-actions"><span class="selection-note" role="status">已选 ${form.item_ids.length} 件</span>
        ${action('next', '下一步', {primary:true, glyph:'right', disabled:!form.item_ids.length})}</div>`;
  }

  function formatPicker() {
    const choices = formats.map(([id, label, description, glyph]) => `<button type="button" class="pb-format"
      data-builder-format="${id}" aria-pressed="${form.mode === id}">
      <span class="pb-format-icon">${icon(glyph)}</span><span><strong>${label}</strong><small>${description}</small></span>
      <span class="pb-format-check">${form.mode === id ? icon('check') : ''}</span></button>`).join('');
    const thumbs = form.item_ids.slice(0,4).map(id => {
      const item = wardrobe?.find(row => row.id === id);
      return item ? `<span>${photo(item)}</span>` : '';
    }).join('');
    return `<p class="section-eyebrow">第 2 步，共 2 步</p><h2 class="screen-title">这次想怎么添置？</h2>
      <div class="pb-picked"><div>${thumbs}</div><span>已选 ${form.item_ids.length} 件衣橱衣物</span></div>
      <div class="pb-formats" role="group" aria-label="添置形式">${choices}</div>
      <div class="pb-actions">${action('generate', busy === 'generate' ? '正在提交…' : '生成推荐', {primary:true, glyph:'spark', disabled:!form.mode || Boolean(busy)})}</div>
      ${request?.status === 'running' || uncertain() && !busy ? action('resume','查看原任务',{glyph:'history'}) : ''}`;
  }

  function resultHtml(plan, savedIds) {
    const title = formats.find(([id]) => id === plan.mode)?.[1] || '添置';
    if (plan.decision === 'enough') return `<h2 class="screen-title">${esc(plan.headline)}</h2>
      <p class="assistant-copy">${esc(plan.summary)}</p>${action('generate','再生成',{glyph:'refresh', disabled:Boolean(busy)})}`;
    const ready = plan.directions.every(direction => purchaseReference(direction));
    const decoding = !ready && plan.directions.some(direction => referenceStatus(direction).busy);
    if (decoding) return '<p class="pi-analysis-status" role="status"><span class="pi-analysis-orb"></span>正在载入款式图…</p>';
    const saved = plan.directions.every(direction => savedIds.has(direction.id));
    const owned = plan.item_ids.map(id => plan.items[id]);
    const flatlay = plan.mode === 'outfit' && ready ? `<div class="pb-flatlay" role="group" aria-label="整套平铺搭配示意">
      ${owned.map(item => `<span>${photo(item)}</span>`).join('')}${plan.directions.map(referencePhoto).join('')}</div>` : '';
    const added = plan.directions.map(direction => `<article class="pb-result-item"><div class="pb-result-photo">${referencePhoto(direction)}</div>
      <h4>${esc(direction.title)}</h4><p>${esc(direction.reason)}</p></article>`).join('');
    return `<p class="section-eyebrow">${icon('spark')}AI 款式示意</p><h2 class="screen-title">${esc(title)}推荐</h2>${flatlay}
      <section class="pb-result-group"><h3>建议添置 <small>${plan.directions.length} 件</small></h3>
        <div class="pb-grid pb-additions" data-count="${plan.directions.length}">${added}</div></section>
      <section class="pb-result-group"><h3>衣橱已有 <small>${owned.length} 件</small></h3>
        <div class="pb-grid">${owned.map(item => `<div class="pb-item"><span class="pb-item-image">${photo(item)}</span>
          <span class="pb-item-name">${esc(item?.name || item?.type_label || '已有衣物')}</span></div>`).join('')}</div></section>
      ${ready ? '' : '<p class="selection-note" role="status">这组款式图暂时无法读取。</p>'}
      <div class="pb-actions">${action('generate', busy === 'generate' ? '正在提交…' : '再生成', {glyph:'refresh', disabled:Boolean(busy)})}
        ${saved ? '<a class="purchase-primary primary" href="#today">已保存 · 看首页</a>'
          : action('save', busy === 'save' ? '正在保存…' : '我想要', {primary:true, glyph:'bookmark', disabled:!ready || Boolean(busy)})}</div>
      ${saved ? '<p class="selection-note" role="status">建议添置的单品已保存到首页。</p>' : ''}`;
  }

  function progress(savedIds) {
    const plan = selectedPlan();
    if (plan) return resultHtml(plan, savedIds);
    if (!loaded && !pending) return '<p class="pi-analysis-status" role="status">正在读取生成进度…</p>';
    const running = request?.status === 'running' || busy === 'generate';
    const waiting = uncertain();
    if (running || waiting) {
      const title = running ? '正在生成推荐' : '先确认原任务';
      const detail = request?.total ? `款式图已生成 ${request.completed || 0} / ${request.total}` : running ? '正在整理所选衣物与风格' : '查询会继续检查这次生成的结果。';
      return `<div class="pi-empty"><h2>${title}</h2><p class="pi-analysis-status" role="status">${running ? '<span class="pi-analysis-orb"></span>' : ''}${detail}</p>
        ${action('query', querying ? '查询中…' : '查询原任务', {primary:true, glyph:'history', disabled:Boolean(busy)})}
        <button type="button" class="quiet blue home-return" data-purchase-home>收起，稍后再看</button></div>`;
    }
    return `<div class="pi-empty"><h2>这次暂时没有新推荐</h2>
      <p>${esc(request?.error || '可以返回调整衣物和添置形式。')}</p>
      ${request ? action('generate','再生成',{primary:true,glyph:'refresh',disabled:Boolean(busy)}) : action('refresh','重新读取进度',{glyph:'refresh'})}
      ${result ? action('prior','查看上一组推荐') : ''}</div>`;
  }

  return {
    get hasRequest() { return Boolean(request || pending); },
    style(page) {
      const source = page === 'builder-status' ? statusSource() : form;
      return source?.aesthetic?.id || source?.aesthetic;
    },
    get picked() { return form.item_ids.length; },
    refresh,
    open(aesthetic) { setForm({aesthetic, item_ids:[], mode:''}); message=''; showPrior=false; },
    loadWardrobe,
    ensureWardrobe() { if (!wardrobe && !loadingWardrobe) void loadWardrobe(); },
    resume() {
      showPrior = false;
      message = '';
    },
    toggle(id) {
      if (!wardrobe?.some(item => item.id === id)) return;
      const selected = form.item_ids.includes(id);
      if (!selected && form.item_ids.length >= maxSelected) { message = `一次最多选 ${maxSelected} 件衣物。`; update(); return; }
      setForm({...form, item_ids:selected ? form.item_ids.filter(item => item !== id) : [...form.item_ids,id]});
      message = '';
      update();
    },
    choose(mode) {
      if (formats.some(([id]) => id === mode)) { setForm({...form, mode}); message = ''; update(); }
    },
    prior() {
      if (!result) return;
      showPrior = true;
      message = '';
      update();
    },
    submit, query, save,
    html(page, savedIds) {
      const feedback = message ? `<p class="pi-query-feedback" role="status">${esc(message)}</p>` : '';
      return `<div class="purchase-builder">${feedback}${page === 'builder-pick' ? picker()
        : page === 'builder-format' ? formatPicker() : progress(savedIds)}</div>`;
    },
    dispose() { clearTimeout(pollTimer); },
  };
}
