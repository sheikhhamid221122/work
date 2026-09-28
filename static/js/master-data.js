/* Shared behaviour for the Buyers, Products and Business Profiles pages:
 * requests, toasts, the right-hand sheet, confirm dialog, pagination, search
 * highlighting and the Excel import dialog. Exposed as window.MD.
 */
(function () {
    'use strict';

    var MD = window.MD = {};

    MD.esc = function (value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    };

    /** Escape `text` and wrap the first occurrence of `term` in <mark>. */
    MD.highlight = function (text, term) {
        var raw = String(text === null || text === undefined ? '' : text);
        if (!term) return MD.esc(raw);
        var i = raw.toLowerCase().indexOf(term.toLowerCase());
        if (i < 0) return MD.esc(raw);
        return MD.esc(raw.slice(0, i)) + '<mark class="md-hit">' + MD.esc(raw.slice(i, i + term.length)) +
            '</mark>' + MD.esc(raw.slice(i + term.length));
    };

    /** fetch -> {ok, status, data}; never throws for HTTP errors. */
    MD.request = function (url, options) {
        options = options || {};
        var init = { method: options.method || 'GET', credentials: 'same-origin', headers: {} };
        if (options.json !== undefined) {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(options.json);
        } else if (options.body) {
            init.body = options.body;
        }
        return fetch(url, init).then(function (res) {
            return res.json().catch(function () { return {}; }).then(function (data) {
                return { ok: res.ok, status: res.status, data: data || {} };
            });
        }).catch(function () {
            return { ok: false, status: 0, data: { error: 'Could not reach the server. Check your connection.' } };
        });
    };

    MD.errorText = function (result, fallback) {
        var d = result && result.data;
        return (d && (d.message || d.error)) || fallback || 'Something went wrong.';
    };

    // Money arrives as decimal strings; formatting for display only.
    MD.pkr = function (value, decimals) {
        var n = Number(value || 0);
        var d = decimals === undefined ? 0 : decimals;
        return 'PKR ' + n.toLocaleString('en-PK', { minimumFractionDigits: d, maximumFractionDigits: d });
    };

    MD.num = function (value) {
        return Number(value || 0).toLocaleString('en-PK', { maximumFractionDigits: 2 });
    };

    MD.date = function (iso) {
        if (!iso) return '–';
        var parts = String(iso).split('-');
        if (parts.length !== 3) return iso;
        var d = new Date(+parts[0], +parts[1] - 1, +parts[2]);
        return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
    };

    MD.debounce = function (fn, ms) {
        var t;
        return function () {
            var args = arguments, self = this;
            clearTimeout(t);
            t = setTimeout(function () { fn.apply(self, args); }, ms);
        };
    };

    /** Option lists shared with the invoice form (provinces, UoMs, ...). */
    var formOptions = null;
    MD.formOptions = function () {
        if (!formOptions) {
            formOptions = MD.request('/api/form-options').then(function (r) { return r.ok ? r.data : {}; });
        }
        return formOptions;
    };

    MD.fillSelect = function (select, options, placeholder) {
        var html = placeholder !== undefined ? '<option value="">' + MD.esc(placeholder) + '</option>' : '';
        (options || []).forEach(function (o) {
            html += '<option value="' + MD.esc(o.value) + '">' + MD.esc(o.label || o.value) + '</option>';
        });
        select.innerHTML = html;
    };

    /** Select the option whose value matches case-insensitively; returns it. */
    MD.selectValue = function (select, value) {
        var want = String(value || '').trim().toLowerCase();
        for (var i = 0; i < select.options.length; i++) {
            if (select.options[i].value.toLowerCase() === want) { select.selectedIndex = i; return true; }
        }
        select.value = '';
        return false;
    };

    // ---------------------------------------------------------------- toasts

    MD.toast = function (type, message) {
        var host = document.getElementById('md-toasts');
        if (!host) {
            host = document.createElement('div');
            host.id = 'md-toasts';
            host.className = 'md-toasts';
            host.setAttribute('role', 'status');
            host.setAttribute('aria-live', 'polite');
            document.body.appendChild(host);
        }
        var icon = { success: 'fa-circle-check', error: 'fa-circle-exclamation', info: 'fa-circle-info' }[type] || 'fa-circle-info';
        var el = document.createElement('div');
        el.className = 'md-toast ' + type;
        el.innerHTML = '<i class="fas ' + icon + '" aria-hidden="true"></i><div>' + MD.esc(message) + '</div>';
        host.appendChild(el);
        setTimeout(function () { el.remove(); }, type === 'error' ? 6000 : 3500);
    };

    // ---------------------------------------------------- sheet / dialogs

    var overlay = null;
    var openLayers = [];

    function ensureOverlay() {
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.className = 'md-overlay';
        overlay.hidden = true;
        overlay.addEventListener('click', function () {
            var top = openLayers[openLayers.length - 1];
            if (top && !top.hasAttribute('data-modal')) MD.close(top);
        });
        document.body.appendChild(overlay);
        return overlay;
    }

    MD.open = function (el, focusSelector) {
        ensureOverlay().hidden = false;
        el.hidden = false;
        if (openLayers.indexOf(el) < 0) openLayers.push(el);
        document.body.style.overflow = 'hidden';
        var target = focusSelector ? el.querySelector(focusSelector) : el.querySelector('input:not([type=hidden]):not([readonly]), select, textarea');
        if (target) setTimeout(function () { target.focus(); }, 30);
    };

    MD.close = function (el) {
        el.hidden = true;
        openLayers = openLayers.filter(function (x) { return x !== el; });
        if (!openLayers.length) {
            if (overlay) overlay.hidden = true;
            document.body.style.overflow = '';
        }
        el.dispatchEvent(new CustomEvent('md:closed'));
    };

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || !openLayers.length) return;
        var top = openLayers[openLayers.length - 1];
        MD.close(top);
    });

    document.addEventListener('click', function (e) {
        var closer = e.target.closest('[data-md-close]');
        if (closer) {
            var layer = closer.closest('.md-sheet, .md-dialog');
            if (layer) MD.close(layer);
        }
    });

    /** Promise<boolean>. */
    MD.confirm = function (opts) {
        var dlg = document.getElementById('md-confirm');
        if (!dlg) {
            dlg = document.createElement('div');
            dlg.id = 'md-confirm';
            dlg.className = 'md-dialog md-dialog-sm';
            dlg.setAttribute('role', 'alertdialog');
            dlg.setAttribute('aria-modal', 'true');
            dlg.hidden = true;
            dlg.innerHTML =
                '<div class="md-sheet-head"><h3 class="md-sheet-title" id="md-confirm-title"></h3>' +
                '<p class="md-sheet-desc" id="md-confirm-text"></p></div>' +
                '<div class="md-dialog-foot"><button type="button" class="md-btn md-btn-outline" data-choice="no">Cancel</button>' +
                '<button type="button" class="md-btn md-btn-danger" data-choice="yes" id="md-confirm-yes"></button></div>';
            document.body.appendChild(dlg);
        }
        dlg.querySelector('#md-confirm-title').textContent = opts.title || 'Are you sure?';
        dlg.querySelector('#md-confirm-text').textContent = opts.message || '';
        var yes = dlg.querySelector('#md-confirm-yes');
        yes.textContent = opts.confirmText || 'Delete';
        return new Promise(function (resolve) {
            function done(value) {
                dlg.removeEventListener('click', onClick);
                dlg.removeEventListener('md:closed', onClosed);
                if (!dlg.hidden) MD.close(dlg);
                resolve(value);
            }
            function onClick(e) {
                var btn = e.target.closest('[data-choice]');
                if (btn) done(btn.getAttribute('data-choice') === 'yes');
            }
            function onClosed() { done(false); }
            dlg.addEventListener('click', onClick);
            dlg.addEventListener('md:closed', onClosed);
            MD.open(dlg, '[data-choice="no"]');
        });
    };

    /** Show an error inside a form's alert box. */
    MD.formError = function (alertEl, message) {
        alertEl.textContent = message || '';
        alertEl.hidden = !message;
        if (message) alertEl.scrollIntoView({ block: 'nearest' });
    };

    // ------------------------------------------------------------- pagination

    MD.renderPager = function (host, page, pages, onPage) {
        if (pages <= 1) { host.innerHTML = ''; return; }
        var html = '<button type="button" data-page="' + (page - 1) + '"' + (page <= 1 ? ' disabled' : '') +
            ' aria-label="Previous page"><i class="fas fa-chevron-left"></i></button>';
        var start = Math.max(1, Math.min(page - 2, pages - 4));
        var end = Math.min(pages, start + 4);
        for (var p = start; p <= end; p++) {
            html += '<button type="button" data-page="' + p + '"' + (p === page ? ' class="is-current" aria-current="page"' : '') + '>' + p + '</button>';
        }
        html += '<button type="button" data-page="' + (page + 1) + '"' + (page >= pages ? ' disabled' : '') +
            ' aria-label="Next page"><i class="fas fa-chevron-right"></i></button>';
        host.innerHTML = html;
        host.querySelectorAll('button[data-page]').forEach(function (b) {
            b.addEventListener('click', function () { onPage(+b.getAttribute('data-page')); });
        });
    };

    /** Filter popover next to the search box. */
    MD.popover = function (trigger, pop) {
        trigger.addEventListener('click', function (e) {
            e.stopPropagation();
            pop.hidden = !pop.hidden;
            trigger.setAttribute('aria-expanded', String(!pop.hidden));
        });
        document.addEventListener('click', function (e) {
            if (!pop.hidden && !pop.contains(e.target) && e.target !== trigger) {
                pop.hidden = true;
                trigger.setAttribute('aria-expanded', 'false');
            }
        });
    };

    // ----------------------------------------------------------------- import

    /**
     * Wire the import dialog. cfg: {dialog, templateUrl, importUrl, noun, onDone}
     * Flow: download template -> choose file -> "Check file" (commit=0) shows
     * every row's outcome -> "Import N" (commit=1) saves the good rows.
     */
    MD.setupImport = function (cfg) {
        var dlg = cfg.dialog;
        var fileInput = dlg.querySelector('input[type=file]');
        var drop = dlg.querySelector('.md-drop');
        var dropTitle = drop.querySelector('.t');
        var dropDesc = drop.querySelector('.d');
        var report = dlg.querySelector('[data-report]');
        var error = dlg.querySelector('[data-error]');
        var checkBtn = dlg.querySelector('[data-action=check]');
        var importBtn = dlg.querySelector('[data-action=import]');
        var file = null;

        function reset() {
            file = null;
            fileInput.value = '';
            dropTitle.textContent = 'Choose the filled-in .xlsx file';
            dropDesc.textContent = 'or drop it here';
            report.innerHTML = '';
            MD.formError(error, '');
            checkBtn.hidden = false;
            checkBtn.disabled = true;
            importBtn.hidden = true;
        }

        function setFile(f) {
            if (!f) return;
            if (!/\.xlsx$/i.test(f.name)) {
                MD.formError(error, 'Upload an .xlsx file (the template format). Older .xls files: open them in Excel and "Save As" .xlsx.');
                return;
            }
            file = f;
            dropTitle.textContent = f.name;
            dropDesc.textContent = Math.max(1, Math.round(f.size / 1024)) + ' KB · click to choose another';
            report.innerHTML = '';
            MD.formError(error, '');
            checkBtn.hidden = false;
            checkBtn.disabled = false;
            importBtn.hidden = true;
        }

        drop.addEventListener('click', function () { fileInput.click(); });
        drop.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } });
        fileInput.addEventListener('change', function () { setFile(fileInput.files[0]); });
        ['dragenter', 'dragover'].forEach(function (t) {
            drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add('is-over'); });
        });
        ['dragleave', 'drop'].forEach(function (t) {
            drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.remove('is-over'); });
        });
        drop.addEventListener('drop', function (e) { setFile(e.dataTransfer.files[0]); });

        function send(commit) {
            var body = new FormData();
            body.append('file', file);
            body.append('commit', commit ? '1' : '0');
            return MD.request(cfg.importUrl, { method: 'POST', body: body });
        }

        function renderReport(data) {
            var s = data.summary || {};
            var labels = { ok: 'Ready', added: 'Added', restored: 'Restored', duplicate: 'Duplicate', error: 'Error' };
            var cls = { ok: 'ok', added: 'ok', restored: 'ok', duplicate: 'dup', error: 'err' };
            var html = '<div class="md-report-summary">' +
                '<span class="md-pill ok"><i class="fas fa-check"></i> ' + (data.committed ? data.added + ' added' : s.ok + ' ready') + '</span>' +
                (s.duplicate ? '<span class="md-pill dup"><i class="fas fa-clone"></i> ' + s.duplicate + ' duplicate' + (s.duplicate === 1 ? '' : 's') + ' skipped</span>' : '') +
                (s.error ? '<span class="md-pill err"><i class="fas fa-triangle-exclamation"></i> ' + s.error + ' with errors</span>' : '') +
                '</div>';
            if ((data.rows || []).length) {
                html += '<div class="md-report"><table><thead><tr><th>Row</th><th>' + MD.esc(cfg.noun) +
                    '</th><th>Result</th></tr></thead><tbody>';
                data.rows.forEach(function (r) {
                    var note = r.status === 'added' ? 'Added as ' + r.message : (r.message || labels[r.status]);
                    html += '<tr><td>' + r.row + '</td><td>' + MD.esc(r.name || '–') + '</td><td><span class="st ' +
                        cls[r.status] + '">' + labels[r.status] + '</span>' +
                        (note && note !== labels[r.status] ? ' — ' + MD.esc(note) : '') + '</td></tr>';
                });
                html += '</tbody></table></div>';
            } else {
                html += '<p class="md-hint">The sheet has no rows to import.</p>';
            }
            report.innerHTML = html;
        }

        checkBtn.addEventListener('click', function () {
            if (!file) return;
            checkBtn.disabled = true;
            checkBtn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Checking…';
            send(false).then(function (res) {
                checkBtn.innerHTML = '<i class="fas fa-list-check"></i> Check file';
                if (!res.ok) { checkBtn.disabled = false; MD.formError(error, MD.errorText(res, 'The file could not be checked.')); return; }
                renderReport(res.data);
                var ready = (res.data.summary || {}).ok || 0;
                checkBtn.hidden = ready > 0;
                checkBtn.disabled = false;
                importBtn.hidden = !ready;
                importBtn.disabled = false;
                importBtn.innerHTML = '<i class="fas fa-file-import"></i> Import ' + ready + ' ' + cfg.noun.toLowerCase() + (ready === 1 ? '' : 's');
            });
        });

        importBtn.addEventListener('click', function () {
            importBtn.disabled = true;
            importBtn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Importing…';
            send(true).then(function (res) {
                if (!res.ok) {
                    importBtn.disabled = false;
                    importBtn.innerHTML = '<i class="fas fa-file-import"></i> Try again';
                    MD.formError(error, MD.errorText(res, 'Import failed; nothing was saved.'));
                    return;
                }
                renderReport(res.data);
                importBtn.hidden = true;
                checkBtn.hidden = true;
                MD.toast('success', res.data.added + ' ' + cfg.noun.toLowerCase() + (res.data.added === 1 ? '' : 's') + ' imported.');
                if (cfg.onDone) cfg.onDone();
            });
        });

        return {
            open: function () { reset(); MD.open(dlg, '.md-drop'); }
        };
    };
})();
