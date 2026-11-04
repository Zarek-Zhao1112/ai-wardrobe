/** §43：把展示图裁到衣物的紧边界，再放进 3:4 画布。
 *
 * ## 为什么必须做
 * 模型返回的展示图一律是 1024×1024 方图，衣物只占画面中间一小块。
 * 实测真机 26 件：宽占比中位 0.635、高占比中位 0.705 —— 也就是说**方图里
 * 四边加起来有 55% 是白底**。方图直接 contain 进格子时，格子的宽度大部分
 * 交给了这圈白边：3 件主副结构主列 193px，衣服只剩 78px，还不如改动前的 58px
 * 好看多少。裁到紧边界、再按最长边归一化，衣服能放大到 1.5 倍以上，而且
 * 三件之间的视觉大小也统一了（原来大衣显得比背心小，就是因为大衣在图里占比低）。
 *
 * ## 为什么在前端做
 * APK 是 Chaquopy 打包，`requirements.txt` 里没有 Pillow，Chaquopy 也不提供
 * Pillow 的 Android wheel —— 后端一旦 `import PIL` 装上手机就崩。
 *
 * ## 为什么不用 CSS 近似
 * `object-fit: cover` / `transform: scale()` 都只能按固定比例裁边。实测 26 件里
 * 有 5 件宽占比 ≥0.75，固定比例会把衣物的边切掉。只有真扫出 bbox 才不切。
 *
 * ## 代价
 * 每件 ~150ms（真机实测 3 件 460ms），所以只处理当前这张卡，结果缓存成 blob URL。
 */

const cache = new Map();      // 原图 URL → blob URL
const inflight = new Map();   // 原图 URL → Promise<blob URL|null>
const MAX_CACHE = 120;        // 120 套 × 最多 6 件 = 720 张，正常翻不到；超了才淘汰最早的

const THRESH = 244;   // 三通道最小值低于它才算"有内容"，纯白底是 255
const PROBE = 256;    // 扫 bbox 用的缩略图边长，够用且快
const PAD = 0.05;     // 裁完四周留 5%，别贴着边
const OUT_W = 720;    // 输出 3:4
const OUT_H = 960;

/** 在缩略图上扫出非白像素的紧边界；全白或全非白都返回 null（交给调用方回退）。 */
function bboxOf(bitmap) {
  const probe = document.createElement("canvas");
  probe.width = PROBE;
  probe.height = PROBE;
  const ctx = probe.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(bitmap, 0, 0, PROBE, PROBE);
  let data;
  try {
    data = ctx.getImageData(0, 0, PROBE, PROBE).data;
  } catch {
    return null;              // 画布被跨域图片污染（同源下不会发生，兜底）
  }
  let x0 = PROBE, y0 = PROBE, x1 = -1, y1 = -1;
  for (let y = 0; y < PROBE; y++) {
    const row = y * PROBE * 4;
    for (let x = 0; x < PROBE; x++) {
      const i = row + x * 4;
      if (data[i] < THRESH || data[i + 1] < THRESH || data[i + 2] < THRESH) {
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
    }
  }
  if (x1 < x0 || y1 < y0) return null;
  // 整张都是内容 = 没有白底可裁（用户自拍/背景复杂的图），不折腾
  if ((x1 - x0 + 1) / PROBE > 0.97 && (y1 - y0 + 1) / PROBE > 0.97) return null;
  return { x0, y0, x1, y1 };
}

/** 裁紧 + 按最长边归一化缩进 3:4 画布，返回 blob URL；任何一步失败返回 null。 */
async function build(url) {
  const response = await fetch(url, { cache: "force-cache" });
  if (!response.ok) return null;
  const bitmap = await createImageBitmap(await response.blob());
  const box = bboxOf(bitmap);
  if (!box) { bitmap.close?.(); return null; }

  const k = bitmap.width / PROBE;
  const padX = (box.x1 - box.x0 + 1) * PAD;
  const padY = (box.y1 - box.y0 + 1) * PAD;
  const sx = Math.max(0, (box.x0 - padX) * k);
  const sy = Math.max(0, (box.y0 - padY) * k);
  const sw = Math.min(bitmap.width - sx, (box.x1 - box.x0 + 1 + padX * 2) * k);
  const sh = Math.min(bitmap.height - sy, (box.y1 - box.y0 + 1 + padY * 2) * k);
  if (sw < 8 || sh < 8) { bitmap.close?.(); return null; }

  const canvas = document.createElement("canvas");
  canvas.width = OUT_W;
  canvas.height = OUT_H;
  const ctx = canvas.getContext("2d");
  // 底色必须和卡片同色（--surface #fff），否则又是一块新的白方块
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, OUT_W, OUT_H);
  // 最长边贴合：竖长的裤子、横宽的眼镜都按自己的比例撑满，视觉大小才统一
  const scale = Math.min(OUT_W / sw, OUT_H / sh);
  const dw = sw * scale;
  const dh = sh * scale;
  ctx.drawImage(bitmap, sx, sy, sw, sh, (OUT_W - dw) / 2, (OUT_H - dh) / 2, dw, dh);
  bitmap.close?.();

  const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.9));
  if (!blob) return null;
  return URL.createObjectURL(blob);
}

function remember(url, blobUrl) {
  if (cache.size >= MAX_CACHE) {
    const [oldUrl, oldBlob] = cache.entries().next().value || [];
    if (oldBlob) URL.revokeObjectURL(oldBlob);
    if (oldUrl) cache.delete(oldUrl);
  }
  cache.set(url, blobUrl);
}

/**
 * 把容器里带 `data-tighten` 的图片换成裁紧版。
 * 幂等：已处理／处理中的图会跳过，重复调用安全。
 */
export function tighten(root) {
  if (!root) return;
  root.querySelectorAll("img[data-tighten]").forEach(img => {
    const state = img.dataset.tighten;
    if (state === "done" || state === "pending" || state === "failed") return;
    const original = img.currentSrc || img.src;
    if (!original || original.startsWith("blob:")) { img.dataset.tighten = "failed"; return; }

    const ready = cache.get(original);
    if (ready) {
      img.src = ready;
      img.dataset.tighten = "done";
      return;
    }

    img.dataset.tighten = "pending";
    let job = inflight.get(original);
    if (!job) {
      job = build(original).finally(() => inflight.delete(original));
      inflight.set(original, job);
    }
    job.then(blobUrl => {
      if (!blobUrl) { img.dataset.tighten = "failed"; return; }
      remember(original, blobUrl);
      // 图可能已经换过（换了一套 / 卡片被回收），换 src 前确认还在文档里
      if (img.isConnected && (img.getAttribute("src") === original || img.dataset.tighten === "pending")) {
        img.src = blobUrl;
        img.dataset.tighten = "done";
      }
    }).catch(() => { img.dataset.tighten = "failed"; });
  });
}
