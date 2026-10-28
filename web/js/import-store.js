/** §14：草稿元数据与图片分开写，编辑一个字不重写整批图片。 */
const DB_NAME = "wardrobe-photo-import";
let opened;
let writes = Promise.resolve();
let memory = null;
const photos = new Map();
let durable = true;
let retryPhotos = [];
export const storageAvailable = () => durable;
// 这里只复制JSON草稿/属性；File保存在独立photos仓库，兼容旧WebView。
export const copyData = value => value === null || typeof value !== "object" ? value : JSON.parse(JSON.stringify(value));
const copy = value => value == null ? null : copyData(value);
function database() {
  if (!opened) opened = new Promise((resolve, reject) => {
    if (!globalThis.indexedDB) { reject(new Error("浏览器不支持本机草稿")); return; }
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore("draft");
      request.result.createObjectStore("photos", { keyPath: "id" });
    };
    request.onsuccess = () => {
      request.result.onversionchange = () => { request.result.close(); opened = null; };
      resolve(request.result);
    };
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error("本机草稿正在被其他页面使用"));
  });
  return opened;
}
async function read(store, key) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, "readonly");
    const request = tx.objectStore(store).get(key);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
    tx.onabort = () => reject(tx.error);
  });
}
export async function loadDraft() {
  if (memory) return copy(memory);
  try {
    const value = await read("draft", "current");
    if (!memory && value && value.version === 1 && Array.isArray(value.items) && value.items.length <= 20) memory = value;
    return copy(memory);
  } catch { durable = false; return null; }
}
export async function loadPhoto(id) {
  if (photos.has(id)) return photos.get(id);
  try {
    const value = await read("photos", id);
    if (photos.has(id)) return photos.get(id);
    if (!value) throw new Error("这张照片的本机草稿不在了，请重新选择");
    photos.set(id, value);
    return value;
  } catch (error) { durable = false; throw error; }
}
export function saveDraft(draft, { put = [], remove = [], clear = false } = {}) {
  const snapshot = copy(draft);
  memory = snapshot;
  if (clear) photos.clear();
  for (const id of remove) photos.delete(id);
  for (const value of put) photos.set(value.id, value);
  const operation = async () => {
    // 失败事务中的图片增删也要重放，不能仅补元数据就宣称草稿已保存。
    const changes = [...retryPhotos, { put, remove, clear }];
    try {
      const db = await database();
      await new Promise((resolve, reject) => {
      const tx = db.transaction(["draft", "photos"], "readwrite");
      const target = tx.objectStore("photos");
      for (const change of changes) {
        if (change.clear) target.clear();
        for (const id of change.remove) target.delete(id);
        for (const value of change.put) target.put(value);
      }
      if (snapshot) tx.objectStore("draft").put(snapshot, "current");
      else tx.objectStore("draft").delete("current");
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error("本机草稿未保存"));
      });
      retryPhotos = []; durable = true;
    } catch (error) { retryPhotos = changes; durable = false; throw error; }
  };
  const result = writes.then(operation);
  writes = result.catch(() => { durable = false; });
  return result.catch(error => { durable = false; throw error; });
}
export const flushDraft = () => writes;

// §23：已入库衣物的生成预览单独放draft键，批量清photos不会删掉它。
const visualMemory = new Map();
export async function loadVisualDraft(id) {
  if (visualMemory.has(id)) return visualMemory.get(id);
  try { const value = await read("draft", "visual:" + id); if (!visualMemory.has(id)) visualMemory.set(id, value || null); }
  catch { return null; }
  return visualMemory.get(id);
}
export function saveVisualDraft(id, value) {
  const snapshot = value ? { state: copyData(value.state), sourceHash: value.sourceHash, file: value.file || null } : null;
  visualMemory.set(id, snapshot);
  const result = writes.then(async () => {
    const db = await database();
    await new Promise((resolve, reject) => {
      const tx = db.transaction("draft", "readwrite"), target = tx.objectStore("draft");
      if (snapshot) target.put(snapshot, "visual:" + id); else target.delete("visual:" + id);
      tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error);
    });
  });
  writes = result.catch(() => {});
  return result;
}
// §22：手填草稿使用独立键，清除它不能触碰批量照片。
let manualMemory, manualDurable = true;
export const manualStorageAvailable = () => manualDurable;
export async function loadManualDraft() {
  if (manualMemory !== undefined) return copy(manualMemory);
  try {
    const value = await read("draft", "manual");
    if (manualMemory === undefined) manualMemory = value?.version === 1 ? value : null;
  } catch { manualDurable = false; }
  return copy(manualMemory);
}
export function saveManualDraft(value) {
  const snapshot = copy(value);
  manualMemory = snapshot;
  const result = writes.then(async () => {
    try {
      const db = await database();
      await new Promise((resolve, reject) => {
        const tx = db.transaction("draft", "readwrite");
        const store = tx.objectStore("draft");
        if (snapshot) store.put(snapshot, "manual"); else store.delete("manual");
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error || new Error("手填草稿未保存"));
      });
      manualDurable = true;
    } catch (error) { manualDurable = false; throw error; }
  });
  writes = result.catch(() => {});
  return result;
}
export function newToken() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return [...bytes].map(b => b.toString(16).padStart(2, "0")).join("");
}
