/* ── 知识星球数据大盘 ────────────────────────────────────────────────
   数据全部来自 social.json（zsxq-cli 只读快照）。这里只算、不猜：
   拿不到阅读/点赞就显示"—"，不写 0，避免把"没数据"读成"没人看"。 */
function zsxqStats(social){
  const acc=((social&&social.accounts)||[]).find(a=>(a.platform||"").indexOf("知识星球")>=0);
  if(!acc) return null;
  const posts=(((social&&social.posts)||[])).filter(p=>(p.platform||"").indexOf("知识星球")>=0);
  /* 口径分三档：
     ① 话题总数、阅读最高 Top5 —— 始终全量（所有可读星球），不随下拉变。
     ② 统计星球下拉（zsxqMemberGroup）—— 空串=全部我的星球；非空=只看这一个星球。
        选定后成员/阅读/篇均/互动率/近30天/大盘状态/页头星球数全部跟着这个星球走。
     ③ 老快照没有 mine 标记时，自动档沿用全量，并在说明里写清，避免把"没标注"读成"没有我的星球"。 */
  const mineGroups=zsxqMineGroups(social);
  const mineKnown=mineGroups.size>0;
  const metrics=acc.metrics||{};
  const perGroup=metrics.perGroup||{};
  const members=metrics.members||{};
  const dashAll=metrics.dashboard||{};
  const rank=name=>[Number(members[name])||0,Number(perGroup[name])||0];
  const byRank=(a,b)=>{const ra=rank(a),rb=rank(b);
    return rb[0]-ra[0]||rb[1]-ra[1]||String(a).localeCompare(String(b),"zh");};
  const mineRanked=[...mineGroups].sort(byRank);
  const primaryGroup=mineRanked[0]||Object.keys(perGroup).slice().sort(byRank)[0]||"";
  // 老快照没有 mine 标记时，按成员数给出前 10 个星球作为可选项，别让下拉只剩一项。
  const selectable=mineRanked.length?mineRanked:Object.keys(perGroup).slice().sort(byRank).slice(0,10);
  const scopeGroup=selectable.indexOf(zsxqMemberGroup)>=0?zsxqMemberGroup:"";
  const scopeNames=scopeGroup?[scopeGroup]:(mineKnown?[...mineGroups]:null);
  const inScope=name=>scopeNames===null||scopeNames.indexOf(String(name||""))>=0;
  // mine=true 且没有 group 的帖子保留兜底；有 group 的一律按当前口径过滤。
  const scopePosts=scopeNames===null
    ? posts
    : posts.filter(p=>inScope(p.group)||(p.mine===true&&!p.group));
  // Number(null) 是 0：缺指标必须显式判空，否则"没读数"会被算成"0 人看"。
  const num=v=>{ if(v===null||v===undefined||v==="") return null;
    const n=Number(v); return isFinite(n)?n:null; };
  const sum=(arr)=>arr.reduce((a,b)=>a+(b||0),0);
  const reads=scopePosts.map(p=>num((p.metrics||{}).reads)).filter(v=>v!==null);
  const likes=scopePosts.map(p=>num((p.metrics||{}).likes)).filter(v=>v!==null);
  const comments=scopePosts.map(p=>num((p.metrics||{}).comments)).filter(v=>v!==null);
  const readSum=reads.length?sum(reads):null;
  const interact=readSum? ((sum(likes)+sum(comments))/readSum*100) : null;
  // 清单仍要列出全部星球，所以 perGroup/members 原样返回；这里另算一份当前口径。
  const pick=obj=>scopeNames===null
    ? Object.assign({},obj)
    : Object.fromEntries(Object.entries(obj).filter(([k])=>inScope(k)));
  const scopePerGroup=pick(perGroup), scopeMembers=pick(members), scopeDash=pick(dashAll);
  /* 自动档成员总数取我的星球里成员最多的那个（当前是主星球）；指定档就是所选星球。 */
  const memberGroup=scopeGroup||primaryGroup;
  const memberTotal=Number(members[memberGroup])||0;
  const dashOk=Object.values(scopeDash).filter(d=>d&&d.status==="ok").length;
  const dashFree=Object.entries(scopeDash).filter(([,d])=>d&&d.status==="unsupported").map(([k])=>k);
  const cutoff=Date.now()-30*86400000;
  const last30=scopePosts.filter(p=>{
    const t=Date.parse(p.publishedAt||"");
    return isFinite(t)&&t>=cutoff;
  }).length;
  /* 阅读最高的 5 条是「全量所有星球」的排行，不随统计星球下拉收窄：
     它是用来看整个可读范围里什么内容最能跑，收窄反而丢了参照。 */
  const top=posts.slice()
    .filter(p=>num((p.metrics||{}).reads)!==null)
    .sort((a,b)=>num((b.metrics||{}).reads)-num((a.metrics||{}).reads))
    .slice(0,5);
  return {acc,posts:posts.length,scopePosts:scopePosts.length,mineKnown,
          groups:Object.keys(scopePerGroup).length,allGroups:Object.keys(perGroup).length,memberTotal,
          memberGroup,primaryGroup,scopeGroup,mineGroupNames:selectable,
          readSum,avgRead:readSum&&reads.length?Math.round(readSum/reads.length):null,
          interact,last30,top,dashOk,dashFree,
          perGroup,members,scopePerGroup,scopeMembers,scopeDash,asOf:acc.asOf};
}
/* 两处大盘共用同一句口径说明，避免一处写"我的"、另一处还在说全量。 */
function zsxqScopeNote(st){
  const head="话题总数、阅读最高的 5 条为全部星球；";
  const scope=st.scopeGroup
    ? `其余指标只看「${st.scopeGroup}」`
    : st.mineKnown
      ? `其余指标只看我的 ${st.groups} 个星球；成员总数只取「${st.memberGroup}」`
      : "未识别我的星球，暂按全部星球统计";
  const free=st.dashFree.length===0?""
    : st.dashFree.length<=3
      ? `；${st.dashFree.join("、")} 为免费星球，官方不提供活跃大盘`
      : `；另有 ${st.dashFree.length} 个星球无官方大盘`;
  return `${head}${scope}。运营大盘可用 ${st.dashOk} 个星球${free}。`;
}

function zsxqFigure(label,value,hint){
  return `<div class="zsxq-figure"><span>${esc(label)}</span>`+
    `<b>${value==null?"—":esc(String(value))}</b>`+
    (hint?`<em>${esc(hint)}</em>`:"")+`</div>`;
}

/* ── 知识星球独立一屏 ────────────────────────────────────────────────
   数据仍然只来自 social.json（zsxq-cli 快照），这一屏只是换一种组织方式：
   上面指标、中间时间线、右侧星球清单。不新增抓取通道，也不改数据结构。 */
let zsxqQuery="", zsxqSort="new", zsxqShown=10;
let zsxqPageIndex=1;                /* 时间线当前页（1 起）；筛选变化即回第一页 */
let zsxqOpen=null, zsxqDetail={};   /* 展开中的话题 + 已拉到的全文缓存（只读） */   /* 当前渲染条数上限 */
const ZSXQ_PAGE=10;                                /* 默认每页 10，可切 20 / 50 / 全部 */
/* 星球筛选 / 清单排序偏好：只影响展示，存本地，不写数据文件。 */
/* 我的星球固定置顶，不再提供开关；只持久化排序方式。 */
let zsxqGroupFilter="", zsxqGroupSort="posts";
let zsxqRange=0;  /* 时间范围（天，0=全部）。与星球/每页筛选一样只影响展示。 */
/* 大盘统计星球：空串=全部我的星球（成员取主星球代表），非空=所有指标只看这一个星球。
   话题总数、阅读最高 Top5 始终是全部星球，不受它影响。只影响展示，存本地。 */
let zsxqMemberGroup="";
function zsxqLoadMemberGroup(){
  try{zsxqMemberGroup=String(localStorage.getItem("zsxqMemberGroup")||"");}catch(e){}
}
function zsxqMemberSelect(st,id){
  const sel=$(id); if(!sel) return;
  const auto=st.mineKnown?"全部我的星球":(st.primaryGroup?"全部星球（默认）":"自动：全部");
  sel.innerHTML=`<option value="">${esc(auto)}</option>`+
    st.mineGroupNames.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join("");
  sel.value=zsxqMemberGroup;
}
function setZsxqMemberGroup(v){
  zsxqMemberGroup=String(v||"");
  try{localStorage.setItem("zsxqMemberGroup",zsxqMemberGroup);}catch(e){}
  renderZsxqScreen();
  if(social) renderZsxqPanel(social);   // 数据没加载完时别改写隐藏面板的文案
}
zsxqLoadMemberGroup();
function zsxqLoadGroupView(){
  try{
    const raw=localStorage.getItem("zsxqGroupView");
    if(raw){const v=JSON.parse(raw);
      zsxqGroupSort=(v&&v.sort==="name")?"name":"posts";
    }
  }catch(e){}
}
function zsxqSaveGroupView(){
  try{localStorage.setItem("zsxqGroupView",JSON.stringify({sort:zsxqGroupSort}));}catch(e){}
}
zsxqLoadGroupView();

function zsxqPosts(social){
  return (((social&&social.posts)||[])).filter(p=>(p.platform||"").indexOf("知识星球")>=0);
}

