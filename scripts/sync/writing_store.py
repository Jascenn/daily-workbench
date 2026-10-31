#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Obsidian 创作项目的稳定标识与安全文件定位。

工作台不接收前端传来的文件路径。页面只拿 ``bookId`` / ``chapterId``，
服务端再用本模块从 ``Obsidian-Tech/projects/`` 重新发现文件。这样即使前端
被篡改，也只能命中服务端已知的 Markdown 文件。
"""
import datetime as dt
import hashlib
import mimetypes
import os
import re
import shutil
from urllib.parse import quote


OBSIDIAN_ROOT = os.path.expanduser("~/Documents/Obsidian-Tech")
WRITING_ROOT = os.path.join(OBSIDIAN_ROOT, "projects")
CREATIVE_ROOT = os.path.join(OBSIDIAN_ROOT, "创作")
OUTLINE_NAME = "章节目录.md"
ID_RE = re.compile(r"^(?:book|chapter)_[0-9a-f]{20}$")
DOC_ID_RE = re.compile(r"^doc_[0-9a-f]{20}$")
CHAPTER_STEM_RE = re.compile(r"^(第[一二三四五六七八九十百]+章|尾声)")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_CHAPTER_BYTES = 2 * 1024 * 1024
MAX_ASSET_BYTES = 20 * 1024 * 1024
CONTENT_TYPE_DIRS = {
    "公众号": "公众号",
    "Twitter": "Twitter",
    "X": "Twitter",
    "Twitter/X": "Twitter",
    "知识星球": "知识星球",
}
TRASH_DIR_NAME = ".trash"
ASSET_RE = re.compile(
    r"^(?:\./)?(?!\.{1,2}/)[^/\\]+(?:/[^/\\]+)*\.(?:png|jpg|jpeg|gif|webp|avif)$",
    re.I,
)


class WritingConflict(Exception):
    """正文在打开后已被外部修改，拒绝覆盖。"""


class WritingNotFound(Exception):
    """章节不存在，或正文尚未创建。"""


def _real(path):
    return os.path.realpath(os.path.abspath(path))


def inside(root, path):
    """路径真实解析后仍位于 root 内才放行，符号链接逃逸会在这里失败。"""
    try:
        return os.path.commonpath((_real(root), _real(path))) == _real(root)
    except (OSError, ValueError):
        return False


def relative_path(path, root=WRITING_ROOT):
    full = _real(path)
    base = _real(root)
    if not inside(base, full):
        raise ValueError("路径不在 Obsidian 创作目录内")
    return os.path.relpath(full, base).replace(os.sep, "/")


def stable_id(kind, path, root=WRITING_ROOT):
    rel = relative_path(path, root)
    digest = hashlib.sha256(("%s:%s" % (kind, rel)).encode("utf-8")).hexdigest()
    return "%s_%s" % (kind, digest[:20])


def book_id(project_path, root=WRITING_ROOT):
    return stable_id("book", project_path, root)


def chapter_id(chapter_path, root=WRITING_ROOT):
    return stable_id("chapter", chapter_path, root)


def creative_root():
    """独立创作区。小说仍兼容 projects/ 下已有的书，新内容统一进 创作/。"""
    if os.path.abspath(OBSIDIAN_ROOT) == os.path.expanduser("~/Documents/Obsidian-Tech"):
        return CREATIVE_ROOT
    return os.path.join(OBSIDIAN_ROOT, "创作")


def novel_root():
    return os.path.join(creative_root(), "小说")


def writing_roots():
    roots = [WRITING_ROOT, novel_root()]
    out = []
    for root in roots:
        root = os.path.abspath(root)
        if root not in out:
            out.append(root)
    return out


def _chapter_name(title):
    match = CHAPTER_STEM_RE.match(title)
    name = match.group(1) if match else title
    if not name or os.path.basename(name) != name or name in (".", ".."):
        return ""
    return name


def discover_chapters(root=WRITING_ROOT):
    """返回所有含章节目录的创作项目及其章节，不读取正文。"""
    base = _real(root)
    if not os.path.isdir(base):
        return []
    books = []
    try:
        entries = sorted(os.scandir(base), key=lambda item: item.name)
    except OSError:
        return []
    for entry in entries:
        # 只发现真实目录；项目目录本身是符号链接时直接跳过。
        if not entry.is_dir(follow_symlinks=False):
            continue
        project = entry.path
        outline = os.path.join(project, OUTLINE_NAME)
        if not os.path.isfile(outline) or not inside(base, outline):
            continue
        try:
            with open(outline, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            continue
        chapters = []
        for match in re.finditer(r"^##\s+(.+?)\s*$", text, re.M):
            title = match.group(1).strip()
            if title.startswith("《") or title in ("章节目录",):
                continue
            name = _chapter_name(title)
            if not name:
                continue
            path = os.path.join(project, name + ".md")
            chapters.append({
                "bookId": book_id(project, base),
                "chapterId": chapter_id(path, base),
                "bookTitle": entry.name,
                "title": title,
                "name": name,
                "path": path,
                "written": os.path.isfile(path) and inside(base, path),
            })
        books.append({
            "bookId": book_id(project, base),
            "bookTitle": entry.name,
            "path": project,
            "root": base,
            "chapters": chapters,
        })
    return books


def discover_books():
    """兼容旧 projects/ 小说，同时发现 创作/小说/ 下的新作品。"""
    books = []
    for root in writing_roots():
        books.extend(discover_chapters(root))
    return books


def resolve_chapter(wanted_book_id, wanted_chapter_id, root=None):
    """按稳定 ID 定位章节；ID 格式不合法时立即拒绝。"""
    if not ID_RE.fullmatch(str(wanted_book_id or "")):
        return None
    if not ID_RE.fullmatch(str(wanted_chapter_id or "")):
        return None
    roots = [root] if root else writing_roots()
    for current_root in roots:
        for book in discover_chapters(current_root):
            if book["bookId"] != wanted_book_id:
                continue
            for chapter in book["chapters"]:
                if chapter["chapterId"] == wanted_chapter_id:
                    chapter["bookTitle"] = book["bookTitle"]
                    chapter["root"] = book["root"]
                    return chapter
    return None


def safe_chapter_path(chapter, root=None):
    """返回安全的章节路径；文件可以尚未创建，供首次保存时原子创建。"""
    if not isinstance(chapter, dict):
        raise ValueError("章节不存在")
    root = root or chapter.get("root") or WRITING_ROOT
    path = chapter.get("path") or ""
    if not inside(root, path):
        raise ValueError("章节路径越界")
    if not path.endswith(".md") or os.path.islink(path):
        raise ValueError("章节不是普通 Markdown 文件")
    parent = os.path.dirname(path)
    if not inside(root, parent) or not os.path.isdir(parent) or os.path.islink(parent):
        raise ValueError("章节目录不存在或不是普通目录")
    return path


def safe_chapter_file(chapter, root=None):
    """返回已存在的普通 Markdown 文件。"""
    path = safe_chapter_path(chapter, root)
    if not os.path.isfile(path):
        raise ValueError("章节正文尚未创建")
    return path


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _obsidian_url(path):
    """只生成 Obsidian-Tech 内的深链，不把本机绝对路径交给前端。"""
    if not inside(OBSIDIAN_ROOT, path):
        return ""
    rel = os.path.relpath(_real(path), _real(OBSIDIAN_ROOT)).replace(os.sep, "/")
    if rel.endswith(".md"):
        rel = rel[:-3]
    return "obsidian://open?vault=%s&file=%s" % (
        quote(os.path.basename(_real(OBSIDIAN_ROOT)), safe=""),
        quote(rel, safe=""),
    )


def _modified_at(path):
    return dt.datetime.fromtimestamp(os.path.getmtime(path)).astimezone().isoformat(
        timespec="seconds")


def _payload(chapter, content, sha256, modified_at, created=None):
    exists = os.path.isfile(chapter["path"])
    return {
        "bookId": chapter["bookId"],
        "chapterId": chapter["chapterId"],
        "title": chapter["title"],
        "name": chapter["name"],
        "bookTitle": chapter["bookTitle"],
        "written": exists,
        "content": content,
        "sha256": sha256,
        "chars": len(content),
        "modifiedAt": modified_at,
        "obsidianUrl": _obsidian_url(chapter["path"]) if exists else "",
        **({"created": created} if created is not None else {}),
    }


def read_chapter(wanted_book_id, wanted_chapter_id, root=None):
    """读取章节正文与当前哈希；未创建的章节返回空正文和空文件哈希。"""
    chapter = resolve_chapter(wanted_book_id, wanted_chapter_id, root)
    if chapter is None:
        raise WritingNotFound("章节不存在")
    path = safe_chapter_path(chapter, root)
    if not os.path.isfile(path):
        return _payload(chapter, "", _sha256(b""), None)
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) > MAX_CHAPTER_BYTES:
        raise ValueError("章节正文超过 2 MB，工作台暂不打开")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("章节不是 UTF-8 Markdown") from exc
    return _payload(chapter, content, _sha256(data), _modified_at(path))


def read_asset(wanted_book_id, wanted_chapter_id, asset_path, root=None):
    """只允许读取章节同目录或子目录里的常见图片，防止借图片接口越界。"""
    chapter = resolve_chapter(wanted_book_id, wanted_chapter_id, root)
    if chapter is None:
        raise WritingNotFound("章节不存在")
    base_root = root or chapter.get("root") or WRITING_ROOT
    chapter_path = safe_chapter_file(chapter, base_root)
    return _read_asset(os.path.dirname(chapter_path), asset_path, base_root)


def _read_asset(base_dir, asset_path, root):
    """从已定位的 Markdown 同目录读取图片，章节与独立稿件共用。"""
    raw = str(asset_path or "").replace("\\", "/")
    if not ASSET_RE.fullmatch(raw):
        raise ValueError("图片路径格式不正确")
    if raw.startswith("./"):
        raw = raw[2:]
    target = os.path.join(base_dir, raw)
    if not inside(root, target):
        raise ValueError("图片路径不在 Obsidian 创作目录内")
    if os.path.islink(target) or not os.path.isfile(target):
        raise ValueError("图片不存在或不是普通文件")
    if os.path.getsize(target) > MAX_ASSET_BYTES:
        raise ValueError("图片超过 20 MB")
    ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
    with open(target, "rb") as handle:
        return ctype, handle.read()


def write_chapter(wanted_book_id, wanted_chapter_id, content, base_sha256,
                  backup_dir, root=None):
    """冲突检测、备份并原子写入章节正文。

    调用方负责串行化写入。这里仍会在替换前重新核对哈希，缩小 Obsidian
    与其他编辑器并发写入时覆盖新内容的窗口。
    """
    if not isinstance(content, str):
        raise ValueError("正文必须是字符串")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_CHAPTER_BYTES:
        raise ValueError("章节正文超过 2 MB")
    if not isinstance(base_sha256, str) or not SHA256_RE.fullmatch(base_sha256):
        raise ValueError("baseSha256 格式不正确")

    chapter = resolve_chapter(wanted_book_id, wanted_chapter_id, root)
    if chapter is None:
        raise WritingNotFound("章节不存在")
    path = safe_chapter_path(chapter, root)

    existed = os.path.isfile(path)
    if existed:
        with open(path, "rb") as handle:
            current = handle.read()
    else:
        current = b""
    current_sha = _sha256(current)
    if current_sha != base_sha256:
        raise WritingConflict("章节已被 Obsidian 或其他编辑器修改")

    if existed:
        os.makedirs(backup_dir, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = os.path.join(
            backup_dir,
            "%s-%s-%s.md.bak" % (chapter["bookId"], chapter["chapterId"], stamp),
        )
        shutil.copy2(path, backup)

    mode = os.stat(path).st_mode if existed else 0o644
    tmp = os.path.join(
        os.path.dirname(path),
        ".%s.tmp.%d.%d" % (os.path.basename(path), os.getpid(), id(encoded)),
    )
    try:
        with open(tmp, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)

        if existed:
            # 备份完成后、替换前再核一次，避免期间被 Obsidian 写入的新版本被盖掉。
            with open(path, "rb") as handle:
                latest_sha = _sha256(handle.read())
            if latest_sha != base_sha256:
                raise WritingConflict("章节刚刚被 Obsidian 或其他编辑器修改")
            os.replace(tmp, path)
        else:
            # 首次保存用硬链接原子占位：文件若在编辑期间被 Obsidian 创建，这里会失败，
            # 不会把外部刚写下的正文静默覆盖。
            try:
                os.link(tmp, path)
            except FileExistsError as exc:
                raise WritingConflict("章节刚刚被 Obsidian 或其他编辑器创建") from exc
            os.unlink(tmp)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    return _payload(chapter, content, _sha256(encoded), _modified_at(path),
                    created=not existed)


def _clean_title(title):
    value = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', "-", str(title or ""))
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value or value in (".", ".."):
        raise ValueError("标题不能为空")
    return value[:80].rstrip(" .")


def obsidian_trash_dir():
    """Obsidian 自带回收站目录；删除内容先进这里，可在 Obsidian 内恢复。"""
    trash = os.path.join(OBSIDIAN_ROOT, TRASH_DIR_NAME)
    os.makedirs(trash, exist_ok=True)
    if os.path.islink(trash) or not os.path.isdir(trash):
        raise ValueError("Obsidian 回收站不可用")
    return trash


def _trash_move(path, display_name):
    """把文件或目录移入回收站；重名时加时间戳，绝不覆盖已有备份。"""
    trash = obsidian_trash_dir()
    target = os.path.join(trash, display_name)
    if os.path.exists(target) or os.path.islink(target):
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = os.path.join(trash, "%s.%s" % (display_name, stamp))
    shutil.move(path, target)
    return os.path.basename(target)


def delete_document(wanted_id):
    """删除独立稿件：整体移入 Obsidian 回收站，不直接物理删除。"""
    doc = resolve_document(wanted_id)
    if doc is None:
        raise WritingNotFound("稿件不存在")
    path = doc["path"]
    root = creative_root()
    if os.path.islink(path) or not inside(root, path) or not os.path.isfile(path):
        raise ValueError("稿件不是普通 Markdown 文件")
    trash_name = _trash_move(path, os.path.basename(path))
    return {
        "ok": True,
        "kind": "document",
        "contentType": doc["contentType"],
        "title": doc["title"],
        "trashName": trash_name,
    }


def delete_novel(wanted_book_id):
    """删除整本小说：项目目录（目录+章节+设定）整体移入 Obsidian 回收站。"""
    if not str(wanted_book_id or "").startswith("book_") or \
            not ID_RE.fullmatch(str(wanted_book_id or "")):
        raise WritingNotFound("小说不存在")
    for root in writing_roots():
        for book in discover_chapters(root):
            if book["bookId"] != wanted_book_id:
                continue
            path = book["path"]
            if os.path.islink(path) or not inside(root, path) or not os.path.isdir(path):
                raise ValueError("小说目录不安全")
            trash_name = _trash_move(path, book["bookTitle"])
            return {
                "ok": True,
                "kind": "novel",
                "title": book["bookTitle"],
                "chapters": len(book["chapters"]),
                "written": sum(1 for c in book["chapters"] if c["written"]),
                "trashName": trash_name,
            }
    raise WritingNotFound("小说不存在")


def list_trash():
    """回收站清单：只给页面展示用的名称/类型/大小，不暴露本机路径。"""
    trash = os.path.join(OBSIDIAN_ROOT, TRASH_DIR_NAME)
    items = []
    if os.path.islink(trash) or not os.path.isdir(trash):
        return items
    try:
        entries = sorted(os.scandir(trash), key=lambda item: item.name)
    except OSError:
        return items
    for entry in entries:
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        kind = "novel" if entry.is_dir(follow_symlinks=False) else "document"
        items.append({
            "kind": kind,
            "name": entry.name,
            "modifiedAt": _modified_at(entry.path),
            "bytes": stat.st_size,
        })
    items.sort(key=lambda item: item["modifiedAt"], reverse=True)
    return items


TRASH_STAMP_RE = re.compile(r"\.\d{8}-\d{6}-\d+$")


def _unique_restore_target(base, is_dir):
    if not (os.path.exists(base) or os.path.islink(base)):
        return base
    # 文件的后缀必须插在扩展名之前，否则恢复出来的文件不再是 .md、会被发现逻辑漏掉。
    template = base + "-恢复{}" if is_dir else os.path.splitext(base)[0] + "-恢复{}" + os.path.splitext(base)[1]
    suffix = 2
    while True:
        candidate = template.format(suffix)
        if not (os.path.exists(candidate) or os.path.islink(candidate)):
            return candidate
        suffix += 1


def restore_from_trash(trash_name):
    """把回收站里的文件/目录移回原位置；重名时加「-恢复N」后缀，绝不覆盖。"""
    raw = str(trash_name or "").strip()
    if not raw or os.path.basename(raw) != raw or "/" in raw or "\\" in raw:
        raise ValueError("回收站条目名称不正确")
    trash = obsidian_trash_dir()
    source = os.path.join(trash, raw)
    if os.path.islink(source) or not os.path.exists(source):
        raise WritingNotFound("回收站中不存在该条目")
    # 删除时为避免重名追加的时间戳后缀在恢复时还原；恢复目标重名则再加「-恢复N」。
    stem = TRASH_STAMP_RE.sub("", raw)
    creative = creative_root()
    if os.path.isdir(source) and not os.path.islink(source):
        target = _unique_restore_target(os.path.join(novel_root(), stem), True)
        if not inside(creative, target):
            raise ValueError("恢复路径越界")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(source, target)
        return {"ok": True, "kind": "novel", "name": os.path.basename(target)}
    if not os.path.isfile(source):
        raise ValueError("回收站条目不是普通文件或目录")
    try:
        with open(source, encoding="utf-8") as handle:
            head = handle.read(4096)
    except UnicodeDecodeError as exc:
        raise ValueError("回收站条目不是 UTF-8 Markdown") from exc
    content_type = _frontmatter(head).get("content-type") or ""
    folder_name = CONTENT_TYPE_DIRS.get(content_type)
    if not folder_name:
        raise ValueError("无法识别稿件类型（frontmatter 缺 content-type），请在 Obsidian 中手动恢复")
    target = _unique_restore_target(os.path.join(content_dir(folder_name), stem), False)
    if not inside(creative, target):
        raise ValueError("恢复路径越界")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.move(source, target)
    doc = resolve_document(stable_id("doc", target, OBSIDIAN_ROOT))
    if doc is None:
        raise OSError("稿件恢复后无法重新发现")
    return {"ok": True, "kind": "document", "name": os.path.basename(target),
            "contentType": folder_name}


def _frontmatter(text):
    if not str(text or "").startswith("---"):
        return {}
    parts = str(text).split("---", 2)
    if len(parts) < 3:
        return {}
    out = {}
    for line in parts[1].splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip().strip("'\"")
    return out


def content_dir(content_type):
    canonical = CONTENT_TYPE_DIRS.get(str(content_type or "").strip())
    if not canonical:
        raise ValueError("不支持的创作类型")
    return os.path.join(creative_root(), canonical)


def _document_paths(content_type=None):
    types = [content_type] if content_type else ["公众号", "Twitter", "知识星球"]
    for kind in types:
        folder = content_dir(kind)
        if not os.path.isdir(folder) or os.path.islink(folder):
            continue
        for entry in sorted(os.scandir(folder), key=lambda item: item.name):
            if (not entry.is_file(follow_symlinks=False) or
                    not entry.name.endswith(".md") or entry.name.startswith(".")):
                continue
            yield CONTENT_TYPE_DIRS.get(kind, kind), entry.path


def discover_documents(content_type=None):
    docs = []
    for kind, path in _document_paths(content_type):
        text = ""
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            continue
        fm = _frontmatter(text)
        docs.append({
            "documentId": stable_id("doc", path, OBSIDIAN_ROOT),
            "contentType": kind,
            "title": fm.get("title") or os.path.splitext(os.path.basename(path))[0],
            "status": fm.get("status") or "草稿",
            "chars": len(text),
            "modifiedAt": _modified_at(path),
            "obsidianUrl": _obsidian_url(path),
            "path": path,
            "root": creative_root(),
        })
    docs.sort(key=lambda item: item["modifiedAt"], reverse=True)
    return docs


def resolve_document(wanted_id, root=None):
    if not DOC_ID_RE.fullmatch(str(wanted_id or "")):
        return None
    for doc in discover_documents():
        if doc["documentId"] == wanted_id and (not root or inside(root, doc["path"])):
            return doc
    return None


def _document_payload(doc, content, sha256, modified_at):
    return {
        "documentId": doc["documentId"],
        "contentType": doc["contentType"],
        "title": doc["title"],
        "status": doc["status"],
        "content": content,
        "sha256": sha256,
        "chars": len(content),
        "modifiedAt": modified_at,
        "obsidianUrl": _obsidian_url(doc["path"]),
    }


def read_document(wanted_id):
    doc = resolve_document(wanted_id)
    if doc is None:
        raise WritingNotFound("稿件不存在")
    if os.path.islink(doc["path"]) or not os.path.isfile(doc["path"]):
        raise ValueError("稿件不是普通 Markdown 文件")
    with open(doc["path"], "rb") as handle:
        data = handle.read()
    if len(data) > MAX_CHAPTER_BYTES:
        raise ValueError("稿件超过 2 MB，工作台暂不打开")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("稿件不是 UTF-8 Markdown") from exc
    return _document_payload(doc, content, _sha256(data), _modified_at(doc["path"]))


def read_document_asset(wanted_id, asset_path):
    """读取独立稿件同目录或子目录里的图片，路径仍由服务端重新定位。"""
    doc = resolve_document(wanted_id)
    if doc is None:
        raise WritingNotFound("稿件不存在")
    path = doc["path"]
    root = creative_root()
    if os.path.islink(path) or not inside(root, path) or not os.path.isfile(path):
        raise ValueError("稿件不是普通 Markdown 文件")
    return _read_asset(os.path.dirname(path), asset_path, root)


def create_document(content_type, title):
    kind = CONTENT_TYPE_DIRS.get(str(content_type or "").strip())
    if not kind:
        raise ValueError("不支持的创作类型")
    clean = _clean_title(title)
    folder = content_dir(kind)
    os.makedirs(folder, exist_ok=True)
    if os.path.islink(folder) or not inside(creative_root(), folder):
        raise ValueError("创作目录不安全")
    base = os.path.join(folder, clean)
    path = base + ".md"
    suffix = 2
    while os.path.exists(path):
        path = "%s-%d.md" % (base, suffix)
        suffix += 1
    now = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    content = (
        "---\n"
        "type: 内容笔记\n"
        "product-type: 内容创作\n"
        "content-type: %s\n"
        "title: %s\n"
        "status: 草稿\n"
        "created: %s\n"
        "updated: %s\n"
        "---\n\n# %s\n\n"
    ) % (kind, clean, now, now, clean)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise ValueError("同名稿件刚刚被创建，请换个标题")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    doc = resolve_document(stable_id("doc", path, OBSIDIAN_ROOT))
    if doc is None:
        raise OSError("稿件创建后无法重新发现")
    return _document_payload(doc, content, _sha256(content.encode("utf-8")),
                             _modified_at(path))


def write_document(wanted_id, content, base_sha256, backup_dir):
    doc = resolve_document(wanted_id)
    if doc is None:
        raise WritingNotFound("稿件不存在")
    if not isinstance(content, str):
        raise ValueError("正文必须是字符串")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_CHAPTER_BYTES:
        raise ValueError("稿件超过 2 MB")
    if not isinstance(base_sha256, str) or not SHA256_RE.fullmatch(base_sha256):
        raise ValueError("baseSha256 格式不正确")
    path = doc["path"]
    if os.path.islink(path) or not inside(creative_root(), path):
        raise ValueError("稿件路径不安全")
    with open(path, "rb") as handle:
        current = handle.read()
    if _sha256(current) != base_sha256:
        raise WritingConflict("稿件已被 Obsidian 或其他编辑器修改")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = os.path.join(
        backup_dir, "%s-%s.md.bak" % (doc["documentId"], stamp))
    shutil.copy2(path, backup)
    mode = os.stat(path).st_mode
    tmp = os.path.join(
        os.path.dirname(path),
        ".%s.tmp.%d.%d" % (os.path.basename(path), os.getpid(), id(encoded)),
    )
    try:
        with open(tmp, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        with open(path, "rb") as handle:
            latest_sha = _sha256(handle.read())
        if latest_sha != base_sha256:
            raise WritingConflict("稿件刚刚被 Obsidian 或其他编辑器修改")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    latest = resolve_document(wanted_id)
    if latest is None:
        raise OSError("稿件保存后无法重新发现")
    return _document_payload(latest, content, _sha256(encoded), _modified_at(path))


def create_novel(title):
    clean = _clean_title(title)
    root = novel_root()
    os.makedirs(root, exist_ok=True)
    project = os.path.join(root, clean)
    if os.path.exists(project):
        raise ValueError("同名小说已存在")
    os.mkdir(project, 0o755)
    now = dt.datetime.now().astimezone().strftime("%Y-%m-%d")
    outline = (
        "---\n"
        "type: 项目笔记\n"
        "product-type: 创作项目\n"
        "content-type: 小说\n"
        "title: %s\n"
        "status: 创作中\n"
        "created: %s\n"
        "updated: %s\n"
        "---\n\n# 《%s》章节目录\n\n"
        "## 第一章 未命名章节\n"
        "在 Obsidian 或工作台里补上这一章的梗概。\n"
    ) % (clean, now, now, clean)
    path = os.path.join(project, OUTLINE_NAME)
    with open(path, "x", encoding="utf-8") as handle:
        handle.write(outline)
        handle.flush()
        os.fsync(handle.fileno())
    book = next((item for item in discover_chapters(root)
                 if os.path.realpath(item["path"]) == os.path.realpath(project)), None)
    if not book or not book["chapters"]:
        raise OSError("小说创建后无法重新发现章节")
    chapter = book["chapters"][0]
    return {
        "bookId": book["bookId"],
        "bookTitle": book["bookTitle"],
        "chapterId": chapter["chapterId"],
        "chapterTitle": chapter["title"],
        "obsidianUrl": _obsidian_url(path),
    }
