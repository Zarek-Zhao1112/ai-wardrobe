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

import { api } from "../../core/api.js";
import { bindClipboardOnce, isImageFile } from "./clipboard.js";
import { mountTextImport } from "./text-input.js";
import { importMarkup } from "./markup.js";
import { attachImport, disposeImport, importSaving, checkpointImport } from "./photo-import.js";
import { openCropper } from "./crop.js";
import { fillForm, readForm } from "../../core/fields.js";
import { esc, icon, itemChipsHtml, renderError, toast } from "../../core/ui.js";

import { loadManualDraft, saveManualDraft, manualStorageAvailable } from "./store.js";
export const label = "录入";
export const addSaving = root => Boolean((activeAdd?.root === root && activeAdd.saving()) || importSaving(root));
export async function checkpointAdd(root) {
  if (addSaving(root)) return false;
  if (activeAdd?.root === root) await activeAdd.checkpoint();
  return checkpointImport(root);
}

/** 打标返回字段 → 界面上的中文名（「识别」后提示填了哪几项用）。
 *  只列表单里真有的：`slot` 是后端算出来的，表单上没有它，故意不列。 */
const FIELD_LABELS = {
  type: "品类",
  // `brand` 必须在这里：`recognize()` 用 `FIELD_LABELS[k]` 把字段名翻成中文，
  // 漏了它 → 提示语里会少一项（`.filter(Boolean)` 把它静默丢掉），
  // 用户看到"AI 填了：品类、颜色"却不知道品牌也被填了。
  brand: "品牌",
  primary_color: "主色",
  material: "材质",
  fit: "版型",
  length: "裤长",
  formality: "正式度",
  pattern: "图案",
  colors: "颜色",
  style: "风格",
  season: "季节",
  scene: "场合",
};

// ---------------------------------------------------------------------------
// 粘贴 / 拖拽图片（2026-10-20 用户拍板）
//
// 目的：电脑上截图后不用「保存 → 点「选一张照片」→ 找路径」，直接 Ctrl+V 就行。
//
// ⚠️ **全局监听只注册一次**。`mount` 每进一次录入页就跑一次，
//   如果每次都往 `document` 上加监听，进出五次后一次粘贴会触发五次。
//   所以用模块级 `activeAdd` 指向**当前**那个录入页，监听器只认它。
// ---------------------------------------------------------------------------
let activeAdd = null;

