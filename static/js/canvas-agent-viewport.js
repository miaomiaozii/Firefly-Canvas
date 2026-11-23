(function(root){
    'use strict';
    root.FireflyAgentViewport = {
        create(options){
            let connection = null;
            let destroyed = false;
            let loadedProject = null;
            let registeredProject = null;
            let generation = null;
            const seen = new Set();
            let drawing = false;
            let drawFrame = null;
            function reset(){
                if(drawFrame !== null) root.cancelAnimationFrame(drawFrame);
                drawFrame = null;
                drawing = false;
                generation = null;
                registeredProject = null;
                seen.clear();
            }
            function snapshot(){
                const view = options.getViewport();
                if(!view || ![view.x, view.y, view.scale].every(Number.isFinite) || view.scale <= 0) return null;
                return {x:view.x, y:view.y, scale:view.scale};
            }
            function send(socket, payload){
                try { socket.send(JSON.stringify(payload)); return true; }
                catch(error){ return false; } // Delivery failure is not an application receipt (#485).
            }
            function advertise(){
                const project = options.getProjectId();
                if(destroyed || !connection || connection.readyState !== 1 || !project || loadedProject !== project || registeredProject === project) return;
                if(send(connection, {type:'canvas_viewport_ready', project_id:project})) registeredProject = project;
            }
            return {
                connect(socket){ if(destroyed || socket === connection) return; reset(); connection = socket; advertise(); },
                disconnect(socket){ if(socket === connection){ reset(); connection = null; } },
                ready(){ loadedProject = options.getProjectId(); advertise(); },
                handleMessage(socket, data){
                    if(typeof data === 'string'){
                        if(data.length > 16384) return false;
                        try { data = JSON.parse(data); } catch(error){ return false; }
                    }
                    if(!data || typeof data !== 'object' || Array.isArray(data)) return false;
                    if(socket !== connection || socket?.readyState !== 1 || !registeredProject || data?.project_id !== registeredProject || options.getProjectId() !== registeredProject) return false;
                    if(data.type === 'canvas_viewport_ready_ack'){
                        if(!generation && typeof data.generation === 'string' && data.generation.trim() && data.generation.length <= 128) generation = data.generation;
                        return true;
                    }
                    if(data.type !== 'canvas_viewport_command' || !generation || data.generation !== generation) return false;
                    if(typeof data.command_id !== 'string' || !data.command_id.trim() || data.command_id.length > 128) return true;
                    if(!['focus', 'zoom', 'state'].includes(data.command)) return true;
                    if(seen.has(data.command_id)) return true;
                    const commandGeneration = generation;
                    const commandProject = registeredProject;
                    const ack = (status, reason) => {
                        if(socket !== connection || socket.readyState !== 1 || generation !== commandGeneration
                            || options.getProjectId() !== commandProject) return;
                        const viewport = reason ? null : snapshot();
                        send(socket, {type:'canvas_viewport_ack', project_id:registeredProject,
                            generation, command_id:data.command_id, command:data.command,
                            ...(viewport ? {status, viewport} : {status:'rejected', reason:reason || 'NOT_READY'})});
                    };
                    const apply = next => {
                        if(![next.x, next.y, next.scale].every(Number.isFinite) || next.scale <= 0){
                            ack('rejected', 'NOT_READY');
                            return;
                        }
                        options.applyViewport(next);
                        drawing = true;
                        drawFrame = root.requestAnimationFrame(() => { drawFrame = null; drawing = false; ack('applied'); });
                    };
                    // Never evict an ID and risk applying its replay in the same generation.
                    if(seen.size >= 4096){ ack('rejected', 'NOT_READY'); return true; }
                    seen.add(data.command_id);
                    if(drawing || !snapshot()){ ack('rejected', 'NOT_READY'); return true; }
                    const shellBounds = data.command === 'state' ? null : options.shell.getBoundingClientRect();
                    if(shellBounds && (![shellBounds.left, shellBounds.top, shellBounds.width, shellBounds.height].every(Number.isFinite)
                        || shellBounds.width <= 0 || shellBounds.height <= 0)){
                        ack('rejected', 'NOT_READY');
                        return true;
                    }
                    if(data.command === 'state') ack('observed');
                    if(data.command === 'zoom'){
                        if(!Number.isFinite(data.level) || data.level < 0.1 || data.level > 8){
                            ack('rejected', 'INVALID_ARGUMENT');
                            return true;
                        }
                        const view = snapshot();
                        const shell = shellBounds;
                        apply({x:shell.width / 2 - (shell.width / 2 - view.x) / view.scale * data.level,
                            y:shell.height / 2 - (shell.height / 2 - view.y) / view.scale * data.level, scale:data.level});
                    }
                    if(data.command === 'focus'){
                        if(typeof data.node_id !== 'string' || !data.node_id.trim() || Array.from(data.node_id).length > 512){
                            ack('rejected', 'INVALID_ARGUMENT');
                            return true;
                        }
                        const node = options.getNodes().find(item => item.id === data.node_id);
                        if(!node){ ack('rejected', 'NODE_NOT_FOUND'); return true; }
                        if(node.type === 'smart-agent' && (node.dock === 'left' || node.dock === 'right')){
                            ack('rejected', 'DOCKED_TARGET');
                            return true;
                        }
                        const view = snapshot();
                        const shell = shellBounds;
                        const element = Array.from(options.world.querySelectorAll('.image-node[data-id]'))
                            .find(item => item.dataset.id === data.node_id);
                        const target = element?.getBoundingClientRect();
                        const visibility = element && root.getComputedStyle?.(element)?.visibility;
                        if(visibility === 'hidden' || visibility === 'collapse' || !element?.isConnected || !element.getClientRects().length || !target
                            || ![target.left, target.top, target.width, target.height].every(Number.isFinite)
                            || target.width <= 0 || target.height <= 0){
                            ack('rejected', 'TARGET_NOT_RENDERABLE');
                            return true;
                        }
                        apply({x:view.x + shell.left + shell.width / 2 - target.left - target.width / 2,
                            y:view.y + shell.top + shell.height / 2 - target.top - target.height / 2, scale:view.scale});
                    }
                    return true;
                },
                destroy(){ destroyed = true; reset(); connection = null; loadedProject = null; },
            };
        },
    };
})(window);
