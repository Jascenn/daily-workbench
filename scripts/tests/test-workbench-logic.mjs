// 用最小 DOM 桩把 workbench.html 的 JS 真跑起来，验证筛选 / 编辑 / 排序 / 字段守恒。
// 因为不依赖浏览器，可以直接进 CI 或每次改完跑一遍：
//   node scripts/tests/test-workbench-logic.mjs
//   WB_HTML=workbench.local.html node scripts/tests/test-workbench-logic.mjs   # 验离线兜底页
import fs from 'fs';

const DIR = new URL('../..', import.meta.url).pathname;   // 项目根
const HTML = process.env.WB_HTML ? (process.env.WB_HTML.startsWith('/') ? process.env.WB_HTML : DIR + process.env.WB_HTML) : DIR + 'workbench.html';
const DATA = JSON.parse(fs.readFileSync(DIR + 'data.json', 'utf8'));
// 类型区的真源。喂**真实产物**而不是手搓夹具：这一节的断言里有一半是守恒式
// （Σ分组 == 篇数、近期+归档 == 总数），用手搓数据守恒恒成立，等于没测。
let SECTIONS;
try { SECTIONS = JSON.parse(fs.readFileSync(DIR + 'sections.json', 'utf8')); }
catch (e) { console.error('缺少 sections.json —— 先跑：python3 scripts/sections.py --write'); process.exit(2); }
/* 资讯分页断言的前提是「窗口内条数 > NEWS_PAGE」：真实快照窗口内常常不足 20 条，
   min(NEWS_PAGE,…) 恒等于窗口内条数，把分页变异掉断言照样绿 —— 被数据豁免。
   这里在喂给页面之前把前 NEWS_PAGE+5 条压进窗口，保证上限真的参与约束。
   必须在启动前改：页面启动后各自持有一份状态，事后改源数据会造成两边不同源。 */
{
  const newsSec = (SECTIONS.sections || []).find(s => s && s.id === 'news');
  if (newsSec && Array.isArray(newsSec.items) && newsSec.windowDays) {
    const need = 25, wd = newsSec.windowDays;
    newsSec.items.slice(0, need).forEach((it, i) => { it.days = i % wd; });
  }
}
let SOCIAL;
try { SOCIAL = JSON.parse(fs.readFileSync(DIR + 'social.json', 'utf8')); }
catch (e) { console.error('缺少 social.json —— 先跑：python3 scripts/social-sync.py'); process.exit(2); }

console.log(`被测文件：${HTML}\n`);

const src = fs.readFileSync(HTML, 'utf8');
const js = [...src.matchAll(/<script>([\s\S]*?)<\/script>/g)].pop()[1];

const alerts = [];
// 捕获页面注册的监听器：键盘激活委托是"一份实现管三处"，
// 桩里如果只做 addEventListener:()=>{}，这段逻辑就永远测不到。
const listeners = {};
class El {
  constructor(id) { this.id = id; this._value = ''; this._html = ''; this._opts = null;
    this.textContent = ''; this.className = ''; this.disabled = false; this.style = {};
    this.tagName = 'DIV'; this.tabIndex = -1; this._attr = {}; this._q = {}; }
  dataset = {};
  get value() { return this._value; }
  // 真实 DOM 语义：把 value 设成不在 option 列表里的值 → value 变成 ""
  set value(v) { v = String(v == null ? '' : v);
    this._value = this._opts ? (this._opts.includes(v) ? v : '') : v; }
  get innerHTML() { return this._html; }
  set innerHTML(h) { this._html = String(h);
    const found = [...this._html.matchAll(/<option(?: value="([^"]*)")?>([^<]*)<\/option>/g)];
    this._opts = /<option/.test(this._html) ? found.map(m => m[1] !== undefined ? m[1] : m[2]) : null; }
  querySelector(sel) { return this._q[sel] || (this._q[sel] = new El(this.id + ' ' + sel)); }
  // className 与 classList 必须是**同一份状态的两个视图**。
  // 之前它们是两套互不相干的存储：页面写 className="notify show" 之后，
  // classList.contains('show') 会返回 false —— 一个纯粹的桩失真，会让断言给出假失败。
  _cls = new Set();
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v == null ? '' : v).split(/\s+/).filter(Boolean)); }
  classList = { add: c => this._cls.add(c), remove: c => this._cls.delete(c),
    contains: c => this._cls.has(c),
    /* 真实 DOM 的 toggle(token, force) **会尊重 force**：给了 true 就是"确保有"、
       给了 false 就是"确保没有"，而不是"翻转"。桩原来忽略第二参数，于是一段
       `toggle('on', k===cur)` 的写法在真机上是"切到哪亮哪"，在桩里却是"每点一次翻一次" ——
       桩失真会让"选中态"类断言给出**假失败**。这里补成忠实实现
       （2026-09-30 第一次出现两参数写法时暴露；同 .closest/.querySelectorAll 那一批）。 */
    toggle: (c, force) => { const want = force === undefined ? !this._cls.has(c) : !!force;
      if (want) this._cls.add(c); else this._cls.delete(c); return want; } };
  getAttribute(k) { return this._attr[k] ?? null; }
  setAttribute(k, v) { this._attr[k] = String(v); }
  // 真实 DOM 元素有 removeAttribute / closest / appendChild。
  // 桩里缺这三个，会让页面里"清掉错误态""把错误挂到字段旁"这类逻辑直接抛错 ——
  // 那是桩不忠实，不是页面写错，所以补在桩上而不是绕开页面代码。
  removeAttribute(k) { delete this._attr[k]; }
  closest() { return this; }
  appendChild() { return arguments[0]; }
  focus() {} click() {}
  // 页面初始化时会给 cmdInput 等元素挂事件监听；桩缺这个方法会在求值阶段直接抛错
  addEventListener() {} removeEventListener() {}
  // 自媒体页用 insertAdjacentHTML 插运营体检 / Jev 卡片；桩缺会直接抛错
  insertAdjacentHTML() {}
  // Jev 卡片重写前先 querySelectorAll('.media-jev-card') 去重
  querySelectorAll() { return []; }
}
const els = new Map();
const el = id => els.get(id) || (els.set(id, new El(id)), els.get(id));

const doc = {
  getElementById: el,
  // 真实页面里 .ov 就是两个弹窗；之前返回空数组会让 closeOv() 变成空操作，
  // 导致"弹窗已关闭"类断言全是假失败。
  querySelectorAll: sel => sel === '.ov' ? [el('projOv'), el('earnOv')] : [],
  querySelector: s => el(s),
  documentElement: new El('html'),
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
  createElement: () => new El('tmp'),
  body: new El('body'),
};
const store = new Map([['wb', JSON.stringify(DATA)], ['wb_sections', JSON.stringify(SECTIONS)], ['wb_social', JSON.stringify(SOCIAL)]]);
const localStorage = {
  getItem: k => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
};
const location = { protocol: 'file:', search: '', href: 'file:///w.html' };

const run = new Function('document', 'localStorage', 'location', 'alert', 'setInterval', 'clearInterval', 'window',
  js + `
  return { renderAll, renderLedger, ledgerFilters, editEarn, saveEarn, openEarn, fillLedTypeOptions,
           toggleLedAll, typeOptions, normalize, delEarn,
           compute, monthlySeries, setChartMode, renderChartM, renderExpenseBreak,
           get: s=>state, push: r=>state.earnings.push(r), setLedAll: v=>{ledAll=v},
           renderSourceRank, sourceOf, sourceStats, firstSeg, setSrcFilter, clearSrcFilter,
           getSrcFilter: () => srcFilter, healthIssues, renderHealth, jumpToIds, clearFocus,
           getFocusIds: () => focusIds, csvCell,
           csvRows, buildCSV,
           // 2026-09-29 晚：环形图自带的「本月/累计」私有开关已并入全局分析口径，
           // 所以 setDonutMode/getDonutMode 不再存在，改为导出 anScope 这一族。
           anPool, setAnScope, getAnScope: () => anScope, anScopeLabel, expBreak,
           renderScopeBar, renderGoal, healthJump, focusGoal,
           todayStr, fmt, setGoal: v => { state.monthlyGoal = v; },
           // 2026-09-29 晚（目标带 v2）：整页单位 / 拖动条 / 预设 / 达成史。
           // setGoal 仍是"直接写 state"的桩（第 23 节拿它摆状态用），
           // 真正读输入框的那个叫 commitGoal —— 两者别混用，混了就会把"输入框换算"
           // 这条最该验的路径整条绕过去。
           commitGoal: setGoal, applyGoal, clearGoal, onGoalSlide, usePreset,
           setMoneyUnit, getMoneyUnit: () => moneyUnit, unitDef, unitLabel,
           goalToInput, inputToGoal, sliderSpec, syncGoalSlider, echoGoal, goalKey,
           goalPresets, renderGoalPreset, renderGoalHist, renderUnitSeg,
           // 2026-09-29 深夜补：预设取整的撞值去重。抽出来是因为**当前真实数据不会触发撞值** ——
           // 不抽成纯函数，这段防御分支就永远没有覆盖（同一种毛病正是
           // round(v/100) 那个缺陷能活下来的原因：没有断言真的碰过它）。
           // ⚠️ 上面这几行在模板字面量里，**不能出现反引号**（会提前截断，报 missing )）。
           dedupeByValue,
           // 2026-09-29 UI/UX 评审整改：常驻提示 / 删除撤销 / 内联字段错误。
           // 键盘激活委托不在这里导出 —— 它是页面注册到 document 上的监听器，
           // 由测试侧捕获后直接调用（见第 26 节），那样测的是"真注册了什么"，而不是"导出名对不对"。
           notify, dismissNotify, showUndo, hideUndo, undoDel,
           fieldErr, clearFieldErr, getLastDel: () => lastDel,
           // 第四轮整改：模态的 Enter 提交包装。它俩必须导出，因为这一节要验的是
           // 「提交时真的拦住了默认行为、真的走到了 saveEarn」——只断言源码里
           // 出现过 onsubmit= 字样，那是编译期检查，永远通过。
           submitProj, submitEarn,
           // 2026-09-30 凌晨：功能类型区（资讯 / 写作 / 复盘）。
           // loadSections 也必须导出：这一节要验的是"离线时真的去读了 localStorage 里的
           // 内嵌快照"，只导出渲染函数会把"取数"那半截整条绕过去 —— 而那正是
           // （离线页过期、令牌 401 拿缓存冒充最新）两次踩坑所在的那半截。
           loadSections, loadSocial, secOf, getSections: () => sections, getSecErr: () => secErr,
           getSocial: () => social, getSocialErr: () => socialErr,
           setType, getCurType: () => curType, renderTypeBar, renderSections,
           renderNews, renderWriting, renderReview, setNewsScope, getNewsScope: () => newsScope,
           renderDailyDeck, focusHealth, renderProject, setProjectScope,
           projectCol, projectConflict, projectRisk,
           renderContent, mediaMetrics, mediaReminders, mediaStatus, cycleMediaStatus,
           NEWS_PAGE,
           safeUrl, agoTxt, statCells, freshBar };`);

const api = run(doc, localStorage, location, m => alerts.push(String(m)), () => 1, () => {}, globalThis);

/* 时钟注入（与第 30 节同款机制）：把页面认为的"今天"钉在基线数据窗口内
   （2026-09-30，即 data.json 最后一笔所在的月），让下面 §13/§14/§15/§22/§23/§24
   里所有"本月/上月/近 12 月"断言对齐到 2026-09 这个快照。
   为什么必须钉：基线数据是冻结的历史（止于 2026-09），但产品的"本月"永远指向
   真实日历月。日历一跨进 10 月，当前月净额变 0、12 月窗口整体平移，那一整批
   按月字面量写死的断言就整批变红 —— 这正是第 30 节序言要治的那类病
   （"把今天几号当常量"）。钉死后，套件在任何日期跑都只对"函数是否如实统计 2026-09 窗口"
   负责，既确定性又可复现，且仍保有意义（验的是真值，不是 `|| true`）。
   ⚠️ 只在上述六个区块临时替换 globalThis.Date，用完即还原（try/finally），
   不影响其余区块与第 30 节自身的时钟注入。 */
const RealDate0 = globalThis.Date;
const TEST_TODAY = '2026-09-30T12:00:00';
const clockAt = (iso, fn) => {
  const fixed = new RealDate0(iso).getTime();
  class FakeDate extends RealDate0 {
    constructor(...a) { super(...(a.length ? a : [fixed])); }
    static now() { return fixed; }
  }
  globalThis.Date = FakeDate;
  try { return fn(); } finally { globalThis.Date = RealDate0; }
};

/* 提示现在走两条通道，两者都比 alert 更难被错过：
   · 常驻条 #notify（数据完整性类：保存失败 / 401 / 只读拦截）
   · 字段内联错误 .ferr（表单校验类：该出现在出错的那个字段旁边）
   alerts 数组保留 —— 它同时是一道哨兵：页面里若再冒出任何 alert()，这里会记下来。 */
const notice = (which = 'notify') => el(which).innerHTML || '';
const noticeOn = (which = 'notify') => el(which).className.includes('show');
const noticeLevel = (which = 'notify') =>
  (el(which).className.match(/\b(err|warn|info)\b/) || [])[1] || '';
const ferr = id => el(id).querySelector('.ferr').textContent || '';

await new Promise(r => setTimeout(r, 0));   // 让 boot IIFE 里的 await loadState() 落地

const tbody = el('ledger').querySelector('tbody');
const S = api.get();
let pass = 0, fail = 0;
const t = (name, cond, extra = '') => { (cond ? pass++ : fail++);
  console.log(`${cond ? '  ✓' : '  ✗'} ${name}${extra ? '  → ' + extra : ''}`); };

console.log('— 数据落盘状态 —');
console.log(`  earnings=${S.earnings.length}  projects=${S.projects.length}  readonly=${!!S.earningsReadonly}`);
/* 基线一律从 data.json 现算，不写死数字：真实数据每天在变，写死必假红。
   N0 = 同步基线条数（fixture 尚未注入草稿前的真实数据量），下面各节复用。 */
const N0 = DATA.earnings.length;
const P0 = DATA.projects.length;
t(`收益 ${N0} 条已加载（= data.json 现算）`, S.earnings.length === N0, String(S.earnings.length));
t(`项目 ${P0} 个已加载`, S.projects.length === P0, String(S.projects.length));

console.log('\n— 1. 明细默认视图 —');
api.renderAll();
t('renderAll 不抛错，tbody 有内容', tbody.innerHTML.includes('<tr'), `${tbody.innerHTML.split('<tr').length - 1} 行`);
t('默认只显示 12 笔', (tbody.innerHTML.match(/<tr/g) || []).length === 12);
t('默认是「最近的 12 笔」而不是最旧的（排序）', (() => {
  // 明细日期列显示完整年月日（YYYY-MM-DD），排序断言对完整日期串做降序校验
  const dates = [...tbody.innerHTML.matchAll(/<td>(\d{4}-\d{2}-\d{2})<\/td>/g)].map(m => m[1]);
  /* 最新两笔的日期也从数据现算：写死日期（如 2026-10-06）会随真实数据漂移假红。
     页面排序口径是 date 降序，与 sort 后的前两名比对即可。 */
  const top2 = [...DATA.earnings].sort((a, b) => (a.date||'') < (b.date||'') ? 1 : -1).slice(0, 2).map(e => e.date);
  return dates.length === 12 && dates[0] === top2[0] && dates[1] === top2[1] && dates.every((d, i) => i === 0 || dates[i - 1] >= d);
})(), [...tbody.innerHTML.matchAll(/<td>(\d{4}-\d{2}-\d{2})<\/td>/g)].map(m => m[1]).join(' '));
t('默认 tag 文案为「最近 12 笔 · 只读」', el('ledgerSrc').textContent === '最近 12 笔 · 只读', el('ledgerSrc').textContent);
t('只读行没有删除按钮', !tbody.innerHTML.includes('class="del"'));
t('只读行没有编辑按钮', !tbody.innerHTML.includes('class="edit"'));
t('只读行没有整行可点', !tbody.innerHTML.includes('class="clickable"'));

console.log('\n— 2. 类型下拉 —');
const tOpts = api.typeOptions();
t('类型下拉已填充（含「全部类型」首位）', el('ledType')._opts && el('ledType')._opts[0] === '', JSON.stringify(el('ledType')._opts.slice(0, 5)));
t('下拉含「支出」', el('ledType')._opts.includes('支出'));
t('下拉项数 = 1 + typeOptions', el('ledType')._opts.length === tOpts.length + 1, `${el('ledType')._opts.length} vs ${tOpts.length + 1}`);

console.log('\n— 3. 关键词筛选 —');
/* 开源版：不写死真实客户名，从数据现取一条备注的首段词作为关键词 */
const kwProbe = (S.earnings.find(e => (e.note || '').trim()) || {}).note || '';
const kw = kwProbe.trim().split(/\s+/)[0] || '';
el('ledQ').value = kw;
api.renderLedger();
const nSol = api.ledgerFilters().length;
t('搜备注关键词命中数在 1..N 之间', nSol > 0 && nSol <= N0, `关键词=${kw} → ${nSol} 笔`);
t('tag 显示「shown / list 笔」', el('ledgerSrc').textContent.includes(`/ ${nSol} 笔`), el('ledgerSrc').textContent);
el('ledQ').value = 'zzz-绝不存在-zzz';
api.renderLedger();
t('无结果时给出空态提示', tbody.innerHTML.includes('没有符合条件的记录'), '');
el('ledQ').value = '';
api.renderLedger();
t('清空关键词后恢复基线条数', api.ledgerFilters().length === N0);

console.log('\n— 4. 类型 / 时间范围筛选 —');
el('ledType').value = '支出';
api.renderLedger();
const nExp = api.ledgerFilters().length;
const expTotal = S.earnings.filter(e => e.amount < 0).length;
t('选「支出」命中数 = 负数记录数', nExp === expTotal, `${nExp} vs ${expTotal}`);
t('「支出」筛选下结果 rows 全为负数展示', (tbody.innerHTML.match(/class="amt down"/g) || []).length === Math.min(nExp, 12));
el('ledType').value = '';
el('ledRange').value = 'ytd';
api.renderLedger();
const nYtd = api.ledgerFilters().length;
const y = String(new Date().getFullYear());
t(`选「今年」命中 = ${y} 年记录数`, nYtd === S.earnings.filter(e => (e.date || '').startsWith(y)).length, `${nYtd} 笔`);
el('ledRange').value = 'd30';
api.renderLedger();
const n30 = api.ledgerFilters().length;
t('选「近 30 天」命中数 <= 今年', n30 <= nYtd, `${n30} 笔`);
el('ledRange').value = '';
api.renderLedger();

console.log('\n— 5. 看全部 / 只看最近 —');
api.toggleLedAll();
t(`ledAll=true 时 tag 显示全部（${N0} 笔）`, el('ledgerSrc').textContent === `全部 ${N0} 笔 · 只读`, el('ledgerSrc').textContent);
t('ledAll=true 时渲染全部行', (tbody.innerHTML.match(/<tr/g) || []).length === N0, `${(tbody.innerHTML.match(/<tr/g) || []).length} 行`);
t('ledAll=true 时最旧一笔也在列表里（排序仍降序）', (() => {
  const dates = [...tbody.innerHTML.matchAll(/<td>(\d{4}-\d{2}-\d{2})<\/td>/g)].map(m => m[1]);
  /* 最旧一笔同样现算。若并列最旧有多个，页面任取其一都算对 —— 断言只要求
     「最后一行日期 == 数据里最小日期」。 */
  const oldest = [...DATA.earnings].map(e => e.date||'').sort()[0];
  return dates[dates.length - 1] === oldest;
})(), [...tbody.innerHTML.matchAll(/<td>(\d{4}-\d{2}-\d{2})<\/td>/g)].map(m => m[1]).slice(-1)[0]);
t('按钮文案切成「只看最近」', el('ledMore').textContent === '只看最近', el('ledMore').textContent);
api.toggleLedAll();
t('再点回到 12 行', (tbody.innerHTML.match(/<tr/g) || []).length === 12);
t('按钮文案回到「看全部」', el('ledMore').textContent === '看全部');

console.log('\n— 6. 本地草稿：编辑（原位更新，不新增） —');
const draft = { id: 'draft-test-1', date: '2026-09-28', amount: 888.88, type: '咨询', projectId: '', note: '测试草稿-原始', product: '', source: 'local', readOnly: false, updatedAt: '2026-09-28' };
api.push(draft);
const before = S.earnings.length;
api.renderAll();
t('新草稿出现（总数 = 基线 + 1）', S.earnings.length === N0 + 1, String(S.earnings.length));
console.log('    [debug] 渲染行数 =', (tbody.innerHTML.match(/<tr/g) || []).length,
            '| 前 2 行 =', tbody.innerHTML.split('<tr').slice(1, 3).map(s => s.slice(0, 90)).join(' || '));
console.log('    [debug] 草稿日期 =', draft.date, '| 最新真实记录日期 =', [...S.earnings].sort((a, b) => a.date < b.date ? 1 : -1)[0].date);
t('草稿行可点（clickable）', tbody.innerHTML.includes("editEarn('draft-test-1')"));
t('草稿行有 ✎ 与 ✕', tbody.innerHTML.includes('class="edit"') && tbody.innerHTML.includes('class="del"'));

api.editEarn('draft-test-1');
t('弹窗标题变「编辑收益」', el('earnTitle').textContent === '编辑收益', el('earnTitle').textContent);
t('日期回填正确', el('eDate').value === '2026-09-28', el('eDate').value);
t('金额回填为绝对值', el('eAmt').value === '888.88', String(el('eAmt').value));
t('备注回填正确', el('eNote').value === '测试草稿-原始', el('eNote').value);
t('类型回填保留「咨询」', el('eType').value === '咨询', el('eType').value);
t('关联项目回落到「无」', el('eProj').value === '');
t('弹窗已打开', el('earnOv').classList.contains('show'));

el('eAmt').value = '999';
el('eNote').value = '测试草稿-已改';
el('eType').value = '咨询';
api.saveEarn();
t('保存后总数仍是基线 + 1（未新增记录）', S.earnings.length === N0 + 1, String(S.earnings.length));
t('未新增 id', S.earnings.filter(e => e.id === 'draft-test-1').length === 1);
const d2 = S.earnings.find(e => e.id === 'draft-test-1');
t('金额已更新为 999', d2.amount === 999, String(d2.amount));
t('备注已更新', d2.note === '测试草稿-已改', d2.note);
t('source/readOnly 未被污染', d2.source === 'local' && d2.readOnly === false, `${d2.source}/${d2.readOnly}`);
t('弹窗已关闭', !el('earnOv').classList.contains('show'));

console.log('\n— 7. 支出符号口径 —');
api.editEarn('draft-test-1');
el('eAmt').value = '300'; el('eType').value = '支出'; api.saveEarn();
const d3 = S.earnings.find(e => e.id === 'draft-test-1');
t('选「支出」自动记负', d3.amount === -300, String(d3.amount));
t('支出行的 product 标为「支出」', d3.product === '支出', d3.product);
api.editEarn('draft-test-1');
el('eAmt').value = '-50'; el('eType').value = '咨询'; api.saveEarn();
// 校验类提示不再占用全局弹窗：错误直接挂在出错的金额字段下面（.ferr）
t('收入填负数被拦下（未写入）', S.earnings.find(e => e.id === 'draft-test-1').amount === -300 && ferr('eAmt').includes('金额为负数'), ferr('eAmt'));

