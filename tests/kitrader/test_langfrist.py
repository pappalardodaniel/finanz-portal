"""Langfristiges Labor: Signale, keine Zukunftsdaten, Kostenrechnung von Hand, Walk-forward, Zufallsvergleich, Reihen und Kommandozeile.
Alles mit erfundenen Zahlen, kein Netz."""
import contextlib
import io
import json
import math
import os
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from kitrader import labor_lang as ll
from kitrader.__main__ import main
from kitrader.historie.speicher import Speicher
from kitrader.strategie import langfrist as lf


def tage(start: str, n: int) -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def monate(start_jahr: int, n: int) -> list[str]:
    return [f"{start_jahr + i // 12:04d}-{i % 12 + 1:02d}-01" for i in range(n)]


def zufallskurs(n: int, seed: int, drift=0.0003, sigma=0.01, start=100.0) -> list[float]:
    rng = random.Random(seed)
    k = [start]
    for _ in range(n - 1):
        k.append(k[-1] * math.exp(rng.gauss(drift, sigma)))
    return k


def reihe_aus(kurs, daten=None, bar=0.0, kosten=0.0, **kw) -> lf.Reihe:
    daten = daten or tage("2020-01-01", len(kurs))
    return lf.Reihe("test", daten, list(kurs), [bar] * len(kurs), kosten, **kw)


class SignalTest(unittest.TestCase):
    def test_gleitender_durchschnitt_von_hand(self):
        # Kurse 1 2 3 2 1 2 3, n = 3: Durchschnitte (3. bis 7. Wert) 2, 2.33, 2, 1.67, 2 -> 3>2 ja, 2>2.33 nein, 1>2 nein, 2>1.67 ja, 3>2 ja
        s = lf.signal_gleitender_durchschnitt([1, 2, 3, 2, 1, 2, 3], 3)
        self.assertEqual(s, [None, None, 1, 0, 0, 1, 1])

    def test_gleich_ist_nicht_darueber(self):
        self.assertEqual(lf.signal_gleitender_durchschnitt([5, 5, 5, 5], 3), [None, None, 0, 0])
        self.assertEqual(lf.signal_momentum([5, 5, 5], 1), [None, 0, 0])

    def test_momentum_von_hand(self):
        # n = 2: Kurs gegen den Kurs zwei Perioden davor: 9>10 nein, 12>11 ja, 12>9 ja
        self.assertEqual(lf.signal_momentum([10, 11, 9, 12, 12], 2), [None, None, 0, 1, 1])

    def test_cape_fest(self):
        self.assertEqual(lf.signal_cape_fest([None, 10.0, 25.0, 30.0], 25.0), [None, 1, 0, 0])

    def test_cape_relativ_nutzt_nur_bisherige_werte(self):
        # nach 3 bekannten Werten: 30 ist der teuerste bisher (Rang 3/3 = 1.0 > 0.8) -> 0; 5 der billigste (1/4) -> 1; 40 wieder der teuerste -> 0
        s = lf.signal_cape_relativ([10.0, 20.0, 30.0, 5.0, 40.0], 0.8, mindestens=3)
        self.assertEqual(s, [None, None, 0, 1, 0])
        # fehlende Werte zählen nicht mit: bekannt sind erst 20 | 20, 10 (Rang von 10 ist 1/2 -> investiert) | 20, 10, 30 (Rang 1.0 -> nicht)
        s2 = lf.signal_cape_relativ([None, 20.0, 10.0, 30.0], 0.8, mindestens=2)
        self.assertEqual(s2, [None, None, 1, 0])

    def test_und(self):
        self.assertEqual(lf.signal_und([1, 1, 0, None], [1, 0, 1, 1]), [1, 0, 0, None])

    def test_ungueltige_parameter(self):
        for f in (lambda: lf.signal_gleitender_durchschnitt([1, 2], 1), lambda: lf.signal_momentum([1, 2], 0),
                  lambda: lf.signal_cape_relativ([1.0], 0.0), lambda: lf.positionen([1], -1)):
            with self.assertRaises(ValueError):
                f()

    def test_positionen_sind_um_verzoegerung_plus_eins_verschoben(self):
        s = [0, 1, 1, 0, 0, 1]
        self.assertEqual(lf.positionen(s, 1), [None, None, 0, 1, 1, 0])      # pos[j] = signal[j-2]: Signal von Kurs i wirkt ab Periode i+2
        self.assertEqual(lf.positionen(s, 0), [None, 0, 1, 1, 0, 0])         # pos[j] = signal[j-1]


