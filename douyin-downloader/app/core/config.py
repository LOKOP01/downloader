# -*- coding: utf-8 -*-
"""应用配置持久化（JSON 文件）"""
import json
import os
import threading

DEFAULT_CONFIG = {
    "download_path": "",          # 默认为 <用户目录>/Downloads/DouyinDownloader
    "cookie": "",                  # 抖音 Cookie
    "x_cookie": "",                # X(Twitter) Cookie（解析敏感内容推文用）
    "ins_cookie": "",              # Instagram Cookie（解析需登录内容用）
    "bili_cookie": "",             # Bilibili Cookie（登录后自动导出，或手填）
    "xhs_cookie": "",              # 小红书 Cookie（登录后自动导出，或手填）
    "jm_cookie": "",               # 禁漫天堂 Cookie（AVS，登录后导出或手填）
    "naming_rule": "unique_id",    # unique_id / timestamp / author_title / title / id_title
    "max_concurrent": 3,
    "theme": "dark",                 # dark / light / auto
    "accent_color": "deepsea",       # 界面强调色，取值见 view/design.ACCENTS（9 套预设）
    "create_author_folder": True,
    "download_cover": False,
    "auto_copy_file": True,       # 单个视频下载完成后自动复制文件到剪贴板
    "mica_effect": False,          # Win11 云母特效（开启会降低滚动流畅度）
    "reduce_motion": False,        # 关闭界面动效（卡片入场/页面切换过渡）
    "timeout": 15,
    "segment_download": True,     # 多连接分段下载（IDM 式加速）
    "segment_connections": 8,     # 每文件并发连接数（IDM 默认 8，范围 1-32）
}

_lock = threading.Lock()

# 取值只能是 int 的配置项：JSON 里手改出 "abc" 这类值时回退默认值，
# 避免 main_window 构造期 int() 抛 ValueError 直接打断启动。
_INT_KEYS = {"max_concurrent": 3, "segment_connections": 8, "timeout": 15}
# segment_connections 的合法区间（下载器按 1-32 使用）
_CONN_RANGE = (1, 32)


def default_download_path() -> str:
    return os.path.join(os.path.expanduser("~"), "Downloads", "DouyinDownloader")


def _coerce_int(key: str, value) -> int:
    """把配置值收敛成合法 int；坏值回退默认值，不抛异常"""
    default = _INT_KEYS[key]
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    if key == "segment_connections":
        return max(_CONN_RANGE[0], min(_CONN_RANGE[1], num))
    if key == "max_concurrent":
        return max(1, min(16, num))
    if key == "timeout":
        return max(1, num)
    return num


