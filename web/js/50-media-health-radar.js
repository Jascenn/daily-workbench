/* ---------- 运营健康分（0-100）+ 诊断规则 ----------
   数据全部来自已接通的通道，缺哪块就按 0 分计入并在明细里说明，不编数。
   五个维度各 20 分：发布节奏 / 账号新鲜度 / 内容表现 / 站点可用 / 数据链路。 */
function opHealth(){
  const out={score:0,dims:[],issues:[]};
  const socialPosts=(social&&social.posts)||[];
  /* publishedAt 有两种格式：ISO（多数平台）和 X 的 "Fri Oct 23 12:33:46 +0000 2026"。
     slice(0,10) 只对 ISO 有效，X 的帖子会被当成不可比较的字符串漏掉。 */
  const dayOf=p=>{const s=String(p.publishedAt||"");const d=new Date(s);return isNaN(d)?s.slice(0,10):d.toISOString().slice(0,10);};
  // 1) 发布节奏：近 7 天有发布的平台占比（todayPlan published + posts 近 7 天）
  const weekAgo=new Date(Date.now()-7*86400000).toISOString().slice(0,10);
  const plan=(social&&social.todayPlan)||[];
  const pubInPlan=plan.filter(p=>mediaStatus(p.id,p.defaultStatus)==="published").length;
  const recent=plan.filter(p=>(socialPosts||[]).some(x=>x.platform===p.platform&&dayOf(x)>=weekAgo)).length;
  const total=plan.length||4;
  const rhythm=Math.min(1,(pubInPlan+recent)/total);
  out.dims.push({name:"发布节奏",score:Math.round(rhythm*20),max:20,
    note:`近 7 天 ${recent}/${total} 个平台有内容${pubInPlan?`，今日已确认 ${pubInPlan} 个`:""}`});
  // 2) 账号新鲜度：按数据日期现算，不读取采集端可能过时的 freshness 标签
  let fresh=20, freshCount=0;
  (social&&social.accounts||[]).forEach(a=>{
    const state=mediaEffectiveFreshness(a).state;
    const restricted=["blocked","limited"].includes(a.connectionState);
    if(state==="fresh"&&!restricted) freshCount++;
    if(a.connectionState==="blocked") fresh-=10;
    else if(a.connectionState==="limited") fresh-=5;
    else if(state==="missing") fresh-=10;
    else if(state==="stale") fresh-=5;
    else if(state==="aging") fresh-=2.5;
  });
  fresh=Math.max(0,Math.round(fresh));
  out.dims.push({name:"账号数据",score:fresh,max:20,
    note:`${freshCount}/${(social&&social.accounts||[]).length} 个平台数据新鲜`});
  // 3) 内容表现：近 7 天有可核验阅读数据的内容条数（0 条给保底说明）
  const recentPosts=socialPosts.filter(p=>dayOf(p)>=weekAgo);
  const withData=recentPosts.filter(p=>typeof(p.metrics||{}).reads==="number").length;
  const perf=recentPosts.length?Math.min(1,withData/recentPosts.length)*20:10;
  out.dims.push({name:"内容表现",score:Math.round(perf),max:20,
    note:recentPosts.length?`近 7 天 ${recentPosts.length} 条，${withData} 条有真实阅读数据`:"近 7 天暂无新内容"});
  // 4) 站点可用
  const sites=(webPresence&&webPresence.sites)||[];
  const okSites=sites.filter(s=>s.state==="ok").length;
  const siteScore=sites.length?okSites/sites.length*20:20;
  out.dims.push({name:"站点可用",score:Math.round(siteScore),max:20,
    note:sites.length?`${okSites}/${sites.length} 个站点正常`:"未配置站点监控"});
  // 5) 数据链路：最后一次同步距今
  let syncScore=20,syncNote="未同步";
  try{
    const last=String((social&&social.generatedAt)||"");
    if(last){
      const hrs=(Date.now()-new Date(last).getTime())/3600000;
      syncScore=hrs<=25?20:hrs<=49?12:hrs<=97?6:0;
      syncNote=hrs<1?"1 小时内同步过":hrs<25?"今天同步过":`已 ${Math.round(hrs)} 小时未同步`;
    }
  }catch(e){}
  out.dims.push({name:"数据链路",score:Math.round(syncScore),max:20,note:syncNote});
  out.score=out.dims.reduce((a,d)=>a+d.score,0);
  // 诊断规则：只输出需要行动的（低分维度自带说明，不再另加重复文案）
  out.dims.forEach(d=>{ if(d.score<=d.max*0.5) out.issues.push({level:"warn",text:`${d.name}：${d.note}`}); });
  return out;
}
function renderOpHealth(){
  const h=opHealth();
  const grade=h.score>=80?["运转良好","ok"]:h.score>=50?["基本健康","warn"]:["需要处理","dead"];
  const rows=h.dims.map(d=>
    `<div class="ophealth-row${d.score<=d.max*0.5?" oh-low":""}"><span class="oh-name">${esc(d.name)}</span>`+
    `<span class="rr-bar"><i style="width:${Math.round(d.score/d.max*100)}%"></i></span>`+
    `<span class="oh-note">${esc(d.note)}</span></div>`).join("");
  const issues=h.issues.length?h.issues.map(i=>`<div class="oh-issue">${esc(i.text)}</div>`).join("")
    :`<div class="oh-issue oh-ok">没有需要处理的运营异常</div>`;
  return `<div class="rhythm-score"><b>${h.score}</b><span class="chip ${grade[1]}">${grade[0]}</span>`+
    `<span class="fnote">发布节奏 · 账号数据 · 内容表现 · 站点可用 · 数据链路 各 20 分</span></div>`+rows+issues;
}
/* 周更节奏健康分：口径只有一份 —— days/threshold 都来自 sections.json，
   页面只做聚合展示，不重新算第二遍。空栏目不计分，stale 按超期比例扣。 */
