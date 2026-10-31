// 集成验证：
//   A. normalize() 的字段守恒 —— 防止"UI 保存一次就把元数据抹掉"
//   B. 真机点一次「同步一次」按钮的完整链路（runSync → POST /api/sync → sync-now.sh → Notion）
import fs from 'fs';

const HTML = '/Users/jascen/Development/daily-workbench/workbench.html';
const DATA_PATH = '/Users/jascen/Development/daily-workbench/data.json';
const BASE = 'http://127.0.0.1:8787';
const TOKEN = fs.readFileSync(process.env.HOME + '/.config/daily-workbench/sync-token', 'utf8').trim();

const before = JSON.parse(fs.readFileSync(DATA_PATH, 'utf8'));
const src = fs.readFileSync(HTML, 'utf8');
const js = [...src.matchAll(/<script>([\s\S]*?)<\/script>/g)].pop()[1];

const alerts = [];
class El {
  constructor(id) { this.id = id; this._value = ''; this._html = ''; this._opts = null;
    this.textContent = ''; this.className = ''; this.disabled = false; this.style = {};
    this._attr = {}; this._q = {}; }
  get value() { return this._value; }
  set value(v) { v = String(v == null ? '' : v);
    this._value = this._opts ? (this._opts.includes(v) ? v : '') : v; }
  get innerHTML() { return this._html; }
  set innerHTML(h) { this._html = String(h);
    const f = [...this._html.matchAll(/<option(?: value="([^"]*)")?>([^<]*)<\/option>/g)];
    this._opts = /<option/.test(this._html) ? f.map(m => m[1] !== undefined ? m[1] : m[2]) : null; }
  querySelector(s) { return this._q[s] || (this._q[s] = new El(this.id + ' ' + s)); }
  // className 与 classList 是同一份状态的两个视图（桩失真会导致假失败，见 logic 套件同处注释）
  _cls = new Set();
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v == null ? '' : v).split(/\s+/).filter(Boolean)); }
  classList = { add: c => this._cls.add(c), remove: c => this._cls.delete(c),
    contains: c => this._cls.has(c), toggle: c => this._cls.has(c) ? this._cls.delete(c) : this._cls.add(c) };
  getAttribute(k) { return this._attr[k] ?? null; } setAttribute(k, v) { this._attr[k] = String(v); }
  // 真实 DOM 有这三个方法；桩里缺了会让页面"清错误态 / 把错误挂到字段旁"的逻辑抛错
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
const doc = { getElementById: el, querySelectorAll: s => s === '.ov' ? [el('projOv'), el('earnOv')] : [],
  querySelector: s => el(s), documentElement: new El('html'), addEventListener: () => {}, createElement: () => new El('t'), body: new El('body') };
const store = new Map([['wb', JSON.stringify(before)]]);
const localStorage = { getItem: k => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)) };
const location = { protocol: 'http:', search: '?token=' + encodeURIComponent(TOKEN), href: BASE + '/workbench.html' };
// 静态 HTML 里的初始文案不会被我这个桩解析出来，手工补上，
// 否则 runSync 里 `label=btn.textContent` 会捕获到空串，令"文案复位"变成假失败。
el('syncBtn').textContent = '同步一次';
// 把相对路径补成绝对，其余原样透传给真实服务
const realFetch = (u, o) => fetch(String(u).startsWith('/') ? BASE + u : u, o);

const run = new Function('document', 'localStorage', 'location', 'alert', 'setInterval', 'clearInterval', 'fetch',
  js + `\nreturn { renderAll, renderLedger, runSync, normalize, loadState,
                  get: ()=>state, keys: o=>Object.keys(o).sort() };`);
const api = run(doc, localStorage, location, m => alerts.push(String(m)), () => 1, () => {}, realFetch);

let pass = 0, fail = 0;
const t = (n, c, x = '') => { c ? pass++ : fail++; console.log(`${c ? '  ✓' : '  ✗'} ${n}${x ? '  → ' + x : ''}`); };

