"""Fünf übliche Handelsansätze als KANDIDATEN für das Strategie-Labor (STRATEGIE-BERICHT.md).

ACHTUNG: Das sind Kandidaten zum Prüfen, keine bewährten Systeme und kein Erfolgsversprechen. Sie sind so einfach und
so gängig gebaut, wie man sie in Büchern findet; ob sie nach Kosten etwas taugen, zeigt allein das Labor
(`python3 -m kitrader labor`) auf echten Kursen, und selbst das nur für den geprüften Zeitraum. Ein Kandidat kommt erst
in den Papierhandel, wenn der Bericht ihn dafür vorschlägt.

Alle Kandidaten:
- sehen nur abgeschlossene Kerzen (die letzte der Liste ist die jüngste) und senden nie selbst Orders;
- verlangen einen Stop-Loss in ATR-Vielfachen (ATR = mittlere Schwankungsbreite einer Kerze) und setzen ein Ziel in
  Vielfachen des Risikos (`crv`), außer der Ansatz verlässt die Position über ein Gegensignal ("flat");
- melden Long- und Short-Signale; ob Short überhaupt gehandelt wird, entscheidet die Risikoprüfung
  (`risiko.short_erlaubt`, Standard aus);
- sind Zeitrahmen-unabhängig (Schwellen in ATR statt in Prozent), laufen also auf 5 Minuten wie auf 1 Stunde.
"""
from __future__ import annotations

from ..broker.basis import Kerze
from ..risiko import Lage
from .basis import Signal, Strategie
from .beispiele import _atr

HINWEIS_KANDIDAT = "KANDIDAT, ungeprüft, kein Erfolgsversprechen"


def _mittel(werte) -> float:
    werte = list(werte)
    return sum(werte) / len(werte)


def _schluesse(kerzen: list[Kerze], n: int) -> list[float]:
    return [k.c for k in kerzen[-n:]]


def rsi(schluesse: list[float], laenge: int = 14) -> float | None:
    """RSI nach Wilder (0..100). Braucht mindestens laenge + 1 Schlusskurse; je mehr, desto besser eingeschwungen."""
    if laenge < 1 or len(schluesse) < laenge + 1:
        return None
    aenderungen = [b - a for a, b in zip(schluesse, schluesse[1:])]
    gewinn = _mittel(max(x, 0.0) for x in aenderungen[:laenge])
    verlust = _mittel(max(-x, 0.0) for x in aenderungen[:laenge])
    for x in aenderungen[laenge:]:
        gewinn = (gewinn * (laenge - 1) + max(x, 0.0)) / laenge
        verlust = (verlust * (laenge - 1) + max(-x, 0.0)) / laenge
    if verlust == 0:
        return 100.0 if gewinn > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gewinn / verlust)


def _long_short(richtung: str, k: Kerze, atr: float, stop_atr: float, crv: float, sicherheit: float,
                text: str) -> Signal:
    """Long/Short mit Stop `stop_atr` mal ATR vom Schlusskurs und Ziel `crv` mal Risiko."""
    risiko = stop_atr * atr
    if richtung == "long":
        return Signal("long", k.c, k.c - risiko, k.c + crv * risiko, sicherheit, text)
    return Signal("short", k.c, k.c + risiko, k.c - crv * risiko, sicherheit, text)


class Donchian(Strategie):
    """Donchian-Ausbruch mit ATR-Stop (KANDIDAT): Schließt eine Kerze über dem höchsten Hoch der letzten `kanal` Kerzen,
    gibt es ein Long-Signal (unter dem tiefsten Tief: Short). Stop `stop_atr` mal ATR, Ziel `crv` mal Risiko."""
    name = "donchian"
    beschreibung = "Donchian-Ausbruch mit ATR-Stop (" + HINWEIS_KANDIDAT + ")"
    parameter = {"kanal": 20, "atr_laenge": 14, "stop_atr": 2.0, "crv": 2.0}
    gitter = {"kanal": [20, 55], "stop_atr": [1.5, 3.0], "crv": [2.0, 4.0]}

    def bewerten(self, kerzen: list[Kerze], lage: Lage):
        if lage.menge() != 0:
            return None
        n = int(self.p["kanal"])
        if len(kerzen) < max(n, int(self.p["atr_laenge"])) + 2:
            return None
        atr = _atr(kerzen, int(self.p["atr_laenge"]))
        if not atr:
            return None
        k, davor = kerzen[-1], kerzen[-n - 1:-1]
        hoch, tief = max(x.h for x in davor), min(x.l for x in davor)
        if k.c > hoch:
            return _long_short("long", k, atr, self.p["stop_atr"], self.p["crv"], min(1.0, 0.5 + (k.c - hoch) / atr * 0.2),
                               f"Schluss {k.c:g} über {n}-Kerzen-Hoch {hoch:g}")
        if k.c < tief:
            return _long_short("short", k, atr, self.p["stop_atr"], self.p["crv"], min(1.0, 0.5 + (tief - k.c) / atr * 0.2),
                               f"Schluss {k.c:g} unter {n}-Kerzen-Tief {tief:g}")
        return None


