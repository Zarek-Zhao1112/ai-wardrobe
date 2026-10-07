/** 搭配页：生成推荐 → 反馈 → 看历史。
 *
 * 这是整条闭环在界面上的出口：反馈会**顺带更新偏好记忆**
 * （见 `services/feedback.py`），所以反馈完要告诉用户"记忆更新了 N 条"——
 * 否则他不知道自己的点击到底起没起作用。
 *
 * 刻意**不自动生成**：每次生成都会往 `outfits` 表写数据，
 * 一进页面就写、反复进反复写，纯属污染。
 */

import { api } from "./api.js";
import { esc, fmtDateTime, itemChipsHtml, renderError, toast } from "./ui.js";

export const label = "搭配";

const FB_TEXT = { like: "这套行", dislike: "不行", swap: "换一套" };
const STATUS_TEXT = {
  proposed: "待反馈",
  accepted: "已采纳",
  rejected: "未采纳",
  swapped: "换过",
};

function optionsHtml(vocab, placeholder) {
  return (
    `<option value="">${esc(placeholder)}</option>` +
    Object.entries(vocab || {})
      .map(([k, lbl]) => `<option value="${esc(k)}">${esc(lbl)}</option>`)
      .join("")
  );
}

export async function mount(root, ctx) {
  const taxonomy = ctx.taxonomy || {};
  const vocab = taxonomy.vocabularies || {};
  const state = { scene: "", season: "", limit: 3, useLlm: true };

  root.innerHTML = `
    <div class="view-head"><h1>搭配</h1><span class="faint" id="o-meta"></span></div>
    <div class="toolbar">
      <label>场合</label>
      <select id="o-scene">${optionsHtml(vocab.scene, "不限")}</select>
      <label>季节</label>
      <select id="o-season">${optionsHtml(vocab.season, "当前季节")}</select>
      <label>套数</label>
      <select id="o-limit">
        <option value="3" selected>3 套</option>
        <option value="5">5 套</option>
      </select>
      <label><input type="checkbox" id="o-llm" checked> 让 AI 写理由</label>
      <span class="spacer"></span>
      <button id="o-go" class="primary">生成搭配</button>
    </div>
    <div id="o-result">
      <div class="placeholder">点右上角「生成搭配」—— 只用你衣橱里<em>真实存在</em>的单品组合，
      不会凭空编。</div>
    </div>
    <div class="panel">
      <h2>最近生成</h2>
      <div id="o-history"><p class="muted">加载中…</p></div>
    </div>
  `;

  const resultEl = root.querySelector("#o-result");
  const historyEl = root.querySelector("#o-history");
  const metaEl = root.querySelector("#o-meta");
  const goBtn = root.querySelector("#o-go");

  root.querySelector("#o-scene").addEventListener("change", (e) => { state.scene = e.target.value; });
  root.querySelector("#o-season").addEventListener("change", (e) => { state.season = e.target.value; });
  root.querySelector("#o-limit").addEventListener("change", (e) => { state.limit = Number(e.target.value); });
  root.querySelector("#o-llm").addEventListener("change", (e) => { state.useLlm = e.target.checked; });

  goBtn.addEventListener("click", generate);
  resultEl.addEventListener("click", onFeedbackClick);

  function outfitCardHtml(outfit, index) {
    const pct = Math.round((outfit.confidence || 0) * 100);
    return `
      <article class="outfit" data-id="${esc(outfit.id)}">
        <div class="outfit-head">
          <span class="outfit-no">第 ${index + 1} 套</span>
          <span class="chip">置信度 ${pct}%</span>
        </div>
        <div class="chips">${itemChipsHtml(outfit.items, taxonomy)}</div>
        ${
          outfit.reason
            ? `<p class="outfit-reason">${esc(outfit.reason)}</p>`
            : `<p class="outfit-reason faint">（没让 AI 写理由）</p>`
        }
        <div class="form-actions">
          <button type="button" data-fb="like">${FB_TEXT.like}</button>
          <button type="button" data-fb="swap">${FB_TEXT.swap}</button>
          <button type="button" data-fb="dislike">${FB_TEXT.dislike}</button>
        </div>
      </article>`;
  }

  async function generate() {
    goBtn.disabled = true;
    goBtn.textContent = "生成中…";
    resultEl.innerHTML = `<p class="muted">正在从你的衣橱里挑……</p>`;
    try {
      const resp = await api.recommend({
        scene: state.scene,
        season: state.season,
        limit: state.limit,
        use_llm: state.useLlm ? "true" : "false",
      });
      renderResult(resp);
      await loadHistory();
    } catch (err) {
      renderError(resultEl, err);
    } finally {
      goBtn.disabled = false;
      goBtn.textContent = "生成搭配";
    }
  }

  function renderResult(resp) {
    metaEl.textContent = `季节：${resp.season_label || resp.season || "—"}${
      resp.degraded ? " · 本地排序" : ""
    }`;
    const outfits = resp.outfits || [];
    if (!outfits.length) {
      resultEl.innerHTML = `<div class="alert">${
        esc(resp.note || "没搭出组合")
      }<br>衣橱可能还没到最小可用量（至少要「一件上装 + 一件下装 + 一双鞋」）。
      <a href="#add">去录衣服 →</a></div>`;
      return;
    }
    resultEl.innerHTML =
      `<p class="faint" style="margin:0 0 10px">候选 ${esc(resp.candidates)} 套${
        resp.degraded ? "，AI 不可用，已退回本地排序" : ""
      }</p>` + outfits.map(outfitCardHtml).join("");
  }

  async function onFeedbackClick(ev) {
    const btn = ev.target.closest("[data-fb]");
    if (!btn) return;
    const card = btn.closest(".outfit");
    if (!card) return;
    const buttons = Array.from(card.querySelectorAll("[data-fb]"));
    buttons.forEach((b) => (b.disabled = true));
    try {
      const resp = await api.feedback({
        outfit_id: card.dataset.id,
        feedback_type: btn.dataset.fb,
      });
      const n = (resp.updated_tags || []).length;
      const word = FB_TEXT[btn.dataset.fb];
      if (resp.duplicate) {
        toast(`这条已经记过「${word}」了`, "info");
      } else {
        toast(`已记下「${word}」${n ? `，偏好记忆更新 ${n} 条` : ""}`, "ok");
      }
      buttons.forEach((b) => b.classList.toggle("chosen", b === btn));
      await loadHistory();
    } catch (err) {
      toast("反馈失败：" + err.message, "error");
    } finally {
      buttons.forEach((b) => (b.disabled = false));
    }
  }

  async function loadHistory() {
    try {
      const data = await api.listOutfits(20);
      const rows = data.outfits || [];
      if (!rows.length) {
        historyEl.innerHTML = `<p class="faint">还没有生成过搭配。</p>`;
        return;
      }
      historyEl.innerHTML = `<ul class="hist">${rows
        .map(
          (o) => `<li class="hist-row">
            <span class="faint">${esc(fmtDateTime(o.created_at))}</span>
            <span class="hist-items">${itemChipsHtml(o.items, taxonomy)}</span>
            <span class="chip">${esc(STATUS_TEXT[o.status] || o.status || "")}</span>
          </li>`
        )
        .join("")}</ul>`;
    } catch (err) {
      renderError(historyEl, err);
    }
  }

  await loadHistory();
}
