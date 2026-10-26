"""Muster-Engine: Wie wirkte Weltgeschehen in der Vergangenheit auf verschiedene Märkte? Rückblick, keine Prognose.

Drei Bausteine, alle nur aus der Standardbibliothek und den gespeicherten Reihen:

1. Mehrmarkt-Reaktion: je Ereignis (muster_ereignis) oder je Gruppe von Ereignissen (muster_art, muster_menge) die Veränderung
   mehrerer Reihen (Dow, S&P, DAX, Bitcoin, Gold, Öl, VIX, Zins …) nach 1 Tag, 1 Woche, 1 Monat und 3 Monaten (Monatsreihen:
   1 und 3 Monate) samt tiefstem und höchstem Stand im Fenster von 3 Monaten. Jede Zahl wird mit den "normalen" Zeiträumen
   derselben Reihe verglichen (Verteilung aller Starttage der Reihe): Perzentil, Anteil negativ, Zufallsvergleich.
2. Ähnlichkeitssuche (aehnliche): findet frühere Ereignisse, die zu einem Text oder zur OSIRIS-Lage passen (Art, Region,
   Stichwörter deutsch/englisch, einfache Gewichtung), mit Begründung und der Mehrmarkt-Reaktion.
3. Krypto-Aktien-Zusammenhang (zusammenhang): rollierende Korrelation der Tagesrenditen von Bitcoin und Dow, aktuell,
   historisch und in Stressphasen.

Ehrlichkeitsregeln: n steht immer dabei; bei n < 5 gibt es keine Kennzahlen, nur die Einzelfälle; Ereignisse überschneiden sich,
die Fälle sind nicht unabhängig; die Auswahl der Ereignisse ist im Nachhinein getroffen; das Ergebnis grenzt ein und garantiert nichts.
"""
from __future__ import annotations

import math
import random
import re
import statistics
import unicodedata
import zlib
from bisect import bisect_left, bisect_right
from collections import deque

from . import ereignisse as ereig
from . import zeit
from .reihen import REIHEN
from .speicher import Speicher

HORIZONTE = (("1T", "1 Tag", 1, "tage"), ("1W", "1 Woche", 7, "tage"), ("1M", "1 Monat", 1, "monate"), ("3M", "3 Monate", 3, "monate"))
HORIZONT_NAMEN = {h[0]: h[1] for h in HORIZONTE}
MONATS_HORIZONTE = ("1M", "3M")        # Monatsreihen kennen nur diese
MIN_N = 5                              # darunter keine Kennzahlen, nur Einzelfälle
ZUFALL_ZIEHUNGEN = 1000                # Stichproben für den Zufallsvergleich (fester Startwert, also wiederholbar)
AUFFAELLIG = 0.05                      # Zufallsvergleich: höchstens so oft so extrem = "ungewöhnlich"
TOLERANZ_TAGE = {"tag": 5, "monat": 31}        # so weit nach dem letzten Datum gilt ein Zieltag noch als abgedeckt
START_ABSTAND_TAGE = {"tag": 7, "monat": 35}   # so weit darf der letzte Wert vor dem Beginn zurückliegen

# Gruppe: (Schlüssel, Anzeigename, Reihen in Reihenfolge der Vorliebe (die erste geladene gilt), Art der Veränderung)
GRUPPEN = (
    ("dow", "Dow Jones", ("dow",), "rendite"),
    ("sp", "S&P 500", ("shiller_sp",), "rendite"),
    ("dax", "DAX", ("dax",), "rendite"),
    ("uk", "UK-Aktien", ("uk_aktien",), "rendite"),
    ("btc", "Bitcoin", ("btc", "btc_blockchain"), "rendite"),
    ("gold", "Gold", ("gold", "gold_monat"), "rendite"),
    ("silber", "Silber", ("silber", "silber_monat"), "rendite"),
    ("platin", "Platin", ("platin", "platin_monat"), "rendite"),
    ("oel", "Öl WTI", ("oel_wti", "oel_wti_monat"), "rendite"),
    ("vix", "VIX", ("vix",), "rendite"),
    ("eurusd", "EUR/USD", ("eurusd",), "rendite"),
    ("zins10", "US-Zins 10 J.", ("us_zins10_tag", "shiller_zins10"), "differenz"),
)
GRUPPEN_KURZ = {g[0]: g for g in GRUPPEN}


HINWEIS_VOR = {"gold": "1971-08-15", "btc": "2014-01-01"}
HINWEISE_REIHEN = {"gold": "Der Goldpreis war bis 1971 weitgehend fest (Goldstandard, Bretton Woods), die Werte sind nicht marktbestimmt",
                   "btc": "Bitcoin war vor 2014 ein sehr kleiner, dünner Markt mit extremen Ausschlägen"}


class MusterFehler(Exception):
    pass


# ------------------------------------------------------------------------------------------------ Zahlen

def _pz(x: float | None, art: str = "rendite", stellen: int = 1, vorzeichen: bool = True) -> str:
    """Prozent (Rendite) bzw. Prozentpunkte (Differenz) mit Dezimalkomma."""
    if x is None:
        return "–"
    if art == "differenz":
        t = f"{x:+.2f} Pp" if vorzeichen else f"{x:.2f} Pp"
    else:
        t = f"{x * 100:+.{stellen}f} %" if vorzeichen else f"{x * 100:.{stellen}f} %"
    return t.replace(".", ",")


def _kr(x: float) -> str:
    """Korrelation mit Vorzeichen und Dezimalkomma."""
    return f"{x:+.2f}".replace(".", ",")


def perzentil(sortiert: list[float], x: float) -> float:
    """Anteil der Werte unter x in Prozent (Gleiche zählen halb). 0 bis 100."""
    if not sortiert:
        return float("nan")
    return (bisect_left(sortiert, x) + bisect_right(sortiert, x)) / 2 / len(sortiert) * 100


def korrelation(x: list[float], y: list[float]) -> float | None:
    """Pearson-Korrelation; None bei weniger als 3 Paaren oder ohne Schwankung."""
    n = len(x)
    if n < 3 or n != len(y):
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


# ------------------------------------------------------------------------------------------------ Verläufe

class Verlauf:
    """Eine Reihe als aufsteigende Datumsliste; kennt die Rechenregeln für Reaktionen."""

    def __init__(self, gruppe: str, reihe: str, werte: list[tuple[str, float]], art: str = "rendite", takt: str | None = None):
        self.gruppe, self.reihe, self.art = gruppe, reihe, art
        self.takt = takt or (REIHEN[reihe].takt if reihe in REIHEN else "tag")
        self.d = [d for d, _ in werte]
        self.w = [w for _, w in werte]

    def index(self, datum: str) -> int | None:
        i = bisect_right(self.d, datum) - 1
        return i if i >= 0 else None

    def abgedeckt(self, datum: str) -> bool:
        return zeit.tage_zwischen(self.d[-1], datum) <= TOLERANZ_TAGE[self.takt]

    def aenderung(self, w0: float, w1: float) -> float:
        return w1 / w0 - 1 if self.art == "rendite" else w1 - w0

    def horizonte(self) -> tuple:
        return HORIZONTE if self.takt == "tag" else tuple(h for h in HORIZONTE if h[0] in MONATS_HORIZONTE)

    def start_index(self, beginn: str) -> int | None:
        """Index des letzten Werts am oder vor dem Beginn; None, wenn die Reihe später beginnt, vorher endet oder der Wert unbrauchbar ist."""
        i0 = self.index(beginn)
        if i0 is None or zeit.tage_zwischen(self.d[i0], beginn) > START_ABSTAND_TAGE[self.takt]:
            return None
        if self.art == "rendite" and self.w[i0] <= 0:
            return None
        return i0

    def ziel_index(self, i0: int, ziel: str) -> int | None:
        """Index des Werts am Zieltag (letzter Wert am oder vor dem Tag, mindestens aber der nächste Wert nach dem Start)."""
        if not self.abgedeckt(ziel):
            return None
        i = max(self.index(ziel) or 0, i0 + 1)
        return i if i < len(self.d) else None


