/* ═══════════════════════════════════════════════════════════════════════════
   微信页。数据来自 /wechat.json（scripts/sync/wechat-sync.py 生成的只读快照）。
   口径三条，写死在这里，不在别处再算一遍：
   1. 「待回复」= 私聊里最后一条是对方发的，且落在窗口内。MCP 不给未读数，
      所以这里也不编一个未读数 —— 只报"最后一条不是我发的"这个事实。
   2. 「群里 @ 我」只认正文里带 @昵称 的消息，且同群只留最近一条。
   3. 公众号/系统号不进待回复：它们的最后一条永远"不是我发的"，
      但没人会回携程的促销。
   ═══════════════════════════════════════════════════════════════════════════ */
function wxHoursLabel(h){
  const n=Math.max(0,Number(h)||0);
  if(n<1) return "刚刚";
  if(n<24) return Math.round(n)+" 小时前";
  const d=Math.floor(n/24);
  return d+" 天前";
}
function wxWhen(iso){
  if(!iso) return "时间未知";
  const t=new Date(iso);
  if(isNaN(t)) return "时间未知";
  const now=new Date();
  const sameYear=t.getFullYear()===now.getFullYear();
  const md=(t.getMonth()+1)+"/"+t.getDate();
  return (sameYear?md:t.getFullYear()+"-"+md)+" "+
    String(t.getHours()).padStart(2,"0")+":"+String(t.getMinutes()).padStart(2,"0");
}
/* 待办行。三行：谁 + 多久了 / 对方要什么 / 我该做什么。
   为什么不像"待回复"那样只显示预览：预览是对方的话，待办要回答的是"我要干嘛"。
   原文收进 <details>，因为模型的转述可能错，人得能一键核对。 */
function wxTodoRow(item){
  const hours=Number(item.hoursAgo)||0;
  const urgent=hours>=24;
  const muted=wxMuted().includes(item.conversationId);
  const raw=[item.preview, item.transcript?("语音转写：" + item.transcript):""]
    .filter(Boolean).join("\n\n");
  const analyzeNote=item.analyzed===false
    ? `<div class="wx-todo-note">这条没能解析（本地模型未就绪），下面是原文。</div>` : "";
  const asrNote=item.asrFailed
    ? `<div class="wx-todo-note">语音未能转写，只能看到"[语音]"。要听原声请在微信里打开。</div>` : "";
  return `<div class="wx-todo${urgent?" wx-todo-urgent":""}">`+
    `<div class="wx-todo-head">`+
      `<span class="wx-kind">${item.kind==="group"?"群":"私聊"}</span>`+
      `<b>${esc(item.name)}</b>`+
      `<span class="wx-todo-age${urgent?" bad":""}">${esc(wxHoursLabel(hours))}</span>`+
      `<button class="wx-mute" type="button" onclick="wxToggleMute('${esc(item.conversationId)}')" `+
        `aria-label="${muted?"恢复":"忽略"} ${esc(item.name)}">${muted?"恢复":"忽略"}</button>`+
    `</div>`+
    (item.want?`<div class="wx-todo-want"><span>对方要</span>${esc(item.want)}</div>`:analyzeNote)+
    (item.action?`<div class="wx-todo-do"><span>你要做</span>${esc(item.action)}</div>`:"")+
    asrNote+
    (raw?`<details class="wx-todo-raw"><summary>看原文</summary><div class="wx-raw-body">${esc(raw)}</div></details>`:"")+
  `</div>`;
}
function wxRow(item,opts){
  const o=opts||{};
  const kind=item.kind==="group"?"群":"私聊";
  const when=item.at?wxWhen(item.at):"";
  const bad=(o.badHours&&Number(item.hoursAgo)>=o.badHours);
  const muted=o.mutable&&wxMuted().includes(item.conversationId);
  const muteBtn=o.mutable
    ? `<button class="wx-mute" type="button" onclick="wxToggleMute('${esc(item.conversationId)}')" `+
      `aria-label="${muted?"恢复":"忽略"} ${esc(item.name)}">${muted?"恢复":"忽略"}</button>`
    : "";
  return `<div class="wx-row${o.old?" wx-old":""}">`+
    `<div class="wx-who"><span class="wx-kind">${esc(kind)}</span>`+
      `<b title="${esc(item.name)}">${esc(item.name)}</b></div>`+
    `<div class="wx-when${bad?" bad":""}">${esc(opts&&opts.whenText?opts.whenText:when)}`+
      ` ${muteBtn}</div>`+
    (item.preview?`<div class="wx-preview">${esc(item.preview)}</div>`:"")+
    (item.sender?`<div class="wx-preview" style="grid-column:1/-1;margin-top:-2px">发送者：${esc(item.sender)}</div>`:"")+
    (item.mentionBasis?`<div class="wx-preview" style="grid-column:1/-1;margin-top:-2px">${item.mentionVerified===true?"已核验群成员":"文本命中 · 待核验群成员"}</div>`:"")+
  `</div>`;
}
let wxView=localStorage.getItem("wb_wx_view")||"brief";
function setWxView(view, scroll=true){
  wxView=["brief","action","opportunity","consultation","mentions"].includes(view)?view:"brief";
  try{localStorage.setItem("wb_wx_view",wxView);}catch(e){}
  document.querySelectorAll("#wxSubnav [data-wxview]").forEach(b=>{
    const on=b.dataset.wxview===wxView;
    b.classList.toggle("on",on); b.setAttribute("aria-selected",on?"true":"false");
  });
  const panels={
    brief:["wxBriefPanel"],
    action:["wxActionPanel","wxActionSecondary","wxActivePanel"],
    opportunity:["wxOpportunityPanel"],
    consultation:["wxConsultationPanel"],
    mentions:["wxMentionPanel"]
  };
  ["wxBriefPanel","wxActionPanel","wxActionSecondary","wxActivePanel","wxOpportunityPanel","wxConsultationPanel","wxMentionPanel"].forEach(id=>{const el=$(id);if(el)el.hidden=!panels[wxView]?.includes(id);});
  if(scroll && wxView!=="brief") document.querySelector("#screenWechat")?.scrollIntoView({block:"start",behavior:"smooth"});
}
/* ── 批奏折状态机（按会话/条目 ID + 最新消息时间存本机；新消息自动重浮） ── */
const WX_TRIAGE_KEY="wb_wx_triage";
let wxBriefType="all";
let wxBriefStateFilter="pending";
let wxBriefCache=[];
let wxReplyCurrent=null;
let wxReplyTone="pro";
let wxReplyDrafts={pro:"",warm:"",short:""};

function wxTriageMap(){
  try{ return JSON.parse(localStorage.getItem(WX_TRIAGE_KEY)||"{}")||{}; }catch(e){ return {}; }
}
function wxSaveTriageMap(m){
  try{ localStorage.setItem(WX_TRIAGE_KEY, JSON.stringify(m)); }catch(e){}
}
function wxBriefEffectiveState(item){
  const rec=wxTriageMap()[item.id];
  if(!rec || !rec.state || rec.state==="pending") return {state:"pending", reopened:false};
  const curTs=item.at?Date.parse(item.at):0;
  const savedTs=rec.at?Date.parse(rec.at):0;
  // 一旦快照里的最新消息时间晚于当时标记时的时间，自动重新浮出为待批
  if(curTs && savedTs && curTs>savedTs) return {state:"pending", reopened:true};
  return {state:rec.state, reopened:false};
}
function wxSetBriefState(id, state){
  const item=wxBriefCache.find(x=>x.id===id);
  const m=wxTriageMap();
  if(state==="pending"){
    delete m[id];
  }else{
    m[id]={state, at:item?.at||"", updatedAt:new Date().toISOString()};
  }
  wxSaveTriageMap(m);
  renderWxBrief();
  const label=state==="done"?"已标为已处理（有新消息自动重浮）":(state==="read"?"已标为已阅":"已恢复为待批");
  notify(label,"info");
}
function wxSetBriefType(t){ wxBriefType=t||"all"; renderWxBrief(); }
function wxSetBriefStateFilter(s){ wxBriefStateFilter=s||"pending"; renderWxBrief(); }

function wxCollectBriefItems(snap){
  if(!snap || snap.available===false) return [];
  const muted=wxMuted();
  const isRest=x=>x.category==="广告"||x.category==="已结束";
  const out=[];
  const seen=new Set();
  const pushUnique=(obj)=>{
    if(!obj || !obj.id || seen.has(obj.id)) return;
    seen.add(obj.id);
    out.push(obj);
  };
  (snap.accounts||[]).forEach(a=>{
    const acc=a.account?.name||"";
    (a.waiting||[]).forEach(x=>{
      if(isRest(x) || muted.includes(x.conversationId)) return;
      const hours=Number(x.hoursAgo)||0;
      const raw=[x.preview, x.transcript?("语音转写："+x.transcript):""].filter(Boolean).join("\n\n");
      pushUnique({
        id:"todo:"+(x.conversationId||x.name),
        conversationId:x.conversationId||"",
        type:"todo", typeLabel:"待办",
        name:x.name||"未命名", kind:x.kind==="group"?"群":"私聊", account:acc,
        at:x.at||"", ageText:wxHoursLabel(hours), urgent:hours>=24,
        want:x.want||x.preview||"待核对原文",
        action:x.action||"打开原文确认后回复",
        prevSummary:x.prevSummary||"", latestDelta:x.latestDelta||"",
        hasProgress:!!x.hasProgress,
        suggestedReply:x.suggestedReply||"",
        raw:raw||x.preview||"",
        sortScore: (hours>=24?1000:0) + (x.hasProgress?200:0) + Math.min(hours,500)
      });
    });
    (a.opportunities||[]).forEach(x=>{
      const lvl=x.opportunityLevel||"O1";
      pushUnique({
        id:"opp:"+(x.id||x.contact),
        conversationId:x.conversationId||"",
        type:"opportunity", typeLabel:"机会 "+lvl,
        name:x.contact||"未命名", kind:x.kind==="group"?"群":"私聊", account:acc,
        at:x.at||"", ageText:wxWhen(x.at), urgent:lvl==="O3",
        want:x.evidence||x.whyRelated||"潜在业务机会",
        action:x.nextAction||"补充询问具体需求与排期",
        prevSummary:x.prevSummary||"", latestDelta:x.latestDelta||"",
        hasProgress:!!x.hasProgress,
        suggestedReply:x.suggestedReply||"",
        raw:x.sourcePreview||x.evidence||"",
        sortScore:(lvl==="O3"?900:(lvl==="O2"?450:150))
      });
    });
    (a.consultations||[]).forEach(x=>{
      pushUnique({
        id:"con:"+(x.id||x.contact),
        conversationId:x.conversationId||"",
        type:"consultation", typeLabel:"咨询",
        name:x.contact||"未命名", kind:x.kind==="group"?"群":"私聊", account:acc,
        at:x.at||"", ageText:wxWhen(x.at), urgent:false,
        want:x.surfaceQuestion||x.underlyingNeed||x.evidence||"明确咨询",
        action:x.nextAction||"结合真实需求给出建议或追问",
        prevSummary:x.prevSummary||"", latestDelta:x.latestDelta||"",
        hasProgress:!!x.hasProgress,
        suggestedReply:x.suggestedReply||"",
        raw:x.sourcePreview||x.surfaceQuestion||"",
        sortScore:320
      });
    });
    (a.mentions||[]).forEach(x=>{
      pushUnique({
        id:"men:"+(x.conversationId||x.name)+":"+(x.at||""),
        conversationId:x.conversationId||"",
        type:"mention", typeLabel:"群 @ 我",
        name:x.name||"未命名群", kind:"群", account:acc,
        at:x.at||"", ageText:wxWhen(x.at), urgent:false,
        want:(x.sender?x.sender+"：":"")+(x.want||x.preview||"群内提及你"),
        action:x.action||"查看群内上下文决定是否回应",
        prevSummary:x.prevSummary||"", latestDelta:x.latestDelta||"",
        hasProgress:!!x.hasProgress,
        suggestedReply:"",
        raw:(x.sender?"发送者："+x.sender+"\n":"")+(x.preview||""),
        sortScore:280
      });
    });
    (a.learningTopics||[]).forEach(x=>{
      pushUnique({
        id:"learn:"+(x.id||x.conversationId||x.name),
        conversationId:x.conversationId||"",
        type:"learning", typeLabel:"群选题",
        name:x.name||"白名单群", kind:"群", account:acc,
        at:x.at||"", ageText:wxWhen(x.at), urgent:false,
        want:x.summary||x.title||"群内高价值讨论",
        action:x.action||"可沉淀为星球帖或公众号选题",
        prevSummary:x.prevSummary||"", latestDelta:x.latestDelta||"",
        hasProgress:!!x.hasProgress,
        suggestedReply:"",
        canTopic:true,
        title:x.title||"",
        raw:x.sourcePreview||x.summary||"",
        sortScore:240 + (x.hasProgress?120:0)
      });
    });
  });
  out.sort((a,b)=>(b.sortScore-a.sortScore)||String(b.at).localeCompare(String(a.at)));
  return out;
}

