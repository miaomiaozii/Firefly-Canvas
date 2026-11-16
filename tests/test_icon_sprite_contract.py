"""#380：图标统一为 SVG sprite（85 个），不再加载 Lucide 运行时。

sprite 的唯一源是 static/smart-canvas.html 顶部的隐藏 <svg id="ffIconSprite">，
引用方式 <use href="#i-xxx">（HTML 里直接写，JS 里经 canvas-core.js 的 ico('xxx')）。
总表 85 个 key 写死在下面，改总表要同步改这里（总表本身在 .local/ 下，进不了 git）。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
PAGE = STATIC / "smart-canvas.html"

SHEET_KEYS = frozenset("""
generate
node-image
film
file-audio
text-cursor-input
messages-square
bot
loop
workflow
dice-5
send
brain
paperclip
git-branch
terminal
corner-up-right
inpaint
paintbrush
eraser
crop
expand
grid-3x3
scan-line
ref
columns-2
layers
square
circle
sliders-horizontal
list-ordered
split
plus
list-checks
group
ungroup
layout-grid
link
unlink
copy
grip-vertical
maximize-2
minimize-2
zoom-in
zoom-out
focus
undo-2
redo-2
library
upload
download
skip-back
skip-forward
folder-plus
pencil
star
save
file-plus-2
file
unused
trash-2
archive-restore
archive-x
arrow-left
x
chevron-down
chevron-up
chevron-left
chevron-right
play
search
eye
history
refresh-cw
loader-2
check
triangle-alert
image-off
info
settings
keyboard
plug-zap
key-round
cpu
monitor
boxes
""".split())


def sprite_ids(html: str) -> list[str]:
    block = re.search(r'<svg[^>]*id="ffIconSprite"[^>]*>(.*?)</svg>', html, re.S)
    assert block, "smart-canvas.html 里找不到 #ffIconSprite"
    return re.findall(r'<symbol id="i-([a-z0-9-]+)"', block.group(1))


def source_files():
    files = [PAGE]
    files += sorted((STATIC / "js").glob("*.js"))
    return files


def referenced_ids() -> dict[str, set[str]]:
    """代码里出现的图标 key -> 出现位置。覆盖三种写法：<use href="#i-x">、ico('x')、agentIco('x')，
    以及三元里的第二个字面量（ico(c ? 'a' : 'b')）和 icon:'x' / const icon = ... 这类变量赋值。"""
    found: dict[str, set[str]] = {}
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        name = path.name
        for key in re.findall(r'<use href="#i-([a-z0-9-]+)"', text):
            found.setdefault(key, set()).add(name)
        for call in re.findall(r"\b(?:agentIco|ico)\(([^()]*(?:\([^()]*\)[^()]*)*)\)", text):
            # 只看第一个参数（图标名，可能是三元）；第二个参数是 CSS 类，三元条件里的 'failed' 之类是比较值。
            first = re.split(r",\s*'[^']*'\s*$|,\s*\(", call)[0]
            if "?" in first:
                first = first.split("?", 1)[1]
            for key in re.findall(r"'([a-z][a-z0-9-]*)'", first):
                found.setdefault(key, set()).add(name)
        for line in text.splitlines():
            if re.search(r"\bicon\b\s*[:=]|roleChip\(", line):
                for key in re.findall(r"'([a-z][a-z0-9]*(?:-[a-z0-9]+)*)'", line):
                    if key in SHEET_KEYS:
                        found.setdefault(key, set()).add(name)
    return found


class IconSpriteContractTests(unittest.TestCase):
    def test_sprite_symbols_match_the_icon_sheet_exactly(self):
        ids = sprite_ids(PAGE.read_text(encoding="utf-8"))
        self.assertEqual(len(ids), len(set(ids)), "sprite 里有重复 symbol id")
        self.assertEqual(set(ids), set(SHEET_KEYS), "sprite 与总表不一致（不准增删图标）")
        self.assertEqual(85, len(ids))

    def test_every_sprite_icon_is_referenced(self):
        used = set(referenced_ids())
        unused = sorted(SHEET_KEYS - used)
        self.assertEqual([], unused, f"sprite 里有但代码没引用的图标：{unused}")

    def test_every_static_use_reference_exists_in_the_sprite(self):
        ids = set(sprite_ids(PAGE.read_text(encoding="utf-8")))
        missing = []
        for path in source_files():
            for key in re.findall(r'<use href="#i-([a-z0-9-]+)"', path.read_text(encoding="utf-8")):
                if key not in ids:
                    missing.append(f"{path.name}: #i-{key}")
        self.assertEqual([], missing)

    def test_every_dynamic_icon_name_exists_in_the_sprite(self):
        bad = {key: sorted(files) for key, files in referenced_ids().items() if key not in SHEET_KEYS}
        self.assertEqual({}, bad, "ico()/agentIco()/icon: 里用了 sprite 没有的图标名")

    def test_lucide_runtime_is_gone(self):
        offenders = []
        for path in list(STATIC.rglob("*.js")) + list(STATIC.rglob("*.html")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"lucide|data-lucide|createIcons", text, re.I):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual([], offenders)
        self.assertFalse((STATIC / "vendor" / "js" / "lucide.js").exists())


if __name__ == "__main__":
    unittest.main()
