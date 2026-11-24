"""
[INPUT]: 仅依赖 Python 标准库（html.parser / ipaddress / re / socket / urllib）
[OUTPUT]: 导出 MAX_ARTICLE_BYTES、_validate_article_url、_SafeArticleRedirectHandler、_ArticleHTMLParser、_extract_article、_decode_article_body、_fetch_article
[POS]: server/ 文章站内阅读与 SSRF 安全防护子模块，由 app.py 路由 /api/article 调用
[PROTOCOL]: Update this header on change, then check GEMINI.md
"""
import html as _html
import html.parser
import ipaddress
import re as _re
import socket
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

MAX_ARTICLE_BYTES = 4 * 1024 * 1024
MAX_ARTICLE_CHARS = 80_000
ARTICLE_TIMEOUT = 15
_ARTICLE_SKIP_TAGS = {
    "script", "style", "noscript", "svg", "nav", "footer", "aside", "form",
    "button", "input", "select", "textarea", "iframe", "canvas", "video", "audio",
}
_ARTICLE_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_ARTICLE_BLOCK_TAGS = {"p", "h2", "h3", "blockquote", "li", "pre"}
_ARTICLE_CANDIDATE_RE = _re.compile(
    r"(?:^|[\s_-])(?:article|entry|post|markdown|prose|story)(?:$|[\s_-])",
    _re.I,
)
_ARTICLE_CONTENT_RE = _re.compile(
    r"(?:^|[\s_-])(?:article|entry|post|markdown|prose|story)"
    r"(?:[\s_-][a-z0-9]+)*[\s_-](?:body|content|text|detail)(?:$|[\s_-])",
    _re.I,
)
# 部分站点会把「追踪报道、相关推荐、点赞分享」等组件放进正文容器。
# 这里只按明确的组件标识跳过，文章自己的普通 ul/li 仍然保留。
_ARTICLE_SKIP_COMPONENT_RE = _re.compile(
    r"(?:^|\s)(?:"
    r"appinn-track|appinn-article-like|"
    r"related-posts?|related-articles?|"
    r"breadcrumbs?|post-navigation|post-nav|"
    r"author-card|author-box|"
    r"mylike|share|social-share"
    r")(?:\s|$)",
    _re.I,
)


