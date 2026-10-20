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
const tabbarEl = document.getElementById("tabbar");
const statusEl = document.getElementById("status");

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
        `<a class="tab${v.id === currentId ? " active" : ""}" href="#${esc(v.id)}">${esc(v.label)}</a>`
    )
    .join("");
  navEl.innerHTML = html;
  if (tabbarEl) tabbarEl.innerHTML = html;
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

/** 用户点了「打开 Termux」之后置 true —— 切回本页时自动重试一次。
 *
 * 为什么用 `visibilitychange` 而不是定时轮询：页面切到后台后，定时器会被浏览器
 * 节流到分钟级（甚至暂停），"每 2 秒探一次"根本不成立；而"切回前台"这个时刻
 * 恰好就是服务已经起来、该重连的时刻，信号比任何轮询都准。
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
 * 2026-10-21 加了两件事（手机实测：从桌面图标点进来只会看到这一页，
 * 用户还得自己退回桌面去找 Termux 图标）：
 *   ① 「打开 Termux 启动服务」按钮 —— 用 `intent:` 直接唤起 Termux App。
 *      Termux 一开，它的 runsvdir 就会把服务拉起来（实测 force-stop 后重开，
 *      15 秒内 8787 恢复响应），所以这一个按钮就等于"点一下就跑"。
 *      只在 Android 上显示：电脑的 hostname 同样是 127.0.0.1，会命中同一个分支，
 *      但 `intent:` 在桌面上毫无意义。
 *   ② 从 Termux 切回来时**自动重试** —— 省掉"再点一次重试"这一下手。
 */
function renderBackendDown() {
  const onServerDevice = ["127.0.0.1", "localhost", "::1"].includes(location.hostname);
  const canWakeTermux = onServerDevice && /Android/i.test(navigator.userAgent);
  document.title = "后端未启动 · AI 穿搭助手";
  rootEl.innerHTML = `
    <div class="view-head"><h1>后端还没启动</h1></div>
    <div class="alert">这个地址上没有服务在响应：<code>${esc(location.origin)}</code></div>
    <div class="panel">
      <h2>${canWakeTermux ? "点一下，服务自己起来" : onServerDevice ? "先把服务启动起来" : "去跑后端的那台设备上启动它"}</h2>
      <p class="muted">${
        canWakeTermux
          ? "下面这个按钮会打开 <strong>Termux</strong>，服务在几秒内自动起来（不用敲命令）。" +
            "切回本页时会自动重连，不用再点「重试」。"
          : onServerDevice
            ? "在项目目录执行 <code>.venv/Scripts/python.exe run.py</code>。"
            : "在跑后端的那台电脑上执行 <code>.venv/Scripts/python.exe run.py</code>。"
      }</p>
      <p class="faint">这页能显示出来，是 Service Worker 缓存的外壳 —— 数据仍然拿不到。</p>
      <div class="form-actions">
        ${canWakeTermux ? `<button id="boot-termux" class="primary">打开 Termux 启动服务</button>` : ""}
        <button id="boot-retry"${canWakeTermux ? "" : ' class="primary"'}>重试</button>
      </div>
    </div>`;

  const termuxBtn = rootEl.querySelector("#boot-termux");
  if (termuxBtn) {
    termuxBtn.addEventListener("click", () => {
      // `intent:` 是 Chromium 的 App 唤起协议。Termux 的启动入口是
      // `.app.TermuxActivity`（实测 `adb shell cmd package resolve-activity
      // --brief com.termux` 返回它；`.HomeActivity` 是同一个页面的别名，也认）。
      waitingForBackend = true;
      termuxBtn.disabled = true;
      termuxBtn.textContent = "等待服务启动…";
      try {
        location.href = "intent:#Intent;component=com.termux/.app.TermuxActivity;end";
      } catch (err) {
        // 浏览器不支持 intent: 时不会跳转，把按钮恢复成可点，别让它卡在"等待中"
        console.warn("唤起 Termux 失败（请手动打开 Termux）:", err && err.message);
        termuxBtn.disabled = false;
        termuxBtn.textContent = "打开 Termux 启动服务";
        return;
      }
      // 同样的兜底：`location.href = "intent:..."` 在不认识的浏览器里**不抛错也不跳转**，
      // 所以只能靠"页面还在不在"来判断 —— 2.5 秒后按钮还挂在 DOM 上，就说明没跳走。
      setTimeout(() => {
        if (termuxBtn.isConnected) {
          termuxBtn.disabled = false;
          termuxBtn.textContent = "打开 Termux 启动服务";
        }
      }, 2500);
    });
  }

  const btn = rootEl.querySelector("#boot-retry");
  if (btn) {
    btn.addEventListener("click", () => {
      btn.disabled = true;
      btn.textContent = "正在连接…";
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
}

window.addEventListener("hashchange", render);

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
    location.reload();
  });
}

boot();
