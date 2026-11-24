/* ---------- render ---------- */
function renderKPIs(){
  const c=compute(), t=todayStr();
  const delta=(cur,prev)=>{
    if(prev===0&&cur===0) return `<div class="delta flat">— 无对比</div>`;
    const d=cur-prev, pp=prev? Math.round(d/prev*100):100;
    const cls=d>0?"up":(d<0?"down":"flat");
    const arr=d>0?"▲":(d<0?"▼":"■");
    return `<div class="delta ${cls}">${arr} 比${pp>=0?"":"-"}前 ${fmt(Math.abs(d))} (${pp>=0?"+":""}${pp}%)</div>`;
  };
  const dcount=(cur,prev)=>{          // 计数用这个：delta() 里 fmt() 会加 ¥，对"笔数"是错的
    if(prev===0&&cur===0) return `<div class="delta flat">— 无对比</div>`;
    const d=cur-prev, pp=prev? Math.round(d/prev*100):100;
    const cls=d>0?"up":(d<0?"down":"flat");
    const arr=d>0?"▲":(d<0?"▼":"■");
    return `<div class="delta ${cls}">${arr} 比前 ${Math.abs(d)} 笔 (${pp>=0?"+":""}${pp}%)</div>`;
  };
  const goalPctTxt = state.monthlyGoal? pctTxt(c.month,state.monthlyGoal):null;
  const topExp = c.expList[0];
  const spark=sparkline(c.spark14);
  const cards=[
    {label:"本日净额",dot:"var(--pos)",val:fmt(c.today),sub:t.slice(5),delta:delta(c.today,c.yest),spark},
    {label:"本月净额",dot:"var(--accent)",val:fmt(c.month),sub:goalPctTxt!==null?("目标 "+goalPctTxt):"未设目标",delta:delta(c.month,c.lastMonth),spark},
    {label:"总净额",dot:"var(--accent)",val:fmt(c.total),sub:"收入 "+fmt(c.income)+" / 支出 "+fmt(c.expense),delta:`<div class="delta flat">累计，含支出扣减</div>`},
    {label:"近 7 日净额",dot:"var(--pri-600)",val:fmt(c.week),sub:"滚动 7 天",delta:delta(c.week,c.prevWeek),spark},
    /* 活跃度：笔数掉得比金额更早、更明显。现在 3 笔 vs 年内月均 9.2 笔 */
    {label:"本月成交笔数",dot:"var(--warn)",val:c.cntThis+" 笔",
      sub: c.cntAvgY? `${t.slice(0,4)} 年月均 ${c.cntAvgY} 笔`:"无对比",
      delta: dcount(c.cntThis,c.cntPrev)},
    {label:"累计支出",dot:"var(--neg)",val:fmt(c.expense),
      sub: topExp? `最大项 ${topExp.k} ${fmt(topExp.v)}（${pct(topExp.v,c.expense)}%）`:"暂无支出",
      delta:`<div class="delta flat">本月 ${fmt(c.expMonth)} · ${c.expKinds} 类</div>`},
    {label:"进行中项目",dot:"var(--warn)",val:c.active+" 个",sub:"共 "+state.projects.length+" 个",delta:`<div class="delta flat">进行中</div>`},
    {label:"平均进度",dot:"var(--ok)",val:(c.withProgress?c.avg+"%":"—"),sub:(c.withProgress?"已设进度的项目":"均未设置进度"),delta:`<div class="delta flat">均值</div>`}
  ];
  $("kpis").innerHTML=cards.map(k=>`
    <div class="kpi">
      <div class="label"><span class="dot" style="background:${k.dot}"></span>${k.label}</div>
      <div class="value">${k.val}</div>
      <div class="sub">${k.sub}</div>
      ${k.delta}${k.spark||""}
    </div>`).join("");
}
/* ---------- 分析口径 ----------
   「看结构」的三张卡（收入来源 / 收益构成 / 支出构成）必须同一个范围，
   否则会出现「来源是累计、构成是本月」这种读者无从判断的错位 —— 一个概念只留一个控件。
   目标带**不在**这里：目标是月度的，恒为本月，并在标题里显式写明。 */
const AN_SCOPES=[["month","本月"],["d90","近 90 天"],["all","累计"]];
function anScopeLabel(k){const f=AN_SCOPES.find(x=>x[0]===(k||anScope));return f?f[1]:"累计";}
function anPool(){
  if(anScope==="month") return state.earnings.filter(e=>e.date.startsWith(todayStr().slice(0,7)));
  if(anScope==="d90")   return state.earnings.filter(e=>inLastDays(e.date,90));
  return state.earnings;
}
function setAnScope(k){
  if(anScope===k) return;
  anScope=k;
  renderScopeBar();renderSourceRank();renderDonut();renderExpenseBreak();
}
function renderScopeBar(){
  [["anM","month"],["anD90","d90"],["anA","all"]].forEach(([id,k])=>{
    const b=$(id); if(b) b.className="segbtn"+(anScope===k?" on":"");
  });
  const h=$("scopeHint"); if(!h) return;
  const pool=anPool(), inc=pool.filter(e=>e.amount>0), exp=pool.filter(e=>e.amount<0);
  h.innerHTML=`当前范围 <b>${anScopeLabel()}</b>：收入 <b>${inc.length}</b> 笔 ${fmt(sum(inc,e=>e.amount))}`+
    (exp.length?` · 支出 ${exp.length} 笔 ${fmt(sum(exp,e=>-e.amount))}`:" · 无支出")+
    `　共 ${pool.length} 条记录`;
}

/* ---------- 目标带（工作台的目标装置） ----------
   放第一屏、全宽、恒为本月口径。三段式：目标 / 进度 / 怎么补上缺口。
   设计要点：「还差 ¥X」本身不构成行动，所以第三段专门把缺口翻译成能做的事 ——
   剩余天数 → 日均需求；本月平均单笔 → 还需要几笔。数字全部由真实记录推导，不手写。

   2026-09-29 晚增补：整页单位切换 / 拖动条 / 快捷预设 / 目标达成史。
   要点是"怎么定目标"这件事以前只能靠拍脑袋，现在给的是几个有数据依据的起点；
   并且目标按月留痕（state.goals）—— 否则目标一改，历史达成率跟着变，等于没有历史。 */
function focusGoal(){
  if(curType!=="ledger") setType("ledger");
  const settings=$("goalSettings"); if(settings) settings.open=true;
  const el=$("goalHero"); if(el&&el.scrollIntoView) el.scrollIntoView({behavior:"smooth",block:"center"});
  const i=$("goalInput"); if(i&&i.focus) i.focus();
}
function focusHealth(){
  if(curType!=="ledger") setType("ledger");
  const el=$("healthCard"); if(el&&el.scrollIntoView) el.scrollIntoView({behavior:"smooth",block:"start"});
}

/* 首屏只回答三个行动问题：今天推进哪个项目、先处理哪条异常、本月目标还差多少。
   下方 KPI 仍保留完整经营信息，但不再要求用户先读完一排数字才知道下一步。 */
function renderDailyDeck(){
  const now=todayStr(), sync=(state.lastNotionSync||"").slice(0,16).replace("T"," ")||"尚未同步";
  const dateTxt=new Date(now+"T12:00:00").toLocaleDateString("zh-CN",{month:"long",day:"numeric",weekday:"short"});
  $("deckDate").textContent=dateTxt;
  $("todayLine").textContent=`${dateTxt} · Notion 最近同步 ${sync}`;
  renderSinceLastVisit();

  const active=(state.projects||[]).filter(p=>!(p.status==="已完成"||p.status==="已取消"));
  const urgent=active.map(p=>({p,r:projectRisk(p)})).filter(x=>x.r).sort((a,b)=>a.r.days-b.r.days)[0];
  // 「正在进行但已 100%」比一条一年前的低优先级逾期项更值得出现在今日首页：
  // 前者只差一次状态收口，后者更像历史数据清理，仍会完整保留在项目页「需要处理」。
  const runningConflict=active.find(p=>p.status==="进行中"&&projectConflict(p));
  const priority=active.find(p=>/^P0\b/.test(p.priority||""));
  const lead=runningConflict||priority||(urgent&&urgent.p)||active.find(p=>p.status==="进行中")||active[0];
  let projectTitle="暂无待推进项目", projectSub="同步后会从真实项目中挑出下一项";
  if(lead){
    const risk=projectRisk(lead), pct=(lead.progress===null||lead.progress===undefined)?"进度未设":`进度 ${lead.progress}%`;
    projectTitle=lead.name;
    projectSub=projectConflict(lead)?projectConflictText(lead):(risk?risk.txt:`${lead.status||"未标状态"} · ${pct}`);
  }
  $("deckProject").innerHTML=`<span class="dk">项目推进</span><strong>${esc(projectTitle)}</strong><small>${esc(projectSub)} →</small>`;

  const issues=healthIssues(), issue=issues.find(x=>x.level==="warn")||issues[0];
  $("deckHealth").innerHTML=issue
    ?`<span class="dk">经营体检 · ${issues.length} 项</span><strong>${esc(issue.title)}</strong><small>${esc(issue.detail)} →</small>`
    :`<span class="dk">经营体检</span><strong>当前没有发现异常</strong><small>查看完整体检 →</small>`;

  const c=compute(), goal=+(state.monthlyGoal||0), gap=Math.max(0,goal-c.month);
  const goalTitle=goal>0?(gap>0?`还差 ${fmt(gap)}`:`已超目标 ${fmt(c.month-goal)}`):"设定本月目标";
  const goalSub=goal>0?`本月净额 ${fmt(c.month)} / 目标 ${fmt(goal)}`:
    (c.lastMonth>0?`可从上月净额 ${fmt(c.lastMonth)} 起步`:`先用一个可完成的小目标起步`);
  $("deckGoal").innerHTML=`<span class="dk">月度目标</span><strong>${goalTitle}</strong><small>${goalSub} →</small>`;
}

