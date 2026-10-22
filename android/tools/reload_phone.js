/* 让手机页面重载，再读启动状态。可选 --hard（清 HTTP 缓存 + 忽略缓存重载）。
 * 只重载页面，不动任何数据。
 */
const http = require("http");
const PORT = process.argv[2] || "9223";
const HARD = process.argv.includes("--hard");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

http.get("http://127.0.0.1:" + PORT + "/json", (r) => {
  let b = "";
  r.on("data", (c) => (b += c));
  r.on("end", async () => {
    const t = JSON.parse(b).find((x) => x.type === "page");
    const ws = new WebSocket(t.webSocketDebuggerUrl);
    const pending = new Map();
    let id = 0;
    const send = (method, params = {}) =>
      new Promise((res) => {
        const mid = ++id;
        pending.set(mid, res);
        ws.send(JSON.stringify({ id: mid, method, params }));
      });
    await new Promise((res) => ws.addEventListener("open", res));
    ws.addEventListener("message", (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) {
        pending.get(m.id)(m.result);
        pending.delete(m.id);
      }
    });

    await send("Page.enable");
    await send("Runtime.enable");
    await send("Network.enable");
    if (HARD) await send("Network.clearBrowserCache");
    await send("Page.reload", { ignoreCache: HARD });
    await sleep(6000);

    const expr =
      "JSON.stringify({" +
      " state:(window.__wz||{}).boot||'missing'," +
      " sw:!!(navigator.serviceWorker&&navigator.serviceWorker.controller)," +
      " text:((document.getElementById('view')||{}).textContent||'').replace(/\\s+/g,' ').trim().slice(0,40)" +
      "})";
    const r2 = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
    console.log((HARD ? "清缓存+强制重载后: " : "普通重载后: ") + r2.result.value);
    ws.close();
    process.exit(0);
  });
}).on("error", (e) => {
  console.log("失败:", e.message);
  process.exit(1);
});
