# -*- coding: utf-8 -*-
"""核对页面里每一句结论的准确数字（与生成脚本同口径：层A优先、组间互斥）。"""
import json, re, collections
d=json.load(open('data.json'))
E=[e for e in d['earnings'] if e.get('amount',0)>0]
def seg1(n):
    s=(n or '').strip(); return s.split('|')[0].strip() or '(空)'
ROWS=[(seg1(e['note']), e['amount'], e['date']) for e in E]
TV=sum(r[1] for r in ROWS); TN=len(ROWS)
A=[('所罗门',r'^(给\s*所罗门|所罗门)'),('卓越运营奖金',r'^卓越运营奖金'),
   ('LionCC 交流群',r'^OpenClaw\s*&\s*LionCC'),('淘金之路',r'^淘金之路'),
   ('AI 代写',r'^AI\s*代写'),('老徐',r'^(老徐|IDO\s*老徐)'),
   ('鹿鸣堂/飞巴',r'(鹿鸣堂|飞巴)'),('小报童',r'小报童')]
B=[('LionCC 相关',r'(lioncc|Lion ?CC|镜像站|代充系统|小龙虾|hermes|Hermes|OPCAPI|API ?相关|giffgaff|静态住宅|第三方openclaw|openclaw ?使用|测试并完成|远程配置|远程登录|接入 ?Claude|Code ?模型|code ?模型)'),
   ('ChatGPT 相关',r'^(ChatGPT|提取ChatGPT)'),('AI 破局体系',r'(AI\s*破局|避坑星球|避坑分享|知识星球)'),
   ('车车/菁华教练',r'(车车|菁华教练)'),('大冲私教',r'大冲'),
   ('账号打理',r'^(梳理帐号|整理帐号|验证帐号|处理账号|账号相关)')]
def apply(rules,pool):
    out=[]
    for nm,pat in rules:
        rx=re.compile(pat); hit=[r for r in pool if rx.search(r[0])]; rest=[r for r in pool if not rx.search(r[0])]
        out.append((nm,hit)); pool=rest
    return out,pool
ag,pool=apply(A,ROWS); bg,tail=apply(B,pool)

def show(groups,tag):
    red=0
    for nm,rows in groups:
        if not rows: continue
        segs=len(set(r[0] for r in rows)); v=sum(r[1] for r in rows); n=len(rows)
        red+=segs-1
        ym=collections.Counter(r[2][:7] for r in rows)
        tiers=collections.Counter(int(round(r[1])) for r in rows).most_common(3)
        top_ym=ym.most_common(2)
        ds=sorted(r[2] for r in rows)
        print('[%s] %-16s %2d笔 %9.2f %5.2f%%  写法%2d种 (可减%d)  跨度%s~%s'%(
            tag,nm,n,v,v/TV*100,segs,segs-1,ds[0],ds[-1]))
        print('        金额档位: '+' / '.join('%d元×%d'%(k,c) for k,c in tiers))
        print('        最密月份: '+' / '.join('%s:%d笔'%(k,c) for k,c in top_ym))
    return red
rA=show(ag,'A'); rB=show(bg,'B')
print()
print('写法总数 %d' % len(set(r[0] for r in ROWS)))
print('层A 归并后写法数 = %d  (204 - %d)' % (204-rA, rA))
print('层A+B 归并后写法数 = %d' % (204-rA-rB))
print('层A 笔数/金额: %d / %.2f (%.1f%%)'%(sum(len(r) for _,r in ag),sum(sum(x[1] for x in r) for _,r in ag),sum(sum(x[1] for x in r) for _,r in ag)/TV*100))
print('层B 笔数/金额: %d / %.2f (%.1f%%)'%(sum(len(r) for _,r in bg),sum(sum(x[1] for x in r) for _,r in bg),sum(sum(x[1] for x in r) for _,r in bg)/TV*100))
print('长尾 笔数/金额: %d / %.2f (%.1f%%)  写法 %d 种'%(len(tail),sum(r[1] for r in tail),sum(r[1] for r in tail)/TV*100,len(set(r[0] for r in tail))))
# LionCC 组 2026-03 起占比
lc=[r for nm,r in bg if nm=='LionCC 相关' for r in r]
late=[r for r in ROWS if r[2]>='2026-03']
print('2026-03 起: 全部 %d笔 %.2f ; LionCC组 %d笔 %.2f → %.1f%%'%(
  len(late),sum(r[1] for r in late),len(lc and [r for r in lc if r[2]>='2026-03']),[1][0]*0+sum(r[1] for r in lc if r[2]>='2026-03'),
  100*sum(r[1] for r in lc if r[2]>='2026-03')/sum(r[1] for r in late)))
