/* ---------- config ---------- */
/* 访问令牌。
   服务端 127.0.0.1:8787 对 /data.json 和 /api/sync 都要求 token；裸链接一律 401，
   页面会静默退回浏览器缓存 —— 看起来一切正常，其实数据是旧的。所以：
   ① 从 URL 的 ?token= 取；② 取到就记进 localStorage，之后裸链接也能用；
   ③ 都没有就置 TOKEN_MISSING，由启动横幅明确告知，不装作正常。 */
const TOKEN=(()=>{
  const sp=new URLSearchParams(location.search);
  const q=sp.get("token")||"";
  if(q){
    // 服务端已通过 Set-Cookie 下发 HttpOnly; SameSite=Strict 会话 Cookie；
    // 不再把令牌持久化写入 localStorage，并顺手清理历史残留的 wb_token，
    // 随后立刻抹除地址栏中的 ?token=，降低 XSS / 历史记录 / 截图泄露面。
    try{localStorage.removeItem("wb_token");}catch(e){}
    try{
      if(typeof history!=="undefined"&&typeof history.replaceState==="function"){
        sp.delete("token");
        const qs=sp.toString();
        history.replaceState(null,"",location.pathname+(qs?("?"+qs):"")+(location.hash||""));
      }
    }catch(e){}
    return q;
  }
  // 向下兼容无 Cookie 的离线测试夹具或旧浏览器残留会话（只读不写）
  try{ return localStorage.getItem("wb_token")||""; }catch(e){ return ""; }
})();
// 统一声明式事件委托（替代高风险内联 onclick=，白名单分发动作）
if(typeof document!=="undefined"&&typeof document.addEventListener==="function"&&!globalThis.__wbActionBound){
  document.addEventListener("click",e=>{
    const el=e.target&&typeof e.target.closest==="function"?e.target.closest("[data-wb-action]"):null;
    if(!el) return;
    const act=el.getAttribute("data-wb-action")||"";
    const arg=el.getAttribute("data-wb-arg")||"";
    if(act==="set-type" && typeof setType==="function") setType(arg);
    else if(act==="zsxq-digest-mode" && typeof setZsxqDigestMode==="function") setZsxqDigestMode(arg);
    else if(act==="zsxq-daily-sync" && typeof syncZsxqDaily==="function") syncZsxqDaily(el);
    else if(act==="social-sync" && typeof syncSocialPlatform==="function") syncSocialPlatform(arg,el);
  });
  globalThis.__wbActionBound=true;
}
const SERVED = location.protocol.startsWith("http");
const TOKEN_MISSING = SERVED && !TOKEN;
// 统一同源鉴权拦截：所有对本机 /*.json 与 /api/* 的 fetch 自动带 HttpOnly Cookie（same-origin），
// 并在内存持有 TOKEN 时通过 Authorization: Bearer Header 发送，彻底消除 URL Query 中的明文令牌。
if(typeof globalThis!=="undefined"&&typeof globalThis.fetch==="function"&&!globalThis.__wbFetchWrapped){
  const _wbRawFetch=globalThis.fetch.bind(globalThis);
  globalThis.fetch=function(input,init){
    const url=typeof input==="string"?input:(input&&typeof input.url==="string"?input.url:"");
    if(url.startsWith("/")&&!url.startsWith("//")){
      const opts=Object.assign({credentials:"same-origin"},init||{});
      if(TOKEN){
        if(typeof Headers!=="undefined"){
          const h=new Headers(opts.headers||{});
          if(!h.has("Authorization")) h.set("Authorization","Bearer "+TOKEN);
          opts.headers=h;
        }else{
          const h=Object.assign({},opts.headers||{});
          if(!h.Authorization&&!h.authorization) h.Authorization="Bearer "+TOKEN;
          opts.headers=h;
        }
      }
      return _wbRawFetch(input,opts);
    }
    return _wbRawFetch(input,init);
  };
  globalThis.__wbFetchWrapped=true;
}
const API="/data.json";
const DATA_META_API="/api/data-meta";
/* 「同步一次」按钮打的后端：浏览器跑不了 shell，交给本地 app.py 代跑 scripts/sync-now.sh */
const SYNC_API="/api/sync";
/* 同步历史（最近 20 条，服务端落盘 sync-log.json），「更多」面板里展开查看 */
const SYNC_LOG_API="/api/sync/log";
/* 资讯站内阅读：服务端抓取并清洗第三方 HTML，页面只接收标题和正文块。 */
const ARTICLE_API="/api/article";
/* 写作正文读写：前端只传稳定 ID，服务端重新定位 Obsidian 文件并做哈希冲突检测。 */
const WRITING_CHAPTER_API="/api/writing/chapter";
const WRITING_SAVE_API="/api/writing/save";
const WRITING_DOCUMENT_API="/api/writing/document";
const WRITING_DOCUMENT_ASSET_API="/api/writing/document/asset";
const WRITING_DOCUMENT_SAVE_API="/api/writing/document/save";
const WRITING_CREATE_API="/api/writing/create";
const WRITING_DELETE_API="/api/writing/delete";
const WRITING_TRASH_API="/api/writing/trash";
const WRITING_RESTORE_API="/api/writing/restore";
const REVIEW_NOTE_API="/api/review/note";
const DEFAULT_TYPES=["项目收入","咨询","被动收入","其他收入"];
const EXPENSE_LABEL="支出";
const STATUS=["进行中","已完成","风险","待启动"];
let state=null, guest=false, guestState=null, uid="", pollTimer=null;
let dataVersion=null, dataPollInFlight=false;
let syncing=false;   // 同步进行中，防连点
let latestSyncLog=null;
let newsReaderAbort=null, newsReaderUrl="";
let newsReaderItems=[], newsReaderIndex=-1, newsReaderHeadingIds=[];
let ledAll=false;    // 收益明细是否展开全部（否则只显示最近 12 笔）
let loadError=null;  // 读取线上数据失败的原因（用于启动横幅，避免"静默用旧缓存"）
let anScope="all";       // 分析口径：month=本月 / d90=近90天 / all=累计。
                         // 只控制「看结构」的三张卡（收入来源 / 收益构成 / 支出构成）。
                         // 默认「累计」：「谁在给我钱」是长期问题，只切到本月会看不到客户结构。
                         // 目标带恒为本月（目标是月度的），KPI 与明细各有自己的语义，都不跟它走。
