"use strict";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const v = (x, suf) => (x == null || x === "") ? '<span class="muted">Non disponible</span>' : esc(x) + (suf || "");
const row = (k, x, suf) => `<tr><th>${esc(k)}</th><td>${v(x, suf)}</td></tr>`;

(async () => {
  const id = new URLSearchParams(location.search).get("id");
  const [rep, tests] = await Promise.all([fetch("/api/report?id=" + encodeURIComponent(id)).then((r) => r.json()),
    fetch("/api/tests").then((r) => r.json())]);
  if (rep.error) { document.getElementById("r").textContent = rep.error; return; }
  const d = rep.device || {}, b = rep.battery, res = rep.result || {};
  let h = `<div class="head"><div class="logo"><span class="mark"><svg viewBox="0 0 40 40" fill="none" stroke-linecap="round" stroke-linejoin="round"><rect x="12" y="5" width="16" height="30" rx="5" fill="#1a1408"/><path d="M17.5 9h5" stroke="#f6e2a8" stroke-width="1.6"/><path d="M3.5 21H12M28 21h8.5" stroke="#1a1408" stroke-width="2.6"/><path d="M12 21h3.2l2-5.2 3.4 10 2.6-7.2 1.4 2.4H28" stroke="#f6e2a8" stroke-width="2.2"/></svg></span>ONLY <b>PHONE</b></div><div style="text-align:right">
    <b>Rapport de ${rep.kind === "diagnostic" ? "diagnostic" : "contrôle (fiche appareil)"}</b><br>
    ${esc(new Date(rep.created * 1000).toLocaleString("fr-FR"))}<br><span class="muted">Réf. ${esc(rep.id)}${rep.ticket ? " · Ticket " + esc(rep.ticket) : ""}</span></div></div>
    <h2>Appareil</h2><table>${row("Marque / modèle", [d.brand, d.model].filter(Boolean).join(" "))}${row("Référence", d.model_code)}
    ${row("Système", d.os ? d.os + " " + (d.os_version || "") : null)}${row("Détails", d.os_detail)}
    ${row("IMEI", (d.details || {}).imei)}${(d.details || {}).imei2 ? row("IMEI 2", d.details.imei2) : ""}
    ${(d.details || {}).serial_number ? row("N° de série", d.details.serial_number) : row("Identifiant appareil (masqué)", d.serial_masked)}${row("Réf. commerciale", (d.details || {}).model_number)}${(d.details || {}).origin ? row("Origine", d.details.origin) : ""}${row("État de reconnaissance", d.state)}
    ${rep.blacklist ? row("Blacklist", (rep.blacklist.status === "clean" ? "Clean" : "BLACKLISTÉ") + " — vérifié sur " + rep.blacklist.source + " le " + new Date(rep.blacklist.checked_at * 1000).toLocaleString("fr-FR")) : row("Blacklist", "non vérifié")}</table>`;
  h += "<h2>Batterie</h2>";
  if (b) {
    h += `<table>${row("Niveau de charge actuel", b.level, " %")}${row("Santé (usure)", b.health)}${row("Base de l'évaluation", b.health_basis)}
      ${row("Capacité maximale", b.capacity_pct, " %")}${row("Cycles", b.cycles)}${row("Température", b.temp_c, " °C")}
      ${row("Tension", b.voltage_v, " V")}${row("Indicateur système", b.bms_health)}</table>
      ${(b.anomalies || []).map((a) => `<p class="alert">⚠ ${esc(a)}</p>`).join("")}
      ${(b.unavailable || []).map((a) => `<p class="muted">— ${esc(a)}</p>`).join("")}`;
  } else h += '<p class="muted">Données de batterie non disponibles via l\'interface utilisée.</p>';
  if (rep.kind === "diagnostic" && res.score) {
    const S = res.score;
    h += `<h2>Bilan</h2><table>${row("Score global", S.score + " / 100 — " + S.grade + " (" + S.label + ")")}</table>` +
      (S.factors.length ? "<ul>" + S.factors.map((f) => `<li>${esc(f.label)} (${f.pts})</li>`).join("") + "</ul>" : "") +
      '<p class="muted">Estimation sur les données lues ; les tests manuels ne sont pas comptés.</p>';
    if ((res.authenticity || []).length) h += "<h2>Pièces d'origine (indices)</h2><table>" + res.authenticity.map((a) =>
      `<tr><th>${esc(a.part)}</th><td><b>${esc(a.status)}</b> — ${esc(a.detail)}</td></tr>`).join("") + "</table>";
    if ((res.known_faults || []).length) h += "<h2>Pannes connues pour ce modèle</h2><ul>" + res.known_faults.map((f) =>
      `<li class="${f.matched ? "alert" : ""}"><b>${esc(f.title)}</b>${f.matched ? " — recoupe ce diagnostic" : ""} : ${esc(f.symptoms)} ${esc(f.check)}</li>`).join("") + "</ul>";
  }
  if (rep.kind === "diagnostic") {
    const ev = res.evidence || [];
    h += "<h2>Indices observés</h2>" + (ev.length ? "<ul>" + ev.map((e) => `<li class="${e.severity === "alerte" ? "alert" : ""}">${esc(e.text)} <span class="muted">(${esc(e.source)})</span></li>`).join("") + "</ul>"
      : '<p class="muted">Aucune anomalie dans les données accessibles.</p>');
    const H = res.hypotheses || [];
    h += "<h2>Causes possibles (hypothèses)</h2>" + (H.length ? H.map((x) => `<div class="hyp"><b>${esc(x.title)}</b> — ${esc(x.category)}, confiance <b>${esc(x.confidence)}</b>
      <div class="muted">Indices : ${x.evidence.map(esc).join(" · ")}</div><ol>${x.next_checks.map((c) => `<li>${esc(c)}</li>`).join("")}</ol></div>`).join("")
      : '<p class="muted">Aucune cause identifiée à partir des données lues.</p>');
  }
  if (rep.flash) {
    const f = rep.flash;
    h += `<h2>Flash iOS</h2><table>${row("Opération", f.operation)}${row("Firmware", f.firmware)}${row("Résultat", f.state || (f.ok ? "réussi" : "échec"))}
      ${f.error ? row("Erreur", f.error) : ""}${row("Durée", Math.round((f.duration_s || 0) / 60) + " min")}</table>` +
      ((f.explain || []).length ? "<b>Causes probables :</b><ul>" + f.explain.map((x) => `<li><b>${esc(x.title)}</b> (confiance ${esc(x.confidence)}) — ${x.next_checks.map(esc).join(" ; ")}</li>`).join("") + "</ul>" : "") +
      ((f.log_tail || []).length && !f.ok ? `<details><summary class="muted">Journal technique</summary><pre style="white-space:pre-wrap;font-size:10px">${esc(f.log_tail.join("\n"))}</pre></details>` : "");
  }
  if (rep.charge_test) {
    const c = rep.charge_test;
    h += `<h2>Test de charge</h2><table>${row("Résultat", c.verdict)}${row("Détail", c.detail)}${c.avg_ma != null ? row("Courant min / moy / max", c.min_ma + " / " + c.avg_ma + " / " + c.max_ma + " mA") : ""}</table>` +
      (c.anomalies || []).map((a) => `<p class="alert">⚠ ${esc(a)}</p>`).join("") +
      ((c.causes || []).length ? "<ul>" + c.causes.map((x) => `<li><b>${esc(x.part)}</b> (confiance ${esc(x.confidence)}) — ${esc(x.reason)}</li>`).join("") + "</ul>" : "");
  }
  if (res.panics && res.panics.length) {
    h += "<h2>Rapports de panic (iPhone)</h2><ul>" + res.panics.map((p) => `<li><b>${esc(p.date || p.name)}</b> — ${esc(p.missing_sensors ? "capteur(s) manquant(s) : " + p.missing_sensors : p.summary)}</li>`).join("") + "</ul>";
  }
  const auto = res.auto_tests || {}, man = rep.manual_tests || {};
  if (rep.kind === "diagnostic" && d.platform !== "ios") {
    h += "<h2>Tests</h2><table><tr><th>Test</th><td><b>Résultat</b></td></tr>" + tests.map((t) => {
      const m = man[t.id], a = auto[t.id];
      return `<tr><th>${esc(t.label)}</th><td>${m ? "<b>" + esc(m.result) + "</b> (réparateur)" + (m.note ? " — " + esc(m.note) : "") : '<span class="muted">non testé</span>'}
        ${a ? `<br><span class="muted">Auto : ${esc(a.result)} — ${esc(a.detail)}</span>` : ""}</td></tr>`;
    }).join("") + "</table>";
  }
  if (rep.kind === "diagnostic") {
    h += "<h2>Limites</h2><ul>" + (res.limits || []).map((l) => `<li class="muted">${esc(l)}</li>`).join("") + "</ul>";
  }
  h += `<div class="foot">Rapport généré localement par Only Tools ${esc(rep.app_version || "")}. Les causes indiquées sont des hypothèses
    fondées sur les données accessibles et ne constituent pas une certitude. Aucune donnée personnelle du téléphone n'a été modifiée.</div>`;
  document.getElementById("r").innerHTML = h;
})();