class KeineZukunftTest(unittest.TestCase):
    """Ein Signal bzw. eine Position an einer Stelle darf sich nicht ändern, wenn man alle späteren Daten abschneidet oder verändert."""

    def test_signale_aendern_sich_nicht_wenn_man_die_zukunft_abschneidet(self):
        kurs = zufallskurs(400, 3)
        cape = [None] * 30 + [10 + 8 * random.Random(i).random() for i in range(370)]
        regeln = {
            "sma": lambda k, c: lf.signal_gleitender_durchschnitt(k, 20),
            "mom": lambda k, c: lf.signal_momentum(k, 30),
            "cape_fest": lambda k, c: lf.signal_cape_fest(c, 14.0),
            "cape_rel": lambda k, c: lf.signal_cape_relativ(c, 0.7, mindestens=40),
            "und": lambda k, c: lf.signal_und(lf.signal_gleitender_durchschnitt(k, 20), lf.signal_cape_relativ(c, 0.7, mindestens=40)),
        }
        for name, f in regeln.items():
            voll = f(kurs, cape)
            for m in (60, 150, 399):
                self.assertEqual(f(kurs[:m], cape[:m]), voll[:m], f"{name} schaut in die Zukunft (Länge {m})")

    def test_position_haengt_nur_von_kursen_bis_zwei_perioden_davor_ab(self):
        kurs = zufallskurs(300, 5)
        for v in (0, 1):
            voll = lf.positionen(lf.signal_gleitender_durchschnitt(kurs, 25), v)
            for j in (40, 100, 250):
                geaendert = kurs[:j - v] + [k * 3 for k in kurs[j - v:]]      # alles ab Kurs j-v ist anders
                pos = lf.positionen(lf.signal_gleitender_durchschnitt(geaendert, 25), v)
                self.assertEqual(pos[:j], voll[:j])

    def test_walk_forward_waehlt_nur_aus_daten_vor_dem_block(self):
        daten = monate(1900, 12 * 60)
        kurs = zufallskurs(len(daten), 11, drift=0.004, sigma=0.04)
        r1 = reihe_aus(kurs, daten, bar=0.002, kosten=0.001)
        regel = lf.regel_trend(10, (3, 6, 10, 12))
        w1 = lf.walk_forward(r1, regel, 1930, 10)
        a1 = lf.blockgrenzen(r1, 1930, 10)[1][0]                              # Beginn des zweiten Testblocks
        kurs2 = kurs[:a1] + [k * random.Random(7).uniform(0.2, 5) for k in kurs[a1:]]
        w2 = lf.walk_forward(reihe_aus(kurs2, daten, bar=0.002, kosten=0.001), regel, 1930, 10)
        # der erste und der zweite Block (Training endet vor a1) dürfen sich nicht ändern, die Zukunft schon
        self.assertEqual([f["parameter"] for f in w1["faltungen"][:2]], [f["parameter"] for f in w2["faltungen"][:2]])
        self.assertEqual([f["training_zuwachs"] for f in w1["faltungen"][:2]], [f["training_zuwachs"] for f in w2["faltungen"][:2]])
        self.assertNotEqual([f["training_zuwachs"] for f in w1["faltungen"][2:]], [f["training_zuwachs"] for f in w2["faltungen"][2:]])

    def test_walk_forward_setzt_die_testbloecke_lueckenlos_zusammen(self):
        daten = monate(1900, 12 * 40)
        r = reihe_aus(zufallskurs(len(daten), 2), daten, bar=0.001, kosten=0.001)
        w = lf.walk_forward(r, lf.regel_momentum(12, (3, 6, 12)), 1920, 10)
        bloecke = lf.blockgrenzen(r, 1920, 10)
        self.assertEqual(len(w["faltungen"]), len(bloecke))
        self.assertEqual(w["von"], bloecke[0][0])
        self.assertEqual(w["bis"], len(r) - 1)
        self.assertTrue(all(p in (0, 1) for p in w["pos"][w["von"]:w["bis"] + 1]))
        self.assertTrue(all(p is None for p in w["pos"][:w["von"]]))
        self.assertEqual([b[0] for b in bloecke[1:]], [b[1] + 1 for b in bloecke[:-1]])

    def test_zu_wenig_training_ist_ein_klarer_fehler(self):
        r = reihe_aus(zufallskurs(60, 1), monate(2000, 60))
        with self.assertRaises(ValueError):
            lf.walk_forward(r, lf.regel_trend(10, (6, 10)), 2001, 10)