/* 「上次以来」摘要：给不常打开的人 30 秒补课（现网仪表盘共识：
   current status / trend / target / next action 之外，还要有 since-last-visit）。
   口径：以 localStorage 存的"上次看到的数据指纹"为准，与本轮数据比差异；
   首次打开不显示（没有基线就没有"以来"）。看本届面只读，不参与任何写入。 */
function dataFingerprint(s){
  const es=s.earnings||[];
  return {n:es.length,
    last:(es.map(e=>e.date).sort().pop()||""),
    sum:Math.round(es.reduce((a,e)=>a+(e.amount||0),0)*100)/100,
    p:(s.projects||[]).length,
    sync:String(s.lastNotionSync||"")};
}
function renderSinceLastVisit(){
  const box=$("sinceLast"); if(!box) return;
  if(guest){ box.innerHTML=""; box.style.display="none"; return; }
  const KEY="wb_lastvisit_fp";
  let prev=null;
  try{ prev=JSON.parse(localStorage.getItem(KEY)||"null"); }catch(e){}
  const cur=dataFingerprint(state);
  const lines=[];
  if(prev){
    if(cur.n!==prev.n){
      const dn=cur.n-prev.n;
      lines.push(`收益记录 ${dn>0?"+":""}${dn} 笔（现 ${cur.n} 条）`);
    }
    if(cur.sum!==prev.sum) lines.push(`累计净额变化 ${fmt(Math.abs(cur.sum-prev.sum))}${cur.sum>prev.sum?"↑":"↓"}`);
    if(cur.p!==prev.p) lines.push(`项目 ${cur.p-prev.p>0?"+":""}${cur.p-prev.p} 个（现 ${cur.p} 个）`);
    if(cur.sync!==prev.sync && cur.sync) lines.push("Notion 已重新同步");
  }
  // 渲染后立即写新指纹：本轮看到的就算"看过"，下次打开才比。
  try{ localStorage.setItem(KEY,JSON.stringify(cur)); }catch(e){}
  if(!lines.length){ box.innerHTML=""; box.style.display="none"; return; }
  box.style.display="";
  box.innerHTML=`<div class="since-bar"><span class="since-k">上次以来</span>`+
    lines.map(l=>`<span class="since-i">${esc(l)}</span>`).join("")+`</div>`;
}

/* --- 整页显示单位：一个单位只留一个控件，不给每张卡各配一个 --- */
const UNIT_BTN=[["uYuan","yuan"],["uK","k"],["uWan","wan"]];
function renderUnitSeg(){
  UNIT_BTN.forEach(([id,k])=>{const b=$(id); if(b) b.className="segbtn"+(moneyUnit===k?" on":"");});
}
function setMoneyUnit(k){
  if(moneyUnit===k || !UNITS.some(u=>u[0]===k)) return;
  moneyUnit=k;
  try{ localStorage.setItem("wb_unit",k); }catch(e){}
  renderAll();          // 单位是整页的（每张卡上的金额都跟着走），所以整页重绘
}

/* --- 目标的读写 ---
   目标只有一个真值：state.monthlyGoal，单位永远是**元**。
   输入框与拖动条都只是它的显示视图，数值按当前单位换算 —— 视图可以有两个，真值只能有一个。 */
function goalToInput(yuan){const d=unitDef()[2];return d===1?yuan:Math.round(yuan/d*100)/100;}
function inputToGoal(v){const d=unitDef()[2];return Math.round((+v||0)*d*100)/100;}
/* 拖动条的上下限。上限取「有意义的最大目标」的 2 倍并取整到 10 格：
   上限太小拖不到需要的值，上限太大则整条滑轨大半程没有意义。
   基准里必须包含**已设目标**，否则一旦目标被手动填得很大，滑块就再也够不着它。
   注意这是"滑块覆盖合理区间、数字框覆盖任意值"的分工，不是硬上限。

   ⚠️ 量纲必须分清：peakYuan 是**元**，而滑块的 min/max/step 必须是**当前显示单位**下的数。
   两者混用会让万元档的 max 变成 199998（＝20 亿元）。所以先按元算格数，最后一步才乘 step 换回显示单位。 */
let slideSpecLock=null;   // 拖动期间钉住的量程（见下）
function sliderSpec(){
  /* ⚠️ 量程绝不能随"当前目标"实时变化 —— 滑块自己就在改目标，那会形成正反馈：
     鼠标停在原地不动，量程跟着目标一起涨，同一格对应的金额越来越大，
     值就自己往上飙。2026-09-29 真机实测：拖一次从 30 万元冲到 **¥1,554.50 万元**。
     （DOM 桩测不出来，因为桩里不会"跟踪鼠标位置重算映射"。）
     所以整个拖动手势期间用 slideSpecLock 把量程钉死在手势开始的那一刻，
     松手后再按新目标重算 —— 一次手势能被拖出的最大金额因而是有界的。 */
  if(slideSpecLock) return slideSpecLock;
  const d=unitDef()[2], ym=todayStr().slice(0,7), c=compute();
  const past=monthlySeries().filter(x=>x.m!==ym).map(x=>x.net);
  const peakYuan=Math.max.apply(null,[c.month,c.lastMonth,state.monthlyGoal||0].concat(past,[10000]));
  const stepYuan=1000;                                  // 一格恒 = 1000 元，三种单位下一致
  const cells=Math.max(20, Math.ceil(Math.ceil(peakYuan*2/stepYuan)/10)*10);
  const r2=v=>Math.round(v*100)/100;
  const step=r2(stepYuan/d);
  return {min:0, max:r2(cells*step), step};
}
function syncGoalSlider(){
  const g=state.monthlyGoal||0, sp=sliderSpec();
  const sl=$("goalRange");
  if(sl){
    sl.min=sp.min; sl.max=sp.max; sl.step=sp.step;
    sl.value=Math.min(sp.max, Math.max(sp.min, goalToInput(g)));
  }
  // 数字框的步进跟着单位走：万元档下按 100 元上下调没有意义
  const inp=$("goalInput");
  if(inp) inp.step=(unitDef()[2]===1?100:sp.step);
  const cl=$("goalClear"); if(cl) cl.style.display = g>0 ? "" : "none";
}
/* 单位不是「元」时，输入框里的数到底是多少必须**显式写出来** ——
   否则"填 3 是 3 万还是 3 元"完全没法确认，这正是切单位最容易出错的地方。 */
function echoGoal(){
  const e=$("goalEcho"); if(!e) return;
  // 一有输入就清掉上一次的错误态 —— 错误提示不该粘着不走
  e.className="goalecho";
  const gi=$("goalInput"); if(gi) gi.removeAttribute("aria-invalid");
  const d=unitDef()[2], g=inputToGoal($("goalInput").value);
  if(!(g>0)){
    e.textContent = d===1 ? "填一个正数，或直接拖下面的滑块"
                          : `按当前单位「${unitLabel()}」填入正数，或直接拖滑块`;
    return;
  }
  e.innerHTML = d===1 ? `将设为 <b>${fmt(g)}</b>`
                      : `= <b>${fmt(g)}</b> · 当前输入按「${unitLabel()}」解释`;
}
/* 目标校验的错误就显示在输入框下方那个等值回显位上 ——
   错误信息应该在出错的那个控件旁边，而不是飞到屏幕中央的弹窗里。 */
function goalErr(msg){
  const e=$("goalEcho"); if(!e) return;
  e.className="goalecho err"; e.textContent=msg;
  const i=$("goalInput"); if(i){i.setAttribute("aria-invalid","true"); if(i.focus) i.focus();}
}
function goalKey(ev){
  if(ev && ev.key==="Enter"){ if(ev.preventDefault) ev.preventDefault(); setGoal(); return; }
  if(ev && ev.key==="Escape"){
    const i=$("goalInput");
    if(i) i.value = (state.monthlyGoal>0 ? goalToInput(state.monthlyGoal) : "");
    echoGoal();
  }
}
/* 落盘策略：拖动过程中**只改内存并重绘**（跟手），松手才 POST。
   否则每移动一格都会打一次同步服务。真值始终是 state.monthlyGoal，所以画面与账目不会分叉。 */
function applyGoal(g, persist){
  if(!(g>0)) return;
  const ym=todayStr().slice(0,7);
  state.monthlyGoal=g;
  state.goals=state.goals||{};
  state.goals[ym]=g;            // 按月留痕：这个月当时定的多少，以后永远可查
  if(persist!==false) saveState();
  renderGoal();renderGoalHist();renderKPIs();renderHealth();
}
function onGoalSlide(commit){
  const sl=$("goalRange"); if(!sl) return;
  if(!slideSpecLock) slideSpecLock=sliderSpec();   // 手势开始：把量程钉住（此时还没 apply，读到的是拖动前的目标）
  const g=inputToGoal(sl.value);
  if(!(g>0)){
    // 拖到最左端 = 0。**不当作"清除目标"**（那是破坏性动作），把滑块弹回去并说明。
    syncGoalSlider();
    const e=$("goalEcho"); if(e) e.textContent="最左端不是「清除」—— 要清除目标请用输入框旁的「清除」按钮。";
    return;
  }
  const i=$("goalInput"); if(i) i.value=goalToInput(g);
  applyGoal(g, !!commit);
  if(commit) slideSpecLock=null;                   // 松手：放开，下次按新目标重算量程
}
function clearGoal(){
  if(!(state.monthlyGoal>0)) return;
  state.monthlyGoal=0;
  // goals 里同月那条也要去掉，否则目标带说"未设目标"而达成史还显示一个目标，两边打架
  if(state.goals) delete state.goals[todayStr().slice(0,7)];
  const i=$("goalInput"); if(i) i.value="";
  saveState();renderGoal();renderGoalHist();renderKPIs();renderHealth();
}

