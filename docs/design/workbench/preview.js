const groups = [
  { name: "今日", states: [
    ["today", "今日首页", "进入 App 或点击底部「今日」。", "从首页进入搭配、浏览风格，或查看已有记录。", "点「选一套」去搭配。点风格卡打开对应说明。"],
    ["today-guide", "今日 · 风格说明", "在今日页点击「找找喜欢的风格」中的风格卡。", "查看该风格的特点和穿法。", "点「按这个风格搭配」进入搭配页。关闭面板回今日。"],
  ] },
  { name: "搭配", states: [
    ["outfits", "搭配首页", "点击底部「搭配」。", "左右浏览审美卡。书本图标打开风格说明，星光按钮打开搭配。", "查看本风格的搭配结果，或切换其他风格。"],
    ["outfit-result", "搭配 · 结果面板", "在审美卡上点击星光按钮。", "查看这套所用的衣物。可以查看搭配依据或进入实时试衣。", "关闭面板回到当前风格卡。"],
    ["outfit-basis", "搭配 · 依据与反馈", "在搭配结果中点击信息按钮。", "查看搭配理由与依据，并提交对这套搭配的反馈。", "用返回按钮回上一层，或关闭面板回搭配页。"],
    ["tryon", "搭配 · 实时试衣面板", "在搭配结果中点击「实时试衣」。", "点击「开启摄像头」后，工作台显示标有说明的示例画面。可切换参考衣物或停止画面。", "关闭面板回到搭配结果。"],
  ] },
  { name: "添置", states: [
    ["purchase", "添置首页", "点击底部「添置」。", "浏览风格参考。星光按钮从当前风格开始添置，已有任务可从回看入口打开。", "进入选衣步骤，或查看已有任务。"],
    ["purchase-picker", "添置 · 选择已有衣物", "在添置风格卡上点击星光按钮。", "选出一件或多件已有衣物，作为本轮添置的搭配基础。", "选中至少一件后点「下一步」。"],
    ["purchase-format", "添置 · 选择添置形式", "在选衣步骤点「下一步」。", "选择一件单品、多件单品或一整套穿搭，再发起推荐。", "点「生成推荐」查看本轮进度。可返回上一步换衣。"],
    ["purchase-progress", "添置 · 生成进度", "选择形式后点击「生成推荐」。", "查看本轮生成状态。查询按钮沿用原任务。", "点「查询原任务」，或收起面板稍后再看。"],
    ["purchase-result", "添置 · 结果示例", "任务完成后进入原任务结果。", "这一张展示「现有衣物已足够」的结果分支。", "可以回到添置首页，或用「再生成」重新请求。"],
  ] },
  { name: "衣橱", states: [
    ["wardrobe", "衣橱首页", "点击底部「衣橱」。", "浏览衣物，搜索、切换分类，或打开筛选与录入。", "点击衣物卡进入详情。"],
    ["wardrobe-filter", "衣橱 · 筛选", "点击衣橱搜索栏旁的筛选按钮。", "选择品类与归档显示选项。", "应用条件后回到衣橱列表，也可直接关闭。"],
    ["item", "衣橱 · 单品详情", "点击衣橱列表中的衣物。", "查看照片，可用这件搭配、标记喜欢，或从右上角查看衣物信息。", "关闭详情回原页面，或点「用这件搭配」进入搭配。"],
    ["item-info", "衣橱 · 衣物信息", "点击单品详情卡上的信息图标。", "查看品类、颜色、材质等属性，可展开更多信息。", "点翻回按钮回照片，或关闭详情回列表。"],
    ["import", "衣橱 · 录入入口", "点击衣橱页的加号。", "录入面板只有照片入口：拍照或从相册多选，属性由识别填写，在逐件确认页核对和修改。", "选照片后进入批次确认。关闭面板回衣橱。"],
    ["import-selection", "录入 · 确认照片", "在录入入口点击相册或拍照按钮。", "工作台会放入一张示例照片，可以预览、移除或追加。", "点击「开始识别」进入衣物确认。"],
    ["import-review", "录入 · 确认衣物信息", "识别和平铺图准备完成后，点照片右上角的信息按钮。", "核对示例属性，按需调整品类和名称；展开「更多属性」还能改版型、风格、季节等九项。", "点「保存并结束」查看录入结果，或翻回照片继续核对。"],
    ["import-done", "录入 · 完成", "确认并保存本批最后一件。", "查看已入库和已跳过的数量。", "点「去衣橱看看」返回衣橱，或再录入一批。"],
  ] },
  { name: "AI 通道", states: [
    ["model-channel", "设置 · AI 通道", "点击 App 右上角的齿轮。工作台的「交互预览」可直接打开这个菜单。", "默认使用 API 直连，可以选择 OpenAI 或 DeepSeek。也可以手动切换到 WorkBuddy 账号额度。", "切换后可在设置里看到当前通道。点设置外部可关闭，原页面留在原位。"],
    ["ai-models", "设置 · 选择模型", "从右上角齿轮进入「API 设置」，也可直接打开本页的交互预览。", "OpenAI 用 GPT-6 Luna，DeepSeek 用 V4.1 Flash。两家都只有一档高能力模型，文字分析和照片识别共用同一个，强度都按高，不需要分别选。", "保存演示选择或关闭面板，返回 AI 通道菜单。"],
  ] },
];
const states = new Map(groups.flatMap(group => group.states).map(state => [state[0], state]));
const nav = document.querySelector("#pages");
const screen = document.querySelector("#screen");
const notes = document.querySelector("#notes");
const storage = document.querySelector("#storage");
const drafts = new Map();
let current = "today";
let mode = "shots";
let frameObserver;
const liveRoutes = new Map([["today", "today"], ["outfits", "outfits"], ["purchase", "gaps"], ["wardrobe", "wardrobe"], ["import", "add"]]);
// 交互预览要真的把 App 跑起来（ES 模块 + /health 等接口），这两样都过不了 file:// 的同源策略。
// 所以从本地文件打开时只能引导到本地服务，不能假装按钮点了就有界面。
const IS_FILE = location.protocol === "file:";
const LIVE_ORIGIN = "http://127.0.0.1:8798";

