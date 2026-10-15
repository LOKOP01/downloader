"""Local folder-based manga library. No GUI, network, or writes to source pages."""
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QLockFile


def natural_key(value):
    # Tagged components remain comparable even if a name starts with a number.
    return tuple((1, int(p), len(p)) if p.isascii() and p.isdigit()
                 else (0, p.casefold()) for p in re.split(r"(\d+)", str(value)))


def _nonnegative_int(value):
    try:
        return max(0, int(value))
    except OverflowError as error:
        raise ValueError("页码或页数必须是有限整数") from error


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
        # None is deliberately not a valid disk revision: an unread library
        # must never overwrite existing records after a transient load failure.
        self._revision = None
        try:
            self.load()
        except OSError as error:
            self._revision = None
            self.warning = f"书架暂时无法读取，已禁用保存；请重新加载书架：{error}"

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
                _nonnegative_int(entry.get("page_index", 0)),
                _nonnegative_int(entry.get("page_count", 0)),
                str(entry.get("last_page") or ""), str(entry.get("last_read") or ""),
                bool(entry.get("favorite", False))))
        return books, data.get("preferences", {})

    @contextmanager
    def _locked(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        lock = QLockFile(self.path + ".lock")
        # A slow live writer must never lose its lock just because of its age.
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise OSError("书架正在被其他实例使用，请稍后重试。")
        try:
            yield
        finally:
            lock.unlock()

    def _disk_revision(self):
        revisions = []
        for path in (self.path, self.path + ".bak"):
            try:
                revisions.append(hashlib.sha256(Path(path).read_bytes()).digest())
            except FileNotFoundError:
                revisions.append(None)
        return tuple(revisions)

    @staticmethod
    def _atomic_write(path, content):
        # Never open the destination for writing: it may be a hard/symbolic link.
        fd, temp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".manga-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.remove(temp)

    def load(self):
        with self._locked():
            self._revision = self._disk_revision()
            self.warning = ""
            if self._revision == (None, None):
                return
            try:
                books, prefs = self._read(self.path)
            except (OSError, ValueError, TypeError):
                self.warning = "书架记录缺失或读取失败，已有文件已保留。"
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
        """Serialize cooperating writers and reject stale in-memory snapshots."""
        with self._locked():
            if self._disk_revision() != self._revision:
                raise OSError("书架已被其他实例修改，为避免丢失记录，已取消保存；请重新加载书架。")
            data = {"version": 1, "books": [asdict(book) for book in self.books],
                    "preferences": self.preferences}
            content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
            if os.path.exists(self.path):
                try:
                    self._read(self.path)
                except (OSError, ValueError, TypeError):
                    # Retain an existing corrupt snapshot, including a link itself.
                    if not os.path.lexists(self.path + ".corrupt"):
                        self._atomic_write(self.path + ".corrupt", Path(self.path).read_bytes())
                else:
                    self._atomic_write(self.path + ".bak", Path(self.path).read_bytes())
            # If replacing the primary fails, this instance can safely retry even
            # when its own successful backup replacement changed the revision.
            self._revision = self._disk_revision()
            self._atomic_write(self.path, content)
            self._revision = self._disk_revision()

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
                                     _nonnegative_int(entry.get("PageIndex", 0)),
                                     _nonnegative_int(entry.get("PageCount", 0)), page,
                                     str(entry.get("LastRead") or ""), bool(entry.get("Favorite", False))))
            seen.add(key)
        self.books.extend(pending)
        return len(pending)
