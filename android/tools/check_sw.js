/* 查手机上 WebView 到底从哪拿到 app.js（SW 缓存 vs 网络）。
 * 只读：fetch 一次文本 + 列缓存桶名。
 */
const http = require("http");
const PORT = process.argv[2] || "9223";

http.get("http://127.0.0.1:" + PORT + "/json", (r) => {
  let b = "";
  r.on("data", (c) => (b += c));
  r.on("end", () => {
    const t = JSON.parse(b).find((x) => x.type === "page");
    const ws = new WebSocket(t.webSocketDebuggerUrl);
    ws.addEventListener("open", () => {
      const expr = `(async () => {
        const reg = navigator.serviceWorker && navigator.serviceWorker.controller;
        let netHas = null, netLen = 0, netErr = null;
        try {
          const r = await fetch('/js/app.js', { cache: 'no-store' });
          const txt = await r.text();
          netHas = txt.includes('__wz');
          netLen = txt.length;
        } catch (e) { netErr = String(e); }
        const keys = window.caches ? await caches.keys() : [];
        let cached = {};
        for (const k of keys) {
          const c = await caches.open(k);
          const hit = await c.match('/js/app.js');
          if (hit) {
            const txt = await hit.text();
            cached[k] = { has_wz: txt.includes('__wz'), len: txt.length };
          }
        }
        return JSON.stringify({
          page_state: (window.__wz || {}).boot || 'missing',
          sw_controller: reg ? reg.scriptURL : null,
          sw_state: reg ? reg.state : null,
          net: { has_wz: netHas, len: netLen, err: netErr },
          cache_buckets: keys,
          cached_app_js: cached,
        });
      })()`;
      ws.send(
        JSON.stringify({
          id: 1,
          method: "Runtime.evaluate",
          params: { expression: expr, returnByValue: true, awaitPromise: true },
        })
      );
    });
    ws.addEventListener("message", (e) => {
      const m = JSON.parse(e.data);
      if (m.id === 1) {
        console.log(m.result && m.result.result && m.result.result.value);
        setTimeout(() => {
          ws.close();
          process.exit(0);
        }, 300);
      }
    });
  });
}).on("error", (e) => {
  console.log("失败:", e.message);
  process.exit(1);
});
