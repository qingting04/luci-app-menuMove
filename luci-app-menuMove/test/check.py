#!/usr/bin/env python3
"""
Sanity checks for luci-app-menuMove.

    python3 luci-app-menuMove/test/check.py

Runs on any development machine and in CI - no OpenWrt, no router needed.
It guards the invariants that already broke once in production:

  * every string shown in the LuCI view has a translation, and po/pot agree
    with the view and with each other
  * the view only calls CLI subcommands the rpcd ACL actually allows
    (a mismatch shows up as "Permission denied" in the browser)
  * the ucode module is imported under its installed name - luci.mk installs
    ucode/* into /usr/share/ucode/luci/, so the module is luci.<file> and never
    the bare file name
  * the ubus object declared by the rpcd plugin is granted by the ACL
  * the view's field defaults match the factory UCI configuration
"""

import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)

VIEW = os.path.join(PKG, 'htdocs/luci-static/resources/view/menuMove/overview.js')
PO = os.path.join(PKG, 'po/zh_Hans/luci-app-menuMove.po')
POT = os.path.join(PKG, 'po/templates/luci-app-menuMove.pot')
ACL = os.path.join(PKG, 'root/usr/share/rpcd/acl.d/luci-app-menuMove.json')
PLUGIN = os.path.join(PKG, 'root/usr/share/rpcd/ucode/luci.menuMove')
CLI = os.path.join(PKG, 'root/usr/bin/menu-move')
CONFIG = os.path.join(PKG, 'root/etc/config/menu-move')
INITD = os.path.join(PKG, 'root/etc/init.d/menu-move')

FAILURES = []


def section(name):
    print('== %s ==' % name)


def ok(label):
    print('  ok   %s' % label)


def bad(label):
    FAILURES.append(label)
    print('  FAIL %s' % label)


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def unescape(text):
    return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t', 'r': '\r'}.get(m.group(1), m.group(1)), text)


MSGID_RE = re.compile(r"""_\(\s*(?:'((?:\\.|[^'\\])*)'|"((?:\\.|[^"\\])*)")\s*\)""", re.S)
PO_ENTRY_RE = re.compile(r'msgid\s+"((?:\\.|[^"\\])*)"\s*\nmsgstr\s+"((?:\\.|[^"\\])*)"')


def view_msgids(src):
    out = []
    for m in MSGID_RE.finditer(src):
        raw = m.group(1) if m.group(1) is not None else m.group(2)
        out.append(unescape(raw))
    return out


def po_entries(path):
    entries = {}
    for m in PO_ENTRY_RE.finditer(read(path)):
        msgid = unescape(m.group(1))
        if msgid == '':
            continue  # file header
        entries[msgid] = unescape(m.group(2))
    return entries


# --- 1. translations --------------------------------------------------------
section('1. 翻译覆盖 / translation coverage')

view_src = read(VIEW)
ids = view_msgids(view_src)
po = po_entries(PO)
pot = po_entries(POT)

missing = [i for i in ids if i not in po]
if missing:
    bad('视图里有 %d 条文案缺翻译：%r' % (len(missing), missing))
else:
    ok('视图里所有文案都有 po 条目（共 %d 条）' % len(ids))

unused = [i for i in po if i not in ids]
if unused:
    bad('po 里有 %d 条已失效条目：%r' % (len(unused), unused))
else:
    ok('po 里没有失效条目')

empty = [i for i, t in po.items() if not t.strip()]
if empty:
    bad('po 里有空译文：%r' % empty)
else:
    ok('所有译文都非空')

if list(po.keys()) != list(pot.keys()):
    bad('pot 与 po 的条目不一致（po 独有 %r / pot 独有 %r）'
        % ([i for i in po if i not in pot], [i for i in pot if i not in po]))
else:
    ok('pot 与 po 条目一致（各 %d 条）' % len(po))

if [i for i, t in pot.items() if t != '']:
    bad('pot 里的 msgstr 必须为空')
else:
    ok('pot 全部 msgstr 为空')

# --- 2. view <-> ACL --------------------------------------------------------
section('2. 视图调用的 CLI 子命令 vs ACL 授权')

