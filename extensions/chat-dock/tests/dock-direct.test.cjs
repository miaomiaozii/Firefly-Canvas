const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

const origin = 'http://127.0.0.1:3000';
const extension = 'chrome-extension://example/';
const project = '?id=alpha&project=alpha&project_mode=1';
const parent = {documentUrl: `${extension}canvas.html${project}&direct=1`, documentId: 'shell'};
const child = {url: `${origin}/static/smart-canvas.html${project}`, origin, tab: {id: 3}, frameId: 2, documentId: 'child', documentLifecycle: 'active'};

function loadDirect({contexts = () => [parent], verifiedSender = child, expiry = false, permit = true} = {}) {
  const sent = [];
  const filters = [];
  const root = vm.createContext({URL, URLSearchParams, setTimeout: (callback, delay) => setTimeout(callback, expiry ? 0 : delay), clearTimeout,
    crypto: {randomUUID: () => 'nonce'},
    chrome: {runtime: {
      getURL: path => `${extension}${path}`,
      getContexts: async filter => { filters.push(JSON.parse(JSON.stringify(filter))); return contexts(filters.length); },
      sendMessage: async data => {
        sent.push(data);
        if (data.action === root.FireflyChatDockDirect.probe) {
          root.FireflyChatDockDirect.acceptProof({action: data.action, nonce: data.nonce}, verifiedSender);
        }
        return true;
      },
    }},
  });
  for (const file of ['protocol.js', 'settings-hello.js', 'dock-direct.js']) {
    vm.runInContext(readFileSync(join(__dirname, '..', file), 'utf8'), root, {filename: file});
  }
  const direct = root.FireflyChatDockDirect;
  return {sent, filters, run: (sender = child) => direct.command('toggle-dock', sender, origin, async () => permit)};
}

test('scoped direct command addresses precisely one verified shell document', async () => {
  const {run, sent, filters} = loadDirect();
  assert.equal(await run(), true);
  assert.deepEqual(filters, Array.from({length: 2}, () => ({contextTypes: ['TAB'], tabIds: [3], frameIds: [0]})));
  assert.equal(sent.length, 2);
  assert.equal(sent[1].documentId, 'shell');
  assert.equal(sent[1].nonce, sent[0].nonce);
  assert.equal(sent[1].command, 'toggle-dock');
});

test('ordinary, stale, or unrelated documents cannot toggle the direct shell', async () => {
  const {run, sent} = loadDirect();
  for (const sender of [{...child, frameId: 0}, {...child, documentLifecycle: 'cached'}, {...child, url: child.url.replace('alpha', 'beta')}]) {
    assert.equal(await run(sender), false);
  }
  assert.equal(sent.length, 0);
  for (const documentUrl of [parent.documentUrl.replace('&direct=1', ''), parent.documentUrl.replace('canvas.html', 'options.html')]) {
    const instance = loadDirect({contexts: () => [{...parent, documentUrl}]});
    assert.equal(await instance.run(), false);
    assert.equal(instance.sent.length, 0);
  }
});

test('misbound proof, revoked origin, and replaced shell produce no command', async () => {
  for (const options of [
    {verifiedSender: {...child, documentId: 'different'}, expiry: true},
    {permit: false},
    {contexts: count => [{...parent, documentId: count === 1 ? 'shell' : 'replacement'}]},
  ]) {
    const instance = loadDirect(options);
    assert.equal(await instance.run(), false);
    assert.equal(instance.sent.length, 1);
  }
});
