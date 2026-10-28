/** 衣橱视图：筛选 + 卡片网格。
 *
 * 两个刻意的取舍：
 * - **工具条只渲染一次**，筛选变化只重绘下面的列表 ——
 *   否则每敲一个字母重建一次 DOM，搜索框会丢焦点。
 * - 搜索**防抖 250ms**，别每敲一下就发一次请求。
 *
 * W1 只做"看得见"。点卡片进详情、改标签、删除在 W2。
 */

import { api } from "../../core/api.js";
import { getImportProgress } from "../import/photo-import.js";
import { slotOptionsHtml, typeOptionsHtml } from "../../core/fields.js";
import { emptyState, esc, icon, renderError, toast, serviceSettingsHtml } from "../../core/ui.js";

export const label = "衣橱";
let badgeBinding=null;
export function unmount(root){if(badgeBinding?.root===root){document.removeEventListener('import:progress',badgeBinding.listener);badgeBinding=null;}}

const PAGE_SIZE = 60;
const SEARCH_DEBOUNCE_MS = 250;

function cardHtml(item) {
  const url = api.presentationUrl(item);
  const media = url
    ? `<img src="${esc(url)}" data-flatlay-source="${esc(url)}" alt="" loading="lazy">`
    : `<span class="ph">${esc(item.type_label || "无照片")}</span>`;

  const badges = [];
  if (item.tagging_status === "pending") badges.push(`<span class="chip warn">待打标</span>`);
  if (item.is_archived) badges.push(`<span class="chip">已归档</span>`);
  if (item.favorite) badges.push(`<span class="wardrobe-liked" aria-label="喜欢的衣服">${icon("heart")}</span>`);

  return `
    <a class="card" href="#item/${esc(item.id)}" data-id="${esc(item.id)}">
      <div class="card-media">${media}</div>
      <div class="card-body">
        <p class="card-title">${esc(item.name || item.type_label || "未命名")}</p>
        <div class="card-foot">
          <span>${esc(item.type_label || "衣物")}</span>
          ${badges.join("")}
        </div>
      </div>
    </a>`;
}

