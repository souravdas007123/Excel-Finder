/* Excel Finder UI: htmx ke upar chhoti si JavaScript (theme, toast, modal, copy, recent searches, update). */
(function () {
    "use strict";
    var doc = document, body = doc.body;
    var $ = function (sel, root) { return (root || doc).querySelector(sel); };
    // Sidebar, toast aur modal page badalne par bhi wahin rehte hain; phir bhi har baar dobara dhundhte hain (safe)

    /* ---------- theme ---------- */
    function setTheme(name) {
        doc.documentElement.setAttribute("data-theme", name);
        try { localStorage.setItem("ef-theme", name); } catch (e) { /* private mode */ }
    }

    /* ---------- toast ---------- */
    function toast(message, kind) {
        var box = $("#toasts");
        if (!box || !message) { return; }
        var el = doc.createElement("div");
        el.className = "toast toast-" + (kind || "info");
        el.setAttribute("role", "status");
        el.textContent = message;               // textContent: XSS safe
        box.appendChild(el);
    }
    function dismissToast(el) {
        el.classList.add("leaving");
        setTimeout(function () { el.remove(); }, 260);
    }
    new MutationObserver(function (list) {      // jo bhi toast judta hai (OOB ya JS se) 5.5 second baad apne aap jata hai
        list.forEach(function (m) {
            m.addedNodes.forEach(function (n) {
                if (n.classList && n.classList.contains("toast") && !n.hasAttribute("data-armed")) {
                    n.setAttribute("data-armed", "1");
                    setTimeout(function () { dismissToast(n); }, 5500);
                }
            });
        });
    }).observe(body, { childList: true, subtree: true });
    doc.addEventListener("htmx:afterSettle", function () {
        doc.querySelectorAll("#toasts .toast:not([data-armed])").forEach(function (t) {
            t.setAttribute("data-armed", "1");
            setTimeout(function () { dismissToast(t); }, 5500);
        });
    });
    window.addEventListener("toast", function (e) { toast(e.detail && e.detail.message, e.detail && e.detail.kind); });

    /* ---------- modal ---------- */
    function openModal() { var m = $("#modal"); if (m && !m.open) { m.showModal(); } }
    function closeModal() { var m = $("#modal"); if (m && m.open) { m.close(); } }
    window.addEventListener("closeModal", closeModal);
    window.addEventListener("resetBrowserData", function () { try { localStorage.removeItem("excelFinder.bulkHistory.v1"); } catch (e) { /* private mode */ } });
    doc.addEventListener("click", function (e) { if (e.target && e.target.id === "modal") { closeModal(); } });   // bahar click
    doc.addEventListener("htmx:afterSwap", function (e) {
        if (e.detail.target && e.detail.target.id === "modal-body") { openModal(); }
    });

    /* ---------- loading bar (sirf page badalte waqt) ---------- */
    doc.addEventListener("htmx:beforeRequest", function (e) { if (e.detail.boosted) { body.classList.add("loading"); } });
    ["htmx:afterRequest", "htmx:sendError", "htmx:responseError"].forEach(function (name) {
        doc.addEventListener(name, function () { body.classList.remove("loading"); });
    });
    doc.addEventListener("htmx:afterSettle", function () { body.classList.remove("nav-open"); updateCounter(); renderHistory(); });
    doc.addEventListener("htmx:responseError", function (e) {
        if (e.detail.xhr && e.detail.xhr.status >= 500) { toast("Something went wrong. Please try again.", "bad"); }
    });
    doc.addEventListener("htmx:sendError", function () { toast("Could not reach the app. Is it still running?", "bad"); });

    /* ---------- copy / download / toggles ---------- */
    function copyText(text) {
        if (navigator.clipboard && window.isSecureContext) { return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return legacyCopy(text); }); }
        return Promise.resolve(legacyCopy(text));
    }
    function legacyCopy(text) {
        try {
            var ta = doc.createElement("textarea");
            ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
            doc.body.appendChild(ta); ta.select();
            var ok = doc.execCommand("copy");
            ta.remove();
            return ok;
        } catch (e) { return false; }
    }
    function flash(btn, text) {
        if (btn._label === undefined) { btn._label = btn.innerHTML; }
        btn.textContent = text;
        clearTimeout(btn._t);
        btn._t = setTimeout(function () { btn.innerHTML = btn._label; btn._label = undefined; }, 1500);
    }

    doc.addEventListener("click", function (e) {
        var t = e.target.closest("[data-copy], [data-copy-from], [data-download], [data-toggle], [data-close-modal], [data-menu], [data-theme-toggle], [data-pick-file], [data-use-folder], [data-history-open], [data-history-del], [data-history-clear], .toast");
        if (!t) {
            var panel = $("#history-menu");
            if (panel && !panel.hidden && !e.target.closest(".history-wrap")) { panel.hidden = true; }
            return;
        }
        if (t.classList.contains("toast")) { dismissToast(t); return; }
        if (t.hasAttribute("data-theme-toggle")) { setTheme(doc.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light"); }
        else if (t.hasAttribute("data-menu")) { body.classList.toggle("nav-open"); }
        else if (t.hasAttribute("data-close-modal")) { closeModal(); }
        else if (t.hasAttribute("data-pick-file")) { var f = $(t.getAttribute("data-pick-file")); if (f) { f.click(); } }
        else if (t.hasAttribute("data-toggle")) { var box = doc.getElementById(t.getAttribute("data-toggle")); if (box) { box.hidden = !box.hidden; t.setAttribute("aria-expanded", String(!box.hidden)); } }
        else if (t.hasAttribute("data-copy") || t.hasAttribute("data-copy-from")) {
            var text = t.hasAttribute("data-copy") ? t.getAttribute("data-copy") : ($(t.getAttribute("data-copy-from")) || {}).value || "";
            copyText(text).then(function (ok) { flash(t, ok ? "Copied ✓" : "Copy failed"); });
        }
        else if (t.hasAttribute("data-download")) {
            var src = $(t.getAttribute("data-download")), blob = new Blob([src ? src.value || src.textContent : ""], { type: "text/plain;charset=utf-8" });
            var a = doc.createElement("a");
            a.href = URL.createObjectURL(blob); a.download = t.getAttribute("data-filename") || "numbers.txt";
            doc.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
        }
        else if (t.hasAttribute("data-use-folder")) {
            var input = $("#custom-path");
            if (input) { input.value = t.getAttribute("data-use-folder"); }
            closeModal();
        }
        else if (t.hasAttribute("data-history-open")) { openHistory(+t.getAttribute("data-history-open")); }
        else if (t.hasAttribute("data-history-del")) { deleteHistory(+t.getAttribute("data-history-del")); }
        else if (t.hasAttribute("data-history-clear")) { writeHistory([]); renderHistory(); }
        if (t.hasAttribute("data-history-toggle")) { /* neeche */ }
    });
    doc.addEventListener("click", function (e) {
        var btn = e.target.closest("#history-btn");
        if (btn) { var p = $("#history-menu"); if (p) { renderHistory(); p.hidden = !p.hidden; } }
    });
    doc.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { var p = $("#history-menu"); if (p) { p.hidden = true; } }
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && e.target.id === "numbers") {
            var go = $("#search-btn"); if (go) { go.click(); }
        }
    });

    /* "Specific folder" chunne par path box dikhao */
    doc.addEventListener("change", function (e) {
        if (e.target.id === "location") {
            var box = $("#custom-box");
            if (box) { box.hidden = e.target.value !== "__custom__"; }
        }
    });

    /* ---------- numbers box: ginti ---------- */
    function updateCounter() {
        var box = $("#numbers"), out = $("#num-counter");
        if (!box || !out) { return; }
        var min = +box.getAttribute("data-min") || 5, seen = {}, count = 0;
        box.value.split(/[,;\n]+/).forEach(function (p) {
            var n = p.replace(/\D/g, "");
            if (n.length >= min && !seen[n]) { seen[n] = 1; count++; }
        });
        out.textContent = count.toLocaleString() + " number" + (count === 1 ? "" : "s") + " detected · shorter than " + min + " digits are ignored · Ctrl+Enter to search";
    }
    doc.addEventListener("input", function (e) { if (e.target.id === "numbers") { updateCounter(); } });

    /* ---------- recent searches (is browser me, last 10) ---------- */
    var HKEY = "excelFinder.bulkHistory.v1", HMAX = 10, HCHARS = 200000;
    function readHistory() {
        try { var h = JSON.parse(localStorage.getItem(HKEY) || "[]"); return Array.isArray(h) ? h.filter(function (x) { return x && typeof x.input === "string"; }) : []; }
        catch (e) { return []; }
    }
    function writeHistory(list) { try { localStorage.setItem(HKEY, JSON.stringify(list)); } catch (e) { /* storage full */ } }
    function uniqueNumbers(text, min) {
        var seen = {}, out = [];
        text.split(/[,;\n]+/).forEach(function (p) { var n = p.replace(/\D/g, ""); if (n.length >= min && !seen[n]) { seen[n] = 1; out.push(n); } });
        return out;
    }
    function addHistory(text, last10) {
        var box = $("#numbers"), min = box ? +box.getAttribute("data-min") || 5 : 5, nums = uniqueNumbers(text, min);
        if (!nums.length || text.length > HCHARS) { return; }
        var sig = nums.join(","), list = readHistory().filter(function (x) { return x.sig !== sig; });
        list.unshift({ sig: sig, input: text, count: nums.length, preview: nums.slice(0, 3).join(", "), ts: Date.now(), last10: last10 });
        writeHistory(list.slice(0, HMAX));
    }
    function ago(ts) {
        var s = Math.max(0, Math.round((Date.now() - ts) / 1000));
        if (s < 60) { return "just now"; }
        if (s < 3600) { return Math.round(s / 60) + " min ago"; }
        if (s < 86400) { return Math.round(s / 3600) + " h ago"; }
        return Math.round(s / 86400) + " d ago";
    }
    function renderHistory() {
        var menu = $("#history-menu");
        if (!menu) { return; }
        var list = readHistory(), btn = $("#history-btn");
        if (btn) { btn.querySelector(".lbl").textContent = list.length ? "Recent (" + list.length + ")" : "Recent"; }
        menu.replaceChildren();
        if (!list.length) {
            var empty = doc.createElement("div"); empty.className = "hint"; empty.style.padding = "14px";
            empty.textContent = "No searches yet. Your last 10 searches will appear here.";
            menu.appendChild(empty); return;
        }
        list.forEach(function (x, i) {
            var row = doc.createElement("div"); row.className = "hist-row";
            var open = doc.createElement("button"); open.type = "button"; open.className = "hist-open"; open.setAttribute("data-history-open", i);
            var a = doc.createElement("b"); a.textContent = x.preview + (x.count > 3 ? " …" : "");
            var b = doc.createElement("span"); b.textContent = x.count.toLocaleString() + " number" + (x.count === 1 ? "" : "s") + " · " + ago(x.ts);
            open.append(a, b);
            var del = doc.createElement("button"); del.type = "button"; del.className = "hist-del"; del.setAttribute("data-history-del", i);
            del.setAttribute("aria-label", "Remove from history"); del.textContent = "✕";
            row.append(open, del); menu.appendChild(row);
        });
        var clear = doc.createElement("button"); clear.type = "button"; clear.className = "hist-clear"; clear.setAttribute("data-history-clear", "1"); clear.textContent = "Clear history";
        menu.appendChild(clear);
    }
    function openHistory(i) {
        var x = readHistory()[i], box = $("#numbers");
        if (!x || !box) { return; }
        box.value = x.input;
        var c = $("#last10"); if (c) { c.checked = x.last10 !== "0"; }
        updateCounter();
        $("#history-menu").hidden = true;
        var go = $("#search-btn"); if (go) { go.click(); }
    }
    function deleteHistory(i) { var l = readHistory(); l.splice(i, 1); writeHistory(l); renderHistory(); }
    doc.addEventListener("htmx:beforeRequest", function (e) {
        if (e.detail.elt && e.detail.elt.id === "search-btn") {
            var box = $("#numbers"), c = $("#last10");
            if (box) { addHistory(box.value, c && !c.checked ? "0" : "1"); }
        }
    });

    /* ---------- update: 'Update now' (download + install) ---------- */
    doc.addEventListener("click", function (e) {
        var button = e.target.closest("#ub-install");
        if (!button) { return; }
        var box = $("#ub-progress"), csrf = (body.getAttribute("hx-headers") || "").match(/"X-CSRFToken":\s*"([^"]+)"/), down = false;
        function show(text) { if (box) { box.textContent = text; } }
        function poll() {
            fetch(button.dataset.status, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
                if (down) { location.reload(); return; }                      // app band hokar naya khul gaya
                show(d.message || "");
                if (d.state === "downloading" || d.state === "installing") { setTimeout(poll, 1000); }
                else if (d.state === "error") { button.disabled = false; }
            }).catch(function () {
                down = true;                                                   // installer app band kar chuka: wapas aane tak ruko
                show("Installing the new version. This page will refresh by itself...");
                setTimeout(poll, 2000);
            });
        }
        button.disabled = true;
        show("Starting...");
        fetch(button.dataset.install, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf ? csrf[1] : "" } })
            .then(function (r) { return r.json(); })
            .then(function (d) { show(d.message || ""); if (d.ok) { poll(); } else { button.disabled = false; } })
            .catch(function () { show("Could not start the update."); button.disabled = false; });
    });
    if ($("#ub-install") && $("#ub-install").disabled) { $("#ub-install").disabled = false; $("#ub-install").click(); }   // update pehle se chal raha ho

    updateCounter();
    renderHistory();
})();
