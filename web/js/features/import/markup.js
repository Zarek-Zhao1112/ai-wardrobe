// 录入入口的静态布局，与任务状态和事件分开维护。
import { icon } from "../../core/ui.js";
import { formHtml } from "../../core/fields.js";
export function importMarkup(taxonomy) {
  return `
    <div class="view-head"><h1>录入</h1></div>
    <p class="view-subtitle">给衣橱添一件</p>

    <form id="add-form" class="panel photo-form" novalidate>
      <div class="field field-wide photo-field"><span class="sr-only">照片（可选）</span>
        <input type="file" id="add-image" accept="image/*" multiple class="sr-only" tabindex="-1">
        <input type="file" id="add-camera-image" accept="image/*" capture="environment" class="sr-only" tabindex="-1">
        <div class="album-picked" id="add-album-picked" hidden></div>
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
        <div class="photo-tools">
          <button type="button" class="ghost" id="add-crop" hidden>裁剪商品图</button>
        </div>
      </div>
      ${formHtml(taxonomy)}
      <div class="form-actions">
        <button type="button" class="ai" id="add-recognize" hidden>AI 识别</button>
        <button type="submit" class="primary" id="add-submit">保存到衣橱</button>
        <span class="faint" id="add-hint">只填品类也能存 —— 颜色、材质这些以后随时能补。</span>
      </div>
      <div class="note-line" id="add-recognize-note"></div>
    </form>
    <div id="add-result"></div>
  `;

}