/* 等页面启动**真的**完成，不要靠固定睡眠赌。
   页面的启动序列是 `await loadState(); await loadSections(); renderAll();` —— 两次网络往返。
   这里原来只 `setTimeout(60)` 就往下跑，只要某一次往返慢一点，`state` 还是 null，
   下一步测试自己调的 `api.renderAll()` 就在页面里抛
   `Cannot read properties of null (reading 'owner')` —— 整个套件**在打印任何断言之前**崩掉，
   而输出看起来像"产品坏了"。（2026-09-30 实测踩到一次：同一份代码前一次跑过了、后一次崩了，
   就是竞态。`sections.json` 有 158KB，第二次往返比第一次慢，把 60ms 撑破了。）
   所以改成轮询等条件 —— 这类"先 sleep 再断言"在测试里等同于掷骰子。 */
const waitFor = async (fn, ms = 8000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { if (fn()) return true; } catch (e) { /* 还没就绪 */ }
    await new Promise(r => setTimeout(r, 25));
  }
  return false;
};
const booted = await waitFor(() => {
  const s = api.get();
  return !!(s && Array.isArray(s.earnings) && s.earnings.length);
});
t('页面已启动完成、数据已就绪（不是靠 60ms 睡眠赌赢的）',
  booted, booted ? `${api.get().earnings.length} 条` : '等了 8s 仍未加载 —— 页面启动或 /data.json 有问题');

console.log('A. normalize() 字段守恒（保存不丢元数据）');
const normKeys = api.keys(api.normalize(before));
const rawKeys = api.keys(before);
// 不变量是「**一个都不许丢**」，不是「键集必须完全相等」——
// normalize 多出一个键是合法的：页面新加的顶层键（如 goals）在用户第一次设目标之前
// 本来就不在 data.json 里。写成相等会把"新功能刚上线还没落盘"判成"丢字段"（实测假红过一次）。
// 放宽到子集并不会漏掉真问题：键名打错（goals→goalsX）照样会让原键消失，仍然会红。
const dropped = rawKeys.filter(k => !normKeys.includes(k));
const added = normKeys.filter(k => !rawKeys.includes(k));
t('normalize 不丢任何 data.json 里已有的键', dropped.length === 0,
  `\n      丢: ${dropped.join(', ') || '（无）'}\n      多出（合法，尚未落盘的新键）: ${added.join(', ') || '（无）'}`);
for (const k of ['earningsReadonly', 'earningsSource', 'projectsSource', 'notionMeta', 'lastNotionSync', 'monthlyGoal', 'sourceMap', 'goals'])
  t(`  关键字段 ${k} 保留`, normKeys.includes(k), String(JSON.stringify(api.normalize(before)[k])).slice(0, 40));

console.log('\nB. 同步一次：真机全链路');
const S = api.get();
api.renderAll();
t('SERVED 模式已生效（页面走线上数据）', S.earnings.length === before.earnings.length, `${S.earnings.length} 条`);
t('按钮初始可用', el('syncBtn').disabled === false && el('syncBtn').textContent === '同步一次');

/* 先等同步锁空闲，再发起同步。
   为什么必须等：/api/sync 用的是**非阻塞**锁（同一时刻只允许一次同步）。
   那个锁是**服务端**持有的 —— 上一次测试进程若被中途杀掉（或有人在浏览器里
   正点着「同步一次」），服务端的同步子进程仍在跑、锁仍被占着，最长 SYNC_TIMEOUT(300s)。
   这时页面立刻拿到 409「已有一次同步正在进行」，而下面 4 条断言会把它读成
   "同步失败 / 耗时 0.0s / lastNotionSync 没刷新" —— 全是误报，
   却长得像产品缺陷。（2026-09-30 实测踩到：一个被 SIGTERM 掉的测试留下的同步
   让下一轮 5 条断言变红。人会被这种红训练成"忽略红"。）
   /api/status 里的 syncing 字段就是为这件事加的。 */
const waitIdle = async (maxMs = 200000) => {
  const started = Date.now();
  for (;;) {
    try {
      const r = await fetch(BASE + '/api/status');
      if (r.ok && (await r.json()).syncing === false) return Math.round((Date.now() - started) / 1000);
    } catch (e) { /* 服务刚好在重启：继续等 */ }
    if (Date.now() - started > maxMs) return -1;
    await new Promise(s => setTimeout(s, 3000));
  }
};
const idleAfter = await waitIdle();
t('同步锁已空闲（等它释放再发同步，否则 409 会被误读成功能坏了）',
  idleAfter >= 0, idleAfter >= 0 ? `等了 ${idleAfter}s` : '等了 200s 仍被占用 —— 服务端可能有卡住的同步');

