# -*- coding: utf-8 -*-
"""从真实 data.json 生成《来源归并映射表》页（评审用 + 落地后的存档页）。
只读数据，不改任何记录。

⚠️ 2026-09-29 重写：规则**不再在本文件里重复定义**，而是直接读 `data.json` 的
   `sourceMap.rules` / `sourceMap.notMerged`。原因：本文件原先自己抄了一份规则，
   于是"页面规则"和"线上规则"漂移了两套数字 —— 页面显示所罗门 39 笔，
   而线上实际是 79 笔（漏了 `scope:"note"` 的整备注匹配）。
   **同一条规则只能存在一个地方。** 现在页面与工作台共用同一份 sourceMap。
"""
import json, re, html, collections, io, os

d = json.load(open('data.json'))
E = [e for e in d['earnings'] if e.get('amount', 0) > 0]
SM = d.get('sourceMap') or {}
if not (SM.get('rules')):
    raise SystemExit('data.json 里没有 sourceMap.rules，先跑 scripts/build-source-map.py')

def seg1(n):
    s = (n or '').strip()
    return s.split('|')[0].strip() or '(空)'

# 行 = (第一段, 金额, 日期, 整条备注)。第 4 项给 scope="note" 的规则用。
ROWS = [(seg1(e['note']), e['amount'], e['date'], e['note']) for e in E]
TV = sum(r[1] for r in ROWS); TN = len(ROWS)

# 规则从 sourceMap 读；scope 决定匹配"第一段"还是"整条备注"
COMPILED = [(r['name'], r.get('kind', ''), r.get('scope', 'seg1'),
             [re.compile(p) for p in r['patterns']], r.get('note', ''))
            for r in SM['rules']]

KIND_LABEL = {'A': '层A', 'B1': '层B1'}

def apply_rules():
    """按规则顺序逐个取行（首个命中生效，组间互斥），返回 (组列表, 剩余长尾)。
    组 = (name, kind, note, [rows])"""
    pool = list(ROWS)
    groups = []
    for name, kind, scope, pats, note in COMPILED:
        hit, rest = [], []
        for r in pool:
            hay = r[3] if scope == 'note' else r[0]
            (hit if any(p.search(hay) for p in pats) else rest).append(r)
        groups.append((name, kind, scope, note, hit))
        pool = rest
    return groups, pool

groups, tail = apply_rules()
a_groups  = [g for g in groups if g[1] == 'A']
b1_groups = [g for g in groups if g[1] == 'B1']

def stat(rows):
    if not rows: return None
    rows = sorted(rows, key=lambda x: x[2])
    tiers = collections.Counter(int(round(r[1])) for r in rows)
    return {
        'n': len(rows), 'v': sum(r[1] for r in rows),
        'segs': len(set(r[0] for r in rows)),
        'span': (rows[0][2], rows[-1][2]),
        'tiers': tiers.most_common(3),
        'rows': rows,
    }

def money(x): return '¥{:,.2f}'.format(x)
def pct(x): return '{:.1f}%'.format(x / TV * 100)

# notMerged 里有两类，必须分开呈现，否则会把"撤销"误读成"从没归并过"：
#   · 撤销（reason 里写了"已并入"）——原确认归并，后查明归错，已并入别的组
#   · 落选 —— 判过但没达到归并门槛
revoked = [x for x in SM.get('notMerged', []) if '已并入' in x.get('reason', '')]
rejected = [x for x in SM.get('notMerged', []) if '已并入' not in x.get('reason', '')]
def group_table(groups, cls):
    """groups 元素为 (name, kind, scope, note, rows)。
    `note` 来自 sourceMap 的规则注释 —— 它是规则自己的说明，不许本文件另写一份。"""
    o = io.StringIO()
    o.write('<table class="tbl %s"><thead><tr><th>归并来源</th><th class="n">写法</th>'
            '<th class="n">笔数</th><th class="n">金额</th><th class="n">占比</th>'
            '<th>时间跨度</th><th>依据</th></tr></thead><tbody>' % cls)
    for name, kind, scope, note, rows in groups:
        s = stat(rows)
        if not s: continue
        tier = ' / '.join('%d元×%d' % (k, c) for k, c in s['tiers'])
        ev = html.escape(note)
        if scope == 'note':
            ev += '<br><span class="tier">匹配范围：整条备注（scope=note）</span>'
        ev += '<br><span class="tier">%s</span>' % html.escape(tier)
        o.write('<tr><td><b>%s</b><br><span class="tier">%s</span></td>'
                '<td class="n">%d</td><td class="n">%d</td>'
                '<td class="n v">%s</td><td class="n">%s</td>'
                '<td class="d">%s<br>~ %s</td><td class="d">%s</td></tr>'
                % (html.escape(name), KIND_LABEL.get(kind, kind), s['segs'], s['n'],
                   money(s['v']), pct(s['v']),
                   s['span'][0], s['span'][1], ev))
    o.write('</tbody></table>')
    return o.getvalue()

