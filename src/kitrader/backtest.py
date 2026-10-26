"""Ereignisgesteuerter Backtest auf Kerzen.

Ablauf je Kerze (keine Blicke in die Zukunft):
  1. Die am Ende der Vorkerze beschlossene Order wird zum Eröffnungskurs dieser Kerze ausgeführt.
  2. Stop-Loss und Take-Profit der offenen Position werden innerhalb der Kerze geprüft. Berühren beide
     dieselbe Kerze, gilt der Stop (vorsichtige Annahme). Springt der Kurs über den Stop, wird zum
     schlechteren Eröffnungskurs ausgeführt.
  3. Am Tagesende wird (Daytrading) zum Schlusskurs glattgestellt.
  4. Am Kerzenende fragt der Backtest die Strategie und schickt das Signal durch dieselbe Risikoprüfung
     wie der Live-Betrieb (risiko.pruefen).

Kosten: feste Gebühr + Prozentgebühr je Ausführung, Spread (halber Spread je Seite) und Schlupf bei
Marktorders und Stops. Take-Profit ist eine Limit-Order: sie füllt erst, wenn der Kurs das Ziel um den
halben Spread durchläuft, und zahlt dann weder Spread noch Schlupf. Börsen mit getrennten Gebühren (Kraken: Maker
günstiger als Taker) bilden das ab: `kosten.gebuehr_prozent` gilt für Marktorders und Stops (Taker),
`kosten.gebuehr_maker_prozent` (optional; fehlt sie, gilt `gebuehr_prozent`) für das Ziel als Limit-Order.

Walk-forward: Die Daten werden zeitlich geteilt. Die Strategie-Parameter werden NUR auf dem
Trainingsteil ausgesucht, der zurückgehaltene Testteil wird danach einmal mit diesen Parametern
gerechnet und getrennt berichtet. Nur der Testteil sagt etwas über die Zukunft; der Trainingsteil
ist durch das Einstellen geschönt.
"""
from __future__ import annotations

import csv
import itertools
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .broker.basis import Kerze, Kurs, Order
from .einstellungen import mit_standard
from .hilfen import prozent, tabelle, zahl, zu_utc
from .risiko import Lage, handelstag, markt_von_symbol, pruefen, tagesverlust_erreicht
from .strategie import Strategie, signal_zu_order, strategie_holen

HINWEIS = ("Vergangenheit sagt nichts sicher über die Zukunft. Kein Erfolgsversprechen: erst mehrere Wochen "
           "Papierhandel, echtes Geld nur mit kleinen Grenzen.")


@dataclass
class Handel:
    """Ein abgeschlossener Trade."""
    richtung: str                 # "long" / "short"
    menge: float
    einstieg_zeit: datetime
    ausstieg_zeit: datetime
    einstieg: float               # tatsächlicher Preis inkl. Spread und Schlupf
    ausstieg: float
    brutto: float                 # Ergebnis zu Mittelkursen, ohne Kosten
    gebuehren: float
    kosten_markt: float           # Spread + Schlupf in Geld
    grund: str                    # stop / ziel / signal / tagesende / ende
    # Abstand von Stop und Ziel zum Einstiegskurs (Mittelkurs) als Anteil, positiv = auf der Verlust- bzw. Gewinnseite.
    # Das Labor (labor.py) gibt Zufalls-Trades dieselben Abstände; None = unbekannt bzw. kein Ziel.
    stop_abstand: float | None = None
    ziel_abstand: float | None = None

    @property
    def netto(self) -> float:
        return self.brutto - self.gebuehren - self.kosten_markt


