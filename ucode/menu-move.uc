'use strict';

/*
 * menu-move - re-organize LuCI tabs in the web interface.
 *
 * Shared logic, used by
 *   - /usr/bin/menu-move              (CLI, for SSH users)
 *   - /usr/share/rpcd/ucode/menu-move (ubus object "menu_move")
 *
 * Background - how the LuCI menu is built (LuCI modules/luci-base/ucode/dispatcher.uc):
 *
 *   - the menu tree is assembled from every /usr/share/luci/menu.d/*.json
 *     file, in alphabetical file order
 *   - for one and the same menu path, a later file overrides the single
 *     properties (title / order / action / depends / ...) of an earlier one,
 *     but the path itself - i.e. the position inside the menu - cannot be
 *     changed by re-declaring it
 *   - a menu entry is rendered only when its "depends" checks (acl, fs, uci)
 *     are satisfied; unsatisfied entries are hidden (client side: ui.js
 *     getChildren() skips nodes with satisfied == false)
 *
 * Therefore "moving a tab" means:
 *
 *   1. clone the original entry - and its whole subtree - below the new
 *      parent path, then
 *   2. hide the original entry by adding an unsatisfiable fs guard to its
 *      depends, so it is not displayed any more.
 *
 * The generated file is picked up on the next request (the dispatcher caches
 * the tree in /tmp/luci-indexcache and invalidates it when the file list
 * changes), a browser reload is enough to show the new layout.
 */

import { readfile, glob, stat, unlink, basename, open } from 'fs';
import { cursor } from 'uci';

export const MENU_DIR = '/usr/share/luci/menu.d';
export const GEN_NAME = 'zz-luci-app-menuMove.json';
export const MARKER = '/usr/lib/luci-menu-move/.disabled';
export const CONFIG = 'menu-move';
export const LUA_DIR = '/usr/lib/lua/luci/controller';

/*
 * Property whitelist and types, kept in sync with the schema used by
 * modules/luci-base/ucode/dispatcher.uc (build_pagetree()).
 */
const schema = {
	action: 'object',
	auth: 'object',
	cors: 'bool',
	css: 'string',
	depends: 'object',
	order: 'int',
	setgroup: 'string',
	setuser: 'string',
	title: 'string',
	wildcard: 'bool',
	firstchild_ineligible: 'bool'
};

/*
 * MENU_MOVE_* environment variables allow overriding the well known paths.
 * This is a testing/debugging aid, e.g. for running the logic against a
 * fixture directory on a development machine - it is never set by the
 * package itself and no user input flows into it.
 */
function env_path(name, fallback) {
	let v = getenv(name);

	return (type(v) == 'string' && length(v)) ? v : fallback;
}

function defaults(o) {
	o ??= {};

	return {
		menu_dir: o.menu_dir ?? env_path('MENU_MOVE_MENU_DIR', MENU_DIR),
		gen_name: o.gen_name ?? GEN_NAME,
		gen_path: o.gen_path ?? env_path('MENU_MOVE_GEN_PATH',
			sprintf('%s/%s', env_path('MENU_MOVE_MENU_DIR', MENU_DIR), GEN_NAME)),
		marker: o.marker ?? env_path('MENU_MOVE_MARKER', MARKER),
		config: o.config ?? env_path('MENU_MOVE_CONFIG', CONFIG),
		lua_dir: o.lua_dir ?? env_path('MENU_MOVE_LUA_DIR', LUA_DIR)
	};
}

export function clone(src) {
	switch (type(src)) {
	case 'array':
		return map(src, clone);

	case 'object':
		let dest = {};

		for (let k, v in src)
			dest[k] = clone(v);

		return dest;

	default:
		return src;
	}
}

/* Merge an array of parsed menu.d documents the very same way the dispatcher does. */
export function merge_specs(docs) {
	let specs = {};

	for (let doc in docs) {
		if (type(doc) != 'object')
			continue;

		for (let path, spec in doc) {
			if (type(spec) != 'object')
				continue;

			if (!exists(specs, path))
				specs[path] = {};

			for (let k, t in schema)
				if (type(spec[k]) == t)
					specs[path][k] = clone(spec[k]);
		}
	}

	return specs;
}

