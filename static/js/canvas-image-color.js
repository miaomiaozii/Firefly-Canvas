// @ts-check
(function () {
    'use strict';

    const ALGO_VERSION = 1;
    const PARAM_KEYS = /** @type {const} */ (['temperature', 'tint', 'exposure', 'contrast', 'saturation']);
    const WB_SCALE = Math.LN2;
    const LINEAR = Float64Array.from({ length: 256 }, (_, i) => {
        const s = i / 255;
        return s <= .04045 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4;
    });
    const LUT_MAX = 65535;
    const SRGB = Uint8Array.from({ length: LUT_MAX + 1 }, (_, i) => {
        const l = i / LUT_MAX;
        return Math.round(255 * (l <= .0031308 ? 12.92 * l : 1.055 * l ** (1 / 2.4) - .055));
    });
    // Saturation can overshoot the unit interval. Extend the encoding LUT to [-1, 2] to clip without per-channel branches.
    const SRGB_EXTENDED = new Uint8Array(3 * LUT_MAX + 1);
    SRGB_EXTENDED.set(SRGB, LUT_MAX);
    SRGB_EXTENDED.fill(255, 2 * LUT_MAX + 1);
    /** @typedef {typeof PARAM_KEYS[number]} ParamKey */
    /** @typedef {Record<ParamKey, number>} Params */
    /** @typedef {Pick<Params, 'temperature'|'tint'>} WhiteBalance */
    /** @typedef {Partial<Record<ParamKey, unknown>>} ParamInput */
    /** @typedef {Uint8Array | Uint8ClampedArray} Pixels */
    /** @typedef {{nodeId: string, source_asset_id: string, url: string, current?: ParamInput, [key: string]: unknown}} Target */
    /** @typedef {{source_asset_id: string, op: 'color', params: Params, algo_version: number}} Metadata */
    /** @typedef {{target: Target, file: object, metadata: Metadata}} Change */
    /** @typedef {'applied' | 'cancel' | 'replaced'} CloseReason */
    /** @typedef {{push: (close: () => void, label: string) => () => void, top?: {close: () => void} | null}} EscapeStack */
    /** @typedef {Element|DOMRectReadOnly} Anchor */
    /** @typedef {{
     * anchor: Anchor | (() => Anchor | null), targets: Target[],
     * translate: (key: string) => string, format: (key: string, values: {count: number}) => string,
     * loadImage: (url: string, target: Target) => HTMLImageElement | ImageBitmap | Promise<HTMLImageElement | ImageBitmap>,
     * encode: (target: Target, pngBlob: Blob) => object | Promise<object>,
     * commit: (changes: Change[]) => void | Promise<void>,
     * onClose?: (reason: CloseReason) => void, escapeStack?: EscapeStack
     * }} OpenOptions */

    /** @param {number} value @param {number} min @param {number} max */
    function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }

    /** @param {unknown} value @param {number} [min] */
    function sliderValue(value, min = -100) {
        const n = typeof value === 'number' || typeof value === 'string' ? Number(value) : NaN;
        return Number.isFinite(n) ? clamp(Math.round(n), min, 100) || 0 : 0;
    }

    /** @param {ParamInput | null} [params] @returns {Params} */
    function normalizeParams(params) {
        return {
            temperature: sliderValue(params?.temperature), tint: sliderValue(params?.tint),
            exposure: sliderValue(params?.exposure), contrast: sliderValue(params?.contrast),
            saturation: sliderValue(params?.saturation),
        };
    }

    /** @param {number} l */
    function encodeSrgb(l) { return SRGB[Math.round(l * LUT_MAX)]; }

    /** Apply to an sRGB RGBA8 buffer in place. Keep the pristine source in callers that need to recompute.
     * @param {Pixels} rgba @param {ParamInput | null} [input] @returns {Pixels}
     */
    function apply(rgba, input) {
        const { temperature, tint, exposure, contrast, saturation } = normalizeParams(input);
        if (!(temperature || tint || exposure || contrast || saturation)) return rgba;

        const warmth = temperature * WB_SCALE / 100;
        const magenta = tint * WB_SCALE / 100;
        const ev = exposure * Math.LN2 / 50;
        const contrastGain = 2 ** (contrast / 100);
        const pivot = .18 * (1 - contrastGain);
        // White balance/exposure act in linear light; log gains keep the WB basis invertible by both suggestions.
        const gains = [ev + warmth + magenta / 3, ev - 2 * magenta / 3, ev - warmth + magenta / 3];
        const tone = gains.map(gain => {
            const scale = Math.exp(gain) * contrastGain;
            return Float64Array.from(LINEAR, v => clamp(v * scale + pivot, 0, 1));
        });
        if (!saturation) {
            const [r, g, b] = tone.map(t => Uint8Array.from(t, encodeSrgb));
            for (let i = 0; i < rgba.length; i += 4) {
                if (!rgba[i + 3]) continue;
                rgba[i] = r[rgba[i]];
                rgba[i + 1] = g[rgba[i + 1]];
                rgba[i + 2] = b[rgba[i + 2]];
            }
        } else {
            const [r, g, b] = tone.map(t => t.map(v => v * LUT_MAX));
            const mix = saturation / 100, scale = 1 + mix;
            for (let i = 0; i < rgba.length; i += 4) {
                if (!rgba[i + 3]) continue;
                const rr = r[rgba[i]], gg = g[rgba[i + 1]], bb = b[rgba[i + 2]];
                const offset = LUT_MAX + .5 - (.2126 * rr + .7152 * gg + .0722 * bb) * mix;
                rgba[i] = SRGB_EXTENDED[(rr * scale + offset) | 0];
                rgba[i + 1] = SRGB_EXTENDED[(gg * scale + offset) | 0];
                rgba[i + 2] = SRGB_EXTENDED[(bb * scale + offset) | 0];
            }
        }
        return rgba;
    }

    /** @param {number} r @param {number} g @param {number} b @param {boolean} limited @returns {WhiteBalance} */
    function suggestFromMeans(r, g, b, limited) {
        const red = Math.log(limited ? clamp(g / r, .8, 1.25) : g / r);
        const blue = Math.log(limited ? clamp(g / b, .8, 1.25) : g / b);
        return {
            temperature: sliderValue((red - blue) * 50 / WB_SCALE),
            tint: sliderValue((red + blue) * 50 / WB_SCALE),
        };
    }

    /** Analyze a downsampled sRGB RGBA8 buffer (the panel supplies a longest edge ≤512 px).
     * @param {Pixels} rgba @returns {WhiteBalance | null}
     */
    function analyzeGrayWorld(rgba) {
        let r = 0, g = 0, b = 0;
        for (let i = 0; i < rgba.length; i += 4) {
            const a = rgba[i + 3], rr = rgba[i], gg = rgba[i + 1], bb = rgba[i + 2];
            if (!a || rr < 8 || gg < 8 || bb < 8 || rr > 247 || gg > 247 || bb > 247) continue;
            r += a * LINEAR[rr];
            g += a * LINEAR[gg];
            b += a * LINEAR[bb];
        }
        return g ? suggestFromMeans(r, g, b, true) : null;
    }

    /** @param {number} width @param {number} height @param {number} x @param {number} y */
    function sampleBounds(width, height, x, y) {
        const cx = clamp(Math.floor(x), 0, width - 1), cy = clamp(Math.floor(y), 0, height - 1);
        return { left: Math.max(0, cx - 2), right: Math.min(width, cx + 3), top: Math.max(0, cy - 2), bottom: Math.min(height, cy + 3) };
    }

    /** @param {Pixels} rgba @param {number} width @param {number} height @param {number} x @param {number} y @returns {WhiteBalance | null} */
    function sampleNeutral(rgba, width, height, x, y) {
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isInteger(width) || !Number.isInteger(height)
            || width < 1 || height < 1 || rgba.length !== width * height * 4) return null;
        const { left, right, top, bottom } = sampleBounds(width, height, x, y);
        let r = 0, g = 0, b = 0, weight = 0;
        for (let row = top; row < bottom; row++) for (let col = left; col < right; col++) {
            const i = (row * width + col) * 4, a = rgba[i + 3];
            r += a * LINEAR[rgba[i]];
            g += a * LINEAR[rgba[i + 1]];
            b += a * LINEAR[rgba[i + 2]];
            weight += a;
        }
        if (!weight || Math.min(r, g, b) / weight < LINEAR[8] || Math.max(r, g, b) / weight > LINEAR[247]) return null;
        return suggestFromMeans(r, g, b, false);
    }

    /** @template {keyof HTMLElementTagNameMap} T @param {T} tag @param {string} [className] @param {string} [text] */
    function el(tag, className = '', text = '') {
        const element = document.createElement(tag);
        element.className = className;
        element.textContent = text;
        return element;
    }

    /** @param {HTMLCanvasElement} canvas */
    function context2d(canvas) {
        // Browser readback and transport PNG both use sRGB; only the backend converts to the source ICC.
        const ctx = canvas.getContext('2d', { colorSpace: 'srgb', willReadFrequently: true });
        if (!ctx) throw new Error('2D canvas unavailable');
        return ctx;
    }

    /** @param {HTMLImageElement | ImageBitmap} image */
    function imageSize(image) {
        const [width, height] = 'naturalWidth' in image ? [image.naturalWidth, image.naturalHeight] : [image.width, image.height];
        if (!width || !height) throw new Error('Invalid image dimensions');
        return { width, height };
    }

    /** @param {HTMLImageElement | ImageBitmap} image @param {Params} params @returns {Promise<Blob>} */
    async function bake(image, params) {
        const canvas = el('canvas');
        const { width, height } = imageSize(image);
        canvas.width = width;
        canvas.height = height;
        try {
            const ctx = context2d(canvas);
            ctx.drawImage(image, 0, 0);
            if (PARAM_KEYS.some(key => params[key])) {
                const pixels = ctx.getImageData(0, 0, width, height);
                apply(pixels.data, params);
                ctx.putImageData(pixels, 0, 0);
            }
            return await new Promise((resolve, reject) => {
                canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('PNG canvas export failed')), 'image/png');
            });
        } finally {
            canvas.width = canvas.height = 0;
        }
    }

    /** @param {HTMLImageElement | ImageBitmap} image @param {number} x @param {number} y */
    function sampleImage(image, x, y) {
        const { width, height } = imageSize(image);
        const px = clamp(Math.floor(x * width), 0, width - 1), py = clamp(Math.floor(y * height), 0, height - 1);
        const { left, right, top, bottom } = sampleBounds(width, height, px, py);
        const canvas = el('canvas');
        canvas.width = right - left;
        canvas.height = bottom - top;
        const ctx = context2d(canvas);
        // Sample source pixels, not a browser-resized or already corrected version of the preview.
        ctx.drawImage(image, left, top, canvas.width, canvas.height, 0, 0, canvas.width, canvas.height);
        const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
        return sampleNeutral(pixels.data, canvas.width, canvas.height, px - left, py - top);
    }

    /** @type {{finish: (reason: CloseReason) => void} | null} */
    let active = null;

    /**
     * The first target is the preview and default apply scope. `current` contains the saved five params;
     * `url` and `source_asset_id` always refer to the original. Keep integration-specific identity fields
     * on target so the host can validate and atomically commit captured occurrences (or throw).
     * `encode` unwraps the backend `{file}` and returns that file object. No host writes before `commit`.
     * Labels use `imageColor.` + title, preview, auto, sampler, sampleHint, strength, the five param
     * names, compare, reset, cancel, apply, scope, scopePrimary, loading, loadError, applying, applyError,
     * or noSample; `format('imageColor.scopeSelection', {count})` uses the captured selection size.
     * `ready` resolves false on load failure or invalidation before load completes. `close()` invalidates
     * pending results; an already-started atomic host commit is not reversible here. Call `reposition()`
     * after host pan/zoom if the anchor moves without a viewport event.
     * @param {OpenOptions} options
     * @returns {{close: () => void, reposition: () => void, ready: Promise<boolean>}}
     */
    function open(options) {
        const { anchor, translate, format, loadImage, encode, commit, onClose, escapeStack } = options;
        const targets = options.targets.map(target => Object.freeze({ ...target, current: Object.freeze(normalizeParams(target.current)) }));
        if (!targets.length || targets.some(target => !target.nodeId || !target.source_asset_id || !target.url)) {
            throw new TypeError('Color adjustment requires captured source targets');
        }
        const primary = targets[0];
        let params = normalizeParams(primary.current);
        /** @type {WhiteBalance | null} */
        let suggestion = null;
        let strength = 100, sampling = false, busy = false, committing = false, closed = false, frame = 0;
        /** @type {number | null} */
        let comparePointer = null;
        let compareKey = false, buttonKey = '';
        /** @type {HTMLImageElement | ImageBitmap | null} */
        let image = null;
        /** @type {{ctx: CanvasRenderingContext2D, original: ImageData, output: ImageData} | null} */
        let preview = null;
        /** @type {(() => void) | undefined} */
        let releaseEscape;

        /** @param {string} name */
        const t = name => translate(`imageColor.${name}`);
        const panel = el('section', 'canvas-image-color');
        panel.tabIndex = -1;
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-label', t('title'));
        const canvas = el('canvas', 'canvas-image-color-preview');
        canvas.hidden = true;
        canvas.tabIndex = -1;
        canvas.setAttribute('aria-label', t('preview'));
        const title = el('h2', 'canvas-image-color-title', t('title'));
        const body = el('div', 'canvas-image-color-controls');
        const shortcuts = el('div', 'canvas-image-color-actions');
        const footer = el('div', 'canvas-image-color-actions');
        const status = el('p', 'canvas-image-color-status');
        const scope = el('select');
        const session = { finish };
        /** @type {(value: boolean) => void} */
        let resolveReady = () => {};
        /** @type {(reason: unknown) => void} */
        let rejectReady = () => {};
        const ready = /** @type {Promise<boolean>} */ (new Promise((resolve, reject) => {
            resolveReady = resolve;
            rejectReady = reject;
        }));
        const requestCancel = () => { if (!committing) finish('cancel'); };

        /** @param {string} [key] @param {boolean} [error] */
        function message(key = '', error = false) {
            status.setAttribute('role', error ? 'alert' : 'status');
            status.textContent = key ? t(key) : '';
            if (panel.isConnected) reposition();
        }

        /** @param {string} name @param {() => void} run @param {string} [className] */
        function button(name, run, className) {
            const control = el('button', className, t(name));
            control.type = 'button';
            control.addEventListener('click', event => {
                event.stopPropagation();
                if (!control.disabled) run();
            });
            return control;
        }

        /** @param {ParamKey | 'strength'} key @param {number} min @param {() => number} get @param {(value: number) => void} set */
        function slider(key, min, get, set) {
            const row = el('div', 'canvas-image-color-row');
            const label = el('label', '', t(key));
            const range = el('input'), numeric = el('input');
            range.type = 'range';
            numeric.type = 'number';
            for (const input of [range, numeric]) {
                input.min = String(min);
                input.max = '100';
                input.step = '1';
                input.setAttribute('aria-label', t(key));
                input.dataset.colorParam = key;
                input.addEventListener('keydown', event => {
                    const delta = ['ArrowRight', 'ArrowUp'].includes(event.key) ? 1
                        : ['ArrowLeft', 'ArrowDown'].includes(event.key) ? -1 : 0;
                    if (!delta || input.disabled || event.altKey || event.metaKey || event.ctrlKey) return;
                    event.preventDefault();
                    set(sliderValue(get() + delta * (event.shiftKey ? 10 : 1), min));
                });
                input.addEventListener('dblclick', event => {
                    if (input.disabled) return;
                    event.preventDefault();
                    set(key === 'strength' ? 100 : 0);
                });
            }
            range.addEventListener('input', () => set(sliderValue(range.value, min)));
            numeric.addEventListener('input', () => {
                if (numeric.value !== '') set(sliderValue(numeric.value, min));
            });
            const completeEntry = () => { numeric.value = String(get()); };
            numeric.addEventListener('change', completeEntry);
            numeric.addEventListener('blur', completeEntry);
            label.append(range);
            row.append(label, numeric);
            body.append(row);
            return { range, numeric, get };
        }

        const automatic = button('auto', () => {
            toggleSampling(false);
            if (preview) acceptSuggestion(analyzeGrayWorld(preview.original.data));
        });
        const sampler = button('sampler', () => toggleSampling(!sampling));
        sampler.setAttribute('aria-keyshortcuts', 'W');
        shortcuts.append(automatic, sampler);
        const strengthRow = slider('strength', 0, () => strength, value => {
            if (!suggestion || busy) return;
            strength = value;
            writeSuggestion();
        });
        const adjustmentRows = PARAM_KEYS.map(key => slider(key, -100, () => params[key], value => {
            if (busy || !preview) return;
            if (key === 'temperature' || key === 'tint') {
                suggestion = null;
                strength = 100;
            }
            params[key] = value;
            update();
        }));

        const compare = button('compare', () => {});
        compare.setAttribute('aria-keyshortcuts', '\\');
        const reset = button('reset', () => {
            params = normalizeParams();
            suggestion = null;
            strength = 100;
            toggleSampling(false);
            message();
            update();
        });
        const cancel = button('cancel', requestCancel);
        const applyButton = button('apply', () => { void submit(); }, 'canvas-image-color-apply');
        footer.append(compare, reset, cancel, applyButton);
        panel.append(title, canvas, shortcuts, body);
        if (targets.length > 1) {
            const primaryOption = el('option', '', t('scopePrimary'));
            primaryOption.value = 'primary';
            const selectionOption = el('option', '', format('imageColor.scopeSelection', { count: targets.length }));
            selectionOption.value = 'selection';
            scope.setAttribute('aria-label', t('scope'));
            scope.append(primaryOption, selectionOption);
            scope.value = 'primary';
            panel.append(scope);
        }
        panel.append(status, footer);

        function update() {
            const unavailable = busy || !preview;
            for (const row of [strengthRow, ...adjustmentRows]) {
                const value = String(row.get());
                row.range.value = value;
                if (row.numeric.value === '' || Number(row.numeric.value) !== Number(value)) row.numeric.value = value;
                row.range.disabled = row.numeric.disabled = unavailable || (row === strengthRow && !suggestion);
            }
            for (const control of [automatic, sampler, compare, reset, scope, applyButton]) control.disabled = unavailable;
            cancel.disabled = committing;
            applyButton.setAttribute('aria-busy', String(busy));
            if (preview && !frame) frame = requestAnimationFrame(() => {
                frame = 0;
                if (closed || !preview) return;
                const { ctx, original, output } = preview;
                if (compareKey || comparePointer !== null || buttonKey) ctx.putImageData(original, 0, 0);
                else {
                    output.data.set(original.data);
                    apply(output.data, params);
                    ctx.putImageData(output, 0, 0);
                }
            });
        }

        function writeSuggestion() {
            if (!suggestion) return;
            params.temperature = sliderValue(suggestion.temperature * strength / 100);
            params.tint = sliderValue(suggestion.tint * strength / 100);
            update();
        }

        /** @param {WhiteBalance | null} value */
        function acceptSuggestion(value) {
            if (!value) { message('noSample', true); return; }
            message();
            suggestion = value;
            writeSuggestion();
        }

        /** @param {boolean} value */
        function toggleSampling(value) {
            if (value) message('sampleHint');
            else if (sampling) message();
            sampling = value;
            sampler.setAttribute('aria-pressed', String(value));
            canvas.classList.toggle('is-sampling', value);
            canvas.tabIndex = value ? 0 : -1;
        }
        toggleSampling(false);

        /** @param {number} x @param {number} y */
        function pick(x, y) {
            if (!image || !sampling || busy) return;
            toggleSampling(false);
            try { acceptSuggestion(sampleImage(image, x, y)); }
            catch { message('noSample', true); }
            sampler.focus({ preventScroll: true });
        }
        canvas.addEventListener('click', event => {
            const rect = canvas.getBoundingClientRect();
            if (rect.width && rect.height) pick((event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height);
        });
        canvas.addEventListener('keydown', event => {
            if (!sampling || !['Enter', ' '].includes(event.key)) return;
            event.preventDefault();
            pick(.5, .5);
        });

        function updateCompare() {
            compare.setAttribute('aria-pressed', String(Boolean(compareKey || comparePointer !== null || buttonKey)));
            update();
        }
        function clearCompare() {
            if (comparePointer === null && !compareKey && !buttonKey) return;
            comparePointer = null;
            compareKey = false;
            buttonKey = '';
            updateCompare();
        }
        compare.setAttribute('aria-pressed', 'false');
        compare.addEventListener('pointerdown', event => {
            if (event.button !== 0 || compare.disabled || comparePointer !== null) return;
            comparePointer = event.pointerId;
            compare.setPointerCapture(event.pointerId);
            updateCompare();
        });
        for (const type of /** @type {const} */ (['pointerup', 'pointercancel', 'lostpointercapture'])) {
            compare.addEventListener(type, event => {
                if (event.pointerId !== comparePointer) return;
                comparePointer = null;
                updateCompare();
            });
        }
        compare.addEventListener('keydown', event => {
            if (compare.disabled || !['Enter', ' '].includes(event.key)) return;
            event.preventDefault();
            buttonKey = event.key;
            updateCompare();
        });
        compare.addEventListener('keyup', event => {
            if (event.key !== buttonKey) return;
            event.preventDefault();
            buttonKey = '';
            updateCompare();
        });
        compare.addEventListener('blur', () => {
            if (!buttonKey) return;
            buttonKey = '';
            updateCompare();
        });

        /** @param {KeyboardEvent} event */
        function consume(event) { event.preventDefault(); event.stopPropagation(); }
        /** @param {KeyboardEvent} event */
        function keyDown(event) {
            if (event.defaultPrevented || event.altKey || event.metaKey || event.ctrlKey || event.isComposing) return;
            const target = /** @type {HTMLElement | null} */ (event.target);
            const inPanel = !!target && panel.contains(target);
            const textInput = target?.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target?.tagName ?? '');
            const isTop = !escapeStack || !('top' in escapeStack) || escapeStack.top?.close === requestCancel;
            if (!isTop) return;
            if (event.key === 'Escape' && (!escapeStack || (inPanel && textInput))) {
                consume(event);
                requestCancel();
            } else if (event.key === '\\' && preview && !busy && (inPanel || !textInput)) {
                consume(event);
                if (!compareKey) { compareKey = true; updateCompare(); }
            } else if (event.key.toLowerCase() === 'w' && inPanel && !textInput && preview && !busy) {
                consume(event);
                if (!event.repeat) toggleSampling(!sampling);
            }
        }
        /** @param {KeyboardEvent} event */
        function keyUp(event) {
            if (event.key !== '\\' || !compareKey) return;
            consume(event);
            compareKey = false;
            updateCompare();
        }

        for (const type of ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'dblclick', 'contextmenu', 'keydown', 'keyup', 'wheel']) {
            panel.addEventListener(type, event => event.stopPropagation());
        }

        function reposition() {
            if (closed) return;
            const at = typeof anchor === 'function' ? anchor() : anchor;
            if (!at || ('isConnected' in at && !at.isConnected)) return;
            const rect = 'getBoundingClientRect' in at ? at.getBoundingClientRect() : at;
            const gap = 8, visualWindow = window.visualViewport;
            const leftEdge = (visualWindow?.offsetLeft ?? 0) + gap;
            const topEdge = (visualWindow?.offsetTop ?? 0) + gap;
            const rightEdge = leftEdge - 2 * gap + (visualWindow?.width ?? window.innerWidth);
            const bottomEdge = topEdge - 2 * gap + (visualWindow?.height ?? window.innerHeight);
            panel.style.maxWidth = `${Math.max(0, rightEdge - leftEdge)}px`;
            panel.style.maxHeight = `${Math.max(0, bottomEdge - topEdge)}px`;
            const { width, height } = panel.getBoundingClientRect();
            const roomRight = rect.right + gap + width <= rightEdge, roomLeft = rect.left - gap - width >= leftEdge;
            const x = roomRight ? rect.right + gap : roomLeft ? rect.left - gap - width : rect.left;
            let y = rect.top;
            if (!roomRight && !roomLeft) {
                if (rect.bottom + gap + height <= bottomEdge) y = rect.bottom + gap;
                else if (rect.top - gap - height >= topEdge) y = rect.top - gap - height;
            }
            panel.style.left = `${clamp(x, leftEdge, rightEdge - width)}px`;
            panel.style.top = `${clamp(y, topEdge, bottomEdge - height)}px`;
        }

        /** @param {CloseReason} reason */
        function finish(reason) {
            if (closed) return;
            closed = true;
            resolveReady(false);
            if (active === session) active = null;
            releaseEscape?.();
            if (frame) cancelAnimationFrame(frame);
            window.removeEventListener('keydown', keyDown, true);
            window.removeEventListener('keyup', keyUp, true);
            window.removeEventListener('blur', clearCompare);
            window.removeEventListener('resize', reposition);
            window.visualViewport?.removeEventListener('resize', reposition);
            window.visualViewport?.removeEventListener('scroll', reposition);
            image = preview = null;
            canvas.width = canvas.height = 0;
            const focused = document.activeElement;
            panel.remove();
            if (reason !== 'replaced' && focused && panel.contains(focused)) {
                const at = typeof anchor === 'function' ? anchor() : anchor;
                if (at && 'focus' in at && at instanceof HTMLElement) at.focus({ preventScroll: true });
            }
            onClose?.(reason);
        }

        async function submit() {
            if (closed || busy || !image) return;
            const selected = scope.value === 'selection' ? targets : [primary];
            const values = normalizeParams(params);
            busy = true;
            toggleSampling(false);
            clearCompare();
            message('applying');
            update();
            /** @type {Change[]} */
            const changes = [];
            try {
                // Sequential export bounds peak canvas memory and cannot publish a partial selection.
                for (const target of selected) {
                    const original = target === primary ? image : await loadImage(target.url, target);
                    if (closed) return;
                    const blob = await bake(original, values);
                    if (closed) return;
                    const file = await encode(target, blob);
                    if (closed) return;
                    if (!file || typeof file !== 'object' || Array.isArray(file)) throw new Error('No encoded file');
                    changes.push({ target, file, metadata: { source_asset_id: target.source_asset_id, op: 'color', params: { ...values }, algo_version: ALGO_VERSION } });
                }
                committing = true;
                update();
                await commit(changes);
            } catch {
                if (!closed) {
                    busy = committing = false;
                    message('applyError', true);
                    update();
                }
                return;
            }
            if (!closed) finish('applied');
        }

        async function loadPreview() {
            try {
                const originalImage = await loadImage(primary.url, primary);
                if (closed) return false;
                const { width, height } = imageSize(originalImage);
                const scale = Math.min(1, 512 / Math.max(width, height));
                canvas.width = Math.max(1, Math.round(width * scale));
                canvas.height = Math.max(1, Math.round(height * scale));
                const ctx = context2d(canvas);
                ctx.drawImage(originalImage, 0, 0, canvas.width, canvas.height);
                preview = {
                    ctx, original: ctx.getImageData(0, 0, canvas.width, canvas.height),
                    output: ctx.createImageData(canvas.width, canvas.height),
                };
                image = originalImage;
            } catch {
                if (!closed) message('loadError', true);
                return false;
            }
            canvas.hidden = false;
            message();
            update();
            return true;
        }

        const previous = active;
        active = session;
        previous?.finish('replaced');
        if (!closed) {
            document.body.append(panel);
            window.addEventListener('keydown', keyDown, true);
            window.addEventListener('keyup', keyUp, true);
            window.addEventListener('blur', clearCompare);
            window.addEventListener('resize', reposition);
            window.visualViewport?.addEventListener('resize', reposition);
            window.visualViewport?.addEventListener('scroll', reposition);
            releaseEscape = escapeStack?.push(requestCancel, 'image-color');
            message('loading');
            update();
            panel.focus({ preventScroll: true });
        }
        if (!closed) void loadPreview().then(resolveReady, rejectReady);
        return { close: () => finish('cancel'), reposition, ready };
    }

    const api = { ALGO_VERSION, normalizeParams, apply, analyzeGrayWorld, sampleNeutral, open };
    Object.assign(globalThis, { CanvasImageColor: api });
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
