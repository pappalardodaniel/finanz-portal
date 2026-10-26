"""Welt-Rotation: wer gewinnt, wer verliert. Märkte aus aller Welt (Indizes, Energie, Metalle, Agrar, Währungen, Krypto) nach Region und
Anlageklasse über 1 Tag, 1 Woche und 1 Monat, dazu die Stärke gegenüber dem Schnitt der Leitindizes.

    python3 -m kitrader welt [--ohne-abruf] [--erzwingen] [--json]

Quellen (nichts davon braucht einen Schlüssel):
- OSIRIS `/api/markets` (Indizes, Rüstung/Technik-Einzelwerte, Öl/Gas, Metalle, Agrar, Krypto, Devisen). Der Feed liefert je Markt den
  Kurs, den Schlusskurs des Vortags und eine kurze Kursreihe (`spark`, rund ein Monat Tagesschlüsse).
- Kraken (öffentliche Tageskerzen) für Krypto in Euro; ersetzt dann die Krypto-Zeilen von OSIRIS.
- Historie-Speicher (`historie.db`, Tagesreihen) als Reserve, wenn ein Markt im Feed fehlt, und für eigene Zusatzreihen (`[welt] reihen`),
  z. B. einen indischen oder brasilianischen Index, den man mit `python3 -m kitrader.historie einlesen` selbst in den Speicher legt.
Jeder Abruf wird als Schnappschuss in `welt.db` abgelegt. Sobald genug Schnappschüsse vorliegen (rund eine Woche bzw. ein Monat),
rechnet die Rotation 1 Woche und 1 Monat bevorzugt aus den eigenen Schnappschüssen (gleicher Zeitpunkt für alle Märkte); bis dahin aus
dem `spark` des Feeds (5 Handelstage bzw. 7 Tage bei Krypto, 1 Monat = Beginn der Reihe, rund 20 Handelstage).

Grenzen: Leitindizes in Landeswährung (Währungseffekte fehlen); China = Hang Seng (Hongkong), kein Festlandsindex; die Futures-Kurse von
OSIRIS (S&P 500, Nasdaq 100) laufen außerhalb der Börsenzeit weiter; „1 Tag“ ist bei geschlossenen Märkten der letzte Handelstag.
Die Rotation beschreibt Kursbewegungen. Sie misst keine Geldflüsse und sagt keine Zukunft voraus.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import einstellungen as _einst
from . import osiris as _osiris
from .hilfen import jetzt_utc, tabelle, zahl, zeit_text, zu_utc

HORIZONTE = ("1T", "1W", "1M")
HORIZONT_NAMEN = {"1T": "1 Tag", "1W": "1 Woche", "1M": "1 Monat"}
SPANNE_MIN = {"1T": 1.0, "1W": 2.0, "1M": 4.0}                 # Abstand in Prozentpunkten, ab dem eine „Umschichtung“ genannt wird
REGIONEN = ("USA", "Europa", "Deutschland", "Großbritannien", "China", "Japan", "Indien", "Brasilien", "Schwellenländer")
SCHWELLENLAENDER = ("China", "Indien", "Brasilien")           # Schwellenländer = Mittel dieser Leitindizes, wenn mindestens zwei vorliegen

# Leitindex je Region (der erste vorhandene zählt). Indien/Brasilien: nur über `[welt] reihen` (Historie) oder `leitindizes`.
LEITINDIZES: dict[str, list[str]] = {
    "USA": ["S&P 500", "Dow Jones"], "Europa": ["Euro Stoxx 50"], "Deutschland": ["DAX"], "Großbritannien": ["FTSE 100"],
    "China": ["Hang Seng"], "Japan": ["Nikkei 225"], "Indien": [], "Brasilien": [],
}

# Name im OSIRIS-Feed -> (Region, Anlageklasse, Art, Vorzeichen). Art: "kurs" (Prozent), "rendite" (Basispunkte), "risiko" (Prozent, nur Anzeige).
# Vorzeichen -1: Paar ist USD/xxx notiert, die Währung xxx gewinnt, wenn das Paar fällt.
OSIRIS_ZUORDNUNG: dict[str, tuple[str, str, str, int]] = {
    "S&P 500": ("USA", "Aktien", "kurs", 1), "Nasdaq 100": ("USA", "Aktien", "kurs", 1), "DAX": ("Deutschland", "Aktien", "kurs", 1),
    "FTSE 100": ("Großbritannien", "Aktien", "kurs", 1), "Euro Stoxx 50": ("Europa", "Aktien", "kurs", 1),
    "Nikkei 225": ("Japan", "Aktien", "kurs", 1), "Hang Seng": ("China", "Aktien", "kurs", 1),
    "VIX": ("USA", "Risiko", "risiko", 1), "US 10Y": ("USA", "Anleihen/Zinsen", "rendite", 1), "Dollar Index": ("USA", "Dollar", "kurs", 1),
    "WTI Crude": ("Welt", "Öl/Gas", "kurs", 1), "Brent Crude": ("Welt", "Öl/Gas", "kurs", 1), "Natural Gas": ("Welt", "Öl/Gas", "kurs", 1),
    "Gold": ("Welt", "Gold/Metalle", "kurs", 1), "Silver": ("Welt", "Gold/Metalle", "kurs", 1), "Copper": ("Welt", "Gold/Metalle", "kurs", 1),
    "Wheat": ("Welt", "Agrar", "kurs", 1), "Corn": ("Welt", "Agrar", "kurs", 1),
    "EUR/USD": ("Europa", "Währungen", "kurs", 1), "GBP/USD": ("Großbritannien", "Währungen", "kurs", 1),
    "USD/JPY": ("Japan", "Währungen", "kurs", -1), "USD/CNY": ("China", "Währungen", "kurs", -1),
}
GRUPPEN = {"indices": ("Aktien", "kurs"), "stocks": ("Rüstung/Tech-Aktien USA", "kurs"), "oil": ("Öl/Gas", "kurs"),
           "commodities": ("Gold/Metalle", "kurs"), "crypto": ("Krypto", "kurs"), "fx": ("Währungen", "kurs")}
KRAKEN_ERSETZT = {"BTC/EUR": "Bitcoin", "ETH/EUR": "Ethereum"}   # diese OSIRIS-Zeilen entfallen, wenn Kraken-Kerzen da sind
KLASSEN_RANG = ("Aktien", "Öl/Gas", "Gold/Metalle", "Agrar", "Krypto", "Währungen", "Rüstung/Tech-Aktien USA")   # in der Rangliste der Anlageklassen
KLASSEN_NAMEN = {"Währungen": "Währungen gg. USD"}

# Reserve und Zusatz aus dem Historie-Speicher (Tagesreihen). ersatz_fuer: nur verwenden, wenn dieser Name im Feed fehlt.
HISTORIE_REIHEN = [
    {"name": "Dow Jones", "region": "USA", "klasse": "Aktien", "reihe": "dow", "art": "kurs", "ersatz_fuer": ""},
    {"name": "Bitcoin", "region": "Welt", "klasse": "Krypto", "reihe": "btc", "art": "kurs", "ersatz_fuer": "Bitcoin"},
    {"name": "WTI Crude", "region": "Welt", "klasse": "Öl/Gas", "reihe": "oel_wti", "art": "kurs", "ersatz_fuer": "WTI Crude"},
    {"name": "VIX", "region": "USA", "klasse": "Risiko", "reihe": "vix", "art": "risiko", "ersatz_fuer": "VIX"},
    {"name": "EUR/USD", "region": "Europa", "klasse": "Währungen", "reihe": "eurusd", "art": "kurs", "ersatz_fuer": "EUR/USD"},
    {"name": "US 10Y", "region": "USA", "klasse": "Anleihen/Zinsen", "reihe": "us_zins10_tag", "art": "rendite", "ersatz_fuer": "US 10Y"},
]

STANDARD: dict = {
    "kraken": ["BTC/EUR", "ETH/EUR"],     # Krypto-Paare, deren Tageskerzen von Kraken geholt werden ([] = aus)
    "mindestabstand_s": 600,              # jüngere Schnappschüsse werden nicht erneut geholt
    "behalten_tage": 120,                 # ältere Schnappschüsse werden gelöscht
    "zeitlimit_s": 30,                    # je Abruf
    "historie": True,                     # Historie-Speicher als Reserve/Zusatz nutzen
    "reihen": HISTORIE_REIHEN,            # Zusatzreihen aus dem Historie-Speicher (name, region, klasse, reihe, art, ersatz_fuer)
    "leitindizes": {},                    # Region -> Name des Leitindex, überschreibt LEITINDIZES, z. B. { Indien = "Nifty 50" }
    "veraltet_tage": 4,                   # Kurse älter als so viele Tage werden gekennzeichnet und nicht in Schnitte gerechnet
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stand (zeit TEXT NOT NULL, schluessel TEXT NOT NULL, name TEXT NOT NULL, region TEXT, klasse TEXT, art TEXT,
    vorzeichen INTEGER NOT NULL DEFAULT 1, preis REAL NOT NULL, vortag REAL, spark TEXT, offen INTEGER, quelle TEXT, kurszeit TEXT,
    PRIMARY KEY (zeit, schluessel));
CREATE INDEX IF NOT EXISTS i_stand_schluessel ON stand(schluessel, zeit);
"""


