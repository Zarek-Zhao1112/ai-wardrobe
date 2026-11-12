"""查找已配对的电脑中转，验证成功才更新地址。"""

import asyncio
import hmac
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
import json
import re
import secrets
from urllib.parse import urlsplit

import httpx

from ...bridge_discovery import DISCOVERY_PATH, discovery_proof
from .desktop import load_settings, replace_bridge_url, status_of

_LAN_RANGES = tuple(IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_CONCURRENCY = 64
_repair_lock = asyncio.Lock()
# APK 入口提供 Android 当前 Wi-Fi 的 IPv4 与前缀，桌面版可检查本机中转。
wifi_ipv4 = None


def _scan_scope():
    if wifi_ipv4 is None:
        return None, (IPv4Address("127.0.0.1"),)
    address = wifi_ipv4()
    if not address:
        return None, ()
    interface = IPv4Interface(str(address))
    network = interface.network
    if not any(network.subnet_of(lan) for lan in _LAN_RANGES):
        return None, ()
    # 常见家庭网段完整查找，较大的网段限制扫描范围。
    if network.prefixlen < 22:
        network = IPv4Network(f"{interface.ip}/24", strict=False)
    return str(interface.ip), tuple(ip for ip in network.hosts() if ip != interface.ip)


async def _probe(client, ip, port, token, seconds):
    nonce = secrets.token_hex(16)
    url = f"http://{ip}:{port}{DISCOVERY_PATH}"
    try:
        async with asyncio.timeout(seconds), client.stream(
            "GET", url, params={"nonce": nonce}, headers={"Accept-Encoding": "identity"},
        ) as response:
            if response.status_code != 200:
                return None
            body = bytearray()
            async for chunk in response.aiter_raw():
                body.extend(chunk)
                if len(body) > 512:
                    return None
            data = json.loads(body)
    except (TimeoutError, httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    state, proof = data.get("state"), data.get("proof")
    if state not in ("ready", "unavailable") or not isinstance(proof, str) or not re.fullmatch("[0-9a-f]{64}", proof):
        return None
    expected = discovery_proof(token, nonce, str(ip), port, state)
    return state if hmac.compare_digest(proof, expected) else None


async def repair_bridge(candidate: str | None = None) -> dict:
    if _repair_lock.locked():
        return {"status": "busy"}
    async with _repair_lock:
        return await _repair_bridge(candidate)


async def _repair_bridge(candidate: str | None) -> dict:
    settings = load_settings()
    if settings is None:
        return {"status": "unconfigured"}
    original = urlsplit(settings["url"])
    old_ip = IPv4Address(original.hostname)
    port = original.port
    token = settings["token"]
    if candidate is None:
        async with httpx.AsyncClient(trust_env=False) as client:
            current = await _probe(client, old_ip, port, token, 2)
        if current == "ready":
            return {"status": "online", "bridge": status_of(settings)}
        if current == "unavailable":
            return {"status": "unavailable"}

    source_ip, hosts = _scan_scope()
    if not hosts:
        return {"status": "no_wifi"}
    if candidate is not None:
        try:
            chosen = IPv4Address(candidate)
        except ValueError:
            return {"status": "missing"}
        if chosen not in hosts:
            return {"status": "missing"}
        hosts = (chosen,)
    else:
        hosts = tuple(ip for ip in hosts if ip != old_ip)

    semaphore = asyncio.Semaphore(_CONCURRENCY)
    limits = httpx.Limits(max_connections=_CONCURRENCY, max_keepalive_connections=0)
    transport = httpx.AsyncHTTPTransport(trust_env=False, local_address=source_ip, limits=limits)
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        async def discover(ip):
            async with semaphore:
                return await _probe(client, ip, port, token, 1.1 if candidate is None else 2)

        results = await asyncio.gather(*(discover(ip) for ip in hosts))
    matches = [ip for ip, state in zip(hosts, results) if state == "ready"]
    if not matches:
        return {"status": "unavailable" if "unavailable" in results else "missing"}
    if len(matches) > 1:
        return {"status": "choose", "candidates": [{"ip": str(ip), "url": f"http://{ip}:{port}"} for ip in matches]}
    updated = replace_bridge_url(settings, f"http://{matches[0]}:{port}")
    return {"status": "repaired", "bridge": status_of(updated)}
