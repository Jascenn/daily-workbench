/* ── 工作台写作：Obsidian Markdown 是唯一正文真源 ─────────────────────
   页面不接收文件路径，只传 bookId / chapterId；服务端重新发现文件。
   自动保存与手动保存共用同一条 POST，所有覆盖都必须带打开时的 SHA-256。 */
const writingEditor={
  kind:"chapter", bookId:"", chapterId:"", documentId:"", sha256:"", dirty:false, saving:false, conflict:false,
  timer:null, previewFrame:null, title:"", bookTitle:"", obsidianUrl:""
};
function writingEditorStatus(text,state){
  const el=$("writingEditorState");
  if(!el) return;
  el.textContent=text;
  el.dataset.state=state||"";
}
function writingEditorCharCount(text){
  return String(text||"").length;
}
function writingChapterUrl(bookId,chapterId){
  const sep=WRITING_CHAPTER_API.includes("?")?"&":"?";
  return WRITING_CHAPTER_API+sep+"bookId="+encodeURIComponent(bookId)+
    "&chapterId="+encodeURIComponent(chapterId);
}
function writingDocumentUrl(documentId){
  const sep=WRITING_DOCUMENT_API.includes("?")?"&":"?";
  return WRITING_DOCUMENT_API+sep+"documentId="+encodeURIComponent(documentId);
}
async function fetchWritingChapter(bookId,chapterId){
  const r=await fetch(writingChapterUrl(bookId,chapterId),{cache:"no-store"});
  const j=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
  return j;
}
async function fetchWritingDocument(documentId){
  const r=await fetch(writingDocumentUrl(documentId),{cache:"no-store"});
  const j=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
  return j;
}
function applyWritingChapter(data,kind="chapter"){
  if(writingEditor.timer) clearTimeout(writingEditor.timer);
  writingEditor.timer=null;
  writingEditor.kind=kind;
  writingEditor.bookId=data.bookId||"";
  writingEditor.chapterId=data.chapterId||"";
  writingEditor.documentId=data.documentId||"";
  writingEditor.sha256=data.sha256||"";
  writingEditor.dirty=false;
  writingEditor.saving=false;
  writingEditor.conflict=false;
  writingEditor.title=data.title||data.name||"未命名章节";
  writingEditor.bookTitle=data.bookTitle||data.contentType||"";
  writingEditor.obsidianUrl=safeObsidianUrl(data.obsidianUrl);
  $("writingEditorTitle").textContent=writingEditor.title;
  $("writingEditorBook").textContent=kind==="document"
    ?`${writingEditor.bookTitle||"内容"} · Obsidian`
    :(writingEditor.bookTitle?`《${writingEditor.bookTitle}》`:"Obsidian");
  $("writingEditorModified").textContent=data.modifiedAt
    ?`Obsidian 修改于 ${String(data.modifiedAt).replace("T"," ").slice(0,19)}`:"";
  const obs=$("writingEditorObsidian");
  if(writingEditor.obsidianUrl){
    obs.href=writingEditor.obsidianUrl;
    obs.hidden=false;
  }else{
    obs.removeAttribute("href");
    obs.hidden=true;
  }
  $("writingEditorInput").value=data.content||"";
  $("writingEditorInput").disabled=false;
  $("writingEditorLoading").hidden=true;
  $("writingEditorBody").hidden=false;
  $("writingEditorConflict").classList.remove("show");
  $("writingEditorSaveBtn").disabled=false;
  $("writingEditorCount").textContent=writingEditorCharCount(data.content).toLocaleString()+" 字";
  writingEditorStatus("已从 Obsidian 载入","saved");
  renderWritingEditorPreview();
}
async function openWritingTarget(kind,ids){
  if(guest){notify("访客模式不会读取或写入真实 Obsidian 正文。","warn");return;}
  if(!SERVED||!TOKEN){
    notify("工作台写作需要本地同步服务，请用 127.0.0.1:8787 的带令牌链接打开。","warn");
    return;
  }
  const isDoc=kind==="document";
  const bookId=ids.bookId||"", chapterId=ids.chapterId||"", documentId=ids.documentId||"";
  if((!isDoc && (!/^book_[0-9a-f]{20}$/.test(String(bookId)) ||
      !/^chapter_[0-9a-f]{20}$/.test(String(chapterId)))) ||
      (isDoc && !/^doc_[0-9a-f]{20}$/.test(String(documentId)))){
    notify("章节标识不完整，无法打开写作编辑器。","err");return;
  }
  if(writingEditor.timer) clearTimeout(writingEditor.timer);
  writingEditor.timer=null;
  writingEditor.kind=kind;
  writingEditor.bookId=bookId;
  writingEditor.chapterId=chapterId;
  writingEditor.documentId=documentId;
  writingEditor.sha256="";
  writingEditor.dirty=false;
  writingEditor.saving=false;
  writingEditor.conflict=false;
  $("writingEditorTitle").textContent=isDoc?"正在打开稿件":"正在打开章节";
  $("writingEditorBook").textContent="—";
  $("writingEditorModified").textContent="—";
  $("writingEditorInput").value="";
  $("writingEditorInput").disabled=true;
  $("writingEditorPreview").innerHTML="";
  $("writingEditorCount").textContent="0 字";
  $("writingEditorSaveBtn").disabled=true;
  $("writingEditorConflict").classList.remove("show");
  $("writingEditorLoading").hidden=false;
  $("writingEditorLoading").textContent=isDoc
    ?"正在从 Obsidian 读取稿件…":"正在从 Obsidian 读取章节（未写章节会以空正文打开）…";
  $("writingEditorBody").hidden=true;
  $("writingEditorDialog").dataset.view="edit";
  setWritingEditorView("edit");
  writingEditorStatus("正在读取正文…","loading");
  openOv("writingEditorOv");
  try{
    const data=isDoc
      ?await fetchWritingDocument(documentId)
      :await fetchWritingChapter(bookId,chapterId);
    applyWritingChapter(data,kind);
    setTimeout(()=>{try{$("writingEditorInput").focus({preventScroll:true});}catch(e){}},0);
  }catch(e){
    $("writingEditorLoading").textContent="读取失败："+e.message;
    writingEditorStatus("读取失败","error");
    notify("读取内容失败："+e.message,"err");
  }
}
function openWritingEditor(bookId,chapterId){
  return openWritingTarget("chapter",{bookId,chapterId});
}
function openWritingDocument(documentId){
  return openWritingTarget("document",{documentId});
}
function setWritingEditorView(view){
  const v=view==="preview"?"preview":"edit";
  const dialog=$("writingEditorDialog");
  if(dialog) dialog.dataset.view=v;
  $("writingEditTab")?.classList.toggle("on",v==="edit");
  $("writingPreviewTab")?.classList.toggle("on",v==="preview");
  if(v==="preview") renderWritingEditorPreview();
}
function onWritingEditorInput(){
  const ready=writingEditor.kind==="document"
    ?!!writingEditor.documentId
    :!!(writingEditor.bookId&&writingEditor.chapterId);
  if(!ready||writingEditor.saving) return;
  writingEditor.dirty=true;
  const input=$("writingEditorInput");
  $("writingEditorCount").textContent=writingEditorCharCount(input.value).toLocaleString()+" 字";
  if(writingEditor.conflict) writingEditorStatus("有未保存修改 · 等待解决冲突","error");
  else writingEditorStatus("有未保存修改 · 2 秒后自动保存","dirty");
  if(writingEditor.previewFrame) cancelAnimationFrame(writingEditor.previewFrame);
  writingEditor.previewFrame=requestAnimationFrame(()=>{
    writingEditor.previewFrame=null;
    renderWritingEditorPreview();
  });
  if(writingEditor.timer) clearTimeout(writingEditor.timer);
  if(!writingEditor.conflict){
    writingEditor.timer=setTimeout(()=>{
      writingEditor.timer=null;
      saveWritingEditor(false);
    },2000);
  }
}
function inlineMarkdown(raw){
  const tokens=[];
  const hold=html=>{
    const key="\u0000WBMD"+(tokens.length)+"\u0000";
    tokens.push(html);
    return key;
  };
  /* Obsidian 的双链与本地资源不走 URL 白名单：双链渲染成标记文本；
     本地图片只允许同目录或子目录，并交给工作台的安全媒体接口读取。 */
  const localAsset=path=>/^(?:\.\/)?[^/\\][^\\]*\.(?:png|jpg|jpeg|gif|webp|avif)$/i.test(path)
    &&!path.includes("..");
  const wikiLink=(target,label)=>{
    const clean=String(target||"").split("|")[0].replace(/^#/,"").trim();
    return clean
      ?hold(`<span class="wb-wikilink" title="${esc(clean)}">${esc(label||clean)}</span>`)
      :esc(label||target);
  };
  let s=String(raw||"");
  s=s.replace(/`([^`\n]+)`/g,(_,code)=>hold(`<code>${esc(code)}</code>`));
  s=s.replace(/!\[\[([^\]]+)\]\]/g,(m,target)=>wikiLink(target,`嵌入：${String(target).split("|").pop()}`));
  s=s.replace(/\[\[([^\]]+)\]\]/g,(m,target)=>{
    const parts=String(target).split("|");
    return wikiLink(parts[0],parts[1]||parts[0]);
  });
  s=s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/g,(m,alt,url)=>{
    const safe=safeUrl(url);
    if(safe) return hold(`<img src="${esc(safe)}" alt="${esc(alt)}" loading="lazy">`);
    if(localAsset(url)) return hold(`<img data-obsidian-asset="${esc(url)}" alt="${esc(alt)}" loading="lazy" hidden>`);
    return esc(m);
  });
  s=s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/g,(m,label,url)=>{
    const safe=safeUrl(url);
    return safe?hold(`<a href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`):esc(m);
  });
  s=esc(s);
  s=s.replace(/\*\*([^*\n]+)\*\*/g,"<strong>$1</strong>");
  s=s.replace(/__([^_\n]+)__/g,"<strong>$1</strong>");
  s=s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g,"$1<em>$2</em>");
  s=s.replace(/(^|[^_])_([^_\n]+)_(?!_)/g,"$1<em>$2</em>");
  tokens.forEach((html,i)=>{s=s.split("\u0000WBMD"+i+"\u0000").join(html);});
  return s;
}
function bindObsidianImages(scope,target){
  const images=[...scope.querySelectorAll("img[data-obsidian-asset]")];
  if(!images.length||!target) return;
  const isDoc=target.kind==="document";
  if(isDoc?!target.documentId:!(target.bookId&&target.chapterId)) return;
  const endpointOf=asset=>{
    const base=isDoc?WRITING_DOCUMENT_ASSET_API:"/api/writing/asset";
    const params=new URLSearchParams({path:asset});
    if(isDoc) params.set("documentId",target.documentId);
    else{params.set("bookId",target.bookId);params.set("chapterId",target.chapterId);}
    if(TOKEN&&!base.includes("token=")) params.set("token",TOKEN);
    return base+(base.includes("?")?"&":"?")+params.toString();
  };
  const load=endpoint=>fetch(endpoint).then(async r=>{
    if(!r.ok) throw new Error((await r.json().catch(()=>({}))).error||("HTTP "+r.status));
    return r.json();
  });
  const sources=new Map();
  images.forEach(img=>{
    if(!img.dataset.obsidianAsset) return;
    const endpoint=endpointOf(img.dataset.obsidianAsset);
    if(!sources.has(endpoint)) sources.set(endpoint,load(endpoint));
    sources.get(endpoint).then(data=>{
      img.src=`data:${data.contentType};base64,${data.data}`;
      img.hidden=false;
    }).catch(()=>img.remove());
  });
}
function safeMarkdownHtml(markdown){
  let source=String(markdown||"").replace(/\r\n?/g,"\n");
  /* YAML frontmatter 是 Obsidian 的元数据，不应在正文预览里铺成正文。
     编辑区仍完整保留它，保存时也不会改一个字。 */
  if(source.startsWith("---\n")){
    const match=source.match(/^---\n[\s\S]*?\n---[ \t]*(?:\n|$)/);
    if(match) source=source.slice(match[0].length);
  }
  const lines=source.split("\n");
  const out=[];
  const isBlockStart=line=>/^\s*```|^\s{0,3}#{1,6}\s+|^\s{0,3}>\s?|^\s{0,3}(?:[-*+]|\d+\.)\s+|^\s{0,3}(?:---+|\*\*\*+|___+)\s*$/.test(line);
  for(let i=0;i<lines.length;){
    const line=lines[i];
    if(!line.trim()){i++;continue;}
    const fence=line.match(/^\s*```([\w.+-]*)\s*$/);
    if(fence){
      const code=[];
      i++;
      while(i<lines.length&&!/^\s*```\s*$/.test(lines[i])) code.push(lines[i++]);
      if(i<lines.length) i++;
      out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
      continue;
    }
    const heading=line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
    if(heading){
      const level=heading[1].length;
      out.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      i++;continue;
    }
    if(/^\s{0,3}(?:---+|\*\*\*+|___+)\s*$/.test(line)){
      out.push("<hr>");i++;continue;
    }
    if(/^\s{0,3}>\s?/.test(line)){
      const quote=[];
      while(i<lines.length&&/^\s{0,3}>\s?/.test(lines[i])){
        quote.push(lines[i].replace(/^\s{0,3}>\s?/,""));i++;
      }
      out.push(`<blockquote>${quote.map(inlineMarkdown).join("<br>")}</blockquote>`);
      continue;
    }
    const list=line.match(/^\s{0,3}([-*+]|\d+\.)\s+(.+)$/);
    if(list){
      const ordered=/\d+\./.test(list[1]);
      const tag=ordered?"ol":"ul";
      const items=[];
      while(i<lines.length){
        const m=lines[i].match(/^\s{0,3}([-*+]|\d+\.)\s+(.+)$/);
        if(!m||/\d+\./.test(m[1])!==ordered) break;
        items.push(`<li>${inlineMarkdown(m[2])}</li>`);i++;
      }
      out.push(`<${tag}>${items.join("")}</${tag}>`);
      continue;
    }
    /* GFM 表格：首行含 | 且第二行是 |---|---| 分隔行才按表格处理，否则照常成段。
       单元格里的 \| 是 Obsidian 的字面竖线，切列前先换成占位符。 */
    const tableRow=line=>{
      const cells=String(line).trim().replace(/^\|/,"").replace(/\|$/,"").replace(/\\\|/g,"\u0000");
      return cells.split("|").map(c=>inlineMarkdown(c.trim().split("\u0000").join("|")));
    };
    const sepCells=(lines[i+1]||"").trim().replace(/^\|/,"").replace(/\|$/,"").split("|");
    if(line.includes("|")&&sepCells.length>=2&&sepCells.every(c=>/^\s*:?-+:?\s*$/.test(c))){
      const aligns=sepCells.map(c=>{
        const t=c.trim();
        return /^:.*:$/.test(t)?"center":/:$/.test(t)?"right":"left";
      });
      const headerCells=tableRow(line);
      i+=2;
      const body=[];
      while(i<lines.length&&lines[i].includes("|")&&lines[i].trim()){
        body.push(tableRow(lines[i]));i++;
      }
      const alignAttr=idx=>idx<aligns.length&&aligns[idx]!=="left"?` style="text-align:${aligns[idx]}"`:"";
      out.push(`<table><thead><tr>${headerCells.map((h,idx)=>`<th${alignAttr(idx)}>${h}</th>`).join("")}</tr></thead>`+
        `<tbody>${body.map(r=>`<tr>${r.map((c,idx)=>`<td${alignAttr(idx)}>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    const para=[line];
    i++;
    while(i<lines.length&&lines[i].trim()&&!isBlockStart(lines[i])) para.push(lines[i++]);
    out.push(`<p>${para.map(inlineMarkdown).join("<br>")}</p>`);
  }
  return out.join("");
}
function renderWritingEditorPreview(){
  const box=$("writingEditorPreview");
  if(!box) return;
  const value=$("writingEditorInput")?.value||"";
  box.innerHTML=value.trim()
    ?safeMarkdownHtml(value)
    :`<div class="writing-editor-empty">开始输入 Markdown，右侧会显示与 Obsidian 笔记一致的标题、引用、列表、代码与图片。</div>`;
  bindObsidianImages(box,{
    kind:writingEditor.kind,
    bookId:writingEditor.bookId,
    chapterId:writingEditor.chapterId,
    documentId:writingEditor.documentId
  });
}
async function saveWritingEditor(force){
  const ready=writingEditor.kind==="document"
    ?!!writingEditor.documentId
    :!!(writingEditor.bookId&&writingEditor.chapterId);
  if(guest||!ready) return false;
  if(writingEditor.saving) return false;
  if(!writingEditor.dirty&&!force){
    writingEditorStatus("没有新的修改","saved");
    return true;
  }
  if(writingEditor.conflict&&!force) return false;
  writingEditor.saving=true;
  writingEditorStatus(force?"正在覆盖保存…":"正在保存到 Obsidian…","saving");
  $("writingEditorSaveBtn").disabled=true;
  try{
    let base=writingEditor.sha256;
    if(force){
      const latest=writingEditor.kind==="document"
        ?await fetchWritingDocument(writingEditor.documentId)
        :await fetchWritingChapter(writingEditor.bookId,writingEditor.chapterId);
      base=latest.sha256;
    }
    const endpoint=writingEditor.kind==="document"?WRITING_DOCUMENT_SAVE_API:WRITING_SAVE_API;
    const payload=writingEditor.kind==="document"
      ?{documentId:writingEditor.documentId,content:$("writingEditorInput").value,baseSha256:base}
      :{bookId:writingEditor.bookId,chapterId:writingEditor.chapterId,
        content:$("writingEditorInput").value,baseSha256:base};
    const r=await fetch(endpoint,{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(payload)
    });
    const j=await r.json().catch(()=>({}));
    if(r.status===409){
      writingEditor.conflict=true;
      writingEditorStatus("检测到 Obsidian 外部修改","error");
      $("writingEditorConflictText").textContent=j.error||"Obsidian 中的正文已变化，请重新加载或明确覆盖。";
      $("writingEditorConflict").classList.add("show");
      return false;
    }
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    writingEditor.sha256=j.sha256||base;
    writingEditor.dirty=false;
    writingEditor.conflict=false;
    $("writingEditorConflict").classList.remove("show");
    $("writingEditorModified").textContent=j.modifiedAt
      ?`Obsidian 修改于 ${String(j.modifiedAt).replace("T"," ").slice(0,19)}`:"";
    $("writingEditorCount").textContent=(j.chars??writingEditorCharCount($("writingEditorInput").value)).toLocaleString()+" 字";
    const now=new Date();
    writingEditorStatus("已保存 "+String(now.getHours()).padStart(2,"0")+":"+
      String(now.getMinutes()).padStart(2,"0")+":"+String(now.getSeconds()).padStart(2,"0"),"saved");
    // 服务端已经重生成 sections.json；这里异步刷新卡片，不阻塞编辑器保存反馈。
    refreshSections().catch(()=>{});
    return true;
  }catch(e){
    writingEditorStatus("保存失败："+e.message,"error");
    notify("保存内容失败："+e.message,"err");
    return false;
  }finally{
    writingEditor.saving=false;
    $("writingEditorSaveBtn").disabled=false;
  }
}
async function reloadWritingEditor(){
  if(writingEditor.dirty&&!confirm("重新加载会丢弃当前未保存的修改，继续吗？")) return;
  writingEditorStatus("正在重新加载…","loading");
  try{
    const data=writingEditor.kind==="document"
      ?await fetchWritingDocument(writingEditor.documentId)
      :await fetchWritingChapter(writingEditor.bookId,writingEditor.chapterId);
    applyWritingChapter(data,writingEditor.kind);
  }catch(e){
    writingEditorStatus("重新加载失败","error");
    notify("重新加载内容失败："+e.message,"err");
  }
}
async function forceSaveWritingEditor(){
  if(!confirm("这会用当前编辑器内容覆盖 Obsidian 中的最新版本。确认继续吗？")) return;
  await saveWritingEditor(true);
}
function writingEditorCanClose(){
  if(!writingEditor.dirty) return true;
  return confirm("当前内容还有未保存的修改。关闭会丢失这些修改，继续吗？");
}
function resetWritingEditor(){
  if(writingEditor.timer) clearTimeout(writingEditor.timer);
  if(writingEditor.previewFrame) cancelAnimationFrame(writingEditor.previewFrame);
  writingEditor.timer=null;
  writingEditor.previewFrame=null;
  writingEditor.kind="chapter";
  writingEditor.bookId="";
  writingEditor.chapterId="";
  writingEditor.documentId="";
  writingEditor.sha256="";
  writingEditor.dirty=false;
  writingEditor.saving=false;
  writingEditor.conflict=false;
}

const WRITING_CONTENT_TYPES=[
  {key:"小说",label:"小说",hint:"新建一本书"},
  {key:"公众号",label:"公众号文章",hint:"长文与草稿"},
  {key:"Twitter",label:"Twitter / X",hint:"推文与长帖"},
  {key:"知识星球",label:"知识星球",hint:"星球文档"}
];
function writingTypeLabel(type){
  return (WRITING_CONTENT_TYPES.find(x=>x.key===type)||{}).label||type||"内容";
}
function renderWritingStudio(s){
  const docs=(s&&s.documents)||[];
  const types=["公众号","Twitter","知识星球"];
  const counts={};
  docs.forEach(d=>{const k=d.contentType||"其它";counts[k]=(counts[k]||0)+1;});
  const shown=writingDocFilter==="全部"?docs:docs.filter(d=>(d.contentType||"其它")===writingDocFilter);
  const create=WRITING_CONTENT_TYPES.map(x=>
    `<button class="writing-create-btn" type="button" onclick="openWritingCreate('${esc(x.key)}')">`+
      `<b>${esc(x.label)}</b><span>${esc(x.hint)}</span></button>`).join("");
  const tabs=["全部",...types].map(k=>
    `<button class="writing-doc-tab${writingDocFilter===k?" on":""}" type="button" `+
      `onclick="setWritingDocFilter('${esc(k)}')">${esc(k==="Twitter"?"Twitter":k)} `+
      `<span>${k==="全部"?docs.length:(counts[k]||0)}</span></button>`).join("");
  const rows=shown.length?shown.map(d=>{
    const date=String(d.modifiedAt||"").replace("T"," ").slice(0,10)||"—";
    const ago=d.days==null?"":agoTxt(d.days);
    const obs=safeObsidianUrl(d.obsidianUrl);
    return `<div class="writing-doc-row">`+
      `<div class="writing-doc-main">`+
        `<button class="writing-doc-title" type="button" onclick="openWritingDocument('${esc(d.documentId)}')">${esc(d.title||"未命名")}</button>`+
        `<div class="writing-doc-meta"><span>${esc(writingTypeLabel(d.contentType))}</span>`+
          `<span>${esc(d.status||"草稿")}</span><span>${esc((d.chars||0).toLocaleString())} 字</span>`+
          `<span>${esc(date)}${ago?" · "+esc(ago):""}</span></div>`+
      `</div>`+
      `<div class="writing-doc-actions">`+
        `<button class="writing-action-secondary" type="button" onclick="openWritingDocument('${esc(d.documentId)}')">继续写</button>`+
        (obs?`<a class="writing-action-tertiary" href="${esc(obs)}" target="_blank" rel="noopener noreferrer">Obsidian</a>`:"")+
        `<button class="writing-delete" type="button" onclick="deleteWritingContent('document','${esc(d.documentId)}','${esc(d.title||"未命名")}')" `+
          `aria-label="删除稿件 ${esc(d.title||"未命名")}">删除</button>`+
      `</div></div>`;
  }).join(""):`<div class="writing-doc-empty">这个分类还没有稿件。点上方的类型入口开始写第一篇。</div>`;
  $("wrStudio").innerHTML=`<div class="card writing-studio-card">`+
    `<div class="writing-studio-head"><div><h2>开始一篇内容</h2>`+
      `<p>统一保存为 Obsidian Markdown；小说按章节管理，其他内容按单篇稿件管理。</p></div>`+
      `<span class="tag">${docs.length} 篇稿件</span></div>`+
    `<div class="writing-create-grid">${create}</div>`+
    `<div class="writing-doc-toolbar"><b>最近稿件</b>${tabs}</div>`+
    `<div class="writing-doc-list">${rows}</div>`+
    `<details class="writing-trash" ontoggle="if(this.open&&!this.dataset.loaded)loadWritingTrash()">`+
      `<summary>回收站 <span class="tag" id="writingTrashTag">Obsidian 内可恢复</span></summary>`+
      `<div class="writing-trash-list" id="writingTrashList">`+
        `<span class="writing-doc-empty">展开时加载…</span></div></details></div>`;
}
async function loadWritingTrash(){
  const box=$("writingTrashList"),tag=$("writingTrashTag");
  if(!box) return;
  if(!SERVED||!TOKEN){
    box.innerHTML=`<span class="writing-doc-empty">回收站清单需要本地同步服务。</span>`;
    return;
  }
  try{
    const r=await fetch(WRITING_TRASH_API,{cache:"no-store"});
    const j=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    const items=j.items||[];
    if(tag) tag.textContent=items.length?`${items.length} 项 · Obsidian 内可恢复`:"空的 · Obsidian 内可恢复";
    box.innerHTML=items.length?items.map(x=>{
      const label=x.kind==="novel"?"小说":"稿件";
      const size=x.kind==="novel"?"目录":`${(x.bytes||0).toLocaleString()} 字节`;
      const when=String(x.modifiedAt||"").replace("T"," ").slice(0,16)||"—";
      return `<div class="writing-trash-row"><b>${esc(x.name)}</b>`+
        `<span>${esc(label)} · ${esc(size)} · 原修改 ${esc(when)}</span>`+
        `<button class="writing-action-secondary" type="button" `+
          `onclick="restoreWritingTrash('${esc(x.name)}')">恢复</button></div>`;
    }).join(""):`<span class="writing-doc-empty">回收站是空的。删除的稿件和小说会先到这里。</span>`;
  }catch(e){
    box.innerHTML=`<span class="writing-doc-empty">回收站读取失败：${esc(e.message)}</span>`;
  }
}
async function restoreWritingTrash(name){
  if(guest){notify("访客模式不能恢复真实 Obsidian 内容。","warn");return;}
  if(!SERVED||!TOKEN){
    notify("恢复需要本地同步服务，请用 127.0.0.1:8787 的带令牌链接打开。","warn");
    return;
  }
  if(!confirm(`会把「${name}」从回收站移回原来的创作目录（重名会加后缀，不会覆盖）。确认恢复吗？`)) return;
  try{
    const r=await fetch(WRITING_RESTORE_API,{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name})
    });
    const j=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    notify(`已恢复「${j.name||name}」到创作目录。`,"info");
    const details=document.querySelector(".writing-trash");
    if(details) details.dataset.loaded="";
    await loadWritingTrash();
    await refreshSections();
  }catch(e){
    notify("恢复失败："+e.message,"err");
  }
}
function setWritingDocFilter(kind){
  writingDocFilter=kind||"全部";
  const sec=secOf("writing");
  if(sec) renderWritingStudio(sec);
}
function openWritingCreate(type){
  if(guest){notify("访客模式不能创建真实 Obsidian 内容。","warn");return;}
  writingCreateKind=type==="小说"?"novel":"document";
  writingCreateType=type||"公众号";
  $("writingCreateTitle").textContent=writingCreateKind==="novel"?"新建小说":"新建内容";
  $("writingCreateHint").textContent=writingCreateKind==="novel"
    ?"会在 Obsidian「创作/小说」下建立目录与第一章，正文仍由你亲手写。"
    :`会在 Obsidian「创作/${writingTypeLabel(writingCreateType)}」下创建一篇新的 Markdown。`;
  $("writingCreateType").textContent=writingTypeLabel(writingCreateType);
  $("writingCreateNameLabel").textContent=writingCreateKind==="novel"?"小说名":"标题";
  $("writingCreateName").placeholder=writingCreateKind==="novel"?"例如：雾中回声":"例如：这周我学到的三件事";
  $("writingCreateName").value="";
  $("writingCreateSubmit").disabled=false;
  $("writingCreateSubmit").textContent="创建并开始写";
  openOv("writingCreateOv");
  setTimeout(()=>{try{$("writingCreateName").focus();}catch(e){}},0);
}
async function submitWritingCreate(event){
  event.preventDefault();
  const title=$("writingCreateName").value.trim();
  if(!title){$("writingCreateName").focus();return false;}
  const btn=$("writingCreateSubmit");
  btn.disabled=true;btn.textContent="创建中…";
  try{
    const r=await fetch(WRITING_CREATE_API,{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({kind:writingCreateKind,contentType:writingCreateType,title})});
    const j=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    closeOv("writingCreateOv");
    if(writingCreateKind==="novel"){
      notify("已创建小说，正在打开第一章。","info");
      await openWritingEditor(j.bookId,j.chapterId);
    }else{
      notify("已创建稿件，正在打开编辑器。","info");
      await openWritingDocument(j.documentId);
    }
    await refreshSections();
  }catch(e){
    notify("创建失败："+e.message,"err");
  }finally{
    btn.disabled=false;btn.textContent="创建并开始写";
  }
  return false;
}

/* 删除永远走 Obsidian 回收站而不是物理删除：确认弹窗只负责拦误触，恢复仍回 Obsidian 里找。 */
async function deleteWritingContent(kind,id,title){
  if(guest){notify("访客模式不能删除真实 Obsidian 内容。","warn");return;}
  if(!SERVED||!TOKEN){
    notify("删除需要本地同步服务，请用 127.0.0.1:8787 的带令牌链接打开。","warn");
    return;
  }
  const safeTitle=String(title||"");
  const message=kind==="novel"
    ?`会把 ${safeTitle} 连同全部章节和设定一起移入 Obsidian 回收站（可恢复）。确认删除吗？`
    :`会把稿件「${safeTitle}」移入 Obsidian 回收站（可恢复）。确认删除吗？`;
  if(!confirm(message)) return;
  try{
    const r=await fetch(WRITING_DELETE_API,{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(kind==="novel"?{kind,bookId:id}:{kind,documentId:id})
    });
    const j=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
    notify(`已把 ${safeTitle} 移入 Obsidian 回收站，可在 Obsidian 内恢复。`,"info");
    await refreshSections();
  }catch(e){
    notify("删除失败："+e.message,"err");
  }
}

function renderWriting(s){
  renderWritingStudio(s);
  const statOrder=["章节进度","正文字数","原创作品","客户内容","最近改动"];
  const orderedStats=[...statOrder.map(k=>(s.stats||[]).find(x=>x.k===k)).filter(Boolean),
    ...(s.stats||[]).filter(x=>!statOrder.includes(x.k))];
  $("wrStats").innerHTML=statCells(orderedStats);
  const books=s.books||[];
  const noteLink=(url,label="打开笔记")=>{
    const href=safeObsidianUrl(url);
    return href?`<a class="writing-open" href="${esc(href)}">${esc(label)} ↗</a>`:"";
  };
  const actionLink=(url,label,cls="writing-action-secondary",showArrow=true)=>{
    const href=safeObsidianUrl(url);
    return href?`<a class="${cls}" href="${esc(href)}">${esc(label)}${showArrow?" ↗":""}</a>`:"";
  };
  $("wrBooks").innerHTML=books.length?books.map(b=>{
    const tgt=b.target;
    // 进度按目标区间的**下限**算。目标是"2 万 - 5 万字"这种区间，拿上限当分母
    // 会让进度永远显得很小，读者第一眼就得到"几乎没写"的错误印象。
    const pct=tgt?Math.round(b.chars/tgt.min*100):null;
    const barPct=tgt?Math.max(0,Math.min(100,pct)):0;
    const kv=(k,v,url)=>`<div class="kvrow"><span>${safeObsidianUrl(url)
      ?`<a href="${esc(safeObsidianUrl(url))}">${esc(k)}</a>`:esc(k)}</span><b>${esc(v)}</b></div>`;
    const chapters=b.chapters||[];
    const next=chapters.find(c=>!c.written);
    const current=[...chapters].reverse().find(c=>c.written);
    const remainingChars=tgt?Math.max(0,tgt.min-b.chars):null;
    const canEditChapter=c=>!guest&&!!c&&/^book_[0-9a-f]{20}$/.test(String(c.bookId||""))&&
      /^chapter_[0-9a-f]{20}$/.test(String(c.chapterId||""));
    const chapterRow=c=>{
      const href=safeObsidianUrl(c.obsidianUrl);
      const title=`${esc(c.name)}${c.written?"":"（未写）"}`;
      const titleNode=canEditChapter(c)
        ?`<button class="ch-open" type="button" onclick="openWritingEditor('${esc(c.bookId)}','${esc(c.chapterId)}')">${title}</button>`
        :(href?`<a href="${esc(href)}">${title}</a>`:title);
      const editAction=canEditChapter(c)
        ?`<button class="ch-write" type="button" onclick="openWritingEditor('${esc(c.bookId)}','${esc(c.chapterId)}')">${c.written?"继续写":"开始写"}</button>`
        :"";
      return `<div class="chrow${c.written?" done":""}${c===next?" next":""}">`+
      `<span class="mk" aria-hidden="true">${c.written?"✓":"·"}</span>`+
      // 状态必须**有文字**，不能只靠 ✓ / · 与颜色（WCAG 1.4.1 不用颜色单独传达信息）
      `<span class="cn">${titleNode}</span>`+
      `<span class="cw">${c.chars?c.chars.toLocaleString()+" 字":(c===next?"下一章":"")}`+
        `${href?` <a class="obsidian-mini" href="${esc(href)}" aria-label="在 Obsidian 中打开 ${esc(c.name)}">↗</a>`:""}`+
        `${editAction}</span>`+
      (c.summary?`<span class="cs">${esc(c.summary)}</span>`:"")+
      `</div>`;
    };
    const written=chapters.filter(c=>c.written);
    const pending=chapters.filter(c=>!c.written);
    const preview=[...written.slice(-2),...pending.slice(0,2)];
    const remaining=chapters.filter(c=>!preview.includes(c));
    const sourceCount=(b.docs||[]).length+(b.drafts||[]).length;
    const sourceChars=[...(b.docs||[]),...(b.drafts||[])].reduce((sum,d)=>sum+(d.chars||0),0);
    const primaryAction=current
      ?(canEditChapter(current)
        ?`<button class="writing-action-primary" type="button" onclick="openWritingEditor('${esc(current.bookId)}','${esc(current.chapterId)}')">在工作台写 ${esc(current.name)}</button>`
        :`<button class="writing-action-primary" type="button" disabled>${guest?"访客示例 · 不写入正文":"当前章节不可编辑"}</button>`)
      :actionLink(b.outlineUrl,"打开章节目录","writing-action-primary");
    return `<div class="card writing-book-card">`+
      `<div class="writing-book-head"><div><h2>《${esc(b.title)}》</h2>`+
        `<div class="genre">${esc(b.genre||"原创")}</div></div>`+
        `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end">`+
          `<span class="tag">原创</span>`+
          (!guest&&/^book_[0-9a-f]{20}$/.test(String(b.bookId||""))
            ?`<button class="writing-delete" type="button" onclick="deleteWritingContent('novel','${esc(b.bookId)}','《${esc(b.title)}》')" aria-label="删除小说 ${esc(b.title)}">删除</button>`
            :"")+
        `</div></div>`+
      `<div class="writing-action-panel"><div class="writing-action-copy">`+
        `<span class="writing-action-kicker">当前写作位置</span>`+
        `<b class="writing-action-title">${current?esc(current.title):"从第一章开始"}</b>`+
        `<span class="writing-action-meta">${current?`正文 ${current.chars.toLocaleString()} 字 · 原生 Markdown 保存在 Obsidian`:"还没有已写章节，先打开目录确认结构"}</span></div>`+
        `<div class="writing-action-buttons">`+
          primaryAction+
          (current?actionLink(current.obsidianUrl,"在 Obsidian 中打开","writing-action-tertiary",false):"")+
          (current?actionLink(b.outlineUrl,"查看目录","writing-action-tertiary",false):"")+
        `</div></div>`+
      (next?`<div class="writing-next"><span class="lbl">下一章</span>`+
        `<div class="writing-next-copy"><b>${esc(next.title||next.name)}</b>`+
        `<span>${esc(next.summary&&!String(next.title||"").includes(next.summary)?next.summary:"目录已排好，正文尚未开始。")}</span></div>`+
        `<div class="writing-next-actions">${canEditChapter(next)
          ?`<button class="writing-action-secondary" type="button" onclick="openWritingEditor('${esc(next.bookId)}','${esc(next.chapterId)}')">${next.written?"继续写这一章":"开始写这一章"}</button>`
          :actionLink(b.outlineUrl,"打开目录","writing-action-tertiary",false)}</div></div>`:
        `<div class="writing-next"><span class="lbl">状态</span>`+
          `<div class="writing-next-copy"><b>目录章节已全部写完</b><span>可以进入通读与修订。</span></div>`+
          `<div class="writing-next-actions">${actionLink(b.outlineUrl,"打开章节目录","writing-action-tertiary",false)}</div></div>`)+
      `<div class="writing-progress">`+
        `<div><span>已写正文</span><b>${b.chars.toLocaleString()} 字</b></div>`+
        `<div><span>离最低目标</span><b>${remainingChars===null?"—":remainingChars.toLocaleString()+" 字"}</b></div>`+
        `<div><span>章节进度</span><b>${b.writtenCount} / ${b.chapterCount}</b></div>`+
      `</div>`+
      (tgt?`<div class="writing-target"><div class="writing-target-head">`+
        `<b>目标完成 ${pct}%</b>`+
        `<span>${b.chars.toLocaleString()} / ${tgt.min.toLocaleString()} 字</span></div>`+
        `<div class="barwrap"><i style="width:${barPct}%"></i></div>`+
        `<div class="no-note" style="margin-top:0">剩余至少 ${remainingChars.toLocaleString()} 字；进度按目标下限计，上限 ${tgt.max.toLocaleString()} 字不计入完成度。</div></div>`:"")+
      (sourceCount?`<details class="writing-disclosure"><summary>设定、大纲与其它稿件`+
        `<span>${sourceCount} 份 · ${sourceChars.toLocaleString()} 字</span></summary>`+
        `<div class="writing-source-list">`+
        (b.docs&&b.docs.length?`<h3>设定与大纲</h3>`+b.docs.map(d=>kv(d.title,d.chars.toLocaleString()+" 字",d.obsidianUrl)).join(""):"")+
        (b.drafts&&b.drafts.length?`<h3>其它稿件</h3>`+b.drafts.map(d=>kv(d.title,d.chars.toLocaleString()+" 字",d.obsidianUrl)).join("")+
          `<div class="no-note" style="margin-top:8px">这几份与正文存在内容重合，因此不计入上面的正文字数，否则字数会虚高。</div>`:"")+
        `</div></details>`:"")+
      `<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:16px">`+
        `<h2 style="margin:0">章节目录</h2><span class="tag">${b.writtenCount} / ${b.chapterCount} 已写</span></div>`+
      `<div class="chlist">${preview.map(chapterRow).join("")}</div>`+
      (remaining.length?`<details class="writing-chapter-more"><summary>查看完整目录`+
        `<span>还有 ${remaining.length} 章</span></summary><div class="chlist">${remaining.map(chapterRow).join("")}</div></details>`:"")+
      `</div>`;
  }).join(""):`<div class="card"><div class="empty">Obsidian projects/ 下还没有创作项目。</div></div>`;

  const cs=s.clients||[];
  const clientRow=c=>{
    const st=c.status||"未标状态";
    const cls=(st==="已完成")?"chip ok":((st==="进行中")?"chip acc":"chip");
    return `<div class="nitem"><div class="nt">${esc(c.title)}</div>`+
      `<div class="nm2"><span class="${cls}">${esc(st)}</span>`+
      (c.due?`<span>截止 ${esc(c.due)}</span>`:"")+`</div></div>`;
  };
  const finished=cs.filter(c=>c.status==="已完成"), open=cs.filter(c=>c.status!=="已完成");
  $("wrClientTag").textContent=`待交付 ${open.length} · 已完成 ${finished.length}`;
  $("wrClients").innerHTML=cs.length?
    `<div class="writing-client-summary"><div><b>${open.length?open.length+" 项待交付":"当前没有待交付内容"}</b>`+
      `<span>${finished.length?"已完成记录默认收起":"客户内容均已清空"}</span></div>`+
      `<span class="tag">${cs.length} 项总计</span></div>`+
    (open.length?open.map(clientRow).join(""):"")+
      (finished.length?`<details class="completed-group"><summary>已完成 ${finished.length} 项<span>按需查看</span></summary>`+
        `<div class="completed-list">${finished.map(clientRow).join("")}</div></details>`:""):
    `<div class="empty">没有 category=客户服务 的项目。</div>`;
  $("wrNote").textContent=s.note||"";
}

function renderReview(s){
  $("rvStats").innerHTML=statCells(s.stats);
  const items=s.items||[], gs=s.groups||[];
  renderReviewProjectTodos();
  const typeSel=$("rvTypeSel");
  if(typeSel){
    const groups=gs.map(g=>g.name).filter(Boolean);
    const current=typeSel.value;
    typeSel.innerHTML=`<option value="">全部类型</option>`+
      groups.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join("");
    if(groups.includes(current)) typeSel.value=current;
  }
  const q=reviewQueryF;
  const filtered=items.filter(n=>
    (!reviewTypeF||n.productType===reviewTypeF)&&
    (!q||[n.title,n.productType,n.type,n.status,(n.products||[]).join(" ")]
      .some(v=>String(v||"").toLowerCase().includes(q))));
  const filtering=!!reviewTypeF||!!q;
  if($("rvClear")) $("rvClear").hidden=!filtering;
  const noteLink=n=>{
    const href=safeObsidianUrl(n.obsidianUrl);
    return href?`<a class="rv-open" href="${esc(href)}">打开笔记 ↗</a>`:"";
  };
  $("rvTag").textContent=filtering?`${filtered.length} / ${items.length} 篇`:`${items.length} 篇`;
  $("rvGroupTag").textContent=gs.length+" 类";
  const reviewRow=(n,lead=false)=>{
    // Obsidian 文件名约定「调试经验-xxx」，列表里 26 条重复前缀全是噪音，渲染时剥掉（不改源数据）。
    const cleanTitle=String(n.title||"").replace(/^调试经验-/,"");
    const cls=n.status?((/已(解决|闭环|修复|加固|确认|验证)/.test(n.status))?"chip ok":"chip"):"chip";
    // 标题点开站内只读预览；Obsidian 深链保留在行尾。产品 chip 点击即按该产品筛选，
    // 与收益页来源筛选同构 —— 筛选入口长在它筛选的数据旁边。
    const jsq=v=>String(v).replace(/\\/g,"\\\\").replace(/'/g,"\\'");
    const titleBtn=`<button class="rv-title-btn" type="button" data-note="${esc(n.noteId||"")}"`+
      ` data-title="${esc(cleanTitle)}" data-obs="${esc(n.obsidianUrl||"")}" onclick="openReviewNote(this)">${esc(cleanTitle)}</button>`;
    const prodChips=(n.products||[]).slice(0,4).map(p=>
      `<button class="chip chip-btn rv-prod" type="button" title="按 ${esc(p)} 筛选"`+
      ` onclick="setReviewProductFilter('${esc(jsq(p))}')">${esc(p)}</button>`).join("");
    const statusChip=(maxLen)=>n.status?`<span class="${cls}" title="${esc(n.status)}">`+
      `${esc(n.status.length>maxLen?n.status.slice(0,maxLen)+"…":n.status)}</span>`:"";
    if(lead) return `<div class="rv-lead"><span class="rv-kicker">最近一条 · ${esc(agoTxt(n.days))}</span>`+
      `<div class="rv-title">${titleBtn}</div>`+
      (n.summary?`<div class="rv-summary">${esc(n.summary)}</div>`:"")+
      `<div class="rv-meta"><span>${esc((n.date||"").slice(0,10))}</span>`+
        `<span class="chip">${esc(n.productType||"未标注")}</span>`+
        statusChip(18)+prodChips+noteLink(n)+`</div>`+
      `<div class="rv-desc">${esc(n.type||"复盘记录")} · ${esc(n.chars?n.chars.toLocaleString()+" 字":"字数未知")}</div></div>`;
    return `<div class="nitem"><div class="nt">${titleBtn}</div>`+
      (n.summary?`<div class="rv-summary">${esc(n.summary)}</div>`:"")+
      `<div class="nm2"><span>${esc((n.date||"").slice(0,10))}</span>`+
        `<span>${esc(agoTxt(n.days))}</span>`+
        `<span class="chip">${esc(n.productType)}</span>`+
        statusChip(14)+prodChips+noteLink(n)+
      `</div></div>`;
  };
  if(!items.length){
    $("rvItems").innerHTML=`<div class="empty">调试经验/ 下还没有复盘笔记。</div>`;
  }else if(!filtered.length){
    $("rvItems").innerHTML=`<div class="empty">没有匹配的复盘记录。清除筛选可查看全部 ${items.length} 篇。</div>`;
  }else{
    const lead=filtered[0], rest=filtered.slice(1), first=rest.slice(0,5), later=rest.slice(5);
    $("rvItems").innerHTML=reviewRow(lead,true)+
      `<div class="rv-more-head" style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:12px">`+
        `<h2 style="margin:0">${filtering?"筛选结果":"最近记录"}</h2><span class="tag">${filtered.length} 篇</span></div>`+
      (first.length?`<div class="nlist">${first.map(n=>reviewRow(n)).join("")}</div>`:`<div class="empty">只有这一条。</div>`)+
      (later.length?`<details class="rv-more"><summary>查看其余 ${later.length} 篇复盘<span>按日期倒序</span></summary>`+
        `<div class="nlist">${later.map(n=>reviewRow(n)).join("")}</div></details>`:"");
  }

  // 分布条复用「支出构成」那套 .exprow/.expbar/.expnote —— 同一个页面里
  // "一行一个分类 + 一根按比例的条"只该有一种画法。
  const mx=Math.max(1,...gs.map(g=>g.count));
  $("rvBreak").innerHTML=gs.length?gs.map(g=>
    `<div class="exprow"><div class="top"><span class="nm">${esc(g.name)}</span>`+
    `<span class="amt" style="color:var(--ink)">${g.count} 篇</span></div>`+
    `<div class="expbar"><i style="width:${Math.max(4,Math.round(g.count/mx*100))}%;background:var(--accent)"></i></div></div>`
  ).join("")+`<div class="expnote">共 ${gs.length} 类、${items.length} 篇。分组用的是笔记 frontmatter 里的
    product-type；缺 frontmatter 的记为「未标注」而不是丢掉 —— 丢掉会让"分组总数 == 篇数"这条守恒悄悄不成立。</div>`
    :`<div class="empty">暂无分组</div>`;
  $("rvNote").textContent=s.note||"";
  renderWeekly();
}

