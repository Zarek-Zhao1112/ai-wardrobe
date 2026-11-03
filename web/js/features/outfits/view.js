/** §20：完整审美方案→真实衣橱组套；生成时保留上一批结果。 */
import { api } from "../../core/api.js";
import { esc, icon, fmtDateTime, itemChipsHtml, toast } from "../../core/ui.js";
import { openTryonSheet } from "./tryon-sheet.js";
import { bindHorizontalSwipe } from "../../components/horizontal-swipe.js";
import { createAestheticSheet } from "./aesthetic-sheet.js";
import { profileCardHtml, methodDetails } from "./aesthetic-ui.js";
export const label = "搭配";
const FB_TEXT = { like: "喜欢这套", dislike: "不喜欢", swap: "先不选" };
const STATUS_TEXT = { proposed: "待反馈", accepted: "已采纳", rejected: "未采纳", swapped: "暂未选择" };
let rememberedView = null;
// 同一搭配的反馈跨mount串行，旧请求结束前不能由新页面重复提交。
const feedbackPending = new Map();
// 切页再回来时复用尚未结束的请求，避免同一次生成重复调用。
let inFlight = null;
let inFlightRequested = null;
let activeOutfit = null;
// 删除标记跨页面保留，阻止离页前的请求/详情背景快照把已删衣服画回来。
const removedItems = new Set();
document.addEventListener("wardrobe:item-removed", event => { if (event.detail?.id) removedItems.add(event.detail.id); });
export function unmount(root) { if (activeOutfit?.root === root) { rememberedView = root.captureState?.() || rememberedView; activeOutfit.dispose(); activeOutfit = null; } }