class BollingerRsi(Strategie):
    """Bollinger-/RSI-Rückkehr zur Mitte (KANDIDAT): Schließt eine Kerze unter dem unteren Bollinger-Band
    (Mittelwert der letzten `laenge` Schlusskurse minus `abw` Standardabweichungen) und ist der RSI höchstens `rsi_max`,
    wird auf die Rückkehr zum Mittelwert gesetzt (Ziel = Mittelwert). Spiegelbildlich Short am oberen Band mit RSI
    mindestens 100 - `rsi_max`. Stop `stop_atr` mal ATR. Liegt das Ziel nicht mindestens `min_crv` mal so weit weg wie der
    Stop, gibt es kein Signal."""
    name = "bollinger_rsi"
    beschreibung = "Bollinger-/RSI-Rückkehr zur Mitte (" + HINWEIS_KANDIDAT + ")"
    parameter = {"laenge": 20, "abw": 2.0, "rsi_laenge": 14, "rsi_max": 30.0, "atr_laenge": 14, "stop_atr": 2.0,
                 "min_crv": 0.5}
    gitter = {"abw": [1.5, 2.0, 2.5], "rsi_max": [25.0, 35.0], "stop_atr": [1.5, 3.0]}

    def bewerten(self, kerzen: list[Kerze], lage: Lage):
        if lage.menge() != 0:
            return None
        n, rl = int(self.p["laenge"]), int(self.p["rsi_laenge"])
        if len(kerzen) < max(n, int(self.p["atr_laenge"]) + 1, rl * 4):
            return None
        atr = _atr(kerzen, int(self.p["atr_laenge"]))
        if not atr:
            return None
        schl = _schluesse(kerzen, n)
        mitte = _mittel(schl)
        abw = (sum((x - mitte) ** 2 for x in schl) / n) ** 0.5
        k = kerzen[-1]
        if abw <= 0:
            return None
        r = rsi(_schluesse(kerzen, rl * 4 + 1), rl)
        if r is None:
            return None
        unten, oben = mitte - self.p["abw"] * abw, mitte + self.p["abw"] * abw
        stop_abstand = self.p["stop_atr"] * atr
        if k.c < unten and r <= self.p["rsi_max"] and mitte - k.c >= self.p["min_crv"] * stop_abstand:
            return Signal("long", k.c, k.c - stop_abstand, mitte, min(1.0, 0.4 + (self.p["rsi_max"] - r) / 100),
                          f"Schluss {k.c:g} unter Bollinger-Band {unten:g}, RSI {r:.0f}, Ziel Mitte {mitte:g}")
        if k.c > oben and r >= 100 - self.p["rsi_max"] and k.c - mitte >= self.p["min_crv"] * stop_abstand:
            return Signal("short", k.c, k.c + stop_abstand, mitte, min(1.0, 0.4 + (r - 100 + self.p["rsi_max"]) / 100),
                          f"Schluss {k.c:g} über Bollinger-Band {oben:g}, RSI {r:.0f}, Ziel Mitte {mitte:g}")
        return None


class MaTrend(Strategie):
    """Gleitende Durchschnitte mit Trendfilter (KANDIDAT): Kreuzt der schnelle Durchschnitt (`schnell` Kerzen) den
    langsamen (`langsam`) von unten nach oben und liegt der Schlusskurs über dem Trendfilter-Durchschnitt (`filter`
    Kerzen), gibt es ein Long-Signal (Short spiegelbildlich). Stop `stop_atr` mal ATR, Ziel `crv` mal Risiko; kreuzen die
    Durchschnitte zurück, während die Position offen ist, kommt ein "flat"-Signal."""
    name = "ma_trend"
    beschreibung = "Gleitende Durchschnitte mit Trendfilter (" + HINWEIS_KANDIDAT + ")"
    parameter = {"schnell": 9, "langsam": 24, "filter": 100, "atr_laenge": 14, "stop_atr": 2.0, "crv": 3.0}
    gitter = {"schnell": [8, 12], "langsam": [24, 48], "stop_atr": [2.0, 3.0]}

    def bewerten(self, kerzen: list[Kerze], lage: Lage):
        s, l, f = int(self.p["schnell"]), int(self.p["langsam"]), int(self.p["filter"])
        if s >= l or len(kerzen) < max(l, f, int(self.p["atr_laenge"]) + 1) + 1:
            return None
        schl = [x.c for x in kerzen[-max(l, f) - 1:]]
        schnell_jetzt, schnell_vor = _mittel(schl[-s:]), _mittel(schl[-s - 1:-1])
        langsam_jetzt, langsam_vor = _mittel(schl[-l:]), _mittel(schl[-l - 1:-1])
        menge = lage.menge()
        if menge != 0:
            if menge > 0 and schnell_jetzt < langsam_jetzt:
                return Signal("flat", begruendung="schneller Durchschnitt unter den langsamen: Long beenden")
            if menge < 0 and schnell_jetzt > langsam_jetzt:
                return Signal("flat", begruendung="schneller Durchschnitt über den langsamen: Short beenden")
            return None
        atr = _atr(kerzen, int(self.p["atr_laenge"]))
        if not atr:
            return None
        k = kerzen[-1]
        trend = _mittel(schl[-f:])
        if schnell_vor <= langsam_vor and schnell_jetzt > langsam_jetzt and k.c > trend:
            return _long_short("long", k, atr, self.p["stop_atr"], self.p["crv"], 0.6,
                               f"Durchschnitte {s}/{l} kreuzen nach oben, Kurs über Trendfilter {trend:g}")
        if schnell_vor >= langsam_vor and schnell_jetzt < langsam_jetzt and k.c < trend:
            return _long_short("short", k, atr, self.p["stop_atr"], self.p["crv"], 0.6,
                               f"Durchschnitte {s}/{l} kreuzen nach unten, Kurs unter Trendfilter {trend:g}")
        return None


