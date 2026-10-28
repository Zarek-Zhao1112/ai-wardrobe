// §19：连接设置不切换视图，不触碰当前衣服和草稿。
import { esc } from "./ui.js";

// 同一会话的前进恢复设置层，避免留下不可见的历史占位。
const historyEntries = new Map();
let restoreContext = null;
window.addEventListener("popstate", () => {
  const entry = history.state?.bridgeSettings, saved = historyEntries.get(entry) || restoreContext;
  if (!entry) return;
  if (saved && !document.querySelector("[data-bridge-settings]")) openBridgeSettings(saved.ctx, saved.onSaved, saved.returnFocus, entry);
});
export function restoreBridgeSettings(ctx, onSaved) {
  restoreContext = { ctx, onSaved, returnFocus: null };
  const entry = history.state?.bridgeSettings;
  if (entry) openBridgeSettings(ctx, onSaved, null, entry);
}

export function openBridgeSettings(ctx, onSaved, returnFocus = null, restoreEntry = null) {
  if (document.querySelector("[data-bridge-settings]")) return;
  const before = document.activeElement;
  const origin = returnFocus || before?.closest(".status-menu")?.querySelector("summary") || before;
  const layer = document.createElement("div");
  layer.className = "import-modal";
  layer.dataset.bridgeSettings = "";
  layer.innerHTML = `<section role="dialog" aria-modal="true" aria-labelledby="bridge-title" class="import-sheet bridge-sheet">
    <h2 id="bridge-title" tabindex="-1">电脑识别设置</h2>
    <p class="muted">使用电脑已登录的个人账号，模型为 DS4.1。手机与电脑需在同一网络，电脑中转保持运行。</p>
    <form>
      <label>电脑地址<input name="url" type="url" required maxlength="200" inputmode="url" autocapitalize="off" spellcheck="false" placeholder="http://192.168.1.2:8891" value="${esc(ctx.health?.desktop_bridge?.url || "")}"></label>
      <label>配对码<input name="token" type="password" required minlength="32" maxlength="128" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="从电脑中转复制完整配对码"></label>
      <p class="faint">测试连接不调用模型。账号登录是否有效，会在识别时校验。</p>
      <p data-result role="status" aria-live="polite"></p>
      <button type="submit" class="primary">连接并保存</button>
      <button type="button" data-close>关闭</button>
    </form>
  </section>`;
  document.body.append(layer);
  const form = layer.querySelector("form");
  const result = layer.querySelector("[data-result]");
  let busy = false, returning = false;
  // §25.4：设置单独消费一次返回，不让底下的添置结果同时退出。
  const entry = restoreEntry || crypto.randomUUID?.() || String(Date.now());
  const url = location.href;
  historyEntries.set(entry, { ctx, onSaved, returnFocus: origin });
  if (!restoreEntry) history.pushState({ ...history.state, bridgeSettings: entry }, "", url);
  const close = (fromHistory = false) => {
    if (busy) return;
    if (!fromHistory && history.state?.bridgeSettings === entry && location.href === url) {
      if (!returning) { returning = true; history.back(); }
      return;
    }
    layer.remove(); document.removeEventListener("keydown", key); window.removeEventListener("popstate", pop);
    const visible = node => node !== document.body && node !== document.documentElement && node?.isConnected && node.getClientRects().length && getComputedStyle(node).visibility !== "hidden";
    const target = visible(origin) ? origin : [...document.querySelectorAll(".page-settings summary,#status summary")].find(visible);
    target?.focus({ preventScroll: true });
  };
  const pop = () => {
    returning = false;
    if (busy) { history.pushState({ ...history.state, bridgeSettings: entry }, "", url); return; }
    close(true);
  };
  const key = event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); close(); }
    if (event.key === "Tab") {
      const inputs = [...layer.querySelectorAll("input,button")].filter(node => !node.disabled);
      if (!inputs.length) { event.preventDefault(); layer.querySelector("h2").focus(); return; }
      if (event.shiftKey && document.activeElement === inputs[0]) { event.preventDefault(); inputs[inputs.length - 1].focus(); }
      else if (!event.shiftKey && document.activeElement === inputs[inputs.length - 1]) { event.preventDefault(); inputs[0].focus(); }
    }
  };
  document.addEventListener("keydown", key);
  window.addEventListener("popstate", pop);
  layer.querySelector("[data-close]").onclick = () => close();
  form.onsubmit = async event => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    const settings = { url: form.elements.url.value.trim(), token: form.elements.token.value.trim() };
    form.querySelectorAll("input,button").forEach(node => { node.disabled = true; });
    layer.querySelector("h2").focus();
    result.textContent = "正在连接电脑…";
    try {
      await ctx.api.configureBridge(settings);
      form.elements.token.value = "";
      // 保存接口已成功即显示事实；健康状态刷新失败仍允许用户重试。
      result.textContent = "连接已保存。返回录入页后可点击「重新识别」或「重新检查服务」。";
      try { ctx.health = await ctx.api.health(); onSaved(); }
      catch { result.textContent = "连接已保存，状态刷新失败。关闭设置后重新打开应用即可更新状态。"; }
    } catch (error) {
      result.textContent = error?.message || "连接未成功，请检查地址和配对码。";
    } finally {
      busy = false;
      form.querySelectorAll("input,button").forEach(node => { node.disabled = false; });
      layer.querySelector("[data-close]").focus();
    }
  };
  form.elements.url.focus();
}