/* 我的星球：由 post.mine 推导 group 名，星球清单据此置顶、时间线据此标"我的"。 */
function zsxqMineGroups(social){
  const set=new Set();
  zsxqPosts(social).forEach(p=>{if(p.mine===true&&p.group) set.add(String(p.group));});
  return set;
}

function zsxqGroupsOf(social){
  const st=zsxqStats(social);
  if(!st) return [];
  const dash=(st.acc.metrics||{}).dashboard||{};
  const mine=zsxqMineGroups(social);
  const groups=Object.keys(st.perGroup||{}).map(name=>({
    name,
    mine:mine.has(name),
    posts:st.perGroup[name],
    members:(st.members||{})[name]??null,
    dashboard:dash[name]||null
  }));
  const by=zsxqGroupSort==="name"
    ? (a,b)=>String(a.name).localeCompare(String(b.name),"zh")
    : (a,b)=>b.posts-a.posts;
  // 我的星球固定置顶，其余按当前排序方式。
  return groups.sort((a,b)=>{
    if(a.mine!==b.mine) return a.mine?-1:1;
    return by(a,b);
  });
}

/* 时间线里真有内容的星球，用于筛选下拉（不列空星球，避免选了没结果）。 */
function zsxqGroupsInTimeline(social){
  const seen=[];
  zsxqPosts(social).forEach(p=>{const g=String(p.group||"");if(g&&seen.indexOf(g)<0)seen.push(g);});
  return seen;
}

function zsxqTimelineRows(social){
  const q=zsxqQuery.trim().toLowerCase();
  let rows=zsxqPosts(social);
  if(zsxqGroupFilter) rows=rows.filter(p=>String(p.group||"")===zsxqGroupFilter);
  if(zsxqRange>0){
    const cutoff=Date.now()-zsxqRange*86400000;
    rows=rows.filter(p=>{const t=Date.parse(p.publishedAt||"");return !isNaN(t)&&t>=cutoff;});
  }
  // 标签也要能搜到：自己的星球用标签展示，只搜标题会漏掉按标签找的场景。
  if(q) rows=rows.filter(p=>(p.title||"").toLowerCase().includes(q)
    || (Array.isArray(p.tags)?p.tags:[]).some(t=>String(t).toLowerCase().includes(q)));
  const key=zsxqSort==="reads"
    ? (a,b)=>((b.metrics||{}).reads||0)-((a.metrics||{}).reads||0)
    : (a,b)=>String(b.publishedAt||"").localeCompare(String(a.publishedAt||""));
  return rows.slice().sort(key);
}

function setZsxqSort(v){
  zsxqSort=(v==="reads")?"reads":"new";
  zsxqShown=ZSXQ_PAGE; zsxqPageIndex=1;
  renderZsxqScreen();
}

/* 星球筛选：再点一次同一个星球就取消，回到全部。 */
function setZsxqGroup(name){
  const v=String(name||"");
  zsxqGroupFilter=(zsxqGroupFilter===v)?"":v;
  zsxqShown=ZSXQ_PAGE; zsxqPageIndex=1;
  renderZsxqScreen();
}

/* 时间范围：再点一次同一档就回到全部。 */
function setZsxqRange(days){
  const v=Number(days)||0;
  zsxqRange=(zsxqRange===v)?0:v;
  zsxqShown=ZSXQ_PAGE; zsxqPageIndex=1;
  renderZsxqScreen();
}

/* 星球清单：我的星球固定置顶，排序偏好即时写本地，刷新后保持。 */
function setZsxqGroupSort(v){
  zsxqGroupSort=(v==="name")?"name":"posts";
  zsxqSaveGroupView();
  renderZsxqScreen();
}

/* page=0 表示全部。条数写在按钮上，用户一眼知道会铺多少。 */

/* ── 发布（公开写入，两步走）────────────────────────────────────────
   预览只读；发布必须带预览返回的 digest + confirm:true。
   服务端还会校验 digest 一致、30 分钟内同内容拒发。 */
let zsxqDraft=null;

/* 星球专属同步：只刷新知识星球，不重跑整条 social-sync（那条 85 秒且会抓 X）。 */
let zsxqSyncing=false;

function zsxqSync(){
  if(zsxqSyncing) return;
  const btn=$("zsxqSyncBtn"), note=$("zsxqScreenNote");
  zsxqSyncing=true;
  if(btn){ btn.disabled=true; btn.textContent="同步中…"; }
  if(note) note.textContent="正在从知识星球拉取最新数据…";
  const url="/api/zsxq/sync";
  fetch(url,{method:"POST",cache:"no-store"})
    .then(r=>r.json().then(d=>({status:r.status,d})))
    .then(({status,d})=>{
      if(!d.ok) throw new Error(d.error||("HTTP "+status));
      return loadSocial().then(()=>{
        renderZsxqScreen();
        const n=$("zsxqScreenNote");
        if(n) n.textContent=`已同步 ${d.posts} 条话题 · ${d.groups||0} 个星球 · ${d.at||""}`;
      });
    })
    .catch(e=>{ const n=$("zsxqScreenNote"); if(n) n.textContent="同步失败："+String(e.message||e); })
    .finally(()=>{
      zsxqSyncing=false;
      if(btn){ btn.disabled=false; btn.textContent="同步数据"; }
    });
}

/* 目标星球多选：只允许配置白名单里的星球（后端还会再校验一次）。 */
let zsxqTargetOptions=[], zsxqTargetSel=[];

function zsxqTargetSummaryText(){
  const chosen=zsxqTargetOptions.filter(t=>zsxqTargetSel.includes(t.groupId));
  if(!chosen.length) return "选星球";
  if(chosen.length===1) return chosen[0].groupName;
  return chosen.length+" 个星球："+chosen.map(t=>t.groupName).join("、");
}

function zsxqRenderTargets(){
  const sum=$("zsxqTargetSummary"), panel=$("zsxqTargetPanel");
  if(sum) sum.textContent=zsxqTargetSummaryText()+(zsxqTargetOptions.length>1?" ▾":"");
  if(!panel) return;
  if(!zsxqTargetOptions.length){ panel.innerHTML=""; return; }
  panel.innerHTML=zsxqTargetOptions.map(t=>{
    const on=zsxqTargetSel.includes(t.groupId);
    const id="zsxqTgt-"+t.groupId;
    // label 用 for= 显式绑定，无障碍检查和键盘都能用
    return `<div class="zsxq-dd-row"><input id="${esc(id)}" type="checkbox" ${on?"checked":""} `+
      `onchange="zsxqToggleTarget('${esc(t.groupId)}')"><label for="${esc(id)}">${esc(t.groupName)}</label></div>`;
  }).join("")+`<div class="zsxq-dd-note">至少选一个。默认只发主星球，加选会同时发。</div>`;
}

function zsxqToggleTargetDD(){
  const panel=$("zsxqTargetPanel"), btn=$("zsxqTargetBtn");
  if(!panel) return;
  panel.hidden=!panel.hidden;
  if(btn) btn.setAttribute("aria-expanded", panel.hidden?"false":"true");
}

function zsxqCloseTargetDD(){
  const panel=$("zsxqTargetPanel"), btn=$("zsxqTargetBtn");
  if(panel&&!panel.hidden){
    panel.hidden=true;
    if(btn) btn.setAttribute("aria-expanded","false");
  }
}

/* 点面板外面收起 —— 只在浏览器里有 document 时挂 */
if(typeof document!=="undefined"&&document.addEventListener){
  document.addEventListener("click",e=>{
    const box=$("zsxqTargetDD");
    if(!box||!e.target||typeof box.contains!=="function") return;
    if(!box.contains(e.target)) zsxqCloseTargetDD();
  });
}

function zsxqToggleTarget(gid){
  const i=zsxqTargetSel.indexOf(gid);
  if(i>=0) zsxqTargetSel.splice(i,1); else zsxqTargetSel.push(gid);
  if(!zsxqTargetSel.length) zsxqTargetSel.push(gid);   // 至少留一个，别让它空
  zsxqRenderTargets();
  const btn=$("zsxqSend"); if(btn) btn.disabled=true;   // 目标变了要重新预览
  const note=$("zsxqSendNote");
  if(note&&zsxqTargetSel.length>1) note.textContent=`将同时发到 ${zsxqTargetSel.length} 个星球，先预览。`;
}

function zsxqTargetsChosen(){ return zsxqTargetSel.slice(); }

function zsxqToggleCompose(open){
  const box=$("zsxqCompose");
  if(!box) return;
  box.hidden = open===false ? true : !box.hidden;
  if(!box.hidden){
    fetch("/api/zsxq/publish-config",{cache:"no-store"})
      .then(r=>r.json())
      .then(d=>{
        const tag=$("zsxqComposeTarget");
        zsxqTargetOptions=(d.targets||[]).map(t=>({groupId:String(t.groupId),groupName:t.groupName||t.groupId}));
        const keep=zsxqTargetSel.filter(g=>zsxqTargetOptions.some(t=>t.groupId===g));
        zsxqTargetSel=keep.length?keep:(d.groupId?[String(d.groupId)]:(zsxqTargetOptions[0]?[zsxqTargetOptions[0].groupId]:[]));
        zsxqRenderTargets();
        if(tag) tag.textContent=d.enabled?(`${zsxqTargetOptions.length} 个可发布星球`):"未配置目标星球";
        const btn=$("zsxqSend"); if(btn) btn.disabled=true;
        zsxqDraft=null;
        const note=$("zsxqSendNote");
        if(note) note.textContent=d.enabled?"先预览，确认内容无误再发布。":"没有配置发帖目标，先在 zsxq-publish.json 里设置。";
      })
      .catch(()=>{});
    zsxqLoadTags();
  }
}

