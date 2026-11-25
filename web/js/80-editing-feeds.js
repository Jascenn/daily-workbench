/* ---------- editing ---------- */
const PROJECT_OPTION_FALLBACK={
  status:["未开始","进行中","已延迟","已完成","已取消"],
  priority:["P0 - 最高","P1 - 高","P2 - 中","P3 - 低","P4 - 最低"],
  type:["产品开发","市场营销","研究调研","内部项目","客户服务","运营维护","培训活动","战略规划","内容教程"],
  sprint:["冲刺 1","冲刺 2","未规划","规划中"]
};
function projectEditOptions(key,current,emptyLabel="未标注"){
  const schema=(state&&state.notionMeta&&state.notionMeta.projectSchema)||{};
  let opts=Array.isArray(schema[key])?schema[key].slice():[];
  if(!opts.length) opts=(PROJECT_OPTION_FALLBACK[key]||[]).slice();
  const cur=String(current||"");
  if(cur&&!opts.includes(cur)) opts.unshift(cur);
  return `<option value="">${esc(emptyLabel)}</option>`+
    opts.map(v=>`<option value="${esc(v)}" ${v===cur?"selected":""}>${esc(v)}</option>`).join("");
}
const projectRelationCtx={
  owners:{selected:[],known:new Map()},
  task:{selected:[],known:new Map(),results:[],seq:0,timer:null,searched:false},
  record:{selected:[],known:new Map(),results:[],seq:0,timer:null,searched:false}
};
const PROJECT_RELATION_IDS={
  task:{selectedId:"pnTasksSelected",resultsId:"pnTasksResults",inputId:"pnTaskSearch"},
  record:{selectedId:"pnRecordsSelected",resultsId:"pnRecordsResults",inputId:"pnRecordSearch"}
};
function projectOwnerChoices(project){
  const choices=new Map();
  ((state&&state.projects)||[]).concat(project?[project]:[]).forEach(p=>{
    const ids=Array.isArray(p&&p.ownerIds)?p.ownerIds.filter(Boolean):[];
    if(!ids.length) return;
    const names=String((p&&p.owner)||"").split(/[、,，]/).map(x=>x.trim()).filter(Boolean);
    ids.forEach((id,i)=>{
      const key=String(id);
      if(choices.has(key)) return;
      choices.set(key,{
        id:key,
        title:names.length===ids.length&&names[i]?names[i]:("成员 "+key.slice(0,8))
      });
    });
  });
  return [...choices.values()];
}
function renderProjectOwners(project){
  const box=$("pnOwners"); if(!box) return;
  projectRelationCtx.owners.known.clear();
  projectOwnerChoices(project).forEach(x=>projectRelationCtx.owners.known.set(String(x.id),x));
  projectRelationCtx.owners.selected=projectRelationCtx.owners.selected
    .filter(id=>projectRelationCtx.owners.known.has(String(id)));
  box.innerHTML=projectRelationCtx.owners.known.size
    ? [...projectRelationCtx.owners.known.values()].map(x=>{
        const on=projectRelationCtx.owners.selected.includes(String(x.id));
        return `<button type="button" class="relchoice${on?" on":""}" aria-pressed="${on}"
          onclick="toggleProjectOwner('${esc(x.id)}')">${esc(x.title)}</button>`;
      }).join("")
    : `<span class="relhint">暂无可选成员。先在 Notion 项目中填写负责人，下一次同步后这里会自动出现。</span>`;
}
function toggleProjectOwner(id){
  const key=String(id);
  const at=projectRelationCtx.owners.selected.indexOf(key);
  if(at>=0) projectRelationCtx.owners.selected.splice(at,1);
  else projectRelationCtx.owners.selected.push(key);
  renderProjectOwners(((state&&state.projects)||[]).find(p=>String(p.id)===String(projectWriteId)));
}
function projectRelationLabel(kind,id){
  const ctx=projectRelationCtx[kind];
  const item=ctx&&ctx.known.get(String(id));
  return item||{id:String(id),title:"页面 "+String(id).slice(0,8),status:"",date:""};
}
function renderProjectRelationPicker(kind){
  const ids=PROJECT_RELATION_IDS[kind], ctx=projectRelationCtx[kind];
  if(!ids||!ctx) return;
  const selected=$(ids.selectedId);
  if(selected){
    selected.innerHTML=ctx.selected.length?ctx.selected.map(id=>{
      const item=projectRelationLabel(kind,id);
      const meta=[item.status,item.date].filter(Boolean).join(" · ");
      return `<span class="relchip"><b title="${esc(item.title)}">${esc(item.title)}</b>`+
        (meta?`<small>${esc(meta)}</small>`:"")+
        `<button type="button" aria-label="移除 ${esc(item.title)}" onclick="removeProjectRelation('${kind}','${esc(id)}')">✕</button></span>`;
    }).join(""):`<span class="relhint">未选择</span>`;
  }
  const results=$(ids.resultsId);
  if(results){
    const rows=ctx.results.filter(x=>!ctx.selected.includes(String(x.id)));
    results.className="relresults"+(ctx.searched?" show":"");
    results.innerHTML=ctx.searched
      ? (rows.length?rows.map(x=>`<button type="button" class="relopt" onclick="addProjectRelation('${kind}','${esc(x.id)}')">
          <span>${esc(x.title)}</span><small>${esc([x.status,x.date].filter(Boolean).join(" · "))}</small></button>`).join("")
        : `<div class="relhint" style="padding:10px">没有匹配项</div>`)
      : "";
  }
}
async function resolveProjectRelations(kind,ids){
  if(!ids.length) return;
  const ctx=projectRelationCtx[kind];
  ids.forEach(id=>{ if(!ctx.known.has(String(id))) ctx.known.set(String(id),{id:String(id),title:"页面 "+String(id).slice(0,8)}); });
  renderProjectRelationPicker(kind);
  try{
    const r=await fetch(`/api/projects/relations?kind=${encodeURIComponent(kind)}&ids=${encodeURIComponent(ids.join(","))}`);
    const j=await r.json();
    if(r.ok&&Array.isArray(j.items)) j.items.forEach(item=>ctx.known.set(String(item.id),item));
  }catch(_){/* 已有 ID 回显；标题解析失败不阻断保存 */}
  renderProjectRelationPicker(kind);
}
function scheduleProjectRelationSearch(kind,query){
  const ctx=projectRelationCtx[kind]; if(!ctx) return;
  clearTimeout(ctx.timer);
  ctx.timer=setTimeout(()=>searchProjectRelations(kind,query),220);
}
async function searchProjectRelations(kind,query){
  const ctx=projectRelationCtx[kind]; if(!ctx) return;
  const q=String(query||"").trim();
  if(!q){
    ctx.searched=false; ctx.results=[]; renderProjectRelationPicker(kind); return;
  }
  const seq=++ctx.seq;
  try{
    const r=await fetch(`/api/projects/relations?kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(q)}`);
    const j=await r.json();
    if(seq!==ctx.seq) return;
    ctx.results=r.ok&&Array.isArray(j.items)?j.items:[];
    ctx.results.forEach(item=>ctx.known.set(String(item.id),item));
  }catch(_){
    if(seq!==ctx.seq) return;
    ctx.results=[];
  }
  ctx.searched=true;
  renderProjectRelationPicker(kind);
}
function addProjectRelation(kind,id){
  const ctx=projectRelationCtx[kind]; if(!ctx) return;
  const key=String(id);
  if(!ctx.selected.includes(key)) ctx.selected.push(key);
  ctx.searched=false; ctx.results=[];
  const input=$(PROJECT_RELATION_IDS[kind].inputId); if(input) input.value="";
  renderProjectRelationPicker(kind);
}
function removeProjectRelation(kind,id){
  const ctx=projectRelationCtx[kind]; if(!ctx) return;
  ctx.selected=ctx.selected.filter(x=>String(x)!==String(id));
  renderProjectRelationPicker(kind);
}
let projectWriteId="";
function openProjectWriteback(id){
  const p=((state&&state.projects)||[]).find(x=>String(x.id)===String(id));
  if(!p||!p.notionId){notify("这个项目没有 Notion 页面标识，无法回写。","warn");return;}
  projectWriteId=String(id);
  $("projNotionSubtitle").textContent=`${p.name} · 保存后直接回写 Notion「项目管理」`;
  $("pnName").value=p.name||"";
  $("pnStatus").innerHTML=projectEditOptions("status",p.status);
  $("pnPriority").innerHTML=projectEditOptions("priority",p.priority);
  $("pnType").innerHTML=projectEditOptions("type",p.projectType||(!p.tags&&p.category)||"");
  $("pnSprint").innerHTML=projectEditOptions("sprint",p.sprint);
  $("pnTags").value=(p.tags||[]).join(", ");
  $("pnStart").value=p.start||"";
  $("pnDue").value=p.due||"";
  $("pnDone").value=p.doneDate||"";
  $("pnSprintStart").value=p.sprintStart||"";
  $("pnSprintEnd").value=p.sprintEnd||"";
  $("pnEstHours").value=(typeof p.estHours==="number")?p.estHours:"";
  $("pnActHours").value=(typeof p.actHours==="number")?p.actHours:"";
  $("pnLink").value=p.relatedLink||"";
  $("pnNote").value=p.note||"";
  const parentNid=p.parentNotionId||((state.projects||[]).find(x=>x.id===p.parent)||{}).notionId||"";
  $("pnParent").innerHTML=`<option value="">无上级项目</option>`+
    (state.projects||[]).filter(x=>x.notionId&&x.notionId!==p.notionId)
      .map(x=>`<option value="${esc(x.notionId)}" ${String(x.notionId)===String(parentNid)?"selected":""}>${esc(x.name)}</option>`).join("");
  projectRelationCtx.owners.selected=(p.ownerIds||[]).map(String);
  projectRelationCtx.owners.known.clear();
  for(const kind of ["task","record"]){
    const ctx=projectRelationCtx[kind];
    ctx.selected=(kind==="task"?(p.relatedTaskIds||[]):(p.sourceRecordIds||[])).map(String);
    ctx.known.clear();
    ctx.results=[]; ctx.searched=false; ctx.timer=null; ctx.seq++;
    const input=$(PROJECT_RELATION_IDS[kind].inputId); if(input) input.value="";
    renderProjectRelationPicker(kind);
  }
  renderProjectOwners(p);
  resolveProjectRelations("task",projectRelationCtx.task.selected);
  resolveProjectRelations("record",projectRelationCtx.record.selected);
  const relationBits=[];
  if((p.childIds||[]).length||Number.isFinite(+p.childCount)) relationBits.push(`子项目 ${(p.childIds||[]).length||(+p.childCount||0)} 项`);
  $("projNotionBoundary").innerHTML=
    `完成百分比由 Notion rollup / 公式计算，不在工作台修改。`+
    (relationBits.length?`<br>${esc(relationBits.join(" · "))}；子项目层级由 Notion 双关联维护，工作台只读展示。`:"");
  $("projWriteState").textContent="";
  $("projWriteState").className="client-edit-state";
  openOv("projNotionOv");
}
function submitProjectWriteback(ev){
  if(ev&&ev.preventDefault) ev.preventDefault();
  saveProjectWriteback();
  return false;
}
async function saveProjectWriteback(){
  if(guest){notify("访客模式不会回写 Notion。","warn");return;}
  if(!SERVED){notify("回写需要本地同步服务（127.0.0.1:8787）。","warn");return;}
  if(!TOKEN){notify("链接缺少访问令牌，无法回写 Notion。","err");return;}
  const p=(state.projects||[]).find(x=>String(x.id)===String(projectWriteId));
  if(!p||!p.notionId){notify("找不到要回写的 Notion 项目。","err");return;}
  const val=id=>String(($(id)&&$(id).value)||"").trim();
  const num=id=>{const v=val(id);return v===""?null:Number(v);};
  const tags=val("pnTags").split(/[,，]/).map(x=>x.trim()).filter(Boolean)
    .filter((x,i,a)=>a.indexOf(x)===i);
  const fields={
    name:val("pnName"),
    status:val("pnStatus"),
    priority:val("pnPriority"),
    projectType:val("pnType"),
    tags,
    start:val("pnStart"),
    due:val("pnDue"),
    doneDate:val("pnDone"),
    sprint:val("pnSprint"),
    sprintStart:val("pnSprintStart"),
    sprintEnd:val("pnSprintEnd"),
    estHours:num("pnEstHours"),
    actHours:num("pnActHours"),
    relatedLink:val("pnLink"),
    parentNotionId:val("pnParent"),
    ownerIds:projectRelationCtx.owners.selected.slice(),
    relatedTaskIds:projectRelationCtx.task.selected.slice(),
    sourceRecordIds:projectRelationCtx.record.selected.slice(),
    note:val("pnNote")
  };
  const stateBox=$("projWriteState"), btn=$("projWriteSave");
  if(stateBox){stateBox.className="client-edit-state";stateBox.textContent="正在写入 Notion…";}
  if(btn){btn.disabled=true;btn.textContent="正在回写…";}
  try{
    const r=await fetch("/api/projects/update",{
      method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({pageId:p.notionId,fields})
    });
    const j=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    const idx=state.projects.findIndex(x=>String(x.id)===String(projectWriteId));
    if(idx>=0&&j.project){
      state.projects[idx]={...state.projects[idx],...j.project};
      if(fields.ownerIds.length){
        state.projects[idx].owner=fields.ownerIds
          .map(id=>projectRelationLabel("owners",id).title).filter(Boolean).join("、");
      }else{
        state.projects[idx].owner="";
      }
      state.projects[idx].category=(state.projects[idx].tags||[])[0]||state.projects[idx].projectType||"";
    }
    cacheLocal(state);
    renderProject();
    closeOv("projNotionOv");
    notify(j.localUpdated===false
      ? "Notion 已更新，但本地 data.json 未落盘；下一次同步会补齐。"
      : "已回写 Notion，并同步更新本地项目卡。",j.localUpdated===false?"warn":"info");
  }catch(e){
    if(stateBox){stateBox.className="client-edit-state err";stateBox.textContent="回写失败："+e.message;}
    notify("项目回写失败："+e.message,"err");
  }finally{
    if(btn){btn.disabled=false;btn.textContent="保存并回写 Notion";}
  }
}
function openProj(){uid="";$("projTitle").textContent="添加项目";
  $("pName").value="";$("pStatus").value="进行中";$("pProg").value=0;$("pCat").value="";$("pDue").value="";$("pNote").value="";
  openOv("projOv");}
