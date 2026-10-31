# -*- coding: utf-8 -*-
"""生成「备注首段 -> 建议归并来源」的映射表草案。只读，不写任何数据。"""
import json, re, collections

d = json.load(open('data.json'))
E = [e for e in d['earnings'] if e.get('amount', 0) > 0]

def first_seg(note):
    s = (note or '').strip()
    if not s:
        return '(空)'
    return s.split('|')[0].strip() or '(空)'

rows = []
for e in E:
    rows.append((first_seg(e['note']), e['amount'], e['date'], e.get('type') or '', e['note']))

agg = collections.OrderedDict()
for seg, amt, date, typ, note in rows:
    o = agg.setdefault(seg, {'n': 0, 'v': 0.0, 'first': date, 'last': date, 'types': collections.Counter()})
    o['n'] += 1
    o['v'] += amt
    o['first'] = min(o['first'], date)
    o['last'] = max(o['last'], date)
    o['types'][typ] += 1

total_v = sum(o['v'] for o in agg.values())
total_n = sum(o['n'] for o in agg.values())
print('=== 基准 ===')
print('收入笔数 %d  金额 %.2f  写法种数 %d' % (total_n, total_v, len(agg)))
print()

# ---------- 归并规则（草案） ----------
RULES = [
    ('老徐',            r'^老徐'),
    ('LionCC 交流群',   r'^OpenClaw\s*&\s*LionCC'),
    ('AI 代写',         r'^AI\s*代写'),
    ('所罗门',          r'^给?\s*所罗门'),
    ('鹿鸣堂（飞巴体系）', r'^鹿鸣堂'),
    ('淘金之路',        r'^淘金之路'),
    ('卓越运营奖金',    r'^卓越运营奖金'),
]

def classify(seg):
    for name, pat in RULES:
        if re.search(pat, seg):
            return name
    return None

grouped = collections.OrderedDict()
unmatched = []
for seg, o in sorted(agg.items(), key=lambda kv: -kv[1]['v']):
    g = classify(seg)
    if g:
        t = grouped.setdefault(g, {'n': 0, 'v': 0.0, 'segs': []})
        t['n'] += o['n']; t['v'] += o['v']
        t['segs'].append((seg, o['n'], o['v']))
    else:
        unmatched.append((seg, o['n'], o['v'], o['first'], o['last']))

print('=== 一、可明确归并的组（规则命中） ===')
gn = gv = 0
for g, t in sorted(grouped.items(), key=lambda kv: -kv[1]['v']):
    print('[%s]  %d 笔  %.2f  <- 合并了 %d 种写法' % (g, t['n'], t['v'], len(t['segs'])))
    for seg, n, v in sorted(t['segs'], key=lambda x: -x[2])[:6]:
        print('      %-38s %2d 笔 %9.2f' % (seg[:38], n, v))
    if len(t['segs']) > 6:
        print('      ... 另有 %d 种写法' % (len(t['segs']) - 6))
    gn += t['n']; gv += t['v']
print('小计：%d 笔  %.2f  (%.1f%% 金额)' % (gn, gv, gv / total_v * 100))
print()

print('=== 二、长尾（未命中规则，逐条就是一个人名/一件事） ===')
print('共 %d 种写法  %d 笔  %.2f' % (len(unmatched),
      sum(x[1] for x in unmatched), sum(x[2] for x in unmatched)))
for seg, n, v, f, l in unmatched[:60]:
    print('  %-40s %2d 笔 %9.2f  %s ~ %s' % (seg[:40], n, v, f, l))
if len(unmatched) > 60:
    print('  ... 另有 %d 种' % (len(unmatched) - 60))
    for seg, n, v, f, l in unmatched[60:]:
        print('  %-40s %2d 笔 %9.2f  %s ~ %s' % (seg[:40], n, v, f, l))
