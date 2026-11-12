/** §55：添置主卡轮播随应用提供的整套穿搭。 */
const LOOKBOOK_STYLES = new Set(["natural", "urban", "heritage_v2", "street_v2", "technical", "refined"]);
const LOOKBOOK_FRAMES = [1, 2, 3];
const INTERVAL_MS = 3500;
const SETTLE_MS = 800;

export function purchaseLookPresentation(style) {
  if (!LOOKBOOK_STYLES.has(style)) return null;
  const photos = LOOKBOOK_FRAMES.map(number => `<div class="outfit-cover-image purchase-look-frame" data-purchase-look>
      <img src="/reference/purchase-lookbook/${style}-${number}.webp" alt="" decoding="async" draggable="false">
    </div>`).join("");
  const dots = `<div class="outfit-cover-dots purchase-look-dots" data-purchase-look-dots role="img" aria-label="整套穿搭示意">${LOOKBOOK_FRAMES.map(() => '<i aria-hidden="true"></i>').join("")}</div>`;
  return { photos, dots };
}

export function mountPurchaseLookbook(deck, { positions, isCovered }) {
  const abort = new AbortController();
  const { signal } = abort;
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  let active = -1;
  let moving = false;
  let timer = 0;
  let inView = !globalThis.IntersectionObserver;

  const cards = [...deck.children].map(card => {
    const frames = [...card.querySelectorAll("[data-purchase-look]")];
    const dots = card.querySelector("[data-purchase-look-dots]");
    const style = card.dataset.aestheticCard;
    let current = -1;
    if (!frames.length) return null;
    const images = frames.map(frame => frame.querySelector("img"));
    const ready = index => images[index]?.complete && images[index].naturalWidth > 0;

    function show(index) {
      if (index === current) return;
      frames.forEach((frame, i) => {
        frame.toggleAttribute("data-previous", i === current);
        frame.toggleAttribute("data-current", i === index);
        dots.children[i].classList.toggle("is-active", i === index);
      });
      current = index;
      positions.set(style, current);
      dots.setAttribute("aria-label", `第 ${current + 1} 张搭配，共 ${frames.length} 张`);
    }

    const initial = positions.get(style) ?? 0;
    const showInitial = () => {
      if (current < 0 && ready(initial)) show(initial);
    };
    images[initial].addEventListener("load", showInitial, { signal });
    showInitial();

    return {
      advance() {
        // 图片还在加载或读取失败时，继续显示已就绪的图。
        for (let offset = 1; offset <= frames.length; offset++) {
          const next = (current + offset) % frames.length;
          if (ready(next)) {
            show(next);
            break;
          }
        }
      },
    };
  });

  function stop() {
    clearTimeout(timer);
    timer = 0;
  }

  function canPlay() {
    return !signal.aborted && deck.isConnected && cards[active] && !moving &&
      inView && !document.hidden && !reducedMotion.matches && !isCovered();
  }

  function schedule(delay = 0) {
    timer = setTimeout(() => {
      timer = 0;
      if (!canPlay()) return;
      cards[active].advance();
      schedule();
    }, INTERVAL_MS + delay);
  }

  function sync() {
    if (!canPlay()) stop();
    else if (!timer) schedule(SETTLE_MS);
  }

  const visibility = globalThis.IntersectionObserver ? new IntersectionObserver(([entry]) => {
    inView = entry.isIntersecting;
    sync();
  }) : null;
  visibility?.observe(deck);
  // 添置助手和风格说明等弹层会作为 body 的直属子元素挂载。
  const overlays = new MutationObserver(sync);
  overlays.observe(document.body, { childList: true });
  document.addEventListener("visibilitychange", sync, { signal });
  reducedMotion.addEventListener("change", sync, { signal });

  return {
    select(index) {
      active = index;
      moving = false;
      stop();
      sync();
    },
    pauseForMove() {
      moving = true;
      stop();
    },
    dispose() {
      abort.abort();
      overlays.disconnect();
      visibility?.disconnect();
      stop();
    },
  };
}
