/* ---------- polling (online mode) ---------- */
async function poll(){
  if(!SERVED||guest||dataPollInFlight)return;
  dataPollInFlight=true;
  try{
    const metaRes=await fetch(DATA_META_API,{cache:"no-store"});
    if(metaRes.ok){
      const meta=await metaRes.json().catch(()=>null);
      if(!meta||!meta.version||meta.version===dataVersion)return;
      const r=await fetch(API,{cache:"no-store",headers:dataVersion?{"If-None-Match":dataVersion}:undefined});
      if(r.status===304){dataVersion=meta.version;return;}
      if(r.ok){const j=await r.json();dataVersion=r.headers.get("X-Data-Version")||meta.version;
        // only reload if agent (Notion sync) changed the store
        if(JSON.stringify(j)!==JSON.stringify(state)){state=normalize(j);renderAll();}
      }
      return;
    }
    // 兼容旧版本地服务：直接使用 ETag 条件请求。
    const r=await fetch(API,{cache:"no-store",headers:dataVersion?{"If-None-Match":dataVersion}:undefined});
    if(r.status===304)return;
    if(r.ok){const j=await r.json();dataVersion=r.headers.get("X-Data-Version")||dataVersion;
      if(JSON.stringify(j)!==JSON.stringify(state)){state=normalize(j);renderAll();}
    }
  }catch(e){}
  finally{dataPollInFlight=false;}
}

/* ---------- keyboard ---------- */
document.addEventListener("keydown",e=>{
  if($("writingEditorOv")&&$("writingEditorOv").classList.contains("show")){
    if((e.metaKey||e.ctrlKey)&&(e.key==="s"||e.key==="S")){
      e.preventDefault();
      saveWritingEditor(false);
      return;
    }
    if((e.metaKey||e.ctrlKey)&&(e.key==="k"||e.key==="K")){
      e.preventDefault();
      return;
    }
  }
  if((e.metaKey||e.ctrlKey)&&(e.key==="k"||e.key==="K")){
    e.preventDefault(); openCmd(); return;
  }
  if(e.key==="Escape"){
    document.querySelectorAll(".proj.flash").forEach(n=>n.classList.remove("flash"));
    /* 先收「更多」菜单再关弹窗：菜单是 details 弹层，Esc 不关它的话
       用户按了 Esc 只看到菜单还杵在那，会以为键盘失灵。 */
    const m=document.querySelector(".actionmenu[open]"); if(m) m.removeAttribute("open");
    if($("readerImageOv")&&$("readerImageOv").classList.contains("show")){
      closeReaderImage();
      return;
    }
    closeOv();return;
  }
  if(document.querySelector(".ov.show"))return;
  const t=e.target;
  if(t&&(t.tagName==="INPUT"||t.tagName==="TEXTAREA"||t.tagName==="SELECT"||t.isContentEditable))return;
  if(guest)return;
  const k=e.key.toLowerCase();
  if(k==="e")openEarn();else if(k==="p")openProj();
  else if(k==="g")toggleGuest();else if(k==="t")toggleTheme();
});

function reloadFromFile(e){const f=e.target.files[0];if(!f)return;
  const r=new FileReader();r.onload=function(){
    try{const j=JSON.parse(r.result);
      if(!Array.isArray(j.projects)||!Array.isArray(j.earnings))throw "不是有效的 data.json";
      Object.assign(state,{projects:j.projects,earnings:j.earnings,monthlyGoal:j.monthlyGoal??null,notionStatus:j.notionStatus||"local",lastNotionSync:j.lastNotionSync||null,earningsReadonly:!!j.earningsReadonly,owner:j.owner||state.owner,projectsSource:j.projectsSource||state.projectsSource,earningsSource:j.earningsSource||state.earningsSource,notionMeta:j.notionMeta||state.notionMeta,focusSessions:Array.isArray(j.focusSessions)?j.focusSessions:state.focusSessions});
      saveState();renderAll();
      const b=$("banner");b.className="banner show online";b.innerHTML="已从 "+f.name+" 重新加载（"+state.projects.length+" 个项目 / "+state.earnings.length+" 条收益）。";
    }catch(err){notify("data.json 解析失败："+err);}
    e.target.value="";};r.readAsText(f);}

/* ---------- boot ---------- */
(async function(){
  applyTheme();
  ensureReaderWenkaiWebFont();
  try{applyReaderFont(localStorage.getItem(READER_FONT_STORAGE_KEY),false,false);}
  catch(e){applyReaderFont("wenkai",false,false);}
  applyViewPrefs();
  await loadState();
  // 首屏只读取当前页所需的派生快照；资讯 / 写作 / 复盘 / 自媒体在首次进入时再按需加载。
  await loadPageData(curType);
  renderAll();
  // 网站监控与 Jev 属于后台增强数据，不阻塞总览首屏。
  setTimeout(()=>{loadWebPresence(true);if(curType==="content") jevLoad();},0);
  // 微信、选题雷达与星球简报后台静默预取：不阻塞总览首屏，加载回来后同步点亮微信角标与「晨间速览」卡片。
  setTimeout(()=>{
    if(guest) return;
    if(!wechat) loadWechat().then(()=>{renderTypeBar();renderMorningBrief();}).catch(()=>{});
    if(typeof radar==="undefined"||!radar) loadRadar().then(()=>{renderMorningBrief();}).catch(()=>{});
    if(!zsxqDaily) loadZsxqDaily().then(()=>{renderMorningBrief();}).catch(()=>{});
  },1200);
  setType(curType);      // 应用上次选的类型；同时补齐 aria-pressed 与各屏 hidden
  renderWorldClock();
  syncWorldClockControls();
  restoreFocusTimer();
  setInterval(tickWorldClock,1000);
  const cookieAuthed = SERVED && !loadError && !!dataVersion;
  if(SERVED && (!TOKEN_MISSING || cookieAuthed)) loadSyncLog();
  if(!SERVED){const b=$("banner");b.className="banner show offline";
    b.innerHTML='离线模式：数据来自浏览器本地缓存，可能不是最新。 <button class="btn-ghost" style="margin-left:10px;padding:2px 10px;font-size:var(--fs-xs)" onclick="$(\'reloadFile\').click()">从 data.json 重新加载</button> <a href="http://127.0.0.1:8787/workbench.html" target="_blank" style="margin-left:6px;color:inherit;text-decoration:underline">或打开在线版</a>（需带 ?token=）';}
  else if(loadError || (TOKEN_MISSING && !cookieAuthed)){
    // 关键：不能把 401 静默降级成"用缓存冒充最新"。同步按钮此刻点了也只会 401。
    showBanner("auth",
      `${loadError||"这个链接里没有访问令牌（?token=），服务端拒绝了读取请求。"}<br>`+
      `因此<b>同步按钮也会失败</b>。请用带令牌的完整链接重新打开本页`+
      `（在终端跑 <code>bash scripts/tools/open-workbench.sh</code> 可打印当前有效链接）。<br>`+
      `本浏览器成功打开过一次带令牌的链接后，会记住它，之后裸链接也能用。`);
  }
  else{
    if(state.earningsReadonly){showBanner("online","收益数据已只读拉取自你的 Notion「贵人计划 / 副业收入 + 产品支出」，不会修改该页面。你在此「记一笔」的本地草稿仅存本地，整理时由我写入 Notion。");}
    pollTimer=setInterval(poll,15000);
    setInterval(()=>{ if(!guest) loadWebPresence(true); },60000);
    maybeAutoSync();
  }
})();
