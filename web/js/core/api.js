/** 后端接口封装。
 *
 * 设计要点：
 * - **只有这一个文件知道 URL 长什么样** —— 视图层只调 `api.xxx()`，
 *   以后后端改路径只改这里。
 * - 错误统一抛 `ApiError`，带上 HTTP 状态码 —— 视图层要能区分
 *   "未配置模型（503）" 和 "真的坏了（500）"，前者该给提示而不是报错。
 * - 同源部署，所以不用配 baseURL，也没必要开 CORS。
 */

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
    this.code = payload?.detail?.code || "";
  }
}

/** 把 `{a:1, b:undefined}` 变成 `?a=1`（跳过空值，避免把 "undefined" 塞进查询串）。 */
function buildQuery(params) {
  if (!params) return "";
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    q.set(key, String(value));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** 从 FastAPI 的错误体里取出人能读的一句话。 */
function readDetail(payload, fallback) {
  if (!payload) return fallback;
  if (typeof payload === "string") return payload;
  const detail = payload.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // Pydantic 校验错误：数组里每项是 {loc, msg, ...}
    return detail.map((d) => d.msg || JSON.stringify(d)).join("；");
  }
  if (detail && typeof detail.message === "string") return detail.message;
  if (detail) return JSON.stringify(detail);
  return fallback;
}

/** 默认超时：45 秒。
 *
 * 取值依据：实测解析 0.1s、打标 1–2.6s、LLM 排序约 2s
 * （`max_tokens` 有约束时）。45s 足够覆盖正常路径，超过就是异常。
 */
const DEFAULT_TIMEOUT_MS = 45000;

