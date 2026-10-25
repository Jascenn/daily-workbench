#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异测试：支持精确串替换与正则替换，逐条确认新断言会变红。

用法：python3 scripts/tests/mutate-workbench.py     （无需参数，靠 WB_HTML 指向临时副本）

注意：expect_red 存的是**断言名片段**。断言一旦改名（含加长/改口），这里必须同步改，
否则会出现"断言明明红了、脚本却报没红"的假警报（第 4 条踩过）。

2026-09-29 从 /tmp/mutate3.py 移进项目 —— 它被 notion-sync.md 与每日自动化引用，
放在 /tmp 里一旦重启就没了，"能变红才算断言"这条纪律会跟着失效。

**加新断言的正确顺序**：写断言 → 确认它现在是绿的 → 故意把被测代码改坏 → 确认它变红。
只做前两步的断言等于注释。第 46 条就是反例：我最初写的"预设值两两不相同"是**永远绿**的
（去重保证了它），改坏取整规则照样绿 —— 必须把"该有的参照一个都不能少"并进同一条才可证伪。
"""
from pathlib import Path
import subprocess, os, re, sys

ROOT = str(Path(__file__).resolve().parent.parent.parent)
SRC = f"{ROOT}/workbench.html"
NODE = "/opt/homebrew/bin/node"
TMP = "/tmp/wb-mut.html"

# (说明, kind, old, new, 期望变红的断言片段)
MUT = [
 ("对比度 A17：warn 用回改造前的浅琥珀 #f59e0b（只有 2.15:1）", "exact",
  "--warn:#a15c00;", "--warn:#f59e0b;", "浅色主题：10 组文字对比全部 ≥4.5:1"),
 ("对比度 A17：pos 用回旧红 #e4393c", "exact",
  "--pos:#c8292c;", "--pos:#e4393c;", "浅色主题：10 组文字对比全部 ≥4.5:1"),
 ("非文字对比 A17：控件边框退回装饰性发丝线", "exact",
  "--line-strong:#878d97;", "--line-strong:#e7ebf2;", "控件边框 --line-strong ≥3:1"),
 ("非文字对比：输入框改回用 --line（边界又看不见了）", "exact",
  "border:1px solid var(--line-strong);border-radius:var(--radius-md);background:var(--bg);color:var(--ink);padding:8px 10px",
  "border:1px solid var(--line);border-radius:var(--radius-md);background:var(--bg);color:var(--ink);padding:8px 10px",
  "每条给输入类控件设边框的规则都用 --line-strong"),
 ("字号纪律：塞回一个硬编码字号", "re",
  r"font-size:var\(--fs-[a-z0-9]+\)", "font-size:11px", "字号已全部走 --fs-*"),
 ("透明档漂移：把 pos 的透明档改回旧红 rgb(228,57,60)", "exact",
  "rgba(200,41,44,.1)", "rgba(228,57,60,.1)", "CSS 里每个 rgba() 的 RGB 都等于某个令牌的 RGB"),
 ("焦点环 V10：全局规则换成裸 outline:none", "exact",
  ":focus-visible{outline:none;box-shadow:0 0 0 2px var(--card),0 0 0 4px var(--accent)}",
  ":focus-visible{outline:none}", "全局 :focus-visible 规则自带 box-shadow 焦点环"),
 ("键盘委托：去掉 role=button 判断", "exact",
  'if(el.getAttribute("role")!=="button" || el.tabIndex!==0) return;',
  'if(el.tabIndex!==0) return;', "只有 role=button 的元素被激活"),
 ("键盘委托：去掉真 <button> 的排除（一次回车触发两次）", "exact",
  'if(tag==="BUTTON"||tag==="INPUT"||tag==="SELECT"||tag==="TEXTAREA"||tag==="A") return;',
  'if(tag==="INPUT"||tag==="SELECT"||tag==="TEXTAREA"||tag==="A") return;', "真 <button> 不被重复处理"),
 ("键盘委托：去掉 Space 的 preventDefault（会连带滚动页面）", "exact",
  "  e.preventDefault();          // Space 默认会滚动页面",
  "  // e.preventDefault();", "Space 同样转发，并阻止默认滚动"),
 ("撤销：改成追加到末尾（而不是插回原索引）", "exact",
  "state.earnings.splice(Math.min(idx,state.earnings.length),0,rec);",
  "state.earnings.push(rec);", "撤销后**插回原索引**"),
 ("撤销：删除时不登记待撤销记录", "exact",
  "  lastDel={rec:e, idx:idx};\n", "", "删除后待撤销记录已登记"),
 ("撤销：撤销后不清空 lastDel（会重复插回）", "exact",
  "  lastDel=null;\n  saveState();renderAll();",
  "  saveState();renderAll();", "撤销后撤销条收起、待撤销记录清空"),
 ("Tab 序：少补一处 tabindex+role=button", "exact",
  ' onclick="setSrcFilter(${i})" style="cursor:pointer" tabindex="0" role="button"',
  ' onclick="setSrcFilter(${i})" style="cursor:pointer"', "3 处整块可点的 div 进了 Tab 序"),
 ("label 关联：去掉一个 for=", "exact",
  '<label for="pName">名称</label>', '<label>名称</label>', "所有 <label> 补齐 for="),
 ("动效偏好 A26：把媒体名写错一位（整个块失效）", "exact",
  "@media(prefers-reduced-motion:reduce){", "@media(prefers-reduced-motion1:reduce){",
  "prefers-reduced-motion 媒体查询结构完整"),
 ("触屏 hover：删掉 @media(hover:none) 块", "exact",
  "  @media(hover:none){", "  @media(hoverX:none){", "@media(hover:none) 已把粘滞 hover"),
 ("提示通道：把一个 alert() 调用放回去", "exact",
  "function notify(msg,level){", 'function notify(msg,level){ alert("回退了");',
  "源码里已不存在任何 alert() 调用"),
 ("提示通道：notify 不再转义（文案里的 < > 会被当标签）", "exact",
  '<span class="ntxt">${esc(msg).replace(/\\n/g,"<br>")}</span>',
  '<span class="ntxt">${msg.replace(/\\n/g,"<br>")}</span>', "提示文案经 HTML 转义"),
 ("数字排版 V6：去掉 tabular-nums", "exact",
  "sans-serif;font-variant-numeric:tabular-nums;", "sans-serif;", "tabular-nums 已启用"),
 ("语义结构：去掉 <main>", "exact",
  "  <main id=\"main\" tabindex=\"-1\">", "", "<main> / <nav> 已补上"),
 ("ARIA：删掉屏读器状态通道", "exact",
  '<div class="undo" id="undo" role="status" aria-live="polite"></div>',
  '<div class="undo" id="undo"></div>', "屏读器状态通道"),
 ("反向断言：给账本 <tr> 加上 tabindex（257 个 Tab 停靠点）", "exact",
  "const click= editable? ` class=\"clickable\" onclick=\"editEarn('${e.id}')\" title=\"点击编辑\"`",
  "const click= editable? ` class=\"clickable\" tabindex=\"0\" onclick=\"editEarn('${e.id}')\" title=\"点击编辑\"`",
  "账本 <tr> 故意**没有**加 tabindex"),
 ("只读保护：删掉删除前的只读判断（真的会删掉 Notion 记录）", "exact",
  "  if(e.readOnly){notify(\"这条来自 Notion（只读拉取），不能在此删除。\\n如需调整，请到 Notion 里改，然后说「同步」即可更新。\",\"warn\");return;}\n",
  "", "只读记录拒绝删除"),
 ("内联错误：fieldErr 不再标记 aria-invalid", "exact",
  '  el.setAttribute("aria-invalid","true");\n}\nfunction clearFieldErr',
  '}\nfunction clearFieldErr', "出错字段标了 aria-invalid"),
 # ── 第 27 节（第四轮整改）────────────────────────────────────────────
 ("命中区 A25：伪元素只扩 2px（命中区缩到 36px，差 8px）", "exact",
  ".del::after,.edit::after{content:\"\";position:absolute;inset:-6px}",
  ".del::after,.edit::after{content:\"\";position:absolute;inset:-2px}",
  "有效命中区 = 32 + 6 + 6 = 44"),
 # 这一条是第 27 节里最重要的：-12px 正是评审报告给的建议，
 # 它让"命中区 44"仍然绿（32+24=56 ≥ 44），只有"不重叠"该红。
 # 如果这条没红，说明"不重叠"只是句注释。
 ("热区重叠：照抄报告的 inset:-12px（命中区 56px 仍达标，但两个热区重叠 12px）", "exact",
  ".del::after,.edit::after{content:\"\";position:absolute;inset:-6px}",
  ".del::after,.edit::after{content:\"\";position:absolute;inset:-12px}",
  "两个热区各扩 6px 后**不重叠**"),
 ("热区重叠：把 12px 空隙拿掉（删错一笔就只差一个像素）", "exact",
  ".del{margin-left:12px}", ".del{margin-left:0}", "✎ 与 ✕ 之间留了 12px 空隙"),
 ("命中区：删掉 position:relative（伪元素失去定位锚，热区塌回 0）", "exact",
  ".del,.edit{position:relative;min-width:32px;", ".del,.edit{min-width:32px;",
  ".del/.edit 有 position:relative"),
 ("反向锁定：为了凑 44px 把盒子撑到 44px（257 行账本会被撑高）", "exact",
  ".del,.edit{position:relative;min-width:32px;min-height:32px;",
  ".del,.edit{position:relative;min-width:44px;min-height:44px;",
  "视觉尺寸**保持不变**"),
 ("表头：删掉 <thead>（读屏用户又不知道每列是什么）", "exact",
  "<thead><tr><th scope=\"col\">日期</th><th scope=\"col\">分类</th><th scope=\"col\">备注</th><th scope=\"col\" class=\"num\">金额</th></tr></thead>\n          ",
  "", "账本表有 <thead>"),
 ("表头：去掉 scope=\"col\"", "exact",
  "<th scope=\"col\">", "<th>", "每列都带 scope=\"col\""),
 ("表头：金额列去掉 .num（数字与表头对不齐）", "exact",
  "<th scope=\"col\" class=\"num\">金额</th>", "<th scope=\"col\">金额</th>",
  "金额列表头带 .num"),
 ("反向锁定：把表头改成「仅读屏可见」（视觉收益直接丢掉）", "exact",
  "#ledger thead th{font-size:var(--fs-xs);", "#ledger thead th{position:absolute;font-size:var(--fs-xs);",
  "表头是可见的"),
 ("跳转链：把它挪到 <main> 之后（等于没有）", "re",
  r'(?s)(<a class="skip" href="#main">跳到主内容</a>\n)(.*?)(<main id="main"[^>]*>)',
  r'\3\2\1',  "跳转链在 <main> **之前**"),
 ("跳转链：聚焦时不再滑进来（永远看不见）", "exact",
  ".skip:focus{top:8px}", ".skip:focus{top:-48px}", "跳转链**聚焦时**才滑进来"),
 ("模态：取消按钮丢掉 type=\"button\"（在 form 里它默认就是 submit，点取消会保存）", "exact",
  '<button class="btn ghost" type="button" onclick="closeOv()">取消</button>',
  '<button class="btn ghost" onclick="closeOv()">取消</button>',
  '两个"取消"都显式写了 type="button"'),
 ("模态：保存按钮加回 onclick（onclick + onsubmit = 同一笔存两次）", "exact",
  '<button class="btn primary" type="submit">保存</button>',
  '<button class="btn primary" type="submit" onclick="saveEarn()">保存</button>',
  "保存按钮上没有 onclick"),
 ("模态：submitEarn 不再转交 saveEarn（只 return false 也能骗过「返回 false」那条）", "exact",
  "function submitEarn(e){ if(e&&e.preventDefault) e.preventDefault(); saveEarn(); return false; }",
  "function submitEarn(e){ if(e&&e.preventDefault) e.preventDefault(); return false; }",
  "submitEarn 真的走到了 saveEarn"),
 ("模态：onsubmit 不再 return（默认提交没被拦住）", "exact",
  '<form id="earnForm" onsubmit="return submitEarn(event)">',
  '<form id="earnForm" onsubmit="submitEarn(event)">',
  '收益模态包在 <form id="earnForm">'),
 ("模态：让 form 带回 UA 默认外边距", "exact",
  "#projForm,#earnForm{margin:0}", "#projForm,#earnForm{margin:1em}",
  "form 不带来 UA 默认外边距"),
 ("跳转链：去掉跳转目标的 tabindex=\"-1\"（回车后焦点接不住，等于没跳）", "exact",
  '<main id="main" tabindex="-1">', '<main id="main">',
  '跳转目标 <main> 带 tabindex=\"-1\"'),
 ("R8：移动断点下输入框退回 14px（iOS 聚焦会放大整页且不缩回）", "exact",
  ".ledbar input,.ledbar select,.field input,.field select,.field textarea{font-size:var(--fs-lg)}",
  ".ledbar input,.ledbar select,.field input,.field select,.field textarea{font-size:var(--fs-base)}",
  "移动断点(≤560px)把输入类控件提到 16px"),
 ("金额列换行：去掉 .amt 的 white-space:nowrap（✎/✕ 会被挤到第二行，行高 56→81）", "exact",
  "td.amt{font-weight:700;text-align:right;white-space:nowrap}",
  "td.amt{font-weight:700;text-align:right}",
  "金额列不换行"),
 ("日期列换行：去掉日期列的 white-space:nowrap（257 行日期断成两行）", "exact",
  "#ledger tbody td:first-child{white-space:nowrap}", "",
  "日期列不换行"),
 # —— 2026-09-30：快捷预设的取整与去重（真机上 4 个按钮有 3 个显示 ¥300）——
 # 这条**不会**让"两两不相同"那种写法变红 —— 去重会把三个 300 收成一个，
 # 界面结构上不会出现重复按钮。真正红的是"参照齐全"那一半：+20% 与"按当前节奏"被静默吃掉。
 # 教训：单说"ps 两两不相同"是**永远绿**的（去重保证了它），必须把"该有的参照一个都不能少"
 # 并进同一条断言，它才可证伪。这也说明两条性质各管一半，缺一条就有半个缺陷逃得掉。
 ("预设取整退回固定 100：'上月+20%'与'按当前节奏'两个参照被静默吃掉（去重把三个 300 收成一个）", "exact",
  "const nice=v=>{ const st = v>=10000?1000 : v>=1000?100 : v>=100?10 : 1;",
  "const nice=v=>{ const st = 100;",
  "预设两两不相同，且每个可用的参照都还在"),
 ("预设去重失效：dedupeByValue 不再判重（取整撞值时会摆出两个结果一样的按钮）", "exact",
  "if(!(p.v>0) || seen.has(p.v)) return false;",
  "if(!(p.v>0)) return false;",
  "预设撞值时会去重"),
 ("预设去重不再滤掉非正值（v ≤ 0 的预设会漏进按钮列表）", "exact",
  "if(!(p.v>0) || seen.has(p.v)) return false;",
  "if(seen.has(p.v)) return false;",
  "去重顺手滤掉非正值"),
 # —— 2026-09-30 凌晨：功能类型区（资讯 / 写作 / 复盘）——
 # 这一批里最值钱的三条：安全（safeUrl）、布局（.ns 的 nowrap）、显示上限（NEWS_PAGE）。
 # 都是"改坏了页面照样能跑、只是信息悄悄变了/撑破了"的类型。
 ("safeUrl 不再校验 scheme：javascript: 链接会直接进 href", "exact",
  'return /^https?:\\/\\//i.test(s) ? s : "";', "return s;",
  "safeUrl 拦掉 javascript:"),
 ("safeUrl 只放行 https，不再放行 http", "exact",
  "return /^https?:\\/\\//i.test(s) ? s : \"\";", "return /^https:\\/\\//i.test(s) ? s : \"\";",
  "safeUrl 放行 http / https"),
 # 这条用**原始字符串**写：上一版写成普通字符串时少了一个斜杠，
 # 变异出来的 JS 是 `/^https?:\/\/.test(...)`（正则没闭合）→ 整个测试文件 SyntaxError，
 # 脚本只会含糊地报"没红"。原始字符串在这类"被变异的就是转义字符本身"的场景里更安全。
 ("safeUrl 去掉大小写不敏感（HTTP:// 与 JaVaScRiPt: 都会漏过去）", "exact",
  r'return /^https?:\/\//i.test(s) ? s : "";', r'return /^https?:\/\//.test(s) ? s : "";',
  "safeUrl 放行 http / https"),
 ("摘要退回 white-space:nowrap（min-content = 968px → fr 轨道被顶开、整页横向溢出）", "exact",
  "display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;",
  "white-space:nowrap;text-overflow:ellipsis;",
  "摘要 .ns 不再用 white-space:nowrap"),
 ("资讯首屏不再限制条数（窗口内全铺，首屏上限失效）", "exact",
  "newsScope===\"recent\" ? fRecent.slice(0,NEWS_PAGE)", "newsScope===\"recent\" ? fRecent",
  "默认档位渲染条数"),
 ("把显示上限本身调到 9999（只断言「渲染数 == min(NEWS_PAGE,…)」是拦不住的）", "exact",
  "const NEWS_PAGE=20;", "const NEWS_PAGE=9999;",
  "默认档位渲染条数"),
 ("三档按钮把条数写两遍（「最近 20  20」+ 双空格）", "exact",
  'b.textContent=labOf[k]+" "+nOf[k];', 'b.textContent=labOf[k]+" "+nOf[k]+" "+nOf[k];',
  # 期望串必须跟断言名**逐字**同步。这里踩过一次：断言从「= 档位名 + 真实条数」改成
  # 「逐字 == …」后，这条变异其实已经红了（脚本自己也把失败名打出来了），
  # 但期望串还是旧名 → 匹配不上 → 报「没红」。
  # 教训：脚本报"没红"时，先看它打出的「实际失败」列表里有没有那条断言 ——
  # 有，就是期望串过期；没有，才是断言不够严。
  "三档按钮文字逐字 == 档位名 + 真实条数"),
 ("选中态只改视觉不更新 aria-pressed（屏幕阅读器一直念「未按下」）", "exact",
  'b.setAttribute("aria-pressed",on?"true":"false");', "",
  "选中态与 aria-pressed 同源更新"),
 ("renderAll 漏调 renderSections（数据同步完，那一屏还是旧的）", "exact",
  "  renderTypeBar();\n  renderSections();\n  renderAiUsage();", "  renderTypeBar();\n  renderAiUsage();",
  "renderAll 会把当前类型区一起重渲染"),
 ("离线时不再回退本地快照（file:// 打开只剩一张空屏）", "exact",
  'const t=localStorage.getItem("wb_sections");', "const t=null;",
  "sections 已加载"),
 ("freshBar 把「无数据」反过来判（空数据被标成「已过期」）", "exact",
  "const badge=f.empty?", "const badge=!f.empty?",
  "freshBar 不把 null 显示成"),
 ("agoTxt 把 null 说成「今天」（把「没有时间」伪装成「刚发生」）", "exact",
  'if(d===null||d===undefined) return "时间未知";',
  'if(d===null||d===undefined) return "今天";',
  "agoTxt 的边界"),
 ("freshBar 对过期与新鲜一视同仁（两种状态渲染成同一个徽标）", "exact",
  ':(f.stale?`<span class="chip warn">已过期</span>`:`<span class="chip ok">新鲜</span>`);',
  ":(f.stale?`<span class=\"chip ok\">新鲜</span>`:`<span class=\"chip ok\">新鲜</span>`);",
  "freshBar 对「过期」与「新鲜」给出不同标记"),
 ("写作进度改用目标区间**上限**当分母（永远显得「几乎没写」）", "exact",
  "Math.round(b.chars/tgt.min*100)", "Math.round(b.chars/tgt.max*100)",
  "进度按目标区间**下限**算"),
 ("未写章节不再标注「（未写）」（只剩一个淡色圆点，状态不可读）", "exact",
  '${c.written?"":"（未写）"}', "",
  "未写的章节在页面上有「（未写）」文字"),
 ("章节状态标记与文字不符：把 done 类一律去掉（已写的看起来也没写）", "exact",
  'class="chrow${c.written?" done":""}"', 'class="chrow"',
  "已写行数 == writtenCount"),
 # —— 2026-09-30 凌晨：日期矩阵（第 30 节）——
 # 这两条守的是"目标行动建议的四个分支"。为什么必须用变异守：
 # 那批断言是**按日期选支**的，日常跑只覆盖当天那一支；第 30 节把时钟注入进来
 # 一次跑遍四支，而这两条变异就是"四支的判别器真的能分辨"的证明 ——
 # 没有它们，第 30 节可能只是把四段文案重新念了一遍。
 ("删掉「本月已结束」这一支（最后一天跌进 tooLate 分支：本月只剩 0 天…顺延到下月）", "exact",
  "}else if(left<=0){", "}else if(false){",
  "2026-09-30 / 目标 100000"),
 ("tooLate 的界从 left<=3 收紧到 left<=0（剩 1~3 天又回到没有行动价值的日均需）", "exact",
  "const tooLate=left<=3;", "const tooLate=left<=0;",
  "2026-09-27 / 目标 100000"),
 # 注：判别器里那个 '????' 兜底（以及 extra 那一支）无法用本脚本变异 ——
 # 本脚本只改 workbench.html，而 branchOf 在测试文件里。
 # 它靠"判别器写错时当场返回 ???? 并让汇总失败"这条设计自证（第一次就抓到我把
 # 「超额」记成了「已超额」），不另设变异。
 # —— 2026-09-30 00:30：同步出口矩阵（第 12.1 节）——
 # 守的是"标色必须和结果一致"这条契约的两个方向。为什么必须用变异守：
 # 真机那侧的成功出口由外部链路决定（当天连续三次 180s 超时），
 # 注入这侧才是唯一能稳定跑到成功出口的地方 —— 如果这些断言其实分辨不出来，
 # 那就等于"成功出口从来没被测过"这件事被一层绿遮住了。
 # 三条分别打在三个不同出口上：成功（不许 err）、409（必须 warn）、fetch 抛异常（必须 err）。
 # 2026-10：runSync 的出口展示已从 out.className="syncout" 演进为 notify() 通知条，
 # 变异目标跟着改：改坏的是 notify 的 level 参数，断言按 level 变红。
 ("同步成功的出口被标成错误样式（同步明明成功了却显示成失败）", "exact",
  '`,"info");\n      await loadState(); renderAll();',
  '`,"err");\n      await loadState(); renderAll();',
  "同步出口「成功」"),
 ("409「已有一次同步在进行」不再标 warn（把并发冲突显示成失败）", "exact",
  'notify("已有一次同步正在进行，请稍候几秒再点。","warn");',
  'notify("已有一次同步正在进行，请稍候几秒再点。","err");',
  "同步出口「409 已有同步在进行」"),
 ("fetch 抛异常（本地服务没开）不再标 err —— 最危险的一支：挂掉却显示成正常", "exact",
  '"同步请求失败："+e.message+"\\n确认本地同步服务在 127.0.0.1:8787 上运行。");',
  '"同步请求失败："+e.message+"\\n确认本地同步服务在 127.0.0.1:8787 上运行。","info");',
  "同步出口「fetch 抛异常（服务没开）」"),
]

orig = open(SRC, encoding="utf-8").read()
print(f"变异测试：{len(MUT)} 条\n" + "=" * 74)
ok = bad = 0
for i, (why, kind, old, new, expect_red) in enumerate(MUT, 1):
    if kind == "exact":
        n = orig.count(old)
        # 同一处旧色值可能出现在多条规则里（如 pos 的透明档有 3 处）。
        # 全改掉才是正确的变异；但要确认至少命中一次，否则是变异自身写错了。
        if n < 1:
            print(f"{i:2d}. ✗ 变异自身有问题：精确串命中 0 次 —— {why}"); bad += 1; continue
        mutated = orig.replace(old, new)
        print(f"      （该变异命中 {n} 处，全部改掉）")
    else:
        mutated, n = re.subn(old, new, orig, count=1)
        if n != 1:
            print(f"{i:2d}. ✗ 变异自身有问题：正则命中 {n} 次 —— {why}"); bad += 1; continue
    assert mutated != orig, f"变异未生效：{why}"
    open(TMP, "w", encoding="utf-8").write(mutated)
    r = subprocess.run([NODE, f"{ROOT}/scripts/tests/test-workbench-logic.mjs"],
                       capture_output=True, text=True, env={**os.environ, "WB_HTML": TMP})
    out = r.stdout + r.stderr
    fails = [l.strip()[2:].split("  →")[0].strip() for l in out.split("\n") if l.strip().startswith("✗")]
    # 变异把文件改成了非法 JS 时，测试会在加载阶段就 SyntaxError 退出：
    # 既没有失败断言也没有汇总。这种必须报"变异自身有问题"，
    # 不能含糊地说"没红" —— 否则会把"我的变异写错了"误读成"断言不够严"，
    # 然后去改一条本来正确的断言（这次真的踩到了，见 safeUrl 那两条）。
    if "SyntaxError" in out or "ReferenceError" in out:
        line = next((l for l in out.split("\n") if "Error" in l), "")[:80]
        print(f"{i:2d}. ✗ 变异自身有问题：改出来不是合法 JS —— {why}")
        print(f"      {line}")
        bad += 1
        continue
    hit = any(expect_red in f for f in fails)
    tail = [l for l in out.split("\n") if l.startswith("结果：")]
    print(f"{i:2d}. {'✓ 变红' if hit else '✗✗ 没红'}  {why}")
    print(f"      {tail[0] if tail else '(无汇总)'}" + (f"  | 期望命中『{expect_red[:26]}…』" if not hit else ""))
    # 没有汇总行 = 测试在打印"结果："之前就退出了（通常是抛异常中断）。
    # 这时"变红"是**碰巧**的：某条断言恰好先失败并被上面匹配到，但整个文件已被截断，
    # 后面几百条根本没跑 —— 拿这种红当"断言有效"会给出虚假的安全感。
    # 第 58 条实测过：读 S.sections 的消息参数抛 TypeError，输出停在第 3 条。
    # 正确做法是修测试（让它早点干净跳出），而不是把这条算成通过。
    if not tail:
        print(f"      ⚠️ 测试提前退出、没有汇总 —— 这条红是碰巧的，需先修测试再重跑")
        bad += 1
        continue
    if hit: ok += 1
    else:
        bad += 1
        print(f"      实际失败：{fails[:4]}")
print("=" * 74)
# 措辞注意：bad 里除了"没红"，还有"变异自身有问题"和"测试崩溃导致的碰巧红"。
# 只说"未变红 N 条"会让人去翻断言，而实际该修的可能完全在别处。
print(f"变异结果：{ok} / {len(MUT)} 条按预期变红；"
      f"另有 {bad} 条需处理（没红 / 变异自身有问题 / 测试提前退出 —— 逐条看上面标注）")
if os.path.exists(TMP): os.remove(TMP)
sys.exit(1 if bad else 0)
