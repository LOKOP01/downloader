# -*- coding: utf-8 -*-
"""站点解析上下文与各平台 Cookie 的组装中心。

背景：`parse_share()` / `ParseWorker` 之前是一长串位置参数
（cookie / x_cookie / ins_cookie / ins_profile_dir / ... 共 11 个），
调用点必须按同样顺序把参数排好 —— 少传一个不会报错，只会静默用错
平台的 Cookie（或丢掉登录态）。这里把参数收进一个 dataclass，并统一
从 Config 组装，任何平台新增字段都只需改这一处。
"""
from dataclasses import dataclass, fields
from typing import Optional

# 各来源 -> 解析时需要携带的 Cookie 配置键
SOURCE_COOKIE_KEY = {
    "douyin": "cookie",
    "x": "x_cookie",
    "instagram": "ins_cookie",
    "bilibili": "bili_cookie",
    "xiaohongshu": "xhs_cookie",
    "jmcomic": "jm_cookie",
}


@dataclass
class SiteContext:
    """一次解析所需的全部站点凭据与路径"""
    cookie: str = ""                  # 抖音
    x_cookie: str = ""                # X(Twitter)
    ins_cookie: str = ""              # Instagram
    ins_profile_dir: str = ""
    x_profile_dir: str = ""
    dy_profile_dir: str = ""
    bili_cookie: str = ""             # Bilibili
    base_dir: str = ""                # 程序所在目录（定位 ffmpeg 等）
    xhs_cookie: str = ""              # 小红书
    xhs_profile_dir: str = ""
    jm_cookie: str = ""               # 禁漫天堂
    jm_profile_dir: str = ""

    @classmethod
    def from_config(cls, config) -> "SiteContext":
        """从 Config 组装（唯一入口，避免各处重复拼参数）"""
        if config is None:
            return cls()
        return cls(
            cookie=config.get("cookie", ""),
            x_cookie=config.get("x_cookie", ""),
            ins_cookie=config.get("ins_cookie", ""),
            ins_profile_dir=config.ins_profile_dir,
            x_profile_dir=config.x_profile_dir,
            dy_profile_dir=config.profile_dir,
            bili_cookie=config.get("bili_cookie", ""),
            base_dir=config.base_dir,
            xhs_cookie=config.get("xhs_cookie", ""),
            xhs_profile_dir=config.xhs_profile_dir,
            jm_cookie=config.get("jm_cookie", ""),
            jm_profile_dir=config.jm_profile_dir,
        )

    def cookie_for(self, source: str) -> str:
        """按平台取下载时该带的 Cookie（未知平台返回空串）"""
        key = SOURCE_COOKIE_KEY.get((source or "").strip().lower(), "")
        return getattr(self, key, "") if key else ""

    def replaced(self, **kwargs) -> "SiteContext":
        """返回替换了部分字段的新上下文（不改原对象）"""
        names = {f.name for f in fields(self)}
        data = {k: v for k, v in kwargs.items() if k in names}
        return SiteContext(**{**self.__dict__, **data})


_DEFAULT: Optional[SiteContext] = None


def default_site_context() -> SiteContext:
    """兜底上下文（批量下载等只有抖音链接的场景）"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = SiteContext()
    return _DEFAULT
