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
ln -sf "$PKG/ucode/menu-move.uc" "$WORK/mods/menu-move.uc"

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
"$UCODE" -R -L "$WORK/mods" "$PKG/root/usr/share/rpcd/ucode/menu-move" \
	&& echo "ok    plugin loads in rpcd raw mode"
grep -q "menu_move:" "$PKG/root/usr/share/rpcd/ucode/menu-move" \
	&& echo "ok    ubus object menu_move is declared"

echo
echo "== menu simulation (dispatcher + client menu algorithm) =="
python3 "$HERE/simulate_menu.py"
