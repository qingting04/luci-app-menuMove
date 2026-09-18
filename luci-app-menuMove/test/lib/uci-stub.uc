'use strict';

/*
 * Test stub replacing OpenWrt's ucode "uci" module.  It serves the content of
 * a JSON file (env UCI_STUB_FILE) that is shaped like the real UCI config:
 *
 *   { "menu-move": { "settings": {".type":"settings","enabled":"1"},
 *                    "cfg01":    {".type":"move","from":"...","to":"..."} } }
 *
 * The file is re-read on every cursor() call, so tests may rewrite it
 * in between two apply() runs.
 */

import { readfile } from 'fs';

const state_file = getenv('UCI_STUB_FILE') ?? '/tmp/menu-move-test-uci.json';

function read_state() {
	try {
		return json(readfile(state_file)) ?? {};
	}
	catch (e) {
		return {};
	}
}

export function cursor() {
	let loaded = {}, state = read_state();

	return {
		load: function(name) {
			loaded[name] = state[name] ?? null;

			return loaded[name] != null;
		},

		get: function(name, section, option) {
			return loaded[name]?.[section]?.[option] ?? null;
		},

		get_all: function(name, section) {
			return loaded[name]?.[section] ?? null;
		},

		foreach: function(name, type, cb) {
			state[name] ??= {};

			for (let sname, sect in state[name]) {
				if ((sect['.type'] ?? null) != type)
					continue;

				if (cb(sect) === false)
					break;
			}
		}
	};
}
