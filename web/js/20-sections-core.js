/* ---------- 功能类型区（资讯 / 写作 / 复盘） ----------
   数据来自 /sections.json：一份**只读派生快照**，由 scripts/sync/sections.py 从 Obsidian 插件
   与 Notion 项目库汇总生成。页面从不写它（写回只有记账那条 data.json 路径）。
   离线时用 make-local.py 内嵌进 localStorage["wb_sections"] 的那份。

   为什么不在页面里现算「距今几天」：这些数字要参与"哪几个源已经死了"这类判断，
   口径必须只有一份 —— `days` 由上游预计算。页面算第二遍 = 两套口径迟早不一致，
   而这正是这一天反复出现的缺陷类型（同一件事两处实现，然后只有一处被改）。 */
const SECTIONS_API="/sections.json";
const SOCIAL_API="/social.json";
const WEB_PRESENCE_API="/web-presence.json";
// 微信只读快照。与 social.json 同一套：服务端令牌门 + 读不到就退本地内嵌。
const WECHAT_API="/wechat.json";
// 知识星球日报只读快照（24h 滚动窗口）。同样走令牌门 + 本地内嵌兜底。
const ZSXQ_DAILY_API="/zsxq-daily.json";
const TYPES=[["ledger","总览"],["project","项目"],["client","客户"],["wechat","微信"],["time","时间"],
            ["news","资讯"],["writing","写作"],["content","自媒体运营"],["review","复盘"]];
/* 二级页面：只挂在某个顶级页下面，不占顶部标签位。
   zsxq（知识星球）是「自媒体运营」的下层菜单，入口在那一屏里。 */
const SUBPAGES={zsxq:{parent:"content",label:"知识星球"}};
/* 条目的三档显示范围。记账区早就用「最近 12 笔 / 看全部」解决过同一件事，
   资讯区第一版却直接铺了全部 116 条 —— 真机量出来屏高 7855px（记账才 5817px），
   要滚十几屏才到底。**同一个问题不要两套解法**：这里沿用账本的分页思路，
   只是档位由"是否展开"改成三选一（近期窗口本身有 116 条，两档不够）。
   注意这是**显示上限**，不是数据口径 —— 所以留在页面里，不进 sections.json。 */
const NEWS_PAGE=20;
// 只存档位键。**档位名与条数都在 renderNews 里拼**，不要在数组里再写一份带数字的标签 ——
// 那样就成了"数字写两遍"（第一版就是这么打出「最近 20  20」的，而且断言只查"有没有数字"，
// 照样全绿）。窗口天数用上游的 s.windowDays，也不在这里写死 21。
const NEWS_SCOPES=["recent","window","all"];
let sections=null, secErr="", secNote="", newsScope="recent";
let newsFeedF="", newsGroupF="", newsUnreadF=false, newsQueryF="";
let reviewQueryF="", reviewTypeF="";
let social=null, socialErr="", socialNote="";
// 微信只读快照。wechatErr 与 socialErr 分开：两个快照各自可能缺，页面要分别报。
let wechat=null, wechatErr="", wechatNote="", wechatLoading=null;
/* 「忽略」是**本浏览器的显示偏好**，不是数据：不进 wechat.json，也不改微信。
   为什么需要它：脚本只按结构过滤机器号（公众号 / 企业微信），真人发来的推广
   就留着 —— 判断"这条营销不重要"得读正文，那是人的判断，不该由脚本替他做。
   但列表天天挂着招行申卡也确实是噪音，所以给一个手动出口，而不是偷偷猜。 */
function wxMuted(){ try{ return JSON.parse(localStorage.getItem("wb_wx_muted")||"[]"); }catch(e){ return []; } }
function wxMutedSet(v){ try{ localStorage.setItem("wb_wx_muted",JSON.stringify(v)); }catch(e){} }
function wxToggleMute(id){
  if(!id) return;
  const list=wxMuted(), i=list.indexOf(id);
  if(i>=0) list.splice(i,1); else list.push(id);
  wxMutedSet(list);
  renderWechat();
}
function wxUnmuteAll(){ wxMutedSet([]); renderWechat(); }
/* 访客模式的示例快照：只给"页面能转起来"的最小结构，全部为虚构示例，
   不含任何真实账号、收益、笔记标题。结构与线上快照同形，渲染函数
   无需分支就能吃 —— 资讯/写作/复盘列表、统计、来源筛选全部可交互。 */
/* 访客示例的日期工具：全部相对“今天”（上海时区）生成。写死日期的示例
   会随真实时间推移变空——“本日净额 ¥0”、近 21 天没条目都是这么来的。
   相对生成后任何一天打开，收益、目标带、专注趋势、资讯窗口都是满的。 */
