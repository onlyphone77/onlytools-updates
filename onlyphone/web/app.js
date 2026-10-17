"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const NA = '<span class="na">Non disponible</span>';
const ICON = {
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3 2 21h20L12 3z"/><path d="M12 10v5M12 18h.01"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/></svg>',
  phone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M11 18h2"/></svg>',
};
let toolsState = {}, cfgDevs = null, lastFlash = null;
function renderLastFlash() {
  const f = lastFlash;
  let h = "";
  if (f && f.state === "en cours") h = alertBox(`Flash en cours : ${f.model || "iPhone"} — ${f.operation}. Ne débranchez pas l'iPhone.`, "info");
  else if (f && f.state && f.state !== "réussi") h = `<div class="alert bad">${ICON.warn}<div><b>Dernier flash : ${esc(f.state)}</b> (${esc(f.model || "iPhone")}, ${esc(f.operation)})<br>
     Erreur : ${esc(f.error || "inconnue")}${(f.explain || []).length ? "<br><b>Causes probables :</b>" + hypHtml(f.explain) : "<br>Cause non identifiée : réessayer en DFU avec un autre câble / port ; si l'erreur revient, notez son code dans l'assistant panne."}</div></div>`;
  else if (f && f.state === "réussi") h = alertBox(`Dernier flash réussi : ${f.model || "iPhone"} (${f.operation}).`, "info");
  ["d-flash", "f-last"].forEach((id) => { if ($(id)) $(id).innerHTML = h; });
}
let devices = [], selected = null, lastEventT = Date.now() / 1000, job = null, report = null, tests = [], busy = false;

async function api(path, body) {
  const r = await fetch(path, body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {});
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg) { const d = document.createElement("div"); d.textContent = msg; $("toast").appendChild(d); setTimeout(() => d.remove(), 5500); }
const label = (d) => [d.brand, d.model].filter(Boolean).join(" ") || "Appareil non identifié";
const STATE_CLS = {"reconnu": "ok", "partiellement reconnu": "warn", "autorisation nécessaire": "warn", "appareil non accessible": "bad", "appareil non identifié": "bad"};
const pill = (txt, cls) => `<span class="pill ${cls}">${esc(txt)}</span>`;
const alertBox = (txt, cls) => `<div class="alert ${cls}">${ICON[cls === "info" ? "info" : "warn"]}<div>${esc(txt)}</div></div>`;

// ---------- Détection automatique ----------
async function poll() {
  try {
    const st = await api("/api/status");
    for (const e of st.events) if (e.t > lastEventT) { toast(e.label); lastEventT = e.t; }
    devices = st.devices; lastFlash = st.last_flash; renderLastFlash();
    if (!devices.find((d) => d.key === selected)) { selected = devices.length ? devices[devices.length - 1].key : null; resetResults(); }
    const t = st.tools || {}; toolsState = t;
    $("toolsinfo").innerHTML = `<div class="tool"><i><span class="dot ${t.adb ? "on" : "off"}"></span>Android (ADB)</i><i>${t.adb ? "prêt" : "absent"}</i></div>
      <div class="tool"><i><span class="dot ${t.libimobiledevice ? "on" : "off"}"></span>Apple (iPhone/iPad)</i><i>${t.libimobiledevice ? "prêt" : "absent"}</i></div>`;
    renderList(); renderDevice();
  } catch (e) { $("toolsinfo").textContent = "Service local injoignable."; }
}
function renderList() {
  $("devlist").innerHTML = devices.map((d) => `<div class="dev ${d.key === selected ? "sel" : ""}" data-k="${esc(d.key)}">
    <div class="ic">${ICON.phone}</div><div><b>${esc(label(d))}</b><small>${esc(d.state)}</small></div></div>`).join("")
    || '<p class="note" style="padding:0 10px">Aucun appareil.</p>';
  document.querySelectorAll(".dev").forEach((el) => el.onclick = () => { selected = el.dataset.k; resetResults(); renderList(); renderDevice(); });
}
const kv = (k, v, copy) => `<div class="kv"><span>${esc(k)}</span><b class="${copy && v ? "copy" : ""}" ${copy && v ? `data-copy="${esc(v)}" title="Cliquer pour copier"` : ""}>${v == null || v === "" ? NA : esc(v)}</b></div>`;
const DETAILS = [["imei", "IMEI", 1], ["imei2", "IMEI 2", 1], ["meid", "MEID", 1], ["serial_number", "N° de série", 1], ["model_number", "Réf. commerciale"],
  ["origin", "Origine"], ["hardware_model", "Carte (modèle matériel)"], ["activation", "Activation"], ["baseband", "Modem"], ["passcode", "Code de verrouillage"],
  ["ram", "RAM"], ["bootloader", "Bootloader"], ["wifi_mac", "Adresse Wi-Fi"], ["bt_mac", "Adresse Bluetooth"]];

