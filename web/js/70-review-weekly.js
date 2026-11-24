/* ---------- 自动周报：POST /api/weekly 生成 → 复盘页展示 ----------
   服务端跑 scripts/sync/weekly-report.py，从本地快照汇总成 Markdown 落盘 reports/。
   进复盘页先 GET 最近一份（不重新算），点按钮才 POST 重新生成。 */
let weeklyLoading=false;
async function genWeekly(){
  if(weeklyLoading) return;
  if(guest){ notify("访客模式下不生成周报（含真实收益数据）","warn"); return; }
  if(!SERVED||!TOKEN){ notify("本地同步服务未连接，无法生成周报","warn"); return; }
  weeklyLoading=true;
  const btn=$("weeklyBtn"); btn.disabled=true; btn.textContent="生成中…";
  try{
    const r=await fetch("/api/weekly",{method:"POST"});
    const j=await r.json().catch(()=>({}));
    if(r.ok&&j.ok){
      $("weeklyTag").textContent=j.path;
      $("weeklyPre").hidden=false;
      $("weeklyPre").textContent=j.content;
      notify("✓ 周报已生成："+j.path,"ok");
    }else{
      notify(j.error||("生成失败（HTTP "+r.status+"）"),"warn");
    }
  }catch(e){ notify("连不上本地服务："+e.message,"warn"); }
  weeklyLoading=false; btn.disabled=false; btn.textContent="生成本周周报";
}
async function renderWeekly(){
  if(!SERVED||!TOKEN) return;
  try{
    const r=await fetch("/api/weekly");
    if(!r.ok) return;
    const j=await r.json().catch(()=>({}));
    if(j.ok&&j.content){
      $("weeklyTag").textContent=j.path;
      const brief=$("weeklyTag").closest('details')?.querySelector('.review-fold-brief');
      if(brief) brief.textContent="本周汇总已生成";
      $("weeklyPre").hidden=false;
      $("weeklyPre").textContent=j.content;
    }
  }catch(e){}
}

/* ---------- 发布预测：记录 → 回填 → 校准 ----------
   预期档位三档：low（低于平均）/ mid（接近平均）/ high（高于平均，预期爆款）。
   回填后用真实阅读数与该平台已有内容的中位数对比，偏差方向（高于/低于预期）
   连续 3 次一致时提示「预期标准偏乐观/悲观，考虑调整」。 */
