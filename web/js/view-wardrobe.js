/** 衣橱视图：筛选 + 卡片网格。
 *
 * 两个刻意的取舍：
 * - **工具条只渲染一次**，筛选变化只重绘下面的列表 ——
 *   否则每敲一个字母重建一次 DOM，搜索框会丢焦点。
 * - 搜索**防抖 250ms**，别每敲一下就发一次请求。
 *
 * W1 只做"看得见"。点卡片进详情、改标签、删除在 W2。
 */

import { api } from "./api.js";
import { slotOptionsHtml, typeOptionsHtml } from "./fields.js";
import { emptyState, esc, labelOf, labelsOf, renderError, toast } from "./ui.js";

export const label = "衣橱";

const PAGE_SIZE = 60;
const SEARCH_DEBOUNCE_MS = 250;

function cardHtml(item, vocab) {
  const url = api.imageUrl(item.image_path);
  const media = url
    ? `<img src="${esc(url)}" alt="" loading="lazy">`
    : `<span class="ph">${esc(item.type_label || "无照片")}</span>`;

  const metaParts = [
    item.type_label,
    labelsOf(vocab.color, item.colors),
    labelOf(vocab.material, item.material),
    labelOf(vocab.fit, item.fit),
  ].filter(Boolean);

  const badges = [];
  if (item.tagging_status === "pending") badges.push(`<span class="chip warn">待打标</span>`);
  if (item.is_archived) badges.push(`<span class="chip">已归档</span>`);
  if (item.favorite) badges.push(`<span class="chip ok">常穿</span>`);

  return `
    <article class="card" data-id="${esc(item.id)}">
      <div class="card-media">${media}</div>
      <div class="card-body">
        <p class="card-title">${esc(item.name || item.type_label || "未命名")}</p>
        <p class="card-meta">${esc(metaParts.join(" · ")) || "&nbsp;"}</p>
        <div class="card-foot">
          <span>${item.wear_count > 0 ? `穿过 ${item.wear_count} 次` : "没穿过"}</span>
          ${badges.join("")}
        </div>
      </div>
    </article>`;
}

export async function mount(root, ctx) {
  const taxonomy = ctx.taxonomy || {};
  const vocab = taxonomy.vocabularies || {};
  const state = {
    search: "",
    slot: "",
    type: "",
    archived: false,
    page: 1,
    items: [],     // 已加载的单品（翻页时累积）
    total: 0,      // 库里的总数（后端返回，用于算"还有多少没显示"）
  };

  root.innerHTML = `
    <div class="view-head">
      <h1>衣橱</h1>
      <span class="faint" id="w-count"></span>
    </div>
    <div class="toolbar">
      <input type="search" id="w-search" placeholder="搜名称 / 品牌 / 备注">
      <select id="w-slot">${slotOptionsHtml(taxonomy)}</select>
      <select id="w-type">${typeOptionsHtml(taxonomy)}</select>
      <label><input type="checkbox" id="w-archived"> 含已归档</label>
      <span class="spacer"></span>
      <button id="w-reload" class="ghost">刷新</button>
    </div>
    <div id="w-body"><p class="muted">加载中…</p></div>
  `;

  const bodyEl = root.querySelector("#w-body");
  const countEl = root.querySelector("#w-count");
  const searchEl = root.querySelector("#w-search");

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
    const card = ev.target.closest(".card");
    if (card && card.dataset.id) location.hash = `#item/${card.dataset.id}`;
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
      `<div class="grid">${items.map((it) => cardHtml(it, vocab)).join("")}</div>` +
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

  async function load({ append = false } = {}) {
    const seq = ++reqSeq;
    if (!append) {
      state.page = 1;
      state.items = [];
      bodyEl.innerHTML = `<p class="muted">加载中…</p>`;
    } else {
      state.page += 1;
    }
    try {
      const data = await api.listItems({
        search: state.search,
        slot: state.slot,
        type: state.type,
        include_archived: state.archived ? "true" : "false",
        page: state.page,
        page_size: PAGE_SIZE,
      });
      if (seq !== reqSeq) return;        // 已经不是最新那次请求了，丢弃
      state.total = data.total || 0;
      state.items = append ? state.items.concat(data.items || []) : data.items || [];
      render();
    } catch (err) {
      if (seq !== reqSeq) return;
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

  await load();
}
