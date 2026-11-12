/** §56：按原图边缘的色调和底纹填满卡面，主图仍用原始文件显示。 */
function samplePhoto(image) {
  const scale = Math.min(1, 360 / Math.max(image.naturalWidth, image.naturalHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  const context = canvas.getContext("2d");
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  return context.getImageData(0, 0, canvas.width, canvas.height);
}

function makeEdge(photo, inline, before) {
  const length = inline ? photo.height : photo.width;
  const depth = inline ? photo.width : photo.height;
  const thickness = Math.max(24, Math.round(length * (before ? .20 : .30)));
  const canvas = document.createElement("canvas");
  canvas.width = inline ? thickness : length;
  canvas.height = inline ? length : thickness;
  const context = canvas.getContext("2d");
  const band = context.createImageData(canvas.width, canvas.height);
  const pixel = (along, distance) => {
    const across = before ? distance : depth - 1 - distance;
    return (inline ? along * photo.width + across : across * photo.width + along) * 4;
  };
  const smoothed = new Float32Array(length * 3);
  const radius = Math.max(1, Math.round(length * .032));
  for (let x = 0; x < length; x++) {
    const first = Math.max(0, x - radius);
    const last = Math.min(length - 1, x + radius);
    for (let at = first; at <= last; at++) {
      const source = pixel(at, 0);
      for (let channel = 0; channel < 3; channel++) {
        smoothed[x * 3 + channel] += photo.data[source + channel] / (last - first + 1);
      }
    }
  }
  const left = Math.floor((length - 1) * .12) * 3;
  const right = Math.ceil((length - 1) * .88) * 3;
  for (let y = 0; y < thickness; y++) {
    const distance = before ? thickness - 1 - y : y;
    const exactWeight = Math.max(0, 1 - distance / (length * .028)) ** 2;
    const shadowWeight = Math.exp(-distance / (length * .032));
    const grainDepth = Math.min(depth - 1, 8 + distance % 29);
    for (let x = 0; x < length; x++) {
      const grainAlong = Math.min(length - 1, 6 + (x + Math.floor(x / 27) * 13 + Math.floor(distance / 29) * 7) % 27);
      const grain = Math.max(-4, Math.min(4, (
        photo.data[pixel(grainAlong, grainDepth)] -
        photo.data[pixel(grainAlong, Math.min(depth - 1, grainDepth + 2))]
      ) * .5));
      const source = pixel(x, 0);
      const target = (inline ? x * thickness + y : y * length + x) * 4;
      const position = x / Math.max(1, length - 1);
      for (let channel = 0; channel < 3; channel++) {
        const background = smoothed[left + channel] * (1 - position) + smoothed[right + channel] * position;
        const extended = background + shadowWeight * (smoothed[x * 3 + channel] - background) + grain;
        band.data[target + channel] = exactWeight * photo.data[source + channel] + (1 - exactWeight) * extended;
      }
      band.data[target + 3] = 255;
    }
  }
  context.putImageData(band, 0, 0);
  return canvas.toDataURL();
}

export function mountCoverBleed(deck, signal) {
  const card = deck.firstElementChild;
  const layers = [...deck.querySelectorAll(".outfit-cover-image")].map(layer => ({
    layer, image: layer.querySelector("img"), axis: "", revision: 0,
  }));
  if (!card || !layers.length) return;

  async function loadEdges(entry, axis, inline) {
    const { layer, image } = entry;
    const revision = ++entry.revision;
    delete layer.dataset.coverBleed;
    try {
      const photo = samplePhoto(image);
      const urls = [makeEdge(photo, inline, true), makeEdge(photo, inline, false)];
      await Promise.all(urls.map(url => {
        const edge = new Image();
        edge.src = url;
        return edge.decode();
      }));
      // 两侧底纹解码后一起铺上，避免 WebView 首帧只合成了一侧。
      if (signal.aborted || revision !== entry.revision) return;
      layer.style.setProperty("--cover-before-image", `url("${urls[0]}")`);
      layer.style.setProperty("--cover-after-image", `url("${urls[1]}")`);
      layer.dataset.coverBleed = axis;
    } catch {
      // 补纹不可用时沿用原来的全幅铺图。
    }
  }

  function fit(entry, width, height) {
    const { image, layer } = entry;
    if (!width || !height || !image.naturalWidth || !image.naturalHeight) return;
    const imageRatio = image.naturalWidth / image.naturalHeight;
    const cardRatio = width / height;
    const inline = imageRatio < cardRatio;
    const ratio = inline ? imageRatio / cardRatio : cardRatio / imageRatio;
    const axis = inline ? "inline" : "block";
    const before = (1 - ratio) * (inline ? .5 : .4);
    layer.style.setProperty("--cover-before", before * 100 + "%");
    layer.style.setProperty("--cover-after", (1 - ratio - before) * 100 + "%");
    if (axis !== entry.axis) {
      entry.axis = axis;
      void loadEdges(entry, axis, inline);
    }
  }

  for (const entry of layers) {
    entry.image.addEventListener("load", () => fit(entry, card.clientWidth, card.clientHeight), { signal });
  }
  function sync() {
    const { clientWidth, clientHeight } = card;
    layers.forEach(entry => fit(entry, clientWidth, clientHeight));
  }
  const observer = new ResizeObserver(sync);
  observer.observe(card);
  signal.addEventListener("abort", () => observer.disconnect(), { once: true });
  sync();
}
