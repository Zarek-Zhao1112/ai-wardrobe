import { bindHorizontalSwipe } from "./horizontal-swipe.js";
import { mountCoverBleed } from "./cover-bleed.js";

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

/** §52：两页共用风格卡的摆放、手势和键盘操作，选中状态由调用方保存。 */
export function mountStyleDeck(deck, { index = 0, enabled = () => true, onChange, onMoveStart }) {
  const cards = [...deck.children];
  const count = cards.length;
  const loop = count >= 4;
  const wrap = value => ((value % count) + count) % count;
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const abort = new AbortController();
  let active = 0;
  let position = 0;
  let frame = 0;
  let dragging = false;

  deck.classList.add("is-stacked");
  deck.classList.toggle("is-single", count === 1);
  mountCoverBleed(deck, abort.signal);

  function layout(next) {
    position = next;
    cards.forEach((card, i) => {
      // 循环时在已经淡出的区间换边，避免露在后排的卡片突然跳位。
      const distance = loop ? wrap(i - position + 1.01) - 1.01 : i - position;
      const behind = Math.max(0, distance);
      const opacity = clamp(Math.min(1 + distance, (1.75 - behind) * 1.6), 0, 1);
      const x = distance < 0 ? distance * 107 : behind * 12;
      card.style.transform = `translateX(${x}%) scale(${1 - behind * .065})`;
      card.style.opacity = String(opacity);
      card.style.zIndex = String(Math.round(40 - distance * 10));
      card.style.setProperty("--cover-brightness", String(1 - behind * .07));
    });
  }

  function stopAnimation() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
  }

  function select(next) {
    stopAnimation();
    dragging = false;
    active = clamp(next, 0, count - 1);
    cards.forEach((card, i) => {
      const current = i === active;
      card.setAttribute("aria-hidden", String(!current));
      card.toggleAttribute("inert", !current);
      card.querySelectorAll("button").forEach(button => { button.tabIndex = current ? 0 : -1; });
    });
    layout(active);
    onChange?.(active);
  }

  function settle(next) {
    stopAnimation();
    onMoveStart?.();
    const from = position;
    const selected = loop ? wrap(next) : next;
    if (reducedMotion.matches || Math.abs(next - from) < .01) {
      select(selected);
      return;
    }
    const start = performance.now();
    function tick(now) {
      const progress = clamp((now - start) / 300, 0, 1);
      if (progress === 1) {
        select(selected);
        return;
      }
      layout(from + (next - from) * (1 - (1 - progress) ** 3));
      frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
  }

  function move(direction) {
    const next = active + direction;
    settle(loop ? next : clamp(next, 0, count - 1));
  }

  const canMove = () => count > 1 && enabled();
  const swipe = bindHorizontalSwipe(deck, {
    selector: ".outfit-cover-photo",
    enabled: canMove,
    onSwipe: move,
    onDrag(_photo, _offset, dx) {
      if (dx === undefined) {
        if (dragging || position !== active) settle(active);
        return;
      }
      if (reducedMotion.matches || !deck.clientWidth) return;
      if (!dragging) onMoveStart?.();
      dragging = true;
      stopAnimation();
      const progress = clamp(-dx / (deck.clientWidth * .64), -.95, .95);
      const next = active + progress;
      layout(loop ? next : clamp(next, 0, count - 1));
    }
  });
  deck.addEventListener("keydown", event => {
    if (!canMove() || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    deck.focus({ preventScroll: true });
    if (!frame) move(event.key === "ArrowRight" ? 1 : -1);
  }, { signal: abort.signal });
  deck.addEventListener("click", event => {
    if (!frame && !event.target.closest('.outfit-cover-card[aria-hidden="true"]')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, { capture: true, signal: abort.signal });

  select(index);
  return {
    select(next) {
      swipe.cancel();
      select(next);
    },
    dispose() {
      swipe.dispose();
      stopAnimation();
      abort.abort();
    }
  };
}
