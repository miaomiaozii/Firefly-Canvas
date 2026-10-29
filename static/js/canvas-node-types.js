// Canvas node type constants (#184). Classic-script globals shared by every
// canvas module (this file must load first in static/smart-canvas.html).
// Values must stay identical to app/constants.py NODE_TYPE_* — the parity is
// locked by tests/test_node_type_constants.py.
const NODE_TYPE_PROMPT = 'smart-prompt';
const NODE_TYPE_IMAGE = 'smart-image';
const NODE_TYPE_AGENT = 'smart-agent';
const NODE_TYPE_CONVERSATION = 'smart-conversation';
const NODE_TYPE_TASK = 'agent-task';
const NODE_TYPE_LOOP = 'smart-loop';
const NODE_TYPE_GROUP = 'smart-group';
const NODE_TYPE_LOCAL_EDIT = 'local-edit';

// `project-canvas-mutations.js` is a UMD module: tests load it through Node's
// require(), where classic-script lexical globals simply do not exist. Expose the
// same values as one object on globalThis (top-level `const` is NOT a globalThis
// property) and through module.exports, so both environments read one source.
const FireflyNodeTypes = {
    NODE_TYPE_PROMPT, NODE_TYPE_IMAGE, NODE_TYPE_AGENT, NODE_TYPE_CONVERSATION,
    NODE_TYPE_TASK, NODE_TYPE_LOOP, NODE_TYPE_GROUP, NODE_TYPE_LOCAL_EDIT,
};

if(typeof globalThis !== 'undefined'){
    globalThis.FireflyNodeTypes = FireflyNodeTypes;
}

if(typeof module === 'object' && module.exports){
    module.exports = FireflyNodeTypes;
}
