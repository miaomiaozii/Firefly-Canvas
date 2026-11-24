import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {loadModules,evaluate} from './harness.mjs';
import {editor} from './smart-human-handlers-harness-392.mjs';
import {Element} from './history-dom-493.mjs';

const savedFixture=JSON.parse(readFileSync(new URL('./fixtures/retrieval-canonical-case10-493.json',import.meta.url),'utf8'));
const cases=process.env.FIREFLY_HISTORY_CANONICAL
    ? JSON.parse(readFileSync(process.env.FIREFLY_HISTORY_CANONICAL,'utf8'))
    : [{canvas:savedFixture,listing:{groups:[savedFixture.history[0].download]},saved:2}];
for(const fixture of cases) test(`restarted canonical: ${fixture.saved} saved, ${4-fixture.saved} missing have separate bound rail cells`,async()=>{
    const rail=new Element(), modal=new Element(), panel=new Element(), badge=new Element('button');
    const {ctx}=editor(fixture.canvas.nodes,h=>{h.elementsById.set('smartHistoryList',rail);h.elementsById.set('smartHistoryModal',modal);});
    loadModules(ctx,['canvas-result-retrieval.js']);
    Object.assign(ctx,{smartHistorySizeSummary:()=>'',smartHistoryResolutionTier:()=>'',historyLogModelMeta:()=>({}),formatRunDuration:()=>'',ico:()=>'',getComputedStyle:()=>({gap:'8px'})});
    evaluate(ctx,`canvas=${JSON.stringify(fixture.canvas)}`);
    fixture.canvas=ctx.hydrateProjectMediaReferences(fixture.canvas);
    const document=ctx.document, originalGet=document.getElementById;
    document.getElementById=id=>({smartRetrievalToggle:badge,smartRetrievalPanel:panel}[id]||originalGet(id));
    document.createElement=tag=>new Element(tag);
    evaluate(ctx,`canvas=${JSON.stringify(fixture.canvas)};canvasState.nodes=canvas.nodes`);
    const log=fixture.canvas.history[0], group=fixture.listing.groups[0], calls=[];
    const api=ctx.canvasResultRetrieval;
    api.configure({getProjectId:()=>fixture.canvas.id,getHistory:()=>fixture.canvas.history,adoptCanonical:async()=>true,fetch:async(url,options)=>{
        calls.push({url,options});return {ok:true,json:async()=>options.method?group:fixture.listing};
    }});
    await api.refresh();ctx.renderSmartCanvasHistory();
    const cells=rail.querySelectorAll('.log-item');
    assert.equal(cells.length,4);
    assert.equal(new Set(cells.map(cell=>cell.dataset.historyCell)).size,4);
    assert.ok(cells.every(cell=>cell.querySelectorAll('.log-media-card').length===1),'each legacy full-width gallery contains one occurrence, never four clipped slides');
    const saved=cells.flatMap(cell=>cell.querySelectorAll('.log-media-card.is-image'));
    assert.deepEqual(saved.map(card=>card.dataset.outputId),log.outputs.map(output=>output.output_id));
    const missing=cells.flatMap(cell=>cell.querySelectorAll('.retrieval-slot'));
    assert.equal(missing.length,4-fixture.saved);
    assert.deepEqual(missing.map(slot=>slot.dataset.retrievalSlot),group.items.slice(fixture.saved).map(item=>item.id));
    assert.equal(rail.querySelectorAll('[data-retrieval-all]').length,fixture.saved===3?0:1);
    if(fixture.saved===3){
        assert.equal(rail.querySelectorAll('[data-retrieval-item]').length,0);
        assert.match(missing[0].querySelector('[role="status"]').textContent,/无法取回|unavailable/);
        assert.doesNotMatch(rail.querySelector('.retrieval-summary').querySelector('strong').textContent,/待取回|awaiting retrieval/);
    }else{
        for(const slot of missing){
            const button=slot.querySelector('[data-retrieval-item]');assert.equal(typeof button.onclick,'function');
            await button.onclick({stopPropagation(){}});
            const posted=calls.filter(call=>call.options.method==='POST').at(-1);
            assert.deepEqual(JSON.parse(posted.options.body).item_ids,[slot.dataset.retrievalSlot]);
        }
    }
    badge.onclick();
    assert.equal(panel.querySelector('h3').textContent,log.prompt);
    assert.equal(panel.querySelector('h3').children.length,0,'user title is escaped, not executable markup');
    assert.equal(panel.querySelector('section').dataset.retrievalGroup,group.history_id);
    const unchanged=JSON.stringify(fixture.canvas);
    const legacy={...log,id:'legacy',task_id:'no-trusted-queue',outputs:log.outputs,client_batchTotal:99,pending:99};
    assert.equal(ctx.smartHistoryDisplayLogs([legacy]).length,1);
    const legacyRoot=new Element();legacyRoot.innerHTML=ctx.smartHistoryLogCardHtml(legacy,0);
    assert.equal(legacyRoot.querySelectorAll('.log-item').length,1);
    assert.equal(legacyRoot.querySelectorAll('.retrieval-slot').length,0);
    assert.equal(JSON.stringify(fixture.canvas),unchanged);
    const deleted={...log,deletedOutputTombstones:[{deleted_output_id:group.items.at(-1).output_id}]};
    const deletedRoot=new Element();deletedRoot.innerHTML=ctx.smartHistoryLogCardHtml(deleted,0);
    assert.equal(deletedRoot.querySelectorAll('.log-media-card').length,3);
    assert.ok(deletedRoot.querySelectorAll('[data-retrieval-item]').every(button=>button.dataset.retrievalItem!==group.items.at(-1).id));
    log.deletedOutputTombstones=deleted.deletedOutputTombstones;
    const postsBefore=calls.filter(call=>call.options.method==='POST').length;
    await api.retrieve(group.history_id,[group.items.at(-1).id],true);
    assert.equal(calls.filter(call=>call.options.method==='POST').length,postsBefore,'canonical deletion also vetoes stale project-panel actions');
    delete log.deletedOutputTombstones;
    if(fixture.saved===0){
        fixture.listing.groups=[{...group,items:Array.from({length:200},(_,index)=>({...group.items[0],id:`item-${index}`,output_id:`output-${index}`,index}))}];
        await api.refresh();ctx.renderSmartCanvasHistory();
        assert.equal(rail.querySelectorAll('.log-item').length,140,'occurrences, not generation groups, consume the existing DOM budget');
    }
});
