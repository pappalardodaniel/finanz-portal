"""Langfristige Regeln auf Tages- und Monatsreihen: Signale, Kostenrechnung, Kennzahlen, Walk-forward und Zufallsvergleich.

Das ist der Rechenkern des langfristigen Labors (`python3 -m kitrader labor lang`, Bericht LANGFRIST-BERICHT.md). Alles hier ist reine
Rechnung auf Listen (nur Standardbibliothek), ohne Dateien und ohne Netz. Das Laden der Reihen steht in `labor.py`.

Grundregeln (damit nichts aus der Zukunft in die Rechnung rutscht):
- Eine Reihe hat Kurse k[0..n-1] (Kurs- oder Gesamtertragsindex) und eine Bar-Rendite b[j] je Periode j (von Kurs j-1 nach Kurs j).
- Ein Signal s[i] (1 = investiert, 0 = Bargeld) darf nur Kurse bis einschließlich Index i benutzen.
- Ausführung zum NÄCHSTEN Kurs: Das Signal von Index i wird zum Kurs i+1 umgesetzt und wirkt erst in der Periode i+2
  (Rendite von Kurs i+1 nach Kurs i+2). Allgemein: pos[j] = s[j-1-verzoegerung] mit verzoegerung = 1 (Standard).
  `verzoegerung=0` ist die Lehrbuch-Annahme "am selben Schlusskurs handeln, der das Signal gab"; sie ist nur als Gegenprobe da.
- Kosten: bei jedem Wechsel (Kauf oder Verkauf) wird der Bruchteil `kosten` des gesamten Werts abgezogen.
  Der Anfangszustand jedes gerechneten Fensters kostet nichts (Strategie und Halten beginnen beide ohne Einstiegskosten).
- Bargeld wächst mit der Bar-Rendite der Periode (Zins oder 0).
- Walk-forward: Parameter werden für jeden Testblock NUR aus den Perioden vor dem Block gewählt (wachsendes Trainingsfenster),
  berichtet wird ausschließlich über die aneinandergesetzten Testblöcke.
- Zufallsvergleich: Die Spannen "investiert"/"Bargeld" der echten Positionen werden zufällig umgeordnet. Anzahl der Wechsel und Anteil
  der Zeit im Markt bleiben exakt gleich, nur der Zeitpunkt ist zufällig. So misst der Vergleich das Timing, nicht die Marktquote.
"""
from __future__ import annotations

import bisect
import math
import random
import statistics
from dataclasses import dataclass, field
from datetime import date
from itertools import accumulate
from operator import sub

EPS = 1e-12
MIN_SPANNEN = 6          # weniger Spannen (also weniger als 5 Wechsel): kein Zufallsvergleich


# ---------------------------------------------------------------- Reihe