function renderRhythm(){
  const secs=((sections&&sections.sections)||[]).filter(s=>s&&s.freshness&&!s.freshness.empty);
  if(!secs.length) return secEmpty("还没有栏目节奏数据（sections.json 未加载）");
  let sum=0;
  secs.forEach(s=>{const f=s.freshness; sum+=Math.max(0,Math.min(1,(f.threshold-f.days)/f.threshold));});
  const row=s=>{
    const f=s.freshness, ratio=Math.max(0,Math.min(1,(f.threshold-f.days)/f.threshold));
    return `<div class="rhythm-row${f.stale?" rr-stale":""}"><span class="rr-name">${esc(s.name)}</span>`+
      `<span class="rr-bar"><i style="width:${Math.max(0,Math.round(ratio*100))}%"></i></span>`+
      `<span class="rr-val">${f.days} 天前 · 阈值 ${f.threshold}</span></div>`;
  };
  const score=Math.round(sum/secs.length*100);
  const grade=score>=80?["运转良好","ok"]:score>=50?["基本健康","warn"]:["明显滞后","dead"];
  return `<div class="rhythm-score"><b>${score}</b><span class="chip ${grade[1]}">${grade[0]}</span>`+
    `<span class="fnote">${secs.length} 个栏目 · 更新越接近阈值上限分越低</span></div>`+
    secs.map(row).join("");
}
function focusMediaVerification(){
  const card=$("mediaTodayCard");
  if(!card||!card.scrollIntoView) return;
  const reduce=window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  card.scrollIntoView({behavior:reduce?"auto":"smooth",block:"start"});
  const candidates=[...card.querySelectorAll(
    ".media-secondary-group[open] .media-platform .media-primary, "+
    "#mediaMatrix>.media-platform .media-primary, .media-platform .media-primary"
  )].filter(el=>el.getClientRects().length&&!el.disabled);
  const first=candidates[0]||card.querySelector(".completed-group>summary")||card;
  requestAnimationFrame(()=>{
    try{first.focus({preventScroll:true});}catch(e){try{first.focus();}catch(err){}}
  });
}
function renderContent(){
  const focusAction=$("mediaFocusAction");
  if(!social){
    $("socialFresh").innerHTML=`<div class="media-context"><span class="media-context-label">数据不可用</span><span class="media-context-copy">${esc(socialErr||"没有自媒体快照")}</span></div>`;
    $("mediaPulse").innerHTML=renderMediaPulse(null);
    $("mediaFocusTitle").textContent="自媒体数据暂不可用";
    $("mediaFocusNote").textContent="先恢复只读数据快照，再进行今日发布核验。";
    if(focusAction){focusAction.hidden=true;focusAction.disabled=true;}
    $("contentStats").innerHTML=""; $("mediaAccounts").innerHTML=secEmpty("没有账号数据"); $("mediaTrend").innerHTML=secEmpty("没有可用的发布趋势数据"); $("mediaTrendBrief").textContent="数据不可用"; $("mediaWeb").innerHTML=renderWebPresence(webPresence); $("mediaMatrix").innerHTML=secEmpty(socialErr);
    $("mediaPosts").innerHTML=secEmpty("没有逐条数据"); $("mediaWeeklyRecap").innerHTML=""; $("mediaKnowledge").innerHTML=secEmpty("没有知识库快照");
    $("mediaEnhancements").innerHTML="";
    renderPredictions();
    $("mediaAgent").innerHTML=""; $("mediaAutomation").innerHTML="";
    renderTodayTimeline();
    return;
  }
  const m=mediaMetrics(social), generated=String(social.generatedAt||"").replace("T"," ").slice(0,16);
  const syncRun=social.syncRun||{};
  const syncCounts=[
    `本轮更新 ${Number(syncRun.updatedAccounts||0)}`,
    Number(syncRun.partialAccounts||0)?`部分 ${Number(syncRun.partialAccounts)}`:null,
    `沿用历史 ${Number(syncRun.historyAccounts||0)}`,
    Number(syncRun.blockedAccounts||0)?`被拦截 ${Number(syncRun.blockedAccounts)}`:null,
    `未接入 ${Number(syncRun.notConnectedAccounts||0)}`
  ].filter(Boolean).join(" · ");
  $("mediaPulse").innerHTML=renderMediaPulse(social);
  if(focusAction){focusAction.hidden=false;focusAction.disabled=false;}
  const syncLabels={updated:"本轮已更新",partial:"本轮部分更新",history:"沿用历史",blocked:"被拦截",not_connected:"未接入"};
  const src=(social.sources||[]).map(x=>{
    const records=x.records==null?"未知":String(x.records);
    return `${x.name} ${x.asOf||"无日期"} · ${records} 条 · ${syncLabels[x.syncStatus]||"未标注"}`;
  }).join("；");
  // 来源明细（四平台各自日期/条数）只对排查有用，日常一眼扫不需要；折叠进 details，
  // 卡面只留生成时间与边界承诺。textContent 仍包含完整说明，测试断言不受影响。
  $("socialFresh").innerHTML=`<div class="media-context"><span class="media-context-label">数据快照</span>`+
    `<span class="media-context-copy">生成 ${esc(generated)} · ${esc(syncCounts)} · `+
    `<details class="media-account-more media-context-more"><summary>来源明细</summary>`+
    `<div>${esc(src)} · ${esc(social.dataPolicy||"")}${socialNote?" · "+esc(socialNote):""}</div></details></span></div>`;
  /* 顶部焦点只服务日更主平台。X / 小红书异常留在 Agent 简报和下方账号卡，
     避免次要平台抢走“今天先做什么”的主结论。 */
  const priority=mediaFocusPriority(social.accounts);
  const priorityFresh=mediaEffectiveFreshness(priority);
  $("mediaFocusTitle").textContent=priority&&priority.platform==="公众号"
    ?(priority.connectionState==="limited"?(priority.verified===false?"公众号素材已接入，统计接口需完成认证":"公众号素材已接入，统计权限待补齐")
      :priority.connectionState==="blocked"?"先恢复公众号数据，再看内容表现"
      :`公众号数据已过期 ${priorityFresh.days} 天`)
    :priority&&priority.platform==="知识星球"?`知识星球数据已过期 ${priorityFresh.days} 天`
    :m.unknown?`今天先确认 ${m.unknown} 个平台是否发布`
    :m.published?`今天已确认发布 ${m.published} 个平台`:"今天的发布状态已核验";
  $("mediaFocusNote").textContent=priority&&priority.platform==="公众号"
    ?(priority.connectionState==="limited"?(priority.permissionReason||"当前公众号没有官方统计接口权限。")+" 现阶段只展示已读取到的素材；缺失的阅读、分享和关注值不会伪装成 0。"
      :priority.connectionState==="blocked"?"公众号凭据已存在，但 IP 白名单拦住了官方统计。处理这一项后，阅读、分享和关注数据才能进入工作台。"
      :`当前数据只到 ${priority.asOf||"未知日期"}。连通不等于新鲜，先刷新数据，再判断内容表现。`)
    :priority&&priority.platform==="知识星球"?`当前星球数据只到 ${priority.asOf||"历史日期"}，已过期 ${priorityFresh.days} 天。今天仍可做发布核验；先接入实时只读或重新导出，才能判断内容表现。`
    :m.unknown?"先完成发布选择，再进入撰稿。这样 Agent 的提醒只围绕今天真正要做的内容。"
    :"状态已收敛，可以把注意力移到表现最好的内容信号。";
  $("contentStats").innerHTML=`<div class="media-score-main"><strong>${esc(m.unknown)}</strong><span>待核验</span></div>`+
    `<div class="media-score-fact"><span>逐条样本</span><b>${esc(m.posts)}</b></div>`+
    `<div class="media-score-fact"><span>草稿连接</span><b>${esc(m.draftAdapters)}</b></div>`;
  $("mediaAccounts").innerHTML=renderMediaAccounts(social.accounts||[]);
  renderZsxqPanel(social);
  $("mediaTrend").innerHTML=renderMediaTrend(social);
  const accounts=social.accounts||[];
  const accountIssue=accounts.filter(a=>mediaEffectiveFreshness(a).state!=="fresh").map(a=>a.platform);
  if($("mediaOverviewBrief")) $("mediaOverviewBrief").textContent=accountIssue.length
    ? `${accountIssue.length} 个平台待补数据 · ${accountIssue.join("、")}`
    : `${accounts.length} 个平台 · 数据均新鲜`;
  $("mediaPerf").innerHTML=renderMediaPerf(social);
  $("mediaWeb").innerHTML=renderWebPresence(webPresence);
  const webSites=(webPresence&&webPresence.sites)||[];
  const webSiteOk=webSites.filter(x=>x.state==="ok").length;
  const webSummary=webPresenceStatus(webPresence);
  if($("mediaWebBrief")) $("mediaWebBrief").textContent=!webPresence ? "监控数据未加载"
    : !webSites.length ? "站点待检测 · 博客已监控"
    : webSummary.issues ? `${webSummary.label} · 博客已监控`
    : webSiteOk===webSites.length ? "站点正常 · 博客已监控"
    : `${webSiteOk}/${webSites.length} 个站点正常 · 博客已监控`;
  $("mediaWebTag").textContent=webPresence?`每 5 分钟检测 · ${esc(String(webPresence.generatedAt||"").slice(11,16))}`:"每 5 分钟检测";
  if($("mediaAccountsTag")) $("mediaAccountsTag").textContent=social&&social.guest?"访客示例 · 未接入不写 0":"真实快照 · 未接入不写 0";
  if($("mediaPostsTag")) $("mediaPostsTag").textContent=social&&social.guest?"访客示例 · 成熟样本优先":
    ((social.analysis||[]).length?`${social.analysis.length} 条信号 · 成熟样本优先`:"暂无可核验信号");

  const mediaPlanRow=p=>{
    const st=mediaStatus(p.id,p.defaultStatus),meta=MEDIA_STATUS[st]||MEDIA_STATUS.unknown;
    const next=st==="unknown"?"设为待发布":st==="planned"?"确认已发布":st==="published"?"设为今日不发":"重新核验";
    const same=safeUrl(p.url)&&safeUrl(p.url)===safeUrl(p.actionUrl);
    const xCopy=p.id==="x"?`<button class="media-ghost" onclick="copyAsTweet()">复制为推文</button>`:"";
    return `<section class="media-platform"><div class="mh"><span class="mn">${esc(p.platform)}</span><span class="chip ${meta[1]}">${meta[0]}</span></div>`+
      `<div class="md">${st==="published"?"已由你人工确认发布。":st==="planned"?"今天计划发布，尚未确认上线。":st==="skip"?"今天明确不发布。":"尚无平台回执，也没有人工确认。"}</div>`+
      `<div class="media-actions"><button class="media-primary" onclick="cycleMediaStatus('${esc(p.id)}')">${next}</button>`+
      (same?outLink(p.actionUrl,"打开后台"):outLink(p.url,"看主页")+outLink(p.actionUrl,"开始写"))+xCopy+`</div></section>`;
  };
  const mediaPlan=mediaSortedPlan(social.todayPlan||[]);
  const pendingPlan=mediaPlan.filter(p=>!["published","skip"].includes(mediaStatus(p.id,p.defaultStatus)));
  const pendingDaily=pendingPlan.filter(p=>MEDIA_DAILY_IDS.includes(p.id));
  const pendingSecondary=pendingPlan.filter(p=>!MEDIA_DAILY_IDS.includes(p.id));
  const dailySettled=mediaPlan.filter(p=>MEDIA_DAILY_IDS.includes(p.id)&&
    ["published","skip"].includes(mediaStatus(p.id,p.defaultStatus)));
  const secondaryPlan=mediaPlan.filter(p=>!MEDIA_DAILY_IDS.includes(p.id));
  if(focusAction){
    focusAction.disabled=!mediaPlan.length;
    focusAction.textContent=pendingPlan.length?`开始核验 ${pendingPlan.length} 个平台`:
      mediaPlan.length?"查看今日核验":"暂无发布计划";
  }
  if($("mediaTodayBrief")) $("mediaTodayBrief").textContent=
    pendingDaily.length&&pendingSecondary.length
      ? `${pendingDaily.map(p=>p.platform).join("、")}待核验 · 其他 ${pendingSecondary.length} 个平台未核验`
      : pendingDaily.length
        ? `${pendingDaily.map(p=>p.platform).join("、")}待核验`
        : pendingSecondary.length
          ? `日更主平台已处理 · 其他 ${pendingSecondary.length} 个平台待核验`
          : "今日平台已处理";
  const secondaryNames=secondaryPlan.map(p=>p.platform).join(" / ");
  const secondaryOpen=!pendingDaily.length&&pendingSecondary.length;
  $("mediaMatrix").innerHTML=(pendingDaily.length?pendingDaily.map(mediaPlanRow).join(""):
      secEmpty("公众号、知识星球今日已处理"))+
    (secondaryPlan.length?`<details class="completed-group media-secondary-group"${secondaryOpen?" open":""}>`+
      `<summary>其他平台 · ${esc(secondaryNames)} · ${pendingSecondary.length?`${pendingSecondary.length} 个待核验`:"已处理"}<span>按需展开</span></summary>`+
      `<div class="completed-list">${secondaryPlan.map(mediaPlanRow).join("")}</div></details>`:"")+
    (dailySettled.length?`<details class="completed-group"><summary>日更主平台已处理 ${dailySettled.length} 个<span>按需查看</span></summary>`+
      `<div class="completed-list">${dailySettled.map(mediaPlanRow).join("")}</div></details>`:"");

  const postById=Object.fromEntries((social.posts||[]).map(p=>[p.id,p]));
  /* Jev 分数按 URL 匹配进文章行；开关开着且已有分析才显示，否则原样。 */
  const jevByUrl={}; if(JEV.enabled) (JEV.lastAnalysis||[]).forEach(r=>{ if(r.url) jevByUrl[r.url]=r; });
  const postRows=(social.analysis||[]).map(a=>{
    const p=postById[a.postId]; if(!p) return "";
    const mm=p.metrics||{}, u=safeUrl(p.url), title=u?`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a>`:esc(p.title);
    const jr=u?jevByUrl[u]:null;
    const jchip=jr&&jr.score!=null?`<span class="chip jev-chip">Jev ${Math.max(0,Math.min(10,+jr.score)).toFixed(1)}</span>`:"";
    return `<div class="nitem media-post"><div class="nt">${title}</div><div class="nm2"><span class="chip ${esc(a.tone||"")}">${esc(a.label)}</span>`+
      jchip+`<span>${esc((p.publishedAt||"").slice(0,10))}</span><span>阅读 ${esc(mm.reads??"未核验")}</span><span>赞 ${esc(mm.likes??"未核验")}</span><span>评 ${esc(mm.comments??"未核验")}</span></div>`+
      `<div class="ns">${esc(a.reason)}</div></div>`;
  }).filter(Boolean);
  $("mediaPosts").innerHTML=postRows.length?(postRows.slice(0,3).join("")+
    (postRows.length>3?`<details class="media-more"><summary>查看其余 ${postRows.length-3} 条信号</summary>${postRows.slice(3).join("")}</details>`:"")):secEmpty("没有足够样本做逐条比较");
  // 内容信号卡头部换成周更节奏健康分；Top/Bottom 已上移到独立的「内容表现」卡。
  $("mediaWeeklyRecap").innerHTML=renderRhythm();

  const kb=social.knowledgeBase||null;
  $("mediaKnowledgeTag").textContent=kb&&kb.available?`${kb.matchedNotes||0} 条候选 · Obsidian 只读`:"Obsidian · 未连接";
  $("mediaKnowledge").innerHTML=renderKnowledgeBase(kb);

  /* 主体顺序沿用最早版本的预览：数据 → 网站 → 发布 → Agent → 内容信号 → 知识库。
     后加的运营体检与 Jev 增强放在这些主体之后，体检先于增强分析。 */
  const healthBlock=`<details class="card media-jev-card op-health-card"><summary class="media-jev-summary">`+
    `<span class="media-jev-title"><span class="media-section-index">08</span>运营体检</span>`+
    `<span class="media-jev-summ">${esc(String(opHealth().score))} 分 · ${opHealth().score>=80?"运转良好":opHealth().score>=50?"基本健康":"需要处理"}</span>`+
    `<span class="media-jev-caret">▾</span></summary>`+
    `<div class="media-jev-body">${renderOpHealth()}</div></details>`;
  $("mediaEnhancements").innerHTML=healthBlock+jevBlock();
  renderPredictions();

  $("mediaAgent").innerHTML=mediaReminders(social).map((x,i)=>`<div class="agent-item${i===0?" lead":""}"><div class="at">${esc(x.title)}</div><div class="ad">${esc(x.detail)}</div></div>`).join("");
  $("mediaAutomation").innerHTML=(social.automation||[]).map(a=>{
    const tone=a.state==="available"?"ok":a.state==="missing"?"dead":"warn";
    return `<section class="media-adapter"><div><div class="media-adapter-name">${esc(a.platform)}</div><span class="chip ${tone}">${esc(a.label)}</span></div>`+
      `<div class="media-adapter-copy">${esc(a.detail)}</div><div class="media-actions">${outLink(a.dashboardUrl,"打开后台")}</div></section>`;
  }).join("")||secEmpty("没有检测到草稿入口");
  renderTodayTimeline();
}

