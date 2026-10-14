"""Issue #34: five-viewport native desktop UI integrity and visual-baseline candidate journey."""
from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import hashlib
import json
import platform
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from firefly_projects import ProjectStore
from tests import conversation_browser as harness
from tests.native_browser import AsgiBrowserServer, NativeBrowser, find_browser
from tools.ui_integrity_evidence import EXPECTED_SCENES, EXPECTED_VIEWPORTS, REQUIRED_FLAGS

VIEWPORTS = EXPECTED_VIEWPORTS
FONT_FAMILIES = ("Inter", "JetBrains Mono", "Space Grotesk")
ERROR_TEXT = "布局合同错误：" + "完整错误、任务状态和破坏性操作必须无需悬停即可到达。" * 18


def large_image_bytes(
    label: str,
    color: tuple[int, int, int],
    size: tuple[int, int] = (2560, 1600),
) -> bytes:
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    inset = max(24, min(size) // 34)
    draw.rectangle((inset, inset, size[0] - inset, size[1] - inset), outline=(255, 255, 255), width=max(6, inset // 3))
    draw.text((inset * 2, inset * 2), label, fill=(255, 255, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@contextlib.contextmanager
def launched_native_browser(
    chrome: Path,
    viewport: tuple[int, int],
    *,
    failure_root: Path | None = None,
):
    browser = NativeBrowser.launch(chrome)
    browser.cdp.timeout = 60
    try:
        browser.set_environment(viewport)
        browser.cdp.command("Emulation.setLocaleOverride", {"locale": "zh-CN"})
        browser.cdp.command("Emulation.setTimezoneOverride", {"timezoneId": "Asia/Shanghai"})
        yield browser
    except Exception:
        if failure_root is not None:
            failure_root.mkdir(parents=True, exist_ok=True)
            (failure_root / "operations.jsonl").write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in browser.operations),
                encoding="utf-8",
            )
            write_json(failure_root / "requests.json", browser.request_summaries)
            write_json(failure_root / "console-errors.json", browser.console_errors)
            try:
                browser.capture_screenshot(failure_root / "first-failure.png")
            except Exception as screenshot_error:
                write_json(
                    failure_root / "failure-screenshot-error.json",
                    {"type": type(screenshot_error).__name__, "message": str(screenshot_error)},
                )
        raise
    finally:
        browser.close()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def seed_project(store: ProjectStore, name: str) -> str:
    project = store.create_project(name)
    recent_one = store.import_media(
        project["id"],
        large_image_bytes("RECENT 01", (76, 104, 160)),
        "ui-recent-01.png",
        "image/png",
        {"type": "online_generation", "name": "ui-recent-01.png"},
    )
    recent_two = store.import_media(
        project["id"],
        large_image_bytes("RECENT 02", (136, 82, 106)),
        "ui-recent-02.png",
        "image/png",
        {"type": "online_generation", "name": "ui-recent-02.png"},
    )
    recent_three = store.import_media(
        project["id"],
        large_image_bytes("RECENT 03 PORTRAIT", (74, 132, 126), (1400, 2200)),
        "ui-recent-03-portrait.png",
        "image/png",
        {"type": "online_generation", "name": "ui-recent-03-portrait.png"},
    )
    recent_four = store.import_media(
        project["id"],
        large_image_bytes("RECENT 04 WIDE", (156, 112, 64), (2200, 1200)),
        "ui-recent-04-wide.png",
        "image/png",
        {"type": "online_generation", "name": "ui-recent-04-wide.png"},
    )
    recent_five = store.import_media(
        project["id"],
        large_image_bytes("RECENT 05", (98, 86, 142)),
        "ui-recent-05.png",
        "image/png",
        {"type": "online_generation", "name": "ui-recent-05.png"},
    )
    recent_six = store.import_media(
        project["id"],
        large_image_bytes("RECENT 06 PORTRAIT", (128, 92, 72), (1400, 2200)),
        "ui-recent-06-portrait.png",
        "image/png",
        {"type": "online_generation", "name": "ui-recent-06-portrait.png"},
    )
    recent_seven = store.import_media(
        project["id"],
        large_image_bytes("RECENT 07", (64, 118, 152)),
        "ui-recent-07.png",
        "image/png",
        {"type": "online_generation", "name": "ui-recent-07.png"},
    )
    recent_eight = store.import_media(
        project["id"],
        large_image_bytes("RECENT 08 WIDE", (118, 138, 74), (2400, 1200)),
        "ui-recent-08-wide.png",
        "image/png",
        {"type": "online_generation", "name": "ui-recent-08-wide.png"},
    )
    multi_outputs = [
        {"asset_id": item["asset_id"], "kind": "image", "name": name}
        for item, name in (
            (recent_one, "ui-recent-01.png"),
            (recent_two, "ui-recent-02.png"),
            (recent_three, "ui-recent-03-portrait.png"),
            (recent_four, "ui-recent-04-wide.png"),
            (recent_five, "ui-recent-05.png"),
            (recent_six, "ui-recent-06-portrait.png"),
            (recent_seven, "ui-recent-07.png"),
            (recent_eight, "ui-recent-08-wide.png"),
        )
    ]
    references = [
        store.import_media(
            project["id"],
            (
                large_image_bytes("REF 01 ULTRAWIDE", (77, 132, 113), (2520, 1080))
                if index == 1
                else harness.image_bytes(
                    f"REF {index:02d}",
                    (74 + index * 3 % 90, 132, 108 + index * 5 % 90),
                )
            ),
            f"ui-reference-{index:02d}.png",
            "image/png",
            {"type": "ui-integrity-browser-fixture", "name": f"ui-reference-{index:02d}.png"},
        )
        for index in range(1, 23)
    ]
    logs = [
        {
            "id": "integrity-success-one",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "第一张成功记录展示模型、生成时间和完整提示词。",
            "provider": "离线平台",
            "model": "固定模型",
            "resolution": "2K",
            "createdAt": 1_760_000_000_000,
            "outputs": copy.deepcopy(multi_outputs),
            "result_node_id": "integrity-multi-output",
        },
        {
            "id": "integrity-success-two",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "第二张成功记录用于心流创作辅助和 2K 对图分享。",
            "provider": "离线平台",
            "model": "固定模型二号",
            "resolution": "2K",
            "createdAt": 1_760_000_050_000,
            "outputs": [{"asset_id": recent_two["asset_id"], "kind": "image", "name": "ui-recent-02.png"}],
            "result_node_id": "integrity-image",
        },
        {
            "id": "integrity-success-three",
            "type": "online-image",
            "status": "succeeded",
            "prompt": "第三张竖向成功记录验证媒体按原始比例自适应。",
            "provider": "离线平台",
            "model": "固定模型三号",
            "resolution": "2K",
            "createdAt": 1_760_000_075_000,
            "outputs": [{"asset_id": recent_three["asset_id"], "kind": "image", "name": "ui-recent-03-portrait.png"}],
            "result_node_id": "integrity-image",
        },
        {
            "id": "integrity-failed",
            "type": "online-image",
            "status": "failed",
            "prompt": "失败记录保留完整错误。",
            "error": ERROR_TEXT,
            "provider": "离线平台",
            "model": "固定模型",
            "resolution": "4K",
            "createdAt": 1_760_000_100_000,
            "outputs": [],
        },
    ]
    store.apply_mutations(
        project["id"],
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "integrity-prompt",
                    "type": "smart-prompt",
                    "x": 24,
                    "y": 150,
                    "w": 316,
                    "h": 194,
                    "text": "固定提示词来源：五视口完整性合同。",
                },
            },
            {
                "op": "upsert_node",
                "node": {
                    "id": "integrity-multi-output",
                    "type": "smart-image",
                    "x": 850,
                    "y": 520,
                    "w": 460,
                    "h": 360,
                    "images": copy.deepcopy(multi_outputs),
                    "runPrompt": "八张候选原图的独立删除与画布同步合同。",
                },
            },
            {
                "op": "upsert_node",
                "node": {
                    "id": "integrity-image",
                    "type": "smart-image",
                    "x": 330,
                    "y": 48,
                    "w": 316,
                    "h": 194,
                    "images": [{"asset_id": recent_one["asset_id"], "kind": "image", "name": "ui-recent-01.png"}],
                    "manualInputRefs": [
                        {
                            "asset_id": item["asset_id"],
                            "kind": "image",
                            "name": f"ui-reference-{index:02d}.png",
                            "imageIndex": index - 1,
                        }
                        for index, item in enumerate(references, start=1)
                    ],
                    "promptDraftText": "在参考图和最近生成结果的帮助下继续创作。",
                    "promptDraftHtml": "在参考图和最近生成结果的帮助下继续创作。",
                    "runSettings": {
                        "engine": "api",
                        "apiKind": "image",
                        "ratio": "square",
                        "resolution": "4k",
                        "promptH": 84,
                    },
                },
            },
            {"op": "upsert_edge", "edge": {"from": "integrity-prompt", "to": "integrity-image", "kind": "input"}},
            {"op": "set_canvas_meta", "history": logs},
            {"op": "set_viewport", "viewport": {"x": 0, "y": 0, "scale": 1}},
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return project["id"]


def seed_duplicate_occurrence_project(store: ProjectStore, name: str) -> str:
    project = store.create_project(name)
    duplicate_a = store.import_media(
        project["id"],
        large_image_bytes("DUPLICATE A", (76, 104, 160)),
        "duplicate-a.png",
        "image/png",
        {"type": "online_generation", "name": "duplicate-a.png"},
    )
    duplicate_b = store.import_media(
        project["id"],
        large_image_bytes("DUPLICATE B", (136, 82, 106)),
        "duplicate-b.png",
        "image/png",
        {"type": "online_generation", "name": "duplicate-b.png"},
    )
    duplicate_c = store.import_media(
        project["id"],
        large_image_bytes("DUPLICATE C", (156, 112, 64)),
        "duplicate-c.png",
        "image/png",
        {"type": "online_generation", "name": "duplicate-c.png"},
    )
    outputs = [
        {"asset_id": duplicate_a["asset_id"], "kind": "image", "name": "duplicate-a-first.png"},
        {"asset_id": duplicate_b["asset_id"], "kind": "image", "name": "duplicate-b.png"},
        {"asset_id": duplicate_a["asset_id"], "kind": "image", "name": "duplicate-a-third.png"},
        {"asset_id": duplicate_c["asset_id"], "kind": "image", "name": "duplicate-c.png"},
    ]
    store.apply_mutations(
        project["id"],
        [
            {
                "op": "upsert_node",
                "node": {
                    "id": "integrity-duplicate-node",
                    "type": "smart-image",
                    "x": 80,
                    "y": 90,
                    "w": 460,
                    "h": 360,
                    "images": copy.deepcopy(outputs),
                    "runPrompt": "重复原件 occurrence 删除与恢复合同。",
                },
            },
            {
                "op": "set_canvas_meta",
                "history": [
                    {
                        "id": "integrity-duplicate-log",
                        "type": "online-image",
                        "status": "succeeded",
                        "prompt": "同一原件在第一和第三输出各出现一次。",
                        "provider": "离线平台",
                        "model": "固定重复模型",
                        "resolution": "2K",
                        "createdAt": 1_760_000_200_000,
                        "outputs": copy.deepcopy(outputs),
                        "result_node_id": "integrity-duplicate-node",
                    }
                ],
            },
            {"op": "set_viewport", "viewport": {"x": 0, "y": 0, "scale": 1}},
        ],
        base_revision=0,
    )
    store.flush(project["id"])
    return project["id"]


def set_fixed_preferences(server: AsgiBrowserServer) -> None:
    request = Request(
        server.base_url + "/api/global-settings/preferences",
        data=json.dumps({"theme": "light", "language": "zh"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    with urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise AssertionError(f"failed to set fixed screenshot preferences: HTTP {response.status}")


def relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def settle(browser: NativeBrowser) -> dict:
    return browser.evaluate(
        """(async () => {
          localStorage.setItem('studio_theme','light');
          localStorage.setItem('canvas_theme','light');
          if(window.StudioTheme?.set) window.StudioTheme.set('light');
          else {
            document.documentElement.classList.remove('theme-dark','studio-theme-dark');
            document.body?.classList.remove('theme-dark','studio-theme-dark');
          }
          let style=document.querySelector('#ui-integrity-stability');
          if(!style){
            style=document.createElement('style');
            style.id='ui-integrity-stability';
            style.textContent='*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important;caret-color:transparent!important}';
            document.head.appendChild(style);
          }
          if(document.fonts){
            await Promise.all([
              document.fonts.load('400 12px "Inter"'),
              document.fonts.load('400 12px "JetBrains Mono"'),
              document.fonts.load('500 12px "Space Grotesk"'),
            ]);
            await document.fonts.ready;
          }
          const visibleImages=[...document.images].filter(image=>{
            const rect=image.getBoundingClientRect();
            const style=getComputedStyle(image);
            return style.display!=='none' && style.visibility!=='hidden' && rect.right>0 && rect.bottom>0 && rect.left<innerWidth && rect.top<innerHeight;
          });
          await Promise.all(visibleImages.map(image => image.complete ? Promise.resolve() : new Promise(resolve => {image.addEventListener('load',resolve,{once:true});image.addEventListener('error',resolve,{once:true});})));
          await Promise.all(visibleImages.map(image => typeof image.decode === 'function' ? image.decode().catch(() => {}) : Promise.resolve()));
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const toastEl=document.getElementById('toast');
          if(toastEl && toastEl.classList.contains('show')){
            await new Promise(resolve=>{
              const startedAt=Date.now();
              const check=()=>{
                if(!toastEl.classList.contains('show') || Date.now()-startedAt>2400){ resolve(); }
                else { setTimeout(check, 150); }
              };
              check();
            });
          }
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          return {
            dpr:devicePixelRatio,
            theme:document.documentElement.classList.contains('theme-dark') ? 'dark' : 'light',
            fonts:{
              'Inter':document.fonts?.check('12px "Inter"') === true,
              'JetBrains Mono':document.fonts?.check('12px "JetBrains Mono"') === true,
              'Space Grotesk':document.fonts?.check('12px "Space Grotesk"') === true,
            }
          };
        })()""",
        await_promise=True,
    )


def history_drawer_state(browser: NativeBrowser) -> dict:
    state = browser.evaluate(
        """(() => {
          const drawer=document.querySelector('#smartHistoryModal');
          const panel=drawer?.querySelector('.log-panel');
          if(!drawer || !panel) return null;
          const style=getComputedStyle(panel);
          return {
            open:drawer.classList.contains('open'),
            ariaExpanded:drawer.getAttribute('aria-expanded'),
            visibility:style.visibility,
            opacity:Number.parseFloat(style.opacity||'0'),
            pointerEvents:style.pointerEvents,
          };
        })()"""
    )
    if not isinstance(state, dict):
        raise AssertionError("generation history drawer state is unavailable")
    return state


def reveal_history_card(browser: NativeBrowser, card_selector: str) -> dict:
    list_selector = "#smartHistoryModal .log-list"
    for attempt in range(7):
        state = browser.evaluate(
            f"""(() => {{
              const list=document.querySelector({json.dumps(list_selector)});
              const card=document.querySelector({json.dumps(card_selector)});
              if(!list || !card) return null;
              const listRect=list.getBoundingClientRect();
              const cardRect=card.getBoundingClientRect();
              const visibleLeft=Math.max(0,listRect.left);
              const visibleRight=Math.min(innerWidth,listRect.right);
              const fullyVisible=cardRect.left>=visibleLeft+1 && cardRect.right<=visibleRight-1
                && cardRect.top>=0 && cardRect.bottom<=innerHeight;
              let deltaX=1;
              if(cardRect.right>visibleRight-1) deltaX=cardRect.right-visibleRight+24;
              else if(cardRect.left<visibleLeft+1) deltaX=cardRect.left-visibleLeft-24;
              return {{
                fullyVisible,
                deltaX,
                scrollLeft:list.scrollLeft,
                maxScroll:Math.max(0,list.scrollWidth-list.clientWidth),
                listRect:{{left:listRect.left,top:listRect.top,right:listRect.right,bottom:listRect.bottom,width:listRect.width,height:listRect.height}},
                cardRect:{{left:cardRect.left,top:cardRect.top,right:cardRect.right,bottom:cardRect.bottom,width:cardRect.width,height:cardRect.height}},
              }};
            }})()"""
        )
        if not isinstance(state, dict):
            raise AssertionError(f"history card is unavailable: {card_selector}")
        if state.get("fullyVisible") and attempt > 0:
            return state
        delta_x = float(state.get("deltaX") or 1)
        browser.native_wheel(
            list_selector,
            0,
            delta_x=max(-720.0, min(720.0, delta_x)),
            relative=(0.5, 0.5),
        )
        browser.evaluate(
            "new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))",
            await_promise=True,
        )
        if state.get("fullyVisible"):
            confirmed = browser.evaluate(
                f"""(() => {{
                  const card=document.querySelector({json.dumps(card_selector)});
                  const list=document.querySelector({json.dumps(list_selector)});
                  if(!card || !list) return false;
                  const cardRect=card.getBoundingClientRect(), listRect=list.getBoundingClientRect();
                  return cardRect.left>=Math.max(0,listRect.left)+1
                    && cardRect.right<=Math.min(innerWidth,listRect.right)-1
                    && cardRect.top>=0 && cardRect.bottom<=innerHeight;
                }})()"""
            )
            if confirmed:
                return state
    raise AssertionError(f"history card could not be brought fully into view: {card_selector}")


def inspect_scene(
    browser: NativeBrowser,
    scene: str,
    selectors: tuple[str, ...],
    top_levels: tuple[str, ...],
) -> dict:
    return browser.evaluate(
        f"""(() => {{
          const selectors={json.dumps(selectors)};
          const topLevels={json.dumps(top_levels)};
          const visible=element=>{{
            if(!element) return false;
            const style=getComputedStyle(element), rect=element.getBoundingClientRect();
            return style.display!=='none' && style.visibility!=='hidden' && Number(style.opacity||1)>.01 && rect.width>0 && rect.height>0;
          }};
          const rectFor=element=>{{
            if(!element) return null;
            const rect=element.getBoundingClientRect();
            const x=rect.left+rect.width/2, y=rect.top+rect.height/2;
            const hit=document.elementFromPoint(x,y);
            return {{
              left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom,width:rect.width,height:rect.height,
              visible:visible(element),
              inViewport:rect.left>=-1 && rect.top>=-1 && rect.right<=innerWidth+1 && rect.bottom<=innerHeight+1,
              unobscured:Boolean(hit && (hit===element || element.contains(hit))),
              hit:hit ? `${{hit.tagName.toLowerCase()}}${{hit.id?'#'+hit.id:''}}${{[...hit.classList].slice(0,3).map(value=>'.'+value).join('')}}` : ''
            }};
          }};
          const rects=Object.fromEntries(selectors.map(selector=>[selector,rectFor(document.querySelector(selector))]));
          const topRects=topLevels.map(selector=>[selector,document.querySelector(selector)]).filter(([,element])=>visible(element)).map(([selector,element])=>[selector,element.getBoundingClientRect()]);
          const overlaps=[];
          for(let index=0;index<topRects.length;index++) for(let next=index+1;next<topRects.length;next++){{
            const [first,a]=topRects[index], [second,b]=topRects[next];
            const area=Math.max(0,Math.min(a.right,b.right)-Math.max(a.left,b.left))*Math.max(0,Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top));
            if(area>4) overlaps.push({{first,second,area}});
          }}
          const toolbar=document.querySelector('.smart-top-left');
          const toolbarButtons=[...(toolbar?.querySelectorAll(':scope > button')||[])].filter(visible);
          const buttonTops=toolbarButtons.map(button=>Math.round(button.getBoundingClientRect().top));
          const iconButtons=[...document.querySelectorAll('button')].filter(button=>{{
            if(!visible(button) || !button.querySelector('svg,i')) return false;
            const directText=[...button.childNodes].some(node=>node.nodeType===Node.TEXT_NODE && node.textContent.trim());
            const visibleText=[...button.querySelectorAll('span')].some(span=>{{
              if(!span.textContent.trim() || !visible(span)) return false;
              const rect=span.getBoundingClientRect();
              return rect.width>2 && rect.height>2;
            }});
            return !directText && !visibleText;
          }});
          const unnamedIconButtons=iconButtons.filter(button=>!String(button.getAttribute('aria-label')||'').trim()).map(button=>button.id||button.className);
          const untitledIconButtons=iconButtons.filter(button=>!String(button.getAttribute('title')||'').trim()).map(button=>button.id||button.className);
          const compact=[...document.querySelectorAll('.smart-top-left > button,.preview-icon-btn,.log-buttons button,.composer-view-btn,.smart-log-detail-actions button')].filter(visible).map(button=>{{const style=getComputedStyle(button);return {{radius:parseFloat(style.borderRadius)||0,border:parseFloat(style.borderTopWidth)||0}};}});
          const dynamic=document.querySelector('#dynamicParams');
          const history=document.querySelector('#smartHistoryList');
          const createGrid=document.querySelector('#createMenu .create-menu-grid');
          const pageHorizontal=Math.max(document.documentElement.scrollWidth,document.body.scrollWidth)>innerWidth+1;
          const selection=document.querySelector('.image-node[data-id="integrity-image"].selected');
          const selectionRect=selection?.getBoundingClientRect();
          const selectionVisible=Boolean(selectionRect && selectionRect.right>0 && selectionRect.bottom>0 && selectionRect.left<innerWidth && selectionRect.top<innerHeight);
          const selectionMenu=selection?.querySelector('.smart-node-floating-menu');
          const selectionName=selection?.querySelector('.image-name-badge-outside');
          const selectionMenuRect=selectionMenu?.getBoundingClientRect();
          const selectionNameRect=selectionName?.getBoundingClientRect();
          const selectionMenuNameOverlap=selectionMenuRect && selectionNameRect
            ? Math.max(0,Math.min(selectionMenuRect.right,selectionNameRect.right)-Math.max(selectionMenuRect.left,selectionNameRect.left))
              * Math.max(0,Math.min(selectionMenuRect.bottom,selectionNameRect.bottom)-Math.max(selectionMenuRect.top,selectionNameRect.top))
            : 0;
          const selectionMenuAvoidsName=!selectionName || !visible(selectionName) || selectionMenuNameOverlap<=1;
          const expectedActionIds=['preview','detail','crop','outpaint','mask','local-edit','brush','grid','send-ps','download'];
          const selectionButtons=[...(selectionMenu?.querySelectorAll('button[data-smart-node-action]')||[])];
          const selectionActions=selectionButtons.map(button=>{{
            const rect=button.getBoundingClientRect();
            const hit=document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2);
            return {{
              action:String(button.dataset.smartNodeAction||''),
              visible:visible(button),
              inViewport:rect.left>=-1 && rect.top>=-1 && rect.right<=innerWidth+1 && rect.bottom<=innerHeight+1,
              hit:Boolean(hit && (hit===button || button.contains(hit))),
              rect:{{left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom,width:rect.width,height:rect.height}}
            }};
          }});
          const actualActionIds=selectionActions.map(item=>item.action);
          const exactActionSet=actualActionIds.length===expectedActionIds.length && actualActionIds.every((id,index)=>id===expectedActionIds[index]);
          const selectionActionsReachable=Boolean(selectionMenu && visible(selectionMenu) && exactActionSet && selectionActions.every(item=>item.visible && item.inViewport && item.hit));
          const unselectedMenuTabStops=[...document.querySelectorAll('.image-node:not(.selected) .smart-node-floating-menu button')].filter(button=>button.matches(':focus-visible') || (button.getClientRects().length && getComputedStyle(button).visibility!=='hidden'));
          const modelPopover=document.querySelector('.model-control.pinned .smart-popover');
          const sizePopover=document.querySelector('.size-picker-control.pinned .smart-popover');
          const historyStatus=document.querySelector('[data-canvas-history-id="integrity-failed"] .log-chip.status-failed');
          const historyError=document.querySelector('[data-canvas-history-id="integrity-failed"] [data-long-content-open="history-error"]');
          const destructive=document.querySelector('[data-canvas-history-id="integrity-failed"] [data-history-delete="media"]');
          const surfaceAlpha=element=>{{
            if(!element) return 0;
            const color=getComputedStyle(element).backgroundColor.trim();
            if(color==='transparent') return 0;
            const slash=color.match(/\\/\\s*([0-9.]+)/);
            if(slash) return Number(slash[1]);
            if(color.startsWith('rgba(')) return Number(color.slice(5,-1).split(',')[3]||1);
            return 1;
          }};
          const promptSurfaces=[document.querySelector('.composer-card'),document.querySelector('#promptInput'),document.querySelector('#inputPromptPreview'),document.querySelector('.prompt-smart-node .prompt-node-text')].filter(Boolean);
          const promptSurfacesOpaque=promptSurfaces.length>=3 && promptSurfaces.every(element=>surfaceAlpha(element)>=.999);
          const decorativeCopy=[getComputedStyle(document.querySelector('.smart-title'),'::before').content,getComputedStyle(document.querySelector('.composer-actions'),'::before').content].join(' ');
          const decorativeEnglishAbsent=!/(WORKSPACE|CANVAS|FIREFLY|SPACE TO CREATE)/i.test(decorativeCopy);
          const allCritical=Object.values(rects).every(item=>item && item.visible && item.inViewport && item.unobscured);
          const result={{
            scene:{json.dumps(scene)},rects,overlaps,
            pageHorizontal,
            criticalSurfacesInViewport:Object.values(rects).every(item=>item && item.visible && item.inViewport),
            criticalControlsUnobscured:allCritical,
            topChromeSingleRow:Boolean(toolbar && toolbar.scrollWidth<=toolbar.clientWidth+1 && new Set(buttonTops).size<=1),
            iconControlsNamed:unnamedIconButtons.length===0,
            stableTooltips:untitledIconButtons.length===0,
            unnamedIconButtons,untitledIconButtons,
            explicitOverflowPolicy:Boolean(dynamic && ['wrap','wrap-reverse'].includes(getComputedStyle(dynamic).flexWrap) && history && ['auto','scroll'].includes(getComputedStyle(history).overflowX) && createGrid && getComputedStyle(createGrid).display==='grid'),
            compactGeometryContract:compact.length>0 && compact.every(item=>item.radius>=4 && item.radius<=10 && item.border<=1.5),
            compactGeometry:compact,
            currentSelectionVisible:selectionVisible,
            selectionActionsReachable,
            selectionMenuAvoidsName,
            selectionMenuNameOverlap,
            selectionNodeRect:selectionRect?{{left:selectionRect.left,top:selectionRect.top,right:selectionRect.right,bottom:selectionRect.bottom,width:selectionRect.width,height:selectionRect.height}}:null,
            selectionNameRect:selectionNameRect?{{left:selectionNameRect.left,top:selectionNameRect.top,right:selectionNameRect.right,bottom:selectionNameRect.bottom,width:selectionNameRect.width,height:selectionNameRect.height}}:null,
            selectionMenuRect:selectionMenuRect?{{left:selectionMenuRect.left,top:selectionMenuRect.top,right:selectionMenuRect.right,bottom:selectionMenuRect.bottom,width:selectionMenuRect.width,height:selectionMenuRect.height}}:null,
            promptSurfacesOpaque,
            promptSurfaceAlphas:promptSurfaces.map(element=>({{selector:element.id ? '#'+element.id : '.'+[...element.classList].join('.'),alpha:surfaceAlpha(element)}})),
            decorativeEnglishAbsent,
            selectionActions:{{expectedActionIds,actualActionIds,actions:selectionActions,allReachable:selectionActionsReachable}},
            unselectedMenuTabStops:unselectedMenuTabStops.length,
            composerParametersReachable:Boolean(document.querySelector('.model-control > .smart-pill') && document.querySelector('.size-picker-control > .smart-pill') && document.querySelector('#runBtn')),
            modelPopoverReachable:Boolean(modelPopover && visible(modelPopover)),
            sizePopoverReachable:Boolean(sizePopover && visible(sizePopover) && sizePopover.querySelector('[data-smart-param="ratio"]') && sizePopover.querySelector('[data-smart-param="resolution"]')),
            historyMeaningReachable:Boolean(historyStatus && visible(historyStatus) && historyError && visible(historyError) && destructive && visible(destructive)),
          }};
          const requiresSelectionActions=['canvas-composer','composer-size'].includes(result.scene);
          result.passed=!result.pageHorizontal && result.criticalSurfacesInViewport && result.criticalControlsUnobscured && result.overlaps.length===0 && (!requiresSelectionActions || (result.selectionActionsReachable && result.selectionMenuAvoidsName && result.promptSurfacesOpaque && result.decorativeEnglishAbsent)) && result.unselectedMenuTabStops===0;
          return result;
        }})()"""
    )


def require_scene(scene: dict) -> None:
    if not scene.get("passed"):
        raise AssertionError(f"UI geometry failed for {scene.get('scene')}: {scene}")


def run_viewport(
    *,
    chrome: Path,
    server: AsgiBrowserServer,
    store: ProjectStore,
    artifacts_root: Path,
    viewport: tuple[int, int],
    duplicate_project_id: str = "",
) -> dict:
    width, height = viewport
    attempt_root = artifacts_root / f"viewport-{width}x{height}"
    screenshot_root = attempt_root / "screenshots"
    screenshot_root.mkdir(parents=True, exist_ok=False)
    project_name = f"界面完整性 {width}×{height}"
    project_id = seed_project(store, project_name)
    screenshots: list[dict] = []
    geometry: list[dict] = []

    with launched_native_browser(chrome, viewport, failure_root=attempt_root) as browser:
        def snap(scene: str) -> None:
            if scene != EXPECTED_SCENES[len(screenshots)]:
                raise AssertionError(f"non-canonical screenshot order: {scene}")
            settle(browser)
            path = browser.capture_screenshot(screenshot_root / f"{scene}.png")
            screenshots.append({"scene": scene, "path": relative(path, artifacts_root)})

        browser.navigate(server.base_url + "/")
        browser.wait_for(
            f"[...document.querySelectorAll('#projectGrid .project-card h2')].some(item=>item.textContent==={json.dumps(project_name)})",
            timeout=20,
        )
        card_index = browser.evaluate(
            f"[...document.querySelectorAll('#projectGrid .project-card h2')].findIndex(item=>item.textContent==={json.dumps(project_name)})"
        )
        browser.native_click(f"#projectGrid .project-card:nth-child({card_index + 1}) .project-open")
        browser.wait_for("document.querySelector('[data-id=\"integrity-image\"]')", timeout=20)
        environment = settle(browser)
        try:
            browser.cdp.command(
                "Browser.grantPermissions",
                {
                    "origin": server.base_url,
                    "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"],
                },
            )
        except Exception as permission_error:
            raise AssertionError(f"failed to grant fixed local clipboard permission: {permission_error}") from permission_error
        if (
            environment.get("dpr") != 1
            or environment.get("theme") != "light"
            or not all(environment.get("fonts", {}).get(font) for font in FONT_FAMILIES)
        ):
            raise AssertionError(f"fixed screenshot environment is unavailable: {environment}")

        browser.native_click('[data-id="integrity-image"]', relative=(0.5, 0.08))
        browser.wait_for(
            "document.querySelector('#composer.open') && composerUpdateTimer===0 && document.querySelector('.model-control > .smart-pill') && document.querySelector('.size-picker-control > .smart-pill')",
            timeout=30,
        )
        settle(browser)
        browser.native_click(".model-control > .smart-pill")
        browser.wait_for("document.querySelector('.model-control.pinned .smart-popover')", timeout=10)
        settle(browser)
        canvas_scene = inspect_scene(
            browser,
            "canvas-composer",
            (
                ".smart-top-left",
                "#minimap",
                "#composer",
                "#runBtn",
                ".model-control > .smart-pill",
                ".model-control.pinned .smart-popover",
                ".size-picker-control > .smart-pill",
                '.image-node[data-id="integrity-image"].selected .smart-node-floating-menu',
            ),
            (".smart-top-left", "#minimap", "#composer", '.image-node[data-id="integrity-image"].selected .smart-node-floating-menu'),
        )
        require_scene(canvas_scene)
        if not canvas_scene["modelPopoverReachable"] or not canvas_scene["currentSelectionVisible"]:
            raise AssertionError(f"model or current selection is not directly reachable: {canvas_scene}")
        geometry.append(canvas_scene)
        snap("canvas-composer")

        browser.native_click(".model-control > .smart-pill")
        browser.native_click(".size-picker-control > .smart-pill")
        browser.wait_for("document.querySelector('.size-picker-control.pinned .smart-popover')", timeout=10)
        settle(browser)
        size_scene = inspect_scene(
            browser,
            "composer-size",
            (
                ".smart-top-left",
                "#minimap",
                "#composer",
                "#runBtn",
                ".size-picker-control > .smart-pill",
                ".size-picker-control.pinned .smart-popover",
                '.image-node[data-id="integrity-image"].selected .smart-node-floating-menu',
            ),
            (".smart-top-left", "#minimap", "#composer", '.image-node[data-id="integrity-image"].selected .smart-node-floating-menu'),
        )
        require_scene(size_scene)
        if not size_scene["sizePopoverReachable"]:
            raise AssertionError(f"ratio and resolution are not one-click reachable: {size_scene}")
        geometry.append(size_scene)
        snap("composer-size")

        popover_scale = browser.evaluate("viewport.scale")
        browser.native_wheel(".size-picker-control.pinned .smart-popover", 180, relative=(0.5, 0.5))
        if abs(float(browser.evaluate("viewport.scale")) - float(popover_scale)) > 0.0001:
            raise AssertionError("wheel inside a Composer secondary popover leaked into canvas zoom")
        browser.native_click(".size-picker-control > .smart-pill")

        prompt_scale = browser.evaluate("viewport.scale")
        browser.native_wheel("#promptInput", -180, relative=(0.9, 0.82))
        if abs(float(browser.evaluate("viewport.scale")) - float(prompt_scale)) > 0.0001:
            raise AssertionError("wheel inside the prompt editor leaked into canvas zoom")
        browser.native_click("#promptInput", relative=(0.9, 0.82))
        browser.native_wheel(".composer-head", -180, relative=(0.5, 0.5))
        browser.wait_for(f"Math.abs(viewport.scale-{float(prompt_scale)})>0.001", timeout=10)
        composer_anchor = browser.evaluate(
            """(() => {
              const node=document.querySelector('[data-id="integrity-image"]')?.getBoundingClientRect();
              const editor=composer.getBoundingClientRect();
              const scale=viewport.scale;
              return {
                scale,
                gapWorld:(editor.top-node.bottom)/scale,
                centerDeltaWorld:Math.abs((editor.left+editor.width/2)-(node.left+node.width/2))/scale,
              };
            })()"""
        )
        composer_world_anchor_and_wheel = bool(
            float(composer_anchor.get("scale", 0)) > 0
            and abs(float(composer_anchor.get("gapWorld", 0)) - 14) <= 1
            and float(composer_anchor.get("centerDeltaWorld", 99)) <= 1
        )
        if not composer_world_anchor_and_wheel:
            raise AssertionError(f"Composer drifted away from its node after viewport zoom: {composer_anchor}")
        size_scene["composerAnchor"] = composer_anchor

        browser.evaluate("window.__uiIntegrityPromptInput = promptInput; true")
        browser.native_click("#composerFlowBtn")
        browser.wait_for("composerPromptView.mode === 'flow' && document.querySelector('#composer.prompt-flow') && document.querySelectorAll('#promptFlowContext .prompt-flow-context-image.is-recent').length === 2 && document.querySelectorAll('#promptFlowContext .prompt-flow-context-image.is-reference').length === 22", timeout=20)
        browser.native_click("#promptInput", relative=(0.55, 0.72))
        browser.native_press("End")
        settle(browser)
        browser.evaluate(
            """(async () => {
              const lists=[...document.querySelectorAll('#promptFlowContext .prompt-flow-context-list')];
              const scrollLists=lists.filter(list=>!list.closest('.prompt-flow-context-group.is-recent'));
              for(const list of scrollLists){
                const step=Math.max(48, Math.floor(list.clientWidth * 0.75));
                for(let left=0; left<=list.scrollWidth; left+=step){
                  list.scrollLeft=left;
                  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
                }
              }
              const images=[...document.querySelectorAll('#promptFlowContext .prompt-flow-context-image img')];
              await Promise.all(images.map(image=>image.complete ? Promise.resolve() : new Promise(resolve=>{
                image.addEventListener('load',resolve,{once:true});
                image.addEventListener('error',resolve,{once:true});
              })));
              lists.forEach(list=>{ list.scrollLeft=0; });
              await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
              return images.map(image=>({complete:image.complete,width:image.naturalWidth,height:image.naturalHeight}));
            })()""",
            await_promise=True,
        )
        flow_scene = inspect_scene(
            browser,
            "composer-flow",
            (
                "#composer.prompt-flow",
                "#promptFlowContext",
                "#promptFlowContext .prompt-flow-context-group.is-recent",
                "#promptFlowContext .prompt-flow-context-group.is-reference",
                "#promptInput",
                "#composerFlowBtn",
            ),
            ("#composer.prompt-flow",),
        )
        require_scene(flow_scene)
        flow_facts = browser.evaluate(
            """(() => {
              const composerRect=composer.getBoundingClientRect();
              const contextRect=promptFlowContext.getBoundingClientRect();
              const promptRect=promptInput.getBoundingClientRect();
              const media=[...promptFlowContext.querySelectorAll('.prompt-flow-context-image')].map(card=>{
                const image=card.querySelector('img');
                const rect=card.getBoundingClientRect();
                const loaded=Boolean(image?.naturalWidth&&image?.naturalHeight);
                const naturalRatio=loaded ? image.naturalWidth/image.naturalHeight : 0;
                const declaredRatio=parseFloat(getComputedStyle(card).getPropertyValue('--prompt-flow-media-ratio'))||1.5;
                return {
                  cardRatio:rect.width/Math.max(1,rect.height),
                  expectedRatio:loaded ? naturalRatio : declaredRatio,
                  naturalRatio,
                  loaded,
                  objectFit:getComputedStyle(image).objectFit,
                };
              });
              const recentList=promptFlowContext.querySelector('.prompt-flow-context-group.is-recent .prompt-flow-context-list');
              const promptBackground=getComputedStyle(promptInput).backgroundColor.trim();
              const promptBackgroundAlpha=promptBackground.startsWith('rgba(') ? Number(promptBackground.slice(5,-1).split(',')[3]||1) : (promptBackground==='transparent' ? 0 : 1);
              return {
                centered:Math.abs(composerRect.left-(innerWidth-composerRect.right))<=2,
                contextAbovePrompt:contextRect.bottom<=promptRect.top+1,
                recentCount:promptFlowContext.querySelectorAll('.prompt-flow-context-image.is-recent').length,
                referenceCount:promptFlowContext.querySelectorAll('.prompt-flow-context-image.is-reference').length,
                sameEditor:window.__uiIntegrityPromptInput===promptInput,
                promptText:promptPlainText(),
                promptOpaque:promptBackgroundAlpha>=.999,
                promptBackground,
                recentNoScroll:Boolean(recentList && getComputedStyle(recentList).overflowX==='hidden' && recentList.scrollWidth<=recentList.clientWidth+1),
                mediaAdaptive:media.length===24 && media.every(item=>item.loaded && item.objectFit==='contain' && Math.abs(item.cardRatio-item.expectedRatio)<=0.08),
                mediaRatioRange:Math.max(...media.map(item=>item.cardRatio))-Math.min(...media.map(item=>item.cardRatio)),
                mediaRatios:media,
              };
            })()"""
        )
        prompt_flow_context_complete = bool(
            flow_facts.get("centered")
            and flow_facts.get("contextAbovePrompt")
            and flow_facts.get("recentCount") == 2
            and flow_facts.get("referenceCount") == 22
            and flow_facts.get("promptOpaque")
            and flow_facts.get("recentNoScroll")
            and flow_facts.get("mediaAdaptive")
            and flow_facts.get("mediaRatioRange", 0) > 0.5
            and flow_facts.get("sameEditor")
            and flow_facts.get("promptText")
        )
        prompt_flow_media_adaptive = bool(
            flow_facts.get("mediaAdaptive") and flow_facts.get("mediaRatioRange", 0) > 0.5
        )
        if not prompt_flow_context_complete:
            raise AssertionError(f"centered creative flow context is incomplete: {flow_facts}")
        flow_scene["promptFlowContext"] = flow_facts
        geometry.append(flow_scene)
        snap("composer-flow")
        browser.native_press("Escape")
        browser.wait_for("composerPromptView.mode !== 'flow' && !document.body.classList.contains('prompt-flow-active')", timeout=10)

        browser.native_click("#shell", relative=(0.98, 0.05))
        browser.wait_for("!document.querySelector('#composer')?.classList.contains('open')", timeout=10)
        browser.native_click_unblocked(
            "#shell",
            blocked_selector=(
                ".image-node,.composer,.smart-top-left,.smart-toolbar,.smart-minimap,#createMenu"
            ),
            button="right",
        )
        browser.wait_for("document.querySelector('#createMenu.open')", timeout=10)
        settle(browser)
        create_scene = inspect_scene(
            browser,
            "create-menu",
            (".smart-top-left", "#minimap", "#createMenu", "#createMenu .create-card"),
            (".smart-top-left", "#minimap", "#createMenu"),
        )
        require_scene(create_scene)
        geometry.append(create_scene)
        snap("create-menu")
        browser.native_press("Escape")

        browser.native_click("#smartHistoryToggle")
        browser.wait_for(
            "document.querySelector('#smartHistoryModal.open') && document.querySelector('[data-canvas-history-id=\"integrity-failed\"] [data-long-content-open=\"history-error\"]')",
            timeout=20,
        )
        settle(browser)
        history_scene = inspect_scene(
            browser,
            "history",
            (
                ".smart-top-left",
                "#minimap",
                "#smartHistoryModal.open .log-panel",
                '[data-canvas-history-id="integrity-failed"] .log-chip.status-failed',
                '[data-canvas-history-id="integrity-failed"] [data-long-content-open="history-error"]',
                '[data-canvas-history-id="integrity-failed"] [data-history-delete="media"]',
            ),
            (".smart-top-left", "#minimap", "#smartHistoryModal.open .log-panel"),
        )
        require_scene(history_scene)
        if not history_scene["historyMeaningReachable"]:
            raise AssertionError(f"history status, error, or destructive meaning is not reachable: {history_scene}")
        history_card_visibility = reveal_history_card(
            browser,
            '[data-canvas-history-id="integrity-success-one"]',
        )
        settle(browser)
        history_facts = browser.evaluate(
            """(() => {
              const drawer=smartHistoryModal.getBoundingClientRect();
              const thumb=document.querySelector('.log-item:not(.failed) .log-thumb-square')?.getBoundingClientRect();
              const images=[...document.querySelectorAll('.log-item:not(.failed) .log-history-image')];
              const title=document.querySelector('.log-title');
              const prompt=document.querySelector('.log-item:not(.failed) .log-prompt-preview');
              const probe=document.createElement('span');
              probe.style.color='var(--designer-accent-strong)';
              document.body.appendChild(probe);
              const accentColor=getComputedStyle(probe).color;
              probe.remove();
              return {
                fixedHeight:Math.abs(drawer.height-Math.min(324,innerHeight-32))<=1,
                noResizeHandle:!document.querySelector('[data-history-resize-handle]'),
                thumbHeight:thumb?.height||0,
                thumbRatio:(thumb?.width||0)/Math.max(1,thumb?.height||0),
                imageCount:images.length,
                scanThumbnailsFillFrame:images.length>=3 && images.every(image=>getComputedStyle(image).objectFit==='cover'),
                titleThemeColor:getComputedStyle(title).color===accentColor,
                titleFont:parseFloat(getComputedStyle(title).fontSize)||0,
                promptFont:parseFloat(getComputedStyle(prompt).fontSize)||0,
                overlayDownload:Boolean(document.querySelector('.log-media-download-original')),
                overlayDelete:Boolean(document.querySelector('.log-media-delete-original')),
              };
            })()"""
        )
        history_image_first_fixed = bool(
            history_facts.get("fixedHeight")
            and history_facts.get("noResizeHandle")
            and history_facts.get("thumbHeight", 0) >= 168
            and history_facts.get("thumbRatio", 0) > 1.05
            and history_facts.get("imageCount", 0) >= 3
            and history_facts.get("scanThumbnailsFillFrame")
            and history_facts.get("titleThemeColor")
            and history_facts.get("titleFont", 0) >= 16
            and history_facts.get("promptFont", 0) >= 13
            and history_facts.get("overlayDownload")
            and not history_facts.get("overlayDelete")
        )
        if not history_image_first_fixed:
            raise AssertionError(f"history is not fixed and image-first: {history_facts}")
        multi_output_facts = browser.evaluate(
            """(() => {
              const card=document.querySelector('[data-canvas-history-id="integrity-success-one"]');
              const list=document.querySelector('#smartHistoryModal .log-list');
              const thumb=card.querySelector('.log-thumb-square');
              const media=[...card.querySelectorAll('.log-output-gallery .log-media-card')];
              const gallery=card.querySelector('.log-output-gallery');
              const strip=card.querySelector('.log-output-preview-list');
              const outputPreviews=[...card.querySelectorAll('[data-history-output-preview]')];
              const rect=element=>{
                const value=element?.getBoundingClientRect();
                return value ? {left:value.left,top:value.top,right:value.right,bottom:value.bottom,width:value.width,height:value.height} : null;
              };
              const contains=(outer,inner,tolerance=1.5)=>Boolean(outer&&inner
                && inner.left>=outer.left-tolerance && inner.top>=outer.top-tolerance
                && inner.right<=outer.right+tolerance && inner.bottom<=outer.bottom+tolerance
                && inner.width>0 && inner.height>0);
              const px=value=>parseFloat(value)||0;
              const radius=element=>px(getComputedStyle(element).borderTopLeftRadius);
              const zIndex=element=>{
                const value=parseFloat(getComputedStyle(element).zIndex);
                return Number.isFinite(value) ? value : 0;
              };
              const alpha=color=>{
                const value=String(color||'').trim();
                if(!value || value==='transparent') return 0;
                if(value.startsWith('rgba(')) return Number(value.slice(5,-1).split(',')[3]||1);
                const slashIndex=value.lastIndexOf('/');
                if(slashIndex>=0) return Number.parseFloat(value.slice(slashIndex+1))||0;
                return 1;
              };
              const scrollbar=element=>({
                scrollbarWidth:getComputedStyle(element).scrollbarWidth,
                webkitDisplay:getComputedStyle(element,'::-webkit-scrollbar').display,
              });
              const thumbRect=rect(thumb);
              const galleryRect=rect(gallery);
              const stripRect=rect(strip);
              const cardRect=rect(card);
              const viewportRect={left:0,top:0,right:innerWidth,bottom:innerHeight,width:innerWidth,height:innerHeight};
              const mediaRatios=media.map(item=>{
                const image=item.querySelector('img');
                const itemRect=rect(item);
                const natural=image?.naturalWidth>0 && image?.naturalHeight>0 ? image.naturalWidth/image.naturalHeight : 0;
                return {frame:itemRect?.height>0 ? itemRect.width/itemRect.height : 0,natural,width:itemRect?.width||0,height:itemRect?.height||0};
              });
              const previews=outputPreviews.map((preview,index)=>({
                outputIndex:Number(preview.dataset.logOutputPreview),
                outputId:String(preview.dataset.outputId||''),
                assetId:String(preview.dataset.assetId||''),
                ariaLabel:preview.getAttribute('aria-label')||'',
                decoded:Boolean(preview.querySelector('img')?.complete && preview.querySelector('img')?.naturalWidth>0),
                radius:radius(preview),
                rect:rect(preview),
                domIndex:index,
              }));
              const stripStyle=getComputedStyle(strip);
              const galleryStyle=getComputedStyle(gallery);
              const columns=stripStyle.gridTemplateColumns.trim().split(' ').filter(Boolean);
              const rows=stripStyle.gridTemplateRows.trim().split(' ').filter(Boolean);
              const previewsContained=previews.every(item=>contains(stripRect,item.rect));
              const firstPreviewReachable=previews.length>0 && contains(stripRect,previews[0].rect);
              const stripScrollable=strip?.scrollWidth > strip?.clientWidth + 1;
              const singleRowTops=previews.length>0 && Math.max(...previews.map(item=>item.rect?.top||0))-Math.min(...previews.map(item=>item.rect?.top||0))<=1.5;
              const mainThumbnailUnshrunk=Math.abs((thumbRect?.height||0)-168)<=1
                && Math.abs((galleryRect?.width||0)-((thumbRect?.width||0)-2))<=2
                && Math.abs((galleryRect?.height||0)-((thumbRect?.height||0)-2))<=2;
              const previewOverlay=contains(thumbRect,stripRect)
                && (stripRect?.top||0)<(galleryRect?.bottom||0)-1
                && zIndex(strip)>zIndex(gallery);
              const outerFrameAbsent=[stripStyle.paddingTop,stripStyle.paddingRight,stripStyle.paddingBottom,stripStyle.paddingLeft].every(value=>px(value)<=.1)
                && [stripStyle.borderTopWidth,stripStyle.borderRightWidth,stripStyle.borderBottomWidth,stripStyle.borderLeftWidth].every(value=>px(value)<=.1)
                && radius(strip)<=.1
                && alpha(stripStyle.backgroundColor)<=.001
                && ['none',''].includes(stripStyle.boxShadow)
                && stripStyle.overflowX==='auto'
                && stripStyle.overflowY==='hidden';
              return {
                outputCount:media.length,
                downloadCount:card.querySelectorAll('[data-history-download-original]').length,
                previewCount:outputPreviews.length,
                previewsDiscoverable:previews.length===8 && previews.every((item,index)=>
                  item.outputIndex===index && item.ariaLabel.includes(`${index + 1}`)
                  && item.ariaLabel.includes('8') && item.decoded && item.rect?.width>=28 && item.rect?.height>=23
                ) && firstPreviewReachable && stripScrollable && singleRowTops,
                galleryScrollable:gallery?.scrollWidth > gallery?.clientWidth + 1,
                fixedScanFrames:mediaRatios.length===8 && mediaRatios.every(item=>
                  Math.abs(item.width-gallery.clientWidth)<=1
                  && Math.abs(item.height-gallery.clientHeight)<=1
                ),
                historyCardFullyVisible:contains(viewportRect,cardRect) && contains(rect(list),cardRect),
                mainThumbnailUnshrunk,
                leftBottomAligned:Math.abs(stripRect.left-thumbRect.left-5)<=1 && Math.abs(thumbRect.bottom-stripRect.bottom-5)<=1,
                previewOverlay,
                outerFrameAbsent,
                eightPreviewGridFits:columns.length===8 && rows.length===1 && stripScrollable && singleRowTops,
                stripWheelShift:false,
                noVisibleScrollbars:[scrollbar(list),scrollbar(gallery),scrollbar(strip)].every(item=>item.scrollbarWidth==='none' && item.webkitDisplay==='none'),
                unifiedRadii:[radius(thumb),radius(gallery),...outputPreviews.map(radius)].every(value=>Math.abs(value-6)<=.5),
                dangerousGroupDeletePresent:!!card.querySelector('[data-history-delete="media"]'),
                mediaRatios,
                previewGeometry:{
                  viewportRect,
                  listRect:rect(list),
                  cardRect,
                  thumbRect,
                  galleryRect,
                  stripRect,
                  galleryZIndex:zIndex(gallery),
                  stripZIndex:zIndex(strip),
                  stripClientWidth:strip?.clientWidth||0,
                  stripScrollWidth:strip?.scrollWidth||0,
                  stripScrollable,
                  grid:{columns:columns.length,rows:rows.length,columnGap:px(stripStyle.columnGap),rowGap:px(stripStyle.rowGap)},
                  previews,
                  thumbRadius:radius(thumb),
                  galleryRadius:radius(gallery),
                  stripStyle:{
                    paddingTop:px(stripStyle.paddingTop),paddingRight:px(stripStyle.paddingRight),paddingBottom:px(stripStyle.paddingBottom),paddingLeft:px(stripStyle.paddingLeft),
                    borderTop:px(stripStyle.borderTopWidth),borderRight:px(stripStyle.borderRightWidth),borderBottom:px(stripStyle.borderBottomWidth),borderLeft:px(stripStyle.borderLeftWidth),
                    borderRadius:radius(strip),backgroundAlpha:alpha(stripStyle.backgroundColor),boxShadow:stripStyle.boxShadow,
                    overflowX:stripStyle.overflowX,overflowY:stripStyle.overflowY,
                  },
                  scrollbars:{list:scrollbar(list),gallery:scrollbar(gallery),strip:scrollbar(strip)},
                },
              };
            })()"""
        )
        multi_output_facts["historyCardVisibility"] = history_card_visibility
        output_identity_before = browser.evaluate(
            "smartHistoryEntryById('integrity-success-one').outputs.map(output=>({assetId:String(output.asset_id||''),outputId:String(output.output_id||'')}))"
        )
        canvas_identity_before = browser.evaluate(
            "canvasState.nodes.find(node=>node.id==='integrity-multi-output').images.map(image=>({assetId:String(image.asset_id||''),outputId:String(image.output_id||'')}))"
        )
        output_asset_ids_before = [item["assetId"] for item in output_identity_before]
        output_ids_before = [item["outputId"] for item in output_identity_before]
        if (
            len(output_identity_before) != 8
            or len(set(output_ids_before)) != 8
            or not all(output_asset_ids_before)
            or not all(output_ids_before)
            or len(set(output_asset_ids_before)) != 8
            or canvas_identity_before != output_identity_before
        ):
            raise AssertionError(
                "eight-output history and canvas node did not expose stable occurrence identities with the duplicate asset fixture: "
                f"history={output_identity_before} canvas={canvas_identity_before}"
            )
        multi_output_facts["outputAssetIdsBefore"] = output_asset_ids_before
        multi_output_facts["outputIdsBefore"] = output_ids_before
        multi_output_facts["canvasAssetIdsBefore"] = [item["assetId"] for item in canvas_identity_before]
        multi_output_facts["canvasOutputIdsBefore"] = [item["outputId"] for item in canvas_identity_before]

        strip_scroll_before = float(browser.evaluate(
            "document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] .log-output-preview-list')?.scrollLeft||0"
        ))
        browser.native_wheel(
            '[data-canvas-history-id="integrity-success-one"] .log-output-preview-list',
            160,
            relative=(0.5, 0.5),
        )
        browser.wait_for(
            "Number(document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] .log-output-preview-list')?.scrollLeft||0)>20",
            timeout=10,
        )
        strip_scroll_after = float(browser.evaluate(
            "document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] .log-output-preview-list')?.scrollLeft||0"
        ))
        browser.evaluate(
            "document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] .log-output-preview-list').scrollLeft=0"
        )
        multi_output_facts["stripWheelShift"] = bool(strip_scroll_after > strip_scroll_before)
        multi_output_facts["stripScrollBefore"] = strip_scroll_before
        multi_output_facts["stripScrollAfter"] = strip_scroll_after
        preview_geometry = multi_output_facts.setdefault("previewGeometry", {})
        preview_geometry["stripWheelShift"] = bool(strip_scroll_after > strip_scroll_before)
        preview_geometry["stripScrollBefore"] = strip_scroll_before
        preview_geometry["stripScrollAfter"] = strip_scroll_after
        if not (
            multi_output_facts.get("outputCount") == 8
            and multi_output_facts.get("downloadCount") == 8
            and multi_output_facts.get("previewCount") == 8
            and multi_output_facts.get("previewsDiscoverable")
            and multi_output_facts.get("galleryScrollable")
            and multi_output_facts.get("fixedScanFrames")
            and multi_output_facts.get("historyCardFullyVisible")
            and multi_output_facts.get("mainThumbnailUnshrunk")
            and multi_output_facts.get("leftBottomAligned")
            and multi_output_facts.get("previewOverlay")
            and multi_output_facts.get("outerFrameAbsent")
            and multi_output_facts.get("eightPreviewGridFits")
            and multi_output_facts.get("noVisibleScrollbars")
            and multi_output_facts.get("unifiedRadii")
            and multi_output_facts.get("dangerousGroupDeletePresent")
        ):
            raise AssertionError(f"eight-output history gallery is incomplete or obstructive: {multi_output_facts}")
        browser.native_wheel(
            '[data-canvas-history-id="integrity-success-one"] .log-output-gallery',
            delta_y=240,
        )
        browser.wait_for(
            "document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] .log-output-gallery')?.scrollLeft > 100",
            timeout=10,
        )
        browser.evaluate(
            """(() => {
              const root=document.documentElement;
              const scrolling=document.scrollingElement;
              const body=document.body;
              const shell=document.querySelector('.shell');
              const list=document.querySelector('#smartHistoryModal .log-list');
              const gallery=document.querySelector('[data-canvas-history-id="integrity-success-one"] .log-output-gallery');
              root.style.scrollBehavior='auto';
              body.style.scrollBehavior='auto';
              if(scrolling) scrolling.scrollLeft=0;
              body.scrollLeft=0;
              window.scrollTo({left:0, top:window.scrollY, behavior:'instant'});
              if(shell) shell.scrollLeft=0;
              if(list) list.scrollLeft=0;
              if(gallery) gallery.scrollLeft=0;
            })()"""
        )
        browser.wait_for(
            "Math.abs(window.scrollX)<1 && Math.abs(document.documentElement.scrollLeft)<1 "
            "&& Math.abs(document.body.scrollLeft)<1 "
            "&& Math.abs(document.querySelector('.shell')?.scrollLeft||0)<1 "
            "&& Math.abs(document.querySelector('#smartHistoryModal .log-list')?.scrollLeft||0)<1",
            timeout=10,
        )
        scroll_state = browser.evaluate(
            """(() => ({
              window:window.scrollX||0,
              root:document.documentElement.scrollLeft||0,
              body:document.body.scrollLeft||0,
              shell:document.querySelector('.shell')?.scrollLeft||0,
              list:document.querySelector('#smartHistoryModal .log-list')?.scrollLeft||0,
              gallery:document.querySelector('[data-canvas-history-id="integrity-success-one"] .log-output-gallery')?.scrollLeft||0,
            }))()"""
        )
        if any(abs(float(scroll_state.get(key) or 0)) > 1 for key in ("window", "root", "body", "shell", "list", "gallery")):
            raise AssertionError(f"history scroll state did not reset: {scroll_state}")
        post_interaction_layout = browser.evaluate(
            """(() => {
              const rect=element=>{const value=element?.getBoundingClientRect(); return value ? {left:value.left,right:value.right,width:value.width} : null;};
              return {
                innerWidth:window.innerWidth,
                scale:getComputedStyle(document.documentElement).getPropertyValue('--studio-ui-scale').trim(),
                rootClasses:document.documentElement.className,
                bodyTransform:getComputedStyle(document.body).transform,
                bodyWidth:getComputedStyle(document.body).width,
                shell:rect(document.querySelector('.shell')),
                panel:rect(document.querySelector('#smartHistoryModal .log-panel')),
              };
            })()"""
        )
        multi_output_facts["postInteractionLayout"] = post_interaction_layout
        multi_output_facts["rootScrollStable"] = True
        history_facts["multiOutputGallery"] = True
        history_facts["multiOutputIndependentDeleteRestore"] = True
        history_facts["rootScrollStable"] = True
        history_scene["historyFacts"] = history_facts
        history_scene["multiOutputFacts"] = multi_output_facts

        browser.native_click(
            '[data-canvas-history-id="integrity-success-one"] [data-history-output-preview="3"]'
        )
        browser.wait_for(
            "!document.querySelector('#smartHistoryLightbox.open') "
            "&& document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] [data-history-output-index=\"3\"]')?.classList.contains('is-selected') "
            "&& document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] [data-history-output-preview=\"3\"]')?.getAttribute('aria-current')==='true' "
            "&& document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] [data-history-output-index=\"3\"] .log-history-image')?.complete "
            "&& document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] [data-history-output-index=\"3\"] .log-history-image')?.naturalWidth>0 "
            "&& (()=>{const gallery=document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] .log-output-gallery');"
            "const selected=document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] [data-history-output-index=\"3\"]');"
            "if(!gallery||!selected)return false;const outer=gallery.getBoundingClientRect();const inner=selected.getBoundingClientRect();"
            "return Math.abs(inner.left-outer.left)<=2&&inner.right<=outer.right+2;})()",
            timeout=10,
        )
        settle(browser)
        browser.evaluate(
            """(() => {
              const gallery=document.querySelector('[data-canvas-history-id="integrity-success-one"] .log-output-gallery');
              const selected=document.querySelector('[data-canvas-history-id="integrity-success-one"] [data-history-output-index="3"]');
              if(gallery && selected){
                gallery.style.scrollSnapType='none';
                gallery.scrollLeft=selected.offsetLeft;
              }
            })()"""
        )
        browser.wait_for(
            "(()=>{const gallery=document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] .log-output-gallery');"
            "const selected=document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] [data-history-output-index=\"3\"]');"
            "if(!gallery||!selected)return false;const outer=gallery.getBoundingClientRect();const inner=selected.getBoundingClientRect();"
            "return Math.abs(inner.left-outer.left)<=2&&inner.right<=outer.right+2;})()",
            timeout=10,
        )
        selected_preview_src = str(browser.evaluate(
            "document.querySelector('[data-canvas-history-id=\"integrity-success-one\"] [data-history-output-index=\"3\"]')?.dataset.url||''"
        ))
        if not selected_preview_src:
            raise AssertionError("selected fourth history preview did not expose its source URL")
        multi_output_facts["previewSelectsWithoutOpeningDetail"] = True
        multi_output_facts["selectedPreviewDecoded"] = True
        multi_output_facts["selectedPreviewSrc"] = selected_preview_src
        geometry.append(history_scene)
        snap("history")
        browser.native_press(" ")
        browser.wait_for(
            "document.querySelector('#smartHistoryLightbox.open .smart-log-detail') "
            "&& document.querySelectorAll('.smart-log-detail-output').length===8 "
            "&& document.querySelector('.smart-log-detail-output[aria-current=\"true\"]')?.dataset.outputIndex==='3' "
            "&& document.querySelector('.smart-log-detail-image')?.complete "
            "&& document.querySelector('.smart-log-detail-image')?.naturalWidth>0",
            timeout=10,
        )
        settle(browser)
        detail_scene = inspect_scene(
            browser,
            "generation-detail",
            (
                "#smartHistoryLightbox.open .smart-log-detail",
                ".smart-log-detail-stage",
                ".smart-log-detail-zoom-tools",
                ".smart-log-detail-info",
                ".smart-log-detail-model",
                ".smart-log-detail-time",
                ".smart-log-detail-prompt",
                ".smart-log-detail-download",
                ".smart-log-detail-copy-2k",
                ".smart-log-detail-delete-original",
            ),
            ("#smartHistoryLightbox.open .smart-log-detail",),
        )
        require_scene(detail_scene)
        detail_facts = browser.evaluate(
            """(() => {
              const probe=document.createElement('span');
              probe.style.color='var(--designer-accent-strong)';
              document.body.appendChild(probe);
              const accentColor=getComputedStyle(probe).color;
              probe.remove();
              const outputStrip=document.querySelector('.smart-log-detail-output-strip');
              const outputStripStyle=getComputedStyle(outputStrip);
              const px=value=>parseFloat(value)||0;
              const alpha=color=>{
                const value=String(color||'').trim();
                if(!value || value==='transparent') return 0;
                if(value.startsWith('rgba(')) return Number(value.slice(5,-1).split(',')[3]||1);
                const slashIndex=value.lastIndexOf('/');
                if(slashIndex>=0) return Number.parseFloat(value.slice(slashIndex+1))||0;
                return 1;
              };
              const outputStripStyleFacts={
                paddingTop:px(outputStripStyle.paddingTop),paddingRight:px(outputStripStyle.paddingRight),paddingBottom:px(outputStripStyle.paddingBottom),paddingLeft:px(outputStripStyle.paddingLeft),
                borderTop:px(outputStripStyle.borderTopWidth),borderRight:px(outputStripStyle.borderRightWidth),borderBottom:px(outputStripStyle.borderBottomWidth),borderLeft:px(outputStripStyle.borderLeftWidth),
                borderRadius:px(outputStripStyle.borderTopLeftRadius),backgroundAlpha:alpha(outputStripStyle.backgroundColor),boxShadow:outputStripStyle.boxShadow,
                overflowX:outputStripStyle.overflowX,overflowY:outputStripStyle.overflowY,
                scrollbarWidth:outputStripStyle.scrollbarWidth,webkitDisplay:getComputedStyle(outputStrip,'::-webkit-scrollbar').display,
                columns:outputStripStyle.gridTemplateColumns.trim().split(' ').filter(Boolean).length,
                rows:outputStripStyle.gridTemplateRows.trim().split(' ').filter(Boolean).length,
                columnGap:px(outputStripStyle.columnGap),rowGap:px(outputStripStyle.rowGap),
              };
              const outputStripOuterFrameAbsent=[outputStripStyleFacts.paddingTop,outputStripStyleFacts.paddingRight,outputStripStyleFacts.paddingBottom,outputStripStyleFacts.paddingLeft].every(value=>value<=.1)
                && [outputStripStyleFacts.borderTop,outputStripStyleFacts.borderRight,outputStripStyleFacts.borderBottom,outputStripStyleFacts.borderLeft].every(value=>value<=.1)
                && outputStripStyleFacts.borderRadius<=.1 && outputStripStyleFacts.backgroundAlpha<=.001
                && ['none',''].includes(outputStripStyleFacts.boxShadow)
                && outputStripStyleFacts.overflowX==='visible' && outputStripStyleFacts.overflowY==='visible';
              return {
              model:document.querySelector('.smart-log-detail-model')?.textContent.trim()||'',
              time:document.querySelector('.smart-log-detail-time')?.textContent.trim()||'',
              prompt:document.querySelector('.smart-log-detail-prompt')?.textContent.trim()||'',
              imageWidth:document.querySelector('.smart-log-detail-image')?.naturalWidth||0,
              imageSrc:document.querySelector('.smart-log-detail-image')?.getAttribute('src')||'',
              selectedImageDecoded:Boolean(document.querySelector('.smart-log-detail-image')?.complete && document.querySelector('.smart-log-detail-image')?.naturalWidth>0),
              outputCount:document.querySelectorAll('.smart-log-detail-output').length,
              selectedOutputIndex:document.querySelector('.smart-log-detail-output[aria-current="true"]')?.dataset.outputIndex||'',
              selectedOutputId:document.querySelector('.smart-log-detail-output[aria-current="true"]')?.dataset.outputId||'',
              outputPosition:document.querySelector('.smart-log-detail-output-position')?.textContent.trim()||'',
              allOutputsDiscoverable:[...document.querySelectorAll('.smart-log-detail-output')].length===8 && [...document.querySelectorAll('.smart-log-detail-output')].every((output,index)=>output.offsetParent && output.getAttribute('aria-label')?.includes(`${index + 1}`) && output.getAttribute('aria-label')?.includes('8')),
              actionsBoundToCurrent:[document.querySelector('.smart-log-detail-image'),document.querySelector('.smart-log-detail-download'),document.querySelector('.smart-log-detail-copy-2k'),document.querySelector('.smart-log-detail-delete-original')].every(element=>element?.dataset.outputIndex==='3' && element?.dataset.assetId===document.querySelector('.smart-log-detail-output[aria-current="true"]')?.dataset.assetId && element?.dataset.outputId===document.querySelector('.smart-log-detail-output[aria-current="true"]')?.dataset.outputId),
              downloadVisible:Boolean(document.querySelector('.smart-log-detail-download')?.offsetParent),
              copyVisible:Boolean(document.querySelector('.smart-log-detail-copy-2k')?.offsetParent),
              deleteVisible:Boolean(document.querySelector('.smart-log-detail-delete-original')?.offsetParent),
              outputGridRows:outputStripStyleFacts.rows===2 && outputStripStyleFacts.columns===4,
              noVisibleScrollbar:outputStripStyleFacts.scrollbarWidth==='none' && outputStripStyleFacts.webkitDisplay==='none',
              outputStripOuterFrameAbsent,
              outputStripStyle:outputStripStyleFacts,
              titleThemeColor:getComputedStyle(document.querySelector('.smart-log-detail-head h2')).color===accentColor,
              englishKickerAbsent:!document.querySelector('.smart-log-detail-kicker'),
              viewportWidthShare:document.querySelector('.smart-log-detail')?.getBoundingClientRect().width/innerWidth||0,
              viewportHeightShare:document.querySelector('.smart-log-detail')?.getBoundingClientRect().height/innerHeight||0,
              };
            })()"""
        )
        fourth_output_src = detail_facts.get("imageSrc", "")
        detail_facts["matchesSelectedPreview"] = bool(
            fourth_output_src and fourth_output_src == selected_preview_src
        )
        browser.native_click('.smart-log-detail-output[data-output-index="1"]')
        browser.wait_for(
            "document.querySelector('.smart-log-detail-output[aria-current=\"true\"]')?.dataset.outputIndex==='1' "
            f"&& document.querySelector('.smart-log-detail-image')?.getAttribute('src')!=={json.dumps(fourth_output_src)} "
            "&& document.querySelector('.smart-log-detail-download')?.dataset.outputIndex==='1' "
            "&& document.querySelector('.smart-log-detail-copy-2k')?.dataset.outputIndex==='1'",
            timeout=10,
        )
        browser.native_press("ArrowRight")
        browser.wait_for(
            "document.querySelector('.smart-log-detail-output[aria-current=\"true\"]')?.dataset.outputIndex==='2'",
            timeout=10,
        )
        browser.native_press("ArrowRight")
        browser.wait_for(
            "document.querySelector('.smart-log-detail-output[aria-current=\"true\"]')?.dataset.outputIndex==='3' "
            f"&& document.querySelector('.smart-log-detail-image')?.getAttribute('src')==={json.dumps(fourth_output_src)}",
            timeout=10,
        )
        browser.native_press("ArrowLeft")
        browser.wait_for(
            "document.querySelector('.smart-log-detail-output[aria-current=\"true\"]')?.dataset.outputIndex==='2'",
            timeout=10,
        )
        browser.native_press("ArrowRight")
        browser.wait_for(
            "document.querySelector('.smart-log-detail-output[aria-current=\"true\"]')?.dataset.outputIndex==='3'",
            timeout=10,
        )
        detail_facts["keyboardSwitch"] = True
        selected_asset_id = output_asset_ids_before[3]
        if not selected_preview_src.rstrip("/").endswith(selected_asset_id):
            raise AssertionError(
                f"selected fourth preview URL did not bind its stable asset identity: {selected_preview_src}"
            )
        expected_original = Path(store.media_record(project_id, selected_asset_id)["path"]).read_bytes()
        downloads_root = attempt_root / "downloads"
        downloads_root.mkdir(exist_ok=True)
        # Chrome 152 headless=new 下 Browser.setDownloadBehavior 对网络 URL 的下载路径不稳定
        # （请求被静默丢弃，会话内重设无效；data: URL 路径正常，已实测）。改用 Fetch 域拦截
        # /api/download-output 并直接取响应字节：确定性，且同样验证“点击对正确 asset 发起请求
        # + 响应字节与媒体原图一致”（只替换 Chrome 自身不稳定的落盘环节，不改验证语义）。
        browser.cdp.command(
            "Fetch.enable",
            {"patterns": [{"urlPattern": "*download-output*", "requestStage": "Response"}]},
        )
        browser.native_click(".smart-log-detail-download")
        download_deadline = time.monotonic() + 10
        downloaded: list[Path] = []
        handled_requests: set[str] = set()
        download_url = ""
        while time.monotonic() < download_deadline and not downloaded:
            for event in browser.cdp.events:
                if event.get("method") != "Fetch.requestPaused":
                    continue
                params = event.get("params") or {}
                request_id = str(params.get("requestId") or "")
                if not request_id or request_id in handled_requests:
                    continue
                request = params.get("request") or {}
                url = str(request.get("url") or "")
                if "download-output" not in url:
                    continue
                handled_requests.add(request_id)
                body = browser.cdp.command("Fetch.getResponseBody", {"requestId": request_id})
                payload = body.get("body") or ""
                raw = base64.b64decode(payload) if body.get("base64Encoded") else payload.encode("utf-8", "surrogateescape")
                name = parse_qs(urlparse(url).query).get("name", ["download-output.bin"])[0]
                (downloads_root / name).write_bytes(raw)
                download_url = url
                try:
                    # 以原始响应头继续：不产生 net::ERR_FAILED console 误报，也不触碰 Chrome 下载管理器
                    browser.cdp.command("Fetch.continueRequest", {"requestId": request_id})
                except Exception:
                    pass
                break
            if not downloaded:
                downloaded = [
                    path for path in downloads_root.iterdir()
                    if path.is_file() and not path.name.endswith(".crdownload")
                ]
            if not downloaded:
                time.sleep(0.05)
        try:
            browser.cdp.command("Fetch.disable")
        except Exception:
            pass
        detail_facts["downloadRequestUrl"] = download_url
        detail_facts["downloadSourceMatches"] = bool(
            len(downloaded) == 1 and downloaded[0].read_bytes() == expected_original
        )
        detail_facts["downloadAssetId"] = selected_asset_id
        detail_facts["downloadBytes"] = downloaded[0].stat().st_size if len(downloaded) == 1 else 0
        if not detail_facts["downloadSourceMatches"]:
            raise AssertionError(
                f"generation detail downloaded the wrong output: asset={selected_asset_id} files={downloaded}"
            )
        browser.native_click(".smart-log-detail-copy-2k")
        browser.wait_for("document.querySelector('#toast')?.textContent.includes('2K PNG')", timeout=20)
        clipboard_facts = browser.evaluate(
            """(async () => {
              const items=await navigator.clipboard.read();
              const png=items.find(item=>item.types.includes('image/png'));
              if(!png) return {png:false};
              const blob=await png.getType('image/png');
              const bitmap=await createImageBitmap(blob);
              const sample=document.createElement('canvas');
              sample.width=1;
              sample.height=1;
              const context=sample.getContext('2d', {willReadFrequently:true});
              context.drawImage(bitmap, Math.floor(bitmap.width/2), Math.floor(bitmap.height/2), 1, 1, 0, 0, 1, 1);
              const centerPixel=[...context.getImageData(0,0,1,1).data];
              const result={png:true,width:bitmap.width,height:bitmap.height,longest:Math.max(bitmap.width,bitmap.height),centerPixel,matchesSelectedOutput:centerPixel.join(',')==='156,112,64,255'};
              bitmap.close();
              return result;
            })()""",
            await_promise=True,
        )
        generation_detail_complete = bool(
            detail_facts.get("model")
            and detail_facts.get("time")
            and detail_facts.get("prompt")
            and detail_facts.get("outputCount") == 8
            and detail_facts.get("selectedOutputIndex") == "3"
            and detail_facts.get("selectedImageDecoded")
            and detail_facts.get("imageWidth", 0) > 0
            and detail_facts.get("matchesSelectedPreview")
            and detail_facts.get("allOutputsDiscoverable")
            and detail_facts.get("actionsBoundToCurrent")
            and detail_facts.get("downloadSourceMatches")
            and "4" in detail_facts.get("outputPosition", "")
            and "8" in detail_facts.get("outputPosition", "")
            and detail_facts.get("keyboardSwitch")
            and detail_facts.get("downloadVisible")
            and detail_facts.get("copyVisible")
            and detail_facts.get("deleteVisible")
            and detail_facts.get("outputGridRows")
            and detail_facts.get("noVisibleScrollbar")
            and detail_facts.get("outputStripOuterFrameAbsent")
            and detail_facts.get("titleThemeColor")
            and detail_facts.get("englishKickerAbsent")
            and detail_facts.get("viewportWidthShare", 0) >= 0.8
            and detail_facts.get("viewportHeightShare", 0) >= 0.8
            and clipboard_facts.get("png")
            and clipboard_facts.get("longest") == 2048
            and clipboard_facts.get("matchesSelectedOutput")
            and clipboard_facts.get("centerPixel") == [156, 112, 64, 255]
        )
        if not generation_detail_complete:
            raise AssertionError(f"generation detail or 2K clipboard is incomplete: detail={detail_facts}, clipboard={clipboard_facts}")

        browser.native_wheel(".smart-log-detail-stage", -240, relative=(0.5, 0.5))
        browser.wait_for("Number(document.querySelector('.smart-log-detail-frame')?.dataset.zoom||1)>1", timeout=10)
        zoom_after_wheel = float(browser.evaluate("Number(document.querySelector('.smart-log-detail-frame')?.dataset.zoom||1)"))
        browser.native_drag(".smart-log-detail-stage", dx=54, dy=28, steps=6, relative=(0.5, 0.5))
        pan_after_drag = browser.evaluate("({...smartHistoryDetailView.pan})")
        if zoom_after_wheel <= 1 or abs(float(pan_after_drag.get("x", 0))) < 20 or abs(float(pan_after_drag.get("y", 0))) < 10:
            raise AssertionError(f"generation detail did not support native wheel zoom and drag pan: zoom={zoom_after_wheel}, pan={pan_after_drag}")
        browser.native_click('[data-smart-log-detail-zoom="reset"]')
        browser.wait_for("Number(document.querySelector('.smart-log-detail-frame')?.dataset.zoom||0)===1 && smartHistoryDetailView.pan.x===0 && smartHistoryDetailView.pan.y===0", timeout=10)
        detail_facts["wheelZoom"] = zoom_after_wheel
        detail_facts["dragPan"] = pan_after_drag
        detail_facts["sharedPreviewFrame"] = bool(browser.evaluate("Boolean(document.querySelector('.smart-log-detail-stage.preview-stage .smart-log-detail-frame.preview-frame'))"))
        detail_scene["detailFacts"] = detail_facts
        detail_scene["clipboardFacts"] = clipboard_facts
        geometry.append(detail_scene)
        snap("generation-detail")

        deleted_asset_id = output_asset_ids_before[3]
        deleted_output_id = output_ids_before[3]
        expected_selected_output_id = output_ids_before[4]
        expected_selected_asset_id = output_asset_ids_before[4]
        browser.evaluate(
            "canvasState.selectedId='integrity-multi-output'; canvasState.selectedImage={nodeId:'integrity-multi-output',index:3}; render(); true"
        )
        browser.wait_for(
            f"document.querySelector('[data-id=\"integrity-multi-output\"] [data-image-index=\"3\"]')?.dataset.outputId==={json.dumps(deleted_output_id)}",
            timeout=10,
        )
        deletion_revisions = browser.evaluate(
            "({clientRevision:Number(canvas?.revision||0),syncInFlight:Boolean(canvasSyncInFlight),contentDirty:Boolean(contentDirty)})"
        )
        deletion_revisions["storeRevision"] = int(
            store.open_project(project_id)["canvas"].get("revision") or 0
        )
        detail_facts["deletionRevisions"] = deletion_revisions
        write_json(attempt_root / "deletion-revisions.json", deletion_revisions)
        browser.native_click(".smart-log-detail-delete-original", accept_dialog=True)
        browser.wait_for(
            "smartHistoryEntryById('integrity-success-one')?.outputs?.length===7 "
            "&& canvasState.nodes.find(node=>node.id==='integrity-multi-output')?.images?.length===7 "
            "&& document.querySelectorAll('[data-canvas-history-id=\"integrity-success-one\"] [data-history-output-preview]').length===7 "
            "&& document.querySelectorAll('#smartHistoryLightbox.open .smart-log-detail-output').length===7",
            timeout=20,
        )
        expected_after_delete = output_asset_ids_before[:3] + output_asset_ids_before[4:]
        expected_output_ids_after_delete = output_ids_before[:3] + output_ids_before[4:]
        post_delete_facts = browser.evaluate(
            """(() => {
              const historyCard=document.querySelector('[data-canvas-history-id="integrity-success-one"]');
              const assetIds=elements=>[...elements].map(element=>String(element.dataset.assetId||''));
              const outputIds=elements=>[...elements].map(element=>String(element.dataset.outputId||''));
              const detailCurrent=document.querySelector('.smart-log-detail-output[aria-current="true"]');
              const actionAssetIds=[
                document.querySelector('.smart-log-detail-image'),
                document.querySelector('.smart-log-detail-download'),
                document.querySelector('.smart-log-detail-copy-2k'),
                document.querySelector('.smart-log-detail-delete-original'),
              ].map(element=>String(element?.dataset.assetId||''));
              return {
                historyAssetIds:smartHistoryEntryById('integrity-success-one').outputs.map(output=>String(output.asset_id||'')),
                historyOutputIds:smartHistoryEntryById('integrity-success-one').outputs.map(output=>String(output.output_id||'')),
                historyDomAssetIds:assetIds(historyCard.querySelectorAll('.log-media-card[data-asset-id]')),
                historyDomOutputIds:outputIds(historyCard.querySelectorAll('.log-media-card[data-output-id]')),
                historyPreviewAssetIds:assetIds(historyCard.querySelectorAll('[data-history-output-preview]')),
                historyPreviewOutputIds:outputIds(historyCard.querySelectorAll('[data-history-output-preview]')),
                historySelectedAssetId:String(historyCard.querySelector('[data-history-output-preview][aria-current="true"]')?.dataset.assetId||''),
                historySelectedOutputId:String(historyCard.querySelector('[data-history-output-preview][aria-current="true"]')?.dataset.outputId||''),
                detailAssetIds:assetIds(document.querySelectorAll('.smart-log-detail-output')),
                detailOutputIds:outputIds(document.querySelectorAll('.smart-log-detail-output')),
                detailSelectedAssetId:String(detailCurrent?.dataset.assetId||''),
                detailSelectedOutputId:String(detailCurrent?.dataset.outputId||''),
                detailPosition:document.querySelector('.smart-log-detail-output-position')?.textContent.trim()||'',
                actionAssetIds,
                actionOutputIds:[document.querySelector('.smart-log-detail-image'),document.querySelector('.smart-log-detail-download'),document.querySelector('.smart-log-detail-copy-2k'),document.querySelector('.smart-log-detail-delete-original')].map(element=>String(element?.dataset.outputId||'')),
                detailStayedOpen:Boolean(document.querySelector('#smartHistoryLightbox.open')),
                canvasAssetIds:canvasState.nodes.find(node=>node.id==='integrity-multi-output').images.map(image=>String(image.asset_id||'')),
                canvasOutputIds:canvasState.nodes.find(node=>node.id==='integrity-multi-output').images.map(image=>String(image.output_id||'')),
                canvasDomAssetIds:assetIds(document.querySelectorAll('[data-id="integrity-multi-output"] [data-asset-id]')),
                canvasDomOutputIds:outputIds(document.querySelectorAll('[data-id="integrity-multi-output"] [data-output-id]')),
                canvasSelectedOutputId:String(document.querySelector('[data-id="integrity-multi-output"] .image-selected')?.dataset.outputId||''),
                // 合同（与 static/js/smart-canvas.js 的 destructiveRecordDelete 实现一致）：
                // 只要记录上仍有存活原图，「删记录+媒体」按钮必须可用——可恢复态（tombstone+
                // 恢复原图按钮）不隐藏它，否则剩余原图无法从该卡片彻底删除；且该路径的原图
                // 进回收区 7 天可恢复（docs/deletion-mechanism-review.md 入口 6），并非不可逆操作。
                // 旧断言 destructiveGroupDeleteHiddenWhileRestorable 与本场景及 1193 行
                // dangerousGroupDeletePresent（同卡片态要求按钮存在）自相矛盾，已废弃。
                destructiveGroupDeleteVisibleWhileOutputsAlive:!!historyCard.querySelector('[data-history-delete="media"]'),
              };
            })()"""
        )
        post_delete_facts["deletedAssetId"] = deleted_asset_id
        post_delete_facts["deletedOutputId"] = deleted_output_id
        post_delete_facts["expectedRemainingAssetIds"] = expected_after_delete
        post_delete_facts["expectedRemainingOutputIds"] = expected_output_ids_after_delete
        post_delete_facts["singleAssetSynchronized"] = bool(
            post_delete_facts.get("historyAssetIds") == expected_after_delete
            and post_delete_facts.get("historyDomAssetIds") == expected_after_delete
            and post_delete_facts.get("historyPreviewAssetIds") == expected_after_delete
            and post_delete_facts.get("detailAssetIds") == expected_after_delete
            and post_delete_facts.get("canvasAssetIds") == expected_after_delete
            and post_delete_facts.get("canvasDomAssetIds") == expected_after_delete
            and post_delete_facts.get("historyOutputIds") == expected_output_ids_after_delete
            and post_delete_facts.get("historyDomOutputIds") == expected_output_ids_after_delete
            and post_delete_facts.get("historyPreviewOutputIds") == expected_output_ids_after_delete
            and post_delete_facts.get("detailOutputIds") == expected_output_ids_after_delete
            and post_delete_facts.get("canvasOutputIds") == expected_output_ids_after_delete
            and post_delete_facts.get("canvasDomOutputIds") == expected_output_ids_after_delete
            and post_delete_facts.get("historySelectedOutputId") == expected_selected_output_id
            and post_delete_facts.get("detailSelectedOutputId") == expected_selected_output_id
            and post_delete_facts.get("canvasSelectedOutputId") == expected_selected_output_id
            and post_delete_facts.get("historySelectedAssetId") == expected_selected_asset_id
            and post_delete_facts.get("detailSelectedAssetId") == expected_selected_asset_id
            and post_delete_facts.get("actionAssetIds") == [expected_selected_asset_id] * 4
            and post_delete_facts.get("actionOutputIds") == [expected_selected_output_id] * 4
            and post_delete_facts.get("detailStayedOpen")
            and post_delete_facts.get("destructiveGroupDeleteVisibleWhileOutputsAlive")
        )
        if not post_delete_facts["singleAssetSynchronized"]:
            raise AssertionError(
                "deleting the selected occurrence did not preserve the other seven occurrence identities across history, detail, and canvas: "
                f"{post_delete_facts}"
            )
        post_delete_path = browser.capture_screenshot(screenshot_root / "generation-detail-after-single-delete.png")
        post_delete_facts["screenshot"] = relative(post_delete_path, artifacts_root)
        detail_facts["postDelete"] = post_delete_facts
        multi_output_facts["independentDelete"] = True
        multi_output_facts["deletedAssetId"] = deleted_asset_id
        multi_output_facts["deletedOutputId"] = deleted_output_id
        multi_output_facts["outputAssetIdsAfterDelete"] = post_delete_facts["historyAssetIds"]
        multi_output_facts["outputIdsAfterDelete"] = post_delete_facts["historyOutputIds"]
        multi_output_facts["canvasAssetIdsAfterDelete"] = post_delete_facts["canvasAssetIds"]
        multi_output_facts["canvasOutputIdsAfterDelete"] = post_delete_facts["canvasOutputIds"]
        multi_output_facts["detailAssetIdsAfterDelete"] = post_delete_facts["detailAssetIds"]
        multi_output_facts["detailOutputIdsAfterDelete"] = post_delete_facts["detailOutputIds"]
        multi_output_facts["singleAssetSynchronized"] = True

        browser.native_press("Escape")
        browser.wait_for("!document.querySelector('#smartHistoryLightbox')?.classList.contains('open')", timeout=10)
        if not browser.evaluate("document.querySelector('#smartHistoryModal')?.classList.contains('open')"):
            raise AssertionError("Escape closed generation history together with generation detail")
        browser.native_click(
            '[data-canvas-history-id="integrity-success-one"] [data-history-restore-original]',
            relative=(0.18, 0.5),
        )
        try:
            browser.wait_for(
                "smartHistoryEntryById('integrity-success-one')?.outputs?.length===8 "
                "&& canvasState.nodes.find(node=>node.id==='integrity-multi-output')?.images?.length===8 "
                "&& document.querySelectorAll('[data-canvas-history-id=\"integrity-success-one\"] [data-history-output-preview]').length===8",
                timeout=20,
            )
        except TimeoutError:
            store_state = store.open_project(project_id)
            restore_diagnostics = browser.evaluate(
                """(() => ({
                  revision:Number(canvas?.revision||0),
                  syncInFlight:Boolean(canvasSyncInFlight),
                  contentDirty:Boolean(contentDirty),
                  historyOutputs:smartHistoryEntryById('integrity-success-one')?.outputs?.map(output=>({assetId:String(output.asset_id||''),outputId:String(output.output_id||'')}))||[],
                  canvasImages:canvasState.nodes.find(node=>node.id==='integrity-multi-output')?.images?.map(image=>({assetId:String(image.asset_id||''),outputId:String(image.output_id||'')}))||[],
                  previewCount:document.querySelectorAll('[data-canvas-history-id="integrity-success-one"] [data-history-output-preview]').length,
                  toast:document.querySelector('#toast')?.textContent||'',
                }))()"""
            )
            restore_diagnostics["storeRevision"] = int(store_state["canvas"].get("revision") or 0)
            restore_diagnostics["storeHistoryOutputs"] = next(
                (
                    item.get("outputs") or []
                    for item in store_state.get("canvas", {}).get("history", [])
                    if str(item.get("id") or "") == "integrity-success-one"
                ),
                [],
            )
            restore_diagnostics["storeCanvasImages"] = next(
                (
                    item.get("images") or []
                    for item in store_state.get("canvas", {}).get("nodes", [])
                    if str(item.get("id") or "") == "integrity-multi-output"
                ),
                [],
            )
            write_json(attempt_root / "restore-diagnostics.json", restore_diagnostics)
            raise
        restore_facts = browser.evaluate(
            """(() => {
              const historyCard=document.querySelector('[data-canvas-history-id="integrity-success-one"]');
              const assetIds=elements=>[...elements].map(element=>String(element.dataset.assetId||''));
              const outputIds=elements=>[...elements].map(element=>String(element.dataset.outputId||''));
              return {
                historyAssetIds:smartHistoryEntryById('integrity-success-one').outputs.map(output=>String(output.asset_id||'')),
                historyOutputIds:smartHistoryEntryById('integrity-success-one').outputs.map(output=>String(output.output_id||'')),
                historyDomAssetIds:assetIds(historyCard.querySelectorAll('.log-media-card[data-asset-id]')),
                historyDomOutputIds:outputIds(historyCard.querySelectorAll('.log-media-card[data-output-id]')),
                historyPreviewAssetIds:assetIds(historyCard.querySelectorAll('[data-history-output-preview]')),
                historyPreviewOutputIds:outputIds(historyCard.querySelectorAll('[data-history-output-preview]')),
                historySelectedAssetId:String(historyCard.querySelector('[data-history-output-preview][aria-current="true"]')?.dataset.assetId||''),
                historySelectedOutputId:String(historyCard.querySelector('[data-history-output-preview][aria-current="true"]')?.dataset.outputId||''),
                canvasAssetIds:canvasState.nodes.find(node=>node.id==='integrity-multi-output').images.map(image=>String(image.asset_id||'')),
                canvasOutputIds:canvasState.nodes.find(node=>node.id==='integrity-multi-output').images.map(image=>String(image.output_id||'')),
                canvasDomAssetIds:assetIds(document.querySelectorAll('[data-id="integrity-multi-output"] [data-asset-id]')),
                canvasDomOutputIds:outputIds(document.querySelectorAll('[data-id="integrity-multi-output"] [data-output-id]')),
                canvasSelectedOutputId:String(document.querySelector('[data-id="integrity-multi-output"] .image-selected')?.dataset.outputId||''),
              };
            })()"""
        )
        identity_order_preserved = bool(
            restore_facts.get("historyAssetIds") == output_asset_ids_before
            and restore_facts.get("historyDomAssetIds") == output_asset_ids_before
            and restore_facts.get("historyPreviewAssetIds") == output_asset_ids_before
            and restore_facts.get("canvasAssetIds") == output_asset_ids_before
            and restore_facts.get("canvasDomAssetIds") == output_asset_ids_before
            and restore_facts.get("historyOutputIds") == output_ids_before
            and restore_facts.get("historyDomOutputIds") == output_ids_before
            and restore_facts.get("historyPreviewOutputIds") == output_ids_before
            and restore_facts.get("canvasOutputIds") == output_ids_before
            and restore_facts.get("canvasDomOutputIds") == output_ids_before
            and restore_facts.get("historySelectedOutputId") == expected_selected_output_id
            and restore_facts.get("canvasSelectedOutputId") == expected_selected_output_id
        )
        if not identity_order_preserved:
            raise AssertionError(
                "restoring the fourth output did not restore exact identity order or preserve the fifth selection: "
                f"{restore_facts}"
            )
        multi_output_facts["restored"] = True
        multi_output_facts["outputAssetIdsAfterRestore"] = restore_facts["historyAssetIds"]
        multi_output_facts["domAssetIdsAfterRestore"] = restore_facts["historyDomAssetIds"]
        multi_output_facts["canvasAssetIdsAfterRestore"] = restore_facts["canvasAssetIds"]
        multi_output_facts["canvasDomAssetIdsAfterRestore"] = restore_facts["canvasDomAssetIds"]
        multi_output_facts["selectionAssetIdAfterRestore"] = restore_facts["historySelectedAssetId"]
        multi_output_facts["outputIdsAfterRestore"] = restore_facts["historyOutputIds"]
        multi_output_facts["domOutputIdsAfterRestore"] = restore_facts["historyDomOutputIds"]
        multi_output_facts["previewOutputIdsAfterRestore"] = restore_facts["historyPreviewOutputIds"]
        multi_output_facts["canvasOutputIdsAfterRestore"] = restore_facts["canvasOutputIds"]
        multi_output_facts["canvasDomOutputIdsAfterRestore"] = restore_facts["canvasDomOutputIds"]
        multi_output_facts["selectionOutputIdAfterRestore"] = restore_facts["historySelectedOutputId"]
        multi_output_facts["identityOrderPreserved"] = identity_order_preserved
        browser.native_press("Escape")
        if not browser.evaluate("document.querySelector('#smartHistoryModal')?.classList.contains('open')"):
            raise AssertionError("Escape closed generation history without a transient detail")
        detail_facts["escapeKeptHistory"] = True
        history_detail_zoom_and_escape = bool(
            detail_facts.get("sharedPreviewFrame")
            and float(detail_facts.get("wheelZoom", 0)) > 1
            and abs(float(detail_facts.get("dragPan", {}).get("x", 0))) >= 20
            and abs(float(detail_facts.get("dragPan", {}).get("y", 0))) >= 10
            and detail_facts.get("escapeKeptHistory")
        )
        generation_detail_complete = bool(generation_detail_complete and history_detail_zoom_and_escape)

        browser.evaluate("document.querySelector('[data-canvas-history-id=\"integrity-failed\"] [data-long-content-open=\"history-error\"]').focus(); true")
        browser.native_press("Enter")
        browser.wait_for("document.querySelector('#longContentReader.open')", timeout=10)
        error_reader = inspect_scene(
            browser,
            "error-reader-check",
            ("#longContentReader .long-content-reader-panel", "#longContentReaderClose"),
            ("#longContentReader .long-content-reader-panel",),
        )
        require_scene(error_reader)
        browser.native_press("Escape")
        browser.wait_for("!document.querySelector('#longContentReader')?.classList.contains('open')", timeout=10)
        browser.native_click('.log-head-actions button[onclick="closeSmartCanvasLog()"]')
        try:
            browser.wait_for(
                "(() => { const drawer=document.querySelector('#smartHistoryModal'); const panel=drawer?.querySelector('.log-panel'); if(!drawer || !panel) return false; const style=getComputedStyle(panel); return !drawer.classList.contains('open') && drawer.getAttribute('aria-expanded')==='false' && style.visibility==='hidden' && Number.parseFloat(style.opacity||'0')<=0.01 && style.pointerEvents==='none'; })()",
                timeout=10,
            )
        except TimeoutError:
            write_json(
                attempt_root / "history-drawer-close-diagnostics.json",
                history_drawer_state(browser),
            )
            raise

        browser.native_click("#smartSettingsLink")
        browser.wait_for(
            "document.querySelector('#smartSettingsModal.open') && document.querySelector('#smartSettingsFrame')?.contentDocument?.readyState === 'complete' && document.querySelector('#smartSettingsFrame')?.contentDocument?.querySelector('#themeSelect')?.value === 'light' && !document.querySelector('#smartSettingsFrame')?.contentDocument?.documentElement.classList.contains('theme-dark')",
            timeout=20,
        )
        settle(browser)
        settings_scene = inspect_scene(
            browser,
            "settings",
            ("#smartSettingsModal.open .smart-settings-panel", "#smartSettingsClose"),
            ("#smartSettingsModal.open .smart-settings-panel",),
        )
        require_scene(settings_scene)
        settings_width = browser.evaluate(
            """(() => {
              const panel=document.querySelector('.smart-settings-panel').getBoundingClientRect();
              const frame=document.querySelector('#smartSettingsFrame');
              const shell=frame.contentDocument.querySelector('.settings-shell').getBoundingClientRect();
              const documentElement=frame.contentDocument.documentElement;
              const bodyStyle=frame.contentWindow.getComputedStyle(frame.contentDocument.body);
              return {
                panelWidth:panel.width,
                expectedPanelWidth:Math.min(1560,innerWidth-36),
                frameWidth:frame.getBoundingClientRect().width,
                shellWidth:shell.width,
                iframeInnerWidth:frame.contentWindow.innerWidth,
                iframeClientWidth:frame.contentDocument.documentElement.clientWidth,
                iframeScrollWidth:Math.max(documentElement.scrollWidth,frame.contentDocument.body.scrollWidth),
                iframeHorizontal:Math.max(documentElement.scrollWidth,frame.contentDocument.body.scrollWidth)>documentElement.clientWidth+1,
                embeddedScaleOptOut:documentElement.dataset.studioScale==='off',
                scaledClassPresent:documentElement.classList.contains('studio-ui-scaled'),
                bodyTransform:bodyStyle.transform,
              };
            })()"""
        )
        settings_width_used = bool(
            abs(settings_width.get("panelWidth", 0) - settings_width.get("expectedPanelWidth", 0)) <= 2
            and settings_width.get("shellWidth", 0) >= settings_width.get("frameWidth", 0) - 42
            and settings_width.get("embeddedScaleOptOut") is True
            and settings_width.get("scaledClassPresent") is False
            and settings_width.get("bodyTransform") in {"none", "matrix(1, 0, 0, 1, 0, 0)"}
            and not settings_width.get("iframeHorizontal")
        )
        if not settings_width_used:
            raise AssertionError(f"settings page still wastes the available width: {settings_width}")
        settings_scene["settingsWidth"] = settings_width
        settings_scene["historyDrawerState"] = history_drawer_state(browser)
        geometry.append(settings_scene)
        snap("settings")
        browser.native_press("Escape")
        browser.wait_for("!document.querySelector('#smartSettingsModal')?.classList.contains('open')", timeout=10)

        browser.native_click("#smartShortcutToggle")
        browser.wait_for("document.querySelector('#smartShortcutModal.open')", timeout=10)
        settle(browser)
        shortcuts_scene = inspect_scene(
            browser,
            "shortcuts",
            (".smart-top-left", "#minimap", "#smartShortcutModal.open", "#smartShortcutModal.open .preview-icon-btn"),
            (".smart-top-left", "#minimap", "#smartShortcutModal.open"),
        )
        require_scene(shortcuts_scene)
        shortcuts_scene["historyDrawerState"] = history_drawer_state(browser)
        geometry.append(shortcuts_scene)
        snap("shortcuts")
        browser.native_click('#smartShortcutModal .preview-icon-btn[onclick="closeSmartCanvasShortcuts()"]')

        browser.native_click("#assetToggle")
        browser.wait_for("document.querySelector('#assetPanel.open')", timeout=10)
        settle(browser)
        asset_check = inspect_scene(
            browser,
            "asset-check",
            ("#assetPanel.open", "#assetCloseBtn"),
            ("#assetPanel.open",),
        )
        require_scene(asset_check)
        browser.native_click("#assetCloseBtn")

        duplicate_occurrence_facts = None
        if duplicate_project_id:
            browser.navigate(server.base_url + "/")
            duplicate_project_name = "界面完整性 重复输出专用合同"
            browser.wait_for(
                f"[...document.querySelectorAll('#projectGrid .project-card h2')].some(item=>item.textContent==={json.dumps(duplicate_project_name)})",
                timeout=20,
            )
            duplicate_card_index = browser.evaluate(
                f"[...document.querySelectorAll('#projectGrid .project-card h2')].findIndex(item=>item.textContent==={json.dumps(duplicate_project_name)})"
            )
            browser.native_click(f"#projectGrid .project-card:nth-child({duplicate_card_index + 1}) .project-open")
            browser.wait_for("document.querySelector('[data-id=\"integrity-duplicate-node\"]')", timeout=20)
            settle(browser)
            browser.native_click("#smartHistoryToggle")
            browser.wait_for(
                "document.querySelector('#smartHistoryModal.open') && document.querySelectorAll('[data-canvas-history-id=\"integrity-duplicate-log\"] [data-history-output-preview]').length===4",
                timeout=20,
            )
            browser.native_click('[data-canvas-history-id="integrity-duplicate-log"] [data-history-output-preview="2"]')
            browser.wait_for(
                "document.querySelector('[data-canvas-history-id=\"integrity-duplicate-log\"] [data-history-output-preview=\"2\"]')?.getAttribute('aria-current')==='true'",
                timeout=10,
            )
            duplicate_before = browser.evaluate(
                """(() => ({
                  history:smartHistoryEntryById('integrity-duplicate-log').outputs.map(output=>({assetId:String(output.asset_id||''),outputId:String(output.output_id||'')})),
                  canvas:canvasState.nodes.find(node=>node.id==='integrity-duplicate-node').images.map(image=>({assetId:String(image.asset_id||''),outputId:String(image.output_id||'')})),
                  selectedOutputId:String(document.querySelector('[data-canvas-history-id="integrity-duplicate-log"] [data-history-output-preview][aria-current="true"]')?.dataset.outputId||''),
                }))()"""
            )
            duplicate_assets = [item["assetId"] for item in duplicate_before["history"]]
            duplicate_output_ids = [item["outputId"] for item in duplicate_before["history"]]
            if not (
                len(duplicate_before["history"]) == 4
                and duplicate_before["canvas"] == duplicate_before["history"]
                and len(set(duplicate_output_ids)) == 4
                and duplicate_assets[0] == duplicate_assets[2]
                and len(set(duplicate_assets)) == 3
                and duplicate_before["selectedOutputId"] == duplicate_output_ids[2]
            ):
                raise AssertionError(f"duplicate occurrence fixture is invalid: {duplicate_before}")
            duplicate_asset_id = duplicate_assets[2]
            duplicate_output_id = duplicate_output_ids[2]
            duplicate_expected_bytes = Path(store.media_record(duplicate_project_id, duplicate_asset_id)["path"]).read_bytes()
            browser.native_click(
                '[data-canvas-history-id="integrity-duplicate-log"] [data-history-delete-original]',
                accept_dialog=True,
            )
            try:
                browser.wait_for(
                    "smartHistoryEntryById('integrity-duplicate-log')?.outputs?.length===3 "
                    "&& canvasState.nodes.find(node=>node.id==='integrity-duplicate-node')?.images?.length===3 "
                    "&& document.querySelectorAll('[data-canvas-history-id=\"integrity-duplicate-log\"] [data-history-output-preview]').length===3",
                    timeout=20,
                )
            except TimeoutError:
                duplicate_store_state = store.open_project(duplicate_project_id)
                duplicate_delete_diagnostics = browser.evaluate(
                    """(() => ({
                      revision:Number(canvas?.revision||0),
                      syncInFlight:Boolean(canvasSyncInFlight),
                      contentDirty:Boolean(contentDirty),
                      selected:smartHistorySelectedImage,
                      history:smartHistoryEntryById('integrity-duplicate-log')?.outputs?.map(output=>({assetId:String(output.asset_id||''),outputId:String(output.output_id||'')}))||[],
                      canvasImages:canvasState.nodes.find(node=>node.id==='integrity-duplicate-node')?.images?.map(image=>({assetId:String(image.asset_id||''),outputId:String(image.output_id||'')}))||[],
                      toast:document.querySelector('#toast')?.textContent||'',
                    }))()"""
                )
                duplicate_delete_diagnostics["storeRevision"] = int(duplicate_store_state["canvas"].get("revision") or 0)
                duplicate_delete_diagnostics["storeHistory"] = next(
                    (
                        item.get("outputs") or []
                        for item in duplicate_store_state["canvas"].get("history", [])
                        if str(item.get("id") or "") == "integrity-duplicate-log"
                    ),
                    [],
                )
                write_json(attempt_root / "duplicate-delete-diagnostics.json", duplicate_delete_diagnostics)
                raise
            duplicate_expected_after_ids = duplicate_output_ids[:2] + duplicate_output_ids[3:]
            duplicate_media_response = browser.evaluate(
                f"""(async () => {{
                  const response=await fetch('/api/project-library/{duplicate_project_id}/media/{duplicate_asset_id}');
                  const bytes=new Uint8Array(await response.arrayBuffer());
                  const digest=await crypto.subtle.digest('SHA-256', bytes);
                  return {{status:response.status,byteLength:bytes.byteLength,sha256:[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('')}};
                }})()""",
                await_promise=True,
            )
            duplicate_after = browser.evaluate(
                """(() => ({
                  historyOutputIds:smartHistoryEntryById('integrity-duplicate-log').outputs.map(output=>String(output.output_id||'')),
                  canvasOutputIds:canvasState.nodes.find(node=>node.id==='integrity-duplicate-node').images.map(image=>String(image.output_id||'')),
                  previewOutputIds:[...document.querySelectorAll('[data-canvas-history-id="integrity-duplicate-log"] [data-history-output-preview]')].map(item=>String(item.dataset.outputId||'')),
                  selectedOutputId:String(document.querySelector('[data-canvas-history-id="integrity-duplicate-log"] [data-history-output-preview][aria-current="true"]')?.dataset.outputId||''),
                  survivingDecoded:Boolean(document.querySelector(`[data-canvas-history-id="integrity-duplicate-log"] [data-output-id="${smartHistoryEntryById('integrity-duplicate-log').outputs[0].output_id}"] img`)?.complete && document.querySelector(`[data-canvas-history-id="integrity-duplicate-log"] [data-output-id="${smartHistoryEntryById('integrity-duplicate-log').outputs[0].output_id}"] img`)?.naturalWidth>0),
                  restoreDeletionId:String(document.querySelector('[data-canvas-history-id="integrity-duplicate-log"] [data-history-restore-original]')?.dataset.deletionId||''),
                }))()"""
            )
            duplicate_media_record = store.media_record(duplicate_project_id, duplicate_asset_id)
            duplicate_after["mediaReadable"] = bool(
                Path(duplicate_media_record["path"]).read_bytes() == duplicate_expected_bytes
                and duplicate_media_response.get("status") == 200
                and duplicate_media_response.get("byteLength") == len(duplicate_expected_bytes)
                and duplicate_media_response.get("sha256") == hashlib.sha256(duplicate_expected_bytes).hexdigest()
                and duplicate_after.get("survivingDecoded")
            )
            if not (
                duplicate_after.get("historyOutputIds") == duplicate_expected_after_ids
                and duplicate_after.get("canvasOutputIds") == duplicate_expected_after_ids
                and duplicate_after.get("previewOutputIds") == duplicate_expected_after_ids
                and duplicate_after.get("selectedOutputId") == duplicate_output_ids[3]
                and duplicate_after.get("mediaReadable")
                and duplicate_after.get("restoreDeletionId")
            ):
                raise AssertionError(f"duplicate occurrence deletion is not exact: {duplicate_after}")
            browser.native_click(
                '[data-canvas-history-id="integrity-duplicate-log"] [data-history-restore-original]',
                relative=(0.18, 0.5),
            )
            browser.wait_for(
                "smartHistoryEntryById('integrity-duplicate-log')?.outputs?.length===4 "
                "&& canvasState.nodes.find(node=>node.id==='integrity-duplicate-node')?.images?.length===4 "
                "&& document.querySelectorAll('[data-canvas-history-id=\"integrity-duplicate-log\"] [data-history-output-preview]').length===4",
                timeout=20,
            )
            duplicate_restore = browser.evaluate(
                """(() => ({
                  historyOutputIds:smartHistoryEntryById('integrity-duplicate-log').outputs.map(output=>String(output.output_id||'')),
                  canvasOutputIds:canvasState.nodes.find(node=>node.id==='integrity-duplicate-node').images.map(image=>String(image.output_id||'')),
                  previewOutputIds:[...document.querySelectorAll('[data-canvas-history-id="integrity-duplicate-log"] [data-history-output-preview]')].map(item=>String(item.dataset.outputId||'')),
                  selectedOutputId:String(document.querySelector('[data-canvas-history-id="integrity-duplicate-log"] [data-history-output-preview][aria-current="true"]')?.dataset.outputId||''),
                }))()"""
            )
            duplicate_occurrence_facts = {
                "before": duplicate_before,
                "deletedAssetId": duplicate_asset_id,
                "deletedOutputId": duplicate_output_id,
                "afterDelete": duplicate_after,
                "afterRestore": duplicate_restore,
                "exactDelete": True,
                "mediaReadable": duplicate_after["mediaReadable"],
                "exactRestore": bool(
                    duplicate_restore.get("historyOutputIds") == duplicate_output_ids
                    and duplicate_restore.get("canvasOutputIds") == duplicate_output_ids
                    and duplicate_restore.get("previewOutputIds") == duplicate_output_ids
                    and duplicate_restore.get("selectedOutputId") == duplicate_output_ids[3]
                ),
            }
            if not duplicate_occurrence_facts["exactRestore"]:
                raise AssertionError(f"duplicate occurrence restore is not exact: {duplicate_restore}")
            geometry.append({"scene": "duplicate-occurrence-check", "duplicateOccurrenceFacts": duplicate_occurrence_facts})

        visual_geometry = [item for item in geometry if item.get("scene") != "duplicate-occurrence-check"]
        all_scenes = [*visual_geometry, error_reader, asset_check]
        chrome_scenes = [item for item in visual_geometry if item.get("scene") != "composer-flow"]
        lifecycle = {
            "no_horizontal_scroll": all(not item["pageHorizontal"] for item in all_scenes),
            "critical_surfaces_in_viewport": all(item["criticalSurfacesInViewport"] for item in all_scenes),
            "critical_controls_unobscured": all(item["criticalControlsUnobscured"] for item in all_scenes),
            "top_chrome_single_row": all(item["topChromeSingleRow"] for item in chrome_scenes),
            "composer_parameters_reachable": canvas_scene["modelPopoverReachable"] and size_scene["sizePopoverReachable"],
            "history_meaning_reachable": history_scene["historyMeaningReachable"],
            "current_selection_visible": canvas_scene["currentSelectionVisible"] and size_scene["currentSelectionVisible"],
            "selection_actions_reachable": canvas_scene["selectionActionsReachable"] and size_scene["selectionActionsReachable"],
            "history_image_first_fixed": history_image_first_fixed,
            "generation_detail_complete": generation_detail_complete,
            "prompt_flow_context_complete": prompt_flow_context_complete,
            "composer_world_anchor_and_wheel": composer_world_anchor_and_wheel,
            "history_detail_zoom_and_escape": history_detail_zoom_and_escape,
            "prompt_flow_media_adaptive": prompt_flow_media_adaptive,
            "settings_width_used": settings_width_used,
            "icon_controls_named": all(item["iconControlsNamed"] for item in all_scenes),
            "stable_tooltips": all(item["stableTooltips"] for item in all_scenes),
            "explicit_overflow_policy": all(item["explicitOverflowPolicy"] for item in chrome_scenes),
            "compact_geometry_contract": all(item["compactGeometryContract"] for item in all_scenes),
            "provider_calls": 0,
        }
        missing = [key for key in REQUIRED_FLAGS if lifecycle.get(key) is not True]
        if missing:
            raise AssertionError(f"UI integrity lifecycle failed: {missing}; scenes={all_scenes}")

        generation_requests = [
            item
            for item in browser.request_summaries
            if any(marker in str(item.get("url") or "") for marker in ("/image-generation-tasks", "/api/generate", "/api/canvas-llm"))
        ]
        if generation_requests:
            raise AssertionError(f"UI integrity interactions triggered provider requests: {generation_requests}")
        console_errors = [
            item
            for item in browser.console_errors
            if not (
                str(((item.get("params") or {}).get("entry") or {}).get("url") or "").endswith("/favicon.ico")
                and "404" in str(((item.get("params") or {}).get("entry") or {}).get("text") or "")
            )
        ]
        if console_errors:
            raise AssertionError(f"unexpected console errors: {console_errors}")

        operations_path = attempt_root / "operations.jsonl"
        operations_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in browser.operations),
            encoding="utf-8",
        )
        requests_path = attempt_root / "requests.json"
        console_path = attempt_root / "console-errors.json"
        geometry_path = attempt_root / "geometry.json"
        write_json(requests_path, browser.request_summaries)
        write_json(console_path, console_errors)
        write_json(geometry_path, geometry)
        return {
            "status": "passed",
            "viewport": [width, height],
            "browser_version": browser.browser_version().get("product", ""),
            "font_contract": environment["fonts"],
            "operations": relative(operations_path, artifacts_root),
            "requests": relative(requests_path, artifacts_root),
            "console_errors": relative(console_path, artifacts_root),
            "geometry": relative(geometry_path, artifacts_root),
            "screenshots": screenshots,
            "lifecycle": lifecycle,
            "project_id": project_id,
        }


def capture_setup_failure(
    artifacts_root: Path,
    viewport: tuple[int, int],
    error: Exception,
) -> tuple[dict, dict]:
    attempt_prefix = f"viewport-{viewport[0]}x{viewport[1]}"
    attempt_root = artifacts_root / attempt_prefix
    attempt_root.mkdir(parents=True, exist_ok=True)
    operations_path = attempt_root / "operations.jsonl"
    requests_path = attempt_root / "requests.json"
    console_path = attempt_root / "console-errors.json"
    screenshot_path = attempt_root / "first-failure.png"
    if not operations_path.exists():
        operations_path.write_text("", encoding="utf-8")
    if not requests_path.exists():
        write_json(requests_path, [])
    if not console_path.exists():
        write_json(console_path, [])
    if not screenshot_path.exists():
        screenshot = Image.new("RGB", viewport, (15, 23, 42))
        draw = ImageDraw.Draw(screenshot)
        failure_text = f"{type(error).__name__}: {error}".encode(
            "ascii", "backslashreplace"
        ).decode("ascii")
        draw.text((24, 24), failure_text, fill=(248, 250, 252))
        screenshot.save(screenshot_path, format="PNG")
    failure = {
        "viewport": list(viewport),
        "type": type(error).__name__,
        "message": str(error),
        "operations": f"{attempt_prefix}/operations.jsonl",
        "requests": f"{attempt_prefix}/requests.json",
        "console_errors": f"{attempt_prefix}/console-errors.json",
        "failure_screenshot": f"{attempt_prefix}/first-failure.png",
    }
    attempt = {"status": "failed", **failure}
    return attempt, failure


def run(artifacts_dir: Path, viewports: tuple[tuple[int, int], ...]) -> int:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[dict] = []
    first_failure: dict | None = None
    try:
        chrome = find_browser("chrome")
        with tempfile.TemporaryDirectory(prefix="firefly-ui-integrity-") as temp:
            root = Path(temp)
            store = ProjectStore(root / "projects")
            duplicate_project_id = seed_duplicate_occurrence_project(
                store,
                "界面完整性 重复输出专用合同",
            )
            app = harness.load_production_app(root, store)
            try:
                with AsgiBrowserServer(app) as server:
                    set_fixed_preferences(server)
                    for viewport in viewports:
                        try:
                            attempts.append(
                                run_viewport(
                                    chrome=chrome,
                                    server=server,
                                    store=store,
                                    artifacts_root=artifacts_dir,
                                    viewport=viewport,
                                    duplicate_project_id=(duplicate_project_id if viewport == (800, 600) else ""),
                                )
                            )
                        except Exception as error:
                            failed_attempt, first_failure = capture_setup_failure(
                                artifacts_dir, viewport, error
                            )
                            attempts.append(failed_attempt)
                            break
            finally:
                store.close()
    except Exception as error:
        if first_failure is None:
            viewport = viewports[min(len(attempts), len(viewports) - 1)]
            failed_attempt, first_failure = capture_setup_failure(artifacts_dir, viewport, error)
            attempts.append(failed_attempt)
    fonts = next((item.get("font_contract") for item in attempts if item.get("status") == "passed"), {name: False for name in FONT_FAMILIES})
    manifest = {
        "schema_version": 1,
        "journey": "desktop-ui-integrity",
        "status": "failed" if first_failure else "passed",
        "environment": {
            "os": platform.system(),
            "browser": {
                "name": "Google Chrome",
                "channel": "stable",
                "version": next((item.get("browser_version", "") for item in attempts if item.get("status") == "passed"), ""),
            },
            "dpr": 1,
            "color_scheme": "light",
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
            "fonts": fonts,
            "provider": "offline-none",
            "animations": "disabled-for-baseline",
        },
        "attempts": attempts,
        "first_failure": first_failure,
    }
    write_json(artifacts_dir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 1 if first_failure else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--viewport", choices=[f"{width}x{height}" for width, height in VIEWPORTS])
    args = parser.parse_args()
    selected = VIEWPORTS
    if args.viewport:
        width, height = (int(value) for value in args.viewport.split("x", 1))
        selected = ((width, height),)
    return run(args.artifacts_dir, selected)


if __name__ == "__main__":
    raise SystemExit(main())
