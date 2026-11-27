const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

const read = name => readFileSync(join(__dirname, '..', name), 'utf8');
require('../protocol.js');
require('../framing.js');
const P = globalThis.FireflyChatProtocol;
const F = globalThis.FireflyChatFraming;
const framingSites = ['chatgpt.com', 'gemini.google.com', 'chat.deepseek.com', 'chat.qwen.ai'];
const manifest = JSON.parse(read('manifest.json'));
const extensionId = 'ecbjgicacjhegoijmnkijlgeagafgogf';
const sites = ['chatgpt.com', 'gemini.google.com', 'chat.deepseek.com', 'chat.qwen.ai', 'www.doubao.com', 'www.kimi.com', 'yuanbao.tencent.com'];
const capture = (action = 'status', fields = {}) => ({channel: P.CHANNEL, action, site: sites[0], ...fields});
const canvasURL = `${P.DEFAULT_ORIGIN}/static/smart-canvas.html`;

test('manifest has the stable ID, exact provider patterns, and no external message entry', () => {
  const digest = createHash('sha256').update(Buffer.from(manifest.key, 'base64')).digest('hex').slice(0, 32);
  assert.equal(digest.replace(/./g, digit => String.fromCharCode(97 + parseInt(digit, 16))), extensionId);
  assert.deepEqual(manifest.content_scripts[0].matches, sites.map(site => `https://${site}/*`));
  assert.deepEqual(manifest.permissions, ['storage', 'scripting', 'declarativeNetRequest']);
  // DNR modifyHeaders needs explicit host permissions; content-script matches alone do not apply it.
  assert.deepEqual(manifest.host_permissions, ['http://127.0.0.1/*', ...framingSites.map(site => `https://${site}/*`)]);
  assert.equal(manifest.externally_connectable, undefined);
  assert.equal(manifest.declarative_net_request, undefined);
});

test('exact chat and canvas origins and exact canvas path', () => {
  for (const site of sites) assert.equal(P.chatSite(`https://${site}/a?q=test`), site);
  for (const url of ['http://chatgpt.com/a', 'https://x.chatgpt.com/', 'https://chatgpt.com.evil.test/',
    'https://chatgpt.com:8443/', 'https://chatgpt.com./', 'https://user@chatgpt.com/']) assert.equal(P.chatSite(url), null, url);
  assert.equal(P.isCanvasURL(`${canvasURL}?project=x#test`, P.DEFAULT_ORIGIN), true);
  for (const url of [canvasURL.replace('3000', '3001'), canvasURL.replace('127.0.0.1', 'localhost'),
    canvasURL.replace('http:', 'https:'), `${canvasURL}.bak`, `${canvasURL}/`, canvasURL.replace('/static', '')]) {
    assert.equal(P.isCanvasURL(url, P.DEFAULT_ORIGIN), false, url);
  }
  assert.equal(P.parseOrigin('https://firefly.example:8443/'), 'https://firefly.example:8443');
  for (const url of ['file:///c:/', 'https://user@x.test', 'https://x.test?q=1', 'https://x.test#f', 'https://x.test/extra']) {
    assert.equal(P.parseOrigin(url), null, url);
  }
});

test('strict message keys, action, sender hostname, text and decoded byte cap', () => {
  assert.equal(P.validCapture(capture()), true);
  assert.equal(P.validCapture(capture('append-prompt', {text: '字'.repeat(20_000)})), true);
  assert.equal(P.validCapture(capture('append-prompt', {text: '字'.repeat(20_001)})), false);
  assert.equal(P.validCapture(capture('new-text', {text: '  '})), false);
  assert.equal(P.validCapture(capture('search', {text: 'x'})), false);
  assert.equal(P.validCapture(capture('status', {text: 'wrong'})), false);
  assert.equal(P.validCapture(capture('new-text', {text: 'word', id: 123})), false);
  assert.equal(P.validCapture(capture(), sites[1]), false);
  const allowed = Buffer.alloc(P.MAX_BYTES).toString('base64');
  const extra = Buffer.alloc(P.MAX_BYTES + 1).toString('base64');
  for (const mime of ['image/png', 'image/jpeg', 'image/gif', 'image/webp']) {
    assert.equal(P.validCapture(capture('new-image', {mime, bytes: 'AAAA'})), true);
  }
  assert.equal(P.validCapture(capture('new-image', {mime: 'image/png', bytes: allowed})), true);
  assert.equal(P.validCapture(capture('new-image', {mime: 'image/png', bytes: extra})), false);
  assert.equal(P.validCapture(capture('new-image', {mime: 'image/png', bytes: 'AA=A'})), false);
  assert.equal(P.validCapture(capture('new-image', {mime: 'image/svg+xml', bytes: 'AAAA'})), false);
});

