# -*- coding: utf-8 -*-
"""下载路径处理回归测试（PermissionError 排查配套）

覆盖：
  - 用户选新目录后，任务实际使用新目录（retry 重挂）
  - 中文/空格路径
  - 不同盘符与不同分隔符（含混合分隔符）
  - 配置保存后重新加载
  - 不存在的目标目录按设计创建
  - 无权限/被占位目录返回清晰错误
  - 临时文件路径与最终文件路径一致
  - 两个任务输出同名文件的冲突处理
  - 下载完成后的命名与临时文件清理

⚠️ 本测试**不调用 DownloadManager.add()**：add() 会立刻 _pump() 起真实下载线程
（实测会去打 example.invalid 并返回 502），那就成了真实网络请求。
这里改为直接登记任务记录，只验证路径逻辑本身。

全部使用临时目录，不访问真实个人文件、不做真实网络下载。
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Config, default_download_path  # noqa: E402
from app.core.downloader import (  # noqa: E402
    DownloadManager,
    ensure_temp_writable,
    prepare_target_path,
)
from app.core.models import DownloadTaskInfo  # noqa: E402


def register(manager, url, save_path, name):
    """把任务登记进 manager 但**不触发下载**（等价于 add() 去掉 _pump）。"""
    path = manager._unique_path(save_path)
    info = DownloadTaskInfo(task_id="t" + str(len(manager._tasks) + 1),
                            url=url, save_path=path, name=name)
    with manager._lock:
        manager._tasks[info.task_id] = info
        manager._cookies[info.task_id] = ""
        manager._plans[info.task_id] = [(url, "", [])]
    return info


class FakeManager(DownloadManager):
    """禁用自动入队执行，避免测试期间发起真实请求"""

    def _pump(self):
        pass

    def _ensure_watchdog(self):
        pass


class PrepareTargetPathTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vdl_path_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_normalizes_mixed_separators(self):
        mixed = self.root.replace("\\", "/") + "\\out.mp4"
        got = prepare_target_path(mixed)
        self.assertEqual(os.path.abspath(os.path.normpath(mixed)), got)

    def test_creates_missing_parent_directory(self):
        target = os.path.join(self.root, "a", "b", "c", "video.mp4")
        self.assertFalse(os.path.isdir(os.path.dirname(target)))
        got = prepare_target_path(target)
        self.assertEqual(os.path.abspath(target), got)
        self.assertTrue(os.path.isdir(os.path.dirname(target)))

    def test_supports_chinese_and_spaces(self):
        target = os.path.join(self.root, "我的 视频 目录", "作品 01.mp4")
        got = prepare_target_path(target)
        self.assertTrue(os.path.isdir(os.path.dirname(got)))
        with open(got, "wb") as f:
            f.write(b"x")
        self.assertTrue(os.path.exists(got))

    def test_directory_at_target_gives_actionable_error(self):
        """关键回归：目标位置已有同名目录 → 必须报「被文件夹占用」而非裸 Errno 13"""
        target = os.path.join(self.root, "occupied.mp4")
        os.makedirs(target)
        with self.assertRaises(IsADirectoryError) as ctx:
            prepare_target_path(target)
        self.assertIn("文件夹占用", str(ctx.exception))
        self.assertIn(target, str(ctx.exception))

    def test_part_occupied_by_directory_is_caught_before_download(self):
        """用户报错的等价场景：最终文件没问题，但 .part 位置是个目录。

        只检查 save_path 会漏掉这种情况，要等 open(.part) 才抛出 Errno 13。
        """
        target = os.path.join(self.root, "video.mp4")
        os.makedirs(target + ".part")
        with self.assertRaises(IsADirectoryError) as ctx:
            prepare_target_path(target)
        self.assertIn("文件夹占用", str(ctx.exception))
        self.assertIn(".part", str(ctx.exception))

    def test_vpart_occupied_by_directory_is_caught(self):
        """DASH 任务的 .vpart / .apart 也是落点，同样要查"""
        target = os.path.join(self.root, "dash.mp4")
        os.makedirs(target + ".vpart")
        with self.assertRaises(IsADirectoryError):
            prepare_target_path(target)

    def test_clean_directory_passes_all_suffix_checks(self):
        """正常目录不能被误伤（没有任何 .part 占位时应当通过）"""
        target = os.path.join(self.root, "ok.mp4")
        self.assertEqual(os.path.abspath(target), prepare_target_path(target))

    def test_relative_path_becomes_absolute(self):
        self.assertTrue(os.path.isabs(prepare_target_path("rel_out.mp4")))

    def test_nonexistent_drive_reports_clearly(self):
        if os.name != "nt":
            self.skipTest("仅 Windows 验证盘符场景")
        with self.assertRaises((PermissionError, OSError)) as ctx:
            prepare_target_path("Q:/definitely_missing_drive/out.mp4")
        # 错误信息必须包含「无法创建下载目录」这类可操作说明，而不是裸 WinError
        self.assertIn("无法创建下载目录", str(ctx.exception))


class EnsureTempWritableTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vdl_tmp_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_ok_for_writable_path(self):
        p = os.path.join(self.root, "x.mp4.part")
        ensure_temp_writable(p)
        self.assertTrue(os.path.exists(p))

    def test_raises_clear_error_when_directory_occupies_path(self):
        p = os.path.join(self.root, "y.mp4.part")
        os.makedirs(p)
        with self.assertRaises(IsADirectoryError) as ctx:
            ensure_temp_writable(p)
        self.assertIn("文件夹占用", str(ctx.exception))


class SavePathConsistencyTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vdl_cons_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_temp_is_sibling_of_final(self):
        final = os.path.join(self.root, "sub", "作品.mp4")
        prepare_target_path(final)
        part = final + ".part"
        self.assertEqual(os.path.dirname(part), os.path.dirname(final))
        self.assertEqual("作品.mp4.part", os.path.basename(part))


class ConfigPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vdl_cfg_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_saved_download_path_survives_reload(self):
        cfg_path = os.path.join(self.root, "config.json")
        target = os.path.join(self.root, "我的 下载")
        Config(cfg_path).set("download_path", target)
        self.assertEqual(target, Config(cfg_path).get("download_path"))

    def test_default_path_used_when_empty(self):
        cfg = Config(os.path.join(self.root, "config.json"))
        self.assertEqual(default_download_path(), cfg.get("download_path"))

    def test_config_file_written_at_given_location(self):
        cfg_path = os.path.join(self.root, "config.json")
        Config(cfg_path).set("download_path", os.path.join(self.root, "d"))
        self.assertTrue(os.path.exists(cfg_path))

    def test_path_with_trailing_separator_reloads_unchanged(self):
        cfg_path = os.path.join(self.root, "config.json")
        target = self.root + os.sep
        Config(cfg_path).set("download_path", target)
        self.assertEqual(target, Config(cfg_path).get("download_path"))


class RetryRebaseTest(unittest.TestCase):
    """改目录后重试必须落到新目录"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vdl_retry_")
        self.old_dir = os.path.join(self.root, "old")
        self.new_dir = os.path.join(self.root, "new")
        os.makedirs(self.old_dir)
        os.makedirs(self.new_dir)
        self.mgr = FakeManager(max_concurrent=1, segment_enabled=False)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_retry_rebases_to_current_directory(self):
        info = register(self.mgr, "http://example.invalid/v.mp4",
                        os.path.join(self.old_dir, "a.mp4"), "a.mp4")
        info.status = "失败"
        self.mgr.retry(info.task_id, rebase_to=self.new_dir)
        self.assertEqual(self.new_dir, os.path.dirname(info.save_path))
        self.assertEqual("a.mp4", os.path.basename(info.save_path))

    def test_retry_without_rebase_keeps_original_path(self):
        info = register(self.mgr, "http://example.invalid/v.mp4",
                        os.path.join(self.old_dir, "b.mp4"), "b.mp4")
        info.status = "失败"
        self.mgr.retry(info.task_id)
        self.assertEqual(self.old_dir, os.path.dirname(info.save_path))

    def test_retry_same_directory_keeps_path_untouched(self):
        path = os.path.join(self.old_dir, "c.mp4")
        info = register(self.mgr, "http://example.invalid/v.mp4", path, "c.mp4")
        info.status = "失败"
        self.mgr.retry(info.task_id, rebase_to=self.old_dir)
        self.assertEqual(path, info.save_path)

    def test_batch_retry_rebases_all_failed(self):
        t1 = register(self.mgr, "http://example.invalid/1.mp4",
                      os.path.join(self.old_dir, "1.mp4"), "1.mp4")
        t2 = register(self.mgr, "http://example.invalid/2.mp4",
                      os.path.join(self.old_dir, "2.mp4"), "2.mp4")
        t1.status = "失败"
        t2.status = "失败"
        self.assertEqual(2, self.mgr.retry_tasks(rebase_to=self.new_dir))
        for t in (t1, t2):
            self.assertEqual(self.new_dir, os.path.dirname(t.save_path))


class UniquePathConflictTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vdl_uniq_")
        self.mgr = FakeManager(max_concurrent=2, segment_enabled=False)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_second_task_gets_suffix(self):
        p = os.path.join(self.root, "same.mp4")
        a = register(self.mgr, "http://example.invalid/a", p, "same.mp4")
        b = register(self.mgr, "http://example.invalid/b", p, "same.mp4")
        self.assertNotEqual(a.save_path, b.save_path)
        self.assertTrue(b.save_path.endswith("_1.mp4"))
        self.assertEqual(os.path.dirname(p), os.path.dirname(b.save_path))

    def test_existing_file_on_disk_is_not_reused(self):
        p = os.path.join(self.root, "exists.mp4")
        with open(p, "wb") as f:
            f.write(b"old")
        info = register(self.mgr, "http://example.invalid/x", p, "exists.mp4")
        self.assertNotEqual(p, info.save_path)
        self.assertTrue(info.save_path.endswith("_1.mp4"))

    def test_unique_path_keeps_directory_and_extension(self):
        p = os.path.join(self.root, "深 目录", "作 品.mp4")
        prepare_target_path(p)
        register(self.mgr, "http://example.invalid/a", p, "x")
        second = register(self.mgr, "http://example.invalid/b", p, "x")
        self.assertEqual(os.path.dirname(p), os.path.dirname(second.save_path))
        self.assertTrue(second.save_path.endswith(".mp4"))


class TempFileCleanupTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vdl_clean_")
        self.mgr = FakeManager(max_concurrent=1, segment_enabled=False)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_cleanup_removes_all_temp_suffixes(self):
        final = os.path.join(self.root, "z.mp4")
        for suffix in DownloadManager.TEMP_SUFFIXES:
            with open(final + suffix, "wb") as f:
                f.write(b"x")
        self.assertEqual(len(DownloadManager.TEMP_SUFFIXES),
                         self.mgr._cleanup_temp_files(final))
        for suffix in DownloadManager.TEMP_SUFFIXES:
            self.assertFalse(os.path.exists(final + suffix))

    def test_cleanup_keeps_final_file(self):
        final = os.path.join(self.root, "keep.mp4")
        with open(final, "wb") as f:
            f.write(b"done")
        with open(final + ".part", "wb") as f:
            f.write(b"partial")
        self.mgr._cleanup_temp_files(final)
        self.assertTrue(os.path.exists(final))

    def test_cleanup_ignores_missing_files(self):
        self.assertEqual(0, self.mgr._cleanup_temp_files(
            os.path.join(self.root, "nothing.mp4")))


class SegmentFallbackPathTest(unittest.TestCase):
    """分段失败后回退单连接：两条路径必须指向同一个临时文件"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vdl_seg_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_segment_and_single_use_same_temp_path(self):
        save = os.path.join(self.root, "v.mp4")
        prepare_target_path(save)
        part = save + ".part"
        # 分段写 part、单连接也写 part；降级前 _discard_partial 清的就是它
        ensure_temp_writable(part)
        self.assertTrue(os.path.exists(part))
        DownloadManager._discard_partial(part)
        self.assertFalse(os.path.exists(part))

    def test_discard_partial_removes_meta_too(self):
        save = os.path.join(self.root, "m.mp4")
        part = save + ".part"
        with open(part, "wb") as f:
            f.write(b"x")
        with open(part + ".meta", "w", encoding="utf-8") as f:
            f.write("0")
        DownloadManager._discard_partial(part)
        self.assertFalse(os.path.exists(part))
        self.assertFalse(os.path.exists(part + ".meta"))

    def test_discard_partial_survives_missing_paths(self):
        DownloadManager._discard_partial(os.path.join(self.root, "nope.part"))


if __name__ == "__main__":
    unittest.main()
