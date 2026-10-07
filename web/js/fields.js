/** 属性表单：按 `/taxonomy` 渲染控件，并提供「读值 / 填值」。
 *
 * **录入页和详情页共用这一个模块** —— 两处各写一遍的话，
 * 迟早会出现"详情页能改版型、录入页忘了加"这类分裂。
 *
 * 控件选择：
 * - 单值字段（材质/版型/正式度/图案/主色）→ `<select>`
 * - 多值字段（颜色/风格/季节/场合）→ **chip 复选**，不用 `<select multiple>`
 *   （后者要按住 Ctrl 才能多选，难用且容易漏）
 */

import { esc, labelOf } from "./ui.js";

const SINGLE_FIELDS = [
  ["primary_color", "主色", "color"],
  ["material", "材质", "material"],
  ["fit", "版型", "fit"],
  ["formality", "正式度", "formality"],
  ["pattern", "图案", "pattern"],
];

const MULTI_FIELDS = [
  ["colors", "颜色", "color"],
  ["style", "风格", "style"],
  ["season", "季节", "season"],
  ["scene", "场合", "scene"],
];

/** 品类下拉，按槽位分组（50 个品类平铺太难找）。 */
export function typeOptionsHtml(taxonomy, placeholder = "全部品类") {
  const slots = (taxonomy && taxonomy.slots) || {};
  const types = (taxonomy && taxonomy.types) || {};
  const bySlot = new Map();
  for (const [key, info] of Object.entries(types)) {
    const slot = info.slot || "other";
    if (!bySlot.has(slot)) bySlot.set(slot, []);
    bySlot.get(slot).push([key, info.label]);
  }
  let html = `<option value="">${esc(placeholder)}</option>`;
  for (const [slot, items] of bySlot) {
    html += `<optgroup label="${esc(slots[slot] || slot)}">`;
    html += items
      .map(([k, lbl]) => `<option value="${esc(k)}">${esc(lbl)}</option>`)
      .join("");
    html += `</optgroup>`;
  }
  return html;
}

export function slotOptionsHtml(taxonomy, placeholder = "全部槽位") {
  const slots = (taxonomy && taxonomy.slots) || {};
  return (
    `<option value="">${esc(placeholder)}</option>` +
    Object.entries(slots)
      .map(([k, lbl]) => `<option value="${esc(k)}">${esc(lbl)}</option>`)
      .join("")
  );
}

function selectHtml(name, text, vocab, anyLabel = "未指定") {
  const opts =
    `<option value="">${esc(anyLabel)}</option>` +
    Object.entries(vocab || {})
      .map(([k, lbl]) => `<option value="${esc(k)}">${esc(lbl)}</option>`)
      .join("");
  return `<label class="field"><span>${esc(text)}</span>
    <select name="${esc(name)}">${opts}</select></label>`;
}

function chipsHtml(name, text, vocab) {
  const chips = Object.entries(vocab || {})
    .map(
      ([k, lbl]) =>
        `<label class="chip-toggle"><input type="checkbox" name="${esc(name)}" ` +
        `value="${esc(k)}"><span>${esc(lbl)}</span></label>`
    )
    .join("");
  return `<div class="field field-wide"><span>${esc(text)}</span>
    <div class="chips">${chips}</div></div>`;
}

/** 整张属性表单的 HTML（不含「保存」按钮）。 */
export function formHtml(taxonomy) {
  const vocab = (taxonomy && taxonomy.vocabularies) || {};
  return `
    <div class="form-grid">
      <label class="field"><span>品类</span>
        <select name="type">${typeOptionsHtml(taxonomy, "选一个品类")}</select></label>
      <label class="field"><span>名称</span>
        <input type="text" name="name" placeholder="例如：白色棉T恤"></label>
      ${SINGLE_FIELDS.map(([k, label, v]) => selectHtml(k, label, vocab[v])).join("")}
      ${MULTI_FIELDS.map(([k, label, v]) => chipsHtml(k, label, vocab[v])).join("")}
      <label class="field"><span>品牌</span><input type="text" name="brand"></label>
      <label class="field"><span>备注</span><input type="text" name="notes"></label>
    </div>
  `;
}

const TEXT_NAMES = [
  "type", "name", "brand", "notes",
  "primary_color", "material", "fit", "formality", "pattern",
];
const MULTI_NAMES = ["colors", "style", "season", "scene"];

/** 把表单读成 `POST /items` / `PATCH /items/{id}` 的请求体。
 *
 * @param root 表单容器
 * @param original 该单品**当前库里的值**（详情页必须传，缺了会丢数据）
 *
 * 两条踩过的规则，改之前先读：
 *
 * **1. 空字符串要发 `null`，不能省略。**
 * 后端靠 `exclude_unset` 判断"这个字段动不动"，**省略 = 不动**。
 * 所以"把品牌删掉"如果省略 `brand`，旧值会**原样复活**（W4 审查实测）。
 * 传 `null` 才是清空 —— 后端 `write.py`/`items.update()` 专门为此改了
 * `exclude_none` 的用法。
 *
 * **2. 多值字段要保留词表外的值。**
 * 勾选框由 `taxonomy` 词表生成；库里那些不在词表里的值（手工录入、
 * 词表改过、早期模型产出）没有对应勾选框。若直接 `= 勾选值`，
 * 一次"打开详情页什么都不改就保存"会**静默删掉它们**（W4 审查实测）。
 */