const gDateAgo=n=>focusDateStr(Date.now()-n*86400000);
const gIsoAgo=n=>new Date(Date.now()-n*86400000).toISOString();
const gMonthAgo=n=>{const d=new Date();d.setDate(1);d.setMonth(d.getMonth()-n);
  return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0");};
const GUEST_SECTIONS=(()=>{
  /* 示例快照同样相对“今天”生成：6 个源、14 条近期条目全部落在近 21 天窗口里，
     无论哪天打开，“数据新鲜度 / 未读 / 来源健康”都是满的演示状态。 */
  const feedDefs=[
    ["示例科技周刊","产品与工具",24,1],["示例设计博客","设计与审美",18,2],
    ["示例 AI 日报","AI 与技术",21,1],["示例独立开发笔记","产品与工具",14,3],
    ["示例前端观察","设计与审美",12,5],["示例效率工具箱","AI 与技术",15,4]];
  const groups=["产品与工具","设计与审美","AI 与技术"];
  /* [天数前, 标题, 源序号, 已读, 摘要] —— 14 条全部在近 21 天，7 条未读。 */
  const itemDefs=[
    [1,"示例：把静态站点迁移到边缘渲染的完整清单",0,false,"示例摘要：从构建产物、缓存策略到回源规则，一条条过。"],
    [1,"示例：边缘函数的冷启动实测",2,true,"示例摘要：三家厂商的预热策略差在哪。"],
    [2,"示例：一套克制的中文字体排印方案",1,false,"示例摘要：字号阶、行高与标点悬挂的取舍。"],
    [3,"示例：订阅源健康检查怎么做才不吵",3,false,"示例摘要：停更判定、失败重试与静默降级。"],
    [4,"示例：本地模型的量化选择速查",2,true,"示例摘要：显存、速度与质量的三角权衡。"],
    [5,"示例：给个人站点做一个极简统计页",4,false,"示例摘要：不引入第三方统计的三种思路。"],
    [7,"示例：自动化脚本的幂等设计",0,true,"示例摘要：重复执行不产生副作用的三种写法。"],
    [8,"示例：标注系统的小步迭代记录",1,true,"示例摘要：先解决最常出错的那 20%。"],
    [9,"示例：用一份 YAML 管理所有定时任务",5,false,"示例摘要：声明式配置与失败告警的配合。"],
    [11,"示例：个人知识库的三年使用复盘",1,true,"示例摘要：收集、整理、输出三层的比例变化。"],
    [12,"示例：轻量监控告警的阈值设定",2,false,"示例摘要：少而准的告警比多而噪的更有用。"],
    [14,"示例：把命令行工具打包成双击应用",3,true,"示例摘要：从 shell 包装到原生菜单的取舍。"],
    [16,"示例：静态站点的构建缓存命中优化",4,true,"示例摘要：从 90 秒到 12 秒的改动清单。"],
    [18,"示例：为旧项目补一套最小回归测试",5,false,"示例摘要：只测会坏的关键路径。"]];
  const items=itemDefs.map(([d,t,si,read,sum],i)=>({
    id:"g"+(i+1),title:t,link:"#",source:feedDefs[si][0],published:gIsoAgo(d),days:d,read:read,summary:sum}));
  const bookChapters=[
    {name:"为什么要做每日工作台",written:true,chars:2200,summary:"示例摘要：把目标、数据与行动放在同一屏。"},
    {name:"先看三件事，再看全部指标",written:true,chars:1900,summary:"示例摘要：行动优先的证据设计。"},
    {name:"用月度目标驱动收入节奏",written:true,chars:2600,summary:"示例摘要：把缺口翻译成可执行动作。"},
    {name:"经营数据体检",written:false,chars:0,summary:""},
    {name:"自媒体与内容信号",written:false,chars:0,summary:""},
    {name:"知识库联动",written:false,chars:0,summary:""}];
  const manualChapters=[
    {name:"选题从哪里来",written:true,chars:1400,summary:"示例摘要：评论区、搜索词和自己的踩坑。"},
    {name:"开头三句决定完读率",written:true,chars:1200,summary:"示例摘要：钩子、冲突、承诺。"},
    {name:"把一篇拆成三条",written:true,chars:1600,summary:"示例摘要：长文的三段式复用。"},
    {name:"发布前的人工核验",written:true,chars:900,summary:"示例摘要：回执、版本、链接、封面。"},
    {name:"数据反馈怎么读",written:true,chars:1300,summary:"示例摘要：高互动不等于高转化。"},
    {name:"案例：一次完整选题流程",written:false,chars:0,summary:""},
    {name:"长期栏目的节奏管理",written:false,chars:0,summary:""},
    {name:"从 0 到 1 的冷启动清单",written:false,chars:0,summary:""}];
  return {version:1,generatedAt:new Date().toISOString(),meta:{guest:true},sections:[
    {id:"news",name:"资讯",freshness:{latest:gIsoAgo(1),days:1,threshold:3,stale:false,basis:"示例更新时间",empty:false},
     stats:[{k:"条目总数",v:"104",hint:"示例数据"},{k:"近 21 天",v:"14 条",hint:"示例数据"},{k:"未读（近期）",v:"7 / 14",hint:"示例数据"},{k:"订阅源",v:"6 个",hint:"示例数据"},{k:"疑似停更源",v:"0 个",hint:"示例数据"}],
     windowDays:21,
     feeds:feedDefs.map(([name,group,count,days])=>({name:name,group:group,url:"#",count:count,latest:gIsoAgo(days),days:days,dead:false})),
     groups:groups.map(name=>({name:name,count:feedDefs.filter(f=>f[1]===name).reduce((s,f)=>s+f[2],0)})),
     items:items,
     note:"访客模式：以上为示例数据，不来自真实订阅。",
     dataPolicy:"示例数据仅用于演示界面。"},
    {id:"writing",name:"写作",freshness:{latest:gIsoAgo(1),days:1,threshold:45,stale:false,basis:"示例更新时间",empty:false},
     stats:[{k:"原创作品",v:"2 部",hint:"示例数据"},{k:"章节进度",v:"8 / 20",hint:"示例数据"},{k:"正文字数",v:"28,200",hint:"示例数据"},{k:"客户内容",v:"5 项",hint:"示例数据"},{k:"最近更新",v:"1 天前",hint:"示例数据"}],
     books:[
      {title:"示例：个人工作台指南",genre:"原创",chapterCount:12,writtenCount:3,chars:18400,target:{min:20000,max:50000},
       docs:[{title:"示例：栏目大纲",chars:1800}],drafts:[{title:"示例：投稿初稿",chars:1200}],
       chapters:bookChapters},
      {title:"示例：公众号写作手册",genre:"原创",chapterCount:8,writtenCount:5,chars:9800,target:{min:8000,max:20000},
       docs:[{title:"示例：选题池",chars:900}],drafts:[{title:"示例：冷启动清单草稿",chars:700}],
       chapters:manualChapters}],
     clients:[
      {title:"示例：产品页文案改写",status:"进行中",due:gDateAgo(-6)},
      {title:"示例：工具站落地页诊断",status:"进行中",due:gDateAgo(-10)},
      {title:"示例：季度内容复盘",status:"已完成",due:gDateAgo(8)},
      {title:"示例：专栏代运营方案",status:"进行中",due:gDateAgo(-14)},
      {title:"示例：官网案例页润色",status:"已完成",due:gDateAgo(20)}],
     note:"访客模式：以上为示例写作数据。",dataPolicy:"示例数据仅用于演示界面。"},
    {id:"review",name:"复盘",freshness:{latest:gIsoAgo(2),days:2,threshold:30,stale:false,basis:"示例更新时间",empty:false},
     stats:[{k:"篇数",v:"10 篇",hint:"示例数据"},{k:"涉及类型",v:"6 类",hint:"示例数据"},{k:"品类数",v:"10 个",hint:"示例数据"},{k:"标注状态",v:"9 / 10",hint:"示例数据"}],
     items:[
      {title:"示例：一次跨时区协作的踩坑与修复",date:gIsoAgo(2),days:2,chars:1800,type:"调试复盘",productType:"协作工具",products:["示例A"],status:"已解决",modified:null},
      {title:"示例：静态站点构建时间从 90s 到 12s",date:gIsoAgo(6),days:6,chars:2400,type:"性能优化",productType:"构建工具",products:["示例B"],status:"已完成",modified:null},
      {title:"示例：家庭网络的 DNS 分流实践",date:gIsoAgo(9),days:9,chars:1500,type:"调试复盘",productType:"网络",products:["示例C"],status:"进行中",modified:null},
      {title:"示例：把日志从 4GB 精简到 200MB",date:gIsoAgo(14),days:14,chars:2100,type:"运维",productType:"日志系统",products:["示例D"],status:"已完成",modified:null},
      {title:"示例：给老项目补上自动化测试",date:gIsoAgo(19),days:19,chars:1900,type:"工程实践",productType:"测试",products:["示例E"],status:"进行中",modified:null},
      {title:"示例：订阅源抓取失败的一次排查",date:gIsoAgo(24),days:24,chars:1700,type:"调试复盘",productType:"资讯管道",products:["示例F"],status:"已解决",modified:null},
      {title:"示例：内容排期自动化的第一版",date:gIsoAgo(30),days:30,chars:2000,type:"自动化",productType:"排期系统",products:["示例G"],status:"已完成",modified:null},
      {title:"示例：一次失败的迁移与回滚",date:gIsoAgo(37),days:37,chars:1600,type:"复盘",productType:"数据库",products:["示例H"],status:"未解决",modified:null},
      {title:"示例：把周报改成模板加摘要",date:gIsoAgo(44),days:44,chars:1400,type:"流程",productType:"复盘系统",products:["示例I"],status:"已完成",modified:null},
      {title:"示例：季度目标为什么差了 20%",date:gIsoAgo(52),days:52,chars:2300,type:"经营复盘",productType:"目标管理",products:["示例J"],status:"进行中",modified:null}],
     note:"访客模式：以上为示例复盘，不来自真实笔记库。",
     dataPolicy:"示例数据仅用于演示界面。"}
  ]};
})();
const GUEST_SOCIAL=(()=>{
  /* [天数前, 平台, 标题, 阅读, 点赞, 评论] —— 6 条内容铺开在最近三周，
     周报的 best/worst 都指向真实存在的示例帖子。 */
  const postDefs=[
    [1,"X","示例：一次工具测评的完整流程",4200,132,18],
    [3,"小红书","示例：桌面改造的三个决定",9800,540,64],
    [5,"公众号","示例：为什么我把工作台开源了",3100,86,24],
    [8,"X","示例：把自动化脚本开源后的反馈整理",2600,88,11],
    [13,"小红书","示例：一套克制的工作台配色",7400,412,53],
    [19,"公众号","示例：我的 Notion 同步管线",2400,64,9]];
  const posts=postDefs.map(([d,p,t,r,l,c],i)=>({
    id:"p"+(i+1),platform:p,title:t,url:"#",publishedAt:gDateAgo(d),
    metrics:{reads:r,likes:l,comments:c}}));
  return {version:1,generatedAt:new Date().toISOString(),guest:true,
  sources:[{name:"示例快照",asOf:gDateAgo(1),records:18}],
  accounts:[
    {platform:"X",connectionState:"ok",freshness:"fresh",asOf:"2026-10-10",dashboardUrl:"#",metrics:{followers:1230,posts:450,recentPublicViews:18200}},
    {platform:"小红书",connectionState:"ok",freshness:"fresh",asOf:"2026-10-09",dashboardUrl:"#",metrics:{followers:860,posts:41,interactions:3200}},
    {platform:"公众号",connectionState:"ok",freshness:"partial",asOf:"2026-10-08",dashboardUrl:"#",accountType:"订阅号",metrics:{followers:2100,materialArticles:24,publishedArticles:18}},
    {platform:"知识星球",connectionState:"ok",freshness:"stale",asOf:"2026-09-20",dashboardUrl:"#",metrics:{posts:320,postsInLast30ExportDays:18,readsSample:510,likes:96,comments:41}}
  ],
  todayPlan:[{id:"gx",platform:"X",url:"#",actionUrl:"#",defaultStatus:"unknown"},
             {id:"gxhs",platform:"小红书",url:"#",actionUrl:"#",defaultStatus:"unknown"}],
  posts:posts,
  analysis:[
    {postId:"p1",tone:"ok",label:"表现较好",reason:"示例分析：选题踩中目标读者的当周痛点，开头钩子清晰。"},
    {postId:"p2",tone:"ok",label:"表现较好",reason:"示例分析：图片质量高，发布时间踩在活跃时段。"},
    {postId:"p3",tone:"",label:"中等",reason:"示例分析：内容扎实但标题偏平，可加一个具体数字。"},
    {postId:"p5",tone:"warn",label:"需要复盘",reason:"示例分析：内容不差，但发布时间撞上了大流量账号。"}
  ],
  weeklyRecap:{weekStart:gDateAgo(6),perPlatform:{"X":{thisWeek:2},"小红书":{thisWeek:1},"公众号":{thisWeek:1}},
    best:{title:"示例：一次工具测评的完整流程",platform:"X",date:gDateAgo(1),reads:4200,likes:132,comments:18,url:"#"},
    worst:{title:"示例：我的 Notion 同步管线",platform:"公众号",date:gDateAgo(19),reads:2400,likes:64,comments:9,url:"#"},
    note:"访客模式：以上为示例数据。"},
  knowledgeBase:{available:true,matchedNotes:4,items:[
    {title:"示例：把一条内容拆成三段传播",snippet:"示例摘要：从开头钩子、核心观点到行动建议的三段式结构。",topics:["内容","结构"],updatedAt:gIsoAgo(1),obsidianUrl:""},
    {title:"示例：发布前的人工核验清单",snippet:"示例摘要：平台回执、素材版本、链接和封面各查一遍。",topics:["运营","检查"],updatedAt:gIsoAgo(3),obsidianUrl:""},
    {title:"示例：从数据反馈里找下一选题",snippet:"示例摘要：高互动不一定等于转化，要结合评论区意图判断。",topics:["选题","数据"],updatedAt:gIsoAgo(5),obsidianUrl:""},
    {title:"示例：平台差异速查",snippet:"示例摘要：同一素材在三个平台的裁剪与发布节奏。",topics:["运营","平台"],updatedAt:gIsoAgo(8),obsidianUrl:""}
  ]},
  automation:[{platform:"X",state:"available",label:"草稿入口",detail:"示例：可直接进入写作。",dashboardUrl:"#"}],
  dataPolicy:"访客模式：全部为虚构示例数据，仅用于演示界面。"
  };
})();
/* 访客态微信示例：结构与真实 wechat.json 同形，但全部是虚构联系人和内容。
   机会/咨询也故意各放一条，让访客能体验微信页签的完整四个子视图；
   toggleGuest 只把这个对象放进内存，退出访客立即恢复真实快照，不写 localStorage。 */
const GUEST_WECHAT={
  version:1, generatedAt:new Date().toISOString(), source:"wechat-mcp-demo", available:true, guest:true,
  dataPolicy:"访客模式：微信内容、账号和分析均为虚构示例，不连接本机微信，不发送消息。",
  summary:{waiting:3,mentions:2,opportunities:1,consultations:1,scanned:12,windowHours:72,mentionDays:7,businessLookbackDays:14},
  accounts:[{
    account:{id:"demo-main",name:"示例账号",ready:true,wechatVersion:"demo",pluginVersion:"demo"},
    semantic:{analyzed:5,llm:"demo",asr:true},
    businessAnalysis:{candidates:4,analyzed:4,lookbackDays:14,terms:["咨询","项目","报价","合作"]},
    waiting:[
      {name:"示例客户",kind:"direct",conversationId:"demo-wait-1",at:gIsoAgo(2),from:"peer",preview:"我们想把客服流程接入 AI",hoursAgo:2,category:"待回复",want:"咨询客服自动化落地",action:"询问现有流程和上线时间",prevSummary:"上周初步咨询过客服自动化可行性",latestDelta:"明确想把现有客服流程接入 AI 并评估周期",hasProgress:true,analyzed:true,account:"示例账号"},
      {name:"示例学员",kind:"direct",conversationId:"demo-wait-2",at:gIsoAgo(8),from:"peer",preview:"AI 副业应该怎么开始？",hoursAgo:8,category:"待回复",want:"寻找低门槛副业方向",action:"追问时间投入和已有资源",analyzed:true,account:"示例账号"},
      {name:"示例广告号",kind:"direct",conversationId:"demo-wait-3",at:gIsoAgo(5),from:"peer",preview:"限时课程优惠，点击报名",hoursAgo:5,category:"广告",want:"推广课程",action:"无需处理",analyzed:true,account:"示例账号"}
    ],
    todos:[
      {name:"示例客户",kind:"direct",conversationId:"demo-wait-1",at:gIsoAgo(2),hoursAgo:2,preview:"我们想把客服流程接入 AI",want:"咨询客服自动化落地",action:"询问现有流程和上线时间",category:"待回复",analyzed:true},
      {name:"示例学员",kind:"direct",conversationId:"demo-wait-2",at:gIsoAgo(8),hoursAgo:8,preview:"AI 副业应该怎么开始？",want:"寻找低门槛副业方向",action:"追问时间投入和已有资源",category:"待回复",analyzed:true}
    ],
    mentions:[
      {name:"示例 AI 共学群",kind:"group",conversationId:"demo-mention-1",at:gIsoAgo(1),sender:"小林",preview:"@凌一 能不能分享一下工作台的做法？",want:"请你分享工作台经验",action:"整理一个简短案例",analyzed:true},
      {name:"示例内容交流群",kind:"group",conversationId:"demo-mention-2",at:gIsoAgo(3),sender:"阿周",preview:"@凌一 这个选题可以继续展开吗？",want:"询问选题是否继续创作",action:"判断是否纳入写作计划",analyzed:true}
    ],
    opportunities:[{
      id:"demo-op-1",account:"示例账号",contact:"示例客户",kind:"direct",at:gIsoAgo(2),opportunityLevel:"O2",businessFit:"企业AI咨询",confidence:"medium",needsReview:true,analyzed:true,
      evidence:"对方明确提到想把客服流程接入 AI",sourcePreview:"我们想把客服流程接入 AI",whyRelated:"符合企业 AI 落地咨询业务",nextAction:"询问现有流程、团队人数和上线时间",missingInfo:"预算、排期、现有系统"
    }],
    consultations:[{
      id:"demo-con-1",account:"示例账号",contact:"示例学员",kind:"direct",at:gIsoAgo(8),consultationType:"教学",confidence:"high",needsReview:true,analyzed:true,
      surfaceQuestion:"AI 副业应该怎么开始？",underlyingNeed:"想找低门槛方向验证收入",evidence:"对方询问 AI 副业的开始方式",sourcePreview:"AI 副业应该怎么开始？",whyRelated:"与 AI/副业教学社群相关",nextAction:"追问时间投入和已有客户资源",missingInfo:"时间、技能、客户资源",suggestedReply:"先盘点已有技能和可触达客户，再选一个具体服务场景"
    }],
    learningTopics:[{
      id:"demo-learn-1",conversationId:"demo-group-learn-1",name:"示例 AI 实战交流群",kind:"group",at:gIsoAgo(3),
      title:"群友共推「增量 Diff + 只读草稿」微信批奏折工作流",
      summary:"多位群友分享实测经验：避免 AI 自动代发，改为本地生成三口吻草稿并按最新时间自动重浮。",
      prevSummary:"昨日讨论微信 MCP 稳定性",
      latestDelta:"今日形成「增量 Diff + 只读草稿」共识",
      hasProgress:true,
      action:"整理成一期星球实战帖或公众号短文",
      sourcePreview:"群友A：别让 AI 自动回微信，做成只读草稿+三口吻复制最稳妥。",
      analyzed:true
    }],
    active:[
      {name:"示例客户",kind:"direct",conversationId:"demo-wait-1",at:gIsoAgo(2),from:"peer",preview:"我们想把客服流程接入 AI"},
      {name:"示例 AI 共学群",kind:"group",conversationId:"demo-mention-1",at:gIsoAgo(1),from:"peer",preview:"@凌一 能不能分享一下工作台的做法？"}
    ],scanned:12
  }]
};

/* 访客态的网站 / AI 用量示例：结构和真实 web-presence.json 同形，
   让总览与自媒体页都能完整演示。所有数字都是虚构，绝不回写或进入本地缓存。 */
const GUEST_WEB={version:1,generatedAt:new Date().toISOString(),guest:true,pollEverySeconds:300,
  sites:[
    {id:"g-site",name:"示例官网",url:"https://example.com/",finalUrl:"https://example.com/",status:200,latencyMs:318,state:"ok",checkedAt:new Date().toISOString(),error:"",consecutiveFailures:0},
    {id:"g-blog",name:"示例博客",url:"https://blog.example.com/",finalUrl:"https://blog.example.com/",status:200,latencyMs:1420,state:"ok",checkedAt:new Date().toISOString(),error:"",consecutiveFailures:0},
    {id:"g-docs",name:"示例文档站",url:"https://docs.example.com/",finalUrl:"https://docs.example.com/",status:200,latencyMs:860,state:"ok",checkedAt:new Date().toISOString(),error:"",consecutiveFailures:0}
  ],
  blog:{feedUrl:"https://blog.example.com/rss.xml",state:"ok",checkedAt:new Date().toISOString(),
    latest:{title:"示例：如何把个人工作台变成每日驾驶舱",url:"#",publishedAt:gIsoAgo(3)},
    itemCount:24,newSinceLastCheck:false,usingPrevious:false,error:""},
  analytics:{provider:"Umami",state:"demo",schedule:"0 1 * * *",cadenceLabel:"每日 09:00 汇总",valuesAvailable:true,lastVerifiedAt:new Date().toISOString(),note:"访客示例：指标仅用于演示。"},
  alerts:[],
  visitors:{latest:{date:gDateAgo(1),pageviews:126,visitors:88,visits:96,topPage:"/workbench (38)、/posts/ai-workflow (24)"},
    last7Pageviews:742,asOf:gDateAgo(1),source:"访客示例数据（不连接任何真实统计服务）",
    days:Array.from({length:7},(_,i)=>({date:gDateAgo(7-i),pageviews:[72,98,126,84,136,118,108][i],visitors:[52,68,88,61,94,82,76][i],visits:[58,74,96,68,102,88,82][i],topPage:"/workbench"}))},
  search:{sites:[{name:"示例官网",clicks:48,impressions:1820,ctr:0.0264,position:12.4,deltaClicks:0.08,period:{start:gDateAgo(14),end:gDateAgo(1)},
      topQueries:[{q:"个人工作台",clicks:16,position:9.2},{q:"AI 工作流",clicks:11,position:13.8},{q:"Notion 模板",clicks:8,position:18.5}]}],
    generatedAt:new Date().toISOString(),error:""},
  aiUsage:{version:1,generatedAt:new Date().toISOString(),demo:true,
    codex:{ok:true,totalTokens:124000000,sessions:38,firstDate:gDateAgo(150),lastDate:gDateAgo(0),checkedAt:new Date().toISOString(),scope:"访客示例：本机日志口径演示",
      totals:{input_tokens:112000000,cached_input_tokens:87400000,output_tokens:12000000,reasoning_output_tokens:4300000}},
    jev:{configured:true,ok:true,model:"jev-demo",requestModel:"jev-preview",state:"访客示例：连通性演示",inputTokens:1240,outputTokens:320,checkedAt:new Date().toISOString()},
    dataPolicy:"访客示例：AI 用量数字为虚构演示，不读取任何真实日志或账号。"}
};
let webPresence=null, webPresenceErr="", webPresenceNote="";
let sectionsLoading=null, socialLoading=null, webPresenceLoading=null, zsxqDailyLoading=null;
let zsxqDaily=null, zsxqDailyErr="", zsxqDailyNote="";
let socialSnapshot=null, sectionsSnapshot=null, webPresenceSnapshot=null, wechatSnapshot=null;
let clientsSnapshot=null;
let timeProjectsSnapshot=null, timeBarsSnapshot=null;
let curType=(()=>{try{const v=localStorage.getItem("wb_type");return TYPES.some(t=>t[0]===v)?v:"ledger";}catch(e){return "ledger";}})();
let metricsExpanded=(()=>{try{return localStorage.getItem("wb_metrics_expanded")==="1";}catch(e){return false;}})();
let focusMode=(()=>{try{return localStorage.getItem("wb_focus_mode")==="1";}catch(e){return false;}})();
const secOf=id=>((sections&&sections.sections)||[]).find(s=>s.id===id)||null;
const tyId=k=>"ty"+k.charAt(0).toUpperCase()+k.slice(1);
const scId=k=>"screen"+k.charAt(0).toUpperCase()+k.slice(1);

function metricBox(){
  const ids={ledger:"kpis",project:"projStats",client:"clientStats",wechat:"wechatStats",time:"timeStats",writing:"wrStats",review:"rvStats"};
  return ids[curType]?$(ids[curType]):null;
}
function renderMetricToggle(){
  const box=metricBox(), total=box?(typeof box.querySelectorAll==="function"
    ?box.querySelectorAll(".kpi").length:(String(box.innerHTML||"").match(/class="kpi"/g)||[]).length):0;
  const inline=$("metricBtn"), menu=$("metricsMenuBtn");
  const hide=!box||total<=2||focusMode;
  const label=metricsExpanded?"收起次要指标":`展开 ${Math.max(0,total-2)} 项指标`;
  [inline,menu].forEach(b=>{if(!b)return;b.hidden=hide;b.textContent=label;b.setAttribute("aria-expanded",metricsExpanded?"true":"false");});
}
function applyViewPrefs(){
  document.body.classList.toggle("metrics-expanded",metricsExpanded);
  document.body.classList.toggle("focus-mode",focusMode);
  const fb=$("focusBtn");
  if(fb){fb.textContent=focusMode?"退出专注模式":"进入专注模式";fb.setAttribute("aria-pressed",focusMode?"true":"false");}
  renderMetricToggle();
}
function toggleMetrics(){
  metricsExpanded=!metricsExpanded;
  try{localStorage.setItem("wb_metrics_expanded",metricsExpanded?"1":"0");}catch(e){}
  applyViewPrefs();
  const m=$("metricsMenuBtn"); if(m&&m.closest("details"))m.closest("details").open=false;
}
function toggleFocusMode(){
  focusMode=!focusMode;
  try{localStorage.setItem("wb_focus_mode",focusMode?"1":"0");}catch(e){}
  applyViewPrefs();
  const f=$("focusBtn"); if(f&&f.closest("details"))f.closest("details").open=false;
}

/* 外部内容里的链接必须先验 scheme 再进 href。
   RSS 的标题与链接全部来自第三方源：`javascript:` 开头的 URL 被点一下就是执行脚本，
   而 <a href="javascript:..."> 是一条真实可达的注入路径，不是理论风险。
   验不过就退化成纯文本（信息还在，只是不可点）。 */
function safeUrl(u){
  const s=String(u||"").trim();
  return /^https?:\/\//i.test(s) ? s : "";
}
function safeObsidianUrl(u){
  const s=String(u||"").trim();
  try{
    const x=new URL(s);
    return x.protocol==="obsidian:" && x.hostname==="open" && x.searchParams.get("vault")==="Obsidian-Tech"
      && !!x.searchParams.get("file") ? s : "";
  }catch(e){ return ""; }
}
/* 相对时间只负责**措辞**，天数由上游算好。 */
function agoTxt(d){
  if(d===null||d===undefined) return "时间未知";
  if(d===0) return "今天";
  if(d===1) return "昨天";
  if(d<30) return d+" 天前";
  if(d<365) return Math.floor(d/30)+" 个月前";
  return Math.floor(d/365)+" 年前";
}
function statCells(list){
  return (list||[]).map(s=>`<div class="kpi"><div class="label">${esc(s.k)}</div>`+
    `<div class="value">${esc(s.v)}</div>`+
    (s.hint?`<div class="sub">${esc(s.hint)}</div>`:"")+`</div>`).join("");
}
/* 迷你趋势线（sparkline）：现网仪表盘 KPI 卡的标准件（值+环比+sparkline）。
   输入一组净额，输出 64×22 的折线 SVG；全部为 0/空时返回空串，不画一条平线骗人。 */
function sparkline(values,color){
  const vs=(values||[]).map(v=>Number(v)||0);
  const max=Math.max(...vs), min=Math.min(...vs);
  if(!vs.length||(max===0&&min===0)) return "";
  const w=64,h=22,pad=2, span=(max-min)||1;
  const pts=vs.map((v,i)=>{
    const x=vs.length===1?w/2:pad+i*(w-2*pad)/(vs.length-1);
    const y=h-pad-(v-min)/span*(h-2*pad);
    return x.toFixed(1)+","+y.toFixed(1);
  }).join(" ");
  const last=vs[vs.length-1], prev=vs.length>1?vs[vs.length-2]:last;
  const stroke=last>=prev?"var(--pos)":"var(--neg)";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">`+
    `<polyline points="${pts}" fill="none" stroke="${color||stroke}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}
/* 新鲜度条：先说"多久没动"，再说"拿什么比的、多少天算过期" ——
   只给一个"已过期"而不给基准，读者没法判断该不该信。 */
function freshBar(f,label,compact){
  if(!f) return "";
  const badge=f.empty?`<span class="chip warn">无数据</span>`
    :(f.stale?`<span class="chip warn">已过期</span>`:`<span class="chip ok">新鲜</span>`);
  return `<div class="fresh${f.stale?" stale":""}">`+
    `<span class="fk">${esc(label||"数据新鲜度")}</span>`+
    `<span class="fv">${esc(f.empty?"—":agoTxt(f.days))}</span>`+badge+
    `<span class="fnote"${compact?` title="基准：${esc(f.basis)}"`:""}>`+
      (compact
        ? `最新 ${esc((f.latest||"—").slice(0,10))} · ${f.threshold} 天阈值`
        : `基准：${esc(f.basis)} · 阈值 ${f.threshold} 天 · 最新 ${esc((f.latest||"—").slice(0,10))}`)+
    `</span>`+
    `</div>`;
}

async function loadSections(){
  if(sectionsLoading) return sectionsLoading;
  sectionsLoading=(async()=>{
  // 在线先读服务（唯一真源）；读不到再退本地快照，并**把"退到了哪一份"说出来**。
  // 静默拿旧快照冒充最新，是这个工作台已经踩过两次的坑（离线页过期、令牌 401 用缓存）。
  if(SERVED && TOKEN){
    try{
      const r=await fetch(SECTIONS_API,{cache:"no-store"});
      if(r.ok){ sections=await r.json(); secErr="";
        try{localStorage.setItem("wb_sections",JSON.stringify(sections));}catch(e){}
        return; }
      secErr = (r.status===404)
        ? "服务端还没有 sections.json。在终端跑一次：python3 scripts/sync/sections.py --write"
        : "读取类型区失败：HTTP "+r.status;
    }catch(e){ secErr="服务未响应："+e.message; }
  }else if(SERVED&&!TOKEN){ secErr="链接里没有访问令牌，读不到类型区数据。"; }
  try{
    const t=localStorage.getItem("wb_sections");
    if(t){ sections=JSON.parse(t);
      secNote="当前显示的是内嵌快照（生成于 "+String(sections.generatedAt||"").replace("T"," ").slice(0,16)+"），不是刚从磁盘读的。";
    }
  }catch(e){}
  if(!sections && !secErr) secErr="没有可用的类型区数据。";
  })();
  try{return await sectionsLoading;}finally{sectionsLoading=null;}
}
async function refreshSections(){ await loadSections(); renderSections(); }

async function loadSocial(){
  if(socialLoading) return socialLoading;
  socialLoading=(async()=>{
  // social.json 与 sections.json 一样是只读派生快照。页面只把“今日人工核验状态”
  // 写进单独 localStorage，不把社媒数据混回 Notion 主状态。
  if(SERVED && TOKEN){
    try{
      const r=await fetch(SOCIAL_API,{cache:"no-store"});
      if(r.ok){ social=await r.json(); socialErr="";
        try{localStorage.setItem("wb_social",JSON.stringify(social));}catch(e){}
        return; }
      socialErr=r.status===404
        ? "服务端还没有 social.json。在终端跑一次：python3 scripts/sync/social-sync.py"
        : "读取自媒体数据失败：HTTP "+r.status;
    }catch(e){ socialErr="自媒体快照服务未响应："+e.message; }
  }else if(SERVED&&!TOKEN){ socialErr="链接里没有访问令牌，读不到自媒体数据。"; }
  try{
    const t=localStorage.getItem("wb_social");
    if(t){ social=JSON.parse(t);
      socialNote="当前显示的是内嵌快照（生成于 "+String(social.generatedAt||"").replace("T"," ").slice(0,16)+"），不是刚从磁盘读的。";
    }
  }catch(e){}
  if(!social&&!socialErr) socialErr="没有可用的自媒体数据。";
  })();
  try{return await socialLoading;}finally{socialLoading=null;}
}

async function loadZsxqDaily(){
  if(zsxqDailyLoading) return zsxqDailyLoading;
  zsxqDailyLoading=(async()=>{
    // 日报同样只按令牌读服务端快照；读不到退回内嵌种子（离线版由 make-local.py 注入）。
    if(SERVED && TOKEN){
      try{
        const r=await fetch(ZSXQ_DAILY_API,{cache:"no-store"});
        if(r.ok){ zsxqDaily=await r.json(); zsxqDailyErr="";
          try{localStorage.setItem("wb_zsxq_daily",JSON.stringify(zsxqDaily));}catch(e){}
          return; }
        zsxqDailyErr=r.status===404
          ? "服务端还没有日报快照。在终端跑一次：python3 scripts/sync/zsxq-daily-sync.py"
          : "读取日报失败：HTTP "+r.status;
      }catch(e){ zsxqDailyErr="日报快照服务未响应："+e.message; }
    }else if(SERVED&&!TOKEN){ zsxqDailyErr="链接里没有访问令牌，读不到日报。"; }
    try{
      const t=localStorage.getItem("wb_zsxq_daily");
      if(t){ zsxqDaily=JSON.parse(t);
        zsxqDailyNote="当前显示的是内嵌快照（生成于 "+String(zsxqDaily.generatedAt||"").replace("T"," ").slice(0,16)+"）。";
      }
    }catch(e){}
    if(!zsxqDaily&&!zsxqDailyErr) zsxqDailyErr="没有可用的日报数据。";
  })();
  try{return await zsxqDailyLoading;}finally{zsxqDailyLoading=null;}
}

async function loadWechat(){
  if(wechatLoading) return wechatLoading;
  wechatLoading=(async()=>{
  // 与 social.json 同一套读取策略：在线读服务（唯一真源），读不到才退内嵌快照，
  // 并且**必须说清楚退到了哪一份**——静默拿旧数据冒充最新是这里反复踩过的坑。
  if(guest){
    wechat=null; wechatErr="访客模式不读取本机微信对话。"; return;
  }
  if(SERVED && TOKEN){
    try{
      const r=await fetch(WECHAT_API,{cache:"no-store"});
      if(r.ok){ wechat=await r.json(); wechatErr="";
        try{localStorage.setItem("wb_wechat",JSON.stringify(wechat));}catch(e){}
        return; }
      wechatErr=r.status===404
        ? "服务端还没有 wechat.json。在终端跑一次：python3 scripts/sync/wechat-sync.py"
        : "读取微信快照失败：HTTP "+r.status;
    }catch(e){ wechatErr="微信快照服务未响应："+e.message; }
  }else if(SERVED&&!TOKEN){ wechatErr="链接里没有访问令牌，读不到微信快照。"; }
  try{
    const t=localStorage.getItem("wb_wechat");
    if(t){ wechat=JSON.parse(t);
      wechatNote="当前显示的是内嵌快照（生成于 "+String(wechat.generatedAt||"").replace("T"," ").slice(0,16)+"），不是刚从磁盘读的。";
    }
  }catch(e){}
  if(!wechat&&!wechatErr) wechatErr="没有可用的微信快照。";
  })();
  try{return await wechatLoading;}finally{wechatLoading=null;}
}

async function loadWebPresence(background=false){
  if(guest) return;
  if(webPresenceLoading) return webPresenceLoading;
  webPresenceLoading=(async()=>{
  if(SERVED && TOKEN){
    try{
      const r=await fetch(WEB_PRESENCE_API,{cache:"no-store"});
      if(r.ok){
        const next=await r.json(), changed=!webPresence||next.generatedAt!==webPresence.generatedAt;
        webPresence=next; webPresenceErr=""; webPresenceNote="";
        try{localStorage.setItem("wb_web_presence",JSON.stringify(webPresence));}catch(e){}
        if(background&&changed&&curType==="content") renderContent();
        return;
      }
      webPresenceErr=r.status===404?"还没有网站监控快照。":"读取网站监控失败：HTTP "+r.status;
    }catch(e){webPresenceErr="网站监控服务未响应："+e.message;}
  }else if(SERVED&&!TOKEN){webPresenceErr="链接里没有访问令牌，读不到网站监控。";}
  try{
    const t=localStorage.getItem("wb_web_presence");
    if(t){webPresence=JSON.parse(t);webPresenceNote="当前显示上一次监控快照。";}
  }catch(e){}
  if(!webPresence&&!webPresenceErr) webPresenceErr="没有可用的网站监控数据。";
  })();
  try{return await webPresenceLoading;}finally{webPresenceLoading=null;}
}

async function loadPageData(k){
  if(["news","writing","review"].includes(k)){
    return sections ? Promise.resolve() : loadSections();
  }
  if(k==="content"){
    const jobs=[];
    if(!social) jobs.push(loadSocial());
    if(!radar) jobs.push(loadRadar());
    if(!webPresence) jobs.push(loadWebPresence());
    return jobs.length?Promise.all(jobs):Promise.resolve();
  }
  if(k==="zsxq"){
    // 星球一屏依赖 social.json + 日报快照；radar / webPresence 与它无关，不要顺手多抓。
    const jobs=[];
    if(!social) jobs.push(loadSocial());
    if(!zsxqDaily) jobs.push(loadZsxqDaily());
    return jobs.length?Promise.all(jobs):Promise.resolve();
  }
  if(k==="wechat"){
    return wechat ? Promise.resolve() : loadWechat();
  }
  return Promise.resolve();
}

/* 微信角标 = 待回复人数。只在快照已经读过时才显示 —— 没读过的 0 不是"没人找你"，
   是"还不知道"，两者不能长一个样。 */
function renderWechatBadge(){
  const badge=$("tyWechatN"); if(!badge) return;
  // 角标必须和页面上的「待办」同源。用 summary.waiting 会多算 —— 那个数含广告
  // 和已收尾的对话，于是导航说 4、点进去待办只有 3，读者会以为漏了一条。
  if(!wechat||!wechat.available){ badge.textContent=""; return; }
  const muted=wxMuted();
  let n=0;
  (wechat.accounts||[]).forEach(a=>{
    (a.waiting||[]).forEach(x=>{
      const rest=x.category==="广告"||x.category==="已结束";
      if(!rest && !muted.includes(x.conversationId)) n++;
    });
  });
  badge.textContent=n?(" "+n):"";
}
function renderTypeBar(){
  TYPES.forEach(([k])=>{
    const b=$(tyId(k)); if(!b) return;
    // 二级页（如知识星球）高亮它挂靠的顶级页，否则顶部一个都不亮
    const activeKey=SUBPAGES[curType]?SUBPAGES[curType].parent:curType;
    const on=(k===activeKey);
    b.classList.toggle("on",on);
    // aria-pressed 与 .on 必须同源更新：只改视觉不改 aria，
    // 屏幕阅读器会一直念"未按下"，而屏幕上已经是选中态。
    b.setAttribute("aria-pressed",on?"true":"false");
    b.tabIndex=on?0:-1;
    b.setAttribute("aria-controls",scId(k));
  });
  const n=secOf("news");
  const badge=$("tyNewsN");
  if(badge){
    const unread=n?((n.items||[]).filter(i=>!i.read&&i.days!==null&&i.days<=n.windowDays).length):0;
    badge.textContent=unread?(" "+unread):"";
  }
  renderWechatBadge();
  const h=$("typeHint"); if(!h) return;
  let txt="", warn=false;
  if(curType==="ledger"){
    const t=(state&&state.lastNotionSync)?String(state.lastNotionSync).slice(0,16).replace("T"," "):"—";
    /* 访客态的页签提示不能照抄真实口径——下面写着「示例数据」，
       角标却说「真实项目 / 81 条真实数据」会自相矛盾。 */
    txt=guest?"访客模式 · 经营数据为虚构示例，不含真实收益"
             :"经营数据来自 Notion 只读同步，最后同步 "+t;
  }else{
    if(curType==="project"){
      const ps=(state&&state.projectsSource&&state.projectsSource.name)||"项目管理";
      txt=ps+" · Notion 可回写 · "+((state&&state.projects)||[]).length+" 个"+(guest?"示例":"真实")+"项目";
    }else if(curType==="content"){
      const g=social&&social.generatedAt?String(social.generatedAt).replace("T"," ").slice(0,16):"—";
      txt=guest?("访客模式 · 自媒体数据为虚构示例 · 快照生成 "+g)
               :((socialErr||NEWHINTS.content)+" · 快照生成 "+g); warn=!!socialErr&&!guest;
    }else if(curType==="wechat"){
      const gen=wechat&&wechat.generatedAt?String(wechat.generatedAt).replace("T"," ").slice(0,16):"—";
      txt=guest?("访客模式 · 不读取本机微信对话")
               :((wechatErr||NEWHINTS.wechat)+" · 快照生成 "+gen);
      warn=!!wechatErr&&!guest;
    }else if(NEWTYPES.includes(curType)){
      txt=(guest&&curType==="client")?"本地试验 · 示例客户数据（不回写 Notion）":NEWHINTS[curType]||"";
      warn=false;
    }  // 本地试验区：自定义提示，不读 sections.json
    else{
      const s=secOf(curType);
      if(!s){ txt=secErr||"类型区数据未加载"; if(secErr) warn=true; }
      else{
        const f=s.freshness||{};
        warn=!!f.stale;
        txt=s.name+" · 最新内容 "+(f.empty?"无":agoTxt(f.days))+" · 阈值 "+f.threshold+" 天"+(f.stale?"（已过期）":"");
      }
    }
  }
  h.textContent=txt;
  h.className=warn?"typehint warn":"typehint";
}

function typeKeydown(e){
  const keys=["ArrowRight","ArrowLeft","Home","End"];
  if(!e||keys.indexOf(e.key)<0) return;
  const buttons=TYPES.map(([id])=>$(tyId(id))).filter(Boolean);
  const target=e.target&&e.target.closest?e.target.closest(".segbtn"):null;
  let i=buttons.indexOf(target);
  if(i<0) i=Math.max(0,TYPES.findIndex(([id])=>id===curType));
  if(e.key==="Home") i=0;
  else if(e.key==="End") i=buttons.length-1;
  else if(e.key==="ArrowRight") i=(i+1)%buttons.length;
  else i=(i-1+buttons.length)%buttons.length;
  e.preventDefault();
  setType(TYPES[i][0]);
  const next=buttons[i];
  if(next&&next.focus){
    try{next.focus({preventScroll:true});}catch(err){next.focus();}
  }
}

function setType(k){
  if(!TYPES.some(t=>t[0]===k) && !SUBPAGES[k]) k="ledger";
  const changed=k!==curType;
  curType=k;
  try{localStorage.setItem("wb_type",k);}catch(e){}
  TYPES.forEach(([id])=>{const el=$(scId(id)); if(el) el.hidden=(id!==k);});
  // 子页屏幕不在 TYPES 里，单独显隐；离开时必须藏起来
  const subScreen=$("screenZsxq"); if(subScreen) subScreen.hidden=!SUBPAGES[k];
  renderTypeBar();
  renderMetricToggle();
  // 总览先显示，进入资讯 / 写作 / 复盘 / 自媒体时再按需读取对应快照；
  // 已经加载过的快照直接渲染，避免切屏时重复请求并替换正在使用的对象。
  // 切到非总览页签时只渲染当前屏，避免白白重绘已隐藏的总览 18 个模块与双重 renderSections()。
  if(k==="ledger"){
    renderAll();
  }else if((k==="content"&&(!social||!webPresence))
      || (k==="zsxq"&&(!social||!zsxqDaily))
      || (k==="wechat"&&!wechat)
      || (["news","writing","review"].includes(k)&&!sections)){
    renderSections();
    loadPageData(k).then(()=>{if(curType===k){renderSections();renderWechatBadge();renderMetricToggle();}}).catch(()=>{});
  }else{
    renderSections();
  }
  /* 手机端类型导航是横向轨道：把当前项滚到中间，避免只露出右边缘的半截标签。 */
  if(typeof window!=="undefined"&&typeof window.matchMedia==="function"
      &&window.matchMedia("(max-width:560px)").matches){
    const align=()=>{
      const seg=$("typeSeg"), active=$(tyId(k));
      if(!seg||!active) return;
      const left=Math.max(0,active.offsetLeft-(seg.clientWidth-active.offsetWidth)/2);
      /* 这里用即时滚动而不是 smooth：手机切页时 smooth 会被后续布局/滚动竞争打断，
         实测会停在最左侧，当前标签反而藏在视口外。 */
      seg.scrollLeft=left;
    };
    align();
  }
  /* 切到另一屏时回到页首：否则上一屏的滚动位置会把新屏标题压在吸顶类型栏下面。 */
  if(changed&&typeof window!=="undefined"&&typeof window.scrollTo==="function") window.scrollTo(0,0);
}

function setNewsScope(k){
  newsScope=(k==="window"||k==="all")?k:"recent";
  const s=secOf("news"); if(s) renderNews(s);
}
function setNewsFilter(){
  newsQueryF=String($("newsQ")?.value||"").trim().toLowerCase();
  newsFeedF=$("newsFeedSel")?.value||"";
  newsGroupF=$("newsGroupSel")?.value||"";
  newsUnreadF=!!$("newsUnreadOnly")?.checked;
  const s=secOf("news"); if(s) renderNews(s);
}
function clearNewsFilter(){
  newsQueryF=""; newsFeedF=""; newsGroupF=""; newsUnreadF=false;
  if($("newsQ")) $("newsQ").value="";
  if($("newsFeedSel")) $("newsFeedSel").value="";
  if($("newsGroupSel")) $("newsGroupSel").value="";
  if($("newsUnreadOnly")) $("newsUnreadOnly").checked=false;
  const s=secOf("news"); if(s) renderNews(s);
}
function setReviewFilter(){
  reviewQueryF=String($("rvQ")?.value||"").trim().toLowerCase();
  reviewTypeF=$("rvTypeSel")?.value||"";
  const s=secOf("review"); if(s) renderReview(s);
}
function clearReviewFilter(){
  reviewQueryF=""; reviewTypeF="";
  if($("rvQ")) $("rvQ").value="";
  if($("rvTypeSel")) $("rvTypeSel").value="";
  const s=secOf("review"); if(s) renderReview(s);
}
/* ---------- 复盘站内预览：GET /api/review/note 拉只读正文，安全渲染成 HTML ----------
   正文真源仍是 Obsidian；这里不提供编辑入口，深链保留在弹窗头部与列表行尾。 */
/* ── 复盘阅读模式：与资讯共用同一个阅读抽屉（newsReaderOv） ──
   侧栏换成复盘列表可点选切换，正文仍从 /api/review/note 拉只读 Markdown。 */
let newsReaderMode="news";
let reviewReaderItems=[], reviewReaderIndex=0, reviewReaderLoading=false;
function setNewsReaderMode(mode){
  newsReaderMode=mode==="review"?"review":"news";
  const ov=$("newsReaderOv");
  if(ov) ov.setAttribute("data-mode",newsReaderMode);
  const texts={
    readerKicker:newsReaderMode==="review"?"复盘 · 只读阅读":"资讯 · 站内阅读",
    readerSidebarEyebrow:newsReaderMode==="review"?"Obsidian 调试经验":"今日阅读",
    readerSidebarTitle:newsReaderMode==="review"?"复盘列表":"资讯列表",
    readerOriginalLabel:newsReaderMode==="review"?"在 Obsidian 中打开":"打开原文"
  };
  Object.entries(texts).forEach(([id,value])=>{
    const node=$(id);
    if(node) node.textContent=value;
  });
}
function reviewReaderItemDate(item){
  const date=String(item&&item.date||"").slice(0,10).replaceAll("-","/");
  const ago=agoTxt(item&&item.days);
  return [date,ago&&ago!=="时间未知"?ago:""].filter(Boolean).join(" · ");
}
function renderReviewReaderList(){
  const box=$("newsReaderList"), count=$("newsReaderListCount");
  if(!box||!count) return;
  count.textContent=reviewReaderItems.length
    ?`${reviewReaderItems.length} 篇 · 点选切换，当前笔记已标记`
    :"没有可切换的复盘笔记";
  box.innerHTML=reviewReaderItems.length?reviewReaderItems.map((item,i)=>{
    const active=i===reviewReaderIndex;
    const date=reviewReaderItemDate(item);
    return `<button class="reader-news-item${active?" active":""}" type="button"
      ${active?'aria-current="true"':""}
      onclick="return selectReviewReader(${i},event)">
      <span class="reader-news-source">复盘</span>
      <span class="reader-news-title">${esc(String(item.title||"").replace(/^调试经验-/,""))}</span>
      ${date?`<span class="reader-news-date">${esc(date)}</span>`:""}
    </button>`;
  }).join(""):`<div class="empty">没有可切换的复盘笔记。</div>`;
  const active=box.querySelector(".reader-news-item.active");
  if(active) setTimeout(()=>{try{active.scrollIntoView({block:"nearest"});}catch(e){}},0);
}
function selectReviewReader(index,event){
  if(event&&event.preventDefault) event.preventDefault();
  const item=reviewReaderItems[index];
  if(!item||!item.noteId||reviewReaderLoading) return false;
  if(index===reviewReaderIndex) return false;
  reviewReaderIndex=index;
  renderReviewReaderList();
  loadReviewReaderNote(item);
  return false;
}
async function loadReviewReaderNote(item){
  reviewReaderLoading=true;
  resetNewsReaderPane({title:item.title,url:item.obsidianUrl||"#"});
  const st=$("newsReaderState");
  if(st) st.textContent="正在读取正文…";
  setNewsReaderMeta({
    site:"Obsidian 调试经验",
    date:reviewReaderItemDate(item),
    detail:""
  });
  try{
    const sep=REVIEW_NOTE_API.includes("?")?"&":"?";
    const r=await fetch(REVIEW_NOTE_API+sep+"noteId="+encodeURIComponent(item.noteId),{cache:"no-store"});
    const j=await r.json().catch(()=>({}));
    if(!r.ok||!j.ok) throw new Error(j.error||("HTTP "+r.status));
    $("newsReaderTitle").textContent=String(item.title||"复盘").replace(/^调试经验-/,"");
    setNewsReaderMeta({
      site:"Obsidian 调试经验",
      date:reviewReaderItemDate(item),
      detail:`${(j.chars||0).toLocaleString()} 字 · 只读，修改请回 Obsidian`
    });
    $("newsReaderBody").innerHTML=safeMarkdownHtml(j.content||"");
    adaptReviewReaderBlocks($("newsReaderBody"));
    st.hidden=true;
    renderNewsReaderTOC();
    requestAnimationFrame(updateNewsReaderProgress);
  }catch(e){
    newsReaderSetError("读取复盘失败："+String(e&&e.message||e));
  }finally{
    reviewReaderLoading=false;
  }
}
async function openReviewNote(btn){
  const noteId=btn?.dataset?.note||"", title=btn?.dataset?.title||"复盘预览", obs=btn?.dataset?.obs||"";
  if(guest){ notify("访客模式不读取本机笔记","warn"); return; }
  if(!SERVED||!TOKEN){ notify("本地同步服务未连接，暂请通过 Obsidian 打开","warn"); return; }
  if(!noteId){ notify("这条记录缺稳定 ID，先刷新数据","warn"); return; }
  // 侧栏列表跟随当前筛选结果；点选可在复盘笔记之间直接切换。
  const sec=secOf("review");
  reviewReaderItems=((sec&&sec.items)||[]).map(n=>({
    noteId:n.noteId||"",title:n.title||"",date:n.date||"",days:n.days,
    obsidianUrl:n.obsidianUrl||""
  })).filter(x=>x.noteId);
  let index=reviewReaderItems.findIndex(x=>x.noteId===noteId);
  if(index<0){
    reviewReaderItems.unshift({noteId,title,date:"",days:null,obsidianUrl:obs});
    index=0;
  }
  reviewReaderIndex=index;
  setNewsReaderMode("review");
  if($("newsReaderOv")&&$("newsReaderOv").getAttribute("data-reader-font")==="weekly"){
    ensureReaderWeeklyFont();
  }
  ensureReaderWenkaiWebFont();
  renderReviewReaderList();
  openOv("newsReaderOv");
  loadReviewReaderNote(reviewReaderItems[index]);
}
/* 复盘正文套用阅读层的交互外观：safeMarkdownHtml 出来的是通用标签，
   这里只做 DOM 包装（代码块/引用加复制按钮、图片可放大），不碰正文文本。 */
function adaptReviewReaderBlocks(scope){
  if(!scope) return;
  scope.querySelectorAll("pre").forEach(pre=>{
    if(pre.closest(".reader-code-block")) return;
    const code=pre.querySelector("code");
    const wrap=document.createElement("div");
    wrap.className="reader-code-block";
    const head=document.createElement("div");
    head.className="reader-code-head";
    head.innerHTML=`<span class="reader-code-lang">代码</span>`;
    head.insertAdjacentHTML("beforeend",readerCopyButton(code?code.textContent:"","复制代码或提示词"));
    pre.replaceWith(wrap);
    wrap.append(head,pre);
  });
  scope.querySelectorAll("blockquote").forEach(quote=>{
    if(quote.closest(".reader-quote-wrap")) return;
    const wrap=document.createElement("div");
    wrap.className="reader-quote-wrap";
    quote.replaceWith(wrap);
    wrap.append(quote);
    wrap.insertAdjacentHTML("beforeend",readerCopyButton(quote.textContent||"","复制引用内容"));
  });
  scope.querySelectorAll("img").forEach(img=>{
    if(img.closest(".reader-figure")) return;
    const fig=document.createElement("figure");
    fig.className="reader-figure";
    const btn=document.createElement("button");
    btn.type="button";
    btn.className="reader-image-open";
    btn.setAttribute("aria-label","放大图片");
    btn.setAttribute("data-caption",img.alt||"");
    btn.setAttribute("onclick","openReaderImage(this)");
    img.replaceWith(fig);
    fig.append(btn);
    btn.append(img);
    img.setAttribute("loading","lazy");
    img.setAttribute("decoding","async");
    img.setAttribute("referrerpolicy","no-referrer");
  });
  scope.querySelectorAll("code").forEach(code=>{
    if(!code.closest("pre")) code.classList.add("reader-inline-code");
  });
}
function setReviewProductFilter(p){
  reviewQueryF=String(p||"").trim().toLowerCase();
  if($("rvQ")) $("rvQ").value=reviewQueryF;
  const s=secOf("review"); if(s) renderReview(s);
  $("rvItems")?.scrollIntoView?.({behavior:"smooth",block:"nearest"});
}
function filterNewsBySource(name){
  newsFeedF=newsFeedF===name?"":name;
  newsScope="all";
  if($("newsFeedSel")) $("newsFeedSel").value=newsFeedF;
  if($("newsGroupSel")) $("newsGroupSel").value="";
  if($("newsUnreadOnly")) $("newsUnreadOnly").checked=false;
  const s=secOf("news"); if(s) renderNews(s);
}
function focusDeadFeeds(){
  const s=secOf("news"); if(!s) return;
  const dead=(s.feeds||[]).filter(f=>f.dead&&!f.paused&&!f.error);
  if(!dead.length){ notify("当前没有停更的订阅源。","info"); return; }
  const first=dead[0];
  filterNewsBySource(first.name);
  if($("newsGroupSel")) $("newsGroupSel").value=first.group||"";
  const html=`<div class="nitem news-lead"><div class="nt">停更订阅源：${dead.map(f=>esc(f.name)).join("、")}</div>
    <div class="nm2"><span class="chip warn">超过 ${s.feedStaleDays||100} 天无更新</span></div>
    <div class="ns">已按分组「${esc(first.group||"—")}」筛选，并定位到「${esc(first.name)}」的历史条目。订阅源健康表仍保留全部源，可继续检查或管理。</div></div>`;
  $("newsItems").insertAdjacentHTML("afterbegin",html);
  notify(`已定位停更源「${first.name}」。顶部摘要同时列出 ${dead.length} 个停更源。`,"warn");
}

/* 每个区的"数据没读到"要用同一句话说清原因和下一步，不要在三个区里各写一套。 */
function secEmpty(msg){
  return `<div class="fresh stale"><span class="fk">读不到数据</span>`+
    `<span class="fnote">${esc(msg||"暂无数据")}</span></div>`;
}
function secClear(msg){
  return `<div class="fresh"><span class="fk">全部正常</span>`+
    `<span class="fnote">${esc(msg||"当前没有异常")}</span></div>`;
}

function renderSections(){
  if(curType==="ledger") return;         // 总览区由 renderAll 里原有那套负责，这里不碰
  if(NEWTYPES.includes(curType)){ renderNewType(curType); return; }  // 项目读 state；另三类读本地试验数据，不污染 sections.json 口径
  const s=secOf(curType);
  const box=$({news:"newsFresh",writing:"wrFresh",review:"rvFresh"}[curType]);
  if(!s){
    if(box) box.innerHTML=secEmpty(secErr);
    return;
  }
  if(box) box.innerHTML=freshBar(s.freshness,undefined,curType==="news"||curType==="writing")+
    (secNote?`<div class="no-note" style="margin:-6px 0 12px">${esc(secNote)}</div>`:"");
  if(curType==="news") renderNews(s);
  else if(curType==="writing") renderWriting(s);
  else if(curType==="review") renderReview(s);
}

