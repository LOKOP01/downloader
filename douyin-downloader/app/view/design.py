# -*- coding: utf-8 -*-
"""设计系统基座：间距尺度、字阶、颜色 token、动效 helper。

把所有页面共用的「魔法值」集中到这里，避免 `#909090`、`36/24/16` 这类硬编码
散落在各 interface 里各写各的（改一处漏三处）。

设计方向见工作区根的 `.impeccable.md`：专业工具感 —— 精确、克制、可靠，
层级靠空间与字重而不是颜色堆叠，动效只做状态反馈与入场。
"""
from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QSizePolicy, QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, StrongBodyLabel,
                            TitleLabel)
from qfluentwidgets.common.config import qconfig
from qfluentwidgets.common.font import getFont
from qfluentwidgets.common.style_sheet import (CustomStyleSheet, ThemeColor,
                                               isDarkTheme, setStyleSheet,
                                               setThemeColor)

# --------------------------------------------------------------------- 间距
# 4px 基数。规则：同组紧邻用 4/6/8，卡片内块间隔用 12，区块之间放手到 24/32。
GAP_XS = 4
GAP_SM = 8
GAP_MD = 12
GAP_LG = 16
GAP_XL = 24
GAP_2XL = 32

PAGE_PAD_H = 36        # 页面左右留白
PAGE_PAD_TOP = 28      # 顶部（比底部紧一点，页头更贴近内容）
PAGE_PAD_BOTTOM = 32
HEADER_GAP = 6         # 页头内部：主标题 ↔ 元信息条
SECTION_GAP = 24       # 页头 → 首张卡片、卡片 → 卡片
CARD_PAD_H = 20
CARD_PAD_V = 16
CARD_GAP = 12          # 卡片内部块与块的间隔

# --------------------------------------------------------------------- 圆角
# 对齐 MXU 的四档（对应它的 --radius-sm/md/lg/xl）。
RADIUS_SM = 6          # 徽章、标签这类小控件
RADIUS_MD = 8          # 按钮、输入框
RADIUS_LG = 12         # 卡片、面板
RADIUS_XL = 16         # 大容器、对话框

# --------------------------------------------------------------------- 标题栏
# 对齐 MXU（它用的是 h-8 / w-12）：32px 高、右侧三个 48px 宽按钮。
# 关闭键 hover 用 Tailwind red-500 —— MXU 里就是 `hover:bg-red-500 hover:text-white`。
TITLEBAR_H = 32
TITLEBAR_BTN_W = 48
CLOSE_HOVER = "#EF4444"

# --------------------------------------------------------------------- 标签栏
# 对齐 MXU 的 TabBar（`src/components/TabBar.tsx`）：条高 **40px**（`h-10`）、
# 底色 bg-secondary + 底部 1px 分隔线；单个标签 `min-w-[120px]`、`text-sm`，
# 选中 `bg-primary + 强调色文字 + 2px 强调色下边框`，未选中 `bg-tertiary +
# 次级文字`、hover 变 bg-hover。右侧工具按钮是 `p-2` 的圆角方块（32×32）。
TABBAR_H = 40
TABBAR_MIN_W = 120
TABBAR_TOOL_W = 32      # 右侧工具按钮边长（MXU 是 p-2 + w-4 图标 = 32px）
TABBAR_ICON = 16        # 工具按钮图标边长

# --------------------------------------------------------------------- 字阶
# 固定 px 尺度，5 级：11 / 12 / 14 / 18 / 24。工具型 UI 不做流体字号。
FONT_MICRO = 11        # 最弱：单位、时间戳、占位提示
FONT_CAPTION = 12      # 次级说明、元信息条
FONT_BODY = 14         # 正文与区块标题（区块标题靠字重加粗区分）
FONT_TITLE = 18        # 内容标题（如解析出的作品名）
FONT_HEADING = 24      # 页面主标题

W_REGULAR = QFont.Weight.Normal
W_MEDIUM = QFont.Weight.Medium
W_SEMI = QFont.Weight.DemiBold


def font(size: int = FONT_BODY, weight=W_REGULAR) -> QFont:
    """按设计系统取字体（字体族跟随 qfluentwidgets 的全局设置）"""
    return getFont(size, weight)


