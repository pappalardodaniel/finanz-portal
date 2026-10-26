"""Gemeinsame Testhilfen: erfundene Kerzen, fester Kursgeber, einfache Uhr. Keine echten Daten, kein Netz."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from kitrader.broker.basis import BrokerFehler, Kerze, Kurs

UTC = timezone.utc
MONTAG = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)     # 2. März 2026 ist ein Montag


class Uhr:
    """Steuerbare Uhr: uhr() liefert die eingestellte Zeit."""

    def __init__(self, start: datetime = MONTAG):
        self.jetzt = start

    def __call__(self) -> datetime:
        return self.jetzt

    def weiter(self, **kw) -> None:
        self.jetzt += timedelta(**kw)


class Markt:
    """Fester Kursgeber: Kurse werden von Hand gesetzt."""

    def __init__(self, uhr: Uhr):
        self.uhr = uhr
        self.kurse: dict[str, tuple[float, float]] = {}

    def setze(self, symbol: str, bid: float, ask: float | None = None) -> None:
        self.kurse[symbol] = (bid, bid + 0.1 if ask is None else ask)

    def __call__(self, symbol: str) -> Kurs:
        if symbol not in self.kurse:
            raise BrokerFehler(f"kein Kurs für {symbol}")
        bid, ask = self.kurse[symbol]
        return Kurs(symbol, bid, ask, (bid + ask) / 2, self.uhr())


def zufallskerzen(tage: int = 30, seed: int = 7, start: datetime = MONTAG, takt_min: int = 5,
                  pro_tag: int = 78, tagesdrift: float = 0.0004) -> list[Kerze]:
    """Erfundene Kerzen: Zufallsbewegung, aber mit zufälligem Trend je Tag (damit überhaupt Signale entstehen)."""
    rng = random.Random(seed)
    kurs = 100.0
    kerzen: list[Kerze] = []
    tag = start
    for _ in range(tage):
        while tag.weekday() >= 5:
            tag += timedelta(days=1)
        kurs *= 1 + rng.gauss(0, 0.004)
        drift = rng.choice([-1, 0, 1]) * tagesdrift
        for i in range(pro_tag):
            o = kurs
            c = o * (1 + drift + rng.gauss(0, 0.0015))
            h = max(o, c) * (1 + abs(rng.gauss(0, 0.0007)))
            l = min(o, c) * (1 - abs(rng.gauss(0, 0.0007)))
            kerzen.append(Kerze(tag + timedelta(minutes=takt_min * i), o, h, l, c, rng.randint(800, 3000)))
            kurs = c
        tag += timedelta(days=1)
    return kerzen


def flache_kerze(t: datetime, preis: float, **kw) -> Kerze:
    return Kerze(t, kw.get("o", preis), kw.get("h", preis), kw.get("l", preis), kw.get("c", preis), kw.get("v", 1000))
