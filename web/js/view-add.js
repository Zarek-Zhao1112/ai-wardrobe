/** 录入页。两条路：
 *
 * ① **粘商品文案 → 识别 → 批量入库**（P5 / 设计文档 §11.3）
 * ② 手填属性 → 保存（原有）
 *
 * 关于 ① 的两个设计点：
 *
 * - **待确认清单只存在前端**（无状态）。后端 `/items/parse` 只解析、不落库，
 *   也不写 `conversation_state` —— 所以不用动 schema，代价是刷新页面清单会丢。
 * - **绝不抓取那个网页**：只读用户粘的文字。淘宝无登录态读不到（实测 6 条入口全被挡），
 *   而分享文案里本来就有商品名。
 *
 * 手填那条路保留原来的「录入后即时反馈」（§9.1/§9.2）：让付出立刻看得见回报。
 */

import { api } from "./api.js";
import { formHtml, readForm, summaryHtml, typeOptionsHtml } from "./fields.js";
import { esc, itemChipsHtml, renderError, toast } from "./ui.js";

export const label = "录入";

// ---------------------------------------------------------------------------
// 粘贴 / 拖拽图片（2026-10-20 用户拍板）
//
// 目的：电脑上截图后不用「保存 → 点选择文件 → 找路径」，直接 Ctrl+V 就行。
//
// ⚠️ **全局监听只注册一次**。`mount` 每进一次录入页就跑一次，
//   如果每次都往 `document` 上加监听，进出五次后一次粘贴会触发五次。
//   所以用模块级 `activeAdd` 指向**当前**那个录入页，监听器只认它。
// ---------------------------------------------------------------------------
let activeAdd = null;

function isImageFile(f) {
  return !!f && typeof f.type === "string" && f.type.indexOf("image/") === 0;
}

function bindClipboardOnce() {
  if (bindClipboardOnce.bound) return;
  bindClipboardOnce.bound = true;

  // ⚠️ 判据用 `fileEl.isConnected`，不是 `root.isConnected` ——
  //   外壳容器是持久的，切走别的页面后它照样连接着；
  //   但输入框是视图内部的节点，会随 innerHTML 替换掉。
  const live = () => activeAdd && activeAdd.fileEl && activeAdd.fileEl.isConnected;
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
    activeAdd.accept(img);
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
    activeAdd.root.classList.add("drop-active");
  });
  document.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (depth === 0 && activeAdd) activeAdd.root.classList.remove("drop-active");
  });
  document.addEventListener("drop", (ev) => {
    if (!live()) return;
    depth = 0;
    activeAdd.root.classList.remove("drop-active");
    const dt = ev.dataTransfer;
    if (!dt || !dt.files || !dt.files.length) return;
    const img = Array.prototype.find.call(dt.files, isImageFile);
    if (!img) return;
    ev.preventDefault(); // 否则浏览器会导航到那张图
    activeAdd.accept(img);
  });
}