# --------------------------------------------------------------------- 颜色
# 中性色阶对齐 MXU（MistEO/MXU，MaaEnd 的 GUI）：实为 Tailwind 的 zinc 灰阶。
# (浅色, 深色) 二元组，顺序与 qfluentwidgets 的 setTextColor(light, dark) 一致。
#
# 注意深色底的三个层次和浅色是**反过来**的：深色下 bg-primary 近纯黑、
# 面板比页面亮；浅色下页面是浅灰、面板纯白。照着抄别自己发挥。
TEXT_PRIMARY = ("#18181B", "#FAFAFA")      # zinc-900 / zinc-50
TEXT_SECONDARY = ("#52525B", "#A1A1AA")    # zinc-600 / zinc-400
TEXT_TERTIARY = ("#71717A", "#71717A")     # zinc-500（两套同值，MXU 就这么写的）
TEXT_ACCENT = ("#1A4A8E", "#8AB4FF")       # 强调文字/链接，取自深海蓝

# 语义色**不跟着 MXU 抄**：它的 warning #f59e0b / error #ef4444 是给
# 状态点那种小面积色块用的，当正文色在浅底上只有 2:1 上下，读不清。
# 这里保留按 WCAG AA 算过的原值。
SUCCESS = ("#0E7C4A", "#4ADE9B")
WARNING = ("#B45309", "#F0B03C")
DANGER = ("#C42B36", "#F2727C")

# 背景三级 + 交互态（对齐 MXU 的 bg.primary/secondary/tertiary/hover/active）。
# 层次规则：页面底 = primary，卡片/面板/标题栏 = secondary，
# 再往里一层（输入框、内嵌块）= tertiary。
BG_PRIMARY = ("#FAFAFA", "#09090B")
BG_SECONDARY = ("#FFFFFF", "#18181B")
BG_TERTIARY = ("#F4F4F5", "#27272A")
BG_HOVER = ("#E4E4E7", "#3F3F46")
BG_ACTIVE = ("#D4D4D8", "#52525B")

# 边框两级（对齐 MXU 的 border.default / border.strong）
BORDER = ("#E4E4E7", "#27272A")
BORDER_STRONG = ("#D4D4D8", "#3F3F46")

# 兼容旧名：SURFACE_SUNKEN 是"下沉"的浅填充（封面槽、图标底板），
# HAIRLINE 是 1px 描边/分隔线，两者分别指向新的三级底与边框。
SURFACE_SUNKEN = BG_TERTIARY
HAIRLINE = BORDER

# 帧率徽章配色（≥50 绿 / 30 中性 / 更低橙）
FPS_HIGH = ("rgba(22,163,74,0.18)", "#16A34A")
FPS_MID = ("rgba(128,128,128,0.18)", "#8A8F98")
FPS_LOW = ("rgba(217,119,6,0.18)", "#D97706")

# --------------------------------------------------------------------- 强调色
# 对齐 MXU 的 9 套预设（默认深海蓝），色值直接取自 MXU 仓库的
# `src/themes/presets/accents/*.json`，不自己发挥。
#
# 与旧约束的差异（旧值 blue/signal/indigo/magenta/rose 已移除以腾位置）：
# 旧约束来自 .impeccable.md ——「避开语义色（红/琥珀/绿）、避开被点名的
# AI 配色（青+深色、紫蓝渐变）、暖玫慎用」。MXU 的色板里有几支确实与
# 成功/警告同色相（宝石绿、熔岩橙），按 MXU 来就先放下这条：语义状态靠
# 图标 + 文案区分，不再依赖色相独占。
#
# 注意 qfluentwidgets 的 ThemeColor.color() 在深色主题里会把 v 强制拉到 1，
# 所以「输入色」和「实际看到的色」不是一回事（如 #0078D4 → #29a2ff）。
# 换色要真渲染出来看，别只看色值。
ACCENTS = [
    ("deepsea",  "深海蓝",   "#1A4A8E"),
    ("emerald",  "宝石绿",   "#008B45"),
    ("lava",     "熔岩橙",   "#E65A1E"),
    ("titanium", "钛金属",   "#91969A"),
    ("celadon",  "影青色",   "#97B6B0"),
    ("rosegold", "流金粉",   "#C1A1A1"),
    ("danxia",   "丹霞紫",   "#A58D92"),
    ("cambrian", "寒武岩灰", "#3B4754"),
    ("pearl",    "珍珠白",   "#D1D5DB"),
]
DEFAULT_ACCENT = "deepsea"
ACCENT_HEX = {key: hexv for key, _, hexv in ACCENTS}

