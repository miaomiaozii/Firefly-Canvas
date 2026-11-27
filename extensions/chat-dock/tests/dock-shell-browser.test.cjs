const assert = require('node:assert/strict');
const {test} = require('node:test');
const {cp, mkdtemp, readFile, rm, writeFile, mkdir} = require('node:fs/promises');
const {resolve, join} = require('node:path');
const {tmpdir} = require('node:os');
const {chromium} = require(process.env.FIREFLY_PLAYWRIGHT || 'playwright');

const extension = resolve(__dirname, '..');
const shell = 'chrome-extension://ecbjgicacjhegoijmnkijlgeagafgogf/canvas.html';
const mockURL = 'https://chatgpt.com/firefly-local-fixture';
const denyURL = 'https://gemini.google.com/firefly-local-fixture';
const nativeDocks = new Map([
  ['www.doubao.com', 'https://www.doubao.com/chat/'],
  ['www.kimi.com', 'https://www.kimi.com/'],
  ['yuanbao.tencent.com', 'https://yuanbao.tencent.com/'],
]);
const edge = process.env.FIREFLY_EDGE_EXECUTABLE;
const screenshotDir = process.env.FIREFLY_DOCK_SCREENSHOTS;
const sampleGif = 'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
const fixtureCanvas = 'http://127.0.0.1:3000/static/smart-canvas.html?id=fixture';
const captureChannel = 'firefly.chat-capture.v1';

