const assert = require('node:assert/strict');
const {test} = require('node:test');
const {execFileSync} = require('node:child_process');
const {createServer} = require('node:https');
const {mkdtemp, readFile, rm} = require('node:fs/promises');
const {resolve, join} = require('node:path');
const {tmpdir} = require('node:os');
const {chromium} = require(process.env.FIREFLY_PLAYWRIGHT || 'playwright');

const extension = resolve(__dirname, '..');
const extensionId = 'ecbjgicacjhegoijmnkijlgeagafgogf';
const shell = `chrome-extension://${extensionId}/canvas.html`;
const options = `chrome-extension://${extensionId}/options.html`;
const edge = process.env.FIREFLY_EDGE_EXECUTABLE;
const captureChannel = 'firefly.chat-capture.v1';
const sampleGif = 'R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw==';

// Playwright-fulfilled responses never pass through declarativeNetRequest, so the sites are served by a
// real local HTTPS server and reached through host-resolver mapping.
const blocking = {
  'chatgpt.com': {'x-frame-options': 'DENY', 'content-security-policy': "frame-ancestors 'none'; script-src 'none'"},
  // Mirrors Gemini: blocked by X-Frame-Options only; its own CSP must survive in the panel.
  'gemini.google.com': {'x-frame-options': 'DENY', 'content-security-policy': "script-src 'none'"},
};

function siteServer(dir) {
  const key = join(dir, 'key.pem');
  const cert = join(dir, 'cert.pem');
  execFileSync(process.env.FIREFLY_OPENSSL || 'openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
    '-subj', '/CN=firefly-dock-fixture', '-keyout', key, '-out', cert], {stdio: 'ignore'});
  return Promise.all([readFile(key), readFile(cert)]).then(([keyPem, certPem]) => new Promise(done => {
    const server = createServer({key: keyPem, cert: certPem}, (request, response) => {
      const host = request.headers.host;
      if (host === 'other.test') {
        response.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
        return response.end('<!doctype html><iframe src="https://chatgpt.com/"></iframe><iframe src="https://gemini.google.com/"></iframe>');
      }
      response.writeHead(200, {'content-type': 'text/html; charset=utf-8', ...(blocking[host] ?? {})});
      response.end(`<!doctype html><title>static</title><h1 id="ok">${host} fixture</h1>` +
        "<script>document.title = 'scripted'</script>" +
        '<p id="capture-word" style="font: 24px sans-serif">Firefly</p>' +
        `<img id="capture-image" width="100" height="70" src="data:image/gif;base64,${sampleGif}">`);
    });
    server.listen(0, '127.0.0.1', () => done(server));
  }));
}

