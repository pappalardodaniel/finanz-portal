"""Ereignisstudie: Was machte eine Reihe (z. B. der Dow) nach einem Ereignis? Keine Prognose, nur Rückblick.

studie(ereignis_id) rechnet für ein Ereignis, studie_art(art) fasst alle Ereignisse einer Art zusammen.
Ehrlich gemeint: n steht immer dabei, bei n < 5 gibt es keine Kennzahlen, nur die Einzelwerte.
Ereignisse überschneiden sich zeitlich; ihre Renditen sind nicht unabhängig voneinander.
"""
from __future__ import annotations

import statistics
from bisect import bisect_right

from . import ereignisse as ereig
from . import zeit
from .reihen import REIHEN
from .speicher import Speicher

HORIZONTE = (1, 3, 6, 12)           # Kalendermonate
MIN_N = 5                           # darunter keine Kennzahlen
DOW_START = "1885-02-16"
TOLERANZ_TAGE = {"tag": 5, "monat": 31}   # so weit nach dem letzten Datum gilt ein Zieltag noch als abgedeckt


class StudienFehler(Exception):
    pass


def standardreihe(beginn: str) -> str:
    return "dow" if beginn >= DOW_START else "shiller_sp"


class _Verlauf:
    def __init__(self, reihe: str, werte: list[tuple[str, float]]):
        if not werte:
            raise StudienFehler(f"Reihe „{reihe}“ ist nicht geladen (python3 -m kitrader.historie laden {reihe})")
        self.reihe = reihe
        self.d = [d for d, _ in werte]
        self.w = [w for _, w in werte]
        self.takt = REIHEN[reihe].takt if reihe in REIHEN else "tag"

    def index(self, datum: str) -> int | None:
        """Index des letzten Werts am oder vor dem Tag, None wenn die Reihe später beginnt."""
        i = bisect_right(self.d, datum) - 1
        return i if i >= 0 else None

    def abgedeckt(self, datum: str) -> bool:
        return zeit.tage_zwischen(self.d[-1], datum) <= TOLERANZ_TAGE[self.takt]


def _verlauf(speicher: Speicher, reihe: str) -> _Verlauf:
    return _Verlauf(reihe, speicher.werte(reihe))


def _rechnen(e: dict, v: _Verlauf) -> dict:
    beginn = e["beginn"]
    erg: dict = {"ereignis": e, "reihe": v.reihe, "takt": v.takt, "start": None, "horizonte": {h: None for h in HORIZONTE},
                 "tief": None, "erholung": None, "ende": None, "hinweise": []}
    hin = erg["hinweise"]
    i0 = v.index(beginn)
    if i0 is None:
        hin.append(f"Die Reihe {v.reihe} beginnt erst {v.d[0]}, das Ereignis liegt davor.")
        return erg
    if not v.abgedeckt(beginn):
        hin.append(f"Die Reihe {v.reihe} endet {v.d[-1]}, vor dem Beginn des Ereignisses.")
        return erg
    start = v.w[i0]
    erg["start"] = {"datum": v.d[i0], "wert": start}
    if e["genauigkeit"] != "tag":
        hin.append(f"Beginn nur auf {e['genauigkeit']} genau; {beginn} ist ein Platzhalter-Tag, die Renditen gelten ab diesem Tag.")
    if v.takt == "monat":
        hin.append("Monatswerte (Mittel): der Wert des Beginnmonats enthält auch Tage nach dem Beginn.")
    if zeit.tage_zwischen(v.d[i0], beginn) > (35 if v.takt == "monat" else 7):
        hin.append(f"Letzter bekannter Wert vor dem Beginn ist vom {v.d[i0]} (Börse zu oder Lücke).")
    # Renditen nach 1, 3, 6, 12 Monaten
    for h in HORIZONTE:
        ziel = zeit.monate_addieren(beginn, h)
        if not v.abgedeckt(ziel):
            erg["horizonte"][h] = {"ziel": ziel, "datum": None, "wert": None, "rendite": None}
            continue
        i = v.index(ziel)
        erg["horizonte"][h] = {"ziel": ziel, "datum": v.d[i], "wert": v.w[i], "rendite": v.w[i] / start - 1}
    # größter Rückgang innerhalb von 12 Monaten (gegenüber dem Startwert)
    ziel12 = zeit.monate_addieren(beginn, 12)
    lo, hi = bisect_right(v.d, beginn), bisect_right(v.d, ziel12)
    fenster = list(range(lo, hi))
    it = min(fenster, key=lambda i: v.w[i]) if fenster else None
    if it is None or v.w[it] >= start:
        it, tief_d, tief_w = i0, v.d[i0], start
    else:
        tief_d, tief_w = v.d[it], v.w[it]
    erg["tief"] = {"datum": tief_d, "wert": tief_w, "rendite": tief_w / start - 1, "fenster_vollstaendig": v.abgedeckt(ziel12)}
    if not erg["tief"]["fenster_vollstaendig"]:
        hin.append(f"Die 12 Monate sind noch nicht vorbei (Reihe endet {v.d[-1]}); das Tief kann noch tiefer werden.")
    # Erholung: erstes Datum nach dem Tief mit Wert >= Startwert
    if tief_w >= start:
        erg["erholung"] = {"erholt": True, "datum": v.d[i0], "tage": 0, "tage_seit_tief": 0}
    else:
        nach = next((i for i in range(it + 1, len(v.d)) if v.w[i] >= start), None)
        if nach is None:
            erg["erholung"] = {"erholt": False, "bis": v.d[-1]}
        else:
            erg["erholung"] = {"erholt": True, "datum": v.d[nach], "tage": zeit.tage_zwischen(beginn, v.d[nach]),
                               "tage_seit_tief": zeit.tage_zwischen(tief_d, v.d[nach])}
    # Beginn bis Ende (Rezessionen, Kriege …)
    if e.get("ende"):
        if v.index(e["ende"]) is not None and v.abgedeckt(e["ende"]):
            i = v.index(e["ende"])
            erg["ende"] = {"ende": e["ende"], "datum": v.d[i], "wert": v.w[i], "rendite": v.w[i] / start - 1}
        else:
            hin.append(f"Das Ende {e['ende']} liegt außerhalb der Reihe.")
    return erg


