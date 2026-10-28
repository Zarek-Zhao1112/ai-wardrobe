/** §20：完整审美方案→真实衣橱组套；生成时保留上一批结果。 */
import { api } from "../../core/api.js";
import { esc, icon, fmtDateTime, itemChipsHtml, toast, serviceSettingsHtml } from "../../core/ui.js";
import { openTryonSheet } from "./tryon-sheet.js";
import { bindHorizontalSwipe } from "../../components/horizontal-swipe.js";
export const label = "搭配";
const FB_TEXT = { like: "喜欢这套", dislike: "不喜欢", swap: "先不选" };
const STATUS_TEXT = { proposed: "待反馈", accepted: "已采纳", rejected: "未采纳", swapped: "暂未选择" };
const ASSESS_TEXT = { matched: "有依据", approximate: "近似尝试", unknown: "信息不足" };
// 切页再回来时复用尚未结束的请求，避免同一次生成重复调用。
let inFlight = null;
let inFlightRequested = null;
let activeOutfit = null;
// 删除标记跨页面保留，阻止离页前的请求/详情背景快照把已删衣服画回来。
const removedItems = new Set();
document.addEventListener("wardrobe:item-removed", event => { if (event.detail?.id) removedItems.add(event.detail.id); });
export function unmount(root) { if (activeOutfit?.root === root) { activeOutfit.dispose(); activeOutfit = null; } }

function methodDetails(profile, assessment = null) {
  if (!profile) return '<p class="faint">旧版搭配，未记录审美方案。</p>';
  return `<details class="aesthetic-details"><summary>${assessment ? "查看搭配依据" : "查看完整审美方案"}</summary>
    <p>${esc(profile.core)}</p><dl>${(profile.dimensions || []).map(dimension => {
      const actual = assessment?.dimensions?.find(row => row.key === dimension.key);
      return `<div><dt>${esc(dimension.label)}${actual ? `<span class="chip">${esc(ASSESS_TEXT[actual.status] || "待确认")}</span>` : ""}</dt>
        <dd>${esc(dimension.goal)}${actual ? `<p>${esc(actual.evidence)}</p>` : ""}</dd></div>`;
    }).join("")}</dl><p class="faint">${esc(profile.flexible)}</p>
    ${assessment ? `<p class="faint">${esc(assessment.note)}</p>` : ""}
    <div class="aesthetic-sources">${(profile.sources || []).filter(source => /^https:\/\//.test(source.url || "")).map(source => `<a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.title)}</a>`).join("")}</div></details>`;
}

