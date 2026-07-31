(function(){
  const q=new URLSearchParams(location.search), project=q.get('project')||'';
  const result={viewport:{width:innerWidth,height:innerHeight},assertions:[],errors:[],pass:false};
  const ok=(name,value,actual)=>result.assertions.push({name,pass:!!value,actual});
  const wait=async(fn,ms=12000)=>{const end=Date.now()+ms;while(Date.now()<end){try{if(fn())return true}catch(_){}await new Promise(r=>setTimeout(r,80));}return false;};
  async function run(){
    ok('real local-edit canvas loaded',await wait(()=>document.querySelector('#smartTitle')),document.title);
    const getLive=()=>document.querySelector('.local-edit-node[data-id="local-1"]')||document.querySelector('.local-edit-node')||document.querySelector('.local-edit-card')?.closest('.image-node');
    const nodeReady=await wait(()=>getLive()?.querySelector('[data-local-edit-mode]'));
    let node=getLive();
    ok('independent local-edit node renders',nodeReady&&!!node,document.querySelectorAll('.local-edit-card').length);
    ok('three explicit role controls render',!!node&&node.querySelectorAll('[data-local-edit-connect-role]').length===3,node?.textContent||'');
    const roleNode=typeof nodes!=='undefined' ? nodes.find(item=>item.id==='local-1') : null;
    const promptEdgeIndex=typeof canvas!=='undefined'&&Array.isArray(canvas?.connections) ? canvas.connections.findIndex(edge=>edge.to==='local-1'&&edge.role==='prompt') : -1;
    if(roleNode&&promptEdgeIndex>=0&&typeof disconnectConnections==='function'){
      disconnectConnections([promptEdgeIndex]);
      const disconnected=await wait(()=>!(roleNode.localEditInputRoles?.promptNodes||[]).includes('prompt-node'));
      ok('disconnect clears local-edit prompt role',disconnected,roleNode.localEditInputRoles);
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
        const referenceCleared=await wait(()=>!(roleNode.reference_asset_ids||[]).length&&String(getLive()?.querySelector('[data-local-edit-role="references"]')?.value||'')==='');
        ok('disconnect clears local-edit reference asset',referenceCleared,{assetIds:roleNode.reference_asset_ids,input:getLive()?.querySelector('[data-local-edit-role="references"]')?.value||''});
        roleNode.localEditActiveRole='references';
        const referenceReconnected=typeof connectInputNode==='function'&&connectInputNode('reference-node','local-1');
        render();
        node=getLive();
        ok('reference role reconnects after cleanup',referenceReconnected&&(roleNode.reference_asset_ids||[]).length===1,roleNode.reference_asset_ids);
      }else ok('disconnect clears local-edit reference asset',false,'reference role edge unavailable');
    }else ok('disconnect clears local-edit prompt role',false,'role cleanup API unavailable');
    const modifyCanvas=node?.querySelector('[data-local-edit-selection-canvas]');
    const rectBefore=String(node?.querySelector('[data-local-edit-selection]')?.value||'');
    if(modifyCanvas){
      await wait(()=>{const box=modifyCanvas.getBoundingClientRect();return box.width>10&&box.height>10;});
      const bounds=modifyCanvas.getBoundingClientRect(), x=bounds.left+bounds.width*.05, y=bounds.top+bounds.height*.05;
      modifyCanvas.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,cancelable:true,button:0,pointerId:3,clientX:x,clientY:y}));
      modifyCanvas.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,cancelable:true,buttons:1,pointerId:3,clientX:x+bounds.width*.30,clientY:y+bounds.height*.30}));
      modifyCanvas.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,cancelable:true,button:0,pointerId:3,clientX:x+bounds.width*.30,clientY:y+bounds.height*.30}));
    }
    const rectSelection=await wait(()=>{const value=String(getLive()?.querySelector('[data-local-edit-selection]')?.value||'');return value.split(',').length===4&&value!==rectBefore;});
    const rectAfter=String(getLive()?.querySelector('[data-local-edit-selection]')?.value||'');
    ok('fixed-ratio rectangle drawing updates source rect',rectSelection,{before:rectBefore,after:rectAfter});
    const menu=document.querySelector('#shell'); menu?.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,button:2,clientX:innerWidth/2,clientY:innerHeight/2}));
    ok('create menu exposes local-edit entry',await wait(()=>document.querySelector('#createMenu.open [data-create-type="local-edit"]')),document.querySelector('#createMenu')?.className||'');
    document.body.dispatchEvent(new MouseEvent('click',{bubbles:true}));
    let live=getLive();
    await wait(()=>getLive()?.querySelector('[data-local-edit-mode]'));
    live=getLive();
    const retain=live?.querySelector('[data-local-edit-mode="retain"]'); retain?.click();
    ok('retain mode is selectable',await wait(()=>getLive()?.querySelector('[data-local-edit-mode="retain"]')?.classList.contains('active')),getLive()?.textContent||'');
    live=getLive();
    const shape=getLive()?.querySelector('[data-local-edit-shape]'); if(shape){shape.value='curve';shape.dispatchEvent(new Event('change',{bubbles:true}));}
    ok('curve free-shape selector is available',shape?.value==='curve',shape?.value||'');
    ok('freehand and lasso choices are available',!!shape&&['freehand','polygon'].every(v=>[...shape.options].some(o=>o.value===v)),shape?[...shape.options].map(o=>o.value):[]);
    live=getLive();
    const selection=getLive()?.querySelector('[data-local-edit-selection]');
    if(selection){selection.value='[[0,0]]';selection.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:null}));}
    const selectionCanvas=getLive()?.querySelector('[data-local-edit-selection-canvas]');
    if(selectionCanvas){
      const bounds=selectionCanvas.getBoundingClientRect(), x=bounds.left+bounds.width*.25, y=bounds.top+bounds.height*.25;
      selectionCanvas.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,cancelable:true,button:0,pointerId:7,clientX:x,clientY:y}));
      selectionCanvas.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,cancelable:true,buttons:1,pointerId:7,clientX:x+bounds.width*.3,clientY:y+bounds.height*.2}));
      selectionCanvas.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,cancelable:true,button:0,pointerId:7,clientX:x+bounds.width*.5,clientY:y+bounds.height*.35}));
    }
    const drawnSelection=await wait(()=>{try{const value=String(getLive()?.querySelector('[data-local-edit-selection]')?.value||'');return value!=='[[0,0]]'&&JSON.parse(value).length>=2}catch(_){return false;}});
    const drawnValue=String(getLive()?.querySelector('[data-local-edit-selection]')?.value||'');
    ok('pointer drawing updates source selection',drawnSelection,{before:'[[0,0]]',after:drawnValue});
    live=getLive();
    const prompt=getLive()?.querySelector('[data-local-edit-role="prompt"]'); if(prompt&&!prompt.value){prompt.value='browser prompt';prompt.dispatchEvent(new Event('input',{bubbles:true}));}
    const submit=getLive()?.querySelector('[data-local-edit-submit]'); submit?.click();
    ok('submit control is present',!!submit,live?.textContent||'');
    ok('result completes through offline adapter',await wait(()=>getLive()?.querySelector('.local-edit-status')?.textContent.includes('已生成')),getLive()?.textContent||'');
    const saved=await fetch(`/api/project-library/${encodeURIComponent(project)}`).then(r=>r.json()).catch(()=>({}));
    const savedNode=(saved.canvas?.nodes||[]).find(n=>n.id==='local-1');
    ok('output is a new image on local-edit node',!!savedNode?.output_asset_id&&savedNode.outputKind==='image'&&savedNode.images?.length===1,savedNode);
    ok('upstream source asset remains unchanged',!!savedNode?.original_asset_id,savedNode?.original_asset_id||'');
  }
  addEventListener('error',e=>result.errors.push(e.error?.stack||e.message));addEventListener('unhandledrejection',e=>result.errors.push(String(e.reason||'rejection')));
  run().catch(e=>result.errors.push(e.stack||String(e))).finally(async()=>{result.pass=!result.errors.length&&result.assertions.every(a=>a.pass);await fetch('/__conversation_browser_results',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(result)});});
})();
