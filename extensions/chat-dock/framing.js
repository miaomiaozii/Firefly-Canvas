(() => {
  'use strict';

  // User ruling 2026-11-28 on #515 ("515 AC"): these sites may be embedded in this extension's own pages
  // only after the user switches each one on. Claude is excluded by the same ruling.
  const KEY = 'firefly.chat-dock.framing.v1';
  const SET_FRAMING = 'firefly.chat-dock.set-framing';
  // `csp` marks sites that block framing through CSP frame-ancestors. DNR can only replace the whole
  // header, so for them the site's own CSP (including its XSS layer) is lost inside the panel; the
  // replacement keeps framing limited to this extension. Gemini blocks with X-Frame-Options only, so
  // its CSP is left untouched.
  const SITES = Object.freeze({
    'chatgpt.com': Object.freeze({ruleId: 5151, csp: true}),
    'gemini.google.com': Object.freeze({ruleId: 5152, csp: false}),
    'chat.deepseek.com': Object.freeze({ruleId: 5153, csp: true}),
    'chat.qwen.ai': Object.freeze({ruleId: 5154, csp: true}),
  });
  const RULE_IDS = Object.freeze(Object.values(SITES).map(site => site.ruleId));

  function flags(stored) {
    const value = stored?.[KEY];
    const enabled = {};
    if (!value || typeof value !== 'object' || Array.isArray(value)) return enabled;
    for (const site of Object.keys(SITES)) if (value[site] === true) enabled[site] = true;
    return enabled;
  }

  function rule(site, extensionId) {
    const config = SITES[site];
    const responseHeaders = [{header: 'x-frame-options', operation: 'remove'}];
    if (config.csp) {
      responseHeaders.push({header: 'content-security-policy', operation: 'set', value: `frame-ancestors chrome-extension://${extensionId}`});
    }
    return {
      id: config.ruleId, priority: 1,
      action: {type: 'modifyHeaders', responseHeaders},
      // topDomains (not initiatorDomains) keeps in-frame navigation working; the top page must be ours.
      condition: {urlFilter: `|https://${site}/`, requestDomains: [site], resourceTypes: ['sub_frame'], topDomains: [extensionId]},
    };
  }

  function validCommand(data) {
    return Boolean(data && typeof data === 'object' && !Array.isArray(data) && Object.keys(data).length === 3 &&
      data.action === SET_FRAMING && Object.hasOwn(SITES, data.site) && typeof data.enabled === 'boolean');
  }

  globalThis.FireflyChatFraming = Object.freeze({KEY, SET_FRAMING, SITES, RULE_IDS, flags, rule, validCommand});
})();
