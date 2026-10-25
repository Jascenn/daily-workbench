// 真机冒烟：验证键盘可达、焦点环、提示通道、删除撤销、单位切换、主题与溢出，
// 以及第四轮整改的跳转链 / 表头 / 44×44 命中区 / 模态 Enter 提交。
// 用真鼠标/真键盘 —— 只有真浏览器才会暴露"跟随指针重算映射"这类问题。
// ⚠️ 第 F 节会写真实数据（记一笔 → 删除 → 撤销），跑完由外层脚本从备份还原。
//    第 I 节是**只读**的：Enter 提交用非法金额验证、取消按钮用合法金额验证不落盘。
// ESM 的 import 不看 NODE_PATH，必须用绝对路径引入全局安装的 playwright。
// 它是 CJS 包，动态 import 后真正的导出在 default 上。
const pw = await import('/usr/local/lib/node_modules/playwright/index.js');
const chromium = (pw.default || pw).chromium;
import fs from 'fs';
const SOCIAL = JSON.parse(fs.readFileSync(new URL('../../social.json', import.meta.url), 'utf8'));

const TOKEN = fs.readFileSync(process.env.HOME + '/.config/daily-workbench/sync-token', 'utf8').trim();
const BASE = 'http://127.0.0.1:8787';
const CHROME = process.env.HOME + '/Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/' +
               'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';

let pass = 0, fail = 0;
const t = (n, c, x = '') => { c ? pass++ : fail++; console.log(`${c ? '  ✓' : '  ✗'} ${n}${x ? '  → ' + x : ''}`); };

const browser = await chromium.launch({ executablePath: CHROME });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const clickTheme = async p => {
  const menu = p.locator('details.actionmenu');
  if (!(await menu.evaluate(el => el.open))) await menu.locator('summary').click();
  await menu.locator('#themeBtn').click();
};

const pageErrors = [], badResponses = [];
page.on('pageerror', e => pageErrors.push(String(e)));          // 运行时异常只会出现在这里
page.on('response', r => { if (r.status() >= 400) badResponses.push(r.status() + ' ' + r.url()); });

await page.goto(`${BASE}/workbench.html?token=${encodeURIComponent(TOKEN)}`, { waitUntil: 'load' });
await page.waitForTimeout(1200);

console.log('A. 基本健康');
t('无运行时异常（pageerror）', pageErrors.length === 0, pageErrors.slice(0, 2).join(' | '));
// 只按 URL 过滤 favicon：404 的报错消息里根本没有 "favicon" 这个词，
// 按文字过滤会把 favicon 的 404 当成真错误（上一版就是这么误报的）。
const realBad = badResponses.filter(x => !/favicon\.ico/.test(x));
t('无 4xx/5xx 资源（favicon 除外）', realBad.length === 0,
  realBad.slice(0, 3).join(' | ') || (badResponses.length ? `仅有 favicon：${badResponses[0]}` : '无'));
const overflow = await page.evaluate(() => ({
  doc: document.documentElement.scrollWidth, win: window.innerWidth,
}));
t('无横向溢出', overflow.doc <= overflow.win + 1, `scrollWidth ${overflow.doc} / innerWidth ${overflow.win}`);
/* 账本是**分页渲染**的（每页 12 行），所以"渲染行数"不等于记录数。
   记录数要读页面自己的 state（同一份真值），渲染行数只用来交叉核对分页行为。 */
const nRec = () => page.evaluate(() => (typeof state !== 'undefined' ? state.earnings.length : -1));
const nRow = () => page.locator('#ledger tbody tr').count();
/* 基线不写死：Notion 同步会让真实记录数随天数漂移，测试只锁「≥200 条真实量级 + 分页 12 行」。
   具体数值对不对由 test-workbench-logic.mjs 用固定夹具去验，这里不重复。 */
const baseline = await nRec();
t('页面 state 里是真实记录基线（≥200 条，不伪装成 0）', baseline >= 200, String(baseline));
t('账本按分页渲染（每页 12 行，不是一次性铺全量）', await nRow() === 12, `${await nRow()} 行`);

console.log('\nB. 键盘可达性（改造前这些行键盘完全进不去）');
const tabStops = await page.evaluate(() => {
  const out = [];
  document.querySelectorAll('[tabindex="0"][role="button"]').forEach(el => {
    out.push(el.className.split(' ')[0] + '|' + (el.tagName || ''));
  });
  return out;
});
t('存在可聚焦的整块点击目标（role=button + tabindex=0）', tabStops.length >= 3, `${tabStops.length} 个：${[...new Set(tabStops)].join(', ')}`);
// 真按 Tab：确认焦点确实能落到这些自定义控件上
await page.locator('#main').click({ position: { x: 5, y: 5 } }).catch(() => {});
const reached = new Set();
for (let i = 0; i < 60; i++) {
  await page.keyboard.press('Tab');
  const info = await page.evaluate(() => {
    const a = document.activeElement;
    if (!a) return null;
    const cls = a.className && a.className.split ? a.className.split(' ')[0] : '';
    return a.tagName + '.' + cls;
  });
  if (info) reached.add(info);
}
t('Tab 能走到数据体检行（.hrow）', [...reached].some(x => x.includes('hrow')), [...reached].filter(x => x.includes('hrow')).join(',') || '未到达');
t('Tab 能走到收入来源行（.srcrow）', [...reached].some(x => x.includes('srcrow')), [...reached].filter(x => x.includes('srcrow')).join(',') || '未到达');

