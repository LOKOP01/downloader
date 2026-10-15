"""Local folder-based manga library. No GUI, network, or writes to source pages."""
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


def natural_key(value):
    # Tagged components remain comparable even if a name starts with a number.
    return tuple((1, int(p), len(p)) if p.isascii() and p.isdigit()
                 else (0, p.casefold()) for p in re.split(r"(\d+)", str(value)))


def folder_key(folder):
    return os.path.normcase(os.path.abspath(os.path.normpath(folder)))


def is_page(path):
    return Path(path).suffix.lower() in (".jpg", ".jpeg")


def _junction(path):
    return getattr(os.path, "isjunction", lambda _: False)(path)


def scan_pages(folder):
    """Sort relative chapter/page names naturally; don't follow directory links."""
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"漫画文件夹不存在：{folder}")
    pages = []

    def fail(error):
        raise error  # Never silently replace a book with an incomplete scan.

    for root, dirs, files in os.walk(folder, onerror=fail, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))
                   and not _junction(os.path.join(root, d))]
        pages.extend(os.path.join(root, f) for f in files if is_page(f))
    return sorted(pages, key=lambda p: natural_key(os.path.relpath(p, folder)))


def discover_books(folder, library_root=False):
    folder = os.path.abspath(folder)
    if not library_root:
        pages = scan_pages(folder)
        return [(folder, pages)] if pages else []
    result = []
    with os.scandir(folder) as entries:
        children = sorted((e.path for e in entries if e.is_dir(follow_symlinks=False)
                           and not _junction(e.path)), key=natural_key)
    for child in children:
        pages = scan_pages(child)
        if pages:
            result.append((child, pages))
    return result


@dataclass
class MangaBook:
    folder: str
    title: str = ""
    page_index: int = 0
    page_count: int = 0
    last_page: str = ""  # Relative name survives moving a whole book.
    last_read: str = ""
    favorite: bool = False

    def restore_index(self, pages):
        if self.last_page:
            for i, path in enumerate(pages):
                if folder_key(path) == folder_key(os.path.join(self.folder, self.last_page)):
                    return i
        return max(0, min(self.page_index, len(pages) - 1))

    def mark_read(self, index, pages):
        self.page_index = max(0, min(index, len(pages) - 1))
        self.page_count = len(pages)
        self.last_page = os.path.relpath(pages[self.page_index], self.folder)
        self.last_read = datetime.now().isoformat(timespec="seconds")


class MangaLibrary:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.books = []
        self.preferences = {"fit": "page", "rtl": False, "paper": False}
        self.warning = ""
        self.load()

    @staticmethod
    def _read(path):
        with open(path, encoding="utf-8-sig") as file:
            data = json.load(file)
        if not isinstance(data, dict) or not isinstance(data.get("books"), list):
            raise ValueError("书架记录格式无效")
        books, seen = [], set()
        for entry in data["books"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("folder"), str):
                raise ValueError("漫画记录格式无效")
            folder = os.path.abspath(entry["folder"])
            if not entry["folder"].strip():
                raise ValueError("漫画路径为空")
            key = folder_key(folder)
            if key in seen:
                continue
            seen.add(key)
            books.append(MangaBook(
                folder, str(entry.get("title") or Path(folder).name),
                max(0, int(entry.get("page_index", 0))),
                max(0, int(entry.get("page_count", 0))),
                str(entry.get("last_page") or ""), str(entry.get("last_read") or ""),
                bool(entry.get("favorite", False))))
        return books, data.get("preferences", {})

    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            books, prefs = self._read(self.path)
        except (OSError, ValueError, TypeError):
            self.warning = "书架记录读取失败，原文件已保留。"
            try:
                books, prefs = self._read(self.path + ".bak")
                self.warning += "已从备份恢复。"
            except (OSError, ValueError, TypeError):
                return
        self.books = books
        if isinstance(prefs, dict):
            self.preferences.update({
                "fit": prefs.get("fit") if prefs.get("fit") in ("page", "width", "actual") else "page",
                "rtl": bool(prefs.get("rtl", False)), "paper": bool(prefs.get("paper", False))})

    def save(self):
        """Atomic replace; only a valid previous snapshot may become the backup."""
        parent = os.path.dirname(self.path)
        os.makedirs(parent, exist_ok=True)
        data = {"version": 1, "books": [asdict(book) for book in self.books],
                "preferences": self.preferences}
        fd, temp = tempfile.mkstemp(dir=parent, prefix=".manga-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            if os.path.exists(self.path):
                try:
                    self._read(self.path)
                except (OSError, ValueError, TypeError):
                    # Keep the corrupt original as well as any good backup.
                    if not os.path.exists(self.path + ".corrupt"):
                        shutil.copy2(self.path, self.path + ".corrupt")
                else:
                    shutil.copy2(self.path, self.path + ".bak")
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.remove(temp)

    def add(self, folder, pages):
        key = folder_key(folder)
        book = next((b for b in self.books if folder_key(b.folder) == key), None)
        if book is None:
            book = MangaBook(os.path.abspath(folder), Path(folder).name)
            self.books.append(book)
        book.page_index = book.restore_index(pages)
        book.page_count = len(pages)
        return book

    def import_yiejian(self, path):
        """Read the standalone reader's JSON, retaining existing project records."""
        with open(path, encoding="utf-8-sig") as file:
            data = json.load(file)
        entries = data.get("Books") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise ValueError("这不是页间阅读器的书架文件")
        pending = []
        seen = {folder_key(b.folder) for b in self.books}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("Folder"), str):
                raise ValueError("页间漫画记录格式无效")
            folder = entry["Folder"]
            if not folder.strip():
                raise ValueError("页间漫画路径为空")
            key = folder_key(folder)
            if key in seen:
                continue
            page = str(entry.get("LastPage") or "")
            if os.path.isabs(page):
                page = os.path.relpath(page, folder)
            pending.append(MangaBook(os.path.abspath(folder), str(entry.get("Title") or Path(folder).name),
                                     max(0, int(entry.get("PageIndex", 0))),
                                     max(0, int(entry.get("PageCount", 0))), page,
                                     str(entry.get("LastRead") or ""), bool(entry.get("Favorite", False))))
            seen.add(key)
        self.books.extend(pending)
        return len(pending)