let focusIds=null;       // 由「数据体检」跳过来时只显示这几条明细
let focusLabel="";
let healthCache=[];      // 体检当前渲染的条目，供 onclick 用索引取回
let writingDocFilter="全部";
let writingCreateKind="document";
let writingCreateType="公众号";

/* ---------- utils ---------- */
const $=id=>document.getElementById(id);
const todayStr=d=>{const x=d?new Date(d):new Date();return x.getFullYear()+"-"+String(x.getMonth()+1).padStart(2,"0")+"-"+String(x.getDate()).padStart(2,"0");};
/* 专注记录按上海日期归档：浏览器可能被带到别的时区，不能用 toISOString() 的 UTC 日期跨日。 */
const focusDateStr=d=>{
  const x=d?new Date(d):new Date();
  const p={};
  new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit"})
    .formatToParts(x).forEach(v=>{p[v.type]=v.value;});
  return p.year+"-"+p.month+"-"+p.day;
};
const uidGen=()=>"x"+Math.random().toString(36).slice(2,9)+Date.now().toString(36).slice(-4);
/* ---------- 显示单位（元 / 千元 / 万元） ----------
   和「分析口径」是两件事，别混淆：口径决定**哪些记录被算进来**，单位只决定**同一个数怎么写**。
   所以口径可以按卡分（来源卡看累计、构成卡看本月），单位必须是整页唯一的 ——
   否则同一屏上会同时出现 ¥26,958.49 和 ¥2.7 万元，读者无法判断是不是同一个数。

   存 localStorage 而不是 data.json：这是显示偏好，不属于账目数据，
   也不该再给 normalize() 的透传列表增负担（漏一个键就是"保存一次即静默抹掉"）。 */
const UNITS=[["yuan","元",1,""],["k","千元",1000,"千"],["wan","万元",10000,"万"]];
let moneyUnit=(()=>{try{const v=localStorage.getItem("wb_unit");return UNITS.some(u=>u[0]===v)?v:"yuan";}catch(e){return "yuan";}})();
function unitDef(k){return UNITS.find(u=>u[0]===(k||moneyUnit))||UNITS[0];}
function unitLabel(k){return unitDef(k)[1];}
/* 金额格式化。**元档下必须与改造前逐字节一致**（测试与页面里大量 ¥26,958.49 这种写法依赖它）。
   千元/万元档的小数位随量级走：≥1 用 2 位、<1 用 4 位、再小用 6 位 ——
   否则"平均单笔 ¥260.87"在万元档会变成 ¥0.03 万元，数字没算错但信息没了。 */
