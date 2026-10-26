"""Brücke vom Markt-Dashboard zu den Handelsteilen von kitrader: alles unter `/api/k/...`.

Das Dashboard ist die Hauptoberfläche. Seine Reiter Trends, KI, Weltlage und Autopilot zeigen, was die kitrader-Module
(`kitrader.welt`, `nachrichten`, `historie`, `ki`, `lage`, `osiris`, `betrieb`, `autopilot`, `journal`) liefern. Die Brücke
benutzt dafür die Logik der kleinen Bedienoberfläche (`kitrader.oberflaeche.server.Oberflaeche`), nur ohne deren eigenen Server.

Lesen (GET): info, symbol, trends, morgenlage, journal, weltlage, autopilot, uebersicht, historie/ereignisse, historie/studie,
historie/reihen, aehnlich, chart/ereignisse und chart/studie (Ereignis-Marker und Hover im Chart), depot (beide Papierkonten).
Schreiben (POST, nur mit Kopf `X-Kitrader: 1`, gleicher Herkunft und JSON): trends/abrufen, ki, weltlage/abrufen, notaus,
autopilot/start, autopilot/stopp. Echte Orders sendet nichts davon: der Autopilot läuft nur im Papierbetrieb von hier aus,
der Not-Aus storniert und stellt glatt (wie `betrieb.py`).
"""
from __future__ import annotations

import copy
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from ... import betrieb
from ... import einstellungen as _einst
from ... import osiris as _osiris
from ...hilfen import jetzt_utc, zu_utc
from ...oberflaeche.server import Anfragefehler, Oberflaeche

AUTOPILOT_ENTSCHEIDUNGEN = 14

# Ereignis-Marker im Chart: welche Historie-Reihe zu einem Chart-Wert gehört (Basiswert ohne Quote wie USDT/EUR), sonst BTC bzw. Dow.
CHART_REIHEN = {"BTC": "btc", "XBT": "btc", "DOW": "dow", "DJI": "dow", "^DJI": "dow", "DIA": "dow", "SPY": "shiller_sp", "SPX": "shiller_sp",
                "^GSPC": "shiller_sp", "US500": "shiller_sp", "GOLD": "gold", "XAU": "gold", "GLD": "gold", "SILVER": "silber", "XAG": "silber",
                "SLV": "silber", "PLATIN": "platin", "XPT": "platin", "DAX": "dax", "^GDAXI": "dax", "VIX": "vix", "WTI": "oel_wti", "CL": "oel_wti",
                "USO": "oel_wti", "EURUSD": "eurusd"}
QUOTEN = ("USDT", "USDC", "BUSD", "EUR", "USD")
# Reihenfolge der Wichtigkeit: bei mehr Ereignissen im Bild als Marker erlaubt sind, bleiben die vorderen Arten
ART_RANG = ("boersencrash", "finanzkrise", "bankenkrise", "krieg", "pandemie", "rezession", "zinsschock", "rohstoffschock", "schuldenkrise",
            "geldpolitik", "terror", "konflikt", "waehrung", "handel", "krypto", "inflation", "wahl", "politik", "naturkatastrophe",
            "regulierung", "sanktionen", "unruhen", "unglueck")
MARKER_GENAU = ("tag", "monat")          # Quartal und Jahr haben nur einen Platzhalter-Tag: kein Marker an einer Kerze


def _ohne_innere(x):
    """Entfernt Schlüssel mit führendem Unterstrich (nur zum Vergleich gedacht) aus einer verschachtelten Struktur."""
    if isinstance(x, dict):
        return {k: _ohne_innere(v) for k, v in x.items() if not str(k).startswith("_")}
    if isinstance(x, list):
        return [_ohne_innere(v) for v in x]
    return x