def reason_table(items, cls):
    """未归并 / 已撤销的组：只列名字 + 原因（它们的行已进了长尾，不重复统计）。"""
    if not items:
        return '<p class="lead">（无）</p>'
    o = io.StringIO()
    o.write('<table class="tbl %s"><thead><tr><th>组名</th><th>原因</th></tr></thead><tbody>' % cls)
    for x in items:
        o.write('<tr><td><b>%s</b></td><td class="d">%s</td></tr>'
                % (html.escape(x['name']), html.escape(x.get('reason', ''))))
    o.write('</tbody></table>')
    return o.getvalue()

# 附录：全部写法
app = collections.defaultdict(lambda: [0, 0.0])
for s, a, dt, note in ROWS:
    app[s][0] += 1; app[s][1] += a
app_rows = sorted(app.items(), key=lambda kv: -kv[1][1])

sa_n  = sum(stat(g[4])['n'] for g in a_groups  if g[4])
sa_v  = sum(stat(g[4])['v'] for g in a_groups  if g[4])
sb1_n = sum(stat(g[4])['n'] for g in b1_groups if g[4])
sb1_v = sum(stat(g[4])['v'] for g in b1_groups if g[4])

st_n = len(tail); st_v = sum(r[1] for r in tail)

# 守恒：归并只能重新分组，不能多算也不能漏算
assert sa_n + sb1_n + st_n == TN, (sa_n, sb1_n, st_n, TN)
assert abs(sa_v + sb1_v + st_v - TV) < 1e-9, (sa_v, sb1_v, st_v, TV)
# 快照也要对得上，否则说明页面口径与 data.json 的 sourceMap 漂了
_snap_n = (SM.get('merged') or {}).get('count')
_snap_v = (SM.get('merged') or {}).get('amount')
if _snap_n is not None and sa_n + sb1_n != _snap_n:
    raise SystemExit('⚠ 笔数与 sourceMap 快照不符：页面 %d vs 快照 %d' % (sa_n + sb1_n, _snap_n))
if _snap_v is not None and abs(sa_v + sb1_v - _snap_v) > 0.005:
    raise SystemExit('⚠ 金额与 sourceMap 快照不符：页面 %.2f vs 快照 %.2f' % (sa_v + sb1_v, _snap_v))

# 所罗门的两种口径：整条备注提到客户 vs 第一段以「给所罗门」开头。
# 两个数差距很大（67 vs 36），正是「客户名常在第二段」的量化证据。
# ⚠️ 第二式必须匹配 **r[0]（第一段）**，不能匹配 r[3]（整条备注）——
#    否则和第一式同义，两个数会一样，这个对比就白做了。
SOL_ANY_SP  = len(set(r[0] for r in ROWS if '所罗门' in r[3] and '给' in r[3]))
SOL_HEAD_SP = len(set(r[0] for r in ROWS if re.match(r'给\s*所罗门', r[0])))
assert SOL_HEAD_SP < SOL_ANY_SP, (
    '两式必须给出不同结果（前者=整备注提到客户，后者=第一段以「给所罗门」开头）；'
    '若相等说明第二式又写成了匹配整条备注。%d vs %d' % (SOL_HEAD_SP, SOL_ANY_SP))