/* 新类型渲染分发（被 renderSections 早返回分支调用） */
function renderNewType(k){
  if(k==="project") renderProject();
  else if(k==="client") renderClient();
  else if(k==="wechat") renderWechat();
  else if(k==="time") renderTime();
  else if(k==="content") renderContent();
  else if(k==="zsxq") renderZsxqScreen();
}

function feedSortMode(){return localStorage.getItem("wb.feedSort")||"stale"}
function setFeedSort(v){
  localStorage.setItem("wb.feedSort",v);
  const s=secOf("news"); if(s) renderNews(s);
}
/* 健康表排序独立于上游：默认沿用「停更最久在前」，其余档在渲染层重排，不动数据本身 */
function sortFeedsFor(list){
  const mode=feedSortMode();
  const arr=list.slice();
  if(mode==="fresh"){
    arr.sort((a,b)=>((a.days===null)-(b.days===null))||((a.days??1e9)-(b.days??1e9)));
  }else if(mode==="count"){
    arr.sort((a,b)=>(b.count-a.count)||((a.days===null)-(b.days===null))||((b.days??-1)-(a.days??-1)));
  }else if(mode==="group"){
    arr.sort((a,b)=>String(a.group||"未分组").localeCompare(String(b.group||"未分组"),"zh")
      ||((a.days===null)-(b.days===null))||((b.days??-1)-(a.days??-1)));
  }
  return arr;
}
/* ── 选题雷达（radar.json）────────────────────────────────────────────────
   热点源抓来的标题不是选题。这里只放"已经判断过"的条目：Jev 给相关度/可写价值，
   本地模型给角度。判断没跑成的条目如实显示"需人工判断"，不假装分析过。 */