function renderDevice() {
  const d = devices.find((x) => x.key === selected);
  $("nodevice").classList.toggle("hidden", !!d); $("device").classList.toggle("hidden", !d);
  if (!d) return;
  $("phone-art").className = "phone-art " + (d.platform === "android" ? "android" : "");
  $("d-title").textContent = label(d);
  $("d-sub").textContent = [d.os && d.os + " " + (d.os_version || ""), d.model_code, d.os_detail].filter(Boolean).join(" · ");
  const det = d.details || {};
  $("d-chips").innerHTML = pill(d.state, STATE_CLS[d.state] || "info") +
    pill({adb: "USB · Android", lockdown: "USB · Apple", usb: "USB (bas niveau)"}[d.transport] || "USB", "gold") +
    (det.origin ? pill(det.origin, det.origin.startsWith("Neuf") ? "ok" : "warn") : "") +
    (d.usb && d.usb.special_mode ? pill(d.usb.special_mode, "bad") : "") +
    (d.connected_since ? pill("branché à " + new Date(d.connected_since * 1000).toLocaleTimeString().slice(0, 5), "info") : "");
  $("d-hints").innerHTML = (d.hints || []).map((h) => alertBox(h, "warn")).join("") + (d.apple_mode === "dfu" ? DFU_EXIT_HTML : "");
  $("btn-exitrec").classList.toggle("hidden", d.apple_mode !== "recovery");
  const ok = d.state === "reconnu", running = busy || (job && !job.done);
  $("btn-diag").disabled = !ok || running; $("btn-charge").disabled = !ok || running;
  $("btn-pair").classList.toggle("hidden", !(d.transport === "lockdown" && d.state !== "reconnu"));
  $("btn-pair").disabled = running;
  $("d-grid").innerHTML = kv("Marque", d.brand) + kv("Modèle", d.model) + kv("Système", d.os ? d.os + " " + (d.os_version || "") : null) +
    DETAILS.filter(([k]) => det[k]).map(([k, l, c]) => kv(l, det[k], c)).join("") +
    (det.imei_note && !det.imei ? kv("IMEI", det.imei_note) : "");
  document.querySelectorAll("[data-copy]").forEach((el) => el.onclick = () => { navigator.clipboard && navigator.clipboard.writeText(el.dataset.copy); toast("Copié"); });
  renderBattery(d.battery, d.battery_note, $("battery"));
  renderPanicAuto(d);
  const imeiIn = $("bl-imei");
  if (det.imei && imeiIn.dataset.dev !== d.key) { imeiIn.value = det.imei; }
  if (imeiIn.dataset.dev !== d.key) { if (!det.imei) imeiIn.value = ""; imeiIn.dataset.dev = d.key; blLocal = null; }
  const bl = d.blacklist || blLocal;
  const inApp = !!(window.pywebview && window.pywebview.api);
  $("bl-clean").classList.toggle("hidden", inApp); $("bl-bad").classList.toggle("hidden", inApp);
  $("bl-status").innerHTML = bl ? pill({clean: "CLEAN", "blacklisté": "BLACKLISTÉ", inconnu: "INCONNU"}[bl.status] || bl.status,
      {clean: "ok", "blacklisté": "bad"}[bl.status] || "info") +
    ` <span class="note" style="margin:0">vérifié à ${new Date(bl.checked_at * 1000).toLocaleTimeString().slice(0, 5)}</span>` : pill("non vérifié", "info");
}
function renderPanicAuto(d) {
  const p = d.panic_info;
  $("panic-card").classList.toggle("hidden", !(d.transport === "lockdown" && d.state === "reconnu"));
  if (!p) { $("panic-auto").innerHTML = '<p class="note">Vérification des panics en cours…</p>'; return; }
  const isNew = (n) => (p.new || []).includes(n);
  $("panic-auto").innerHTML = `<div class="row">${p.count ? pill(p.count + " panic(s) sur l'iPhone", "warn") : pill("Aucun panic sur l'iPhone", "ok")}
      ${(p.new || []).length ? pill((p.new.length) + " NOUVEAU(X) depuis le branchement", "bad") : ""}
      <span class="note" style="margin:0">vérifié à ${new Date(p.checked_at * 1000).toLocaleTimeString().slice(0, 5)} · surveillance continue</span></div>` +
    (p.hypotheses || []).map((h) => `<div class="hyp c-${esc(h.confidence)}" style="margin-top:10px"><h3>${esc(h.title)} ${pill(h.category, "gold")}
      ${pill("confiance " + h.confidence, h.confidence === "élevée" ? "bad" : h.confidence === "moyenne" ? "warn" : "info")}</h3>
      <div class="ev">${esc(h.next_checks[0] || "")}</div></div>`).join("") +
    (p.latest || []).map((x) => `<div class="panic">${isNew(x.name) ? pill("NOUVEAU", "bad") + " " : ""}<b>${esc(x.date || x.name)}</b>
      ${x.missing_sensors ? " · capteur(s) manquant(s) : <code>" + esc(x.missing_sensors) + "</code>" : ""}<br><code>${esc(x.summary)}</code></div>`).join("");
}

