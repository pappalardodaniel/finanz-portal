/* Reiter der Handelsteile im Dashboard: Trends, KI, Weltlage, Autopilot.
   Daten kommen von /api/k/... (dashboard/kbruecke.py); schreibende Aufrufe tragen den Kopf X-Kitrader. Nichts hier sendet eine Order:
   der Autopilot handelt nur im Papierbetrieb und mit Risikoprüfung, der Not-Aus storniert und stellt glatt (betrieb.py). */
(() => {
const q = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const num = (x, n = 2) => x == null || Number.isNaN(+x) ? "–" : (+x).toLocaleString("de-DE", {minimumFractionDigits: n, maximumFractionDigits: n});
const pz = (x, n = 2) => x == null ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : "±"}${Math.abs(x).toLocaleString("de-DE", {minimumFractionDigits: n, maximumFractionDigits: n})} %`;
const vz = x => x > 0 ? "k-gut" : x < 0 ? "k-schlecht" : "k-leise";
const zeit = iso => { if (!iso) return "–"; const d = new Date(iso); return isNaN(d) ? "–" : d.toLocaleString("de-DE", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"}); };
const alterText = min => min == null ? "" : min < 90 ? `vor ${min} Min.` : min < 48 * 60 ? `vor ${Math.round(min / 60)} Std.` : `vor ${Math.round(min / 1440)} Tagen`;
const http = u => /^https?:\/\//.test(u || "") ? u : "";

async function api(pfad, daten) {
  const opt = daten === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-Kitrader": "1"}, body: JSON.stringify(daten)};
  const r = await fetch("/api/k/" + pfad, opt);
  let d = null;
  try { d = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error((d && d.fehler) || `Fehler ${r.status}`);
  return d;
}
const fehlerHtml = e => `<div class="k-fehler" role="alert">${esc(e.message || e)}</div>`;
const leerHtml = t => `<div class="k-leer">${t}</div>`;

// Prozentzelle mit Farbe: Sättigung nach Betrag (skala = Betrag in %, ab dem die Farbe voll ist)
function wZelle(x, skala = 3, n = 2) {
  if (x == null) return `<td class="k-w n">–</td>`;
  const a = Math.min(1, Math.abs(x) / skala), f = x > 0 ? "var(--gut)" : x < 0 ? "var(--kritisch)" : "transparent";
  return `<td class="k-w ${vz(x)}" style="background:color-mix(in srgb, ${f} ${Math.round(8 + a * 30)}%, transparent)">${pz(x, n)}</td>`;
}
const PFEIL = {steigt: ["▲", "steigt"], faellt: ["▼", "fällt"], seitwaerts: ["►", "seitwärts"]};
const pfeil = r => { const p = PFEIL[r] || ["?", r || "?"]; return `<span class="k-pfeil ${esc(r)}" aria-hidden="true">${p[0]}</span>${p[1]}`; };
const HORIZONT = {kurz: ["Kurz", "1 Tag"], mittel: ["Mittel", "3 Monate"], lang: ["Lang", "1 Jahr"]};

const K = {};   // je Reiter: laden()
let timer = null;
window.K_STAND = {
  trends: "Trends · Welt-Rotation, Nachrichten-Radar und frühere, ähnliche Lagen · Zählungen und Rückblicke, keine Prognose",
  ki: "KI-Einschätzung · kurz, mittel, lang · eine Meinung zum Nachdenken, kein Handelssignal",
  weltlage: "Weltlage aus OSIRIS · Vorfälle, Konfliktzonen, Engpässe, Märkte · Einstufungen sind die von OSIRIS",
  autopilot: "Autopilot · Betriebsart, Grenzen, Entscheidungen · Papierbetrieb: simuliert, kein echtes Geld",
};
let info = null;
async function infoLaden() { if (!info) info = await api("info"); return info; }

// =================================================================== Trends
const T = {d: null, ereignisse: null, art: "", gewaehlt: null, reihe: "btc", studie: null, laeuft: false, meldung: ""};
K.trends = async function () {
  const w = q("#trends");
  if (!w.dataset.gebaut) {
    w.dataset.gebaut = "1";
    w.innerHTML = `
      <div class="k-leiste">
        <button class="haupt-knopf" id="tr-abruf" type="button">Neu abrufen</button>
        <button class="leise" id="tr-neu" type="button">Anzeige aktualisieren</button>
        <span class="leise-text" id="tr-stand"></span>
      </div>
      <div id="tr-meldung"></div>
      <p class="k-hinweis">Was sich bewegt: Welt-Rotation, Nachrichten-Radar und frühere, ähnliche Lagen. Das sind Zählungen und Rückblicke aus öffentlichen Quellen,
        keine Prognose und kein Handelssignal. „Neu abrufen“ holt höflich und höchstens so oft, wie der Mindestabstand der Einstellungen erlaubt; die Nachrichten brauchen ein bis drei Minuten.</p>
      <div id="tr-inhalt"></div>`;
    q("#tr-abruf").onclick = trendsAbrufen;
    q("#tr-neu").onclick = () => trendsLaden(true);
  }
  await trendsLaden();
};
async function trendsLaden(neu) {
  const z = q("#tr-inhalt");
  if (!T.d || neu) z.innerHTML = leerHtml("wird geladen …");
  try {
    T.d = await api("trends");
    if (!T.ereignisse) T.ereignisse = await api("historie/ereignisse");
    trendsZeichnen();
  } catch (e) { z.innerHTML = fehlerHtml(e); }
}
async function trendsAbrufen() {
  if (T.laeuft) return;
  T.laeuft = true;
  const knopf = q("#tr-abruf"), m = q("#tr-meldung");
  knopf.disabled = true;
  const zeilen = [];
  for (const [teil, name] of [["welt", "Welt-Rotation"], ["nachrichten", "Nachrichten-Radar"]]) {
    m.innerHTML = `<div class="k-leer">${esc(zeilen.concat(`${name} wird abgerufen …`).join(" · "))}</div>`;
    try {
      const r = await api("trends/abrufen", {teil});
      zeilen.push(`${name}: ${r.neu ? (r.zusammenfassung || "neu geholt") : r.hinweis}` + (r.fehler && r.fehler.length ? ` (Fehler: ${r.fehler.join("; ")})` : ""));
    } catch (e) { zeilen.push(`${name}: ${e.message}`); }
  }
  m.innerHTML = `<div class="k-ok">${zeilen.map(esc).join("<br>")}</div>`;
  T.laeuft = false; knopf.disabled = false;
  trendsLaden(true);
}
function rotationHtml(w) {
  if (!w.vorhanden) return leerHtml(esc(w.fehler || w.hinweis));
  const kopf = `<tr><th>&nbsp;</th><th class="r">1 Tag</th><th class="r">1 Woche</th><th class="r">1 Monat</th></tr>`;
  const reg = w.regionen.map(r => `<tr><td>${esc(r.region)}<small>${esc(r.index)}${r.offen ? "" : " · Börse zu"}</small></td>${["1T", "1W", "1M"].map(h => wZelle(r.aend[h], h === "1T" ? 1.5 : h === "1W" ? 3 : 6)).join("")}</tr>`).join("");
  const kl = w.klassen.map(r => `<tr><td>${esc(r.klasse)}<small>${esc((r.mitglieder || []).slice(0, 4).join(", "))}</small></td>${["1T", "1W", "1M"].map(h => wZelle(r.aend[h], h === "1T" ? 2 : h === "1W" ? 4 : 8)).join("")}</tr>`).join("");
  const rang = ["1T", "1W", "1M"].map(h => {
    const r = (w.rang || {})[h]; if (!r) return "";
    const li = (l, k) => l.map(([n, x]) => `<li><span>${esc(n)}</span><b class="${k}">${pz(x, 1)}</b></li>`).join("");
    return `<div><h4>${{"1T": "1 Tag", "1W": "1 Woche", "1M": "1 Monat"}[h]}</h4><ul>${li(r.gewinner, "k-gut")}</ul><ul style="margin-top:4px">${li(r.verlierer, "k-schlecht")}</ul></div>`;
  }).join("");
  return `
    <div class="k-gitter">
      <div class="k-kasten"><h3>Aktienmärkte nach Region <small>Weltschnitt ${pz(w.weltschnitt["1T"], 1)} / ${pz(w.weltschnitt["1W"], 1)} / ${pz(w.weltschnitt["1M"], 1)}</small></h3>
        <div class="rollbar"><table class="k-tab"><thead>${kopf}</thead><tbody>${reg}</tbody></table></div></div>
      <div class="k-kasten"><h3>Anlageklassen</h3>
        <div class="rollbar"><table class="k-tab"><thead>${kopf}</thead><tbody>${kl}</tbody></table></div></div>
    </div>
    <div class="k-kasten" style="margin-top:12px"><h3>Gewinner und Verlierer <small>je Horizont die drei stärksten und schwächsten</small></h3><div class="k-rang">${rang}</div>
      ${(w.umschichtung || []).length ? `<p class="k-hinweis">${w.umschichtung.map(esc).join("<br>")}</p>` : ""}</div>
    <p class="k-hinweis">Stand ${zeit(w.stand)} (${alterText(Math.round(w.alter_h * 60))}) · Quellen: ${esc((w.quellen || []).join(", "))}. ${(w.hinweise || []).map(esc).join(" ")}</p>`;
}
function radarHtml(r) {
  if (!r.vorhanden) return leerHtml(esc(r.fehler || r.hinweis));
  const chip = (t, k) => `<span class="k-chip ${k || ""}">${esc(t)}</span>`;
  const themen = r.themen.map(t => {
    const titel = http(t.url) ? `<a href="${esc(t.url)}" target="_blank" rel="noopener noreferrer">${esc(t.titel)}</a>` : esc(t.titel);
    return `<li><div class="titel">${titel}</div>
      <div class="zahlen">${t.verlage_24h} Quellen in 24 Std. (${t.verlage_6h} in 6 Std.)${t.quelle ? " · zuerst " + esc(t.quelle) : ""}</div>
      <div class="k-chips">${t.neu ? chip("neu", "neu") : ""}${t.amtlich ? chip("amtliche Quelle") : ""}${(t.regionen || []).map(x => chip(x, "region")).join("")}
        ${(t.arten || []).map(x => chip(x)).join("")}${(t.klassen || []).map(x => chip(x, "markt")).join("")}${(t.branchen || []).map(x => chip(x, "markt")).join("")}</div></li>`;
  }).join("");
  const zaehl = (titel, l) => l.length ? `<div><h4>${titel}</h4><ul>${l.map(x => `<li><span>${esc(x.name)}</span><b>${x.verlage_6h} / ${x.verlage_24h}</b></li>`).join("")}</ul></div>` : "";
  return `<div class="k-kasten"><h3>Top-Themen der letzten 24 Stunden <small>${r.meldungen_24h} Meldungen aus ${r.quellen_24h} Quellen · Stand ${zeit(r.stand)} ${alterText(r.alter_min)}</small></h3>
      ${themen ? `<ul class="k-themen">${themen}</ul>` : leerHtml("Zurzeit meldeten mindestens zwei unabhängige Quellen kein gemeinsames Thema.")}
      <div class="k-rang">${zaehl("Arten (6 Std. / 24 Std.)", r.arten)}${zaehl("Märkte und Klassen", r.klassen)}${zaehl("Branchen", r.branchen)}</div>
      <p class="k-hinweis">Das Radar zählt Meldungen aus 45 Feeds und GDELT; die Meldungen sind Quellenangaben, nicht geprüft. Mehrere Quellen zum selben Thema heißt nicht, dass es stimmt.</p></div>`;
}
function kiKurzHtml(k) {
  if (!k.vorhanden) return leerHtml(esc(k.hinweis));
  const zelle = p => p ? `<td>${pfeil(p.richtung)}<small>${p.wahrscheinlichkeit == null ? "" : Math.round(p.wahrscheinlichkeit * 100) + " %"}</small></td>` : `<td class="k-leise">–</td>`;
  return `<div class="rollbar"><table class="k-tab"><thead><tr><th>Wert</th><th>Kurz (1 Tag)</th><th>Mittel (3 Monate)</th><th>Lang (1 Jahr)</th><th>Stand</th></tr></thead><tbody>` +
    k.werte.map(z => z.vorhanden ? `<tr><td><b>${esc(z.wert)}</b></td>${zelle(z.kurz)}${zelle(z.mittel)}${zelle(z.lang)}<td class="k-leise">${esc(z.quelle)} · ${alterText(Math.round(z.alter_h * 60))}</td></tr>`
      : `<tr><td><b>${esc(z.wert)}</b></td><td colspan="4" class="k-leise">noch keine Einschätzung</td></tr>`).join("") + `</tbody></table></div>`;
}
const STAT_H = {"1": "1 Monat", "3": "3 Monate", "6": "6 Monate", "12": "12 Monate", "1T": "1 Tag", "1W": "1 Woche", "1M": "1 Monat", "3M": "3 Monate"};
function aehnlichHtml(a) {
  if (!a.vorhanden) return leerHtml(esc(a.fehler || a.hinweis));
  const treffer = a.treffer.map(t => `<tr><td>${esc(t.beginn)}</td><td>${esc(t.titel)}<small>${esc(t.art)} · ${esc(t.region || "")}</small></td><td class="r">${num(t.punkte, 1)}</td><td><small>${esc((t.gruende || []).join(", "))}</small></td></tr>`).join("");
  const hs = [...new Set(a.gruppen.flatMap(g => Object.keys(g.h)))];
  const stat = z => !z || !z.n ? `<td class="k-w n">–</td>` : z.median == null
    ? `<td class="k-w n">n = ${z.n}<small>${(z.werte || []).map(v => pz(v * 100, 1)).join(", ")}</small></td>`
    : wZelle(z.median * 100, 15, 1).replace("</td>", `<small>n = ${z.n}${z.anteil_negativ != null ? ` · ${Math.round(z.anteil_negativ * 100)} % im Minus` : ""}${z.auffaellig ? " · auffällig" : ""}</small></td>`);
  const gruppen = a.gruppen.map(g => `<tr><td>${esc(g.name)}<small>${esc(g.art)} · ${esc(g.takt)}</small></td>${hs.map(h => stat(g.h[h])).join("")}</tr>`).join("");
  return `<div class="k-kasten"><h3>Wichtigstes Thema: ${esc(a.thema.titel)} <small>${a.thema.verlage_24h} Quellen in 24 Std.${a.thema.arten.length ? " · Art " + esc(a.thema.arten.join(", ")) : ""}</small></h3>
      <p>${a.n} frühere Ereignisse ähneln der Lage${a.n_muster !== a.n ? ` (${a.n_muster} davon alt genug für eine Auswertung)` : ""}. Mindestens ${a.min_n} sind nötig, damit ein Median gezeigt wird.</p>
      ${a.treffer.length ? `<div class="rollbar"><table class="k-tab"><thead><tr><th>Beginn</th><th>Ereignis</th><th class="r">Punkte</th><th>Warum ähnlich</th></tr></thead><tbody>${treffer}</tbody></table></div>` : ""}
      ${a.gruppen.length ? `<h3 style="margin-top:14px">Wie die Märkte danach reagierten <small>Median der Rendite, n = Fälle</small></h3>
        <div class="rollbar"><table class="k-tab"><thead><tr><th>Markt</th>${hs.map(h => `<th class="r">${STAT_H[h] || esc(h)}</th>`).join("")}</tr></thead><tbody>${gruppen}</tbody></table></div>`
        : leerHtml(esc(a.hinweis_historie || ""))}
      ${(a.nicht_geladen || []).length ? `<p class="k-hinweis">Nicht geladen: ${a.nicht_geladen.map(x => esc(Array.isArray(x) ? x.join(" ") : x)).join(", ")}. Laden mit <code>python3 -m kitrader.historie laden …</code></p>` : ""}
      <p class="k-hinweis">Rückblick auf Ähnliches früher, keine Prognose: kleine Fallzahlen sagen wenig, und jede Lage hat eigene Gründe.</p></div>`;
}
function ereignisHtml() {
  const e = T.ereignisse; if (!e) return "";
  const liste = e.ereignisse.filter(x => !T.art || x.art === T.art).slice().reverse().slice(0, 60);
  const s = T.studie;
  const studie = !s ? `<p class="k-hinweis">Ereignis anklicken: Was tat die gewählte Reihe danach?</p>` : s.fehler ? fehlerHtml(s.fehler) : `
    <h3>${esc(s.ereignis.titel)} <small>${esc(s.ereignis.beginn)} · ${esc(s.ereignis.art)}${s.ereignis.region ? " · " + esc(s.ereignis.region) : ""}</small></h3>
    <p>${esc(s.ereignis.beschreibung || "")}</p>
    <p><b>${esc(s.titel_reihe)}</b>: Start ${esc(s.start.datum)} bei ${num(s.start.wert, s.start.wert < 10 ? 3 : 0)}.</p>
    <div class="rollbar"><table class="k-tab"><thead><tr><th>Nach</th><th class="r">Rendite</th><th>Stand</th></tr></thead><tbody>
      ${Object.entries(s.horizonte).map(([m, h]) => `<tr><td>${m} ${m === "1" ? "Monat" : "Monaten"}</td>${h.rendite == null ? `<td class="k-w n">noch offen</td>` : wZelle(h.rendite * 100, 20, 1)}<td class="k-leise">${esc(h.datum || h.ziel)}</td></tr>`).join("")}
      ${s.tief ? `<tr><td>Tiefpunkt</td>${wZelle(s.tief.rendite * 100, 20, 1)}<td class="k-leise">${esc(s.tief.datum)}</td></tr>` : ""}</tbody></table></div>
    ${s.erholung ? `<p class="k-hinweis">${s.erholung.erholt ? `Wieder auf dem Startwert nach ${s.erholung.tage} Tagen.` : "Noch nicht wieder auf dem Startwert."}</p>` : ""}
    ${(s.hinweise || []).map(h => `<p class="k-hinweis">${esc(h)}</p>`).join("")}`;
  return `<div class="rubrik"><h2>Historische Ereignisse</h2><span class="rechts">${e.ereignisse.length} Ereignisse · Klick zeigt die Ereignisstudie</span></div>
    <div class="k-gitter"><div class="k-kasten">
      <div class="k-eingabe" style="margin:0 0 8px"><select id="ev-art" aria-label="Art"><option value="">alle Arten</option>${e.arten.map(a => `<option ${a === T.art ? "selected" : ""}>${esc(a)}</option>`).join("")}</select>
        <select id="ev-reihe" aria-label="Reihe"><option value="btc" ${T.reihe === "btc" ? "selected" : ""}>Bitcoin</option><option value="dow" ${T.reihe === "dow" ? "selected" : ""}>Dow Jones</option></select></div>
      <div class="k-ereignisse rollbar"><table class="k-tab ereignis-tab"><tbody>${liste.map(x => `<tr data-id="${esc(x.id)}" class="${x.id === T.gewaehlt ? "gewaehlt" : ""}"><td>${esc(x.beginn)}</td><td>${esc(x.titel)}<small>${esc(x.art)}${x.region ? " · " + esc(x.region) : ""}</small></td></tr>`).join("")}</tbody></table></div></div>
      <div class="k-kasten" id="ev-studie">${studie}</div></div>`;
}
function trendsZeichnen() {
  const d = T.d;
  q("#tr-stand").textContent = `Stand ${zeit(d.stand)}`;
  q("#tr-inhalt").innerHTML = `
    <div class="rubrik"><h2>Welt-Rotation</h2><span class="rechts">wer gewinnt, wer verliert</span></div>${rotationHtml(d.welt)}
    <div class="rubrik"><h2>Nachrichten-Radar</h2><span class="rechts">worüber weltweit berichtet wird</span></div>${radarHtml(d.radar)}
    <div class="rubrik"><h2>KI-Richtung der Watchlist</h2><span class="rechts">aus der Morgenlage bzw. der letzten Einschätzung · Details im Reiter KI</span></div><div class="k-kasten">${kiKurzHtml(d.ki)}</div>
    <div class="rubrik"><h2>Ähnliche Fälle früher</h2><span class="rechts">zum wichtigsten aktuellen Thema</span></div>${aehnlichHtml(d.aehnlich)}
    ${ereignisHtml()}`;
  const art = q("#ev-art"), reihe = q("#ev-reihe");
  if (art) art.onchange = () => { T.art = art.value; trendsZeichnen(); };
  if (reihe) reihe.onchange = () => { T.reihe = reihe.value; if (T.gewaehlt) studieLaden(T.gewaehlt); else trendsZeichnen(); };
  document.querySelectorAll(".ereignis-tab tr[data-id]").forEach(tr => tr.onclick = () => studieLaden(tr.dataset.id));
}
async function studieLaden(id) {
  T.gewaehlt = id;
  try { T.studie = await api(`historie/studie?id=${encodeURIComponent(id)}&reihe=${encodeURIComponent(T.reihe)}`); }
  catch (e) { T.studie = {fehler: e.message}; }
  const st = q("#ev-studie").parentElement.parentElement.scrollTop;
  trendsZeichnen();
}

// =================================================================== KI
const KI = {wert: "", ergebnis: null, laeuft: false, ml: null, quote: null, erkannt: null};
K.ki = async function () {
  const w = q("#ki");
  if (!w.dataset.gebaut) {
    w.dataset.gebaut = "1";
    const i = await infoLaden();
    KI.wert = KI.wert || (i.watchlist[0] || "");
    w.innerHTML = `
      <div class="k-kasten">
        <h3>Wert wählen</h3>
        <div class="k-wert-wahl" id="ki-werte" role="group" aria-label="Watchlist"></div>
        <div class="k-eingabe">
          <input id="ki-frei" type="text" placeholder="BTC, Bitcoin, ETH/EUR, SOL, AAPL …" autocomplete="off" spellcheck="false" aria-label="Wert frei eingeben">
          <select id="ki-treiber" aria-label="KI-Treiber">
            <option value="einstellung">KI laut Einstellungen (${esc(i.ki_treiber)})</option><option value="aus">nur Kontext zeigen (KI aus)</option></select>
          <button class="haupt-knopf" id="ki-los" type="button">Einschätzen</button>
        </div>
        <div class="k-erkannt" id="ki-erkannt" aria-live="polite"></div>
        <p class="k-hinweis">Die KI schätzt für den Wert kurz (1 Tag), mittel (3 Monate) und lang (1 Jahr) ein: Richtung, Wahrscheinlichkeit, Begründung und Gegenargumente.
          Mit dem Treiber <code>claude_cli</code> kann das einige Minuten dauern. Jede Einschätzung kommt ins Journal und wird später am Kurs gemessen (Trefferquote unten).
          Eine Meinung zum Nachdenken, kein Handelssignal und keine Anlageberatung; die KI sendet nie Orders.</p>
      </div>
      <div id="ki-ergebnis"></div>
      <div class="rubrik"><h2>Letzte Morgenlage</h2><span class="rechts" id="ki-ml-stand"></span></div><div id="ki-ml"></div>
      <div class="rubrik"><h2>Trefferquote der KI</h2><span class="rechts">aus dem Journal, nur KI-Einschätzungen</span></div><div id="ki-quote"></div>`;
    q("#ki-los").onclick = kiLos;
    const frei = q("#ki-frei");
    frei.oninput = () => { clearTimeout(KI.zt); KI.zt = setTimeout(() => erkennen(frei.value), 350); };
    frei.onkeydown = e => { if (e.key === "Enter") kiLos(); };
    wertWahl();
  }
  kiMorgenlage(); kiQuote();
};
function wertWahl() {
  const i = info, z = q("#ki-werte");
  z.innerHTML = i.watchlist.map(s => `<button type="button" aria-pressed="${s === KI.wert && !q("#ki-frei").value}" data-w="${esc(s)}">${esc(s)}</button>`).join("") || `<span class="leise-text">Keine Werte in der Watchlist der Einstellungen.</span>`;
  z.querySelectorAll("button").forEach(b => b.onclick = () => { KI.wert = b.dataset.w; q("#ki-frei").value = ""; q("#ki-erkannt").textContent = ""; wertWahl(); });
}
async function erkennen(text) {
  const el = q("#ki-erkannt");
  text = text.trim();
  KI.erkannt = null;
  wertWahlMarke();
  if (!text) { el.textContent = ""; el.className = "k-erkannt"; return; }
  try {
    const r = await api("symbol?q=" + encodeURIComponent(text));
    if (q("#ki-frei").value.trim() !== text) return;
    if (r.erkannt) {
      KI.erkannt = r.symbol;
      el.className = "k-erkannt";
      el.innerHTML = `Erkannt: <b>${esc(r.symbol)}</b>${r.name && r.name !== r.symbol ? ` (${esc(r.name)})` : ""}${r.geprueft ? " · bei Kraken gefunden" + (r.paar ? ` (${esc(r.paar.altname)})` : "") : ""}${r.hinweis ? ` · ${esc(r.hinweis)}` : ""}`;
    } else { el.className = "k-erkannt fehler"; el.textContent = r.fehler; }
  } catch (e) { el.className = "k-erkannt fehler"; el.textContent = e.message; }
}
function wertWahlMarke() { document.querySelectorAll("#ki-werte button").forEach(b => b.setAttribute("aria-pressed", b.dataset.w === KI.wert && !q("#ki-frei").value)); }
async function kiLos() {
  if (KI.laeuft) return;
  const frei = q("#ki-frei").value.trim(), wert = frei || KI.wert;
  const z = q("#ki-ergebnis");
  if (!wert) { z.innerHTML = fehlerHtml("Bitte einen Wert wählen oder eingeben."); return; }
  KI.laeuft = true; q("#ki-los").disabled = true;
  z.innerHTML = leerHtml(`${esc(wert)} wird eingeschätzt … (Kerzen holen, Kontext bauen${q("#ki-treiber").value === "aus" ? "" : ", KI fragen – das kann einige Minuten dauern"})`);
  try {
    KI.ergebnis = await api("ki", {wert, treiber: q("#ki-treiber").value});
    kiZeichnen();
    kiMorgenlage(); kiQuote();
  } catch (e) { z.innerHTML = fehlerHtml(e); }
  KI.laeuft = false; q("#ki-los").disabled = false;
}
function kiZeichnen() {
  const e = KI.ergebnis, z = q("#ki-ergebnis");
  const karten = ["kurz", "mittel", "lang"].map(n => {
    const p = e.prognosen[n], h = HORIZONT[n];
    if (!p) return `<div class="k-kasten k-ki"><div class="horizont">${h[0]} · ${h[1]}</div><p class="k-leise" style="margin-top:8px">Keine Einschätzung (KI aus oder keine gültige Antwort).</p></div>`;
    const w = Math.round((p.wahrscheinlichkeit || 0) * 100);
    return `<div class="k-kasten k-ki"><div class="horizont">${h[0]} · ${h[1]}</div>
      <div class="gross">${pfeil(p.richtung)}</div>
      <div class="k-balken ${esc(p.richtung)}" role="img" aria-label="Wahrscheinlichkeit ${w} Prozent"><span style="width:${w}%"></span></div>
      <div class="leise-text">Wahrscheinlichkeit ${w} %</div>
      <h4>Begründung</h4><p>${esc(p.begruendung)}</p>
      ${p.gegenargumente ? `<h4>Gegenargumente</h4><p>${esc(Array.isArray(p.gegenargumente) ? p.gegenargumente.join(" · ") : p.gegenargumente)}</p>` : ""}
      ${p.was_widerlegt ? `<h4>Was die Einschätzung widerlegen würde</h4><p>${esc(p.was_widerlegt)}</p>` : ""}</div>`;
  }).join("");
  z.innerHTML = `<div class="rubrik"><h2>${esc(e.wert)} <span class="leise-text">Kurs ${num(e.preis, e.preis < 10 ? 4 : 2)} · ${zeit(e.zeit)} · Treiber ${esc(e.treiber)}</span></h2></div>
    ${(e.warnungen || []).map(w => `<div class="k-fehler">${esc(w)}</div>`).join("")}
    ${Object.keys(e.prognosen).length ? `<div class="k-karten">${karten}</div>` : leerHtml("Die KI ist aus: Es gibt nur den Kontext, den sie sonst bekäme.")}
    <details class="k-roh"><summary>Kontext, den die KI sieht</summary><div class="k-text">${esc(e.kontext || e.text)}</div></details>`;
}
async function kiMorgenlage() {
  try {
    const m = await api("morgenlage"), z = q("#ki-ml");
    if (!m.vorhanden) { z.innerHTML = leerHtml(`Noch keine Morgenlage. Sie entsteht täglich zur eingestellten Uhrzeit, solange kitrader läuft, oder per <code>python3 -m kitrader morgenlage</code>.`); q("#ki-ml-stand").textContent = ""; return; }
    q("#ki-ml-stand").textContent = `${zeit(m.daten.erstellt)} · Treiber ${m.daten.treiber} · ${alterText(Math.round(m.alter_h * 60))}`;
    const zelle = p => p ? `<td>${pfeil(p.richtung)}<small>${Math.round(p.wahrscheinlichkeit * 100)} %</small></td>` : `<td class="k-leise">–</td>`;
    const werte = m.daten.werte.map(w => w.preis == null ? `<tr><td><b>${esc(w.wert)}</b></td><td colspan="7" class="k-leise">${esc(w.fehler)}</td></tr>` :
      `<tr><td><b>${esc(w.wert)}</b></td><td class="r">${num(w.preis, w.preis < 10 ? 4 : 2)}</td>${["1", "7", "30"].map(f => `<td class="r ${vz((w.aenderung_prozent || {})[f])}">${pz((w.aenderung_prozent || {})[f], 1)}</td>`).join("")}${zelle((w.prognosen || {}).kurz)}${zelle((w.prognosen || {}).mittel)}${zelle((w.prognosen || {}).lang)}</tr>`).join("");
    z.innerHTML = `<div class="k-kasten"><div class="rollbar"><table class="k-tab"><thead><tr><th>Wert</th><th class="r">Kurs</th><th class="r">1 T</th><th class="r">7 T</th><th class="r">30 T</th><th>Kurz</th><th>Mittel</th><th>Lang</th></tr></thead><tbody>${werte}</tbody></table></div>
      ${m.daten.osiris && m.daten.osiris.veraenderung ? `<details class="k-roh"><summary>Was sich in der Weltlage verändert hat</summary><div class="k-text">${esc(m.daten.osiris.veraenderung)}</div></details>` : ""}
      <p class="k-hinweis">${esc(m.daten.hinweis || "")}</p></div>`;
  } catch (e) { q("#ki-ml").innerHTML = fehlerHtml(e); }
}
async function kiQuote() {
  try {
    const j = await api("journal?anzahl=1"), z = q("#ki-quote");
    const ki = j.trefferquote.filter(x => ["1d", "3M", "1J"].includes(x.horizont));
    const namen = {"1d": "Kurz (1 Tag)", "3M": "Mittel (3 Monate)", "1J": "Lang (1 Jahr)"};
    const quote = x => x.quote == null ? "noch keine" : `${num(x.quote, 0)} %`;
    z.innerHTML = `<div class="k-kasten"><div class="rollbar"><table class="k-tab"><thead><tr><th>Horizont</th><th class="r">ausgewertet</th><th class="r">Treffer</th><th class="r">Quote</th><th class="r">offen</th><th>Als Treffer zählt</th></tr></thead><tbody>
      ${ki.map(x => `<tr><td>${namen[x.horizont]}</td><td class="r">${x.n ?? 0}</td><td class="r">${x.treffer ?? 0}</td><td class="r"><b>${quote(x)}</b></td><td class="r">${x.offen ?? 0}</td><td class="k-leise">Bewegung ab ${x.schwelle} %</td></tr>`).join("")}</tbody></table></div>
      <p class="k-hinweis">Unter 30 ausgewerteten Fällen je Horizont ist die Quote kaum aussagekräftig. Auswertung läuft automatisch nach Ablauf des Horizonts, solange kitrader läuft.</p></div>`;
  } catch (e) { q("#ki-quote").innerHTML = fehlerHtml(e); }
}