class WeltFehler(RuntimeError):
    """Keine Marktdaten abrufbar oder gespeichert. Text ist für Menschen lesbar."""


def einstellungen_lesen(einst: dict | None) -> dict:
    cfg = {**STANDARD, **((einst or {}).get("welt") or {})}
    for k in ("mindestabstand_s", "behalten_tage", "zeitlimit_s", "veraltet_tage"):
        if isinstance(cfg[k], bool) or not isinstance(cfg[k], (int, float)) or cfg[k] < 0:
            raise _einst.EinstellungsFehler(f"welt.{k} muss eine Zahl >= 0 sein")
    if not isinstance(cfg["kraken"], list) or not all(isinstance(x, str) for x in cfg["kraken"]):
        raise _einst.EinstellungsFehler('welt.kraken muss eine Liste von Paaren sein, z. B. ["BTC/EUR"]')
    for r in cfg["reihen"]:
        if not isinstance(r, dict) or not {"name", "region", "klasse", "reihe"} <= set(r):
            raise _einst.EinstellungsFehler(f"welt.reihen: Eintrag braucht name, region, klasse, reihe: {r!r}")
    return cfg


def _zahl(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x else None


# --------------------------------------------------------------------------------------------------- Eingänge

def aus_osiris(markt: dict) -> list[dict]:
    """Macht aus der Antwort von `/api/markets` eine Liste einheitlicher Zeilen (ohne Zeit)."""
    erg = []
    for gruppe, (klasse0, art0) in GRUPPEN.items():
        tabelle_ = markt.get(gruppe)
        if not isinstance(tabelle_, dict):
            continue
        for name, q in tabelle_.items():
            preis = _zahl(q.get("price")) if isinstance(q, dict) else None
            if preis is None or preis <= 0:
                continue
            region, klasse, art, vz = OSIRIS_ZUORDNUNG.get(name, ("Welt" if gruppe != "indices" else str(name), klasse0, art0, 1))
            spark = [x for x in (_zahl(s) for s in (q.get("spark") or [])) if x is not None and x > 0]
            if gruppe == "stocks":
                region = "USA"
            ms = _zahl(q.get("time"))
            erg.append({"schluessel": str(name), "name": str(name), "region": region, "klasse": klasse, "art": art, "vorzeichen": vz,
                        "preis": preis, "vortag": _zahl(q.get("prev_close")) or None, "spark": spark,
                        "offen": None if q.get("market_open") is None else bool(q.get("market_open")), "quelle": "OSIRIS",
                        "kurszeit": datetime.fromtimestamp(ms, timezone.utc).isoformat(timespec="seconds") if ms and ms > 1e9 else None})
    return erg


def aus_kerzen(paar: str, kerzen: list) -> dict | None:
    """Tageskerzen (Kraken) -> eine Zeile. Die letzte Kerze ist die laufende; Vortag = Schluss der Kerze davor."""
    schluesse = [k.c for k in sorted(kerzen, key=lambda k: k.t) if k.c and k.c > 0]
    if len(schluesse) < 2:
        return None
    return {"schluessel": paar, "name": paar, "region": "Welt", "klasse": "Krypto", "art": "kurs", "vorzeichen": 1, "preis": schluesse[-1],
            "vortag": schluesse[-2], "spark": schluesse[-31:], "offen": True, "quelle": "Kraken",
            "kurszeit": zu_utc(sorted(kerzen, key=lambda k: k.t)[-1].t).isoformat(timespec="seconds")}


def _kraken_kerzen(paar: str, takt: str, anzahl: int) -> list:
    from .broker.kraken import KrakenBroker
    return KrakenBroker(nur_pruefen=True, waehrung="EUR").kerzen(paar, takt, anzahl)


# --------------------------------------------------------------------------------------------------- Welt

class Welt:
    def __init__(self, einstellungen: dict | None = None, pfad: str | Path | None = None, uhr: Callable[[], datetime] | None = None,
                 holen: Callable[[str, float], tuple[int, bytes, str]] = _osiris._http_holen,
                 kerzen_geber: Callable[[str, str, int], list] | None = None, historie_pfad: str | Path | None = None):
        self.einst = _einst.mit_standard(einstellungen) if einstellungen is not None else _einst.laden()
        self.cfg = einstellungen_lesen(self.einst)
        self.uhr, self.holen = uhr or jetzt_utc, holen
        self.kerzen_geber = kerzen_geber or _kraken_kerzen
        ordner = _einst.datenordner() if pfad is None else None
        self.historie_pfad = Path(historie_pfad) if historie_pfad is not None else (ordner / "historie.db" if ordner else None)
        self.db = sqlite3.connect(str(pfad if pfad is not None else ordner / "welt.db"))
        self.db.row_factory = sqlite3.Row
        try:
            self.db.executescript(_SCHEMA)
            self.db.commit()
        except sqlite3.Error:                                       # keine Datenbank / beschädigt: Verbindung nicht offen lassen
            self.db.close()
            raise

    def beenden(self) -> None:
        self.db.commit()
        self.db.close()

    # ---- Abruf und Speicher

    def letzter_stand(self) -> datetime | None:
        z = self.db.execute("SELECT MAX(zeit) FROM stand").fetchone()[0]
        return None if z is None else datetime.fromisoformat(z)

    def speichern(self, zeilen: list[dict], zeit: datetime) -> None:
        """Legt einen Schnappschuss ab. Die kurzen Kursreihen (`spark`) bleiben nur im jüngsten Schnappschuss."""
        z = zu_utc(zeit).isoformat(timespec="seconds")
        self.db.execute("UPDATE stand SET spark=NULL WHERE zeit < ?", (z,))
        for r in zeilen:
            self.db.execute("INSERT OR REPLACE INTO stand VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (z, r["schluessel"], r["name"], r["region"], r["klasse"], r["art"], r["vorzeichen"], r["preis"], r["vortag"],
                             json.dumps(r["spark"]) if r.get("spark") else None, None if r["offen"] is None else int(r["offen"]), r["quelle"], r["kurszeit"]))
        self.db.execute("DELETE FROM stand WHERE zeit < ?", ((zu_utc(zeit) - timedelta(days=self.cfg["behalten_tage"])).isoformat(timespec="seconds"),))
        self.db.commit()

    def abrufen(self, erzwingen: bool = False) -> dict:
        """Holt `/api/markets` (ein Abruf) und die Kraken-Tageskerzen und speichert einen Schnappschuss. Ist der letzte jünger als
        `mindestabstand_s`, passiert nichts (Ausnahme: erzwingen). Gibt {"zeit","neu","zeilen","quellen": {name: Text}} zurück.
        Ohne OSIRIS und ohne Kraken wird WeltFehler geworfen."""
        jetzt = zu_utc(self.uhr())
        letzter = self.letzter_stand()
        if letzter and not erzwingen and (jetzt - letzter).total_seconds() < self.cfg["mindestabstand_s"]:
            return {"zeit": letzter, "neu": False, "zeilen": 0, "quellen": {}}
        zeilen: list[dict] = []
        quellen: dict[str, str] = {}
        adresse = self.einst["osiris"]["adresse"].rstrip("/") + _osiris.FEEDS["maerkte"]
        status, inhalt, fehler = self.holen(adresse, self.cfg["zeitlimit_s"])
        if status == 200:
            try:
                zeilen += aus_osiris(json.loads(inhalt))
                quellen["OSIRIS /api/markets"] = f"ok, {len(zeilen)} Märkte"
            except (ValueError, AttributeError):
                quellen["OSIRIS /api/markets"] = "keine gültige JSON-Antwort"
        else:
            quellen["OSIRIS /api/markets"] = fehler or f"HTTP {status}"
        for paar in self.cfg["kraken"]:
            try:
                z = aus_kerzen(paar, self.kerzen_geber(paar, "1d", 40))
            except Exception as e:                                   # Netz, unbekanntes Paar ...
                quellen[f"Kraken {paar}"] = str(e)[:100]
                continue
            if z:
                zeilen.append(z)
                quellen[f"Kraken {paar}"] = "ok"
            else:
                quellen[f"Kraken {paar}"] = "zu wenige Kerzen"
        if not zeilen:
            raise WeltFehler("Keine Marktdaten abrufbar: " + "; ".join(f"{k}: {v}" for k, v in quellen.items()))
        self.speichern(zeilen, jetzt)
        return {"zeit": jetzt, "neu": True, "zeilen": len(zeilen), "quellen": quellen}

    # ---- Änderungen

    def _eigener_kurs(self, schluessel: str, ziel: datetime, toleranz_tage: float, vor: str) -> float | None:
        z = self.db.execute(
            "SELECT preis FROM stand WHERE schluessel=? AND zeit < ? AND zeit >= ? AND zeit <= ? "
            "ORDER BY ABS(julianday(zeit) - julianday(?)) LIMIT 1",
            (schluessel, vor, (ziel - timedelta(days=toleranz_tage)).isoformat(timespec="seconds"),
             (ziel + timedelta(days=toleranz_tage)).isoformat(timespec="seconds"), ziel.isoformat(timespec="seconds"))).fetchone()
        return None if z is None else z[0]

    def _aenderungen(self, r: dict, zeit: datetime) -> tuple[dict, dict]:
        """({"1T","1W","1M"}: Prozent bzw. Basispunkte oder None, {"1W": "eigen"|"feed", ...})"""
        p1, spark = r["preis"], r["spark"] or []
        vor = zeit.isoformat(timespec="seconds")
        p0 = {"1T": r["vortag"] or (spark[-2] if len(spark) >= 2 else None)}
        herkunft: dict[str, str] = {}
        eigen_w = self._eigener_kurs(r["schluessel"], zeit - timedelta(days=7), 1.5, vor)
        eigen_m = self._eigener_kurs(r["schluessel"], zeit - timedelta(days=30), 5.0, vor)
        schritte = 7 if r["klasse"] == "Krypto" else 5
        p0["1W"], herkunft["1W"] = (eigen_w, "eigen") if eigen_w else ((spark[-1 - schritte], "feed") if len(spark) > schritte else (None, ""))
        p0["1M"], herkunft["1M"] = (eigen_m, "eigen") if eigen_m else ((spark[0], "feed") if len(spark) >= 15 else (None, ""))
        erg = {}
        for h in HORIZONTE:
            a = p0[h]
            if not a or a <= 0 or not p1 or p1 <= 0:
                erg[h] = None
            elif r["art"] == "rendite":
                erg[h] = (p1 - a) * 100                                      # Basispunkte
            else:
                erg[h] = ((a / p1) if r["vorzeichen"] < 0 else (p1 / a)) * 100 - 100
        return erg, herkunft

    def _historie_zeilen(self, vorhanden: set[str], jetzt: datetime) -> list[dict]:
        """Zeilen aus dem Historie-Speicher (Tagesreihen): Zusatzreihen immer, Ersatz nur wenn der Name im Feed fehlt."""
        if not self.cfg["historie"] or self.historie_pfad is None or not Path(self.historie_pfad).is_file():
            return []
        from .historie.speicher import Speicher
        erg = []
        with Speicher(Path(self.historie_pfad).parent) as sp:
            for r in self.cfg["reihen"]:
                if r.get("ersatz_fuer") and r["ersatz_fuer"] in vorhanden:
                    continue
                if r["name"] in vorhanden:
                    continue
                von = (jetzt - timedelta(days=75)).date().isoformat()
                werte = [(d, w) for d, w in sp.werte(r["reihe"], von=von) if w and w > 0]
                if len(werte) < 2:
                    continue
                letztes = datetime.fromisoformat(werte[-1][0]).replace(tzinfo=timezone.utc)

                def vor(tage):
                    ziel = (letztes - timedelta(days=tage)).date().isoformat()
                    kand = [w for d, w in werte if d <= ziel]
                    return kand[-1] if kand else None
                spark = None
                erg.append({"schluessel": r["name"], "name": r["name"], "region": r["region"], "klasse": r["klasse"], "art": r.get("art", "kurs"),
                            "vorzeichen": 1, "preis": werte[-1][1], "vortag": werte[-2][1], "spark": spark, "offen": None, "quelle": "Historie",
                            "kurszeit": letztes.isoformat(timespec="seconds"), "_p1w": vor(7), "_p1m": vor(30)})
        return erg

    # ---- Rotation

    def rotation(self, jetzt: datetime | None = None) -> dict:
        """Berechnet die Rotation aus dem jüngsten Schnappschuss (ohne Netzzugriff). `stand` ist None, wenn nichts gespeichert ist."""
        jetzt = zu_utc(jetzt or self.uhr())
        zeit = self.letzter_stand()
        if zeit is None:
            return {"stand": None, "text": "Welt-Rotation: Es ist noch kein Schnappschuss gespeichert (python3 -m kitrader welt).", "regionen": [],
                    "klassen": [], "fehlend": [], "hinweise": [], "umschichtung": [], "rang": {}, "einzel": []}
        zeilen = []
        for z in self.db.execute("SELECT * FROM stand WHERE zeit=? ORDER BY schluessel", (zeit.isoformat(timespec="seconds"),)):
            d = dict(z)
            d["spark"] = json.loads(d["spark"]) if d["spark"] else []
            d["offen"] = None if d["offen"] is None else bool(d["offen"])
            zeilen.append(d)
        quellen_da = {r["quelle"] for r in zeilen}
        vorhanden = {r["schluessel"] for r in zeilen}                  # vor dem Ersetzen: auch OSIRIS-Zeilen, die Kraken ablöst, zählen als da
        if "Kraken" in quellen_da:
            ersetzt = {KRAKEN_ERSETZT[r["schluessel"]] for r in zeilen if r["quelle"] == "Kraken" and r["schluessel"] in KRAKEN_ERSETZT}
            zeilen = [r for r in zeilen if not (r["quelle"] == "OSIRIS" and r["schluessel"] in ersetzt)]
        hinweise: list[str] = []
        historie = self._historie_zeilen(vorhanden, jetzt)
        items = []
        for r in zeilen:
            ae, herkunft = self._aenderungen(r, zeit)
            items.append({**r, "aend": ae, "herkunft": herkunft})
        for r in historie:                                            # Reihen aus dem Historie-Speicher: Änderungen aus den Tageswerten
            p1w, p1m = r.pop("_p1w"), r.pop("_p1m")
            p1 = r["preis"]

            def ae(p0):
                if not p0:
                    return None
                return (p1 - p0) * 100 if r["art"] == "rendite" else (p1 / p0 - 1) * 100
            items.append({**r, "aend": {"1T": ae(r["vortag"]), "1W": ae(p1w), "1M": ae(p1m)}, "herkunft": {"1W": "historie", "1M": "historie"}})
        grenze = jetzt - timedelta(days=self.cfg["veraltet_tage"])
        for r in items:
            r["veraltet"] = bool(r["kurszeit"]) and datetime.fromisoformat(r["kurszeit"]) < grenze
            if r["veraltet"]:
                hinweise.append(f"{r['name']} ({r['quelle']}): letzter Kurs vom {r['kurszeit'][:10]}, nicht in die Schnitte gerechnet.")
        gueltig = [r for r in items if not r["veraltet"]]
        leit = {**LEITINDIZES, **{k: [v] for k, v in (self.cfg["leitindizes"] or {}).items()}}
        nach_name = {r["name"]: r for r in gueltig if r["klasse"] == "Aktien" and r["art"] == "kurs"}
        regionen: list[dict] = []
        fehlend: list[str] = []
        for region in REGIONEN:
            if region == "Schwellenländer":
                teil = [x for x in regionen if x["region"] in SCHWELLENLAENDER]
                if len(teil) >= 2:
                    regionen.append({"region": region, "index": "Mittel aus " + ", ".join(x["region"] for x in teil), "preis": None, "offen": None,
                                     "aend": {h: _mittel([x["aend"][h] for x in teil]) for h in HORIZONTE}, "herkunft": {}, "quelle": "berechnet"})
                else:
                    fehlend.append(f"{region} (Mittel braucht mindestens zwei Leitindizes aus {', '.join(SCHWELLENLAENDER)}; vorhanden: "
                                   f"{', '.join(x['region'] for x in teil) or 'keiner'})")
                continue
            r = next((nach_name[n] for n in leit.get(region, []) if n in nach_name), None)
            if r is None:
                fehlend.append(f"{region} (kein Leitindex im Feed" + (", Zusatzreihe möglich über [welt] reihen" if region in ("Indien", "Brasilien") else "") + ")")
                continue
            regionen.append({"region": region, "index": r["name"], "preis": r["preis"], "offen": r["offen"], "aend": r["aend"],
                             "herkunft": r["herkunft"], "quelle": r["quelle"]})
        basis = [x for x in regionen if x["region"] != "Schwellenländer"]
        welt = {h: _mittel([x["aend"][h] for x in basis]) for h in HORIZONTE}
        for x in regionen:
            x["rel"] = {h: (None if x["aend"][h] is None or welt[h] is None else x["aend"][h] - welt[h]) for h in HORIZONTE}
        klassen = []
        for k in KLASSEN_RANG:
            if k == "Aktien":
                mitglieder = [(x["index"], x["aend"]) for x in basis]
            else:
                mitglieder = [(r["name"], r["aend"]) for r in gueltig if r["klasse"] == k and r["art"] == "kurs"]
            if not mitglieder:
                fehlend.append(f"{KLASSEN_NAMEN.get(k, k)} (keine Daten)")
                continue
            aend = {h: _mittel([a[h] for _, a in mitglieder]) for h in HORIZONTE}
            klassen.append({"klasse": KLASSEN_NAMEN.get(k, k), "mitglieder": [n for n, _ in mitglieder], "aend": aend,
                            "rel": {h: (None if aend[h] is None or welt[h] is None else aend[h] - welt[h]) for h in HORIZONTE}})
        indikatoren = [{"name": r["name"], "art": r["art"], "preis": r["preis"], "aend": r["aend"], "quelle": r["quelle"]}
                       for r in gueltig if r["art"] in ("rendite", "risiko") or r["name"] == "Dollar Index"]
        einzel = [{"name": r["name"], "region": r["region"], "klasse": r["klasse"], "aend": r["aend"], "quelle": r["quelle"]}
                  for r in gueltig if r["art"] == "kurs" and r["klasse"] not in ("Risiko",) and r["name"] != "Dollar Index"]
        rang = {}
        for h in HORIZONTE:
            gruppen = sorted([(e["name"], e["aend"][h]) for e in einzel if e["aend"][h] is not None], key=lambda t: -t[1])
            rang[h] = {"gewinner": gruppen[:3], "verlierer": list(reversed(gruppen[-3:])) if len(gruppen) > 3 else []}
        umschichtung = _umschichtung(regionen, klassen)
        if any(r["herkunft"].get("1W") == "feed" or r["herkunft"].get("1M") == "feed" for r in items):
            hinweise.append("1 Woche/1 Monat stammen teils aus der Kursreihe des Feeds (5 Handelstage bzw. 7 Tage bei Krypto; Monat = rund 20 Handelstage), "
                            "bis genug eigene Schnappschüsse vorliegen.")
        if any(x["offen"] is False for x in regionen):
            hinweise.append("Märkte, die zum Abrufzeitpunkt geschlossen waren (zu), zeigen den letzten Handelstag.")
        res = {"stand": zeit.isoformat(timespec="seconds"), "alter_h": max(0.0, (jetzt - zeit).total_seconds() / 3600),
               "quellen": sorted(quellen_da | ({"Historie"} if historie else set())), "weltschnitt": {**welt, "n": len(basis)},
               "regionen": regionen, "klassen": klassen, "indikatoren": indikatoren, "einzel": einzel, "rang": rang,
               "umschichtung": umschichtung, "fehlend": fehlend, "hinweise": hinweise}
        res["text"] = als_text(res)
        return res


