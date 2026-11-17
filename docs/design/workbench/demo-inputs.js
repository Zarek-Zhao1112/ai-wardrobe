// 仅在工作台内把设备输入替换为仓库示例，后续仍走 App 的原有流程。
(() => {
  const photoURL = "/images/light-blue-shirt.jpg";
  const inputs = "#add-image, #add-camera-image, #import-append";

  // 试衣叠在搭配面板上时，仅让最上层面板管理焦点。
  document.addEventListener("focusin", event => {
    const panel = document.querySelector(".tryon-sheet-open .tryon-panel");
    if (!panel || !document.querySelector(".aesthetic-panel")) return;
    event.stopImmediatePropagation();
    if (!panel.contains(event.target)) panel.focus({ preventScroll: true });
  }, true);
  document.addEventListener("keydown", event => {
    const close = document.querySelector(".tryon-sheet-open [data-tryon-close]");
    if (event.key !== "Escape" || !close) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    close.click();
  }, true);

  document.addEventListener("click", async event => {
    const input = event.target.closest(inputs);
    if (!input) return;
    event.preventDefault();
    try {
      const response = await fetch(photoURL);
      if (!response.ok) throw Error("演示图片暂不可用");
      const transfer = new DataTransfer();
      transfer.items.add(new File([await response.blob()], "示例浅蓝衬衫.jpg", { type: "image/jpeg" }));
      if (!input.isConnected) return;
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    } catch (error) {
      console.error(error);
    }
  }, true);

  if (navigator.mediaDevices) {
    navigator.mediaDevices.getUserMedia = async ({ video } = {}) => {
      const canvas = document.createElement("canvas");
      canvas.width = 480;
      canvas.height = 640;
      const context = canvas.getContext("2d");
      const photo = new Image();
      photo.src = photoURL;
      await photo.decode();
      const scale = Math.min(360 / photo.width, 430 / photo.height);
      const width = photo.width * scale;
      const height = photo.height * scale;
      context.fillStyle = "#ebeef2";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(photo, (480 - width) / 2, (640 - height) / 2, width, height);
      context.fillStyle = "#1d1d1f";
      context.font = "24px sans-serif";
      context.textAlign = "center";
      if (video?.facingMode?.ideal === "user") {
        context.translate(canvas.width, 0);
        context.scale(-1, 1);
      }
      context.fillText("工作台演示画面", 240, 568);
      return canvas.captureStream(10);
    };
  }
})();

// 工作台可以直接进入 AI 通道菜单或模型设置。
const panel = new URLSearchParams(location.search).get("panel");
if (panel === "ai-channel" || panel === "ai-models") {
  document.addEventListener("DOMContentLoaded", () => {
    const status = document.getElementById("status");
    if (!status) return;
    const openMenu = () => {
      const menu = status.querySelector(".status-menu");
      const configure = menu?.querySelector("[data-ai-configure]");
      if (!configure) return;
      menu.open = true;
      observer.disconnect();
      if (panel === "ai-models") configure.click();
    };
    const observer = new MutationObserver(openMenu);
    observer.observe(status, { childList: true, subtree: true });
    openMenu();
  }, { once: true });
}