@dataclass
class Ergebnis:
    startkapital: float
    trades: list[Handel] = field(default_factory=list)
    tages_pnl: list[float] = field(default_factory=list)    # Ergebnis je Handelstag (auch Tage ohne Trade)
    von: datetime | None = None
    bis: datetime | None = None
    tage_pro_jahr: int = 252

    def kennzahlen(self) -> dict:
        return kennzahlen(self.trades, self.tages_pnl, self.startkapital, self.tage_pro_jahr)

    @staticmethod
    def zusammen(teile: list["Ergebnis"]) -> "Ergebnis":
        z = Ergebnis(teile[0].startkapital, tage_pro_jahr=teile[0].tage_pro_jahr,
                     von=teile[0].von, bis=teile[-1].bis)
        for t in teile:
            z.trades += t.trades
            z.tages_pnl += t.tages_pnl
        return z


def kennzahlen(trades: list[Handel], tages_pnl: list[float], startkapital: float, tage_pro_jahr: int = 252) -> dict:
    nettos = [h.netto for h in trades]
    gewinne = [x for x in nettos if x > 0]
    verluste = [x for x in nettos if x < 0]
    # maximaler Rückgang auf der Kurve nach jedem abgeschlossenen Trade
    eigen, spitze, rueckgang, rueckgang_pz = startkapital, startkapital, 0.0, 0.0
    for x in nettos:
        eigen += x
        spitze = max(spitze, eigen)
        if spitze - eigen > rueckgang:
            rueckgang = spitze - eigen
            rueckgang_pz = rueckgang / spitze * 100
    # Sharpe aus Tagesrenditen (Ergebnis des Tages / Eigenkapital zu Tagesbeginn), mal Wurzel(Handelstage/Jahr)
    renditen, eigen = [], startkapital
    for p in tages_pnl:
        renditen.append(p / eigen if eigen else 0.0)
        eigen += p
    sharpe = None
    if len(renditen) >= 2:
        abw = statistics.stdev(renditen)
        if abw > 0:
            sharpe = statistics.fmean(renditen) / abw * math.sqrt(tage_pro_jahr)
    summe_verluste = abs(sum(verluste))
    return {
        "netto": sum(nettos),                                   # Ergebnis nach Kosten
        "vor_kosten": sum(h.brutto for h in trades),
        "gebuehren": sum(h.gebuehren for h in trades),
        "spread_schlupf": sum(h.kosten_markt for h in trades),
        "trades": len(trades),
        "trefferquote": len(gewinne) / len(trades) * 100 if trades else None,
        "profitfaktor": (sum(gewinne) / summe_verluste if summe_verluste else (math.inf if gewinne else None)),
        "gewinn_schnitt": statistics.fmean(gewinne) if gewinne else None,
        "verlust_schnitt": statistics.fmean(verluste) if verluste else None,
        "max_rueckgang": rueckgang,
        "max_rueckgang_prozent": rueckgang_pz,
        "sharpe": sharpe,
    }


# ---------------------------------------------------------------- Daten

def kerzen_aus_csv(pfad: str | Path) -> list[Kerze]:
    """Liest Kerzen aus CSV mit den Spalten t,o,h,l,c,v. t ist ISO-Zeit oder Unix-Sekunden (auch Millisekunden).
    Eine Kopfzeile ist erlaubt. Ergebnis: nach Zeit sortiert, UTC."""
    kerzen: list[Kerze] = []
    with open(pfad, newline="", encoding="utf-8") as f:
        for nr, zeile in enumerate(csv.reader(f), 1):
            zeile = [z.strip() for z in zeile]
            if not zeile or not zeile[0] or zeile[0].startswith("#"):
                continue
            try:
                werte = [float(x) for x in zeile[1:6]]
            except ValueError:
                if nr == 1:
                    continue            # Kopfzeile
                raise ValueError(f"{pfad}, Zeile {nr}: Zahlen erwartet (t,o,h,l,c,v)") from None
            if len(werte) < 4:
                raise ValueError(f"{pfad}, Zeile {nr}: mindestens t,o,h,l,c nötig")
            kerzen.append(Kerze(_zeit_lesen(zeile[0], pfad, nr), werte[0], werte[1], werte[2], werte[3],
                                werte[4] if len(werte) > 4 else 0.0))
    kerzen.sort(key=lambda k: k.t)
    if not kerzen:
        raise ValueError(f"{pfad}: keine Kerzen gefunden")
    return kerzen