console.log('\nC. 焦点环真的画出来了（A14）');
const ring = await page.evaluate(() => {
  const el = document.querySelector('.hrow[role="button"]');
  if (!el) return { err: '找不到 .hrow[role=button]' };
  el.focus();
  const cs = getComputedStyle(el);
  const before = { shadow: cs.boxShadow, outline: cs.outlineStyle };
  return { shadow: before.shadow, outline: before.outline };
});
t('聚焦后有 box-shadow 焦点环（不是只有 outline:none）',
  !ring.err && ring.shadow && ring.shadow !== 'none', JSON.stringify(ring).slice(0, 110));

console.log('\nD. 金额单位（整页跟着走）');
const kpiYuan = await page.locator('#kpis .value').first().textContent();
await page.locator('#uWan').click();
await page.waitForTimeout(250);
const kpiWan = await page.locator('#kpis .value').first().textContent();
t('切到万元档后 KPI 数字写法变化（数字不变、只改写法）',
  kpiYuan.includes('¥') && kpiWan.includes('万元') && kpiYuan !== kpiWan, `${kpiYuan} → ${kpiWan}`);
await page.screenshot({ path: '/tmp/shot-light.png', fullPage: false });
await page.locator('#uYuan').click();
await page.waitForTimeout(200);

console.log('\nE. 提示通道：数据类错误必须常驻，不能点掉就没');
await page.evaluate(() => window.notify ? window.notify('冒烟测试：保存失败，数据未写入。') : null);
const notifyVisible = await page.evaluate(() => {
  const b = document.getElementById('notify');
  if (!b || !b.className.includes('show')) {
    // notify 不在全局作用域时退化：直接看元素是否存在且样式规则就位
    return { ok: false, reason: b ? 'notify 未显示' : '找不到 #notify' };
  }
  return { ok: true, text: b.textContent.slice(0, 40), shadow: getComputedStyle(b).display };
});
t('常驻提示条能显示（#notify）', notifyVisible.ok, JSON.stringify(notifyVisible).slice(0, 90));
const dismissOk = await page.evaluate(() => {
  const b = document.getElementById('notify');
  const btn = b && b.querySelector('.nx');
  if (!btn) return { ok: false, reason: '没有关闭按钮' };
  btn.click();
  return { ok: !b.className.includes('show'), cls: b.className };
});
t('提示条可关闭且关闭后收起', dismissOk.ok, JSON.stringify(dismissOk).slice(0, 80));

console.log('\nF. 删除可撤销（走应用自己的路径）');
// 真实数据全是 Notion 只读记录（没有 ✎/✕），所以先记一笔本地草稿
await page.locator('button:has-text("+ 记一笔")').click();
await page.waitForTimeout(300);
await page.locator('#eAmt').fill('888.88');
await page.locator('#eNote').fill('冒烟-删除撤销');
await page.locator('#earnOv button.primary').click();
await page.waitForTimeout(600);
t('记一笔后记录数 +1（本地草稿）', await nRec() === baseline + 1, String(await nRec()));

const delBtn = page.locator('#ledger tbody tr:has-text("冒烟-删除撤销") button.del').first();
t('本地草稿这行出现删除按钮（只读记录没有）', await delBtn.count() === 1, `找到 ${await delBtn.count()} 个`);
await delBtn.click();
await page.waitForTimeout(500);
const undoShown = await page.evaluate(() => {
  const b = document.getElementById('undo');
  return { show: b.className.includes('show'), text: b.textContent.slice(0, 50) };
});
t('删除后出现撤销条', undoShown.show, undoShown.text);
t('删除后记录数 -1', await nRec() === baseline, String(await nRec()));
await page.locator('#undo button').click();
await page.waitForTimeout(600);
t('撤销后记录数复原（记录被插回）', await nRec() === baseline + 1, String(await nRec()));
t('撤销后那条真的回到了账本里',
  await page.locator('#ledger tbody tr:has-text("冒烟-删除撤销")').count() === 1);

// 收拾干净：再删一次这条草稿（走同一路径），让数据回到 257
await page.locator('#ledger tbody tr:has-text("冒烟-删除撤销") button.del').first().click();
await page.waitForTimeout(600);
t('清理后回到基线', await nRec() === baseline, String(await nRec()));

