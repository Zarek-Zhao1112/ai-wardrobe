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
const VERSION = "v3";   // v2→v3：清掉 2026-10-15 那次安装留下的旧外壳桶
                        //（v1→v2 是图片「缓存优先」改「网络优先」，同理靠 +1 清桶）
const SHELL = `wzbbti-shell-${VERSION}`;
const IMAGES = `wzbbti-img-${VERSION}`;

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

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      .then((cache) =>
        // 逐个 add 而不是 addAll：addAll 里任何一个 404 都会让整批失败，
        // 而这份清单是手工维护的 —— 漏一个文件就整个 SW 装不上，很难查。
        Promise.all(SHELL_ASSETS.map((url) => cache.add(url).catch(() => undefined)))
      )
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => k !== SHELL && k !== IMAGES).map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

/** 网络优先：成功就顺手更新缓存；失败（离线）回落缓存。 */
async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const res = await fetch(request);
    if (res && res.ok) cache.put(request, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(request);
    if (hit) return hit;
    // 导航请求最后兜底到首页（hash 路由，所有页面都从它进）
    if (request.mode === "navigate") {
      const shell = await cache.match("/");
      if (shell) return shell;
    }
    throw err;
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
