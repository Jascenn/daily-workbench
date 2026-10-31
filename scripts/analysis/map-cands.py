# -*- coding: utf-8 -*-
"""候选归并组：分层给出笔数/金额/覆盖率。只读。"""
import json, re, collections

d = json.load(open('data.json'))
E = [e for e in d['earnings'] if e.get('amount', 0) > 0]
def seg1(n): 
    s=(n or '').strip(); return s.split('|')[0].strip() or '(空)'
rows=[(seg1(e['note']), e['amount'], e['date'], e['note'], e['type'] or '') for e in E]
TV=sum(r[1] for r in rows); TN=len(rows)

# ---------- 层 A：同一实体的书写变体（高置信） ----------
A=[
 ('老徐（含 IDO老徐）',      r'^(老徐|IDO\s*老徐)'),
 ('所罗门（给对方干活）',    r'^(给\s*所罗门|所罗门)'),
 ('LionCC 交流群（群成员）', r'^OpenClaw\s*&\s*LionCC'),
 ('AI 代写',                 r'^AI\s*代写'),
 ('卓越运营奖金',            r'^卓越运营奖金'),
 ('淘金之路',                r'^淘金之路'),
 ('小报童（全部分成）',      r'小报童'),
 ('鹿鸣堂 / 飞巴体系',       r'(鹿鸣堂|飞巴)'),
]
# ---------- 层 B：疑似同一雇主/业务线（需确认） ----------
B=[
 ('LionCC 相关（官网/教程/远程配置/API）', r'(lioncc|Lion ?CC|镜像站|代充系统|小龙虾|hermes|Hermes|OPCAPI|API 相关|API相关信息|giffgaff|静态住宅|第三方openclaw|openclaw 使用|测试并完成|远程配置|远程登录|接入\s*Claude|Code 模型|code 模型)'),
 ('ChatGPT 相关维护',        r'^(ChatGPT|提取ChatGPT)'),
 ('AI 破局体系',             r'(AI\s*破局|避坑星球|避坑分享|知识星球)'),
 ('车车 / 菁华教练社群',     r'(车车|菁华教练)'),
 ('大冲私教体系',            r'大冲'),
 ('微信公众号/账号打理类',   r'^(梳理帐号|整理帐号|验证帐号|处理账号|账号相关)'),
]

def report(title, rules, taken):
    print('=== %s ===' % title)
    groups=collections.OrderedDict()
    for name,pat in rules:
        for seg,amt,date,note,typ in rows:
            if id(note) in taken: continue
            if re.search(pat, seg):
                g=groups.setdefault(name,{'n':0,'v':0.0,'segs':collections.Counter(),'rows':[]})
                g['n']+=1; g['v']+=amt; g['segs'][seg]+=1; g['rows'].append((seg,amt,note))
    cn=cv=0
    for name,g in sorted(groups.items(), key=lambda kv:-kv[1]['v']):
        print('  %-40s %3d 笔 %10.2f  (%5.2f%%)  写法 %d 种' % (name,g['n'],g['v'],g['v']/TV*100,len(g['segs'])))
        cn+=g['n']; cv+=g['v']
        for seg,amt,note in g['rows']:
            taken.add(id(note))
    print('  小计 %d 笔 %.2f (%.2f%%)' % (cn,cv,cv/TV*100))
    print()
    return cn,cv

taken=set()
an,av=report('层 A：同一实体的书写变体（高置信，建议直接归并）',A,taken)
bn,bv=report('层 B：疑似同一雇主/业务线（需你确认）',B,taken)
print('=== 汇总 ===')
print('层A  %3d 笔 %10.2f  %5.2f%%' % (an,av,av/TV*100))
print('层B  %3d 笔 %10.2f  %5.2f%%' % (bn,bv,bv/TV*100))
print('A+B  %3d 笔 %10.2f  %5.2f%%' % (an+bn,av+bv,(av+bv)/TV*100))
print('长尾 %3d 笔 %10.2f  %5.2f%%  （逐条人名/一事一记）' % (TN-an-bn,TV-av-bv,(TV-av-bv)/TV*100))
print()
print('=== 关键歧义点（需要你拍板） ===')
idio={'老徐','IDO老徐'}
print('1) IDO老徐(4笔325.70) 与 老徐-*(12笔267.78) 是不是同一个人？若同 → 老徐 16笔 593.48')
print('2) LionCC 层B 命中里，有些词（远程配置/测试并完成）语义是「工作内容」而 non 客户，')
print('   若它们其实都是给 LionCC 干活 → 归一个客户；若给别人干 → 就不能按词归。')
# 打印层B里靠关键词擦边的样本，便于人眼判断
print()
print('--- 层B 中靠「工作内容词」而非「客户名」命中的样本（最需要你判断） ---')
pat=re.compile(r'(测试并完成|远程配置|远程登录|镜像站|代充系统|giffgaff|静态住宅|API 相关)')
seen=0
for seg,amt,date,note,typ in sorted(rows,key=lambda x:-x[1]):
    if pat.search(seg) and seen<24:
        print('   %-46s %8.2f  %s' % (seg[:46],amt,date)); seen+=1