acl_raw = json.loads(read(ACL))
acl_text = read(ACL)
groups = list(acl_raw.values())
cli_calls = sorted(set(re.findall(r"""cli_run\(\s*\[\s*'([^']+)'""", view_src)))

m = re.search(r"""var\s+CLI\s*=\s*'([^']+)'""", view_src)
if not m:
    bad('视图里找不到 CLI 常量')
else:
    cli_path = m.group(1)
    ok('视图使用的 CLI 路径：%s' % cli_path)

    if not cli_calls:
        bad('视图里没有解析出任何 cli_run 调用')
    for cmd in cli_calls:
        if '%s %s' % (cli_path, cmd) in acl_text:
            ok('ACL 允许 `%s %s`' % (cli_path, cmd))
        else:
            bad('ACL 缺少 `%s %s` 的 exec 授权（浏览器会报 Permission denied）' % (cli_path, cmd))

    patterns = [p for g in groups for p in g.get('read', {}).get('file', {})]
    patterns += [p for g in groups for p in g.get('write', {}).get('file', {})]
    stray = [p for p in patterns if not p.startswith(cli_path + ' ')]
    if stray:
        bad('ACL 里有与视图无关的 exec 路径：%r' % stray)
    else:
        ok('ACL 里只有该 CLI 的 exec 授权')

    for group in groups:
        for perm in ('read', 'write'):
            methods = group.get(perm, {}).get('ubus', {}).get('file', [])
            if 'exec' not in methods:
                bad('ACL 的 %s 组缺少 ubus `file` 的 exec 方法' % perm)
    ok('ACL 声明了 ubus file 的 exec 方法')

# --- 3. module import name / install path -----------------------------------
section('3. ucode 模块名与安装路径')

bad_imports = []
for root, _dirs, files in os.walk(PKG):
    if os.sep + 'work' in root:
        continue
    for name in files:
        if not name.endswith(('.uc', '.js')) and '/usr/bin/' not in os.path.join(root, name):
            continue
        path = os.path.join(root, name)
        if re.search(r"from\s+'menuMove'|from\s+\"menu-move\"", read(path)):
            bad_imports.append(os.path.relpath(path, PKG))

if bad_imports:
    bad('这些文件用了裸模块名 menu-move（luci.mk 装到 /usr/share/ucode/luci/）：%r' % bad_imports)
else:
    ok('所有导入都使用 luci.menuMove')

if os.path.isdir(os.path.join(PKG, 'ucode')) and re.search(
        r"from\s+'luci\.menuMove'", read(CLI)):
    ok('CLI 以 luci.menuMove 导入模块（与 luci.mk 的 UCODE_LIBRARYDIR 一致）')
else:
    bad('CLI 必须用 luci.menuMove 导入模块')

# --- 4. rpcd plugin / ubus object ------------------------------------------
section('4. rpcd 插件与 ubus 对象')

plugin_src = read(PLUGIN)
obj = re.search(r'return\s*\{\s*(\w+)\s*:\s*\{', plugin_src)
if not obj:
    bad('rpcd 插件没有顶层 return { <object>: {...} }（rpcd 用这个表当 ubus 签名）')
else:
    name = obj.group(1)
    ok('插件声明的 ubus 对象：%s' % name)

    for meth in ('status', 'apply'):
        if re.search(r'\b%s\s*:\s*\{' % meth, plugin_src):
            ok('插件实现了方法 %s' % meth)
        else:
            bad('插件缺少方法 %s' % meth)

        granted = name in acl_text and '"%s"' % meth in acl_text
        if granted:
            ok('ACL 授权了 %s/%s' % (name, meth))
        else:
            bad('ACL 没有授权 %s/%s' % (name, meth))

# --- 5. UCI factory defaults vs view defaults -------------------------------
section('5. 出厂默认值与视图默认值')

config_src = read(CONFIG)
config_defaults = {}
for m in re.finditer(r'^\s*#?\s*option\s+(\w+)\s+\'([^\']*)\'', config_src, re.M):
    config_defaults.setdefault(m.group(1), m.group(2))

