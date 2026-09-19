'use strict';
'require view';
'require form';
'require fs';
'require uci';
'require ui';

/*
 * LuCI view for luci-app-menuMove.
 *
 * Backend access goes through the /usr/bin/menu-move CLI, invoked with the
 * stock "file" rpcd object (fs.exec).  Two reasons for that:
 *
 *   - it does not depend on the optional "menu_move" ubus object, so the page
 *     keeps working when that plugin is unavailable;
 *   - whatever the CLI prints on stderr - e.g. an ucode error while loading
 *     /usr/share/ucode/luci/menuMove.uc - is shown verbatim instead of being
 *     swallowed, which makes deployment problems visible.
 *
 * The override file is regenerated right after "Save & apply" and by the
 * "Regenerate menu now" button; the browser menu cache is flushed afterwards.
 */

var CLI = '/usr/bin/menu-move';

/* Run the CLI; never rejects - ACL denials are reported like error output. */
function cli_run(args) {
	return fs.exec(CLI, args).catch(function(err) {
		return { code: -1, stdout: '', stderr: (err && err.message) ? err.message : String(err) };
	});
}

function cli_error(res) {
	var out = ((res.stderr || '')).trim();

	if (out.length)
		return out;

	return ((res.stdout || '')).trim() || _('The command failed with exit code %s.').format(res.code);
}

/* menu-move json -> { status: {...}, specs: [...], plan: {...} } */
function cli_state() {
	return cli_run([ 'json' ]).then(function(res) {
		var data = null;

		if (res.code == 0) {
			try {
				data = JSON.parse(res.stdout);
			}
			catch (e) {
				data = null;
			}
		}

		if (data === null || typeof(data) != 'object' ||
		    data.status === null || typeof(data.status) != 'object')
			throw new Error(cli_error(res));

		return data;
	});
}

/* Collect every visible menu entry as a flat list of { path, title, order, type }. */
function collect_entries(tree) {
	var out = [];

	function walk(node, prefix) {
		ui.menu.getChildren(node).forEach(function(child) {
			var path = prefix ? '%s/%s'.format(prefix, child.name) : child.name;
			var action = child.action || {};

			out.push({
				path: path,
				title: child.title,
				order: (child.order != null) ? child.order : 1000,
				type: action.type || '-'
			});

			walk(child, path);
		});
	}

	walk(tree, '');

	return out;
}

/* Short, flat status lines - no long explanations. */
function status_lines(data) {
	var st = data.status, plan = data.plan || {}, lines = [];

	lines.push('%s: %s'.format(_('Configuration'), st.enabled ? _('enabled') : _('disabled')));
	lines.push('%s: %d'.format(_('Rules'), st.rules));
	lines.push('%s: %s (%s)'.format(_('Generated file'), st.generated,
		st.exists ? _('present') : _('missing')));

	if (st.stale)
		lines.push(_('The generated file is outdated - regenerate it.'));

	if (st.marker_present)
		lines.push(_('Note: the marker file %s exists, so hidden entries are visible again.').format(st.marker));

	(plan.errors || []).forEach(function(e) {
		lines.push('⚠ %s'.format(e.error));
	});

	return lines;
}

