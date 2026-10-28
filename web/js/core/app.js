/** 前端外壳：顶栏 + 视图路由 + 共享上下文。
 *
 * 加一个页面只要两步：
 *   1) 写一个导出 `label` 和 `mount(root, ctx)` 的模块
 *   2) 在 VIEWS 里加一行
 *
 * 路由用 hash（`#wardrobe`），所以刷新页面不会丢当前页，也不需要后端配合。
 */

import { api } from "./api.js";
import { watchFlatlayImages } from "../components/flatlay-display.js";
import { esc, icon, toast, serviceSettingsHtml } from "./ui.js";
import { guardImportNavigation, refreshImportService } from "../features/import/photo-import.js";
import { openBridgeSettings, restoreBridgeSettings } from "./desktop-settings.js";
import { canReveal, capturePage, revealPage, cancelReveal, revealRunning } from "../components/page-transition.js";
import * as add from "../features/import/view.js";
import { openImportSheet, closeImportSheet, importSheetOpen } from "../features/import/sheet.js";
import * as gaps from "../features/purchase/view.js";
import { closePurchaseSheet } from "../features/purchase/sheet.js";
import { closeTryonSheet } from "../features/outfits/tryon-sheet.js";
import * as item from "../features/wardrobe/detail.js";
import { openItemSheet, closeItemSheet, itemSheetOpen, itemSheetRoot, itemSheetReturn, itemSheetHistory, refreshItemSheet } from "../features/wardrobe/sheet.js";
import * as outfits from "../features/outfits/view.js";
import * as wardrobe from "../features/wardrobe/view.js";

/** 已实现的页面。后面几波会往这里加：缺口 / 搭配。
 *
 * `hidden: true` 的页面**不出现在导航里**，只能通过 hash 进去
 * （例如 `#item/it_xxx` 是详情页，它不该占一个 Tab）。
 */
const VIEWS = [
  { id: "wardrobe", label: "衣橱", mod: wardrobe },
  { id: "add", label: "录入", mod: add, hidden: true, parent: "wardrobe" },
  { id: "gaps", label: "添置", mod: gaps },
  { id: "outfits", label: "搭配", mod: outfits },
  { id: "item", label: "单品", mod: item, hidden: true, parent: "wardrobe" },
];

let rootEl = document.getElementById("view");
watchFlatlayImages();
const navEl = document.getElementById("nav");
const tabbarEl = document.getElementById("tabbar");
const statusEl = document.getElementById("status");
let renderSequence = 0, pressedTab = null, pendingTurn = null, queuedTab = null;

function clearTurns() { pressedTab = null; pendingTurn = null; queuedTab = null; cancelReveal(); }

function navigateTab(target) {
  if (target.hash === (location.hash || "#wardrobe")) return;
  try {
    const snapshot = capturePage([document.querySelector(".topbar"), rootEl, tabbarEl]);
    pendingTurn = { ...target, from: location.hash, snapshot };
  } catch { pendingTurn = null; }
  location.hash = target.hash;
}

