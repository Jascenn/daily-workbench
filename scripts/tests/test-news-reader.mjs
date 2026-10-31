#!/usr/bin/env node
// 资讯站内阅读的真实浏览器回归：外链不导航、正文渲染、失败兜底、Esc 关闭与手机布局。
import fs from 'fs';

const pw = await import('/usr/local/lib/node_modules/playwright/index.js');
const chromium = (pw.default || pw).chromium;
const TOKEN = fs.readFileSync(process.env.HOME + '/.config/daily-workbench/sync-token', 'utf8').trim();
const BASE = 'http://127.0.0.1:8787';
const CHROME = process.env.HOME + '/Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/' +
  'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';

let pass = 0;
let fail = 0;
const t = (name, ok, detail = '') => {
  ok ? pass++ : fail++;
  console.log(`  ${ok ? '✓' : '✗'} ${name}${detail ? '  → ' + detail : ''}`);
};

const browser = await chromium.launch({ executablePath: CHROME });

async function pageAt(width, height) {
  const page = await browser.newPage({ viewport: { width, height } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: async text => {
          window.__copiedText = text;
        },
      },
    });
  });
  await page.goto(`${BASE}/workbench.html?token=${encodeURIComponent(TOKEN)}`, { waitUntil: 'load' });
  await page.evaluate(() => setType('news'));
  return { page, errors };
}

async function mockArticle(page, status = 200) {
  let load = 0;
  await page.route('**/api/article?*', async route => {
    if (status !== 200) {
      await route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify({ error: '页面要求登录' }),
      });
      return;
    }
    load++;
    const title = load === 1 ? '工作台里的文章标题' : `工作台里的文章标题 ${load}`;
    const longBody = Array.from({ length: 40 }, (_, i) => ({
      type: 'p',
      text: `这是第 ${i + 1} 段正文。长文需要在右栏独立滚动，同时保留左侧列表作为阅读上下文。`,
    }));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        title,
        site: '测试资讯源',
        publishedAt: '2026-10-29T08:00:00+08:00',
        chars: 1800,
        blocks: [
          { type: 'p', text: '这是第一段正文。' },
          { type: 'h2', text: '小标题' },
          { type: 'p', text: '<img src=x onerror=alert(1)> 这是被转义的正文。' },
          { type: 'h3', text: '二级小标题' },
          {
            type: 'p',
            text: '查看文档，以及一段 inline_code()。',
            segments: [
              { type: 'text', text: '查看' },
              { type: 'link', text: '文档', href: 'https://example.com/docs' },
              { type: 'text', text: '，以及一段 ' },
              { type: 'code', text: 'inline_code()' },
              { type: 'text', text: '。' },
            ],
          },
          {
            type: 'p',
            text: '危险链接',
            segments: [{ type: 'link', text: '危险链接', href: 'javascript:alert(1)' }],
          },
          {
            type: 'image',
            src: 'https://example.com/cover.png',
            alt: '测试封面',
            caption: '这是图片说明',
          },
          { type: 'image', src: 'data:image/svg+xml,bad', alt: '危险图片', caption: '' },
          {
            type: 'pre',
            language: 'js',
            text: 'const html = "<img src=x onerror=alert(1)>";\nconsole.log(html);',
          },
          {
            type: 'quote',
            text: '请先检查系统设置，再逐项关闭推广通知。',
          },
          { type: 'li', text: '有序列表第一项', ordered: true, marker: '1.' },
          { type: 'li', text: '无序列表项目', ordered: false, marker: '•' },
          { type: 'hr', text: '' },
          ...longBody,
        ],
      }),
    });
  });
}

