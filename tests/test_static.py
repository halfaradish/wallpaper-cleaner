"""前端静态资源的守卫测试

前端拆成多个 ES 模块后，最容易犯的错不是逻辑错，而是「引用了不存在的文件」——
浏览器只会在控制台留一条 404，页面看着还在，功能却整块不见了。这里把它变成测试：

- 引用的资源必须存在，而且必须在服务白名单里（漏进白名单同样是 404）
- CSS 里引用的每个变量都必须有定义
- index.html 不能有内联 <script> / style=（严格 CSP 的前提）
- 白名单之外的东西一律取不到，目录穿越取不到

运行：python -m unittest discover -s tests -v
"""

import os
import re
import sys
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wallpaper_cleaner import web

STATIC_DIR = web.STATIC_DIR
JS_DIR = os.path.join(STATIC_DIR, 'js')
CSS_DIR = os.path.join(STATIC_DIR, 'css')


def read(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def walk_files(root, suffix):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(suffix):
                yield os.path.join(dirpath, name)


def rel_posix(path):
    return os.path.relpath(path, STATIC_DIR).replace(os.sep, '/')


# ---------- 颜色与对比度：给"两套主题都达标"这条断言用 ----------

def _block_at(css, open_index):
    """返回 open_index 处 '{' 所配对的括号内部内容"""
    depth = 0
    for i in range(open_index, len(css)):
        if css[i] == '{':
            depth += 1
        elif css[i] == '}':
            depth -= 1
            if depth == 0:
                return css[open_index + 1:i]
    return ''


def _parse_decls(block):
    """从一段 CSS 里取自定义属性。带注释的行、多行值都不关心——颜色声明都是单行。"""
    decls = {}
    for line in block.splitlines():
        code = line.split('/*')[0].strip()
        match = re.match(r'(--[\w-]+)\s*:\s*(.+?);?$', code)
        if match:
            decls[match.group(1)] = match.group(2).strip().rstrip(';').strip()
    return decls


def _scan_rules(css, media=None, out=None):
    """把 CSS 拆成 (媒体条件, 选择器, 声明) 三元组

    只处理一层 @media 嵌套——tokens.css 就这个复杂度，写个完整的 CSS 解析器不值当。
    """
    if out is None:
        out = []
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    i = 0
    while i < len(css):
        brace = css.find('{', i)
        if brace < 0:
            break
        head = css[i:brace].strip()
        depth = 0
        end = -1
        for j in range(brace, len(css)):
            if css[j] == '{':
                depth += 1
            elif css[j] == '}':
                depth -= 1
                if depth == 0:
                    end = j
                    break
        body = css[brace + 1:end]
        if head.startswith('@media'):
            _scan_rules(body, head, out)
        elif head.startswith('@'):
            pass   # @keyframes / @supports 之类，这里不关心
        else:
            out.append((media, head, body))
        i = end + 1
    return out


def parse_token_themes(css):
    """取出 tokens.css 里深色与浅色两套声明

    浅色是覆盖在深色之上的一层（只写要改的），所以调用方需要把两者合并。
    浅色在两处出现：手动指定（`[data-theme="light"]`）与跟随系统（媒体查询里）——
    这是 CSS 逼出来的重复，另有专门的测试盯着它们必须逐字相同。
    """
    base = {}
    manual_light = None
    auto_light = None
    for media, selector, body in _scan_rules(css):
        if ':root' not in selector:
            continue
        if media and 'prefers-color-scheme: light' in media:
            auto_light = _parse_decls(body)
        elif 'data-theme="light"' in selector:
            manual_light = _parse_decls(body)
        elif selector.strip() == ':root':
            base = _parse_decls(body)

    light = dict(base)
    light.update(manual_light or {})
    return {
        'dark': base,
        'light': light,
        'light_manual': manual_light,
        'light_auto': auto_light,
    }


def resolve_token(values, name, depth=0):
    """跟着 var() 一路解析到底；解析不动返回 None"""
    if depth > 10:
        return None
    raw = values.get(name)
    if raw is None:
        return None
    match = re.fullmatch(r'var\(\s*(--[\w-]+)\s*(?:,\s*(.+))?\)', raw)
    if not match:
        return raw
    inner = resolve_token(values, match.group(1), depth + 1)
    return inner if inner is not None else match.group(2)


def parse_rgba(value):
    """返回 (r, g, b, a)；认不出来（渐变、关键字等）返回 None"""
    if not value:
        return None
    text = value.strip()
    hex_match = re.fullmatch(r'#([0-9a-fA-F]{3,8})', text)
    if hex_match:
        digits = hex_match.group(1)
        if len(digits) == 3:
            return tuple(int(c * 2, 16) for c in digits) + (1.0,)
        if len(digits) == 6:
            return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)
        if len(digits) == 8:
            return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4)) + (
                int(digits[6:8], 16) / 255,)
        return None
    fn_match = re.fullmatch(
        r'rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)\s*(?:[/,]\s*([\d.]+%?))?\s*\)',
        text,
    )
    if not fn_match:
        return None
    alpha = 1.0
    if fn_match.group(4):
        raw_alpha = fn_match.group(4)
        alpha = float(raw_alpha.rstrip('%')) / (100 if raw_alpha.endswith('%') else 1)
    return (int(float(fn_match.group(1))), int(float(fn_match.group(2))),
            int(float(fn_match.group(3))), alpha)


