"""Prüfung der geladenen Reihen gegen das Verzeichnis: fehlt, beginnt später, veraltet, größte Lücke. Keine Wertung echter Lücken."""
from __future__ import annotations

from datetime import date

from . import zeit
from .reihen import REIHEN
from .speicher import Speicher

VERALTET_TAGE = {"tag": 10, "monat": 75}
SPAET_TAGE = 31


def pruefen(speicher: Speicher | None = None, heute: date | None = None) -> list[dict]:
    """Je Reihe aus REIHEN ein Eintrag: id, status, anzahl, erstes, letztes, luecke {groesse, einheit, von, bis} oder None."""
    eigener = speicher is None
    speicher = speicher or Speicher()
    heute = heute or zeit.heute()
    try:
        return [_pruefe(r, [d for d, _ in speicher.werte(r.id)], heute) for r in REIHEN.values()]
    finally:
        if eigener:
            speicher.close()


def _pruefe(r, daten: list[str], heute: date) -> dict:
    if not daten:
        return {"id": r.id, "status": "fehlt", "anzahl": 0, "erstes": "", "letztes": "", "luecke": None, "endet": r.endet}
    status = []
    if zeit.tage_zwischen(r.start, daten[0]) > SPAET_TAGE:
        status.append("beginnt später als erwartet")
    if not r.endet and (heute - zeit.tag(daten[-1])).days > VERALTET_TAGE[r.takt]:
        status.append("veraltet")
    return {"id": r.id, "status": "; ".join(status) or "ok", "anzahl": len(daten), "erstes": daten[0],
            "letztes": daten[-1], "luecke": _luecke(daten, r.takt), "endet": r.endet}


def _luecke(daten: list[str], takt: str) -> dict | None:
    """Größte Lücke: bei Tagesreihen Tage zwischen zwei Werten, bei Monatsreihen fehlende Monate."""
    beste = None
    for a, b in zip(daten, daten[1:]):
        g = zeit.tage_zwischen(a, b) if takt == "tag" else zeit.monate_zwischen(a, b) - 1
        if beste is None or g > beste["groesse"]:
            beste = {"groesse": g, "einheit": "Tage" if takt == "tag" else "fehlende Monate", "von": a, "bis": b}
    return beste
