/** §34：六审美、三图指南与真实搭配依据；嵌套返回不离开当前页面。 */
import { esc, icon } from "../../core/ui.js";
import { examplesFor, rulesHtml, methodDetails, coverFor } from "./aesthetic-ui.js";
const FOCUSABLE = "button,a[href],input,select,summary,[tabindex]";
const FEEDBACK = { like: "喜欢这套", dislike: "不喜欢", swap: "先不选" };
let nextId = 0;
export function createAestheticSheet({ root, profiles, getSettings, onSettings, onSelect, onGenerate, onFeedback, isFeedbackPending = () => false, useLabel = '用这个审美搭配', getBackground }) {
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
      const anchor = e.anchor?.isConnected ? e.anchor : root.querySelector("[data-open-aesthetics],#o-go");
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
  function render(e, focus = true) {
    if (!alive(e)) return;
    e.gesture?.dispose();
    const frame = e.frames[e.index], profile = frame.profile;
    const titles = { select: "选择审美", guide: "认识" + (profile?.name || "这个审美"), example: "穿搭示例", options: "搭配设置", basis: "这套为什么这样搭" };
    e.panel.setAttribute("aria-label", titles[frame.kind]);
    e.head.innerHTML = `<div class="aesthetic-sheet-title">${e.index ? `<button type="button" data-sheet-back class="aesthetic-back" aria-label="返回上一层">${icon("back")}</button>` : icon("spark")}<h2>${esc(titles[frame.kind])}</h2></div><button type="button" class="aesthetic-glass" data-sheet-close aria-label="关闭面板">${icon("close")}</button>`;
    let body = "", footer = "";
    if (frame.kind === "select") {
      body = `<p class="aesthetic-sheet-intro">每种审美都有完整安排。选一组，也可以先看三套穿搭。</p><div class="aesthetic-choice-grid">${profiles.map(p => {
        const cover = coverFor(p);
        return `<article class="aesthetic-choice-card ${p.id === getSettings().aesthetic ? "is-selected" : ""}"><button type="button" data-sheet-pick="${esc(p.id)}" aria-pressed="${p.id === getSettings().aesthetic}">${cover ? `<img src="${esc(cover)}" alt="${esc(p.name)}质感示意" loading="lazy">` : ""}<strong>${esc(p.name)}</strong><small>${esc((p.points || []).slice(0, 2).join(" · "))}</small>${p.id === getSettings().aesthetic ? `<span class="aesthetic-choice-check">${icon("check")}</span>` : ""}</button><button type="button" data-sheet-guide="${esc(p.id)}">看三套穿搭${icon("right")}</button></article>`;
      }).join("")}</div>`;
    } else if (frame.kind === "guide") {
      // 视觉只留一张平铺静物（与卡片同一张、同一比例），不再放模特上身照 ——
      // 两者是两套视觉语言，点进来会像换了个页面。三套具体穿法改成文字条，
      // 每条可展开看五条安排要点，信息一条没少。
      const cover = coverFor(profile), plans = examplesFor(profile);
      body = `<h3 class="aesthetic-guide-name">${esc(profile.name)}</h3><p class="aesthetic-sheet-intro">${esc(profile.core || profile.summary)}</p>
        ${cover ? `<div class="aesthetic-guide-cover"><img src="${esc(cover)}" alt="${esc(profile.name)}质感示意" decoding="async"></div>` : ""}
        ${plans.length ? `<h3 class="aesthetic-rule-heading">三套具体穿法</h3><div class="aesthetic-plans">${plans.map((plan, index) => `<details class="aesthetic-plan"><summary><span class="aesthetic-plan-index">${index + 1}</span><span class="aesthetic-plan-text"><strong>${esc(plan.title)}</strong><small>${esc(plan.items)}</small></span>${icon("chevron")}</summary><dl class="aesthetic-plan-notes">${Object.entries(plan.notes || {}).map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("")}</dl></details>`).join("")}</div>` : ""}
        <h3 class="aesthetic-rule-heading">三套变化，共同的审美安排</h3>
        ${rulesHtml(profile)}<p class="aesthetic-example-note">${esc(profile.example_note || profile.flexible)}</p>`;
      if (profiles.some(p => p.id === profile.id && p.version === profile.version)) footer = `<button type="button" class="primary" data-sheet-use="${esc(profile.id)}">${icon("spark")}${esc(useLabel)}</button>`;
    } else if (frame.kind === "options") {
      const settings = getSettings();
      body = `<div class="aesthetic-setting-row"><strong>一次生成</strong><div class="aesthetic-counts">${[3, 5].map(n => `<button type="button" data-sheet-count="${n}" aria-pressed="${settings.limit === n}">${n} 套</button>`).join("")}</div></div><div class="aesthetic-setting-row"><div><strong>AI 比较</strong><p>从候选组合里挑选更协调的搭配。</p></div><button type="button" role="switch" data-sheet-ai aria-checked="${settings.useLlm}" aria-label="AI 比较" class="aesthetic-switch"></button></div><p class="aesthetic-example-note">按当前季节，从已有衣物中选择。</p>`;
      footer = '<button type="button" class="primary" data-sheet-close>好，就这样</button>';
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
      if ("sheetPick" in data || "sheetUse" in data) {
        const profile = profiles.find(p => p.id === (data.sheetPick || data.sheetUse)); if (!profile) return;
        onSelect(profile.id); requestClose("sheetUse" in data ? () => onGenerate() : undefined); return;
      }
      if ("sheetGuide" in data) { const profile = profiles.find(p => p.id === data.sheetGuide); if (profile) push({ kind: "guide", profile }); return; }
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
    openSelector: anchor => open({ kind: "select" }, anchor),
    openGuide: (profile, anchor) => profile && open({ kind: "guide", profile }, anchor),
    openOptions: anchor => open({ kind: "options" }, anchor),
    openBasis: (outfit, anchor) => open({ kind: "basis", outfit }, anchor),
    itemRemoved(id) { if (entry?.frames.some(frame => frame.outfit?.item_ids?.includes(id))) requestClose(); },
    dispose() { disposed = true; if (entry) finish(entry, false); }
  };
}

