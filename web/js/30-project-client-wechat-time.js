/* ═══════════════════════════════════════════════════════════════════════════
   项目直接读 state.projects；客户直接读 state.clients（未连接时回退本地试验）；
   时间走本地试验数据；自媒体读 social.json，
   只有“今日人工核验状态”写独立 localStorage。四类都不进 sections.json。 */
const NEWTYPES=["project","client","wechat","time","content","zsxq"];
const NEWHINTS={
  client:"Notion 贵人计划只读接入 · 81 条真实数据",
  time:"本地试验区 · 番茄钟与工时只存本浏览器",
  wechat:"微信 MCP 只读快照 · 不发送、不改会话 · 待回复按最后一条推断",
  content:"X / 小红书公开数据已接入 · 知识星球历史导入 + 每日核验 · 公众号指标待接"
};
function loadNew(key,seed){
  try{const v=localStorage.getItem(key); if(v) return JSON.parse(v);}catch(e){}
  return seed;
}function saveNew(key,val){ try{localStorage.setItem(key,JSON.stringify(val));}catch(e){} }
function seedNew(type){
  if(type==="client"){ CLIENTS=CLIENTS_SEED.map(c=>c.slice()); saveNew("wb_clients",CLIENTS); renderClient(); }
  else if(type==="time"){ TIME_PROJECTS=[]; TIME_BARS={}; saveNew("wb_time_projects",TIME_PROJECTS); saveNew("wb_time_bars",TIME_BARS); renderTime(); }
}

/* —— 项目：直接消费 state.projects，Notion 行只展示、不提供写入口 —— */
let projectScope="active";
let projectView="board";
let calMonth=null;   /* 日历视图当前月：YYYY-MM，null=今天所在月 */
const PROJ_COLS=["进行中","未开始","已延迟","其他","已完成","已取消"];
function setProjectView(v){
  projectView=(v==="tree")?"tree":(v==="calendar")?"calendar":"board";
  const b=$("pjViewBoard"), t=$("pjViewTree"), c=$("pjViewCal");
  if(b) b.classList.toggle("on",projectView==="board");
  if(t) t.classList.toggle("on",projectView==="tree");
  if(c) c.classList.toggle("on",projectView==="calendar");
  renderProject();
}
function setProjectScope(k){
  projectScope=(k==="all"||k==="done"||k==="cancelled")?k:"active";
  renderProject();
}
function projectCol(p){ return PROJ_COLS.includes(p.status)?p.status:"其他"; }
function projectConflict(p){
  const n=(p.progress===null||p.progress===undefined||p.progress==="")?null:+p.progress;
  if(p.status==="已取消") return false;
  return n!==null && ((p.status==="未开始"&&n>=100)||(p.status==="进行中"&&n>=100)||(p.status==="已完成"&&n<100));
}
function projectConflictText(p){
  const n=(p.progress===null||p.progress===undefined||p.progress==="")?null:+p.progress;
  if(p.status==="进行中"&&n>=100) return "进度已 100%，请到 Notion 确认是否完成";
  if(p.status==="未开始"&&n>=100) return "未开始但进度已 100%，请到 Notion 核对";
  if(p.status==="已完成"&&n<100) return `已完成但进度仅 ${n}%，请到 Notion 核对`;
  return "状态与进度冲突，请到 Notion 核对";
}
function projectRisk(p){
  if(!p||p.status==="已完成"||p.status==="已取消"||!/^\d{4}-\d{2}-\d{2}$/.test(p.due||"")) return null;
  const base=new Date(todayStr()+"T00:00:00"), end=new Date(p.due+"T00:00:00");
  const d=Math.round((end-base)/86400000);
  /* 逾期分档：拖过 90 天的不再和"刚逾期一天"并列。
     这种项目通常已经没人推进或事实停摆，提示语必须不同，否则真正紧急的那条会被埋掉。
     判定阈值是「逾期天数」不是「状态」——已完成项目在上面就返回了，不会误伤。 */
  if(d<0) return {days:d,cls:"over",rk:d<-90?"长期挂起":"逾期",txt:`已逾期 ${Math.abs(d)} 天`,stale:d<-90};
  if(d<=1) return {days:d,cls:"today",rk:"紧迫",txt:d===0?"今天截止":"明天截止"};
  if(d<=3) return {days:d,cls:"soon",rk:"临期",txt:`${d} 天后截止`};
  return null;
}
/* 优先级排序权重：Notion 里是「P0 - 最高」这种带说明的字符串，
   只认前缀 P0~P4。空值排最后，但不从列表里删掉（缺优先级是数据问题，不该藏起来）。 */
