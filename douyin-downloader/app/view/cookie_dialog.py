# -*- coding: utf-8 -*-
"""Cookie 配置对话框：按平台给出所需字段的独立输入框"""
from typing import Dict, List, Tuple

from PySide6.QtCore import Qt
from qfluentwidgets import BodyLabel, LineEdit, MessageBox, StrongBodyLabel


class CookieDialog(MessageBox):
    """按字段填写的 Cookie 配置框。

    fields: [(字段名, 说明, 是否必填), ...]
    返回拼接好的 "k=v; k=v" 字符串（get_cookie_string）。
    """

    def __init__(self, title: str, guide: str,
                 fields: List[Tuple[str, str, bool]],
                 current: str = "", parent=None):
        super().__init__(title, guide, parent)
        self._edits: Dict[str, LineEdit] = {}
        current_kv = self._parse(current)

        for name, hint, required in fields:
            label = StrongBodyLabel(name + ("（必填）" if required else "（可选）"), self)
            self.textLayout.addWidget(label)
            edit = LineEdit(self)
            edit.setPlaceholderText(hint)
            edit.setClearButtonEnabled(True)
            if current_kv.get(name):
                edit.setText(current_kv[name])
            self.textLayout.addWidget(edit)
            self._edits[name] = edit

        tip = BodyLabel("保存时会自动拼接为标准 Cookie 格式，无需手动加分号和等号", self)
        tip.setTextColor("#909090", "#a0a0a0")
        self.textLayout.addWidget(tip)

        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")
        # 必填校验在 accept 前做
        self._required = [n for n, _, r in fields if r]

    @staticmethod
    def _parse(cookie: str) -> Dict[str, str]:
        kv = {}
        for pair in (cookie or "").split(";"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                kv[k.strip()] = v.strip()
        return kv

    def validate(self) -> bool:
        missing = [n for n in self._required if not self._edits[n].text().strip()]
        if missing:
            for n in missing:
                self._edits[n].setFocus()
            return False
        return True

    def get_cookie_string(self) -> str:
        parts = []
        for name, edit in self._edits.items():
            value = edit.text().strip()
            if value:
                parts.append(f"{name}={value}")
        return "; ".join(parts)

    def exec(self):
        # 点保存但未过必填校验时不关闭对话框
        while True:
            result = super().exec()
            if result and not self.validate():
                from qfluentwidgets import InfoBar, InfoBarPosition
                InfoBar.warning("缺少必填项", "请填写标记为必填的字段",
                                orient=Qt.Horizontal, isClosable=True,
                                position=InfoBarPosition.TOP, duration=2500,
                                parent=self)
                continue
            return result


DOUYIN_FIELDS = [
    ("sessionid", "抖音网页版登录后的 sessionid（最重要）", True),
    ("ttwid", "ttwid（可选，提高接口通过率）", False),
    ("msToken", "msToken（可选）", False),
]

X_FIELDS = [
    ("auth_token", "x.com 登录后的 auth_token", True),
    ("ct0", "x.com 登录后的 ct0（CSRF 令牌）", True),
]

INS_FIELDS = [
    ("sessionid", "instagram.com 登录后的 sessionid", True),
    ("csrftoken", "instagram.com 登录后的 csrftoken", True),
    ("ds_user_id", "instagram.com 登录后的 ds_user_id（可选）", False),
]

BILI_FIELDS = [
    ("SESSDATA", "bilibili.com 登录后的 SESSDATA（必填，登录态核心）", True),
    ("bili_jct", "bilibili.com 登录后的 bili_jct（可选，CSRF）", False),
    ("DedeUserID", "bilibili.com 登录后的 DedeUserID（可选）", False),
]

XHS_FIELDS = [
    ("web_session", "xiaohongshu.com 登录后的 web_session（最重要）", True),
    ("a1", "xiaohongshu.com 的 a1（设备标识，可选）", False),
    ("webId", "xiaohongshu.com 的 webId（可选）", False),
]

JM_FIELDS = [
    ("AVS", "禁漫登录后的 AVS（最重要，大部分本子免登录）", True),
]
