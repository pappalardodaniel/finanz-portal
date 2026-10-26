"""Manuelle Orders aus der Bedienoberfläche (Reiter „Live“, Order-Fenster). Nur im Papierbetrieb.

Eine von Hand gesetzte Order geht durch dieselbe Risikoprüfung (`risiko.pruefen`) wie jede Order des Autopiloten: Kurs frisch, Handelszeiten,
Not-Aus, Tagesverlustgrenze, Stop-Loss-Pflicht, Risiko je Trade, Positionsgröße, Anzahl Positionen und Orders, Short erlaubt. Ausgeführt wird sie
über den Papier-Broker (simulierte Ausführung mit echten Kursen, kein Geld). Jede Order, auch die abgelehnte, kommt als Entscheidung mit der
Strategie „manuell“ ins Journal. Offene Positionen lassen sich mit `schliessen` glattstellen (ebenfalls durch die Risikoprüfung).

Funktionen: `vorschau` (rechnet nur und prüft), `senden` (prüft und führt aus), `schliessen`. Der Aufrufer öffnet Broker und Journal; die
Uhr ist austauschbar (Tests). In `demo` und `echt` verweigert jede Funktion die Arbeit (`ManuellFehler`, Status 403).
"""
from __future__ import annotations

import math
from datetime import datetime

from . import symbole as _symbole
from .broker.basis import Broker, BrokerFehler, Order, symbol_schluessel
from .hilfen import jetzt_utc, zu_utc
from .journal import Journal
from .risiko import Lage, einstiegspreis, mengenschritt, pruefen, wechselkurs_gueltig

STRATEGIE = "manuell"
RICHTUNGEN = {"kaufen": "kauf", "kauf": "kauf", "long": "kauf", "short": "verkauf", "verkaufen": "verkauf"}
_EPS = 1e-9


class ManuellFehler(ValueError):
    """Eingabe unbrauchbar (Status 400) oder Betriebsart nicht erlaubt (403). Der Text ist für Menschen lesbar."""

    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.status = status


def erlaubt(einst: dict) -> tuple[bool, str]:
    """(erlaubt, Hinweis). Das Order-Fenster gibt es nur im Papierbetrieb."""
    modus = einst.get("modus", "papier")
    if modus == "papier":
        return True, ""
    return False, (f"Das Order-Fenster ist in der Betriebsart {modus.upper()} gesperrt: Von Hand wird nur auf dem Papier gehandelt. "
                   "Echte Orders setzt allein der Autopilot, mit Freigabe und Risikoprüfung.")


# ---------------------------------------------------------------- Eingaben

def _zahl(eingabe: dict, name: str, text: str, pflicht: bool = False) -> float | None:
    roh = eingabe.get(name)
    if roh is None or (isinstance(roh, str) and not roh.strip()):
        if pflicht:
            raise ManuellFehler(f"{text} fehlt.")
        return None
    if isinstance(roh, bool):
        raise ManuellFehler(f"{text} muss eine Zahl größer als 0 sein.")
    try:
        if isinstance(roh, (int, float)):
            wert = float(roh)
        else:
            t = str(roh).strip().replace(" ", "")
            wert = float(t.replace(".", "").replace(",", ".") if "," in t else t)      # 1.234,5 und 1234.5 beide lesbar
    except ValueError:
        raise ManuellFehler(f"{text}: „{roh}“ ist keine Zahl.") from None
    if not math.isfinite(wert) or wert <= 0:
        raise ManuellFehler(f"{text} muss eine Zahl größer als 0 sein.")
    return wert


def _einstellungen_fuer(einst: dict, symbol: str) -> dict:
    """Kopie der Einstellungen, die für ein Kryptopaar bei Kraken die Vorgaben von `start` ergänzt (rund um die Uhr, feiner Mengenschritt),
    falls das Symbol nicht ausdrücklich eingestellt ist. Sonst würde ein beliebiges Paar wie eine US-Aktie behandelt."""
    if not _symbole.nutzt_kraken(einst):
        return einst
    e = {**einst, "maerkte": dict(einst.get("maerkte", {})), "mengenschritte": dict(einst.get("mengenschritte", {}))}
    schluessel = symbol_schluessel(symbol)
    if not any(symbol_schluessel(k) == schluessel for k in e["maerkte"]):
        e["maerkte"][symbol] = "krypto"
    if not any(symbol_schluessel(k) == schluessel for k in e["mengenschritte"]):
        from .start import _mengenschritt           # dieselbe Vorgabe wie für die Symbole des Autopiloten
        e["mengenschritte"][symbol] = _mengenschritt(symbol)
    return e