function editProject(id){const p=state.projects.find(x=>x.id===id);if(!p)return;
  if(p.readOnly){notify("该项目来自 Notion（只读拉取），不可在此编辑。\n如需修改，请到 Notion 里改，然后说「同步」即可更新。","warn");return;}
  uid=id;
  $("projTitle").textContent="编辑项目";$("pName").value=p.name;$("pStatus").value=p.status;$("pProg").value=p.progress;
  $("pCat").value=p.category;$("pDue").value=p.due;$("pNote").value=p.note;openOv("projOv");}
/* 两个模态都包了 <form>：这样 Enter 提交、Esc 关闭是浏览器/平台自带的行为，
   不必给每个输入框单独绑 keydown。
   注意 preventDefault 必须留着：不拦的话表单会走默认的 GET 提交把页面刷掉，
   而这一页的状态全在内存里，刷新等于把用户刚填的东西丢掉。 */
function submitProj(e){ if(e&&e.preventDefault) e.preventDefault(); saveProj(); return false; }
function submitEarn(e){ if(e&&e.preventDefault) e.preventDefault(); saveEarn(); return false; }
function saveProj(){const name=$("pName").value.trim();clearFieldErr("pName");if(!name){fieldErr("pName","请填名称");return;}
  const data={name,status:$("pStatus").value,progress:Math.max(0,Math.min(100,+$("pProg").value||0)),category:$("pCat").value,due:$("pDue").value,note:$("pNote").value,updatedAt:todayStr()+"T"+new Date().toTimeString().slice(0,8)};
  if(uid){const p=state.projects.find(x=>x.id===uid);Object.assign(p,data);}else{state.projects.push({id:uidGen(),...data});}
  closeOv();saveState();renderAll();}
