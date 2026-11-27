const assert = require('node:assert/strict');
const {join} = require('node:path');
const {test} = require('node:test');
const {chromium} = require(process.env.FIREFLY_PLAYWRIGHT || 'playwright');

const ext = join(__dirname, '..');
const canvasURL = 'http://127.0.0.1:3000/static/smart-canvas.html';
const chatURL = 'https://chatgpt.com/fixture';
const imageURL = 'https://chatgpt.com/fixture.gif';
const gif = 'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
const channel = 'firefly.chat-capture.v1';

test('unpacked MV3 runs the manual control and relay on offline fixtures', {timeout: 60_000}, async () => {
  const context = await chromium.launchPersistentContext('', {
    channel: 'chromium', headless: true,
    args: ['--disable-background-networking', '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND',
      `--disable-extensions-except=${ext}`, `--load-extension=${ext}`],
  });
  try {
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker', {timeout: 8000});
    assert.match(worker.url(), /^chrome-extension:\/\/ecbjgicacjhegoijmnkijlgeagafgogf\/background\.js$/);
    const imageFetches = [];
    await context.route('**/*', async route => {
      const url = route.request().url();
      if (url.startsWith(worker.url().replace('background.js', ''))) return route.continue();
      if (url === chatURL || url === 'https://sub.chatgpt.com/fixture' || url === 'https://chatgpt.com:8443/fixture') {
        return route.fulfill({contentType: 'text/html', headers: {'Content-Security-Policy': "script-src 'self'; require-trusted-types-for 'script'"},
          body: `<p id="selected" style="font:24px sans-serif">Firefly</p>
            <p id="editable" contenteditable>Editable</p>
            <img id="image" style="width:150px;height:100px" src="data:image/gif;base64,${gif}">
            <img id="https-image" style="width:150px;height:100px" src="${imageURL}">`});
      }
      if (url === imageURL) {
        if (route.request().resourceType() === 'fetch') {
          const headers = await route.request().allHeaders();
          imageFetches.push([route.request().method(), headers.cookie, headers.referer]);
        }
        return route.fulfill({contentType: 'image/gif', body: Buffer.from(gif, 'base64')});
      }
      if ([canvasURL, `${canvasURL}?other=1`, `${canvasURL}.bak`, canvasURL.replace('3000', '3001')].includes(url)) {
        return route.fulfill({contentType: 'text/html', body: '<h1>Fixture canvas</h1>'});
      }
      return route.abort();
    });
    await context.addInitScript(name => {
      window.fixture = {captured: [], canAppend: false};
      window.addEventListener('message', event => {
        if (!event.isTrusted || event.source !== window || event.origin !== location.origin ||
            event.data?.channel !== name || event.ports.length !== 1) return;
        window.fixture.captured.push(event.data);
        event.ports[0].postMessage(event.data.action === 'status' ? {ok: true, canAppend: window.fixture.canAppend} : {ok: true});
        event.ports[0].close();
      });
    }, channel);
    const canvas = await context.newPage();
    await canvas.goto(canvasURL);
    const otherPort = await context.newPage();
    await otherPort.goto(canvasURL.replace('3000', '3001'));
    const otherPath = await context.newPage();
    await otherPath.goto(`${canvasURL}.bak`);
    const chat = await context.newPage();
    chat.setDefaultTimeout(5000);
    for (const url of ['https://sub.chatgpt.com/fixture', 'https://chatgpt.com:8443/fixture']) {
      await chat.goto(url);
      assert.equal(await chat.locator('#firefly-chat-capture-host').count(), 0);
    }
    await chat.goto(chatURL);
    await chat.bringToFront();
    const host = chat.locator('#firefly-chat-capture-host');
    await host.waitFor({state: 'attached'});
    const chip = host.locator('#selection-chip');
    const menu = host.locator('#text-menu');
    const append = host.locator('#append-prompt');
    const imageChip = host.locator('#image-chip');
    const emitted = () => canvas.evaluate(() => window.fixture.captured);
    const selectWord = () => chat.locator('#selected').dblclick({position: {x: 24, y: 12}});

    await chat.locator('#editable').dblclick({position: {x: 24, y: 8}});
    await chip.waitFor({state: 'hidden'});
    await selectWord();
    await chip.waitFor();
    await chip.evaluate(node => node.click());
    assert.equal(await menu.isVisible(), false);
    assert.deepEqual(await emitted(), []);
    await chip.click();
    await host.locator('#append-reason').getByText('请先在画布中选中一个可编辑的生图节点。').waitFor();
    assert.equal(await append.isDisabled(), true);
    const createText = host.locator('#new-text');
    await createText.evaluate(node => node.click());
    assert.equal((await emitted()).length, 1);
    await createText.click();
    await host.locator('#feedback').getByText('已复制到画布。').waitFor();
    assert.deepEqual(await emitted(), [
      {channel, action: 'status', site: 'chatgpt.com'},
      {channel, action: 'new-text', site: 'chatgpt.com', text: 'Firefly'},
    ]);

    await canvas.evaluate(() => { window.fixture.canAppend = true; });
    await selectWord();
    await chip.click();
    await append.click();
    await host.locator('#feedback').getByText('已复制到画布。').waitFor();
    assert.deepEqual((await emitted()).slice(2), [
      {channel, action: 'status', site: 'chatgpt.com'},
      {channel, action: 'append-prompt', site: 'chatgpt.com', text: 'Firefly'},
    ]);
    await chat.locator('#image').hover();
    await imageChip.waitFor();
    await imageChip.evaluate(node => node.click());
    assert.equal((await emitted()).length, 4);
    await imageChip.click();
    await host.locator('#feedback').getByText('已复制到画布。').waitFor();
    assert.deepEqual((await emitted())[4], {channel, action: 'new-image', site: 'chatgpt.com', mime: 'image/gif', bytes: gif});
    assert.deepEqual(await otherPort.evaluate(() => window.fixture.captured), []);
    assert.deepEqual(await otherPath.evaluate(() => window.fixture.captured), []);

    const duplicate = await context.newPage();
    await duplicate.goto(`${canvasURL}?other=1`);
    await chat.bringToFront();
    await selectWord();
    await chip.click();
    await host.getByText('打开了多个 Firefly 画布，请关闭多余的标签页后重试。').waitFor();
    assert.equal((await emitted()).length, 5);
    assert.deepEqual(await duplicate.evaluate(() => window.fixture.captured), []);

    const options = await context.newPage();
    await options.goto(worker.url().replace('background.js', 'options.html'));
    await options.waitForFunction(() => document.getElementById('firefly-origin').value === 'http://127.0.0.1:3000', undefined, {timeout: 5000});
    await options.locator('#firefly-origin').fill('http://127.0.0.1:3001');
    await options.getByRole('button', {name: '授权并保存'}).click();
    await options.waitForFunction(() => document.getElementById('result').textContent, undefined, {timeout: 5000});
    assert.equal(await options.locator('#result').textContent(), '已保存，请刷新 Firefly 画布标签页。');
    assert.equal((await worker.evaluate(() => chrome.scripting.getRegisteredContentScripts()))[0]?.id, 'firefly-chat-capture-canvas');
    await otherPort.reload();
    await chat.bringToFront();
    await selectWord();
    await chip.click();
    await host.locator('#append-reason').getByText('请先在画布中选中一个可编辑的生图节点。').waitFor();
    await createText.click();
    await host.locator('#feedback').getByText('已复制到画布。').waitFor();
    assert.deepEqual(await otherPort.evaluate(() => window.fixture.captured), [
      {channel, action: 'status', site: 'chatgpt.com'},
      {channel, action: 'new-text', site: 'chatgpt.com', text: 'Firefly'},
    ]);
    assert.equal((await emitted()).length, 5);
    await chat.locator('#https-image').hover();
    await imageChip.waitFor();
    assert.deepEqual(imageFetches, []);
    await imageChip.click();
    await host.locator('#feedback').getByText('已复制到画布。').waitFor();
    assert.deepEqual(imageFetches, [['GET', undefined, undefined]]);
    assert.deepEqual((await otherPort.evaluate(() => window.fixture.captured))[2],
      {channel, action: 'new-image', site: 'chatgpt.com', mime: 'image/gif', bytes: gif});
  } finally {
    await context.close();
  }
});