function wxLocalDraftsFor(item){
  const base=item.suggestedReply||"";
  const act=item.action||"了解下具体情况再回复你";
  const who=item.name||"你";
  return {
    pro: base || `收到，关于「${(item.want||"").slice(0,28)}」这边我先确认一下细节，${act}，有进展第一时间同步您。`,
    warm: base ? `${base} 有什么补充随时跟我说～` : `收到啦！我这边先帮你看下，${act}，晚点给你消息～`,
    short: base ? base.slice(0,60) : `收到，${act}。`
  };
}

function wxRenderReplyTones(){
  const box=$("wxReplyTones"), ta=$("wxReplyText");
  if(!box || !ta) return;
  const tones=[["pro","专业利落"],["warm","温和体贴"],["short","简短确认"]];
  box.innerHTML=tones.map(([k,l])=>
    `<button class="wx-brief-tab${wxReplyTone===k?" on":""}" type="button" role="tab" aria-selected="${wxReplyTone===k?"true":"false"}" onclick="wxPickReplyTone('${k}')">${l}</button>`
  ).join("");
  ta.value=wxReplyDrafts[wxReplyTone]||"";
}
function wxPickReplyTone(t){
  wxReplyTone=["pro","warm","short"].includes(t)?t:"pro";
  wxRenderReplyTones();
}
async function wxOpenReplyModal(id){
  const item=wxBriefCache.find(x=>x.id===id);
  if(!item) return;
  wxReplyCurrent=item;
  wxReplyTone="pro";
  wxReplyDrafts=wxLocalDraftsFor(item);
  const title=$("wxReplyTitle"), status=$("wxReplyStatus");
  if(title) title.textContent="回应草稿 · "+item.name;
  if(status) status.textContent="只读边界 · 不写微信";
  wxRenderReplyTones();
  openOv("wxReplyOv");
  // 在线且非访客时，尝试用本地模型按三口吻润色（只读、零副作用）
  if(SERVED && !guest){
    if(status) status.textContent="正在用本地模型润色三口吻…（已先给出基础草稿）";
    try{
      const r=await fetch("/api/wechat/reply-draft",{
        method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          contact:item.name, kind:item.kind, type:item.typeLabel,
          want:item.want, action:item.action, raw:item.raw.slice(0,800),
          suggestedReply:item.suggestedReply||""
        })
      });
      const j=await r.json().catch(()=>({}));
      if(r.ok && j.ok && j.drafts && wxReplyCurrent && wxReplyCurrent.id===item.id){
        wxReplyDrafts={
          pro:j.drafts.pro||wxReplyDrafts.pro,
          warm:j.drafts.warm||wxReplyDrafts.warm,
          short:j.drafts.short||wxReplyDrafts.short
        };
        wxRenderReplyTones();
        if(status) status.textContent=(j.source==="ollama"?`已由本地 ${j.model||"模型"} 生成`:"已用本地规则生成")+" · 仅复制不发微信";
      }else if(status){
        status.textContent="只读边界 · 已使用本地结构化草稿";
      }
    }catch(e){
      if(status) status.textContent="只读边界 · 已使用本地结构化草稿";
    }
  }
}
function wxCopyBriefReply(markDone){
  const ta=$("wxReplyText");
  const text=(ta&&ta.value)||"";
  try{ navigator.clipboard?.writeText(text); }catch(e){}
  if(markDone && wxReplyCurrent) wxSetBriefState(wxReplyCurrent.id,"done");
  closeOv("wxReplyOv");
  notify(markDone?"草稿已复制并标为已处理（不会自动发送）。":"草稿已复制到剪贴板（仅复制，不会自动发送）。","info");
}
function wxOpenRawModal(id){
  const item=wxBriefCache.find(x=>x.id===id);
  if(!item) return;
  const title=$("wxRawTitle"), body=$("wxRawBody");
  if(title) title.textContent="原文核对 · "+item.name;
  if(body) body.textContent=item.raw||"这条快照没有附带原始正文。";
  openOv("wxRawOv");
}
function wxSendBriefToTopic(id){
  const item=wxBriefCache.find(x=>x.id===id);
  if(!item) return;
  const text=`【微信群选题】${item.title||item.want}\n来源：${item.name}\n切入角度：${item.action}\n原话摘录：${(item.raw||"").slice(0,200)}`;
  try{ navigator.clipboard?.writeText(text); }catch(e){}
  notify("选题素材已复制到剪贴板，可直接粘贴到写作或星球草稿。","info");
}