class Config:
    def __init__(self, path: str):
        self.path = path
        self._data = dict(DEFAULT_CONFIG)
        # 上次 load 是否读到了「无法解析」的文件：损坏时备份原文件，
        # 既不让 save() 静默覆盖掉用户的 Cookie，也不让下次启动继续报错。
        self.load_error = ""
        self.load()

    def load(self):
        self.load_error = ""
        with _lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as f:
                        saved = json.load(f)
                except (ValueError, OSError) as e:
                    # 顶层不是 JSON 对象（[]、null、数字、字符串）时 .items() 会抛
                    # AttributeError，此前没被 except 覆盖，导致 Config 构造失败、
                    # 整个程序起不来。这里统一兜住并备份坏文件。
                    self.load_error = f"{type(e).__name__}: {e}"
                    self._backup_corrupt()
                else:
                    if isinstance(saved, dict):
                        self._data.update({k: v for k, v in saved.items()
                                           if k in DEFAULT_CONFIG})
                    else:
                        self.load_error = f"配置根节点应为对象，实际是 {type(saved).__name__}"
                        self._backup_corrupt()
        for key in _INT_KEYS:
            self._data[key] = _coerce_int(key, self._data.get(key, DEFAULT_CONFIG[key]))
        if not self._data.get("download_path"):
            self._data["download_path"] = default_download_path()

    def _backup_corrupt(self):
        """把读不懂的 config.json 改名留档，避免随后的 save() 把它整体覆盖"""
        try:
            backup = f"{self.path}.corrupt"
            os.replace(self.path, backup)
        except OSError:
            pass

    def save(self):
        # self._data 在锁内序列化：避免与 set() 并发时写出"写了一半"的字典。
        # 先写临时文件再 os.replace：进程被杀 / 磁盘满时不会留下半截 JSON。
        with _lock:
            try:
                payload = json.dumps(self._data, ensure_ascii=False, indent=2)
                parent = os.path.dirname(self.path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                tmp = f"{self.path}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except (OSError, TypeError, ValueError):
                pass

    def update(self, values: dict):
        """批量写入多个配置项，只落盘一次"""
        with _lock:
            self._data.update(values)
        self.save()

    def get(self, key, default=None):
        with _lock:
            return self._data.get(key, DEFAULT_CONFIG.get(key, default))

    @property
    def profile_dir(self) -> str:
        """Edge 持久化配置文件目录（保存抖音登录态），与 config.json 同级"""
        return os.path.join(os.path.dirname(os.path.abspath(self.path)),
                            "edge_profile")

    @property
    def base_dir(self) -> str:
        """程序所在目录（exe 同级）"""
        return os.path.dirname(os.path.abspath(self.path))

    @property
    def ins_profile_dir(self) -> str:
        """Edge 持久化配置文件目录（保存 Instagram 登录态）"""
        return os.path.join(os.path.dirname(os.path.abspath(self.path)),
                            "edge_profile_ins")

    @property
    def x_profile_dir(self) -> str:
        """Edge 持久化配置文件目录（保存 X 登录态）"""
        return os.path.join(os.path.dirname(os.path.abspath(self.path)),
                            "edge_profile_x")

    @property
    def bili_profile_dir(self) -> str:
        """Edge 持久化配置文件目录（保存 B 站登录态）"""
        return os.path.join(os.path.dirname(os.path.abspath(self.path)),
                            "edge_profile_bili")

    @property
    def xhs_profile_dir(self) -> str:
        """Edge 持久化配置文件目录（保存小红书登录态）"""
        return os.path.join(os.path.dirname(os.path.abspath(self.path)),
                            "edge_profile_xhs")

    @property
    def jm_profile_dir(self) -> str:
        """Edge 持久化配置文件目录（保存禁漫登录态）"""
        return os.path.join(os.path.dirname(os.path.abspath(self.path)),
                            "edge_profile_jm")

    def set(self, key, value):
        # 写入 + 落盘放在同一把锁里：多个开关快速切换时不会丢写
        with _lock:
            self._data[key] = value
        self.save()

    # 时间戳前缀格式：年月日_时分秒，如 20260918_221319
    DATE_TIME_FMT = "%Y%m%d_%H%M%S"

    def build_filename(self, info) -> str:
        """按命名规则生成文件名（不含扩展名）"""
        rule = self.get("naming_rule", "unique_id")
        title = info.safe_title()
        if rule == "timestamp":
            from datetime import datetime
            # 精确到秒，如 20260918_221319；重名由下载队列自动加 _1 后缀
            return datetime.now().strftime(self.DATE_TIME_FMT)
        import re
        from datetime import datetime
        author = re.sub(r'[\\/:*?"<>|\r\n]+', "_", info.author or "")[:30] or "unknown"
        # 其余规则统一在前面加「年月日_」，便于按下载日期排序与检索
        prefix = datetime.now().strftime("%Y%m%d_")
        if rule == "unique_id":
            # 唯一命名：作品ID + 标题 + 下载时间戳。ID 保证同一作品跨次解析
            # 落到同一前缀（增量更新靠它判重），末尾时间戳保证「重新下载同一
            # 作品」也得到新文件名，不会互相覆盖；标题保留可读性便于人工翻找。
            uid = re.sub(r'[\\/:*?"<>|\r\n]+', "_", info.unique_id or "")[:80]
            tail = uid if not title else f"{uid}_{title}"
            return f"{tail}_{datetime.now().strftime(self.DATE_TIME_FMT)}"[:150]
        if rule == "title":
            return f"{prefix}{title}"
        if rule == "id_title":
            return f"{prefix}{info.aweme_id}_{title}"
        return f"{prefix}{author}_{title}"