def _mittel(werte: list) -> float | None:
    w = [x for x in werte if x is not None]
    return sum(w) / len(w) if w else None


def _umschichtung(regionen: list[dict], klassen: list[dict]) -> list[str]:
    """Beobachtung der Zahlen: wo der Abstand zwischen stärkster und schwächster Region bzw. Anlageklasse groß ist."""
    erg = []
    for h in HORIZONTE:
        for titel, liste, schluessel in (("Aktienmärkte", [r for r in regionen if r["region"] != "Schwellenländer"], "region"),
                                         ("Anlageklassen", klassen, "klasse")):
            w = sorted([(x[schluessel], x["aend"][h]) for x in liste if x["aend"][h] is not None], key=lambda t: t[1])
            if len(w) >= 3 and w[-1][1] - w[0][1] >= SPANNE_MIN[h]:
                erg.append(f"{HORIZONT_NAMEN[h]}, {titel}: von {w[0][0]} ({_pz(w[0][1])} %) nach {w[-1][0]} ({_pz(w[-1][1])} %) "
                           f"(Abstand {zahl(w[-1][1] - w[0][1], 1)} Pp)")
    return erg


# --------------------------------------------------------------------------------------------------- Text

def _kuerzen(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 1].rstrip(", ") + "…"


def _pz(x: float | None, stellen: int = 2) -> str:
    return "-" if x is None else ("+" if x > 0 else "") + zahl(x, stellen)