// §21：使用按下落点，但先确认一次有效轻点；拖动、取消和键盘不复用旧触点。
tabbarEl?.addEventListener("pointerdown", event => {
  pressedTab = null;
  const tab = event.target.closest("a.tab");
  if (!tab || event.button !== 0 || !event.isPrimary || !canReveal(rootEl, tabbarEl)) return;
  pressedTab = { hash: tab.hash, x: event.clientX, y: event.clientY, pointer: event.pointerId,
    from: location.hash, time: performance.now() };
});
document.addEventListener("pointermove", event => {
  if (pressedTab?.pointer === event.pointerId && Math.hypot(event.clientX - pressedTab.x, event.clientY - pressedTab.y) > 10) pressedTab = null;
}, { passive: true });
document.addEventListener("pointercancel", () => { pressedTab = null; });
tabbarEl?.addEventListener("contextmenu", () => { pressedTab = null; });
tabbarEl?.addEventListener("click", event => {
  const tab = event.target.closest("a.tab"), point = pressedTab;
  pressedTab = null;
  if (!tab || event.button !== 0 || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
  if (tab.hash === (location.hash || "#wardrobe")) { event.preventDefault(); queuedTab = null; return; }
  if (!event.detail || !point || point.hash !== tab.hash || point.from !== location.hash ||
      performance.now() - point.time > 700 || !canReveal(rootEl, tabbarEl)) return;
  event.preventDefault();
  const target = { hash: tab.hash, point: { x: point.x, y: point.y } };
  if (revealRunning()) queuedTab = target;
  else navigateTab(target);
});
window.addEventListener("resize", clearTurns);
window.visualViewport?.addEventListener("resize", clearTurns);
document.addEventListener("visibilitychange", () => { if (document.hidden) clearTurns(); });
matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", clearTurns);

/**
 * 启动状态，**给 index.html 的看门狗一个可判定的答案**。
 *
 * 为什么要这个（2026-10-22）：看门狗原来靠"#view 的文本里还有没有『加载中』"
 * 猜页面有没有画完。这个判据是错的 —— 衣橱页点「加载更多」时，按钮文字
 * 就是"加载中…"（`view-wardrobe.js`），那时整页是好的，却会被判成"没内容"
 * 然后被错误面板覆盖；而数据回来时写入的是**已经被换掉的节点**，
 * 页面就永久停在那张面板上，只能手动刷新。
 *
 * 现在由 app.js 自己汇报：
 *   "booting" — 模块在执行，还在等后端数据
 *   "ok"      — 首屏已经画出结论（正常页面，或渲染失败的错误面板）
 *   "down"    — 后端不可用，画的是 `renderBackendDown()` 的指引页
 * 而 `window.__wz` **不存在**本身就是答案：模块一行都没跑（加载失败 / 语法错误）。
 *
 * ★ 赋值必须放在模块体最外层：静态 import 的依赖先执行，任何一步抛错
 *   都不会走到这里 —— 那正是我们想识别的"模块没起来"。
 */
window.__wz = { boot: "booting" };

/** 共享上下文。视图不必自己去取词表和健康状态。 */
const ctx = { api, taxonomy: null, health: null };

/** 解析 `#view` 或 `#view/参数`（例：`#item/it_abc123`）。 */
function parseHash() {
  const raw = (location.hash || "").replace(/^#/, "");
  const [id, ...rest] = raw.split("/");
  return { id: id || VIEWS[0].id, arg: rest.join("/") || null };
}

/** 渲染导航。**同一份数据写两处**：顶栏的 `#nav`（桌面）和底部的 `#tabbar`（手机）。
 *
 * 显示哪个**交给 CSS 断点决定** —— 这样 JS 完全不用知道"现在是手机还是桌面"，
 * 也就不会出现"JS 判断的视口"和"CSS 实际生效的断点"不一致的经典坑。
 */
function renderNav(currentId) {
  const html = VIEWS.filter((v) => !v.hidden)
    .map(
      (v) =>
        `<a class="tab${v.id === currentId ? " active" : ""}" href="#${esc(v.id)}"${v.id === currentId ? ' aria-current="page"' : ""}>${icon(v.id)}<span>${esc(v.label)}</span></a>`
    )
    .join("");
  navEl.innerHTML = html;
  if (tabbarEl) tabbarEl.innerHTML = html;
}

function renderStatus() {
  const html = serviceSettingsHtml(ctx.health);
  statusEl.innerHTML = html;
  document.querySelectorAll(".page-settings").forEach(node => node.innerHTML = html);
}

// 状态浮层不接管页面焦点；Escape 和点外部都可以收起。
document.addEventListener("click", (ev) => {
  document.querySelectorAll(".status-menu[open]").forEach(menu => { if (!menu.contains(ev.target)) menu.open = false; });
  const button = ev.target.closest("[data-desktop-settings]");
  if (!button) return;
  button.closest("details").open = false;
  openBridgeSettings(ctx, () => { renderStatus(); refreshImportService(); refreshItemSheet(ctx); }, button.closest(".status-menu")?.querySelector("summary"));
});
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  document.querySelectorAll(".status-menu[open]").forEach(menu => {
    ev.preventDefault();
    menu.open = false;
    const summary = menu.querySelector("summary");
    if (summary.getClientRects().length) summary.focus();
  });
});