def _validate_article_url(url):
    """只允许访问公网 HTTP(S)，阻止本机、私网、链路本地与元数据地址。"""
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("缺少文章地址")
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("只支持 http / https 文章地址")
    if parsed.username or parsed.password:
        raise ValueError("文章地址不能包含用户名或密码")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("文章地址缺少域名")
    if host == "localhost" or host.endswith(".local"):
        raise ValueError("不允许访问本机域名")
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise ValueError("文章地址端口不合法") from exc
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("文章域名无法解析") from exc
    if not infos:
        raise ValueError("文章域名没有可用地址")
    for info in infos:
        ip_text = str((info[4] or [""])[0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError as exc:
            raise ValueError("文章域名解析结果不合法") from exc
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ValueError("不允许访问内网或保留地址")
    return raw


class _SafeArticleRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_article_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _ArticleHTMLParser(html.parser.HTMLParser):
    """从第三方 HTML 中提取结构化正文块，不把远程标签原样带进页面 DOM。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._tag_stack = []
        self._skip_depth = 0
        self._candidate_stack = []
        self._candidates = []
        self._fallback_blocks = []
        self._active_blocks = []
        self._title_tag = []
        self._h1 = []
        self._meta = {}
        self._in_title = False
        self._in_h1 = False
        self._figure = None
        self._image_caption = None
        self._details_stack = []
        self._details_seq = 0
        self._inline_styles = []
        self._pre_depth = 0
        self._list_stack = []

    @staticmethod
    def _clean(text):
        return _re.sub(r"\s+", " ", str(text or "")).strip()

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = {str(k).lower(): str(v or "") for k, v in attrs}
        marker = " ".join((attrs.get("id", ""), attrs.get("class", ""))).strip()
        strength = 0
        if tag in ("article", "main"):
            strength = 1
        if marker and _ARTICLE_CANDIDATE_RE.search(marker):
            strength = max(strength, 2)
        if marker and _ARTICLE_CONTENT_RE.search(marker):
            # WordPress 等站点常用 article > .post-single-content 的两层结构。
            # 外层还包含相关文章、作者卡和侧栏；明确的内容容器应优先于 article。
            strength = 4
        is_candidate = strength > 0
        is_skip = (
            tag in _ARTICLE_SKIP_TAGS
            or bool(marker and _ARTICLE_SKIP_COMPONENT_RE.search(marker))
        )
        candidate = None
        if is_candidate:
            candidate = {
                "id": len(self._candidates),
                "tag": tag,
                "marker": marker,
                "strength": strength,
                "blocks": [],
            }
            self._candidates.append(candidate)
            self._candidate_stack.append(candidate)
        self._tag_stack.append({
            "tag": tag,
            "candidate_id": candidate["id"] if candidate else None,
            "skip": is_skip,
        })
        if tag in ("meta", "link", "time"):
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key and attrs.get("content"):
                self._meta[key] = self._clean(attrs["content"])
            if tag == "time" and attrs.get("datetime"):
                self._meta.setdefault("article:published_time", self._clean(attrs["datetime"]))
        if tag in _ARTICLE_VOID_TAGS:
            if tag == "img" and self._skip_depth == 0 and self._candidate_stack:
                src = next((
                    attrs.get(key) for key in (
                        "data-src", "data-original", "data-lazy-src", "src")
                    if attrs.get(key)
                ), "")
                image = self._safe_image(src, attrs.get("alt", ""))
                if image:
                    if self._figure is not None:
                        self._figure["images"].append(image)
                    else:
                        capture_ids = tuple(c["id"] for c in self._candidate_stack)
                        self._append_record(dict(image, type="image"), capture_ids)
            elif tag == "hr" and self._skip_depth == 0 and self._candidate_stack:
                self._record_block("hr", "")
            return
        if tag == "title":
            self._in_title = True
        if tag == "h1":
            self._in_h1 = True
        if is_skip:
            self._skip_depth += 1
            return
        if tag == "figure" and self._candidate_stack:
            self._figure = {
                "images": [],
                "caption": [],
                "capture_ids": tuple(c["id"] for c in self._candidate_stack),
            }
        elif tag == "details" and self._skip_depth == 0:
            # 博客常用 <details> 折叠长附录；summary 是标题，内部块要归组
            self._details_seq += 1
            self._details_stack.append({"id": self._details_seq, "summary": []})
        elif tag == "summary" and self._details_stack:
            self._details_stack[-1]["collect"] = True
        elif tag == "figcaption" and self._figure is not None:
            self._image_caption = self._figure
        elif tag in ("ul", "ol"):
            self._list_stack.append({"tag": tag, "index": 0})
            if self._active_blocks and self._active_blocks[-1]["tag"] == "li":
                self._active_blocks[-1]["list_marker"] = (
                    "%d." % self._list_stack[-1]["index"] if tag == "ol" else "•")
        elif tag == "a":
            self._inline_styles.append({
                "type": "link",
                "href": self._safe_link(attrs.get("href", "")),
            })
        elif tag in ("strong", "b"):
            self._inline_styles.append({"type": "strong"})
        elif tag in ("em", "i"):
            self._inline_styles.append({"type": "em"})
        elif tag == "code" and self._pre_depth == 0:
            self._inline_styles.append({"type": "code"})
        elif tag == "code" and self._pre_depth > 0:
            if self._active_blocks and self._active_blocks[-1]["tag"] == "pre":
                self._active_blocks[-1]["language_source"] = attrs.get("class", "")
        if tag == "pre":
            self._pre_depth += 1
        if tag in _ARTICLE_BLOCK_TAGS and self._skip_depth == 0:
            marker = ""
            if tag == "li":
                current = self._list_stack[-1] if self._list_stack else {"tag": "ul", "index": 0}
                current["index"] += 1
                marker = ("%d." % current["index"]) if current["tag"] == "ol" else "•"
            block = {
                "tag": tag,
                "parts": [],
                "segments": [],
                "capture_ids": tuple(c["id"] for c in self._candidate_stack),
                "ordered": bool(self._list_stack and self._list_stack[-1]["tag"] == "ol"),
                "marker": marker,
                "language": "",
                "language_source": "",
            }
            if tag == "pre":
                block["language_source"] = attrs.get("class", "")
            self._active_blocks.append(block)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._in_h1 = False
        if tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)
        if tag == "a":
            if self._inline_styles and self._inline_styles[-1]["type"] == "link":
                self._inline_styles.pop()
        elif tag in ("strong", "b"):
            if self._inline_styles and self._inline_styles[-1]["type"] == "strong":
                self._inline_styles.pop()
        elif tag in ("em", "i"):
            if self._inline_styles and self._inline_styles[-1]["type"] == "em":
                self._inline_styles.pop()
        elif tag == "code" and self._pre_depth == 0:
            if self._inline_styles and self._inline_styles[-1]["type"] == "code":
                self._inline_styles.pop()
        if (
            tag in _ARTICLE_BLOCK_TAGS
            and self._active_blocks
            and self._active_blocks[-1]["tag"] == tag
        ):
            block = self._active_blocks.pop()
            if tag == "pre":
                text = "".join(block["parts"]).strip("\n")
                block["segments"] = [{"type": "text", "text": text}]
                if self._active_blocks and self._active_blocks[-1]["tag"] == "pre":
                    self._active_blocks[-1]["language"] = self._pre_language(
                        self._active_blocks[-1].get("language_source", ""))
            else:
                text = self._clean("".join(block["parts"]))
            if text:
                record = {
                    "type": "quote" if tag == "blockquote" else tag,
                    "text": text,
                    "segments": self._clean_segments(block["segments"], text),
                }
                if tag == "li":
                    record["ordered"] = block["ordered"]
                    record["marker"] = block["marker"]
                if tag == "pre":
                    record["language"] = block["language"]
                self._append_record(record, block["capture_ids"])
        if tag == "figcaption" and self._image_caption is not None:
            self._image_caption = None
        if tag == "summary" and self._details_stack:
            self._details_stack[-1]["collect"] = False
        if tag == "details" and self._details_stack:
            self._details_stack.pop()
        if tag == "figure" and self._figure is not None:
            figure = self._figure
            self._figure = None
            caption = self._clean("".join(figure["caption"]))
            for index, image in enumerate(figure["images"]):
                image["caption"] = caption if index == 0 else ""
                self._append_record(dict(image, type="image"), figure["capture_ids"])
        if (
            tag in _ARTICLE_BLOCK_TAGS
            and self._active_blocks
            and self._active_blocks[-1]["tag"] == "li"
            and self._active_blocks[-1].get("list_marker")
        ):
            self._active_blocks[-1]["marker"] = self._active_blocks[-1].pop("list_marker")
        if tag in ("code", "pre"):
            if self._active_blocks and self._active_blocks[-1]["tag"] == "pre":
                self._active_blocks[-1]["language"] = self._pre_language(
                    self._active_blocks[-1].get("language_source", ""))
        if tag in ("ul", "ol") and self._list_stack:
            self._list_stack.pop()
        while self._tag_stack:
            entry = self._tag_stack.pop()
            if entry.get("skip"):
                self._skip_depth = max(0, self._skip_depth - 1)
            if entry.get("candidate_id") is not None:
                self._candidate_stack.pop()
            if entry.get("tag") == tag:
                break

    def handle_data(self, data):
        if self._in_title:
            self._title_tag.append(data)
        if self._in_h1:
            self._h1.append(data)
        if self._details_stack and self._details_stack[-1].get("collect"):
            self._details_stack[-1]["summary"].append(data)
        if self._image_caption is not None:
            self._image_caption["caption"].append(data)
        if self._active_blocks and self._skip_depth == 0:
            self._active_blocks[-1]["parts"].append(data)
            if data:
                style = self._inline_styles[-1] if self._inline_styles else None
                segment_type = style["type"] if style else "text"
                href = style.get("href", "") if style else ""
                if segment_type == "link" and not href:
                    segment_type = "text"
                segments = self._active_blocks[-1]["segments"]
                if (
                    segments
                    and segments[-1]["type"] == segment_type
                    and segments[-1].get("href", "") == href
                ):
                    segments[-1]["text"] += data
                else:
                    segment = {"type": segment_type, "text": data}
                    if href:
                        segment["href"] = href
                    segments.append(segment)

    def _append_record(self, record, capture_ids):
        if self._details_stack:
            top = self._details_stack[-1]
            record["fold"] = top["id"]
            record["fold_summary"] = self._clean("".join(top["summary"]))
        for candidate_id in capture_ids:
            self._candidates[candidate_id]["blocks"].append(record)
        if not capture_ids:
            self._fallback_blocks.append(record)

    def _record_block(self, kind, text):
        record = {"type": kind, "text": text, "segments": []}
        capture_ids = tuple(c["id"] for c in self._candidate_stack)
        self._append_record(record, capture_ids)

    def _safe_link(self, value):
        raw = _html.unescape(str(value or "")).strip()
        if not raw or raw.startswith("#"):
            return ""
        if raw.startswith("mailto:"):
            return raw if _re.match(r"^mailto:[^@\s]+@[^@\s]+", raw, _re.I) else ""
        if raw.startswith("//"):
            raw = "https:" + raw
        absolute = urljoin(getattr(self, "_source_url", ""), raw)
        parsed = urlparse(absolute)
        return absolute if parsed.scheme.lower() in ("http", "https") else ""

    def _safe_image(self, value, alt):
        raw = _html.unescape(str(value or "")).strip()
        if not raw:
            return None
        if raw.startswith("//"):
            raw = "https:" + raw
        absolute = urljoin(getattr(self, "_source_url", ""), raw)
        parsed = urlparse(absolute)
        if parsed.scheme.lower() not in ("http", "https"):
            return None
        return {"src": absolute, "alt": self._clean(alt), "caption": ""}

    @staticmethod
    def _pre_language(marker):
        match = _re.search(r"(?:^|\s)language-([a-z0-9_+-]+)", marker or "", _re.I)
        return match.group(1).lower() if match else ""

    def _clean_segments(self, segments, fallback):
        cleaned = []
        for segment in segments:
            text = segment.get("text", "")
            if not text:
                continue
            if self._pre_depth:
                text = text.replace("\r", "")
            item = {"type": segment.get("type", "text"), "text": text}
            if item["type"] == "link" and segment.get("href"):
                item["href"] = segment["href"]
            elif item["type"] == "link":
                item["type"] = "text"
            cleaned.append(item)
        return cleaned or ([{"type": "text", "text": fallback}] if fallback else [])

    def result(self, source_url):
        def score(candidate):
            blocks = candidate["blocks"]
            return (
                sum(len(b["text"]) for b in blocks if b["type"] in ("p", "quote", "li")),
                sum(1 for b in blocks if b["type"] in ("h2", "h3")),
                len(blocks),
            )

        nonempty = [c for c in self._candidates if c["blocks"]]
        strong = [c for c in nonempty if c["strength"] >= 4]
        if strong:
            blocks = max(strong, key=score)["blocks"]
        elif nonempty:
            blocks = max(nonempty, key=lambda c: (c["strength"], score(c)))["blocks"]
        else:
            blocks = self._fallback_blocks
        cleaned = []
        seen = set()
        chars = 0
        budget_exhausted = False

        def process(block, sink):
            nonlocal chars
            kind = block.get("type") if block.get("type") in (
                "p", "h2", "h3", "quote", "li", "pre", "image", "hr") else "p"
            text = (
                str(block.get("text") or "").strip("\n")
                if kind == "pre"
                else self._clean(block.get("text"))
            )
            if kind == "hr":
                sink.append({"type": "hr", "text": "", "segments": []})
                return True
            if kind == "image":
                image = {
                    "type": "image",
                    "text": "",
                    "segments": [],
                    "src": block["src"],
                    "alt": self._clean(block.get("alt")),
                    "caption": self._clean(block.get("caption")),
                }
                sink.append(image)
                return True
            minimum = 1 if kind == "pre" else (2 if kind in ("h2", "h3", "li") else 8)
            if len(text) < minimum:
                return True
            key = (kind, text)
            if key in seen:
                return True
            seen.add(key)
            if chars + len(text) > MAX_ARTICLE_CHARS:
                remaining = MAX_ARTICLE_CHARS - chars
                if remaining < 20:
                    return False
                text = text[:remaining]
                block = dict(block, segments=[{"type": "text", "text": text}])
            record = {"type": kind, "text": text, "segments": block.get("segments", [])}
            if kind == "li":
                record["ordered"] = bool(block.get("ordered"))
                record["marker"] = str(block.get("marker") or "•")
            if kind == "pre":
                record["language"] = str(block.get("language") or "")
            sink.append(record)
            chars += len(text)
            return len(cleaned) < 160

        for block in blocks:
            fold_id = block.get("fold")
            if fold_id is None:
                if not process(block, cleaned):
                    budget_exhausted = True
                    break
                continue
            # 连续同一折叠块的记录归为一组，前端按 <details> 渲染
            if (
                cleaned
                and cleaned[-1].get("type") == "details"
                and cleaned[-1].get("_fold") == fold_id
            ):
                sink = cleaned[-1]["blocks"]
            else:
                sink = []
                cleaned.append({
                    "type": "details",
                    "text": str(block.get("fold_summary") or "展开查看"),
                    "blocks": sink,
                    "_fold": fold_id,
                })
            if not process(block, sink):
                budget_exhausted = True
                break
        for record in cleaned:
            record.pop("_fold", None)
        cleaned[:] = [r for r in cleaned if not (r.get("type") == "details" and not r["blocks"])]
        if not cleaned:
            raise ValueError("没有从页面里识别出可读正文")
        title = (
            self._meta.get("og:title")
            or self._meta.get("twitter:title")
            or self._clean("".join(self._h1))
            or self._clean("".join(self._title_tag))
            or urlparse(source_url).hostname
            or "未命名文章"
        )
        site = (
            self._meta.get("og:site_name")
            or urlparse(source_url).hostname
            or ""
        )
        if site:
            for separator in (" - ", " | ", " — ", " – ", " _ "):
                suffix = separator + site
                if title.endswith(suffix):
                    title = title[:-len(suffix)].rstrip()
                    break
        return {
            "ok": True,
            "url": source_url,
            "title": title,
            "site": site,
            "publishedAt": self._meta.get("article:published_time") or "",
            "description": self._meta.get("og:description") or self._meta.get("description") or "",
            "blocks": cleaned,
            "chars": chars,
        }


def _extract_article(source, source_url):
    parser = _ArticleHTMLParser()
    parser._source_url = source_url
    parser.feed(source)
    parser.close()
    return parser.result(source_url)


def _decode_article_body(raw, content_type):
    match = _re.search(r"charset\s*=\s*[\"']?([^;\"'\s]+)", content_type or "", _re.I)
    encodings = [match.group(1)] if match else []
    encodings.extend(["utf-8", "gb18030", "big5"])
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def _fetch_article(url):
    _validate_article_url(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "DailyWorkbench/1.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    })
    opener = urllib.request.build_opener(_SafeArticleRedirectHandler())
    with opener.open(req, timeout=ARTICLE_TIMEOUT) as resp:
        final_url = resp.geturl()
        _validate_article_url(final_url)
        ctype = resp.headers.get("Content-Type", "")
        if ctype and "html" not in ctype.lower():
            raise ValueError("目标地址不是 HTML 文章页")
        raw = resp.read(MAX_ARTICLE_BYTES + 1)
        if len(raw) > MAX_ARTICLE_BYTES:
            raise ValueError("文章页面超过 4 MB，已停止读取")
    return _extract_article(_decode_article_body(raw, ctype), final_url)


