"""回归：逐张下载的站点（禁漫 / E-Hentai）走 DownloadManager 时，
`opts` 必须经参数传进 `_run_ehentai`，且任务表「大小」列要按张数显示。

背景（v1.8.10.27 引入、v1.8.10.28 仍在的真实事故）：
`_run()` 里的局部变量 `opts` 被 `_run_ehentai()` 直接引用。两者是不同方法，
`opts` 对后者不可见，模块级也没有 → 每次 E-Hentai 入队都抛
`NameError: name 'opts' is not defined`，而异常被 `_run_ehentai` 自己的
`except Exception` 吞掉，用户只看到任务「失败」+ 一行看不懂的错误。

这类 bug 的教训：`py_compile` 与 import 都查不出来（NameError 只在运行到那行
才发生），必须**真调一次 `add()` 并跑完 `_run_ehentai`**。
"""
import ast
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DOWNLOADER = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app", "core", "downloader.py")
_TASK_UI = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app", "view", "task_interface.py")


def _unbound_names(func_node, module_names):
    """返回函数里引用了、但既非局部绑定也非模块全局的名字"""
    bound = set()
    for n in ast.walk(func_node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
    # 嵌套函数会捕获外层局部量：把嵌套函数里绑定的名字也算进来（保守，宁少报）
    loaded = {n.id for n in ast.walk(func_node)
              if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    import builtins
    return loaded - bound - module_names - set(dir(builtins))


class EhentaiOptsScopeTest(unittest.TestCase):
    """静态：_run_ehentai 不能引用外层的局部名字（如 _run 里的 opts）"""

    def test_run_ehentai_has_no_unbound_names(self):
        with open(_DOWNLOADER, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        module_names = set()
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                module_names.add(n.name)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    module_names.add((a.asname or a.name).split(".")[0])
            else:
                for sub in ast.walk(n):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        module_names.add(sub.id)

        found = [n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_run_ehentai"]
        self.assertEqual(len(found), 1, "应恰好有一个 _run_ehentai")
        func = found[0]

        # 只看本函数自身作用域（嵌套的 on_progress 另算，它捕获外层是合法的）
        names = _unbound_names(func, module_names)
        # 嵌套函数（on_progress）里用到的外层名字要排除：先收集它们的绑定
        nested_bound = set()
        for sub in ast.walk(func):
            if isinstance(sub, ast.FunctionDef) and sub is not func:
                for n in ast.walk(sub):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                        nested_bound.add(n.id)
        names -= nested_bound

        self.assertNotIn(
            "opts", names,
            "_run_ehentai 引用了未绑定的 opts —— 必须由 _run() 显式传参"
            "（历史上这条让 E-Hentai 下载 100% 失败）")

    def test_run_passes_opts_to_run_ehentai(self):
        """_run 调用 _run_ehentai 时必须把 opts 传进去"""
        with open(_DOWNLOADER, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        calls = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "_run_ehentai":
                calls.append(n)
        self.assertTrue(calls, "没找到 _run_ehentai 的调用点")
        for call in calls:
            self.assertEqual(
                len(call.args), 4,
                "_run_ehentai 调用应传 4 个位置参数（info, cancel, cookies, opts）")


class EhentaiProgressIsPagesTest(unittest.TestCase):
    """E-Hentai 的进度语义是「张数」，UI 的大小列不能按字节格式化"""

    def test_size_column_treats_ehentai_as_pages(self):
        from app.core.models import DownloadTaskInfo
        from app.core.downloader import DownloadManager

        info = DownloadTaskInfo(task_id="t")
        info.url = "ehentai://e-hentai.org/1/aa"
        info.downloaded, info.total = 45, 91

        # 从 task_interface 抠出真实判断逻辑跑一遍
        with open(_TASK_UI, encoding="utf-8") as fh:
            src = fh.read()
        lines = src.splitlines()
        seg = None
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_updates":
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.If)
                            and isinstance(sub.test, ast.Call)
                            and getattr(sub.test.func, "attr", "") == "startswith"):
                        body = "\n".join(lines[sub.lineno - 1:sub.end_lineno])
                        if "张" in body:
                            seg = body
                            break
            if seg:
                break
        self.assertIsNotNone(seg, "没找到大小列的判断逻辑")

        import textwrap
        from app.core.models import fmt_size
        env = {"info": info, "fmt_size": fmt_size}
        exec(compile(textwrap.dedent(seg), "<task_interface>", "exec"), env)
        self.assertEqual(env.get("txt"), "45/91 张")

    def test_run_ehentai_signature_accepts_opts(self):
        """签名必须能收 opts（防止有人只改签名不改调用，或反之）"""
        from app.core.downloader import DownloadManager
        import inspect
        params = inspect.signature(
            DownloadManager._run_ehentai).parameters
        self.assertIn("opts", params)


if __name__ == "__main__":
    unittest.main()
