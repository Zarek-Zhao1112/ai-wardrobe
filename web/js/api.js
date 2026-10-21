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
        null
      );
    }
    throw new ApiError("连不上后端服务，确认它还在运行", 0, null);
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

export const api = {
  // 元信息。
  // ★ 这两个**必须给短超时**（2026-10-21 加）：`boot()` 拿 /health 判断"后端在不在"，
  //   用 45 秒的默认值意味着服务被系统挂起时要**白等 45 秒**才显示引导页 ——
  //   期间页面一直停在"加载中…"，PWA 的原生闪屏也不退，用户只会觉得"打不开"。
  //   本机回环正常时是 10ms 级，6 秒已经极宽松；超时就当"没起"，立刻给指引。
  health: () => request("GET", "/health", { timeoutMs: 6000 }),
  taxonomy: () => request("GET", "/taxonomy", { timeoutMs: 10000 }),

  // 单品
  listItems: (params) => request("GET", "/items", { params }),
  getItem: (id) => request("GET", `/items/${encodeURIComponent(id)}`),
  createItem: (payload) => request("POST", "/items", { json: payload }),
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
  uploadImage: (id, file) => {
    const form = new FormData();
    form.append("file", file);
    return request("POST", `/items/${encodeURIComponent(id)}/image`, { form });
  },

  // 穿着记录
  logWear: (payload) => request("POST", "/items/wear", { json: payload }),
  listWearEvents: (limit) => request("GET", "/items/wear/events", { params: { limit } }),

  // 衣橱分析
  gaps: (season) => request("GET", "/wardrobe/gaps", { params: { season } }),
  combinations: (params) => request("GET", "/wardrobe/combinations", { params }),

  // 搭配与反馈
  recommend: (params) => request("POST", "/outfits/recommend", { params }),
  feedback: (payload) => request("POST", "/outfits/feedback", { json: payload }),
  listOutfits: (limit) => request("GET", "/outfits", { params: { limit } }),
  getOutfit: (id) => request("GET", `/outfits/${encodeURIComponent(id)}`),

  // 打标（无 key 时后端返回 503，视图层要当"未配置"而不是报错）
  tagItem: (id, params) =>
    request("POST", `/tagging/${encodeURIComponent(id)}`, { params }),

  // 偏好
  preferences: (params) => request("GET", "/preferences", { params }),
  preferenceContexts: () => request("GET", "/preferences/contexts"),

  /** 图片 URL。`image_path` 是文件名（不是完整路径）。 */
  imageUrl(imagePath) {
    if (!imagePath) return null;
    return `/images/${encodeURIComponent(imagePath)}`;
  },
};
