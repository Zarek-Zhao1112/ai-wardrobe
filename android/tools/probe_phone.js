/* 连手机 WebView，读启动状态并主动发一条 console.warn，
 * 用来验证壳新加的 onConsoleMessage 是否真把 JS 日志转进了 logcat。
 * 只读 + 一条日志，不改任何数据。
 */
const http = require("http");
const PORT = process.argv[2] || "9223";

http.get("http://127.0.0.1:" + PORT + "/json", (r) => {
  let b = "";
  r.on("data", (c) => (b += c));
  r.on("end", () => {
    const t = JSON.parse(b).find((x) => x.type === "page");
    if (!t) {
      console.log("没有 page target");
      process.exit(1);
    }
    const ws = new WebSocket(t.webSocketDebuggerUrl);
    ws.addEventListener("open", () => {
      const expr =
        "console.warn('wz-probe-from-cdp');" +
        "JSON.stringify({state:(window.__wz||{}).boot||'missing'," +
        " items:document.querySelectorAll('.card,.item-card').length," +
        " text:(document.getElementById('view')||{}).textContent.trim().slice(0,40)})";
      ws.send(
        JSON.stringify({
          id: 1,
          method: "Runtime.evaluate",
          params: { expression: expr, returnByValue: true },
        })
      );
    });
    ws.addEventListener("message", (e) => {
      const m = JSON.parse(e.data);
      if (m.id === 1) {
        console.log("页面返回:", m.result && m.result.result && m.result.result.value);
        setTimeout(() => {
          ws.close();
          process.exit(0);
        }, 800);
      }
    });
    ws.addEventListener("error", (e) => {
      console.log("ws 失败:", e.message || e);
      process.exit(1);
    });
  });
}).on("error", (e) => {
  console.log("取 target 失败:", e.message);
  process.exit(1);
});