const t0 = Date.now();
await api.runSync();
const secs = ((Date.now() - t0) / 1000).toFixed(1);

/* runSync 的结果现在走 notify() 常驻提示条（不再有 #syncOut 输出区）。
   页面桩里 $() 落到 el('notify')，innerHTML 写进去的文本可以直接断言。 */
const out = el('notify');
console.log('    [notify innerHTML]\n      ' + out._html.slice(0, 400));
/* 先把"同步到底通没通"判出来，再决定后面几条要不要评。
   为什么：这条链路要打真实 Notion，延迟与可用性不由被测代码决定
   （notion-sync.md §七 记着：配额报错会伪装成网络抖动，判法就是重试一次看是不是
   usage_limit_reached）。外部抖动时，naive 的写法会报出 3~5 条**像产品缺陷**的红
   （同步文案不对 / 耗时 0s / lastNotionSync 没刷新），而真正的原因只有一个。
   本项目自己的纪律是"会随机变红的闸门比没有闸门更糟"—— 所以这里把外部失败
   收敛成**一条**，并且标题里直接写"外部"；依赖它的断言标记为未执行。
   （2026-09-30 00:14 实测：一次 180s 超时（HTTP 504）把 3 条报成红的，而紧接着
   手跑一次 sync-now.sh 9.9s 就成功了 —— 纯粹的 Notion 侧抖动。） */
/* notify() 把消息写进 innerHTML（含 <br> 换行），桩元素的 textContent 不会同步它，
   所以判据读 _html 并剥掉标签得到纯文本。 */
const notifyTxt = out._html.replace(/<[^>]+>/g, '');
const syncOk = notifyTxt.includes('✓ 同步完成');
const externalFail = /504|超时|timeout|fetch failed|internal_error|usage_limit_reached/i.test(notifyTxt);
t('同步链路跑通（外部网络/Notion 侧失败单列一条，不混进产品缺陷）',
  syncOk, syncOk ? '✓ 同步完成' : (externalFail ? '外部原因：' : '原因不明：') + notifyTxt.trim().slice(0, 60));
/* 这条原来写的是 `t('未标记为错误样式', !out.className.includes('err'))` —— 它只在**成功**时成立，
   同步一失败就变红，而那时 `show err` 恰恰是页面**做对了**。
   2026-09-30 00:22 实测踩到：一次 180s 超时（HTTP 504）把它报成第 2 条"产品缺陷"，
   而它和上面那条外部失败说的是同一件事 —— 收敛没收敛干净。
   ⚠️ 但正解**不是** `if (syncOk) { ... }` 把它跳过。那样就浪费了一次真机会：
   "标色和结果不一致"是**两个方向**都算缺陷的，跳过等于放弃其中一半。
     · 成功却不标 err → 回归（比如某次改动让成功的输出也走错误样式）
     · 失败却不标 err → **更糟**：同步挂了却显示成正常，用户以为数据是新的
   所以判据改成"**一致性**"：结果是什么，样式就该是什么。这样无论外部链路通不通，
   这条断言都在评一个真性质，而不是在等一个好运气。
   ⚠️ 诚实记录覆盖的局限：一次运行只会走其中一个方向（由外部链路决定），
   另一个方向靠 `test-workbench-logic.mjs` 里的**源码级**断言守 ——
   那里逐个数 runSync 的出口（成功出口不许写 err、5 个失败出口必须都写 err）。
   这正是第 30 节"日期矩阵"那条教训的同款处理：能注入的就注入跑遍全支，
   注入不了的（这里是真实网络）就用源码级断言把契约钉死，不留"以为测过了"的空白。 */
const errStyled = out.className.includes('err');
t('输出样式与同步结果一致（成功不许标 err；失败**必须**标 err，否则挂掉会显示成正常）',
  syncOk ? !errStyled : errStyled,
  `结果=${syncOk ? '成功' : '失败'} / className="${out.className}"`);
