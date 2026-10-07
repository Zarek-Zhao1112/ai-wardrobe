/** 缺口分析面板：这季能搭多少套、断在哪、补什么最划算。
 *
 * 「补什么最划算」用的是 `ΔC × 多样性折扣`，不是单纯的"件数最少"。
 * 原因：纯乘法模型下"少一件的相对跌幅"恒等于 1/该槽位件数，
 * 所以朴素瓶颈指标必然退化成"件数最少的槽位" —— 那个结论没信息量。
 */

import { api } from "./api.js";
import { esc, renderError } from "./ui.js";

export const label = "缺口";

function seasonOptionsHtml(taxonomy) {
  const seasons = (taxonomy.vocabularies && taxonomy.vocabularies.season) || {};
  return (
    `<option value="">当前季节</option>` +
    Object.entries(seasons)
      .map(([k, lbl]) => `<option value="${esc(k)}">${esc(lbl)}</option>`)
      .join("")
  );
}

export async function mount(root, ctx) {
  const taxonomy = ctx.taxonomy || {};
  const state = { season: "" };

  root.innerHTML = `
    <div class="view-head"><h1>缺口分析</h1><span class="faint" id="g-season"></span></div>
    <div class="toolbar">
      <label>季节</label>
      <select id="g-season-sel">${seasonOptionsHtml(taxonomy)}</select>
      <span class="spacer"></span>
      <button id="g-reload" class="ghost">刷新</button>
    </div>
    <div id="g-body"><p class="muted">加载中…</p></div>
  `;

  const bodyEl = root.querySelector("#g-body");
  const seasonLabelEl = root.querySelector("#g-season");
  const selEl = root.querySelector("#g-season-sel");

  selEl.addEventListener("change", () => {
    state.season = selEl.value;
    load();
  });
  root.querySelector("#g-reload").addEventListener("click", () => load());

  function stat(value, text) {
    return `<div class="stat"><span class="stat-num">${esc(value)}</span>
      <span class="stat-label">${esc(text)}</span></div>`;
  }

  function render(d) {
    const counts = d.counts || {};
    const slots = taxonomy.slots || {};
    const total = d.total_combinations || 0;
    const usable = Object.values(counts).reduce((a, b) => a + (b || 0), 0);
    const never = d.never_worn || [];
    const picks = d.slot_suggestions || [];

    seasonLabelEl.textContent = `季节：${d.season_label || d.season || "—"}`;

    const countChips = Object.entries(slots)
      .map(([k, lbl]) => `<span class="chip">${esc(lbl)} ${counts[k] || 0}</span>`)
      .join("");

    const suggestionRows = picks.length
      ? picks
          .map(
            (s) => `<tr>
              <td>${esc(s.slot_label)}</td>
              <td>${esc(s.current_count)}</td>
              <td>+${esc(s.delta)} 套</td>
              <td>${Math.round((s.diversity_discount || 0) * 100)}%</td>
              <td>${Math.round((s.score || 0) * 10) / 10}</td>
            </tr>`
          )
          .join("")
      : `<tr><td colspan="5" class="faint">没有可算的建议（衣橱太小或已断链）</td></tr>`;

    bodyEl.innerHTML = `
      <div class="stat-row">
        ${stat(total, "当季可搭套数")}
        ${stat(usable, "当季可用单品")}
        ${stat(never.length, "从没穿过")}
      </div>

      ${
        d.min_viable
          ? ""
          : `<div class="alert">这季<strong>搭不出任何一套</strong> —— 断链的槽位：
             ${esc((d.blocking_labels || []).join("、") || "基础单品")}。
             至少要「一件上装 + 一件下装 + 一双鞋」或「一件连身 + 一双鞋」。</div>`
      }

      <div class="panel">
        <h2>各槽位件数</h2>
        <div class="chips">${countChips}</div>
      </div>

      <div class="panel">
        <h2>补什么最划算</h2>
        <table class="tbl">
          <thead><tr>
            <th>槽位</th><th>现有</th><th>每多一件</th><th>颜色多样性</th><th>得分</th>
          </tr></thead>
          <tbody>${suggestionRows}</tbody>
        </table>
        <p class="faint" style="margin:10px 0 0">
          「颜色多样性」越低，说明同色重复越多 —— 再补同色的收益会打折。
        </p>
      </div>

      <div class="panel">
        <h2>建议</h2>
        <ul class="bullets">
          ${(d.suggestions || []).map((s) => `<li>${esc(s)}</li>`).join("") || `<li class="faint">暂无</li>`}
        </ul>
      </div>

      ${
        never.length
          ? `<div class="panel">
               <h2>从没穿过的（${never.length}）</h2>
               <div class="chips">
                 ${never
                   .slice(0, 30)
                   .map(
                     (i) =>
                       `<a class="chip chip-link" href="#item/${esc(i.id)}">${esc(
                         i.name || i.type_label || "未命名"
                       )}</a>`
                   )
                   .join("")}
               </div>
             </div>`
          : ""
      }

      <details class="panel">
        <summary>旧规则（硬编码白名单）会怎么说 —— 对照</summary>
        <p class="faint" style="margin:10px 0 0">${esc(d.legacy_rule_verdict || "")}</p>
        <p class="faint">
          上面那条规则只认 <code>shirt/blouse/t-shirt/top</code> 和
          <code>pants/jeans/skirt/shorts</code>；毛衣、卫衣、外套、连衣裙全不算数，
          所以「20 件毛衣 + 2 条裤子」它一声不吭。
        </p>
      </details>
    `;
  }

  /** 竞态：动一下季节下拉就可能让两个请求同时在飞，先发的慢请求后回来会
   * 覆盖新结果（界面显示的季节与数据对不上，静默错）。用序号只认最后一次。 */
  let reqSeq = 0;

  async function load() {
    const seq = ++reqSeq;
    bodyEl.innerHTML = `<p class="muted">加载中…</p>`;
    try {
      const data = await api.gaps(state.season || undefined);
      if (seq !== reqSeq) return;
      render(data);
    } catch (err) {
      if (seq !== reqSeq) return;
      seasonLabelEl.textContent = "";
      renderError(bodyEl, err);
    }
  }

  await load();
}