async function request(method, path, { json, form, params, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const init = { method, headers: {} };
  if (json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (form !== undefined) {
    // FormData：**不要**手动设 Content-Type，浏览器要自己加 boundary
    init.body = form;
  }

  // ★ 超时**必须有**（2026-10-20 修）。
  //
  // 没有超时的时候，只要后端不响应（没起、重启中、TCP 半开），
  // `await fetch` 会**永远挂着** → 调用方 `finally` 永不执行 →
  // 按钮永远停在「识别中…」且 disabled → **点不动、换内容也没用**。
  // 这不是"慢"，是**死锁**，而且界面上没有任何错误可看。
  //
  // 计时器覆盖到 `res.text()`：响应头回来了但 body 卡住也要算超时。
  const controller = new AbortController();
  init.signal = controller.signal;
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let res;
  let text;
  try {
    res = await fetch(path + buildQuery(params), init);
    text = await res.text();
  } catch (err) {
    // `AbortError` = 我们自己掐断的（超时），与"网络连不上"要分开说 ——
    // 前者告诉用户"后端可能没在跑"，后者是"根本连不上"。
    if (err && err.name === "AbortError") {
      throw new ApiError(
        `请求 ${Math.round(timeoutMs / 1000)} 秒没响应 —— 确认后端服务还在运行`,
        0,
        { detail: { code: "request_timeout" } }
      );
    }
    throw new ApiError("连不上后端服务，确认它还在运行", 0, { detail: { code: "request_network" } });
  } finally {
    clearTimeout(timer);
  }

  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!res.ok) {
    throw new ApiError(readDetail(payload, res.statusText), res.status, payload);
  }
  return payload;
}

/** §12：裁剪图为主，原图与选区仅供本次识别参考。 */
function imageForm(file, context = {}) {
  const form = new FormData();
  form.append("file", file);
  if (context.original && context.original !== file) {
    form.append("reference_image", context.original);
    if (context.rect) form.append("reference_crop", JSON.stringify(context.rect));
  }
  return form;
}

export const api = {
  // 元信息。
  // ★ 这两个**必须给短超时**（2026-10-21 加）：`boot()` 拿 /health 判断"后端在不在"，
  //   用 45 秒的默认值意味着服务被系统挂起时要**白等 45 秒**才显示引导页 ——
  //   期间页面一直停在"加载中…"，PWA 的原生闪屏也不退，用户只会觉得"打不开"。
  //   本机回环正常时是 10ms 级，6 秒已经极宽松；超时就当"没起"，立刻给指引。
  health: () => request("GET", "/health", { timeoutMs: 6000 }),
  configureBridge: (settings) => request("PUT", "/settings/desktop-bridge", { json: settings, timeoutMs: 12000 }),
  taxonomy: () => request("GET", "/taxonomy", { timeoutMs: 10000 }),

  // 单品
  listItems: (params) => request("GET", "/items", { params }),
  getItem: (id) => request("GET", `/items/${encodeURIComponent(id)}`),
  createItem: (payload) => request("POST", "/items", { json: payload }),
  commitPhoto: (token, file, payload, { original, display } = {}) => {
    const form = new FormData();
    form.append("token", token);
    form.append("file", file);
    form.append("payload", JSON.stringify(payload));
    if (original) form.append("original", original);
    if (display) form.append("display", display);
    return request("POST", "/items/import-photo", { form });
  },
  photoImportStatus: (token) => request("GET", `/items/import-photo/${encodeURIComponent(token)}`),
  startFlatlay: (token, file, kind) => {
    const form = new FormData(); form.append("token", token); form.append("file", file); form.append("kind", kind || "other");
    return request("POST", "/tagging/flatlay/jobs", { form, timeoutMs: 30000 });
  },
  flatlayStatus: token => request("GET", `/tagging/flatlay/jobs/${encodeURIComponent(token)}`, { timeoutMs: 20000 }),
  savePresentation: (id, file, sourceHash) => {
    const form = new FormData(); form.append("file", file); form.append("source_hash", sourceHash);
    return request("POST", `/items/${encodeURIComponent(id)}/presentation`, { form });
  },
  selectPresentation: (id, selected) => request("PATCH", `/items/${encodeURIComponent(id)}/presentation`, { json: { selected } }),
  updateItem: (id, payload) =>
    request("PATCH", `/items/${encodeURIComponent(id)}`, { json: payload }),
  deleteItem: (id) => request("DELETE", `/items/${encodeURIComponent(id)}`),
  bulkItems: (payload) => request("POST", "/items/bulk", { json: payload }),
  /** 解析粘贴来的商品文案。**只解析，不入库** —— 用户确认后再 `bulkItems`。
   *
   * ★ 超时单独设短一点（15s）：实测只要 0.1s（纯本地解析，不调模型）。
   * 45s 的默认值对这个动作等于"点了按钮要干等 45 秒"，体验上像卡死。
   * 真卡住时 15 秒就给结论，比 45 秒人道。
   */
  parseItems: (text) =>
    request("POST", "/items/parse", { json: { text }, timeoutMs: 15000 }),
  uploadImage: (id, file, context) => {
    const form = imageForm(file, context);
    return request("POST", `/items/${encodeURIComponent(id)}/image`, { form, timeoutMs: 100000 });
  },

  // 手机相册（2026-10-21 加，2026-10-22 起**无 UI 入口**）
  //
  // 历史：桌面图标的 PWA 壳没有实现 WebView 的 `onShowFileChooser`，
  // `<input type=file>` 点下去毫无反应 —— 而服务就跑在同一台手机上，
  // 所以曾让后端直接读相册目录兜底。
  // 2026-10-22 换成自建 APK、壳侧补上 onShowFileChooser 之后，两条路做的事
  // 完全重合（`accept="image/*"` 拉起来的就是相册网格），UI 收口成原生 input。
  // ⚠️ 下面这几个方法现在只有 `view-add.js` 里的休眠分支在引用（`pickedPhoto`
  //    恒 null，跑不到）。留着是为了壳再坏时能接回来，别因为"没人调"就删。
  //    恢复步骤见 `view-add.js` 上「照片」那一段注释。
  albumPhotos: (limit) =>
    request("GET", "/items/album/photos", { params: { limit }, timeoutMs: 15000 }),
  /** 相册原图的 URL（当缩略图用）。**不走 `request`** —— 那是 JSON 通道。 */
  albumPhotoUrl: (id) => `/items/album/photo?id=${encodeURIComponent(id)}`,
  importFromAlbum: (itemId, id) =>
    request("POST", `/items/${encodeURIComponent(itemId)}/image-from-album`, { json: { id }, timeoutMs: 100000 }),

  // 穿着记录
  logWear: (payload) => request("POST", "/items/wear", { json: payload }),
  listWearEvents: (limit) => request("GET", "/items/wear/events", { params: { limit } }),

  // 衣橱分析
  gaps: (season) => request("GET", "/wardrobe/gaps", { params: { season } }),
  purchaseState: () => request("GET", "/wardrobe/purchase", { timeoutMs: 10000 }),
  purchaseGenerate: (payload) => request("POST", "/wardrobe/purchase", { json: payload, timeoutMs: 110000 }),
  purchaseChoice: (payload) => request("PUT", "/wardrobe/purchase/choice", { json: payload, timeoutMs: 10000 }),
  combinations: (params) => request("GET", "/wardrobe/combinations", { params }),

  // 搭配与反馈
  recommend: (params) => request("POST", "/outfits/recommend", { params, timeoutMs: 100000 }),
  aesthetics: () => request("GET", "/outfits/aesthetics", { timeoutMs: 10000 }),
  feedback: (payload) => request("POST", "/outfits/feedback", { json: payload }),
  listOutfits: (limit) => request("GET", "/outfits", { params: { limit } }),
  purchaseReferenceStart: (payload) => request('POST', '/wardrobe/purchase/references', { json: payload, timeoutMs: 15000 }),
  purchaseReferenceEnsure: (planId) => request('POST', '/wardrobe/purchase/references/ensure', { json: { plan_id: planId }, timeoutMs: 15000 }),
  purchaseReferenceState: (id) => request('GET', '/wardrobe/purchase/references/' + encodeURIComponent(id), { timeoutMs: 25000 }),
  getOutfit: (id) => request("GET", `/outfits/${encodeURIComponent(id)}`),

  // 打标（无 key 时后端返回 503，视图层要当"未配置"而不是报错）
  tagItem: (id, params) =>
    request("POST", `/tagging/${encodeURIComponent(id)}`, { params, timeoutMs: 100000 }),

  /** **只识别、不入库**：录入页「AI 识别」按钮用。
   *
   * 和 `tagItem` 的区别：那个要求单品已经建好、照片已经存好；这里照片还在
   * 浏览器里 —— 用户想先看 AI 认成什么，觉得对再保存。
   *
   * 后端返回 `{tags, unresolved, ai_confidence, raw, applied:false, status:"preview"}`，
   * `tags` 的键就是表单的 name（`type` / `colors` / `material` …），可直接 `fillForm`。
   *
   * 超时 30s：实测 1.5~3s，30s 是给"大图 + 慢模型"留的余量；
   * 再长用户只会觉得按钮卡死（文案停在「识别中…」）。
   */
  previewTags: (file, context) => {
    const form = imageForm(file, context);
    return request("POST", "/tagging/preview", { form, timeoutMs: 100000 });
  },
  /** 同上，但照片在手机相册里（PWA 壳点不动 `<input type=file>` 时走这条）。 */
  previewTagsFromAlbum: (id) => {
    const form = new FormData();
    form.append("album_id", id);
    return request("POST", "/tagging/preview", { form, timeoutMs: 100000 });
  },

  // 偏好
  preferences: (params) => request("GET", "/preferences", { params }),
  preferenceContexts: () => request("GET", "/preferences/contexts"),

  /** 图片 URL。`image_path` 是文件名（不是完整路径）。
   *
   * `v` 是可选的**缓存版本**（通常传 `item.updated_at`）。
   *
   * ★ 为什么需要它（2026-10-21 审查发现）：`image_path` 是固定文件名
   *   `<item_id>.jpg`（`images.image_path_of` 明写「重新上传就是覆盖」），
   *   所以换图之后 URL **一个字都不变**。而 `/images/` 走的是网络优先 +
   *   浏览器 HTTP 缓存，`StaticFiles` 又不发 `Cache-Control` —— 老图可能有
   *   几天的新鲜期。后果不只是「换完看不出变化」：详情页「裁剪这张」是拿
   *   **同一个 URL** 取图去画的，取到旧位图就会把刚存的新图裁成旧图的裁剪结果，
   *   一次不可逆的数据覆盖。
   *
   *   拼上 `?v=` 之后，换图前后是两个缓存键，SW 的 `cache.put` 也天然按版本分开。
   *   **不传 `v` 时行为完全不变** —— 衣橱卡片、搭配页那些只读场景不受影响。
   */
  imageUrl(imagePath, v) {
    if (!imagePath) return null;
    const q = v ? `?v=${encodeURIComponent(v)}` : "";
    return `/images/${encodeURIComponent(imagePath)}${q}`;
  },
  presentationUrl(item) {
    return this.imageUrl(item.display_image_path, item.updated_at);
  },
};
