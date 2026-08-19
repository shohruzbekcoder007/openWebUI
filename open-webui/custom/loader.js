/*
 * Open WebUI — custom sidebar page
 * Served at /static/loader.js
 *
 * Upstream ships static/loader.js and static/custom.css as EMPTY files and loads both on
 * every page (see src/app.html in the Open WebUI repo). They are the official seam for
 * customizing the UI, so this file is mounted read-only over the empty one from
 * docker-compose.yml — no Open WebUI source is forked or modified.
 *
 * What it does:
 *   - adds a "Custom" item to the sidebar, after the built-in menu items
 *   - opens an overlay page for it, tracked with the #custom URL hash
 *
 * The page is an overlay rather than a real /custom route because the SPA route table is
 * compiled into the bundle: navigating to an unknown path renders the app's 404 page.
 *
 * Extending it: window.OpenWebUICustomPage.setContent(html) or .content (the body
 * element). See open-webui/README.md.
 */
(function () {
	'use strict';

	var LABEL = 'Custom';
	var HASH = '#custom';
	var PAGE_ID = 'owui-custom-page';
	var ITEM_ATTR = 'data-owui-custom-item';

	// Built-in sidebar menu items; ours goes after the last one that is visible.
	var MENU_HREFS = ['/workspace', '/notes', '/automations', '/calendar', '/playground'];
	var MENU_SELECTOR = MENU_HREFS.map(function (href) {
		return 'a[href="' + href + '"]';
	}).join(',');

	var ICON =
		'<svg class="size-4" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" stroke-width="1.5" fill="none">' +
		'<rect x="3.5" y="3.5" width="7" height="7" rx="2" stroke="currentColor"/>' +
		'<rect x="13.5" y="3.5" width="7" height="7" rx="2" stroke="currentColor"/>' +
		'<rect x="3.5" y="13.5" width="7" height="7" rx="2" stroke="currentColor"/>' +
		'<path d="M17 14V20" stroke="currentColor" stroke-linecap="round"/>' +
		'<path d="M14 17H20" stroke="currentColor" stroke-linecap="round"/>' +
		'</svg>';

	// ---------------------------------------------------------------- sidebar item

	// Climbs from a menu link to the element representing the whole item. The two sidebar
	// variants nest it differently (the collapsed rail adds a tooltip wrapper), so climb
	// while the parent holds nothing but this one child.
	function itemRoot(anchor) {
		var el = anchor;
		for (var i = 0; i < 4; i++) {
			var parent = el.parentElement;
			if (!parent || parent === document.body || parent.id === 'sidebar') break;
			if (parent.childElementCount > 1) break;
			el = parent;
		}
		return el;
	}

	function stripActiveClasses(root) {
		var nodes = [root].concat(Array.prototype.slice.call(root.querySelectorAll('*')));
		nodes.forEach(function (node) {
			Array.prototype.slice.call(node.classList || []).forEach(function (name) {
				if (name.indexOf('bg-black/') === 0 || name.indexOf('dark:bg-white/') === 0) {
					node.classList.remove(name);
				}
			});
		});
	}

	// Clones a built-in item so the copy inherits whatever classes the current Open WebUI
	// version uses, then swaps in our label, icon and click handler.
	function buildItem(template, variant) {
		var item = template.cloneNode(true);
		item.setAttribute(ITEM_ATTR, '');
		item.setAttribute('data-owui-variant', variant);
		item.removeAttribute('data-id');

		var link = item.querySelector('a');
		if (!link) return null;

		link.setAttribute('href', HASH);
		link.setAttribute('id', 'sidebar-custom-button');
		link.setAttribute('aria-label', LABEL);
		link.setAttribute('title', LABEL);
		link.removeAttribute('draggable');
		stripActiveClasses(link);

		var icon = link.querySelector('svg');
		if (icon && icon.parentNode) {
			var holder = document.createElement('div');
			holder.innerHTML = ICON;
			icon.parentNode.replaceChild(holder.firstElementChild, icon);
		}

		// Only the expanded sidebar renders a label next to the icon.
		var walker = document.createTreeWalker(link, NodeFilter.SHOW_TEXT, null);
		var text;
		while ((text = walker.nextNode())) {
			if (text.nodeValue.trim()) {
				text.nodeValue = LABEL;
				break;
			}
		}

		link.addEventListener('click', function (event) {
			event.preventDefault();
			event.stopPropagation();
			open();
		});

		return item;
	}

	function syncSidebar() {
		var anchors = document.querySelectorAll(MENU_SELECTOR); // document order
		if (!anchors.length) return;

		var containers = [];
		var lastRoots = [];

		Array.prototype.forEach.call(anchors, function (anchor) {
			var root = itemRoot(anchor);
			var container = root.parentElement;
			if (!container) return;

			var index = containers.indexOf(container);
			if (index === -1) {
				containers.push(container);
				lastRoots.push(root);
			} else {
				lastRoots[index] = root;
			}
		});

		var list = document.getElementById('pinned-menu-items-list');

		containers.forEach(function (container, index) {
			if (container.querySelector('[' + ITEM_ATTR + ']')) return;

			var variant = list && (container === list || list.contains(container)) ? 'list' : 'rail';
			var item = buildItem(lastRoots[index], variant);
			if (item) lastRoots[index].insertAdjacentElement('afterend', item);
		});
	}

	// ---------------------------------------------------------------- overlay page

	function buildPage() {
		var page = document.getElementById(PAGE_ID);
		if (page) return page;

		page = document.createElement('div');
		page.id = PAGE_ID;
		page.setAttribute('hidden', '');
		page.innerHTML = [
			'<div class="owui-custom-head">',
			'<div class="owui-custom-title">',
			'<span class="owui-custom-badge">' + ICON + '</span>',
			'<span class="owui-custom-heading">',
			'<strong>' + LABEL + '</strong>',
			'<small>Custom tools and pages for this platform</small>',
			'</span>',
			'</div>',
			'<button type="button" class="owui-custom-close" aria-label="Close">&#10005;</button>',
			'</div>',
			'<div class="owui-custom-body">',
			'<div class="owui-custom-placeholder">',
			'This page is a placeholder — features will be added here.',
			'</div>',
			'</div>'
		].join('');

		page.querySelector('.owui-custom-close').addEventListener('click', function () {
			close();
		});

		document.body.appendChild(page);
		return page;
	}

	// The overlay covers everything right of the sidebar, whichever variant is on screen;
	// on mobile the sidebar floats above the content, so the overlay covers the viewport.
	function layout() {
		var page = document.getElementById(PAGE_ID);
		if (!page || page.hasAttribute('hidden')) return;

		var left = 0;
		var sidebar = document.getElementById('sidebar');
		if (sidebar && window.innerWidth >= 768) {
			var rect = sidebar.getBoundingClientRect();
			left = Math.max(0, Math.min(rect.right, window.innerWidth));
		}
		page.style.setProperty('--owui-custom-left', left + 'px');
	}

	function markActive(active) {
		Array.prototype.forEach.call(
			document.querySelectorAll('[' + ITEM_ATTR + ']'),
			function (item) {
				item.classList.toggle('is-active', active);
			}
		);
	}

	function isOpen() {
		var page = document.getElementById(PAGE_ID);
		return !!page && !page.hasAttribute('hidden');
	}

	function open() {
		buildPage().removeAttribute('hidden');
		if (location.hash !== HASH) {
			history.pushState(null, '', location.pathname + location.search + HASH);
		}
		layout();
		markActive(true);
	}

	function close() {
		var page = document.getElementById(PAGE_ID);
		if (page) page.setAttribute('hidden', '');
		if (location.hash === HASH) {
			history.replaceState(null, '', location.pathname + location.search);
		}
		markActive(false);
	}

	function syncFromHash() {
		if (location.hash === HASH) {
			if (!isOpen()) open();
		} else if (isOpen()) {
			close();
		}
	}

	// ---------------------------------------------------------------- wiring

	var queued = false;
	function schedule() {
		if (queued) return;
		queued = true;
		requestAnimationFrame(function () {
			queued = false;
			syncSidebar();
			if (location.hash === HASH && !isOpen()) open();
			markActive(isOpen());
			layout();
		});
	}

	function start() {
		schedule();
		new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });

		window.addEventListener('resize', layout);
		window.addEventListener('popstate', syncFromHash);
		window.addEventListener('hashchange', syncFromHash);

		document.addEventListener('keydown', function (event) {
			if (event.key === 'Escape' && isOpen()) close();
		});

		// Any other sidebar navigation leaves the page.
		document.addEventListener(
			'click',
			function (event) {
				if (!isOpen()) return;
				var target = event.target;
				if (!target || !target.closest) return;
				if (target.closest('[' + ITEM_ATTR + ']') || target.closest('#' + PAGE_ID)) return;
				if (target.closest('#sidebar') || target.closest('a[href^="/"]')) close();
			},
			true
		);
	}

	// Public hook for future features.
	window.OpenWebUICustomPage = {
		open: open,
		close: close,
		isOpen: isOpen,
		get content() {
			return buildPage().querySelector('.owui-custom-body');
		},
		setContent: function (content) {
			var body = buildPage().querySelector('.owui-custom-body');
			if (typeof content === 'string') {
				body.innerHTML = content;
			} else {
				body.innerHTML = '';
				body.appendChild(content);
			}
			return body;
		}
	};

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', start);
	} else {
		start();
	}
})();