function ring(pct, color, big, small) {
  const r = 62, c = 2 * Math.PI * r, p = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  return `<div class="gauge"><svg width="150" height="150"><circle cx="75" cy="75" r="${r}" stroke="#2a2d33" stroke-width="12" fill="none"/>
    <circle cx="75" cy="75" r="${r}" stroke="${color}" stroke-width="12" fill="none" stroke-linecap="round"
      stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - p / 100)}" style="transition:stroke-dashoffset .6s"/></svg>
    <div class="val"><div><b>${big}</b><small>${small}</small></div></div></div>`;
}
const HCOL = {"bon": "#4ade80", "dégradé": "#f87171", "à contrôler": "#fbbf24", "inconnu": "#6b6e76"};
function renderBattery(b, note, el) {
  if (!b) { el.innerHTML = alertBox(note || "Données de batterie non disponibles pour cet appareil.", "info"); return; }
  const lvlCol = b.level == null ? "#6b6e76" : b.level <= 20 ? "#f87171" : "#d4af5a";
  el.innerHTML = `<div class="bat">
    ${ring(b.level, lvlCol, b.level != null ? b.level + "%" : "—", "Charge actuelle")}
    ${ring(b.capacity_pct, HCOL[b.health] || "#6b6e76", b.capacity_pct != null ? Math.round(b.capacity_pct) + "%" : "—", "Santé · " + esc(b.health))}
    <div class="bat-side">${kv("Cycles", b.cycles)}${kv("Capacité (actuelle / origine)", b.full_mah ? b.full_mah + " / " + b.design_mah + " mAh" : null)}
      ${kv("État", [b.status, b.plugged].filter(Boolean).join(" · "))}${kv("Courant", b.current_ma != null ? b.current_ma + " mA" : null)}
      ${kv("Tension", b.voltage_v != null ? b.voltage_v + " V" : null)}${kv("Température", b.temp_c != null ? b.temp_c + " °C" : null)}</div></div>
    <div class="note">${esc(b.health_basis || "")} ${b.capacity_source ? "— " + esc(b.capacity_source) : ""}<br>La charge actuelle (%) n'indique pas la santé de la batterie.</div>
    ${(b.anomalies || []).map((a) => alertBox(a, "bad")).join("")}
    ${(b.alternatives || []).length && b.health === "inconnu" ? alertBox("Alternatives : " + b.alternatives.join(" · "), "info") : ""}`;
}

const DFU_EXIT_HTML = `<div class="alert info">${ICON.info}<div><b>Sortir du mode DFU = redémarrage forcé</b> (aucun logiciel ne peut le faire en DFU) :<br>
  • <b>iPhone 8 et ultérieurs</b> : appuyer/relâcher Volume +, appuyer/relâcher Volume −, puis maintenir le bouton latéral jusqu'au logo Apple (~10-15 s).<br>
  • <b>iPhone 7</b> : maintenir bouton latéral + Volume − jusqu'au logo Apple.<br>
  • <b>iPhone 6s et antérieurs</b> : maintenir bouton principal + bouton latéral/haut jusqu'au logo Apple.<br>
  Le logiciel détectera automatiquement le redémarrage.</div></div>`;
async function exitRecovery(ecid) {
  try {
    const r = await api("/api/flash/recovery", {action: "exit", ecid: ecid || null}); toast("Sortie du mode Récupération…");
    const wait = async () => { const j = await api("/api/job?id=" + r.job); if (!j.done) return setTimeout(wait, 700);
      if (j.error) { toast(j.error); if (/DFU/.test(j.error)) $("d-hints").insertAdjacentHTML("beforeend", DFU_EXIT_HTML); }
      else toast(j.result && j.result.message || "Terminé"); };
    wait();
  } catch (e) { toast(e.message); }
}
$("btn-exitrec").onclick = () => { const d = devices.find((x) => x.key === selected); exitRecovery(d && d.ecid); };

// ---------- Blacklist (imeipro.info, vérification manuelle avec CAPTCHA) ----------
let blLocal = null;
async function blacklist(status) {
  const imei = $("bl-imei").value.trim();
  try {
    const r = await api("/api/blacklist", {imei, status, report_id: report && report.id});
    if (!status) {
      if (window.pywebview && window.pywebview.api) { window.pywebview.api.open_imei_check(imei);
        toast("Cochez le CAPTCHA puis CHECK : le résultat s'affichera ici automatiquement."); }
      else { window.open(r.url, "_blank"); toast("Version navigateur : notez le résultat avec Clean / Blacklisté."); }
    } else { blLocal = r.result; toast("Résultat enregistré : " + status); poll(); }
  } catch (e) { toast(e.message); }
}
$("bl-check").onclick = () => blacklist(null);
$("bl-clean").onclick = () => blacklist("clean");
$("bl-bad").onclick = () => blacklist("blacklisté");