def _ziele(beginn: str) -> dict[str, str]:
    return {k: (zeit.tage_addieren(beginn, n) if einheit == "tage" else zeit.monate_addieren(beginn, n)) for k, _, n, einheit in HORIZONTE}


def reaktion(v: Verlauf, beginn: str) -> dict | None:
    """Veränderung der Reihe nach dem Beginn: {"start": (datum, wert), "h": {1T,1W,1M,3M: Veränderung|None}, "tief", "hoch"}.

    Startwert = letzter Wert am oder vor dem Beginn. Veränderung = Rendite (w1/w0 − 1) bzw. Differenz (Zinsen, in Prozentpunkten).
    tief/hoch = größter Rückgang bzw. Anstieg gegenüber dem Start innerhalb von 3 Monaten (0, wenn es keinen gab); None, wenn die 3 Monate
    nicht abgedeckt sind. Gerechnet wird nur, was die Reihe tatsächlich abdeckt.
    """
    i0 = v.start_index(beginn)
    if i0 is None:
        return None
    w0 = v.w[i0]
    ziele = _ziele(beginn)
    h = {}
    for k, _, _, _ in v.horizonte():
        i = v.ziel_index(i0, ziele[k])
        h[k] = None if i is None else v.aenderung(w0, v.w[i])
    tief = hoch = None
    if v.abgedeckt(ziele["3M"]):
        lo, hi = i0 + 1, bisect_right(v.d, ziele["3M"])
        if hi > lo:
            tief = min(0.0, v.aenderung(w0, min(v.w[lo:hi])))
            hoch = max(0.0, v.aenderung(w0, max(v.w[lo:hi])))
    return {"start": (v.d[i0], w0), "h": h, "tief": tief, "hoch": hoch}


_BASIS: dict[tuple, dict] = {}


def basis(v: Verlauf) -> dict:
    """Verteilung ("normale" Zeiträume): für jeden Starttag der Reihe dieselbe Rechnung wie im Ereignisfall.

    Rückgabe {"h": {Schlüssel: sortierte Liste}, "tief": [...], "hoch": [...], "von", "bis", "n_start"}. Wird je Reihe einmal gerechnet
    (Zwischenspeicher im Prozess, Schlüssel = Reihe, Länge, erstes und letztes Datum, letzter Wert).
    """
    schl = (v.reihe, v.art, len(v.d), v.d[0], v.d[-1], v.w[-1])
    if schl in _BASIS:
        return _BASIS[schl]
    horizonte = v.horizonte()
    h: dict[str, list[float]] = {k: [] for k, _, _, _ in horizonte}
    for i0 in range(len(v.d)):
        w0 = v.w[i0]
        if v.art == "rendite" and w0 <= 0:
            continue
        ziele = _ziele(v.d[i0])
        for k, _, _, _ in horizonte:
            i = v.ziel_index(i0, ziele[k])
            if i is not None:
                h[k].append(v.aenderung(w0, v.w[i]))
    tief, hoch = _tief_hoch_basis(v)
    erg = {"h": {k: sorted(x) for k, x in h.items()}, "tief": sorted(tief), "hoch": sorted(hoch),
           "von": v.d[0], "bis": v.d[-1], "n_start": len(v.d)}
    _BASIS[schl] = erg
    return erg


def _tief_hoch_basis(v: Verlauf) -> tuple[list[float], list[float]]:
    """Tief und Hoch im 3-Monats-Fenster für alle Starttage mit abgedecktem Fenster (gleitendes Minimum/Maximum, Zeitaufwand linear)."""
    n = len(v.d)
    tief: list[float] = []
    hoch: list[float] = []
    mn: deque = deque()                 # Indizes mit aufsteigenden Werten
    mx: deque = deque()                 # Indizes mit absteigenden Werten
    naechster = 0                       # nächster noch nicht aufgenommener Index
    for i0 in range(n):
        w0 = v.w[i0]
        ziel = zeit.monate_addieren(v.d[i0], 3)
        if not v.abgedeckt(ziel) or (v.art == "rendite" and w0 <= 0):
            continue
        hi = bisect_right(v.d, ziel)                    # Fenster [i0 + 1, hi)
        while naechster < hi:
            while mn and v.w[mn[-1]] >= v.w[naechster]:
                mn.pop()
            mn.append(naechster)
            while mx and v.w[mx[-1]] <= v.w[naechster]:
                mx.pop()
            mx.append(naechster)
            naechster += 1
        while mn and mn[0] <= i0:
            mn.popleft()
        while mx and mx[0] <= i0:
            mx.popleft()
        if hi > i0 + 1 and mn and mx:
            tief.append(min(0.0, v.aenderung(w0, v.w[mn[0]])))
            hoch.append(max(0.0, v.aenderung(w0, v.w[mx[0]])))
    return tief, hoch


# ------------------------------------------------------------------------------------------------ Kennzahlen mit Vergleich

def _stat(werte: list[float], vergleich: list[float], seed: str, mit_negativ: bool = True) -> dict:
    """Kennzahlen einer Liste von Reaktionen gegen die Verteilung `vergleich` (sortiert) derselben Reihe und desselben Horizonts.

    n < MIN_N: keine Kennzahlen, nur die Einzelwerte (bei n == 1 zusätzlich das Perzentil des Einzelwerts).
    n >= MIN_N: Median, Mittel, Anteil negativ, Perzentil des Medians in der Basis und Zufallsvergleich (Anteil von Stichproben aus n
    zufälligen Starttagen, deren Median höchstens bzw. mindestens so extrem war); "auffaellig" = "tiefer" oder "höher", wenn dieser
    Anteil höchstens 5 % beträgt.
    """
    n = len(werte)
    s: dict = {"n": n, "werte": list(werte), "median": None, "mittel": None, "anteil_negativ": None, "perzentil_median": None,
               "perzentil": None, "zufall_unten": None, "zufall_oben": None, "auffaellig": None,
               "basis": {"n": len(vergleich), "median": statistics.median(vergleich) if vergleich else None,
                         "anteil_negativ": (sum(x < 0 for x in vergleich) / len(vergleich)) if (vergleich and mit_negativ) else None}}
    if n == 0 or not vergleich:
        return s
    if n == 1:
        p = perzentil(vergleich, werte[0])
        s["perzentil"] = p
        s["auffaellig"] = "tiefer" if p <= AUFFAELLIG * 100 else "höher" if p >= 100 - AUFFAELLIG * 100 else None
        return s
    if n < MIN_N:
        return s
    med = statistics.median(werte)
    s.update(median=med, mittel=statistics.fmean(werte), perzentil_median=perzentil(vergleich, med),
             anteil_negativ=(sum(x < 0 for x in werte) / n) if mit_negativ else None)
    rng = random.Random(zlib.crc32(seed.encode()))
    unten = oben = 0
    for _ in range(ZUFALL_ZIEHUNGEN):
        m = statistics.median(rng.choices(vergleich, k=n))
        unten += m <= med
        oben += m >= med
    s["zufall_unten"], s["zufall_oben"] = unten / ZUFALL_ZIEHUNGEN, oben / ZUFALL_ZIEHUNGEN
    s["auffaellig"] = "tiefer" if s["zufall_unten"] <= AUFFAELLIG else "höher" if s["zufall_oben"] <= AUFFAELLIG else None
    return s


