(function () {
  "use strict";

  /* ------------------------------------------------------------------ helpers */
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function fmtTime(ts) {
    if (!ts) return "—";
    var d = new Date(ts * 1000);
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }
  function ago(ts) {
    if (!ts) return "never";
    var s = Math.round(Date.now() / 1000 - ts);
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " d ago";
  }
  function until(ts) {
    var s = Math.round(ts - Date.now() / 1000);
    if (s <= 0) return "now";
    if (s < 60) return "in " + s + " s";
    return "in " + Math.round(s / 60) + " min";
  }

  var ICON = {
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
    alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
    users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>',
    mail: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M22 6l-10 7L2 6"/></svg>',
    inbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1L2 12v6a2 2 0 002 2h16a2 2 0 002-2v-6l-3.5-6.9A2 2 0 0016.8 4H7.2a2 2 0 00-1.7 1.1z"/></svg>',
    activity: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
    shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>',
    more: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>',
    upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  };
  function emptyState(icon, title, text, actions) {
    return '<div class="empty"><div class="icon">' + ICON[icon] + '</div><span class="serif">' + esc(title) + "</span>" + (text || "") + (actions ? '<div class="btn-row">' + actions + "</div>" : "") + "</div>";
  }
  function closeBtn() { return '<button class="btn ghost close-x" id="x" aria-label="Close">' + ICON.x + "</button>"; }
  function busy(btn, on) { if (!btn) return; btn.disabled = on; btn.classList.toggle("busy", on); }
  var SKELETON = '<div class="skeleton"><div class="sk title"></div><div class="sk line"></div><div class="sk-row"><div class="sk"></div><div class="sk"></div><div class="sk"></div><div class="sk"></div></div><div class="sk block"></div><div class="sk block"></div></div>';
  function initials(s) { return String(s || "?").split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map(function (w) { return w[0].toUpperCase(); }).join(""); }

  var OFFLINE = "Can't reach the server right now. Check your connection and try again in a moment.";
  function offline() { throw new Error(OFFLINE); }
  function api(method, path, body, isForm) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      if (isForm) opts.body = body;
      else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
    }
    return fetch("/api" + path, opts).catch(offline).then(function (r) {
      return r.text().then(function (t) {
        var data = t ? JSON.parse(t) : null;
        if (r.status === 401 && path !== "/auth/login" && authState && authState.users_exist) { showLogin(); }
        if (!r.ok) {
          var msg = data && data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : r.statusText;
          throw new Error(msg);
        }
        return data;
      });
    });
  }

  function toast(msg, isError) {
    var el = document.createElement("div");
    el.className = "toast" + (isError ? " error" : "");
    el.setAttribute("role", isError ? "alert" : "status");
    el.innerHTML = (isError ? ICON.alert : ICON.check) + "<span></span>";
    el.lastChild.textContent = msg;
    $("#toasts").appendChild(el);
    setTimeout(function () { el.classList.add("out"); setTimeout(function () { el.remove(); }, 220); }, isError ? 6000 : 3200);
  }
  function fail(e) { toast(e.message || String(e), true); }

  function openOverlay(html, kind) {
    var cls = kind === "drawer" ? "backdrop" : "backdrop center";
    var inner = kind === "drawer" ? '<aside class="drawer">' + html + "</aside>" : '<div class="modal' + (kind === "wide" ? " wide" : "") + '">' + html + "</div>";
    $("#overlay").innerHTML = '<div class="' + cls + '" id="backdrop">' + inner + "</div>";
    $("#backdrop").addEventListener("mousedown", function (e) { if (e.target.id === "backdrop") closeOverlay(); });
    return $("#backdrop");
  }
  function closeOverlay() { $("#overlay").innerHTML = ""; }
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") { closeOverlay(); $$(".menu.open").forEach(function (m) { m.classList.remove("open"); }); } });
  document.addEventListener("click", function () { $$(".menu.open").forEach(function (m) { m.classList.remove("open"); }); });

  var STATUS_LABEL = { new: "Not started", drafted: "Drafted", sent: "Sent", replied: "Replied", adopted: "Interested", declined: "Not interested", unsubscribed: "Unsubscribed", bounced: "Bounced" };
  var TYPE_LABEL = { doctor: "Doctor", hospital: "Hospital", company: "Company", partner: "Product partner", brand: "Brand", professional: "Health professional" };
  var MARKET_TYPES = ["brand", "professional"];
  function isMarket(t) { return MARKET_TYPES.indexOf(t) >= 0; }
  function categoryLabel(c) {
    var cats = (meta.categories || {})[c.type] || [];
    var hit = cats.filter(function (x) { return x.key === c.category; })[0];
    return hit ? hit.label : "";
  }
  function categoryOptions(type, selected) {
    return '<option value="">— not set —</option>' + ((meta.categories || {})[type] || []).map(function (x) { return '<option value="' + x.key + '"' + (x.key === selected ? " selected" : "") + ">" + esc(x.label) + "</option>"; }).join("");
  }
  var INTENT_LABEL = { interested: "Interested", question: "Has a question", not_now: "Not now", not_interested: "Not interested", wrong_person: "Wrong person", other: "Other" };
  var INTENT_CLASS = { interested: "adopted", question: "info", not_now: "warn", not_interested: "declined", wrong_person: "warn", other: "" };
  function intentBadge(i) { return i ? badge(INTENT_CLASS[i] || "", INTENT_LABEL[i] || i) : ""; }
  function kindLabel(k) { return k === "followup" ? "Follow-up" : k === "reply" ? "Response to their reply" : "Email"; }
  var MSG_LABEL = { draft: "Needs review", approved: "Approved — queued", sending: "Sending", sent: "Sent", failed: "Failed", discarded: "Discarded" };
  function badge(cls, text) { return '<span class="badge ' + esc(cls) + '">' + esc(text) + "</span>"; }
  function statusBadge(s) { return badge(s, STATUS_LABEL[s] || s); }

  var meta = { specialties: [] };
  var settingsCache = null;
  function loadSettings() { return api("GET", "/settings").then(function (s) { settingsCache = s; return s; }); }

  /* ------------------------------------------------------------------ router */
  var pages = {};
  var current = null;
  var refreshTimer = null;

  function route() {
    var name = (location.hash.replace(/^#\/?/, "") || "dashboard").split("?")[0];
    if (!pages[name]) name = "dashboard";
    current = name;
    $$("#nav a").forEach(function (a) { a.classList.toggle("active", a.dataset.page === name); });
    closeOverlay();
    clearInterval(refreshTimer);
    $("#page").innerHTML = SKELETON;
    var link = $('#nav a[data-page="' + name + '"]');
    document.title = (link ? link.textContent.replace(/\d+$/, "").trim() + " · " : "") + "Dr.Tracker Outreach";
    Promise.resolve(pages[name].render($("#page"))).then(function () {
      var head = $("#page .page-head");
      if (head) $("#page").classList.add("page-enter");
      setTimeout(function () { $("#page").classList.remove("page-enter"); }, 300);
    }).catch(function (e) {
      $("#page").innerHTML = '<div class="card">' + emptyState("alert", "This page couldn't load", '<div>' + esc(e.message || e) + '</div>', '<button class="btn secondary" id="retryPage">Try again</button>') + "</div>";
      $("#retryPage").onclick = route;
    });
    if (pages[name].refresh) refreshTimer = setInterval(function () { if (current === name && !$("#overlay").innerHTML) pages[name].refresh(); }, 15000);
    window.scrollTo(0, 0);
  }
  window.addEventListener("hashchange", route);

  function refreshSide() {
    return api("GET", "/campaign").then(function (c) {
      var rc = $("#reviewCount");
      rc.hidden = !c.drafts_to_review;
      rc.textContent = c.drafts_to_review;
      $("#sideFoot").innerHTML =
        '<div class="side-status"><div class="row head"><span class="status-dot ' + (c.running ? (c.in_window ? "on" : "warn") : "") + '"></span>' +
        (c.running ? (c.in_window ? "Campaign running" : "Waiting for send window") : "Campaign paused") + "</div>" +
        '<div class="row">Sent today<strong>' + sentToday(c) + "</strong></div>" +
        '<div class="row">Sending<strong>' + (c.email_mode === "smtp" ? "Live" : "Test mode") + "</strong></div>" +
        '<div class="row">Drafts by<strong>' + (c.drafter === "claude" ? "Claude" : "Templates") + "</strong></div></div>" + authFoot();
      if ($("#signOut")) $("#signOut").onclick = function (e) { e.preventDefault(); signOut(); };
    }).catch(function (e) {
      if (e.message === OFFLINE) $("#sideFoot").innerHTML = '<div class="side-status"><div class="row head"><span class="status-dot off"></span>Offline</div>' +
        '<div class="row" style="line-height:1.4">Reconnecting automatically…</div></div>';
    });
  }
  setInterval(refreshSide, 20000);
  function sentToday(c) { return c.sent_today + (+c.daily_cap ? " / " + c.daily_cap : ""); }

  /* ------------------------------------------------------------------ dashboard */
  pages.dashboard = {
    render: function (el) {
      return Promise.all([api("GET", "/dashboard"), loadSettings()]).then(function (r) {
        var d = r[0], s = r[1], c = d.campaign, k = d.counts;
        var total = Object.keys(k).reduce(function (a, x) { return a + k[x]; }, 0);
        var checks = [
          [!!(s.sender.name && s.sender.address), "Fill in sender details and business address", "#/setup"],
          [!!s.pitches.doctor.pitch, "Write your pitch in your own words (optional — a sensible default is used)", "#/setup"],
          [total > 0, "Upload your contact spreadsheet", "#/contacts"],
          [s.ai.anthropic_api_key_set, "Add an Anthropic API key for AI-written drafts (optional — templates work without it)", "#/connections"],
          [s.email.mode === "smtp", "Connect your email account (until then everything runs in test mode)", "#/connections"],
          [c.running, "Review drafts and start the campaign", "#/campaign"],
        ];
        var doneAll = checks.every(function (x) { return x[0]; });
        var funnel = ["new", "drafted", "sent", "replied", "adopted"];
        var maxDaily = Math.max.apply(null, [1].concat(d.daily_sends.map(function (x) { return x.n; })));
        var doneCount = checks.filter(function (x) { return x[0]; }).length;

        el.innerHTML =
          '<div class="page-head"><div><h2>Dashboard</h2><p>Where your outreach stands today.</p></div>' +
            '<div class="btn-row"><a class="btn secondary" href="#/contacts">' + ICON.users + "Contacts</a>" + (c.drafts_to_review ? '<a class="btn" href="#/review">Review ' + c.drafts_to_review + " draft" + (c.drafts_to_review === 1 ? "" : "s") + "</a>" : "") + "</div></div>" +
          (doneAll ? "" : '<div class="card" style="margin-bottom:16px"><div class="card-head"><h3>Getting started</h3><span class="progress-label">' + doneCount + " of " + checks.length + ' done</span></div><p class="sub">Work down the list — the system runs on its own once these are done.</p>' +
            '<div class="bar" style="margin:0 0 14px"><span style="width:' + Math.round(100 * doneCount / checks.length) + '%"></span></div><ul class="checklist">' +
            checks.map(function (x) { return '<li class="' + (x[0] ? "done" : "") + '"><span class="tick">' + (x[0] ? ICON.check : "") + '</span><span class="t">' + esc(x[1]) + '</span>' + (x[0] ? "" : ' <a href="' + x[2] + '">Set up →</a>') + "</li>"; }).join("") + "</ul></div>") +
          '<div class="stats">' +
            stat(total, "Contacts") + stat(d.contacted, "Contacted") +
            stat(d.replied + (d.reply_rate != null ? ' <span class="muted" style="font-size:14px">(' + d.reply_rate + "%)</span>" : ""), "Replied", true) +
            stat(k.unsubscribed + k.bounced, "Opted out / bounced") +
          "</div>" +
          '<div class="grid two">' +
            '<div class="card"><h3>Funnel</h3><p class="sub">Contacts by stage.</p><div class="funnel">' +
              funnel.map(function (f) { return '<div class="step"><div class="n">' + (k[f] || 0) + '</div><div class="l">' + STATUS_LABEL[f] + '</div><div class="bar"><span style="width:' + (total ? Math.round(100 * (k[f] || 0) / total) : 0) + '%"></span></div></div>'; }).join("") +
            "</div></div>" +
            '<div class="card"><h3>Campaign</h3>' + campaignSummary(c) + '<div class="btn-row" style="margin-top:16px"><a class="btn secondary small" href="#/campaign">Manage campaign</a></div></div>' +
          "</div>" +
          '<div class="grid two" style="margin-top:16px">' +
            '<div class="card"><h3>Recent replies</h3>' + (d.recent_replies.length ? '<ul class="timeline" style="margin-top:12px">' + d.recent_replies.map(function (i) {
              return "<li>" + badge(i.kind, i.kind === "reply" ? "Reply" : "Unsubscribed") + " " + intentBadge(i.intent) + " <strong>" + esc(i.contact_name || i.from_addr) + '</strong> <span class="muted">' + ago(i.received_at) + "</span>" + (i.snippet ? "<pre>" + esc(i.snippet) + "</pre>" : "") + "</li>";
            }).join("") + "</ul>" : emptyState("inbox", "No replies yet", "Once your inbox is connected, replies are picked up automatically.")) + "</div>" +
            '<div class="card"><h3>Emails sent</h3><p class="sub" style="margin:0">Last 14 days.</p>' + (d.daily_sends.length ? '<div class="bars">' + d.daily_sends.map(function (x) {
              var day = new Date(x.day + "T00:00:00");
              return '<div class="col" title="' + esc(x.day) + ": " + x.n + ' sent"><span class="v">' + x.n + '</span><div class="b" style="height:' + Math.max(4, Math.round(100 * x.n / maxDaily)) + 'px"></div><span class="d">' + esc(isNaN(day) ? x.day.slice(5) : day.toLocaleDateString(undefined, { day: "numeric", month: "short" })) + "</span></div>";
            }).join("") + "</div>" : emptyState("mail", "Nothing sent yet", "Approved emails show up here once the campaign sends them.")) + "</div>" +
          "</div>";
      });
    },
    refresh: function () { return pages.dashboard.render($("#page")); },
  };
  function stat(n, l, raw) { return '<div class="stat"><div class="n">' + (raw ? n : esc(n)) + '</div><div class="l">' + esc(l) + "</div></div>"; }
  function campaignSummary(c) {
    var state = c.running ? (c.in_window ? '<span class="status-dot on"></span> Running' : '<span class="status-dot warn"></span> Running — waiting for the sending window') : '<span class="status-dot"></span> Paused';
    var next = "";
    if (c.running && c.in_window && c.approved_waiting) next = c.next_send_at && c.next_send_at > Date.now() / 1000 ? "Next send " + until(c.next_send_at) : "Sending shortly";
    else if (c.running && !c.in_window && c.next_window) next = "Window opens " + esc(c.next_window);
    return '<p style="display:flex;gap:8px;align-items:center;margin:8px 0 4px;font-weight:600">' + state + "</p>" +
      '<p class="muted" style="margin:0 0 14px">' + esc(c.ist_now) + (next ? " · " + next : "") + "</p>" +
      '<div class="stats compact" style="grid-template-columns:repeat(3,1fr);margin-bottom:12px">' +
        stat(sentToday(c), "Sent today") + stat(c.approved_waiting, "Queued") + stat(c.drafts_to_review, "To review") + "</div>" +
      '<dl class="kv">' +
        (c.contacts_to_draft ? "<dt>Waiting to be drafted</dt><dd>" + c.contacts_to_draft + "</dd>" : "") +
        "<dt>Daily limit</dt><dd>" + (+c.daily_cap ? c.daily_cap + " emails" : "No limit") + "</dd>" +
        "<dt>Sending</dt><dd>" + (c.email_mode === "smtp" ? "Live" : "Test mode — nothing is really sent") + "</dd>" +
        "<dt>Drafts written by</dt><dd>" + (c.drafter === "claude" ? "Claude" : "Built-in templates") + "</dd>" +
      "</dl>" +
      (c.last_inbox_error ? '<div class="notice warn" style="margin:14px 0 0">Inbox: ' + esc(c.last_inbox_error) + "</div>" : "");
  }

  /* ------------------------------------------------------------------ contacts */
  var contactFilter = { q: "", status: "", type: "" };
  pages.contacts = {
    render: function (el) {
      el.innerHTML =
        '<div class="page-head"><div><h2>Contacts</h2><p>Doctors, hospitals, companies, product partners, and marketplace brands and health professionals. Upload a spreadsheet and the system detects each doctor\'s specialty, then writes to them in their own world.</p></div>' +
        '<div class="btn-row"><button class="btn secondary" id="addBtn">' + ICON.plus + 'Add contact</button><button class="btn" id="importBtn">' + ICON.upload + "Upload spreadsheet</button>" +
          '<div class="menu-wrap"><button class="btn secondary" id="moreBtn" aria-label="More actions" aria-haspopup="true">' + ICON.more + '</button><div class="menu" id="moreMenu">' +
            '<a href="/api/export/contacts.csv">Export contacts<small>All contacts as a CSV file</small></a>' +
            '<a href="/api/export/partner-status.csv">Export partner statuses<small>Statuses keyed by partner ID</small></a>' +
            '<button id="wbBtn">Update partner workbook…<small>Upload the workbook, get it back with Status filled in</small></button><hr>' +
            '<a href="/api/import/template.csv">Download import template<small>The expected spreadsheet columns</small></a>' +
          '</div></div><input type="file" id="wbFile" accept=".xlsx,.xlsm" hidden></div></div>' +
        '<div class="toolbar"><div class="search">' + ICON.search + '<input type="text" id="q" placeholder="Search name, email, clinic…" value="' + esc(contactFilter.q) + '"></div>' +
        '<select id="typeF"><option value="">All types</option>' + Object.keys(TYPE_LABEL).map(function (t) { return '<option value="' + t + '"' + (contactFilter.type === t ? " selected" : "") + ">" + TYPE_LABEL[t] + "</option>"; }).join("") + "</select></div>" +
        '<div class="filters" id="chips"></div>' +
        '<div id="contactsTable"></div>';
      $("#importBtn").onclick = openImport;
      $("#addBtn").onclick = openAddContact;
      $("#moreBtn").onclick = function (e) { e.stopPropagation(); $("#moreMenu").classList.toggle("open"); };
      $("#wbBtn").onclick = function () { $("#moreMenu").classList.remove("open"); $("#wbFile").value = ""; $("#wbFile").click(); };
      $("#wbFile").onchange = function (e) { if (e.target.files[0]) updateWorkbook(e.target.files[0]); };
      var t;
      $("#q").oninput = function (e) { clearTimeout(t); t = setTimeout(function () { contactFilter.q = e.target.value; loadContacts(); }, 250); };
      $("#typeF").onchange = function (e) { contactFilter.type = e.target.value; loadContacts(); };
      return loadContacts();
    },
    refresh: function () { return loadContacts(); },
  };

  function updateWorkbook(f) {
    var fd = new FormData();
    fd.append("file", f);
    toast("Updating Status in " + f.name + "…");
    fetch("/api/export/partner-workbook", { method: "POST", body: fd }).catch(offline).then(function (r) {
      if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || r.statusText); });
      var summary = JSON.parse(r.headers.get("X-Status-Summary") || "{}");
      return r.blob().then(function (blob) {
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = f.name.replace(/\.(xlsx|xlsm)$/i, "") + " (status updated)." + (/\.xlsm$/i.test(f.name) ? "xlsm" : "xlsx");
        document.body.appendChild(a);
        a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
        toast(summary.updated + " Status cells updated, " + summary.unchanged + " already right" + (summary.unmatched ? ", " + summary.unmatched + " rows not in the app (left alone)" : "") + ".");
      });
    }).catch(fail);
  }

  function loadContacts() {
    var qs = "?q=" + encodeURIComponent(contactFilter.q) + (contactFilter.status ? "&status=" + contactFilter.status : "");
    return api("GET", "/contacts" + qs).then(function (list) {
      if (contactFilter.type) list = list.filter(function (c) { return c.type === contactFilter.type; });
      var chips = [["", "All"]].concat(Object.keys(STATUS_LABEL).map(function (s) { return [s, STATUS_LABEL[s]]; }));
      $("#chips").innerHTML = chips.map(function (c) { return '<button class="chip' + (contactFilter.status === c[0] ? " active" : "") + '" data-s="' + c[0] + '">' + c[1] + "</button>"; }).join("");
      $$("#chips .chip").forEach(function (b) { b.onclick = function () { contactFilter.status = b.dataset.s; loadContacts(); }; });
      var wrap = $("#contactsTable");
      if (!list.length) {
        var filtered = contactFilter.q || contactFilter.status || contactFilter.type;
        wrap.innerHTML = '<div class="table-wrap">' + (filtered
          ? emptyState("search", "No matches", "Try a different search or clear the filters.", '<button class="btn secondary small" id="clearF">Clear filters</button>')
          : emptyState("users", "No contacts yet", "Upload a spreadsheet (CSV or Excel) or add someone by hand.", '<button class="btn small" id="emptyImport">' + ICON.upload + "Upload spreadsheet</button>")) + "</div>";
        if ($("#clearF")) $("#clearF").onclick = function () { contactFilter = { q: "", status: "", type: "" }; $("#q").value = ""; $("#typeF").value = ""; loadContacts(); };
        if ($("#emptyImport")) $("#emptyImport").onclick = openImport;
        return;
      }
      wrap.innerHTML = '<div class="table-wrap"><table><thead><tr><th>Name</th><th>Type</th><th>Specialty / role</th><th>Clinic / company</th><th>City</th><th>Status</th><th>Next step</th></tr></thead><tbody>' +
        list.map(function (c) {
          var spec = isMarket(c.type)
            ? (categoryLabel(c) ? "<strong>" + esc(categoryLabel(c)) + "</strong>" : '<span class="muted">Category not set</span>') + (c.needs_check ? '<br><span class="muted">Check: ' + esc(c.needs_check) + "</span>" : "")
            : c.type === "doctor"
            ? (c.specialty_label ? "<strong>" + esc(c.specialty_label) + "</strong>" + (c.specialty && c.specialty.toLowerCase() !== c.specialty_label.toLowerCase() ? '<br><span class="muted">' + esc(c.specialty) + "</span>" : "") : '<span class="muted">' + (esc(c.specialty) || "Not detected") + "</span>")
            : esc(c.specialty || "—");
          var next = c.pending_message ? badge(c.pending_message.status, MSG_LABEL[c.pending_message.status]) : (c.last_error ? badge("error", "Draft failed") : '<span class="muted">—</span>');
          return '<tr class="clickable" data-id="' + c.id + '"><td class="name-cell"><strong>' + esc(c.name) + "</strong><span>" + esc(c.email) + "</span></td><td>" + TYPE_LABEL[c.type] + "</td><td>" + spec + "</td><td>" + esc(c.clinic || "—") + (c.product ? '<br><span class="muted">' + esc(c.product) + "</span>" : "") + "</td><td>" + esc(c.city || "—") + "</td><td>" + statusBadge(c.status) + "</td><td>" + next + "</td></tr>";
        }).join("") + '</tbody></table><div class="table-foot">' + list.length + " contact" + (list.length === 1 ? "" : "s") + "</div></div>";
      $$("#contactsTable tr[data-id]").forEach(function (tr) { tr.onclick = function () { openContact(+tr.dataset.id); }; });
    });
  }

  function contactForm(c) {
    c = c || { type: "doctor" };
    return '<div class="field"><label>Type</label><select id="fType">' + Object.keys(TYPE_LABEL).map(function (t) { return '<option value="' + t + '"' + (c.type === t ? " selected" : "") + ">" + TYPE_LABEL[t] + "</option>"; }).join("") + "</select></div>" +
      '<div class="field-row"><div class="field"><label>Name</label><input type="text" id="fName" value="' + esc(c.name) + '" placeholder="Dr. Aditi Sharma"></div>' +
      '<div class="field"><label>Email</label><input type="email" id="fEmail" value="' + esc(c.email) + '"' + (c.id ? " disabled" : "") + ' placeholder="aditi@clinic.in"></div></div>' +
      '<div class="field-row"><div class="field"><label id="fSpecL">' + (c.type === "doctor" ? "Specialty (as written)" : isMarket(c.type) ? "Category (as written)" : "Role") + '</label><input type="text" id="fSpec" value="' + esc(c.specialty) + '" placeholder="MD, DM (Cardiology)"></div>' +
      '<div class="field"><label>Clinic / hospital / company</label><input type="text" id="fClinic" value="' + esc(c.clinic) + '"></div></div>' +
      '<div class="field-row"><div class="field"><label>City</label><input type="text" id="fCity" value="' + esc(c.city) + '"></div>' +
      '<div class="field" id="fProductWrap"' + (c.type === "partner" || c.type === "company" ? "" : " hidden") + '><label>Their product</label><input type="text" id="fProduct" value="' + esc(c.product) + '" placeholder="e.g. VitalSense home BP monitor"></div></div>' +
      '<div id="fMarketWrap"' + (isMarket(c.type) ? "" : " hidden") + '>' +
        '<div class="field"><label>Category</label><select id="fCategory">' + categoryOptions(c.type, c.category) + "</select></div>" +
        '<div class="field"><label>What they sell or do <span class="muted">(one line; used to make the opening specific)</span></label><textarea id="fDesc" rows="2" placeholder="e.g. Millet snack bars with no added sugar">' + esc(c.description) + "</textarea></div>" +
        '<div class="field"><label>Check before sending <span class="muted">(optional — shown as a warning on Review)</span></label><input type="text" id="fCheck" value="' + esc(c.needs_check) + '" placeholder="e.g. email is a generic care@ inbox"></div>' +
        '<div class="field-row"><div class="field"><label>Website</label><input type="text" id="fWebsite" value="' + esc(c.website) + '" placeholder="crunchwell.in"></div>' +
        '<div class="field"><label>Instagram / socials <span class="muted">(links or @handle; read with the website)</span></label><input type="text" id="fSocials" value="' + esc(c.socials) + '" placeholder="https://www.instagram.com/…"></div></div>' +
        '<div class="field-row"><div class="field"><label>Hero product <span class="muted">(brands)</span></label><input type="text" id="fHero" value="' + esc(c.product) + '"></div>' +
        '<div class="field"><label>Pricing <span class="muted">(for you; never used in emails)</span></label><input type="text" id="fPricing" value="' + esc(c.pricing) + '"></div></div>' +
        '<div class="field-row"><div class="field"><label>Spend last quarter (₹ lakhs) <span class="muted">(brands, optional)</span></label><input type="text" id="fSpend" value="' + esc(c.spend) + '" placeholder="e.g. 1.5–2"></div>' +
        '<div class="field"><label>Spend source</label><input type="text" id="fSpendSource" value="' + esc(c.spend_source) + '" placeholder="Where the figure comes from"></div></div>' +
        '<p class="hint" style="margin-top:-6px">The brand guide only uses the marketing-bill angle when both spend fields are filled in. Never estimate the figure.</p>' +
        (function () {
          var extra = {};
          try { extra = JSON.parse(c.extra || "{}") || {}; } catch (e) { extra = {}; }
          var keys = Object.keys(extra);
          return keys.length ? '<div class="field"><label>Other columns from the sheet</label><ul class="hint" style="margin:0;padding-left:18px">' + keys.map(function (k) { return "<li><strong>" + esc(k) + ":</strong> " + esc(extra[k]) + "</li>"; }).join("") + "</ul></div>" : "";
        })() +
      "</div>" +
      '<div class="field"><label>Notes <span class="muted">(used to personalise the email)</span></label><textarea id="fNotes" rows="3">' + esc(c.notes) + "</textarea></div>";
  }
  function readContactForm() {
    var t = $("#fType").value;
    var out = { type: t, name: $("#fName").value.trim(), email: $("#fEmail").value.trim(), specialty: $("#fSpec").value.trim(), clinic: $("#fClinic").value.trim(), city: $("#fCity").value.trim(), product: $("#fProduct").value.trim(), notes: $("#fNotes").value.trim(), category: $("#fCategory").value, description: $("#fDesc").value.trim(), needs_check: $("#fCheck").value.trim() };
    if (isMarket(t)) {
      Object.assign(out, { product: $("#fHero").value.trim(), website: $("#fWebsite").value.trim(), socials: $("#fSocials").value.trim(), pricing: $("#fPricing").value.trim(), spend: $("#fSpend").value.trim(), spend_source: $("#fSpendSource").value.trim() });
    }
    return out;
  }
  function wireTypeToggle() {
    $("#fType").onchange = function (e) {
      var t = e.target.value;
      $("#fProductWrap").hidden = !(t === "partner" || t === "company");
      $("#fMarketWrap").hidden = !isMarket(t);
      if (isMarket(t)) $("#fCategory").innerHTML = categoryOptions(t, "");
      $("#fSpecL").textContent = t === "doctor" ? "Specialty (as written)" : isMarket(t) ? "Category (as written)" : "Role";
    };
  }

  function openAddContact() {
    openOverlay(closeBtn() + '<h3>Add contact</h3>' + contactForm() + '<div class="btn-row"><button class="btn" id="save">Add contact</button></div>');
    wireTypeToggle();
    $("#x").onclick = closeOverlay;
    $("#save").onclick = function () {
      api("POST", "/contacts", readContactForm()).then(function (c) {
        closeOverlay();
        toast("Added " + c.name + (c.specialty_label ? " — detected " + c.specialty_label : ""));
        loadContacts();
      }).catch(fail);
    };
  }

  function openContact(id) {
    api("GET", "/contacts/" + id).then(function (c) {
      var specOpts = '<option value="">Automatic' + (c.specialty_source !== "manual" && c.specialty_label ? " (" + esc(c.specialty_label) + ")" : "") + "</option>" +
        meta.specialties.map(function (s) { return '<option value="' + s.key + '"' + (c.specialty_source === "manual" && c.specialty_key === s.key ? " selected" : "") + ">" + esc(s.label) + "</option>"; }).join("");
      var timeline = [];
      c.messages.forEach(function (m) {
        timeline.push({ ts: m.sent_at || m.created_at, html: badge(m.status, MSG_LABEL[m.status] || m.status) + " " + kindLabel(m.kind) + ' <span class="muted">' + fmtTime(m.sent_at || m.created_at) + " · " + esc(m.drafted_by) + (m.transport === "dry_run" ? " · test mode" : "") + "</span>" + (m.error ? '<div style="color:var(--danger);font-size:13px">' + esc(m.error) + "</div>" : "") + "<pre><strong>" + esc(m.subject) + "</strong>\n\n" + esc(m.body) + "</pre>" });
      });
      c.inbound.forEach(function (i) {
        timeline.push({ ts: i.received_at, html: badge(i.kind, i.kind.replace("_", " ")) + " " + intentBadge(i.intent) + ' <span class="muted">' + fmtTime(i.received_at) + " · " + esc(i.from_addr) + "</span>" + (i.intent_note ? '<div class="muted" style="font-size:13px">' + esc(i.intent_note) + "</div>" : "") + ((i.body || i.snippet) ? "<pre>" + esc(i.body || i.snippet) + "</pre>" : "") });
      });
      timeline.sort(function (a, b) { return b.ts - a.ts; });

      var d = openOverlay(
        closeBtn() +
        "<h3>" + esc(c.name) + "</h3>" +
        '<div class="btn-row" style="margin:-6px 0 16px">' + statusBadge(c.status) + badge("", TYPE_LABEL[c.type]) + (c.suppressed ? badge("unsubscribed", "On opt-out list") : "") + '<span class="muted">' + esc(c.email) + "</span></div>" +
        (c.last_error ? '<div class="notice warn">' + (c.last_error.indexOf("Not written:") === 0 ? esc(c.last_error) + ' <button class="btn secondary small" id="forceBtn" style="margin-left:8px">Write it anyway</button>' : "Last draft attempt failed: " + esc(c.last_error)) + "</div>" : "") +
        '<div class="card" style="padding:16px"><div class="btn-row">' +
          '<button class="btn small" id="draftBtn">' + (c.pending_message ? "Rewrite draft" : "Write draft") + "</button>" +
          (c.status === "sent" ? '<button class="btn secondary small" id="fuBtn">Write follow-up</button>' : "") +
          (c.inbound.some(function (i) { return i.kind === "reply"; }) ? '<button class="btn secondary small" id="replyBtn">Suggest a response</button>' : "") +
          (c.pending_message ? '<a class="btn secondary small" href="#/review" id="toReview">Open in Review</a>' : "") +
          '<span class="spacer"></span><label style="margin:0" for="statusSel">Status</label><select id="statusSel" style="width:auto">' +
          Object.keys(STATUS_LABEL).map(function (s) { return '<option value="' + s + '"' + (c.status === s ? " selected" : "") + ">" + STATUS_LABEL[s] + "</option>"; }).join("") +
        "</select></div></div>" +
        '<div class="card" style="padding:16px">' + contactForm(c) +
          (c.type === "doctor" ? '<div class="field"><label>Specialty used for the email</label><select id="fSpecKey">' + specOpts + '</select><p class="hint">' +
            (c.specialty_label ? "Detected <strong>" + esc(c.specialty_label) + "</strong>" + (c.specialty_source ? " from the " + esc(c.specialty_source) : "") + ". " : "Not detected yet — pick one so the email opens with a scene from their specialty. ") +
            "The opening scene and subject are chosen for this specialty.</p></div>" : "") +
          (isMarket(c.type) ? '<div class="field"><label>From their website and socials</label>' +
            (c.site_text ? "<pre class=\"site-text\">" + esc(c.site_text) + "</pre>" + (c.site_error ? '<p class="hint">Couldn\'t read: ' + esc(c.site_error) + ".</p>" : "") : '<p class="hint" style="margin-top:0">' + (c.site_error ? "Not read: " + esc(c.site_error) + "." : "Read automatically before Claude writes the first email: the homepage, its About and Products pages, and the social profiles in the sheet.") + "</p>") +
            (c.site_checked_at ? '<p class="hint">Checked ' + ago(c.site_checked_at) + ". Claude uses this only to be specific about what they do; it never repeats their claims.</p>" : "") +
            '<button class="btn ghost small" id="siteBtn">' + (c.site_checked_at ? "Read again" : "Read now") + "</button></div>" : "") +
          '<div class="btn-row"><button class="btn secondary small" id="saveC">Save changes</button><span class="spacer"></span><button class="btn danger small" id="delC">Delete contact</button></div></div>' +
        '<h3 style="margin-top:22px;font-size:16px">History</h3>' +
        (timeline.length ? '<ul class="timeline">' + timeline.map(function (t) { return "<li>" + t.html + "</li>"; }).join("") + "</ul>" : '<p class="muted">Nothing yet.</p>'),
        "drawer"
      );
      wireTypeToggle();
      $("#x").onclick = closeOverlay;
      if ($("#toReview")) $("#toReview").onclick = closeOverlay;
      $("#statusSel").onchange = function (e) {
        var s = e.target.value;
        if ((c.status === "unsubscribed" || c.status === "bounced") && !confirm(c.email + " is on the opt-out list. Only move them off it if they clearly opted back in. Continue?")) { e.target.value = c.status; return; }
        api("POST", "/contacts/" + id + "/status", { status: s }).then(function () { toast("Status updated"); openContact(id); loadContacts(); }).catch(fail);
      };
      $("#saveC").onclick = function () {
        var body = readContactForm();
        delete body.email;
        if ($("#fSpecKey")) body.specialty_key = $("#fSpecKey").value;
        api("PATCH", "/contacts/" + id, body).then(function () { toast("Saved"); openContact(id); loadContacts(); }).catch(fail);
      };
      $("#delC").onclick = function () {
        if (!confirm("Delete " + c.name + " and their history? If they opted out, their email stays on the opt-out list.")) return;
        api("DELETE", "/contacts/" + id).then(function () { closeOverlay(); toast("Deleted"); loadContacts(); }).catch(fail);
      };
      $("#draftBtn").onclick = function () { draftFor(id, "initial", this); };
      if ($("#forceBtn")) $("#forceBtn").onclick = function () { draftFor(id, "initial", this, true); };
      if ($("#fuBtn")) $("#fuBtn").onclick = function () { draftFor(id, "followup", this); };
      if ($("#replyBtn")) $("#replyBtn").onclick = function () { draftFor(id, "reply", this); };
      if ($("#siteBtn")) $("#siteBtn").onclick = function () {
        var b = this; b.disabled = true; b.textContent = "Reading…";
        api("POST", "/contacts/" + id + "/research").then(function (r) { toast(!r.site_text && r.site_error ? "Couldn't read it: " + r.site_error : "Website and socials read" + (r.site_error ? " (some couldn't be)" : "")); openContact(id); }).catch(function (e) { fail(e); b.disabled = false; });
      };
    }).catch(fail);
  }

  function draftFor(id, kind, btn, force) {
    busy(btn, true);
    api("POST", "/contacts/" + id + "/draft?kind=" + kind + (force ? "&force=true" : "")).then(function () {
      toast("Draft ready — review it before it goes out");
      openContact(id);
      refreshSide();
    }).catch(function (e) { fail(e); busy(btn, false); });
  }

  /* Import wizard */
  var FIELD_LABEL = {
    name: "Name", email: "Email *", type: "Type (doctor / hospital / company / partner / brand / professional)", category: "Marketplace category",
    specialty: "Specialty / role / category", clinic: "Clinic / hospital / company", product: "Product (partners) or description (brands, professionals)", city: "City", notes: "Notes",
    description: "Description (what they sell or do)", notes_source: "Notes / Source (founder's sheet)", partner_id: "Partner ID", tier: "Tier",
    phone: "Phone", website: "Website", socials: "Instagram / socials", pricing: "Pricing (never used in emails)",
    spend: "Spend last quarter (₹ lakhs)", spend_source: "Spend source", source_url: "Source URL", needs_check: "Check before sending",
  };
  var AUDIENCE_LABEL = { brand: "brands", professional: "professionals", mixed: "mixed types" };
  function openImport() {
    openOverlay(
      closeBtn() + '<h3>Upload a spreadsheet</h3>' +
      '<p class="sub" style="color:var(--ink-soft)">CSV or Excel (.xlsx). Every tab of a workbook is read (one tab per city is fine), and every column is kept. Columns are matched automatically and you can fix the mapping on the next step. Only upload contacts you have a legitimate reason to email; people on the opt-out list are skipped automatically.</p>' +
      '<div class="field"><label>Who is this spreadsheet for?</label><div class="audience-pick">' +
        '<label class="check"><input type="radio" name="aud" value="brand"> <span><strong>Brands</strong><br><span class="muted">Emails use the brand template and banner.</span></span></label>' +
        '<label class="check"><input type="radio" name="aud" value="professional"> <span><strong>Professionals</strong><br><span class="muted">Emails use the professional template<span id="profNote"></span>.</span></span></label>' +
        '<label class="check"><input type="radio" name="aud" value="mixed"> <span><strong>Mixed</strong><br><span class="muted">Each row’s Type column decides (the founder’s partner workbook).</span></span></label>' +
      "</div></div>" +
      '<div class="field"><input type="file" id="file" accept=".csv,.tsv,.txt,.xlsx,.xlsm" disabled><p class="hint" id="fileHint">Choose who it’s for first.</p></div>' +
      '<p class="hint"><a href="/api/import/template.csv">Download a template</a> to see the expected columns.</p>' +
      '<div id="importStep"></div>', "wide");
    $("#x").onclick = closeOverlay;
    api("GET", "/templates").then(function (t) {
      templateCache = t;
      if (!t.professional.guide_source && $("#profNote")) $("#profNote").textContent = " (not added yet: the built-in pitch for now)";
    }).catch(function () {});
    var audience = "";
    $$('input[name="aud"]').forEach(function (r) {
      r.onchange = function () {
        audience = r.value;
        $("#file").disabled = false;
        $("#fileHint").textContent = "Every row will be imported as " + AUDIENCE_LABEL[audience] + ".";
        if ($("#file").files[0]) $("#file").onchange({ target: $("#file") });  // re-read with the new choice
      };
    });
    $("#file").onchange = function (e) {
      var f = e.target.files[0];
      if (!f || !audience) return;
      var fd = new FormData();
      fd.append("file", f);
      fd.append("audience", audience);
      $("#importStep").innerHTML = '<p class="muted">Reading ' + esc(f.name) + "…</p>";
      api("POST", "/import/preview", fd, true).then(showMapping).catch(function (err) { $("#importStep").innerHTML = ""; fail(err); });
    };
  }
  var SHEET_STATUS = { new: "Not contacted", sent: "Contacted", replied: "Replied", adopted: "Interested", declined: "Not interested", bounced: "Bounced", unsubscribed: "Unsubscribed" };
  function sheetLists(r) {
    var skipped = r.skipped || [], flags = r.flags || [];
    return (flags.length ? '<h4 style="margin:16px 0 6px">Needs a look (' + flags.length + ")</h4><ul class=\"hint\" style=\"margin:0;padding-left:18px\">" +
        flags.map(function (f) { return "<li>" + esc(f.tab + " row " + f.row + " · " + f.name + ": " + f.message) + "</li>"; }).join("") + "</ul>" : "") +
      (skipped.length ? '<h4 style="margin:16px 0 6px">Skipped (' + skipped.length + ")</h4><ul class=\"hint\" style=\"margin:0;padding-left:18px\">" +
        skipped.map(function (x) { return "<li>" + esc(x.tab + " row " + x.row + " · " + (x.name || "(no name)") + " — " + x.reason + (x.detail ? " (" + x.detail + ")" : "")) + "</li>"; }).join("") + "</ul>" : "");
  }
  function sheetCounts(c) {
    var reasons = Object.keys(c.skipped_by_reason).map(function (k) { return c.skipped_by_reason[k] + " " + k; }).join(", ");
    return '<div class="stats" style="margin:12px 0">' + stat(c.add, "to add") + stat(c.update, "to update") + stat(c.unchanged, "already up to date") + stat(c.skipped, "skipped") + "</div>" +
      (reasons ? '<p class="hint">Skipped: ' + esc(reasons) + ".</p>" : "") +
      (c.generic_inboxes ? '<p class="hint">' + c.generic_inboxes + " of the new contacts use a generic inbox (info@, care@…). They're imported; replies from these tend to be slower.</p>" : "");
  }
  function showSheetPlan(p) {
    var c = p.counts;
    var notContacted = p.adds.filter(function (a) { return a.status === "new"; }).length;
    $("#importStep").innerHTML =
      '<div class="notice info"><strong>' + esc(p.filename) + "</strong>: the partner workbook" + (p.audience !== "mixed" ? ", " + AUDIENCE_LABEL[p.audience] + " only (rows of the other type are skipped)" : "") + ". " + p.row_count + " rows across " + p.tabs.map(esc).join(", ") +
        (p.skipped_tabs.length ? " (" + p.skipped_tabs.map(esc).join(", ") + " skipped — no partner headers)" : "") + ".</div>" +
      sheetCounts(c) +
      (c.add ? '<p class="hint">' + notContacted + " new contacts are “Not contacted” and will be drafted. Rows already marked Contacted, Replied, Interested or Not interested are imported with that status and never drafted; Bounced and Unsubscribed rows go straight onto the opt-out list.</p>" : "") +
      '<p class="hint">Existing contacts are matched by email, then by partner ID (ignoring the tier). Only filled-in cells overwrite; status and history are never changed. Machine-written drafts for updated contacts are replaced.</p>' +
      sheetLists(p) +
      '<div class="btn-row" style="margin-top:16px"><button class="btn" id="commit"' + (c.add + c.update ? "" : " disabled") + ">" +
        (c.add + c.update ? "Import: add " + c.add + ", update " + c.update : "Nothing to import") + "</button></div>";
    $("#commit").onclick = function () {
      this.disabled = true;
      api("POST", "/import/commit", { token: p.token }).then(function (r) {
        $("#importStep").innerHTML = '<div class="notice ok"><strong>' + r.added + " added, " + r.updated + " updated.</strong>" +
          (r.redrafted ? " " + r.redrafted + " out-of-date drafts were replaced and will be rewritten." : "") + "</div>" + sheetLists(r) +
          '<div class="btn-row"><button class="btn" id="done">Done</button><a class="btn secondary" href="#/review" id="goRev">Go to Review</a></div>';
        $("#done").onclick = function () { closeOverlay(); loadContacts(); refreshSide(); };
        $("#goRev").onclick = closeOverlay;
      }).catch(fail);
    };
  }
  function showMapping(p) {
    if (p.format === "partner_workbook") return showSheetPlan(p);
    var opts = function (sel) {
      return '<option value="">— not in this file —</option>' + p.headers.map(function (h, i) { return '<option value="' + i + '"' + (sel === i ? " selected" : "") + ">" + esc(h || "Column " + (i + 1)) + "</option>"; }).join("");
    };
    var nameHint = p.mapping.name_first != null ? '<p class="hint">Name will be built from “' + esc(p.headers[p.mapping.name_first]) + "”" + (p.mapping.name_last != null ? " + “" + esc(p.headers[p.mapping.name_last]) + "”" : "") + ".</p>" : "";
    var tabs = p.tabs || [];
    $("#importStep").innerHTML =
      '<div class="notice info"><strong>' + esc(p.filename) + "</strong>: " + p.row_count + " rows found" + (p.audience !== "mixed" ? ", imported as " + AUDIENCE_LABEL[p.audience] : "") + "." +
        (p.skipped_tabs && p.skipped_tabs.length ? " Tabs without a recognisable header row were left out: " + p.skipped_tabs.map(esc).join(", ") + "." : "") + "</div>" +
      (tabs.length > 1 ? '<h4 style="margin:0 0 8px">Tabs to import</h4><div class="tab-pick">' + tabs.map(function (t) {
        return '<label class="check"><input type="checkbox" data-tab="' + esc(t.name) + '" checked> ' + esc(t.name) + ' <span class="muted">(' + t.rows + " rows" + (/^(brands?|test)/i.test(t.name) ? ", looks like a test list" : "") + ")</span></label>";
      }).join("") + "</div>" : "") +
      '<h4 style="margin:0 0 10px">Match the columns</h4><div class="mapping">' +
      p.fields.map(function (f) { return "<label style=\"margin:0\">" + FIELD_LABEL[f] + '</label><select data-f="' + f + '">' + opts(p.mapping[f]) + "</select>"; }).join("") +
      (p.audience === "mixed" ? '<label style="margin:0">If no type is given</label><select id="defType">' + Object.keys(TYPE_LABEL).map(function (t) { return '<option value="' + t + '">' + TYPE_LABEL[t] + "</option>"; }).join("") + "</select>" : "") +
      "</div>" + nameHint +
      '<h4 style="margin:18px 0 8px">Preview</h4><div class="table-wrap"><table class="preview-table"><thead><tr>' + p.headers.map(function (h) { return "<th>" + esc(h) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      p.preview.map(function (r) { return "<tr>" + r.map(function (v) { return "<td title=\"" + esc(v) + "\">" + esc(v) + "</td>"; }).join("") + "</tr>"; }).join("") + "</tbody></table></div>" +
      '<div class="btn-row" style="margin-top:16px"><button class="btn" id="commit">Import ' + (tabs.length > 1 ? "the ticked tabs" : p.row_count + " rows") + "</button></div>";
    $("#commit").onclick = function () {
      var mapping = { name_first: p.mapping.name_first, name_last: p.mapping.name_last };
      $$("#importStep select[data-f]").forEach(function (s) { mapping[s.dataset.f] = s.value === "" ? null : +s.value; });
      if (mapping.name != null) { mapping.name_first = null; mapping.name_last = null; }
      if (mapping.email == null) { toast("Pick which column has the email address.", true); return; }
      this.disabled = true;
      var body = { token: p.token, mapping: mapping, default_type: $("#defType") ? $("#defType").value : "doctor" };
      if (tabs.length > 1) body.tabs = $$("[data-tab]").filter(function (x) { return x.checked; }).map(function (x) { return x.dataset.tab; });
      api("POST", "/import/commit", body).then(function (r) {
        $("#importStep").innerHTML = '<div class="notice ok"><strong>' + r.added + " " + (r.audience !== "mixed" ? AUDIENCE_LABEL[r.audience] : "contacts") + " added.</strong> " +
          (r.duplicates ? r.duplicates + " already in your list. " : "") + (r.opted_out ? r.opted_out + " skipped (opted out). " : "") + (r.invalid_count ? r.invalid_count + " rows skipped (listed below)." : "") + "</div>" +
          (r.invalid.length ? '<h4 style="margin:12px 0 6px">Skipped</h4><ul class="hint" style="margin:0;padding-left:18px;max-height:200px;overflow:auto">' + r.invalid.map(function (x) { return "<li>" + esc((x.tab ? x.tab + " " : "") + (x.row ? "row " + x.row + " · " : "") + (x.name || "(no name)") + " — " + x.reason) + "</li>"; }).join("") + (r.invalid_count > r.invalid.length ? "<li>…</li>" : "") + "</ul>" : "") +
          '<p class="hint">Specialties are detected automatically. Drafts will be written in the background (see Campaign → Auto-draft) and land in Review.</p>' +
          '<div class="btn-row"><button class="btn" id="done">Done</button><a class="btn secondary" href="#/review" id="goRev">Go to Review</a></div>';
        $("#done").onclick = function () { closeOverlay(); loadContacts(); refreshSide(); };
        $("#goRev").onclick = closeOverlay;
      }).catch(function (e) { fail(e); });
    };
  }

  /* ------------------------------------------------------------------ review */
  var reviewFilter = "draft";
  pages.review = {
    render: function (el) {
      el.innerHTML =
        '<div class="page-head"><div><h2>Review</h2><p>Every email is checked against the pre-send rules (including Indian advertising and medical-conduct rules) before it can go out. Edit anything, then approve — approved emails are sent by the campaign at a steady pace.</p></div>' +
        '<div class="btn-row"><button class="btn secondary" id="approveClean">Approve all without errors</button></div></div>' +
        '<div class="tabs-inline" id="revTabs"></div><div id="revList"></div>';
      $("#approveClean").onclick = function () {
        if (!confirm("Approve every draft that has no pre-send errors? Warnings don't block approval — skim them first.")) return;
        api("POST", "/review/approve-clean").then(function (r) { toast(r.approved + " approved" + (r.skipped ? ", " + r.skipped + " still need fixes" : "")); loadReview(); refreshSide(); }).catch(fail);
      };
      return loadReview();
    },
  };

  var templateCache = null;
  function loadReview() {
    return Promise.all([api("GET", "/review"), settingsCache ? Promise.resolve(settingsCache) : loadSettings(), api("GET", "/templates")]).then(function (r) {
      templateCache = r[2];
      var items = r[0], s = r[1];
      var groups = { draft: [], approved: [], failed: [] };
      items.forEach(function (m) { (groups[m.status] || []).push(m); });
      $("#revTabs").innerHTML = [["draft", "Needs review"], ["approved", "Approved — queued"], ["failed", "Failed"]].map(function (t) {
        return '<button data-t="' + t[0] + '" class="' + (reviewFilter === t[0] ? "active" : "") + '">' + t[1] + ' <span class="n">' + groups[t[0]].length + "</span></button>";
      }).join("");
      $$("#revTabs button").forEach(function (b) { b.onclick = function () { reviewFilter = b.dataset.t; loadReview(); }; });
      var list = groups[reviewFilter];
      var notices = "";
      if (!s.ai.anthropic_api_key_set && items.some(function (m) { return m.drafted_by.indexOf("template") === 0; }))
        notices += '<div class="notice info">These drafts come from the built-in templates. Add an Anthropic API key under <a href="#/connections">Connections</a> and each email is written fresh for the person and their specialty.</div>';
      if (!list.length) {
        $("#revList").innerHTML = notices + '<div class="card">' + (reviewFilter === "draft" ? emptyState("check", "You're all caught up", "New drafts appear here as contacts are added and drafted.")
          : reviewFilter === "approved" ? emptyState("mail", "Nothing queued", "Approved emails wait here until the campaign sends them.")
          : emptyState("check", "No failed sends", "Anything that couldn't be delivered shows up here to retry.")) + "</div>";
        return;
      }
      $("#revList").innerHTML = notices + list.map(reviewCard).join("");
      list.forEach(wireReviewCard);
    });
  }

  function qaHtml(qa) {
    return "<h4>Pre-send check</h4>" + (qa.length ? "<ul>" + qa.map(function (i) { return '<li class="' + i.level + '">' + esc(i.message) + "</li>"; }).join("") + "</ul>" : '<div class="ok">' + ICON.check + "No issues found. Give it a quick read before approving.</div>");
  }

  function reviewCard(m) {
    var hasErr = m.qa.some(function (i) { return i.level === "error"; });
    var by = m.drafted_by.indexOf("claude") === 0 ? badge("claude", "Written by Claude") : m.drafted_by.indexOf("template") === 0 ? badge("", "Template") : badge("", "Written by hand");
    return '<div class="review-card" id="m' + m.id + '">' +
      '<div class="head"><div><strong style="font-size:15px">' + esc(m.contact_name) + "</strong> " + badge("", TYPE_LABEL[m.contact_type]) + " " + (m.kind === "followup" ? badge("info", "Follow-up") : m.kind === "reply" ? badge("info", "Response to their reply") + " " + intentBadge(m.intent) : "") + " " + by +
        '<br><span class="muted">' + esc(m.contact_email) + (m.specialty ? " · " + esc(m.specialty) : "") + (m.clinic ? " · " + esc(m.clinic) : "") + (m.city ? " · " + esc(m.city) : "") + "</span></div>" +
        badge(m.status, MSG_LABEL[m.status]) + "</div>" +
      (m.error ? '<div class="notice warn">' + esc(m.error) + "</div>" : "") +
      (m.kind === "reply" ? '<div class="field"><label>They wrote' + (m.their_received_at ? ' <span class="muted">· ' + fmtTime(m.their_received_at) + "</span>" : "") + "</label>" +
        (m.intent_note ? '<p class="hint" style="margin-top:0">' + esc(m.intent_note) + "</p>" : "") + "<pre class=\"their-reply\">" + esc(m.their_text || "") + "</pre></div>" : "") +
      '<div class="field"><label>Subject</label><textarea class="subject" rows="1" data-k="subject"' + (m.kind !== "initial" ? " disabled" : "") + ">" + esc(m.subject) + "</textarea>" + (m.kind !== "initial" ? '<p class="hint">Sent as a reply in the same thread.</p>' : "") + "</div>" +
      '<div class="field"><label>Body</label><textarea class="body" data-k="body">' + esc(m.body) + '</textarea><p class="hint">Sender identity, your address and an opt-out line are added below this automatically.' +
        (m.kind === "initial" && templateCache && templateCache[m.contact_type] && templateCache[m.contact_type].banner_source ? ' The image' + (templateCache[m.contact_type].images.length > 1 ? "s below go" : " below goes") + ' inside the body where the <code>[SCREENSHOT_1]</code>… markers (or <code>[HOW_IT_WORKS_IMAGE]</code>) are. Use <em>Preview as sent</em> to see the whole email.</p>' + imageThumbs(m.contact_type, templateCache[m.contact_type]) : "</p>") + "</div>" +
      '<div class="qa">' + qaHtml(m.qa) + "</div>" +
      '<div class="btn-row">' +
        (m.status === "draft" ? '<button class="btn" data-a="approve"' + (hasErr ? " disabled" : "") + ">Approve</button>" : "") +
        (m.status === "approved" ? '<button class="btn secondary" data-a="unapprove">Back to review</button>' : "") +
        (m.status === "failed" ? '<button class="btn" data-a="retry">Retry</button>' : "") +
        (m.status !== "failed" ? '<button class="btn secondary" data-a="send"' + (hasErr ? " disabled" : "") + ">Send now</button>" : "") +
        '<button class="btn secondary" data-a="regen">' + "Rewrite" + "</button>" +
        '<button class="btn ghost" data-a="preview">Preview as sent</button>' +
        '<span class="spacer"></span><span class="muted" data-saved></span><button class="btn danger" data-a="discard">Discard</button>' +
      "</div></div>";
  }

  function wireReviewCard(m) {
    var card = $("#m" + m.id);
    var saveT;
    $$("textarea", card).forEach(function (ta) {
      ta.oninput = function () {
        clearTimeout(saveT);
        $("[data-saved]", card).textContent = "Editing…";
        saveT = setTimeout(function () {
          var body = {};
          body[ta.dataset.k] = ta.value;
          api("PUT", "/messages/" + m.id, body).then(function (u) {
            m = Object.assign(m, u);
            $(".qa", card).innerHTML = qaHtml(u.qa);
            $("[data-saved]", card).textContent = "Saved";
            var hasErr = u.qa.some(function (i) { return i.level === "error"; });
            $$('[data-a="approve"],[data-a="send"]', card).forEach(function (b) { b.disabled = hasErr; });
            if (u.status !== m.status || (reviewFilter === "approved" && u.status === "draft")) loadReview();
          }).catch(fail);
        }, 700);
      };
    });
    $$("[data-a]", card).forEach(function (b) {
      b.onclick = function () {
        var a = b.dataset.a;
        if (a === "preview") return previewMessage(m.id);
        if (a === "discard" && !confirm("Discard this draft? The contact stays in your list.")) return;
        if (a === "send" && !confirm("Send this to " + m.contact_email + " right now (outside the normal pacing)?")) return;
        busy(b, true);
        var req = a === "regen" ? api("POST", "/contacts/" + m.contact_id + "/draft?kind=" + m.kind) : api("POST", "/messages/" + m.id + "/" + a);
        req.then(function () {
          toast({ approve: "Approved — it'll go out with the campaign", unapprove: "Moved back to review", send: "Sent", discard: "Discarded", retry: "Moved back to review", regen: "Rewritten" }[a]);
          loadReview();
          refreshSide();
        }).catch(function (e) { fail(e); busy(b, false); });
      };
    });
  }

  function imageThumbs(audience, x) {
    return (x.images || []).map(function (img, i) {
      return '<img class="banner-thumb" src="/api/templates/' + audience + "/image/" + i + "?v=" + img.size + '" alt="' + esc(img.alt) + '">';
    }).join("");
  }
  function previewMessage(id) {
    api("GET", "/preview/" + id).then(function (p) {
      var pre = function (t) { return '<pre style="white-space:pre-wrap;font-family:inherit;margin:0;line-height:1.55">' + esc(t.replace(/^\s+|\s+$/g, "")) + "</pre>"; };
      var inner = (p.parts || [{ text: p.body }]).map(function (part) {
        return part.image ? '<img class="banner-preview" src="' + part.image + '" alt="How it works">' : pre(part.text);
      }).join("");
      openOverlay(closeBtn() + '<h3>As the recipient sees it</h3><div class="email-preview"><div class="meta"><strong>' + esc(p.subject) + '</strong></div><div class="content">' + inner + "</div></div>", "wide");
      $("#x").onclick = closeOverlay;
    }).catch(fail);
  }

  /* ------------------------------------------------------------------ campaign */
  var DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  pages.campaign = {
    render: function (el) {
      return Promise.all([api("GET", "/campaign"), loadSettings()]).then(function (r) {
        var c = r[0], camp = r[1].campaign;
        el.innerHTML =
          '<div class="page-head"><div><h2>Campaign</h2><p>The background worker runs on the server — it keeps drafting, sending and checking replies even when this page is closed.</p></div>' +
          '<div class="btn-row">' + (c.running ? '<button class="btn secondary" id="pause">Pause campaign</button>' : '<button class="btn" id="start">Start campaign</button>') + "</div></div>" +
          (c.email_mode !== "smtp" ? '<div class="notice warn"><strong>Test mode.</strong> No email account is connected, so "sent" emails are only recorded in Activity → Outbox. Connect one under <a href="#/connections">Connections</a> to send for real.</div>' : "") +
          '<div class="grid two"><div class="card" id="campStatus"></div>' +
          '<div class="card"><h3>How it runs</h3><ol class="steps">' +
            "<li>New contacts are drafted automatically (Claude, or templates without a key), using their specialty.</li>" +
            "<li>" + (camp.require_approval ? "Drafts wait in <a href=\"#/review\">Review</a> until you approve them." : "Drafts that pass the pre-send check are approved automatically.") + "</li>" +
            "<li>Approved emails go out one at a time, " + camp.min_delay_seconds + "–" + (camp.min_delay_seconds + camp.jitter_seconds) + " s apart" + (+camp.daily_cap ? ", at most " + camp.daily_cap + " a day" : "") + (camp.ist_window ? ", " + camp.window_start_hour + ":00–" + camp.window_end_hour + ":00 IST" : "") + ".</li>" +
            "<li>" + (camp.followups_enabled ? "No reply after " + camp.followup_after_days + " days → up to " + camp.max_followups + " short follow-up(s) in the same thread." : "Follow-ups are off.") + "</li>" +
            "<li>Replies, bounces and “unsubscribe” are picked up from your inbox every " + camp.inbox_poll_minutes + " min and stop further emails.</li>" +
            "<li>Each reply is sorted (interested, question, not now…), a suggested response waits in <a href=\"#/review\">Review</a> for your approval, and you get an alert email (set under <a href=\"#/connections\">Connections</a>).</li></ol></div></div>" +
          '<div class="card" style="margin-top:16px"><h3>Settings</h3><p class="sub">Sending from a single personal inbox works best at modest volume. For large campaigns, use a proper sending domain (SPF/DKIM/DMARC).</p>' +
            '<div class="field"><label class="check"><input type="checkbox" id="require_approval"' + (camp.require_approval ? " checked" : "") + "> Require my approval for every email <span class=\"muted\">(recommended, at least at first)</span></label></div>" +
            '<div class="field"><label class="check"><input type="checkbox" id="auto_draft"' + (camp.auto_draft ? " checked" : "") + "> Draft new contacts automatically</label></div>" +
            '<div class="field-row three"><div class="field"><label>Daily limit <span class="muted">(optional)</span></label><input type="number" min="0" id="daily_cap" value="' + (+camp.daily_cap || "") + '" placeholder="No limit"></div>' +
            '<div class="field"><label>Minimum gap between sends (s)</label><input type="number" min="0" id="min_delay_seconds" value="' + camp.min_delay_seconds + '"></div>' +
            '<div class="field"><label>Extra random gap, up to (s)</label><input type="number" min="0" id="jitter_seconds" value="' + camp.jitter_seconds + '"></div></div>' +
            '<div class="field"><label class="check"><input type="checkbox" id="ist_window"' + (camp.ist_window ? " checked" : "") + "> Only send inside the IST window</label></div>" +
            '<div class="field-row three"><div class="field"><label>Window opens (hour, IST)</label><input type="number" min="0" max="23" id="window_start_hour" value="' + camp.window_start_hour + '"></div>' +
            '<div class="field"><label>Window closes (hour, IST)</label><input type="number" min="1" max="24" id="window_end_hour" value="' + camp.window_end_hour + '"></div>' +
            '<div class="field"><label>Days</label><div class="btn-row" id="days">' + DAYS.map(function (d, i) { return '<label class="check" style="font-size:13px"><input type="checkbox" value="' + i + '"' + (camp.send_days.indexOf(i) > -1 ? " checked" : "") + "> " + d + "</label>"; }).join("") + "</div></div></div>" +
            '<div class="field"><label class="check"><input type="checkbox" id="followups_enabled"' + (camp.followups_enabled ? " checked" : "") + "> Send a follow-up when there's no reply</label></div>" +
            '<div class="field-row three"><div class="field"><label>Follow up after (days)</label><input type="number" min="1" id="followup_after_days" value="' + camp.followup_after_days + '"></div>' +
            '<div class="field"><label>Max follow-ups per person</label><input type="number" min="0" max="3" id="max_followups" value="' + camp.max_followups + '"></div>' +
            '<div class="field"><label>Check inbox every (min)</label><input type="number" min="1" id="inbox_poll_minutes" value="' + camp.inbox_poll_minutes + '"></div></div>' +
            '<div class="btn-row"><button class="btn" id="saveCamp">Save settings</button></div></div>';

        if ($("#start")) $("#start").onclick = function () { api("POST", "/campaign/start").then(function () { toast("Campaign started"); route(); refreshSide(); }).catch(fail); };
        if ($("#pause")) $("#pause").onclick = function () { api("POST", "/campaign/pause").then(function () { toast("Paused — nothing more will be sent"); route(); refreshSide(); }).catch(fail); };
        renderCampStatus(c);
        $("#saveCamp").onclick = function () {
          var patch = { send_days: $$("#days input:checked").map(function (i) { return +i.value; }), daily_cap: Math.max(0, +$("#daily_cap").value || 0) };
          ["require_approval", "auto_draft", "ist_window", "followups_enabled"].forEach(function (k) { patch[k] = $("#" + k).checked; });
          ["min_delay_seconds", "jitter_seconds", "window_start_hour", "window_end_hour", "followup_after_days", "max_followups", "inbox_poll_minutes"].forEach(function (k) { patch[k] = +$("#" + k).value; });
          api("PUT", "/settings/campaign", patch).then(function () { toast("Saved"); route(); }).catch(fail);
        };
      });
    },
    refresh: function () { return api("GET", "/campaign").then(renderCampStatus); },
  };

  function renderCampStatus(c) {
    var box = $("#campStatus");
    if (!box) return;
    box.innerHTML = "<h3>Status</h3>" + campaignSummary(c) +
      '<div class="btn-row" style="margin-top:14px"><button class="btn secondary small" id="tick">Process queue now</button><button class="btn secondary small" id="inbox">Check inbox now</button></div>';
        $("#tick").onclick = function () {
          busy(this, true);
          api("POST", "/campaign/tick").then(function (r) {
            var res = r.result || {}, parts = [];
            if (res.drafted) parts.push(res.drafted + " drafted");
            if (res.send && res.send.sent) parts.push("1 sent");
            else if (res.send && res.send.skipped) parts.push("send: " + res.send.skipped);
            else if (res.send && res.send.error) parts.push("send failed: " + res.send.error);
            toast(parts.join(" · ") || "Nothing to do");
            renderCampStatus(r.status); refreshSide();
          }).catch(function (e) { fail(e); busy($("#tick"), false); });
        };
        $("#inbox").onclick = function () {
          var b = this; busy(b, true);
          api("POST", "/campaign/check-inbox").then(function (r) {
            busy(b, false);
            if (r.skipped) toast(r.skipped); else if (r.error) toast(r.error, true);
            else toast("Inbox checked: " + r.reply + " replies, " + r.bounce + " bounces, " + r.unsubscribe + " opt-outs");
          }).catch(function (e) { fail(e); busy(b, false); });
        };
  }

  /* ------------------------------------------------------------------ activity */
  var activityTab = "events";
  pages.activity = {
    render: function (el) {
      el.innerHTML = '<div class="page-head"><div><h2>Activity</h2><p>Everything the system has done, every email sent, everything that came back, and who asked not to be contacted.</p></div></div>' +
        '<div class="tabs-inline" id="actTabs">' + [["events", "Log"], ["outbox", "Outbox"], ["inbox", "Inbox"], ["suppression", "Opt-out list"]].map(function (t) {
          return '<button data-t="' + t[0] + '" class="' + (activityTab === t[0] ? "active" : "") + '">' + t[1] + "</button>";
        }).join("") + '</div><div id="actBody"></div>';
      $$("#actTabs button").forEach(function (b) { b.onclick = function () { activityTab = b.dataset.t; pages.activity.render(el); }; });
      return loadActivity();
    },
    refresh: function () { return loadActivity(); },
  };
  function loadActivity() {
    var body = $("#actBody");
    if (activityTab === "events") return api("GET", "/activity").then(function (rows) {
      body.innerHTML = rows.length ? '<div class="table-wrap"><table><thead><tr><th>When</th><th>What</th><th>Who</th><th></th></tr></thead><tbody>' + rows.map(function (e) {
        return "<tr><td style=\"white-space:nowrap\">" + fmtTime(e.ts) + "</td><td>" + esc(e.message) + '</td><td class="muted">' + esc(e.actor || "") + "</td><td>" + (e.level !== "info" ? badge(e.level, e.level) : badge("", e.kind)) + "</td></tr>";
      }).join("") + "</tbody></table></div>" : '<div class="card">' + emptyState("activity", "No activity yet", "Everything the system does is logged here.") + "</div>";
    });
    if (activityTab === "outbox") return api("GET", "/outbox").then(function (rows) {
      body.innerHTML = rows.length ? '<div class="table-wrap"><table><thead><tr><th>Sent</th><th>To</th><th>Subject</th><th>Via</th></tr></thead><tbody>' + rows.map(function (m) {
        return '<tr class="clickable" data-id="' + m.id + '"><td style="white-space:nowrap">' + fmtTime(m.sent_at) + '</td><td class="name-cell"><strong>' + esc(m.contact_name) + "</strong><span>" + esc(m.contact_email) + "</span></td><td>" + esc(m.subject) + (m.kind === "followup" ? " " + badge("info", "Follow-up") : "") + "</td><td>" + (m.transport === "dry_run" ? badge("warn", "Test mode") : badge("sent", "Delivered")) + "</td></tr>";
      }).join("") + "</tbody></table></div>" : '<div class="card">' + emptyState("mail", "Nothing sent yet", "Approved emails appear here once the campaign sends them.") + "</div>";
      $$("#actBody tr[data-id]").forEach(function (tr) { tr.onclick = function () { previewMessage(+tr.dataset.id); }; });
    });
    if (activityTab === "inbox") return api("GET", "/inbox").then(function (rows) {
      body.innerHTML = rows.length ? '<div class="table-wrap"><table><thead><tr><th>Received</th><th>From</th><th>Kind</th><th>What they wrote</th></tr></thead><tbody>' + rows.map(function (i) {
        return "<tr><td style=\"white-space:nowrap\">" + fmtTime(i.received_at) + '</td><td class="name-cell"><strong>' + esc(i.contact_name || "—") + "</strong><span>" + esc(i.from_addr) + "</span></td><td>" + badge(i.kind, i.kind.replace("_", " ")) + '</td><td class="muted">' + esc(i.snippet) + "</td></tr>";
      }).join("") + "</tbody></table></div>" : '<div class="card">' + emptyState("inbox", "No replies yet", 'Connect your inbox under <a href="#/connections">Connections</a> to detect replies, bounces and opt-outs automatically.') + "</div>";
    });
    return api("GET", "/suppression").then(function (rows) {
      body.innerHTML = '<div class="card" style="margin-bottom:16px"><h3>Add to the opt-out list</h3><p class="sub">Anyone here is never emailed and can\'t be re-imported (DPDP Act — withdrawal of consent). Replies containing “unsubscribe” are added automatically.</p>' +
        '<div class="btn-row"><input type="email" id="supEmail" placeholder="name@example.in" style="max-width:320px"><button class="btn secondary" id="supAdd">Add</button></div></div>' +
        (rows.length ? '<div class="table-wrap"><table><thead><tr><th>Email</th><th>Reason</th><th>Since</th><th></th></tr></thead><tbody>' + rows.map(function (r) {
          return "<tr><td>" + esc(r.email) + "</td><td>" + badge(r.reason === "bounced" ? "bounced" : "unsubscribed", r.reason) + "</td><td>" + fmtTime(r.created_at) + '</td><td style="text-align:right"><button class="btn danger small" data-e="' + esc(r.email) + '">Remove</button></td></tr>';
        }).join("") + "</tbody></table></div>" : '<div class="card">' + emptyState("shield", "Nobody has opted out", "") + "</div>");
      $("#supAdd").onclick = function () {
        var e = $("#supEmail").value.trim();
        if (!e) return;
        api("POST", "/suppression", { email: e }).then(function () { toast("Added"); loadActivity(); }).catch(fail);
      };
      $$("[data-e]", body).forEach(function (b) {
        b.onclick = function () {
          if (!confirm("Remove " + b.dataset.e + " from the opt-out list? Only do this if they clearly asked to hear from you again.")) return;
          api("DELETE", "/suppression/" + encodeURIComponent(b.dataset.e)).then(function () { toast("Removed"); loadActivity(); }).catch(fail);
        };
      });
    });
  }

  /* ------------------------------------------------------------------ setup */
  var PITCH_INFO = {
    doctor: ["Doctors", "What Dr.Tracker does for a doctor, in one or two sentences. This only ever appears in the last paragraph — the email opens with a scene from their specialty.", "e.g. Patients check in between visits with symptoms and readings, and you get a quiet flag when something needs a look."],
    hospital: ["Hospitals", "What a pilot across the hospital's doctors would look like.", "e.g. A simple way for doctors across departments to stay in touch with discharged patients, without adding a task to anyone's day."],
    company: ["Companies (networks, insurers, chains)", "What bringing Dr.Tracker to their doctors offers the organisation.", "e.g. Gives the doctors in your network a simple way to stay connected to patients between appointments."],
    partner: ["Product partners", "What a partnership offers a company with a product (device, test, app). Keep it honest: doctors choose independently what their patients use — never promise recommendations.", "e.g. Readings from your product reach the doctor already following that patient, inside Dr.Tracker."],
  };
  pages.setup = {
    render: function (el) {
      return loadSettings().then(function (s) {
        var sd = s.sender, mk = s.marketplace;
        el.innerHTML =
          '<div class="page-head"><div><h2>Setup</h2><p>Who the emails come from and what you want to say. Every draft uses this.</p></div></div>' +
          '<div class="card"><h3>Sender</h3><p class="sub">Shown in the sign-off and the compliance footer.</p>' +
            '<div class="field-row"><div class="field"><label>Your name</label><input type="text" id="s_name" value="' + esc(sd.name) + '" placeholder="Ananya Gupta"></div>' +
            '<div class="field"><label>Your role</label><input type="text" id="s_role" value="' + esc(sd.role) + '" placeholder="Founder, Dr.Tracker"></div></div>' +
            '<div class="field-row"><div class="field"><label>Your email</label><input type="email" id="s_email" value="' + esc(sd.email) + '" placeholder="you@dr-tracker.com"></div>' +
            '<div class="field"><label>Phone (for the signature)</label><input type="text" id="s_phone" value="' + esc(sd.phone) + '" placeholder="+91 98765 43210"></div></div>' +
            '<div class="field-row"><div class="field"><label>Tone</label><select id="s_tone">' + [["warm", "Warm & personal"], ["direct", "Direct & brief"], ["formal", "Formal & clinical"]].map(function (o) { return '<option value="' + o[0] + '"' + (sd.tone === o[0] ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") + "</select></div>" +
            '<div class="field"><label>Language</label><select id="s_language">' + [["english", "English"], ["hinglish", "Hinglish (light Hindi touches)"], ["hindi", "Hindi (हिन्दी)"]].map(function (o) { return '<option value="' + o[0] + '"' + (sd.language === o[0] ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") + "</select></div></div>" +
            '<div class="field"><label>Registered / business address</label><input type="text" id="s_address" value="' + esc(sd.address) + '" placeholder="Dr.Tracker Technologies Pvt Ltd, HSR Layout, Bengaluru 560102"><p class="hint">Required. Goes in the footer of every email with your identity and an opt-out line (IT Act 2000, DPDP Act 2023).</p></div>' +
            '<div class="btn-row"><button class="btn" id="saveSender">Save sender</button></div></div>' +
          '<div class="card" id="marketplaceCard"><h3>Marketplace pitch — brands &amp; professionals</h3><p class="sub">The wording every brand and health-professional email is built from, with Claude or without. Change it here; no code changes needed.</p>' +
            '<div class="field"><label>Value proposition — brands</label><textarea rows="3" id="m_value_prop_brands">' + esc(mk.value_prop_brands) + "</textarea></div>" +
            '<div class="field"><label>Value proposition — professionals</label><textarea rows="3" id="m_value_prop_professionals">' + esc(mk.value_prop_professionals) + "</textarea></div>" +
            '<div class="field"><label class="check"><input type="checkbox" id="m_mention_trial"' + (mk.mention_trial ? " checked" : "") + '> Mention the trial offer</label><p class="hint">Off: emails never mention prices, fees, commission or a trial (the pre-send check blocks them). On: the trial text below is the only offer allowed. Doctors and clinics never get commission or per-booking language either way.</p></div>' +
            '<div class="field"><label>Trial text</label><input type="text" id="m_trial_text" value="' + esc(mk.trial_text) + '"></div>' +
            '<div class="field-row"><div class="field"><label>Signature name</label><input type="text" id="m_signature_name" value="' + esc(mk.signature_name) + '" placeholder="' + esc(sd.name || "Same as sender") + '"></div>' +
            '<div class="field"><label>Signature role</label><input type="text" id="m_signature_role" value="' + esc(mk.signature_role) + '" placeholder="' + esc(sd.role || "Same as sender") + '"></div></div>' +
            '<p class="hint">Leave the signature blank to use the sender details above.</p>' +
            '<div class="field"><label>Meeting link</label><input type="url" id="m_meeting_link" value="' + esc(mk.meeting_link || "") + '" placeholder="https://calendar.app.google/…"><p class="hint">Ends the ask in emails written to a template (the brand guide’s <code>{Meeting link}</code>). Blank: the ask ends with a question instead.</p></div>' +
            '<div class="btn-row"><button class="btn" id="saveMarket">Save marketplace pitch</button></div></div>' +
          '<div class="card" id="templatesCard"><h3>Email templates</h3><p class="sub">Brands and professionals each get their own template: a writing guide that Claude follows for the first email, and images placed inside the email body where the draft’s <code>[SCREENSHOT_1]</code>… markers (or <code>[HOW_IT_WORKS_IMAGE]</code>) are. Choose which one a spreadsheet uses when you upload it.</p><div id="templatesBody" class="muted">Loading…</div></div>' +
          '<div class="card" id="usersCard"><h3>Users</h3><p class="sub">Everyone who reviews or sends gets their own sign-in, and the Activity log records who approved and sent each email. Once the first account exists, signing in is required.</p><div id="usersList" class="muted">Loading…</div></div>' +
          '<div class="card"><h3>Never import</h3><p class="sub">Rows whose Name or Notes contain any of these words are skipped on import and listed in the summary. Crisis lines and government helplines must never get marketing email.</p>' +
            '<div class="field"><label>One per line (whole words, any case)</label><textarea rows="5" id="i_blocked">' + esc((s["import"].blocked_patterns || []).join("\n")) + "</textarea></div>" +
            '<div class="btn-row"><button class="btn secondary" id="saveImport">Save</button></div></div>' +
          Object.keys(PITCH_INFO).map(function (t) {
            var p = s.pitches[t], info = PITCH_INFO[t];
            return '<div class="card"><h3>Pitch — ' + info[0] + '</h3><p class="sub">' + info[1] + "</p>" +
              '<div class="field"><label>In a sentence or two</label><textarea rows="3" id="p_' + t + '" placeholder="' + esc(info[2]) + '">' + esc(p.pitch) + "</textarea></div>" +
              '<div class="field"><label>Why it matters to them <span class="muted">(one per line, optional — only things you can back up)</span></label><textarea rows="3" id="pp_' + t + '">' + esc((p.props || []).join("\n")) + "</textarea></div>" +
              '<div class="btn-row"><button class="btn secondary" data-pitch="' + t + '">Save</button></div></div>';
          }).join("") +
          '<div class="card"><h3>Rules every email follows</h3><p class="sub">Written into the drafting instructions and checked again before anything is sent.</p><ul class="rules">' +
            "<li>" + ICON.shield + "<span><strong>ASCI Code</strong> — truthful, substantiated claims; no “best / No. 1 / India's first”; no fear-mongering.</span></li>" +
            "<li>" + ICON.shield + "<span><strong>CCPA misleading-ads (2022) &amp; dark-pattern (2023) guidelines</strong> — no false urgency or scarcity; honest subjects, never a fake “Re:”.</span></li>" +
            "<li>" + ICON.shield + "<span><strong>Drugs &amp; Magic Remedies Act 1954</strong> — no cure, prevention or guaranteed-outcome claims; Dr.Tracker doesn't diagnose or treat.</span></li>" +
            "<li>" + ICON.shield + "<span><strong>NMC Professional Conduct Regulations 2023 &amp; UCPMP 2024</strong> — no gifts, fees or incentives to doctors; never ask a doctor to endorse or prescribe.</span></li>" +
            "<li>" + ICON.shield + "<span><strong>DPDP Act 2023</strong> — hypothetical scenes only, no patient data; clear identity and one-reply opt-out, honoured automatically.</span></li>" +
            "<li>" + ICON.shield + "<span><strong>IT Act 2000</strong> — honest sender identity and headers.</span></li></ul></div>";
        $("#saveSender").onclick = function () {
          var patch = {};
          ["name", "role", "email", "phone", "tone", "language", "address"].forEach(function (k) { patch[k] = $("#s_" + k).value.trim(); });
          api("PUT", "/settings/sender", patch).then(function (r) { settingsCache = r; toast("Saved"); }).catch(fail);
        };
        $("#saveMarket").onclick = function () {
          var patch = { mention_trial: $("#m_mention_trial").checked };
          ["value_prop_brands", "value_prop_professionals", "trial_text", "signature_name", "signature_role", "meeting_link"].forEach(function (k) { patch[k] = $("#m_" + k).value.trim(); });
          api("PUT", "/settings/marketplace", patch).then(function (r) { settingsCache = r; toast("Saved"); }).catch(fail);
        };
        loadUsers();
        loadTemplates();
        $("#saveImport").onclick = function () {
          var list = $("#i_blocked").value.split("\n").map(function (x) { return x.trim(); }).filter(Boolean);
          api("PUT", "/settings/import", { blocked_patterns: list }).then(function (r) { settingsCache = r; toast("Saved"); }).catch(fail);
        };
        $$("[data-pitch]").forEach(function (b) {
          b.onclick = function () {
            var t = b.dataset.pitch, patch = {};
            patch[t] = { pitch: $("#p_" + t).value.trim(), props: $("#pp_" + t).value.split("\n").map(function (x) { return x.trim(); }).filter(Boolean) };
            api("PUT", "/settings/pitches", patch).then(function (r) { settingsCache = r; toast("Saved"); }).catch(fail);
          };
        });
      });
    },
  };

  function loadTemplates() {
    api("GET", "/templates").then(function (t) {
      templateCache = t;
      var src = { custom: "your version", "built-in": "built in" };
      $("#templatesBody").innerHTML = ["brand", "professional"].map(function (a) {
        var x = t[a], label = a === "brand" ? "Brands" : "Professionals";
        return '<div class="template-block"><h4>' + label + "</h4>" +
          '<div class="field"><label>Writing guide <span class="muted">(' + (x.guide_source ? src[x.guide_source] : "none yet: the built-in marketplace pitch is used") + ')</span></label>' +
            '<textarea rows="8" id="tg_' + a + '" placeholder="Paste the guide (Markdown) here.">' + esc(x.guide) + "</textarea>" +
            '<div class="btn-row"><button class="btn secondary small" data-tg="' + a + '">Save guide</button>' +
            (x.guide_source === "custom" ? '<button class="btn ghost small" data-treset="' + a + '/guide">' + (x.guide_builtin ? "Reset to built-in" : "Remove") + "</button>" : "") + "</div></div>" +
          '<div class="field"><label>' + ((x.images || []).length > 1 ? "Images" : "Banner") + ' <span class="muted">(' + (x.banner_source ? src[x.banner_source] + ((x.images || []).length > 1 ? ", " + x.images.length + " in this order" : "") + ", sent at " + x.banner_dims[0] + "×" + x.banner_dims[1] + ", " + Math.round(x.banner_size / 1024) + " KB" : "none yet: emails go out as plain text") + ")</span></label>" +
            (x.banner_error ? '<div class="notice warn">' + esc(x.banner_error) + "</div>" : "") +
            imageThumbs(a, x) +
            '<div class="btn-row"><label class="btn secondary small" style="cursor:pointer">Upload banner<input type="file" accept=".png,.jpg,.jpeg,.gif" data-tb="' + a + '" hidden></label>' +
            (x.banner_source === "custom" ? '<button class="btn ghost small" data-treset="' + a + '/banner">' + (x.banner_builtin ? "Reset to built-in" : "Remove") + "</button>" : "") + "</div>" +
            '<p class="hint">PNG or JPG. It’s resized to 1200 px wide for email; the original is kept.' + ((x.images || []).length > 1 && x.banner_source === "built-in" ? " An uploaded banner replaces the built-in images." : "") + "</p></div></div>";
      }).join("");
      $$("[data-tg]").forEach(function (b) { b.onclick = function () {
        api("PUT", "/templates/" + b.dataset.tg + "/guide", { text: $("#tg_" + b.dataset.tg).value }).then(function () { toast("Guide saved"); loadTemplates(); }).catch(fail);
      }; });
      $$("[data-treset]").forEach(function (b) { b.onclick = function () {
        if (!confirm("Remove your version? The built-in one (if any) is used again.")) return;
        api("DELETE", "/templates/" + b.dataset.treset).then(function () { toast("Reset"); loadTemplates(); }).catch(fail);
      }; });
      $$("[data-tb]").forEach(function (input) { input.onchange = function () {
        var f = input.files[0];
        if (!f) return;
        var fd = new FormData();
        fd.append("file", f);
        api("POST", "/templates/" + input.dataset.tb + "/banner", fd, true).then(function () { toast("Banner uploaded"); loadTemplates(); }).catch(fail);
      }; });
    }).catch(fail);
  }

  function loadUsers() {
    var me = authState && authState.user;
    var isAdmin = !me || me.role === "admin";
    var form = '<h4 style="margin:16px 0 8px">' + (authState && authState.users_exist ? "Add a user" : "Create the first account (an admin)") + "</h4>" +
      '<div class="field-row"><div class="field"><label>Email</label><input type="email" id="u_email"></div><div class="field"><label>Name</label><input type="text" id="u_name"></div></div>' +
      '<div class="field-row"><div class="field"><label>Password (10+ characters)</label><input type="password" id="u_password" autocomplete="new-password"></div>' +
      '<div class="field"><label>Role</label><select id="u_role"><option value="member">Member</option><option value="admin">Admin (can manage users)</option></select></div></div>' +
      '<div class="btn-row"><button class="btn secondary" id="u_add">Add user</button></div>';
    var pw = me ? '<h4 style="margin:16px 0 8px">Change your password</h4><div class="field-row"><div class="field"><label>Current</label><input type="password" id="pw_current" autocomplete="current-password"></div>' +
      '<div class="field"><label>New (10+ characters)</label><input type="password" id="pw_new" autocomplete="new-password"></div></div><div class="btn-row"><button class="btn secondary" id="pw_save">Change password</button></div>' : "";
    var render = function (users) {
      $("#usersList").innerHTML = (users.length ? '<div class="table-wrap"><table><thead><tr><th>Email</th><th>Name</th><th>Role</th><th>Last sign-in</th><th></th></tr></thead><tbody>' + users.map(function (u) {
        return "<tr><td>" + esc(u.email) + "</td><td>" + esc(u.name) + "</td><td>" + esc(u.role) + "</td><td>" + (u.last_login_at ? fmtTime(u.last_login_at) : "—") + "</td><td>" +
          (isAdmin && (!me || me.id !== u.id) ? '<button class="btn ghost small" data-del="' + u.id + '">Remove</button>' : "") + "</td></tr>";
      }).join("") + "</tbody></table></div>" : '<p class="muted">No accounts yet.</p>') + (isAdmin ? form : "") + pw;
      if ($("#u_add")) $("#u_add").onclick = function () {
        api("POST", "/users", { email: $("#u_email").value.trim(), name: $("#u_name").value.trim(), password: $("#u_password").value, role: $("#u_role").value })
          .then(function () { toast("User added"); if (!authState.users_exist) { location.reload(); return; } loadUsers(); }).catch(fail);
      };
      $$("[data-del]").forEach(function (b) { b.onclick = function () {
        if (!confirm("Remove this user? They'll be signed out.")) return;
        api("DELETE", "/users/" + b.dataset.del).then(function () { toast("Removed"); loadUsers(); }).catch(fail);
      }; });
      if ($("#pw_save")) $("#pw_save").onclick = function () {
        api("POST", "/auth/password", { current: $("#pw_current").value, new: $("#pw_new").value }).then(function () { toast("Password changed — sign in again"); setTimeout(function () { location.reload(); }, 1200); }).catch(fail);
      };
    };
    if (!isAdmin) { render(me ? [me] : []); return; }
    api("GET", "/users").then(render).catch(fail);
  }

  /* ------------------------------------------------------------------ connections */
  var PRESETS = {
    gmail: { label: "Gmail / Google Workspace", smtp_host: "smtp.gmail.com", smtp_port: 465, smtp_security: "ssl", imap_host: "imap.gmail.com", imap_port: 993 },
    zoho: { label: "Zoho Mail (India)", smtp_host: "smtppro.zoho.in", smtp_port: 465, smtp_security: "ssl", imap_host: "imappro.zoho.in", imap_port: 993 },
    outlook: { label: "Outlook / Microsoft 365", smtp_host: "smtp.office365.com", smtp_port: 587, smtp_security: "starttls", imap_host: "outlook.office365.com", imap_port: 993 },
  };
  function secretInput(id, set, hint, placeholder) {
    return '<div class="secret-row"><input type="password" id="' + id + '" autocomplete="new-password" placeholder="' + esc(set ? "Saved (" + hint + ") — leave blank to keep" : placeholder) + '">' + (set ? '<button class="btn ghost small" data-clear="' + id + '">Remove</button>' : "") + "</div>";
  }
  pages.connections = {
    render: function (el) {
      return loadSettings().then(function (s) {
        var ai = s.ai, e = s.email;
        el.innerHTML =
          '<div class="page-head"><div><h2>Connections</h2><p>Paste your keys here and the system does the rest. Keys are stored only in this server\'s local database and are never shown again. Anything set in the <code>.env</code> file takes priority and is locked here.</p></div></div>' +
          '<div class="card"><h3>Claude (AI drafting)</h3><p class="sub">With a key, every email is written fresh for the person, their specialty and your pitch. Without one, the built-in templates are used. Get a key at console.anthropic.com → API keys.</p>' +
            '<div class="field"><label>Anthropic API key</label>' + secretInput("ai_key", ai.anthropic_api_key_set, ai.anthropic_api_key_hint, "sk-ant-…") + "</div>" +
            '<div class="field-row"><div class="field"><label>Model</label><select id="ai_model">' + [["claude-opus-5-5", "Claude Opus 5.5 (best writing)"], ["claude-sonnet-5-5", "Claude Sonnet 5.5 (faster, cheaper)"], ["claude-haiku-4-5", "Claude Haiku 4.5 (cheapest)"]].map(function (o) { return '<option value="' + o[0] + '"' + (ai.model === o[0] ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") + "</select></div>" +
            '<div class="field"><label>Effort</label><select id="ai_effort">' + [["low", "Low"], ["medium", "Medium (recommended)"], ["high", "High"]].map(function (o) { return '<option value="' + o[0] + '"' + (ai.effort === o[0] ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") + '</select><p class="hint">Haiku ignores this setting.</p></div></div>' +
            '<div class="field"><label class="check"><input type="checkbox" id="ai_research"' + (ai.website_research ? " checked" : "") + "> Read each brand's or professional's website before writing their first email</label><p class=\"hint\">Uses their Website column, or their email domain. Claude uses it only to be specific about what they do.</p></div>" +
            '<div class="field"><label class="check"><input type="checkbox" id="ai_replies"' + (ai.reply_drafts ? " checked" : "") + "> When someone replies, sort the reply and draft a response for my approval</label></div>" +
            '<div class="btn-row"><button class="btn" id="saveAi">Save</button><button class="btn secondary" id="testAi">Test</button></div><div class="test-result" id="aiResult"></div></div>' +

          '<div class="card"><h3>Sending email</h3><p class="sub">Uses your own mailbox over SMTP. For Gmail or Google Workspace, turn on 2-Step Verification and create an <strong>App Password</strong> (Google Account → Security → App passwords) — paste that here, not your normal password. For real volume, point this at a transactional provider\'s SMTP (Amazon SES, Postmark, Brevo…) on your own domain with SPF, DKIM and DMARC set up.</p>' +
            '<div class="field"><label>Mode</label><div class="btn-row"><label class="check"><input type="radio" name="mode" value="dry_run"' + (e.mode !== "smtp" ? " checked" : "") + "> Test mode — record emails, send nothing</label>" +
            '<label class="check"><input type="radio" name="mode" value="smtp"' + (e.mode === "smtp" ? " checked" : "") + "> Live — send through SMTP</label></div></div>" +
            '<div class="field"><label>Provider preset</label><div class="btn-row">' + Object.keys(PRESETS).map(function (k) { return '<button class="btn secondary small" data-preset="' + k + '">' + PRESETS[k].label + "</button>"; }).join("") + "</div></div>" +
            '<div class="field-row three"><div class="field"><label>SMTP host</label><input type="text" id="smtp_host" value="' + esc(e.smtp_host) + '"></div>' +
            '<div class="field"><label>Port</label><input type="number" id="smtp_port" value="' + esc(e.smtp_port) + '"></div>' +
            '<div class="field"><label>Security</label><select id="smtp_security">' + [["ssl", "SSL/TLS"], ["starttls", "STARTTLS"], ["none", "None"]].map(function (o) { return '<option value="' + o[0] + '"' + (e.smtp_security === o[0] ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") + "</select></div></div>" +
            '<div class="field-row"><div class="field"><label>Username</label><input type="text" id="smtp_username" value="' + esc(e.smtp_username) + '" placeholder="you@dr-tracker.com"></div>' +
            '<div class="field"><label>Password / app password</label>' + secretInput("smtp_password", e.smtp_password_set, e.smtp_password_hint, "16-character app password") + "</div></div>" +
            '<div class="field-row three"><div class="field"><label>From address</label><input type="email" id="from_email" value="' + esc(e.from_email) + '" placeholder="Same as username"></div>' +
            '<div class="field"><label>From name</label><input type="text" id="from_name" value="' + esc(e.from_name) + '" placeholder="Your name from Setup"></div>' +
            '<div class="field"><label>Reply-to <span class="muted">(optional)</span></label><input type="email" id="reply_to" value="' + esc(e.reply_to) + '"></div></div>' +
            '<div class="btn-row"><button class="btn" id="saveMail">Save</button><button class="btn secondary" id="testSmtp">Test login</button><span class="spacer"></span><input type="email" id="testTo" placeholder="Send a test to…" style="max-width:240px"><button class="btn secondary" id="testSend">Send test</button></div><div class="test-result" id="smtpResult"></div></div>' +

          '<div class="card"><h3>Reading replies (IMAP)</h3><p class="sub">Lets the system notice replies, bounces and “unsubscribe” on its own, stop follow-ups, and keep the opt-out list current. Nothing is marked as read. For Gmail, IMAP is on by default; the same app password works.</p>' +
            '<div class="field"><label class="check"><input type="checkbox" id="imap_enabled"' + (e.imap_enabled ? " checked" : "") + "> Check my inbox for replies</label></div>" +
            '<div class="field-row three"><div class="field"><label>IMAP host</label><input type="text" id="imap_host" value="' + esc(e.imap_host) + '"></div>' +
            '<div class="field"><label>Port</label><input type="number" id="imap_port" value="' + esc(e.imap_port) + '"></div>' +
            '<div class="field"><label>Folder</label><input type="text" id="imap_folder" value="' + esc(e.imap_folder) + '"></div></div>' +
            '<div class="field-row"><div class="field"><label>Username <span class="muted">(blank = same as SMTP)</span></label><input type="text" id="imap_username" value="' + esc(e.imap_username) + '"></div>' +
            '<div class="field"><label>Password <span class="muted">(blank = same as SMTP)</span></label>' + secretInput("imap_password", e.imap_password_set, e.imap_password_hint, "") + "</div></div>" +
            '<div class="field"><label>Email me when someone replies</label><input type="email" id="alert_to" value="' + esc(e.alert_to || "") + '" placeholder="founder@dr-tracker.com"><p class="hint">Use a person\'s own inbox, not the sending mailbox. You also get an alert if the inbox stops being readable. Blank = no alerts.</p></div>' +
            '<div class="btn-row"><button class="btn" id="saveImap">Save</button><button class="btn secondary" id="testImap">Test</button></div><div class="test-result" id="imapResult"></div></div>';

        // Fields set in .env win over anything saved here, so lock them and say where they come from.
        var ENV_INPUTS = { anthropic_api_key: "ai_key", model: "ai_model" };
        (ai.from_env || []).concat(e.from_env || []).forEach(function (field) {
          var inputs = field === "mode" ? $$('input[name="mode"]') : [$("#" + (ENV_INPUTS[field] || field))];
          inputs.forEach(function (input) {
            if (!input) return;
            input.disabled = true;
            if (input.type === "password") input.placeholder = "Set in .env";
          });
          var box = inputs[0] && inputs[0].closest(".field");
          if (!box) return;
          var clear = box.querySelector("[data-clear]");
          if (clear) clear.remove();
          var note = document.createElement("p");
          note.className = "hint";
          note.textContent = "Set in .env. Edit the file and restart the app to change it.";
          box.appendChild(note);
        });
        function result(id, r) { var el2 = $("#" + id); el2.className = "test-result " + (r.ok ? "ok" : "fail"); el2.textContent = (r.ok ? "✓ " : "✗ ") + r.message; }
        function testing(id) { var el2 = $("#" + id); el2.className = "test-result"; el2.textContent = "Testing…"; }
        $$("[data-clear]").forEach(function (b) {
          b.onclick = function () {
            if (!confirm("Remove the saved secret?")) return;
            var map = { ai_key: ["ai", "clear_anthropic_api_key"], smtp_password: ["email", "clear_smtp_password"], imap_password: ["email", "clear_imap_password"] }[b.dataset.clear];
            var patch = {}; patch[map[1]] = true;
            api("PUT", "/settings/" + map[0], patch).then(function () { toast("Removed"); route(); }).catch(fail);
          };
        });
        $("#saveAi").onclick = function () {
          api("PUT", "/settings/ai", { anthropic_api_key: $("#ai_key").value.trim(), model: $("#ai_model").value, effort: $("#ai_effort").value, website_research: $("#ai_research").checked, reply_drafts: $("#ai_replies").checked }).then(function () { toast("Saved"); route(); refreshSide(); }).catch(fail);
        };
        $("#testAi").onclick = function () { testing("aiResult"); api("POST", "/settings/test/ai").then(function (r) { result("aiResult", r); }).catch(fail); };
        $$("[data-preset]").forEach(function (b) {
          b.onclick = function () {
            var p = PRESETS[b.dataset.preset];
            ["smtp_host", "smtp_port", "imap_host", "imap_port"].forEach(function (k) { $("#" + k).value = p[k]; });
            $("#smtp_security").value = p.smtp_security;
            toast(p.label + " settings filled in — add your username and app password, then Save");
          };
        });
        function mailPatch() {
          return {
            mode: $('input[name="mode"]:checked').value, smtp_host: $("#smtp_host").value.trim(), smtp_port: +$("#smtp_port").value, smtp_security: $("#smtp_security").value,
            smtp_username: $("#smtp_username").value.trim(), smtp_password: $("#smtp_password").value, from_email: $("#from_email").value.trim(), from_name: $("#from_name").value.trim(),
            reply_to: $("#reply_to").value.trim(), imap_host: $("#imap_host").value.trim(), imap_port: +$("#imap_port").value,
          };
        }
        $("#saveMail").onclick = function () {
          var p = mailPatch();
          if (p.mode === "smtp" && !confirm("Live mode: approved emails will really be sent from " + (p.from_email || p.smtp_username || "this account") + ". Continue?")) return;
          api("PUT", "/settings/email", p).then(function () { toast("Saved"); route(); refreshSide(); }).catch(fail);
        };
        $("#testSmtp").onclick = function () { testing("smtpResult"); api("PUT", "/settings/email", mailPatch()).then(function () { return api("POST", "/settings/test/smtp"); }).then(function (r) { result("smtpResult", r); }).catch(fail); };
        $("#testSend").onclick = function () {
          var to = $("#testTo").value.trim();
          if (!to) { toast("Enter an address to send the test to.", true); return; }
          testing("smtpResult");
          api("PUT", "/settings/email", mailPatch()).then(function () { return api("POST", "/settings/test/send", { to: to }); }).then(function (r) { result("smtpResult", r); }).catch(fail);
        };
        function imapPatch() { return { imap_enabled: $("#imap_enabled").checked, imap_host: $("#imap_host").value.trim(), imap_port: +$("#imap_port").value, imap_folder: $("#imap_folder").value.trim() || "INBOX", imap_username: $("#imap_username").value.trim(), imap_password: $("#imap_password").value, alert_to: $("#alert_to").value.trim() }; }
        $("#saveImap").onclick = function () { api("PUT", "/settings/email", imapPatch()).then(function () { toast("Saved"); route(); }).catch(fail); };
        $("#testImap").onclick = function () { testing("imapResult"); api("PUT", "/settings/email", imapPatch()).then(function () { return api("POST", "/settings/test/imap"); }).then(function (r) { result("imapResult", r); }).catch(fail); };
      });
    },
  };

  /* ------------------------------------------------------------------ sign-in */
  var authState = null;
  function showLogin() {
    if ($("#login")) return;
    $(".app").hidden = true;
    var wrap = document.createElement("div");
    wrap.id = "login";
    wrap.className = "login";
    wrap.innerHTML = '<div class="card"><div class="brand">' + $(".brand").innerHTML + '</div><h3>Sign in</h3><p class="muted" style="margin:0 0 20px">Welcome back. Sign in to continue.</p>' +
      '<form id="loginForm"><div class="field"><label>Email</label><input type="email" id="l_email" autocomplete="username" required autofocus></div>' +
      '<div class="field"><label>Password</label><input type="password" id="l_password" autocomplete="current-password" required></div>' +
      '<p class="hint" id="l_error" style="color:var(--danger)"></p>' +
      '<button class="btn" type="submit">Sign in</button></form>' +
      '<p class="hint" style="text-align:center;margin-top:16px">Forgot your password? Ask an admin to reset it.</p></div>';
    document.body.appendChild(wrap);
    $("#loginForm").onsubmit = function (e) {
      e.preventDefault();
      var b = $("#loginForm .btn");
      busy(b, true);
      api("POST", "/auth/login", { email: $("#l_email").value.trim(), password: $("#l_password").value })
        .then(function () { location.reload(); })
        .catch(function (err) { busy(b, false); $("#l_error").textContent = err.message; });
    };
  }
  function signOut() { api("POST", "/auth/logout").then(function () { location.reload(); }).catch(fail); }
  function authFoot() {
    var a = authState || {};
    var who = a.user ? '<div class="side-user"><span class="avatar">' + esc(initials(a.user.name || a.user.email)) + '</span><div class="who"><div class="name">' + esc(a.user.name || a.user.email.split("@")[0]) + "</div><div>" + esc(a.user.email) + '</div></div><a href="#" id="signOut" class="btn ghost small">Sign out</a></div>' : "";
    var warn = (a.warnings || []).map(function (w) { return '<div class="side-warn">' + esc(w) + "</div>"; }).join("");
    return warn + who;
  }

  /* ------------------------------------------------------------------ boot */
  api("GET", "/auth/state").then(function (a) {
    authState = a;
    if (a.users_exist && !a.user) { showLogin(); return; }
    api("GET", "/meta").then(function (m) { meta = m; }).catch(function () {});
    refreshSide();
    route();
  }).catch(fail);
})();