async function withDockBrowser(run, {mock = false} = {}) {
  const work = await mkdtemp(join(tmpdir(), 'firefly-dock-515-'));
  let context;
  try {
    const loadedExtension = mock ? join(work, 'extension') : extension;
    if (mock) {
      await cp(extension, loadedExtension, {recursive: true});
      const path = join(loadedExtension, 'dock-embed.js');
      const implementation = await readFile(path, 'utf8');
      const approval = /const approvedPages = Object\.freeze\(\{[^}]*\}\);/g;
      assert.equal([...implementation.matchAll(approval)].length, 1);
      // Only the copied approval table changes; fixtures exercise the shipped loader with intact headers.
      await writeFile(path, implementation.replace(approval, `const approvedPages = Object.freeze(${JSON.stringify({
        'chatgpt.com': mockURL, 'gemini.google.com': denyURL,
      })});`));
    }
    context = await chromium.launchPersistentContext(join(work, 'profile'), {
      ...(edge ? {executablePath: edge} : {channel: 'chromium'}), headless: true,
      args: ['--disable-background-networking', '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND',
        `--disable-extensions-except=${loadedExtension}`, `--load-extension=${loadedExtension}`],
    });
    const requested = [];
    await context.addInitScript(channel => {
      window.receivedCaptures = [];
      window.addEventListener('message', event => {
        if (!event.isTrusted || event.source !== window || event.origin !== location.origin ||
            event.data?.channel !== channel || event.ports.length !== 1) return;
        window.receivedCaptures.push(event.data);
        event.ports[0].postMessage({ok: true, canAppend: true});
        event.ports[0].close();
      });
    }, captureChannel);
    await context.route('**/*', async route => {
      const url = route.request().url();
      if (url.startsWith('chrome-extension:')) return route.continue();
      requested.push(url);
      if (/^http:\/\/127\.0\.0\.1:300[01]\/$/.test(url)) return route.fulfill({contentType: 'text/html; charset=utf-8', body: `<!doctype html>
        <style>body{font:16px system-ui;margin:24px} section{border:1px solid #aaa;border-radius:10px;padding:18px} a{display:block;margin-top:14px}</style>
        <h1>Firefly · 离线测试工作区</h1><section>示例项目<a href="/static/smart-canvas.html?id=fixture">打开画布</a></section>`});
      if (url === fixtureCanvas || url === fixtureCanvas.replace(':3000', ':3001')) {
        return route.fulfill({contentType: 'text/html; charset=utf-8', body:
          '<h1>示例画布</h1><label>提示词 <textarea></textarea></label>'});
      }
      if (url === 'https://unrelated.invalid/embedded') {
        return route.fulfill({contentType: 'text/html', body: `<iframe src="${nativeDocks.get('www.kimi.com')}"></iframe>`});
      }
      if ([mockURL, denyURL, 'https://chatgpt.com/', ...nativeDocks.values()].includes(url)) {
        return route.fulfill({contentType: 'text/html; charset=utf-8', headers: url === denyURL ? {
          'x-frame-options': 'DENY', 'content-security-policy': "frame-ancestors 'none'",
        } : {'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-src https://www.kimi.com"}, body: `<!doctype html>
          <style>:root{color-scheme:light dark}body{font:15px system-ui;margin:16px}section{padding:14px;border:1px solid #888;border-radius:8px}textarea{box-sizing:border-box;width:100%;margin-top:10px}</style>
          <h1>离线对话示例</h1><section>此页面允许正常嵌入。<textarea aria-label="示例对话输入"></textarea></section>
          <p id="capture-word" style="font: 24px sans-serif">Firefly</p>
          <img id="capture-image" width="100" height="70" src="data:image/gif;base64,${sampleGif}">`});
      }
      if (url === 'https://www.kimi.com/nested') {
        return route.fulfill({contentType: 'text/html', body: '<p>Nested chat frame</p>'});
      }
      return route.abort();
    });
    const page = context.pages()[0] || await context.newPage();
    page.setDefaultTimeout(5000);
    await page.setViewportSize({width: 1440, height: 900});
    await page.goto(shell);
    await run({page, context, requested});
  } finally {
    await context?.close();
    await rm(work, {recursive: true, force: true});
  }
}

async function shot(page, name) {
  if (!screenshotDir) return;
  await mkdir(screenshotDir, {recursive: true});
  await page.screenshot({path: join(screenshotDir, `${edge ? 'edge' : 'chromium'}-${name}.png`)});
}

test('packaged shell docks only the three confirmed sites and keeps every ordinary-tab destination', {timeout: 60_000}, () =>
  withDockBrowser(async ({page, context, requested}) => {
    const options = await context.newPage();
    await options.goto(new URL('options.html', shell).href);
    const launch = options.getByRole('link', {name: '打开画布与对话面板'});
    assert.equal(await launch.evaluate(link => link.href), shell);
    await options.close();
    const frame = page.locator('#firefly-frame');
    const firefly = frame.contentFrame();
    await firefly.getByRole('link', {name: '打开画布'}).click();
    assert.equal(await frame.getAttribute('allow'),
      'clipboard-read http://127.0.0.1:3000; clipboard-write http://127.0.0.1:3000; fullscreen http://127.0.0.1:3000');
    await firefly.getByRole('textbox', {name: '提示词'}).fill('canvas is independently usable');
    const sites = page.getByLabel('对话站点');
    const link = page.getByRole('link', {name: '在新标签页打开'});
    const expected = ['chatgpt.com', 'gemini.google.com', 'chat.deepseek.com', 'chat.qwen.ai', 'www.doubao.com', 'www.kimi.com', 'yuanbao.tencent.com'];
    assert.deepEqual(await sites.locator('option').evaluateAll(rows => rows.map(row => row.value)), expected);
    for (const site of expected) {
      await sites.selectOption(site);
      assert.equal(await link.getAttribute('href'), `https://${site}/`);
      const dock = page.locator('#chat-view iframe');
      const dockURL = nativeDocks.get(site);
      if (dockURL) {
        assert.equal(await dock.count(), 1, `${site} should dock`);
        assert.equal(await dock.getAttribute('src'), dockURL);
        await dock.contentFrame().getByRole('heading', {name: '离线对话示例'}).waitFor();
      } else {
        assert.equal(await dock.count(), 0, `${site} must remain ordinary-tab only`);
      }
    }
    assert.deepEqual(requested.filter(url => url.startsWith('https:')), [...nativeDocks.values()]);
    await sites.selectOption('chatgpt.com');
    await shot(page, 'site-fallback');
    const tabPromise = context.waitForEvent('page');
    await link.click();
    const tab = await tabPromise;
    await tab.getByRole('heading', {name: '离线对话示例'}).waitFor();
    assert.equal(tab.url(), 'https://chatgpt.com/');
    assert.equal(await tab.evaluate(() => window.opener), null);
    assert.equal(await firefly.getByRole('textbox', {name: '提示词'}).inputValue(), 'canvas is independently usable');
  }));

test('manual dock capture stays in its one Firefly document, including after a site switch', {timeout: 60_000}, () =>
  withDockBrowser(async ({page, context}) => {
    const outside = await context.newPage();
    await outside.goto(fixtureCanvas);
    const anotherDock = await context.newPage();
    await anotherDock.goto(shell);
    const otherCanvas = anotherDock.frameLocator('#firefly-frame');
    await otherCanvas.getByRole('link', {name: '打开画布'}).click();
    await page.bringToFront();
    const canvas = page.frameLocator('#firefly-frame');
    const site = page.getByLabel('对话站点');
    const chat = page.frameLocator('#chat-view iframe');
    const host = chat.locator('#firefly-chat-capture-host');
    const chip = host.locator('#selection-chip');
    async function openCaptureMenu() {
      await chat.locator('#capture-word').dblclick({position: {x: 24, y: 12}});
      await chip.click();
    }
    await site.selectOption('www.doubao.com');
    await openCaptureMenu();
    await host.getByText('请打开并刷新 Firefly 画布，确认项目已加载。').waitFor();
    assert.deepEqual(await outside.evaluate(() => window.receivedCaptures), []);
    await canvas.getByRole('link', {name: '打开画布'}).click();
    await openCaptureMenu();
    await host.locator('#new-text').waitFor();
    await shot(page, 'capture-menu');
    await host.locator('#new-text').click();
    await host.getByText('已复制到画布。').waitFor();
    await site.selectOption('www.kimi.com');
    await openCaptureMenu();
    await host.locator('#append-prompt').click();
    await host.getByText('已复制到画布。').waitFor();
    await site.selectOption('yuanbao.tencent.com');
    const image = chat.locator('#capture-image');
    await image.waitFor();
    const bounds = await image.boundingBox();
    await page.mouse.move(bounds.x + 8, bounds.y + 8);
    await host.locator('#image-chip').click();
    await host.getByText('已复制到画布。').waitFor();
    const expected = [
      {site: 'www.doubao.com', action: 'status'},
      {site: 'www.doubao.com', action: 'new-text', text: 'Firefly'},
      {site: 'www.kimi.com', action: 'status'},
      {site: 'www.kimi.com', action: 'append-prompt', text: 'Firefly'},
      {site: 'yuanbao.tencent.com', action: 'new-image', mime: 'image/gif', bytes: sampleGif},
    ].map(data => ({channel: captureChannel, ...data}));
    const received = frame => frame.locator('body').evaluate(() => window.receivedCaptures);
    assert.deepEqual(await received(canvas), expected);
    assert.deepEqual(await received(otherCanvas), []);
    assert.deepEqual(await outside.evaluate(() => window.receivedCaptures), []);
    const ordinary = await context.newPage();
    await ordinary.goto('https://unrelated.invalid/embedded');
    const unrelatedChat = ordinary.frameLocator('iframe');
    await unrelatedChat.locator('#capture-word').dblclick();
    assert.equal(await unrelatedChat.locator('#firefly-chat-capture-host').count(), 0);
    await ordinary.close();
    await page.bringToFront();
    await site.selectOption('www.kimi.com');
    await chat.locator('body').evaluate(() => {
      const frame = document.createElement('iframe');
      frame.src = 'https://www.kimi.com/nested';
      document.body.append(frame);
    });
    const nested = chat.frameLocator('iframe');
    await nested.getByText('Nested chat frame').dblclick();
    assert.equal(await nested.locator('#firefly-chat-capture-host').count(), 0);
    assert.deepEqual(await received(canvas), expected);
    const duplicate = await page.evaluateHandle(url => {
      const frame = document.createElement('iframe');
      frame.src = url;
      document.body.append(frame);
      return frame;
    }, fixtureCanvas);
    await page.locator('body > iframe').contentFrame().getByRole('heading', {name: '示例画布'}).waitFor();
    await openCaptureMenu();
    await host.getByText('请打开并刷新 Firefly 画布，确认项目已加载。').waitFor();
    assert.deepEqual(await received(canvas), expected);
    await duplicate.evaluate(frame => frame.remove());
    const wrongOrigin = fixtureCanvas.replace(':3000', ':3001');
    const changed = page.waitForEvent('framenavigated', frame => frame.url() === wrongOrigin);
    await page.locator('#firefly-frame').evaluate((frame, url) => { frame.src = url; }, wrongOrigin);
    await changed;
    await canvas.getByRole('heading', {name: '示例画布'}).waitFor();
    await openCaptureMenu();
    await host.getByText('请打开并刷新 Firefly 画布，确认项目已加载。').waitFor();
    assert.deepEqual(await received(canvas), []);
    assert.deepEqual(await received(otherCanvas), []);
    assert.deepEqual(await outside.evaluate(() => window.receivedCaptures), []);

    const options = await context.newPage();
    await options.goto(new URL('options.html', shell).href);
    await options.locator('#firefly-origin').fill('http://127.0.0.1:3001');
    await options.getByRole('button', {name: '授权并保存'}).click();
    await options.getByText('已保存，请刷新 Firefly 画布标签页。').waitFor();
    await options.close();
    await page.bringToFront();
    await page.reload();
    await canvas.getByRole('link', {name: '打开画布'}).click();
    await openCaptureMenu();
    await host.locator('#new-text').click();
    await host.getByText('已复制到画布。').waitFor();
    assert.deepEqual(await received(canvas), [
      {channel: captureChannel, site: 'www.kimi.com', action: 'status'},
      {channel: captureChannel, site: 'www.kimi.com', action: 'new-text', text: 'Firefly'},
    ]);
    assert.equal(await canvas.locator('body').evaluate(() => location.port), '3001');
    assert.deepEqual(await received(otherCanvas), []);
    assert.deepEqual(await outside.evaluate(() => window.receivedCaptures), []);
  }));

test('allowed mock stays loaded across docks and float drag/resize; restores each site and keeps the browser-denied page blocked', {timeout: 75_000}, () =>
  withDockBrowser(async ({page, context}) => {
    const panel = page.locator('#chat-dock-panel');
    const firefly = page.locator('#firefly-frame');
    const mock = page.frameLocator('#chat-view iframe');
    const input = mock.getByLabel('示例对话输入');
    const sites = page.getByLabel('对话站点');
    await input.fill('the iframe is not reloaded by layout changes');
    await mock.locator('#capture-word').dblclick();
    assert.equal(await mock.locator('#firefly-chat-capture-host').count(), 0);
    const pressMode = name => page.getByRole('button', {name, exact: true}).click();
    const drag = async (locator, dx, dy) => {
      const box = await locator.boundingBox();
      const x = box.x + box.width / 2;
      const y = box.y + box.height / 2;
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x + dx, y + dy, {steps: 5});
      await page.mouse.up();
    };
    const fit = async (width, height, mode) => {
      const box = await panel.boundingBox();
      const surface = await firefly.boundingBox();
      assert.ok(box.x >= 0 && box.y >= 0 && box.x + box.width <= width + .5 && box.y + box.height <= height + .5);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), width);
      if (mode === '左侧停靠') assert.ok(surface.x >= box.x + box.width);
      else if (mode === '右侧停靠') assert.ok(surface.x + surface.width <= box.x);
      else assert.equal(surface.width, width);
    };
    await pressMode('左侧停靠');
    const beforeDock = await panel.boundingBox();
    await drag(page.getByRole('separator'), 54, 0);
    assert.equal((await panel.boundingBox()).width, beforeDock.width + 54);
    await pressMode('浮动');
    await drag(page.getByRole('button', {name: '移动对话面板'}), -60, 40);
    let floating = await panel.boundingBox();
    assert.ok(floating.y > 30);
    await drag(page.getByRole('button', {name: '调整浮动面板尺寸'}), 35, -40);
    assert.ok((await panel.boundingBox()).width > floating.width);
    assert.ok((await panel.boundingBox()).height < floating.height);
    floating = await panel.boundingBox();
    await page.getByRole('button', {name: '收起对话面板'}).click();
    await page.getByRole('button', {name: '展开对话面板'}).click();
    assert.equal(await input.inputValue(), 'the iframe is not reloaded by layout changes');
    const blocked = page.waitForEvent('console', {predicate: message =>
      /frame-ancestors|X-Frame-Options/i.test(message.text()) && message.type() === 'error'});
    await sites.selectOption('gemini.google.com');
    await pressMode('右侧停靠');
    const open = page.getByRole('link', {name: '在新标签页打开'});
    assert.equal(await open.getAttribute('href'), 'https://gemini.google.com/');
    await page.waitForFunction(() => document.querySelector('#chat-view iframe')?.src.includes('gemini.google.com'));
    await blocked;
    assert.equal(await page.frameLocator('#chat-view iframe').getByText('离线对话示例', {exact: true}).count(), 0);
    const ordinary = await context.newPage();
    const deniedResponse = await ordinary.goto(denyURL);
    assert.equal((await deniedResponse.allHeaders())['x-frame-options'], 'DENY');
    assert.equal((await deniedResponse.allHeaders())['content-security-policy'], "frame-ancestors 'none'");
    await ordinary.getByRole('heading', {name: '离线对话示例'}).waitFor();
    await ordinary.close();
    await sites.selectOption('chatgpt.com');
    assert.deepEqual(await panel.boundingBox(), floating);
    await page.reload();
    await input.waitFor();
    assert.deepEqual(await panel.boundingBox(), floating);
    await sites.selectOption('gemini.google.com');
    assert.equal(await page.getByRole('button', {name: '右侧停靠', exact: true}).getAttribute('aria-pressed'), 'true');
    await sites.selectOption('chatgpt.com');
    for (const [width, height] of [[1440, 900], [1100, 800], [390, 844]]) {
      await page.setViewportSize({width, height});
      for (const theme of ['light','dark']) {
        await page.emulateMedia({colorScheme: theme});
        await page.waitForFunction(want => document.documentElement.classList.contains('studio-theme-dark') === want, theme === 'dark');
        for (const [index, mode] of ['左侧停靠', '右侧停靠', '浮动'].entries()) {
          await pressMode(mode);
          await fit(width, height, mode);
          await shot(page, `${width}-${theme}-${index}`);
        }
      }
    }
  }, {mock: true}));