function renderWxBrief(){
  const grid=$("wxBriefGrid"), tf=$("wxBriefTypeFilters"), sf=$("wxBriefStateFilters"), prog=$("wxBriefProgress"), subCnt=$("wxBriefCount");
  if(!grid) return;
  wxBriefCache=wxCollectBriefItems(wechat);
  const enriched=wxBriefCache.map(item=>{
    const st=wxBriefEffectiveState(item);
    return {...item, _state:st.state, _reopened:st.reopened};
  });
  const total=enriched.length;
  const pendingCnt=enriched.filter(x=>x._state==="pending").length;
  const doneCnt=total-pendingCnt;
  const pct=total?Math.round(doneCnt/total*100):0;

  if(subCnt) subCnt.textContent=pendingCnt?String(pendingCnt):"";
  if(prog) prog.innerHTML=`<span>已批 ${doneCnt}/${total}</span><span class="wx-brief-prog-bar"><i style="width:${pct}%"></i></span>`;

  const matchState=(x)=>{
    if(wxBriefStateFilter==="all") return true;
    if(wxBriefStateFilter==="pending") return x._state==="pending";
    return x._state==="read"||x._state==="done";
  };
  const typeMeta=[["all","全部"],["todo","待办"],["opportunity","机会"],["consultation","咨询"],["mention","群 @ 我"],["learning","群选题"]];
  if(tf){
    tf.innerHTML=typeMeta.map(([k,label])=>{
      const cnt=enriched.filter(x=>(k==="all"||x.type===k)&&matchState(x)).length;
      if(k==="learning" && !enriched.some(x=>x.type==="learning")) return "";
      return `<button class="wx-brief-tab${wxBriefType===k?" on":""}" type="button" role="tab" aria-selected="${wxBriefType===k?"true":"false"}" onclick="wxSetBriefType('${k}')">${label}<span>${cnt}</span></button>`;
    }).join("");
  }
  const stateMeta=[["pending","待批"],["all","全部"],["done","已办"]];
  if(sf){
    sf.innerHTML=stateMeta.map(([k,label])=>
      `<button class="wx-state-btn${wxBriefStateFilter===k?" on":""}" type="button" onclick="wxSetBriefStateFilter('${k}')">${label}</button>`
    ).join("");
  }

  const shown=enriched.filter(x=>(wxBriefType==="all"||x.type===wxBriefType)&&matchState(x));
  if(!total){
    grid.innerHTML=`<div class="wx-empty" style="grid-column:1/-1;border-top:0">当前微信快照里没有需要批阅的待办、机会或群提醒。</div>`;
    return;
  }
  if(!shown.length){
    grid.innerHTML=`<div class="wx-empty" style="grid-column:1/-1;border-top:0">当前筛选下没有批件。可以切到「全部」或「已办」回看历史批阅记录。</div>`;
    return;
  }
  grid.innerHTML=shown.map(x=>{
    const cls=["wx-brief-item", x._state==="done"?"done":(x._state==="read"?"read":(x.urgent?"urgent":""))].filter(Boolean).join(" ");
    const hasDelta=(x.prevSummary && x.latestDelta) || x._reopened;
    const factHtml=(x.prevSummary && x.latestDelta)
      ? `<div class="wx-brief-line"><span>进展</span><div class="wx-diff-inline">${esc(x.prevSummary)} → <b>${esc(x.latestDelta)}</b></div></div>`
      : `<div class="wx-brief-line"><span>对方要</span><div>${esc(x.want)}</div></div>`;
    const idArg=esc(x.id).replace(/'/g,"\\'");
    return `<article class="${cls}">`+
      `<div class="wx-brief-head">`+
        `<div class="wx-brief-who">`+
          `<span class="wx-kind">${esc(x.kind)}</span>`+
          `<b>${esc(x.name)}</b>`+
          `<span class="wx-brief-type">${esc(x.typeLabel)}</span>`+
          (hasDelta && x._state==="pending"?`<span class="wx-delta-dot">新进展</span>`:"")+
          (x._state==="read"?`<span class="wx-brief-type">· 已阅</span>`:"")+
          (x._state==="done"?`<span class="wx-brief-type">· 已处理</span>`:"")+
        `</div>`+
        `<span class="wx-todo-age${x.urgent&&x._state==="pending"?" bad":""}">${esc(x.ageText)}</span>`+
      `</div>`+
      factHtml+
      `<div class="wx-brief-line do"><span>你要做</span><div>${esc(x.action)}</div></div>`+
      `<div class="wx-brief-foot">`+
        `<div class="wx-links">`+
          `<button class="wx-link primary" type="button" onclick="wxOpenReplyModal('${idArg}')">回应草稿</button>`+
          `<button class="wx-link" type="button" onclick="wxOpenRawModal('${idArg}')">看原文</button>`+
          (x.canTopic?`<button class="wx-link ok" type="button" onclick="wxSendBriefToTopic('${idArg}')">送入选题</button>`:"")+
        `</div>`+
        `<div class="wx-links">`+
          (x._state!=="read"?`<button class="wx-link" type="button" onclick="wxSetBriefState('${idArg}','read')">已阅</button>`:"")+
          (x._state!=="done"?`<button class="wx-link" type="button" onclick="wxSetBriefState('${idArg}','done')">已处理</button>`:`<button class="wx-link" type="button" onclick="wxSetBriefState('${idArg}','pending')">撤销</button>`)+
        `</div>`+
      `</div>`+
    `</article>`;
  }).join("");
}
const WX_BUSINESS_STATES=["待核对","已确认","跟进中","待报价","已成交","暂不跟进"];
function wxBusinessState(id){
  try{return JSON.parse(localStorage.getItem("wb_wx_business")||"{}")[id]||{};}catch(e){return {};}
}
function wxSetBusinessState(id,patch){
  try{const all=JSON.parse(localStorage.getItem("wb_wx_business")||"{}");all[id]={...(all[id]||{}),...patch,updatedAt:new Date().toISOString()};localStorage.setItem("wb_wx_business",JSON.stringify(all));}catch(e){}
  renderWechat();
}
function wxSetStatus(id,status){if(WX_BUSINESS_STATES.includes(status))wxSetBusinessState(id,{status});}
function wxSetFollowup(id,value){wxSetBusinessState(id,{followupAt:value||null});}
function wxDraftReply(item){
  const text=item.suggestedReply||item.nextAction||"我先了解一下你的具体情况，再给你建议。";
  try{navigator.clipboard?.writeText(text);}catch(e){}
  notify("回复草稿已复制到剪贴板（仅复制，不会自动发送）。","info");
}
/* Jev 的 score 是 0–9（10 档 criteria 上限，11 档直接 422）。
   展示层只给整数百分比：两位小数在 ±0.1 的调用波动下没有意义，
   而 O 等级已经由后端阈值算好，页面不重复推导。 */
function wxScorePct(v){
  // null / 空串不是 0 分：缺值必须留空，否则会把"没评分"画成"0%"
  if(v===null||v===undefined||v==="") return "";
  const n=Number(v);
  if(!isFinite(n)) return "";
  return Math.round(Math.max(0,Math.min(9,n))/9*100)+"%";
}
function wxCoverageText(data){
  const b=data.businessAnalysis||{};
  const m=data.mcp||{};
  const server=m.server&&m.server.version?`MCP ${m.server.version}`:"MCP";
  const events=m.eventRetentionSeconds?Math.round(Number(m.eventRetentionSeconds)/3600)+"h事件窗口":"事件窗口未知";
  const ev=(data.eventCoverage||{});
  const live=ev.available?`实时覆盖 ${ev.returned||0} 条${ev.eventGap?" · 有缺口":""}`:"实时事件未接入";
  return `分析窗口 ${b.lookbackDays||"—"} 天 · 候选 ${b.candidates||0} · 已判断 ${b.analyzed||0} · ${b.judgmentModel||"未启用"} · ${server} · ${events} · ${live}`;
}
function wxAnalysisCard(item,kind){
  const level=item.opportunityLevel||"O1";
  const levelClass=level==="O3"?"hot":(level==="O2"?"medium":"");
  const type=kind==="opportunity"?(item.businessFit||"待确认"):(item.consultationType||"待确认");
  const question=kind==="consultation"?item.surfaceQuestion:item.evidence;
  const need=kind==="consultation"?item.underlyingNeed:item.evidence;
  const action=item.nextAction||"补充询问具体需求";
  const missing=item.missingInfo||"待确认";
  const raw=item.sourcePreview||"";
  const local=wxBusinessState(item.id);
  const status=local.status||"待核对";
  const followup=local.followupAt||"";
  return `<article class="wx-analysis-card">`+
    `<div class="wx-analysis-head"><span class="wx-kind">${esc(item.kind==="group"?"群":"私聊")}</span><b>${esc(item.contact||"未命名")}</b>`+
      (item.account?`<span class="wx-analysis-account">${esc(item.account)}</span>`:"")+
      (kind==="opportunity"?`<span class="wx-analysis-level ${levelClass}">${esc(level)} · ${esc(type)}</span>`:`<span class="wx-analysis-level">${esc(type)}</span>`)+
      `<span class="wx-todo-age">${esc(wxWhen(item.at))}</span></div>`+
    `<div class="wx-analysis-fact"><span>${kind==="opportunity"?"证据":"表面问题"}</span>${esc(question||"未提取到可读问题")}</div>`+
    (kind==="consultation"&&need?`<div class="wx-analysis-fact"><span>真实需求</span>${esc(need)}</div>`:"")+
    `<div class="wx-analysis-why"><b>为什么与我有关</b>${esc(item.whyRelated||"暂未发现直接关联，需人工核对")}</div>`+
    `<div class="wx-analysis-action"><span>可以做什么</span>${esc(action)}</div>`+
    `<div class="wx-analysis-missing"><span>还缺信息</span>${esc(missing)}</div>`+
    (kind==="consultation"&&item.suggestedReply?`<div class="wx-analysis-reply"><span>建议回答</span>${esc(item.suggestedReply)}</div>`:"")+
    `<div class="wx-analysis-actions"><select aria-label="机会状态" onchange="wxSetStatus('${esc(item.id)}',this.value)">`+
      WX_BUSINESS_STATES.map(x=>`<option value="${esc(x)}"${x===status?" selected":""}>${esc(x)}</option>`).join("")+`</select>`+
      `<input type="date" aria-label="跟进日期" value="${esc(followup)}" onchange="wxSetFollowup('${esc(item.id)}',this.value)">`+
      (kind==="consultation"&&item.suggestedReply?`<button class="wx-mute" type="button" onclick='wxDraftReply(${JSON.stringify(item).replace(/'/g,"&apos;")})'>复制回复草稿</button>`:"")+
    `</div>`+
    `<div class="wx-analysis-meta">`+
      `状态：${esc(status)}${followup?" · 跟进 "+esc(followup):""} · ${item.needsReview!==false?"需要人工确认 · ":""}${esc(item.judgmentModel||"未知模型")}${item.opportunityScore!=null?` · 机会 ${wxScorePct(item.opportunityScore)}`:""}${item.consultationScore!=null?` · 咨询 ${wxScorePct(item.consultationScore)}`:""}${item.analyzed===false?" · 模型未解析 · 仅展示检索证据":" · 仅供参考"}</div>`+
    (raw?`<details class="wx-analysis-raw"><summary>查看原文证据</summary><div class="wx-raw-body">${esc(raw)}</div></details>`:"")+
  `</article>`;
}
function renderWxAnalysis(data){
  const opp=data.opportunities||[], con=data.consultations||[];
  const op=$("wxOpportunities"), co=$("wxConsultations");
  if(op){op.innerHTML=opp.length?`<div class="wx-analysis-list">${opp.map(x=>wxAnalysisCard(x,"opportunity")).join("")}</div>`:`<div class="wx-empty">近 ${esc(String((data.businessAnalysis||{}).lookbackDays||14))} 天没有发现高置信机会。未命中关键词、未覆盖的历史消息和无法解析的媒体不等于没有机会。</div>`;}
  if(co){co.innerHTML=con.length?`<div class="wx-analysis-list">${con.map(x=>wxAnalysisCard(x,"consultation")).join("")}</div>`:`<div class="wx-empty">近 ${esc(String((data.businessAnalysis||{}).lookbackDays||14))} 天没有发现明确咨询。普通闲聊与纯资讯不会进入这里。</div>`;}
  $("wxOpportunityTag").textContent=opp.length?opp.length+" 条":"0";
  $("wxConsultationTag").textContent=con.length?con.length+" 条":"0";
  const cov=$("wxCoverage"); if(cov) cov.textContent=wxCoverageText(data);
  $("wxOpportunityCount").textContent=opp.length?opp.length:"";
  $("wxConsultationCount").textContent=con.length?con.length:"";
  $("wxMentionCount").textContent=(data.mentionsCount||0)?data.mentionsCount:"";
}
function renderWxSubnav(){
  document.querySelectorAll("#wxSubnav [data-wxview]").forEach(b=>{const on=b.dataset.wxview===wxView;b.classList.toggle("on",on);b.setAttribute("aria-selected",on?"true":"false");});
  setWxView(wxView,false);
}
function renderWechat(){
  const stats=$("wechatStats"), src=$("wechatSource"), note=$("wechatNote"),
        todoBox=$("wxTodo"), waitBox=$("wxWait"), mentionBox=$("wxMention"), activeBox=$("wxActive"),
        boundary=$("wxBoundary");
  if(!stats) return;

  const wash=(msg)=>{
    src.textContent="—";
    stats.innerHTML="";
    if(todoBox) todoBox.innerHTML=`<div class="wx-empty">${esc(msg)}</div>`;
    const briefBox=$("wxBriefGrid");
    if(briefBox) briefBox.innerHTML=`<div class="wx-empty" style="grid-column:1/-1;border-top:0">${esc(msg)}</div>`;
    const briefCnt=$("wxBriefCount"); if(briefCnt) briefCnt.textContent="";
    waitBox.innerHTML=`<div class="wx-empty">—</div>`;
    mentionBox.innerHTML=`<div class="wx-empty">—</div>`;
    activeBox.innerHTML="";
    const coverage=$("wxCoverage"); if(coverage) coverage.textContent="覆盖范围暂不可用";
    $("wxTodoTag").textContent="—";
    $("wxWaitTag").textContent="—";
    $("wxMentionTag").textContent="—";
    $("wxActiveTag").textContent="—";
    boundary.innerHTML="";
    if(note){ note.style.display=msg? "" : "none"; note.innerHTML=`<strong>微信快照不可用</strong><span>${esc(msg)}</span>`; }
  };

  if(!wechat){ wash(wechatErr||"还没有微信快照。先跑一次 python3 scripts/sync/wechat-sync.py。"); return; }
  if(wechat.available===false){ wash(wechat.reason||"微信 MCP 未连接。"); return; }

  const accounts=wechat.accounts||[];
  if(!accounts.length){ wash("MCP 在线，但没有可读的微信账号。"); return; }
  const opportunities=[], consultations=[];
  accounts.forEach(a=>{(a.opportunities||[]).forEach(x=>opportunities.push({...x,account:a.account?.name}));(a.consultations||[]).forEach(x=>consultations.push({...x,account:a.account?.name}));});
  renderWxAnalysis({opportunities,consultations,mentionsCount:accounts.reduce((n,a)=>n+(a.mentions||[]).length,0),businessAnalysis:accounts[0].businessAnalysis||{},eventCoverage:accounts[0].eventCoverage||{},mcp:wechat.mcp||{}});
  renderWxBrief();
  renderWxSubnav();

  if(note){
    if(wechatNote){ note.style.display=""; note.innerHTML=`<strong>离线快照</strong><span>${esc(wechatNote)}</span>`; }
    else note.style.display="none";
  }

  const sum=wechat.summary||{};
  const muted=wxMuted();
  const waitAll=[], mentionAll=[], activeAll=[];
  accounts.forEach(a=>{
    (a.waiting||[]).forEach(x=>waitAll.push({...x,account:a.account.name}));
    (a.mentions||[]).forEach(x=>mentionAll.push({...x,account:a.account.name}));
    (a.active||[]).forEach(x=>activeAll.push({...x,account:a.account.name}));
  });
  waitAll.sort((a,b)=>(Number(b.hoursAgo)||0)-(Number(a.hoursAgo)||0));  // 搁置最久在前
  // 忽略只影响这一屏的显示，快照里的数字不动 —— 所以 KPI 那两个数必须说清楚
  // 是"全部"还是"忽略后"，否则同一个页面两个口径。
  const waitHidden=waitAll.filter(x=>muted.includes(x.conversationId));
  const waitShown=waitAll.filter(x=>!muted.includes(x.conversationId));
  mentionAll.sort((a,b)=>String(b.at).localeCompare(String(a.at)));
  activeAll.sort((a,b)=>String(b.at).localeCompare(String(a.at)));

  const accountLine=accounts.map(a=>{
    const err=a.error?`（读取失败：${a.error}）`:"";
    return `${a.account.name}${a.account.ready?"":"（未就绪）"}${err}`;
  }).join(" · ");
  const generated=String(wechat.generatedAt||"").replace("T"," ").slice(0,16);
  src.innerHTML=`<b>微信 MCP</b> · 只读 · ${esc(accountLine)} · 快照 ${esc(generated)}`;
  src.title=src.textContent.trim();

  const windowHours=Number(sum.windowHours)||72;
  // 待办 = 模型判为"待回复"的那些。广告和已收尾不在待办里，但仍在快照里，
  // 折到下面的「不用回」区，随时能点开核对原文。
  // 默认方向必须是「当待办」，不能是「当广告」：快照缺 category（旧版本、
  // 模型没跑起来）时，归到"不用回"就等于把真待办静默藏起来 —— 宁可多显示一条，
  // 也不能漏掉一个在等你回话的人。只有明确判成广告/已结束的才移出待办。
  const isRest=x=>x.category==="广告"||x.category==="已结束";
  const todoAll=waitAll.filter(x=>!isRest(x));
  const restAll=waitAll.filter(isRest);
  const todoShown=todoAll.filter(x=>!muted.includes(x.conversationId));
  const restShown=restAll.filter(x=>!muted.includes(x.conversationId));
  const oldest=todoShown.length?Math.round(todoShown[0].hoursAgo):0;
  const hiddenHint=waitHidden.length?" · 已忽略 "+waitHidden.length:"";
  const notAnalyzed=waitAll.filter(x=>x.analyzed===false).length;

  stats.innerHTML=statCells([
    {k:"待办",v:todoShown.length,hint:(todoShown.length?`最久已放 ${oldest<24?oldest+" 小时":Math.floor(oldest/24)+" 天"}`:"微信上没有要你处理的事")+hiddenHint},
    {k:"不用回",v:restShown.length,hint:restShown.length?"广告与已收尾":"—"},
    {k:"群里 @ 我",v:mentionAll.length,hint:mentionAll.length?"近 "+((Number(sum.mentionDays)||7))+" 天":"近 7 天没有被点名"},
    {k:"已解析",v:notAnalyzed?(waitAll.length-notAnalyzed)+"/"+waitAll.length:"全部",hint:notAnalyzed?notAnalyzed+" 条模型未解析，显示原文":"本地模型已逐条读过"}
  ]);

  $("wxTodoTag").textContent=todoShown.length?todoShown.length+" 件":"0";
  $("wxWaitTag").textContent=restShown.length?restShown.length+" 条":"0";
  $("wxMentionTag").textContent=mentionAll.length?mentionAll.length+" 条":"0";
  $("wxActiveTag").textContent=activeAll.length?activeAll.length+" 个":"0";

  if(todoShown.length){
    todoBox.innerHTML = `<div class="wx-todo-list">`+todoShown.map(wxTodoRow).join("")+`</div>`;
  }else if(todoAll.length){
    todoBox.innerHTML = `<div class="wx-empty">${todoAll.length} 件待办都被你忽略了。`+
      `<button class="wx-mute" type="button" style="margin-left:8px" onclick="wxUnmuteAll()">全部恢复</button></div>`;
  }else{
    todoBox.innerHTML = `<div class="wx-empty">微信上没有要你处理的事 —— 近 ${windowHours} 小时里，没有人在等你回话。</div>`;
  }
  if(!todoShown.length && !todoAll.length && !restShown.length){
    todoBox.innerHTML = `<div class="wx-empty">近 ${windowHours} 小时没有待回复的私聊，最后一条都是你自己发的。</div>`;
  }

  if(restShown.length){
    waitBox.innerHTML = `<div class="wx-list">`+restShown.map(x=>wxRow({...x,kind:"direct"},{
        whenText:wxHoursLabel(x.hoursAgo), mutable:true
      })).join("")+`</div>`;
  }else{
    waitBox.innerHTML = `<div class="wx-empty">没有广告或已收尾的对话。</div>`;
  }

  mentionBox.innerHTML = mentionAll.length
    ? `<div class="wx-list">`+mentionAll.map(x=>wxRow({...x,kind:"group"},{
        whenText:wxWhen(x.at)
      })).join("")+`</div>`
    : `<div class="wx-empty">近 ${Number(sum.mentionDays)||7} 天没有群里 @ 你的消息。</div>`;

  activeBox.innerHTML = activeAll.length
    ? `<div class="wx-list">`+activeAll.slice(0,40).map(x=>wxRow(x,{old:x.from==="self"})).join("")+`</div>`
    : `<div class="wx-empty">没有可读的活跃会话。</div>`;
  $("wxActiveBrief").textContent = activeAll.length
    ? `最近一条 ${wxWhen(activeAll[0].at)} · 灰掉的是最后一条由你发出`
    : "没有可读的活跃会话";

  const semInfo=(wechat.accounts||[]).map(a=>a.semantic).filter(Boolean)[0]||{};
  boundary.innerHTML = `<b>口径：</b>「待办」由本地模型 <b>${esc(semInfo.llm||"未启用")}</b> 逐条读过最近 8 条消息后判断，` +
    `不是微信的未读数 —— MCP 不提供未读，所以这里不写 0 也不编数字。` +
    `语音${semInfo.asr?"已转成文字":"<b>未能转写</b>"}（SILK → SenseVoice，在本机跑，不上传）。` +
    `模型的判断会错，每一条都能展开「看原文」核对；<b>分类只用来分组，不用来删数据</b> —— ` +
    `判成广告的仍留在「不用回」区。本页只读：不能在这里发消息，也不会改动任何会话。`;
}
function renderTime(){
  const sel=$("timerProj");
  if(sel){
    const opts=focusProjectOptions();
    const cur=focusTimer.projectId||opts[0]?.value||"free";
    if(!opts.some(p=>p.value===cur)) opts.push({value:cur,label:"已选项目（当前不在进行中）",source:"local",status:""});
    const groups=[
      ["Notion 进行中",opts.filter(p=>p.source==="notion")],
      ["本地项目",opts.filter(p=>p.source!=="notion")]
    ];
    sel.innerHTML=`<option value="free"${cur==="free"?" selected":""}>自由专注（不关联项目）</option>`+
      groups.filter(g=>g[1].length).map(([label,list])=>
        `<optgroup label="${esc(label)}">`+list.map(p=>`<option value="${esc(p.value)}"${p.value===cur?" selected":""}>${esc(p.label)}</option>`).join("")+`</optgroup>`
      ).join("");
    sel.value=cur;
    sel.onchange=()=>{focusTimer.projectId=sel.value||"free";saveFocusTimer();saveNew("wb_focus_project",focusTimer.projectId);};
  }
  renderTimeStats();syncFocusTimerUI();renderFocusToday();renderFocusTrend();renderTimeBars();
  renderWorldClock();
}
function setFocusDuration(minutes){
  if(focusTimer.running||focusTimer.status==="paused"){notify("计时进行中，先暂停或放弃后才能切换时长。","warn");return;}
  focusTimer=freshFocusTimer(minutes,focusTimer.projectId);
  saveFocusTimer();syncFocusTimerUI();
}
function startFocusTicker(){
  if(focusTicker) clearInterval(focusTicker);
  focusTicker=setInterval(focusTimerTick,500);
}
function focusTimerTick(){
  if(!focusTimer.running) return;
  const left=focusTimerRemaining();
  const clock=$("clock"); if(clock) clock.textContent=mmss(left);
  if(left<=0) completeFocusTimer();
}
function toggleTimer(){
  if(guest){notify("访客模式只展示示例，不记录专注。","warn");return;}
  if(focusTimer.running){
    focusTimer.remainingSeconds=focusTimerRemaining();
    focusTimer.running=false;focusTimer.endAt=null;focusTimer.status="paused";
    saveFocusTimer();syncFocusTimerUI();return;
  }
  if(focusTimer.remainingSeconds<=0) focusTimer.remainingSeconds=focusTimer.durationMinutes*60;
  const sel=$("timerProj");
  if(sel&&sel.value) focusTimer.projectId=sel.value;
  focusTimer.startAt=focusTimer.startAt||new Date().toISOString();
  focusTimer.endAt=Date.now()+focusTimer.remainingSeconds*1000;
  focusTimer.running=true;focusTimer.status="running";
  saveFocusTimer();syncFocusTimerUI();startFocusTicker();
}
function resetTimer(){
  if(focusTimer.running||focusTimer.status==="paused"||focusTimerElapsedSeconds()>0){
    recordFocusSession("abandoned");
  }
  focusTimer=freshFocusTimer(focusTimer.durationMinutes,focusTimer.projectId);
  saveFocusTimer();syncFocusTimerUI();renderTime();
}
function completeFocusTimer(){
  recordFocusSession("completed");
  focusTimer=freshFocusTimer(focusTimer.durationMinutes,focusTimer.projectId);
  saveFocusTimer();syncFocusTimerUI();renderTime();
}
function recordFocusSession(status){
  if(guest) return null;
  const p=focusProjectByValue(focusTimer.projectId);
  const elapsed=status==="completed"?focusTimer.durationMinutes*60:focusTimerElapsedSeconds();
  if(status!=="completed"&&elapsed<5) return null;
  const endAt=status==="completed"&&focusTimer.endAt
    ? new Date(+focusTimer.endAt).toISOString() : new Date().toISOString();
  const startAt=focusTimer.startAt||new Date(Date.now()-elapsed*1000).toISOString();
  const s={
    id:uidGen(),date:focusDateStr(endAt),startAt,endAt,
    durationMinutes:focusTimer.durationMinutes,elapsedSeconds:elapsed,
    projectId:focusTimer.projectId||"free",projectName:p?p.label:"自由专注",
    projectSource:p?p.source:"local",status,noteLine:"",noteAddedAt:null,
    createdAt:new Date().toISOString()
  };
  s.noteLine=focusNoteLine(s);
  if(!Array.isArray(state.focusSessions)) state.focusSessions=[];
  state.focusSessions.push(s);
  if(state.focusSessions.length>1000) state.focusSessions=state.focusSessions.slice(-1000);
  saveState();
  if(status==="completed"&&focusAutoNoteEnabled()) appendFocusToNote(s.id,true);
  if(status==="completed"){
    notify(`专注完成：${s.projectName} · ${Math.round(elapsed/60)} 分钟`,"info");
    if(focusNotifyEnabled()){
      try{new Notification("专注完成",{body:`${s.projectName} · ${Math.round(elapsed/60)} 分钟`,tag:"daily-workbench-focus"});}catch(e){}
    }
  }else{
    notify(`已记录中断：${s.projectName} · ${Math.max(1,Math.round(elapsed/60))} 分钟`,"warn");
  }
  renderFocusToday();renderFocusTrend();renderTimeBars();renderTimeStats();renderTodayTimeline();
  return s;
}
function appendFocusToNote(id,quiet){
  const s=(state.focusSessions||[]).find(x=>x.id===id); if(!s) return;
  const line=s.noteLine||focusNoteLine(s);
  const old=String(state.notes[s.date]||"").trim();
  if(!old.includes(line)) state.notes[s.date]=old?old+"\n"+line:line;
  s.noteLine=line;s.noteAddedAt=new Date().toISOString();
  saveState();renderNotes();renderNoteHistory();renderFocusToday();renderTodayTimeline();
  if(!quiet) notify("已追加到今日备注。","info");
}
async function pushFocusToNotion(id){
  if(guest){notify("访客模式不写真实数据，也不会回写 Notion。","warn");return;}
  if(!SERVED){notify("回写需要本地同步服务，请用 127.0.0.1:8787 的在线链接打开。","warn");return;}
  if(!TOKEN){notify("链接缺少访问令牌，无法回写 Notion。","err");return;}
  const s=(state.focusSessions||[]).find(x=>x.id===id);
  if(!s){notify("找不到这条专注记录。","err");return;}
  if(s.status!=="completed"){notify("只有已完成的专注记录可以回写。","warn");return;}
  if(s.notionPageId){notify("这条专注已经回写过 Notion。","info");return;}
  try{
    const r=await fetch("/api/focus/push",{
      method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sessionId:id})
    });
    let j={};try{j=await r.json();}catch(e){}
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    if(j.session) Object.assign(s,j.session);
    renderFocusToday();renderTodayTimeline();
    notify(j.alreadyPushed?"这条专注已在 Notion，无需重复写入。":"已回写到 Notion「今日番茄追踪表」。","info");
  }catch(e){
    notify("回写 Notion 失败："+e.message,"err");
  }
}
function undoFocusSession(id){
  if(guest) return;
  const i=(state.focusSessions||[]).findIndex(s=>s.id===id); if(i<0) return;
  const s=state.focusSessions[i];
  if(s.noteAddedAt&&s.noteLine){
    const left=String(state.notes[s.date]||"").split("\n").filter(line=>line.trim()!==s.noteLine.trim()).join("\n").trim();
    if(left) state.notes[s.date]=left;else delete state.notes[s.date];
  }
  state.focusSessions.splice(i,1);
  saveState();renderNotes();renderNoteHistory();renderTime();renderTodayTimeline();
  notify("已撤销这条专注记录。","info");
}
function undoLastFocusSession(){
  const rows=focusSessionsForDate(focusDateStr());
  if(!rows.length){notify("今天还没有可撤销的专注记录。","warn");return;}
  undoFocusSession(rows[0].id);
}
function setFocusRange(n){
  focusRange=[7,14,30].includes(+n)?+n:14;
  saveNew(FOCUS_RANGE_KEY,focusRange);renderFocusTrend();
}
function toggleFocusAutoNote(){
  const on=!!$("focusAutoNote")?.checked;
  saveNew(FOCUS_AUTO_NOTE_KEY,on);
  notify(on?"完成专注后会追加到今日备注。":"不会自动写备注，可在记录里手动写入。","info");
}
async function toggleFocusNotifications(){
  if(!("Notification" in window)){notify("当前浏览器不支持系统通知。","warn");return;}
  if(Notification.permission==="granted"){
    saveNew(FOCUS_NOTIFY_KEY,!focusNotifyEnabled());
    renderFocusControls();
    return;
  }
  if(Notification.permission==="denied"){notify("浏览器已拒绝通知，请在网站权限里重新开启。","warn");return;}
  try{
    const p=await Notification.requestPermission();
    saveNew(FOCUS_NOTIFY_KEY,p==="granted");
    renderFocusControls();
    if(p==="granted") notify("浏览器提醒已开启。","info");
  }catch(e){notify("开启通知失败："+e.message,"warn");}
}
function addTimeProj(){
  const nm=$("ntNm").value.trim(); if(!nm) return;
  if(!TIME_PROJECTS.some(p=>p.name===nm)) TIME_PROJECTS.push({id:uidGen(),name:nm,source:"local",status:"进行中"});
  saveNew("wb_time_projects",TIME_PROJECTS);
  const added=TIME_PROJECTS.find(p=>p.name===nm);
  focusTimer.projectId=focusProjectKey(added);saveFocusTimer();
  $("ntNm").value="";renderTime();
}
function delTimeProj(value){
  const p=TIME_PROJECTS.find(x=>focusProjectKey(x)===value);
  if(!p) return;
  TIME_PROJECTS=TIME_PROJECTS.filter(x=>x.id!==p.id);
  saveNew("wb_time_projects",TIME_PROJECTS);
  if(focusTimer.projectId===value){focusTimer.projectId="free";saveFocusTimer();}
  renderTime();
}
function delLegacyTime(name){
  delete TIME_BARS[name];saveNew("wb_time_bars",TIME_BARS);renderTimeBars();
}
function restoreFocusTimer(){
  if(focusTimer.running){
    if(focusTimerRemaining()<=0) completeFocusTimer();
    else startFocusTicker();
  }
  syncFocusTimerUI();
}

