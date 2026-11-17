// 录入入口的静态布局：只有照片入口。识别与逐件确认由 photo-import 重绘接管。
import { icon } from "../../core/ui.js";
export function importMarkup() {
  return `
    <div class="view-head"><h1>录入</h1></div>
    <p class="view-subtitle">给衣橱添一件</p>

    <form id="add-form" class="panel photo-form" novalidate>
      <div class="field field-wide photo-field"><span class="sr-only">照片</span>
        <input type="file" id="add-image" accept="image/*" multiple class="sr-only" tabindex="-1">
        <input type="file" id="add-camera-image" accept="image/*" capture="environment" class="sr-only" tabindex="-1">
        <div class="add-shot" id="add-shot" hidden><img id="add-shot-img" alt=""></div>
        <div class="photo-picker import-source-card" data-import-source>
          ${icon("photo")}<strong>把衣服放进衣橱</strong>
          <span class="faint">可连续拍摄或选择多张照片，自动识别属性并生成白底平铺图。</span>
          <div class="import-source-actions">
            <button type="button" class="primary" data-take-photo aria-label="拍照">${icon("camera")}</button>
            <button type="button" data-pick-photos aria-label="从相册选择，可多选">${icon("photo")}</button>
          </div>
        </div>
        <section class="import-selection" data-import-selection aria-label="待识别照片" hidden>
          <div class="import-batch-top"><span data-selection-count aria-live="polite"></span><span class="faint">待识别</span></div>
          <div class="import-batch-track" aria-hidden="true"></div>
          <div class="import-batch-thumbs" data-selection-thumbs role="group" aria-label="已选照片"></div>
          <p class="import-position" data-selection-position></p>
          <div class="import-photo"><img data-selection-preview alt=""></div>
          <div class="import-selection-remove"><button type="button" class="ghost danger" data-remove-photo>移除这张</button></div>
          <div class="import-selection-actions">
            <button type="button" data-take-photo>${icon("camera")}再拍一件</button>
            <button type="button" data-pick-photos>${icon("photo")}从相册添加</button>
            <button type="button" class="primary" data-start-recognition>开始识别</button>
          </div>
        </section>
      </div>
    </form>
  `;
}
