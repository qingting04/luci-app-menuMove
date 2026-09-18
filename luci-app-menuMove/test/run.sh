#!/bin/sh
# Local test runner for the menu-move logic.
#
#   UCODE=/path/to/ucode test/run.sh
#
# The runner builds a private fixture root below test/work and points the
# MENU_MOVE_* environment variables at it, so the tests can run as an
# unprivileged user and never touch /usr/share/luci.
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
PKG=$(cd "$HERE/.." && pwd)
UCODE=${UCODE:-ucode}
WORK="$HERE/work"

command -v "$UCODE" >/dev/null 2>&1 || {
	echo "ucode not found - install ucode or set UCODE=/path/to/ucode" >&2
	exit 77
}

rm -rf "$WORK"
mkdir -p "$WORK/menu.d" "$WORK/lua" "$WORK/mods" "$WORK/marker"

cp "$HERE"/fixtures/menu.d/*.json "$WORK/menu.d/"
cp "$HERE"/fixtures/uci-state.json "$WORK/uci-state.json"
cp "$HERE"/fixtures/lua/*.lua "$WORK/lua/"
cp "$HERE"/lib/uci-stub.uc "$WORK/mods/uci.uc"

# 复刻 luci.mk 的真实安装布局：${CURDIR}/ucode/* -> /usr/share/ucode/luci/*
# （模块名因此是 luci.menu-move，而不是裸的 menu-move）
mkdir -p "$WORK/mods/luci"
ln -sf "$PKG/ucode/menu-move.uc" "$WORK/mods/luci/menu-move.uc"

# Make all ucode modules visible through one single -L directory: the module
# search path of some ucode builds does not accept several -L arguments.
UCODEDIR=$(dirname "$(command -v "$UCODE")")
for dir in "$UCODEDIR" /usr/lib/ucode /usr/share/ucode; do
	[ -d "$dir" ] || continue
	for f in "$dir"/*.so "$dir"/*.uc; do
		[ -e "$f" ] && ln -sf "$f" "$WORK/mods/$(basename "$f")"
	done
done

export MENU_MOVE_MENU_DIR="$WORK/menu.d"
export MENU_MOVE_GEN_PATH="$WORK/menu.d/zz-luci-app-menuMove.json"
export MENU_MOVE_MARKER="$WORK/marker/.disabled"
export MENU_MOVE_LUA_DIR="$WORK/lua"
export UCI_STUB_FILE="$WORK/uci-state.json"

echo "== unit tests =="
"$UCODE" -L "$WORK/mods" "$HERE/test.uc"

echo
echo "== CLI smoke test =="
"$UCODE" -L "$WORK/mods" "$PKG/root/usr/bin/menu-move" status
"$UCODE" -L "$WORK/mods" "$PKG/root/usr/bin/menu-move" check || true
"$UCODE" -L "$WORK/mods" "$PKG/root/usr/bin/menu-move" paths
# the fixture config contains one invalid rule on purpose, so apply exits 1
"$UCODE" -L "$WORK/mods" "$PKG/root/usr/bin/menu-move" apply || true

echo
echo "== rpcd plugin =="
# rpcd loads plugin files in ucode "raw mode" and uses the top level return
# value as the ubus method signature - run it exactly that way.
"$UCODE" -R -L "$WORK/mods" "$PKG/root/usr/share/rpcd/ucode/luci.menu-move" \
	&& echo "ok    plugin loads in rpcd raw mode"
grep -q "menu_move:" "$PKG/root/usr/share/rpcd/ucode/luci.menu-move" \
	&& echo "ok    ubus object menu_move is declared"

echo
echo "== 部署路径一致性（防止再出现「导入名 ≠ 安装路径」这类问题）=="
if grep -rn "from 'menu[-]move'" "$PKG/root" "$PKG/ucode" "$HERE/test.uc" "$HERE/simulate_menu.py" 2>/dev/null; then
	echo "FAIL  还有文件用裸模块名 'menu-move' 导入（luci.mk 装到 /usr/share/ucode/luci/）"
	exit 1
else
	echo "ok    所有导入都用 luci.menu-move"
fi
# 真的按安装布局验一遍：模块在 <dir>/luci/menu-move.uc，用 luci.menu-move 导入
"$UCODE" -L "$WORK/mods" -e "import * as mm from 'luci.menu-move'; print('ok    luci.menu-move 按安装布局解析成功（GEN_NAME=' + mm.GEN_NAME + '）\n');"

echo
echo "== page <-> CLI 契约（网页解析 menu-move json 的输出）=="
"$UCODE" -L "$WORK/mods" "$PKG/root/usr/bin/menu-move" json > "$WORK/state.json"
python3 - "$WORK/state.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for key in ('status', 'specs', 'plan', 'warnings'):
    assert key in d, 'missing top level key %r' % key
for key in ('enabled', 'rules', 'generated', 'exists', 'stale', 'marker', 'marker_present'):
    assert key in d['status'], 'missing status key %r' % key
for key in ('applied', 'errors'):
    assert key in d['plan'], 'missing plan key %r' % key
for entry in d['plan']['applied']:
    for key in ('from', 'path', 'hidden'):
        assert key in entry, 'missing applied key %r' % key
print('ok    menu-move json 含网页需要的全部字段（%d 条计划）' % len(d['plan']['applied']))
PY

echo
echo "== menu simulation (dispatcher + client menu algorithm) =="
python3 "$HERE/simulate_menu.py"