console.log('被测：资讯标题进入工作台站内阅读');
{
  const { page, errors } = await pageAt(1440, 1000);
  await mockArticle(page);
  const before = page.url();
  const originalHref = await page.locator('#newsItems .nitem .nt a').first().getAttribute('href');
  await page.locator('#newsItems .nitem .nt a').first().click();
  await page.waitForSelector('#newsReaderOv.show');
  await page.waitForFunction(() => document.querySelectorAll('#newsReaderBody p').length >= 2);
  const state = await page.evaluate(() => ({
    title: document.getElementById('newsReaderTitle').textContent,
    site: document.getElementById('newsReaderSite').textContent,
    date: document.getElementById('newsReaderDate').textContent,
    meta: document.getElementById('newsReaderMeta').textContent,
    body: document.getElementById('newsReaderBody').innerHTML,
    original: document.getElementById('newsReaderOriginal').getAttribute('href'),
    overflow: document.documentElement.scrollWidth - window.innerWidth,
    layout: (() => {
      const drawer = document.querySelector('.reader-drawer').getBoundingClientRect();
      const sidebar = document.querySelector('.reader-sidebar').getBoundingClientRect();
      const pane = document.querySelector('.reader-pane').getBoundingClientRect();
      const title = document.getElementById('newsReaderTitle');
      const titleBox = title.getBoundingClientRect();
      const body = document.getElementById('newsReaderBody');
      const bodyBox = body.getBoundingClientRect();
      const scroll = document.getElementById('newsReaderScroll');
      const list = document.getElementById('newsReaderList');
      const toc = document.getElementById('newsReaderToc');
      const bodyStyle = getComputedStyle(body);
      const fontSelect = document.getElementById('newsReaderFont');
      const rootStyle = getComputedStyle(document.documentElement);
      return {
        drawerWidth: Math.round(drawer.width),
        sidebarWidth: Math.round(sidebar.width),
        paneWidth: Math.round(pane.width),
        sameTop: Math.round(sidebar.top) === Math.round(pane.top),
        listItems: list.querySelectorAll('.reader-news-item').length,
        activeItems: list.querySelectorAll('.reader-news-item.active').length,
        tocVisible: !toc.hidden && getComputedStyle(toc).display !== 'none',
        tocLinks: document.querySelectorAll('#newsReaderTocList a').length,
        progressExists: !!document.getElementById('newsReaderProgress'),
        readerFont: getComputedStyle(document.querySelector('.reader-pane')).fontFamily,
        readerFontKey: document.getElementById('newsReaderOv').dataset.readerFont,
        readerFontValue: fontSelect.value,
        readerFontOptions: [...fontSelect.options].map(option => option.value),
        fontLabelVisible: !!document.querySelector('.reader-font-picker-label'),
        originalText: document.getElementById('newsReaderOriginal').textContent.trim(),
        originalStyle: (() => {
          const link = document.getElementById('newsReaderOriginal');
          const style = getComputedStyle(link);
          return {
            borderWidth: style.borderTopWidth,
            borderRadius: style.borderRadius,
            background: style.backgroundColor,
          };
        })(),
        sidebarFont: getComputedStyle(document.querySelector('.reader-sidebar')).fontFamily,
        pageFont: getComputedStyle(document.body).fontFamily,
        titleWidth: Math.round(titleBox.width),
        titleHeight: Math.round(titleBox.height),
        titleLeft: Math.round(titleBox.left),
        bodyWidth: Math.round(bodyBox.width),
        bodyLeft: Math.round(bodyBox.left),
        titleInScroll: scroll.contains(title),
        bodyFont: parseFloat(bodyStyle.fontSize),
        bodyLine: parseFloat(bodyStyle.lineHeight) / parseFloat(bodyStyle.fontSize),
        radiusSm: rootStyle.getPropertyValue('--radius-sm').trim(),
        radiusXs: rootStyle.getPropertyValue('--radius-xs').trim(),
        rich: (() => {
          const image = body.querySelector('.reader-figure img');
          const figure = body.querySelector('.reader-figure');
          const caption = body.querySelector('.reader-figure figcaption');
          const link = body.querySelector('a[href="https://example.com/docs"]');
          const inlineCode = body.querySelector('.reader-inline-code');
          const codeBlock = body.querySelector('.reader-code-block');
          const pre = codeBlock && codeBlock.querySelector('pre');
          const quote = body.querySelector('.reader-quote-wrap');
          const imageButton = body.querySelector('.reader-image-open');
          const ordered = body.querySelector('.reader-list-item.ordered');
          const imageBox = image && image.getBoundingClientRect();
          const bodyBox = body.getBoundingClientRect();
          const imageStyle = image ? getComputedStyle(image) : null;
          return {
            images: body.querySelectorAll('.reader-figure img').length,
            imageSrc: image && image.getAttribute('src'),
            imageAlt: image && image.getAttribute('alt'),
            imageLoading: image && image.getAttribute('loading'),
            imageReferrer: image && image.getAttribute('referrerpolicy'),
            imageWidth: imageBox && Math.round(imageBox.width),
            bodyWidth: Math.round(bodyBox.width),
            imageRadius: imageStyle && imageStyle.borderRadius,
            imageBorderWidth: imageStyle && imageStyle.borderTopWidth,
            caption: caption && caption.textContent,
            linkHref: link && link.getAttribute('href'),
            linkTarget: link && link.getAttribute('target'),
            linkRel: link && link.getAttribute('rel'),
            linkReferrer: link && link.getAttribute('referrerpolicy'),
            inlineCode: inlineCode && inlineCode.textContent,
            codeText: pre && pre.textContent,
            codeLanguage: codeBlock && codeBlock.querySelector('.reader-code-lang')?.textContent,
            codeInjectedImages: codeBlock ? codeBlock.querySelectorAll('img').length : -1,
            copyButtons: body.querySelectorAll('.reader-copy-btn').length,
            codeCopy: codeBlock && codeBlock.querySelector('.reader-copy-btn')?.dataset.copy,
            quoteCopy: quote && quote.querySelector('.reader-copy-btn')?.dataset.copy,
            imageButton: !!imageButton,
            imageButtonLabel: imageButton && imageButton.getAttribute('aria-label'),
            orderedMarker: ordered && ordered.querySelector('.reader-list-marker')?.textContent,
            hasUnsafeImage: [...body.querySelectorAll('img')].some(node =>
              /^data:|^javascript:/i.test(node.getAttribute('src') || '')),
            hasUnsafeLink: [...body.querySelectorAll('a')].some(node =>
              /^javascript:/i.test(node.getAttribute('href') || '')),
            hasHr: !!body.querySelector('hr'),
          };
        })(),
      };
    })(),
  }));
  t('点击标题后页面 URL 没有离开工作台', page.url() === before, page.url());
  t('阅读层显示标题、来源和日期',
    state.title === '工作台里的文章标题' && state.site === '测试资讯源' && state.date === '2026/10/29',
    `${state.title} · ${state.site} · ${state.date}`);
  t('正文段落与标题按结构化内容渲染',
    state.body.includes('<p>这是第一段正文。</p>') && /<h2[^>]*>小标题<\/h2>/.test(state.body));
  t('★ 第三方文本中的 HTML 被转义，不执行也不生成图片节点',
    state.body.includes('&lt;img src=x onerror=alert(1)&gt;')
      && state.layout.rich.images === 1
      && state.layout.rich.codeInjectedImages === 0);
  t('正文图片按博客样式渲染，并保留 alt、图注与安全加载属性',
    state.layout.rich.images === 1
      && state.layout.rich.imageSrc === 'https://example.com/cover.png'
      && state.layout.rich.imageAlt === '测试封面'
      && state.layout.rich.imageLoading === 'eager'
      && state.layout.rich.imageReferrer === 'no-referrer'
      && state.layout.rich.caption === '这是图片说明'
      && state.layout.rich.imageRadius === state.layout.radiusSm
      && state.layout.rich.imageBorderWidth === '1px',
    `${state.layout.rich.images} 张 / ${state.layout.rich.imageRadius}`);
  t('图片与正文同为 624px 满栏宽',
    Math.abs(state.layout.rich.imageWidth - state.layout.rich.bodyWidth) <= 1,
    `${state.layout.rich.imageWidth}px / ${state.layout.rich.bodyWidth}px`);
  t('正文链接、行内代码和有序列表保持博客阅读样式',
    state.layout.rich.linkHref === 'https://example.com/docs'
      && state.layout.rich.linkTarget === '_blank'
      && state.layout.rich.linkRel === 'noopener noreferrer'
      && state.layout.rich.linkReferrer === 'no-referrer'
      && state.layout.rich.inlineCode === 'inline_code()'
      && state.layout.rich.orderedMarker === '1.'
      && state.layout.rich.hasHr,
    `${state.layout.rich.linkHref} / ${state.layout.rich.orderedMarker}`);
  t('代码块保留语言、换行和原始文本，代码中的标签不会生成 DOM',
    state.layout.rich.codeLanguage === 'js'
      && state.layout.rich.codeText.includes('\nconsole.log(html);')
      && state.layout.rich.codeText.includes('<img src=x onerror=alert(1)>')
      && state.layout.rich.codeInjectedImages === 0,
    state.layout.rich.codeLanguage);
  t('代码块和引用块各自带复制按钮，不提供整篇复制',
    state.layout.rich.copyButtons === 2
      && state.layout.rich.codeCopy === 'const html = "<img src=x onerror=alert(1)>";\nconsole.log(html);'
      && state.layout.rich.quoteCopy === '请先检查系统设置，再逐项关闭推广通知。',
    `${state.layout.rich.copyButtons} 个块级复制按钮`);
  await page.locator('.reader-code-block .reader-copy-btn').click();
  const copiedCode = await page.evaluate(() => window.__copiedText);
  await page.locator('.reader-quote-wrap .reader-copy-btn').click();
  const copiedQuote = await page.evaluate(() => window.__copiedText);
  t('复制按钮只复制当前代码或提示词块',
    copiedCode === 'const html = "<img src=x onerror=alert(1)>";\nconsole.log(html);'
      && copiedQuote === '请先检查系统设置，再逐项关闭推广通知。',
    `${copiedCode?.split('\n')[0]} / ${copiedQuote}`);
  t('图片显示为可放大的按钮入口',
    state.layout.rich.imageButton
      && state.layout.rich.imageButtonLabel === '放大图片：测试封面',
    state.layout.rich.imageButtonLabel);
  t('危险链接与 data: 图片不会进入可点击或可加载属性',
    !state.layout.rich.hasUnsafeImage && !state.layout.rich.hasUnsafeLink);
  await page.locator('.reader-image-open').click();
  await page.waitForSelector('#readerImageOv.show');
  const imageOverlay = await page.evaluate(() => ({
    src: document.getElementById('readerImageViewer').getAttribute('src'),
    alt: document.getElementById('readerImageViewer').getAttribute('alt'),
    zoom: document.getElementById('readerImageZoom').textContent,
    readerStillOpen: document.getElementById('newsReaderOv').classList.contains('show'),
  }));
  await page.locator('#readerImageOv [aria-label="放大图片"]').click();
  const zoomed = await page.evaluate(() => document.getElementById('readerImageZoom').textContent);
  t('图片点击后进入独立大图查看层',
    imageOverlay.src === 'https://example.com/cover.png'
      && imageOverlay.alt === '测试封面'
      && imageOverlay.readerStillOpen,
    `${imageOverlay.src} / ${imageOverlay.alt}`);
  t('大图支持缩放并实时显示比例',
    imageOverlay.zoom === '100%' && zoomed === '125%',
    `${imageOverlay.zoom} → ${zoomed}`);
  await page.keyboard.press('Escape');
  const imageClosed = await page.evaluate(() => ({
    imageOpen: document.getElementById('readerImageOv').classList.contains('show'),
    readerOpen: document.getElementById('newsReaderOv').classList.contains('show'),
  }));
  t('Esc 只关闭大图，阅读页仍保留',
    !imageClosed.imageOpen && imageClosed.readerOpen);
  t('打开原文仍指向当前资讯的真实外链',
    state.original === originalHref, state.original);
  t('工具栏去掉“字体”标签，并把“打开原文”收敛为纯文字操作',
    state.layout.fontLabelVisible === false
      && state.layout.originalText === '打开原文'
      && state.layout.originalStyle.borderWidth === '0px'
      && state.layout.originalStyle.borderRadius === state.layout.radiusXs
      && state.layout.originalStyle.background === 'rgba(0, 0, 0, 0)',
    `${state.layout.originalText} / border ${state.layout.originalStyle.borderWidth}`);
  t('桌面端显示左侧资讯列表与右侧正文双栏',
    state.layout.sidebarWidth >= 280 && state.layout.paneWidth > state.layout.sidebarWidth
      && state.layout.sameTop,
    `列表 ${state.layout.sidebarWidth}px / 正文 ${state.layout.paneWidth}px`);
  t('左侧列表复用当前资讯，并高亮当前文章',
    state.layout.listItems >= 2 && state.layout.activeItems === 1,
    `${state.layout.listItems} 篇 / ${state.layout.activeItems} 个高亮`);
  t('宽屏正文显示目录，顶部有阅读进度线',
    state.layout.tocVisible && state.layout.tocLinks >= 2 && state.layout.progressExists,
    `${state.layout.tocLinks} 个目录项`);
  t('标题与正文同栏，标题已脱离固定工具条并进入滚动阅读区',
    state.layout.titleInScroll
      && Math.abs(state.layout.titleLeft - state.layout.bodyLeft) <= 1
      && Math.abs(state.layout.titleWidth - state.layout.bodyWidth) <= 1
      && state.layout.titleHeight >= 36,
    `标题 ${state.layout.titleWidth}px / 正文 ${state.layout.bodyWidth}px`);
  t('桌面正文与博客实测排版一致',
    state.layout.bodyWidth >= 620 && state.layout.bodyWidth <= 630
      && state.layout.bodyFont === 16 && state.layout.bodyLine === 1.8,
    `${state.layout.bodyWidth}px / ${state.layout.bodyFont}px / ${state.layout.bodyLine.toFixed(2)}`);
  t('阅读字体默认是霞鹜文楷，并提供四个可选主题',
    state.layout.readerFontKey === 'wenkai'
      && state.layout.readerFontValue === 'wenkai'
      && state.layout.readerFontOptions.join(',') === 'wenkai,fangsong,weekly,system'
      && /LXGW WenKai|霞鹜文楷/.test(state.layout.readerFont),
    `${state.layout.readerFontKey} / ${state.layout.readerFontOptions.join(',')}`);
  t('霞鹜文楷只作用于文章阅读区，不扩散到主工作台与资讯列表',
    /LXGW WenKai|霞鹜文楷/.test(state.layout.readerFont)
      && !/LXGW WenKai|霞鹜文楷/.test(state.layout.sidebarFont)
      && !/LXGW WenKai|霞鹜文楷/.test(state.layout.pageFont),
    `正文区 ${state.layout.readerFont.split(',')[0]} / 资讯列表 ${state.layout.sidebarFont.split(',')[0]}`);
  t('来源、日期、字数与阅读时长合成一条元信息',
    state.site === '测试资讯源' && state.date === '2026/10/29'
      && state.meta.includes('1,800 字') && state.meta.includes('约 4 分钟'));
  t('桌面阅读层没有横向溢出', state.overflow <= 1, `${state.overflow}px`);
  t('页面没有运行时异常', errors.length === 0, errors.slice(0, 2).join(' | '));

  await page.locator('#newsReaderFont').focus();
  const fontPickerFocus = await page.evaluate(() => {
    const picker = document.querySelector('.reader-font-picker');
    const select = document.getElementById('newsReaderFont');
    const pickerStyle = getComputedStyle(picker);
    const selectStyle = getComputedStyle(select);
    return {
      active: document.activeElement === select,
      outlineStyle: selectStyle.outlineStyle,
      outlineWidth: selectStyle.outlineWidth,
      selectShadow: selectStyle.boxShadow,
      pickerShadow: pickerStyle.boxShadow,
      selectBorderWidth: selectStyle.borderTopWidth,
    };
  });
  t('字体选择聚焦时只保留单层边框，不出现双重焦点圈',
    fontPickerFocus.active && fontPickerFocus.outlineStyle === 'none'
      && fontPickerFocus.outlineWidth === '0px'
      && fontPickerFocus.selectShadow === 'none'
      && fontPickerFocus.pickerShadow === 'none'
      && fontPickerFocus.selectBorderWidth === '1px',
    `outline ${fontPickerFocus.outlineWidth} / inner ${fontPickerFocus.selectShadow} / outer ${fontPickerFocus.pickerShadow}`);

  const fontStates = [];
  for (const key of ['fangsong', 'weekly', 'system', 'wenkai']) {
    await page.selectOption('#newsReaderFont', key);
    await page.waitForFunction(expected =>
      document.getElementById('newsReaderOv').dataset.readerFont === expected, key);
    fontStates.push(await page.evaluate(() => ({
      key: document.getElementById('newsReaderOv').dataset.readerFont,
      value: document.getElementById('newsReaderFont').value,
      family: getComputedStyle(document.querySelector('.reader-pane')).fontFamily,
      sidebar: getComputedStyle(document.querySelector('.reader-sidebar')).fontFamily,
      page: getComputedStyle(document.body).fontFamily,
      stored: localStorage.getItem('wb_reader_font'),
      wenkaiRegularCss: !!document.getElementById('readerWenkaiRegularCss'),
      wenkaiBoldCss: !!document.getElementById('readerWenkaiBoldCss'),
      wenkaiLoaded: document.fonts.check('16px "LXGW WenKai"'),
      wenkaiBoldLoaded: document.fonts.check('bold 16px "LXGW WenKai"'),
      weeklyCss: !!document.getElementById('readerWeeklyFontCss'),
    })));
  }
  const byFont = Object.fromEntries(fontStates.map(item => [item.key, item]));
  t('切换仿宋后正文 computed font 与字距主题同步更新',
    /FangSong|STFangsong|Songti SC/.test(byFont.fangsong.family)
      && byFont.fangsong.value === 'fangsong'
      && byFont.fangsong.stored === 'fangsong',
    byFont.fangsong.family.split(',')[0]);
  t('切换 Tw93 金楷后只加载字体表，正文使用 TsangerJinKai02',
    /TsangerJinKai02/.test(byFont.weekly.family)
      && byFont.weekly.weeklyCss
      && byFont.weekly.value === 'weekly'
      && byFont.weekly.stored === 'weekly',
    byFont.weekly.family.split(',')[0]);
  t('系统主题恢复系统字体，所有切换都不污染资讯列表和主工作台',
    /-apple-system/.test(byFont.system.family)
      && fontStates.every(item => !/LXGW WenKai|FangSong|TsangerJinKai02/.test(item.sidebar))
      && fontStates.every(item => !/LXGW WenKai|FangSong|TsangerJinKai02/.test(item.page)),
    byFont.system.family.split(',')[0]);
  t('霞鹜文楷复用博客同版本 WebFont，常规与粗体均加载成功',
    /LXGW WenKai|霞鹜文楷/.test(byFont.wenkai.family)
      && byFont.wenkai.value === 'wenkai'
      && byFont.wenkai.stored === 'wenkai'
      && byFont.wenkai.wenkaiRegularCss
      && byFont.wenkai.wenkaiBoldCss
      && byFont.wenkai.wenkaiLoaded
      && byFont.wenkai.wenkaiBoldLoaded,
    `regular ${byFont.wenkai.wenkaiLoaded} / bold ${byFont.wenkai.wenkaiBoldLoaded}`);

  const secondHref = await page.locator('#newsReaderList .reader-news-item').nth(1).evaluate(el => {
    const source = document.getElementById('newsReaderList');
    const items = [...source.querySelectorAll('.reader-news-item')];
    return items[1].getAttribute('onclick');
  });
  await page.locator('#newsReaderList .reader-news-item').nth(1).click();
  await page.waitForFunction(() => document.getElementById('newsReaderTitle').textContent.endsWith('2'));
  const switched = await page.evaluate(() => {
    const list = document.getElementById('newsReaderList');
    const sc = document.getElementById('newsReaderScroll');
    const beforeWindowY = window.scrollY;
    sc.scrollTop = sc.scrollHeight;
    sc.dispatchEvent(new Event('scroll'));
    return new Promise(resolve => requestAnimationFrame(() => resolve({
      title: document.getElementById('newsReaderTitle').textContent,
      activeIndex: [...list.querySelectorAll('.reader-news-item')]
        .findIndex(el => el.classList.contains('active')),
      scrollable: sc.scrollHeight > sc.clientHeight,
      scrollHeight: sc.scrollHeight,
      clientHeight: sc.clientHeight,
      scrolled: sc.scrollTop > 0,
      progress: parseFloat(document.getElementById('newsReaderProgress').style.width) || 0,
      windowY: window.scrollY,
      beforeWindowY,
    })));
  });
  t('点击左侧文章会切换右侧正文并更新高亮',
    switched.title === '工作台里的文章标题 2' && switched.activeIndex === 1,
    `${switched.title} / 第 ${switched.activeIndex + 1} 项`);
  t('正文在右栏独立滚动，进度线随滚动更新',
    switched.scrollable && switched.scrolled && switched.progress > 0
      && switched.windowY === switched.beforeWindowY,
    `${Math.round(switched.progress)}% / ${switched.clientHeight}→${switched.scrollHeight} / windowY ${switched.windowY}`);
  t('切换列表项仍使用可点击按钮而不是裸文本', secondHref.includes('selectNewsReader(1'));
  await page.keyboard.press('Escape');
  t('Esc 能关闭阅读层', !(await page.locator('#newsReaderOv').evaluate(el => el.classList.contains('show'))));
  await page.close();
}