async function render(turn = null) {
  const sequence = ++renderSequence;
  await closePurchaseSheet();
  await closeTryonSheet();
  if (sequence !== renderSequence) return;
  cancelReveal();
  const { id, arg } = parseHash();
  const requested = VIEWS.find((v) => v.id === id) || VIEWS[0];
  if (requested.id === "item") {
    // 从必选搭配返回详情时重建原背景，用数据快照恢复，避免复活已卸载的异步页面。
    const origin = history.state?.itemOrigin?.id === arg ? history.state.itemOrigin : null;
    if (origin && rootEl.dataset.routeHash !== origin.returnHash) {
      const [backgroundId, ...parts] = origin.returnHash.replace(/^#/, "").split("/");
      const view = VIEWS.find(v => v.id === backgroundId && !v.hidden) || VIEWS[0];
      const background = document.createElement("main"); background.id = "view"; background.className = "view";
      background.dataset.view = view.id; background.dataset.routeHash = origin.returnHash; background.setAttribute("aria-label", view.label);
      gaps.unmount(rootEl); wardrobe.unmount(rootEl); outfits.unmount(rootEl);
      rootEl.replaceWith(background); rootEl = background;
      await view.mod.mount(background, ctx, parts.join("/"), origin.background);
      if (sequence !== renderSequence) return;
      window.scrollTo(0, origin.scrollY || 0);
    }
    if (!["wardrobe", "outfits", "gaps"].includes(rootEl.dataset.view)) {
      const background = document.createElement("main"); background.id = "view"; background.className = "view"; background.dataset.view = "wardrobe"; background.dataset.routeHash = "#wardrobe"; background.setAttribute("aria-label", "衣橱");
      rootEl.replaceWith(background); rootEl = background; await wardrobe.mount(background, ctx);
      if (sequence !== renderSequence) return;
      history.replaceState(null, "", "#wardrobe"); history.pushState({ wardrobeItem: true }, "", "#item/" + arg);
    }
    renderNav(rootEl.dataset.view); document.title = "衣物详情 · AI 穿搭助手";
    await openItemSheet(ctx, arg, rootEl);
    if (sequence === renderSequence && window.__wz) window.__wz.boot = "ok";
    return;
  }
  if (requested.id === "add" && rootEl.dataset.view === "wardrobe") {
    renderNav("wardrobe"); document.title = "录入 · AI 穿搭助手";
    await openImportSheet(ctx);
    if (window.__wz) window.__wz.boot = "ok";
    return;
  }
  const view = requested.id === "add" ? VIEWS[0] : requested;
  // 新页先接入DOM，旧真实节点断开；迟到mount只能写各自的旧根节点。
  const next = document.createElement("main");
  next.id = "view"; next.className = "view"; next.dataset.view = view.id;
  next.dataset.routeHash = "#" + view.id + (arg ? "/" + arg : "");
  next.setAttribute("aria-label", view.label);
  gaps.unmount(rootEl);
  wardrobe.unmount(rootEl);
  outfits.unmount(rootEl);
  rootEl.replaceWith(next); rootEl = next;
  // 隐藏页（详情）不在导航里，高亮它所属的那个 Tab，免得看起来"哪个都没选中"
  renderNav(view.parent || view.id);
  document.title = `${view.label} · AI 穿搭助手`;
  window.scrollTo(0, 0);
  try {
    const mounted = view.mod.mount(next, ctx, arg);
    if (turn && sequence === renderSequence) revealPage(turn.snapshot, turn.point, next, () => {
      if (sequence !== renderSequence) return;
      const queued = queuedTab; queuedTab = null;
      if (queued && canReveal(rootEl, tabbarEl)) navigateTab(queued);
    });
    await mounted;
    if (requested.id === "add" && sequence === renderSequence) await openImportSheet(ctx);
  } catch (err) {
    if (sequence === renderSequence && next.isConnected) {
      next.innerHTML = `<div class="alert error">页面加载失败：${esc(err && err.message)}</div>`;
    }
  } finally {
    // 不管是正常渲染还是渲染失败，`#view` 里都已经有结论了 ——
    // 告诉看门狗"别再动这页"（成功和错误面板都算结论）。
    if (sequence === renderSequence && window.__wz) window.__wz.boot = "ok";
  }
}

/** 点过「重试」之后置 true —— 切回前台时自动再试一次。
 *
 * 为什么用 `visibilitychange` 而不是定时轮询：页面切到后台后，定时器会被浏览器
 * 节流到分钟级（甚至暂停），"每 2 秒探一次"根本不成立；而"切回前台"这个时刻
 * 恰好就是服务可能已经恢复、该重连的时刻，信号比任何轮询都准。
 */
let waitingForBackend = false;

/** 后端没起来时的引导页。
 *
 * 为什么需要它（这是「按需启动」模式的必备件）：
 * 首页是**后端自己发出来的** —— 后端没在跑时，浏览器连 HTML 都拿不到，
 * 只会显示它自己那张"无法访问此网站"的白页。用户看到那个只会觉得**坏了**，
 * 而实际只是"后端还没启动"。
 * 现在：Service Worker 会拿缓存里的外壳兜底（见 sw.js），页面能正常渲染出来，
 * 于是这里就能给一句**照着做就行**的话，而不是让浏览器去吓人。
 *
 * ★ 2026-10-22 删掉了「试着打开 Termux」按钮（原来那套 `intent:` 唤起）。
 *   Termux 版部署当天已弃用，服务改成 APK 自己的前台服务；而这个按钮在 APK 里
 *   点下去仍会真的唤起 com.termux（手机上还装着），它的 `.bashrc` 钩子随即去
 *   bind 8787 —— 跟 APK 里的 uvicorn **抢同一个端口**，症状比"没反应"更难查。
 *
 *   文案也一并改了：原来说"把应用从最近任务里划掉再重新打开"，这是**误导**。
 *   前台服务没有 `stopWithTask`，划掉只销毁页面、服务照跑；重开时服务已在运行，
 *   只走 `onStartCommand`（START_STICKY），**不会重跑 `bootPython`**。
 *   只有系统的「强制停止」才会杀掉进程。
 */
function renderBackendDown() {
  clearTurns(); ++renderSequence;
  // 先定状态再画 DOM：index.html 的看门狗以这个字段为准，晚一步就可能被它插手
  if (window.__wz) window.__wz.boot = "down";
  const onServerDevice = ["127.0.0.1", "localhost", "::1"].includes(location.hostname);
  // WebView 的 UA 必带 `; wv)`（壳没有 setUserAgentString，UA 是唯一
  // 不依赖壳改动的稳定信号）。电脑 Chrome 的 UA 里没有 `wv`，不会误命中。
  const inWebView = /;\s*wv\)/.test(navigator.userAgent);
  document.title = "后端未启动 · AI 穿搭助手";
  rootEl.innerHTML = `
    <div class="view-head"><h1>${inWebView ? "内置服务没响应" : "后端还没启动"}</h1></div>
    <div class="alert">${
      inWebView
        ? "应用内置的服务没有响应 —— 这会儿读不到数据。"
        : `这个地址上没有服务在响应：<code>${esc(location.origin)}</code>`
    }</div>
    <div class="panel">
      <h2>${
        inWebView
          ? "先点重试，再强制停止"
          : onServerDevice
            ? "先把服务启动起来"
            : "去跑后端的那台设备上启动它"
      }</h2>
      <p class="muted">${
        inWebView
          ? "服务跟着这个应用走，不用连电脑。先点下面的「重试」重新加载页面；" +
            "还是不行，就去<strong>系统设置 → 应用 → 找到本应用 → 强制停止</strong>，" +
            "再打开一次 —— 只有「强制停止」会重启内置服务。" +
            "（从最近任务里划掉<strong>不算</strong>，那只关掉页面，服务还在原地。）"
          : onServerDevice
            ? "在项目目录执行 <code>.venv/Scripts/python.exe run.py</code>。"
            : "在跑后端的那台电脑上执行 <code>.venv/Scripts/python.exe run.py</code>。"
      }</p>
      <p class="faint">这页能显示出来，是 Service Worker 缓存的外壳 —— 数据仍然拿不到。</p>
      <div class="form-actions">
        <button id="boot-retry" class="primary">重试</button>
      </div>
    </div>`;

  const btn = rootEl.querySelector("#boot-retry");
  if (btn) {
    btn.addEventListener("click", () => {
      btn.disabled = true;
      btn.textContent = "正在连接…";
      // 万一这次连不上，切回前台时会自动再试一次（见上面的 visibilitychange）
      waitingForBackend = true;
      boot();
    });
  }
}