# ------------------------------------------------------------------------------------------------ Reihen laden

def gruppe_waehlen(schluessel: str) -> tuple:
    """Gruppe über Schlüssel (dow, btc …) oder über eine Reihen-ID (gold_monat …)."""
    if schluessel in GRUPPEN_KURZ:
        return GRUPPEN_KURZ[schluessel]
    for g in GRUPPEN:
        if schluessel in g[2]:
            return (g[0], g[1], (schluessel,), g[3])
    raise MusterFehler(f"unbekannte Reihe oder Gruppe „{schluessel}“ (Gruppen: {', '.join(GRUPPEN_KURZ)})")


def laden_verlaeufe(speicher: Speicher, reihen: list[str] | None = None) -> tuple[dict[str, Verlauf], list[tuple[str, str]]]:
    """Lädt die gewünschten Gruppen; Rückgabe (Verläufe nach Gruppenschlüssel, nicht geladene Gruppen als (Schlüssel, Name))."""
    gruppen = [gruppe_waehlen(r) for r in reihen] if reihen else list(GRUPPEN)
    verlaeufe: dict[str, Verlauf] = {}
    fehlen: list[tuple[str, str]] = []
    for schl, name, ids, art in gruppen:
        for rid in ids:
            w = speicher.werte(rid)
            if w:
                verlaeufe[schl] = Verlauf(schl, rid, w, art)
                break
        else:
            fehlen.append((schl, f"{name} ({' oder '.join(ids)})"))
    return verlaeufe, fehlen


def anzeigename(v: Verlauf) -> str:
    name = GRUPPEN_KURZ[v.gruppe][1] if v.gruppe in GRUPPEN_KURZ else v.gruppe
    return name + (" (M)" if v.takt == "monat" else "")


# ------------------------------------------------------------------------------------------------ Mehrmarkt-Reaktion

def _horizont_erlaubt(e: dict, schluessel: str, grobe: bool) -> bool:
    """Tag/Woche nur bei Ereignissen mit Tagesgenauigkeit; Monate auch bei Monatsgenauigkeit; Quartal/Jahr nur mit grobe=True (wie Monat)."""
    gen = e["genauigkeit"]
    if gen == "tag":
        return True
    if gen == "monat" or (grobe and gen in ("quartal", "jahr")):
        return schluessel in MONATS_HORIZONTE or schluessel in ("tief", "hoch")
    return False


def muster_menge(liste: list[dict], speicher: Speicher | None = None, reihen: list[str] | None = None, grobe: bool = False) -> dict:
    """Mehrmarkt-Reaktion für eine Liste von Ereignissen (Dicts wie in ereignisse.json).

    Rückgabe {"ereignisse": liste, "gruppen": [{"gruppe", "name", "reihe", "takt", "art", "faelle", "stat", "stat_tief", "stat_hoch",
    "basis", "ohne"}], "nicht_geladen": [...], "ausgelassen": [(id, Grund)], "min_n", "grobe"}.
    Je Fall: {"id", "beginn", "titel", "start": (datum, wert), "h": {…}, "tief", "hoch"}; Ereignisse, die die Reihe nicht abdeckt,
    stehen in "ohne" (id, Grund). Ereignisse mit zu grober Datierung werden nicht gerechnet (ausgelassen), außer grobe=True.
    """
    eigener = speicher is None
    speicher = speicher or Speicher()
    try:
        verlaeufe, fehlen = laden_verlaeufe(speicher, reihen)
    finally:
        if eigener:
            speicher.close()
    ausgelassen = [(e["id"], f"Beginn nur auf {e['genauigkeit']} genau") for e in liste
                   if e["genauigkeit"] in ("quartal", "jahr") and not grobe]
    rechnen = [e for e in liste if not (e["genauigkeit"] in ("quartal", "jahr") and not grobe)]
    gruppen = []
    for schl, v in verlaeufe.items():
        b = basis(v)
        faelle, ohne = [], []
        for e in rechnen:
            r = reaktion(v, e["beginn"])
            if r is None:
                if v.d[0] > e["beginn"]:
                    grund = f"Reihe beginnt erst {v.d[0]}"
                else:
                    grund = f"Reihe endet {v.d[-1]}"
                ohne.append((e["id"], grund))
                continue
            h = {k: (x if _horizont_erlaubt(e, k, grobe) else None) for k, x in r["h"].items()}
            faelle.append({"id": e["id"], "beginn": e["beginn"], "titel": e["titel"], "genauigkeit": e["genauigkeit"], "start": r["start"],
                           "h": h, "tief": r["tief"] if _horizont_erlaubt(e, "tief", grobe) else None,
                           "hoch": r["hoch"] if _horizont_erlaubt(e, "hoch", grobe) else None})
        stat = {}
        for k, _, _, _ in v.horizonte():
            stat[k] = _stat([f["h"][k] for f in faelle if f["h"].get(k) is not None], b["h"].get(k, []), f"{v.reihe}:{k}")
        gruppen.append({"gruppe": schl, "name": anzeigename(v), "reihe": v.reihe, "takt": v.takt, "art": v.art, "faelle": faelle,
                        "stat": stat,
                        "stat_tief": _stat([f["tief"] for f in faelle if f["tief"] is not None], b["tief"], f"{v.reihe}:tief", False),
                        "stat_hoch": _stat([f["hoch"] for f in faelle if f["hoch"] is not None], b["hoch"], f"{v.reihe}:hoch", False),
                        "basis": {"von": b["von"], "bis": b["bis"], "n_start": b["n_start"]}, "ohne": ohne})
    hinweise = []
    for g in gruppen:
        frueh = [f["beginn"] for f in g["faelle"] if f["beginn"] < HINWEIS_VOR.get(g["gruppe"], "0000")]
        if frueh:
            hinweise.append(f"{g['name']}: {HINWEISE_REIHEN[g['gruppe']]} (betrifft {len(frueh)} Fall/Fälle vor {HINWEIS_VOR[g['gruppe']]}).")
    return {"ereignisse": liste, "gruppen": gruppen, "nicht_geladen": fehlen, "ausgelassen": ausgelassen, "min_n": MIN_N, "grobe": grobe,
            "hinweise": hinweise}


def muster_ereignis(ereignis_id: str, speicher: Speicher | None = None, reihen: list[str] | None = None) -> dict:
    """Mehrmarkt-Reaktion für ein Ereignis (n = 1: je Zelle Veränderung und Perzentil in der Basis der Reihe)."""
    e = ereig.ereignis(ereignis_id)
    res = muster_menge([e], speicher, reihen, grobe=True)
    res["ereignis"] = e
    res["ausgelassen"] = []
    return res


def muster_art(art: str, region: str | None = None, speicher: Speicher | None = None, reihen: list[str] | None = None,
               grobe: bool = False) -> dict:
    """Mehrmarkt-Reaktion für alle Ereignisse einer Art (optional einer Region)."""
    liste = ereig.ereignisse(art=art, region=region)
    if not liste:
        raise MusterFehler(f"keine Ereignisse der Art „{art}“" + (f" in Region „{region}“" if region else ""))
    res = muster_menge(liste, speicher, reihen, grobe)
    res.update(art=art, region=region)
    return res


# ------------------------------------------------------------------------------------------------ Ähnlichkeitssuche

def _norm(text: str) -> str:
    """Kleinbuchstaben, ä/ö/ü zu ae/oe/ue, Akzente weg."""
    t = str(text or "").casefold().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


def _woerter(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _norm(text))