function fmt(n){
  const u=unitDef(), div=u[2], s=n<0?"-":"";
  const v=Math.abs(n)/div;
  const d=div===1?2:(v>=1?2:(v>=0.01?4:6));
  const p=Math.pow(10,d);
  const num=Math.round(v*p)/p;
  const str=num.toLocaleString("zh-CN",{minimumFractionDigits:div===1?0:2,maximumFractionDigits:d});
  return s+"¥"+str+(u[3]?(" "+u[1]):"");
}
function sign(n){return n>0?"+":(n<0?"-":"");}
function pct(a,b){return b? Math.round(a/b*100):0;}
/* 达成率文案。不足 1% 时如实写「不到 1%」：0.1% 被 round 成 0% 看起来像"没数据"，
   是个假状态。目标带 / KPI 卡 / 达成史三处共用这一个实现，别各写一份。
   返回 null 表示"没有目标"，由调用方决定怎么表述。 */
function pctTxt(net,goal){
  if(!(goal>0)) return null;
  const p=Math.round(net/goal*100);
  return (net>0 && p<1) ? "不到 1%" : p+"%";
}
function daysInMonth(y,m){return new Date(y,m,0).getDate();}
function sum(arr,f){return arr.reduce((s,x)=>s+f(x),0);}

/* ---------- load / save ---------- */
function defaultState(){
  return {version:2,owner:"我的工作台",monthlyGoal:null,goals:{},notionStatus:"local",lastNotionSync:null,
    projects:[],earnings:[],notes:{},predictions:[],focusSessions:[]};
}
async function loadState(){
  if(SERVED){
    try{
      const r=await fetch(API,{cache:"no-store"});
      if(r.ok){const j=await r.json(); state=normalize(j); dataVersion=r.headers.get("X-Data-Version")||dataVersion; cacheLocal(state); loadError=null; return;}
      // 读不到就必须说清楚，否则会静默用旧缓存冒充"最新数据"
      loadError = r.status===401
        ? "服务端拒绝了读取请求（401）：这个链接里没有访问令牌，页面上看到的是<b>浏览器缓存的旧数据</b>。"
        : "读取 /data.json 失败（HTTP "+r.status+"），页面上看到的是<b>浏览器缓存的旧数据</b>。";
    }catch(e){
      loadError="连不上本地同步服务 127.0.0.1:8787，页面上看到的是<b>浏览器缓存的旧数据</b>。";
    }
  }
  const c=localStorage.getItem("wb");
  state = c? normalize(JSON.parse(c)) : defaultState();
}
function cacheLocal(s){try{localStorage.setItem("wb",JSON.stringify(s));}catch(e){}}
function normalize(j){
  j=j||{};
  return {
    version:2,
    owner:j.owner||"我的工作台",
    monthlyGoal:+(j.monthlyGoal)||0,
    notionStatus:j.notionStatus||"local",
    lastNotionSync:j.lastNotionSync||null,
    // 以下 6 个字段必须原样透传：normalize() 的结果会被 saveState() 整体 POST 回 data.json，
    // 漏掉就会被"保存一次即静默抹掉"。earningsReadonly 还控制着只读文案与只读提示；
    // sourceMap 是「备注 → 规范来源」的归并规则（由 scripts/build-source-map.py 生成），
    // 抹掉后来源排行会当场退化成"每种写法各算一个来源"，且不报错；
    // goals 是**按月留痕的目标**（页面自己写的），抹掉后「目标达成史」会全面退化成"未设目标"。
    earningsReadonly:!!j.earningsReadonly,
    earningsSource:j.earningsSource||null,
    projectsSource:j.projectsSource||null,
    notionMeta:j.notionMeta||null,
    sourceMap:j.sourceMap||null,
    goals:j.goals||{},
    // 发布前预测（topic/title + 平台 + 预期档位 + Jev 分），发布后回填真实数据做偏差校准。
    // 与 goals 同级，属于页面自己写的数据；漏透传 = 保存一次即静默清空。
    predictions:Array.isArray(j.predictions)?j.predictions:[],
    /* 专注记录是页面自己写的本地事实，和收益草稿同级；必须原样透传，
       否则任何一次保存都会把历史专注静默清空。 */
    focusSessions:Array.isArray(j.focusSessions)?j.focusSessions.map(s=>({
      id:s.id||uidGen(),
      date:s.date||focusDateStr(s.startAt),
      startAt:s.startAt||null,
      endAt:s.endAt||null,
      durationMinutes:+(s.durationMinutes)||0,
      elapsedSeconds:+(s.elapsedSeconds)||0,
      projectId:s.projectId||"free",
      projectName:s.projectName||"自由专注",
      projectSource:s.projectSource||"local",
      status:s.status==="abandoned"?"abandoned":"completed",
      noteLine:s.noteLine||"",
      noteAddedAt:s.noteAddedAt||null,
      notionPageId:s.notionPageId||null,
      notionUrl:s.notionUrl||"",
      notionPushedAt:s.notionPushedAt||null,
      createdAt:s.createdAt||new Date().toISOString()
    })).filter(s=>s.id):[],
    projects:Array.isArray(j.projects)?j.projects.map(p=>({
      id:p.id||uidGen(),notionId:p.notionId||null,name:p.name||"未命名",status:p.status||"进行中",
      progress:(p.progress===null||p.progress===undefined||p.progress==="")?null:(+(p.progress)||0),
      category:p.category||"",projectType:p.projectType||"",tags:Array.isArray(p.tags)?p.tags:[],
      priority:p.priority||"",start:p.start||"",due:p.due||"",doneDate:p.doneDate||null,
      note:p.note||"",source:p.source||"local",readOnly:!!p.readOnly,updatedAt:p.updatedAt||todayStr(),
      parent:p.parent||null,parentNotionId:p.parentNotionId||null,
      estHours:(typeof p.estHours==="number")?p.estHours:null,
      actHours:(typeof p.actHours==="number")?p.actHours:null,
      sprint:p.sprint||"",sprintStart:p.sprintStart||"",sprintEnd:p.sprintEnd||"",
      owner:p.owner||"",ownerIds:Array.isArray(p.ownerIds)?p.ownerIds:[],
      relatedTaskIds:Array.isArray(p.relatedTaskIds)?p.relatedTaskIds:[],
      sourceRecordIds:Array.isArray(p.sourceRecordIds)?p.sourceRecordIds:[],
      childIds:Array.isArray(p.childIds)?p.childIds:[],
      childCount:(typeof p.childCount==="number")?p.childCount:null,
      relatedLink:p.relatedLink||"",
      blockedBy:Array.isArray(p.blockedBy)?p.blockedBy:[],
      blocks:Array.isArray(p.blocks)?p.blocks:[]
    })):[],
    earnings:Array.isArray(j.earnings)?j.earnings.map(e=>({id:e.id||uidGen(),date:e.date||todayStr(),amount:+(e.amount)||0,type:e.type||"项目收入",projectId:e.projectId||"",note:e.note||"",product:e.product||"",source:e.source||"local",readOnly:!!e.readOnly,updatedAt:e.updatedAt||todayStr()})):[],
    notes:j.notes||{},
    // 客户与跟进记录都由 notion-sync.py 只读生成。原样透传，避免一次保存
    // 就把 Notion 标识、真实状态、负责人、下次跟进等字段静默抹掉。
    clients:Array.isArray(j.clients)?j.clients:null,
    followups:Array.isArray(j.followups)?j.followups:[]
  };
}
async function saveState(){
  if(guest) return;              // 访客模式不写真实数据
  cacheLocal(state);
  if(SERVED){
    try{
      const r=await fetch(API,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(state)});
      if(r.status===409){
        const j=await r.json().catch(()=>({}));
        const field=j.field==="earnings"?"收益记录":"项目";
        notify(`保存被拒绝：${field}数量将从 ${j.before} 条骤降到 ${j.after} 条，疑似误清空。\n数据未被改动。\n\n如确认要这样改，请用 &force=1 重新打开本页再试。`,"warn");
      }else if(!r.ok){
        notify("保存失败：HTTP "+r.status+"，数据未写入。");
      }
    }catch(e){
      notify("保存失败：连不上本地同步服务 127.0.0.1:8787。改动只存在浏览器本地缓存，未进 data.json。");
    }
  }
}
/* 分类选项从真实记录里动态提取，避免与 Notion 的分类体系脱节 */
function typeOptions(){
  const cnt={};
  state.earnings.forEach(e=>{
    const t=e.type;
    if(!t||t===EXPENSE_LABEL) return;
    cnt[t]=(cnt[t]||0)+1;
  });
  const list=Object.keys(cnt).sort((a,b)=>(cnt[b]-cnt[a])||a.localeCompare(b,"zh"));
  if(!list.length) list.push(...DEFAULT_TYPES);   // 仅在完全无数据时兜底，不污染真实分类
  list.push(EXPENSE_LABEL);
  return list;
}
function fillTypeOptions(){
  const sel=$("eType"), keep=sel.value, opts=typeOptions();
  sel.innerHTML=opts.map(t=>`<option>${esc(t)}</option>`).join("");
  sel.value=opts.indexOf(keep)>=0? keep : opts[0];
}