export async function mount(root, ctx) {
  const taxonomy = ctx.taxonomy || {};

  root.innerHTML = `
    <div class="view-head"><h1>录一件衣服</h1></div>

    <section class="panel">
      <h2>① 粘商品链接 / 分享文案</h2>
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
    </section>

    <div id="paste-result"></div>

    <form id="add-form" class="panel" novalidate>
      <h2>② 或者手动填</h2>
      <label class="field field-wide"><span>照片（可选）</span>
        <input type="file" id="add-image" accept="image/*">
        <small class="faint">电脑上可以直接 Ctrl+V 粘贴截图，或把图片拖进页面</small>
      </label>
      ${formHtml(taxonomy)}
      <div class="form-actions">
        <button type="submit" class="primary" id="add-submit">保存</button>
        <span class="faint" id="add-hint">只填品类也能存 —— 颜色、材质这些以后随时能补。</span>
      </div>
    </form>
    <div id="add-result"></div>
  `;

  const pasteText = root.querySelector("#paste-text");
  const pasteBtn = root.querySelector("#paste-btn");
  const pasteResult = root.querySelector("#paste-result");
  const form = root.querySelector("#add-form");
  const fileEl = root.querySelector("#add-image");
  const addResult = root.querySelector("#add-result");

  /** 把粘贴 / 拖拽来的图片塞进 file input，之后走的还是原来那条路：
   *  `change` → 保存时上传 → 后端自动打标。**不另开一条上传链路**，
   *  否则两条路早晚会走岔（一条有打标、一条没有）。 */
  function acceptImage(file) {
    try {
      // `input.files` 是只读的，只能整个换掉（DataTransfer 是标准做法）
      const dt = new DataTransfer();
      dt.items.add(file);
      fileEl.files = dt.files;
      fileEl.dispatchEvent(new Event("change", { bubbles: true }));
      const kb = Math.max(1, Math.round(file.size / 1024));
      toast(`已接住图片（${kb} KB）—— 保存时自动识别属性`, "ok");
    } catch (err) {
      // 极老的浏览器不给 `input.files` 赋值
      toast("这张图接不住，请点「选择文件」", "error");
    }
  }

  activeAdd = { root, fileEl, accept: acceptImage };
  bindClipboardOnce();

  // ------------------------------------------------------------------
  // ① 粘贴 → 解析 → 预览 → 批量提交
  // ------------------------------------------------------------------

  /** 解析结果。**前端持有** —— 后端不存，这是刻意的无状态设计。 */
  let rows = [];

  /** 请求代号：每发起一次 +1。回来时对不上就**丢弃**结果。
   *
   * 为什么需要它：用户可能在上一轮还没回来时又发起一轮（改内容就能重试）。
   * 没有代号的话，**先发的慢响应会回来覆盖后发的结果** —— 看到的是旧链接的识别结果，
   * 而且无法察觉（静默错）。
   */
  let parseGen = 0;

  async function doParse() {
    const text = (pasteText.value || "").trim();
    if (!text) {
      toast("先粘点东西进来", "error");
      return;
    }
    const gen = ++parseGen;
    pasteBtn.disabled = true;
    pasteBtn.textContent = "识别中…";
    try {
      const resp = await api.parseItems(text);
      if (gen !== parseGen) return; // 已经过期（用户改了内容 / 又点了一次）
      rows = resp.items || [];
      if (!rows.length) {
        pasteResult.innerHTML =
          `<div class="panel"><div class="alert">${esc(resp.message || "没认出商品名")}</div></div>`;
        return;
      }
      renderPreview();
    } catch (err) {
      if (gen !== parseGen) return;
      renderError(pasteResult, err);
    } finally {
      if (gen === parseGen) {
        pasteBtn.disabled = false;
        pasteBtn.textContent = "识别";
      }
    }
  }

  // ★ 改了输入框 → **立刻**作废在飞的那一轮，按钮马上恢复。
  //
  // 为什么必须有（2026-10-20 用户反馈"识别不出来的链接一直识别、不能换链接"）：
  // 请求期间按钮是禁用的。如果那次请求迟迟不返回（后端没起 / 网络挂住），
  // 按钮就一直停在「识别中…」，用户改了链接也点不动 —— 界面上没有任何出路。
  // 现在把"改内容"当作"我要重来"，不用干等超时。
  pasteText.addEventListener("input", () => {
    if (!pasteBtn.disabled) return;
    parseGen += 1; // 让旧请求的结果被丢弃
    pasteBtn.disabled = false;
    pasteBtn.textContent = "识别";
    pasteResult.innerHTML = ""; // 上一轮的提示/预览作废，免得误当成新结果
  });

  function renderPreview() {
    pasteResult.innerHTML = `
      <section class="panel">
        <h2>识别到 ${rows.length} 件，确认后入库</h2>
        <div id="paste-error"></div>
        <p class="faint">不需要的取消勾选即可。品类认不出的必须选一个才能入库。</p>
        <div id="preview-rows">
          ${rows.map((row, i) => rowHtml(row, i)).join("")}
        </div>
        <div class="form-actions">
          <button type="button" class="primary" id="paste-save">入库选中的</button>
          <button type="button" class="ghost" id="paste-clear">清空</button>
        </div>
      </section>
    `;

    pasteResult.querySelector("#paste-clear").addEventListener("click", () => {
      rows = [];
      pasteResult.innerHTML = "";
      pasteText.value = "";
    });

    pasteResult.querySelector("#paste-save").addEventListener("click", doSubmit);
  }

  function rowHtml(row, i) {
    const notes = [];
    if (row.duplicate_hint) {
      notes.push(`<div class="alert">${esc(row.duplicate_hint)}（仍可入库）</div>`);
    }
    if (row.unresolved && Object.keys(row.unresolved).length) {
      notes.push(
        `<p class="faint">这几个没认出来：${esc(Object.values(row.unresolved).join("、"))}</p>`
      );
    }
    // ⚠️ 提示要挂在**真能出现**的条件下：模型降级时 `type` 必为空 →
    //   一定是 `needs_type` 那一支。原来写成 `!row.needs_type && ...` ，
    //   与 "needs_type 为真" 互斥 → 恒为假、永远不显示（W4 审查抓到，
    //   后端同款死代码在同一轮被删）。
    if (row.needs_type && row.llm_error) {
      notes.push(`<p class="faint">模型没接上：${esc(row.llm_error)}</p>`);
    }

    // 认不出品类 → 必须让用户选（§11.3：认不出就不许入库）
    const typeField = row.needs_type
      ? `<label class="field"><span>品类（必选）</span>
           <select data-role="type" data-i="${i}">
             ${typeOptionsHtml(taxonomy, "选一个品类")}
           </select>
         </label>`
      : "";

    return `
      <div class="panel" data-row="${i}">
        <label class="chips">
          <span class="chip">
            <input type="checkbox" data-role="pick" data-i="${i}"
              ${row.needs_type ? "" : "checked"}>
            入库
          </span>
        </label>
        <p><strong>${esc(row.title)}</strong></p>
        ${
          row.platform || row.url
            ? `<p class="faint">${esc(row.platform || "")} ${esc(row.url || "")}</p>`
            : ""
        }
        ${
          row.needs_type
            ? `<p><span class="chip warn">没认出品类</span></p>`
            : summaryHtml({ ...row.fields, type_label: row.type_label }, taxonomy)
        }
        ${typeField}
        ${notes.join("")}
      </div>
    `;
  }

  async function doSubmit() {
    const picked = [];
    let missingType = 0;

    for (const row of rows) {
      const box = pasteResult.querySelector(`input[data-role="pick"][data-i="${row.index}"]`);
      if (!box || !box.checked) continue;

      let type = row.fields.type || "";
      if (row.needs_type) {
        const sel = pasteResult.querySelector(`select[data-role="type"][data-i="${row.index}"]`);
        type = sel ? sel.value : "";
        if (!type) missingType += 1;
      }
      picked.push({ ...row.fields, type: type || "other" });
    }

    if (!picked.length) {
      toast("一件都没选", "error");
      return;
    }
    if (missingType) {
      toast(`还有 ${missingType} 件没选品类`, "error");
      return;
    }

    const saveBtn = pasteResult.querySelector("#paste-save");
    const clearBtn = pasteResult.querySelector("#paste-clear");
    saveBtn.disabled = true;
    saveBtn.textContent = "入库中…";
    // 提交中别让「清空」还能点 —— 点了之后返回的结果面板又会把清单"复活"，看着像没清掉（W4 抓到）
    if (clearBtn) clearBtn.disabled = true;
    try {
      const resp = await api.bulkItems({ items: picked });
      renderBulkResult(resp);
      toast(resp.failed ? "部分入库失败" : "已入库", resp.failed ? "error" : "ok");
    } catch (err) {
      // ⚠️ **不要** renderError(pasteResult, err)：那会把整块预览（用户已勾选的行、
      //    已选好的品类）一起冲掉。清单只在前端内存里、没有别的入口，
      //    冲掉就等于让用户从头再粘一遍（W4 的 Bug 猎手抓到）。只把错误写进专门的位置。
      const box = pasteResult.querySelector("#paste-error");
      if (box) box.innerHTML = `<div class="alert error">入库失败：${esc(err.message)}</div>`;
      toast("入库失败", "error");
      // 按钮**重新查一遍**拿：出错路径上 DOM 可能已经不是原来那批节点了
      const b = pasteResult.querySelector("#paste-save");
      const c = pasteResult.querySelector("#paste-clear");
      if (b) { b.disabled = false; b.textContent = "入库选中的"; }
      if (c) c.disabled = false;
    }
  }

  function renderBulkResult(resp) {
    const bad = (resp.outcomes || []).filter((o) => o.status !== "created");
    const delta = resp.combination_delta || {};
    const grew = typeof delta.before === "number" && delta.after > delta.before;

    pasteResult.innerHTML = `
      <section class="panel ok-panel">
        <h2>入库完成${grew ? `：可搭组合 ${delta.before} → ${delta.after} 套` : ""}</h2>
        <p>${esc(resp.message || "")}</p>
        ${
          bad.length
            ? `<div class="alert">这几件没进去：<ul>${bad
                .map((o) => `<li>${esc(o.name || "(无名字)")} —— ${esc(o.reason || "")}</li>`)
                .join("")}</ul></div>`
            : ""
        }
        <div class="form-actions">
          <a class="btn" href="#wardrobe">去衣橱看看</a>
          <button type="button" class="ghost" id="paste-again">再粘一批</button>
        </div>
      </section>
    `;
    rows = [];
    pasteResult.querySelector("#paste-again").addEventListener("click", () => {
      pasteResult.innerHTML = "";
      pasteText.value = "";
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  pasteBtn.addEventListener("click", doParse);

  // ------------------------------------------------------------------
  // ② 手填（原有逻辑，未改）
  // ------------------------------------------------------------------

  const submitBtn = root.querySelector("#add-submit");
  const addHint = root.querySelector("#add-hint");

  // ★ 有照片时把提示改成"我来认"：这是视觉打标**唯一需要用户知道的入口变化**。
  //   只在真能生效时才说（`vision_ready`）—— 没配视觉模型却说"我来认"，
  //   用户会一直等一个不会发生的事。
  if (ctx.health && ctx.health.vision_ready) {
    const syncHint = () => {
      const has = !!(fileEl.files && fileEl.files[0]);
      const typePicked = !!readForm(form).type;
      addHint.textContent = has && !typePicked
        ? "传了照片就不用选品类了 —— 保存时我来看图认，认错了再改。"
        : "只填品类也能存 —— 颜色、材质这些以后随时能补。";
    };
    fileEl.addEventListener("change", syncHint);
    form.addEventListener("change", syncHint);
    syncHint();
  }

  /**
   * 把打标返回的 `unresolved` 摊平成一句人话。
   *
   * ⚠️ 为什么必须有它（设计文档 §10.2 承诺过）：
   * 归一失败的值**不会**写进库（宁缺勿脏），所以它们**只存在于返回值里**。
   * 界面不展示 = 这些信息彻底消失，用户以为模型全认出来了。
   *
   * 值有两种形状：多值字段是**数组**（`["light"]`），单值字段是**字符串**。
   */
  function unresolvedText(unresolved) {
    const parts = [];
    for (const v of Object.values(unresolved || {})) {
      if (Array.isArray(v)) parts.push(...v.map(String));
      else if (v !== null && v !== undefined && v !== "") parts.push(String(v));
    }
    return parts.join("、");
  }

  /** 打标结果的说明块。`tagging` 缺失（老服务）时返回空串。 */
  function taggingNoteHtml(tagging) {
    if (!tagging || !tagging.status) return "";
    const missed = unresolvedText(tagging.unresolved);
    switch (tagging.status) {
      case "applied":
        return `
          <p class="faint">照片已自动识别：AI 帮你补了 ${esc(
            Object.keys(tagging.tags || {}).length
          )} 项属性${
            missed
              ? `（<strong>没认出来</strong>：${esc(missed)} —— 没写进库，你可以手动补）`
              : ""
          }。认错了在详情页改就行。</p>`;
      case "empty":
        return `<p class="faint">照片已存，但这张图没认出可用属性${
          missed ? `（${esc(missed)}）` : ""
        }。属性可以手动填。</p>`;
      case "skipped":
        return `<p class="faint">照片已存。没配视觉模型（.env 的 VISION_MODEL），属性请手动填。</p>`;
      case "failed":
        // 打标失败**不影响照片**，文案不能让人以为整件事失败了。
        return `<div class="alert">照片已存，但自动识别失败：${esc(
          tagging.message || "请手动填属性"
        )}</div>`;
      default:
        return "";
    }
  }

  function renderFeedback(resp, imageError, tagging) {
    const outfit = (resp.sample_outfits || [])[0];
    const delta = resp.combination_delta || {};
    const grew = typeof delta.before === "number" && delta.after > delta.before;
    addResult.innerHTML = `
      <div class="panel ok-panel">
        <h2>已入库${grew ? `：可搭组合 ${delta.before} → ${delta.after} 套` : ""}</h2>
        <p>${esc(resp.message || "")}</p>
        ${
          imageError
            ? `<div class="alert">照片没传上去：${esc(imageError.message)}
               （单品已经保存了，可以在详情页重传）</div>`
            : ""
        }
        ${taggingNoteHtml(tagging)}
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
    addResult.querySelector("#add-again").addEventListener("click", () => {
      form.reset();
      fileEl.value = "";
      addResult.innerHTML = "";
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const payload = readForm(form);
    const file = fileEl.files && fileEl.files[0];

    // ★ 品类门槛：**传了照片就不逼你先选**。
    //
    // 为什么：视觉打标能自己认品类（实测 5/5 全对），硬要你先选
    // 等于把最有价值的那个能力架空了 —— 而且录衣服时你手上只有照片。
    //
    // 底线仍在：**没照片**时必须选（没有别的信息源，存 `other` 等于扔掉一件衣服）。
    // 有照片时先按 `other` 建单品，随后打标会**把它改成真品类**
    // （`tag_and_apply` 的 `_only_blank` 特判 `other` = 还没填）。
    if (!payload.type) {
      if (!file) {
        toast("先选一个品类（或者传张照片，让我来认）", "error");
        return;
      }
      payload.type = "other";
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "保存中…";
    try {
      const resp = await api.createItem(payload);

      // 图片要等单品建好才有 id —— 分两步。
      // 图失败**不能**说成"保存失败"：单品已经进库了。
      //
      // ⚠️ 上传接口现在会顺手跑一次打标，返回体里带 `tagging`。
      //    **必须把它讲出来**：否则用户看到属性悄悄变了却什么提示都没有。
      //    打标失败绝不影响入库结果（照片已经存好了、单品也已经建好了）。
      let imageError = null;
      let tagging = null;
      if (file) {
        try {
          const up = await api.uploadImage(resp.item.id, file);
          tagging = up.tagging || null;
        } catch (err) {
          imageError = err;
        }
      }

      renderFeedback(resp, imageError, tagging);
      // toast 只说"照片没传上"这种**用户需要马上知道**的事；
      // 打标是增强信息，放在结果区里说清（toast 3 秒就没了）。
      toast(imageError ? "已入库（照片没传上）" : "已入库", imageError ? "error" : "ok");
    } catch (err) {
      renderError(addResult, err);
      toast("保存失败", "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "保存";
    }
  });
}