export async function mount(root, ctx, arg = "", restore = null) {
  if (activeOutfit) unmount(activeOutfit.root);
  const anchorId = arg || "";
  if (!restore && rememberedView?.anchorId === anchorId) restore = rememberedView;
  if (restore?.anchorId !== anchorId) restore = null;
  let disposed = false, gesture = null, motion = null, sheet = null;
  const taxonomy = ctx.taxonomy || {};
  root.innerHTML = `<section class="outfit-view">${anchorId ? `<div class="outfit-anchor" data-anchor-id="${esc(anchorId)}"><button type="button" class="outfit-anchor-back" data-anchor-back aria-label="返回这件衣服">${icon("back")}</button><div data-anchor-piece><span class="faint">正在读取必选衣物…</span></div></div>` : ""}<div class="outfit-top-row">
    <div class="outfit-method-slot"><div data-aesthetic-loading class="muted">正在读取审美方案…</div><button id="o-chooser" type="button" data-open-aesthetics class="aesthetic-selector" hidden>${icon("spark")}<strong id="o-current"></strong><span aria-hidden="true">⌄</span></button></div>
    <div id="o-season" class="outfit-season" aria-label="当前季节"></div></div><div data-outfit-content hidden>
      <div id="o-intro"></div><p id="o-method-summary" class="outfit-method-summary" hidden></p>
      <div id="o-message" role="status" aria-live="polite"></div><div id="o-result"></div>
      <div class="outfit-generate-bar"><button id="o-go" type="button" class="primary">${icon("spark")}从衣橱生成搭配</button><button type="button" id="o-options" class="outfit-options-button">3 套 · 更多选项${icon("chevron")}</button><span class="outfit-generate-note">用已有衣服，按当前季节搭配</span></div>
      <div id="o-recent"></div><details class="outfit-history-panel"><summary>最近搭配</summary><div id="o-history"><p class="muted">加载中…</p></div></details></div></section>`;
  const container = root.querySelector(".outfit-view"), alive = () => !disposed && container.isConnected;
  activeOutfit = { root, dispose() { disposed = true; gesture?.dispose(); motion?.cancel(); sheet?.dispose(); document.removeEventListener("visibilitychange", refreshSeason); document.removeEventListener("outfits:feedback", feedbackChanged); } };
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
  state.season = ""; // 当前季节由服务端确定，旧快照的季节选择不再沿用。
  if (!profiles.some(profile => profile.id === state.aesthetic)) state.aesthetic = catalog.default || profiles[0].id;
  const resultEl = container.querySelector("#o-result"), messageEl = container.querySelector("#o-message");
  const historyEl = container.querySelector("#o-history"), goBtn = container.querySelector("#o-go");
  const includesAnchor = outfit => !anchorId || (outfit.item_ids?.includes(anchorId) && outfit.items?.some(item => item.id === anchorId));
  const usableOutfit = outfit => includesAnchor(outfit) && outfit.item_ids?.length && outfit.item_ids.every(id => !removedItems.has(id) && outfit.items?.some(item => item.id === id));
  let busy = false, historySeq = 0, historyRows = [], deck = (restore?.deck || []).filter(usableOutfit), deckIndex = 0;
  deckIndex = Math.min(Math.max(0, restore?.deckIndex || 0), Math.max(0, deck.length - 1));
  root.captureState = () => ({ anchorId, state: { ...state }, deck, deckIndex,
    historyOpen: container.querySelector(".outfit-history-panel").open });
  function showSeason(data) {
    if (!alive()) return;
    const label = data.season_label || "当前季节";
    container.querySelector("#o-season").innerHTML = `${icon("leaf")}<span>${esc(label)}</span>`;
  }
  showSeason(catalog);
  function refreshSeason() {
    if (!document.hidden && alive()) void api.aesthetics().then(showSeason).catch(() => {});
  }
  document.addEventListener("visibilitychange", refreshSeason);
  const goLabel = () => {
    const name = profiles.find(profile => profile.id === state.aesthetic)?.name || "当前审美";
    return icon(deck.length ? "replay" : "spark") + (deck.length ? (deck[deckIndex]?.aesthetic?.id !== state.aesthetic ? `用${name}重新搭配` : "按这个审美，重新搭配") : anchorId ? `用这件生成 ${state.limit} 套搭配` : "从衣橱生成搭配");
  };
  goBtn.innerHTML = goLabel();
  sheet = createAestheticSheet({ root, profiles, getSettings: () => ({ ...state }),
    onSettings: patch => { if (!alive()) return; Object.assign(state, patch); selectMethod(); },
    onSelect: id => { if (alive()) { state.aesthetic = id; selectMethod(); } },
    onGenerate: () => { if (alive()) void generate(); },
    onFeedback: (outfit, kind) => submitFeedback(outfit, kind),
    isFeedbackPending: id => feedbackPending.has(id) });
  document.addEventListener("outfits:feedback", feedbackChanged);
  root.addEventListener("item:removed", event => {
    if (!alive()) return;
    const removed = event.detail?.id; sheet?.itemRemoved?.(removed);
    if (anchorId === removed) { location.replace("#wardrobe"); return; }
    deck = deck.filter(outfit => !outfit.item_ids?.includes(removed));
    deckIndex = Math.min(deckIndex, Math.max(0, deck.length - 1));
    if (deck.length) renderDeck();
    else { container.classList.remove('has-deck'); resultEl.innerHTML = '<div class="outfit-empty"><p>衣橱已更新，可以重新搭配。</p></div>'; goBtn.innerHTML = goLabel(); }
    void loadHistory();
  });
  function methodNotice() {
    const note = container.querySelector("#o-method-summary"), result = deck[deckIndex];
    const changed = result && result.aesthetic?.id !== state.aesthetic;
    note.hidden = !changed;
    note.textContent = changed ? `上方已选「${profiles.find(p => p.id === state.aesthetic)?.name || "当前审美"}」，重新搭配后会应用。` : "";
  }
  function selectMethod() {
    const selected = profiles.find(profile => profile.id === state.aesthetic);
    container.querySelector("#o-current").textContent = selected?.name || "选择审美";
    container.querySelector("#o-intro").innerHTML = selected ? profileCardHtml(selected) : "";
    container.querySelector("#o-options").innerHTML = `${deck.length ? "" : state.limit + " 套 · "}更多选项${icon("chevron")}`;
    methodNotice();
    if (!busy && !inFlight) goBtn.innerHTML = goLabel();
  }
  selectMethod();
  container.querySelector("#o-chooser").onclick = event => sheet.openSelector(event.currentTarget);
  container.querySelector("#o-options").onclick = event => sheet.openOptions(event.currentTarget);
  container.querySelector("#o-intro").onclick = event => {
    const guide = event.target.closest("[data-aesthetic-guide]");
    if (guide) sheet.openGuide(profiles.find(row => row.id === state.aesthetic), guide);
  };

  function outfitCardHtml(outfit, index) {
    const profile = outfit.aesthetic;
    const items = outfit.items || [];
    const liked = (outfit._feedback || ({ accepted: "like", rejected: "dislike", swapped: "swap" }[outfit.status])) === "like";
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
    return `<div class="outfit-result-guide"><span>${esc(profile?.name || "历史搭配")} · ${deck.length} 套搭配</span>${profile ? `<button type="button" data-outfit-guide>${icon("photo")}查看审美方案</button>` : ""}</div><article class="outfit" data-id="${esc(outfit.id)}"><div class="outfit-stage"><div class="outfit-stage-art" aria-label="这套搭配中的单品，可左右滑动换套"><span class="outfit-stage-count"><span>${index + 1} / ${deck.length} · ${esc(profile?.name || '来自你的衣橱')}</span>${deck.length > 1 ? '<small aria-hidden="true">↔ 左右滑动换套</small>' : ''}</span>
      ${columns.length ? `<div class="outfit-stage-main ${columns.length === 1 ? 'is-single' : ''}">${columns.map(column => `<div class="outfit-stage-column">${piecesHtml(column)}</div>`).join('')}</div>` : ''}
      ${extras.length ? `<div class="outfit-stage-extras">${piecesHtml(extras)}</div>` : ''}</div><div class="outfit-stage-copy"><div class="outfit-stage-label"><span>${esc(profile?.name || "历史搭配")} · ${items.length} 件衣物</span><button type="button" class="outfit-like" data-fb="like" data-outfit-favorite aria-label="${liked ? "已喜欢这套" : "喜欢这套"}" title="${liked ? "已喜欢这套" : "喜欢这套"}" aria-pressed="${liked}" ${feedbackPending.has(outfit.id) ? "disabled" : ""}>${icon("bookmark")}</button></div><h2>${esc(labels.slice(0, 2).join('与') || profile?.name || '这套搭配')}${labels.length ? '，' + esc(profile?.name || '自然搭配') + '。' : ''}</h2>
      ${outfit.reason ? `<p class="outfit-reason">${esc(outfit.reason)}</p>` : `<p class="outfit-reason faint">按${esc(profile?.name || "本地规则")}选衣，具体依据见下方。</p>`}
      <div class="outfit-color-row">${colors.map(color => `<i style="background:${palette[color]}"></i>`).join('')}<span>${esc(labels.join(' × '))}</span></div></div></div>
      <div class="outfit-deck-actions"><button type="button" class="primary" data-tryon>${icon('camera')}实时试衣</button><button type="button" class="outfit-detail-toggle" data-outfit-details aria-expanded="false" aria-label="查看搭配依据与反馈">${icon('info')}</button></div>
      <div class="outfit-pagination"><div class="outfit-dots" role="group" aria-label="选择搭配">${deck.map((_, position) => `<button type="button" data-outfit-index="${position}" aria-pressed="${position === index}" aria-label="查看第${position + 1}套搭配"><i></i></button>`).join('')}</div><button type="button" data-outfit-next class="outfit-next" ${deck.length < 2 ? 'disabled' : ''}>换一套看看${icon('right')}</button></div>
      </article>`;
  }
  function renderDeck(focus = false) {
    gesture?.cancel(); motion?.cancel(); motion = null;
    container.classList.toggle('has-deck', !!deck.length);
    if (!deck.length) return;
    if (!busy) goBtn.innerHTML = goLabel();
    methodNotice();
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
    showSeason(response);
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
    if (detail) { sheet.openBasis(deck[deckIndex], detail); return; }
    const guide = event.target.closest('[data-outfit-guide]');
    if (guide) { sheet.openGuide(deck[deckIndex]?.aesthetic, guide); return; }
    const trial = event.target.closest('[data-tryon]');
    if (trial) { const trialDeck = deck; openTryonSheet({ root, anchor: trial, outfits: trialDeck, index: deckIndex, onSelect: index => { if (alive() && deck === trialDeck) { deckIndex = index; renderDeck(); } } }); return; }
    const button = event.target.closest("[data-fb]"), card = button?.closest(".outfit");
    const outfit = card && deck.find(row => row.id === card.dataset.id);
    if (outfit && !button.disabled) {
      try { await submitFeedback(outfit, button.dataset.fb); }
      catch (error) { if (alive()) toast("反馈未成功：" + error.message, "error"); }
    }
  };
  function feedbackChanged(event) {
    if (!alive()) return;
    const { id, kind } = event.detail;
    if (kind) {
      for (const outfit of [...deck, ...historyRows]) if (outfit.id === id) outfit._feedback = kind;
      void loadHistory();
    }
    const card = [...resultEl.querySelectorAll(".outfit")].find(row => row.dataset.id === id);
    card?.querySelectorAll("[data-fb]").forEach(node => {
      node.disabled = feedbackPending.has(id);
      if (!kind) return;
      const chosen = node.dataset.fb === kind; node.classList.toggle("chosen", chosen); node.setAttribute("aria-pressed", String(chosen));
      if (node.hasAttribute("data-outfit-favorite")) { node.setAttribute("aria-label", kind === "like" ? "已喜欢这套" : "喜欢这套"); node.title = node.getAttribute("aria-label"); }
    });
  }
  async function submitFeedback(outfit, kind) {
    if (!outfit) return;
    if (feedbackPending.has(outfit.id)) return feedbackPending.get(outfit.id);
    const notify = type => document.dispatchEvent(new CustomEvent("outfits:feedback", { detail: { id: outfit.id, kind: type } }));
    const pending = api.feedback({ outfit_id: outfit.id, feedback_type: kind }).then(response => {
      const actual = ({ accepted: "like", rejected: "dislike", swapped: "swap" }[response.outfit_status]) || kind;
      outfit._feedback = actual; notify(actual);
      if (alive()) { toast(response.duplicate ? "已更新当前选择，偏好记录未重复累加" : `已记下「${FB_TEXT[actual]}」`, "ok"); }
      return response;
    }).finally(() => {
      if (feedbackPending.get(outfit.id) === pending) { feedbackPending.delete(outfit.id); notify(); }
    });
    feedbackPending.set(outfit.id, pending); notify();
    return pending;
  }
  function restoreHistory(event) {
    const button = event.target.closest("[data-restore-outfit]"); if (!button) return;
    const outfit = historyRows.find(row => row.id === button.dataset.restoreOutfit);
    if (!outfit || !usableOutfit(outfit)) return;
    deck = [outfit]; deckIndex = 0; renderDeck(true);
    resultEl.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }
  historyEl.onclick = restoreHistory; container.querySelector("#o-recent").onclick = restoreHistory;
  async function loadHistory() {
    const seq = ++historySeq;
    try {
      const data = await api.listOutfits(20);
      if (!alive() || seq !== historySeq) return;
      const rows = (data.outfits || []).filter(usableOutfit); historyRows = rows;
      const recent = rows[0];
      container.querySelector("#o-recent").innerHTML = recent ? `<button type="button" class="outfit-recent-card" data-restore-outfit="${esc(recent.id)}"><span class="outfit-recent-images">${(recent.items || []).slice(0, 3).map(item => { const url = api.presentationUrl(item); return url ? `<img src="${esc(url)}" data-flatlay-source="${esc(url)}" alt="${esc(item.name || item.type_label)}">` : ""; }).join("")}</span><span><strong>${esc(recent.aesthetic?.name || "历史搭配")}</strong><small>${recent.items?.length || 0} 件衣物 · 回看这套</small></span>${icon("right")}</button>` : "";
      historyEl.innerHTML = rows.length ? `<ul class="hist">${rows.map(outfit => `<li class="aesthetic-history"><div class="hist-row"><span class="faint">${esc(fmtDateTime(outfit.created_at))}</span><span>${esc(outfit.aesthetic?.name || "旧版搭配")}${outfit.aesthetic ? ` · v${esc(outfit.aesthetic.version)}` : ""}</span><span class="chip">${esc(STATUS_TEXT[outfit.status] || outfit.status || "")}</span></div><button type="button" class="outfit-history-open" data-restore-outfit="${esc(outfit.id)}">回看这套搭配${icon("right")}</button><div class="chips">${itemChipsHtml(outfit.items, taxonomy)}</div>${methodDetails(outfit.aesthetic, outfit.aesthetic?.assessment)}</li>`).join("")}</ul>` : '<p class="faint">还没有生成过搭配。</p>';
    } catch (error) {
      if (alive() && seq === historySeq) historyEl.innerHTML = `<p class="alert error">最近生成读取失败：${esc(error.message)}</p><button type="button" data-history-retry>重新读取</button>`;
      historyEl.querySelector("[data-history-retry]")?.addEventListener("click", loadHistory);
    }
  }
  if (deck.length) {
    renderDeck();

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
