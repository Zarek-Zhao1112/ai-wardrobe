/** Service Worker —— PWA 外壳（离线可用）
 *
 * 策略（刻意简单，每条都有理由）：
 *
 * ① **页面外壳**（HTML / CSS / JS / manifest）→ **网络优先**，失败回落缓存。
 *    为什么不用 cache-first：这是自托管项目，用户会随时更新代码（我把新版推进手机）。
 *    cache-first 会让手机长期显示旧界面，而且"该不该更新"要靠记得改版本号。
 *    而服务就在本机/局域网，网络优先几乎没有延迟代价。
 *
 * ② **图片（images 目录）** → **也是网络优先**，缓存只当离线兜底。
 *
 *    ⚠️ 这里原本写的是"缓存优先"，理由是"图片是不可变内容（文件名唯一）" ——
 *    **这个前提是错的**，W4 的 Bug 猎手抓出来了：
 *    文件名其实是 `<单品id>.<扩展名>`（`services/images.py` 明确"重新上传就是覆盖"），
 *    所以换一张**同扩展名**的照片时 URL 根本不变 → 缓存永久命中旧图，
 *    用户会以为换成功了、实际一直看着旧照片（而且这种错是**静默**的）。
 *    服务就在本机，网络优先没有延迟代价 → 改成网络优先。
 *
 * ③ **其余请求（API：items / outfits / health …）完全不拦截** —— 直接走网络。
 *    缓存 API 响应会让用户看到过期的衣橱数据，这是最坏的一类 bug。
 *
 * ⚠️ 改前端**不需要**动 VERSION（网络优先会自动刷新缓存）。
 *    只有当缓存策略本身变了、想强制清掉旧缓存时才 +1。
 *
 *    例外（2026-10-21 实测踩到）：如果**当前这个桶里装的已经是旧文件**，
 *    光靠网络优先救不回来 —— 浏览器先用缓存里的 index.html 和 app.js 渲染，
 *    而那份旧 app.js 里没有新逻辑，它自己不知道自己旧，界面上也看不出异常
 *    （手机 PWA 装了 5 天还显示老界面，就是这么来的）。
 *    这时必须 +1 强制重装：sw.js 字节一变，浏览器才会重新 install、重建桶。
 *
 * ⚠️ 2026-10-21 补充（同一个坑的另一面）：
 *    只改 `web/` 下的**其他**文件（app.js / css / html）时，`sw.js` 本身没变 →
 *    浏览器不会重新 install → 预缓存桶里留着的还是旧文件。
 *    服务活着时看不出来（网络优先会拿到新的），但**"服务没起 → 回落缓存"那一刻
 *    显示的就是旧版** —— 恰恰是最需要它正确的时候。
 *    所以发版时如果希望离线缓存也跟着更新，**必须让 sw.js 变一个字节**（哪怕只改日期）。
 *    2026-10-21 这一次就是为此把这行说明加进来。
 *
 * ⚠️ 写本文件的注释时**别让星号紧挨着斜杠**（比如写成 星号斜杠 images 星号），
 *    那会被当成注释结束符 —— 后面的中文立刻变成代码，浏览器只报
 *    「ServiceWorker script evaluation failed」，指不到真正的位置（踩过）。
 */
const VERSION = "v7";   // v6→v7：录入页加「AI 识别」（动了 view-add.js / api.js /
                        //          fields.js / style.css，四个都在 SHELL_ASSETS 里）
                        //（v5→v6：重装外壳；v4→v5：activate 不再盲目删旧桶；
                        //  v3→v4：外壳请求加超时）
const SHELL = `wzbbti-shell-${VERSION}`;
const IMAGES = `wzbbti-img-${VERSION}`;

/** 单次网络请求的超时（毫秒）。**这是本文件最重要的一个数字。**
 *
 * ★ 为什么必须有（2026-10-21 手机实测，用户报「点 PWA 图标打不开，得重启 Termux」）：
 *   Android 会挂起后台的 Termux —— 进程还活着、CPU 也在跑，但新连接迟迟不被处理。
 *   这种状态下 `fetch(request)` **不会立刻失败**，而是一直挂着等浏览器自己的超时
 *   （可达数分钟）。后果链条是：
 *       请求挂住 → 页面外壳（/ 、/js/app.js）拿不到 → 连 HTML 都渲染不出来
 *       → PWA 的原生启动闪屏一直不退 → 用户看到的就是「打不开」。
 *   给了上限之后，超时即**回落缓存**：页面 3 秒内就能渲染出来，再由 app.js 显示
 *   「后端还没启动」的引导页。用户至少有出路，不用再去划掉 Termux。
 */
const NET_TIMEOUT_MS = 3000;

/* 预缓存清单：与 index.html 里引用的资源保持一致（手动维护，别漏）。 */
const SHELL_ASSETS = [
  "/",
  "/manifest.webmanifest",
  "/css/tokens.css",
  "/css/style.css",
  "/js/app.js",
  "/js/api.js",
  "/js/ui.js",
  "/js/fields.js",
  "/js/view-wardrobe.js",
  "/js/view-item.js",
  "/js/view-add.js",
  "/js/view-gaps.js",
  "/js/view-outfits.js",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];

