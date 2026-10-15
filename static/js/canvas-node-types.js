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
