"""项目内合并的 XHS 上游小工具（原 cookie_util / http_util / xhs_util）。"""

from __future__ import annotations

REQUEST_TIMEOUT = 15


def trans_cookies(cookies_str):
    """把 "k=v; k=v" 或 "k=v;k=v" 转成 dict，保留原值里的 '='。"""
    if not cookies_str:
        return {}
    sep = '; ' if '; ' in cookies_str else ';'
    return {
        i.split('=')[0]: '='.join(i.split('=')[1:])
        for i in cookies_str.split(sep)
        if i
    }


__all__ = ['REQUEST_TIMEOUT', 'trans_cookies']
