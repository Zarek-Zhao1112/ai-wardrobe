import { esc, icon } from "../../core/ui.js";
import { bindHorizontalSwipe } from "../../components/horizontal-swipe.js";
import { createAestheticSheet } from "../outfits/aesthetic-sheet.js";
import { coverImage } from "../outfits/aesthetic-ui.js";
import { mountRecentPlans } from "./recent.js";

const artwork = `<svg class="today-artwork" viewBox="0 0 350 165" aria-hidden="true" focusable="false">
  <ellipse cx="175" cy="82" rx="102" ry="62" transform="rotate(-16 175 82)" fill="none" stroke="white"/>
  <g transform="translate(210 87) rotate(13)">
    <rect class="today-art-paper" x="-42" y="-56" width="84" height="112" rx="10"/>
    <rect class="today-art-blue" x="-36" y="-50" width="72" height="89" rx="6"/>
    <path fill="#53708e" d="M-16-38h32l6 66H5L0-10l-5 38h-17z"/>
    <path class="today-art-rule" d="M-9 47H9"/>
  </g>
  <g transform="translate(147 79) rotate(-11)">
    <rect class="today-art-paper" x="-42" y="-56" width="84" height="112" rx="10"/>
    <rect class="today-art-gray" x="-36" y="-50" width="72" height="89" rx="6"/>
    <path fill="#7b8594" d="m-7-31-15 5-10 25 12 5 5-11v27h30V-7l5 11 12-5-10-25-15-5a7 7 0 0 1-14 0Z"/>
    <path fill="#636e7c" d="M-15 15h30v5h-30z"/>
    <path class="today-art-rule" d="M-9 47H9"/>
  </g>
  <path class="today-art-spark" d="M82 39q0 9 9 9-9 0-9 9 0-9-9-9 9 0 9-9Zm192 63q0 6 6 6-6 0-6 6 0-6-6-6 6 0 6-6Z"/>
</svg>`;

let active = null;
let railPosition = 0;

export function unmount(root) {
  if (active?.root !== root) return;
  active.dispose();
  active = null;
}

export async function mount(root, { api }) {
  root.innerHTML = `<div class="today-view">
    <section class="today-hero" aria-labelledby="today-heading">
      <div class="today-art">${artwork}</div>
      <div class="today-hero-copy">
        <h1 id="today-heading">今天穿什么？</h1>
        <p>从喜欢的风格开始，挑一套适合今天的。</p>
        <a class="btn primary today-primary" href="#outfits">选一套${icon("right")}</a>
      </div>
    </section>
    <section class="today-styles" aria-labelledby="today-styles-title">
      <h2 id="today-styles-title">找找喜欢的风格</h2>
      <div class="today-style-rail" role="group" aria-label="左右滑动浏览审美风格"></div>
      <p class="muted today-style-status" role="status">正在读取风格…</p>
    </section>
    <div class="today-recent"></div>
  </div>`;
  const rail = root.querySelector(".today-style-rail");
  const status = root.querySelector(".today-style-status");
  const controller = new AbortController();
  const { signal } = controller;
  mountRecentPlans(root.querySelector(".today-recent"), api, signal);
  const alive = () => !signal.aborted && root.isConnected;
  let sheet = null;
  const moveRail = direction => {
    const card = rail.firstElementChild;
    if (!card) return;
    const gap = Number.parseFloat(getComputedStyle(rail).columnGap) || 0;
    rail.scrollBy({
      left: direction * (card.getBoundingClientRect().width + gap),
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"
    });
  };
  const swipe = bindHorizontalSwipe(rail, { selector: "[data-today-style]", enabled: alive, onSwipe: moveRail });
  active = { root, dispose() {
    if (rail.childElementCount) railPosition = rail.scrollLeft;
    controller.abort();
    swipe.dispose();
    sheet?.dispose();
  } };
  rail.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const card = event.target.closest("[data-today-style]");
    const next = event.key === "ArrowRight" ? card?.nextElementSibling : card?.previousElementSibling;
    if (!next) return;
    event.preventDefault();
    next.focus();
  }, { signal });
  try {
    const catalog = await api.aesthetics();
    if (!alive()) return;
    const profiles = catalog.aesthetics || [];
    const first = profiles.find(profile => profile.id === catalog.default);
    const ordered = first ? [first, ...profiles.filter(profile => profile !== first)] : profiles;
    sheet = createAestheticSheet({
      root, profiles, useLabel: "按这个风格搭配",
      onUseProfile: profile => { location.hash = "outfits/style/" + encodeURIComponent(profile.id); }
    });
    rail.innerHTML = ordered.map(profile => {
      const cover = coverImage(profile);
      return `<button type="button" class="today-style-card" data-today-style="${esc(profile.id)}" aria-haspopup="dialog">
        ${cover ? `<img src="${esc(cover)}" alt="" decoding="async" draggable="false">` : ""}
        <span class="today-style-name">${esc(profile.name)}${icon("chevron")}</span>
      </button>`;
    }).join("");
    if (profiles.length) {
      status.hidden = true;
      rail.scrollLeft = railPosition;
    } else status.textContent = "暂时没有可浏览的风格。";
    rail.addEventListener("click", event => {
      const card = event.target.closest("[data-today-style]");
      const profile = profiles.find(row => row.id === card?.dataset.todayStyle);
      if (profile && alive()) sheet.openGuide(profile, card);
    }, { signal });
  } catch {
    if (!alive()) return;
    status.textContent = "风格暂时没有读到。";
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "quiet blue";
    retry.textContent = "重新读取";
    status.after(retry);
    retry.addEventListener("click", () => { unmount(root); void mount(root, { api }); }, { signal });
  }
}