function openEarn(){uid="";$("earnTitle").textContent="记一笔收益";
  $("eDate").value=todayStr();$("eAmt").value="";$("eNote").value="";
  fillTypeOptions();$("eType").value="项目收入";
  const sel=$("eProj");sel.innerHTML='<option value="">（无）</option>'+state.projects.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
  sel.value="";
  openOv("earnOv");}
/* 从收益明细点进来改一条本地草稿（Notion 只读行已在 renderLedger 里屏蔽入口） */
function editEarn(id){
  if(guest) return;
  const e=state.earnings.find(x=>x.id===id);
  if(!e) return;
  if(e.readOnly){notify("这条来自 Notion（只读拉取），不能在此编辑。\n如需调整，请到 Notion 里改，然后说「同步」即可更新。","warn");return;}
  uid=id;
  $("earnTitle").textContent="编辑收益";
  $("eDate").value=e.date||todayStr();
  $("eAmt").value=Math.abs(+e.amount||0);   // 支出在存储里是负数；输入框显示绝对值，符号由类型决定
  $("eNote").value=e.note||"";
  fillTypeOptions();
  $("eType").value=(typeOptions().indexOf(e.type)>=0? e.type : "项目收入");
  const sel=$("eProj");
  sel.innerHTML='<option value="">（无）</option>'+state.projects.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
  sel.value=e.projectId||"";
  if(sel.value!==(e.projectId||"")) sel.value="";   // 关联项目已被删掉时回落「无」，避免静默挂到别的项目上
  openOv("earnOv");
}
function saveEarn(){const date=$("eDate").value||todayStr();
  clearFieldErr("eAmt");
  if(!$("eAmt").value){fieldErr("eAmt","请填金额");return;}
  const type=$("eType").value;
  let amt=+$("eAmt").value;
  // 口径统一：支出必须为负、收入必须为正，避免符号与分类互相矛盾污染统计
  if(type===EXPENSE_LABEL){ amt=-Math.abs(amt); }
  else if(amt<0){ fieldErr("eAmt","金额为负数，但类型不是「支出」。请改选「支出」或把金额改成正数。"); return; }
  // 本地草稿：仅存到本地 data.json，不回写 Notion（Notion 来源为只读）
  const data={date,amount:amt,type,projectId:$("eProj").value,note:$("eNote").value,
    product:(type===EXPENSE_LABEL?"支出":""),updatedAt:todayStr()+"T"+new Date().toTimeString().slice(0,8)};
  if(uid){
    const e=state.earnings.find(x=>x.id===uid);
    if(e){
      if(e.readOnly){notify("这条来自 Notion（只读），不能修改。","warn");closeOv();return;}
      Object.assign(e,data);                        // 原位更新：不新增记录，不改 date 顺序以外的字段
    }else{
      state.earnings.push({id:uidGen(),...data,source:"local",readOnly:false});  // 记录已被删，按新增处理
    }
  }else{
    state.earnings.push({id:uidGen(),...data,source:"local",readOnly:false});
  }
  closeOv();saveState();renderAll();}