const RADAR_API="/radar.json";
let radar=null, radarErr="", radarShowAll=false;

async function loadRadar(){
  try{
    const r=await fetch(RADAR_API,{cache:"no-store"});
    if(!r.ok) throw new Error("HTTP "+r.status);
    radar=await r.json();
    radarErr="";
  }catch(e){
    radar=null;
    radarErr=String((e&&e.message)||e);
  }
}

function setRadarAll(v){
  radarShowAll=!!v;
  const a=$("radarS1"), b=$("radarS2");
  if(a&&b){ a.classList.toggle("on",!radarShowAll); b.classList.toggle("on",radarShowAll); }
  renderRadar();
}

/* 相关度权重高于可写价值：先确认"和我有关"，再谈"能不能写"。
   两个分都是 Jev 的 0–9，展示时统一折成百分比。 */
function radarItemScore(it){
  const rel=Number(it.relevance), ang=Number(it.angle);
  return (isFinite(rel)?rel*0.6:0)+(isFinite(ang)?ang*0.4:0);
}

function radarRows(){
  const items=(radar&&radar.items)||[];
  const rows=items.filter(it=>radarShowAll||it.whyRelated||((it.angles||[]).length>0));
  return rows.slice().sort((a,b)=>radarItemScore(b)-radarItemScore(a));
}

function radarSourceChips(sources){
  const label={ok:"可用",empty:"无条目",unsupported:"未接入",error:"抓取失败"};
  return (sources||[]).map(s=>{
    const cls=s.status==="ok"?"ok":(s.status==="error"?"bad":"warn");
    const count=s.itemCount?` ${s.itemCount} 条`:"";
    return `<span class="radar-src ${cls}" title="${esc(s.error||s.url||"")}">${esc(s.name)} · ${esc(label[s.status]||s.status||"未知")}${count}</span>`;
  }).join("");
}

function radarCard(it){
  const rel=wxScorePct(it.relevance), ang=wxScorePct(it.angle);
  const meta=[it.sourceName||"未知来源"];
  if(rel) meta.push("相关 "+rel);
  if(ang) meta.push("可写 "+ang);
  const angles=(it.angles||[]);
  const needs=it.needsHuman||(!it.whyRelated&&!angles.length);
  return `<div class="radar-row">`+
    `<div class="radar-title"><a href="${esc(safeUrl(it.url))}" target="_blank" rel="noopener">${esc(it.title||"无标题")}</a></div>`+
    `<div class="radar-meta">${esc(meta.join(" · "))}${needs?` · <span class="radar-needs">需人工判断</span>`:""}</div>`+
    (it.whyRelated?`<div class="radar-why"><b>为什么</b>${esc(it.whyRelated)}</div>`:"")+
    (angles.length?`<div class="radar-angle"><b>能写</b>${angles.map(a=>esc(a)).join(" / ")}</div>`:"")+
    (it.nextAction?`<div class="radar-next"><b>下一步</b>${esc(it.nextAction)}</div>`:"")+
    `</div>`;
}

function renderRadar(){
  const list=$("radarList"), note=$("radarNote"), tag=$("radarTag"), srcs=$("radarSources");
  if(!list||!note||!tag) return;
  if(!radar){
    tag.textContent="—";
    srcs.innerHTML="";
    note.innerHTML=radarErr
      ? `读不到选题雷达（${esc(radarErr)}）。先在终端跑一次：<code>python3 scripts/sync/radar-sync.py</code>`
      : `还没有选题雷达快照。在终端跑一次：<code>python3 scripts/sync/radar-sync.py</code>`;
    list.innerHTML="";
    return;
  }
  const cov=radar.coverage||{};
  const rows=radarRows();
  const total=(radar.items||[]).length;
  tag.textContent=`可写 ${cov.keptForWriting||rows.length} / ${total}`;
  srcs.innerHTML=radarSourceChips(radar.sources);
  note.textContent=rows.length
    ? `${cov.sourcesOk||0}/${cov.sourcesTotal||0} 源可用 · 抓取 ${cov.itemsFetched||total} 条 · Jev 判断 ${cov.itemsJudged||0} 条 · ${radar.generatedAt||""}`
    : (radarShowAll? "本轮没有抓到条目。" : "本轮没有达到可写线的条目；可以点「全部」看判断明细。");
  list.innerHTML=rows.slice(0,8).map(radarCard).join("");
}

