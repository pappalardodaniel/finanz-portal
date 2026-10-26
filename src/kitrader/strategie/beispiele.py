"""Zwei einfache BEISPIEL-Strategien als Ausgangspunkt.

ACHTUNG: Das sind Lehrbeispiele, keine bewährten Handelssysteme. Sie versprechen keinen Gewinn; auf
echten Daten verlieren solche Regeln nach Kosten sehr oft Geld. Erst Backtest (mit zurückgehaltenem
Testteil), dann mehrere Wochen Papierhandel, bevor irgendetwas davon in die Nähe von echtem Geld kommt.
"""
from __future__ import annotations

from ..broker.basis import Kerze
from ..risiko import Lage
from .basis import Signal, Strategie


def _tageskerzen(kerzen: list[Kerze]) -> list[Kerze]:
    """Kerzen des letzten Kalendertags (UTC) der Liste."""
    if not kerzen:
        return []
    tag = kerzen[-1].t.date()
    i = len(kerzen)
    while i > 0 and kerzen[i - 1].t.date() == tag:
        i -= 1
    return kerzen[i:]


def _atr(kerzen: list[Kerze], laenge: int) -> float | None:
    """Mittlere Schwankungsbreite (einfacher Durchschnitt der True Range der letzten `laenge` Kerzen)."""
    if len(kerzen) < laenge + 1:
        return None
    tr = []
    for vorher, k in zip(kerzen[-laenge - 1:-1], kerzen[-laenge:]):
        tr.append(max(k.h - k.l, abs(k.h - vorher.c), abs(k.l - vorher.c)))
    return sum(tr) / laenge


class Eroeffnungsausbruch(Strategie):
    """Opening Range Breakout (Beispiel): Die ersten `range_kerzen` Kerzen des Tages bilden eine Spanne.
    Schließt die erste Kerze danach über dem Hoch (unter dem Tief), gibt es ein Long- (Short-)Signal.
    Stop am anderen Ende der Spanne, Ziel = `crv` mal Risiko. Pro Tag höchstens ein Signal."""
    name = "ausbruch"
    beschreibung = "Eröffnungs-Ausbruch (Beispiel, kein Erfolgsversprechen)"
    parameter = {"range_kerzen": 6, "crv": 2.0, "min_breite_prozent": 0.2}
    gitter = {"range_kerzen": [3, 6, 12], "crv": [1.0, 1.5, 2.0]}

    def bewerten(self, kerzen, lage: Lage):
        if lage.menge() != 0:
            return None
        tag = _tageskerzen(kerzen)
        n = int(self.p["range_kerzen"])
        if len(tag) <= n:
            return None
        spanne, danach = tag[:n], tag[n:]
        hoch, tief = max(k.h for k in spanne), min(k.l for k in spanne)
        breite = hoch - tief
        if tief <= 0 or breite / tief * 100 < self.p["min_breite_prozent"]:
            return None
        # nur der erste Ausbruch des Tages zählt
        if any(k.c > hoch or k.c < tief for k in danach[:-1]):
            return None
        letzte = danach[-1]
        if letzte.c > hoch:
            stop = tief
            return Signal("long", letzte.c, stop, letzte.c + self.p["crv"] * (letzte.c - stop),
                          min(1.0, 0.4 + 0.6 * (letzte.c - hoch) / breite),
                          f"Ausbruch über Spanne {tief:g}-{hoch:g} nach {n} Kerzen")
        if letzte.c < tief:
            stop = hoch
            return Signal("short", letzte.c, stop, letzte.c - self.p["crv"] * (stop - letzte.c),
                          min(1.0, 0.4 + 0.6 * (tief - letzte.c) / breite),
                          f"Ausbruch unter Spanne {tief:g}-{hoch:g} nach {n} Kerzen")
        return None


class VwapRueckkehr(Strategie):
    """Rückkehr zum VWAP (Beispiel): Entfernt sich der Kurs mehr als `abstand_atr` mal ATR vom Tages-VWAP
    und dreht die letzte Kerze zurück, wird auf die Rückkehr zum VWAP gesetzt. Stop `stop_atr` mal ATR
    jenseits des Einstiegs. Ist das Ziel näher als der Stop mal `min_crv`, gibt es kein Signal."""
    name = "vwap"
    beschreibung = "Rückkehr zum VWAP (Beispiel, kein Erfolgsversprechen)"
    parameter = {"abstand_atr": 2.0, "stop_atr": 1.0, "atr_laenge": 14, "min_kerzen": 20, "min_crv": 1.0}
    gitter = {"abstand_atr": [1.5, 2.0, 2.5], "stop_atr": [1.0, 1.5]}

    def bewerten(self, kerzen, lage: Lage):
        if lage.menge() != 0:
            return None
        tag = _tageskerzen(kerzen)
        if len(tag) < self.p["min_kerzen"]:
            return None
        atr = _atr(kerzen, int(self.p["atr_laenge"]))
        if not atr:
            return None
        gewicht = sum(k.v for k in tag)
        if gewicht > 0:
            vwap = sum((k.h + k.l + k.c) / 3 * k.v for k in tag) / gewicht
        else:                                  # ohne Umsatzdaten: gleich gewichtet
            vwap = sum((k.h + k.l + k.c) / 3 for k in tag) / len(tag)
        k = tag[-1]
        abstand = (k.c - vwap) / atr
        if abstand <= -self.p["abstand_atr"] and k.c > k.o:     # weit unter VWAP, Kerze dreht nach oben
            stop = k.c - self.p["stop_atr"] * atr
            if vwap - k.c >= self.p["min_crv"] * (k.c - stop):
                return Signal("long", k.c, stop, vwap, min(1.0, 0.4 + 0.1 * (-abstand - self.p["abstand_atr"] + 1)),
                              f"{-abstand:.1f} ATR unter VWAP {vwap:.2f}, Kerze dreht nach oben")
        if abstand >= self.p["abstand_atr"] and k.c < k.o:      # weit über VWAP, Kerze dreht nach unten
            stop = k.c + self.p["stop_atr"] * atr
            if k.c - vwap >= self.p["min_crv"] * (stop - k.c):
                return Signal("short", k.c, stop, vwap, min(1.0, 0.4 + 0.1 * (abstand - self.p["abstand_atr"] + 1)),
                              f"{abstand:.1f} ATR über VWAP {vwap:.2f}, Kerze dreht nach unten")
        return None