view_defaults = {}
current = None
for line in view_src.splitlines():
    m = re.search(r"""\.option\(\s*form\.\w+\s*,\s*'([^']+)'""", line)
    if m:
        current = m.group(1)
        continue
    m = re.search(r"""\.default\s*=\s*'([^']*)'""", line)
    if m and current:
        view_defaults.setdefault(current, m.group(1))

for opt, val in sorted(view_defaults.items()):
    if opt not in config_defaults:
        ok('%s 仅在视图中设置默认值 %r（出厂配置未声明）' % (opt, val))
    elif config_defaults[opt] == val:
        ok('%s 视图默认值 %r 与出厂配置一致' % (opt, val))
    else:
        bad('%s 视图默认值 %r != 出厂配置 %r' % (opt, val, config_defaults[opt]))

# --- 6. JSON / JS syntax ----------------------------------------------------
section('6. 语法与 JSON')

json_files = []
for root, _dirs, files in os.walk(PKG):
    if os.sep + 'work' in root:
        continue
    json_files += [os.path.join(root, f) for f in files if f.endswith('.json')]

broken = []
for path in json_files:
    try:
        json.loads(read(path))
    except Exception as e:
        broken.append('%s: %s' % (os.path.relpath(path, PKG), e))

if broken:
    bad('JSON 解析失败：%r' % broken)
else:
    ok('%d 个 JSON 文件全部可解析' % len(json_files))

if shutil.which('node'):
    for path in (VIEW,):
        proc = subprocess.run(['node', '--check', path], capture_output=True, text=True)
        if proc.returncode == 0:
            ok('node --check 通过：%s' % os.path.basename(path))
        else:
            bad('node --check 失败 %s: %s' % (path, proc.stderr.strip()))
else:
    print('  skip node --check（未安装 node）')

proc = subprocess.run(['sh', '-n', INITD], capture_output=True, text=True)
if proc.returncode == 0:
    ok('init 脚本 shell 语法通过')
else:
    bad('init 脚本语法错误：%s' % proc.stderr.strip())

# --- 7. ucode 语法保守性 ----------------------------------------------------
section('7. ucode 语法保守性（老固件兼容）')

NO_MODERN = [
    (re.compile(r'\?\?'), '?? / ??='),
    (re.compile(r'\?\.'), '?.'),
    (re.compile(r'`'), '模板字符串'),
    (re.compile(r'for \(let \w+,\s*\w+ in'), '多变量 for-in'),
]


def strip_comments(src):
    return re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', src, flags=re.S))


ucode_files = [
    os.path.join(PKG, 'ucode/menuMove.uc'),
    os.path.join(PKG, 'root/usr/bin/menu-move'),
]

for root, _dirs, files in os.walk(os.path.join(PKG, 'root/usr/share/rpcd/ucode')):
    ucode_files += [os.path.join(root, f) for f in files if not f.startswith('.')]

found = []

for path in ucode_files:
    code = strip_comments(read(path))

    for rx, name in NO_MODERN:
        if rx.search(code):
            found.append('%s 里出现 %s' % (os.path.relpath(path, PKG), name))

if found:
    bad('老固件不支持的 ucode 语法：%r（路由器上会报 Expecting \';\' 之类的语法错）' % found)
else:
    ok('%d 个 ucode 文件只用保守语法（无空值合并/可选链/模板字符串/多变量 for-in）' % len(ucode_files))

# --- 8. 必须可执行的文件 ----------------------------------------------------
section('8. 可执行位（init.d / uci-defaults / CLI）')

NEED_X = [
    'root/etc/init.d/menu-move',
    'root/etc/uci-defaults/90-luci-app-menuMove',
    'root/usr/bin/menu-move',
]

noexec = []

for rel in NEED_X:
    path = os.path.join(PKG, rel)

    if not os.path.exists(path):
        noexec.append('%s 不存在' % rel)
    elif not (os.stat(path).st_mode & 0o111):
        noexec.append('%s 没有执行位（装到路由器上会 Permission denied）' % rel)

if noexec:
    bad('; '.join(noexec))
else:
    ok('%d 个脚本都有执行位' % len(NEED_X))

# --- summary ---------------------------------------------------------------
print()
if FAILURES:
    print('%d 项失败' % len(FAILURES))
    for f in FAILURES:
        print('  - %s' % f)
    sys.exit(1)

print('0 项失败')
