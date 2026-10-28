// §24：详情只显示平铺图；新结果应用失败时仅重试保存，不重复生图。
import { api } from "./api.js";
import { controls, generate, flatlayBusy } from "./flatlay.js";
import { loadVisualDraft, saveVisualDraft } from "./import-store.js";
import { toast } from "./ui.js";
import { setFlatlayImage, layoutMessage } from "./flatlay-display.js";

export async function mountItemFlatlay(host, ctx, item, gate) {
  const root = host.closest("[data-item-root]"), media = root.querySelector(".detail-media");
  let state = { selected: true, view: "display" }, preview = null, previewUrl = null, durable = true, loading = true, saving = false;
  const hash = item.image_hash, valid = () => host.isConnected && root.dataset.closing !== "true" && item.image_hash === hash;
  const persist = async () => {
    try { await saveVisualDraft(item.id, { state, sourceHash: hash, file: preview }); durable = true; }
    catch { durable = false; if (valid()) toast("本次结果暂未保存到本机，请保持应用打开", "error"); }
  };
  function picture() {
    const src = previewUrl || api.presentationUrl(item);
    if (!valid() || !src) return;
    let image = media.querySelector("img");
    if (!image) { media.querySelector(".ph")?.remove(); image = document.createElement("img"); image.alt = item.name || "衣物平铺图"; media.prepend(image); }
    void setFlatlayImage(image, src, state.view !== "full").then(result => {
      const note = host.querySelector("[data-flatlay-layout-note]");
      if (valid() && note && image.dataset.flatlaySource === src) note.textContent = layoutMessage(result.status);
    });
  }
  function render() {
    if (!valid()) return;
    root.dataset.flatlayRunning = String(flatlayBusy(state));
    host.innerHTML = controls(state, { disabled: loading || saving || (gate.busy && gate.owner !== "flatlay") || !item.image_path, configured: Boolean(ctx.health?.flatlay_configured), hasImage: Boolean(preview || item.display_image_path), needsApply: Boolean(preview) });
    host.querySelectorAll("[data-flatlay]").forEach(button => button.onclick = () => void action(button.dataset.flatlay)); picture();
  }
  async function apply() {
    if (!valid() || !preview) return;
    saving = true; render();
    try {
      const fresh = await api.savePresentation(item.id, preview, hash);
      if (!valid()) return;
      Object.assign(item, fresh); preview = null; state.error = ""; state.state = "completed"; state.selected = true;
      if (previewUrl) URL.revokeObjectURL(previewUrl); previewUrl = null;
      await persist(); root.dispatchEvent(new Event("item:updated", { bubbles: true }));
    } catch (error) { if (valid()) { state.error = error.message || "平铺图还未保存，请重试保存"; await persist(); } }
    finally { saving = false; render(); }
  }
  async function start(resume = false) {
    if (!valid() || saving || loading || (!resume && flatlayBusy(state))) return;
    try {
      let file = null;
      if (!resume) {
        loading = true; render();
        const response = await fetch(api.imageUrl(item.image_path, item.updated_at));
        if (!response.ok) throw Error("暂时读不到原照片，请稍后重试");
        const blob = await response.blob();
        if (!blob.size || blob.size > 10 * 1024 * 1024) throw Error("原照片为空或超过10MB");
        file = new File([blob], "source", { type: blob.type });
        if (!valid()) return;
        state.autoApply = true;
      }
      loading = false;
      await generate({ state, file, kind: item.type, resume, valid, durable: () => durable,
        changed: async () => { if (valid()) { await persist(); render(); } },
        completed: async result => {
          if (!valid()) return;
          if (previewUrl) URL.revokeObjectURL(previewUrl);
          preview = result; previewUrl = URL.createObjectURL(result); state.selected = true; await persist();
          if (state.autoApply) await apply();
        },
      });
    } catch (error) { if (valid()) { state.state = "failed"; state.error = error.message; } }
    finally { loading = false; render(); }
  }
  async function action(name) {
    if (name === "settings") { document.querySelector("[data-desktop-settings]")?.click(); return; }
    if (loading || saving || gate.busy || flatlayBusy(state)) return;
    if (name === "full") { state.view = state.view === "full" ? "display" : "full"; await persist(); render(); return; }
    if (name === "apply") { await gate.run("flatlay", apply); return; }
    if (name === "generate" || name === "restart") await gate.run("flatlay", () => start(name === "generate" && state.state === "waiting"));
  }
  gate.changed = render; host.addEventListener("flatlay:service", render); render();
  const observer = new MutationObserver(() => { if (!host.isConnected) { if (previewUrl) URL.revokeObjectURL(previewUrl); observer.disconnect(); } });
  observer.observe(document.body, { childList: true, subtree: true });
  await gate.run("flatlay", async () => {
    try {
      const stored = await loadVisualDraft(item.id); if (!valid()) return;
      if (stored?.sourceHash === hash) {
        state = { ...stored.state, selected: true, view: "display" }; preview = stored.file;
        if (preview) previewUrl = URL.createObjectURL(preview);
      } else if (stored) await saveVisualDraft(item.id, null);
    } finally { loading = false; render(); }
    if (valid() && state.id && ["starting", "running"].includes(state.state)) await start(true);
  });
}
