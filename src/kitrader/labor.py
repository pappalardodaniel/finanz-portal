"""Strategie-Labor: Kandidaten auf echten Kerzen prüfen und ehrlich mit Halten und Zufall vergleichen.

Aufruf:  python3 -m kitrader labor [--paare BTC/EUR ETH/EUR] [--takte 5m 1h] ...   (Kerzen vorher mit `daten kraken` holen)

Langfristig (Tages- und Monatsreihen über Jahrzehnte, Daten aus dem Paket kitrader.historie):  python3 -m kitrader labor lang ...
Das steht in labor_lang.py (Rechenkern: strategie/langfrist.py); Ergebnisse in LANGFRIST-BERICHT.md.

Ablauf je Kandidat, Paar und Takt:
1. Walk-forward (backtest.walk_forward): Die Daten werden zeitlich geteilt (Standard 40 % Training, 60 % Test in 4 Faltungen).
   Die Parameter werden nur auf Daten VOR der jeweiligen Testfaltung gewählt (bestes Netto-Ergebnis nach Kosten); berichtet wird
   ausschließlich über die Testfaltungen, zusammen.
2. Kaufen und Halten über denselben Testzeitraum, mit denselben Kosten (einmal kaufen, einmal verkaufen).
3. Zufallsvergleich: gleich viele Trades mit denselben Einsätzen, Stop- und Ziel-Abständen und denselben Ausstiegsregeln und Kosten
   wie beim Kandidaten (alles nach den Regeln des Backtests), aber Einstiegszeiten per Zufall im selben Testzeitraum (nur Long). 200
   Läufe; gemeldet wird der Anteil der Zufallsläufe, die mindestens so gut abschneiden wie der Kandidat. Bei hohem Anteil hat der
   Kandidat keinen erkennbaren Vorteil gegenüber blindem Raten; bei kleinem Anteil (unter 5 %) wäre er auffällig, aber bei vielen
   Tests kommt das auch durch Zufall vor.
4. Bei weniger als 30 Trades im Testteil wird keine Aussage gemacht.

Kosten (Standard = Kraken Pro, niedrigste Stufe, Stand 26.10.2026): Taker 0,80 % für Marktorders und Stops, Maker 0,40 % für das
Ziel als Limit-Order, dazu Spread 0,05 % (insgesamt) und Schlupf 0,02 % bei Marktorders und Stops. Alles ist über Befehlszeilen-
Optionen änderbar. Nur Long (Short bräuchte bei Kraken Margin und ist in der Risikoprüfung standardmäßig aus), Daytrading (am UTC-
Tagesende wird glattgestellt), Einsatz je Trade höchstens 10 % des Kapitals (Risikoprüfung).
"""
from __future__ import annotations

import json
import math
import os
import random
import statistics
import zlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .backtest import Handel, kerzen_aus_csv, walk_forward
from .broker.basis import Kerze
from .daten import dateiname, kerzenordner, paar_normal
from .einstellungen import mit_standard
from .hilfen import prozent, tabelle, takt_dauer, zahl
from .strategie import STRATEGIEN

MIN_TRADES = 30
ALLE_STRATEGIEN = ("ausbruch", "vwap", "donchian", "bollinger_rsi", "ma_trend", "momentum", "volumen")
STANDARD_KOSTEN = {"gebuehr_fest": 0.0, "gebuehr_prozent": 0.80, "gebuehr_maker_prozent": 0.40,
                   "schlupf_prozent": 0.02, "spread_prozent": 0.05}
HINWEIS = ("Vergangenheit sagt nichts sicher über die Zukunft. Kein Erfolgsversprechen und keine Anlageberatung. "
           "Die meisten Privatanleger verlieren beim Daytrading Geld.")


class LaborFehler(ValueError):
    """Das Labor kann nicht laufen (fehlende Daten, falsche Angaben). Der Text ist für Menschen lesbar."""


# ---------------------------------------------------------------- Einstellungen

def labor_einstellungen(train_anteil: float = 0.4, faltungen: int = 4, min_trades_training: int = 10,
                        kosten: dict | None = None, startkapital: float = 10000.0) -> dict:
    """Einstellungen für den Krypto-Test: Kraken-Kosten, Krypto-Markt rund um die Uhr, kleine Mengenschritte, 365 Tage im Jahr."""
    return mit_standard({
        "kosten": {**STANDARD_KOSTEN, **(kosten or {})},
        "risiko": {"standard_markt": "krypto", "mengenschritt": 0.00001},
        "backtest": {"startkapital": startkapital, "train_anteil": train_anteil, "faltungen": faltungen,
                     "min_trades_training": min_trades_training, "tage_pro_jahr": 365},
    })


