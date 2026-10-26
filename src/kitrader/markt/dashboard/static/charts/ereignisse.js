/*
 * ereignisse.js – kitrader: historische Ereignisse als Marker im Chart.
 *
 * Bei Tages- und Wochenkerzen automatisch, bei allen anderen Zeiteinheiten auf Wunsch (Schalter „Ereignisse“ in der Kopfzeile; die
 * Wahl wird im Browser gemerkt). Die Ereignisse kommen aus kitrader.historie (/api/k/chart/ereignisse, nur lesend). Im sichtbaren
 * Ausschnitt stehen höchstens 60 Marker, wichtige Arten zuerst; Form und Farbe zeigen die Gruppe. Hover zeigt Titel, Datum und Quelle,
 * Klick auf einen Marker öffnet die Karte mit Beschreibung und der Ereignisstudie für den aktuellen Wert (oder BTC bzw. Dow, wenn es
 * für den Wert keine eigene Reihe gibt). Rückblick, keine Prognose.
 */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const A = () => window.__kitrader;
  const MAX = 60;
  const SPEICHER = 'kitrader.ereignisse';
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // Gruppen: Form und Farbe der Marker
  const GRUPPEN = {
    krise: { name: 'Krisen und Crashs', form: 'circle', farbe: '#f05454', arten: ['boersencrash', 'finanzkrise', 'bankenkrise', 'schuldenkrise'] },
    wirtschaft: { name: 'Wirtschaft und Geld', form: 'circle', farbe: '#f5b942', arten: ['rezession', 'zinsschock', 'rohstoffschock', 'inflation', 'waehrung', 'geldpolitik'] },
    gewalt: { name: 'Krieg und Gewalt', form: 'square', farbe: '#b8a2ff', arten: ['krieg', 'konflikt', 'terror', 'unruhen', 'sanktionen'] },
    politik: { name: 'Politik, Handel, Regeln', form: 'square', farbe: '#6ea8ff', arten: ['politik', 'wahl', 'regulierung', 'handel', 'krypto'] },
    natur: { name: 'Seuchen und Katastrophen', form: 'circle', farbe: '#5ad7e6', arten: ['pandemie', 'naturkatastrophe', 'unglueck'] },
  };
  const GRUPPE_VON_ART = Object.fromEntries(Object.entries(GRUPPEN).flatMap(([g, v]) => v.arten.map((a) => [a, g])));
  const ARTNAME = { boersencrash: 'Börsencrash', finanzkrise: 'Finanzkrise', bankenkrise: 'Bankenkrise', schuldenkrise: 'Schuldenkrise', rezession: 'Rezession',
    zinsschock: 'Zinsschock', rohstoffschock: 'Rohstoffschock', inflation: 'Inflation', waehrung: 'Währung', geldpolitik: 'Geldpolitik', krieg: 'Krieg',
    konflikt: 'Konflikt', terror: 'Terror', unruhen: 'Unruhen', sanktionen: 'Sanktionen', politik: 'Politik', wahl: 'Wahl', regulierung: 'Regulierung',
    handel: 'Handel', krypto: 'Krypto', pandemie: 'Pandemie', naturkatastrophe: 'Naturkatastrophe', unglueck: 'Unglück' };
  const stil = (art) => GRUPPEN[GRUPPE_VON_ART[art]] || GRUPPEN.politik;
  const datum = (iso) => { const [j, m, t] = String(iso).split('-'); return t ? `${t}.${m}.${j}` : m ? `${m}.${j}` : j; };
  const zeitraum = (e) => {
    if (e.genauigkeit === 'monat') return new Date(e.beginn + 'T12:00:00Z').toLocaleDateString('de-DE', { month: 'long', year: 'numeric', timeZone: 'UTC' });
    return datum(e.beginn) + (e.ende ? ' bis ' + datum(e.ende) : '');
  };
  const prozent = (x) => (x == null ? '–' : `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`);
  const ton = (x) => (x == null ? '' : x > 0 ? 'pos' : x < 0 ? 'neg' : '');

  let wahl = null;                                   // null = automatisch (Tages- und Wochenkerzen), true/false = vom Nutzer
  try { const w = localStorage.getItem(SPEICHER); wahl = w === 'an' ? true : w === 'aus' ? false : null; } catch (e) { /* kein Speicher */ }
  let daten = null, reihe = null, signatur = '', anfrage = 0, sichtbar = [], zeiger = { x: 0, y: 0 }, karteId = null;

  const aktiv = () => { const S = A(); return wahl ?? (S.state.tf === '1d' || S.state.tf === '1w'); };
  const art = () => (A().state.kind === 'crypto' ? 'krypto' : 'aktie');
  const tagDatum = (t) => new Date(t * 1000).toISOString().slice(0, 10);

  // ---------------- Daten ----------------
  async function laden() {
    const S = A(), b = S.state.bars, sym = S.state.symbol, n = ++anfrage;
    if (!b.length) return;
    const url = `/api/k/chart/ereignisse?symbol=${encodeURIComponent(sym)}&art=${art()}&von=${tagDatum(b[0].time)}&bis=${tagDatum(b[b.length - 1].time + S.TFS.find((t) => t.id === S.state.tf).sec)}`;
    let d;
    try { d = await (await fetch(url, { cache: 'no-store' })).json(); } catch (e) { return; }
    if (n !== anfrage || d.fehler) return;
    reihe = d.reihe;
    daten = d.ereignisse.map((e) => ({ ...e, start: Date.parse(e.beginn + 'T00:00:00Z') / 1000 }));
    zuordnen();
    zeichnen();
  }
  // Kerze je Ereignis: die erste, die nach dem Beginn noch läuft oder beginnt (Wochenende und Feiertage landen am nächsten Handelstag)
  function zuordnen() {
    const S = A(), b = S.state.bars, sek = S.TFS.find((t) => t.id === S.state.tf).sec;
    daten.forEach((e) => {
      e.idx = null;
      if (!b.length || e.start + 86400 <= b[0].time || e.start >= b[b.length - 1].time + sek) return;
      let lo = 0, hi = b.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (b[mid].time + sek > e.start) hi = mid; else lo = mid + 1; }
      if (b[lo].time + sek > e.start) e.idx = lo;
    });
  }
  function auswahl() {
    const S = A(), n = S.state.bars.length;
    if (!daten || !n) return [];
    const r = S.main.timeScale().getVisibleLogicalRange();
    const von = r ? Math.max(0, Math.floor(r.from)) : 0, bis = r ? Math.min(n - 1, Math.ceil(r.to)) : n - 1;
    const alle = daten.filter((e) => e.idx != null && e.idx >= von && e.idx <= bis);
    return alle.sort((x, y) => x.rang - y.rang || (x.beginn < y.beginn ? 1 : -1)).slice(0, MAX).sort((x, y) => x.idx - y.idx || x.rang - y.rang);
  }
  function zeichnen() {
    const S = A();
    if (!aktiv() || !daten) { sichtbar = []; S.setMarkerQuelle('ereignisse', []); knopf(); tippWeg(); return; }
    sichtbar = auswahl();
    S.setMarkerQuelle('ereignisse', sichtbar.map((e) => ({ time: S.state.times[e.idx], position: 'aboveBar', shape: stil(e.art).form, color: stil(e.art).farbe, size: 0.8, id: 'ev:' + e.id })));
    knopf();
  }
  function knopf() {
    const k = $('#btnEreignisse'); if (!k) return;
    const an = aktiv();
    k.classList.toggle('on', an);
    k.textContent = an && daten ? `Ereignisse · ${sichtbar.length}` : 'Ereignisse';
    const legende = Object.values(GRUPPEN).map((g) => `${g.form === 'circle' ? '●' : '■'} ${g.name}`).join('\n');
    k.title = `Historische Ereignisse als Marker (kitrader.historie), höchstens ${MAX} im Bild, wichtige Arten zuerst. Klick auf einen Marker: Beschreibung, Quelle und Ereignisstudie.\n${legende}\n${wahl == null ? 'Aus bei 1D und 1W automatisch an; ' : ''}Klick schaltet um.`;
  }

  // ---------------- Hover und Karte ----------------
  const einzel = (id) => (daten || []).find((e) => 'ev:' + e.id === id);
  function tippZeigen(e) {
    let t = $('#evTipp');
    if (!t) { t = document.createElement('div'); t.id = 'evTipp'; $('#stage').appendChild(t); }
    const s = stil(e.art), box = $('#stage').getBoundingClientRect();
    t.innerHTML = `<b style="color:${s.farbe}">${s.form === 'circle' ? '●' : '■'} ${esc(ARTNAME[e.art] || e.art)}</b> · ${esc(zeitraum(e))}<div class="t">${esc(e.titel)}</div><div class="q">${esc(e.quelle.name)} · Klick für Studie</div>`;
    t.style.display = 'block';
    const w = t.offsetWidth, h = t.offsetHeight;
    t.style.left = Math.max(8, Math.min(box.width - w - 8, zeiger.x - box.left + 14)) + 'px';
    t.style.top = Math.max(8, Math.min(box.height - h - 8, zeiger.y - box.top + 16)) + 'px';
  }
  function tippWeg() { const t = $('#evTipp'); if (t) t.style.display = 'none'; }

  function karte() {
    let k = $('#evKarte');
    if (!k) {
      k = document.createElement('aside'); k.id = 'evKarte'; k.setAttribute('aria-label', 'Ereignis');
      $('#stage').appendChild(k);
      k.addEventListener('click', (ev) => { if (ev.target.closest('[data-zu]')) { k.style.display = 'none'; karteId = null; } });
    }
    return k;
  }
  function studieHtml(r) {
    const s = r.studie, rh = r.reihe;
    let h = `<div class="ev-studie"><div class="ev-kopf">Ereignisstudie · ${esc(rh.titel)}</div>`;
    if (!rh.eigene || rh.hinweis) h += `<div class="ev-hinweis">${esc(rh.hinweis || '')}</div>`;
    if (!s) return h + `<div class="ev-hinweis">${esc(r.fehler || 'Keine Studie möglich.')}</div></div>`;
    if (!s.start) return h + `<div class="ev-hinweis">${esc((s.hinweise || []).join(' ') || 'Keine Werte zum Beginn.')}</div></div>`;
    const zeile = (name, v) => `<div><span>${name}</span><b class="${ton(v)}">${prozent(v)}</b></div>`;
    h += `<div class="ev-zahlen">`;
    [1, 3, 6, 12].forEach((m) => { const x = s.horizonte[m]; h += zeile(`${m} Mon.`, x ? x.rendite : null); });
    h += `</div>`;
    if (s.tief) h += `<div class="ev-zeile">Größter Rückgang binnen 12 Monaten <b class="${ton(s.tief.rendite)}">${prozent(s.tief.rendite)}</b> am ${datum(s.tief.datum)}${s.tief.fenster_vollstaendig ? '' : ' (noch offen)'}</div>`;
    if (s.erholung) h += `<div class="ev-zeile">${s.erholung.erholt ? (s.erholung.tage ? `Erholt nach ${s.erholung.tage} Tagen auf den Stand vom Beginn` : 'Kein Rückgang unter den Stand vom Beginn') : `Bis ${datum(s.erholung.bis)} nicht wieder auf dem Stand vom Beginn`}</div>`;
    if (s.ende) h += `<div class="ev-zeile">Bis zum Ende (${datum(s.ende.ende)}) <b class="${ton(s.ende.rendite)}">${prozent(s.ende.rendite)}</b></div>`;
    h += `<div class="ev-zeile ev-leise">Start ${datum(s.start.datum)} · ${esc(s.takt === 'monat' ? 'Monatswerte' : 'Tageswerte')}. Rückblick, keine Prognose; Ereignisse überschneiden sich.</div></div>`;
    return h;
  }
  async function karteOeffnen(e) {
    const k = karte(), s = stil(e.art);
    karteId = e.id;
    k.style.display = 'block';
    k.innerHTML = `<button class="ev-zu" data-zu title="Schließen">✕</button>
      <div class="ev-art" style="color:${s.farbe}">${s.form === 'circle' ? '●' : '■'} ${esc(ARTNAME[e.art] || e.art)} · ${esc(e.region)}</div>
      <h3>${esc(e.titel)}</h3>
      <div class="ev-datum">${esc(zeitraum(e))}${e.genauigkeit === 'monat' ? ' (Tag nicht genau bekannt)' : ''}</div>
      <div class="ev-quelle">Quelle: ${e.quelle.url ? `<a href="${esc(e.quelle.url)}" target="_blank" rel="noopener noreferrer">${esc(e.quelle.name)}</a>` : esc(e.quelle.name)}</div>
      <div class="ev-laedt">Beschreibung und Studie laden …</div>`;
    let r;
    try { r = await (await fetch(`/api/k/chart/studie?id=${encodeURIComponent(e.id)}&symbol=${encodeURIComponent(A().state.symbol)}&art=${art()}`, { cache: 'no-store' })).json(); }
    catch (err) { r = { fehler: 'Dashboard nicht erreichbar' }; }
    if (karteId !== e.id) return;
    const platz = k.querySelector('.ev-laedt');
    if (r.fehler && !r.ereignis) { platz.outerHTML = `<div class="ev-hinweis">${esc(r.fehler)}</div>`; return; }
    platz.outerHTML = `<p class="ev-text">${esc(r.ereignis.beschreibung)}</p>` + studieHtml(r);
  }

  // ---------------- Start ----------------
  function start() {
    const S = A();
    if (!S || !S.state.bars.length || !S.main) return setTimeout(start, 300);
    const k = $('#btnEreignisse');
    k.onclick = () => {
      wahl = !aktiv();
      try { localStorage.setItem(SPEICHER, wahl ? 'an' : 'aus'); } catch (e) { /* kein Speicher */ }
      if (aktiv() && !daten) laden(); else zeichnen();
    };
    knopf();
    const el = S.main.chartElement();
    el.addEventListener('pointermove', (e) => { zeiger = { x: e.clientX, y: e.clientY }; }, true);
    S.main.subscribeCrosshairMove((p) => {
      const e = typeof p.hoveredObjectId === 'string' ? einzel(p.hoveredObjectId) : null;
      if (e) tippZeigen(e); else tippWeg();
      el.style.cursor = e ? 'pointer' : '';
    });
    S.main.subscribeClick((p) => { const e = typeof p.hoveredObjectId === 'string' ? einzel(p.hoveredObjectId) : null; if (e) karteOeffnen(e); });
    let tm = null;
    S.main.timeScale().subscribeVisibleLogicalRangeChange(() => { clearTimeout(tm); tm = setTimeout(zeichnen, 120); });
    setInterval(() => {                                          // Wert, Zeiteinheit oder Kerzen gewechselt: neu laden
      const b = S.state.bars, sig = S.state.symbol + '|' + S.state.tf + '|' + (b[0]?.time || '') + '|' + (b.length ? b[b.length - 1].time : '');
      if (sig === signatur) return;
      const gleicheReihe = signatur.split('|').slice(0, 3).join('|') === sig.split('|').slice(0, 3).join('|');
      signatur = sig;
      if (!aktiv()) { daten = null; zeichnen(); return; }
      if (gleicheReihe && daten) { zuordnen(); zeichnen(); return; }   // nur eine neue Kerze
      const k2 = $('#evKarte'); if (k2) { k2.style.display = 'none'; karteId = null; }
      daten = null; laden();
    }, 400);
  }
  window.__kitraderEreignisse = { sichtbar: () => sichtbar, daten: () => daten, reihe: () => reihe };   // für Tests
  start();
})();
