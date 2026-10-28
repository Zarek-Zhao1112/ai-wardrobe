// 商品文案解析、预览与确认入库；离页后忽略迟到结果。
import { api } from "../../core/api.js";
import { esc, toast, renderError } from "../../core/ui.js";
import { summaryHtml, typeOptionsHtml } from "../../core/fields.js";
export function mountTextImport({root, taxonomy, pasteText, pasteBtn, pasteResult, isSaving, beginSave, endSave, scrollTop, isCurrent}) {
  let disposed = false;
  const current = () => !disposed && isCurrent();
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
    if (isSaving()) return;
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
      if (!current() || gen !== parseGen) return; // 已经过期（用户改了内容 / 又点了一次）
      rows = resp.items || [];
      if (!rows.length) {
        pasteResult.innerHTML =
          `<div class="panel"><div class="alert">${esc(resp.message || "没认出商品名")}</div></div>`;
        return;
      }
      renderPreview();
    } catch (err) {
      if (!current() || gen !== parseGen) return;
      renderError(pasteResult, err);
    } finally {
      if (current() && gen === parseGen) {
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
        <label class="chips control-toggle">
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
    if (isSaving()) return;
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
    beginSave();
    saveBtn.disabled = true;
    saveBtn.textContent = "入库中…";
    // 提交中别让「清空」还能点 —— 点了之后返回的结果面板又会把清单"复活"，看着像没清掉（W4 抓到）
    if (clearBtn) clearBtn.disabled = true;
    try {
      const resp = await api.bulkItems({ items: picked });
      root.dataset.wardrobeDirty = "true";
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
    } finally { endSave(); }
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
      scrollTop();
    });
  }

  pasteBtn.addEventListener("click", doParse);
  return () => { disposed = true; parseGen += 1; };
}
