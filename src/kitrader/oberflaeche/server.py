"""Server der Bedienoberfläche: http.server, nur 127.0.0.1, eine Seite (seite.html), JSON-Schnittstelle.

Schutz vor fremden Webseiten und fremden Programmen:
* gebunden wird ausschließlich an 127.0.0.1, es gibt keine Einstellung, die das ändert;
* `Host` muss 127.0.0.1:<port> oder localhost:<port> sein (schützt vor DNS-Rebinding);
* ist ein `Origin` angegeben, muss er http://127.0.0.1:<port> oder http://localhost:<port> sein; bei POST ist er Pflicht;
* jede Anfrage an /api/ braucht den zufälligen Schlüssel (beim Start erzeugt, in die Seite eingebettet, im Kopf
  `X-Kitrader-Schluessel` mitgeschickt); es gibt keine CORS-Freigaben;
* strenge Kopfzeilen (CSP mit Nonce, kein Einbetten in Rahmen, kein Zwischenspeichern).

Der Reiter „Live“ (/api/live/...), „Trends“ (/api/trends) und die Morgenlage (/api/morgenlage) lesen nur: Kerzen und Kurs aus der
Kursquelle, eigene Trades aus Papierkonto bzw. Journal, die letzte KI-Einschätzung aus dem Journal, `morgenlage.json` aus dem Datenordner,
Welt-Rotation, Nachrichten-Radar und Muster aus den gespeicherten Daten.

Aktionen am Broker gibt es nur zwei, beide mit demselben Schutz (Schlüssel im Kopf, Origin-Prüfung, nur 127.0.0.1):
* der Not-Aus (Orders stornieren, die Positionen der eigenen Symbole glattstellen; fremde bleiben unberührt); er lässt sich hier nicht
  aufheben, sondern nur per Kommandozeile;
* das Order-Fenster (/api/order/...): manuelle Orders **nur im Papierbetrieb**, durch dieselbe Risikoprüfung wie beim Autopiloten
  (manuell.py). In `demo` und `echt` ist es gesperrt (403). Echte Orders sendet die Oberfläche nie.
"""
from __future__ import annotations

import hmac
import json
import logging
import math
import re
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from .. import betrieb
from .. import einstellungen as _einst
from .. import ki as _ki
from .. import manuell as _manuell
from .. import nachrichten as _nachrichten
from .. import osiris as _osiris
from .. import symbole as _symbole
from .. import welt as _welt
from . import live as _live
from . import trends as _trends
from ..historie import ereignisse as _ereignisse
from ..historie.reihen import REIHEN
from ..historie.speicher import Speicher
from ..historie.studie import StudienFehler, studie as _studie
from ..broker.basis import BrokerFehler, symbol_schluessel
from ..hilfen import jetzt_utc, takt_dauer, zu_utc
from ..journal import Journal
from ..kursquelle import quelle_aus_einstellungen

log = logging.getLogger("kitrader.oberflaeche")

HOST = "127.0.0.1"
KOPF_SCHLUESSEL = "X-Kitrader-Schluessel"
SEITE = Path(__file__).with_name("seite.html")
MAX_KOERPER = 20_000
WERT_FORM = re.compile(r"^[A-Za-z0-9._:/\-]{1,24}$")
TRENDS_GUELTIG_S = 20.0              # Trends werden so lange zwischengespeichert (die Ähnlichkeitssuche rechnet ein paar Sekunden)
DATUM_FORM = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
UEBERSICHT_GUELTIG_S = 3.0
LIVE_GUELTIG_S = 8.0                 # Kerzen und Kurs werden so lange zwischengespeichert (mehrere Fenster schonen die Kursquelle)
LIVE_AKTUALISIERUNG_S = 30