def _stamm(wort: str) -> str:
    """Grobe Wortstamm-Näherung für deutsche und englische Wörter: Plural-s weg, auf 6 Zeichen gekürzt."""
    if len(wort) > 4 and wort.endswith("s") and not wort.endswith("ss"):
        wort = wort[:-1]
    return wort[:6]


STOPPWOERTER = frozenset("""aber alle als also am an and are auch auf aus bei bis das dass dem den der des die dies diese durch ein eine einen einer
eines for from für gegen had has have hat hatte im in ist its mit nach nicht noch oder sein sich sie sind that the their then they this über um und
unter von vor war was waren wegen wie wird wurde wurden zu zum zur with which were been will bei beim seit sowie sowohl dabei danach
mehr nur schon sehr nach vom ueber fuer""".split())

# Stichwörter je Ereignisart (deutsch und englisch). "x*" = Wortanfang, sonst genau das Wort, Wörter mit Leerzeichen als Teiltext.
ART_WOERTER: dict[str, tuple[str, ...]] = {
    "unruhen": ("ausschreit*", "unruh*", "krawall*", "protest*", "demonstr*", "generalstreik*", "streik*", "aufstand*", "aufstaend*", "riot*",
                "unrest", "uprising*", "revolt*", "plunder*", "looting", "loot*", "barrikad*", "gelbwest*", "tumult*", "kundgebung*", "banlieue*",
                "tear gas", "traenengas", "wasserwerfer", "civil unrest", "strike action", "general strike", "walkout"),
    "krieg": ("krieg*", "war", "wars", "invasion*", "einmarsch*", "offensive*", "luftangriff*", "airstrike*", "air strike", "missile*",
              "rakete*", "raketen*", "kampfhandlung*", "militaerschlag*", "armee", "truppen"),
    "konflikt": ("konflikt*", "conflict*", "spannung*", "tension*", "eskalation*", "escalation", "clashes", "gefecht*", "grenzkonflikt*",
                 "huthi*", "houthi*", "waffenruhe*", "ceasefire"),
    "terror": ("terror*", "anschlag*", "anschlaege*", "attentat*", "attack*", "bomb*", "geisel*", "hostage*", "amoklauf*", "selbstmordattentat*"),
    "rohstoffschock": ("oel*", "oil", "opec*", "hormuz", "embargo*", "energie*", "energy", "fuel", "benzin*", "diesel*", "erdgas*", "pipeline*",
                       "engpass*", "chokepoint*", "rohstoff*", "commodit*", "lieferketten*", "spritpreis*", "kraftstoff*"),
    "bankenkrise": ("bankenkrise*", "bankrun", "bank run", "bankenpleite*", "bank failure", "bankenrettung*", "bank collapse"),
    "finanzkrise": ("finanzkrise*", "financial crisis", "kreditkrise*", "credit crunch", "lehman", "subprime"),
    "boersencrash": ("crash*", "boersensturz*", "kurssturz*", "selloff", "sell-off", "kursrutsch*", "boersenpanik*", "panic"),
    "geldpolitik": ("leitzins*", "notenbank*", "ezb", "ecb", "central bank", "rate hike", "rate cut", "zinserhoehung*", "zinssenkung*",
                    "geldpolitik*", "zinsentscheid*", "fed"),
    "zinsschock": ("zinsschock*", "rate shock", "zinsanstieg*", "zinsen steigen", "rising rates", "interest rates are up"),
    "inflation": ("inflation*", "teuerung*", "preisanstieg*", "cost of living", "lebenshaltungskosten*", "kaufkraft*", "energiepreis*",
                  "preissteigerung*", "price rises"),
    "handel": ("zoll*", "zoelle", "tariff*", "handelskrieg*", "trade war", "handelsstreit*", "handelspolitik*"),
    "sanktionen": ("sanktion*", "sanction*"),
    "waehrung": ("waehrung*", "currency", "abwertung*", "devaluation", "wechselkurs*", "currency crisis", "waehrungskrise*"),
    "schuldenkrise": ("schuldenkrise*", "staatspleite*", "zahlungsausfall*", "debt crisis", "sovereign default", "staatsschulden*"),
    "pandemie": ("pandemie*", "epidemie*", "virus*", "covid*", "corona*", "seuche*", "outbreak", "lockdown*"),
    "naturkatastrophe": ("erdbeben*", "earthquake*", "tsunami*", "hurrikan*", "hurricane*", "taifun*", "flut*", "flood*", "ueberschwemmung*",
                         "waldbrand*", "wildfire*", "duerre*", "drought", "vulkan*"),
    "wahl": ("wahl*", "election*", "referendum*", "abstimmung*"),
    "politik": ("regierung*", "koalition*", "putsch*", "coup", "ruecktritt*", "resign*", "misstrauensvotum*", "regierungssturz*", "government collapse"),
    "krypto": ("krypto*", "crypto*", "bitcoin*", "ethereum*", "stablecoin*", "exchange hack"),
    "rezession": ("rezession*", "recession", "wirtschaftskrise*", "konjunktureinbruch*"),
}
# Verwandte Arten: bekommen einen Teil der Punkte einer gleichen Art
ART_NAHE: dict[frozenset, float] = {
    frozenset(("krieg", "konflikt")): 0.6, frozenset(("krieg", "terror")): 0.3, frozenset(("konflikt", "terror")): 0.4,
    frozenset(("bankenkrise", "finanzkrise")): 0.6, frozenset(("zinsschock", "geldpolitik")): 0.6, frozenset(("inflation", "zinsschock")): 0.4,
    frozenset(("unruhen", "politik")): 0.4, frozenset(("unruhen", "inflation")): 0.3, frozenset(("rohstoffschock", "inflation")): 0.3,
    frozenset(("rohstoffschock", "krieg")): 0.3, frozenset(("waehrung", "schuldenkrise")): 0.4, frozenset(("boersencrash", "finanzkrise")): 0.4,
    frozenset(("handel", "sanktionen")): 0.5, frozenset(("rezession", "finanzkrise")): 0.3,
}

