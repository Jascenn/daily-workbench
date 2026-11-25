/* ---------- notes / theme / guest ---------- */
function saveNote(){if(guest)return;state.notes[todayStr()]=$("note").value;saveState();renderNoteHistory();noteSaveState("已自动保存");}
function saveNoteManual(){
  if(guest){notify("访客模式下不保存备注。","warn");return;}
  state.notes[todayStr()]=$("note").value;
  cacheLocal(state);renderNoteHistory();
  if(!SERVED||TOKEN_MISSING){noteSaveState("仅浏览器缓存，未进 data.json",true);return;}
  noteSaveState("保存中…");
  fetch(API,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(state)})
    .then(r=>{
      if(r.ok){noteSaveState("已保存 ✓");}
      else{noteSaveState("保存失败（HTTP "+r.status+"）",true);notify("备注保存失败：HTTP "+r.status+"，数据未写入。");}
    })
    .catch(e=>{noteSaveState("连不上同步服务，仅本地缓存",true);});
}
/* 保存状态提示：默认 2.5 秒后淡出；出错时保持可见并标红 */
function noteSaveState(txt,err){
  const el=$("noteSaveState"); if(!el) return;
  el.textContent=txt;
  el.classList.toggle("err",!!err);
  el.classList.add("show");
  clearTimeout(el._t);
  el._t=setTimeout(()=>el.classList.remove("show"),err?8000:2500);
}
function toggleTheme(){const r=document.documentElement;r.setAttribute("data-theme",r.getAttribute("data-theme")==="dark"?"light":"dark");localStorage.setItem("theme",r.getAttribute("data-theme"));$("themeBtn").textContent=r.getAttribute("data-theme")==="dark"?"浅色":"深色";}
function toggleGuest(){
  guest=!guest;
  if(guest){
    guestState=state;
    state=guestSample();
    /* 访客隔离：自媒体/资讯快照含真实账号与逐条内容表现，AI 用量含
       个人 token 数 —— 这些都不能给访客看。置空后各渲染函数自己走
       "读不到数据"分支；轮询 loadSections 等也带 guest 检查，不会重新拉。 */
    socialSnapshot=social; social=GUEST_SOCIAL;
    sectionsSnapshot=sections; sections=GUEST_SECTIONS; secErr=""; secNote="访客模式 · 示例快照";
    socialErr=""; socialNote="访客模式 · 示例快照";
    webPresenceSnapshot=webPresence; webPresence=GUEST_WEB; webPresenceErr=""; webPresenceNote="";
    // 微信也必须隔离：访客要能体验待办/机会/咨询，但不能看到本机真实聊天。
    wechatSnapshot=wechat; wechat=GUEST_WECHAT; wechatErr=""; wechatNote="访客模式 · 微信示例快照";
    // 访客切换后若当前就在微信页，立即用 mock 重绘；否则下次进入时也会读到 mock。
    if(curType==="wechat") { renderWechat(); renderTypeBar(); }
    /* 客户名单走独立的 localStorage（wb_clients），不在 state 里 —— 不隔离的话
       访客会看到真实贵人名单。换成示例名单，且退出时原样恢复、不落盘。 */
    clientsSnapshot=CLIENTS; CLIENTS=CLIENTS_SEED.map(c=>c.slice());
    /* 时间页的自定义计时项目同样只存本浏览器：访客态清空展示，
       演示用的“按项目专注”由示例 state.projects 供给。 */
    timeProjectsSnapshot=TIME_PROJECTS; timeBarsSnapshot=TIME_BARS;
    TIME_PROJECTS=[]; TIME_BARS={};
    JEV={enabled:false,lastAnalysis:null,analyzedAt:null};
    jevBtnLabel();
    loadSyncLog();
    $("guestBtn").textContent="退出访客";
    /* 提示灯与横幅同时开：横幅可能被滚出视野，灯常驻在顶部 */
    const lamp=$("guestLamp"); if(lamp) lamp.hidden=false;
    showBanner("guest","访客模式：展示示例数据，不含真实账号、收益与自媒体数据。可随意点按熟悉操作，点「退出访客」恢复。");
  }else{
    state=guestState||state;guestState=null;
    social=socialSnapshot; socialSnapshot=null; socialErr=""; socialNote="";
    sections=sectionsSnapshot; sectionsSnapshot=null; secErr=""; secNote="";
    webPresence=webPresenceSnapshot; webPresenceSnapshot=null; webPresenceErr=""; webPresenceNote="";
    wechat=wechatSnapshot; wechatSnapshot=null; wechatErr=""; wechatNote="";
    if(curType==="wechat") { renderWechat(); renderTypeBar(); }
    CLIENTS=clientsSnapshot||CLIENTS_SEED.map(c=>c.slice()); clientsSnapshot=null;
    TIME_PROJECTS=timeProjectsSnapshot||[]; timeProjectsSnapshot=null;
    TIME_BARS=timeBarsSnapshot||{}; timeBarsSnapshot=null;
    jevLoad();
    loadSyncLog();
    $("guestBtn").textContent="访客模式";
    const lamp=$("guestLamp"); if(lamp) lamp.hidden=true;
    hideBanner();
  }
  renderAll();
}
function guestSample(){return normalize({owner:"示例用户（访客）",monthlyGoal:33000,notionStatus:"local",lastNotionSync:null,
  projects:[
    /* 示例数据要能把每个视图都喂饱：日历（密集截止日）、树（三级父子）、
       依赖（有阻塞链）、工时（有超支也有结余）、交付节奏（多个月完成）。
       全部标「示例：」，与真实数据靠 owner/来源区分，不冒充真实口径。 */
    {id:"s01",name:"示例：官网改版",status:"进行中",progress:65,category:"设计",priority:"P1 - 高",
     due:gDateAgo(3),start:gDateAgo(26),estHours:40,actHours:52,note:"示例：首页已完成，进入内容迁移。"},
    {id:"s02",name:"示例：客户小程序",status:"进行中",progress:40,category:"开发",priority:"P0 - 最高",
     due:gDateAgo(-8),start:gDateAgo(19),estHours:120,actHours:46,parent:"s01",
     blockedBy:["s03"],note:"示例：接口联调中，等设计稿定稿。"},
    {id:"s03",name:"示例：品牌视觉定稿",status:"进行中",progress:80,category:"设计",priority:"P0 - 最高",
     due:gDateAgo(1),start:gDateAgo(23),estHours:24,actHours:30,blocks:["s02"],
     note:"示例：主视觉已定，等客户确认辅助图形。"},
    {id:"s04",name:"示例：内部工具",status:"已延迟",progress:25,category:"工具",priority:"P2 - 中",
     due:gDateAgo(15),start:gDateAgo(41),estHours:60,actHours:18,note:"示例：需求还没收口。"},
    {id:"s05",name:"示例：自动化脚本",status:"进行中",progress:82,category:"自动化",priority:"P2 - 中",
     due:gDateAgo(-4),start:gDateAgo(17),estHours:16,actHours:14,note:"示例：只剩告警通知。"},
    {id:"s06",name:"示例：数据看板",status:"未开始",progress:0,category:"开发",priority:"P3 - 低",
     due:gDateAgo(-20),start:gDateAgo(-2),estHours:32,blockedBy:["s04"],note:"示例：等内部工具接口稳定。"},
    {id:"s07",name:"示例：博客迁移",status:"已完成",progress:100,category:"内容",priority:"P2 - 中",
     due:gDateAgo(23),doneDate:gDateAgo(21),estHours:20,actHours:17,note:"示例：已上线。"},
    {id:"s08",name:"示例：季度复盘",status:"已完成",progress:100,category:"运营",priority:"P1 - 高",
     due:gDateAgo(13),doneDate:gDateAgo(12),estHours:8,actHours:6},
    {id:"s09",name:"示例：客户培训",status:"已完成",progress:100,category:"服务",priority:"P2 - 中",
     due:gDateAgo(11),doneDate:gDateAgo(10),estHours:12,actHours:15},
    {id:"s10",name:"示例：工具站重构",status:"已完成",progress:100,category:"开发",priority:"P1 - 高",
     due:gDateAgo(46),doneDate:gDateAgo(45),estHours:80,actHours:72},
    {id:"s11",name:"示例：内容排期系统",status:"已完成",progress:100,category:"自动化",priority:"P2 - 中",
     due:gDateAgo(78),doneDate:gDateAgo(77),estHours:36,actHours:40},
    {id:"s12",name:"示例：旧活动页",status:"已取消",progress:30,category:"运营",priority:"P4 - 最低",
     due:gDateAgo(16),note:"示例：活动取消，页面不再维护。"},
    {id:"s13",name:"示例：早期脚本整理",status:"已完成",progress:100,category:"自动化",priority:"P3 - 低",
     due:gDateAgo(98),doneDate:gDateAgo(95),estHours:10,actHours:9},
    {id:"s14",name:"示例：课程大纲",status:"进行中",progress:55,category:"内容",priority:"P1 - 高",
     due:gDateAgo(-2),start:gDateAgo(13),estHours:28,actHours:12,parent:"s02",
     note:"示例：大纲已过一遍，等小程序交付后拍演示。"}
  ],
  /* 收益 40 条：过去约 5 个月收入/支出交错，“今天”固定有一笔入账，
     保证本日净额、近 21 天窗口、月度目标带和归并口径都有东西可看。 */
  earnings:(()=>{
    const rows=[
      [158,4200,"项目收入","示例：官网一期款"],[152,-360,"AI 技能","示例：模型订阅"],
      [145,980,"咨询","示例：快速诊断"],[141,1500,"知识付费","示例：素材包"],
      [137,1200,"咨询","示例：顾问日"],[128,2400,"知识付费","示例：工作坊"],
      [126,3200,"项目收入","示例：工具站定制"],[113,-820,"投资","示例：工具采购"],
      [104,1800,"咨询","示例：方案评审"],[99,1200,"咨询","示例：线上答疑"],
      [96,5200,"项目收入","示例：官网二期款"],[88,-420,"AI 技能","示例：API 用量"],
      [84,1400,"知识付费","示例：模板包"],[80,2100,"知识付费","示例：工作坊回放"],
      [76,2800,"项目收入","示例：脚本定制"],[72,3600,"项目收入","示例：内部工具一期"],
      [65,-520,"投资","示例：云服务"],[61,1500,"咨询","示例：架构诊断"],
      [58,2600,"咨询","示例：评审会"],[54,3200,"项目收入","示例：看板原型"],
      [50,2400,"知识付费","示例：模板更新"],[47,-260,"AI 技能","示例：转写用量"],
      [43,7400,"项目收入","示例：内部工具二期"],[40,2200,"知识付费","示例：训练营"],
      [36,-320,"AI 技能","示例：日志分析订阅"],[33,2600,"咨询","示例：方案诊断"],
      [30,1800,"咨询","示例：季度评审"],[25,1500,"知识付费","示例：专栏订阅"],
      [22,4200,"项目收入","示例：自动化一期"],[19,1800,"咨询","示例：评审"],
      [16,2400,"知识付费","示例：工作坊二期"],[13,5600,"项目收入","示例：数据看板一期"],
      [11,-480,"投资","示例：域名与备案"],[9,2600,"知识付费","示例：模板包"],
      [7,3600,"项目收入","示例：自动化二期"],[5,1400,"咨询","示例：方案诊断"],
      [3,-360,"AI 技能","示例：模型用量"],[2,4200,"项目收入","示例：数据看板二期"],
      [1,1200,"咨询","示例：快速诊断"],
      [0,2800,"知识付费","示例：模板包更新"]
    ];
    return rows.map(([d,a,t,nt])=>({date:gDateAgo(d),amount:a,type:t,note:nt}));
  })(),
  goals:(()=>{const g={};[9000,8000,9000,12000,12000].forEach((v,i)=>{g[gMonthAgo(5-i)]=v;});
    g[gMonthAgo(0)]=33000;return g;})(),
  /* 专注足迹：过去 14 天大多 1-2 个完成番茄、留两天空白，趋势图是“真的在用”
     的形状，而不是只有今天一根柱子；今天的 2 完成 + 1 放弃追加在后面。 */
  focusSessions:(()=>{
    const names=["示例：官网改版","示例：客户小程序","示例：品牌视觉定稿","示例：课程大纲"];
    const plan=[[1,1,25],[2,2,25],[3,1,45],[4,1,25],[6,2,25],[7,1,25],[8,1,45],[9,1,25],[11,1,45],[12,1,25],[13,1,25]];
    const rows=[];
    plan.forEach(([d,cnt,min],pi)=>{for(let k=0;k<cnt;k++){rows.push({
      id:"guest-h"+d+"-"+k,date:gDateAgo(d),
      startAt:new Date(Date.now()-d*86400000-(5-k)*3600000).toISOString(),
      endAt:new Date(Date.now()-d*86400000-(5-k)*3600000+min*60000).toISOString(),
      durationMinutes:min,elapsedSeconds:min*60,
      projectId:"local:s0"+((pi%4)+1),projectName:names[pi%4],projectSource:"local",status:"completed"});}});
    return rows;
  })().concat([
    {id:"guest-focus-1",date:focusDateStr(),startAt:new Date(Date.now()-95*60000).toISOString(),endAt:new Date(Date.now()-70*60000).toISOString(),
     durationMinutes:25,elapsedSeconds:1500,projectId:"local:s01",projectName:"示例：官网改版",projectSource:"local",status:"completed"},
    {id:"guest-focus-2",date:focusDateStr(),startAt:new Date(Date.now()-45*60000).toISOString(),endAt:new Date(Date.now()-20*60000).toISOString(),
     durationMinutes:25,elapsedSeconds:1500,projectId:"local:s02",projectName:"示例：客户小程序",projectSource:"local",status:"completed"},
    {id:"guest-focus-3",date:focusDateStr(),startAt:new Date(Date.now()-10*60000).toISOString(),endAt:new Date().toISOString(),
     durationMinutes:25,elapsedSeconds:420,projectId:"local:s03",projectName:"示例：品牌视觉定稿",projectSource:"local",status:"abandoned"}
  ]),
  /* 打卡备注补上最近两天：连续记录不是 1 天空起步，时间线也有内容可看。 */
  notes:{[gDateAgo(1)]:"示例：把数据看板二期的验收标准发给客户了，等反馈。",
         [gDateAgo(2)]:"示例：自动化二期的告警通知联调完成，只剩文档。"}});}