@dataclass
class Reihe:
    """Eine Kursreihe mit Bargeld-Rendite. `daten` ISO-Daten aufsteigend, `kurs` Index (Gesamtertrag, wenn Dividenden enthalten sind),
    `bar[j]` einfache Bar-Rendite der Periode j (bar[0] unbenutzt), `kosten` je Wechsel als Anteil (0,001 = 0,1 %)."""
    name: str
    daten: list[str]
    kurs: list[float]
    bar: list[float]
    kosten: float = 0.001
    preisniveau: list[float] | None = None       # Verbraucherpreise zum Umrechnen in reale Renditen (optional)
    cape: list[float | None] | None = None       # CAPE je Datum (optional)
    signal_kurs: list[float] | None = None       # Kurse, auf denen die Trend-Signale gerechnet werden (Standard: kurs); z. B. der Kursindex
                                                 # ohne Dividenden, wenn `kurs` ein Gesamtertragsindex ist
    tage: list[int] = field(init=False, repr=False)
    lr_aktie: list[float] = field(init=False, repr=False)
    lr_bar: list[float] = field(init=False, repr=False)

    def __post_init__(self):
        n = len(self.daten)
        if n < 3 or len(self.kurs) != n or len(self.bar) != n:
            raise ValueError("Reihe: Daten, Kurse und Bar-Renditen brauchen gleiche Länge (mindestens 3)")
        if any(k <= 0 for k in self.kurs) or (self.signal_kurs is not None and (len(self.signal_kurs) != n or any(k <= 0 for k in self.signal_kurs))):
            raise ValueError("Reihe: Kurse müssen positiv sein und die gleiche Länge haben")
        if not 0 <= self.kosten < 1:
            raise ValueError("Reihe: Kosten je Wechsel müssen zwischen 0 und 1 (Anteil) liegen")
        self.tage = [date.fromisoformat(d).toordinal() for d in self.daten]
        if any(b <= a for a, b in zip(self.tage, self.tage[1:])):
            raise ValueError("Reihe: Daten müssen streng aufsteigend sein")
        self.lr_aktie = [0.0] + [math.log(b / a) for a, b in zip(self.kurs, self.kurs[1:])]
        self.lr_bar = [0.0] + [math.log1p(b) for b in self.bar[1:]]

    def __len__(self) -> int:
        return len(self.daten)

    @property
    def preise(self) -> list[float]:
        """Die Kurse, auf denen Trend und Momentum gerechnet werden."""
        return self.signal_kurs if self.signal_kurs is not None else self.kurs

    def index_ab(self, datum: str) -> int:
        """Erster Index mit Datum >= `datum` (len, wenn keiner)."""
        return bisect.bisect_left(self.daten, datum)

    def index_bis(self, datum: str) -> int:
        """Letzter Index mit Datum <= `datum` (-1, wenn keiner)."""
        return bisect.bisect_right(self.daten, datum) - 1

    def mit(self, **aenderungen) -> "Reihe":
        """Kopie mit geänderten Feldern (z. B. anderen Kosten); die abgeleiteten Listen werden neu gerechnet."""
        felder = dict(name=self.name, daten=self.daten, kurs=self.kurs, bar=self.bar, kosten=self.kosten,
                      preisniveau=self.preisniveau, cape=self.cape, signal_kurs=self.signal_kurs)
        felder.update(aenderungen)
        return Reihe(**felder)


# ---------------------------------------------------------------- Signale (nur Daten bis einschließlich Index i)

def signal_gleitender_durchschnitt(kurs: list[float], n: int) -> list[int | None]:
    """1, wenn der Kurs über dem Durchschnitt der letzten n Kurse (mit dem heutigen) liegt, sonst 0. Davor None."""
    if n < 2:
        raise ValueError("Gleitender Durchschnitt: n muss mindestens 2 sein")
    s: list[int | None] = [None] * len(kurs)
    summe = 0.0
    for i, k in enumerate(kurs):
        summe += k
        if i >= n:
            summe -= kurs[i - n]
        if i >= n - 1:
            s[i] = 1 if k > summe / n else 0
    return s


def signal_momentum(kurs: list[float], n: int) -> list[int | None]:
    """Zeitreihen-Momentum: 1, wenn die Rendite der letzten n Perioden positiv ist (Kurs höher als vor n Perioden), sonst 0."""
    if n < 1:
        raise ValueError("Momentum: n muss mindestens 1 sein")
    return [None if i < n else (1 if kurs[i] > kurs[i - n] else 0) for i in range(len(kurs))]


def signal_cape_fest(cape: list[float | None], schwelle: float) -> list[int | None]:
    """1, wenn das CAPE unter dem festen Schwellenwert liegt (Bewertung "nicht zu teuer"), sonst 0. Ohne CAPE-Wert None."""
    return [None if c is None else (1 if c < schwelle else 0) for c in cape]


def signal_cape_relativ(cape: list[float | None], quantil: float, mindestens: int = 240) -> list[int | None]:
    """1, wenn das heutige CAPE nicht zu den teuersten (1 - quantil) aller bis heute bekannten CAPE-Werte gehört, sonst 0.

    Der Rang wird nur aus den Werten bis einschließlich heute gebildet (wachsendes Fenster). Bis `mindestens` bekannte Werte
    vorliegen, ist das Signal None. `quantil` zwischen 0 und 1, z. B. 0,8 = investiert, solange das CAPE nicht im teuersten Fünftel liegt."""
    if not 0 < quantil <= 1:
        raise ValueError("Quantil muss in (0, 1] liegen")
    s: list[int | None] = []
    bekannt: list[float] = []
    for c in cape:
        if c is None:
            s.append(None)
            continue
        bisect.insort(bekannt, c)
        if len(bekannt) < mindestens:
            s.append(None)
            continue
        rang = bisect.bisect_right(bekannt, c) / len(bekannt)
        s.append(1 if rang <= quantil + EPS else 0)
    return s