// ---------- Actions ----------
function resetResults() { job = null; report = null; ["results", "progress-panel", "charge-card"].forEach((i) => $(i).classList.add("hidden")); }
async function runJob(path, body, onDone) {
  busy = true; renderDevice();
  try {
    const r = await api(path, body);
    job = {id: r.job, done: false}; $("progress-panel").classList.remove("hidden");
    const tick = async () => {
      const j = await api("/api/job?id=" + job.id); job = j;
      $("bar").style.width = j.progress + "%"; $("step").textContent = j.steps[j.steps.length - 1] || "";
      if (!j.done) return setTimeout(tick, 800);
      busy = false; renderDevice();
      if (j.error) { $("step").textContent = "⚠ " + j.error; toast(j.error); return; }
      onDone(j.result);
    };
    tick();
  } catch (e) { busy = false; renderDevice(); toast("Erreur : " + e.message); }
}
$("btn-diag").onclick = () => { resetResults(); runJob("/api/diagnostic", {device: selected, ticket: $("ticket").value, consent_raw: $("consent").checked},
  (res) => { report = {id: res.report_id}; renderResults(res); $("progress-panel").classList.add("hidden"); }); };
$("btn-pair").onclick = () => runJob("/api/pair", {device: selected}, (res) => { toast(res.message); $("progress-panel").classList.add("hidden"); poll(); });
$("btn-charge").onclick = () => {
  $("charge-card").classList.remove("hidden"); $("charge-sum").textContent = "Mesure en cours (20 s) — laissez le chargeur branché…"; drawChart([]);
  runJob("/api/chargetest", {device: selected, report_id: report && report.id}, (res) => {
    drawChart(res.samples); const s = res.summary;
    $("charge-sum").innerHTML = `<b>${esc(s.verdict)}</b> — ${esc(s.detail)}${s.avg_ma != null ? ` (min ${s.min_ma} / max ${s.max_ma} mA)` : ""}` +
      (s.adapter_w ? ` · chargeur détecté : ${esc(s.adapter_w)} W` : "") +
      (s.anomalies || []).map((a) => alertBox(a, "bad")).join("") +
      ((s.causes || []).length ? "<p style='margin:14px 0 6px'><b>Pièce probable :</b></p>" + s.causes.map((c) =>
        `<div class="hyp c-${esc(c.confidence)}"><h3>${esc(c.part)} ${pill("confiance " + c.confidence, c.confidence === "élevée" ? "bad" : c.confidence === "moyenne" ? "warn" : "info")}</h3><div class="ev">${esc(c.reason)}</div></div>`).join("") : "");
    $("progress-panel").classList.add("hidden");
  });
};
function drawChart(samples) {
  const pts = (samples || []).filter((s) => s.current_ma != null && !s.lost), W = 600, H = 170;
  if (!pts.length) { $("chart").innerHTML = `<text x="300" y="90" fill="#6b6e76" text-anchor="middle" font-size="13">En attente des mesures…</text>`; return; }
  const maxV = Math.max(500, ...pts.map((p) => p.current_ma)) * 1.15, minV = Math.min(0, ...pts.map((p) => p.current_ma));
  const tMax = Math.max(20, pts[pts.length - 1].t || 1);
  const x = (t) => 10 + (t / tMax) * (W - 20), y = (v) => H - 14 - ((v - minV) / (maxV - minV)) * (H - 30);
  const line = pts.map((p) => `${x(p.t).toFixed(1)},${y(p.current_ma).toFixed(1)}`).join(" ");
  $("chart").innerHTML = `<defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#d4af5a" stop-opacity=".35"/><stop offset="1" stop-color="#d4af5a" stop-opacity="0"/></linearGradient></defs>
    <line x1="10" x2="590" y1="${y(0)}" y2="${y(0)}" stroke="#33363d" stroke-dasharray="4 4"/>
    <polygon points="${x(pts[0].t)},${y(minV)} ${line} ${x(pts[pts.length - 1].t)},${y(minV)}" fill="url(#g)"/>
    <polyline points="${line}" fill="none" stroke="#f0d48e" stroke-width="2.5" stroke-linejoin="round"/>
    <text x="14" y="16" fill="#9a9ca3" font-size="12">${Math.round(maxV / 1.15)} mA</text>`;
}
function renderResults(r) {
  $("results").classList.remove("hidden");
  const ev = r.evidence || [];
  $("evidence").innerHTML = ev.length ? ev.map((e) => alertBox(e.text + " — " + e.source, e.severity === "alerte" ? "warn" : "info")).join("")
    : '<p class="note">Aucune anomalie dans les données accessibles (cela ne garantit pas l\'absence de panne).</p>';
  const H = r.hypotheses || [];
  $("hyps").innerHTML = H.length ? H.map((h) => `<div class="hyp c-${esc(h.confidence)}"><h3>${esc(h.title)} ${pill(h.category, "gold")}
     ${pill("confiance " + h.confidence, h.confidence === "élevée" ? "bad" : h.confidence === "moyenne" ? "warn" : "info")}</h3>
     <div class="ev">Indices : ${h.evidence.map(esc).join(" · ")}</div>
     <ol>${h.next_checks.map((c) => `<li>${esc(c)}</li>`).join("")}</ol></div>`).join("")
    : '<p class="note">Aucune cause identifiée à partir des données lues. Poursuivre avec les tests guidés.</p>';
  const P = r.panics;
  $("panics").innerHTML = !P ? '<p class="note">Non applicable (Android : voir les indices de redémarrage).</p>' : P.length ? P.map((p) =>
    `<div class="panic"><b>${esc(p.date || p.name)}</b>${p.missing_sensors ? " · capteur(s) manquant(s) : <code>" + esc(p.missing_sensors) + "</code>" : ""}<br><code>${esc(p.summary)}</code></div>`).join("")
    : '<p class="note">Aucun rapport de panic sur l\'iPhone.</p>';
  $("tests-card").classList.toggle("hidden", (r.identity || {}).platform === "ios");  // tests guidés : Android uniquement
  const auto = r.auto_tests || {};
  $("tests").innerHTML = tests.map((t) => `<div class="test"><div><b>${esc(t.label)}</b><p>${esc(t.procedure)}</p>
     ${auto[t.id] ? `<p>${pill("auto : " + auto[t.id].result, auto[t.id].result === "ok" ? "ok" : auto[t.id].result === "échec" ? "bad" : "info")} ${esc(auto[t.id].detail)}</p>` : ""}</div>
     <div><div class="seg">${["non testé", "ok", "échec", "n/a"].map((v, i) => `<label><input type="radio" name="t-${t.id}" value="${v}" ${i ? "" : "checked"}><span>${v}</span></label>`).join("")}</div>
     <input data-n="${t.id}" placeholder="Note" maxlength="300" style="margin-top:8px;width:100%"></div></div>`).join("");
  $("limits").innerHTML = (r.limits || []).map((l) => `<li>${esc(l)}</li>`).join("");
}
$("btn-save-tests").onclick = async () => {
  if (!report) return;
  const results = {};
  tests.forEach((t) => { const c = document.querySelector(`input[name="t-${t.id}"]:checked`); results[t.id] = {result: c ? c.value : "non testé", note: document.querySelector(`[data-n="${t.id}"]`).value}; });
  try { await api("/api/report/tests", {id: report.id, results}); toast("Tests enregistrés"); } catch (e) { toast("Erreur : " + e.message); }
};
$("btn-pdf").onclick = async () => { await $("btn-save-tests").onclick(); if (report) openReport(report.id); };
$("btn-export").onclick = async () => {
  try { openReport(report ? report.id : (await api("/api/report/quick", {device: selected, ticket: $("ticket").value})).id); } catch (e) { toast("Erreur : " + e.message); }
};
function openReport(id) {
  if (window.pywebview && window.pywebview.api) return window.pywebview.api.open_report(id);
  window.open("report.html?id=" + id, "_blank");
}

