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

/**
 * 把后端 `unresolved` 摊平成一句人话："这几个没认出来：xxx、yyy"。
 *
 * ⚠️ 为什么必须有它（设计文档 §10.2 承诺过）：
 * 归一失败的值**不会**写进库（宁缺勿脏），所以它们**只存在于返回值里**。
 * 前端不展示 = 这些信息彻底消失，用户以为模型全认出来了。
 * 粘贴录入那条路早就展示了（`view-add.js` 的预览行），打标这条路原本漏了。
 *
 * `unresolved` 的值有两种形状：多值字段是**数组**（`["light"]`），
 * 单值字段是**字符串**（`"荧光_chartreuse"`），所以要分别处理。
 */
function unresolvedText(unresolved) {
  const parts = [];
  for (const v of Object.values(unresolved || {})) {
    if (Array.isArray(v)) {
      parts.push(...v.map(String));
    } else if (v !== null && v !== undefined && v !== "") {
      parts.push(String(v));
    }
  }
  return parts.join("、");
}

/** 上传照片后自动打标的提示。`tagging` 缺失（老服务）时退回旧文案。 */
function uploadToast(tagging) {
  if (!tagging) return "照片已更新";
  const missed = unresolvedText(tagging.unresolved);
  switch (tagging.status) {
    case "applied":
      return missed
        ? `照片已更新，AI 认出了大部分属性（没认出来：${missed}），可以在下面改`
        : "照片已更新，AI 已自动填好属性，可以在下面改";
    case "empty":
      return missed
        ? `照片已更新，但这张图没认出可用属性（${missed}），请手动填`
        : "照片已更新，这张图没认出可用属性，请手动填";
    case "skipped":
      return "照片已更新（没配视觉模型，属性请手动填）";
    case "failed":
      // 打标失败**不影响照片**，所以文案不能让人以为整件事失败了。
      return `照片已更新，但自动识别失败：${tagging.message || "请手动填属性"}`;
    default:
      return "照片已更新";
  }
}

/** 手动点「AI 识别属性」的提示。 */
function tagToast(res) {
  const missed = unresolvedText(res && res.unresolved);
  if (res && res.status === "empty") {
    return missed ? `没认出可用属性（${missed}）` : "没认出可用属性";
  }
  if (missed) return `识别完成（这几个没认出来：${missed}）`;
  return "识别完成";
}

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
      `<p class="mt-3"><a href="#wardrobe">← 回衣橱</a></p>`
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

      // ★ 必须把「上传」和「重新拉取」分成两步 —— 它们的失败含义完全不同。
      //   合在一个 try 里的话，照片明明已经存好了，却因为随后那次 GET 失败
      //   而报「上传失败」，用户会以为白传了、重新传一遍（W4 的 Bug 猎手抓到）。
      //   `view-add.js` 早就把这两步拆开了，这里是漏掉的一处。
      let res;
      try {
        res = await api.uploadImage(item.id, file);
      } catch (err) {
        toast("上传失败：" + err.message, "error");
        btn.disabled = false;
        btn.textContent = "上传 / 替换";
        return;
      }

      // 到这里照片**已经存好了**，下面失败不能说成"上传失败"。
      // ⚠️ 上传接口还会顺手跑一次打标，返回里带 `tagging` ——
      //    **必须把它讲出来**，否则用户看到照片变了、属性悄悄也变了却没提示
      //    （静默改动比不改动更让人不放心）。打标失败绝不影响上传结果。
      toast(uploadToast(res.tagging), "ok", 3600);
      try {
        const fresh = await api.getItem(item.id);
        render(fresh);
      } catch (err) {
        toast("照片已更新，但页面没刷新过来：" + err.message, "error");
        btn.disabled = false;
        btn.textContent = "上传 / 替换";
      }
    });

    root.querySelector("#item-tag").addEventListener("click", async () => {
      const btn = root.querySelector("#item-tag");
      btn.disabled = true;
      btn.textContent = "识别中…";
      try {
        const res = await api.tagItem(item.id);
        const fresh = await api.getItem(item.id);
        toast(tagToast(res), "ok", 3600);
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