/* --- 快捷预设 ---
   值全部由真实数据推导：上月实际 / 上月 +20% / 按当前节奏外推的月末 / 历史最好月。
   这正是"该定多少"最常被问的四个参照，也是唯一比拍脑袋强的起点。 */
function goalPresets(){
  const c=compute(), t=todayStr(), ym=t.slice(0,7);
  const day=+t.slice(8), dim=daysInMonth(+t.slice(0,4),+t.slice(5,7));
  const proj=day? c.month/day*dim : c.month;
  const past=monthlySeries().filter(x=>x.m!==ym && x.n>0).map(x=>x.net);
  const best=past.length? Math.max.apply(null,past) : 0;
  /* 取整必须**跟着量级走**。原先固定 Math.round(v/100)*100，在小额的月份会把语义不同的
     几个预设压成同一个数：实测 2026-09 上月净额 ¥260.87→¥300、上月 +20% ¥313.04→¥300、
     按当前节奏 ¥329.86→¥300 —— 四个按钮里三个显示 ¥300、点下去结果完全一样，
     「+20% 的野心」这个语义直接消失。
     当时的断言只查了"是 100 的整数倍"，所以三条全绿、什么都没抓到。
     步长按量级选：既保住"好看的数字"，又不吃掉差异。 */
  const nice=v=>{ const st = v>=10000?1000 : v>=1000?100 : v>=100?10 : 1;
                  return Math.round(v/st)*st; };
  const out=[];
  if(c.lastMonth>0) out.push({label:"上月净额",v:nice(c.lastMonth)});
  if(c.lastMonth>0) out.push({label:"上月 +20%",v:nice(c.lastMonth*1.2)});
  if(proj>0)        out.push({label:"按当前节奏",v:nice(proj)});
  if(best>0)        out.push({label:"历史最好月",v:nice(best)});
  return dedupeByValue(out);
}
/* 取整后仍可能撞值（上月 ¥2 与 +20% ¥2.4 都落在 ¥2；或"按当前节奏"恰好等于"上月净额"）。
   撞了就丢掉后者：宁可少给一个参照，也不要摆两个点下去结果一样的按钮。
   **抽成纯函数是为了能单独断言** —— 当前真实数据不会触发撞值，不抽出来这段就是死代码，
   "到底有没有生效"永远验不到（这也是上面那个 round(100) 缺陷能活下来的同一种原因）。 */
function dedupeByValue(list){
  const seen=new Set();
  return list.filter(p=>{ if(!(p.v>0) || seen.has(p.v)) return false; seen.add(p.v); return true; });
}
function renderGoalPreset(){
  const b=$("goalPreset"); if(!b) return;
  const ps=goalPresets();
  b.innerHTML = ps.length
    ? ps.map(p=>`<button class="pbtn" onclick="usePreset(${p.v})">${p.label} ${fmt(p.v)}</button>`).join("")
    : `<span class="ghr">本月没有可参照的数据，先有一笔进账再谈目标。</span>`;
}
function usePreset(v){const i=$("goalInput"); if(i) i.value=goalToInput(v); applyGoal(v);}

/* --- 目标达成史：最近 6 个月的真实净额 vs **当时设的目标** ---
   月度净额复用 monthlySeries()（趋势图用的同一份聚合），不另写一遍 —— 同一个口径只能有一份实现。 */
function renderGoalHist(){
  const box=$("goalHist"); if(!box) return;
  const ym=todayStr().slice(0,7), goals=state.goals||{};
  box.innerHTML=monthlySeries().slice(-6).map(x=>{
    const g=+goals[x.m]||0, now=x.m===ym;
    const rate=g>0? Math.round(x.net/g*100) : null;
    const cls=rate===null? "" : (x.net>=g? "ok":"bad");
    const rTxt=g>0? pctTxt(x.net,g) : null;   // 与目标带、KPI 同一套"不到 1%"规则
    return `<div class="ghcell${now?" now":""}">
      <div class="ghk">${x.label}${now?" · 本月":""}</div>
      <div class="ghv">${fmt(x.net)}</div>
      <div class="ghr">${g>0 ? `<span class="${cls}">${rTxt}</span> / ${fmt(g)}` : `<span>未设目标</span>`}</div>
    </div>`;
  }).join("");
}

/* --- 三段式主体 ---
   收尾动作（同步滑块/回显/预设）必须**在早退分支之后也执行**，所以外包一层：
   未设目标时 renderGoalBody 会 return，控件却仍需与当前单位、当前值对齐。 */