class KostenVonHandTest(unittest.TestCase):
    def test_wertentwicklung_und_kosten_nachgerechnet(self):
        # Kurse 100 110 99 108.9 108.9 119.79 (Veränderungen +10 %, -10 %, +10 %, 0 %, +10 %); Bargeld +1 % je Periode; 1 % Kosten je Wechsel.
        # Positionen ab Periode 1: investiert, investiert, Bargeld (Wechsel), Bargeld, investiert (Wechsel).
        r = reihe_aus([100, 110, 99, 108.9, 108.9, 119.79], bar=0.01, kosten=0.01)
        pos = [None, 1, 1, 0, 0, 1]
        # Wert: 1.1 (P1) * 0.9 (P2) * [0.99 Kosten * 1.01 Bargeld] (P3) * 1.01 (P4) * [0.99 Kosten * 1.1 Kurs] (P5)
        erwartet = 1.1 * 0.9 * 0.99 * 1.01 * 1.01 * 0.99 * 1.1
        lr = lf.lauf(r, pos, 1, 5)
        self.assertAlmostEqual(math.exp(sum(lr)), erwartet, places=12)
        self.assertAlmostEqual(erwartet, 1.0887822108900, places=10)           # die Zahl, die man von Hand erhält
        k = lf.kennzahlen(r, lr, pos, 1)
        self.assertAlmostEqual(k["endwert"], erwartet, places=12)
        self.assertEqual(k["wechsel"], 2)
        self.assertAlmostEqual(k["investiert"], 3 / 5)
        # Halten kostet nichts und hat weder Wechsel noch Bargeld: 119.79 / 100
        h = lf.kennzahlen_halten(r, 1, 5)
        self.assertAlmostEqual(h["endwert"], 1.1979, places=12)
        self.assertEqual(h["wechsel"], 0)
        # ohne Kosten bleibt genau das Bruttoergebnis
        r0 = reihe_aus([100, 110, 99, 108.9, 108.9, 119.79], bar=0.01, kosten=0.0)
        self.assertAlmostEqual(math.exp(sum(lf.lauf(r0, pos, 1, 5))), 1.1 * 0.9 * 1.01 * 1.01 * 1.1, places=12)

    def test_kosten_je_wechsel_wirken_auf_den_ganzen_wert(self):
        # ständig wechselnde Position bei unverändertem Kurs und 0 % Bargeld: nach 4 Wechseln bleibt 0.99 hoch 4
        r = reihe_aus([100] * 6, bar=0.0, kosten=0.01)
        lr = lf.lauf(r, [None, 1, 0, 1, 0, 1], 1, 5)
        self.assertAlmostEqual(math.exp(sum(lr)), 0.99 ** 4, places=12)

    def test_anfangszustand_kostet_nichts(self):
        r = reihe_aus([100, 100, 100, 100], kosten=0.05)
        self.assertAlmostEqual(math.exp(sum(lf.lauf(r, [None, 1, 1, 1], 1, 3))), 1.0)
        self.assertAlmostEqual(math.exp(sum(lf.lauf(r, [None, 0, 0, 0], 1, 3))), 1.0)

    def test_fehlende_positionen_und_falsches_fenster(self):
        r = reihe_aus([100, 101, 102, 103])
        with self.assertRaises(ValueError):
            lf.lauf(r, [None, None, 1, 1], 1, 3)
        with self.assertRaises(ValueError):
            lf.lauf(r, [None, 1, 1, 1], 0, 3)
        with self.assertRaises(ValueError):
            lf.lauf(r, [None, 1, 1, 1], 1, 4)

    def test_barrendite_ohne_kosten_wachst_mit_dem_zins(self):
        r = reihe_aus([100] * 4, bar=0.02)
        self.assertAlmostEqual(math.exp(sum(lf.lauf(r, [None, 0, 0, 0], 1, 3))), 1.02 ** 3)

    def test_reihe_prueft_eingaben(self):
        with self.assertRaises(ValueError):
            lf.Reihe("x", ["2020-01-01", "2020-01-01", "2020-01-02"], [1, 1, 1], [0, 0, 0])      # Datum doppelt
        with self.assertRaises(ValueError):
            lf.Reihe("x", tage("2020-01-01", 3), [1, 0, 1], [0, 0, 0])                           # Kurs 0
        with self.assertRaises(ValueError):
            lf.Reihe("x", tage("2020-01-01", 3), [1, 1, 1], [0, 0, 0], kosten=1.0)
        with self.assertRaises(ValueError):
            lf.Reihe("x", tage("2020-01-01", 3), [1, 1], [0, 0, 0])