function delEarn(id){if(guest)return;
  const idx=state.earnings.findIndex(x=>x.id===id);
  if(idx<0) return;
  const e=state.earnings[idx];
  if(e.readOnly){notify("这条来自 Notion（只读拉取），不能在此删除。\n如需调整，请到 Notion 里改，然后说「同步」即可更新。","warn");return;}
  // 记下原索引：撤销时要插回原来的位置，否则记录顺序变了、按日期排序的视图会跳
  state.earnings.splice(idx,1);
  lastDel={rec:e, idx:idx};
  saveState();renderAll();showUndo(e);}
/* 模态焦点管理：aria-modal 只告诉辅助技术"背景不参与"，浏览器不会自动限制 Tab。
   这里统一处理三件事：打开后聚焦首个可操作项、Tab 在模态内循环、关闭后回到触发按钮。 */
const modalReturnFocus=new Map();
function modalRoot(ov){ return ov && (ov.querySelector('[role="dialog"]')||ov); }
function modalFocusable(ov){
  const root=modalRoot(ov); if(!root) return [];
  const sel='a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),'+
    'select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
  return [...root.querySelectorAll(sel)].filter(el=>{
    if(el.hidden||el.getAttribute("aria-hidden")==="true"||el.closest("[inert]")) return false;
    return !!(el.offsetWidth||el.offsetHeight||el.getClientRects().length);
  });
}
function focusModalStart(ov){
  const root=modalRoot(ov); if(!root) return;
  const preferred=root.querySelector('[autofocus],[data-autofocus]')||
    modalFocusable(ov).find(el=>/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName))||
    modalFocusable(ov)[0]||root;
  try{preferred.focus({preventScroll:true});}catch(e){try{preferred.focus();}catch(err){}}
}
function syncModalOpenState(){
  document.body.classList.toggle("modal-open",!!document.querySelector(".ov.show"));
}
function closeOv(id){
  const writingOpen=!!$("writingEditorOv")&&$("writingEditorOv").classList.contains("show");
  if((id==="writingEditorOv"||(!id&&writingOpen))&&!writingEditorCanClose()) return;
  const activeOv=document.activeElement&&document.activeElement.closest?
    document.activeElement.closest(".ov.show"):null;
  const ids=id?[id]:[...document.querySelectorAll(".ov.show")].map(o=>o.id);
  if(ids.includes("newsReaderOv")&&$("readerImageOv")&&!ids.includes("readerImageOv")){
    ids.push("readerImageOv");
  }
  if((!id||ids.includes("newsReaderOv"))&&newsReaderAbort){
    newsReaderAbort.abort();
    newsReaderAbort=null;
  }
  ids.forEach(ovId=>{
    const o=$(ovId);
    if(!o) return;
    o.classList.remove("show");
    if(ovId==="readerImageOv") o.setAttribute("aria-hidden","true");
    if(ovId==="writingEditorOv") resetWritingEditor();
  });
  syncModalOpenState();
  if(document.querySelector(".ov.show")) return;
  const restoreId=id||(activeOv&&activeOv.id)||ids[ids.length-1];
  const target=restoreId&&modalReturnFocus.get(restoreId);
  if(restoreId) modalReturnFocus.delete(restoreId);
  if(target&&target.isConnected){
    setTimeout(()=>{try{target.focus({preventScroll:true});}catch(e){try{target.focus();}catch(err){}}},0);
  }
}
document.addEventListener("keydown",e=>{
  if(e.key!=="Tab") return;
  const open=[...document.querySelectorAll(".ov.show")];
  const ov=open[open.length-1]; if(!ov) return;
  const list=modalFocusable(ov);
  const root=modalRoot(ov);
  if(!list.length){
    e.preventDefault();
    try{root.focus({preventScroll:true});}catch(err){}
    return;
  }
  const first=list[0],last=list[list.length-1],active=document.activeElement;
  if(!ov.contains(active)){
    e.preventDefault();(e.shiftKey?last:first).focus();
  }else if(e.shiftKey&&active===first){
    e.preventDefault();last.focus();
  }else if(!e.shiftKey&&active===last){
    e.preventDefault();first.focus();
  }
});
/* 弹窗点遮罩空白处关闭：只有点在遮罩本身（不在 .modal 内部）才算"点在外面"。
   直接点面板里的输入框/按钮不应关窗，否则填一半的表单会被误关。 */
