"""Regression checks for real folder order, durable progress, and migration."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core.manga import MangaLibrary, discover_books, natural_key, scan_pages


class MangaLibraryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / "漫画 [测试]"
        self.folder.mkdir()
        for name in ("10.jpg", "2.JPG", "1.jpeg", "notes.txt"):
            (self.folder / name).write_bytes(b"fixture")
        self.library = MangaLibrary(self.root / "state" / "manga_library.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_numeric_order_and_case_insensitive_extensions(self):
        self.assertEqual([Path(p).name for p in scan_pages(self.folder)], ["1.jpeg", "2.JPG", "10.jpg"])

    def test_chapters_sorted_by_relative_names(self):
        for chapter in ("第10章", "第2章"):
            target = self.folder / chapter
            target.mkdir()
            (target / "1.jpg").write_bytes(b"chapter")
        pages = scan_pages(self.folder)
        self.assertLess(next(i for i, p in enumerate(pages) if "第2章" in p),
                        next(i for i, p in enumerate(pages) if "第10章" in p))

    def test_big_numeric_names_and_mixed_prefixes(self):
        names = ["a10", "a2", "9" * 40, "a01", "a1", "10", "2"]
        self.assertEqual(sorted(names, key=natural_key), ["2", "10", "9" * 40, "a1", "a01", "a2", "a10"])

    def test_library_root_keeps_one_child_per_book(self):
        empty = self.root / "空文件夹"
        empty.mkdir()
        self.assertEqual(len(discover_books(self.root, True)), 1)
        self.assertEqual(len(discover_books(self.folder)[0][1]), 3)

    def test_repeat_import_preserves_progress_and_favorite(self):
        pages = scan_pages(self.folder)
        book = self.library.add(self.folder, pages)
        book.favorite = True
        book.mark_read(1, pages)
        self.assertIs(book, self.library.add(str(self.folder) + os.sep, pages))
        self.assertTrue(book.favorite)
        self.assertEqual(book.page_index, 1)
        self.assertEqual(len(self.library.books), 1)

    def test_filename_restore_survives_page_insertion(self):
        pages = scan_pages(self.folder)
        book = self.library.add(self.folder, pages)
        book.mark_read(1, pages)
        (self.folder / "0.jpg").write_bytes(b"new")
        self.assertEqual(book.restore_index(scan_pages(self.folder)), 2)

    def test_removed_page_clamps_to_available_index(self):
        pages = scan_pages(self.folder)
        book = self.library.add(self.folder, pages)
        book.mark_read(2, pages)
        (self.folder / "10.jpg").unlink()
        self.assertEqual(book.restore_index(scan_pages(self.folder)), 1)

    def test_saved_progress_preferences_and_unicode_roundtrip(self):
        pages = scan_pages(self.folder)
        book = self.library.add(self.folder, pages)
        book.mark_read(1, pages)
        book.favorite = True
        self.library.preferences.update({"rtl": True, "fit": "width", "paper": True})
        self.library.save()
        restored = MangaLibrary(self.library.path)
        self.assertEqual(restored.books[0].title, self.folder.name)
        self.assertEqual(restored.books[0].restore_index(pages), 1)
        self.assertTrue(restored.books[0].favorite)
        self.assertEqual(restored.preferences, {"rtl": True, "fit": "width", "paper": True})

    def test_invalid_primary_recovers_backup_without_destroying_original(self):
        self.library.add(self.folder, scan_pages(self.folder))
        self.library.save()
        self.library.save()
        Path(self.library.path).write_text("{broken", encoding="utf-8")
        recovered = MangaLibrary(self.library.path)
        self.assertEqual(len(recovered.books), 1)
        self.assertIn("备份恢复", recovered.warning)
        recovered.save()
        self.assertEqual(Path(self.library.path + ".corrupt").read_text(), "{broken")
        self.assertEqual(len(MangaLibrary(self.library.path).books), 1)

    def test_failed_replace_keeps_previous_library_and_cleans_temp(self):
        self.library.save()
        before = Path(self.library.path).read_bytes()
        self.library.add(self.folder, scan_pages(self.folder))
        with patch("app.core.manga.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.library.save()
        self.assertEqual(Path(self.library.path).read_bytes(), before)
        self.assertEqual(list(Path(self.library.path).parent.glob(".manga-*.tmp")), [])

    def test_missing_folder_reports_error_without_removing_record(self):
        self.library.add(self.folder, scan_pages(self.folder))
        with self.assertRaises(FileNotFoundError):
            scan_pages(self.root / "missing")
        self.assertEqual(len(self.library.books), 1)

    def test_standalone_migration_keeps_favorites_and_page_and_deduplicates(self):
        pages = scan_pages(self.folder)
        legacy = self.root / "library.json"
        legacy.write_text(json.dumps({"Books": [{"Folder": str(self.folder), "Title": "旧书架",
                          "PageIndex": 1, "PageCount": 3, "LastPage": pages[1],
                          "LastRead": "2026-10-14T12:00:00", "Favorite": True}]}), encoding="utf-8")
        self.assertEqual(self.library.import_yiejian(legacy), 1)
        self.assertEqual(self.library.import_yiejian(legacy), 0)
        self.assertTrue(self.library.books[0].favorite)
        self.assertEqual(self.library.books[0].restore_index(pages), 1)

    def test_invalid_migration_does_not_partially_import(self):
        legacy = self.root / "bad.json"
        legacy.write_text(json.dumps({"Books": [{"Folder": str(self.folder)}, {"Folder": 123}]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.library.import_yiejian(legacy)
        self.assertEqual(self.library.books, [])

    def test_missing_primary_restores_backup(self):
        self.library.add(self.folder, scan_pages(self.folder))
        self.library.save()
        self.library.save()
        Path(self.library.path).unlink()
        restored = MangaLibrary(self.library.path)
        self.assertEqual(len(restored.books), 1)
        self.assertIn("备份恢复", restored.warning)
        restored.save()
        self.assertEqual(len(MangaLibrary(self.library.path).books), 1)

    def test_infinite_page_numbers_recover_backup(self):
        self.library.add(self.folder, scan_pages(self.folder))
        self.library.save()
        self.library.save()
        for field in ("page_index", "page_count"):
            with self.subTest(field=field):
                Path(self.library.path).write_text(
                    '{"books":[{"folder":' + json.dumps(str(self.folder)) +
                    ',"' + field + '":1e400}]}', encoding="utf-8")
                restored = MangaLibrary(self.library.path)
                self.assertEqual(len(restored.books), 1)
                self.assertIn("备份恢复", restored.warning)
                restored.save()

    def test_infinite_migration_rejected_without_partial_import(self):
        legacy = self.root / "infinite.json"
        for field in ("PageIndex", "PageCount"):
            with self.subTest(field=field):
                legacy.write_text(json.dumps({"Books": [
                    {"Folder": str(self.folder)},
                    {"Folder": str(self.root / "other"), field: float("inf")}
                ]}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.library.import_yiejian(legacy)
                self.assertEqual(self.library.books, [])

    def test_backup_hardlink_does_not_overwrite_source(self):
        self.library.save()
        source = self.folder / "1.jpeg"
        before = source.read_bytes()
        backup = Path(self.library.path + ".bak")
        os.link(source, backup)
        # Load after preparing the destination so this is not a revision conflict.
        library = MangaLibrary(self.library.path)
        library.save()
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(os.path.samefile(source, backup))
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["books"], [])

    def test_backup_symlink_does_not_overwrite_source(self):
        self.library.save()
        source = self.folder / "1.jpeg"
        before = source.read_bytes()
        backup = Path(self.library.path + ".bak")
        try:
            backup.symlink_to(source)
        except OSError as error:
            self.skipTest(str(error))
        MangaLibrary(self.library.path).save()
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(backup.is_symlink())

    def test_corrupt_snapshot_hardlink_does_not_overwrite_source(self):
        self.library.save()
        Path(self.library.path).write_text("{broken", encoding="utf-8")
        source = self.folder / "1.jpeg"
        before = source.read_bytes()
        os.link(source, self.library.path + ".corrupt")
        MangaLibrary(self.library.path).save()
        self.assertEqual(source.read_bytes(), before)

    def test_failed_backup_replace_preserves_both_snapshots(self):
        self.library.save()
        self.library.save()
        primary = Path(self.library.path).read_bytes()
        backup = Path(self.library.path + ".bak").read_bytes()
        self.library.add(self.folder, scan_pages(self.folder))
        with patch("app.core.manga.os.replace", side_effect=OSError("backup disk error")):
            with self.assertRaises(OSError):
                self.library.save()
        self.assertEqual(Path(self.library.path).read_bytes(), primary)
        self.assertEqual(Path(self.library.path + ".bak").read_bytes(), backup)
        self.assertEqual(list(Path(self.library.path).parent.glob(".manga-*.tmp")), [])
        self.library.save()  # Failed saves release the lock and remain retryable.

    def test_primary_replace_failure_after_backup_is_retryable(self):
        self.library.save()
        primary = Path(self.library.path).read_bytes()
        self.library.add(self.folder, scan_pages(self.folder))
        replace = os.replace

        def fail_primary(source, destination):
            if destination == self.library.path:
                raise OSError("primary replace failed")
            return replace(source, destination)

        with patch("app.core.manga.os.replace", side_effect=fail_primary):
            with self.assertRaises(OSError):
                self.library.save()
        self.assertEqual(Path(self.library.path).read_bytes(), primary)
        self.assertEqual(Path(self.library.path + ".bak").read_bytes(), primary)
        self.assertEqual(list(Path(self.library.path).parent.glob(".manga-*.tmp")), [])
        self.library.save()
        self.assertEqual(len(MangaLibrary(self.library.path).books), 1)

    def test_stale_instance_save_reports_conflict(self):
        other = MangaLibrary(self.library.path)
        self.library.add(self.folder, scan_pages(self.folder))
        self.library.save()
        other.add(self.root / "other", [])
        with self.assertRaisesRegex(OSError, "其他实例修改"):
            other.save()
        restored = MangaLibrary(self.library.path)
        self.assertEqual([b.folder for b in restored.books], [str(self.folder)])
        self.assertEqual(len(other.books), 1)  # Unsaved local state is retained.

    def test_initial_load_lock_conflict_warns_without_enabling_save(self):
        from PySide6.QtCore import QLockFile
        self.library.add(self.folder, scan_pages(self.folder))
        self.library.save()
        before = Path(self.library.path).read_bytes()
        lock = QLockFile(self.library.path + ".lock")
        self.assertTrue(lock.tryLock(0))
        try:
            other = MangaLibrary(self.library.path)
            self.assertIn("禁用保存", other.warning)
        finally:
            lock.unlock()
        with self.assertRaisesRegex(OSError, "其他实例修改"):
            other.save()
        self.assertEqual(Path(self.library.path).read_bytes(), before)
        other.load()
        self.assertEqual(len(other.books), 1)
        self.assertEqual(other.warning, "")
        other.save()

    def test_active_writer_lock_blocks_save(self):
        from PySide6.QtCore import QLockFile
        lock = QLockFile(self.library.path + ".lock")
        self.assertTrue(lock.tryLock(0))
        try:
            with self.assertRaisesRegex(OSError, "其他实例使用"):
                self.library.save()
        finally:
            lock.unlock()
        self.library.save()

    def test_source_pages_unchanged_after_library_operations(self):
        before = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        pages = scan_pages(self.folder)
        book = self.library.add(self.folder, pages)
        book.mark_read(1, pages)
        self.library.save()
        self.library.books.remove(book)
        self.library.save()
        self.assertEqual({p.name: p.read_bytes() for p in self.folder.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