def _wechselkurs(broker: Broker, einst: dict, kurs_waehrung: str, konto_waehrung: str) -> float | None:
    von, nach = (kurs_waehrung or "").strip().upper(), (konto_waehrung or einst["waehrung"]).strip().upper()
    if not von or von == nach:
        return 1.0
    wk = None
    try:
        wk = broker.wechselkurs(von, nach)
    except BrokerFehler:
        pass
    if not wechselkurs_gueltig(wk):
        wk = {str(k).upper(): v for k, v in einst["wechselkurse"].items()}.get(von)
    return float(wk) if wechselkurs_gueltig(wk) else None


def lage_bauen(broker: Broker, journal: Journal, einst: dict, ordner, symbol: str, jetzt: datetime, modus: str = "papier") -> Lage:
    """Die Lage für die Risikoprüfung, wie der Autopilot sie baut: Konto, Positionen, Kurs, offene Orders, Tagesergebnis, Sperren."""
    konto = broker.konto()
    positionen: dict[str, float] = {}
    for p in broker.positionen():
        positionen[p.symbol] = positionen.get(p.symbol, 0.0) + p.menge
    kurs = broker.kurs(symbol)
    offene, unbekannt = [], False
    try:
        offene = broker.orders(nur_offene=True)
    except BrokerFehler:
        unbekannt = True
    datum = jetzt.date().isoformat()
    stand = journal.tagesstand(datum)
    tagesergebnis = konto.gesamtwert - stand["startwert"] if stand.get("startwert") is not None else (konto.tagesergebnis or 0.0)
    return Lage(jetzt=jetzt, gesamtwert=konto.gesamtwert, einstellungen=einst, symbol=symbol, guthaben=konto.guthaben, positionen=positionen,
                tagesergebnis=tagesergebnis, orders_heute=journal.orders_heute(datum, modus), kurs=kurs,
                tag_gesperrt=bool(stand.get("gesperrt")), datenordner=ordner, offene_orders=offene, orders_unbekannt=unbekannt,
                konto_waehrung=konto.waehrung, wechselkurs=_wechselkurs(broker, einst, kurs.waehrung, konto.waehrung))


def _runden(menge: float, schritt: float) -> float:
    return round(math.floor(menge / schritt + _EPS) * schritt, 10)