function prioRank(p){
  const m=/^P([0-4])\b/.exec(String((p&&p.priority)||"").trim());
  return m?+m[1]:9;
}
/* 徽标只显示 P0~P4 前缀，不带 Notion 里的「- 最高」尾巴 —— 卡片行高有限。 */
function prioLabel(p){
  const raw=String((p&&p.priority)||"").trim();
  const m=/^(P[0-4])\b/.exec(raw);
  return m?m[1]:"";
}
function realProjectCard(p){
  const pct=(p.progress===null||p.progress===undefined||p.progress==="")?null:Math.max(0,Math.min(100,+p.progress||0));
  const conflict=projectConflict(p), risk=projectRisk(p);
  const cls=projectCol(p), badgeCls=cls==="其他"?"待启动":cls;
  const source=p.readOnly?"Notion 可回写":"本地草稿";
  const due=p.due?`截止 ${esc(p.due)}`:"无截止日期";
  const pl=prioLabel(p);
  /* 工时徽标：填了才显示；超支红/结余绿，只填一个就只显示那一个 */
  const eh=(typeof p.estHours==="number")?p.estHours:null, ah=(typeof p.actHours==="number")?p.actHours:null;
  const hoursChip=(eh!==null&&ah!==null)?`<span class="hchip${ah>eh?" over":" ok"}">${ah}h / ${eh}h</span>`
    :(ah!==null?`<span class="hchip">${ah}h</span>`:(eh!==null?`<span class="hchip">预计 ${eh}h</span>`:""));
  const noteMark=p.note?`<span class="notemark" title="${esc(p.note)}">✎</span>`:"";
  const category=p.projectType||p.category||"未分类";
  const tags=(p.tags||[]).filter(Boolean);
  const tagLine=tags.length?`<span class="cat" title="${esc(tags.join("、"))}">${esc(tags.slice(0,2).join("、"))}${tags.length>2?` +${tags.length-2}`:""}</span>`:"";
  const extras=[];
  if(p.sprint) extras.push(`冲刺 ${p.sprint}`);
  if(p.owner) extras.push(`负责人 ${p.owner}`);
  if(Number.isFinite(+p.childCount)&&+p.childCount>0) extras.push(`${p.childCount} 个子项`);
  // 关联任务是「项目页 关联任务」relation，和任务侧的「所属项目」是两条关系，
  // 所以数量只能看这一侧；没挂任务就不显示，写 0 会像是"任务全丢了"。
  const taskN=(p.relatedTaskIds||[]).length;
  if(taskN>0) extras.push(`${taskN} 个关联任务`);
  const relatedLink=safeUrl(p.relatedLink||"");
  return `<article class="proj${conflict?" conflict":""}${risk&&risk.days<0?" overdue":""}${risk&&risk.stale?" stale":""}" data-pid="${esc(p.id)}">
    <div class="top"><span class="nm">${pl?`<span class="prio prio-${pl.toLowerCase()}">${pl}</span> `:""}${esc(p.name)}</span><span class="badge b-${badgeCls}">${esc(p.status||"其他")}</span></div>
    <div class="pbar"><i style="width:${pct===null?0:pct}%"></i></div>
    <div class="meta"><span>${esc(category)}${tagLine?` ${tagLine}`:""}${noteMark}</span><span>进度 ${pct===null?"—":pct+"%"} · ${due}${hoursChip?" · "+hoursChip:""}</span></div>
    ${extras.length?`<div class="projnote">${esc(extras.join(" · "))}</div>`:""}
    ${p.note?`<div class="projnote">${esc(p.note)}</div>`:""}
    <div class="srcmeta"><span class="chip ${p.readOnly?"acc":"warn"}">${source}</span>${conflict?`<span class="conflictchip">状态与进度冲突</span>`:""}
      ${relatedLink?`<a class="notionlink" href="${esc(relatedLink)}" target="_blank" rel="noopener noreferrer">相关链接 ↗</a>`:""}
      ${p.source==="notion"&&p.notionId?`<a class="notionlink" href="https://app.notion.com/${esc(String(p.notionId).replace(/-/g,""))}" target="_blank" rel="noopener" onclick="event.stopPropagation()" aria-label="在 Notion 中打开 ${esc(p.name)}">Notion ↗</a>`:""}</div>
    ${p.readOnly
      ?`<div class="pacts"><button class="miniact" type="button" onclick="openProjectWriteback('${esc(p.id)}')">编辑并回写</button></div>`
      :`<div class="pacts"><button class="miniact" onclick="editProject('${esc(p.id)}')">编辑本地项目</button></div>`}
  </article>`;
}
function renderProject(){
  const all=(state&&state.projects)||[], q=($("projQ")&&$("projQ").value||"").trim().toLowerCase();
  const active=all.filter(p=>!(p.status==="已完成"||p.status==="已取消"));
  const completed=all.filter(p=>p.status==="已完成"), cancelled=all.filter(p=>p.status==="已取消");
  const ended=all.filter(p=>p.status==="已完成"||p.status==="已取消");
  const overdue=all.filter(p=>{const r=projectRisk(p);return r&&r.days<0;});
  const conflicts=all.filter(projectConflict), noDue=active.filter(p=>!p.due).length;
  const attention=all.filter(p=>projectRisk(p)||projectConflict(p)).length;
  $("projStats").innerHTML=statCells([
    {k:"当前项目",v:active.length,hint:`共 ${all.length} 个 · ${ended.length} 个已结束`},
    {k:"需要处理",v:attention,hint:"逾期或状态冲突"},
    {k:"已逾期",v:overdue.length,hint:"有截止日期"},{k:"数据冲突",v:conflicts.length,hint:"状态 × 进度"}]);
  const ps=(state.projectsSource&&state.projectsSource.name)||"项目管理";
  const ls=state.lastNotionSync?new Date(state.lastNotionSync).toLocaleString("zh-CN"):"从未";
  $("projSourceBar").innerHTML=`<b>${esc(ps)}</b> · Notion 可回写 · 最近同步 ${esc(ls)}`;
  $("projSourceBar").title=$("projSourceBar").textContent.trim();
  const pool=(projectScope==="done"?completed:(projectScope==="cancelled"?cancelled:(projectScope==="all"?all:active))).filter(p=>
    !q||[p.name,p.projectType,p.category,(p.tags||[]).join(" "),p.status,p.owner,p.sprint,p.note]
      .some(v=>String(v||"").toLowerCase().includes(q)));
  [["pjActive",active.length],["pjAll",all.length],["pjDone",completed.length],["pjCancelled",cancelled.length]].forEach(([id,n])=>{
    const b=$(id); if(!b) return; const key=id==="pjAll"?"all":id==="pjDone"?"done":id==="pjCancelled"?"cancelled":"active";
    b.classList.toggle("on",projectScope===key); b.setAttribute("aria-pressed",projectScope===key?"true":"false");
    b.textContent=(key==="active"?"当前":key==="done"?"已完成":key==="cancelled"?"已取消":"全部")+" "+n;
  });
  $("projBoardTag").textContent=`显示 ${pool.length} / ${all.length}`;
  const boardEl=$("projBoard");
  if(projectView==="tree"){ boardEl.className=""; renderProjectTree(pool); return; }
  if(projectView==="calendar"){ boardEl.className=""; renderProjectCalendar(pool); return; }
  const projectColumn=(col,rows)=>{
    /* 列内排序：优先级 → 逾期天数 → 名称。
       同一列里不排序时，P0 的活会被几张顺手记下的 P3 卡压在下面。 */
    const items=rows.filter(p=>projectCol(p)===col).sort((a,b)=>
      prioRank(a)-prioRank(b) || String(a.name).localeCompare(String(b.name),"zh"));
    return `<div class="kcol ${items.length?"filled":"zero"}"><div class="khead"><span>${col}</span><span class="kc">${items.length}</span></div>`+
      (items.length?items.map(realProjectCard).join(""):`<div class="empty" style="border:0;padding:6px 2px">—</div>`)+`</div>`;
  };
  if(projectScope==="all"){
    /* 全部视图：活动列全展开；已取消收折叠组；已完成按交付月分组折叠——
       20 张已完成卡平铺会把看板拉到三屏长，按月折叠后复盘节奏也一目了然。 */
    const cancelPool=pool.filter(p=>p.status==="已取消"), donePool=pool.filter(p=>p.status==="已完成");
    const doneMonths={};
    donePool.forEach(p=>{
      const m=String(p.doneDate||p.updatedAt||"0000-00").slice(0,7);
      (doneMonths[m]=doneMonths[m]||[]).push(p);
    });
    const monthGroups=Object.keys(doneMonths).sort().reverse().map(m=>{
      const rows=doneMonths[m];
      return `<details class="completed-group"><summary>${m} 交付 ${rows.length} 个<span>按需查看</span></summary>`+
        `<div class="completed-list">${projectColumn("已完成",rows)}</div></details>`;
    }).join("");
    boardEl.className="kanban";
    boardEl.innerHTML=PROJ_COLS.filter(col=>col!=="已完成"&&col!=="已取消").map(col=>projectColumn(col,pool)).join("")+
      (cancelPool.length?`<details class="completed-group"><summary>已取消 ${cancelPool.length} 个<span>按需查看</span></summary>`+
        `<div class="completed-list">${projectColumn("已取消",cancelPool)}</div></details>`:"")+
      monthGroups;
  }else{
    let cols=projectScope==="done"?["已完成"]:(projectScope==="cancelled"?["已取消"]:PROJ_COLS.filter(col=>col!=="已完成"));
    const nonEmpty=cols.filter(col=>pool.some(p=>projectCol(p)===col));
    const singleFilled=projectScope==="active"&&!q&&nonEmpty.length===1;
    if(singleFilled) cols=nonEmpty;
    boardEl.className="kanban"+(singleFilled?" single-filled":"");
    boardEl.innerHTML=cols.map(col=>projectColumn(col,pool)).join("");
  }
  if(!pool.length){
    $("projBoard").innerHTML=`<div class="empty">没有匹配「${esc(q)}」的项目。换个关键词，或切到「全部」看看。</div>`;
  }
  const issues=[];
  all.forEach(p=>{const r=projectRisk(p);if(r)issues.push({p,r});if(projectConflict(p))issues.push({p,r:{days:999,cls:"soon",rk:"冲突",txt:projectConflictText(p)}});});
  /* 排序按「该先处理谁」，不是按日期数字大小：
       ① 状态/进度冲突 → 数据不可信，先看
       ② 今天/明天/3 天内 → 还来得及做
       ③ 刚逾期 → 还能补救
       ④ 长期挂起（>90 天）→ 大概率已停滞，放最后等你去 Notion 决定归档
     原先直接按 days 升序，导致逾期 416 天的排在第一位，而 3 天内到期的排在后面。 */
  const sev=x=>x.r.rk==="冲突"?0:x.r.cls==="today"?1:x.r.cls==="soon"?2:(x.r.stale?4:3);
  issues.sort((a,b)=>sev(a)-sev(b) || Math.abs(a.r.days)-Math.abs(b.r.days));
  $("riskTag").textContent=issues.length+" 项";
  $("riskList").innerHTML=(issues.length?issues.map(({p,r})=>`
    <div class="risk ${r.cls}${r.stale?" stale":""}" role="button" tabindex="0" aria-label="定位到 ${esc(p.name)}"
      onclick="jumpToProject('${esc(p.id)}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();jumpToProject('${esc(p.id)}')}">
      <span class="rk">${r.rk}</span><span class="rd"><b>${esc(p.name)}</b> · ${esc(r.txt)}</span>${p.source==="notion"&&p.notionId?`<a class="notionlink" href="https://app.notion.com/${esc(String(p.notionId).replace(/-/g,""))}" target="_blank" rel="noopener" onclick="event.stopPropagation()" title="在 Notion 中打开">Notion ↗</a>`:""}</div>`).join("")
    :`<div class="empty">暂无临期、逾期或状态冲突</div>`)+
    `<div class="no-note" style="margin-top:10px">当前未完成项目中 ${noDue} 个没有截止日期；这里不把“无日期”伪装成“安全”。</div>`;
  renderProjectClosure(all);
  renderProjPrio(all);
}
/* 告警 → 本地定位：切到能看到目标项目的范围（完成/取消走「全部」），
   渲染后展开折叠组、滚动到卡片并闪烁两下。
   树状视图的行是上下文淡化，定位到卡片用看板更精确，所以强制切回看板。
   去 Notion 修数据走卡片上的「Notion ↗」链接，不在这抢跳转。 */
function jumpToProject(id){
  const p=(state.projects||[]).find(x=>x.id===id); if(!p) return;
  /* 从复盘页点过来时，项目屏本身还是 hidden 的。先切页签，否则下面虽然能把
     卡片渲染并加高亮，用户看到的仍是复盘页，等于“跳了但没跳”。 */
  if(curType!=="project") setType("project");
  /* 新定位取代旧定位：同一时刻最多一张卡带高亮，点下一条自动清掉上一条 */
  document.querySelectorAll(".proj.flash").forEach(n=>n.classList.remove("flash"));
  /* 本地定位：切到能看到目标项目的范围（完成/取消走「全部」），
     渲染后展开折叠组、滚动到卡片并闪烁两下。
     树状视图的行是上下文淡化，定位到卡片用看板更精确，所以强制切回看板。 */
  projectView="board";
  const b=$("pjViewBoard"), t=$("pjViewTree"), c=$("pjViewCal");
  if(b) b.classList.toggle("on",true);
  if(t) t.classList.toggle("on",false);
  if(c) c.classList.toggle("on",false);
  setProjectScope(p.status==="已完成"?"done":(p.status==="已取消"?"cancelled":"active"));
  requestAnimationFrame(()=>{
    let el=document.querySelector(`[data-pid="${id}"]`);
    if(!el) return;
    let det=el.closest("details");
    while(det){ det.open=true; det=det.parentElement&&det.parentElement.closest("details"); }
    el.scrollIntoView({behavior:"smooth",block:"center"});
    el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
    /* 静态高亮为主：保持 6 秒，远长于动画，读得清在哪 */
    setTimeout(()=>el&&el.classList.remove("flash"),6000);
  });
}
/* 高亮的自然退出：点页面空白处或按 Esc 都清掉，不让红框常驻干扰 */
document.addEventListener("click",e=>{
  if(e.target.closest(".proj")||e.target.closest("#riskList"))return;
  document.querySelectorAll(".proj.flash").forEach(n=>n.classList.remove("flash"));
});
/* 日历视图：按截止日期把项目铺到月历格子里。
   没填截止日期的项目不进日历（在页脚单列出来，避免"看不见=不存在"）。
   颜色语义与看板一致：逾期红、今天橙、3 天内浅橙、已完成/取消灰。 */
