"""Kraken (Spot-REST-API) mit Schutz gegen versehentliche Echtgeld-Orders.

Nur Standardbibliothek. Schlüssel kommen aus Umgebungsvariablen oder aus ~/.config/kitrader/kraken.env
und werden nie ausgegeben, protokolliert oder in Fehlertexte geschrieben. Anleitung und Quellen: BROKER.md.

Wichtig:
- Kraken Spot hat kein Demokonto. Der Schutz ist `nur_pruefen` (Standard: an): AddOrder wird mit validate=true
  gesendet, Kraken prüft die Order, führt sie aber NICHT aus. Abschalten nur ausdrücklich (nur_pruefen=False oder
  KRAKEN_NUR_PRUEFEN=0 in der Schlüsseldatei).
- Short nur über Margin (`hebel`), standardmäßig aus. `leverage` bekommen nur Orders, die eine Short-Position eröffnen
  oder vergrößern bzw. eine offene Margin-Position schließen, nie der Verkauf von Spot-Bestand.
- Kraken kennt in AddOrder genau EINE bedingte Schlussorder (close[...]). Bei Stop-Loss UND Take-Profit wird der
  Stop-Loss angehängt, der Take-Profit nicht (Meldung im OrderStand): zwei Orders auf denselben Bestand würden sich
  bei Spot gegenseitig sperren und bei Margin ohne OCO eine ungewollte Gegenposition riskieren. Das Ziel überwacht
  deshalb der Autopilot selbst (`fuehrt_ziel_als_order` ist hier False, wenn ein Stop-Loss mitgeht).
- Die Kurszeit kommt vom letzten Handel (`/0/public/Trades`), denn der Ticker enthält keine Zeit. Ohne diese Angabe
  ist die Zeit nur geschätzt (`Kurs.zeit_geschaetzt`): die Risikoprüfung lässt dann keine Eröffnung zu.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path

from .. import symbole as _symbole
from .basis import Broker, BrokerFehler, Kerze, Konto, Kurs, Order, OrderStand, Position

BASIS_URL = "https://api.kraken.com"
SCHLUESSEL_DATEI = Path.home() / ".config" / "kitrader" / "kraken.env"
LOKALE_HOSTS = ("localhost", "127.0.0.1", "::1")

_TAKTE = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 1440}
_FIAT_MIT_Z = ("EUR", "USD", "GBP", "CAD", "JPY", "AUD")
_AUS = ("0", "false", "nein", "aus", "no", "off")
_EPS = 1e-12

_FEHLER = {
    "EAPI:Invalid key": "Kraken kennt diesen API-Schlüssel nicht (falsch kopiert, gelöscht oder widerrufen).",
    "EAPI:Invalid signature": "Kraken lehnt die Signatur ab: das API-Geheimnis (Private Key) ist falsch oder unvollständig kopiert.",
    "EAPI:Invalid nonce": "Kraken lehnt die Nonce ab (nicht größer als die letzte): läuft derselbe Schlüssel gleichzeitig in einem "
                          "anderen Programm? Für kitrader einen eigenen Schlüssel verwenden.",
    "EAPI:Rate limit exceeded": "Zu viele Abfragen in kurzer Zeit; Kraken sperrt den Schlüssel für einige Sekunden. Kurz warten.",
    "EGeneral:Temporary lockout": "Kraken hat den Schlüssel vorübergehend gesperrt (zu viele ungültige Anfragen). Einige Minuten warten.",
    "EGeneral:Permission denied": "Dem API-Schlüssel fehlt eine Berechtigung. Bei Kraken 'Abfragen' und 'Orders' freigeben "
                                  "(niemals 'Auszahlen').",
    "EGeneral:Invalid arguments:Index unavailable": "Für dieses Paar gibt es keinen Indexpreis (trigger=index nicht möglich).",
    "EGeneral:Invalid arguments": "Kraken versteht die Anfrage nicht (fehlende oder ungültige Angaben).",
    "EGeneral:Internal error": "Interner Fehler bei Kraken. Später erneut versuchen.",
    "EService:Unavailable": "Kraken ist gerade nicht verfügbar (Wartung?).",
    "EService:Busy": "Kraken ist überlastet. Später erneut versuchen.",
    "EService:Market in cancel_only mode": "Der Markt nimmt gerade nur Stornierungen an.",
    "EService:Market in post_only mode": "Der Markt nimmt gerade nur passive (Post-only-)Orders an.",
    "EService:Deadline elapsed": "Kraken hat die Anfrage zu spät erhalten und verworfen.",
    "EOrder:Insufficient funds": "Nicht genug Guthaben. (Ein Verkauf ohne Bestand wäre ein Leerverkauf; Margin/Hebel ist aus.)",
    "EOrder:Order minimum not met": "Die Menge liegt unter dem Kraken-Minimum für dieses Paar.",
    "EOrder:Cost minimum not met": "Preis mal Menge liegt unter dem Kraken-Mindestbetrag für dieses Paar.",
    "EOrder:Tick size check failed": "Der Preis passt nicht zur kleinsten Preisstufe (tick_size) des Paars.",
    "EOrder:Invalid price": "Ungültiger Preis.",
    "EOrder:Rate limit exceeded": "Zu viele Order-Aktionen in kurzer Zeit; kurz warten, bis Kraken den Zähler abgebaut hat.",
    "EOrder:Orders limit exceeded": "Zu viele offene Orders für dieses Paar.",
    "EOrder:Positions limit exceeded": "Zu viele offene Margin-Positionen.",
    "EOrder:Margin allowance exceeded": "Das Margin-Limit des Kontos wäre überschritten.",
    "EOrder:Margin level too low": "Zu wenig Sicherheiten für die Margin-Position.",
    "EOrder:Insufficient margin": "Kraken hat gerade nicht genug Margin-Mittel frei.",
    "EOrder:Cannot open position": "Das Konto darf keine Margin-Position eröffnen (Margin nicht freigeschaltet).",
    "EOrder:Cannot open opposing position": "Das Konto darf keine Gegenposition eröffnen (Margin nicht freigeschaltet).",
    "EOrder:Unknown position": "Diese Position gibt es nicht.",
    "EOrder:Unknown order": "Diese Order gibt es nicht (mehr).",
    "EOrder:Trading agreement required": "Bei Kraken muss zuerst die Handelsvereinbarung für dieses Produkt akzeptiert werden.",
    "EQuery:Unknown asset pair": "Kraken kennt dieses Handelspaar nicht.",
    "EQuery:Invalid asset pair": "Ungültiges Handelspaar.",
    "EAuth:Account temporary disabled": "Das Kraken-Konto ist vorübergehend deaktiviert.",
    "EAuth:Account unconfirmed": "Das Kraken-Konto ist noch nicht bestätigt (E-Mail).",
}


class _Transportfehler(BrokerFehler):
    """Netzwerkproblem oder HTTP-Fehler (nicht Krakens eigene Fehlerliste)."""


# ---------- Signatur, Schlüssel, Einstellungen ----------

def signatur(pfad: str, daten: str, nonce: int | str, geheimnis: str) -> str:
    """API-Sign: HMAC-SHA512 über Pfad + SHA256(nonce + POST-Daten) mit dem base64-dekodierten Geheimnis, base64-kodiert.
    `daten` ist der gesendete, URL-kodierte POST-Body (er enthält die nonce selbst)."""
    kern = hashlib.sha256((str(nonce) + daten).encode()).digest()
    mac = hmac.new(base64.b64decode(geheimnis), pfad.encode() + kern, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


@dataclass
class Zugang:
    schluessel: str = field(repr=False)
    geheimnis: str = field(repr=False)

    def __repr__(self) -> str:
        return "Zugang(***)"


def _wahr(text: str | None) -> bool:
    """Alles außer ausdrücklichem Nein bleibt 'an' (sicherer Standard)."""
    return text is None or text.strip().lower() not in _AUS


def lade_einstellungen(datei: Path | str | None = None, umgebung: dict | None = None) -> dict:
    """Liest KRAKEN_API_KEY, KRAKEN_API_SECRET, KRAKEN_NUR_PRUEFEN aus der Umgebung und der Schlüsseldatei
    (Umgebung hat Vorrang). Gibt {'zugang': Zugang|None, 'nur_pruefen': bool} zurück."""
    umgebung = os.environ if umgebung is None else umgebung
    pfad = Path(datei) if datei else Path(umgebung.get("KRAKEN_ENV_DATEI") or SCHLUESSEL_DATEI)
    werte: dict[str, str] = {}
    if pfad.is_file():
        if os.name == "posix" and pfad.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise BrokerFehler(f"Die Schlüsseldatei {pfad} ist für andere Benutzer lesbar. Bitte: chmod 600 {pfad}")
        for zeile in pfad.read_text(encoding="utf-8").splitlines():
            zeile = zeile.strip()
            if not zeile or zeile.startswith("#") or "=" not in zeile:
                continue
            name, _, wert = zeile.partition("=")
            name = name.strip().removeprefix("export ").strip()
            werte[name] = wert.strip().strip("'\"")
    for name in ("KRAKEN_API_KEY", "KRAKEN_API_SECRET", "KRAKEN_NUR_PRUEFEN"):
        if umgebung.get(name):
            werte[name] = umgebung[name]
    zugang = None
    if werte.get("KRAKEN_API_KEY") and werte.get("KRAKEN_API_SECRET"):
        zugang = Zugang(werte["KRAKEN_API_KEY"], werte["KRAKEN_API_SECRET"])
    return {"zugang": zugang, "nur_pruefen": _wahr(werte.get("KRAKEN_NUR_PRUEFEN"))}


# ---------- Ratenbegrenzung ----------

class Ratenzaehler:
    """Nachbildung des Kraken-Aufrufzählers: jeder Aufruf erhöht ihn, er sinkt gleichmäßig; über dem Maximum wird gewartet."""
    STUFEN = {"starter": (15, 0.33), "intermediate": (20, 0.5), "pro": (20, 1.0)}

    def __init__(self, maximum: float, abfall: float, uhr=time.monotonic, schlafen=time.sleep):
        self.maximum, self.abfall, self._uhr, self._schlafen = maximum, abfall, uhr, schlafen
        self._wert, self._stand, self._sperre = 0.0, uhr(), threading.Lock()

    def _abbauen(self) -> None:
        jetzt = self._uhr()
        self._wert = max(0.0, self._wert - (jetzt - self._stand) * self.abfall)
        self._stand = jetzt

    def warte(self, kosten: float = 1) -> None:
        with self._sperre:
            self._abbauen()
            if self._wert + kosten > self.maximum:
                self._schlafen((self._wert + kosten - self.maximum) / self.abfall)
                self._abbauen()
            self._wert += kosten

    def sperre(self, sekunden: float) -> None:
        """Nach 'Rate limit exceeded': so tun, als wäre der Zähler voll, damit die nächsten Aufrufe warten."""
        with self._sperre:
            self._abbauen()
            self._wert = self.maximum + sekunden * self.abfall


_nonce_sperre = threading.Lock()
_letzte_nonce = 0


def _naechste_nonce() -> int:
    """Streng steigend (Millisekunden-Zeitstempel, im Prozess zusätzlich hochgezählt)."""
    global _letzte_nonce
    with _nonce_sperre:
        _letzte_nonce = max(int(time.time() * 1000), _letzte_nonce + 1)
        return _letzte_nonce


# ---------- Hilfen ----------

def _f(wert) -> float | None:
    try:
        return float(wert)
    except (TypeError, ValueError):
        return None


def _waehrung_von(asset: str) -> str:
    """'ZEUR' -> 'EUR', 'XXBT' -> 'XBT', 'USDT' bleibt."""
    return asset[1:] if len(asset) == 4 and asset[0] in "ZX" else asset


def _normal(symbol: str) -> str:
    s = symbol.strip().upper().replace("/", "").replace("-", "").replace("_", "")
    if s.startswith("BTC"):
        s = "XBT" + s[3:]          # Kraken nennt Bitcoin XBT
    elif s.startswith("DOGE"):
        s = "XDG" + s[4:]
    return s


def _utc(sekunden) -> datetime | None:
    zahl = _f(sekunden)
    return datetime.fromtimestamp(zahl, tz=timezone.utc) if zahl else None


def _text(wert: Decimal) -> str:
    return format(wert, "f")


class KrakenBroker(Broker):
    name = "kraken"
    papier = False        # Kraken Spot hat kein Demokonto; der Schutz ist nur_pruefen

    def __init__(self, zugang: Zugang | None = None, nur_pruefen: bool | None = None, basis_url: str = BASIS_URL,
                 waehrung: str = "EUR", hebel: int | None = None, spot_paare: tuple[str, ...] = (),
                 stufe: str = "starter", timeout: float = 20.0, ratenbegrenzung: bool = True,
                 datei: Path | str | None = None):
        """zugang/nur_pruefen: None = aus Umgebung bzw. ~/.config/kitrader/kraken.env (nur_pruefen dann Standard True).
        hebel: None = keine Margin, kein Short. spot_paare: Paare (z. B. "XBTEUR"), deren Bestand als Position zählt."""
        self.basis_url = basis_url.rstrip("/")
        self.waehrung = waehrung.upper()
        self.hebel = hebel
        self.spot_paare = tuple(spot_paare)
        self.timeout = timeout
        self.ratenbegrenzung = ratenbegrenzung
        self._zugang = zugang
        self._nur_pruefen = nur_pruefen
        self._datei = datei
        self._konfig_geladen = zugang is not None and nur_pruefen is not None
        maximum, abfall = Ratenzaehler.STUFEN[stufe]
        self._zaehler = Ratenzaehler(maximum, abfall)
        self._letzter_oeffentlich = 0.0
        self._paare: dict[str, dict] = {}
        self._verzeichnis: _symbole.PaarVerzeichnis | None = None
        host = (urllib.parse.urlsplit(self.basis_url).hostname or "").lower()
        # Lokale Testserver ohne Proxy ansprechen, sonst die Proxy-Einstellungen des Systems verwenden.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if host in LOKALE_HOSTS \
            else urllib.request.build_opener()

    def __repr__(self) -> str:
        return f"KrakenBroker(nur_pruefen={self.nur_pruefen})"

    # ---------- Einstellungen ----------

    def _konfig(self) -> None:
        if self._konfig_geladen:
            return
        k = lade_einstellungen(self._datei)
        if self._zugang is None:
            self._zugang = k["zugang"]
        if self._nur_pruefen is None:
            self._nur_pruefen = k["nur_pruefen"]
        self._konfig_geladen = True

    @property
    def nur_pruefen(self) -> bool:
        self._konfig()
        return True if self._nur_pruefen is None else bool(self._nur_pruefen)

    def _schluessel(self) -> Zugang:
        self._konfig()
        if self._zugang is None:
            raise BrokerFehler(
                "Kein Kraken-API-Schlüssel gefunden. Lege die Datei ~/.config/kitrader/kraken.env an (chmod 600) mit den Zeilen "
                "KRAKEN_API_KEY=... und KRAKEN_API_SECRET=... oder setze diese Umgebungsvariablen. Siehe BROKER.md.")
        return self._zugang

    # ---------- HTTP ----------

    def _senden(self, anfrage: urllib.request.Request, pfad: str):
        try:
            with self._opener.open(anfrage, timeout=self.timeout) as antwort:
                text = antwort.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 403):
                self._zaehler.sperre(10)
            raise _Transportfehler(f"Kraken antwortet mit HTTP {e.code} bei {pfad} (überlastet, gesperrt oder in Wartung).") from None
        except (urllib.error.URLError, OSError) as e:
            grund = getattr(e, "reason", e)
            if isinstance(grund, (TimeoutError, socket.timeout)):
                raise _Transportfehler(f"Kraken antwortet nicht innerhalb von {self.timeout:g} s ({pfad}).") from None
            raise _Transportfehler(f"Kraken ist nicht erreichbar ({pfad}): {grund}. Internetverbindung prüfen.") from None
        try:
            j = json.loads(text)
        except ValueError:
            raise _Transportfehler(f"Unerwartete Antwort von Kraken bei {pfad}: {text[:100]!r}") from None
        fehler = [str(x) for x in (j.get("error") or []) if str(x).startswith("E")]   # "W..." sind nur Warnungen
        if fehler:
            if any(x.startswith(("EAPI:Rate limit", "EOrder:Rate limit")) for x in fehler):
                self._zaehler.sperre(10)
            raise BrokerFehler("; ".join(self._uebersetzen(x) for x in fehler))
        return j.get("result")

    @staticmethod
    def _uebersetzen(code: str) -> str:
        if code in _FEHLER:
            text = _FEHLER[code]
        else:   # z. B. "EGeneral:Internal error:5": längsten passenden Anfang nehmen
            passend = [k for k in _FEHLER if code.startswith(k)]
            text = _FEHLER[max(passend, key=len)] if passend else "Kraken meldet einen Fehler."
        return f"{text} [{code}]"

    def _oeffentlich(self, methode: str, parameter: dict):
        if self.ratenbegrenzung:
            warten = self._letzter_oeffentlich + 1.0 - time.monotonic()   # eigene Vorsicht: höchstens 1 Aufruf/s
            if warten > 0:
                time.sleep(warten)
            self._letzter_oeffentlich = time.monotonic()
        pfad = f"/0/public/{methode}"
        url = f"{self.basis_url}{pfad}?{urllib.parse.urlencode(parameter)}"
        return self._senden(urllib.request.Request(url, headers={"User-Agent": "kitrader/0.1"}), pfad)

    def _privat(self, methode: str, daten: dict | None = None, kosten: float = 1):
        zugang = self._schluessel()
        if self.ratenbegrenzung and kosten:
            self._zaehler.warte(kosten)
        pfad = f"/0/private/{methode}"
        nonce = _naechste_nonce()
        koerper = urllib.parse.urlencode({"nonce": nonce, **(daten or {})})
        kopf = {"API-Key": zugang.schluessel, "API-Sign": signatur(pfad, koerper, nonce, zugang.geheimnis),
                "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "kitrader/0.1"}
        anfrage = urllib.request.Request(self.basis_url + pfad, data=koerper.encode(), headers=kopf, method="POST")
        return self._senden(anfrage, pfad)

    # ---------- Handelspaare ----------

    def _paar(self, symbol: str, kennung: str | None = None) -> dict:
        """Paar-Infos (Genauigkeit, Mindestmenge ...) von Kraken; zwischengespeichert.

        `symbol` darf vieles sein (BTC, Bitcoin, BTC/EUR, XBT/EUR, XBTEUR ...): `symbole.kraken_name` macht daraus den Altnamen
        (XBTEUR); ohne Gegenwährung gilt die Währung dieses Brokers. Kennt Kraken den Namen nicht, wird die Paarliste befragt und der
        Fehler nennt Vorschläge („Meintest du …?“). Mit `kennung` (z. B. XXBTZEUR) wird genau dieser Name gefragt."""
        try:
            name = (kennung or _symbole.kraken_name(symbol, self.waehrung)).upper()
        except _symbole.SymbolFehler as e:
            raise BrokerFehler(str(e)) from None
        if name in self._paare:
            return self._paare[name]
        try:
            ergebnis = self._oeffentlich("AssetPairs", {"pair": name})
        except _Transportfehler:
            raise
        except BrokerFehler as e:
            if kennung is not None or "asset pair" not in str(e).lower():
                raise
            ergebnis = self._paar_suchen(symbol, e)
        if not ergebnis:
            raise BrokerFehler(f"Kraken kennt das Handelspaar {symbol!r} nicht.")
        schluessel, info = next(iter(ergebnis.items()))
        paar = dict(info, key=schluessel)
        paar.setdefault("altname", name)
        for n in (name, schluessel, paar["altname"].upper()):
            self._paare[n] = paar
        return paar

    def _paar_suchen(self, symbol: str, ursache: BrokerFehler) -> dict:
        """Zweiter Versuch über die Paarliste von Kraken (Altname, wsname, Basis und Gegenwährung). Wirft BrokerFehler mit Vorschlägen."""
        if self._verzeichnis is None:
            self._verzeichnis = _symbole.PaarVerzeichnis(holen=lambda: self._oeffentlich("AssetPairs", {}))
        try:
            treffer = self._verzeichnis.finden(symbol, self.waehrung)
        except _symbole.SymbolFehler as e:
            raise BrokerFehler(str(e)) from None
        return self._oeffentlich("AssetPairs", {"pair": treffer.altname}) or {}

    def _altname(self, kraken_paar: str) -> str:
        """Interner Name (XXBTZEUR) -> Altname (XBTEUR); bei Problemen unverändert."""
        try:
            return self._paar(kraken_paar, kennung=kraken_paar)["altname"]
        except BrokerFehler:
            return kraken_paar

    # ---------- Konto ----------

    def konto(self) -> Konto:
        asset = ("Z" + self.waehrung) if self.waehrung in _FIAT_MIT_Z else self.waehrung
        bestaende = self._privat("Balance") or {}
        tb = self._privat("TradeBalance", {"asset": asset}) or {}
        eb = _f(tb.get("eb"))
        if eb is None:
            raise BrokerFehler("Kraken liefert keinen Kontowert (TradeBalance).")
        guthaben = _f(bestaende.get(asset)) or _f(bestaende.get(self.waehrung)) or 0.0
        return Konto(
            waehrung=self.waehrung, guthaben=guthaben, gesamtwert=eb + (_f(tb.get("n")) or 0.0),
            kaufkraft=_f(tb.get("mfo")) if self.hebel else guthaben, tagesergebnis=None, papier=False,
            zusatz={"bestaende": {k: float(v) for k, v in bestaende.items() if _f(v)},
                    "margin_frei": _f(tb.get("mf")), "margin_stand_prozent": _f(tb.get("ml")),
                    "nur_pruefen": self.nur_pruefen})

    def positionen(self) -> list[Position]:
        ergebnis: list[Position] = []
        roh = self._privat("OpenPositions", {"docalcs": "true", "consolidation": "market"}) or []
        for p in (roh.values() if isinstance(roh, dict) else roh):
            offen = (_f(p.get("vol")) or 0.0) - (_f(p.get("vol_closed")) or 0.0)
            if offen <= 0:
                continue
            paar = self._paar(p.get("pair", ""), kennung=p.get("pair")) if p.get("pair") else {}
            gesamt = _f(p.get("vol")) or offen
            wert = _f(p.get("value"))
            ergebnis.append(Position(
                symbol=paar.get("altname", p.get("pair", "")),
                menge=offen if p.get("type") == "buy" else -offen,
                einstand=(_f(p.get("cost")) or 0.0) / gesamt,
                kurs=wert / offen if wert is not None else None,
                waehrung=_waehrung_von(paar.get("quote", ""))))
        if self.spot_paare:
            bestaende = self._privat("Balance") or {}
            for symbol in self.spot_paare:
                paar = self._paar(symbol)
                menge = _f(bestaende.get(paar.get("base", ""))) or 0.0
                if menge and menge >= (_f(paar.get("ordermin")) or 0.0):
                    kurs = self.kurs(paar["altname"]).mitte
                    # Kraken kennt bei Spot keinen Einstandspreis: als einstand gilt der aktuelle Kurs (Ergebnis 0).
                    ergebnis.append(Position(paar["altname"], menge, kurs or 0.0, kurs, _waehrung_von(paar.get("quote", ""))))
        return ergebnis

    # ---------- Kurse ----------

    def kurs(self, symbol: str) -> Kurs:
        return self._kurs(symbol, mit_zeit=True)

    def _kurs(self, symbol: str, mit_zeit: bool, kennung: str | None = None) -> Kurs:
        paar = self._paar(symbol, kennung)
        ergebnis = self._oeffentlich("Ticker", {"pair": paar["altname"]})
        if not ergebnis:
            raise BrokerFehler(f"Kraken liefert keinen Kurs für {symbol!r}.")
        t = next(iter(ergebnis.values()))
        erstes = lambda feld: _f((t.get(feld) or [None])[0])   # noqa: E731
        jetzt = datetime.now(timezone.utc)
        # Der Ticker enthält keinen Zeitstempel. Echte Zeit liefert nur der letzte Handel (Trades); gibt es sie nicht,
        # ist die Abrufzeit bloß geschätzt, und das wird im Kurs vermerkt.
        handel = self._letzter_handel(paar) if mit_zeit else None
        return Kurs(symbol=symbol, bid=erstes("b"), ask=erstes("a"), letzter=erstes("c"),
                    zeit=min(handel, jetzt) if handel else jetzt, zeit_geschaetzt=handel is None,
                    waehrung=_waehrung_von(paar.get("quote", "")))

    def _letzter_handel(self, paar: dict) -> datetime | None:
        """Zeit des letzten Handels im Paar (echte Zeit von Kraken) oder None, wenn sie sich nicht ermitteln lässt."""
        try:
            ergebnis = self._oeffentlich("Trades", {"pair": paar["altname"], "count": 1}) or {}
        except BrokerFehler:
            return None
        zeilen = next((v for k, v in ergebnis.items() if k != "last"), None)
        zeiten = [_f(z[2]) for z in zeilen if isinstance(z, (list, tuple)) and len(z) > 2] if isinstance(zeilen, list) else []
        zeiten = [z for z in zeiten if z]
        return _utc(max(zeiten)) if zeiten else None

    def wechselkurs(self, von: str, nach: str) -> float | None:
        """Wert einer Einheit `von` in `nach` über das Kraken-Paar (z. B. EURUSD); auch das umgekehrte Paar wird versucht."""
        von, nach = _normal(von), _normal(nach)
        if von == nach:
            return 1.0
        for name, invers in ((von + nach, False), (nach + von, True)):
            try:
                mitte = self._kurs(name, mit_zeit=False, kennung=name).mitte
            except BrokerFehler:
                continue
            if mitte and mitte > 0:
                return 1 / mitte if invers else mitte
        return None

    def fuehrt_ziel_als_order(self, order: Order) -> bool:
        """Kraken hängt nur eine Schlussorder an: mit Stop-Loss geht das Ziel nicht mit, dann überwacht es der Autopilot."""
        return order.take_profit is None or order.stop_loss is None

    def kerzen(self, symbol: str, takt: str = "1m", anzahl: int = 300) -> list[Kerze]:
        if takt not in _TAKTE:
            raise BrokerFehler(f"Unbekannter Takt {takt!r}; erlaubt: {', '.join(_TAKTE)}.")
        if anzahl < 1:
            raise BrokerFehler("anzahl muss größer als 0 sein.")
        paar = self._paar(symbol)
        ergebnis = self._oeffentlich("OHLC", {"pair": paar["altname"], "interval": _TAKTE[takt]}) or {}
        zeilen = next((v for k, v in ergebnis.items() if k != "last"), None)
        if not zeilen:
            raise BrokerFehler(f"Kraken liefert keine Kerzen für {symbol!r}.")
        kerzen = [Kerze(_utc(z[0]) or datetime.fromtimestamp(0, timezone.utc), float(z[1]), float(z[2]), float(z[3]),
                        float(z[4]), float(z[6])) for z in zeilen]
        return kerzen[-anzahl:]   # Kraken liefert höchstens 720; die letzte Kerze ist die laufende (unfertige)

    # ---------- Orders ----------

    def _preis(self, wert: float, paar: dict) -> str:
        d = Decimal(str(wert))
        tick = _f(paar.get("tick_size"))
        if tick:
            tick_d = Decimal(str(paar["tick_size"]))
            return _text((d / tick_d).quantize(Decimal(1), ROUND_HALF_UP) * tick_d)
        return _text(d.quantize(Decimal(1).scaleb(-int(paar.get("pair_decimals", 8))), ROUND_HALF_UP))

    def _auftrag(self, order: Order, paar: dict, cid: str) -> tuple[dict, str]:
        """Baut die AddOrder-Parameter. Gibt (Parameter, Zusatzmeldung) zurück."""
        status = paar.get("status", "online")
        if status in ("cancel_only", "reduce_only") or (status in ("post_only", "limit_only") and order.art != "limit"):
            raise BrokerFehler(f"Das Paar {paar['altname']} ist bei Kraken gerade im Modus '{status}'; diese Order ist nicht möglich.")
        menge = Decimal(str(order.menge)).quantize(Decimal(1).scaleb(-int(paar.get("lot_decimals", 8))), ROUND_DOWN)
        mindest = _f(paar.get("ordermin")) or 0.0
        if float(menge) < mindest:
            raise BrokerFehler(f"Menge {order.menge:g} ist unter dem Kraken-Minimum {mindest:g} für {paar['altname']}.")
        d = {"pair": paar["altname"], "type": "buy" if order.seite == "kauf" else "sell", "volume": _text(menge),
             "cl_ord_id": cid}
        if order.art == "markt":
            d["ordertype"] = "market"
        elif order.art == "limit":
            d.update(ordertype="limit", price=self._preis(order.limit, paar))
        elif order.art == "stop":
            d.update(ordertype="stop-loss", price=self._preis(order.stop, paar))
        else:
            d.update(ordertype="stop-loss-limit", price=self._preis(order.stop, paar), price2=self._preis(order.limit, paar))
        kosten_min = _f(paar.get("costmin"))
        if order.art == "limit" and kosten_min and order.menge * order.limit < kosten_min:
            raise BrokerFehler(f"Order unter dem Kraken-Mindestbetrag von {kosten_min:g} {_waehrung_von(paar.get('quote', ''))}.")
        if order.gueltig == "tag" and order.art != "markt":
            ende = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + 86400
            d.update(timeinforce="GTD", expiretm=str(int(ende)))     # "Tag" = bis 00:00 UTC
        if order.stop_loss is not None and order.take_profit is not None:
            if not (order.stop_loss < order.take_profit if order.seite == "kauf" else order.stop_loss > order.take_profit):
                raise BrokerFehler("Stop-Loss und Take-Profit passen nicht zur Richtung der Order.")
        zusatz = ""
        if order.stop_loss is not None:
            d.update({"close[ordertype]": "stop-loss", "close[price]": self._preis(order.stop_loss, paar)})
            if order.take_profit is not None:
                zusatz = ("Take-Profit NICHT angehängt: Kraken erlaubt nur eine bedingte Schlussorder, der Stop-Loss hat Vorrang; "
                          "den Gewinnausstieg überwacht der Autopilot selbst (fuehrt_ziel_als_order).")
        elif order.take_profit is not None:
            d.update({"close[ordertype]": "take-profit", "close[price]": self._preis(order.take_profit, paar)})
        return d, zusatz

    def _margin_menge(self, paar: dict) -> float:
        """Offene Margin-Position im Paar (positiv = long, negativ = short, 0 = keine)."""
        summe = 0.0
        roh = self._privat("OpenPositions", {"consolidation": "market"}) or []
        for p in (roh.values() if isinstance(roh, dict) else roh):
            if not p.get("pair") or self._paar(p["pair"], kennung=p["pair"])["altname"] != paar["altname"]:
                continue
            offen = (_f(p.get("vol")) or 0.0) - (_f(p.get("vol_closed")) or 0.0)
            if offen > 0:
                summe += offen if p.get("type") == "buy" else -offen
        return summe

    def _hebel_fuer(self, order: Order, paar: dict, menge: float) -> str | None:
        """Hebel (`leverage`) nur dort, wo er gebraucht wird: für eine Order, die eine Short-Position eröffnet oder
        vergrößert, und für die Order, die eine offene Margin-Position schließt. Der Verkauf von Spot-Bestand bekommt nie
        Hebel (er würde sonst einen Short zusätzlich zum Bestand eröffnen), und ein Kauf eröffnet nie eine Margin-Long-Position.
        Unklare Mischfälle werden abgelehnt statt geraten."""
        if not self.hebel:
            return None
        margin = self._margin_menge(paar)
        if order.seite == "verkauf":
            if margin > _EPS:                       # offene Margin-Long-Position: der Verkauf schließt sie
                if menge > margin + _EPS:
                    raise BrokerFehler(f"Der Verkauf von {menge:g} {paar['altname']} ist größer als die Margin-Long-Position "
                                       f"({margin:g}) und würde zusätzlich einen Short eröffnen. Nicht gesendet; bitte einzeln schließen.")
                return str(self.hebel)
            spot = _f((self._privat("Balance") or {}).get(paar.get("base", ""))) or 0.0
            if menge <= spot + _EPS:
                return None                         # Verkauf von Spot-Bestand: ohne Hebel
            if spot > _EPS:
                raise BrokerFehler(f"Der Verkauf von {menge:g} {paar['altname']} übersteigt den Spot-Bestand ({spot:g}) und "
                                   "würde zusätzlich einen Short eröffnen. Nicht gesendet; Bestand und Short getrennt handeln.")
            return str(self.hebel)                  # kein Bestand: Short eröffnen oder vergrößern
        if margin < -_EPS:                          # offene Margin-Short-Position: der Kauf schließt sie
            if menge > -margin + _EPS:
                raise BrokerFehler(f"Der Kauf von {menge:g} {paar['altname']} ist größer als die Margin-Short-Position "
                                   f"({-margin:g}) und würde zusätzlich eine Long-Position eröffnen. Nicht gesendet.")
            return str(self.hebel)
        return None                                 # normaler Spot-Kauf

    def senden(self, order: Order) -> OrderStand:
        paar = self._paar(order.symbol, order.kennung)
        cid = "kt" + uuid.uuid4().hex[:12]              # eigene Order-Kennung, um nach einem Netzfehler nachsehen zu können
        daten, zusatz = self._auftrag(order, paar, cid)
        hebel = self._hebel_fuer(order, paar, float(daten["volume"]))
        if hebel:
            daten["leverage"] = hebel
        pruefen = self.nur_pruefen
        if pruefen:
            daten["validate"] = "true"
        try:
            ergebnis = self._privat("AddOrder", daten, kosten=0) or {}
        except _Transportfehler as e:
            raise BrokerFehler(f"{e} Ob die Order angekommen ist, ist unklar (cl_ord_id {cid}): bitte offene Orders prüfen, "
                               f"nicht blind wiederholen.") from None
        beschreibung = (ergebnis.get("descr") or {}).get("order", "")
        if (ergebnis.get("descr") or {}).get("close"):
            beschreibung += f" | {ergebnis['descr']['close']}"
        if pruefen:
            hinweis = "NUR GEPRÜFT (validate=true), nicht ausgeführt: " + beschreibung
            return OrderStand(id="GEPRUEFT-" + cid, order=order, status="storniert", zeit=datetime.now(timezone.utc),
                              meldung=" ".join(x for x in (hinweis, zusatz) if x))
        txid = (ergebnis.get("txid") or [None])[0]
        if not txid:
            raise BrokerFehler("Kraken hat keine Order-Kennung geliefert; bitte offene Orders prüfen.")
        stand = OrderStand(id=txid, order=order, status="offen", zeit=datetime.now(timezone.utc),
                           meldung=" ".join(x for x in (beschreibung, zusatz) if x))
        if order.art == "markt":
            try:
                gelesen = self._privat("QueryOrders", {"txid": txid}) or {}
                if txid in gelesen:
                    neu = self._order_stand(txid, gelesen[txid])
                    stand.status, stand.ausgefuehrt, stand.preis = neu.status, neu.ausgefuehrt, neu.preis
            except BrokerFehler:
                stand.meldung += " (Status noch nicht abgefragt)"
        return stand

    def _order_stand(self, txid: str, o: dict) -> OrderStand:
        d = o.get("descr") or {}
        roh = str(d.get("ordertype", "limit"))
        p1, p2 = _f(d.get("price")), _f(d.get("price2"))
        menge = _f(o.get("vol")) or 0.0
        seite = "kauf" if d.get("type") == "buy" else "verkauf"
        symbol = d.get("pair", "")
        if roh == "market":
            art, limit, stop = "markt", None, None
        elif roh in ("take-profit", "take-profit-limit"):
            # Gewinnziel, kein Schutz: darf vom Autopiloten nicht als Schutz-Stop gezählt werden
            art, limit, stop = "limit", (p2 if roh == "take-profit-limit" and p2 else p1), None
        elif roh.endswith("-limit") and roh != "limit":
            art, limit, stop = "stop_limit", p2, p1
        elif roh == "limit" or roh in ("iceberg", "settle-position"):
            art, limit, stop = "limit", p1, None
        else:
            art, limit, stop = "stop", None, p1       # stop-loss, trailing-stop: Auslösepreis
        meldung = f"{d.get('order', '')} [{roh}]".strip()
        if d.get("close"):
            meldung += f" | {d['close']}"
        try:
            order = Order(symbol, seite, menge, art, limit, stop, kennung=symbol)
        except ValueError:
            order = Order(symbol or "?", seite, menge or 1e-12, kennung=symbol)
            meldung += " (Preis/Menge nicht lesbar)"
        ausgefuehrt = _f(o.get("vol_exec")) or 0.0
        status = {"pending": "offen", "open": "offen", "closed": "ausgefuehrt", "canceled": "storniert",
                  "expired": "storniert"}.get(str(o.get("status")), "offen")
        if status == "offen" and ausgefuehrt > 0:
            status = "teilweise"
        return OrderStand(id=txid, order=order, status=status, ausgefuehrt=ausgefuehrt,
                          preis=_f(o.get("price")) or None, zeit=_utc(o.get("closetm")) or _utc(o.get("opentm")), meldung=meldung)

    def orders(self, nur_offene: bool = True) -> list[OrderStand]:
        liste = [self._order_stand(t, o) for t, o in ((self._privat("OpenOrders") or {}).get("open") or {}).items()]
        if not nur_offene:
            geschlossen = self._privat("ClosedOrders", {"without_count": "true"}, kosten=2) or {}
            liste += [self._order_stand(t, o) for t, o in (geschlossen.get("closed") or {}).items()]
        return liste

    def stornieren(self, order_id: str) -> None:
        if order_id.startswith("GEPRUEFT-"):
            return                                      # nur geprüfte Order: gibt es bei Kraken nicht
        ergebnis = self._privat("CancelOrder", {"txid": order_id}, kosten=0) or {}
        if not ergebnis.get("count"):
            raise BrokerFehler(f"Kraken hat keine Order {order_id} storniert (schon ausgeführt oder storniert?).")

    def alles_schliessen(self, symbole=None) -> list[str]:
        schritte = super().alles_schliessen(symbole)
        if self.nur_pruefen:
            schritte.insert(0, "HINWEIS nur_pruefen=true: Stornierungen sind echt, Glattstellungen werden nur geprüft, nicht ausgeführt.")
        return schritte