export async function mount(root, ctx, arg, restore = null) {
  const taxonomy = ctx.taxonomy || {};
  const state = {
    search: restore?.search || "",
    slot: restore?.slot || "",
    type: restore?.type || "",
    archived: Boolean(restore?.archived),
    page: Math.max(1, Number(restore?.page) || 1),
    items: [],     // 已加载的单品（翻页时累积）
    total: 0,      // 库里的总数（后端返回，用于算"还有多少没显示"）
  };

  root.innerHTML = `
    <div class="view-head page-actions wardrobe-actions">
      <span class="faint wardrobe-count" id="w-count"></span>
      <div class="page-settings status">${serviceSettingsHtml(ctx.health)}</div>
    </div>
    <div class="wardrobe-tools">
      <div class="search-row">
        <label class="search-field">${icon("search")}<input type="search" id="w-search" placeholder="搜索衣服" aria-label="搜索名称、品牌或备注"></label>
        <button type="button" id="w-filter-toggle" class="filter-toggle" aria-expanded="false" aria-controls="w-filters">${icon("filter")}<span>筛选</span><span id="w-filter-count" class="filter-count" hidden></span></button>
      </div>
      <div id="w-filters" class="filter-fields" hidden>
        <label class="field"><span>分类</span><select id="w-slot">${slotOptionsHtml(taxonomy, "全部分类")}</select></label>
        <label class="field"><span>品类</span><select id="w-type">${typeOptionsHtml(taxonomy)}</select></label>
        <label class="control-toggle"><input type="checkbox" id="w-archived"> 含已归档</label>
        <button id="w-reload" class="ghost">刷新列表</button>
      </div>
      <div class="category-nav" role="group" aria-label="按衣服分类筛选">
        ${[["", "全部"], ...Object.entries(taxonomy.slots || {})].map(([key, text]) => `<button type="button" class="category${key === "" ? " active" : ""}" data-slot="${esc(key)}" aria-pressed="${key === ""}">${esc(text)}</button>`).join("")}
      </div>
    </div>
    <div id="w-body"><p class="muted">加载中…</p></div>
    <button type="button" class="wardrobe-import-fab" data-open-import aria-haspopup="dialog" aria-label="录入衣物" title="录入衣物">${icon('plus')}<span class="import-fab-badge" hidden></span></button>
  `;

  root.querySelector("[data-open-import]").onclick = () => { location.hash = "#add"; };
  if(badgeBinding)unmount(badgeBinding.root);
  const updateBadge=event=>{
    if(!root.isConnected)return;
    const progress=event.detail,button=root.querySelector('[data-open-import]'),badge=button.querySelector('.import-fab-badge');
    badge.hidden=!progress.remaining;badge.textContent=`${progress.prepared}/${progress.total}`;
    button.setAttribute('aria-label',progress.remaining?`继续录入衣物，已准备 ${progress.prepared} / ${progress.total} 件`:'录入衣物');
  };
  badgeBinding={root,listener:updateBadge};document.addEventListener('import:progress',updateBadge);
  void getImportProgress().then(detail=>updateBadge({detail}));
  root.addEventListener("wardrobe:refresh", event => {
    const cards = [...root.querySelectorAll('.card')], position = cards.findIndex(card => card.dataset.id === event.detail?.focusItemId);
    void load({ preserve: true }).then(() => {
      if (!root.isConnected || document.querySelector('.item-glass-layer') || !event.detail?.focusItemId) return;
      const fresh = [...root.querySelectorAll('.card')];
      (fresh.find(card => card.dataset.id === event.detail.focusItemId) || fresh[Math.max(0, Math.min(position, fresh.length - 1))] || root.querySelector('[data-open-import]'))?.focus({ preventScroll: true });
    });
  });

  const bodyEl = root.querySelector("#w-body");
  const countEl = root.querySelector("#w-count");
  const searchEl = root.querySelector("#w-search");
  const filterToggle = root.querySelector("#w-filter-toggle");
  const filters = root.querySelector("#w-filters");
  searchEl.value = state.search; root.querySelector("#w-slot").value = state.slot;
  root.querySelector("#w-type").value = state.type; root.querySelector("#w-archived").checked = state.archived;
  filters.hidden = !restore?.filters; filterToggle.setAttribute("aria-expanded", String(!filters.hidden));
  root.captureState = () => ({ search: searchEl.value.trim(), slot: state.slot, type: state.type,
    archived: state.archived, page: state.page, filters: !filters.hidden });
  filterToggle.addEventListener("click", () => {
    filters.hidden = !filters.hidden;
    filterToggle.setAttribute("aria-expanded", String(!filters.hidden));
  });
  filters.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    filters.hidden = true;
    filterToggle.setAttribute("aria-expanded", "false");
    filterToggle.focus();
  });
  root.querySelector(".category-nav").addEventListener("click", (ev) => {
    const button = ev.target.closest("[data-slot]");
    if (!button) return;
    state.slot = button.dataset.slot;
    state.type = "";
    root.querySelector("#w-slot").value = state.slot;
    root.querySelector("#w-type").value = "";
    load();
  });

  function syncFilters() {
    root.querySelectorAll("[data-slot]").forEach((button) => {
      const active = button.dataset.slot === state.slot;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    const count = [state.slot, state.type, state.archived].filter(Boolean).length;
    const badge = root.querySelector("#w-filter-count");
    badge.textContent = count;
    badge.hidden = count === 0;
  }

  let debounce = null;
  searchEl.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      state.search = searchEl.value.trim();
      load();
    }, SEARCH_DEBOUNCE_MS);
  });
  root.querySelector("#w-slot").addEventListener("change", (ev) => {
    state.slot = ev.target.value;
    load();
  });
  root.querySelector("#w-type").addEventListener("change", (ev) => {
    state.type = ev.target.value;
    load();
  });
  root.querySelector("#w-archived").addEventListener("change", (ev) => {
    state.archived = ev.target.checked;
    load();
  });
  root.querySelector("#w-reload").addEventListener("click", () => load());

  // 事件委托：卡片和「加载更多」按钮每次渲染都是新节点
  bodyEl.addEventListener("click", (ev) => {
    const more = ev.target.closest("#w-more");
    if (more) {
      more.disabled = true;
      more.textContent = "加载中…";
      load({ append: true });
      return;
    }
  });

  function render() {
    const items = state.items;
    const total = state.total;
    countEl.textContent = total ? `共 ${total} 件` : "";

    if (items.length === 0) {
      const filtered = state.search || state.slot || state.type || state.archived;
      bodyEl.innerHTML = filtered
        ? emptyState("没有符合条件的单品", "换个筛选条件试试")
        : emptyState(
            "衣橱还是空的",
            "先录几件衣服 —— 缺口分析和搭配建议都要从这一步开始"
          );
      return;
    }

    const hidden = total - items.length;
    bodyEl.innerHTML =
      `<div class="grid">${items.map(cardHtml).join("")}</div>` +
      (hidden > 0
        ? `<div class="more-row"><button id="w-more" class="ghost">加载更多（还有 ${hidden} 件）</button></div>`
        : "");
  }

  /** 取一页。
   *
   * `append=false`（默认）用于筛选变化/刷新：**重置**到第 1 页并清空已加载的。
   * `append=true` 用于「加载更多」：保留已有的，往后取一页。
   *
   * ⚠️ 之前这里只有"取第 1 页"一条路径（`state.page` 恒为 1，且没有翻页控件），
   * 却对用户显示「还有 N 件未显示（分页）」—— 文案承诺了做不到的事，
   * 70 件的衣橱里最后 10 件永远看不到。现在按钮是真的能翻。
   *
   * ⚠️ **竞态**：搜索防抖 + 快速切筛选会让多个请求在飞。不加序号的话，
   * 先发的慢请求可能**后**回来，把新结果覆盖掉 —— 界面与筛选条件不符（静默错）。
   * 所以用 `reqSeq` 只认最后一次请求的结果。
   */
  let reqSeq = 0;

  async function load({ append = false, preserve = false } = {}) {
    syncFilters();
    const seq = ++reqSeq;
    if (!append && !preserve) {
      state.page = 1;
      state.items = [];
      bodyEl.innerHTML = `<p class="muted">加载中…</p>`;
    } else if (append) {
      state.page += 1;
    }
    try {
      const pages = preserve ? Array.from({ length: state.page }, (_, index) => index + 1) : [state.page];
      const results = await Promise.all(pages.map(page => api.listItems({
        search: state.search, slot: state.slot, type: state.type,
        include_archived: state.archived ? "true" : "false", page, page_size: PAGE_SIZE,
      })));
      const data = { total: results[0].total, items: results.flatMap(result => result.items || []) };
      if (seq !== reqSeq) return;        // 已经不是最新那次请求了，丢弃
      state.total = data.total || 0;
      state.items = append ? state.items.concat(data.items || []) : data.items || [];
      const currentScroll = window.scrollY;
      render();
      if (preserve && root.isConnected && root === document.getElementById("view")) window.scrollTo(0, currentScroll);
    } catch (err) {
      if (seq !== reqSeq) return;
      if (preserve) { toast("刷新失败，现有衣橱仍保留", "error"); return; }
      countEl.textContent = "";
      if (append) {
        // 追加失败：回退页码，保留已加载的，只把按钮恢复可点
        state.page -= 1;
        more_sync();
        toast("加载失败：" + err.message, "error");
      } else {
        renderError(bodyEl, err);
      }
    }
  }

  /** 把「加载更多」按钮恢复成可点状态（追加失败时用）。 */
  function more_sync() {
    const more = bodyEl.querySelector("#w-more");
    if (more) {
      more.disabled = false;
      more.textContent = `加载更多（还有 ${state.total - state.items.length} 件）`;
    }
  }

  await load({ preserve: Boolean(restore) });
}
