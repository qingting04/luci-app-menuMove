#!/usr/bin/ucode
'use strict';

/*
 * Test suite for the shared menu-move logic.
 *
 * Run it with test/run.sh - it prepares the fixture environment and points
 * MENU_MOVE_* at the fixture directories, so no root access is needed.
 */

import {
	menu_paths, clone, merge_specs, norm_path, last_segment, read_specs, read_rules, build_plan, render_overrides, lua_conflicts, status, apply
} from 'luci.menuMove';
import { readfile, writefile, stat, unlink, dirname } from 'fs';

const PATHS = menu_paths();

const BASE = dirname(SCRIPT_NAME);
const WORK = BASE + '/work';
const OPTS = {
	menu_dir: WORK + '/menu.d',
	gen_path: WORK + '/menu.d/' + PATHS.gen_name,
	marker: WORK + '/marker/.disabled',
	lua_dir: WORK + '/lua',
	config: PATHS.config
};
const GEN = OPTS.gen_path;
const UCI_STATE = WORK + '/uci-state.json';
const UCI_STATE_ORIG = readfile(UCI_STATE);

let fails = 0, checks = 0;

function check(cond, msg) {
	checks++;

	if (cond)
		print(sprintf('PASS  %s\n', msg));
	else {
		fails++;
		print(sprintf('FAIL  %s\n', msg));
	}
}

function uci_state() {
	return json(readfile(UCI_STATE));
}

function set_uci_state(state) {
	writefile(UCI_STATE, sprintf('%.J\n', state));
}

if (stat(GEN))
	unlink(GEN);

print("### 1. read_specs / merge semantics\n");
let scan = read_specs(OPTS);
check(exists(scan.specs, 'admin/nas/nfs'), 'read_specs finds admin/nas/nfs');
check(scan.specs['admin/nas/nfs'].title == 'NFS', 'title of admin/nas/nfs is NFS');
check(scan.specs['admin/nas/nfs'].action.path == 'nas/nfs', 'action.path preserved');
check(!exists(scan.specs, PATHS.gen_name), 'a generated file is never read back');
check(scan.specs['admin/services'].action.type == 'firstchild', 'section action preserved');
check(scan.specs['admin/services/openclash'].title == 'OpenClash', 'later file overrides earlier one');

print("\n### 2. build_plan: move a leaf entry and hide the original\n");
let plan = build_plan(scan.specs, [
	{ from: 'admin/nas/nfs', to: 'admin/services', order: 45, title: '', hide_original: true }
], OPTS.marker);

check(length(plan.errors) == 0, 'no errors');
check(exists(plan.overrides, 'admin/services/nfs'), 'new entry admin/services/nfs created');
check(plan.overrides['admin/services/nfs'].title == 'NFS', 'new entry keeps the title');
check(plan.overrides['admin/services/nfs'].order == 45, 'new entry gets the new order');
check(type(plan.overrides['admin/services/nfs'].order) == 'int', 'order is an int (JSON number)');
check(plan.overrides['admin/services/nfs'].action.path == 'nas/nfs', 'new entry keeps the view action');
check(plan.overrides['admin/services/nfs'].depends.acl[0] == 'luci-app-nfs', 'new entry keeps the ACL');
check(plan.overrides['admin/services/nfs'].depends.fs == null, 'new entry has no hide guard');
check(exists(plan.overrides, 'admin/nas/nfs'), 'original entry is overridden');
check(plan.overrides['admin/nas/nfs'].depends.fs[0][OPTS.marker] == 'file', 'original entry got the fs guard');
check(plan.overrides['admin/nas/nfs'].depends.acl[0] == 'luci-app-nfs', 'original entry keeps its ACL');
check(plan.overrides['admin/nas/nfs'].title == 'NFS', 'original entry keeps title/action properties');

print("\n### 3. build_plan: subtree move\n");
let plan2 = build_plan(scan.specs, [
	{ from: 'admin/nas/aria2', to: 'admin/services', order: 46, hide_original: true }
], OPTS.marker);
check(exists(plan2.overrides, 'admin/services/aria2'), 'subtree root moved');
check(exists(plan2.overrides, 'admin/services/aria2/log'), 'subtree child moved as well');
check(plan2.overrides['admin/services/aria2/log'].title == 'Log', 'child keeps its own properties');
check(plan2.overrides['admin/services/aria2/log'].order == 10, 'child keeps its own order');

