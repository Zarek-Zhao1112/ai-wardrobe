"""提供设计工作台、App 前端静态文件与隔离的演示接口。"""

import argparse
from base64 import b64encode
from email import policy
from email.parser import BytesParser
from hashlib import sha256
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
import socket
import sys
from threading import Lock
from urllib import request as urllib_request
from urllib.parse import parse_qs, unquote, urlsplit
import webbrowser

WORKBENCH = Path(__file__).resolve().parent
PROJECT = WORKBENCH.parents[2]
sys.path.insert(0,str(PROJECT))
from app import taxonomy
from app.services import aesthetics

WEB = PROJECT / "web"
SAMPLES = PROJECT / "tests/fixtures/reference"
ITEMS = [
    {"id":"demo-shirt","name":"浅蓝衬衫","type":"shirt","type_label":"上装","slot":"top","primary_color":"light_blue","colors":["light_blue"],"display_image_path":"light-blue-shirt.jpg","image_path":"light-blue-shirt.jpg","season":["spring","autumn"],"tagging_status":"tagged","updated_at":"2026-11-11","favorite":True},
    {"id":"demo-pants","name":"奶油锥形裤","type":"trousers","type_label":"下装","slot":"bottom","primary_color":"beige","colors":["beige"],"display_image_path":"cream-tapered-pants.jpg","image_path":"cream-tapered-pants.jpg","season":["spring","autumn"],"tagging_status":"tagged","updated_at":"2026-11-11"},
    {"id":"demo-cardigan","name":"灰色针织开衫","type":"cardigan","type_label":"开衫","slot":"outer","primary_color":"gray","colors":["gray"],"display_image_path":"gray-cardigan.jpg","image_path":"gray-cardigan.jpg","season":["autumn","winter"],"tagging_status":"tagged","updated_at":"2026-11-11"},
    {"id":"demo-shoes","name":"白色运动鞋","type":"sneakers","type_label":"运动鞋","slot":"shoes","primary_color":"white","colors":["white"],"display_image_path":"white-sneakers.jpg","image_path":"white-sneakers.jpg","season":["spring","summer","autumn"],"tagging_status":"tagged","updated_at":"2026-11-11"},
    {"id":"demo-tee","name":"白色圆领 T 恤","type":"tshirt","type_label":"T 恤","slot":"top","primary_color":"white","colors":["white"],"display_image_path":"white-tshirt.jpg","image_path":"white-tshirt.jpg","season":["spring","summer"],"tagging_status":"tagged","updated_at":"2026-11-11"},
    {"id":"demo-wide","name":"白色阔腿裤","type":"trousers","type_label":"下装","slot":"bottom","primary_color":"white","colors":["white"],"display_image_path":"white-wide-pants.jpg","image_path":"white-wide-pants.jpg","season":["spring","summer"],"tagging_status":"tagged","updated_at":"2026-11-11"},
]
ITEM_BY_ID = {item["id"]:item for item in ITEMS}
CATALOG = {"aesthetics":aesthetics.list_profiles(),"default":aesthetics.DEFAULT,
           "season":taxonomy.current_season(),"season_label":taxonomy.SEASON_LABELS[taxonomy.current_season()]}
DEFAULT_STYLE = next(row for row in CATALOG["aesthetics"] if row["id"] == CATALOG["default"])
OUTFIT = {"id":"demo-outfit","created_at":"2026-11-11","status":"proposed",
          "items":[ITEMS[0],ITEMS[1],ITEMS[3]],"item_ids":["demo-shirt","demo-pants","demo-shoes"],
          "aesthetic":DEFAULT_STYLE,"reason":"浅蓝和奶油色形成柔和对比，白色鞋履延续整体配色。"}
OUTFITS = {"outfits":[OUTFIT]}
PURCHASE = {"total":len(ITEMS),"items":ITEM_BY_ID,"saved":[],"dismissed":[],"references":{},"result_history":[]}
BUILDER = "/wardrobe/purchase/builder"
SAMPLE_PHOTO = "light-blue-shirt.jpg"
SAMPLE_TAGS = {"name": "示例浅蓝衬衫", "type": "shirt", "colors": ["light_blue"]}


