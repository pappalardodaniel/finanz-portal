"""Strategie „ki“: die KI schätzt ein, der Autopilot handelt danach (Papierbetrieb zuerst).

ACHTUNG: Kein Erfolgsversprechen. Dass eine KI-Einschätzung nach Kosten Geld verdient, ist nicht belegt; die einfachen
Regeln im STRATEGIE-BERICHT.md haben es nicht getan. Ob diese Strategie etwas taugt, zeigt allein ein längerer
Papierhandel, in dem das Journal die Trefferquote der KI misst. Ein Backtest mit echter KI ist nicht aussagekräftig
(die KI kennt die Vergangenheit aus ihrem Training) und wird deshalb nicht erlaubt; getestet wird nur mit Fake-Treiber.

So arbeitet sie:
1. Je Symbol wird die KI (`ki.einschaetzen`) höchstens alle `abstand_min` Minuten gefragt (Zwischenspeicher; die Zeit
   ist die Zeit der Lage, also auch im Backtest und in der Wiedergabe die simulierte). Standard: 30 Minuten bei Kerzen
   unter einem Tag, 6 Stunden bei Tageskerzen. Jede Antwort landet im Journal (Trefferquote).
2. Horizont `kurz` (Daytrading: die 1-Tages-Einschätzung, Kerzen unter einer Stunde werden für die KI zu Stundenkerzen
   verdichtet) oder `mittel` (Swing: die 3-Monats-Einschätzung auf Tageskerzen).
3. „steigt“ wird ein Long-, „faellt“ ein Short-Signal (nur wenn `risiko.short_erlaubt` an ist), „seitwaerts“ nichts. Die
   Wahrscheinlichkeit der KI muss mindestens `min_wahrscheinlichkeit` betragen.
4. Stop und Ziel kommen aus der ATR (`stop_atr`, `ziel_atr`). Der erwartete Weg (Abstand zum Ziel) muss die Kosten
   (Gebühren, Schlupf, Spread aus den Einstellungen `kosten`) um den Faktor `kosten_faktor` übersteigen, und die
   Erwartung nach Kosten (p mal Ziel minus (1 − p) mal Stop minus Kosten) muss positiv sein. Annahme dabei: die
   KI-Wahrscheinlichkeit gilt auch für „Ziel vor Stop“ – das ist nicht geprüft.
5. Jede Einschätzung wird genau einmal bewertet; danach gibt es kein weiteres Signal, bis die KI neu gefragt wurde.
   Dreht die KI mit Mindestwahrscheinlichkeit gegen eine offene Position, kommt „flat“.
6. KI nicht erreichbar, Antwort ungültig, Aufrufgrenze erreicht → kein Signal (und ein Vermerk im Journal).

Die Strategie sendet nie selbst Orders. Die Risikoprüfung des Autopiloten (risiko.py) gilt vor jeder Order unverändert.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .. import ki as _ki
from .. import osiris as _osiris
from ..broker.basis import Kerze
from ..hilfen import takt_dauer, zahl, zu_utc
from ..journal import Journal
from ..risiko import Lage
from .basis import Signal, Strategie
from .beispiele import _atr

HORIZONTE = ("kurz", "mittel")
FRAGE_ABSTAND_FEHLER = timedelta(minutes=10)      # nach einem KI-Fehler nicht öfter als alle 10 Minuten neu fragen


class KiStrategieFehler(ValueError):
    """Die Strategie „ki“ ist falsch benutzt (z. B. im Backtest ohne Fake-Treiber). Text ist für Menschen lesbar."""


def verdichten(kerzen: list[Kerze], ziel: timedelta) -> list[Kerze]:
    """Fasst kürzere Kerzen zu Kerzen der Länge `ziel` zusammen. Die Gruppen laufen vom Ende der letzten Kerze rückwärts,
    die jüngste Gruppe ist also voll und endet genau mit der letzten Kerze. Die älteste Gruppe fällt weg, wenn sie unvollständig ist.
    Ist die Kerzenlänge schon so lang wie `ziel` (oder fast), kommen die Kerzen unverändert zurück."""
    if len(kerzen) < 2:
        return list(kerzen)
    takt = _ki._takt_sekunden(kerzen)
    ziel_s = ziel.total_seconds()
    if ziel_s <= takt * 1.5:
        return list(kerzen)
    ende = zu_utc(kerzen[-1].t).timestamp() + takt
    gruppen: dict[int, list[Kerze]] = {}
    for k in kerzen:
        gruppen.setdefault(int((ende - (zu_utc(k.t).timestamp() + takt)) // ziel_s), []).append(k)
    ergebnis = []
    for idx in sorted(gruppen, reverse=True):
        g = gruppen[idx]
        beginn = ende - (idx + 1) * ziel_s
        ergebnis.append((beginn, Kerze(datetime.fromtimestamp(beginn, timezone.utc), g[0].o, max(k.h for k in g),
                                       min(k.l for k in g), g[-1].c, sum(k.v for k in g))))
    if ergebnis and zu_utc(gruppen[max(gruppen)][0].t).timestamp() > ergebnis[0][0] + takt / 2:
        ergebnis.pop(0)                               # älteste Gruppe beginnt zu spät: unvollständig
    return [k for _, k in ergebnis]


def kosten_prozent(einst: dict, gesamtwert: float, stop_prozent: float) -> float:
    """Geschätzte Kosten eines Hin- und Rückwegs in % vom Positionswert: Gebühren und Schlupf zweimal, der Spread einmal
    (Kauf zum Ask, Verkauf zum Bid), dazu die feste Gebühr zweimal, bezogen auf den Positionswert, den die
    Risikoregeln bei diesem Stop erlauben würden. Alles Taker; ein Ziel als Limit-Order wäre etwas billiger."""
    k, r = einst["kosten"], einst["risiko"]
    wert = gesamtwert * r["position_max_prozent"] / 100
    if stop_prozent > 0 and r["risiko_pro_trade_prozent"] > 0:
        wert = min(wert, gesamtwert * r["risiko_pro_trade_prozent"] / 100 / (stop_prozent / 100))
    fest = 2 * k["gebuehr_fest"] / wert * 100 if wert > 0 else float("inf")
    return 2 * k["gebuehr_prozent"] + 2 * k["schlupf_prozent"] + k["spread_prozent"] + fest


def _sauber(text: str, grenze: int = 220) -> str:
    """Freitext der KI für Notizen: eine Zeile, gekürzt. ' zu P' wird entschärft (so erkennt der Autopilot Schutzorders)."""
    t = re.sub(r"\s+", " ", text or "").strip().replace(" zu P", " zu p")
    return t if len(t) <= grenze else t[:grenze - 1].rstrip() + "…"


class KiStrategie(Strategie):
    name = "ki"
    beschreibung = "KI schätzt ein, der Autopilot handelt danach (Papierbetrieb zuerst, kein Erfolgsversprechen)"
    parameter = {
        "horizont": "kurz",                  # "kurz" (1-Tages-Einschätzung, Daytrading) oder "mittel" (3 Monate, Swing)
        "min_wahrscheinlichkeit": 0.6,       # darunter kein Signal
        "abstand_min": 0,                    # so viele Minuten gilt eine Einschätzung; 0 = automatisch (30 bzw. 360 bei Tageskerzen)
        "atr_laenge": 14,
        "stop_atr": 1.5,                     # Stop-Abstand in ATR (der Verdichtung, bei „kurz“ also Stunden-ATR)
        "ziel_atr": 3.0,                     # Ziel-Abstand in ATR = erwarteter Weg
        "kosten_faktor": 3.0,                # der Weg zum Ziel muss die Kosten mindestens so oft enthalten
        "ausstieg_bei_umkehr": True,         # dreht die KI mit Mindestwahrscheinlichkeit gegen die Position: schließen
        "kontext_takt": "auto",              # Kerzenlänge für die KI: "auto" (kurz: 1h, mittel: 1d), "" = wie geliefert, oder z. B. "4h"
        "max_aufrufe_pro_tag": 0,            # 0 = unbegrenzt (Standard); auf Wunsch eine Zahl als Kostenbremse je Kalendertag
        "max_aufrufe": 0,                    # und insgesamt je Lauf (0 = unbegrenzt)
    }
    gitter: dict = {}                        # nichts durchprobieren: jeder Versuch würde die KI fragen

    def __init__(self, treiber=None, osiris_lage=None, historie=None, **parameter):
        """`treiber`: Funktion Prompt -> Text (nur für Tests; sonst gilt `ki.treiber` aus den Einstellungen).
        `osiris_lage`: feste OSIRIS-Lage (Tests); `historie`: Ersatz für kitrader.historie (Tests)."""
        super().__init__(**parameter)
        p = self.p
        if p["horizont"] not in HORIZONTE:
            raise ValueError(f"ki: horizont muss {HORIZONTE} sein, nicht {p['horizont']!r}")
        if not 0 <= p["min_wahrscheinlichkeit"] <= 1:
            raise ValueError("ki: min_wahrscheinlichkeit muss zwischen 0 und 1 liegen")
        for schluessel in ("stop_atr", "ziel_atr", "atr_laenge"):
            if not p[schluessel] > 0:
                raise ValueError(f"ki: {schluessel} muss größer als 0 sein")
        for schluessel in ("abstand_min", "kosten_faktor", "max_aufrufe_pro_tag", "max_aufrufe"):
            if p[schluessel] < 0:
                raise ValueError(f"ki: {schluessel} darf nicht negativ sein")
        if p["kontext_takt"] not in ("auto", ""):
            takt_dauer(p["kontext_takt"])             # wirft ValueError bei Unsinn
        self._treiber, self._osiris_fest, self._historie = treiber, osiris_lage, historie
        self._journal: Journal | None = None
        self._ordner = None
        self._modus = "papier"
        self._einst: dict | None = None
        self._gebunden = False
        self._frage = None                            # gezählter Treiber
        self._heute = ""                              # Kalendertag (Zeit der Lage) für die Tagesgrenze
        self.aufrufe = 0                              # tatsächliche Treiber-Aufrufe (auch Nachfragen)
        self._aufrufe_tag: dict[str, int] = {}
        self._stand: dict[str, dict] = {}             # Symbol -> {"zeit", "prognose", "preis", "fehler", "genutzt"}
        self.letzte: dict[str, str] = {}              # Symbol -> letzter Vermerk (zur Anzeige)

    # ------------------------------------------------------------ Anbindung an den Autopiloten

    def anbinden(self, journal: Journal, datenordner, modus: str, einst: dict) -> None:
        """Vom Autopiloten gerufen: Journal (Einschätzungen und Vermerke), Datenordner (OSIRIS) und Betriebsart.
        Prüft früh, dass ein KI-Treiber eingestellt ist."""
        self._journal, self._ordner, self._modus, self._einst, self._gebunden = journal, datenordner, modus, einst, True
        self._treiber_holen(einst)

    def _treiber_holen(self, einst: dict):
        """Der Treiber, mit Zähler und Aufrufgrenzen. Ohne eingestellten Treiber (ki.treiber = "aus") ein klarer Fehler."""
        if self._frage is not None:
            return self._frage
        try:
            name, frage = _ki._treiber_waehlen(self._treiber, einst)
        except _ki.KiFehler as e:
            raise KiStrategieFehler(str(e)) from None
        if frage is None:
            raise KiStrategieFehler("Die Strategie „ki“ braucht einen KI-Treiber (ki.treiber = \"claude_cli\" oder \"anthropic\"), "
                                    "nicht \"aus\".")

        def gezaehlt(prompt: str) -> str:
            heute = self._heute
            grenze_lauf, grenze_tag = int(self.p["max_aufrufe"]), int(self.p["max_aufrufe_pro_tag"])
            if grenze_lauf and self.aufrufe >= grenze_lauf:
                raise _ki.KiFehler(f"KI-Aufrufgrenze erreicht (strategie.ki.max_aufrufe = {grenze_lauf}); keine weiteren Aufrufe.")
            if grenze_tag and self._aufrufe_tag.get(heute, 0) >= grenze_tag:
                raise _ki.KiFehler(f"KI-Aufrufgrenze für heute erreicht (strategie.ki.max_aufrufe_pro_tag = {grenze_tag}).")
            self.aufrufe += 1
            self._aufrufe_tag[heute] = self._aufrufe_tag.get(heute, 0) + 1
            return frage(prompt)

        gezaehlt.__name__ = name
        self._frage = gezaehlt
        return gezaehlt

    # ------------------------------------------------------------ Hilfen

    def _ki_takt(self) -> timedelta | None:
        """In welche Kerzenlänge die Kerzen für die KI verdichtet werden (None = unverändert)."""
        wahl = self.p["kontext_takt"]
        if wahl == "":
            return None
        if wahl == "auto":
            return timedelta(hours=1) if self.p["horizont"] == "kurz" else timedelta(days=1)
        return takt_dauer(wahl)

    def _abstand(self, kerzen: list[Kerze]) -> timedelta:
        if self.p["abstand_min"]:
            return timedelta(minutes=self.p["abstand_min"])
        tag = _ki._takt_sekunden(kerzen) >= 86400
        return timedelta(hours=6) if tag else timedelta(minutes=30)

    def _notieren(self, symbol: str, text: str) -> None:
        self.letzte[symbol] = text
        if self._journal is not None:
            self._journal.entscheidung(self._modus, symbol, self.name, None, None, None, text)

    def _abfragen(self, symbol: str, kerzen: list[Kerze], lage: Lage) -> dict:
        """Zwischenspeicher je Symbol: die letzte Einschätzung, solange sie jünger als der Abstand ist (nach einem Fehler
        höchstens 10 Minuten). Sonst wird die KI gefragt."""
        alt = self._stand.get(symbol)
        gueltig = self._abstand(kerzen)
        if alt is not None:
            alter = zu_utc(lage.jetzt) - alt["zeit"]
            if alter < (gueltig if alt["prognose"] else min(gueltig, FRAGE_ABSTAND_FEHLER)):
                return alt
        einst = self._einst or lage.einstellungen
        frage = self._treiber_holen(einst)
        self._heute = zu_utc(lage.jetzt).date().isoformat()
        eintrag = {"zeit": zu_utc(lage.jetzt), "prognose": None, "preis": kerzen[-1].c, "fehler": "", "genutzt": False}
        osiris_lage = self._osiris_fest
        if osiris_lage is None and not self._gebunden:           # Backtest: nie das Netz oder fremde Daten anfassen
            osiris_lage = _osiris.Lage(None, None, "OSIRIS: im Backtest nicht verfügbar.")
        journal = self._journal or Journal(":memory:", einst)
        try:
            e = _ki.einschaetzen(symbol, kerzen, treiber=frage, einstellungen=einst, journal=journal, lage=osiris_lage,
                                 historie=self._historie, jetzt=lage.jetzt, ordner=self._ordner,
                                 speichern=self._journal is not None)
            eintrag["prognose"] = e.prognosen.get(self.p["horizont"])
        except _ki.KiFehler as fehler:
            eintrag["fehler"] = str(fehler)
        finally:
            if self._journal is None:
                journal.beenden()
        self._stand[symbol] = eintrag
        return eintrag

    # ------------------------------------------------------------ Bewertung

    def bewerten(self, kerzen: list[Kerze], lage: Lage) -> Signal | None:
        if not self._gebunden and self._treiber is None:
            raise KiStrategieFehler("Die Strategie „ki“ läuft nur im Autopiloten (Papierbetrieb zuerst). Ein Backtest mit echter KI "
                                    "kostet viele Aufrufe und sagt nichts, weil die KI die Vergangenheit kennt; in Tests gibt "
                                    "treiber=<Funktion> eine Antwort vor.")
        if not kerzen:
            return None
        symbol = lage.symbol
        einst = self._einst or lage.einstellungen
        ziel = self._ki_takt()
        ansicht = verdichten(kerzen, ziel) if ziel else list(kerzen)
        atr = _atr(ansicht, int(self.p["atr_laenge"]))
        preis = kerzen[-1].c
        if not atr or preis <= 0 or len(ansicht) < 2:
            return None                                          # zu wenige Kerzen: die KI wird gar nicht erst gefragt
        eintrag = self._abfragen(symbol, ansicht, lage)
        if eintrag["genutzt"]:
            return None                                          # diese Einschätzung wurde schon bewertet
        eintrag["genutzt"] = True
        signal, text = self._entscheiden(eintrag, preis, atr, lage, einst)
        if signal is None or signal.richtung == "flat":
            self._notieren(symbol, text)
        else:
            self.letzte[symbol] = text
        return signal

    def _entscheiden(self, eintrag: dict, preis: float, atr: float, lage: Lage, einst: dict) -> tuple[Signal | None, str]:
        p = self.p
        prog = eintrag["prognose"]
        if prog is None:
            return None, f"KI nicht verfügbar, kein Signal ({_sauber(eintrag['fehler'], 200)})"
        ki_text = f"KI ({p['horizont']}): {prog.richtung} {prog.wahrscheinlichkeit:.2f}"
        richtung = {"steigt": "long", "faellt": "short"}.get(prog.richtung)
        sicher = prog.wahrscheinlichkeit >= p["min_wahrscheinlichkeit"]
        menge = lage.menge()
        if menge:                                                # offene Position: höchstens schließen, nie aufstocken
            gegen = richtung is not None and (richtung == "long") != (menge > 0)
            if p["ausstieg_bei_umkehr"] and gegen and sicher:
                return (Signal("flat", begruendung=f"{ki_text} gegen die offene Position: schließen"),
                        f"{ki_text}: dreht gegen die offene Position, schließen")
            return None, f"{ki_text}: Position bleibt (Stop und Ziel gelten)"
        if richtung is None:
            return None, f"{ki_text}: keine Richtung, kein Signal"
        if not sicher:
            return None, f"{ki_text} unter Mindestwahrscheinlichkeit {p['min_wahrscheinlichkeit']:.2f}: kein Signal"
        if richtung == "short" and not einst["risiko"]["short_erlaubt"]:
            return None, f"{ki_text}: Short ist verboten (risiko.short_erlaubt = false), kein Signal"
        vz = 1 if richtung == "long" else -1
        stop_abstand, ziel_abstand = p["stop_atr"] * atr, p["ziel_atr"] * atr
        stop_pct, ziel_pct = stop_abstand / preis * 100, ziel_abstand / preis * 100
        kosten = kosten_prozent(einst, lage.gesamtwert, stop_pct)
        netto = prog.wahrscheinlichkeit * ziel_pct - (1 - prog.wahrscheinlichkeit) * stop_pct - kosten
        zahlen = (f"Weg {zahl(ziel_pct)} % ({p['ziel_atr']:g} ATR), Stop {zahl(stop_pct)} %, Kosten {zahl(kosten)} %, "
                  f"Erwartung nach Kosten {zahl(netto)} %")
        if ziel_pct < p["kosten_faktor"] * kosten:
            return None, f"{ki_text}: Weg zu klein gegen die Kosten (nötig {p['kosten_faktor']:g}-fach), kein Signal. {zahlen}"
        if netto <= 0:
            return None, f"{ki_text}: Erwartung nach Kosten nicht positiv, kein Signal. {zahlen}"
        begruendung = _sauber(f"{ki_text}. {prog.begruendung} | {zahlen}", 400)
        return Signal(richtung, preis, preis - vz * stop_abstand, preis + vz * ziel_abstand,
                      prog.wahrscheinlichkeit, begruendung), begruendung
