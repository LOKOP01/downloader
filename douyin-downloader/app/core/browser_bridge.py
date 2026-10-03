# -*- coding: utf-8 -*-
"""浏览器桥：驱动本机 Edge（Playwright, channel=msedge）访问抖音

2026 年起抖音 web API 被 ArgusSecurityPlugin 门禁，直接 HTTP 请求一律 403，
只能由真实浏览器页面发起。未登录时用户主页只返回第一页（约 18 个作品），
因此本模块使用持久化 Edge 配置文件保存扫码登录态，登录后可抓全部作品。
"""
import time
from typing import Callable, Dict, List, Optional, Tuple

PC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

POST_API_KEY = "/aweme/v1/web/aweme/post/"
PROFILE_API_KEY = "/aweme/v1/web/user/profile/"
DETAIL_API_KEY = "/aweme/v1/web/aweme/detail/"
# 注意：passport_csrf_token 游客访问也会种下，不能作为登录判据
LOGIN_COOKIE_KEYS = ("sessionid", "sessionid_ss")


class BrowserBridgeError(Exception):
    pass


STEALTH_JS = ("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
               "window.chrome = window.chrome || { runtime: {} };")

EDGE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")


def _launch(pw, profile_dir: Optional[str], headless: bool):
    """启动浏览器。有 profile_dir 时用持久化上下文（保留登录态）"""
    args = ["--disable-blink-features=AutomationControlled",
            "--disable-infobars", "--no-first-run"]
    if profile_dir:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir, channel="msedge", headless=headless,
            args=args, user_agent=EDGE_UA,
            viewport={"width": 1280, "height": 900}, locale="zh-CN")
        context.add_init_script(STEALTH_JS)
        return context, None
    browser = pw.chromium.launch(channel="msedge", headless=headless, args=args)
    context = browser.new_context(user_agent=EDGE_UA,
                                  viewport={"width": 1280, "height": 900},
                                  locale="zh-CN")
    context.add_init_script(STEALTH_JS)
    return context, browser


def launch_clean(pw, profile_dir: Optional[str], headless: bool = False):
    """裸启动（不覆盖 UA、不注入 stealth 脚本）。

    小红书会识别 `user_agent` 覆盖与 `navigator.webdriver` 篡改等
    自动化特征，套用抖音那套 stealth 参数会直接被跳到登录页，
    因此该平台必须用原生指纹启动。
    """
    args = ["--disable-blink-features=AutomationControlled",
            "--disable-infobars", "--no-first-run"]
    if profile_dir:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir, channel="msedge", headless=headless,
            args=args, viewport={"width": 1366, "height": 900})
        return context, None
    browser = pw.chromium.launch(channel="msedge", headless=headless, args=args)
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    return context, browser


def _is_logged_in(context) -> bool:
    try:
        names = {c.get("name") for c in context.cookies("https://www.douyin.com")}
        return any(k in names for k in LOGIN_COOKIE_KEYS)
    except Exception:  # noqa: BLE001
        return False


def login(profile_dir: str, timeout: int = 240) -> Tuple[bool, str]:
    """打开有头 Edge 供用户扫码/账密登录抖音，登录态写入 profile_dir。"""
    return login_site(profile_dir, "https://www.douyin.com/",
                      LOGIN_COOKIE_KEYS, timeout)


def login_instagram(profile_dir: str, timeout: int = 300) -> Tuple[bool, str]:
    """打开有头 Edge 供用户登录 Instagram，登录态写入 profile_dir。"""
    return login_site(profile_dir, "https://www.instagram.com/accounts/login/",
                      ("sessionid", "ds_user_id"), timeout)


def login_x(profile_dir: str, timeout: int = 300) -> Tuple[bool, str]:
    """打开有头 Edge 供用户登录 X(Twitter)，登录态写入 profile_dir。"""
    return login_site(profile_dir, "https://x.com/i/flow/login",
                      ("auth_token",), timeout)


def login_bilibili(profile_dir: str, timeout: int = 300) -> Tuple[bool, str]:
    """打开有头 Edge 供用户登录 B 站，登录态写入 profile_dir。"""
    return login_site(profile_dir, "https://passport.bilibili.com/login",
                      ("SESSDATA",), timeout)