// ---------- Historique ----------
async function loadHistory() {
  const rows = await api(`/api/history?q=${encodeURIComponent($("h-q").value)}&date=${encodeURIComponent($("h-date").value)}`);
  $("h-rows").innerHTML = rows.map((r) => `<tr><td>${esc(new Date(r.created * 1000).toLocaleString())}</td>
    <td><b>${esc(label(r.device))}</b> <span class="note">${esc(r.device.serial_masked || "")}</span></td><td>${pill(r.kind, "gold")}</td>
    <td>${esc(r.ticket || "")}</td><td>${r.flash ? pill(r.flash.state || "?", r.flash.state === "réussi" ? "ok" : r.flash.state === "en cours" ? "info" : "bad") +
      (r.flash.error ? `<div class="note" style="margin:4px 0 0">${esc(r.flash.error.slice(0, 160))}</div>` : "") +
      (r.causes && r.causes.length ? `<div class="note" style="margin:2px 0 0"><b>Cause probable :</b> ${esc(r.causes[0])}</div>` : "") : r.hypotheses}</td><td><button data-r="${esc(r.id)}">Ouvrir</button></td></tr>`).join("")
    || '<tr><td colspan="6" class="note">Aucun rapport.</td></tr>';
  document.querySelectorAll("[data-r]").forEach((b) => b.onclick = () => openReport(b.dataset.r));
}
$("h-go").onclick = loadHistory;
let curTab = "diag";
function tab(name) {
  curTab = name;
  ["diag", "hist", "flash"].forEach((n) => { $("view-" + n).classList.toggle("hidden", n !== name); $("tab-" + n).classList.toggle("active", n === name); });
  if (name === "hist") loadHistory();
  if (name === "flash") { fwFor = null; flashRefresh(); }
}
$("tab-diag").onclick = () => tab("diag"); $("tab-hist").onclick = () => tab("hist"); $("tab-flash").onclick = () => tab("flash");
const hypHtml = (H) => H.map((h) => `<div class="hyp c-${esc(h.confidence)}" style="margin-top:10px"><h3>${esc(h.title)} ${pill(h.category, "gold")}
  ${pill("confiance " + h.confidence, h.confidence === "élevée" ? "bad" : h.confidence === "moyenne" ? "warn" : "info")}</h3>
  <div class="ev">${esc(h.source)}</div><ol>${h.next_checks.map((c) => `<li>${esc(c)}</li>`).join("")}</ol></div>`).join("");