def signal_und(a: list[int | None], b: list[int | None]) -> list[int | None]:
    """Beide Signale müssen 1 sein (Trend UND Bewertung). Fehlt eines, fehlt das Ergebnis."""
    return [None if x is None or y is None else x & y for x, y in zip(a, b)]


def positionen(signal: list[int | None], verzoegerung: int = 1) -> list[int | None]:
    """Position je Periode: pos[j] = signal[j-1-verzoegerung]. Signal von Kurs i, Ausführung zum Kurs i+1+verzoegerung."""
    if verzoegerung < 0:
        raise ValueError("verzoegerung darf nicht negativ sein")
    n = len(signal)
    vorlauf = 1 + verzoegerung
    return [None] * min(vorlauf, n) + signal[:max(0, n - vorlauf)]


def erster_index(pos: list[int | None]) -> int:
    """Erster Index mit gültiger Position (len, wenn keiner)."""
    return next((j for j, p in enumerate(pos) if p is not None), len(pos))


# ---------------------------------------------------------------- Rechnung

def lauf(reihe: Reihe, pos: list[int | None], von: int, bis: int) -> list[float]:
    """Logarithmische Netto-Zuwächse der Perioden von..bis (einschließlich) für die Positionen pos.

    Investiert wächst der Wert mit dem Kurs, sonst mit dem Bargeld. Bei jedem Positionswechsel gegenüber der Vorperiode wird
    log(1 - kosten) abgezogen. Anfangszustand = pos[von], ohne Einstiegskosten."""
    if von < 1 or bis < von or bis >= len(reihe):
        raise ValueError(f"Fenster {von}..{bis} liegt nicht in der Reihe (Perioden 1..{len(reihe) - 1})")
    p = pos[von:bis + 1]
    if any(x is None for x in p):
        raise ValueError("Im Fenster fehlen Positionen (Signal noch nicht berechenbar)")
    lc = math.log1p(-reihe.kosten)
    q = [p[0]] + p[:-1]
    return [(a if x else b) + (lc if x != y else 0.0)
            for x, y, a, b in zip(p, q, reihe.lr_aktie[von:bis + 1], reihe.lr_bar[von:bis + 1])]


def wechsel(pos: list[int | None], von: int, bis: int) -> int:
    """Anzahl Positionswechsel (Käufe und Verkäufe) in den Perioden von+1..bis; der Anfangszustand zählt nicht."""
    p = pos[von:bis + 1]
    return sum(1 for x, y in zip(p, p[1:]) if x != y)


def _summe_und_rueckgang(lr: list[float]) -> tuple[float, float]:
    """(log-Gesamtzuwachs, größter Rückgang als Anteil) aus Zuwächsen; schnell, ohne Python-Schleife."""
    pfad = [0.0] + list(accumulate(lr))
    spitzen = accumulate(pfad, max)
    return pfad[-1], 0.0 - math.expm1(min(map(sub, pfad, spitzen)))


def kennzahlen(reihe: Reihe, lr: list[float], pos: list[int | None] | None, von: int) -> dict:
    """Kennzahlen aus den Zuwächsen der Perioden von..von+len(lr)-1.

    rendite_pa (geometrisch, nach Kosten), schwankung (Standardabweichung der Perioden-Zuwächse, auf ein Jahr hochgerechnet),
    max_rueckgang (größter Rückgang vom Hoch, Anteil), unter_wasser_jahre (längste Zeit zwischen einem Hoch und der Rückkehr darauf),
    unter_wasser_anteil, wechsel, investiert (Anteil der Perioden im Markt), endwert (aus 1 gestartet), jahre, inflation_pa (falls Preisniveau)."""
    bis = von + len(lr) - 1
    pfad = [0.0] + list(accumulate(lr))
    t0, t1 = reihe.tage[von - 1], reihe.tage[bis]
    jahre = (t1 - t0) / 365.25
    pro_jahr = len(lr) / jahre
    spitzen = list(accumulate(pfad, max))
    rueck = [p - s for p, s in zip(pfad, spitzen)]
    laengste, spitze_tag, seit = 0, t0, None
    for k, r in enumerate(rueck):
        tag = reihe.tage[von - 1 + k]
        if r >= -EPS:
            if seit is not None:
                laengste = max(laengste, tag - seit)
                seit = None
            spitze_tag = tag
        elif seit is None:
            seit = spitze_tag
    if seit is not None:
        laengste = max(laengste, t1 - seit)
    out = {
        "von": reihe.daten[von - 1], "bis": reihe.daten[bis], "jahre": jahre, "perioden": len(lr),
        "rendite_pa": math.expm1(pfad[-1] / jahre),
        "schwankung": (statistics.pstdev(lr) * math.sqrt(pro_jahr)) if len(lr) > 1 else 0.0,
        "max_rueckgang": 0.0 - math.expm1(min(rueck)),
        "unter_wasser_jahre": laengste / 365.25,
        "unter_wasser_anteil": sum(1 for r in rueck if r < -EPS) / len(rueck),
        "endwert": math.exp(pfad[-1]),
        "wechsel": None if pos is None else wechsel(pos, von, bis),
        "investiert": None if pos is None else sum(pos[von:bis + 1]) / len(lr),
    }
    if reihe.preisniveau:
        out["inflation_pa"] = (reihe.preisniveau[bis] / reihe.preisniveau[von - 1]) ** (1 / jahre) - 1
        out["rendite_pa_real"] = (1 + out["rendite_pa"]) / (1 + out["inflation_pa"]) - 1
    return out


