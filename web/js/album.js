/** 手机相册选择浮层（**共享**：录入页与详情页都用这一个窗口）。
 *
 * 为什么要有这条通道（2026-10-21 实测）：桌面图标打开的是小米浏览器的 PWA 壳
 * （`PWAActivity`），它没实现 WebView 的 `onShowFileChooser` —— `<input type=file>`
 * 点下去**毫无反应**：不弹选择器、不报错、`change` 都不来。同一个地址在同一台
 * 手机的浏览器里却是好的，所以不是页面代码的错。
 *
 * 浏览器沙箱不给页面读本地文件，纯前端绕不过去；但服务本身就跑在这台手机上
 * （Termux），于是让后端去读相册目录，页面只负责挑一张 + 回传 id
 * （`GET /items/album/photos` → `POST /items/{id}/image-from-album`）。
 *
 * ★ 为什么抽成模块而不是各页各写一份：
 *   「挑一张」这个窗口两页要的是同一个东西（录入页是"挑给新单品"，
 *   详情页是"换掉现有的那张"）。复制一份的话，相册读不到时的提示文案、
 *   目录列表、关闭方式迟早会走岔 —— 而它们描述的其实是同一个后端接口。
 *
 * ⚠️ 相册图必须**同源**（`/items/album/photo`）：裁剪要把它画进 canvas，
 *    跨源图会污染画布，`toBlob` 直接抛 `SecurityError`。
 */

import { api } from "./api.js";
import { esc } from "./ui.js";

/** 探测结果的模块级缓存（一次会话只探一次）。
 *
 * ★ 为什么需要（2026-10-21 审查发现）：后端列相册是**全量 walk + stat**
 *   （上限 3000 个文件，`items.py` 的 `_iter_album_files`），Android 的 FUSE 上
 *   可能到秒级。每进一次详情页就探一次，等于每次都要排队扫一遍相册 ——
 *   而这个结果在一次会话里根本不会变。
 *
 * ⚠️ `false` 也缓存：相册目录不会在会话中间突然长出来，而"每次进来都卡几秒
 *   再决定要不要显示按钮"比"偶尔得重开一次页面"难受得多。
 */
let probe = null;

/** 相册里到底有没有照片可挑。
 *
 * 电脑上没有 `/sdcard` → 后端返回 `available: false` → 入口不该露出来
 * （露出来就是个点了没用的按钮）。接口失败也按"没有"处理：这条通道
 * 本来就是兜底，兜不住就不显示，不影响 `<input type=file>` 那条正路。
 */
export function albumAvailable() {
  return (probe ??= _probe());
}

async function _probe() {
  try {
    const res = await api.albumPhotos(1);
    return !!(res && res.available);
  } catch {
    return false;
  }
}

/** 当前开着的那个浮层（Promise）。
 *
 * ★ 连点两下会叠出**两层**浮层（移动端很容易），两层各自 resolve：
 *   后选中的把先选中的覆盖掉，用户看到的是"选了半天没反应"。
 *   所以这里做单例 —— 已经开着就把同一个 Promise 交回去。
 */
let current = null;

/**
 * 打开相册浮层，等用户挑一张。
 *
 * @param title 头部文案。录入页「选一张照片」，详情页「换一张照片」。
 * @returns 选中时 `{id, name}`；关闭 / 点空白 / Esc / 路由切走 → `null`
 */
export function openAlbumPicker({ title = "选一张照片" } = {}) {
  if (current) return current;

  const promise = new Promise((resolve) => {
    const layer = document.createElement("div");
    layer.className = "album-layer";
    layer.innerHTML = `
      <div class="album-sheet">
        <div class="album-head">
          <strong>${esc(title)}</strong>
          <button type="button" class="ghost" data-close>关闭</button>
        </div>
        <div class="album-grid"><p class="faint">正在读相册…</p></div>
      </div>
    `;
    document.body.appendChild(layer);

    /** 关窗只允许发生一次：点格子、点关闭、点空白、按 Esc、切路由可能连着来
     *  （点格子之后浮层被移除，同一帧里的 click 还会冒到 layer 上）。
     *  不设这道闸的话 `resolve` 第二次是空操作，但 `layer.remove()` 之后
     *  再去读 `layer.querySelector` 就会抛 —— 那会变成没人接的异常。 */
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("hashchange", onHash);
      layer.remove();
      resolve(value);
    };

    function onKey(ev) {
      if (ev.key === "Escape") finish(null);
    }

    /** 路由切走（Android 返回键 / 点导航）→ 关窗。
     *
     * ★ 浮层挂在 `body` 上，不受 `#view` 的 `innerHTML` 替换影响 —— 不关的话
     *   它会盖在新页面上（`z-index` 比底部导航高，导航都点不到），用户只能
     *   找「关闭」。`crop.js` 的 `onHash` 就是为这个加的，这里照抄一份。
     */
    function onHash() {
      finish(null);
    }

    document.addEventListener("keydown", onKey);
    window.addEventListener("hashchange", onHash);

    // 点浮层的空白处关闭（`.album-sheet` 里的点击不算）
    layer.addEventListener("click", (ev) => {
      if (ev.target === layer) finish(null);
    });
    layer.querySelector("[data-close]").addEventListener("click", () => finish(null));

    const grid = layer.querySelector(".album-grid");
    grid.addEventListener("click", (ev) => {
      if (done) return;
      // 点在图上的话，`closest` 会一路找到外层那个 `.album-cell` 按钮
      const cell = ev.target.closest(".album-cell");
      if (!cell) return;
      finish({ id: cell.dataset.id, name: cell.dataset.name });
    });

    api
      .albumPhotos(36)
      .then((res) => {
        // 用户可能在请求回来之前就把窗口关了。这时再写 innerHTML 是无害的
        // （节点已脱离文档），但没必要 —— 直接退出更清楚。
        if (done) return;
        const photos = (res && res.photos) || [];
        if (!photos.length) {
          // `dirs` 一起返回就是为了能说清"为什么是空的"：
          //   Termux 没拿到存储权限 vs 相册本来就没照片 vs 全是 HEIC
          //   —— 这是三种完全不同的处置。
          const dirs = (res && res.dirs) || [];
          grid.innerHTML = `<p class="faint">没读到照片。${
            dirs.length
              ? `找到的目录：${esc(dirs.join("、"))}（只认 jpg / png / webp / gif，HEIC 要先导出成 JPG）`
              : "相册目录都没找到 —— Termux 可能没拿到存储权限"
          }</p>`;
          return;
        }
        // 用原图当缩略图（服务端不装 Pillow），靠 lazy 只加载滚到的那几张
        grid.innerHTML = photos
          .map(
            (p) => `<button type="button" class="album-cell"
              data-id="${esc(p.id)}" data-name="${esc(p.name)}">
              <img src="${esc(api.albumPhotoUrl(p.id))}" loading="lazy" decoding="async" alt="">
            </button>`
          )
          .join("");
      })
      .catch((err) => {
        if (done) return;
        grid.innerHTML = `<p class="faint">读相册失败：${esc(err.message)}</p>`;
      });
  });

  current = promise;
  // 关掉之后解锁，下一次还能再开。用 `then` 而不是 `finally`：这个 Promise
  // 从不 reject，但写成 `finally` 会多出一个没人接的派生 Promise。
  promise.then(() => {
    current = null;
  });
  return promise;
}