# ---------------------------------------------------------------- Vergleiche

def _json_sicher(x):
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: _json_sicher(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_sicher(v) for v in x]
    if isinstance(x, datetime):
        return x.isoformat()
    return x


def halten(kerzen: list[Kerze], kosten: dict, einsatz: float = 1000.0) -> dict:
    """Kaufen und Halten über die Kerzen: zum Eröffnungskurs der ersten, Verkauf zum Schlusskurs der letzten Kerze.
    Kosten wie im Backtest: Taker-Gebühr, halber Spread und Schlupf, je beim Kauf und beim Verkauf."""
    if not kerzen:
        raise LaborFehler("Keine Kerzen im Testzeitraum")
    p0, p1 = kerzen[0].o, kerzen[-1].c
    je_seite = kosten["gebuehr_prozent"] / 100 + kosten["spread_prozent"] / 200 + kosten["schlupf_prozent"] / 100
    brutto = p1 / p0 - 1
    netto = (p1 * (1 - je_seite)) / (p0 * (1 + je_seite)) - 1
    spitze, rueckgang = p0, 0.0
    for k in kerzen:
        spitze = max(spitze, k.c)
        rueckgang = max(rueckgang, (spitze - k.c) / spitze)
    return {"von": kerzen[0].t.isoformat(), "bis": kerzen[-1].t.isoformat(), "preis_start": p0, "preis_ende": p1,
            "aenderung_prozent": brutto * 100, "netto_prozent": netto * 100, "netto_euro": netto * einsatz,
            "einsatz": einsatz, "max_rueckgang_prozent": rueckgang * 100}


def _perzentil(sortiert: list[float], q: float) -> float:
    return sortiert[min(len(sortiert) - 1, max(0, int(round(q * (len(sortiert) - 1)))))]


class Zufallswelt:
    """Rechnet einzelne Long-Trades auf den Kerzen genau nach den Regeln des Backtests (backtest.py), aber schnell:
    Einstieg zum Eröffnungskurs der Kerze nach dem Signal, Stop vor Ziel in derselben Kerze, Lücken über den Stop zum
    schlechteren Kurs, Ziel als Limit-Order (Maker-Gebühr, ohne Spread und Schlupf), Marktorders und Stops mit Taker-Gebühr,
    halbem Spread und Schlupf, am Tagesende (UTC) zum Schlusskurs glattgestellt."""

    def __init__(self, kerzen: list[Kerze], kosten: dict):
        n = len(kerzen)
        self.o = [k.o for k in kerzen]
        self.h = [k.h for k in kerzen]
        self.l = [k.l for k in kerzen]
        self.c = [k.c for k in kerzen]
        tage = [k.t.date() for k in kerzen]
        self.tagesende = [0] * n
        letzte = n - 1
        for j in range(n - 1, -1, -1):
            if j < n - 1 and tage[j] != tage[j + 1]:
                letzte = j
            self.tagesende[j] = letzte
        self.signalkerzen = [a for a in range(n - 1) if tage[a] == tage[a + 1]]       # danach folgt eine Kerze desselben Tages
        self.s2 = kosten["spread_prozent"] / 200
        self.schlupf = kosten["schlupf_prozent"] / 100
        self.taker = kosten["gebuehr_prozent"] / 100
        maker = kosten.get("gebuehr_maker_prozent")
        self.maker = self.taker if maker is None else maker / 100
        self.fest = kosten["gebuehr_fest"]

    def _aus(self, menge, preis_ein, gebuehr_ein, ref, limit):
        preis = ref if limit else ref * (1 - self.s2) * (1 - self.schlupf)
        gebuehr_aus = self.fest + (self.maker if limit else self.taker) * menge * preis
        return menge * (preis - preis_ein) - gebuehr_ein - gebuehr_aus

    def handel(self, signalkerze: int, wert: float, stop_abstand: float, ziel_abstand: float | None, halt: int | None = None) -> float:
        """Netto-Ergebnis eines Long-Trades mit Signal am Ende der Kerze `signalkerze`, Einsatz `wert` (Kontowährung nach Einstieg),
        Stop und Ziel als Abstand zum Einstiegskurs (Anteil). `halt`: Ausstieg nach so vielen Kerzen zum Eröffnungskurs (Gegensignal)."""
        j0 = signalkerze + 1
        ref = self.o[j0]
        preis_ein = ref * (1 + self.s2) * (1 + self.schlupf)
        menge = wert / preis_ein
        gebuehr_ein = self.fest + self.taker * menge * preis_ein
        stop = ref * (1 - stop_abstand)
        ziel = None if ziel_abstand is None else ref * (1 + ziel_abstand)
        letzte = self.tagesende[j0]
        for j in range(j0, letzte + 1):
            if halt is not None and j == j0 + halt:
                return self._aus(menge, preis_ein, gebuehr_ein, self.o[j], False)
            if self.l[j] <= stop:
                return self._aus(menge, preis_ein, gebuehr_ein, min(stop, self.o[j]), False)
            if ziel is not None and self.h[j] * (1 - self.s2) >= ziel:
                return self._aus(menge, preis_ein, gebuehr_ein, max(ziel, self.o[j] * (1 - self.s2)), True)
            if j == letzte:
                return self._aus(menge, preis_ein, gebuehr_ein, self.c[j], False)
        raise AssertionError("unerreichbar")


