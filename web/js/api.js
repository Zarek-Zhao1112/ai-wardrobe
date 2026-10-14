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

async function request(method, path, { json, form, params } = {}) {
  const init = { method, headers: {} };
  if (json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (form !== undefined) {
    // FormData：**不要**手动设 Content-Type，浏览器要自己加 boundary
    init.body = form;
  }

  let res;
  try {
    res = await fetch(path + buildQuery(params), init);
  } catch (err) {
    throw new ApiError("连不上后端服务，确认它还在运行", 0, null);
  }

  const text = await res.text();
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
  // 元信息
  health: () => request("GET", "/health"),
  taxonomy: () => request("GET", "/taxonomy"),

  // 单品
  listItems: (params) => request("GET", "/items", { params }),
  getItem: (id) => request("GET", `/items/${encodeURIComponent(id)}`),
  createItem: (payload) => request("POST", "/items", { json: payload }),
  updateItem: (id, payload) =>
    request("PATCH", `/items/${encodeURIComponent(id)}`, { json: payload }),
  deleteItem: (id) => request("DELETE", `/items/${encodeURIComponent(id)}`),
  bulkItems: (payload) => request("POST", "/items/bulk", { json: payload }),
  /** 解析粘贴来的商品文案。**只解析，不入库** —— 用户确认后再 `bulkItems`。 */
  parseItems: (text) => request("POST", "/items/parse", { json: { text } }),
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