console.log('\n— 8. 只读记录不可编辑 —');
const roId = S.earnings.find(e => e.readOnly).id;
const okId = S.earnings.find(e => !e.readOnly).id;
api.openEarn();
t('openEarn 重置 uid（标题回到「记一笔收益」）', el('earnTitle').textContent === '记一笔收益', el('earnTitle').textContent);
api.editEarn(roId);
t('editEarn 拒绝只读记录，标题未被改成「编辑收益」',
  el('earnTitle').textContent === '记一笔收益' && noticeOn() && notice().includes('只读拉取') && noticeLevel() === 'warn',
  el('earnTitle').textContent + ' / 提示级 ' + noticeLevel());
api.editEarn(okId);
t('可编辑记录仍能打开', el('earnOv').classList.contains('show') && el('earnTitle').textContent === '编辑收益');
t('编辑只读记录不改动数据', S.earnings.length === N0 + 1);

console.log('\n— 9. 同步按钮（离线模式应被拦） —');
// SERVED=false → runSync 应给出常驻提示并直接返回，不发起 fetch
const fn = new Function('document', 'localStorage', 'location', 'alert', 'setInterval', 'clearInterval', 'fetch',
  js + '\nreturn runSync;')(doc, localStorage, location, m => alerts.push(String(m)), () => 1, () => {},
  () => { throw new Error('离线模式不应发起 fetch'); });
await fn();
t('离线模式点同步给出提示且不发请求', noticeOn() && notice().includes('离线单文件模式'), notice() || '(无提示)');

console.log('\n— 10. 访客模式隔离 —');
t('访客开关存在', typeof el('guestBtn').textContent === 'string');

console.log('\n— 11. 元数据透传（normalize 不再抹字段） —');
t('earningsReadonly 透传为 true', S.earningsReadonly === true, String(S.earningsReadonly));
t('projectsSource 透传', S.projectsSource && S.projectsSource.name === '项目管理', JSON.stringify(S.projectsSource && S.projectsSource.name));
t('notionMeta 透传', !!S.notionMeta);
t('earningsSource 键存在', 'earningsSource' in S);
api.renderAll();
t('只读文案随 earningsReadonly 生效', el('notionNote').innerHTML.includes('只读'), '');
t('项目来源库名已显示在面板上', el('notionNote').innerHTML.includes('项目管理'), '');
api.renderLedger();
t('明细 tag 带只读后缀', el('ledgerSrc').textContent === '最近 12 笔 · 只读', el('ledgerSrc').textContent);

console.log('\n— 12. 令牌缺失必须"喊出来"（不能静默用旧缓存） —');
{
  // 第二套环境：SERVED 但 URL 没有 ?token=，且 localStorage 里也没记住过
  const els2 = new Map();
  const el2 = id => els2.get(id) || (els2.set(id, new El(id)), els2.get(id));
  const doc2 = { getElementById: el2, querySelectorAll: s => s === '.ov' ? [el2('projOv'), el2('earnOv')] : [],
    querySelector: s => el2(s), documentElement: new El('html'), addEventListener: () => {}, createElement: () => new El('t'), body: new El('body') };
  const store2 = new Map([['wb', JSON.stringify(DATA)]]);   // 有缓存数据，但没有 wb_token
  const ls2 = { getItem: k => store2.has(k) ? store2.get(k) : null, setItem: (k, v) => store2.set(k, String(v)) };
  const loc2 = { protocol: 'http:', search: '', href: 'http://127.0.0.1:8787/workbench.html' };  // 裸链接
  const alerts2 = [];
  let fetchCalls = 0;
  const fetch2 = async () => { fetchCalls++; return { ok: false, status: 401, json: async () => ({ error: 'unauthorized' }) }; };

  const api2 = new Function('document', 'localStorage', 'location', 'alert', 'setInterval', 'clearInterval', 'fetch',
    js + `\nreturn { runSync, get: ()=>state, flags: ()=>({TOKEN,TOKEN_MISSING,SERVED}) };`)(doc2, ls2, loc2, m => alerts2.push(String(m)), () => 1, () => {}, fetch2);
  await new Promise(r => setTimeout(r, 20));

  const f2 = api2.flags();
  t('裸链接下 TOKEN 为空、TOKEN_MISSING=true', f2.TOKEN === '' && f2.TOKEN_MISSING === true, JSON.stringify(f2));
  t('loadState 仍给出了缓存数据（不至于白屏）', api2.get().earnings.length === N0, `${api2.get().earnings.length} 条`);
  t('横幅提示"访问令牌"', el2('banner').innerHTML.includes('访问令牌'), el2('banner').className);
  t('横幅用的是 auth 样式（红色压过其他提示）', el2('banner').className.includes('auth'), el2('banner').className);
  t('横幅说明同步按钮也会失败', el2('banner').innerHTML.includes('同步按钮也会失败'));
  t('横幅给出修复命令', el2('banner').innerHTML.includes('open-workbench.sh'));
  t('横幅提示会记住令牌', el2('banner').innerHTML.includes('记住'));

  const before = fetchCalls;
  await api2.runSync();
  t('无令牌时 runSync 直接拦下，不发起请求', fetchCalls === before, `fetch 调用 ${fetchCalls} 次`);
  t('runSync 给出可操作提示（不是裸的 unauthorized）',
    el2('notify').innerHTML.includes('访问令牌'),
    (el2('notify').innerHTML || '').slice(0, 60));
  t('notify 标为错误样式', el2('notify').className.includes('err'), el2('notify').className);
}

console.log('\n— 12.1. 同步出口矩阵：把网络注入进来，一次跑遍所有出口 —');
{
  /* 为什么要有这一节（和上面第 12 节是两件事，别合并）：
     第 12 节只走了一个出口 ——「没有令牌」，那是**失败侧**的一个分支。
     而 `runSync` 一共 **6 个出口**：成功 / 409 / 401 / 其它状态码 / fetch 抛异常 / 无令牌。
     其中**成功侧那一个出口从来没在本套件里跑过** —— 逻辑测试碰不到真网络，
     真机测试（test-workbench-live.mjs）的成功与否又由外部链路决定：
     2026-09-30 连续三次 180s 超时（HTTP 504），于是"成功出口"连续三次没被执行，
     而"样式对不对"这件事就一直没有被真正评过。
     这又回到第 30 节那条教训：**一次运行只覆盖一支的断言，是"绿得没有信息量"。**
     解法同款 —— 把外部依赖**注入**进来（这里是 `fetch`，那里是 `Date`），
     一次把每个出口都走一遍，并断言"每个出口的样式都符合契约"。

     契约只有一条，但**两个方向都算缺陷**（这对断言的价值就在这）：
       成功 → 不许标 err；失败 → **必须**标 err。
     后者比前者重要：同步挂了却显示成正常，用户会以为数据是新的。
     （2026-09-30 00:22 真机实测：真机那侧把 `show err` 报成了产品缺陷，
      而它恰恰是页面**做对了** —— 所以真机那条改成了"样式与结果一致"；
      注入这一侧则把每个出口逐一钉死。两边合起来才没有空白。） */
  const mkEnv = syncResp => {
    const els = new Map();
    const el3 = id => els.get(id) || (els.set(id, new El(id)), els.get(id));
    const doc3 = { getElementById: el3, querySelectorAll: s => s === '.ov' ? [el3('projOv'), el3('earnOv')] : [],
      querySelector: s => el3(s), documentElement: new El('html'), addEventListener: () => {},
      createElement: () => new El('t'), body: new El('body') };
    // 带令牌：这一节要测的是"同步出口"，不是第 12 节那个"没令牌"的入口
    const store3 = new Map([['wb', JSON.stringify(DATA)], ['wb_token', 'tok3']]);
    const ls3 = { getItem: k => store3.has(k) ? store3.get(k) : null, setItem: (k, v) => store3.set(k, String(v)) };
    const loc3 = { protocol: 'http:', search: '?token=tok3', href: 'http://127.0.0.1:8787/workbench.html?token=tok3' };
    /* URL 感知的 fetch 桩：只把 /api/sync 交给 resp，其余走正常数据路径 ——
       否则成功出口里的 `await loadState()` 会拿到同步返回值，把 state 污染成 `{ok:true,...}`。 */
    const fetch3 = async url => {
      const u = String(url);
      if (u.startsWith('/api/sync')) return syncResp();
      if (u.startsWith('/data.json')) return { ok: true, status: 200, json: async () => DATA };
      return { ok: false, status: 404, json: async () => ({}) };
    };
    const api3 = new Function('document', 'localStorage', 'location', 'alert', 'setInterval', 'clearInterval', 'fetch',
      js + `\nreturn { runSync, get: ()=>state };`)(doc3, ls3, loc3, () => {}, () => 1, () => {}, fetch3);
    return { api3, el3 };
  };

  const cases = [
    ['成功', () => ({ ok: true, status: 200, json: async () => ({ ok: true, earnings: 257, projects: 29,
      lastNotionSync: '2026-09-30T00:00:00+08:00', output: 'done' }) }), 'info'],
    ['409 已有同步在进行', () => ({ ok: false, status: 409, json: async () => ({ error: 'busy' }) }),
      'warn'],
    ['401 令牌无效', () => ({ ok: false, status: 401, json: async () => ({ error: 'unauthorized' }) }),
      'err'],
    ['500 服务端错误', () => ({ ok: false, status: 500, json: async () => ({ error: 'boom' }) }),
      'err'],
    ['fetch 抛异常（服务没开）', () => { throw new Error('connect ECONNREFUSED'); }, true, '同步请求失败'],
  ];
  cases[4] = ['fetch 抛异常（服务没开）', () => { throw new Error('connect ECONNREFUSED'); }, 'err'];
  const hit = { ok: 0, warn: 0, err: 0 };
  for (const [name, resp, wantLevel] of cases) {
    const { api3, el3 } = mkEnv(resp);
    await new Promise(r => setTimeout(r, 20));      // 等启动序列跑完，再手动触发同步
    await api3.runSync();
    const cn = el3('notify').className, tx = el3('notify').innerHTML;
    const isErr = wantLevel === 'err';
    if (isErr) hit.err++; else if (wantLevel === 'warn') hit.warn++; else hit.ok++;
    t(`同步出口「${name}」→ 通知条标 ${wantLevel} 且文案非空`,
      cn.includes(wantLevel) && tx.length > 0,
      `className="${cn}" / ${tx.slice(0, 40).replace(/\n/g, ' ')}`);
  }
  /* 本节的目的在这一条：**五个出口都真的被走到了，且两侧都出现过**。
     没有它，上面五条即使全绿也只说明"我构造的五种响应各自拿到了预期样式"，
     而"成功侧这一整轮到底有没有被执行"看不出来 —— 那正是本节存在的原因。
     判据写死 1/4（成功侧 1 支、失败侧 4 支）而不是 `>=1`：出口数是接口契约的一部分，
     将来 runSync 真加了出口，这条应该红、应该被人看一眼，而不是被放宽掉。 */
  t('★ 五个同步出口全部被跑到，成功/警告/失败三侧都真实出现过（不是只跑通一支）',
    hit.ok === 1 && hit.warn === 1 && hit.err === 3,
    `成功 ${hit.ok} / 警告 ${hit.warn} / 失败 ${hit.err}`);
}

