/** 录入页：填属性 → 保存 → 展示「录入后即时反馈」。
 *
 * 这里的重点是**反馈**（设计文档 §9.1/§9.2）：
 * 保存成功不是弹个"成功"就完了，而是告诉用户
 * 「你的可选搭配从 0 套变成 1 套」+ 一条用这件新衣服搭好的现成搭配。
 * 录入是体力活，得让付出立刻看得见回报。
 */

import { api } from "./api.js";
import { formHtml, readForm } from "./fields.js";
import { esc, itemChipsHtml, renderError, toast } from "./ui.js";

export const label = "录入";

export async function mount(root, ctx) {
  const taxonomy = ctx.taxonomy || {};

  root.innerHTML = `
    <div class="view-head"><h1>录一件衣服</h1></div>
    <form id="add-form" class="panel" novalidate>
      <label class="field field-wide"><span>照片（可选）</span>
        <input type="file" id="add-image" accept="image/*">
      </label>
      ${formHtml(taxonomy)}
      <div class="form-actions">
        <button type="submit" class="primary" id="add-submit">保存</button>
        <span class="faint">只填品类也能存 —— 颜色、材质这些以后随时能补。</span>
      </div>
    </form>
    <div id="add-result"></div>
  `;

  const form = root.querySelector("#add-form");
  const fileEl = root.querySelector("#add-image");
  const resultEl = root.querySelector("#add-result");
  const submitBtn = root.querySelector("#add-submit");

  function renderFeedback(resp, imageError) {
    const outfit = (resp.sample_outfits || [])[0];
    const delta = resp.combination_delta || {};
    const grew = typeof delta.before === "number" && delta.after > delta.before;
    resultEl.innerHTML = `
      <div class="panel ok-panel">
        <h2>已入库${grew ? `：可搭组合 ${delta.before} → ${delta.after} 套` : ""}</h2>
        <p>${esc(resp.message || "")}</p>
        ${
          imageError
            ? `<div class="alert">照片没传上去：${esc(imageError.message)}
               （单品已经保存了，可以在详情页重传）</div>`
            : ""
        }
        ${
          outfit
            ? `<p class="faint">它现在就能这么搭：</p>
               <div class="chips">${itemChipsHtml(outfit.items, taxonomy)}</div>`
            : ""
        }
        <div class="form-actions">
          <a class="btn" href="#item/${esc(resp.item.id)}">查看 / 修改这件</a>
          <button type="button" class="ghost" id="add-again">再录一件</button>
        </div>
      </div>
    `;
    resultEl.querySelector("#add-again").addEventListener("click", () => {
      form.reset();
      fileEl.value = "";
      resultEl.innerHTML = "";
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const payload = readForm(form);
    if (!payload.type) {
      toast("先选一个品类", "error");
      return;
    }
    submitBtn.disabled = true;
    submitBtn.textContent = "保存中…";
    try {
      const resp = await api.createItem(payload);

      // 图片要等单品建好才有 id —— 分两步。
      // 图失败**不能**说成"保存失败"：单品已经进库了。
      let imageError = null;
      const file = fileEl.files && fileEl.files[0];
      if (file) {
        try {
          await api.uploadImage(resp.item.id, file);
        } catch (err) {
          imageError = err;
        }
      }

      renderFeedback(resp, imageError);
      toast(imageError ? "已入库（照片没传上）" : "已入库", imageError ? "error" : "ok");
    } catch (err) {
      renderError(resultEl, err);
      toast("保存失败", "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "保存";
    }
  });
}
