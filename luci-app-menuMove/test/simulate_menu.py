#!/usr/bin/env python3
"""
Simulate LuCI's server side menu building + the client side menu rendering
using the *generated* override file, to verify that the tab really ends up in
the target section.

This is a faithful port of the relevant upstream code:

  server: modules/luci-base/ucode/dispatcher.uc   (build_pagetree, check_depends)
  client: modules/luci-base/htdocs/luci-static/resources/ui.js
          (scrubMenu, ui.menu.getChildren)

Run via test/run.sh (needs test/work to be prepared).
"""
import json
import os
import sys
from pathlib import Path

TEST = Path(__file__).resolve().parent
MENU_DIR = TEST / 'work' / 'menu.d'
GEN = MENU_DIR / 'zz-luci-app-menuMove.json'
MARKER = str(TEST / 'work' / 'marker' / '.disabled')

SCHEMA = {
    'action': dict, 'auth': dict, 'depends': dict,
    'order': int, 'css': str, 'title': str,
    'setgroup': str, 'setuser': str,
    'wildcard': bool, 'firstchild_ineligible': bool,
}

fails = checks = 0


def check(cond, msg):
    global fails, checks
    checks += 1
    print(('PASS  ' if cond else 'FAIL  ') + msg)
    if not cond:
        fails += 1


def check_fs_depends(spec):
    """port of check_fs_depends() in dispatcher.uc"""
    for path, kind in spec.items():
        if kind == 'directory':
            if not os.path.isdir(path) or not os.listdir(path):
                return False
        elif kind == 'file':
            if not os.path.isfile(path):
                return False
        elif kind == 'executable':
            if not (os.path.isfile(path) and os.access(path, os.X_OK)):
                return False
        elif kind == 'absent':
            if os.path.exists(path):
                return False
    return True


def check_uci_depends(cfg):
    """simplified: /etc/config is not present here, treat known configs as existing"""
    for config, values in cfg.items():
        if values is True and config in ('aria2', 'rpcd', 'dropbear'):
            continue
        if isinstance(values, dict):
            continue
    return True


def check_depends(spec):
    depends = spec.get('depends') or {}
    if isinstance(depends.get('fs'), (list, dict)):
        alts = depends['fs'] if isinstance(depends['fs'], list) else [depends['fs']]
        if not any(check_fs_depends(a) for a in alts):
            return False
    if isinstance(depends.get('uci'), (list, dict)):
        alts = depends['uci'] if isinstance(depends['uci'], list) else [depends['uci']]
        if not any(check_uci_depends(a) for a in alts):
            return False
    return True


def build_pagetree(files):
    """port of build_pagetree() in dispatcher.uc"""
    tree = {'action': {'type': 'firstchild'}}

    for path in files:
        data = json.loads(path.read_text())
        for mpath, spec in data.items():
            if not isinstance(spec, dict):
                continue
            node = tree
            for seg in [s for s in mpath.split('/') if s]:
                node.setdefault('children', {})
                node['children'].setdefault(seg, {'satisfied': True})
                node = node['children'][seg]
            if node is not tree:
                for k, t in SCHEMA.items():
                    if isinstance(spec.get(k), t):
                        node[k] = spec[k]
                node['action'] = spec.get('action', node.get('action'))
                node['satisfied'] = check_depends(spec)
    return tree


def scrub_menu(node):
    """port of scrubMenu() in ui.js"""
    has_satisfied_child = False
    for child in (node.get('children') or {}).values():
        child = scrub_menu(child)
        if child.get('title') and not child.get('firstchild_ineligible'):
            has_satisfied_child = has_satisfied_child or bool(child.get('satisfied'))
    if isinstance(node.get('action'), dict) and node['action'].get('type') == 'firstchild' \
            and not has_satisfied_child:
        node['satisfied'] = False
    return node


def get_children(node):
    """port of ui.menu.getChildren() in ui.js"""
    kids = []
    for name, child in (node.get('children') or {}).items():
        if not child.get('satisfied'):
            continue
        if 'title' not in child:
            continue
        kids.append(dict(child, name=name))
    return sorted(kids, key=lambda c: (c.get('order', 1000), c['name']))


def resolve(node, path):
    """port of resolve_page(): stop at the first unsatisfied segment"""
    for seg in [s for s in path.split('/') if s]:
        node = (node.get('children') or {}).get(seg)
        if not node or not node.get('satisfied'):
            return None
    return node


def visible(node, prefix=''):
    out = []
    for child in get_children(node):
        path = f'{prefix}/{child["name"]}'
        action = child.get('action') or {}
        out.append((path, child.get('order'), child.get('title'), action.get('type'), action.get('path')))
        out.extend(visible(child, path))
    return out


def dump(tree, label):
    print(f'--- visible menu: {label} ---')
    for path, order, title, atype, apath in visible(tree):
        depth = path.count('/') - 1
        detail = f'{atype}:{apath}' if apath else atype
        print(f'    {"  " * depth}{title:<22} {path:<34} {detail}')
    print()


files = sorted(p for p in MENU_DIR.glob('*.json') if p.name != GEN.name)
before = scrub_menu(build_pagetree(files))
dump(before, 'without menu-move overrides (installed apps)')

check(GEN.exists(), 'the generated override file exists (run test.uc first)')

files_after = sorted(MENU_DIR.glob('*.json'))
after = scrub_menu(build_pagetree(files_after))
dump(after, 'with menu-move overrides applied')


def names(tree, path):
    node = resolve(tree, path)
    return [c['name'] for c in get_children(node)] if node else None


# --- expectations -----------------------------------------------------------
check(names(before, 'admin/nas') == ['samba', 'nfs', 'aria2', 'ttyd'],
      'before: NAS holds samba, nfs, aria2, ttyd')
check(names(before, 'admin/services') == ['samba4', 'ttyd', 'openclash'],
      'before: Services holds samba4, ttyd, openclash')

check(names(after, 'admin/nas') == ['ttyd'],
      'after: NAS only holds the entries that were not moved')
check(names(after, 'admin/services') == ['ttyd', 'samba4', 'nfs', 'aria2', 'openclash'],
      'after: Services holds the moved tabs, sorted by order')
check(names(after, 'admin/status') == ['overview', 'samba'],
      'after: Status holds overview + the moved samba entry')
check(names(after, 'admin/services/aria2') == ['log'],
      'after: the moved subtree kept its child tab')

node = resolve(after, 'admin/services/nfs')
check(node is not None and node.get('action', {}).get('path') == 'nas/nfs',
      'after: /admin/services/nfs resolves to the NFS view (nas/nfs)')
node = resolve(after, 'admin/services/aria2/log')
check(node is not None and node.get('action', {}).get('path') == 'nas/aria2_log',
      'after: a moved sub-tab still resolves to its view')
check(resolve(after, 'admin/nas/nfs') is None,
      'after: the old URL /admin/nas/nfs is gone (documented trade-off)')

raw = build_pagetree(files_after)
check(raw['children']['admin']['children']['nas']['children']['nfs']['satisfied'] is False,
      'after: the original node is still in the tree but unsatisfied')
check(raw['children']['admin']['children']['nas']['children']['nfs']['depends']['fs'][0][MARKER] == 'file',
      'after: hiding is done through the fs guard on the marker file')

print(f'\n==== {checks} checks, {fails} failures ====')
sys.exit(1 if fails else 0)
