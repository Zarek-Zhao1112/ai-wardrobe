/** 单品详情 / 编辑页（hash：`#item/<id>`）。
 *
 * 这一页存在的理由（设计文档 §9.3）：**AI 打标一定会有打错的**，
 * 所以界面上必须能改 —— 而且改完要能看出"这条是人工改过的"。
 */

import { api } from "./api.js";
import { fillForm, formHtml, readForm, summaryHtml } from "./fields.js";
import { esc, fmtDateTime, renderError, toast } from "./ui.js";

export const label = "单品";
/** 不出现在顶部 Tab，只通过 `#item/<id>` 进入。 */
export const hidden = true;

export async function mount(root, ctx, arg) {
  const taxonomy = ctx.taxonomy || {};
  const id = arg;
  if (!id) {
    root.innerHTML = `<div class="alert error">缺少单品 id</div>`;
    return;
  }

  // 状态栏已经知道"有没有配视觉模型"了，那就别让用户去点一个必然失败的按钮 ——
  // 否则控制台会多一条红色的 503，看起来像坏了。
  const canTag = !!(ctx.health && ctx.health.vision_ready);

  root.innerHTML = `<p class="muted">加载中…</p>`;

  let first;
  try {
    first = await api.getItem(id);
  } catch (err) {
    renderError(root, err);
    root.insertAdjacentHTML(
      "beforeend",
      `<p style="margin-top:12px"><a href="#wardrobe">← 回衣橱</a></p>`
    );
    return;
  }

  function mediaHtml(item) {
    const url = api.imageUrl(item.image_path);
    return url
      ? `<img src="${esc(url)}" alt="">`
      : `<span class="ph">${esc(item.type_label || "还没有照片")}</span>`;
  }

  function render(item) {
    const rows = [
      ["穿着次数", item.wear_count ? `${item.wear_count} 次` : "还没穿过"],
      [
        "打标状态",
        item.tagging_status === "tagged"
          ? `已打标（${item.tagged_by === "ai" ? "AI" : item.tagged_by === "manual" ? "人工" : "未知"}）`
          : "待打标",
      ],
      ["入库时间", fmtDateTime(item.created_at)],
      ["最近更新", fmtDateTime(item.updated_at)],
    ];

    root.innerHTML = `
      <div class="view-head">
        <a class="back" href="#wardrobe">← 衣橱</a>
        <h1>${esc(item.name || item.type_label || "未命名")}</h1>
      </div>

      <div class="detail">
        <div class="detail-media">${mediaHtml(item)}</div>
        <div class="detail-info">
          <dl class="kv">${rows
            .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`)
            .join("")}</dl>
          ${summaryHtml(item, taxonomy)}
        </div>
      </div>

      <form id="item-form" class="panel">
        <h2>属性</h2>
        <p class="faint">AI 打标打错了就在这儿改 —— 改完会记为「人工修改」。</p>
        ${formHtml(taxonomy)}

        <div class="field field-wide"><span>照片</span>
          <div class="form-actions">
            <input type="file" id="item-image" accept="image/*">
            <button type="button" id="item-upload">上传 / 替换</button>
          </div>
        </div>

        <div class="form-actions">
          <button type="submit" class="primary" id="item-save">保存修改</button>
          <button type="button" class="ghost" id="item-tag"${
            canTag ? "" : ` disabled title="需要配一个能看图的模型"`
          }>${canTag ? "AI 识别属性" : "AI 识别（未配置视觉模型）"}</button>
          <span class="spacer"></span>
          <button type="button" class="danger" id="item-delete">删除这件</button>
        </div>
      </form>
    `;

    fillForm(root.querySelector("#item-form"), item);
    bind(item);
  }

  function bind(item) {
    const form = root.querySelector("#item-form");

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const btn = root.querySelector("#item-save");
      btn.disabled = true;
      btn.textContent = "保存中…";
      try {
        // ⚠️ 必须把**当前库里的值**传进去：`readForm` 靠它区分
        // 「用户取消了某个勾选」和「这个值本来就不在词表里」。
        // 不传的话，打开详情页什么都不改直接保存，会把词表外的值删光。
        const updated = await api.updateItem(item.id, readForm(form, item));
        toast("已保存", "ok");
        render(updated); // 重绘后按钮是新节点，不用复位
      } catch (err) {
        toast("保存失败：" + err.message, "error");
        btn.disabled = false;
        btn.textContent = "保存修改";
      }
    });

    root.querySelector("#item-upload").addEventListener("click", async () => {
      const fileEl = root.querySelector("#item-image");
      const file = fileEl.files && fileEl.files[0];
      if (!file) {
        toast("先选一张图片", "error");
        return;
      }
      const btn = root.querySelector("#item-upload");
      btn.disabled = true;
      btn.textContent = "上传中…";
      try {
        await api.uploadImage(item.id, file);
        const fresh = await api.getItem(item.id);
        toast("照片已更新", "ok");
        render(fresh);
      } catch (err) {
        toast("上传失败：" + err.message, "error");
        btn.disabled = false;
        btn.textContent = "上传 / 替换";
      }
    });

    root.querySelector("#item-tag").addEventListener("click", async () => {
      const btn = root.querySelector("#item-tag");
      btn.disabled = true;
      btn.textContent = "识别中…";
      try {
        await api.tagItem(item.id);
        const fresh = await api.getItem(item.id);
        toast("识别完成", "ok");
        render(fresh);
        return;
      } catch (err) {
        // 503 = 还没配视觉模型。这是"没配好"，不是故障，文案要区分开。
        if (err.status === 503) {
          toast("还没配视觉模型，打标用不了（属性可以手动填）", "error", 4200);
        } else {
          toast("识别失败：" + err.message, "error");
        }
      }
      btn.disabled = false;
      btn.textContent = "AI 识别属性";
    });

    root.querySelector("#item-delete").addEventListener("click", async () => {
      if (!window.confirm("确定删除这件吗？删掉就找不回来了。")) return;
      const btn = root.querySelector("#item-delete");
      btn.disabled = true;
      btn.textContent = "删除中…";
      try {
        await api.deleteItem(item.id);
        toast("已删除", "ok");
        location.hash = "#wardrobe";
      } catch (err) {
        toast("删除失败：" + err.message, "error");
        btn.disabled = false;
        btn.textContent = "删除这件";
      }
    });
  }

  render(first);
}
