/** 商品图裁剪窗口（独立全屏浮层）。
 *
 * **为什么单独一个"过程"而不是在录入页里拖**（2026-10-21 用户拍板）：
 * 录入页那一片只有一张小缩略图，在里面拖框既看不清、又容易误触表单。
 * 裁剪要的是"整张图铺满屏幕、框外的都变暗"的那种专注感 —— 所以做成一个
 * 盖住全屏、自己带标题和按钮的窗口，确认后回到录入页。
 *
 * **为什么不走后端裁**：服务端**没装 Pillow**（`images.py:13` 明确不引入图像库），
 * 装它只为一个裁剪不值。canvas 本来就在浏览器里，`drawImage` 一步到位，
 * 还省了一次上传往返。
 *
 * 用法：
 *   const out = await openCropper({ src, initial });
 *   if (out) { ... out.blob / out.width / out.height ... }
 *
 * `src` 可以是 blob: URL（本地粘贴/选的文件）或同源 URL（手机相册那张）。
 * ⚠️ **必须同源**：跨源图片画进 canvas 会污染画布，`toBlob` 直接抛
 * `SecurityError`。相册图走的是 `/items/album/photo`，同源，没问题。
 */

/** 框的最小边长（像素）。44 是移动端触控目标的经验下限，再小手指捏不住。 */
const MIN_PX = 44;

/** 输出图片的最长边。订单截图的商品图本来就不大，1600 足够卡片和详情页用，
 *  又不会让裁剪后的文件比原图还大（`MAX_IMAGE_BYTES` 是 10 MB）。 */
const MAX_SIDE = 1600;

/** 放大镜的倍数（相对**屏幕**上的显示尺寸）。
 *  2.5 是权衡：手机上一张 864px 宽的截图显示在 390px 宽的屏上（≈0.45 倍），
 *  再乘 2.5 就是 1.1 倍原图像素 —— 刚好到"能看清边缘"又不至于糊成马赛克。 */
const ZOOM = 2.5;

/** 把 AI 给的框往外扩。
 *
 * **为什么必须扩**（2026-10-21 实测，同一张维动订单截图）：
 * 模型给 `w=0.25 h=0.12`（像素 216×230），而商品图实际约 287×309 ——
 * 它习惯**沿着商品本体描边**，不留一点余量。直接拿来裁会切掉鞋边/裤脚。
 *
 * 1.5 倍是实测下来能把"模型框"覆盖到"实际框"的系数（同一张图两次调用，
 * 模型给过 0.22 和 0.25 两种宽度；1.4 倍时右边仍会切掉商品图的白边）。
 * 它只是**起点**，用户还能拖 —— 所以宁可略大一点：往外拖比往里收容易，
 * 收的时候要精确压在商品边缘上。
 */
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

/** 没有 AI 框时的默认框：居中 60%。 */
const DEFAULT_BOX = { x: 0.2, y: 0.2, w: 0.6, h: 0.6 };

const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

/**
 * 打开裁剪窗口。
 *
 * @param src     图片地址（blob: / 同源 http）
 * @param initial AI 给的归一化框 `{x,y,w,h}`，可空
 * @returns `Promise<{blob, width, height} | null>` —— `null` = 用户选了「保持原图」
 */
