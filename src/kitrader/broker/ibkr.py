"""Interactive Brokers über die Client Portal Web API (lokales Gateway).

Der Nutzer startet das Client Portal Gateway selbst und meldet sich im Browser an
(https://localhost:5000). kitrader spricht nur mit diesem lokalen Gateway, nie mit Zugangsdaten.
Nur Standardbibliothek. Anleitung und Quellen: BROKER.md.

Wichtig:
- Das Gateway hat ein selbstsigniertes Zertifikat. Die Prüfung wird NUR für localhost / 127.0.0.1 / ::1
  abgeschaltet, für jede andere Adresse bleibt sie an.
- Unbekannte Rückfragen von IBKR werden nie automatisch bestätigt, nur eine kleine Liste harmloser Hinweise.
- Solange `echtgeld_erlaubt` nicht ausdrücklich gesetzt ist, sendet dieser Broker nur an Demokonten ("DU...").
"""
from __future__ import annotations

import json
import math
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from .basis import Broker, BrokerFehler, Kerze, Konto, Kurs, Order, OrderStand, Position

STANDARD_URL = "https://localhost:5000/v1/api"
LOKALE_HOSTS = ("localhost", "127.0.0.1", "::1")

# Rückfragen (messageIds), die nur vor Risiken der Ordertypen warnen, die wir bewusst benutzen:
# o10151 Marktorder-Risiko, o10152 Stop-Order-Risiko, o10331 Stop-Order-Hinweis, o10153 Pflicht-Preisgrenze.
# Alles andere (Preisabweichung o163, keine Marktdaten o354, Größen-/Wertgrenzen o383/o451, Tickgröße o382 ...)
# sind Schutzmeldungen von IBKR und führen zu einem Fehler statt zur Bestätigung.
HARMLOSE_HINWEISE = frozenset({"o10151", "o10152", "o10153", "o10331"})

US_BOERSEN = frozenset({"NYSE", "NASDAQ", "NASDAQ.NMS", "ARCA", "NYSE ARCA", "AMEX", "BATS", "IEX"})

# Takt -> (Minuten, Balkenbreite für IBKR)
_TAKTE = {"1m": (1, "1min"), "5m": (5, "5min"), "15m": (15, "15min"), "1h": (60, "1h"), "1d": (1440, "1d")}

# Mindestabstände je Pfad in Sekunden laut IBKR-Seite "Pacing Limitations"; global 10 Anfragen/s
_MINDESTABSTAND = {
    "/portfolio/accounts": 5.0,
    "/iserver/account/orders": 5.0,
    "/iserver/account/pnl/partitioned": 5.0,
    "/tickle": 1.0,
}
_GLOBALER_ABSTAND = 0.11

# Ordertypen laut Web-API-Referenz (Stop-Limit heißt dort STOP_LIMIT)
_ART = {"markt": "MKT", "limit": "LMT", "stop": "STP", "stop_limit": "STOP_LIMIT"}

_STATUS = {
    "Submitted": "offen", "PreSubmitted": "offen", "PendingSubmit": "offen", "PendingCancel": "offen",
    "Filled": "ausgefuehrt",
    "Cancelled": "storniert", "ApiCancelled": "storniert",
    "Inactive": "abgelehnt", "Rejected": "abgelehnt",
}

_HILFE_GATEWAY = (
    "Das IBKR Client Portal Gateway ist nicht erreichbar ({url}). So geht es: "
    "1) Gateway starten (Ordner clientportal.gw: 'bin/run.sh root/conf.yaml', unter Windows 'bin\\run.bat root\\conf.yaml'), "
    "2) im Browser https://localhost:5000 öffnen und mit dem Demo-Zugang anmelden, "
    "3) danach erneut versuchen. Einzelheiten: src/kitrader/broker/BROKER.md. ({grund})"
)
_HILFE_LOGIN = (
    "IBKR ist nicht angemeldet (oder die Sitzung ist abgelaufen). Bitte im Browser https://localhost:5000 "
    "öffnen und mit dem Demo-Zugang neu anmelden. Danach erneut versuchen."
)


