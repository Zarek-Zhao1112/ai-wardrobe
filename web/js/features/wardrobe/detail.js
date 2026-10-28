// §24：详情只读，喜欢、用这件搭配与确认删除均保留原位置。
import { api } from "../../core/api.js";
import { esc, icon, labelOf, toast, renderError } from "../../core/ui.js";

export const label = "单品", hidden = true;
export function refreshService() {}

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
  const known = raw => raw && !["unknown", "未知", "未识别"].includes(raw);
  const value = (key, raw) => Array.isArray(raw) ? raw.filter(known).map(v => labelOf(vocab[key], v)).filter(Boolean).join("、") : known(raw) ? labelOf(vocab[key], raw) : "";
  const color = value("color", item.primary_color);
  const rows = pairs => pairs.filter(([, val]) => val && String(val).trim()).map(([label, val]) => `<div><span>${esc(label)}</span><span>${esc(val)}</span></div>`).join("");
  const summary = rows([["品类", item.type === "unknown" ? "" : item.type_label], ["颜色", color], ["品牌", known(item.brand) ? item.brand : ""]]);
  const more = rows([["材质", value("material", item.material)], ["版型", value("fit", item.fit)], ["图案", value("pattern", item.pattern)], ["长度", value("length", item.length)], ["风格", value("style", item.style)], ["季节", value("season", item.season)]]);
  const url = api.presentationUrl(item);
  root.innerHTML = `<div class="detail-media">${url ? `<img src="${esc(url)}" data-flatlay-source="${esc(url)}" alt="${esc(item.name || item.type_label || "衣物")}">` : '<span class="ph">暂无衣物平铺图</span>'}</div>
    <div class="item-title-row"><h1>${esc(item.name || item.type_label || "未命名")}</h1><button type="button" class="item-heart" data-favorite>${icon("heart")}</button></div>
    <p class="item-subtitle">${esc([item.type_label, color].filter(Boolean).join(" · "))}</p>
    ${summary ? `<div class="item-summary">${summary}</div>` : ""}
    ${more ? `<details class="item-more"><summary>更多属性</summary><div class="item-summary">${more}</div></details>` : ""}
    <div class="item-readonly-actions"><button type="button" class="primary item-use-outfit" data-use-outfit>${icon("outfits")}用这件搭配</button><button type="button" class="item-delete-button" data-delete>删除这件衣服</button></div>`;
  const scroll = root.closest(".item-glass-scroll"), heart = root.querySelector("[data-favorite]");
  const changed = () => root.dispatchEvent(new Event("item:updated", { bubbles: true }));
  const syncHeart = () => {
    heart.setAttribute("aria-pressed", String(Boolean(item.favorite)));
    heart.setAttribute("aria-label", item.favorite ? "取消喜欢" : "喜欢这件");
    heart.title = item.favorite ? "取消喜欢" : "喜欢这件";
  };
  syncHeart();
  root.captureDetail = () => ({ more: Boolean(root.querySelector(".item-more")?.open), scrollTop: scroll?.scrollTop || 0 });
  if (restore?.more && root.querySelector(".item-more")) root.querySelector(".item-more").open = true;
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