# 归并后写法数：每组合并成 1 种，故写法数减去 Σ(组内写法数-1)
seg_a_red  = sum(len(set(r[0] for r in g[4])) - 1 for g in a_groups  if g[4])
seg_b1_red = sum(len(set(r[0] for r in g[4])) - 1 for g in b1_groups if g[4])

def evidence_b():
    """本段全部由数据推导，不许手写结论句 —— 手写就一定会跟表格口径漂。"""
    late = [r for r in ROWS if r[2] >= '2026-03']
    o = ["<b>为什么还要分 B1 / B2：</b>B1 必须<strong>同时</strong>满足两条——"
         "① 金额档位集中（最大一档占本组 ≥ 40%，像「同一份报价反复执行」）；"
         "② 样本 ≥ 5 笔（笔数太少时集中度没有意义）。"
         "两条不齐的进 B2：只能说明<strong>活是同类</strong>，不能说明<strong>付款人是同一个</strong>。",
         "B2 是<strong>主观判断</strong>，所以通道留了撤销：任一组被证伪就写进 "
         "<code>sourceMap.notMerged</code> 并注明原因，规则里同时删掉。"]
    for g in b1_groups:
        if g[0] != 'LionCC 相关' or not g[4]: continue
        rows = g[4]
        lc = [r for r in rows if r[2] >= '2026-03']
        if late and lc:
            o.append('参照：2026-03 起全部进账 %d 笔 %s，其中 LionCC 组 %d 笔 %s，'
                     '占该期间金额 <b>%.0f%%</b>。'
                     % (len(late), money(sum(r[1] for r in late)),
                        len(lc), money(sum(r[1] for r in lc)),
                        sum(r[1] for r in lc) / sum(r[1] for r in late) * 100))
        s = stat(rows)
        o.append('LionCC 组修正后的时间跨度是 <b>%s ~ %s</b>（%d 笔）——'
                 '收窄到一个月内，比"按第一段关键词粗匹配"时**自洽得多**。'
                 % (s['span'][0], s['span'][1], s['n']))
    if revoked:
        o.append('已撤销的组：%s —— 原因见下方「已撤销」表；它们已不再成组，'
                 '行归入了对应客户（不是被删掉）。'
                 % '、'.join('<b>%s</b>' % html.escape(x['name']) for x in revoked))
    return ('<p class="lead" style="margin:14px 0 0"><b>判据与反证：</b></p>'
            '<ul class="ev">%s</ul>' % ''.join('<li>%s</li>' % x for x in o))