test('only one exact canvas, with unique active preference and no arbitrary fallback', () => {
  const tab = (id, active, fields = {}) => ({id, active, url: canvasURL, ...fields});
  assert.equal(P.chooseCanvas([tab(1, false)], P.DEFAULT_ORIGIN).id, 1);
  assert.equal(P.chooseCanvas([tab(1, false), tab(2, true)], P.DEFAULT_ORIGIN).id, 2);
  assert.equal(P.chooseCanvas([tab(1, true), tab(2, true)], P.DEFAULT_ORIGIN).error, 'CHOOSE_CANVAS');
  assert.equal(P.chooseCanvas([tab(1, false), tab(2, false)], P.DEFAULT_ORIGIN).error, 'CHOOSE_CANVAS');
  assert.equal(P.chooseCanvas([tab(1, false), tab(2, true, {url: `${canvasURL}.bak`})], P.DEFAULT_ORIGIN).id, 1);
  assert.equal(P.chooseCanvas([tab(1, true, {pendingUrl: 'https://example.test/'})], P.DEFAULT_ORIGIN).id, undefined);
});

const plain = value => JSON.parse(JSON.stringify(value));

function listeners() {
  const event = {addListener(listener) { event.run = listener; }};
  return event;
}

function sandboxChrome() {
  const context = vm.createContext({URL, setTimeout, clearTimeout});
  context.importScripts = (...names) => names.forEach(name => vm.runInContext(read(name), context));
  const state = {stored: {}, grants: new Set(manifest.host_permissions), sent: [], registrations: [], tabs: [], rules: [], failRules: false};
  context.chrome = {
    runtime: {
      id: extensionId, getURL: path => `chrome-extension://${extensionId}/${path}`,
      onMessage: listeners(), onInstalled: listeners(), onStartup: listeners(),
    },
    storage: {local: {get: async () => state.stored, set: async data => { state.rulesAtSet = state.rules.map(rule => rule.id); state.stored = {...state.stored, ...data}; }}},
    declarativeNetRequest: {
      updateDynamicRules: async ({removeRuleIds = [], addRules = []}) => {
        if (state.failRules) throw new Error('rule limit');
        state.rules = [...state.rules.filter(rule => !removeRuleIds.includes(rule.id)), ...JSON.parse(JSON.stringify(addRules))];
      },
    },
    permissions: {
      contains: async ({origins}) => origins.every(origin => state.grants.has(origin)),
      remove: async ({origins}) => origins.every(origin => state.grants.delete(origin)),
      onRemoved: listeners(),
    },
    tabs: {
      query: async () => state.tabs,
      sendMessage: async (...args) => { state.sent.push(args); return {ok: true, canAppend: false, extra: 'strip'}; },
    },
    scripting: {
      getRegisteredContentScripts: async () => state.registrations,
      registerContentScripts: async items => { state.registrations = items; },
      unregisterContentScripts: async () => { state.registrations = []; },
    },
  };
  return {context, state};
}

function receive(context, data, sender) {
  return new Promise(resolve => {
    if (context.chrome.runtime.onMessage.run(data, sender, value => resolve(JSON.parse(JSON.stringify(value)))) !== true) resolve(undefined);
  });
}