class Bruecke:
    """Hält die Einstellungen, den Datenordner und (im Papierbetrieb) die Steuerung des Autopiloten."""

    def __init__(self, einst: dict, ordner: Path, steuerung=None, uhr: Callable[[], datetime] | None = None, **kwargs):
        self.einst = _einst.mit_standard(einst)
        self.ordner = Path(ordner)
        self.app = Oberflaeche(self.einst, self.ordner, uhr=uhr, **kwargs)
        self.steuerung = steuerung
        self._steuerung_sperre = threading.Lock()
        self.uhr = uhr or jetzt_utc

    # ---- Autopilot-Steuerung

    def _steuerung(self):
        with self._steuerung_sperre:
            if self.steuerung is None:
                from ... import start as _start
                e = copy.deepcopy(self.einst)
                if e["modus"] == "papier":
                    e, _ = _start.vorgaben_anwenden(e, _start.roh_einstellungen())
                self.steuerung = _start.AutopilotSteuerung(e, self.ordner, e["modus"], ausgabe=lambda *_: None)
            return self.steuerung

    # ---- GET

    def get(self, pfad: str, q: dict[str, list[str]]):
        """pfad ohne Vorsatz, z. B. "trends" oder "historie/studie". Wirft Anfragefehler."""
        a = self.app
        if pfad == "info":
            return self.info()
        if pfad == "symbol":
            return a.get("/api/symbol", q)
        if pfad == "trends":
            return a.trends()
        if pfad == "morgenlage":
            return a.get("/api/morgenlage", q)
        if pfad == "journal":
            return a.get("/api/journal", q)
        if pfad == "weltlage":
            return self.weltlage()
        if pfad == "uebersicht":
            return a.uebersicht()
        if pfad == "autopilot":
            return self.autopilot()
        if pfad == "aehnlich":
            return self.aehnlich(q)
        if pfad == "chart/ereignisse":
            return self.chart_ereignisse(q)
        if pfad == "chart/studie":
            return self.chart_studie(q)
        if pfad == "depot":
            return self.depot()
        if pfad in ("historie/ereignisse", "historie/studie", "historie/reihen", "historie/reihe"):
            return a.get("/api/" + pfad, q)
        raise Anfragefehler(404, "Unbekannte Adresse")

    def info(self) -> dict:
        e = self.einst
        modus = e["modus"]
        watch = list(dict.fromkeys([*e["watchlist"]["werte"], *e["autopilot"]["symbole"]]))
        return {"modus": modus, "modus_text": betrieb.MODUS_TEXT[modus], "echtes_geld": modus == "echt", "waehrung": e["waehrung"],
                "kursquelle": e["kursquelle"], "ki_treiber": e["ki"]["treiber"], "ki_treiber_liste": list(_einst.KI_TREIBER),
                "watchlist": watch, "broker": e["broker"] if modus != "papier" else "papier",
                "autopilot_steuerbar": modus == "papier"}

    # ---- Chart: Ereignisse als Marker

    def chart_reihe(self, symbol: str, art: str) -> dict:
        """Historie-Reihe für die Ereignisstudie zu einem Chart-Wert: die eigene, wenn es sie gibt, sonst BTC (Krypto) bzw. Dow (Rest)."""
        s = (symbol or "").strip().upper().replace("/", "").replace("-", "")     # BTC/EUR, BTC-USD, BTCUSDT
        basis = s
        for quote in QUOTEN:
            if s.endswith(quote) and len(s) > len(quote):
                basis = s[:-len(quote)]
                break
        eigene = CHART_REIHEN.get(basis) or CHART_REIHEN.get(s)
        reihe = eigene or ("btc" if art == "krypto" else "dow")
        info = next((r for r in self.app.historie_reihen()["reihen"] if r["id"] == reihe), None)
        if info is None:
            raise Anfragefehler(404, f"Reihe {reihe!r} gibt es nicht")
        hinweis = "" if eigene else (f"Für {s or 'diesen Wert'} gibt es keine eigene Reihe: Studie ersatzweise für "
                                     f"{'Bitcoin' if reihe == 'btc' else 'den Dow'}.")
        if not info["geladen"]:
            hinweis = (hinweis + " " if hinweis else "") + f"Die Reihe ist nicht geladen (python3 -m kitrader.historie laden {reihe})."
        return {"id": reihe, "titel": info["titel"], "eigene": bool(eigene), "geladen": info["geladen"], "erstes": info["erstes"],
                "letztes": info["letztes"], "hinweis": hinweis}

    def chart_ereignisse(self, q: dict[str, list[str]]) -> dict:
        """Ereignisse für Marker im Chart (?symbol=BTCUSDT&art=krypto|aktie&von=JJJJ-MM-TT&bis=JJJJ-MM-TT): nur Beginn auf Tag oder Monat genau,
        je Ereignis ein Rang (0 = wichtigste Art). Nur lesend."""
        from ...historie import ereignisse as _ereig
        arg = lambda k: ((q.get(k) or [""])[0]).strip()
        von, bis = arg("von") or None, arg("bis") or None
        for d in (von, bis):
            if d and not (len(d) == 10 and d[4] == "-" and d[7] == "-"):
                raise Anfragefehler(400, "von und bis als JJJJ-MM-TT angeben")
        art = "krypto" if arg("art") == "krypto" else "aktie"
        liste = _ereig.ereignisse(von=von, bis=bis)
        genau = [e for e in liste if e["genauigkeit"] in MARKER_GENAU]
        rang = {a: i for i, a in enumerate(ART_RANG)}
        aus = [{"id": e["id"], "beginn": e["beginn"], "ende": e.get("ende"), "genauigkeit": e["genauigkeit"], "art": e["art"],
                "region": e["region"], "titel": e["titel"], "quelle": {"name": e["quelle"]["name"], "url": e["quelle"].get("url")},
                "rang": rang.get(e["art"], len(rang))} for e in genau]
        return {"ereignisse": aus, "ausgelassen": len(liste) - len(genau), "arten": list(ART_RANG),
                "reihe": self.chart_reihe(arg("symbol"), art)}

    def chart_studie(self, q: dict[str, list[str]]) -> dict:
        """Ein Ereignis mit Beschreibung und Ereignisstudie für den Chart-Wert (?id=...&symbol=...&art=...). Ist die Reihe nicht geladen,
        steht das Ereignis trotzdem da und `studie` ist leer mit einer Erklärung in `fehler`."""
        from ...historie import ereignisse as _ereig
        arg = lambda k: ((q.get(k) or [""])[0]).strip()
        try:
            e = _ereig.ereignis(arg("id"))
        except KeyError:
            raise Anfragefehler(404, f"Ereignis {arg('id')!r} gibt es nicht") from None
        reihe = self.chart_reihe(arg("symbol"), "krypto" if arg("art") == "krypto" else "aktie")
        studie, fehler = None, ""
        try:
            studie = self.app.historie_studie(e["id"], reihe["id"])
        except Anfragefehler as f:
            fehler = f.text
        return {"ereignis": e, "reihe": reihe, "studie": studie, "fehler": fehler}

    # ---- Depot: Papierkonto des Autopiloten neben dem manuellen Konto der Charts (nur lesen)

    def depot(self) -> dict:
        """Papierkonto des Autopiloten (`papier.db` im kitrader-Datenordner): Konto, Positionen, offene Orders, abgeschlossene Trades
        und Ergebnis. Das manuelle Konto der Charts liefert `/api/papier`; die Seite rechnet beide zur Gesamtsumme zusammen.
        Liest nur, auch wenn die Betriebsart nicht Papier ist (dann wird das Papierkonto trotzdem gezeigt, nie ein anderes)."""
        u = (self.app.uebersicht() if self.einst["modus"] == "papier"
             else betrieb.uebersicht(self.einst, self.ordner, modus="papier", bauer=self.app.broker_bauer))
        k = u.get("konto")
        start = float(self.einst["papier"]["startguthaben"])
        trades, fehler = [], list(u.get("fehler") or [])
        db = self.ordner / "papier.db"
        if db.exists():
            try:
                con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=5)
                con.row_factory = sqlite3.Row
                try:
                    trades = [dict(z) for z in con.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 200")]
                finally:
                    con.close()
            except sqlite3.Error as e:
                fehler.append(f"Trades nicht lesbar: {e}")
        netto = sum(t["netto"] or 0.0 for t in trades)
        gewinner = sum(1 for t in trades if (t["netto"] or 0.0) > 0)
        unreal = sum(p["ergebnis"] or 0.0 for p in u.get("positionen") or [])
        return {"vorhanden": k is not None, "waehrung": self.einst["waehrung"], "modus": self.einst["modus"], "start": start,
                "konto": k, "positionen": u.get("positionen") or [], "orders": u.get("orders") or [],
                "trades": [{"nr": t["id"], "symbol": t["symbol"], "richtung": t["richtung"], "menge": t["menge"],
                            "einstieg": t["einstieg_preis"], "ausstieg": t["ausstieg_preis"], "eroeffnet": t["einstieg_zeit"],
                            "geschlossen": t["ausstieg_zeit"], "brutto": t["brutto"], "gebuehren": t["gebuehren"], "netto": t["netto"]}
                           for t in trades[:50]],
                "ergebnis": {"realisiert": netto, "unrealisiert": unreal, "gebuehren": sum(t["gebuehren"] or 0.0 for t in trades),
                             "gesamt": (k["gesamtwert"] - start) if k else 0.0},
                "statistik": {"trades": len(trades), "gewinner": gewinner, "verlierer": sum(1 for t in trades if (t["netto"] or 0.0) < 0),
                              "trefferquote": gewinner / len(trades) if trades else None},
                "fehler": fehler, "stand": u.get("stand"),
                "hinweis": "Papierkonto des Autopiloten (simuliert, kein echtes Geld), getrennt vom manuellen Konto der Charts."}

    def aehnlich(self, q: dict[str, list[str]]) -> dict:
        """Frühere, ähnliche Fälle zu einem frei gewählten Thema (?text=...&arten=konflikt,wahl): `muster.aehnliche` mit Mehrmarkt-Reaktion."""
        from ...oberflaeche import trends as _trends
        text = (q.get("text") or [""])[0].strip()
        if not text:
            raise Anfragefehler(400, "text fehlt: das Thema in einem Satz, z. B. text=Huthi greifen Schiffe im Roten Meer an")
        arten = [a for a in ((q.get("arten") or [""])[0]).split(",") if a.strip()]
        thema = {"titel": text[:300], "arten": arten, "punkte_6h": 1, "punkte_24h": 1, "verlage_6h": 0, "verlage_24h": 0}
        try:
            return _trends.aehnliche_teil(self.einst, self.ordner, {"themen": [thema]}, zu_utc(self.uhr()))
        except Exception as e:
            raise Anfragefehler(502, f"Nicht berechenbar ({type(e).__name__}: {e})") from None

    def weltlage(self) -> dict:
        """OSIRIS-Lage lesbar: Text plus die verdichteten Teile (Vorfälle, Konfliktzonen, Länderrisiko, Engpässe, Märkte ...)."""
        o = _osiris.Osiris(self.einst, self.ordner / "osiris.db")
        try:
            lage = o.lage()
        finally:
            o.beenden()
        alter = lage.alter() if lage.stand else None
        return {"vorhanden": lage.stand is not None, "text": lage.text, "stand": lage.stand.isoformat() if lage.stand else None,
                "vorher": lage.vorher.isoformat() if lage.vorher else None,
                "alter_min": None if alter is None else round(alter.total_seconds() / 60),
                "daten": _ohne_innere(lage.daten or {}),
                "hinweis": "Einstufungen (Stufen, Risiko, Score) sind die von OSIRIS, nicht von kitrader. Eine Meinung zum Nachdenken, "
                           "kein Handelssignal."}

    def autopilot(self) -> dict:
        s = self._steuerung() if self.einst["modus"] == "papier" or self.steuerung is not None else None
        stand = s.status() if s else {"laeuft": False, "status": "nicht gestartet", "fehler": "", "neustarts": 0}
        e = (s.einst if s else self.einst)
        r = e["risiko"]
        d = {**stand, "modus": e["modus"], "modus_text": betrieb.MODUS_TEXT[e["modus"]], "echtes_geld": e["modus"] == "echt",
             "steuerbar": e["modus"] == "papier", "strategie": e["strategie"]["name"], "symbole": list(e["autopilot"]["symbole"]),
             "takt": e["autopilot"].get("takt"), "ki_filter": bool(e["ki"].get("filter")),
             "grenzen_einstellung": {"tagesverlust_max": r["tagesverlust_max"], "tagesverlust_max_prozent": r["tagesverlust_max_prozent"],
                                     "position_max_prozent": r["position_max_prozent"], "risiko_pro_trade_prozent": r["risiko_pro_trade_prozent"],
                                     "positionen_max": r["positionen_max"], "orders_max_pro_tag": r.get("orders_max_pro_tag"),
                                     "stop_loss_pflicht": bool(r["stop_loss_pflicht"]), "short_erlaubt": bool(r["short_erlaubt"])},
             "uebersicht": self.app.uebersicht()}
        j = self.app.journal(AUTOPILOT_ENTSCHEIDUNGEN)
        d["entscheidungen"] = j["entscheidungen"]
        d["trefferquote_ki"] = j["trefferquote"]
        d["ereignisse"] = j["ereignisse"]
        return d

    # ---- POST

    def post(self, pfad: str, daten: dict):
        a = self.app
        if pfad == "notaus":
            return a.notaus()
        if pfad == "ki":
            return a.ki(daten)
        if pfad == "trends/abrufen":
            return a.trends_abrufen(daten)
        if pfad == "weltlage/abrufen":
            return a.weltlage_abrufen()
        if pfad == "autopilot/start":
            return self._autopilot_schalten(True)
        if pfad == "autopilot/stopp":
            return self._autopilot_schalten(False)
        raise Anfragefehler(404, "Unbekannte Adresse")

    def _autopilot_schalten(self, an: bool) -> dict:
        if self.einst["modus"] != "papier" and self.steuerung is None:
            raise Anfragefehler(403, f"Start und Stopp des Autopiloten gehen hier nur im Papierbetrieb. Betriebsart ist "
                                     f"{betrieb.MODUS_TEXT[self.einst['modus']]}: dann startet `python3 -m kitrader start --modus {self.einst['modus']}`.")
        s = self._steuerung()
        if s.modus != "papier":
            raise Anfragefehler(403, "Start und Stopp des Autopiloten gehen hier nur im Papierbetrieb.")
        try:
            erg = s.starten() if an else s.stoppen()
        except ValueError as e:
            raise Anfragefehler(409, str(e)) from None
        a = self.app
        a._cache = None
        return erg
