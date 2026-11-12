import { esc, icon } from "../../core/ui.js";
import { referenceLabel, referenceRows } from "../purchase/reference.js";
import { seedReferences } from "../purchase/reference-store.js";

const headings = { outfit: "最近搭配", purchase: "最近想要添置" };

function dateLabel(value) {
  const date = new Date(value);
  return value && Number.isFinite(date.getTime()) ? `${date.getMonth() + 1}月${date.getDate()}日` : "";
}

function latestOutfit(data, api) {
  const outfit = (data.outfits || []).find(row =>
    row.item_ids?.length && row.item_ids.every(id => row.items?.some(item => item.id === id))
  );
  if (!outfit) return null;
  const names = outfit.items.map(item => item.name || item.type_label || "衣物");
  return {
    id: outfit.id,
    title: names.slice(0, 2).join(" × "),
    meta: [outfit.aesthetic?.name, dateLabel(outfit.created_at)].filter(Boolean).join(" · "),
    photos: outfit.items.map(item => api.presentationUrl(item)).filter(Boolean).slice(0, 3),
    glyph: "layers",
    pieces: names.join("、"),
    reason: outfit.reason,
  };
}

function wantedPurchases(data, api) {
  seedReferences(data.references);
  // 新记录使用保存时的图片。旧记录仍可从已有参考图读取。
  return [...(data.saved || [])].reverse().flatMap(plan => (plan.directions || []).map(direction => {
    const photo = referenceRows(direction, { preload: false })[0];
    const style = plan.aesthetic?.name || "添置方向";
    return {
      id: direction.id,
      planId: plan.id,
      title: direction.title,
      style,
      meta: [style, dateLabel(plan.saved_at)].filter(Boolean).join(" · "),
      photos: photo ? [api.imageUrl(photo.image_path)] : [],
      photoLabel: photo ? referenceLabel(direction) : "",
      glyph: "bookmark",
      reason: direction.reason,
      tips: [["颜色", direction.color], ["版型", direction.fit], ["外观", direction.texture]].filter(([, value]) => value),
      pairing: direction.pairing,
      caution: direction.caution,
    };
  }));
}

function photoPlaceholder(glyph) {
  return `<span class="today-recent-photo-placeholder">${icon(glyph)}</span>`;
}

function photosHtml(record, detail = false) {
  const layout = record.photos.length > 1 ? ` is-tiles is-${record.photos.length}` : "";
  return `<span class="today-recent-media${detail ? " is-detail" : ""}${layout}" aria-hidden="true">${record.photos.length
    ? record.photos.map(url => `<img src="${esc(url)}" alt="" decoding="async"${record.planId && !detail ? ' loading="lazy"' : ''} data-photo-fallback="${record.glyph}">`).join("")
    : photoPlaceholder(record.glyph)}</span>`;
}

function cardHtml(kind, record) {
  const wanted = kind === "purchase";
  const subtitle = wanted ? record.style : record.meta;
  return `<button type="button" class="${wanted ? "today-wanted-card" : "today-recent-card"}" data-today-recent="${kind}" data-recent-id="${esc(record.id)}" aria-haspopup="dialog">
    ${photosHtml(record)}
    <span class="today-recent-copy"><strong>${esc(record.title)}</strong>
      ${subtitle ? `<span>${esc(subtitle)}</span>` : ""}
    </span>${wanted ? "" : icon("right")}
  </button>`;
}

function detailHtml(record) {
  return `${photosHtml(record, true)}
    ${record.photoLabel ? `<p class="today-recent-photo-label">${esc(record.photoLabel)}</p>` : ""}
    <h3>${esc(record.title)}</h3>
    ${record.meta ? `<p class="today-recent-meta">${esc(record.meta)}</p>` : ""}
    ${record.pieces ? `<p>${esc(record.pieces)}</p>` : ""}
    ${record.reason ? `<p>${esc(record.reason)}</p>` : ""}
    ${record.tips?.length ? `<dl>${record.tips.map(([label, text]) =>
      `<div><dt>${label}</dt><dd>${esc(text)}</dd></div>`).join("")}</dl>` : ""}
    ${record.pairing ? `<p>${esc(record.pairing)}</p>` : ""}
    ${record.caution ? `<p class="today-recent-meta">${esc(record.caution)}</p>` : ""}
    ${record.planId ? '<p class="today-wanted-error" data-wanted-error role="status"></p><button type="button" class="today-wanted-remove" data-wanted-remove>取消想要</button>' : ""}`;
}