/* 标签选择：常用直接点，全量走搜索面板。
   星球标签会越攒越多（现在 42 个），平铺一屏没法看，所以只铺高频的前 6 个。 */
let zsxqTagOptions=[], zsxqTagsSel=[], zsxqTagsLoaded=false;

function zsxqLoadTags(){
  if(zsxqTagsLoaded) return Promise.resolve();
  const url="/api/zsxq/tags";
  return fetch(url,{cache:"no-store"})
    .then(r=>r.json())
    .then(d=>{ if(d.ok){ zsxqTagOptions=d.items||[]; zsxqTagsLoaded=true; } })
    .catch(()=>{})
    .then(()=>renderZsxqTags());
}

function zsxqCollectTags(){
  const custom=(($("zsxqTags")||{}).value||"").split(/[,，\s]+/).filter(Boolean);
  return zsxqTagsSel.concat(custom.filter(t=>!zsxqTagsSel.includes(t)));
}

function zsxqToggleTag(name){
  if(!name) return;
  const i=zsxqTagsSel.indexOf(name);
  if(i>=0) zsxqTagsSel.splice(i,1); else zsxqTagsSel.push(name);
  renderZsxqTags();
}

function zsxqToggleTagPanel(){
  const panel=$("zsxqTagPanel");
  if(!panel) return;
  panel.hidden=!panel.hidden;
  if(!panel.hidden){ zsxqLoadTags(); renderZsxqTags(); }
}

/* 置顶标签：这三个是日常必用（bot 还决定 AI 声明），永远排在前面 */
const ZSXQ_PINNED_TAGS=["AI","碎屑","bot"];

function zsxqOrderTags(items){
  const pinned=zsxqTagOptions.length?zsxqTagOptions:items;
  return items.slice().sort((a,b)=>{
    const ia=ZSXQ_PINNED_TAGS.indexOf(a.name), ib=ZSXQ_PINNED_TAGS.indexOf(b.name);
    if(ia>=0||ib>=0){
      if(ia<0) return 1;
      if(ib<0) return -1;
      return ia-ib;                     // 按置顶表里的先后
    }
    return (b.count||0)-(a.count||0);   // 其余仍按使用次数
  });
}

/* 快捷 chip：先置顶，再按次数补到 6 个；置顶标签即使接口没返回也要显示 */
function zsxqQuickTags(){
  const byName={};
  zsxqTagOptions.forEach(t=>{ byName[t.name]=t; });
  const pinned=ZSXQ_PINNED_TAGS.map(n=>byName[n]||{name:n,count:0});
  const rest=zsxqTagOptions.filter(t=>ZSXQ_PINNED_TAGS.indexOf(t.name)<0)
    .sort((a,b)=>(b.count||0)-(a.count||0));
  return pinned.concat(rest)
    .filter(t=>zsxqTagsSel.indexOf(t.name)<0)
    .filter((t,i,arr)=>arr.findIndex(x=>x.name===t.name)===i)
    .slice(0,6);
}

function renderZsxqTags(){
  const chips=$("zsxqTagChips"), list=$("zsxqTagList");
  zsxqSyncAiMode();
  if(chips){
    const quick=zsxqQuickTags();
    // 标签名可能很长（"《隐士财富哲学》解读"），chip 上截断，完整名放 title
    const brief=n=>n.length>10?n.slice(0,10)+"…":n;
    chips.innerHTML=zsxqTagsSel.map(n=>`<button class="zsxq-chip on" type="button" title="#${esc(n)}" onclick="zsxqToggleTag('${esc(n)}')">#${esc(brief(n))} ✕</button>`).join("")+
      quick.map(t=>`<button class="zsxq-chip" type="button" title="#${esc(t.name)}" onclick="zsxqToggleTag('${esc(t.name)}')">#${esc(brief(t.name))}</button>`).join("");
  }
  if(list){
    const q=(($("zsxqTagQ")||{}).value||"").trim().toLowerCase();
    const rows=zsxqOrderTags(zsxqTagOptions.filter(t=>!q||t.name.toLowerCase().includes(q))).slice(0,40);
    list.innerHTML=rows.length?rows.map(t=>{
      const on=zsxqTagsSel.includes(t.name);
      return `<button class="zsxq-tagitem${on?" on":""}" type="button" onclick="zsxqToggleTag('${esc(t.name)}')">`+
        `<span>#${esc(t.name)}</span><em>${t.count||0}</em></button>`;
    }).join(""):`<div class="no-note">没有匹配的标签，可在左边自定义输入。</div>`;
  }
}

/* 预览按发布后的样子渲染：标签显示成胶囊，正文照常。
   原始富文本标记藏在「查看源码」里，避免一屏尖括号。 */
function zsxqPreviewParts(finalText){
  const text=String(finalText||"");
  const lines=text.split("\n");
  const first=lines[0]||"";
  const isMarkup=/<e\s+type="hashtag"/.test(first);
  const tags=isMarkup
    ? [...first.matchAll(/title="([^"]+)"/g)]
        .map(m=>{ try{ return decodeURIComponent(m[1]).replace(/^#+|#+$/g,""); }catch(e){ return ""; } })
        .filter(Boolean)
    : [];
  return {tags, body: isMarkup?lines.slice(1).join("\n"):text, raw:text};
}

function zsxqRenderPreview(d){
  const box=$("zsxqPreviewBox"); if(!box) return;
  const warn=(d.warnings||[]).length
    ? `<div class="zsxq-warn">${d.warnings.map(w=>esc(w)).join("；")}</div>` : "";
  const parts=zsxqPreviewParts(d.finalText);
  const chips=parts.tags.length
    ? `<div class="zsxq-preview-tags">${parts.tags.map(t=>`<span class="zsxq-ptag">#${esc(t)}</span>`).join("")}</div>` : "";
  box.innerHTML=`<div class="zsxq-preview-head">将要发布 · ${esc(d.groupName||"")} · ${esc(d.aiMode||"")} · ${d.chars||0} 字</div>`+
    `<div class="zsxq-preview-card">${chips}<div class="zsxq-preview-body">${esc(parts.body)}</div></div>`+
    warn+
    `<details class="zsxq-raw"><summary>查看源码</summary><pre class="zsxq-preview-text">${esc(parts.raw)}</pre></details>`;
}

/* AI 声明自动判断：沿用内容约定——AI 写的一律带 #bot 标签。
   所以默认不看下拉框，标签里有 bot 就是 aigc，没有就不声明；
   手动改过一次就记住，不再跟着标签翻。 */
let zsxqAiManual=false;
const ZSXQ_AI_LABEL={aigc:"AI 生成（aigc）",personal_perspective:"个人观点",none:"不声明"};

function zsxqAutoAiMode(){
  return zsxqTagsSel.includes("bot") ? "aigc" : "none";
}

function zsxqSyncAiMode(){
  const sel=$("zsxqAiMode");
  if(!sel) return "none";
  if(!zsxqAiManual) sel.value=zsxqAutoAiMode();
  const txt=$("zsxqAiModeText");
  if(txt) txt.textContent=ZSXQ_AI_LABEL[sel.value]||sel.value;
  return sel.value;
}

function zsxqToggleAiOverride(){
  const sel=$("zsxqAiMode"); if(!sel) return;
  sel.hidden=!sel.hidden;
  if(!sel.hidden){ zsxqAiManual=true; if(typeof sel.focus==="function") sel.focus(); }
}

function zsxqAiModePicked(){
  zsxqAiManual=true;
  const sel=$("zsxqAiMode");
  const txt=$("zsxqAiModeText");
  if(txt&&sel) txt.textContent=ZSXQ_AI_LABEL[sel.value]||sel.value;
}

function zsxqCurrentAiMode(){
  const v=zsxqSyncAiMode();
  return v||"none";
}

/* AI 续写：只往正文框里写草稿，顺带把 #bot 选上（声明就自动变 aigc）。
   绝不自动预览、绝不自动发布——要不要发还是人点。 */
function zsxqApplyDraft(text){
  const box=$("zsxqText");
  if(box) box.value=String(text||"").trim();
  if(!zsxqTagsSel.includes("bot")) zsxqTagsSel.push("bot");
  zsxqAiManual=false;
  renderZsxqTags();
  const note=$("zsxqDraftNote");
  if(note) note.textContent="草稿已填进正文框，自己过一遍再预览。已自动勾上 #bot。";
  return box?box.value:"";
}

function zsxqGenDraft(){
  const seed=($("zsxqSeed")||{}).value||"";
  const note=$("zsxqDraftNote"), btn=$("zsxqDraftBtn");
  if(!seed.trim()){ if(note) note.textContent="先写一句起因，比如「今天踩了个什么坑」。"; return; }
  if(btn) btn.disabled=true;
  if(note) note.textContent="生成中…（本地模型，十几秒）";
  const url="/api/zsxq/draft";
  fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({seed})})
    .then(r=>r.json())
    .then(d=>{
      if(d&&d.ok){ zsxqApplyDraft(d.text); return; }
      if(note) note.textContent=(d&&d.error)||"生成失败";
    })
    .catch(e=>{ if(note) note.textContent="生成失败："+String(e.message||e); })
    .finally(()=>{ if(btn) btn.disabled=false; });
}

function zsxqPreview(){
  const text=($("zsxqText")||{}).value||"";
  const tags=zsxqCollectTags();
  const aiMode=zsxqCurrentAiMode();
  const note=$("zsxqSendNote");
  if(!text.trim()){ if(note) note.textContent="正文不能为空。"; return; }
  const url="/api/zsxq/preview";
  fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({text,tags,aiMode,groupIds:zsxqTargetsChosen()})})
    .then(r=>r.json())
    .then(d=>{
      zsxqDraft=d.ok?d:null;
      zsxqRenderPreview(d);
      const btn=$("zsxqSend"); if(btn) btn.disabled=!d.ok;
      if(note) note.textContent=d.ok
        ? `确认无误后点「确认发布」，会真的发到 ${(d.targets||[]).length||1} 个星球。`
        : (d.error||"预览失败");
    })
    .catch(e=>{ if(note) note.textContent="预览失败："+String(e.message||e); });
}