/* 总览的统一时间线：专注 / 收益 / 项目 / 内容放在同一天里看。 */
function renderTodayTimeline(){
  const box=$("todayTimeline"); if(!box) return;
  const today=focusDateStr(), events=[];
  focusSessionsForDate(today).forEach(s=>events.push({
    sort:+new Date(s.startAt||s.createdAt||Date.now()),
    time:focusTimeLabel(s.startAt),kind:"专注",cls:"focus",
    title:s.projectName||"自由专注",
    detail:`${Math.max(1,Math.round((+s.elapsedSeconds||0)/60))} 分钟 · ${s.status==="completed"?"完成":"中断"}`
  }));
  (state&&state.earnings||[]).filter(e=>e.date===today).forEach(e=>events.push({
    sort:Date.now()-1,time:"—",kind:"收益",cls:"income",
    title:e.note||e.type||"收益记录",detail:`${fmt(e.amount)} · ${e.type||"未标类型"}`
  }));
  (state&&state.projects||[]).filter(p=>!["已完成","已取消"].includes(p.status)).slice(0,3).forEach(p=>events.push({
    sort:Date.now()-2,time:p.due===today?"今天":(p.due||"—"),kind:"项目",cls:"project",
    title:p.name,detail:`${p.status||"未标状态"}${p.progress!==null&&p.progress!==undefined?` · ${p.progress}%`:""}`
  }));
  const plan=(social&&social.todayPlan)||[];
  plan.forEach(p=>{
    const st=mediaStatus(p.id,p.defaultStatus);
    const label=st==="published"?"已发布":st==="skip"?"已跳过":"未核验";
    events.push({sort:Date.now()-3,time:"—",kind:"内容",cls:"content",
      title:p.platform||p.name||"内容计划",detail:label});
  });
  events.sort((a,b)=>b.sort-a.sort);
  const tag=$("todayTimelineTag"); if(tag) tag.textContent=`${events.length} 条`;
  box.innerHTML=events.length?events.map(e=>
    `<div class="timeline-row ${e.cls}"><span class="tl-time">${esc(e.time)}</span>`+
    `<span class="tl-kind">${esc(e.kind)}</span>`+
    `<span class="tl-main">${esc(e.title)}<small>${esc(e.detail)}</small></span></div>`
  ).join(""):`<div class="focus-empty">今天还没有可汇总的专注、收益、项目或内容记录。</div>`;
}

