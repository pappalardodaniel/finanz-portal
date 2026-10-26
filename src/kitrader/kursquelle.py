"""Kursquellen und Broker-Auswahl für Papierhandel, Demo und Echt.

Eine Kursquelle liefert `kurs(symbol)` und `kerzen(symbol, takt, anzahl)`. Das kann ein echter Broker im
Nur-Lese-Betrieb sein (`BrokerQuelle`) oder eine Wiedergabe aus Kerzen (`WiedergabeQuelle`, für Tests und
zum Ausprobieren ohne Netz).
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta

from .broker.basis import Broker, BrokerFehler, Kerze, Kurs
from .hilfen import takt_dauer, zu_utc


class BrokerQuelle:
    """Nutzt einen echten Broker nur zum Lesen (Kurse, Kerzen). Es werden nie Orders gesendet."""

    def __init__(self, broker: Broker):
        self.broker = broker

    def kurs(self, symbol: str) -> Kurs:
        return self.broker.kurs(symbol)

    def kerzen(self, symbol: str, takt: str = "1m", anzahl: int = 300) -> list[Kerze]:
        return self.broker.kerzen(symbol, takt, anzahl)

    def wechselkurs(self, von: str, nach: str) -> float | None:
        return self.broker.wechselkurs(von, nach)


class WiedergabeQuelle:
    """Spielt vorhandene Kerzen Schritt für Schritt ab. Die Uhr steht immer am Ende der aktuellen Kerze.
    Mit `weiter()` springt sie zur nächsten Kerze (False, wenn keine mehr da ist)."""

    def __init__(self, kerzen_je_symbol: dict[str, list[Kerze]], takt: str = "5m", spread_prozent: float = 0.05,
                 start: int = 0):
        self.daten = {s: sorted(k, key=lambda x: x.t) for s, k in kerzen_je_symbol.items()}
        self.takt = takt
        self.dauer = takt_dauer(takt)
        self.s2 = spread_prozent / 200
        self.i = start
        self.laenge = max(len(k) for k in self.daten.values())

    def jetzt(self) -> datetime:
        """Ende der aktuellen Kerze (die Kerze gilt dann als abgeschlossen)."""
        kerzen = next(iter(self.daten.values()))
        return zu_utc(kerzen[min(self.i, len(kerzen) - 1)].t) + self.dauer

    def weiter(self, *_) -> bool:
        if self.i + 1 >= self.laenge:
            return False
        self.i += 1
        return True

    def kurs(self, symbol: str) -> Kurs:
        k = self._liste(symbol)[min(self.i, len(self._liste(symbol)) - 1)]
        return Kurs(symbol, k.c * (1 - self.s2), k.c * (1 + self.s2), k.c, zu_utc(k.t) + self.dauer)

    def kerzen(self, symbol: str, takt: str = "5m", anzahl: int = 300) -> list[Kerze]:
        liste = self._liste(symbol)
        return liste[max(0, self.i + 1 - anzahl):self.i + 1]

    def _liste(self, symbol: str) -> list[Kerze]:
        if symbol not in self.daten:
            raise BrokerFehler(f"Wiedergabe: keine Daten für {symbol}")
        return self.daten[symbol]


def broker_aus_einstellungen(einst: dict, demo: bool) -> Broker:
    """Baut den echten Broker (`ibkr` oder `kraken`).

    demo=True: IBKR ohne Erlaubnis für Echtgeld (nur Demokonto "DU..."), Kraken im Modus "nur prüfen".
    demo=False: nur für die Betriebsart echt; der Autopilot ruft das erst nach der Freigabeprüfung auf."""
    name = einst["broker"]
    if name not in ("ibkr", "kraken"):
        raise BrokerFehler(f"Unbekannter Broker {name!r} in den Einstellungen (erlaubt: ibkr, kraken)")
    try:
        modul = importlib.import_module(f"kitrader.broker.{name}")
    except ImportError as e:
        raise BrokerFehler(f"Die Anbindung kitrader.broker.{name} ist nicht vorhanden oder unvollständig: {e}") from e
    if name == "ibkr":
        cfg = einst["ibkr"]
        kw = {k: cfg[k] for k in ("basis_url", "konto_id") if cfg.get(k)}
        return modul.IbkrBroker(echtgeld_erlaubt=not demo, **kw)
    cfg = einst["kraken"]
    return modul.KrakenBroker(nur_pruefen=demo, waehrung=cfg["waehrung"], spot_paare=tuple(cfg["spot_paare"]),
                              hebel=cfg["hebel"] or None)


def quelle_aus_einstellungen(einst: dict, wiedergabe: WiedergabeQuelle | None = None):
    """Kursquelle für den Papierhandel nach Einstellung `kursquelle`."""
    art = einst["kursquelle"]
    if art == "wiedergabe":
        if wiedergabe is None:
            raise BrokerFehler("kursquelle = \"wiedergabe\" braucht Daten (autopilot --wiedergabe DATEI.csv)")
        return wiedergabe
    if art not in ("ibkr", "kraken"):
        raise BrokerFehler(f"Unbekannte kursquelle {art!r} (erlaubt: ibkr, kraken, wiedergabe)")
    return BrokerQuelle(broker_aus_einstellungen({**einst, "broker": art}, demo=True))
