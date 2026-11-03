/** §34：审美样片用于理解方法，与真实衣橱结果分开展示。 */
import { esc, icon } from "../../core/ui.js";
const STATUS = { matched: "有依据", approximate: "近似尝试", unknown: "信息不足" };
export function examplesFor(profile) {
  return (profile?.examples || []).filter(row => row.kind === "ai_example" &&
    /^\/reference\/aesthetics\/[a-zA-Z0-9_-]+\.webp$/.test(row.image || "")).slice(0, 3);
}
export function arrangementFor(profile) {
  return profile?.arrangement?.length ? profile.arrangement :
    (profile?.dimensions || []).map(row => ({ label: row.label, goal: row.goal }));
}
/** 只取校验过的封面地址，拿不到就返回空串 —— **绝不回退到示例人像**。
 *  添置页用它：那里若回退，卡片又会冒出模特，等于把刚撤掉的视觉放回来。 */
export function coverImage(profile) {
  const cover = profile?.cover;
  return typeof cover === "string" && /^\/reference\/aesthetics\/[a-zA-Z0-9_-]+\.webp$/.test(cover) ? cover : "";
}
/** 横条缩略图：封面下方「鞋 + 裤脚」的局部，给 74×52 那种横向格子用。
 *  ⚠️ §38 起**不在渲染路径上** —— 添置行改回整套竖封面（60×87）了。
 *  留着是因为 `thumb-<id>.webp` 六张图还在库里（111 KB），哪天又要横格子直接能用；
 *  调用方为 0，改它不会影响任何页面。 */
export function thumbImage(profile) {
  const thumb = profile?.thumb;
  if (typeof thumb === "string" && /^\/reference\/aesthetics\/[a-zA-Z0-9_-]+\.webp$/.test(thumb)) return thumb;
  return coverImage(profile);
}
/** 方向封面：平铺静物。没有 cover 的历史快照回退到第一张示例，行为与旧版一致。 */
export function coverFor(profile) {
  return coverImage(profile) || examplesFor(profile)[0]?.image || "";
}
export function rulesHtml(profile) {
  return `<dl class="aesthetic-rules">${arrangementFor(profile).map(row => `<div><dt>${esc(row.label)}</dt><dd>${esc(row.goal)}</dd></div>`).join("")}</dl>`;
}
export function profileCardHtml(profile) {
  const cover = coverFor(profile);
  // 卡片不列配色条：`_color_score` 判的是 20 个颜色**名字**（黑/米白/藏蓝…），
  // 而配色条画的是从封面上量出来的 hex，两者没有任何连接。摆出来等于承诺一个
  // 不存在的功能，所以撤掉。封面色调本身已经在图里，不需要再做一次转述。
  return `<section class="outfit-profile-card"><p class="aesthetic-eyebrow">当前审美${cover ? " · 质感" : ""}</p><h2>${esc(profile.name)}</h2>
    <p class="outfit-profile-core">${esc(profile.core || profile.summary)}</p>
    ${cover ? `<button type="button" class="outfit-profile-cover" data-aesthetic-guide aria-label="认识${esc(profile.name)}"><img src="${esc(cover)}" alt="" decoding="async"></button>` : ""}
    <div class="chips">${(profile.points || []).map(point => `<span class="chip">${esc(point)}</span>`).join("")}</div>
    <div class="outfit-profile-learn"><span>配色、轮廓、质感一起安排</span><button type="button" data-aesthetic-guide>认识这个审美${icon("right")}</button></div></section>`;
}
export function methodDetails(profile, assessment = null, opened = false) {
  if (!profile) return '<p class="faint">旧版搭配，未记录审美方案。</p>';
  return `<details class="aesthetic-details" ${opened ? "open" : ""}><summary>${assessment ? "查看搭配依据" : "查看完整审美方案"}</summary>
    <p>${esc(profile.core)}</p><dl>${(profile.dimensions || []).map(dimension => {
      const actual = assessment?.dimensions?.find(row => row.key === dimension.key);
      return `<div><dt>${esc(dimension.label)}${actual ? `<span class="chip">${esc(STATUS[actual.status] || "待确认")}</span>` : ""}</dt><dd>${esc(dimension.goal)}${actual ? `<p>${esc(actual.evidence)}</p>` : ""}</dd></div>`;
    }).join("")}</dl><p class="faint">${esc(profile.flexible)}</p>${assessment ? `<p class="faint">${esc(assessment.note)}</p>` : ""}
    <div class="aesthetic-sources">${(profile.sources || []).filter(source => /^https:\/\//.test(source.url || "")).map(source => `<a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.title)}</a>`).join("")}</div></details>`;
}