def _bp(x: float | None) -> str:
    return "-" if x is None else ("+" if x > 0 else "") + zahl(x, 1) + " bp"


def als_text(rot: dict, kompakt: bool = False) -> str:
    """Lesbare Ausgabe (Tabellen). `kompakt`: nur das Wesentliche für die KI und die Morgenlage."""
    stand = datetime.fromisoformat(rot["stand"])
    kopf = (f"WELT-ROTATION, Stand {stand:%Y-%m-%d %H:%M} UTC (Alter {zahl(rot['alter_h'], 1)} h; Quellen {', '.join(rot['quellen'])}). "
            "Änderungen in Prozent, Rendite in Basispunkten; rel. = Abstand zum Schnitt der Leitindizes in Prozentpunkten. "
            "Beschreibung von Kursbewegungen, keine Geldflussmessung und keine Prognose.")
    z = [kopf]
    ws = rot["weltschnitt"]
    z.append(f"Schnitt der {ws['n']} Leitindizes: 1 T {_pz(ws['1T'])} %, 1 W {_pz(ws['1W'])} %, 1 M {_pz(ws['1M'])} %.")
    reg = sorted(rot["regionen"], key=lambda x: (x["aend"]["1W"] is None, -(x["aend"]["1W"] or 0)))
    if kompakt:
        z.append("Regionen (1 T/1 W/1 M in %, in Klammern rel. 1 W): " + "; ".join(
            f"{x['region']} {_pz(x['aend']['1T'], 1)}/{_pz(x['aend']['1W'], 1)}/{_pz(x['aend']['1M'], 1)} ({_pz(x['rel']['1W'], 1)})" for x in reg))
        z.append("Anlageklassen (1 T/1 W/1 M in %): " + "; ".join(
            f"{k['klasse']} {_pz(k['aend']['1T'], 1)}/{_pz(k['aend']['1W'], 1)}/{_pz(k['aend']['1M'], 1)}" for k in
            sorted(rot["klassen"], key=lambda k: (k["aend"]["1W"] is None, -(k["aend"]["1W"] or 0)))))
        for ind in rot["indikatoren"]:
            z.append(f"{ind['name']} {zahl(ind['preis'], 2)}: 1 T {_bp(ind['aend']['1T']) if ind['art'] == 'rendite' else _pz(ind['aend']['1T']) + ' %'}, "
                     f"1 W {_bp(ind['aend']['1W']) if ind['art'] == 'rendite' else _pz(ind['aend']['1W']) + ' %'}")
    else:
        z.append("\nRegionen (Leitindex in Landeswährung; sortiert nach 1 Woche):")
        z.append(tabelle(["Region", "Leitindex", "Stand", "Markt", "1 T", "1 W", "1 M", "rel 1 T", "rel 1 W", "rel 1 M"],
                         [[x["region"], x["index"], "-" if x["preis"] is None else zahl(x["preis"], 2 if x["preis"] < 1000 else 0),
                           {True: "offen", False: "zu", None: "-"}[x["offen"]], _pz(x["aend"]["1T"]), _pz(x["aend"]["1W"]), _pz(x["aend"]["1M"]),
                           _pz(x["rel"]["1T"]), _pz(x["rel"]["1W"]), _pz(x["rel"]["1M"])] for x in reg], links=(0, 1)))
        z.append("\nAnlageklassen (Mittel der Mitglieder, gleich gewichtet; rel. gegen den Schnitt der Leitindizes):")
        z.append(tabelle(["Klasse", "Mitglieder", "1 T", "1 W", "1 M", "rel 1 W", "rel 1 M"],
                         [[k["klasse"], _kuerzen(", ".join(k["mitglieder"]), 46), _pz(k["aend"]["1T"]), _pz(k["aend"]["1W"]), _pz(k["aend"]["1M"]),
                           _pz(k["rel"]["1W"]), _pz(k["rel"]["1M"])] for k in
                          sorted(rot["klassen"], key=lambda k: (k["aend"]["1W"] is None, -(k["aend"]["1W"] or 0)))], links=(0, 1)))
        if rot["indikatoren"]:
            z.append("\nIndikatoren (nicht in der Rangliste):")
            z.append(tabelle(["Name", "Stand", "1 T", "1 W", "1 M"],
                             [[i["name"], zahl(i["preis"], 2), *[(_bp(i["aend"][h]) if i["art"] == "rendite" else _pz(i["aend"][h]) + " %") for h in HORIZONTE]]
                              for i in rot["indikatoren"]], links=(0,)))
    z.append("\nGewinner und Verlierer unter allen Märkten (Einzelwerte):" if not kompakt else "Einzelwerte:")
    for h in HORIZONTE:
        g, v = rot["rang"][h]["gewinner"], rot["rang"][h]["verlierer"]
        z.append(f"  {HORIZONT_NAMEN[h]}: stärkste " + (", ".join(f"{n} {_pz(w, 1)} %" for n, w in g) or "-")
                 + "; schwächste " + (", ".join(f"{n} {_pz(w, 1)} %" for n, w in v) or "-"))
    if rot["umschichtung"]:
        z.append("\nUmschichtung (reine Beobachtung der Kurszahlen, kein Nachweis von Geldflüssen und keine Ursache):" if not kompakt
                 else "Umschichtung (nur Kursbeobachtung):")
        z += ["  - " + u for u in rot["umschichtung"]]
    else:
        z.append("Umschichtung: kein Abstand zwischen stärkster und schwächster Region/Klasse über der Schwelle "
                 f"({', '.join(f'{h} {zahl(SPANNE_MIN[h], 0)} Pp' for h in HORIZONTE)}).")
    if rot["fehlend"]:
        z.append("Nicht verfügbar: " + "; ".join(rot["fehlend"]) + ".")
    if not kompakt:
        z += ["Hinweis: " + h for h in rot["hinweise"]]
    return "\n".join(z)


