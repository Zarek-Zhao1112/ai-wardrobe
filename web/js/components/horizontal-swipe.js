/** §31：横向手势只作用于指定图片区域；纵向滚动由浏览器处理。 */
export function bindHorizontalSwipe(host, { selector, enabled = () => true, onSwipe, onDrag = () => {} }) {
  let gesture = null, blockUntil = 0;
  const cancelledPointers = new Set();
  const now = () => Date.now();
  function cancel() {
    const previous = gesture; gesture = null;
    if (!previous) return;
    onDrag(previous.node, 0);
    if (host.hasPointerCapture?.(previous.id)) host.releasePointerCapture(previous.id);
  }
  function down(event) {
    if (event.isPrimary === false) { if (gesture) cancelledPointers.add(gesture.id); cancelledPointers.add(event.pointerId); cancel(); blockUntil = now() + 450; return; }
    const node = event.target.closest(selector);
    if (!node || !host.contains(node) || event.button > 0 || !enabled()) return;
    cancel(); cancelledPointers.clear(); gesture = { node, id: event.pointerId, x: event.clientX, y: event.clientY, axis: '' };
  }
  function move(event) {
    const g = gesture;
    if (!g || g.id !== event.pointerId) return;
    const dx = event.clientX - g.x, dy = event.clientY - g.y;
    if (!g.axis && Math.max(Math.abs(dx), Math.abs(dy)) > 10) {
      g.axis = Math.abs(dx) > Math.abs(dy) * 1.25 ? 'x' : 'y';
      if (g.axis === 'x') { try { host.setPointerCapture(event.pointerId); } catch {} }
    }
    if (g.axis) blockUntil = now() + 450;
    if (g.axis === 'x') onDrag(g.node, Math.max(-54, Math.min(54, dx * .3)));
  }
  function up(event) {
    if (cancelledPointers.has(event.pointerId)) { cancelledPointers.delete(event.pointerId); blockUntil = now() + 450; return; }
    const g = gesture;
    if (!g || g.id !== event.pointerId) return;
    const dx = event.clientX - g.x, dy = event.clientY - g.y;
    cancel();
    // 已经移动过的手势，停住再松手也不应变成单击。
    if (g.axis) blockUntil = now() + 450;
    if (g.axis === 'x' && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.25 && enabled()) onSwipe(dx < 0 ? 1 : -1);
  }
  function click(event) { if (now() < blockUntil) { event.preventDefault(); event.stopImmediatePropagation(); } }
  function drag(event) { if (event.target.closest(selector)) event.preventDefault(); }
  function lost(event) { if (gesture?.id === event.pointerId) cancel(); }
  host.addEventListener('pointerdown', down); host.addEventListener('pointermove', move);
  host.addEventListener('pointerup', up); host.addEventListener('pointercancel', cancel);
  host.addEventListener('lostpointercapture', lost); host.addEventListener('click', click, true);
  host.addEventListener('dragstart', drag);
  return { cancel, dispose() {
    cancel(); host.removeEventListener('pointerdown', down); host.removeEventListener('pointermove', move);
    host.removeEventListener('pointerup', up); host.removeEventListener('pointercancel', cancel);
    host.removeEventListener('lostpointercapture', lost); host.removeEventListener('click', click, true);
    host.removeEventListener('dragstart', drag);
  } };
}