function renderNews(s){
  renderRadar();
  $("newsStats").innerHTML=statCells(s.stats);
  const all=s.items||[];
  const recent=all.filter(i=>i.days!==null && i.days<=s.windowDays);
  // 三档取列表：最近 N 条 / 近窗口全部 / 全部时间。每一档都从 all 这个**同一份**数组派生，
  // 不各自 filter 一遍 —— 两处 filter 迟早会因为条件写歪而不一致。
  // 筛选（源/分组/未读）在档位过滤之后叠加：档位管"看多远"，筛选管"看哪类"。
  const sourceOf=(i)=>i.source||"";
  const q=newsQueryF;
  const filtered = all.filter(i=>
    (!newsFeedF||sourceOf(i)===newsFeedF)&&
    (!newsGroupF||(s.feeds||[]).some(f=>f.name===sourceOf(i)&&f.group===newsGroupF))&&
    (!newsUnreadF||!i.read)&&
    (!q||[i.title,i.summary,i.source,i.group].some(v=>String(v||"").toLowerCase().includes(q))));
  const fRecent=recent.filter(i=>filtered.includes(i));
  const list = newsScope==="recent" ? fRecent.slice(0,NEWS_PAGE)
             : newsScope==="window" ? fRecent
             : filtered;
  // 阅读层复用当前筛选后的列表，不再二次抓取或重算筛选。
  // 阅读中如果后台轮询触发整页重绘，保留当前这份上下文，避免左侧列表突然换批。
  const readerOpen=$("newsReaderOv")&&$("newsReaderOv").classList.contains("show");
  if(!readerOpen){
    newsReaderItems=list.map(i=>({
      title:String(i.title||"未命名文章"),
      url:safeUrl(i.link),
      source:String(i.source||"资讯"),
      published:String(i.published||""),
      days:i.days,
      read:!!i.read
    }));
  }
  // 各档的真实条数：不点进去也知道"全部"是多少条
  const nOf={recent:Math.min(NEWS_PAGE,fRecent.length), window:fRecent.length, all:filtered.length};
  const labOf={recent:"最近", window:"近 "+s.windowDays+" 天", all:"全部"};
  NEWS_SCOPES.forEach((k,i)=>{
    const b=$("newsS"+(i+1)); if(!b) return;
    b.classList.toggle("on",k===newsScope);
    b.textContent=labOf[k]+" "+nOf[k];
    b.setAttribute("aria-pressed",k===newsScope?"true":"false");
  });
  /* 卡片标题行只有 650px 宽（左列），而「条目」这一张要同时放：标题 + 状态标签 +
     三档按钮。第一版把「显示 20 / 217 条（总量含 2022 年起的归档）」整句塞进标签里，
     标签被挤成两行、按钮挤到边上 —— 真机截图才看出来。
     修法：标签只留"显示多少 / 共多少"，那句解释挪到列表下方的说明位。 */
  const fActive=newsFeedF||newsGroupF||newsUnreadF||newsQueryF;
  $("newsTag").textContent=`显示 ${list.length} / ${fActive?filtered.length:all.length}`;
  const filterBox=$("newsFilter");
  if(filterBox) filterBox.classList.toggle("active",!!fActive);
  // 筛选下拉：按源（含条目数）与分组填充；只在数据变化时重建，保留当前选择
  const fsel=$("newsFeedSel"), gsel=$("newsGroupSel");
  if(fsel&&gsel){
    const fCounts={}; all.forEach(i=>{fCounts[sourceOf(i)]=(fCounts[sourceOf(i)]||0)+1;});
    const fNames=Object.keys(fCounts).sort((a,b)=>fCounts[b]-fCounts[a]);
    const fOpts=`<option value="">全部源</option>`+fNames.map(n=>
      `<option value="${esc(n)}"${n===newsFeedF?" selected":""}>${esc(n)}（${fCounts[n]}）</option>`).join("");
    if(fsel.dataset.built!==fNames.join("|")){ fsel.innerHTML=fOpts; fsel.dataset.built=fNames.join("|"); }
    else { /* 重建会丢选择；只在源集合变化时重建，这里只同步选中态 */ if(fsel.value!==newsFeedF) fsel.value=newsFeedF; }
    const gs=[...new Set((s.feeds||[]).map(f=>f.group).filter(Boolean))].sort();
    const gOpts=`<option value="">全部分组</option>`+gs.map(g=>
      `<option value="${esc(g)}"${g===newsGroupF?" selected":""}>${esc(g)}</option>`).join("");
    if(gsel.dataset.built!==gs.join("|")){ gsel.innerHTML=gOpts; gsel.dataset.built=gs.join("|"); }
    else if(gsel.value!==newsGroupF) gsel.value=newsGroupF;
  }
  $("newsListNote").textContent=`共 ${all.length} 条，包含 2022 年起的归档；`+
    `上面三档分别是"最近 ${NEWS_PAGE} 条 / 近 ${s.windowDays} 天 / 全部时间"。`;
  const newsRow=(it,lead,index)=>{
    const u=safeUrl(it.link), t=esc(it.title);
    const src=`<button class="feed-src" type="button" onclick="filterNewsBySource('${esc(it.source)}')" aria-label="只看 ${esc(it.source)}">${esc(it.source)}</button>`;
    return `<div class="nitem${it.read?" read":""}${lead?" news-lead":""}${lead&&index===0?" featured":""}">`+
      `<div class="nt">${u?`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer" onclick="return openNewsReader(this,event)">${t}</a>`:t}</div>`+
      `<div class="nm2">`+
        `<span class="dot ${it.read?"old":"new"}" aria-hidden="true"></span>`+
        `<span>${it.read?"已读":"未读"}</span>`+
        src+
        `<span>${esc(agoTxt(it.days))}</span>`+
        `<span>${esc((it.published||"").slice(0,10))}</span>`+
      `</div>`+
      // 摘要**不放进 .nm2**：它是多行块，混在那一行 flex 里会参与那一行的固有宽度计算
      (it.summary?`<div class="ns">${esc(it.summary)}</div>`:"")+
      `</div>`;
  };
  const leadCount=newsScope==="recent"?Math.min(3,list.length):0;
  const showAllInline=newsScope!=="recent"||!!fActive;
  const focus=list.slice(0,leadCount);
  $("newsItems").innerHTML=list.length?(
    (focus.length?`<div class="news-focus-head"><b>今日焦点</b><span>${focus.length} 篇 · 按发布时间排序</span></div>`+
      `<div class="news-focus-grid">${focus.map((it,i)=>newsRow(it,true,i)).join("")}</div>`:"")+
    (list.length>leadCount?`<details class="news-more"${showAllInline?" open":""}>
      <summary>${fActive?"筛选结果":"其余近期内容"} ${list.length-leadCount} 条<span>按需展开</span></summary>
      ${list.slice(leadCount).map((it,i)=>newsRow(it,false,i)).join("")}</details>`:"")):
    `<div class="empty">${fActive?"没有符合筛选条件的条目。":"这个范围里没有条目，换成「全部」看完整归档。"}</div>`;

  const fs=s.feeds||[];
  const dead=fs.filter(f=>f.dead&&!f.paused&&!f.error);
  const failed=fs.filter(f=>f.error);
  const paused=fs.filter(f=>f.paused);
  const active=fs.filter(f=>!f.dead&&!f.paused&&!f.error);
  const unreadNear=recent.filter(i=>!i.read).length;
  const groups=new Set(fs.map(f=>f.group).filter(Boolean));
  const attention=fs.filter(f=>f.dead||f.paused||f.error).length;
  const ov=$("newsOverview");
  if(ov) ov.innerHTML=`<div><b>${recent.length} 条近期内容</b><span><span>${unreadNear} 未读</span><span>近 ${s.windowDays} 天</span></span></div>`+
    `<div><b>${fs.length} 个订阅源</b><span><span>${groups.size} 组 · ${active.length} 正常</span>${attention?`<span>${attention} 个需留意</span>`:""}</span></div>`+
    `<div><b>${all.length} 条归档</b><span><span>2022 年起</span><span>显示 ${list.length} 条</span></span></div>`;
  const brief=$("newsRiskBrief");
  if(brief){
    brief.classList.toggle("is-alert",!!attention);
    brief.innerHTML=`<div class="news-risk-copy"><b>${dead.length?`${dead.length} 个源已停更`
      :failed.length?`${failed.length} 个源抓取失败`
      :paused.length?`${paused.length} 个源已暂停`
      :"订阅源状态正常"}</b>
      <span>${dead.length
        ? `${esc(dead.map(f=>f.name).join("、"))} · 超过 ${s.feedStaleDays||100} 天无更新`
        : failed.length
          ? `${esc(failed.map(f=>f.name).join("、"))} · 最近一次抓取失败`
          : paused.length
            ? `${esc(paused.map(f=>f.name).join("、"))} · 已从日常刷新中停用`
            : `${active.length} 个源在最近 ${s.feedStaleDays||100} 天内有更新`}</span></div>
      <div class="news-risk-actions">
        ${dead.length?`<button class="btn" type="button" onclick="focusDeadFeeds()">查看停更源</button>`:""}
        <button class="btn" type="button" onclick="probeFeeds()">探查新源</button>
        <button class="btn" type="button" onclick="openFeedManage()">管理</button>
      </div>`;
  }
  $("newsFeedTag").textContent=fs.length?("共 "+fs.length+" 个"+(dead.length?"，"+dead.length+" 个停更":"")+(failed.length?"，"+failed.length+" 个失败":"")+(paused.length?"，"+paused.length+" 个暂停":"")):"—";
  const sortSel=$("feedSortSel");
  if(sortSel&&sortSel.value!==feedSortMode()) sortSel.value=feedSortMode();
  const fb=document.querySelector('#newsFeeds tbody');
  if(fb) fb.innerHTML=sortFeedsFor(fs).map(f=>{
    const dot=f.dead?"fdot-dead":f.error?"fdot-err":f.paused?"fdot-off":"";
    const tip=f.error?`抓取失败（${Number(f.errorCount)||0} 次）：${esc(f.error)} · 最后尝试 ${esc(feedClock(f.lastAttemptAt))}`
      :f.dead?`停更：超过 ${s.feedStaleDays||100} 天没有新条目 · 最后尝试 ${esc(feedClock(f.lastAttemptAt))}`
      :f.paused?`已从日常刷新中停用 · 最后尝试 ${esc(feedClock(f.lastAttemptAt))}`
      :`最后尝试 ${esc(feedClock(f.lastAttemptAt))}`;
    return `<tr class="${f.dead||f.error?"health-issue":"health-ok"}" title="${tip}">`+
    `<td class="tight"><span class="fdot ${dot}"></span>`+
      `<button class="feed-src" type="button" title="${esc(f.name)}" onclick="filterNewsBySource('${esc(f.name)}')">${esc(f.name)}</button></td>`+
    `<td class="tight">${esc(f.group||"—")}</td>`+
    `<td class="num">${f.count}</td>`+
    `<td class="num tight">${f.days===null?"—":esc(agoTxt(f.days))}</td>`+
    `</tr>`;
  }).join("");
  const fn=$("newsFeedNote");
  if(fn) fn.textContent = dead.length
    ? "状态灯：绿=正常、琥珀=停更、红=抓取失败、灰=已暂停。「停更」= 超过 "+(s.feedStaleDays||100)+" 天没有新条目，这类源不会报错，只会安静地不再出现。"
    : "状态灯：绿=正常、琥珀=停更、红=抓取失败、灰=已暂停。全部源都在活跃区间内。";
  $("newsNote").textContent=s.note||"";
}