# --------------------------------------------------------------------------------------------------- Einbau und Befehl

def rotation_lesen(einst: dict, ordner: Path, jetzt: datetime | None = None) -> dict | None:
    """Für ki.py und lage.py: Rotation aus einer vorhandenen welt.db, ohne Netz. None: keine Datenbank oder kein Schnappschuss."""
    pfad = Path(ordner) / "welt.db"
    if not pfad.is_file():
        return None
    w = Welt(einst, pfad, historie_pfad=Path(ordner) / "historie.db")
    try:
        r = w.rotation(jetzt)
    finally:
        w.beenden()
    return r if r["stand"] else None


def kontext_text(rot: dict) -> str:
    return als_text(rot, kompakt=True)


def morgen_block(einst: dict, ordner: Path, jetzt: datetime | None = None) -> dict | None:
    """Kurzer Block für die Morgenlage: {"stand", "text"} oder None, wenn nichts gespeichert ist."""
    try:
        rot = rotation_lesen(einst, ordner, jetzt)
    except (sqlite3.Error, OSError):
        return None
    if rot is None:
        return None
    z = []
    for h in ("1W", "1T"):
        g, v = rot["rang"][h]["gewinner"][:2], rot["rang"][h]["verlierer"][:2]
        z.append(f"{HORIZONT_NAMEN[h]}: stärkste " + ", ".join(f"{n} {_pz(w, 1)} %" for n, w in g) + "; schwächste " + ", ".join(f"{n} {_pz(w, 1)} %" for n, w in v))
    lang = [u for u in rot["umschichtung"] if not u.startswith("1 Tag")]            # Woche und Monat aussagekräftiger als der Tag
    z += ["Umschichtung: " + u for u in (lang or rot["umschichtung"])[:2]]
    return {"stand": rot["stand"], "text": "\n".join(z)}


