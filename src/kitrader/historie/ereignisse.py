"""Ereignisliste (ereignisse.json): Krisen, Geld- und Handelspolitik, Rezessionen … mit Quelle je Ereignis.

Die Datei liegt neben diesem Modul. beginn ist bei genauigkeit monat, quartal oder jahr ein Platzhalter-Tag
(1. des Monats bzw. Jahres), nicht der echte Tag.
"""
from __future__ import annotations

import json
import pathlib
from datetime import date

DATEI = pathlib.Path(__file__).with_name("ereignisse.json")
GENAUIGKEITEN = ("tag", "monat", "quartal", "jahr")
PFLICHT = ("id", "beginn", "genauigkeit", "art", "region", "titel", "beschreibung", "quelle")
_cache: list | None = None


def alle() -> list[dict]:
    global _cache
    if _cache is None:
        if not DATEI.exists():
            raise FileNotFoundError(f"{DATEI} fehlt")
        _cache = json.loads(DATEI.read_text(encoding="utf-8"))
    return _cache


def ereignis(id_: str) -> dict:
    for e in alle():
        if e["id"] == id_:
            return e
    raise KeyError(f"Ereignis „{id_}“ gibt es nicht (Liste: python3 -m kitrader.historie ereignisse)")


def ereignisse(art: str | None = None, region: str | None = None, von: str | None = None, bis: str | None = None,
               suche: str | None = None) -> list[dict]:
    """Gefilterte Ereignisse nach Beginn sortiert. art gleich; region enthält (USA trifft auch USA/China);
    von/bis nach Beginn, auch als 'JJJJ' oder 'JJJJ-MM'; suche in Titel, Beschreibung und id."""
    aus = []
    for e in alle():
        if art and e["art"].casefold() != art.casefold():
            continue
        if region and region.casefold() not in e["region"].casefold():
            continue
        if von and e["beginn"] < von:
            continue
        if bis and e["beginn"][:len(bis)] > bis:
            continue
        if suche and suche.casefold() not in f'{e["titel"]} {e["beschreibung"]} {e["id"]}'.casefold():
            continue
        aus.append(e)
    return sorted(aus, key=lambda e: (e["beginn"], e["id"]))


def _iso(s) -> bool:
    try:
        return isinstance(s, str) and len(s) == 10 and date.fromisoformat(s).isoformat() == s
    except ValueError:
        return False


def pruefe_ereignisse(liste: list[dict] | None = None) -> list[str]:
    """Liste von Beanstandungen (leer = in Ordnung): Pflichtfelder, ISO-Daten, genauigkeit, URL, doppelte id, Reihenfolge."""
    liste = alle() if liste is None else liste
    probleme, ids = [], set()
    for n, e in enumerate(liste):
        kennung = e.get("id") if isinstance(e, dict) else None
        kennung = kennung or f"Nr. {n + 1}"
        if not isinstance(e, dict):
            probleme.append(f"{kennung}: kein Objekt")
            continue
        for f in PFLICHT:
            if not e.get(f):
                probleme.append(f"{kennung}: Pflichtfeld {f} fehlt oder ist leer")
        if e.get("id") in ids:
            probleme.append(f"{kennung}: id doppelt")
        ids.add(e.get("id"))
        if e.get("beginn") and not _iso(e["beginn"]):
            probleme.append(f"{kennung}: beginn „{e['beginn']}“ ist kein ISO-Datum JJJJ-MM-TT")
        if e.get("ende") is not None and not _iso(e["ende"]):
            probleme.append(f"{kennung}: ende „{e['ende']}“ ist kein ISO-Datum JJJJ-MM-TT")
        if _iso(e.get("beginn")) and _iso(e.get("ende")) and e["ende"] < e["beginn"]:
            probleme.append(f"{kennung}: ende liegt vor beginn")
        if e.get("genauigkeit") and e["genauigkeit"] not in GENAUIGKEITEN:
            probleme.append(f"{kennung}: genauigkeit „{e['genauigkeit']}“ nicht in {'/'.join(GENAUIGKEITEN)}")
        q = e.get("quelle")
        if isinstance(q, dict):
            for f in ("name", "url", "abgerufen"):
                if not q.get(f):
                    probleme.append(f"{kennung}: quelle.{f} fehlt")
            if q.get("url") and not str(q["url"]).startswith("http"):
                probleme.append(f"{kennung}: quelle.url beginnt nicht mit http")
            if q.get("abgerufen") and not _iso(q["abgerufen"]):
                probleme.append(f"{kennung}: quelle.abgerufen ist kein ISO-Datum")
        elif e.get("quelle"):
            probleme.append(f"{kennung}: quelle ist kein Objekt")
    beginne = [e.get("beginn", "") for e in liste if isinstance(e, dict)]
    if beginne != sorted(beginne):
        probleme.append("Liste ist nicht nach beginn sortiert")
    return probleme