def ssl_kontext(url: str) -> ssl.SSLContext:
    """Zertifikatsprüfung nur für das lokale Gateway abschalten (selbstsigniert), sonst immer prüfen."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    kontext = ssl.create_default_context()
    if host in LOKALE_HOSTS:
        kontext.check_hostname = False
        kontext.verify_mode = ssl.CERT_NONE
    return kontext


def _zahl(wert) -> float | None:
    """'1,300', 'C168.42', 168.4 -> float; leer oder unlesbar -> None."""
    if wert is None or wert == "":
        return None
    if isinstance(wert, bool):
        return None
    if isinstance(wert, (int, float)):
        return float(wert)
    text = str(wert).strip().replace(",", "")
    while text and text[0].isalpha():
        text = text[1:]
    try:
        return float(text)
    except ValueError:
        return None


def _ganz(x: float) -> float | int:
    """100.0 -> 100 (schöneres JSON), 0.5 bleibt 0.5."""
    return int(x) if float(x).is_integer() else x


def _utc(ms) -> datetime | None:
    zahl = _zahl(ms)
    if zahl is None or zahl <= 0:
        return None
    return datetime.fromtimestamp(zahl / 1000, tz=timezone.utc)


class IbkrBroker(Broker):
    name = "ibkr"
    # Bis die Kontokennung bekannt ist, gilt vorsichtshalber "echtes Geld". Mit konto_id="DU..." sofort True.
    papier = False

    def __init__(self, basis_url: str = STANDARD_URL, konto_id: str | None = None, echtgeld_erlaubt: bool = False,
                 timeout: float = 15.0, ratenbegrenzung: bool = True, ausserhalb_handelszeit: bool = False,
                 boerse: str = "SMART", snapshot_versuche: int = 5, snapshot_pause: float = 0.5):
        self.basis_url = basis_url.rstrip("/")
        self.konto_id = konto_id
        self.echtgeld_erlaubt = echtgeld_erlaubt
        self.timeout = timeout
        self.ratenbegrenzung = ratenbegrenzung
        self.ausserhalb_handelszeit = ausserhalb_handelszeit
        self.boerse = boerse
        self.snapshot_versuche = max(1, snapshot_versuche)
        self.snapshot_pause = snapshot_pause
        self.papier = self._ist_papier(konto_id) if konto_id else False
        self.letzter_tickle_fehler: str | None = None
        self._waehrung = ""
        self._conids: dict[str, int] = {}
        self._conid_waehrung: dict[int, str] = {}
        self._devisen: dict[str, int] = {}
        self._geprueft_bis = 0.0
        self._sperre = threading.Lock()
        self._letzter_aufruf = 0.0
        self._letzter_pfad: dict[str, float] = {}
        self._pnl: tuple[float, float | None] = (0.0, None)
        # Kein Proxy: das Gateway läuft lokal. Zertifikatsprüfung siehe ssl_kontext().
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl_kontext(self.basis_url)))

    @staticmethod
    def _ist_papier(konto_id: str) -> bool:
        return konto_id.strip().upper().startswith("DU")   # Demokonten beginnen bei IBKR mit "DU"

    # ---------- HTTP ----------

    def _warte(self, pfad: str) -> None:
        if not self.ratenbegrenzung:
            return
        with self._sperre:
            jetzt = time.monotonic()
            frei = max(self._letzter_aufruf + _GLOBALER_ABSTAND,
                       self._letzter_pfad.get(pfad, 0.0) + _MINDESTABSTAND.get(pfad, 0.0))
            if frei > jetzt:
                time.sleep(frei - jetzt)
            self._letzter_aufruf = self._letzter_pfad[pfad] = time.monotonic()

    def _anfrage(self, methode: str, pfad: str, daten=None, abfrage: dict | None = None):
        self._warte(pfad)
        url = self.basis_url + pfad
        if abfrage:
            url += "?" + urllib.parse.urlencode(abfrage)
        kopf = {"User-Agent": "kitrader/0.1", "Accept": "application/json"}   # IBKR verlangt einen User-Agent
        koerper = None
        if daten is not None:
            koerper = json.dumps(daten).encode()
            kopf["Content-Type"] = "application/json"
        anfrage = urllib.request.Request(url, data=koerper, headers=kopf, method=methode)
        try:
            with self._opener.open(anfrage, timeout=self.timeout) as antwort:
                text = antwort.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            self._http_fehler(e.code, e.read().decode("utf-8", "replace"), pfad)
        except (urllib.error.URLError, OSError) as e:
            grund = getattr(e, "reason", e)
            if isinstance(grund, TimeoutError):
                raise BrokerFehler(f"IBKR antwortet nicht innerhalb von {self.timeout:g} s ({pfad}).") from None
            if isinstance(grund, ssl.SSLError):
                raise BrokerFehler(
                    f"TLS-Fehler zum IBKR-Gateway ({grund}). Prüfe, ob die Adresse {self.basis_url} stimmt.") from None
            raise BrokerFehler(_HILFE_GATEWAY.format(url=self.basis_url, grund=grund)) from None
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except ValueError:
            raise BrokerFehler(f"Unerwartete Antwort von IBKR bei {pfad}: {text[:120]!r}") from None

    def _http_fehler(self, code: int, text: str, pfad: str):
        meldung = text.strip()[:200]
        try:
            j = json.loads(text)
            if isinstance(j, dict):
                meldung = str(j.get("error") or j.get("message") or meldung)
        except ValueError:
            pass
        if code == 401:
            self._geprueft_bis = 0.0
            raise BrokerFehler(_HILFE_LOGIN) from None
        if code == 429:
            raise BrokerFehler("IBKR meldet zu viele Anfragen (429). Kurz warten; wiederholte Verstöße sperren die IP für 10 Minuten.") from None
        raise BrokerFehler(f"IBKR-Fehler {code} bei {pfad}: {meldung}") from None

    # ---------- Sitzung ----------

    def pruefen(self) -> dict:
        """Gateway erreichbar, angemeldet, Konto gewählt? Wirft BrokerFehler mit Anleitung, sonst Statusinfo."""
        status = self._anfrage("POST", "/iserver/auth/status")
        if not isinstance(status, dict) or not status.get("authenticated"):
            raise BrokerFehler(_HILFE_LOGIN)
        if status.get("competing"):
            raise BrokerFehler("Bei IBKR läuft eine konkurrierende Sitzung (z. B. Handelsplattform oder App mit demselben "
                               "Benutzernamen). Dort abmelden und im Gateway neu anmelden.")
        if status.get("connected") is False:
            raise BrokerFehler(_HILFE_LOGIN)
        konten = self._anfrage("GET", "/iserver/accounts")      # Pflicht vor Marktdaten und Orders
        liste = konten.get("accounts") if isinstance(konten, dict) else None
        if not liste:
            raise BrokerFehler("IBKR meldet keine handelbaren Konten. Ist die Anmeldung im Browser vollständig (Demo-Zugang)?")
        if self.konto_id:
            if self.konto_id not in liste:
                raise BrokerFehler(f"Konto {self.konto_id} gehört nicht zur Anmeldung (gefunden: {', '.join(liste)}).")
        else:
            self.konto_id = konten.get("selectedAccount") or liste[0]
        self.papier = self._ist_papier(self.konto_id)
        portfolio = self._anfrage("GET", "/portfolio/accounts")  # Pflicht vor allen /portfolio-Aufrufen
        if isinstance(portfolio, list):
            for k in portfolio:
                if isinstance(k, dict) and k.get("accountId") == self.konto_id and isinstance(k.get("currency"), str):
                    self._waehrung = k["currency"]
        self._geprueft_bis = time.monotonic() + 60
        return {"konto": self.konto_id, "papier": self.papier, "waehrung": self._waehrung}

    def _bereit(self) -> str:
        if self._geprueft_bis <= time.monotonic():
            self.pruefen()
        return self.konto_id

    def tickle(self) -> dict:
        """Hält die Sitzung am Leben (IBKR beendet sie nach 5 Minuten ohne Aufruf)."""
        antwort = self._anfrage("POST", "/tickle")
        return antwort if isinstance(antwort, dict) else {}

    def sitzung_halten(self, intervall: float = 60.0) -> threading.Event:
        """Startet einen Hintergrundfaden, der alle `intervall` Sekunden tickelt. Event.set() beendet ihn."""
        ende = threading.Event()

        def lauf():
            while not ende.is_set():
                try:
                    self.tickle()
                    self.letzter_tickle_fehler = None
                except BrokerFehler as e:
                    self.letzter_tickle_fehler = str(e)
                ende.wait(intervall)

        threading.Thread(target=lauf, name="ibkr-tickle", daemon=True).start()
        return ende

    # ---------- Konto ----------

    def konto(self) -> Konto:
        k = self._bereit()
        s = self._anfrage("GET", f"/portfolio/{k}/summary")
        if not isinstance(s, dict):
            raise BrokerFehler("Unerwartete Antwort von IBKR bei der Kontoübersicht.")

        def eintrag(name):
            e = s.get(name)
            return e if isinstance(e, dict) else {}

        def betrag(name):
            return _zahl(eintrag(name).get("amount"))

        waehrung = ""
        for name in ("totalcashvalue", "netliquidation"):
            w = eintrag(name).get("currency")
            if isinstance(w, str) and w:
                waehrung = w
                break
        waehrung = waehrung or self._waehrung
        guthaben = betrag("totalcashvalue")
        gesamt = betrag("netliquidation")
        if gesamt is None:
            raise BrokerFehler("IBKR liefert keinen Kontowert (netliquidation). Ist das Konto bereit?")
        return Konto(
            waehrung=waehrung, guthaben=guthaben if guthaben is not None else 0.0, gesamtwert=gesamt,
            kaufkraft=betrag("buyingpower"), tagesergebnis=self._tagesergebnis(k), papier=self.papier,
            zusatz={"konto": k, "verfuegbar": betrag("availablefunds"), "ueberschuss": betrag("excessliquidity"),
                    "positionswert": betrag("grosspositionvalue")})

    def _tagesergebnis(self, k: str) -> float | None:
        """Tagesergebnis (dpl); höchstens alle 5 s abfragen, Fehler sind hier nicht schlimm."""
        zeit, wert = self._pnl
        if time.monotonic() - zeit < 5:
            return wert
        try:
            antwort = self._anfrage("GET", "/iserver/account/pnl/partitioned")
            wert = None
            upnl = antwort.get("upnl") if isinstance(antwort, dict) else None
            for schluessel, v in (upnl or {}).items():
                if isinstance(v, dict) and schluessel.startswith(k):
                    wert = _zahl(v.get("dpl"))
                    break
        except BrokerFehler:
            wert = None
        self._pnl = (time.monotonic(), wert)
        return wert

    def positionen(self) -> list[Position]:
        k = self._bereit()
        ergebnis: list[Position] = []
        for seite in range(20):
            liste = self._anfrage("GET", f"/portfolio/{k}/positions/{seite}")
            if not isinstance(liste, list) or not liste:
                break
            for p in liste:
                menge = _zahl(p.get("position"))
                if not menge:
                    continue
                symbol = str(p.get("ticker") or p.get("contractDesc") or p.get("conid"))
                if p.get("conid"):
                    self._conids[symbol.upper()] = int(p["conid"])
                einstand = _zahl(p.get("avgPrice"))
                if einstand is None:
                    einstand = _zahl(p.get("avgCost")) or 0.0
                ergebnis.append(Position(symbol=symbol, menge=menge, einstand=einstand,
                                         kurs=_zahl(p.get("mktPrice")), waehrung=str(p.get("currency") or "")))
            if len(liste) < 100:
                break
        return ergebnis

    # ---------- Instrumente und Kurse ----------

    def _conid(self, symbol: str, kennung: str | None = None) -> int:
        """Symbol -> conid. Eine conid darf direkt als kennung (oder als Symbol) übergeben werden."""
        if kennung:
            try:
                return int(kennung)
            except ValueError:
                raise BrokerFehler(f"kennung muss die IBKR-conid (Zahl) sein, nicht {kennung!r}.") from None
        text = symbol.strip()
        if text.isdigit():
            return int(text)
        schluessel = text.upper()
        if schluessel in self._conids:
            return self._conids[schluessel]
        self._bereit()
        liste = self._anfrage("GET", "/iserver/secdef/search", abfrage={"symbol": schluessel, "secType": "STK"})
        treffer = [t for t in (liste or []) if isinstance(t, dict) and t.get("conid")
                   and str(t.get("symbol", "")).upper() == schluessel]
        aktien = [t for t in treffer if any(s.get("secType") == "STK" for s in t.get("sections") or [])]
        if not aktien:
            raise BrokerFehler(f"Symbol {symbol!r} bei IBKR nicht gefunden. Tipp: die conid direkt als kennung angeben.")
        wahl = aktien
        if len(aktien) > 1:
            us = [t for t in aktien if str(t.get("description", "")).upper() in US_BOERSEN]
            wahl = us if len(us) == 1 else aktien
        if len(wahl) > 1:
            namen = "; ".join(f"{t.get('companyHeader', '?')} (conid {t['conid']})" for t in wahl[:5])
            raise BrokerFehler(f"Symbol {symbol!r} ist bei IBKR nicht eindeutig: {namen}. Bitte die conid als kennung angeben.")
        conid = int(wahl[0]["conid"])
        self._conids[schluessel] = conid
        return conid

    def kurs(self, symbol: str) -> Kurs:
        self._bereit()
        conid = self._conid(symbol)
        kurs = self._kurs_zu_conid(symbol, conid)
        kurs.waehrung = self._instrument_waehrung(conid)
        return kurs

    def _kurs_zu_conid(self, symbol: str, conid: int) -> Kurs:
        eintrag: dict = {}
        for versuch in range(self.snapshot_versuche):
            # Der erste Aufruf startet nur den Datenstrom und liefert oft noch keine Werte.
            antwort = self._anfrage("GET", "/iserver/marketdata/snapshot", abfrage={"conids": conid, "fields": "31,84,86"})
            eintrag = antwort[0] if isinstance(antwort, list) and antwort and isinstance(antwort[0], dict) else {}
            if "84" in eintrag and "86" in eintrag:
                break
            if versuch < self.snapshot_versuche - 1:
                time.sleep(self.snapshot_pause)
        roh_letzter = str(eintrag.get("31", ""))
        if roh_letzter[:1] == "H":
            raise BrokerFehler(f"Der Handel in {symbol} ist ausgesetzt (Halt).")
        # Präfix "C" = Schlusskurs des Vortags, kein aktueller Kurs
        letzter = None if roh_letzter[:1] == "C" else _zahl(roh_letzter)
        bid, ask = _zahl(eintrag.get("84")), _zahl(eintrag.get("86"))
        if bid is None and ask is None and letzter is None:
            raise BrokerFehler(f"Kein aktueller Kurs für {symbol}: Markt geschlossen, Daten noch nicht geladen "
                               f"oder kein Marktdaten-Abo für das Konto.")
        # Feld "_updated" ist die Zeit der letzten Aktualisierung. Fehlt es, ist die Kurszeit unbekannt: die Abrufzeit ist
        # nur eine Schätzung und die Risikoprüfung lässt damit keine Eröffnung zu.
        echte_zeit = _utc(eintrag.get("_updated"))
        return Kurs(symbol=symbol, bid=bid, ask=ask, letzter=letzter, zeit=echte_zeit or datetime.now(timezone.utc),
                    zeit_geschaetzt=echte_zeit is None)

    def _instrument_waehrung(self, conid: int) -> str:
        """Währung des Instruments laut IBKR-Vertragsdaten (zwischengespeichert). Leer, wenn nicht lesbar (= unbekannt)."""
        if conid not in self._conid_waehrung:
            try:
                info = self._anfrage("GET", f"/iserver/contract/{conid}/info")
            except BrokerFehler:
                return ""
            waehrung = info.get("currency") if isinstance(info, dict) else None
            if not (isinstance(waehrung, str) and waehrung.strip()):
                return ""
            self._conid_waehrung[conid] = waehrung.strip().upper()
        return self._conid_waehrung[conid]

    def _devisen_conid(self, basis: str, quote: str) -> int | None:
        """conid des Devisenpaars BASIS.QUOTE (IBKR-Schreibweise EUR.USD, Typ CASH) oder None."""
        name = f"{basis}.{quote}"
        if name not in self._devisen:
            liste = self._anfrage("GET", "/iserver/secdef/search", abfrage={"symbol": basis, "secType": "CASH"})
            for t in liste if isinstance(liste, list) else []:
                kopf = str(t.get("companyHeader", "")).upper().replace("/", ".").replace(" ", "") if isinstance(t, dict) else ""
                if kopf.split("(")[0] == name and t.get("conid"):
                    self._devisen[name] = int(t["conid"])
                    break
            else:
                return None
        return self._devisen[name]

    def wechselkurs(self, von: str, nach: str) -> float | None:
        """Kurs des Devisenpaars über IBKR (Typ CASH, z. B. EUR.USD). Beide Richtungen werden versucht. None, wenn IBKR
        das Paar nicht findet oder keinen Kurs liefert. Gegen ein echtes Gateway nicht erprobt (siehe BROKER.md)."""
        von, nach = von.strip().upper(), nach.strip().upper()
        if von == nach:
            return 1.0
        self._bereit()
        for basis, quote, invers in ((von, nach, False), (nach, von, True)):
            try:
                conid = self._devisen_conid(basis, quote)
                if conid is None:
                    continue
                mitte = self._kurs_zu_conid(f"{basis}.{quote}", conid).mitte
            except BrokerFehler:
                continue
            if mitte and mitte > 0:
                return 1 / mitte if invers else mitte
        return None

    def kerzen(self, symbol: str, takt: str = "1m", anzahl: int = 300) -> list[Kerze]:
        if takt not in _TAKTE:
            raise BrokerFehler(f"Unbekannter Takt {takt!r}; erlaubt: {', '.join(_TAKTE)}.")
        if anzahl < 1:
            raise BrokerFehler("anzahl muss größer als 0 sein.")
        self._bereit()
        conid = self._conid(symbol)
        minuten, balken = _TAKTE[takt]
        # Genug Handelstage anfordern (Börse ca. 390 Minuten/Tag, dazu Reserve), danach auf `anzahl` kürzen.
        handelstage = anzahl + 2 if minuten >= 1440 else math.ceil(anzahl * minuten / 390) + 1
        if handelstage <= 30:
            periode = f"{handelstage}d"
        elif handelstage <= 260:
            periode = f"{math.ceil(handelstage / 5)}w"
        else:
            periode = f"{math.ceil(handelstage / 250)}y"
        antwort = self._anfrage("GET", "/iserver/marketdata/history", abfrage={
            "conid": conid, "period": periode, "bar": balken, "outsideRth": str(self.ausserhalb_handelszeit).lower()})
        daten = antwort.get("data") if isinstance(antwort, dict) else None
        if not daten:
            raise BrokerFehler(f"IBKR liefert keine Kerzen für {symbol} ({takt}). Marktdaten-Abo vorhanden?")
        kerzen = []
        for b in daten:
            t = _utc(b.get("t"))
            werte = [_zahl(b.get(x)) for x in ("o", "h", "l", "c")]
            if t is None or None in werte:
                continue
            kerzen.append(Kerze(t, *werte, v=_zahl(b.get("v")) or 0.0))
        kerzen.sort(key=lambda k: k.t)
        return kerzen[-anzahl:]

    # ---------- Orders ----------

    def senden(self, order: Order) -> OrderStand:
        konto = self._bereit()
        if not self.papier and not self.echtgeld_erlaubt:
            raise BrokerFehler(f"Konto {konto} ist kein Demokonto. Echtgeld ist gesperrt, bis IbkrBroker(echtgeld_erlaubt=True) "
                               f"ausdrücklich gesetzt wird.")
        conid = self._conid(order.symbol, order.kennung)
        tif = "GTC" if order.gueltig == "gtc" else "DAY"
        eltern_id = "kt-" + uuid.uuid4().hex[:20]          # cOID: eindeutig, höchstens 64 Zeichen
        kern = {"acctId": konto, "conid": conid, "listingExchange": self.boerse, "tif": tif,
                "quantity": _ganz(order.menge)}
        ticket = dict(kern, cOID=eltern_id, side="BUY" if order.seite == "kauf" else "SELL",
                      orderType=_ART[order.art], outsideRTH=self.ausserhalb_handelszeit)
        ticket.update(self._preise(order))
        tickets = [ticket]
        gegenseite = "SELL" if order.seite == "kauf" else "BUY"
        if order.stop_loss is not None and order.take_profit is not None:
            ok = order.stop_loss < order.take_profit if order.seite == "kauf" else order.stop_loss > order.take_profit
            if not ok:
                raise BrokerFehler("Stop-Loss und Take-Profit passen nicht zur Richtung der Order.")
        for art, preis in (("STP", order.stop_loss), ("LMT", order.take_profit)):
            if preis is not None:
                # Bracket: Kinder tragen die cOID des Elternteils als parentId und haben selbst keine cOID.
                tickets.append(dict(kern, orderType=art, price=round(preis, 4), side=gegenseite,
                                    outsideRTH=False, parentId=eltern_id))
        antwort = self._anfrage("POST", f"/iserver/account/{konto}/orders", {"orders": tickets})
        return self._order_antwort(antwort, order, geschuetzt=len(tickets) > 1)

    @staticmethod
    def _preise(order: Order) -> dict:
        if order.art == "limit":
            return {"price": round(order.limit, 4)}
        if order.art == "stop":
            return {"price": round(order.stop, 4)}      # STP: price = Stop-Kurs (wie im Bracket-Beispiel der Doku)
        if order.art == "stop_limit":
            return {"price": round(order.limit, 4), "auxPrice": round(order.stop, 4)}
        return {}

    def _order_antwort(self, antwort, order: Order, geschuetzt: bool) -> OrderStand:
        """Wertet die Antwort auf eine Order aus; bestätigt nur bekannte, harmlose Rückfragen."""
        for _ in range(6):
            if isinstance(antwort, dict):
                if antwort.get("error"):
                    raise BrokerFehler(f"IBKR lehnt die Order ab: {antwort['error']}")
                antwort = [antwort]
            if not isinstance(antwort, list) or not antwort or not isinstance(antwort[0], dict):
                raise BrokerFehler(f"Unerwartete Antwort von IBKR auf die Order: {str(antwort)[:150]}")
            erste = antwort[0]
            if "id" in erste and "message" in erste:                    # Rückfrage ("order reply message")
                ids = sorted({m for e in antwort for m in (e.get("messageIds") or [])})
                text = " | ".join(" ".join(e["message"]) if isinstance(e.get("message"), list) else str(e.get("message"))
                                  for e in antwort).replace("\n", " ")
                if ids and set(ids) <= HARMLOSE_HINWEISE:
                    antwort = self._anfrage("POST", f"/iserver/reply/{erste['id']}", {"confirmed": True})
                    continue
                raise BrokerFehler(f"IBKR verlangt eine Bestätigung, die nicht automatisch erteilt wird "
                                   f"({', '.join(ids) or 'ohne Kennung'}): {text}")
            if "order_id" in erste:
                status = _STATUS.get(str(erste.get("order_status")), "offen")
                meldung = f"IBKR-Status: {erste.get('order_status')}"
                if len(antwort) > 1:
                    meldung += "; Schutz-Orders: " + ", ".join(str(e.get("order_id")) for e in antwort[1:])
                elif geschuetzt:
                    meldung += "; Schutz-Orders: Kennungen unbekannt"
                return OrderStand(id=str(erste["order_id"]), order=order, status=status,
                                  zeit=datetime.now(timezone.utc), meldung=meldung)
            if "text" in erste or "options" in erste:
                raise BrokerFehler(f"IBKR hat die Order angehalten: {str(erste.get('text'))[:200]} "
                                   f"(Server-Rückfrage, wird nicht automatisch beantwortet).")
            raise BrokerFehler(f"Unerwartete Antwort von IBKR auf die Order: {str(erste)[:150]}")
        raise BrokerFehler("IBKR stellt zu viele Rückfragen nacheinander; Order nicht gesendet.")

    def orders(self, nur_offene: bool = True) -> list[OrderStand]:
        k = self._bereit()
        abfrage = {"force": "true", "accountId": k}
        antwort = self._anfrage("GET", "/iserver/account/orders", abfrage=abfrage)
        if isinstance(antwort, dict) and antwort.get("snapshot") is False:     # erster Aufruf lädt nur
            antwort = self._anfrage("GET", "/iserver/account/orders", abfrage=abfrage)
        liste = antwort.get("orders") if isinstance(antwort, dict) else antwort
        ergebnis = []
        for e in liste or []:
            if not isinstance(e, dict) or (e.get("acct") or e.get("account") or k) != k:
                continue
            stand = self._order_stand(e)
            if stand and (not nur_offene or stand.status in ("offen", "teilweise")):
                ergebnis.append(stand)
        return ergebnis

    @staticmethod
    def _order_stand(e: dict) -> OrderStand | None:
        oid = e.get("orderId")
        menge = _zahl(e.get("totalSize")) or _zahl(e.get("sizeAndFills"))
        if oid is None or not menge:
            return None
        roh = str(e.get("origOrderType") or e.get("orderType") or "").lower().replace(" ", "").replace("_", "")
        preis, hilf = _zahl(e.get("price")), _zahl(e.get("auxPrice"))
        art, limit, stop = "markt", None, None
        if roh.startswith("stoplimit") or roh.startswith("stplmt"):
            art, limit, stop = "stop_limit", preis, hilf
        elif roh.startswith("stop") or roh == "stp":
            art, stop = "stop", preis if preis is not None else hilf
        elif roh.startswith("limit") or roh == "lmt":
            art, limit = "limit", preis
        seite = "kauf" if str(e.get("side", "")).upper().startswith("B") else "verkauf"
        meldung = str(e.get("orderDesc") or "")
        try:
            order = Order(str(e.get("ticker") or e.get("description1") or e.get("conid")), seite, menge, art, limit, stop,
                          kennung=str(e.get("conid") or ""))
        except ValueError:                                           # unvollständige Preise: als Marktorder führen
            order = Order(str(e.get("ticker") or e.get("conid")), seite, menge, kennung=str(e.get("conid") or ""))
            meldung = (meldung + " (Preis nicht lesbar)").strip()
        ausgefuehrt = _zahl(e.get("filledQuantity")) or 0.0
        status = _STATUS.get(str(e.get("status")), "offen")
        if status == "offen" and ausgefuehrt > 0:
            status = "teilweise"
        return OrderStand(id=str(oid), order=order, status=status, ausgefuehrt=ausgefuehrt, preis=_zahl(e.get("avgPrice")),
                          zeit=_utc(e.get("lastExecutionTime_r")), meldung=meldung)

    def stornieren(self, order_id: str) -> None:
        k = self._bereit()
        antwort = self._anfrage("DELETE", f"/iserver/account/{k}/order/{order_id}")
        if isinstance(antwort, dict) and antwort.get("error"):
            raise BrokerFehler(f"IBKR kann Order {order_id} nicht stornieren: {antwort['error']}")
        if not isinstance(antwort, dict) or "msg" not in antwort:
            raise BrokerFehler(f"Unerwartete Antwort von IBKR beim Stornieren von {order_id}: {str(antwort)[:150]}")