def halten_pos(n: int) -> list[int]:
    return [1] * n


def kennzahlen_fenster(reihe: Reihe, pos: list[int | None], von: int, bis: int) -> dict:
    """Rechnet das Fenster von..bis mit den Positionen und gibt die Kennzahlen zurück."""
    return kennzahlen(reihe, lauf(reihe, pos, von, bis), pos, von)


def kennzahlen_halten(reihe: Reihe, von: int, bis: int) -> dict:
    """Kaufen und Halten im Fenster (immer investiert, keine Wechsel, keine Kosten)."""
    return kennzahlen_fenster(reihe, halten_pos(len(reihe)), von, bis)


# ---------------------------------------------------------------- Regeln

@dataclass
class Regel:
    """Eine Regel: `name`, Parameterliste `gitter`, Signalfunktion `signal(reihe, parameter)`. `fest` = fester Parameter ohne Einstellung."""
    name: str
    gitter: tuple
    signal: object
    fest: object = None


def regel_trend(n_fest: int, gitter: tuple) -> Regel:
    return Regel("Trend", tuple(gitter), lambda r, n: signal_gleitender_durchschnitt(r.preise, n), n_fest)


def regel_momentum(n_fest: int, gitter: tuple) -> Regel:
    return Regel("Momentum", tuple(gitter), lambda r, n: signal_momentum(r.preise, n), n_fest)


def regel_cape_fest(gitter: tuple = (15, 20, 25, 30, 35)) -> Regel:
    return Regel("CAPE fest", tuple(gitter), lambda r, s: signal_cape_fest(r.cape, s))


def regel_cape_relativ(gitter: tuple = (0.6, 0.7, 0.8, 0.9)) -> Regel:
    return Regel("CAPE relativ", tuple(gitter), lambda r, q: signal_cape_relativ(r.cape, q))


def regel_trend_und_cape(n_trend: int, gitter: tuple = (0.6, 0.7, 0.8, 0.9)) -> Regel:
    """Trend (fester Durchschnitt über n_trend Perioden) UND CAPE relativ; eingestellt wird nur das CAPE-Quantil."""
    return Regel("Trend + CAPE", tuple(gitter),
                 lambda r, q: signal_und(signal_gleitender_durchschnitt(r.preise, n_trend), signal_cape_relativ(r.cape, q)))


# ---------------------------------------------------------------- Walk-forward

def blockgrenzen(reihe: Reihe, erstes_jahr: int, block_jahre: int) -> list[tuple[int, int]]:
    """Testblöcke als (erster, letzter) Perioden-Index: Kalenderjahre erstes_jahr, +block_jahre, ... bis zum Ende der Reihe."""
    grenzen, jahr = [], erstes_jahr
    while True:
        a = reihe.index_ab(f"{jahr:04d}-01-01")
        if a >= len(reihe):
            break
        grenzen.append(a)
        jahr += block_jahre
    if not grenzen:
        raise ValueError("Walk-forward: kein Testblock (Reihe endet vor dem ersten Testjahr)")
    return [(a, (grenzen[i + 1] - 1) if i + 1 < len(grenzen) else len(reihe) - 1) for i, a in enumerate(grenzen)]