_accent = DEFAULT_ACCENT


def apply_accent(key: str) -> str:
    """切换全局强调色，返回实际生效的 key（未知值回落默认）。

    不用 setThemeColor(save=True)：那会写进 qfluentwidgets 自己的配置文件，
    我们是落在项目的 config.json 里统一管的。
    """
    global _accent
    if key not in ACCENT_HEX:
        key = DEFAULT_ACCENT
    _accent = key
    setThemeColor(QColor(ACCENT_HEX[key]))
    return key


def accent_key() -> str:
    return _accent


# ------------------------------------------------------------------ 主色提亮修正
# qfluentwidgets 的 `ThemeColor.color()`（common/style_sheet.py）在深色主题下写死了：
#
#     if isDarkTheme():
#         s *= 0.84
#         v = 1              # ← 明度强制拉满
#
# 于是「输入什么色」和「深色下看到什么色」完全是两回事：MPXU 的深海蓝
# `#1A4A8E`（V≈0.56）会被拉成亮蓝。**光改输入色补偿不回来**，因为 v 恒为 1。
#
# 这里把深色分支的 PRIMARY 直接改回色板原值，其余派生变体（DARK_*/LIGHT_*，
# 用作 hover / pressed / 边框）仍按上游规则算，只把明度基准从 1 换成原始 v ——
# 否则那些变体会比主色亮一大截，层次就反了。
#
# 代价：这是改第三方库的行为，库升级后要回来核对上面那段源码还在不在。
# 保留原函数以便浅色分支走原逻辑。
_ORIG_THEME_COLOR = ThemeColor.color


def _patched_theme_color(self):
    if not isDarkTheme():
        return _ORIG_THEME_COLOR(self)

    base = QColor(qconfig.get(qconfig._cfg.themeColor))
    if self == ThemeColor.PRIMARY:
        return base                      # MXU：深色下主色就是色板原值

    h, s, v, _ = base.getHsvF()
    s *= 0.84
    vv = v
    if self == ThemeColor.DARK_1:
        vv *= 0.9
    elif self == ThemeColor.DARK_2:
        s *= 0.977
        vv *= 0.82
    elif self == ThemeColor.DARK_3:
        s *= 0.95
        vv *= 0.7
    elif self == ThemeColor.LIGHT_1:
        s *= 0.92
    elif self == ThemeColor.LIGHT_2:
        s *= 0.78
    elif self == ThemeColor.LIGHT_3:
        s *= 0.65
    out = QColor()
    out.setHsvF(h, min(s, 1.0), min(vv, 1.0))
    return out


ThemeColor.color = _patched_theme_color


# --------------------------------------------------------------------- 动效
# 只用于状态反馈与入场。禁止回弹/弹性曲线（观感廉价且拖慢感知速度）。
DUR_INSTANT = 120      # 即时反馈：徽章切换
DUR_BASE = 200         # 状态变化：显隐
DUR_ENTER = 300        # 入场：卡片出现、页面切换
EASE_ENTER = QEasingCurve.Type.OutCubic   # 自然减速，等价 CSS ease-out-quart
EASE_STATE = QEasingCurve.Type.OutCubic

_reduce_motion = False


def set_reduce_motion(flag: bool) -> None:
    """全局关闭动效（设置页开关 / 低配机器）"""
    global _reduce_motion
    _reduce_motion = bool(flag)


def reduce_motion() -> bool:
    return _reduce_motion