def studie(ereignis_id: str, reihe: str | None = None, speicher: Speicher | None = None) -> dict:
    """Studie für ein Ereignis. Standardreihe: dow ab 1885-02-16, davor shiller_sp."""
    e = ereig.ereignis(ereignis_id)
    reihe = reihe or standardreihe(e["beginn"])
    if reihe not in REIHEN:
        raise StudienFehler(f"unbekannte Reihe „{reihe}“")
    eigener = speicher is None
    speicher = speicher or Speicher()
    try:
        return _rechnen(e, _verlauf(speicher, reihe))
    finally:
        if eigener:
            speicher.close()


def _kennzahlen(werte: list[float]) -> dict:
    n = len(werte)
    if n < MIN_N:
        return {"n": n, "median": None, "mittel": None, "anteil_negativ": None, "werte": werte}
    return {"n": n, "median": statistics.median(werte), "mittel": statistics.fmean(werte),
            "anteil_negativ": sum(w < 0 for w in werte) / n, "werte": werte}


def studie_art(art: str, reihe: str | None = None, region: str | None = None, speicher: Speicher | None = None,
               auch_grobe: bool = False) -> dict:
    """Alle Ereignisse einer Art mit Kennzahlen je Horizont (n, Median, Mittel, Anteil negativ).

    Ereignisse mit genauigkeit quartal oder jahr werden nur mit auch_grobe=True mitgerechnet (der Beginn ist dort ein Platzhalter).
    """
    liste = ereig.ereignisse(art=art, region=region)
    if not liste:
        raise StudienFehler(f"keine Ereignisse der Art „{art}“" + (f" in Region „{region}“" if region else ""))
    if reihe is not None and reihe not in REIHEN:
        raise StudienFehler(f"unbekannte Reihe „{reihe}“")
    eigener = speicher is None
    speicher = speicher or Speicher()
    try:
        verlaeufe: dict[str, _Verlauf | None] = {}
        eintraege, ausgelassen = [], []
        for e in liste:
            if e["genauigkeit"] in ("quartal", "jahr") and not auch_grobe:
                ausgelassen.append((e["id"], f"Beginn nur auf {e['genauigkeit']} genau"))
                continue
            r = reihe or standardreihe(e["beginn"])
            if r not in verlaeufe:
                try:
                    verlaeufe[r] = _verlauf(speicher, r)
                except StudienFehler:
                    verlaeufe[r] = None
            if verlaeufe[r] is None:
                ausgelassen.append((e["id"], f"Reihe {r} nicht geladen"))
                continue
            s = _rechnen(e, verlaeufe[r])
            if s["start"] is None:
                ausgelassen.append((e["id"], "; ".join(s["hinweise"])))
                continue
            eintraege.append(s)
    finally:
        if eigener:
            speicher.close()
    statistik = {h: _kennzahlen([s["horizonte"][h]["rendite"] for s in eintraege
                                 if s["horizonte"][h] and s["horizonte"][h]["rendite"] is not None]) for h in HORIZONTE}
    statistik["tief"] = _kennzahlen([s["tief"]["rendite"] for s in eintraege if s["tief"]["fenster_vollstaendig"]])
    return {"art": art, "region": region, "reihe": reihe, "n_ereignisse": len(liste), "eintraege": eintraege,
            "ausgelassen": ausgelassen, "statistik": statistik,
            "reihen": sorted({s["reihe"] for s in eintraege}), "min_n": MIN_N}