def zufallsvergleich(trades: list[Handel], kerzen: list[Kerze], takt_s: float, kosten: dict, laeufe: int = 200,
                     seed: int = 1) -> dict:
    """Vergleicht die echten Trades mit Zufalls-Trades, die sich NUR im Einstiegszeitpunkt unterscheiden.

    Zufalls-Trade i bekommt vom echten Trade i: Einsatz, Stop- und Ziel-Abstand (und bei Ausstieg per Gegensignal die Haltedauer);
    sein Einstieg liegt an einer zufälligen Kerze des Testzeitraums. Gerechnet wird nach genau denselben Regeln und Kosten wie im
    Backtest (`Zufallswelt`), also auch mit denselben vorsichtigen Annahmen (Stop vor Ziel, Taker-Gebühr für Stops, Tagesende).
    Nur Long-Trades. Rückgabe: Kandidat-Netto, Zufallsverteilung (Mittel, Median, 5 %, 95 %) und `anteil_besser` = Anteil der Läufe
    mit Ergebnis mindestens so gut wie der Kandidat (in Prozent)."""
    if not trades:
        raise LaborFehler("Zufallsvergleich ohne Trades nicht möglich")
    if any(t.richtung != "long" or t.stop_abstand is None for t in trades):
        raise LaborFehler("Der Zufallsvergleich kennt nur Long-Trades mit Stop")
    welt = Zufallswelt(kerzen, kosten)
    if not welt.signalkerzen:
        raise LaborFehler("Zu wenige Kerzen für den Zufallsvergleich")
    vorlagen = [(t.menge * t.einstieg, t.stop_abstand, t.ziel_abstand,
                 max(1, round((t.ausstieg_zeit - t.einstieg_zeit).total_seconds() / takt_s)) if t.grund == "signal" else None)
                for t in trades]
    rng = random.Random(seed)
    summen = []
    for _ in range(laeufe):
        summen.append(sum(welt.handel(rng.choice(welt.signalkerzen), *v) for v in vorlagen))
    summen.sort()
    kandidat = sum(t.netto for t in trades)
    return {"laeufe": laeufe, "kandidat_netto": kandidat, "mittel": sum(summen) / laeufe, "median": _perzentil(summen, 0.5),
            "p5": _perzentil(summen, 0.05), "p95": _perzentil(summen, 0.95),
            "anteil_besser": sum(1 for x in summen if x >= kandidat) / laeufe * 100}


# ---------------------------------------------------------------- eine Aufgabe (läuft in einem Unterprozess)