class KennzahlenTest(unittest.TestCase):
    def test_rueckgang_und_zeit_unter_wasser_von_hand(self):
        # Wertpfad 1, 1.1, 0.99, 0.891, 1.3365 (Faktoren 1.1, 0.9, 0.9, 1.5); Tage hintereinander
        r = reihe_aus([100, 110, 99, 89.1, 133.65])
        k = lf.kennzahlen_halten(r, 1, 4)
        self.assertAlmostEqual(k["max_rueckgang"], 1 - 0.891 / 1.1, places=12)
        # Hoch am 2. Tag (Index 1), Rückkehr über das Hoch am 5. Tag (Index 4): 3 Tage unter Wasser
        self.assertAlmostEqual(k["unter_wasser_jahre"], 3 / 365.25, places=12)
        self.assertAlmostEqual(k["unter_wasser_anteil"], 2 / 5)           # Punkte 2 und 3 liegen unter dem Hoch (von 5 Punkten)
        self.assertAlmostEqual(k["jahre"], 4 / 365.25)
        self.assertTrue(math.isclose(k["rendite_pa"], 1.3365 ** (365.25 / 4) - 1, rel_tol=1e-9))

    def test_nie_unter_wasser_ist_null_rueckgang(self):
        k = lf.kennzahlen_halten(reihe_aus([100, 101, 102, 103]), 1, 3)
        self.assertEqual(k["max_rueckgang"], 0.0)
        self.assertEqual(str(k["max_rueckgang"]), "0.0")
        self.assertEqual(k["unter_wasser_jahre"], 0.0)

    def test_noch_nicht_erholt_zaehlt_bis_zum_ende(self):
        k = lf.kennzahlen_halten(reihe_aus([100, 110, 100, 90]), 1, 3)
        self.assertAlmostEqual(k["unter_wasser_jahre"], 2 / 365.25)

    def test_reale_rendite_mit_preisniveau(self):
        r = reihe_aus([100, 110, 121], daten=["2020-01-01", "2021-01-01", "2022-01-01"], preisniveau=[100, 105, 110.25])
        k = lf.kennzahlen_halten(r, 1, 2)
        self.assertAlmostEqual(k["inflation_pa"], 0.05, places=3)
        self.assertAlmostEqual(k["rendite_pa_real"], 1.1 / 1.05 - 1, places=3)

    def test_teilfenster(self):
        daten = monate(2000, 120)
        r = reihe_aus(zufallskurs(120, 1), daten)
        a, b = lf.teilfenster(r, 1, 119, "2003-01-01", "2005-12-31")
        self.assertEqual((r.daten[a - 1], r.daten[b]), ("2003-01-01", "2005-12-01"))
        self.assertIsNone(lf.teilfenster(r, 1, 119, "2009-06-01", ""))      # weniger als 24 Perioden


