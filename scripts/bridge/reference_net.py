"""§31：从已搜索到的公开商品网页读取照片，不跟随到本机或内网。"""
from __future__ import annotations
import http.client
import ipaddress
import json
import re
import socket
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


def public_url(value):
    if not isinstance(value, str) or len(value) > 3000 or any(ord(c) < 33 for c in value):
        raise ValueError('无效公开网址')
    p = urlsplit(value)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.fragment or '\\' in value:
        raise ValueError('无效公开网址')
    if p.port not in (None, 80 if p.scheme == 'http' else 443):
        raise ValueError('不支持的网址端口')
    host = p.hostname.encode('idna').decode('ascii').lower()
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')) or '.' not in host:
        raise ValueError('不接受本地地址')
    try:
        if not ipaddress.ip_address(host).is_global: raise ValueError('不接受内网地址')
    except ValueError as error:
        if '不接受' in str(error): raise
    return value


def fetch_public(url, limit=2_000_000, *, redirects=3):
    """逐跳检查DNS并固定连接IP，保留原主机名进行TLS证书校验。"""
    deadline = time.monotonic() + 12
    for _ in range(redirects + 1):
        public_url(url); p = urlsplit(url); port = p.port or (443 if p.scheme == 'https' else 80)
        found = {}; done = threading.Event()
        def resolve(host=p.hostname, service=port, result=found, event=done):
            try: result['addresses'] = {entry[4][0] for entry in socket.getaddrinfo(host, service, type=socket.SOCK_STREAM)}
            except OSError: result['addresses'] = set()
            finally: event.set()
        # 迟到的DNS线程只返回地址，不执行连接或后续模型调用。
        threading.Thread(target=resolve, daemon=True).start()
        if not done.wait(max(0, min(3, deadline-time.monotonic()))): raise ValueError('网页地址解析超时')
        addresses = found.get('addresses', set())
        if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast for address in addresses):
            raise ValueError('网页地址未通过校验')
        chosen = sorted(addresses)[0]
        remaining = deadline-time.monotonic()
        if remaining <= 0: raise ValueError('公开网页读取超时')
        connection = (http.client.HTTPSConnection if p.scheme == 'https' else http.client.HTTPConnection)(p.hostname, port, timeout=min(7, remaining))
        connection._create_connection = lambda address, timeout=7, source_address=None, **kwargs: socket.create_connection((chosen, port), min(timeout, max(.01, deadline-time.monotonic())))
        expired = threading.Event(); held = {}
        def abort(conn=connection, flag=expired, sockets=held):
            flag.set(); stream = sockets.get('stream') or conn.sock
            if stream:
                try: stream.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                try: stream.close()
                except OSError: pass
            conn.close()
        timer = threading.Timer(remaining, abort); timer.daemon = True; timer.start()
        try:
            connection.request('GET', (p.path or '/') + ('?' + p.query if p.query else ''), headers={'User-Agent': 'WardrobeReference/1.0', 'Accept': 'text/html,application/json,image/*;q=0.8', 'Accept-Encoding': 'identity'})
            held['stream'] = connection.sock
            if expired.is_set(): raise ValueError('公开网页读取超时')
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location: raise ValueError('网页跳转无效')
                url = urljoin(url, location); continue
            if response.status != 200: raise ValueError('公开网页暂时无法读取')
            if int(response.getheader('Content-Length') or 0) > limit: raise ValueError('内容过大')
            content = response.read(limit + 1)
            if expired.is_set(): raise ValueError('公开网页读取超时')
            if len(content) > limit: raise ValueError('内容过大')
            return content, (response.getheader('Content-Type') or '').split(';')[0].lower(), url
        finally:
            timer.cancel()
            connection.close()
    raise ValueError('网页跳转过多')


class ProductParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.meta = {}; self.scripts = []; self.script = None; self.title = ''; self.in_title = False; self.photos = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta': self.meta[(a.get('property') or a.get('name') or '').lower()] = a.get('content') or ''
        if tag == 'script' and (a.get('type') or '').lower() == 'application/ld+json': self.script = ''
        if tag == 'title': self.in_title = True
        if tag == 'img' and len(self.photos) < 100:
            self.photos.append({'url': a.get('data-src') or a.get('src'), 'alt': a.get('alt') or '', 'class': a.get('class') or ''})
    def handle_data(self, data):
        if self.script is not None: self.script += data
        if self.in_title: self.title += data
    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None: self.scripts.append(self.script); self.script = None
        if tag == 'title': self.in_title = False


def product_images(html, url):
    p = ProductParser(); p.feed(html)
    products = []
    def collect(value):
        if isinstance(value, list):
            for row in value: collect(row)
        elif isinstance(value, dict):
            kinds = value.get('@type', [])
            if isinstance(kinds, str): kinds = [kinds]
            if not isinstance(kinds, list): kinds = []
            if 'Product' in kinds: products.append(value)
            for key in ('@graph', 'mainEntity', 'itemListElement', 'item'): collect(value.get(key))
    for raw in p.scripts:
        try: collect(json.loads(raw))
        except (ValueError, RecursionError): pass
    result = []
    def add(value, name):
        if isinstance(value, list):
            for row in value[:6]: add(row, name)
        elif isinstance(value, dict): add(value.get('url') or value.get('contentUrl'), name)
        elif isinstance(value, str):
            try: target = public_url(urljoin(url, value))
            except ValueError: return
            if target not in [row['image_url'] for row in result]: result.append({'image_url': target, 'source_url': url, 'name': re.sub(r'\s+', ' ', str(name))[:180]})
    for product in products[:3]: add(product.get('image'), product.get('name') or p.title)
    # 没有商品结构化数据时，只接受明确的商品页元数据。
    product_path = bool(re.search(r'/(?:products?|items?|shopping)/', urlsplit(url).path, re.I))
    if not result and (p.meta.get('og:type', '').lower() in ('product', 'og:product') or product_path):
        add(p.meta.get('og:image'), p.meta.get('og:title') or p.title)
    if product_path:
        title = p.meta.get('og:title') or p.title
        for photo in p.photos:
            alt = photo['alt'].strip()
            if len(alt) >= 6 and alt.lower() in title.lower() or re.search(r'product[-_](?:image|photo|media)', photo['class'], re.I):
                add(photo['url'], alt or title)
    return result[:3]