const PRED_LEVELS={low:"低于平均",mid:"接近平均",high:"预期爆款"};
function renderPredictions(){
  const tag=$("predTag"),list=$("predList");
  if(!tag||!list) return;
  const preds=(state.predictions||[]);
  tag.textContent=preds.length+" 条";
  const brief=tag.closest('details')?.querySelector('.review-fold-brief');
  if(brief) brief.textContent=preds.length?`${preds.length} 条 · 点开对比实际`:"尚无预测";
  if(!preds.length){ list.innerHTML=`<div class="empty">还没有预测记录。发布前把选题和预期档位记下来，复盘时才有「预测 vs 实际」可比。</div>`; return; }
  // 按平台算中位数基准（有真实阅读数据的条目）
  const posts=(social&&social.posts)||[];
  const medianByPlatform={};
  ["X","公众号","小红书","知识星球"].forEach(pf=>{
    const reads=posts.filter(p=>p.platform===pf&&typeof(p.metrics||{}).reads==="number").map(p=>p.metrics.reads).sort((a,b)=>a-b);
    if(reads.length>=3) medianByPlatform[pf]=reads[Math.floor(reads.length/2)];
  });
  list.innerHTML=preds.slice().reverse().map(pr=>{
    const actual=pr.actualReads;
    const base=medianByPlatform[pr.platform];
    let verdict="";
    if(actual!=null&&base!=null){
      const dir=pr.level==="high"?(actual>=base?"命中":"低于预期"):pr.level==="low"?(actual<=base?"命中":"高于预期"):(Math.abs(actual-base)<=base*0.5?"命中":actual>base*1.5?"高于预期":"低于预期");
      verdict=`<span class="chip ${dir==="命中"?"ok":"warn"}">${dir} · 实际 ${actual} / 平台中位 ${base}</span>`;
    }else if(actual!=null){
      verdict=`<span class="chip">实际 ${actual}（平台基准不足 3 条，暂不判偏差）</span>`;
    }else{
      verdict=`<span class="chip">待回填</span>`;
    }
    return `<div class="nitem"><div class="nt">${esc(pr.title)}</div>`+
      `<div class="nm2"><span>${esc(pr.platform)}</span><span>预期 ${PRED_LEVELS[pr.level]||pr.level}</span>${verdict}`+
      `<button class="btn" style="padding:2px 8px;font-size:var(--fs-xs)" onclick="predFill('${esc(pr.id)}')">回填</button>`+
      `<button class="btn" style="padding:2px 8px;font-size:var(--fs-xs)" onclick="predDel('${esc(pr.id)}')">删除</button></div></div>`;
  }).join("");
  // 校准提醒：已回填的最近 3 条偏差方向一致才提示
  const filled=preds.filter(p=>p.actualReads!=null&&medianByPlatform[p.platform]!=null).slice(-3);
  if(filled.length===3){
    const dirs=filled.map(p=>{
      const base=medianByPlatform[p.platform];
      return p.level==="high"?(p.actualReads>=base?1:-1):p.level==="low"?(p.actualReads<=base?1:-1):(p.actualReads>base*1.5?1:p.actualReads<base*0.5?-1:0);
    });
    if(dirs.every(d=>d===-1)) notify("最近 3 条预测全部低于实际——你的预期可能偏保守，可以上调档位标准。","info");
    else if(dirs.every(d=>d===1)) notify("最近 3 条预测全部高于实际——你的预期可能偏乐观，考虑下调档位标准。","info");
  }
}
function predOpenForm(){
  const title=prompt("选题/标题："); if(!title) return;
  const platform=prompt("平台（X / 公众号 / 小红书 / 知识星球）：","X"); if(!platform) return;
  const level=prompt("预期档位：low（低于平均）/ mid（接近平均）/ high（预期爆款）","mid"); if(!level) return;
  if(!PRED_LEVELS[level]){ notify("档位必须是 low / mid / high","warn"); return; }
  state.predictions=state.predictions||[];
  state.predictions.push({id:uidGen(),title:title.trim(),platform:platform.trim(),level,createdAt:todayStr(),actualReads:null});
  saveState(); renderPredictions();
}
function predFill(id){
  const pr=(state.predictions||[]).find(p=>p.id===id); if(!pr) return;
  const v=prompt(`「${pr.title}」的实际阅读数：`); if(v===null) return;
  const n=parseInt(v,10); if(isNaN(n)){ notify("请输入数字","warn"); return; }
  pr.actualReads=n; pr.filledAt=todayStr();
  saveState(); renderPredictions();
}
function predDel(id){
  state.predictions=(state.predictions||[]).filter(p=>p.id!==id);
  saveState(); renderPredictions();
}

/* AI 用量卡：本机 Codex 会话日志统计 + Jev 模型连通性。数据来自
   web-presence.json 的 aiUsage 段（scripts/tools/ai_usage.py，每 5 分钟轮询重扫，
   总量准实时）。只写真实值：面板基线未实时接入前不展示，避免和本地扫描混成两个数。 */