function renderGoal(){
  renderGoalBody();
  syncGoalSlider(); echoGoal(); renderGoalPreset(); renderGoalSettingsSummary();
}
function renderGoalSettingsSummary(){
  const hint=$("goalSettingsSummary"); if(!hint) return;
  const goal=+(state.monthlyGoal||0), c=compute();
  hint.textContent=goal>0
    ? `当前 ${fmt(goal)} · 已达成 ${pctTxt(c.month,goal)}`
    : "尚未设定 · 从有依据的起点开始";
}
function renderGoalBody(){
  const c=compute(), t=todayStr(), ym=t.slice(0,7);
  const goal=state.monthlyGoal||0;
  const day=+t.slice(8), dim=daysInMonth(+t.slice(0,4),+t.slice(5,7)), left=Math.max(0,dim-day);
  $("goalTag").textContent=ym;

  // 本月实际：用于把缺口换算成"还需要几笔"。平均单笔取自本月真实收入，不是我拍的数。
  const mInc=state.earnings.filter(e=>e.date.startsWith(ym)&&e.amount>0);
  const mIncV=sum(mInc,e=>e.amount), mIncN=mInc.length;
  const avgTicket=mIncN? mIncV/mIncN : 0;
  const mk=$("goalPaceMarker");

  if(!goal){
    // 没目标时**不显示一堆 0** —— 那看起来像"达成了 0%"，是个假进度。
    // 改成给引导，建议值取自上月真实净额，让起点有依据。
    $("goalAll").textContent="未设目标";$("goalAll").className="hval dim";
    $("goalFill").style.width="0%";
    if(mk) mk.style.display="none";
    $("goalNow").textContent=`本月已入账 ${fmt(c.month)}`;
    $("goalPaceTxt").textContent=`${ym} · 第 ${day}/${dim} 天`;
    $("goalProj").innerHTML="";
    $("goalRag").textContent="";$("goalRag").className="rag";
    const sug=Math.round(c.lastMonth/100)*100;
    $("goalAction").innerHTML=`<div class="gempty">
      没有目标就答不出「还差多少」—— 而这正是这个工作台的主要用途。<br>
      ${c.lastMonth>0
        ? `建议起点：上月实际净额 <b>${fmt(c.lastMonth)}</b>${sug>0?`，可取整为 <b>${fmt(sug)}</b>`:""}。`
        : `上月净额是 <b>${fmt(c.lastMonth)}</b>（非正）—— 这时该先看「支出构成」和「收入来源」，而不是拉高目标。`}
      <button class="btn ghost" onclick="focusGoal()">去设定</button></div>`;
    return;
  }

  const pctRaw=goal? c.month/goal*100 : 0;
  const p=Math.min(100,pct(c.month,goal));
  const pTxt=pctTxt(c.month,goal);
  $("goalAll").textContent="目标 "+fmt(goal);$("goalAll").className="hval";
  $("goalFill").style.width=p+"%";
  // 时间刻度：今天"应该"完成的比例（day/dim）。进度条压没压过这条线，一眼看出超前还是落后。
  if(mk){mk.style.display="";mk.style.left=Math.min(100,Math.round(day/dim*100))+"%";}
  const gap=goal-c.month;
  const proj=day? Math.round(c.month/day*dim) : c.month;
  $("goalNow").innerHTML=`已达成 ${fmt(c.month)} · <b>${pTxt}</b>${gap<=0?" 目标已达成":""}`;
  $("goalPaceTxt").textContent=`${ym} 第 ${day}/${dim} 天 · 按当前节奏月末约 ${fmt(proj)}`;
  $("goalProj").innerHTML= gap>0
    ? `<div class="goalgap"><span class="gk">还差</span><b>${fmt(gap)}</b>
       <span class="gs">${left>0?`还剩 ${left} 天`:"本月已到最后一天"}</span></div>`
    : `<div class="goalgap"><span class="gk">已超额</span><b style="color:var(--ok)">${fmt(-gap)}</b>
       <span class="gs">${left>0?`还剩 ${left} 天`:"本月结束"}</span></div>`;

  const r=$("goalRag");
  if(gap<=0){r.textContent="目标已达成 ✓";r.className="rag ok";}
  else if(proj>=goal){r.textContent="预计可达成 ✓";r.className="rag ok";}
  else if(proj>=goal*0.7){r.textContent="进度偏慢，需加把劲";r.className="rag warn";}
  else{r.textContent="预计难达成";r.className="rag bad";}

  // 把缺口翻译成行动，而不是只报一个差额
  if(gap<=0){
    $("goalAction").innerHTML=`<ul class="gact">
      <li>目标已过线，超额 <b>${fmt(-gap)}</b>。</li>
      <li>若后半程维持当前日均（${fmt(day? c.month/day:0)}/天），月末约 <b>${fmt(proj)}</b>。</li>
      <li>可以把目标上调到 <b>${fmt(Math.round(proj/100)*100)}</b> 再观察。</li></ul>`;
  }else if(left<=0){
    $("goalAction").innerHTML=`<ul class="gact">
      <li>本月已结束，缺口 <b>${fmt(gap)}</b>。</li>
      <li>下月目标建议按实际能力重设（点左侧「设定」）。</li></ul>`;
  }else{
    // 只剩两三天时，"日均需 ¥99,681" 虽然算得对，但毫无行动价值 —— 换一句诚实的话说。
    const tooLate=left<=3;
    /* 笔数换算同样要过"可行动"这一关：算式对 ≠ 值得显示。
       缺口折合 2820 笔而本月只成交 3 笔时，摆出"还差 2820 笔"会让人以为"多接几单就行"。
       所以超过本月成交量 20 倍就不再说成"还要几笔的量"，直说靠加量补不回来。 */
    const need=avgTicket>0? Math.ceil(gap/avgTicket) : 0;
    const plausible=mIncN>0 && need<=mIncN*20;
    const perTicket=mIncN>0
      ? `<li>本月平均单笔 <b>${fmt(avgTicket)}</b>（${mIncN} 笔）。`+
        (plausible
          ? (tooLate? `缺口约合 <b>${need}</b> 笔。` : `还差约 <b>${need}</b> 笔的量。`)
          : `缺口折合 <b>${need}</b> 笔，是本月成交量的 <b>${Math.round(need/mIncN)}</b> 倍 —— 靠多接几单补不回来。`)+`</li>`
      : `<li>本月还没有收入记录，先有一笔进账才能谈节奏。</li>`;
    $("goalAction").innerHTML=`<ul class="gact">
      ${tooLate
        ? `<li>本月只剩 <b>${left}</b> 天，缺口 <b>${fmt(gap)}</b> —— 这几天基本补不上，更现实的是把它顺延到下月。</li>`
        : `<li>剩 <b>${left}</b> 天，日均需 <b>${fmt(gap/left)}</b>（当前日均 ${fmt(c.month/day)}）。</li>`}
      ${perTicket}
      <li>谁还能加量：看下方「收入来源」，点某一行可把明细筛到那一组。</li></ul>`;
  }
  $("goalInput").value=goalToInput(goal);
}
function renderDonut(){
  const scope=anScopeLabel();
  const pool=anPool();
  const inc=pool.filter(e=>e.amount>0);
  const exp=sum(pool.filter(e=>e.amount<0),e=>Math.abs(e.amount));
  const byType={};
  inc.forEach(e=>byType[e.type]=(byType[e.type]||0)+e.amount);
  const palette=["var(--ink)","var(--pri-600)","var(--pri-700)","var(--neu-500)","var(--neu-200)","var(--neu-100)","var(--line)","var(--bg)"];
  let segs=Object.entries(byType).map(([k,v])=>({k,v})).filter(s=>s.v>0).sort((a,b)=>b.v-a.v);
  const top=segs.slice(0,6), rest=segs.slice(6);
  let extra=0; rest.forEach(s=>extra+=s.v);
  if(extra>0) top.push({k:"其他",v:extra});
  const segs2=top.map((s,i)=>({k:s.k,v:s.v,c:palette[i%palette.length]}));
  const total=sum(segs2,s=>s.v)||1;
  /* 环细一点、半径大一点：内径要装得下完整金额（如 ¥27,092.69），否则文字压到环上 */
  const R=52,C=2*Math.PI*R; let off=0;
  let svg=`<circle cx="60" cy="60" r="${R}" fill="none" stroke="var(--line)" stroke-width="11"/>`;
  segs2.forEach(s=>{const len=C*s.v/total;svg+=`<circle cx="60" cy="60" r="${R}" fill="none" stroke="${s.c}" stroke-width="11" stroke-dasharray="${len} ${C-len}" stroke-dashoffset="${-off}" transform="rotate(-90 60 60)"/>`;off+=len;});
  svg+=`<text x="60" y="56" text-anchor="middle" font-size="10" fill="var(--muted)">${scope}收入</text><text x="60" y="72" text-anchor="middle" font-size="11.5" font-weight="700" fill="var(--ink)">${fmt(total)}</text>`;
  $("donut").innerHTML=svg;
  $("donutLg").innerHTML=segs2.map(s=>`<div><i style="background:${s.c}"></i>${s.k} ${fmt(s.v)}</div>`).join("")||`<div>${scope}暂无收入</div>`;
  $("expenseLine").innerHTML=exp? `${scope}支出合计 <b>${fmt(exp)}</b>，${scope}净额 <b>${fmt(total-exp)}</b>` : `${scope}暂无支出`;
  if($("donutTag")) $("donutTag").textContent=`${scope} · ${inc.length} 笔入账`;
}
function renderStreak(){
  const days=new Set(state.earnings.map(e=>e.date));
  let s=0;let d=new Date(todayStr());
  if(!days.has(todayStr())) d.setDate(d.getDate()-1);
  while(days.has(todayStr(d))){s++;d.setDate(d.getDate()-1);}
  $("streak").innerHTML=`已连续记录 <b>${s}</b> 天<br><span style="font-size:var(--fs-xs)">每天记一笔，保持手感</span>`;
}
function renderChart(){
  const n=30,t=todayStr();const days=[];
  for(let i=n-1;i>=0;i--){const d=new Date(t);d.setDate(d.getDate()-i);days.push(todayStr(d));}
  const amts=days.map(d=>sum(state.earnings.filter(e=>e.date===d),e=>e.amount));
  const max=Math.max(1,...amts.map(Math.abs));
  const W=680,H=170,pad=6,bw=(W-pad*2)/n;
  let svg="";
  const zero=H-18;
  days.forEach((d,i)=>{
    const v=amts[i],h=Math.abs(v)/max*(H-30);
    const x=pad+i*bw, y=v>=0?zero-h:zero;
    const col=v>=0?"var(--pos)":(v<0?"var(--neg)":"var(--line)");
    if(h>0) svg+=`<rect x="${x+bw*0.2}" y="${y}" width="${bw*0.6}" height="${h}" rx="1.5" fill="${col}" ${d===t?'opacity="1" stroke="var(--accent)" stroke-width="1"':''}/>`;
  });
  svg+=`<line x1="${pad}" y1="${zero}" x2="${W-pad}" y2="${zero}" stroke="var(--line)" stroke-width="1"/>`;
  $("chart").innerHTML=svg;
}
/* ---------- 近 12 个月趋势（柱=净额，线=成交笔数） ----------
   纯手写 SVG，不用 Chart.js/CDN —— 离线兜底页必须能独立渲染。
   笔数走独立的纵向映射区间，不与金额共用刻度，避免两条线互相误读。 */
let chartMode="d30";
function monthlySeries(){
  const t=todayStr(), base=new Date(+t.slice(0,4), +t.slice(5,7)-1, 1), out=[];
  for(let i=11;i>=0;i--){
    const d=new Date(base.getFullYear(), base.getMonth()-i, 1);
    const m=d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0");
    const rows=state.earnings.filter(e=>e.date.startsWith(m));
    out.push({m,label:m.slice(2).replace("-","/"),net:sum(rows,e=>e.amount),n:rows.length});
  }
  return out;
}
function setChartMode(m){
  chartMode=m;
  $("chart").style.display = m==="d30"?"":"none";
  $("chartM").style.display = m==="m12"?"":"none";
  $("segD30").className="segbtn"+(m==="d30"?" on":"");
  $("segM12").className="segbtn"+(m==="m12"?" on":"");
  $("chartLegend").innerHTML = m==="d30"
    ? '<span><i style="background:var(--pos)"></i>盈利（红）</span><span><i style="background:var(--neg)"></i>亏损（绿）</span><span><i style="background:var(--accent)"></i>当日</span>'
    : '<span><i style="background:var(--pos)"></i>月度净额为正</span><span><i style="background:var(--neg)"></i>月度净额为负</span><span><i style="background:var(--pri-600)"></i>成交笔数（独立刻度）</span>';
  if(m==="m12") renderChartM();
}
function renderChartM(){
  const s=monthlySeries(), W=680, H=170, pad=10, top=16, bot=H-26;
  const maxP=Math.max(0,...s.map(x=>Math.max(0,x.net)));
  const maxN=Math.max(0,...s.map(x=>Math.max(0,-x.net)));
  const span=(maxP+maxN)||1, hAll=bot-top;
  const hP=hAll*maxP/span, hN=hAll*maxN/span, zero=top+hP;
  const bw=(W-pad*2)/12, maxCnt=Math.max(1,...s.map(x=>x.n));
  let svg="", hit="", bars="", line=[];
  s.forEach((x,i)=>{
    const cx=pad+i*bw+bw/2, w=bw*0.56, bx=cx-w/2;
    hit+=`<rect x="${pad+i*bw}" y="${top}" width="${bw}" height="${hAll}" fill="transparent"><title>${x.m}　净额 ${fmt(x.net)}　${x.n} 笔</title></rect>`;
    if(x.net>0&&maxP>0) bars+=`<rect x="${bx}" y="${zero-hP*x.net/maxP}" width="${w}" height="${Math.max(1.5,hP*x.net/maxP)}" rx="2" fill="var(--pos)"/>`;
    else if(x.net<0&&maxN>0){const h=hN*(-x.net)/maxN; bars+=`<rect x="${bx}" y="${zero}" width="${w}" height="${Math.max(1.5,h)}" rx="2" fill="var(--neg)"/>`;}
    line.push([cx,(bot-6)-((bot-6)-(top+6))*x.n/maxCnt]);
  });
  svg+=hit+bars;
  svg+=`<line x1="${pad}" y1="${zero}" x2="${W-pad}" y2="${zero}" stroke="var(--line)" stroke-width="1"/>`;
  svg+=`<polyline points="${line.map(p=>p[0]+","+p[1]).join(" ")}" fill="none" stroke="var(--pri-600)" stroke-width="1.6"/>`;
  line.forEach(p=>{svg+=`<rect x="${p[0]-2.5}" y="${p[1]-2.5}" width="5" height="5" fill="var(--pri-600)"/>`;});
  s.forEach((x,i)=>{svg+=`<text x="${pad+i*bw+bw/2}" y="${H-9}" text-anchor="middle" font-size="10" fill="var(--muted)">${x.m.slice(2)}</text>`;});
  $("chartM").innerHTML=svg;
}
/* 支出构成：按真实分类拆开，而不是笼统的「支出」 */
/* 支出按真实分类拆分。抽成纯函数是为了让它能被测试直接喂不同口径的 pool ——
   原先它写死在 renderExpenseBreak 里并且恒为累计，跟「收益构成」的月份口径对不上。 */