export async function mount(root, ctx, options = {}) {
  disposeImport();
  activeAdd?.dispose();
  const taxonomy = ctx.taxonomy || {};
  let disposeText = () => {};
  let saving = false, manualSaved = false, lockedControls = [], manualReady = Promise.resolve();
  function beginSave() {
    saving = true;
    lockedControls = [...root.querySelectorAll("button,input,select,textarea")].map(node => [node, node.disabled]);
    for (const [node] of lockedControls) node.disabled = true;
  }
  function endSave() {
    saving = false;
    for (const [node, disabled] of lockedControls) if (node.isConnected) node.disabled = disabled;
    lockedControls = [];
  }
  const scrollTop = () => (root.closest(".wardrobe-import-scroll") || window).scrollTo({ top: 0, behavior: "smooth" });

  root.innerHTML = importMarkup(taxonomy);

  const pasteText = root.querySelector("#paste-text");
  root.append(root.querySelector('.paste-panel'));
  const pasteBtn = root.querySelector("#paste-btn");
  const pasteResult = root.querySelector("#paste-result");
  const form = root.querySelector("#add-form");
  const fileEl = root.querySelector("#add-image");
  const addResult = root.querySelector("#add-result");

  // §12：只撤回仍由 AI 管理的补值；用户编辑过的字段保留。
  const aiFields = new Map();
  function fieldValue(name) {
    const nodes = [...form.querySelectorAll(`[name="${name}"]`)];
    return nodes[0]?.type === "checkbox" ? nodes.filter(n => n.checked).map(n => n.value) : nodes[0]?.value;
  }
  function clearAiFields() {
    for (const [name, saved] of aiFields) {
      if (JSON.stringify(fieldValue(name)) !== JSON.stringify(saved.after)) continue;
      for (const node of form.querySelectorAll(`[name="${name}"]`)) {
        if (node.type === "checkbox") node.checked = saved.before.includes(node.value);
        else node.value = saved.before ?? "";
      }
    }
    aiFields.clear();
  }
  function applyRecognition(tags) {
    clearAiFields();
    const before = Object.fromEntries(Object.keys(tags || {}).map(name => [name, fieldValue(name)]));
    const filled = fillForm(form, tags, { onlyEmpty: true });
    for (const name of filled) aiFields.set(name, { before: before[name], after: fieldValue(name) });
    return filled;
  }
  for (const event of ["input", "change"]) form.addEventListener(event, ev => {
    if (ev.target.name) aiFields.delete(ev.target.name);
  });

  // §11：照片是主入口；文案功能移到表单后方，已有节点与事件逻辑不变。
  form.after(root.querySelector(".paste-panel"), pasteResult);

  /** 把粘贴 / 拖拽来的图片塞进 file input，之后走的还是原来那条路：
   *  `change` → 保存时上传 → 后端自动打标。**不另开一条上传链路**，
   *  否则两条路早晚会走岔（一条有打标、一条没有）。
   *
   *  `fromCrop`：这次替换是**裁剪流程自己**干的（把裁好的图塞回来）。
   *  「换了张新图就忘记裁剪记录」那条逻辑必须能把它和"用户真的换了图"
   *  区分开 —— 不然每裁一次就把原图记录清掉，等于没法再调。 */
  function acceptImage(file, { quiet = false, fromCrop = false } = {}) {
    try {
      // `input.files` 是只读的，只能整个换掉（DataTransfer 是标准做法）
      const dt = new DataTransfer();
      dt.items.add(file);
      fileEl.files = dt.files;
      fileEl.dispatchEvent(new CustomEvent("change", { bubbles: true, detail: { fromCrop } }));
      if (!quiet) {
        const kb = Math.max(1, Math.round(file.size / 1024));
        toast(`已接住图片（${kb} KB）—— 保存时自动识别属性`, "ok");
      }
      return true;
    } catch (err) {
      // 极老的浏览器不给 `input.files` 赋值。裁剪完走这里说明**白裁了**，
      // 必须说出来：静默失败的话用户会以为存的是裁好的图。
      toast("这张图接不住，请点「选一张照片」再试一次", "error");
      return false;
    }
  }

  const shot = root.querySelector("#add-shot");
  const shotImg = root.querySelector("#add-shot-img");
  const cropBtn = root.querySelector("#add-crop");
  // §24：照片统一交给逐件识别/平铺流程，旧裁剪控件不再进入界面。
  cropBtn.remove();

  async function checkpoint() {
    await manualReady;
    if (manualSaved || !fileEl.isConnected) return;
    try {
      await saveManualDraft({ version: 1, fields: readForm(form), text: pasteText.value,
        more: form.querySelector(".more-fields")?.open || false });
    } catch {}
    if (!manualStorageAvailable()) toast("本次填写暂未保存到本机，请保持应用打开。", "error", 6000);
  }
  activeAdd = { root, fileEl, accept: acceptImage, dispose, saving: () => saving, checkpoint };
  const editedManual = new Set();
  const remember = event => {
    if (event.target?.name) { editedManual.add(event.target.name); manualSaved = false; if (!saving) root.querySelector("#add-submit").disabled = false; }
    else editedManual.add(event.target === pasteText ? "$text" : "$more");
    void checkpoint();
  };
  form.addEventListener("input", remember); form.addEventListener("change", remember);
  pasteText.addEventListener("input", remember);
  form.querySelector(".more-fields")?.addEventListener("toggle", remember);
  manualReady = loadManualDraft().then(draft => {
    if (!draft || !fileEl.isConnected) return;
    const toRestore = Object.fromEntries(Object.entries(draft.fields || {}).filter(([name]) => !editedManual.has(name)));
    fillForm(form, toRestore, { names: Object.keys(toRestore) });
    if (!editedManual.has("$text")) pasteText.value = draft.text || "";
    const more = form.querySelector(".more-fields"); if (more && !editedManual.has("$more")) more.open = !!draft.more;
  });
  bindClipboardOnce(() => activeAdd);

  // ------------------------------------------------------------------
  // 照片：只有一条路 —— `<input type=file>`（2026-10-22 收口）
  //
  // 历史：2026-10-21 发现小米浏览器的 PWA 壳 `PWAActivity` 没实现
  // WebView 的 `onShowFileChooser`，点 `<input type=file>` 毫无反应，
  // 于是另开了一条「后端直读 /sdcard」的相册通道兜底。
  //
  // 2026-10-22 手机端换成自建 APK，壳侧 `MainActivity` 补上了
  // `onShowFileChooser` → 原生选择器能用了。而 `accept="image/*"` 在
  // Android 上拉起来的**本来就是相册网格**，所以两条路做的事完全重合，
  // 兜底那条反而更慢（拉 36 张原图当缩略图，服务端没有 Pillow 出不了
  // 小图）。于是收口成一条：按钮只是 `.click()` 那个藏起来的 input。
  //
  // 当前通过原生多选录入；旧相册服务API保留兼容，旧页面已移除。
  // 历史实现位于796678c:web/js/view-add.js，不作为当前恢复入口。
  // ------------------------------------------------------------------
  const albumBtn = root.querySelector("#add-album");
  const albumPicked = root.querySelector("#add-album-picked");
  /** 相册里选中的那张 `{id, name}`。与 `fileEl.files` **互斥**，后选的赢。
   *  ⚠️ 2026-10-22 起没有 UI 入口，恒为 `null` —— 见上面那段说明。 */
  let pickedPhoto = null;
  /** 「传了照片就不用选品类」那句提示的刷新钩子。
   *  真正的实现写在下面（要读 `ctx.health.vision_ready`），
   *  这里先占位 —— 相册的选择动作发生得比它早。 */
  let refreshHint = () => {};

  function renderPicked() {
    if (!pickedPhoto) {
      albumPicked.hidden = true;
      albumPicked.innerHTML = "";
      refreshHint();
      renderShot(); // 相册那张被清掉了，预览要退回 input 里的文件（或空）
      return;
    }
    albumPicked.hidden = false;
    albumPicked.innerHTML = `
      <div class="album-chip">
        <img src="${esc(api.albumPhotoUrl(pickedPhoto.id))}" loading="lazy" decoding="async" alt="">
        <div class="album-chip-body">
          <div class="album-chip-name">${esc(pickedPhoto.name)}</div>
          <button type="button" class="ghost" id="album-clear">不用这张</button>
        </div>
      </div>
    `;
    albumPicked.querySelector("#album-clear").addEventListener("click", () => {
      pickedPhoto = null;
      renderPicked();
    });
    refreshHint();
    renderShot();
  }

  // ------------------------------------------------------------------
  // 图片预览 + 裁剪（2026-10-21）
  //
  // ★ 为什么裁剪要**另开一个全屏窗口**（用户拍板）：录入页里这块只有一张
  //   小缩略图，在里面拖框既看不清、又容易误触下面的表单。裁剪窗口把图铺满
  //   屏幕、框外压暗，用户只看这一件事，确认后回到这里继续填。
  //
  // ★ 为什么裁剪只在**前端**做：服务端没装 Pillow（`images.py:13` 明确不引入
  //   图像库），只为一个裁剪装它不值。canvas 本来就在浏览器里，还省一次往返。
  //
  // ★ 裁剪结果是**替换**而不是新增：`image_path` 一个单品只有一列
  //   （`image_path_of` 注释：一个单品只保留一张图），保留原截图做不到。
  // ------------------------------------------------------------------

  /** 裁剪用的**原图**（不是裁完的那张）+ 上次生效的归一化框。
   *
   *  ★ 为什么必须留着它（用户要的"截取后仍可再次调整选区"）：
   *    裁完写进 `fileEl` 的是**裁剪结果**。这个时候再点「裁剪商品图」，
   *    `imageSource()` 拿到的是那张结果图 —— 等于在一张裁图里再裁一次：
   *    越裁越小，而且**回不去了**（原截图已经被替换掉了）。
   *    存着原图 + 上次的框，第二次打开就能接着上次的位置调。
   *
   *  `revoke: true` 表示 `src` 是我们自己 `createObjectURL` 出来的，
   *  所有权归这里，换图 / 离开时要负责释放。
   *
   *  ⚠️ 它只在**本次录入**有效：保存之后只有一个 `image_path`，原截图不落库。 */
  let cropSource = null;

  /** 丢掉原图记录（用户真的换了图 / 又录一件）。 */
  function dropCropSource() {
    if (!cropSource) return;
    if (cropSource.revoke) URL.revokeObjectURL(cropSource.src);
    cropSource = null;
  }

  /** 当前待录入的那张图。两条通道互斥（后选的赢），所以按优先级取一个即可。
   *
   *  ★ `cropSource` 排在最前：只要还留着原图，裁剪就永远从**原图**开始，
   *    绝不在裁剪结果上再裁。 */
  function imageSource() {
    if (cropSource) {
      return { src: cropSource.src, revoke: false, rect: cropSource.rect, file: cropSource.file };
    }
    const file = fileEl.files && fileEl.files[0];
    if (file) return { src: URL.createObjectURL(file), revoke: true, rect: null, file };
    if (pickedPhoto) {
      // 相册图走同源接口 —— canvas 能画。跨源图会让 `toBlob` 抛 SecurityError，
      // 那正是"裁完没反应"这类幽灵问题的来源。
      return { src: api.albumPhotoUrl(pickedPhoto.id), revoke: false, rect: null };
    }
    return null;
  }

  /** 上一次预览用的 blob URL。每次重画都先 revoke，否则拖十张图就漏十个。 */
  let shotUrl = null;

  function dispose() {
    disposeText();
    dropCropSource();
    if (shotUrl) URL.revokeObjectURL(shotUrl);
    shotUrl = null;
    aiFields.clear();
    if (activeAdd?.fileEl === fileEl) activeAdd = null;
  }

  /** 把当前图渲染成一张缩略图，并决定「裁剪商品图」按钮是否出现。
   *  没有这张预览的话，用户裁完看不到结果，只能等保存后进详情页才发现裁歪了。 */
  function renderShot() {
    if (shotUrl) {
      URL.revokeObjectURL(shotUrl);
      shotUrl = null;
    }
    const file = fileEl.files && fileEl.files[0];
    if (file) {
      shotUrl = URL.createObjectURL(file);
      shotImg.src = shotUrl;
      shot.hidden = false;
    } else if (pickedPhoto) {
      shotImg.src = api.albumPhotoUrl(pickedPhoto.id);
      shot.hidden = false;
    } else {
      shotImg.removeAttribute("src");
      shot.hidden = true;
    }
    cropBtn.hidden = !(file || pickedPhoto);
    const hasPhoto = !!(file || pickedPhoto);
    albumBtn.classList.toggle("has-photo", hasPhoto);
    albumBtn.innerHTML = hasPhoto
      ? `${icon("photo")}<span>更换照片</span>`
      : `${icon("photo")}<strong>选一张照片</strong><span class="faint">拍下衣服，或从相册中选择</span>`;
  }

  /** 正在裁剪。互斥位——两个浮层同时开着会互相覆盖结果：识别流程自动弹一个、
   *  用户又点「裁剪商品图」再弹一个，后确认的那个拿**原图**重裁，
   *  表现就是"我裁好的图又变回去了"。 */
  let cropping = false;

  /**
   * 打开裁剪窗口。
   * @param initial AI 刚给的归一化框（可空）
   * @param grow    `initial` 要不要按"模型框偏小"外扩。**上次的框不能扩**
   *                —— 每开一次大一圈，用户会发现框自己在长。
   * @returns 是否真的替换了图片（用户点「保持原图」返回 false）
   */
  async function cropImage(initial = null, { grow = true } = {}) {
    if (cropping) return false;
    const source = imageSource();
    if (!source) {
      toast("先放一张照片", "error");
      return false;
    }
    cropping = true;
    /** 这次是不是把 `source.src` 的所有权接过来存进 `cropSource` 了。
     *  接手了就不能在 finally 里 revoke —— 下次还要用它再打开裁剪窗。 */
    let adopted = false;
    try {
      // 没有新的 AI 框就回到上次的位置；第一次裁才用默认框。
      const startRect = initial || source.rect || null;
      const out = await openCropper({
        src: source.src,
        initial: startRect,
        grow: initial ? grow : false,
      });
      if (!out) return false; // 用户选了「保持原图」

      // 浮层开着时用户可能按了返回键 / 点了导航。这时不能再写 `fileEl`、
      // 也不该弹 toast —— 那会落到一个已经销毁的视图上，而且新页面上会
      // 莫名其妙冒出一句"已裁出商品图"。
      if (!fileEl.isConnected) return false;

      const file = new File([out.blob], `crop-${Date.now()}.jpg`, {
        type: "image/jpeg",
      });
      // ★ 不在这里手动清 `pickedPhoto`：`acceptImage` 派发 `change` 时，
      //   上面那个 `fileEl.addEventListener("change")` 会处理（`files[0] &&
      //   pickedPhoto` → 清掉 + 重画预览）。提前清的话，万一 `acceptImage`
      //   在极老浏览器上赋值失败，两条通道就都空了 —— 用户刚选的相册图直接消失。
      if (!acceptImage(file, { quiet: true, fromCrop: true })) return false;

      // 记下**原图 + 这次的框**：下次点「裁剪商品图」能接着调，而不是
      // 在当前这张裁图上再裁一刀。
      //
      // ★ 同一条 URL 绝不能用「先 drop 再存回来」的写法：`dropCropSource()`
      //   会把它 `revokeObjectURL` 掉，而这次传进来的 `source.src` 正是它
      //   本身（`imageSource()` 在有 `cropSource` 时返回的就是它的地址）。
      //   那样第一次重裁之后，原图链接就成了死地址 —— 第三次点「裁剪商品图」
      //   直接"图片读不出来"，而用户手上的图还是好的。实测过，别再写回去。
      if (cropSource && cropSource.src === source.src) {
        cropSource.rect = out.rect;
      } else {
        dropCropSource();
        cropSource = { src: source.src, revoke: source.revoke, rect: out.rect, file: source.file };
        adopted = source.revoke;
      }

      renderShot();
      toast(`已裁出商品图（${out.width}×${out.height}）`, "ok");
      return true;
    } finally {
      cropping = false;
      // 窗口已经关掉、canvas 已经画完，这时才安全释放。
      // 但被 `cropSource` 接手的那条 URL 要留着（见上面的 adopted）。
      if (source.revoke && !adopted) URL.revokeObjectURL(source.src);
    }
  }

  // 按钮就是那个藏起来的 input —— 点它等于点 input 本身。
  // 必须在用户手势里**同步**调 `.click()`（异步之后再调，浏览器会当自动弹出拦掉）。
  albumBtn.addEventListener("click", () => {
    fileEl.click();
  });

  // 用 input 选文件 / 粘贴 / 拖拽 → 都说明用户走的是另一条路
  fileEl.addEventListener("change", (ev) => {
    const picked = fileEl.files && fileEl.files[0];
    // ★ 列表为空 = 这次没拿到文件（用户在选择器里按了返回，或壳侧以空数组回调）。
    //   这不是"换了张图"，**什么都不该动** —— 尤其不能 `dropCropSource()`：
    //   那会把用户之前裁剪用的原图和框一起丢掉，下次「裁剪商品图」只能拿裁好的
    //   结果再裁，越裁越小（正是 `imageSource` 注释要防的事）。
    if (!picked) return;
    clearAiFields();
    // 真的是"换了张图"才把裁剪记录清掉。裁剪流程自己塞回来的那份
    // （`fromCrop`）不算 —— 清了就等于裁完再也不能调了。
    const fromCrop = !!(ev.detail && ev.detail.fromCrop);
    if (!fromCrop) dropCropSource();
    if (pickedPhoto) {
      pickedPhoto = null;
      renderPicked(); // 内部会重画预览
    } else {
      renderShot();
    }
  });

  // 独立的裁剪入口：不识别也能裁（用户可能只想修一下图）
  cropBtn.addEventListener("click", () => cropImage(null));

  // ------------------------------------------------------------------
  // ① 粘贴 → 解析 → 预览 → 批量提交
  // ------------------------------------------------------------------

  disposeText = mountTextImport({ root, taxonomy, pasteText, pasteBtn, pasteResult,
    isSaving: () => saving, beginSave, endSave, scrollTop, isCurrent: () => fileEl.isConnected });


  // ------------------------------------------------------------------
  // ② 手填（原有逻辑，未改）
  // ------------------------------------------------------------------

  const submitBtn = root.querySelector("#add-submit");
  const addHint = root.querySelector("#add-hint");

  // ★ 有照片时把提示改成"我来认"：这是视觉打标**唯一需要用户知道的入口变化**。
  //   只在真能生效时才说（`vision_ready`）—— 没配视觉模型却说"我来认"，
  //   用户会一直等一个不会发生的事。
  if (ctx.health && ctx.health.vision_ready) {
    refreshHint = () => {
      const has = !!(fileEl.files && fileEl.files[0]) || !!pickedPhoto;
      const typePicked = !!readForm(form).type;
      addHint.textContent = has && !typePicked
        ? "传了照片就不用选品类了 —— 保存时我来看图认，认错了再改。"
        : "只填品类也能存 —— 颜色、材质这些以后随时能补。";
    };
    fileEl.addEventListener("change", refreshHint);
    form.addEventListener("change", refreshHint);
    refreshHint();
  }

  // ------------------------------------------------------------------
  // 「AI 识别」按钮（2026-10-21 用户要求）
  //
  // 打标原来只发生在**保存之后**（上传接口顺手打）：想先看 AI 认成什么，
  // 只能先存一件半成品再进详情页改。按钮放在保存旁边，把顺序倒过来 ——
  // 先识别、填表、看着不对就改、再保存。
  //
  // ⚠️ 只填**空着的**字段（`fillForm(..., {onlyEmpty:true})`）：用户手填的
  //    优先级永远高于 AI。这和保存时 `_only_blank` 是同一条口径 ——
  //    点一下按钮就把已填内容冲掉，是这类按钮最容易犯的错。
  // ------------------------------------------------------------------
  const recognizeBtn = root.querySelector("#add-recognize");
  const recognizeNote = root.querySelector("#add-recognize-note");

  function imageContext() {
    return cropSource?.file ? { original: cropSource.file, rect: cropSource.rect } : {};
  }

  async function recognize() {
    const file = fileEl.files && fileEl.files[0];
    if (!file && !pickedPhoto) {
      toast("先放一张照片：点「选一张照片」，或直接粘贴 / 拖进来", "error");
      return;
    }
    // 拍一张"这一刻是哪张图"的快照。识别要等 2~30 秒，这期间用户完全可能
    // 粘一张新图/换一个相册照片 —— 回来的 `tags` 和 `crop` 都属于**旧图**，
    // 直接填进表单就是张冠李戴，裁剪框还会按旧图的比例套在新图上。
    const fileSnapshot = file;
    const photoSnapshot = pickedPhoto;

    recognizeBtn.disabled = true;
    // 识别期间不许保存：否则会把"识别前那份还没填的空值"提交上去，
    // 而用户以为等的是识别结果。两个动作共用一个 busy 位。
    submitBtn.disabled = true;
    // 也别让用户开第二个裁剪窗：识别成功后会自动弹一个，两个叠在一起，
    // 后确认的那个会拿原图重裁，把先前的成果盖掉。
    cropBtn.disabled = true;
    const idleText = recognizeBtn.textContent;
    recognizeBtn.textContent = "识别中…";
    recognizeNote.innerHTML = "";
    try {
      // 照片可能来自两条通道：浏览器里的 File，或手机相册的 id。
      // 后端 `/tagging/preview` 两个都收，识别结果一模一样（同一条打标链路）。
      let res = file
        ? await api.previewTags(file, imageContext())
        : await api.previewTagsFromAlbum(pickedPhoto.id);

      // 200 但 body 不是 JSON 时，`request()` 会把**原始字符串**当 payload 返回
      // （反代/网关改写响应就是这样）。不校验的话会走成功分支、弹一句
      // 「没有可补的字段」—— 用户以为模型没认出来，其实请求根本没到模型。
      if (!res || typeof res !== "object" || !res.tags || typeof res.tags !== "object") {
        throw new Error("返回格式不对（可能被网关拦了），看下后端日志");
      }

      // 等结果期间换了图 → 这份结果不属于当前这张图，丢掉。
      // （比对**对象引用**：File 和 pickedPhoto 每次选择都是新对象，
      //  同一张图重新选一次也算"换了"，宁可让用户再点一次也不写错数据。）
      if (
        (fileEl.files && fileEl.files[0]) !== fileSnapshot ||
        pickedPhoto !== photoSnapshot
      ) {
        toast("图换了，这次识别结果丢掉了 —— 对新图再点一次识别", "error");
        return;
      }

      // 识别这几秒里用户可能已经切走页面（hash 路由会把视图整个换掉）。
      // 改 DOM 本身无害，但 `toast()` 写的是**全局**节点 —— 会在别的页面上
      // 弹一句「识别完成」，很莫名其妙。这里用和剪贴板监听同一套判据。
      if (!recognizeBtn.isConnected) return;

      // ★ 识别完先走一步裁剪 —— 这是用户要的"单独一个过程"。
      //
      // 为什么排在报结果**之前**：裁剪窗口会盖住整页，用户从窗口回来才看到
      // 「AI 填了…」。反过来的话，那句话在他框图的时候就已经写好了，回来还
      // 停在那儿，像是上一条消息。
      //
      // 为什么面积 > 0.75 就不弹：那说明整张图就是商品图（纯衣服照片，没有
      // 订单信息），弹一个几乎贴着图片边的框只会让人觉得这个功能很烦。
      //
      // 为什么 `cropSource` 有值就不弹：这时 `fileEl` 里装的是**裁剪结果**，
      // 模型给的 `crop` 是相对那张裁图的坐标，而裁剪窗会从**原图**打开 ——
      // 两套坐标系，套上去就是裁错地方。要接着调，用户按「裁剪商品图」
      // 就行，那条路走的是原图 + 上次的框，坐标是自洽的。
      const crop = res.crop;
      if (crop && crop.w * crop.h <= 0.75 && !cropSource) {
        recognizeNote.textContent = "识别完了 —— 先把商品图框出来";
        const changed = await cropImage(crop);
        // 框图那几十秒里用户可能已经切走页面了：这时别再往下写 note/toast，
        // 否则会在别的页面上弹一句"识别完成"。
        if (!recognizeBtn.isConnected) return;
        if (changed) {
          const croppedFile = fileEl.files[0];
          recognizeNote.textContent = "正在结合商品图与原图描述重新识别…";
          res = await api.previewTags(croppedFile, imageContext());
          if (!recognizeBtn.isConnected) return;
          if (!res || typeof res !== "object" || !res.tags || typeof res.tags !== "object") {
            throw new Error("返回格式不对（可能被网关拦了），看下后端日志");
          }
          if (fileEl.files[0] !== croppedFile) {
            toast("图换了，请对新图重新识别", "error");
            return;
          }
        }
      }

      const filled = applyRecognition(res.tags);
      refreshHint();

      const names = filled.map((k) => FIELD_LABELS[k]).filter(Boolean);
      const missed = unresolvedText(res.unresolved);
      const conf = typeof res.ai_confidence === "number" ? res.ai_confidence : 0;

      const parts = [
        names.length
          ? `AI 填了：${names.join("、")}`
          : "没有可补的字段（你填过的我没动）",
      ];
      if (missed) parts.push(`没认出来：${missed}（没写进表单）`);
      if (Object.keys(res.conflicts || {}).length) parts.push(`需确认：${Object.entries(res.conflicts).map(([key, reason]) => `${FIELD_LABELS[key] || key}：${reason}`).join("；")}（待确认项未自动填入）`);
      if (names.length && conf && conf < 0.5) parts.push("AI 把握不大，建议核对");
      recognizeNote.textContent = parts.join(" · ");

      toast(
        names.length ? `识别完成，填了 ${names.length} 项` : "识别完成，没有可补的字段",
        "ok"
      );
    } catch (err) {
      if (!recognizeBtn.isConnected) return;
      // 503 = 没配模型 / 模型连不上，**不是这张图的问题** —— 说清楚，
      // 否则用户会反复换图重试（保存那条路也是同一条契约）。
      // 后端 detail 已经是「模型不可用：…」，这里不再套第二层括号。
      const raw = String(err.message || "识别失败").slice(0, 200);
      const msg = err.status === 503 ? `${raw} —— 这是配置问题，换张图也没用` : raw;
      recognizeNote.innerHTML = `<div class="alert error">${esc(msg)}</div>`;
      toast("识别失败", "error");
    } finally {
      recognizeBtn.disabled = false;
      submitBtn.disabled = false;
      cropBtn.disabled = false;
      recognizeBtn.textContent = idleText;
    }
  }

  if (ctx.health && ctx.health.vision_ready) {
    // 没配视觉模型时**不显示**这个按钮 —— 摆一个点了就报错的按钮，
    // 比没有按钮更让人以为是坏的（相册入口用的是同一条判断）。
    recognizeBtn.hidden = false;
    recognizeBtn.addEventListener("click", recognize);
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
    const conflicts = Object.entries(tagging?.conflicts || {});
    return taggingSummaryHtml(tagging) + (conflicts.length
      ? `<div class="alert">以下属性需确认：${conflicts.map(([key, reason]) => esc(`${FIELD_LABELS[key] || key}：${reason}`)).join("；")}</div>` : "");
  }

  function taggingSummaryHtml(tagging) {
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
      manualSaved = false; submitBtn.disabled = false; void saveManualDraft(null).catch(() => {});
      aiFields.clear();
      // `form.reset()` **只还原 selected / checked，不删节点**。上一步 AI 若
      // 认出了词表外的值（`fields.js` 会动态补 option / chip），不清就一直挂在
      // 下拉和 chip 区里 —— 下一件能选中上一件的垃圾值。
      form.querySelectorAll("[data-dynamic]").forEach((n) => n.remove());
      fileEl.value = "";
      pickedPhoto = null;
      dropCropSource(); // 上一件的原图和框都作废，否则下一件会按上一件的位置裁
      renderPicked();
      recognizeNote.innerHTML = ""; // 上一件的"AI 填了…"别留到下一件
      addResult.innerHTML = "";
      scrollTop();
    });
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (saving || manualSaved) return;
    beginSave();
    await manualReady;
    const payload = readForm(form);
    const file = fileEl.files && fileEl.files[0];
    const uploadContext = imageContext();

    // ★ 品类门槛：**传了照片就不逼你先选**。
    //
    // 为什么：视觉打标能自己认品类（实测 5/5 全对），硬要你先选
    // 等于把最有价值的那个能力架空了 —— 而且录衣服时你手上只有照片。
    //
    // 底线仍在：**没照片**时必须选（没有别的信息源，存 `other` 等于扔掉一件衣服）。
    // 有照片时先按 `other` 建单品，随后打标会**把它改成真品类**
    // （`tag_and_apply` 的 `_only_blank` 特判 `other` = 还没填）。
    if (!payload.type) {
      if (!file && !pickedPhoto) {
        endSave();
        toast("先选一个品类（或者传张照片，让我来认）", "error");
        return;
      }
      payload.type = "other";
    }

    submitBtn.disabled = true;
    // 保存期间禁止识别：`payload` 已经读走了，这时填表只会让人以为
    // "改的东西也一起存了"，实际存的是旧值。
    recognizeBtn.disabled = true;
    submitBtn.textContent = "保存中…";
    try {
      const resp = await api.createItem(payload);
      root.dataset.wardrobeDirty = "true";

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
          const up = await api.uploadImage(resp.item.id, file, uploadContext);
          tagging = up.tagging || null;
        } catch (err) {
          imageError = err;
        }
      } else if (pickedPhoto) {
        // 相册那条路：只传 id，字节由后端自己从磁盘读（浏览器没碰过文件）
        try {
          const up = await api.importFromAlbum(resp.item.id, pickedPhoto.id);
          tagging = up.tagging || null;
        } catch (err) {
          imageError = err;
        }
      }

      manualSaved = true; await saveManualDraft(null).catch(() => {});
      renderFeedback(resp, imageError, tagging);
      // toast 只说"照片没传上"这种**用户需要马上知道**的事；
      // 打标是增强信息，放在结果区里说清（toast 3 秒就没了）。
      toast(imageError ? "已入库（照片没传上）" : "已入库", imageError ? "error" : "ok");
    } catch (err) {
      renderError(addResult, err);
      toast("保存失败", "error");
    } finally {
      endSave();
      submitBtn.disabled = manualSaved;
      recognizeBtn.disabled = false;
      submitBtn.textContent = "保存到衣橱";
    }
  });
  attachImport(root, ctx, { fileEl, ready: () => manualReady, isSaving: () => saving, onPreparing: value => value ? beginSave() : endSave(),
    onStart: ({ consumeManual = false } = {}) => { if (consumeManual) void saveManualDraft(pasteText.value ? { version: 1, fields: {}, text: pasteText.value } : null).catch(() => {}); dispose(); },
    onExit: () => mount(root, ctx, options), initialFields: () => readForm(form) });
}