def walk_forward(reihe: Reihe, regel: Regel, erstes_jahr: int, block_jahre: int, verzoegerung: int = 1,
                 start: int | None = None, positions_cache: dict | None = None) -> dict:
    """Wählt je Testblock den Parameter mit dem besten Netto-Zuwachs auf dem Training (alles VOR dem Block, ab `start`)
    und setzt die Testblöcke aneinander.

    Rückgabe: pos (Länge der Reihe, außerhalb der Testblöcke None), von, bis (Fenster aller Testblöcke) und faltungen
    (je Block: von, bis, parameter, training_zuwachs, training_von). `start`: erster Trainingsindex (Standard: erster Index,
    ab dem für alle Parameter des Gitters eine Position vorliegt)."""
    cache = positions_cache if positions_cache is not None else {}
    for p in regel.gitter:
        if (regel.name, p) not in cache:
            cache[(regel.name, p)] = positionen(regel.signal(reihe, p), verzoegerung)
    t0 = max(erster_index(cache[(regel.name, p)]) for p in regel.gitter) if start is None else start
    t0 = max(t0, 1)
    bloecke = blockgrenzen(reihe, erstes_jahr, block_jahre)
    pos: list[int | None] = [None] * len(reihe)
    faltungen = []
    for a, b in bloecke:
        if a - 1 < t0 + 11:
            raise ValueError(f"Walk-forward: zu wenig Training vor dem Block ab {reihe.daten[a]} (Trainingsbeginn {reihe.daten[t0]})")
        bester, beste_note = None, -math.inf
        for p in regel.gitter:
            note = sum(lauf(reihe, cache[(regel.name, p)], t0, a - 1))
            if note > beste_note + EPS:
                bester, beste_note = p, note
        pos[a:b + 1] = cache[(regel.name, bester)][a:b + 1]
        faltungen.append({"von": reihe.daten[a], "bis": reihe.daten[b], "parameter": bester, "training_von": reihe.daten[t0],
                          "training_zuwachs": beste_note})
    return {"pos": pos, "von": bloecke[0][0], "bis": bloecke[-1][1], "faltungen": faltungen, "start": t0}


# ---------------------------------------------------------------- Zufallsvergleich

def spannen(p: list[int]) -> list[tuple[int, int]]:
    """Aufeinanderfolgende gleiche Positionen als (Zustand, Länge)."""
    out: list[tuple[int, int]] = []
    for x in p:
        if out and out[-1][0] == x:
            out[-1] = (x, out[-1][1] + 1)
        else:
            out.append((x, 1))
    return out


def zufalls_positionen(sp: list[tuple[int, int]], rng: random.Random) -> list[int]:
    """Ordnet die Spannen "investiert" und die Spannen "Bargeld" je für sich zufällig um, behält aber die Abfolge investiert/Bargeld.
    Anzahl Wechsel und Anteil der Zeit im Markt bleiben dadurch genau gleich."""
    lang = {0: [l for s, l in sp if s == 0], 1: [l for s, l in sp if s == 1]}
    rng.shuffle(lang[0])
    rng.shuffle(lang[1])
    nimm = {0: iter(lang[0]), 1: iter(lang[1])}
    out: list[int] = []
    for s, _ in sp:
        out.extend([s] * next(nimm[s]))
    return out