const chatSender = {id: extensionId, frameId: 0, url: 'https://chatgpt.com/c/example', tab: {id: 34}};

test('background rejects bad senders/actions and dispatches exactly one top-frame message', async () => {
  const {context, state} = sandboxChrome();
  vm.runInContext(read('background.js'), context);
  state.tabs = [{id: 1, url: canvasURL}, {id: 2, url: `${canvasURL}?test`, active: true}];
  for (const sender of [{...chatSender, id: 'other'}, {...chatSender, frameId: 1}, {...chatSender, tab: undefined},
    {...chatSender, url: 'https://evil.chatgpt.com/'}, {...chatSender, url: 'https://gemini.google.com/'}]) {
    assert.equal(await receive(context, capture(), sender), undefined);
  }
  assert.equal(await receive(context, capture('send'), chatSender), undefined);
  assert.deepEqual(state.sent, []);
  assert.deepEqual(await receive(context, capture(), chatSender), {ok: true, canAppend: false});
  assert.deepEqual(JSON.parse(JSON.stringify(state.sent)), [[2, capture(), {frameId: 0}]]);
  state.tabs[0].active = true;
  assert.deepEqual(await receive(context, capture(), chatSender), {ok: false, error: 'CHOOSE_CANVAS'});
  assert.equal(state.sent.length, 1);
});

test('custom relay requires options sender and a grant; revoked grant does not fall back', async () => {
  const {context, state} = sandboxChrome();
  vm.runInContext(read('background.js'), context);
  const origin = 'https://canvas.example:1234';
  const option = {id: extensionId, frameId: 0, url: context.chrome.runtime.getURL('options.html')};
  const command = {action: P.SET_ORIGIN, origin};
  const permission = 'https://canvas.example/*';
  assert.equal(await receive(context, command, chatSender), undefined);
  assert.deepEqual(await receive(context, command, option), {ok: false, error: 'PERMISSION_DENIED'});
  assert.equal(state.registrations.length, 0);
  state.grants.add(permission);
  assert.deepEqual(await receive(context, command, option), {ok: true});
  assert.deepEqual([...state.registrations[0].matches], ['https://canvas.example/static/smart-canvas.html*']);
  state.tabs = [{id: 1, url: canvasURL}, {id: 2, url: `${origin}/static/smart-canvas.html`}];
  assert.equal((await receive(context, capture(), chatSender)).ok, true);
  assert.equal(state.sent[0][0], 2);
  state.grants.delete(permission);
  context.chrome.permissions.onRemoved.run();
  assert.deepEqual(await receive(context, capture(), chatSender), {ok: false, error: 'PERMISSION_DENIED'});
  assert.equal(state.sent.length, 1);
  assert.equal(state.registrations.length, 0);
});

test('isolated relay checks sender and exact origin before transferring a same-origin port', async () => {
  const {context, state} = sandboxChrome();
  context.window = {postMessage(data, targetOrigin, ports) {
    context.posts.push({data, targetOrigin, ports});
    ports[0].postMessage({ok: true, canAppend: true});
  }};
  context.window.top = context.window;
  context.posts = [];
  context.location = new URL(canvasURL);
  context.MessageChannel = MessageChannel;
  vm.runInContext(read('protocol.js'), context);
  vm.runInContext(read('canvas-relay.js'), context);
  const serviceWorker = {id: extensionId, url: context.chrome.runtime.getURL('background.js')};
  assert.equal(await receive(context, capture(), chatSender), undefined);
  assert.equal(await receive(context, capture(), {...serviceWorker, id: 'wrong'}), undefined);
  assert.equal(await receive(context, capture('other'), serviceWorker), undefined);
  state.stored.fireflyOrigin = 'http://127.0.0.1:9010';
  assert.deepEqual(await receive(context, capture(), serviceWorker), {ok: false, error: 'CANVAS_NOT_READY'});
  assert.equal(context.posts.length, 0);
  state.stored.fireflyOrigin = P.DEFAULT_ORIGIN;
  assert.deepEqual(await receive(context, capture(), serviceWorker), {ok: true, canAppend: true});
  assert.equal(context.posts.length, 1);
  assert.equal(context.posts[0].targetOrigin, P.DEFAULT_ORIGIN);
  assert.deepEqual(context.posts[0].data, capture());
  assert.equal(context.posts[0].ports.length, 1);
  context.posts[0].ports[0].close();
});