// 从 Termux 切回本页 → 服务此时通常已经起来了，自动重连一次
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && waitingForBackend) boot();
});

async function boot() {
  let healthOk = false;
  try {
    ctx.health = await api.health();
    healthOk = true;
    waitingForBackend = false; // 连上了就别再自动重试，免得每次切回前台都打一次 /health
    // 页面确实起来了 → 清掉"自动刷新过几次"的计数，下次发版还能自动刷一次
    try {
      sessionStorage.removeItem("swReloads");
    } catch {
      /* 同上，拿不到就算了 */
    }
  } catch (err) {
    // 这里 err.message 是「连不上后端服务，确认它还在运行」——比 renderStatus()
    // 那句笼统的"后端未就绪"有用得多，所以下面**不能再调 renderStatus()** 把它盖掉
    //（W4 的 Bug 猎手抓到：具体线索被覆盖成没信息量的一句话，排障时少一条信息）。
    statusEl.innerHTML = `<span class="dot off"></span>${esc(err.message)}`;
  }

  // 后端不可用 → **不要**再让每个视图各自弹一句"加载失败"（那看着就像坏了）。
  // 直接给一页能照做的指引。见 renderBackendDown 的注释。
  if (!healthOk) {
    renderBackendDown();
    return;
  }
  renderStatus();

  try {
    ctx.taxonomy = await api.taxonomy();
  } catch {
    // 词表拿不到不该整页白屏 —— 视图会退化成显示原始 key
    toast("词表加载失败，标签可能显示成英文 key", "error");
  }

  await render();
  restoreBridgeSettings(ctx, () => { renderStatus(); refreshImportService(); refreshItemSheet(ctx); });
}

