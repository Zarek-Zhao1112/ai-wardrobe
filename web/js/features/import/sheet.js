// §22：录入驻留在独立浮层中，收起不销毁表单与图片。
import { mount, addSaving, checkpointAdd } from "./view.js";
import { toast } from "../../core/ui.js";

let layer, content, panel, scroll, previousFocus, background = [], focusables = [], scrollY = 0;
let mounted, visible = false, closing = false, drag = null;
let viewportMaxHeight = 0, viewportWidth = 0;
const focusSelector = 'a[href],button,input,select,textarea,summary,[tabindex]';
const topLayer = () => document.querySelector(".crop-layer,.import-modal,.bridge-modal");
export const importSheetOpen = () => visible;
export const importSheetRoot = () => content;

function viewport() {
  if (!layer) return;
  const vv = window.visualViewport;
  const width = vv?.width || innerWidth, height = vv?.height || innerHeight;
  // Android adjustResize 会同时缩小 innerHeight，需保留同一方向的完整高度。
  if (Math.abs(width - viewportWidth) > 40) viewportMaxHeight = height;
  else viewportMaxHeight = Math.max(viewportMaxHeight, height);
  viewportWidth = width;
  layer.style.setProperty("--import-viewport-h", `${vv?.height || innerHeight}px`);
  layer.style.setProperty("--import-viewport-top", `${vv?.offsetTop || 0}px`);
  if (visible && !topLayer() && content.contains(document.activeElement)) {
    const focused = document.activeElement;
    const rect = focused.getBoundingClientRect(), bounds = scroll.getBoundingClientRect();
    if (rect.bottom > bounds.bottom || rect.top < bounds.top) requestAnimationFrame(() => focused.scrollIntoView({ block: "nearest" }));
  }
  cancelDrag();
}
function cancelDrag() {
  drag = null;
  panel?.classList.remove("is-dragging");
  panel?.style.removeProperty("--import-drag");
}
function requestClose() {
  if (!visible || closing || topLayer()) return;
  if (addSaving(content)) { toast("正在保存，请稍候"); return; }
  if (history.state?.wardrobeImport) history.back();
  else location.hash = "#wardrobe";
}
function create(ctx) {
  layer = document.createElement("div");
  layer.className = "wardrobe-import-layer"; layer.hidden = true;
  layer.innerHTML = `<div class="wardrobe-import-scrim" aria-hidden="true"></div>
    <section class="wardrobe-import-panel" role="dialog" aria-modal="true" aria-labelledby="wardrobe-import-title" tabindex="-1">
      <header class="wardrobe-import-header"><div class="wardrobe-import-handle" aria-hidden="true"></div>
        <div><h2 id="wardrobe-import-title">录入衣物</h2><button type="button" class="ghost" data-import-close aria-label="收起录入面板">×</button></div>
      </header>
      <div class="wardrobe-import-scroll"><div class="wardrobe-import-content" data-view="add" data-embedded="true"></div></div>
    </section>`;
  document.body.append(layer);
  panel = layer.querySelector(".wardrobe-import-panel");
  content = layer.querySelector(".wardrobe-import-content");
  scroll = layer.querySelector(".wardrobe-import-scroll");
  layer.querySelector("[data-import-close]").onclick = requestClose;
  layer.querySelector(".wardrobe-import-scrim").onclick = requestClose;
  content.addEventListener("import:close", requestClose);
  const header = layer.querySelector(".wardrobe-import-header");
  header.addEventListener("pointerdown", event => {
    if (drag || !event.isPrimary) { cancelDrag(); return; }
    const keyboard = window.visualViewport && viewportMaxHeight - visualViewport.height > 120;
    if (event.button !== 0 || event.target.closest("button,a,input") || keyboard || topLayer() || addSaving(content)) return;
    drag = { id: event.pointerId, y: event.clientY, x: event.clientX, distance: 0 };
    header.setPointerCapture(event.pointerId); panel.classList.add("is-dragging");
  });
  header.addEventListener("pointermove", event => {
    if (drag?.id !== event.pointerId) return;
    if (Math.abs(event.clientX - drag.x) > 40) { cancelDrag(); return; }
    drag.distance = Math.max(0, event.clientY - drag.y);
    panel.style.setProperty("--import-drag", `${drag.distance}px`);
  });
  header.addEventListener("pointerup", () => { const close = drag?.distance > 100; cancelDrag(); if (close) requestClose(); });
  header.addEventListener("pointercancel", cancelDrag);
  document.addEventListener("pointerdown", event => { if (drag && event.pointerId !== drag.id) cancelDrag(); }, true);
  document.addEventListener("keydown", event => {
    if (!visible || topLayer()) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); requestClose(); }
    if (event.key !== "Tab") return;
    const nodes = [...panel.querySelectorAll(focusSelector)].filter(node => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length);
    if (!nodes.length) { event.preventDefault(); panel.focus(); return; }
    if (event.shiftKey && (document.activeElement === nodes[0] || document.activeElement === panel)) { event.preventDefault(); nodes[nodes.length - 1].focus(); }
    else if (!event.shiftKey && (document.activeElement === nodes[nodes.length - 1] || !panel.contains(document.activeElement))) { event.preventDefault(); nodes[0].focus(); }
  }, true);
  document.addEventListener("focusin", event => {
    if (visible && !topLayer() && !panel.contains(event.target)) panel.focus({ preventScroll: true });
  });
  window.addEventListener("resize", viewport);
  window.visualViewport?.addEventListener("resize", viewport);
  window.visualViewport?.addEventListener("scroll", viewport);
  document.addEventListener("visibilitychange", () => { cancelDrag(); if (document.hidden && visible) void checkpointAdd(content); });
  mounted = mount(content, ctx);
}