export function openCropper({ src, initial = null }) {
  return new Promise((resolve) => {
    const layer = document.createElement("div");
    layer.className = "crop-layer";
    layer.innerHTML = `
      <div class="crop-head">
        <strong>框出商品图</strong>
        <p class="faint">拖四角或四边，放大镜会跟着你拖的那个角走</p>
      </div>
      <div class="crop-stage">
        <img class="crop-img" alt="">
        <div class="crop-zoom" aria-hidden="true">
          <div class="crop-zoom-frame"><canvas></canvas></div>
          <span class="crop-zoom-cap">放大镜 <b>—</b></span>
        </div>
      </div>
      <div class="crop-box" hidden>
        <i class="crop-h" data-h="nw"></i><i class="crop-h" data-h="n"></i><i class="crop-h" data-h="ne"></i>
        <i class="crop-h" data-h="w"></i><i class="crop-h" data-h="e"></i>
        <i class="crop-h" data-h="sw"></i><i class="crop-h" data-h="s"></i><i class="crop-h" data-h="se"></i>
      </div>
      <div class="crop-actions">
        <button type="button" class="ghost" data-cancel>保持原图</button>
        <button type="button" class="primary" data-ok disabled>用这张</button>
      </div>
      <div class="crop-tip faint" hidden>图片读不出来，可能已经不在了</div>
    `;
    document.body.appendChild(layer);
    // 遮罩是全屏 fixed，但滚轮/触摸仍能滚到底下的录入页 —— 拖框时整页跟着
    // 动一下的观感非常糟。锁住 body 的滚动，关窗口时再解开。
    document.body.classList.add("crop-open");

    const img = layer.querySelector(".crop-img");
    const boxEl = layer.querySelector(".crop-box");
    const okBtn = layer.querySelector("[data-ok]");
    const tip = layer.querySelector(".crop-tip");
    const zoomEl = layer.querySelector(".crop-zoom");
    const zoomFrame = layer.querySelector(".crop-zoom-frame");
    const zoomCanvas = zoomEl.querySelector("canvas");
    const zoomCap = zoomEl.querySelector(".crop-zoom-cap b");

    /** 当前框，**存的是相对图片的比例**（0~1），不是像素。
     *  这样窗口大小变了、图片重新布局了，框都还贴在同一个位置。 */
    let box = initial ? expand(initial) : { ...DEFAULT_BOX };
    let imgRect = null;
    /** 拖动中的临时状态。`null` = 没在拖。 */
    let drag = null;
    let done = false;
    /** 首帧标志：第一次量出图片尺寸时放一次「整图 → 目标框」的推近动画，
     *  之后的 resize 只重画，不再放动画。 */
    let firstPaint = true;
    /** 入场动画的进行态（`null` = 已收敛）。放动画时 `box` 是**整张图**，
     *  `intro.target` 才是真正的目标框 —— 中途被打断就靠它立刻归位。 */
    let intro = null;
    let zoomRaf = 0;
    /** 放大镜对准的点（归一化）。`null` = 对准框中心；
     *  拖过手柄之后会一直停在那个角上，方便反复微调同一处。 */
    let focus = null;

    const finish = (value) => {
      if (done) return;
      done = true;
      intro = null;
      if (zoomRaf) cancelAnimationFrame(zoomRaf);
      // 所有挂在 window 上的监听都要摘。`finish` 有四条路（取消 / 确认 /
      // Esc / 路由切换），漏摘任何一条都会让浮层关掉之后还在响应。
      window.removeEventListener("resize", onResize);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("pointerup", endDrag);
      document.body.classList.remove("crop-open");
      // 淡出 160ms 再真正删掉：直接 `remove()` 是"啪一下消失"，从全屏黑底
      // 切回白底表单会闪一下。
      // ⚠️ 这 170ms 里 `resolve` 还没发出去，调用方会一直等 —— 所以 `done`
      //    必须先置 true，否则连点两下会走两遍。
      layer.classList.add("leaving");
      window.setTimeout(() => {
        layer.remove();
        resolve(value);
      }, 170);
    };

    /** 把比例框转成屏幕坐标（视口坐标系，因为 box 元素是 `position: fixed`）。 */
    function renderBox() {
      if (!imgRect) return;
      boxEl.style.left = `${imgRect.left + box.x * imgRect.width}px`;
      boxEl.style.top = `${imgRect.top + box.y * imgRect.height}px`;
      boxEl.style.width = `${box.w * imgRect.width}px`;
      boxEl.style.height = `${box.h * imgRect.height}px`;
      scheduleZoom();
    }

    /** 入场动画：从整张图缩到目标框，像镜头推近。
     *  除了好看，它同时回答了"AI 圈的是哪一块" —— 用户不用满屏找框。 */
    function startIntro() {
      const target = box;
      box = { x: 0, y: 0, w: 1, h: 1 };
      renderBox();
      boxEl.classList.add("animating");
      // 用局部引用 `me` 而不是到处写 `intro.xxx`：`settleIntro` 会先把
      // `intro` 置 null，若这期间 rAF 才回调，`intro.raf = …` 就是
      // "给 null 赋值" 的 TypeError。
      const me = { target, timer: 0, raf: 0 };
      intro = me;
      me.raf = requestAnimationFrame(() => {
        if (!intro) return;
        // 双 rAF：第一帧只把"整张图"这个起点画出去，第二帧才改成目标框。
        // 同一帧里连着改两次内联样式，浏览器会把它们合并成一次计算样式，
        // transition 没有起点 —— 表现为"框直接出现，没有动画"。
        me.raf = requestAnimationFrame(() => {
          if (!intro) return;
          box = target;
          renderBox();
          me.timer = window.setTimeout(settleIntro, 470);
        });
      });
    }

    /** 让入场动画立刻收敛到目标框。
     *  拖动或关闭时必须调用：否则 `box` 还停在"整张图"，一按就跳。 */
    function settleIntro() {
      if (!intro) return;
      const t = intro;
      intro = null;
      cancelAnimationFrame(t.raf);
      clearTimeout(t.timer);
      box = t.target;
      boxEl.classList.remove("animating");
      renderBox();
      placeZoom(null);
      zoomEl.classList.add("show");
      drawZoom();
    }

    /** 画右上角的「框内放大」。
     *  rAF 合帧：拖动时 `pointermove` 一秒几十次，每次都 `drawImage` 是白烧 CPU，
     *  而屏幕一帧只看得见一次。 */
    function scheduleZoom() {
      if (zoomRaf) return;
      zoomRaf = requestAnimationFrame(() => {
        zoomRaf = 0;
        drawZoom();
      });
    }

    function drawZoom() {
      // 入场动画途中不画：那时 `box` 还是整张图，画出来是从大缩到小，没意义。
      if (done || !imgRect || !img.naturalWidth || intro) return;
      const cw = zoomFrame.clientWidth;
      const ch = zoomFrame.clientHeight;
      if (!cw || !ch) return;

      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const bw = Math.round(cw * dpr);
      const bh = Math.round(ch * dpr);
      // 只在尺寸真变了才赋值：给 `canvas.width` 赋值会清空画布
      if (zoomCanvas.width !== bw) zoomCanvas.width = bw;
      if (zoomCanvas.height !== bh) zoomCanvas.height = bh;
      const ctx = zoomCanvas.getContext("2d");
      if (!ctx) return;

      // 主图 1 个屏幕像素 = 多少原图像素。横竖相等（图片没被拉伸），取宽度算即可。
      const natPerCss = img.naturalWidth / imgRect.width;
      // 面板（正方形）要盖住多宽的一块原图：**CSS 尺寸** ÷ 倍数。
      // ⚠️ 这里必须用 CSS 像素（cw），不能用画布缓冲尺寸（bw）—— bw = cw × dpr，
      //    在 2 倍屏上会把 span 算大一倍，实际倍数就变成 ZOOM/dpr（实测手机 ×1.3）。
      // 也不能超过原图本身，否则 `drawImage` 会画出图片外的空白。
      const span = clamp(
        (Math.min(cw, ch) * natPerCss) / ZOOM,
        1,
        Math.min(img.naturalWidth, img.naturalHeight)
      );

      const c = focus || { x: box.x + box.w / 2, y: box.y + box.h / 2 };
      // 贴近图片边界时**平移**取景窗，而不是压缩到边界内 ——
      // 压缩会让边缘那圈变形，正好是要看细节的地方。
      const sx = clamp(c.x * img.naturalWidth - span / 2, 0, img.naturalWidth - span);
      const sy = clamp(c.y * img.naturalHeight - span / 2, 0, img.naturalHeight - span);

      ctx.fillStyle = "#0b0c10";
      ctx.fillRect(0, 0, bw, bh);
      try {
        ctx.drawImage(img, sx, sy, span, span, 0, 0, bw, bh);
      } catch {
        // 画布被跨源图片污染（SecurityError）。两条图源都同源，理论上不会发生；
        // 真发生了也只是少个放大镜，不能让拖动跟着挂掉。
        return;
      }

      // 十字线：没有它就不知道放大镜中心对着哪一点，画面在动但看不出在拖哪儿。
      ctx.strokeStyle = "rgba(99, 179, 255, .85)";
      ctx.lineWidth = Math.max(1, Math.round(dpr));
      ctx.beginPath();
      ctx.moveTo(bw / 2, 0);
      ctx.lineTo(bw / 2, bh);
      ctx.moveTo(0, bh / 2);
      ctx.lineTo(bw, bh / 2);
      ctx.stroke();
      // 交点挖个小洞，双眼更容易对准
      const hole = 2 * dpr;
      ctx.fillStyle = "rgba(11, 12, 16, .92)";
      ctx.fillRect(bw / 2 - hole, bh / 2 - hole, hole * 2, hole * 2);

      // 实际倍数 = 画布每个原图像素占几个缓冲像素 ÷ 每个屏幕像素占几个缓冲像素
      const actual = ((bw / span) * natPerCss) / dpr;
      zoomCap.textContent = `×${actual.toFixed(1)}`;
    }

    /** 重新量图片位置。图片加载完、窗口尺寸变化（手机转屏）都要调。 */
    function measure() {
      if (!img.naturalWidth) return;
      // ⚠️ 先算临时矩形、校验通过再赋给 `imgRect`。反过来的话（先赋值再判零）
      //   会在"图片被压成 0 尺寸"时留下一个 0 宽基准，之后拖动
      //   `dx = 位移 / 0` 得到 Infinity/NaN，框跳到边界或冻在 `NaNpx`。
      const rect = img.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) {
        imgRect = null;
        boxEl.hidden = true;
        okBtn.disabled = true;
        return;
      }
      imgRect = rect;
      boxEl.hidden = false;
      okBtn.disabled = false;
      if (firstPaint) {
        firstPaint = false;
        startIntro();
      } else if (!intro) {
        renderBox();
      }
    }

    img.addEventListener("load", measure);
    img.addEventListener("error", () => {
      tip.hidden = false;
      okBtn.disabled = true;
    });
    img.src = src;

    // 用 `function` 声明而不是 `const`：`finish()` 里要摘这两个监听，
    // 而 `finish` 定义在上面。函数声明会提升，`const` 不会（TDZ）——
    // 现在没炸只是因为三条路都是用户点的，但换个触发时机就会 ReferenceError。
    function onResize() {
      measure();
    }
    window.addEventListener("resize", onResize);

    /** 手柄 → 它所在的那个角（归一化坐标）。
     *  放大镜跟着这个点走：拖右下角，看的就是右下角那片像素。 */
    function cornerOf(mode) {
      const x = mode.includes("w") ? box.x
        : mode.includes("e") ? box.x + box.w
        : box.x + box.w / 2;
      const y = mode.includes("n") ? box.y
        : mode.includes("s") ? box.y + box.h
        : box.y + box.h / 2;
      return { x, y };
    }

    /** 放大镜该躲到哪个角：拖哪个手柄就躲到**对面**，别盖住手指。
     *  手机上图片铺满屏宽，面板一压就是压在手柄上 —— 这条不是润色。 */
    const AVOID = {
      nw: "br", n: "br", ne: "bl",
      w: "tr", e: "tl",
      sw: "tr", s: "tr", se: "tl",
    };

    function placeZoom(mode) {
      const pos = (mode && AVOID[mode]) || "tr";
      if (zoomEl.dataset.pos !== pos) zoomEl.dataset.pos = pos;
    }

    // -- 拖动 -------------------------------------------------------------
    //
    // 用 Pointer Events 而不是 mouse/touch 两套：一套代码同时吃鼠标、触摸屏、
    // 触控笔，还自带 setPointerCapture（手指划出框外也不会丢事件）。
    layer.addEventListener("pointerdown", (ev) => {
      if (!imgRect || done) return;
      // 入场动画还没走完就按下了：先让框立刻归位再进入拖动。
      // 不归位的话 `drag.box` 拿到的是"整张图"，手指一动框会跳回全屏。
      settleIntro();
      const handle = ev.target.closest("[data-h]");
      const onBox = ev.target.closest(".crop-box");
      // 框内既有手柄也有空白：手柄优先，其次是"拖动整体"
      const mode = handle ? handle.dataset.h : onBox ? "move" : null;
      if (!mode) return;

      ev.preventDefault();
      drag = {
        mode,
        startX: ev.clientX,
        startY: ev.clientY,
        box: { ...box },
      };
      // 捕获到 layer：手指移出窗口也能继续收到 move/up。
      // ⚠️ 必须包 try：`setPointerCapture` 对**合成事件**（自动化测试派发的
      // PointerEvent）会抛 NotFoundError，不包就会让第一次按下就中断拖动。
      try {
        layer.setPointerCapture?.(ev.pointerId);
      } catch {
        /* 拿不到捕获也能拖，只是手指移出窗口时会断 —— 不该因此报错 */
      }
      // 放大镜对准正在拖的那个角；拖整框时回到中心
      focus = mode === "move" ? null : cornerOf(mode);
      placeZoom(mode);
      scheduleZoom();
      boxEl.classList.add("dragging");
    });

    layer.addEventListener("pointermove", (ev) => {
      if (!drag || !imgRect) return;
      const dx = (ev.clientX - drag.startX) / imgRect.width;
      const dy = (ev.clientY - drag.startY) / imgRect.height;
      const b = drag.box;
      const minW = MIN_PX / imgRect.width;
      const minH = MIN_PX / imgRect.height;
      const m = drag.mode;

      if (m === "move") {
        box = {
          ...b,
          x: clamp(b.x + dx, 0, 1 - b.w),
          y: clamp(b.y + dy, 0, 1 - b.h),
        };
      } else {
        let { x, y, w, h } = b;
        // 拖左边：左边界动，同时从宽度里扣掉同样的量（右边界固定）
        if (m.includes("w")) {
          const nx = clamp(b.x + dx, 0, b.x + b.w - minW);
          w = b.x + b.w - nx;
          x = nx;
        }
        if (m.includes("e")) {
          w = clamp(b.w + dx, minW, 1 - b.x);
        }
        if (m.includes("n")) {
          const ny = clamp(b.y + dy, 0, b.y + b.h - minH);
          h = b.y + b.h - ny;
          y = ny;
        }
        if (m.includes("s")) {
          h = clamp(b.h + dy, minH, 1 - b.y);
        }
        box = { x, y, w, h };
        // 角跟着框动，放大镜要一直盯着这个角
        focus = cornerOf(m);
      }
      renderBox();
    });

    function endDrag() {
      drag = null;
      boxEl.classList.remove("dragging");
    }
    layer.addEventListener("pointerup", endDrag);
    layer.addEventListener("pointercancel", endDrag);
    // 兜底：`setPointerCapture` 失败时（合成事件 / 老浏览器），手指移出浮层
    // 再抬起，pointerup 不一定会派发到 layer —— 那样 `drag` 一直非 null，
    // 之后鼠标划过浮层就会莫名其妙改框。
    window.addEventListener("pointerup", endDrag);

    // -- 确认 / 取消 -------------------------------------------------------

    layer.querySelector("[data-cancel]").addEventListener("click", () => finish(null));

    okBtn.addEventListener("click", async () => {
      if (!imgRect || !img.naturalWidth) return;
      okBtn.disabled = true;
      okBtn.textContent = "处理中…";

      // 比例 → 原图像素。**必须用 naturalWidth**，不是屏幕上的显示尺寸，
      // 否则裁出来是一张和缩略图一样大的糊图。
      const sx = Math.round(box.x * img.naturalWidth);
      const sy = Math.round(box.y * img.naturalHeight);
      const sw = Math.max(1, Math.round(box.w * img.naturalWidth));
      const sh = Math.max(1, Math.round(box.h * img.naturalHeight));

      // 超大图按最长边缩一下。不缩的话一张 4000px 的图裁完还是几 MB，
      // 上传会撞 `MAX_IMAGE_BYTES`，而用户看不出为什么"保存失败"。
      const k = Math.min(1, MAX_SIDE / Math.max(sw, sh));
      const outW = Math.max(1, Math.round(sw * k));
      const outH = Math.max(1, Math.round(sh * k));

      const canvas = document.createElement("canvas");
      canvas.width = outW;
      canvas.height = outH;
      let blob = null;
      try {
        const ctx = canvas.getContext("2d");
        if (ctx) {
          // 透明背景填白：PNG 截图裁出来是 JPEG，不填白会变黑底
          ctx.fillStyle = "#fff";
          ctx.fillRect(0, 0, outW, outH);
          ctx.drawImage(img, sx, sy, sw, sh, 0, 0, outW, outH);
          blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.92));
        }
      } catch {
        // `drawImage` 抛错（位图不可用 / canvas 分配失败）或画布被跨源图片污染
        // （`SecurityError`）。两条图源理论上都同源，但真发生了不能让窗口卡住。
        //
        // ⚠️ 整段必须在 try 里：只包 `toBlob` 的话，`drawImage` 抛出的异常会
        //   reject 掉这个 async click handler，`finish` 永不执行 ——
        //   浮层关不掉、`body` 永久 `overflow:hidden`、按钮停在"处理中…"。
        blob = null;
      }
      finish(blob ? { blob, width: outW, height: outH } : null);
    });

    // Esc = 保持原图（桌面端习惯）。不加的话只有点按钮一条路。
    function onKey(ev) {
      if (ev.key === "Escape") finish(null);
    }
    window.addEventListener("keydown", onKey);

    // 路由切走（Android 返回键 / 点导航）→ 关窗。
    //
    // 浮层挂在 `body` 上，不受 `#view` 的 `innerHTML` 替换影响 —— 不关的话它会
    // 盖在新页面上；用户再点「用这张」，`acceptImage` 会写进一个**已经脱离文档**
    // 的 input，裁剪结果丢失，还在新页面上弹一句 toast。
    function onHash() {
      finish(null);
    }
    window.addEventListener("hashchange", onHash);
  });
}
