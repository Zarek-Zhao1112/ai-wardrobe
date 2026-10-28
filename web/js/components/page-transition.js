/** §21：旧页可视克隆挖出圆形透明区，露出真实新页；不改变实际页面层级。 */
const DURATION = 600, FEATHER = 4;
const reduced = matchMedia("(prefers-reduced-motion: reduce)");
let active = null;

export function canReveal(root, bar) {
  return !reduced.matches && typeof requestAnimationFrame === "function" &&
    CSS.supports("-webkit-mask-image", "radial-gradient(circle, transparent 0px, black 4px)") &&
    bar?.getBoundingClientRect().height > 0 && !root.classList.contains("import-view") &&
    !document.querySelector(".import-modal,.crop-layer,.bridge-modal,.wardrobe-import-layer.is-open,.item-glass-layer") && !document.body.classList.contains("crop-open");
}

export function revealRunning() { return !!active; }

export function capturePage(elements) {
  const cover = document.createElement("div"), scroll = [];
  cover.className = "page-turn-old";
  cover.setAttribute("aria-hidden", "true");
  cover.setAttribute("inert", "");
  for (const source of elements) {
    if (!source) continue;
    const rect = source.getBoundingClientRect(), clone = source.cloneNode(true);
    const originals = [source, ...source.querySelectorAll("*")], copies = [clone, ...clone.querySelectorAll("*")];
    originals.forEach((node, index) => {
      const copy = copies[index];
      // 去ID后保留现有ID选择器参与的布局，避免旧画面在展开过程中换排。
      const layout = { "add-submit": ["flex"], "add-hint": ["flex-basis"], "o-message": ["margin-bottom"] }[node.id];
      if (layout) {
        const computed = getComputedStyle(node);
        layout.forEach(property => copy.style.setProperty(property, computed.getPropertyValue(property)));
      }
      copy.removeAttribute("id"); copy.removeAttribute("autofocus");
      if (copy.matches("a,button,input,select,textarea,summary,[tabindex],[contenteditable]")) copy.setAttribute("tabindex", "-1");
      if (copy.hasAttribute("contenteditable")) copy.setAttribute("contenteditable", "false");
      if (node instanceof HTMLSelectElement) copy.selectedIndex = node.selectedIndex;
      if (node instanceof HTMLTextAreaElement) copy.value = node.value;
      if (node.scrollTop || node.scrollLeft) scroll.push([copy, node.scrollTop, node.scrollLeft]);
    });
    Object.assign(clone.style, { position: "absolute", top: `${rect.top}px`, left: `${rect.left}px`,
      width: `${rect.width}px`, height: `${rect.height}px`, margin: "0", right: "auto", bottom: "auto" });
    cover.append(clone);
  }
  return { cover, scroll };
}

// cubic-bezier(.4,0,.2,1)：先求时间对应的参数，再取纵向进度。
function easing(time) {
  let low = 0, high = 1, t = time;
  for (let i = 0; i < 12; i++) {
    const x = 3 * (1 - t) ** 2 * t * .4 + 3 * (1 - t) * t ** 2 * .2 + t ** 3;
    if (x < time) low = t; else high = t;
    t = (low + high) / 2;
  }
  return 3 * (1 - t) * t ** 2 + t ** 3;
}

export function cancelReveal() { active?.finish(false); }

export function revealPage(snapshot, point, root, onDone) {
  cancelReveal();
  const { cover, scroll } = snapshot;
  const width = innerWidth, height = innerHeight;
  const x = Math.max(0, Math.min(width, point.x)), y = Math.max(0, Math.min(height, point.y));
  const radius = Math.max(Math.hypot(x, y), Math.hypot(width - x, y),
    Math.hypot(x, height - y), Math.hypot(width - x, height - y)) + FEATHER + 2;
  let frame = 0, timer = 0, start = null, ended = false;
  const block = event => { event.preventDefault(); event.stopImmediatePropagation(); };
  const focus = () => document.querySelector("#tabbar [aria-current=page]")?.focus({ preventScroll: true });
  const finish = notify => {
    if (ended) return;
    ended = true; cancelAnimationFrame(frame); clearTimeout(timer); cover.remove();
    root.classList.remove("page-turn-new"); root.removeAttribute("aria-busy");
    for (const type of ["click", "pointerdown", "keydown"]) root.removeEventListener(type, block, true);
    root.removeEventListener("focusin", focus, true);
    if (active?.finish === finish) {
      active = null;
      document.documentElement.classList.remove("page-turning");
      if (notify) onDone();
    }
  };
  try {
    document.body.append(cover);
    scroll.forEach(([node, top, left]) => { node.scrollTop = top; node.scrollLeft = left; });
    root.classList.add("page-turn-new"); root.setAttribute("aria-busy", "true");
    for (const type of ["click", "pointerdown", "keydown"]) root.addEventListener(type, block, true);
    root.addEventListener("focusin", focus, true);
    document.documentElement.classList.add("page-turning");
    active = { finish };
    const step = now => {
      if (ended) return;
      try {
        if (start === null) start = now;
        const progress = Math.min(1, (now - start) / DURATION), r = easing(progress) * radius;
        const mask = `radial-gradient(circle at ${x}px ${y}px, transparent ${r}px, black ${r + FEATHER}px)`;
        cover.style.webkitMaskImage = mask; cover.style.maskImage = mask;
        if (progress >= 1) finish(true); else frame = requestAnimationFrame(step);
      } catch { finish(true); }
    };
    // 后台节流或浏览器未继续绘制时，也有独立清理上限。
    timer = setTimeout(() => finish(true), DURATION + 350);
    frame = requestAnimationFrame(step);
  } catch { finish(true); }
}