/* —— 世界时钟 —— */
const WC_PIN="Asia/Shanghai";
const WC_PRESETS=[
  {tz:"Asia/Shanghai",label:"上海"},{tz:"Asia/Tokyo",label:"东京"},
  {tz:"Asia/Seoul",label:"首尔"},{tz:"Asia/Singapore",label:"新加坡"},
  {tz:"Asia/Dubai",label:"迪拜"},{tz:"Europe/London",label:"伦敦"},
  {tz:"Europe/Paris",label:"巴黎"},{tz:"Europe/Berlin",label:"柏林"},
  {tz:"America/New_York",label:"纽约"},{tz:"America/Chicago",label:"芝加哥"},
  {tz:"America/Denver",label:"丹佛"},{tz:"America/Los_Angeles",label:"洛杉矶"},
  {tz:"America/Vancouver",label:"温哥华"},{tz:"America/Sao_Paulo",label:"圣保罗"},
  {tz:"Australia/Sydney",label:"悉尼"},{tz:"Pacific/Auckland",label:"奥克兰"},
  {tz:"UTC",label:"UTC"}
];
const WC_DEFAULT=["Asia/Shanghai","America/Los_Angeles","America/New_York","Europe/London","Asia/Tokyo"];
/* 必须传副本：loadNew 在没有存档时直接返回 seed 引用，后续 push 会污染「恢复默认」。 */
let WC_ZONES=loadNew("wb_world_clock",WC_DEFAULT.slice());
let WC_SORT=loadNew("wb_world_clock_sort","order");
if(!["order","time","offset"].includes(WC_SORT)) WC_SORT="order";
const WC_CANDIDATE_KEY="wb_world_clock_candidates";
const WC_WORK_KEY="wb_world_clock_work";
const WC_NOTES_KEY="wb_world_clock_notes";
const WC_WORK_DEFAULT={start:"09:00",end:"18:00",days:[1,2,3,4,5]};
let WC_CANDIDATES=loadNew(WC_CANDIDATE_KEY,[]);
if(!Array.isArray(WC_CANDIDATES)) WC_CANDIDATES=[];
let WC_NOTES=loadNew(WC_NOTES_KEY,{});
if(!WC_NOTES||typeof WC_NOTES!=="object"||Array.isArray(WC_NOTES)) WC_NOTES={};
function wcLabel(tz){const p=WC_PRESETS.find(x=>x.tz===tz);return p?p.label:tz;}
/* 用 Intl 而不是手写 UTC 偏移：夏令时由 ICU 时区库负责，换城市不用改代码。 */
function wcParts(tz,d){
  const f=new Intl.DateTimeFormat("en-CA",{timeZone:tz,year:"numeric",month:"2-digit",day:"2-digit",
    hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false,weekday:"short"});
  const p={}; f.formatToParts(d).forEach(x=>{p[x.type]=x.value;});
  const hour=Number(p.hour)%24;
  const offPart=new Intl.DateTimeFormat("en-US",{timeZone:tz,timeZoneName:"longOffset"})
    .formatToParts(d).find(x=>x.type==="timeZoneName");
  const off=(offPart?offPart.value:"GMT").replace("GMT","UTC");
  const offMin=(()=>{const m=/^UTC([+-])(\d{2}):(\d{2})$/.exec(off);
    return m?(m[1]==="-"?-1:1)*(Number(m[2])*60+Number(m[3])):0;})();
  return {date:p.year+"-"+p.month+"-"+p.day,
    time:String(hour).padStart(2,"0")+":"+p.minute+":"+p.second,
    hour,weekday:p.weekday,offset:off,offsetMinutes:offMin};
}
function wcDayLabel(tzDate,localDate){
  const d=(new Date(tzDate+"T00:00:00Z")-new Date(localDate+"T00:00:00Z"))/86400000;
  return d===0?"今天":d===1?"明天":d===-1?"昨天":tzDate;
}
function wcWorkConfig(){
  const x=loadNew(WC_WORK_KEY,WC_WORK_DEFAULT);
  const okTime=v=>/^\d{2}:\d{2}$/.test(String(v||""));
  return {
    start:okTime(x&&x.start)?x.start:WC_WORK_DEFAULT.start,
    end:okTime(x&&x.end)?x.end:WC_WORK_DEFAULT.end,
    days:Array.isArray(x&&x.days)?x.days.map(Number).filter(n=>n>=0&&n<=6):WC_WORK_DEFAULT.days.slice()
  };
}
function wcClockMinutes(v){
  const m=/^(\d{2}):(\d{2})$/.exec(String(v||""));
  return m?Number(m[1])*60+Number(m[2]):0;
}
function wcStatus(hour,weekday,cfg){
  cfg=cfg||wcWorkConfig();
  const day={Sun:0,Mon:1,Tue:2,Wed:3,Thu:4,Fri:5,Sat:6}[weekday];
  if(!cfg.days.includes(day)) return {cls:"rest",txt:weekday==="Sat"||weekday==="Sun"?"周末":"非工作日"};
  const now=Number(hour)*60, start=wcClockMinutes(cfg.start), end=wcClockMinutes(cfg.end);
  const working=start<=end?(now>=start&&now<end):(now>=start||now<end);
  if(working) return {cls:"work",txt:"工作时间"};
  if(hour>=22||hour<8) return {cls:"rest",txt:"休息"};
  return {cls:"off",txt:"非工作时段"};
}
/* 与上海比较时差；用分钟差而不是拼接 UTC 字符串，跨半小时时区也不会算错。 */
function wcDiff(tz,now){
  const base=wcParts(WC_PIN,now).offsetMinutes;
  const diff=wcParts(tz,now).offsetMinutes-base;
  if(diff===0) return "与上海同时";
  const abs=Math.abs(diff), h=Math.floor(abs/60), m=abs%60;
  return `比上海${diff>0?"快":"慢"} ${h} 小时${m?` ${m} 分`:""}`;
}
function wcHourMode(){ return loadNew("wb_world_clock_12h",false)===true; }
function wcFormatTime(p,hour12){
  if(!hour12) return p.time;
  const [hh,mm,ss]=p.time.split(":").map(Number);
  return `${String(hh%12||12).padStart(2,"0")}:${String(mm).padStart(2,"0")}:${String(ss).padStart(2,"0")} ${hh<12?"AM":"PM"}`;
}
function wcWeekdayLabel(weekday){
  return ({Sun:"周日",Mon:"周一",Tue:"周二",Wed:"周三",Thu:"周四",Fri:"周五",Sat:"周六"})[weekday]||weekday;
}
function wcOverviewDate(p){
  const m=/^(\d{4})-(\d{2})-(\d{2})$/.exec(p.date);
  return m?`${+m[2]}月${+m[3]}日 · ${wcWeekdayLabel(p.weekday)}`:p.date;
}
function wcCandidateZones(){
  const available=WC_ZONES.filter(tz=>tz!==WC_PIN);
  const picked=[];
  WC_CANDIDATES.forEach(tz=>{
    if(available.includes(tz)&&!picked.includes(tz)&&picked.length<2) picked.push(tz);
  });
  available.forEach(tz=>{
    if(!picked.includes(tz)&&picked.length<2) picked.push(tz);
  });
  WC_CANDIDATES=picked;
  return picked;
}
function renderWorldClockOverview(now){
  const box=$("wcOverview"); if(!box) return;
  now=now||new Date();
  const hour12=wcHourMode();
  const primary=wcParts(WC_PIN,now);
  const primaryStatus=wcStatus(primary.hour,primary.weekday);
  const alternatives=wcCandidateZones();
  const zones=[0,1].map(i=>{
    const tz=alternatives[i];
    if(!tz) return `<div class="wc-overview-zone empty">
      <span class="wc-zone-index">${i+1}</span>
      <span class="wc-zone-main"><span class="wc-zone-name">候选时区 ${i+1}</span>
      <span class="wc-zone-meta">在下方选择候选</span></span>
      <span class="wc-zone-time">--:--</span>
    </div>`;
    const p=wcParts(tz,now),st=wcStatus(p.hour,p.weekday);
    return `<div class="wc-overview-zone" data-tz="${esc(tz)}">
      <span class="wc-zone-index">${i+1}</span>
      <span class="wc-zone-main"><span class="wc-zone-name">${esc(wcLabel(tz))}</span>
      <span class="wc-zone-meta"><i class="wc-dot ${st.cls}"></i>${esc(st.txt)} · ${esc(wcDayLabel(p.date,primary.date))}</span></span>
      <span class="wc-zone-time" data-wc-time>${esc(wcFormatTime(p,hour12))}</span>
    </div>`;
  }).join("");
  box.innerHTML=`<div class="wc-overview-primary" data-tz="${esc(WC_PIN)}">
      <span class="wc-overview-kicker"><span>上海 · 当前</span><span class="wc-overview-scope">候选时间 · 1–2</span></span>
      <strong class="wc-overview-time" data-wc-time>${esc(wcFormatTime(primary,hour12))}</strong>
      <span class="wc-overview-meta"><i class="wc-dot ${primaryStatus.cls}"></i>${esc(wcOverviewDate(primary))} · ${esc(primaryStatus.txt)}</span>
    </div>
    <div class="wc-overview-zones">${zones}</div>`;
}
/* 协作窗口找未来 48 小时里同时在线城市最多的一段，返回上海本地时段。 */
function wcBestWindow(now){
  const slots=[];
  const start=new Date(now);
  start.setSeconds(0,0);
  start.setMinutes(Math.ceil(start.getMinutes()/30)*30);
  for(let i=0;i<96;i++){
    const at=new Date(start.getTime()+i*30*60000);
    const cities=WC_ZONES.filter(tz=>wcStatus(wcParts(tz,at).hour,wcParts(tz,at).weekday).cls==="work");
    slots.push({at,count:cities.length,cities});
  }
  const max=slots.reduce((n,s)=>Math.max(n,s.count),0);
  if(!max) return null;
  let startIdx=slots.findIndex(s=>s.count===max);
  let endIdx=startIdx;
  while(endIdx+1<slots.length&&slots[endIdx+1].count===max) endIdx++;
  const first=slots[startIdx], firstShanghai=wcParts(WC_PIN,first.at);
  const end=new Date(slots[endIdx].at.getTime()+30*60000), endShanghai=wcParts(WC_PIN,end);
  const day=wcDayLabel(firstShanghai.date,wcParts(WC_PIN,now).date);
  const hhmm=t=>t.time.slice(0,5);
  return {count:max,cities:first.cities,label:`${day} ${hhmm(firstShanghai)}–${hhmm(endShanghai)}`};
}
function wcZonedInstant(dateStr,timeStr,tz){
  const dm=/^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr||""));
  const tm=/^(\d{2}):(\d{2})$/.exec(String(timeStr||""));
  if(!dm||!tm) return null;
  const y=+dm[1],mon=+dm[2],day=+dm[3],h=+tm[1],min=+tm[2];
  let guess=Date.UTC(y,mon-1,day,h,min);
  for(let i=0;i<2;i++){
    const off=wcParts(tz,new Date(guess)).offsetMinutes;
    guess=Date.UTC(y,mon-1,day,h,min)-off*60000;
  }
  return new Date(guess);
}
function syncWorldClockControls(){
  const cfg=wcWorkConfig();
  if($("wcWorkStart")) $("wcWorkStart").value=cfg.start;
  if($("wcWorkEnd")) $("wcWorkEnd").value=cfg.end;
  for(let d=0;d<=6;d++){const c=$("wcDay"+d);if(c) c.checked=cfg.days.includes(d);}
  const date=$("wcMeetDate");
  if(date&&!date.value) date.value=focusDateStr();
}
function setWorldClockWork(){
  const start=$("wcWorkStart")?.value||WC_WORK_DEFAULT.start;
  const end=$("wcWorkEnd")?.value||WC_WORK_DEFAULT.end;
  const days=[];
  for(let d=0;d<=6;d++) if($("wcDay"+d)?.checked) days.push(d);
  if(!days.length){notify("至少选择一个工作日。","warn");return;}
  saveNew(WC_WORK_KEY,{start,end,days});
  renderWorldClock();
  notify(`工作时间已设为 ${start}–${end}。`,"info");
}
function editWorldClockNote(tz){
  const current=String(WC_NOTES[tz]||"");
  const next=typeof prompt==="function"?prompt("城市备注（客户、团队或会议用途）",current):current;
  if(next===null) return;
  const v=String(next).trim();
  if(v) WC_NOTES[tz]=v.slice(0,120);else delete WC_NOTES[tz];
  saveNew(WC_NOTES_KEY,WC_NOTES);renderWorldClock();
}
async function copyWorldClockTime(tz){
  const p=wcParts(tz,new Date());
  const text=`${wcLabel(tz)} ${p.date} ${wcFormatTime(p,wcHourMode())} (${p.offset})`;
  try{
    if(navigator.clipboard&&navigator.clipboard.writeText) await navigator.clipboard.writeText(text);
    else throw new Error("浏览器不支持剪贴板 API");
    notify("已复制："+text,"info");
  }catch(e){notify("复制失败："+e.message,"warn");}
}
function renderMeetingSources(){
  const sel=$("wcMeetFrom"); if(!sel) return;
  const cur=sel.value||WC_PIN;
  sel.innerHTML=WC_ZONES.map(tz=>`<option value="${esc(tz)}">${esc(wcLabel(tz))} · ${esc(tz)}</option>`).join("");
  sel.value=WC_ZONES.includes(cur)?cur:WC_PIN;
  const date=$("wcMeetDate"); if(date&&!date.value) date.value=focusDateStr();
}
function convertMeetingTime(){
  const date=$("wcMeetDate")?.value||focusDateStr();
  const time=$("wcMeetTime")?.value||"09:00";
  const from=$("wcMeetFrom")?.value||WC_PIN;
  const at=wcZonedInstant(date,time,from);
  const out=$("wcMeetOut");
  if(!at){if(out) out.innerHTML="<span class=\"note\">日期或时间格式不正确。</span>";return;}
  const localDate=wcParts(from,at).date;
  const rows=WC_ZONES.map(tz=>{
    const p=wcParts(tz,at), st=wcStatus(p.hour,p.weekday);
    return `<div class="wc-meet-row"><span>${esc(wcLabel(tz))} · ${esc(wcDayLabel(p.date,localDate))} · ${esc(st.txt)}</span>
      <b>${esc(p.date)} ${esc(wcFormatTime(p,wcHourMode()))}</b></div>`;
  }).join("");
  if(out) out.innerHTML=`<div class="note">以 ${esc(wcLabel(from))} ${esc(date)} ${esc(time)} 为基准</div>${rows}`;
}
function wcOrderedStates(states){
  return [...states].sort((a,b)=>{
    if(WC_SORT==="order") return WC_ZONES.indexOf(a.tz)-WC_ZONES.indexOf(b.tz);
    if(a.pinned!==b.pinned) return a.pinned?-1:1;
    if(WC_SORT==="time") return (a.p.hour*3600+a.p.minute*60+a.p.second)-(b.p.hour*3600+b.p.minute*60+b.p.second);
    return a.p.offsetMinutes-b.p.offsetMinutes;
  });
}
function renderWorldClock(){
  const box=$("wcRows"); if(!box) return;
  const now=new Date();
  wcCandidateZones();
  renderWorldClockOverview(now);
  const localTz=Intl.DateTimeFormat().resolvedOptions().timeZone||WC_PIN;
  const localDate=wcParts(localTz,now).date;
  const hour12=wcHourMode();
  const states=WC_ZONES.map(tz=>{
    const p=wcParts(tz,now);
    return {tz,p,st:wcStatus(p.hour,p.weekday),pinned:tz===WC_PIN};
  });
  const query=String(($("wcFind")&&$("wcFind").value)||"").trim().toLowerCase();
  const shown=wcOrderedStates(states.filter(x=>!query
    ||wcLabel(x.tz).toLowerCase().includes(query)||x.tz.toLowerCase().includes(query)));
  box.innerHTML=shown.map(({tz,p,st,pinned})=>{
    const memo=String(WC_NOTES[tz]||"").trim();
    return `<div class="wc-row" role="listitem" data-tz="${esc(tz)}">
      <div class="wc-city"><b>${esc(wcLabel(tz))}${pinned?' <span class="tag">本地</span>':''}</b>
        <span><i class="wc-dot ${st.cls}"></i>${st.txt} · ${wcDayLabel(p.date,localDate)} · ${esc(p.offset)}</span>
        <span class="wc-diff">${esc(wcDiff(tz,now))}</span>
        ${memo?`<span class="wc-note">${esc(memo)}</span>`:""}</div>
      <div class="wc-side">
        <div class="wc-time" data-wc-time>${esc(wcFormatTime(p,hour12))}</div>
        <span><button class="wc-copy" onclick="copyWorldClockTime('${esc(tz)}')">复制</button>
        <button class="wc-copy" onclick="editWorldClockNote('${esc(tz)}')">备注</button>
        ${pinned?"":`<button class="wc-remove" onclick="delWorldClock('${esc(tz)}')" aria-label="从世界时钟移除 ${esc(wcLabel(tz))}">移除</button>`}</span>
      </div>
    </div>`;
  }).join("")||`<div class="empty">${query?"没有匹配的城市，换个关键词试试。":"还没有城市，下面添加一个。"}</div>`;
  const working=states.filter(x=>x.st.cls==="work").length;
  const workingNames=states.filter(x=>x.st.cls==="work").map(x=>wcLabel(x.tz)).join("、")||"暂无";
  const best=wcBestWindow(now);
  const work=wcWorkConfig();
  const summary=$("wcSummary");
  if(summary) summary.innerHTML=`<div><b>${working} / ${WC_ZONES.length} 个城市在线</b>
      <span>${esc(workingNames)} · 当地时间 ${esc(work.start)}–${esc(work.end)}</span></div>
    <div><b>${best?esc(best.label):"未来 48 小时无协作时段"}</b>
      <span>${best?`${best.count} / ${WC_ZONES.length} 城同时在线`:"没有城市处于工作时间"}</span></div>
    <div><b>时差以上海为基准</b>
      <span>自动跟随夏令时与半小时时区</span></div>`;
  const brief=$("wcBrief");
  if(brief) brief.textContent=`${working} 城在线 · ${best?`最佳窗口 ${best.label}`:"未来 48 小时暂无协作窗口"}`;
  const pick=$("wcPick");
  if(pick) pick.innerHTML=WC_PRESETS.filter(p=>!WC_ZONES.includes(p.tz))
    .map(p=>`<option value="${esc(p.tz)}">${esc(p.label)} · ${esc(p.tz)}</option>`).join("");
  [1,2].forEach(slot=>{
    const select=$("wcCandidate"+slot);
    if(!select) return;
    const current=WC_CANDIDATES[slot-1]||"";
    select.innerHTML=`<option value="">未选择</option>`+WC_ZONES.filter(tz=>tz!==WC_PIN)
      .map(tz=>`<option value="${esc(tz)}">${esc(wcLabel(tz))}</option>`).join("");
    select.value=current;
  });
  const tag=$("wcTag"); if(tag) tag.textContent=WC_ZONES.length+" 个城市 · 本地设置";
  const sort=$("wcSort"); if(sort) sort.value=WC_SORT;
  renderMeetingSources();
  const b24=$("wcHour24"),b12=$("wcHour12");
  if(b24&&b12){
    b24.classList.toggle("on",!hour12); b12.classList.toggle("on",hour12);
    b24.setAttribute("aria-pressed",String(!hour12)); b12.setAttribute("aria-pressed",String(hour12));
  }
}
let wcLastMin="";
function tickWorldClock(){
  if(document.hidden) return;
  const now=new Date();
  const stamp=now.getFullYear()+"-"+(now.getMonth()+1)+"-"+now.getDate()+" "+now.getHours()+":"+now.getMinutes();
  /* 每分钟重建一次：跨过整点/午夜时，工作状态与「今天/明天」也要跟着变。 */
  if(stamp!==wcLastMin){ wcLastMin=stamp; renderWorldClock(); return; }
  document.querySelectorAll("[data-wc-time]").forEach?.(el=>{
    const host=el.closest("[data-tz]"); if(!host) return;
    const tz=host.getAttribute("data-tz"); if(tz) el.textContent=wcFormatTime(wcParts(tz,now),wcHourMode());
  });
}
function addWorldClock(){
  const pick=$("wcPick"); const tz=pick&&pick.value;
  if(!tz) return;
  if(!WC_ZONES.includes(tz)) WC_ZONES.push(tz);
  saveNew("wb_world_clock",WC_ZONES); renderWorldClock();
}
function delWorldClock(tz){
  if(tz===WC_PIN){notify("本地城市固定保留，用来做日期换算基准。","warn");return;}
  WC_ZONES=WC_ZONES.filter(x=>x!==tz); delete WC_NOTES[tz];
  WC_CANDIDATES=WC_CANDIDATES.filter(x=>x!==tz);
  saveNew("wb_world_clock",WC_ZONES); saveNew(WC_NOTES_KEY,WC_NOTES);
  saveNew(WC_CANDIDATE_KEY,WC_CANDIDATES); renderWorldClock();
}
function resetWorldClock(){
  WC_ZONES=WC_DEFAULT.slice(); WC_CANDIDATES=[];
  saveNew("wb_world_clock",WC_ZONES); saveNew(WC_CANDIDATE_KEY,WC_CANDIDATES); renderWorldClock();
}
function setWorldClockCandidate(slot,tz){
  if(![1,2].includes(slot)||tz===WC_PIN||!WC_ZONES.includes(tz)) return;
  const next=WC_CANDIDATES.slice(0,2);
  const other=next[slot===1?1:0];
  if(tz===other) next[slot===1?1:0]=next[slot-1]||"";
  next[slot-1]=tz;
  WC_CANDIDATES=next.filter((x,i,a)=>x&&a.indexOf(x)===i).slice(0,2);
  saveNew(WC_CANDIDATE_KEY,WC_CANDIDATES); renderWorldClock();
}
function setWorldClockHourMode(hour12){
  saveNew("wb_world_clock_12h",!!hour12); renderWorldClock();
}
function setWorldClockSort(mode){
  WC_SORT=["order","time","offset"].includes(mode)?mode:"order";
  saveNew("wb_world_clock_sort",WC_SORT); renderWorldClock();
}

