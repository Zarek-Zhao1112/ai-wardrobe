// §19：连接设置不切换视图，不触碰当前衣服和草稿。
import { esc, icon } from "./ui.js";

const repairMessages = {
  online: "电脑连接正常，当前地址可用。",
  repaired: "连接已修复，新地址已保存。",
  choose: "找到多台匹配的电脑，请选择要连接的电脑。",
  unconfigured: "请先填写电脑地址和配对码，连接并保存。",
  no_wifi: "请先让手机和电脑连在同一 Wi‑Fi，再试一次。",
  missing: "找不到已配对的电脑。请确认手机与电脑同网，且电脑中转已更新并正在运行。",
  unavailable: "找到已配对的电脑，但中转暂不可用。请在电脑上检查中转状态。",
  busy: "正在修复连接，请稍等片刻再试。",
};

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
      <div class="bridge-candidates" data-bridge-candidates hidden></div>
      <button type="submit" class="primary">连接并保存</button>
      <button type="button" class="ai bridge-repair" data-repair>${icon("refresh")}<span>自动修复连接</span></button>
      <button type="button" data-close>关闭</button>
    </form>
  </section>`;
  document.body.append(layer);
  const form = layer.querySelector("form");
  const result = layer.querySelector("[data-result]");
  const choices = form.querySelector("[data-bridge-candidates]");
  const repair = form.querySelector("[data-repair]");
  let busy = false, returning = false;
  const setBusy = value => {
    busy = value;
    form.querySelectorAll("input,button").forEach(node => { node.disabled = value; });
  };
  const showResult = (message, tone = "") => {
    result.textContent = message;
    result.dataset.tone = tone;
  };
  const clearChoices = () => {
    choices.hidden = true;
    choices.replaceChildren();
  };
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
    const target = visible(origin) ? origin : [...document.querySelectorAll("#status summary")].find(visible);
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
  const autoRepair = async candidate => {
    if (busy) return;
    clearChoices();
    setBusy(true);
    repair.querySelector("span").textContent = "修复中…";
    layer.querySelector("h2").focus();
    showResult(candidate ? "正在验证所选电脑…" : "正在检查连接并查找已配对的电脑…");
    try {
      const response = await ctx.api.repairBridge(candidate);
      const { status, bridge } = response;
      const ok = status === "repaired" || status === "online";
      showResult(repairMessages[status] || "暂时无法修复电脑连接，请稍后重试。", ok ? "success" : status === "choose" ? "" : "warning");
      if (status === "repaired") {
        form.elements.url.value = bridge.url;
        form.elements.token.value = "";
        ctx.health = { ...ctx.health, desktop_bridge: bridge };
        try { ctx.health = await ctx.api.health(); } catch { /* 地址已保存，沿用现有状态。 */ }
        onSaved();
      } else if (status === "choose") {
        for (const computer of response.candidates) {
          const option = document.createElement("button");
          option.type = "button";
          option.textContent = computer.url;
          option.onclick = () => void autoRepair(computer.ip);
          choices.append(option);
        }
        choices.hidden = false;
      }
    } catch (error) {
      showResult(error?.message || "暂时无法修复电脑连接，请稍后重试。", "warning");
    } finally {
      setBusy(false);
      repair.querySelector("span").textContent = "自动修复连接";
      (choices.querySelector("button") || repair).focus();
    }
  };
  repair.onclick = () => void autoRepair();
  form.onsubmit = async event => {
    event.preventDefault();
    if (busy) return;
    clearChoices();
    setBusy(true);
    const settings = { url: form.elements.url.value.trim(), token: form.elements.token.value.trim() };
    layer.querySelector("h2").focus();
    showResult("正在连接电脑…");
    try {
      await ctx.api.configureBridge(settings);
      form.elements.token.value = "";
      // 保存接口已成功即显示事实；健康状态刷新失败仍允许用户重试。
      showResult("连接已保存。返回录入页后可点击「重新识别」或「重新检查服务」。", "success");
      try { ctx.health = await ctx.api.health(); onSaved(); }
      catch { showResult("连接已保存，状态刷新失败。关闭设置后重新打开应用即可更新状态。", "warning"); }
    } catch (error) {
      showResult(error?.message || "连接未成功，请检查地址和配对码。", "warning");
    } finally {
      setBusy(false);
      layer.querySelector("[data-close]").focus();
    }
  };
  form.elements.url.focus();
}
