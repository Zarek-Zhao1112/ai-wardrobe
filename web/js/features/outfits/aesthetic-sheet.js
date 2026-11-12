/** §34：六审美、三图指南与真实搭配依据；嵌套返回不离开当前页面。
 *
 *  §40 起它同时是**搭配结果面板**：
 *    deck  —— 一屏一张的堆叠卡（玻璃面板整块盖住页面，按住最上面那张左右拖）
 *    more  —— 生成设置（3/5 套、AI 比较）+ 最近搭配
 *  两者都从 `deck` 适配器取内容，**不在本文件里存 deck 状态** —— 数据归 view.js，
 *  这里只负责"什么时候重画"。这样滑走一套之后不会出现两份真值。
 */
import { esc, icon } from "../../core/ui.js";
import { arrangementFor, examplesFor, rulesHtml, methodDetails, coverImage } from "./aesthetic-ui.js";
const FOCUSABLE = "button,a[href],input,select,summary,[tabindex]";
const FEEDBACK = { like: "喜欢这套", dislike: "不喜欢", swap: "先不选" };
let nextId = 0;
export function createAestheticSheet({ root, profiles, getSettings, onSettings, onUseProfile, onFeedback, isFeedbackPending = () => false, useLabel, getBackground, deck = null, getHistoryHtml = () => "", onRestore = () => {}, onMore = () => {} }) {
  let entry = null, disposed = false;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const alive = e => !disposed && entry === e && root.isConnected;
  const marker = () => history.state?.aestheticSheet;
  function lock(e) {
    e.background = (getBackground?.() || [document.querySelector(".topbar"), root, document.querySelector("#tabbar")]).filter(Boolean).map(node => ({
      node, aria: node.getAttribute("aria-hidden"), filter: node.style.filter
    }));
    e.focus = e.background.flatMap(({ node }) => [...node.querySelectorAll(FOCUSABLE)].map(node => [node, node.getAttribute("tabindex")]));
    e.background.forEach(({ node }) => node.setAttribute("aria-hidden", "true"));
    e.focus.forEach(([node]) => node.tabIndex = -1);
    document.body.classList.add("aesthetic-sheet-open");
  }
  function finish(e, restoreFocus = true) {
    if (entry !== e) return;
    clearTimeout(e.timer); e.gesture?.dispose(); e.removers.forEach(remove => remove());
    e.background.forEach(({ node, aria, filter }) => {
      node.style.filter = filter; aria === null ? node.removeAttribute("aria-hidden") : node.setAttribute("aria-hidden", aria);
    });
    e.focus.forEach(([node, value]) => value === null ? node.removeAttribute("tabindex") : node.setAttribute("tabindex", value));
    document.body.classList.remove("aesthetic-sheet-open");
    e.layer.remove(); entry = null;
    if (location.hash === e.hash && marker()?.id === e.id) {
      const state = { ...history.state }; delete state.aestheticSheet; history.replaceState(state, "", location.href);
    }
    if (restoreFocus && root.isConnected) {
      window.scrollTo(0, e.scrollY);
      const anchor = e.anchor?.isConnected ? e.anchor : root.querySelector("[data-aesthetic-open]");
      anchor?.focus({ preventScroll: true });
    }
    if (!disposed && root.isConnected && location.hash === e.hash) e.afterClose?.();
  }
  function close(e, instant = false) {
    if (!alive(e)) return;
    if (e.phase === "closing") { if (instant) finish(e, false); return; }
    e.phase = "closing"; e.gesture?.dispose(); e.layer.classList.remove("is-open"); e.layer.style.pointerEvents = "none";
    e.background.forEach(({ node, filter }) => node.style.filter = filter);
    if (instant || reduced.matches) finish(e, !instant);
    else e.timer = setTimeout(() => finish(e), 370);
  }
  function requestClose(afterClose) {
    const e = entry; if (!e || e.phase === "closing" || e.returning) return;
    e.afterClose = afterClose; e.returning = true;
    if (location.hash === e.hash && marker()?.id === e.id) history.go(-(e.index + 1));
    else close(e);
  }
  function push(frame, replace = false) {
    const e = entry; if (!e || e.returning || e.phase === "closing") return;
    e.frames[e.index].scroll = e.scroll.scrollTop;
    if (replace) { e.frames[e.index] = frame; history.replaceState({ ...history.state, aestheticSheet: { id: e.id, depth: e.index } }, "", location.href); }
    else { e.frames = e.frames.slice(0, e.index + 1); e.frames.push(frame); e.index++; history.pushState({ ...history.state, aestheticSheet: { id: e.id, depth: e.index } }, "", location.href); }
    render(e);
  }
  /** 生成设置那一块：options 帧和 more 帧共用，别写两份。 */
  function optionsHtml() {
    const settings = getSettings();
    // ★ §42 单套模式：一次只出一套，「一次生成 3/5 套」这行没有意义了，整体去掉。
    // 「AI 比较」也改了说法 —— 模型不再从候选里挑，只负责给选中的那套写理由。
    return `<div class="aesthetic-setting-row"><div><strong>AI 写理由</strong><p>让 AI 说清这套为什么合这个审美。</p></div><button type="button" role="switch" data-sheet-ai aria-checked="${settings.useLlm}" aria-label="AI 写理由" class="aesthetic-switch"></button></div><p class="aesthetic-example-note">按当前季节，从已有衣物中选择。</p>`;
  }
  function renderDeck(e, focus = true) {
    const built = deck.build();
    e.panel.setAttribute("aria-label", built.title || "搭配");
    // 从「认识这个」里点"用这个审美生成搭配"时，deck 是压在 guide 上的一帧，
    // 所以头部要留一个返回；从第一层直接点卡进来是根帧，没有返回。
    e.head.innerHTML = `<div class="aesthetic-sheet-title">${e.index ? `<button type="button" data-sheet-back class="aesthetic-back" aria-label="返回上一层">${icon("back")}</button>` : ""}<h2>${esc(built.title)}</h2></div><div class="outfit-deck-tools"><button type="button" class="outfit-deck-more" data-deck-more>更多</button><button type="button" class="aesthetic-glass" data-sheet-close aria-label="关闭面板">${icon("close")}</button></div>`;
    e.scroll.innerHTML = built.body;
    e.foot.innerHTML = built.foot || ""; e.foot.hidden = !built.foot;
    deck.afterRender?.(e.scroll);
    if (focus) e.head.querySelector("[data-sheet-close]")?.focus({ preventScroll: true });
  }
  function render(e, focus = true) {
    if (!alive(e)) return;
    e.gesture?.dispose();
    const frame = e.frames[e.index], profile = frame.profile;
    const isDeck = frame.kind === "deck";
    e.layer.classList.toggle("is-deck", isDeck);
    e.panel.classList.toggle("is-deck", isDeck);
    e.panel.classList.toggle("is-guide", frame.kind === "guide");
    if (isDeck) { renderDeck(e, focus); return; }
    const titles = { guide: profile?.name || "认识这个风格", options: "搭配设置", more: "更多", basis: "这套为什么这样搭" };
    e.panel.setAttribute("aria-label", titles[frame.kind]);
    e.head.innerHTML = `<div class="aesthetic-sheet-title">${e.index ? `<button type="button" data-sheet-back class="aesthetic-back" aria-label="返回上一层">${icon("back")}</button>` : icon("spark")}<h2>${esc(titles[frame.kind])}</h2></div><button type="button" class="aesthetic-glass" data-sheet-close aria-label="关闭面板">${icon("close")}</button>`;
    let body = "", footer = "";
    if (frame.kind === "guide") {
      const cover = coverImage(profile);
      const plans = examplesFor(profile);
      const rules = arrangementFor(profile);
      const summary = rules.filter(row => ["配色", "轮廓", "材质"].includes(row.label));
      const extra = rules.filter(row => !summary.includes(row));
      body = `<div class="aesthetic-guide-intro">
          <p>${esc(profile.core || profile.summary)}</p>
          ${cover ? `<figure class="aesthetic-guide-cover"><img src="${esc(cover)}" alt="${esc(profile.name)}服装平铺示意" decoding="async"><figcaption>AI 平铺示意</figcaption></figure>` : ""}
        </div>
        ${rulesHtml(summary)}
        ${plans.length ? `<details class="aesthetic-guide-more" data-fold="1"><summary>三套具体穿法${icon("chevron")}</summary><ol class="aesthetic-guide-examples">${plans.map(plan => `<li><strong>${esc(plan.title)}</strong><span>${esc(plan.items)}</span></li>`).join("")}</ol></details>` : ""}
        ${extra.length ? `<details class="aesthetic-guide-more" data-fold="1"><summary>更多风格特点${icon("chevron")}</summary>${rulesHtml(extra)}</details>` : ""}`;
      if (useLabel && onUseProfile && profiles.some(p => p.id === profile.id && p.version === profile.version)) {
        footer = `<button type="button" class="primary" data-sheet-use="${esc(profile.id)}">${esc(useLabel)}${icon("right")}</button>`;
      }
    } else if (frame.kind === "options") {
      body = optionsHtml();
      footer = '<button type="button" class="primary" data-sheet-close>好，就这样</button>';
    } else if (frame.kind === "more") {
      body = `${optionsHtml()}<h3 class="aesthetic-rule-heading">最近搭配</h3>${getHistoryHtml()}`;
    } else if (frame.kind === "basis") {
      const outfit = frame.outfit, selected = outfit._feedback || ({ accepted: "like", rejected: "dislike", swapped: "swap" }[outfit.status]);
      const waiting = e.feedbackBusy || isFeedbackPending(outfit.id);
      body = `<p class="aesthetic-sheet-intro">${esc(outfit.reason || "依据生成时的审美目标与已有属性组套。")}</p>${methodDetails(outfit.aesthetic, outfit.aesthetic?.assessment, true)}<div class="aesthetic-feedback">${Object.entries(FEEDBACK).map(([key, label]) => `<button type="button" data-sheet-feedback="${key}" aria-pressed="${selected === key}" ${waiting ? "disabled" : ""}>${label}</button>`).join("")}</div><p class="aesthetic-sheet-error" role="status">${esc(frame.error || "")}</p>`;
      footer = '<button type="button" class="aesthetic-secondary" data-sheet-close>回到搭配页</button>';
    }
    e.scroll.innerHTML = body; e.foot.innerHTML = footer; e.foot.hidden = !footer; e.scroll.scrollTop = frame.scroll || 0;
    if (focus) e.head.querySelector("[data-sheet-close]").focus({ preventScroll: true });
  }
  function open(frame, anchor) {
    if (disposed || !root.isConnected || entry) return;
    const layer = document.createElement("div"); layer.className = "aesthetic-layer";
    layer.innerHTML = '<div class="aesthetic-veil" data-sheet-close></div><section class="aesthetic-panel" role="dialog" aria-modal="true" tabindex="-1"><header class="aesthetic-sheet-head"></header><div class="aesthetic-sheet-scroll"></div><footer class="aesthetic-sheet-footer"></footer></section>';
    const e = { id: "as-" + Date.now() + "-" + ++nextId, root, anchor, layer, hash: location.hash, scrollY: scrollY, frames: [frame], index: 0, phase: "opening", removers: [] };
    entry = e; document.body.append(layer);
    e.panel = layer.querySelector(".aesthetic-panel"); e.head = layer.querySelector(".aesthetic-sheet-head"); e.scroll = layer.querySelector(".aesthetic-sheet-scroll"); e.foot = layer.querySelector(".aesthetic-sheet-footer");
    // 单图与方案快照只由调用方传入，历史不套用新版目录。
    lock(e); render(e);
    const state = { ...history.state }; delete state.aestheticSheet;
    history.replaceState(state, "", location.href); history.pushState({ ...state, aestheticSheet: { id: e.id, depth: 0 } }, "", location.href);
    const listen = (target, name, fn) => { target?.addEventListener(name, fn); e.removers.push(() => target?.removeEventListener(name, fn)); };
    listen(window, "popstate", () => {
      if (!alive(e)) return;
      if (location.hash !== e.hash) { close(e, true); return; }
      const state = marker();
      if (state?.id === e.id && Number.isInteger(state.depth) && e.frames[state.depth]) {
        e.index = state.depth; e.returning = false; render(e);
      } else close(e);
    });
    listen(window, "hashchange", () => { if (location.hash !== e.hash) close(e, true); });
    listen(document, "outfits:feedback", event => {
      const frame = e.frames[e.index];
      if (!alive(e) || frame.kind !== "basis" || frame.outfit.id !== event.detail.id) return;
      if (event.detail.kind) frame.outfit._feedback = event.detail.kind;
      const selected = frame.outfit._feedback || ({ accepted: "like", rejected: "dislike", swapped: "swap" }[frame.outfit.status]);
      e.panel.querySelectorAll("[data-sheet-feedback]").forEach(button => {
        button.disabled = e.feedbackBusy || isFeedbackPending(frame.outfit.id);
        button.setAttribute("aria-pressed", String(selected === button.dataset.sheetFeedback));
      });
    });
    const resize = () => { layer.style.setProperty("--aesthetic-vh", (window.visualViewport?.height || innerHeight) + "px"); layer.style.setProperty("--aesthetic-top", (window.visualViewport?.offsetTop || 0) + "px"); };
    listen(window, "resize", resize); listen(window.visualViewport, "resize", resize); resize();
    listen(document, "focusin", event => { if (alive(e) && !e.panel.contains(event.target)) e.panel.focus({ preventScroll: true }); });
    listen(document, "keydown", event => {
      if (!alive(e)) return;
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); requestClose(); }
      if (event.key === "Tab") {
        const nodes = [...e.panel.querySelectorAll(FOCUSABLE)].filter(node => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length);
        if (!nodes.length) { event.preventDefault(); e.panel.focus(); }
        else if (event.shiftKey && (document.activeElement === nodes[0] || document.activeElement === e.panel)) { event.preventDefault(); nodes.at(-1).focus(); }
        else if (!event.shiftKey && document.activeElement === nodes.at(-1)) { event.preventDefault(); nodes[0].focus(); }
      }
    });
    listen(layer, "click", async event => {
      if (!alive(e) || e.phase === "closing" || e.returning) return;
      const button = event.target.closest("button,[data-sheet-close]"); if (!button || button.disabled) return;
      const frame = e.frames[e.index], data = button.dataset;
      if ("sheetClose" in data) { requestClose(); return; }
      if ("sheetBack" in data) { if (e.index) history.back(); else requestClose(); return; }
      // ★ 结果卡的四个动作交给 view：它才知道 deck 和当前是第几套。
      if (frame.kind === "deck" && data.deckAction) { deck.onAction(data.deckAction, button); return; }
      if ("deckMore" in data) { onMore(); push({ kind: "more" }); return; }
      if ("restoreOutfit" in data) {
        onRestore(data.restoreOutfit);
        if (e.index) history.back(); else render(e);
        return;
      }
      if ("sheetUse" in data) {
        const profile = profiles.find(p => p.id === data.sheetUse); if (!profile) return;
        requestClose(() => onUseProfile?.(profile)); return;
      }
      if ("sheetCount" in data) { onSettings({ limit: Number(data.sheetCount) }); render(e, false); e.scroll.querySelector(`[data-sheet-count="${data.sheetCount}"]`)?.focus({ preventScroll: true }); return; }
      if ("sheetAi" in data) { onSettings({ useLlm: !getSettings().useLlm }); render(e, false); e.scroll.querySelector("[data-sheet-ai]").focus({ preventScroll: true }); return; }
      if ("sheetFeedback" in data && !e.feedbackBusy) {
        // 用打开时的真实outfit对象提交；生成新deck不会改变这次反馈的ID。
        e.feedbackBusy = true; frame.error = ""; render(e, false);
        try { await onFeedback(frame.outfit, data.sheetFeedback); }
        catch (error) { frame.error = "反馈未成功：" + error.message; }
        finally { e.feedbackBusy = false; if (alive(e) && e.frames[e.index] === frame) render(e); }
      }
    });
    requestAnimationFrame(() => {
      if (!alive(e) || e.phase === "closing") return;
      layer.classList.add("is-open"); e.background.forEach(({ node }) => node.style.filter = "blur(8px)"); e.phase = "open";
    });
  }
  return {
    openDeck(profile, anchor) {
      if (entry) {
        const frame = entry.frames[entry.index];
        if (frame?.kind !== "deck" && profile) push({ kind: "deck", profile });
        return entry;
      }
      open({ kind: "deck", profile }, anchor);
      return entry;
    },
    openGuide: (profile, anchor) => profile && open({ kind: "guide", profile }, anchor),
    openOptions: anchor => open({ kind: "options" }, anchor),
    /** ★ 从结果卡里点 (i) 时面板**已经开着**（entry 存在），走 open() 会被
     *  `if (disposed || !root.isConnected || entry) return;` 直接挡掉 ——
     *  表现为按钮点了没反应。这里必须叠一帧上去。 */
    openBasis: (outfit, anchor) => {
      if (!outfit) return;
      if (entry) { push({ kind: "basis", outfit }); return; }
      open({ kind: "basis", outfit }, anchor);
    },
    /** 只重画"当前是这一类"的帧：生成完成刷 deck，历史回来刷 more。 */
    refresh(kind) {
      const e = entry; if (!e || e.phase === "closing") return;
      const frame = e.frames[e.index];
      if (kind && frame?.kind !== kind) return;
      render(e, false);
    },
    itemRemoved(id) { if (entry?.frames.some(frame => frame.outfit?.item_ids?.includes(id))) requestClose(); },
    dispose() { disposed = true; if (entry) finish(entry, false); }
  };
}
