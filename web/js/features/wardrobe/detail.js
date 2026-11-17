// §33：衣物详情翻卡查看属性；§64：入库后改属性走「编辑衣物信息」弹层。
import { api } from "../../core/api.js";
import { esc, icon, labelOf, toast, renderError } from "../../core/ui.js";
import { garmentProfileHtml } from "../../components/garment-profile.js";
import { reviewFieldsHtml, readForm, fillForm } from "../../core/fields.js";

export const label = "单品", hidden = true;
export function refreshService() {}

function attributeRows(rows) {
  return rows.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd${value ? "" : ' class="faint"'}>${esc(value || "暂未确认")}</dd></div>`).join("");
}

export async function mount(root, ctx, id, restore = null) {
  root.dataset.itemRoot = "true";
  const current = () => root.isConnected && root.dataset.closing !== "true";
  root.innerHTML = '<p class="faint">正在读取衣物…</p>';
  let item;
  try { item = await api.getItem(id); }
  catch (error) { if (current()) renderError(root, error); return; }
  if (!current()) return;
  root.latestItem = item;
  const vocab = ctx.taxonomy?.vocabularies || {};
  const known = raw => typeof raw === "string" && raw.trim() && !["unknown", "未知", "未识别", "暂未确认"].includes(raw.trim().toLowerCase());
  const value = (key, raw) => Array.isArray(raw) ? raw.filter(known).map(v => labelOf(vocab[key], v)).filter(Boolean).join("、") : known(raw) ? labelOf(vocab[key], raw) : "";
  const type = known(item.type_label) ? item.type_label : "";
  const name = known(item.name) ? item.name : type || "未命名";
  const colors = [...new Set([item.primary_color, ...(Array.isArray(item.colors) ? item.colors : [])])];
  const basic = attributeRows([
    ["品类", type], ["颜色", value("color", colors)], ["材质", value("material", item.material)],
    ["季节", value("season", item.season)], ["版型", value("fit", item.fit)], ["风格", value("style", item.style)],
  ]);
  const extra = attributeRows([
    ["图案", value("pattern", item.pattern)], ["品牌", known(item.brand) ? item.brand : ""],
    ...(item.slot === "bottom" || known(item.length) ? [["裤长", value("length", item.length)]] : []),
    ["正式度", value("formality", item.formality)], ["场合", value("scene", item.scene)],
    ["备注", known(item.notes) ? item.notes : ""],
  ]);
  const url = api.presentationUrl(item);
  root.innerHTML = `<div class="item-detail-card" data-flipped="false">
      <button type="button" class="item-detail-flip" data-flip aria-expanded="false" aria-controls="item-detail-info" aria-label="查看衣物信息" title="查看衣物信息">
        <span data-flip-open>${icon("clipboard")}</span><span data-flip-back hidden>${icon("replay")}</span>
      </button>
      <div class="item-detail-stack">
        <section class="item-detail-face item-detail-front" aria-label="衣物照片">
          <div class="detail-media">${url ? `<img src="${esc(url)}" data-flatlay-source="${esc(url)}" alt="${esc(name)}">` : '<span class="ph">暂无衣物照片</span>'}</div>
          <div class="item-detail-caption"><h1>${esc(name)}</h1><p>${esc(type || "品类暂未确认")}</p></div>
        </section>
        <section class="item-detail-face item-detail-back" id="item-detail-info" aria-labelledby="item-detail-info-title" aria-hidden="true" inert>
          <header><h2 id="item-detail-info-title">衣物信息</h2><p>${esc(name)}</p></header>
          <div class="item-detail-info-scroll">
            <button type="button" class="item-edit-entry" data-edit>${icon("edit")}编辑衣物信息</button>
            <dl class="item-detail-attributes">${basic}</dl>
            <details class="item-detail-more">
              <summary>更多信息 ${icon("chevron")}</summary>
              <dl class="item-detail-attributes">${extra}</dl>
              ${garmentProfileHtml(item, ctx.taxonomy, { mode: "detail", disclosure: false })}
            </details>
          </div>
        </section>
      </div>
    </div>
    <div class="item-readonly-actions">
      <button type="button" class="primary item-use-outfit" data-use-outfit>${icon("outfits")}用这件搭配</button>
      <button type="button" class="item-heart" data-favorite>${icon("heart")}</button>
      <button type="button" class="item-delete-button" data-delete>删除这件衣服</button>
    </div>`;
  const scroll = root.closest(".item-glass-scroll"), heart = root.querySelector("[data-favorite]");
  const card = root.querySelector(".item-detail-card");
  const front = card.querySelector(".item-detail-front"), back = card.querySelector(".item-detail-back");
  const flip = card.querySelector("[data-flip]");
  const openIcon = flip.querySelector("[data-flip-open]"), backIcon = flip.querySelector("[data-flip-back]");
  const more = back.querySelector(".item-detail-more"), infoScroll = back.querySelector(".item-detail-info-scroll");
  const summary = more.querySelector("summary");
  let flipped = false;
  function setFlipped(showInfo) {
    flipped = showInfo;
    card.dataset.flipped = String(flipped);
    front.inert = flipped;
    back.inert = !flipped;
    front.setAttribute("aria-hidden", String(flipped));
    back.setAttribute("aria-hidden", String(!flipped));
    summary.tabIndex = flipped ? 0 : -1;
    flip.setAttribute("aria-expanded", String(flipped));
    const action = flipped ? "翻回照片" : "查看衣物信息";
    flip.setAttribute("aria-label", action);
    flip.title = action;
    openIcon.hidden = flipped;
    backIcon.hidden = !flipped;
  }
  setFlipped(Boolean(restore?.flipped));
  more.open = Boolean(restore?.more);
  infoScroll.scrollTop = restore?.infoScrollTop || 0;
  root.captureDetail = () => ({
    flipped, more: more.open, infoScrollTop: infoScroll.scrollTop, scrollTop: scroll?.scrollTop || 0,
  });
  root.flipDetailBack = () => {
    if (!flipped || flip.disabled || !current()) return false;
    flip.focus({ preventScroll: true });
    setFlipped(false);
    return true;
  };
  flip.onclick = () => {
    if (back.contains(document.activeElement)) flip.focus({ preventScroll: true });
    setFlipped(!flipped);
  };
  const changed = () => root.dispatchEvent(new Event("item:updated", { bubbles: true }));
  const syncHeart = () => {
    heart.setAttribute("aria-pressed", String(Boolean(item.favorite)));
    heart.setAttribute("aria-label", item.favorite ? "取消喜欢" : "喜欢这件");
    heart.title = item.favorite ? "取消喜欢" : "喜欢这件";
  };
  syncHeart();
  if (scroll && restore) scroll.scrollTop = restore.scrollTop || 0;
  const gate = root.detailGate = {
    busy: false,
    async run(action) {
      if (this.busy || !current()) return;
      this.busy = true;
      const buttons = [
        ...root.querySelectorAll("button"),
        ...(root.deleteDialog?.querySelectorAll("button") || []),
        ...(root.editDialog?.querySelectorAll("button") || []),
      ];
      buttons.forEach(button => button.disabled = true);
      try { await action(); }
      catch (error) {
        if (current()) {
          // 弹层里的错误就地显示：toast 会被玻璃卡片盖住，用户看不到保存失败。
          const box = root.deleteDialog?.querySelector("[data-delete-error]")
            || root.editDialog?.querySelector("[data-edit-error]");
          if (box) box.textContent = error.message || "本次操作未成功，请重试";
          else toast(error.message || "本次操作未成功，请重试", "error");
        }
      }
      finally { this.busy = false; buttons.forEach(button => button.disabled = false); }
    },
  };
  heart.onclick = () => void gate.run(async () => {
    const fresh = await api.updateItem(id, { favorite: !item.favorite });
    if (!current()) return;
    item = fresh; root.latestItem = fresh; syncHeart(); changed();
  });
  root.querySelector("[data-use-outfit]").onclick = () => {
    if (!gate.busy && current()) root.dispatchEvent(new CustomEvent("item:outfit", { bubbles: true, detail: { id } }));
  };

  // ── §64 编辑衣物信息 ────────────────────────────────────────────────
  // 补的是这条断掉的路：入库之后发现标错，以前只有「整件重新识别」一个办法，
  // 而重认会把 AI 已经打对的字段一起覆盖 —— 等于拿整件重来换一个字段。
  //
  // 表单不另写一套：`reviewFieldsHtml` 与录入页逐件确认共用，
  // `fillForm` / `readForm` 的产物直接就是 `PATCH /items/{id}` 的请求体。
  // 弹层样式复用删除确认那套玻璃卡片，翻卡 / inert 状态一律不碰。
  const EDIT_EMPTY = { colors: [], style: [], season: [], scene: [] };
  /** 只把**真改过**的字段放进 PATCH。
   *
   *  全量提交也能跑（`readForm` 保证"什么都不改 = no-op"），但会把
   *  `colors` 归一化、主色兜底这些副作物一并写库 ——
   *  一次"只改名称"的保存不该在别的字段上留下 diff。 */
  function changedFields(payload) {
    const patch = {};
    for (const [key, next] of Object.entries(payload)) {
      const before = item[key] ?? EDIT_EMPTY[key] ?? null;
      const same = Array.isArray(next) && Array.isArray(before)
        ? next.length === before.length && next.every((v, i) => v === before[i])
        : String(next ?? "") === String(before ?? "");
      if (!same) patch[key] = next;
    }
    return patch;
  }
  const panelOf = () => root.closest(".item-glass-panel") || root;
  root.dismissEdit = () => {
    if (!root.editDialog || gate.busy) return false;
    root.editDialog.remove(); root.editDialog = null;
    panelOf().classList.remove("item-editing");
    for (const [node, aria] of root.editBackground || []) {
      if (aria === null) node.removeAttribute("aria-hidden");
      else node.setAttribute("aria-hidden", aria);
    }
    root.editBackground = null;
    root.querySelector("[data-edit]")?.focus({ preventScroll: true });
    return true;
  };
  async function saveEdit(dialog, body) {
    const errorEl = dialog.querySelector("[data-edit-error]");
    errorEl.textContent = "";
    const patch = changedFields(readForm(body, item));
    if (!Object.keys(patch).length) {
      // 还没进 gate，`busy` 必然是 false —— 关弹层的闸门不会拦。
      root.dismissEdit();
      toast("没有改动");
      return;
    }
    await gate.run(async () => {
      const fresh = await api.updateItem(id, patch);
      if (!current()) return;
      item = fresh; root.latestItem = fresh;
      const state = root.captureDetail();
      gate.busy = false; root.dismissEdit(); gate.busy = true;
      changed();  // sheet.js 记 dirty，关闭详情时列表跟着刷新
      // 属性块是拼好的字符串（`basic` / `extra` 在 mount 开头算死），
      // 就地改文本要重算两遍还得处理 details 里的 garment-profile ——
      // 重挂一次最省事，`restore` 保住翻卡位置、折叠状态与滚动。
      await mount(root, ctx, id, state);
      toast("已更新衣物信息", "ok");
    });
  }
  function openEditor() {
    if (gate.busy || root.editDialog || !current()) return;
    const dialog = document.createElement("div");
    dialog.className = "item-edit-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "item-edit-title");
    dialog.tabIndex = -1;
    dialog.innerHTML = `<div class="item-edit-glass">
        <header class="item-edit-head">
          <h2 id="item-edit-title">编辑衣物信息</h2>
          <p>${esc(item.name || item.type_label || "这件衣服")}</p>
        </header>
        <div class="item-edit-body">${reviewFieldsHtml(ctx.taxonomy)}<p class="item-edit-error" data-edit-error role="alert"></p></div>
        <footer class="item-edit-actions">
          <button type="button" class="ghost" data-cancel-edit>取消</button>
          <button type="button" class="primary" data-save-edit>保存</button>
        </footer>
      </div>`;
    const panel = panelOf();
    root.editDialog = dialog;
    root.editBackground = [...panel.children].map(node => [node, node.getAttribute("aria-hidden")]);
    root.editBackground.forEach(([node]) => node.setAttribute("aria-hidden", "true"));
    panel.append(dialog);
    panel.classList.add("item-editing");
    const body = dialog.querySelector(".item-edit-body");
    fillForm(body, item);
    // 「更多属性」默认展开：点编辑就是来改的，再让人多点一下纯属添堵。
    body.querySelector("details")?.setAttribute("open", "");
    dialog.querySelectorAll("[data-cancel-edit]").forEach(button => { button.onclick = root.dismissEdit; });
    dialog.querySelector("[data-save-edit]").onclick = () => void saveEdit(dialog, body);
    dialog.onclick = event => { if (event.target === dialog) root.dismissEdit(); };
    (body.querySelector('input[name="name"]') || dialog).focus({ preventScroll: true });
  }
  root.querySelector("[data-edit]").onclick = openEditor;
  root.dismissDelete = () => {
    if (!root.deleteDialog || gate.busy) return false;
    root.deleteDialog.remove(); root.deleteDialog = null;
    const panel = root.closest(".item-glass-panel") || root;
    panel.classList.remove("item-confirming-delete");
    for (const [node, aria] of root.deleteBackground || []) { if (aria === null) node.removeAttribute("aria-hidden"); else node.setAttribute("aria-hidden", aria); }
    root.querySelector("[data-delete]")?.focus({ preventScroll: true });
    return true;
  };
  root.querySelector("[data-delete]").onclick = () => {
    if (gate.busy || root.deleteDialog || !current()) return;
    const panel = root.closest(".item-glass-panel") || root;
    const dialog = document.createElement("div");
    dialog.className = "item-delete-dialog"; dialog.setAttribute("role", "alertdialog"); dialog.setAttribute("aria-modal", "true"); dialog.setAttribute("aria-labelledby", "item-delete-title"); dialog.setAttribute("aria-describedby", "item-delete-copy"); dialog.tabIndex = -1;
    dialog.innerHTML = `<div class="item-delete-glass"><span class="item-delete-symbol" aria-hidden="true">${icon("wardrobe")}</span><h2 id="item-delete-title">删除这件衣服？</h2><p id="item-delete-copy">「${esc(item.name || item.type_label || "这件衣服")}」将从衣橱移除。</p><p class="item-delete-error" data-delete-error role="alert"></p><button type="button" class="item-delete-button" data-confirm-delete>确认删除</button><button type="button" class="ghost" data-cancel-delete>再想想</button></div>`;
    root.deleteDialog = dialog;
    root.deleteBackground = [...panel.children].map(node => [node, node.getAttribute("aria-hidden")]);
    root.deleteBackground.forEach(([node]) => node.setAttribute("aria-hidden", "true"));
    panel.append(dialog); panel.classList.add("item-confirming-delete");
    dialog.querySelector("[data-cancel-delete]").onclick = root.dismissDelete;
    dialog.querySelector("[data-confirm-delete]").onclick = () => void gate.run(async () => {
      await api.deleteItem(id);
      if (!current()) return;
      root.deleted = true; changed();
      document.dispatchEvent(new CustomEvent("wardrobe:item-removed", { detail: { id } }));
      // 解除内层后再关闭详情；退出动效仍由外层玻璃卡片统一执行。
      gate.busy = false; root.dismissDelete(); gate.busy = true;
      root.dispatchEvent(new Event("item:close", { bubbles: true })); toast("已删除这件衣服", "ok");
    });
    dialog.onclick = event => { if (event.target === dialog) root.dismissDelete(); };
    dialog.querySelector("[data-cancel-delete]").focus({ preventScroll: true });
  };
}