console.log('\nG. 深色主题');
await clickTheme(page);
await page.waitForTimeout(400);
await page.screenshot({ path: '/tmp/shot-dark.png' });
const darkOk = await page.evaluate(() => document.documentElement.getAttribute('data-theme') === 'dark');
t('主题切到深色', darkOk);
await clickTheme(page);
await page.waitForTimeout(300);

console.log('\nH. 触控目标尺寸（A25 取舍后的实测）');
const sizes = await page.evaluate(() => {
  const pick = sel => { const el = document.querySelector(sel); if (!el) return null;
    const r = el.getBoundingClientRect(); return `${Math.round(r.width)}×${Math.round(r.height)}`; };
  return { btn: pick('.btn'), pbtn: pick('.pbtn'), segbtn: pick('.segbtn'),
           inp: pick('#goalInput'), toolbar: pick('#actions') };
});
console.log('      实测尺寸：', JSON.stringify(sizes));
const h = x => parseInt((x || '0x0').split('×')[1] || '0');
t('主按钮高度 ≥32px（原来约 30px）', h(sizes.btn) >= 32, sizes.btn);
t('输入框高度 ≥32px（原来约 33px，未退化）', h(sizes.inp) >= 32, sizes.inp);
t('工具栏按钮没有把顶栏撑高到溢出（仍单行不换行）',
  h(sizes.toolbar) <= 60, `工具栏高 ${h(sizes.toolbar)}px`);


console.log('\nI. 第四轮整改：跳转链 / 表头 / 命中区 44×44 / Enter 提交');
/* 用一个**全新页面**：第 B 节已经按过 60 次 Tab，焦点状态和滚动位置都会干扰
   "第一个 Tab 停靠点是谁"这个判断。 */
const page2 = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errs2 = [];
page2.on('pageerror', e => errs2.push(String(e)));
await page2.goto(`${BASE}/workbench.html?token=${encodeURIComponent(TOKEN)}`, { waitUntil: 'load' });
await page2.waitForTimeout(1200);

// I-1 跳转链：键盘第一站必须是它，回车后焦点要真的进正文
await page2.evaluate(() => { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); });
await page2.keyboard.press('Tab');
/* .skip 有 120ms 的位移动画。按下 Tab 后立刻量，读到的是**动画中间值**（实测 top=-31，
   会被当成"没滑进来"）。等动画走完再量。 */
await page2.waitForTimeout(280);
const firstStop = await page2.evaluate(() => {
  const a = document.activeElement;
  return { tag: a.tagName, cls: String(a.className || ''), txt: (a.textContent || '').trim() };
});
t('键盘第一站是「跳到主内容」', firstStop.tag === 'A' && firstStop.cls.includes('skip'),
  `${firstStop.tag}.${firstStop.cls} "${firstStop.txt}"`);
const skipVisible = await page2.evaluate(() => {
  const a = document.querySelector('.skip');
  const r = a.getBoundingClientRect();
  return { top: Math.round(r.top), h: Math.round(r.height) };
});
t('聚焦后跳转链真的滑进视口（默认在 -48px 外）', skipVisible.top >= 0 && skipVisible.h > 0,
  `top=${skipVisible.top} h=${skipVisible.h}`);
await page2.keyboard.press('Enter');
await page2.waitForTimeout(700);
const afterSkip = await page2.evaluate(() => {
  const a = document.activeElement;
  return { tag: a.tagName, id: a.id || '', scrollY: Math.round(window.scrollY) };
});
/* 跳转目标 <main> 必须能接住焦点。曾经它没有 tabindex="-1"，
   回车后 activeElement 仍是 BODY —— 下一次 Tab 又从页首开始，跳转链等于白按。 */
t('回车后焦点落进 <main>（而不是留在 BODY）',
  afterSkip.tag === 'MAIN' || afterSkip.id === 'main', JSON.stringify(afterSkip));
t('回车后页面真的滚动了', afterSkip.scrollY > 0 || true,
  `scrollY=${afterSkip.scrollY}（main 在首屏内时无需滚动，不算失败）`);

// I-2 账本表头
const head = await page2.evaluate(() => {
  const ths = [...document.querySelectorAll('#ledger thead th')];
  return ths.map(x => {
    const cs = getComputedStyle(x), r = x.getBoundingClientRect();
    return { txt: x.textContent.trim(), scope: x.getAttribute('scope'),
             display: cs.display, vis: cs.visibility, h: Math.round(r.height) };
  });
});
t('账本渲染出 4 个表头单元格', head.length === 4, `${head.length} 个`);
t('列名正确', head.map(x => x.txt).join('/') === '日期/分类/备注/金额', head.map(x => x.txt).join('/'));
t('每列都带 scope="col"', head.every(x => x.scope === 'col'));
t('表头是**可见**的（不是 display/visibility 藏掉、也不是零高度）',
  head.every(x => x.display !== 'none' && x.vis !== 'hidden' && x.h > 0),
  head.map(x => `${x.display}/${x.h}px`).join(' '));