document.querySelectorAll(".ov").forEach(ov=>{
  ov.addEventListener("mousedown",e=>{
    if(e.target!==ov) return;
    if(ov.id==="readerImageOv") closeReaderImage();
    else closeOv(ov.id);
  });
});
/* 「更多」菜单：点面板外部或按 Esc 收起。原实现只靠再点一次 summary，点页面别处不关。 */
document.addEventListener("mousedown",e=>{
  const menu=document.querySelector(".actionmenu[open]");
  if(!menu) return;
  if(menu.contains(e.target)) return;
  menu.removeAttribute("open");
});

/* ── 订阅源探查 / 管理 ─────────────────────────────────────────────
   服务端 /api/feeds/*：GET 列表、POST 探查、收录、暂停/恢复、永久删除。
   写的是工作台自己的 rss-store.json（真源，首建时从 Qiaomu 插件导入），写完自动重跑 sections.py，
   前端再拉一次 sections.json —— 保证筛选下拉与健康表和真源一致。 */
let PROBE_HITS=[];
/* —— Jev 增强插件：设置入口在「更多」菜单；开着时自媒体页多一个
   「Jev 分析」块（对内容信号逐条打质量分），关着时页面完全走原逻辑。
   开关状态存服务端（~/.config/daily-workbench/jev-settings.json），刷新不丢。 */
