# -*- coding: utf-8 -*-
"""数据模型定义"""
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class VideoInfo:
    """单个作品（视频 / 图集）的解析结果"""
    aweme_id: str = ""
    title: str = ""               # 作品标题/文案
    author: str = ""              # 作者昵称
    author_uid: str = ""          # 作者 uid
    sec_uid: str = ""             # 作者 sec_uid（用于批量抓取其作品）
    cover_url: str = ""           # 封面地址
    play_url: str = ""            # 无水印播放地址（最高可获得画质）
    play_url_candidates: List[str] = field(default_factory=list)  # 备用地址（高→低画质）
    quality_options: List[Tuple[str, str]] = field(default_factory=list)  # [(画质标签, 地址)]
    # 视频地址 → 文件字节数（用于在下拉里显示"1080p · 12.3 MB"）
    # 键与 quality_options 里的地址一一对应；拿不到大小的档位可以不进这个表
    quality_sizes: Dict[str, int] = field(default_factory=dict)
    audio_map: Dict[str, str] = field(default_factory=dict)  # 视频地址 → 配套音频地址（DASH 需合并）
    # 同一路流的其他可用地址（CDN 多源）。B站 playurl 的 baseUrl 挂在 PCDN 节点
    # （cn-jsnj-fx-*）上，时好时坏；接口同时给的 backupUrl 才是稳定镜像。只认 baseUrl
    # 时主地址一挂整单就卡在 0%（2026-09-22「琵琶曲」卡死就是这么来的）。
    url_backups: Dict[str, List[str]] = field(default_factory=dict)   # 视频主地址 → 同档备用地址
    audio_backups: Dict[str, List[str]] = field(default_factory=dict)  # 音频主地址 → 备用地址
    raw_play_url: str = ""        # 原始（带水印）地址
    quality: str = ""             # 画质标签，如 1080p
    source: str = "douyin"        # 来源平台：douyin / x / instagram
    duration: int = 0             # 毫秒
    digg_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    create_time: int = 0
    is_image: bool = False        # 是否为图文/图集
    image_urls: List[str] = field(default_factory=list)
    music_title: str = ""
    extra: Dict[str, str] = field(default_factory=dict)  # jmcomic chapters/pages

    @property
    def duration_text(self) -> str:
        sec = self.duration // 1000
        return f"{sec // 60:02d}:{sec % 60:02d}" if sec else "--:--"

    @property
    def type_text(self) -> str:
        src = (self.source or "").lower()
        if src in ("jmcomic", "jm", "18comic"):
            return "禁漫本子"
        return "图集" if self.is_image else "视频"

    def safe_title(self, max_len: int = 60) -> str:
        """生成可作为文件名的标题"""
        import re
        name = self.title.strip() or self.aweme_id
        name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name)
        return name[:max_len].strip() or self.aweme_id

    @property
    def unique_id(self) -> str:
        """作品在整个下载目录里的唯一标识（用于增量更新去重）

        各平台的 `aweme_id` 语义不同，但都满足「同一作品跨次解析稳定不变」：
        抖音/B站/X/小红书/Iwara/Pornhub/hanime1 是平台作品 ID，Instagram 是
        shortcode，禁漫是车号。`source` 前缀避免不同平台撞号（Iwara 与
        hanime1 的 ID 都可能出现纯数字）。
        识别不出 ID 时回退到「来源_标题」，宁可比对偏严也不要漏判重复。
        """
        aid = (self.aweme_id or "").strip()
        if aid:
            return f"{self.source}_{aid}"
        return f"{self.source}_{self.safe_title(40)}"

    def size_of(self, url: str) -> int:
        """取某个档位地址对应的文件大小（字节）；未知返回 0"""
        return int(self.quality_sizes.get(url or "", 0) or 0)

    def backups_of(self, url: str) -> List[str]:
        """某个视频地址的同档备用地址（无则空列表）"""
        return [u for u in (self.url_backups.get(url or "") or []) if u]

    def audio_backups_of(self, url: str) -> List[str]:
        """某路音频地址的备用地址（无则空列表）"""
        return [u for u in (self.audio_backups.get(url or "") or []) if u]

    def quality_label(self, index: int, with_size: bool = True) -> str:
        """生成下拉里显示的文字：'1080×1920 · 2500kbps · 12.3 MB'"""
        if not self.quality_options or not (0 <= index < len(self.quality_options)):
            return ""
        label, url = self.quality_options[index]
        if with_size:
            size = self.size_of(url)
            if size > 0:
                return f"{label} · {fmt_size(size)}"
        return label

    def fps_of(self, url: str) -> int:
        """取某个档位地址的帧率（标签里的 '60fps'）；未知返回 0"""
        for label, u in self.quality_options or []:
            if u == (url or ""):
                return fps_in_label(label)
        return 0

    def fps_at(self, index: int) -> int:
        """取第 index 档的帧率；未知返回 0"""
        if not self.quality_options or not (0 <= index < len(self.quality_options)):
            return 0
        return fps_in_label(self.quality_options[index][0])