export async function openImportSheet(ctx) {
  if (!layer) create(ctx);
  await mounted;
  if (location.hash !== "#add" || visible) return;
  previousFocus = document.activeElement;
  scrollY = window.scrollY;
  background = [...document.querySelectorAll("body > .topbar,body > #view,body > #tabbar")].map(node => [node, node.getAttribute("aria-hidden")]);
  focusables = background.flatMap(([node]) => [...node.querySelectorAll(focusSelector)].map(el => [el, el.getAttribute("tabindex")]));
  for (const [node] of background) node.setAttribute("aria-hidden", "true");
  for (const [node] of focusables) node.setAttribute("tabindex", "-1");
  visible = true; content.dataset.sheetActive = "true"; layer.hidden = false;
  layer.removeAttribute("aria-hidden");
  document.body.classList.add("import-sheet-open");
  viewport();
  // 两帧确保初次打开也能从底部连续浮起。
  requestAnimationFrame(() => requestAnimationFrame(() => { if (visible) layer.classList.add("is-open"); }));
  panel.focus({ preventScroll: true });
}

export async function closeImportSheet() {
  if (!visible) return true;
  if (closing) return false;
  if (topLayer()) { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); return false; }
  if (addSaving(content)) { toast("正在保存，请稍候"); return false; }
  closing = true;
  try {
    if (!await checkpointAdd(content)) return false;
    if (addSaving(content)) { toast("正在保存，请稍候"); return false; }
    // 检查点期间又回到录入路由，就保留打开状态。
    if (location.hash === "#add") return false;
    visible = false; content.dataset.sheetActive = "false"; cancelDrag(); layer.classList.remove("is-open");
    layer.setAttribute("aria-hidden", "true");
    document.body.classList.remove("import-sheet-open");
    for (const [node, value] of background) { if (value === null) node.removeAttribute("aria-hidden"); else node.setAttribute("aria-hidden", value); }
    for (const [node, value] of focusables) { if (value === null) node.removeAttribute("tabindex"); else node.setAttribute("tabindex", value); }
    background = []; focusables = [];
    window.scrollTo(0, scrollY);
    previousFocus?.focus?.({ preventScroll: true });
    if (content.dataset.wardrobeDirty === "true") {
      content.dataset.wardrobeDirty = "false";
      document.getElementById("view")?.dispatchEvent(new Event("wardrobe:refresh"));
    }
    const hide = () => { if (!visible) layer.hidden = true; };
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) hide(); else setTimeout(hide, 450);
    return true;
  } finally { closing = false; }
}
