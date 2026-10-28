// §23.1：仅本机展示裁边。完整生成文件不改写，不发模型请求。
const cache = new Map(), active = new Map(), pending = [];
let running = 0, observer = null, intersection = null;
const waiting = new Set();
// 已验证的平台双行角标形状（64×32位图），不含任何衣物/账号数据。
const footerPattern = Uint8Array.from(atob("AAAAAAAAAAAAAACAAQAYAQAAAIwBAJgBgAccCAAAAACAAAQAAPgBBAAIBGL4iAMGQAgEefgIAARAAAQd+AgAAIAUhAMACAABoBAEAQDIMwMQOAQAAAgyA1A4BARoCBADQAAEHHgIEwEIAAQAQAgTAQh/BAAAGBMAIMUEgAAYAwAUgAQAABgSARQgBAEAlAsCDOAVf/idlQIAAAD//x0MAwAAAAAAAAACAAAAAAAAAAAAAAAAAAAAAAAAQBYAAAAARp9Hs8k8nwBEkUQTySSbAQCRRBDJRBMDBJEAEclEEwYEkUARyUQTAySfRDPZPp9xJMRs9nAeB3AAAAAAAAAAAA=="), c => c.charCodeAt(0));
function knownFooter(mask, rw, x0, y0, rect) {
  const observed = new Uint8Array(2048), reference = new Uint8Array(2048);
  for (let y = 0; y < 32; y++) for (let x = 0; x < 64; x++) {
    const index = y * 64 + x;
    const sx = rect.x - x0 + Math.min(rect.width - 1, Math.floor((x + .5) * rect.width / 64));
    const sy = rect.y - y0 + Math.min(rect.height - 1, Math.floor((y + .5) * rect.height / 32));
    observed[index] = mask[sy * rw + sx]; reference[index] = (footerPattern[index >> 3] >> (index & 7)) & 1;
  }
  const near = (bits, x, y) => {
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) if (x + dx >= 0 && x + dx < 64 && y + dy >= 0 && y + dy < 32 && bits[(y + dy) * 64 + x + dx]) return true;
    return false;
  };
  let a = 0, b = 0, alignedA = 0, alignedB = 0;
  for (let y = 0; y < 32; y++) for (let x = 0; x < 64; x++) {
    const index = y * 64 + x;
    if (observed[index]) { a++; if (near(reference, x, y)) alignedA++; }
    if (reference[index]) { b++; if (near(observed, x, y)) alignedB++; }
  }
  return a > b * .45 && a < b * 2 && alignedA / a >= .78 && alignedB / b >= .65;
}
export const layoutMessage = status => ({
  trimmed: "已整理角落留白，衣物大小与位置保持，完整平铺图仍保留。",
  unchanged: "当前图片无需裁边，完整平铺图仍保留。",
  unsafe: "这张图未能安全整理，已保留完整平铺图，请核对后再选用。",
  full: "正在查看完整生成图，衣橱展示会单独整理边缘。",
}[status] || "平铺展示会检查边缘留白，完整生成图始终保留。");