api("/api/deadphone").then((r) => { $("dp-amp").innerHTML += r.patterns.map((p) => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join(""); });
$("dp-go").onclick = async () => {
  const r = await api(`/api/deadphone?code=${encodeURIComponent($("dp-code").value)}&amp=${encodeURIComponent($("dp-amp").value)}`);
  const H = r.restore.concat(r.ammeter);
  $("dp-res").innerHTML = H.length ? hypHtml(H) + '<p class="note">Pistes à confirmer par mesure ou échange de pièce, pas une certitude.</p>'
    : '<p class="note">Code inconnu ou aucune mesure : saisissez un code d\'erreur et/ou choisissez la mesure de l\'ampèremètre.</p>';
};

// ---------- Flash iOS ----------
let dfu = null, fwFor = null, fwList = [], libSel = null;
const gb = (n) => n ? (n / 1e9).toFixed(1) + " Go" : "?";
function flashTarget() {
  const d = devices.find((x) => x.key === selected && x.platform === "ios" && x.transport === "lockdown");
  if (d) return {kind: "device", key: d.key, label: label(d), pt: d.model_code, model: d.model, battery: d.battery && d.battery.level, state: d.state};
  const sp = devices.find((x) => x.key === selected && x.apple_mode && x.model_code) || devices.find((x) => x.apple_mode && x.model_code);
  if (sp) return {kind: "cfg", key: sp.key, label: label(sp), pt: sp.model_code, model: sp.model, ecid: sp.cfg_ecid};
  if (dfu) return {kind: "dfu", label: (dfu.display_name || dfu.product_type) + " (" + dfu.mode + ")", pt: dfu.product_type, model: dfu.display_name || dfu.product_type, ecid: dfu.ecid};
  return null;
}
async function flashRefresh() {
  if (curTab !== "flash") return;
  const t = flashTarget();
  $("f-target").innerHTML = t ? `<div class="row">${pill(t.label, "gold")} ${t.pt ? pill(t.pt, "info") : ""} ${t.battery != null ? pill("batterie " + t.battery + " %", t.battery < 30 ? "bad" : "ok") : ""}
     ${t.kind === "dfu" ? pill("ECID " + t.ecid, "info") : ""}</div>` : alertBox("Aucun iPhone : branchez-le (sélectionnez-le à gauche) ou mettez-le en Récupération/DFU puis « Détecter ».", "info");
  $("f-enter").disabled = !(t && t.kind === "device");
  if (t && t.pt && fwFor !== t.pt) {
    fwFor = t.pt; $("f-list").textContent = "Chargement de la liste Apple…";
    try { fwList = await api("/api/firmwares?pt=" + encodeURIComponent(t.pt)); } catch (e) { fwList = []; $("f-list").textContent = "Liste indisponible : " + e.message; }
  }
  let lib = {files: [], downloads: []};
  try { lib = await api("/api/firmware/library"); } catch (e) { /* hors ligne */ }
  const dl = Object.fromEntries((lib.downloads || []).map((x) => [x.url, x]));
  const have = new Set((lib.files || []).map((f) => f.file));
  const fwRow = (f) => { const x = dl[f.url];
    const act = have.has(f.file) ? pill("téléchargé", "ok") : x && ["en cours", "vérification"].includes(x.state) ?
      `${esc(x.state)} ${x.total ? Math.floor(x.done * 100 / x.total) : 0} % · ${(x.speed / 1e6).toFixed(1)} Mo/s <button data-cancel="${esc(f.url)}">Annuler</button>` :
      (x && x.error ? pill(x.error, "bad") + " " : "") + (f.signed ? `<button data-dl="${esc(f.url)}">Télécharger</button>` : "");
    return `<tr><td><b>${esc(f.version)}</b></td><td>${esc(f.build)}</td><td>${esc(f.date)}</td><td>${gb(f.size)}</td>
      <td>${f.signed ? pill("signé", "ok") : pill("non signé", "bad")}</td><td>${act}</td></tr>`; };
  const signed = fwList.filter((f) => f.signed), unsigned = fwList.filter((f) => !f.signed);
  const openU = !!document.querySelector("#f-unsigned[open]");
  if (fwList.length) $("f-list").innerHTML = (signed.length ? `<table><tbody>${signed.map(fwRow).join("")}</tbody></table>` : '<p class="note">Aucune version signée.</p>') +
    (unsigned.length ? `<details id="f-unsigned" ${openU ? "open" : ""} style="margin-top:8px"><summary class="note" style="cursor:pointer">Versions non signées (${unsigned.length}) — non installables</summary>
      <table><tbody>${unsigned.slice(0, 40).map(fwRow).join("")}</tbody></table></details>` : "");
  document.querySelectorAll("[data-dl]").forEach((b) => b.onclick = async () => { try { await api("/api/firmware/download", {pt: fwFor, url: b.dataset.dl}); flashRefresh(); } catch (e) { toast(e.message); } });
  document.querySelectorAll("[data-cancel]").forEach((b) => b.onclick = () => api("/api/firmware/cancel", {url: b.dataset.cancel}));
  const files = (lib.files || []).filter((f) => !t || !t.pt || (f.file.split("_")[0].match(/(iPhone|iPad|iPod)\d+,\d+/g) || []).includes(t.pt));
  if (!files.find((f) => f.file === libSel)) libSel = files.length ? files[0].file : null;
  $("f-lib").innerHTML = files.length ? files.map((f) => `<label class="panic" style="display:block;cursor:pointer"><input type="radio" name="f-file" value="${esc(f.file)}" ${f.file === libSel ? "checked" : ""}> <b>${esc(f.file)}</b> · ${gb(f.size)}</label>`).join("")
    : '<p class="note">Aucun firmware compatible téléchargé.</p>';
  document.querySelectorAll('input[name="f-file"]').forEach((r) => r.onchange = () => { libSel = r.value; });
  $("f-dir").textContent = lib.dir ? "Dossier : " + lib.dir : "";
  const mode = (document.querySelector('input[name="f-mode"]:checked') || {}).value;
  const phrase = t ? "FLASH" : "";
  $("f-phrase").innerHTML = t ? `Pour confirmer, tapez exactement : <b>${esc(phrase)}</b>` : "";
  const fj = lib.flash; const running = fj && !fj.done;
  $("f-go").disabled = !t || t.kind === "cfg" || !libSel || running; $("f-exit").disabled = running; $("f-detect").disabled = running;
  if (fj) { $("f-prog").classList.remove("hidden"); $("f-bar").style.width = fj.progress + "%";
    $("f-step").innerHTML = fj.error ? "⚠ " + esc(fj.error) + (fj.result && fj.result.explain && fj.result.explain.length ? "<b> — causes probables :</b>" + hypHtml(fj.result.explain) : "")
      : esc(fj.steps[fj.steps.length - 1] || ""); }
}
function renderCfg() {
  if (!toolsState.cfgutil) {
    $("cfg-box").innerHTML = /Win/.test(navigator.platform) ? "Sur Windows : restaurez ces appareils avec l'application Apple Devices (Apple Configurator n'existe que sur Mac)." : `Apple Configurator n'est pas installé. <b>Mac App Store → « Apple Configurator »</b> (gratuit, Apple) : dès qu'il est installé, cette fonction s'active toute seule.`;
    return;
  }
  if (!cfgDevs) { const auto = devices.filter((d) => d.cfg_ecid).map((d) => ({ecid: d.cfg_ecid, type: d.model_code, name: d.model})); if (auto.length) cfgDevs = auto; }
  const list = (cfgDevs || []).map((d) => `<label class="panic" style="display:block"><input type="radio" name="cfg-dev" value="${esc(d.ecid)}" data-type="${esc(d.type || "")}" ${cfgDevs.length === 1 ? "checked" : ""}>
     <b>${esc(d.type || "Appareil Apple")}</b> ${d.name ? "· " + esc(d.name) : ""} · ECID ${esc(d.ecid)}</label>`).join("");
  $("cfg-box").innerHTML = `<div class="row"><button id="cfg-scan">Rechercher les appareils</button>
     <button id="cfg-revive" ${cfgDevs && cfgDevs.length ? "" : "disabled"}>Ranimer (garde les données si possible)</button>
     <button id="cfg-restore" class="primary" ${cfgDevs && cfgDevs.length ? "" : "disabled"}>Restaurer (efface tout)</button></div>
     ${cfgDevs ? (list || "<p>Aucun appareil trouvé.</p>") : ""}
     <p>Confirmation : tapez <b>FLASH</b> dans le champ « Lancer le flash » ci-dessous. Firmware : celui sélectionné dans la bibliothèque, sinon le dernier iOS signé (téléchargé par Apple).</p>`;
  $("cfg-scan").onclick = async () => { try { const r = await api("/api/cfg/list", {}); toast("Recherche…");
    const w = async () => { const j = await api("/api/job?id=" + r.job); if (!j.done) return setTimeout(w, 700);
      if (j.error) return toast(j.error); cfgDevs = j.result.devices || []; renderCfg(); }; w(); } catch (e) { toast(e.message); } };
  const go = (op) => async () => {
    const sel = document.querySelector('input[name="cfg-dev"]:checked'); if (!sel) return toast("Choisissez l'appareil.");
    const file = libSel && (libSel.split("_")[0].match(/(iPhone|iPad|iPod)\d+,\d+/g) || []).includes(sel.dataset.type) ? libSel : null;
    if (!confirm((op === "restore" ? "EFFACER DÉFINITIVEMENT et restaurer " : "Ranimer ") + (sel.dataset.type || "l'appareil") + " ?")) return;
    try { await api("/api/cfg/restore", {op, ecid: sel.value, file, confirm: $("f-confirm").value, model: sel.dataset.type});
      toast("Lancé : ne débranchez pas l'iPhone."); $("f-confirm").value = ""; } catch (e) { toast(e.message); }
  };
  $("cfg-revive").onclick = go("revive"); $("cfg-restore").onclick = go("restore");
}
let cfgRendered = "";
setInterval(() => { const k = String(!!toolsState.cfgutil) + JSON.stringify(cfgDevs) + devices.map((d) => d.cfg_ecid || "").join();
  if (!devices.some((d) => d.cfg_ecid) && cfgDevs && cfgDevs.every((c) => c.name && / en mode /.test(c.name))) cfgDevs = null; if (curTab === "flash" && k !== cfgRendered) { cfgRendered = k; renderCfg(); } }, 1000);
setInterval(flashRefresh, 2000);
document.querySelectorAll('input[name="f-mode"]').forEach((r) => r.onchange = flashRefresh);
$("f-enter").onclick = async () => { try { await api("/api/flash/recovery", {action: "enter", device: selected}); toast("Passage en mode Récupération…"); } catch (e) { toast(e.message); } };
$("f-exit").onclick = () => { const d = devices.find((x) => x.apple_mode); exitRecovery((dfu && dfu.ecid) || (d && d.ecid)); };
$("f-detect").onclick = async () => {
  try { const r = await api("/api/flash/detect", {}); toast("Recherche…");
    const wait = async () => { const j = await api("/api/job?id=" + r.job); if (!j.done) return setTimeout(wait, 700);
      if (j.error) return toast(j.error); dfu = j.result.device; fwFor = null; toast("Détecté : " + (dfu.display_name || dfu.product_type)); flashRefresh(); };
    wait(); } catch (e) { toast(e.message); }
};
$("f-go").onclick = async () => {
  const t = flashTarget(), mode = document.querySelector('input[name="f-mode"]:checked').value;
  if (!confirm((mode === "restore-erase" ? "EFFACER DÉFINITIVEMENT " : "Flasher ") + t.label + " avec " + libSel + " ?")) return;
  try { await api("/api/flash/start", {op: mode, file: libSel, confirm: $("f-confirm").value, device: t.kind === "device" ? t.key : null,
      ecid: t.ecid, model: t.model, product_type: t.pt}); toast("Flash lancé : ne débranchez pas l'iPhone."); $("f-confirm").value = ""; flashRefresh();
  } catch (e) { toast(e.message); }
};

api("/api/tests").then((t) => tests = t);
async function checkUpdate(force) {
  try {
    const u = await api("/api/update" + (force ? "?force=1" : ""));
    let h = `Version ${esc(u.current)}`;
    if (u.installed) h += ` · ${pill("v" + u.installed + " installée : redémarrez", "ok")}`;
    else if (u.available) h += `<div class="alert info" style="margin-top:6px">${ICON.info}<div><b>Mise à jour ${esc(u.latest)} disponible</b><br>${esc(u.notes || "")}<br><button id="upd-go" class="primary" style="margin-top:6px">Installer</button></div></div>`;
    else if (u.configured) h += ` · <a href="#" id="upd-chk" style="color:var(--gold)">vérifier</a>`;
    $("upd").innerHTML = h;
    if ($("upd-go")) $("upd-go").onclick = async () => { $("upd-go").disabled = true; toast("Téléchargement de la mise à jour…");
      try { await api("/api/update/install", {}); toast("Mise à jour installée : fermez et rouvrez Only Tools."); } catch (e) { toast(e.message); } checkUpdate(); };
    if ($("upd-chk")) $("upd-chk").onclick = (e) => { e.preventDefault(); checkUpdate(true); };
  } catch (e) { /* hors ligne */ }
}
checkUpdate(); setInterval(checkUpdate, 6 * 3600 * 1000);
poll(); setInterval(() => { if (!document.hidden) poll(); }, 2000);