# Regionen: Name -> (Textstichwörter wie oben, Teilstrings in ereignisse.json "region" (kleingeschrieben, auch mit ä usw.), Großregion)
REGIONEN: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "Frankreich": (("frankreich*", "france", "french", "franzoes*", "paris", "macron", "marseille", "lyon"), ("frankreich",), "Europa"),
    "Deutschland": (("deutschland*", "german*", "berlin", "bundestag", "deutsch*"), ("deutschland",), "Europa"),
    "Vereinigtes Königreich": (("grossbritannien", "britain", "british", "england", "london", "uk", "united kingdom", "vereinigtes koenigreich"),
                               ("vereinigtes königreich", "großbritannien", "england"), "Europa"),
    "Italien": (("italien*", "italy", "italian*", "rom", "rome"), ("italien",), "Europa"),
    "Spanien": (("spanien*", "spain", "spanish", "madrid", "barcelona"), ("spanien",), "Europa"),
    "Griechenland": (("griechenland*", "greece", "greek", "athen"), ("griechenland",), "Europa"),
    "Europa": (("europa*", "europe*", "euroraum*", "eurozone", "eu"), ("europa", "euroraum", "westeuropa", "osteuropa"), "Europa"),
    "USA": (("usa", "amerika*", "america*", "united states", "washington", "trump", "us"), ("usa",), "Nordamerika"),
    "Naher Osten": (("naher osten", "nahost", "middle east", "iran*", "israel*", "gaza*", "libanon*", "lebanon", "hisbollah*", "hezbollah*",
                     "syrien*", "syria*", "irak*", "iraq*", "jemen*", "yemen*", "saudi*", "persisch*", "gulf", "golf", "teheran", "tehran", "riad",
                     "riyadh"),
                    ("naher osten", "iran", "israel", "libanon", "syrien", "irak", "jemen", "saudi", "gaza", "golf", "huthi"), "Naher Osten"),
    "Ukraine/Russland": (("ukrain*", "russland*", "russia*", "kiew", "kyiv", "moskau", "moscow", "putin"),
                         ("ukraine", "russland", "osteuropa", "kaukasus"), "Osteuropa"),
    "Asien": (("china", "chines*", "hongkong", "hong kong", "taiwan*", "peking", "beijing", "japan*", "indien*", "india*", "korea*", "asien*", "asia*",
               "nepal", "sri lanka", "bangladesch", "bangladesh"),
              ("china", "hongkong", "taiwan", "asien", "japan", "indien", "südasien", "vietnam", "korea", "nepal", "sri lanka"), "Asien"),
    "Afrika": (("afrika*", "africa*", "kenia", "kenya", "tunesien", "tunisia", "aegypten", "egypt", "nigeria", "suedafrika"),
               ("afrika", "kenia", "tunesien", "ägypten", "nigeria"), "Afrika"),
    "Lateinamerika": (("lateinamerika*", "latin america", "chile*", "brasil*", "argentin*", "venezuela*", "mexiko*", "mexico", "kolumbien"),
                      ("lateinamerika", "chile", "brasilien", "argentinien", "venezuela", "mexiko"), "Lateinamerika"),
}
LAENDERCODES = {"FR": "Frankreich", "DE": "Deutschland", "GB": "Vereinigtes Königreich", "UK": "Vereinigtes Königreich", "IT": "Italien",
                "ES": "Spanien", "GR": "Griechenland", "US": "USA", "IR": "Naher Osten", "IL": "Naher Osten", "LB": "Naher Osten",
                "SY": "Naher Osten", "IQ": "Naher Osten", "YE": "Naher Osten", "SA": "Naher Osten", "PS": "Naher Osten", "UA": "Ukraine/Russland",
                "RU": "Ukraine/Russland", "CN": "Asien", "HK": "Asien", "TW": "Asien", "JP": "Asien", "IN": "Asien", "NP": "Asien", "LK": "Asien",
                "KE": "Afrika", "EG": "Afrika", "TN": "Afrika", "NG": "Afrika", "CL": "Lateinamerika", "BR": "Lateinamerika",
                "AR": "Lateinamerika", "VE": "Lateinamerika", "MX": "Lateinamerika"}


def _passt(muster: str, norm_text: str, woerter: list[str]) -> bool:
    """Ein Stichwort gegen einen normalisierten Text: "x*" Wortanfang, mit Leerzeichen Teiltext, sonst ganzes Wort."""
    if " " in muster:
        return muster in norm_text
    if muster.endswith("*"):
        return any(w.startswith(muster[:-1]) for w in woerter)
    return muster in woerter


def arten_aus_text(text: str) -> dict[str, int]:
    """Welche Ereignisarten ein Text anspricht, mit Zahl der verschiedenen Stichwort-Treffer."""
    n = _norm(text)
    w = _woerter(text)
    erg = {}
    for art, muster in ART_WOERTER.items():
        treffer = sum(_passt(m, n, w) for m in muster)
        if treffer:
            erg[art] = treffer
    return erg


def regionen_aus_text(text: str) -> list[str]:
    n = _norm(text)
    w = _woerter(text)
    return [name for name, (stichw, _, _) in REGIONEN.items() if any(_passt(s, n, w) for s in stichw)]


def regionen_des_ereignisses(region: str) -> set[str]:
    r = str(region).casefold()
    return {name for name, (_, teile, _) in REGIONEN.items() if any(t in r for t in teile)}


def merkmale_aus_lage(daten: dict, arten: list[str] | None = None) -> dict:
    """Merkmale aus verdichteten OSIRIS-Daten (osiris.verdichten / Lage.daten): Konfliktzonen, Länderrisiko, Meldungen, Engpässe.

    Meldungen sind laut OSIRIS ungeprüft; ihre Stichwörter zählen deshalb erst ab zwei verschiedenen Treffern je Art und mit halbem Gewicht.
    `arten` (z. B. aus festen Regeln) zählen voll.
    """
    arten_g: dict[str, float] = {a: 1.0 for a in (arten or [])}
    regionen: list[str] = []
    texte: list[str] = []
    zonen = [z for z in (daten.get("konflikte", {}) or {}).get("zonen", []) if z.get("stufe") == "war"]
    if zonen:
        arten_g.setdefault("krieg", 1.0)
        arten_g.setdefault("konflikt", 1.0)
        for z in zonen:
            texte.append(str(z.get("name") or ""))
    for c in ((daten.get("laenderrisiko", {}) or {}).get("top", []) or [])[:5]:
        code = str(c.get("land") or "").upper()
        if code in LAENDERCODES and LAENDERCODES[code] not in regionen:
            regionen.append(LAENDERCODES[code])
        for m in c.get("merkmale") or []:
            texte.append(str(m))
    nach = daten.get("nachrichten", {}) or {}
    meldungen = [m.get("titel", "") for m in (nach.get("wichtig") or []) + (nach.get("neueste") or [])]
    kopf = " ".join(meldungen)
    for art, treffer in arten_aus_text(kopf).items():
        if treffer >= 2:
            arten_g[art] = max(arten_g.get(art, 0.0), 0.5)
    for r in regionen_aus_text(kopf + " " + " ".join(texte)):
        if r not in regionen:
            regionen.append(r)
    for z in zonen:
        for r in regionen_aus_text(str(z.get("name") or "")):
            if r not in regionen:
                regionen.append(r)
    return {"arten": arten_g, "regionen": regionen, "text": " ".join(texte + meldungen), "quelle": "OSIRIS-Lage"}


def merkmale(eingabe) -> dict:
    """Merkmale aus Text, aus einem Dict {"text", "arten", "regionen"}, aus verdichteten OSIRIS-Daten oder aus einem Objekt mit `.daten`."""
    if hasattr(eingabe, "daten") and isinstance(getattr(eingabe, "daten"), dict):
        eingabe = eingabe.daten
    if isinstance(eingabe, dict) and isinstance(eingabe.get("arten"), dict) and "regionen" in eingabe and "quelle" in eingabe:
        return eingabe                                             # schon fertige Merkmale (Ergebnis von merkmale())
    if isinstance(eingabe, str):
        arten = {a: 1.0 for a in arten_aus_text(eingabe)}
        return {"arten": arten, "regionen": regionen_aus_text(eingabe), "text": eingabe, "quelle": "Text"}
    if isinstance(eingabe, dict):
        if any(k in eingabe for k in ("konflikte", "laenderrisiko", "nachrichten", "schifffahrt", "maerkte", "vorfaelle")):
            return merkmale_aus_lage(eingabe)
        text = str(eingabe.get("text") or "")
        arten = {a: 1.0 for a in arten_aus_text(text)} if text else {}
        for a in eingabe.get("arten") or []:
            arten[a] = 1.0
        regionen = list(eingabe.get("regionen") or []) + [r for r in regionen_aus_text(text) if r not in (eingabe.get("regionen") or [])]
        return {"arten": arten, "regionen": regionen, "text": text, "quelle": "Merkmale"}
    raise MusterFehler("Text, Merkmale oder OSIRIS-Lage erwartet")


def _idf(liste: list[dict]) -> dict[str, float]:
    df: dict[str, int] = {}
    for e in liste:
        for s in {_stamm(w) for w in _woerter(e["titel"] + " " + e["beschreibung"]) if len(w) >= 4 and w not in STOPPWOERTER}:
            df[s] = df.get(s, 0) + 1
    n = len(liste)
    return {s: math.log((n + 1) / (c + 0.5)) for s, c in df.items()}


