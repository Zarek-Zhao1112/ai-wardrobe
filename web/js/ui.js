/** 通用 UI 小工具：转义、提示、格式化。
 *
 * 刻意保持极小 —— 不引框架，也不**自造**框架。
 * 视图层用模板字符串拼 HTML，所以这里的 `esc()` 是安全底线。
 */

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** HTML 转义。
 *
 * **凡是把外部来的字符串插进模板，一律过这里** ——
 * 单品名、备注、模型写的搭配理由都算外部输入，不转义就是注入。
 */
export function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

let toastTimer = null;

/** 底部浮动提示。`kind` ∈ info | ok | error */
export function toast(message, kind = "info", ms = 2600) {
  const node = document.getElementById("toast");
  if (!node) return;
  node.textContent = String(message);
  node.className = "toast" + (kind === "info" ? "" : ` ${kind}`);
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
  }, ms);
}

/** 统一的失败渲染。带上 HTTP 状态码 —— 视图层要靠它区分
 *  "模型没配（503）" 和 "真的坏了"。 */
export function renderError(node, err) {
  const status = err && err.status ? `（HTTP ${err.status}）` : "";
  const msg = (err && err.message) || "未知错误";
  node.innerHTML = `<div class="alert error">加载失败${esc(status)}：${esc(msg)}</div>`;
}

/** 空态块 */
export function emptyState(title, hint = "") {
  return (
    `<div class="empty"><h2>${esc(title)}</h2>` +
    (hint ? `<p>${esc(hint)}</p>` : "") +
    `</div>`
  );
}

/** "2026-10-03T12:25:51+08:00" → "10-03 12:25" */
export function fmtDateTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 受控词表的 key → 中文标签。查不到就**原样返回**，不硬猜。 */
export function labelOf(vocab, key) {
  if (key === null || key === undefined || key === "") return "";
  const table = vocab || {};
  return table[key] || String(key);
}

/** 多值字段 → "白 / 黑" */
export function labelsOf(vocab, keys) {
  if (!Array.isArray(keys) || keys.length === 0) return "";
  return keys.map((k) => labelOf(vocab, k)).filter(Boolean).join(" / ");
}

/** 一组单品 → 一排 chip（搭配预览用）。 */
export function itemChipsHtml(items, taxonomy) {
  const vocab = (taxonomy && taxonomy.vocabularies) || {};
  return (items || [])
    .map((it) => {
      const name = it.name || it.type_label || "未命名";
      const color = labelOf(vocab.color, it.primary_color);
      return `<span class="chip">${esc(name)}${color ? ` · ${esc(color)}` : ""}</span>`;
    })
    .join("");
}

/** 在**本次渲染出来的**容器上委托一个点击处理器。
 *
 * ⚠️ 传入的 `container` 必须是每次渲染新建的元素 ——
 * 挂到常驻节点上会随每次渲染叠加监听器。
 */
export function onClick(container, selector, handler) {
  container.addEventListener("click", (ev) => {
    const hit = ev.target.closest(selector);
    if (hit && container.contains(hit)) handler(hit, ev);
  });
}