return view.extend({
	load: function() {
		return Promise.all([
			uci.load('menu-move'),
			ui.menu.load(),
			cli_state().catch(function(err) { return err; })
		]);
	},

	/* Rebuild the override file and reload the interface. */
	handleRegenerate: function(ev) {
		var btn = ev.currentTarget, done = function() { btn.disabled = false; };

		btn.disabled = true;

		return cli_run([ 'apply' ]).then(function(res) {
			if (res.code != 0 || ((res.stderr || '')).trim().length)
				throw new Error(cli_error(res));

			return cli_state();
		}).then(function(data) {
			var applied = (data.plan || {}).applied || [];
			var msg = [ _('Menu override regenerated: %d rule(s) applied.').format(applied.length) ];

			applied.forEach(function(m) {
				msg.push('• %s → %s%s'.format(m.from, m.path, m.hidden ? _(' (original hidden)') : ''));
			});

			(data.warnings || []).forEach(function(w) { msg.push('⚠ %s'.format(w)); });
			(data.plan.errors || []).forEach(function(e) { msg.push('⚠ %s'.format(e.error)); });

			ui.menu.flushCache();
			done();

			ui.showModal(_('Menu layout'), [
				E('div', {}, msg.map(function(l) { return E('div', {}, l); })),
				E('p', { 'class': 'right' }, [
					E('button', {
						'class': 'btn cbi-button cbi-button-apply',
						'click': function() {
							ui.hideModal();
							window.location.reload();
						}
					}, [ _('Reload interface') ])
				])
			]);
		}).catch(function(err) {
			done();
			ui.addNotification(null, E('p', {}, _('Failed to regenerate the menu: %s').format(err.message || err)), 'error');
		});
	},

	handleSaveApply: function(ev, mode) {
		return this.handleSave(ev)
			.then(function() { return uci.apply(); })
			.then(function() { return cli_run([ 'apply' ]); })
			.then(function(res) {
				ui.menu.flushCache();

				var err = (res.code == 0) ? ((res.stderr || '')).trim() : cli_error(res);

				if (err.length)
					return ui.addNotification(_('Menu layout'),
						E('p', {}, _('Failed to regenerate the menu: %s').format(err)), 'error');

				ui.addNotification(_('Menu layout'),
					E('p', {}, _('Menu layout applied - reloading the interface.')), 'info');

				window.setTimeout(function() { window.location.reload(); }, 1500);
			})
			.catch(function(err) {
				ui.addNotification(null, E('p', {}, _('Failed to regenerate the menu: %s').format(err.message || err)), 'error');
			});
	},

	render: function(data) {
		var self = this;
		var tree = data[1] || {};
		var state = data[2];
		var entries = collect_entries(tree);
		var known = {};

		entries.forEach(function(e) { known[e.path] = true; });

		var m = new form.Map('menu-move', _('Menu Tabs'));

		var general = m.section(form.NamedSection, 'settings', 'settings', _('General'));
		general.anonymous = true;
		general.addremove = false;

		var o = general.option(form.Flag, 'enabled', _('Enable menu moving'));
		o.default = '1';
		o.rmempty = false;

		var s = m.section(form.GridSection, 'move', _('Move rules'));
		s.anonymous = true;
		s.addremove = true;
		s.sortable = true;

		var o1 = s.option(form.ListValue, 'from', _('Tab to move'));
		o1.rmempty = false;
		o1.modalonly = true;
		o1.value('', _('-- please choose --'));
		entries.forEach(function(e) {
			o1.value(e.path, '%s [%s]'.format(_(e.title), e.path));
		});

		/* Target section: only the top level groups (Services, Network, ...).
		 * The moved tab keeps its own name, so the category is all we need. */
		var o2 = s.option(form.ListValue, 'to', _('Target section'));
		o2.rmempty = false;
		o2.modalonly = true;
		o2.value('', _('-- please choose --'));

		var categories = {};

		entries.forEach(function(e) {
			if (e.path.split('/').length != 2)
				return;

			categories[e.path] = true;
			o2.value(e.path, '%s [%s]'.format(_(e.title), e.path));
		});

		/* keep categories already referenced by an existing rule selectable */
		uci.sections('menu-move', 'move').forEach(function(sec) {
			if (sec.to && !categories[sec.to])
				o2.value(sec.to, sec.to);
		});

		var o3 = s.option(form.Value, 'order', _('Order'));
		o3.datatype = 'uinteger';
		o3.placeholder = '100';
		o3.modalonly = true;

		var o4 = s.option(form.Value, 'title', _('New title'));
		o4.modalonly = true;
		o4.placeholder = _('keep original');

		var o5 = s.option(form.Flag, 'hide_original', _('Hide the original tab'));
		o5.default = '1';
		o5.modalonly = true;

		var o6 = s.option(form.Flag, 'enabled', _('Enabled'));
		o6.default = '1';
		o6.modalonly = true;

		/* Read-only grid column. All editable options above are modalonly
		 * (= modal dialog only), so without this the table row would render no
		 * cell at all. DummyValue is a non-modalonly child, so it shows up in
		 * the row while the modal keeps the real input fields. */
		var p1 = s.option(form.DummyValue, '_preview', '');
		p1.modalonly = false;
		p1.cfgvalue = function(sid) {
			var from = uci.get('menu-move', sid, 'from') || '';
			var to = uci.get('menu-move', sid, 'to') || '';

			return from ? '%s  \u2192  %s'.format(from, to || '?') : '-';
		};

		return m.render().then(function(mapNode) {
			var blocks = [];

			/* --- status / actions ------------------------------------- */
			var statusLines = (state instanceof Error)
				? [ '⚠ %s'.format(state.message) ]
				: status_lines(state);

			blocks.push(E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, _('Status')),
				E('div', { 'class': 'cbi-section-descr' }, statusLines.map(function(l) {
					return E('div', {}, l);
				}))
			]));

			return E('div', {}, [ mapNode ].concat(blocks));
		});
	}
});
