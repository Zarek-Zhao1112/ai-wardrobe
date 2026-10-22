/* 临时诊断：连手机 WebView 的 CDP，抓 JS 异常与 SW 缓存状态。
 * 只读：不点任何按钮、不改 DOM。用完可删。
 */
const http = require("http");

function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, (r) => {
      let b = "";
      r.on("data", (c) => (b += c));
      r.on("end", () => {
        try { res(JSON.parse(b)); } catch (e) { rej(e); }
      });
    }).on("error", rej);
  });
}

(async () => {
  const targets = await getJson("http://127.0.0.1:9222/json");
  const t = targets.find((x) => x.type === "page");
  if (!t) { console.log("没有可用的 page target"); return; }
  console.log("target url:", t.url);

  const ws = new WebSocket(t.webSocketDebuggerUrl);
  const events = [];
  let id = 0;
  const pending = new Map();

  const send = (method, params = {}) => new Promise((res, rej) => {
    const mid = ++id;
    pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

  await new Promise((res, rej) => {
    ws.addEventListener("open", res);
    ws.addEventListener("error", () => rej(new Error("ws 连不上")));
  });

  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
      return;
    }
    if (m.method === "Runtime.exceptionThrown") {
      const d = m.params.exceptionDetails;
      events.push({
        kind: "exception",
        text: d.text,
        desc: d.exception && d.exception.description,
        url: d.url,
        line: d.lineNumber,
      });
    }
    if (m.method === "Runtime.consoleAPICalled") {
      events.push({
        kind: "console." + m.params.type,
        text: (m.params.args || []).map((a) => a.value ?? a.description ?? "").join(" "),
      });
    }
    if (m.method === "Log.entryAdded") {
      const e = m.params.entry;
      events.push({ kind: "log." + e.level, text: e.text, url: e.url, line: e.lineNumber });
    }
    if (m.method === "Network.loadingFailed") {
      events.push({ kind: "netfail", text: m.params.errorText + " " + (m.params.type || "") });
    }
  });

  await send("Runtime.enable");
  await send("Log.enable");
  await send("Page.enable");
  await send("Network.enable");

  const evalJs = async (expr, awaitPromise = false) => {
    const r = await send("Runtime.evaluate", {
      expression: expr, returnByValue: true, awaitPromise,
    });
    if (r.exceptionDetails) return { __err: r.exceptionDetails.text + " " + (r.exceptionDetails.exception || {}).description };
    return r.result.value;
  };

  console.log("\n=== 当前页面状态（重载前）===");
  console.log("view 文本:", JSON.stringify(await evalJs("document.getElementById('view')?.textContent?.trim().slice(0,80)")));
  console.log("已加载模块数:", await evalJs("performance.getEntriesByType('resource').filter(r=>r.name.endsWith('.js')).length"));
  console.log("模块清单:", await evalJs("performance.getEntriesByType('resource').filter(r=>r.name.endsWith('.js')).map(r=>r.name.split('/js/')[1]+':'+Math.round(r.transferSize/1024)+'k')"));

  console.log("\n=== SW 状态 ===");
  console.log(JSON.stringify(await evalJs(
    "navigator.serviceWorker.getRegistrations().then(rs=>rs.map(r=>({scope:r.scope,active:r.active&&r.active.state,installing:r.installing&&r.installing.state})))",
    true), null, 1));
  console.log("缓存桶:", JSON.stringify(await evalJs(
    "caches.keys().then(ks=>ks)", true)));
  console.log("缓存里的 view-add.js 是否含 tabindex:", await evalJs(
    "caches.open((await caches.keys())[0]).then(c=>c.match('/js/view-add.js')).then(r=>r?r.text():'').then(t=>t.includes('tabindex'))", true));

  console.log("\n=== 重载并收集 8 秒事件 ===");
  events.length = 0;
  await send("Page.reload", { ignoreCache: true });
  await new Promise((r) => setTimeout(r, 8000));

  console.log("view 文本（重载后）:", JSON.stringify(await evalJs("document.getElementById('view')?.textContent?.trim().slice(0,80)")));
  console.log("事件数:", events.length);
  for (const e of events.slice(0, 40)) {
    console.log(` - [${e.kind}] ${(e.desc || e.text || "").toString().replace(/\n/g, " ⏎ ").slice(0, 260)}`);
    if (e.url) console.log(`     at ${e.url}:${e.line}`);
  }

  ws.close();
})().catch((e) => {
  console.log("FAILED:", e.message);
  process.exit(1);
});