def aufgabe(csv_pfad: str, strategie: str, symbol: str, einst: dict, laeufe: int, seed: int) -> dict:
    """Walk-forward, Halten und Zufall für einen Kandidaten auf einer Kerzendatei. Gibt ein JSON-taugliches dict zurück."""
    kerzen = kerzen_aus_csv(csv_pfad)
    wf = walk_forward(strategie, kerzen, einst, symbol, "krypto")
    test = wf["test"]
    k = test.kennzahlen()
    fenster = [x for x in kerzen if test.von <= x.t < test.bis]
    takt_s = statistics.median((b.t - a.t).total_seconds() for a, b in zip(kerzen, kerzen[1:]))     # Kerzenlänge (Lücken stören nicht)
    kosten_ges = k["gebuehren"] + k["spread_schlupf"]
    wert = [h.menge * h.einstieg for h in test.trades]
    out = {
        "strategie": strategie, "symbol": symbol, "datei": Path(csv_pfad).name,
        "test_von": test.von.isoformat() if test.von else None, "test_bis": test.bis.isoformat() if test.bis else None,
        "test_tage": (test.bis - test.von).total_seconds() / 86400 if test.von else 0,
        "kerzen": len(kerzen),
        "kennzahlen": k, "kosten_gesamt": kosten_ges,
        "kostenanteil_prozent": kosten_ges / k["vor_kosten"] * 100 if k["vor_kosten"] > 0 else None,
        "brutto_je_trade_prozent": (sum(h.brutto / w for h, w in zip(test.trades, wert)) / len(wert) * 100) if wert else None,
        "kosten_je_trade_prozent": (sum((h.gebuehren + h.kosten_markt) / w for h, w in zip(test.trades, wert)) / len(wert) * 100)
        if wert else None,
        "aussage": k["trades"] >= MIN_TRADES,
        "faltungen": [{
            "parameter": f["parameter"], "ersatz_standard": bool(wf["gitter"]) and not f["parameter"],
            "training_netto": f["training"]["netto"], "training_trades": f["training"]["trades"],
            "test_netto": f["test"]["netto"], "test_trades": f["test"]["trades"]} for f in wf["faelle"]],
        "halten": halten(fenster, einst["kosten"]),
        "zufall": None,
    }
    if test.trades:
        out["zufall"] = zufallsvergleich(test.trades, fenster, takt_s, einst["kosten"], laeufe, seed)
    return _json_sicher(out)


# ---------------------------------------------------------------- Studie

def kerzendatei(datenordner: Path, paar: str, takt: str) -> Path:
    return kerzenordner(datenordner) / dateiname(paar_normal(paar), takt)


def studie(datenordner: Path, paare: list[str], takte: list[str], strategien: list[str], einst: dict, laeufe: int = 200,
           seed: int = 1, prozesse: int | None = None, fortschritt=print) -> dict:
    """Rechnet alle Kombinationen (parallel) und gibt {"einstellungen", "ergebnisse": [...], "daten": [...]} zurück."""
    for s in strategien:
        if s not in STRATEGIEN:
            raise LaborFehler(f"Unbekannte Strategie {s!r}. Vorhanden: {', '.join(sorted(STRATEGIEN))}")
    aufgaben, daten = [], []
    for paar in paare:
        for takt in takte:
            pfad = kerzendatei(datenordner, paar, takt)
            if not pfad.is_file():
                raise LaborFehler(f"Keine Kerzen für {paar} {takt}: {pfad} fehlt. Zuerst: python3 -m kitrader daten kraken {paar} "
                                  f"--takt {takt} --tage 90")
            kerzen = kerzen_aus_csv(pfad)
            dauer = takt_dauer(takt)
            luecken = sum(max(0, round((b.t - a.t) / dauer) - 1) for a, b in zip(kerzen, kerzen[1:]))
            daten.append({"paar": paar, "takt": takt, "datei": pfad.name, "kerzen": len(kerzen), "luecken": luecken,
                          "von": kerzen[0].t.isoformat(), "bis": (kerzen[-1].t + dauer).isoformat(),
                          "tage": (kerzen[-1].t + dauer - kerzen[0].t).total_seconds() / 86400,
                          "kurs_start": kerzen[0].o, "kurs_ende": kerzen[-1].c})
            for s in strategien:
                aufgaben.append((paar, takt, s, str(pfad), paar_normal(paar)))
    prozesse = prozesse or min(4, os.cpu_count() or 1)
    ergebnisse = []
    fortschritt(f"{len(aufgaben)} Aufgaben auf {prozesse} Prozessen ...")
    if prozesse <= 1:
        for paar, takt, s, pfad, symbol in aufgaben:
            r = aufgabe(pfad, s, symbol, einst, laeufe, _seed(seed, s, paar, takt))
            r.update(paar=paar, takt=takt)
            ergebnisse.append(r)
            fortschritt(f"  fertig: {paar} {takt} {s}: {r['kennzahlen']['trades']} Trades, Netto {zahl(r['kennzahlen']['netto'])}")
    else:
        with ProcessPoolExecutor(max_workers=prozesse) as pool:
            futures = {pool.submit(aufgabe, pfad, s, symbol, einst, laeufe, _seed(seed, s, paar, takt)): (paar, takt, s)
                       for paar, takt, s, pfad, symbol in aufgaben}
            for f in as_completed(futures):
                paar, takt, s = futures[f]
                r = f.result()
                r.update(paar=paar, takt=takt)
                ergebnisse.append(r)
                fortschritt(f"  fertig: {paar} {takt} {s}: {r['kennzahlen']['trades']} Trades, Netto {zahl(r['kennzahlen']['netto'])}")
    reihenfolge = {(p, t, s): i for i, (p, t, s, *_r) in enumerate(aufgaben)}
    ergebnisse.sort(key=lambda r: reihenfolge[(r["paar"], r["takt"], r["strategie"])])
    return {"erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "einstellungen": {
        "kosten": einst["kosten"], "backtest": {k: einst["backtest"][k] for k in
                                               ("startkapital", "train_anteil", "faltungen", "min_trades_training", "tage_pro_jahr")},
        "risiko": {k: einst["risiko"][k] for k in ("position_max_prozent", "risiko_pro_trade_prozent", "short_erlaubt",
                                                   "tagesverlust_max", "tagesverlust_max_prozent")},
        "zufallslaeufe": laeufe, "seed": seed}, "daten": daten, "ergebnisse": ergebnisse}