export function mountRecentPlans(host, api, signal) {
  host.innerHTML = `
    <section class="today-recent-section" aria-labelledby="today-recent-purchase">
      <div class="today-wanted-heading"><h2 id="today-recent-purchase">${headings.purchase}</h2><span data-wanted-count aria-live="polite"></span></div>
      <div class="today-wanted-grid" data-wanted-grid></div>
      <div class="today-wanted-empty" data-wanted-empty hidden>
        ${icon("bookmark")}<p>还没有想要的款式</p><a class="btn quiet blue" href="#gaps">去添置页看看</a>
      </div>
      <p class="today-wanted-status" data-wanted-status role="status">正在读取想要的款式…</p>
      <button type="button" class="quiet blue" data-wanted-retry hidden>重新读取</button>
    </section>
    <section class="today-recent-section" data-recent-outfit aria-labelledby="today-recent-outfit" hidden>
      <h2 id="today-recent-outfit">${headings.outfit}</h2>
      <div data-outfit-card></div>
    </section>
    <dialog class="today-recent-dialog" aria-labelledby="today-recent-dialog-title">
      <header><h2 id="today-recent-dialog-title"></h2>
        <button type="button" data-recent-close aria-label="关闭记录详情">${icon("close")}</button>
      </header>
      <div class="today-recent-detail"></div>
    </dialog>`;

  const dialog = host.querySelector(".today-recent-dialog");
  const detail = host.querySelector(".today-recent-detail");
  const grid = host.querySelector("[data-wanted-grid]");
  const status = host.querySelector("[data-wanted-status]");
  const retry = host.querySelector("[data-wanted-retry]");
  const empty = host.querySelector("[data-wanted-empty]");
  const hash = location.hash;
  const alive = () => !signal.aborted && host.isConnected;
  let outfit = null;
  let wanted = [];
  let trigger = null;
  let openRecord = null;
  let fallbackIndex = -1;
  let closing = false;
  let removing = false;
  const recordFor = (kind, id) => kind === "purchase" ? wanted.find(record => record.id === id) : outfit?.id === id ? outfit : null;

  function close() {
    if (!dialog.open) return;
    dialog.close();
    document.body.classList.remove("today-recent-open");
    if (alive() && location.hash === hash) {
      const fallback = grid.children[Math.min(fallbackIndex, grid.childElementCount - 1)] || empty.querySelector("a");
      (trigger?.isConnected ? trigger : fallback)?.focus({ preventScroll: true });
    }
    trigger = null;
    openRecord = null;
  }

  function syncDialog() {
    const marker = history.state?.todayRecent;
    const record = recordFor(marker?.kind, marker?.id);
    if (location.hash !== hash || !record) {
      close();
      return;
    }
    if (dialog.open) return;
    trigger = host.querySelector(`[data-today-recent="${marker.kind}"][data-recent-id="${CSS.escape(record.id)}"]`);
    openRecord = record;
    dialog.querySelector("h2").textContent = headings[marker.kind];
    detail.innerHTML = detailHtml(record);
    const remove = detail.querySelector("[data-wanted-remove]");
    if (remove) remove.disabled = removing;
    dialog.showModal();
    detail.scrollTop = 0;
    document.body.classList.add("today-recent-open");
  }

  function dismiss() {
    if (closing || !dialog.open) return;
    closing = true;
    history.back();
  }

  function renderWanted(data) {
    wanted = wantedPurchases(data, api);
    grid.innerHTML = wanted.map(record => cardHtml("purchase", record)).join("");
    host.querySelector("[data-wanted-count]").textContent = wanted.length + " 款";
    empty.hidden = wanted.length > 0;
    status.hidden = true;
    retry.hidden = true;
  }

  async function loadWanted() {
    status.hidden = false;
    status.textContent = "正在读取想要的款式…";
    retry.hidden = true;
    try {
      const data = await api.purchaseState();
      if (!alive()) return;
      renderWanted(data);
      syncDialog();
    } catch {
      if (!alive()) return;
      status.textContent = "暂时读不到想要的款式。";
      retry.hidden = false;
    }
  }

  async function removeWanted() {
    const record = openRecord;
    if (!record?.planId || removing || closing) return;
    removing = true;
    const button = detail.querySelector("[data-wanted-remove]");
    const message = detail.querySelector("[data-wanted-error]");
    button.disabled = true;
    message.textContent = "";
    try {
      const data = await api.purchaseChoice({ plan_id: record.planId, direction_id: record.id, action: "unsave" });
      if (!alive()) return;
      fallbackIndex = wanted.findIndex(item => item.id === record.id);
      renderWanted(data);
      if (openRecord === record) dismiss();
    } catch (error) {
      if (alive() && openRecord === record) message.textContent = error.message || "暂时无法取消想要，请重试。";
    } finally {
      removing = false;
      const current = detail.querySelector("[data-wanted-remove]");
      if (current) current.disabled = false;
    }
  }

  host.addEventListener("click", event => {
    const button = event.target.closest("[data-today-recent]");
    if (button) {
      const kind = button.dataset.todayRecent;
      const record = recordFor(kind, button.dataset.recentId);
      if (!record || dialog.open) return;
      history.pushState({ ...history.state, todayRecent: { kind, id: record.id } }, "", location.href);
      syncDialog();
    } else if (event.target.closest("[data-wanted-remove]")) void removeWanted();
    else if (event.target.closest("[data-wanted-retry]")) void loadWanted();
    else if (event.target.closest("[data-recent-close]")) dismiss();
    else if (event.target === dialog) {
      const { left, right, top, bottom } = dialog.getBoundingClientRect();
      if (event.clientX < left || event.clientX > right || event.clientY < top || event.clientY > bottom) dismiss();
    }
  }, { signal });
  host.addEventListener("error", event => {
    const glyph = event.target.dataset?.photoFallback;
    if (!glyph) return;
    if (detail.contains(event.target)) {
      const label = detail.querySelector(".today-recent-photo-label");
      if (label) label.hidden = true;
    }
    event.target.outerHTML = photoPlaceholder(glyph);
  }, { signal, capture: true });
  dialog.addEventListener("cancel", event => { event.preventDefault(); dismiss(); }, { signal });
  window.addEventListener("popstate", () => { closing = false; syncDialog(); }, { signal });
  signal.addEventListener("abort", close, { once: true });

  void api.listOutfits(20).then(data => {
    if (!alive()) return;
    outfit = latestOutfit(data, api);
    if (!outfit) return;
    const section = host.querySelector("[data-recent-outfit]");
    section.querySelector("[data-outfit-card]").innerHTML = cardHtml("outfit", outfit);
    section.hidden = false;
    syncDialog();
  }).catch(() => {});
  void loadWanted();
}
