/** §18：原图常显，单指拖框取景，双指以固定起始中点缩放。 */
const MAX_SIDE = 1600;
const DEFAULT_BOX = { x: .2, y: .2, w: .6, h: .6 };
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

function expand(box, factor = 1.5) {
  const cx = box.x + box.w / 2;
  const cy = box.y + box.h / 2;
  const w = Math.min(1, box.w * factor);
  const h = Math.min(1, box.h * factor);
  return {
    x: Math.max(0, Math.min(1 - w, cx - w / 2)),
    y: Math.max(0, Math.min(1 - h, cy - h / 2)),
    w,
    h,
  };
}

export async function cropFile(file, rect) {
  if (!rect || ["x", "y", "w", "h"].some(k => !Number.isFinite(rect[k])) || rect.w <= 0 || rect.h <= 0) throw new Error("裁剪建议格式不正确");
  const box = expand(rect, 1.5);
  const src = URL.createObjectURL(file);
  try {
    const image = new Image(); image.src = src; await image.decode();
    const sw = Math.max(1, Math.round(box.w * image.naturalWidth)), sh = Math.max(1, Math.round(box.h * image.naturalHeight));
    const scale = Math.min(1, MAX_SIDE / Math.max(sw, sh)), canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(sw * scale)); canvas.height = Math.max(1, Math.round(sh * scale));
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("无法生成裁剪预览");
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(image, Math.round(box.x * image.naturalWidth), Math.round(box.y * image.naturalHeight), sw, sh, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.92));
    if (!blob) throw new Error("裁剪图生成失败");
    return { file: new File([blob], "crop.jpg", { type: "image/jpeg" }), rect: box };
  } finally { URL.revokeObjectURL(src); }
}

