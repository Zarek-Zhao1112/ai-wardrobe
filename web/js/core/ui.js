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

/** §11：统一的线性图标。路径来自静态白名单，外部输入不进入 SVG。 */
export function icon(name) {
  const paths = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    wardrobe: '<path d="m9 4-4 2-2 5 4 2v8h10v-8l4-2-2-5-4-2a3 3 0 0 1-6 0Z"/>',
    today: '<path d="m3 10 9-7 9 7v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM9 21v-7h6v7"/>',
    layers: '<path d="m12 3 9 5-9 5-9-5zM3 12l9 5 9-5M3 16l9 5 9-5"/>',
    add: '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
    gaps: '<path d="M5 7h14l1 13H4zM8 8V6a4 4 0 0 1 8 0v2"/>',
    bookmark: '<path d="M6 4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v17l-6-4-6 4z"/>',
    check: '<path d="m5 12 4 4 10-10"/>',
    back: '<path d="m14 6-6 6 6 6M8 12h12"/>',
    history: '<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l3 2"/>',
    right: '<path d="m10 6 6 6-6 6"/>',
    chevron: '<path d="m7 10 5 5 5-5"/>',
    leaf: '<path d="M20 3C11 2 4 6 4 12a6 6 0 0 0 6 6c6 0 10-6 10-15ZM4 21 15 9"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
    clipboard: '<path d="M8 5H6a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M10 11h6m-6 5h6M8 11h.01M8 16h.01"/><rect x="8" y="2" width="8" height="5" rx="1.5"/>',
    book: '<path d="M12 5C9 3 6 3 3 4v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1ZM12 5v15"/>',
    outfits: '<path d="m9 4-5 3-2 5 5 2v7h10v-7l5-2-2-5-5-3a3 3 0 0 1-6 0Z"/><path d="m15 3 1-2 1 2 2 1-2 1-1 2-1-2-2-1Z"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
    refresh: '<path d="M20 11a8 8 0 0 0-14-5L3 9m0-5v5h5m-4 4a8 8 0 0 0 14 5l3-3m0 5v-5h-5"/>',
    filter: '<path d="M4 6h6m4 0h6M4 12h2m4 0h10M4 18h10m4 0h2"/><circle cx="12" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="16" cy="18" r="2"/>',
    photo: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
    camera: '<path d="M4 7h4l2-3h4l2 3h4v14H4V7Z"/><circle cx="12" cy="13" r="4"/>',
    flip: '<path d="M3 8a9 9 0 0 1 15-3l3 3M21 2v6h-6M21 16a9 9 0 0 1-15 3l-3-3M3 22v-6h6"/>',
    compare: '<path d="M12 3v18M8 5H4v14h4M16 5h4v14h-4"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    replay: '<path d="M4 11a8 8 0 1 1 2 7M4 5v6h6"/>',
    spark: '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5zM20 2v4M18 4h4"/>',
    heart: '<path d="M20 5c-3-3-6-1-8 1-2-2-5-4-8-1-4 4 1 10 8 15C19 15 24 9 20 5Z"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    edit: '<path d="M4 20h4L20 8l-4-4L4 16v4Z"/><path d="m14 6 4 4"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="m9 3-1 3-3 1-2 3 2 2-1 3 2 3 3-1 2 3h3l1-3 3-1 2-3-2-2 1-3-2-3-3 1-2-3Z"/>',
  };
  return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${paths[name] || paths.wardrobe}</svg>`;
}

let toastTimer = null;

/** §26：同一份设置菜单供桌面顶栏和手机页面操作行使用，无独立状态副本。 */
export function serviceSettingsHtml(health) {
  const h = health || {};
  const bridge = h.desktop_bridge || {};
  const engine = h.engine === "api" ? "api" : "account";
  const engines = bridge.engines || {};
  // 只有电脑真能提供两条路时才给选择器。只有一条路时摆两个选项，
  // 等于让用户去点一个必定失败的东西。
  const canPick = Boolean(bridge.configured && !bridge.invalid && engines.account && engines.api);
  const option = (key, title, note) =>
    '<button type="button" role="radio" aria-checked="' + (engine === key) + '" data-engine="' + key +
    '" class="engine-option' + (engine === key ? " on" : "") + '"><span class="engine-dot" aria-hidden="true"></span>' +
    '<span class="engine-copy"><strong>' + title + "</strong><span>" + note + "</span></span></button>";
  const channel = h.ai_channel;
  const active = channel?.mode === "legacy" && engine === "account" ? "account" : channel?.mode;
  const provider = { openai: "OpenAI", deepseek: "DeepSeek" }[channel?.provider] || "";
  const channelOption = (mode, title, description) =>
    '<button type="button" class="engine-option' + (active === mode ? " on" : "") +
    '" data-ai-mode="' + mode + '" aria-pressed="' + (active === mode) + '">' +
    '<span class="engine-dot" aria-hidden="true"></span><span class="engine-copy"><strong>' + title +
    '</strong><span>' + description + '</span></span></button>';
  const picker = channel
    ? '<div class="engine-picker" data-ai-channel aria-label="AI 通道"><span class="engine-title">AI 通道</span>' +
      channelOption("direct", "API 直连", "本机联网 · 按 API 用量计费") +
      '<button type="button" data-ai-configure>' + (provider ? provider + " · API 设置" : "配置 OpenAI 或 DeepSeek") + '</button>' +
      channelOption("account", "WorkBuddy 账号额度", "使用电脑已登录账号的额度") + '</div>'
    : canPick
    ? '<div class="engine-picker" role="radiogroup" aria-label="识别引擎"><span class="engine-title">识别引擎</span>' +
      option("account", "账号额度", "不花钱 · 每次十几秒") +
      option("api", "API 直连", "按量计费 · 每次几秒") +
      "</div>"
    : "";
  const hint = channel ? ({
    direct: channel.direct_ready ? "当前通道：API 直连" + (provider ? " · " + provider : "") : "当前通道：API 直连 · 待配置",
    account: "当前通道：WorkBuddy 账号额度",
    legacy: "当前通道：电脑中转 API（旧版设置）",
  }[active] || "") : !bridge.configured
    ? (h.vision_ready ? "照片自动识别可用" : "视觉模型未配置，属性可手动填写")
    : engine === "api"
      ? "走电脑直调模型，需电脑代理开着"
      : "已配置电脑中转，识别时需电脑在线";
  const modelLine = h.llm_ready
    ? '模型：<span data-engine-model>' + esc(h.llm_model || "已配置") + "</span>"
    : "搭配使用本地排序";
  return `<details class="status-menu">
    <summary aria-label="设置：查看服务与模型状态" title="设置"><span class="dot${h.llm_ready ? "" : " off"}"></span><span class="status-label">${bridge.configured ? "电脑识别" : h.llm_ready ? "AI 已就绪" : "本地模式"}</span>${icon("settings")}</summary>
    <div class="status-popover"><strong>服务状态</strong>
      <p>${modelLine}</p>
      <p data-engine-hint>${esc(hint)}</p>
      ${picker}
      <button type="button" data-desktop-settings>电脑识别设置</button>
    </div></details>`;
}

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
