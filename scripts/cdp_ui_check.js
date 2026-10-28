/* 前端 UI 门禁：多视口截图 + 断言（契约见 docs/ui-contract.md）
 *
 * 它是**只读**的：只导航、量尺寸、截图，不点任何会写数据的按钮。
 *
 * 前置（三样都要）：
 *   1) Node ≥ 18（用到全局 WebSocket）
 *   2) 本机 Chrome 以调试端口常驻：
 *        chrome --headless=new --remote-debugging-port=9333 \
 *               --user-data-dir=<全新目录> --disable-gpu --hide-scrollbars about:blank
 *      ★ profile 目录必须每次新建，否则会踩 ES module 缓存（改了代码却看不到变化）
 *   3) 应用在跑：APP_PORT=8799 python run.py
 *
 * 用法：
 *   node scripts/cdp_ui_check.js
 *   BASE=http://127.0.0.1:8787/ node scripts/cdp_ui_check.js
 *
 * 产出：stdout 一份 JSON 报告（人读/机读都行）+ <OUT>/*.png 截图
 *
 * **判据**（失败时对照契约的 Definition of Done）：
 *   - overflow_x 必须全 false
 *   - tap.small_count 必须为 0（手机端所有可点元素 ≥ 44×44）
 *   - console_errors / exceptions 必须为空
 *   - sw.active 必须 true（只有安全上下文能注册，见契约 §5）
 *
 * ⚠️ 断言里描述「哪些算可点元素」要用**语义选择器**，别把类名一个个列进来 ——
 *    枚举永远会漏（录入页那个长在 `.chip` 里的勾选框就漏过一次）。
 */
const http = require("http");
const fs = require("fs");
const os = require("os");
const path = require("path");

const CDP_PORT = Number(process.env.CDP_PORT || 9333);
const BASE = process.env.BASE || "http://127.0.0.1:8799/";
const OUT = process.env.OUT || path.join(os.tmpdir(), "cdp-ui-check");