let JEV={enabled:false,lastAnalysis:null,analyzedAt:null};
async function jevLoad(){
  if(guest){ JEV={enabled:false,lastAnalysis:null,analyzedAt:null}; jevBtnLabel(); return; }
  if(!SERVED||!TOKEN) return;
  try{
    const r=await fetch("/api/jev");
    if(!r.ok) return;
    const j=await r.json();
    JEV={enabled:!!j.enabled,lastAnalysis:j.lastAnalysis||null,analyzedAt:j.analyzedAt||null};
  }catch(e){}
  jevBtnLabel();
  if(curType==="content") renderContent();
}
function jevBtnLabel(){
  const b=$("jevBtn"); if(!b) return;
  if(guest){
    b.innerHTML=`<span class="sync-dot off" style="margin-right:5px"></span>Jev 增强 · 关`;
    b.style.fontWeight="inherit";
    return;
  }
  const label=(JEV.enabled?"开":"关");
  // 用 innerHTML 保留状态点；textContent 会在点被清掉后只剩文字，
  // 竞态下甚至可能整格空掉（用户截图见过一次），所以每次整体重写。
  // 注意不要加 .primary：本按钮带 .ghost，ghost 的 transparent 背景在
  // CSS 里排在 primary 之后，会把深色底覆盖掉 → 白字浮在白菜单上不可见。
  b.innerHTML=`<span class="sync-dot${JEV.enabled?" ok":" off"}" style="margin-right:5px" id="jevDot"></span>Jev 增强 · ${label}`;
  b.style.fontWeight=JEV.enabled?"750":"inherit";
}
async function toggleJev(){
  if(!SERVED||!TOKEN){ notify("Jev 增强需要本地同步服务。请用 127.0.0.1:8787 的带令牌链接打开。","warn"); return; }
  const next=!JEV.enabled;
  JEV.enabled=next; jevBtnLabel();   // 乐观更新：先改标签，请求失败再回滚
  try{
    const r=await fetch("/api/jev/toggle",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({enabled:next})});
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    notify(next?"✓ Jev 增强已开启：自媒体页的「内容信号」会多出 Jev 质量评分。":"Jev 增强已关闭：页面回到原来的展示。","info");
    if(curType==="content") renderContent();
  }catch(e){
    JEV.enabled=!next; jevBtnLabel();  // 回滚到点击前的状态
    notify("切换 Jev 增强失败："+e.message,"warn");
  }
}
async function jevAnalyze(){
  if(!JEV.enabled){ notify("先在「更多」菜单里打开 Jev 增强。","warn"); return; }
  try{
    const r=await fetch("/api/jev/analyze",{method:"POST"});
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    JEV.lastAnalysis=j.results||null; JEV.analyzedAt=j.analyzedAt||null;
    notify("✓ Jev 分析完成","info");
    if(curType==="content") renderContent();
  }catch(e){ notify("Jev 分析失败："+e.message,"warn"); }
}
function jevBlock(){
  if(!JEV.enabled) return "";
  const list=(JEV.lastAnalysis||[]);
  const scored=list.filter(r=>!r.error&&r.score!=null);
  const hi=scored.length?Math.max(...scored.map(r=>+r.score)):null;
  const lo=scored.length?Math.min(...scored.map(r=>+r.score)):null;
  const rows=list.map(r=>{
    if(r.error) return `<div class="jev-row"><span class="jt">${esc(r.title||"(未命名)")}</span>`+
      `<span class="jscore"><span class="jnum low">失败</span></span><span class="jusage">${esc(r.error)}</span></div>`;
    const score=Math.max(0,Math.min(10,Number(r.score??0)));
    const pct=Math.round(score*10);
    const mm=r.metrics||{};
    const perfBits=[]; if(mm.reads!=null)perfBits.push(`阅读 ${mm.reads}`); if(mm.likes)perfBits.push(`赞 ${mm.likes}`);
    if(mm.comments)perfBits.push(`评 ${mm.comments}`); if(mm.shares)perfBits.push(`分享 ${mm.shares}`);
    const perf=perfBits.length?`<span class="jusage">${esc(perfBits.join(" · "))}</span>`:"";
    const why=r.reason?`<span class="jusage">${esc(String(r.reason).slice(0,120))}</span>`:"";
    return `<div class="jev-row"><span class="jt">${esc(r.title||"(未命名)")}</span>`+
      `<span class="jscore"><span class="jmeter"><i style="width:${pct}%"></i></span>`+
      `<span class="jnum${score<4?" low":""}">${score.toFixed(1)}</span></span>`+
      (perf||why?perf+why:"")+
      (r.usage?`<span class="jusage">探测 ${r.usage.input_tokens??0} 入 / ${r.usage.output_tokens??0} 出</span>`:"")+
      `</div>`;
  }).join("");
  /* 默认折叠：增强结论属于"可选深入"，收起时一行摘要不占首屏；
     每条内容的分数已直接嵌进「内容信号」的文章行里。 */
  const summ=scored.length?`${list.length} 条 · 最高 ${hi.toFixed(1)} · 最低 ${lo.toFixed(1)}`
    :(JEV.lastAnalysis?"分析异常":"未分析");
  return `<details class="card media-jev-card"><summary class="media-jev-summary">`+
    `<span class="media-jev-title"><span class="media-section-index">09</span>Jev 增强</span>`+
    `<span class="media-jev-summ">${esc(summ)}${JEV.analyzedAt?(" · "+esc(String(JEV.analyzedAt).slice(11,16))):""}</span>`+
    `<span class="media-jev-caret">▾</span></summary>`+
    `<div class="media-jev-body">${rows||`<div class="jev-empty">还没有分析结果。点下方「Jev 分析」，对内容信号逐条打质量分。</div>`}`+
    `<div class="media-actions"><button class="media-primary" onclick="jevAnalyze()">${JEV.lastAnalysis?"重新分析":"Jev 分析"}</button></div></div></details>`;
}
function openOv(id){
  const o=$(id); if(!o) return;
  const wasOpen=o.classList.contains("show");
  if(!wasOpen){
    const active=document.activeElement;
    if(active&&active!==document.body&&active.isConnected) modalReturnFocus.set(id,active);
  }
  o.classList.add("show");
  syncModalOpenState();
  setTimeout(()=>{if(o.classList.contains("show")) focusModalStart(o);},0);
}
function openFeedManage(){ renderFeedManage(); openOv("feedManageOv"); }
async function refreshAllFeeds(btn){
  if(guest){ notify("访客模式不刷新真实订阅源。","warn"); return; }
  if(!SERVED){ notify("刷新订阅需要本地同步服务（请用 127.0.0.1:8787 打开）。","warn"); return; }
  const b=btn||$("newsRefreshBtn");
  const old=b?b.textContent:"刷新订阅";
  if(b){ b.disabled=true; b.textContent="刷新中…"; }
  try{
    const r=await fetch("/api/feeds/refresh",{
      method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({all:true})
    });
    const j=await r.json().catch(()=>({}));
    if(!r.ok || !j.ok) throw new Error(j.error||("HTTP "+r.status));
    await loadSections();
    renderSections();
    renderTypeBar();
    const tail=j.failed?`（${j.failed} 个源失败）`:"";
    notify(`已刷新 ${j.refreshed||0} 个订阅源，新增 ${j.added||0} 条内容${tail}`,"info");
  }catch(e){
    notify("刷新订阅失败："+e.message,"err");
  }finally{
    if(b){ b.disabled=false; b.textContent=old; }
  }
}

/* ---------- 数据来源弹窗 ----------
   把"每个信息源怎么管理"从口头解释变成页面里的一等公民：
   描述/频率/凭据写死（与 scripts/ 的真实实现一一对应，改脚本要同步改这里），
   状态 chip 和最近同步时间从已加载的数据现算，不另发请求。 */