class Momentum(Strategie):
    """Momentum über N Kerzen (KANDIDAT): Ist der Kurs in den letzten `n` Kerzen um mindestens `schwelle_atr` ATR
    gestiegen und schließt die letzte Kerze im Plus, gibt es ein Long-Signal (Short spiegelbildlich bei Kursfall).
    Stop `stop_atr` mal ATR, Ziel `crv` mal Risiko."""
    name = "momentum"
    beschreibung = "Momentum über N Kerzen (" + HINWEIS_KANDIDAT + ")"
    parameter = {"n": 24, "schwelle_atr": 6.0, "atr_laenge": 14, "stop_atr": 2.0, "crv": 2.0}
    gitter = {"n": [12, 36], "schwelle_atr": [4.0, 8.0], "stop_atr": [1.5, 3.0]}

    def bewerten(self, kerzen: list[Kerze], lage: Lage):
        if lage.menge() != 0:
            return None
        n = int(self.p["n"])
        if len(kerzen) < max(n, int(self.p["atr_laenge"])) + 2:
            return None
        atr = _atr(kerzen, int(self.p["atr_laenge"]))
        if not atr:
            return None
        k = kerzen[-1]
        bewegung = (k.c - kerzen[-n - 1].c) / atr
        if bewegung >= self.p["schwelle_atr"] and k.c > k.o:
            return _long_short("long", k, atr, self.p["stop_atr"], self.p["crv"], min(1.0, 0.4 + bewegung / 40),
                               f"+{bewegung:.1f} ATR in {n} Kerzen")
        if bewegung <= -self.p["schwelle_atr"] and k.c < k.o:
            return _long_short("short", k, atr, self.p["stop_atr"], self.p["crv"], min(1.0, 0.4 - bewegung / 40),
                               f"{bewegung:.1f} ATR in {n} Kerzen")
        return None


class VolumenAusbruch(Strategie):
    """Volumen-Ausbruch (KANDIDAT): Wie der Donchian-Ausbruch über das Hoch (bzw. unter das Tief) der letzten `kanal`
    Kerzen, aber nur, wenn der Umsatz der Ausbruchskerze mindestens `vol_faktor` mal so hoch ist wie der Durchschnitt der
    `kanal` Kerzen davor. Stop `stop_atr` mal ATR, Ziel `crv` mal Risiko. Ohne Umsatzdaten kein Signal."""
    name = "volumen"
    beschreibung = "Volumen-Ausbruch (" + HINWEIS_KANDIDAT + ")"
    parameter = {"kanal": 20, "vol_faktor": 2.5, "atr_laenge": 14, "stop_atr": 2.0, "crv": 2.0}
    gitter = {"kanal": [20, 50], "vol_faktor": [2.0, 3.5], "stop_atr": [1.5, 3.0]}

    def bewerten(self, kerzen: list[Kerze], lage: Lage):
        if lage.menge() != 0:
            return None
        n = int(self.p["kanal"])
        if len(kerzen) < max(n, int(self.p["atr_laenge"])) + 2:
            return None
        k, davor = kerzen[-1], kerzen[-n - 1:-1]
        schnitt = _mittel(x.v for x in davor)
        if schnitt <= 0 or k.v < self.p["vol_faktor"] * schnitt:
            return None
        atr = _atr(kerzen, int(self.p["atr_laenge"]))
        if not atr:
            return None
        hoch, tief = max(x.h for x in davor), min(x.l for x in davor)
        sicherheit = min(1.0, 0.4 + 0.1 * k.v / schnitt)
        if k.c > hoch:
            return _long_short("long", k, atr, self.p["stop_atr"], self.p["crv"], sicherheit,
                               f"Ausbruch über {hoch:g} mit {k.v / schnitt:.1f}-fachem Umsatz")
        if k.c < tief:
            return _long_short("short", k, atr, self.p["stop_atr"], self.p["crv"], sicherheit,
                               f"Ausbruch unter {tief:g} mit {k.v / schnitt:.1f}-fachem Umsatz")
        return None


KANDIDATEN: tuple[type[Strategie], ...] = (Donchian, BollingerRsi, MaTrend, Momentum, VolumenAusbruch)
