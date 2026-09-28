/* App shell: behaviour for partials/topbar.html and partials/sidebar.html.
 *
 *   - dropdown menus (environment, notifications, account)
 *   - environment switch, confirmed with the account password
 *   - theme toggle
 *   - the mobile navigation drawer
 *   - unsubmitted-drafts badge (window.TLP.draftCount for page scripts)
 *   - top-bar search over invoices, buyers and products
 *   - notifications bell
 *
 * Loaded with `defer`, so it runs after the page is parsed but before
 * DOMContentLoaded -- page scripts can rely on window.TLP in their
 * DOMContentLoaded handlers.
 */
(function () {
    'use strict';

    if (window.__tlpShellInitialized) return;
    window.__tlpShellInitialized = true;

    var TLP = window.TLP = window.TLP || {};

    function $(id) { return document.getElementById(id); }

    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function getJson(url) {
        return fetch(url, { credentials: 'same-origin' }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        });
    }

    // ------------------------------------------------------------ dropdowns

    var openDropdown = null;

    function closeDropdown() {
        if (!openDropdown) return;
        openDropdown.menu.hidden = true;
        openDropdown.trigger.setAttribute('aria-expanded', 'false');
        openDropdown = null;
    }

    function toggleDropdown(wrapper) {
        var trigger = wrapper.querySelector('[data-dd-trigger]');
        var menu = wrapper.querySelector('.tl-menu');
        if (openDropdown && openDropdown.menu === menu) { closeDropdown(); return; }
        closeDropdown();
        closeSearch();
        menu.hidden = false;
        trigger.setAttribute('aria-expanded', 'true');
        openDropdown = { trigger: trigger, menu: menu, wrapper: wrapper };
        wrapper.dispatchEvent(new CustomEvent('tl:open'));
    }

    document.querySelectorAll('.tl-dd').forEach(function (wrapper) {
        var trigger = wrapper.querySelector('[data-dd-trigger]');
        if (!trigger) return;
        trigger.addEventListener('click', function (e) {
            e.stopPropagation();
            toggleDropdown(wrapper);
        });
    });

    document.addEventListener('click', function (e) {
        if (openDropdown && !openDropdown.wrapper.contains(e.target)) closeDropdown();
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            if (openDropdown) { openDropdown.trigger.focus(); closeDropdown(); }
            closeDrawer();
        }
    });

    // ------------------------------------------------------ mobile drawer

    var drawer = $('tl-drawer');

    function openDrawer() {
        if (!drawer) return;
        drawer.hidden = false;
        document.body.classList.add('tl-drawer-open');
    }

    function closeDrawer() {
        if (!drawer || drawer.hidden) return;
        drawer.hidden = true;
        document.body.classList.remove('tl-drawer-open');
    }

    if ($('tl-menu-open')) $('tl-menu-open').addEventListener('click', openDrawer);
    if (drawer) {
        drawer.addEventListener('click', function (e) {
            if (e.target.closest('[data-drawer-close]') || e.target.closest('a')) closeDrawer();
        });
    }

    // ------------------------------------------------- environment switch

    (function () {
        var trigger = $('tl-env-trigger');
        var modal = $('env-switch-modal');
        if (!trigger || !modal) return;

        var message = $('env-switch-modal-message');
        var errorEl = $('env-switch-error');
        var form = $('env-switch-form');
        var password = $('env-switch-password');
        var confirmBtn = $('env-switch-confirm');
        var pendingEnv = null;

        function label(env) { return env === 'production' ? 'Production' : 'Sandbox'; }

        function showError(text) {
            errorEl.textContent = text;
            errorEl.hidden = false;
        }

        function close() {
            modal.hidden = true;
            errorEl.hidden = true;
            errorEl.textContent = '';
            password.value = '';
            pendingEnv = null;
            confirmBtn.disabled = false;
            confirmBtn.textContent = 'Switch';
        }

        function open(env) {
            pendingEnv = env;
            message.textContent = 'Enter your password to switch to ' + label(env) + '.';
            modal.hidden = false;
            password.focus();
        }

        document.querySelectorAll('[data-env-choice]').forEach(function (item) {
            item.addEventListener('click', function () {
                var env = item.getAttribute('data-env-choice');
                closeDropdown();
                if (env && env !== trigger.getAttribute('data-current-env')) open(env);
            });
        });

        $('env-switch-cancel').addEventListener('click', close);
        $('env-switch-modal-backdrop').addEventListener('click', close);
        modal.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            if (!pendingEnv) { close(); return; }
            var value = password.value.trim();
            if (!value) { showError('Password is required.'); return; }

            confirmBtn.disabled = true;
            confirmBtn.textContent = 'Switching…';
            errorEl.hidden = true;

            fetch('/switch-environment', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ environment: pendingEnv, password: value })
            })
                .then(function (response) {
                    return response.json().then(function (data) { return { ok: response.ok, data: data }; });
                })
                .then(function (result) {
                    if (!result.ok) throw new Error(result.data.error || 'Failed to switch environment');
                    window.location.reload();
                })
                .catch(function (err) {
                    showError(err.message);
                    confirmBtn.disabled = false;
                    confirmBtn.textContent = 'Switch';
                });
        });
    })();

    // --------------------------------------------------------- theme toggle

    // No stored preference means "follow the OS" (handled by the stylesheet's
    // prefers-color-scheme block); clicking stores an explicit choice. The
    // pre-paint script in each page <head> applies it before first paint.
    (function () {
        var btn = $('theme-toggle');
        if (!btn) return;
        var icon = $('theme-toggle-icon');
        var media = window.matchMedia('(prefers-color-scheme: dark)');

        function effective() {
            var explicit = document.documentElement.getAttribute('data-theme');
            if (explicit === 'dark' || explicit === 'light') return explicit;
            return media.matches ? 'dark' : 'light';
        }

        function paint() {
            var dark = effective() === 'dark';
            // The button shows the theme you would switch TO.
            icon.className = dark ? 'fas fa-sun' : 'fas fa-moon';
            var text = dark ? 'Switch to light mode' : 'Switch to dark mode';
            btn.setAttribute('aria-label', text);
            btn.title = text;
        }

        btn.addEventListener('click', function () {
            var next = effective() === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', next);
            try { localStorage.setItem('tlp-theme', next); } catch (e) { /* private mode */ }
            paint();
        });

        if (media.addEventListener) {
            media.addEventListener('change', function () {
                if (!document.documentElement.getAttribute('data-theme')) paint();
            });
        }
        paint();
    })();

    // ------------------------------------------------ unsubmitted drafts

    // Same request the Drafts page makes by default: every environment,
    // not yet submitted. Shared as a promise so the dashboard tile and the
    // sidebar badge are one request and always agree.
    TLP.draftCount = getJson('/api/draft-invoices?filter_submitted=not_submitted&filter_date=all')
        .then(function (drafts) { return Array.isArray(drafts) ? drafts.length : null; })
        .catch(function () { return null; });

    TLP.draftCount.then(function (count) {
        document.querySelectorAll('[data-draft-count]').forEach(function (badge) {
            if (count) {
                badge.textContent = count > 99 ? '99+' : String(count);
                badge.hidden = false;
            } else {
                badge.hidden = true;
            }
        });
    });

    // --------------------------------------------------------------- search

    var searchInput = $('tl-search-input');
    var searchPanel = $('tl-search-results');
    var searchTimer = null;
    var searchSeq = 0;
    var searchItems = [];
    var searchActive = -1;

    function closeSearch() {
        if (!searchPanel || searchPanel.hidden) return;
        searchPanel.hidden = true;
        searchInput.setAttribute('aria-expanded', 'false');
        searchActive = -1;
    }

    function openSearchPanel(html) {
        searchPanel.innerHTML = html;
        searchPanel.hidden = false;
        searchInput.setAttribute('aria-expanded', 'true');
        searchItems = Array.prototype.slice.call(searchPanel.querySelectorAll('.tl-sr-item'));
        searchActive = -1;
    }

    function highlight(text, term) {
        var safe = escapeHtml(text);
        if (!term) return safe;
        var i = String(text).toLowerCase().indexOf(term.toLowerCase());
        if (i < 0) return safe;
        var raw = String(text);
        return escapeHtml(raw.slice(0, i)) + '<mark>' + escapeHtml(raw.slice(i, i + term.length)) + '</mark>' +
            escapeHtml(raw.slice(i + term.length));
    }

    function resultRow(href, icon, title, meta, extra, data) {
        return '<a class="tl-sr-item" role="option" href="' + escapeHtml(href) + '"' + (data || '') + '>' +
            '<span class="tl-sr-icon"><i class="fas ' + icon + '" aria-hidden="true"></i></span>' +
            '<span class="tl-sr-main"><span class="tl-sr-title">' + title + '</span>' +
            '<span class="tl-sr-meta">' + meta + '</span></span>' + (extra || '') + '</a>';
    }

    function renderResults(data, term) {
        var html = '';
        var inv = data.invoices || [], buyers = data.buyers || [], products = data.products || [];

        if (inv.length) {
            html += '<div class="tl-sr-group">Invoices</div>';
            inv.forEach(function (x) {
                var meta = [x.buyer ? highlight(x.buyer, term) : '', escapeHtml(x.date || ''),
                    x.refNo ? 'Ref ' + highlight(x.refNo, term) : ''].filter(Boolean).join(' · ');
                var badge = '<span class="tl-sr-badge ' + (x.success ? 'ok' : 'bad') + '">' +
                    (x.success ? 'Submitted' : 'Failed') + '</span>';
                html += resultRow('/dashboard?invoice=' + encodeURIComponent(x.id), 'fa-file-invoice',
                    highlight(x.number, term), meta, badge, ' data-invoice-id="' + escapeHtml(x.id) + '"');
            });
        }
        if (buyers.length) {
            html += '<div class="tl-sr-group">Buyers</div>';
            buyers.forEach(function (b) {
                var meta = [b.ntn ? 'NTN/CNIC ' + highlight(b.ntn, term) : '', escapeHtml(b.province || '')]
                    .filter(Boolean).join(' · ');
                html += resultRow('/buyers?q=' + encodeURIComponent(b.name), 'fa-users',
                    highlight(b.name, term), meta);
            });
        }
        if (products.length) {
            html += '<div class="tl-sr-group">Products</div>';
            products.forEach(function (p) {
                var meta = [p.hsCode ? 'HS ' + highlight(p.hsCode, term) : '', escapeHtml(p.uom || '')]
                    .filter(Boolean).join(' · ');
                html += resultRow('/products?q=' + encodeURIComponent(p.description), 'fa-box',
                    highlight(p.description, term), meta);
            });
        }
        if (!html) {
            html = '<div class="tl-sr-empty">No invoices, buyers or products match “' + escapeHtml(term) + '”.</div>';
        } else {
            html += '<div class="tl-sr-foot"><span><kbd>↑</kbd><kbd>↓</kbd> to move</span><span><kbd>Enter</kbd> to open</span></div>';
        }
        openSearchPanel(html);
    }

    function runSearch() {
        var term = searchInput.value.trim();
        if (term.length < 2) {
            if (term.length === 0) closeSearch();
            else openSearchPanel('<div class="tl-sr-empty">Keep typing — at least 2 characters.</div>');
            return;
        }
        var seq = ++searchSeq;
        openSearchPanel('<div class="tl-sr-empty"><i class="fas fa-circle-notch fa-spin"></i> Searching…</div>');
        getJson('/api/search?q=' + encodeURIComponent(term))
            .then(function (data) { if (seq === searchSeq) renderResults(data, term); })
            .catch(function () {
                if (seq === searchSeq) openSearchPanel('<div class="tl-sr-empty">Search is unavailable right now.</div>');
            });
    }

    function setActive(index) {
        if (!searchItems.length) return;
        if (searchActive >= 0) searchItems[searchActive].classList.remove('is-active');
        searchActive = (index + searchItems.length) % searchItems.length;
        var item = searchItems[searchActive];
        item.classList.add('is-active');
        item.scrollIntoView({ block: 'nearest' });
    }

    if (searchInput && searchPanel) {
        searchInput.addEventListener('input', function () {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(runSearch, 220);
        });
        searchInput.addEventListener('focus', function () {
            closeDropdown();
            if (searchInput.value.trim().length >= 2 && searchPanel.innerHTML) {
                searchPanel.hidden = false;
                searchInput.setAttribute('aria-expanded', 'true');
            }
        });
        searchInput.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowDown') { e.preventDefault(); setActive(searchActive + 1); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(searchActive - 1); }
            else if (e.key === 'Enter') {
                var target = searchItems[searchActive >= 0 ? searchActive : 0];
                if (target && !searchPanel.hidden) { e.preventDefault(); target.click(); }
            } else if (e.key === 'Escape') {
                closeSearch();
                searchInput.blur();
            }
        });

        // An invoice opens in the dashboard's detail view: directly when that
        // view is on this page, otherwise via /dashboard?invoice=<id>.
        searchPanel.addEventListener('click', function (e) {
            var item = e.target.closest('.tl-sr-item');
            if (!item) return;
            var invoiceId = item.getAttribute('data-invoice-id');
            if (invoiceId && typeof window.viewInvoiceDetails === 'function') {
                e.preventDefault();
                closeSearch();
                window.viewInvoiceDetails(invoiceId);
            }
        });

        document.addEventListener('click', function (e) {
            if (!$('tl-search').contains(e.target)) closeSearch();
        });

        // Ctrl+K / Cmd+K focuses search from anywhere.
        document.addEventListener('keydown', function (e) {
            if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
                e.preventDefault();
                searchInput.focus();
                searchInput.select();
            }
        });

        if (/Mac|iPhone|iPad/.test(navigator.platform || '') && $('tl-search-kbd')) {
            $('tl-search-kbd').textContent = '⌘K';
        }
    }

    // -------------------------------------------------------- notifications

    (function () {
        var wrapper = $('tl-notifications');
        if (!wrapper) return;
        var dot = $('tl-bell-dot');
        var list = $('tl-notif-list');
        var countEl = $('tl-notif-count');
        var items = [];

        var ICONS = {
            info: 'fa-circle-info',
            success: 'fa-circle-check',
            warning: 'fa-triangle-exclamation',
            critical: 'fa-circle-exclamation'
        };

        function ago(iso) {
            var t = Date.parse(iso);
            if (isNaN(t)) return '';
            var s = Math.max(0, (Date.now() - t) / 1000);
            if (s < 60) return 'just now';
            if (s < 3600) return Math.floor(s / 60) + 'm ago';
            if (s < 86400) return Math.floor(s / 3600) + 'h ago';
            if (s < 604800) return Math.floor(s / 86400) + 'd ago';
            return new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
        }

        function paintDot(unread) {
            dot.hidden = !(unread > 0);
            countEl.textContent = unread > 0 ? unread + ' new' : '';
            wrapper.querySelector('[data-dd-trigger]').setAttribute('aria-label',
                unread > 0 ? 'Notifications, ' + unread + ' unread' : 'Notifications');
        }

        function render() {
            if (!items.length) {
                list.innerHTML = '<div class="tl-notif-empty"><i class="fas fa-bell-slash"></i>You’re all caught up.</div>';
                return;
            }
            list.innerHTML = items.map(function (n) {
                var level = ICONS[n.level] ? n.level : 'info';
                var body = '<span class="tl-notif-icon lvl-' + level + '"><i class="fas ' + ICONS[level] + '"></i></span>' +
                    '<span class="tl-notif-main"><span class="tl-notif-title">' + escapeHtml(n.title) + '</span>' +
                    (n.message ? '<span class="tl-notif-msg">' + escapeHtml(n.message) + '</span>' : '') +
                    '<span class="tl-notif-time">' + escapeHtml(ago(n.created_at)) + '</span></span>' +
                    (n.read ? '' : '<span class="tl-notif-unread" aria-label="unread"></span>');
                // Only same-site links: a notification must not become a way
                // to send someone to an arbitrary URL.
                var safeLink = /^\/(?!\/)/.test(n.link || '') ? n.link : '';
                return safeLink
                    ? '<a class="tl-notif-item" href="' + escapeHtml(safeLink) + '">' + body + '</a>'
                    : '<div class="tl-notif-item">' + body + '</div>';
            }).join('');
        }

        function load() {
            return getJson('/api/notifications').then(function (data) {
                items = Array.isArray(data.items) ? data.items : [];
                paintDot(data.unread || 0);
                if (!openDropdown || openDropdown.wrapper !== wrapper) render();
            }).catch(function () {
                list.innerHTML = '<div class="tl-notif-empty">Notifications are unavailable right now.</div>';
            });
        }

        // Opening the panel counts as reading what it shows. The rows keep
        // their unread marker for this viewing so you can see what was new.
        wrapper.addEventListener('tl:open', function () {
            render();
            var unreadIds = items.filter(function (n) { return !n.read; }).map(function (n) { return n.id; });
            if (!unreadIds.length) return;
            fetch('/api/notifications/read', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: unreadIds })
            }).then(function (r) {
                if (!r.ok) return;
                items.forEach(function (n) { n.read = true; });
                paintDot(0);
            }).catch(function () { /* stays unread; retried next open */ });
        });

        load();
        // Pick up new notifications without a reload, only while visible.
        setInterval(function () { if (!document.hidden) load(); }, 120000);
    })();
})();