def bewerten(m: dict, e: dict, idf: dict[str, float]) -> tuple[float, list[str]]:
    """Punkte und Gründe für ein Ereignis gegen die Merkmale: Art (3, verwandt bis 1,8), Region (2, Großregion 1, weltweit 0,5),
    Stichwörter (je 0,5 mal Seltenheit, höchstens 3)."""
    punkte, gruende = 0.0, []
    art_p, art_text = 0.0, ""
    for a, gew in m["arten"].items():
        if a == e["art"]:
            p = 3.0 * gew
            text = f"gleiche Art ({a})"
        else:
            nahe = ART_NAHE.get(frozenset((a, e["art"])), 0.0)
            p = 3.0 * nahe * gew
            text = f"verwandte Art ({e['art']} ~ {a})"
        if p > art_p:
            art_p, art_text = p, text
    if art_p:
        punkte += art_p
        gruende.append(art_text)
    er = regionen_des_ereignisses(e["region"])
    reg_p, reg_text = 0.0, ""
    for q in m["regionen"]:
        if q in er:
            p, text = 2.0, f"gleiche Region ({q})"
        elif any(REGIONEN[q][2] == REGIONEN[x][2] for x in er):
            p, text = 1.0, f"gleiche Großregion ({REGIONEN[q][2]})"
        elif "welt" in str(e["region"]).casefold():
            p, text = 0.5, "weltweites Ereignis"
        else:
            continue
        if p > reg_p:
            reg_p, reg_text = p, text
    if reg_p:
        punkte += reg_p
        gruende.append(reg_text)
    # Stichwörter: Wörter der Anfrage, die (als Stamm) im Titel oder in der Beschreibung des Ereignisses vorkommen
    ev_stamm = {_stamm(w) for w in _woerter(e["titel"] + " " + e["beschreibung"]) if len(w) >= 4}
    gesehen, treffer, kw = set(), [], 0.0
    text_norm = _norm(m.get("text", ""))
    ortswoerter = {w for w in _woerter(text_norm) if any(_passt(st, text_norm, [w]) for _, (sw, _, _) in REGIONEN.items() for st in sw)}
    for w in _woerter(m.get("text", "")):
        s = _stamm(w)
        if len(w) < 4 or w in STOPPWOERTER or s in gesehen or w in ortswoerter:        # Ortsnamen zählen schon bei der Region
            continue
        gesehen.add(s)
        if s in ev_stamm and s in idf:
            kw += 0.5 * idf[s]
            treffer.append(w)
    if treffer:
        punkte += min(3.0, kw)
        gruende.append("Stichwort: " + ", ".join(treffer[:4]))
    return punkte, gruende


def aehnliche(eingabe, n: int = 10, speicher: Speicher | None = None, reihen: list[str] | None = None, min_alter_tage: int = 14,
              heute: str | None = None, mit_reaktion: bool = True, min_punkte: float = 2.0, auswahl_anteil: float = 0.5) -> dict:
    """Frühere Ereignisse, die zu Text oder Lage passen: bis zu n Treffer mit Punkten, Gründen und (mit_reaktion) der Mehrmarkt-Reaktion.

    Ein Treffer braucht mindestens eine Übereinstimmung in Art oder Region und `min_punkte`. Ereignisse der letzten `min_alter_tage` Tage
    werden ausgeklammert (die laufende Lage ist ihr eigenes Vorbild nicht; ihr Verlauf ist noch nicht belegt). Die Mehrmarkt-Reaktion
    ("muster") wird nur über Treffer mit mindestens `auswahl_anteil` der besten Punktzahl gerechnet (n_muster), damit schwache Treffer sie nicht
    verwässern. Rückgabe: {"merkmale", "treffer": [{"ereignis", "punkte", "gruende"}], "ausgeklammert": [...], "muster": muster_menge(...) | None, "n", "n_muster"}.
    """
    m = merkmale(eingabe)
    if not m["arten"] and not m["regionen"]:
        raise MusterFehler("Aus der Eingabe ließ sich weder eine Ereignisart noch eine Region erkennen (Beispiel: „Ausschreitungen in Frankreich“).")
    heute = heute or zeit.heute().isoformat()
    grenze = zeit.tage_addieren(heute, -min_alter_tage)
    alle = ereig.alle()
    idf = _idf(alle)
    bewertet, ausgeklammert = [], []
    for e in alle:
        p, g = bewerten(m, e, idf)
        art_oder_region = any(x.startswith(("gleiche Art", "verwandte Art", "gleiche Region", "gleiche Großregion")) for x in g)
        if p < min_punkte or not art_oder_region:
            continue
        if e["beginn"] > grenze:
            ausgeklammert.append(e)
            continue
        bewertet.append({"ereignis": e, "punkte": round(p, 2), "gruende": g})
    bewertet.sort(key=lambda t: (-t["punkte"], _umgekehrt(t["ereignis"]["beginn"])))      # bei gleichen Punkten zuerst die jüngeren
    treffer = bewertet[:n]
    ausgewaehlt = [t for t in treffer if treffer and t["punkte"] >= auswahl_anteil * treffer[0]["punkte"]]
    muster = muster_menge([t["ereignis"] for t in ausgewaehlt], speicher, reihen) if (mit_reaktion and ausgewaehlt) else None
    return {"merkmale": m, "treffer": treffer, "ausgeklammert": ausgeklammert, "muster": muster, "n": len(treffer),
            "n_muster": len(ausgewaehlt), "auswahl_anteil": auswahl_anteil, "min_alter_tage": min_alter_tage, "heute": heute}


def _umgekehrt(datum: str) -> int:
    """Sortierschlüssel: jüngeres Datum zuerst."""
    return -zeit.tag(datum).toordinal()


# ------------------------------------------------------------------------------------------------ Krypto und Aktien

FENSTER = (30, 90)                     # Handelstage
STRESS_TAGE = (-0.02, -0.03)           # Tage, an denen die Aktienreihe um mindestens so viel fiel
STRESS_RUECKGANG = 0.10                # Phasen, in denen die Aktienreihe mindestens so weit unter ihrem Hoch der letzten 250 Handelstage lag
JAHR_SEIT = "2020-01-01"


def gemeinsame_renditen(aktien: list[tuple[str, float]], krypto: list[tuple[str, float]]) -> list[tuple[str, float, float]]:
    """Renditen über denselben Zeitraum: von einem Handelstag der Aktienreihe zum nächsten (über Wochenenden hinweg), Krypto zu denselben
    Stichtagen (Krypto handelt täglich). Rückgabe [(Datum, Rendite Aktien, Rendite Krypto)] ohne Tage, an denen ein Wert fehlt."""
    k = dict(krypto)
    erg = []
    for (d0, a0), (d1, a1) in zip(aktien, aktien[1:]):
        if d0 in k and d1 in k and a0 > 0 and k[d0] > 0:
            erg.append((d1, a1 / a0 - 1, k[d1] / k[d0] - 1))
    return erg


def rollierende_korrelation(paare: list[tuple[str, float, float]], fenster: int) -> list[tuple[str, float]]:
    """Pearson-Korrelation der letzten `fenster` Paare, für jeden Tag ab dem fenster-ten (Summen laufend, Zeitaufwand linear)."""
    n = len(paare)
    if n < fenster:
        return []
    x = [p[1] for p in paare]
    y = [p[2] for p in paare]
    erg = []
    sx = sy = sxx = syy = sxy = 0.0
    for i in range(n):
        sx += x[i]; sy += y[i]; sxx += x[i] * x[i]; syy += y[i] * y[i]; sxy += x[i] * y[i]
        if i >= fenster:
            j = i - fenster
            sx -= x[j]; sy -= y[j]; sxx -= x[j] * x[j]; syy -= y[j] * y[j]; sxy -= x[j] * y[j]
        if i >= fenster - 1:
            vx = sxx - sx * sx / fenster
            vy = syy - sy * sy / fenster
            if vx > 1e-18 and vy > 1e-18:
                erg.append((paare[i][0], max(-1.0, min(1.0, (sxy - sx * sy / fenster) / math.sqrt(vx * vy)))))
    return erg