class ZufallTest(unittest.TestCase):
    def test_umordnen_behaelt_wechsel_und_marktquote(self):
        p = [1] * 5 + [0] * 3 + [1] * 2 + [0] * 7 + [1] * 4 + [0] * 1
        sp = lf.spannen(p)
        self.assertEqual(sp, [(1, 5), (0, 3), (1, 2), (0, 7), (1, 4), (0, 1)])
        rng = random.Random(4)
        verschieden = set()
        for _ in range(50):
            q = lf.zufalls_positionen(sp, rng)
            self.assertEqual(len(q), len(p))
            self.assertEqual(sum(q), sum(p))
            self.assertEqual(len(lf.spannen(q)), len(sp))
            self.assertEqual(q[0], p[0])
            verschieden.add(tuple(q))
        self.assertGreater(len(verschieden), 5)

    def test_gleicher_startwert_gleiches_ergebnis(self):
        r = reihe_aus(zufallskurs(400, 8), bar=0.0, kosten=0.001)
        pos = [None] + lf.positionen(lf.signal_gleitender_durchschnitt(r.kurs, 20), 0)[1:]
        pos[:25] = [None] * 25
        a = lf.zufallsvergleich(r, pos, 30, 399, laeufe=40, seed=3)
        b = lf.zufallsvergleich(r, pos, 30, 399, laeufe=40, seed=3)
        self.assertEqual(a, b)

    def test_hellseher_schlaegt_jeden_zufallslauf(self):
        # Position = Vorzeichen der echten nächsten Rendite (verbotener Blick in die Zukunft): muss alle Zufallsläufe übertreffen
        kurs = zufallskurs(600, 21, drift=0.0, sigma=0.02)
        r = reihe_aus(kurs, bar=0.0, kosten=0.0)
        pos = [None] + [1 if kurs[j] > kurs[j - 1] else 0 for j in range(1, 600)]
        z = lf.zufallsvergleich(r, pos, 1, 599, laeufe=200, seed=1)
        self.assertEqual(z["anteil_besser_rendite"], 0.0)
        self.assertEqual(z["anteil_besser_rueckgang"], 0.0)

    def test_zufallspositionen_liegen_in_der_mitte(self):
        # eine Regel, die nichts weiß (Positionen selbst zufällig gezogen): Anteil besser liegt weit weg von 0 und 100
        rng = random.Random(5)
        r = reihe_aus(zufallskurs(800, 6, drift=0.0004, sigma=0.01), bar=0.0, kosten=0.0005)
        pos = [None] + [1 if rng.random() < 0.7 else 0 for _ in range(799)]
        z = lf.zufallsvergleich(r, pos, 1, 799, laeufe=300, seed=2)
        self.assertGreater(z["anteil_besser_rendite"], 5)
        self.assertLess(z["anteil_besser_rendite"], 95)

    def test_zu_wenige_wechsel_keine_aussage(self):
        r = reihe_aus(zufallskurs(50, 1))
        self.assertIsNone(lf.zufallsvergleich(r, [None] + [1] * 49, 1, 49))
        self.assertIsNone(lf.zufallsvergleich(r, [None] + [1] * 20 + [0] * 29, 1, 49))


class KalibrierungTest(unittest.TestCase):
    """Gegenprobe mit künstlichen Zufallskursen ohne jede Struktur: Der Zufallsvergleich darf der Regel dort keinen Vorteil zeigen."""

    def test_auf_reinem_zufall_liegt_der_anteil_besser_in_der_mitte(self):
        daten = monate(1900, 12 * 80)
        anteile, treffer = [], 0
        for seed in range(10):
            kurs = zufallskurs(len(daten), 500 + seed, drift=0.0065, sigma=0.043)
            r = reihe_aus(kurs, daten, bar=0.0025, kosten=0.001)
            pos = lf.positionen(lf.signal_gleitender_durchschnitt(kurs, 10), 1)
            z = lf.zufallsvergleich(r, pos, r.index_ab("1930-01-01") + 1, len(r) - 1, laeufe=100, seed=seed)
            anteile.append(z["anteil_besser_rendite"])
            treffer += z["anteil_besser_rendite"] < 5
        self.assertGreater(sum(anteile) / len(anteile), 30)
        self.assertLess(sum(anteile) / len(anteile), 70)
        self.assertLessEqual(treffer, 2)                                # bei 10 Reihen sind höchstens ein, zwei Zufallstreffer plausibel


