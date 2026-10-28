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
// Android打包文件时间固定；更新说明同时改变字节数，避免同长度ETag把新脚本误判为未变化。
const VERSION = "v47"; // §33：只读衣物详情、红色喜欢、玻璃确认删除与本次必选搭配。
                        // v21：移除独立大图预览，保留裁剪与随表单滚动的操作。
                        // v20：图片大图预览与随表单滚动的操作按钮。
                        // v19：旧WebView兼容的JSON草稿复制。
                        // v18：多选照片、逐件确认入库及本机草稿。
                        // v17：C 款上衣 Logo 定稿。
                        // v16：双图识别 + 裁剪双指缩放。
                        // v15：A 版清冷极简 + 悬浮玻璃导航。
                        // v13→v14：**所有** fetch 加 `cache: "no-store"`（networkFirst
                        //          与 install 的预缓存都加了）。v13 及之前会吃浏览器的
                        //          HTTP 磁盘缓存 —— 覆盖安装后手机上可能长期跑旧 js，
                        //          普通刷新救不回来（2026-10-22 实测：清缓存前页面里
                        //          `window.__wz` 不存在，而 `fetch('/js/core/app.js')` 返回新版）。
                        //          缓存策略变了 → 必须 +1，否则新逻辑不会被激活。
                        // v12→v13：照片入口收口成原生 `<input type=file>`
                        //          （动了 view-add.js / view-item.js / style.css 的 .sr-only）
                        //          → 让离线桶跟着换，否则"服务没起"那一刻看到的
                        //          还是两个入口的旧界面 —— 正是最需要它正确的时候。
                        // v10→v11：裁剪窗改成「只有一个画面」——图片不动，长按才把窗口
                        //          开到整个显示区、框外以 58% 黑露出来（动了 crop.js /
                        //          style.css；改 sw.js 一个字节是为了让离线缓存也跟着更新）
                        //（v9→v10 是"选区铺满 / 按下变整图"双态 + 去掉放大镜；
                        //  v8→v9：推近动画 + 框内放大预览；v7→v8：新增 /js/features/import/crop.js；
                        //  v6→v7：录入页加「AI 识别」；v5→v6：重装外壳；
                        //  v4→v5：activate 不再盲目删旧桶；v3→v4：外壳请求加超时）
const IMAGES = `wzbbti-img-${VERSION}`;

/** 单次网络请求的超时（毫秒）。**这是本文件最重要的一个数字。**
 *
 * ★ 为什么必须有（2026-10-21 手机实测，用户报「点 PWA 图标打不开，得重启 Termux」）：
 *   Android 会挂起后台的 Termux —— 进程还活着、CPU 也在跑，但新连接迟迟不被处理。
 *   这种状态下 `fetch(request)` **不会立刻失败**，而是一直挂着等浏览器自己的超时
 *   （可达数分钟）。后果链条是：
 *       请求挂住 → 页面外壳（/ 、/js/core/app.js）拿不到 → 连 HTML 都渲染不出来
 *       → PWA 的原生启动闪屏一直不退 → 用户看到的就是「打不开」。
 *   给了上限之后，超时即**回落缓存**：页面 3 秒内就能渲染出来，再由 app.js 显示
 *   「后端还没启动」的引导页。用户至少有出路，不用再去划掉 Termux。
 */
const NET_TIMEOUT_MS = 3000;

// 清单由 scripts/checks/web_assets.py 生成，激活检查复用同一清单。
// BEGIN GENERATED ASSETS
const ASSET_REVISION = "dfe35ad1d97c9547";
const SHELL_ASSETS = [
  "/",
  "/bind.html",
  "/css/base/headerless.css",
  "/css/base/style.css",
  "/css/base/tokens.css",
  "/css/features/import.css",
  "/css/features/outfits.css",
  "/css/features/purchase-reference.css",
  "/css/features/purchase.css",
  "/css/features/wardrobe.css",
  "/icons/apple-touch-icon.png",
  "/icons/favicon-32.png",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/icon-maskable-512.png",
  "/icons/logo.svg",
  "/js/components/flatlay-display.js",
  "/js/components/horizontal-swipe.js",
  "/js/components/page-transition.js",
  "/js/core/api.js",
  "/js/core/app.js",
  "/js/core/desktop-settings.js",
  "/js/core/fields.js",
  "/js/core/ui.js",
  "/js/features/import/assistant.js",
  "/js/features/import/clipboard.js",
  "/js/features/import/crop.js",
  "/js/features/import/flatlay.js",
  "/js/features/import/markup.js",
  "/js/features/import/photo-import.js",
  "/js/features/import/queue.js",
  "/js/features/import/sheet.js",
  "/js/features/import/store.js",
  "/js/features/import/text-input.js",
  "/js/features/import/view.js",
  "/js/features/outfits/camera.js",
  "/js/features/outfits/tryon-sheet.js",
  "/js/features/outfits/view.js",
  "/js/features/purchase/reference-media.js",
  "/js/features/purchase/reference-store.js",
  "/js/features/purchase/reference.js",
  "/js/features/purchase/sheet.js",
  "/js/features/purchase/view.js",
  "/js/features/wardrobe/detail.js",
  "/js/features/wardrobe/sheet.js",
  "/js/features/wardrobe/view.js",
  "/manifest.webmanifest"
];
// END GENERATED ASSETS
// 同一VERSION下的不同资源构建也使用独立桶，避免新HTML与旧模块混合。
const SHELL = `wzbbti-shell-${VERSION}-${ASSET_REVISION}`;
const COMPLETE = "/__wardrobe_shell_complete__";

