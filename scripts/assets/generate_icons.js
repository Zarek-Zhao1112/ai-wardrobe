/* §13：SVG 母版 → 网页 / PWA / Android 图标。
 * 前置：Chrome 以独立 profile 开启 CDP（同 cdp_ui_check.js），Node >= 22。
 * 用法：CDP_PORT=9333 node scripts/assets/generate_icons.js
 * 只改 logo.svg，再运行本脚本；衍生 PNG / XML 一并进仓库。
 */
const fs = require("fs");
const path = require("path");
const http = require("http");
const ROOT = path.resolve(__dirname, "../..");
const PORT = Number(process.env.CDP_PORT || 9333);
function request(method, url) {
  return new Promise((resolve, reject) => {
    const r = http.request({host:"127.0.0.1", port:PORT, path:url, method}, res => {
      let body = ""; res.on("data", b => body += b); res.on("end", () => {
        if (res.statusCode >= 400) { reject(Error(body)); return; }
        try { resolve(JSON.parse(body)); } catch { resolve(body); }
      });
    });
    r.on("error", reject); r.setTimeout(10000, () => r.destroy(Error("CDP 请求超时"))); r.end();
  });
}
class Client {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map();
    ws.addEventListener("message", e => {
      const msg = JSON.parse(e.data), item = this.pending.get(msg.id);
      if (!item) return;
      clearTimeout(item.timer); this.pending.delete(msg.id);
      if (msg.error) item.reject(Error(msg.error.message)); else item.resolve(msg.result);
    });
    ws.addEventListener("close", () => {
      for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(Error("CDP 已关闭")); }
      this.pending.clear();
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(Error(method + " 超时")); }, 30000);
      this.pending.set(id, {resolve,reject,timer}); this.ws.send(JSON.stringify({id,method,params}));
    });
  }
}
function write(relative, data) {
  const output = path.join(ROOT, relative);
  fs.mkdirSync(path.dirname(output), {recursive:true}); fs.writeFileSync(output, data);
}
async function main() {
  if (typeof WebSocket !== "function") throw Error("需要 Node >= 22（全局 WebSocket）");
  const source = fs.readFileSync(path.join(ROOT,"web/icons/logo.svg"), "utf8");
  const target = await request("PUT", "/json/new?about:blank");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  try {
    await new Promise((resolve,reject) => {
      const timer = setTimeout(() => reject(Error("CDP 连接超时")),10000);
      ws.addEventListener("open", () => {clearTimeout(timer); resolve();}, {once:true});
      ws.addEventListener("error", () => {clearTimeout(timer); reject(Error("CDP 连接失败"));}, {once:true});
    });
    const c = new Client(ws);
    const result = await c.send("Runtime.evaluate", {awaitPromise:true, returnByValue:true,
      expression: "(" + render.toString() + ")(" + JSON.stringify(source) + ")"});
    if (result.exceptionDetails) throw Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    const data = result.result.value;
    for (const file of data.png) write(file.name, Buffer.from(file.base64,"base64"));
    const ns = 'xmlns:android="http://schemas.android.com/apk/res/android"';
    const vector = (monochrome) => {
      const paths = monochrome
        ? '<path android:fillColor="#FFFFFFFF" android:fillType="evenOdd" android:pathData="' + data.parts.map(p=>p.d).join(" ") + '"/>'
        : data.parts.map(p=>'<path android:fillColor="' + p.fill + '" android:pathData="' + p.d + '"/>').join("\n      ");
      // 自适应图层 108dp；中央 72dp 可见。留出 18dp，保证与普通图标同一重心。
      return '<?xml version="1.0" encoding="utf-8"?>\n<vector ' + ns + ' android:width="108dp" android:height="108dp" android:viewportWidth="256" android:viewportHeight="256">\n  <group android:translateX="42.666667" android:translateY="42.666667" android:scaleX="0.6666667" android:scaleY="0.6666667">\n    <group android:translateX="' + data.transform[0] + '" android:translateY="' + data.transform[1] + '">\n      <group android:rotation="' + data.transform[2] + '" android:pivotX="' + data.transform[3] + '" android:pivotY="' + data.transform[4] + '">\n      ' + paths + '\n      </group>\n    </group>\n  </group>\n</vector>\n';
    };
    write("android/app/src/main/res/drawable/ic_launcher_foreground.xml", vector(false));
    write("android/app/src/main/res/drawable/ic_launcher_monochrome.xml", vector(true));
    write("android/app/src/main/res/values/icon_colors.xml", '<?xml version="1.0" encoding="utf-8"?>\n<resources><color name="icon_background">' + data.background + '</color></resources>\n');
    for (const version of [26,33]) for (const name of ["ic_launcher","ic_launcher_round"]) {
      write("android/app/src/main/res/mipmap-anydpi-v" + version + "/" + name + ".xml",
        '<?xml version="1.0" encoding="utf-8"?>\n<adaptive-icon ' + ns + '>\n  <background android:drawable="@color/icon_background"/>\n  <foreground android:drawable="@drawable/ic_launcher_foreground"/>' +
        (version >= 33 ? '\n  <monochrome android:drawable="@drawable/ic_launcher_monochrome"/>' : '') + '\n</adaptive-icon>\n');
    }
    console.log(JSON.stringify({png:data.png.map(p=>({name:p.name,size:p.size})),android_vectors:2,adaptive_icons:4,background:data.background},null,2));
  } finally {
    ws.close();
    try { await request("GET", "/json/close/" + encodeURIComponent(target.id)); }
    catch (e) { console.warn("图标标签清理失败：" + e.message); }
  }
}
// 在 Chrome 内解析和渲染 SVG，PNG 没有外部字体或图片依赖。
async function render(source) {
  const doc = new DOMParser().parseFromString(source,"image/svg+xml"), svg = doc.documentElement;
  if (doc.querySelector("parsererror") || svg.localName !== "svg") throw Error("SVG 母版无法解析");
  const background = doc.getElementById("background")?.getAttribute("fill");
  const mascot = doc.getElementById("mascot");
  const parts = ["body","eyes","smile"].map(id=>{
    const p = doc.getElementById(id); if (!p || p.localName!=="path") throw Error("缺少图标路径：" + id);
    return {d:p.getAttribute("d"),fill:p.getAttribute("fill")};
  });
  const match = mascot?.getAttribute("transform").match(/^translate\(([-\d.]+) ([-\d.]+)\) rotate\(([-\d.]+) ([-\d.]+) ([-\d.]+)\)$/);
  if (!match || !background || parts.some(p=>!p.d||!p.fill)) throw Error("不支持的母版结构");
  if (svg.querySelector("image,script,foreignObject") || [...svg.querySelectorAll("*")].some(el=>[...el.attributes].some(a=>a.localName==="href"||a.name.startsWith("on")))) throw Error("图标须为自包含矢量");
  async function png(size, mode) {
    const copy = svg.cloneNode(true);
    // 系统负责圆角；maskable / Apple 图标必须是满幅不透明底色。
    if (mode==="square") copy.querySelector("#tile rect").setAttribute("rx","0");
    if (mode==="round") copy.querySelector("#tile").innerHTML='<circle cx="128" cy="128" r="128"/>';
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(copy)],{type:"image/svg+xml"}));
    try {
      const img = new Image(); img.src=url; await img.decode();
      const canvas=document.createElement("canvas");canvas.width=canvas.height=size;
      const ctx=canvas.getContext("2d"); if (!ctx) throw Error("无法创建画布");
      ctx.drawImage(img,0,0,size,size);return canvas.toDataURL("image/png").split(",")[1];
    } finally {URL.revokeObjectURL(url);}
  }
  const outputs = [
    ["web/icons/icon-192.png",192,"normal"],["web/icons/icon-512.png",512,"normal"],
    ["web/icons/icon-maskable-512.png",512,"square"],["web/icons/apple-touch-icon.png",180,"square"],
    ["web/icons/favicon-32.png",32,"normal"]
  ];
  for(const [density,size] of [["mdpi",48],["hdpi",72],["xhdpi",96],["xxhdpi",144],["xxxhdpi",192]]) {
    outputs.push(["android/app/src/main/res/mipmap-"+density+"/ic_launcher.png",size,"normal"]);
    outputs.push(["android/app/src/main/res/mipmap-"+density+"/ic_launcher_round.png",size,"round"]);
  }
  const images=[];
  for(const [name,size,mode] of outputs) images.push({name,size,base64:await png(size,mode)});
  return {png:images,parts,transform:match.slice(1).map(Number),background};
}
main().catch(e=>{console.error(e.stack);process.exitCode=1;});
