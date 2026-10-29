(function(){
  const q=new URLSearchParams(location.search), project=q.get('project')||'';
  const result={viewport:{width:innerWidth,height:innerHeight},assertions:[],errors:[],pass:false};
  const ok=(name,value,actual)=>result.assertions.push({name,pass:!!value,actual});
  const wait=async(fn,ms=12000)=>{const end=Date.now()+ms;while(Date.now()<end){try{if(fn())return true}catch(_){}await new Promise(r=>setTimeout(r,80));}return false;};
  // #213：2d3381b（#162 P2e 4a）起 nodes 不再是裸全局，挂在 window.canvasState.nodes
  // （static/js/canvas-state.js:8）；兼容访问器同 tests/fly_fixes_browser_client.js:48。
  // 修复前 `typeof nodes!=='undefined'` 恒为 false：role 清理分支整体被跳过（记红断言），
  // 各选区 wait 全部空转到 12s，累计超出 harness 60s → client_events=[] 超时。
  const nodes=()=>{const s=window.canvasState;return(s&&Array.isArray(s.nodes))?s.nodes:(0,eval)('nodes');};
  const ptr=(el,type,clientX,clientY,extra)=>el.dispatchEvent(Object.assign(new PointerEvent(type,{bubbles:true,cancelable:true,button:0,pointerId:extra&&extra.id||3,clientX,clientY,buttons:type==='pointermove'?1:0}),(extra&&extra.raw)||{}));
  async function run(){
    ok('real local-edit canvas loaded',await wait(()=>document.querySelector('#smartTitle')),document.title);
    const getLive=()=>document.querySelector('.local-edit-node[data-id="local-1"]')||document.querySelector('.local-edit-node')||document.querySelector('.local-edit-card')?.closest('.image-node');
    let node=getLive();
    ok('independent local-edit node renders',await wait(()=>getLive()?.querySelector('[data-local-edit-mode]'))&&!!getLive(),document.querySelectorAll('.local-edit-card').length);
    node=getLive();
    ok('node shell has themed panel background',!!node&&getComputedStyle(node).backgroundColor!=='rgba(0, 0, 0, 0)',getComputedStyle(node||document.body).backgroundColor);
    ok('card has themed heading and mode switch',!!node&&!!node.querySelector('.local-edit-heading')&&node.querySelectorAll('[data-local-edit-mode]').length===2,node?.querySelector('.local-edit-heading')?.textContent||'');
    ok('three explicit role controls render',!!node&&node.querySelectorAll('[data-local-edit-connect-role]').length===3,node?.textContent?.slice(0,120)||'');
    const roleNode=typeof nodes!=='undefined' ? nodes().find(item=>item.id==='local-1') : null;
    const promptEdgeIndex=typeof canvas!=='undefined'&&Array.isArray(canvas?.connections) ? canvas.connections.findIndex(edge=>edge.to==='local-1'&&edge.role==='prompt') : -1;
    ok('connected prompt textarea is read-only',!!node&&!!node.querySelector('[data-local-edit-role="prompt"]')?.readOnly,!!node?.querySelector('[data-local-edit-role="prompt"]'));
    if(roleNode&&promptEdgeIndex>=0&&typeof disconnectConnections==='function'){
      disconnectConnections([promptEdgeIndex]);
      render();
      const disconnected=await wait(()=>!(roleNode.localEditInputRoles?.promptNodes||[]).includes('prompt-node'));
      ok('disconnect clears local-edit prompt role',disconnected,roleNode.localEditInputRoles);
      node=getLive();
      ok('prompt textarea becomes editable after disconnect',!!node?.querySelector('[data-local-edit-role="prompt"]')&&!node.querySelector('[data-local-edit-role="prompt"]').readOnly,!!node);
      roleNode.localEditActiveRole='prompt';
      const reconnected=typeof connectInputNode==='function'&&connectInputNode('prompt-node','local-1');
      render();
      node=getLive();
      ok('prompt role can reconnect after cleanup',reconnected&&(roleNode.localEditInputRoles?.promptNodes||[]).includes('prompt-node'),roleNode.localEditInputRoles);
      const eraseIndex=typeof canvas!=='undefined'&&Array.isArray(canvas?.connections) ? canvas.connections.findIndex(edge=>edge.to==='local-1'&&edge.role==='prompt') : -1;
      let erased=false;
      if(eraseIndex>=0&&typeof finishConnectionErase==='function'&&typeof connectionEraseState!=='undefined'){
        connectionEraseState={indices:new Set([eraseIndex]),started:true,count:1};
        erased=finishConnectionErase();
        connectionEraseState=null;
      }
      ok('erase path clears local-edit prompt role',erased&&!(roleNode.localEditInputRoles?.promptNodes||[]).includes('prompt-node'),roleNode.localEditInputRoles);
      roleNode.localEditActiveRole='prompt';
      const eraseReconnect=typeof connectInputNode==='function'&&connectInputNode('prompt-node','local-1');
      render();
      node=getLive();
      ok('prompt role reconnects after erase path',eraseReconnect&&(roleNode.localEditInputRoles?.promptNodes||[]).includes('prompt-node'),roleNode.localEditInputRoles);
      const referenceEdgeIndex=typeof canvas!=='undefined'&&Array.isArray(canvas?.connections) ? canvas.connections.findIndex(edge=>edge.to==='local-1'&&edge.role==='references') : -1;
      if(referenceEdgeIndex>=0){
        disconnectConnections([referenceEdgeIndex]);
        render();
        const referenceCleared=await wait(()=>!(roleNode.reference_asset_ids||[]).length);
        ok('disconnect clears local-edit reference asset',referenceCleared,{assetIds:roleNode.reference_asset_ids});
        roleNode.localEditActiveRole='references';
        const referenceReconnected=typeof connectInputNode==='function'&&connectInputNode('reference-node','local-1');
        render();
        node=getLive();
        ok('reference role reconnects after cleanup',referenceReconnected&&(roleNode.reference_asset_ids||[]).length===1,roleNode.reference_asset_ids);
      }else ok('disconnect clears local-edit reference asset',false,'reference role edge unavailable');
    }else ok('disconnect clears local-edit prompt role',false,'role cleanup API unavailable');
    // modify：固定比例矩形拖拽 → source 像素矩形 + 精确比例
    node=getLive();
    const rectBefore=JSON.stringify(roleNode?.selection||null);
    const modifyCanvas=node?.querySelector('[data-local-edit-selection-canvas]');
    if(modifyCanvas){
      await wait(()=>{const box=modifyCanvas.getBoundingClientRect();return box.width>10&&box.height>10;});
      const bounds=modifyCanvas.getBoundingClientRect(), x=bounds.left+bounds.width*.05, y=bounds.top+bounds.height*.05;
      ptr(modifyCanvas,'pointerdown',x,y,{id:3});
      ptr(modifyCanvas,'pointermove',x+bounds.width*.30,y+bounds.height*.30,{id:3});
      ptr(modifyCanvas,'pointerup',x+bounds.width*.30,y+bounds.height*.30,{id:3});
    }
    const liveRect=await wait(()=>{const sel=(typeof nodes!=='undefined'&&(nodes().find(n=>n.id==='local-1')||{})).selection;return Array.isArray(sel)&&sel.length===4&&JSON.stringify(sel)!==rectBefore&&sel[2]>sel[0]&&sel[3]>sel[1];});
    const selNow=(typeof nodes!=='undefined'&&(nodes().find(n=>n.id==='local-1')||{})).selection||[];
    ok('fixed-ratio rectangle drawing updates source rect',liveRect,{before:rectBefore,after:selNow});
    const ratioValue=String((typeof nodes!=='undefined'&&(nodes().find(n=>n.id==='local-1')||{})).aspect_ratio||'1:1').split(':').map(Number);
    const rectRatio=selNow.length===4?(selNow[2]-selNow[0])/(selNow[3]-selNow[1]):0;
    ok('drawn rect snaps to the fixed aspect ratio',Math.abs(rectRatio-ratioValue[0]/ratioValue[1])<=0.02,{rect:selNow,ratio:rectRatio});
    // 重置选区 → 全图按比例的居中矩形
    const resetBtn=node?.querySelector('[data-local-edit-reset-selection]');
    ok('reset selection control renders',!!resetBtn,node?.textContent?.slice(0,60)||'');
    resetBtn?.click();
    const resetDone=await wait(()=>{const sel=(typeof nodes!=='undefined'&&(nodes().find(n=>n.id==='local-1')||{})).selection||[];return sel.length===4&&sel[0]===0||sel.length===4&&sel[2]-sel[0]>=sel[3]-sel[1];});
    ok('reset selection produces full-image ratio rect',resetDone,(typeof nodes!=='undefined'&&(nodes().find(n=>n.id==='local-1')||{})).selection);
    // 创建菜单入口
    const menu=document.querySelector('#shell'); menu?.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,button:2,clientX:innerWidth/2,clientY:innerHeight/2}));
    ok('create menu exposes local-edit entry',await wait(()=>document.querySelector('#createMenu.open [data-create-type="local-edit"]')),document.querySelector('#createMenu')?.className||'');
    document.body.dispatchEvent(new MouseEvent('click',{bubbles:true}));
    await wait(()=>getLive()?.querySelector('[data-local-edit-mode]'));
    // retain：freehand 拖拽
    node=getLive();
    node.querySelector('[data-local-edit-mode="retain"]')?.click();
    ok('retain mode is selectable',await wait(()=>getLive()?.querySelector('[data-local-edit-mode="retain"]')?.classList.contains('active')),getLive()?.textContent?.slice(0,60)||'');
    node=getLive();
    const shape=node?.querySelector('[data-local-edit-shape]');
    ok('freehand and lasso choices are available',!!shape&&['freehand','polygon','curve'].every(v=>[...shape.options].some(o=>o.value===v)),shape?[...shape.options].map(o=>o.value):[]);
    shape.value='freehand';shape.dispatchEvent(new Event('change',{bubbles:true}));
    const freehandCanvas=node?.querySelector('[data-local-edit-selection-canvas]');
    if(freehandCanvas){
      const bounds=freehandCanvas.getBoundingClientRect(), x=bounds.left+bounds.width*.25, y=bounds.top+bounds.height*.25;
      ptr(freehandCanvas,'pointerdown',x,y,{id:7});
      ptr(freehandCanvas,'pointermove',x+bounds.width*.15,y+bounds.height*.12,{id:7});
      ptr(freehandCanvas,'pointermove',x+bounds.width*.30,y+bounds.height*.25,{id:7});
      ptr(freehandCanvas,'pointerup',x+bounds.width*.35,y+bounds.height*.35,{id:7});
    }
    const freehandDone=await wait(()=>{const sel=(typeof nodes!=='undefined'&&(nodes().find(n=>n.id==='local-1')||{})).selection||[];return Array.isArray(sel)&&sel.length>=2&&Array.isArray(sel[0]);});
    ok('freehand drag updates source selection',freehandDone,(typeof nodes!=='undefined'&&(nodes().find(n=>n.id==='local-1')||{})).selection);
    // retain：polygon 点击布点 + 双击闭合
    shape.value='polygon';shape.dispatchEvent(new Event('change',{bubbles:true}));
    node=getLive();
    const polyCanvas=node?.querySelector('[data-local-edit-selection-canvas]');
    if(polyCanvas){
      const b=polyCanvas.getBoundingClientRect(), x=b.left+b.width*.3, y=b.top+b.height*.3;
      ptr(polyCanvas,'pointerdown',x,y,{id:11}); ptr(polyCanvas,'pointerup',x,y,{id:11});
      ptr(polyCanvas,'pointerdown',x+b.width*.18,y+b.height*.05,{id:11}); ptr(polyCanvas,'pointerup',x+b.width*.18,y+b.height*.05,{id:11});
      ptr(polyCanvas,'pointerdown',x+b.width*.12,y+b.height*.22,{id:11}); ptr(polyCanvas,'pointerup',x+b.width*.12,y+b.height*.22,{id:11});
      polyCanvas.dispatchEvent(new MouseEvent('dblclick',{bubbles:true,cancelable:true,clientX:x+b.width*.12,clientY:y+b.height*.22}));
    }
    const polyDone=await wait(()=>{const sel=(typeof nodes!=='undefined'&&(nodes().find(n=>n.id==='local-1')||{})).selection||[];return Array.isArray(sel)&&sel.length>=3&&Array.isArray(sel[0]);});
    ok('polygon clicks + double-click commit a closed selection',polyDone,(typeof nodes!=='undefined'&&(nodes().find(n=>n.id==='local-1')||{})).selection);
    // retain：curve 点击布点 + 双击完成
    shape.value='curve';shape.dispatchEvent(new Event('change',{bubbles:true}));
    node=getLive();
    const curveCanvas=node?.querySelector('[data-local-edit-selection-canvas]');
    if(curveCanvas){
      const b=curveCanvas.getBoundingClientRect(), x=b.left+b.width*.4, y=b.top+b.height*.4;
      ptr(curveCanvas,'pointerdown',x,y,{id:13}); ptr(curveCanvas,'pointerup',x,y,{id:13});
      ptr(curveCanvas,'pointerdown',x+b.width*.15,y+b.height*.15,{id:13}); ptr(curveCanvas,'pointerup',x+b.width*.15,y+b.height*.15,{id:13});
      curveCanvas.dispatchEvent(new MouseEvent('dblclick',{bubbles:true,cancelable:true,clientX:x+b.width*.15,clientY:y+b.height*.15}));
    }
    const curveDone=await wait(()=>{const sel=(typeof nodes!=='undefined'&&(nodes().find(n=>n.id==='local-1')||{})).selection||[];return Array.isArray(sel)&&sel.length>=2&&Array.isArray(sel[0]);});
    ok('curve clicks + double-click commit curve points',curveDone,(typeof nodes!=='undefined'&&(nodes().find(n=>n.id==='local-1')||{})).selection);
    // 切回 modify（点列选区自动重置为全图矩形），随后提交
    node=getLive();
    node.querySelector('[data-local-edit-mode="modify"]')?.click();
    const backToRect=await wait(()=>{const sel=(typeof nodes!=='undefined'&&(nodes().find(n=>n.id==='local-1')||{})).selection||[];return Array.isArray(sel)&&sel.length===4&&sel[2]>sel[0];});
    ok('switching back to modify resets selection to a rect',backToRect,(typeof nodes!=='undefined'&&(nodes().find(n=>n.id==='local-1')||{})).selection);
    // 工具排「局部编辑」入口
    node=getLive();
    const srcNodeEl=document.querySelector('.image-node[data-id="source-node"]');
    const toolbarBefore=document.querySelectorAll('.image-node[data-id="source-node"] [data-smart-node-action="local-edit"]').length;
    ok('image node toolbar exposes local-edit entry',toolbarBefore===1,toolbarBefore);
    if(toolbarBefore===1){
      document.querySelector('.image-node[data-id="source-node"] [data-smart-node-action="local-edit"]').click();
      const created=await wait(()=>{const n=nodes().find(n=>n.type==='local-edit'&&n.id!=='local-1');return n&&(canvas?.connections||[]).some(c=>c.from==='source-node'&&c.to===n.id&&c.role==='original');});
      ok('toolbar creates local-edit node with original edge',created,nodes().map(n=>n.type).join(','));
    }
    // 提交（offline adapter）
    node=getLive();
    const submit=node?.querySelector('[data-local-edit-submit]');
    ok('submit control is enabled when inputs are complete',!!submit&&!submit.disabled,!!submit&&!submit.disabled);
    submit?.click();
    ok('result image renders on the node after submit',await wait(()=>getLive()?.querySelector('[data-local-edit-result] img')),getLive()?.textContent?.slice(0,120)||'');
    const saved=await fetch(`/api/project-library/${encodeURIComponent(project)}`).then(r=>r.json()).catch(()=>({}));
    const savedNode=(saved.canvas?.nodes||[]).find(n=>n.id==='local-1');
    ok('output is a new image on local-edit node',!!savedNode?.output_asset_id&&savedNode.outputKind==='image'&&savedNode.images?.length===1,savedNode);
    ok('upstream source asset remains unchanged',!!savedNode?.original_asset_id,savedNode?.original_asset_id||'');
  }
  addEventListener('error',e=>result.errors.push(e.error?.stack||e.message));addEventListener('unhandledrejection',e=>result.errors.push(String(e.reason||'rejection')));
  run().catch(e=>result.errors.push(e.stack||String(e))).finally(async()=>{result.pass=!result.errors.length&&result.assertions.every(a=>a.pass);await fetch('/__conversation_browser_results',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(result)});});
})();