for (const group of groups) {
  const section = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = group.name;
  section.append(summary);
  for (const [id, title] of group.states) {
    const link = document.createElement("a");
    link.href = "#" + id;
    link.textContent = title;
    section.append(link);
  }
  nav.append(section);
}

function getLiveState() {
  const frame = document.querySelector("#live");
  const route = (frame.contentWindow?.location.hash || "").slice(1).split("/")[0];
  const doc = frame.contentDocument;
  if (doc?.querySelector("[data-ai-settings][open]")) return "ai-models";
  if (doc?.querySelector(".status-menu[open] [data-ai-channel]")) return "model-channel";
  if (doc?.querySelector(".tryon-panel")) return "tryon";
  if (doc?.querySelector(".item-glass-panel")) {
    return doc.querySelector(".item-detail-card[data-flipped='true']") ? "item-info" : "item";
  }
  if (doc?.querySelector("#w-filters[open]")) return "wardrobe-filter";
  if (doc?.querySelector(".aesthetic-layer .aesthetic-panel")) {
    if (route === "today") return "today-guide";
    return doc.querySelector(".aesthetic-feedback") ? "outfit-basis" : "outfit-result";
  }
  const purchase = doc?.querySelector(".pi-assistant-panel");
  if (purchase?.querySelector("[data-builder-format]")) return "purchase-format";
  if (purchase?.querySelector("[data-builder-item]")) return "purchase-picker";
  if (purchase?.querySelector("[data-builder-action='query']")) return "purchase-progress";
  if (purchase?.innerText.includes("现有衣物就能搭出这一套")) return "purchase-result";
  if (route === "add") {
    if (doc?.querySelector(".import-done")) return "import-done";
    if (doc?.querySelector("[data-import-review]")) return "import-review";
    if (doc?.querySelector("[data-import-selection]:not([hidden])")) return "import-selection";
    return "import";
  }
  return route;
}

const PANEL_STATES = ["model-channel", "ai-models"];
// 能直接跳过去的界面：五个主页面路由 + 两个设置面板。
// 其余是"过程态"（结果面板、试衣、录入确认…），只能在实际界面里点出来，
// 所以左栏点它们时仍然停截图模式。
const LIVE_STATES = new Set([...liveRoutes.keys(), ...PANEL_STATES]);
let pendingPanel = null;
let syncing = false;

function hintFor(id) {
  if (mode === "live") {
    if (IS_FILE) return "本地文件打开 · 交互预览需要本地服务";
    return PANEL_STATES.includes(id)
      ? "App 真实界面 · 可直接操作，API Key 用演示值"
      : "项目原始前端 · 演示衣物数据 · 不调用 AI 服务";
  }
  return "手机界面截图 · 393 × 819 · 演示数据";
}