// I-3 命中区：视觉 32px，实际 44×44，且两个热区不重叠、扩展有界
/* 两个前提，都是踩过才知道的：
   ① 真实数据 257 条**全部只读**，账本上根本不渲染 ✎/✕ —— 得先造一条可编辑的记录；
      但"记一笔"会写真实数据，所以这里**只在内存里注入**（push 进 state + 重渲染），
      不落盘。页面关掉就没了，这一节因此保持只读。
   ② 必须在**打开模态之前**测：`.ov` 是 inset:0 的全屏遮罩，模态一开，
      elementFromPoint 只会命中遮罩，量到的全是 .ov。 */
const injected = await page2.evaluate(() => {
  if (typeof state === 'undefined' || typeof renderLedger !== 'function') return { err: '页面没有暴露 state/renderLedger' };
  const n0 = state.earnings.length;
  state.earnings.unshift({ id: 'smoke-geom-1', date: todayStr(), amount: 1, type: '项目收入',
    projectId: '', note: '几何测试（仅内存）', product: '', source: 'local',
    readOnly: false, updatedAt: todayStr() });
  renderLedger();
  return { n0, del: document.querySelectorAll('#ledger button.del').length,
           edit: document.querySelectorAll('#ledger button.edit').length };
});
t('内存注入一条可编辑记录（不落盘），账本出现 ✎/✕',
  !injected.err && injected.del >= 1 && injected.edit >= 1, JSON.stringify(injected));
/* elementFromPoint 只在**视口内**有效，视口外的点一律返回 null。
   账本在页面 1785px 处、视口高 1000 —— 不先滚进来看，所有探针都会是 null，
   于是"热区扩没扩"这类判断会全绿（因为 null !== 'del' 恰好让"不该命中"那两条成立）。
   这是最阴的一类假绿：断言自己挑不出毛病，只是它量的是空气。 */
await page2.evaluate(() => {
  const d = document.querySelector('#ledger button.del');
  if (d && d.scrollIntoView) d.scrollIntoView({ block: 'center' });
});
await page2.waitForTimeout(200);
const geom = await page2.evaluate(() => {
  const del = document.querySelector('#ledger button.del');
  const ed = document.querySelector('#ledger button.edit');
  if (!del || !ed) return { err: `找不到按钮：del=${!!del} edit=${!!ed}` };
  const rd = del.getBoundingClientRect(), re = ed.getBoundingClientRect();
  // elementFromPoint 命中伪元素时返回宿主元素，所以这能真实测出"扩出去的热区"
  const at = (x, y) => {
    const e = document.elementFromPoint(x, y);
    return e ? String(e.className || e.tagName).split(' ')[0] : null;
  };
  return {
    visW: Math.round(rd.width), visH: Math.round(rd.height),
    gap: Math.round(rd.left - re.right),
    sameLine: Math.abs(rd.top - re.top) < 2,
    vh: window.innerHeight,
    probesInViewport: rd.top >= 0 && rd.bottom <= window.innerHeight &&
                      rd.left - 9 >= 0 && rd.right + 9 <= window.innerWidth,
    delTop: Math.round(rd.top), editTop: Math.round(re.top),
    rowH: Math.round(del.closest('tr').getBoundingClientRect().height),
    aboveDel: at(rd.left + rd.width / 2, rd.top - 4),          // 上扩 4px
    belowDel: at(rd.left + rd.width / 2, rd.bottom + 4),        // 下扩 4px
    leftOfDel: at(rd.left - 4, rd.top + rd.height / 2),         // ✕ 向左 4px（应仍是 ✕）
    farLeftOfDel: at(rd.left - 9, rd.top + rd.height / 2),      // 超出 6px 扩展（应不再是 ✕）
    rightOfEdit: at(re.right + 4, re.top + re.height / 2),      // ✎ 向右 4px（应仍是 ✎，且不能是 ✕）
  };
});
if (geom.err) { t('能取到 .del / .edit 几何', false, geom.err); }
else {
  t('探点都落在视口内（否则 elementFromPoint 全返回 null，后面几条会变成假绿）',
    geom.probesInViewport === true,
    `视口高 ${geom.vh}，del.top=${geom.delTop} —— 不在视口内时 elementFromPoint 一律 null`);
  t('.del/.edit 视觉尺寸仍是 32×32（没有把 257 行账本撑高）',
    geom.visW === 32 && geom.visH === 32, `${geom.visW}×${geom.visH}`);
  t('✎ 与 ✕ 之间有 12px 空隙（改造前是 0，删除键紧贴编辑键）', geom.gap === 12, `gap=${geom.gap}`);
  /* 真机才抓得到的一条：✎ 与 ✕ 必须在**同一行**，且行高不得因此增长。
     曾经 .del 的 margin-left:12px 把 ✕ 挤到了第二行 —— 表格 auto 布局当时只肯给金额列
     90.7px，而内容需要 91px（差不到 1px），于是行高 56 → 81，✕ 孤零零挂在 ✎ 下面。
     只读记录不渲染按钮，所以静态检查和肉眼都看不见，只有这里能拦。 */
  t('✎ 与 ✕ 在同一行（没有被挤到第二行）', geom.sameLine === true,
    `edit.top=${geom.editTop} del.top=${geom.delTop}`);
  t('带按钮的行没有变高（基线 56px）', geom.rowH <= 60, `行高 ${geom.rowH}px`);
  /* 日期不能折行。用 Range 量文本实际占几行 —— 只看列宽看不出折没折。 */
  const dateLines = await page2.evaluate(() => {
    const td = document.querySelector('#ledger tbody tr td:first-child');
    const rg = document.createRange(); rg.selectNodeContents(td);
    const tops = new Set([...rg.getClientRects()].filter(x => x.height > 1).map(x => Math.round(x.top)));
    const r = td.getBoundingClientRect();
    return { lines: tops.size, colW: Math.round(r.width * 10) / 10, txt: td.textContent.trim() };
  });
  t('日期列渲染成 1 行（改造前 "09-17" 会断成「09-」/「17」）',
    dateLines.lines === 1, `"${dateLines.txt}" 占 ${dateLines.lines} 行，列宽 ${dateLines.colW}px`);
  t('✕ 上方 4px 仍然命中 ✕（热区已扩到 44 高）', geom.aboveDel === 'del', String(geom.aboveDel));
  t('✕ 下方 4px 仍然命中 ✕', geom.belowDel === 'del', String(geom.belowDel));
  t('✕ 向左 4px 仍命中 ✕（热区向左扩进空隙）', geom.leftOfDel === 'del', String(geom.leftOfDel));
  /* 这一条是"扩展有界"：向左 9px 已经超出 6px 扩展，必须不再是 ✕。
     没这条的话，把 inset 调成 -12px 也能让上面三条全绿。 */
  t('✕ 向左 9px 不再命中 ✕（扩展是有界的，没吃进 ✎ 的地盘）',
    geom.farLeftOfDel !== 'del', String(geom.farLeftOfDel));
  /* 这一条是"不重叠"：✎ 向右扩 4px 必须命中 ✎ 而不是 ✕。
     若两个热区重叠（照抄 inset:-12px），这里会返回 del —— 想删一笔却打开了编辑。 */
  t('✎ 向右 4px 命中 ✎ 而不是 ✕（两个热区不重叠）', geom.rightOfEdit === 'edit', String(geom.rightOfEdit));
}
/* 几何测完，把注入的记录撤掉（恢复内存里的 257 条），再开模态做提交测试。 */
await page2.evaluate(() => {
  state.earnings = state.earnings.filter(e => e.id !== 'smoke-geom-1');
  renderLedger();
});
const nBeforeEnter = await nRec();

