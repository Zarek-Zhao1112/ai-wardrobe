/* 验证 index.html 启动看门狗（只读，不点任何按钮）。
 *
 * 五条场景，前四条测"该不该报"，最后一条测**最要命的那个假阳性**：
 *
 *   A 正常               → 不许出现任何兜底面板
 *   B app.js 被拦（模块没跑） → 「页面脚本没加载起来」（后端是通的）
 *   C app.js + /health 都断   → 「后端没响应」
 *   D 列表请求挂住（脚本在跑、只是慢）→ 「页面还没加载完」
 *   E 正常页面里出现"加载中…"字样 → **不许被覆盖**（P1-1 回归：
 *     衣橱页点「加载更多」时按钮文字就是它，旧判据会把整页盖掉）
 *
 * 依赖：Chrome 调试端口 9333 + 应用跑在 8799。
 */
const http = require("http");

const CDP = "http://127.0.0.1:9333";
const APP = "http://127.0.0.1:8799/";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function req(method, url) {
  return new Promise((res, rej) => {
    const r = http.request(url, { method }, (resp) => {
      let b = "";
      resp.on("data", (c) => (b += c));
      resp.on("end", () => res(b));
    });
    r.on("error", rej);
    r.end();
  });
}

async function openTarget() {
  return JSON.parse(await req("PUT", CDP + "/json/new?about:blank"));
}

function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  const pending = new Map();
  let id = 0;
  const send = (method, params = {}) =>
    new Promise((res, rej) => {
      const mid = ++id;
      pending.set(mid, { res, rej });
      ws.send(JSON.stringify({ id: mid, method, params }));
    });
  const ready = new Promise((res, rej) => {
    ws.addEventListener("open", res);
    ws.addEventListener("error", () => rej(new Error("ws 失败")));
  });
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
    }
  });
  return { ws, send, ready };
}

const VIEW_TEXT = "document.getElementById('view')?.textContent?.replace(/\\s+/g,' ').trim().slice(0,100)";

function classify(text) {
  if (text.includes("后端没响应")) return "后端没响应";
  if (text.includes("页面脚本没加载起来")) return "页面脚本没加载起来";
  if (text.includes("页面还没加载完")) return "页面还没加载完";
  if (text.includes("加载中")) return "仍在加载中";
  return "正常内容";
}

async function runCase(name, expect, opts = {}) {
  const { blocked = [], holdPatterns = null, waitMs = 11000, injectLoading = false } = opts;
  const t = await openTarget();
  const { ws, send, ready } = connect(t.webSocketDebuggerUrl);
  await ready;
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Network.enable");
  // 绕过 SW：否则 index.html 与 js 都从缓存来，拦 URL 拦不到，测的就不是真路径
  await send("Network.setBypassServiceWorker", { bypass: true });
  await send("Network.setBlockedURLs", { urls: blocked });
  // ★ holdPatterns 与 blocked 的区别很关键：
  //   blocked 是**立刻失败**（页面 catch 一下就照常渲染，测不到"慢"）；
  //   Fetch 暂停后**不回包**才是真的挂住 —— 这才是"后端活着但数据很慢"的形态。
  if (holdPatterns) {
    await send("Fetch.enable", {
      patterns: holdPatterns.map((p) => ({ urlPattern: p })),
    });
    // 故意不监听 Fetch.requestPaused：让请求一直悬着
  }
  await send("Page.navigate", { url: APP });

  if (injectLoading) {
    // 等首屏画完（state 已经是 ok），再往正常页面里塞一句"加载中…"，
    // 完全复刻衣橱页点「加载更多」时的 DOM 形态。
    await sleep(4000);
    await send("Runtime.evaluate", {
      expression: `(() => {
        const v = document.getElementById('view');
        const s = document.createElement('span');
        s.textContent = '加载中…';
        v.appendChild(s);
        return window.__wz && window.__wz.boot;
      })()`,
      returnByValue: true,
    });
    await sleep(waitMs);
  } else {
    await sleep(waitMs);
  }

  const r = await send("Runtime.evaluate", { expression: VIEW_TEXT, returnByValue: true });
  const text = r.result.value || "";
  const got = classify(text);
  const pass = got === expect;
  console.log(`\n[${pass ? "PASS" : "FAIL"}] ${name}`);
  console.log(`  期望=${expect}  实际=${got}`);
  console.log("  首屏文本:", JSON.stringify(text));
  ws.close();
  await req("GET", CDP + "/json/close/" + t.id).catch(() => {});
  return pass;
}

(async () => {
  const results = [];
  results.push(await runCase("A 正常 → 不许出现兜底面板", "正常内容", {}));
  results.push(
    await runCase("B app.js 被拦（模块没跑）→ 说脚本没起来", "页面脚本没加载起来", {
      blocked: ["*/js/core/app.js"],
    })
  );
  results.push(
    await runCase("C app.js + /health 都断 → 才说后端没响应", "后端没响应", {
      blocked: ["*/js/core/app.js", "*/health*"],
    })
  );
  results.push(
    await runCase("D 列表请求挂住（脚本在跑、只是慢）→ 说还没加载完", "页面还没加载完", {
      holdPatterns: ["*/items*"],
      waitMs: 19000, // 9s 首检 + 8s 宽限 + 4s 探测
    })
  );
  results.push(
    await runCase('E 正常页面里出现"加载中…"→ 不许被覆盖（P1-1）', "正常内容", {
      injectLoading: true,
      waitMs: 11000,
    })
  );

  const ok = results.every(Boolean);
  console.log("\n=== 汇总 ===");
  console.log(`通过 ${results.filter(Boolean).length}/${results.length}`);
  process.exit(ok ? 0 : 1);
})().catch((e) => {
  console.log("FAILED:", e.message);
  process.exit(1);
});
