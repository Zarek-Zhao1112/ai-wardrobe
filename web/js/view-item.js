/** 单品详情 / 编辑页（hash：`#item/<id>`）。
 *
 * 这一页存在的理由（设计文档 §9.3）：**AI 打标一定会有打错的**，
 * 所以界面上必须能改 —— 而且改完要能看出"这条是人工改过的"。
 */

import { albumAvailable, openAlbumPicker } from "./album.js";
import { api } from "./api.js";
import { openCropper } from "./crop.js";
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
      // ⚠️ 后端的 `empty` 有两种口径，别混成一句（2026-10-21 审查发现）：
      //   ① 这张图真的没认出可用属性；
      //   ② 认出来了，但**这件已经有属性**，`overwrite=False` 于是什么都不写。
      //   把 ② 说成"没认出"会让用户以为模型不行，把本来正确的属性重填一遍。
      //   后端已经把准确说法放进 `message`，优先用它。
      if (tagging.message) return `照片已更新（${tagging.message}）`;
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

  /** 相册通道是否可用（只有跑在手机上的服务才有 `/sdcard`）。
   *
   *  ★ 为什么要有（2026-10-21）：桌面图标打开的是小米浏览器的 PWA 壳，它没
   *    实现 `onShowFileChooser` —— `<input type=file>` 点下去**毫无反应**，
   *    不弹选择器也不报错。同一地址在手机浏览器里是正常的，所以不是页面问题。
   *    录入页早就接了相册通道（`/items/album/*`），详情页一直漏着：手机上
   *    换不了图，只能回电脑改。这里补上，浮层复用 `album.js`。
   *
   *  探测一次就够：`render()` 每次重绘都读这个变量决定按钮露不露。
   *  电脑上它一直是 false，界面上不会多一个点了没用的按钮。 */
  let canAlbum = false;

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
    // 带 `updated_at` 当版本号：这个单品的图是固定文件名，换图后 URL 不变，
    // 不加版本会被浏览器 / HTTP 缓存拿旧图（详见 `api.imageUrl` 的注释）。
    const url = api.imageUrl(item.image_path, item.updated_at);
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
            <!-- 手机 PWA 壳点不动上面那个 <input type=file>（没实现
                 onShowFileChooser），相册通道是那边唯一的入口。
                 电脑上相册探测返回 false，这个按钮不会露出来。 -->
            <button type="button" class="ghost" id="item-album"${
              canAlbum ? "" : " hidden"
            }>从手机相册选</button>
            <!-- 裁剪针对库里那张图（同源 /images/…，canvas 画得进去）。 -->
            <button type="button" class="ghost" id="item-crop"${
              item.image_path ? "" : " hidden"
            }>裁剪这张</button>
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

    // ----------------------------------------------------------------
    // 照片：三个入口共用一套收尾
    //   ① 选文件（桌面 / 手机浏览器）
    //   ② 手机相册（PWA 壳里唯一能用的那条，见 album.js）
    //   ③ 裁剪当前图（同源 /images/…，canvas 能画）
    // 三个入口**最终都落到同一个上传接口**（`/items/{id}/image` 与
    // `/items/{id}/image-from-album` 都走 `_save_and_tag`），所以收尾、
    // 打标提示、失败语义必须一致 —— 各写一份迟早走岔。
    // ----------------------------------------------------------------

    /** 照片换好之后的收尾：报打标结果 → 重新拉一遍 → 重绘。
     *
     * @param idleLabel 失败时把按钮恢复成的**静止态文案**（不是忙碌文案）。
     */
    async function afterImage(res, btn, idleLabel) {
      // ★ 必须把「上传」和「重新拉取」分成两步 —— 它们的失败含义完全不同。
      //   合在一个 try 里的话，照片明明已经存好了，却因为随后那次 GET 失败
      //   而报「上传失败」，用户会以为白传了、重新传一遍（W4 的 Bug 猎手抓到）。
      //
      // ⚠️ 上传接口还会顺手跑一次打标，返回里带 `tagging` ——
      //    **必须把它讲出来**，否则用户看到照片变了、属性悄悄也变了却没提示
      //    （静默改动比不改动更让人不放心）。打标失败绝不影响上传结果。
      toast(uploadToast(res && res.tagging), "ok", 3600);
      try {
        const fresh = await api.getItem(item.id);
        // ★ `root` 是所有路由共用的 `#view`（`app.js` 里只有一个）。上传 + 打标
        //   要 1–3 秒，这期间用户完全可能已经切到衣橱页了 —— 这时再 `render()`
        //   就会把衣橱整页换成这件单品的详情，而且 `hash` 没变，用户再点同一个
        //   Tab 不会触发 `hashchange`，只能先点别的再点回来。
        //   判据和 `view-add.js` 的 `fileEl.isConnected` 是同一套。
        if (!btn.isConnected) return;
        render(fresh); // 重绘后按钮是新节点，不用复位
      } catch (err) {
        toast("照片已更新，但页面没刷新过来：" + err.message, "error");
        if (!btn.isConnected) return;
        btn.disabled = false;
        btn.textContent = idleLabel;
      }
    }

    /** 忙碌态 + 失败文案。三个入口分开写的话，早晚有一个忘了复位按钮
     *  —— 表现就是「点了没反应」，而且看不出是失败还是慢。 */
    async function runImageAction(btn, label, busy, failPrefix, run) {
      btn.disabled = true;
      btn.textContent = busy;
      let res;
      try {
        res = await run();
      } catch (err) {
        toast(failPrefix + err.message, "error");
        btn.disabled = false;
        btn.textContent = label;
        return;
      }
      await afterImage(res, btn, label);
    }

    root.querySelector("#item-upload").addEventListener("click", async () => {
      const fileEl = root.querySelector("#item-image");
      const file = fileEl.files && fileEl.files[0];
      if (!file) {
        toast("先选一张图片", "error");
        return;
      }
      await runImageAction(
        root.querySelector("#item-upload"),
        "上传 / 替换",
        "上传中…",
        "上传失败：",
        () => api.uploadImage(item.id, file)
      );
    });

    // ② 手机相册：照片由后端从磁盘直接读，浏览器一次都没碰本地文件。
    root.querySelector("#item-album").addEventListener("click", async () => {
      const btn = root.querySelector("#item-album");
      const picked = await openAlbumPicker({ title: "换一张照片" });
      if (!picked) return; // 关掉了 / 点了空白
      // ★ 详情页是**立刻写库**（一个单品只留一张图，旧文件直接被覆盖），
      //   而录入页是"先选中、保存才生效"。同一套浮层、同一个动作、后果不同 ——
      //   所以这里必须确认一次，否则按录入页的习惯点一下就把原图盖掉了，
      //   而且不可撤销。
      if (!window.confirm("用这张替换现在的照片？原来的照片不会保留。")) return;
      // 挑的时候可能已经点导航走了：这时按钮已脱离文档，忙碌态和 toast
      // 都会落到一个没人看的页面上。
      if (!btn.isConnected) return;
      await runImageAction(btn, "从手机相册选", "读取中…", "换图失败：", () =>
        api.importFromAlbum(item.id, picked.id)
      );
    });

    // ③ 裁剪当前那张。
    //   ★ `src` 必须**同源**（`/images/…` 由本服务发）：跨源图会污染 canvas，
    //     `toBlob` 直接抛 SecurityError —— 表现就是"裁完没反应"。
    //   ★ 这里不传 `initial`：详情页没有 AI 框可继承，用 crop.js 的居中默认框。
    root.querySelector("#item-crop").addEventListener("click", async () => {
      const btn = root.querySelector("#item-crop");
      const src = api.imageUrl(item.image_path, item.updated_at);
      if (!src) return;

      btn.disabled = true;
      btn.textContent = "裁剪中…";
      let out;
      try {
        out = await openCropper({ src, title: "调整取景" });
      } catch (err) {
        // 打不开窗口（图丢了 / 读不出来）。不能让按钮停在"裁剪中…"。
        if (btn.isConnected) {
          btn.disabled = false;
          btn.textContent = "裁剪这张";
        }
        toast("裁剪窗口打不开：" + err.message, "error");
        return;
      }

      // 浮层关掉后还有 170ms 的淡出，这期间用户可能已经切页了。
      if (!btn.isConnected) return;
      btn.disabled = false;
      btn.textContent = "裁剪这张";
      if (!out) return; // 用户选了「保持原图」

      const file = new File([out.blob], `crop-${Date.now()}.jpg`, { type: "image/jpeg" });
      await runImageAction(btn, "裁剪这张", "上传中…", "上传失败：", () =>
        api.uploadImage(item.id, file)
      );
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

  // 相册探测放在首屏之后：`canAlbum` 只影响**后续**重绘，这一次得直接改 DOM
  // —— 否则用户要保存一次才看得见按钮。电脑上返回 false，按钮保持 hidden。
  albumAvailable().then((ok) => {
    canAlbum = ok;
    const btn = root.querySelector("#item-album");
    if (btn) btn.hidden = !ok;
  });
}