def fade_in(widget: QWidget, duration: int = DUR_ENTER, on_finished=None):
    """淡入一个控件，返回动画对象（不需持有也可）。

    动画结束后立刻摘掉 QGraphicsOpacityEffect —— 留着会让控件持续走离屏合成，
    滚动时掉帧（本项目对滚动流畅度很敏感）。
    """
    if widget is None or _reduce_motion or duration <= 0:
        if widget is not None:
            widget.setGraphicsEffect(None)
        if on_finished:
            on_finished()
        return None

    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)

    ani = QPropertyAnimation(effect, b"opacity", widget)
    ani.setDuration(duration)
    ani.setStartValue(0.0)
    ani.setEndValue(1.0)
    ani.setEasingCurve(EASE_ENTER)

    def _cleanup():
        # 延后一拍再摘效果：此刻动画还没析构，直接删目标对象不安全
        QTimer.singleShot(0, lambda: widget.setGraphicsEffect(None))
        if on_finished:
            on_finished()

    ani.finished.connect(_cleanup)
    ani.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return ani


def theme_qss(widget: QWidget, light_qss: str, dark_qss: str) -> None:
    """给控件套一套跟随主题的样式（浅/深各一份）。

    **不要**改成裸的 `setCustomStyleSheet` —— 那个函数只往控件上写两个动态属性，
    真正把属性变成 stylesheet 的是样式管理器里的 CustomStyleSheetWatcher，而
    watcher 只在控件注册进管理器之后才装上。qfluentwidgets 自己的标签能用是因为
    它在 `_init` 里先 `FluentStyleSheet.LABEL.apply(self)` 注册过一遍。
    对没注册过的裸 QLabel / QFrame，setCustomStyleSheet 之后 styleSheet() 仍是
    空字符串 —— 样式等于没设（占位块和分隔线就这么"消失"过一轮）。

    这里的 setStyleSheet(register=True) 会顺手完成注册，之后主题切换会自动重刷。
    """
    setStyleSheet(widget, CustomStyleSheet(widget).setCustomStyleSheet(
        light_qss, dark_qss))


