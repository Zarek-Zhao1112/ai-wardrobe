// §24：详情覆盖在原页面上，图片按真实起终点展开/回收，背景DOM及滚动保留。
import { mount, refreshService } from "./detail.js";
import { icon, toast } from "../../core/ui.js";
import { api } from "../../core/api.js";

let active = null, launch = null, serial = 0;
const reduced = matchMedia("(prefers-reduced-motion: reduce)");
const focusSelector = 'a[href],button,input,select,textarea,summary,[tabindex]';
export const itemSheetOpen = () => Boolean(active);
export const itemSheetRoot = () => active?.content || null;
export const itemSheetReturn = () => active?.returnHash || "#wardrobe";
export const itemSheetHistory = () => active?.historyState || null;
export const refreshItemSheet = ctx => { if (active) refreshService(active.content, ctx); };

document.addEventListener("click", event => {
  const link = event.target.closest('a[href^="#item/"]');
  if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
  launch = { hash: link.hash, returnHash: location.hash || "#wardrobe", anchor: link, time: performance.now() };
}, true);
function rect(node) {
  const r = node?.getBoundingClientRect();
  return r ? { left: r.left, top: r.top, width: r.width, height: r.height } : null;
}
function css(values) {
  return Object.fromEntries(Object.entries(values).map(([key, value]) => [key, typeof value === "number" && ["left", "top", "width", "height", "borderRadius"].includes(key) ? value + "px" : value]));
}
function apply(node, values) { Object.assign(node.style, css(values)); }
function animate(entry, node, frames, duration, easing = "cubic-bezier(.22,.75,.24,1)") {
  if (!node?.animate) { apply(node, frames[frames.length - 1]); return; }
  entry.animations.push(node.animate(frames.map(css), { duration, easing, fill: "both" }));
}
function cancel(entry) { entry.animations.forEach(a => a.cancel()); entry.animations = []; clearTimeout(entry.timer); }
async function settle(entry, duration) {
  await Promise.race([Promise.all(entry.animations.map(a => a.finished.catch(() => {}))), new Promise(resolve => entry.timer = setTimeout(resolve, duration + 150))]);
}
function background(entry, hidden) {
  if (hidden) {
    entry.background = [document.querySelector(".topbar"), entry.base, document.querySelector("#tabbar")].filter(Boolean).map(node => ({ node, aria: node.getAttribute("aria-hidden"), filter: node.style.filter }));
    entry.focusables = entry.background.flatMap(({ node }) => [...node.querySelectorAll(focusSelector)].map(el => [el, el.getAttribute("tabindex")]));
    entry.background.forEach(({ node }) => node.setAttribute("aria-hidden", "true"));
    entry.focusables.forEach(([node]) => node.setAttribute("tabindex", "-1"));
    entry.padding = document.body.style.paddingRight;
    const scrollbar = innerWidth - document.documentElement.clientWidth;
    if (scrollbar > 0) document.body.style.paddingRight = scrollbar + "px";
    document.body.classList.add("item-sheet-open");
  } else {
    entry.background.forEach(({ node, aria, filter }) => { node.style.filter = filter; if (aria === null) node.removeAttribute("aria-hidden"); else node.setAttribute("aria-hidden", aria); });
    entry.focusables.forEach(([node, value]) => { if (value === null) node.removeAttribute("tabindex"); else node.setAttribute("tabindex", value); });
    document.body.style.paddingRight = entry.padding; document.body.classList.remove("item-sheet-open");
  }
}
function canClose(entry) {
  if (document.querySelector(".bridge-modal,.import-modal")) { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); return false; }
  if (entry.content.detailGate?.busy && entry.content.dataset.flatlayRunning !== "true") { toast("正在保存，请稍候"); return false; }
  if (entry.content.deleteDialog) { entry.content.dismissDelete(); return false; }
  return true;
}
function requestClose() {
  const entry = active;
  if (!entry || !canClose(entry)) return;
  if (entry.fromLink && location.hash === "#item/" + entry.id) history.back();
  else location.hash = entry.returnHash;
}
function findAnchor(entry) {
  if (entry.anchor?.isConnected) return entry.anchor;
  return [...entry.base.querySelectorAll('a[href^="#item/"]')].find(a => a.hash === "#item/" + entry.id) || null;
}
export async function openItemSheet(ctx, id, base) {
  if (active?.id === id) return;
  const token = ++serial;
  if (active && !await closeItemSheet()) return;
  if (token !== serial || location.hash !== "#item/" + id) return;
  const source = launch?.hash === location.hash && performance.now() - launch.time < 1800 ? launch : null;
  launch = null;
  const layer = document.createElement("div"); layer.className = "item-glass-layer";
  layer.innerHTML = `<div class="item-glass-veil" aria-hidden="true"></div><div class="item-morph-shell" aria-hidden="true"></div><section class="item-glass-panel" role="dialog" aria-modal="true" aria-label="衣物详情" tabindex="-1"><header class="item-glass-header"><span>衣物详情</span><button type="button" class="item-glass-close" aria-label="关闭衣物详情">${icon("close")}</button></header><div class="item-glass-scroll"><div class="item-glass-content" data-item-root></div></div></section><img class="item-flying-photo" alt="" aria-hidden="true">`;
  const origin = history.state?.itemOrigin?.id === id ? history.state.itemOrigin : null;
  const entry = { id, base, token, layer, anchor: source?.anchor, fromLink: Boolean(source) || Boolean(history.state?.wardrobeItem), returnHash: source?.returnHash || origin?.returnHash || base.dataset.routeHash || "#" + (base.dataset.view || "wardrobe"), phase: "loading", animations: [], scrollY: window.scrollY, dirty: false, historyState: history.state };
  entry.panel = layer.querySelector(".item-glass-panel"); entry.content = layer.querySelector(".item-glass-content"); entry.shell = layer.querySelector(".item-morph-shell"); entry.fly = layer.querySelector(".item-flying-photo"); entry.veil = layer.querySelector(".item-glass-veil");
  const sourceImage = entry.anchor?.querySelector("img");
  entry.sourceCard = rect(entry.anchor); entry.sourcePhoto = rect(sourceImage);
  if (sourceImage) entry.fly.src = sourceImage.currentSrc || sourceImage.src;
  active = entry; document.body.append(layer); background(entry, true);
  const viewport = () => {
    layer.style.setProperty("--item-viewport-h", (window.visualViewport?.height || innerHeight) + "px");
    layer.style.setProperty("--item-viewport-top", (window.visualViewport?.offsetTop || 0) + "px");
    if (entry.phase === "open") apply(entry.shell, { ...rect(entry.panel), borderRadius: 30 });
    if (entry.phase === "opening") {
      cancel(entry); apply(entry.shell, { ...rect(entry.panel), borderRadius: 30 }); entry.panel.style.opacity = "1"; entry.veil.style.opacity = "1";
      entry.background.forEach(({ node }) => node.style.filter = "blur(8px)");
      if (entry.hero) entry.hero.style.visibility = ""; entry.fly.style.visibility = "hidden"; entry.phase = "open";
    } else if (entry.phase === "closing") cancel(entry);
  };
  viewport();
  entry.onResize = viewport; window.addEventListener("resize", viewport); window.visualViewport?.addEventListener("resize", viewport);
  entry.onFocus = event => {
    if (active !== entry || document.querySelector(".bridge-modal,.import-modal")) return;
    const scope = entry.content.deleteDialog || entry.panel;
    if (!scope.contains(event.target)) scope.focus({ preventScroll: true });
  };
  entry.onKey = event => {
    if (active !== entry || document.querySelector(".bridge-modal,.import-modal")) return;
    if (event.key === "Escape") { event.preventDefault(); requestClose(); }
    if (event.key === "Tab") {
      const scope = entry.content.deleteDialog || entry.panel;
      const nodes = [...scope.querySelectorAll(focusSelector)].filter(n => n.getClientRects().length && !n.disabled && n.tabIndex >= 0);
      if (!nodes.length) { event.preventDefault(); scope.focus(); return; }
      if (event.shiftKey && (document.activeElement === nodes[0] || document.activeElement === scope)) { event.preventDefault(); nodes[nodes.length - 1].focus(); }
      else if (!event.shiftKey && document.activeElement === nodes[nodes.length - 1]) { event.preventDefault(); nodes[0].focus(); }
    }
  };
  document.addEventListener("focusin", entry.onFocus); document.addEventListener("keydown", entry.onKey);
  layer.querySelector(".item-glass-close").onclick = requestClose; entry.veil.onclick = requestClose;
  entry.content.addEventListener("item:updated", () => entry.dirty = true);
  entry.content.addEventListener("item:close", () => setTimeout(requestClose, 0));
  entry.content.addEventListener("item:outfit", event => {
    if (active !== entry || entry.content.detailGate?.busy || entry.phase === "closing") return;
    const itemOrigin = { id, returnHash: entry.returnHash, background: base.captureState?.() || null,
      detail: entry.content.captureDetail?.(), scrollY: entry.scrollY };
    entry.historyState = { ...history.state, wardrobeItem: entry.fromLink, itemOrigin };
    history.replaceState(entry.historyState, "", location.href);
    location.hash = "#outfits/" + encodeURIComponent(event.detail.id);
    history.replaceState({ outfitAnchorFrom: "#item/" + id }, "", location.href);
  });
  entry.panel.focus({ preventScroll: true });
  let loadingShown = false;
  const loadingTimer = setTimeout(() => {
    if (active !== entry || entry.phase !== "loading") return;
    loadingShown = true; entry.panel.style.opacity = "1"; entry.shell.style.visibility = "visible";
    apply(entry.shell, { ...rect(entry.panel), borderRadius: 30 }); entry.veil.style.opacity = "1";
    entry.background.forEach(({ node }) => node.style.filter = "blur(8px)");
  }, 150);
  try { await mount(entry.content, ctx, id, origin?.detail); } catch (error) { if (active === entry) entry.content.textContent = error.message || "暂时读不到衣物，请稍后重试"; }
  clearTimeout(loadingTimer);
  if (active !== entry || token !== serial) return;
  const hero = entry.content.querySelector(".detail-media img"), target = rect(entry.content.querySelector(".detail-media"));
  entry.hero = hero;
  entry.anchor?.classList.add("item-origin-hidden");
  entry.shell.style.visibility = "visible";
  if (loadingShown) { entry.phase = "open"; return; }
  const duration = reduced.matches ? 1 : 390, finish = rect(entry.panel);
  entry.phase = "opening";
  const start = entry.sourceCard || { ...finish, top: finish.top + 14, opacity: 0 };
  animate(entry, entry.shell, [{ ...start, borderRadius: 17 }, { ...finish, borderRadius: 30, opacity: 1 }], duration);
  animate(entry, entry.veil, [{ opacity: 0 }, { opacity: 1 }], duration);
  animate(entry, entry.panel, [{ opacity: 0, offset: 0 }, { opacity: 0, offset: .35 }, { opacity: 1, offset: 1 }], duration);
  for (const { node } of entry.background) animate(entry, node, [{ filter: "blur(0px)" }, { filter: "blur(8px)" }], duration);
  if (entry.sourcePhoto && target && sourceImage?.naturalWidth) {
    entry.fly.style.visibility = "visible"; if (hero) hero.style.visibility = "hidden";
    animate(entry, entry.fly, [{ ...entry.sourcePhoto, borderRadius: 16 }, { ...target, borderRadius: 20 }], duration);
  }
  await settle(entry, duration);
  if (active !== entry || entry.phase !== "opening") return;
  apply(entry.shell, { ...rect(entry.panel), borderRadius: 30, opacity: 1 }); entry.veil.style.opacity = "1"; entry.panel.style.opacity = "1";
  entry.background.forEach(({ node }) => node.style.filter = "blur(8px)");
  if (hero) hero.style.visibility = ""; entry.fly.style.visibility = "hidden"; cancel(entry); entry.phase = "open";
}