def order_bauen(eingabe: dict, lage: Lage, einst: dict, symbol: str) -> tuple[Order, dict]:
    """Aus der Eingabe (richtung, betrag/menge, stop, ziel) die Order und ihre Kennzahlen. Wirft ManuellFehler bei unbrauchbaren Angaben."""
    seite = RICHTUNGEN.get(str(eingabe.get("richtung", "")).strip().lower())
    if seite is None:
        raise ManuellFehler("Richtung: „kaufen“ oder „short“.")
    stop = _zahl(eingabe, "stop", "Der Stop", pflicht=True)
    ziel = _zahl(eingabe, "ziel", "Das Ziel")
    preis = einstiegspreis(Order(symbol, seite, 1.0), lage.kurs)
    if preis is None or not preis > 0 or not math.isfinite(preis):
        raise ManuellFehler(f"Für {symbol} liegt kein brauchbarer Kurs vor.")
    schritt = float(mengenschritt(symbol, einst))
    wk = lage.wechselkurs if wechselkurs_gueltig(lage.wechselkurs) else 1.0
    art = str(eingabe.get("betrag_art", "eur")).strip().lower()
    if art in ("eur", "betrag", "geld"):
        betrag = _zahl(eingabe, "betrag", "Der Betrag", pflicht=True)
        roh = betrag / (preis * wk)
    elif art in ("stueck", "stück", "menge"):
        roh = _zahl(eingabe, "betrag", "Die Stückzahl", pflicht=True)
    else:
        raise ManuellFehler("Betrag in „eur“ oder „stueck“ angeben.")
    menge = _runden(roh, schritt)
    if menge <= 0:
        raise ManuellFehler(f"Die Menge liegt unter dem kleinsten Schritt ({schritt:g} Stück). Bitte einen größeren Betrag wählen.")
    order = Order(symbol, seite, menge, "markt", stop_loss=stop, take_profit=ziel, notiz="manuell")
    risiko = abs(preis - stop) * menge * wk
    wert = menge * preis * wk
    gesamt = lage.gesamtwert
    kosten = einst["kosten"]
    k = {"symbol": symbol, "richtung": "kaufen" if seite == "kauf" else "short", "seite": seite, "menge": menge, "mengenschritt": schritt,
         "preis": preis, "stop": stop, "ziel": ziel, "waehrung": lage.konto_waehrung or einst["waehrung"],
         "kurs_waehrung": lage.kurs.waehrung if lage.kurs and lage.kurs.waehrung else (lage.konto_waehrung or einst["waehrung"]),
         "wert": wert, "risiko": risiko, "risiko_prozent": risiko / gesamt * 100 if gesamt > 0 else None,
         "risiko_max": gesamt * einst["risiko"]["risiko_pro_trade_prozent"] / 100,
         "position_prozent": wert / gesamt * 100 if gesamt > 0 else None, "position_max_prozent": einst["risiko"]["position_max_prozent"],
         "gesamtwert": gesamt, "gebuehr_ca": kosten["gebuehr_fest"] + wert * kosten["gebuehr_prozent"] / 100,
         "chance": abs(ziel - preis) * menge * wk if ziel else None,
         "crv": abs(ziel - preis) / abs(preis - stop) if ziel and abs(preis - stop) > 0 else None,
         "kurs_alter_s": max(0.0, (zu_utc(lage.jetzt) - zu_utc(lage.kurs.zeit)).total_seconds()) if lage.kurs else None}
    return order, k


def _vorbereiten(broker: Broker, journal: Journal, einst: dict, ordner, eingabe: dict, jetzt: datetime):
    ok, hinweis = erlaubt(einst)
    if not ok:
        raise ManuellFehler(hinweis, 403)
    try:
        symbol = _symbole.kanonisch(str(eingabe.get("symbol", "")), einst)
    except _symbole.SymbolFehler as e:
        raise ManuellFehler(str(e)) from None
    e = _einstellungen_fuer(einst, symbol)
    try:
        lage = lage_bauen(broker, journal, e, ordner, symbol, jetzt)
    except BrokerFehler as fehler:
        raise ManuellFehler(f"Kein Kurs für {symbol} abrufbar ({fehler}).", 502) from None
    order, kennzahlen = order_bauen(eingabe, lage, e, symbol)
    return order, lage, kennzahlen


# ---------------------------------------------------------------- Vorschau, Senden, Schließen

def vorschau(broker: Broker, journal: Journal, einst: dict, ordner, eingabe: dict, uhr=jetzt_utc) -> dict:
    """Rechnet Menge, Wert und Risiko und fragt die Risikoprüfung, ohne etwas zu senden. {"ok", "gruende", ...Kennzahlen}."""
    jetzt = zu_utc(uhr())
    order, lage, k = _vorbereiten(broker, journal, einst, ordner, eingabe, jetzt)
    pruefung = pruefen(order, lage)
    return {**k, "ok": pruefung.ok, "gruende": list(pruefung.gruende)}