function zsxqPublish(){
  if(!zsxqDraft||!zsxqDraft.digest){ return; }
  const chosen=zsxqTargetsChosen();
  const names=(zsxqDraft.targets||[]).filter(t=>chosen.includes(String(t.groupId)))
    .map(t=>t.groupName||t.groupId);
  const target=names.length?names.join("、"):(zsxqDraft.groupName||zsxqDraft.groupId||"");
  if(typeof window!=="undefined"&&typeof window.confirm==="function"){
    if(!window.confirm("确认发布到「"+target+"」？发布后对这些星球的成员都可见。")) return;
  }
  const note=$("zsxqSendNote"), btn=$("zsxqSend");
  if(btn) btn.disabled=true;
  if(note) note.textContent="发布中…";
  const url="/api/zsxq/publish";
  fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({text:($("zsxqText")||{}).value||"",
      tags:zsxqCollectTags(),
      aiMode:zsxqCurrentAiMode(),
      groupIds:zsxqTargetsChosen(),
      digests:(zsxqDraft.targets||[]).reduce((m,t)=>{m[String(t.groupId)]=t.digest;return m;},{}),
      digest:zsxqDraft.digest,confirm:true})})
    .then(r=>r.json())
    .then(d=>{
      const rows=Array.isArray(d.results)?d.results:[];
      if(note){
        if(d.ok){
          note.textContent="已发布到 "+(rows.length||1)+" 个星球"+(rows.length>1?"："+rows.map(r=>r.groupName||r.groupId).join("、"):"")+"。到星球核对一下。";
        }else if(d.partial){
          const bad=rows.filter(r=>!r.ok);
          note.textContent="部分成功：失败 "+bad.map(r=>(r.groupName||r.groupId)+"（"+(r.error||"未知")+"）").join("、");
        }else{
          note.textContent="发布失败："+(d.error||"未知原因");
        }
      }
      if(d.ok){ zsxqDraft=null; const t=$("zsxqText"); if(t) t.value=""; const p=$("zsxqPreviewBox"); if(p) p.innerHTML=""; }
      else if(btn) btn.disabled=false;
    })
    .catch(e=>{ if(note) note.textContent="发布失败："+String(e.message||e); if(btn) btn.disabled=false; });
}

function zsxqTopicId(p){
  return String((p&&p.url)||"").replace(/\/+$/,"").split("/").pop()||"";
}

/* 展开全文：只读接口 /api/zsxq/topic，一次一条，拉到就缓存。 */
function zsxqToggle(id){
  if(!id) return;
  if(zsxqOpen===id){ zsxqOpen=null; renderZsxqScreen(); return; }
  zsxqOpen=id;
  if(zsxqDetail[id]){ renderZsxqScreen(); return; }
  renderZsxqScreen();                       // 先显示"读取中"
  const url="/api/zsxq/topic?id="+encodeURIComponent(id);
  fetch(url,{cache:"no-store"})
    .then(r=>r.json().then(d=>({ok:r.ok,d})))
    .then(({ok,d})=>{
      zsxqDetail[id]= ok&&d.text ? {text:d.text} : {error:(d&&d.error)||"读取失败"};
      if(zsxqOpen===id) renderZsxqScreen();
    })
    .catch(e=>{ zsxqDetail[id]={error:String(e.message||e)}; if(zsxqOpen===id) renderZsxqScreen(); });
}

function setZsxqPage(page){
  zsxqShown=page>0?page:1e9;
  zsxqPageIndex=1;                 // 改每页条数后从第一页重新数
  renderZsxqScreen();
}
function setZsxqPageIndex(i){
  zsxqPageIndex=Math.max(1,Number(i)||1);
  renderZsxqScreen();
}

let zsxqDigestMode="daily";
function setZsxqDigestMode(mode){
  zsxqDigestMode=mode==="weekly"?"weekly":"daily";
  renderZsxqDaily();
}

async function syncZsxqDaily(btn){
  if(!SERVED){ notify("离线模式无法刷新星球简报，请在在线服务下操作。"); return; }
  if(guest){ notify("访客模式下不刷新真实星球简报。"); return; }
  if(btn){ btn.disabled=true; btn.textContent="刷新中…"; }
  try{
    const r=await fetch("/api/zsxq/daily-sync",{method:"POST"});
    const j=await r.json().catch(()=>({}));
    if(!r.ok||!j.ok) throw new Error(j.error||("HTTP "+r.status));
    zsxqDaily=null;
    await loadZsxqDaily();
    renderZsxqDaily();
    if(typeof renderMorningBrief==="function") renderMorningBrief();
    notify(`星球简报已更新（${j.elapsed??"—"}s）`);
  }catch(e){
    notify("星球简报刷新失败："+(e&&e.message||e));
  }finally{
    if(btn){ btn.disabled=false; btn.textContent="刷新简报"; }
  }
}

const SOCIAL_PLATFORM_TARGET={"公众号":"wechat_mp","知识星球":"zsxq","X":"x","小红书":"xhs"};
async function syncSocialPlatform(target, btn){
  if(!SERVED){ notify("离线模式无法单源刷新，请在在线服务下操作。"); return; }
  if(guest){ notify("访客模式下不执行单源刷新。"); return; }
  if(!target) return;
  const origText=btn?btn.textContent:"快刷";
  if(btn){ btn.disabled=true; btn.textContent="刷新中…"; }
  try{
    const r=await fetch("/api/social/sync",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({target})
    });
    const j=await r.json().catch(()=>({}));
    if(!r.ok||!j.ok) throw new Error(j.error||("HTTP "+r.status));
    social=null;
    await loadSocial();
    renderSections();
    notify(`${target} 单源已刷新（${j.elapsed??"—"}s）`);
  }catch(e){
    notify(`单源刷新失败：${e&&e.message||e}`);
  }finally{
    if(btn){ btn.disabled=false; btn.textContent=origText; }
  }
}