print("\n### 4. build_plan: in-place ordering (same section)\n");
let plan3 = build_plan(scan.specs, [
	{ from: 'admin/services/ttyd', to: 'admin/services', order: 5, title: 'Terminal (custom)', hide_original: true }
], OPTS.marker);
check(exists(plan3.overrides, 'admin/services/ttyd'), 'in-place entry overridden');
check(plan3.overrides['admin/services/ttyd'].order == 5, 'order updated');
check(plan3.overrides['admin/services/ttyd'].title == 'Terminal (custom)', 'title overridden');
check(plan3.overrides['admin/services/ttyd'].depends.fs == null, 'in-place entry is NOT hidden');
check(plan3.applied[0].in_place == true, 'in_place flag set');

print("\n### 5. build_plan: rejected rules\n");
let errs = build_plan(scan.specs, [
	{ from: 'admin/does/notexist', to: 'admin/services', hide_original: true },
	{ from: 'admin/services/ttyd', to: 'admin/nas', hide_original: true },
	{ from: 'admin/nas/nfs', to: 'admin/not-a-section', hide_original: true },
	{ from: 'admin/nas', to: 'admin/nas/inner', hide_original: true },
	{ from: 'admin/', to: 'admin/services', hide_original: true },
	{ from: 'admin/nas', to: 'admin/nas', hide_original: true },
	{ from: 'admin/services/ttyd', to: 'admin/services', hide_original: true },
	{ from: 'admin/nas/nfs', to: 'admin/nas/nfs', hide_original: true }
], OPTS.marker);

let emsg = join('|', map(errs.errors, e => e.error));

for (let e in emsg == null ? [] : split(emsg, '|'))
	print(sprintf('      rejected: %s\n', e));

check(length(errs.errors) == 5, '5 of 8 rules rejected');
check(index(emsg, 'does not exist') != -1, 'missing source detected');
check(index(emsg, 'already exists below') != -1, 'name collision in the target section detected');
check(index(emsg, 'target section "admin/not-a-section" does not exist') != -1, 'unknown target section detected');
check(index(emsg, 'target lies inside the moved entry') != -1, 'cycle (target inside source) detected');
check(length(errs.applied) == 3, 'the 3 valid rules were applied');
check(!exists(errs.overrides, 'admin/nas/ttyd'), 'rejected collision rule did not hide the original');
check(errs.overrides['admin/nas/nfs'].depends.fs == null, 'the in-place rule for admin/nas/nfs did not add a hide guard');

print("\n### 6. apply() end to end\n");
/* 先删掉覆盖文件，确保这一节真的测到「写入」而不是「内容一致跳过」 */
unlink(GEN);
let res = apply(OPTS);
check(res.enabled == true, 'config is enabled');
check(res.rules == 5, 'five enabled rules read from uci (one disabled rule skipped)');
check(res.written == true, 'override file written');
check(res.unchanged == false, 'a fresh write is not reported as unchanged');
check(stat(GEN) != null, 'override file exists on disk');
check(length(res.errors) == 1, 'the invalid rule is reported');
check(length(res.applied) == 4, 'four rules applied');

let written = readfile(GEN);
print('---- generated ' + GEN + ' ----\n' + written + '\n------------------------\n');

let parsed = json(written);
check(parsed['admin/services/nfs'].title == 'NFS', 'generated: nfs moved to services');
check(parsed['admin/services/nfs'].order == 45, 'generated: order 45');
check(parsed['admin/services/aria2/log'] != null, 'generated: subtree child moved');
check(parsed['admin/nas/nfs'].depends.fs[0][OPTS.marker] == 'file', 'generated: hide guard present');
check(parsed['admin/nas/nfs'].title == 'NFS', 'generated: hidden entry keeps title/action');
check(keys(parsed)[0] == 'admin/nas/aria2', 'generated: keys are sorted');
check(parsed['admin/status/samba'].title == 'Network Shares', 'generated: samba moved into Status');
check(keys(parsed)[length(keys(parsed)) - 1] == 'admin/status/samba', 'generated: last key is admin/status/samba (sorted output)');

