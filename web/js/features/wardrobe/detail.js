// §33：衣物详情只读，翻卡查看属性。
import { api } from "../../core/api.js";
import { esc, icon, labelOf, toast, renderError } from "../../core/ui.js";
import { garmentProfileHtml } from "../../components/garment-profile.js";

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
      const buttons = [...root.querySelectorAll("button"), ...(root.deleteDialog?.querySelectorAll("button") || [])];
      buttons.forEach(button => button.disabled = true);
      try { await action(); }
      catch (error) { if (current()) { if (root.deleteDialog) root.deleteDialog.querySelector("[data-delete-error]").textContent = error.message || "删除未成功，请重试"; else toast(error.message || "本次操作未成功，请重试", "error"); } }
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