function renderAiUsage(){
  const u=webPresence&&webPresence.aiUsage;
  const tag=$("aiUsageTag"), body=$("aiUsageBody");
  if(!tag||!body) return;
  if(!u){tag.textContent="未接入";body.innerHTML=secEmpty("还没有 AI 用量快照");return;}
  const c=u.codex, j=u.jev;
  const fmtB=n=>n==null?"—":(n/1e9).toFixed(2)+" 亿";
  const fmtI=n=>n==null?"—":Number(n).toLocaleString("en-US");
  const rows=[];
  rows.push(`<div class="exprow"><div class="top"><span class="nm">Codex 总用量（本机日志）</span><span class="amt">${esc(c&&c.ok?fmtB(c.totalTokens):"—")}</span></div>`+
    (c&&c.ok?`<div class="expbar"><i style="width:100%;background:var(--accent)"></i></div>`+
      `<div class="expnote">${esc(fmtI(c.totalTokens))} tokens · ${esc(String(c.sessions))} 个会话 · ${esc(c.firstDate)} ~ ${esc(c.lastDate)} · 扫描于 ${esc(String(c.checkedAt||"").slice(11,16))}</div>`
      :`<div class="expnote">${esc((c&&c.error)||"Codex 日志统计暂不可用")}</div>`)+`</div>`);
  if(c&&c.ok&&c.totals){
    const cache=c.totals.cached_input_tokens||0, inp=c.totals.input_tokens||0;
    if(inp) rows.push(`<div class="exprow"><div class="top"><span class="nm">缓存命中率</span><span class="amt">${esc(Math.round(cache/inp*100))}%</span></div>`+
      `<div class="expbar"><i style="width:${Math.round(cache/inp*100)}%;background:var(--ink)"></i></div>`+
      `<div class="expnote">命中 ${esc(fmtI(cache))} / 输入 ${esc(fmtI(inp))} · 输出 ${esc(fmtI(c.totals.output_tokens))}（含推理 ${esc(fmtI(c.totals.reasoning_output_tokens))}）</div></div>`);
  }
  rows.push(`<div class="exprow"><div class="top"><span class="nm">Jev 模型（TypeSafe）</span>`+
    (j&&j.ok?`<span class="chip ok">已连通</span>`:j&&j.configured?`<span class="chip warn">异常</span>`:`<span class="chip">未配置</span>`)+`</div>`+
    `<div class="expnote">${j&&j.ok?esc(j.model)+" · 探测 "+esc(fmtI(j.inputTokens))+" 入 / "+esc(fmtI(j.outputTokens))+" 出 · "+esc(String(j.checkedAt||"").slice(11,16)):(j&&(j.error||j.note))||"未找到 TYPESAFE_API_KEY"}</div></div>`);
  body.innerHTML=rows.join("")+`<div class="expnote">${esc(u.dataPolicy||"")}</div>`;
  tag.textContent=c&&c.ok?fmtB(c.totalTokens):"—";
}