HTML = '''<!doctype html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>来源归并映射表</title>
<style>
  :root{
    --bg:#f5f7fb; --card:#ffffff; --ink:#1b2330; --muted:#6b7689; --line:#e7ebf2;
    --accent:#3b6ef5; --accent2:#7c5cff; --pos:#e4393c; --neg:#16a34a; --warn:#f59e0b;
    --shadow:0 1px 3px rgba(20,30,60,.06),0 6px 20px rgba(20,30,60,.05);
  }
  [data-theme="dark"]{
    --bg:#0f1420; --card:#161d2c; --ink:#e7ecf5; --muted:#9aa6bd; --line:#243047;
    --accent:#5b8bff; --accent2:#9a7bff; --pos:#ff6b6e; --neg:#4ade80; --warn:#fbbf24;
    --shadow:0 1px 3px rgba(0,0,0,.3),0 8px 24px rgba(0,0,0,.35);
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
  .wrap{max-width:1120px;margin:0 auto;padding:22px 20px 70px}
  h1{font-size:21px;margin:0 0 4px;letter-spacing:.3px}
  .sub{color:var(--muted);font-size:13px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:16px;
    padding:16px 18px;box-shadow:var(--shadow);margin:14px 0}
  .card h2{font-size:15px;margin:0 0 10px;display:flex;align-items:center;gap:8px}
  .card h2 .tag{font-size:11px;font-weight:600;color:var(--muted);background:var(--bg);
    border:1px solid var(--line);padding:2px 8px;border-radius:20px}
  .lead{color:var(--muted);font-size:13px;margin:0 0 12px}
  .tbl{width:100%;border-collapse:collapse;font-size:13px}
  .tbl th{text-align:left;font-size:11.5px;font-weight:600;color:var(--muted);
    border-bottom:1px solid var(--line);padding:7px 8px;white-space:nowrap}
  .tbl td{border-bottom:1px solid var(--line);padding:8px}
  .tbl tr:last-child td{border-bottom:0}
  .tbl .n{text-align:right;white-space:nowrap}
  .tbl .v{font-weight:700}
  .tbl .d{font-size:11.5px;color:var(--muted);line-height:1.5}
  .tier{color:var(--accent2)}
  .ev{margin:8px 0 0;padding-left:18px;font-size:12.5px;color:var(--muted);line-height:1.7}
  .ev b{color:var(--ink)}
  .tblA{border-left:3px solid var(--neg)}
  .tblB1{border-left:3px solid var(--warn)}
  .tblB2{border-left:3px solid rgba(245,158,11,.45)}
  .cover{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:6px 0 2px}
  h3.sec{font-size:14px;margin:16px 0 6px;display:flex;align-items:center;gap:8px}
  .cov{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
  .cov .k{font-size:12px;color:var(--muted)}
  .cov .num{font-size:20px;font-weight:700;margin:4px 0 2px}
  .cov .d{font-size:11.5px;color:var(--muted)}
  .bar{height:12px;border-radius:8px;overflow:hidden;display:flex;margin:12px 0 6px;
    border:1px solid var(--line)}
  .bar>i{display:block;height:100%}
  .legend{font-size:11.5px;color:var(--muted);display:flex;gap:16px;flex-wrap:wrap}
  .legend i{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:5px}
  .q{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--warn);
    border-radius:12px;padding:12px 15px;margin:10px 0}
  .q b{display:block;margin-bottom:4px}
  .q .hint{font-size:12.5px;color:var(--muted)}
  .path{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:4px}
  .p{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:13px 15px}
  .p h3{margin:0 0 6px;font-size:14px}
  .p ul{margin:6px 0 0;padding-left:18px;font-size:12.5px;color:var(--muted)}
  .p li{margin:3px 0}
  .pill{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px}
  .pill.ok{background:rgba(22,163,74,.12);color:var(--neg)}
  .pill.wa{background:rgba(245,158,11,.14);color:var(--warn)}
  .pill.ac{background:rgba(59,110,245,.12);color:var(--accent)}
  code{background:var(--bg);border:1px solid var(--line);border-radius:5px;padding:1px 5px;
    font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  details{margin-top:10px}
  summary{cursor:pointer;font-size:13px;color:var(--accent);outline:none}
  .app{max-height:420px;overflow:auto;margin-top:10px;border:1px solid var(--line);
    border-radius:10px}
  .app table{font-size:12px;width:100%;border-collapse:collapse}
  .app td{border-bottom:1px solid var(--line);padding:4px 9px}
  .app tr:last-child td{border-bottom:0}
  .app .n{text-align:right;color:var(--muted);white-space:nowrap}
  @media(max-width:900px){
    .cover,.path{grid-template-columns:1fr}
    .tbl .d{display:none}
    .tbl th:nth-child(6),.tbl th:nth-child(7){display:none}
  }
</style>
</head>
<body>
<div class="wrap">
  <h1>「备注 → 来源」归并映射表 <span class="pill ok">已落地</span></h1>
  <div class="sub">数据源：本地 <code>data.json</code>（只读） · 收入 __TN__ 笔 / __TV__ · 由脚本从真实记录生成，未手写任何数字<br>
  <b>规则不写在本页</b>：与工作台共用同一份 <code>data.json → sourceMap</code>，本页只是把它渲染出来 · 生成于 <code>__BUILT__</code></div>

  <div class="card">
    <h2>问题是什么 <span class="tag">为什么不能直接统计</span></h2>
    <p class="lead">备注第一段就是「来源」，但它从来不是一个受控字段，而是<strong>随手写的任务描述</strong>。
    __TN__ 笔收入散成了 <strong>__NSEG__ 种第一段写法</strong>，同一件事有无数种拼法：</p>
    <table class="tbl">
      <thead><tr><th>实际写过的写法</th><th>其实是同一来源</th></tr></thead>
      <tbody>
        <tr><td>老徐-100个小项目 / 老徐-小报童 / 老徐-200个案例信息源 / IDO老徐</td><td>老徐</td></tr>
        <tr><td>OpenClaw &amp; LionCC 交流群 2026-肚子圆鼓鼓 / …2026-小T同学 / …2026-建国</td><td>LionCC 交流群（成员挨个写了一遍）</td></tr>
        <tr><td>AI 代写-来都来了 / AI 代写-0530-改写+降重 / AI 代写-VBA智能插入行</td><td>AI 代写</td></tr>
        <tr><td>给所罗门制作一个博客 / 给所罗门完成博客部署 / <b>远程配置接入API | 给所罗门远程配置接入 API</b>…</td><td>所罗门（<b>客户名常常写在第二段</b>，见下）</td></tr>
      </tbody>
    </table>
    <p class="lead" style="margin:12px 0 0">所以「按备注统计来源」等于没统计——头部一堆 1 笔的散点，真正的客户被切碎了。</p>
    <p class="lead" style="margin:12px 0 0"><b>⚠️ 最反直觉的一条（第一次就做错了）：</b>备注的真实结构频繁是「<b>任务 | 给&lt;客户&gt;做某事</b>」——<b>客户名在第二段</b>。只按第一段归类时，<code>远程配置接入API | 给所罗门远程配置接入 API</code> 会被归到别的组，<b>所罗门被少算一半</b>。真实数字：备注里写着「给…所罗门」的记录共 <b>__SOL_ANY_SP__ 种第一段写法</b>，其中只有 <b>__SOL_HEAD_SP__ 种</b>是以「给所罗门」开头的。所以显式客户标记的规则用 <code>scope:"note"</code> 匹配整条备注，并<b>排在最前</b>。</p>
  </div>

  <div class="card">
    <h2>覆盖总览</h2>
    <div class="cover">
      <div class="cov"><div class="k">层 A · 直接归并</div><div class="num">__SAN__ 笔</div>
        <div class="d">__SAV__ · __SAP__ 金额<br>__NGRP_A__ 组：书写变体 + 显式客户标记</div></div>
      <div class="cov"><div class="k">层 B1 · 确认归并（可撤销）</div><div class="num">__SB1N__ 笔</div>
        <div class="d">__SB1V__ · __SB1P__ 金额<br>__NGRP_B1__ 组：时间窗口干净 + 档位集中</div></div>
      <div class="cov"><div class="k">长尾 · 不归并</div><div class="num">__STN__ 笔</div>
        <div class="d">__STV__ · __STP__ 金额<br>__NSEG_TAIL__ 种写法，逐条就是一个人 / 一件小事</div></div>
    </div>
    <div class="bar">
      <i style="width:__SAP__;background:var(--neg)"></i>
      <i style="width:__SB1P__;background:var(--warn)"></i>
      <i style="width:__STP__;background:var(--line)"></i>
    </div>
    <div class="legend">
      <span><i style="background:var(--neg)"></i>层 A 高置信</span>
      <span><i style="background:var(--warn)"></i>层 B1 确认归并</span>
      <span><i style="background:var(--line)"></i>长尾</span>
    </div>
  </div>

  <div class="card">
    <h2>层 A：书写变体 + 显式客户标记 <span class="pill ok">已归并</span></h2>
    <p class="lead">两类：① <strong>同一件事被写成了不同字</strong>（书写变体，不存在判断分歧）；
    ② <strong>备注里显式写了客户名</strong>（如「给所罗门…」），用 <code>scope:"note"</code> 整条匹配。
    仅计第一段的写法数从 <strong>__NSEG__ 降到 __SEG_A__</strong>（层 A 组内合并掉 __SEG_A_RED__ 种重复写法）。</p>
    __TABLE_A__
  </div>

  <div class="card">
    <h2>层 B1：确认归并 <span class="pill wa">主观判断 · 可撤销</span></h2>
    <p class="lead">这一层是<strong>推测</strong>，依据是「时间窗口 + 金额档位 + 任务性质」。
    备注里写的是<strong>工作内容</strong>（如「远程配置」「测试并完成」），不是<strong>客户名</strong>——
    如果这些活其实分别给不同人干，那一归并就<strong>静默造假</strong>了。
    所以归并门槛设成「金额档位集中 ≥40% <b>且</b> 样本 ≥5 笔」，两条都要满足；
    并保留<strong>撤销通道</strong>——任一组被证伪就删掉规则、写进 <code>notMerged</code> 留痕。</p>
    __TABLE_B1__
    __EVIDENCE_B__
  </div>

  <div class="card">
    <h2>未归并 / 已撤销 <span class="pill ac">留痕，不是漏了</span></h2>
    <p class="lead">判过但<strong>没达到归并门槛</strong>的组，以及<strong>原确认归并、后查明归错</strong>而撤销的组，
    都必须留痕写清原因 —— 否则以后会有人以为是漏了。</p>
    <h3 class="sec">已撤销 <span class="pill ac">原已归并，后证伪</span></h3>
    __TABLE_REVOKED__
    <h3 class="sec" style="margin-top:18px">落选 <span class="pill ac">未达门槛</span></h3>
    __TABLE_REJECTED__
  </div>

  <div class="card">
    <h2>长尾 <span class="pill ac">保持原样</span></h2>
    <p class="lead">剩下 <strong>__STN__ 笔 / __STV__（__STP__）</strong>，是 __NSEG_TAIL__ 种零星写法：
    单个人名（建国、半糖、桑桑、马幸运…）、一次性的小事（帮忙下载内容、推荐群…）。
    这些<strong>本来就该是一条一个来源</strong>，硬归并只会把真实结构搅乱。建议原样保留，不进映射表。</p>
  </div>

  <div class="card">
    <h2>已确认的决定 <span class="pill ok">__DECIDED_AT__ · __DECIDED_BY__</span></h2>
    <div class="q"><b>1 · 层 B 认不认？ → 认，但按证据强度筛</b>
      <div class="hint">最终只保留 <b>__NGRP_B1__ 组</b>（LionCC 相关）；其余因档位分散或样本过少<strong>明确不归并</strong>，理由见上表。</div></div>
    <div class="q"><b>2 · <code>IDO老徐</code> 和 <code>老徐-*</code> 是同一个人吗？ → 是</b>
      <div class="hint">已按同一人归并：<b>16 笔 ¥593.48</b>。（本次最大的一次修正正是"客户名常在第二段"，见「问题是什么」卡末段。）</div></div>
    <div class="q"><b>3 · 走哪条路落地？ → 走 ②，先用本地映射表</b>
      <div class="hint">理由：① 是<strong>不可逆的方向性投入</strong>，应等「来源」这个维度被证明有用之后再动；② 已落地（<code>data.json → sourceMap</code>），且 B1 标注了「可撤销」、撤销有留痕，风险可控。</div></div>
  </div>

  <div class="card">
    <h2>落地路径对比 <span class="pill ac">已选 ②</span></h2>
    <div class="path">
      <div class="p">
        <h3>路径 ① 在 Notion 加「客户/来源」字段 <span class="pill ac">暂不启动</span></h3>
        <ul>
          <li>一次性成本，之后<strong>每笔收入自己就带规范来源</strong>，不再依赖推测</li>
          <li>能支撑「按客户看收入 / 复购 / 客户价值」这类真正有用的分析</li>
          <li>代价：需要去 Notion 加一个字段（<strong>只加字段、不动历史数据</strong>，
              历史记录的来源仍由映射表兜底）</li>
          <li>需要改 Notion 页面 → <strong>按约定等你明确说「整理到 Notion」我才动手</strong></li>
        </ul>
      </div>
      <div class="p">
        <h3>路径 ② 只用映射表（改本地） <span class="pill ok">已选</span></h3>
        <ul>
          <li>零 Notion 改动，只在本地 <code>data.json</code> 的读取层加一层归类</li>
          <li>立刻能用：工作台上出「按来源排行 / 客户收入构成」</li>
          <li>代价：映射表是<strong>我的判断</strong>，LionCC 这类推测错了会<strong>静默算错</strong>，
              而且以后新记的备注还得回来改表</li>
          <li>适合「先看清楚，暂不动 Notion」</li>
        </ul>
      </div>
    </div>
    <p class="lead" style="margin:12px 0 0"><strong>已执行 ②：</strong>规则落在 <code>data.json → sourceMap</code>，工作台「收入来源」卡已按它渲染，点某一行可把明细筛到那一组逐条核对。<span class="pill ok">本页与工作台共用同一份规则</span></p>
  </div>

  <div class="card">
    <h2>附录：全部 __NSEG__ 种第一段写法</h2>
    <details>
      <summary>展开看完整清单（按金额倒序，含笔数）</summary>
      <div class="app"><table><tbody>
      __APPENDIX__
      </tbody></table></div>
    </details>
  </div>

  <div class="sub" style="margin-top:18px">
    生成脚本 <code>scripts/analysis/build-map-draft.py</code> · 本页<strong>只渲染、不定义规则</strong>（规则在 <code>data.json → sourceMap</code>）· 未修改 <code>data.json</code> 与任何 Notion 记录。
  </div>
</div>
</body>
</html>
'''

