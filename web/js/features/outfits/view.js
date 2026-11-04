/** §40：搭配页两层 —— 第一层一屏一张审美卡（点卡即生成），第二层玻璃面板里堆叠搭配。
 *
 * 为什么不再有「从衣橱生成搭配」按钮：§20 那张长页面在 390×844 上量过，
 * 审美区 + 完整结果卡 + 底栏 = 1007px，可用只有 712px，**任何组合都装不下**。
 * 所以生成动作挂到审美卡本身（点卡＝按这个审美生成），结果进玻璃面板，
 * 第一层纵向滚动严格为 0。
 *
 * 面板由 aesthetic-sheet.js 提供（它同时管 guide/basis/more 三帧），
 * **deck 数据留在这里** —— 面板只负责画，滑到第几张、生成到哪一步都以本文件为准。
 */
import { api } from "../../core/api.js";
import { esc, icon, fmtDateTime, itemChipsHtml, toast } from "../../core/ui.js";
import { openTryonSheet } from "./tryon-sheet.js";
import { createAestheticSheet } from "./aesthetic-sheet.js";
import { aestheticCardHtml, aestheticDotsHtml, methodDetails } from "./aesthetic-ui.js";
import { tighten } from "./tight.js";

export const label = "搭配";

const FB_TEXT = { like: "喜欢这套", dislike: "不喜欢", swap: "先不选" };
const STATUS_TEXT = { proposed: "待反馈", accepted: "已采纳", rejected: "未采纳", swapped: "暂未选择" };
// 颜色名 → 色值。只用于结果卡上那几个小圆点，判的是 20 个颜色**名字**。
const PALETTE = { black: "#292929", white: "#eee", gray: "#c9c9c5", navy: "#293247", blue: "#8cabc8", light_blue: "#c5d9eb", khaki: "#baa078", beige: "#dfd6c4", brown: "#82634f", burgundy: "#702e3c", green: "#8b9f83", army_green: "#7e8863", red: "#ae4949", yellow: "#dbc371", pink: "#dba8b7", purple: "#a18db8", orange: "#c58c5d", silver: "#aeb8c1", gold: "#bfac71" };
const COLOR_LABEL = { black: "黑", white: "白", gray: "灰", navy: "藏蓝", blue: "蓝", light_blue: "浅蓝", khaki: "卡其", beige: "米色", brown: "棕", burgundy: "酒红" };

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
  let disposed = false, sheet = null;
  const taxonomy = ctx.taxonomy || {};
  const alive = () => !disposed && root.isConnected;

  root.innerHTML = `<section class="outfit-view">${anchorId ? `<div class="outfit-anchor" data-anchor-id="${esc(anchorId)}"><button type="button" class="outfit-anchor-back" data-anchor-back aria-label="返回这件衣服">${icon("back")}</button><div data-anchor-piece><span class="faint">正在读取必选衣物…</span></div></div>` : ""}<div class="outfit-season-line"><span data-season><span class="faint">正在读取审美方案…</span></span><span class="outfit-season-count" data-season-count></span></div>
    <div class="outfit-aesthetic-deck" data-aesthetic-deck aria-label="审美方案，左右滑动切换"><div class="outfit-aesthetic-card is-loading"></div></div>
    <div class="outfit-aesthetic-dots" data-aesthetic-dots role="group" aria-label="切换审美方案"></div></section>`;
  const container = root.querySelector(".outfit-view");
  const deckEl = container.querySelector("[data-aesthetic-deck]");
  const dotsEl = container.querySelector("[data-aesthetic-dots]");
  const seasonEl = container.querySelector("[data-season]");
  const countEl = container.querySelector("[data-season-count]");
  // ★ CSS 里**不能**用 100dvh 算这个高度：部分 Android WebView 把 `100dvh` 解析成 0px，
  //   calc 出负数后 height 退回 auto，「一屏一张」直接失效（实测小米 12S Ultra 多出 89px 滚动）。
  //   这里把实测视口高度写成变量，供 outfits-deck.css 的 calc 使用。
  const syncViewport = () => {
    container.style.setProperty("--outfit-vh", (window.visualViewport?.height || window.innerHeight) + "px");
  };
  syncViewport();
  window.addEventListener("resize", syncViewport);
  window.visualViewport?.addEventListener("resize", syncViewport);
  // 配件图加载失败时换成占位文字。`error` 不冒泡，只能在捕获阶段接；
  // 不接的话浏览器会渲染 alt 文字 —— 它是个 `<a>` 里的 img，会被染成蓝色带下划线的
  // 链接样子，看着像坏了（真机偶发过一次：图片本身 200，但请求被瞬断）。
  const imageFailed = event => {
    const img = event.target;
    if (!(img instanceof HTMLImageElement) || !img.closest(".outfit-stack-piece")) return;
    const ph = document.createElement("span");
    ph.className = "ph";
    ph.textContent = "图片暂时打不开";
    img.replaceWith(ph);
  };
  document.addEventListener("error", imageFailed, true);
  activeOutfit = { root, dispose() {
    disposed = true;
    window.removeEventListener("resize", syncViewport);
    window.visualViewport?.removeEventListener("resize", syncViewport);
    document.removeEventListener("error", imageFailed, true);
    sheet?.dispose();
    document.removeEventListener("visibilitychange", refreshSeason);
    document.removeEventListener("outfits:feedback", feedbackChanged);
  } };
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
        deckEl.innerHTML = `<div class="outfit-deck-empty"><p>${esc(error.message || "请返回衣橱重新选择")}</p></div>`;
      }
      return;
    }
  }
  let catalog;
  try { catalog = await api.aesthetics(); }
  catch (error) {
    if (!alive()) return;
    deckEl.innerHTML = `<div class="outfit-deck-empty"><p>审美方案读取失败：${esc(error.message)}</p><button type="button" class="outfit-deck-foot" data-retry>重新读取</button></div>`;
    deckEl.querySelector("[data-retry]").onclick = () => { if (alive()) void mount(root, ctx, arg, restore); };
    return;
  }
  if (!alive()) return;
  const profiles = catalog.aesthetics || [];
  if (!profiles.length) { deckEl.innerHTML = `<div class="outfit-deck-empty"><p>暂时没有可用的审美方案。</p></div>`; return; }

  const state = { aesthetic: catalog.default || profiles[0].id, limit: 1, useLlm: true, anchorId };
  if (restore?.state) Object.assign(state, restore.state);
  state.limit = 1;   // ★ 单套模式：一次只出一套（§42）。旧快照里的 3/5 套不再沿用。
  state.season = ""; // 当前季节由服务端确定，旧快照的季节选择不再沿用。
  state.anchorId = anchorId;
  if (!profiles.some(profile => profile.id === state.aesthetic)) state.aesthetic = catalog.default || profiles[0].id;

  const includesAnchor = outfit => !anchorId || (outfit.item_ids?.includes(anchorId) && outfit.items?.some(item => item.id === anchorId));
  const usableOutfit = outfit => includesAnchor(outfit) && outfit.item_ids?.length && outfit.item_ids.every(id => !removedItems.has(id) && outfit.items?.some(item => item.id === id));
  const clampIndex = (value, length) => Math.min(Math.max(0, value || 0), Math.max(0, length - 1));

  let deck = [], deckIndex = 0, deckProfile = null, busy = false, errorText = "";
  let historyRows = [], historySeq = 0, historyError = "";
  let aestheticIndex = 0;
  if (restore) {
    deck = (restore.deck || []).filter(usableOutfit);
    deckIndex = clampIndex(restore.deckIndex, deck.length);
    deckProfile = restore.deckProfile || null;
    aestheticIndex = Math.min(Math.max(0, restore.aestheticIndex || 0), profiles.length - 1);
  }
  if (!deckProfile) deckProfile = profiles.find(profile => profile.id === state.aesthetic) || profiles[0];
  if (!deck.length) deckIndex = 0;

  function paletteOf(items) {
    const colors = [...new Set(items.map(item => item.primary_color).filter(color => PALETTE[color]))].slice(0, 4);
    const labels = colors.map(color => typeof taxonomy.vocabularies?.color?.[color] === "string" ? taxonomy.vocabularies.color[color] : (COLOR_LABEL[color] || "")).filter(Boolean);
    return { colors, labels };
  }

  /* ---------- 第一层：审美卡 ---------- */
  function syncAesthetic(index, force = false) {
    const next = Math.min(Math.max(0, index), profiles.length - 1);
    if (!force && next === aestheticIndex) return;
    aestheticIndex = next;
    countEl.textContent = `${next + 1} / ${profiles.length}`;
    [...dotsEl.children].forEach((button, position) => button.setAttribute("aria-pressed", String(position === next)));
  }
  function renderAestheticDeck() {
    deckEl.innerHTML = profiles.map(profile => aestheticCardHtml(profile)).join("");
    dotsEl.innerHTML = aestheticDotsHtml(profiles, aestheticIndex);
    syncAesthetic(aestheticIndex, true);
    // 首帧可能还没布局完，宽度拿不到就先落一次，等 rAF 再对一次。
    requestAnimationFrame(() => { if (alive() && deckEl.clientWidth) deckEl.scrollLeft = aestheticIndex * deckEl.clientWidth; });
  }
  let scrollTick = 0;
  deckEl.addEventListener("scroll", () => {
    if (scrollTick) return;
    scrollTick = requestAnimationFrame(() => {
      scrollTick = 0;
      const width = deckEl.clientWidth;
      if (width) syncAesthetic(Math.round(deckEl.scrollLeft / width));
    });
  }, { passive: true });
  deckEl.addEventListener("click", event => {
    const learn = event.target.closest("[data-aesthetic-guide]");
    if (learn) { const profile = profiles.find(row => row.id === learn.dataset.aestheticGuide); if (profile && alive()) sheet.openGuide(profile, learn); return; }
    const face = event.target.closest("[data-aesthetic-open]");
    if (!face) return;
    const profile = profiles.find(row => row.id === face.dataset.aestheticOpen);
    if (profile && alive()) openDeck(profile, face);
  });
  dotsEl.addEventListener("click", event => {
    const dot = event.target.closest("[data-aesthetic-dot]");
    if (!dot) return;
    const index = Number(dot.dataset.aestheticDot);
    deckEl.scrollTo({ left: index * deckEl.clientWidth, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  });

  function showSeason(data) {
    if (!alive()) return;
    const name = data?.season_label || "当前季节";
    seasonEl.innerHTML = `${icon("leaf")}<span>${esc(name)}</span>`;
  }
  function refreshSeason() {
    if (!document.hidden && alive()) void api.aesthetics().then(showSeason).catch(() => {});
  }
  document.addEventListener("visibilitychange", refreshSeason);
  showSeason(catalog);

  /* ---------- 第二层：堆叠结果 ---------- */
  function stackCardHtml(outfit, k, position, total) {
    // 后两张只是"露边"的厚度感：不给按钮、不给图，省一大截 DOM，也免得
    // 那些被缩放成 40×40 的按钮被当成真的触控目标（契约 §4 量的是 44）。
    if (k > 0) return `<div class="outfit-stack-card is-ghost" data-k="${k}" style="--k:${k};z-index:${20 - k}" aria-hidden="true"></div>`;
    const items = outfit.items || [];
    const profile = outfit.aesthetic || deckProfile;
    const { colors, labels } = paletteOf(items);
    const title = labels.length ? `${labels.slice(0, 2).join("与")}，${profile?.name || "自然搭配"}。` : (profile?.name || "这套搭配");
    const pieces = items.slice(0, 6).map(item => {
      const url = api.presentationUrl(item), name = item.name || item.type_label || "未命名";
      return `<a class="outfit-stack-piece" href="#item/${esc(item.id)}" aria-label="查看${esc(name)}">${url ? `<img src="${esc(url)}" data-tighten data-flatlay-source="${esc(url)}" alt="${esc(name)}" loading="lazy">` : `<span class="ph">${esc(item.type_label || "无照片")}</span>`}${item.id === anchorId ? '<span class="outfit-piece-anchor">必选</span>' : ""}</a>`;
    }).join("");
    // 排法随件数变（§43）：1 铺满 / 2 并排 / 3 主副（主件贯穿两行）/ 4 两行两列 /
    // 5 件 3+2 / 6 件 3+3。图区已经是白底，件数决定形状，不再靠 --cols 调列数。
    const n = items.length;
    const shape = n <= 1 ? 1 : n === 2 ? 2 : n === 3 ? 3 : n === 4 ? 4 : n === 5 ? 5 : 6;
    return `<article class="outfit-stack-card is-top" data-k="${k}" data-outfit-card="${esc(outfit.id)}" style="--k:${k};z-index:${20 - k}">
      <div class="outfit-stack-grid" data-shape="${shape}">${pieces}</div>
      <div class="outfit-stack-meta"><div class="outfit-stack-copy"><div class="outfit-stack-k"><span>${total > 1 ? `${position + 1} / ${total} 套` : ""}</span><span>${items.length} 件</span></div>
        <h3>${esc(title)}</h3><p>${esc(outfit.reason || `按${profile?.name || "当前审美"}从已有衣物里挑的。`)}</p>
        <div class="outfit-color-row">${colors.map(color => `<i style="background:${PALETTE[color]}"></i>`).join("")}<span>${esc(labels.join(" × "))}</span></div></div>
        <div class="outfit-stack-acts"><button type="button" class="primary" data-deck-action="tryon">${icon("camera")}实时试衣</button><button type="button" class="outfit-stack-info" data-deck-action="basis" aria-label="查看这套的搭配依据与反馈">${icon("info")}</button></div>
      </div></article>`;
  }
  function skeletonHtml() {
    return `<div class="outfit-deck-skel"><div class="g"><i></i><i></i><i></i><i></i></div><div class="t"><i></i><i></i><i></i></div><p class="msg">正在按「${esc(deckProfile?.name || "当前审美")}」从衣橱挑选…</p></div>`;
  }
  function deckFrame() {
    const total = deck.length;
    const index = total ? Math.min(deckIndex, total - 1) : 0;
    const name = deckProfile?.name || "搭配";
    // 空结果 + 没报错 = 还没生成过 → 用骨架，不要先闪一下"没有可用搭配"。
    const working = busy || !!inFlight || (!total && !errorText);
    // 单套模式（§42）：标题不再挂"· 1 套"，副标题也不再挂"1 / 1 · 左右拖动"。
    const title = working ? `${name} · 生成中` : total === 1 ? name : total ? `${name} · ${total} 套` : name;
    const sub = working
      ? (state.limit === 1 ? `正在按「${name}」从衣橱挑 1 套…` : `正在从衣橱里挑 ${state.limit} 套…`)
      : errorText ? ""
      : total === 1 ? `按${name}挑了 1 套`
      : total ? `<b>${index + 1} / ${total}</b> · 左右拖动换一套`
      : "换个审美，或者先去衣橱多录几件";
    let body;
    if (working) body = `<div class="outfit-stack">${skeletonHtml()}</div>`;
    else if (errorText) body = `<div class="outfit-stack"><div class="outfit-deck-error"><p>${esc(errorText)}</p><button type="button" data-deck-action="retry">重新生成</button></div></div>`;
    else if (!total) body = `<div class="outfit-stack"><div class="outfit-deck-error"><p>这个审美还没配出可用的组合，先去衣橱多录几件。</p><button type="button" data-deck-action="retry">再试一次</button></div></div>`;
    else body = `<div class="outfit-stack">${deck.map((outfit, position) => position >= index && position <= index + 2 ? stackCardHtml(outfit, position - index, position, total) : "").join("")}</div>`;
    let foot = "";
    if (!working && !errorText && total) foot = total === 1
      ? `<button type="button" class="outfit-deck-foot" data-deck-action="shuffle">${icon("replay")}换一套</button>`
      : index < total - 1
        ? `<button type="button" class="outfit-deck-foot" data-deck-action="next">还有 ${total - 1 - index} 套 · 滑走这一张${icon("right")}</button>`
        : `<button type="button" class="outfit-deck-foot" data-deck-action="regenerate">${icon("replay")}用这个审美再生成 ${state.limit} 套</button>`;
    return { title, body: `<p class="outfit-deck-sub">${sub}</p>${body}`, foot };
  }
  /** 重画面板并顺手把卡里的展示图换成裁紧版（§43）。
   * 所有 `sheet.refresh("deck")` 都必须走这里 —— refresh 是同步换 innerHTML，
   * 之后再调 tighten 才能拿到新的 img 节点。单独调 refresh 会跳过裁图。 */
  function refreshDeck() {
    sheet.refresh("deck");
    tighten(root.querySelector(".outfit-stack"));
  }

  function step(direction) {
    const next = deckIndex + direction;
    if (next < 0 || next >= deck.length) return;
    deckIndex = next;
    refreshDeck();
  }
  let dragUntil = 0;
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  /** visual = 卡片飞出去的方向（-1 左 / +1 右）；direction = 翻页方向（左滑＝下一套）。 */
  function fly(card, visual, direction) {
    card.style.transform = `translateX(${visual * 140}%) rotate(${visual * 14}deg)`;
    card.style.opacity = "0";
    setTimeout(() => { if (alive()) step(direction); }, reducedMotion.matches ? 0 : 190);
  }
  function bindStackDrag(scroll) {
    const card = scroll.querySelector(".outfit-stack-card.is-top");
    if (!card || deck.length < 2) return;
    const width = scroll.clientWidth || 320;
    let startX = 0, offset = 0, pointer = null;
    card.addEventListener("pointerdown", event => {
      // 图片格子本身就是链接，**允许**从它上面起手拖动；真正拖动了才拦掉那次 click。
      if (event.button > 0 || event.target.closest("button")) return;
      pointer = event.pointerId; startX = event.clientX; offset = 0;
      card.classList.add("is-dragging");
      try { card.setPointerCapture(event.pointerId); } catch { /* 某些壳不支持捕获，仍能收到冒泡 */ }
    });
    card.addEventListener("pointermove", event => {
      if (pointer !== event.pointerId) return;
      offset = event.clientX - startX;
      card.style.transform = `translateX(${offset}px) rotate(${offset / 26}deg)`;
      card.style.opacity = String(Math.max(.35, 1 - Math.abs(offset) / (width * 1.5)));
    });
    const end = event => {
      if (pointer !== event.pointerId) return;
      pointer = null;
      card.classList.remove("is-dragging");
      const far = Math.abs(offset) > 86;
      if (far) dragUntil = Date.now() + 400;
      if (far && offset < 0 && deckIndex < deck.length - 1) fly(card, -1, 1);
      else if (far && offset > 0 && deckIndex > 0) fly(card, 1, -1);
      else { card.style.transform = ""; card.style.opacity = ""; }
      offset = 0;
    };
    card.addEventListener("pointerup", end);
    card.addEventListener("pointercancel", end);
    // 拖完松手会补一次 click，别让它落进图片链接里跳走。
    card.addEventListener("click", event => { if (Date.now() < dragUntil) { event.preventDefault(); event.stopPropagation(); } }, true);
  }
  function deckAction(action, node) {
    if (!alive()) return;
    if (action === "next") { step(1); return; }
    if (action === "regenerate" || action === "retry") { errorText = ""; void generate(deckProfile); return; }
    // 「换一套」：把当前这套的单品排除掉，服务端从本地前几名里另挑一套。
    if (action === "shuffle") { errorText = ""; void generate(deckProfile, deck[deckIndex]?.item_ids || []); return; }
    if (action === "tryon") {
      const trialDeck = deck;
      openTryonSheet({ root, anchor: node, outfits: trialDeck, index: deckIndex, onSelect: index => { if (alive() && deck === trialDeck) { deckIndex = index; refreshDeck(); } } });
      return;
    }
    if (action === "basis") sheet.openBasis(deck[deckIndex], node);
  }

  async function generate(profile, excludeIds) {
    if (busy || inFlight || !profile) return;
    busy = true; errorText = "";
    refreshDeck();
    const requested = { aesthetic: profile.id, limit: state.limit, useLlm: state.useLlm, anchorId };
    // 单套模式：「换一套」把当前这套的单品带上去，服务端会避开它们重新挑。
    const exclude = (excludeIds || []).filter(Boolean);
    const pending = api.recommend({ aesthetic: profile.id, season: "", limit: requested.limit, use_llm: requested.useLlm ? "true" : "false", anchor_item_id: anchorId || undefined, exclude_item_ids: exclude.length ? exclude.join(",") : undefined });
    inFlight = pending;
    inFlightRequested = requested;
    try {
      const response = await pending;
      if (!alive() || requested.anchorId !== anchorId) return;
      showSeason(response);
      const outfits = (response.outfits || []).filter(usableOutfit);
      if (deckProfile?.id === requested.aesthetic) {
        deck = outfits; deckIndex = 0;
        if (!outfits.length) errorText = response.note || "这次没配出能用的组合，先去衣橱多录几件。";
        // 降级提示只弹一次：它不该常驻在副标题里挤掉"第几套"这些实时信息。
        else if (response.degraded) toast("AI 写理由暂不可用，已按这个审美本地组套", "info");
      }
    } catch (error) {
      if (deckProfile?.id === requested.aesthetic) {
        errorText = error.code === "request_timeout" ? "这次没收到结果，后台可能已经生成完，再试一次就能看到。" : `这次生成未成功：${error.message}`;
      }
    } finally {
      if (inFlight === pending) { inFlight = null; inFlightRequested = null; }
      busy = false;
      if (alive()) refreshDeck();
      void loadHistory();
    }
  }
  function openDeck(profile, anchor) {
    if (!profile) return;
    if (deckProfile?.id !== profile.id) { deck = []; deckIndex = 0; errorText = ""; }
    deckProfile = profile;
    state.aesthetic = profile.id;
    sheet.openDeck(profile, anchor);
    if (!deck.length && !busy && !inFlight) void generate(profile);
  }

  /* ---------- 最近搭配 ---------- */
  function historyHtml() {
    if (historyError) return `<p class="alert error">${esc(historyError)}</p>`;
    if (!historyRows.length) return '<p class="faint">还没有生成过搭配。</p>';
    return `<ul class="hist">${historyRows.map(outfit => `<li class="aesthetic-history"><div class="hist-row"><span class="faint">${esc(fmtDateTime(outfit.created_at))}</span><span>${esc(outfit.aesthetic?.name || "旧版搭配")}${outfit.aesthetic ? ` · v${esc(outfit.aesthetic.version)}` : ""}</span><span class="chip">${esc(STATUS_TEXT[outfit.status] || outfit.status || "")}</span></div><button type="button" class="outfit-history-open" data-restore-outfit="${esc(outfit.id)}">回看这套搭配${icon("right")}</button><div class="chips">${itemChipsHtml(outfit.items, taxonomy)}</div>${methodDetails(outfit.aesthetic, outfit.aesthetic?.assessment)}</li>`).join("")}</ul>`;
  }
  function restoreOutfit(id) {
    const outfit = historyRows.find(row => row.id === id);
    if (!outfit) return;
    deckProfile = outfit.aesthetic || { id: "", name: "历史搭配" };
    deck = [outfit]; deckIndex = 0; errorText = "";
  }
  async function loadHistory() {
    const seq = ++historySeq;
    try {
      const data = await api.listOutfits(20);
      if (!alive() || seq !== historySeq) return;
      historyRows = (data.outfits || []).filter(usableOutfit);
      historyError = "";
    } catch (error) {
      if (!alive() || seq !== historySeq) return;
      historyRows = []; historyError = `最近搭配读取失败：${error.message}`;
    }
    sheet.refresh("more");
  }

  function feedbackChanged(event) {
    if (!alive()) return;
    const { id, kind } = event.detail;
    if (!id || !kind) return;
    for (const outfit of [...deck, ...historyRows]) if (outfit.id === id) outfit._feedback = kind;
    void loadHistory();
  }
  async function submitFeedback(outfit, kind) {
    if (!outfit) return;
    if (feedbackPending.has(outfit.id)) return feedbackPending.get(outfit.id);
    const notify = type => document.dispatchEvent(new CustomEvent("outfits:feedback", { detail: { id: outfit.id, kind: type } }));
    const pending = api.feedback({ outfit_id: outfit.id, feedback_type: kind }).then(response => {
      const actual = ({ accepted: "like", rejected: "dislike", swapped: "swap" }[response.outfit_status]) || kind;
      outfit._feedback = actual; notify(actual);
      if (alive()) toast(response.duplicate ? "已更新当前选择，偏好记录未重复累加" : `已记下「${FB_TEXT[actual]}」`, "ok");
      return response;
    }).finally(() => {
      if (feedbackPending.get(outfit.id) === pending) { feedbackPending.delete(outfit.id); notify(); }
    });
    feedbackPending.set(outfit.id, pending); notify();
    return pending;
  }
  function itemRemoved(event) {
    if (!alive()) return;
    const removed = event.detail?.id;
    if (!removed) return;
    sheet?.itemRemoved(removed);
    if (anchorId === removed) { location.replace("#wardrobe"); return; }
    deck = deck.filter(outfit => !outfit.item_ids?.includes(removed));
    deckIndex = clampIndex(deckIndex, deck.length);
    sheet?.refresh("deck");
    void loadHistory();
  }
  container.addEventListener("item:removed", itemRemoved);
  document.addEventListener("outfits:feedback", feedbackChanged);

  sheet = createAestheticSheet({
    root, profiles,
    getSettings: () => ({ ...state }),
    onSettings: patch => { if (alive()) { Object.assign(state, patch); state.limit = 1; } },
    onUseProfile: profile => { if (alive()) openDeck(profile, null); },
    onFeedback: (outfit, kind) => submitFeedback(outfit, kind),
    isFeedbackPending: id => feedbackPending.has(id),
    useLabel: "用这个审美生成搭配",
    getHistoryHtml: historyHtml,
    onRestore: restoreOutfit,
    onMore: () => { void loadHistory(); },
    deck: { build: deckFrame, afterRender: bindStackDrag, onAction: deckAction }
  });

  root.captureState = () => ({ anchorId, state: { ...state }, deck, deckIndex, deckProfile, aestheticIndex });
  renderAestheticDeck();
  if (inFlight) {
    const waiting = inFlight, requested = inFlightRequested || { ...state };
    void waiting.then(response => {
      if (!alive() || requested.anchorId !== anchorId) return;
      if (deckProfile?.id !== requested.aesthetic) return;
      showSeason(response);
      deck = (response.outfits || []).filter(usableOutfit); deckIndex = 0;
      if (!deck.length) errorText = response.note || "这次没配出能用的组合。";
      refreshDeck();
    }).catch(() => {
      if (!alive() || deckProfile?.id !== requested.aesthetic) return;
      errorText = "上一轮生成未成功，再试一次。";
      refreshDeck();
    });
  }
  await loadHistory();
}