// 保守规则：识别右下角小型亮色文字组件，只整理其周围留白。
export function inspectFooter(pixels, width, height) {
  const pixel = (x, y) => { const p = (y * width + x) * 4; return [pixels[p], pixels[p + 1], pixels[p + 2], pixels[p + 3]]; };
  if (width < 128 || height < 128 || Math.abs(width / height - 1) > .01) return { status: "unsafe" };
  const corner = (x, y) => {
    const values = [];
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) values.push(pixel(Math.max(0, Math.min(width - 1, x + dx)), Math.max(0, Math.min(height - 1, y + dy))));
    if (values.some(p => p[3] < 250)) return null;
    return [0, 1, 2].map(c => values.map(p => p[c]).sort((a, b) => a - b)[12]);
  };
  const neutral = colors => colors.every(c => c && Math.min(...c) >= 210 && Math.max(...c) - Math.min(...c) <= 24);
  const corners = [corner(4, 4), corner(width - 5, 4), corner(4, height - 5), corner(width - 5, height - 5)];
  if (!neutral(corners)) return { status: "unsafe", reason: "corners" };
  const blend = (colors, rx, ry) => [0, 1, 2].map(c => colors[0][c] * (1 - rx) * (1 - ry) + colors[1][c] * rx * (1 - ry) + colors[2][c] * (1 - rx) * ry + colors[3][c] * rx * ry);
  const expected = (x, y) => blend(corners, x / (width - 1), y / (height - 1));
  const x0 = Math.floor(width * .875), y0 = Math.floor(height * .93), rw = width - x0, rh = height - y0;
  const mask = new Uint8Array(rw * rh), seen = new Uint8Array(mask.length);
  const radius = 3, sx = x0 - radius, sy = y0 - radius, sw = rw + radius, sh = rh + radius;
  const integral = new Float64Array((sw + 1) * (sh + 1));
  for (let y = 0; y < sh; y++) {
    let sum = 0;
    for (let x = 0; x < sw; x++) {
      const p = pixel(sx + x, sy + y); sum += (p[0] + p[1] + p[2]) / 3;
      integral[(y + 1) * (sw + 1) + x + 1] = integral[y * (sw + 1) + x + 1] + sum;
    }
  }
  const localMean = (x, y) => {
    const a = Math.max(0, x - sx - radius), b = Math.max(0, y - sy - radius), c = Math.min(sw, x - sx + radius + 1), d = Math.min(sh, y - sy + radius + 1), stride = sw + 1;
    return (integral[d * stride + c] - integral[b * stride + c] - integral[d * stride + a] + integral[b * stride + a]) / ((c - a) * (d - b));
  };
  for (let y = 0; y < rh; y++) for (let x = 0; x < rw; x++) {
    const p = pixel(x0 + x, y0 + y), bg = expected(x0 + x, y0 + y);
    if (p[3] < 250) return { status: "unsafe", reason: "alpha" };
    const difference = (p[0] + p[1] + p[2]) / 3 - localMean(x0 + x, y0 + y);
    if (difference >= 1.8 && Math.max(...p.slice(0, 3)) - Math.min(...p.slice(0, 3)) <= 18) mask[y * rw + x] = 1;
  }
  // 连通衣物常跨出角标候选区；不把这样的亮色主体当文字。
  const components = [];
  for (let index = 0; index < mask.length; index++) if (mask[index] && !seen[index]) {
    const queue = [index]; seen[index] = 1;
    let loX = rw, loY = rh, hiX = 0, hiY = 0;
    for (let head = 0; head < queue.length; head++) {
      const at = queue[head], x = at % rw, y = Math.floor(at / rw);
      loX = Math.min(loX, x); loY = Math.min(loY, y); hiX = Math.max(hiX, x); hiY = Math.max(hiY, y);
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const xx = x + dx, yy = y + dy, next = yy * rw + xx;
        if (xx >= 0 && xx < rw && yy >= 0 && yy < rh && mask[next] && !seen[next]) { seen[next] = 1; queue.push(next); }
      }
    }
    if (queue.length < Math.max(2, width / 85)) continue;
    if (loX <= 1 || loY <= 1 || queue.length > mask.length * .18 || hiX - loX > width * .09 || hiY - loY > height * .045) return { status: "unsafe", reason: "component", bounds: [loX,loY,hiX,hiY], size: queue.length };
    components.push({ loX, loY, hiX, hiY });
  }
  if (components.length < 4) return { status: "unchanged" };
  const margin = Math.max(2, Math.ceil(width / 512));
  const left = Math.max(x0, x0 + Math.min(...components.map(c => c.loX)) - margin);
  const top = Math.max(y0, y0 + Math.min(...components.map(c => c.loY)) - margin);
  const right = Math.min(width, x0 + Math.max(...components.map(c => c.hiX)) + margin + 1);
  const bottom = Math.min(height, y0 + Math.max(...components.map(c => c.hiY)) + margin + 1);
  const rect = { x: left, y: top, width: right - left, height: bottom - top };
  if (!knownFooter(mask, rw, x0, y0, rect)) return { status: "unsafe" };
  const local = [corner(left - 3, top - 3), corner(right + 2, top - 3), corner(left - 3, bottom + 2), corner(right + 2, bottom + 2)];
  if (!neutral(local)) return { status: "unsafe", reason: "local", rect, local };
  const positions = [[left - 3, top - 3], [right + 2, top - 3], [left - 3, bottom + 2], [right + 2, bottom + 2]];
  if (local.some((color, i) => { const bg = expected(...positions[i]); return Math.abs(color.reduce((a, b, c) => a + b - bg[c], 0) / 3) > 8; })) return { status: "unsafe", reason: "background-shift", rect, local };
  // 最终安全检查按原分辨率，细线和透明像素不会被缩小平均掉。
  for (let y = Math.max(0, top - margin); y < bottom; y++) for (let x = Math.max(0, left - margin); x < right; x++) {
    const p = pixel(x, y), bg = blend(local, Math.max(0, (x - left) / Math.max(1, rect.width - 1)), Math.max(0, (y - top) / Math.max(1, rect.height - 1)));
    if (p[3] < 250 || p.some((v, c) => c < 3 && v < bg[c] - 10) || Math.max(...p.slice(0, 3)) - Math.min(...p.slice(0, 3)) > 24) return { status: "unsafe", reason: "detail", point: [x,y], pixel: p, expected: bg };
    if (x < left || y < top) {
      let sum = 0, count = 0;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const q = pixel(Math.max(0, Math.min(width - 1, x + dx)), Math.max(0, Math.min(height - 1, y + dy)));
        sum += (q[0] + q[1] + q[2] - bg[0] - bg[1] - bg[2]) / 3; count++;
      }
      if (Math.abs(sum / count) >= 4.5) return { status: "unsafe", reason: "guard", point: [x,y], difference:sum/count };
    }
  }
  return { status: "trimmed", rect, patchCorners: local };
}

