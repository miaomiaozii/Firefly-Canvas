// #249：Escape 栈统一管理（static/js/ 全局面板/弹窗）。
//
// 背景：以前每个全局模态各挂一个 document/window keydown 监听，按一次 Escape
// 会同时触发多个监听——一次按键关多层，事件还会穿透到画布底层（弹层没关干净、
// 选中态被误取消）。ComfyArt canvas 的做法是每个浮层只管自己：打开时登记
// Escape 处理、关闭时注销、Escape 只结算当前最顶层（见
// docs/reference-comfyart-canvas.md 各面板的「Escape 捕获阶段」条目）。
//
// 本模块提供 ~24 行的栈核心：
//   window.FireflyEscapeStack.push(closeFn, label) → 返回 release()；
//   唯一的全局捕获监听在 Escape 时只调用栈顶 close 并吞掉事件
//   （preventDefault + stopPropagation），栈空时不干预——画布自身处理不变。
//   输入目标（input/textarea/select/contentEditable）上的 Escape 不拦截，
//   交给元素自己的 keydown 处理。
//
// 接入约定（全局面板/弹窗）：打开时 push、关闭时 release，不要再自己挂
// 全局 Escape 监听；元素级（输入框/菜单内）的 Escape 监听保持原样。
(function(){
    const stack = [];
    const isEditableTarget = target => {
        if(!target) return false;
        if(target.isContentEditable) return true;
        const tag = String(target.tagName || '').toUpperCase();
        return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
    };
    window.FireflyEscapeStack = {
        push(closeFn, label = ''){
            const entry = { close: closeFn, label };
            stack.push(entry);
            return () => { const i = stack.lastIndexOf(entry); if(i >= 0) stack.splice(i, 1); };
        },
        get size(){ return stack.length; },
        get top(){ return stack[stack.length - 1] || null; },
        _stack: stack,
    };
    document.addEventListener('keydown', event => {
        if(event.key !== 'Escape' || event.altKey || event.ctrlKey || event.metaKey) return;
        if(isEditableTarget(event.target)) return;
        const entry = stack[stack.length - 1];
        if(!entry) return;
        event.preventDefault();
        event.stopPropagation();
        entry.close();
    }, true);
})();
