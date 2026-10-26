"""Gemeinsame Schnittstelle aller Broker: Papier, Interactive Brokers, Kraken.

Jeder Broker liefert dieselben Datentypen. Die Strategie und die KI sprechen nie direkt mit einem Broker,
sondern immer über den Autopiloten, der jede Order vorher durch die Risikoprüfung (risiko.py) schickt.
Mengen sind immer positiv; die Richtung steckt in `seite`. Zeiten sind UTC.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

SEITEN = ("kauf", "verkauf")
ARTEN = ("markt", "limit", "stop", "stop_limit")
STATUS = ("offen", "teilweise", "ausgefuehrt", "storniert", "abgelehnt")


def symbol_schluessel(symbol: str) -> str:
    """Vergleichsschlüssel für Symbolnamen: Broker liefern Positionen und Orders unter ihrem eigenen Namen
    (Kraken "XBTEUR", IBKR "AAPL"), in den Einstellungen steht oft "BTC/EUR" oder "aapl". Gleicher Schlüssel = gleiches Symbol."""
    text = "".join(c for c in symbol.upper() if c.isalnum())
    if text.startswith("BTC"):
        return "XBT" + text[3:]                 # Kraken nennt Bitcoin XBT
    if text.startswith("DOGE"):
        return "XDG" + text[4:]
    return text


class BrokerFehler(RuntimeError):
    """Der Broker hat abgelehnt oder ist nicht erreichbar. Text ist für Menschen lesbar."""


@dataclass
class Order:
    symbol: str                      # z. B. "SAP", "AAPL", "XBTEUR"; Broker-eigene Kennung in `kennung`
    seite: str                       # "kauf" oder "verkauf" (verkauf ohne Bestand = Short)
    menge: float
    art: str = "markt"
    limit: float | None = None
    stop: float | None = None
    stop_loss: float | None = None   # Schutz-Stop als angehängte Order (Pflicht laut Risikoprüfung)
    take_profit: float | None = None
    gueltig: str = "tag"             # "tag" oder "gtc"
    kennung: str | None = None       # Broker-eigene Instrument-ID (IBKR conid, Kraken-Paar)
    notiz: str = ""                  # Begründung, landet im Journal

    def __post_init__(self):
        if self.seite not in SEITEN:
            raise ValueError(f"seite muss {SEITEN} sein, nicht {self.seite!r}")
        if self.art not in ARTEN:
            raise ValueError(f"art muss {ARTEN} sein, nicht {self.art!r}")
        if not self.menge > 0:
            raise ValueError("menge muss größer als 0 sein")
        if self.art in ("limit", "stop_limit") and self.limit is None:
            raise ValueError("limit fehlt")
        if self.art in ("stop", "stop_limit") and self.stop is None:
            raise ValueError("stop fehlt")


@dataclass
class OrderStand:
    id: str
    order: Order
    status: str
    ausgefuehrt: float = 0.0         # bereits ausgeführte Menge
    preis: float | None = None       # Durchschnittspreis der Ausführungen
    zeit: datetime | None = None
    meldung: str = ""


@dataclass
class Position:
    symbol: str
    menge: float                     # positiv = long, negativ = short
    einstand: float                  # Durchschnittspreis
    kurs: float | None = None        # letzter bekannter Kurs
    waehrung: str = ""

    @property
    def ergebnis(self) -> float | None:
        """Unrealisiertes Ergebnis in Währung."""
        return None if self.kurs is None else (self.kurs - self.einstand) * self.menge


@dataclass
class Konto:
    waehrung: str
    guthaben: float                  # Bargeld
    gesamtwert: float                # Guthaben plus Positionen zum Kurs
    kaufkraft: float | None = None
    tagesergebnis: float | None = None
    papier: bool = True              # True = Demokonto / Simulation
    zusatz: dict = field(default_factory=dict)


@dataclass
class Kurs:
    symbol: str
    bid: float | None
    ask: float | None
    letzter: float | None
    zeit: datetime                   # Zeit des Kurses laut Quelle (nicht Abrufzeit)
    # True = die Quelle nennt keine echte Kurszeit, `zeit` ist nur die Abrufzeit. Dann ist unbekannt, wie alt der Kurs
    # wirklich ist: die Risikoprüfung lässt damit keine Eröffnung zu (Schließen bleibt erlaubt).
    zeit_geschaetzt: bool = False
    # Währung, in der der Kurs notiert ist (z. B. "USD"). Leer = unbekannt, gilt dann wie die Kontowährung.
    waehrung: str = ""

    @property
    def mitte(self) -> float | None:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2
        return self.letzter


@dataclass
class Kerze:
    t: datetime                      # Beginn der Kerze, UTC
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


class Broker(ABC):
    """Alle Methoden werfen BrokerFehler mit verständlichem Text, wenn etwas schiefgeht."""

    name: str = "basis"
    papier: bool = True              # False nur bei echtem Geld

    @abstractmethod
    def konto(self) -> Konto: ...

    @abstractmethod
    def positionen(self) -> list[Position]: ...

    @abstractmethod
    def orders(self, nur_offene: bool = True) -> list[OrderStand]: ...

    @abstractmethod
    def senden(self, order: Order) -> OrderStand: ...

    @abstractmethod
    def stornieren(self, order_id: str) -> None: ...

    @abstractmethod
    def kurs(self, symbol: str) -> Kurs: ...

    @abstractmethod
    def kerzen(self, symbol: str, takt: str = "1m", anzahl: int = 300) -> list[Kerze]:
        """takt: "1m", "5m", "15m", "1h", "1d". Älteste zuerst."""

    def fuehrt_ziel_als_order(self, order: Order) -> bool:
        """True, wenn der Broker das Gewinnziel (take_profit) der Order selbst als Order führt. False: der Autopilot
        muss das Ziel selbst überwachen und bei Erreichen glattstellen (z. B. Kraken: nur der Stop wird angehängt)."""
        return True

    def wechselkurs(self, von: str, nach: str) -> float | None:
        """Wert einer Einheit `von` in der Währung `nach` (z. B. USD -> EUR: 0.92) oder None, wenn der Broker keinen
        Kurs liefert. Gleiche Währung: 1.0. Nie raten: lieber None."""
        return 1.0 if von.upper() == nach.upper() else None

    def alles_schliessen(self, symbole: Iterable[str] | None = None) -> list[str]:
        """Not-Aus: offene Orders stornieren, Positionen zum Markt glattstellen.
        `symbole=None`: das ganze Konto. Mit `symbole` nur diese Symbole (Vergleich über `symbol_schluessel`); alles andere
        wird nur gemeldet ("fremd, nicht angefasst"), nie angefasst. Gibt eine lesbare Liste der Schritte zurück.
        Broker dürfen das effizienter überschreiben."""
        eigene = None if symbole is None else {symbol_schluessel(s) for s in symbole}
        gehoert = (lambda symbol: True) if eigene is None else (lambda symbol: symbol_schluessel(symbol) in eigene)
        schritte = []
        for o in self.orders(nur_offene=True):
            if not gehoert(o.order.symbol):
                schritte.append(f"fremd, nicht angefasst: Order {o.id} {o.order.symbol}")
                continue
            try:
                self.stornieren(o.id)
                schritte.append(f"storniert: {o.id} {o.order.symbol}")
            except BrokerFehler as e:
                schritte.append(f"FEHLER beim Stornieren {o.id}: {e}")
        for p in self.positionen():
            if p.menge:
                if not gehoert(p.symbol):
                    schritte.append(f"fremd, nicht angefasst: Position {p.symbol} {p.menge:+g}")
                    continue
                seite = "verkauf" if p.menge > 0 else "kauf"
                try:
                    s = self.senden(Order(p.symbol, seite, abs(p.menge), notiz="Not-Aus"))
                    if s.status == "abgelehnt":     # nicht als Erfolg melden, sonst wird der Not-Aus nie wiederholt
                        schritte.append(f"FEHLER beim Glattstellen {p.symbol}: Order abgelehnt"
                                        + (f" ({s.meldung})" if s.meldung else ""))
                    else:
                        schritte.append(f"glattgestellt: {p.symbol} {p.menge:+g} ({s.status})")
                except BrokerFehler as e:
                    schritte.append(f"FEHLER beim Glattstellen {p.symbol}: {e}")
        return schritte