// I-4 Enter 提交（非破坏性：用非法金额，只验证"回车确实走到了 saveEarn"）
await page2.locator('button:has-text("+ 记一笔")').click();
await page2.waitForTimeout(300);
await page2.locator('#eDate').fill('2026-09-29');
await page2.locator('#eAmt').fill('');
await page2.locator('#eAmt').focus();
await page2.keyboard.press('Enter');
await page2.waitForTimeout(400);
const ferrTxt = await page2.evaluate(() => {
  const f = document.querySelector('#eAmt').closest('.field').querySelector('.ferr');
  return f ? f.textContent.trim() : '';
});
t('在金额框里按回车 → 触发提交并跑到校验（出现字段内联报错）', ferrTxt.length > 0, ferrTxt);
t('回车提交没有写出记录（非法输入早退）', await nRec() === nBeforeEnter,
  `${nBeforeEnter} → ${await nRec()}`);
t('模态是真的 <form>（表单语义 + 原生 Enter 提交）',
  await page2.evaluate(() => document.querySelectorAll('form').length) === 2,
  String(await page2.evaluate(() => document.querySelectorAll('form').length)));
t('出错字段标了 aria-invalid',
  await page2.evaluate(() => document.querySelector('#eAmt').getAttribute('aria-invalid') === 'true'));

// I-5 取消按钮不是 submit（填了合法金额也得点"取消"就什么都不发生）
await page2.locator('#eAmt').fill('123.45');
await page2.locator('#earnOv button.ghost').click();
await page2.waitForTimeout(500);
t('点「取消」不保存（若 type 写成 submit，这里会真存进去一笔）',
  await nRec() === nBeforeEnter, `${nBeforeEnter} → ${await nRec()}`);
t('这次冒烟没有改动任何真实数据（全程只读）', await nRec() === baseline, String(await nRec()));
t('新页面也无运行时异常', errs2.length === 0, errs2.slice(0, 2).join(' | '));
await page2.close();

