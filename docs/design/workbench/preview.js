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
    ["import", "衣橱 · 录入入口", "点击衣橱页的加号。", "打开录入面板，从相册或拍照开始录入。", "选照片后进入批次确认。关闭面板回衣橱。"],
    ["import-selection", "录入 · 确认照片", "在录入入口点击相册或拍照按钮。", "工作台会放入一张示例照片，可以预览、移除或追加。", "点击「开始识别」进入衣物确认。"],
    ["import-review", "录入 · 确认衣物信息", "识别和平铺图准备完成后，点照片右上角的信息按钮。", "核对示例属性，按需调整品类和名称。", "点「保存并结束」查看录入结果，或翻回照片继续核对。"],
    ["import-done", "录入 · 完成", "确认并保存本批最后一件。", "查看已入库和已跳过的数量。", "点「去衣橱看看」返回衣橱，或再录入一批。"],
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

function render() {
  const frame = document.querySelector("#live");
  const requested = mode === "live" ? getLiveState() : location.hash.slice(1).split("/")[0];
  current = states.has(requested) ? requested : requested === "gaps" ? "purchase" : "today";
  const [id, title, entry, action, next] = states.get(current);
  const oldLink = nav.querySelector("[aria-current]");
  oldLink?.removeAttribute("aria-current");
  const link = nav.querySelector('a[href="#' + id + '"]');
  link.setAttribute("aria-current", "page");
  link.closest("details").open = true;
  document.querySelector("#screen-title").textContent = title;
  screen.src = "screens/" + id + ".png";
  screen.alt = title + "，当前项目手机界面截图";
  screen.hidden = mode === "live";
  frame.hidden = mode !== "live";
  document.querySelector("#preview-hint").textContent = mode === "live"
    ? "项目原始前端 · 演示衣物数据 · 不调用 AI 服务"
    : "当前前端源码截图 · 393 × 819 手机视口 · 演示数据";
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
  mode = "shots";
  document.querySelector("#show-shots").setAttribute("aria-pressed", "true");
  document.querySelector("#show-live").setAttribute("aria-pressed", "false");
  render();
});
document.querySelector("#show-live").addEventListener("click", () => {
  if (location.protocol === "file:") {
    document.querySelector("#preview-hint").textContent = "请用本地工作台地址打开交互预览。";
    return;
  }
  mode = "live";
  const frame = document.querySelector("#live");
  if (frame.src === "about:blank") frame.src = "/app/#" + (liveRoutes.get(current) || "today");
  document.querySelector("#show-shots").setAttribute("aria-pressed", "false");
  document.querySelector("#show-live").setAttribute("aria-pressed", "true");
  render();
});
document.querySelector("#live").addEventListener("load", () => {
  try {
    const frame = document.querySelector("#live");
    frame.contentWindow.addEventListener("hashchange", render);
    frameObserver?.disconnect();
    frameObserver = new MutationObserver(() => {
      const state = getLiveState();
      const id = state === "gaps" ? "purchase" : state;
      if (mode === "live" && states.has(id) && id !== current) render();
    });
    frameObserver.observe(frame.contentDocument.body, {
      childList: true, subtree: true, attributes: true, attributeFilter: ["data-flipped", "open", "hidden"],
    });
  } catch {}
  render();
});
nav.addEventListener("click", event => {
  const link = event.target.closest("a");
  if (!link || mode !== "live") return;
  event.preventDefault();
  const route = liveRoutes.get(link.hash.slice(1));
  if (route) {
    document.querySelector("#live").contentWindow.location.hash = route;
  } else {
    location.hash = link.hash;
    document.querySelector("#show-shots").click();
  }
});
window.addEventListener("hashchange", render);
render();