class PruefeReiheTest(unittest.TestCase):
    def setUp(self):
        self.daten = monate(1900, 12 * 60)
        self.kurs = zufallskurs(len(self.daten), 31, drift=0.004, sigma=0.035)
        self.cape = [None] * 20 + [15 + 10 * random.Random(i).random() for i in range(len(self.daten) - 20)]
        self.plan = lf.Plan("Test", 10, (5, 10), 12, (6, 12), 1930, 10,
                            (("bis 1949", "", "1949-12-31"), ("ab 1950", "1950-01-01", "")), mit_cape=True, einheit="Monate")

    def test_alle_regeln_mit_kennzahlen_zufall_und_teilzeitraeumen(self):
        r = reihe_aus(self.kurs, self.daten, bar=0.002, kosten=0.001, cape=self.cape)
        res = lf.pruefe_reihe(r, self.plan, laeufe=30, seed=1)
        namen = [z["regel"] for z in res["fenster_a"]["zeilen"]]
        self.assertEqual(namen, ["Trend (fest 10)", "Momentum (fest 12)", "Trend (Walk-forward)", "Momentum (Walk-forward)",
                                 "CAPE fest (Walk-forward)", "CAPE relativ (Walk-forward)", "Trend + CAPE (Walk-forward)"])
        self.assertEqual([z["regel"] for z in res["fenster_b"]["zeilen"]], ["Trend (fest 10)", "Momentum (fest 12)"])
        for z in res["fenster_a"]["zeilen"]:
            self.assertEqual(z["kennzahlen"]["von"], res["fenster_a"]["von"])
            self.assertEqual(z["kennzahlen"]["bis"], res["fenster_a"]["bis"])
            self.assertEqual([t["name"] for t in z["teile"]], ["bis 1949", "ab 1950"])
        self.assertEqual(res["fenster_a"]["halten"]["wechsel"], 0)
        json.dumps(res)                                                      # ist JSON-tauglich

    def test_ohne_cape_keine_bewertungsregeln_und_ohne_zufall_kein_zufallsblock(self):
        plan = lf.Plan("Test", 10, (5, 10), 12, (6, 12), 1930, 10)
        res = lf.pruefe_reihe(reihe_aus(self.kurs, self.daten, bar=0.0, kosten=0.001), plan, laeufe=0)
        self.assertEqual(len(res["fenster_a"]["zeilen"]), 4)
        self.assertTrue(all(z["zufall"] is None for z in res["fenster_a"]["zeilen"]))

    def test_empfindlichkeit_hohe_kosten_schaden_und_verzoegerung_wirkt(self):
        r = reihe_aus(self.kurs, self.daten, bar=0.002, kosten=0.001)
        plan = lf.Plan("Test", 10, (5, 10), 12, (6, 12), 1930, 10)
        e = lf.empfindlichkeit(r, plan, [0.0, 0.001, 0.02])
        self.assertEqual(len(e), 2 * 3 * 2)                                  # 2 Verzögerungen x 3 Kosten x 2 feste Regeln
        je = {(x["regel"], x["verzoegerung"], x["kosten"]): x["rendite_pa"] for x in e}
        for regel in ("Trend (fest 10)", "Momentum (fest 12)"):
            for v in (0, 1):
                self.assertGreater(je[(regel, v, 0.0)], je[(regel, v, 0.001)])
                self.assertGreater(je[(regel, v, 0.001)], je[(regel, v, 0.02)])


class ReihenBauenTest(unittest.TestCase):
    def test_vormonat_und_samstag(self):
        self.assertEqual(ll.vormonat("2020-01-15"), "2019-12")
        self.assertEqual(ll.vormonat("2020-03-01"), "2020-02")
        d = [("1950-01-06", 1.0), ("1950-01-07", 2.0), ("1950-01-08", 3.0), ("1950-01-09", 4.0)]      # Freitag, Samstag, Sonntag, Montag
        self.assertEqual([x[0] for x in ll.ohne_samstag(d)], ["1950-01-06", "1950-01-08", "1950-01-09"])

    def test_barrendite_mit_zins_des_vormonats_und_tagen(self):
        z = ll.Monatswerte([("2019-12-01", 12.0), ("2020-01-01", 0.0)])
        b = ll.bar_renditen(["2020-01-15", "2020-01-16", "2020-01-19"], z)
        self.assertEqual(b[0], 0.0)
        self.assertAlmostEqual(b[1], 1.12 ** (1 / 365) - 1, places=15)      # Zins von Dezember 2019 (nicht der Januar-Wert 0)
        self.assertAlmostEqual(b[2], 1.12 ** (3 / 365) - 1, places=15)      # 3 Kalendertage
        self.assertEqual(ll.bar_renditen(["2020-01-15", "2020-01-16"], None), [0.0, 0.0])
        with self.assertRaises(ll.LangFehler):
            ll.bar_renditen(["2025-01-15", "2025-01-16"], ll.Monatswerte([("2030-01-01", 1.0)]))

    def test_erster_monat_der_zinsreihe_nutzt_den_eigenen_wert(self):
        z = ll.Monatswerte([("1871-01-01", 5.0), ("1871-02-01", 6.0)])
        self.assertEqual(z.fuer("1871-01-20"), 5.0)
        self.assertEqual(z.fuer("1871-02-20"), 5.0)                          # Vormonat = Januar
        self.assertIsNone(z.fuer("1870-06-01"))

    def test_dividenden_naeherung(self):
        # Kurs bleibt gleich, Rendite 3.65 % im Jahr: ein Tag bringt 3.65 % / 365 = 0.01 %
        tr = ll.mit_dividenden(["2020-02-01", "2020-02-02"], [100.0, 100.0], ll.Monatswerte([("2020-01-01", 0.0365)]))
        self.assertAlmostEqual(tr[1] / tr[0], 1.0001, places=12)

    def test_bauen_aus_gespeicherten_reihen(self):
        with tempfile.TemporaryDirectory() as d:
            s = Speicher(Path(d))
            try:
                tg = tage("2012-01-01", 400)
                s.schreiben("btc", [(t, 10.0 + i) for i, t in enumerate(tg)], "test")
                s.schreiben("shiller_zins10", [(m, 4.0) for m in monate(2011, 40)], "test")
                r = ll.bauen(s, "btc", "zins10")
                self.assertEqual(len(r), 400)
                self.assertEqual(r.kosten, 0.005)
                self.assertAlmostEqual(r.bar[1], 1.04 ** (1 / 365) - 1, places=15)
                self.assertEqual(ll.bauen(s, "btc", "null", kosten_krypto=0.2).kosten, 0.002)
                with self.assertRaises(ll.LangFehler) as ctx:
                    ll.bauen(s, "gold", "null")
                self.assertIn("historie laden gold_monat", str(ctx.exception))
                with self.assertRaises(ll.LangFehler):
                    ll.bauen(s, "uk", "zins10")
                with self.assertRaises(ll.LangFehler):
                    ll.bauen(s, "unbekannt", "null")
            finally:
                s.close()

    def test_sp_aus_real_cpi_cape_und_ohne_den_vorlaeufigen_monat(self):
        with tempfile.TemporaryDirectory() as d:
            s = Speicher(Path(d))
            try:
                m = monate(1900, 36)
                s.schreiben("shiller_real_tr", [(x, 100.0 + i) for i, x in enumerate(m)], "test")
                s.schreiben("shiller_cpi", [(x, 10.0 + 0.1 * i) for i, x in enumerate(m)], "test")
                s.schreiben("shiller_sp", [(x, 5.0 + 0.2 * i) for i, x in enumerate(m)], "test")
                s.schreiben("shiller_cape", [(x, 15.0) for x in m[12:]], "test")
                r = ll.bauen(s, "sp", "null")
                self.assertEqual(len(r), 35)                                  # letzter Monat (vorläufig) fehlt
                self.assertAlmostEqual(r.kurs[3], 103.0 * 10.3)               # nominal = real mal Preisniveau
                self.assertEqual(r.preisniveau[3], 10.3)
                self.assertEqual(r.signal_kurs[3], 5.6)                       # Signale auf dem Kursindex ohne Dividenden
                self.assertIsNone(r.cape[0])
                self.assertEqual(r.cape[12], 15.0)
            finally:
                s.close()