export async function closeItemSheet() {
  const entry = active;
  if (!entry) return true;
  if (entry.closing) return entry.closing;
  if (!canClose(entry)) return false;
  entry.closing = (async () => {
    ++serial;
    const anchor = findAnchor(entry), targetPhoto = rect(anchor?.querySelector("img")), target = rect(anchor);
    const shell = rect(entry.shell), panelOpacity = getComputedStyle(entry.panel).opacity, opacity = getComputedStyle(entry.veil).opacity;
    const flying = entry.phase === "opening" && entry.fly.style.visibility === "visible";
    const hero = entry.content.querySelector(".detail-media img"), imageRect = flying ? rect(entry.fly) : rect(hero);
    const imageSrc = flying ? entry.fly.src : hero?.currentSrc || hero?.src;
    const filters = entry.background.map(({ node }) => getComputedStyle(node).filter);
    cancel(entry); entry.phase = "closing";
    entry.content.dataset.closing = "true"; entry.panel.style.pointerEvents = "none";
    if (hero) hero.style.visibility = "hidden";
    const duration = reduced.matches ? 1 : 330;
    apply(entry.shell, shell); entry.panel.style.opacity = panelOpacity;
    animate(entry, entry.shell, [{ ...shell, borderRadius: 30, opacity: 1 }, target ? { ...target, borderRadius: 17, opacity: 1 } : { ...shell, top: shell.top + 12, opacity: 0 }], duration);
    animate(entry, entry.panel, [{ opacity: panelOpacity, offset: 0 }, { opacity: 0, offset: .35 }, { opacity: 0, offset: 1 }], duration, "linear");
    animate(entry, entry.veil, [{ opacity }, { opacity: 0 }], duration);
    entry.background.forEach(({ node }, i) => animate(entry, node, [{ filter: filters[i] }, { filter: "blur(0px)" }], duration));
    const clip = rect(entry.layer.querySelector(".item-glass-scroll"));
    const visibleHero = imageRect && clip && imageRect.top >= clip.top - 1 && imageRect.top + imageRect.height <= clip.top + clip.height + 1;
    if (targetPhoto && imageRect && imageSrc && (flying || visibleHero)) {
      entry.fly.src = imageSrc; entry.fly.style.visibility = "visible";
      animate(entry, entry.fly, [{ ...imageRect, borderRadius: 20, opacity: 1 }, { ...targetPhoto, borderRadius: 16, opacity: 1 }], duration);
    } else entry.fly.style.visibility = "hidden";
    await settle(entry, duration);
    if (active !== entry) return true;
    cancel(entry); entry.anchor?.classList.remove("item-origin-hidden");
    background(entry, false); entry.layer.remove(); active = null;
    document.removeEventListener("focusin", entry.onFocus); document.removeEventListener("keydown", entry.onKey);
    window.removeEventListener("resize", entry.onResize); window.visualViewport?.removeEventListener("resize", entry.onResize);
    window.scrollTo(0, entry.scrollY); anchor?.focus?.({ preventScroll: true });
    if (entry.dirty && entry.base.isConnected) {
      entry.base.dispatchEvent(new CustomEvent("wardrobe:refresh", { detail: { focusItemId: entry.id, deleted: Boolean(entry.content.deleted) } }));
      if (entry.content.deleted) entry.base.dispatchEvent(new CustomEvent("item:removed", { detail: { id: entry.id } }));
    }
    if (entry.dirty && entry.base.dataset.view !== "wardrobe") {
      for (const link of entry.base.querySelectorAll('a[href^="#item/"]')) {
        if (link.hash !== "#item/" + entry.id) continue;
        if (entry.content.deleted) { link.remove(); continue; }
        const item = entry.content.latestItem;
        if (!item) continue;
        const image = link.querySelector("img"), label = link.querySelector("span");
        if (label) label.textContent = item.name || item.type_label;
        if (image && api.presentationUrl(item)) { image.src = api.presentationUrl(item); image.dataset.flatlaySource = image.src; }
      }
    }
    return true;
  })();
  return entry.closing;
}