let routeSequence = 0;
window.addEventListener("hashchange", event => {
  const request = ++routeSequence;
  const turn = pendingTurn;
  pendingTurn = null;
  if (itemSheetOpen()) {
    clearTurns(); const target = location.hash, previous = new URL(event.oldURL).hash, returnHash = itemSheetReturn(), itemState = itemSheetHistory();
    void closeItemSheet().then(allowed => {
      if (request !== routeSequence) return;
      if (!allowed) { history.pushState(itemState, "", previous); return; }
      if (location.hash !== target) return;
      if (target === returnHash && rootEl.dataset.routeHash === target) { renderNav(rootEl.dataset.view); document.title = "AI 衣橱"; }
      else void render();
    });
    return;
  }
  if (importSheetOpen() && location.hash !== "#add") {
    clearTurns();
    // 返回只消费最上层；裁剪原有 hashchange 监听不会再处理同一次事件。
    if (document.querySelector(".crop-layer,.import-modal,.bridge-modal")) {
      event.stopImmediatePropagation();
      history.pushState({ wardrobeImport: true }, "", "#add");
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      return;
    }
    const target = location.hash;
    void closeImportSheet().then(allowed => {
      if (request !== routeSequence) return;
      if (!allowed) { history.pushState({ wardrobeImport: true }, "", "#add"); return; }
      if (location.hash !== target) return;
      if (target === "#wardrobe" && rootEl.dataset.view === "wardrobe") {
        renderNav("wardrobe"); document.title = "衣橱 · AI 穿搭助手";
      } else void render();
    });
    return;
  }
  if (!guardImportNavigation(event)) { clearTurns(); return; }
  if (location.hash === "#add") {
    clearTurns(); history.replaceState({ wardrobeImport: true }, "", "#add");
    void render(); return;
  }
  const matching = turn && new URL(event.oldURL).hash === turn.from &&
    new URL(event.newURL).hash === turn.hash && location.hash === turn.hash;
  if (!matching) queuedTab = null;
  void render(matching ? turn : null);
});