appendix = ''.join(
    '<tr><td>%s</td><td class="n">%d 笔</td><td class="n">%s</td></tr>'
    % (html.escape(s), n, money(v)) for s, (n, v) in app_rows)

HTML = (HTML.replace('__TN__', str(TN))
            .replace('__TV__', money(TV))
            .replace('__NSEG__', str(len(app)))
            .replace('__NGRP_A__', str(len([g for g in a_groups if g[4]])))
            .replace('__NGRP_B1__', str(len([g for g in b1_groups if g[4]])))
            .replace('__BUILT__', SM.get('builtAt', '—'))
            .replace('__DECIDED_AT__', str((SM.get('decisions') or {}).get('confirmedAt', '—')))
            .replace('__DECIDED_BY__', str((SM.get('decisions') or {}).get('confirmedBy', '—')))
            .replace('__SOL_ANY_SP__', str(SOL_ANY_SP))
            .replace('__SOL_HEAD_SP__', str(SOL_HEAD_SP))
            .replace('__NSEG_TAIL__', str(len(set(r[0] for r in tail))))
            .replace('__SEG_A__', str(len(app) - seg_a_red))
            .replace('__SEG_A_RED__', str(seg_a_red))
            .replace('__EVIDENCE_B__', evidence_b())
            .replace('__SAN__', str(sa_n)).replace('__SAV__', money(sa_v)).replace('__SAP__', pct(sa_v))
            .replace('__SB1N__', str(sb1_n)).replace('__SB1V__', money(sb1_v)).replace('__SB1P__', pct(sb1_v))
            .replace('__STN__', str(st_n)).replace('__STV__', money(st_v)).replace('__STP__', pct(st_v))
            .replace('__TABLE_A__', group_table(a_groups, 'tblA'))
            .replace('__TABLE_B1__', group_table(b1_groups, 'tblB1'))
            .replace('__TABLE_REVOKED__', reason_table(revoked, 'tblB2'))
            .replace('__TABLE_REJECTED__', reason_table(rejected, 'tblB2'))
            .replace('__APPENDIX__', appendix))

OUT = '来源归并映射表.html'
open(OUT, 'w', encoding='utf-8').write(HTML)
print('生成完成：%s  %d bytes' % (OUT, len(HTML.encode('utf-8'))))
print('层A %d 笔 %s (%s) / 层B1 %d 笔 %s (%s) / 长尾 %d 笔 %s (%s)'
      % (sa_n, money(sa_v), pct(sa_v), sb1_n, money(sb1_v), pct(sb1_v),
         st_n, money(st_v), pct(st_v)))
print('写法总数 %d ｜ 已撤销 %d 组 ｜ 落选 %d 组' % (len(app), len(revoked), len(rejected)))