function openSources(){
  const ai=webPresence&&webPresence.aiUsage, secErrNow=secErr;
  const aiState=ai?(ai.codex&&ai.codex.totalTokens!=null?"ok":"warn"):"dead";
  const aiLabel=ai?"已接入（5 分钟轮询）":"未加载";
  const gsc=(webPresence&&webPresence.search)||{};
  const gscState=gsc.error?"dead":(gsc.sites&&gsc.sites.length?"ok":"warn");
  const syncT=state&&state.lastNotionSync?String(state.lastNotionSync).slice(0,16).replace("T"," "):"尚未同步";
  const socialT=social&&social.generatedAt?String(social.generatedAt).slice(0,16).replace("T"," "):"—";
  const webT=webPresence&&webPresence.generatedAt?String(webPresence.generatedAt).slice(11,16):"—";
  const row=(name,chip,chipCls,desc,meta)=>`<div class="src-row"><div class="src-head"><span class="src-name">${esc(name)}</span>`+
    `<span class="chip ${chipCls}">${esc(chip)}</span></div><div class="src-desc">${esc(desc)}</div>`+
    (meta?`<div class="src-meta">${esc(meta)}</div>`:"")+"</div>";
  $("srcTagModal").textContent="8 个信息源";
  $("srcList").innerHTML=[
    row("Notion 收益 / 项目","最近同步 "+syncT,state&&state.notionStatus==="connected"?"ok":"warn",
      "收益与项目只读拉取，页面不回写 Notion。手动点「同步数据」，或打开页面时数据超过 1 天自动同步一次（每天至多一次）。",
      "scripts/sync-now.sh → notion-sync.py（ntn CLI + Surge 代理）→ data.json"),
    row("资讯订阅（RSS）",(sections&&sections.sections?"已接入":"未加载"),secErrNow?"warn":"ok",
      "订阅源在「资讯」页签管理，支持探查新源。栏目节奏（天数/阈值）由上游统一计算，页面只展示。",
      "scripts/sync/sections.py --write → sections.json；读写工作台 rss-store.json（真源独立）"),
    row("写作 / 复盘",secErrNow?"读取失败":"已接入",secErrNow?"warn":"ok",
      "与资讯同一份 sections.json，按栏目区分阈值；写作 45 天 / 复盘 30 天。",
      "Obsidian 知识库目录扫描 → sections.json"),
    row("X（Twitter）",socialT==="—"?"未加载":"快照 "+socialT,"ok",
      "只读公开数据：账号资料 + 最近时间线的公开浏览/赞/评/转发。官方统计 API 未接入。",
      "scripts/sync/social-sync.py → social.json"),
    row("小红书","快照 "+socialT,"ok",
      "已通过会话 Cookie 接通，只读本人主页与公开笔记互动，不自动发布。",
      "scripts/sync/social-sync.py（凭据 ~/.config/xfetch/session.json）→ social.json"),
    row("公众号","素材已接入 · 统计待认证","warn",
      "未认证订阅号没有官方统计接口；已保留草稿凭据（本机环境变量），素材 7 篇。IP 白名单需与出口 IP 一致。",
      "scripts/sync/social-sync.py → social.json"),
    row("知识星球","历史快照 · 每日核验","warn",
      "平台无稳定逐条接口，数据停在最后一次后台导出；每天发布状态人工核验，定期导出回填。",
      "本机导出 → social.json"),
    row("网站 / 博客 / AI 用量","检测 "+webT,webT==="—"?"warn":"ok",
      "三个站点可用性每 5 分钟检测；博客更新走 RSS；访问数据来自 Notion 每日 09:00 汇总；AI 用量扫本机会话日志。搜索流量当前"+(gsc.error?"密钥失效，按知识库《GSC 密钥更换指南》处理。":"已接入。"),
      "scripts/sync/web-monitor.py + ai_usage.py → web-presence.json；GSC 密钥 ~/.config/daily-workbench/gsc-service-account.json")].join("");
  renderDraftPush();
  openOv("sourcesOv");
}
function renderDraftPush(){
  const bar=$("draftPushBar"); if(!bar) return;
  const drafts=(state.earnings||[]).filter(e=>e.source==="local");
  if(!drafts.length){ bar.innerHTML=""; return; }
  const amt=drafts.reduce((s,e)=>s+(+e.amount||0),0);
  bar.innerHTML=`<div class="src-row"><div class="src-head"><span class="src-name">本地草稿</span>`+
    `<span class="chip warn">${drafts.length} 条待整理</span></div>`+
    `<div class="src-desc">「记一笔」的记录仅存本机，尚未写入 Notion「副业收入」库（合计 ${fmt(amt)}）。</div>`+
    `<div class="src-meta"><button class="btn" onclick="pushDrafts()">推送到 Notion</button> · `+
    `写成功后自动转为只读，下次同步不会重复。</div></div>`;
}
async function pushDrafts(){
  if(guest){ notify("访客模式下不能推送草稿。","warn"); return; }
  if(!confirm("把本地草稿写入 Notion「副业收入」库？写成功后这些记录会转为只读。")) return;
  try{
    const r=await fetch("/api/notes/push",{method:"POST"});
    const j=await r.json().catch(()=>({}));
    const lines=(j.output||"").split("\n").filter(l=>l.includes("✓")||l.includes("✗")||l.includes("完成"));
    if(r.ok&&j.ok){ notify("推送完成\n"+lines.slice(0,6).join("\n"),"info"); }
    else{ notify("推送失败（HTTP "+r.status+"）\n"+(lines.slice(0,4).join("\n")||j.error||""),"err"); }
    await loadState(); renderAll();
    renderDraftPush();
  }catch(e){ notify("推送请求失败："+e.message,"err"); }
}
function feedClock(v){
  const raw=String(v??"").trim();
  if(!raw||raw==="0") return "尚未抓取";
  const d=/^\d+$/.test(raw)?new Date(Number(raw)):new Date(raw);
  if(Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false});
}
function feedManageState(f){
  if(f.error) return {label:"抓取失败",cls:"dead"};
  if(f.paused) return {label:"已暂停",cls:"warn"};
  if(f.dead) return {label:"停更",cls:"dead"};
  return {label:"正常",cls:"ok"};
}
async function renderFeedManage(){
  try{
    const r=await fetch("/api/feeds");
    if(!r.ok) throw new Error("HTTP "+r.status);
    const j=await r.json();
    const fs=j.feeds||[];
    const newsSec=(sections&&sections.sections||[]).find(x=>x&&x.id==="news")||{};
    const healthByUrl=Object.fromEntries((newsSec.feeds||[]).map(f=>[f.url,f]));
    if($("fmGroupOptions")) $("fmGroupOptions").innerHTML=(j.groups||[]).map(g=>
      `<option value="${esc(g.name)}"></option>`).join("");
    $("fmTag").textContent=fs.length+" 个";
    $("fmList").innerHTML=fs.length?fs.map((f,i)=>{
      const fv={...f,...(healthByUrl[f.url]||{})};
      return `
      <div class="fm-card${fv.paused?" paused":""}${fv.error?" failed":""}">
        <div class="fm-card-main">
          <div class="fm-card-title"><b>${esc(fv.name)}</b><span class="chip ${feedManageState(fv).cls}">${feedManageState(fv).label}</span></div>
          <div class="fm-card-url">${esc(fv.url)}</div>
          <div class="fm-card-meta">
            <span>${esc(fv.group||"未分组")}</span>
            <span>${Number(fv.count)||0} 条历史</span>
            <span>最后尝试 ${esc(feedClock(fv.lastAttemptAt))}</span>
            <span>更新 ${esc(feedClock(fv.updatedAt))}</span>
            ${fv.errorCount?`<span>失败 ${Number(fv.errorCount)||0} 次</span>`:""}
          </div>
          ${fv.error?`<div class="fm-card-error">${esc(fv.error)}</div>`:""}
        </div>
        <div class="fm-card-actions">
          <button class="btn" type="button" onclick="fmSetPaused(${i},${fv.paused?"false":"true"})">${fv.paused?"恢复更新":"停用"}</button>
          <button class="btn danger" type="button" onclick="fmPurge(${i})">永久删除</button>
        </div>
      </div>`;}).join(""):`<div class="empty">还没有订阅源。</div>`;
  }catch(e){ $("fmList").innerHTML=`<div class="empty">读不到订阅源列表（${esc(String(e.message||e))}）。离线单文件模式不支持管理。</div>`; }
}
function probeFeeds(){ $("fpResult").innerHTML=""; $("fpUrl").value=""; openOv("feedProbeOv"); setTimeout(()=>$("fpUrl").focus(),50); }
async function fpFind(){
  const u=$("fpUrl").value.trim();
  if(!u){ $("fpResult").innerHTML=`<div class="fp-note">先填一个地址。</div>`; return; }
  $("fpFind").disabled=true; $("fpFind").textContent="探查中…";
  try{
    const r=await fetch("/api/feeds/probe",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({url:u})});
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    PROBE_HITS=j.hits||[];
    $("fpResult").innerHTML=PROBE_HITS.length?PROBE_HITS.map((h,i)=>`
      <div class="fp-hit"><span class="nm">${esc(h.title||"(未命名)")}</span>
        <span class="u">${esc(h.url)}</span>
        ${h.existing?`<span class="chip warn">已收录${h.existingName?` · ${esc(h.existingName)}`:""}</span>`:
          `<button class="btn" onclick="fpAdd(${i})">收录</button>`}
      </div>`).join(""):`<div class="fp-note">这个地址上没有发现 RSS 源。可以试 <b>${esc(u.replace(/\/?$/,"/"))}feed/</b> 或 <b>/rss.xml</b>。</div>`;
  }catch(e){ $("fpResult").innerHTML=`<div class="fp-note">探查失败：${esc(String(e.message||e))}</div>`; }
  finally{ $("fpFind").disabled=false; $("fpFind").textContent="探查"; }
}
async function fpAdd(i){
  const h=PROBE_HITS[i]; if(!h) return;
  await fmAddUrl(h.url,h.title||"", "");
}
async function fmAdd(){
  const u=$("fmUrl").value.trim(), g=$("fmGroup").value.trim();
  if(!u){ $("fmErr").textContent="先填 RSS 地址。"; return; }
  $("fmErr").textContent="";
  await fmAddUrl(u,"",g);
  $("fmUrl").value="";
}
async function fmAddUrl(url,title,group){
  try{
    const r=await fetch("/api/feeds/add",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({url,title,group})});
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    notify("已收录 "+(j.name||url)+"，重新生成快照完成。","info");
    await renderFeedManage();
    await refreshSections();
  }catch(e){ $("fmErr").textContent=String(e.message||e); notify("收录失败："+String(e.message||e),"err"); }
}
async function fmSetPaused(i,paused){
  /* 点击时重新拉列表拿真 URL，避免索引漂移误操作 */
  try{
    const r0=await fetch("/api/feeds");
    const j0=await r0.json(); const f=(j0.feeds||[])[i]; if(!f) return;
    const r=await fetch("/api/feeds/pause",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({url:f.url,paused:!!paused})});
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    notify((paused?"已停用 ":"已恢复 ")+(j.name||f.name)+"，历史条目保持不变。","info");
    await renderFeedManage();
    await refreshSections();
  }catch(e){ notify((paused?"停用":"恢复")+"失败："+String(e.message||e),"err"); }
}
async function fmPurge(i){
  try{
    const r0=await fetch("/api/feeds");
    const j0=await r0.json(); const f=(j0.feeds||[])[i]; if(!f) return;
    if(!confirm("永久删除「"+f.name+"」？\n这会删除该源的 "+Number(f.count||0)+" 条历史条目，且无法在工作台撤销。")) return;
    const typed=prompt("这是不可恢复操作。请输入“删除”确认：","");
    if(typed!=="删除"){ notify("已取消永久删除。","info"); return; }
    const r=await fetch("/api/feeds/delete",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({url:f.url,purge:true})});
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    notify("已永久删除 "+(j.name||f.name)+"（含 "+Number(j.entries||0)+" 条历史）。","info");
    await renderFeedManage();
    await refreshSections();
  }catch(e){ notify("永久删除失败："+String(e.message||e),"err"); }
}
function setGoal(){
  // 输入框里的数是**当前单位**下的数，存进 state 前必须换算回元
  const g=inputToGoal($("goalInput").value);
  if(!(g>0)){ goalErr("请填一个正数，或直接拖滑块 / 点预设。"); return; }
  applyGoal(g);
}

