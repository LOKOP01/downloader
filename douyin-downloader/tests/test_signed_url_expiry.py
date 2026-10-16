# -*- coding: utf-8 -*-
"""抖音直链时效签名检测回归测试

对应真实故障：17:29 解析的链接，21:17 重试时 15 个候选地址全部 403。
根因是抖音 CDN 直链带时效签名（URL 里的 l= 参数是签发时刻）。

用真实日志里的 URL 片段做断言，不访问网络。
"""
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.downloader import (  # noqa: E402
    SIGNED_URL_TTL_SECONDS,
    signed_url_expired,
    signed_url_issued_at,
)

# 取自用户日志的真实 URL（已截断到关键部分，不含完整签名）
REAL_URL_1729 = ("https://v11-weba.douyinvod.com/dc650287ca7db343e588a509ab830961/"
                 "6ad218c2/video/tos/cn/tos-cn-ve-15/os8oGeV3BIeaRaTEiAVALCIEgS7cAJu8NByhVf/"
                 "?a=6383&ch=26&mime_type=video_mp4&qs=15"
                 "&l=20261016172945FA19D39086ECE2A161FA")


class SignedUrlIssuedAtTest(unittest.TestCase):
    def test_parses_real_douyin_url(self):
        issued = signed_url_issued_at(REAL_URL_1729)
        self.assertGreater(issued, 0)
        # 2026-10-16 17:29:45 本地时间
        import datetime
        dt = datetime.datetime.fromtimestamp(issued)
        self.assertEqual((2026, 10, 16, 17, 29, 45),
                         (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second))

    def test_returns_zero_without_signature(self):
        for url in ("https://example.com/video.mp4",
                    "https://v11-weba.douyinvod.com/x/?a=1",
                    "",
                    None):
            self.assertEqual(0.0, signed_url_issued_at(url), url)

    def test_returns_zero_on_malformed_timestamp(self):
        # 14 位但不是合法日期
        bad = "https://x/?l=99999999999999AAAAAAAA"
        self.assertEqual(0.0, signed_url_issued_at(bad))


class SignedUrlExpiredTest(unittest.TestCase):
    def test_old_url_is_expired(self):
        # 用户那次：17:29 解析 → 21:17 下载，间隔 3h47m，必然过期
        self.assertTrue(signed_url_expired(REAL_URL_1729))

    def test_fresh_url_is_not_expired(self):
        import datetime
        now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        fresh = "https://v11-weba.douyinvod.com/x/?l=%sABCDEF" % now
        self.assertFalse(signed_url_expired(fresh))

    def test_boundary_respects_ttl(self):
        import datetime
        # 刚过 TTL 一点点 → 过期
        past = (datetime.datetime.now()
                - datetime.timedelta(seconds=SIGNED_URL_TTL_SECONDS + 60))
        url = "https://x/?l=%sAAAA" % past.strftime("%Y%m%d%H%M%S")
        self.assertTrue(signed_url_expired(url))
        # TTL 内 → 未过期
        recent = (datetime.datetime.now()
                  - datetime.timedelta(seconds=SIGNED_URL_TTL_SECONDS - 60))
        url2 = "https://x/?l=%sAAAA" % recent.strftime("%Y%m%d%H%M%S")
        self.assertFalse(signed_url_expired(url2))

    def test_custom_ttl_is_honoured(self):
        import datetime
        one_hour_ago = (datetime.datetime.now()
                        - datetime.timedelta(hours=1)).strftime("%Y%m%d%H%M%S")
        url = "https://x/?l=%sAAAA" % one_hour_ago
        self.assertTrue(signed_url_expired(url, ttl=30 * 60))
        self.assertFalse(signed_url_expired(url, ttl=2 * 3600))

    def test_non_douyin_url_is_never_flagged(self):
        """不能误伤：没有签名参数的地址一律不判过期"""
        for url in ("https://cdn.example.com/a.mp4",
                    "https://upos-sz-mirror.bilivideo.com/x.m4s",
                    ""):
            self.assertFalse(signed_url_expired(url), url)


class ExpiredErrorTypeTest(unittest.TestCase):
    def test_signed_url_expired_is_distinct_from_value_error(self):
        """它必须能被单独捕获，才能避免跑完所有候选地址"""
        from app.core.downloader import SignedUrlExpired
        self.assertTrue(issubclass(SignedUrlExpired, Exception))
        # 不是 ValueError 的子类也行，但必须可在 _run 的 except 元组里被捕获
        self.assertIn(SignedUrlExpired, (SignedUrlExpired,))


if __name__ == "__main__":
    unittest.main()