def _seed(basis: int, *teile: str) -> int:
    return basis * 1_000_003 + zlib.crc32("|".join(teile).encode())


# ---------------------------------------------------------------- Ausgabe

def _urteil(r: dict) -> str:
    if not r["aussage"]:
        return f"keine Aussage (n={r['kennzahlen']['trades']} < {MIN_TRADES})"
    k = r["kennzahlen"]
    z = r["zufall"]
    if k["netto"] <= 0:
        return "im Minus" + (" (Zufall im Schnitt schlechter)" if z and z["median"] < k["netto"] else
                             " (Zufall im Schnitt besser)" if z else "")
    return "im Plus (siehe Zufall)"


def _zeile(r: dict) -> list:
    k = r["kennzahlen"]
    z = r["zufall"]
    if not r["aussage"]:
        zufall = "-"
    else:
        zufall = prozent(z["anteil_besser"], 0) if z else "-"
    return [r["strategie"], k["trades"], zahl(k["netto"]), zahl(k["vor_kosten"]), zahl(r["kosten_gesamt"]), _kostenanteil(r),
            prozent(k["trefferquote"], 0), zahl(k["profitfaktor"]), prozent(k["max_rueckgang_prozent"], 1), zahl(k["sharpe"]),
            zufall, "ja" if r["aussage"] else "nein"]


def _kostenanteil(r: dict) -> str:
    """Kosten in Prozent des Brutto-Ergebnisses; ist das Brutto-Ergebnis schon 0 oder negativ, ist der Anteil nicht sinnvoll."""
    if r["kennzahlen"]["trades"] == 0:
        return "-"
    a = r["kostenanteil_prozent"]
    if a is None:
        return "Brutto ≤ 0"
    return "über 999 %" if a > 999 else prozent(a, 0)


KOPF = ["Kandidat", "Trades", "Netto €", "Brutto €", "Kosten €", "Kosten/Brutto", "Treffer", "Profitfaktor", "Max. Rückgang",
        "Sharpe", "Zufall besser", "Aussage"]


def gruppen(ergebnis: dict):
    """Ergebnisse gruppiert nach (Paar, Takt) in Studienreihenfolge."""
    seen: dict = {}
    for r in ergebnis["ergebnisse"]:
        seen.setdefault((r["paar"], r["takt"]), []).append(r)
    return seen