// =================================================================== Weltlage
const W = {d: null, laeuft: false};
K.weltlage = async function () {
  const w = q("#weltlage");
  if (!w.dataset.gebaut) {
    w.dataset.gebaut = "1";
    w.innerHTML = `
      <div class="k-leiste"><button class="haupt-knopf" id="wl-abruf" type="button">Neu abrufen</button><span class="leise-text" id="wl-stand"></span></div>
      <div id="wl-meldung"></div>
      <p class="k-hinweis">Die Weltlage kommt von OSIRIS (osirisai.live): Vorfälle, Konfliktzonen, Länderrisiko, Seeverkehr, Märkte. Stufen, Risiko und Scores sind die von OSIRIS,
        nicht von kitrader. Eine Meinung zum Nachdenken, kein Handelssignal. „Neu abrufen“ ist höflich begrenzt.</p>
      <div id="wl-inhalt"></div>`;
    q("#wl-abruf").onclick = weltAbrufen;
  }
  try { W.d = await api("weltlage"); weltZeichnen(); } catch (e) { q("#wl-inhalt").innerHTML = fehlerHtml(e); }
};
async function weltAbrufen() {
  if (W.laeuft) return;
  W.laeuft = true; q("#wl-abruf").disabled = true;
  q("#wl-meldung").innerHTML = leerHtml("OSIRIS-Feeds werden abgerufen … (etwa eine halbe Minute)");
  try {
    const r = await api("weltlage/abrufen", {});
    const fehler = r.feeds.filter(f => f.fehler);
    q("#wl-meldung").innerHTML = `<div class="k-ok">${r.neu ? `${r.feeds.length - fehler.length} von ${r.feeds.length} Feeds abgerufen.` : esc(r.hinweis)}${fehler.length ? " Fehler: " + fehler.map(f => esc(f.name + ": " + f.fehler)).join("; ") : ""}</div>`;
  } catch (e) { q("#wl-meldung").innerHTML = fehlerHtml(e); }
  W.laeuft = false; q("#wl-abruf").disabled = false;
  try { W.d = await api("weltlage"); weltZeichnen(); } catch (e) {}
}
const stufe = s => `<span class="k-stufe ${esc(String(s).toLowerCase())}">${esc(s)}</span>`;
function weltZeichnen() {
  const d = W.d, z = q("#wl-inhalt");
  if (!d.vorhanden) { q("#wl-stand").textContent = ""; z.innerHTML = leerHtml("Noch kein OSIRIS-Schnappschuss gespeichert. „Neu abrufen“ holt ihn, oder <code>python3 -m kitrader osiris abrufen</code>."); return; }
  q("#wl-stand").textContent = `Stand ${zeit(d.stand)} (${alterText(d.alter_min)})`;
  const v = d.daten, teile = [];
  if (v.konflikte) {
    const zonen = v.konflikte.zonen.slice().sort((a, b) => (b.stufe === "war") - (a.stufe === "war"));
    teile.push(`<div class="k-kasten"><h3>Konfliktzonen <small>${zonen.length} Zonen, Liste von OSIRIS</small></h3><div class="rollbar"><table class="k-tab"><tbody>${zonen.map(x => `<tr><td>${esc(x.name)}</td><td>${stufe(x.stufe)}</td><td class="r k-leise">${x.meldungen ?? 0} Schlagzeilen</td></tr>`).join("")}</tbody></table></div></div>`);
  }
  if (v.vorfaelle) {
    const x = v.vorfaelle;
    teile.push(`<div class="k-kasten"><h3>Vorfälle <small>${x.gesamt} aktuelle Meldungen</small></h3>
      <div class="k-chips" style="margin:0 0 6px">${Object.entries(x.arten).map(([a, n]) => `<span class="k-chip">${esc(a)} ${n}</span>`).join("")}</div>
      <div class="k-chips" style="margin:0 0 6px">${Object.entries(x.regionen).map(([r, c]) => `<span class="k-chip region">${esc(r)} ${Object.values(c).reduce((s, n) => s + n, 0)}</span>`).join("")}</div>
      ${x.wichtig.length ? `<table class="k-tab"><tbody>${x.wichtig.slice(0, 8).map(w => `<tr><td>${stufe(w.stufe)}</td><td>${esc(w.text)}<small>${esc(w.ort)} · ${esc(w.art)}</small></td></tr>`).join("")}</tbody></table>` : `<p class="k-leise">Keine Alarmstufe Orange oder Rot.</p>`}</div>`);
  }
  if (v.laenderrisiko) {
    const x = v.laenderrisiko;
    teile.push(`<div class="k-kasten"><h3>Länderrisiko <small>laut OSIRIS, höchste</small></h3><div class="rollbar"><table class="k-tab"><tbody>${x.top.map(c => `<tr><td>${esc(c.land)}</td><td class="r">${num(c.wert, 0)}</td><td>${stufe(c.stufe)}<small>${esc((c.merkmale || []).join(", "))}</small></td></tr>`).join("")}</tbody></table></div></div>`);
  }
  if (v.schifffahrt) {
    const x = v.schifffahrt;
    teile.push(`<div class="k-kasten"><h3>Engpässe und Seeverkehr <small>${x.schiffe_live ?? "–"} Schiffe mit Live-Ortung</small></h3><div class="rollbar"><table class="k-tab"><tbody>${x.engpaesse.map(c => `<tr><td>${esc(c.name)}</td><td>${stufe(c.risiko)}</td><td class="k-leise">${esc(c.verkehr)}</td></tr>`).join("")}</tbody></table></div>
      <p class="k-hinweis">Häfen mit Stau: ${x.haefen_auffaellig.length ? x.haefen_auffaellig.map(p => esc(p.name + " " + p.stau)).join(", ") : `keine (von ${x.haefen_gesamt} Häfen alle normal)`}.</p></div>`);
  }
  if (v.nachrichten) {
    const x = v.nachrichten;
    teile.push(`<div class="k-kasten"><h3>Meldungen mit hohem Score <small>${x.gesamt} im Feed, ${x.hoch} mit Score ≥ 5</small></h3><div class="rollbar"><table class="k-tab"><tbody>${x.wichtig.slice(0, 8).map(m => `<tr><td class="k-leise">${zeit(m.zeit)}</td><td>${esc(m.titel)}<small>${esc(m.quelle)} (${esc(m.lager)})</small></td><td class="r">${m.wert}</td></tr>`).join("")}</tbody></table></div></div>`);
  }
  const sonst = [];
  if (v.erdbeben) sonst.push(`Erdbeben: ${v.erdbeben.gesamt} aktuelle, davon M≥5: ${v.erdbeben.ab_5}. ` + v.erdbeben.staerkste.map(b => `M${b.magnitude} ${esc(b.ort)}${b.tsunami ? " (Tsunami-Flag)" : ""}`).join("; "));
  if (v.unwetter) sonst.push(`Unwetter und Naturereignisse: ${v.unwetter.gesamt}${v.unwetter.hoch_gesamt ? `, davon schwer: ${v.unwetter.hoch_gesamt}` : ""}.`);
  if (v.cyber) sonst.push(`Cyber (CISA): Stufe ${esc(v.cyber.stufe)}, ${v.cyber.kritisch} kritisch unter den neuesten.`);
  if (v.netzausfaelle) sonst.push(`Internetausfälle: ${v.netzausfaelle.gesamt}${v.netzausfaelle.laender.length ? " in " + esc(v.netzausfaelle.laender.join(", ")) : ""}.`);
  if (v.lieferketten) sonst.push(`Lieferketten: ${v.lieferketten.kritisch ?? "–"} kritisch markiert, ${v.lieferketten.gestoert} gestörte Signale.`);
  if (sonst.length) teile.push(`<div class="k-kasten"><h3>Weiteres</h3>${sonst.map(t => `<p>${t}</p>`).join("")}</div>`);
  let maerkte = "";
  if (v.maerkte) {
    const namen = {indices: "Indizes", stocks: "Rüstung und Technik", oil: "Energie", commodities: "Rohstoffe", crypto: "Krypto", fx: "Devisen"};
    maerkte = `<div class="rubrik"><h2>Märkte laut OSIRIS</h2><span class="rechts">letzter Kurs und Tagesänderung</span></div><div class="k-gitter">` +
      Object.entries(v.maerkte.gruppen).map(([g, l]) => `<div class="k-kasten"><h3>${namen[g] || esc(g)}</h3><table class="k-tab"><tbody>${l.map(m => `<tr><td>${esc(m.name)}</td><td class="r">${num(m.preis, m.preis < 10 ? 4 : 2)}</td>${wZelle(m.aenderung, 3)}</tr>`).join("")}</tbody></table></div>`).join("") + `</div>`;
  }
  z.innerHTML = (v.fehlend && v.fehlend.length ? `<div class="k-fehler">Nicht abrufbar in diesem Schnappschuss: ${esc(v.fehlend.join(", "))}.</div>` : "") +
    `<div class="rubrik"><h2>Lage</h2><span class="rechts">Schnappschuss ${zeit(d.stand)}${d.vorher ? ", vorher " + zeit(d.vorher) : ""}</span></div><div class="k-gitter">${teile.join("")}</div>${maerkte}
    <details class="k-roh"><summary>Gesamttext (so sieht ihn die KI)</summary><div class="k-text">${esc(d.text)}</div></details>
    <p class="k-hinweis">${esc(d.hinweis)}</p>`;
}