@dataclass
class DownloadTaskInfo:
    """下载任务状态"""
    task_id: str = ""
    url: str = ""
    save_path: str = ""
    name: str = ""
    quality: str = ""             # 所选清晰度标签（含帧率，如 '1920×1080 · 1700kbps · 60fps'）
    total: int = 0
    downloaded: int = 0
    speed: float = 0.0            # bytes/s
    status: str = "等待中"        # 等待中/下载中/已完成/失败/已取消
    error: str = ""

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(100, int(self.downloaded * 100 / self.total))


def scan_downloaded_ids(base_dir: str, source: str = "") -> set:
    """扫描下载目录，返回「已存在文件的名称前缀」集合（供增量更新判重）

    返回的是**文件名去掉扩展名后的原串**，例如
    `douyin_7123456789_标题_20261004_213700`、`x_1234567890_标题`。
    调用方用 `is_already_downloaded(info, ids)` 判断某作品是否已下过 ——
    两者约定：只要文件名**以 `<唯一ID>` 或 `<唯一ID>_` 开头**即算命中，
    因此重名自动追加的 `_1` / `_2` 后缀不影响判定。

    扫描范围：下载目录一层 + 各作者子目录一层（`create_author_folder`
    开启时的布局）。图集是「作品ID 同名子文件夹」，目录名同样会被收进来，
    于是图集也能被正确判重。
    """
    found = set()
    if not base_dir or not os.path.isdir(base_dir):
        return found
    want_src = f"{source}_" if source else ""

    def _take(name: str):
        stem = os.path.splitext(name)[0]
        if not stem:
            return
        if want_src and not stem.startswith(want_src):
            return
        found.add(stem)

    try:
        names = os.listdir(base_dir)
    except OSError:
        return found
    for name in names:
        _take(name)                       # 平铺文件 / 图集子文件夹
        sub = os.path.join(base_dir, name)
        if os.path.isdir(sub):
            try:
                for f in os.listdir(sub):
                    _take(f)              # 作者子目录里的文件
            except OSError:
                continue
    return found


def is_already_downloaded(info, existing: set) -> bool:
    """判断某个作品是否已存在于下载目录

    `existing` 为 `scan_downloaded_ids` 的返回值。判定方式：文件名**以
    `<唯一ID>` 开头且紧跟边界**（结尾或 `_`）即视为已下载。唯一命名规则下
    新文件一定以 ID 开头；历史文件若不含 ID 则无法判定，返回 False
    （宁可重下也不漏下）。

    **为什么不能只看前缀**：ID 之间常有包含关系（`x_1234` 是 `x_12345` 的
    前缀），只用 `startswith(uid)` 会把别人的作品误判成已下载，导致整件作品
    被增量更新跳过。这里要求 uid 之后必须是字符串结尾或 `_` —— 而 `<uid>_`
    这个形态是「重名后缀 `_1`」和「uid 后接标题」共有的合法形态。
    """
    uid = (info.unique_id or "").strip()
    if not uid or not existing:
        return False
    for stem in existing:
        if stem == uid:
            return True
        # 只认 `<uid>_…`：排除 `x_12345_bar` 这类「同前缀但 ID 更长」的误命中
        if stem.startswith(uid) and len(stem) > len(uid) and stem[len(uid)] == "_":
            return True
    return False


