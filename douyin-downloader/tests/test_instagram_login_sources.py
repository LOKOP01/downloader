"""回归：Instagram 解析的正确性护栏（不需要网络，全部是静态检查）。

背景（2026-10-14 用户报「reel 解析成图片、点赞 0」）：
根因是**IG 登录态整体失效**（`edge_profile_ins` 里 8 个 cookie 值全空、无
sessionid；config 的 ins_cookie 也被服务端 401 并清除）。于是年龄限制帖
只返回登录墙，页面里没有 video_versions，最后被 og:image 兜底 =>
「图集（1 张图）」+ 点赞 0 —— 一个**误导性的成功**，而不是报错。

当时的排查还试过「往持久化上下文里注入 config 的 cookie」，实测**有害**：
注入后 IG 把页面降级成「登录墙推广页」，连公开帖也拿不到数据（已回滚）。
所以下面的测试锁死的是最终形态：

  1. reel 链接不允许被 HTTP 兜底判成图集
  2. node 为空（只有封面）时必须报错说明需要登录，而不是静默返回图集
  3. 不允许往 persistent context 注入 cookie（会劣化页面）
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SITES = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app", "core", "sites.py")


def _src():
    with open(_SITES, encoding="utf-8") as fh:
        return fh.read()


def _func(name):
    tree = ast.parse(_src())
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"找不到函数 {name}")


class CookieInjectionGuardTest(unittest.TestCase):
    """往 persistent context 注入 cookie 会把 IG 页面降级，必须禁止"""

    def test_no_add_cookies_inside_profile_branch(self):
        fn = _func("_instagram_browser")
        target = None
        for stmt in ast.walk(fn):
            if (isinstance(stmt, ast.If)
                    and isinstance(stmt.test, ast.Name)
                    and stmt.test.id == "profile_dir"):
                target = stmt
                break
        self.assertIsNotNone(target, "找不到 `if profile_dir:` 分支")

        # 只看 `if` 的 body（profile 分支本身）；else 分支里注入是允许的
        calls = []
        for stmt in target.body:
            calls += [n for n in ast.walk(stmt)
                      if isinstance(n, ast.Call)
                      and getattr(n.func, "attr", "") == "add_cookies"]
        self.assertEqual(
            calls, [],
            "profile 分支里出现了 add_cookies —— 实测注入后 IG 会把页面降级成"
            "登录墙推广页，连公开帖都解析不了（2026-10-14 已回滚过一次）")


class ReelNeverBecomesImageTest(unittest.TestCase):
    """/reel/ 链接不允许被判成图集"""

    def test_http_fallback_guards_reel(self):
        fn = _func("_instagram_http")
        self.assertIn("is_reel", [a.arg for a in fn.args.args],
                      "_instagram_http 应接收 is_reel")

        guard = None
        for stmt in ast.walk(fn):
            if (isinstance(stmt, ast.If)
                    and isinstance(stmt.test, ast.Name)
                    and stmt.test.id == "is_reel"):
                guard = stmt
                break
        self.assertIsNotNone(guard, "缺少 `if is_reel:` 保护")
        self.assertTrue([n for n in ast.walk(guard) if isinstance(n, ast.Raise)],
                        "is_reel 分支里应当抛 ParseError 而不是判成图集")

    def test_parse_instagram_computes_is_reel(self):
        fn = _func("parse_instagram")
        assigned = {t.id for n in ast.walk(fn)
                    if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name)}
        self.assertIn("is_reel", assigned,
                      "parse_instagram 应算出 is_reel 并往下传")

    def test_browser_rejects_empty_node(self):
        """node 为空（只拿到封面）时必须报错，不能静默当图集"""
        fn = _func("_instagram_browser")

        guard = None
        for stmt in fn.body:
            if (isinstance(stmt, ast.If)
                    and isinstance(stmt.test, ast.UnaryOp)
                    and isinstance(stmt.test.op, ast.Not)
                    and isinstance(stmt.test.operand, ast.Name)
                    and stmt.test.operand.id == "node"):
                guard = stmt
                break
        self.assertIsNotNone(guard, "缺少 `if not node:` 的早退保护")
        raises = [n for n in ast.walk(guard) if isinstance(n, ast.Raise)]
        self.assertTrue(raises, "node 为空时应抛 ParseError 说明需要登录态")


class LoginCriteriaTest(unittest.TestCase):
    """登录判据必须是「真正会随登出消失」的会话凭证。

    2026-10-14 实测：`login_instagram` 的判据曾是 ("sessionid", "ds_user_id")，
    而 `_has_cookie` 是 any() 语义 —— `ds_user_id`（纯数字 uid）在登录**过期后
    仍留在 profile 里**，于是 `login_site` 打开窗口的第一轮循环就判成「登录成功」
    并立刻关窗。用户看到的是「登录窗口跳一下就消失，根本没法登录」。
    实测窗口只活了 1ms。
    """

    def test_instagram_criteria_is_sessionid_only(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "app", "core",
                "browser_bridge.py"), encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "login_instagram"), None)
        self.assertIsNotNone(fn, "找不到 login_instagram")

        # 找到 return login_site(...) 里的第二个位置参数（cookie_names 元组）
        ret = next((n for n in ast.walk(fn) if isinstance(n, ast.Return)), None)
        self.assertIsNotNone(ret, "login_instagram 里没有 return")
        call = ret.value
        self.assertIsInstance(call, ast.Call)
        tupled = [a for a in call.args if isinstance(a, ast.Tuple)]
        self.assertTrue(tupled, "login_site 的 cookie_names 应是字面元组")
        keys = [e.value for e in tupled[0].elts if isinstance(e, ast.Constant)]
        self.assertEqual(
            keys, ["sessionid"],
            "IG 登录判据必须只认 sessionid —— 带上 ds_user_id 会让登录窗口"
            "一闪即关（登录过期后该字段仍在，any() 会立刻判成功）")

    def test_has_cookie_is_any_semantics(self):
        """钉住 _has_cookie 的语义，避免有人改成「全部命中」时忘了上面的前提"""
        with open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "app", "core",
                "browser_bridge.py"), encoding="utf-8") as fh:
            src = fh.read()
        fn_src = src[src.index("def _has_cookie"):]
        fn_src = fn_src[:fn_src.index("\ndef ", 1)]
        self.assertIn("any(", fn_src,
                      "_has_cookie 若是 all() 语义，判据的含义会变，需同步改文档")


class AccountRestrictedHintTest(unittest.TestCase):
    """账号被 IG 限流时要说清原因，不能误导用户去重新登录。

    2026-10-14 实测：用户完成登录（sessionid 长度 78 有效）后，IG 因安全策略
    把所有页面 302 到 /accounts/suspended/（页面要求输入短信验证码），
    连公开帖也一样。此时登录态没问题、重新登录也没用，但旧提示是
    「页面未能加载内容…请重新登录」，把人带偏。
    """

    def test_suspended_redirect_is_detected(self):
        fn = _func("_instagram_browser")
        # 函数里必须出现 "accounts/suspended" 这个判据字符串
        has_const = any(isinstance(c, ast.Constant)
                        and isinstance(c.value, str)
                        and "accounts/suspended" in c.value
                        for c in ast.walk(fn))
        self.assertTrue(has_const,
                        "_instagram_browser 里应检测 page.url 是否含 accounts/suspended")

    def test_hint_says_relogin_is_useless(self):
        """提示必须点明「不是登录失效、重新登录无效」，否则用户会白折腾"""
        with open(_SITES, encoding="utf-8") as fh:
            src = fh.read()
        seg = src[src.index("accounts/suspended"):]
        seg = seg[:seg.index("raise ParseError")]
        end = src.index("raise ParseError", src.index("accounts/suspended"))
        msg = src[end:end + 400]
        self.assertIn("不是登录失效", msg,
                      "提示里要写明这不是登录失效")
        self.assertIn("短信验证码", msg,
                      "提示里要点出页面会要求输入短信验证码")


if __name__ == "__main__":
    unittest.main()