function expBreak(pool){
  const rows=pool.filter(e=>e.amount<0), by={};
  rows.forEach(e=>{const k=e.type||"未分类";
    if(!by[k]) by[k]={v:0,n:0};
    by[k].v+=Math.abs(e.amount); by[k].n++;});
  const list=Object.entries(by).map(([k,o])=>({k,v:o.v,n:o.n})).sort((a,b)=>b.v-a.v);
  return {list:list,total:sum(list,x=>x.v),kinds:list.length,n:rows.length};
}
function renderExpenseBreak(){
  const scope=anScopeLabel(), b=expBreak(anPool());
  $("expTag").textContent = `${scope} ${fmt(b.total)}`+(b.n?` · ${b.n} 笔`:"");
  if(!b.list.length||b.total<=0){ $("expBreak").innerHTML=`<div class="empty">${scope}暂无支出记录</div>`; return; }
  const palette=["var(--ink)","var(--pri-600)","var(--pri-700)","var(--neu-500)","var(--neu-200)","var(--neu-100)","var(--line)"];
  $("expBreak").innerHTML=b.list.map((x,i)=>`
    <div class="exprow">
      <div class="top">
        <span class="nm">${esc(x.k)} <span class="rochip">${x.n} 笔</span></span>
        <span class="amt">-${fmt(x.v)}</span>
      </div>
      <div class="expbar"><i style="width:${Math.max(2,Math.round(x.v/b.total*100))}%;background:${palette[i%palette.length]}"></i></div>
    </div>`).join("")
    + `<div class="expnote">共 ${b.kinds} 类、${b.n} 笔，合计 ${fmt(b.total)}（${scope}口径）。
       支出记录的「分类」字段存的是真实分类（AI 技能 / 投资 / 其他），不是「支出」这个统称 ——
       所以这里按真实分类拆分，钱的去向才看得见。</div>`;
}
/* ---------- 收入来源（备注 → 规范来源） ----------
   备注第一段就是"来源"，但它从来不是受控字段，而是随手写的任务描述：
   249 笔收入散成 200+ 种写法（同一件事有无数拼法）。规则文件由
   scripts/build-source-map.py 生成、随 data.json 一起下发（键名 sourceMap），
   规则顺序即优先级，首个命中生效。全部离线可用，不依赖任何 CDN。 */
let srcRules=null, srcRulesKey=null;
let srcFilter=null;          // 当前按来源筛选的组名（null=不筛）；由来源排行点一下设置
let srcRowsCache=[];         // 排行当前渲染的行，供 onclick 用索引取回（名字里有 / 和空格，不能拼进 onclick）
function compileSrcRules(){
  const sm=(state&&state.sourceMap)||null;
  // 按对象引用缓存：测试里会整体替换 state，用引用做 key 才不会拿到上一份规则
  if(srcRules&&srcRulesKey===sm) return srcRules;
  const defs=(sm&&sm.rules)||[];
  srcRules=defs.map(r=>{
    const pats=[];
    (r.patterns||[]).forEach(p=>{ try{ pats.push(new RegExp(p)); }catch(e){} });
    // scope="note" = 在整个备注里找；默认只看第一段。
    // 备注真实结构常是「任务 | 给<客户>做某事」——客户名在第二段，
    // 只看首段会把「给所罗门远程配置API」误判成 LionCC 的活（2026-09-29 踩过）。
    return {name:r.name, kind:r.kind||"", scope:r.scope||"seg1", note:r.note||"", pats:pats};
  });
  srcRulesKey=sm;
  return srcRules;
}
/* 备注首段：'|' 之前那段，就是当初写下的"来源" */
function firstSeg(note){const s=(note||"").trim();return (s.split("|")[0]||"").trim();}
/* 归类一条收入。merged=false 表示没命中规则，按原样算一个独立来源（长尾）。 */
function sourceOf(e){
  const seg=firstSeg(e.note)||"(未填备注)";
  const note=e.note||"";
  for(const r of compileSrcRules()){
    const hay=(r.scope==="note")? note : seg;
    for(const p of r.pats){ if(p.test(hay)) return {name:r.name, kind:r.kind, merged:true}; }
  }
  return {name:seg, kind:"", merged:false};
}
function sourceStats(pool){
  // pool 缺省 = 全部收益（累计口径）。渲染时传 anPool()，让来源卡跟随全局分析口径。
  const inc=(pool||state.earnings).filter(e=>e.amount>0);
  const total=sum(inc,e=>e.amount);
  const map={};   // name -> {n,v,merged,kind,first,last}
  inc.forEach(e=>{
    const s=sourceOf(e);
    const k=s.name;
    if(!map[k]) map[k]={name:k,n:0,v:0,merged:s.merged,kind:s.kind,first:e.date,last:e.date};
    const o=map[k];
    o.n++; o.v+=e.amount;
    if(e.date<o.first) o.first=e.date;
    if(e.date>o.last) o.last=e.date;
  });
  const all=Object.values(map).sort((a,b)=>b.v-a.v);
  const merged=all.filter(x=>x.merged);
  return {
    all:all, merged:merged, total:total, incomeN:inc.length,
    mergedN:sum(merged,x=>x.n), mergedV:sum(merged,x=>x.v),
    tailN:sum(all.filter(x=>!x.merged),x=>x.n),
    tailV:sum(all.filter(x=>!x.merged),x=>x.v),
    spellings:all.length,
  };
}
function renderSourceRank(){
  const box=$("srcRank"); if(!box) return;
  const noRules=!(state.sourceMap&&(state.sourceMap.rules||[]).length);
  const s=sourceStats(anPool());
  $("srcTag").textContent= noRules ? "未载入规则"
    : `${anScopeLabel()} · 已归并 ${s.merged.length} 组 · ${s.incomeN} 笔`;

  if(!s.incomeN){ box.innerHTML='<div class="empty">暂无收入记录</div>'; return; }

  // 归并后必须与真实总收入一致；不一致就说明规则把记录算漏/算重了，必须喊出来
  const diff=s.mergedV+s.tailV-s.total;
  const warn=Math.abs(diff)>0.005
    ? `<div class="srcwarn">⚠ 归并后金额 ${fmt(s.mergedV+s.tailV)} 与真实收入 ${fmt(s.total)}
       不一致（差 ${fmt(diff)}）—— 规则有问题，此表不可信。</div>` : '';

  if(noRules){
    box.innerHTML=warn+`<div class="srcwarn">data.json 里没有 <code>sourceMap</code>，
      来源归并被关掉了：下面每种写法会各算一个来源，<b>数字看着正常但是错的</b>。
      跑一次 <code>python3 scripts/build-source-map.py</code> 恢复。</div>`
      + `<div class="srcdot">没有规则时，前 12 位是：</div>`
      + s.all.slice(0,12).map((x,i)=>`
        <div class="srcrow">
          <span class="rk">${i+1}</span>
          <span class="nm">${esc(x.name)}</span>
          <span class="amt">${fmt(x.v)}</span>
        </div>`).join("");
    return;
  }

  const TOP=10;
  const head=s.merged.slice(0,TOP);
  const restMerged=s.merged.slice(TOP);
  const tailRow=(restMerged.length||s.tailN>0)?{
    name:`其他已归并 ${restMerged.length} 组 + 长尾 ${s.spellings-s.merged.length} 种写法`,
    n:sum(restMerged,x=>x.n)+s.tailN,
    v:sum(restMerged,x=>x.v)+s.tailV,
    merged:false, first:"", last:""
  }:null;
  const rows=head.concat(tailRow?[tailRow]:[]);
  const maxV=Math.max(...rows.map(x=>x.v),1);
  srcRowsCache=rows;

  box.innerHTML=warn+rows.map((x,i)=>{
    const isAgg=!x.merged&&i>0&&!x.kind;
    const on=srcFilter!==null&&srcFilter===x.name;
    const cls=(isAgg?"srcrow agg":"srcrow")+(on?" on":"");
    const pill=x.merged?`<span class="kindchip ${x.kind==="B1"?"k-b1":"k-a"}">${x.kind==="B1"?"层B1":"层A"}</span>`:``;
    const pctv=pct(x.v,s.total);
    // 聚合行不参与筛选（它不是单一来源）；其余行点一下就把明细筛到这一组，便于逐组核对
    // tabindex+role 让它进得了 Tab 序；Enter/Space 的激活由文件末尾那一个事件委托统一处理
    const click=isAgg? "" : ` onclick="setSrcFilter(${i})" style="cursor:pointer" tabindex="0" role="button" aria-pressed="${on?"true":"false"}"`;
    const hint=isAgg? "" : "　点一下筛下面明细";
    return `
    <div class="${cls}"${click} title="${esc(x.name)}${x.first?("　"+x.first+" ~ "+x.last):""}${hint}">
      <span class="rk">${isAgg?"—":i+1}</span>
      <span class="nm">${esc(x.name)} ${pill}</span>
      <span class="n">${x.n} 笔</span>
      <span class="amt">${fmt(x.v)}</span>
      <span class="pc">${pctv}%</span>
      <div class="srcbar"><i style="width:${Math.max(1,Math.round(x.v/maxV*100))}%"></i></div>
    </div>`;
  }).join("")
  + `<div class="srcnote">
      收入 ${fmt(s.total)} 全部有归属：已归并 ${s.mergedN} 笔 ${fmt(s.mergedV)}（${pct(s.mergedV,s.total)}%）
      + 长尾 ${s.tailN} 笔 ${fmt(s.tailV)}（${pct(s.tailV,s.total)}%）。
      「层A」是书写变体（事实），「层B1」是按金额档位与时间窗口推测的同一雇主（<b>判断，可撤销</b>）——
      规则见 data.json 的 <code>sourceMap</code>。</div>`;
}
/* 点来源行 → 把明细筛到这一组。用索引取回行数据，避免名字里的 / 和空格破坏 onclick。 */
function setSrcFilter(i){
  const r=srcRowsCache[i];
  if(!r) return;
  focusIds=null; focusLabel="";                        // 两个筛选维度互斥，避免叠加出空结果
  srcFilter = (srcFilter===r.name) ? null : r.name;   // 再点一下取消
  renderLedger(); renderHealth();
  const c=$("ledgerCard");
  if(c && c.scrollIntoView) c.scrollIntoView({behavior:"smooth",block:"start"});
}
function clearSrcFilter(){ srcFilter=null; renderLedger(); renderSourceRank(); }
/* ---------- 数据体检 ----------
   这些问题的共同点：**页面照常显示、数字照常算，但其中有几笔是错的**。
   所以必须显式列出来并可直接跳到那几条，否则永远没人会发现。 */
