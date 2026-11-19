// §14：多选后逐件确认入库，草稿存本机，提交使用稳定标识。
import { api } from "../../core/api.js";
import { flatlayBusy, flatlayPreparing, sourceHash } from "./flatlay.js";
import { controls as assistantControls } from "./assistant.js";
import { setFlatlayImage } from "../../components/flatlay-display.js";
import { fillForm, readForm, reviewFieldsHtml } from "../../core/fields.js";
import { garmentProfileHtml } from "../../components/garment-profile.js";
import { ImportQueue, batchProgress } from "./queue.js";
import { esc, toast, icon } from "../../core/ui.js";
import { loadDraft, loadPhoto, saveDraft, flushDraft, loadPhotoSelection, savePhotoSelection, newToken, storageAvailable, copyData as clone } from "./store.js";
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
  if (bridgeMessages[code]) return { kind: "recognition", code, text: `${bridgeMessages[code]}照片和已有属性已保留，可以重试或跳过这件。` };
  if (code === "insufficient_balance") return { kind: "recognition", code, text: "AI识别服务余额或额度不足。照片和填写内容已保留，补充额度后点击「重新识别」。" };
  const messages = { request_timeout: "识别请求等待超时，请稍后重试。", request_network: "暂时连不上识别服务，请检查网络后重试。", model_timeout: "AI识别服务响应超时，请稍后重试。", model_network: "暂时连不上AI识别服务，请检查网络后重试。", model_auth: "AI识别服务鉴权失败，请检查模型配置。", model_rate_limit: "AI识别请求过于频繁，请稍后重试。", model_unavailable: "AI识别服务暂不可用，请稍后重试。" };
  return { kind: "recognition", code, text: `${messages[code] || (error?.status === 0 ? "识别请求未能连通或超时，请检查网络后重试。" : "这次AI识别未成功，请稍后重试。")}照片和已有属性已保留，可以重试或跳过这件。` };
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
  const form = root.querySelector("#add-form");
  const source = form.querySelector("[data-import-source]");
  const selection = form.querySelector("[data-import-selection]");
  const camera = form.querySelector("#add-camera-image");
  const thumbs = selection.querySelector("[data-selection-thumbs]");
  const preview = selection.querySelector("[data-selection-preview]");
  const startButton = selection.querySelector("[data-start-recognition]");
  const inputs = [...form.querySelectorAll("[data-take-photo],[data-pick-photos]")];
  let staged = [], position = 0, disposed = false;
  let starting = false;
  const live = () => !disposed && fileEl.isConnected;
  const available = () => live() && !starting && !isSaving() && !root.closest("[hidden]") && root.dataset.sheetActive !== "false";

  function renderSelection() {
    const current = staged[position];
    source.hidden = Boolean(current);
    selection.hidden = !current;
    form.classList.toggle("has-import-selection", Boolean(current));
    if (!current) {
      thumbs.replaceChildren();
      preview.removeAttribute("src");
      return;
    }
    selection.querySelector("[data-selection-count]").textContent = `已选 ${staged.length} 张`;
    selection.querySelector("[data-selection-position]").textContent = `第 ${position + 1} / ${staged.length} 张`;
    startButton.textContent = `开始识别 · ${staged.length} 张`;
    preview.src = current.url;
    preview.alt = `待识别照片第 ${position + 1} 张`;
    const focused = thumbs.contains(document.activeElement);
    thumbs.replaceChildren(...staged.map(({ url }, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "import-batch-thumb";
      button.classList.toggle("is-current", position === index);
      button.setAttribute("aria-label", `查看第 ${index + 1} 张照片`);
      button.setAttribute("aria-pressed", String(position === index));
      const img = document.createElement("img");
      img.src = url;
      img.alt = "";
      button.append(img);
      button.onclick = () => { position = index; renderSelection(); };
      return button;
    }));
    if (focused) thumbs.children[position].focus({ preventScroll: true });
  }

  const toPhoto = file => ({ file, url: URL.createObjectURL(file) });
  const selectionReady = loadPhotoSelection().then(files => {
    if (!live()) return;
    staged = files.map(toPhoto);
    renderSelection();
  });
  function persistSelection() {
    void savePhotoSelection(staged.map(({ file }) => file)).catch(() => {
      if (live()) toast("照片暂未保存到本机，请保持应用打开", "error");
    });
  }
  async function stage(files) {
    await selectionReady;
    if (!available()) return;
    const accepted = photoFiles(files, 20 - staged.length);
    if (!accepted.length) return;
    staged.push(...accepted.map(toPhoto));
    position = staged.length - 1;
    renderSelection();
    persistSelection();
    thumbs.children[position].focus({ preventScroll: true });
  }
  for (const button of inputs) button.onclick = () => {
    if (!available()) return;
    if (staged.length >= 20) { toast("已达到每批20张上限"); return; }
    (button.hasAttribute("data-take-photo") ? camera : fileEl).click();
  };
  camera.onchange = () => {
    const files = [...camera.files];
    camera.value = "";
    if (files.length) void stage(files);
  };
  selection.querySelector("[data-remove-photo]").onclick = () => {
    if (!available() || !staged.length) return;
    const [removed] = staged.splice(position, 1);
    URL.revokeObjectURL(removed.url);
    position = Math.max(0, Math.min(position, staged.length - 1));
    renderSelection();
    persistSelection();
    if (!staged.length) source.querySelector("[data-take-photo]").focus({ preventScroll: true });
  };
  startButton.onclick = async () => {
    if (!available() || !staged.length) return;
    starting = true; onPreparing(true);
    try {
      await ready();
      const previous = await loadDraft();
      const reuse = previous && remaining(previous).length > 0;
      const draft = reuse ? previous : { version: 1, id: newToken(), position: 0, items: [] };
      if (!live()) return;
      if (staged.length > 20 - draft.items.length) {
        toast("本批超过20张，请先继续上次录入或移除部分新照片", "error");
        return;
      }
      const put = [];
      for (const [index, { file }] of staged.entries()) {
        const fields = !reuse && index === 0 ? { ...emptyFields(), ...initialFields() } : emptyFields();
        const item = makeItem(file, fields); draft.items.push(item);
        put.push({ id: item.id, original: file, file });
      }
      draft.updatedAt = Date.now();
      await saveDraft(draft, { put, clear: !reuse, consumeSelection: true });
      if (!live()) return;
      onStart({ consumeManual: !reuse }); active = new ImportFlow(root, ctx, draft, onExit); await active.openCurrent(); await active.queue.restore(); await active.queue.authorize(put.map(photo=>photo.id));
    } catch (error) { if (fileEl.isConnected) toast(error.message || "照片未能加入，请重试", "error"); }
    finally { starting = false; onPreparing(false); }
  };
  fileEl.addEventListener("change", event => {
    if (!fileEl.files?.length || event.detail?.fromCrop) return;
    event.stopImmediatePropagation();
    const files = [...fileEl.files];
    fileEl.value = "";
    void stage(files);
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
  return () => {
    disposed = true;
    for (const { url } of staged) URL.revokeObjectURL(url);
    staged = [];
  };
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
    this.reviewOpen = false; this.batchOpen = false;
    this.photo = null; this.busy = "loading";
    if (this.photoUrl) URL.revokeObjectURL(this.photoUrl);
    this.photoUrl = null; this.render();
    if (item.pending) {
      try {
        const result = await api.photoImportStatus(id);
        if (!this.valid(id, sequence)) return;
        // §66：saved 表示这件已经入库了，有没有平铺图都不改变这个事实。
        if (result.saved && !item.recovered) { await this.saved(result.item); return; }
      } catch {}
      item.error = { kind: "save", text: "上次保存结果待确认，点击重试会核对同一件，不会重复创建。" };
    }
    try {
      const photo = await loadPhoto(id);
      if (!this.valid(id, sequence)) return;
      this.photo = photo; this.photoFile = photo.original || photo.file;
      this.photoUrl = URL.createObjectURL(this.photoFile);
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
    if (this.reviewOpen) this.item.fields = { ...this.item.fields, ...readForm(form, this.item.fields) };
    if (event?.target?.name) {
      const name = event.target.name;
      this.item.edited ||= {}; this.item.edited[name] = true;
      delete this.item.ai?.[name];
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
      } else if (event.type === "change") this.updateProfile();
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
      const original = photo.original || photo.file;
      if(this.photoFile!==original){
        if(this.photoUrl)URL.revokeObjectURL(this.photoUrl);
        this.photoFile=original;this.photoUrl=URL.createObjectURL(original);
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
    if (strip.hidden) return;
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
  /* §66：平铺图不是入库前提。已经认出品类 + 原图在本地，就可以直接用这张照片入库。
     生图额度、电脑离线、队列暂停都不该把整件锁死 —— 那会让用户只剩「跳过」一条路。 */
  attrReady() {
    return Boolean(this.photo && this.item?.fields.type);
  }
  canSave() {
    return Boolean(this.item?.pending) || this.displayReady() || this.attrReady();
  }
  flipReview(open) {
    if (this.saving || this.switching || this.composing) return;
    if (!open) this.capture();
    this.reviewOpen = open;
    this.updateFlatlay();
    this.root.querySelector(this.reviewOpen ? "[data-review-close]" : "[data-review-open]:not([hidden]),[data-review-type]:not([hidden])")?.focus({ preventScroll: true });
  }
  updateFlatlay() {
    if (!this.live || !this.item) return;
    const panel = this.root.querySelector("[data-flatlay-controls]");
    if (!panel) return;
    const state = this.item.flatlay || {}, generating = flatlayBusy(state), locked = this.saving || Boolean(this.item.pending);
    const preparing=this.queue.busy(this.item),phase=this.queue.job?.id===this.item.id?this.item.assistant?.stage:null;
    const processing = preparing || generating || this.busy === "loading";
    const working = processing || this.saving;
    const needsType = this.item.assistant?.stage === "needs_type" && !this.item.fields.type;
    const ready = this.displayReady();
    const canReview = Boolean(this.photo && (this.photo.display || this.item.pending || needsType || this.item.fields.type));
    if (processing || !canReview) this.reviewOpen = false;
    const review = this.root.querySelector("[data-import-review]");
    review.dataset.open = String(Boolean(this.reviewOpen));
    for (const [face, inactive] of [["[data-review-front]", Boolean(this.reviewOpen)], ["[data-review-back]", !this.reviewOpen]]) {
      const node = review.querySelector(face);
      node.inert = inactive;
      node.setAttribute("aria-hidden", String(inactive));
    }
    const open = review.querySelector("[data-review-open]");
    open.hidden = processing || !canReview || (needsType && !this.photo?.display);
    open.disabled = this.switching || this.saving;
    open.setAttribute("aria-expanded", String(Boolean(this.reviewOpen)));
    review.querySelector("[data-review-close]").disabled = this.saving || this.switching;
    review.querySelector("[data-review-type]").hidden = processing || !this.photo || !needsType || Boolean(this.photo.display);
    review.querySelector("[data-review-skip]").hidden = working || canReview || locked;
    const regenerate = review.querySelector("[data-review-regenerate]");
    regenerate.hidden = !this.photo?.display || ready || locked;
    regenerate.disabled = working || this.switching || !this.item.fields.type;
    review.querySelectorAll(".import-review-fields input,.import-review-fields select,.import-review-fields button").forEach(node => {
      node.disabled = locked || working || this.switching || !this.reviewOpen;
    });
    const batchToggle = this.root.querySelector("[data-batch-toggle]");
    if (processing) this.batchOpen = false;
    batchToggle.disabled = processing || locked || this.switching || this.draft.items.length < 2;
    batchToggle.setAttribute("aria-expanded", String(Boolean(this.batchOpen)));
    this.root.querySelector("[data-batch-thumbs]").hidden = !this.batchOpen;
    const focusInside = panel.contains(document.activeElement), focused = focusInside ? document.activeElement.dataset.flatlay : null;
    panel.hidden = processing;
    panel.innerHTML = assistantControls(this.item, { busy: phase==="recognizing"?"recognizing":this.busy, locked, chaining: preparing, queued:!phase&&this.queue.pending(this.item), configured: Boolean(this.ctx.health?.flatlay_configured), vision: Boolean(this.ctx.health?.vision_ready), hasImage: Boolean(this.photo?.display), canSave: this.canSave() });
    panel.querySelector("#add-recognize")?.addEventListener("click", () => {
      const generationOnly = state.state === "failed" || this.item.assistant?.stage === "needs_type";
      void this.process(needsType ? { reidentify: true } : { generationOnly });
    });
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
      const displayView = Boolean(this.displayUrl && !processing);
      const src = displayView ? this.displayUrl : this.photoUrl;
      image.hidden = !src;
      image.alt = displayView ? "当前衣物的白底平铺图" : "当前衣物的原照片";
      const placeholder = picture.querySelector(".import-photo-placeholder");
      placeholder.hidden = Boolean(src);
      placeholder.querySelector("span").textContent = this.busy === "loading" ? "正在读取照片" : "照片暂不可用";
      if (src) void setFlatlayImage(image, src, displayView).then(result => {
        if (!this.live || this.item.flatlay !== state || !displayView || image.dataset.flatlaySource !== src) return;
        const note = panel.querySelector("[data-flatlay-layout-note]");
        if (note) { note.hidden = result.status !== "unsafe"; note.textContent = "请对照实物确认生成图中的衣物细节。"; }
      });
    }
    const hint = picture?.querySelector(".import-photo-hint");
    if (hint) hint.hidden = !this.displayUrl || processing;
    const save = this.root.querySelector("#add-submit"); if (save) save.disabled = !this.reviewOpen || working || this.switching || (!this.item.pending && !this.canSave());
    this.root.querySelectorAll("[data-skip],[data-review-skip]").forEach(button => button.disabled = working || this.switching || locked);
    this.root.querySelectorAll("[data-undo]").forEach(button => button.disabled = working || this.switching || locked);
    this.root.querySelectorAll("[data-append]").forEach(button => button.disabled = this.switching || locked);
    const recheck = this.root.querySelector("[data-import-recheck]");
    if (recheck) { recheck.hidden = !this.photo?.display; recheck.querySelectorAll("button").forEach(button => button.disabled = working || locked); }
    const error = this.root.querySelector("[data-recognition-error]");
    if (error) { error.hidden = this.item.error?.kind !== "recognition"; error.textContent = this.item.error?.kind === "recognition" ? recognitionError(this.item.error).text : ""; }
    const overlay = picture?.querySelector(".import-loading");
    if (overlay) {
      overlay.hidden = !processing;
      overlay.textContent = this.busy === "loading" ? "正在读取照片…" : phase === "recognizing" ? "正在识别这件衣服…" : flatlayPreparing(state) ? "正在准备照片…" : "正在生成平铺图…";
    }
    this.updateBatch();
    this.updateProfile();
    const confirmation=this.root.querySelector('[data-import-confirmation]');
    if(confirmation){const reasons=Object.values(this.item.conflicts||{}).filter(v=>typeof v==='string'&&v.trim());confirmation.hidden=!reasons.length;confirmation.textContent=reasons.length?`以下属性需确认：${reasons.join('；')}`:'';}
    if(focusInside&&!panel.hidden&&!this.reviewOpen){const target=(focused&&panel.querySelector(`[data-flatlay="${focused}"]`))||panel.querySelector("#add-recognize:not(:disabled)");panel.tabIndex=-1;(target||panel).focus({preventScroll:true});}
  }
  updateProfile(){
    if(!this.live||!this.item)return;
    const container=this.root.querySelector('[data-garment-info]'),form=this.root.querySelector('#add-form');
    if(!container||form?.dataset.item!==this.item.id)return;
    const signature=JSON.stringify(this.item.fields);
    if(this.profileRoot===container&&this.profileSignature===signature)return;
    const more=container.querySelector('[data-garment-more]'),opened=more?.open??Boolean(this.item.more);
    const focus=container.contains(document.activeElement)?document.activeElement.closest('summary')?'summary':document.activeElement.dataset.reidentify!==undefined?'[data-reidentify]':null:null;
    const scroller=this.root.closest('.wardrobe-import-scroll'),top=scroller?.scrollTop;
    container.innerHTML=garmentProfileHtml(this.item.fields,this.ctx.taxonomy,{open:opened});
    this.profileRoot=container;this.profileSignature=signature;
    const details=container.querySelector('[data-garment-more]'),recheck=details.querySelector('[data-import-recheck]');
    recheck.innerHTML='<button type="button" class="ghost ai" data-reidentify>重新识别属性</button><button type="button" class="ghost" data-regenerate>重新生成平铺图</button>';
    recheck.hidden=!this.photo?.display;
    recheck.querySelector('[data-reidentify]').onclick=()=>void this.process({reidentify:true});
    recheck.querySelector('[data-regenerate]').onclick=()=>void this.process({generationOnly:true,restart:true});
    recheck.querySelectorAll('button').forEach(button=>button.disabled=Boolean(this.saving||this.item.pending||this.queue.busy(this.item)||this.busy));
    const item=this.item;
    details.addEventListener('toggle',()=>{if(this.item!==item||!details.isConnected)return;item.more=details.open;void this.persist();});
    if(top!==undefined)scroller.scrollTop=top;
    if(focus)container.querySelector(focus)?.focus({preventScroll:true});
  }
  async save() {
    if (!this.reviewOpen || this.busy || this.switching || this.queue.busy(this.item) || !this.photo || this.saving || flatlayBusy(this.item?.flatlay)) return;
    this.capture();
    if (!this.item.pending && (!this.item.fields.type || !this.canSave())) { toast("先确认品类，或等平铺图出来再保存", "error"); return; }
    const item = this.item, id = item.id, sequence = ++this.sequence;
    this.saving = true; this.busy = "saving"; this.updateFlatlay();
    if (!item.pending) {
      const hash = await sourceHash(this.photo.file).catch(() => null);
      if (!this.valid(id, sequence)) return;
      // §66：只有「带平铺图入库」才要求平铺图确实来自当前照片。没有平铺图时没有可比对象，
      // 不能拿照片指纹当入库前提 —— 否则额度耗尽、电脑离线就永远存不进去。
      if (this.displayReady() && hash !== item.flatlay?.sourceHash) { this.busy = ""; this.saving = false; this.updateFlatlay(); toast("平铺图来源已变化，请重新识别生成", "error"); return; }
      if (item.recovered && hash && !hash.startsWith((item.recovered.image_hash || "").replace(/^sha256:/, ""))) {
        this.busy = ""; this.saving = false; item.error = { kind: "save", text: "恢复照片与已入库来源不同，请到已有衣物详情生成平铺图。", itemId: item.recovered.id }; this.render(); return;
      }
      item.pending = { payload: clone(item.fields), revision: item.revision, display: Boolean(this.photo.display) };
    }
    if (item.pending.display && !this.photo.display) { this.busy = ""; this.saving = false; this.updateFlatlay(); toast("平铺图草稿暂不可用，请保留本次内容后重试", "error"); return; }
    this.saving = true; this.busy = "saving"; item.error = null;
    await this.persist(); if (!this.valid(id, sequence)) return;
    this.render();
    try {
      let result;
      if (item.recovered) {
        if (item.pending.display && this.photo.display) await api.savePresentation(item.recovered.id, this.photo.display, item.recovered.image_hash);
        result = { item: await api.updateItem(item.recovered.id, item.pending.payload), replayed: true };
      } else result = await api.commitPhoto(id, this.photo.file, item.pending.payload, { original: this.photo.original || this.photo.file, display: item.pending.display ? this.photo.display : null });
      if (!this.valid(id, sequence)) return;
      if (!result?.item?.id) throw new Error("保存响应格式不正确，请重试核对");
      // §66：display_image_path 为空现在是合法终态（没有平铺图也能入库）。
      // 以前拿它当「这件早就入库了」的信号，会把刚保存成功的单品判成失败；
      // replay 与否只看后端 replayed，两种都算保存成功。
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
      <header class="import-head"><div class="import-batch-top"><button type="button" class="ghost import-batch-toggle" data-batch-toggle aria-expanded="false" aria-controls="import-batch-photos"><span data-batch-count></span>${icon("chevron")}</button><div class="import-batch-actions"><span data-batch-saved></span><button type="button" class="ghost" data-append aria-label="追加照片" title="追加照片">${icon("plus")}</button></div></div><div class="import-batch-track"><i data-batch-track></i></div><div id="import-batch-photos" class="import-batch-thumbs" data-batch-thumbs hidden role="group" aria-label="本批衣物处理进度"></div><div class="import-position">第 ${this.draft.position + 1} / ${this.draft.items.length} 件</div>
      <div class="import-meta"><span>已入库 ${count(this.draft, "saved")} 件${count(this.draft, "skipped") ? ` · 跳过 ${count(this.draft, "skipped")} 张` : ""}</span><div><button type="button" class="ghost" data-pause aria-label="稍后继续" ${this.saving ? "disabled" : ""}>×</button></div></div></header>
      <div class="alert" data-import-storage hidden role="status"></div><div class="import-batch-notice" data-batch-note hidden><span></span><button type="button" class="ghost" data-batch-resume>继续自动准备</button></div>
      ${this.draft.recognitionPaused && error?.code !== "insufficient_balance" ? '<div class="alert" data-recognition-paused role="status">AI服务额度不足，可以先手动录入，补充额度后点击「识别」。</div>' : ""}
      <div class="alert" data-recognition-error role="status" hidden></div>
      ${error && error.kind !== "recognition" ? `<div class="alert ${error.kind === "save" ? "error" : ""}" role="status">${esc(error.text)}${error.itemId ? ` <a href="#item/${encodeURIComponent(error.itemId)}">查看已有单品</a>` : ""}${error.kind === "image" ? '<button type="button" data-reload>重试读取图片</button>' : ""}</div>` : ""}
      <form id="add-form" data-item="${esc(item.id)}" class="panel import-form" novalidate>
        <div class="import-review" data-import-review data-open="false">
          <div class="import-review-stack">
            <section class="import-review-face import-review-front" data-review-front aria-label="当前衣物照片">
              <div class="import-photo" data-import-photo>
                <img alt="" hidden><div class="import-photo-placeholder">${icon("wardrobe")}<span>正在读取照片</span></div>
                <span class="import-loading" role="status" hidden></span><span class="import-photo-hint" hidden>衣物平铺图</span>
              </div>
              <button type="button" class="ghost import-review-corner" data-review-open aria-controls="import-review-info" aria-expanded="false" aria-label="查看衣物信息" title="查看衣物信息" hidden>${icon("clipboard")}</button>
              <section class="import-assistant" data-flatlay-controls></section>
              <button type="button" class="ghost import-review-recovery" data-review-type hidden>填写品类</button>
              <button type="button" class="ghost import-review-recovery" data-review-skip hidden>跳过这件</button>
            </section>
            <section id="import-review-info" class="import-review-face import-review-back" data-review-back aria-labelledby="import-review-title" aria-hidden="true" inert>
              <header><h3 id="import-review-title">衣物信息</h3><p class="faint">确认属性后即可入库</p></header>
              <button type="button" class="ghost import-review-corner" data-review-close aria-label="翻回衣物照片" title="翻回衣物照片">${icon("replay")}</button>
              <div class="import-review-content">
                <div class="alert" data-import-confirmation role="status" ${conflict.length ? "" : "hidden"}>以下属性需确认：${conflict.map(esc).join("；")}</div>
                ${reviewFieldsHtml(this.ctx.taxonomy)}
                <button type="button" class="ghost import-review-recovery" data-review-regenerate hidden>重新生成平铺图</button>
                <div data-garment-info></div>
              </div>
              <div class="import-actions"><button type="button" class="ghost" data-skip>跳过这件</button><button type="submit" class="primary" id="add-submit" ${busy || !this.photo ? "disabled" : ""}>${this.saving ? "保存中…" : error?.kind === "save" ? last ? "重试并结束" : "重试并下一件" : last ? "保存并结束" : "保存并下一件"}</button></div>
            </section>
          </div>
        </div>
      </form>
      ${this.lastSkip ? '<div class="import-undo" role="status"><span>已跳过上一件</span><button type="button" class="ghost" data-undo>撤销</button></div>' : ""}
      <input type="file" accept="image/*" multiple class="sr-only" tabindex="-1" id="import-append">
    </section>`;
    const form = this.root.querySelector("#add-form");
    fillForm(form, item.fields);
    if (locked||this.busy==="loading") form.querySelectorAll("input,select,textarea").forEach(node => node.disabled = true);
    this.updateProfile();
    form.addEventListener("input", event => this.capture(event)); form.addEventListener("change", event => this.capture(event));
    form.addEventListener("submit", event => { event.preventDefault(); void this.save(); });
    this.root.querySelector("[data-batch-resume]").onclick=()=>void this.queue.resume();
    this.root.querySelector("[data-batch-toggle]").onclick = () => {
      this.batchOpen = !this.batchOpen;
      this.updateFlatlay();
    };
    for (const button of form.querySelectorAll("[data-skip],[data-review-skip]")) button.onclick = () => void this.skip();
    form.querySelector("[data-review-open]").onclick = () => this.flipReview(true);
    form.querySelector("[data-review-type]").onclick = () => this.flipReview(true);
    form.querySelector("[data-review-close]").onclick = () => this.flipReview(false);
    form.querySelector("[data-review-regenerate]").onclick = () => void this.process({ generationOnly: true, restart: true });
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