class EreignisseTest(unittest.TestCase):
    def test_ereignisauswertung_mit_n_und_vergleichswert(self):
        with tempfile.TemporaryDirectory() as d:
            s = Speicher(Path(d))
            try:
                d0 = date(1885, 2, 16)
                tg = [(d0 + timedelta(days=7 * i)).isoformat() for i in range(7300)]
                s.schreiben("dow", [(t, 100 + 0.5 * i) for i, t in enumerate(tg)], "test")
                s.schreiben("shiller_sp", [(m, 10 + 0.1 * i) for i, m in enumerate(monate(1871, 1870))], "test")
                e = ll.ereignis_auswertung(s)
            finally:
                s.close()
        zeilen = {(z["reihe"], z["art"]): z for z in e["zeilen"]}
        self.assertEqual(set(zeilen), {(r, a) for r in ("dow", "shiller_sp") for a in ll.EREIGNIS_ARTEN})
        krieg = zeilen[("dow", "krieg")]
        self.assertGreaterEqual(krieg["n_gerechnet"], 5)
        self.assertIsNotNone(krieg["statistik"]["12"]["median"])
        self.assertGreater(krieg["statistik"]["12"]["median"], 0)             # steigende Testreihe
        self.assertEqual(krieg["statistik"]["tief"]["median"], 0.0)           # nie ein Rückgang
        for reihe in ("dow", "shiller_sp"):
            b = e["basis"][reihe]
            self.assertGreater(b["12"]["n"], 1000)
            self.assertEqual(b["tief"]["anteil_negativ"], 0.0)
        json.dumps(e)


