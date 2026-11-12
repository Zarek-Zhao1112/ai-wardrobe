/** 自绘选择层：替换原生 `<select>` 的弹窗。
 *
 * 为什么要替换：Android WebView 的原生下拉是**系统弹窗**，颜色/圆角/分组
 * 全都跟不上页面，51 个品类挤在一个滚动列表里也很难找（手机实测）。
 *
 * 设计底线 —— **不改变数据契约**：
 * 原生 `<select>` 仍然在 DOM 里、仍然带 `name`、仍然是唯一的真理来源。
 * 这里只是给它加一个看得见的替身（`.picker-btn`）。所以：
 *   · `readForm()` / `fillForm()` / `hasOption()` 一行都不用改；
 *   · 保存、必填校验、`form.elements` 一切照旧；
 *   · JS 没跑起来时 `<select>` 还在（只是被 CSS 藏了）——
 *     这也是为什么**隐藏**必须由本模块加类来做，而不是写在 HTML 的行内样式里。
 *
 * 提交时用 `select.value = x` + 派发 `change`：调用方监听的是 `change`，
 * 换成 `input` 派发那些处理器就收不到了（原生 select 只发 `input`+`change`，
 * 但本项目各处监听的是 `change`）。
 */

import { esc, icon } from "../core/ui.js";

/** 主色 → 色点。键取自 `app/taxonomy.py` 的 `COLORS`（顺序一致）。
 *  词表里没有的值（词表外的手工值）不画色点，只留文字。 */
const COLOR_HEX = {
  black: "#1d1d1f",
  white: "#ffffff",
  gray: "#9a9aa0",
  beige: "#efe9dc",
  khaki: "#c9b28a",
  brown: "#7a4b2a",
  navy: "#1f2b4c",
  blue: "#2f6fd0",
  light_blue: "#a9cdf0",
  green: "#4c8f5f",
  army_green: "#5b6b3d",
  red: "#cf3a2f",
  burgundy: "#7a2334",
  pink: "#f2b8c6",
  purple: "#7c5cae",
  yellow: "#f2c94c",
  orange: "#e9802b",
  silver: "#c8ccd2",
  gold: "#c9a227",
  multi: "multi",
  print: "print",
};

let started = false;
let layer = null;
let titleEl = null;
let searchWrap = null;
let searchEl = null;
let bodyEl = null;
let current = null;      // 正在编辑的 <select>
let closeTimer = 0;

const DURATION = 300;

/** 空值选项要不要显示成一行「清除」。
 *
 * `selectHtml()` 的占位是「未指定」—— 那是个**可以选**的清除动作；
 * 而「选一个品类 / 全部品类」是提示语，不是选项，渲染成可点的按钮会误导。
 */
function isClearOption(option) {
  return option.value === "" && option.textContent.includes("未指定");
}

function groupsOf(select) {
  const groups = [];
  let bare = null;
  for (const child of select.children) {
    if (child.tagName === "OPTGROUP") {
      bare = null;
      groups.push({ label: child.label || "", items: [...child.children] });
    } else if (child.tagName === "OPTION") {
      if (isClearOption(child)) {
        if (!bare) { bare = { label: "", items: [] }; groups.push(bare); }
        bare.items.push(child);
      } else if (child.value !== "") {
        if (!bare) { bare = { label: "", items: [] }; groups.push(bare); }
        bare.items.push(child);
      }
    }
  }
  return groups.filter((group) => group.items.length);
}

function dotStyle(value) {
  const hex = COLOR_HEX[value];
  if (hex === "multi") {
    return "background:conic-gradient(#cf3a2f 0 25%,#f2c94c 0 50%,#4c8f5f 0 75%,#2f6fd0 0)";
  }
  if (hex === "print") {
    return "background:repeating-linear-gradient(45deg,#1d1d1f 0 2px,#ffffff 2px 5px)";
  }
  if (hex) return `background:${hex}`;
  return "background:var(--surface-2)";
}

function render(keyword) {
  if (!current) return;
  const kind = current.dataset.pickerKind || "list";
  const q = (keyword || "").trim();
  let html = "";
  let total = 0;

  for (const group of groupsOf(current)) {
    const items = q ? group.items.filter((o) => o.textContent.includes(q)) : group.items;
    if (!items.length) continue;
    total += items.length;
    if (group.label) html += `<div class="picker-group">${esc(group.label)}</div>`;
    html += `<div class="picker-grid${kind === "color" ? " is-column" : ""}">`;
    for (const option of items) {
      const on = option.value === current.value;
      const text = esc(option.textContent);
      if (kind === "color") {
        html +=
          `<button type="button" class="picker-row${on ? " is-on" : ""}"` +
          ` data-pick="${esc(option.value)}" role="option" aria-selected="${on}">` +
          `<i class="picker-dot" style="${dotStyle(option.value)}"></i>` +
          `<span>${text}</span><b class="picker-tick">✓</b></button>`;
      } else {
        const cls = "picker-item" + (option.value === "" ? " is-clear" : "") + (on ? " is-on" : "");
        html +=
          `<button type="button" class="${cls}"` +
          ` data-pick="${esc(option.value)}" role="option" aria-selected="${on}">${text}</button>`;
      }
    }
    html += "</div>";
  }

  bodyEl.innerHTML = total ? html : '<p class="picker-empty">没有匹配的选项</p>';
}