export function readForm(root, original = {}) {
  const payload = {};
  for (const name of TEXT_NAMES) {
    const el = root.querySelector(`[name="${name}"]`);
    // 界面上**根本没有这个字段**时不要写 null —— 那是"我表达不了它"，
    // 不等于"用户清空了它"。不加这个判断会静默清库里的值。
    if (!el) continue;
    const value = String(el.value).trim();
    // 空 → null（= 清空），而不是省略（= 不动）
    payload[name] = value || null;
  }
  for (const name of MULTI_NAMES) {
    const boxes = Array.from(root.querySelectorAll(`[name="${name}"]`));
    const known = new Set(boxes.map((el) => el.value));
    const checked = boxes.filter((el) => el.checked).map((el) => el.value);
    const checkedSet = new Set(checked);

    // 尽量**保持原有顺序**，让"打开详情页什么都不改就保存"成为一个
    // 真正的 no-op（数据一个字节都不变）。否则每次保存都会重排数组，
    // 用户看不出问题，但库里多了一堆无意义的 diff。
    const kept = [];
    for (const v of original[name] || []) {
      if (checkedSet.has(v)) {
        kept.push(v);
        checkedSet.delete(v);      // 已处理，别在末尾重复
      } else if (!known.has(v)) {
        // 界面上**完全没有**这个值的勾选框 —— 正常不会走到这里，因为 `fillForm`
        // 会给词表外的值动态补一个 chip。这是兜底：万一补 chip 失败
        // （比如找不到容器），也宁可保留而不是静默删掉。
        // 注意：用户取消勾选**动态 chip** 时走不到这里 —— 那个 chip 也是
        // `[name=...]` 的 DOM 元素，所以在 `known` 里，会被正常判为"取消了"。
        kept.push(v);
      }
      // 其余情况 = 用户主动取消勾选 → 丢弃
    }
    payload[name] = [...kept, ...checkedSet];
  }
  // 选了颜色却没点主色 → 取第一个兜底。
  //
  // ⚠️ 只在**原本没有主色**时做。理由：详情页里已经有主色的话，用户没动就不该被改
  // —— 否则"打开详情页什么都不做点保存"会静默写库（还会顺手改掉打标状态）。
  // 原本没有主色时补一次是**刻意的归一**（`colors` 有值而主色空属于半残数据，
  // 缺口分析的"颜色多样性"要用到主色）。
  // 实测确认：后端 `POST /items` **不会**做这个回填，所以只能在前端补。
  if (!payload.primary_color && payload.colors.length && !original.primary_color) {
    payload.primary_color = payload.colors[0];
  }
  return payload;
}

/** 把单品数据填回表单（详情页要用）。 */
export function fillForm(root, item) {
  for (const name of TEXT_NAMES) {
    const el = root.querySelector(`[name="${name}"]`);
    if (!el) continue;
    const value = item[name] === null || item[name] === undefined ? "" : String(item[name]);
    // ⚠️ **单值 select 遇到词表外的值时必须补一个 option。**
    // 否则浏览器会丢弃这次赋值（`el.value = '自定义紫'` 在没有该 option 时变成 `''`），
    // 保存时就被当成"用户清空了它" —— 实测：一个 `primary_color='自定义紫'`
    // 的单品，打开详情页什么都不改点保存，主色变成了 `null`。
    // 这和多值字段补 chip 是同一类问题的两个面。
    if (value && el.tagName === "SELECT" && !hasOption(el, value)) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = `${value}（不在词表里）`;
      el.append(opt);
    }
    el.value = value;
  }
  for (const name of MULTI_NAMES) {
    const boxes = Array.from(root.querySelectorAll(`[name="${name}"]`));
    const known = new Set(boxes.map((el) => el.value));
    const picked = new Set(item[name] || []);
    boxes.forEach((el) => {
      el.checked = picked.has(el.value);
    });

    // 词表外的值：界面上没有对应勾选框，**动态补一个**。
    // `readForm` 会保留它们，但"看不见的值"照样是坑 —— 用户会以为它丢了，
    // 也没法主动删掉它。补出来才能看见、能取消。
    const unknown = (item[name] || []).filter((v) => !known.has(v));
    const container = boxes.length ? boxes[0].closest(".chips") : null;
    if (!unknown.length || !container) continue;
    for (const value of unknown) {
      const label = document.createElement("label");
      label.className = "chip-toggle";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.name = name;
      input.value = value;
      input.checked = true;
      const span = document.createElement("span");
      // 用 textContent 而不是 innerHTML：这个值可能是手工录入的任意字符串
      span.textContent = value;
      span.title = "不在词表里（保存时会保留）";
      label.append(input, span);
      container.append(label);
    }
  }
}

/** select 里有没有这个 value 的 option。 */
function hasOption(select, value) {
  return Array.from(select.options).some((o) => o.value === value);
}

/** 只读的属性摘要（详情页顶部用）。 */
export function summaryHtml(item, taxonomy) {
  const vocab = (taxonomy && taxonomy.vocabularies) || {};
  const rows = [
    ["品类", item.type_label],
    ["颜色", (item.colors || []).map((c) => labelOf(vocab.color, c)).join(" / ")],
    ["材质", labelOf(vocab.material, item.material)],
    ["版型", labelOf(vocab.fit, item.fit)],
    ["正式度", labelOf(vocab.formality, item.formality)],
    ["图案", labelOf(vocab.pattern, item.pattern)],
    ["风格", (item.style || []).map((s) => labelOf(vocab.style, s)).join(" / ")],
    ["季节", (item.season || []).map((s) => labelOf(vocab.season, s)).join(" / ")],
    ["场合", (item.scene || []).map((s) => labelOf(vocab.scene, s)).join(" / ")],
    ["品牌", item.brand],
    ["备注", item.notes],
  ].filter(([, v]) => v);

  return (
    `<dl class="kv">` +
    rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("") +
    `</dl>`
  );
}