function render() {
  const frame = document.querySelector("#live");
  const hashState = location.hash.slice(1).split("/")[0];
  // 打开设置面板的那一小段时间里，iframe 里的状态还没稳定（菜单要等 /health 回来），
  // 先按用户点的那一项锁住，别让 render 把左栏跳到「今日首页」。
  const requested = mode === "live" ? pendingPanel || getLiveState() || hashState : hashState;
  current = states.has(requested) ? requested : requested === "gaps" ? "purchase" : "today";
  const [id, title, entry, action, next] = states.get(current);
  const oldLink = nav.querySelector("[aria-current]");
  oldLink?.removeAttribute("aria-current");
  const link = nav.querySelector('a[href="#' + id + '"]');
  link.setAttribute("aria-current", "page");
  link.closest("details").open = true;
  document.querySelector("#screen-title").textContent = title;
  screen.src = "screens/" + id + ".png";
  screen.alt = title + "，手机界面截图";
  screen.hidden = mode === "live";
  // 本地文件打开时 iframe 永远是 about:blank，留着就是一个空白大方框
  frame.hidden = mode !== "live" || IS_FILE;
  document.querySelector("#preview-hint").textContent = hintFor(current);
  // 本地文件打开时，「交互预览」只能给引导卡片，不能留一个点不动的空舞台
  const notice = document.querySelector("#need-server");
  if (notice) notice.hidden = !(IS_FILE && mode === "live");
  const explanation = document.querySelector("#explanation");
  explanation.replaceChildren();
  for (const [label, value] of [["如何进入", entry], ["这个界面能做什么", action], ["下一步与返回", next]]) {
    const term = document.createElement("dt");
    term.textContent = label;
    const desc = document.createElement("dd");
    desc.textContent = value;
    explanation.append(term, desc);
  }
  try {
    notes.value = localStorage.getItem("wardrobe-workbench/screen/" + id) || "";
    storage.textContent = "备注保存在当前浏览器。";
  } catch {
    notes.value = drafts.get(id) || "";
    storage.textContent = "当前浏览器仅支持临时备注。";
  }
}

notes.addEventListener("input", () => {
  drafts.set(current, notes.value);
  try {
    localStorage.setItem("wardrobe-workbench/screen/" + current, notes.value);
    storage.textContent = "备注已保存。";
  } catch {
    storage.textContent = "当前浏览器仅支持临时备注。";
  }
});
document.querySelector("#size").addEventListener("change", event => {
  const phone = event.target.value === "phone";
  screen.classList.toggle("original", phone);
  document.querySelector("#live").classList.toggle("original", phone);
});
document.querySelector("#show-shots").addEventListener("click", () => {
  if (location.hash !== "#" + current) location.hash = current;
  mode = "shots";
  rememberMode();
  setPressed(false);
  render();
});
function setPressed(live) {
  document.querySelector("#show-shots").setAttribute("aria-pressed", String(!live));
  document.querySelector("#show-live").setAttribute("aria-pressed", String(live));
}

function rememberMode() {
  try { sessionStorage.setItem("wardrobe-workbench/mode", mode); } catch {}
}

/** 把 iframe 切到某一项。设置面板要额外点开菜单 / 弹窗，所以拆出来单说。 */
function applyLive(id) {
  const frame = document.querySelector("#live");
  if (PANEL_STATES.includes(id)) {
    openAISettings(frame, id);
    return;
  }
  const route = liveRoutes.get(id) || "today";
  if (frame.getAttribute("src") === "about:blank") frame.src = "/app/#" + route;
  else frame.contentWindow.location.hash = route;
}

/** 打开 AI 通道菜单 / API 设置面板。
 *
 * 两个坑：① iframe 还是 about:blank 时要靠 `?panel=` 让演示注入脚本在启动时打开，
 * 而状态菜单要等 `/health` 回来才渲染 —— 所以这里必须重试，不能只点一次；
 * ② 期间要先用 `pendingPanel` 把左栏锁在用户点的那一项，否则会闪回「今日首页」。
 */
function openAISettings(frame, id) {
  pendingPanel = id;
  applyPanel(frame, id, 0);
}

function applyPanel(frame, id, tries) {
  let doc = null;
  try { doc = frame.contentDocument; } catch {}
  const menu = doc?.querySelector(".status-menu");
  const configure = menu?.querySelector("[data-ai-configure]");
  if (!configure) {
    if (tries === 0) {
      const target = "/app/?panel=" + (id === "ai-models" ? "ai-models" : "ai-channel") + "#today";
      if (frame.getAttribute("src") !== target) frame.src = target;
    }
    if (tries < 25) setTimeout(() => applyPanel(frame, id, tries + 1), 100);
    return;
  }
  menu.open = true;
  if (id === "ai-models") configure.click();
  else doc.querySelector("[data-ai-settings][open]")?.close();
  // 面板是异步渲染的，这段时间里别让 MutationObserver 改掉当前项
  syncing = true;
  setTimeout(() => { syncing = false; pendingPanel = null; render(); }, 400);
}