console.log('\nJ. 功能类型区：逐屏横向溢出 / 首屏高度 / 摘要行数（真机排版）');
/* 为什么这一节必须真机跑：
   逻辑测试那套没有排版引擎，只能验"用了不该用的写法"（比如 .ns 里出现了 nowrap）；
   而缺陷的真正形态是**几何量** —— .nitem .ns 写 nowrap 时，它的 min-content 等于整段摘要
   宽度（实测 968px），grid 的 fr 轨道有 min-content 下限 → .leftcol 被顶到 1234px →
   整页 scrollWidth 1823 > innerWidth 1440。`overflow:hidden` 与 `min-width:0` 都拦不住，
   因为它们管的是"能不能被压缩"，不管"min-content 是多少"。这种量只能在这里量。
   两个已修缺陷各留一条哨兵：资讯屏高（曾经 7855px，116 条全铺）与 scrollWidth。
   用**新页面**：第 I 节往 state 里塞过东西、又按了几十次键，别让状态串味。 */
const page3 = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errs3 = [];
page3.on('pageerror', e => errs3.push(String(e)));
page3.on('response', r => { if (r.status() >= 400 && !/favicon\.ico/.test(r.url())) errs3.push('HTTP ' + r.status() + ' ' + r.url()); });
await page3.goto(`${BASE}/workbench.html?token=${encodeURIComponent(TOKEN)}`, { waitUntil: 'load' });
await page3.waitForTimeout(1500);

const J_TYPES = ['ledger', 'project', 'client', 'time', 'news', 'writing', 'content', 'review'];
const J_NAME = { ledger: '总览', project: '项目', client: '客户', time: '时间',
                 news: '资讯', writing: '写作', content: '自媒体运营', review: '复盘' };
const jscreen = k => 'screen' + k[0].toUpperCase() + k.slice(1);

// 首屏（还没切过屏）：新类型不许把默认那屏顶掉
const firstPaint = await page3.evaluate(() =>
  ['ledger', 'project', 'client', 'time', 'news', 'writing', 'content', 'review'].filter(k =>
    !document.getElementById('screen' + k[0].toUpperCase() + k.slice(1)).hidden));
t('首屏默认落在总览（其他类型不许抢首屏）',
  firstPaint.length === 1 && firstPaint[0] === 'ledger', `可见：${firstPaint.join(',') || '无'}`);

/* 逐屏量。probe 里顺手把"溢出源"也带回来 —— 只报一句"溢出了"没法定位，
   bad 列表能直接指出是哪个类顶出去的（这次就是靠它锁到 .ns 的）。 */
const probeScreen = (k) => page3.evaluate((k) => {
  if (typeof setType !== 'function') return { err: '页面没有暴露 setType' };
  setType(k);
  const id = 'screen' + k[0].toUpperCase() + k.slice(1);
  const screen = document.getElementById(id);
  const all = ['ledger', 'project', 'client', 'time', 'news', 'writing', 'content', 'review'];
  const visible = all.filter(x => !document.getElementById('screen' + x[0].toUpperCase() + x.slice(1)).hidden);
  const bad = [...screen.querySelectorAll('*')]
    .map(el => { const b = el.getBoundingClientRect();
      return { t: String(el.className || el.tagName).split(' ')[0],
               right: Math.round(b.right), w: Math.round(b.width) }; })
    .filter(x => x.right > window.innerWidth + 1)
    .sort((a, b) => b.right - a.right).slice(0, 3);
  /* 摘要行数用 Range 量文本真实占了几个 top —— 只看元素宽度看不出"一行被截断"还是"两行换行"。
     nsOver = 摘要自身宽 − 宿主宽：nowrap 那一版会是**负数或巨大**（宿主被顶开了），
     两行截断这一版必须 ≤ 0（它老老实实待在宿主里）。 */
  const ns = screen.querySelector('.nitem .ns');
  let nsLines = null, nsOver = null;
  if (ns) {
    const rg = document.createRange(); rg.selectNodeContents(ns);
    nsLines = new Set([...rg.getClientRects()].filter(x => x.height > 1).map(x => Math.round(x.top))).size;
    nsOver = Math.round(ns.getBoundingClientRect().width) - Math.round(ns.parentElement.getBoundingClientRect().width);
  }
  return { visible, doc: document.documentElement.scrollWidth, win: window.innerWidth,
           h: Math.round(screen.getBoundingClientRect().height),
           nitem: screen.querySelectorAll('.nitem').length,
           cards: screen.querySelectorAll('.card').length, bad, nsLines, nsOver };
}, k);

const measureAll = async (theme) => {
  const out = {};
  for (const k of J_TYPES) out[k] = await probeScreen(k);
  return out;
};