export async function mount(root, ctx, arg = "", restore = null) {
  if (activeOutfit) unmount(activeOutfit.root);
  const anchorId = arg || "";
  if (restore?.anchorId !== anchorId) restore = null;
  let disposed = false, gesture = null, motion = null;
  const taxonomy = ctx.taxonomy || {}, seasons = taxonomy.vocabularies?.season || {};
  root.innerHTML = `<section class="outfit-view">${anchorId ? `<div class="outfit-anchor" data-anchor-id="${esc(anchorId)}"><button type="button" class="outfit-anchor-back" data-anchor-back aria-label="返回这件衣服">${icon("back")}</button><div data-anchor-piece><span class="faint">正在读取必选衣物…</span></div></div>` : ""}<div class="outfit-top-row">
    <div class="outfit-method-slot"><div data-aesthetic-loading class="muted">正在读取审美方案…</div>
      <details id="o-chooser" class="aesthetic-chooser" hidden><summary><strong id="o-current"></strong><span id="o-choice-count"></span></summary>
        <div id="o-aesthetics" class="aesthetic-picker" role="group" aria-label="审美方案"></div></details></div>
    <label class="outfit-season"><span class="sr-only">季节</span><select id="o-season"><option value="">当前季节</option>${Object.entries(seasons).map(([key, name]) => `<option value="${esc(key)}">${esc(name)}</option>`).join("")}</select></label>
    <div class="page-settings status">${serviceSettingsHtml(ctx.health)}</div></div><div data-outfit-content hidden>
      <p id="o-method-summary" class="outfit-method-summary"></p><div id="o-method" hidden></div>
      <div id="o-message" role="status" aria-live="polite"></div>
      <div id="o-result"><div class="outfit-empty">${icon('wardrobe')}<h2>用已有的衣服，<br>搭出新的感觉。</h2><p>选一种审美，再从衣橱里组一套。<br>配色、轮廓与质感一起考虑。</p></div></div>
      <div class="outfit-generate-bar"><button id="o-go" type="button" class="primary">${icon('spark')}从衣橱生成搭配</button><details class="outfit-options"><summary>更多选项</summary><div class="outfit-option-fields"><label class="field"><span>套数</span><select id="o-limit"><option value="3">3 套</option><option value="5">5 套</option></select></label><label class="control-toggle"><input type="checkbox" id="o-llm" checked> AI 比较与说明</label></div></details></div>
      <details class="outfit-history-panel"><summary>最近生成</summary><div id="o-history"><p class="muted">加载中…</p></div></details></div></section>`;
  const container = root.querySelector(".outfit-view"), alive = () => !disposed && container.isConnected;
  activeOutfit = { root, dispose() { disposed = true; gesture?.dispose(); motion?.cancel(); } };
  if (anchorId) {
    container.querySelector("[data-anchor-back]").onclick = () => {
      if (history.state?.outfitAnchorFrom === "#item/" + anchorId) history.back();
      else location.hash = "#item/" + encodeURIComponent(anchorId);
    };
    try {
      const item = await api.getItem(anchorId);
      if (!alive()) return;
      const photo = api.presentationUrl(item);
      container.querySelector("[data-anchor-piece]").innerHTML = `${photo ? `<img src="${esc(photo)}" data-flatlay-source="${esc(photo)}" alt="">` : ""}<div><span>本次必选</span><strong>${esc(item.name || item.type_label || "这件衣服")}</strong></div>`;
    } catch (error) {
      if (alive()) {
        container.querySelector("[data-anchor-piece]").textContent = "这件衣服暂不可用";
        container.querySelector(".outfit-top-row").innerHTML = `<p class="alert error">${esc(error.message || "请返回衣橱重新选择")}</p>`;
      }
      return;
    }
  }
  let catalog;
  try { catalog = await api.aesthetics(); }
  catch (error) {
    if (alive()) {
      const loading = container.querySelector("[data-aesthetic-loading]");
      loading.innerHTML = `<div class="alert error">审美方案读取失败：${esc(error.message)}</div><button type="button" data-retry>重新读取</button>`;
      loading.querySelector("[data-retry]").onclick = () => { if (alive()) void mount(root, ctx, arg, restore); };
    }
    return;
  }
  if (!alive()) return;
  const profiles = catalog.aesthetics || [];
  if (!profiles.length) { container.querySelector("[data-aesthetic-loading]").textContent = "暂时没有可用的审美方案。"; return; }
  container.querySelector("[data-aesthetic-loading]").remove();
  container.querySelector("#o-chooser").hidden = false;
  container.querySelector("[data-outfit-content]").hidden = false;
  const state = { aesthetic: catalog.default || profiles[0].id, season: "", limit: 3, useLlm: true,
    ...(restore?.state || {}), anchorId };
  if (!profiles.some(profile => profile.id === state.aesthetic)) state.aesthetic = catalog.default || profiles[0].id;
  const resultEl = container.querySelector("#o-result"), messageEl = container.querySelector("#o-message");
  const historyEl = container.querySelector("#o-history"), goBtn = container.querySelector("#o-go");
  const includesAnchor = outfit => !anchorId || (outfit.item_ids?.includes(anchorId) && outfit.items?.some(item => item.id === anchorId));
  const usableOutfit = outfit => includesAnchor(outfit) && outfit.item_ids?.length && outfit.item_ids.every(id => !removedItems.has(id) && outfit.items?.some(item => item.id === id));
  let busy = false, historySeq = 0, deck = (restore?.deck || []).filter(usableOutfit), deckIndex = 0, restoreInitialHistory = !anchorId && !restore;
  deckIndex = Math.min(Math.max(0, restore?.deckIndex || 0), Math.max(0, deck.length - 1));
  root.captureState = () => ({ anchorId, state: { ...state }, deck, deckIndex,
    historyOpen: container.querySelector(".outfit-history-panel").open,
    detailsOpen: Boolean(resultEl.querySelector(".outfit-deck-details:not([hidden])")) });
  container.querySelector("#o-season").value = state.season;
  container.querySelector("#o-limit").value = String(state.limit);
  container.querySelector("#o-llm").checked = state.useLlm;
  const goLabel = () => icon(deck.length ? "replay" : "spark") + (deck.length ? "按这个审美，重新搭配" : anchorId ? `用这件生成 ${state.limit} 套搭配` : "从衣橱生成搭配");
  goBtn.innerHTML = goLabel();
  if (anchorId) resultEl.innerHTML = `<div class="outfit-empty outfit-anchor-empty">${icon("outfits")}<h2>围绕这件，<br>搭出新的感觉。</h2><p>选择一种审美，<br>每套都会带上这件衣服。</p></div>`;
  const feedbackPending = new Set();
  root.addEventListener("item:removed", event => {
    if (!alive()) return;
    const removed = event.detail?.id;
    if (anchorId === removed) { location.replace("#wardrobe"); return; }
    deck = deck.filter(outfit => !outfit.item_ids?.includes(removed));
    deckIndex = Math.min(deckIndex, Math.max(0, deck.length - 1));
    if (deck.length) renderDeck();
    else { container.classList.remove('has-deck'); resultEl.innerHTML = '<div class="outfit-empty"><p>衣橱已更新，可以重新搭配。</p></div>'; goBtn.innerHTML = goLabel(); }
    void loadHistory();
  });
  function selectMethod() {
    const selected = profiles.find(profile => profile.id === state.aesthetic);
    container.querySelector("#o-current").textContent = selected?.name || "选择审美";
    container.querySelector("#o-choice-count").textContent = "⌄";
    container.querySelector("#o-method-summary").textContent = selected?.summary || "";
    container.querySelector("#o-aesthetics").innerHTML = profiles.map(profile => `<button type="button" class="aesthetic-choice" data-aesthetic="${esc(profile.id)}" aria-pressed="${state.aesthetic === profile.id}"><strong>${esc(profile.name)}</strong><span>${esc(profile.summary)}</span></button>`).join("");
    container.querySelector("#o-method").innerHTML = methodDetails(selected);
  }
  selectMethod();
  container.querySelector("#o-aesthetics").onclick = event => {
    const button = event.target.closest("[data-aesthetic]");
    if (!button) return;
    state.aesthetic = button.dataset.aesthetic; selectMethod();
    container.querySelector("#o-chooser").open = false;
    container.querySelector("#o-chooser > summary").focus();
  };
  container.querySelector("#o-season").onchange = event => { state.season = event.target.value; };
  container.querySelector("#o-limit").onchange = event => { state.limit = Number(event.target.value); if (!busy && !inFlight) goBtn.innerHTML = goLabel(); };
  container.querySelector("#o-llm").onchange = event => { state.useLlm = event.target.checked; };

  function outfitCardHtml(outfit, index) {
    const profile = outfit.aesthetic;
    const items = outfit.items || [];
    const slotOf = item => item.slot || taxonomy.types?.[item.type]?.slot || 'accessory';
    // 衣物分列、鞋饰另起一行；图片与点击区域都不互相叠压。
    const columns = [items.filter(item => ['top', 'outer'].includes(slotOf(item))), items.filter(item => ['bottom', 'full'].includes(slotOf(item)))].filter(column => column.length);
    const extras = items.filter(item => !['top', 'outer', 'bottom', 'full'].includes(slotOf(item)));
    const piecesHtml = rows => rows.map(item => {
      const url = api.presentationUrl(item), name = item.name || item.type_label || '未命名';
      return `<a class="outfit-piece piece-${esc(slotOf(item))}${item.id === anchorId ? ' is-anchor' : ''}" href="#item/${esc(item.id)}" aria-label="查看${esc(name)}">${url ? `<img src="${esc(url)}" data-flatlay-source="${esc(url)}" alt="${esc(name)}">` : `<span class="ph">${esc(item.type_label || '无照片')}</span>`}${item.id === anchorId ? '<span class="outfit-piece-anchor">必选</span>' : ''}</a>`;
    }).join('');
    const palette = { black: '#292929', white: '#eee', gray: '#c9c9c5', navy: '#293247', blue: '#8cabc8', light_blue: '#c5d9eb', khaki: '#baa078', beige: '#dfd6c4', brown: '#82634f', burgundy: '#702e3c', green: '#8b9f83', army_green: '#7e8863', red: '#ae4949', yellow: '#dbc371', pink: '#dba8b7', purple: '#a18db8', orange: '#c58c5d', silver: '#aeb8c1', gold: '#bfac71' };
    const colors = [...new Set(items.map(item => item.primary_color).filter(color => palette[color]))].slice(0, 4);
    const labels = colors.map(color => typeof taxonomy.vocabularies?.color?.[color] === 'string' ? taxonomy.vocabularies.color[color] : ({ black: '黑', white: '白', gray: '灰', navy: '藏蓝', blue: '蓝', light_blue: '浅蓝', khaki: '卡其', beige: '米色', brown: '棕', burgundy: '酒红' }[color] || '')) .filter(Boolean);
    return `<article class="outfit" data-id="${esc(outfit.id)}"><div class="outfit-stage"><div class="outfit-stage-art" aria-label="这套搭配中的单品，可左右滑动换套"><span class="outfit-stage-count"><span>${index + 1} / ${deck.length} · ${esc(profile?.name || '来自你的衣橱')}</span>${deck.length > 1 ? '<small aria-hidden="true">↔ 左右滑动换套</small>' : ''}</span>
      ${columns.length ? `<div class="outfit-stage-main ${columns.length === 1 ? 'is-single' : ''}">${columns.map(column => `<div class="outfit-stage-column">${piecesHtml(column)}</div>`).join('')}</div>` : ''}
      ${extras.length ? `<div class="outfit-stage-extras">${piecesHtml(extras)}</div>` : ''}</div><div class="outfit-stage-copy"><h2>${esc(labels.slice(0, 2).join('与') || profile?.name || '这套搭配')}${labels.length ? '，' + esc(profile?.name || '自然搭配') + '。' : ''}</h2>
      ${outfit.reason ? `<p class="outfit-reason">${esc(outfit.reason)}</p>` : `<p class="outfit-reason faint">按${esc(profile?.name || "本地规则")}选衣，具体依据见下方。</p>`}
      <div class="outfit-color-row">${colors.map(color => `<i style="background:${palette[color]}"></i>`).join('')}<span>${esc(labels.join(' × '))}</span></div></div></div>
      <div class="outfit-deck-actions"><button type="button" class="primary" data-tryon>${icon('camera')}实时试衣</button><button type="button" class="outfit-detail-toggle" data-outfit-details aria-expanded="false" aria-label="查看搭配依据与反馈">${icon('info')}</button></div>
      <div class="outfit-pagination"><div class="outfit-dots" role="group" aria-label="选择搭配">${deck.map((_, position) => `<button type="button" data-outfit-index="${position}" aria-pressed="${position === index}" aria-label="查看第${position + 1}套搭配"><i></i></button>`).join('')}</div><button type="button" data-outfit-next class="outfit-next" ${deck.length < 2 ? 'disabled' : ''}>换一套看看${icon('right')}</button></div>
      <div class="outfit-deck-details" hidden><div class="chips">${itemChipsHtml(outfit.items, taxonomy)}</div>
      ${methodDetails(profile, profile?.assessment)}
      <div class="form-actions">${Object.entries(FB_TEXT).map(([key, text]) => { const chosen = (outfit._feedback || ({ accepted: 'like', rejected: 'dislike', swapped: 'swap' }[outfit.status])) === key; return `<button type="button" data-fb="${key}" class="${chosen ? 'chosen' : ''}" aria-pressed="${chosen}" ${feedbackPending.has(outfit.id) ? 'disabled' : ''}>${esc(text)}</button>`; }).join("")}</div></div></article>`;
  }
  function renderDeck(focus = false) {
    gesture?.cancel(); motion?.cancel(); motion = null;
    container.classList.toggle('has-deck', !!deck.length);
    if (!deck.length) return;
    if (!busy) goBtn.innerHTML = icon("replay") + "按这个审美，重新搭配";
    resultEl.innerHTML = outfitCardHtml(deck[deckIndex], deckIndex);
    if (focus) resultEl.querySelector(`[data-outfit-index="${deckIndex}"]`)?.focus({ preventScroll: true });
  }
  function chooseOutfit(position, focus = false) {
    if (!alive() || !deck.length) return;
    const next = (position + deck.length) % deck.length;
    if (next === deckIndex) return;
    const direction = position < deckIndex ? -1 : 1;
    deckIndex = next; renderDeck(focus);
    const card = resultEl.querySelector('.outfit');
    if (card?.animate && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      motion = card.animate([{ opacity: .35, transform: `translateX(${direction * 18}px)` }, { opacity: 1, transform: 'none' }], { duration: 270, easing: 'cubic-bezier(.2,.7,.2,1)' });
    }
  }
  gesture = bindHorizontalSwipe(resultEl, {
    selector: '.outfit-stage-art', enabled: () => alive() && deck.length > 1,
    onSwipe: direction => chooseOutfit(deckIndex + direction),
    onDrag: (node, offset) => { node.style.transform = offset ? `translateX(${offset}px)` : ''; },
  });
  function renderResult(response, requested) {
    if (requested.anchorId !== anchorId) return;
    if ((response.outfits || []).some(outfit => !includesAnchor(outfit))) throw new Error("返回的搭配未包含必选衣物，请重新生成");
    restoreInitialHistory = false;
    const outfits = (response.outfits || []).filter(usableOutfit);
    const name = response.aesthetic?.name || profiles.find(profile => profile.id === requested.aesthetic)?.name || "审美方案";
    if (!outfits.length) { deck = []; deckIndex = 0; container.classList.remove('has-deck'); resultEl.innerHTML = `<div class="alert">${esc(response.note || "暂未找到可用搭配")} <a href="#add">去录衣服 →</a></div>`; return; }
    const note = response.degraded ? "AI 比较暂不可用，以下仍按所选审美方案在本地组套。" : "";
    deck = outfits; deckIndex = 0; renderDeck();
    if (note) messageEl.textContent = note;
  }
  async function generate() {
    if (busy || inFlight) return;
    const requested = { ...state };
    busy = true; goBtn.disabled = true; goBtn.textContent = "正在组搭…";
    messageEl.className = "muted";
    messageEl.textContent = `正在按「${profiles.find(profile => profile.id === requested.aesthetic)?.name || "所选审美"}」从衣橱挑选…`;
    const pending = api.recommend({ aesthetic: requested.aesthetic, season: requested.season, limit: requested.limit, use_llm: requested.useLlm ? "true" : "false", anchor_item_id: anchorId || undefined });
    inFlight = pending;
    inFlightRequested = requested;
    try {
      const response = await pending;
      if (!alive()) return;
      renderResult(response, requested); messageEl.textContent = response.degraded ? "AI 比较暂不可用，以下按所选审美在本地组套。" : ""; void loadHistory();
    } catch (error) {
      if (!alive()) return;
      messageEl.className = "alert error";
      messageEl.textContent = error.code === "request_timeout" ? "暂未收到生成结果，后台可能已经生成。请先查看最近生成，已有结果保留。" : `这次生成未成功：${error.message}。已有搭配保留。`;
      void loadHistory();
    } finally {
      if (inFlight === pending) { inFlight = null; inFlightRequested = null; }
      busy = false;
      if (alive()) { goBtn.disabled = false; goBtn.innerHTML = goLabel(); }
    }
  }
  goBtn.onclick = generate;
  resultEl.onclick = async event => {
    const next = event.target.closest('[data-outfit-next]'), position = event.target.closest('[data-outfit-index]');
    if (next || position) { chooseOutfit(position ? Number(position.dataset.outfitIndex) : deckIndex + 1, true); return; }
    const detail = event.target.closest('[data-outfit-details]');
    if (detail) { const body = detail.closest('.outfit').querySelector('.outfit-deck-details'); body.hidden = !body.hidden; detail.setAttribute('aria-expanded', String(!body.hidden)); return; }
    const trial = event.target.closest('[data-tryon]');
    if (trial) { const trialDeck = deck; openTryonSheet({ root, anchor: trial, outfits: trialDeck, index: deckIndex, onSelect: index => { if (alive() && deck === trialDeck) { deckIndex = index; renderDeck(); } } }); return; }
    const button = event.target.closest("[data-fb]"), card = button?.closest(".outfit");
    if (!card || button.disabled || feedbackPending.has(card.dataset.id)) return;
    feedbackPending.add(card.dataset.id);
    const buttons = [...card.querySelectorAll("[data-fb]")]; buttons.forEach(node => { node.disabled = true; });
    try {
      const response = await api.feedback({ outfit_id: card.dataset.id, feedback_type: button.dataset.fb });
      const recorded = deck.find(outfit => outfit.id === card.dataset.id); if (recorded) recorded._feedback = button.dataset.fb;
      if (!alive()) return;
      const currentCard = [...resultEl.querySelectorAll('.outfit')].find(node => node.dataset.id === card.dataset.id);
      currentCard?.querySelectorAll('[data-fb]').forEach(node => { const chosen = node.dataset.fb === button.dataset.fb; node.classList.toggle('chosen', chosen); node.setAttribute('aria-pressed', String(chosen)); });
      toast(response.duplicate ? "这条反馈已经记过了" : `已记下「${FB_TEXT[button.dataset.fb]}」`, "ok");
      buttons.forEach(node => { node.classList.toggle("chosen", node === button); node.setAttribute("aria-pressed", String(node === button)); });
      await loadHistory();
    } catch (error) { if (alive()) toast("反馈未成功：" + error.message, "error"); }
    finally { feedbackPending.delete(card.dataset.id); buttons.forEach(node => { node.disabled = false; }); if (alive()) [...resultEl.querySelectorAll('.outfit')].find(node => node.dataset.id === card.dataset.id)?.querySelectorAll('[data-fb]').forEach(node => { node.disabled = false; }); }
  };
  async function loadHistory() {
    const seq = ++historySeq;
    try {
      const data = await api.listOutfits(20);
      if (!alive() || seq !== historySeq) return;
      const rows = (data.outfits || []).filter(usableOutfit);
      if (restoreInitialHistory) { restoreInitialHistory = false; if (!deck.length && rows.some(row => row.items?.length)) { deck = rows.filter(row => row.items?.length).slice(0, 5); deckIndex = 0; renderDeck(); } }
      historyEl.innerHTML = rows.length ? `<ul class="hist">${rows.map(outfit => `<li class="aesthetic-history"><div class="hist-row"><span class="faint">${esc(fmtDateTime(outfit.created_at))}</span><span>${esc(outfit.aesthetic?.name || "旧版搭配")}${outfit.aesthetic ? ` · v${esc(outfit.aesthetic.version)}` : ""}</span><span class="chip">${esc(STATUS_TEXT[outfit.status] || outfit.status || "")}</span></div><div class="chips">${itemChipsHtml(outfit.items, taxonomy)}</div>${methodDetails(outfit.aesthetic, outfit.aesthetic?.assessment)}</li>`).join("")}</ul>` : '<p class="faint">还没有生成过搭配。</p>';
    } catch (error) {
      if (alive() && seq === historySeq) historyEl.innerHTML = `<p class="alert error">最近生成读取失败：${esc(error.message)}</p><button type="button" data-history-retry>重新读取</button>`;
      historyEl.querySelector("[data-history-retry]")?.addEventListener("click", loadHistory);
    }
  }
  if (deck.length) {
    renderDeck();
    if (restore?.detailsOpen) { resultEl.querySelector(".outfit-deck-details").hidden = false; resultEl.querySelector("[data-outfit-details]").setAttribute("aria-expanded", "true"); }
  }
  if (restore?.historyOpen) container.querySelector(".outfit-history-panel").open = true;
  if (inFlight) {
    const waiting = inFlight, requested = inFlightRequested || { ...state };
    goBtn.disabled = true; messageEl.textContent = "上一轮搭配仍在生成，结束后可查看最近生成。";
    void waiting.then(response => { if (alive()) { if (requested.anchorId === anchorId) { renderResult(response, requested); messageEl.textContent = response.degraded ? "AI 比较暂不可用，以下按所选审美在本地组套。" : ""; } else messageEl.textContent = "可以围绕当前衣服生成搭配了。"; void loadHistory(); } }).catch(() => { if (alive()) messageEl.textContent = "上一轮生成未成功，当前选择已保留。"; }).finally(() => {
      if (alive()) { goBtn.disabled = false; goBtn.innerHTML = goLabel(); }
    });
  }
  await loadHistory();
}