/* "admin/nas/nfs/" -> "admin/nas/nfs", invalid -> null */
export function norm_path(p) {
	if (type(p) != 'string')
		return null;

	let s = trim(p, '/ \t\r\n');

	if (!length(s) || !match(s, /^[A-Za-z0-9_.-]+(\/[A-Za-z0-9_.-]+)*$/))
		return null;

	return s;
}

export function last_segment(p) {
	let segs = split(p, '/');

	return segs[length(segs) - 1];
}

function to_int(v) {
	if (type(v) == 'int')
		return v;

	if (type(v) == 'string' && match(v, /^-?\d+$/))
		return +v;

	return null;
}

function is_true(v) {
	return v === true || v === 1 || v === '1';
}

function is_subpath(path, parent) {
	return path != parent && substr(path, 0, length(parent) + 1) == parent + '/';
}

export function read_specs(o) {
	let opts = defaults(o), docs = [], sources = {};

	for (let file in glob(opts.menu_dir + '/*.json')) {
		let name = basename(file);

		/* never read back our own generated file */
		if (name == opts.gen_name)
			continue;

		let doc = null;

		try {
			doc = json(readfile(file));
		}
		catch (e) {
			warn(sprintf('menu-move: cannot parse %s: %s\n', file, e));
			continue;
		}

		if (type(doc) == 'object') {
			push(docs, doc);

			for (let path in doc)
				sources[path] ??= name;
		}
	}

	return { specs: merge_specs(docs), sources };
}

export function read_rules(o) {
	let opts = defaults(o), c = cursor(), rules = [], enabled = true;

	c.load(opts.config);
	enabled = (c.get(opts.config, 'settings', 'enabled') ?? '1') != '0';

	c.foreach(opts.config, 'move', (s) => {
		if ((s.enabled ?? '1') == '0')
			return;

		push(rules, {
			from: trim(s.from ?? ''),
			to: trim(s.to ?? ''),
			title: trim(s.title ?? ''),
			order: to_int(s.order),
			hide_original: (s.hide_original ?? '1') != '0'
		});
	});

	return { enabled: enabled, rules: rules };
}

/*
 * Pure planner: no file system access, so it can be tested in isolation.
 *
 * specs  - merged menu.d specs (path -> spec)
 * rules  - array of { from, to, order, title, hide_original }
 * marker - path of the file whose absence hides a menu entry
 */
export function build_plan(specs, rules, marker) {
	let out = {}, errors = [], applied = [];

	for (let rule in rules) {
		let from = norm_path(rule.from), to = norm_path(rule.to);

		if (from == null) {
			push(errors, { from: rule.from, to: rule.to, error: 'invalid source path' });
			continue;
		}

		if (to == null) {
			push(errors, { from: from, to: rule.to, error: 'invalid target path' });
			continue;
		}

		if (!exists(specs, from)) {
			push(errors, { from: from, to: to, error: sprintf('menu entry "%s" does not exist (app not installed?)', from) });
			continue;
		}

		/* moving a section into itself would create a loop */
		if (is_subpath(to, from)) {
			push(errors, { from: from, to: to, error: 'target lies inside the moved entry' });
			continue;
		}

		if (!exists(specs, to)) {
			push(errors, { from: from, to: to, error: sprintf('target section "%s" does not exist', to) });
			continue;
		}

		let name = last_segment(from);
		let npath = to + '/' + name;

		/* the entry itself or its own section: only fix up order/title */
		if (to == from || npath == from) {
			let spec = clone(specs[from]);

			if (length(rule.title))
				spec.title = rule.title;

			if (rule.order != null)
				spec.order = rule.order;

			out[from] = spec;
			push(applied, { from: from, to: to, path: from, entries: [ from ], hidden: false, in_place: true });
			continue;
		}

		if (exists(specs, npath) || exists(out, npath)) {
			push(errors, {
				from: from, to: to,
				error: sprintf('"%s" already exists below "%s"', name, to)
			});
			continue;
		}

		let moved = [], failed = false;

		for (let path, spec in specs) {
			if (path != from && !is_subpath(path, from))
				continue;

			let rel = (path == from) ? '' : substr(path, length(from) + 1);
			let np = length(rel) ? (npath + '/' + rel) : npath;

			if (exists(specs, np) || exists(out, np)) {
				push(errors, { from: from, to: to, error: sprintf('conflict: "%s" already exists', np) });
				failed = true;
				continue;
			}

			let copy = clone(spec);

			if (!length(rel)) {
				if (length(rule.title))
					copy.title = rule.title;

				if (rule.order != null)
					copy.order = rule.order;
			}

			out[np] = copy;
			push(moved, np);
		}

		if (failed)
			continue;

		if (is_true(rule.hide_original)) {
			let hidden = clone(specs[from]);

			hidden.depends ??= {};

			let guard = {};
			guard[marker] = 'file';
			hidden.depends.fs = [ guard ];

			out[from] = hidden;
		}

		push(applied, {
			from: from, to: to, path: npath, entries: moved,
			hidden: is_true(rule.hide_original), in_place: false
		});
	}

	return { overrides: out, errors: errors, applied: applied };
}

