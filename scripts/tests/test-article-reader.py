#!/usr/bin/env python3
"""资讯站内阅读的安全边界与正文提取测试（不访问真实网络）。"""
import importlib.util
import os
import socket
import urllib.error
import urllib.request
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("daily_workbench_app", os.path.join(ROOT, "app.py"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

passed = 0
failed = 0


def t(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ✓ " + name + (("  → " + detail) if detail else ""))
    else:
        failed += 1
        print("  ✗ " + name + (("  → " + detail) if detail else ""))


def rejected(url):
    try:
        app._validate_article_url(url)
        return False
    except ValueError:
        return True


print("被测：/api/article 的 URL 安全边界")
t("拒绝 javascript: 地址", rejected("javascript:alert(1)"))
t("拒绝 file: 地址", rejected("file:///etc/passwd"))
t("拒绝本机回环地址", rejected("http://127.0.0.1:8787/data.json"))
t("拒绝 IPv6 回环地址", rejected("http://[::1]/"))
t("拒绝私网地址", rejected("http://192.168.1.10/"))
t("拒绝云元数据地址", rejected("http://169.254.169.254/latest/meta-data/"))
t("拒绝本机域名", rejected("http://localhost:8787/"))
t("拒绝 .local 域名", rejected("http://printer.local/"))
t("拒绝带用户名的地址", rejected("https://user:pass@example.com/article"))

public_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
with mock.patch.object(app.socket, "getaddrinfo", return_value=public_dns):
    t("允许解析到公网地址的 HTTPS 文章",
      app._validate_article_url("https://example.com/post") == "https://example.com/post")

redirect_handler = app._SafeArticleRedirectHandler()
redirect_req = urllib.request.Request("https://example.com/post")
try:
    redirect_handler.redirect_request(
        redirect_req, None, 302, "Found", {}, "http://127.0.0.1/private")
    private_redirect_blocked = False
except (ValueError, urllib.error.URLError):
    private_redirect_blocked = True
t("重定向到本机地址时会在跟随前被拒绝", private_redirect_blocked)


print("\n被测：第三方 HTML 的正文提取")
fixture = """<!doctype html>
<html>
<head>
  <title>页面标题</title>
  <meta property="og:title" content="一篇可读的文章">
  <meta property="og:site_name" content="测试站点">
  <meta property="article:published_time" content="2026-10-29T08:00:00+08:00">
</head>
<body>
  <nav>首页 导航 不应出现</nav>
  <article class="post-content">
    <h1>文章里的主标题</h1>
    <p>这是第一段正文，应该被保留下来。</p>
    <script>alert('不应出现')</script>
    <h2>一个小标题</h2>
    <p>这是第二段正文，也应该被保留下来。</p>
    <blockquote>这是一段引用内容。</blockquote>
  </article>
  <footer>版权页脚不应出现</footer>
</body>
</html>"""
article = app._extract_article(fixture, "https://example.com/post")
text = "\n".join(x["text"] for x in article["blocks"])
t("优先使用 og:title 作为阅读标题", article["title"] == "一篇可读的文章", article["title"])
t("保留站点名与发布时间",
  article["site"] == "测试站点" and article["publishedAt"].startswith("2026-10-29"))
t("正文段落、小标题和引用都被提取",
  any(x["type"] == "p" and "第一段正文" in x["text"] for x in article["blocks"])
  and any(x["type"] == "h2" and x["text"] == "一个小标题" for x in article["blocks"])
  and any(x["type"] == "quote" and "引用内容" in x["text"] for x in article["blocks"]))
t("script 内容不进入正文", "alert(" not in text and "不应出现" not in text)
t("nav 与 footer 不进入正文", "首页 导航" not in text and "版权页脚" not in text)
t("正文块只包含白名单类型",
  all(x["type"] in ("p", "h2", "h3", "quote", "li", "pre", "image", "hr")
      for x in article["blocks"]))

wordpress_fixture = """<!doctype html>
<html><head>
  <meta property="og:title" content="带相关文章的页面">
  <meta property="og:site_name" content="WordPress 站点">
</head><body>
  <article class="article">
    <ul class="related-posts">
      <li>相关文章一，不应进入正文</li>
      <li>相关文章二，不应进入正文</li>
    </ul>
    <div class="post-single-content box mark-links entry-content">
      <p>这才是文章正文的第一段。</p>
      <ul>
        <li>正文自己的列表项应该保留。</li>
      </ul>
      <h2>正文小标题</h2>
      <p>正文最后一段。</p>
    </div>
    <div class="related-posts">
      <p>页尾相关推荐，也不应进入正文。</p>
    </div>
  </article>
</body></html>"""
wordpress_article = app._extract_article(wordpress_fixture, "https://example.com/wordpress")
wordpress_text = "\n".join(x["text"] for x in wordpress_article["blocks"])
t("优先选择最具体的正文容器，不把外层相关文章一起抓入",
  wordpress_article["blocks"][0]["text"] == "这才是文章正文的第一段。"
  and "相关文章一" not in wordpress_text
  and "页尾相关推荐" not in wordpress_text,
  " / ".join(x["text"] for x in wordpress_article["blocks"][:3]))
t("具体正文容器内的列表仍会保留",
  any(x["type"] == "li" and "正文自己的列表项" in x["text"]
      for x in wordpress_article["blocks"]))

appinn_fixture = """<!doctype html>
<html><head>
  <meta property="og:title" content="正文里带追踪组件的页面">
  <meta property="og:site_name" content="Appinn">
</head><body>
  <article class="post">
    <div class="post-single-content box mark-links entry-content">
      <section class="appinn-track" aria-label="测试事件追踪报道">
        <ol>
          <li>2026.10.29 当前文章追踪列表，不应进入正文</li>
          <li>2026.10.27 历史追踪报道，不应进入正文</li>
        </ol>
      </section>
      <p>距离事件曝光已经过去十二天，这才是正文第一段。</p>
      <ul>
        <li>正文自己的普通列表仍应保留。</li>
      </ul>
      <div class="appinn-article-like">
        <p>点赞组件文字不应进入正文。</p>
      </div>
      <p>这是正文最后一段。</p>
    </div>
  </article>
</body></html>"""
appinn_article = app._extract_article(appinn_fixture, "https://www.appinn.com/example")
appinn_text = "\n".join(x["text"] for x in appinn_article["blocks"])
t("正文容器内的追踪报道和点赞组件会被跳过",
  appinn_article["blocks"][0]["text"] == "距离事件曝光已经过去十二天，这才是正文第一段。"
  and "当前文章追踪列表" not in appinn_text
  and "历史追踪报道" not in appinn_text
  and "点赞组件文字" not in appinn_text,
  " / ".join(x["text"] for x in appinn_article["blocks"][:3]))
t("跳过站点组件后，正文自己的普通列表仍会保留",
  any(x["type"] == "li" and "正文自己的普通列表仍应保留" in x["text"]
      for x in appinn_article["blocks"]))

rich_fixture = """<!doctype html>
<html><head>
  <meta property="og:title" content="带富文本格式的文章">
  <meta property="og:site_name" content="富文本测试站">
</head><body>
  <article class="post-content">
    <figure>
      <img data-src="/images/cover.webp" alt="文章封面">
      <figcaption>封面图说明，应该和图片一起保留。</figcaption>
    </figure>
    <p>查看<a href="/docs/guide">相对链接文档</a>，以及
      <strong>加粗说明</strong>、<em>斜体说明</em>和
      <code>inline_code()</code>。</p>
    <p><a href="javascript:alert(1)">危险链接</a>
      <img src="data:image/svg+xml,<svg onload=alert(1)>"></p>
    <ol>
      <li>有序列表第一项</li>
      <li>有序列表第二项</li>
    </ol>
    <ul>
      <li>无序列表项目</li>
    </ul>
    <pre><code class="language-js">const value = "&lt;script&gt;";
console.log(value);</code></pre>
    <hr>
  </article>
</body></html>"""
rich_article = app._extract_article(rich_fixture, "https://example.com/posts/rich")
rich_blocks = rich_article["blocks"]
rich_text = "\n".join(x["text"] for x in rich_blocks)
image_block = next((x for x in rich_blocks if x["type"] == "image"), None)
rich_paragraph = next((x for x in rich_blocks if x["type"] == "p"), None)
code_block = next((x for x in rich_blocks if x["type"] == "pre"), None)
ordered_items = [x for x in rich_blocks if x["type"] == "li" and x.get("ordered")]
plain_items = [x for x in rich_blocks if x["type"] == "li" and not x.get("ordered")]
link_segments = [s for b in rich_blocks for s in b.get("segments", []) if s["type"] == "link"]
t("lazy-load 图片会绝对化并保留 alt 与图注",
  image_block and image_block["src"] == "https://example.com/images/cover.webp"
  and image_block["alt"] == "文章封面"
  and image_block["caption"] == "封面图说明，应该和图片一起保留。",
  image_block and image_block["src"] + " / " + image_block["caption"])
t("段落会保留链接、粗体、斜体和行内代码片段",
  rich_paragraph and [s["type"] for s in rich_paragraph["segments"]]
    == ["text", "link", "text", "strong", "text", "em", "text", "code", "text"]
  and link_segments and link_segments[0]["href"] == "https://example.com/docs/guide",
  rich_paragraph and "/".join(s["type"] for s in rich_paragraph["segments"]))
t("javascript: 链接与 data: 图片会被丢弃",
  all(s.get("href", "").startswith(("http://", "https://", "mailto:"))
      for s in link_segments)
  and "javascript:" not in rich_text
  and all(x.get("src", "") != "data:image/svg+xml,<svg onload=alert(1)>"
          for x in rich_blocks),
  "危险链接已转成纯文本；data URI 图片未进入结构化块")
t("有序列表保留序号，无序列表保留项目符号",
  [x["marker"] for x in ordered_items] == ["1.", "2."]
  and len(plain_items) == 1 and plain_items[0]["marker"] == "•",
  "有序 %s / 无序 %s" % ([x["marker"] for x in ordered_items],
                      [x["marker"] for x in plain_items]))
t("代码块保留换行、HTML 实体与语言标识",
  code_block and code_block["language"] == "js"
  and code_block["text"] == 'const value = "<script>";\nconsole.log(value);',
  code_block and repr(code_block["text"]))
t("正文分隔线进入结构化块", any(x["type"] == "hr" for x in rich_blocks))

print("\n结果：%d 通过 / %d 失败" % (passed, failed))
raise SystemExit(0 if failed == 0 else 1)