const VIEWPORTS = [
  { name: "mobile-390", width: 390, height: 844, mobile: true },
  { name: "tablet-768", width: 768, height: 1024, mobile: true },
  { name: "desktop-1440", width: 1440, height: 900, mobile: false },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function req(method, path) {
  return new Promise((resolve, reject) => {
    const r = http.request({ host: "127.0.0.1", port: CDP_PORT, path, method }, (res) => {
      let d = "";
      res.on("data", (c) => (d += c));
      res.on("end", () => {
        try { resolve(JSON.parse(d)); } catch { resolve(d); }
      });
    });
    r.on("error", reject);
    r.end();
  });
}

class Client {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.handlers = new Map();
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      } else if (msg.method && this.handlers.has(msg.method)) {
        for (const fn of this.handlers.get(msg.method)) fn(msg.params);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  on(method, fn) {
    if (!this.handlers.has(method)) this.handlers.set(method, new Set());
    this.handlers.get(method).add(fn);
  }
}

async function evalJson(cli, expression) {
  const r = await cli.send("Runtime.evaluate", {
    expression, returnByValue: true, awaitPromise: true,
  });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return r.result.value;
}

/* ── 断言表达式 ───────────────────────────────────────────── */

const LAYOUT = `(() => {
  const cs = getComputedStyle(document.documentElement);
  const tb  = document.querySelector('.tabbar');
  const nav = document.querySelector('.nav');
  const brand = document.querySelector('.brand');
  const tab = document.querySelector('.tabbar .tab');
  const R = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    return [Math.round(r.width), Math.round(r.height)]; };
  const de = document.documentElement;
  return {
    token_accent: cs.getPropertyValue('--accent').trim(),
    token_radius: cs.getPropertyValue('--radius').trim(),
    body_bg: getComputedStyle(document.body).backgroundColor,
    tabbar_display: tb ? getComputedStyle(tb).display : null,
    nav_display: nav ? getComputedStyle(nav).display : null,
    tabbar_count: document.querySelectorAll('.tabbar .tab').length,
    tabbar_tab_size: R(tab),
    brand_height: brand ? Math.round(brand.getBoundingClientRect().height) : null,
    brand_lines: brand ? Math.round(brand.getBoundingClientRect().height /
        parseFloat(getComputedStyle(brand).lineHeight || '24')) : null,
    viewport: [de.clientWidth, de.clientHeight],
    scroll_width: de.scrollWidth,
    overflow_x: de.scrollWidth > de.clientWidth,
    nav_active: (document.querySelector('.tabbar .tab.active, .nav .tab.active') || {}).textContent || null,
    title: document.title,
  };
})()`;

const OVERFLOW_ONLY = `(() => {
  const de = document.documentElement;
  const wide = [...document.querySelectorAll('body *')]
    .filter(el => el.getBoundingClientRect().right > de.clientWidth + 1)
    .slice(0, 5)
    .map(el => el.tagName + '.' + (el.className || '').toString().slice(0, 30));
  return { hash: location.hash, overflow_x: de.scrollWidth > de.clientWidth,
           scroll_width: de.scrollWidth, viewport_w: de.clientWidth, offenders: wide,
           main_text: (document.getElementById('view')||{}).innerText ?
                      (document.getElementById('view').innerText.slice(0, 60)) : null };
})()`;

/* 触控目标：契约 §4 要求手机端可点元素 ≥ 44px。
 * 只查**真控件**（按钮/下拉/勾选框的 label/可交互标签）；
 * 纯文字链接不在范围内（HIG 也不要求），避免制造噪音。 */
const TAP_TARGETS = `(() => {
  const sel = 'button, select, summary, a.btn, a.tab, a.brand, .chip-toggle > span,' +
              // ⚠️ file input 要排除 sr-only 那一份（2026-10-22 起它被视觉隐藏，
              //    真入口是旁边的按钮，button 本来就在选择器里）。不排除的话，
              //    它那个 1x1 的盒子会被量成"触控目标太小"，是**假红**。
              //    ⚠️ 这段在模板字符串里，注释里**不能写反引号**（会截断字符串）。
              'input[type=file]:not(.sr-only), input[type=text], input[type=search]';
  // 旧WebView不支持:has；按控件找label，仍然覆盖未预知类名的复选标签。
  const labels = [...document.querySelectorAll('input[type=checkbox], input[type=radio]')]
    .map(el => el.closest('label')).filter(Boolean);
  const els = [...new Set([...document.querySelectorAll(sel), ...labels])].filter(el =>
    el.offsetParent !== null && (!el.checkVisibility || el.checkVisibility()));
  const small = els
    .map(el => { const r = el.getBoundingClientRect();
      return { tag: el.tagName, cls: (el.className || '').toString().slice(0, 22),
               w: Math.round(r.width), h: Math.round(r.height),
               text: (el.textContent || '').trim().slice(0, 10) }; })
    .filter(o => o.w > 0 && o.h > 0 && (o.w < 44 || o.h < 44));
  return { checked: els.length, small_count: small.length, small: small.slice(0, 12) };
})()`;

/* 手机端：滚到底时，最后一段内容不能被固定底部栏盖住。
 * ⚠️ 这条是 W4 补的 —— 之前门禁只量"横向溢出"，而"内容被底部栏遮住"是**纵向**的，
 *    结构上量不到。当时 `body { padding-bottom: … }` 静默失效（放错文件被覆盖），
 *    门禁全绿却真的有内容看不到。 */
const TABBAR_CLEARANCE = `(() => {
  // §22：录入浮层覆盖底栏，检查自己的滚动区最后一项能完整到达。
  const sheet = document.querySelector('.wardrobe-import-layer.is-open .wardrobe-import-scroll');
  if (sheet) {
    const before = sheet.scrollTop; sheet.scrollTop = sheet.scrollHeight;
    const kids = [...sheet.querySelector('.wardrobe-import-content').children].filter(el => el.getBoundingClientRect().height > 0);
    const lastBottom = kids[kids.length - 1]?.getBoundingClientRect().bottom || 0;
    const edge = sheet.getBoundingClientRect().bottom;
    sheet.scrollTop = before;
    return { sheet: true, last_child_bottom: Math.round(lastBottom), hidden_px: Math.round(Math.max(0, lastBottom - edge)), ok: lastBottom <= edge + 1 };
  }
  const de = document.documentElement;
  const tb = document.querySelector('.tabbar');
  if (!tb || getComputedStyle(tb).display === 'none') return { skipped: '非手机端（无底部栏）' };
  window.scrollTo(0, de.scrollHeight);
  const container = document.getElementById('view') ||
                    document.querySelector('.bind-wrap') || document.body;
  const kids = [...container.children].filter((el) => el.offsetParent !== null);
  const last = kids[kids.length - 1];
  const tbTop = tb.getBoundingClientRect().top;
  const lastBottom = last ? last.getBoundingClientRect().bottom : 0;
  window.scrollTo(0, 0);
  return {
    tabbar_top: Math.round(tbTop),
    last_child_bottom: Math.round(lastBottom),
    hidden_px: Math.round(Math.max(0, lastBottom - tbTop)),
    ok: lastBottom <= tbTop + 1,
  };
})()`;

const SW_STATE = `(async () => {
  const out = {};
  if (!('serviceWorker' in navigator)) return { supported: false };
  out.supported = true;
  out.secure_context = window.isSecureContext;
  out.url = location.href;
  try {
    const regs = await navigator.serviceWorker.getRegistrations();
    out.registrations = regs.map(r => ({ scope: r.scope, active: !!r.active,
      installing: !!r.installing, waiting: !!r.waiting }));
  } catch (e) { out.getErr = String(e); }
  try {
    const r = await navigator.serviceWorker.register('/sw.js');
    out.direct_register = { ok: true, scope: r.scope };
    await new Promise(res => setTimeout(res, 1000));
    out.after_register = { active: !!r.active, installing: !!r.installing,
      waiting: !!r.waiting, controller: !!navigator.serviceWorker.controller };
  } catch (e) { out.direct_register = { ok: false, err: String(e) }; }
  return out;
})()`;

/* ── 主流程 ───────────────────────────────────────────────── */

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const target = await req("PUT", "/json/new?" + encodeURIComponent("about:blank"));
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener("open", res);
    ws.addEventListener("error", () => rej(new Error("WebSocket 连不上 CDP")));
  });
  const cli = new Client(ws);
  await cli.send("Page.enable");
  await cli.send("Runtime.enable");

  const report = { layout: {}, mobile_subpages: {}, sw: null, console_errors: [], exceptions: [] };

  cli.on("Runtime.consoleAPICalled", (p) => {
    if (p.type === "error" || p.type === "warning") {
      report.console_errors.push(p.type + ": " + ((p.args[0] || {}).value || "?"));
    }
  });
  cli.on("Runtime.exceptionThrown", (p) => {
    report.exceptions.push(JSON.stringify(p.exceptionDetails).slice(0, 300));
  });

  for (const vp of VIEWPORTS) {
    await cli.send("Emulation.setDeviceMetricsOverride", {
      width: vp.width, height: vp.height,
      deviceScaleFactor: vp.mobile ? 2 : 1, mobile: vp.mobile,
    });
    await cli.send("Page.navigate", { url: BASE });
    await sleep(1800);
    report.layout[vp.name] = await evalJson(cli, LAYOUT);

    if (vp.name === "mobile-390") {
      for (const hash of ["gaps", "add", "outfits"]) {
        await cli.send("Page.navigate", { url: BASE + "#" + hash });
        await sleep(1200);
        const ov = await evalJson(cli, OVERFLOW_ONLY);
        ov.tap = await evalJson(cli, TAP_TARGETS);
        ov.tabbar_clearance = await evalJson(cli, TABBAR_CLEARANCE);
        report.mobile_subpages[hash] = ov;
        const s = await cli.send("Page.captureScreenshot", { format: "png" });
        fs.writeFileSync(`${OUT}/mobile-390-${hash}.png`, Buffer.from(s.data, "base64"));
      }
      // 契约视图清单第 6 项：微信绑定页（独立 HTML，路由管不到）
      await cli.send("Page.navigate", { url: BASE + "bind.html" });
      await sleep(1200);
      const ovb = await evalJson(cli, OVERFLOW_ONLY);
      ovb.tap = await evalJson(cli, TAP_TARGETS);
      ovb.tabbar_clearance = await evalJson(cli, TABBAR_CLEARANCE);
      report.mobile_subpages["bind.html"] = ovb;
      const sb = await cli.send("Page.captureScreenshot", { format: "png" });
      fs.writeFileSync(`${OUT}/mobile-390-bind.png`, Buffer.from(sb.data, "base64"));

      await cli.send("Page.navigate", { url: BASE + "#wardrobe" });
      await sleep(1200);
    }

    const shot = await cli.send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(`${OUT}/${vp.name}.png`, Buffer.from(shot.data, "base64"));
  }

  // ---- 启动看门狗：假阳性检查（2026-10-22 加）----
  // 为什么单独做：上面每个页面只等 1.2–1.8 秒，而看门狗 9 秒才动 ——
  // 它**永远跑不到**，等于零覆盖。而这里是用户唯一会看见的兜底面：
  // 只要它误报一次，用户就会认定"后端坏了"（2026-10-22 真发生过：
  // /health 秒回、打标 1.5 秒出结果，页面却写着"后端没响应"）。
  // 所以单独导航一次，等满 16 秒（跨过 9s 首检 + 8s 宽限 + 4s 探测），
  // 断言页面上**没有**任何兜底文案。后端此时是活的，出现即误报。
  await cli.send("Emulation.setDeviceMetricsOverride", {
    width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
  });
  await cli.send("Page.navigate", { url: BASE });
  await sleep(16000);
  report.watchdog = await evalJson(
    cli,
    `(() => {
      const t = ((document.getElementById('view') || {}).textContent || '').replace(/\\s+/g, ' ');
      const hit = ['后端没响应', '页面还没加载完', '页面脚本没加载起来'].filter((s) => t.includes(s));
      return { state: (window.__wz || {}).boot || 'missing', false_alarm: hit, text: t.trim().slice(0, 60) };
    })()`
  );
  if (report.watchdog.false_alarm.length) {
    report.console_errors.push(
      "watchdog false alarm: " + report.watchdog.false_alarm.join(",") + " | " + report.watchdog.text
    );
  }

  // SW 只能在一个标签里查（同一个 origin 共享注册）
  report.sw = await evalJson(cli, SW_STATE);

  // 顺手验证 manifest 能拿到且是合法 JSON（URL 从 BASE 推，别写死端口）
  try {
    const m = await new Promise((res, rej) => {
      http.get(new URL("/manifest.webmanifest", BASE).href, (r) => {
        let d = ""; r.on("data", (c) => (d += c));
        r.on("end", () => res({ status: r.statusCode, ct: r.headers["content-type"], body: d }));
      }).on("error", rej);
    });
    const j = JSON.parse(m.body);
    report.manifest = {
      status: m.status, content_type: m.ct, name: j.name,
      display: j.display, icons: j.icons.length, start_url: j.start_url,
    };
  } catch (e) {
    report.manifest = { error: String(e) };
  }

  console.log(JSON.stringify(report, null, 2));
  ws.close();
  process.exit(0);
})().catch((e) => {
  console.error("FAILED:", e && e.message);
  process.exit(1);
});