function renderMorningBrief(){
  const card=$("morningBriefCard"), grid=$("morningBriefGrid"), tag=$("morningBriefTag");
  if(!card||!grid) return;
  if(guest){
    if(tag) tag.textContent="访客脱敏";
    grid.innerHTML=`<div class="morning-col"><div class="morning-col-head"><span class="morning-col-title">晨间速览</span></div><div class="morning-col-list">访客模式下不读取个人微信待办、选题雷达与星球简报。</div></div>`;
    return;
  }
  // 1. 微信待办（不伪造 0）
  let wxCount=null, wxRows=[];
  if(wechat&&wechat.available){
    const muted=typeof wxMuted==="function"?wxMuted():[];
    const list=[];
    (wechat.accounts||[]).forEach(a=>{
      (a.waiting||[]).forEach(x=>{
        if(x.category==="广告"||x.category==="已结束") return;
        if(muted.includes(x.conversationId)) return;
        list.push(x);
      });
    });
    wxCount=list.length;
    wxRows=list.slice(0,2).map(x=>`<div class="morning-col-row"><b>${esc(x.displayName||x.name||"联系人")}</b>：${esc(x.summary||x.need||x.preview||"待回复")}</div>`);
  }
  const wxHtml=wxCount===null
    ? `<div class="morning-col-list">${wechat?"微信快照不可用":"正在读取微信待办…"}</div>`
    : (wxRows.length?`<div class="morning-col-list">${wxRows.join("")}</div>`:`<div class="morning-col-list">当前没有待回复消息</div>`);

  // 2. 选题雷达 Top 3（可写条目）
  let rdCount=null, rdRows=[];
  if(typeof radar!=="undefined"&&radar&&Array.isArray(radar.items)){
    const writable=radar.items.filter(it=>it.analyzed&&!it.needsHuman&&Number(it.writeScore||it.writeValue||0)>=5);
    rdCount=writable.length;
    rdRows=writable.slice(0,2).map(it=>`<div class="morning-col-row"><b>${esc(it.source||"雷达")}</b> · ${esc(it.title||"")}</div>`);
  }
  const rdHtml=rdCount===null
    ? `<div class="morning-col-list">正在读取选题雷达…</div>`
    : (rdRows.length?`<div class="morning-col-list">${rdRows.join("")}</div>`:`<div class="morning-col-list">本轮暂无高分可写选题</div>`);

  // 3. 知识星球简报（24h + 7 天周报）
  let zdCount=null, zdRows=[], zdSub="";
  if(typeof zsxqDaily!=="undefined"&&zsxqDaily){
    const dItems=Array.isArray(zsxqDaily.items)?zsxqDaily.items:[];
    const wk=zsxqDaily.weekly||null;
    zdCount=dItems.length;
    zdSub=wk?` · 7 天 ${wk.total??0} 条${wk.digestedCount?`（精华 ${wk.digestedCount}）`:""}`:"";
    const pick=(dItems.length?dItems:(wk&&wk.highlights)||[]).slice(0,2);
    zdRows=pick.map(it=>`<div class="morning-col-row">${it.digested?"★ ":""}<b>${esc(it.group||"星球")}</b> · ${esc(it.title||"")}</div>`);
  }
  const zdHtml=zdCount===null
    ? `<div class="morning-col-list">正在读取星球简报…</div>`
    : (zdRows.length?`<div class="morning-col-list">${zdRows.join("")}</div>`:`<div class="morning-col-list">过去 24h 可读星球暂无新帖</div>`);

  if(tag){
    const parts=[];
    if(wxCount!==null) parts.push(`微信 ${wxCount} 待回`);
    if(rdCount!==null) parts.push(`雷达 ${rdCount} 可写`);
    if(zdCount!==null) parts.push(`星球 24h ${zdCount} 条${zdSub}`);
    tag.textContent=parts.length?parts.join(" · "):"后台加载中";
  }
  grid.innerHTML=
    `<div class="morning-col"><div class="morning-col-head"><span class="morning-col-title">微信待办${wxCount!==null?` (${wxCount})`:""}</span>`+
      `<button class="morning-col-link" type="button" data-wb-action="set-type" data-wb-arg="wechat">去处理 →</button></div>${wxHtml}</div>`+
    `<div class="morning-col"><div class="morning-col-head"><span class="morning-col-title">选题雷达${rdCount!==null?` (${rdCount})`:""}</span>`+
      `<button class="morning-col-link" type="button" data-wb-action="set-type" data-wb-arg="news">看选题 →</button></div>${rdHtml}</div>`+
    `<div class="morning-col"><div class="morning-col-head"><span class="morning-col-title">星球简报${zdCount!==null?` (${zdCount})`:""}</span>`+
      `<button class="morning-col-link" type="button" data-wb-action="set-type" data-wb-arg="zsxq">看日报/周报 →</button></div>${zdHtml}</div>`;
}

function renderAll(){
  $("who").textContent=state.owner||"我的工作台";
  // 拖动过程中走的是 renderGoal/renderKPIs 那条轻路径，不经过 renderAll，
  // 所以在这里清锁是安全的，同时能兜住"锁没被释放"的任何异常路径。
  slideSpecLock=null;
  renderUnitSeg();
  renderDailyDeck();renderMorningBrief();renderTodayTimeline();renderKPIs();renderGoal();renderGoalHist();renderDonut();renderStreak();renderChart();
  renderProjects();fillLedTypeOptions();renderLedger();renderNotionPanel();renderSyncAlert();renderNotes();
  renderNoteHistory();
  renderExpenseBreak();
  renderScopeBar();
  renderSourceRank();
  renderHealth();
  if(chartMode==="m12") renderChartM();   // 切到月度视图时数据变化也要跟着刷新
  // 类型区不依赖 state，但 follow 一次：切屏不需要重算，而"数据同步后类型区跟着刷新"
  // 是符合直觉的。两处都只读 sections，不写。
  renderTypeBar();
  renderSections();
  renderWechatBadge();
  renderAiUsage();
  renderMetricToggle();
}

