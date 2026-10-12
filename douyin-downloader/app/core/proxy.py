# -*- coding: utf-8 -*-
"""系统代理读取与可用性回退（各 HTTP 客户端共用）。

两个职责：
1. `system_proxies()`：读 Windows 系统代理并补齐协议头。系统代理常是
   `127.0.0.1:2410` 这种**不带协议头**的写法，requests / curl_cffi 都要
   `http://` 前缀才认。
2. `working_proxies()`：**系统代理指着一个不通的端口时自动回退**。
   Clash 这类本地代理的端口经常变（实测同一天里就见过 2410 / 7897 / 9159
   轮换），而系统代理设置未必跟着更新 —— 表现是需要代理的站点一律
   `CONNECT tunnel failed, response 502`。这里并发试一遍候选端口，取第一个
   真能连通的，结果按进程缓存（只探一次，之后零开销）。
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

# 本地代理常见端口（Clash Verge / ClashX / v2rayN 系）
COMMON_PORTS = (7897, 7890, 7891, 7892, 10809, 10808, 1080)

_PROBE_URL = "https://e-hentai.org/"     # 只判「能不能出去」，看内容没必要
_lock = threading.Lock()
_cache: Optional[Dict[str, str]] = None


def _normalize(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    return text


def system_proxies() -> Dict[str, str]:
    """读 Windows 系统代理并补齐协议头；读不到返回空字典。"""
    try:
        from common import ProxyBuilder
        raw = ProxyBuilder.system_proxy() or {}
    except Exception:  # noqa: BLE001 - 读不到就当没代理
        return {}
    out: Dict[str, str] = {}
    for key, value in raw.items():
        text = _normalize(value)
        if text:
            out[key] = text
    return out


def _candidates() -> List[Dict[str, str]]:
    """候选代理：系统代理排最前（它通常是用户真正选的节点），再补常见端口。"""
    out: List[Dict[str, str]] = []
    seen = set()
    sys_p = system_proxies()
    if sys_p:
        out.append(sys_p)
        seen.add(sys_p.get("https") or sys_p.get("http"))
    for port in COMMON_PORTS:
        url = f"http://127.0.0.1:{port}"
        if url in seen:
            continue
        seen.add(url)
        out.append({"http": url, "https": url})
    return out


def _alive(proxies: Dict[str, str]) -> bool:
    """真发一次请求验证（端口在监听 ≠ 上游节点能用，必须实探）。"""
    try:
        import requests
        return requests.get(_PROBE_URL, proxies=proxies,
                            timeout=4).status_code < 500
    except Exception:  # noqa: BLE001
        return False


def working_proxies(refresh: bool = False) -> Dict[str, str]:
    """返回一个**实测可用**的代理配置；全都不通时退回系统代理（可能为空）。

    结果按进程缓存 —— 第一次调用并发探测（最坏约 6 秒），之后零开销。
    """
    global _cache
    with _lock:
        if _cache is not None and not refresh:
            return _cache
    cands = _candidates()
    best = cands[0] if cands else {}
    if cands:
        # 并发探：串行的话 8 个候选最坏要 48 秒，用户会以为程序卡死
        with ThreadPoolExecutor(max_workers=len(cands)) as pool:
            flags = list(pool.map(_alive, cands))
        for cand, ok in zip(cands, flags):
            if ok:
                best = cand
                break
    with _lock:
        _cache = best
    return best