def zusammenfassung(ergebnis: dict) -> dict:
    """Kurzfazit über alle Kombinationen. Nur Kombinationen mit mindestens 30 Test-Trades zählen als Aussage.
    `zufallstreffer_erwartet`: Wie viele Kandidaten allein durch Zufall unter den besten 5 % der Zufallsläufe landen würden."""
    rs = ergebnis["ergebnisse"]
    mit = [r for r in rs if r["aussage"]]
    mit_zufall = [r for r in mit if r["zufall"]]
    return {
        "kombinationen": len(rs),
        "mit_aussage": len(mit),
        "im_plus": sum(1 for r in mit if r["kennzahlen"]["netto"] > 0),
        "besser_als_halten": sum(1 for r in mit if r["kennzahlen"]["netto"] > r["halten"]["netto_euro"]),
        "besser_als_95_prozent_zufall": sum(1 for r in mit_zufall if r["zufall"]["anteil_besser"] < 5),
        "zufallstreffer_erwartet": 0.05 * len(mit_zufall),
        "brutto_im_plus": sum(1 for r in mit if r["kennzahlen"]["vor_kosten"] > 0),
        "bestes_netto": max(((r["kennzahlen"]["netto"], f"{r['paar']} {r['takt']} {r['strategie']}") for r in mit), default=None),
        "summe_kosten": sum(r["kosten_gesamt"] for r in rs),
        "summe_brutto": sum(r["kennzahlen"]["vor_kosten"] for r in rs),
    }


def zusammenfassung_text(ergebnis: dict) -> str:
    z = zusammenfassung(ergebnis)
    zeilen = [f"Kurzfazit: {z['kombinationen']} Kombinationen (Kandidat, Paar, Takt), davon {z['mit_aussage']} mit mindestens {MIN_TRADES} "
              f"Test-Trades (nur dort ist eine Aussage erlaubt).",
              f"  Nach Kosten im Plus: {z['im_plus']} von {z['mit_aussage']}. Schon vor Kosten im Plus: {z['brutto_im_plus']}. "
              f"Besser als Kaufen und Halten (Netto-Euro): {z['besser_als_halten']}.",
              f"  Besser als 95 % der Zufallsläufe: {z['besser_als_95_prozent_zufall']} (allein durch Zufall zu erwarten: "
              f"etwa {zahl(z['zufallstreffer_erwartet'], 1)}).",
              f"  Alle Kombinationen zusammen: Brutto {zahl(z['summe_brutto'])} €, Kosten {zahl(z['summe_kosten'])} €."]
    if z["bestes_netto"]:
        zeilen.append(f"  Bestes Netto-Ergebnis mit Aussage: {zahl(z['bestes_netto'][0])} € ({z['bestes_netto'][1]}).")
    return "\n".join(zeilen)


def als_text(ergebnis: dict) -> str:
    teile = []
    for (paar, takt), rs in gruppen(ergebnis).items():
        h = rs[0]["halten"]
        teile.append(f"== {paar}, Takt {takt}, Testzeitraum {rs[0]['test_von'][:10]} bis {rs[0]['test_bis'][:10]} "
                     f"({zahl(rs[0]['test_tage'], 0)} Tage) ==")
        teile.append(tabelle(KOPF, [_zeile(r) for r in rs], links=(0,)))
        teile.append(f"Kaufen und Halten im Testzeitraum: Kurs {zahl(h['preis_start'], 1)} -> {zahl(h['preis_ende'], 1)} "
                     f"({prozent(h['aenderung_prozent'], 1)}), nach Kosten {prozent(h['netto_prozent'], 1)} "
                     f"= {zahl(h['netto_euro'])} € bei {zahl(h['einsatz'], 0)} € Einsatz; größter Rückgang unterwegs "
                     f"{prozent(h['max_rueckgang_prozent'], 1)}.")
        teile.append("")
    teile.append(zusammenfassung_text(ergebnis))
    teile.append("")
    teile.append(HINWEIS)
    return "\n".join(teile)


def als_markdown(ergebnis: dict) -> str:
    """Tabellen für STRATEGIE-BERICHT.md (nur Testteile)."""
    teile = []
    for (paar, takt), rs in gruppen(ergebnis).items():
        h = rs[0]["halten"]
        teile.append(f"**{paar}, Takt {takt}** – Testzeitraum {rs[0]['test_von'][:10]} bis {rs[0]['test_bis'][:10]} "
                     f"({zahl(rs[0]['test_tage'], 0)} Tage, {len(rs[0]['faltungen'])} Faltungen)\n")
        teile.append("| " + " | ".join(KOPF) + " |")
        teile.append("|" + "|".join(["---"] + ["---:"] * (len(KOPF) - 2) + [":---:"]) + "|")
        for r in rs:
            teile.append("| " + " | ".join(str(c) for c in _zeile(r)) + " |")
        teile.append(f"| *Kaufen und Halten* | 1 | {zahl(h['netto_euro'])} | {zahl(h['aenderung_prozent'] / 100 * h['einsatz'])} | "
                     f"{zahl(h['aenderung_prozent'] / 100 * h['einsatz'] - h['netto_euro'])} | - | - | - | "
                     f"{prozent(h['max_rueckgang_prozent'], 1)} | - | - | - |")
        teile.append("")
    return "\n".join(teile)