def _zeit_lesen(text: str, pfad, nr: int) -> datetime:
    try:
        sek = float(text)
        if sek > 1e11:
            sek /= 1000
        return datetime.fromtimestamp(sek, timezone.utc)
    except ValueError:
        pass
    try:
        return zu_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        raise ValueError(f"{pfad}, Zeile {nr}: Zeit {text!r} weder ISO-Zeit noch Unix-Sekunden") from None


def _takt(kerzen: list[Kerze]):
    abstaende = [b.t - a.t for a, b in zip(kerzen, kerzen[1:]) if b.t > a.t]
    if not abstaende:
        raise ValueError("Zu wenige Kerzen für einen Backtest")
    return statistics.median(abstaende)


# ---------------------------------------------------------------- Lauf

def backtest(strategie: Strategie, kerzen: list[Kerze], einstellungen: dict | None = None,
             symbol: str = "TEST", markt: str | None = None, startkapital: float | None = None,
             handel_ab: datetime | None = None) -> Ergebnis:
    """Rechnet die Strategie über die Kerzen und gibt Trades und Tagesergebnisse zurück.

    `handel_ab`: Kerzen davor sind nur Vorgeschichte (die Strategie sieht sie als Verlauf für ihre Kennzahlen, wie im Live-Betrieb),
    gehandelt und gezählt wird erst ab dieser Zeit. Die Vorgeschichte reicht höchstens `backtest.kerzen_fenster` Kerzen zurück
    und enthält nur Vergangenes, nie Daten nach `handel_ab`."""
    einst = mit_standard(einstellungen)
    bt, kosten = einst["backtest"], einst["kosten"]
    s2 = kosten["spread_prozent"] / 200          # halber Spread als Anteil
    schlupf = kosten["schlupf_prozent"] / 100
    markt = markt or markt_von_symbol(symbol, einst)
    start = float(startkapital if startkapital is not None else bt["startkapital"])
    dauer = _takt(kerzen)
    fenster = int(bt["kerzen_fenster"])

    tage = [handelstag(markt, k.t, einst) for k in kerzen]
    erg = Ergebnis(start, von=handel_ab or kerzen[0].t, bis=kerzen[-1].t + dauer, tage_pro_jahr=bt["tage_pro_jahr"])
    kasse = start
    pos: dict | None = None            # offene Position
    wartend: Order | None = None       # am Kerzenende beschlossen, wird zur nächsten Eröffnung ausgeführt
    wartend_flat = False
    tag = None
    tagesstart = start
    gesperrt = False
    orders_heute = 0

    maker = kosten.get("gebuehr_maker_prozent")
    maker = kosten["gebuehr_prozent"] if maker is None else maker

    def gebuehr(menge, preis, limit=False):
        return kosten["gebuehr_fest"] + (maker if limit else kosten["gebuehr_prozent"]) / 100 * menge * preis

    def eigenkapital(kurs):
        return kasse + (pos["vz"] * pos["menge"] * kurs if pos else 0.0)

    def eroeffnen(order: Order, o: float, zeit: datetime):
        nonlocal kasse, pos
        vz = 1 if order.seite == "kauf" else -1
        preis = o * (1 + vz * s2) * (1 + vz * schlupf)
        g = gebuehr(order.menge, preis)
        kasse -= vz * order.menge * preis + g
        pos = {"vz": vz, "menge": order.menge, "ref": o, "preis": preis, "zeit": zeit, "gebuehr": g,
               "stop": order.stop_loss, "ziel": order.take_profit}

    def schliessen(ref: float, zeit: datetime, grund: str, limit: bool = False):
        """Position zum Referenzkurs `ref` glattstellen (Limit: ohne Spread/Schlupf)."""
        nonlocal kasse, pos
        vz = pos["vz"]
        preis = ref if limit else ref * (1 - vz * s2) * (1 - vz * schlupf)
        g = gebuehr(pos["menge"], preis, limit)
        kasse += vz * pos["menge"] * preis - g
        brutto = (ref - pos["ref"]) * vz * pos["menge"]
        markt_kosten = (abs(pos["preis"] - pos["ref"]) + (0 if limit else abs(preis - ref))) * pos["menge"]
        stop_a = vz * (pos["ref"] - pos["stop"]) / pos["ref"] if pos["stop"] is not None else None
        ziel_a = vz * (pos["ziel"] - pos["ref"]) / pos["ref"] if pos["ziel"] is not None else None
        erg.trades.append(Handel("long" if vz > 0 else "short", pos["menge"], pos["zeit"], zeit, pos["preis"],
                                 preis, brutto, pos["gebuehr"] + g, markt_kosten, grund, stop_a, ziel_a))
        pos = None

    for i, k in enumerate(kerzen):
        heute = tage[i]
        vorlauf = handel_ab is not None and k.t < handel_ab          # nur Vorgeschichte: nichts handeln, nichts zählen
        if heute != tag:                              # neuer Handelstag
            if tag is not None and not (handel_ab is not None and kerzen[i - 1].t < handel_ab):
                erg.tages_pnl.append(eigenkapital(kerzen[i - 1].c) - tagesstart)
            tag, tagesstart, gesperrt, orders_heute = heute, eigenkapital(k.o), False, 0
        if vorlauf:
            continue
        letzte_des_tages = i + 1 == len(kerzen) or tage[i + 1] != heute
        ende = k.t + dauer

        # 1. Eröffnung bzw. Glattstellung zum Eröffnungskurs
        if wartend is not None and pos is None:
            eroeffnen(wartend, k.o, k.t)
        elif wartend_flat and pos is not None:
            schliessen(k.o, k.t, "signal")
        wartend, wartend_flat = None, False

        # 2. Schutzorders innerhalb der Kerze
        if pos is not None:
            if pos["vz"] > 0:
                if pos["stop"] is not None and k.l <= pos["stop"]:
                    schliessen(min(pos["stop"], k.o), k.t, "stop")
                elif pos["ziel"] is not None and k.h * (1 - s2) >= pos["ziel"]:
                    schliessen(max(pos["ziel"], k.o * (1 - s2)), k.t, "ziel", limit=True)
            else:
                if pos["stop"] is not None and k.h >= pos["stop"]:
                    schliessen(max(pos["stop"], k.o), k.t, "stop")
                elif pos["ziel"] is not None and k.l * (1 + s2) <= pos["ziel"]:
                    schliessen(min(pos["ziel"], k.o * (1 + s2)), k.t, "ziel", limit=True)

        # 3. Tagesende
        if letzte_des_tages and pos is not None and (bt["tagesende_flat"] or i + 1 == len(kerzen)):
            schliessen(k.c, k.t, "tagesende" if bt["tagesende_flat"] else "ende")
        if letzte_des_tages and bt["tagesende_flat"]:
            continue                                  # keine neuen Positionen über Nacht

        # 4. Strategie fragen
        ek = eigenkapital(k.c)
        lage = Lage(jetzt=ende, gesamtwert=ek, einstellungen=einst, symbol=symbol, guthaben=kasse,
                    positionen={symbol: pos["vz"] * pos["menge"]} if pos else {},
                    tagesergebnis=ek - tagesstart, orders_heute=orders_heute,
                    kurs=Kurs(symbol, k.c * (1 - s2), k.c * (1 + s2), k.c, ende), tag_gesperrt=gesperrt)
        gesperrt = gesperrt or tagesverlust_erreicht(lage)
        lage.tag_gesperrt = gesperrt
        signal = strategie.bewerten(kerzen[max(0, i + 1 - fenster):i + 1], lage)
        if signal is None:
            continue
        if signal.richtung == "flat":
            wartend_flat = pos is not None
            continue
        if pos is not None or signal.sicherheit < einst["autopilot"]["sicherheit_min"]:
            continue
        order = signal_zu_order(signal, symbol, lage)
        if order is None or not pruefen(order, lage).ok:
            continue
        wartend = order
        orders_heute += 1

    if tag is not None:
        erg.tages_pnl.append(eigenkapital(kerzen[-1].c) - tagesstart)
    return erg