/* 资讯阅读层：点击标题先留在工作台。标题仍是真外链（新窗口 + noopener），
   但拦截默认导航；后端无法提取时，底部「打开原文」始终可用。 */
const READER_FONT_STORAGE_KEY="wb_reader_font";
const READER_FONT_KEYS=new Set(["wenkai","fangsong","weekly","system"]);
function normalizeReaderFont(value){
  return READER_FONT_KEYS.has(String(value||""))?String(value):"wenkai";
}
function ensureReaderWeeklyFont(){
  if(document.getElementById("readerWeeklyFontCss")) return;
  const link=document.createElement("link");
  link.id="readerWeeklyFontCss";
  link.rel="stylesheet";
  link.href="https://tw93.fun/css/jinkai.css";
  link.dataset.readerFontAsset="weekly";
  document.head.appendChild(link);
}
function ensureReaderWenkaiWebFont(){
  if(document.getElementById("readerWenkaiRegularCss")) return;
  const assets=[
    ["readerWenkaiRegularCss","https://cdn.jsdelivr.net/npm/lxgw-wenkai-webfont@1.1.0/lxgwwenkai-regular.css"],
    ["readerWenkaiBoldCss","https://cdn.jsdelivr.net/npm/lxgw-wenkai-webfont@1.1.0/lxgwwenkai-bold.css"]
  ];
  for(const [id,href] of assets){
    const link=document.createElement("link");
    link.id=id;
    link.rel="stylesheet";
    link.href=href;
    link.dataset.readerFontAsset="wenkai";
    document.head.appendChild(link);
  }
}
function applyReaderFont(value,persist=false,loadAsset=false){
  const key=normalizeReaderFont(value);
  const overlay=$("newsReaderOv");
  if(overlay) overlay.setAttribute("data-reader-font",key);
  const select=$("newsReaderFont");
  if(select) select.value=key;
  if(key==="wenkai"&&loadAsset) ensureReaderWenkaiWebFont();
  if(key==="weekly"&&loadAsset) ensureReaderWeeklyFont();
  if(persist){try{localStorage.setItem(READER_FONT_STORAGE_KEY,key);}catch(e){}}
  return key;
}
function setNewsReaderFont(value){
  return applyReaderFont(value,true,true);
}
function readerCopyButton(text,label){
  return `<button class="reader-copy-btn" type="button" data-copy="${esc(text)}"
    aria-label="${esc(label)}" title="${esc(label)}" onclick="copyReaderBlock(this)">复制</button>`;
}
async function writeReaderClipboard(text){
  if(navigator.clipboard&&navigator.clipboard.writeText){
    await navigator.clipboard.writeText(text);
    return;
  }
  const area=document.createElement("textarea");
  area.value=text;
  area.setAttribute("readonly","");
  area.style.position="fixed";
  area.style.left="-9999px";
  area.style.top="0";
  document.body.appendChild(area);
  area.select();
  const ok=document.execCommand("copy");
  area.remove();
  if(!ok) throw new Error("浏览器不允许写入剪贴板");
}
async function copyReaderBlock(button){
  const text=button&&button.dataset?String(button.dataset.copy||""):"";
  if(!text) return;
  const label=button.getAttribute("aria-label")||"复制";
  if(button._copyTimer) clearTimeout(button._copyTimer);
  try{
    await writeReaderClipboard(text);
    button.textContent="已复制";
    button.classList.add("copied");
    button.setAttribute("aria-label","已复制");
  }catch(e){
    button.textContent="复制失败";
    button.classList.remove("copied");
    button.setAttribute("aria-label","复制失败，请重试");
  }
  button._copyTimer=setTimeout(()=>{
    button.textContent="复制";
    button.classList.remove("copied");
    button.setAttribute("aria-label",label);
    button._copyTimer=null;
  },1400);
}
let readerImageZoom=1, readerImageBaseWidth=0, readerImageReturnFocus=null;
let readerImageNaturalWidth=1200, readerImageNaturalHeight=800;
function openReaderImage(trigger){
  const image=trigger&&trigger.querySelector?trigger.querySelector("img"):null;
  const src=safeUrl(image&&(image.currentSrc||image.src||image.getAttribute("src")));
  if(!src) return;
  readerImageReturnFocus=trigger;
  readerImageNaturalWidth=Math.max(1,+(image.naturalWidth||1200));
  readerImageNaturalHeight=Math.max(1,+(image.naturalHeight||800));
  readerImageZoom=1;
  readerImageBaseWidth=0;
  const viewer=$("readerImageViewer");
  viewer.src=src;
  viewer.alt=image.alt||"";
  viewer.style.width="0px";
  const caption=String(trigger.dataset.caption||image.alt||"").trim();
  const captionNode=$("readerImageCaption");
  captionNode.textContent=caption;
  captionNode.hidden=!caption;
  const overlay=$("readerImageOv");
  overlay.setAttribute("aria-hidden","false");
  openOv("readerImageOv");
  requestAnimationFrame(()=>fitReaderImage());
}
function fitReaderImage(){
  const overlay=$("readerImageOv"), stage=$("readerImageStage"), viewer=$("readerImageViewer");
  if(!overlay||!stage||!viewer||!overlay.classList.contains("show")) return;
  const naturalWidth=Math.max(1,+(viewer.naturalWidth||readerImageNaturalWidth));
  const naturalHeight=Math.max(1,+(viewer.naturalHeight||readerImageNaturalHeight));
  readerImageNaturalWidth=naturalWidth;
  readerImageNaturalHeight=naturalHeight;
  const availableWidth=Math.max(120,stage.clientWidth-40);
  const availableHeight=Math.max(120,stage.clientHeight-40);
  const fit=Math.min(availableWidth/naturalWidth,availableHeight/naturalHeight);
  readerImageBaseWidth=Math.max(1,naturalWidth*fit);
  applyReaderImageZoom(false);
}
function applyReaderImageZoom(preserveCenter=true){
  const overlay=$("readerImageOv"), stage=$("readerImageStage"), viewer=$("readerImageViewer");
  const label=$("readerImageZoom");
  if(!overlay||!stage||!viewer||!label||!readerImageBaseWidth) return;
  const oldScrollWidth=Math.max(1,stage.scrollWidth);
  const centerX=(stage.scrollLeft+stage.clientWidth/2)/oldScrollWidth;
  const centerY=(stage.scrollTop+stage.clientHeight/2)/Math.max(1,stage.scrollHeight);
  viewer.style.width=Math.max(1,Math.round(readerImageBaseWidth*readerImageZoom))+"px";
  viewer.style.cursor=readerImageZoom>1?"zoom-out":"zoom-in";
  label.textContent=Math.round(readerImageZoom*100)+"%";
  if(!preserveCenter) return;
  requestAnimationFrame(()=>{
    stage.scrollLeft=centerX*stage.scrollWidth-stage.clientWidth/2;
    stage.scrollTop=centerY*stage.scrollHeight-stage.clientHeight/2;
  });
}
function setReaderImageZoom(value){
  readerImageZoom=Math.min(3,Math.max(.5,Math.round(+value*4)/4));
  applyReaderImageZoom(true);
}
function zoomReaderImage(delta){
  setReaderImageZoom(readerImageZoom+delta);
}
function resetReaderImageZoom(){
  readerImageZoom=1;
  applyReaderImageZoom(false);
  const stage=$("readerImageStage");
  if(stage){stage.scrollLeft=0;stage.scrollTop=0;}
}
function toggleReaderImageZoom(){
  if(readerImageZoom===1) setReaderImageZoom(2);
  else resetReaderImageZoom();
}
function closeReaderImage(){
  const target=readerImageReturnFocus;
  const overlay=$("readerImageOv");
  if(overlay) overlay.setAttribute("aria-hidden","true");
  closeOv("readerImageOv");
  modalReturnFocus.delete("readerImageOv");
  if(target&&target.isConnected){
    try{target.focus({preventScroll:true});}catch(e){try{target.focus();}catch(err){}}
  }
}
window.addEventListener("resize",()=>{
  const overlay=$("readerImageOv");
  if(overlay&&overlay.classList.contains("show")) fitReaderImage();
});
function readerSafeLinkUrl(value){
  const raw=String(value||"").trim();
  return /^(?:https?:\/\/|mailto:[^@\s]+@[^@\s]+)/i.test(raw)?raw:"";
}
function newsReaderSegmentsHTML(segments,fallback){
  const items=Array.isArray(segments)?segments:[];
  const html=items.map(segment=>{
    const text=esc(segment&&segment.text);
    if(!text) return "";
    const type=String(segment&&segment.type||"text");
    if(type==="strong") return `<strong>${text}</strong>`;
    if(type==="em") return `<em>${text}</em>`;
    if(type==="code") return `<code class="reader-inline-code">${text}</code>`;
    if(type==="link"){
      const href=readerSafeLinkUrl(segment.href);
      if(!href) return text;
      const external=/^https?:\/\//i.test(href);
      return `<a href="${esc(href)}"${external?' target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer"':""}>${text}</a>`;
    }
    return text;
  }).join("");
  return html||esc(fallback);
}
function newsReaderBlocksHTML(blocks){
  let imageIndex=0;
  return (blocks||[]).map(b=>{
    if(!b) return "";
    if(b.type==="hr") return "<hr>";
    if(b.type==="details"){
      const inner=newsReaderBlocksHTML(b.blocks||[]);
      if(!inner) return "";
      return `<details class="reader-fold"><summary>${esc(b.text||"展开查看")}</summary>`+
        `<div class="reader-fold-body">${inner}</div></details>`;
    }
    if(b.type==="image"){
      const src=safeUrl(b.src);
      if(!src) return "";
      const alt=esc(b.alt||"");
      const caption=esc(b.caption||"");
      const imageLabel=alt||caption||"正文图片";
      // Safari 在独立滚动容器里偶尔不会触发首图的 lazy-load，导致正文已出现但封面空白。
      // 首图优先加载，后续图片继续懒加载，兼顾首屏稳定性和长文流量。
      const loading=imageIndex++===0?"eager":"lazy";
      return `<figure class="reader-figure"><button class="reader-image-open" type="button"
        data-caption="${caption}" aria-label="放大图片：${imageLabel}" onclick="openReaderImage(this)">
        <img src="${esc(src)}" alt="${alt}" loading="${loading}" decoding="async"
          referrerpolicy="no-referrer"></button>${caption?`<figcaption>${caption}</figcaption>`:""}</figure>`;
    }
    if(b.type==="pre"){
      const code=esc(b.text);
      if(!code) return "";
      const language=esc(b.language||"");
      return `<div class="reader-code-block"><div class="reader-code-head">`+
        `<span class="reader-code-lang">${language||"纯文本"}</span>`+
        `${readerCopyButton(b.text,"复制代码或提示词")}</div>`+
        `<pre><code>${code}</code></pre></div>`;
    }
    const html=newsReaderSegmentsHTML(b.segments,b.text);
    if(!html) return "";
    if(b.type==="h2") return `<h2>${html}</h2>`;
    if(b.type==="h3") return `<h3>${html}</h3>`;
    if(b.type==="quote"){
      return `<div class="reader-quote-wrap"><blockquote>${html}</blockquote>`+
        `${readerCopyButton(b.text,"复制引用内容")}</div>`;
    }
    if(b.type==="li"){
      const marker=esc(b.marker||"");
      return `<div class="reader-list-item${b.ordered?" ordered":""}">${b.ordered?`<span class="reader-list-marker">${marker}</span>`:""}${html}</div>`;
    }
    return `<p>${html}</p>`;
  }).join("");
}
function newsReaderItemDate(item){
  const date=String(item&&item.published||"").slice(0,10).replaceAll("-","/");
  const ago=agoTxt(item&&item.days);
  return [date,ago&&ago!=="时间未知"?ago:""].filter(Boolean).join(" · ");
}
function renderNewsReaderList(){
  const box=$("newsReaderList"), count=$("newsReaderListCount");
  if(!box||!count) return;
  count.textContent=newsReaderItems.length
    ? `${newsReaderItems.length} 篇 · 点选切换，当前文章已标记`
    : "当前筛选下没有可切换的文章";
  box.innerHTML=newsReaderItems.length?newsReaderItems.map((item,i)=>{
    const active=i===newsReaderIndex;
    const date=newsReaderItemDate(item);
    return `<button class="reader-news-item${active?" active":""}" type="button"
      ${active?'aria-current="true"':""}${item.url?"":' disabled'}
      onclick="return selectNewsReader(${i},event)">
      <span class="reader-news-source">${esc(item.source||"资讯")}</span>
      <span class="reader-news-title">${esc(item.title||"未命名文章")}</span>
      ${date?`<span class="reader-news-date">${esc(date)}</span>`:""}
    </button>`;
  }).join(""):`<div class="empty">没有可切换的文章。</div>`;
  const active=box.querySelector(".reader-news-item.active");
  if(active) setTimeout(()=>{try{active.scrollIntoView({block:"nearest"});}catch(e){}},0);
}
function updateNewsReaderProgress(){
  const sc=$("newsReaderScroll"), bar=$("newsReaderProgress");
  if(!sc||!bar) return;
  const max=Math.max(0,sc.scrollHeight-sc.clientHeight);
  const ratio=max?Math.min(1,Math.max(0,sc.scrollTop/max)):0;
  bar.style.width=(ratio*100).toFixed(2)+"%";
  const headings=newsReaderHeadingIds.map(id=>$(id)).filter(Boolean);
  if(!headings.length) return;
  const line=sc.getBoundingClientRect().top+48;
  let current=headings[0].id;
  headings.forEach(h=>{if(h.getBoundingClientRect().top<=line) current=h.id;});
  document.querySelectorAll("#newsReaderTocList a").forEach(a=>{
    a.setAttribute("aria-current",a.dataset.target===current?"true":"false");
  });
}
function jumpNewsReaderHeading(id,event){
  if(event&&event.preventDefault) event.preventDefault();
  const target=$(id);
  if(target) target.scrollIntoView({block:"start",behavior:"smooth"});
  return false;
}
function renderNewsReaderTOC(){
  const body=$("newsReaderBody"), toc=$("newsReaderToc"), list=$("newsReaderTocList");
  if(!body||!toc||!list) return;
  const headings=[...body.querySelectorAll("h2,h3")];
  newsReaderHeadingIds=headings.map((h,i)=>`reader-heading-${i}`);
  headings.forEach((h,i)=>{h.id=newsReaderHeadingIds[i];});
  toc.hidden=headings.length===0;
  list.innerHTML=headings.map((h,i)=>
    `<a href="#${newsReaderHeadingIds[i]}" data-target="${newsReaderHeadingIds[i]}"
      class="level-${h.tagName==="H3"?"3":"2"}"
      onclick="return jumpNewsReaderHeading('${newsReaderHeadingIds[i]}',event)">${esc(h.textContent)}</a>`
  ).join("");
  requestAnimationFrame(updateNewsReaderProgress);
}
function resetNewsReaderPane(item){
  const fallbackTitle=String(item&&item.title||"文章").trim();
  $("newsReaderTitle").textContent=fallbackTitle;
  setNewsReaderMeta({
    site:String(item&&item.source||"资讯"),
    date:String(item&&item.published||"").slice(0,10).replaceAll("-","/"),
    detail:""
  });
  $("newsReaderBody").innerHTML="";
  $("newsReaderToc").hidden=true;
  $("newsReaderTocList").innerHTML="";
  newsReaderHeadingIds=[];
  const st=$("newsReaderState");
  st.hidden=false;
  st.className="reader-state";
  st.textContent="正在抓取正文…";
  $("newsReaderOriginal").href=item&&item.url||"#";
  $("newsReaderScroll").scrollTop=0;
  $("newsReaderProgress").style.width="0%";
}
function setNewsReaderMeta({site="",date="",detail=""}={}){
  const values={newsReaderSite:site,newsReaderDate:date,newsReaderMeta:detail};
  Object.entries(values).forEach(([id,value])=>{
    const node=$(id);
    if(!node) return;
    node.textContent=value;
    node.hidden=!value;
  });
}
function newsReaderSetError(message){
  const st=$("newsReaderState");
  if(!st) return;
  st.hidden=false;
  st.className="reader-state error";
  st.textContent=message+" 可直接点顶部「打开原文」。";
  $("newsReaderToc").hidden=true;
  updateNewsReaderProgress();
}
async function loadNewsReaderArticle(item){
  resetNewsReaderPane(item);
  const st=$("newsReaderState");
  if(!SERVED||!TOKEN){
    closeOv("newsReaderOv");
    try{window.open(item.url,"_blank","noopener,noreferrer");}catch(e){}
    return;
  }
  if(newsReaderAbort) newsReaderAbort.abort();
  const ctrl=new AbortController();
  newsReaderAbort=ctrl;
  const timer=setTimeout(()=>ctrl.abort(),16000);
  try{
    const api=ARTICLE_API+(ARTICLE_API.includes("?")?"&":"?")+"url="+encodeURIComponent(item.url);
    const r=await fetch(api,{cache:"no-store",signal:ctrl.signal});
    let data={};
    try{data=await r.json();}catch(e){}
    if(!r.ok) throw new Error(data.error||("HTTP "+r.status));
    if(newsReaderAbort!==ctrl) return;
    const title=String(data.title||item.title||"文章");
    const site=String(data.site||item.source||"资讯");
    const date=String(data.publishedAt||"").slice(0,10);
    const chars=+(data.chars||0);
    const mins=Math.max(1,Math.ceil(chars/500));
    $("newsReaderTitle").textContent=title;
    setNewsReaderMeta({
      site,
      date:date?date.replaceAll("-","/"):"",
      detail:[
      chars?`${chars.toLocaleString("zh-CN")} 字`:"",
      chars?`约 ${mins} 分钟`:""
      ].filter(Boolean).join(" · ")
    });
    $("newsReaderBody").innerHTML=newsReaderBlocksHTML(data.blocks);
    st.hidden=true;
    renderNewsReaderTOC();
    requestAnimationFrame(updateNewsReaderProgress);
  }catch(e){
    if(e&&e.name==="AbortError"){
      if(newsReaderAbort===ctrl) newsReaderSetError("抓取超时，原文页面可能需要登录或禁止自动读取。");
    }else{
      newsReaderSetError("这次没有取到正文："+String(e&&e.message||e));
    }
  }finally{
    clearTimeout(timer);
    if(newsReaderAbort===ctrl) newsReaderAbort=null;
  }
}
function selectNewsReader(index,event){
  if(event&&event.preventDefault) event.preventDefault();
  const item=newsReaderItems[index];
  if(!item||!item.url) return false;
  if(index===newsReaderIndex&&newsReaderUrl===item.url&&!newsReaderAbort) return false;
  newsReaderIndex=index;
  newsReaderUrl=item.url;
  renderNewsReaderList();
  loadNewsReaderArticle(item);
  return false;
}
function openNewsReader(anchor,event){
  if(event&&event.preventDefault) event.preventDefault();
  setNewsReaderMode("news");
  const url=safeUrl(anchor&&(anchor.href||anchor.getAttribute("href")));
  if(!url) return true;
  if($("newsReaderOv")&&$("newsReaderOv").getAttribute("data-reader-font")==="weekly"){
    ensureReaderWeeklyFont();
  }
  ensureReaderWenkaiWebFont();
  const fallbackTitle=String(anchor.textContent||"文章").trim();
  let index=newsReaderItems.findIndex(item=>item.url===url&&item.title===fallbackTitle);
  if(index<0) index=newsReaderItems.findIndex(item=>item.url===url);
  if(index<0){
    newsReaderItems.unshift({
      title:fallbackTitle,
      url,
      source:String(anchor.closest(".nitem")?.querySelector(".feed-src")?.textContent||"资讯").trim(),
      published:"",
      days:null,
      read:false
    });
    index=0;
  }
  newsReaderIndex=index;
  newsReaderUrl=url;
  renderNewsReaderList();
  openOv("newsReaderOv");
  loadNewsReaderArticle(newsReaderItems[index]);
  return false;
}