# ---------------------------------------------------------------- Kommandozeile

def argumente(s) -> None:
    s.add_argument("--paare", nargs="+", default=["BTC/EUR", "ETH/EUR"], help="Paare (Standard BTC/EUR ETH/EUR)")
    s.add_argument("--takte", nargs="+", default=["5m", "1h"], help="Takte (Standard 5m 1h)")
    s.add_argument("--strategien", nargs="+", default=list(ALLE_STRATEGIEN), metavar="NAME",
                   help="Standard: die zwei Beispiele und die fünf Kandidaten")
    s.add_argument("--train-anteil", type=float, default=0.4, help="Anteil der Daten zum Einstellen (Standard 0.4)")
    s.add_argument("--faltungen", type=int, default=4, help="Walk-forward-Faltungen im Testteil (Standard 4)")
    s.add_argument("--min-trades-training", type=int, default=10, help="Einstellungen mit weniger Trades im Training zählen nicht (Standard 10)")
    s.add_argument("--zufall", type=int, default=200, help="Zahl der Zufallsläufe je Kandidat (Standard 200)")
    s.add_argument("--seed", type=int, default=1, help="Startwert des Zufalls (Standard 1; gleiche Daten + gleicher Startwert = gleiches Ergebnis)")
    s.add_argument("--prozesse", type=int, help="parallele Prozesse (Standard: bis zu 4)")
    s.add_argument("--taker", type=float, default=STANDARD_KOSTEN["gebuehr_prozent"], help="Taker-Gebühr in %% (Standard 0.80, Kraken Pro Stufe 1)")
    s.add_argument("--maker", type=float, default=STANDARD_KOSTEN["gebuehr_maker_prozent"], help="Maker-Gebühr in %% (Standard 0.40)")
    s.add_argument("--spread", type=float, default=STANDARD_KOSTEN["spread_prozent"], help="Spread insgesamt in %% (Standard 0.05)")
    s.add_argument("--schlupf", type=float, default=STANDARD_KOSTEN["schlupf_prozent"], help="Schlupf in %% (Standard 0.02)")
    s.add_argument("--ausgabe", metavar="JSON", help="Ergebnisse als JSON speichern (Standard: <Datenordner>/labor/ergebnis.json)")
    s.add_argument("--markdown", metavar="MD", help="Tabellen für den Bericht als Markdown speichern")
    s.add_argument("--aus-json", metavar="JSON", help="nichts rechnen, nur Tabellen aus einer gespeicherten JSON-Datei zeigen")


def befehl(args, einst, ordner) -> int:
    if args.aus_json:
        ergebnis = json.loads(Path(args.aus_json).read_text(encoding="utf-8"))
    else:
        kosten = {"gebuehr_prozent": args.taker, "gebuehr_maker_prozent": args.maker, "spread_prozent": args.spread,
                  "schlupf_prozent": args.schlupf}
        if not 0 < args.train_anteil < 1 or args.faltungen < 1 or args.zufall < 1:
            raise LaborFehler("train-anteil muss zwischen 0 und 1 liegen, faltungen und zufall mindestens 1")
        e = labor_einstellungen(args.train_anteil, args.faltungen, args.min_trades_training, kosten)
        print(f"Strategie-Labor: Kosten Taker {args.taker} %, Maker {args.maker} %, Spread {args.spread} %, Schlupf {args.schlupf} %.\n"
              f"Das kann bei 5-Minuten-Kerzen einige Minuten dauern.")
        ergebnis = studie(Path(ordner), args.paare, args.takte, args.strategien, e, args.zufall, args.seed, args.prozesse)
        ziel = Path(args.ausgabe) if args.ausgabe else Path(ordner) / "labor" / "ergebnis.json"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(json.dumps(ergebnis, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nErgebnisse gespeichert: {ziel}\n")
    print(als_text(ergebnis))
    if args.markdown:
        Path(args.markdown).write_text(als_markdown(ergebnis), encoding="utf-8")
        print(f"\nMarkdown-Tabellen gespeichert: {args.markdown}")
    return 0
