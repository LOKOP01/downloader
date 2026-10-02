# -*- coding: utf-8 -*-
"""账号卡：一个平台一张卡，把「网页登录 / 填 Cookie」两个入口收进同一行。

改动背景：设置页原先把 6 个平台的登录与 Cookie 摊成 11 张结构完全相同的卡片，
找「小红书 Cookie」要扫 11 行 —— 这是整页认知负载最高的地方（见 .impeccable.md
的设计原则 2：同构即噪声）。现在按平台聚合，一张卡 = 一个平台 = 一行。

登录态有三种真实状态，都要能表达，否则会出现「左边登录成功、右边显示未配置」
的自相矛盾：
  - 未配置      既没有持久化登录态，也没有手填 Cookie
  - 已登录      浏览器 profile 里有登录态（网页登录过）
  - 已填 Cookie 配置里存了 Cookie 字符串（手填或登录时自动导出）
"""
import os

from PySide6.QtCore import Qt, Signal

from qfluentwidgets import CaptionLabel, PushButton, SettingCard

from . import design as D

STATE_NONE = "none"
STATE_LOGIN = "login"
STATE_COOKIE = "cookie"


def profile_logged_in(profile_dir: str) -> bool:
    """浏览器 profile 目录里存在登录态。

    Edge 的持久化 profile 一定含 Default 子目录，比 listdir 便宜也更可靠
    （登录过一次就会创建，空目录则不算）。
    """
    if not profile_dir:
        return False
    return os.path.isdir(os.path.join(profile_dir, "Default"))


class AccountCard(SettingCard):
    """一个平台的账号入口：左侧平台名 + 说明，右侧登录态 + 两个入口按钮"""

    loginRequested = Signal()
    cookieRequested = Signal()

    def __init__(self, icon, name: str, desc: str, parent=None):
        super().__init__(icon, name, desc, parent)
        self._descText = desc
        self._busy = False

        # 登录态：小写字 + 状态色，放在按钮左边，扫一眼就知道这个平台要不要配置
        self.stateLabel = CaptionLabel("未配置", self)
        self.stateLabel.setFont(D.font(D.FONT_CAPTION, D.W_MEDIUM))
        self.hBoxLayout.addWidget(self.stateLabel, 0,
                                  Qt.AlignmentFlag.AlignVCenter)
        self.hBoxLayout.addSpacing(D.GAP_LG)

        self.loginBtn = PushButton("网页登录", self)
        self.loginBtn.setToolTip(
            "打开浏览器登录，登录态本地保存 —— 无需手动提取 Cookie（推荐）")
        self.loginBtn.clicked.connect(self.loginRequested)
        self.hBoxLayout.addWidget(self.loginBtn)
        self.hBoxLayout.addSpacing(D.GAP_SM)

        self.cookieBtn = PushButton("填 Cookie", self)
        self.cookieBtn.setToolTip("手动填写该平台的 Cookie 字段")
        self.cookieBtn.clicked.connect(self.cookieRequested)
        self.hBoxLayout.addWidget(self.cookieBtn)

        self.set_state(STATE_NONE)

    # ------------------------------------------------------------------ #
    def set_state(self, state: str, note: str = "") -> None:
        """更新登录态显示。note 非空时作为 tooltip 的补充说明。"""
        if state == STATE_LOGIN:
            text, color = "已登录", D.SUCCESS
            tip = "浏览器登录态已保存在本地（网页登录）"
        elif state == STATE_COOKIE:
            text, color = "已填 Cookie", D.SUCCESS
            tip = "配置里已存有该平台的 Cookie"
        else:
            text, color = "未配置", D.TEXT_TERTIARY
            tip = "尚未配置：免登录的站点可以直接用，受限内容需要先登录或填 Cookie"
        self.stateLabel.setText(f"● {text}")
        self.stateLabel.setTextColor(*color)
        self.stateLabel.setToolTip(f"{tip}\n{note}" if note else tip)

    def set_busy(self, busy: bool) -> None:
        """登录过程中禁用本卡入口，避免并发开多个浏览器窗口"""
        self._busy = bool(busy)
        self.loginBtn.setEnabled(not busy)
        self.cookieBtn.setEnabled(not busy)
        self.setContent("正在等待浏览器登录 …" if busy else self._descText)

    def is_busy(self) -> bool:
        return self._busy
