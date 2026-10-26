"""Daten für den Reiter „Trends“ der Bedienoberfläche. Nur Lesen aus dem, was die vorhandenen Module schon gespeichert haben.

* Welt-Rotation (welt.py, `welt.db`): Gewinner und Verlierer nach Region und Anlageklasse, 1 Tag, 1 Woche, 1 Monat.
* Nachrichten-Radar (nachrichten.py, `nachrichten.db`): Top-Themen der letzten 24 Stunden mit Quellenzahl, Regionen und betroffenen Märkten.
* KI-Richtung je Watchlist-Wert: aus der jüngsten Morgenlage (`morgenlage.json`) oder dem Journal, was neuer ist (kurz, mittel, lang).
* Ähnliche Fälle früher: `historie.muster.aehnliche` zum wichtigsten aktuellen Thema des Radars, mit der Mehrmarkt-Reaktion samt n.

Fehlt ein Teil, steht im Ergebnis, welcher Befehl ihn füllt. Jeder Teil fällt für sich aus. Nichts davon sendet eine Order; die Zahlen
beschreiben Vergangenes und sind keine Prognose.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .. import nachrichten as _nachrichten
from .. import welt as _welt
from ..broker.basis import symbol_schluessel
from ..hilfen import jetzt_utc, zu_utc
from ..historie import ereignisse as _ereignisse
from ..historie import muster as _muster
from ..historie.speicher import Speicher
from ..journal import Journal
from . import live as _live

HINWEIS_WELT = "Noch keine Welt-Rotation gespeichert. Abrufen mit dem Knopf „neu abrufen“ oder: python3 -m kitrader welt"
HINWEIS_RADAR = "Noch keine Nachrichten gespeichert. Abrufen mit dem Knopf „neu abrufen“ oder: python3 -m kitrader nachrichten abrufen"
HINWEIS_KI = "Noch keine KI-Einschätzung. Reiter „KI“ (Einschätzung anstoßen) oder: python3 -m kitrader morgenlage"
HINWEIS_HISTORIE = "Keine Kursreihen der Historie geladen. Laden mit: python3 -m kitrader.historie laden dow btc"
THEMEN_MAX = 8
RANG_MAX = 3
TREFFER_MAX = 6
NAMEN_RICHTUNG = {"steigt": "steigt", "faellt": "fällt", "seitwaerts": "seitwärts"}


# ---------------------------------------------------------------- Welt-Rotation

def welt_teil(einst: dict, ordner: Path, jetzt: datetime) -> dict:
    try:
        rot = _welt.rotation_lesen(einst, ordner, jetzt)
    except Exception as e:
        return {"vorhanden": False, "fehler": f"Welt-Rotation nicht lesbar ({type(e).__name__}: {e})", "hinweis": HINWEIS_WELT}
    if rot is None:
        return {"vorhanden": False, "hinweis": HINWEIS_WELT}
    return {"vorhanden": True, "stand": rot["stand"], "alter_h": rot["alter_h"], "quellen": rot["quellen"],
            "weltschnitt": rot["weltschnitt"],
            "regionen": [{k: r.get(k) for k in ("region", "index", "aend", "rel", "offen")} for r in rot["regionen"]],
            "klassen": [{k: r.get(k) for k in ("klasse", "aend", "rel", "mitglieder")} for r in rot["klassen"]],
            "rang": {h: {"gewinner": [list(x) for x in v["gewinner"][:RANG_MAX]], "verlierer": [list(x) for x in v["verlierer"][:RANG_MAX]]}
                     for h, v in rot["rang"].items()},
            "umschichtung": rot["umschichtung"], "hinweise": rot["hinweise"], "fehlend": rot["fehlend"]}


# ---------------------------------------------------------------- Nachrichten-Radar

def radar_teil(einst: dict, ordner: Path, jetzt: datetime) -> tuple[dict, dict | None]:
    """(Anzeige, Radar-Daten für die Ähnlichkeitssuche)"""
    try:
        r = _nachrichten.radar_lesen(einst, ordner, jetzt)
    except Exception as e:
        return {"vorhanden": False, "fehler": f"Nachrichten-Radar nicht lesbar ({type(e).__name__}: {e})", "hinweis": HINWEIS_RADAR}, None
    if r is None:
        return {"vorhanden": False, "hinweis": HINWEIS_RADAR}, None
    d = r.daten
    themen = []
    for t in d["themen"][:THEMEN_MAX]:
        themen.append({"titel": t["titel"], "quelle": t["quelle"], "url": t["url"] if str(t.get("url", "")).startswith(("http://", "https://")) else "",
                       "verlage_24h": t["verlage_24h"], "verlage_6h": t["verlage_6h"], "regionen": t["regionen"], "laender": t.get("laender", []),
                       "arten": t.get("arten", []), "klassen": t.get("klassen", []), "branchen": t.get("branchen", []),
                       "neu": t["neu"], "amtlich": t["amtlich"]})
    kurz = lambda liste: [{k: x[k] for k in ("name", "verlage_6h", "verlage_24h", "faktor", "laender")} for x in liste[:6]]    # noqa: E731
    anzeige = {"vorhanden": True, "stand": r.stand.isoformat() if r.stand else None, "abgerufen": r.abgerufen.isoformat() if r.abgerufen else None,
               "alter_min": None if r.alter(jetzt) is None else round(r.alter(jetzt).total_seconds() / 60),
               "meldungen_24h": d["meldungen_24h"], "quellen_24h": d["quellen_24h"], "themen": themen,
               "arten": kurz(d["arten"]), "klassen": kurz(d["klassen"]), "branchen": kurz(d["branchen"])}
    return anzeige, d


# ---------------------------------------------------------------- KI-Richtung je Watchlist-Wert

_KI_NAMEN = (("kurz", "1d"), ("mittel", "3M"), ("lang", "1J"))


def _aus_morgenlage(ordner: Path, jetzt: datetime) -> dict[str, dict]:
    ml = _live.morgenlage(ordner, jetzt)
    if not ml.get("vorhanden"):
        return {}
    daten = ml["daten"]
    if not isinstance(daten, dict) or not isinstance(daten.get("werte"), list):
        return {}
    try:
        stand = datetime.fromisoformat(str(daten.get("erstellt"))) if daten.get("erstellt") else datetime.fromisoformat(ml["stand"])
    except ValueError:
        stand = datetime.fromisoformat(ml["stand"])
    erg = {}
    for w in daten["werte"]:
        if not isinstance(w, dict) or not isinstance(w.get("prognosen"), dict) or not w["prognosen"]:
            continue
        erg[symbol_schluessel(str(w.get("wert", "")))] = {
            "quelle": "Morgenlage", "stand": zu_utc(stand),
            **{n: {"richtung": p.get("richtung"), "wahrscheinlichkeit": p.get("wahrscheinlichkeit")}
               for n, p in w["prognosen"].items() if n in ("kurz", "mittel", "lang") and isinstance(p, dict)}}
    return erg


def ki_teil(einst: dict, ordner: Path, werte: list[str], jetzt: datetime) -> dict:
    """Je Wert die jüngste KI-Einschätzung: aus der Morgenlage oder dem Journal, je nachdem was neuer ist."""
    try:
        aus_ml = _aus_morgenlage(ordner, jetzt)
    except Exception:
        aus_ml = {}
    journal_ki: dict[str, dict] = {}
    try:
        j = Journal(Path(ordner) / "journal.db", einst)
        try:
            for w in werte:
                e = _live.ki_einschaetzung(j, w, jetzt)
                if e:
                    p = {x["horizont"]: x for x in e["prognosen"]}
                    journal_ki[symbol_schluessel(w)] = {
                        "quelle": "Journal", "stand": datetime.fromtimestamp(e["zeit"], tz=jetzt.tzinfo),
                        **{n: {"richtung": p[h]["richtung"], "wahrscheinlichkeit": p[h]["wahrscheinlichkeit"]} for n, h in _KI_NAMEN if h in p}}
        finally:
            j.beenden()
    except Exception:
        journal_ki = {}
    zeilen = []
    for w in werte:
        k = symbol_schluessel(w)
        kandidaten = [x for x in (aus_ml.get(k), journal_ki.get(k)) if x]
        if not kandidaten:
            zeilen.append({"wert": w, "vorhanden": False})
            continue
        z = max(kandidaten, key=lambda x: x["stand"])
        zeilen.append({"wert": w, "vorhanden": True, "quelle": z["quelle"], "stand": z["stand"].isoformat(timespec="seconds"),
                       "alter_h": round(max(0.0, (jetzt - z["stand"]).total_seconds() / 3600), 1),
                       **{n: z.get(n) for n in ("kurz", "mittel", "lang")}})
    return {"vorhanden": any(z["vorhanden"] for z in zeilen), "werte": zeilen, "hinweis": HINWEIS_KI}


# ---------------------------------------------------------------- Ähnliche Fälle früher

def _stat_zelle(st: dict) -> dict:
    keys = ("n", "median", "mittel", "anteil_negativ", "auffaellig")
    z = {k: st.get(k) for k in keys}
    if st["n"] and st.get("median") is None:               # unter n = 5: nur die Einzelwerte
        z["werte"] = [round(x, 4) for x in st["werte"][:5]]
    return z


def aehnliche_teil(einst: dict, ordner: Path, radar: dict | None, jetzt: datetime) -> dict:
    if radar is None:
        return {"vorhanden": False, "hinweis": "Ohne Nachrichten-Radar gibt es kein aktuelles Thema. " + HINWEIS_RADAR}
    bekannt = {e["art"] for e in _ereignisse.alle()}
    themen = sorted(radar["themen"], key=lambda t: (-t["punkte_6h"], -t["punkte_24h"]))
    if not themen:
        return {"vorhanden": False, "hinweis": "Das Radar hat zurzeit kein Thema, das mindestens zwei unabhängige Quellen meldeten."}
    letzter_fehler = ""
    for t in themen[:5]:
        arten = [a for a in t.get("arten", []) if a in bekannt][:1]          # nur die häufigste Art: weitere Arten verwässern die Suche
        eingabe = {"text": t["titel"], "arten": arten, "regionen": []}
        try:
            with Speicher(Path(ordner)) as sp:
                erg = _muster.aehnliche(eingabe, n=TREFFER_MAX, speicher=sp, heute=jetzt.date().isoformat())
        except _muster.MusterFehler as e:
            letzter_fehler = str(e)
            continue
        except Exception as e:
            return {"vorhanden": False, "hinweis": HINWEIS_HISTORIE, "fehler": f"Muster nicht berechenbar ({type(e).__name__}: {e})"}
        m = erg["muster"]
        gruppen = []
        for g in (m["gruppen"] if m else []):
            gruppen.append({"name": g["name"], "takt": g["takt"], "art": g["art"],
                            "h": {k: _stat_zelle(st) for k, st in g["stat"].items()}})
        return {"vorhanden": True,
                "thema": {"titel": t["titel"], "verlage_6h": t["verlage_6h"], "verlage_24h": t["verlage_24h"], "arten": arten},
                "n": erg["n"], "n_muster": erg["n_muster"], "min_n": _muster.MIN_N, "min_alter_tage": erg["min_alter_tage"],
                "merkmale": {"arten": sorted(erg["merkmale"]["arten"]), "regionen": erg["merkmale"]["regionen"]},
                "treffer": [{"beginn": x["ereignis"]["beginn"], "titel": x["ereignis"]["titel"], "art": x["ereignis"]["art"],
                             "region": x["ereignis"]["region"], "punkte": x["punkte"], "gruende": x["gruende"]} for x in erg["treffer"]],
                "gruppen": gruppen, "nicht_geladen": [list(x) if isinstance(x, (list, tuple)) else x for x in (m or {}).get("nicht_geladen", [])],
                "hinweis_historie": "" if gruppen else HINWEIS_HISTORIE}
    return {"vorhanden": False, "hinweis": "Kein aktuelles Thema ließ sich einer früheren Ereignisart oder Region zuordnen"
            + (f" ({letzter_fehler})" if letzter_fehler else "") + "."}


# ---------------------------------------------------------------- alles

def trends(einst: dict, ordner: Path, werte: list[str], jetzt: datetime | None = None) -> dict:
    """Alle vier Teile für den Reiter „Trends“. Jeder Teil fällt für sich aus (mit Hinweis), nie die ganze Antwort."""
    jetzt = zu_utc(jetzt or jetzt_utc())
    ordner = Path(ordner)
    welt = welt_teil(einst, ordner, jetzt)
    radar, radar_daten = radar_teil(einst, ordner, jetzt)
    try:
        aehnlich = aehnliche_teil(einst, ordner, radar_daten, jetzt)
    except Exception as e:
        aehnlich = {"vorhanden": False, "fehler": f"{type(e).__name__}: {e}", "hinweis": HINWEIS_HISTORIE}
    return {"stand": jetzt.isoformat(timespec="seconds"), "welt": welt, "radar": radar, "ki": ki_teil(einst, ordner, werte, jetzt),
            "aehnlich": aehnlich,
            "hinweis": "Rückblick und Zählungen aus öffentlichen Quellen, keine Prognose und kein Handelssignal."}
