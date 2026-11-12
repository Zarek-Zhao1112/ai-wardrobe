/** 折叠面板动画（全站公共）。
 *
 * 为什么自己写而不靠 CSS：
 * - 真机 WebView 96（Android 12）**不支持** `::details-content`，
 *   也不支持 `height: auto` 的过渡（`interpolate-size` 是 Chrome 129 才有）。
 *   所以「从 0 滑到自然高度」只能先用 JS 量出像素值再写死过渡。
 * - `<details>` 关闭时浏览器直接 `display:none` 掉内容，必然没有过渡。
 *   这里把内容包进 `.collapse-body`，改用 `height` 控制显隐。
 *
 * 渐进增强：
 * 标记只写 `data-fold="1"`，**由本模块**在运行时改成 `data-fold="ready"`
 * 才接管。JS 没跑起来（缓存旧包 / 语法错误）时页面上没有任何 `.collapse-body`，
 * `<details>` 退回浏览器原生行为 —— 功能不会因为动画挂了而消失。
 *
 * 用法：给想动画的 `<details>` 加 `data-fold="1"`，别的什么都不用做。
 * 本模块在 `document.body` 上挂 MutationObserver，动态渲染出来的也自动接管。
 */

const DURATION = 240;
const EASE = "cubic-bezier(.4,0,.2,1)";
const MARK = "data-fold";
const BODY_CLASS = "collapse-body";

/** 正在做动画的 details —— 动画期间再点一次直接忽略，
 *  否则两个过渡抢同一个 `height`，高度会跳到中间值。 */
const running = new WeakSet();
let started = false;

const reducedMotion = () =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** 内容容器：没有就**原地创建**一个，把 summary 以外的节点搬进去。 */
function bodyOf(details) {
  const existing = details.querySelector(":scope > ." + BODY_CLASS);
  if (existing) return existing;
  const body = document.createElement("div");
  body.className = BODY_CLASS;
  const moved = [...details.childNodes].filter((node) => node.nodeName !== "SUMMARY");
  for (const node of moved) body.appendChild(node);
  details.appendChild(body);
  return body;
}

/** 不带动画地对齐到 `details.open` 的当前状态。
 *
 * 用于：首次接管、以及**代码里直接改 `.open`**（比如草稿恢复
 * `more.open = !!draft.more`）。后者会触发 `toggle`，走的就是这里。
 */
function sync(details) {
  const body = details.querySelector(":scope > ." + BODY_CLASS);
  if (!body) return;
  const open = details.open;
  body.style.transition = "none";
  body.style.height = open ? "auto" : "0px";
  // 关着的时候用 display:none 收尾 —— 高度 0 + overflow:hidden 仍然会把
  // 里面的输入框留在 Tab 顺序里，键盘用户会聚焦到看不见的控件上。
  body.style.display = open ? "block" : "none";
  void body.offsetHeight;
  body.style.transition = "";
}

function finish(details, body, open) {
  body.style.transition = "";
  if (open) {
    body.style.height = "auto";
  } else {
    details.open = false;      // 结束时才翻 `+ / −`，动画期间箭头不动
    body.style.height = "0px";
    body.style.display = "none";
  }
  running.delete(details);
}

/** 展开 / 收起。`animate=false` 时直接到位（首屏恢复状态用）。 */
export function setFold(details, open, animate = true) {
  if (!details || details.tagName !== "DETAILS") return;
  const body = details.querySelector(":scope > ." + BODY_CLASS);
  if (!body) {
    details.open = open;
    return;
  }
  if (running.has(details)) return;

  if (!animate || reducedMotion()) {
    details.open = open;
    sync(details);
    return;
  }

  running.add(details);
  const start = body.getBoundingClientRect().height;

  // 展开时先 `open` —— 否则内容还是 display:none，量不到自然高度
  if (open) details.open = true;
  body.style.display = "block";
  body.style.transition = "none";
  body.style.height = "auto";
  const natural = body.getBoundingClientRect().height;
  body.style.height = start + "px";
  void body.offsetHeight;                  // 强制回流，让起始高度生效
  body.style.transition = "height " + DURATION + "ms " + EASE;

  const target = open ? natural : 0;
  let timer = 0;
  const done = () => {
    body.removeEventListener("transitionend", onEnd);
    clearTimeout(timer);
    finish(details, body, open);
  };
  const onEnd = (event) => {
    if (event.target === body && event.propertyName === "height") done();
  };
  body.addEventListener("transitionend", onEnd);
  // 兜底：元素被移出 DOM / 高度没变化时 `transitionend` 不会来
  timer = setTimeout(done, DURATION + 80);

  requestAnimationFrame(() => {
    body.style.height = target + "px";
  });
}

function enhance(root) {
  const nodes = [];
  if (root.nodeType === 1 && root.matches('[data-fold="1"]')) nodes.push(root);
  if (root.querySelectorAll) nodes.push(...root.querySelectorAll('[data-fold="1"]'));
  for (const details of nodes) {
    details.setAttribute(MARK, "ready");
    bodyOf(details);
    sync(details);
    details.addEventListener("toggle", () => {
      // 自己动画引起的 toggle 已经在 `finish` 里收过尾了
      if (running.has(details)) return;
      sync(details);
    });
  }
}

function onClick(event) {
  const summary = event.target.closest && event.target.closest("summary");
  if (!summary) return;
  const details = summary.parentElement;
  if (!details || details.tagName !== "DETAILS" || details.getAttribute(MARK) !== "ready") return;
  // summary 里如果有链接/按钮，点它们是那个控件的事，不该顺手折叠
  const hit = event.target.closest("a[href],button,input,select,textarea");
  if (hit && hit !== summary && summary.contains(hit)) return;

  event.preventDefault();          // 挡掉浏览器原生的「立即开关」
  setFold(details, !details.open);
}

/** 全站启用。重复调用无副作用。 */
export function initCollapse() {
  if (started) return;
  started = true;
  enhance(document.body);
  new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType === 1) enhance(node);
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
  // 捕获阶段：summary 的点击可能被别的委托处理器先拦下
  document.addEventListener("click", onClick, true);
}