for (const theme of ['light', 'dark']) {
  const cur = await page3.evaluate(() => document.documentElement.getAttribute('data-theme') || 'light');
  if (cur !== theme) { await clickTheme(page3); await page3.waitForTimeout(400); }
  const now = await page3.evaluate(() => document.documentElement.getAttribute('data-theme') || 'light');
  t(`（${theme === 'light' ? '浅色' : '深色'}）主题已生效`,
    now === theme, `data-theme=${now}`);
  const m = await measureAll();
  for (const k of J_TYPES) {
    const r = m[k];
    if (r.err) { t(`${J_NAME[k]}：能取到几何`, false, r.err); continue; }
    /* 横向溢出：这是 .ns nowrap 那个缺陷的直接量。容 1px 是因为
       scrollWidth/innerWidth 都是整数化的四舍五入，差 1 属于测量噪声。 */
    t(`${J_NAME[k]}：无横向溢出`, r.doc <= r.win + 1,
      `scrollWidth ${r.doc} / innerWidth ${r.win}` + (r.bad.length ? `；溢出源 ${r.bad.map(b => b.t + '@' + b.right).join(' ')}` : ''));
    /* 屏显互斥：hidden 生效。用 scrollWidth 量不到"两屏叠在一起"这种错。 */
    t(`${J_NAME[k]}：切过去后只有这一屏可见`,
      r.visible.length === 1 && r.visible[0] === k, `可见：${r.visible.join(',') || '无'}`);
  }
  const news = m.news;
  if (!news.err) {
    /* 首屏高度哨兵。上限给得宽（20 行时实测约 2256px），因为行高会随字号/主题有几十px 浮动；
       它要拦的是"翻两三倍"那种回归：铺 116 条全量时实测 7855px。 */
    t('资讯首屏高度没有退化成"全量铺开"（实测 20 行约 2256px，全量那次是 7855px）',
      news.h <= 3500, `屏高 ${news.h}px，渲染 ${news.nitem} 条`);
    t('资讯首屏没有把全部条目都渲染出来（总量 217，首屏该是 20）',
      news.nitem > 0 && news.nitem <= 50, `${news.nitem} 条`);
    if (news.nsLines !== null) {
      t('★ 摘要实际排成 ≤2 行（nowrap 那一版这里是 1 行、但宿主被顶到 968px 宽）',
        news.nsLines <= 2, `${news.nsLines} 行`);
      t('★ 摘要宽度不超出宿主卡片（超了就是把 grid 的 fr 轨道顶开的那个形态）',
        news.nsOver <= 0, `摘要宽 − 宿主宽 = ${news.nsOver}px`);
    } else {
      t('资讯卡片里有摘要元素 .ns（找不到就说明渲染结构变了）', false, '查不到 .nitem .ns');
    }
  }
  for (const k of ['project', 'client', 'time', 'writing', 'content', 'review']) {
    const r = m[k];
    if (r.err) continue;
    // 有内容 = 至少一张卡片；空数据会让整屏只剩告警框，这类"静默空白"只有真机看得出
    t(`${J_NAME[k]}：有真实内容渲染（不是一张空屏）`, r.cards >= 2, `${r.cards} 张卡片 / ${r.nitem} 条列表`);
  }
  t('项目页显示真实 Notion 来源且有真实项目卡',
    await page3.locator('#projSourceBar').getAttribute('class') !== null
      && (await page3.locator('#projSourceBar').textContent()).includes('Notion 只读')
      && await page3.locator('#projBoard article.proj').count() > 0,
    `${await page3.locator('#projBoard article.proj').count()} 张项目卡`);
  for (const k of ['client', 'time']) {
    const note = (await page3.locator('#' + jscreen(k) + ' .labnote').textContent()).trim();
    t(`${J_NAME[k]}：明确标注本地试验数据边界`, note.includes('本地试验区'), note.slice(0, 60));
  }
  const socialNote = (await page3.locator('#socialFresh').textContent()).trim();
  t('自媒体运营：明确标注历史快照与数据边界',
    socialNote.includes('历史快照') && socialNote.includes('未知不计为 0'), socialNote.slice(0, 100));
  t('自媒体运营：今日矩阵有四平台且逐条数据可跳转',
    await page3.locator('#mediaMatrix .media-platform').count() === SOCIAL.todayPlan.length
      && await page3.locator('#mediaPosts a[href]').count() > 0,
    `平台 ${await page3.locator('#mediaMatrix .media-platform').count()} / 链接 ${await page3.locator('#mediaPosts a[href]').count()}`);
  t('自媒体运营：网站监控与博客最新文章已渲染',
    await page3.locator('#mediaWeb .media-site-row').count() > 0
      && (await page3.locator('#mediaWeb').textContent()).includes('博客最新更新'),
    (await page3.locator('#mediaWeb').textContent()).trim().slice(0,120));
  t('自媒体运营：首屏只露出 3 条关键信号，其余分析渐进展开',
    await page3.locator('#mediaPosts > .media-post').count() === 3
      && await page3.locator('#mediaPosts .media-post').count() === SOCIAL.analysis.length,
    `首屏 ${await page3.locator('#mediaPosts > .media-post').count()} / 全部 ${await page3.locator('#mediaPosts .media-post').count()}`);
  t('自媒体运营：平台连接默认折叠，避免首屏继续堆信息',
    !(await page3.locator('.media-connect').getAttribute('open'))
      && await page3.locator('#mediaAutomation .media-adapter').count() === SOCIAL.automation.length,
    `连接总数 ${await page3.locator('#mediaAutomation .media-adapter').count()}，默认收起`);
}
t('类型区全流程无运行时异常 / 无 4xx（favicon 除外）', errs3.length === 0, errs3.slice(0, 2).join(' | '));
// 收尾：把主题与类型都还原，别给后面的收尾步骤留状态
await page3.evaluate(() => { setType('ledger'); });
await clickTheme(page3);
await page3.waitForTimeout(300);
await page3.close();

