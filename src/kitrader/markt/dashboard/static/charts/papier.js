/*
 * papier.js – kitrader: Papierhandel in kitrader Charts (simuliert, kein echtes Geld).
 *
 * Orders werden nur hier aufgegeben: über die Karte „Papierhandel“ rechts oder mit dem Order-Werkzeug links
 * (Klick in den Chart setzt den Preis: beim Kauf darunter Limit, darüber Stopp; beim Verkauf umgekehrt).
 * Preis, Menge und Gesamtbetrag hängen zusammen: wer den Betrag eingibt, bekommt die Stückzahl und umgekehrt.
 * Ausgeführt wird im Kern des Dashboards (/api/papier, live/papier.py) gegen Kurs und Orderbuch.
 * Im Chart: Pfeile für Ausführungen, Linie und Schild mit ✕ für jede offene Order, eine Linie für den Einstand, Stop und Ziel.
 *
 * Erweitert: Short (Verkauf ohne Bestand), Pflicht-Stop und optionales Ziel für jede Eröffnung, Prüfung durch kitrader.risiko vor dem Absenden
 * (/api/papier/pruefen zeigt Kennzahlen und Ablehnungsgründe live), Position schließen (/api/papier/schliessen). Stop und Ziel überwacht der Kern.
 */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const A = () => window.__kitrader;
  const ZEICHEN = { EUR: '€', USD: '$' };
  const zahl = (x, n = 2) => (x == null || !isFinite(x) ? '–' : x.toLocaleString('de-DE', { minimumFractionDigits: n, maximumFractionDigits: n }));
  const preis = (x) => (x == null ? '–' : zahl(x, x < 10 ? 3 : 2));
  const sek = (iso) => (iso ? Date.parse(String(iso).replace(/(\.\d{3})\d+/, '$1')) / 1000 : null);   // Nanosekunden kürzen
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const wert = () => daten?.handelbar?.[A().state.symbol];
  const krypto = () => wert()?.markt === 'KRYPTO';
  const stueck = (m, k = krypto()) => zahl(m, k ? (m < 1 ? 4 : 3) : 0);
  const einheit = () => (krypto() ? A().state.symbol.replace(/USDT$|USDC$/, '') : 'Stk');
  const artName = { markt: 'Markt', limit: 'Limit', stopp: 'Stopp' };
  let daten = null, seite = 'kauf', zuletzt = 'menge', mengeFuer = null, pruefTimer = null, pruefSig = '', pruefAntwort = null;
  const KOPF = { 'Content-Type': 'application/json', 'X-Kitrader': '1' };
  const apiSeite = () => (seite === 'short' ? 'verkauf' : seite);          // an den Kern geht kauf oder verkauf, Short ist ein Verkauf mit Kennzeichen
  const richtungName = () => (seite === 'kauf' ? 'Kauf' : seite === 'short' ? 'Short' : 'Verkauf');
  let linien = [], vorschau = null, markerSig = '', linienSig = '', symbolVorher = null, skalaPreise = [];

  // ---------------- Daten ----------------
  async function laden() {
    try { daten = await (await fetch('/api/papier', { cache: 'no-store' })).json(); } catch (e) { return; }
    zeichnen();
  }
  const position = () => daten?.positionen.find((p) => p.symbol === A().state.symbol);
  const offene = () => (daten?.offen || []).filter((o) => o.symbol === A().state.symbol);

  // ---------------- Preis, Menge, Gesamt ----------------
  const art = () => $('#pdArt').value;
  const bezug = () => (art() === 'markt' ? A().state.lastPrice : +$('#pdPreis').value || A().state.lastPrice);
  function preisSchritt(p) { return p >= 10000 ? 5 : p >= 1000 ? 0.5 : p >= 100 ? 0.05 : p >= 10 ? 0.01 : 0.001; }
  function runden(m) { return krypto() ? Math.floor(m * 1e6) / 1e6 : Math.floor(m); }
  // Menge oder Gesamt nachführen, je nachdem, was zuletzt eingegeben wurde
  function abgleichen() {
    const p = bezug(); if (!p) return;
    if (zuletzt === 'gesamt') $('#pdMenge').value = runden((+$('#pdGesamt').value || 0) / p);
    else $('#pdGesamt').value = ((+$('#pdMenge').value || 0) * p).toFixed(2);
  }
  function plusMinus(feld, richtung) {
    const el = $('#pd' + feld[0].toUpperCase() + feld.slice(1)), v = +el.value || 0;
    const preisFeld = feld === 'preis' || feld === 'stop' || feld === 'ziel';
    const start = preisFeld && !v ? A().state.lastPrice || 0 : v;
    const schritt = preisFeld ? preisSchritt(start || A().state.lastPrice) : feld === 'menge' ? (krypto() ? (v >= 1 ? 0.1 : 0.001) : 1) : 100;
    const neu = Math.max(0, Math.round((start + richtung * schritt) / schritt) * schritt);
    el.value = feld === 'menge' ? runden(neu) : +neu.toFixed(feld === 'gesamt' ? 2 : 6);
    if (!preisFeld) zuletzt = feld;
    abgleichen(); vorschauSetzen(); zeichnen();
  }

  // ---------------- Karte ----------------
  function zeichnen() {
    const w = wert(), sym = A().state.symbol;
    $('#orderCard').classList.toggle('aus', !w);
    $('#pdWaehrung').textContent = w ? ZEICHEN[w.waehrung] : '';
    if (!w) {
      $('#pdInfo').innerHTML = `<span class="pd-warn">${esc(sym)} ist nicht auf der Watchlist. Papierhandel geht nur mit Werten der Watchlist (Reiter Kurse → Wert hinzufügen).</span>`;
      $('#pdOffen').innerHTML = ''; $('#pdOffenKopf').hidden = true; chartAktualisieren(); return;
    }
    if (mengeFuer !== sym) {                                     // neuer Wert: sinnvolle Vorgaben
      mengeFuer = sym; zuletzt = 'menge';
      $('#pdMenge').value = w.markt === 'KRYPTO' ? '0.01' : '10';
      if (art() !== 'markt') $('#pdPreis').value = +(A().state.lastPrice || 0).toFixed(A().state.lastPrice < 10 ? 3 : 2);
      abgleichen();
    }
    if (art() === 'markt' && document.activeElement?.id !== 'pdGesamt' && document.activeElement?.id !== 'pdMenge') abgleichen();   // Markt-Order: Betrag folgt dem Kurs
    const z = ZEICHEN[w.waehrung], k = daten.konto[w.waehrung], pos = position();
    const pos0 = position(), eroeffnet = seite === 'short' || (seite === 'kauf' && !(pos0 && pos0.richtung === 'short'));
    $('#pdStopZeile').classList.toggle('nein', !eroeffnet); $('#pdZielZeile').classList.toggle('nein', !eroeffnet);
    $('#pdStopEinheit').textContent = $('#pdZielEinheit').textContent = ZEICHEN[w.waehrung];
    $('#pdPreisZeile').hidden = art() === 'markt';
    $('#pdPreisLabel').textContent = art() === 'stopp' ? 'Stopp-Preis' : 'Limit-Preis';
    $('#pdPreisEinheit').textContent = $('#pdGesamtEinheit').textContent = z;
    $('#pdMengeEinheit').textContent = einheit();
    $('#pdLos').textContent = seite === 'short' ? 'Short-Order aufgeben' : `${seite === 'kauf' && pos0 && pos0.richtung === 'short' ? 'Eindecken (Kauf)' : richtungName()}-Order aufgeben`;
    $('#pdLos').className = 'primary pd-los ' + seite;
    $('#pdHinweis').textContent = w.markt === 'DE' ? 'Xetra · 15 Min. verzögert' : w.markt === 'US' ? 'US · IEX live' : 'Binance live';
    const erklaerung = art() === 'markt' ? 'sofort zum besten Kurs, durch das Orderbuch'
      : art() === 'limit' ? (seite === 'kauf' ? 'ausgeführt, sobald der Kurs auf das Limit fällt' : 'ausgeführt, sobald der Kurs auf das Limit steigt')
        : (seite === 'kauf' ? 'wird zur Markt-Order, sobald der Kurs den Stopp erreicht' : 'Schutz: verkauft, sobald der Kurs auf den Stopp fällt');
    const bestand = pos ? `${pos.richtung === 'short' ? 'Short −' : ''}${stueck(pos.menge)}` : '0';
    $('#pdInfo').innerHTML = `<span>${erklaerung}</span>
      <span>Guthaben <b>${zahl(k.guthaben)} ${z}</b> · Bestand <b>${bestand} ${esc(einheit())}</b></span>
      ${pos ? `<button class="pd-schliessen" id="pdSchliessen" title="stellt die ganze Position zum Markt glatt und storniert offene Orders dieses Werts">Position schließen (${pos.richtung === 'short' ? 'Short eindecken' : 'long verkaufen'}, ${pos.ergebnis == null ? '–' : (pos.ergebnis >= 0 ? '+' : '') + zahl(pos.ergebnis)} ${z})</button>` : ''}`;
    if ($('#pdSchliessen')) $('#pdSchliessen').onclick = schliessen;
    const liste = offene();
    $('#pdOffenKopf').hidden = !liste.length;
    $('#pdOffenKopf').textContent = `Offene Orders (${liste.length})`;
    $('#pdOffen').innerHTML = liste.map((o) => { const p = o.limit_preis ?? o.stopp_preis;
      return `<li><span class="pd-tag ${o.seite}">${o.seite === 'kauf' ? 'Kauf' : 'Verkauf'}</span>
        <span class="p"><b>${artName[o.art]}${p ? ' ' + preis(p) + ' ' + z : ''}</b> · ${stueck(o.menge)} ${esc(einheit())}${p ? ` · ${zahl(o.menge * p)} ${z}` : ''}
          <small>${esc(o.hinweis || (o.art === 'limit' ? 'wartet auf Limit' : o.art === 'stopp' ? 'wartet auf Stopp' : 'wartet auf Kurs'))}</small></span>
        <button class="pd-storno" data-storno="${o.nr}">Stornieren</button></li>`; }).join('');
    $('#pdOffen').querySelectorAll('[data-storno]').forEach((b) => b.onclick = () => stornieren(+b.dataset.storno));
    pruefungPlanen();
    chartAktualisieren();
  }
  // ---------------- Risikoprüfung (kitrader.risiko) ----------------
  function auftrag() {
    const w = wert(), p = +$('#pdPreis').value;
    const b = { symbol: A().state.symbol, seite: apiSeite(), art: art(), menge: +$('#pdMenge').value, short: seite === 'short',
      stop: $('#pdStop').value || null, ziel: $('#pdZiel').value || null };
    if (art() === 'limit') b.limit = p; if (art() === 'stopp') b.stopp = p;
    return w ? b : null;
  }
  function pruefungPlanen() {
    const b = auftrag(), box = $('#pdPruefung');
    if (!b || !(b.menge > 0)) { box.innerHTML = ''; pruefSig = ''; return; }
    const sig = JSON.stringify(b);
    if (sig === pruefSig) return;
    pruefSig = sig; clearTimeout(pruefTimer);
    pruefTimer = setTimeout(async () => {
      let r;
      try { r = await (await fetch('/api/papier/pruefen', { method: 'POST', headers: KOPF, body: sig })).json(); } catch (e) { return; }
      if (pruefSig !== sig) return;                              // inzwischen anders eingegeben
      pruefAntwort = r; pruefungZeigen(r);
    }, 350);
  }
  function pruefungZeigen(r, nachAblehnung = false) {
    const box = $('#pdPruefung'), z = ZEICHEN[wert()?.waehrung] || '';
    if (r.fehler && !r.gruende) { box.innerHTML = `<div class="nein-liste"><ul>${`<li>${esc(r.fehler)}</li>`}</ul></div>`; return; }
    if (r.schliesst) { box.innerHTML = `<div class="hinweis">Schließt oder verkleinert die Position: keine Eröffnung, keine Risikoprüfung nötig.</div>`; return; }
    const zahlen = [
      r.risiko != null ? `Risiko bis zum Stop <b>${zahl(r.risiko)} ${z}</b> (${zahl(r.risiko_prozent)} % vom Depot, erlaubt ${zahl(r.risiko_max)} ${z} = ${zahl(r.risiko_pro_trade_prozent)} %)` : `Risiko je Trade höchstens <b>${zahl(r.risiko_max)} ${z}</b> (${zahl(r.risiko_pro_trade_prozent)} % vom Depot) – dafür den Stop setzen`,
      r.wert != null ? `Positionswert <b>${zahl(r.wert)} ${z}</b> (${zahl(r.position_prozent, 1)} % vom Depot, erlaubt ${zahl(r.position_max_prozent, 0)} %)` : '',
      r.crv != null ? `Chance-Risiko-Verhältnis <b>${zahl(r.crv, 1)}</b>` : ''].filter(Boolean);
    box.innerHTML = `<div class="zahlen">${zahlen.map((t) => `<span>${t}</span>`).join('')}</div>` +
      (r.ok ? `<div class="ok">✓ Risikoprüfung bestanden</div>` : `<ul class="nein-liste">${(r.gruende || []).map((g) => `<li>${esc(g)}</li>`).join('')}</ul>`);
  }
  async function schliessen() {
    const pos = position(); if (!pos) return;
    let r;
    try { r = await (await fetch('/api/papier/schliessen', { method: 'POST', headers: KOPF, body: JSON.stringify({ symbol: A().state.symbol }) })).json(); }
    catch (e) { r = { fehler: 'Dashboard nicht erreichbar' }; }
    if (r.fehler) { A().toast('Position nicht geschlossen', r.fehler); return; }
    const o = r.order, z = ZEICHEN[wert()?.waehrung] || '';
    A().toast(o.status === 'ausgefuehrt' ? `Geschlossen: ${o.symbol}` : 'Schließen vorgemerkt', o.status === 'ausgefuehrt' ? `${stueck(o.menge)} ${einheit()} zu ${preis(o.preis)} ${z} · ${o.hinweis}` : (o.hinweis || 'wartet auf Kurs oder Handelszeit'), 'signal');
    laden();
  }
  function seiteSetzen(s) {
    seite = s; $('#orderCard').querySelectorAll('[data-seite]').forEach((x) => x.classList.toggle('on', x.dataset.seite === s));
  }

  async function stornieren(nr) {
    await fetch('/api/papier/order?nr=' + nr, { method: 'DELETE' });
    A().toast('Order storniert', `${A().state.symbol} · Nr. ${nr}`); laden();
  }

  async function aufgeben() {
    const w = wert(); if (!w) return;
    const p = +$('#pdPreis').value, m = +$('#pdMenge').value;
    if (!(m > 0)) { A().toast('Order nicht aufgegeben', 'Menge oder Gesamtbetrag eingeben'); return; }
    const body = { ...auftrag(), menge: m, notiz: $('#pdNotiz').value, pruefen: true };
    $('#pdLos').disabled = true;
    let r;
    try { r = await (await fetch('/api/papier/order', { method: 'POST', headers: KOPF, body: JSON.stringify(body) })).json(); }
    catch (e) { r = { fehler: 'Dashboard nicht erreichbar' }; }
    $('#pdLos').disabled = false;
    if (r.fehler) {
      if (r.pruefung) pruefungZeigen(r.pruefung, true);
      A().toast(r.gruende ? 'Von der Risikoprüfung abgelehnt' : 'Order nicht aufgegeben', r.gruende ? r.gruende.join(' · ') : r.fehler);
      return;
    }
    const o = r.order, z = ZEICHEN[w.waehrung];
    if (o.status === 'ausgefuehrt') A().toast(`${o.seite === 'kauf' ? 'Gekauft' : o.short ? 'Short eröffnet' : 'Verkauft'}: ${o.symbol}`, `${stueck(o.menge)} ${einheit()} zu ${preis(o.preis)} ${z} = ${zahl(o.menge * o.preis)} ${z} · ${o.hinweis}`, 'signal');
    else if (o.status === 'abgelehnt') A().toast('Order abgelehnt', o.hinweis);
    else A().toast('Order offen', `${o.seite === 'kauf' ? 'Kauf' : 'Verkauf'}-${artName[o.art]} ${preis(o.limit_preis ?? o.stopp_preis)} ${z} · ${stueck(o.menge)} ${einheit()}. Löschen: ✕ am Schild im Chart oder „Stornieren“ in der Karte.`, 'signal');
    $('#pdNotiz').value = ''; $('#pdStop').value = ''; $('#pdZiel').value = ''; pruefSig = ''; vorschauWeg();
    laden();
  }

  // ---------------- Chart: Ausführungen, offene Orders, Einstand, Vorschau ----------------
  function barZeit(t) {
    const b = A().state.bars; if (!b.length || t == null || t < b[0].time) return null;
    let lo = 0, hi = b.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (b[mid].time <= t) lo = mid; else hi = mid - 1; }
    return A().state.times[lo];                                  // Anzeigezeit der Kerze (kitrader Charts zeigt Ortszeit)
  }
  function chartAktualisieren() {
    const S = A(); if (!S || !daten) return;
    const sym = S.state.symbol, b = S.state.bars;
    const ausgefuehrt = (daten.verlauf || []).filter((o) => o.symbol === sym && o.status === 'ausgefuehrt');
    const marker = ausgefuehrt.map((o) => ({ time: barZeit(sek(o.datenzeit || o.ausgefuehrt)), kauf: o.seite === 'kauf', o })).filter((x) => x.time != null)
      .map(({ time, kauf, o }) => ({ time, position: kauf ? 'belowBar' : 'aboveBar', shape: kauf ? 'arrowUp' : 'arrowDown', color: kauf ? '#2fcf7a' : '#f05454',
        text: `${kauf ? 'K' : o.short ? 'S' : 'V'} ${stueck(o.menge, o.markt === 'KRYPTO')}` }))
      .sort((x, y) => x.time - y.time);
    const sig = sym + (b[0]?.time || '') + b.length + JSON.stringify(marker);
    if (sig !== markerSig) { markerSig = sig; S.setMarkerQuelle('papier', marker); }
    const pos = position();
    const soll = [...offene().map((o) => ({ price: o.limit_preis ?? o.stopp_preis, color: o.seite === 'kauf' ? '#2fcf7a' : '#f05454', style: 2, title: '' })).filter((l) => l.price),
      ...(pos ? [{ price: pos.einstand, color: '#4fc79a', style: 0, title: `Ø Einstand ${pos.richtung === 'short' ? 'Short ' : ''}${stueck(pos.menge)}` }] : []),
      ...(daten.schutz || []).filter((x) => x.symbol === sym && pos).flatMap((x) => [
        ...(x.stop ? [{ price: x.stop, color: '#f05454', style: 2, title: 'Stop' }] : []), ...(x.ziel ? [{ price: x.ziel, color: '#2fcf7a', style: 2, title: 'Ziel' }] : [])])];
    const lsig = sym + JSON.stringify(soll);
    if (lsig !== linienSig) {
      linienSig = lsig;
      skalaPreise = soll.filter((l) => ['Stop', 'Ziel'].includes(l.title) || String(l.title).startsWith('Ø Einstand')).map((l) => l.price).filter(isFinite);
      skalaNeu();
      linien.forEach((l) => { try { S.candle.removePriceLine(l); } catch (e) { /* schon weg */ } });
      linien = soll.map((l) => S.candle.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: l.style, axisLabelVisible: true, title: l.title }));
    }
    schilderZeichnen();
  }
  // Die Preisskala schließt Stop, Ziel und Einstand der offenen Position ein (sonst liegen die Linien oft außerhalb des Bildes).
  // Gilt nur bei automatischer Skala; wer die Preisachse von Hand zieht, behält seinen Ausschnitt.
  function skalaProvider(original) {
    const r = original();
    if (!r || !skalaPreise.length) return r;
    return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, ...skalaPreise), maxValue: Math.max(r.priceRange.maxValue, ...skalaPreise) } };
  }
  function skalaNeu() {
    const S = A(); if (!S) return;
    S.candle.applyOptions({ autoscaleInfoProvider: skalaProvider });
    const opt = S.main.priceScale('right').options();
    if (opt.autoScale) { S.main.priceScale('right').applyOptions({ autoScale: false }); S.main.priceScale('right').applyOptions({ autoScale: true }); }   // Skala neu berechnen
  }
  // Schild an jeder offenen Order: was sie ist und ein ✕ zum Löschen
  function schilderZeichnen() {
    const box = $('#pdSchilder'); if (!box || !daten) return;
    const w = wert(), z = w ? ZEICHEN[w.waehrung] : '';
    const html = offene().filter((o) => o.limit_preis ?? o.stopp_preis).map((o) => { const p = o.limit_preis ?? o.stopp_preis;
      return `<div class="pd-schild ${o.seite}" data-preis="${p}"><b>${o.seite === 'kauf' ? 'Kauf' : 'Verkauf'}-${artName[o.art]}</b>
        ${stueck(o.menge)} ${esc(einheit())} · ${zahl(o.menge * p)} ${z}<button data-storno="${o.nr}" title="Order löschen (stornieren)">✕</button></div>`; }).join('');
    if (box.dataset.html !== html) {                             // nur bei Änderungen neu, damit Klicks nicht ins Leere gehen
      box.dataset.html = html; box.innerHTML = html;
      box.querySelectorAll('[data-storno]').forEach((b) => b.onclick = (e) => { e.stopPropagation(); stornieren(+b.dataset.storno); });
    }
    schilderSetzen();
  }
  function schilderSetzen() {
    const S = A(); if (!S) return;
    const rechts = (S.main.priceScale('right').width() || 60) + 10, hoehe = S.main.chartElement().clientHeight;
    document.querySelectorAll('#pdSchilder .pd-schild').forEach((el) => {
      const y = S.candle.priceToCoordinate(+el.dataset.preis);
      el.style.display = y == null || y < 4 || y > hoehe - 4 ? 'none' : '';
      el.style.top = `${y - 12}px`; el.style.right = `${rechts}px`;
    });
  }
  function vorschauWeg() { if (vorschau) { try { A().candle.removePriceLine(vorschau); } catch (e) { /* schon weg */ } vorschau = null; } }
  // Vorschau-Linie, solange eine Limit- oder Stopp-Order eingestellt wird: zeigt Art, Menge und Betrag
  function vorschauSetzen() {
    vorschauWeg();
    const w = wert(), p = +$('#pdPreis').value; if (!w || art() === 'markt' || !p) return;
    const m = +$('#pdMenge').value || 0;
    vorschau = A().candle.createPriceLine({ price: p, color: '#f5b942', lineWidth: 1, lineStyle: 1, axisLabelVisible: true,
      title: `Vorschau ${richtungName()}-${artName[art()]} · ${stueck(m)} ${einheit()} · ${zahl(m * p)} ${ZEICHEN[w.waehrung]}` });
  }

  // Order-Werkzeug: Klick in den Chart setzt Preis und Orderart
  function chartKlick(y) {
    const S = A(), p = S.candle.coordinateToPrice(y), last = S.state.lastPrice;
    if (!isFinite(p) || !last) return;
    const unter = p < last;
    $('#pdArt').value = seite === 'kauf' ? (unter ? 'limit' : 'stopp') : (unter ? 'stopp' : 'limit');   // Short wie Verkauf
    $('#pdPreis').value = +p.toFixed(p < 10 ? 3 : 2);
    abgleichen(); vorschauSetzen();
    document.querySelector('#toolbar [data-tool="cross"]')?.click();
    zeichnen();
    $('#pdMenge').focus(); $('#pdMenge').select();
    S.toast(`${richtungName()}-${artName[art()]} vorbereitet`, `Preis ${preis(+$('#pdPreis').value)} gesetzt. Menge oder Betrag rechts prüfen, dann „Order aufgeben“.`);
  }

  // ---------------- Start ----------------
  function start() {
    const S = A();
    if (!S || !S.state.bars.length) return setTimeout(start, 300);
    const schilder = document.createElement('div'); schilder.id = 'pdSchilder'; $('#stage').appendChild(schilder);
    $('#orderCard').querySelectorAll('[data-seite]').forEach((b) => b.onclick = () => { seiteSetzen(b.dataset.seite); pruefSig = ''; vorschauSetzen(); zeichnen(); });
    $('#pdArt').addEventListener('change', () => {
      if (art() !== 'markt' && !+$('#pdPreis').value) $('#pdPreis').value = +(S.state.lastPrice || 0).toFixed(S.state.lastPrice < 10 ? 3 : 2);
      abgleichen(); vorschauSetzen(); zeichnen();
    });
    $('#pdPreis').addEventListener('input', () => { abgleichen(); vorschauSetzen(); zeichnen(); });
    $('#pdStop').addEventListener('input', () => zeichnen());
    $('#pdZiel').addEventListener('input', () => zeichnen());
    $('#pdMenge').addEventListener('input', () => { zuletzt = 'menge'; abgleichen(); vorschauSetzen(); zeichnen(); });
    $('#pdGesamt').addEventListener('input', () => { zuletzt = 'gesamt'; abgleichen(); vorschauSetzen(); zeichnen(); });
    $('#pdGesamt').addEventListener('blur', () => { zuletzt = 'menge'; abgleichen(); zeichnen(); });   // Betrag auf ganze Stück runden
    $('#orderCard').querySelectorAll('[data-pm]').forEach((b) => b.onclick = () => { const [f, r] = b.dataset.pm.split(':'); plusMinus(f, +r); });
    $('#pdLos').onclick = aufgeben;
    const knopf = document.querySelector('#toolbar [data-tool="order"]');
    knopf.addEventListener('click', () => {                      // nach setTool() der App: eigener Hinweis
      const h = $('#toolHint'); h.textContent = 'Klick in den Chart setzt den Order-Preis (Kauf: darunter Limit, darüber Stopp)'; h.style.display = 'block';
    });
    addEventListener('keydown', (e) => { if (e.altKey && e.code === 'KeyO') { e.preventDefault(); knopf.click(); } });
    const el = S.main.chartElement();
    let unten = null;
    el.addEventListener('pointerdown', (e) => { if (e.button === 0) unten = { x: e.clientX, y: e.clientY }; }, true);
    el.addEventListener('pointerup', (e) => {
      if (!unten || S.state.tool !== 'order') return;
      const weit = Math.hypot(e.clientX - unten.x, e.clientY - unten.y); unten = null;
      if (weit > 4) return;
      chartKlick(e.clientY - el.getBoundingClientRect().top);
    }, true);
    laden();
    setInterval(() => { if (!document.hidden) laden(); }, 4000);
    setInterval(() => { if (!document.hidden) schilderSetzen(); }, 120);   // Schilder folgen Zoom und Verschieben
    setInterval(() => {                                          // Symbolwechsel: Karte und Chart sofort anpassen
      const s = S.state.symbol + '|' + S.state.tf + '|' + (S.state.bars[0]?.time || '');
      if (s !== symbolVorher) { symbolVorher = s; vorschauWeg(); markerSig = ''; linienSig = ''; if (daten) zeichnen(); }
    }, 500);
  }
  start();
})();