/* —— 自媒体运营：混合数据快照只读；今天发没发由平台回执或人工核验决定 —— */
const MEDIA_STATUS={unknown:["未核验","warn"],planned:["待发布","acc"],published:["已发布","ok"],skip:["今日不发",""]};
const MEDIA_DAILY_IDS=["wechat","zsxq"];
const MEDIA_PLAN_PRIORITY={wechat:0,zsxq:1,x:2,xiaohongshu:3};
function mediaSortedPlan(plan){
  return (plan||[]).slice().sort((a,b)=>
    (MEDIA_PLAN_PRIORITY[a.id]??99)-(MEDIA_PLAN_PRIORITY[b.id]??99));
}
/* 新鲜度只看数据代表的最后日期，不信任采集端写死的 freshness。
   连通不等于新鲜：公众号可以 API 连通，但 mpdt 快照仍停在几个月前。 */
function mediaEffectiveFreshness(a, now=new Date()){
  const asOf=String((a&&a.asOf)||"").slice(0,10);
  if(!/^\d{4}-\d{2}-\d{2}$/.test(asOf))
    return {state:"missing",days:null,label:"不可用",tone:"warn"};
  const [year,month,day]=asOf.split("-").map(Number);
  const asOfMs=Date.parse(asOf+"T00:00:00Z");
  if(!Number.isFinite(asOfMs))
    return {state:"missing",days:null,label:"不可用",tone:"warn"};
  const parsed=new Date(asOfMs);
  if(parsed.getUTCFullYear()!==year||parsed.getUTCMonth()+1!==month||parsed.getUTCDate()!==day)
    return {state:"missing",days:null,label:"不可用",tone:"warn"};
  const nowDate=now instanceof Date?now:new Date(now);
  const shanghaiDay=new Date(nowDate.getTime()+8*3600000).toISOString().slice(0,10);
  const age=Math.max(0,Math.round((
    Date.parse(shanghaiDay+"T00:00:00Z")-asOfMs
  )/86400000));
  if(age<=1) return {state:"fresh",days:age,label:"新鲜",tone:"ok"};
  if(age<=7) return {state:"aging",days:age,label:"待更新",tone:"warn"};
  return {state:"stale",days:age,label:`过期 ${age} 天`,tone:"warn"};
}
function mediaFreshnessCopy(a, fresh=mediaEffectiveFreshness(a)){
  if(!a||!a.asOf) return "暂无可用数据日期";
  if(fresh.days==null) return "数据日期不可用";
  const ageCopy=fresh.state==="stale"?`过期 ${fresh.days} 天`:
    fresh.days===0?"今天":`${fresh.days} 天前`;
  return `数据截至 ${String(a.asOf).slice(0,10)} · ${ageCopy}`;
}
function webPresenceStatus(w){
  if(!w) return {label:"未加载",tone:"warn",issues:0};
  const sites=Array.isArray(w.sites)?w.sites:[], alerts=Array.isArray(w.alerts)?w.alerts:[];
  const down=sites.filter(s=>s&&s.state==="down").length;
  const slow=sites.filter(s=>s&&s.state==="slow").length;
  const checking=sites.filter(s=>s&&s.state==="checking").length;
  const other=sites.filter(s=>s&&!["ok","down","slow","checking"].includes(s.state)).length;
  if(alerts.length) return {label:`${alerts.length} 条提醒`,tone:"warn",issues:alerts.length};
  if(down) return {label:`${down} 个站点异常`,tone:"dead",issues:down};
  if(slow) return {label:`${slow} 个站点偏慢`,tone:"warn",issues:slow};
  if(checking) return {label:`${checking} 个站点复检中`,tone:"warn",issues:checking};
  if(!sites.length) return {label:"待检测",tone:"warn",issues:0};
  if(other) return {label:`${sites.length-other}/${sites.length} 正常`,tone:"warn",issues:other};
  return {label:"正常",tone:"ok",issues:0};
}