def _verteilung(werte: list[float]) -> dict:
    if not werte:
        return {"n": 0}
    s = sorted(werte)

    def q(p: float) -> float:
        k = (len(s) - 1) * p
        lo, hi = int(math.floor(k)), int(math.ceil(k))
        return s[lo] + (s[hi] - s[lo]) * (k - lo)
    return {"n": len(s), "min": s[0], "p10": q(0.1), "p25": q(0.25), "median": q(0.5), "p75": q(0.75), "p90": q(0.9), "max": s[-1],
            "anteil_positiv": sum(x > 0 for x in s) / len(s)}


def _tage_im_rueckgang(aktien: list[tuple[str, float]], schwelle: float, fenster: int = 250) -> set[str]:
    """Daten, an denen die Reihe mindestens `schwelle` unter ihrem Höchstwert der letzten `fenster` Werte lag."""
    erg: set[str] = set()
    mx: deque = deque()
    for i, (d, w) in enumerate(aktien):
        while mx and aktien[mx[-1]][1] <= w:
            mx.pop()
        mx.append(i)
        while mx[0] <= i - fenster:
            mx.popleft()
        if w <= aktien[mx[0]][1] * (1 - schwelle):
            erg.add(d)
    return erg


def zusammenhang(speicher: Speicher | None = None, aktien: str = "dow", krypto: str = "btc", fenster: tuple[int, ...] = FENSTER) -> dict:
    """Rollierende Korrelation der Tagesrenditen von Krypto (Standard Bitcoin) und einer Aktienreihe (Standard Dow; Tagesreihe nötig).

    Rückgabe: {"von", "bis", "paare", "fenster": {w: {"aktuell", "datum", "verteilung", "verteilung_seit", "perzentil_aktuell", "jahre"}},
    "stress_tage": {schwelle: {n, krypto_median, krypto_mittel, krypto_anteil_negativ, alle_*, korrelation}},
    "stress_phasen": {"schwelle", "n_stress", "n_sonst", "korrelation_stress", "korrelation_sonst"}, "hinweise"}.
    """
    eigener = speicher is None
    speicher = speicher or Speicher()
    try:
        a, k = speicher.werte(aktien), speicher.werte(krypto)
    finally:
        if eigener:
            speicher.close()
    if not a:
        raise MusterFehler(f"Reihe „{aktien}“ ist nicht geladen (python3 -m kitrader.historie laden {aktien})")
    if not k:
        raise MusterFehler(f"Reihe „{krypto}“ ist nicht geladen (python3 -m kitrader.historie laden {krypto})")
    if aktien in REIHEN and REIHEN[aktien].takt != "tag":
        raise MusterFehler(f"„{aktien}“ ist keine Tagesreihe; für Tagesrenditen wird eine Tagesreihe gebraucht (dow)")
    paare = gemeinsame_renditen(a, k)
    if len(paare) < max(fenster) + 5:
        raise MusterFehler(f"zu wenige gemeinsame Tage ({len(paare)}) für Fenster von {max(fenster)} Tagen")
    res: dict = {"aktien": aktien, "krypto": krypto, "von": paare[0][0], "bis": paare[-1][0], "paare": len(paare), "fenster": {},
                 "hinweise": [f"Renditen von Handelstag zu Handelstag der Reihe {aktien} (Wochenenden und Feiertage sind eingeschlossen), "
                              f"Krypto ({krypto}) zu denselben Stichtagen. Fenster zählen Handelstage, nicht Kalendertage.",
                              "Korrelation misst den gleichgerichteten Ausschlag der Tagesrenditen, nicht Ursache und Wirkung; sie schwankt stark von Phase zu Phase."]}
    for w in fenster:
        reihe = rollierende_korrelation(paare, w)
        if not reihe:
            continue
        werte = [r for _, r in reihe]
        jahre: dict[str, list[float]] = {}
        for d, r in reihe:
            jahre.setdefault(d[:4], []).append(r)
        res["fenster"][w] = {"aktuell": reihe[-1][1], "datum": reihe[-1][0], "verteilung": _verteilung(werte),
                             "verteilung_seit": _verteilung([r for d, r in reihe if d >= JAHR_SEIT]),
                             "perzentil_aktuell": perzentil(sorted(werte), reihe[-1][1]),
                             "jahre": {j: statistics.median(x) for j, x in sorted(jahre.items())}}
    alle_k = [p[2] for p in paare]
    basis_k = {"median": statistics.median(alle_k), "anteil_negativ": sum(x < 0 for x in alle_k) / len(alle_k)}
    stress = {}
    for schw in STRESS_TAGE:
        sel = [p for p in paare if p[1] <= schw]
        kk = [p[2] for p in sel]
        stress[schw] = {"n": len(sel), "krypto_median": statistics.median(kk) if kk else None, "krypto_mittel": statistics.fmean(kk) if kk else None,
                        "krypto_anteil_negativ": (sum(x < 0 for x in kk) / len(kk)) if kk else None,
                        "alle_median": basis_k["median"], "alle_anteil_negativ": basis_k["anteil_negativ"],
                        "korrelation": korrelation([p[1] for p in sel], kk) if len(sel) >= MIN_N else None,
                        "von": sel[0][0] if sel else None, "bis": sel[-1][0] if sel else None}
    res["stress_tage"] = stress
    rueck = _tage_im_rueckgang(a, STRESS_RUECKGANG)
    s1 = [p for p in paare if p[0] in rueck]
    s0 = [p for p in paare if p[0] not in rueck]
    res["stress_phasen"] = {"schwelle": STRESS_RUECKGANG, "n_stress": len(s1), "n_sonst": len(s0),
                            "korrelation_stress": korrelation([p[1] for p in s1], [p[2] for p in s1]),
                            "korrelation_sonst": korrelation([p[1] for p in s0], [p[2] for p in s0])}
    return res


# ------------------------------------------------------------------------------------------------ Kompakter Text für die KI

KONTEXT_GRUPPEN = ("dow", "dax", "btc", "gold", "oel", "vix", "zins10")
KONTEXT_HORIZONTE = ("1W", "1M", "3M")
HINWEIS_KI = ("Hinweis: Die Geschichte grenzt ein und garantiert nichts. Es sind wenige, im Nachhinein ausgewählte Fälle, die sich überschneiden und "
              "nicht unabhängig sind; unter n=5 gibt es nur Einzelwerte. Die Basis sind alle Zeiträume derselben Reihe (Perzentil/Anteil negativ), "
              "* = in einem Zufallsvergleich (1000 Stichproben) selten, kein Beweis für Ursache und Wirkung.")


def _zelle(st: dict, art: str) -> str:
    """Eine Kennzahlzelle für den Text: Median mit n, Anteil negativ und Basis; bei n < 5 die Einzelwerte."""
    if st["n"] == 0:
        return "–"
    if st["median"] is None:
        return f"n={st['n']}: " + ", ".join(_pz(x, art) for x in st["werte"])
    stern = "*" if st["auffaellig"] else ""
    neg = ""
    if st["anteil_negativ"] is not None and st["basis"]["anteil_negativ"] is not None:
        neg = f", {st['anteil_negativ'] * 100:.0f} % neg. (Basis {st['basis']['anteil_negativ'] * 100:.0f} %)"
    return f"{_pz(st['median'], art)}{stern} (n={st['n']}{neg})"