# ---------------------------------------------------------------- Walk-forward

def _tagesanfaenge(kerzen: list[Kerze], markt: str, einst: dict) -> list[int]:
    tage = [handelstag(markt, k.t, einst) for k in kerzen]
    return [i for i in range(len(kerzen)) if i == 0 or tage[i] != tage[i - 1]]


def _gitter_punkte(gitter: dict[str, list]) -> list[dict]:
    if not gitter:
        return [{}]
    namen = list(gitter)
    return [dict(zip(namen, werte)) for werte in itertools.product(*(gitter[n] for n in namen))]


def walk_forward(strategie_name: str, kerzen: list[Kerze], einstellungen: dict | None = None,
                 symbol: str = "TEST", markt: str | None = None, gitter: dict[str, list] | None = None,
                 vorgeschichte: bool = True) -> dict:
    """Stellt die Strategie auf dem Trainingsteil ein und rechnet den zurückgehaltenen Testteil getrennt.

    Rückgabe: {"strategie", "parameter" (zuletzt gewählt), "training": Ergebnis, "test": Ergebnis,
    "faelle": [{"parameter", "training": kennzahlen, "test": kennzahlen}, ...]}.
    Bei `faltungen` > 1 wird der Testteil in gleich lange Abschnitte geteilt; jeder Abschnitt wird mit den
    Parametern gerechnet, die auf allen Daten davor gewählt wurden (nach vorne laufend).
    `vorgeschichte`: Die Strategie sieht beim Test die Kerzen vor dem Testabschnitt als Verlauf (bis `kerzen_fenster`), damit sich
    Kennzahlen wie ein 100-Kerzen-Durchschnitt nicht in jedem Abschnitt neu aufwärmen müssen; gehandelt wird erst im Testabschnitt."""
    einst = mit_standard(einstellungen)
    bt = einst["backtest"]
    markt = markt or markt_von_symbol(symbol, einst)
    klasse = type(strategie_holen(strategie_name, einst))
    gitter = klasse.gitter if gitter is None else gitter
    tage = _tagesanfaenge(kerzen, markt, einst)
    if len(tage) < 2:
        raise ValueError("Für den Walk-forward braucht es mindestens 2 Handelstage Daten")
    faltungen = max(1, int(bt["faltungen"]))
    ziel = int(len(kerzen) * bt["train_anteil"])
    start_test = next((i for i in tage if i >= ziel and i > 0), tage[-1])     # Teilung immer an einem Tagesanfang
    grenzen = {start_test, len(kerzen)}
    for j in range(1, faltungen):
        soll = start_test + (len(kerzen) - start_test) * j // faltungen
        grenzen.add(next((i for i in tage if i >= soll), len(kerzen)))
    grenzen = sorted(grenzen)

    def rechnen(parameter, teil, ab=None):
        s = strategie_holen(strategie_name, einst, **parameter)
        return backtest(s, teil, einst, symbol, markt, handel_ab=ab)

    fenster = int(bt["kerzen_fenster"])

    training = None
    tests, faelle, letzte = [], [], {}
    for a, b in zip(grenzen, grenzen[1:]):
        if b <= a:
            continue
        beste, bestes_netto, bestes_training = None, None, None
        for parameter in _gitter_punkte(gitter):
            e = rechnen(parameter, kerzen[:a])
            k = e.kennzahlen()
            if k["trades"] >= bt["min_trades_training"] and (bestes_netto is None or k["netto"] > bestes_netto):
                beste, bestes_netto, bestes_training = parameter, k["netto"], e
        if beste is None:          # nichts Brauchbares gefunden: Standardparameter, Training trotzdem zeigen
            beste = {}
            bestes_training = rechnen(beste, kerzen[:a])
        test = rechnen(beste, kerzen[max(0, a - fenster):b], kerzen[a].t) if vorgeschichte else rechnen(beste, kerzen[a:b])
        training = training or bestes_training
        tests.append(test)
        letzte = beste
        faelle.append({"parameter": beste, "training": bestes_training.kennzahlen(), "test": test.kennzahlen()})
    return {"strategie": strategie_name, "parameter": letzte, "training": training,
            "test": Ergebnis.zusammen(tests), "faelle": faelle, "symbol": symbol,
            "gitter": gitter, "markt": markt}