/** 带超时的 `cache.add`。**不能直接用 `cache.add`**：它没有超时参数，
 *  服务不响应时会一直挂着 → `Promise.all` 永远不 resolve → `install` 事件不完成
 *  → 新 SW 永远卡在 installing，`skipWaiting()` 也不会执行。
 *  那样连"更新"这个动作本身都会把 PWA 卡死一次。 */
function addWithTimeout(cache, url) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), NET_TIMEOUT_MS);
  return cache
    // `cache: "no-store"` 同上：install 时要的是**真实的服务端文件**。
    // 少了它会命中 HTTP 缓存，于是"新桶里装的是旧文件"，升 VERSION 也白升。
    .add(new Request(url, { signal: ctrl.signal, cache: "no-store" }))
    .catch(() => undefined)
    .finally(() => clearTimeout(timer));
}

self.addEventListener("install", (event) => {
  let existed = true;
  event.waitUntil(
    caches
      .has(SHELL)
      .then(present => { existed = present; return caches.open(SHELL); })
      // 逐个 add 而不是 addAll：addAll 里任何一个 404 都会让整批失败，
      // 清单由资源检查工具生成 —— 漏一个文件就整个 SW 装不上，很难查。
      .then(async (cache) => {
        await Promise.all(SHELL_ASSETS.map((url) => addWithTimeout(cache, url)));
        const ready = await Promise.all(SHELL_ASSETS.map(url => cache.match(url)));
        if (ready.some(response => !response)) {
          // 目录迁移时旧缓存无法补齐新模块路径，缺资源就拒绝安装，沿用旧worker。
          if (!existed) await caches.delete(SHELL);
          throw new Error("离线资源未齐，保留当前版本");
        }
        await cache.put(COMPLETE, new Response(ASSET_REVISION));
      })
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
        // ⚠️ 只看 "/" 不够（2026-10-21 审查发现）：`addWithTimeout` 把每个资源的
        //    失败都吞成 `undefined`，所以"首页存到了、某个 js 超时了"这种情况，
        //    install 照样算成功。而模块是**静态 import** 的，`view-add.js` /
        //    `view-item.js` 缺一个依赖 → 整条模块图加载失败 → 那个页面直接白屏
        //    （比"少个按钮"严重）。所以关键模块必须逐个确认，缺一个就当这次
        //    安装没成功、保留旧桶。
        //    ⚠️ 名单要跟着**静态 import 图**走：
        //      · album.js 2026-10-22 起已无人 import —— 留在里面只会把"一个没人用的
        //        文件超时"误判成"这次安装没成功"，白留旧桶；
        //      · crop.js / fields.js / ui.js 是 view-add / view-item 的静态依赖，
        //        以前漏在名单外，它们超时反而会被当成安装成功。
        const critical = SHELL_ASSETS;
        const got = await Promise.all(critical.map((u) => fresh.match(u)));
        if (got.some((r) => !r)) return; // 保留所有旧桶
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
    const candidate = await caches.open(key);
    if (!/^wzbbti-shell-v\d+$/.test(key) && !await candidate.match(COMPLETE)) continue;
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
    // ★★ `cache: "no-store"` 是 2026-10-22 加的，这条**不是优化，是正确性**。
    //    没有它，`fetch()` 会走浏览器的 HTTP 磁盘缓存，于是：
    //      改完前端 → 覆盖安装 APK → 手机上跑的还是**旧的 js**，
    //      普通刷新、甚至 SW 的 network-first 都拿的缓存副本。
    //    实测（小米 12S Ultra / Android 15）：清缓存前页面里 `window.__wz` 不存在
    //    （旧 app.js），同一时刻 `fetch('/js/core/app.js')` 却返回新版（走的另一条路）；
    //    `Network.clearBrowserCache` + 强制重载后立刻变正常。
    //    也就是说"改了没生效"这类幽灵问题的最后一层就是这个。
    //    代价：每次请求都真发出去（本地回环，几十 KB，可忽略；这也让 304 失去意义）。
    const res = await fetch(
      new Request(request, { signal: ctrl.signal, cache: "no-store" })
    );
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
    url.pathname.startsWith("/reference/") ||
    url.pathname === "/manifest.webmanifest";

  if (isShell) event.respondWith(networkFirst(req, SHELL));
  // 其余（API）不拦截 —— 数据必须新鲜
});

// v46：只读衣物详情、红色喜欢、确认删除与必选衣物搭配。
