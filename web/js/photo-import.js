// §14：多选后逐件确认入库，草稿存本机，提交使用稳定标识。
import { api } from "./api.js";
import { flatlayBusy, sourceHash } from "./flatlay.js";
import { controls as assistantControls } from "./import-assistant.js";
import { setFlatlayImage } from "./flatlay-display.js";
import { fillForm, formHtml, readForm } from "./fields.js";
import { ImportQueue, batchProgress } from "./import-queue.js";
import { esc, toast, icon } from "./ui.js";
import { loadDraft, loadPhoto, saveDraft, flushDraft, newToken, storageAvailable, copyData as clone } from "./import-store.js";
let active = null;
export async function getImportProgress(){return batchProgress(active?.live?active.draft:await loadDraft());}
function announceProgress(draft){document.dispatchEvent(new CustomEvent('import:progress',{detail:batchProgress(draft)}));}
const remaining = draft => draft.items.filter(item => !["saved", "skipped"].includes(item.status));
const count = (draft, status) => draft.items.filter(item => item.status === status).length;
const emptyFields = () => ({ colors: [], style: [], season: [], scene: [] });
function recognitionError(error) {
  // §18：兼容升级前已经存入草稿的供应商402文案，不展示原始响应体。
  const text = error?.message || error?.text || "";
  const code = error?.code || error?.payload?.detail?.code || (/HTTP\s*402\b/i.test(text) ? "insufficient_balance" : "");
  const bridgeMessages = { bridge_offline: "连不上电脑，请检查电脑地址是否已变化、中转是否启动，以及手机与电脑是否同网。", bridge_login: "电脑个人CLI登录已失效，请在电脑重新登录。", bridge_pairing: "配对码不匹配，请打开「电脑识别设置」重新连接。", bridge_busy: "电脑正在处理其他识别，请稍后重试。", bridge_timeout: "电脑识别等待超时，请稍后重试。", bridge_config: "电脑连接配置需要重新设置。", bridge_unavailable: "电脑中转本次调用失败，请检查电脑个人CLI状态后重试。" };
  if (bridgeMessages[code]) return { kind: "recognition", code, text: `${bridgeMessages[code]}照片和已有属性已保留，也可以手动填写。` };
  if (code === "insufficient_balance") return { kind: "recognition", code, text: "AI识别服务余额或额度不足。照片和填写内容已保留，补充额度后点击「重新识别」。" };
  const messages = { request_timeout: "识别请求等待超时，请稍后重试。", request_network: "暂时连不上识别服务，请检查网络后重试。", model_timeout: "AI识别服务响应超时，请稍后重试。", model_network: "暂时连不上AI识别服务，请检查网络后重试。", model_auth: "AI识别服务鉴权失败，请检查模型配置。", model_rate_limit: "AI识别请求过于频繁，请稍后重试。", model_unavailable: "AI识别服务暂不可用，请稍后重试。" };
  return { kind: "recognition", code, text: `${messages[code] || (error?.status === 0 ? "识别请求未能连通或超时，请检查网络后重试。" : "这次AI识别未成功，请稍后重试。")}照片和已有属性已保留，也可以手动填写。` };
}
export function disposeImport() { active?.dispose(); }
export const importSaving = root => active?.root === root && (active.saving||active.switching);
export async function checkpointImport(root) {
  if (active?.root !== root) return true;
  if (active.saving) return false;
  active.capture(); await active.persist(); await flushDraft();
  if (!storageAvailable()) toast("进度暂未保存到本机，请保持应用打开。", "error", 6000);
  return true;
}
export function guardImportNavigation(event) { return active ? active.onHash(event) : true; }
export function refreshImportService() {
  // 只更新按钮可用性；保留正在编辑的 DOM、选区和额度暂停状态。
  if (!active?.live) return;
  active.updateFlatlay();
}

