/** §30：只在用户轻点后开镜头；取消后迟到的流也要关闭，不保存或上传画面。 */
export function createTryonCamera({ video, mediaDevices = globalThis.navigator?.mediaDevices, onChange = () => {} }) {
  let stream = null, generation = 0, disposed = false, facing = 'user', state = 'idle';
  const emit = (next, error = '') => { state = next; if (!disposed) onChange({ state, error, facing }); };
  const release = source => source?.getTracks().forEach(track => track.stop());
  function stop() {
    ++generation; release(stream); stream = null;
    video.pause?.(); video.srcObject = null; emit('idle');
  }
  async function start(requested = facing) {
    if (disposed) return false;
    stop(); const attempt = generation;
    if (!mediaDevices?.getUserMedia) { emit('error', '当前浏览器无法开启摄像头，请使用应用或本机安全连接。'); return false; }
    emit('opening'); let candidate = null;
    try {
      candidate = await mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: requested }, width: { ideal: 960 }, height: { ideal: 1280 } } });
      if (disposed || attempt !== generation) { release(candidate); return false; }
      stream = candidate;
      const track = stream.getVideoTracks()[0];
      facing = track?.getSettings?.().facingMode || requested;
      track?.addEventListener?.('ended', () => { if (!disposed && attempt === generation) { stop(); emit('error', '摄像头已停止，可以重新开启。'); } }, { once: true });
      video.muted = true; video.playsInline = true; video.srcObject = stream;
      await video.play();
      if (disposed || attempt !== generation) { release(candidate); return false; }
      emit('preview'); return true;
    } catch (error) {
      release(candidate);
      if (disposed || attempt !== generation) return false;
      stream = null; video.srcObject = null;
      const message = error?.name === 'NotAllowedError' ? '摄像头权限未开启，请允许后再试。'
        : error?.name === 'NotFoundError' || error?.name === 'OverconstrainedError' ? '没有找到可用的摄像头。'
        : error?.name === 'NotReadableError' ? '摄像头正被其他应用使用，请关闭后再试。' : '摄像头暂时无法开启，请重试。';
      emit('error', message); return false;
    }
  }
  return { start, stop, switch: () => start(facing === 'user' ? 'environment' : 'user'),
    dispose() { if (disposed) return; stop(); disposed = true; },
    snapshot: () => ({ state, facing, active: !!stream }) };
}