/* ---------- export / import ---------- */
/* 导出 CSV 给 Excel / 数字表格用。带 BOM，否则 Excel 打开中文是乱码。
   特意多导一列「归并来源」—— 这样归并结果能拿到外部去核对，不用只在页面上看。 */
function csvCell(v){
  const s=(v===null||v===undefined)?"":String(v);
  return /[",\n\r]/.test(s) ? '"'+s.replace(/"/g,'""')+'"' : s;
}
/* 构建 CSV 的表头与数据行（抽出来是为了能单独断言内容，不必真去下载文件） */
function csvRows(){
  const hasMap=!!(state.sourceMap&&(state.sourceMap.rules||[]).length);
  const head=["日期","金额","方向","分类","归并层级","归并来源","备注第一段","项目","详细备注","来源"];
  const rows=state.earnings.slice()
    .sort((a,b)=>(a.date<b.date?1:(a.date>b.date?-1:0)))
    .map(e=>{
      const seg=firstSeg(e.note)||"";
      // ⚠️ 金额必须导出**原始精度**，不能 toFixed(2)。
      // 逐行四舍五入后再求和会漂：收入 26958.487 → 26958.50（差 1.3 分），
      // 用户拿这张表在 Excel 里 SUM，会得到与 Notion 不一致的数，然后再来问为什么对不上。
      // 显示格式交给 Excel，数据精度留给我们。
      const so=(e.amount>0&&hasMap)? sourceOf(e) : null;
      const p=e.projectId? (state.projects.find(x=>x.id===e.projectId)||{}).name||"" : "";
      // 方向必须三值：金额为 0 的记录既不是收入也不是支出。
      // 只用 `amount<0 ? 支出 : 收入` 会把它标成「收入」，可它的来源列是空的 —— 标签自相矛盾。
      const dir = e.amount<0? "支出" : (e.amount>0? "收入" : "零额");
      return [e.date, String(e.amount), dir, e.type||"",
              so? (so.merged? (so.kind==="B1"?"层B1":"层A") : "长尾") : "",
              so? so.name : "",
              seg, p, e.note||"", e.source||""];
    });
  return {head:head, rows:rows, hasMap:hasMap};
}
function buildCSV(){
  const c=csvRows();
  // BOM 必须有，否则 Excel 打开中文是乱码
  return {text:"\uFEFF"+[c.head].concat(c.rows).map(r=>r.map(csvCell).join(",")).join("\r\n"),
          n:c.rows.length, hasMap:c.hasMap};
}
function exportCSV(){
  const c=buildCSV();
  const blob=new Blob([c.text],{type:"text/csv;charset=utf-8"});
  const a=document.createElement("a");
  a.href=URL.createObjectURL(blob);
  a.download="收益明细-"+todayStr()+".csv";
  a.click();
  showBanner("online", `已导出 ${c.n} 条为 CSV`+(c.hasMap? "（含「归并来源」列）":""));
  setTimeout(hideBanner, 4000);
}
function exportJSON(){
  const blob=new Blob([JSON.stringify(state,null,2)],{type:"application/json"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="workbench-"+todayStr()+".json";a.click();
}
function importJSON(ev){const f=ev.target.files[0];if(!f)return;const rd=new FileReader();
  rd.onload=()=>{try{state=normalize(JSON.parse(rd.result));saveState();renderAll();hideBanner();}catch(e){notify("导入失败："+e.message);}};
  rd.readAsText(f);ev.target.value="";}

/* ---------- banner / status ---------- */
/* 用 innerHTML 而非 textContent：auth 横幅需要 <b>/<br>/<code> 才能把修复动作说清楚。
   所有调用点传的都是本文件里的静态文案，不含用户输入。 */
function showBanner(cls,msg){const b=$("banner");b.className="banner show "+cls;b.innerHTML=msg;}
function hideBanner(){$("banner").className="banner";$("banner").textContent="";}

/* ---------- 常驻提示 / 撤销 ---------- */
/* 数据完整性类消息不能只弹一次。
   旧写法用 alert()：「保存失败，数据未写入」弹一下，点掉就没了 —— 一旦被错过，
   用户会以为已经存上。这是账本，不能靠记忆。所以做成页内常驻、可复制、显式关闭的条。
   level: "err"（默认，红）/ "warn"（琥珀）/ "info"（蓝）。 */
function notify(msg,level){
  const b=$("notify"); if(!b) return;
  b.className="notify show "+(level||"err");
  b.innerHTML=`<span class="ntxt">${esc(msg).replace(/\n/g,"<br>")}</span>`+
              `<button class="nx" onclick="dismissNotify()" aria-label="关闭提示">✕</button>`;
}
function dismissNotify(){const b=$("notify");if(b){b.className="notify";b.innerHTML="";}}

/* 删除可撤销：账本删错一笔直接影响净收益，而"弹窗确认"既打断又救不回来。
   所以暂存 {记录, 原索引}，撤销时按原索引插回 —— 不改变其余记录的顺序。 */
let lastDel=null, delTimer=null;
function showUndo(e){
  const b=$("undo"); if(!b) return;
  const seg=firstSeg(e.note)||"";
  b.className="undo show";
  b.innerHTML=`<span>已删除 ${esc(e.date.slice(5))} ${fmt(e.amount)}${seg?(" · "+esc(seg)):""}</span>`+
              `<button onclick="undoDel()">撤销</button>`;
  if(delTimer) clearTimeout(delTimer);
  delTimer=setTimeout(hideUndo, 8000);
}
function hideUndo(){
  const b=$("undo"); if(b){b.className="undo";b.innerHTML="";}
  if(delTimer){clearTimeout(delTimer);delTimer=null;}
  lastDel=null;
}
function undoDel(){
  if(!lastDel) return;
  const rec=lastDel.rec, idx=lastDel.idx;
  state.earnings.splice(Math.min(idx,state.earnings.length),0,rec);
  const b=$("undo"); if(b){b.className="undo";b.innerHTML="";}
  if(delTimer){clearTimeout(delTimer);delTimer=null;}
  lastDel=null;
  saveState();renderAll();
  notify(`已恢复 ${fmt(rec.amount)}（${rec.date.slice(5)}）。`,"info");
}

/* 字段内联错误：校验信息应该出现在出错的那个字段旁边，而不是全局弹窗 */
function fieldErr(id,msg){
  const el=$(id); if(!el) return;
  const box=el.closest(".field")||el.parentNode;
  let e=box.querySelector(".ferr");
  if(!e){e=document.createElement("div");e.className="ferr";box.appendChild(e);}
  e.textContent=msg;
  el.setAttribute("aria-invalid","true");
}
function clearFieldErr(id){
  const el=$(id); if(!el) return;
  el.removeAttribute("aria-invalid");
  const box=el.closest(".field")||el.parentNode;
  const e=box&&box.querySelector(".ferr");
  if(e) e.textContent="";
}

/* 键盘可达：点击目标里有 3 处是整块可点的 <div>（来源行 / 体检行 / 项目卡），
   它们不是原生按钮，只在各处加 tabindex+role 并不够 —— 还得有人处理 Enter/Space。
   与其在四个模板里各写一遍 keydown，不如在这里做一次事件委托：一份实现，四处生效。
   真 <button> 不在此列：浏览器自己会处理 Enter/Space，重复处理会导致触发两次。

   注：账本 <tr> 故意**没有**加进可聚焦列表。它每行已经带了 ✎/✕ 两个真按钮，
   「点行=编辑」只是鼠标用户的便利路径；给 257 行都加一个 Tab 停靠点，
   键盘用户反而要按几百次 Tab 才能穿过去。 */
document.addEventListener("keydown",e=>{
  if(e.key!=="Enter" && e.key!==" " && e.key!=="Spacebar") return;
  const el=e.target;
  if(!el || !el.getAttribute) return;
  const tag=el.tagName;
  if(tag==="BUTTON"||tag==="INPUT"||tag==="SELECT"||tag==="TEXTAREA"||tag==="A") return;
  if(el.getAttribute("role")!=="button" || el.tabIndex!==0) return;
  e.preventDefault();          // Space 默认会滚动页面
  el.click();
});
function applyTheme(){const t=localStorage.getItem("theme")||"light";document.documentElement.setAttribute("data-theme",t);$("themeBtn").textContent=t==="dark"?"浅色":"深色";}

/* ---------- 命令面板（Cmd/Ctrl+K）----------
   跳页签 / 直达动作 / 搜账目。账目搜索只是把关键词填进总览的搜索框并过滤，
   不另建第二套过滤逻辑 —— 过滤口径永远只有 ledgerFilters() 一份。 */
let cmdItems=[],cmdActive=0;
function cmdCommands(){
  const nav=TYPES.map(([id,label])=>({label:"跳到「"+label+"」",hint:"页签",run:()=>setType(id)}));
  const acts=guest?[]:[
    {label:"打开微信批奏折",hint:"直达",run:()=>{setType("wechat");setWxView("brief");}},
    {label:"展开星球简报详版",hint:"直达",run:()=>{setType("zsxq");openZsxqDigestModal();}},
    {label:"记一笔收益",hint:"动作",run:openEarn},
    {label:"添加项目",hint:"动作",run:openProj},
    {label:"立即同步 Notion",hint:"动作",run:runSync},
    {label:"管理订阅源",hint:"动作",run:openFeedManage},
    {label:(JEV.enabled?"关闭":"开启")+" Jev 增强",hint:"动作",run:toggleJev},
    {label:"导出 CSV",hint:"动作",run:exportCSV},
  ];
  const misc=[
    {label:document.documentElement.getAttribute("data-theme")==="dark"?"切换到浅色模式":"切换到深色模式",hint:"外观",run:toggleTheme},
    {label:guest?"退出访客模式":"进入访客模式",hint:"外观",run:toggleGuest},
  ];
  return [...nav,...acts,...misc];
}
function cmdQuery(q){
  const s=(q||"").trim().toLowerCase();
  const cmds=cmdCommands().filter(c=>!s||c.label.toLowerCase().includes(s));
  let earn=[];
  if(s&&!guest&&state&&state.earnings&&s.length>=2){
    earn=state.earnings.filter(e=>[e.note,e.type,e.product].join(" ").toLowerCase().includes(s))
      .sort((a,b)=>(a.date<b.date?1:(a.date>b.date?-1:0))).slice(0,6)
      .map(e=>({label:`${e.date} · ${fmt(e.amount)} · ${(e.note||e.type||"").slice(0,24)}`,hint:"账目",
        run:()=>{setType("ledger");const q2=$("ledQ");if(q2){q2.value=s;}renderLedger();}}));
  }
  return [...cmds,...earn];
}
function renderCmd(){
  const box=$("cmdList");
  if(!cmdItems.length){
    box.innerHTML=`<div class="cmd-item" style="cursor:default;color:var(--muted)">没有匹配的命令或账目</div>`;
    return;
  }
  box.innerHTML=cmdItems.map((c,i)=>
    `<button type="button" class="cmd-item${i===cmdActive?" active":""}" data-i="${i}">`+
    `<span>${esc(c.label)}</span><span class="ci-hint">${esc(c.hint)}</span></button>`).join("");
  const act=box.querySelector(".cmd-item.active");
  if(act) act.scrollIntoView({block:"nearest"});
}
function openCmd(){
  openOv("cmdOv"); cmdActive=0; $("cmdInput").value=""; cmdItems=cmdQuery(""); renderCmd();
  setTimeout(()=>{try{$("cmdInput").focus();}catch(e){}},0);
}
function closeCmd(){ closeOv("cmdOv"); }
function runCmd(i){
  const c=cmdItems[i]; if(!c) return;
  closeCmd();
  try{c.run();}catch(e){notify("执行失败："+e.message,"warn");}
}
$("cmdInput").addEventListener("input",()=>{cmdActive=0;cmdItems=cmdQuery($("cmdInput").value);renderCmd();});
$("cmdInput").addEventListener("keydown",e=>{
  if(e.key==="ArrowDown"){e.preventDefault();cmdActive=Math.min(cmdItems.length-1,cmdActive+1);renderCmd();}
  else if(e.key==="ArrowUp"){e.preventDefault();cmdActive=Math.max(0,cmdActive-1);renderCmd();}
  else if(e.key==="Enter"){e.preventDefault();runCmd(cmdActive);}
});
$("cmdList").addEventListener("click",e=>{
  const b=e.target.closest(".cmd-item"); if(!b||b.dataset.i===undefined) return;
  runCmd(+b.dataset.i);
});
$("cmdList").addEventListener("mousemove",e=>{
  const b=e.target.closest(".cmd-item"); if(!b||b.dataset.i===undefined) return;
  const i=+b.dataset.i; if(i!==cmdActive){cmdActive=i;renderCmd();}
});
/* 内容表现折叠状态记住用户的习惯：展开/收起各存一次，刷新不回弹 */
try{
  const pf=$("perfFold"), saved=localStorage.getItem("wb_perf_fold");
  if(saved==="0") pf.open=false;
  pf.addEventListener("toggle",()=>{try{localStorage.setItem("wb_perf_fold",pf.open?"1":"0");}catch(e){}});
}catch(e){}