export function render_overrides(overrides) {
	let sorted = {};

	for (let path in sort(keys(overrides)))
		sorted[path] = overrides[path];

	return sprintf('%.J\n', sorted);
}

function write_file(path, content) {
	let fd = open(path, 'w', 0644);

	if (!fd)
		return false;

	fd.write(content);
	fd.close();

	return true;
}

function remove_file(path) {
	if (stat(path))
		unlink(path);
}

/*
 * Heuristic check for legacy Lua controllers, which are loaded after the
 * menu.d JSON files and would therefore override our JSON stubs.
 */
export function lua_conflicts(o, rules) {
	let opts = defaults(o), warnings = [];

	for (let dir in [ opts.lua_dir, opts.lua_dir + '/*' ]) {
		for (let file in glob(dir + '/*.lua')) {
			let src = null;

			try {
				src = readfile(file);
			}
			catch (e) {
				continue;
			}

			for (let rule in rules) {
				let from = norm_path(rule.from);
				let path = from ?? rule.from;
				let last = last_segment(path);

				if (match(src, regexp('"' + path + '"', 'g')) ||
				    match(src, regexp('"' + last + '"', 'g')))
					push(warnings, sprintf('"%s" is probably declared by the Lua controller %s - hiding it may not work', path, basename(file)));
			}
		}
	}

	return uniq(warnings);
}

export function status(o) {
	let opts = defaults(o);
	let rl = read_rules(opts);
	let st = stat(opts.gen_path);
	let stale = false;

	if (rl.enabled && length(rl.rules)) {
		if (!st) {
			stale = true;
		}
		else {
			for (let file in glob(opts.menu_dir + '/*.json')) {
				let name = basename(file);

				if (name == opts.gen_name)
					continue;

				let s = stat(file);

				if (s && s.mtime > st.mtime) {
					stale = true;
					break;
				}
			}
		}
	}

	return {
		enabled: rl.enabled,
		rules: length(rl.rules),
		generated: opts.gen_path,
		exists: !!st,
		mtime: st?.mtime ?? null,
		stale: stale,
		marker: opts.marker,
		marker_present: !!stat(opts.marker)
	};
}

export function apply(o) {
	let opts = defaults(o);
	let rl = read_rules(opts);
	let res = {
		enabled: rl.enabled,
		rules: length(rl.rules),
		generated: opts.gen_path,
		written: false,
		removed: false,
		errors: [],
		applied: [],
		warnings: []
	};

	/* disabled or no rules at all: make sure no stale override is left behind */
	if (!rl.enabled || !length(rl.rules)) {
		if (stat(opts.gen_path)) {
			unlink(opts.gen_path);
			res.removed = true;
		}

		return res;
	}

	let scan = read_specs(opts);
	let plan = build_plan(scan.specs, rl.rules, opts.marker);

	res.errors = plan.errors;
	res.applied = plan.applied;
	res.warnings = lua_conflicts(opts, filter(rl.rules, rule => is_true(rule.hide_original)));

	if (!length(plan.applied)) {
		if (stat(opts.gen_path)) {
			unlink(opts.gen_path);
			res.removed = true;
		}

		return res;
	}

	res.content = render_overrides(plan.overrides);
	res.written = write_file(opts.gen_path, res.content);

	if (!res.written)
		push(res.errors, { error: sprintf('cannot write %s', opts.gen_path) });

	return res;
}
