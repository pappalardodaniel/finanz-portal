"""Daten für den Reiter „Live“ und die Morgenlage der Bedienoberfläche. Nur Lesen, nie eine Order.

* Kerzen kommen aus der Kursquelle (der Server holt sie), hier werden sie nur für das JSON umgeformt.
* Eigene Trades: im Papierbetrieb aus dem Papierkonto (`papier.db`, Tabellen trades/lots/orders; nur dort stehen auch
  Ausstiege durch Stop oder Ziel), sonst aus dem Journal (ausgeführte Orders, vorwärts in FIFO-Reihenfolge gepaart).
* KI: die letzte Einschätzung mit Marke [KI] zum Symbol (Richtung und Wahrscheinlichkeit je Horizont).
* Morgenlage: die Datei `morgenlage.json` im Datenordner (schreibt `lage.py`), unbesehen weitergereicht.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..broker.basis import symbol_schluessel
from ..hilfen import jetzt_utc, zu_utc
from ..journal import Journal

MORGENLAGE_DATEI = "morgenlage.json"
MORGENLAGE_MAX_BYTES = 400_000
_EPS = 1e-9
_WAHRSCHEINLICHKEIT = re.compile(r"Wahrscheinlichkeit (\d(?:\.\d+)?)")
KI_MARKE = "[KI]"
KI_HORIZONTE = ("1d", "3M", "1J")                  # kurz, mittel, lang (siehe ki.py)


def epoche(zeit: str | datetime | None) -> int | None:
    """ISO-Zeit (auch '2026-10-26 12:00:00+00:00') oder datetime -> Unix-Sekunden; Zeiten ohne Zeitzone gelten als UTC."""
    if zeit is None or zeit == "":
        return None
    try:
        z = zeit if isinstance(zeit, datetime) else datetime.fromisoformat(str(zeit))
    except ValueError:
        return None
    return int(zu_utc(z).timestamp())


def kerzen_zeilen(kerzen) -> list[list[float]]:
    """Kerzen -> [[Unix-Sekunden, Eröffnung, Hoch, Tief, Schluss, Umsatz], ...], älteste zuerst."""
    return [[int(zu_utc(k.t).timestamp()), k.o, k.h, k.l, k.c, k.v] for k in sorted(kerzen, key=lambda k: k.t)]


def kurs_dict(kurs) -> dict | None:
    if kurs is None:
        return None
    return {"bid": kurs.bid, "ask": kurs.ask, "letzter": kurs.letzter, "mitte": kurs.mitte,
            "zeit": epoche(kurs.zeit), "zeit_geschaetzt": bool(getattr(kurs, "zeit_geschaetzt", False))}


# ---------------------------------------------------------------- Trades aus dem Papierkonto

def _lesen(pfad: Path):
    """Papierkonto nur zum Lesen öffnen (der Autopilot schreibt gleichzeitig; die Oberfläche ändert nie etwas)."""
    db = sqlite3.connect(Path(pfad).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def papier_trades(pfad: Path, symbol: str, maximal: int = 60) -> list[dict]:
    """Abgeschlossene Trades und offene Positionen des Papierkontos zu einem Symbol, älteste zuerst.
    Stop und Ziel: bei offenen Positionen aus den offenen Schutzorders, bei abgeschlossenen aus der Eröffnungsorder, wenn
    der Trade durch Stop oder Ziel endete (sonst unbekannt)."""
    pfad = Path(pfad)
    if not pfad.is_file():
        return []
    schluessel = symbol_schluessel(symbol)
    ergebnis: list[dict] = []
    try:
        db = _lesen(pfad)
    except sqlite3.Error:
        return []
    try:
        for z in db.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 400").fetchall()[::-1]:
            if symbol_schluessel(z["symbol"]) != schluessel:
                continue
            stop = ziel = None
            schliesser = db.execute("SELECT eltern FROM orders WHERE id=?", (z["order_id"],)).fetchone()
            if schliesser and schliesser["eltern"]:
                eroeffnung = db.execute("SELECT stop_loss, take_profit FROM orders WHERE id=?", (schliesser["eltern"],)).fetchone()
                if eroeffnung:
                    stop, ziel = eroeffnung["stop_loss"], eroeffnung["take_profit"]
            ergebnis.append({
                "quelle": "papier", "status": "zu", "richtung": z["richtung"], "menge": z["menge"],
                "einstieg": {"zeit": epoche(z["einstieg_zeit"]), "preis": z["einstieg_preis"]},
                "ausstieg": {"zeit": epoche(z["ausstieg_zeit"]), "preis": z["ausstieg_preis"]},
                "brutto": z["brutto"], "netto": z["netto"], "stop": stop, "ziel": ziel})
        schutz = []
        for z in db.execute("SELECT * FROM orders WHERE status='offen' AND rolle IN ('stop_loss','take_profit')"):
            if symbol_schluessel(z["symbol"]) != schluessel:
                continue
            eltern = db.execute("SELECT zeit, preis FROM orders WHERE id=?", (z["eltern"],)).fetchone()
            schutz.append((z, (eltern["zeit"], eltern["preis"]) if eltern else None))
        for lot in db.execute("SELECT * FROM lots ORDER BY id"):
            if symbol_schluessel(lot["symbol"]) != schluessel or abs(lot["menge"]) < _EPS:
                continue
            lang = lot["menge"] > 0
            gegen = "verkauf" if lang else "kauf"
            # Die Schutzorders gehören zur Eröffnungsorder, die dieses Lot erzeugt hat (gleiche Zeit und gleicher Preis);
            # sonst (z. B. Teilausführungen) gelten die Schutzorders des Symbols auf der Gegenseite.
            eigene = [z for z, schluessel_eltern in schutz if z["seite"] == gegen and schluessel_eltern == (lot["zeit"], lot["preis"])] \
                or [z for z, _ in schutz if z["seite"] == gegen]
            stop = next((z["stop_preis"] for z in eigene if z["rolle"] == "stop_loss"), None)
            ziel = next((z["limit_preis"] for z in eigene if z["rolle"] == "take_profit"), None)
            ergebnis.append({
                "quelle": "papier", "status": "offen", "richtung": "long" if lang else "short", "menge": abs(lot["menge"]),
                "einstieg": {"zeit": epoche(lot["zeit"]), "preis": lot["preis"]}, "ausstieg": None,
                "brutto": None, "netto": None, "stop": stop, "ziel": ziel})
    except sqlite3.Error:
        return []
    finally:
        db.close()
    return ergebnis[-maximal:]


# ---------------------------------------------------------------- Trades aus dem Journal (Demo und Echt)

def journal_trades(journal: Journal, symbol: str, modus: str, maximal: int = 60) -> list[dict]:
    """Aus den ausgeführten Orders im Journal: Eröffnungen und Schließungen in zeitlicher Reihenfolge, FIFO gepaart.
    Ausstiege, die der Broker allein ausführt (Schutz-Stop), stehen nicht im Journal; solche Positionen bleiben hier offen."""
    schluessel = symbol_schluessel(symbol)
    ausfuehrungen = []
    for e in journal.entscheidungen(3000):
        if e["modus"] != modus or symbol_schluessel(e["symbol"]) != schluessel or not e["ausfuehrung"] or not e["ord"]:
            continue
        try:
            ausf, order = json.loads(e["ausfuehrung"]), json.loads(e["ord"])
        except ValueError:
            continue
        if ausf.get("status") not in ("ausgefuehrt", "teilweise") or ausf.get("preis") is None:
            continue
        menge = ausf.get("ausgefuehrt") or order.get("menge")
        zeit = epoche(ausf.get("zeit")) or epoche(e["zeit"])
        if not menge or zeit is None:
            continue
        ausfuehrungen.append({"zeit": zeit, "preis": ausf["preis"], "menge": float(menge),
                              "vorzeichen": 1 if order.get("seite") == "kauf" else -1,
                              "stop": order.get("stop_loss"), "ziel": order.get("take_profit")})
    ausfuehrungen.sort(key=lambda a: a["zeit"])
    offen: list[dict] = []
    fertig: list[dict] = []
    for a in ausfuehrungen:
        rest = a["menge"]
        while rest > _EPS and offen and offen[0]["vorzeichen"] != a["vorzeichen"]:
            lot = offen[0]
            nimm = min(rest, lot["menge"])
            fertig.append({"quelle": "journal", "status": "zu", "richtung": "long" if lot["vorzeichen"] > 0 else "short",
                           "menge": nimm, "einstieg": {"zeit": lot["zeit"], "preis": lot["preis"]},
                           "ausstieg": {"zeit": a["zeit"], "preis": a["preis"]},
                           "brutto": (a["preis"] - lot["preis"]) * lot["vorzeichen"] * nimm, "netto": None,
                           "stop": lot["stop"], "ziel": lot["ziel"]})
            lot["menge"] -= nimm
            rest -= nimm
            if lot["menge"] <= _EPS:
                offen.pop(0)
        if rest > _EPS:
            offen.append({**a, "menge": rest})
    for lot in offen:
        fertig.append({"quelle": "journal", "status": "offen", "richtung": "long" if lot["vorzeichen"] > 0 else "short",
                       "menge": lot["menge"], "einstieg": {"zeit": lot["zeit"], "preis": lot["preis"]}, "ausstieg": None,
                       "brutto": None, "netto": None, "stop": lot["stop"], "ziel": lot["ziel"]})
    return fertig[-maximal:]


def trades(ordner: Path, einst: dict, modus: str, symbol: str) -> list[dict]:
    """Eigene Trades zu einem Symbol: im Papierbetrieb aus dem Papierkonto, sonst aus dem Journal."""
    ordner = Path(ordner)
    if modus == "papier":
        return papier_trades(ordner / "papier.db", symbol)
    j = Journal(ordner / "journal.db", einst)
    try:
        return journal_trades(j, symbol, modus)
    finally:
        j.beenden()


# ---------------------------------------------------------------- KI-Einschätzung

def ki_einschaetzung(journal: Journal, symbol: str, jetzt: datetime | None = None) -> dict | None:
    """Die jüngste KI-Einschätzung (Marke [KI]) zum Symbol: eine Zeile je Horizont (1d, 3M, 1J) mit Richtung und
    Wahrscheinlichkeit. None, wenn es keine gibt."""
    schluessel = symbol_schluessel(symbol)
    treffer = [z for z in journal.einschaetzungen()
               if (z["begruendung"] or "").startswith(KI_MARKE) and symbol_schluessel(z["wert"]) == schluessel]
    if not treffer:
        return None
    letzte_zeit = max(z["zeit"] for z in treffer)
    jetzt = zu_utc(jetzt or jetzt_utc())
    prognosen = []
    for z in sorted((z for z in treffer if z["zeit"] == letzte_zeit), key=lambda z: z["id"]):
        m = _WAHRSCHEINLICHKEIT.search(z["begruendung"] or "")
        prognosen.append({"horizont": z["horizont"], "richtung": z["richtung"],
                          "wahrscheinlichkeit": float(m.group(1)) if m else None, "status": z["status"],
                          "treffer": None if z["treffer"] is None else bool(z["treffer"])})
    prognosen.sort(key=lambda p: KI_HORIZONTE.index(p["horizont"]) if p["horizont"] in KI_HORIZONTE else 99)
    zeit = datetime.fromisoformat(letzte_zeit)
    return {"zeit": epoche(zeit), "alter_min": max(0, round((jetzt - zu_utc(zeit)).total_seconds() / 60)),
            "preis": next((z["preis_bei_abgabe"] for z in treffer if z["zeit"] == letzte_zeit), None),
            "prognosen": prognosen}


# ---------------------------------------------------------------- Morgenlage

def morgenlage(ordner: Path, jetzt: datetime | None = None) -> dict:
    """Inhalt von `morgenlage.json` (falls vorhanden) samt Alter der Datei. Der Inhalt wird unbesehen weitergegeben; die Seite
    zeigt ihn nur als Text an. {"vorhanden": False} ohne Datei, {"vorhanden": False, "fehler": ...} bei unlesbarer Datei."""
    datei = Path(ordner) / MORGENLAGE_DATEI
    try:
        stat = datei.stat()
    except OSError:
        return {"vorhanden": False}
    jetzt = zu_utc(jetzt or jetzt_utc())
    stand = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    antwort = {"vorhanden": False, "stand": stand.isoformat(timespec="seconds"),
               "alter_h": round((jetzt - stand) / timedelta(hours=1), 1)}
    if stat.st_size > MORGENLAGE_MAX_BYTES:
        return {**antwort, "fehler": f"morgenlage.json ist zu groß ({stat.st_size // 1000} kB)"}
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {**antwort, "fehler": f"morgenlage.json nicht lesbar ({type(e).__name__})"}
    return {**antwort, "vorhanden": True, "daten": daten}
