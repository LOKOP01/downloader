"""XHS PC 笔记详情接口（裁剪自 Spider_XHS 的 apis/xhs_pc_apis.py）。

仅保留「笔记详情」路径所需：get_note_info + bootstrap/get_user_me +
_request_params。签名材料统一走 XHSPcAuth；不引入 loguru，直接用 stdlib 记录。
"""

from __future__ import annotations

import urllib.parse

from .util import REQUEST_TIMEOUT
from .xhs_pc import XHSAuth, XHSPcAuth
from .xhs_pc.params import (
    PC_CURRENT_BROWSER_UA,
    build_pc_business_headers,
    generate_request_params,
    generate_x_rap_param,
)


# RAP 白名单（浏览器实抓：需 x-rap-param）
_RAP_PATH_MARKERS = (
    "api/sns/web/v1/homefeed",
    "api/sns/web/v1/search/notes",
    "api/sns/web/v2/search/notes",
    "api/sns/web/v1/user_posted",
    "api/sns/web/v1/feed",
    "api/sns/web/v1/comment/post",
)
# 浏览器实抓：仅这些接口带 xy-direction
_XY_PATH_MARKERS = (
    "api/sns/web/v1/homefeed",
    "api/sns/web/v1/feed",
)


def _get_query_params(parsed_url):
    return {
        key: values[-1] if values else ''
        for key, values in urllib.parse.parse_qs(
            parsed_url.query, keep_blank_values=True).items()
    }


class XHS_Apis:
    """PC 端笔记详情 API。鉴权材料全部走 XHSPcAuth。"""

    def __init__(self, auth: XHSAuth):
        if not isinstance(auth, XHSPcAuth):
            raise TypeError('XHS_Apis 当前仅支持 XHSPcAuth')
        self.auth: XHSPcAuth = auth
        self.http = auth.http_client
        self.base_url = auth.origin('api')

    def bootstrap(self, proxies: dict = None):
        """调 user/me 写入 auth.user_id（算 xy-direction 用）。"""
        success, msg, res = self.get_user_me(proxies)
        if not success:
            raise RuntimeError(f'bootstrap user/me failed: {msg}')
        uid = ((res or {}).get('data') or {}).get('user_id') or ''
        if not uid:
            raise RuntimeError('bootstrap: user/me 未返回 user_id')
        self.auth.set_user_id(uid)
        return self

    def _proxies(self, proxies: dict = None):
        return proxies if proxies is not None else self.auth.proxies

    @staticmethod
    def _needs_rap(api: str) -> bool:
        path = (api or "").split("?", 1)[0].rstrip("/").lstrip("/")
        for m in _RAP_PATH_MARKERS:
            if "user_posted" in m:
                if m in path:
                    return True
            elif path == m:
                return True
        return False

    @staticmethod
    def _needs_xy(api: str) -> bool:
        path = (api or "").split("?", 1)[0].rstrip("/").lstrip("/")
        return path in _XY_PATH_MARKERS

    def _request_params(self, api, data='', method='POST', tier=None, *,
                        target_origin=None):
        """按浏览器实抓组装头：x-s/x-t/x-s-common/x-b3-traceid/x-xray-traceid，
        条件带 x-rap-param（feed 等）与 xy-direction（仅 homefeed/feed）。"""
        self.auth.validate(require_user_id=False)
        if self._needs_xy(api) and not self.auth.user_id:
            self.bootstrap()
        sign_context = self.auth.next_sign_context(api, tier=tier)
        b1 = self.auth.current_b1(sign_context['now'])
        headers, cookies, body = generate_request_params(
            self.auth.cookies, api, data, method,
            user_id=self.auth.user_id,
            b1=b1,
            dsl_pair=self.auth.dsl_pair,
            doc_cookie=self.auth.sign_cookie,
            with_xy_direction=self._needs_xy(api),
            tier=sign_context['tier'],
            sign_context=sign_context,
            include_client_hints=False,
        )
        headers.pop("x-mns", None)
        if api.split('?', 1)[0] in {
            '/api/sns/web/v1/config',
            '/api/sns/web/v1/system/config',
            '/api/sns/web/v2/user/me',
        }:
            headers['cache-control'] = 'no-cache'
            headers['pragma'] = 'no-cache'
        if self._needs_rap(api):
            headers["x-rap-param"] = generate_x_rap_param(
                api,
                body or "",
                app_id=self.auth.profile.release['appId'],
                fingerprint_hex=self.auth.profile.rap_fingerprint_hex,
            )
        elif "x-rap-param" in headers:
            headers.pop("x-rap-param", None)
        target = (target_origin or self.base_url) + api
        wire_cookies = self.auth.cookies_for_url(target, cookies)
        headers = build_pc_business_headers(
            headers,
            wire_cookies,
            api=api,
            method=method,
        )
        headers['user-agent'] = sign_context.get('userAgent', PC_CURRENT_BROWSER_UA)
        return headers, wire_cookies, body

    def get_user_me(self, proxies: dict = None):
        res_json = None
        try:
            api = "/api/sns/web/v2/user/me"
            headers, cookies, data = self._request_params(api, '', 'GET')
            response = self.http.get(self.base_url + api, headers=headers,
                                     cookies=cookies, proxies=self._proxies(proxies),
                                     timeout=REQUEST_TIMEOUT)
            res_json = response.json()
            success, msg = res_json["success"], res_json["msg"]
        except Exception as e:  # noqa: BLE001
            success, msg = False, str(e)
        return success, msg, res_json

    def get_note_info(self, url: str, proxies: dict = None):
        """获取笔记详情。url 需为带 note_id 的完整链接（含 xsec_token/xsec_source）。"""
        res_json = None
        try:
            url_parse = urllib.parse.urlparse(url)
            note_id = url_parse.path.split("/")[-1]
            kv_dist = _get_query_params(url_parse)
            api = "/api/sns/web/v1/feed"
            data = {
                "source_note_id": note_id,
                "image_formats": ["jpg", "webp", "avif"],
                "extra": {"need_body_topic": "1"},
                "xsec_source": kv_dist.get('xsec_source', 'pc_search'),
                "xsec_token": kv_dist.get('xsec_token', ''),
            }
            headers, cookies, body = self._request_params(api, data, 'POST')
            response = self.http.post(self.base_url + api, headers=headers,
                                      data=body, cookies=cookies,
                                      proxies=self._proxies(proxies),
                                      timeout=REQUEST_TIMEOUT)
            res_json = response.json()
            success, msg = res_json["success"], res_json["msg"]
        except Exception as e:  # noqa: BLE001
            success, msg = False, str(e)
        return success, msg, res_json


__all__ = ['XHS_Apis', 'XHSAuth', 'XHSPcAuth']
