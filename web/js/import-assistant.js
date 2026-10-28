// §24：一键识别，无图种切换，平铺图就绪后确认入库。
import { esc, icon } from "./ui.js";
import { flatlayBusy } from "./flatlay.js";
import { layoutMessage } from "./flatlay-display.js";

export function controls(item, { busy = "", locked = false, chaining = false, queued = false, hasImage = false, vision = false, configured = false } = {}) {
  const state = item.flatlay || {}, flow = item.assistant || {};
  const recognizing = busy === "recognizing", working = recognizing || flatlayBusy(state) || chaining;
  const waiting = state.id && state.state === "waiting", failed = state.state === "failed";
  const needsType = flow.stage === "needs_type" && !item.fields.type;
  const invalid = hasImage && (state.sourceValid === false || (state.kind && state.kind !== item.fields.type));
  const reuse = hasImage && state.state === "completed" && !invalid;
  const generationOnly = failed || (item.recognized && item.error?.kind !== "recognition" && !reuse);
  const disabled = locked || working || Boolean(busy) || (waiting || generationOnly ? !configured : !vision);
  if(queued)return '<p class="import-assistant-message" role="status">等待自动准备，无需逐张点击。</p>';
  let html = reuse?`<p class="import-assistant-ready" role="status">${icon('check')}这件已准备好，确认后再入库。</p>`:"";
  if (working) html += `<div class="import-assistant-status" role="status"><i class="import-spinner" aria-hidden="true"></i><strong>${recognizing ? "正在识别衣物…" : "正在生成平铺图…"}</strong><small>可收起继续</small></div><div class="import-progress" aria-hidden="true"><i></i></div><p class="import-assistant-steps">${recognizing ? "识别属性 · 生成平铺图" : "属性已填入 · 正在生成平铺图"}</p>`;
  else if (!reuse || waiting || failed) {
    if (needsType) html += '<p class="import-assistant-message" role="status">请先确认品类，再继续生成平铺图。其他拿不准的属性可以留空。</p>';
    if (failed || waiting) html += `<p class="import-assistant-message" role="status">${esc(state.error || "照片和填写内容已保留，请重试生成平铺图。")}</p>`;
    if (flow.stage === "interrupted") html += '<p class="faint" role="status">上次识别中断，照片和填写内容已保留。</p>';
    if (invalid) html += '<p class="import-assistant-message">品类或照片来源有变化，请重新生成平铺图后保存。</p>';
    const label = waiting ? "查看生成结果" : needsType ? "选好品类后继续" : failed ? "仅重试生成平铺图" : invalid ? "重新生成平铺图" : generationOnly ? "继续生成平铺图" : item.error?.kind === "recognition" ? "重新识别" : "识别";
    html += `<button type="button" class="primary ai import-assistant-primary" ${invalid ? 'data-flatlay="restart"' : 'id="add-recognize"'} ${disabled || needsType ? "disabled" : ""}>${icon("spark")}${label}</button><p class="import-assistant-caption">${waiting ? "查询原任务，不会再次生图" : failed || invalid ? "可重新生成平铺图" : "识别衣物属性并生成平铺图"}</p>`;
    if (waiting) html += `<button type="button" class="ghost" data-flatlay="restart" ${locked || working ? "disabled" : ""}>重新生成（另计积分）</button>`;
  }
  if (hasImage) html += `<small class="faint" data-flatlay-layout-note>${layoutMessage("pending")}</small><small class="faint">AI 可能补全被遮挡细节，请对照实物确认。</small>`;
  if ((!vision && !generationOnly && !waiting) || !configured) html += '<button type="button" class="ghost" data-flatlay="settings">连接电脑</button>';
  return html;
}