// =================================================================== Autopilot
const A = {d: null, laeuft: false};
K.autopilot = async function () {
  const w = q("#autopilot");
  if (!w.dataset.gebaut) {
    w.dataset.gebaut = "1";
    w.innerHTML = `<div class="k-banner" id="ap-banner" role="alert">NOT-AUS IST AKTIV: Es wird nichts mehr gehandelt. Aufheben nur am Rechner mit: python3 -m kitrader notaus --aufheben</div>
      <div id="ap-inhalt">${leerHtml("wird geladen …")}</div>
      <dialog class="k-dialog" id="ap-dialog" aria-labelledby="ap-dialog-titel"></dialog>`;
  }
  await autopilotLaden();
  clearInterval(timer);
  timer = setInterval(() => { if (!q("#autopilot").hidden && !document.hidden) autopilotLaden(); }, 6000);
};
async function autopilotLaden() {
  try { A.d = await api("autopilot"); autopilotZeichnen(); }
  catch (e) { q("#ap-inhalt").innerHTML = fehlerHtml(e); }
}
const STATUS_AP = {laeuft: ["läuft", "an"], pause: ["Pause nach einem Fehler", "pause"], wartet: ["startet …", "pause"], gestoppt: ["gestoppt", ""], aufgegeben: ["aufgegeben", ""], "nicht gestartet": ["nicht gestartet", ""]};
const ORDER_TEXT = {kauf: "Kauf", verkauf: "Verkauf"};
function autopilotZeichnen() {
  const d = A.d, u = d.uebersicht, z = q("#ap-inhalt");
  q("#ap-banner").classList.toggle("an", !!u.notaus.aktiv);
  const st = STATUS_AP[d.status] || [d.status, ""];
  const MODUS = {papier: "Simulation mit echten Kursen, kein echtes Geld", demo: "Demokonto beim Broker, kein echtes Geld", echt: "ECHTES GELD: Orders gehen an den Broker"};
  const k = u.konto;
  const grenzen = u.grenzen.map(g => `<div class="k-grenze ${g.status === "erreicht" ? "rot" : g.status === "nah" ? "warn" : ""}"><b>${esc(g.name)}</b>
      <div>${g.anteil == null ? `<span class="k-leise">–</span>` : `<div class="balken" title="${Math.round(g.anteil * 100)} % der Grenze"><span style="width:${Math.min(100, Math.round(g.anteil * 100))}%"></span></div>`}</div>
      <span>${esc(g.aktuell)} <span class="k-leise">von ${esc(g.grenze)}</span></span>${g.hinweis ? `<small class="k-leise" style="grid-column:1/-1">${esc(g.hinweis)}</small>` : ""}</div>`).join("");
  const pos = u.positionen.length ? `<div class="rollbar"><table class="k-tab"><thead><tr><th>Symbol</th><th>Richtung</th><th class="r">Menge</th><th class="r">Einstand</th><th class="r">Kurs</th><th class="r">Ergebnis</th></tr></thead><tbody>${
    u.positionen.map(p => `<tr><td><b>${esc(p.symbol)}</b></td><td>${esc(p.richtung)}</td><td class="r">${num(Math.abs(p.menge), 4)}</td><td class="r">${num(p.einstand, p.einstand < 10 ? 4 : 2)}</td><td class="r">${num(p.kurs, p.kurs < 10 ? 4 : 2)}</td><td class="r ${vz(p.ergebnis)}">${p.ergebnis == null ? "–" : num(p.ergebnis, 2) + " " + esc(p.waehrung || "")}</td></tr>`).join("")}</tbody></table></div>` : `<p class="k-leise">Keine offenen Positionen.</p>`;
  const ord = u.orders.length ? `<div class="rollbar"><table class="k-tab"><thead><tr><th>Symbol</th><th>Order</th><th class="r">Menge</th><th class="r">Preis</th><th>Status</th><th>Hinweis</th></tr></thead><tbody>${
    u.orders.map(o => `<tr><td><b>${esc(o.symbol)}</b></td><td>${ORDER_TEXT[o.seite] || esc(o.seite)} ${esc(o.art)}</td><td class="r">${num(o.menge, 4)}</td><td class="r">${o.stop ? "Stop " + num(o.stop, 2) : o.limit ? "Limit " + num(o.limit, 2) : "Markt"}</td><td>${esc(o.status)}</td><td class="k-leise">${esc(o.hinweis || "")}</td></tr>`).join("")}</tbody></table></div>` : `<p class="k-leise">Keine offenen Orders.</p>`;
  const entsch = d.entscheidungen.length ? `<div class="rollbar"><table class="k-tab"><thead><tr><th>Zeit</th><th>Symbol</th><th>Strategie</th><th>Entscheidung</th><th>Gründe</th></tr></thead><tbody>${
    d.entscheidungen.map(e => {
      const o = e.order, was = o ? `${ORDER_TEXT[o.seite] || o.seite} ${num(o.menge, 4)}` + (o.stop_loss ? ` · Stop ${num(o.stop_loss, 2)}` : "") + (o.take_profit ? ` · Ziel ${num(o.take_profit, 2)}` : "") : (e.signal ? "kein Handel" : "–");
      const ok = e.pruefung == null ? "" : e.pruefung ? `<span class="k-gut">freigegeben</span>` : `<span class="k-schlecht">abgelehnt</span>`;
      const aus = e.ausfuehrung ? ` · ${esc(e.ausfuehrung.status || "")}` : "";
      return `<tr><td class="k-leise">${zeit(e.zeit)}</td><td><b>${esc(e.symbol)}</b></td><td>${esc(e.strategie)}</td><td>${esc(was)}<small>${ok}${aus}${e.ergebnis != null ? " · Ergebnis " + num(e.ergebnis, 2) : ""}</small></td><td>${(e.gruende || []).map(esc).join("<br>") || `<span class="k-leise">${esc(e.notiz || "")}</span>`}</td></tr>`;
    }).join("")}</tbody></table></div>` : leerHtml("Noch keine Entscheidungen. Sobald die Strategie ein Signal meldet, steht es hier mit der Risikoprüfung, auch wenn sie es ablehnt.");
  const quote = d.trefferquote_ki.filter(x => ["1d", "3M", "1J"].includes(x.horizont)).map(x => `${{"1d": "kurz", "3M": "mittel", "1J": "lang"}[x.horizont]}: ${x.quote == null ? "noch keine" : num(x.quote, 0) + " %"} (n = ${x.n ?? 0})`).join(" · ");
  const r = d.grenzen_einstellung;
  z.innerHTML = `
    <div class="k-modus ${esc(u.modus)}"><div><div class="klein">Betriebsart</div><div class="gross">${esc(u.modus_text)}</div></div>
      <div class="klein">${esc(MODUS[u.modus] || "")}${u.freigabe ? `<br><b>${esc(u.freigabe.text)}</b>` : ""}</div>
      <button class="k-notaus" id="ap-notaus" type="button">NOT-AUS</button></div>
    ${u.warnungen.map(w => `<div class="k-fehler">${esc(w)}</div>`).join("")}${u.fehler.map(w => `<div class="k-fehler">${esc(w)}</div>`).join("")}
    <div class="k-gitter">
      <div class="k-kasten"><h3><span class="k-status-punkt ${st[1]}"></span>Autopilot: ${esc(st[0])}</h3>
        <div class="k-leiste">${d.steuerbar ? `<button class="haupt-knopf" id="ap-start" type="button" ${d.gestartet ? "disabled" : ""}>Autopilot starten</button><button class="leise" id="ap-stopp" type="button" ${d.gestartet ? "" : "disabled"}>Autopilot stoppen</button>`
          : `<span class="k-leise">Start und Stopp hier nur im Papierbetrieb. In dieser Betriebsart: <code>python3 -m kitrader start --modus ${esc(u.modus)}</code></span>`}</div>
        <div id="ap-meldung"></div>
        ${d.fehler ? `<p class="k-schlecht">${esc(d.fehler)}</p>` : ""}
        <p>Strategie <b>${esc(d.strategie)}</b> · Takt ${esc(d.takt || "–")} · KI-Filter ${d.ki_filter ? "an" : "aus"}</p>
        <p>Symbole: ${d.symbole.length ? d.symbole.map(s => `<span class="k-chip markt">${esc(s)}</span>`).join(" ") : `<span class="k-leise">keine eingestellt (autopilot.symbole)</span>`}</p>
        ${d.seit ? `<p class="k-leise">läuft seit ${zeit(d.seit)}${d.neustarts ? `, ${d.neustarts} Neustarts` : ""}</p>` : ""}
        <p class="k-hinweis">Der Autopilot handelt nur im Papierbetrieb von hier aus, jede Order geht durch die Risikoprüfung. Echtgeld braucht Einstellung und Freigabedatei und startet nur über die Kommandozeile.</p></div>
      <div class="k-kasten"><h3>Konto <small>${k ? (k.simulation ? "Simulation" : "Konto des Brokers") : ""}</small></h3>${k ? `
        <table class="k-tab"><tbody><tr><td>Gesamtwert</td><td class="r"><b>${num(k.gesamtwert)} ${esc(k.waehrung)}</b></td></tr><tr><td>Guthaben</td><td class="r">${num(k.guthaben)} ${esc(k.waehrung)}</td></tr>
        <tr><td>Ergebnis heute</td><td class="r ${vz(k.tagesergebnis)}"><b>${num(k.tagesergebnis)} ${esc(k.waehrung)}</b></td></tr></tbody></table><p class="k-hinweis">Tagesergebnis: ${esc(k.tagesergebnis_quelle)}.</p>` : leerHtml("Konto nicht lesbar.")}
        <p class="k-hinweis">Trefferquote der KI-Einschätzungen: ${quote || "noch keine"}</p></div>
    </div>
    <div class="rubrik"><h2>Risiko-Grenzen und Tagesstand</h2><span class="rechts">${u.tag_gesperrt ? "<b class='k-schlecht'>heute gesperrt</b>" : "Stand der Grenzen"}</span></div>
    <div class="k-kasten">${grenzen}</div>
    <div class="rubrik"><h2>Offene Positionen</h2></div><div class="k-kasten">${pos}</div>
    <div class="rubrik"><h2>Offene Orders</h2></div><div class="k-kasten">${ord}</div>
    <div class="rubrik"><h2>Letzte Entscheidungen</h2><span class="rechts">mit Gründen, auch die abgelehnten · neueste zuerst</span></div><div class="k-kasten">${entsch}</div>
    ${u.protokoll.length ? `<div class="rubrik"><h2>Protokoll</h2></div><div class="k-kasten"><table class="k-tab"><tbody>${u.protokoll.slice().sort((a, b) => String(b.zeit).localeCompare(String(a.zeit))).map(e => `<tr><td class="k-leise">${zeit(e.zeit)}</td><td>${esc(e.art)}</td><td>${esc(e.text)}</td></tr>`).join("")}</tbody></table></div>` : ""}
    <p class="k-hinweis">Keine Anlageberatung, kein Erfolgsversprechen. Papierhandel ist ein Technik-Test. Grenzen: Tagesverlust ${num(r.tagesverlust_max, 0)} ${esc(u.konto ? u.konto.waehrung : "")} oder ${num(r.tagesverlust_max_prozent, 1)} %, Position höchstens ${num(r.position_max_prozent, 0)} %, Risiko je Trade ${num(r.risiko_pro_trade_prozent, 2)} %, Stop-Loss ${r.stop_loss_pflicht ? "Pflicht" : "keine Pflicht"}, Short ${r.short_erlaubt ? "erlaubt" : "verboten"} (Einstellungen).</p>`;
  q("#ap-notaus").onclick = notausDialog;
  const s = q("#ap-start"), t = q("#ap-stopp");
  if (s) s.onclick = () => autopilotSchalten("start");
  if (t) t.onclick = () => autopilotSchalten("stopp");
}
async function autopilotSchalten(was) {
  const m = q("#ap-meldung");
  try { const r = await api("autopilot/" + was, {}); m.innerHTML = `<div class="k-ok">${esc(r.hinweis)}</div>`; }
  catch (e) { m.innerHTML = fehlerHtml(e); }
  setTimeout(autopilotLaden, 1500);
}
function notausDialog() {
  const dlg = q("#ap-dialog"), modus = A.d.uebersicht.modus_text;
  dlg.innerHTML = `<h3 id="ap-dialog-titel">Not-Aus auslösen?</h3>
    <p>Das storniert die offenen Orders und stellt die Positionen der <b>eigenen Symbole</b> (autopilot.symbole und vom Autopiloten eröffnete) zum Marktpreis glatt (Betriebsart <b>${esc(modus)}</b>)
    und sperrt danach jeden Handel. Fremde Positionen bleiben unberührt.</p>
    <p class="k-hinweis">Aufheben geht bewusst nur am Rechner: <code>python3 -m kitrader notaus --aufheben</code></p>
    <div id="ap-schritte"></div>
    <div class="knoepfe"><button class="leise" id="ap-abbruch" type="button">Abbrechen</button><button class="k-notaus" id="ap-ja" type="button" style="font-size:16px;padding:9px 22px;margin:0">NOT-AUS jetzt</button></div>`;
  q("#ap-abbruch").onclick = () => dlg.close();
  q("#ap-ja").onclick = async () => {
    q("#ap-ja").disabled = true;
    try {
      const e = await api("notaus", {});
      q("#ap-schritte").innerHTML = `<div class="${e.ok ? "k-ok" : "k-fehler"}">${e.ok ? "Not-Aus ausgelöst. Der Handel ist gesperrt." : "ACHTUNG: " + esc(e.fehler)}</div><ol>${(e.schritte || []).map(x => `<li>${esc(x)}</li>`).join("")}</ol>`;
      q("#ap-ja").hidden = true; q("#ap-abbruch").textContent = "Schließen";
      autopilotLaden();
    } catch (err) { q("#ap-schritte").innerHTML = `<div class="k-fehler">Not-Aus NICHT bestätigt: ${esc(err.message)}. Auf dem Rechner ausführen: python3 -m kitrader notaus</div>`; q("#ap-ja").disabled = false; }
  };
  dlg.showModal();
}

// =================================================================== Umschalten
window.kZeigen = function (ziel) {
  const a = document.activeElement;       // Mausklick auf den Reiter: Fokus freigeben, sonst bleibt die Seitenleiste ausgeklappt und deckt Knöpfe zu
  if (a && a.closest && a.closest(".seitenleiste") && !a.matches(":focus-visible")) a.blur();
  if (ziel !== "autopilot") clearInterval(timer);
  if (K[ziel]) { const t = q("#" + ziel); K[ziel]().catch(e => { t.innerHTML = fehlerHtml(e); }); }
};
try { if (K[seite]) { kZeigen(seite); q("#stand").textContent = window.K_STAND[seite]; } } catch (e) {}   // Seite wurde direkt mit #trends usw. geöffnet
})();