async function withFramingBrowser(run) {
  const work = await mkdtemp(join(tmpdir(), 'firefly-dock-framing-515-'));
  let server;
  let context;
  try {
    server = await siteServer(work);
    const {port} = server.address();
    const mapped = ['chatgpt.com', 'gemini.google.com', 'other.test'].map(host => `MAP ${host}:443 127.0.0.1:${port}`);
    context = await chromium.launchPersistentContext(join(work, 'profile'), {
      ...(edge ? {executablePath: edge} : {channel: 'chromium'}), headless: true, ignoreHTTPSErrors: true,
      args: ['--disable-background-networking', '--no-proxy-server', '--ignore-certificate-errors',
        `--host-resolver-rules=${[...mapped, 'MAP * ~NOTFOUND'].join(', ')}`,
        `--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
    });
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
    // Only the default Firefly origin is intercepted; it must never reach a real local service.
    await context.route('http://127.0.0.1:3000/**', route => route.fulfill({contentType: 'text/html; charset=utf-8',
      body: '<h1>Firefly · 离线测试工作区</h1>'}));
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    const page = context.pages()[0] || await context.newPage();
    page.setDefaultTimeout(5000);
    await page.goto(shell);
    await run({context, page, worker});
  } finally {
    await context?.close();
    server?.close();
    await rm(work, {recursive: true, force: true});
  }
}

async function frameState(page, site) {
  await page.waitForTimeout(1200);
  const frame = page.frames().find(item => item.url().startsWith(`https://${site}/`));
  if (!frame) return 'blocked';
  if (!await frame.locator('#ok').count()) return 'blocked';
  return frame.evaluate(() => document.title);
}

const ruleIds = worker => worker.evaluate(async () =>
  (await chrome.declarativeNetRequest.getDynamicRules()).map(rule => rule.id).sort());

test('per-site switch embeds a blocking site only inside the extension and switching off restores the block', {timeout: 90_000}, () =>
  withFramingBrowser(async ({context, page, worker}) => {
    const sites = page.getByLabel('对话站点');
    const hint = page.locator('#chat-framing-hint');
    await sites.selectOption('chatgpt.com');
    assert.equal(await page.locator('#chat-view iframe').count(), 0, 'default off: no frame');
    await hint.waitFor();
    assert.deepEqual(await ruleIds(worker), []);

    const settings = await context.newPage();
    await settings.goto(options);
    const result = settings.locator('#framing-result');
    for (const name of ['ChatGPT', 'Gemini']) {
      const box = settings.getByRole('checkbox', {name});
      await box.check();
      await result.getByText(`已开启 ${name}`, {exact: false}).waitFor();
    }
    // Page scripts cannot flip the switch; only a real user change is honored.
    await settings.evaluate(() => {
      const box = document.querySelector('[data-framing-site="chat.deepseek.com"]');
      box.checked = true;
      box.dispatchEvent(new Event('change'));
    });
    assert.equal(await settings.getByRole('checkbox', {name: 'DeepSeek'}).isChecked(), false);
    assert.deepEqual(await ruleIds(worker), [5151, 5152]);

    await page.bringToFront();
    await page.locator('#chat-view iframe').waitFor();
    assert.equal(await hint.isHidden(), true);
    // The replaced CSP is the documented cost for ChatGPT: its own script-src no longer applies.
    assert.equal(await frameState(page, 'chatgpt.com'), 'scripted');
    await sites.selectOption('gemini.google.com');
    assert.equal(await frameState(page, 'gemini.google.com'), 'static', 'Gemini keeps its own CSP');

    const ordinary = await context.newPage();
    await ordinary.goto('https://other.test/');
    // Gemini keeps its CSP, so only the extension-scoped rule condition stops ordinary pages from framing it.
    for (const site of ['chatgpt.com', 'gemini.google.com']) {
      assert.equal(await frameState(ordinary, site), 'blocked', `ordinary pages stay blocked for ${site} while switched on`);
    }
    await ordinary.close();

    await sites.selectOption('chatgpt.com');
    await page.locator('#chat-view iframe').waitFor();
    await settings.getByRole('checkbox', {name: 'ChatGPT'}).uncheck();
    await result.getByText('已关闭 ChatGPT', {exact: false}).waitFor();
    assert.deepEqual(await ruleIds(worker), [5152]);
    await page.bringToFront();
    await hint.waitFor();
    assert.equal(await page.locator('#chat-view iframe').count(), 0);
    await settings.evaluate(() => {
      const frame = document.createElement('iframe');
      frame.src = 'https://chatgpt.com/';
      document.body.append(frame);
    });
    assert.equal(await frameState(settings, 'chatgpt.com'), 'blocked', 'switched off: extension pages are blocked again');
  }));

test('#520 same-tab shell honors the per-site switches and starts collapsed without loading a chat site', {timeout: 90_000}, () =>
  withFramingBrowser(async ({context, page, worker}) => {
    const settings = await context.newPage();
    await settings.goto(options);
    await settings.getByRole('checkbox', {name: 'ChatGPT'}).check();
    await settings.locator('#framing-result').getByText('已开启 ChatGPT', {exact: false}).waitFor();
    assert.deepEqual(await ruleIds(worker), [5151]);

    await page.bringToFront();
    const direct = `${shell}?id=c1&project=p1&project_mode=1&direct=1`;
    await page.goto(direct);
    await page.waitForTimeout(800);
    assert.equal(await page.locator('#chat-dock-panel').isHidden(), true, 'same-tab shell starts collapsed');
    assert.equal(await page.locator('#chat-view iframe').count(), 0, 'collapsed: no chat site loaded');

    await page.goto(`${direct}&expand=1`);
    await page.getByLabel('对话站点').selectOption('chatgpt.com');
    await page.locator('#chat-view iframe').waitFor();
    assert.equal(await frameState(page, 'chatgpt.com'), 'scripted', 'switched-on site embeds in the same-tab shell');

    await settings.getByRole('checkbox', {name: 'ChatGPT'}).uncheck();
    await settings.locator('#framing-result').getByText('已关闭 ChatGPT', {exact: false}).waitFor();
    await page.bringToFront();
    await page.locator('#chat-framing-hint').waitFor();
    assert.equal(await page.locator('#chat-view iframe').count(), 0, 'switching off removes the frame in the same-tab shell');
  }));

test('#515 switched-on framed sites capture into the shell canvas; switching off takes them out of the panel', {timeout: 90_000}, () =>
  withFramingBrowser(async ({context, page}) => {
    const settings = await context.newPage();
    await settings.goto(options);
    const result = settings.locator('#framing-result');
    for (const name of ['ChatGPT', 'Gemini']) {
      await settings.getByRole('checkbox', {name}).check();
      await result.getByText(`已开启 ${name}`, {exact: false}).waitFor();
    }

    await page.bringToFront();
    await page.goto(`${shell}?id=c1&project=p1&project_mode=1`);
    const canvas = page.frameLocator('#firefly-frame');
    await canvas.getByRole('heading', {name: 'Firefly · 离线测试工作区'}).waitFor();
    const sites = page.getByLabel('对话站点');
    const chat = page.frameLocator('#chat-view iframe');
    const host = chat.locator('#firefly-chat-capture-host');
    await sites.selectOption('chatgpt.com');
    await chat.locator('#capture-word').dblclick({position: {x: 24, y: 12}});
    await host.locator('#selection-chip').click();
    await host.locator('#new-text').click();
    await host.getByText('已复制到画布。').waitFor();

    await sites.selectOption('gemini.google.com');
    const image = chat.locator('#capture-image');
    await image.waitFor();
    const bounds = await image.boundingBox();
    await page.mouse.move(bounds.x + 8, bounds.y + 8);
    await host.locator('#image-chip').click();
    await host.getByText('已复制到画布。').waitFor();
    const received = () => canvas.locator('body').evaluate(() => window.receivedCaptures);
    const expected = [
      {site: 'chatgpt.com', action: 'status'},
      {site: 'chatgpt.com', action: 'new-text', text: 'Firefly'},
      {site: 'gemini.google.com', action: 'new-image', mime: 'image/gif', bytes: sampleGif},
    ].map(data => ({channel: captureChannel, ...data}));
    assert.deepEqual(await received(), expected);

    await settings.getByRole('checkbox', {name: 'ChatGPT'}).uncheck();
    await result.getByText('已关闭 ChatGPT', {exact: false}).waitFor();
    await page.bringToFront();
    await sites.selectOption('chatgpt.com');
    await page.locator('#chat-framing-hint').waitFor();
    assert.equal(await page.locator('#chat-view iframe').count(), 0, 'switched off: no panel frame to capture from');
    assert.deepEqual(await received(), expected);
  }));