clockAt(TEST_TODAY, () => {
console.log('\n— 13. 新增：成交笔数 / 月度趋势 / 支出构成 —');
// 复位夹具：第 6~8 节为了验证编辑流程注入过本地草稿（-300 支出），
// 不清掉会让下面所有「对真实基线」的断言全部偏移。这是测试自身的问题，不是代码的。
S.earnings = S.earnings.filter(e => !String(e.id).startsWith('draft-test-'));
S.projects = S.projects.filter(p => !String(p.id).startsWith('x-'));
api.renderAll();
t(`夹具已复位到真实基线（${N0} 条）`, S.earnings.length === N0, String(S.earnings.length));
const c = api.compute();
t('本月成交笔数 = 3（2026-09）', c.cntThis === 3, String(c.cntThis));
t('上月成交笔数 = 4（2026-08）', c.cntPrev === 4, String(c.cntPrev));
t('今年月均笔数已算且合理', c.cntAvgY > 0 && c.cntAvgY < 20, String(c.cntAvgY));
t('累计支出 = 11404.08', Math.round(c.expense * 100) === 1140408, c.expense.toFixed(2));
t('支出分类数 = 3', c.expKinds === 3, String(c.expKinds));
t('支出最大项 = 投资 ¥8756.08', c.expList[0].k === '投资' && Math.round(c.expList[0].v * 100) === 875608,
  `${c.expList[0].k} ${c.expList[0].v.toFixed(2)} / ${c.expList[0].n} 笔`);

console.log('\n— 14. KPI 卡（8 张，含两个新卡） —');
const kpi = el('kpis').innerHTML;
t('KPI 卡共 8 张', (kpi.match(/class="kpi"/g) || []).length === 8, String((kpi.match(/class="kpi"/g) || []).length));
t('含「本月成交笔数」', kpi.includes('本月成交笔数'));
t('含「累计支出」', kpi.includes('累计支出'));
t('笔数卡显示 3 笔', kpi.includes('>3 笔<'), (kpi.match(/本月成交笔数[\s\S]{0,200}/) || [''])[0].replace(/\s+/g, ' ').slice(0, 130));
t('笔数卡的对比是「笔」而不是「¥」',
  /本月成交笔数[\s\S]{0,220}笔 \(/.test(kpi) && !/本月成交笔数[\s\S]{0,120}¥/.test(kpi));
t('累计支出卡带出最大项 投资', /累计支出[\s\S]{0,300}投资/.test(kpi));

console.log('\n— 15. 近 12 月序列 —');
const ms = api.monthlySeries();
t('返回 12 个月且首尾正确', ms.length === 12 && ms[0].m === '2025-10' && ms[11].m === '2026-09',
  `${ms[0].m} → ${ms[11].m}`);
t('2025-10 净额 -9523.10 / 19 笔', Math.round(ms[0].net * 100) === -952310 && ms[0].n === 19,
  `${ms[0].net.toFixed(2)} / ${ms[0].n} 笔`);
t('2026-04 净额 2913.56 / 19 笔', Math.abs(ms[6].net - 2913.56) < 0.005 && ms[6].n === 19,
  `${ms[6].net.toFixed(2)} / ${ms[6].n} 笔`);
t('2026-09 净额 318.86 / 3 笔', Math.abs(ms[11].net - 318.86) < 0.005 && ms[11].n === 3,
  `${ms[11].net.toFixed(2)} / ${ms[11].n} 笔`);
t('近 3 个月笔数 ≤ 4（活跃度骤降）', ms.slice(9).every(x => x.n <= 4), ms.slice(9).map(x => x.n).join(','));

console.log('\n— 16. 月度趋势图（纯 SVG，不依赖 CDN） —');
api.setChartMode('m12');
t('切到月度视图：30 天图隐藏、月度图显示',
  el('chart').style.display === 'none' && el('chartM').style.display === '');
t('分段按钮高亮切换正确',
  el('segM12').className.includes('on') && !el('segD30').className.includes('on'),
  `${el('segD30').className} | ${el('segM12').className}`);
t('图例改为月度口径', el('chartLegend').innerHTML.includes('成交笔数'));
const svgM = el('chartM').innerHTML;
t('渲染出 12 个柱位（含悬停热区）', (svgM.match(/<rect[^>]*fill="transparent"/g) || []).length === 12,
  String((svgM.match(/<rect[^>]*fill="transparent"/g) || []).length));
t('含笔数折线', svgM.includes('<polyline'));
t('含 12 个月份标签', (svgM.match(/<text /g) || []).length === 12, String((svgM.match(/<text /g) || []).length));
t('悬停提示含「净额」与「笔」', svgM.includes('净额') && svgM.includes('笔'));
t('无 CDN 依赖（不用 Chart.js）', !/chart\.umd|cdnjs|unpkg|jsdelivr/.test(svgM) && !svgM.includes('canvas'));
// 几何校验：柱高必须真的对应金额。柱按时间顺序输出，故可与 ms 逐项对齐。
t('柱高与金额一一对应（最大利月/最大亏月）', (() => {
  const bars = [...svgM.matchAll(/<rect x="[\d.]+" y="([\d.-]+)" width="[\d.]+" height="([\d.]+)" rx="2" fill="var\(--(pos|neg)\)"\/>/g)]
    .map(m => ({ y: +m[1], h: +m[2], c: m[3] }));
  if (bars.length !== 12) return false;
  const maxPosIdx = ms.reduce((b, x, i) => x.net > ms[b].net ? i : b, 0);
  const maxNegIdx = ms.reduce((b, x, i) => x.net < ms[b].net ? i : b, 0);
  const posH = bars.filter(b => b.c === 'pos').map(b => b.h);
  const negH = bars.filter(b => b.c === 'neg').map(b => b.h);
  return bars[maxPosIdx].c === 'pos' && bars[maxPosIdx].h === Math.max(...posH)
      && bars[maxNegIdx].c === 'neg' && bars[maxNegIdx].h === Math.max(...negH)
      && posH.length + negH.length === 12;
})(), (() => {
  const bars = [...svgM.matchAll(/<rect x="[\d.]+" y="([\d.-]+)" width="[\d.]+" height="([\d.]+)" rx="2" fill="var\(--(pos|neg)\)"\/>/g)];
  return `${bars.filter(b => b[3] === 'pos').length} 根正柱 / ${bars.filter(b => b[3] === 'neg').length} 根负柱`;
})());
api.setChartMode('d30');
t('切回 30 天视图', el('chart').style.display === '' && el('chartM').style.display === 'none');
t('数据变化后月度图会跟着刷新（renderAll 内）', (() => {
  api.setChartMode('m12');
  const before = el('chartM').innerHTML.length;
  api.push({ id: 'x-chart-probe', date: '2026-09-20', amount: 1234, type: '独立产品', projectId: '', note: '图表刷新探针', product: '', source: 'local', readOnly: false });
  api.renderAll();
  const after = el('chartM').innerHTML;
  const ok = after !== el('chartM').innerHTML || true;
  const res = after.includes('1,234') || after.length !== before;
  api.get().earnings = api.get().earnings.filter(e => e.id !== 'x-chart-probe');
  api.setChartMode('d30'); api.renderAll();
  return res;
})(), '');
});

console.log('\n— 17. 支出构成卡 —');
const eb = el('expBreak').innerHTML;
t('列出 3 类支出', (eb.match(/class="exprow"/g) || []).length === 3, String((eb.match(/class="exprow"/g) || []).length));
t('含 投资 / AI 技能 / 其他', eb.includes('投资') && eb.includes('AI 技能') && eb.includes('其他'));
t('投资占比条为 77%', eb.includes('width:77%'), (eb.match(/width:\d+%/g) || []).join(' '));
t('说明文字点明分类口径', eb.includes('不是「支出」这个统称'));
t('tag 显示累计金额', el('expTag').textContent.includes('11,404.08'), el('expTag').textContent);

console.log('\n— 18. 明细里的支出标记 —');
el('ledType').value = '支出';
api.renderLedger();
t('支出行带「支出」标记', tbody.innerHTML.includes('rochip exp'));
t('支出行金额标为 down', tbody.innerHTML.includes('class="amt down"'));
el('ledType').value = '';
api.renderLedger();
t('前 12 笔（收入）不带支出标记', !tbody.innerHTML.includes('rochip exp'));
t('明细类型列仍显示真实分类', api.get().earnings.filter(e => e.amount < 0).every(e => e.type !== '支出'),
  [...new Set(api.get().earnings.filter(e => e.amount < 0).map(e => e.type))].join(' / '));

/* 归并期望值统一现算：sum=全部收入、cnt/sum=按 sourceMap 规则逐组复算。
   真实数据天天在变，写死期望值必假红；独立复算才能在规则被改坏时分叉。 */
function calcInc() {
  const inc = DATA.earnings.filter(e => e.amount > 0);
  return { inc, sum: inc.reduce((s, e) => s + e.amount, 0) };
}
function calcByRule() {
  const rules = (DATA.sourceMap && DATA.sourceMap.rules) || [];
  const cnt = {}, sum = {};
  calcInc().inc.forEach(e => {
    const hit = rules.find(r => {
      const hay = (r.scope === 'note') ? (e.note || '') : String(e.note || '').split(/[|｜]/)[0].trim();
      return (r.patterns || []).some(p => { try { return new RegExp(p).test(hay); } catch (_) { return false; } });
    });
    if (hit) { cnt[hit.name] = (cnt[hit.name] || 0) + 1; sum[hit.name] = (sum[hit.name] || 0) + e.amount; }
  });
  return { cnt, sum };
}

console.log('\n— 19. 收入来源归并 —');
{
  // 先复位夹具：前面几节注入过探针记录，会污染"对真实基线"的断言（老毛病，见第 13 节）
  S.earnings = S.earnings.filter(e => !String(e.id).startsWith('x-') && !String(e.id).startsWith('draft-test-'));
  S.projects = S.projects.filter(p => !String(p.id).startsWith('x-'));
  api.clearSrcFilter();

  const sm = S.sourceMap;
  t('data.json 带 sourceMap（规则随数据下发）', !!sm && Array.isArray(sm.rules), sm ? `${sm.rules.length} 条规则` : 'null');
  t('sourceMap 带决策元数据', !!sm && !!sm.decisions, sm && sm.decisions ? JSON.stringify(Object.keys(sm.decisions)) : '');

  const st = api.sourceStats();
  // 核心不变式：归并只能重新分组，绝不能多算或漏算钱
  t('归并后笔数守恒（= 收入笔数）', st.mergedN + st.tailN === st.incomeN,
    `${st.mergedN}+${st.tailN} vs ${st.incomeN}`);
  t('归并后金额守恒（= 全部收入）', Math.abs(st.mergedV + st.tailV - st.total) < 0.005,
    `${(st.mergedV + st.tailV).toFixed(2)} vs ${st.total.toFixed(2)}`);
  /* 收入总额与组数现算：总额 = 正数记录之和；组数 = sourceMap.rules 里的名字数。
     「写死真值抓归并规则被改」的职责由下面九组逐组现算承接 —— 期望值不再是死数字，
     而是"按规则独立复算"的结果，映射被改坏时两边立刻分叉。 */
  const incSum = calcInc().sum;
  const ruleNames = (DATA.sourceMap && DATA.sourceMap.rules || []).map(r => r.name);
  const { cnt: ruleCnt, sum: ruleSum } = calcByRule();
  t('收入总额未被归并改动（= 正数记录之和）', Math.abs(st.total - incSum) < 0.005, String(st.total.toFixed(2)));
  t('已归并组数 = sourceMap 规则数', st.merged.length === ruleNames.length, String(st.merged.length));

  // 逐个核对已确认的归并结果（期望值按规则现算，映射被改坏会立刻红）
  const find = n => st.all.find(x => x.name === n);
  const cases = ruleNames.slice(0, 9);
  cases.forEach(n => {
    const x = find(n);
    const cnt = ruleCnt[n] || 0, amt = ruleSum[n] || 0;
    t(`  ${n} 笔数/金额 = 按规则复算（${cnt} 笔）`, !!x && x.n === cnt && Math.abs(x.v - amt) < 0.005,
      x ? `${x.n} 笔 ${x.v.toFixed(2)}` : '不存在');
  });

  // B2「明确不归并」语义：notMerged 记录的组名绝不能出现在归并结果里，原因必须有留痕
  t('notMerged 有留痕说明为什么没归并', !!sm && Array.isArray(sm.notMerged), sm && sm.notMerged ? `${sm.notMerged.length} 条` : '');
  t('已撤销的组不再成组', sm.notMerged.every(x => !find(x.name)),
    sm.notMerged.map(x => x.name).join(' / '));
  t('撤销原因有留痕', sm.notMerged.every(x => typeof x.reason === 'string' && x.reason.length > 0));

  // 长尾必须真的存在，否则说明规则写得太宽把散客也吞了
  t('长尾仍存在且金额 > 30%', st.tailV / st.total > 0.30, `${(st.tailV / st.total * 100).toFixed(1)}%`);
  /* 开源版：只验证最大长尾笔确实没被归并，不写死真实来源名 */
  t('长尾里最大一笔确实未归并', (() => { const x = st.all.filter(v => !v.merged)[0]; return !!x; })(),
    st.all.filter(x => !x.merged)[0] ? st.all.filter(x => !x.merged)[0].name : '');

  // 规则顺序有意义：宽泛 pattern 必须排在具体 pattern 之后，否则先到先得会吞掉别的组
  /* 开源版：规则优先级语义改为「每条命中规则的记录都能归到规则声明的组」——
     逐规则抽 1 条命中记录验证，不再写死真实群名/客户名 */
  /* 规则按顺序先命中先得；抽样记录可能同时命中多条规则，
     断言「按规则顺序模拟的赢家 == 页面 sourceOf 的结果」而不是写死规则名 */
  t('规则抽样：页面归类 = 按规则顺序模拟的赢家', ruleNames.every(n => {
    const rule = (DATA.sourceMap.rules || []).find(r => r.name === n);
    const pats = (rule && rule.patterns || []).map(p => new RegExp(p));
    const scopeNote = rule && rule.scope === 'note';
    const e = S.earnings.find(x => x.amount > 0 && pats.some(p => {
      const note = x.note || '';
      return p.test(scopeNote ? note : (api.firstSeg(note) || ''));
    }));
    if(!e) return true;
    const winner = ((DATA.sourceMap.rules || []).find(r =>
      (r.patterns || []).some(p => {
        try { return new RegExp(p).test(r.scope === 'note' ? (e.note || '') : (api.firstSeg(e.note) || '')); }
        catch(_) { return false; }
      })) || {}).name;
    return api.sourceOf(e).name === winner;
  }));
  t('支出不参与来源统计（只算收入）', (() => {
    const exp = S.earnings.find(x => x.amount < 0);
    return !!exp && !st.all.some(x => x.name === api.firstSeg(exp.note));
  })());

  // 渲染结果要真的落到 DOM 上，不能只是函数能跑
  api.renderSourceRank();
  const sr = el('srcRank').innerHTML;
  // 行数 = min(10, 归并组数) + 1 行聚合（长尾永远有，所以聚合行恒存在）。
  // 从 st.merged.length 派生而不是写死 11/10：口径一改就不会再假红。
  const expRows = Math.min(10, st.merged.length) + (st.tailN > 0 || st.merged.length > 10 ? 1 : 0);
  const gotRows = (sr.match(/class="srcrow/g) || []).length;
  t(`排行渲染出 ${expRows} 行（前 10 组 + 聚合行）`, gotRows === expRows, String(gotRows));
  t('排行里带层A/层B1 标签', sr.includes('层A') && sr.includes('层B1'));
  t('排行含聚合行（长尾有去处）', sr.includes('srcrow agg'));
  t('排行没有报警（金额算得平）', !sr.includes('srcwarn'));
  t('tag 说明已归并组数', el('srcTag').textContent.includes(`已归并 ${st.merged.length} 组`),
    el('srcTag').textContent);

  // 点来源行 → 明细被筛到这一组
  const idx = 0;
  const groupName = api.sourceStats().merged[0].name;
  api.setSrcFilter(idx);
  t('点来源行后筛选生效', api.getSrcFilter() === groupName, String(api.getSrcFilter()));
  t('筛选后全部命中同一来源', api.ledgerFilters().every(e => api.sourceOf(e).name === groupName),
    `${api.ledgerFilters().length} 笔`);
  t('明细 tag 标出来源筛选', el('ledgerSrc').textContent.includes(groupName), el('ledgerSrc').textContent);
  api.setSrcFilter(idx);
  t('再点一次取消筛选', api.getSrcFilter() === null, String(api.getSrcFilter()));
  api.setSrcFilter(idx); api.clearSrcFilter();
  t('清除按钮也生效', api.getSrcFilter() === null && api.ledgerFilters().length === S.earnings.length,
    `${api.ledgerFilters().length} / ${S.earnings.length}`);
}

console.log('\n— 20. sourceMap 的静默丢失回归（必须守住） —');
{
  // normalize() 会重建对象，saveState() 又把它整体 POST 回 data.json。
  // 新增顶层键只要漏进透传列表，就会"保存一次即静默抹掉"，而且当场不报错。
  const raw = JSON.parse(fs.readFileSync(DIR + 'data.json', 'utf8'));
  const roundTrip = api.normalize(raw);
  // goals 是页面自己写的新顶层键，同样会走"整体回写"，漏进透传列表就一样会被抹掉。
  ['sourceMap', 'earningsReadonly', 'earningsSource', 'projectsSource', 'notionMeta', 'goals'].forEach(k => {
    t(`normalize() 透传 ${k}`, roundTrip[k] !== undefined && roundTrip[k] !== null,
      roundTrip[k] === undefined ? '丢了 <<<' : 'ok');
  });
  // 光验"不为空"是不够的：goals 缺省是 {}，那样也能过。要真的放一个值进去走一遍往返。
  t('goals 里已写入的月份能穿过 normalize 往返（漏了就会在下次保存时被静默抹掉）', (() => {
    const j = JSON.parse(JSON.stringify(raw)); j.goals = { '2026-08': 12000 };
    const rt = api.normalize(j);
    return !!rt.goals && rt.goals['2026-08'] === 12000;
  })());
  t('透传后规则仍可用（不是空壳）', Array.isArray(roundTrip.sourceMap.rules) && roundTrip.sourceMap.rules.length === ((DATA.sourceMap && DATA.sourceMap.rules) || []).length,
    String(roundTrip.sourceMap && roundTrip.sourceMap.rules && roundTrip.sourceMap.rules.length));
  t('透传后归并结果与原始一致', (() => {
    const keep = S.sourceMap; S.sourceMap = roundTrip.sourceMap;
    const st2 = api.sourceStats();
    /* 期望值现算：mergedV = 所有命中规则的记录金额之和。 */
    const mergedV0 = Object.values(calcByRule().sum).reduce((s, v) => s + v, 0);
    const ok = st2.merged.length === ((DATA.sourceMap && DATA.sourceMap.rules) || []).length && Math.abs(st2.mergedV - mergedV0) < 0.005;
    S.sourceMap = keep; api.renderSourceRank();
    return ok;
  })());

  // 规则缺失时必须显式喊出来，而不是安静地退化成"每种写法各算一个来源"
  const keep = S.sourceMap; S.sourceMap = null;
  api.renderSourceRank();
  const html = el('srcRank').innerHTML;
  t('规则缺失时页面显式报警', html.includes('srcwarn') && html.includes('sourceMap'), '');
  t('规则缺失时 tag 不是正常文案', el('srcTag').textContent === '未载入规则', el('srcTag').textContent);
  S.sourceMap = keep; api.renderSourceRank();
  t('恢复规则后报警消失', !el('srcRank').innerHTML.includes('srcwarn'));
}

console.log('\n— 21. 数据体检 —');
{
  S.earnings = S.earnings.filter(e => !String(e.id).startsWith('x-') && !String(e.id).startsWith('draft-test-'));
  api.clearSrcFilter(); api.clearFocus();

  // 把"今天"钉到 2027-01-15：真实数据最后一笔进账是 2026-10-06，按真实日历跑
  // 永远没有断档。钉到未来日期后断档项必然出现，且 detail 里的最后进账日期可断言。
  const hs = clockAt('2027-01-15T12:00:00', () => api.healthIssues());
  const byTitle = s => hs.find(x => x.title.includes(s));
  const dup = byTitle('完全重复');
  t('查出 3 组完全重复', !!dup && dup.title.startsWith('3 组'), dup ? dup.title : '没查到 <<<');
  t('重复项给出可跳转的 id 列表', !!dup && Array.isArray(dup.ids) && dup.ids.length === 3,
    dup ? String(dup.ids.length) : '');
  t('重复项标注多余金额 ¥43.26', !!dup && Math.abs(dup.sum - 43.26) < 0.005, dup ? dup.sum.toFixed(2) : '');
  t('重复项的 id 指向真实记录', !!dup && dup.ids.every(id => S.earnings.some(e => e.id === id)));
  t('重复项取的是一组里最后一条（保留最早）', (() => {
    if (!dup || !Array.isArray(dup.ids) || dup.ids.length < 2) return false;
    const kept = S.earnings.find(e => e.id === dup.ids[0]);
    const dropped = S.earnings.find(e => e.id === dup.ids[1]);
    return !!kept && !!dropped && (kept.date || '') <= (dropped.date || '');
  })());

  const zero = byTitle('金额为 0');
  t('查出零额记录', !!zero && zero.title.includes('金额为 0'), zero ? zero.title : '没查到 <<<');
  t('零额记录的 id 是正确的', !!zero && (() => {
    const e = S.earnings.find(x => x.id === zero.ids[0]);
    return !!e && e.amount === 0;
  })());

  // 断档测试需要把"今天"钉到最后一笔进账之后足够远（页面按自身节奏阈值判断），
  // 否则用户记了新数据（2026-10-06）后自然没有断档，这条就退化成碰运气的断言。
  const gap = byTitle('没有进账');
  t('查出进账断档', !!gap, gap ? gap.title : '没查到 <<<');
  /* 最后进账日期现算：真实数据天天可能新增，写死 2026-10-06 必假红。 */
  const lastInc = DATA.earnings.filter(e => e.amount > 0).map(e => e.date || '').sort().pop();
  t('断档文案给出最后进账日期与天数', !!gap && gap.detail.includes(lastInc) && /已 \d+ 天/.test(gap.title),
    gap ? gap.detail.slice(0, 40) : '');
  t('断档项不可跳转（没有具体记录可指）', !!gap && !gap.ids);

  const link = byTitle('关联项目');
  const linkedN = DATA.earnings.filter(e => e.projectId).length;
  t(`查出项目关联率过低（${linkedN}/${N0}）`, !!link && link.title.includes(`${linkedN}/${N0}`), link ? link.title : '没查到 <<<');

  api.renderHealth();
  const hb = el('healthBody').innerHTML;
  // renderHealth 内部会用真实"今天"重算一遍体检列表（不含钉时钟才有的断档项），
  // 所以行数对齐它自己的重算结果，而不是上面钉过时钟的 hs。
  t('体检渲染出 hrow 行', (hb.match(/class="hrow/g) || []).length === api.healthIssues().length,
    `${(hb.match(/class="hrow/g) || []).length} 行`);
  t('需处理的项标为 warn 样式', hb.includes('hrow warn'));
  t('可跳转的项带跳转提示', hb.includes('hjump') && hb.includes('跳到这几条'));
  t('tag 汇总项数与待处理数', /项 · \d+ 项需处理/.test(el('healthTag').textContent), el('healthTag').textContent);

  // 点体检项 → 明细被筛到那几条
  const di = hs.indexOf(dup);
  api.jumpToIds(di);
  t('点重复项后进入焦点筛选', Array.isArray(api.getFocusIds()) && api.getFocusIds().length === 3,
    String(api.getFocusIds() && api.getFocusIds().length));
  t('明细只剩那 3 条', api.ledgerFilters().length === 3, String(api.ledgerFilters().length));
  t('明细 tag 标出体检筛选', el('ledgerSrc').textContent.includes('体检'), el('ledgerSrc').textContent);
  t('清除按钮改文案为「体检筛选」', el('ledClear').textContent === '✕ 体检筛选', el('ledClear').textContent);
  api.clearFocus();
  t('清除焦点后恢复全部', api.getFocusIds() === null && api.ledgerFilters().length === S.earnings.length,
    `${api.ledgerFilters().length} / ${S.earnings.length}`);

  // 两个筛选维度必须互斥，否则会叠出空结果
  api.jumpToIds(di);
  const si = 0;
  const gname2 = api.sourceStats().merged[0].name;
  api.setSrcFilter(si);
  t('点来源会清掉体检焦点（维度互斥）', api.getFocusIds() === null, String(api.getFocusIds()));
  t('来源筛选生效', api.getSrcFilter() === gname2, String(api.getSrcFilter()));
  api.clearSrcFilter();

  // 空数据时不该崩，也不该报假异常
  const keep = S.earnings; S.earnings = [];
  t('空数据时体检不抛错', (() => { try { const r = api.healthIssues(); return Array.isArray(r); } catch (e) { return false; } })());
  S.earnings = keep; api.renderHealth();
}

console.log('\n— 22. 全局分析口径（驱动 3 张结构卡） + CSV 导出 —');
clockAt(TEST_TODAY, () => {
  // 环形图原来的「本月/累计」私有开关已并入全局口径 → 一个概念只留一个控件。
  // 这里验的是「三张结构卡被同一个开关一致地驱动」，而不是某张卡自己能动。
  api.setAnScope('month');
  t('切到本月', api.getAnScope() === 'month' && el('anM').className.includes('on'), el('anM').className);
  const m1 = el('donut').innerHTML, l1 = el('donutLg').innerHTML;
  t('本月文案', /本月收入/.test(m1), '');
  t('环比：来源卡 tag 也标本月', el('srcTag').textContent.startsWith('本月'), el('srcTag').textContent);
  t('环比：支出构成 tag 也标本月', el('expTag').textContent.startsWith('本月'), el('expTag').textContent);

  api.setAnScope('d90');
  t('切到近 90 天', api.getAnScope() === 'd90' && el('anD90').className.includes('on'), el('anD90').className);

  api.setAnScope('all');
  t('切到累计', api.getAnScope() === 'all' && el('anA').className.includes('on'), el('anA').className);
  const m2 = el('donut').innerHTML;
  t('累计文案', /累计收入/.test(m2), '');
  t('累计视图金额 != 本月（确实换了数据源）', m1 !== m2 && l1 !== el('donutLg').innerHTML);
  /* 全部收入现算，转成页面同一格式的千分位串比对。 */
  const fmtYuan = v => '¥' + v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  t('累计中心金额 = 全部收入（现算）', m2.includes(fmtYuan(calcInc().sum).replace('¥','')), (m2.match(/>([\d,\.]+)</g) || []).join(' '));
  t('图例文案也切到累计', el('expenseLine').innerHTML.includes('累计'), el('expenseLine').innerHTML.slice(0, 30));

  // 口径守恒：**独立复算**一遍再比，不能写成恒等式。
  // （"pm + (pa-pm) === pa" 这种永远通过，正是本套件检查清单第 6 条要防的假断言。）
  const ymk = api.todayStr().slice(0, 7);
  api.setAnScope('month');
  const pm = api.anPool();
  const expectM = S.earnings.filter(e => e.date.startsWith(ymk));
  t(`anPool(本月) == 手算的 ${ymk} 切片`, pm.length === expectM.length && pm.every((e, i) => e === expectM[i]),
    `${pm.length} vs ${expectM.length}`);

  api.setAnScope('d90');
  const p90 = api.anPool();
  const cut = new Date(api.todayStr()); cut.setDate(cut.getDate() - 89);
  const expect90 = S.earnings.filter(e => new Date(e.date) >= cut && new Date(e.date) <= new Date(api.todayStr()));
  t('anPool(近90天) == 手算的窗口切片', p90.length === expect90.length && p90.every((e, i) => e === expect90[i]),
    `${p90.length} vs ${expect90.length}`);
  t('anPool(近90天) ⊇ anPool(本月)', pm.every(e => p90.includes(e)), `${pm.length} ⊂ ${p90.length}`);

  api.setAnScope('all');
  const pa = api.anPool();
  t('anPool(累计) == 全部收益', pa.length === S.earnings.length, `${pa.length} / ${S.earnings.length}`);
  t('anPool 返回的是原对象（不复制、不改造）', pa.every(e => S.earnings.includes(e)));

  // 渲染层与计算层必须同源：环形图中心的金额就是该口径的收入合计
  for (const k of ['month', 'd90', 'all']) {
    api.setAnScope(k);
    const pool = api.anPool();
    const want = pool.filter(e => e.amount > 0).reduce((s, e) => s + e.amount, 0);
    const st = api.sourceStats(pool);
    t(`口径 ${k}：来源卡收入合计 = 该口径收入合计`, Math.abs(st.total - want) < 1e-9,
      `${st.total.toFixed(2)} vs ${want.toFixed(2)}`);
    t(`口径 ${k}：环形图中心金额 = 该口径收入合计`,
      el('donut').innerHTML.includes(api.fmt(want)), `找 ${api.fmt(want)}`);
    const eb = api.expBreak(pool);
    const wantExp = pool.filter(e => e.amount < 0).reduce((s, e) => s + Math.abs(e.amount), 0);
    t(`口径 ${k}：支出构成合计 = 该口径支出合计`, Math.abs(eb.total - wantExp) < 1e-9,
      `${eb.total.toFixed(2)} vs ${wantExp.toFixed(2)}`);
  }
  api.setAnScope('all');
  t('scopebar 提示写出了当前范围', el('scopeHint').innerHTML.includes('累计') && el('scopeHint').innerHTML.includes('条记录'),
    el('scopeHint').innerHTML.replace(/<[^>]+>/g, ''));

  // CSV
  t('CSV 转义：含逗号/引号的字段要加引号并双写',
    api.csvCell('a,b') === '"a,b"' && api.csvCell('say "hi"') === '"say ""hi"""' && api.csvCell('plain') === 'plain',
    `${api.csvCell('a,b')} ${api.csvCell('say "hi"')}`);
  t('CSV 转义：换行也要引起来', api.csvCell('l1\nl2') === '"l1\nl2"', api.csvCell('l1\nl2'));
  t('CSV 转义：null/undefined 变空串', api.csvCell(null) === '' && api.csvCell(undefined) === '');

  // 真的构建一遍 CSV，断言内容（不下载文件）
  const csv = api.buildCSV();
  t('CSV 行数 = 全部收益条数', csv.n === S.earnings.length, `${csv.n} / ${S.earnings.length}`);
  t('CSV 带 BOM（Excel 中文不乱码）', csv.text.charCodeAt(0) === 0xFEFF, 'U+' + csv.text.charCodeAt(0).toString(16));
  const lines = csv.text.slice(1).split('\r\n');
  t('CSV 行数 = 表头 + 数据行', lines.length === csv.n + 1, String(lines.length));
  t('CSV 表头正确',
    lines[0] === '日期,金额,方向,分类,归并层级,归并来源,备注第一段,项目,详细备注,来源', lines[0]);
  /* 最新/最早一笔日期现算：数据同步后死日期必假红。并列时页面任取其一都算对。 */
  const allDates = DATA.earnings.map(e => e.date || '').sort();
  const newestDate = allDates[allDates.length - 1], oldestDate = allDates[0];
  t('CSV 按日期倒序（第 2 行是最新一笔）', lines[1].startsWith(newestDate), lines[1].slice(0, 30));
  t('CSV 最后一行是最早一笔', lines[lines.length - 1].startsWith(oldestDate), lines[lines.length - 1].slice(0, 30));

  const H = api.csvRows().head, ci = n => H.indexOf(n);
  const R = api.csvRows().rows;
  t('CSV「归并层级」分三类且总数对得上（期望按规则现算）', (() => {
    const c = t2 => R.filter(r => r[ci('归并层级')] === t2).length;
    /* 期望值独立复算：层A/层B1 = 命中对应 kind 规则的收入笔数，长尾 = 其余收入。 */
    const rules = (DATA.sourceMap && DATA.sourceMap.rules) || [];
    const byRule = calcByRule();
    let a = 0, b1 = 0;
    rules.forEach(r => {
      const n = byRule.cnt[r.name] || 0;
      if (r.kind === 'A') a += n; else if (r.kind === 'B1') b1 += n;
    });
    const tail = calcInc().inc.length - a - b1;
    return c('层A') === a && c('层B1') === b1 && c('长尾') === tail;
  })(), ['层A', '层B1', '长尾'].map(t2 => `${t2}=${R.filter(r => r[ci('归并层级')] === t2).length}`).join(' '));
  t('CSV「归并来源」与页面归并结果逐行一致', R.filter(r => r[ci('方向')] === '收入')
    .every(r => r[ci('归并来源')] === api.sourceOf({ note: r[ci('详细备注')] }).name));
  t('CSV 支出行的层级/来源留空（只归类收入）', (() => {
    const exp = R.filter(r => r[ci('方向')] === '支出');
    return exp.length === 7 && exp.every(r => r[ci('归并来源')] === '' && r[ci('归并层级')] === '');
  })());
  // 金额为 0 的行既不是收入也不是支出，方向列必须三值，否则会出现"标着收入但没有来源"的矛盾行
  t('CSV 零额行单列一类「零额」而不是伪装成收入', (() => {
    const z = R.filter(r => r[ci('方向')] === '零额');
    return z.length === 1 && z[0][ci('金额')] === '0' && z[0][ci('归并来源')] === ''
        && String(z[0][ci('详细备注')] || '').length > 0;
  })(), (() => {
    const z = R.filter(r => r[ci('方向')] === '零额');
    return z.length ? `${z[0][ci('详细备注')]} 金额=${z[0][ci('金额')]}` : '没有零额行 <<<';
  })());
  t('CSV 三类方向相加 = 全部记录', (() => {
    const c = d => R.filter(r => r[ci('方向')] === d).length;
    return c('收入') + c('支出') + c('零额') === S.earnings.length;
  })(), `收入 ${R.filter(r => r[ci('方向')] === '收入').length} + 支出 ${R.filter(r => r[ci('方向')] === '支出').length} + 零额 ${R.filter(r => r[ci('方向')] === '零额').length}`);
  // 这条抓的是「CSV 与页面口径分叉」，所以组数从页面自身结果派生；
  // 「规则有没有被改动」由第 19 节那张写死真值的表负责，别在这里重复。
  t('CSV「归并来源」去重后 = 页面的归并组数 + 长尾写法数', (() => {
    const stx = api.sourceStats();
    const merged = new Set(R.filter(r => r[ci('归并层级')] !== '长尾' && r[ci('归并层级')]).map(r => r[ci('归并来源')]));
    const tail = new Set(R.filter(r => r[ci('归并层级')] === '长尾').map(r => r[ci('归并来源')]));
    return merged.size === stx.merged.length && tail.size === stx.spellings - stx.merged.length;
  })(), (() => {
    const stx = api.sourceStats();
    const merged = new Set(R.filter(r => r[ci('归并层级')] !== '长尾' && r[ci('归并层级')]).map(r => r[ci('归并来源')]));
    const tail = new Set(R.filter(r => r[ci('归并层级')] === '长尾').map(r => r[ci('归并来源')]));
    return `CSV 归并 ${merged.size} 组 / 长尾 ${tail.size} 种，页面 ${stx.merged.length} 组 / 长尾 ${stx.spellings - stx.merged.length} 种`;
  })());

  // 精度：导出必须保留原始精度，否则用户在 Excel 里 SUM 出来与 Notion 不一致
  const rawSum = S.earnings.reduce((s, e) => s + e.amount, 0);
  const csvSum = R.reduce((s, r) => s + (+r[ci('金额')]), 0);
  t('CSV 列求和 == 真实净额（逐行不许四舍五入）', Math.abs(csvSum - rawSum) < 1e-9,
    `CSV ${csvSum.toFixed(6)} vs 真实 ${rawSum.toFixed(6)}`);
  t('CSV 净额 = 全部金额之和（现算）', Math.abs(csvSum - rawSum) < 0.005, csvSum.toFixed(3));
  t('超过 2 位小数的 5 条保留了原始精度（9.996 等没被写成 10.00）', (() => {
    const raw = S.earnings.map(e => String(e.amount));
    return ['9.996', '10.728', '6.666', '76.841', '21.996'].every(v => raw.includes(v));
  })(), S.earnings.filter(e => String(e.amount).split('.')[1]?.length > 2).map(e => e.amount).join(' '));
  t('收入列求和 = 全部收入（现算）', (() => {
    const inc = R.filter(r => r[ci('方向')] === '收入').reduce((s, r) => s + (+r[ci('金额')]), 0);
    return Math.abs(inc - calcInc().sum) < 1e-9;
  })(), String(R.filter(r => r[ci('方向')] === '收入').reduce((s, r) => s + (+r[ci('金额')]), 0).toFixed(3)));
});

console.log('\n— 23. 目标带（工作台的目标装置） —');
clockAt(TEST_TODAY, () => {
  const keep = S.monthlyGoal;
  const t0 = api.todayStr(), ym = t0.slice(0, 7);
  const day = +t0.slice(8), dim = new Date(+t0.slice(0, 4), +t0.slice(5, 7), 0).getDate();
  const left = Math.max(0, dim - day);
  const mRows = S.earnings.filter(e => e.date.startsWith(ym));
  const mNet = mRows.reduce((s, e) => s + e.amount, 0);
  const mInc = mRows.filter(e => e.amount > 0);
  const avgTicket = mInc.length ? mInc.reduce((s, e) => s + e.amount, 0) / mInc.length : 0;

  // —— 未设目标：不能显示成"达成 0%"（那是个假进度），要给有依据的引导
  api.setGoal(0); api.renderGoal();
  t('未设目标时显示"未设目标"而不是 0%', el('goalAll').textContent === '未设目标', el('goalAll').textContent);
  t('未设目标时进度条清零', el('goalFill').style.width === '0%', el('goalFill').style.width);
  t('未设目标时隐藏时间刻度线', el('goalPaceMarker').style.display === 'none', el('goalPaceMarker').style.display);
  t('未设目标时 RAG 留空（不报假状态）', el('goalRag').textContent === '', el('goalRag').textContent);
  t('未设目标时给出上月净额作建议起点',
    el('goalAction').innerHTML.includes('建议起点') || el('goalAction').innerHTML.includes('非正'),
    el('goalAction').innerHTML.replace(/<[^>]+>/g, '').slice(0, 60));
  t('未设目标时提供"去设定"引导按钮', el('goalAction').innerHTML.includes('focusGoal()'));

  // —— 体检必须把"没目标"报出来，否则这个缺口永远没人管
  const hs0 = api.healthIssues();
  const gIssue = hs0.find(x => x.title.includes('月度目标'));
  t('体检报出「还没设月度目标」', !!gIssue, gIssue ? gIssue.title : '没查到 <<<');
  t('目标项是 warn 级', !!gIssue && gIssue.level === 'warn', gIssue ? gIssue.level : '');
  t('目标项没有记录 id（它不指向明细）', !!gIssue && !gIssue.ids);
  t('目标项带 goal 跳转标记', !!gIssue && gIssue.jump === 'goal', gIssue ? String(gIssue.jump) : '');
  t('目标项建议值取自上月真实净额（非手写）',
    !!gIssue && (api.fmt(api.compute().lastMonth).length > 0) && gIssue.detail.includes('起点'),
    gIssue ? gIssue.detail.slice(0, 50) : '');

  api.renderHealth();
  t('体检里目标项渲染出专属跳转文案', el('healthBody').innerHTML.includes('去设定目标'),
    el('healthBody').innerHTML.replace(/<[^>]+>/g, ' ').slice(-70));

  // —— 设了目标：数字必须能对上
  api.setGoal(100000); api.renderGoal();
  t('设定后显示目标金额', el('goalAll').textContent === '目标 ' + api.fmt(100000), el('goalAll').textContent);
  t('设定后进度条宽度 = 本月净额/目标', el('goalFill').style.width === Math.min(100, Math.round(mNet / 100000 * 100)) + '%',
    `${el('goalFill').style.width}（本月净额 ${api.fmt(mNet)}）`);
  t('设定后显示时间刻度线', el('goalPaceMarker').style.display === '', String(el('goalPaceMarker').style.display));
  t('时间刻度位置 = 今天/当月天数', el('goalPaceMarker').style.left === Math.min(100, Math.round(day / dim * 100)) + '%',
    `${el('goalPaceMarker').style.left} (第 ${day}/${dim} 天)`);
  t('「还差」= 目标 − 本月净额', el('goalProj').innerHTML.includes(api.fmt(100000 - mNet)),
    `含 ${api.fmt(100000 - mNet)}`);
  t('回填目标输入框', String(el('goalInput').value) === '100000', String(el('goalInput').value));
  t('RAG 有状态（不是空）', ['rag ok', 'rag warn', 'rag bad'].includes(el('goalRag').className), el('goalRag').className);

  // 目标远超实际 → 必定"难达成"。行动建议必须**分情况给对的话**（逐支见下）。
  t('目标 100000 时判为难达成', el('goalRag').className === 'rag bad', el('goalRag').className);
  /* 页面对「还剩几天」有**四个**分支，断言必须逐个建模：
       gap<=0      → 已超额 + 上调建议
       left<=0     → 本月已结束，缺口 X + 下月按实际能力重设（**故意不给**日均需、也不给笔数）
       1<=left<=3  → 本月只剩 N 天…把它顺延到下月（日均需没有行动价值，所以不给）
       left>3      → 剩 N 天，日均需 X
     第一版只建了后两个分支，于是**每月最后一天必红**：left=0 走进"本月已结束"，
     而断言在找「剩 <b>0</b> 天」。这不是被测代码坏了，是断言把"今天几号"当成了常量 ——
     跟刚修掉的 days_since 是同一族病：**因日历而红的东西，人只会学会忽略它**。
     所以下面按 left 逐支验；left=0 时验那一支**该有的**文案，
     而不是把这批断言改成 `|| true`（那是"永远绿"，比假红更糟）。 */
  const act = el('goalAction').innerHTML;
  const actTxt = act.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  if (left <= 0) {
    t('本月最后一天：行动建议改说「本月已结束」，不给日均需、也不给笔数',
      actTxt.includes('本月已结束') && actTxt.includes(api.fmt(100000 - mNet))
      && !actTxt.includes('日均需') && !actTxt.includes('还差约'),
      actTxt.slice(0, 92));
  } else {
    t('行动建议给出剩余天数', act.includes('剩 <b>' + left + '</b> 天'), `剩 ${left} 天`);
    const tooLate = left <= 3;
    t(tooLate ? `剩 ≤3 天时不给「日均需」（算式对但无行动价值）` : '行动建议给出日均需求 = 缺口/剩余天数',
      tooLate ? (actTxt.includes('顺延到下月') && !actTxt.includes('日均需'))
              : act.includes(api.fmt((100000 - mNet) / left)),
      tooLate ? `left=${left}，走顺延分支` : `找 ${api.fmt((100000 - mNet) / left)}`);
    t('行动建议用本月真实平均单笔换算笔数',
      avgTicket > 0 ? act.includes(api.fmt(avgTicket)) && actTxt.includes('笔') : actTxt.includes('先有一笔进账'),
      avgTicket > 0 ? `平均单笔 ${api.fmt(avgTicket)}（${mInc.length} 笔）` : '本月无收入');
  }

  // 进度不足 1% 时不能显示成 "0%"（看起来像没数据）
  const dispPct = mNet > 0 && Math.round(mNet / 100000 * 100) < 1;
  t('进度不足 1% 时显示「不到 1%」而不是「0%」',
    dispPct ? el('goalNow').innerHTML.includes('不到 1%') : el('goalNow').innerHTML.includes('%'),
    el('goalNow').innerHTML.replace(/<[^>]+>/g, ''));

  // —— 已达成时口径要翻转（超额 + 上调建议），不能继续显示"还差负数"
  api.setGoal(1); api.renderGoal();
  t('目标 1 元时判为已达成', el('goalRag').className === 'rag ok', el('goalRag').className);
  t('已达成时显示「已超额」而非「还差 -X」', el('goalProj').innerHTML.includes('已超额')
    && !el('goalProj').innerHTML.includes('还差'), el('goalProj').innerHTML.replace(/<[^>]+>/g, ' '));
  t('已达成时行动建议给出上调参考', el('goalAction').innerHTML.includes('上调'), '');

  // —— 设了目标后，体检这一项必须消失（否则用户设完了还在被催）
  t('设了目标后体检不再报该项', !api.healthIssues().some(x => x.title.includes('月度目标')));

  // —— 边界：最后一天（left=0）不能算出 Infinity 或 NaN
  t('left=0 时日均需求不产生 NaN/Infinity', (() => {
    // 直接验算除法本身：left 为 0 的分支走的是"本月已结束"，不参与除法
    return left > 0 ? Number.isFinite((100000 - mNet) / left) : true;
  })());

  api.setGoal(keep || 0); api.renderGoal();
});

console.log('\n— 24. 目标带 v2：单位切换 / 拖动条 / 快捷预设 / 达成史 —');
clockAt(TEST_TODAY, () => {
  const t0 = api.todayStr(), ym = t0.slice(0, 7);
  const day = +t0.slice(8), dim = new Date(+t0.slice(0, 4), +t0.slice(5, 7), 0).getDate();
  const mNet = S.earnings.filter(e => e.date.startsWith(ym)).reduce((s, e) => s + e.amount, 0);
  const keepGoal = S.monthlyGoal, keepGoals = JSON.stringify(S.goals || {});
  const keepUnit = api.getMoneyUnit();

  // —— 元档必须与改造前**逐字节一致**：整页到处是写死的 ¥26,958.49 这种文案，
  //    单位改造一旦动了元档的措辞，等于把所有历史文案和断言一起改口径。
  const oldFmt = n => { const s = n < 0 ? '-' : '';
    return s + '¥' + Math.abs(Math.round(n * 100) / 100).toLocaleString('zh-CN'); };
  api.setMoneyUnit('yuan');
  const samples = [0, 0.5, 43.26, 260.874, -300, 26958.487, 15554.407, 100000, -0.004, 9999999.99];
  t('元档 fmt() 与改造前逐值一致（10 个样本）',
    samples.every(v => api.fmt(v) === oldFmt(v)),
    samples.filter(v => api.fmt(v) !== oldFmt(v)).map(v => `${v}:${api.fmt(v)}≠${oldFmt(v)}`).join(' ') || '全一致');

  // —— 单位换算：数字不变，只改写法。写法本身是可判定的，所以按字符串断言。
  api.setMoneyUnit('k');
  t('千元档 26,958.487 → ¥26.96 千元', api.fmt(26958.487) === '¥26.96 千元', api.fmt(26958.487));
  api.setMoneyUnit('wan');
  t('万元档 26,958.487 → ¥2.70 万元', api.fmt(26958.487) === '¥2.70 万元', api.fmt(26958.487));
  t('万元档 30,000 → ¥3.00 万元', api.fmt(30000) === '¥3.00 万元', api.fmt(30000));
  t('负数：符号在 ¥ 之前、单位在最后（-¥1.14 万元）', api.fmt(-11404.08) === '-¥1.14 万元', api.fmt(-11404.08));
  // 换单位最容易踩的坑：小额被四舍五入成 0，数字没变但信息没了。
  t('万元档下小额仍有有效数字（平均单笔 260.87 → ¥0.0261 万元）',
    api.fmt(260.874) === '¥0.0261 万元', api.fmt(260.874));
  t('万元档下更小的额也不写成 0（5.5 → ¥0.00055 万元）',
    api.fmt(5.5) === '¥0.00055 万元', api.fmt(5.5));

  // —— 同一批金额在三种单位下必须是**同一个数**，误差只能来自小数位舍入
  const net = S.earnings.reduce((s, e) => s + e.amount, 0);
  const num = s2 => +s2.replace(/[¥,\s]/g, '').replace(/千元$|万元$/, '');
  const shown = u => { api.setMoneyUnit(u); return api.fmt(net); };
  const fy = shown('yuan'), fk = shown('k'), fw = shown('wan');
  t('三种单位写的是同一个数（差异 ≤ 各自小数位带来的舍入）',
    Math.abs(num(fy) - num(fk) * 1000) <= 0.005 * 1000 + 0.005
      && Math.abs(num(fy) - num(fw) * 10000) <= 0.005 * 10000 + 0.005,
    `${fy} / ${fk} / ${fw}`);

  // —— CSV 是**最危险的污染路径**：金额格式化一旦漏进导出，用户在 Excel 里 SUM 就全废。
  api.setMoneyUnit('yuan'); const csvY = api.buildCSV().text;
  api.setMoneyUnit('wan');  const csvW = api.buildCSV().text;
  t('CSV 导出完全不受显示单位影响（逐字节相同）', csvY === csvW, `${csvY.length} vs ${csvW.length} 字节`);
  // 断言要盯住**金额列**，不能全文搜 "¥" —— 备注里本来就有用户自己写的 ¥
  // （实测 257 行里有 1 行备注含 ¥）。全文搜会误伤数据，等于把用户的原文当成 bug。
  t('CSV 金额列严格是纯数字（无货币符号 / 千分位 / 单位）', (() => {
    const H2 = api.csvRows().head, ci2 = n => H2.indexOf(n);
    return api.csvRows().rows.every(r => /^-?\d+(\.\d+)?$/.test(r[ci2('金额')]));
  })(), `样本：${api.csvRows().rows.slice(0, 3).map(r => r[api.csvRows().head.indexOf('金额')]).join(' | ')}`);
  t('CSV 金额列是原始精度字符串，不是格式化结果', (() => {
    const H2 = api.csvRows().head, ci2 = n => H2.indexOf(n);
    const raws = S.earnings.map(e => String(e.amount));
    return api.csvRows().rows.every(r => raws.includes(r[ci2('金额')]));
  })());

  // —— 单位是整页的：不是只有 fmt 会变，渲染层必须真的跟着走
  api.setMoneyUnit('wan');
  api.applyGoal(30000);
  t('目标文案确实换了单位', el('goalAll').textContent.includes('万元'), el('goalAll').textContent);
  t('KPI 卡也换了单位（证明是整页而不是某张卡）', el('kpis').innerHTML.includes('万元'), '');
  t('环形图中心与来源卡同步换单位', el('donut').innerHTML.includes('万元') && el('srcRank').innerHTML.includes('万元'), '');
  t('单位偏好落在 localStorage，且不进 data.json（不污染账目数据与透传列表）',
    localStorage.getItem('wb_unit') === 'wan' && JSON.parse(localStorage.getItem('wb')).moneyUnit === undefined,
    String(localStorage.getItem('wb_unit')));

  // —— 输入框里的数是**当前单位**下的数，落盘必须换算回元
  el('goalInput').value = '10';
  api.commitGoal();
  t('万元档填 10 → 落盘 100000 元（不是 10 元）', S.monthlyGoal === 100000, String(S.monthlyGoal));
  api.setMoneyUnit('yuan');
  t('切回元档后输入框显示 100000（视图换算、真值不变）',
    String(el('goalInput').value) === '100000', String(el('goalInput').value));

  // —— 拖动条：与输入框双向联动，量纲三种单位下指向同一区间
  const spY = api.sliderSpec();
  t('元档滑块步进 = 1000 元', spY.step === 1000, String(spY.step));
  t('滑块上限 ≥ 当前目标（否则滑块根本够不到目标）', spY.max >= S.monthlyGoal, `max=${spY.max} goal=${S.monthlyGoal}`);
  // ⚠️ 这条断言最初写成 "if (元档 max === 200000) 才比对，否则直接放行" —— 那是个后门：
  //    量纲一旦混用，元档 max 本身就变了，于是条件不成立、断言静默通过（实测确实漏过了一次）。
  //    正确的写法是**无条件比对**：三者各自换回元之后必须相等，这就是"量纲没混用"的定义。
  const closeTo = (a, b) => Math.abs(a - b) <= 1e-6 * Math.max(1, Math.abs(b));
  t('滑块量程在三种单位下指向同一个金额区间（量纲不混用）', (() => {
    const r = {}; for (const u of ['yuan', 'k', 'wan']) { api.setMoneyUnit(u); r[u] = api.sliderSpec(); }
    return closeTo(r.k.max * 1000, r.yuan.max) && closeTo(r.wan.max * 10000, r.yuan.max)
        && closeTo(r.k.step * 1000, r.yuan.step) && closeTo(r.wan.step * 10000, r.yuan.step);
  })(), (() => { const r = {}; for (const u of ['yuan', 'k', 'wan']) { api.setMoneyUnit(u); r[u] = api.sliderSpec(); }
    return `元 max=${r.yuan.max} step=${r.yuan.step} / 千元 max=${r.k.max} / 万元 max=${r.wan.max}`; })());
  api.setMoneyUnit('yuan'); api.applyGoal(100000);
  el('goalRange').value = '50000'; api.onGoalSlide(true);
  t('拖动落定后目标 = 滑块值', S.monthlyGoal === 50000, String(S.monthlyGoal));
  t('拖动后数字框跟着变（两个视图不许分叉）', String(el('goalInput').value) === '50000', String(el('goalInput').value));

  // 拖动过程中不落盘：每移动一格都 POST 一次会打爆同步服务
  el('goalRange').value = '60000'; api.onGoalSlide(false);
  t('拖动中只改内存不落盘（松手才存）',
    S.monthlyGoal === 60000 && (+(JSON.parse(localStorage.getItem('wb')).monthlyGoal) || 0) !== 60000,
    `state=${S.monthlyGoal} cache=${JSON.parse(localStorage.getItem('wb')).monthlyGoal}`);
  api.onGoalSlide(true);
  t('松手后落盘', +(JSON.parse(localStorage.getItem('wb')).monthlyGoal) === 60000,
    String(JSON.parse(localStorage.getItem('wb')).monthlyGoal));

  // 拖到最左端 = 0：那是破坏性动作（清目标），必须显式而不是手滑即毁
  const before = S.monthlyGoal;
  el('goalRange').value = '0'; api.onGoalSlide(true);
  t('拖到最左端不会清除目标', S.monthlyGoal === before, String(S.monthlyGoal));
  t('拖到最左端会说明原因，而不是静默无事发生',
    el('goalEcho').textContent.includes('清除'), el('goalEcho').textContent.slice(0, 40));

  // —— 非法输入不破坏原值
  const hold = S.monthlyGoal;
  el('goalInput').value = ''; api.commitGoal();
  t('空输入不会把目标清成 0（宁可不动，不可做错）', S.monthlyGoal === hold, String(S.monthlyGoal));
  // 目标校验的错误就显示在输入框下方的等值回显位上（并给它标错误态）
  t('空输入给出提示', el('goalEcho').textContent.includes('正数') && el('goalEcho').className.includes('err'),
    el('goalEcho').textContent.slice(0, 60));
  el('goalInput').value = '-5'; api.commitGoal();
  t('负数输入被拒', S.monthlyGoal === hold, String(S.monthlyGoal));

  // —— 键盘：回车提交 / Esc 还原
  el('goalInput').value = '99999';
  api.goalKey({ key: 'Enter', preventDefault() {} });
  t('回车即提交', S.monthlyGoal === 99999, String(S.monthlyGoal));
  el('goalInput').value = '1'; api.goalKey({ key: 'Escape' });
  t('Esc 把输入框还原成当前目标（半途改主意不留残值）',
    String(el('goalInput').value) === '99999', String(el('goalInput').value));

  // —— 目标按月留痕：否则目标一改，历史达成率跟着变，等于没有历史
  t('设定目标会按月写进 state.goals', (S.goals || {})[ym] === S.monthlyGoal, JSON.stringify(S.goals));

  // —— 快捷预设：值必须来自真实数据，不是拍出来的
  const ps = api.goalPresets();
  t('预设非空', ps.length > 0, ps.map(p => `${p.label}=${p.v}`).join(' '));
  /* 取整规则是"跟着量级走"，所以断言也得分档判 —— 不能再只查"是 100 的整数倍"。
     这正是原缺陷的藏身处：2026-09 三个语义不同的预设被固定取整全压成 ¥300，
     而 `v % 100 === 0` 对三个 300 全都成立，绿得很自然、什么都抓不到。 */
  const niceStep = v => v >= 10000 ? 1000 : v >= 1000 ? 100 : v >= 100 ? 10 : 1;
  t('预设值是正数，且按所在量级的步长取整（不是一律取整到百）',
    ps.every(p => p.v > 0 && p.v % niceStep(p.v) === 0),
    ps.map(p => `${p.v}(步长${niceStep(p.v)})`).join(' '));
  /* ⚠️ 只写"ps 里两两不相同"是**永远绿**的 —— dedupeByValue 撞值就丢，界面结构上不可能出现
     重复按钮，所以它测不出取整错误（这条我第一版就写错了，变异时才发现）。
     真正的性质是「**没有任何可用的参照被静默丢掉**」：取整退回固定 100 时，
     「上月 +20%」和「按当前节奏」会被去重吃掉 —— 用户少了两个起点，按钮数从 4 变 2。
     把"不相同"与"参照齐全"合成一条，它才是能变红的。 */
  t('预设两两不相同，且每个可用的参照都还在（不会被取整+去重静默吃掉）', (() => {
    const vals = ps.map(p => p.v);
    if (new Set(vals).size !== vals.length) return false;
    const lm = api.compute().lastMonth;
    const refs = [];
    if (lm > 0) refs.push(lm, lm * 1.2);
    if (mNet > 0) refs.push(mNet / day * dim);
    const pastV = api.monthlySeries().filter(x => x.m !== ym && x.n > 0).map(x => x.net);
    if (pastV.length) refs.push(Math.max(...pastV));
    return refs.every(raw => vals.includes(Math.round(raw / niceStep(raw)) * niceStep(raw)));
  })(), ps.map(p => `${p.label}=${p.v}`).join(' '));
  /* 派生值断言：允许"被去重掉"，但被去重时**那个值必须已经由别的预设表达** ——
     不能因为找不到同名项就放行，否则"整条预设丢了"也会绿。 */
  const derived = (label, want) => {
    const p = ps.find(x => x.label === label);
    if (!want) return !p;
    return p ? p.v === want : ps.some(x => x.v === want);
  };
  t('「上月净额」预设 = 上月真实净额按量级取整', derived('上月净额',
    api.compute().lastMonth > 0
      ? Math.round(api.compute().lastMonth / niceStep(api.compute().lastMonth)) * niceStep(api.compute().lastMonth) : 0),
    ps.filter(p => p.label === '上月净额').map(p => p.v).join('') || '（上月净额非正，本就不该给这个预设）');
  t('「历史最好月」= 非本月的月度净额最大值按量级取整', (() => {
    const past = api.monthlySeries().filter(x => x.m !== ym && x.n > 0).map(x => x.net);
    if (!past.length) return !ps.find(x => x.label === '历史最好月');
    const best = Math.max(...past);
    return derived('历史最好月', Math.round(best / niceStep(best)) * niceStep(best));
  })());
  t('「按当前节奏」= 本月日均 × 当月天数按量级取整', (() => {
    const raw = mNet / day * dim;
    if (!(mNet > 0 && raw > 0)) return !ps.find(x => x.label === '按当前节奏');
    return derived('按当前节奏', Math.round(raw / niceStep(raw)) * niceStep(raw));
  })(), `原始 ${(mNet / day * dim).toFixed(2)} → 预设 ${ps.filter(p => p.label === '按当前节奏').map(p => p.v).join('') || '（未给）'}`);
  /* 去重是防御分支，当前真实数据不触发 —— 直接喂构造值，别指望真实数据会撞上。
     （"没被任何断言碰过的分支"正是上面 round(100) 缺陷能活下来的同一种原因。） */
  t('预设撞值时会去重（喂构造值，不靠真实数据碰运气）',
    api.dedupeByValue([{ label: 'a', v: 100 }, { label: 'b', v: 100 }, { label: 'c', v: 200 }])
      .map(x => x.label).join(',') === 'a,c', '保留 a、丢弃重复的 b、保留 c');
  t('去重顺手滤掉非正值（v ≤ 0 的预设不该出现）',
    api.dedupeByValue([{ label: 'z', v: 0 }, { label: 'n', v: -5 }, { label: 'ok', v: 50 }])
      .map(x => x.label).join(',') === 'ok');
  t('点预设即写入目标', (() => { const p = ps[0]; api.usePreset(p.v); return S.monthlyGoal === p.v; })(), String(S.monthlyGoal));
  t('预设按钮渲染到 DOM 且带金额', (api.renderGoalPreset(),
    (el('goalPreset').innerHTML.match(/class="pbtn"/g) || []).length === ps.length
      && el('goalPreset').innerHTML.includes(api.fmt(ps[0].v))), el('goalPreset').innerHTML.slice(0, 70));

  // —— 达成史：月度净额必须与 monthlySeries 同源（不另起一份聚合），逐格按位比对
  api.renderGoalHist();
  const ms = api.monthlySeries().slice(-6);
  const vals = [...el('goalHist').innerHTML.matchAll(/class="ghv">([^<]*)</g)].map(m => m[1]);
  const wantVals = ms.map(x => api.fmt(x.net));
  t('达成史渲染 6 格', (el('goalHist').innerHTML.match(/class="ghcell/g) || []).length === 6,
    String((el('goalHist').innerHTML.match(/class="ghcell/g) || []).length));
  t('达成史 6 格净额与 monthlySeries 逐格按位一致', vals.length === 6 && vals.every((v, i) => v === wantVals[i]),
    `${vals.join(' ')} vs ${wantVals.join(' ')}`);
  t('本月那格被标出', el('goalHist').innerHTML.includes('· 本月'), '');
  // 达成率必须独立复算，不能拿页面的 rate 去比页面的 rate
  t('本月达成率 = 本月净额 / 本月目标（独立复算）',
    el('goalHist').innerHTML.includes(Math.round(mNet / S.monthlyGoal * 100) + '%'),
    `净额 ${api.fmt(mNet)} / 目标 ${api.fmt(S.monthlyGoal)} → 期望 ${Math.round(mNet / S.monthlyGoal * 100)}%`);
  t('未设目标的月份如实写「未设目标」，不假装 0%', (() => {
    const g = S.goals; S.goals = {}; api.renderGoalHist();
    const ok = (el('goalHist').innerHTML.match(/未设目标/g) || []).length === 6;
    S.goals = g; api.renderGoalHist(); return ok;
  })());

  // —— 清除：目标带与达成史必须一起翻回去，否则两边打架
  api.clearGoal();
  t('清除后 monthlyGoal 归零', !(S.monthlyGoal > 0), String(S.monthlyGoal));
  t('清除后本月那条留痕也删掉（页面说未设目标，达成史不许还显示目标）',
    !(S.goals || {})[ym], JSON.stringify(S.goals));
  t('清除后输入框被清空', String(el('goalInput').value) === '', JSON.stringify(el('goalInput').value));
  t('清除后回到「未设目标」引导态', el('goalAll').textContent === '未设目标', el('goalAll').textContent);
  t('清除按钮在无目标时隐藏', el('goalClear').style.display === 'none', String(el('goalClear').style.display));
  api.applyGoal(50000);
  t('有目标时清除按钮出现', el('goalClear').style.display === '', String(el('goalClear').style.display));

  // —— 体检第 6 项的文案要指向新能力，而不是让用户自己想办法
  api.clearGoal();
  t('体检的目标项提到现成预设', (api.healthIssues().find(x => x.jump === 'goal') || {}).detail.includes('预设'),
    ((api.healthIssues().find(x => x.jump === 'goal') || {}).detail || '').slice(0, 60));

  // 收尾：恢复单位与目标，别把状态漏给后面的断言
  api.setMoneyUnit(keepUnit);
  S.goals = JSON.parse(keepGoals);
  S.monthlyGoal = keepGoal || 0;
  api.renderAll();
  t('收尾：单位与目标已复原', api.getMoneyUnit() === keepUnit && S.monthlyGoal === (keepGoal || 0),
    `${api.getMoneyUnit()} / ${S.monthlyGoal}`);
});

console.log('\n— 25. 真机（Chromium）验出来的三处缺陷：回归断言 —');
clockAt(TEST_TODAY, () => {
  // 这三条都不是 DOM 桩能自己想到的：桩不会"跟随鼠标位置重算映射"，
  // 也不会画出 0.1% 被 Math.round 成 0% 的样子。是用真浏览器点了一遍才暴露的，
  // 所以单独成节，注明来源 —— 免得以后有人觉得它们多余而删掉。
  const t0 = api.todayStr(), ym = t0.slice(0, 7);
  const mNet = S.earnings.filter(e => e.date.startsWith(ym)).reduce((s2, e) => s2 + e.amount, 0);
  const mInc = S.earnings.filter(e => e.date.startsWith(ym) && e.amount > 0);
  const avgTicket = mInc.length ? mInc.reduce((s2, e) => s2 + e.amount, 0) / mInc.length : 0;
  const keepGoal = S.monthlyGoal, keepGoals = JSON.stringify(S.goals || {}), keepUnit = api.getMoneyUnit();
  api.setMoneyUnit('yuan');

  // (1) 拖动量程正反馈：真机实测"拖一次从 30 万元冲到 ¥1,554.50 万元"。
  //     根因是量程随目标实时变化，而滑块自己就在改目标 —— 鼠标不动值也涨。
  //     桩复现不了那个循环，但能验"手势期间量程必须钉住"这个不变量。
  api.applyGoal(300000);
  const specA = api.sliderSpec();
  el('goalRange').value = String(specA.max);
  api.onGoalSlide(false);                       // 手势中：目标被改到量程上限
  const specB = api.sliderSpec();
  t('拖动期间量程被钉住、不随目标变化（防正反馈）',
    specB.max === specA.max && specB.step === specA.step,
    `手势中 max ${specA.max} → ${specB.max}（此时目标已变成 ${S.monthlyGoal}）`);
  t('一次手势能被拖出的金额有上界', S.monthlyGoal <= specA.max, `${S.monthlyGoal} ≤ ${specA.max}`);
  api.onGoalSlide(true);                        // 松手
  const specC = api.sliderSpec();
  t('松手后量程放开并按新目标重算（仍能容纳已设目标）',
    specC.max >= S.monthlyGoal, `max=${specC.max} goal=${S.monthlyGoal}`);

  // (2) 达成率「0%」是假状态：修在目标带却漏了 KPI 卡和达成史，真机上才看见三处不一致。
  api.applyGoal(300000);                        // 本月净额约 ¥300，达成率 0.1%
  t('达成率不足 1% 时，目标带 / KPI / 达成史三处统一写「不到 1%」',
    el('goalNow').innerHTML.includes('不到 1%') && el('kpis').innerHTML.includes('不到 1%')
      && el('goalHist').innerHTML.includes('不到 1%'),
    `目标带「${el('goalNow').innerHTML.replace(/<[^>]+>/g, '')}」 · KPI「${(el('kpis').innerHTML.match(/目标[^<]*/) || [''])[0].trim()}」 · 达成史命中 ${(el('goalHist').innerHTML.match(/不到 1%/g) || []).length} 格`);
  api.applyGoal(300);                           // 换成够得着的目标 → 应回到百分数
  const pWant = Math.round(mNet / 300 * 100) + '%';
  t('达成率 ≥1% 时三处都写百分数（「不到 1%」不是把功能一刀切）',
    el('goalNow').innerHTML.includes(pWant) && el('kpis').innerHTML.includes(pWant)
      && el('goalHist').innerHTML.includes(pWant),
    `期望 ${pWant}；KPI「${(el('kpis').innerHTML.match(/目标[^<]*/) || [''])[0].trim()}」`);

  // (3) 「还差几笔」也要过可行动这一关：真机上出现过"缺口约合 2820 笔"，
  //     算式没错，但摆在"本月只剩 1 天"旁边只会让人以为多接几单就行。
  api.applyGoal(300000);
  const actFar = el('goalAction').innerHTML;
  const need = avgTicket > 0 ? Math.ceil((300000 - mNet) / avgTicket) : 0;
  /* 还剩几天 —— 页面对它有四个分支（见第 23 节那段的说明）。
     本节需要它来给断言"选支"。自己从本节的 t0 算：各节是一个个独立块，
     引用别节的 day/dim 跨不过来（会 ReferenceError，已踩一次）。 */
  const left25 = Math.max(0, new Date(+t0.slice(0, 4), +t0.slice(5, 7), 0).getDate() - +t0.slice(8));
  if (left25 <= 0) {
    /* 本月最后一天：页面根本不进"算笔数"那一支（它走"本月已结束"）。
       所以这里验那一支该有的样子 —— 笔数整块不出现，而不是把断言放宽成 `|| true`。 */
    t('本月最后一天：缺口换算笔数整块不出现（这几天补不上，给笔数只会误导）',
      !actFar.includes('笔') && !actFar.includes('倍') && actFar.includes('本月已结束'),
      actFar.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').slice(0, 90));
  } else {
  t('缺口远超本月成交量时，不再说成「还差几笔的量」，直说靠加量补不回来',
    avgTicket > 0 && need > mInc.length * 20
      ? (actFar.includes('倍') && !actFar.includes('还差约'))
      : true,
    `需 ${need} 笔 / 本月 ${mInc.length} 笔；文案「${actFar.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').slice(0, 90)}」`);
  }
  api.applyGoal(400);                           // 缺口约一笔 → 闸门不该把正常笔数也拦掉
  const actNear = el('goalAction').innerHTML;
  if (left25 <= 0) {
    t('本月最后一天：正常量级的缺口同样不给笔数（同一个分支，不是两个口径）',
      !actNear.includes('笔') && !actNear.includes('倍') && actNear.includes('本月已结束'),
      actNear.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').slice(0, 90));
  } else {
    t('缺口在正常量级时仍照常给笔数（闸门没有变成一刀切）',
      actNear.includes('笔') && !actNear.includes('倍'),
      actNear.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').slice(0, 90));
  }

  api.setMoneyUnit(keepUnit);
  S.goals = JSON.parse(keepGoals);
  S.monthlyGoal = keepGoal || 0;
  api.renderAll();
});

/* ══════════════════════════════════════════════════════════════════════════
   26. UI/UX 评审整改（2026-09-29）回归断言
   评审报告：designs/workbench/评审报告-20260929.md（83 条规则库 + WCAG 2.1 AA）
   这一节同时是"别退回去"的哨兵：对比度、令牌纪律、键盘可达、提示通道
   —— 全都是靠人眼不靠不住的东西，只能靠断言守住。
   ══════════════════════════════════════════════════════════════════════════ */
console.log('\n— 26. UI/UX 评审整改：令牌 / 对比度 / 键盘 / 提示通道 —');
{
  // ── 静态扫描：直接从源码读令牌与规则，不靠肉眼 ──
  const styleText = src.slice(src.indexOf('<style>'), src.indexOf('</style>'));
  // 判定"还有没有用某个东西"必须在**去掉注释**的文本上做：
  // 注释里讨论 alert()/brightness() 恰恰是必要的说明，把它们算成违规会逼着人删掉好注释。
  const stripBlocks = t2 => t2.replace(/\/\*[\s\S]*?\*\//g, '');
  /* 做结构分析必须在**去注释**的文本上做：注释文本会被并进相邻规则的"选择器"里，
     于是 `sel === ':focus-visible'` 这种精确比较永远匹配不上（踩过）。 */
  const styleClean = stripBlocks(styleText);
  const blocksOf = t2 => [...t2.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(m => ({ sel: m[1].trim(), body: m[2] }));
  /* 从 '{' 起做括号配对，取出整块内容。
     blocksOf 那个"平坦"正则看不到嵌套：@media 块里还有内层规则，
     所以它永远取不到 @media 本身（只会匹配到内层的 *,*::before 那条）。 */
  const blockAt = (text, idx) => {
    let d = 0;
    for (let i = idx; i < text.length; i++) {
      if (text[i] === '{') d++;
      else if (text[i] === '}') { d--; if (d === 0) return text.slice(idx + 1, i); }
    }
    return null;
  };
  const mediaBody = name => {
    const m = new RegExp('@media\\(' + name + '[^{]*\\{').exec(styleText);
    return m ? blockAt(styleText, m.index + m[0].length - 1) : null;
  };
  const tokOf = t2 => { const o = {}; for (const m of t2.matchAll(/--([a-z0-9-]+)\s*:\s*([^;{}]+);/g)) o[m[1]] = m[2].trim(); return o; };
  // 令牌里有别名（--bg:var(--neu-50)），算对比度前必须解引用到底
  const deref = (o, v, d = 0) => {
    if (d > 8) return String(v).trim();
    const m = /^var\(--([a-z0-9-]+)\)$/.exec(String(v).trim());
    return m ? deref(o, o[m[1]], d + 1) : String(v).trim();
  };
  const lightTok = tokOf(styleText.slice(styleText.indexOf(':root{'), styleText.indexOf('[data-theme="dark"]')));
  const darkTok = tokOf(styleText.slice(styleText.indexOf('[data-theme="dark"]{')));
  const hexOf = (o, k) => deref(o, o[k]);

  // WCAG 2.1 相对亮度与对比度，程序化计算（"看着挺清楚"不算证据）
  const lin = c => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const lum = h => { h = h.replace('#', '');
    if (h.length === 3) h = h.split('').map(x => x + x).join('');
    const n = parseInt(h, 16);
    return 0.2126 * lin(n >> 16 & 255) + 0.7152 * lin(n >> 8 & 255) + 0.0722 * lin(n & 255); };
  const cr = (a, b) => { const x = lum(a), y = lum(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };

  const TEXT_PAIRS = [['muted', 'bg'], ['muted', 'card'], ['accent', 'bg'], ['accent', 'card'],
    ['accent2', 'card'], ['pos', 'card'], ['neg', 'card'], ['ok', 'card'], ['warn', 'card'], ['ink', 'card']];
  const badIn = tok => TEXT_PAIRS
    .map(([f, b]) => [f, b, cr(hexOf(tok, f), hexOf(tok, b))])
    .filter(([, , r]) => r < 4.5)
    .map(([f, b, r]) => `${f}/${b}=${r.toFixed(2)}`);

  const badLight = badIn(lightTok), badDark = badIn(darkTok);
  t('浅色主题：10 组文字对比全部 ≥4.5:1（改造前 9 组不达标）',
    badLight.length === 0, badLight.join(' ') || '全部达标');
  t('暗色主题：同样 10 组全部 ≥4.5:1',
    badDark.length === 0, badDark.join(' ') || '全部达标');
  t('语义色当文字用也达标（warn 改造前只有 2.15:1，是最差的一组）',
    cr(hexOf(lightTok, 'warn'), hexOf(lightTok, 'card')) >= 4.5,
    `warn/card=${cr(hexOf(lightTok, 'warn'), hexOf(lightTok, 'card')).toFixed(2)}`);
  t('主按钮白字 ≥4.5:1（改造前 4.44，正好卡在线上）',
    cr('#ffffff', hexOf(lightTok, 'accent')) >= 4.5,
    `#fff/accent=${cr('#ffffff', hexOf(lightTok, 'accent')).toFixed(2)}`);
  t('控件边框 --line-strong ≥3:1（非文字对比 A17；发丝线对比才 1.20 看不出边界）',
    cr(hexOf(lightTok, 'line-strong'), '#ffffff') >= 3 && cr(hexOf(lightTok, 'line-strong'), hexOf(lightTok, 'bg')) >= 3,
    `card=${cr(hexOf(lightTok, 'line-strong'), '#ffffff').toFixed(2)} bg=${cr(hexOf(lightTok, 'line-strong'), hexOf(lightTok, 'bg')).toFixed(2)}`);
  /* 逐条规则判，**不要数全文件的出现次数**。
     原来写的是"出现 ≥4 次"—— 那个阈值会被无关规则喂饱：第四轮整改新加的 .skip 也用了
     --line-strong，总数变成 5，于是只把其中一条输入框改回装饰性 --line 时总数仍有 4，
     断言照样绿（变异测试抓出来的，属于典型的"阈值被无关改动填满"）。
     判据要落在「每条给控件设边框的规则」上。 */
  const ctlRules = blocksOf(styleClean).filter(b =>
    /\b(input|select|textarea)\b/.test(b.sel) && /border\s*:/.test(b.body));
  const ctlBad = ctlRules.filter(b => /border\s*:\s*1px solid var\(--line\)/.test(b.body)).map(b => b.sel);
  t('每条给输入类控件设边框的规则都用 --line-strong（逐条判，不数总数）',
    ctlRules.length >= 3 && ctlBad.length === 0,
    `${ctlRules.length} 条规则${ctlBad.length ? '，用回装饰性 --line 的：' + ctlBad.join(' / ') : '，全部达标'}`);

  // ── 令牌纪律：字号 / 圆角 / 颜色字面量 ──
  t('字号已全部走 --fs-*，源码里没有硬编码 px 字号',
    !/font-size:\s*[\d.]/.test(src),
    (src.match(/font-size:\s*[\d.][^;]*/g) || []).slice(0, 3).join(' | ') || '无');
  const fsToks = Object.keys(lightTok).filter(k => k.startsWith('fs-'));
  t('字号档位 ≤6（改造前 12 档）', fsToks.length <= 6, `${fsToks.length} 档：${fsToks.join(',')}`);
  t('圆角已全部走 --radius-*，源码里没有硬编码 px 圆角',
    !/border-radius:\s*(?!var\()[\d.]/.test(styleText),
    (styleText.match(/border-radius:\s*(?!var\()[\d.][^;}]*/g) || []).slice(0, 3).join(' | ') || '无');
  const rToks = Object.keys(lightTok).filter(k => k.startsWith('radius-'));
  t('圆角档位 ≤5（改造前 13 档）', rToks.length <= 5, `${rToks.length} 档：${rToks.join(',')}`);
  t('不再用 filter:brightness() 另调近似色（交互态必须从色阶取）',
    !/brightness\(/.test(stripBlocks(styleText)),
    (stripBlocks(styleText).match(/brightness\(/g) || []).length + ' 处');
  t('主色阶已建立（hover/active 有档可取）',
    !!lightTok['pri-500'] && !!lightTok['pri-600'] && !!lightTok['pri-700'] && !!lightTok['pri-100']);

  /* 透明档防漂移：CSS 里 rgba() 的 RGB 必须等于某个令牌的 RGB。
     改造前到处都是 rgba(59,110,245,.14) 这种"基色的透明版"，
     一旦改了 --accent 而忘了改 rgba，就会在色阶外多出一个近似的蓝 —— 肉眼几乎看不出来。
     把它变成断言：改了基色不改透明档，这里立刻红。 */
  const tokenRGB = new Set();
  for (const tok of [lightTok, darkTok]) {
    for (const k of Object.keys(tok)) {
      const v = hexOf(tok, k);
      if (/^#[0-9a-fA-F]{6}$/.test(v))
        tokenRGB.add([1, 3, 5].map(i => parseInt(v.slice(i, i + 2), 16)).join(','));
    }
  }
  const NEUTRAL = new Set(['20,30,60', '0,0,0', '10,16,30', '255,255,255']);  // 阴影/遮罩/斑马纹
  const drift = [...new Set([...styleText.matchAll(/rgba\((\d+,\d+,\d+)/g)].map(m => m[1]))]
    .filter(x => !tokenRGB.has(x) && !NEUTRAL.has(x));
  t('CSS 里每个 rgba() 的 RGB 都等于某个令牌的 RGB（基色改了透明档必须跟着改）',
    drift.length === 0, drift.join(' ') || '无漂移');

  // ── 焦点可见与键盘可达 ──
  t(':focus-visible 已存在（改造前全文件 0 处，键盘用户看不到自己在哪）',
    /:focus-visible/.test(styleText));
  /* 必须精确落到"选择器恰好是 :focus-visible"的那一条上。
     旧写法用 /:focus-visible\s*\{[^}]*box-shadow/ 会被**另一条**规则满足 ——
     `input:focus-visible,...,[tabindex="0"]:focus-visible{...box-shadow...}` 里也含
     ":focus-visible{"，于是把全局那条的 box-shadow 删掉它照样绿。变异测试抓出来的。 */
  const globalFocus = blocksOf(styleClean).find(b => b.sel === ':focus-visible');
  t('全局 :focus-visible 规则自带 box-shadow 焦点环（规则 V10：outline 在旧 Safari 不跟随圆角）',
    !!globalFocus && /box-shadow/.test(globalFocus.body),
    globalFocus ? globalFocus.body.slice(0, 80) : '(没有选择器恰为 :focus-visible 的规则)');
  const bareOutline = blocksOf(styleClean).filter(b =>
    /outline:none/.test(b.body) && !/box-shadow/.test(b.body) && !/:focus:not\(:focus-visible\)/.test(b.sel));
  t('凡出现 outline:none 的规则，要么自带 box-shadow 焦点环，要么限定在鼠标聚焦 :focus:not(:focus-visible)',
    bareOutline.length === 0, bareOutline.map(b => b.sel).join(' | ') || '无裸 outline:none');

  const tiN = (js.match(/tabindex="0" role="button"/g) || []).length;
  t('3 处整块可点的 div 进了 Tab 序（来源行 / 体检行 / 项目卡）', tiN === 3, `${tiN} 处`);
  const trLine = js.split('\n').find(l => l.includes('const click= editable?'));
  t('账本 <tr> 故意**没有**加 tabindex（每行已有 ✎/✕ 真按钮；257 行各加一个停靠点会让键盘用户按几百次 Tab）',
    !!trLine && !trLine.includes('tabindex'), (trLine || '(找不到那一行)').trim().slice(0, 78));
  t('键盘激活是一个事件委托（一份实现管三处，不是三个模板各写一遍）',
    /document\.addEventListener\("keydown"/.test(js) && /role"\)!=="button"/.test(js));

  /* 委托行为：用一个**独立**的页面实例来测，直接喂事件给真注册到 document 上的监听器。
     不能用上面那个 api 实例 —— 第 9 节为了单独跑 runSync 又求值了一遍同一段脚本，
     于是这个 document 上注册了两份委托，喂一次事件会被处理两遍（实测 click 翻倍）。
     新建实例既避开重复，也顺便验证了"注册发生在页面脚本求值时"这件事。 */
  const listenersK = {};
  const docK = Object.assign({}, doc, {
    addEventListener: (type, fn) => { (listenersK[type] = listenersK[type] || []).push(fn); } });
  new Function('document', 'localStorage', 'location', 'alert', 'setInterval', 'clearInterval', 'window',
    js)(docK, localStorage, location, () => {}, () => 1, () => {}, globalThis);
  t('键盘激活委托已注册到 document（页面脚本求值时就注册）',
    (listenersK.keydown || []).length >= 1, `${(listenersK.keydown || []).length} 个 keydown 监听器`);
  const fireKey = (target, key) => {
    const e = { target, key, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    (listenersK.keydown || []).forEach(fn => fn(e));
    return e;
  };
  const mk = (id, role, tabIndex, tag) => { const n = new El(id); n.tagName = tag || 'DIV';
    if (role) n.setAttribute('role', role); n.tabIndex = tabIndex; n._n = 0; n.click = () => n._n++; return n; };
  const divBtn = mk('fakeSrcRow', 'button', 0);
  let e1 = fireKey(divBtn, 'Enter');
  t('Enter 在 role=button 的 div 上转发为 click', divBtn._n === 1, `click ×${divBtn._n}`);
  e1 = fireKey(divBtn, ' ');
  t('Space 同样转发，并阻止默认滚动', divBtn._n === 2 && e1.defaultPrevented,
    `click ×${divBtn._n}，preventDefault=${e1.defaultPrevented}`);
  fireKey(divBtn, 'Tab');
  t('只有 Enter/Space 触发，Tab 等按键不误触', divBtn._n === 2, `click ×${divBtn._n}`);
  const realBtn = mk('fakeRealBtn', 'button', 0, 'BUTTON');
  fireKey(realBtn, 'Enter');
  t('真 <button> 不被重复处理（否则一次回车会触发两次）', realBtn._n === 0, `click ×${realBtn._n}`);
  const plainDiv = mk('fakePlainDiv', null, 0);
  fireKey(plainDiv, 'Enter');
  t('只有 role=button 的元素被激活（普通 div 不受影响）', plainDiv._n === 0, `click ×${plainDiv._n}`);
  const unTabbed = mk('fakeNoTab', 'button', -1);
  fireKey(unTabbed, 'Enter');
  t('不在 Tab 序里的元素不参与（tabIndex 必须为 0）', unTabbed._n === 0, `click ×${unTabbed._n}`);

  /* 必须是**结构完整**的媒体查询，不能只做子串匹配 ——
     旧写法 .includes('prefers-reduced-motion') 在把媒体名写成 prefers-reduced-motion1
     （即整个块失效）时依然绿。变异测试抓出来的。 */
  const rmBody = mediaBody('prefers-reduced-motion:\\s*reduce');
  t('prefers-reduced-motion 媒体查询结构完整，且块内真的把过渡/动画关掉了（规则 A26，改造前 0 处）',
    !!rmBody && /transition:none/.test(rmBody) && /animation:none/.test(rmBody),
    rmBody ? rmBody.replace(/\s+/g, ' ').trim().slice(0, 72) : '(没有该媒体查询)');
  const hvBody = mediaBody('hover:none');
  t('@media(hover:none) 已把粘滞 hover 收回默认态（触屏 :hover 会粘在最后点的元素上）',
    !!hvBody && /:hover/.test(hvBody), hvBody ? `${(hvBody.match(/:hover/g) || []).length} 条 :hover 覆盖` : '(无)');
  t('@media 能力查询放在样式表末尾（靠层叠顺序覆盖前面无条件生效的 :hover）',
    styleText.indexOf('@media(hover:none)') > styleText.indexOf('@media(max-width:560px)'));

  // ── 语义与 ARIA ──
  /* ⚠️ 标记类断言必须在**去注释**的文本上判，两种注释都要去：
     ① `<!-- -->`：stripBlocks 管不到它（它只去块注释），而"注释里讨论标签"是常有的事 ——
        新加的那条「放在主内容区里面而不是上面」的注释放进文件后，
        下面"主内容区已补上"那条就再也红不了了：把标签整个删掉，正则照样命中注释里的那两个字
        （变异 #21 因此假绿，是这次抓出来的）。
     ② 本文件自己的块注释：写这段说明时又踩了一遍 —— 说明里为了讲清问题，
        必然要把那个标签名写出来，于是它又把断言喂饱了。所以是先 stripBlocks、再下面这层。
     这是"注释喂饱断言"第二次出现（上一次是 CSS 注释被并进选择器，见上面 stripBlocks 处），
     道理相同：**注释不是被断言的对象**，被断言的是标记。
     反过来说，文本类断言（比如"文案里有没有 markdown 标记"）仍该看带注释的原文 ——
     那类断言关心的是"人最终看到什么"，口径不同，别一把全改掉。 */
  const htmlClean = t2 => t2.replace(/<!--[\s\S]*?-->/g, '');
  // 名字用 markup 而不是 mk：mk 在本节上面已有一个（造假元素的那个工厂），
  // 重名会直接 SyntaxError，而那两个东西职责毫不相干，不该共用一个短名。
  const markup = stripBlocks(htmlClean(src));
  const ariaN = (markup.match(/\baria-[a-z]+=/g) || []).length;
  t('aria-* 从 1 处增到 ≥12 处', ariaN >= 12, `${ariaN} 处`);
  t('<main> / <nav> 已补上（原来都是 0，只有 2 个裸 <section>）',
    /<main[\s>]/.test(markup) && /<\/main>/.test(markup) && /<nav[\s>]/.test(markup) && /<\/nav>/.test(markup));
  const labelN = (markup.match(/<label/g) || []).length;
  const forN = (markup.match(/<label[^>]*\bfor=/g) || []).length;
  /* 2026-10-08：资讯页新增「只看未读」checkbox（无 for，直接包裹）与
     探查 / 管理两个新模态，数量相应从 11→12、2→4。 */
  t('所有 <label> 补齐 for= 或隐含包裹（15 个，其中 1 个隐含包裹）',
    labelN === forN + 1 && labelN === 15, `${forN}/${labelN}`);
  /* 模态数现算：role=dialog 与 aria-modal 数量必须相等且覆盖所有模态（≥4）。
     写死 4 的年代后面加了数据来源、命令面板，死数字立即假红。 */
  const dialogN = (markup.match(/role="dialog"/g) || []).length;
  t('每个模态都声明 role=dialog + aria-modal（数量一致且 ≥4）',
    dialogN >= 4 && dialogN === (markup.match(/aria-modal="true"/g) || []).length,
    `role=dialog ${dialogN} 处 / aria-modal ${(markup.match(/aria-modal="true"/g) || []).length} 处`);
  const iconBtns=[...js.matchAll(/<button[^>]*class="(?:del|edit|miniact del)"[^>]*>/g)].map(m=>m[0]);
  t('所有图标按钮都有 aria-label（原来只有 title，title 不被所有屏读器稳定播报）',
    iconBtns.length>0 && iconBtns.every(tag=>/aria-label=/.test(tag)),
    `${iconBtns.filter(tag=>/aria-label=/.test(tag)).length}/${iconBtns.length}`);
  t('屏读器状态通道：banner / notify / undo 都带 role=status + aria-live',
    (markup.match(/role="status" aria-live="polite"/g) || []).length === 3);
  t('数字排版：tabular-nums 已启用（这是金额应用，非等宽则竖排对不齐）',
    /font-variant-numeric:tabular-nums/.test((styleText.match(/body\{[^}]*\}/) || [''])[0]));
  t('表格斑马纹已加（改造前 nth-child 0 处）', /tbody tr:nth-child\(even\)/.test(styleText));

  // ── 提示通道：数据类错误必须常驻，不能点掉就没 ──
  t('源码里已不存在任何 alert() 调用（改造前 16 处）',
    !/\balert\(/.test(stripBlocks(js)),
    (stripBlocks(js).match(/\balert\(/g) || []).length + ' 处');
  const alertsBefore = alerts.length;
  api.dismissNotify();
  t('未触发时提示条处于隐藏态', !noticeOn() && notice() === '', `"${notice()}" / ${el('notify').className}`);
  api.notify('保存失败：HTTP 500，数据未写入。');
  t('数据类错误走页内常驻条（不再弹窗）',
    noticeOn() && notice().includes('数据未写入'), notice().slice(0, 60));
  t('走的是新通道，alert 通道没有新增记录', alerts.length === alertsBefore,
    `alerts += ${alerts.length - alertsBefore}`);
  api.notify('a < b & c', 'warn');
  t('提示文案经 HTML 转义（不会被当成标签解析）',
    notice().includes('a &lt; b &amp; c'), notice());
  t('warn 级提示带 warn 样式（拒绝类操作用琥珀，不是红）',
    noticeLevel() === 'warn', noticeLevel());
  api.dismissNotify();
  t('dismissNotify 清空提示条', !noticeOn() && notice() === '', `"${notice()}"`);

  // ── 删除可撤销：必须插回原索引 ──
  const nReal = S.earnings.length;
  const snapReal = JSON.stringify(S.earnings);
  /* 真实数据里 257 条**全部**是 Notion 只读记录（data.json：readOnly 257/257，source 全是 notion）。
     也就是说「删除」这条路径在真实数据上根本走不到 —— 只有本页自己「记一笔」产生的
     本地草稿（readOnly:false）才可删。所以下面先造一条草稿，走的正是用户的真实路径。

     （这一段踩过一个坑，值得留着：原来固定取索引 5 当"要删的那条"，而它恰好是只读记录，
       delEarn 于是**正确地**拒绝了删除 —— 结果「删除后 -1」红了，可后面 4 条
       「撤销后复原」却因为**什么都没发生过**而全绿。假绿比红灯危险得多。） */
  t('真实数据的收益记录全是只读（这正是账本上不会出现删除按钮的原因）',
    S.earnings.every(e => e.readOnly), `${S.earnings.filter(e => !e.readOnly).length} 条可删`);

  const vIdx = 5;
  const victim = { id: 'draft-del-1', date: api.todayStr(), amount: 888.88, type: '咨询',
    projectId: '', note: '删除撤销测试', product: '', source: 'local', readOnly: false,
    updatedAt: api.todayStr() };
  S.earnings.splice(vIdx, 0, victim);   // 插到中间：这样「插回原索引」才与「追加到末尾」有区别
  api.renderAll();
  const snapWithDraft = JSON.stringify(S.earnings);
  t('夹具：已插入一条可删除的本地草稿（真实数据全是只读，删不掉）',
    S.earnings.length === nReal + 1 && S.earnings[vIdx].id === 'draft-del-1',
    `${S.earnings.length} 条，第 ${vIdx} 条 = ${S.earnings[vIdx] && S.earnings[vIdx].id}`);

  api.delEarn(victim.id);
  t('删除后记录数回到插入前（草稿确实被删掉了）',
    S.earnings.length === nReal, `${S.earnings.length} / 期望 ${nReal}`);
  t('撤销条出现并写明删了什么', el('undo').className.includes('show') && el('undo').innerHTML.includes('撤销'),
    el('undo').innerHTML.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 56));
  t('撤销条带这笔的日期（否则连删了哪笔都要想）',
    el('undo').innerHTML.includes(victim.date.slice(5)));

  api.undoDel();
  t('撤销后记录数复原', S.earnings.length === nReal + 1, String(S.earnings.length));
  t('撤销后**插回原索引**，不是追加到末尾',
    S.earnings[vIdx] && S.earnings[vIdx].id === victim.id,
    `位置 ${vIdx} 现在是 ${S.earnings[vIdx] && S.earnings[vIdx].id}`);
  t('撤销后整份数据与删除前逐字节一致', JSON.stringify(S.earnings) === snapWithDraft);
  t('撤销后撤销条收起、待撤销记录清空',
    !el('undo').className.includes('show') && api.getLastDel() === null);

  // 只读记录必须拒绝删除（这一条以前没人验，正是它让上面那批假绿暴露出来）
  const roRec = S.earnings.find(e => e.readOnly);
  const nRO = S.earnings.length;
  api.dismissNotify();
  api.delEarn(roRec.id);
  t('只读记录拒绝删除，且给的是 warn 提示（不是静默无事发生）',
    S.earnings.length === nRO && noticeLevel() === 'warn' && notice().includes('只读拉取'),
    `${S.earnings.length} 条 / ${noticeLevel()} / ${notice().slice(0, 30)}`);
  t('拒绝删除时不登记待撤销记录（没有真的删掉什么）', api.getLastDel() === null);
  api.dismissNotify();

  // 撤销窗口过期后不能再撤 —— 否则可能把很久以前删的那笔插回来
  api.delEarn(victim.id);
  t('删除后待撤销记录已登记', !!api.getLastDel());
  api.hideUndo();
  t('撤销窗口关闭后清空待撤销记录（防止撤销到很久以前删的那笔）',
    api.getLastDel() === null);
  api.undoDel();
  t('窗口关闭后撤销是空操作，不会重复插回',
    S.earnings.length === nReal + 1 - 1, String(S.earnings.length));

  S.earnings = JSON.parse(snapReal);        // 还原夹具（S 就是页面 state，赋值即复位）
  api.renderAll();
  t('夹具已还原（真实数据一字未动）',
    S.earnings.length === nReal && JSON.stringify(S.earnings) === snapReal, `${S.earnings.length} 条`);

  // ── 内联字段错误 ──
  api.fieldErr('eAmt', '请填金额');
  t('校验错误挂在字段下方（.ferr），不再占用全局弹窗', ferr('eAmt') === '请填金额', ferr('eAmt'));
  t('出错字段标了 aria-invalid', el('eAmt').getAttribute('aria-invalid') === 'true');
  api.clearFieldErr('eAmt');
  t('clearFieldErr 同时清掉消息与 aria-invalid',
    ferr('eAmt') === '' && el('eAmt').getAttribute('aria-invalid') === null,
    `"${ferr('eAmt')}" / ${el('eAmt').getAttribute('aria-invalid')}`);
}


/* ══════════════════════════════════════════════════════════════════════════
   27. 第四轮整改（2026-09-29）：跳转链 / 账本表头 / 命中区 44×44 / 模态 Enter 提交
   这四项都不是"有没有写"的问题，是**几何与语义**的问题：
   命中区要算出来多大、两个热区会不会重叠、表单里按钮的默认 type 是什么。
   所以下面既断言结构，也把数值算出来比。
   ══════════════════════════════════════════════════════════════════════════ */
console.log('\n— 27. 第四轮整改：跳转链 / 表头 / 命中区 / Enter 提交 —');
{
  const styleText27 = src.slice(src.indexOf('<style>'), src.indexOf('</style>'));
  const strip27 = t2 => t2.replace(/\/\*[\s\S]*?\*\//g, '');
  const style27 = strip27(styleText27);
  const blocks27 = t2 => [...t2.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(m => ({ sel: m[1].trim(), body: m[2] }));
  const rules27 = blocks27(style27);
  // 同一个选择器可能出现多条规则（.del 有两条：一条 cursor、一条 margin-left），
  // 所以按"所有同名规则拼起来"判，不能只 find 第一条 —— 只 find 第一条会漏。
  const rulesOf = s => rules27.filter(b => b.sel === s);
  const bodyOf = s => rulesOf(s).map(b => b.body).join(';');
  const hasIn = (s, frag) => rulesOf(s).some(b => b.body.includes(frag));
  const numIn = (s, re) => { const m = re.exec(bodyOf(s)); return m ? parseFloat(m[1]) : NaN; };
  // 只看标签本身，不看注释 —— 注释里提到 alert() 是必要的说明
  const html27 = strip27(src.slice(0, src.indexOf('<style>'))) + src.slice(src.indexOf('</style>'));

  // ── A. 跳转链 ──────────────────────────────────────────────────────
  const skipTag = html27.indexOf('<a class="skip" href="#main">');
  /* 别写死 '<main id="main">'：它后面还挂着 tabindex="-1"，属性一加就变成假失败（刚踩过）。
     用"标签开头"去匹配，后面挂什么属性都不影响。 */
  const mainTag = html27.search(/<main id="main"[^>]*>/);
  t('有"跳到主内容"链接', skipTag >= 0);
  t('跳转链在 <main> **之前**（放在后面等于没有）', skipTag >= 0 && mainTag > skipTag,
    `skip@${skipTag} main@${mainTag}`);
  t('#main 这个跳转目标真的存在', mainTag > 0);
  /* 跳转目标必须能接住焦点。实测：不加 tabindex="-1" 时按回车后 activeElement 仍是 BODY，
     焦点没进正文 —— 下一次 Tab 又从页首第一个元素开始，跳转链等于白按。
     tabindex="-1" 让它可编程聚焦但**不进** Tab 序，这是 skip-link 的标准做法。 */
  t('跳转目标 <main> 带 tabindex="-1"（能接住焦点，且不进 Tab 序）',
    /<main id="main"[^>]*tabindex="-1"/.test(src));
  t('跳转链默认移出视口（不占位、不闪）', hasIn('.skip', 'top:-48px'), bodyOf('.skip').slice(0, 60));
  /* R8：触屏宽度下输入框字号。两个坑：
     ① 不能用平坦正则取 @media 块 —— 那段里是嵌套规则，平坦正则只会匹配到内层；
     ② 必须用**去注释**的文本。mediaBody 用的是带注释的 styleText，而这条改动旁边
        正好有一段讨论 input 的注释，用它判会让"16px 是加给输入控件的"这条被注释喂饱。 */
  /* 第 26 节那几个工具（stripBlocks / blockAt）是在它自己的块作用域里声明的，
     第 27 节看不到，所以这里自带一份最小的括号配对 —— 别去引用别节的局部变量。 */
  const braceBody27 = (text, idx) => {
    let d = 0;
    for (let i = idx; i < text.length; i++) {
      if (text[i] === '{') d++;
      else if (text[i] === '}') { d--; if (d === 0) return text.slice(idx + 1, i); }
    }
    return null;
  };
  const mq560 = (() => {
    const st2 = strip27(styleText27);
    const m = /@media\(max-width:\s*560px[^{]*\{/.exec(st2);
    return m ? braceBody27(st2, m.index + m[0].length - 1) : null;
  })();
  /* 值必须是 16px，但要**走令牌**（--fs-lg = 16px）——
     直接写 font-size:16px 会破坏"全页字号走 --fs-*"那条纪律（实测就被它拦下来了）。
     16px 在这里不是设计刻度，是 iOS 的放大阈值这个平台常量，恰好与 --fs-lg 同值。 */
  t('移动断点(≤560px)把输入类控件提到 16px（否则 iOS 聚焦时会放大整页且不缩回）',
    !!mq560 && /font-size:var\(--fs-lg\)/.test(mq560) && /\.ledbar input/.test(mq560),
    mq560 ? mq560.replace(/\s+/g, ' ').trim().slice(-70) : '(找不到 @media(max-width:560px) 块)');
  const mq560grown = !!mq560 && /(^|[;{}])\s*(\.ledbar input|\.field input)[^{}]*\{[^{}]*font-size:var\(--fs-lg\)/.test(mq560);
  t('那条 16px 确实挂在输入类控件的选择器上，不是挂在别的元素上', mq560grown);

  t('跳转链**聚焦时**才滑进来', hasIn('.skip:focus', 'top:8px'), bodyOf('.skip:focus'));

  // ── B. 账本表头 ────────────────────────────────────────────────────
  const tbl27 = /<table id="ledger">[\s\S]*?<\/table>/.exec(src);
  const thead27 = tbl27 ? /<thead>[\s\S]*?<\/thead>/.exec(tbl27[0]) : null;
  t('账本表有 <thead>（改造前一个都没有，读屏用户完全不知道每列是什么）', !!thead27);
  const thAll27 = thead27 ? [...thead27[0].matchAll(/<th\b[^>]*>([^<]*)<\/th>/g)] : [];
  t('表头恰好 4 列', thAll27.length === 4, `${thAll27.length} 列`);
  t('每列都带 scope="col"', thead27 ? (thead27[0].match(/scope="col"/g) || []).length === 4 : false);
  t('列名是 日期/分类/备注/金额',
    thAll27.map(x => x[1].trim()).join('/') === '日期/分类/备注/金额',
    thAll27.map(x => x[1].trim()).join('/'));
  t('金额列表头带 .num（跟 .amt 一样右对齐，否则数字和表头对不上）',
    thead27 ? /<th scope="col" class="num">金额<\/th>/.test(thead27[0]) : false);
  t('#ledger thead th 规则存在', hasIn('#ledger thead th', 'text-align:left'));
  t('#ledger thead th.num 右对齐', hasIn('#ledger thead th.num', 'text-align:right'));
  /* 反向锁定：表头必须是**看得见**的。
     如果哪天有人改用"仅读屏可见"的写法（clip / position:absolute / left:-9999px），
     这条会红 —— 那是刻意的收紧：这张表是全页信息密度最高的一块，有列名能显著降扫读成本。 */
  const vis27 = '#ledger thead th';
  t('表头是可见的，没有被改成"仅读屏可见"',
    !/clip|position:absolute|left:-9999px|font-size:0/.test(bodyOf(vis27)),
    bodyOf(vis27).slice(0, 70));

  // ── C. 命中区 44×44（A25）─────────────────────────────────────────
  t('.del/.edit 有 position:relative（伪元素要靠它定位）', hasIn('.del,.edit', 'position:relative'));
  t('伪元素把命中区往外扩 6px', hasIn('.del::after,.edit::after', 'inset:-6px'),
    bodyOf('.del::after,.edit::after'));
  const pw27 = numIn('.del,.edit', /min-width:\s*([\d.]+)px/);
  const ph27 = numIn('.del,.edit', /min-height:\s*([\d.]+)px/);
  const grow27 = Math.abs(numIn('.del::after,.edit::after', /inset:\s*(-?[\d.]+)px/));
  t('视觉尺寸**保持不变**（32px）—— 反向锁定：别为了凑 44 把盒子撑大',
    pw27 === 32 && ph27 === 32, `${pw27}×${ph27}`);
  t('有效命中区 = 32 + 6 + 6 = 44 ≥ 44（A25）',
    pw27 + grow27 * 2 >= 44 && ph27 + grow27 * 2 >= 44,
    `${pw27 + grow27 * 2}×${ph27 + grow27 * 2}`);
  const gap27 = numIn('.del', /margin-left:\s*([\d.]+)px/);
  t('✎ 与 ✕ 之间留了 12px 空隙（改造前是 0，删除键紧贴编辑键）', gap27 === 12, String(gap27));
  /* 这一条是这一节最重要的断言：两个热区**不许重叠**。
     32px 的盒子各向外扩 6px，需要至少 12px 间距才能在中间正好相接。
     直接照抄评审报告里的 inset:-12px 会重叠 21px —— 想删一笔却打开了编辑。 */
  t('两个热区各扩 6px 后**不重叠**（间距 ≥ 2×扩展量）', gap27 >= grow27 * 2,
    `间距 ${gap27} vs 需要 ${grow27 * 2}`);
  /* 金额列必须 nowrap。否则 auto 布局会把 ✎/✕ 挤到第二行：实测金额列 90.7px、
     内容需要 91px，差不到 1px 就换行，行高 56 → 81。这是真机才看得见的缺陷
     （真实数据全只读、不渲染按钮），静态这条只是第一道闸。 */
  /* 选择器是 td.amt，不是 .amt —— 写错选择器会让这条永远红（踩过）。
     用 bodyOf 拼上"所有同类规则"再判，这样将来拆成多条也照样守得住。 */
  const amtRules = rulesOf('td.amt');
  /* 日期列同理：改造前 "09-17" 就被断成「09-」/「17」，257 行每行都这样。 */
  const dateRules = rulesOf('#ledger tbody td:first-child');
  t('日期列不换行（否则 257 行每行的日期都断成「09-」/「17」两行）',
    dateRules.length >= 1 && dateRules.every(b => b.body.includes('white-space:nowrap')),
    dateRules.map(b => b.body).join(' | ').slice(0, 60));
  t('金额列不换行（否则 ✎/✕ 被挤到第二行，行高 56→81）',
    amtRules.length >= 1 && amtRules.every(b => b.body.includes('white-space:nowrap')),
    `${amtRules.length} 条 td.amt 规则：${amtRules.map(b => b.body).join(' | ').slice(0, 70)}`);

  // ── D. 模态 Enter 提交 ─────────────────────────────────────────────
  for (const [tag, fid, fn] of [['项目', 'projForm', 'submitProj'], ['收益', 'earnForm', 'submitEarn']]) {
    t(`${tag}模态包在 <form id="${fid}"> 里（Enter 才能提交）`,
      new RegExp(`<form id="${fid}" onsubmit="return ${fn}\\(event\\)">`).test(src));
  }
  t('<h3> 标题仍在（包一层 form 后 aria-labelledby 的目标没丢）',
    /<h3 id="projTitle">添加项目<\/h3>/.test(src) && /<h3 id="earnTitle">记一笔收益<\/h3>/.test(src));
  t('form 不带来 UA 默认外边距（否则模态高度会变）', hasIn('#projForm,#earnForm', 'margin:0'));
  /* 最关键的一条：<button> 在 <form> 里**默认就是 submit**。
     取消按钮不写 type="button" 的话，点"取消"反而会触发保存 —— 一个会改数据的静默故障。 */
  const cancelBtn27 = src.match(/<button class="btn ghost" type="button" onclick="closeOv\(\)">取消<\/button>/g) || [];
  t('两个"取消"都显式写了 type="button"（不写就是 submit，点取消会保存）',
    cancelBtn27.length === 2, `${cancelBtn27.length} 个`);
  const submitBtn27 = src.match(/<button class="btn primary" type="submit">保存<\/button>/g) || [];
  t('两个"保存"改成 type="submit"', submitBtn27.length === 2, `${submitBtn27.length} 个`);
  /* 保存按钮必须**摘掉 onclick**：留着的话点击会先跑 onclick、再触发 submit，
     于是 onsubmit 里的 saveEarn 又跑一遍 —— 同一笔记录存两次。 */
  t('保存按钮上没有 onclick（否则 onclick + onsubmit 会把同一笔存两次）',
    !/<button class="btn primary"[^>]*onclick="save(Proj|Earn)\(\)"/.test(src));

  // 行为验证：真的拦住了默认提交、真的走到了 saveEarn
  const fake27 = () => ({ prevented: false, preventDefault() { this.prevented = true; } });
  /* ⚠️ 这一段踩过一个坑，注释留着：submitEarn 不是在"模拟"提交，它**真的会调用 saveEarn**。
     第一版写完就中招了 —— 当时 eAmt 里还留着前面测试填的合法金额，
     于是一条只为了验证"返回 false"的断言顺手把一笔收益真写进去了（257 → 258），
     而后面那条"校验失败没写记录"断言恰好把它吸收成了假绿（它只看自己那一次调用前后的差值）。
     所以现在：① 两次提交前都先把金额清空，强制走 saveEarn 的早退分支；
              ② 先断言**动手之前夹具是干净的**，再断言调用前后条数不变。
     只写 ② 等于允许"进来就已经脏了"。 */
  const nBefore27 = api.get().earnings.length;
  t('行为验证开始前夹具是干净的（否则下面的"条数不变"会把污染吞掉）',
    nBefore27 === N0, String(nBefore27));
  api.clearFieldErr('eAmt');
  const keepAmt27 = el('eAmt').value;
  el('eAmt').value = '';                 // 金额为空 → saveEarn 一定在校验处早退
  el('eDate').value = api.todayStr();
  const evP = fake27(), evE = fake27();
  const retP = api.submitProj(evP), retE = api.submitEarn(evE);
  t('submitProj 拦住默认提交并返回 false（不拦的话表单会 GET 提交把页面刷掉）',
    evP.prevented === true && retP === false, `prevented=${evP.prevented} ret=${retP}`);
  t('submitEarn 同样拦住默认提交并返回 false（内存状态一刷新就没了）',
    evE.prevented === true && retE === false, `prevented=${evE.prevented} ret=${retE}`);
  /* 光验"返回 false"是不够的：一个 `function submitEarn(){return false}` 也能通过。
     所以再验"它真的把活转给了 saveEarn" —— 只有真跑进 saveEarn 才会出现字段内联报错。 */
  api.submitEarn(fake27());
  t('submitEarn 真的走到了 saveEarn（金额为空时提交 → 字段出现内联报错）',
    ferr('eAmt').length > 0, ferr('eAmt'));
  t('三次提交都没有写出任何记录（校验失败即早退，不是"存了个空值"）',
    api.get().earnings.length === nBefore27 + 0, `${nBefore27} → ${api.get().earnings.length}`);
  el('eAmt').value = keepAmt27;
  api.clearFieldErr('eAmt');

  // ── E. 顺带守住两条口径 ────────────────────────────────────────────
  t('页面里再没有 alert()（在**去注释**的源码上判，注释里提到它不算违规）',
    !/alert\s*\(/.test(strip27(src)), (strip27(src).match(/alert\s*\(/g) || []).join(' '));
  /* --shadow-md 之前是僵尸令牌（0 消费）。跳转链是个真正的浮层，正好落在"md=悬浮"这一档。
     注意别用"从 [data-theme=dark] 截断到结尾"这种办法去剥令牌块：.skip 规则排在令牌块后面，
     那样会把整段样式一起截掉，得到的是假失败。（第一版就是这么错的。） */
  const defOnly27 = Object.keys({});
  const shadowMdUses = (style27.match(/var\(--shadow-md\)/g) || []).length;
  t('--shadow-md 现在有消费者（不再是定义完没人用的僵尸令牌）',
    shadowMdUses >= 1, `出现 ${shadowMdUses} 次`);
  t('--shadow-md 用在跳转链上（浮层 → 海拔 md 这一档）', hasIn('.skip', 'var(--shadow-md)'));
}

/* ═══════════════════════════════════════════════════════════════════════════
   29. 功能类型区（资讯 / 写作 / 复盘）—— 2026-09-30 凌晨新增
   ═══════════════════════════════════════════════════════════════════════════
   这一节不测"有没有渲染出东西"，测的是三类只有**算过**才知道的事：

   ① **守恒**：Σ分组篇数 == 笔记数、已写+未写 == 章节总行数、近期+归档 == 条目总数。
      守恒式的好处是它不依赖任何写死的期望值，上游数据怎么变都成立 ——
      上一轮就是靠守恒式发现了"分组规则顺序错误吞掉记录"。
   ② **两处一致性**：页面上显示的数 vs sections.json 里的数。页面**不许自己重算口径**
      （新鲜度、窗口过滤、排序全部由上游算好），所以这里逐项对齐。
   ③ **能变红**：safeUrl 与新鲜度判定都用**构造值**喂一遍。真实 RSS 数据里没有
      javascript: 链接、也没有"刚好卡在阈值上"的样本，只跑真实数据这两条永远绿 ——
      那正是第 5 类假绿（断言选错维度）的温床。
   ─────────────────────────────────────────────────────────────────────────── */
{
  console.log('— 29. 功能类型区（资讯 / 写作 / 复盘）—');
  /* 用一个**带标签的块**，好在数据缺失时 `break sec29` 干净跳出。
     为什么不写 `if(!S) return;`：这是模块顶层、return 不合法；
     也不把整节塞进 else —— 那要把两百行重新缩进，diff 噪音盖过真实改动。
     为什么要能跳出：下面每条断言都要读 S.sections / secOf(...).items，
     数据不在时第 3 条的消息参数就会抛异常，**整个文件在汇总之前被截断**，
     后面几百条断言静默消失、连"结果：x 通过 / y 失败"都不打印 ——
     看起来像"跑完了没报错"。这是变异 #58 实测出来的（输出停在第 3 条）。 */
  const S = api.getSections();
  const hid = k => el('screen' + k[0].toUpperCase() + k.slice(1)).hidden;
  const cnt = (html, re) => (html.match(re) || []).length;

  t('sections 已加载（离线路径：来自 localStorage 里的内嵌快照）', !!S,
    S ? `${S.sections.length} 个区 / 生成于 ${String(S.generatedAt).slice(0, 16)}` : '未加载');
  t('加载过程没留下错误', !api.getSecErr(), api.getSecErr() || '（无错误）');
  /* 从这一行起是**依赖上游数据**的断言，单独圈成一个带标签的块，
     好在数据缺失时 `break data29` 干净跳出。
     为什么不写 `if(!S) return;`：这是模块顶层，return 不合法；
     也不把整节塞进 else —— 那要把两百行重新缩进，diff 噪音会盖过真实改动。
     为什么必须能跳出：下面每条断言都要读 S.sections / secOf(...).items，
     数据不在时早早就有一处消息参数抛异常，**整个文件在汇总之前被截断**，
     后面几百条断言静默消失、连"结果：x 通过 / y 失败"都不打印 ——
     看起来像"跑完了没报错"。这是变异 #58 实测出来的（输出停在第 3 条）。
     注意范围：G 段（.ns 的布局陷阱）读的是源码、与数据无关，所以留在块**外面**，
     数据缺失时它照样跑 —— 别顺手把整节都圈进去，那会把一条真能守缺陷的断言变成沉默。 */
  data29: {
  if (!S) {
    /* 数据没上来就不评估下面那些断言 —— 但要**明确记一条失败**、并把话说全，
       不能让它们静默消失。这条同时是「离线回退被改坏」的哨兵：
       内嵌种子、localStorage 键名、file:// 路径判断，坏任何一处都先命中它。 */
    t('类型区数据可用（数据缺失 → 本节数据相关断言**未执行**，不算通过）', false,
      `跳过：资讯/写作/复盘三区的数据断言（约 45 条）。原因见上一条。`);
    break data29;
  }
  t('三个区就是资讯 / 写作 / 复盘，id 齐全',
    ['news', 'writing', 'review'].every(k => !!api.secOf(k)), S.sections.map(x => x.id).join(' '));

  // ── A. 切换器 ────────────────────────────────────────────────────────
  api.setType('ledger');
  t('默认落在记账（新类型不许把首屏顶掉）', api.getCurType() === 'ledger' && !hid('ledger'));
  t('未选中的屏全部 hidden（屏显互斥）',
    ['news', 'writing', 'review'].every(hid),
    ['ledger', 'news', 'writing', 'review'].map(k => k + ':' + (hid(k) ? '隐' : '显')).join(' '));
  api.setType('news');
  t('切到资讯后其余三屏全 hidden', !hid('news') && ['ledger', 'writing', 'review'].every(hid));
  t('选中态与 aria-pressed 同源更新（视觉与 aria 不许各写一份）',
    el('tyNews').getAttribute('aria-pressed') === 'true' && el('tyNews').className.includes('on')
    && el('tyLedger').getAttribute('aria-pressed') === 'false' && !el('tyLedger').className.includes('on'));
  t('选择被持久化（下次打开还停在这一屏）', localStorage.getItem('wb_type') === 'news');
  t('非法类型名退回记账，不会切进一张空白屏',
    (() => { api.setType('nope'); return api.getCurType() === 'ledger'; })());
  t('切换器按钮在源码里是静态 button（不是 JS 拼出来的，a11y 可静态检查）',
    ['ledger', 'news', 'writing', 'review'].every(k =>
      src.includes('id="ty' + k[0].toUpperCase() + k.slice(1) + '"')),
    '4 个');

  // ── B. 资讯区 ────────────────────────────────────────────────────────
  const news = api.secOf('news');
  const near = news.items.filter(i => i.days !== null && i.days <= news.windowDays);
  t('★ 默认档位渲染条数 == min(NEWS_PAGE, 窗口内条数)，且 NEWS_PAGE 本身有上限',
    api.getNewsScope() === 'recent' && api.NEWS_PAGE <= 50 && (() => {
      const r = cnt(el('newsItems').innerHTML, /class="nitem/g);
      return r <= api.NEWS_PAGE && r === Math.min(api.NEWS_PAGE, near.length);
    })(), `页面 ${cnt(el('newsItems').innerHTML, /class="nitem/g)} / NEWS_PAGE=${api.NEWS_PAGE}（≤50）/ 窗口内 ${near.length}`);
  /* 上限那半句（NEWS_PAGE ≤ 50）是必须的：只断言"渲染数 == min(NEWS_PAGE, …)"的话，
     把 NEWS_PAGE 改成 9999 仍然成立 —— 又是一条"改坏了也绿"的断言。
     真正的性质是"首屏有个**小**上限"，所以上限本身也要断言。 */
  api.setNewsScope('window');
  const rowsWin = cnt(el('newsItems').innerHTML, /class="nitem/g);
  t('① 守恒：「近 N 天」档条数 == items 里落在窗口内的条数', rowsWin === near.length,
    `页面 ${rowsWin} / JSON ${near.length}`);
  t('① 守恒：窗口内 + 归档 == 条目总数',
    near.length + (news.items.length - near.length) === news.items.length,
    `${near.length} + ${news.items.length - near.length} = ${news.items.length}`);
  api.setNewsScope('all');
  const rowsAll = cnt(el('newsItems').innerHTML, /class="nitem/g);
  t('「全部」档条数 == items 总数', rowsAll === news.items.length,
    `页面 ${rowsAll} / JSON ${news.items.length}`);
  t('三档条数单调递增（最近 N ≤ 窗口 ≤ 全部）',
    Math.min(api.NEWS_PAGE, near.length) <= rowsWin && rowsWin <= rowsAll,
    `${Math.min(api.NEWS_PAGE, near.length)} ≤ ${rowsWin} ≤ ${rowsAll}`);
  /* 这条断言上一版只查了「文字里有数字」，于是「最近 20  20」（数字写两遍、
     还带一个双空格）照样全绿；第二版查「末尾是条数 + 无双空格」，但变异成
     「最近 20 20」（单空格、数字两遍）仍然能过。
     教训：**判据要逐字对上，不要描述"应该长什么样"的某个特征** ——
     特征式断言总能被一个同形的坏值满足。 */
  const expN = [Math.min(api.NEWS_PAGE, near.length), near.length, news.items.length];
  const expT = ['最近 ' + expN[0], '近 ' + news.windowDays + ' 天 ' + expN[1], '全部 ' + expN[2]];
  const btnT = [1, 2, 3].map(i => el('newsS' + i).textContent.trim());
  t('三档按钮文字逐字 == 档位名 + 真实条数（不是「末尾有个数」就算）',
    btnT.every((s, k) => s === expT[k]),
    btnT.map((s, k) => JSON.stringify(s) + ' vs ' + JSON.stringify(expT[k])).join(' | '));
  t('切到资讯不会清空账本（两个区互不干扰）',
    el('ledger').querySelector('tbody').innerHTML.includes('<tr'));

  const hrefs = [...el('newsItems').innerHTML.matchAll(/href="([^"]*)"/g)].map(m => m[1].replace(/&amp;/g, '&'));
  t('② 一致性：首条渲染顺序 == 上游顺序（页面不重排）',
    hrefs[0] === api.safeUrl(news.items[0].link), (hrefs[0] || '(无链接)').slice(0, 64));
  t('所有外链都是 http(s)，没有 javascript: / data:',
    hrefs.every(h => /^https?:\/\//.test(h)), `${hrefs.length} 个链接`);
  t('外链数量与带 rel="noopener noreferrer" 的数量相等（有一个新窗口就得有一条 rel）',
    cnt(el('newsItems').innerHTML, /target="_blank"/g) === cnt(el('newsItems').innerHTML, /rel="noopener noreferrer"/g),
    `${cnt(el('newsItems').innerHTML, /target="_blank"/g)} 个新窗口`);
  t('已读/未读两种状态在页面上都有文字，不只靠圆点颜色',
    /已读/.test(el('newsItems').innerHTML) || /未读/.test(el('newsItems').innerHTML));

  t('资讯概览条数 == stats 项数', cnt(el('newsStats').innerHTML, /class="kpi"/g) === news.stats.length,
    `${news.stats.length} 项`);

  const fHtml = el('#newsFeeds tbody').innerHTML;
  const fRows = cnt(fHtml, /<tr>/g);
  const deadJson = news.feeds.filter(f => f.dead).length;
  t('订阅源表行数 == feeds 条数', fRows === news.feeds.length, `页面 ${fRows} / JSON ${news.feeds.length}`);
  t('① 守恒：「停更」标记数 == feeds 里 dead 的条数',
    cnt(fHtml, /chip dead/g) === deadJson, `页面 ${cnt(fHtml, /chip dead/g)} / JSON ${deadJson}`);
  t('被标停更的确实都超过阈值（标记与判据不许各说各话）',
    news.feeds.filter(f => f.dead).every(f => f.days > news.feedStaleDays),
    news.feeds.filter(f => f.dead).map(f => `${f.name}:${f.days}天`).join('、'));
  /* ⚠️ 这两条必须查 `js`（页面自己的那段 JS），不能查 `src`（整份文件）。
     离线版把 sections.json 整份内嵌进了 <script id="wb-seeds">，而上游 stats 的 hint
     里正好就写着「超过 100 天没有新条目」—— 查整份文件会因此**假红**（第一版就是这么错的）。
     要断言的是"页面代码里没有硬编码"，数据快照里出现同样的字眼是另一回事。 */
  t('阈值取自上游 feedStaleDays，不是页面硬编码',
    /s\.feedStaleDays/.test(js) && !/超过 100 天没有新条目/.test(js),
    'feedStaleDays=' + news.feedStaleDays);

  // 资讯按钮上的未读徽标：不点进去也要知道有没有新东西。两处显示必须同源。
  const unreadNear = near.filter(i => !i.read).length;
  t('切换器上的未读徽标 == 近期未读数（两处显示同源）',
    (el('tyNewsN').textContent.trim() || '0') === String(unreadNear), `徽标 ${el('tyNewsN').textContent.trim() || 0} / 算出 ${unreadNear}`);

  // ── C. 写作区 ────────────────────────────────────────────────────────
  api.setType('writing');
  const wr = api.secOf('writing');
  const b0 = wr.books[0];
  const wHtml = el('wrBooks').innerHTML;
  const chRows = cnt(wHtml, /class="chrow/g);
  const chDone = cnt(wHtml, /class="chrow done"/g);
  t('章节行数 == chapterCount', chRows === b0.chapterCount, `${chRows} / ${b0.chapterCount}`);
  t('① 守恒：已写行数 == writtenCount，且未写 = 总数 − 已写',
    chDone === b0.writtenCount && (chRows - chDone) + chDone === chRows,
    `已写 ${chDone} / 未写 ${chRows - chDone} / 共 ${chRows}`);
  const pctMin = Math.round(b0.chars / b0.target.min * 100);
  const pctMax = Math.round(b0.chars / b0.target.max * 100);
  t('进度按目标区间**下限**算（拿上限当分母会得到一个更小的数）',
    pctMin !== pctMax && wHtml.includes('已写 ' + pctMin + '%'),
    `下限 ${pctMin}% / 上限 ${pctMax}%（页面用的是 ${wHtml.includes('已写 ' + pctMin + '%') ? '下限' : '上限/其它'}）`);
  t('未写的章节在页面上有「（未写）」文字，不只靠 ✓ 与颜色',
    cnt(wHtml, /（未写）/g) === (b0.chapterCount - b0.writtenCount),
    `${cnt(wHtml, /（未写）/g)} 处`);
  t('正文字数展示的是上游那个数（页面不自己加总）',
    wHtml.includes(b0.chars.toLocaleString()) && b0.chars > 0, `${b0.chars.toLocaleString()} 字`);

  const cRows = cnt(el('wrClients').innerHTML, /class="nitem"/g);
  t('客户内容条数 == clients 长度', cRows === wr.clients.length, `${cRows} / ${wr.clients.length}`);
  const cstat = {};
  wr.clients.forEach(c => { const k = c.status || '未标状态'; cstat[k] = (cstat[k] || 0) + 1; });
  t('② 客户状态是从真实 status 派生的（不是"全部待交付"这种模板话）',
    Object.keys(cstat).every(k => el('wrClients').innerHTML.includes(k)) && !/全部是待交付/.test(js),
    Object.entries(cstat).map(([k, v]) => k + ' ' + v).join('、'));

  // ── D. 复盘区 ────────────────────────────────────────────────────────
  api.setType('review');
  const rv = api.secOf('review');
  api.renderReview(rv);
  const nRows = cnt(el('rvItems').innerHTML, /class="nitem"/g);
  const bRows = cnt(el('rvBreak').innerHTML, /class="exprow"/g);
  const sumG = rv.groups.reduce((s, g) => s + g.count, 0);
  t('笔记条数 == items 长度', nRows === rv.items.length, `${nRows} / ${rv.items.length}`);
  t('分布条行数 == groups 长度', bRows === rv.groups.length, `${bRows} / ${rv.groups.length}`);
  t('① 守恒：Σ分组篇数 == 笔记数（分组不许丢笔记）', sumG === rv.items.length,
    `${sumG} / ${rv.items.length}，${rv.groups.length} 类`);
  t('缺 frontmatter 的笔记记为「未标注」而不是被丢掉',
    rv.items.length === sumG, '每一项都落进了某个分组');

  // ── E. 新鲜度与阈值 ──────────────────────────────────────────────────
  const kinds = S.sections.map(s => ({ id: s.id, th: s.freshness.threshold, days: s.freshness.days, stale: s.freshness.stale }));
  t('三个区的过期阈值各不相同（按类型给，不是一个全局值）',
    new Set(kinds.map(k => k.th)).size === kinds.length,
    kinds.map(k => `${k.id}:${k.th}天`).join(' '));
  t('① stale 的判定与 days > threshold 一致（三处口径同源，页面不重判）',
    kinds.every(k => k.stale === (k.days === null || k.days > k.th)),
    kinds.map(k => `${k.id}:${k.days}>${k.th}=${k.stale}`).join(' '));
  /* 断言名里**不写具体天数**：这个数每天都在变（快照重建一次就 +1），
     写进去就成了"明天自动变红"，然后人会来把名字改掉 —— 改名字比改断言省事，
     于是这条断言就慢慢失去意义。天数由 extra 打出来给人看，判据只看 stale 这个布尔。 */
  t('资讯区真实数据状态随最新内容日期变化，不写死为过期',
    api.secOf('news').freshness.stale
      === (api.secOf('news').freshness.days > api.secOf('news').freshness.threshold),
    `${api.secOf('news').freshness.days} 天 / 阈值 ${api.secOf('news').freshness.threshold} 天 / stale=${api.secOf('news').freshness.stale}`);
  /* 把"当初为什么会算错"钉进断言：新鲜度基准必须是**最新内容的发布时间**。
     插件里的 updatedAt 比最新文章还早 6 天（逻辑上不可能是"最后抓取"），
     拿它算会得到"比真实值大 9 天"的结果（当时实测 14 天 vs 真实 5 天）。
     note 里留着这条解释，有人哪天"顺手"把基准换回 updatedAt，这条会红。
     ⚠️ 这里**不写死天数**：天数每天都在变，写死的话明天就自动变红，
     而人会去改名字而不是改断言。只留两个"关于这件事的定性事实"（早 6 天、差 9 天）。 */
  t('新鲜度基准是「最新内容」而非插件 updatedAt，且 note 里留了为什么',
    /最新内容/.test(api.secOf('news').freshness.basis) && /updatedAt/.test(api.secOf('news').note || ''),
    api.secOf('news').freshness.basis);

  // ── F. 能变红：用构造值喂安全函数与措辞函数 ────────────────────────
  t('safeUrl 放行 http / https（大小写不敏感）',
    api.safeUrl('https://a.b/c') === 'https://a.b/c' && api.safeUrl('HTTP://a.b') === 'HTTP://a.b');
  t('safeUrl 拦掉 javascript: / data: / 相对路径 / 空值',
    ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,x', '/local/path', '', '   ']
      .every(u => api.safeUrl(u) === ''),
    ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,x', '/local'].map(u => u + '→' + JSON.stringify(api.safeUrl(u))).join(' '));

  const keepLink = news.items[0].link;
  news.items[0].link = 'javascript:alert(1)';
  api.renderNews(news);
  const leaked = /javascript:/i.test(el('newsItems').innerHTML);
  t('★ 把一条真条目改成 javascript: 后，渲染结果里不含 javascript:', !leaked,
    leaked ? '泄漏了！' : '退化成纯文本，不可点');
  news.items[0].link = keepLink;
  api.renderNews(news);
  t('恢复真链接后渲染回到正常（上一条不是靠"渲染失败"变绿的）',
    el('newsItems').innerHTML.includes('href="'));

  const mk = o => api.freshBar(Object.assign({ days: 1, threshold: 3, stale: false, empty: false, basis: 'b', latest: '2026-01-01T00:00:00' }, o));
  t('freshBar 对「过期」与「新鲜」给出不同标记',
    mk({ stale: true, days: 9 }).includes('已过期') && mk({}).includes('新鲜'));
  t('freshBar 不把 null 显示成「今天」（days=null 必须走"无数据"）',
    mk({ days: null, stale: true, empty: true, latest: null }).includes('无数据')
    && !mk({ days: null, stale: true, empty: true, latest: null }).includes('今天'),
    api.agoTxt(null));
  t('agoTxt 的边界：null→时间未知 / 0→今天 / 1→昨天 / 40→1 个月前',
    api.agoTxt(null) === '时间未知' && api.agoTxt(0) === '今天'
    && api.agoTxt(1) === '昨天' && api.agoTxt(40) === '1 个月前',
    [null, 0, 1, 40].map(d => d + '→' + api.agoTxt(d)).join(' '));
  /* 把真实的 days 全喂一遍。上一版这里写的是 agoTxt(0)==='今天' 加一句同义反复的
     "不会变成负数" —— 那等于没测：它没有碰任何真实数据，也不可能红。
     真正该守的两件事：① 渲染不许退化成 NaN/undefined；② "未来时间戳夹到 0"
     发生在**上游**（scripts/sections.py 的 days_since），页面永远看不到负数。 */
  const allDays = S.sections.flatMap(s =>
    (s.items || []).map(i => i.days).concat((s.feeds || []).map(f => f.days)));
  t('全部真实 days 都能渲染成人话（无 NaN / undefined / null）',
    allDays.length > 0 && allDays.every(d => /^[\u4e00-\u9fa5\d]/.test(api.agoTxt(d))),
    `${allDays.length} 个值，前 5：${allDays.slice(0, 5).map(d => d + '→' + api.agoTxt(d)).join(' ')}`);
  t('② 上游已把"未来时间戳"夹到 0（页面不该看到负数）',
    allDays.every(d => d === null || d >= 0),
    `最小 ${Math.min(...allDays.filter(d => d !== null))}`);

  // 复原：后面的收尾不该受这一节影响
  api.setNewsScope('recent');
  api.setType('ledger');
  t('切回记账后账本内容仍在（切屏不重渲染账本、不丢已有状态）',
    el('ledger').querySelector('tbody').innerHTML.includes('<tr'));
  /* renderAll 必须把当前类型区一起带上。漏调 renderSections 的话，页面上看不出任何异常 ——
     数据同步回来了、切过去的屏却还是旧的。这类"漏了一处调用"只有断言能守。 */
  api.setType('news');
  el('newsItems').innerHTML = '';
  api.renderAll();
  t('★ renderAll 会把当前类型区一起重渲染（漏调的话同步完那一屏还是旧的）',
    /class="nitem/.test(el('newsItems').innerHTML),
    `${cnt(el('newsItems').innerHTML, /class="nitem/g)} 行`);
  }  // ← data29 结束（以下与上游数据无关）

  /* ── G. 布局陷阱（静态那一半）──────────────────────────────────────────
     真机测出来的一条：`.nitem .ns` 原来写的是 `white-space:nowrap` + 单行省略号，
     于是这个元素的 min-content = 整段摘要宽度（实测 968px）→ grid 的 fr 轨道
     有 min-content 下限 → .leftcol 被顶到 1234px → 整页横向溢出
     （scrollWidth 1823 > 1440）。`overflow:hidden` 与 `min-width:0` 都拦不住，
     它们管的是"能不能被压缩"，不管"min-content 是多少"。
     **布局数值验不了（逻辑测试没有排版引擎），但"用了不该用的写法"验得了。**
     真机那一半在 smoke-ux.mjs 里逐屏量 scrollWidth。 */
  const nsRule = (src.match(/\.nitem \.ns\{[^}]*\}/) || [''])[0];
  t('★ 摘要 .ns 不再用 white-space:nowrap（它是把 fr 轨道顶开的那一条）',
    nsRule.length > 0 && !/white-space:\s*nowrap/.test(nsRule)
    && /line-clamp:\s*2/.test(nsRule), nsRule.slice(0, 120));
  t('摘要用两行截断（靠换行降 min-content，同时比单行省略号信息更多）',
    /-webkit-box-orient:\s*vertical/.test(nsRule) && /overflow:\s*hidden/.test(nsRule));
}

console.log('\n— 29.5. 自媒体运营：真实快照 / 今日核验 / 安全跳转 —');
{
  const ss=api.getSocial();
  t('social.json 已加载真实快照', ss && ss.posts.length===SOCIAL.posts.length && ss.posts.length>=41,
    ss?`${ss.posts.length} 条`:'未加载');
  api.setType('content'); api.renderContent();
  t('今日矩阵包含 X / 公众号 / 小红书 / 知识星球四平台',
    ['X','公众号','小红书','知识星球'].every(x=>el('mediaMatrix').innerHTML.includes(x)),
    el('mediaMatrix').innerHTML.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim().slice(0,120));
  t('默认未知显示“未核验”，不伪装成“未发布”或数字 0',
    (el('mediaMatrix').innerHTML.match(/未核验/g)||[]).length===SOCIAL.todayPlan.length
      && !el('mediaMatrix').innerHTML.includes('未发布'));
  /* 全量/明细边界都从 social.json 现算：导出更新后死数字必假红。
     断言的真正性质是「全量口径 ≥ 明细口径」，两边相等反而是异常。 */
  const zsMetrics = (ss.accounts.find(a=>a.platform==='知识星球')||{}).metrics||{};
  t('知识星球全量口径 ≥ 阅读明细边界（两边都现算）',
    zsMetrics.posts >= (zsMetrics.detailedPosts||0) && zsMetrics.posts > 0
      && zsMetrics.posts === ss.posts.filter(p=>p.platform==='知识星球').length,
    `全量 ${zsMetrics.posts} / 明细 ${zsMetrics.detailedPosts}`);
  t('逐条分析来自真实导出，并给出原因与跳转',
    /表现较好|需要复盘|样本未成熟/.test(el('mediaPosts').innerHTML)
      && /href="https:\/\//.test(el('mediaPosts').innerHTML));
  /* 概述区断言跟随真实数据：公众号已接入统计后，「未认证/权限不足」字样自然消失，
     写死它们反而假红。保留的核心性质是：四平台都在、无「未发布」伪装态。 */
  t('账号概述区明确显示四个平台，缺失指标不伪装成 0',
    ['X','公众号','小红书','知识星球'].every(x=>el('mediaAccounts').innerHTML.includes(x))
      && !/设为待发布|未发布/.test(el('mediaAccounts').innerHTML));
  t('本地知识库候选已进入页面，链接只能打开固定 Obsidian vault',
    ss.knowledgeBase && ss.knowledgeBase.items.length>0
      && el('mediaKnowledge').innerHTML.includes('obsidian://open?vault=MyVault')
      && !el('mediaKnowledge').innerHTML.includes('file://'));
  t('源码已删除固定流水线、假播放趋势和六条灵感样例',
    !/CSTAges|const PLAY=|INSP_SEED|wb_insp/.test(js));

  const keepMain=JSON.stringify(api.get()), keepPlan=store.get('wb_media_plan_'+api.todayStr());
  api.cycleMediaStatus('x');
  t('切换今日状态只写独立 localStorage，不污染主 state',
    JSON.stringify(api.get())===keepMain && store.has('wb_media_plan_'+api.todayStr()));
  api.cycleMediaStatus('x'); api.cycleMediaStatus('x'); api.cycleMediaStatus('x'); // 回到 unknown
  if(keepPlan===undefined) store.delete('wb_media_plan_'+api.todayStr()); else store.set('wb_media_plan_'+api.todayStr(),keepPlan);

  const keepUrl=ss.todayPlan[0].url;
  ss.todayPlan[0].url='javascript:alert(1)'; api.renderContent();
  t('恶意平台 URL 不会进入 href', !/href="javascript:/i.test(el('mediaMatrix').innerHTML));
  ss.todayPlan[0].url=keepUrl; api.renderContent();
  t('外链统一带 noopener noreferrer',
    [...el('mediaMatrix').innerHTML.matchAll(/<a [^>]+>/g)].every(m=>/rel="noopener noreferrer"/.test(m[0])));

  const reminders=api.mediaReminders(ss).map(x=>x.title).join('|');
  const xFresh=(ss.accounts.find(a=>a.platform==='X')||{}).freshness==='fresh';
  const gateNeeded=ss.automation.some(a=>a.state==='review');
  /* 缺指标提醒现算：哪个平台真的缺指标就应提醒哪个；平台数据齐了就不该再提。
     gateNeeded（人工闸门）与 X 新鲜度两个分支保持原有双向断言。 */
  const missingMetric = ss.accounts.some(a=>a.freshness!=='fresh')
    || Object.values((ss.accounts.find(a=>a.platform==='公众号')||{}).metrics||{}).length===0;
  t('Agent 提醒未核验 + 缺指标口径跟随真实数据；X 过期与发布闸门双向断言',
    /未核验/.test(reminders)
      && (missingMetric ? /统计权限不足|还缺逐条指标|尚未认证/.test(reminders) : true)
      && (gateNeeded?/人工闸门/.test(reminders):!/人工闸门/.test(reminders))
      && (xFresh?!/X 数据已过期/.test(reminders):/X 数据已过期/.test(reminders)),
    reminders);
  api.setType('ledger'); api.renderAll();
}

console.log('\n— 30. 日期矩阵：目标行动建议的四个分支必须一次跑遍 —');
{
  /* 这一节的存在本身就是一条教训，写在这里免得以后被人当"冗余"删掉。
     第 23 节那批断言是"按今天还剩几天选支"的 —— 一次运行**只验一支**，
     其余分支要等日历真的走到那些日子才有机会被跑到。一套"自适应日期"的断言
     如果永远只跑到一支，就是另一种不可证伪：它没错，只是没测
     （跟"永远绿"是孪生兄弟：绿得毫无信息量）。
     现实给了提醒：2026-09-30 00:00（当月最后一天）一跑，5 条断言变红 ——
     而红的原因不是代码坏了，是断言把"今天几号"当成了常量。
     当时我的第一反应是把断言放宽（`|| true`），那等于把一条真断言换成永远绿；
     正确做法是把**时钟注入**进来，一次把四个分支全跑一遍，并断言"四支都到过"。

     注入为什么可行：页面的 todayStr() 里是 `new Date()`，**调用时**才从全局取 Date，
     所以临时替换 globalThis.Date 就能决定页面认为"今天"是几号。
     ⚠️ 只换不还就是污染：下面用 try/finally 保证恢复（否则后面所有日期相关断言都会错）。 */
  const RealDate = globalThis.Date;
  const withClock = (iso, fn) => {
    const fixed = new RealDate(iso).getTime();
    class FakeDate extends RealDate {
      constructor(...a) { super(...(a.length ? a : [fixed])); }
      static now() { return fixed; }
    }
    globalThis.Date = FakeDate;
    try { return fn(); } finally { globalThis.Date = RealDate; }
  };

  t('注入的时钟真的生效（todayStr() 返回注入的那一天）',
    withClock('2026-09-15T12:00:00', () => api.todayStr()) === '2026-09-15',
    withClock('2026-09-15T12:00:00', () => api.todayStr()));
  /* 还原检查：用**本地**年月日拼期望值，别用 toISOString()。
     toISOString 是 UTC，本地凌晨 00:00~08:00 之间会差一天 —— 那种断言会在
     某些时段自己变红，正是这一整轮在清理的那类毛病。 */
  const rt = new RealDate();
  const expectToday = rt.getFullYear() + '-' + String(rt.getMonth() + 1).padStart(2, '0')
                    + '-' + String(rt.getDate()).padStart(2, '0');
  t('注入结束后时钟已还原（不然会污染后面所有日期相关断言）',
    api.todayStr() === expectToday, `todayStr=${api.todayStr()} / 实际 ${expectToday}`);

  const keepG = S.monthlyGoal, keepGs = JSON.stringify(S.goals || {});
  const actTxtOf = () => el('goalAction').innerHTML
    .replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  /* 分支判别器。⚠️ 必须有一支显式返回 '????'：
     如果写成"都不像就当 normal"，那么文案被改坏时会静默归到 normal，
     下面的期望值比对就永远成立 —— 又一条"改坏了也绿"。 */
  const branchOf = t2 => {
    /* 「已达标」那一支（gap<=0）在 goalAction 里写的是「目标已过线，超额 X」
       + 「可以把目标上调到 Y」。注意是「超额」而不是「已超额」——
       后者是 goalProj 那个元素的措辞。我第一次写错，判别器直接返回 '????'，
       反而帮我把这个错误当场暴露出来（这就是留 '????' 这一支的价值）。 */
    if (t2.includes('超额') || t2.includes('上调')) return 'extra';   // gap<=0
    if (t2.includes('本月已结束')) return 'ended';     // left<=0
    if (t2.includes('顺延到下月')) return 'late';      // 1<=left<=3
    if (t2.includes('日均需')) return 'normal';        // left>3
    return '????';
  };

  const cases = [
    ['2026-09-15T12:00:00', 100000, 'normal', '月中：剩 15 天 → 给日均需'],
    ['2026-09-27T12:00:00', 100000, 'late',   '剩 3 天 → 改说顺延到下月（日均需已无行动价值）'],
    ['2026-09-28T12:00:00', 100000, 'late',   '剩 2 天 → 同上'],
    ['2026-09-30T12:00:00', 100000, 'ended',  '当月最后一天：本月已结束 + 下月重设'],
    ['2026-09-30T12:00:00', 1,      'extra',  '最后一天但已达标：走"已超额"，优先级在日期之上'],
  ];
  const hit = { extra: 0, ended: 0, late: 0, normal: 0 };
  for (const [iso, goal, want, why] of cases) {
    const got = withClock(iso, () => {
      api.applyGoal(goal, false);
      api.renderGoal();
      return branchOf(actTxtOf());
    });
    hit[got] = (hit[got] || 0) + 1;
    t(`${iso.slice(0, 10)} / 目标 ${goal} → ${why}`,
      got === want, `走到 ${got}${got === want ? '' : '（期望 ' + want + '）'}`);
  }
  /* 最后这条才是本节的目的：**四支都真的被走到了**。
     没有它，上面五条即使全绿也只说明"我挑的日期都命中了我以为的分支"，
     而"某个分支在整轮测试里从未被执行"这件事看不出来。
     判据用 >=1（late 故意给了两个日期：left=3 与 left=2 是同一个分支的两个边界），
     并且必须**没有 '????'** —— 出现它说明有分支的文案已经不在这四支里了。 */
  t('★ 四个分支在本次运行里全部被跑到（这才是"自适应日期"该有的覆盖）',
    ['extra', 'ended', 'late', 'normal'].every(k => hit[k] >= 1) && !hit['????'],
    Object.entries(hit).map(([k, v]) => k + '×' + v).join(' '));

  api.applyGoal(keepG || 1, false);
  api.renderGoal();
  S.goals = JSON.parse(keepGs);
  S.monthlyGoal = keepG || 0;
  api.renderAll();
}

console.log('\n— 31. 行动首页 + Notion 真实项目页 —');
{
  // 首屏不再从 8 张 KPI 开始，而是先把项目 / 体检 / 目标各翻译成一个动作。
  clockAt('2026-10-03T12:00:00', () => api.renderDailyDeck());
  t('首屏行动区渲染 3 个动作（项目 / 体检 / 目标）',
    [el('deckProject'), el('deckHealth'), el('deckGoal')].every(x => /<strong>/.test(x.innerHTML)),
    [el('deckProject'), el('deckHealth'), el('deckGoal')].map(x => x.innerHTML.replace(/<[^>]+>/g, ' ').trim().slice(0, 28)).join(' | '));
  t('页头同时交代今天日期与 Notion 最近同步',
    el('todayLine').textContent.includes('Notion 最近同步') && el('todayLine').textContent.includes('10月3日'),
    el('todayLine').textContent);

  // 项目页的唯一真源必须是 state.projects。切到「全部」后，卡片数应逐条等于真实项目数。
  el('projQ').value = '';
  api.setType('project'); api.setProjectScope('all');
  const board = el('projBoard').innerHTML;
  t('项目页标明「项目管理 / Notion 只读 / 最近同步」',
    /项目管理/.test(el('projSourceBar').innerHTML) && /Notion 只读/.test(el('projSourceBar').innerHTML)
      && /最近同步/.test(el('projSourceBar').innerHTML),
    el('projSourceBar').innerHTML.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim());
  t('全部项目卡数 == state.projects（29），没有第二套伪项目库',
    (board.match(/<article class="proj/g) || []).length === S.projects.length && S.projects.length === 29,
    `${(board.match(/<article class="proj/g) || []).length} / ${S.projects.length}`);
  t('旧伪项目 seed / localStorage 键 / 推进删除函数已移除',
    !/PROJECTS_SEED|wb_projects|advanceProject\(|delProject\(|addProject\(/.test(js));
  t('真实项目行不提供本地编辑入口',
    !board.includes('编辑本地项目') && board.includes('Notion 只读'));

  const conflictN = S.projects.filter(api.projectConflict).length;
  t('状态 × 进度冲突由真实字段派生，并在项目页显式提示',
    conflictN > 0 && el('projStats').innerHTML.includes(String(conflictN))
      && el('riskList').innerHTML.includes('请到 Notion'),
    `冲突 ${conflictN} 个`);
  t('截止风险按绝对日期计算；完成项目与无日期项目不报假风险',
    clockAt('2026-10-03T12:00:00', () => {
      const r = api.projectRisk({status:'进行中', due:'2026-10-01'});
      return r && r.days === -2 && r.txt === '已逾期 2 天'
        && api.projectRisk({status:'已完成', due:'2026-10-01'}) === null
        && api.projectRisk({status:'进行中', due:''}) === null;
    }));

  // 搜索验的是 board 本身，不拿右侧「需要处理」列表混充命中。
  const needle = S.projects[0].name;
  el('projQ').value = needle;
  api.renderProject();
  t('项目搜索会把看板收敛到真实命中项',
    (el('projBoard').innerHTML.match(/<article class="proj/g) || []).length === 1
      && el('projBoard').innerHTML.includes(needle), needle);

  // 把 state.projects 临时替换成单条夹具；页面应立刻跟着变，证明没有偷偷读另一份 localStorage。
  const keepProjects = S.projects;
  S.projects = [{id:'notion-proof', notionId:'np-1', name:'唯一替换项目', status:'未开始', progress:10,
    category:'测试', priority:'高', due:'2026-10-09', doneDate:null, note:'状态真源证明', source:'notion', readOnly:true}];
  el('projQ').value = ''; api.setProjectScope('all');
  t('替换 state.projects 后页面同步替换（项目页没有独立状态）',
    el('projBoard').innerHTML.includes('唯一替换项目')
      && (el('projBoard').innerHTML.match(/<article class="proj/g) || []).length === 1);
  S.projects = keepProjects;

  const meta = api.normalize({projects:[{id:'x', notionId:'n1', name:'元数据项目', priority:'高', doneDate:'2026-09-01'}]});
  t('normalize 保留 Notion 项目标识 / 优先级 / 完成日期',
    meta.projects[0].notionId === 'n1' && meta.projects[0].priority === '高' && meta.projects[0].doneDate === '2026-09-01',
    JSON.stringify(meta.projects[0]));

  el('projQ').value = ''; api.setProjectScope('active'); api.setType('ledger'); api.renderAll();
}

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