function renderMediaPulse(source=social,presence=webPresence){
  const plan=mediaSortedPlan(source&&source.todayPlan||[]);
  const accounts=(source&&source.accounts)||[];
  const dailyTile=(id,label)=>{
    const p=plan.find(x=>x.id===id)||{id,platform:label,defaultStatus:"unknown"};
    const a=accounts.find(x=>x.platform===label)||null;
    const st=mediaStatus(p.id,p.defaultStatus), statusMeta=MEDIA_STATUS[st]||MEDIA_STATUS.unknown;
    const fresh=mediaEffectiveFreshness(a);
    let copy=mediaFreshnessCopy(a,fresh);
    if(a&&a.connectionState==="blocked") copy="API 被 IP 白名单拦截";
    else if(a&&a.connectionState==="limited") copy="统计权限不足 · "+copy;
    const settled=st==="published"||st==="skip";
    const publishWarn=!settled;
    const dataWarn=fresh.state!=="fresh"||!!(a&&["blocked","limited"].includes(a.connectionState));
    return `<div class="media-pulse-item ${publishWarn?"is-warn":"is-ok"}">`+
      `<i class="media-pulse-dot" aria-hidden="true"></i><span class="media-pulse-title">${esc(label)}</span>`+
      `<strong class="media-pulse-value">${esc(statusMeta[0])}</strong><span class="media-pulse-copy${dataWarn?" is-data-warn":""}">${esc(copy)}</span></div>`;
  };
  const sites=(presence&&presence.sites)||[];
  const blog=(presence&&presence.blog&&presence.blog.latest)||{};
  const siteState=webPresenceStatus(presence);
  const siteOk=sites.filter(x=>x.state==="ok").length;
  const siteValue=siteState.label;
  const blogCopy=blog.publishedAt?`博客 ${String(blog.publishedAt).slice(0,10)}`:"博客更新时间待读取";
  const siteCopy=!presence?"网站监控数据不可用":sites.length?`${siteOk}/${sites.length} 个站点正常 · ${blogCopy}`:`尚未配置站点 · ${blogCopy}`;
  const siteWarn=siteState.tone!=="ok";
  return dailyTile("wechat","公众号")+dailyTile("zsxq","知识星球")+
    `<div class="media-pulse-item ${siteWarn?"is-warn":"is-ok"}">`+
    `<i class="media-pulse-dot" aria-hidden="true"></i><span class="media-pulse-title">网站与博客</span>`+
    `<strong class="media-pulse-value">${esc(siteValue)}</strong><span class="media-pulse-copy">${esc(siteCopy)}</span></div>`;
}
function mediaPlanKey(){return "wb_media_plan_"+todayStr();}
function loadMediaPlan(){
  try{const x=JSON.parse(localStorage.getItem(mediaPlanKey())||"{}");return x&&typeof x==="object"?x:{};}catch(e){return {};}
}
let MEDIA_PLAN=loadMediaPlan();
function mediaStatus(id,def){return MEDIA_PLAN[id]||def||"unknown";}
/* 把今日备注转成推文格式复制到剪贴板：纯文本（markdown 符号剥掉）、
   超 280 字符截到最近一个完整句/词边界并加省略号。只复制，不发布。 */