/** 关掉设置弹窗和状态菜单。
 *
 * 难点：`openAIChannelSettings` 的 close 回调会把触发它的 `<details>` 重新打开
 * （为了让焦点回到原按钮），而这个回调是异步的 —— 如果只在这里把菜单关掉，
 * 下一拍它又自己张开了，观察器就会把左栏拽回「设置 · AI 通道」。
 * 所以关完再压一次，并用 `syncing` 把这几百毫秒的变动挡掉。
 */
function closePanels(frame) {
  let doc = null;
  try { doc = frame.contentDocument; } catch {}
  if (!doc) return;
  syncing = true;
  doc.querySelector("[data-ai-settings][open]")?.close();
  const menu = doc.querySelector(".status-menu");
  if (menu) menu.open = false;
  setTimeout(() => {
    const again = doc.querySelector?.(".status-menu");
    if (again) again.open = false;
    syncing = false;
    render();
  }, 300);
}

/** 探一下本地工作台服务在不在。
 *
 * `no-cors` 就够了：只关心「能不能连上」，不读响应体，也就不需要 serve.py 开跨域头。
 * 加超时是因为服务没起时有的浏览器会挂住十几秒，这期间按钮会一直等。
 */
async function probeServer() {
  try {
    const control = new AbortController();
    const timer = setTimeout(() => control.abort(), 1500);
    await fetch(LIVE_ORIGIN + "/health", { mode: "no-cors", cache: "no-store", signal: control.signal });
    clearTimeout(timer);
    return true;
  } catch {
    return false;
  }
}

async function updateServerState() {
  const state = document.querySelector("#server-state");
  const button = document.querySelector("#open-live-url");
  if (!state || !button) return;
  state.textContent = "正在检查本地服务…";
  button.hidden = true;
  if (await probeServer()) {
    state.textContent = "本地服务已经在运行，可以直接过去。";
    button.hidden = false;
  } else {
    state.textContent = "本地服务当前没有运行。";
  }
}

document.querySelector("#open-live-url").addEventListener("click", () => {
  location.href = LIVE_ORIGIN + "/?mode=live#" + current;
});

document.querySelector("#show-live").addEventListener("click", () => {
  mode = "live";
  rememberMode();
  setPressed(true);
  if (IS_FILE) {
    updateServerState();
    render();
    return;
  }
  applyLive(current);
  render();
});
document.querySelector("#live").addEventListener("load", () => {
  try {
    const frame = document.querySelector("#live");
    frame.contentWindow.addEventListener("hashchange", render);
    frameObserver?.disconnect();
    frameObserver = new MutationObserver(() => {
      if (syncing) return;
      const state = getLiveState();
      const id = state === "gaps" ? "purchase" : state;
      if (mode === "live" && states.has(id) && id !== current) {
        // 面板被关掉 / 保存后回到菜单，都是 iframe 内部的变化：地址栏要跟着走，
        // 否则会出现「左栏是设置·AI 通道，URL 还停在 #ai-models」的错位。
        history.replaceState(null, "", "#" + id);
        render();
      }
    });
    frameObserver.observe(frame.contentDocument.body, {
      childList: true, subtree: true, attributes: true, attributeFilter: ["data-flipped", "open", "hidden"],
    });
  } catch {}
  if (mode === "live" && pendingPanel) applyPanel(document.querySelector("#live"), pendingPanel, 0);
  render();
});
nav.addEventListener("click", event => {
  const link = event.target.closest("a");
  if (!link || mode !== "live") return;
  const id = link.hash.slice(1);
  event.preventDefault();
  // 本地文件打开时没有 iframe 可切，换页只换说明和截图，别去改 about:blank
  if (IS_FILE) {
    history.replaceState(null, "", "#" + id);
    render();
    return;
  }
  if (PANEL_STATES.includes(id)) {
    history.replaceState(null, "", "#" + id);
    applyLive(id);
    render();
    return;
  }
  if (liveRoutes.has(id)) {
    const frame = document.querySelector("#live");
    closePanels(frame);
    history.replaceState(null, "", "#" + id);
    applyLive(id);
    render();
    return;
  }
  document.querySelector("#show-shots").click();
  location.hash = link.hash;
});
window.addEventListener("hashchange", render);

// 支持 `?mode=live#ai-models` 直接进交互预览；刷新后保持上次的模式。
// 默认仍是截图 —— 首屏不该为了一张图去拉整个 App。
// 本地文件打开时强制回到截图：交互模式在这里只有一张引导卡，不该一进来就堵在它前面。
try {
  const wanted = new URLSearchParams(location.search).get("mode");
  if (!IS_FILE && (wanted === "live" || (!wanted && sessionStorage.getItem("wardrobe-workbench/mode") === "live"))) mode = "live";
} catch {}
setPressed(mode === "live");
render();
if (mode === "live" && IS_FILE) updateServerState();
else if (mode === "live" && LIVE_STATES.has(current)) applyLive(current);