function healthIssues(){
  const E=state.earnings, out=[];
  const dayDiff=(a,b)=>Math.round((new Date(b)-new Date(a))/86400000);

  // 1. 完全重复：同一天 + 同一金额 + 同一备注。几乎不可能是巧合，多半是重复录入。
  const byKey={};
  E.forEach(e=>{
    const k=e.date+"|"+(Math.round(e.amount*100)/100)+"|"+((e.note||"").trim());
    (byKey[k]=byKey[k]||[]).push(e);
  });
  const dups=[];
  Object.values(byKey).forEach(v=>{ if(v.length>1) dups.push(v); });
  if(dups.length){
    out.push({level:"warn", title:`${dups.length} 组完全重复的记录`,
      detail:`同一天 + 同一金额 + 同一备注。下面列出多余记录，每组保留最早一条。`,
      ids:dups.map(g=>g[g.length-1].id),
      sum:sum(dups,g=>g[g.length-1].amount)});
  }

  // 2. 金额为 0：通常是漏填金额
  const zeros=E.filter(e=>e.amount===0);
  if(zeros.length){
    out.push({level:"warn", title:`${zeros.length} 条金额为 0`,
      detail:"多半是录入时漏填了金额，不计入任何统计。", ids:zeros.map(e=>e.id), sum:0});
  }

  // 3. 本地草稿待整理：只存在本地 data.json，还没写进 Notion
  const drafts=E.filter(e=>e.source==="local");
  const dp=state.projects.filter(p=>p.source==="local");
  if(drafts.length||dp.length){
    out.push({level:"info", title:`${drafts.length+dp.length} 条本地草稿待整理到 Notion`,
      detail:"这些只存在本地，Notion 里还没有。改完确认无误后可以说「整理到 Notion」。",
      ids:drafts.map(e=>e.id), sum:sum(drafts,e=>e.amount), extra:`项目 ${dp.length} 个`});
  }

  // 4. 进账断档：记录是连续的，断档说明"要么真没进账，要么忘了记"
  const incv=E.filter(e=>e.amount>0);
  if(incv.length){
    const dates=incv.map(e=>e.date).sort();
    const last=dates[dates.length-1];
    const gap=dayDiff(last, todayStr());
    const activeDays=new Set(dates).size;
    const span=Math.max(1, dayDiff(dates[0], last));
    // 每 avg 天约有 1 天产生进账；断档超过它的 2 倍（且至少 10 天）才提示，避免假警报
    const avg=Math.max(1, Math.round(span/activeDays));
    const thres=Math.max(10, avg*2);
    if(gap>=thres) out.push({level:"warn", title:`已 ${gap} 天没有进账`,
      detail:`最后一笔进账是 ${last}。按你自己的节奏，平均约 ${avg} 天有 1 天会产生进账，`
             +`现在断了 ${gap} 天 —— 可能是真的空白期，也可能是漏记了。`, ids:null, sum:0});
  }

  // 5. 收益没关联项目：关联率过低时这条建议才有意义
  const linked=E.filter(e=>(e.projectId||"").trim()).length;
  if(E.length && linked/E.length < 0.5){
    out.push({level:"info", title:`收益关联项目 ${linked}/${E.length} 条`,
      detail:"关联后才能看到「哪个项目在赚钱」。备注里没有项目名可自动匹配，"
             +"需要在「+ 记一笔」时手动选项目（或到 Notion 里补）。", ids:null, sum:0});
  }

  // 6. 月度目标未设定：这不是数据错误，是**页面主要用途失效** —— 没有目标就答不出
  //    「还差多少」。建议值取上月真实净额，避免给一个拍出来的数；
  //    目标带里还备了几个由真实数据推导的预设，点一下就是起点。
  if(!(state.monthlyGoal>0)){
    out.push({level:"warn", title:"还没设月度目标",
      detail:`没有目标，「离目标还差多少」就无从回答 —— 而这正是工作台的主要用途。`
             +(compute().lastMonth>0?`可用上月实际净额 ${fmt(compute().lastMonth)} 作为起点；首屏目标带里也有现成预设（上月净额 / +20% / 按当前节奏 / 历史最好月），点一下即可。`
                                    :`上月净额非正，建议先看支出构成，而不是拉高目标。`),
      ids:null, sum:0, jump:"goal", jumpText:"点这里去设定目标 →"});
  }
  return out;
}
function renderHealth(){
  const box=$("healthBody"); if(!box) return;
  const list=healthIssues();
  const warnN=list.filter(x=>x.level==="warn").length;
  $("healthTag").textContent = list.length? `${list.length} 项 · ${warnN} 项需处理` : "无异常";
  // 有问题默认展开，无异常默认收起：问题不该藏在折叠里
  const fold=$("healthFold"); if(fold) fold.open = warnN>0;
  if(!list.length){ box.innerHTML='<div class="empty">没有发现异常 ✓</div>'; return; }
  healthCache=list;
  box.innerHTML=list.map((x,i)=>{
    // 两类可跳：带具体记录的（跳明细筛选）和带 jump 目标的（跳到目标带/设定框）。
    const canJump=!!(x.ids&&x.ids.length) || !!x.jump;
    const money=x.sum? `<span class="amt">${fmt(x.sum)}</span>`:"";
    return `<div class="hrow ${x.level}"${canJump?` onclick="healthJump(${i})" style="cursor:pointer" tabindex="0" role="button"`:""}>
      <div class="htop">
        <span class="nm">${esc(x.title)}</span>${money}
      </div>
      <div class="hdet">${x.extra?esc(x.extra)+" · ":""}${esc(x.detail)}</div>
      ${canJump?`<div class="hjump">${esc(x.jumpText||"点这里跳到这几条 →")}</div>`:""}
    </div>`;
  }).join("");
}
/* 体检项点击分发：带 jump 的走对应跳转，其余按记录 id 跳明细。用索引取回，避免拼串出错。 */
function healthJump(i){
  const x=healthCache[i]; if(!x) return;
  if(x.jump==="goal"){ focusGoal(); return; }
  if(x.ids&&x.ids.length){ jumpToIds(i); return; }
}
/* 跳到明细并只显示指定的那几条。用索引取回，避免 id 拼进 onclick 出问题。 */
function jumpToIds(i){
  const x=healthCache[i]; if(!x||!x.ids) return;
  focusIds=x.ids.slice(); focusLabel=x.title;
  const lf=$("ledgerFold"); if(lf) lf.open=true;   // 体检跳转前先展开收益明细卡，否则目标行藏在折叠里
  renderLedger();
  const c=$("ledgerCard"); if(c&&c.scrollIntoView) c.scrollIntoView({behavior:"smooth",block:"start"});
}
function clearFocus(){ focusIds=null; focusLabel=""; renderLedger(); renderHealth(); }

