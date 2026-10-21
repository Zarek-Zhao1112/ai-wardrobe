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
 * 只有一个画面（2026-10-21 第三轮改版，用户拍板）
 * ------------------------------------------------------------------
 * 上一版做成"两个视图"：静息是选区铺满、按下切换成整图 1:1。用户否了：
 * 「不要分两个窗口，就是一个窗口，视角主要是在截取框内，然后长按截取框会
 *   显示不用截取的部分（淡化颜色，不是直接溶于背景）。如果没长按就只看
 *   截取框内的内容。」
 *
 * 所以现在的做法是**图片一动不动**，只让"窗口"本身开合：
 *   · 静息：窗口裁得正好等于选框 → 看到的就是"存进去的样子"，铺满整个显示区
 *   · 长按：窗口放到整个舞台 → 选框周围那一圈**以原尺寸、原位置**露出来，
 *     上面盖一层 58% 黑（看得出来是什么，但明显退后）
 *   · 松手：窗口收回选框，缩放同时归位（框被拖过的话，重新铺满）
 *
 * ★ 为什么这样就不"分两个窗口"了：变的东西只有两个 —— 窗口的 `clip-path`
 *   和那层填色的深浅。图片的 `transform` 在开合过程中**完全不变**（除非框
 *   被拖过、需要在收起时重新铺满），所以框里的内容不会放大缩小、也不会平移，
 *   看上去就是"同一张图，周围那圈打开了"。上一版之所以像另一个窗口，是因为
 *   它把整张图缩进显示区，画面主体从"商品"换成了"整页订单"。
 *
 * ★ 为什么开合用 JS 逐帧算（而不是 CSS transition）：
 *   开合时"窗口裁剪到哪"和"框画在哪"必须**每帧**用同一组数字算出来。拆成
 *   CSS 各转各的，只要有 1px 不同步，框边就会从窗口边上"漂"出去（这个 bug
 *   在上一版的放大镜时期已经踩过一次）。所以这里只有一处几何计算 `paint()`，
 *   动画只是把状态 `{s, x, y, open}` 插值后再调它。
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

/** 框的最小边长（屏幕像素）。44 是移动端触控目标的经验下限，再小手指捏不住。 */
const MIN_PX = 44;

/** 输出图片的最长边。订单截图的商品图本来就不大，1600 足够卡片和详情页用，
 *  又不会让裁剪后的文件比原图还大（`MAX_IMAGE_BYTES` 是 10 MB）。 */
const MAX_SIDE = 1600;

/** 最大放大倍数。框极小（AI 偶尔给 2%）时，"铺满"会算出 ×20 —— 那样只是把
 *  几个像素放大成一堵马赛克墙，既看不清也没意义。到 4 倍就停，窗口小一点、
 *  周围留底色，反而是更诚实的预览。 */
const MAX_SCALE = 4;

/** 长按判定。360ms 比 Android 系统长按（500ms）短一档 —— 这个动作是
 *  "看一眼"，等半秒会有明显的迟滞感；再短又容易和"点一下"混起来。 */
const HOLD_MS = 360;

/** 长按之前的抖动容差（屏幕像素）。手指按下去总有几像素漂移，超过这个数
 *  就判定"用户是想拖，不是想长按"。 */
const MOVE_TOL = 10;

/** 窗口开合时长。快一点：它是个"看一眼"的动作，慢吞吞的反而不像同一张图。 */
const OPEN_MS = 260;

/** 拖完之后让窗口多开一会儿，好让用户看清刚调成什么样。 */
const LINGER_MS = 520;

/** 显示区四周留给手柄的余量（px）。手柄 30px、中心压在框边上，
 *  所以每边 16px 刚好让最外侧那半个方块不越出舞台。 */
const PAD = 16;

/** 边缘命中宽度（px）。手柄在静息态是隐身的（框 == 窗口，没有多余的边），
 *  要给足容差用户才摸得到。 */
const EDGE = 26;

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

/** 三次缓出。和 CSS 的 `cubic-bezier(.22,.68,.16,1)` 观感接近 —— 收尾快、
 *  起步稍缓，适合"窗口展开"这种有明确终点的动作。 */