function tweetText(){
  const raw=String((state.notes||{})[todayStr()]||"").trim();
  if(!raw) return "";
  const plain=raw.replace(/^#{1,6}\s+/gm,"").replace(/\*\*?([^*]+)\*\*?/g,"$1")
    .replace(/`([^`]+)`/g,"$1").replace(/^\s*[-*]\s+/gm,"· ").trim();
  if(plain.length<=280) return plain;
  const cut=plain.slice(0,277);
  const boundary=Math.max(cut.lastIndexOf("。"),cut.lastIndexOf("，"),cut.lastIndexOf(" "),cut.lastIndexOf("\n"));
  return (boundary>140?cut.slice(0,boundary+1):cut).trimEnd()+"…";
}
async function copyAsTweet(){
  const text=tweetText();
  if(!text){ notify("今日备注是空的，先在总览写一段今天要发的内容。","warn"); return; }
  try{
    await navigator.clipboard.writeText(text);
    notify("✓ 已按推文格式复制（"+text.length+" 字符），粘贴到 X 即可","info");
  }catch(e){ notify("复制失败："+e.message,"warn"); }
}
function cycleMediaStatus(id){
  const seq=["unknown","planned","published","skip"],cur=mediaStatus(id,"unknown");
  MEDIA_PLAN[id]=seq[(seq.indexOf(cur)+1)%seq.length];
  try{localStorage.setItem(mediaPlanKey(),JSON.stringify(MEDIA_PLAN));}catch(e){}
  renderContent();
}
function mediaMetrics(s){
  s=s||{}; const plan=s.todayPlan||[], auto=s.automation||[], posts=s.posts||[];
  return {platforms:(s.accounts||[]).length,posts:posts.length,
    unknown:plan.filter(p=>mediaStatus(p.id,p.defaultStatus)==="unknown").length,
    published:plan.filter(p=>mediaStatus(p.id,p.defaultStatus)==="published").length,
    draftAdapters:auto.filter(a=>a.state!=="missing").length};
}
function mediaReminders(s){
  if(!s) return [{tone:"warn",title:"自媒体数据不可用",detail:socialErr||"还没有生成 social.json。"}];
  const out=[], plan=s.todayPlan||[], unknown=plan.filter(p=>mediaStatus(p.id,p.defaultStatus)==="unknown");
  const wechat=(s.accounts||[]).find(a=>a.platform==="公众号");
  const zsxq=(s.accounts||[]).find(a=>a.platform==="知识星球");
  const wechatFresh=mediaEffectiveFreshness(wechat);
  if(wechat&&wechat.connectionState==="blocked") out.push({tone:"warn",title:"先恢复公众号数据",detail:"凭据已配置，但当前出口 IP 不在公众号 API 白名单。打开后台处理白名单后，下一次同步会回填阅读、分享和关注数据。"});
  if(wechat&&wechat.connectionState==="limited") out.push({tone:"warn",title:wechat.verified===false?"公众号已连接，但账号尚未认证":"公众号已连接，但统计权限不足",detail:(wechat.permissionReason||"当前账号没有文章统计、用户分析或已发布内容接口权限。")+" 已读取素材库内容；缺失的阅读、分享和关注值不会伪装成 0。"});
  if(wechat&&wechatFresh.state==="stale"&&!["blocked","limited"].includes(wechat.connectionState))
    out.push({tone:"warn",title:`公众号数据已过期 ${wechatFresh.days} 天`,
      detail:`数据只到 ${wechat.asOf||"未知日期"}。即使采集通道可用，也不能把旧快照当成今天的阅读与关注表现；先刷新 mpdt 或恢复官方 API。`});
  const zsFresh=mediaEffectiveFreshness(zsxq);
  if(zsxq&&zsFresh.state==="stale") out.push({tone:"warn",title:`知识星球数据已过期 ${zsFresh.days} 天`,
    detail:`数据只到 ${zsxq.asOf||"历史日期"}。你星球是日更，超过 7 天不更新，工作台的阅读分析就会整体失真。今天仍可做发布核验；接入实时只读或重新导出后即可回填。`});
  if(unknown.length) out.push({tone:"warn",title:"今天还有 "+unknown.length+" 个平台未核验",detail:unknown.map(x=>x.platform).join("、")+"：先确认是否计划发布，再标记状态。"});
  const x=(s.accounts||[]).find(a=>a.platform==="X");
  if(x&&mediaEffectiveFreshness(x).state!=="fresh") out.push({tone:"warn",title:"X 数据已过期",detail:"现有证据停在 "+(x.asOf||"未知日期")+"。先从 X Analytics 导出最近 30 天逐条 CSV，原创与转发分开。"});
  const missing=(s.accounts||[]).filter(a=>mediaEffectiveFreshness(a).state==="missing").map(a=>a.platform);
  if(missing.length) out.push({tone:"warn",title:"还缺逐条指标",detail:missing.join("、")+" 尚未接入阅读、互动、点击与关注转化，因此暂不做内容好坏判断。"});
  const review=(s.automation||[]).filter(a=>a.state==="review");
  if(review.length) out.push({tone:"",title:"发布自动化需要人工闸门",detail:review.map(a=>a.platform).join("、")+" 的现有入口不应直接自动发布；Agent 负责分析和草稿，最终发送由你确认。"});
  if(webPresence&&(webPresence.alerts||[]).length){
    const a=webPresence.alerts[0]; out.unshift({tone:a.tone||"warn",title:a.title,detail:a.detail});
  }
  if(!out.length) out.push({tone:"",title:"今天没有待处理提醒",detail:"平台状态均已核验，等下一轮数据回收。"});
  return out;
}
function mediaFocusPriority(accounts){
  const focusAccounts=(accounts||[]).filter(a=>["公众号","知识星球"].includes(a.platform));
  return focusAccounts.find(a=>a.connectionState==="blocked")||
    focusAccounts.find(a=>a.connectionState==="limited")||
    focusAccounts.find(a=>mediaEffectiveFreshness(a).state==="stale")||null;
}
function outLink(url,label,cls){
  const u=safeUrl(url); return u?`<a${cls?` class="${esc(cls)}"`:""} href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`:"";
}
function mediaAccountSummary(a){
  const m=a.metrics||{}, p=a.platform||"未命名";
  if(p==="X") return {value:m.followers??"待接入",label:m.followers==null?"关注者未核验":"关注者",
    sub:[["发帖",m.posts],[m.recentPublicViews!=null?"近样本公开浏览":"近 30 天展示",m.recentPublicViews??m.last30DayImpressions]]};
  if(p==="小红书") return {value:m.followers??"待接入",label:m.followers==null?"粉丝未核验":"粉丝",
    sub:[["内容",m.posts],["获赞与收藏",m.interactions]]};
  if(p==="公众号"){
    // mpdt 通道：文章数是主指标；官方 API 通道退回关注者/素材口径。
    if(m.samplePosts!=null) return {value:m.samplePosts, label:"文章统计",
      sub:[["平均阅读",m.avgReads],["完读明细",m.detailPosts||(m.detailPosts===0?0:null)],["平均完读率",m.avgCompletion!=null?(m.avgCompletion*100).toFixed(0)+"%":null]]};
    return {value:m.followers??m.materialArticles??"待接入",
      label:m.followers!=null?"关注者":m.materialArticles!=null?"可读素材":"关注者未核验",
      sub:[["账号类型",a.accountType],["昨日阅读",m.yesterdayReads],["昨日分享",m.yesterdayShares],["已发布",m.publishedArticles],["素材文章",m.materialArticles]]};
  }
  if(p==="知识星球"){
    // 实时聚合通道：星球数 + 话题数是主指标；无 token 时退回历史导出口径。
    if(m.groups!=null){
      const per=m.perGroup||{};
      const top=Object.entries(per).sort((x,y)=>y[1]-x[1]).slice(0,2);
      return {value:m.posts??"待接入",label:"近 30 条话题",
        sub:[["聚合星球",m.groups],...top.map(([name,count])=>[name,count])]};
    }
    return {value:m.posts??"待接入",label:m.posts==null?"内容未核验":"历史内容",
      sub:[["近 30 个导出日",m.postsInLast30ExportDays],["41 条样本阅读",m.readsSample],
        ["历史互动",m.likes!=null&&m.comments!=null?m.likes+m.comments:null]]};
  }
  return {value:"待接入",label:"暂无账号指标",sub:[["状态","仅有草稿入口"]]};
}
