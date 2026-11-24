import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {editor} from './smart-human-handlers-harness-392.mjs';
import {loadModules, evaluate, fromContext, ROOT} from './harness.mjs';
import {Element} from './history-dom-493.mjs';

const read = file => readFileSync(path.join(ROOT,file),'utf8');
const css = read('static/css/canvas-result-retrieval.css');
const canvasCss = read('static/css/smart-canvas.css');
function rule(source, selector) {
    const blocks = [...source.replace(/\/\*[\s\S]*?\*\//g,'').matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(m => m[1].trim() === selector);
    assert.equal(blocks.length,1,selector);
    return Object.fromEntries(blocks[0][2].split(';').filter(s => s.trim()).map(s => s.trim().split(/\s*:\s*/)));
}
const image = i => ({url:`/offline/${i}.png`,width:1024,height:768,output_id:`out-${i}`,output_order:i});
function fixture(nodes=[]) {
    const e=editor(nodes);
    loadModules(e.ctx,['canvas-node-ops.js','canvas-agent-tui.js']);
    evaluate(e.ctx,read('static/js/smart-canvas.js').split(/\r?\n/).filter(line=>/^const MEDIA_(NODE_DEFAULT_SCALE|GROUP_THUMB_BASE|GROUP_MAX_VISIBLE_ROWS) =/.test(line)).join('\n'));
    return e;
}
async function retrieval(count, lang='en', bytes=0) {
    const node = {id:'result',type:'smart-image',pending:count,_batchTotal:count,_generationAspect:{w:4,h:3},images:[]};
    const e = fixture([node]);
    loadModules(e.ctx,['canvas-result-retrieval.js']);
    e.ctx.StudioI18n = {lang:() => lang};
    const group = {project_id:'unit-392',history_id:'history',task_id:'task',node_id:node.id,items:Array.from({length:count},(_,index) => ({id:`item-${index}`,index,output_id:`out-${index}`,status:'failed',can_retrieve:true,remaining_seconds:90,downloaded_bytes:bytes,total_bytes:1024,error:'Long <failure> '.repeat(80)}))};
    const api = e.ctx.canvasResultRetrieval;
    api.configure({getProjectId:() => 'unit-392',getHistory:() => [{id:'history',task_id:'task',result_node_id:node.id}],fetch:async() => ({ok:true,json:async() => ({groups:[group]})})});
    await api.refresh();
    return {...e,node,api,group};
}

test('actual singleton renderer fits the inner flex viewport; all initial information remains separate and present', async () => {
    for(const lang of ['zh','en']) {
        const {ctx,node} = await retrieval(1,lang);
        const layout = ctx.imageLayout([],1,node);
        assert.deepEqual([layout.width,layout.height],[292,227]);
        const root = new Element(); root.innerHTML = ctx.nodeBodyHtml(node,layout);
        const single = root.querySelector('.retrieval-single'), slot = root.querySelector('.retrieval-slot');
        assert.ok(single);
        assert.equal(slot.getAttribute('style'),'width:100%;height:100%');
        assert.doesNotMatch(root.innerHTML,/width:292px|height:227px/);
        assert.doesNotMatch(slot.children.map(child=>child.textContent || '').join(' '),/\d+%/);
        assert.deepEqual(slot.children.map(c => c.tagName),['SPAN','SPAN','SMALL','SMALL','DETAILS']);
        const action = slot.querySelector('.retrieval-action');
        assert.deepEqual(action.children.map(c => c.tagName),['BUTTON','SPAN']);
        assert.equal(action.querySelector('.retrieval-no-charge').textContent,lang === 'zh' ? '不扣费' : 'No charge');
        assert.equal(slot.querySelector('button').disabled,false);
        assert.equal(slot.querySelectorAll('small')[0].textContent,lang === 'zh' ? '剩余 2 分钟' : '2 minutes remaining');
        assert.equal(slot.querySelectorAll('small')[1].textContent,'0 / 1024 B');
        assert.equal(slot.querySelector('details').hasAttribute('open'),false);
        assert.match(slot.querySelector('details').querySelector('span').textContent,/Long <failure>/);
    }
});

test('singleton CSS allocates the footer remainder, not outer-frame dimensions, without shrinking type or CTA', () => {
    const grid = rule(css,'.retrieval-single > .thumb-grid');
    assert.deepEqual(grid,{'grid-template-columns':'minmax(0,1fr)','grid-template-rows':'minmax(0,1fr)',width:'100%',padding:'0','max-height':'none'});
    assert.equal(rule(css,'.retrieval-single .retrieval-slot')['min-height'],'0');
    assert.equal(rule(css,'.retrieval-single .retrieval-slot > *')['flex-shrink'],'0');
    const footer = rule(css,'.retrieval-node > .retrieval-summary');
    const gap = Number.parseInt(rule(canvasCss,'.pending-batch-stack').gap);
    const viewport = 227 - 2*16 - Number.parseInt(footer.height) - gap;
    assert.equal(viewport,147);
    // Source budget only: measured normal 16px text (20), small (17), CTA (32), closed disclosure (20).
    // No VM test claims native computed layout; the parent must remeasure actual fonts and scroll extents.
    const closedContent = 20+32+17+17+20+4*Number.parseInt(rule(css,'.retrieval-single .retrieval-slot').gap)+2;
    assert.equal(closedContent,124); assert.ok(closedContent+20 <= viewport,'two-line expired/recoverable status also fits');
    assert.equal(rule(css,'.retrieval-single .retrieval-slot details[open] span').position,'absolute');
    assert.equal(rule(css,'.retrieval-slot').overflow,'auto');
    assert.equal(rule(css,'.retrieval-single .retrieval-slot')['border-radius'],'12px');
    for(const [selector,body] of [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(m=>[m[1],m[2]])) {
        if(selector.includes('.retrieval-single')) assert.doesNotMatch(body,/font|scale|zoom|line-clamp|overflow\s*:\s*hidden/);
    }
});

test('known bytes including zero are truthful; unknown progress does not invent byte counts or percent', async () => {
    const {api,node,ctx,group} = await retrieval(1);
    const layout = ctx.imageLayout([],1,node);
    for(const value of [undefined,0,128]) {
        group.items[0].downloaded_bytes=value;
        await api.refresh();
        const root = new Element(); root.innerHTML = ctx.nodeBodyHtml(node,layout);
        const small = root.querySelector('.retrieval-slot').querySelectorAll('small');
        assert.equal(small.length,value === undefined ? 1 : 2);
        if(value !== undefined) assert.equal(small[1].textContent,`${value} / 1024 B`);
    }
});

test('2/4/6/8 retrieval and saved cells remain 192x144 with gap12 at every viewport zoom', async () => {
    for(const count of [2,4,6,8]) {
        const {ctx,node,api} = await retrieval(count);
        node.images=[image(0)];
        for(const zoom of [.5,1,2,4]) {
            ctx.viewport.scale=zoom;
            const layout=ctx.imageLayout(node.images,1,node), styles=[];
            const html=api.nodeHtml(node,layout,(_output,_index,style) => {styles.push(style);return `<div class="saved" style="${style}"></div>`;});
            assert.doesNotMatch(html,/retrieval-single/);
            assert.deepEqual(styles,['--thumb-w:192px;--thumb-h:144px;width:192px;height:144px']);
            const root=new Element();root.innerHTML=html;
            assert.equal(root.querySelectorAll('.retrieval-slot').length,count-1);
            assert.ok(root.querySelectorAll('.retrieval-slot').every(slot=>slot.getAttribute('style')===styles[0]));
            assert.match(root.querySelector('.thumb-grid').getAttribute('style'),/gap:12px/);
        }
    }
});

test('actual saved-group renderer budgets complete rows using the scoped gap and existing label/padding table', () => {
    const e=fixture();
    for(const count of [2,4,6,8]) {
        const grid=e.ctx.getBatchThumbGridSize(count);
        const group={id:'saved',type:'smart-group',images:Array.from({length:count},(_,i)=>image(i)),items:[],w:grid.boxW,h:grid.boxH};
        evaluate(e.ctx,`canvasState.nodes=${JSON.stringify([group])}`);
        for(const zoom of [.5,1,2,4]) {
            e.ctx.viewport.scale=zoom;
            const layout=e.ctx.smartGroupThumbLayout(group);
            const fullRows=grid.rows*144+(grid.rows-1)*12;
            assert.equal(layout.gridHeight,fullRows);
            assert.deepEqual([layout.width,layout.height],[grid.boxW,grid.boxH]);
            const root=new Element();root.innerHTML=e.ctx.smartGroupBodyHtml(group);
            assert.match(root.querySelector('.smart-group-thumb-grid').getAttribute('style'),new RegExp(`--thumb-max-height:${fullRows}px`));
            assert.equal(root.querySelectorAll('.thumb-item').length,count);
            assert.ok(root.querySelectorAll('.thumb-item').every(cell=>cell.getAttribute('style')==='--thumb-w:192px;--thumb-h:144px'));
            assert.equal(grid.boxH,20*2+14+8+fullRows);
        }
    }
    assert.deepEqual(fromContext(evaluate(e.ctx,'[SMART_GROUP_LAYOUT.PADDING,SMART_GROUP_LAYOUT.SUMMARY_H,SMART_GROUP_LAYOUT.SUMMARY_GAP,BATCH_THUMB.GAP]')),[20,14,8,12]);
});

test('manual group geometry is never expanded to fit rows; valid constrained scrolling survives', () => {
    const e=fixture(), images=Array.from({length:8},(_,i)=>image(i));
    for(const [w,h] of [[844,362],[843,362],[844,361],[600,200]]) {
        const node={id:'manual',type:'smart-group',w,h,images,items:[]};
        evaluate(e.ctx,`canvasState.nodes=${JSON.stringify([node])}`);
        const before=JSON.stringify(node),layout=e.ctx.smartGroupThumbLayout(node);
        assert.deepEqual([layout.width,layout.height,layout.gridHeight],[w,h,300]);
        e.ctx.smartGroupBodyHtml(node);
        assert.equal(JSON.stringify(node),before);
    }
    const scoped=rule(canvasCss,'.smart-group-card .smart-group-thumb-grid');
    assert.equal(scoped.flex,'0 1 auto');assert.equal(scoped['min-height'],'0');assert.equal(scoped.gap,'12px');
    assert.equal(rule(canvasCss,'.thumb-grid')['overflow-y'],'auto');
    assert.equal(rule(canvasCss,'.smart-group-card.has-thumbs').overflow,'hidden');
});

test('ordinary media grids retain their original 8px budget and natural-ratio cells', () => {
    const e=fixture(), images=Array.from({length:4},(_,i)=>image(i));
    assert.equal(e.ctx.mediaGroupGridHeight(images,2,2,192),296);
    assert.equal(e.ctx.mediaGroupGridHeight(images,2,2,192,12),300);
    assert.equal(e.ctx.thumbItemStyle({width:100,height:200},192),'--thumb-w:96px;--thumb-h:192px');
    assert.equal(rule(canvasCss,'.thumb-grid').gap,'8px');
});