def fmt_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} GB"


def fmt_speed(bps: float) -> str:
    return f"{fmt_size(bps)}/s"


def fmt_count(n) -> str:
    """互动数（点赞/评论/分享）的可扫读形式：过万折成「万」。

    界面上一串 `128473` 要数位才能读，`12.8万` 一眼就知道量级。
    精确值由调用方放在 tooltip 里，信息不丢。
    """
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return str(n)
    if n < 10000:
        return str(n)
    if n < 100000000:
        return f"{n / 10000:.1f}万"
    return f"{n / 100000000:.1f}亿"


def fps_in_label(label: str) -> int:
    """从画质标签里取帧率；拿不到返回 0

    各家写法不一样，都要认：
    - `1890×1440 · 1321kbps · 30fps`（抖音、B 站 DASH 打的新标签）
    - `1080P60 高帧率`（B 站清晰度描述：`P` 后面直接跟帧率）
    - `1080P 60帧`（中文「帧」）
    只看 `fps/帧/P+数字` 这几种，不会把 `2500kbps`、`H.265` 里的数字当帧率。
    """
    text = label or ""
    for pattern in _FPS_PATTERNS:
        m = pattern.search(text)
        if m:
            return int(m.group(1))
    return 0


_RES_PATTERN = re.compile(r'(\d{2,5})×(\d{2,5})')
# 只有高度时（`720p`、B 站 `1080P60`）：`P` 后面允许跟 1~2 位的帧率，
# 但不能跟另一个分辨率数字（`1080P1080` 这种不是真的标签）
_HEIGHT_PATTERN = re.compile(r'(\d{3,4})\s*p(?![0-9]{3})', re.I)
_FPS_PATTERNS = (
    re.compile(r'(\d{2,3})\s*fps', re.I),     # 60fps
    re.compile(r'(\d{2,3})\s*帧'),             # 60帧
    re.compile(r'p(\d{2,3})(?![0-9])', re.I),  # B 站 1080P60 / 720P60
)
HIGH_FPS = 50          # 「60fps 级」的门槛：≥50 算高帧率（30fps 那条就是 30）


def resolution_area(label: str) -> int:
    """从画质标签里取分辨率「面积」，用来比较档位高低；取不到返回 0

    标签形如 `1890×1440 · 1321kbps · 30fps`（抖音）、`1080P 1920×1080（最高）`
    （B 站 DASH）、`720P`（B 站 durl 回退）。分辨率用严格匹配，避免把
    `60fps` 里的 60、`2500kbps` 里的 2500 当成分辨率。只有高度时按 16:9
    折算宽度，只为让同一来源的档位能互相比较，不代表真实画面尺寸。
    """
    m = _RES_PATTERN.search(label or "")
    if m:
        return int(m.group(1)) * int(m.group(2))
    m = _HEIGHT_PATTERN.search(label or "")
    if m:
        h = int(m.group(1))
        return h * h * 16 // 9
    return 0


def top_resolution_indices(options, keep: int = 3) -> List[int]:
    """下拉里要展示的档位下标：分辨率只保留前三高的，太低的直接省略

    抖音一条作品的 bit_rate 常有近 20 档（同分辨率多个码率），整列铺开要滚
    好几屏，低分辨率那堆基本没人会选。规则：
    - 同一种分辨率算一档，只保留最高的 keep 种，其余省略；
    - 分辨率种类 ≤ keep 时全部展示（不折腾）；
    - 标签里读不出分辨率的档位（`最高画质`／`视频`）不参与分组，始终保留，
      免得把来源自己的兜底档位藏掉。
    """
    if keep <= 0 or not options:
        return list(range(len(options or [])))
    areas = [resolution_area(label) for label, _ in options]
    known = sorted({a for a in areas if a > 0}, reverse=True)
    if len(known) <= keep:
        return list(range(len(options)))
    allowed = set(known[:keep])
    return [i for i, a in enumerate(areas) if a <= 0 or a in allowed]