class Anfragefehler(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status, self.text = status, text


def _sauber(x):
    """Nicht darstellbare Zahlen (NaN, unendlich) durch None ersetzen, damit das JSON im Browser lesbar bleibt."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _sauber(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_sauber(v) for v in x]
    return x


def _json_bytes(x) -> bytes:
    return json.dumps(_sauber(x), ensure_ascii=False, default=str, allow_nan=False).encode("utf-8")


def kuerzen(text: str, maximal: int) -> str:
    """Text auf höchstens `maximal` Zeichen kürzen und das Abgeschnittene mit „…“ kenntlich machen (kein halbes Trennzeichen)."""
    if len(text) <= maximal:
        return text
    return text[:maximal - 1].rstrip(" |") + "…"


def ausduennen(werte: list[tuple[str, float]], maximal: int) -> list[tuple[str, float]]:
    """Höchstens ~`maximal` Punkte für den Linienchart: je Abschnitt Tief und Hoch behalten (Einbrüche bleiben sichtbar),
    erster und letzter Punkt immer."""
    n = len(werte)
    if n <= maximal:
        return list(werte)
    abschnitte = max(1, maximal // 2 - 1)
    schritt = n / abschnitte
    behalten = {0, n - 1}
    for i in range(abschnitte):
        a, b = int(i * schritt), min(n, int((i + 1) * schritt))
        if b <= a:
            continue
        teil = range(a, b)
        behalten.add(min(teil, key=lambda k: werte[k][1]))
        behalten.add(max(teil, key=lambda k: werte[k][1]))
    return [werte[k] for k in sorted(behalten)]


class Oberflaeche:
    """Zustand und Logik der Oberfläche; `bauen()` macht daraus einen laufbereiten Server."""

    def __init__(self, einst: dict, ordner: Path, schluessel: str | None = None,
                 broker_bauer: Callable | None = None, kursquelle: Callable | None = None, ki_treiber=None,
                 uhr: Callable | None = None):
        self.einst = _einst.mit_standard(einst)
        self.ordner = Path(ordner)
        self.schluessel = schluessel or secrets.token_urlsafe(32)
        self.broker_bauer = broker_bauer           # ersetzt den Broker (nur Tests)
        self.kursquelle = kursquelle or (lambda: quelle_aus_einstellungen(self.einst))
        self.ki_treiber = ki_treiber               # ersetzt den Treiber aus den Einstellungen (nur Tests)
        self.uhr = uhr or jetzt_utc                # Uhr für Risikoprüfung und Trends (Tests setzen eine feste)
        self.port = 0
        self._seite = SEITE.read_text(encoding="utf-8")
        self._sperre_uebersicht = threading.Lock()
        self._cache: tuple[float, dict] | None = None
        self._notaus_sperre = threading.Lock()
        self._ki_sperre = threading.Lock()
        self._osiris_sperre = threading.Lock()
        self._live_sperre = threading.Lock()
        self._order_sperre = threading.Lock()
        self._trends_sperre = threading.Lock()
        self._trends_abruf_sperre = threading.Lock()
        self._trends_cache: tuple[float, dict] | None = None
        self._paarliste: _symbole.PaarVerzeichnis | None = None
        self._live_quelle = None
        self._live_cache: dict[tuple, tuple[float, list, dict | None, str]] = {}

    # ---- Seite und Prüfungen

    def erlaubte_hosts(self) -> set[str]:
        return {f"{HOST}:{self.port}", f"localhost:{self.port}"}

    def erlaubte_origins(self) -> set[str]:
        return {f"http://{HOST}:{self.port}", f"http://localhost:{self.port}"}

    def seite(self, nonce: str) -> bytes:
        return self._seite.replace("__SCHLUESSEL__", self.schluessel).replace("__NONCE__", nonce).encode("utf-8")

    def schluessel_ok(self, gegeben: str | None) -> bool:
        return bool(gegeben) and hmac.compare_digest(gegeben.encode("utf-8"), self.schluessel.encode("utf-8"))

    # ---- GET

    def get(self, pfad: str, q: dict[str, list[str]]):
        def param(name: str, standard: str = "") -> str:
            return (q.get(name) or [standard])[0]

        if pfad == "/api/info":
            return {"modus": self.einst["modus"], "modus_text": betrieb.MODUS_TEXT[self.einst["modus"]],
                    "ki_treiber": self.einst["ki"]["treiber"], "ki_treiber_liste": list(_einst.KI_TREIBER),
                    "waehrung": self.einst["waehrung"]}
        if pfad == "/api/uebersicht":
            return self.uebersicht()
        if pfad == "/api/symbol":
            try:                                    # ein Tippfehler ist kein Fehler der Anfrage: 200 mit Text und Vorschlägen
                return {"erkannt": True, **self.symbol_erkennen(param("q"))}
            except Anfragefehler as e:
                if e.status != 400:
                    raise
                return {"erkannt": False, "fehler": e.text, "eingabe": param("q")}
        if pfad == "/api/trends":
            return self.trends()
        if pfad == "/api/live/symbole":
            return self.live_symbole()
        if pfad == "/api/live/chart":
            return self.live_chart(param("symbol"), param("takt") or self.einst["oberflaeche"]["live_takt"],
                                   self._zahl(param("anzahl") or str(self.einst["oberflaeche"]["live_kerzen"]), 20, 300))
        if pfad == "/api/morgenlage":
            return _live.morgenlage(self.ordner)
        if pfad == "/api/journal":
            return self.journal(self._zahl(param("anzahl", "50"), 1, 500))
        if pfad == "/api/weltlage":
            return self.weltlage()
        if pfad == "/api/historie/reihen":
            return self.historie_reihen()
        if pfad == "/api/historie/reihe":
            return self.historie_reihe(param("id"), param("von"), param("bis"), self._zahl(param("max", "900"), 50, 5000))
        if pfad == "/api/historie/ereignisse":
            return self.historie_ereignisse()
        if pfad == "/api/historie/studie":
            return self.historie_studie(param("id"), param("reihe") or None)
        raise Anfragefehler(404, "Unbekannte Adresse")

    @staticmethod
    def _zahl(text: str, kleinst: int, groesst: int) -> int:
        try:
            return max(kleinst, min(groesst, int(text)))
        except ValueError:
            raise Anfragefehler(400, "Zahl erwartet") from None

    def uebersicht(self) -> dict:
        with self._sperre_uebersicht:
            if self._cache and time.monotonic() - self._cache[0] < UEBERSICHT_GUELTIG_S:
                return self._cache[1]
            d = betrieb.uebersicht(self.einst, self.ordner, bauer=self.broker_bauer)
            self._cache = (time.monotonic(), d)
            return d

    # ---- Live

    def _live_liste(self) -> list[str]:
        """Symbole des Autopiloten und der Watchlist ([watchlist] werte), ohne Doppelte (BTC/EUR und XBTEUR sind dasselbe)."""
        liste: list[str] = []
        gesehen: set[str] = set()
        for s in [*self.einst["autopilot"]["symbole"], *self.einst["watchlist"]["werte"]]:
            if s.strip() and symbol_schluessel(s) not in gesehen:
                gesehen.add(symbol_schluessel(s))
                liste.append(s.strip())
        return liste

    def live_symbole(self) -> dict:
        o = self.einst["oberflaeche"]
        return {"symbole": self._live_liste(), "takte": list(_einst.LIVE_TAKTE), "takt": o["live_takt"], "kerzen": o["live_kerzen"],
                "aktualisierung_s": LIVE_AKTUALISIERUNG_S, "modus": self.einst["modus"],
                "kursquelle": self.einst["kursquelle"], "order": self.order_info()}

    def order_info(self) -> dict:
        """Was das Order-Fenster wissen muss: ob es erlaubt ist und welche Grenzen die Risikoprüfung setzt."""
        ok, hinweis = _manuell.erlaubt(self.einst)
        r = self.einst["risiko"]
        return {"erlaubt": ok, "hinweis": hinweis, "short_erlaubt": bool(r["short_erlaubt"]), "stop_pflicht": bool(r["stop_loss_pflicht"]),
                "risiko_pro_trade_prozent": r["risiko_pro_trade_prozent"], "position_max_prozent": r["position_max_prozent"],
                "positionen_max": r["positionen_max"], "waehrung": self.einst["waehrung"]}

    def _live_kerzen_und_kurs(self, symbol: str, takt: str, anzahl: int) -> tuple[list, dict | None, str]:
        """Kerzen und Kurs aus der Kursquelle (kurz zwischengespeichert, nacheinander abgerufen). Fehlt der Kurs, bleibt der Chart."""
        schluessel = (symbol_schluessel(symbol), takt, anzahl)
        with self._live_sperre:
            gespeichert = self._live_cache.get(schluessel)
            if gespeichert and time.monotonic() - gespeichert[0] < LIVE_GUELTIG_S:
                return gespeichert[1:]
            try:
                if self._live_quelle is None:
                    self._live_quelle = self.kursquelle()
                kerzen = self._live_quelle.kerzen(symbol, takt, anzahl)
            except Exception as e:
                self._live_quelle = None                      # beim nächsten Mal neu verbinden
                raise Anfragefehler(502, f"Keine Kerzen für {symbol} abrufbar ({e}).") from None
            fehler = ""
            try:
                kurs = _live.kurs_dict(self._live_quelle.kurs(symbol))
            except Exception as e:
                kurs, fehler = None, f"Kurs nicht abrufbar ({e})"
            ergebnis = (_live.kerzen_zeilen(kerzen)[-anzahl:], kurs, fehler)
            self._live_cache[schluessel] = (time.monotonic(), *ergebnis)
            return ergebnis

    def live_chart(self, symbol: str, takt: str, anzahl: int) -> dict:
        """Alles für den Chart eines Symbols: Kerzen, Kurs, eigene Trades (Einstieg, Ausstieg, Stop, Ziel), letzte KI-Einschätzung."""
        erlaubt = {symbol_schluessel(s): s for s in self._live_liste()}
        if not WERT_FORM.match(symbol) or symbol_schluessel(symbol) not in erlaubt:
            raise Anfragefehler(400, "Unbekanntes Symbol (nur autopilot.symbole und watchlist.werte).")
        if takt not in _einst.LIVE_TAKTE:
            raise Anfragefehler(400, f"Takt: {', '.join(_einst.LIVE_TAKTE)}")
        symbol = erlaubt[symbol_schluessel(symbol)]
        kerzen, kurs, kurs_fehler = self._live_kerzen_und_kurs(symbol, takt, anzahl)
        modus = self.einst["modus"]
        d = {"symbol": symbol, "takt": takt, "takt_s": int(takt_dauer(takt).total_seconds()), "modus": modus, "kerzen": kerzen,
             "kurs": kurs, "kurs_fehler": kurs_fehler, "stand": int(time.time()), "trades": [], "einschaetzung": None,
             "fehler": []}
        try:
            d["trades"] = _live.trades(self.ordner, self.einst, modus, symbol)
        except Exception as e:
            d["fehler"].append(f"Trades nicht lesbar: {e}")
        try:
            j = Journal(self.ordner / "journal.db", self.einst)
            try:
                d["einschaetzung"] = _live.ki_einschaetzung(j, symbol)
            finally:
                j.beenden()
        except Exception as e:
            d["fehler"].append(f"Journal nicht lesbar: {e}")
        return d

    def journal(self, anzahl: int) -> dict:
        j = Journal(self.ordner / "journal.db", self.einst)
        try:
            zeilen = []
            for e in reversed(j.entscheidungen(anzahl)):
                zeilen.append({
                    "id": e["id"], "zeit": e["zeit"], "modus": e["modus"], "symbol": e["symbol"], "strategie": e["strategie"],
                    "signal": json.loads(e["signal"]) if e["signal"] else None,
                    "order": json.loads(e["ord"]) if e["ord"] else None,
                    "pruefung": None if e["pruefung_ok"] is None else bool(e["pruefung_ok"]),
                    "gruende": json.loads(e["gruende"]) if e["gruende"] else [],
                    "ausfuehrung": json.loads(e["ausfuehrung"]) if e["ausfuehrung"] else None,
                    "ergebnis": e["ergebnis"], "notiz": e["notiz"] or ""})
            schwellen = self.einst["journal"]["schwellen"]
            quote = [{"horizont": h, "schwelle": schwellen.get(h), **q} for h, q in j.trefferquote().items()]
            einschaetzungen = [{k: e[k] for k in ("id", "zeit", "wert", "richtung", "horizont", "status", "treffer",
                                                  "aenderung_prozent", "faellig")} | {"begruendung": kuerzen(e["begruendung"] or "", 400)}
                               for e in reversed(j.einschaetzungen()[-30:])]
            ereignisse = [dict(e) for e in reversed(j.ereignisse(15))]
        finally:
            j.beenden()
        return {"entscheidungen": zeilen, "trefferquote": quote, "einschaetzungen": einschaetzungen, "ereignisse": ereignisse}

    def weltlage(self) -> dict:
        o = _osiris.Osiris(self.einst, self.ordner / "osiris.db")
        try:
            lage = o.lage()
        finally:
            o.beenden()
        alter = lage.alter() if lage.stand else None
        return {"text": lage.text, "stand": lage.stand.isoformat() if lage.stand else None,
                "vorher": lage.vorher.isoformat() if lage.vorher else None,
                "alter_min": None if alter is None else round(alter.total_seconds() / 60)}

    def historie_reihen(self) -> dict:
        with Speicher(self.ordner) as sp:
            stand = {s["reihe"]: s for s in sp.stand()}
        reihen = []
        for r in REIHEN.values():
            s = stand.get(r.id)
            reihen.append({"id": r.id, "titel": r.titel, "einheit": r.einheit, "takt": r.takt, "quelle": r.quelle,
                           "geladen": bool(s), "anzahl": s["anzahl"] if s else 0, "erstes": s["erstes"] if s else None,
                           "letztes": s["letztes"] if s else None})
        return {"reihen": reihen}

    def historie_reihe(self, reihe: str, von: str, bis: str, maximal: int) -> dict:
        if reihe not in REIHEN:
            raise Anfragefehler(400, f"Unbekannte Reihe {reihe!r}")
        for d in (von, bis):
            if d and not DATUM_FORM.match(d):
                raise Anfragefehler(400, "Datum als JJJJ, JJJJ-MM oder JJJJ-MM-TT angeben")
        with Speicher(self.ordner) as sp:
            werte = sp.werte(reihe, von or None, bis or None)
        r = REIHEN[reihe]
        return {"id": reihe, "titel": r.titel, "einheit": r.einheit, "takt": r.takt, "anzahl_gesamt": len(werte),
                "punkte": ausduennen(werte, maximal), "ausgeduennt": len(werte) > maximal}

    def historie_ereignisse(self) -> dict:
        liste = [{k: e.get(k) for k in ("id", "beginn", "ende", "genauigkeit", "art", "region", "titel")}
                 for e in _ereignisse.alle()]
        return {"ereignisse": liste, "arten": sorted({e["art"] for e in liste})}

    def historie_studie(self, ereignis_id: str, reihe: str | None) -> dict:
        if reihe is not None and reihe not in REIHEN:
            raise Anfragefehler(400, f"Unbekannte Reihe {reihe!r}")
        try:
            with Speicher(self.ordner) as sp:
                s = _studie(ereignis_id, reihe, speicher=sp)
        except KeyError:
            raise Anfragefehler(404, f"Ereignis {ereignis_id!r} gibt es nicht") from None
        except StudienFehler as e:
            raise Anfragefehler(409, str(e)) from None
        s["horizonte"] = {str(h): v for h, v in s["horizonte"].items()}
        s["titel_reihe"] = REIHEN[s["reihe"]].titel if s["reihe"] in REIHEN else s["reihe"]
        return s

    # ---- POST

    def post(self, pfad: str, daten: dict):
        if pfad == "/api/notaus":
            return self.notaus()
        if pfad == "/api/weltlage/abrufen":
            return self.weltlage_abrufen()
        if pfad == "/api/ki":
            return self.ki(daten)
        if pfad == "/api/order/vorschau":
            return self._order("vorschau", daten)
        if pfad == "/api/order/senden":
            return self._order("senden", daten)
        if pfad == "/api/order/schliessen":
            return self._order("schliessen", daten)
        if pfad == "/api/trends/abrufen":
            return self.trends_abrufen(daten)
        raise Anfragefehler(404, "Unbekannte Adresse")

    # ---- Order-Fenster (nur Papierbetrieb, durch dieselbe Risikoprüfung wie der Autopilot)

    def _order(self, art: str, daten: dict) -> dict:
        ok, hinweis = _manuell.erlaubt(self.einst)
        if not ok:
            raise Anfragefehler(403, hinweis)
        if not self._order_sperre.acquire(blocking=False):
            raise Anfragefehler(409, "Es läuft schon eine Order. Bitte einen Moment warten.")
        try:
            with betrieb.broker_oeffnen("papier", self.einst, self.ordner, self.broker_bauer) as broker:
                journal = Journal(self.ordner / "journal.db", self.einst)
                try:
                    if art == "vorschau":
                        return _manuell.vorschau(broker, journal, self.einst, self.ordner, daten, self.uhr)
                    if art == "senden":
                        return _manuell.senden(broker, journal, self.einst, self.ordner, daten, self.uhr)
                    return _manuell.schliessen(broker, journal, self.einst, self.ordner, str(daten.get("symbol", "")), self.uhr)
                except _manuell.ManuellFehler as e:
                    raise Anfragefehler(e.status, str(e)) from None
                except BrokerFehler as e:
                    raise Anfragefehler(502, str(e)) from None
                finally:
                    journal.beenden()
        finally:
            self._order_sperre.release()
            self._cache = None

    # ---- Trends

    def trends(self) -> dict:
        with self._trends_sperre:
            if self._trends_cache and time.monotonic() - self._trends_cache[0] < TRENDS_GUELTIG_S:
                return self._trends_cache[1]
            d = _trends.trends(self.einst, self.ordner, [s for s in self.einst["watchlist"]["werte"] if s.strip()], zu_utc(self.uhr()))
            self._trends_cache = (time.monotonic(), d)
            return d

    def trends_abrufen(self, daten: dict) -> dict:
        """Welt-Rotation bzw. Nachrichten-Radar neu holen, höflich: nicht öfter als der jeweilige Mindestabstand (mindestabstand_s)."""
        teil = str(daten.get("teil", ""))
        if teil not in ("welt", "nachrichten"):
            raise Anfragefehler(400, "teil: 'welt' oder 'nachrichten'")
        if not self._trends_abruf_sperre.acquire(blocking=False):
            raise Anfragefehler(409, "Es wird schon abgerufen.")
        try:
            try:
                if teil == "welt":
                    w = _welt.Welt(self.einst, self.ordner / "welt.db", historie_pfad=self.ordner / "historie.db")
                    try:
                        erg, mindest = w.abrufen(erzwingen=False), w.cfg["mindestabstand_s"]
                    finally:
                        w.beenden()
                    fehler = [f"{k}: {v}" for k, v in erg["quellen"].items() if erg["neu"] and not str(v).startswith("ok")]
                    summe = f"{len(erg['quellen']) - len(fehler)} von {len(erg['quellen'])} Quellen ok" if erg.get("neu") else ""
                else:
                    n = _nachrichten.Nachrichten(self.einst, self.ordner / "nachrichten.db")
                    try:
                        erg, mindest = n.abrufen(erzwingen=False), n.cfg["mindestabstand_s"]
                    finally:
                        n.beenden()
                    fehler = [f"{k}: {v['fehler']}" for k, v in erg["quellen"].items() if erg["neu"] and v.get("fehler")]
                    summe = f"{erg['neue_meldungen']} neue Meldungen, {len(erg['quellen']) - len(fehler)} von {len(erg['quellen'])} Feeds ok" \
                        if erg.get("neu") else ""
            except (_welt.WeltFehler, _nachrichten.NachrichtenFehler) as e:
                raise Anfragefehler(502, str(e)) from None
        finally:
            self._trends_abruf_sperre.release()
        self._trends_cache = None
        hinweis = "" if erg.get("neu") else f"Der letzte Abruf ist jünger als {mindest} s und wird weiterverwendet."
        return {"teil": teil, "neu": bool(erg.get("neu")), "hinweis": hinweis, "zusammenfassung": summe, "fehler": fehler[:6]}

    def notaus(self) -> dict:
        if not self._notaus_sperre.acquire(blocking=False):
            raise Anfragefehler(409, "Ein Not-Aus läuft bereits.")
        try:
            ergebnis = betrieb.notaus_ausloesen(self.einst, self.ordner, quelle="Bedienoberfläche",
                                                bauer=self.broker_bauer, melden=True)
        finally:
            self._notaus_sperre.release()
        self._cache = None
        return ergebnis

    def weltlage_abrufen(self) -> dict:
        if not self._osiris_sperre.acquire(blocking=False):
            raise Anfragefehler(409, "Es wird schon abgerufen.")
        try:
            o = _osiris.Osiris(self.einst, self.ordner / "osiris.db")
            try:
                erg = o.abrufen(erzwingen=False)       # höflich: nicht öfter als osiris.mindestabstand_s
            except _osiris.OsirisFehler as e:
                raise Anfragefehler(502, str(e)) from None
            finally:
                o.beenden()
        finally:
            self._osiris_sperre.release()
        mindest = self.einst["osiris"]["mindestabstand_s"]
        hinweis = "" if erg["neu"] else f"Der letzte Schnappschuss ist jünger als {mindest} s und wird weiterverwendet."
        return {"neu": erg["neu"], "hinweis": hinweis,
                "feeds": [{"name": n, "status": f["status"], "bytes": f["bytes"], "fehler": f["fehler"]}
                          for n, f in erg["feeds"].items()]}

    def _paare(self) -> _symbole.PaarVerzeichnis:
        if self._paarliste is None:
            self._paarliste = _symbole.PaarVerzeichnis(datei=self.ordner / "kraken_paare.json")
        return self._paarliste

    def symbol_erkennen(self, eingabe: str) -> dict:
        """Eingabe (BTC, Bitcoin, XBT/EUR ...) -> Symbol wie BTC/EUR. Ist Kraken die Kursquelle, wird gegen die Paarliste von Kraken
        geprüft (Altname, wsname, Basis und Gegenwährung); unbekannt ergibt 400 mit Vorschlägen („Meintest du …?“)."""
        eingabe = str(eingabe or "")
        try:
            symbol = _symbole.kanonisch(eingabe, self.einst)
            z = _symbole.zerlegen(eingabe, _symbole.gegenwaehrung(self.einst))
        except _symbole.SymbolFehler as e:
            raise Anfragefehler(400, str(e)) from None
        paar, hinweis = None, ""
        if _symbole.nutzt_kraken(self.einst):
            try:
                paar = self._paare().finden(eingabe, _symbole.gegenwaehrung(self.einst))
                symbol = paar.symbol
            except _symbole.AbrufFehler as e:
                hinweis = f"{e} Das Symbol ist nicht gegen Kraken geprüft."
            except _symbole.SymbolFehler as e:
                raise Anfragefehler(400, str(e)) from None
        basis = paar.basis if paar else (symbol.split("/")[0] if "/" in symbol else symbol)
        return {"symbol": symbol, "name": _symbole.klartext(basis) if z.basis in _symbole.COINS or paar else symbol, "eingabe": eingabe.strip(),
                "geprueft": paar is not None, "hinweis": hinweis,
                "paar": None if paar is None else {"altname": paar.altname, "wsname": paar.wsname, "schluessel": paar.schluessel}}

    def ki(self, daten: dict) -> dict:
        wert = str(daten.get("wert", "")).strip()
        if not wert:
            raise Anfragefehler(400, "Bitte einen Wert eingeben, z. B. BTC, Bitcoin, ETH/EUR oder AAPL.")
        wert = self.symbol_erkennen(wert)["symbol"]
        modus = daten.get("treiber") or "einstellung"
        if modus not in ("einstellung", "aus"):
            raise Anfragefehler(400, "Treiber: 'einstellung' (wie in den Einstellungen) oder 'aus' (nur Kontext zeigen)")
        treiber = "aus" if modus == "aus" else (self.ki_treiber or None)
        if not self._ki_sperre.acquire(blocking=False):
            raise Anfragefehler(409, "Es läuft schon eine Einschätzung. Das kann einige Minuten dauern.")
        try:
            try:
                kerzen = self.kursquelle().kerzen(wert, "1d", 250)
            except Exception as e:
                raise Anfragefehler(502, f"Keine Kerzen für {wert} abrufbar ({e}).") from None
            j = Journal(self.ordner / "journal.db", self.einst)
            try:
                e = _ki.einschaetzen(wert, kerzen, treiber=treiber, einstellungen=self.einst, journal=j, ordner=self.ordner)
            except _ki.KiFehler as fehler:
                raise Anfragefehler(502, str(fehler)) from None
            finally:
                j.beenden()
        finally:
            self._ki_sperre.release()
        return {"wert": e.wert, "preis": e.preis, "zeit": zu_utc(e.zeit).isoformat(), "treiber": e.treiber,
                "text": _ki.als_text(e), "kontext": e.kontext, "warnungen": e.warnungen,
                "prognosen": {n: {"horizont": p.horizont, "richtung": p.richtung, "wahrscheinlichkeit": p.wahrscheinlichkeit,
                                  "begruendung": p.begruendung, "gegenargumente": p.gegenargumente,
                                  "was_widerlegt": p.was_widerlegt, "journal_id": p.journal_id}
                              for n, p in e.prognosen.items()}}


# ---------------------------------------------------------------- HTTP

def _handler(app: Oberflaeche):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kitrader"
        sys_version = ""

        def version_string(self) -> str:
            return "kitrader"

        def log_message(self, format, *args):               # kein Zugriffsprotokoll (Adressen enthalten Eingaben)
            pass

        # ---- Antworten

        def _senden(self, status: int, koerper: bytes, typ: str = "application/json; charset=utf-8",
                    csp: str | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", typ)
            self.send_header("Content-Length", str(len(koerper)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            if csp:
                self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(koerper)

        def _fehler(self, status: int, text: str) -> None:
            self._senden(status, _json_bytes({"fehler": text}))

        # ---- Prüfungen

        def _pruefen(self, schreibend: bool, api: bool) -> bool:
            """True, wenn die Anfrage durch alle Prüfungen kommt; sonst ist die Antwort (403) schon gesendet."""
            if (self.headers.get("Host") or "").lower() not in app.erlaubte_hosts():
                self._fehler(403, "Host nicht erlaubt")
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin not in app.erlaubte_origins():
                self._fehler(403, "Origin nicht erlaubt")
                return False
            if schreibend and origin is None:
                self._fehler(403, "Origin fehlt")
                return False
            if api:
                if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                    self._fehler(403, "Anfrage von einer fremden Seite")
                    return False
                if not app.schluessel_ok(self.headers.get(KOPF_SCHLUESSEL)):
                    self._fehler(403, "Schlüssel fehlt oder ist falsch (Seite neu laden)")
                    return False
            return True

        def _ausfuehren(self, aktion: Callable[[], dict]) -> None:
            try:
                self._senden(200, _json_bytes(aktion()))
            except Anfragefehler as e:
                self._fehler(e.status, e.text)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass                                        # der Browser hat die Seite verlassen oder neu geladen: nichts zu melden
            except Exception as e:
                log.error("Fehler bei %s: %s", self.path.split("?")[0], traceback.format_exc())
                self._fehler(500, f"Interner Fehler: {type(e).__name__}: {e}")

        # ---- Methoden

        def do_GET(self) -> None:
            teile = urlsplit(self.path)
            api = teile.path.startswith("/api/")
            if not self._pruefen(schreibend=False, api=api):
                return
            if teile.path in ("/", "/index.html"):
                nonce = secrets.token_urlsafe(16)
                csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; img-src 'self' data:; "
                       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
                self._senden(200, app.seite(nonce), "text/html; charset=utf-8", csp)
            elif api:
                self._ausfuehren(lambda: app.get(teile.path, parse_qs(teile.query)))
            else:
                self._fehler(404, "Nicht gefunden")

        do_HEAD = do_GET

        def do_POST(self) -> None:
            pfad = urlsplit(self.path).path
            if not self._pruefen(schreibend=True, api=True):
                return
            if not pfad.startswith("/api/"):
                self._fehler(404, "Nicht gefunden")
                return
            if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                self._fehler(415, "Content-Type application/json erwartet")
                return
            try:
                laenge = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._fehler(400, "Content-Length ungültig")
                return
            if laenge < 0 or laenge > MAX_KOERPER:
                self._fehler(413, "Anfrage zu groß")
                return
            try:
                daten = json.loads(self.rfile.read(laenge).decode("utf-8") or "{}")
                if not isinstance(daten, dict):
                    raise ValueError
            except ValueError:
                self._fehler(400, "JSON-Objekt erwartet")
                return
            self._ausfuehren(lambda: app.post(pfad, daten))

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return                                          # Browser weg: kein Traceback im Terminal (z. B. von `start`)
        super().handle_error(request, client_address)


def bauen(einst: dict, ordner: Path, port: int | None = None, **kwargs) -> tuple[Oberflaeche, ThreadingHTTPServer]:
    """Baut Oberfläche und Server (noch nicht gestartet). port=0 wählt einen freien Port. Wirft OSError, wenn der Port belegt ist."""
    app = Oberflaeche(einst, ordner, **kwargs)
    port = app.einst["oberflaeche"]["port"] if port is None else port
    server = _Server((HOST, port), _handler(app))
    app.port = server.server_address[1]
    return app, server


def laufen(einst: dict, ordner: Path, port: int | None = None, browser: bool | None = None,
           ausgabe: Callable[[str], None] = print) -> int:
    """Startet die Oberfläche und läuft bis Strg+C."""
    try:
        app, server = bauen(einst, ordner, port)
    except OSError as e:
        ausgabe(f"Fehler: Der Port {app_port(einst, port)} ist nicht frei ({e}). Läuft die Oberfläche schon? "
                "Anderen Port wählen: python3 -m kitrader oberflaeche --port 8851")
        return 2
    url = f"http://{HOST}:{app.port}/"
    modus = betrieb.MODUS_TEXT[app.einst["modus"]]
    ausgabe(f"kitrader Bedienoberfläche läuft: {url}   (Betriebsart laut Einstellungen: {modus})\n"
            "Nur auf diesem Rechner erreichbar. Beenden mit Strg+C. Von Hand lässt sich nur im Papierbetrieb eine Order setzen "
            "(Reiter Live, mit Risikoprüfung); der Not-Aus-Knopf storniert die Orders und stellt die eigenen Positionen glatt (fremde bleiben unberührt).")
    if app.einst["modus"] == "echt":
        ausgabe("!!! Betriebsart ECHT: Konto und Not-Aus beziehen sich auf ECHTES GELD. !!!")
    if (app.einst["oberflaeche"]["browser_oeffnen"] if browser is None else browser):
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        ausgabe("\nOberfläche beendet.")
    finally:
        server.server_close()
    return 0


def app_port(einst: dict, port: int | None) -> int:
    return einst["oberflaeche"]["port"] if port is None else port
