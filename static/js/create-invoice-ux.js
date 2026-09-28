/* Create Invoice -- layout helpers from the redesign prototype.
 *
 * Everything here sits in front of the existing wizard and drives it through
 * its own functions and elements, so the invoice logic is untouched:
 *
 *   - live summary rail          reads the form fields and the items table
 *   - business profile cards     pick = set #business-profile-select + change
 *   - searchable buyer picker    pick = set #buyer-select + change
 *   - searchable product picker  pick = selectProductRow(product), as the old menu did
 *   - advanced tax fields        a <details> that opens itself when in use
 *   - "save this new buyer / new products?" prompts, with "don't ask again"
 *
 * Exposes window.TLPInvoiceUX.{confirmSaveProducts, offerSaveBuyer}, called
 * from the Buyer and Products steps' Next buttons.
 */
(function () {
    'use strict';

    var $ = function (id) { return document.getElementById(id); };

    function esc(v) {
        return String(v === null || v === undefined ? '' : v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function highlight(text, term) {
        var raw = String(text || '');
        if (!term) return esc(raw);
        var i = raw.toLowerCase().indexOf(term.toLowerCase());
        if (i < 0) return esc(raw);
        return esc(raw.slice(0, i)) + '<mark>' + esc(raw.slice(i, i + term.length)) + '</mark>' + esc(raw.slice(i + term.length));
    }
    function pkr(n) {
        var v = Number(n) || 0;
        return 'PKR ' + v.toLocaleString('en-PK', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    function num(text) {
        var v = parseFloat(String(text || '').replace(/[^0-9.\-]/g, ''));
        return isFinite(v) ? v : 0;
    }
    function taxId(v) {
        var raw = String(v || '').trim().toUpperCase();
        var digits = raw.replace(/\D/g, '');
        return digits.length === 13 ? digits : raw.replace(/[^0-9A-Z]/g, '');
    }
    function nameKey(v) { return String(v || '').replace(/\s+/g, ' ').trim().toLowerCase(); }
    function notify(type, title, msg) {
        if (typeof window.showNotification === 'function') window.showNotification(type, title, msg);
    }
    function currentStep() {
        try { return (typeof state !== 'undefined' && state.currentStep) || 1; } catch (e) { return 1; }
    }
    function items() {
        try { return (typeof state !== 'undefined' && state.productItems) || []; } catch (e) { return []; }
    }
    function catalogue() {
        try { return (typeof productList !== 'undefined' && Array.isArray(productList)) ? productList : []; } catch (e) { return []; }
    }
    function url(endpoint) {
        return typeof window.apiUrl === 'function' ? window.apiUrl(endpoint) : endpoint;
    }

    // Wrap one of the page's global functions so we hear when it runs.
    function after(name, fn) {
        var orig = window[name];
        if (typeof orig !== 'function') return;
        window[name] = function () {
            var result = orig.apply(this, arguments);
            try { fn.apply(this, arguments); } catch (e) { console.error('[ux] ' + name, e); }
            return result;
        };
    }

    // --------------------------------------------------------- preferences
    // Per account, in this browser: 'ask' (default), 'always' or 'never'.
    // Several accounts can share one browser, so the key carries the account.
    var PREF = { products: 'tlp:save-new-products', buyers: 'tlp:save-new-buyers' };
    function prefKey(kind) {
        var wrap = $('scenario-id-container');
        return PREF[kind] + ':' + ((wrap && wrap.getAttribute('data-client')) || 'me');
    }
    // The old keys were shared by every account that used this browser.
    try { localStorage.removeItem(PREF.products); localStorage.removeItem(PREF.buyers); } catch (e) { }
    function getPref(kind) {
        try { return localStorage.getItem(prefKey(kind)) || 'ask'; } catch (e) { return 'ask'; }
    }
    function setPref(kind, value) {
        try { localStorage.setItem(prefKey(kind), value); } catch (e) { }
        renderPrefs();
    }
    function renderPrefs() {
        [['buyers', 'ci-buyer-pref', 'buyers'], ['products', 'ci-product-pref', 'products']].forEach(function (p) {
            var el = $(p[1]);
            if (!el) return;
            var pref = getPref(p[0]);
            el.hidden = pref === 'ask';
            if (pref === 'ask') return;
            el.innerHTML = '<i class="fas fa-circle-info" aria-hidden="true"></i> New ' + p[2] + ' you type in are ' +
                (pref === 'always' ? '<b>saved automatically</b>' : '<b>not saved</b>') +
                ' for next time. <button type="button" data-reset-pref="' + p[0] + '">Ask me again</button>';
        });
    }
    document.addEventListener('click', function (e) {
        var b = e.target.closest('[data-reset-pref]');
        if (b) setPref(b.getAttribute('data-reset-pref'), 'ask');
    });

    // -------------------------------------------------------------- dialog
    function ask(opts) {
        // opts: {icon, title, text, list: [{t, d, checked}], saveLabel(n)} -> Promise<{save, chosen[], never}>
        return new Promise(function (resolve) {
            var overlay = document.createElement('div');
            overlay.className = 'ci-overlay';
            var dlg = document.createElement('div');
            dlg.className = 'ci-dialog';
            dlg.setAttribute('role', 'dialog');
            dlg.setAttribute('aria-modal', 'true');
            dlg.innerHTML =
                '<div class="ci-dialog-body">' +
                '<span class="ci-dialog-icon"><i class="fas ' + opts.icon + '"></i></span>' +
                '<h3>' + esc(opts.title) + '</h3><p>' + esc(opts.text) + '</p>' +
                '<div class="ci-dialog-list">' + opts.list.map(function (x, i) {
                    return '<label class="ci-dialog-item">' + (opts.list.length > 1
                        ? '<input type="checkbox" data-i="' + i + '" checked>' : '') +
                        '<span><span class="t">' + esc(x.t) + '</span><br><span class="d">' + esc(x.d) + '</span></span></label>';
                }).join('') + '</div>' +
                '<label class="ci-dialog-never"><input type="checkbox" id="ci-never"> Don’t ask me again — remember my choice</label>' +
                '</div>' +
                '<div class="ci-dialog-foot">' +
                '<button type="button" class="ci-btn ci-btn-outline" data-act="skip">Not now</button>' +
                '<button type="button" class="ci-btn ci-btn-primary" data-act="save"><i class="fas fa-bookmark"></i> <span></span></button>' +
                '</div>';
            document.body.appendChild(overlay);
            document.body.appendChild(dlg);
            var saveBtn = dlg.querySelector('[data-act=save]');
            function chosen() {
                if (opts.list.length === 1) return [0];
                return Array.prototype.slice.call(dlg.querySelectorAll('[data-i]'))
                    .filter(function (c) { return c.checked; }).map(function (c) { return +c.getAttribute('data-i'); });
            }
            function label() {
                var n = chosen().length;
                saveBtn.querySelector('span').textContent = opts.saveLabel(n);
                saveBtn.disabled = n === 0;
            }
            function done(save) {
                var never = dlg.querySelector('#ci-never').checked;
                var result = { save: save, chosen: save ? chosen() : [], never: never };
                overlay.remove();
                dlg.remove();
                document.removeEventListener('keydown', onKey, true);
                resolve(result);
            }
            function onKey(e) {
                if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); done(false); }
            }
            dlg.addEventListener('change', label);
            dlg.querySelector('[data-act=skip]').addEventListener('click', function () { done(false); });
            saveBtn.addEventListener('click', function () { done(true); });
            document.addEventListener('keydown', onKey, true);
            label();
            setTimeout(function () { saveBtn.focus(); }, 30);
        });
    }

    // ------------------------------------------------- save new products
    function confirmSaveProducts(toSave) {
        var pref = getPref('products');
        if (pref === 'never') return Promise.resolve([]);
        if (pref === 'always') return Promise.resolve(toSave);
        return ask({
            icon: 'fa-box',
            title: toSave.length === 1 ? 'Save this new product?' : 'Save ' + toSave.length + ' new products?',
            text: (toSave.length === 1 ? 'This product isn’t in your catalogue yet.' : 'These products aren’t in your catalogue yet.') +
                ' Save them to pick them next time without retyping. The invoice is not affected either way.',
            list: toSave.map(function (it) {
                return {
                    t: it.productDescription,
                    d: [it.hsCode ? 'HS ' + it.hsCode : '', it.uoM, it.rate ? pkr(it.rate) + ' / unit' : '', it.taxRate]
                        .filter(Boolean).join(' · ')
                };
            }),
            saveLabel: function (n) { return n === 1 ? 'Save product' : 'Save ' + n + ' products'; }
        }).then(function (r) {
            if (r.never) setPref('products', r.save ? 'always' : 'never');
            return r.chosen.map(function (i) { return toSave[i]; });
        });
    }

    // ---------------------------------------------------- save new buyer
    function savedBuyers() {
        var sel = $('buyer-select');
        if (!sel) return [];
        return Array.prototype.slice.call(sel.options).map(function (o) {
            try { return o.dataset.buyer ? JSON.parse(o.dataset.buyer) : null; } catch (e) { return null; }
        }).filter(Boolean);
    }

    function offerSaveBuyer() {
        var sel = $('buyer-select');
        if (!sel || sel.value) return Promise.resolve();  // picked from the saved list
        var buyer = {
            business_name: ($('buyer-business-name').value || '').replace(/\s+/g, ' ').trim(),
            ntn_cnic: ($('buyer-ntn-cnic').value || '').trim(),
            strn: ($('buyer-strn').value || '').trim(),
            province: $('buyer-province').value,
            registration_type: $('buyer-registration-type').value,
            address: ($('buyer-address').value || '').trim(),
            buyer_code: ($('buyer-code').value || '').trim()
        };
        if (!buyer.business_name || !buyer.ntn_cnic) return Promise.resolve();
        var known = savedBuyers().some(function (b) {
            return taxId(b.ntn_cnic) === taxId(buyer.ntn_cnic) || nameKey(b.business_name) === nameKey(buyer.business_name);
        });
        if (known) return Promise.resolve();

        var pref = getPref('buyers');
        if (pref === 'never') return Promise.resolve();
        var decision = pref === 'always' ? Promise.resolve(true) : ask({
            icon: 'fa-user-plus',
            title: 'Save this new buyer?',
            text: 'This buyer isn’t in your buyer list yet. Save it to pick it next time; it gets the next buyer code automatically.',
            list: [{ t: buyer.business_name, d: ['NTN/CNIC ' + buyer.ntn_cnic, buyer.registration_type, buyer.province].filter(Boolean).join(' · ') }],
            saveLabel: function () { return 'Save buyer'; }
        }).then(function (r) {
            if (r.never) setPref('buyers', r.save ? 'always' : 'never');
            return r.save;
        });

        return decision.then(function (save) {
            if (!save) return;
            if (!buyer.address || !buyer.province || !buyer.registration_type) {
                notify('info', 'Buyer not saved', 'Address, province and registration type are needed to save a buyer.');
                return;
            }
            return fetch(url('/api/buyers'), {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(buyer)
            }).then(function (res) {
                return res.json().catch(function () { return {}; }).then(function (data) {
                    if (!res.ok) {
                        notify('info', 'Buyer not saved', data.error || 'The buyer could not be saved.');
                        return;
                    }
                    var saved = Object.assign({}, buyer, { id: data.id, buyer_code: data.buyer_code || buyer.buyer_code });
                    // Add it to the picker without refetching: fetchBuyers()
                    // would re-select the default buyer over this invoice's.
                    var opt = document.createElement('option');
                    opt.value = saved.id;
                    opt.textContent = saved.business_name;
                    opt.dataset.buyer = JSON.stringify(saved);
                    sel.appendChild(opt);
                    sel.value = saved.id;
                    if (saved.buyer_code) $('buyer-code').value = saved.buyer_code;
                    try {
                        if (typeof state !== 'undefined' && state.buyerData) {
                            state.buyerData.id = saved.id;
                            state.buyerData.buyerCode = saved.buyer_code;
                        }
                    } catch (e) { }
                    syncBuyerCombo();
                    notify('success', 'Buyer saved', saved.business_name + (saved.buyer_code ? ' saved as ' + saved.buyer_code : ' saved to your buyer list'));
                });
            }).catch(function () { notify('error', 'Buyer not saved', 'Could not reach the server.'); });
        });
    }

    window.TLPInvoiceUX = { confirmSaveProducts: confirmSaveProducts, offerSaveBuyer: offerSaveBuyer };

    // ------------------------------------------------------ searchable picker
    // cfg: {input, list, clear, items(), render(item, term), match(item, term),
    //       pick(item), newLabel(term), onNew(term), empty}
    function combo(cfg) {
        var input = cfg.input, list = cfg.list, active = -1, shown = [];
        function close() {
            list.hidden = true;
            input.setAttribute('aria-expanded', 'false');
            active = -1;
        }
        function open() {
            var term = input.value.trim();
            var all = cfg.items();
            shown = all.filter(function (x) { return !term || cfg.match(x, term.toLowerCase()); }).slice(0, 60);
            var html = shown.map(function (x, i) {
                return '<button type="button" class="ci-combo-item' + (cfg.isCurrent && cfg.isCurrent(x) ? ' is-current' : '') +
                    '" role="option" data-i="' + i + '">' + cfg.render(x, term) + '</button>';
            }).join('');
            if (!shown.length) {
                html = '<div class="ci-combo-empty">' + esc(all.length ? 'No matches for “' + term + '”.' : cfg.empty) + '</div>';
            }
            if (term && cfg.onNew && !shown.some(function (x) { return nameKey(cfg.label(x)) === nameKey(term); })) {
                html += '<div class="ci-combo-new"><button type="button" class="ci-combo-item" role="option" data-new="1">' +
                    '<span class="ci-combo-avatar"><i class="fas fa-plus"></i></span><span class="ci-combo-main"><span class="ci-combo-name">' +
                    esc(cfg.newLabel(term)) + '</span><span class="ci-combo-meta">Not saved yet — you’ll be asked whether to save it</span></span></button></div>';
            }
            list.innerHTML = html;
            list.hidden = false;
            input.setAttribute('aria-expanded', 'true');
            active = -1;
        }
        function move(d) {
            var btns = list.querySelectorAll('.ci-combo-item');
            if (!btns.length) return;
            if (active >= 0 && btns[active]) btns[active].classList.remove('is-active');
            active = (active + d + btns.length) % btns.length;
            btns[active].classList.add('is-active');
            btns[active].scrollIntoView({ block: 'nearest' });
        }
        function choose(btn) {
            if (!btn) return;
            if (btn.hasAttribute('data-new')) {
                var term = input.value.trim();
                close();
                cfg.onNew(term);
            } else {
                var item = shown[+btn.getAttribute('data-i')];
                close();
                cfg.pick(item);
            }
            refreshClear();
        }
        function refreshClear() { if (cfg.clear) cfg.clear.hidden = !input.value; }

        input.addEventListener('focus', open);
        input.addEventListener('click', function () { if (list.hidden) open(); });
        input.addEventListener('input', function () { open(); refreshClear(); });
        input.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowDown') { e.preventDefault(); if (list.hidden) open(); move(1); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
            else if (e.key === 'Enter') {
                if (list.hidden) return;
                e.preventDefault();
                var btns = list.querySelectorAll('.ci-combo-item');
                choose(btns[active >= 0 ? active : 0]);
            } else if (e.key === 'Escape') { close(); }
            else if (e.key === 'Tab') { close(); }
        });
        list.addEventListener('mousedown', function (e) { e.preventDefault(); });
        list.addEventListener('click', function (e) { choose(e.target.closest('.ci-combo-item')); });
        document.addEventListener('click', function (e) {
            if (!list.hidden && !cfg.root.contains(e.target)) close();
        });
        if (cfg.clear) {
            cfg.clear.addEventListener('click', function () {
                input.value = '';
                refreshClear();
                if (cfg.onClear) cfg.onClear();
                input.focus();
            });
        }
        return { refresh: refreshClear, close: close };
    }

    // ---------------------------------------------------------- buyer picker
    var buyerCombo = null;
    function syncBuyerCombo() {
        var sel = $('buyer-select'), input = $('buyer-combo-input');
        if (!sel || !input) return;
        var opt = sel.options[sel.selectedIndex];
        input.value = sel.value && opt ? opt.textContent : '';
        if (buyerCombo) buyerCombo.refresh();
    }

    function initBuyerPicker() {
        var input = $('buyer-combo-input');
        if (!input) return;
        buyerCombo = combo({
            root: $('buyer-combo'), input: input, list: $('buyer-combo-list'), clear: $('buyer-combo-clear'),
            empty: 'No saved buyers yet — type a name to enter a new buyer.',
            items: savedBuyers,
            label: function (b) { return b.business_name; },
            isCurrent: function (b) { return String(b.id) === $('buyer-select').value; },
            match: function (b, t) {
                var digits = t.replace(/[^0-9a-z]/g, '');
                return nameKey(b.business_name).indexOf(t) >= 0 || String(b.buyer_code || '').toLowerCase().indexOf(t) >= 0 ||
                    (digits && taxId(b.ntn_cnic).toLowerCase().indexOf(digits) >= 0);
            },
            render: function (b, term) {
                var reg = String(b.registration_type || '');
                return '<span class="ci-combo-avatar"><i class="fas fa-building-user"></i></span>' +
                    '<span class="ci-combo-main"><span class="ci-combo-name">' + highlight(b.business_name, term) + '</span>' +
                    '<span class="ci-combo-meta">' + [b.buyer_code ? highlight(b.buyer_code, term) : '', b.ntn_cnic ? 'NTN/CNIC ' + highlight(b.ntn_cnic, term) : '', esc(b.province || '')]
                        .filter(Boolean).join(' · ') + '</span></span>' +
                    (reg ? '<span class="ci-combo-side" style="font-weight:400;color:var(--tlp-muted-2)">' + esc(reg) + '</span>' : '');
            },
            pick: function (b) {
                var sel = $('buyer-select');
                sel.value = String(b.id);
                sel.dispatchEvent(new Event('change', { bubbles: true }));  // existing handler fills the form
                syncBuyerCombo();
            },
            newLabel: function (t) { return 'Enter “' + t + '” as a new buyer'; },
            onNew: function (t) {
                var sel = $('buyer-select');
                sel.value = '';
                ['buyer-ntn-cnic', 'buyer-strn', 'buyer-code', 'buyer-address'].forEach(function (id) { if ($(id)) $(id).value = ''; });
                $('buyer-business-name').value = t;
                $('buyer-combo-input').value = t;
                if (buyerCombo) buyerCombo.refresh();
                $('buyer-ntn-cnic').focus();
            },
            onClear: function () { $('buyer-select').value = ''; }
        });
        after('populateBuyerForm', syncBuyerCombo);
        new MutationObserver(syncBuyerCombo).observe($('buyer-select'), { childList: true });
        syncBuyerCombo();
    }

    // -------------------------------------------------------- product picker
    var productCombo = null;
    function initProductPicker() {
        var input = $('product-combo-input');
        if (!input) return;
        productCombo = combo({
            root: $('product-combo'), input: input, list: $('product-combo-list'), clear: $('product-combo-clear'),
            empty: 'No saved products yet — type a name to add one.',
            items: catalogue,
            label: function (p) { return p.description; },
            match: function (p, t) {
                return nameKey(p.description).indexOf(t) >= 0 || String(p.product_code || '').toLowerCase().indexOf(t) >= 0 ||
                    String(p.hs_code || '').toLowerCase().indexOf(t) >= 0;
            },
            render: function (p, term) {
                return '<span class="ci-combo-avatar"><i class="fas fa-box"></i></span>' +
                    '<span class="ci-combo-main"><span class="ci-combo-name">' + highlight(p.description, term) + '</span>' +
                    '<span class="ci-combo-meta">' + [p.product_code ? highlight(p.product_code, term) : '', p.hs_code ? 'HS ' + highlight(p.hs_code, term) : '',
                        esc(p.uom || ''), p.default_tax_rate !== undefined && p.default_tax_rate !== null ? esc(p.default_tax_rate) + '% tax' : '']
                        .filter(Boolean).join(' · ') + '</span></span>' +
                    (Number(p.rate) ? '<span class="ci-combo-side">' + pkr(p.rate) + '</span>' : '');
            },
            pick: function (p) {
                if (typeof window.selectProductRow === 'function') window.selectProductRow(p);
                input.value = p.description || '';
                productCombo.refresh();
                updateAdvanced(true);
                var qty = $('product-quantity');
                if (qty && !qty.value) qty.focus();
            },
            newLabel: function (t) { return 'Add “' + t + '” as a new product'; },
            onNew: function (t) {
                $('product-description').value = t;
                input.value = t;
                productCombo.refresh();
                var hs = $('product-hs-code');
                if (hs) hs.focus();
            }
        });
        // After "Add Item" the form is cleared; clear the picker with it.
        var addBtn = $('add-product-item-btn');
        if (addBtn) addBtn.addEventListener('click', function () {
            setTimeout(function () {
                if (!$('product-description').value) { input.value = ''; productCombo.refresh(); }
                updateAdvanced(false);
            }, 0);
        });
    }

    // ------------------------------------------------------- profile cards
    function profiles() {
        var sel = $('business-profile-select');
        if (!sel) return [];
        return Array.prototype.slice.call(sel.options).map(function (o) {
            try { return o.dataset.profile ? JSON.parse(o.dataset.profile) : null; } catch (e) { return null; }
        }).filter(Boolean);
    }
    function renderProfileCards() {
        var host = $('ci-profile-cards'), sel = $('business-profile-select');
        if (!host || !sel) return;
        var list = profiles();
        var useCards = list.length > 0 && list.length <= 8;
        host.hidden = !useCards;
        var row = $('ci-profile-select-row');
        if (row) row.hidden = useCards;
        if (!useCards) return;
        host.innerHTML = list.map(function (p) {
            var on = String(p.id) === sel.value;
            return '<button type="button" class="ci-profile-card' + (on ? ' is-selected' : '') + '" data-profile="' + esc(p.id) + '" aria-pressed="' + on + '">' +
                '<span class="ci-profile-top"><span class="ci-profile-name">' + esc(p.business_name) + '</span>' +
                (p.is_default ? '<span class="ci-profile-badge">Default</span>' : '') + '</span>' +
                '<span class="ci-profile-addr" title="' + esc(p.address) + '">' + esc(p.address || '') + '</span>' +
                '<span class="ci-profile-ids"><span>NTN <b>' + esc(p.ntn_cnic || '–') + '</b></span><span>STRN <b>' + esc(p.strn || '–') + '</b></span></span>' +
                '</button>';
        }).join('') +
            '<button type="button" class="ci-profile-card ci-profile-add" data-profile-manage><i class="fas fa-plus"></i>Add or manage profiles</button>';
    }
    function initProfileCards() {
        var host = $('ci-profile-cards'), sel = $('business-profile-select');
        if (!host || !sel) return;
        host.addEventListener('click', function (e) {
            var card = e.target.closest('[data-profile]');
            if (card) {
                sel.value = card.getAttribute('data-profile');
                sel.dispatchEvent(new Event('change', { bubbles: true }));  // existing handler fills the form
                renderProfileCards();
                return;
            }
            if (e.target.closest('[data-profile-manage]')) {
                if (typeof window.openBusinessProfileModal === 'function') window.openBusinessProfileModal();
                else if ($('add-business-profile-btn')) $('add-business-profile-btn').click();
            }
        });
        sel.addEventListener('change', renderProfileCards);
        new MutationObserver(renderProfileCards).observe(sel, { childList: true });
        after('populateSellerForm', renderProfileCards);
        renderProfileCards();
    }

    // ---------------------------------------------------- advanced tax fields
    var ADV = [['product-sro-schedule-no', ''], ['product-sro-item-serial-no', ''], ['product-further-tax', '0'],
               ['product-extra-tax', ''], ['product-st-withheld', '0'], ['product-fed-payable', '0'],
               ['product-discount', '0'], ['product-fixed-value', '0']];
    function advancedInUse() {
        return ADV.filter(function (f) {
            var el = $(f[0]);
            if (!el) return false;
            var v = String(el.value || '').trim();
            return v !== '' && v !== f[1] && !(f[1] === '0' && Number(v) === 0);
        }).length;
    }
    function needsSro() {
        var st = $('product-sale-type');
        var v = st ? String(st.value || '').toLowerCase() : '';
        return !!v && v.indexOf('standard rate') < 0;
    }
    // Opens itself when a field holds a value or the sale type needs SRO
    // details; never closes itself, so it doesn't jump shut under the user.
    function updateAdvanced(mayOpen) {
        var det = $('ci-advanced'), badge = $('ci-advanced-badge');
        if (!det) return;
        var n = advancedInUse();
        if (badge) {
            badge.hidden = !n;
            badge.textContent = n + ' in use';
        }
        if (mayOpen !== false && (n || needsSro())) det.open = true;
    }
    function initAdvanced() {
        var det = $('ci-advanced');
        if (!det) return;
        det.addEventListener('input', function () { updateAdvanced(false); });
        var st = $('product-sale-type');
        if (st) st.addEventListener('change', function () { updateAdvanced(true); });
        after('selectProductRow', function () { updateAdvanced(true); });
        new MutationObserver(function () { if (currentStep() === 4) updateAdvanced(true); })
            .observe($('step-4'), { attributes: true, attributeFilter: ['class'] });
        updateAdvanced(true);
    }

    // --------------------------------------------------------- summary rail
    var STEPS = [[1, 'Seller', 'fa-building'], [2, 'Invoice details', 'fa-file-lines'], [3, 'Buyer', 'fa-user'],
                 [4, 'Products', 'fa-box'], [5, 'Review', 'fa-circle-check']];
    function text(id, value) { var el = $(id); if (el) { el.textContent = value; el.title = value; } }
    function updateSummary() {
        var sub = num(($('total-value-excl') || {}).textContent);
        var tax = num(($('total-tax-amount') || {}).textContent);
        var grand = num(($('grand-total') || {}).textContent);
        var n = items().length;
        text('ci-sum-total', pkr(grand));
        text('ci-sum-items', n + ' item' + (n === 1 ? '' : 's'));
        text('ci-sum-seller', ($('seller-business-name') || {}).value || '–');
        text('ci-sum-buyer', ($('buyer-business-name') || {}).value || '–');
        text('ci-sum-ref', ($('invoice-ref-no') || {}).value || '–');
        var d = ($('invoice-date') || {}).value;
        text('ci-sum-date', d ? new Date(d + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '–');
        text('ci-sum-subtotal', pkr(sub));
        text('ci-sum-tax', pkr(tax));
        text('ci-sum-grand', pkr(grand));
        var cur = currentStep();
        var host = $('ci-sum-steps');
        if (host) {
            host.innerHTML = STEPS.map(function (s) {
                var done = s[0] < cur, on = s[0] === cur;
                return '<button type="button" class="ci-summary-step' + (done ? ' is-done' : '') + (on ? ' is-current' : '') + '"' +
                    (done ? ' data-goto="' + s[0] + '"' : ' tabindex="-1"') + '><i class="fas ' + (done ? 'fa-circle-check' : s[2]) + '"></i>' +
                    esc(s[1]) + '</button>';
            }).join('');
        }
    }
    var scheduleSummary = (function () {
        var t = null;
        return function () { clearTimeout(t); t = setTimeout(updateSummary, 60); };
    })();
    function initSummary() {
        document.addEventListener('input', scheduleSummary, true);
        document.addEventListener('change', scheduleSummary, true);
        var watch = ['product-items-table', 'grand-total', 'total-value-excl', 'total-tax-amount'];
        var mo = new MutationObserver(scheduleSummary);
        watch.forEach(function (id) { if ($(id)) mo.observe($(id), { childList: true, subtree: true, characterData: true }); });
        for (var i = 1; i <= 5; i++) if ($('step-' + i)) mo.observe($('step-' + i), { attributes: true, attributeFilter: ['class'] });
        after('populateSellerForm', scheduleSummary);
        after('populateBuyerForm', scheduleSummary);
        var host = $('ci-sum-steps');
        if (host) host.addEventListener('click', function (e) {
            var b = e.target.closest('[data-goto]');
            if (b && typeof window.goToStep === 'function') window.goToStep(+b.getAttribute('data-goto'));
        });
        updateSummary();
    }

    // -------------------------------------------------- default scenario ID
    // Sandbox asks for a Scenario ID on every invoice and most accounts always
    // pick the same one. "Use as my default" remembers it (per account, in
    // this browser) and pre-selects it whenever the field is empty. A draft
    // or a restored in-progress invoice sets its own scenario and wins.
    function initScenarioDefault() {
        var sel = $('scenario-id'), box = $('scenario-default-toggle'), wrap = $('scenario-id-container');
        if (!sel || !box || !wrap) return;
        var key = 'tlp:default-scenario:' + (wrap.getAttribute('data-client') || 'me');
        function stored() { try { return localStorage.getItem(key) || ''; } catch (e) { return ''; } }
        function paint() {
            var def = stored();
            box.checked = !!def && sel.value === def;
            var chosen = sel.value;
            $('scenario-default-text').innerHTML = box.checked
                ? '<b>' + esc(chosen) + '</b> is my default'
                : chosen ? 'Make <b>' + esc(chosen) + '</b> my default'
                : (def ? 'Default: <b>' + esc(def) + '</b>' : 'Set as my default');
            box.parentNode.title = 'Pre-select this Scenario ID on every new invoice (you can still change it per invoice)';
        }
        function applyDefault() {
            var def = stored();
            if (def && !sel.value && Array.prototype.some.call(sel.options, function (o) { return o.value === def; })) {
                sel.value = def;
                sel.dispatchEvent(new Event('change', { bubbles: true }));  // existing handlers sync sale type etc.
            }
            paint();
        }
        box.addEventListener('change', function () {
            if (box.checked) {
                if (!sel.value) {
                    box.checked = false;
                    notify('info', 'Choose a scenario first', 'Pick the Scenario ID you want to use by default.');
                    sel.focus();
                    return;
                }
                try { localStorage.setItem(key, sel.value); } catch (e) { }
                notify('success', 'Default saved', sel.value + ' will be pre-selected on new invoices.');
            } else {
                try { localStorage.removeItem(key); } catch (e) { }
            }
            paint();
        });
        sel.addEventListener('change', paint);
        after('populateScenarioSelect', function () { setTimeout(applyDefault, 0); });
        applyDefault();
    }

    // ------------------------------------------------------------------ init
    function init() {
        [initSummary, initProfileCards, initBuyerPicker, initProductPicker, initAdvanced, initScenarioDefault, renderPrefs].forEach(function (fn) {
            try { fn(); } catch (e) { console.error('[ux] init', fn.name, e); }
        });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