def abrufen(einstellungen: dict | None = None, erzwingen: bool = False, **kw) -> dict:
    w = Welt(einstellungen, **kw)
    try:
        return w.abrufen(erzwingen)
    finally:
        w.beenden()


def argumente(p) -> None:
    p.add_argument("--ohne-abruf", action="store_true", help="nichts holen, nur den gespeicherten Schnappschuss zeigen")
    p.add_argument("--erzwingen", action="store_true", help="auch dann neu holen, wenn der letzte Schnappschuss noch frisch ist")
    p.add_argument("--json", action="store_true", help="Rotation als JSON statt als Tabellen")


def laufen(args, einst: dict, ordner: Path) -> int:
    from .autopilot import Abbruch
    w = Welt(einst, ordner / "welt.db", historie_pfad=ordner / "historie.db")
    try:
        if not args.ohne_abruf:
            try:
                erg = w.abrufen(erzwingen=args.erzwingen)
                if erg["neu"]:
                    print("Abruf: " + "; ".join(f"{k}: {v}" for k, v in erg["quellen"].items()) + f" ({zeit_text(erg['zeit'])} UTC)\n", flush=True)
                else:
                    print(f"Der letzte Schnappschuss ist jünger als {w.cfg['mindestabstand_s']} s und wird weiterverwendet (neu holen mit --erzwingen).\n")
            except WeltFehler as e:
                if w.letzter_stand() is None:
                    raise Abbruch(str(e)) from None
                print(f"Hinweis: Abruf fehlgeschlagen ({e}); es gilt der gespeicherte Schnappschuss.\n")
        rot = w.rotation()
        if rot["stand"] is None:
            raise Abbruch(rot["text"])
        print(json.dumps(rot, ensure_ascii=False, indent=1, default=str) if args.json else rot["text"])
        return 0
    finally:
        w.beenden()
