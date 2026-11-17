/** 录入页：一条路 —— 拍照 / 选片 → 逐件识别 → 确认入库。
 *
 * 2026-11-17 用户拍板（B 方案）：删掉「手填属性 → 保存」那条路。
 * 它其实早就不可用了 —— `import.css` 里 `.photo-form > :not(.photo-field)`
 * 把整张属性表单 `display:none`，而用户一旦选片，整个视图又被 `PhotoImport`
 * 接管重绘（`root.innerHTML = ...`），那份表单连 DOM 都不存在了。
 * 留着它只会让页面挂着一个「只填品类也能存」的死按钮。
 *
 * 手填属性没有消失，位置换到逐件确认的「衣物信息」面（`reviewFieldsHtml`，
 * 名称 / 品类 / 主色 / 材质）以及 `garment-profile` 的更多属性里。
 */

import { bindClipboardOnce } from "./clipboard.js";
import { importMarkup } from "./markup.js";
import { attachImport, disposeImport, importSaving, checkpointImport } from "./photo-import.js";
import { toast } from "../../core/ui.js";

export const label = "录入";

export const addSaving = root => importSaving(root);

export async function checkpointAdd(root) {
  if (importSaving(root)) return false;
  return checkpointImport(root);
}

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
  let disposeSelection = () => {};

  root.innerHTML = importMarkup();
  const fileEl = root.querySelector("#add-image");
  const shot = root.querySelector("#add-shot");
  const shotImg = root.querySelector("#add-shot-img");

  /** 把粘贴 / 拖拽来的图片送给与相册共用的 change 处理。
   *
   *  `input.files` 是只读的，只能整个换掉（DataTransfer 是标准做法）。
   *  赋值失败说明浏览器太老，必须说出来：静默失败会让用户以为图已经放好了。 */
  function acceptImage(file) {
    try {
      const dt = new DataTransfer();
      dt.items.add(file);
      fileEl.files = dt.files;
      fileEl.dispatchEvent(new CustomEvent("change", { bubbles: true }));
      return true;
    } catch {
      toast("这张图接不住，请从相册选择再试一次", "error");
      return false;
    }
  }

  /** 上一次预览用的 blob URL。每次重画都先 revoke，否则拖十张图就漏十个。 */
  let shotUrl = null;

  function dispose() {
    disposeSelection();
    if (shotUrl) URL.revokeObjectURL(shotUrl);
    shotUrl = null;
    if (activeAdd?.fileEl === fileEl) activeAdd = null;
  }

  /** 选中照片后在照片卡里显示缩略图。
   *  选完片很快就会切到逐件确认，但「换了哪张」要能立刻看见。 */
  function renderShot() {
    if (shotUrl) {
      URL.revokeObjectURL(shotUrl);
      shotUrl = null;
    }
    const file = fileEl.files && fileEl.files[0];
    if (!file) {
      shotImg.removeAttribute("src");
      shot.hidden = true;
      return;
    }
    shotUrl = URL.createObjectURL(file);
    shotImg.src = shotUrl;
    shot.hidden = false;
  }

  // ★ 列表为空 = 这次没拿到文件（用户在选择器里按了返回，或壳侧以空数组回调）。
  //   这不是"换了张图"，什么都不该动 —— 尤其别把已经预览的那张清掉。
  fileEl.addEventListener("change", () => {
    if (!(fileEl.files && fileEl.files[0])) return;
    renderShot();
  });

  activeAdd = { root, fileEl, accept: acceptImage, dispose };
  bindClipboardOnce(() => activeAdd);

  disposeSelection = attachImport(root, ctx, {
    fileEl,
    onStart: () => dispose(),
    onExit: () => mount(root, ctx, options),
    initialFields: () => ({}),
  });
}