test('framing rules exist only for switched-on sites, only for sub frames under this extension', async () => {
  const {context, state} = sandboxChrome();
  vm.runInContext(read('background.js'), context);
  const option = {id: extensionId, frameId: 0, url: context.chrome.runtime.getURL('options.html')};
  const command = (site, enabled) => ({action: F.SET_FRAMING, site, enabled});
  assert.deepEqual(Object.keys(F.SITES), framingSites);
  for (const [data, sender] of [[command('chatgpt.com', true), chatSender], [command('claude.ai', true), option],
    [command('www.kimi.com', true), option], [command('chatgpt.com', 'yes'), option],
    [{...command('chatgpt.com', true), extra: 1}, option], [command('chatgpt.com', true), {...option, frameId: 2}],
    [command('chatgpt.com', true), {...option, url: context.chrome.runtime.getURL('canvas.html')}]]) {
    assert.equal(await receive(context, data, sender), undefined);
  }
  assert.deepEqual(state.rules, []);
  state.stored[P.ORIGIN_KEY] = 'http://127.0.0.1:3000';
  assert.deepEqual(await receive(context, command('chatgpt.com', true), option), {ok: true});
  // The panel reloads on the storage change, so the rule must already exist when the switch is stored.
  assert.deepEqual(plain(state.rulesAtSet), [5151]);
  assert.deepEqual(await receive(context, command('gemini.google.com', true), option), {ok: true});
  assert.equal(state.stored[P.ORIGIN_KEY], 'http://127.0.0.1:3000');
  assert.deepEqual(plain(state.stored[F.KEY]), {'chatgpt.com': true, 'gemini.google.com': true});
  const [chat, gemini] = state.rules;
  assert.deepEqual(chat, {id: 5151, priority: 1,
    action: {type: 'modifyHeaders', responseHeaders: [{header: 'x-frame-options', operation: 'remove'},
      {header: 'content-security-policy', operation: 'set', value: `frame-ancestors chrome-extension://${extensionId}`}]},
    condition: {urlFilter: '|https://chatgpt.com/', requestDomains: ['chatgpt.com'], resourceTypes: ['sub_frame'], topDomains: [extensionId]}});
  // Gemini only blocks with X-Frame-Options; its own CSP stays in force.
  assert.deepEqual(gemini.action.responseHeaders, [{header: 'x-frame-options', operation: 'remove'}]);
  assert.deepEqual(gemini.condition.topDomains, [extensionId]);
  assert.deepEqual(await receive(context, command('chatgpt.com', false), option), {ok: true});
  assert.deepEqual(state.rules.map(rule => rule.id), [5152]);
  assert.deepEqual(plain(state.stored[F.KEY]), {'gemini.google.com': true});
});

test('a failed rule update leaves the switch off and startup rebuilds rules from stored switches only', async () => {
  const {context, state} = sandboxChrome();
  vm.runInContext(read('background.js'), context);
  const option = {id: extensionId, frameId: 0, url: context.chrome.runtime.getURL('options.html')};
  state.failRules = true;
  assert.deepEqual(await receive(context, {action: F.SET_FRAMING, site: 'chat.qwen.ai', enabled: true}, option),
    {ok: false, error: 'CAPTURE_FAILED'});
  assert.equal(state.stored[F.KEY], undefined);
  state.failRules = false;
  state.stored[F.KEY] = {'chat.deepseek.com': true, 'claude.ai': true, 'chat.qwen.ai': 'true'};
  state.rules = [{id: 5151, stale: true}, {id: 5154, stale: true}];
  context.chrome.runtime.onStartup.run();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.deepEqual(state.rules.map(rule => rule.id), [5153]);
});