if (!syncOk) {
  /* 明确记一条"未执行"，而不是让它们静默消失 —— 静默消失会让人以为"跑过了也没问题"。 */
  t('同步没跑通 → 依赖它的 3 条断言（耗时 / 文案含耗时 / lastNotionSync 刷新）未执行，不算通过',
    false, '先让外部链路恢复，再重跑本套件');
} else {
  t('输出里带耗时', /\d+\.\d+s/.test(notifyTxt));
}
t('按钮已恢复可用且文案复位', el('syncBtn').disabled === false && el('syncBtn').textContent === '同步一次', el('syncBtn').textContent);
/* 耗时这条的定位是**卡死探测**，不是延迟断言。
   ntn 经 Surge 打 mcp.notion.com 的延迟本身剧烈波动（sync-now.sh 头部记着
   0.18s~12s、成功率约 1/5），所以整条同步的耗时不是被测代码能决定的：
   2026-09-30 实测同一条链路，顺利时 9.6s（含新加的 sections 重建约 0.3s），
   重试时 68.6s。把上限卡在 60s 会把"网络慢"报成"代码有问题"，
   而人学会忽略它之后，真正卡死（180s 超时）也看不见了。
   所以上限贴 SYNC_TIMEOUT（300s）：它要拦的是"挂住不返回"，而不是正常的外网慢。 */
/* 只有同步真的跑通时才评它 —— 不写 `: true` 那种"真空通过"。
   （本项目抓过的第 5 类假绿就是这种：条件不成立就静默放行，看起来绿其实没测。
   这里改成"整条不发出"，并在上面用一条显式失败说明"这 3 条未执行"，不静默。） */
if (syncOk) {
  t(`耗时没有挂死（≤${300}s；与 SYNC_TIMEOUT 对齐，正常同步实测可到 160s+）`,
    +secs >= 2 && +secs <= 300, secs + 's');
}

const after = JSON.parse(fs.readFileSync(DATA_PATH, 'utf8'));
console.log('\nC. 落盘结果对比');
const cmp = [['收益', 'earnings'], ['项目', 'projects']];
for (const [label, k] of cmp)
  t(`${label}条数未异常波动`, Math.abs(after[k].length - before[k].length) <= 1, `${before[k].length} → ${after[k].length}`);
if (syncOk) {
  t('lastNotionSync 已刷新', after.lastNotionSync && after.lastNotionSync !== before.lastNotionSync,
    `${before.lastNotionSync} → ${after.lastNotionSync}`);
}  // 未跑通时不在上面假装通过：那 3 条已由显式失败标记为"未执行"
t('earningsReadonly 仍为 true（未被 UI 抹掉）', after.earningsReadonly === true, String(after.earningsReadonly));
t('projectsSource 仍指向「项目管理」', after.projectsSource && after.projectsSource.name === '项目管理');
t('顶层键集未变', JSON.stringify(api.keys(after)) === JSON.stringify(rawKeys), api.keys(after).join(', '));
/* 期望值从同步前的 data.json 现算而不是写死数字：写死会随每次 Notion 同步过期。
   这里要验证的只是「分位精度没被 round 破坏」，比较同步前后按分位取整的结果即可。 */
const inc = after.earnings.filter(e => e.amount > 0).reduce((s, e) => s + e.amount, 0);
const exp = after.earnings.filter(e => e.amount < 0).reduce((s, e) => s - e.amount, 0);
const inc0 = before.earnings.filter(e => e.amount > 0).reduce((s, e) => s + e.amount, 0);
const exp0 = before.earnings.filter(e => e.amount < 0).reduce((s, e) => s - e.amount, 0);
const cents = v => Math.round(v * 100);
t(`收入合计 = ${(cents(inc0) / 100).toFixed(2)}（分位精度未被 round 破坏）`,
  cents(inc) === cents(inc0), inc.toFixed(4));
t(`支出合计 = ${(cents(exp0) / 100).toFixed(2)}`,
  cents(exp) === cents(exp0), exp.toFixed(4));
t(`净额 = ${(cents(inc0 - exp0) / 100).toFixed(2)}`,
  cents(inc - exp) === cents(inc0 - exp0), (inc - exp).toFixed(4));