function dialog(title, text, onLeave, leaveText = "保留进度并离开", onClose = () => {}) {
  const layer = document.createElement("div");
  layer.className = "import-modal";
  layer.innerHTML = `<section role="dialog" aria-modal="true" aria-labelledby="import-dialog-title" class="import-sheet"><h2 id="import-dialog-title">${esc(title)}</h2><p>${esc(text)}</p><button type="button" class="primary" data-stay>继续录入</button><button type="button" data-leave>${esc(leaveText)}</button></section>`;
  document.body.append(layer);
  const before = document.activeElement;
  const close = () => { layer.remove(); document.removeEventListener("keydown", key); before?.focus?.(); onClose(); };
  const key = event => {
    if (event.key === "Escape") { event.preventDefault(); close(); }
    if (event.key === "Tab") {
      const buttons = [...layer.querySelectorAll("button")];
      if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons.at(-1).focus(); }
      else if (!event.shiftKey && document.activeElement === buttons.at(-1)) { event.preventDefault(); buttons[0].focus(); }
    }
  };
  document.addEventListener("keydown", key);
  layer.querySelector("[data-stay]").onclick = close;
  layer.querySelector("[data-leave]").onclick = async () => {
    layer.querySelectorAll("button").forEach(button => button.disabled = true);
    try { await onLeave(); close(); } catch (error) {
      layer.querySelector("p").textContent = error.message;
      layer.querySelectorAll("button").forEach(button => button.disabled = false);
    }
  };
  layer.querySelector("[data-stay]").focus();
  return close;
}
function photoFiles(files, capacity) {
  const accepted = [], rejected = [];
  for (const file of files) {
    if (accepted.length >= capacity) { rejected.push("已达到每批20张上限"); continue; }
    if (!file.size || file.size > 10 * 1024 * 1024) { rejected.push("空图片或超过10 MB"); continue; }
    if (!/^image\/(jpeg|png|webp|gif)$/.test(file.type) && !(file.type === "" && /\.(jpe?g|png|webp|gif)$/i.test(file.name))) {
      rejected.push("只支持JPEG、PNG、WebP和GIF"); continue;
    }
    accepted.push(file);
  }
  if (rejected.length) toast(`有${rejected.length}张未加入：${[...new Set(rejected)].join("；")}`, "error", 5000);
  return accepted;
}
function makeItem(file, fields = emptyFields()) {
  return { id: newToken(), sourceName: file.name, status: "queued", fields, ai: {}, rect: null, revision: 0, recognized: false, error: null, pending: null };
}
export function attachImport(root, ctx, { fileEl, onStart, onExit, initialFields, ready = () => Promise.resolve(), isSaving = () => false, onPreparing = () => {} }) {
  fileEl.multiple = true;
  let starting = false;
  const start = async files => {
    if (starting || isSaving() || !fileEl.isConnected || root.closest("[hidden]")) return;
    starting = true; onPreparing(true);
    try {
      await ready();
      const previous = await loadDraft();
      const reuse = previous && remaining(previous).length > 0;
      const draft = reuse ? previous : { version: 1, id: newToken(), position: 0, items: [] };
      const accepted = photoFiles(files, 20 - draft.items.length);
      if (!accepted.length || !fileEl.isConnected) return;
      const put = [];
      for (const [index, file] of accepted.entries()) {
        const fields = !reuse && index === 0 ? { ...emptyFields(), ...initialFields() } : emptyFields();
        const item = makeItem(file, fields); draft.items.push(item);
        put.push({ id: item.id, original: file, file });
      }
      draft.updatedAt = Date.now();
      await saveDraft(draft, { put, clear: !reuse });
      if (!fileEl.isConnected) return;
      onStart({ consumeManual: !reuse }); active = new ImportFlow(root, ctx, draft, onExit); await active.openCurrent(); await active.queue.restore(); await active.queue.authorize(put.map(photo=>photo.id));
    } catch (error) { if (fileEl.isConnected) toast(error.message || "照片未能加入，请重试", "error"); }
    finally { starting = false; onPreparing(false); }
  };
  fileEl.addEventListener("change", event => {
    if (!fileEl.files?.length || event.detail?.fromCrop) return;
    event.stopImmediatePropagation(); void start([...fileEl.files]);
  }, true);
  void loadDraft().then(draft => {
    if (!fileEl.isConnected || starting || !draft) return;
    if (!remaining(draft).length) { void saveDraft(null, { clear: true }).catch(() => {}); return; }
    const card = document.createElement("section"); card.className = "panel import-resume";
    card.innerHTML = `<div><strong>还有 ${remaining(draft).length} 张待录入</strong><p class="faint">已入库 ${count(draft, "saved")} 件，回来可以接着录</p></div><button type="button" class="primary" data-resume>继续上次录入 →</button><button type="button" class="ghost" data-discard>清除未录入进度</button>`;
    root.querySelector("#add-form").before(card);
    card.querySelector("[data-resume]").onclick = async () => {
      if (starting || isSaving()) return;
      starting = true; onPreparing(true);
      try {
        await ready();
        const latest = await loadDraft();
        if (!fileEl.isConnected || !latest || !remaining(latest).length) return;
        onStart(); active = new ImportFlow(root, ctx, latest, onExit); await active.openCurrent(); await active.queue.restore();
      } finally { starting = false; onPreparing(false); }
    };
    card.querySelector("[data-discard]").onclick = () => dialog("清除未录入进度？", "已经入库的衣物会保留；这批未录入的照片和填写内容将从本机草稿中清除。", async () => {
      await saveDraft(null, { clear: true }); card.remove(); toast("未录入进度已清除");
    }, "清除未录入进度");
  });
}
class ImportFlow {
  constructor(root, ctx, draft, onExit) {
    active?.dispose();
    Object.assign(this, { root, ctx, draft, onExit, live: true, busy: "", sequence: 0, photo: null, photoUrl: null, lastSkip: null, saving: false });
    if (!Object.prototype.hasOwnProperty.call(draft, "recognitionPaused")) draft.recognitionPaused = draft.items.some(item => item.error?.kind === "recognition" && recognitionError(item.error).code === "insufficient_balance") ? "insufficient_balance" : null;
    this.batchPhotos=new Map();this.photoFile=null;
    this.queue=new ImportQueue({draft,ctx,persist:options=>this.persist(options),error:recognitionError,
      capture:id=>{if(this.item?.id===id&&this.busy!=='loading')this.capture();},
      fields:(item,names)=>{
        if(this.item!==item)return;
        const form=this.root.querySelector('#add-form');if(form?.dataset.item!==item.id)return;
        const filtered=names.filter(name=>!item.edited?.[name]&&!(this.composing&&document.activeElement?.name===name));
        fillForm(form,item.fields,{names:filtered});
      },
      changed:(item,photo)=>this.refreshQueue(item,photo)});
    root.classList.add("import-view");
    this.onHash = event => {
      if (root.dataset.embedded === "true") return true;
      if (!this.live) return true;
      if (!remaining(this.draft).length) { void this.persist({ clear: true }); this.dispose(); return true; }
      event.stopImmediatePropagation(); history.replaceState(null, "", event.oldURL);
      if (document.body.classList.contains("crop-open")) { window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); return false; }
      if (this.saving) { toast("正在保存当前这件，请稍候"); return false; }
      this.pause(new URL(event.newURL).hash);
      return false;
    };
    this.onHide = () => { if (document.hidden) void this.persist(); };
    this.onPaste = event => {
      if (!this.live || this.done || (this.root.closest("[hidden]") || this.root.dataset.sheetActive === "false") || document.body.classList.contains("crop-open") || this.saving) return;
      const files = [...(event.clipboardData?.files || [])].filter(file => file.type.startsWith("image/"));
      if (files.length) { event.preventDefault(); void this.append(files); }
    };
    this.onDrag = event => { if ([...(event.dataTransfer?.types || [])].includes("Files")) event.preventDefault(); };
    this.onDrop = event => {
      if (!this.live || this.done || (this.root.closest("[hidden]") || this.root.dataset.sheetActive === "false") || this.saving || document.body.classList.contains("crop-open")) return;
      const files = [...(event.dataTransfer?.files || [])].filter(file => file.type.startsWith("image/"));
      if (files.length) { event.preventDefault(); void this.append(files); }
    };
    this.onCompositionStart = event => { this.composing = true; this.capture(event); };
    this.onCompositionEnd = event => {
      this.composing = false; this.capture(event);
      if (this.needsRender) { this.needsRender = false; this.render(); }
    };
    document.addEventListener("visibilitychange", this.onHide);
    document.addEventListener("paste", this.onPaste);
    root.addEventListener("dragover", this.onDrag); root.addEventListener("drop", this.onDrop);
    root.addEventListener("compositionstart", this.onCompositionStart);
    root.addEventListener("compositionend", this.onCompositionEnd);
  }
  get item() { return this.draft.items[this.draft.position]; }
  valid(id, sequence) { return this.live && this.item?.id === id && this.sequence === sequence && this.root.isConnected; }
  async persist(options) {
    this.draft.updatedAt = Date.now();
    let saved=true;
    try { await saveDraft(this.draft, options); } catch { saved=false; }
    this.storageNote();announceProgress(this.draft);
    return saved&&storageAvailable();
  }
  storageNote() {
    const node = this.root.querySelector("[data-import-storage]");
    if (node) {
      node.hidden = storageAvailable();
      node.textContent = "进度暂未能保存到本机。当前照片和内容仍在，请保持页面打开。";
    }
  }
  dispose() {
    this.live = false; this.sequence++;this.queue.dispose();
    for(const value of this.batchPhotos.values())if(value.url)URL.revokeObjectURL(value.url);this.batchPhotos.clear();
    clearTimeout(this.undoTimer);
    if (this.photoUrl) URL.revokeObjectURL(this.photoUrl);
    this.photoUrl = null;
    if (this.displayUrl) URL.revokeObjectURL(this.displayUrl); this.displayUrl = null;
    document.removeEventListener("visibilitychange", this.onHide);
    document.removeEventListener("paste", this.onPaste);
    this.root.removeEventListener("dragover", this.onDrag); this.root.removeEventListener("drop", this.onDrop);
    this.root.removeEventListener("compositionstart", this.onCompositionStart);
    this.root.removeEventListener("compositionend", this.onCompositionEnd);
    this.root.classList.remove("import-view", "import-has-undo"); this.closeDialog?.();
    if (active === this) active = null;
  }
  async openCurrent() {
    if (this.displayUrl) URL.revokeObjectURL(this.displayUrl); this.displayUrl = null; this.displayFile = null;
    if (!this.live) return;
    const next = remaining(this.draft);
    if (!next.length) { await this.finish(); return; }
    if (!this.item || ["saved", "skipped"].includes(this.item.status)) this.draft.position = this.draft.items.indexOf(next[0]);
    const item = this.item, id = item.id, sequence = ++this.sequence;
    this.photo = null; this.busy = "loading";
    if (this.photoUrl) URL.revokeObjectURL(this.photoUrl);
    this.photoUrl = null; this.render();
    if (item.pending) {
      try {
        const result = await api.photoImportStatus(id);
        if (!this.valid(id, sequence)) return;
        if (result.saved && result.item.display_image_path && !item.recovered) { await this.saved(result.item); return; }
        if (result.saved && !item.recovered) { item.recovered = result.item; item.pending = null; }
      } catch {}
      item.error = { kind: "save", text: "上次保存结果待确认，点击重试会核对同一件，不会重复创建。" };
    }
    try {
      const photo = await loadPhoto(id);
      if (!this.valid(id, sequence)) return;
      this.photo = photo; this.photoFile=photo.file; this.photoUrl = URL.createObjectURL(photo.file);
      if (photo.display && item.flatlay) {
        item.flatlay.sourceValid = item.flatlay.sourceHash === await sourceHash(photo.file);
        if (!this.valid(id, sequence)) return;
        item.flatlay.view = "display"; item.flatlay.selected = item.flatlay.sourceValid;
      }
      this.busy = ""; this.render();
    } catch (error) {
      if (!this.valid(id, sequence)) return;
      item.error = { kind: "image", text: error.message || "图片草稿暂不可用，可以重试或跳过这件" };
      this.busy = ""; this.render();
    }
  }
  capture(event) {
    const form = this.root.querySelector("#add-form");
    if (!form || !this.item || form.dataset.item!==this.item.id || this.saving || this.item.pending) return;
    this.item.fields = readForm(form);
    if (event?.target?.name) {
      const name = event.target.name;
      this.item.edited ||= {}; this.item.edited[name] = true;
      delete this.item.ai[name];
      if (this.item.conflicts?.[name]) {
        delete this.item.conflicts[name];
        const note = this.root.querySelector("[data-import-confirmation]");
        const reasons = Object.values(this.item.conflicts).filter(value => typeof value === "string" && value.trim());
        if (note) { note.hidden = !reasons.length; note.textContent = `以下属性需确认：${reasons.join("；")}`; }
      }
      if (name === "type") {
        const state = this.item.flatlay;
        if (state?.kind && state.kind !== this.item.fields.type) state.selected = false;
        this.updateFlatlay();
        if (this.item.assistant?.stage === "needs_type" && this.item.fields.type) void this.process({ generationOnly: true, automatic:true });
      }
    }
    void this.persist();
  }
  process(options={}) {
    if(!this.live||!this.photo||this.saving||this.busy||this.item.pending)return;
    this.capture();this.queue.request(this.item.id,options);
  }
  refreshQueue(item,photo){
    if(!this.live)return;
    if(item===this.item&&photo&&this.busy!=='loading'){
      this.photo=photo;
      if(this.photoFile!==photo.file){
        if(this.photoUrl)URL.revokeObjectURL(this.photoUrl);
        this.photoFile=photo.file;this.photoUrl=URL.createObjectURL(photo.file);
      }
    }
    this.updateBatch();this.updateFlatlay();announceProgress(this.draft);
  }
  async switchItem(id){
    if(this.saving||this.switching||this.busy||this.item?.pending||this.composing)return;
    const item=this.draft.items.find(i=>i.id===id);if(!item||['saved','skipped'].includes(item.status)||item===this.item)return;
    const original=this.item;this.switching=true;this.updateFlatlay();
    try{
      this.capture();if(!await this.persist()||!this.live||this.item!==original||this.saving||original.pending)return;
      this.capture();this.draft.position=this.draft.items.indexOf(item);await this.openCurrent();await this.persist();
    }finally{this.switching=false;this.updateFlatlay();if(this.item===item)this.root.querySelector('[data-batch-item="'+id+'"]')?.focus({preventScroll:true});}
  }
  updateBatch(){
    if(!this.live||!this.root.querySelector('[data-batch-count]'))return;
    const progress=batchProgress(this.draft);
    this.root.querySelector('[data-batch-count]').textContent=`已准备 ${progress.prepared} / ${progress.total} 件`;
    this.root.querySelector('[data-batch-saved]').textContent=`已入库 ${progress.saved} 件`;
    this.root.querySelector('[data-batch-track]').style.width=(progress.total?progress.prepared/progress.total*100:0)+'%';
    const paused=this.draft.automation?.paused,hasWaiting=this.draft.items.some(i=>!['saved','skipped'].includes(i.status)&&!i.error&&i.flatlay?.state!=='completed'&&!['interrupted','recognition_failed','needs_type'].includes(i.assistant?.stage));
    const notice=this.root.querySelector('[data-batch-note]');
    notice.hidden=!paused&&(this.queue.pumping||!hasWaiting);
    notice.querySelector('span').textContent=paused?.text||'照片和进度已保留，点击后继续自动准备尚未处理的衣物。';
    notice.querySelector('button').disabled=this.saving||Boolean(this.queue.pumping);
    const strip=this.root.querySelector('[data-batch-thumbs]');
    for(const [index,item]of this.draft.items.entries()){
      let button=[...strip.children].find(b=>b.dataset.batchItem===item.id);
      if(!button){button=document.createElement('button');button.type='button';button.className='import-batch-thumb';button.dataset.batchItem=item.id;button.innerHTML='<img alt="" loading="lazy"><i></i>';button.onclick=()=>void this.switchItem(item.id);strip.append(button);}
      const done=['saved','skipped'].includes(item.status),prepared=item.flatlay?.state==='completed'&&item.flatlay.kind===item.fields.type&&item.flatlay.revision===item.revision;
      button.disabled=done||this.saving||this.switching||Boolean(this.item?.pending);button.classList.toggle('is-current',item===this.item);button.classList.toggle('is-ready',prepared);button.classList.toggle('is-running',this.queue.job?.id===item.id);button.classList.toggle('is-saved',item.status==='saved');
      button.setAttribute('aria-pressed',String(item===this.item));button.setAttribute('aria-label',`第 ${index+1} 件${item.status==='saved'?'，已入库':item.status==='skipped'?'，已跳过':prepared?'，已准备好':''}`);
      if(!done)void loadPhoto(item.id).then(photo=>{
        if(!this.live||!button.isConnected)return;
        const file=photo.display||photo.file,previous=this.batchPhotos.get(item.id);
        if(previous?.file===file){button.querySelector('img').src=previous.url;return;}
        if(previous?.url)URL.revokeObjectURL(previous.url);
        const url=URL.createObjectURL(file);this.batchPhotos.set(item.id,{file,url});button.querySelector('img').src=url;
      }).catch(()=>{});
    }
  }
  displayReady() {
    const state = this.item?.flatlay;
    return Boolean(this.photo?.display && state && state.sourceValid !== false && (!state.kind || state.kind === this.item.fields.type) && (state.revision === undefined || state.revision === this.item.revision));
  }
  updateFlatlay() {
    if (!this.live || !this.item) return;
    const panel = this.root.querySelector("[data-flatlay-controls]");
    if (!panel) return;
    const state = this.item.flatlay || {}, generating = flatlayBusy(state), locked = this.saving || Boolean(this.item.pending);
    const preparing=this.queue.busy(this.item),phase=this.queue.job?.id===this.item.id?this.item.assistant?.stage:null;
    const focused = panel.contains(document.activeElement) ? document.activeElement.dataset.flatlay : null;
    panel.innerHTML = assistantControls(this.item, { busy: phase==="recognizing"?"recognizing":this.busy, locked, chaining: preparing, queued:!phase&&this.queue.pending(this.item), configured: Boolean(this.ctx.health?.flatlay_configured), vision: Boolean(this.ctx.health?.vision_ready), hasImage: Boolean(this.photo?.display) });
    panel.querySelector("#add-recognize")?.addEventListener("click", () => void this.process({ generationOnly: state.state === "failed" }));
    panel.querySelectorAll("[data-flatlay]").forEach(button => button.onclick = () => {
      const action = button.dataset.flatlay;
      if (action === "settings") { document.querySelector("[data-desktop-settings]")?.click(); return; }
      if (action === "restart") { void this.process({ generationOnly: true, restart: true }); return; }
      if (locked || generating || this.busy || this.assisting || !this.photo?.display) return;
    });
    if (this.displayFile !== this.photo?.display) {
      if (this.displayUrl) URL.revokeObjectURL(this.displayUrl);
      this.displayFile = this.photo?.display; this.displayUrl = this.displayFile ? URL.createObjectURL(this.displayFile) : null;
    }
    const picture = this.root.querySelector("[data-import-photo]"), image = picture?.querySelector("img");
    if (image) {
      const displayView = Boolean(this.displayUrl);
      const src = displayView ? this.displayUrl : this.photoUrl;
      void setFlatlayImage(image, src, displayView).then(result => {
        if (!this.live || this.item.flatlay !== state || !displayView || image.dataset.flatlaySource !== src) return;
        const note = panel.querySelector("[data-flatlay-layout-note]");
        if (note) { note.hidden = result.status !== "unsafe"; note.textContent = "请对照实物确认生成图中的衣物细节。"; }
      });
    }
    const hint = picture?.querySelector(".import-photo-hint");
    if (hint) hint.textContent = this.displayUrl ? "衣物平铺图" : "待处理照片";
    const working = preparing || generating || Boolean(this.busy);
    const save = this.root.querySelector("#add-submit"); if (save) save.disabled = working || this.switching || (!this.item.pending && !this.displayReady());
    const skip = this.root.querySelector("[data-skip]"); if (skip) skip.disabled = working || this.switching || locked;
    this.root.querySelectorAll("[data-undo]").forEach(button => button.disabled = working || this.switching || locked);
    this.root.querySelectorAll("[data-append]").forEach(button => button.disabled = this.switching || locked);
    const recheck = this.root.querySelector("[data-import-recheck]");
    if (recheck) { recheck.hidden = !this.photo?.display; recheck.querySelectorAll("button").forEach(button => button.disabled = working || locked); }
    const error = this.root.querySelector("[data-recognition-error]");
    if (error) { error.hidden = this.item.error?.kind !== "recognition"; error.textContent = this.item.error?.kind === "recognition" ? recognitionError(this.item.error).text : ""; }
    const overlay = picture?.querySelector(".import-loading");
    if (overlay) { overlay.hidden = !working; overlay.textContent = this.busy === "loading" ? "正在读取照片…" : phase === "recognizing" ? "正在识别这件衣服…" : "正在生成平铺图…"; }
    this.updateBatch();
    const confirmation=this.root.querySelector('[data-import-confirmation]');
    if(confirmation){const reasons=Object.values(this.item.conflicts||{}).filter(v=>typeof v==='string'&&v.trim());confirmation.hidden=!reasons.length;confirmation.textContent=reasons.length?`以下属性需确认：${reasons.join('；')}`:'';}
    if (focused) panel.querySelector(`[data-flatlay="${focused}"]`)?.focus({ preventScroll: true });
  }
  async save() {
    if (this.busy || this.switching || this.queue.busy(this.item) || !this.photo || this.saving || flatlayBusy(this.item?.flatlay)) return;
    this.capture();
    if (!this.item.pending && (!this.item.fields.type || !this.displayReady())) { toast("识别并生成平铺图后，再确认保存", "error"); return; }
    const item = this.item, id = item.id, sequence = ++this.sequence;
    this.saving = true; this.busy = "saving"; this.updateFlatlay();
    if (!item.pending) {
      const hash = await sourceHash(this.photo.file).catch(() => null);
      if (!this.valid(id, sequence) || hash !== item.flatlay?.sourceHash) { this.busy = ""; this.saving = false; this.updateFlatlay(); toast("平铺图来源已变化，请重新识别生成", "error"); return; }
      if (item.recovered && !hash.startsWith((item.recovered.image_hash || "").replace(/^sha256:/, ""))) {
        this.busy = ""; this.saving = false; item.error = { kind: "save", text: "恢复照片与已入库来源不同，请到已有衣物详情生成平铺图。", itemId: item.recovered.id }; this.render(); return;
      }
      item.pending = { payload: clone(item.fields), revision: item.revision, display: true };
    }
    if (item.pending.display && !this.photo.display) { this.busy = ""; this.saving = false; this.updateFlatlay(); toast("平铺图草稿暂不可用，请保留本次内容后重试", "error"); return; }
    this.saving = true; this.busy = "saving"; item.error = null;
    await this.persist(); if (!this.valid(id, sequence)) return;
    this.render();
    try {
      let result;
      if (item.recovered) {
        await api.savePresentation(item.recovered.id, this.photo.display, item.recovered.image_hash);
        result = { item: await api.updateItem(item.recovered.id, item.pending.payload) };
      } else result = await api.commitPhoto(id, this.photo.file, item.pending.payload, { original: this.photo.original || this.photo.file, display: item.pending.display ? this.photo.display : null });
      if (!this.valid(id, sequence)) return;
      if (!result?.item?.id) throw new Error("保存响应格式不正确，请重试核对");
      if (!result.item.display_image_path) {
        item.recovered = result.item; item.pending = null; item.error = { kind: "save", text: "此前提交已入库。生成平铺图后会更新同一件衣服，不会重复入库。" };
        this.busy = ""; this.saving = false; await this.persist(); this.render(); return;
      }
      await this.saved(result.item);
    } catch (error) {
      if (!this.valid(id, sequence)) return;
      const detail = error.payload?.detail;
      if (error.status >= 400 && error.status < 500) item.pending = null;
      item.error = { kind: "save", text: error.message || "保存结果暂未确认，请重试", itemId: detail?.item_id || item.recovered?.id || null };
      this.busy = ""; this.saving = false; await this.persist(); if (this.live) this.render();
    } finally {
      if (this.valid(id, sequence)) { this.saving = false; if (this.busy === "saving") this.busy = ""; }
    }
  }
  async saved(serverItem) {
    this.root.dataset.wardrobeDirty = "true";
    const item = this.item;
    item.status = "saved"; item.savedId = serverItem.id; item.fields = { ...item.fields, ...serverItem }; item.pending = null; item.error = null;
    this.busy = ""; this.saving = false; await this.persist({ remove: [item.id] });
    if (!this.live) return;
    toast(`${serverItem.name || serverItem.type_label || "这件衣服"}已入库`, "ok");
    const next = remaining(this.draft)[0];
    if (next) { this.draft.position = this.draft.items.indexOf(next); await this.openCurrent(); }
    else await this.finish();
  }
  async skip() {
    if (this.saving || this.switching || this.busy || this.queue.busy(this.item) || this.item.pending || flatlayBusy(this.item.flatlay)) return;
    this.capture(); ++this.sequence;
    this.item.status = "skipped"; this.lastSkip = this.item.id; this.busy = ""; await this.persist();
    clearTimeout(this.undoTimer);
    const skippedId = this.lastSkip;
    this.undoTimer = setTimeout(() => {
      if (this.lastSkip === skippedId) {
        this.lastSkip = null;
        this.root.classList.remove("import-has-undo");
        this.root.querySelector(".import-undo")?.remove();
        this.root.querySelector(".import-final-undo")?.remove();
        if (this.done) void this.persist({ clear: true });
      }
    }, 6000);
    if (!this.live) return;
    const next = remaining(this.draft)[0];
    if (next) { this.draft.position = this.draft.items.indexOf(next); await this.openCurrent(); }
    else await this.finish();
  }
  async undo() {
    if (this.saving || this.switching || this.busy || this.queue.busy(this.item) || flatlayBusy(this.item?.flatlay) || this.item?.pending || !this.lastSkip) return;
    this.capture(); const item = this.draft.items.find(item => item.id === this.lastSkip);
    if (!item || item.status !== "skipped") return;
    this.done = false; item.status = item.recognized ? "ready" : "queued";
    this.draft.position = this.draft.items.indexOf(item); this.lastSkip = null;
    await this.persist(); await this.openCurrent();
  }
  async append(files) {
    if (this.saving || this.switching || !this.live || this.done) return;
    this.capture(); const accepted = photoFiles(files, 20 - this.draft.items.length);
    if (!accepted.length) return;
    const put = [];
    for (const file of accepted) {
      const item = makeItem(file); this.draft.items.push(item); put.push({ id: item.id, file, original: file });
    }
    const saved=await this.persist({ put });
    if(saved)await this.queue.authorize(put.map(photo=>photo.id));
    if (this.live) { this.updateBatch(); toast(`已追加${accepted.length}张照片`); }
  }
  pause(hash = null) {
    if (this.saving || this.closeDialog) return;
    if (this.root.dataset.embedded === "true") { this.root.dispatchEvent(new Event("import:close")); return; }
    this.capture();
    const text = storageAvailable() ? `已入库的${count(this.draft, "saved")}件会保留。剩余${remaining(this.draft).length}张照片可下次接着录入。` : "当前进度暂时未能保存到本机，离开后只能在本次页面会话中继续。";
    this.closeDialog = dialog("稍后接着录？", text, async () => {
      await this.persist(); await flushDraft(); this.closeDialog = null; this.dispose();
      if (hash !== null) location.hash = hash; else await this.onExit();
    }, "保留进度并离开", () => this.closeDialog = null);
  }
  async finish() {
    this.root.classList.remove("import-has-undo");
    ++this.sequence; this.busy = ""; this.saving = false; this.done = true;
    // 结束页保留最近跳过项，撤销窗口内仍可回到它；到期再释放图片。
    await this.persist(this.lastSkip ? undefined : { clear: true });
    if (!this.live) return;
    if (this.photoUrl) URL.revokeObjectURL(this.photoUrl);
    this.photoUrl = null; this.photo = null;
    const saved = count(this.draft, "saved"), skipped = count(this.draft, "skipped");
    this.root.innerHTML = `<section class="panel import-done"><img src="/icons/logo.svg" alt="" width="72" height="72"><h1>${saved ? "这批录好了" : "这批处理完了"}</h1><p class="faint">${saved ? "新衣服已经收进你的衣橱" : "本次没有新增衣物"}</p><div class="import-counts"><div><strong>${saved}</strong><span>件已入库</span></div><div><strong>${skipped}</strong><span>张已跳过</span></div></div><p class="faint">跳过的图片没有入库</p>${this.lastSkip ? '<div class="import-final-undo"><button type="button" class="ghost" data-undo>撤销刚才的跳过</button></div>' : ''}<a class="btn primary" href="#wardrobe">去衣橱看看 →</a><button type="button" class="ghost" data-new>再选一批</button></section>`;
    this.root.querySelector("[data-undo]")?.addEventListener("click", () => void this.undo());
    this.root.querySelector("[data-new]").onclick = async () => { await this.persist({ clear: true }); this.dispose(); await this.onExit(); };
  }
  render() {
    if (!this.live) return;
    if (this.composing) { this.needsRender = true; return; }
    this.root.classList.toggle("import-has-undo", Boolean(this.lastSkip));
    const focused = this.root.contains(document.activeElement) && document.activeElement.name ? {
      name: document.activeElement.name, value: document.activeElement.value,
      start: document.activeElement.selectionStart, end: document.activeElement.selectionEnd,
    } : null;
    const item = this.item, busy = Boolean(this.busy), locked = this.saving || Boolean(item.pending);
    const last = remaining(this.draft).length === 1;
    const error = item.error?.kind === "recognition" ? recognitionError(item.error) : item.error;
    const conflict = Object.values(item.conflicts || {}).filter(value => typeof value === "string");
    this.root.innerHTML = `<section class="import-workspace">
      <header class="import-head"><div class="import-batch-top"><span data-batch-count></span><div class="import-batch-actions"><span data-batch-saved></span><button type="button" class="ghost" data-append aria-label="追加照片" title="追加照片">${icon("plus")}</button></div></div><div class="import-batch-track"><i data-batch-track></i></div><div class="import-batch-thumbs" data-batch-thumbs role="group" aria-label="本批衣物处理进度"></div><div class="import-position">第 ${this.draft.position + 1} / ${this.draft.items.length} 件</div>
      <div class="import-meta"><span>已入库 ${count(this.draft, "saved")} 件${count(this.draft, "skipped") ? ` · 跳过 ${count(this.draft, "skipped")} 张` : ""}</span><div><button type="button" class="ghost" data-pause aria-label="稍后继续" ${this.saving ? "disabled" : ""}>×</button></div></div></header>
      <div class="alert" data-import-storage hidden role="status"></div><div class="import-batch-notice" data-batch-note hidden><span></span><button type="button" class="ghost" data-batch-resume>继续自动准备</button></div>
      ${this.draft.recognitionPaused && error?.code !== "insufficient_balance" ? '<div class="alert" data-recognition-paused role="status">AI服务额度不足，可以先手动录入，补充额度后点击「识别」。</div>' : ""}
      <div class="alert" data-recognition-error role="status" hidden></div>
      ${error && error.kind !== "recognition" ? `<div class="alert ${error.kind === "save" ? "error" : ""}" role="status">${esc(error.text)}${error.itemId ? ` <a href="#item/${encodeURIComponent(error.itemId)}">查看已有单品</a>` : ""}${error.kind === "image" ? '<button type="button" data-reload>重试读取图片</button>' : ""}</div>` : ""}
      <div class="alert" data-import-confirmation role="status" ${conflict.length ? "" : "hidden"}>以下属性需确认：${conflict.map(esc).join("；")}</div>
      <form id="add-form" data-item="${esc(item.id)}" class="panel import-form" novalidate>
        <div class="import-photo" data-import-photo>${this.photoUrl ? `<img src="${esc(this.photoUrl)}" alt="当前待录入的衣物">` : '<span class="faint">正在读取照片…</span>'}<span class="import-loading" role="status" hidden></span><span class="import-photo-hint">待处理照片</span></div>
        <section class="import-assistant" data-flatlay-controls></section>
        ${formHtml(this.ctx.taxonomy, { compact: true })}
        <div class="import-actions"><button type="button" class="ghost" data-skip ${this.saving || item.pending ? "disabled" : ""}>跳过这件</button><button type="submit" class="primary" id="add-submit" ${busy || !this.photo ? "disabled" : ""}>${this.saving ? "保存中…" : error?.kind === "save" ? last ? "重试并结束" : "重试并下一件" : last ? "保存并结束" : "保存并下一件"}</button></div>
      </form>
      ${this.lastSkip ? '<div class="import-undo" role="status"><span>已跳过上一件</span><button type="button" class="ghost" data-undo>撤销</button></div>' : ""}
      <input type="file" accept="image/*" multiple class="sr-only" tabindex="-1" id="import-append">
    </section>`;
    const form = this.root.querySelector("#add-form");
    fillForm(form, item.fields);
    if (locked||this.busy==="loading") form.querySelectorAll("input,select,textarea").forEach(node => node.disabled = true);
    const details = form.querySelector(".more-fields"); details.open = Boolean(item.more);
    details.insertAdjacentHTML("beforeend", '<div data-import-recheck hidden><button type="button" class="ghost ai" data-reidentify>重新识别属性</button><button type="button" class="ghost" data-regenerate>重新生成平铺图（另计积分）</button></div>');
    details.querySelector("[data-reidentify]").onclick = () => void this.process({ reidentify: true });
    details.querySelector("[data-regenerate]").onclick = () => void this.process({ generationOnly: true, restart: true });
    details.addEventListener("toggle", () => { item.more = details.open; void this.persist(); });
    form.addEventListener("input", event => this.capture(event)); form.addEventListener("change", event => this.capture(event));
    form.addEventListener("submit", event => { event.preventDefault(); void this.save(); });
    this.root.querySelector("[data-batch-resume]").onclick=()=>void this.queue.resume();
    this.root.querySelector("[data-skip]").onclick = () => void this.skip();
    this.root.querySelector("[data-pause]").onclick = () => this.pause();
    const input = this.root.querySelector("#import-append");
    this.root.querySelector("[data-append]").onclick = () => input.click();
    input.onchange = () => { if (input.files?.length) void this.append([...input.files]); };
    this.root.querySelector("[data-undo]")?.addEventListener("click", () => void this.undo());
    this.root.querySelector("[data-reload]")?.addEventListener("click", () => void this.openCurrent({ auto: false }));
    this.updateBatch();this.updateFlatlay();
    this.storageNote();
    if (focused) {
      const target = [...form.elements].find(node => node.name === focused.name && (node.type !== "checkbox" || node.value === focused.value));
      if (target && !target.disabled) {
        target.focus({ preventScroll: true });
        if (focused.start !== null && typeof target.setSelectionRange === "function") target.setSelectionRange(focused.start, focused.end);
      }
    }
  }
}
