"""#378/#417: prevent new literal colors outside the shared token file.

The fixture is a per-path, per-literal occurrence budget, not a palette allowlist.
Regenerate only from the agreed baseline with ``python -m tests.test_studio_color_guard --generate``;
verify it independently with ``python -m tests.test_studio_color_guard --check-baseline``.
This heuristic scans CSS declarations and recognizable JS styling contexts, not a
full CSS/JS parser: computed strings, dynamically assembled values, unusual JS
sinks and ambiguous identifiers can escape detection or require review. Color
functions count as expressions even when their arguments are only token references;
var() itself is not a literal, but its fallback can contain one.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/studio_color_literals_378_417.json'
BASELINE = '4719e971c0fff8fb9cf6ee97ded7b185ed5c94f5'
TOKEN_FILE = 'static/css/studio-foundation.css'

# CSS Color 4 named colors; currentColor and transparent are color values too.
NAMES = frozenset('''aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen transparent currentcolor'''.split())
CSS_ESCAPE = re.compile(r'\\([0-9a-fA-F]{1,6}(?:\r\n|[\t\n\r\f ])?|[^\n\r\f])')
COLOR = re.compile(r'(?<![\w-])#[\da-fA-F]{3,8}(?![\w-])|(?<![\w-])[a-zA-Z_][\w-]*(?![\w-])', re.I)
COLOR_FUNCTIONS = frozenset('rgb rgba hsl hsla hwb lab lch oklab oklch color color-mix'.split())
CSS_PARTS = re.compile(r'/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|url\s*\([^)]*\)', re.I | re.S)
DECL = re.compile(r'(?:^|[;{}])\s*(?:--[\w-]+|[a-zA-Z_][\w-]*)\s*:\s*([^;{}]+)(?=[;}]|$)', re.S)
JS_PARTS = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'|`(?:\\[\s\S]|[^`\\])*`')
COLOR_CONTEXT = re.compile(r'(?:\b[\w$]*(?:colou?r|fillstyle|strokestyle|background|border|shadow|outline|stroke|fill)[\w$]*["\']?\s*\]?\s*[:=]\s*|\.setProperty\s*\(\s*["\'][\w-]+["\']\s*,\s*|\.setAttribute\s*\(\s*["\'](?:fill|stroke|color|style)["\']\s*,\s*|\b(?:setColou?r|addColorStop)\s*\(\s*(?:[\d.]+\s*,\s*)?|\b[\w$]*(?:colou?r|palette)[\w$]*\s*[:=]\s*\[[^\]]*)$', re.I)
SELECTOR_CONTEXT = re.compile(r'(?:querySelector(?:All)?|matches|closest|getElementById|\$)\s*\(\s*$', re.I)
JS_ESCAPE = re.compile(r'\\(?:x([0-9a-fA-F]{2})|u([0-9a-fA-F]{4})|u\{([0-9a-fA-F]{1,6})\}|([\\"\'`]))')


def unescape_js(text: str) -> str:
    def replace(match: re.Match) -> str:
        number = next((part for part in match.groups()[:3] if part), None)
        return chr(int(number, 16)) if number and int(number, 16) <= 0x10ffff else (match.group(4) or '\ufffd')
    return JS_ESCAPE.sub(replace, text)


def unescape_css(text: str) -> str:
    def replace(match: re.Match) -> str:
        value = match.group(1)
        if re.match(r'[0-9a-fA-F]', value):
            number = int(value.strip(), 16)
            return chr(number) if 0 < number <= 0x10ffff else '\ufffd'
        return value
    return CSS_ESCAPE.sub(replace, text)


def colors(value: str) -> Counter:
    value = CSS_PARTS.sub(' ', unescape_css(value))
    result = Counter()

    def visit(start: int, end: int) -> None:
        pos = start
        while match := COLOR.search(value, pos, end):
            token = match.group().lower()
            pos = match.end()
            opening = re.match(r'\s*\(', value[pos:end])
            if opening and not token.startswith('#'):
                inner = pos + opening.end()
                depth, close = 1, inner
                while close < end and depth:
                    depth += (value[close] == '(') - (value[close] == ')')
                    close += 1
                if depth:
                    return
                if token == 'var':
                    # The custom property identifier is not a color; its fallback can be.
                    comma = value.find(',', inner, close - 1)
                    if comma != -1:
                        visit(comma + 1, close - 1)
                elif token == 'color-mix':
                    visit(inner, close - 1)
                elif token in COLOR_FUNCTIONS:
                    literal = value[match.start():close].lower()
                    literal = re.sub(r'\s+', ' ', literal)
                    literal = re.sub(r'\s*([(),/])\s*', r'\1', literal)
                    result[literal] += 1
                else:
                    visit(inner, close - 1)
                pos = close
            elif token.startswith('#'):
                if len(token) - 1 in (3, 4, 6, 8):
                    result[token] += 1
            elif token in NAMES:
                result[token] += 1

    visit(0, len(value))
    return result


def css_colors(source: str) -> Counter:
    # Blank strings/comments rather than deleting them: selector boundaries remain intact.
    source = re.sub(r'/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda m: ' ' if m.group().startswith('/*') else m.group(), source)
    result = Counter()
    for match in DECL.finditer(source):
        result.update(colors(match.group(1)))
    return result


def js_colors(source: str) -> Counter:
    result = Counter()
    for match in JS_PARTS.finditer(source):
        raw = match.group()
        if raw.startswith(('/',)):
            continue
        text = unescape_js(raw[1:-1])
        prefix = source[max(0, match.start() - 180):match.start()]
        if SELECTOR_CONTEXT.search(prefix):
            continue
        # Inline style attributes need their own boundary, not the surrounding HTML.
        inline_styles = re.findall(r'\bstyle\s*=\s*(["\'])(.*?)\1', text, re.I | re.S)
        if inline_styles:
            for _, value in inline_styles:
                result.update(css_colors(value))
        else:
            result.update(css_colors(text))
        for tag in re.findall(r'<[a-zA-Z][^>]*>', text):
            for attribute in re.finditer(r'\b(?:fill|stroke|color|bgcolor)\s*=\s*(["\'])(.*?)\1', tag, re.I | re.S):
                result.update(colors(attribute.group(2)))
        stripped = text.strip()
        color_values = colors(stripped)
        normalized = re.sub(r'\s*([(),/])\s*', r'\1', re.sub(r'\s+', ' ', unescape_css(stripped).lower()))
        full = len(color_values) == 1 and color_values.get(normalized) == 1
        context = COLOR_CONTEXT.search(prefix)
        css_sink = re.search(r'(?:\.style\b|\.setProperty\s*\(|\b(?:fillStyle|strokeStyle)\s*=)', prefix, re.I)
        if context and (full or css_sink):
            result.update(color_values)
    return result


def scan(path: str, source: str) -> Counter:
    if path == TOKEN_FILE:
        return Counter()
    if path.lower().endswith('.css'):
        return css_colors(source)
    if path.lower().endswith('.js'):
        return js_colors(source)
    return Counter()


def violations(files: dict[str, str], budgets: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    result = {}
    for path, source in sorted(files.items()):
        excess = scan(path, source) - Counter({literal: item['count'] for literal, item in budgets.get(path, {}).items()})
        if excess:
            result[path] = dict(sorted(excess.items()))
    return result


def sources() -> dict[str, str]:
    return {p.relative_to(ROOT).as_posix(): p.read_text(encoding='utf-8')
            for p in sorted((ROOT / 'static').rglob('*'))
            if p.is_file() and p.suffix.lower() in ('.css', '.js')}


def baseline_fixture() -> dict:
    names = subprocess.check_output(['git', 'ls-files', '--with-tree=' + BASELINE, '-z', '--', 'static'], cwd=ROOT).decode().split('\0')
    budgets = {}
    for path in sorted(names):
        if not path.endswith(('.css', '.js')) or path == TOKEN_FILE:
            continue
        blob = subprocess.run(['git', 'show', f'{BASELINE}:{path}'], cwd=ROOT, capture_output=True)
        if blob.returncode:
            continue
        found = scan(path, blob.stdout.decode('utf-8'))
        if found:
            budgets[path] = {literal: {'count': count, 'issues': ['#378', '#417']}
                             for literal, count in sorted(found.items())}
    return {'issues': ['#378', '#417'], 'baseline': BASELINE, 'occurrences': budgets}


def generate() -> None:
    FIXTURE.write_text(json.dumps(baseline_fixture(), indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


class StudioColorGuardTests(unittest.TestCase):
    def test_repository_has_no_new_literals(self):
        fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.assertEqual(fixture['baseline'], BASELINE)
        self.assertEqual(fixture['issues'], ['#378', '#417'])
        for path, literals in fixture['occurrences'].items():
            self.assertTrue(path.startswith('static/') and path != TOKEN_FILE)
            for literal, item in literals.items():
                self.assertTrue(literal)
                self.assertEqual(item['issues'], ['#378', '#417'])
                self.assertGreater(item['count'], 0)
        self.assertEqual(violations(sources(), fixture['occurrences']), {})

    def test_new_color_and_new_occurrence_are_rejected_per_path(self):
        budget = {'static/css/a.css': {'#abc': {'count': 1, 'issues': ['#378', '#417']}}}
        self.assertEqual(violations({'static/css/a.css': 'a{color:#abc}'}, budget), {})
        self.assertTrue(violations({'static/css/a.css': 'a{color:#abc;background:#abc}'}, budget))
        self.assertTrue(violations({'static/css/a.css': 'a{color:hotpink}'}, budget))
        self.assertTrue(violations({'static/css/b.css': 'a{color:#abc}'}, budget))
        self.assertEqual(violations({'static/css/a.css': 'a{color:var(--text)}'}, budget), {})

    def test_css_syntax_and_false_positives(self):
        source = '''/* color: red */ #abc, #red:hover { color: #abcd; background: rgb(1 2 3 / 50%); border: 1px solid hsl(4 5% 6%); box-shadow: 0 0 1px ReD; --x: #12345678; content: "blue"; background-image: url(#fff); }'''
        self.assertEqual(css_colors(source), Counter({'#abcd': 1, 'rgb(1 2 3/50%)': 1, 'hsl(4 5% 6%)': 1, 'red': 1, '#12345678': 1}))
        self.assertEqual(css_colors(r'a { color: r\65 d; background: #123456; outline-color: #abc; }'), Counter({'red': 1, '#123456': 1, '#abc': 1}))

    def test_javascript_colors_and_non_color_strings(self):
        source = '''// style.color = "red";
        document.querySelector("#abc"); const id = "red"; const hash = "#abc"; const url = "/img/#abc";
        el.style.color = "red"; ctx.fillStyle = '#abc';
        el.style.setProperty('--accent', 'hsl(1 2% 3%)');
        const html = `<div style="background: rgba(1, 2, 3, .5); color: blue;"><svg fill="#1234"></svg>`;
        el.style.background = 'linear-gradient(yellow, hsla(1, 2%, 3%, .4))';
        '''
        self.assertEqual(js_colors(source), Counter({'red': 1, '#abc': 1, 'hsl(1 2% 3%)': 1, 'rgba(1,2,3,.5)': 1, 'blue': 1, '#1234': 1, 'yellow': 1, 'hsla(1,2%,3%,.4)': 1}))

    def test_escaped_literals_and_javascript_contexts(self):
        source = r'''el.style["color"] = "\x23abc";
        el.style.setProperty('background', 'red');
        const colors = ['blue', 'green'];
        gradient.addColorStop(0, 'yellow');
        const other = 'blue';'''
        self.assertEqual(js_colors(source), Counter({'#abc': 1, 'red': 1, 'blue': 1, 'green': 1, 'yellow': 1}))
        self.assertEqual(css_colors(r'a{color:r\65 d; background:#abc}'), Counter({'red': 1, '#abc': 1}))

    def test_modern_functions_in_css_and_js_are_rejected(self):
        literals = [
            'hwb(30 20% 10% / .5)', 'lab(50% 30 -20)', 'lch(50% 40 30)',
            'oklab(.5 .1 .2)', 'oklch(.5 .2 30)', 'color(display-p3 1 0 0 / .4)',
            'color-mix(in oklab, var(--red), color-mix(in srgb, blue, #abc))',
            'rgba(1, 2, 3, .25)', '#abcd', '#12345678', 'rebeccapurple',
        ]
        for literal in literals:
            with self.subTest(literal=literal):
                for path, source in [
                    ('static/css/a.css', f'a{{color:{literal}}}'),
                    ('static/js/a.js', f'el.style.color = "{literal}";'),
                ]:
                    self.assertTrue(violations({path: source}, {}), (path, literal))
                    found = scan(path, source)
                    budget = {path: {key: {'count': count, 'issues': ['#378', '#417']}
                                     for key, count in found.items()}}
                    self.assertFalse(violations({path: source}, budget))
                    self.assertTrue(violations({path: source + '\n' + source}, budget))

    def test_variables_selectors_comments_and_keywords_are_not_colors(self):
        self.assertEqual(css_colors('''/* a{color:red} */
            #abc, .red, #blue:hover { color: var(--red); background: var(--blue, var(--black));
            content: "/* not a comment */ red"; border: 0 solid; }'''), Counter())
        self.assertEqual(css_colors('a { color:var(--red, blue) }'), Counter({'blue': 1}))
        self.assertEqual(css_colors('a { color:inherit; background:none; border:initial }'), Counter())
        self.assertEqual(css_colors('a { color:color-mix(in srgb, var(--red), var(--blue)) }'), Counter())
        self.assertEqual(css_colors('a { color:color-mix(in srgb, var(--red), color-mix(in srgb, blue, #abc)) }'),
                         Counter({'blue': 1, '#abc': 1}))
        self.assertEqual(js_colors('el.style.color = "color-mix(in srgb, var(--red), var(--blue))";'), Counter())
        self.assertEqual(js_colors('''// el.style.color = "red";
            /* ctx.fillStyle = "#abc"; */
            document.querySelector('#abc'); document.querySelectorAll('.red');
            document.getElementById('blue'); el.matches('#fff');
            const id = 'red'; const className = 'blue';
            el.style.color = 'var(--red)';'''), Counter())

    def test_token_exemption_is_exact(self):
        self.assertFalse(scan(TOKEN_FILE, ':root{--x:red}'))
        self.assertTrue(scan('static/css/other.css', ':root{--x:red}'))
        self.assertTrue(scan('static/js/studio-foundation.js', 'el.style.color = "red"'))
        self.assertTrue(scan('static/css/other.CSS', 'a{color:red}'))

    def test_chat_dock_studio_tokens_match_source(self):
        extension_copy = 'extensions/chat-dock/studio-foundation.css'
        self.assertTrue(
            (ROOT / extension_copy).read_text(encoding='utf-8') == (ROOT / TOKEN_FILE).read_text(encoding='utf-8'),
            f'#515: {extension_copy} differs from {TOKEN_FILE}. Sync the extension copy from {TOKEN_FILE}.',
        )

    def test_fast_gate_wiring(self):
        from tools.quality_gate import MODE_STEPS, STEP_DEFINITIONS
        self.assertIn('studio-color-guard', MODE_STEPS['fast'])
        self.assertIn('tests.test_studio_color_guard', STEP_DEFINITIONS['studio-color-guard'](sys.executable))


if __name__ == '__main__':
    if sys.argv[1:] == ['--generate']:
        generate()
    elif sys.argv[1:] == ['--check-baseline']:
        if json.loads(FIXTURE.read_text(encoding='utf-8')) != baseline_fixture():
            raise SystemExit('fixture differs from baseline scan')
        print('fixture matches baseline scan')
    else:
        unittest.main()