class PreviewHandler(BaseHTTPRequestHandler):
    def read_body(self):
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 <= size <= 30 * 1024 * 1024:
            raise ValueError("演示请求大小超出限制")
        return self.rfile.read(size)

    def multipart(self):
        content_type = self.headers.get("Content-Type", "")
        header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n"
        message = BytesParser(policy=policy.default).parsebytes(header.encode("ascii") + self.read_body())
        return {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                for part in message.iter_parts()}

    def builder_state(self, request_id=None):
        job = self.server.purchase_jobs.get(request_id or self.server.latest_purchase)
        result = None
        if job and job["status"] == "completed":
            result = {**job, "decision": "enough", "headline": "现有衣物就能搭出这一套",
                      "summary": "用浅蓝与奶油色做主色，白色鞋履让整体保持清爽。"}
        return {"wardrobe": list(ITEM_BY_ID.values()), "request": job, "result": result, "references": {}}

    def respond(self, status, payload, content_type="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload,ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type",content_type)
        self.send_header("Cache-Control","no-store")
        self.send_header("Permissions-Policy", "camera=(), microphone=()")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def health(self):
        return {
            "status": "healthy", "item_count": len(ITEM_BY_ID), "pending_tagging": 0,
            "llm_ready": True, "vision_ready": True, "llm_model": "演示模式", "vision_model": "演示模式",
            "desktop_bridge": {"configured": True}, "flatlay_configured": True,
            "ai_channel": self.server.ai_channel,
        }

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/health":
            return self.respond(200, self.health())
        if path == "/taxonomy":
            return self.respond(200,{"slots":taxonomy.SLOTS,"types":{k:{"label":v[0],"slot":v[1]} for k,v in taxonomy.CLOTHING_TYPES.items()},"vocabularies":taxonomy.VOCABULARIES})
        if path == "/outfits/aesthetics":
            return self.respond(200,CATALOG)
        if path == "/items":
            args = {key: values[-1] for key, values in parse_qs(urlsplit(self.path).query).items()}
            rows = [item for item in ITEM_BY_ID.values()
                    if args.get("search", "").casefold() in item["name"].casefold()
                    and all(not args.get(key) or args[key] == item[key] for key in ("slot", "type"))]
            return self.respond(200,{"items":rows,"total":len(rows),"page":1,"page_size":60})
        if path.startswith("/items/import-photo/"):
            saved = self.server.imports.get(path.rsplit("/", 1)[-1])
            return self.respond(200 if saved else 404, {"item": saved} if saved else {"detail": "没有这条演示录入记录"})
        if path.startswith("/items/") and path.count("/") == 2:
            return self.respond(200,ITEM_BY_ID.get(path.rsplit("/",1)[-1],{}))
        if path == "/outfits":
            return self.respond(200,OUTFITS)
        if path.startswith("/outfits/"):
            return self.respond(200,OUTFIT)
        if path == "/wardrobe/gaps":
            return self.respond(200,{"gaps":[],"counts":{},"total":len(ITEMS)})
        if path in ("/wardrobe/purchase","/wardrobe/purchase/intent"):
            return self.respond(200,PURCHASE)
        if path == BUILDER or path.startswith(BUILDER + "/"):
            request_id = path.removeprefix(BUILDER + "/") if path != BUILDER else None
            with self.server.demo_lock:
                result = self.builder_state(request_id)
            return self.respond(200, result)
        if path.startswith("/tagging/flatlay/jobs/"):
            result = self.server.flatlays.get(path.rsplit("/", 1)[-1])
            return self.respond(200 if result else 404, result or {"detail": "没有这个演示平铺任务"})
        if path.startswith("/images/"):
            name = PurePosixPath(path.removeprefix("/images/")).name
            file = SAMPLES/name
            if name.endswith(".jpg") and file.is_file():
                return self.respond(200,file.read_bytes(),"image/jpeg")
            return self.respond(404,{})
        if path.startswith(("/css/","/js/","/icons/","/reference/")) or path == "/manifest.webmanifest":
            relative = PurePosixPath(path.lstrip("/"))
            if ".." not in relative.parts and "\\" not in path:
                file = WEB.joinpath(*relative.parts).resolve()
                if file.is_relative_to(WEB) and file.is_file():
                    return self.respond(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        if path == "/sw.js":
            # 清理旧版工作台曾注册的 App worker，防止它缓存演示页面。
            worker = b"self.addEventListener('install',()=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.registration.unregister()));"
            return self.respond(200, worker, "text/javascript; charset=utf-8")
        if path == "/":
            return self.respond(200,(WORKBENCH/"index.html").read_bytes(),"text/html; charset=utf-8")
        if path in ("/app","/app/"):
            preview_head = (b'<head><script type="importmap">{"imports":{'
                            b'"/js/core/ai-channel-settings.js":"/ai-model-preview.js"}}</script>'
                            b'<script src="/demo-inputs.js"></script>')
            html = (WEB / "index.html").read_bytes().replace(b"<head>", preview_head, 1)
            return self.respond(200, html, "text/html; charset=utf-8")
        if path.startswith("/screens/"):
            name = PurePosixPath(path.removeprefix("/screens/")).name
            file = WORKBENCH/"screens"/name
            if name.endswith(".png") and file.is_file():
                return self.respond(200,file.read_bytes(),"image/png")
        if path in ("/preview.css", "/preview.js", "/demo-inputs.js", "/ai-model-preview.js"):
            kind = "text/css" if path.endswith(".css") else "text/javascript"
            return self.respond(200, (WORKBENCH / path.lstrip("/")).read_bytes(), kind + "; charset=utf-8")
        if path == "/页面说明.md":
            return self.respond(200,(WORKBENCH/"页面说明.md").read_bytes(),"text/markdown; charset=utf-8")
        if path in ("/index.html","/workbench"):
            return self.respond(200,(WORKBENCH/"index.html").read_bytes(),"text/html; charset=utf-8")
        return self.respond(404,{"detail":"演示环境没有这个资源"})

    do_HEAD = do_GET

    def do_POST(self):
        url = urlsplit(self.path)
        path = unquote(url.path)
        try:
            return self.write_demo(path, parse_qs(url.query))
        except (KeyError, TypeError, ValueError):
            return self.respond(400, {"detail": "这次演示操作缺少必要的数据。"})

    def write_demo(self, path, query):
        if path == "/settings/ai-channel":
            body = json.loads(self.read_body())
            mode = body["mode"]
            if mode not in ("direct", "account"):
                raise ValueError()
            state = {"mode": mode}
            for field in ("provider", "model", "vision_model"):
                if field in body:
                    state[field] = str(body[field])[:150]
            self.server.ai_channel = {**self.server.ai_channel, **state}
            return self.respond(200, self.health())
        if path == "/outfits/recommend":
            style = query.get("aesthetic", [""])[0]
            profile = next((row for row in CATALOG["aesthetics"] if row["id"] == style), DEFAULT_STYLE)
            return self.respond(200, {"outfits": [{**OUTFIT, "aesthetic": profile}],
                                      "season_label": CATALOG["season_label"]})
        if path == "/outfits/feedback":
            kind = json.loads(self.read_body())["feedback_type"]
            return self.respond(200, {"outfit_status": {"like":"accepted","dislike":"rejected","swap":"swapped"}.get(kind)})
        if path == BUILDER:
            payload = json.loads(self.read_body())
            request_id = payload["request_id"]
            if not isinstance(request_id, str) or not isinstance(payload["item_ids"], list):
                raise ValueError()
            with self.server.demo_lock:
                self.server.purchase_jobs.setdefault(request_id, {
                    "id": request_id, "status": "running", "phase": "analyzing", "mode": payload["mode"],
                    "aesthetic": {"id": payload["aesthetic"]}, "item_ids": payload["item_ids"],
                })
                self.server.latest_purchase = request_id
                result = self.builder_state(request_id)
            return self.respond(200, result)
        if path.startswith(BUILDER + "/") and path.endswith("/query"):
            request_id = path.split("/")[-2]
            with self.server.demo_lock:
                job = self.server.purchase_jobs.get(request_id)
                if job:
                    self.server.purchase_jobs[request_id] = {**job, "status": "completed", "phase": "finished"}
                    result = self.builder_state(request_id)
            return self.respond(200, result) if job else self.respond(404, {"detail": "未找到该演示任务"})
        if path == "/tagging/preview":
            self.read_body()
            return self.respond(200, {"tags": SAMPLE_TAGS, "unresolved": [], "status": "preview"})
        if path == "/tagging/flatlay/jobs":
            form = self.multipart()
            task_id = form["token"].decode("utf-8")
            image = b64encode((SAMPLES / SAMPLE_PHOTO).read_bytes()).decode("ascii")
            result = {"id": task_id, "state": "completed", "source_hash": sha256(form["file"]).hexdigest(),
                      "image": "data:image/jpeg;base64," + image, "image_model": "预设演示", "credit": 0}
            self.server.flatlays[task_id] = result
            return self.respond(200, result)
        if path == "/items/import-photo":
            form = self.multipart()
            token = form["token"].decode("utf-8")
            item = {**ITEMS[0], "id": "demo-import-" + token, "name": "示例浅蓝衬衫", "favorite": False}
            with self.server.demo_lock:
                item = self.server.imports.setdefault(token, item)
                ITEM_BY_ID[item["id"]] = item
            return self.respond(200, {"item": item})
        return self.respond(501, {"detail": "该操作暂未提供演示数据。"})

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST


def serving_here(port):
    """确认这个端口上跑的就是工作台（而不是 App 本体或其他程序）。

    判据用 /preview.js —— 只有工作台提供这个文件。`/health` 不能用：
    App 本体的 /health 也会带 item_count，会被误认成工作台。
    必须绕开代理：本机的 http_proxy 指向外部代理，走它访问 127.0.0.1 会失败。
    """
    opener = urllib_request.build_opener(urllib_request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/preview.js", timeout=1.5) as response:
            return b"wardrobe-workbench" in response.read()
    except OSError:
        return False


def port_state(port):
    """先问再绑。

    Windows 上 SO_REUSEADDR 允许两个进程同时绑同一个端口（HTTPServer 默认就开着），
    所以「绑不上就报错」这条路在 Windows 上根本不会触发 —— 必须自己先探一下。
    """
    with socket.socket() as probe:
        probe.settimeout(0.4)
        if probe.connect_ex(("127.0.0.1", port)) != 0:
            return "free"
    return "workbench" if serving_here(port) else "other"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port",type=int,default=8798)
    parser.add_argument("--open",action="store_true",help="启动后打开默认浏览器")
    args = parser.parse_args()
    # 双击两次不该看到一串 traceback：已经有一个就把它打开，被别人占了就说清楚。
    state = port_state(args.port)
    if state != "free":
        if state == "workbench":
            print(f"工作台已经在运行：http://127.0.0.1:{args.port}/",flush=True)
            if args.open:
                webbrowser.open(f"http://127.0.0.1:{args.port}/")
            return 0
        print(f"端口 {args.port} 被其他程序占用。换一个：serve.py --port 8799",flush=True)
        return 1
    with ThreadingHTTPServer(("127.0.0.1",args.port),PreviewHandler) as server:
        server.demo_lock = Lock()
        server.purchase_jobs = {}
        server.latest_purchase = None
        server.flatlays = {}
        server.imports = {}
        server.ai_channel = {"mode": "direct", "provider": "openai", "model": "gpt-6-luna",
                             "vision_model": "", "configured": True, "direct_ready": True}
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"设计工作台：{url}",flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