def login_xiaohongshu(profile_dir: str, timeout: int = 300) -> Tuple[bool, str]:
    """打开有头 Edge 供用户扫码登录小红书，登录态写入 profile_dir。

    小红书解析本身免登录可用（SSR 内联数据），登录后能拿到更稳的
    xsec_token、以及部分仅登录可见的笔记。

    判据只认 `web_session`：`a1` / `webId` 是游客态就会种下的设备 Cookie，
    拿它们当「已登录」会让登录窗口刚打开就立刻报成功（用户还没扫码）。
    """
    return login_site(profile_dir, "https://www.xiaohongshu.com/explore",
                      ("web_session",), timeout)



def login_jmcomic(profile_dir: str, timeout: int = 300) -> Tuple[bool, str]:
    """打开有头 Edge 供用户登录禁漫天堂，登录态写入 profile_dir。"""
    return login_site(profile_dir, "https://18comic.vip/login",
                      ("AVS",), timeout)


def dump_cookies(profile_dir: str, domain_key: str) -> str:
    """从持久化 profile 中导出指定域名的 Cookie 字符串（供 requests 复用登录态）"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    with sync_playwright() as pw:
        try:
            context, browser = _launch(pw, profile_dir, headless=True)
        except Exception:  # noqa: BLE001
            return ""
        try:
            pairs = []
            for c in context.cookies():
                if domain_key in (c.get("domain") or ""):
                    pairs.append(f"{c.get('name')}={c.get('value')}")
        finally:
            context.close()
            if browser:
                browser.close()
    return "; ".join(pairs)


def login_site(profile_dir: str, url: str,
               cookie_names: Tuple[str, ...], timeout: int) -> Tuple[bool, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise BrowserBridgeError(f"Playwright 未安装：{e}")

    with sync_playwright() as pw:
        try:
            context, _ = _launch(pw, profile_dir, headless=False)
        except Exception as e:  # noqa: BLE001
            raise BrowserBridgeError(f"无法启动本机 Edge 浏览器：{e}")
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            deadline = time.time() + timeout
            while time.time() < deadline:
                if _has_cookie(context, cookie_names):
                    return True, "登录成功，已保存登录状态"
                try:
                    page.wait_for_timeout(2000)
                except Exception:  # noqa: BLE001
                    pass
                if page.is_closed():
                    pages = [p for p in context.pages if not p.is_closed()]
                    if not pages:
                        return False, "登录窗口已被关闭，未完成登录"
                    page = pages[0]
            return False, "等待登录超时，未检测到登录状态"
        finally:
            context.close()


def _has_cookie(context, names) -> bool:
    try:
        got = {c.get("name") for c in context.cookies()}
        return any(n in got for n in names)
    except Exception:  # noqa: BLE001
        return False


def fetch_aweme_detail(aweme_id: str,
                       profile_dir: Optional[str] = None) -> Optional[dict]:
    """用真实浏览器打开作品页，拦截详情接口 JSON，拿到完整 bit_rate（含 1080p+）

    返回 aweme_detail 字典；失败返回 None（调用方保留原有解析结果）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    detail = {}

    with sync_playwright() as pw:
        try:
            context, browser = _launch(pw, profile_dir, headless=True)
        except Exception:  # noqa: BLE001 - 浏览器不可用时静默跳过
            return None
        try:
            page = context.new_page()

            def on_response(resp):
                if DETAIL_API_KEY not in resp.url or detail:
                    return
                try:
                    data = resp.json() or {}
                except Exception:  # noqa: BLE001
                    return
                item = data.get("aweme_detail") or {}
                if item:
                    detail.update(item)

            page.on("response", on_response)
            try:
                page.goto(f"https://www.douyin.com/video/{aweme_id}",
                          wait_until="domcontentloaded", timeout=40000)
            except Exception:  # noqa: BLE001
                return None
            # 详情接口实测 goto 后约 1s 就回（一次响应即含完整 bit_rate），
            # 因此轮询「拿到数据立刻走」，不要固定死等 4s（原先每次白等 3s）
            waited = 0
            while not detail and waited < 6000:
                try:
                    page.wait_for_timeout(100)
                except Exception:  # noqa: BLE001
                    break
                waited += 100
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
        finally:
            context.close()
            if browser:
                browser.close()

    return detail or None