t('同步后页面 KPI 已重算', el('kpis').innerHTML.includes('总净额'), '');

console.log('\nD. 静态兜底白名单：令牌之外的文件不许被读到');
/* 这一节是 2026-09-30 实测到的一个漏洞的哨兵。
   原状：/data.json 这条路由要令牌，但末尾兜底是无条件 super().do_GET() ——
   **同一份数据的几十个副本都不需要令牌**：
       GET /backups/data.json.<ts>.bak  → 200（87110 字节，写入护栏自己旋转的全量副本）
       GET /notion-sync.md             → 200（56255 字节）
   也就是"锁了正门、侧门开着"。修法是 STATIC_ALLOW 白名单（只放 workbench.html）。
   下面每条都先确认**磁盘上真有这个文件** —— 否则 404 可能只是"文件不存在"，
   那是一条永远绿的假断言（本套件第 5 类假绿）。 */
const DIR = '/Users/jascen/Development/daily-workbench';
const probe = async (path, withToken) => {
  const r = await fetch(BASE + path + (withToken ? '?token=' + encodeURIComponent(TOKEN) : ''));
  return { code: r.status, len: (await r.arrayBuffer()).byteLength };
};

const mustBlock = ['/workbench.local.html', '/archive/notion-sync.md', '/scripts/sync/sections.py']
  .filter(p => fs.existsSync(DIR + p));
t('探测清单里的敏感文件在磁盘上确实存在（否则下面的 404 会是"文件本来就没有"）',
  mustBlock.length === 3, mustBlock.join(' ') || '(一个都没有，探测无效)');
for (const p of mustBlock) {
  const r = await probe(p);
  t(`无令牌 ${p} → 404`, r.code === 404, `HTTP ${r.code}（${r.len} 字节）`);
}
/* 清单里的 workbench.local.html 值得单独说明一句：它把整份 data.json 与 sections.json
   内嵌在文件里（那正是它"离线可用"的代价），等于一份可直接下载的完整数据副本 ——
   它绝不能在白名单里，哪怕它和 workbench.html 长得像。
   上面那条"三个文件都存在"的断言同时也是它的前置条件：文件不在时不会假装通过，
   会直接红，因为构建漏了离线页本身就是问题。 */
const bk = fs.readdirSync(DIR + '/backups').filter(f => /^data\.json\..*\.bak$/.test(f)).sort().pop();
t('backups/ 里有可探测的全量副本（否则下一条等于没测）', !!bk, bk || '(空)');
if (bk) {
  const r = await probe('/backups/' + bk);
  t('★ 无令牌 /backups/<全量副本> → 404（"锁正门留侧门"那一处）',
    r.code === 404, `HTTP ${r.code}（${r.len} 字节）`);
}
for (const [p, want] of [['/data.json', 401], ['/sections.json', 401], ['/social.json', 401], ['/web-presence.json', 401]]) {
  const r = await probe(p);
  t(`无令牌 ${p} → ${want}（路由上的令牌闸门没被绕开）`, r.code === want, `HTTP ${r.code}`);
}
const okH = await probe('/workbench.html');
t('无令牌 /workbench.html 仍可读（应用本体、不含数据，保留原行为）', okH.code === 200, `HTTP ${okH.code}`);
t('无令牌 /api/status 仍可读（页面靠它判断在线/离线，故意不要令牌）',
  (await probe('/api/status')).code === 200);
const tS = await probe('/sections.json', true);
t('带令牌 /sections.json → 200（白名单没有误伤正常路径）', tS.code === 200, `HTTP ${tS.code}（${tS.len} 字节）`);
const tSocial = await probe('/social.json', true);
t('带令牌 /social.json → 200（自媒体快照走同一令牌闸门）', tSocial.code === 200, `HTTP ${tSocial.code}（${tSocial.len} 字节）`);
const tWeb = await probe('/web-presence.json', true);
t('带令牌 /web-presence.json → 200（网站监控走同一令牌闸门）', tWeb.code === 200, `HTTP ${tWeb.code}（${tWeb.len} 字节）`);
const tH = await probe('/workbench.html', true);
t('带令牌 /workbench.html → 200', tH.code === 200, `HTTP ${tH.code}（${tH.len} 字节）`);

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