# --------------------------------------------------------------------- 组件
class PageHeader(QWidget):
    """页头：主标题 + 一行元信息条。

    元信息条不重复 placeholder、不重复按钮字面 —— 只放扫一眼就知道
    "这个页面支持什么" 的信息，用最小字级 + 三级色，不抢主标题。
    """

    def __init__(self, title: str, meta: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(HEADER_GAP)

        self.titleLabel = TitleLabel(title, self)
        self.titleLabel.setFont(font(FONT_HEADING, W_SEMI))
        self.titleLabel.setTextColor(*TEXT_PRIMARY)
        lay.addWidget(self.titleLabel)

        self.metaLabel = None
        if meta:
            self.metaLabel = CaptionLabel(meta, self)
            self.metaLabel.setFont(font(FONT_CAPTION))
            self.metaLabel.setTextColor(*TEXT_TERTIARY)
            self.metaLabel.setWordWrap(True)
            lay.addWidget(self.metaLabel)


def page_layout(host: QWidget) -> QVBoxLayout:
    """页面根布局：统一页边距与区块间距（页头内部紧、区块之间放手）"""
    box = QVBoxLayout(host)
    box.setContentsMargins(PAGE_PAD_H, PAGE_PAD_TOP, PAGE_PAD_H, PAGE_PAD_BOTTOM)
    box.setSpacing(SECTION_GAP)
    return box


def card(parent, pad_h: int = CARD_PAD_H, pad_v: int = CARD_PAD_V,
         gap: int = CARD_GAP):
    """卡片 + 内容布局，返回 (card, layout)。

    统一 padding/gap，免得每个页面各写一套 20,16,20,16。
    圆角显式设成 RADIUS_LG：qfluentwidgets 的 CardWidget 默认只有 5px，
    太方；MXU 的面板是 `rounded-lg`（12px）。
    """
    c = CardWidget(parent)
    c.setBorderRadius(RADIUS_LG)
    lay = QVBoxLayout(c)
    lay.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
    lay.setSpacing(gap)
    return c, lay


def section_label(text: str, parent=None) -> StrongBodyLabel:
    """卡片内的区块标题（14 DemiBold）——靠字重与颜色与正文拉开，不靠字号"""
    lab = StrongBodyLabel(text, parent)
    lab.setFont(font(FONT_BODY, W_SEMI))
    lab.setTextColor(*TEXT_PRIMARY)
    return lab


def hint_label(text: str = "", parent=None) -> CaptionLabel:
    """次级说明（12，三级色）"""
    lab = CaptionLabel(text, parent)
    lab.setFont(font(FONT_CAPTION))
    lab.setTextColor(*TEXT_TERTIARY)
    return lab


def caption(text: str = "", parent=None) -> BodyLabel:
    """正文级说明（14，次级色）"""
    lab = BodyLabel(text, parent)
    lab.setFont(font(FONT_BODY))
    lab.setTextColor(*TEXT_SECONDARY)
    return lab


class Divider(QFrame):
    """1px 分隔线（浅/深色各一套，白底深底都压得住）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hDivider")
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme_qss(self,
                  f"QFrame#hDivider{{background:{HAIRLINE[0]};}}",
                  f"QFrame#hDivider{{background:{HAIRLINE[1]};}}")


class Placeholder(QLabel):
    """占位块：下沉填充 + 1px 描边（封面未加载、缩略图缺失时用）。

    比原来的 `rgba(128,128,128,40)` 灰块多做三件事：跟着主题走、有描边所以能
    读出"这是一个槽位"、文字用次级色（填充调深后仍满足 4.5:1）。
    用描边而不是加深填充，是为了不跟块内文字的对比度打架。
    """

    def __init__(self, text: str = "", size=None, radius: int = 8, parent=None):
        super().__init__(text, parent)
        self.setObjectName("placeholderBox")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(font(FONT_MICRO))
        self._radius = radius
        if size:
            self.setFixedSize(size[0], size[1])
        theme_qss(
            self,
            f"QLabel#placeholderBox{{border-radius:{radius}px;"
            f"color:{TEXT_SECONDARY[0]};background:{SURFACE_SUNKEN[0]};"
            f"border:1px solid {HAIRLINE[0]};}}",
            f"QLabel#placeholderBox{{border-radius:{radius}px;"
            f"color:{TEXT_SECONDARY[1]};background:{SURFACE_SUNKEN[1]};"
            f"border:1px solid {HAIRLINE[1]};}}")

    def set_image(self, pixmap) -> None:
        """按「cover」方式填入图片：等比放大到铺满 + 居中裁切 + 圆角。

        原来的写法是 KeepAspectRatio 塞进固定框 —— 竖版封面会左右留黑边、方角
        盖住占位块的圆角。这里裁成圆角并铺满，观感更整。
        """
        if pixmap is None or pixmap.isNull() or self.width() <= 0:
            return
        scaled = pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        target = QPixmap(self.size())
        target.fill(Qt.GlobalColor.transparent)
        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(target.rect()), self._radius, self._radius)
        painter.setClipPath(path)
        painter.drawPixmap(-(scaled.width() - self.width()) // 2,
                           -(scaled.height() - self.height()) // 2, scaled)
        painter.end()
        self.setText("")
        self.setPixmap(target)

    def reset(self, text: str = "封面") -> None:
        """回到未加载状态：清掉图片，只留占位底 + 提示字"""
        self.clear()
        self.setText(text)


class StatStrip(QLabel):
    """统计数据条：数字加粗、标签弱化。

    原实现把「点赞 123   评论 45」写成同一权重的纯文本 —— 数字淹没在标签里。
    这里让数字成为视觉主体（14 DemiBold，主文字色），标签退成背景（12 三级色）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statStrip")
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setFont(font(FONT_CAPTION))
        theme_qss(
            self,
            f"QLabel#statStrip{{color:{TEXT_TERTIARY[0]};}}",
            f"QLabel#statStrip{{color:{TEXT_TERTIARY[1]};}}")

    def set_stats(self, items) -> None:
        """items: [(标签, 值), ...]"""
        parts = []
        for label, value in items:
            parts.append(
                f'<span style="font-weight:600; font-size:{FONT_BODY}px;">'
                f'{value}</span>&nbsp;{label}')
        self.setText("&nbsp;&nbsp;&nbsp;".join(parts))


def icon_group(parent=None, gap: int = GAP_XS) -> QHBoxLayout:
    """一排贴右的图标按钮"""
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(gap)
    return lay