console.log('\n被测：手机与深色模式');
{
  const { page, errors } = await pageAt(390, 844);
  await mockArticle(page);
  await page.evaluate(() => {
    if (document.documentElement.getAttribute('data-theme') !== 'dark') toggleTheme();
  });
  await page.locator('#newsItems .nitem .nt a').first().click();
  await page.waitForFunction(() => document.querySelectorAll('#newsReaderBody p').length >= 2);
  const mobile = await page.evaluate(() => {
    const dlg = document.querySelector('.reader-drawer').getBoundingClientRect();
    const btn = document.getElementById('newsReaderOriginal').getBoundingClientRect();
    const back = document.querySelector('.reader-mobile-back');
    const backBox = back.getBoundingClientRect();
    const fontPicker = document.querySelector('.reader-font-picker').getBoundingClientRect();
    const fontSelect = document.getElementById('newsReaderFont');
    const originalLabel = document.querySelector('.reader-original-label');
    const side = getComputedStyle(document.querySelector('.reader-sidebar'));
    const toc = getComputedStyle(document.getElementById('newsReaderToc'));
    const body = getComputedStyle(document.getElementById('newsReaderBody'));
    return {
      width: Math.round(dlg.width), height: Math.round(dlg.height),
      overflow: document.documentElement.scrollWidth - window.innerWidth,
      buttonHeight: Math.round(btn.height),
      buttonWidth: Math.round(btn.width),
      fontPickerWidth: Math.round(fontPicker.width),
      fontPickerRight: Math.round(fontPicker.right),
      fontPickerHeight: Math.round(fontPicker.height),
      fontSelectVisible: getComputedStyle(fontSelect).display !== 'none',
      backVisible: getComputedStyle(back).display !== 'none',
      backHeight: Math.round(backBox.height),
      originalLabelVisible: getComputedStyle(originalLabel).display !== 'none',
      sidebarDisplay: side.display,
      tocDisplay: toc.display,
      bodyFont: parseFloat(body.fontSize),
      image: (() => {
        const img = document.querySelector('#newsReaderBody .reader-figure img');
        if (!img) return null;
        const bodyBox = document.getElementById('newsReaderBody').getBoundingClientRect();
        const imageBox = img.getBoundingClientRect();
        return {
          width: Math.round(imageBox.width),
          bodyWidth: Math.round(bodyBox.width),
          right: Math.round(imageBox.right),
        };
      })(),
      color: body.color, background: getComputedStyle(document.querySelector('.reader-drawer')).backgroundColor,
      theme: document.documentElement.getAttribute('data-theme'),
    };
  });
  t('390px 下正文全屏，占满可视宽高', mobile.width === 390 && mobile.height === 844,
    `${mobile.width}×${mobile.height}px`);
  t('手机端隐藏左侧列表与目录，保留返回列表入口',
    mobile.sidebarDisplay === 'none' && mobile.tocDisplay === 'none'
      && mobile.backVisible && mobile.backHeight >= 44,
    `${mobile.sidebarDisplay} / ${mobile.backHeight}px`);
  t('手机阅读层没有横向溢出', mobile.overflow <= 1, `${mobile.overflow}px`);
  t('手机「打开原文」按钮保持 44px 触控区',
    mobile.buttonHeight >= 44 && mobile.buttonWidth >= 44 && mobile.originalLabelVisible,
    `${mobile.buttonWidth}×${mobile.buttonHeight}px`);
  t('手机保留字体主题入口，且不挤出右侧工具栏',
    mobile.fontSelectVisible && mobile.fontPickerHeight >= 40
      && mobile.fontPickerWidth <= 122 && mobile.fontPickerRight <= 390,
    `${mobile.fontPickerWidth}px / right ${mobile.fontPickerRight}px`);
  t('手机正文与博客保持一致，使用 16px 阅读字号',
    mobile.bodyFont === 16, `${mobile.bodyFont}px`);
  t('手机图片不超出正文栏且右侧不会溢出视口',
    mobile.image && mobile.image.width <= mobile.image.bodyWidth + 1
      && mobile.image.right <= 390,
    mobile.image && `${mobile.image.width}px / right ${mobile.image.right}px`);
  t('深色模式下正文与底板仍有独立颜色',
    mobile.theme === 'dark' && mobile.color && mobile.background && mobile.color !== mobile.background,
    `${mobile.color} / ${mobile.background}`);
  t('手机页面没有运行时异常', errors.length === 0, errors.slice(0, 2).join(' | '));
  await page.close();
}