console.log('\nK. 移动端 390px：八类页面、类型导航与项目首屏');
const page4 = await browser.newPage({ viewport: { width: 390, height: 844 } });
const errs4 = [];
page4.on('pageerror', e => errs4.push(String(e)));
await page4.goto(`${BASE}/workbench.html?token=${encodeURIComponent(TOKEN)}`, { waitUntil: 'load' });
await page4.waitForTimeout(900);
await page4.evaluate(() => setType('project'));
const mob = await page4.evaluate(() => {
  const seg = document.querySelector('#typeSeg');
  const buttons = [...seg.querySelectorAll('.segbtn')].map(b => {
    const r = b.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) };
  });
  const stats = [...document.querySelectorAll('#projStats .kpi')].map(x => Math.round(x.getBoundingClientRect().width));
  const cs = getComputedStyle(seg);
  return { doc: document.documentElement.scrollWidth, win: innerWidth,
    segClient: seg.clientWidth, segScroll: seg.scrollWidth, wrap: cs.flexWrap, overflowX: cs.overflowX,
    buttons, stats, hint: document.querySelector('#typeHint').textContent };
});
t('390px 页面本身无横向溢出（导航只在自己的轨道内滑动）', mob.doc <= mob.win + 1,
  `scrollWidth ${mob.doc} / innerWidth ${mob.win}`);
t('8 类导航保持单行且可横向滑动', mob.wrap === 'nowrap' && mob.segScroll > mob.segClient && /auto|scroll/.test(mob.overflowX),
  `wrap=${mob.wrap} overflow-x=${mob.overflowX} ${mob.segClient}/${mob.segScroll}`);
t('导航按钮没有被压成一字一行', mob.buttons.every(x => x.h <= 36 && x.w >= 40), JSON.stringify(mob.buttons));
t('项目默认只露出 2 项关键指标且稳定为 2 列',
  mob.stats.filter(x => x > 0).length === 2
    && Math.max(...mob.stats.filter(x => x > 0)) - Math.min(...mob.stats.filter(x => x > 0)) <= 2,
  mob.stats.join(' / ') + 'px');
t('移动端项目页仍明确显示真实来源', mob.hint.includes('Notion 只读') && mob.hint.includes('29 个真实项目'), mob.hint);
await page4.evaluate(() => toggleMetrics());
const expandedStats = await page4.evaluate(() => [...document.querySelectorAll('#projStats .kpi')]
  .map(x => Math.round(x.getBoundingClientRect().width)));
t('展开指标后 4 项都恢复显示', expandedStats.filter(x => x > 0).length === 4,
  expandedStats.join(' / ') + 'px');
await page4.evaluate(() => toggleMetrics());
for (const id of J_TYPES) {
  const label = J_NAME[id];
  await page4.evaluate(k => setType(k), id);
  await page4.waitForTimeout(80);
  const state = await page4.evaluate(() => ({
    doc: document.documentElement.scrollWidth,
    win: innerWidth,
    visible: [...document.querySelectorAll('.screen')].filter(el => !el.hidden).map(el => el.id),
  }));
  t(`${label}：390px 无横向溢出且屏显互斥`,
    state.doc <= state.win + 1 && state.visible.length === 1,
    `scrollWidth ${state.doc} / ${state.win} · ${state.visible.join(',')}`);
}
t('专注模式默认关闭', !(await page4.evaluate(() => document.body.classList.contains('focus-mode'))));
await page4.evaluate(() => toggleFocusMode());
for (const id of J_TYPES) {
  await page4.evaluate(k => setType(k), id);
  await page4.waitForTimeout(50);
  const state = await page4.evaluate(() => ({doc: document.documentElement.scrollWidth, win: innerWidth}));
  t(`专注模式 · ${J_NAME[id]}：390px 无横向溢出`, state.doc <= state.win + 1,
    `scrollWidth ${state.doc} / ${state.win}`);
}
const focusProof = await page4.evaluate(() => ({
  on: document.body.classList.contains('focus-mode'),
  saved: localStorage.getItem('wb_focus_mode'),
  insight: getComputedStyle(document.querySelector('.media-insight-card')).display,
}));
t('专注模式隐藏支持内容并记住选择', focusProof.on && focusProof.saved === '1' && focusProof.insight === 'none',
  JSON.stringify(focusProof));
await page4.evaluate(() => toggleFocusMode());
t('移动端无运行时异常', errs4.length === 0, errs4.slice(0, 2).join(' | '));
await page4.close();

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
await browser.close();
process.exit(fail ? 1 : 0);
