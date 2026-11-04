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
/* ---------- 一屏一张的审美卡：搭配页与添置页共用 ----------
 * 两个页面的**主动作不同**（搭配页＝生成搭配，添置页＝按它分析衣橱），
 * 但卡长一样，所以只把文案参数化，结构与样式共用一份。
 * ⚠️ 两个 data 属性名是全站约定，事件由各自页面绑：
 *    data-aesthetic-open  = 卡面按钮（主动作）
 *    data-aesthetic-guide = 「认识这个」（进 guide 帧）
 * ⚠️ 样式在 css/features/outfits-deck.css（裸类名，不挂在 .outfit-view 下），
 *    添置页直接套同一个类名即可，别复制第二份。 */
export function aestheticCardHtml(profile, { hint = "点这里生成搭配", openLabel = "", showCore = true, showLearn = true, actions = "" } = {}) {
  const cover = coverFor(profile);
  const label = openLabel || `用${profile.name}生成搭配`;
  // showCore=false：卡片上不放那句 core。§42 添置页用它换 49px 给封面
  //   —— 511 = 354 × 1299/900 正好是封面原比例，图框上下一个像素都不裁。
  //   信息不丢：methodDetails()（「认识这个」点开的详情）第一行本来就是同一个 core。
  const core = showCore ? '<p>' + esc(profile.core || profile.summary || "") + '</p>' : "";
  /* §44：可选的「卡面操作条」。传 actions（一串 <button>）时，卡面外面套一层 stage，
   * 操作条绝对定位压在封面底部 —— 不占卡片高度，所以封面能一直长到原图比例。
   * ⚠️ 操作条必须是 .outfit-aesthetic-face 的**兄弟**，不能塞进它里面：
   *    face 本身就是 <button>，按钮套按钮会被 HTML 解析器拆开，布局直接散。
   * ⚠️ 不传 actions 时 DOM 与旧版逐字节一致（不套 stage），搭配页不受影响。 */
  const face = `<button type="button" class="outfit-aesthetic-face" data-aesthetic-open="${esc(profile.id)}" aria-label="${esc(label)}">
        ${cover ? `<img src="${esc(cover)}" alt="" decoding="async">` : `<span class="ph">暂无意向图</span>`}
        <span class="outfit-aesthetic-hint">${icon("spark")}${esc(hint)}</span>
      </button>`;
  // showLearn=false：把「认识这个」让给卡面操作条，文案行只剩 chips。
  const learn = showLearn ? `<button type="button" class="outfit-aesthetic-learn" data-aesthetic-guide="${esc(profile.id)}">认识这个${icon("right")}</button>` : "";
  return `<article class="outfit-aesthetic-card" data-aesthetic-card="${esc(profile.id)}">
      ${actions ? `<div class="outfit-aesthetic-stage">${face}<div class="outfit-aesthetic-actions">${actions}</div></div>` : face}
      <div class="outfit-aesthetic-copy"><h2>${esc(profile.name)}</h2>${core}
        <div class="outfit-aesthetic-row"><div class="chips">${(profile.points || []).slice(0, 3).map(point => `<span class="chip">${esc(point)}</span>`).join("")}</div>${learn}</div>
      </div></article>`;
}
/** 圆点：44×44 的按钮包 6px 的条 —— 契约 §4 量的是按钮，不是条。
 *  §42 起圆点右侧可以带一枚「1/6」页码：只有添置页传 `{label:true}`，
 *  搭配页不传就还是原来的纯圆点（那边一屏能滑好几张，页码意义不大）。 */
export function aestheticDotsHtml(profiles, active = 0, { label = false } = {}) {
  const dots = profiles.map((profile, index) => `<button type="button" data-aesthetic-dot="${index}" aria-pressed="${index === active}" aria-label="第 ${index + 1} 个方向：${esc(profile.name)}"><i></i></button>`).join("");
  return label ? dots + `<span class="label" data-aesthetic-page>${active + 1}/${profiles.length}</span>` : dots;
}
export function rulesHtml(profile) {
  return `<dl class="aesthetic-rules">${arrangementFor(profile).map(row => `<div><dt>${esc(row.label)}</dt><dd>${esc(row.goal)}</dd></div>`).join("")}</dl>`;
}
/* §40 起没有「当前审美」大卡了：第一层是一屏一张的审美卡，点开是堆叠结果，
 * 所以原来的 profileCardHtml 已删除。它当初撤掉配色条的理由仍然成立，记在这里：
 * `_color_score` 判的是 20 个颜色**名字**，而配色条画的是从封面上量出来的 hex，
 * 两者没有任何连接，摆出来等于承诺一个不存在的功能。封面色调本身已经在图里。 */
export function methodDetails(profile, assessment = null, opened = false) {
  if (!profile) return '<p class="faint">旧版搭配，未记录审美方案。</p>';
  return `<details class="aesthetic-details" ${opened ? "open" : ""}><summary>${assessment ? "查看搭配依据" : "查看完整审美方案"}</summary>
    <p>${esc(profile.core)}</p><dl>${(profile.dimensions || []).map(dimension => {
      const actual = assessment?.dimensions?.find(row => row.key === dimension.key);
      return `<div><dt>${esc(dimension.label)}${actual ? `<span class="chip">${esc(STATUS[actual.status] || "待确认")}</span>` : ""}</dt><dd>${esc(dimension.goal)}${actual ? `<p>${esc(actual.evidence)}</p>` : ""}</dd></div>`;
    }).join("")}</dl><p class="faint">${esc(profile.flexible)}</p>${assessment ? `<p class="faint">${esc(assessment.note)}</p>` : ""}
    <div class="aesthetic-sources">${(profile.sources || []).filter(source => /^https:\/\//.test(source.url || "")).map(source => `<a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.title)}</a>`).join("")}</div></details>`;
}