# ---------------------------------------------------------------- Ausgabe

def _zeilen(ks: list[dict]) -> list[list[str]]:
    def spalte(k):
        return [zahl(k["netto"]), zahl(k["vor_kosten"]), zahl(k["gebuehren"]), zahl(k["spread_schlupf"]),
                k["trades"], prozent(k["trefferquote"]), zahl(k["profitfaktor"]), zahl(k["gewinn_schnitt"]),
                zahl(k["verlust_schnitt"]),
                f"{zahl(k['max_rueckgang'])} ({zahl(k['max_rueckgang_prozent'])} %)", zahl(k["sharpe"])]
    kopf = ["Netto-Ergebnis (nach Kosten)", "Ergebnis vor Kosten", "Gebühren", "Spread + Schlupf",
            "Anzahl Trades", "Trefferquote", "Profitfaktor", "Ø Gewinn", "Ø Verlust",
            "Max. Rückgang", "Sharpe (annualisiert)"]
    spalten = [spalte(k) for k in ks]
    return [[kopf[i]] + [s[i] for s in spalten] for i in range(len(kopf))]


def _zeitraum(e: Ergebnis) -> str:
    return "-" if e.von is None else f"{e.von:%Y-%m-%d} bis {e.bis:%Y-%m-%d}"


def tabelle_einzel(e: Ergebnis, titel: str = "Backtest") -> str:
    k = e.kennzahlen()
    text = tabelle(["Kennzahl", titel], _zeilen([k]))
    return text + _warnungen([k]) + "\n\n" + HINWEIS