function renderZsxqDaily(){
  const card=$("zdailyCard"), tag=$("zdailyTag"), win=$("zdailyWindow"),
        summary=$("zdailySummary"), body=$("zdailyBody"), foot=$("zdailyFoot");
  if(!card||!body) return;
  const bDaily=$("zdailyModeDaily"), bWeekly=$("zdailyModeWeekly");
  if(bDaily){ bDaily.classList.toggle("on",zsxqDigestMode==="daily"); bDaily.setAttribute("aria-selected",String(zsxqDigestMode==="daily")); }
  if(bWeekly){ bWeekly.classList.toggle("on",zsxqDigestMode==="weekly"); bWeekly.setAttribute("aria-selected",String(zsxqDigestMode==="weekly")); }
  if(guest){
    if(tag) tag.textContent="访客模式";
    if(win) win.textContent="脱敏演示 · 不读本机星球简报";
    if(summary){ summary.hidden=true; summary.innerHTML=""; }
    body.innerHTML=`<div class="zdaily-empty">访客模式下不加载个人知识星球日报/周报快照。</div>`;
    if(foot) foot.textContent="";
    return;
  }
  if(!zsxqDaily){
    if(tag) tag.textContent=zsxqDailyLoading?"读取中":"未生成";
    if(win) win.textContent=zsxqDigestMode==="weekly"?"过去 7 天滚动窗口":"过去 24 小时滚动窗口";
    if(summary){ summary.hidden=true; summary.innerHTML=""; }
    body.innerHTML=`<div class="zdaily-empty">${esc(zsxqDailyErr||"还没有知识星球简报快照：先跑 python3 scripts/sync/zsxq-daily-sync.py。")}</div>`;
    if(foot) foot.textContent="";
    return;
  }
  const fmtWin=iso=>String(iso||"").replace("T"," ").slice(5,16)||"未知";
  const cov=zsxqDaily.coverage||{};
  const isWeekly=zsxqDigestMode==="weekly";
  const wk=zsxqDaily.weekly||null;
  if(isWeekly && !wk){
    if(tag) tag.textContent="待生成周报";
    if(win) win.textContent="过去 7 天滚动窗口";
    if(summary){ summary.hidden=true; summary.innerHTML=""; }
    body.innerHTML=`<div class="zdaily-empty">当前快照还是仅含 24h 日报的旧版本：在终端重跑一次 <code>python3 scripts/sync/zsxq-daily-sync.py</code> 即可同时生成 7 天周报。</div>`;
    if(foot) foot.textContent=[zsxqDaily.coverageNote||zsxqDaily.dataPolicy||"", zsxqDailyNote].filter(Boolean).join(" · ");
    return;
  }
  const items=isWeekly
    ? (Array.isArray(wk.items)?wk.items:[])
    : (Array.isArray(zsxqDaily.items)?zsxqDaily.items:[]);
  const byCat=(isWeekly?wk.byCategory:zsxqDaily.byCategory)||{};
  const winStart=isWeekly?wk.windowStart:zsxqDaily.windowStart;
  const winEnd=isWeekly?wk.windowEnd:zsxqDaily.windowEnd;
  const activeGroups=isWeekly?(wk.withItems??cov.withWeekly??0):(cov.withToday??0);
  if(tag) tag.textContent=isWeekly
    ? `7 天 ${items.length} 条 · ${activeGroups} 个星球${wk.digestedCount?` · 精华 ${wk.digestedCount}`:""}`
    : `24h ${items.length} 条 · ${activeGroups} 个星球`;
  if(win) win.textContent=`窗口 ${fmtWin(winStart)} → ${fmtWin(winEnd)}`;
  if(summary){
    const pills=Object.entries(byCat).filter(([,v])=>v>0).map(([k,v])=>`<span class="zdaily-pill">${esc(k)} ${v}</span>`);
    if(isWeekly && wk.groupCounts){
      Object.entries(wk.groupCounts).slice(0,4).forEach(([g,n])=>{
        pills.push(`<span class="zdaily-pill">${esc(g)} · ${n}</span>`);
      });
    }
    summary.innerHTML=pills.join("");
    summary.hidden=!pills.length;
  }
  const row=it=>{
    const href=safeUrl(it.url||it.mobileUrl||"");
    const title=esc(it.title||"无标题");
    const star=it.digested?`<span class="zdaily-star" title="精华">★</span>`:"";
    const link=href?`<a href="${href}" target="_blank" rel="noopener">${title}</a>`:title;
    const grp=it.group?`<span class="tag">${esc(it.group)}</span>`:"";
    const m=it.metrics||{};
    const statParts=[];
    if(isWeekly && it.publishedAt) statParts.push(fmtWin(it.publishedAt).slice(0,5));
    if(m.reads!=null) statParts.push(`阅 ${m.reads}`);
    if(m.likes) statParts.push(`赞 ${m.likes}`);
    if(m.comments) statParts.push(`评 ${m.comments}`);
    const meta=statParts.length?`<span class="zdaily-meta">${esc(statParts.join(" · "))}</span>`:"";
    return `<div class="zdaily-item"><span class="zdaily-cat">${esc(it.category||"其他")}</span><span>${star}${link} ${grp}${meta}</span></div>`;
  };
  const limit=isWeekly?6:5;
  if(!items.length){
    body.innerHTML=`<div class="zdaily-empty">该${isWeekly?" 7 天":" 24h "}窗口内可读星球没有新主题（未开通 Skill 权限的星球不计入「没更新」）。</div>`;
  }else{
    // 周报优先把精华与高互动精选放前排，其余按时间折进展开区
    const ordered=isWeekly && Array.isArray(wk.highlights) && wk.highlights.length
      ? [...wk.highlights, ...items.filter(it=>!wk.highlights.some(h=>h.id===it.id))]
      : items;
    const top=ordered.slice(0,limit).map(row).join("");
    const rest=ordered.slice(limit);
    const more=rest.length?`<details class="zdaily-more"><summary>展开其余 ${rest.length} 条</summary>${rest.map(row).join("")}</details>`:"";
    body.innerHTML=top+more;
  }
  if(foot) foot.textContent=[zsxqDaily.coverageNote||zsxqDaily.dataPolicy||"", zsxqDailyNote].filter(Boolean).join(" · ");
}

function renderZsxqScreen(){
  renderZsxqDaily();
  const tag=$("zsxqScreenTag"), note=$("zsxqScreenNote"), statsBox=$("zsxqScreenStats");
  if(!statsBox) return;
  const st=zsxqStats(social);
  if(!st){
    if(tag) tag.textContent="未接入";
    statsBox.innerHTML=""; 
    if(note) note.textContent="social.json 里没有知识星球账号：先跑 python3 scripts/sync/social-sync.py。";
    $("zsxqTimeline").innerHTML="";
    $("zsxqGroups").innerHTML="";
    return;
  }
  const scopeHint=st.scopeGroup?"该星球":"我的星球";
  if(tag) tag.textContent=st.scopeGroup
    ? `「${st.scopeGroup}」· 截至 ${st.asOf||"未知"}`
    : `我的 ${st.groups} 个星球 · 截至 ${st.asOf||"未知"}`;
  zsxqMemberSelect(st,"zsxqScreenMemberSel");
  statsBox.innerHTML=[
    zsxqFigure("成员总数", st.memberTotal||null, st.memberGroup?`仅 ${st.memberGroup}`:"指定星球"),
    zsxqFigure("话题总数", st.posts, "全部星球已抓取"),
    zsxqFigure("阅读总量", st.readSum, st.readSum==null?"缺指标":`${scopeHint} · 累计`),
    zsxqFigure("篇均阅读", st.avgRead, `${scopeHint} · 按有阅读数的条目`),
    zsxqFigure("互动率", st.interact==null?null:st.interact.toFixed(1)+"%", `${scopeHint} ·（赞+评）/阅读`),
    zsxqFigure("近 30 天发帖", st.last30, `${scopeHint} · 按发布时间`)
  ].join("");
  if(note) note.textContent=zsxqScopeNote(st);

  const allGroups=zsxqGroupsOf(social);
  const groups=allGroups.filter(g=>g.posts>0);
  const emptyGroups=allGroups.filter(g=>!(g.posts>0));
  const gids=((st.acc.metrics||{}).groupIds)||{};
  const groupCard=(g)=>{
    const dash=g.dashboard&&g.dashboard.status==="ok"?`昨日活跃 ${g.dashboard.yesterdayActive??"—"} · 30 日均 ${g.dashboard.avg30Active??"—"}`:"官方无大盘";
    const meta=`<div class="zsxq-group-meta">${g.posts} 条 · ${g.members==null?"成员未知":g.members+" 人"} · ${esc(dash)}</div>`;
    const mineBadge=g.mine?` <span class="zsxq-mine-badge">我的</span>`:"";
    const name=`<div class="zsxq-group-name">${esc(g.name)}${mineBadge}</div>`;
    const gid=gids[g.name];
    // 有 group_id 才能拼出星球首页；没有就保持纯文本，不乱造链接
    return gid
      ? `<a class="zsxq-group zsxq-group-link" href="https://wx.zsxq.com/group/${encodeURIComponent(gid)}" target="_blank" rel="noopener">${name}${meta}</a>`
      : `<div class="zsxq-group">${name}${meta}</div>`;
  };
  const GROUP_PREVIEW=12;  // 全量后有两百多个星球，默认只铺前 12 个
  const groupHtml=groups.length
    ? groups.slice(0,GROUP_PREVIEW).map(groupCard).join("")+
      (groups.length>GROUP_PREVIEW?`<details class="zsxq-more-groups"><summary>展开其余 ${groups.length-GROUP_PREVIEW} 个星球</summary>${groups.slice(GROUP_PREVIEW).map(groupCard).join("")}</details>`:"")+
      (emptyGroups.length?`<details class="zsxq-more-groups"><summary>另有 ${emptyGroups.length} 个星球没有内容</summary><div class="zsxq-emptygroups">${emptyGroups.map(g=>esc(g.name)).join("、")}</div></details>`:"")
    : `<div class="no-note">没有星球数据。</div>`;
  $("zsxqGroups").innerHTML=groupHtml;

  const rows=zsxqTimelineRows(social);
  // 每页条数决定页数；「全部」时只有一页。筛选后页数变少，当前页要夹回有效范围。
  const perPage=zsxqShown>=1e9?(rows.length||1):zsxqShown;
  const pages=Math.max(1,Math.ceil(rows.length/perPage));
  if(zsxqPageIndex>pages) zsxqPageIndex=pages;
  const start=(zsxqPageIndex-1)*perPage;
  const shown=rows.slice(start,start+perPage);
  const perText=zsxqShown>=1e9?"全部":`${zsxqShown} 条`;
  if($("zsxqTimelineTag")) $("zsxqTimelineTag").textContent=
    `第 ${zsxqPageIndex} / ${pages} 页 · 每页 ${perText} · 共 ${rows.length} 条`;
  const navBtns=(typeof document!=="undefined"&&document.querySelectorAll)
    ? document.querySelectorAll(".zsxq-page-btn") : [];
  navBtns.forEach((btn,i)=>{btn.disabled=i===0?zsxqPageIndex<=1:zsxqPageIndex>=pages;});
  // 排序按钮的选中态：之前只改了数据，按钮一直是「最新」高亮
  const sortNew=$("zsxqSortNew"), sortReads=$("zsxqSortReads");
  if(sortNew&&sortReads&&sortNew.classList){
    sortNew.classList.toggle("on", zsxqSort!=="reads");
    sortReads.classList.toggle("on", zsxqSort==="reads");
  }

  // 分页按钮的选中态跟着当前上限走
  // 测试环境没有 document；浏览器里才同步按钮态
  const pageBtns=(typeof document!=="undefined"&&document.querySelectorAll)
    ? document.querySelectorAll("#zsxqPageSeg .segbtn") : [];
  pageBtns.forEach(btn=>{
    const want=Number(btn.dataset.page);
    const on=(want===0)?(zsxqShown>=1e9):(zsxqShown===want);
    btn.classList.toggle("on",on);
  });

  // 时间范围按钮态
  const rangeBtns=(typeof document!=="undefined"&&document.querySelectorAll)
    ? document.querySelectorAll("#zsxqRangeSeg .segbtn") : [];
  rangeBtns.forEach(btn=>btn.classList.toggle("on",Number(btn.dataset.range)===zsxqRange));

  // 星球筛选下拉：只列时间线里真有内容的星球，选中态跟着 filter 走
  const groupSel=$("zsxqGroupSel");
  if(groupSel){
    const names=zsxqGroupsInTimeline(social);
    groupSel.innerHTML=`<option value="">全部星球</option>`+
      names.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join("");
    groupSel.value=zsxqGroupFilter;
  }
  // 清单排序按钮态
  const sortBtns=(typeof document!=="undefined"&&document.querySelectorAll)
    ? document.querySelectorAll("#zsxqGroupSort .segbtn") : [];
  sortBtns.forEach(btn=>btn.classList.toggle("on",btn.dataset.sort===zsxqGroupSort));

  $("zsxqTimeline").innerHTML=rows.length?shown.map(p=>{
    const m=p.metrics||{};
    const when=(p.publishedAt||"").slice(0,10);
    const title=esc(p.title||"无标题");
    // 摘要与标题常以同一句开头，去掉重复部分，避免两行说同一件事
    let bodyText=(p.excerpt||"");
    if(p.title && bodyText.startsWith(p.title)) bodyText=bodyText.slice(p.title.length).replace(/^[。！？，、\s]+/,"");
    const excerpt=esc(bodyText.slice(0,140));
    const tid=zsxqTopicId(p);
    const open=(zsxqOpen===tid);
    const det=zsxqDetail[tid];
    const fullBlock=!open?"":(det&&det.text
      ? `<div class="zsxq-full">${esc(det.text)}</div>`
      : `<div class="zsxq-full zsxq-loading">${det&&det.error?("读取失败："+esc(det.error)):"读取全文…"}</div>`);
    const tags=Array.isArray(p.tags)?p.tags:[];
    // 标签放到正文下方当轻量元信息（原在正文上方像标题），保留 # 便于识别。
    const tagRow=tags.length?`<div class="zsxq-tl-tags"><span class="zsxq-tl-tags-label">标签</span>`+
      tags.map(t=>`<span class="zsxq-tl-tag">#${esc(t)}</span>`).join("")+`</div>`:"";
    // 星球标识：每条都标来源；自己的星球额外挂「我的」角标，和清单里的角标一致。
    const mine=p.mine===true;
    const groupMark=`<span class="zsxq-tl-group">${esc(p.group||"未知星球")}`+
      (mine?`<span class="zsxq-mine-badge">我的</span>`:"")+`</span>`;
    // 自己的星球：内容多为无标题短文，标题那条是正文首句，套成标题会重复且别扭。
    // 只有 mine（owner=凌一）走短文卡片；加入的别人星球保持原来的标题+摘要。
    const inner=mine
      ? `<div class="zsxq-tl-head zsxq-tl-head-note"><span class="zsxq-tl-date">${esc(when)}</span>`+
        groupMark+`</div>`+
        `<div class="zsxq-tl-note">${title}${excerpt?`<br>${excerpt}`:""}</div>`+
        tagRow+
        `<div class="zsxq-tl-meta">阅读 ${m.reads??"—"} · 赞 ${m.likes??"—"} · 评 ${m.comments??"—"}`+
        (tid?` · <button class="zsxq-expand" type="button" onclick="event.preventDefault();event.stopPropagation();zsxqToggle('${esc(tid)}')">${open?"收起":"展开全文"}</button>`:"")+
        `</div>`+fullBlock
      : `<div class="zsxq-tl-head"><span class="zsxq-tl-date">${esc(when)}</span>`+
        groupMark+`<span class="zsxq-tl-title">${title}</span></div>`+
        (excerpt?`<div class="zsxq-tl-excerpt">${excerpt}</div>`:"")+
        `<div class="zsxq-tl-meta">阅读 ${m.reads??"—"} · 赞 ${m.likes??"—"} · 评 ${m.comments??"—"}`+
        (tid?` · <button class="zsxq-expand" type="button" onclick="event.preventDefault();event.stopPropagation();zsxqToggle('${esc(tid)}')">${open?"收起":"展开全文"}</button>`:"")+
        `</div>`+fullBlock;
    return p.url?`<a class="zsxq-tl-row" href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener">${inner}</a>`
                :`<div class="zsxq-tl-row">${inner}</div>`;
  }).join(""):`<div class="no-note">${zsxqQuery?"没有匹配的话题。":"没有话题数据。"}</div>`;
}