export function openCropper({ src, initial = null, grow = true, title = "框出商品图" }) {
  return new Promise(resolve => {
    const layer = document.createElement("div");
    layer.className = "crop-layer is-open";
    layer.setAttribute("role", "dialog"); layer.setAttribute("aria-modal", "true");
    layer.setAttribute("aria-labelledby", "crop-title");
    layer.innerHTML = `
      <div class="crop-head"><strong id="crop-title"></strong><p data-hint>拖动框取景 · 双指只缩放图片</p></div>
      <div class="crop-stage">
        <div class="crop-window"><div class="crop-view"><img class="crop-img" alt="待裁剪的原图" draggable="false"><div class="crop-mask" hidden></div></div></div>
        <div class="crop-box" tabindex="0" role="group" aria-label="裁剪框，方向键移动，Alt加方向键调整大小" hidden>
          <i class="crop-h" data-h="nw" aria-hidden="true"></i><i class="crop-h" data-h="ne" aria-hidden="true"></i>
          <i class="crop-h" data-h="sw" aria-hidden="true"></i><i class="crop-h" data-h="se" aria-hidden="true"></i>
        </div>
      </div>
      <div class="crop-controls"><button type="button" data-zoom-out aria-label="缩小图片" disabled>−</button><output data-scale>100%</output><button type="button" data-zoom-in aria-label="放大图片" disabled>＋</button><button type="button" data-reset disabled>重置</button></div>
      <div class="crop-actions"><button type="button" class="ghost" data-cancel>取消</button><button type="button" class="primary" data-ok disabled>用这张</button></div>`;
    layer.querySelector("#crop-title").textContent = title;
    const before = document.activeElement, background = [...document.body.children];
    document.body.append(layer); document.body.classList.add("crop-open");
    layer.querySelector("[data-cancel]").focus({ preventScroll: true });
    const hiddenStates = background.map(node => [node, node.getAttribute("aria-hidden")]);
    background.forEach(node => node.setAttribute("aria-hidden", "true"));
    const $ = selector => layer.querySelector(selector);
    const stage = $(".crop-stage"), image = $(".crop-img"), view = $(".crop-view"), frame = $(".crop-box"), mask = $(".crop-mask");
    let box = initial && ["x", "y", "w", "h"].every(k => Number.isFinite(initial[k])) && initial.w > 0 && initial.h > 0 ? { ...initial } : { ...DEFAULT_BOX };
    box.w = clamp(box.w, .000001, 1); box.h = clamp(box.h, .000001, 1);
    box.x = clamp(box.x, 0, 1 - box.w); box.y = clamp(box.y, 0, 1 - box.h);
    if (initial && grow) box = expand(box);
    const openingBox = { ...box }, pointers = new Map();
    let st = { s: 1, x: 0, y: 0 }, base = 1, width = 0, height = 0;
    let ready = false, closed = false, exporting = false, gesture = null, blockSingle = false;
    const screenBox = () => ({ x: st.x + box.x * image.naturalWidth * st.s, y: st.y + box.y * image.naturalHeight * st.s, w: box.w * image.naturalWidth * st.s, h: box.h * image.naturalHeight * st.s });
    const fromScreen = f => {
      const iw = image.naturalWidth * st.s, ih = image.naturalHeight * st.s;
      box = { x: clamp((f.x - st.x) / iw, 0, 1), y: clamp((f.y - st.y) / ih, 0, 1), w: Math.min(1, f.w / iw), h: Math.min(1, f.h / ih) };
      box.x = Math.min(box.x, 1 - box.w); box.y = Math.min(box.y, 1 - box.h);
    };
    function paint() {
      view.style.width = `${image.naturalWidth}px`; view.style.height = `${image.naturalHeight}px`;
      view.style.transform = `translate(${st.x}px, ${st.y}px) scale(${st.s})`;
      const f = screenBox();
      Object.assign(frame.style, { transform: `translate(${f.x}px, ${f.y}px)`, width: `${f.w}px`, height: `${f.h}px` });
      Object.assign(mask.style, { left: `${box.x * image.naturalWidth}px`, top: `${box.y * image.naturalHeight}px`, width: `${box.w * image.naturalWidth}px`, height: `${box.h * image.naturalHeight}px` });
      mask.style.boxShadow = `0 0 0 ${Math.max(image.naturalWidth, image.naturalHeight) * 2}px rgba(0,0,0,.58)`;
      $("[data-scale]").textContent = `${Math.round(st.s / base * 100)}%`;
    }
    function fit() {
      const size = stage.getBoundingClientRect();
      if (!size.width || !size.height) return;
      if (ready && width === size.width && height === size.height) return;
      width = size.width; height = size.height; endGesture();
      base = Math.min(Math.max(1, width - 48) / image.naturalWidth, Math.max(1, height - 48) / image.naturalHeight);
      st = { s: base, x: (width - image.naturalWidth * base) / 2, y: (height - image.naturalHeight * base) / 2 };
      paint();
    }
    function zoom(scale, anchor, start, fixed) {
      const min = Math.max(base, fixed.w / image.naturalWidth, fixed.h / image.naturalHeight);
      const s = clamp(scale, min, Math.max(base * 4, min));
      const x = anchor.x - (anchor.x - start.x) / start.s * s;
      const y = anchor.y - (anchor.y - start.y) / start.s * s;
      st = { s, x: clamp(x, fixed.x + fixed.w - image.naturalWidth * s, fixed.x), y: clamp(y, fixed.y + fixed.h - image.naturalHeight * s, fixed.y) };
      fromScreen(fixed); paint();
    }
    function endGesture() {
      pointers.clear(); gesture = null; blockSingle = false; frame.classList.remove("dragging");
    }
    const point = event => { const r = stage.getBoundingClientRect(); return { x: event.clientX - r.left, y: event.clientY - r.top }; };
    const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    function down(event) {
      if (!ready || exporting || (event.pointerType === "mouse" && event.button !== 0)) return;
      event.preventDefault(); stage.setPointerCapture?.(event.pointerId);
      const p = point(event); pointers.set(event.pointerId, p);
      if (pointers.size >= 2) {
        blockSingle = true; frame.classList.remove("dragging");
        if (pointers.size === 2) {
          const [a, b] = [...pointers.values()];
          gesture = { kind: "pinch", distance: Math.max(1, distance(a, b)), anchor: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, st: { ...st }, frame: screenBox() };
        }
        return;
      }
      if (blockSingle) return;
      const f = screenBox(), handle = event.target.closest?.("[data-h]")?.dataset.h;
      if (!handle && !(p.x >= f.x && p.x <= f.x + f.w && p.y >= f.y && p.y <= f.y + f.h)) return;
      gesture = { kind: handle || "move", start: p, frame: f, id: event.pointerId };
      frame.classList.add("dragging");
    }
    function adjust(f, dx, dy, kind) {
      const loX = Math.max(24, st.x), loY = Math.max(24, st.y);
      const hiX = Math.min(width - 24, st.x + image.naturalWidth * st.s), hiY = Math.min(height - 24, st.y + image.naturalHeight * st.s);
      let x = f.x, y = f.y, right = f.x + f.w, bottom = f.y + f.h;
      if (kind === "move") {
        x = clamp(x + dx, loX, Math.max(loX, hiX - f.w)); y = clamp(y + dy, loY, Math.max(loY, hiY - f.h));
        right = x + f.w; bottom = y + f.h;
      } else {
        const minW = Math.min(88, kind.includes("w") ? right - loX : hiX - x), minH = Math.min(88, kind.includes("n") ? bottom - loY : hiY - y);
        if (kind.includes("w")) x = clamp(x + dx, loX, right - minW);
        if (kind.includes("e")) right = clamp(right + dx, x + minW, hiX);
        if (kind.includes("n")) y = clamp(y + dy, loY, bottom - minH);
        if (kind.includes("s")) bottom = clamp(bottom + dy, y + minH, hiY);
      }
      fromScreen({ x, y, w: right - x, h: bottom - y }); paint();
    }
    function move(event) {
      if (!pointers.has(event.pointerId) || exporting) return;
      event.preventDefault(); pointers.set(event.pointerId, point(event));
      if (gesture?.kind === "pinch" && pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        // §18：锚点取起始中点，移动两根手指的中点不产生平移。
        zoom(gesture.st.s * distance(a, b) / gesture.distance, gesture.anchor, gesture.st, gesture.frame);
      } else if (!blockSingle && gesture && gesture.id === event.pointerId) {
        const p = point(event); adjust(gesture.frame, p.x - gesture.start.x, p.y - gesture.start.y, gesture.kind);
      }
    }
    function up(event) {
      if (!pointers.has(event.pointerId)) return;
      pointers.delete(event.pointerId); gesture = null; frame.classList.remove("dragging");
      if (!pointers.size) blockSingle = false;
    }
    function finish(result) {
      if (closed) return;
      closed = true; endGesture(); observer?.disconnect();
      window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", up);
      window.removeEventListener("resize", resize); window.removeEventListener("blur", endGesture);
      window.removeEventListener("keydown", key); window.removeEventListener("hashchange", cancel);
      hiddenStates.forEach(([node, value]) => value === null ? node.removeAttribute("aria-hidden") : node.setAttribute("aria-hidden", value));
      layer.remove(); document.body.classList.remove("crop-open");
      if (before?.isConnected) before.focus({ preventScroll: true });
      resolve(result);
    }
    const cancel = () => finish(null);
    function key(event) {
      if (event.key === "Escape") { event.preventDefault(); cancel(); return; }
      if (event.key === "Tab") {
        const nodes = [...layer.querySelectorAll('button:not(:disabled), [tabindex="0"]')].filter(el => !el.hidden);
        const index = nodes.indexOf(document.activeElement), next = (index + (event.shiftKey ? -1 : 1) + nodes.length) % nodes.length;
        event.preventDefault(); nodes[next]?.focus(); return;
      }
      if (!ready || exporting || document.activeElement !== frame || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault(); endGesture();
      const step = event.shiftKey ? 10 : 1;
      adjust(screenBox(), event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0, event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0, event.altKey ? "se" : "move");
    }
    const resize = () => { if (ready && !exporting) fit(); };
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(resize) : null;
    observer?.observe(stage);
    stage.addEventListener("pointerdown", down);
    window.addEventListener("pointermove", move, { passive: false }); window.addEventListener("pointerup", up); window.addEventListener("pointercancel", up);
    window.addEventListener("resize", resize); window.addEventListener("blur", endGesture);
    window.addEventListener("keydown", key); window.addEventListener("hashchange", cancel);
    $("[data-cancel]").onclick = cancel;
    for (const [selector, factor] of [["[data-zoom-in]", 1.2], ["[data-zoom-out]", 1 / 1.2]]) $(selector).onclick = () => {
      if (!ready || exporting) return;
      endGesture(); const f = screenBox(); zoom(st.s * factor, { x: f.x + f.w / 2, y: f.y + f.h / 2 }, { ...st }, f);
    };
    $("[data-reset]").onclick = () => {
      if (!ready || exporting) return;
      box = { ...openingBox }; width = 0; fit();
    };
    $("[data-ok]").onclick = async () => {
      if (!ready || exporting) return;
      exporting = true; endGesture();
      layer.querySelectorAll("button:not([data-cancel])").forEach(el => el.disabled = true);
      const frozen = { ...box };
      try {
        const sw = Math.min(image.naturalWidth, Math.max(1, Math.round(frozen.w * image.naturalWidth)));
        const sh = Math.min(image.naturalHeight, Math.max(1, Math.round(frozen.h * image.naturalHeight)));
        const sx = clamp(Math.round(frozen.x * image.naturalWidth), 0, image.naturalWidth - sw), sy = clamp(Math.round(frozen.y * image.naturalHeight), 0, image.naturalHeight - sh);
        const scale = Math.min(1, MAX_SIDE / Math.max(sw, sh)), canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(sw * scale)); canvas.height = Math.max(1, Math.round(sh * scale));
        const ctx = canvas.getContext("2d");
        if (!ctx) throw new Error("无法生成裁剪图");
        ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(image, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
        const blob = await new Promise(done => canvas.toBlob(done, "image/jpeg", .92));
        if (!blob) throw new Error("裁剪图生成失败");
        finish({ blob, width: canvas.width, height: canvas.height, rect: frozen });
      } catch {
        if (closed) return;
        exporting = false;
        $("[data-hint]").textContent = "裁剪图生成失败，请重试或取消";
        layer.querySelectorAll("button").forEach(el => el.disabled = false);
      }
    };
    image.onload = () => {
      if (closed) return;
      fit(); ready = true; frame.hidden = false; mask.hidden = false;
      layer.querySelectorAll("button").forEach(el => el.disabled = false);
    };
    image.onerror = () => { if (!closed) $("[data-hint]").textContent = "照片暂时打不开，请取消后重试"; };
    image.src = src;
  });
}