def _over(fg, bg):
    """把带透明度的 fg 叠在 bg 上，返回不透明结果"""
    a = fg[3]
    return tuple(round(fg[i] * a + bg[i] * (1 - a)) for i in range(3)) + (1.0,)


def _linear(channel):
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(rgb):
    r, g, b = (_linear(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(rgb_a, rgb_b):
    la, lb = _luminance(rgb_a), _luminance(rgb_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def effective_pair(values, fg_name, bg_name):
    """算出一对"实际会看到的"不透明颜色

    半透明的底（rgb(... / 0.1) 那种浅色底）要先叠在卡片表面上才谈得上对比度，
    否则算出来的数字没有意义。
    """
    fg = parse_rgba(resolve_token(values, fg_name))
    bg = parse_rgba(resolve_token(values, bg_name))
    if fg is None or bg is None:
        return None
    if bg[3] < 1:
        base = parse_rgba(resolve_token(values, '--surface'))
        if base is None:
            return None
        bg = _over(bg, base)
    if fg[3] < 1:
        fg = _over(fg, bg)
    return fg, bg


class TestStaticReferences(unittest.TestCase):
    """磁盘层面的引用完整性：不需要起服务，跑得快"""

    def test_index_references_exist_and_are_servable(self):
        html = read(os.path.join(STATIC_DIR, 'index.html'))
        refs = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
        self.assertTrue(refs, 'index.html 里应当至少引用一个静态资源')

        for ref in refs:
            name = ref[len('/static/'):]
            with self.subTest(ref=ref):
                self.assertTrue(
                    os.path.isfile(os.path.join(STATIC_DIR, name.replace('/', os.sep))),
                    f'{ref} 在磁盘上不存在',
                )
                self.assertIn(name, web.static_files(), f'{ref} 不在服务白名单里，会 404')

    def test_module_imports_exist_and_are_servable(self):
        pattern = re.compile(r"""import\s[^'"]*?from\s+['"](\.[^'"]+)['"]""")
        seen = 0
        for path in walk_files(JS_DIR, '.js'):
            source = read(path)
            for target in pattern.findall(source):
                seen += 1
                resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
                with self.subTest(src=rel_posix(path), target=target):
                    self.assertTrue(os.path.isfile(resolved), f'{target} 不存在')
                    self.assertIn(
                        rel_posix(resolved), web.static_files(),
                        f'{target} 不在服务白名单里，会 404',
                    )
        self.assertGreater(seen, 10, '模块之间的 import 应当被扫到，检查正则是否失效')

    def test_every_static_file_is_reachable_or_intentionally_hidden(self):
        """白名单来自目录扫描，所以磁盘上的每个文件都应当可取——index.html 除外"""
        for path in walk_files(STATIC_DIR, ''):
            name = rel_posix(path)
            with self.subTest(name=name):
                if name == 'index.html':
                    self.assertIn(name, web.static_files())
                else:
                    self.assertIn(name, web.static_files())

    def test_whitelist_picks_up_new_files_without_a_restart(self):
        """白名单要跟着磁盘走，不能只在进程启动时扫一次

        只在导入时扫一次的代价是：开发时新加一个前端模块必须重启面板才不 404，
        而这一点在浏览器里只表现为"整块功能没了"，很难往这上面想。
        """
        added = os.path.join(JS_DIR, 'tmp-whitelist-probe.js')
        self.assertNotIn('js/tmp-whitelist-probe.js', web.static_files())
        try:
            with open(added, 'w', encoding='utf-8') as f:
                f.write('// 临时文件，测完就删\n')
            # 目录 mtime 的分辨率有限，显式推一下，别让测试靠运气
            stamp = os.stat(JS_DIR).st_mtime_ns
            os.utime(JS_DIR, ns=(stamp + 10 ** 9, stamp + 10 ** 9))
            self.assertIn('js/tmp-whitelist-probe.js', web.static_files())
        finally:
            os.remove(added)
            stamp = os.stat(JS_DIR).st_mtime_ns
            os.utime(JS_DIR, ns=(stamp + 10 ** 9, stamp + 10 ** 9))
        self.assertNotIn('js/tmp-whitelist-probe.js', web.static_files())

    def test_imported_names_are_actually_exported(self):
        """import 的名字必须在目标模块里真的导出

        这是没有打包器时的经典翻车点：文件存在、路径也对，但导出名写错或改名后忘了同步，
        浏览器只在控制台报一句 "does not provide an export named"，页面直接白屏。
        """
        export_patterns = (
            # 标识符里可以有 $（dom.js 就导出了 $），而 Python 的 \w 不认它
            re.compile(r'^export\s+(?:async\s+)?function\s+([\w$]+)', re.M),
            re.compile(r'^export\s+(?:const|let|var|class)\s+([\w$]+)', re.M),
        )
        named_export = re.compile(r'^export\s*\{([^}]*)\}', re.M)
        import_pattern = re.compile(
            r"""import\s*\{([^}]*)\}\s*from\s*['"](\.[^'"]+)['"]""", re.S)

        exports = {}
        for path in walk_files(JS_DIR, '.js'):
            source = read(path)
            names = set()
            for pattern in export_patterns:
                names.update(pattern.findall(source))
            for block in named_export.findall(source):
                for piece in block.split(','):
                    piece = piece.strip()
                    if not piece:
                        continue
                    # 「export { a as b }」对外暴露的名字是 b
                    names.add(piece.split(' as ')[-1].strip())
            exports[rel_posix(path)] = names

        checked = 0
        for path in walk_files(JS_DIR, '.js'):
            for block, target in import_pattern.findall(read(path)):
                resolved = rel_posix(os.path.normpath(os.path.join(os.path.dirname(path), target)))
                available = exports.get(resolved, set())
                for piece in block.split(','):
                    piece = piece.strip()
                    if not piece:
                        continue
                    # 「import { a as b }」要检查的是 a
                    wanted = piece.split(' as ')[0].strip()
                    checked += 1
                    with self.subTest(src=rel_posix(path), target=target, name=wanted):
                        self.assertIn(
                            wanted, available,
                            f'{rel_posix(path)} 从 {target} 导入了 {wanted}，但那边没有导出它',
                        )
        self.assertGreater(checked, 20, 'import 的名字应当被扫到，检查正则是否失效')

    def test_css_variables_are_defined(self):
        defined = set()
        referenced = set()
        for path in walk_files(CSS_DIR, '.css'):
            source = read(path)
            defined.update(re.findall(r'(--[\w-]+)\s*:', source))
            # var(--x) 与 var(--x, fallback) 都要认
            referenced.update(re.findall(r'var\(\s*(--[\w-]+)', source))

        self.assertTrue(defined, '应当至少定义一组设计令牌')
        missing = sorted(referenced - defined)
        self.assertEqual(missing, [], f'这些变量被引用但没有定义：{missing}')

    def test_no_hardcoded_colors_outside_tokens(self):
        """tokens.css 之外不允许出现字面颜色值

        这是浅色主题能成立的前提：只要组件里还写着一个 #rrggbb 或 rgb()，
        换主题时它就不会跟着变，而这类漏网只会在浅色下才显形。
        顺带也让"改配色"变成只改一个文件的事。
        """
        # #rgb / #rrggbb / #rrggbbaa
        hex_color = re.compile(r'#[0-9a-fA-F]{3,8}\b')
        # rgb() / rgba() / hsl() / hsla()，含空格分隔的新写法
        func_color = re.compile(r'\b(?:rgba?|hsla?)\(')

        offenders = []
        for path in walk_files(CSS_DIR, '.css'):
            name = rel_posix(path)
            if name == 'css/tokens.css':
                continue
            for lineno, line in enumerate(read(path).splitlines(), 1):
                code = line.split('/*')[0]
                if not code.strip():
                    continue
                for pattern in (hex_color, func_color):
                    for hit in pattern.findall(code):
                        offenders.append(f'{name}:{lineno} {hit.strip()} → {code.strip()}')

        self.assertEqual(
            offenders, [],
            '组件样式里出现了字面颜色，请改成语义令牌：\n  ' + '\n  '.join(offenders),
        )

    def test_tokens_define_both_theme_hooks(self):
        """语义层必须覆盖深色、浅色、以及手动切换用的 data-theme 钩子"""
        tokens = read(os.path.join(CSS_DIR, 'tokens.css'))
        self.assertIn('@media (prefers-color-scheme: light)', tokens)
        self.assertIn(':root[data-theme="light"]', tokens)
        self.assertIn(':root[data-theme="dark"]', tokens)
        self.assertIn('color-scheme:', tokens)

    def test_light_tokens_are_identical_in_both_places(self):
        """浅色令牌写了两遍，必须逐字相同

        CSS 逼出来的重复：媒体查询没法写进选择器列表，而"手动选浅色"与"跟随系统且
        系统是浅色"两种情况都得命中。既然躲不掉，就用哨兵注释圈起来 + 这条测试盯着，
        让"改了一处忘了另一处"变成一次测试失败，而不是用户那边的半套配色。
        """
        tokens = read(os.path.join(CSS_DIR, 'tokens.css'))
        themes = parse_token_themes(tokens)
        self.assertTrue(themes['light_manual'], '没解析出手动指定的浅色块')
        self.assertTrue(themes['light_auto'], '没解析出跟随系统的浅色块')
        self.assertEqual(
            themes['light_manual'], themes['light_auto'],
            '两处浅色令牌不一致了——改了 [data-theme="light"] 就要同步改媒体查询里那份',
        )

    def test_theme_attribute_only_accepts_known_values(self):
        """前端写进 data-theme 的值必须在 CSS 里有对应分支

        写一个 CSS 里没有的值不会报错，只会让主题静默地停在上一个状态。
        """
        tokens = read(os.path.join(CSS_DIR, 'tokens.css'))
        # 前端那份清单是权威：它同时是接口校验用的
        source = read(os.path.join(JS_DIR, 'ui', 'theme.js'))
        frontend = set(re.findall(r"'(\w+)'", source))
        for value in ('auto', 'light', 'dark'):
            self.assertIn(value, frontend, f'theme.js 里应当有 {value}')
        # auto 不需要自己的块：它靠"没有 data-theme 分支命中"落到媒体查询上
        self.assertIn(':root[data-theme="light"]', tokens)
        self.assertIn(':root[data-theme="dark"]', tokens)

    def test_index_has_theme_placeholder_and_button(self):
        """主题靠服务端注入第一帧，占位符被删掉就会退化成"每次先闪一下浅色" """
        html = read(os.path.join(STATIC_DIR, 'index.html'))
        self.assertIn('__PANEL_THEME_VALUE__', html)
        self.assertIn('data-theme="__PANEL_THEME_VALUE__"', html)
        self.assertIn('id="btn-theme"', html)

    def test_contrast_meets_wcag_aa_in_both_themes(self):
        """逐对校验前景/背景的对比度

        这是浅色主题唯一的自动化防线：浅色配色最容易犯的错就是"看着还行，
        实际只有 2:1"。深色那套的配对是实测过的，浅色这套是推出来的，
        所以必须让机器算，而不是靠眼睛。
        """
        tokens = read(os.path.join(CSS_DIR, 'tokens.css'))
        themes = parse_token_themes(tokens)
        self.assertTrue(themes['dark'], '深色语义层没解析出来，检查 tokens.css 的结构')
        self.assertTrue(themes['light'], '浅色语义层没解析出来')
        # 只校验这两套完整的；light_manual / light_auto 是部分覆盖，单独有测试比对
        complete = {'dark': themes['dark'], 'light': themes['light']}

        # (前景, 背景, 最低要求)。4.5 是正文标准
        pairs = [
            ('--text', '--bg', 4.5),
            ('--text', '--surface', 4.5),
            ('--text', '--surface-2', 4.5),
            ('--text', '--surface-3', 4.5),
            ('--text-dim', '--surface', 4.5),
            ('--text-dim', '--surface-2', 4.5),
            ('--text-dim', '--surface-3', 4.5),
            ('--muted', '--bg', 4.5),
            ('--muted', '--surface', 4.5),
            ('--muted', '--surface-2', 4.5),
            ('--muted', '--surface-3', 4.5),
            ('--accent', '--surface', 4.5),
            ('--accent', '--surface-2', 4.5),
            ('--accent', '--bg', 4.5),
            ('--accent-on', '--accent-bg', 4.5),
            ('--danger-on', '--danger-bg', 4.5),
            ('--danger-text', '--danger-surface', 4.5),
            ('--good-text', '--good-surface', 4.5),
            ('--warn', '--surface', 4.5),
            ('--warn', '--surface-2', 4.5),
            ('--warn-text', '--warn-soft', 4.5),
            ('--good-text', '--good-soft', 4.5),
            ('--danger-text', '--danger-soft', 4.5),
        ]

        failures = []
        checked = 0
        for theme_name, values in complete.items():
            for fg_name, bg_name, minimum in pairs:
                pair = effective_pair(values, fg_name, bg_name)
                self.assertIsNotNone(
                    pair,
                    f'{theme_name}: 算不出 {fg_name} on {bg_name} 的实际颜色，'
                    '多半是某个令牌写成了渐变或未定义',
                )
                fg, bg = pair
                ratio = contrast_ratio(fg, bg)
                checked += 1
                if ratio < minimum:
                    failures.append(
                        f'{theme_name}: {fg_name} on {bg_name} = {ratio:.2f}:1（要求 {minimum}）'
                    )

        self.assertEqual(checked, len(pairs) * 2, '两套主题的每一对都要被校验到')
        self.assertEqual(failures, [], '对比度不足：\n  ' + '\n  '.join(failures))

    def test_index_has_no_inline_script_or_style(self):
        """严格 CSP 的前提：页面里不能有内联脚本，也不能有 style= 属性"""
        html = read(os.path.join(STATIC_DIR, 'index.html'))
        body = html.split('</head>', 1)[-1]

        self.assertNotIn('<script>', body, 'index.html 里不能有内联 <script>')
        self.assertNotIn('javascript:', html.lower(), 'index.html 里不能有 javascript: 链接')
        self.assertEqual(
            re.findall(r'\sstyle="', html), [],
            'index.html 里不能有内联 style 属性（style-src 没放行 unsafe-inline）',
        )
        # 脚本只能从外部文件加载，而且要走模块方式
        self.assertIn('<script type="module" src="/static/js/main.js"></script>', html)

    def test_token_and_version_placeholders_still_present(self):
        """web.py 靠字符串替换注入这两项，占位符被删掉就会静默失效"""
        html = read(os.path.join(STATIC_DIR, 'index.html'))
        self.assertIn('__PANEL_TOKEN_VALUE__', html)
        self.assertIn('__PANEL_VERSION_VALUE__', html)
        self.assertNotIn('window.__PANEL_TOKEN__', html, 'token 不该再走内联脚本')


class StaticEndpointTestCase(unittest.TestCase):
    """HTTP 层面：白名单、目录穿越、CSP、缓存校验"""

    def setUp(self):
        self.state = web.PanelState()
        # 不用 web.create_server：它会初始化全局日志，把日志文件钉在别处
        self.server = web.PanelServer(('127.0.0.1', 0), web.PanelHandler, self.state)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self, path, headers=None):
        request = urllib.request.Request(
            f'http://127.0.0.1:{self.port}{path}', headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as res:
                return res.status, res.headers, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()


class TestStaticEndpoint(StaticEndpointTestCase):
    def test_serves_a_nested_module(self):
        status, headers, body = self.get('/static/js/core/api.js')
        self.assertEqual(status, 200)
        self.assertIn('javascript', headers.get('Content-Type', ''))
        self.assertIn(b'export async function api', body)

    def test_serves_a_nested_stylesheet(self):
        status, headers, _body = self.get('/static/css/tokens.css')
        self.assertEqual(status, 200)
        self.assertIn('text/css', headers.get('Content-Type', ''))

    def test_index_is_not_served_as_a_static_file(self):
        """index.html 要走 / 那条路（要替换占位符），从 /static/ 取不到"""
        status, _headers, _body = self.get('/static/index.html')
        self.assertEqual(status, 404)

    def test_unknown_file_is_rejected(self):
        status, _headers, _body = self.get('/static/nope.js')
        self.assertEqual(status, 404)

    def test_traversal_is_rejected(self):
        for path in (
            '/static/../web.py',
            '/static/js/../../web.py',
            '/static/..%2fweb.py',
            '/static/js/%2e%2e/%2e%2e/web.py',
            '/static//etc/passwd',
        ):
            with self.subTest(path=path):
                status, _headers, _body = self.get(path)
                self.assertEqual(status, 404)

    def test_static_assets_carry_etag_and_revalidate(self):
        _status, headers, _body = self.get('/static/css/base.css')
        etag = headers.get('ETag')
        self.assertTrue(etag, '静态资源应当带 ETag')

        status, _headers, body = self.get('/static/css/base.css', {'If-None-Match': etag})
        self.assertEqual(status, 304)
        self.assertEqual(body, b'')

    def test_panel_page_sends_a_strict_csp(self):
        status, headers, _body = self.get('/')
        self.assertEqual(status, 200)
        csp = headers.get('Content-Security-Policy', '')
        self.assertIn("default-src 'none'", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertIn("img-src 'self' data:", csp)
        self.assertNotIn('unsafe-inline', csp)
        self.assertNotIn('unsafe-eval', csp)

    def test_panel_page_injects_token_into_meta(self):
        status, _headers, body = self.get('/')
        self.assertEqual(status, 200)
        html = body.decode('utf-8')
        self.assertNotIn('__PANEL_TOKEN_VALUE__', html, '占位符没被替换')
        self.assertIn(f'content="{self.state.token}"', html)
        self.assertIn('<meta name="panel-token"', html)

    def test_panel_page_is_never_cached(self):
        """页面里内嵌着本次运行的 token，缓存下来会把旧 token 一起带过去"""
        _status, headers, _body = self.get('/')
        self.assertIn('no-store', headers.get('Cache-Control', ''))


if __name__ == '__main__':
    unittest.main()
