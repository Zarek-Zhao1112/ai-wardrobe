/** 前端外壳：顶栏 + 视图路由 + 共享上下文。
 *
 * 加一个页面只要两步：
 *   1) 写一个导出 `label` 和 `mount(root, ctx)` 的模块
 *   2) 在 VIEWS 里加一行
 *
 * 路由用 hash（`#wardrobe`），所以刷新页面不会丢当前页，也不需要后端配合。
 */

import { api } from "./api.js";
import { esc, toast } from "./ui.js";
import * as add from "./view-add.js";
import * as gaps from "./view-gaps.js";
import * as item from "./view-item.js";
import * as outfits from "./view-outfits.js";
import * as wardrobe from "./view-wardrobe.js";

/** 已实现的页面。后面几波会往这里加：缺口 / 搭配。
 *
 * `hidden: true` 的页面**不出现在导航里**，只能通过 hash 进去
 * （例如 `#item/it_xxx` 是详情页，它不该占一个 Tab）。
 */
const VIEWS = [
  { id: "wardrobe", label: "衣橱", mod: wardrobe },
  { id: "add", label: "录入", mod: add },
  { id: "gaps", label: "缺口", mod: gaps },
  { id: "outfits", label: "搭配", mod: outfits },
  { id: "item", label: "单品", mod: item, hidden: true, parent: "wardrobe" },
];

const rootEl = document.getElementById("view");
const navEl = document.getElementById("nav");
const statusEl = document.getElementById("status");

/** 共享上下文。视图不必自己去取词表和健康状态。 */
const ctx = { api, taxonomy: null, health: null };

/** 解析 `#view` 或 `#view/参数`（例：`#item/it_abc123`）。 */
function parseHash() {
  const raw = (location.hash || "").replace(/^#/, "");
  const [id, ...rest] = raw.split("/");
  return { id: id || VIEWS[0].id, arg: rest.join("/") || null };
}

function renderNav(currentId) {
  navEl.innerHTML = VIEWS.filter((v) => !v.hidden)
    .map(
      (v) =>
        `<a class="tab${v.id === currentId ? " active" : ""}" href="#${esc(v.id)}">${esc(v.label)}</a>`
    )
    .join("");
}

function renderStatus() {
  const h = ctx.health;
  if (!h) {
    statusEl.innerHTML = `<span class="dot off"></span>后端未就绪`;
    return;
  }
  const model = h.llm_ready
    ? `<span class="dot"></span>模型 ${esc(h.llm_model || "已配置")}`
    : `<span class="dot off"></span>模型未配置`;
  const tagging = h.vision_ready ? "" : ` · 打标不可用`;
  statusEl.innerHTML =
    `${model}${tagging}<span class="faint"> · schema v${esc(h.schema_version)}</span>`;
}

async function render() {
  const { id, arg } = parseHash();
  const view = VIEWS.find((v) => v.id === id) || VIEWS[0];
  // 隐藏页（详情）不在导航里，高亮它所属的那个 Tab，免得看起来"哪个都没选中"
  renderNav(view.parent || view.id);
  document.title = `${view.label} · AI 穿搭助手`;
  try {
    await view.mod.mount(rootEl, ctx, arg);
  } catch (err) {
    rootEl.innerHTML = `<div class="alert error">页面加载失败：${esc(err && err.message)}</div>`;
  }
}

async function boot() {
  try {
    ctx.health = await api.health();
  } catch (err) {
    statusEl.innerHTML = `<span class="dot off"></span>${esc(err.message)}`;
  }
  renderStatus();

  try {
    ctx.taxonomy = await api.taxonomy();
  } catch {
    // 词表拿不到不该整页白屏 —— 视图会退化成显示原始 key
    toast("词表加载失败，标签可能显示成英文 key", "error");
  }

  await render();
}

window.addEventListener("hashchange", render);
boot();