function enqueue(run, wanted) {
  return new Promise(resolve => { pending.push({ run, wanted, resolve }); pump(); });
}
function pump() {
  while (running < 2 && pending.length) {
    const job = pending.shift();
    if (!job.wanted()) { job.resolve({ status: "unsafe", cancelled: true }); continue; }
    running++;
    Promise.resolve().then(job.run).catch(() => ({ status: "unsafe", transient: true })).then(job.resolve).finally(() => { running--; pump(); });
  }
}
function decode(src) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const timeout = setTimeout(() => { image.onload = image.onerror = null; image.src = ""; reject(Error("图片暂不可读")); }, 6000);
    image.onload = () => { clearTimeout(timeout); resolve(image); };
    image.onerror = () => { clearTimeout(timeout); reject(Error("图片暂不可读")); };
    image.src = src;
  });
}
async function prepare(src) {
  const url = new URL(src, location.href);
  if (url.protocol !== "blob:" && (url.origin !== location.origin || !url.pathname.startsWith("/images/"))) return { status: "unsafe" };
  const image = await decode(src), width = image.naturalWidth, height = image.naturalHeight;
  if (width < 128 || height < 128 || width > 2048 || height > 2048 || Math.abs(width / height - 1) > .01) return { status: "unsafe" };
  const sample = document.createElement("canvas"); sample.width = width; sample.height = height;
  const ctx = sample.getContext("2d", { willReadFrequently: true });
  // 不缩小检查：单像素深色边缘/透明细节也不能被平均后误删。
  ctx.drawImage(image, 0, 0);
  const result = inspectFooter(ctx.getImageData(0, 0, width, height).data, width, height);
  if (result.status !== "trimmed") return result;
  const canvas = document.createElement("canvas"); canvas.width = width; canvas.height = height;
  const output = canvas.getContext("2d");
  output.drawImage(image, 0, 0);
  const rect = result.rect;
  const patch = output.createImageData(rect.width, rect.height);
  // 仅角落留白使用周边背景延续，衣物区域的像素、尺寸和位置都不改变。
  for (let y = 0; y < rect.height; y++) for (let x = 0; x < rect.width; x++) {
    const rx = x / Math.max(1, rect.width - 1), ry = y / Math.max(1, rect.height - 1), offset = (y * rect.width + x) * 4;
    for (let c = 0; c < 3; c++) patch.data[offset + c] = Math.round(result.patchCorners[0][c] * (1 - rx) * (1 - ry) + result.patchCorners[1][c] * rx * (1 - ry) + result.patchCorners[2][c] * (1 - rx) * ry + result.patchCorners[3][c] * rx * ry);
    patch.data[offset + 3] = 255;
  }
  output.putImageData(patch, rect.x, rect.y);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
  return blob ? { ...result, blob } : { status: "unsafe" };
}
function prepared(src) {
  if (cache.has(src)) { const found = cache.get(src); cache.delete(src); cache.set(src, found); return found; }
  const wanted = () => [...active.entries()].some(([image, state]) => image.isConnected && state.enabled && state.src === src);
  const promise = enqueue(() => prepare(src), wanted).then(result => {
    if ((result.cancelled || result.transient) && cache.get(src) === promise) cache.delete(src);
    return result;
  }); cache.set(src, promise);
  if (cache.size > 32) cache.delete(cache.keys().next().value);
  return promise;
}
function release(image) {
  const state = active.get(image);
  if (state?.url) URL.revokeObjectURL(state.url);
  active.delete(image);
}
export function setFlatlayImage(image, src, enabled = true) {
  if (enabled) { if (image.dataset.flatlaySource !== src) image.dataset.flatlaySource = src; }
  else delete image.dataset.flatlaySource;
  const previous = active.get(image);
  if (previous?.src === src && previous.enabled === enabled) return previous.promise;
  release(image);
  const state = { src, enabled, url: null, promise: null }; active.set(image, state);
  if (image.getAttribute("src") !== src) image.src = src;
  image.dataset.flatlayStatus = enabled ? "pending" : "full";
  state.promise = enabled ? prepared(src).then(result => {
    if (active.get(image) !== state || !image.isConnected) return result;
    if (result.blob) { state.url = URL.createObjectURL(result.blob); image.src = state.url; }
    image.dataset.flatlayStatus = result.status;
    image.dispatchEvent(new CustomEvent("flatlay-layout", { detail: { status: result.status, source: src } }));
    return result;
  }) : Promise.resolve({ status: "full" });
  return state.promise;
}
export function watchFlatlayImages() {
  if (observer || !document.body) return;
  const scan = node => {
    if (node.nodeType !== 1) return;
    const images = [...node.querySelectorAll("img[data-flatlay-source]")];
    if (node.matches("img[data-flatlay-source]")) images.push(node);
    images.forEach(image => {
      if (intersection && image.loading === "lazy" && !active.has(image)) { waiting.add(image); intersection.observe(image); }
      else void setFlatlayImage(image, image.dataset.flatlaySource, true);
    });
  };
  if (typeof IntersectionObserver === "function") intersection = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      const image = entry.target; intersection.unobserve(image); waiting.delete(image);
      if (image.isConnected && image.dataset.flatlaySource) void setFlatlayImage(image, image.dataset.flatlaySource, true);
    }
  }, { rootMargin: "400px" });
  observer = new MutationObserver(records => {
    for (const record of records) {
      if (record.type === "attributes") scan(record.target);
      else for (const node of record.addedNodes) scan(node);
    }
    for (const image of active.keys()) if (!image.isConnected) release(image);
    for (const image of waiting) if (!image.isConnected || !image.dataset.flatlaySource) { intersection.unobserve(image); waiting.delete(image); }
  });
  observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-flatlay-source"] });
  scan(document.body);
}