class KommandozeileTest(unittest.TestCase):
    def aufruf(self, ordner: str, *argumente):
        aus, fehler = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"KITRADER_DATEN": ordner, "KITRADER_KONFIG": str(Path(ordner) / "keine.toml")}), \
                contextlib.redirect_stdout(aus), contextlib.redirect_stderr(fehler):
            code = main(list(argumente))
        return code, aus.getvalue(), fehler.getvalue()

    def test_labor_lang_rechnet_zeigt_und_speichert(self):
        with tempfile.TemporaryDirectory() as d:
            s = Speicher(Path(d))
            try:
                s.schreiben("btc", [(t, k) for t, k in zip(tage("2012-01-01", 3300), zufallskurs(3300, 17, drift=0.001, sigma=0.03))], "test")
                s.schreiben("shiller_zins10", [(m, 3.0) for m in monate(2011, 130)], "test")
            finally:
                s.close()
            md = Path(d) / "lang.md"
            code, aus, fehler = self.aufruf(d, "labor", "lang", "--reihen", "btc", "--zufall", "20", "--prozesse", "1",
                                            "--ohne-ereignisse", "--markdown", str(md))
            self.assertEqual(code, 0, fehler)
            for text in ("Kaufen und Halten", "Trend (fest 200)", "Momentum (fest 365)", "Trend (Walk-forward)", "Bargeld 0 %",
                         "Kosten 0,50 % je Wechsel", "Kein Erfolgsversprechen", "Kurzfazit"):
                self.assertIn(text, aus)
            erg = json.loads((Path(d) / "labor" / "lang.json").read_text(encoding="utf-8"))
            self.assertEqual([(r["reihe"], r["bar"]) for r in erg["ergebnisse"]], [("btc", "null"), ("btc", "zins10")])
            self.assertEqual(erg["einstellungen"]["kosten_krypto_prozent"], 0.5)
            self.assertIsNone(erg["ereignisse"])
            text = md.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("### Übersicht"))
            self.assertIn("**Bitcoin täglich (US-Dollar) - Bargeld 0 %**", text)
            # nur anzeigen, nichts rechnen
            code, aus2, _ = self.aufruf(d, "labor", "lang", "--aus-json", str(Path(d) / "labor" / "lang.json"))
            self.assertEqual(code, 0)
            self.assertIn("Trend (fest 200)", aus2)

    def test_fehlende_reihe_nennt_den_befehl_zum_laden(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, fehler = self.aufruf(d, "labor", "lang", "--reihen", "gold", "--zufall", "5", "--prozesse", "1")
            self.assertEqual(code, 2)
            self.assertIn("historie laden", fehler)

    def test_unbekannte_reihe_und_falsche_kosten(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, fehler = self.aufruf(d, "labor", "lang", "--reihen", "xyz")
            self.assertEqual(code, 2)
            self.assertIn("Unbekannte Reihe", fehler)
            code, _, fehler = self.aufruf(d, "labor", "lang", "--kosten-aktie", "-1")
            self.assertEqual(code, 2)

    def test_der_alte_labor_befehl_bleibt_unveraendert(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, fehler = self.aufruf(d, "labor", "--paare", "BTC/EUR", "--takte", "5m", "--prozesse", "1")
            self.assertEqual(code, 2)
            self.assertIn("daten kraken", fehler)


class ZusammenfassungTest(unittest.TestCase):
    def test_zaehlt_ueber_alle_zeilen(self):
        def zeile(rendite, rueckgang, schwankung, ar, ak):
            return {"kennzahlen": {"rendite_pa": rendite, "max_rueckgang": rueckgang, "schwankung": schwankung},
                    "zufall": {"anteil_besser_rendite": ar, "anteil_besser_rueckgang": ak}}
        halten = {"rendite_pa": 0.08, "max_rueckgang": 0.6, "schwankung": 0.15}
        erg = {"ergebnisse": [{"bar": "null", "fenster_a": {"halten": halten, "zeilen": [zeile(0.07, 0.3, 0.1, 3, 40), zeile(0.09, 0.5, 0.12, 50, 2)]}},
                              {"bar": "zins10", "fenster_a": {"halten": halten, "zeilen": [zeile(0.09, 0.3, 0.1, 1, 1)]}}]}
        z = ll.zusammenfassung(erg)
        self.assertEqual(z["zeilen"], 3)
        self.assertEqual(z["schlagen_halten_rendite"], 2)
        self.assertEqual(z["schlagen_halten_rendite_bargeld_null"], 1)
        self.assertEqual(z["rueckgang_ein_drittel_kleiner"], 2)             # 0.3 <= 0.4 (ja), 0.5 (nein), 0.3 (ja)
        self.assertEqual(z["beides"], 1)
        self.assertEqual(z["zufall_rendite_unter_5"], 2)
        self.assertEqual(z["zufall_rueckgang_unter_5"], 2)
        self.assertAlmostEqual(z["zufallstreffer_erwartet"], 0.15)


if __name__ == "__main__":
    unittest.main()
