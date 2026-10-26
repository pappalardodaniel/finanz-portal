"""Schnittstelle für Strategien.

Eine Strategie sieht nur abgeschlossene Kerzen (älteste zuerst) und die `Lage`. Sie schlägt ein Signal vor
oder nichts (None). Sie sendet nie selbst Orders: der Autopilot macht daraus eine Order und schickt sie
durch die Risikoprüfung (risiko.py).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..broker.basis import Kerze, Order
from ..risiko import Lage, einstiegspreis, mengenschritt, positionsgroesse, wechselkurs_gueltig

RICHTUNGEN = ("long", "short", "flat")


@dataclass
class Signal:
    richtung: str                    # "long", "short" oder "flat" (Position schließen)
    einstieg: float | None = None
    stop: float | None = None        # Stop-Loss (Pflicht bei long/short)
    ziel: float | None = None        # Take-Profit
    sicherheit: float = 0.5          # 0..1, grobe Einschätzung der Strategie (keine Wahrscheinlichkeit)
    begruendung: str = ""

    def __post_init__(self):
        if self.richtung not in RICHTUNGEN:
            raise ValueError(f"richtung muss {RICHTUNGEN} sein, nicht {self.richtung!r}")
        if not 0 <= self.sicherheit <= 1:
            raise ValueError("sicherheit muss zwischen 0 und 1 liegen")


class Strategie(ABC):
    name = "basis"
    beschreibung = ""
    parameter: dict = {}              # Standardwerte; Einstellungen überschreiben sie
    gitter: dict[str, list] = {}      # Werte, die der Backtest auf dem Trainingsteil durchprobiert

    def __init__(self, **parameter):
        unbekannt = set(parameter) - set(self.parameter)
        if unbekannt:
            raise ValueError(f"{self.name}: unbekannte Parameter {sorted(unbekannt)}")
        self.p = {**self.parameter, **parameter}

    @abstractmethod
    def bewerten(self, kerzen: list[Kerze], lage: Lage) -> Signal | None:
        """Signal für die nächste Kerze oder None. `kerzen`: abgeschlossene Kerzen, älteste zuerst."""


def signal_zu_order(signal: Signal, symbol: str, lage: Lage) -> Order | None:
    """Macht aus einem long/short-Signal eine Marktorder mit Stop-Loss und Take-Profit.
    Menge aus dem Risiko je Trade (risiko_pro_trade_prozent), begrenzt durch die maximale Positionsgröße.
    Kurse in fremder Währung werden mit `lage.wechselkurs` in die Kontowährung umgerechnet.
    None bei flat oder wenn die Menge 0 wird (Stop zu nah/zu weit, Konto zu klein, kein Wechselkurs)."""
    if signal.richtung == "flat" or signal.stop is None or not wechselkurs_gueltig(lage.wechselkurs):
        return None
    einst = lage.einstellungen
    seite = "kauf" if signal.richtung == "long" else "verkauf"
    # mit dem Preis rechnen, den die Risikoprüfung später sieht (Ask bzw. Bid), sonst mit dem Signal-Einstieg
    vorlaeufig = Order(symbol, seite, 1.0)
    preis = einstiegspreis(vorlaeufig, lage.kurs) or signal.einstieg
    if preis is None:
        return None
    menge = positionsgroesse(lage.gesamtwert, einst["risiko"]["risiko_pro_trade_prozent"], preis, signal.stop,
                             einst["risiko"]["position_max_prozent"], mengenschritt(symbol, einst),
                             wechselkurs=lage.wechselkurs)
    if menge <= 0:
        return None
    return Order(symbol, seite, menge, "markt", stop_loss=signal.stop, take_profit=signal.ziel,
                 notiz=signal.begruendung)