def zufallsvergleich(reihe: Reihe, pos: list[int | None], von: int, bis: int, laeufe: int = 500, seed: int = 1) -> dict | None:
    """Vergleicht die echten Positionen im Fenster mit Zufallspositionen gleicher Wechselzahl und gleicher Marktquote.

    Rückgabe: laeufe, zuwachs_pa (log) des Kandidaten, zufall_median_pa/p5/p95 (Rendite p. a.), anteil_besser_rendite
    (Anteil der Zufallsläufe mit mindestens so hoher Rendite, in Prozent), anteil_besser_rueckgang (Anteil mit höchstens so
    großem Rückgang, in Prozent). None, wenn es zu wenige Wechsel gibt (weniger als MIN_SPANNEN - 1)."""
    p = pos[von:bis + 1]
    if any(x is None for x in p):
        raise ValueError("Im Fenster fehlen Positionen")
    sp = spannen(p)
    if len(sp) < MIN_SPANNEN:
        return None
    lc = math.log1p(-reihe.kosten)
    la, lb = reihe.lr_aktie[von:bis + 1], reihe.lr_bar[von:bis + 1]

    def rechne(q: list[int]) -> tuple[float, float]:
        qq = [q[0]] + q[:-1]
        return _summe_und_rueckgang([(a if x else b) + (lc if x != y else 0.0) for x, y, a, b in zip(q, qq, la, lb)])

    ziel_s, ziel_r = rechne(p)
    jahre = (reihe.tage[bis] - reihe.tage[von - 1]) / 365.25
    rng = random.Random(seed)
    summen, rueck = [], []
    for _ in range(laeufe):
        s, r = rechne(zufalls_positionen(sp, rng))
        summen.append(s)
        rueck.append(r)
    summen.sort()

    def pa(s: float) -> float:
        return math.expm1(s / jahre)

    def perz(q: float) -> float:
        return summen[min(len(summen) - 1, max(0, int(round(q * (len(summen) - 1)))))]

    return {"laeufe": laeufe, "spannen": len(sp), "zufall_median_pa": pa(perz(0.5)), "zufall_p5_pa": pa(perz(0.05)),
            "zufall_p95_pa": pa(perz(0.95)), "zufall_mittel_rueckgang": statistics.fmean(rueck),
            "anteil_besser_rendite": sum(1 for s in summen if s >= ziel_s - EPS) / laeufe * 100,
            "anteil_besser_rueckgang": sum(1 for r in rueck if r <= ziel_r + EPS) / laeufe * 100}


# ---------------------------------------------------------------- Teilzeiträume

def teilfenster(reihe: Reihe, von: int, bis: int, ab: str, bis_datum: str, mindestens: int = 24) -> tuple[int, int] | None:
    """Schnitt des Fensters von..bis mit [ab, bis_datum] (leer = offen); None, wenn weniger als `mindestens` Perioden übrig bleiben."""
    a = max(von, reihe.index_ab(ab) + 1 if ab else von)
    b = min(bis, reihe.index_bis(bis_datum) if bis_datum else bis)
    return (a, b) if b - a + 1 >= mindestens else None


# ---------------------------------------------------------------- Pläne je Reihe

@dataclass
class Plan:
    """Wie eine Reihe geprüft wird: feste Lehrbuch-Parameter, Gitter für die Einstellung, Walk-forward-Blöcke, Teilzeiträume."""
    titel: str
    trend_fest: int
    trend_gitter: tuple
    momentum_fest: int
    momentum_gitter: tuple
    erstes_testjahr: int
    block_jahre: int
    teilzeitraeume: tuple = ()               # (Name, ab, bis) mit ISO-Daten, "" = offen
    mit_cape: bool = False
    einheit: str = "Tage"


def regeln_fuer(plan: Plan) -> list[Regel]:
    """Alle Regeln einer Reihe: Trend, Momentum und (nur wo es ein CAPE gibt) die drei Bewertungsregeln."""
    rs = [regel_trend(plan.trend_fest, plan.trend_gitter), regel_momentum(plan.momentum_fest, plan.momentum_gitter)]
    if plan.mit_cape:
        rs += [regel_cape_fest(), regel_cape_relativ(), regel_trend_und_cape(plan.trend_fest)]
    return rs


def _teile(reihe: Reihe, plan: Plan, pos, von: int, bis: int) -> list[dict]:
    out = []
    for name, ab, bis_d in plan.teilzeitraeume:
        w = teilfenster(reihe, von, bis, ab, bis_d)
        if w is None:
            out.append({"name": name, "regel": None, "halten": None})
            continue
        out.append({"name": name, "von": reihe.daten[w[0] - 1], "bis": reihe.daten[w[1]],
                    "regel": kennzahlen_fenster(reihe, pos, *w), "halten": kennzahlen_halten(reihe, *w)})
    return out


def zeile(reihe: Reihe, plan: Plan, name: str, art: str, pos, von: int, bis: int, laeufe: int, seed: int,
          parameter=None) -> dict:
    """Eine Ergebniszeile: Kennzahlen im Fenster, Zufallsvergleich, Teilzeiträume."""
    return {"regel": name, "art": art, "parameter": parameter, "kennzahlen": kennzahlen_fenster(reihe, pos, von, bis),
            "zufall": zufallsvergleich(reihe, pos, von, bis, laeufe, seed) if laeufe > 0 else None,
            "teile": _teile(reihe, plan, pos, von, bis)}


