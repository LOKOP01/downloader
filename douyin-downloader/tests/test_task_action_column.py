"""回归：任务表的「操作」列必须永远可见。

背景（2026-10-14 用户报「看不见取消按键」）：
固定列宽合计 1150px，而主窗口默认只有 1100px（`main_window.py` 的
`self.resize(1100, 720)`）。操作列原本放在最右 → 被推到视口外
（实测取消按钮右边界 1038px、表格视口仅 983px），**默认就看不见**，
要手动把窗口拖宽到 1200px 以上才会出现。用户因此认为「无法中途取消」。

修复：把操作列移到最左侧（第 0 列）。这样无论窗口多窄、横向怎么滚动，
取消/重试/打开/删除都始终可见。

这个测试不需要显示窗口，用离屏 Qt 量几何即可。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from app.view.task_interface import (COL_ACTION, COL_NAME, COL_QUALITY, _COL_WIDTHS)


class ActionColumnLayoutTest(unittest.TestCase):
    """静态：操作列必须在最左，且列宽预算不能超过默认窗口"""

    def test_action_column_is_first(self):
        self.assertEqual(
            COL_ACTION, 0,
            "「操作」列必须在第 0 列（最左）—— 放最右会被挤出默认窗口的视口，"
            "用户看不见取消按钮（2026-10-14 事故）")

    def test_name_column_is_second(self):
        self.assertEqual(COL_NAME, 1, "「文件名」列应紧跟操作列")

    def test_fixed_widths_within_budget(self):
        """固定列宽合计要有上限，且超出的部分不得影响核心操作列。

        实测（`_scratch_x_probe/_quality_col_width.py`）：画质列最长文本
        `2160×3840 · 21000kbps · 27fps` 真实渲染宽度就是 **348px**，
        所以 360px 是必要的、不能再收（收了会变省略号、信息有损）。
        因此固定列合计 1050px 会略超默认窗口下的表格视口（约 1000px），
        超出的只是最右的「状态」列 —— 用户横向滚动即可看到，
        而「操作」列固定在**最左**，任何窗口宽度下都可见。

        这个测试守两条线：
          1. 合计不得继续膨胀（<= 1100px，防止有人随意加宽列）
          2. 操作列必须是第一列（真正的可见性保障，见上面的测试）
        """
        fixed_total = sum(_COL_WIDTHS.values())
        self.assertLessEqual(
            fixed_total, 1100,
            f"固定列宽合计 {fixed_total}px 过大（当前设计是 1050px）。"
            f"加宽任一列前请先量真实文本宽度，并确认操作列仍固定在最左")

    def test_action_column_fixed_not_stretch(self):
        """操作列必须是 Fixed：Stretch 会让按钮被压扁或拉伸。

        注意不能用 ResizeToContents（MEMORY 记载的禁忌：会让 Stretch 的
        文件名列随内容变化而抖动，整行水平平移）。
        """
        from PySide6.QtWidgets import QHeaderView
        import tempfile
        from app.core.config import Config
        from app.core.downloader import DownloadManager
        from app.view.task_interface import TaskInterface

        class Ctx:
            pass

        tmp = tempfile.mkdtemp(prefix="colfix_")
        ctx = Ctx()
        ctx.config = Config(os.path.join(tmp, "config.json"))
        ctx.config.set("download_path", tmp)
        ctx.manager = DownloadManager(max_concurrent=1)
        ui = TaskInterface(ctx)
        try:
            header = ui.table.horizontalHeader()
            mode = header.sectionResizeMode(COL_ACTION)
            self.assertEqual(
                mode, QHeaderView.Fixed,
                "操作列应为 Fixed 宽度，避免按钮被压扁/拉伸")
            self.assertEqual(
                header.sectionResizeMode(COL_NAME), QHeaderView.Stretch,
                "文件名列应保持 Stretch（吸收剩余宽度）")
        finally:
            ui.close()


class ActionColumnGeometryTest(unittest.TestCase):
    """动态：离屏实例化任务表，量取消按钮的几何，必须在视口内"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _build(self):
        import tempfile
        from app.core.config import Config
        from app.core.downloader import DownloadManager
        from app.view.task_interface import TaskInterface

        class Ctx:
            pass

        tmp = tempfile.mkdtemp(prefix="colreg_")
        ctx = Ctx()
        ctx.config = Config(os.path.join(tmp, "config.json"))
        ctx.config.set("download_path", tmp)
        ctx.manager = DownloadManager(max_concurrent=2)
        ui = TaskInterface(ctx)
        # 用主窗口的默认宽度
        ui.resize(1100, 600)
        ui.show()
        self.app.processEvents()
        return ui, ctx, tmp

    def test_cancel_button_visible_at_default_width(self):
        from qfluentwidgets import TransparentToolButton
        ui, ctx, tmp = self._build()
        try:
            task = ctx.manager.add("http://127.0.0.1:1/x.mp4",
                                   os.path.join(tmp, "x.mp4"), "x.mp4")
            self.app.processEvents()
            row = ui._rows.get(task.task_id)
            self.assertIsNotNone(row, "任务行未建立")

            tbl = ui.table
            vw = tbl.viewport().width()
            act_x = tbl.columnViewportPosition(COL_ACTION)
            holder = tbl.cellWidget(row, COL_ACTION)
            self.assertIsNotNone(holder, "操作列没有 cellWidget")

            cancel = None
            for b in holder.findChildren(TransparentToolButton):
                if b.toolTip() == "取消":
                    cancel = b
                    break
            self.assertIsNotNone(cancel, "操作列里找不到「取消」按钮")

            right = act_x + cancel.x() + cancel.width()
            self.assertLessEqual(
                right, vw,
                f"默认窗口宽度下取消按钮不可见：按钮右边界 {right}px > "
                f"表格视口 {vw}px —— 用户会以为「无法取消」")
            self.assertTrue(cancel.isVisible(), "取消按钮应可见")
            self.assertTrue(cancel.isEnabled(), "取消按钮应可点击")

            ctx.manager.remove_tasks([task.task_id])
        finally:
            ui.close()


if __name__ == "__main__":
    unittest.main()