function renderZsxqPanel(social){
  const box=$("zsxqStats"), note=$("zsxqNote"), top=$("zsxqTop"), tag=$("zsxqTag");
  if(!box) return;
  const st=zsxqStats(social);
  if(!st){
    if(tag) tag.textContent="未接入";
    box.innerHTML="";
    top.innerHTML="";
    note.textContent="social.json 里没有知识星球账号：先跑 python3 scripts/sync/social-sync.py。";
    return;
  }
  const scopeHint=st.scopeGroup?"该星球":"我的星球";
  if(tag) tag.textContent=st.scopeGroup
    ? `「${st.scopeGroup}」· 截至 ${st.asOf||"未知"}`
    : `我的 ${st.groups} 个星球 · 截至 ${st.asOf||"未知"}`;
  zsxqMemberSelect(st,"zsxqMemberSel");
  box.innerHTML=[
    zsxqFigure("成员总数", st.memberTotal||null, st.memberGroup?`仅 ${st.memberGroup}`:"指定星球"),
    zsxqFigure("话题总数", st.posts, "全部星球已抓取"),
    zsxqFigure("阅读总量", st.readSum, st.readSum==null?"缺指标":`${scopeHint} · 累计`),
    zsxqFigure("篇均阅读", st.avgRead, `${scopeHint} · 按有阅读数的条目`),
    zsxqFigure("互动率", st.interact==null?null:st.interact.toFixed(1)+"%", `${scopeHint} ·（赞+评）/阅读`),
    zsxqFigure("近 30 天发帖", st.last30, `${scopeHint} · 按发布时间`)
  ].join("");
  note.textContent=zsxqScopeNote(st);
  top.innerHTML=st.top.length
    ? `<div class="zsxq-top-head">阅读最高的 ${st.top.length} 条 <span class="zsxq-top-scope">全部星球</span></div>`+
      st.top.map((p,i)=>{
        const reads=(p.metrics||{}).reads;
        const title=esc(p.title||"无标题");
        const inner=`<span class="zsxq-rank">${i+1}</span><span class="zsxq-title">${title}</span>`+
          `<span class="zsxq-reads">${reads==null?"—":esc(String(reads))}</span>`;
        return p.url?`<a class="zsxq-row" href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener">${inner}</a>`
                    :`<div class="zsxq-row">${inner}</div>`;
      }).join("")
    : `<div class="no-note">这批话题没有阅读指标，不做排行。</div>`;
}

function renderMediaAccounts(accounts){
  const freshRank={missing:0,stale:1,aging:2,fresh:3};
  const rank=a=>a.connectionState==="blocked"?-2:a.connectionState==="limited"?-1:
    (freshRank[mediaEffectiveFreshness(a).state]??0);
  return (accounts||[]).slice().sort((a,b)=>rank(a)-rank(b)).map(a=>{
    const s=mediaAccountSummary(a), freshness=mediaEffectiveFreshness(a);
    const fresh=[freshness.label,freshness.tone];
    const subs=s.sub.filter(x=>x[1]!==null&&x[1]!==undefined).map(x=>`<span>${esc(x[0])} ${esc(x[1])}</span>`).join("");
    const dashboard=outLink(a.dashboardUrl,"数据后台");
    const issue=a.blockReason||(a.connectionState==="limited"?a.note:"")||
      (freshness.state==="stale"?`数据截至 ${a.asOf||"未知日期"}，已过期 ${freshness.days} 天。`: "");
    const cls=["blocked","limited"].includes(a.connectionState)?"is-blocked":freshness.state==="stale"?"is-stale":"";
    const sync=a.sync||{};
    const syncShort=sync.status==="updated"?"本轮已更新":sync.status==="history"?"本轮沿用历史":sync.status==="not_connected"?"本轮未接入":sync.status==="blocked"?"本轮被拦截":sync.status==="partial"?"本轮部分更新":"";
    const freshnessShort=["blocked","limited"].includes(a.connectionState)
      ? (a.platform==="公众号"?"统计接口未授权 · 素材已接入":(a.platform==="X"?"官方数据未接入":"需要更新数据"))
      : freshness.state==="stale" ? `${a.platform==="知识星球"?"历史快照":"数据过期"} · ${freshness.days} 天未更新`
      : freshness.state==="aging" ? `数据待更新 · ${freshness.days} 天前` : "";
    const issueShort=[syncShort,freshnessShort].filter(Boolean).join(" · ");
    const issueLong=issue?`<details class="media-account-more"><summary>为什么数据受限</summary><div>${esc(issue)}</div></details>`:"";
    const target=SOCIAL_PLATFORM_TARGET[a.platform]||"";
    const syncBtn=(SERVED&&!guest&&target)
      ? `<button class="media-sync-btn" type="button" data-wb-action="social-sync" data-wb-arg="${esc(target)}" title="仅刷新 ${esc(a.platform)}">快刷</button>`
      : "";
    return `<article class="media-account ${cls}"><div class="media-account-head"><span class="media-account-name">${esc(a.platform)}</span>`+
      `<span class="chip ${fresh[1]}">${fresh[0]}</span>${syncBtn}</div><div class="media-account-value">${esc(s.value)}</div>`+
      `<div class="media-account-label">${esc(s.label)}</div><div class="media-account-sub">${subs}</div>`+
      (issueShort?`<div class="media-account-issue">${esc(issueShort)}</div>`:"")+
      (issue?issueLong:"")+
      `<div class="media-account-foot"><span>${a.asOf?esc(mediaFreshnessCopy(a,freshness)):"等待首次导入"}</span>${dashboard}</div></article>`;
  }).join("")||secEmpty("还没有账号数据源");
}
/* 近 7 天只按“发布日期”归集真实明细。阅读/互动是当前快照里的累计值，
   不是每日新增流量；缺失指标保持未知，绝不补 0。 */