function renderProjects(){
  const wrap=$("projects");
  $("projTag").textContent=state.projects.length+" 个项目";
  if(!state.projects.length){wrap.innerHTML='<div class="empty">暂无项目。<br>项目来自你的 Notion「项目管理」库（collection://00000000-0000-4000-8000-000000000002，只读），<br>也可以点右上角「+ 项目」手动添加。</div>';return;}
  const order={"风险":0,"进行中":1,"待启动":2,"未开始":2,"已完成":3,"已取消":3};
  const list=[...state.projects].sort((a,b)=>(order[a.status]??9)-(order[b.status]??9));
  const projectRow=p=>`
    <div class="proj" ${guest?"":`onclick="editProject('${p.id}')" tabindex="0" role="button"`} style="${guest?"":"cursor:pointer"}">
      <div class="top">
        <div class="nm">${esc(p.name)} <span class="cat">${esc(p.category||"未分类")}</span></div>
        <span class="badge b-${p.status}">${p.status}</span>
      </div>
      <div class="pbar"><i style="width:${p.progress==null?0:p.progress}%"></i></div>
      <div class="meta"><span>${p.progress==null?"进度 —":("进度 "+p.progress+"%")}</span><span>${p.due?("截止 "+p.due):"无截止"}</span></div>
    </div>`;
  const current=list.filter(p=>!(p.status==="已完成"||p.status==="已取消")), done=list.filter(p=>p.status==="已完成"||p.status==="已取消");
  wrap.innerHTML=current.map(projectRow).join("")+
    (done.length?`<details class="completed-group"><summary>已完成/已取消 ${done.length} 个<span>按需查看</span></summary>`+
      `<div class="completed-list">${done.map(projectRow).join("")}</div></details>`:"");
}
/* 收益明细：关键词 + 类型 + 时间范围 + 来源 四重过滤，行内可点开编辑（Notion 只读行除外） */
function ledgerFilters(){
  const q=($("ledQ")&&$("ledQ").value||"").trim().toLowerCase();
  const t=($("ledType")&&$("ledType").value)||"";
  const rg=($("ledRange")&&$("ledRange").value)||"";
  const y=String(new Date().getFullYear());
  return state.earnings.filter(e=>{
    // 体检跳过来时只看指定的那几条；否则正常走下面的筛选
    if(focusIds) return focusIds.indexOf(e.id)>=0;
    // 「支出」不是一个真实分类，而是"金额为负"的统称：
    // Notion 拉回来的支出记录，type 存的是真实分类（AI 技能 / 投资 / 其他），
    // 支出标记在 product 字段上。所以这里必须按金额符号判断，不能用 type===「支出」。
    if(t===EXPENSE_LABEL){ if(!(e.amount<0)) return false; }
    else if(t && (e.type||"")!==t) return false;
    if(srcFilter!==null){ if(!(e.amount>0) || sourceOf(e).name!==srcFilter) return false; }
    if(rg==="d30" && !inLastDays(e.date,30)) return false;
    if(rg==="d90" && !inLastDays(e.date,90)) return false;
    if(rg==="ytd" && !(e.date||"").startsWith(y)) return false;
    if(q){
      const hay=[e.note,e.type,e.product].join(" ").toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
}
/* 分类下拉复用 typeOptions()（按真实记录频次排序），每轮 renderAll 重建但保留用户已选值 */
function fillLedTypeOptions(){
  const sel=$("ledType"); if(!sel) return;
  const keep=sel.value;
  const opts=[""].concat(typeOptions());          // typeOptions() 末尾已含「支出」
  const labels=["全部类型"].concat(opts.slice(1));
  const last=labels.length-1;
  if(labels[last]==="支出") labels[last]="仅支出";  // 标明是"按金额符号筛"，不是按分类
  sel.innerHTML=opts.map((v,i)=>`<option value="${esc(v)}">${esc(labels[i])}</option>`).join("");
  sel.value=opts.indexOf(keep)>=0? keep : "";
}
function toggleLedAll(){
  ledAll=!ledAll;
  $("ledMore").textContent=ledAll? "只看最近" : "看全部";
  renderLedger();
}
function renderLedger(){
  const tb=$("ledger").querySelector("tbody");
  const total=state.earnings.length;
  // 必须对「过滤后的结果」排序再切前 N 条。filter() 返回的是原数组顺序（按 Notion 同步进来的
  // 时间升序），漏了这一步就会永远展示最旧的 12 条 —— 之前就是把 sort 加在了没用的副本上。
  // filter() 已返回新数组，这里原地 sort 不会污染 state.earnings。
  const list=ledgerFilters().sort((a,b)=>(a.date<b.date?1:(a.date>b.date?-1:0)));
  const filtered = list.length!==total;
  const shown=list.slice(0, ledAll? list.length : 12);

  // tag 始终说清「现在看到的是哪一段」，只读来源作为后缀，避免两种情况互相覆盖
  const srcTagTxt = focusIds ? `体检：${focusLabel} `
    : (srcFilter!==null ? `来源「${srcFilter}」 ` : "");
  const scope = (filtered||focusIds)
    ? `${srcTagTxt}${shown.length} / ${list.length} 笔`
    : (ledAll? `全部 ${list.length} 笔` : `最近 ${shown.length} 笔`);
  $("ledgerSrc").textContent = state.earningsReadonly ? scope+" · 只读" : scope;
  const lc=$("ledClear");
  lc.style.display = (focusIds||srcFilter!==null) ? "" : "none";
  lc.textContent = focusIds ? "✕ 体检筛选" : (srcFilter!==null ? "✕ 来源筛选" : "");
  lc.onclick = focusIds? clearFocus : clearSrcFilter;

  if(!shown.length){
    tb.innerHTML=`<tr><td colspan="4" class="empty">${total? "没有符合条件的记录，放宽一下筛选试试":"还没有收益记录"}</td></tr>`;
    return;
  }
  tb.innerHTML=shown.map(e=>{
    const ro=e.readOnly? '<span class="rochip" title="来自 Notion，只读不可改">Notion</span>':'';
    /* 支出标记：金额为负就是支出。若 type 本身已经叫「支出」就不重复标（老的本地草稿是那样存的）。 */
    const expchip=(e.amount<0 && e.type!==EXPENSE_LABEL)? '<span class="rochip exp" title="这是一笔支出">支出</span>':'';
    const editable=!guest && !e.readOnly;
    const del= editable? `<button class="del" title="删除" aria-label="删除 ${esc(e.date)} 的 ${fmt(e.amount)}" onclick="event.stopPropagation();delEarn('${e.id}')">✕</button>`:"";
    const edit= editable? `<button class="edit" title="编辑" aria-label="编辑 ${esc(e.date)} 的 ${fmt(e.amount)}" onclick="event.stopPropagation();editEarn('${e.id}')">✎</button>`:"";
    const click= editable? ` class="clickable" onclick="editEarn('${e.id}')" title="点击编辑"`:"";
    const p=e.projectId? state.projects.find(x=>x.id===e.projectId):null;
    const note=esc(e.note||"")+(p? ` <span class="rochip">${esc(p.name)}</span>`:"");
    return `<tr${click}>
      <td>${esc(e.date)}</td>
      <td>${esc(e.type)} ${expchip} ${ro}</td>
      <td>${note}</td>
      <td class="amt ${e.amount>=0?"up":"down"}">${fmt(e.amount)} ${edit}${del}</td>
    </tr>`;
  }).join("");
}
/* 同步一次：POST /api/sync，把脚本输出原样回显给用户，成功后再拉一次最新数据 */
async function runSync(trigger){
  const top=$("syncTop");
  if(!SERVED){ notify("同步需要本地同步服务（127.0.0.1:8787）。\n当前是离线单文件模式，无法触发同步。\n\n请改用 http://127.0.0.1:8787/workbench.html?token=<你的令牌> 打开。"); return; }
  if(!TOKEN){
    notify("同步失败：链接缺少访问令牌。\n\n请用带 ?token= 的完整链接打开本页；\n或运行 scripts/tools/open-workbench.sh 获取正确链接。\n（本浏览器打开过一次带令牌的链接后，就会记住它。）");
    return;
  }
  if(guest){ notify("访客模式下不能同步，避免影响你的真实数据。请先退出访客模式。","warn"); return; }
  if(syncing) return;
  syncing=true;
  const topHTML=top?top.innerHTML:null;
  if(top) top.disabled=true;
  if(top) top.textContent="同步中…";
  const t0=Date.now();
  try{
  const r=await fetch(SYNC_API+(SYNC_API.includes("?")?"&":"?")+"src="+(trigger==="auto"?"auto":"manual"),{method:"POST"});
    const j=await r.json().catch(()=>({}));
    const secs=((Date.now()-t0)/1000).toFixed(1);
    if(r.ok && j.ok){
      notify(`✓ 同步完成（${secs}s）· 收益 ${j.earnings} 条 / 项目 ${j.projects} 个\n最近同步：${j.lastNotionSync? new Date(j.lastNotionSync).toLocaleString("zh-CN"):"—"}`,"info");
      await loadState(); renderAll();
    }else if(r.status===409){
      notify("已有一次同步正在进行，请稍候几秒再点。","warn");
    }else if(r.status===401){
      notify("同步失败（HTTP 401）：访问令牌无效或缺失。\n链接里的 ?token= 与 ~/.config/daily-workbench/sync-token 不一致（服务端只有在首次运行且该文件不存在时才会重新生成）。\n\n修复：用 scripts/tools/open-workbench.sh 打印当前有效的完整链接后重新打开。");
    }else{
      notify(`同步失败（HTTP ${r.status}，${secs}s）\n${j.error||""}\n\n${j.output||""}`.trim());
    }
  }catch(e){
    notify("同步请求失败："+e.message+"\n确认本地同步服务在 127.0.0.1:8787 上运行。");
  }finally{
    syncing=false;
    if(top){ top.disabled=false; top.innerHTML=topHTML; }
    renderNotionPanel();
    await loadSyncLog();
  }
}
/* 打开页面时若数据过期（>1 天）自动同步一次；localStorage 当天只跑一次 */
const AUTOSYNC_KEY="wb_autosync_day";
async function maybeAutoSync(){
  if(!SERVED||!TOKEN||guest||syncing||loadError) return;
  const d=new Date();
  const today=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
  try{ if(localStorage.getItem(AUTOSYNC_KEY)===today) return; }catch(e){}
  const last=state.lastNotionSync? new Date(state.lastNotionSync):null;
  const stale = !last || (Date.now()-last.getTime())/86400000 >= 1;
  if(!stale) return;
  try{ localStorage.setItem(AUTOSYNC_KEY,today); }catch(e){}
  await runSync("auto");
}
function syncFailureIsCurrent(log=latestSyncLog){
  if(!log||log.ok||guest) return false;
  const failedAt=Date.parse(log.ts);
  const successAt=Date.parse(state&&state.lastNotionSync);
  return !Number.isFinite(failedAt)||!Number.isFinite(successAt)||failedAt>=successAt;
}
function syncLogReason(e){
  const raw=String((e&&e.error)||"").trim();
  if(raw&&!/^=+/.test(raw)) return raw;
  const bad=((e&&e.steps)||[]).find(s=>s&&s.ok===false);
  return bad&&String(bad.detail||"").trim()||"";
}
function renderSyncAlert(log=latestSyncLog){
  const box=$("syncAlert"); if(!box) return;
  if(!syncFailureIsCurrent(log)){box.hidden=true; return;}
  const step=((log.steps||[]).find(s=>s&&s.ok===false))||{};
  const trigger=log.trigger==="auto"?"自动同步":"手动同步";
  const elapsed=typeof log.elapsed==="number"&&Number.isFinite(log.elapsed)?`耗时 ${log.elapsed.toFixed(1)}s`:"";
  const reason=syncLogReason(log)||"未返回具体错误";
  const detail=[`${trigger} · ${syncLogTime(log.ts)}`,step.name?`${step.name}失败`:"",reason,elapsed].filter(Boolean).join(" · ");
  const version=state&&state.lastNotionSync?new Date(state.lastNotionSync).toLocaleString("zh-CN",{hour12:false}):"暂无成功版本";
  box.hidden=false;
  $("syncAlertMeta").innerHTML=`${esc(detail)}<br>当前展示 <b>${esc(version)}</b> 的成功数据；本次失败未覆盖现有数据。`;
}
function renderNotionPanel(){
  // 状态点：绿=已连，灰=未连，橙=从未同步。title 即完整状态，悬停可见。
  const never=!state.lastNotionSync;
  const ok=state.notionStatus==="connected";
  const syncFailed=syncFailureIsCurrent();
  const dotTitle = syncFailed
    ? "最近一次同步失败 · 当前仍使用 "+(state.lastNotionSync?new Date(state.lastNotionSync).toLocaleString("zh-CN"):"上一版")+" 数据"
    : ok
      ? "已连接 Notion · 最近同步 "+(state.lastNotionSync?new Date(state.lastNotionSync).toLocaleString("zh-CN"):"")
      : (never?"从未同步":"未连接 Notion");
  for(const id of ["syncDotTop"]){
    const dot=$(id);
    if(!dot) continue;
    dot.className="sync-dot "+(syncFailed?"warn":(ok?"ok":(never?"off":"warn")))+(id==="syncDotTop"?" lead":"");
    dot.title=dotTitle;
  }
  const ls=state.lastNotionSync? new Date(state.lastNotionSync).toLocaleString("zh-CN"):"从未";
  // 把「数据实际来自哪个库」写出来 —— 之前配错过数据源（挂到「我的项目」那 2 条模板上了），
  // 显示出来才能一眼发现回归。
  const ps=state.projectsSource;
  const srcLine=ps&&ps.name? `项目来源：<b>${esc(ps.name)}</b>（只读）`:"";
  if(state.notionStatus==="connected"){
    const ro = state.earningsReadonly
      ? `收益数据<b>只读</b>拉取自你的 Notion「贵人计划 / 副业收入 + 产品支出」，<b>不回写</b>该页面。<br>你在此工作台「记一笔」的本地草稿仅存于本地，等你整理时再由我写入 Notion。`
      : `已与 Notion 双向同步。<br>`;
    const failLine=syncFailed?`<br><b>最近一次同步失败：</b>${esc(syncLogReason(latestSyncLog)||"未返回具体错误")}`:"";
    $("notionNote").innerHTML=`已与 Notion 连接。${ro}${srcLine? "<br>"+srcLine:""}<br>最近同步：${ls}${failLine}<br>顶部「同步数据」可手动拉取最新。`;
  }else{
    $("notionNote").innerHTML=`数据最终整理进 Notion。连接 Notion 后，每日工作台会把本工作台的收益（只读拉取自你的「贵人计划」页面）与项目同步过去。<br>最近同步：${ls}<br>当前为本地存储，离线可用。`;
  }
}
let syncLogRequestId=0;
let syncLogAbortController=null;
async function loadSyncLog(){
  const requestId=++syncLogRequestId;
  if(syncLogAbortController) syncLogAbortController.abort();
  syncLogAbortController=typeof AbortController!=="undefined"?new AbortController():null;
  const isCurrent=()=>requestId===syncLogRequestId;
  if(!SERVED||!TOKEN){latestSyncLog=null;renderSyncAlert(null);return;}
  const hint=$("syncLogHint"), list=$("syncLogList");
  if(!hint||!list) return;
  if(guest){ latestSyncLog=null; renderSyncAlert(null); list.textContent="访客模式下不展示同步日志"; hint.textContent="—"; renderNotionPanel(); return; }
  try{
    const r=await fetch(SYNC_LOG_API,{signal:syncLogAbortController&&syncLogAbortController.signal});
    if(!isCurrent()) return;
    if(!r.ok){ latestSyncLog=null; renderSyncAlert(null); list.textContent="同步日志需要带令牌的链接打开"; renderNotionPanel(); return; }
    const j=await r.json().catch(()=>({}));
    if(!isCurrent()) return;
    const logs=Array.isArray(j.logs)?j.logs:[];
    if(!logs.length){ latestSyncLog=null; renderSyncAlert(null); list.textContent="还没有同步记录"; hint.textContent="查看"; renderNotionPanel(); return; }
    const last=logs[logs.length-1];
    latestSyncLog=last;
    renderSyncAlert(last);
    renderNotionPanel();
    hint.textContent=fmtSyncLogShort(last);
    list.innerHTML=logs.slice().reverse().map((e,i)=>
      `<div class="synclog-entry"><span class="synclog-time">${esc(syncLogTime(e.ts))}</span>`+
      `<span class="synclog-line ${e.ok?"":"synclog-fail"}">${esc(fmtSyncLogLine(e))}</span></div>`+
      /* 步骤明细只给最新一条：历史记录全是成功摘要，全展开只会刷屏 */
      (i===0&&Array.isArray(e.steps)&&e.steps.length?renderSyncSteps(e):"")).join("");
  }catch(e){
    if(e&&e.name==="AbortError") return;
    if(!isCurrent()) return;
    latestSyncLog=null;
    renderSyncAlert(null);
    renderNotionPanel();
    list.textContent="无法读取同步日志（同步服务未运行？）";
  }finally{
    if(isCurrent()) syncLogAbortController=null;
  }
}
function fmtSyncLogLine(e){
  const el=typeof e.elapsed==="number"? e.elapsed.toFixed(1)+"s":"—";
  if(e.ok) return `成功 · ${el} · 收益 ${e.earnings??"—"} / 项目 ${e.projects??"—"}`;
  const reason=syncLogReason(e), why=reason?` · ${reason}`:"";
  return `失败 · code ${e.code??"—"} · ${el}${why}`;
}
function fmtSyncLogShort(e){
  if(!e) return "查看";
  return (e.ok?"✓ 成功":"✗ 失败")+" · "+syncLogTime(e.ts);
}
function renderSyncSteps(e){
  /* Linux 启动日志风格：[ ✓ ] 步骤名 …… 结果 + 耗时；只给最慢步骤一条极轻的强调。 */
  const timed=e.steps.filter(s=>typeof s.elapsed==="number"&&Number.isFinite(s.elapsed));
  const slowest=timed.length>1?timed.reduce((a,b)=>b.elapsed>a.elapsed?b:a):null;
  const rows=e.steps.map(s=>{
    const isSlowest=slowest&&s===slowest;
    const time=typeof s.elapsed==="number"&&Number.isFinite(s.elapsed)
      ? `${isSlowest?"最慢 · ":""}${s.elapsed.toFixed(1)}s` : "";
    return `<div class="synclog-step${isSlowest?" slowest":""}">`+
      `<span class="st-mark ${s.ok?"":"bad"}">${s.ok?"✓":"✗"}</span>`+
      `<span class="st-name">${esc(s.name)}</span>`+
      `<span class="st-val">${esc(s.detail||"")}</span>`+
      `<span class="st-time${isSlowest?" slow":""}">${esc(time)}</span></div>`;
  }).join("");
  const trig=e.trigger==="auto"?"自动":"手动";
  const total=typeof e.elapsed==="number"&&Number.isFinite(e.elapsed)?" · 总耗时 "+e.elapsed.toFixed(1)+"s":"";
  const slowMeta=slowest?" · 最慢 "+esc(slowest.name)+" "+slowest.elapsed.toFixed(1)+"s":"";
  return `<div class="synclog-steps"><div class="st-meta">${esc(trig)}触发${e.code?(" · exit "+e.code):""}${total}${slowMeta}</div>${rows}</div>`;
}
function toggleSyncLog(){
  const w=$("syncLogWrap");
  if(w) w.hidden=!w.hidden;
}
function syncLogTime(ts){
  const d=new Date(ts);
  if(isNaN(d)) return ts||"";
  const now=new Date();
  const sameDay=d.getFullYear()===now.getFullYear()&&d.getMonth()===now.getMonth()&&d.getDate()===now.getDate();
  const hm=String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0");
  if(sameDay) return "今天 "+hm;
  return `${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")} ${hm}`;
}
function renderNotes(){
  const t=todayStr();$("noteDate").textContent=t;
  $("note").value=state.notes[t]||"";
}
/* 备注历史：state.notes 按「YYYY-MM-DD → 文本」存本地，一直只能写不能回看。
   复盘页折叠卡按日期倒序列出全部非空备注；当天备注实时跟随输入框。 */
function renderNoteHistory(){
  const list=$("noteHistList"); if(!list) return;
  const days=Object.keys(state.notes||{}).filter(k=>String(state.notes[k]||"").trim()).sort().reverse();
  const tag=$("noteHistTag");
  if(tag) tag.textContent=days.length+" 天";
  if(!days.length){ list.innerHTML=`<div class="empty">还没有备注。总览页写下当天第一条，就会出现在这里。</div>`; return; }
  list.innerHTML=days.map(d=>{
    const txt=String(state.notes[d]).trim();
    const isToday=d===todayStr();
    return `<div class="nitem"><div class="nt">${esc(txt)}</div>`+
      `<div class="nm2"><span>${esc(d)}${isToday?' · 今天':''}</span>`+
      (isToday?`<span class="chip ok">实时</span>`:`<span class="chip">${esc(agoTxt(Math.round((new Date(todayStr())-new Date(d))/86400000)))}</span>`)+
      `</div></div>`;
  }).join("");
}
function esc(s){ s=(s==null)?"":String(s); return s.replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

