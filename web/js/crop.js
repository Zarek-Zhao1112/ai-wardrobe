/** 商品图裁剪窗口（独立全屏浮层）。
 *
 * **为什么单独一个"过程"而不是在录入页里拖**（2026-10-21 用户拍板）：
 * 录入页那一片只有一张小缩略图，在里面拖框既看不清、又容易误触表单。
 * 裁剪要的是"整张图铺满屏幕、框外的都变暗"的那种专注感 —— 所以做成一个
 * 盖住全屏、自己带标题和按钮的窗口，确认后回到录入页。
 *
 * **为什么不走后端裁**：服务端**没装 Pillow**（`images.py:13` 明确不引入图像库），
 * 装它只为裁剪不值。canvas 本来就在浏览器里，`drawImage` 一步到位，
 * 还省了一次上传往返。
 *
 * ------------------------------------------------------------------
 * 两种视图（2026-10-21 第二轮改版，用户要的）
 * ------------------------------------------------------------------
 * `peek`（静息态）：**框里那块铺满整个显示区**，框外的部分用与背景相同的
 *   颜色盖掉。用户不用眯着眼看缩略图，直接看到"存进衣橱的会是这个样子"，
 *   而且是屏幕上能给到的最大尺寸。
 * `edit`（调整态）：整张图缩到显示区里，框 + 八个手柄 + 框外填底色。
 *   手指一按下就切过来，**松手后再切回 peek**，于是"拖动时能看见框外被
 *   藏起来的那部分"和"平时看到的是放大后的选区"两件事同时成立。
 *
 * ★ 为什么切到 edit 是**瞬间**的、回 peek 才有动画：
 *   peek 里那张图被放大过（几张到十几张），edit 才是 1:1。拖动的手感必须
 *   按 1:1 算，否则"挪 1px 框跳 8px"。如果 edit 也做过渡，那过渡的这 200ms
 *   里框（按 1:1 定位）和图片（还在缩放途中）是**错位**的，一按下去就看见
 *   两个框在打架。回 peek 没有精度要求，才做成动画。
 *
 * ★ 为什么"框外填底色"要和图片挂在**同一个变换层**（`.crop-view`）里：
 *   填色如果留在帧上、按屏幕坐标画，那么 peek 放大之后它的位置就是**旧的**
 *   —— 实测表现为选区左右各露出一截没选中的订单页白边。放进变换层、用
 *   图片本地坐标，它就永远贴着"选中的那块"，任何视图、任何动画中间都对。
 *   而框的八个手柄**不能**放在变换层里：有一半伸在框外，会被裁剪容器切掉，
 *   而且 peek 里会跟着放大好几倍。所以手柄留在帧上，只出现在 edit 视图
 *   （那时变换就是纯平移，两套坐标只差一个 `ox/oy`）。
 *
 * 用法：
 *   const out = await openCropper({ src, initial, grow });
 *   if (out) { out.blob / out.width / out.height / out.rect }
 *
 *   `out.rect` 是**最终生效的归一化框**，调用方存下来，下次带着它再开一次
 *   就能接着调（"截取后仍可再次调整选区"就是这个用途）。
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

/** peek 推近（整图 → 选区铺满）的时长。**只用于进场和"松手回弹"**，
 *  切到 edit 是瞬间的，理由见文件头。 */
const ZOOM_MS = 460;

/** 松手后多久回到 peek。
 *  不是 0：用户经常要连着调两下（拖完整体再收一个角），立刻放大回去会让人
 *  看不清刚才调到了哪儿。也不是 2 秒以上：那样松手后画面停在那儿不动，
 *  像是"卡住了"。这段时间内再按下去会取消回弹，继续在 edit 里调。 */
const PEEK_DELAY = 900;

/** 显示区四周留给手柄的余量（px）。手柄 30px、中心压在框边上，
 *  所以每边 14px 刚好让最外侧那半个方块不越出舞台。 */
const PAD = 14;

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
 *
 * ⚠️ **只对 AI 给的框用**。上次裁剪的结果（`rect`）原样带回来，再扩一次就
 * 会每开一次大一圈 —— 所以调用方必须传 `grow: false`。
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
 * @param initial 归一化框 `{x,y,w,h}`，可空
 * @param grow    `initial` 是否算"AI 给的、要外扩"（默认 true）。
 *                传回上次的 `rect` 时必须传 false。
 * @returns `Promise<{blob, width, height, rect} | null>` —— `null` = 用户选了「保持原图」
 */