function mediaTrendData(days=7, source=social, now=new Date()){
  const weekdays=["日","一","二","三","四","五","六"];
  const dates=[];
  for(let i=days-1;i>=0;i--){
    const d=new Date(now.getTime()-i*86400000);
    dates.push({key:focusDateStr(d),date:d,count:0,knownReads:0,knownPosts:0,platforms:{}});
  }
  const byDate=Object.fromEntries(dates.map(d=>[d.key,d]));
  (source&&source.posts||[]).forEach(p=>{
    const parsed=new Date(p.publishedAt||"");
    if(isNaN(parsed.getTime())) return;
    const d=byDate[focusDateStr(parsed)];
    if(!d) return;
    d.count++;
    d.platforms[p.platform]=(d.platforms[p.platform]||0)+1;
    const reads=(p.metrics||{}).reads;
    if(typeof reads==="number" && Number.isFinite(reads)){
      d.knownReads+=reads;
      d.knownPosts++;
    }
  });
  dates.forEach(d=>{
    d.day=String(d.date.getDate()).padStart(2,"0");
    d.month=String(d.date.getMonth()+1).padStart(2,"0");
    d.weekday=weekdays[d.date.getDay()];
  });
  const platformTotals={};
  dates.forEach(d=>Object.entries(d.platforms).forEach(([k,v])=>{platformTotals[k]=(platformTotals[k]||0)+v;}));
  return {
    days:dates,
    totalPosts:dates.reduce((n,d)=>n+d.count,0),
    knownReads:dates.reduce((n,d)=>n+d.knownReads,0),
    knownPosts:dates.reduce((n,d)=>n+d.knownPosts,0),
    platforms:Object.entries(platformTotals).sort((a,b)=>b[1]-a[1])
  };
}
function renderMediaTrend(source=social){
  const trend=mediaTrendData(7,source);
  const max=Math.max(1,...trend.days.map(d=>d.count));
  const platformClass=Object.fromEntries(trend.platforms.map(([name],i)=>[name,"s"+Math.min(i,2)]));
  const legend=trend.platforms.map(([name,n],i)=>
    `<span><i class="s${Math.min(i,2)}"></i>${esc(name)} ${n}</span>`).join("");
  const cols=trend.days.map((d,i)=>{
    const h=d.count?Math.max(10,Math.round(d.count/max*100)):0;
    const segs=Object.entries(d.platforms).sort((a,b)=>b[1]-a[1]).map(([name,n])=>
      `<i class="media-trend-seg ${platformClass[name]||""}" style="flex:${n}" title="${esc(name)} ${n} 条"></i>`).join("");
    const read=d.count?(d.knownPosts?`${d.knownReads} 阅`:"阅未核验"):"无样本";
    return `<div class="media-trend-day${d.count?"":" no-post"}" title="${d.month}/${d.day} · ${d.count} 条发布 · ${read}">`+
      `<div class="media-trend-bar"><span class="media-trend-stack" style="height:${h}%">${segs}</span></div>`+
      `<span class="media-trend-date">${i===trend.days.length-1?"今天":d.month+"/"+d.day}</span>`+
      `<span class="media-trend-value">${d.count} 条</span><span class="media-trend-read">${read}</span></div>`;
  }).join("");
  const activePlatforms=trend.platforms.length;
  const trendBrief=$("mediaTrendBrief");
  if(trendBrief) trendBrief.textContent=`${trend.totalPosts} 条发布 · ${activePlatforms} 个活跃平台`;
  return `<div class="media-trend-head"><div><h3>近 7 天发布趋势</h3>`+
      `<p>按内容发布日期归集；阅读为当前快照的累计值，不代表每日新增流量。</p></div>`+
      `<div class="media-trend-summary"><div><b>${trend.totalPosts}</b><span>7 天发布</span></div>`+
      `<div><b>${activePlatforms}</b><span>活跃平台</span></div>`+
      `<div><b>${trend.knownPosts?trend.knownReads.toLocaleString("zh-CN"):"—"}</b><span>已知累计阅读</span></div></div></div>`+
    `<div class="media-trend-chart" role="img" aria-label="近 7 天共发布 ${trend.totalPosts} 条，${activePlatforms} 个平台有可核验明细">${cols}</div>`+
    `<div class="media-trend-foot"><div class="media-trend-legend">${legend||"近 7 天没有可核验的发布明细"}</div>`+
      `<span>空心日为无发布；阅读数未知时显示「阅未核验」，不计作 0。</span></div>`;
}
function renderKnowledgeBase(kb){
  if(!kb||!kb.available) return secEmpty("没有检测到本地知识库");
  const items=kb.items||[];
  const row=n=>{
    const u=safeObsidianUrl(n.obsidianUrl), title=u?`<a href="${esc(u)}">${esc(n.title)}</a>`:esc(n.title);
    return `<article class="knowledge-item"><div>${title}</div><p>${esc(n.snippet||"")}</p>`+
      `<div class="knowledge-meta">${(n.topics||[]).map(t=>`<span class="chip">${esc(t)}</span>`).join("")}`+
      `<span>${esc((n.updatedAt||"").slice(0,10))}</span></div></article>`;
  };
  return items.length?(items.slice(0,3).map(row).join("")+
    (items.length>3?`<details class="completed-group"><summary>更多相关素材 ${items.length-3} 条<span>继续浏览</span></summary>`+
      `<div class="completed-list">${items.slice(3).map(row).join("")}</div></details>`:"")):
    secEmpty("知识库里暂未匹配到自媒体相关内容");
}
function renderWebPresence(w){
  if(!w) return secEmpty(webPresenceErr||"没有网站监控数据");
  const states={ok:["正常","ok"],checking:["复检中","warn"],slow:["偏慢","warn"],down:["异常","dead"]};
  const summary=webPresenceStatus(w);
  const siteRow=s=>{const meta=states[s.state]||states.down, latency=s.latencyMs==null?"未测得":s.latencyMs+" ms";
    return `<div class="media-site-row"><strong>${esc(s.name)}</strong><span class="site-meta">${esc(latency)}${s.status?" · HTTP "+esc(s.status):""}</span>`+
      `<span>${outLink(s.finalUrl||s.url,meta[0],"chip "+meta[1])}</span></div>`;};
  const sites=w.sites||[], active=sites.filter(s=>s.state!=="ok"), settled=sites.filter(s=>s.state==="ok");
  const checks=(active.length?active.map(siteRow).join(""):secClear("当前没有站点异常"))+
    (settled.length?`<details class="completed-group"><summary>运行正常 ${settled.length} 个站点<span>收起展示</span></summary><div class="completed-list">${settled.map(siteRow).join("")}</div></details>`:"");
  const blog=w.blog||{}, latest=blog.latest||{}, latestUrl=safeUrl(latest.url), title=latestUrl?`<a href="${esc(latestUrl)}" target="_blank" rel="noopener noreferrer">${esc(latest.title||"最新文章")}</a>`:`<strong>${esc(latest.title||"尚未读到文章")}</strong>`;
  const newBadge=blog.newSinceLastCheck?`<span class="chip ok">检测到更新</span>`:"";
  const analytics=w.analytics||{};
  const vis=w.visitors||null;
  const visBlock=vis?`<div class="media-visitor-row"><div class="mv-item"><b>${esc(String(vis.latest.pageviews??"—"))}</b><span>昨日浏览（${esc(vis.asOf)}）</span></div>`+
    `<div class="mv-item"><b>${esc(String(vis.last7Pageviews??"—"))}</b><span>近 7 天浏览</span></div>`+
    `<div class="mv-item"><b>${esc(String(vis.latest.visitors??"—"))}</b><span>昨日访客</span></div>`+
    `<div class="mv-item"><b>${esc(String(vis.latest.topPage||"—"))}</b><span>热门页面</span></div></div>`+
    `<div class="media-traffic-note"><b>访问数据：</b>${esc(vis.source||"")}。数字为每日 09:00 汇总，当天数据次日更新。</div>`:
    `<div class="media-traffic-note"><b>访问数据：</b>${esc(analytics.cadenceLabel||"未接入")}。${esc(analytics.note||"")}</div>`;
  const searchBlock=renderSearchTraffic(w.search);
  return `<div><div class="media-web-alert"><div class="wt">站点状态 <span class="chip ${summary.tone}">${esc(summary.label)}</span></div>`+
    `<div class="wd">${esc(w.dataPolicy||"")}${webPresenceNote?" · "+esc(webPresenceNote):""}</div></div>${checks}</div>`+
    `<div><div class="media-blog-latest"><div class="eyebrow">博客最新更新 ${newBadge}</div>${title}<p>${latest.publishedAt?"发布 "+esc(String(latest.publishedAt).slice(0,16).replace("T"," ")):"RSS 暂未返回发布日期"}${blog.usingPrevious?" · 当前沿用上一次成功结果":""}</p></div>`+
    visBlock+searchBlock+`</div>`;
}
function renderSearchTraffic(s){
  if(!s) return "";
  const sites=s.sites||[];
  if(s.notConfigured&&!sites.length)
    return `<div class="media-traffic-note"><b>搜索流量：</b>未接入。Google Cloud 建服务账号并把密钥存为 ~/.config/daily-workbench/gsc-service-account.json，再到两个站点的 GSC 里给服务账号加查看权限。</div>`;
  if(s.error&&!sites.length)
    return `<div class="media-traffic-note"><b>搜索流量：</b>${esc(s.error)}</div>`;
  const dChip=v=>(v==null||typeof v!=="number")?"":` <span class="chip ${v<0?"warn":"ok"}">${v<0?"▼":"▲"}${esc(Math.abs(v*100).toFixed(0))}%</span>`;
  const siteSum=site=>`${esc(site.name||site.id||"")} ${esc(String(site.clicks??"—"))} 点击 · ${esc(String(site.impressions??"—"))} 曝光`;
  const summary=sites.filter(site=>site&&!site.error).map(siteSum).join(" · ")||"暂无站点数据";
  const rows=sites.map(site=>{
    if(!site||site.error) return `<div class="media-site-row"><strong>${esc((site&&site.name)||"")}</strong><span class="site-meta">${esc((site&&site.error)||"")}</span></div>`;
    const qs=(site.topQueries||[]).slice(0,5).map(q=>
      `<div class="media-site-row"><span>${esc(q.q||"")}</span><span class="site-meta">${esc(String(q.clicks??"—"))} 点击 · 排名 ${esc(String(q.position??"—"))}</span></div>`).join("");
    const per=site.period?`${esc(site.period.start||"")} ~ ${esc(site.period.end||"")}`:"";
    return `<div class="media-web-alert"><div class="wt">${esc(site.name||site.id||"")}${dChip(site.deltaClicks)}<div class="wd">${per} · 较上期</div></div></div>`+
      `<div class="media-visitor-row">`+
      `<div class="mv-item"><b>${esc(String(site.clicks??"—"))}</b><span>点击</span></div>`+
      `<div class="mv-item"><b>${esc(String(site.impressions??"—"))}</b><span>曝光</span></div>`+
      `<div class="mv-item"><b>${esc(site.ctr!=null?(site.ctr*100).toFixed(1)+"%":"—")}</b><span>点击率</span></div>`+
      `<div class="mv-item"><b>${esc(String(site.position??"—"))}</b><span>平均排名</span></div></div>`+
      (qs?`<details class="completed-group"><summary>热门搜索词<span>展开</span></summary><div class="completed-list">${qs}</div></details>`:"");
  }).join("");
  const note=s.error?`<div class="media-traffic-note">${esc(s.error)}</div>`
    :`<div class="media-traffic-note"><b>搜索流量：</b>每日更新（GSC），${esc(String(s.generatedAt||"").slice(0,16).replace("T"," "))} 生成。</div>`;
  /* 右栏长内容默认折叠：摘要行回答"有没有量"，四格指标和热门词按需展开 */
  return `<details class="completed-group web-search-details"><summary>搜索流量<span class="ws-summary">${summary}</span><span>展开</span></summary>`+
    `<div class="completed-list">${rows}</div>${note}</details>`;
}
/* 内容表现 Top3/Bottom3：与 social.json 的 weeklyRecap 同一套互动分口径
   （阅读 + 赞×3 + 评×5），阅读未核验（null）的条目不参与排名，不编数据。
   原因用规则从数据里挑最大差异点说一句话；Jev 开着时给主题判断，两者可共存。 */
