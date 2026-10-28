// 录入入口的静态布局，与任务状态和事件分开维护。
import { icon } from "../../core/ui.js";
import { formHtml } from "../../core/fields.js";
export function importMarkup(taxonomy) {
  return `
    <div class="view-head"><h1>录入</h1></div>
    <p class="view-subtitle">给衣橱添一件</p>

    <details class="panel paste-panel">
      <summary>粘贴商品文案录入</summary>
      <p class="faint">
        在淘宝点「复制链接」拿到的那<strong>整段文案</strong>，直接粘进来即可 ——
        可以一次粘好几件。我只读你粘的这段文字，不会去打开那个网页。
      </p>
      <label class="field field-wide"><span>商品文案</span>
        <textarea id="paste-text" rows="3"
          placeholder="【淘宝】7天无理由退货 https://… 「宽松直筒牛仔裤 深灰」"></textarea>
      </label>
      <div class="form-actions">
        <button type="button" class="primary" id="paste-btn">识别</button>
        <span class="faint">认不出的品类会让你在下面选，不会硬猜。</span>
      </div>
    </details>

    <div id="paste-result"></div>

    <form id="add-form" class="panel photo-form" novalidate>
      <div class="field field-wide photo-field"><span class="sr-only">照片（可选）</span>
        <!-- 原生 input 藏起来（.sr-only 类），只留下面那个按钮当入口 ——
             它俩现在做的是同一件事，摆两个一模一样的按钮只会让人犹豫点哪个。
             tabindex="-1"：藏起来的控件不该占 Tab 停点，否则键盘用户会聚焦到
             一个看不见的东西上（真正的键盘路径是那个 button）。
             ⚠️ 这段在模板字符串里，注释里**不能写反引号**，会截断字符串。 -->
        <input type="file" id="add-image" accept="image/*" multiple class="sr-only" tabindex="-1">
        <div class="album-picked" id="add-album-picked" hidden></div>
        <div class="add-shot" id="add-shot" hidden><img id="add-shot-img" alt=""></div>
        <button type="button" class="photo-picker" id="add-album" aria-label="选择照片，可多选">${icon("photo")}<strong>把衣服放进衣橱</strong><span class="faint">一次选择多张照片，自动识别属性并生成白底平铺图。</span><span class="photo-select-cta">＋ 选择照片</span></button>
        <div class="photo-tools">
          <button type="button" class="ghost" id="add-crop" hidden>裁剪商品图</button>
        </div>
        <small class="faint">电脑上可以直接 Ctrl+V 粘贴截图，或把图片拖进页面</small>
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