console.log('\n被测：阅读字体选择持久化');
{
  const { page, errors } = await pageAt(1440, 1000);
  await mockArticle(page);
  await page.locator('#newsItems .nitem .nt a').first().click();
  await page.waitForFunction(() => document.querySelectorAll('#newsReaderBody p').length >= 2);
  await page.selectOption('#newsReaderFont', 'fangsong');
  await page.waitForFunction(() =>
    document.getElementById('newsReaderOv').dataset.readerFont === 'fangsong');
  await page.reload({ waitUntil: 'load' });
  await page.evaluate(() => setType('news'));
  await page.locator('#newsItems .nitem .nt a').first().click();
  await page.waitForFunction(() => document.querySelectorAll('#newsReaderBody p').length >= 2);
  const persisted = await page.evaluate(() => ({
    key: document.getElementById('newsReaderOv').dataset.readerFont,
    value: document.getElementById('newsReaderFont').value,
    family: getComputedStyle(document.querySelector('.reader-pane')).fontFamily,
    stored: localStorage.getItem('wb_reader_font'),
  }));
  t('刷新页面后仍保留上次选择的仿宋主题',
    persisted.key === 'fangsong' && persisted.value === 'fangsong'
      && persisted.stored === 'fangsong'
      && /FangSong|STFangsong|Songti SC/.test(persisted.family),
    `${persisted.key} / ${persisted.family.split(',')[0]}`);
  t('字体持久化流程没有运行时异常', errors.length === 0, errors.slice(0, 2).join(' | '));
  await page.close();
}

console.log('\n被测：正文抓取失败');
{
  const { page } = await pageAt(1440, 1000);
  await mockArticle(page, 422);
  await page.locator('#newsItems .nitem .nt a').first().click();
  await page.waitForFunction(() => document.getElementById('newsReaderState').textContent.includes('没有取到正文'));
  const fallback = await page.evaluate(() => ({
    error: document.getElementById('newsReaderState').textContent,
    original: document.getElementById('newsReaderOriginal').href,
    body: document.getElementById('newsReaderBody').textContent.trim(),
  }));
  t('抓取失败时显示可理解的错误', fallback.error.includes('页面要求登录'), fallback.error);
  t('失败时正文区保持空白，不展示半截脏内容', fallback.body === '', JSON.stringify(fallback.body));
  t('失败时仍保留打开原文入口', /^https?:\/\//.test(fallback.original), fallback.original);
  await page.close();
}

await browser.close();
console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
