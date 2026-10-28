// §23：任务独立于识别；刷新后只查询同一任务，不重复发起扣积分请求。
import { api } from "../../core/api.js";
import { esc } from "../../core/ui.js";
import { newToken } from "./store.js";
import { layoutMessage } from "../../components/flatlay-display.js";

const tasks = new Map();
export const flatlayBusy = state => state?.state === "starting" || state?.state === "running";
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
export async function sourceHash(file) {
  const hash = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(hash)].map(v => v.toString(16).padStart(2, "0")).join("");
}

export function controls(state = {}, { disabled = false, configured = false, hasImage = false, needsApply = false } = {}) {
  const busy = flatlayBusy(state), locked = disabled || busy;
  return `<div class="flatlay-head"><strong>衣物平铺图</strong><span class="faint">使用 WorkBuddy 积分</span></div>
    <p class="faint" role="status">${esc(busy ? "正在生成平铺图，可收起后继续。" : state.error || (hasImage ? "衣橱与详情统一展示平铺图。" : "去除模特和购物文字，生成单独衣物图。"))}</p>
    <div class="flatlay-actions"><button type="button" class="ghost ai" data-flatlay="${needsApply && !busy ? "apply" : "generate"}" ${locked || (!needsApply && !configured) ? "disabled" : ""}>${busy ? "生成中…" : needsApply ? "保存此平铺图" : state.state === "waiting" ? "查看生成结果" : hasImage ? "重新生成平铺图" : "生成平铺图"}</button>
    ${state.state === "waiting" ? `<button type="button" class="ghost" data-flatlay="restart" ${locked ? "disabled" : ""}>重新生成（另计积分）</button>` : ""}
    ${hasImage ? `<button type="button" class="ghost" data-flatlay="full" ${locked ? "disabled" : ""}>${state.view === "full" ? "返回平铺展示" : "查看完整生成图"}</button>` : ""}
    ${!configured ? '<button type="button" class="ghost" data-flatlay="settings">连接电脑</button>' : ""}</div>
    ${hasImage ? `<small class="faint" data-flatlay-layout-note>${layoutMessage(state.view === "full" ? "full" : "pending")}</small>` : ""}`;
}

function fileFromResult(value) {
  if (typeof value !== "string" || value.length > 14 * 1024 * 1024) throw Error("生成结果格式或大小不支持");
  const match = /^data:(image\/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)$/.exec(value);
  if (!match) throw Error("生成结果不是支持的图片");
  const binary = atob(match[2]);
  if (!binary.length || binary.length > 10 * 1024 * 1024) throw Error("生成图片为空或超过10MB");
  const bytes = Uint8Array.from(binary, letter => letter.charCodeAt(0));
  return new File([bytes], "flatlay." + (match[1] === "image/jpeg" ? "jpg" : match[1].split("/")[1]), { type: match[1] });
}

export async function generate({ state, file, kind, changed, valid, completed, durable = () => true, canStart = () => true, resume = false }) {
  if (!resume) {
    if (flatlayBusy(state)) return;
    state.id = newToken(); state.state = "starting"; state.error = ""; state.code="";
    try { state.sourceHash = await sourceHash(file); await changed(); }
    catch (error) { state.state = "failed"; state.error = error.message || "源图未能读取，尚未开始生成。"; await changed(); return; }
    if (!durable()) { state.state = "failed"; state.error = "任务标识未能保存到本机，暂未开始生成。请保持应用打开后重试。"; await changed(); return; }
  }
  if (!state.id) return;
  if (tasks.has(state.id)) {
    await tasks.get(state.id);
    if (resume && valid() && ["starting", "running"].includes(state.state)) return generate({ state, file, kind, changed, valid, completed, durable, resume: true });
    return;
  }
  const id = state.id, hash = state.sourceHash;
  const current = () => valid() && state.id === id && state.sourceHash === hash;
  const task = (async () => {
    try {
      if (!current()) return;
      state.state = "running"; state.error = ""; await changed();
      if(!current())return;
      if(!resume&&(!durable()||!canStart())){state.state='failed';state.code='task_not_started';state.error='任务条件或保存状态已变化，本次尚未开始生成。';await changed();return;}
      let response = resume ? await api.flatlayStatus(id) : await api.startFlatlay(id, file, kind);
      const started = Date.now();
      while (current() && response.state === "running" && Date.now() - started < 240000) {
        await sleep(1300);
        if (!current()) return;
        response = await api.flatlayStatus(id);
      }
      if (!current()) return;
      if (response.source_hash !== hash || response.id !== id) throw Error("生成结果与当前裁剪来源不一致，请保留源图并重做");
      if (response.state === "completed") {
        const result = fileFromResult(response.image);
        state.state = "completed"; state.selected = false; state.view = "display"; state.error = "";
        state.credit = typeof response.credit === "number" && Number.isFinite(response.credit) ? response.credit : null;
        state.model = response.image_model;
        await completed(result); await changed();
      } else if (["failed", "expired"].includes(response.state)) {
        state.state = "failed"; state.code=response.error_code||"flatlay_failed"; state.error = response.error || "本次生成未成功，裁剪图和填写内容已保留。"; await changed();
      } else throw Error("结果仍未确认，点击查看生成结果不会重新扣一次生图积分。");
    } catch (error) {
      if (!current()) return;
      state.state = "waiting";state.code=error.code||error.payload?.detail?.code||"flatlay_unknown";
      state.error = `${error.message || "暂时无法确认生成结果。"} 源图和填写内容已保留；先查询原任务，重新生成会另计积分。`;
      await changed();
    } finally { tasks.delete(id); }
  })();
  tasks.set(id, task);
  return task;
}
