/* In-page driver for tests/trash_panel_browser.py (issues #210 / #211).
 *
 * Installed by the Python runner via CDP Runtime.evaluate. Keeps in-page work
 * (reading the rendered trash list, sampling computed styles) in one auditable
 * place; user gestures (node × clicks, opening the panel) are done by the
 * Python side with native CDP mouse events.
 */
(function () {
    "use strict";

    function sleep(ms) {
        return new Promise(function (resolve) { setTimeout(resolve, ms); });
    }

    /* Rendered trash list, in DOM order (the user-visible ordering). */
    function readTrashDom() {
        var items = Array.prototype.slice.call(
            document.querySelectorAll("#smartTrashList .smart-trash-item")
        );
        return items.map(function (el) {
            var name = el.querySelector(".smart-trash-name");
            var sub = el.querySelector(".smart-trash-sub");
            return {
                asset_id: el.getAttribute("data-trash-asset") || "",
                name: name ? (name.getAttribute("title") || name.textContent) : "",
                sub: sub ? sub.textContent : "",
            };
        });
    }

    /* Resolve a custom property to the same rgb()/rgba() string that
     * getComputedStyle produces for real elements, so Python can compare
     * element colors against theme variables directly. */
    function resolvedColor(varName) {
        var probe = document.createElement("span");
        probe.style.color = "var(" + varName + ")";
        document.body.appendChild(probe);
        var value = getComputedStyle(probe).color;
        probe.remove();
        return value;
    }

    function elementStyle(selector, prop) {
        var el = document.querySelector(selector);
        if (!el) return null;
        return getComputedStyle(el)[prop];
    }

    /* #211: panel element colors + the theme variables they must track. */
    function readThemeStyles() {
        var vars = ["--panel", "--card", "--soft", "--text", "--muted", "--faint", "--line", "--accent"];
        var resolved = {};
        vars.forEach(function (name) { resolved[name] = resolvedColor(name); });
        return {
            theme_class: document.documentElement.classList.contains("theme-dark") ? "dark" : "light",
            vars: resolved,
            elements: {
                item_border: elementStyle("#smartTrashList .smart-trash-item", "borderTopColor"),
                name_color: elementStyle("#smartTrashList .smart-trash-name", "color"),
                sub_color: elementStyle("#smartTrashList .smart-trash-sub", "color"),
                icon_color: elementStyle("#smartTrashList .smart-trash-item .smart-trash-icon", "color"),
                thumb_border: elementStyle("#smartTrashList .smart-trash-thumb-wrap", "borderTopColor"),
                thumb_bg: elementStyle("#smartTrashList .smart-trash-thumb-wrap", "backgroundColor"),
            },
        };
    }

    window.__trashPanelTest = {
        readTrashDom: readTrashDom,
        readThemeStyles: readThemeStyles,
        /* Panel rendered (loading state replaced by items)? */
        trashItemsRendered: function (count) {
            var list = document.querySelector("#smartTrashList");
            if (!list) return { rendered: false, count: 0 };
            var items = list.querySelectorAll(".smart-trash-item");
            return { rendered: count === 0 || items.length >= count, count: items.length };
        },
        trashOpenButtonVisible: function () {
            var btn = document.querySelector("#smartTrashOpenBtn");
            return Boolean(btn) && !btn.hidden;
        },
        _sleep: sleep,
    };
    return { installed: true };
})()
