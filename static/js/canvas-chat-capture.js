// @ts-check
(function (root) {
    'use strict';

    const channel = 'firefly.chat-capture.v1';
    const sites = new Set(['chatgpt.com', 'gemini.google.com', 'chat.deepseek.com', 'chat.qwen.ai', 'www.doubao.com', 'www.kimi.com', 'yuanbao.tencent.com']);
    const imageTypes = new Map([['image/png', 'png'], ['image/jpeg', 'jpg'], ['image/webp', 'webp'], ['image/gif', 'gif']]);
    const maxBytes = 8 * 1024 * 1024;

    /** @typedef {{site:string, action:'status'} | {site:string, action:'append-prompt'|'new-text', text:string} | {site:string, action:'new-image', mime:string, bytes:string}} Capture */
    /** @typedef {{ok:boolean, error?:string, canAppend?:boolean}} Result */
    /** @typedef {{ready:()=>boolean, canAppend:()=>boolean, append:(text:string,site:string)=>Result, newText:(text:string,site:string)=>Result, newImage:(file:File,site:string)=>Promise<Result>}} Actions */

    /** @param {unknown} value @returns {Capture|null} */
    function parseCapture(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
        const data = /** @type {Record<string, unknown>} */ (value);
        if (!['channel', 'site', 'action'].every(key => Object.hasOwn(data, key)) || data.channel !== channel ||
            typeof data.site !== 'string' || !sites.has(data.site)) return null;
        const {site, action, text, mime, bytes} = data;
        const keys = Object.keys(data).length;
        if (action === 'status' && keys === 3) return {site, action};
        if (action === 'append-prompt' || action === 'new-text') {
            return keys === 4 && Object.hasOwn(data, 'text') && typeof text === 'string' && text.length <= 20000 && text.trim()
                ? {site, action, text} : null;
        }
        if (action !== 'new-image' || keys !== 5 || !Object.hasOwn(data, 'mime') || !Object.hasOwn(data, 'bytes') ||
            typeof mime !== 'string' || !imageTypes.has(mime) || typeof bytes !== 'string' || !bytes.length ||
            bytes.length % 4 !== 0 || bytes.length > Math.ceil(maxBytes / 3) * 4 || !/^[A-Za-z0-9+/]+={0,2}$/.test(bytes)) return null;
        return {site, action, mime, bytes};
    }

    /** @param {string} mime @param {string} encoded @returns {File|null} */
    function captureFile(mime, encoded) {
        const bytes = atob(encoded);
        if (bytes.length > maxBytes) return null;
        const valid = mime === 'image/png' ? bytes.startsWith('\x89PNG\r\n\x1a\n') :
            mime === 'image/jpeg' ? bytes.startsWith('\xff\xd8\xff') :
            mime === 'image/webp' ? bytes.startsWith('RIFF') && bytes.slice(8, 12) === 'WEBP' :
            bytes.startsWith('GIF87a') || bytes.startsWith('GIF89a');
        if (!valid) return null;
        return new File([Uint8Array.from(bytes, value => value.charCodeAt(0))], `chat-image.${imageTypes.get(mime)}`, {type:mime});
    }

    /** @param {Window} win @param {Actions} canvasActions */
    function listen(win, canvasActions) {
        win.addEventListener('message', event => {
            // The relay is same-window. This is not authentication of scripts already running on Firefly's origin.
            if (!event.isTrusted || event.source !== win || event.origin !== win.location.origin || event.ports.length !== 1) return;
            const data = parseCapture(event.data);
            if (!data) return;
            const port = event.ports[0];
            Promise.resolve().then(() => {
                if (!canvasActions.ready()) return {ok:false, error:'CANVAS_NOT_READY'};
                if (data.action === 'status') return {ok:true, canAppend:canvasActions.canAppend()};
                if (data.action === 'append-prompt') return canvasActions.append(data.text, data.site);
                if (data.action === 'new-text') return canvasActions.newText(data.text, data.site);
                if (data.action === 'new-image') {
                    const file = captureFile(data.mime, data.bytes);
                    return file ? canvasActions.newImage(file, data.site) : {ok:false, error:'INVALID_IMAGE'};
                }
                return {ok:false, error:'CAPTURE_FAILED'};
            }).then(result => port.postMessage(result), () => port.postMessage({ok:false, error:'CAPTURE_FAILED'}))
                .finally(() => port.close());
        });
    }

    /** @typedef {{x:number,y:number}} Point */
    /** @typedef {{width:number,height:number}} Size */
    /** @typedef {Point & Size} Rect */
    /** @typedef {{id:string,promptDraftText?:string,promptDraftHtml?:string,chatSourceDomain?:string,userTouchedAt?:number,promptEditedByUser?:boolean}} DraftNode */
    /** @typedef {DraftNode & {running?:boolean,readonly?:boolean,readOnly?:boolean,runPrompt?:string,prompt?:string,text?:string,runPromptRefs?:unknown[]}} ImageNode */
    /** @typedef {{url?:string}} UploadedImage */
    /**
     * @typedef {object} CanvasHost
     * @property {()=>string|null} project
     * @property {()=>ImageNode|null} selectedImage
     * @property {(node: ImageNode)=>boolean} isDraftOpen
     * @property {()=>void} flushDraft
     * @property {()=>string} readDraft
     * @property {(node:ImageNode)=>void} showDraft
     * @property {(node:ImageNode)=>()=>void} beginDraftEdit
     * @property {(before:DraftNode,after:DraftNode)=>void} recordDraftUndo
     * @property {(text:string,refs:unknown[])=>string} formatDraft
     * @property {(text:string)=>string} escapeText
     * @property {()=>Point} viewCenter
     * @property {()=>Rect[]} occupied
     * @property {(anchor:Point,size:Size,boxes:Rect[])=>Point & {exhausted:boolean}} findSpot
     * @property {(point:Point,text:string)=>DraftNode} addTextNode
     * @property {(file:File)=>Promise<UploadedImage[]>} uploadImage
     * @property {(image:UploadedImage)=>Size} measureImage
     * @property {(point:Point,image:UploadedImage)=>DraftNode} addImageNode
     * @property {()=>void} redraw
     * @property {()=>void} save
     */

    /** @param {CanvasHost} host */
    function connect(host) {
        const ready = () => Boolean(host.project());
        function promptTarget() {
            const node = host.selectedImage();
            return node && !node.running && !node.readonly && !node.readOnly ? node : null;
        }
        /** @param {Size} size */
        function spot(size) {
            const center = host.viewCenter();
            const anchor = {x:center.x - Math.round(size.width / 2), y:center.y - Math.round(size.height / 2)};
            return host.findSpot(anchor, size, host.occupied());
        }
        /** @param {string} text @param {string} site @returns {Result} */
        function append(text, site) {
            const node = promptTarget();
            if (!node) return {ok:false, error:'SELECT_ONE_IMAGE'};
            const active = host.isDraftOpen(node);
            if (active) host.flushDraft();
            const fields = /** @type {const} */ (['promptDraftText', 'promptDraftHtml', 'chatSourceDomain', 'userTouchedAt', 'promptEditedByUser']);
            const snapshot = () => ({id:node.id, ...Object.fromEntries(fields.filter(key => Object.hasOwn(node, key)).map(key => [key, node[key]]))});
            const before = snapshot();
            const finishEdit = host.beginDraftEdit(node);
            const prior = active ? host.readDraft() : (node.promptDraftText || node.runPrompt || node.prompt || node.text || '');
            const html = node.promptDraftHtml || host.formatDraft(prior, node.runPromptRefs || []) || host.escapeText(prior);
            const suffix = (prior ? '\n\n' : '') + text;
            node.promptDraftText = prior + suffix;
            node.promptDraftHtml = html + host.escapeText(suffix);
            node.chatSourceDomain = site;
            finishEdit();
            host.recordDraftUndo(before, snapshot());
            if (active) host.showDraft(node);
            host.redraw();
            host.save();
            return {ok:true};
        }
        /** @param {string} text @param {string} site @returns {Result} */
        function newText(text, site) {
            const point = spot({width:316, height:240});
            if (point.exhausted) return {ok:false, error:'NO_FREE_SPOT'};
            const node = host.addTextNode(point, text);
            node.chatSourceDomain = site;
            host.redraw();
            host.save();
            return {ok:true};
        }
        /** @param {File} file @param {string} site @returns {Promise<Result>} */
        async function newImage(file, site) {
            const project = host.project();
            const [image] = await host.uploadImage(file);
            if (host.project() !== project) return {ok:false, error:'PROJECT_CHANGED'};
            if (!image?.url) return {ok:false, error:'CAPTURE_FAILED'};
            const point = spot(host.measureImage(image));
            if (point.exhausted) return {ok:false, error:'NO_FREE_SPOT'};
            const node = host.addImageNode(point, image);
            node.chatSourceDomain = site;
            host.save();
            return {ok:true};
        }
        listen(root, {
            ready, canAppend:() => Boolean(promptTarget()),
            append, newText, newImage,
        });
    }

    Object.assign(root, {FireflyChatCapture:{connect}});
})(window);
