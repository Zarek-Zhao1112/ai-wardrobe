// 录入剪贴板与拖放只绑定一次；目标由页面生命周期提供。
export function isImageFile(f) {
  return !!f && typeof f.type === "string" && f.type.indexOf("image/") === 0;
}

export function bindClipboardOnce(getActive) {
  if (bindClipboardOnce.bound) return;
  bindClipboardOnce.bound = true;
  window.addEventListener("hashchange", () => {
    if (getActive() && !getActive().fileEl.isConnected) getActive().dispose();
  });

  // ⚠️ 判据用 `fileEl.isConnected`，不是 `root.isConnected` ——
  //   外壳容器是持久的，切走别的页面后它照样连接着；
  //   但输入框是视图内部的节点，会随 innerHTML 替换掉。
  //
  // ⚠️ 第二条判据（裁剪窗开着）是同一天补的：窗口里的框是按**打开那一刻**
  //   那张图算的比例，此时再粘一张新图会写进 `fileEl`，等用户点「用这张」，
  //   `acceptImage(crop)` 又把新图**覆盖成旧图的裁剪结果** —— 新图静默丢失。
  const live = () =>
    getActive() &&
    getActive().fileEl &&
    getActive().fileEl.isConnected &&
    !getActive().root.closest("[hidden]") && getActive().root.dataset.sheetActive !== "false" && !getActive().saving() &&
    !document.body.classList.contains("crop-open");
  const hasFiles = (dt) => !!dt && Array.prototype.includes.call(dt.types || [], "Files");

  // 粘贴：**只有剪贴板里真有图片才拦**。
  // 否则用户往「商品文案」里粘文字会被吃掉 —— 这是最容易写错的地方。
  document.addEventListener("paste", (ev) => {
    if (!live()) return;
    const bag = ev.clipboardData && ev.clipboardData.files;
    if (!bag || !bag.length) return;
    const img = Array.prototype.find.call(bag, isImageFile);
    if (!img) return;
    ev.preventDefault();
    getActive().accept(img);
  });

  // 拖拽：`dragover` 不 preventDefault 的话，浏览器会把图片当网页**直接打开**，
  // 当前页面就没了。
  let depth = 0;
  document.addEventListener("dragover", (ev) => {
    if (!live()) return;
    if (!hasFiles(ev.dataTransfer)) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "copy";
  });
  // dragenter/dragleave 会因为进入子元素反复触发 → 用计数决定高亮开关
  document.addEventListener("dragenter", (ev) => {
    if (!live()) return;
    if (!hasFiles(ev.dataTransfer)) return;
    depth += 1;
    getActive().root.classList.add("drop-active");
  });
  document.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (depth === 0 && getActive()) getActive().root.classList.remove("drop-active");
  });
  document.addEventListener("drop", (ev) => {
    if (!live()) return;
    depth = 0;
    getActive().root.classList.remove("drop-active");
    const dt = ev.dataTransfer;
    if (!dt || !dt.files || !dt.files.length) return;
    const img = Array.prototype.find.call(dt.files, isImageFile);
    if (!img) return;
    ev.preventDefault(); // 否则浏览器会导航到那张图
    getActive().accept(img);
  });
}

