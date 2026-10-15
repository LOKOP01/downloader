# -*- coding: utf-8 -*-
"""系统代理读取（各 HTTP 客户端共用）。

`system_proxies()`：读 Windows 系统代理并补齐协议头。系统代理常是
`127.0.0.1:2410` 这种**不带协议头**的写法，requests / curl_cffi 都要
`http://` 前缀才认。读不到就返回空字典（调用方按直连处理）。
"""
from typing import Dict


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