/** 注册 Service Worker（PWA：离线可用 + 可"添加到主屏幕"）。
 *
 * ⚠️ 只在**安全上下文**里能注册：https，或 localhost / 127.0.0.1。
 *    所以在手机上用 `http://127.0.0.1:8787` 打开时能注册，
 *    而用局域网 IP（`http://192.168.x.x:8787`）打开时**注册不了** ——
 *    这是浏览器的规矩，不是我们的 bug。想让手机装成 App 就用 127.0.0.1。
 *    失败不抛错：没有 SW 页面照样能用，只是不能离线。
 */
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js")
      .then((reg) => {
        // ★ 主动检查更新（2026-10-21 加，实测踩到）。
        // MIUI 浏览器里，页面加载时的 register() 只做"注册/复用"，**不会重新拉
        // sw.js** —— 于是改了 sw.js 也要等浏览器自己想起来（最长 24 小时）。
        // 表现就是"代码更新了、手机上还是旧界面"，而且怎么刷新都没用
        // （旧 app.js 里没有新逻辑，它自己不知道自己旧）。
        // update() 是幂等的，多拉一次只有 5 KB；服务就在本机，成本可以忽略。
        reg.update().catch(() => undefined);
      })
      .catch((err) => {
        console.warn("Service Worker 注册失败（不影响使用）:", err && err.message);
      });
  });

  // 新 SW 接管后**自动刷新一次**，让页面立刻用上它缓存的新文件。
  // 用 `swRefreshed` 卡住：`controllerchange` 在某些浏览器会连续触发，
  // 不设防会变成无限刷新。
  let swRefreshed = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (swRefreshed) return;
    swRefreshed = true;
    // ★ 跨 reload 去重（2026-10-21 加，手机实测踩到）：
    //   `swRefreshed` 只活在本页的 JS 上下文里，**reload 之后就重置了**。
    //   如果新 SW 又接管一次（更新期间很常见），就会再刷一次 → 反复刷新，
    //   表现是"点开 PWA 一直闪、始终进不去"，只能重启 App 才停。
    //   sessionStorage 跟着标签页走、刷新不丢，用它在多次加载之间计数：
    //   一次会话里最多自动刷 1 次，之后交给用户手动刷新。
    try {
      const n = Number(sessionStorage.getItem("swReloads") || "0");
      if (n >= 1) return;
      sessionStorage.setItem("swReloads", String(n + 1));
    } catch {
      // 隐私模式 / 某些 WebView 不给 sessionStorage：退回"只防本页循环"
    }
    location.reload();
  });
}

// §22：直达录入也有衣橱历史，Android 第一次返回不会退出应用。
if (location.hash === "#add") {
  history.replaceState(null, "", "#wardrobe");
  history.pushState({ wardrobeImport: true }, "", "#add");
}
boot();