/* ---------- computations ---------- */
function compute(){
  const E=state.earnings, today=todayStr(), ymk=today.slice(0,7);
  const dayAmt=d=>sum(E.filter(e=>e.date===d),e=>e.amount);
  const todayAmt=dayAmt(today);
  const y=yesterday(today); const yAmt=dayAmt(y);
  const ym=lastMonthKey(today), lm=sum(E.filter(e=>e.date.startsWith(ym)),e=>e.amount);
  const cm=sum(E.filter(e=>e.date.startsWith(ymk)),e=>e.amount);
  const lw=sum(E.filter(e=>inLastDays(e.date,7)),e=>e.amount);
  const pw=sum(E.filter(e=>inLastDays(e.date,7,7)),e=>e.amount);
  const active=state.projects.filter(p=>p.status==="进行中").length;
  const withP=state.projects.filter(p=>typeof p.progress==="number");const avg=withP.length?Math.round(sum(withP,p=>p.progress)/withP.length):0;
  const income=sum(E.filter(e=>e.amount>0),e=>e.amount);
  const expense=sum(E.filter(e=>e.amount<0),e=>-e.amount);

  /* 成交笔数：活跃度指标。
     刻意不用「来源数」——备注首段有 204 个不同值（同一来源写法不一致），按它计数会虚高，
     得出一个看着像真的假数字。笔数不依赖任何名称归并，完全可复现。 */
  const cntThis=E.filter(e=>e.date.startsWith(ymk)).length;
  const cntPrev=E.filter(e=>e.date.startsWith(ym)).length;
  const y0=today.slice(0,4), cntByMonth={};
  E.forEach(e=>{const k=e.date.slice(0,7); cntByMonth[k]=(cntByMonth[k]||0)+1;});
  const yKeys=Object.keys(cntByMonth).filter(k=>k.startsWith(y0));
  const cntAvgY=yKeys.length? Math.round(sum(yKeys,k=>cntByMonth[k])/yKeys.length*10)/10 : 0;

  /* 支出分类：支出记录的 type 存的是真实分类（AI 技能 / 投资 / 其他），
     不是一个叫「支出」的统称 —— 所以要按 type 拆开看，才能知道钱花在哪。 */
  const expRows=E.filter(e=>e.amount<0), expByType={};
  expRows.forEach(e=>{const k=e.type||"未分类";
    if(!expByType[k]) expByType[k]={v:0,n:0};
    expByType[k].v+=Math.abs(e.amount); expByType[k].n++;});
  const expList=Object.entries(expByType).map(([k,o])=>({k,v:o.v,n:o.n})).sort((a,b)=>b.v-a.v);
  const expMonth=sum(E.filter(e=>e.date.startsWith(ymk)&&e.amount<0),e=>-e.amount);
  // 近 14 天逐日净额（旧→新），给 KPI sparkline 用；数据源与主图同源，只是窗口更短
  const spark14=[];
  for(let i=13;i>=0;i--){const d=new Date(today);d.setDate(d.getDate()-i);spark14.push(dayAmt(todayStr(d)));}

  return {today:todayAmt,yest:yAmt,month:cm,lastMonth:lm,week:lw,prevWeek:pw,active,avg,spark14,
          withProgress:withP.length,income,expense,total:income-expense,
          cntThis,cntPrev,cntAvgY,expList,expMonth,expKinds:expList.length};
}
function yesterday(t){const d=new Date(t);d.setDate(d.getDate()-1);return todayStr(d);}
function lastMonthKey(t){const d=new Date(t);d.setMonth(d.getMonth()-1);return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0");}
function inLastDays(dateStr,n,offset=0){const d=new Date(dateStr),base=new Date(todayStr());base.setDate(base.getDate()-(n+offset-1));const end=new Date(todayStr());end.setDate(end.getDate()-offset);return d>=base&&d<=end;}