def pruefe_reihe(reihe: Reihe, plan: Plan, laeufe: int = 500, seed: int = 1, verzoegerung: int = 1) -> dict:
    """Komplette Prüfung einer Reihe.

    fenster_a: Walk-forward-Fenster (ab erstem Testjahr). Zeilen: feste Lehrbuch-Regeln (ohne Einstellung) und alle Regeln mit
               Walk-forward-Einstellung, jeweils gegen Halten im selben Fenster.
    fenster_b: gesamte Reihe ab dem ersten Tag, an dem die festen Regeln rechnen können; nur die festen Regeln (nichts eingestellt).
    """
    regeln = regeln_fuer(plan)
    cache: dict = {}
    wf = {r.name: walk_forward(reihe, r, plan.erstes_testjahr, plan.block_jahre, verzoegerung, positions_cache=cache) for r in regeln}
    # alle Regeln teilen sich dasselbe Testfenster (gleiche Blockgrenzen)
    von, bis = wf[regeln[0].name]["von"], wf[regeln[0].name]["bis"]
    fest = [r for r in regeln if r.fest is not None]
    fest_pos = {r.name: positionen(r.signal(reihe, r.fest), verzoegerung) for r in fest}
    a_zeilen = []
    for k, r in enumerate(fest):
        a_zeilen.append(zeile(reihe, plan, f"{r.name} (fest {r.fest})", "fest", fest_pos[r.name], von, bis, laeufe, seed + k, r.fest))
    for k, r in enumerate(regeln):
        a_zeilen.append(zeile(reihe, plan, f"{r.name} (Walk-forward)", "walk_forward", wf[r.name]["pos"], von, bis, laeufe,
                              seed + 100 + k))
    start_b = max(erster_index(p) for p in fest_pos.values())
    b_zeilen = [zeile(reihe, plan, f"{r.name} (fest {r.fest})", "fest", fest_pos[r.name], start_b, len(reihe) - 1, laeufe, seed + 200 + k, r.fest)
                for k, r in enumerate(fest)]
    return {
        "titel": plan.titel, "verzoegerung": verzoegerung, "kosten": reihe.kosten, "erstes_testjahr": plan.erstes_testjahr,
        "daten": {"von": reihe.daten[0], "bis": reihe.daten[-1], "n": len(reihe)},
        "fenster_a": {"von": reihe.daten[von - 1], "bis": reihe.daten[bis], "halten": kennzahlen_halten(reihe, von, bis),
                      "zeilen": a_zeilen},
        "fenster_b": {"von": reihe.daten[start_b - 1], "bis": reihe.daten[-1], "halten": kennzahlen_halten(reihe, start_b, len(reihe) - 1),
                      "zeilen": b_zeilen},
        "faltungen": {r.name: wf[r.name]["faltungen"] for r in regeln},
        "teilzeitraeume": [t[0] for t in plan.teilzeitraeume],
    }


def empfindlichkeit(reihe: Reihe, plan: Plan, kosten_liste: list[float], verzoegerungen: tuple = (1, 0)) -> list[dict]:
    """Die festen Regeln über die gesamte Reihe mit anderen Kosten je Wechsel und mit/ohne zusätzlichen Tag bzw. Monat Verzögerung.
    Ohne Zufall und ohne Walk-forward; zeigt, wie stark das Ergebnis an der Kostenannahme und am Ausführungszeitpunkt hängt."""
    out = []
    regeln = [r for r in regeln_fuer(plan) if r.fest is not None]
    for v in verzoegerungen:
        for c in kosten_liste:
            r2 = reihe.mit(kosten=c)
            pos = {r.name: positionen(r.signal(r2, r.fest), v) for r in regeln}
            von = max(erster_index(p) for p in pos.values())
            halten = kennzahlen_halten(r2, von, len(r2) - 1)
            for r in regeln:
                k = kennzahlen_fenster(r2, pos[r.name], von, len(r2) - 1)
                out.append({"regel": f"{r.name} (fest {r.fest})", "verzoegerung": v, "kosten": c, "von": k["von"], "bis": k["bis"],
                            "rendite_pa": k["rendite_pa"], "halten_pa": halten["rendite_pa"], "max_rueckgang": k["max_rueckgang"],
                            "halten_rueckgang": halten["max_rueckgang"], "wechsel": k["wechsel"]})
    return out