function calMonthStr(){
  if(calMonth) return calMonth;
  return todayStr().slice(0,7);
}
function calShift(n){
  const m=calMonthStr().split("-").map(Number);
  const d=new Date(m[0],m[1]-1+n,1);
  calMonth=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}`;
  renderProject();
}
function renderProjectCalendar(pool){
  const ms=calMonthStr(), [y,mo]=ms.split("-").map(Number);
  const first=new Date(y,mo-1,1), lead=(first.getDay()+6)%7;  /* 周一为第一列 */
  const days=new Date(y,mo,0).getDate();
  const tdy=todayStr();
  const byDay={};
  const noDue=[];
  pool.forEach(p=>{
    if(/^\d{4}-\d{2}-\d{2}$/.test(p.due||"")) (byDay[p.due]=byDay[p.due]||[]).push(p);
    else noDue.push(p);
  });
  const cells=[];
  for(let i=0;i<lead;i++) cells.push(`<div class="calcell empty"></div>`);
  for(let d=1;d<=days;d++){
    const ds=`${ms}-${String(d).padStart(2,"0")}`;
    const items=(byDay[ds]||[]).sort((a,b)=>prioRank(a)-prioRank(b));
    const isToday=ds===tdy;
    cells.push(`<div class="calcell${isToday?" today":""}">
      <span class="calday">${d}</span>
      ${items.map(p=>{
        const late=p.due<tdy&&p.status!=="已完成"&&p.status!=="已取消";
        const cls=late?"late":(p.status==="已完成"||p.status==="已取消")?"done":"";
        const pl=prioLabel(p);
        return `<button class="calev ${cls}" data-pid="${esc(p.id)}" onclick="jumpToProject('${esc(p.id)}')" title="${esc(p.name)} · ${esc(p.status)}${p.due?" · 截止 "+p.due:""}">
          <span class="calt">${pl?`<b>${pl}</b> `:""}${esc(p.name)}</span>
          <span class="cals">${esc(p.status||"")}${p.progress!==null&&p.progress!==undefined?` · ${Math.round(+p.progress||0)}%`:""}</span>
        </button>`;
      }).join("")}
    </div>`);
  }
  const tail=(7-(lead+days)%7)%7;
  for(let i=0;i<tail;i++) cells.push(`<div class="calcell empty"></div>`);
  $("projBoard").innerHTML=`<div class="calhead">
      <button class="miniact" onclick="calShift(-1)" aria-label="上个月">←</button>
      <b>${y} 年 ${mo} 月</b>
      <button class="miniact" onclick="calShift(1)" aria-label="下个月">→</button>
      ${calMonth?`<button class="miniact" onclick="calMonth=null;renderProject()">回到本月</button>`:""}
    </div>
    <div class="calgrid">
      ${["一","二","三","四","五","六","日"].map(w=>`<div class="calwd">${w}</div>`).join("")}
      ${cells.join("")}
    </div>
    ${noDue.length?`<div class="prionote" style="margin-top:10px">另有 <b>${noDue.length}</b> 个项目没有截止日期，不在日历里：${
      noDue.slice(0,6).map(p=>esc(p.name)).join("、")}${noDue.length>6?"…":""}</div>`:""}`;
}
/* 树状视图：按「上级 项目」relation 组装层级。
   只读展示——层级结构是 Notion 的真源，工作台不提供拖拽改挂载。
   孤儿（父 id 不在当前数组里，通常是父项目被删）兜底为根，不丢卡。 */
function projectTreeRows(pool){
  const byId={}; pool.forEach(p=>byId[p.id]=p);
  const kids={}; const roots=[];
  pool.forEach(p=>{
    const par=p.parent&&byId[p.parent]?p.parent:null;
    if(par){ (kids[par]=kids[par]||[]).push(p); p._treeDepth=void 0; }
    else roots.push(p);
  });
  const rows=[];
  const walk=(p,d)=>{
    rows.push({p,d});
    const cs=(kids[p.id]||[]).sort((a,b)=>prioRank(a)-prioRank(b)||String(a.name).localeCompare(String(b.name),"zh"));
    cs.forEach(c=>walk(c,d+1));
  };
  roots.sort((a,b)=>prioRank(a)-prioRank(b)||String(a.name).localeCompare(String(b.name),"zh"));
  roots.forEach(r=>walk(r,0));
  return rows;
}
function renderProjectTree(pool){
  /* 层级必须用全量数据建：父项目被「当前/搜索」筛掉时，子项目仍要知道自己挂在哪。
     命中筛选的行正常显示，未命中但属于命中行祖先链的行淡化保留（上下文），
     其余未命中行隐藏——既保住树形，又不让「当前」视图被 25 条已完成淹没。 */
  const all=(state&&state.projects)||[];
  const poolSet=new Set(pool.map(p=>p.id));
  const full=projectTreeRows(all);
  const depthOf={}, kidsOf={};
  full.forEach(({p,d})=>depthOf[p.id]=d);
  const keep=new Set();
  full.forEach(({p})=>{
    if(!poolSet.has(p.id)) return;
    keep.add(p.id);
    let cur=p;
    while(cur&&cur.parent&&depthOf[cur.parent]!==undefined){ keep.add(cur.parent); cur=all.find(x=>x.id===cur.parent); }
  });
  const rows=full.filter(({p})=>keep.has(p.id));
  const kidCountAll={};
  all.forEach(p=>{ if(p.parent&&depthOf[p.parent]!==undefined) kidCountAll[p.parent]=(kidCountAll[p.parent]||0)+1; });
  $("projBoard").innerHTML=rows.length?`<div class="ptree">`+rows.map(({p,d})=>{
    const pct=(p.progress===null||p.progress===undefined||p.progress==="")?null:Math.max(0,Math.min(100,+p.progress||0));
    const pl=prioLabel(p);
    const done=p.status==="已完成"||p.status==="已取消";
    const ind=`<span class="ptree-ind" style="width:${d*18}px"></span>`;
    const branch=d>0?`<span class="ptree-branch">${d===1?"└":"└"}</span>`:"";
    const kidCount=kidCountAll[p.id]||0;
    const ctx=!poolSet.has(p.id);
    return `<div class="ptree-row${done?" done":""}${ctx?" ctx":""}" style="--depth:${d}">
      ${ind}${branch}
      ${pl?`<span class="prio prio-${pl.toLowerCase()}">${pl}</span>`:""}
      <span class="ptree-name">${esc(p.name)}</span>
      ${kidCount?`<span class="ptree-kids">${kidCount} 子项</span>`:""}
      <span class="badge b-${p.status==="已取消"?"已取消":p.status}">${esc(p.status||"其他")}</span>
      <span class="ptree-meta">${pct===null?"—":Math.round(pct)+"%"}</span>
    </div>`;
  }).join("")+`</div>`:`<div class="empty">没有匹配的项目。</div>`;
}
/* 项目收口审计：项目结束不等于事情结束。
   已完成缺完成日期 → 交付节奏与里程碑不可信；已完成/已取消缺备注 → 以后无法复盘。
   取消原因复用 Notion 现有「备注」字段，避免再建一个只写不看的重复属性。
   这里只读提示，不自动改 Notion；点一条仍然走本地定位，Notion 链接在卡片上。 */
function projectClosureIssues(all){
  const out=[];
  (all||[]).forEach(p=>{
    if(p.status==="已完成"&&!p.doneDate) out.push({p,kind:"完成日期",why:"已完成但没有完成日期，交付节奏和里程碑会漏掉它"});
    if(p.status==="已完成"&&!String(p.note||"").trim()) out.push({p,kind:"完成说明",why:"完成后没有留下结果或复盘，之后只能看到“做完了”"});
    if(p.status==="已取消"&&!String(p.note||"").trim()) out.push({p,kind:"取消原因",why:"取消但没有说明原因，无法判断是放弃、合并还是改期"});
  });
  return out;
}
function renderProjectClosure(all){
  const box=$("closeAuditList"), tag=$("closeAuditTag"), brief=$("closeAuditBrief");
  if(!box||!tag) return;
  const issues=projectClosureIssues(all);
  const ended=(all||[]).filter(p=>p.status==="已完成"||p.status==="已取消").length;
  tag.textContent=issues.length?`${issues.length} 待补`:`${ended} 个已收口`;
  if(brief) brief.textContent=issues.length?"展开处理":"无缺口";
  if(!issues.length){
    box.innerHTML=`<div class="closeaudit-ok">已结束项目都留下了完成日期或收口说明。</div>`;
    return;
  }
  const row=x=>`<button class="closeaudit-row" type="button" onclick="jumpToProject('${esc(x.p.id)}')"
      title="${esc(x.why)}">
      <span class="ca-k">${x.kind}</span>
      <span class="ca-n">${esc(x.p.name)}</span>
      <span class="ca-go">定位 →</span>
    </button>`;
  const head=issues.slice(0,5).map(row).join("");
  const rest=issues.slice(5);
  box.innerHTML=head+(rest.length?`<details class="completed-group"><summary>查看剩余 ${rest.length} 项<span>按需查看</span></summary>
    <div class="closeaudit" style="margin-top:6px">${rest.map(row).join("")}</div></details>`:"");
}
/* 复盘页的项目入口：只显示「缺收口说明」的项目，不把“缺完成日期”也算进来。
   完成日期是排期字段，可在项目页补；复盘页要回答的是“这件事留下了什么”。
   同一个项目若同时缺完成日期和说明，这里只出现一次（按项目去重）。 */
function projectReviewTodos(all){
  return (all||[]).filter(p=>(p.status==="已完成"||p.status==="已取消")&&!String(p.note||"").trim());
}
function renderReviewProjectTodos(){
  const box=$("rvProjTodo"), tag=$("rvProjTodoTag");
  if(!box||!tag) return;
  const rows=projectReviewTodos((state&&state.projects)||[]);
  tag.textContent=rows.length?`${rows.length} 项待补`:"已收口";
  if(!rows.length){
    box.innerHTML=`<div class="closeaudit-ok">已结束项目都留下了收口说明。</div>`;
    return;
  }
  box.innerHTML=rows.slice(0,4).map(p=>`<button class="closeaudit-row" type="button" onclick="jumpToProject('${esc(p.id)}')"
      title="${esc(p.name)}">
      <span class="ca-k">${p.status==="已取消"?"取消原因":"完成说明"}</span>
      <span class="ca-n">${esc(p.name)}</span>
      <span class="ca-go">定位 →</span>
    </button>`).join("")+
    (rows.length>4?`<details class="completed-group"><summary>查看剩余 ${rows.length-4} 项<span>按需查看</span></summary>
      <div class="closeaudit" style="margin-top:6px">${rows.slice(4).map(p=>`<button class="closeaudit-row" type="button" onclick="jumpToProject('${esc(p.id)}')"
        title="${esc(p.name)}"><span class="ca-k">${p.status==="已取消"?"取消原因":"完成说明"}</span>
        <span class="ca-n">${esc(p.name)}</span><span class="ca-go">定位 →</span></button>`).join("")}</div></details>`:"");
}
/* 优先级分布：Notion 的「优先级」属性一直没在页面上露过面，这里把它的真实填写情况摊开。
   故意把「未填」也列出来 —— 11/29 条没填才是当前最该被看见的事实。 */
function renderProjPrio(all){
  const box=$("projPrio"), brief=$("prioFoldBrief"); if(!box) return;
  const levels=["P0","P1","P2","P3","P4"], cnt={}, miss=[];
  levels.forEach(l=>cnt[l]=0);
  all.forEach(p=>{const l=prioLabel(p); if(l)cnt[l]++; else miss.push(p);});
  const active=all.filter(p=>!(p.status==="已完成"||p.status==="已取消"));
  const missActive=active.filter(p=>!prioLabel(p)).length;
  if(brief) brief.textContent=`未填 ${miss.length}/${all.length}`;
  const max=Math.max(1,...levels.map(l=>cnt[l]));
  box.innerHTML=levels.map(l=>{
    const n=cnt[l], pct=Math.round(n/max*100);
    return `<div class="priobar"><span class="pl">${l}</span>
      <span class="ptrack"><i class="pfill ${l.toLowerCase()}" style="width:${pct}%"></i></span>
      <span class="pn">${n}</span></div>`;
  }).join("")+
  `<div class="prionote">未填优先级 <b>${miss.length}</b> / ${all.length}${missActive?`（其中 ${missActive} 个是未完成项目）`:""}。
  ${missActive?"这些项目在排序里只能排到最后，建议到 Notion 补上。":"已全部标注。"}</div>`;
  renderProjPulse(all);
}
/* 交付节奏 sparkline + 里程碑时间带：全部由 doneDate 现算。
   doneDate 是批量回填的（2025-10-16 一天 8 个），峰值是真实历史不是 bug，
   时间带上标月份和数量，hover 出明细。 */
function renderProjPulse(all){
  const done=all.filter(p=>p.status==="已完成"&&p.doneDate).map(p=>p.doneDate).sort();
  const weeks=[];
  {
    const base=new Date(todayStr()+"T00:00:00");
    for(let i=7;i>=0;i--){
      const end=new Date(base); end.setDate(end.getDate()-i*7);
      const start=new Date(end); start.setDate(start.getDate()-6);
      const s=start.toISOString().slice(0,10), e2=end.toISOString().slice(0,10);
      weeks.push({s,e:e2,n:done.filter(d=>d>=s&&d<=e2).length});
    }
  }
  const wmax=Math.max(1,...weeks.map(w=>w.n));
  const recentN=weeks.reduce((a,w)=>a+w.n,0);
  const pulseBrief=$("pulseFoldBrief");
  if(pulseBrief) pulseBrief.textContent=`${recentN} 个交付`;
  const spark=weeks.map(w=>`<i style="height:${Math.round(w.n/wmax*100)}%" title="${w.s} ~ ${w.e}: ${w.n} 个"></i>`).join("");
  /* 里程碑带：按月聚合，最近 12 个有交付的月 */
  const months={};
  done.forEach(d=>{const m=d.slice(0,7);months[m]=(months[m]||0)+1;});
  const ms=Object.keys(months).sort().slice(-12);
  const mmax=Math.max(1,...ms.map(m=>months[m]));
  const band=ms.map(m=>{
    const n=months[m];
    const detail=done.filter(d=>d.slice(0,7)===m).join("、");
    return `<span class="mscol" title="${m}：交付 ${n} 个（${detail}）">
      <i style="height:${Math.max(8,Math.round(n/mmax*100))}%" class="${n===mmax?"top":""}"></i>
      <em>${m.slice(2).replace("-","/")}</em></span>`;
  }).join("");
  const box=$("projPulse"); if(!box) return;
  box.innerHTML=`
    <div class="pulseblock"><h3 class="prioh">交付节奏 <span class="tag">近 8 周</span></h3>
      <div class="pulsespark">${spark}</div>
      <div class="prionote">近 8 周共交付 <b>${recentN}</b> 个。${weeks.every(w=>!w.n)?"近期没有交付记录。":""}</div>
    </div>
    <div class="pulseblock"><h3 class="prioh">里程碑 <span class="tag">按月</span></h3>
      ${ms.length?`<div class="msband">${band}</div><div class="prionote">hover 柱子看具体日期。</div>`:`<div class="prionote">还没有完成日期数据。</div>`}
    </div>
    ${renderHoursSummary(all)}`;
}
/* 工时汇总：只统计填了的项目，并明确说清覆盖率——
   3/29 填了就报 3 个，不拿"部分数据"冒充全局口径。 */
function renderHoursSummary(all){
  const withAct=all.filter(p=>typeof p.actHours==="number");
  const hasBoth=all.filter(p=>typeof p.actHours==="number"&&typeof p.estHours==="number");
  const sumAct=withAct.reduce((a,p)=>a+(p.actHours||0),0);
  const sumEst=hasBoth.reduce((a,p)=>a+(p.estHours||0),0);
  const over=hasBoth.filter(p=>p.actHours>p.estHours);
  const withOwner=all.filter(p=>(p.ownerIds||[]).length||p.owner);
  const withRelations=all.filter(p=>(p.relatedTaskIds||[]).length||(p.sourceRecordIds||[]).length||(p.childIds||[]).length);
  if(!withAct.length&&!withOwner.length&&!withRelations.length){
    return `<div class="pulseblock"><h3 class="prioh">工时与关系 <span class="tag">来自 Notion</span></h3>
      <div class="prionote">还没有工时、负责人或关联任务数据。在 Notion 里填写对应字段后，这里自动出现汇总。</div></div>`;
  }
  const parts=[];
  if(withAct.length){
    let line=`已记录工时 <b>${withAct.length}</b> / ${all.length} 项，实际合计 <b>${sumAct}h</b>`;
    if(hasBoth.length){
      const diff=Math.round(Math.abs(sumAct-sumEst)*10)/10;
      line+=`；其中 ${hasBoth.length} 项有预估，预估合计 ${sumEst}h `+
        (sumAct>sumEst?`<span class="over">超支 ${diff}h</span>`:`<span class="ok">省 ${diff}h</span>`);
    }
    parts.push(`<div class="hrline">${line}</div>`);
    if(over.length){
      parts.push(`<div class="prionote">超支项目：${over.map(p=>esc(p.name)+"（"+p.actHours+"h / "+p.estHours+"h）").join("、")}</div>`);
    }
  }
  if(withOwner.length){
    parts.push(`<div class="hrline" style="margin-top:8px">负责人已填写 <b>${withOwner.length}</b> / ${all.length} 项。负责人由 Notion people 字段维护，工作台只读展示。</div>`);
  }
  if(withRelations.length){
    const taskN=all.reduce((n,p)=>n+(p.relatedTaskIds||[]).length,0);
    const sourceN=all.reduce((n,p)=>n+(p.sourceRecordIds||[]).length,0);
    const childN=all.reduce((n,p)=>n+(p.childIds||[]).length,0);
    parts.push(`<div class="hrline" style="margin-top:8px">关联记录已接入 <b>${withRelations.length}</b> 项：关联任务 ${taskN} · 来源记录 ${sourceN} · 子项目 ${childN}</div>`);
  }
  const body=parts.join("");
  return `<div class="pulseblock"><h3 class="prioh">工时与关系 <span class="tag">来自 Notion</span></h3>${body}</div>`;
}

/* —— 客户 / 贵人 —— */
const CLIENTS_SEED=[
  ["王姐","读者","线索",9,"—"],
  ["赵老师","教授","线索",12,"—"],
  ["刘总","合伙人","初步接触",7,"—"],
  ["张总","客户","深度沟通",1,"¥2,000 待回款"],
  ["陈博","顾问","深度沟通",3,"¥5,000 报价"],
  ["李工","客户","已成交",5,"¥800"],
  ["周姐","渠道","回款中",2,"¥3,000"]
];
let CLIENTS=loadNew("wb_clients",CLIENTS_SEED);
// 商务管线各阶段数从真实贵人清单派生（不写死），status → 阶段 映射
const STAGE_OF={"线索":"线索","初步接触":"初步接触","深度沟通":"深度沟通","方案确认":"深度沟通","成交意向":"成交","已成交":"成交","回款中":"回款","待跟进":"线索"};
const PIPE=[["线索","待触达"],["初步接触","已加微信"],["深度沟通","推进中"],["成交","本周"],["回款","进行中"]];
function pipeCount(){ const m={"线索":0,"初步接触":0,"深度沟通":0,"成交":0,"回款":0};
  CLIENTS.forEach(c=>{ const s=STAGE_OF[c[2]]||"线索"; if(s in m) m[s]++; }); return m; }
const CLIENT_FLOW=["线索","初步接触","深度沟通","已成交","回款中"];
function nextClientStage(st){ if(st==="待跟进") return "初步接触"; const i=CLIENT_FLOW.indexOf(st); return (i>=0&&i<CLIENT_FLOW.length-1)?CLIENT_FLOW[i+1]:st; }
/* 客户页双模式：state.clients 有 Notion 数据 → 真实只读渲染；
   否则回退本地试验数组（老格式 [姓名,关系,状态,天数,备注]）。 */
function notionClients(){
  const cs=(state&&Array.isArray(state.clients))?state.clients:[];
  return cs.length?cs:null;
}
let clientHealthF="";
const CLIENT_HEALTH=[
  ["loss","亏损客户","收入减支出为负","bad"],
  ["zero","零收入","还没有任何收入往来","warn"],
  ["noStatus","未标客户状态","Notion「客户状态」为空","warn"],
  ["noSource","未标关系来源","不知道客户从哪里来","warn"],
  ["noOwner","未标负责人","团队协作时无法分派","warn"],
  ["noNext","没有下次跟进","没有排下一次联系","warn"],
  ["suspicious","疑似非个人客户","名称像订单、群组或测试项",""]
];
const CLIENT_HEALTH_PRIMARY=["noNext","noStatus","loss","suspicious"];
function clientNet(c){
  const p=Number(c&&c.profit);
  if(Number.isFinite(p)) return p;
  return (Number(c&&c.income)||0)-(Number(c&&c.expense)||0);
}
function clientDateDiff(dateStr){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(String(dateStr||""))) return null;
  const a=new Date(dateStr+"T00:00:00");
  const b=new Date(todayStr()+"T00:00:00");
  if(isNaN(a.getTime())||isNaN(b.getTime())) return null;
  return Math.round((a-b)/86400000);
}
function clientNextDate(c){
  return String((c&&(c.nextFollowUp||c.nextFollowupAuto))||"").trim();
}
const CLIENT_OPTION_FALLBACK={
  customerType:["个人","团队","渠道","群组","订单","其他"],
  status:["线索","沟通中","方案中","成交","复购","流失"],
  relationSource:["知识星球","公众号","小红书","微信","生财","朋友推荐","闲鱼","其他"]
};
function clientEditOptions(key,current){
  const schema=(state&&state.notionMeta&&state.notionMeta.clientSchema)||{};
  let opts=Array.isArray(schema[key])?schema[key].slice():[];
  if(!opts.length) opts=(CLIENT_OPTION_FALLBACK[key]||[]).slice();
  if(current&&!opts.includes(current)) opts.unshift(current);
  return `<option value="">未标注</option>`+
    opts.map(v=>`<option value="${esc(v)}" ${v===current?"selected":""}>${esc(v)}</option>`).join("");
}
function clientEditField(label,id,value,attrs){
  return `<div class="field"><label for="${id}">${esc(label)}</label>`+
    `<input id="${id}" name="${id}" value="${esc(value||"")}" ${attrs||""}></div>`;
}
function clientEditSelect(label,id,value,key){
  return `<div class="field"><label for="${id}">${esc(label)}</label>`+
    `<select id="${id}" name="${id}">${clientEditOptions(key,String(value||""))}</select></div>`;
}
let clientEditId="";
function openClientEdit(id){
  const c=((state&&state.clients)||[]).find(x=>String(x.notionId||"")===String(id));
  if(!c) return;
  clientEditId=String(id);
  $("clientDetailBody").innerHTML=`<form id="clientEditForm" onsubmit="saveClient(event)">`+
    `<div class="client-drawer-head"><div><h3 id="clientDetailTitle">编辑客户资料</h3>`+
    `<p>${esc(c.name)} · 保存后直接回写 Notion「贵人计划」</p></div><span class="chip">Notion 回写</span></div>`+
    `<div class="client-edit-grid">`+
      `<div class="wide">${clientEditField("客户名称","ceName",c.name,'maxlength="200" required')}</div>`+
      clientEditSelect("客户类型","ceType",c.customerType,"customerType")+
      clientEditSelect("客户状态","ceStatus",c.status,"status")+
      clientEditSelect("关系来源","ceSource",c.relationSource,"relationSource")+
      clientEditField("首次接触","ceFirst",c.firstContact,'type="date"')+
      clientEditField("上次联系","ceLast",c.lastContact,'type="date"')+
      clientEditField("下次跟进","ceNext",c.nextFollowUp,'type="date"')+
      clientEditField("微信 ID","ceWechat",c.wechatId,'maxlength="200"')+
      `<div class="field wide"><label for="ceNote">备注</label><textarea id="ceNote" name="ceNote" rows="3" maxlength="8000">${esc(c.memo||"")}</textarea></div>`+
      `<div class="field wide"><label for="ceTodo">待办</label><textarea id="ceTodo" name="ceTodo" rows="2" maxlength="8000">${esc(c.todo||"")}</textarea></div>`+
      clientEditField("坐标","ceCoord",c.coordinate,'maxlength="200"')+
      clientEditField("已送福利","ceBenefits",c.benefits,'maxlength="200"')+
      `<div class="wide">${clientEditField("快递地址","ceAddress",c.address,'maxlength="500"')}</div>`+
      `<div class="wide">${clientEditField("渠道","ceChannel",c.channel,'maxlength="500"')}</div>`+
    `</div>`+
    `<div class="prionote">负责人是 Notion people 字段，当前版本不在网页里回写，避免把成员误改成文本。收入、支出、利润由 Notion rollup / formula 维护，也不在这里改。</div>`+
    `<div class="client-edit-state" id="clientEditState" role="status" aria-live="polite"></div>`+
    `<div class="acts"><button class="btn ghost" type="button" onclick="openClientDetail('${esc(c.notionId||"")}')">取消</button>`+
    `<button class="btn primary" id="clientEditSave" type="submit">保存并回写 Notion</button></div>`+
    `</form>`;
  openOv("clientOv");
}
async function saveClient(ev){
  if(ev&&ev.preventDefault) ev.preventDefault();
  if(guest){notify("访客模式不会回写 Notion。","warn");return;}
  if(!SERVED){notify("回写需要本地同步服务（127.0.0.1:8787）。","warn");return;}
  if(!TOKEN){notify("链接缺少访问令牌，无法回写 Notion。","err");return;}
  const val=id=>String(($(id)&&$(id).value)||"").trim();
  const fields={
    name:val("ceName"),customerType:val("ceType"),status:val("ceStatus"),
    relationSource:val("ceSource"),firstContact:val("ceFirst"),lastContact:val("ceLast"),
    nextFollowUp:val("ceNext"),wechatId:val("ceWechat"),note:val("ceNote"),
    todo:val("ceTodo"),coordinate:val("ceCoord"),benefits:val("ceBenefits"),
    address:val("ceAddress"),channel:val("ceChannel")
  };
  const stateBox=$("clientEditState"), btn=$("clientEditSave");
  if(stateBox){stateBox.className="client-edit-state";stateBox.textContent="正在写入 Notion…";}
  if(btn){btn.disabled=true;btn.textContent="正在回写…";}
  try{
    const r=await fetch("/api/clients/update",{
      method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({pageId:clientEditId,fields})
    });
    const j=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    const idx=((state&&state.clients)||[]).findIndex(c=>String(c.notionId||"")===String(clientEditId));
    if(idx>=0&&j.client) state.clients[idx]={...state.clients[idx],...j.client};
    cacheLocal(state);
    renderClient();
    notify(j.localUpdated===false
      ? "Notion 已更新，但本地 data.json 未落盘；下一次同步会补齐。"
      : "已回写 Notion，并同步更新本地客户卡。",j.localUpdated===false?"warn":"info");
    openClientDetail(clientEditId);
  }catch(e){
    if(stateBox){stateBox.className="client-edit-state err";stateBox.textContent="回写失败："+e.message;}
    notify("客户回写失败："+e.message,"err");
  }finally{
    if(btn){btn.disabled=false;btn.textContent="保存并回写 Notion";}
  }
}
function clientNextText(c){
  const d=clientDateDiff(clientNextDate(c));
  if(d===null) return {txt:"未排期",cls:""};
  if(d<0) return {txt:`逾期 ${Math.abs(d)} 天`,cls:"late"};
  if(d===0) return {txt:"今天跟进",cls:"warn"};
  return {txt:`${d} 天后`,cls:d<=3?"warn":""};
}
function clientStatusChip(c,showEmpty=true){
  const s=String((c&&c.status)||"").trim();
  if(!s) return showEmpty?`<span class="chip">未标注</span>`:"";
  const tone=s==="成交"||s==="复购"?"ok":s==="流失"?"dead":s==="沟通中"||s==="方案中"?"warn":"";
  return `<span class="chip ${tone}">${esc(s)}</span>`;
}
function clientProfileScore(c){
  const fields=[
    ["客户类型",c&&c.customerType],
    ["关系来源",c&&c.relationSource],
    ["客户状态",c&&c.status],
    ["负责人",c&&c.owner],
    ["下次跟进",clientNextDate(c)]
  ];
  const missing=fields.filter(([,v])=>!String(v||"").trim()).map(([k])=>k);
  return {filled:fields.length-missing.length,total:fields.length,missing};
}
function clientHealthMatch(c,kind){
  if(!kind) return true;
  if(kind==="loss") return clientNet(c)<0;
  if(kind==="zero") return (Number(c.income)||0)<=0;
  if(kind==="noStatus") return !String(c.status||"").trim();
  if(kind==="noSource") return !String(c.relationSource||"").trim();
  if(kind==="noOwner") return !String(c.owner||"").trim();
  if(kind==="noNext") return !clientNextDate(c);
  if(kind==="suspicious") return /订单|群|团队|测试|support|代写/i.test(String(c.name||""));
  return true;
}
function clientOptionHTML(id,allLabel,field){
  const elx=$(id);
  const keep=elx?elx.value:"";
  const vals=[...new Set(((state&&state.clients)||[]).map(c=>String(c[field]||"").trim()).filter(Boolean))]
    .sort((a,b)=>a.localeCompare(b,"zh-CN"));
  const miss=((state&&state.clients)||[]).some(c=>!String(c[field]||"").trim());
  return `<option value="">${allLabel}</option>`+
    (miss?`<option value="__missing" ${keep==="__missing"?"selected":""}>未标注</option>`:"")+
    vals.map(v=>`<option value="${esc(v)}" ${keep===v?"selected":""}>${esc(v)}</option>`).join("");
}
function renderClientFilters(nc){
  const defs=[["clientTypeF","全部类型","customerType"],
              ["clientStatusF","全部状态","status"],
              ["clientSourceF","全部来源","relationSource"]];
  defs.forEach(([id,label,field])=>{
    const box=$(id);
    if(!box) return;
    const signature=nc.map(c=>String(c[field]||"")).sort().join("\u0001")+"\u0002"+label;
    if(box._clientSig===signature) return;
    const keep=box.value;
    box._clientSig=signature;
    box.innerHTML=clientOptionHTML(id,label,field);
    box.value=keep;
  });
}
function clientHealthJump(kind){
  clientHealthF=clientHealthF===kind?"":kind;
  ["clientTypeF","clientStatusF","clientSourceF"].forEach(id=>{const e=$(id);if(e)e.value="";});
  renderClient();
}
const CLIENT_PAGE=20;
let clientAll=false;
function clientFilterChanged(){ clientHealthF=""; clientAll=false; renderClient(); }
function clientSortChanged(){ clientAll=false; renderClient(); }
function clearClientFilters(){
  ["clientQ","clientTypeF","clientStatusF","clientSourceF"].forEach(id=>{const e=$(id);if(e)e.value="";});
  clientHealthF="";
  clientAll=false;
  renderClient();
}
function toggleClientAll(){ clientAll=!clientAll; renderClient(); }
function clientFollowData(){
  const fs=(state&&Array.isArray(state.followups))?state.followups:[];
  const active=fs.filter(f=>!["已完成","已取消"].includes(String(f.status||"")));
  const today=todayStr();
  const overdue=active.filter(f=>{
    const d=clientDateDiff(f.nextFollowUp||f.date);
    return d!==null&&d<0;
  });
  const dueToday=active.filter(f=>String(f.nextFollowUp||"")===today);
  return {all:fs,active,overdue,dueToday};
}
function followupClientSummary(f){
  const ids=(f&&Array.isArray(f.clientIds))?f.clientIds:[];
  const all=(state&&Array.isArray(state.clients))?state.clients:[];
  const linked=ids.map(id=>all.find(c=>String(c.notionId||"")===String(id))).filter(Boolean);
  return {
    linked,
    income:linked.reduce((a,c)=>a+(Number(c.income)||0),0),
    expense:linked.reduce((a,c)=>a+(Number(c.expense)||0),0),
    profit:linked.reduce((a,c)=>a+clientNet(c),0)
  };
}
function clientHealthCounts(nc){
  return Object.fromEntries(CLIENT_HEALTH.map(([k])=>[k,nc.filter(c=>clientHealthMatch(c,k)).length]));
}
function clientHealthItem([k,name,desc,tone],counts){
  return `<button type="button" class="client-health-item ${tone}${clientHealthF===k?" on":""}" onclick="clientHealthJump('${k}')" aria-pressed="${clientHealthF===k?"true":"false"}">`+
    `<span class="chk" aria-hidden="true"></span><span><span class="chn">${esc(name)}</span><span class="chd">${esc(desc)}</span></span><span class="chnum">${counts[k]}</span></button>`;
}
function clientRenderHealth(nc){
  const counts=clientHealthCounts(nc);
  // 一个客户可能同时缺状态/来源/负责人，不能把各类数量相加冒充“客户数”。
  const withGap=nc.filter(c=>CLIENT_HEALTH.some(([k])=>clientHealthMatch(c,k))).length;
  const primary=CLIENT_HEALTH.filter(([k])=>CLIENT_HEALTH_PRIMARY.includes(k));
  const rest=CLIENT_HEALTH.filter(([k])=>!CLIENT_HEALTH_PRIMARY.includes(k));
  $("clientHealthTag").textContent=`${withGap} 位 · ${CLIENT_HEALTH.length} 类`;
  $("clientHealth").innerHTML=primary.map(x=>clientHealthItem(x,counts)).join("");
  $("clientHealthRest").innerHTML=rest.map(x=>clientHealthItem(x,counts)).join("");
  $("clientHealthMoreTag").textContent=`${rest.length} 项`;
  const more=$("clientHealthMore");
  if(more) more.open=rest.some(([k])=>k===clientHealthF);
}
function clientRenderFollowups(){
  const d=clientFollowData();
  const dated=d.active.filter(f=>f.nextFollowUp||f.date)
    .sort((a,b)=>String(a.nextFollowUp||a.date).localeCompare(String(b.nextFollowUp||b.date)));
  const undated=d.active.filter(f=>!f.nextFollowUp&&!f.date);
  const closed=d.all.filter(f=>!d.active.includes(f))
    .sort((a,b)=>String(b.date||"").localeCompare(String(a.date||"")));
  const rows=[...dated,...undated,...closed].slice(0,6).map(f=>{
    const date=f.nextFollowUp||f.date, dd=clientDateDiff(date);
    const cls=dd!==null&&dd<0?"late":dd===0?"warn":"";
    const rel=followupClientSummary(f);
    const client=rel.linked.length
      ? rel.linked.map(c=>`<button type="button" class="fclient" onclick="openClientDetail('${esc(c.notionId||"")}')" aria-label="查看客户 ${esc(c.name)}">${esc(c.name)}</button>`).join("、")
      : esc((f.clientNames||[]).join("、")||"未关联客户");
    const money=rel.linked.length
      ? `<span class="fm-money" title="按 relation 自动汇总：收入 ${fmt(rel.income)} / 支出 ${fmt(rel.expense)}">`+
        `${rel.linked.length>1?rel.linked.length+" 位客户 · ":""}关联利润 ${fmt(rel.profit)}</span>`
      : "";
    const u=safeUrl(f.notionUrl);
    return `<div class="followup-row"><div class="ft"><span>${esc(f.title)}</span>`+
      `<span class="chip ${f.status==="已完成"?"ok":f.status==="已取消"?"dead":"warn"}">${esc(f.status||"未标状态")}</span></div>`+
      `<div class="fm"><span>${client}</span><span class="${cls}">${esc(date||"未排期")}</span>`+
      `<span>${esc(f.type||"未标类型")}</span>${money}${u?`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">打开 Notion</a>`:""}</div></div>`;
  });
  $("clientFollowTag").textContent=`今日 ${d.dueToday.length} · 逾期 ${d.overdue.length}`;
  const emptyTitle=d.all.length?"当前没有待处理跟进":"跟进库暂无记录";
  const emptyCopy=d.all.length
    ?`已有 ${d.all.length} 条记录，均已完成或取消；需要继续跟随时在 Notion 新建记录。`
    :"客户库与跟进库的 relation 已接通；先在 Notion 新增跟进记录。";
  $("clientFollowups").innerHTML=rows.length?rows.join(""):
    `<div class="client-follow-empty"><div class="cfe-copy"><b>${emptyTitle}</b><span>${emptyCopy}</span></div>`+
    `<a class="btn" href="https://www.notion.so/f8f6fe86ccfe40248b70cfb2e222a02e" target="_blank" rel="noopener noreferrer">打开 Notion 跟进库</a></div>`;
}
function clientRow(c,idx){
  const nxt=clientNextText(c);
  const id=c.notionId||("local-"+idx);
  const subtitle=[c.customerType,c.relationSource].filter(v=>String(v||"").trim()).join(" · ");
  const owner=String(c.owner||"").trim();
  const score=clientProfileScore(c);
  const profileTitle=score.missing.length?`缺少：${score.missing.join("、")}`:"客户资料完整";
  return `<button type="button" class="client-row" onclick="openClientDetail('${esc(id)}')">`+
    `<span class="cname"><b>${esc(c.name)}</b>${subtitle?`<small>${esc(subtitle)}</small>`:""}</span>`+
    `<span class="cmeta">${clientStatusChip(c,false)}`+
      `${owner?`<span class="cowner">负责人 ${esc(owner)}</span>`:""}`+
      `<span class="cprofile${score.missing.length?" warn":""}" title="${esc(profileTitle)}">资料 ${score.filled}/${score.total}</span></span>`+
    `<span class="cmoney"><b>${fmt(clientNet(c))}</b><small>收入 ${fmt(Number(c.income)||0)}</small></span>`+
    `<span class="cnext ${nxt.cls}">${esc(nxt.txt)}</span></button>`;
}
function renderClient(){
  const nc=notionClients();
  $("clientLabnote").style.display = nc?"none":"";
  $("clientLocalTools").style.display = nc?"none":"";
  $("clientSource").innerHTML = nc
    ? `<b>Notion</b> · 贵人计划 · 可回写 · ${nc.length} 条 · 最近同步 ${esc(String((state.notionMeta||{}).syncedAt||"").replace("T"," ").slice(0,16))}`
    : `<b>本地试验</b> · 不回写 Notion`;
  $("clientSource").title=$("clientSource").textContent.trim();
  if(nc){
    const totalIncome=nc.reduce((a,c)=>a+(+c.income||0),0);
    const totalProfit=nc.reduce((a,c)=>a+clientNet(c),0);
    const paying=nc.filter(c=>(+c.income||0)>0);
    const top5=paying.slice().sort((a,b)=>(+b.income||0)-(+a.income||0)).slice(0,5)
      .reduce((a,c)=>a+(+c.income||0),0);
    const top5pct=totalIncome?Math.round(top5/totalIncome*100):0;
    const noStatus=nc.filter(c=>!String(c.status||"").trim()).length;
    const noNext=nc.filter(c=>!clientNextDate(c)).length;
    $("clientStats").innerHTML=statCells([
      {k:"客户总数",v:nc.length,hint:"Notion 只读"},
      {k:"未排下次跟进",v:noNext,hint:noNext?"优先补排期":"全部已排期"},
      {k:"累计利润",v:fmt(totalProfit),hint:`收入 ${fmt(totalIncome)}`},
      {k:"前五集中度",v:top5pct+"%",hint:"收入是否过度集中"},
      {k:"未标状态",v:noStatus,hint:"在 Notion 客户状态补齐"}]);
    // 只构建一次选项。否则每输入一个字都会重写 select.innerHTML，
    // 浏览器会把当前选中值清空 —— 筛选项看起来可选，实际选完立刻失效。
    renderClientFilters(nc);
    clientRenderHealth(nc);
    clientRenderFollowups();
    const q=String(($("clientQ")&&$("clientQ").value)||"").trim().toLowerCase();
    const tf=String(($("clientTypeF")&&$("clientTypeF").value)||"");
    const sf=String(($("clientStatusF")&&$("clientStatusF").value)||"");
    const rf=String(($("clientSourceF")&&$("clientSourceF").value)||"");
    const sort=String(($("clientSort")&&$("clientSort").value)||"profit");
    const clear=$("clientClear");
    if(clear) clear.hidden=!(q||tf||sf||rf||clientHealthF);
    const matchField=(v,f)=>!f||(f==="__missing"?!String(v||"").trim():String(v||"")===f);
    let rows=nc.filter(c=>{
      const hay=[c.name,c.customerType,c.status,c.relationSource,c.owner,c.role,c.note].join(" ").toLowerCase();
      return (!q||hay.includes(q))&&matchField(c.customerType,tf)&&matchField(c.status,sf)
        &&matchField(c.relationSource,rf)&&clientHealthMatch(c,clientHealthF);
    });
    const sorters={
      profit:(a,b)=>clientNet(b)-clientNet(a),
      income:(a,b)=>(Number(b.income)||0)-(Number(a.income)||0),
      next:(a,b)=>(clientNextDate(a)||"9999-99-99").localeCompare(clientNextDate(b)||"9999-99-99"),
      name:(a,b)=>String(a.name||"").localeCompare(String(b.name||""),"zh-CN")
    };
    rows.sort(sorters[sort]||sorters.profit);
    const matched=rows.length;
    const shownRows=clientAll?rows:rows.slice(0,CLIENT_PAGE);
    $("clientListTag").textContent=`${rows.length} / ${nc.length}`;
    $("clientCount").textContent=`显示 ${shownRows.length} / ${matched} 条`+
      (clientHealthF?` · ${CLIENT_HEALTH.find(x=>x[0]===clientHealthF)[1]}`:"");
    $("clientListTitle").innerHTML=`客户经营名单 <span class="tag" id="clientListTag">${matched} / ${nc.length}</span>`;
    $("clientList").innerHTML=shownRows.length?shownRows.map(clientRow).join("")+
      (matched>CLIENT_PAGE?`<div class="client-more"><span>每次显示 ${CLIENT_PAGE} 条，避免名单把页面拉得过长。</span>`+
        `<button type="button" class="btn" onclick="toggleClientAll()">${clientAll?"收起":"查看全部 "+matched+" 条"}</button></div>`:""):
      `<div class="empty">没有符合条件的客户。清空筛选后再看。</div>`;
    return;
  }
  const active=CLIENTS.length;
  const follow=CLIENTS.filter(c=>c[3]<=3).length;
  const due=CLIENTS.filter(c=>c[4].includes("待回款")||c[2]==="回款中").length;
  const lead=CLIENTS.filter(c=>c[1]==="线索").length;
  if($("clientClear")) $("clientClear").hidden=true;
  $("clientStats").innerHTML=statCells([
    {k:"活跃贵人",v:active,hint:"全部在跟"},{k:"本月需跟进",v:follow,hint:"≤3 天"},
    {k:"待回款",v:due,hint:"笔"},{k:"新线索",v:lead,hint:"待触达"}]);
  $("clientHealthTag").textContent="本地试验";
  $("clientHealth").innerHTML=`<div class="prionote">本地试验数据没有 Notion 客户状态和来源，不参与经营体检。</div>`;
  $("clientFollowTag").textContent="未连接";
  $("clientFollowups").innerHTML=`<div class="prionote">连接 Notion 后显示今日、逾期和未排期的跟进。</div>`;
  $("clientListTitle").innerHTML=`本地试验名单 <span class="tag">${CLIENTS.length} 条</span>`;
  $("clientList").innerHTML=CLIENTS.map((c,i)=>`
    <div class="crow"><span><span class="nm">${esc(c[0])}</span> <span class="rl">· ${esc(c[1])}</span></span>
      <span class="due">${esc(c[2])}</span>
      <span class="due ${c[3]<=3?"hot":""}">${c[3]} 天${c[3]<=3?" ⚠":""}</span>
      <span class="due">${esc(c[4])}</span>
      <span class="acts">
        <button class="miniact" onclick="advanceClient(${i})" aria-label="推进 ${esc(c[0])}">推进 ▸</button>
        <button class="miniact del" onclick="delClient(${i})" aria-label="删除 ${esc(c[0])}">✕</button>
      </span></div>`).join("");
}
function openClientDetail(id){
  const nc=notionClients();
  if(!nc) return;
  const c=nc.find(x=>String(x.notionId||"")===String(id));
  if(!c) return;
  const related=((state&&state.followups)||[]).filter(f=>(f.clientIds||[]).includes(c.notionId));
  const u=safeUrl(c.notionUrl);
  const fields=[
    ["客户类型",c.customerType||"未标注"],["客户状态",c.status||"未标注"],
    ["关系来源",c.relationSource||"未标注"],["负责人",c.owner||"未标注"],
    ["首次接触",c.firstContact||"未标注"],["上次联系",c.lastContact||"未标注"],
    ["下次跟进",clientNextDate(c)||"未排期"],["跟进次数",String(c.followupCount??0)],
    ["最近跟进",c.lastFollowup||"未标注"],["Notion 来源","贵人计划 · 可回写"]
  ];
  const follow=related.length?related.map(f=>
    `<div class="followup-row"><div class="ft"><span>${esc(f.title)}</span><span class="chip">${esc(f.status||"未标状态")}</span></div>`+
    `<div class="fm"><span>${esc(f.date||"未标日期")}</span><span>${esc(f.type||"未标类型")}</span>`+
    `<span>${esc(f.nextStep||f.summary||"无摘要")}</span></div></div>`).join(""):
    `<div class="empty" style="padding:12px 0;text-align:left">暂无关联跟进记录。</div>`;
  $("clientDetailBody").innerHTML=`<div class="client-drawer-head"><div><h3 id="clientDetailTitle">${esc(c.name)}</h3>`+
    `<p>${esc(c.customerType||"未标类型")} · ${esc(c.relationSource||"未标来源")} · ${esc(c.role||"贵人")}</p></div>${clientStatusChip(c)}</div>`+
    `<div class="client-detail-stats"><div><span>累计收入</span><b>${fmt(Number(c.income)||0)}</b></div>`+
    `<div><span>累计支出</span><b>${fmt(Number(c.expense)||0)}</b></div>`+
    `<div><span>纯利润</span><b>${fmt(clientNet(c))}</b></div></div>`+
    `<div class="client-detail-grid">${fields.map(([k,v])=>`<div class="cd"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}</div>`+
    (c.note?`<div class="client-detail-note">${esc(c.note)}</div>`:"")+
    `<div class="prioblock"><h3 class="prioh">关联跟进 <span class="tag">${related.length} 条</span></h3>${follow}</div>`+
    `<div class="media-actions" style="margin-top:14px">`+
    `<button class="btn primary" type="button" onclick="openClientEdit('${esc(c.notionId||"")}')">编辑并回写 Notion</button>`+
    `${u?`<a class="btn" href="${esc(u)}" target="_blank" rel="noopener noreferrer">打开 Notion 客户页</a>`:""}</div>`;
  openOv("clientOv");
}
function advanceClient(i){ if(!CLIENTS[i]) return; CLIENTS[i][2]=nextClientStage(CLIENTS[i][2]); if(!guest) saveNew("wb_clients",CLIENTS); renderClient(); }
function delClient(i){ if(!CLIENTS[i]) return; CLIENTS.splice(i,1); if(!guest) saveNew("wb_clients",CLIENTS); renderClient(); }
function addClient(){
  const nm=$("ncNm").value.trim(), rl=$("ncRl").value.trim()||"—", st=$("ncSt").value, due=parseInt($("ncDue").value||"0",10)||0;
  if(!nm) return;
  CLIENTS.unshift([nm,rl,st,due,"—"]); if(!guest) saveNew("wb_clients",CLIENTS); renderClient();
  $("ncNm").value=""; $("ncRl").value="";
}

/* —— 时间 / 专注 —— */
const FOCUS_DURATIONS=[25,45,60];
const FOCUS_TIMER_KEY="wb_focus_timer";
const FOCUS_AUTO_NOTE_KEY="wb_focus_auto_note";
const FOCUS_NOTIFY_KEY="wb_focus_notify";
const FOCUS_RANGE_KEY="wb_focus_range";
const LEGACY_TIME_BARS_SEED={"每日自动签到":95,"贵人计划落地":62,"内容创作":48,"副业收入自动化":27,"个人知识库索引":12};
let focusTicker=null;
let focusRange=(()=>{const n=+(loadNew(FOCUS_RANGE_KEY,14));return [7,14,30].includes(n)?n:14;})();
let TIME_PROJECTS=loadNew("wb_time_projects",[]);
if(!Array.isArray(TIME_PROJECTS)) TIME_PROJECTS=[];
TIME_PROJECTS=TIME_PROJECTS.map(p=>({
  id:p.id||uidGen(),name:String(p.name||"").trim(),source:"local",status:p.status||"进行中"
})).filter(p=>p.name);

function legacyTimeBars(raw){
  if(!raw||typeof raw!=="object"||Array.isArray(raw)) return {};
  const ks=Object.keys(raw), sk=Object.keys(LEGACY_TIME_BARS_SEED);
  if(ks.length===sk.length&&sk.every(k=>Number(raw[k])===Number(LEGACY_TIME_BARS_SEED[k]))) return {};
  return raw;
}
let TIME_BARS=legacyTimeBars(loadNew("wb_time_bars",{}));
if(!Object.keys(TIME_BARS).length) saveNew("wb_time_bars",{});

function mmss(s){
  s=Math.max(0,Math.ceil(+s||0));
  return String(Math.floor(s/60)).padStart(2,"0")+":"+String(s%60).padStart(2,"0");
}
function focusProjectKey(p){
  return p&&p.source==="notion" ? "notion:"+(p.notionId||p.id) : "local:"+(p&&p.id||"free");
}
function focusProjectOptions(){
  const notion=(state&&state.projects||[]).filter(p=>p.source==="notion"&&p.status==="进行中");
  const local=(state&&state.projects||[]).filter(p=>p.source!=="notion"&&!["已完成","已取消"].includes(p.status));
  const extras=TIME_PROJECTS.filter(p=>!local.some(x=>x.id===p.id));
  return [...notion,...local,...extras].map(p=>({
    value:focusProjectKey(p),label:p.name,source:p.source||"local",status:p.status||"进行中",raw:p
  }));
}
function focusProjectByValue(value){
  return focusProjectOptions().find(p=>p.value===value)||null;
}
function freshFocusTimer(minutes=25,projectId=""){
  const m=FOCUS_DURATIONS.includes(+minutes)?+minutes:25;
  return {durationMinutes:m,remainingSeconds:m*60,startAt:null,endAt:null,running:false,
    projectId:projectId||"free",status:"idle"};
}
function loadFocusTimer(){
  const x=loadNew(FOCUS_TIMER_KEY,null);
  if(!x||typeof x!=="object") return freshFocusTimer();
  const m=FOCUS_DURATIONS.includes(+x.durationMinutes)?+x.durationMinutes:25;
  return {
    durationMinutes:m,
    remainingSeconds:Math.max(0,Math.min(m*60,+x.remainingSeconds||m*60)),
    startAt:x.startAt||null,
    endAt:x.endAt||null,
    running:!!x.running,
    projectId:x.projectId||"free",
    status:x.running?"running":(x.status==="paused"?"paused":"idle")
  };
}
let focusTimer=loadFocusTimer();
function saveFocusTimer(){saveNew(FOCUS_TIMER_KEY,focusTimer);}
function focusTimerRemaining(){
  if(focusTimer.running&&focusTimer.endAt) return Math.max(0,Math.ceil((+focusTimer.endAt-Date.now())/1000));
  return Math.max(0,+focusTimer.remainingSeconds||0);
}
function focusTimerElapsedSeconds(){
  return Math.max(0,focusTimer.durationMinutes*60-focusTimerRemaining());
}
function focusAutoNoteEnabled(){return loadNew(FOCUS_AUTO_NOTE_KEY,false)===true;}
function focusNotifyEnabled(){
  try{return loadNew(FOCUS_NOTIFY_KEY,false)===true&&"Notification" in window&&Notification.permission==="granted";}
  catch(e){return false;}
}
function focusTimeLabel(iso){
  if(!iso) return "—";
  const d=new Date(iso); if(isNaN(d)) return "—";
  return new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",hour:"2-digit",minute:"2-digit",hour12:false}).format(d);
}
function focusSessionsForDate(date){
  return (state&&Array.isArray(state.focusSessions)?state.focusSessions:[])
    .filter(s=>s.date===date)
    .sort((a,b)=>String(b.startAt||b.createdAt||"").localeCompare(String(a.startAt||a.createdAt||"")));
}
function focusRangeDays(n){
  const out=[], base=new Date(focusDateStr()+"T12:00:00+08:00");
  for(let i=n-1;i>=0;i--){const d=new Date(base);d.setDate(d.getDate()-i);out.push(focusDateStr(d));}
  return out;
}
function focusStats(days=1){
  const dates=new Set(days===1?[focusDateStr()]:focusRangeDays(days));
  const rows=(state&&Array.isArray(state.focusSessions)?state.focusSessions:[]).filter(s=>dates.has(s.date));
  const completed=rows.filter(s=>s.status==="completed");
  return {
    completed:completed.length,
    abandoned:rows.filter(s=>s.status==="abandoned").length,
    minutes:Math.round(sum(completed,s=>s.elapsedSeconds)/60),
    sessions:rows
  };
}
function renderFocusControls(){
  const selected=focusTimer.durationMinutes;
  FOCUS_DURATIONS.forEach(n=>{
    const b=$("dur"+n);
    if(b){b.className="segbtn"+(selected===n?" on":"");b.setAttribute("aria-pressed",String(selected===n));}
  });
  const auto=$("focusAutoNote"); if(auto) auto.checked=focusAutoNoteEnabled();
  const nb=$("focusNotifyBtn");
  if(nb) nb.textContent=focusNotifyEnabled()?"提醒已开启":"开启浏览器提醒";
  const title=$("timeFocusTitle");
  if(title) title.textContent=`现在只专注一个 ${selected} 分钟`;
}
function syncFocusTimerUI(){
  const remaining=focusTimerRemaining();
  const clock=$("clock"); if(clock){clock.textContent=mmss(remaining);clock.classList.toggle("run",focusTimer.running);}
  const toggle=$("tmToggle"); if(toggle) toggle.textContent=focusTimer.running?"暂停":(remaining<focusTimer.durationMinutes*60?"继续":"开始");
  const tag=$("timerStateTag");
  if(tag) tag.textContent=focusTimer.running?"进行中":(focusTimer.status==="paused"?"已暂停":"未开始");
  const st=$("focusStatus");
  if(st){
    const stateTxt=focusTimer.running?"计时进行中 · 刷新或切后台后仍按截止时间校正":
      (focusTimer.status==="paused"?`已暂停 · 剩余 ${mmss(remaining)}`:"准备好后开始");
    st.innerHTML=`<i class="dot ${focusTimer.running?"run":focusTimer.status==="paused"?"paused":""}"></i><span>${esc(stateTxt)}</span>`;
  }
  renderFocusControls();
}
function renderTimeStats(){
  const today=focusStats(1), week=focusStats(7);
  $("timeStats").innerHTML=statCells([
    {k:"今日完成",v:today.completed,hint:"个番茄"},
    {k:"今日专注",v:today.minutes,hint:"分钟"},
    {k:"今日中断",v:today.abandoned,hint:"次"},
    {k:"近 7 天专注",v:week.minutes,hint:"分钟"}
  ]);
}
function focusNoteLine(s){
  const range=focusTimeLabel(s.startAt)+"–"+focusTimeLabel(s.endAt);
  const mins=Math.max(1,Math.round((+s.elapsedSeconds||0)/60));
  const status=s.status==="completed"?"完成":"中断";
  return `[专注] ${range} · ${s.projectName||"自由专注"} · ${mins} 分钟 · ${status}`;
}
function renderFocusToday(){
  const rows=focusSessionsForDate(focusDateStr());
  const done=rows.filter(s=>s.status==="completed").length;
  const tag=$("focusTodayTag"); if(tag) tag.textContent=`${rows.length} 条 · 完成 ${done}`;
  $("focusTodayList").innerHTML=rows.length?rows.map(s=>{
    const status=s.status==="completed"?"完成":"中断";
    const cls=s.status==="completed"?"ok":"warn";
    const canNote=s.status==="completed"&&!s.noteAddedAt;
    const notionUrl=s.notionUrl?safeUrl(s.notionUrl):"";
    return `<div class="focus-session" data-focus-id="${esc(s.id)}">
      <span class="fs-time">${esc(focusTimeLabel(s.startAt))}</span>
      <div class="fs-main"><b>${esc(s.projectName||"自由专注")}</b>
        <span>${esc(focusTimeLabel(s.startAt))}–${esc(focusTimeLabel(s.endAt))} · ${Math.max(1,Math.round((+s.elapsedSeconds||0)/60))} 分钟${s.noteAddedAt?" · 已写今日备注":""}${s.notionPageId?" · 已回写 Notion":""}</span></div>
      <div class="fs-side"><span class="chip ${cls}">${status}</span>
        ${canNote?`<button class="wc-copy" type="button" onclick="appendFocusToNote('${esc(s.id)}')">写备注</button>`:""}
        ${s.status==="completed"&&notionUrl?`<a class="wc-copy" href="${esc(notionUrl)}" target="_blank" rel="noopener noreferrer">Notion ↗</a>`:
          (s.status==="completed"?`<button class="wc-copy" type="button" onclick="pushFocusToNotion('${esc(s.id)}')">回写 Notion</button>`:"")}
        <button class="wc-remove" type="button" onclick="undoFocusSession('${esc(s.id)}')">撤销</button></div>
    </div>`;
  }).join(""):`<div class="focus-empty compact">
    <div class="fe-copy"><b>今天还没有专注记录</b><span>完成或放弃一次计时后会出现在这里；顶部番茄钟可直接开始。</span></div>
  </div>`;
}
function renderFocusTrend(){
  const dates=focusRangeDays(focusRange);
  const map={};
  dates.forEach(d=>map[d]={completed:0,minutes:0,abandoned:0});
  (state&&state.focusSessions||[]).forEach(s=>{
    if(!map[s.date]) return;
    if(s.status==="completed"){map[s.date].completed++;map[s.date].minutes+=Math.round((+s.elapsedSeconds||0)/60);}
    else map[s.date].abandoned++;
  });
  const max=Math.max(1,...dates.map(d=>map[d].minutes));
  $("focusBars").innerHTML=dates.map(d=>{
    const v=map[d].minutes, h=v?Math.max(3,Math.round(v/max*92)):2;
    const day=d.slice(5), label=focusRange===7?day:d.slice(8);
    return `<div class="focus-bar${d===focusDateStr()?" today":""}" title="${esc(d)} · ${v} 分钟">
      <i style="height:${h}px"></i><span>${esc(label)}</span></div>`;
  }).join("");
  const total=dates.reduce((n,d)=>n+map[d].minutes,0);
  const completed=dates.reduce((n,d)=>n+map[d].completed,0);
  const abandoned=dates.reduce((n,d)=>n+map[d].abandoned,0);
  const activeDays=dates.filter(d=>map[d].minutes>0).length;
  $("focusBars").classList.toggle("empty",total===0);
  const tag=$("focusTrendTag"); if(tag) tag.textContent=`${focusRange} 天 · ${completed} 个完成`;
  const trend=$("focusTrendCard");
  const trendBody=trend&&trend.querySelector(".focus-trend");
  const empty=total===0&&completed===0&&abandoned===0;
  if(trendBody) trendBody.hidden=empty;
  let emptyBox=trend&&trend.querySelector(".focus-trend-empty");
  if(!emptyBox&&trend){
    emptyBox=document.createElement("div");
    emptyBox.className="focus-trend-empty";
    trend.appendChild(emptyBox);
  }
  if(emptyBox){
    emptyBox.hidden=!empty;
    emptyBox.innerHTML=empty?`<div><b>这 ${focusRange} 天还没有完成记录</b>
      <span>完成首个番茄后按上海日期累计，空白日不补假数据。</span></div>`:"";
  }
  $("focusSummary").innerHTML=
    `<div><b>${total}</b>分钟专注</div>`+
    `<div><b>${activeDays}</b>天有记录</div>`+
    `<div><b>${abandoned}</b>次中断</div>`+
    `<div><span>趋势只统计本地真实记录；空白日为 0，不补假数据。</span></div>`;
  [7,14,30].forEach(n=>{const b=$("focus"+n);if(b){b.className="segbtn"+(focusRange===n?" on":"");b.setAttribute("aria-pressed",String(focusRange===n));}});
}
function renderTimeBars(){
  const mins={};
  (state&&state.focusSessions||[]).forEach(s=>{
    if(s.status!=="completed") return;
    mins[s.projectId]=(mins[s.projectId]||0)+Math.round((+s.elapsedSeconds||0)/60);
  });
  const projects=focusProjectOptions();
  const rows=projects.map(p=>({id:p.value,name:p.label,source:p.source,value:mins[p.value]||0}));
  Object.keys(mins).forEach(id=>{
    if(rows.some(r=>r.id===id)) return;
    const old=(state&&state.focusSessions||[]).find(s=>s.projectId===id);
    rows.push({id,name:old&&old.projectName||"历史项目",source:"local",value:mins[id]});
  });
  const max=Math.max(1,...rows.map(r=>r.value));
  rows.sort((a,b)=>b.value-a.value||a.name.localeCompare(b.name,"zh-CN"));
  const notionCount=projects.filter(p=>p.source==="notion").length;
  const localCount=projects.filter(p=>p.source!=="notion").length;
  const todayMinutes=focusStats(1).minutes;
  const brief=$("focusProjectBrief");
  if(brief) brief.textContent=`${notionCount} 个 Notion 进行中 · ${localCount?`${localCount} 个本地 · `:""}今日 ${todayMinutes}m`;
  $("timeBars").innerHTML=rows.length?rows.map(r=>{
    const w=r.value?Math.max(2,Math.round(r.value/max*100)):0;
    const canDelete=r.source!=="notion";
    return `<div class="exprow"><div class="top"><span class="nm">${esc(r.name)}
      <span class="chip ${r.source==="notion"?"acc":""}">${r.source==="notion"?"Notion":"本地"}</span></span>
      <span style="display:flex;gap:8px;align-items:center"><span class="amt" style="color:var(--accent)">${r.value}m</span>
      ${canDelete&&r.id.startsWith("local:")?`<button class="wc-remove" onclick="delTimeProj('${esc(r.id)}')" aria-label="删除 ${esc(r.name)}">移除</button>`:""}</span></div>
      <div class="expbar"><i style="width:${w}%;background:var(--ink)"></i></div></div>`;
  }).join(""):`<div class="empty">当前没有可计时项目。同步 Notion 或添加一个本地项目。</div>`;
  const legacy=Object.keys(TIME_BARS);
  const box=$("legacyTimeBars");
  if(box){
    box.hidden=!legacy.length;
    box.innerHTML=legacy.length?`<h3>旧版本地工时 <span class="tag">不计入专注记录</span></h3>`+
      legacy.map(k=>`<div class="exprow"><div class="top"><span class="nm">${esc(k)}</span>
        <span><span class="amt">${esc(TIME_BARS[k])}m</span> <button class="wc-remove" onclick="delLegacyTime('${esc(k)}')">移除</button></span></div></div>`).join(""):"";
  }
}