def dropdown_quality_indices(options, keep: int = 3) -> List[int]:
    """清晰度下拉最终摆哪些档位（首页下拉用这个，别直接用 top_resolution_indices）

    两条规则叠在一起：
    1. **有 60fps 级的源（≥50fps）时，30fps 那些直接不显示** —— 抖音同分辨率
       常同时给 `normal_1080_0`（30fps · 3041kbps）和 `adapt_lowest_1080_1`
       （60fps · 1700kbps），30fps 码率更高但更卡；有 60fps 就没必要再列 30fps。
    2. 剩下的里分辨率只保留前三高的种类（同分辨率的多个码率都算一档），太低的
       省略；种类 ≤ keep 时全部显示。

    帧率或分辨率读不出来的档位（`最高画质（原始）`／`视频`）不参与这两条判断，
    始终保留 —— 拿不准的不动，免得把来源自己的兜底档位藏掉。
    """
    fps_all = [fps_in_label(label) for label, _ in options]
    if any(f >= HIGH_FPS for f in fps_all):
        pool = [i for i, f in enumerate(fps_all) if f == 0 or f >= HIGH_FPS]
    else:
        pool = list(range(len(options)))
    keep_pos = top_resolution_indices([options[i] for i in pool], keep)
    return [pool[p] for p in keep_pos]


def quality_html(label: str) -> str:
    """画质标签 → 带高亮帧率的富文本（60fps 绿、30fps 灰），无帧率则原样返回

    用于「下载任务」「批量下载」表格：帧率是选档位时最容易忽略的一项，
    单独上色后一眼能看出下的是 60fps 还是 30fps 那条。
    """
    if not label:
        return ""
    fps = fps_in_label(label)
    if not fps:
        return label
    head = (label or "").replace(f" · {fps}fps", "").strip(" ·")
    color = "#16A34A" if fps >= 50 else "#8A8F98"
    return (f'{head} · <b><span style="color:{color}">{fps}fps</span></b>'
            if head else f'<b><span style="color:{color}">{fps}fps</span></b>')


def clean_cookie(cookie: str) -> str:
    """清洗 Cookie 字符串，保证能安全放进 requests 的请求头

    `requests` 按 RFC 2616 用 latin-1 编码请求头，Cookie 值里只要出现
    非 Latin-1 字符（中文、emoji、以及 U+07A4 这类真实出现过的码位），
    就会抛：
        UnicodeEncodeError: 'latin-1' codec can't encode character ...
    浏览器自己在 HTTP 层做的是 **percent-encoding**，所以这些值在浏览器
    里工作正常；但把 Playwright / SQLite 里取到的原始值直接塞进 requests
    header 就会崩。

    关键点：**不能简单删字符**。X 的 `auth_token` / `ct0` 本身就含
    U+07A4 等码位，而且 `ct0` 是 `auth_token` 的哈希 —— 删掉字符会把
    两者改成不一致，服务端直接 401/400（实测踩过这个坑）。
    正确做法跟浏览器一致：把这些字节 **percent-encode**，长度与语义都保住。

    键名必须是合法 ASCII token；含非 ASCII 或分隔符的整条丢弃（这类不是
    真 Cookie，通常是导出时的脏数据）。
    """
    if not cookie:
        return ""
    parts = []
    for pair in cookie.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k or any(ord(c) > 127 for c in k) or any(c in k for c in ' =,;"'):
            continue
        try:
            v.encode("latin-1")
        except UnicodeEncodeError:
            # 与浏览器一致：按 UTF-8 取字节再 percent-encode
            v = urllib.parse.quote(v, safe="")
        parts.append(f"{k}={v}")
    return "; ".join(parts)