function perfScore(p){
  const m=p.metrics||{};
  if(m.reads==null||typeof m.reads!=="number") return null;
  return (m.reads||0)+(m.likes||0)*3+(m.comments||0)*5;
}
function perfReason(p,median,mean){
  const m=p.metrics||{}, bits=[];
  const reads=m.reads||0, inter=(m.likes||0)+(m.comments||0);
  const interRate=reads?inter/reads:0;
  if(mean&&reads>mean*3) bits.push(`阅读 ${reads}，是样本均值的 ${Math.round(reads/mean)} 倍`);
  else if(mean&&reads<mean/5) bits.push(`仅 ${reads} 次阅读，远低于样本均值 ${Math.round(mean)}`);
  else if(median!=null&&reads>median) bits.push(`阅读 ${reads}，高于中位数 ${median}`);
  if(interRate>0.05) bits.push("互动率突出（≥5%）");
  else if(reads>=50&&interRate<0.005) bits.push("看完的人多但互动少，缺一个引导");
  if((m.shares||0)>0) bits.push(`有 ${m.shares} 次转发`);
  if(!bits.length) bits.push(reads>=50?`阅读 ${reads}，处于样本中游`:`样本量小（阅读 ${reads}），仅供参考`);
  return bits.slice(0,2).join("；");
}
function renderMediaPerf(social){
  const pool=(social.posts||[]).map(p=>({p,score:perfScore(p)})).filter(x=>x.score!=null);
  const brief=$("mediaPerfBrief");
  if(pool.length<4){
    if(brief) brief.textContent="样本不足 · 暂不排名";
    return secEmpty("阅读数据可核验的条目不足 4 条，暂不做 Top/Bottom 排名");
  }
  const reads=pool.map(x=>x.p.metrics.reads).sort((a,b)=>a-b);
  const median=reads[Math.floor(reads.length/2)];
  const mean=reads.reduce((a,b)=>a+b,0)/reads.length;
  const rank=pool.slice().sort((a,b)=>b.score-a.score);
  if(brief) brief.textContent=`${pool.length} 条可核验 · Top / Bottom 各 3`;
  const jevByUrl={}; if(JEV.enabled) (JEV.lastAnalysis||[]).forEach(r=>{ if(r.url) jevByUrl[r.url]=r; });
  const item=(x,i,good)=>{
    const p=x.p,u=safeUrl(p.url);
    const title=u?`<a class="media-perf-title" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a>`:`<span class="media-perf-title">${esc(p.title)}</span>`;
    const jr=jevByUrl[u];
    const why=perfReason(p,median,mean);
    return `<div class="media-perf-item"><span class="media-perf-rank">${i+1}</span>`+
      title+
      `<div class="media-perf-why">${esc(why)}</div></div>`;
  };
  return `<div class="media-perf-col"><h4>表现最好 · Top 3</h4>${rank.slice(0,3).map((x,i)=>item(x,i,true)).join("")}</div>`+
    `<div class="media-perf-col"><h4>需要复盘 · Bottom 3</h4>${rank.slice(-3).reverse().map((x,i)=>item(x,i,false)).join("")}</div>`+
    renderCompletionBoard(social);
}
/* 完读率排行榜：只统计 mpdt 渠道页明细（发表后 30 天窗口，微信口径）。
   阅读人数 <5 的样本按 mpdt 报告的同一规则排除（1 人读完=100% 无统计意义）。 */
function renderCompletionBoard(social){
  const rows=(social.posts||[]).filter(p=>p.platform==="公众号"&&p.metrics&&
    typeof p.metrics.completionRate==="number"&&typeof p.metrics.readUsers==="number"&&p.metrics.readUsers>=5)
    .sort((a,b)=>b.metrics.completionRate-a.metrics.completionRate);
  if(!rows.length) return `<div class="media-perf-col media-completion-col"><h4>完读率排行</h4>`+
    `<div class="media-perf-why">还没有窗口内明细：完读率只对发表后 30 天内的文章有效（微信口径），发文后同步自动出现。</div></div>`;
  const item=(p,i)=>{
    const u=safeUrl(p.url);
    const title=u?`<a class="media-perf-title" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a>`:`<span class="media-perf-title">${esc(p.title)}</span>`;
    const c=p.metrics.completionRate, secs=p.metrics.avgReadSeconds;
    const why=`${p.metrics.readUsers} 人阅读 · 平均 ${Math.round(secs||0)} 秒${c>=0.5?" · 读完过半，标题与开头留住了人":c<0.2?" · 早早流失，检查开头是否切题":""}`;
    return `<div class="media-perf-item"><span class="media-perf-rank">${i+1}</span>`+
      title+
      `<div class="media-perf-why">${esc(why)}</div>`+
      `<div class="media-completion-bar"><i style="width:${Math.round(Math.min(1,c)*100)}%"></i></div></div>`;
  };
  return `<div class="media-perf-col media-completion-col"><h4>完读率排行 · 最近 30 天发文</h4>${rows.slice(0,5).map(item).join("")}</div>`;
}
