# -*- coding: utf-8 -*-
"""验证层B两大组是否是「同一雇主」：看时间聚簇 + 金额档位 + 月内占比。"""
import json, re, collections
d=json.load(open('data.json'))
E=[e for e in d['earnings'] if e.get('amount',0)>0]
def seg1(n):
    s=(n or '').strip(); return s.split('|')[0].strip() or '(空)'
ALL=[(seg1(e['note']), e['amount'], e['date']) for e in E]
TV=sum(x[1] for x in ALL)

LION=re.compile(r'(lioncc|Lion ?CC|镜像站|代充系统|小龙虾|hermes|Hermes|OPCAPI|API 相关|API相关信息|giffgaff|静态住宅|第三方openclaw|openclaw 使用|测试并完成|远程配置|远程登录|接入\s*Claude|Code 模型|code 模型)')
CKGPT=re.compile(r'^(ChatGPT|提取ChatGPT)')

def probe(name,pat):
    g=[x for x in ALL if pat.search(x[0])]
    if not g: return
    g.sort(key=lambda x:x[2])
    v=sum(x[1] for x in g)
    print('== %s : %d 笔  %.2f  (%.2f%%) =='%(name,len(g),v,v/TV*100))
    print('   时间跨度 %s ~ %s'%(g[0][2],g[-1][2]))
    ym=collections.Counter(x[2][:7] for x in g)
    print('   按月分布：'+'  '.join('%s:%d'%(k,ym[k]) for k in sorted(ym)))
    am=collections.Counter(round(x[1]) for x in g)
    top=am.most_common(8)
    print('   金额档位（取整后TOP）：'+'  '.join('%d元×%d'%(k,c) for k,c in top))
    # 该月里这些笔数占当月总收入比重
    print('   金额尾数 .88/.99/.66 占比：%.0f%%'%(
      100*sum(1 for x in g if round(x[1])%1==0 and str(int(round(x[1])))[-2:] in ('88','99','66') or abs(x[1]-round(x[1]))<0.005 and str(int(round(x[1])))[-2:] in ('88','99','66'))/len(g)))
    print()

probe('LionCC 相关（层B主组）',LION)
probe('ChatGPT 相关（层B次组）',CKGPT)

# 关键：2026-03 起，「LionCC 组」占全部进账的比重
print('== 2026 年 3 月及以后，LionCC 组 vs 全部进账 ==')
late=[x for x in ALL if x[2]>='2026-03']
liong=[x for x in late if LION.search(x[0])]
print('   期间共 %d 笔 %.2f ；其中 LionCC 组 %d 笔 %.2f → 占金额 %.1f%%'%(
   len(late),sum(x[1] for x in late),len(liong),sum(x[1] for x in liong),
   100*sum(x[1] for x in liong)/sum(x[1] for x in late)))
print()
print('== 未归入任何组的写法里，金额 TOP20（长尾头部） ==')
groups=[LION,CKGPT,re.compile(r'^(老徐|IDO\s*老徐)'),re.compile(r'^(给\s*所罗门|所罗门)'),
        re.compile(r'^OpenClaw\s*&\s*LionCC'),re.compile(r'^AI\s*代写'),re.compile(r'^卓越运营奖金'),
        re.compile(r'^淘金之路'),re.compile(r'小报童'),re.compile(r'(鹿鸣堂|飞巴)'),
        re.compile(r'(AI\s*破局|避坑星球|避坑分享|知识星球)'),re.compile(r'(车车|菁华教练)'),
        re.compile(r'大冲'),re.compile(r'^(梳理帐号|整理帐号|验证帐号|处理账号|账号相关)')]
agg=collections.defaultdict(lambda:[0,0.0])
for s,a,dt in ALL:
    if any(p.search(s) for p in groups): continue
    agg[s][0]+=1; agg[s][1]+=a
top=sorted(agg.items(),key=lambda kv:-kv[1][1])[:20]
for s,(n,v) in top: print('   %-44s %2d 笔 %9.2f'%(s[:44],n,v))