def senden(broker: Broker, journal: Journal, einst: dict, ordner, eingabe: dict, uhr=jetzt_utc) -> dict:
    """Prüft (dieselbe Risikoprüfung wie beim Autopiloten) und führt über den Papier-Broker aus. Abgelehnte Orders werden nicht
    gesendet, aber im Journal vermerkt. {"ausgefuehrt", "ok", "gruende", "status", "preis", "order_id", "journal_id", ...}."""
    jetzt = zu_utc(uhr())
    if hasattr(broker, "aktualisieren"):
        broker.aktualisieren()                       # Stops und Ziele bisheriger Positionen zuerst abarbeiten
    order, lage, k = _vorbereiten(broker, journal, einst, ordner, eingabe, jetzt)
    pruefung = pruefen(order, lage)
    eid = journal.entscheidung("papier", order.symbol, STRATEGIE, None, pruefung, order,
                               "manuell" + ("" if pruefung.ok else ": abgelehnt"))
    if not pruefung.ok:
        return {**k, "ausgefuehrt": False, "ok": False, "gruende": list(pruefung.gruende), "status": "abgelehnt", "journal_id": eid}
    stand = broker.senden(order)
    journal.ausfuehrung(eid, stand)
    if stand.status in ("offen", "teilweise", "ausgefuehrt"):
        journal.eigene_merken(order.symbol, "papier", "long" if order.seite == "kauf" else "short", None, stand.id)
    abgelehnt = stand.status == "abgelehnt"
    return {**k, "ausgefuehrt": stand.status == "ausgefuehrt", "ok": not abgelehnt,
            "gruende": [f"Der Papier-Broker hat die Order abgelehnt: {stand.meldung}"] if abgelehnt else [], "status": stand.status,
            "ausgefuehrter_preis": stand.preis, "order_id": stand.id, "meldung": stand.meldung, "journal_id": eid}


def schliessen(broker: Broker, journal: Journal, einst: dict, ordner, symbol: str, uhr=jetzt_utc) -> dict:
    """Stellt die offene Position zum Marktpreis glatt (offene Schutzorders des Symbols werden vorher storniert)."""
    ok, hinweis = erlaubt(einst)
    if not ok:
        raise ManuellFehler(hinweis, 403)
    jetzt = zu_utc(uhr())
    schluessel = symbol_schluessel(symbol or "")
    offen = [p for p in broker.positionen() if symbol_schluessel(p.symbol) == schluessel and abs(p.menge) > _EPS]
    if not offen:
        raise ManuellFehler(f"Zu {symbol!r} gibt es keine offene Position.", 404)
    name = offen[0].symbol
    menge = sum(p.menge for p in offen)
    order = Order(name, "verkauf" if menge > 0 else "kauf", abs(menge), notiz="manuell: Schließen")
    try:
        lage = lage_bauen(broker, journal, _einstellungen_fuer(einst, name), ordner, name, jetzt)
    except BrokerFehler as fehler:
        raise ManuellFehler(f"Kein Kurs für {name} abrufbar ({fehler}).", 502) from None
    pruefung = pruefen(order, lage)
    eid = journal.entscheidung("papier", name, STRATEGIE, None, pruefung, order, "manuell: Schließen" + ("" if pruefung.ok else " abgelehnt"))
    if not pruefung.ok:
        return {"symbol": name, "ausgefuehrt": False, "ok": False, "gruende": list(pruefung.gruende), "status": "abgelehnt", "journal_id": eid}
    vorher = max((t["id"] for t in broker.trades()), default=0) if hasattr(broker, "trades") else 0
    for o in broker.orders(nur_offene=True):
        if symbol_schluessel(o.order.symbol) == schluessel:
            broker.stornieren(o.id)
    stand = broker.senden(order)
    journal.ausfuehrung(eid, stand)
    journal.ziel_loeschen(name, "papier")
    ergebnis = None
    if hasattr(broker, "trades"):                    # Ergebnis der Schließung wie beim Autopiloten ins Journal nachtragen
        for t in broker.trades():
            if t["id"] > vorher and t["order_id"] == stand.id:
                journal.ergebnis_zu_order(stand.id, t["netto"], f"Trade {t['symbol']} netto")
                ergebnis = (ergebnis or 0.0) + t["netto"]
    abgelehnt = stand.status == "abgelehnt"
    return {"symbol": name, "ausgefuehrt": stand.status == "ausgefuehrt", "ok": not abgelehnt,
            "gruende": [f"Der Papier-Broker hat die Order abgelehnt: {stand.meldung}"] if abgelehnt else [], "status": stand.status, "ausgefuehrter_preis": stand.preis, "menge": abs(menge), "order_id": stand.id,
            "ergebnis": ergebnis, "meldung": stand.meldung, "journal_id": eid}