def kontext_text(daten: dict | None = None, arten: list[str] | None = None, text: str = "", speicher: Speicher | None = None,
                 max_zeichen: int = 3000, heute: str | None = None, n_ereignisse: int = 8) -> str:
    """Kompakter Abschnitt für den KI-Kontext: ähnliche frühere Ereignisse, ihre Mehrmarkt-Reaktion gegen die Basis, Krypto-Aktien-Zusammenhang.

    daten = verdichtete OSIRIS-Lage (osiris.verdichten bzw. Lage.daten); arten = zusätzlich feste Arten (z. B. aus den Regeln der KI);
    text = freier Text. Höchstens max_zeichen Zeichen; nie ein Absturz: Fehler stehen als Zeile im Text.
    """
    eigener = speicher is None
    try:
        speicher = speicher or Speicher()
    except Exception as e:                                 # Datenordner nicht lesbar
        return f"Historie nicht verfügbar ({e})."
    try:
        return _kontext_text(daten, arten, text, speicher, max_zeichen, heute, n_ereignisse)
    finally:
        if eigener:
            speicher.close()


def _kontext_text(daten, arten, text, speicher, max_zeichen, heute, n_ereignisse) -> str:
    teile: list[tuple[int, str]] = []          # (Wichtigkeit: kleiner = wichtiger, Zeile)
    m = None
    try:
        if daten:
            m = merkmale_aus_lage(daten, arten)
            if text:
                for a in arten_aus_text(text):
                    m["arten"].setdefault(a, 1.0)
                m["regionen"] += [r for r in regionen_aus_text(text) if r not in m["regionen"]]
                m["text"] += " " + text
        elif text:
            m = merkmale(text)
            for a in arten or []:
                m["arten"].setdefault(a, 1.0)
        elif arten:
            m = {"arten": {a: 1.0 for a in arten}, "regionen": [], "text": "", "quelle": "Arten"}
    except MusterFehler as e:
        teile.append((1, f"Ähnlichkeitssuche nicht möglich: {e}"))
    res = None
    if m and (m["arten"] or m["regionen"]):
        try:
            res = aehnliche(m, n=n_ereignisse, speicher=speicher, heute=heute)
        except MusterFehler as e:
            teile.append((1, f"Ähnlichkeitssuche nicht möglich: {e}"))
    else:
        teile.append((1, "Keine Ereignisart oder Region aus der Lage erkennbar; keine Ähnlichkeitssuche."))
    if res and res["treffer"]:
        erk = ", ".join(f"{a}" for a in m["arten"]) or "keine"
        reg = ", ".join(m["regionen"]) or "keine"
        teile.append((1, f"Erkannte Merkmale der Lage: Arten {erk}; Regionen {reg}."))
        zeilen = [f"Frühere Ereignisse, die zur Lage passen ({res['n']}; jüngste {res['min_alter_tage']} Tage ausgeklammert):"]
        for i, t in enumerate(res["treffer"][:5], 1):
            e = t["ereignis"]
            zeilen.append(f" {i}) {e['beginn']} {e['titel'][:70]} [{e['art']}, {e['region']}]: {'; '.join(t['gruende'])}")
        teile.append((2, "\n".join(zeilen)))
        mu = res["muster"]
        if mu and not mu["gruppen"]:
            teile.append((3, "Keine Kursreihe geladen (python3 -m kitrader.historie laden), daher ist keine Reaktion berechenbar."))
        elif mu:
            kopf = (f"Reaktion der {res['n_muster']} ähnlichsten Ereignisse (Veränderung ab Beginn: Median, n, Anteil negativ gegen die Basis aller Zeiträume "
                    "derselben Reihe; (M) = Monatsmittel):")
            zl = [kopf]
            for g in mu["gruppen"]:
                if g["gruppe"] not in KONTEXT_GRUPPEN:
                    continue
                zellen = [f"{HORIZONT_NAMEN[k]} {_zelle(g['stat'][k], g['art'])}" for k in KONTEXT_HORIZONTE if k in g["stat"]]
                zl.append(f" {g['name']}: " + "; ".join(zellen))
            fehlt = [n for _, n in mu["nicht_geladen"] if any(n.startswith(GRUPPEN_KURZ[k][1]) for k in KONTEXT_GRUPPEN)]
            if fehlt:
                zl.append(" Nicht geladen: " + ", ".join(sorted(f.split(" (")[0] for f in fehlt)))
            teile.append((3, "\n".join(zl)))
    elif res is not None:
        teile.append((2, "Keine früheren Ereignisse mit genug Übereinstimmung gefunden (mindestens Art oder Region und 2 Punkte)."))
    # Krypto und Aktien
    try:
        z = zusammenhang(speicher)
        f30, f90 = z["fenster"].get(30), z["fenster"].get(90)
        if f30 and f90:
            v90, vs = f90["verteilung"], f90["verteilung_seit"]
            zeile = (f"Krypto–Aktien (Bitcoin gegen Dow, Korrelation der Tagesrenditen, Fenster in Handelstagen): aktuell 30 T {_kr(f30['aktuell'])}, "
                     f"90 T {_kr(f90['aktuell'])} (Stand {f90['datum']}); seit {z['von'][:4]} Median 90 T {_kr(v90['median'])} "
                     f"(10–90 %: {_kr(v90['p10'])} bis {_kr(v90['p90'])}), aktuelles Perzentil {f90['perzentil_aktuell']:.0f}")
            if vs.get("n"):
                zeile += f"; seit {JAHR_SEIT[:4]} Median {_kr(vs['median'])}"
            zeile += "."
            s2 = z["stress_tage"][STRESS_TAGE[0]]
            if s2["n"] >= MIN_N:
                zeile += (f" An {s2['n']} Tagen mit Dow-Verlust ab {abs(STRESS_TAGE[0]) * 100:.0f} %: Bitcoin Median {_pz(s2['krypto_median'])}, "
                          f"{s2['krypto_anteil_negativ'] * 100:.0f} % negativ (an allen Tagen {s2['alle_anteil_negativ'] * 100:.0f} %)"
                          + (f", Korrelation an diesen Tagen {_kr(s2['korrelation'])}" if s2["korrelation"] is not None else "") + ".")
            sp = z["stress_phasen"]
            if sp["korrelation_stress"] is not None and sp["korrelation_sonst"] is not None:
                zeile += (f" In Rückgangsphasen (Dow mindestens {sp['schwelle'] * 100:.0f} % unter dem Hoch der letzten 250 Handelstage, "
                          f"{sp['n_stress']} Tage) {_kr(sp['korrelation_stress'])}, sonst {_kr(sp['korrelation_sonst'])}.")
            teile.append((2, zeile))
    except MusterFehler as e:
        teile.append((4, f"Krypto–Aktien-Zusammenhang nicht verfügbar: {e}"))
    teile.append((1, HINWEIS_KI))
    # Zeichenbudget: wichtige Teile zuerst, unwichtige weglassen, wenn es nicht reicht
    ausgabe: list[str] = []
    rest = max_zeichen
    for wichtig, zeile in sorted(enumerate(teile), key=lambda t: (t[1][0], t[0])):
        if len(zeile[1]) + 1 <= rest:
            ausgabe.append((wichtig, zeile[1]))
            rest -= len(zeile[1]) + 1
        elif zeile[0] <= 2 and rest > 200:
            ausgabe.append((wichtig, zeile[1][:rest - 2].rstrip() + "…"))
            rest = 0
    ausgabe.sort(key=lambda t: t[0])
    return "\n".join(z for _, z in ausgabe)