print("\n### 7. status() and idempotency\n");
let st = status(OPTS);
check(st.exists == true, 'status: override present');
check(st.stale == false, 'status: not stale right after apply');
check(st.rules == 5, 'status: rule count');

let first = readfile(GEN);
sleep(1100);
apply(OPTS);
check(readfile(GEN) == first, 'apply is idempotent');

print("\n### 8. disabling the config removes the override\n");
let off = uci_state();
off['menu-move'].settings.enabled = '0';
set_uci_state(off);

let res2 = apply(OPTS);
check(res2.removed == true, 'override file removed when disabled');
check(stat(GEN) == null, 'override file is gone');

let none = uci_state();
delete none['menu-move'].cfg01;
delete none['menu-move'].cfg02;
delete none['menu-move'].cfg03;
delete none['menu-move'].cfg04;
delete none['menu-move'].cfg05;
set_uci_state(none);

let res3 = apply(OPTS);
check(res3.written == false && res3.removed == false, 'no rules: nothing written, nothing removed');

set_uci_state(json(UCI_STATE_ORIG));
apply(OPTS);
check(stat(GEN) != null, 'override restored for the simulation step');

print("\n### 9. legacy Lua controller detection\n");
let warn = lua_conflicts(OPTS, [
	{ from: 'admin/services/openclash', hide_original: true },
	{ from: 'admin/nas/nfs', hide_original: true }
]);
check(length(warn) == 1, 'exactly one Lua controller warning');
check(match(warn[0], /openclash/), 'warning names the affected entry');

print("\n### 10. env override hook\n");
check(status({ menu_dir: WORK + '/menu.d' }).rules == 5, 'opts override works');

print("\n### 11. 原子/幂等写入 + 内容比对过期判定\n");

set_uci_state(json(UCI_STATE_ORIG));
unlink(GEN); /* 从「文件不存在」开始，先验证真的会写 */
let a1 = apply(OPTS);
check(a1.written && !a1.unchanged, 'apply writes the file when it is missing');

let mtime1 = stat(GEN).mtime;
let a2 = apply(OPTS);
check(!a2.written && a2.unchanged, 'identical content is not rewritten');
check(stat(GEN).mtime == mtime1, 'mtime kept when content is identical (menu cache stays valid)');
check(stat(GEN + '.tmp') == null, 'no leftover .tmp file');
check(status(OPTS).stale == false, 'not stale right after apply');

/* 只改 mtime、内容不变：旧的 mtime 判定在这里会误报过期 */
writefile(WORK + '/menu.d/luci-app-nfs.json', readfile(WORK + '/menu.d/luci-app-nfs.json'));
check(status(OPTS).stale == false, 'touch with identical content is not stale');

/* 新增一个没被规则引用的 menu.d：生成内容不变，所以也不算过期 */
writefile(WORK + '/menu.d/zz-newapp.json',
	sprintf('%.J\n', { 'admin/services/newapp': { title: 'New App', action: { type: 'view', path: 'services/newapp' } } }));
check(status(OPTS).stale == false, 'unreferenced new menu.d is not stale');
unlink(WORK + '/menu.d/zz-newapp.json');

/* 改规则：生成内容会变 → 立刻算过期 */
let state = json(UCI_STATE_ORIG);
state['menu-move']['cfg01']['order'] = '99';
set_uci_state(state);
check(status(OPTS).stale == true, 'changed rule is detected as stale');

/* 关掉总开关：期望状态是「没有覆盖文件」，所以也算过期（要清理） */
state['menu-move']['settings']['enabled'] = '0';
set_uci_state(state);
check(status(OPTS).stale == true, 'disabling is detected as stale (file must go away)');

/* 收尾：恢复出厂 fixtures 并重新生成（后面的仿真步骤依赖它） */
set_uci_state(json(UCI_STATE_ORIG));
apply(OPTS);
check(stat(GEN) != null, 'fixture override restored for the simulation step');

print(sprintf('\n==== %d checks, %d failures ====\n', checks, fails));
exit(fails ? 1 : 0);