const easeOut = (p) => 1 - (1 - p) ** 3;

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
    layer.className = "crop-layer";
    layer.innerHTML = `
      <div class="crop-head">
        <strong>框出商品图</strong>
        <!-- 先放一行占位：留空的话这一行高度是 0，等 updateHint() 填上字，
             上面的头部会变高、显示区跟着变矮，图片就按旧尺寸摆偏了。 -->
        <p class="faint" data-hint>正在读取照片…</p>
      </div>
      <div class="crop-stage">
        <!-- 窗口：clip-path 决定"看得见哪一块"。静息时裁成正好等于选框的
             矩形（框内铺满），长按时放到整个舞台（框外露出来）。
             ⚠️ 它是唯一被裁的东西，.crop-box 必须放在**外面** —— 八个手柄
             有一半伸在框外，放进去会被切掉。 -->
        <div class="crop-window">
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
    const win = layer.querySelector(".crop-window");
    const stage = layer.querySelector(".crop-stage");
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
    // 0 宽/0 高的框会让 `plan()` 除出 Infinity。2% 是纯防御，正常路径到不了。
    box.w = Math.max(0.02, box.w);
    box.h = Math.max(0.02, box.h);
    box.x = clamp(box.x, 0, 1 - box.w);
    box.y = clamp(box.y, 0, 1 - box.h);

    /** ★ 整个画面的**唯一**状态。
     *  `s`    屏幕像素 / 原图像素
     *  `x, y` 图片左上角在舞台里的坐标
     *  `open` 0 = 窗口贴着框；1 = 窗口放到整个舞台
     *  开合动画只是把这个对象插值，画面永远是它的一次函数 —— 所以"窗口裁到哪"
     *  和"框画在哪"不可能错位。 */
    let st = { s: 1, x: 0, y: 0, open: 0 };

    /** 图片量出来了、可以交互。 */
    let ready = false;
    /** 已经关窗（含淡出的那 170ms）。所有异步回调都要先看它。 */
    let done = false;
    /** 舞台的屏幕矩形。拖动过程中每帧都读 `getBoundingClientRect()` 会强制
     *  同步布局（那一帧刚写过 transform），手感会掉帧 —— 按下时量一次就够。 */
    let rect = null;

    let drag = null;
    let holdTimer = 0;
    let lingerTimer = 0;
    let raf = 0;
    let animFrom = null;
    let animTo = null;
    let animT0 = 0;

    const finish = (value) => {
      if (done) return;
      done = true;
      cancelAnimationFrame(raf);
      raf = 0;
      animTo = null;
      clearTimeout(holdTimer);
      clearTimeout(lingerTimer);
      holdTimer = lingerTimer = 0;
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

    // -- 几何 -------------------------------------------------------------

    /** 选框在舞台坐标系里的矩形（**当前**缩放，含动画中间态）。 */
    function boxRect() {
      const nw = img.naturalWidth;
      const nh = img.naturalHeight;
      return {
        x: st.x + box.x * nw * st.s,
        y: st.y + box.y * nh * st.s,
        w: box.w * nw * st.s,
        h: box.h * nh * st.s,
      };
    }

    /** 算出"让当前选框正好铺满舞台"的缩放和图片位置。**不改状态**，只算 ——
     *  收起时要往这儿走，拖动中也可能随时拿来算。 */
    function plan() {
      const sw = stage.clientWidth;
      const sh = stage.clientHeight;
      const nw = img.naturalWidth;
      const nh = img.naturalHeight;
      const maxW = Math.max(1, sw - PAD * 2);
      const maxH = Math.max(1, sh - PAD * 2);
      let s = Math.min(maxW / (box.w * nw), maxH / (box.h * nh));
      if (!isFinite(s) || s <= 0) s = 1;
      s = Math.min(s, MAX_SCALE);
      return {
        s,
        // 选框中心 → 舞台中心
        x: sw / 2 - (box.x + box.w / 2) * nw * s,
        y: sh / 2 - (box.y + box.h / 2) * nh * s,
        open: 0,
      };
    }

    /** 唯一的绘制入口。任何状态下都调它，别在别处写内联样式。 */
    function paint() {
      if (!ready) return;
      const nw = img.naturalWidth;
      const nh = img.naturalHeight;
      const sw = stage.clientWidth;
      const sh = stage.clientHeight;

      view.style.transform =
        `translate3d(${st.x.toFixed(2)}px, ${st.y.toFixed(2)}px, 0) scale(${st.s.toFixed(5)})`;

      // 窗口 = 选框矩形 ——(按 open)→ 整个舞台。四个 inset 分别插值，
      // 于是"窗口边界恰好压在框边上"（open=0）和"完全打开"（open=1）之间的
      // 每一帧都是有意义的，不会出现框露在窗口外。
      const r = boxRect();
      const k = 1 - st.open;
      win.style.clipPath =
        `inset(${Math.max(0, r.y * k)}px ${Math.max(0, (sw - r.x - r.w) * k)}px ` +
        `${Math.max(0, (sh - r.y - r.h) * k)}px ${Math.max(0, r.x * k)}px)`;

      // 框（边框 + 手柄）在舞台坐标系里，不受窗口裁剪 —— 手柄要能伸出去。
      boxEl.style.transform = `translate3d(${r.x}px, ${r.y}px, 0)`;
      boxEl.style.width = `${r.w}px`;
      boxEl.style.height = `${r.h}px`;

      // 填色在 `.crop-view` 里，用**图片本地坐标**（原图像素）：它跟着
      // transform 一起缩放平移，所以"选框以外"在任何缩放、任何动画中间
      // 都严丝合缝地贴着框。写屏幕坐标的话，一放大就会露出没选中的内容。
      maskEl.style.left = `${box.x * nw}px`;
      maskEl.style.top = `${box.y * nh}px`;
      maskEl.style.width = `${box.w * nw}px`;
      maskEl.style.height = `${box.h * nh}px`;
    }

    /** 把状态插到 `next`，然后重画。`ms = 0` 直接就位。 */
    function to(next, ms) {
      cancelAnimationFrame(raf);
      raf = 0;
      animTo = null;
      if (!ms) {
        st = next;
        paint();
        updateHint();
        return;
      }
      animFrom = { ...st };
      animTo = next;
      animT0 = performance.now();
      const step = () => {
        const p = Math.min(1, (performance.now() - animT0) / ms);
        const e = easeOut(p);
        st = {
          s: animFrom.s + (animTo.s - animFrom.s) * e,
          x: animFrom.x + (animTo.x - animFrom.x) * e,
          y: animFrom.y + (animTo.y - animFrom.y) * e,
          open: animFrom.open + (animTo.open - animFrom.open) * e,
        };
        paint();
        updateHint(); // ★ 必须跟着 `st` 走 —— `open` 是**动画中**才变成 1 的
        if (p < 1) {
          raf = requestAnimationFrame(step);
        } else {
          st = animTo;
          animTo = null;
          raf = 0;
        }
      };
      raf = requestAnimationFrame(step);
    }

    /** 动画还在跑就直接跳到终点。新手势开始前必须调一次 —— 否则拖动基准
     *  会取到动画中间态的 `s`，手指一动框就跳一段。 */
    function settle() {
      if (!animTo) return;
      cancelAnimationFrame(raf);
      raf = 0;
      st = animTo;
      animTo = null;
      paint();
      updateHint();
    }

    /** 把框夹回合法范围：不小于 44px（手指捏得住）、不越出图片。
     *
     * ★ `MIN_PX / (nw * s)` 是"44 屏幕像素等于多少比例"，随缩放变 —— 所以
     *   必须在 `s` 定下来之后才能算。
     *
     * ★ 还要防 `clamp(v, lo, hi)` 在 `hi < lo` 时**返回 `hi`** 这个坑：框小于
     *   44px 时，`b.x + b.w - minW` 会小于 0，于是按下就跳、甚至跳到负坐标
     *   （画面上还框着，`output()` 却把源区域夹回 0，裁出来的不是框住那块）。
     *   所以下面每个 `clamp` 的 hi 都带了 `Math.max(...)` 兜底。
     */
    function keepBoxInBounds() {
      const nw = img.naturalWidth;
      const nh = img.naturalHeight;
      const minW = Math.min(1, MIN_PX / (nw * st.s));
      const minH = Math.min(1, MIN_PX / (nh * st.s));
      box.w = clamp(box.w, minW, 1);
      box.h = clamp(box.h, minH, 1);
      box.x = clamp(box.x, 0, 1 - box.w);
      box.y = clamp(box.y, 0, 1 - box.h);
    }

    /** 首屏（以及窗口尺寸变化后）：重新铺满 + 修掉过小的框。 */
    function fit() {
      const nw = img.naturalWidth;
      const nh = img.naturalHeight;
      // ★ `.crop-view` 的宽高必须是**原图像素**：它的子元素（img / mask）都按
      //   这个尺寸百分比铺开，漏写的话 img 就是 0×0 —— 画面全黑，而
      //   `boxRect()` 还照常算得出框，看起来像"功能没好"（实测踩过）。
      //   缩放交给 `transform: scale()`，这样开合时可以逐帧插值。
      view.style.width = `${nw}px`;
      view.style.height = `${nh}px`;
      let p = plan();
      const minW = Math.min(1, MIN_PX / (nw * p.s));
      const minH = Math.min(1, MIN_PX / (nh * p.s));
      if (box.w < minW || box.h < minH) {
        box.w = Math.max(box.w, minW);
        box.h = Math.max(box.h, minH);
        box.x = clamp(box.x, 0, 1 - box.w);
        box.y = clamp(box.y, 0, 1 - box.h);
        p = plan(); // 框变大了，缩放要重算 —— 否则窗口会溢出去
      }
      keepBoxInBounds();
      st = p;
      paint();
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

    /** 标题下面那行提示。它承担了"告诉用户这里能长按"的职责 —— 不说的话
     *  没有人会想到去长按一块图片。高度由 CSS 写死，换文案不会推动布局。 */
    function updateHint() {
      if (!ready) return;
      const o = output();
      hint.textContent =
        st.open > 0.5
          ? `松开收起 · 拖动边角调整 · 输出 ${o.outW}×${o.outH}`
          : `长按查看框外 · 输出 ${o.outW}×${o.outH} px`;
    }

    // -- 开合 -------------------------------------------------------------

    /** 把窗口放到整个舞台（长按 / 开始拖动时）。
     *
     *  ★ 这里**不碰** `st.s / st.x / st.y` —— 图片一动不动，"打开"的只是窗口。
     *    这是整套改版的核心：用户看到的是同一张图揭掉了周围那圈布，而不是
     *    换了一屏内容。 */
    function reveal(animate) {
      if (st.open === 1 && !animTo) return;
      layer.classList.add("is-open");
      // 提示由 `to()` 的动画帧负责更新（`open` 要等插值到位才是 1），
      // 这里再调一次只会拿到还是 0 的旧值。
      to({ ...st, open: 1 }, animate && !reduced ? OPEN_MS : 0);
    }

    /** 收回窗口，同时把缩放归位（框被拖过的话要重新铺满舞台）。 */
    function conceal() {
      if (done) return;
      layer.classList.remove("is-open");
      to(plan(), reduced ? 0 : OPEN_MS);
    }

    // -- 命中 / 拖动 -------------------------------------------------------

    /** 舞台坐标系里的点。 */
    function localPoint(ev) {
      const r = rect || (rect = stage.getBoundingClientRect());
      return { x: ev.clientX - r.left, y: ev.clientY - r.top };
    }

    /** 判定按下的位置：八个手柄之一 / 整体移动 / 无效（返回 null）。
     *
     *  ★ 静息态框边**就是**窗口边，手上没有可见的手柄可抓 —— 所以容差给到
     *    26px，用户"按住边缘"就能起手。这比画一圈看得见但很丑的手柄更省地方。
     *  ⚠️ 容差不能超过框的 1/3：框很小的时候，否则整个框都算"角"，永远拖不动
     *    整体。 */
    function hitTest(p) {
      const r = boxRect();
      const e = Math.min(EDGE, r.w / 3, r.h / 3);
      if (p.x < r.x - e || p.x > r.x + r.w + e || p.y < r.y - e || p.y > r.y + r.h + e) return null;
      const L = p.x - r.x <= e;
      const R = r.x + r.w - p.x <= e;
      const T = p.y - r.y <= e;
      const B = r.y + r.h - p.y <= e;
      if (L && T) return "nw";
      if (R && T) return "ne";
      if (L && B) return "sw";
      if (R && B) return "se";
      if (L) return "w";
      if (R) return "e";
      if (T) return "n";
      if (B) return "s";
      const outside = p.x < r.x || p.x > r.x + r.w || p.y < r.y || p.y > r.y + r.h;
      // 框内、离边都还远 → 整体挪。按在框外那一圈又不是边上 → 不做事
      // （否则想收个角会先把框拖跑）。
      return outside ? null : "move";
    }

    function onDown(ev) {
      if (!ready || done) return;
      if (ev.pointerType === "mouse" && ev.button !== 0) return;
      // 第二根手指不参与：不然双指捏合 / 误触会让框跳到另一根手指的位置，
      // 而且任意一根抬起都会结束拖动。
      if (ev.isPrimary === false) return;
      // ★ 只有**显示区里**的按下才算"查看 / 调整"。
      //   不圈定范围的话，点标题、点「用这张」也会起一个长按计时器 —— 手指
      //   抖几像素就是一次改框，用户完全看不出（点确认那一刻框已经变了）。
      if (!stage.contains(ev.target)) return;

      settle();
      const p = localPoint(ev);
      const kind = hitTest(p);
      if (!kind) return;

      ev.preventDefault();
      clearTimeout(lingerTimer);
      lingerTimer = 0;
      drag = {
        kind,
        pointerId: ev.pointerId,
        startX: p.x,
        startY: p.y,
        lastX: p.x,
        lastY: p.y,
        box: { ...box },
        moved: false,
      };
      // 长按 → 展开看框外。**不移动**才算长按，所以先起计时器；
      // 手指一动就取消（那种情况按"开始拖动"处理，见 onMove）。
      clearTimeout(holdTimer);
      holdTimer = window.setTimeout(onHold, HOLD_MS);
      // 捕获到 layer：手指移出窗口也能继续收到 move/up。
      // ⚠️ 必须包 try：`setPointerCapture` 对**合成事件**（自动化测试派发的
      // PointerEvent）会抛 NotFoundError，不包就会让第一次按下就中断拖动。
      try {
        layer.setPointerCapture?.(ev.pointerId);
      } catch {
        /* 拿不到捕获也能拖，只是手指移出窗口时会断 —— 不该因此报错 */
      }
    }

    function onHold() {
      holdTimer = 0;
      if (!drag || done) return;
      reveal(true);
    }

    function onMove(ev) {
      if (!drag || !ready || done) return;
      // 只认发起这次拖动的那个指针。不校验的话，中途落下的第二根手指
      // 会按自己的坐标算出巨大的位移，框直接飞走。
      if (ev.pointerId !== drag.pointerId) return;
      const p = localPoint(ev);
      drag.lastX = p.x;
      drag.lastY = p.y;

      const dx = p.x - drag.startX;
      const dy = p.y - drag.startY;
      if (!drag.moved && st.open < 1) {
        // 还没展开：先看看是不是"想拖"（位移超过抖动容差）
        if (Math.hypot(dx, dy) < MOVE_TOL) return;
        clearTimeout(holdTimer);
        holdTimer = 0;
        // 立刻展开，不给动画：手指已经在动，窗口再花 260ms 慢慢开会让整个
        // 手势发黏，而且拖动基准会落在动画中间态上。
        reveal(false);
      }
      drag.moved = true;
      boxEl.classList.add("dragging");
      applyMove(dx, dy);
    }

    function applyMove(dxPx, dyPx) {
      const nw = img.naturalWidth;
      const nh = img.naturalHeight;
      // 屏幕像素 → 比例。**不能用 `getBoundingClientRect()`** 换算：那是
      // "当前视觉尺寸"，和 `st.s` 一旦不同步，框就走得比手指快或慢。
      const dx = dxPx / (nw * st.s);
      const dy = dyPx / (nh * st.s);
      const b = drag.box;
      const minW = Math.min(1, MIN_PX / (nw * st.s));
      const minH = Math.min(1, MIN_PX / (nh * st.s));
      const m = drag.kind;

      if (m === "move") {
        box = {
          ...b,
          x: clamp(b.x + dx, 0, Math.max(0, 1 - b.w)),
          y: clamp(b.y + dy, 0, Math.max(0, 1 - b.h)),
        };
      } else {
        let { x, y, w, h } = b;
        // 拖左边：左边界动，同时从宽度里扣掉同样的量（右边界固定）
        if (m.includes("w")) {
          const nx = clamp(b.x + dx, 0, Math.max(0, b.x + b.w - minW));
          w = b.x + b.w - nx;
          x = nx;
        }
        if (m.includes("e")) {
          w = clamp(b.w + dx, minW, Math.max(minW, 1 - b.x));
        }
        if (m.includes("n")) {
          const ny = clamp(b.y + dy, 0, Math.max(0, b.y + b.h - minH));
          h = b.y + b.h - ny;
          y = ny;
        }
        if (m.includes("s")) {
          h = clamp(b.h + dy, minH, Math.max(minH, 1 - b.y));
        }
        box = { x, y, w, h };
      }
      paint();
      updateHint();
    }

    /** @param ev 可空（`blur` 那条路没有事件） */
    function endDrag(ev) {
      if (!drag) return;
      // 抬起的不是发起拖动的那个指针 → 不结束（另一根手指抬起不该打断）
      if (ev && ev.pointerId !== undefined && ev.pointerId !== drag.pointerId) return;
      const moved = drag.moved;
      drag = null;
      clearTimeout(holdTimer);
      holdTimer = 0;
      boxEl.classList.remove("dragging");
      if (done) return;
      // 静息态按了一下就松（没到长按时长、也没拖动）→ 什么都没发生，不用收。
      if (st.open === 0) return;
      clearTimeout(lingerTimer);
      // 拖过就多开一会儿让用户看清结果；只是长按看了一眼就马上收。
      // 不写 0 是因为 `pointerup` 和合成的 click 之间可能还隔一帧，窗口闪一下
      // 反而像故障。
      lingerTimer = window.setTimeout(conceal, moved ? LINGER_MS : 60);
    }

    // 用 `function` 声明而不是 `const`：`finish()` 里要摘这几个监听，
    // 而 `finish` 定义在上面。函数声明会提升，`const` 不会（TDZ）。
    function onResize() {
      rect = null;
      if (!img.naturalWidth) return;
      settle();
      if (!stage.clientWidth || !stage.clientHeight) return;
      ready = true;
      fit();
      // 拖动途中换了布局（手机地址栏收起 / 转屏）：`s` 变了，而
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
     *  无声改框，而且长按计时器 / 收起计时器也不会再被排上。 */
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

    /** 图片量出来了：解锁交互、铺满、写提示。 */
    function remeasure() {
      rect = null;
      if (!stage.clientWidth || !stage.clientHeight) return;
      ready = true;
      boxEl.hidden = false;
      maskEl.hidden = false;
      okBtn.disabled = false;
      // 图还在加载时用户就转了屏 → `load` 之后舞台尺寸可能已经变了，
      // 所以这里不是"用上次量的数字"，而是按当前尺寸重算一遍。
      fit();
    }

    img.addEventListener("load", () => {
      if (done || !img.naturalWidth) return;
      remeasure();
    });
    img.addEventListener("error", () => {
      tip.hidden = false;
      okBtn.disabled = true;
      hint.textContent = "这张图读不出来";
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