export function openCropper({ src, initial = null, grow = true }) {
  return new Promise((resolve) => {
    const layer = document.createElement("div");
    // 一开始就是 peek：图还没加载时是一块纯底色，不会闪一下整张截图。
    layer.className = "crop-layer is-peek";
    layer.innerHTML = `
      <div class="crop-head">
        <strong>框出商品图</strong>
        <!-- 先放一行占位：留空的话这一行高度是 0，等 updateHint() 填上字，
             上面的头部会变高、显示区跟着变矮，图片就按旧尺寸摆偏了。 -->
        <p class="faint" data-hint>正在读取照片…</p>
      </div>
      <div class="crop-stage">
        <div class="crop-frame">
          <div class="crop-clip">
            <div class="crop-view">
              <img class="crop-img" alt="">
              <div class="crop-mask" aria-hidden="true" hidden></div>
            </div>
          </div>
          <div class="crop-box" hidden>
            <i class="crop-h" data-h="nw"></i><i class="crop-h" data-h="n"></i><i class="crop-h" data-h="ne"></i>
            <i class="crop-h" data-h="w"></i><i class="crop-h" data-h="e"></i>
            <i class="crop-h" data-h="sw"></i><i class="crop-h" data-h="s"></i><i class="crop-h" data-h="se"></i>
          </div>
        </div>
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
    const view = layer.querySelector(".crop-view");
    const stage = layer.querySelector(".crop-stage");
    const frame = layer.querySelector(".crop-frame");
    const maskEl = layer.querySelector(".crop-mask");
    const boxEl = layer.querySelector(".crop-box");
    const hint = layer.querySelector("[data-hint]");
    const okBtn = layer.querySelector("[data-ok]");
    const tip = layer.querySelector(".crop-tip");
    const reduced = !!(
      window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );

    /** 当前框，**存的是相对图片的比例**（0~1），不是像素。
     *  这样窗口大小变了、图片重新布局了，框都还贴在同一个位置。
     *  `grow:false`（带回来的是上次的框）时用 `factor=1`：`expand` 顺带
     *  做了"夹进 [0,1]"和"超界就往里收"，正好当校验用。 */
    let box = initial
      ? expand(initial, grow ? 1.5 : 1)
      : { ...DEFAULT_BOX };
    /** `"peek"` = 选区铺满显示区；`"edit"` = 整图 + 可拖的框。 */
    let mode = "peek";
    /** 图片量出来了、可以交互。 */
    let ready = false;
    /** 已经关窗（含淡出的那 170ms）。所有异步回调都要先看它。 */
    let done = false;

    // ---- 几何：图片在 edit 视图下（1:1）的布局 ----
    // `w0/h0` 是图片铺进显示区后的尺寸，`ox/oy` 是它在帧里的左上角
    // （显示区按图片比例定尺寸，所以这两个恒为 0，留着只为看懂坐标系）。
    // 拖动换算和框的定位**全部**基于它们，与当前视觉缩放无关 —— 所以
    // 从 peek 切到 edit 的那一刻不会算错。
    let w0 = 0;
    let h0 = 0;
    let ox = 0;
    let oy = 0;

    let drag = null;
    let peekTimer = 0;
    /** 进场动画进行中。这时 `mode` 已经是 peek，但画面上要停在"整图"。 */
    let intro = false;
    /** peek 的放大倍数，只用来写提示文案。 */
    let peekZoom = 1;
    let firstPaint = true;

    const finish = (value) => {
      if (done) return;
      done = true;
      clearTimeout(peekTimer);
      peekTimer = 0;
      drag = null;
      // 所有挂在 window 上的监听都要摘。`finish` 有四条路（取消 / 确认 /
      // Esc / 路由切换），漏摘任何一条都会让浮层关掉之后还在响应。
      window.removeEventListener("resize", onResize);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", endDrag);
      window.removeEventListener("pointercancel", endDrag);
      window.removeEventListener("blur", onBlur);
      // ★ `crop-open` 不能在这里摘：它还兼着"锁页面滚动"和"挡住全局粘贴"两件事
      //   （`view-add.js` 的粘贴守卫读的就是这个类）。提前摘掉的话，淡出的这
      //   170ms 里页面能滚、还能粘进一张新图，而稍后 `acceptImage(crop)` 会
      //   把那张新图覆盖掉 —— 又是一次静默丢图。跟 `layer.remove()` 一起摘。
      // 淡出 160ms 再真正删掉：直接 `remove()` 是"啪一下消失"，从全屏黑底
      // 切回白底表单会闪一下。
      // ⚠️ 这 170ms 里 `resolve` 还没发出去，调用方会一直等 —— 所以 `done`
      //    必须先置 true，否则连点两下会走两遍。
      layer.classList.add("leaving");
      window.setTimeout(() => {
        layer.remove();
        document.body.classList.remove("crop-open");
        resolve(value);
      }, 170);
    };

    // -- 布局 -------------------------------------------------------------

    /** 量一次舞台，按**图片比例**定出显示区，再算出图片在 edit 视图下的尺寸。
     *  图片加载完、窗口尺寸变化（手机转屏 / 地址栏收起）都要重来。
     *
     *  ★ 显示区的尺寸跟着图片走（而不是写死一条边距让它自己撑）：
     *    edit 视图里图片**正好铺满**显示区，一点不浪费；peek 里选区也是
     *    这个区域能给到的最大尺寸。写成 `inset: 14px` 的话，竖版订单截图
     *    在宽窗口里左右会空掉一大半，"铺满"就成了空话。 */
    function layout() {
      const stageW = stage.clientWidth;
      const stageH = stage.clientHeight;
      if (!stageW || !stageH || !img.naturalWidth || !img.naturalHeight) return false;
      // 给手柄留的余量：手柄 30px、中心压在框边上，所以每边 14px 刚好不越界
      const availW = Math.max(1, stageW - PAD * 2);
      const availH = Math.max(1, stageH - PAD * 2);
      const fit = Math.min(availW / img.naturalWidth, availH / img.naturalHeight);
      // 图片被压成 0 尺寸时 `fit` 会是 0 → 后面 `dx = 位移 / 0` 得到
      // Infinity，框跳到边界或冻在 `NaNpx`。宁可这一帧不画。
      if (!isFinite(fit) || fit <= 0) return false;
      w0 = img.naturalWidth * fit;
      h0 = img.naturalHeight * fit;
      frame.style.width = `${w0}px`;
      frame.style.height = `${h0}px`;
      frame.style.left = `${(stageW - w0) / 2}px`;
      frame.style.top = `${(stageH - h0) / 2}px`;
      // 显示区就是图片本身的大小，所以帧内偏移恒为 0；这两个变量留着是为了
      // 让 `renderBox()` / `applyTransform()` 的坐标系一眼看得懂。
      ox = 0;
      oy = 0;
      view.style.width = `${w0}px`;
      view.style.height = `${h0}px`;
      return true;
    }

    function setTransition(on) {
      const v = on && !reduced ? `transform ${ZOOM_MS}ms cubic-bezier(.22, .68, .16, 1)` : "none";
      if (view.style.transition !== v) view.style.transition = v;
    }

    /** 按当前视图画图片的位置与缩放。
     *
     *  peek：把框里的那块按**能放下的最大倍数**放大（`contain`），看上去就是
     *        "选区铺满了显示区"。比例和显示区不完全一致时，短的那一边会留出
     *        底色 —— 宁可留白，也不裁掉用户刚框进来的内容。
     *  edit：整图，1:1。 */
    function applyTransform() {
      if (mode === "peek" && !intro) {
        // 显示区就是 `w0 × h0` 那块（见 `layout()`），所以目标框就是它。
        // 下限只为防 0 除；框本身有 MIN_PX 兜着，正常不会到这里。
        const bw = Math.max(0.001, box.w * w0);
        const bh = Math.max(0.001, box.h * h0);
        const z = Math.min(w0 / bw, h0 / bh);
        peekZoom = isFinite(z) && z > 0 ? z : 1;
        // `transform-origin` 是 0 0，所以"框中心挪到显示区中心"就是
        // 直接把框中心的本地坐标乘上 `z` 再从中心减掉。
        const tx = w0 / 2 - z * (box.x * w0 + bw / 2);
        const ty = h0 / 2 - z * (box.y * h0 + bh / 2);
        view.style.transform = `translate(${tx}px, ${ty}px) scale(${z})`;
      } else {
        peekZoom = 1;
        view.style.transform = `translate(${ox}px, ${oy}px)`;
      }
    }

    /** 框（边框 + 手柄）和框外填色。
     *
     * ★ 两者坐标系不一样，这是刻意的：
     *  · 填色在 `.crop-view` 里，用**图片本地坐标** —— 它跟着 `transform`
     *    一起缩放平移，所以"选区以外"在任何视图、任何动画中间都被盖成底色，
     *    不可能露出没选中的内容。
     *  · 边框手柄在 `.crop-frame` 里，用**帧坐标** —— 它必须不被 `.crop-clip`
     *    裁剪，否则框贴到图片边上时手柄会被切掉一半（那一半正好在框外）。
     *    peek 里它是隐身的，位置随便停在 edit 的坐标上就行。
     */
    function renderBox() {
      if (!ready) return;
      const lx = box.x * w0;
      const ly = box.y * h0;
      const lw = box.w * w0;
      const lh = box.h * h0;
      maskEl.style.left = `${lx}px`;
      maskEl.style.top = `${ly}px`;
      maskEl.style.width = `${lw}px`;
      maskEl.style.height = `${lh}px`;
      boxEl.style.left = `${ox + lx}px`;
      boxEl.style.top = `${oy + ly}px`;
      boxEl.style.width = `${lw}px`;
      boxEl.style.height = `${lh}px`;
      updateHint();
    }

    /** 最终会裁出的尺寸（原图像素，已按 MAX_SIDE 缩过）。 */
    function output() {
      const sw = Math.min(img.naturalWidth, Math.max(1, Math.round(box.w * img.naturalWidth)));
      const sh = Math.min(img.naturalHeight, Math.max(1, Math.round(box.h * img.naturalHeight)));
      const sx = clamp(Math.round(box.x * img.naturalWidth), 0, img.naturalWidth - sw);
      const sy = clamp(Math.round(box.y * img.naturalHeight), 0, img.naturalHeight - sh);
      const k = Math.min(1, MAX_SIDE / Math.max(sw, sh));
      return {
        sx,
        sy,
        sw,
        sh,
        outW: Math.max(1, Math.round(sw * k)),
        outH: Math.max(1, Math.round(sh * k)),
      };
    }

    /** 标题下面那行提示。它承担了"解释现在看到的是什么"的职责 ——
     *  peek 里画面被放大了好几倍，不写一句"已放大 ×3.4"，用户会以为
     *  这张图本来就是糊的。 */
    function updateHint() {
      if (!ready || intro) return;
      if (mode === "peek") {
        const z = peekZoom >= 1.05 ? `×${peekZoom.toFixed(1)}` : "原尺寸";
        hint.textContent = `已放大 ${z} · 点一下画面调整选区`;
      } else {
        const o = output();
        hint.textContent = `拖动四角或四边 · 输出 ${o.outW}×${o.outH} px`;
      }
    }

    // -- 视图切换 ---------------------------------------------------------

    function enterEdit({ animate = false } = {}) {
      if (done) return;
      clearTimeout(peekTimer);
      peekTimer = 0;
      intro = false;
      if (mode === "edit") return;
      mode = "edit";
      layer.classList.remove("is-peek");
      layer.classList.add("is-edit");
      setTransition(animate);
      applyTransform();
      renderBox();
    }

    function enterPeek({ animate = true } = {}) {
      if (done) return;
      clearTimeout(peekTimer);
      peekTimer = 0;
      if (mode === "peek") return;
      intro = false;
      mode = "peek";
      layer.classList.add("is-peek");
      layer.classList.remove("is-edit");
      setTransition(animate);
      applyTransform();
      updateHint();
    }

    /** 进场：先给一帧"整图"，再推近到选区。
     *  两个作用：① 告诉用户这是从哪张图里裁的；② 让"铺满"这个结果
     *  有个来处，不会一打开就是一张莫名放大的图。 */
    function startIntro() {
      mode = "peek";
      intro = true;
      layer.classList.add("is-peek");
      layer.classList.remove("is-edit");
      setTransition(false);
      applyTransform();
      // 先摆一次框：不然 `maskEl` 会是一个 0×0、贴在左上角的元素，
      // 它那 9999px 的外溢阴影正好糊住整个画面（第一次淡入时才会暴露）。
      renderBox();
      if (reduced) {
        intro = false;
        applyTransform();
        updateHint();
        return;
      }
      // 双 rAF：第一帧把"整图"这个起点真正画出去，第二帧才改成推近后的
      // transform。同一帧里连着改两次内联样式会被合并成一次计算样式，
      // transition 没有起点 —— 表现就是"直接跳过去，没有动画"。
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (done || !intro) return;
          intro = false;
          setTransition(true);
          applyTransform();
          updateHint();
        });
      });
    }

    /** 把框拉回合法范围：不小于 44px（手指捏得住）、不越出图片。
     *
     * ★ 必须在每次量完布局后跑一遍。AI 偶尔给出极小的框（比如高 2%），
     *   `MIN_PX / h0` 就比框本身还大，于是拖动里 `clamp(v, lo, hi)` 出现
     *   `hi < lo` —— 而 `clamp` 在那种情况下返回 `hi`：一按下框就自己跳，
     *   甚至跳到负坐标（画面上还框着，`output()` 却把源区域夹回 0，裁出来
     *   的不是框住的那块）。 */
    function keepBoxInBounds() {
      const minW = Math.min(1, MIN_PX / w0);
      const minH = Math.min(1, MIN_PX / h0);
      box.w = clamp(box.w, minW, 1);
      box.h = clamp(box.h, minH, 1);
      box.x = clamp(box.x, 0, 1 - box.w);
      box.y = clamp(box.y, 0, 1 - box.h);
    }

    /** 重新量一遍再重画。图片加载完、窗口尺寸变化都走这里。 */
    function remeasure() {
      if (!img.naturalWidth) return;
      if (!layout()) {
        ready = false;
        boxEl.hidden = true;
        maskEl.hidden = true;
        okBtn.disabled = true;
        return;
      }
      ready = true;
      keepBoxInBounds();
      boxEl.hidden = false;
      maskEl.hidden = false;
      okBtn.disabled = false;
      if (firstPaint) {
        firstPaint = false;
        startIntro();
        return;
      }
      // 转屏 / 键盘收起：直接落到新位置，别放动画（动画期间拖动会算歪）
      setTransition(false);
      applyTransform();
      renderBox();
    }

    // -- 拖动 -------------------------------------------------------------
    //
    // 用 Pointer Events 而不是 mouse/touch 两套：一套代码同时吃鼠标、触摸屏、
    // 触控笔，还自带 setPointerCapture（手指划出框外也不会丢事件）。

    function onDown(ev) {
      if (!ready || done) return;
      if (ev.pointerType === "mouse" && ev.button !== 0) return;
      // 第二根手指不参与：不然双指捏合 / 误触会让框跳到另一根手指的位置，
      // 而且任意一根抬起都会结束拖动。
      if (ev.isPrimary === false) return;
      // ★ 只有**显示区里**的按下才算"调整选区"。
      //   不圈定范围的话，点标题、点「用这张」也会先瞬间切到 edit 并开始一次
      //   拖动 —— 触屏点按抖动几像素是常态，框就被悄悄挪了，用户完全看不出
      //   （点确认的那一刻框已经变了）。这条是实测之外的必然推论，必须挡。
      if (!stage.contains(ev.target)) return;

      const wasPeek = mode === "peek";
      const handle = ev.target.closest ? ev.target.closest("[data-h]") : null;
      let kind = handle ? handle.dataset.h : null;
      if (!kind) {
        // peek 里整屏都是选区，用户"按在图上"的意图就是拖这个框；
        // edit 里只有框内才能整体拖，按在框外（那片压暗区）不做事 ——
        // 否则想收个角会先把框拖跑。
        if (wasPeek || boxEl.contains(ev.target)) kind = "move";
      }
      // 按在框外：什么都不做，也**不动**那个待回弹的计时器（让它照常到点回 peek）
      if (!kind) return;

      ev.preventDefault();
      // 一按下就切到 edit（瞬间），这样手指还没动就能看见框外被藏起来的部分
      if (wasPeek) enterEdit({ animate: false });
      // 真正开始拖了才取消回弹：否则拖到一半画面自己放大回 peek
      clearTimeout(peekTimer);
      peekTimer = 0;

      drag = {
        kind,
        pointerId: ev.pointerId,
        lastX: ev.clientX,
        lastY: ev.clientY,
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
      boxEl.classList.add("dragging");
    }

    function onMove(ev) {
      if (!drag || !ready || done) return;
      // 只认发起这次拖动的那个指针。不校验的话，中途落下的第二根手指
      // 会按自己的坐标算出巨大的位移，框直接飞走。
      if (ev.pointerId !== drag.pointerId) return;
      drag.lastX = ev.clientX;
      drag.lastY = ev.clientY;
      // 1:1 视图下的换算基准。**不能用 `getBoundingClientRect()`** ——
      // 那是"当前视觉尺寸"，peek 里被放大过，用它算出的位移会小好几倍。
      const dx = (ev.clientX - drag.startX) / w0;
      const dy = (ev.clientY - drag.startY) / h0;
      const b = drag.box;
      const minW = MIN_PX / w0;
      const minH = MIN_PX / h0;
      const m = drag.kind;

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
      }
      renderBox();
    }

    /** @param ev 可空（`blur` 那条路没有事件） */
    function endDrag(ev) {
      if (!drag) return;
      // 抬起的不是发起拖动的那个指针 → 不结束（另一根手指抬起不该打断）
      if (ev && ev.pointerId !== undefined && ev.pointerId !== drag.pointerId) return;
      drag = null;
      boxEl.classList.remove("dragging");
      if (done || mode !== "edit") return;
      // 松手 → 过一会儿回到"选区铺满"。这期间再按下去会取消它（继续调），
      // 于是"连续微调"和"松手看效果"两种节奏都能走通。
      clearTimeout(peekTimer);
      peekTimer = window.setTimeout(() => enterPeek({ animate: true }), PEEK_DELAY);
    }

    // 用 `function` 声明而不是 `const`：`finish()` 里要摘这几个监听，
    // 而 `finish` 定义在上面。函数声明会提升，`const` 不会（TDZ）。
    function onResize() {
      remeasure();
      // 拖动途中换了布局（手机地址栏收起 / 转屏）：`w0/h0` 变了，而
      // `drag.startX` 是像素坐标 —— 不重设基准的话，下一次 `pointermove`
      // 会拿"旧起点 + 新系数"算，框直接跳一段。
      if (drag) {
        drag.startX = drag.lastX;
        drag.startY = drag.lastY;
        drag.box = { ...box };
      }
    }
    function onKey(ev) {
      if (ev.key === "Escape") finish(null);
    }
    // 路由切走（Android 返回键 / 点导航）→ 关窗。
    //
    // 浮层挂在 `body` 上，不受 `#view` 的 `innerHTML` 替换影响 —— 不关的话它会
    // 盖在新页面上；用户再点「用这张」，`acceptImage` 会写进一个**已经脱离文档**
    // 的 input，裁剪结果丢失，还在新页面上弹一句 toast。
    function onHash() {
      finish(null);
    }
    /** 窗口失焦（Alt+Tab / 切到别的 App / 拖出浏览器窗口）：很可能收不到
     *  `pointerup`。不自己收尾的话 `drag` 会一直挂着 —— 之后鼠标划过浮层就
     *  无声改框，而且**永远不回 peek**（没人再排那个计时器了）。 */
    function onBlur() {
      endDrag();
    }

    layer.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    // 兜底：`setPointerCapture` 失败时（合成事件 / 老浏览器），手指移出浮层
    // 再抬起，pointerup 不一定会派发到 layer —— 那样 `drag` 一直非 null，
    // 之后鼠标划过浮层就会莫名其妙改框。
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
    window.addEventListener("blur", onBlur);
    window.addEventListener("resize", onResize);
    window.addEventListener("keydown", onKey);
    window.addEventListener("hashchange", onHash);

    img.addEventListener("load", remeasure);
    img.addEventListener("error", () => {
      tip.hidden = false;
      okBtn.disabled = true;
    });
    img.src = src;

    // -- 确认 / 取消 -------------------------------------------------------

    layer.querySelector("[data-cancel]").addEventListener("click", () => finish(null));

    okBtn.addEventListener("click", async () => {
      if (!ready || !img.naturalWidth) return;
      okBtn.disabled = true;
      okBtn.textContent = "处理中…";

      // 比例 → 原图像素。**必须用 naturalWidth**，不是屏幕上的显示尺寸，
      // 否则裁出来是一张和缩略图一样大的糊图。
      const o = output();
      const canvas = document.createElement("canvas");
      canvas.width = o.outW;
      canvas.height = o.outH;
      let blob = null;
      try {
        const ctx = canvas.getContext("2d");
        if (ctx) {
          // 透明背景填白：PNG 截图裁出来是 JPEG，不填白会变黑底
          ctx.fillStyle = "#fff";
          ctx.fillRect(0, 0, o.outW, o.outH);
          ctx.drawImage(img, o.sx, o.sy, o.sw, o.sh, 0, 0, o.outW, o.outH);
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
      finish(
        blob
          ? {
              blob,
              width: o.outW,
              height: o.outH,
              // 把生效的框带回去：下次再打开时按它复位，用户能接着调。
              rect: { x: box.x, y: box.y, w: box.w, h: box.h },
            }
          : null
      );
    });
  });
}