def tabelle_walk_forward(wf: dict) -> str:
    train, test = wf["training"], wf["test"]
    kt, ke = train.kennzahlen(), test.kennzahlen()
    kopf = ["Kennzahl", f"Training ({_zeitraum(train)})", f"TEST, zurückgehalten ({_zeitraum(test)})"]
    teile = [f"Strategie {wf['strategie']} auf {wf['symbol']} (Markt {wf['markt']}), "
             f"Parameter aus dem Training: {wf['parameter'] or 'Standard'}",
             tabelle(kopf, _zeilen([kt, ke]))]
    return "\n".join(teile) + _warnungen([kt, ke]) + "\n\nNur die Testspalte zählt; das Training ist durch das Einstellen geschönt.\n" + HINWEIS


def _warnungen(ks: list[dict]) -> str:
    w = ""
    if any(k["trades"] < 30 for k in ks):
        w += "\nAchtung: weniger als 30 Trades, die Kennzahlen sind kaum aussagekräftig (n = " + \
             " / ".join(str(k["trades"]) for k in ks) + ")."
    return w


def als_dict(e: Ergebnis | dict) -> dict:
    """Ergebnis als dict (für JSON). Akzeptiert ein Ergebnis oder das Walk-forward-dict."""
    if isinstance(e, Ergebnis):
        return {"zeitraum": _zeitraum(e), "kennzahlen": e.kennzahlen(),
                "trades": [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in vars(h).items()}
                           | {"netto": h.netto} for h in e.trades]}
    return {"strategie": e["strategie"], "symbol": e["symbol"], "markt": e["markt"], "parameter": e["parameter"],
            "training": als_dict(e["training"]), "test": als_dict(e["test"]), "faelle": e["faelle"]}