function open(button) {
  const host = button.closest("[data-picker-host]");
  const select = host && host.querySelector("select");
  if (!select) return;
  current = select;
  clearTimeout(closeTimer);

  const kind = select.dataset.pickerKind || "list";
  titleEl.textContent = host.dataset.pickerTitle || "选择";
  const searchable = kind === "type";
  searchWrap.hidden = !searchable;
  searchEl.value = "";
  render("");

  layer.hidden = false;
  button.setAttribute("aria-expanded", "true");
  // 双 rAF：`hidden` 解除与 transform 变化落在同一帧的话过渡不会触发
  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      layer.classList.add("is-open");
      bodyEl.scrollTop = 0;
      const on = bodyEl.querySelector(".is-on");
      if (on) {
        const box = bodyEl.getBoundingClientRect();
        const hit = on.getBoundingClientRect();
        bodyEl.scrollTop += hit.top - box.top - (box.height - hit.height) / 2;
      }
    })
  );
}

function close() {
  if (!layer || layer.hidden) return;
  layer.classList.remove("is-open");
  const button = current && current.closest("[data-picker-host]")?.querySelector(".picker-btn");
  if (button) button.setAttribute("aria-expanded", "false");
  current = null;
  clearTimeout(closeTimer);
  closeTimer = setTimeout(() => {
    layer.hidden = true;
  }, DURATION + 40);
}

/** 把 `<select>` 的当前值同步到替身按钮上。 */
export function syncPicker(select) {
  if (!select) return;
  const host = select.closest("[data-picker-host]");
  if (!host) return;
  const label = host.querySelector("[data-picker-value]");
  if (!label) return;
  const option = select.options[select.selectedIndex];
  label.textContent = option ? option.textContent : "";
  host.classList.toggle("is-empty", !select.value);
}

/** 批量同步（`fillForm` 之后必须调 —— 直接赋值 `select.value` 不发事件）。 */
export function syncPickers(root) {
  if (!root || !root.querySelectorAll) return;
  for (const select of root.querySelectorAll("select[data-picker-kind]")) syncPicker(select);
}

export function initPickers() {
  if (started) return;
  started = true;

  layer = document.createElement("div");
  layer.className = "picker-layer";
  layer.hidden = true;
  layer.innerHTML =
    '<div class="picker-scrim" data-picker-dismiss></div>' +
    '<div class="picker-panel" role="dialog" aria-modal="true" aria-labelledby="picker-title">' +
    '<div class="picker-head"><i class="picker-handle"></i>' +
    '<h3 id="picker-title"></h3>' +
    `<button type="button" class="picker-close" data-picker-dismiss aria-label="关闭">${icon("close")}</button>` +
    "</div>" +
    '<div class="picker-search" hidden><input type="search" placeholder="搜品类，例如「牛仔」" aria-label="搜索品类" autocomplete="off"></div>' +
    '<div class="picker-body" role="listbox"></div>' +
    "</div>";
  document.body.appendChild(layer);
  titleEl = layer.querySelector("#picker-title");
  searchWrap = layer.querySelector(".picker-search");
  searchEl = layer.querySelector("input");
  bodyEl = layer.querySelector(".picker-body");

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-picker-btn]");
    if (trigger) {
      // label 包着这个按钮：不挡掉的话浏览器会把点击转给被藏起来的 select
      event.preventDefault();
      open(trigger);
      return;
    }
    if (event.target.closest("[data-picker-dismiss]")) {
      event.preventDefault();
      close();
      return;
    }
    const choice = event.target.closest("[data-pick]");
    if (choice && current) {
      event.preventDefault();
      const select = current;
      select.value = choice.dataset.pick;
      syncPicker(select);
      select.dispatchEvent(new Event("change", { bubbles: true }));
      close();
    }
  });

  searchEl.addEventListener("input", () => render(searchEl.value));

  // 原生 select 自己变了（键盘 / 表单重置 / 别的代码赋值后派发 change）也要跟上
  document.addEventListener("change", (event) => {
    if (event.target.matches?.("select[data-picker-kind]")) syncPicker(event.target);
  }, true);
  document.addEventListener("reset", (event) => {
    const form = event.target;
    setTimeout(() => syncPickers(form), 0);
  }, true);

  // 新渲染出来的 select（首页表单、草稿恢复、详情重绘）自动对齐一次。
  // `fillForm` 直接赋值不发事件，那条路由它自己调 `syncPickers` 兜住。
  new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.matches?.("select[data-picker-kind]")) syncPicker(node);
        else node.querySelectorAll?.("select[data-picker-kind]").forEach(syncPicker);
      }
    }
  }).observe(document.body, { childList: true, subtree: true });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !layer || layer.hidden) return;
    // 别让全局的 Escape（关弹层 / 关详情）顺手把下面的页面也关了
    event.stopPropagation();
    event.preventDefault();
    close();
  }, true);
}