/** 带超时的 `cache.add`。**不能直接用 `cache.add`**：它没有超时参数，
 *  服务不响应时会一直挂着 → `Promise.all` 永远不 resolve → `install` 事件不完成
 *  → 新 SW 永远卡在 installing，`skipWaiting()` 也不会执行。
 *  那样连"更新"这个动作本身都会把 PWA 卡死一次。 */
function addWithTimeout(cache, url) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), NET_TIMEOUT_MS);
  return cache
    .add(new Request(url, { signal: ctrl.signal }))
    .catch(() => undefined)
    .finally(() => clearTimeout(timer));
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      // 逐个 add 而不是 addAll：addAll 里任何一个 404 都会让整批失败，
      // 而这份清单是手工维护的 —— 漏一个文件就整个 SW 装不上，很难查。
      .then((cache) => Promise.all(SHELL_ASSETS.map((url) => addWithTimeout(cache, url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then(async (keys) => {
        // ★★ 删旧桶之前**先确认新桶真的有货**（2026-10-21 修，这是
        //    「每次更新完都得重启 Termux 才能打开」的根因）：
        //
        //    发版流程是"sw.js 字节一变 → 浏览器装新 SW → install 里逐个
        //    cache.add 填充新桶 → activate 删掉所有旧桶"。
        //    可是 install 那一刻后端**可能正好不可用**（部署中在重启，或
        //    Termux 在后台被系统挂起）。此时 15 个 add 全部失败，**新桶是空的**，
        //    而原来的写法照样把旧桶删个干净 —— 缓存里一份外壳都不剩，
        //    页面连 HTML 都拿不到，PWA 的原生闪屏就一直不退。
        //    用户唯一的出路是去把 Termux 重启一次，让后端活过来重新填桶。
        //
        //    现在：新桶里连 "/" 都没有 ⇒ 判定这次安装没成功 ⇒ **旧桶一份不删**，
        //    继续用上一版外壳兜底。宁可多占一点空间，也不能让用户没界面可用。
        const fresh = await caches.open(SHELL);
        if (!(await fresh.match("/"))) return; // 保留所有旧桶
        await Promise.all(
          keys.filter((k) => k !== SHELL && k !== IMAGES).map((k) => caches.delete(k))
        );
      })
      .then(() => self.clients.claim())
  );
});

/** 在当前版本的桶里找；找不到就**逐个翻历史版本的桶**。
 *
 * 为什么需要（同上）：新桶可能是空的（install 时后端不可用），而旧桶里
 * 躺着上个月那版完好的外壳。只有当前桶时，空桶 = 白屏；翻历史桶就能兜住。
 * 顺序按 caches.keys() 返回顺序，先命中的版本优先，够用了。 */
async function matchShellAnywhere(request) {
  const current = await caches.open(SHELL);
  const hit = await current.match(request);
  if (hit) return hit;
  for (const key of await caches.keys()) {
    if (!key.startsWith("wzbbti-shell-") || key === SHELL) continue;
    const old = await caches.open(key);
    const oldHit = await old.match(request);
    if (oldHit) return oldHit;
  }
  return null;
}

/** 网络优先：成功就顺手更新缓存；失败**或超时**回落缓存。 */
async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  // ★ 用 `new Request(request, {signal})` 覆盖 signal，而不是给 fetch 传第二参：
  //   后者在部分 Chromium 版本里不会替换原 request 自带的 signal，超时就掐不断。
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), NET_TIMEOUT_MS);
  try {
    const res = await fetch(new Request(request, { signal: ctrl.signal }));
    if (res && res.ok) cache.put(request, res.clone());
    return res;
  } catch (err) {
    // 回落：外壳请求翻遍所有历史桶（当前桶可能是空的，理由见 matchShellAnywhere）；
    // 图片只认当前图片桶 —— shell 桶里本来就不存图，翻它纯属浪费。
    const hit =
      cacheName === SHELL ? await matchShellAnywhere(request) : await cache.match(request);
    if (hit) return hit;
    // 导航请求最后兜底到首页（hash 路由，所有页面都从它进）
    if (request.mode === "navigate") {
      const shell = await matchShellAnywhere("/");
      if (shell) return shell;
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // 写操作一律走网络

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // 不插手第三方请求

  // 图片：网络优先（理由见文件头 ② —— 文件名**不唯一**，重新上传是覆盖同名文件）
  if (url.pathname.startsWith("/images/")) {
    event.respondWith(networkFirst(req, IMAGES));
    return;
  }

  const isShell =
    req.mode === "navigate" ||
    url.pathname === "/" ||
    url.pathname.startsWith("/css/") ||
    url.pathname.startsWith("/js/") ||
    url.pathname.startsWith("/icons/") ||
    url.pathname === "/manifest.webmanifest";

  if (isShell) event.respondWith(networkFirst(req, SHELL));
  // 其余（API）不拦截 —— 数据必须新鲜
});