def fetch_user_posts(sec_uid: str, cookie: str = "",
                     profile_dir: Optional[str] = None,
                     max_scrolls: int = 300,
                     progress_cb: Optional[Callable[[int, int], None]] = None
                     ) -> Tuple[List[dict], str, bool, int]:
    """抓取用户主页公开作品。

    progress_cb(已抓取数, 作者作品总数[0 为未知])。
    返回 (原始 aweme 字典列表, 用户昵称, 是否疑似被登录墙限制, 作者作品总数)。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise BrowserBridgeError(f"Playwright 未安装：{e}")

    items: Dict[str, dict] = {}
    nickname = ""
    expected = 0     # 作者作品总数（来自用户信息接口），用于判断是否抓全
    state = {"has_more": True, "done": False, "blocked": False}

    with sync_playwright() as pw:
        try:
            context, browser = _launch(pw, profile_dir, headless=True)
        except Exception as e:  # noqa: BLE001
            raise BrowserBridgeError(
                f"无法启动本机 Edge 浏览器（批量抓取依赖它）：{e}")
        try:
            page = context.new_page()

            def on_response(resp):
                nonlocal nickname, expected
                try:
                    if PROFILE_API_KEY in resp.url:
                        data = resp.json()
                        user = (data or {}).get("user") or {}
                        # 必须确认是目标作者本人的资料，防止误采推荐用户的 aweme_count
                        if user.get("sec_uid") == sec_uid:
                            expected = int(user.get("aweme_count") or 0) or expected
                            if not nickname:
                                nickname = user.get("nickname") or ""
                        return
                    if POST_API_KEY not in resp.url:
                        return
                    if resp.status == 403:
                        # 翻页过快触发限流，记录后由主循环退避等待
                        state["blocked"] = True
                        return
                    data = resp.json()
                except Exception:  # noqa: BLE001
                    return
                state["blocked"] = False
                aweme_list = data.get("aweme_list") or []
                for it in aweme_list:
                    aid = str(it.get("aweme_id", ""))
                    if aid:
                        items[aid] = it
                        if not nickname:
                            nickname = ((it.get("author") or {}).get("nickname")) or ""
                state["has_more"] = bool(data.get("has_more"))
                if data.get("has_more") in (0, False):
                    state["done"] = True

            page.on("response", on_response)

            try:
                page.goto(f"https://www.douyin.com/user/{sec_uid}",
                          wait_until="domcontentloaded", timeout=45000)
            except Exception as e:  # noqa: BLE001
                raise BrowserBridgeError(f"用户主页打开失败：{e}")

            page.wait_for_timeout(3500)
            last_count, no_progress, blocked_streak = 0, 0, 0
            for _ in range(max_scrolls):
                if state["done"]:
                    break
                if expected and len(items) >= expected:
                    break
                try:
                    page.keyboard.press("Escape")
                    # 鼠标移到列表区域中央再滚动，确保事件落在作品网格上
                    page.mouse.move(640, 500)
                    page.mouse.wheel(0, 1400)
                except Exception:  # noqa: BLE001
                    break
                page.wait_for_timeout(2200)
                if progress_cb:
                    progress_cb(len(items), expected)

                if len(items) != last_count:
                    no_progress = 0
                    blocked_streak = 0
                    last_count = len(items)
                    continue

                # ---- 无进展处理 ----
                if state["blocked"]:
                    # 被限流：递增退避（最长 20s），再轻推滚动重试
                    blocked_streak += 1
                    if blocked_streak > 6:
                        break
                    page.wait_for_timeout(min(20000, 5000 * blocked_streak))
                    state["blocked"] = False
                    try:
                        page.mouse.move(640, 500)
                        page.mouse.wheel(0, -1200)
                        page.wait_for_timeout(800)
                        page.mouse.wheel(0, 1400)
                    except Exception:  # noqa: BLE001
                        break
                    continue

                no_progress += 1
                # 连续无进展时回滚一段再下滚，重新触发懒加载
                if no_progress % 3 == 0:
                    try:
                        page.mouse.move(640, 500)
                        page.mouse.wheel(0, -3200)
                        page.wait_for_timeout(800)
                    except Exception:  # noqa: BLE001
                        pass
                if no_progress >= 9:
                    break

            logged_in = _is_logged_in(context)
        finally:
            context.close()
            if browser:
                browser.close()

    if not items:
        raise BrowserBridgeError(
            "未抓取到作品。该账号可能为私密账号或触发风控验证，"
            "请先点击「登录抖音」扫码登录后重试")
    # 未登录且没抓到"已无更多"标记 → 大概率被登录墙截断在第一页
    limited = (not logged_in) and (not state["done"])
    return list(items.values()), nickname, limited, expected
