'use strict';
'require view';
'require form';
'require uci';
'require ui';
'require rpc';

/*
 * LuCI view for luci-app-menuMove.
 *
 * The backend (rpcd ubus object "menu_move") regenerates
 * /usr/share/luci/menu.d/zz-luci-app-menuMove.json from the rules stored in
 * /etc/config/menu-move.  The browser caches the menu tree in its session
 * storage, so the view flushes that cache and reloads the page after
 * applying changes.
 */

var callStatus = rpc.declare({ object: 'menu_move', method: 'status', expect: {} });
var callApply = rpc.declare({ object: 'menu_move', method: 'apply', expect: {} });

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

return view.extend({
	load: function() {
		return Promise.all([
			uci.load('menu-move'),
			ui.menu.load(),
			callStatus().catch(function() { return null; })
		]);
	},

	/* Rebuild the override file from the current UCI state and reload the UI. */
	handleApplyNow: function(node) {
		var self = this, btn = node.target;

		btn.disabled = true;

		return callApply().then(function(res) {
			ui.menu.flushCache();
			ui.hideModal();

			var lines = [], errs = (res.errors || []);

			if (res.removed)
				lines.push(_('The override file was removed (menu move disabled or no rules).'));
			else if (res.written)
				lines.push(_('Override file written: %s').format(res.generated));
			else
				lines.push(_('Nothing to do - the menu is left untouched.'));

			(res.applied || []).forEach(function(m) {
				lines.push('• %s → %s%s'.format(m.from, m.path,
					m.hidden ? _(' (original hidden)') : ''));
			});

			errs.forEach(function(e) {
				lines.push('⚠ %s'.format(e.error));
			});

			(res.warnings || []).forEach(function(w) {
				lines.push('⚠ %s'.format(w));
			});

			var content = E('div', {}, [
				E('p', {}, lines.map(function(l) { return E('div', {}, l); })),
				E('p', {}, _('The interface will reload now.'))
			]);

			ui.showModal(_('Menu layout'), [
				content,
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

			btn.disabled = false;
		}).catch(function(err) {
			btn.disabled = false;
			ui.addNotification(null, E('p', {}, _('Failed to regenerate the menu: %s').format(err)), 'error');
		});
	},

	handleSaveApply: function(ev, mode) {
		var self = this;

		return this.handleSave(ev)
			.then(function() { return uci.apply(); })
			.then(function() { return callApply(); })
			.then(function(res) {
				ui.menu.flushCache();
				ui.hideModal();

				if ((res.errors || []).length || (res.warnings || []).length) {
					var msg = [];

					(res.errors || []).forEach(function(e) { msg.push('⚠ %s'.format(e.error)); });
					(res.warnings || []).forEach(function(w) { msg.push('⚠ %s'.format(w)); });

					ui.addNotification(_('Menu layout'), E('div', {}, msg.map(function(m) {
						return E('div', {}, m);
					})), 'warning');
				}
				else {
					ui.addNotification(_('Menu layout'),
						E('p', {}, _('Menu layout applied - reloading the interface.')), 'info');
				}

				window.setTimeout(function() { window.location.reload(); }, 1500);
			})
			.catch(function(err) {
				ui.addNotification(null, E('p', {}, _('Failed to regenerate the menu: %s').format(err)), 'error');
			});
	},

	render: function(data) {
		var self = this;
		var tree = data[1] || {};
		var status = data[2];
		var entries = collect_entries(tree);
		var known = {};

		entries.forEach(function(e) { known[e.path] = true; });

		var m = new form.Map('menu-move', _('Menu Tabs'),
			_('Move menu entries (tabs) of the LuCI web interface to another section, for example move the tabs below "NAS" into "Services". The original entry is hidden, the same page is registered below the target section instead.'));

		var general = m.section(form.NamedSection, 'settings', 'settings', _('General'));
		general.anonymous = true;
		general.addremove = false;

		var o = general.option(form.Flag, 'enabled', _('Enable menu moving'));
		o.default = '1';
		o.rmempty = false;
		o.description = _('When disabled the override file is removed and the original menu layout is restored.');

		var s = m.section(form.GridSection, 'move', _('Move rules'));
		s.anonymous = true;
		s.addremove = true;
		s.sortable = true;
		s.description = _('Pick the tab to move and the section it should live in. "order" is the sort weight inside the target section (lower comes first, empty keeps the original value) and "new title" renames the tab; to only reorder a tab, set the target to its own section. Rules whose source entry no longer exists (app uninstalled) are skipped and reported.');

		var o1 = s.option(form.ListValue, 'from', _('Tab to move'));
		o1.rmempty = false;
		o1.modalonly = false;
		o1.value('', _('-- please choose --'));
		entries.forEach(function(e) {
			o1.value(e.path, '%s [%s]'.format(_(e.title), e.path));
		});

		/* keep entries that disappeared (e.g. app uninstalled) selectable */
		uci.sections('menu-move', 'move').forEach(function(sec) {
			if (sec.from && !known[sec.from])
				o1.value(sec.from, '%s (%s)'.format(sec.from, _('not found')));
		});

		var o2 = s.option(form.ListValue, 'to', _('Target section'));
		o2.rmempty = false;
		o2.modalonly = false;
		o2.value('', _('-- please choose --'));
		entries.forEach(function(e) {
			o2.value(e.path, '%s [%s]'.format(_(e.title), e.path));
		});

		var o3 = s.option(form.Value, 'order', _('Order'));
		o3.datatype = 'uinteger';
		o3.placeholder = '100';
		o3.modalonly = false;

		var o4 = s.option(form.Value, 'title', _('New title'));
		o4.modalonly = false;
		o4.placeholder = _('keep original');

		var o5 = s.option(form.Flag, 'hide_original', _('Hide the original tab'));
		o5.default = '1';
		o5.modalonly = false;

		var o6 = s.option(form.Flag, 'enabled', _('Enabled'));
		o6.default = '1';
		o6.modalonly = false;

		return m.render().then(function(mapNode) {
			var blocks = [];

			/* --- status / actions ------------------------------------- */
			var statusLines = [];

			if (!status)
				statusLines.push(_('The rpcd plugin "menu_move" is not reachable - the menu cannot be regenerated from here. Run "/etc/init.d/rpcd reload" on the router (or reinstall the package) and reload this page.'));
			else {
				statusLines.push('%s: %s'.format(_('Configuration'),
					status.enabled ? _('enabled') : _('disabled')));
				statusLines.push('%s: %s'.format(_('Rules'), status.rules));
				statusLines.push('%s: %s (%s)'.format(_('Generated file'), status.generated,
					status.exists ? _('present') : _('missing')));

				if (status.stale)
					statusLines.push(_('The generated file is outdated - regenerate it.'));

				if (status.marker_present)
					statusLines.push(_('Note: the marker file %s exists, so hidden entries are visible again.').format(status.marker));
			}

			blocks.push(E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, _('Status')),
				E('div', { 'class': 'cbi-section-descr' }, statusLines.map(function(l) {
					return E('div', {}, l);
				})),
				E('div', { 'class': 'cbi-page-actions' }, [
					E('button', {
						'class': 'btn cbi-button cbi-button-apply',
						'click': ui.createHandlerFn(self, 'handleApplyNow')
					}, [ _('Regenerate menu now') ]),
					' ',
					E('button', {
						'class': 'btn cbi-button',
						'click': function() {
							ui.menu.flushCache();
							window.location.reload();
						}
					}, [ _('Reload interface') ])
				])
			]));

			/* --- reference: current menu structure -------------------- */
			var rows = entries.map(function(e) {
				return E('tr', {}, [
					E('td', { 'style': 'padding:0 .5em' }, e.path),
					E('td', { 'style': 'padding:0 .5em' }, _(e.title)),
					E('td', { 'style': 'padding:0 .5em' }, e.type)
				]);
			});

			blocks.push(E('details', {}, [
				E('summary', {}, _('Show the current menu entries (%d)').format(entries.length)),
				E('div', { 'style': 'max-height:24em;overflow:auto' }, [
					E('table', { 'class': 'table' }, [
						E('thead', {}, E('tr', {}, [
							E('th', {}, _('Path')),
							E('th', {}, _('Title')),
							E('th', {}, _('Type'))
						])),
						E('tbody', {}, rows)
					])
				])
			]));

			return E('div', {}, [ mapNode ].concat(blocks));
		});
	}
});
