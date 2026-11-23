import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {createCanvasContext, JS_DIR} from './harness.mjs';

const flush = () => new Promise(resolve => setImmediate(resolve));
const id = value => 'sha256:' + value.repeat(64);
const matching = {backend:{schema:1,loaded:id('a'),source:id('a'),restart_required:false}};
const stale = {backend:{schema:1,loaded:id('a'),source:id('b'),restart_required:true}};
function setup(stored = [], denyStorage = false) {
    const {ctx,harness} = createCanvasContext();
    for (const [key,value] of stored) harness.store.set(key,value);
    if (denyStorage) {
        for (const method of ['getItem','setItem','removeItem']) ctx.localStorage[method] = () => { throw new Error('denied'); };
    }
    ctx.AbortController = AbortController;
    const create = ctx.document.createElement;
    ctx.document.createElement = tag => {
        const el = create(tag);
        el.replaceChildren = (...children) => {
            el.children = [];
            children.forEach(child => el.appendChild(child));
        };
        return el;
    };
    const calls = [];
    ctx.fetch = (url,options) => new Promise((resolve,reject) => calls.push({url,options,resolve,reject}));
    for (const file of ['i18n-core.js','i18n/common.js','service-version.js']) {
        vm.runInContext(readFileSync(path.join(JS_DIR,file),'utf8'),ctx,{filename:file});
    }
    const notice = () => ctx.document.body.children.find(el => el.id === 'serviceVersionNotice');
    const text = () => notice()?.children.map(el => el.textContent).join('\n') || '';
    const reply = async (data,status=200) => {
        calls.at(-1).resolve({ok:status === 200,status,json:async()=>data}); await flush();
    };
    const focus = () => harness.windowListeners.get('focus').forEach(listener=>listener(new ctx.Event('focus')));
    return {ctx,harness,calls,notice,text,reply,focus};
}

test('matching service has no notice and probes only its own origin',async()=>{
    const h=setup();
    assert.equal(h.calls[0].url,'/api/app-info');
    assert.equal(h.calls[0].options.cache,'no-store');
    await h.reply(matching);
    assert.ok(!h.notice() || h.notice().hidden);
});
for(const [name,payload,status] of [['stale',stale,200],['old endpoint',{},404],['old fields',{version:'old'},200]]){
    test(`${name}: restart notice persists, reload/offline retains it, matching clears it`,async()=>{
        const h=setup(); await h.reply(payload,status);
        assert.equal(h.notice().hidden,false);
        assert.match(h.text(),/服务需要重启/);
        assert.match(h.text(),/重启服务/); assert.match(h.text(),/run.bat/);
        assert.doesNotMatch(h.text(),/Not Found|sha256:/);
        assert.equal(h.notice().getAttribute('role'),'alert');
        assert.equal(h.harness.timers.filter(timer=>!timer.cancelled).length,0,'no auto-dismiss timer');
        const reload=setup(h.harness.store);
        assert.match(reload.text(),/服务需要重启/);
        reload.calls[0].reject(new Error('offline')); await flush();
        assert.match(reload.text(),/服务需要重启/);
        reload.focus(); await reload.reply(matching);
        assert.equal(reload.notice().hidden,true);
        assert.equal(reload.harness.store.has('firefly.backendRestartNotice.v1'),false);
    });
}
test('unrelated errors are unverified, not proof of an update, retry is explicit',async()=>{
    for(const outcome of ['network','503','malformed','partial']){
        const h=setup();
        if(outcome==='network'){ h.calls[0].reject(new Error('<img src=x onerror=alert(1)>')); await flush(); }
        else await h.reply(outcome==='partial'?{backend:{schema:1}}:[],outcome==='503'?503:200);
        assert.match(h.text(),/服务版本未确认/);
        assert.doesNotMatch(h.text(),/后端已更新|服务需要重启|<img/);
        h.notice().children.at(-1).click();
        assert.equal(h.calls.length,2);
        await h.reply(matching); assert.equal(h.notice().hidden,true);
    }
});
test('storage denial keeps actionable notice in memory and never inserts server content',async()=>{
    const h=setup([],true);
    await h.reply({...stale,name:'<script>bad</script>'});
    assert.match(h.text(),/后端已更新，需要重启服务/);
    assert.doesNotMatch(h.text(),/<script>|bad/);
    assert.ok(h.notice().children.every(el=>!el.innerHTML));
});
test('a timed out probe stays unverified and periodic probes detect later source updates',async()=>{
    const h=setup();
    h.calls[0].options.signal.addEventListener('abort',()=>h.calls[0].reject(new Error('controlled abort')));
    h.harness.advanceTimers(); await flush();
    assert.match(h.text(),/服务版本未确认/);
    assert.doesNotMatch(h.text(),/后端已更新/);
    h.harness.intervals[0].fn(); await h.reply(matching);
    assert.equal(h.notice().hidden,true);
    h.harness.intervals[0].fn(); await h.reply(stale);
    assert.match(h.text(),/后端已更新，需要重启服务/);
});
