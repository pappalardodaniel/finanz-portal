"""Backtest: Kennzahlen an einem von Hand nachgerechneten Beispiel, Kosten, Lücken, Risikoprüfung, Walk-forward."""
import json
import math
import statistics
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kitrader.backtest import (Handel, als_dict, backtest, kennzahlen, kerzen_aus_csv, tabelle_einzel,
                               tabelle_walk_forward, walk_forward)
from kitrader.broker.basis import Kerze
from kitrader.einstellungen import mit_standard
from kitrader.strategie import Signal, Strategie

from .daten import zufallskerzen

UTC = timezone.utc
TAG1 = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
TAG2 = datetime(2026, 3, 3, 10, 0, tzinfo=UTC)


class Skript(Strategie):
    """Gibt Signale nach Plan: {Anzahl Kerzen: Signal}. Nur wenn keine Position offen ist."""
    name = "skript"

    def __init__(self, plan=None, immer=None):
        super().__init__()
        self.plan, self.immer = plan or {}, immer

    def bewerten(self, kerzen, lage):
        if lage.menge() != 0:
            return None
        return self.immer if self.immer else self.plan.get(len(kerzen))


def einst(kosten=None, risiko=None, backtest=None):
    return mit_standard({
        "kosten": {"gebuehr_fest": 1.0, "gebuehr_prozent": 0.0, "schlupf_prozent": 0.0, "spread_prozent": 0.0, **(kosten or {})},
        "risiko": {"standard_markt": "krypto", "short_erlaubt": True, "position_max_prozent": 100.0, **(risiko or {})},
        "backtest": backtest or {}})


def kerzen(tag, werte):
    """werte: Liste (o, h, l, c); 5-Minuten-Kerzen ab 10:00 des Tages."""
    return [Kerze(tag + timedelta(minutes=5 * i), *w) for i, w in enumerate(werte)]


def beispiel_kerzen():
    tag1 = kerzen(TAG1, [(100, 100.5, 99.5, 100),      # 0: Signal long (Einstieg 100, Stop 98, Ziel 104)
                         (100, 105, 99.5, 104.5),       # 1: Einstieg zum Open 100, Ziel 104 erreicht
                         (104, 104, 104, 104),
                         (104, 104, 104, 104)])
    tag2 = kerzen(TAG2, [(110, 110.5, 109.5, 110),     # 4: Signal short (Einstieg 110, Stop 112, Ziel 106)
                         (110, 112.5, 109, 111),         # 5: Einstieg zum Open 110, Stop 112 getroffen
                         (111, 111, 111, 111),
                         (111, 111, 111, 111)])
    return tag1 + tag2


PLAN = {1: Signal("long", 100, 98, 104, 0.8, "T1"), 5: Signal("short", 110, 112, 106, 0.8, "T2")}


class HandrechnungTest(unittest.TestCase):
    def test_kennzahlen_beispiel(self):
        e = backtest(Skript(PLAN), beispiel_kerzen(), einst(), "TEST", "krypto")
        self.assertEqual(len(e.trades), 2)
        t1, t2 = e.trades
        # Trade 1: 25 Stück (50 Risiko / 2 Abstand), Kauf 100, Ziel 104: Brutto 100, Gebühren 2
        self.assertEqual((t1.richtung, t1.menge, t1.einstieg, t1.ausstieg, t1.grund), ("long", 25, 100, 104, "ziel"))
        self.assertAlmostEqual(t1.brutto, 100.0)
        self.assertAlmostEqual(t1.netto, 98.0)
        # Trade 2: Eigenkapital 10.098 -> Risiko 50,49 / 2 = 25,2 -> 25 Stück; Short 110, Stop 112: Brutto -50
        self.assertEqual((t2.richtung, t2.menge, t2.einstieg, t2.ausstieg, t2.grund), ("short", 25, 110, 112, "stop"))
        self.assertAlmostEqual(t2.brutto, -50.0)
        self.assertAlmostEqual(t2.netto, -52.0)

        k = e.kennzahlen()
        self.assertAlmostEqual(k["netto"], 46.0)                       # 98 - 52
        self.assertAlmostEqual(k["vor_kosten"], 50.0)
        self.assertAlmostEqual(k["gebuehren"], 4.0)
        self.assertAlmostEqual(k["spread_schlupf"], 0.0)
        self.assertEqual(k["trades"], 2)
        self.assertAlmostEqual(k["trefferquote"], 50.0)
        self.assertAlmostEqual(k["profitfaktor"], 98 / 52)
        self.assertAlmostEqual(k["gewinn_schnitt"], 98.0)
        self.assertAlmostEqual(k["verlust_schnitt"], -52.0)
        self.assertAlmostEqual(k["max_rueckgang"], 52.0)               # Spitze 10.098, danach 10.046
        self.assertAlmostEqual(k["max_rueckgang_prozent"], 52 / 10098 * 100)
        # Sharpe: Tagesergebnisse +98 (bei 10.000) und -52 (bei 10.098)
        r = [98 / 10000, -52 / 10098]
        self.assertEqual(len(e.tages_pnl), 2)
        self.assertAlmostEqual(e.tages_pnl[0], 98.0)
        self.assertAlmostEqual(e.tages_pnl[1], -52.0)
        self.assertAlmostEqual(k["sharpe"], statistics.fmean(r) / statistics.stdev(r) * math.sqrt(252))

    def test_ergebnis_nach_kosten_stimmt_mit_kontostand_ueberein(self):
        e = backtest(Skript(PLAN), beispiel_kerzen(), einst(), "TEST", "krypto")
        self.assertAlmostEqual(sum(e.tages_pnl), e.kennzahlen()["netto"])

    def test_spread_schlupf_und_gebuehr(self):
        # Spread 1 % (halber Spread 0,5 % je Seite), Schlupf 0,5 %, Gebühr 1 + 0,1 %
        k = einst(kosten={"spread_prozent": 1.0, "schlupf_prozent": 0.5, "gebuehr_prozent": 0.1})
        e = backtest(Skript({1: PLAN[1]}), beispiel_kerzen(), k, "TEST", "krypto")
        t = e.trades[0]
        # Ask am Signal = 100 x 1,005 = 100,5 -> Abstand zum Stop 2,5 -> 50 / 2,5 = 20 Stück
        self.assertEqual(t.menge, 20)
        self.assertAlmostEqual(t.einstieg, 100 * 1.005 * 1.005)       # 101,0025
        self.assertAlmostEqual(t.ausstieg, 104.0)                      # Ziel = Limit-Order, ohne Spread und Schlupf
        gebuehren = (1 + 0.001 * 20 * t.einstieg) + (1 + 0.001 * 20 * 104.0)
        self.assertAlmostEqual(t.gebuehren, gebuehren)
        self.assertAlmostEqual(t.brutto, 80.0)
        self.assertAlmostEqual(t.kosten_markt, (101.0025 - 100) * 20)
        self.assertAlmostEqual(t.netto, (104.0 - 101.0025) * 20 - gebuehren)    # Ergebnis nach Kosten, direkt gerechnet

    def test_maker_gebuehr_gilt_nur_fuers_ziel_als_limit_order(self):
        # Taker 0,8 % (Einstieg, Stop), Maker 0,4 % (Ziel); sonst keine Kosten
        k = einst(kosten={"gebuehr_fest": 0.0, "gebuehr_prozent": 0.8, "gebuehr_maker_prozent": 0.4})
        t = backtest(Skript({1: PLAN[1]}), beispiel_kerzen(), k, "TEST", "krypto").trades[0]          # Ziel erreicht
        self.assertEqual(t.grund, "ziel")
        self.assertAlmostEqual(t.gebuehren, 0.008 * 25 * 100 + 0.004 * 25 * 104)
        werte = kerzen(TAG1, [(100, 100.5, 99.5, 100), (100, 101, 97, 98), (98, 98, 98, 98), (98, 98, 98, 98)])
        s = backtest(Skript({1: PLAN[1]}), werte, k, "TEST", "krypto").trades[0]                      # Stop getroffen
        self.assertEqual(s.grund, "stop")
        self.assertAlmostEqual(s.gebuehren, 0.008 * 25 * 100 + 0.008 * 25 * 98)                       # Stop = Marktorder = Taker

    def test_ohne_maker_einstellung_gilt_ueberall_dieselbe_gebuehr(self):
        k = einst(kosten={"gebuehr_fest": 0.0, "gebuehr_prozent": 0.8})
        t = backtest(Skript({1: PLAN[1]}), beispiel_kerzen(), k, "TEST", "krypto").trades[0]
        self.assertAlmostEqual(t.gebuehren, 0.008 * 25 * 100 + 0.008 * 25 * 104)

    def test_luecke_ueber_den_stop_wird_zum_schlechteren_kurs_gefuellt(self):
        werte = kerzen(TAG1, [(100, 100.5, 99.5, 100), (100, 101, 99, 100), (95, 96, 94, 95), (95, 95, 95, 95)])
        e = backtest(Skript({1: PLAN[1]}), werte, einst(), "TEST", "krypto")
        t = e.trades[0]
        self.assertEqual((t.grund, t.ausstieg), ("stop", 95))          # nicht 98
        self.assertAlmostEqual(t.brutto, -5 * 25)

    def test_stop_und_ziel_in_derselben_kerze_gilt_der_stop(self):
        werte = kerzen(TAG1, [(100, 100.5, 99.5, 100), (100, 106, 97, 100), (100, 100, 100, 100), (100, 100, 100, 100)])
        e = backtest(Skript({1: PLAN[1]}), werte, einst(), "TEST", "krypto")
        self.assertEqual(e.trades[0].grund, "stop")

    def test_tagesende_stellt_glatt(self):
        flach = kerzen(TAG1, [(100, 100.5, 99.5, 100)] + [(100, 100.2, 99.8, 100)] * 3)
        e = backtest(Skript({1: Signal("long", 100, 90, 120, 0.8)}), flach, einst(), "TEST", "krypto")
        self.assertEqual(len(e.trades), 1)
        self.assertEqual(e.trades[0].grund, "tagesende")
        self.assertAlmostEqual(e.trades[0].netto, -2.0)               # nur die Gebühren

    def test_ohne_tagesende_wird_am_datenende_geschlossen(self):
        flach = kerzen(TAG1, [(100, 100.5, 99.5, 100)] + [(100, 100.2, 99.8, 100)] * 3)
        e = backtest(Skript({1: Signal("long", 100, 90, 120, 0.8)}), flach, einst(backtest={"tagesende_flat": False}),
                     "TEST", "krypto")
        self.assertEqual(e.trades[0].grund, "ende")


class RisikopruefungImBacktestTest(unittest.TestCase):
    def test_short_wird_blockiert_wenn_nicht_erlaubt(self):
        e = backtest(Skript(PLAN), beispiel_kerzen(), einst(risiko={"short_erlaubt": False}), "TEST", "krypto")
        self.assertEqual([t.richtung for t in e.trades], ["long"])

    def test_signal_ohne_stop_wird_nie_gehandelt(self):
        e = backtest(Skript({1: Signal("long", 100, None, 104)}), beispiel_kerzen(), einst(), "TEST", "krypto")
        self.assertEqual(e.trades, [])

    def test_tagesverlustgrenze_sperrt_den_rest_des_tages(self):
        werte = kerzen(TAG1, [(100, 100.5, 99.5, 100), (100, 100.5, 97, 98), (98, 100, 97, 100), (100, 100, 100, 100)])
        immer = Signal("long", 100, 98, None, 0.8)
        frei = backtest(Skript(immer=immer), werte, einst(risiko={"tagesverlust_max": 1e9, "tagesverlust_max_prozent": 100}),
                        "TEST", "krypto")
        gesperrt = backtest(Skript(immer=immer), werte, einst(risiko={"tagesverlust_max": 5}), "TEST", "krypto")
        self.assertGreaterEqual(len(frei.trades), 2)
        self.assertEqual(len(gesperrt.trades), 1)

    def test_sicherheit_min_gilt_auch_im_backtest(self):
        e = einst()
        e["autopilot"]["sicherheit_min"] = 0.9                  # die Signale im Plan haben 0,8
        self.assertEqual(backtest(Skript(PLAN), beispiel_kerzen(), e, "TEST", "krypto").trades, [])

    def test_handelszeiten_gelten_auch_im_backtest(self):
        zu = einst(risiko={"standard_markt": "boerse"})
        zu["handelszeiten"]["boerse"] = {"zeitzone": "UTC", "von": "20:00", "bis": "21:00", "tage": [0, 1, 2, 3, 4]}
        self.assertEqual(backtest(Skript(PLAN), beispiel_kerzen(), zu, "TEST").trades, [])


class KennzahlenTest(unittest.TestCase):
    def handel(self, netto):
        z = TAG1
        return Handel("long", 1, z, z, 100, 100 + netto, netto, 0.0, 0.0, "ziel")

    def test_keine_trades(self):
        k = kennzahlen([], [], 10000)
        self.assertEqual((k["trades"], k["netto"]), (0, 0))
        self.assertIsNone(k["trefferquote"])
        self.assertIsNone(k["profitfaktor"])
        self.assertIsNone(k["sharpe"])

    def test_nur_gewinne(self):
        k = kennzahlen([self.handel(5), self.handel(3)], [8.0], 10000)
        self.assertEqual(k["profitfaktor"], math.inf)
        self.assertEqual(k["trefferquote"], 100.0)
        self.assertIsNone(k["verlust_schnitt"])
        self.assertIsNone(k["sharpe"])                                  # nur ein Tag

    def test_maximaler_rueckgang_ueber_mehrere_trades(self):
        trades = [self.handel(x) for x in (10, -4, -3, 5, -12, 20)]
        k = kennzahlen(trades, [], 1000)
        # Kurve: 1000, 1010, 1006, 1003, 1008, 996, 1016 -> tiefster Fall von 1010 auf 996 = 14
        self.assertAlmostEqual(k["max_rueckgang"], 14.0)
        self.assertAlmostEqual(k["max_rueckgang_prozent"], 14 / 1010 * 100)


class DatenUndAusgabeTest(unittest.TestCase):
    def test_csv_iso_unix_und_kopfzeile(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "k.csv"
            p.write_text("t,o,h,l,c,v\n"
                         "1772445900,100,101,99,100.5,10\n"                      # 2026-03-02 10:05 UTC, Sekunden
                         "2026-03-02T10:00:00Z,99,100,98,100,20\n"                # ISO, unsortiert
                         "1772446500000,100.5,102,100,101,5\n", encoding="utf-8")   # 10:15, Millisekunden
            ks = kerzen_aus_csv(p)
            self.assertEqual([k.t for k in ks], [TAG1, TAG1 + timedelta(minutes=5), TAG1 + timedelta(minutes=15)])
            self.assertEqual((ks[0].o, ks[0].c, ks[0].v), (99, 100, 20))

    def test_csv_fehler(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "k.csv"
            p.write_text("t,o,h,l,c,v\n2026-03-02T10:00:00Z,abc,1,1,1,1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                kerzen_aus_csv(p)
            p.write_text("t,o,h,l,c,v\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                kerzen_aus_csv(p)

    def test_tabelle_und_dict(self):
        e = backtest(Skript(PLAN), beispiel_kerzen(), einst(), "TEST", "krypto")
        text = tabelle_einzel(e)
        self.assertIn("Netto-Ergebnis", text)
        self.assertIn("46,00", text)
        self.assertIn("weniger als 30 Trades", text)
        d = als_dict(e)
        self.assertEqual(d["kennzahlen"]["trades"], 2)
        json.dumps(d, default=str)


class VorgeschichteTest(unittest.TestCase):
    """handel_ab: Kerzen davor sind nur Verlauf für die Strategie, gehandelt und gezählt wird erst danach."""

    def test_vorlauf_wird_nicht_gehandelt_und_nicht_gezaehlt(self):
        e = backtest(Skript(PLAN), beispiel_kerzen(), einst(), "TEST", "krypto", handel_ab=TAG2)
        self.assertEqual(len(e.trades), 1)                              # Signal 1 liegt im Vorlauf
        t = e.trades[0]
        self.assertEqual((t.richtung, t.grund, t.menge), ("short", "stop", 25))
        self.assertAlmostEqual(t.netto, -52.0)
        self.assertEqual(e.von, TAG2)
        self.assertEqual(len(e.tages_pnl), 1)                           # nur der Testtag, keine Null-Tage aus dem Vorlauf
        self.assertAlmostEqual(e.tages_pnl[0], -52.0)

    def test_strategie_sieht_im_test_die_vorgeschichte_aber_nie_die_zukunft(self):
        gesehen = []

        class Beobachter(Strategie):
            name = "beobachter"

            def bewerten(self, kerzen, lage):
                gesehen.append((kerzen[-1].t, len(kerzen), kerzen[0].t))
                return None

        werte = zufallskerzen(tage=10, seed=5, takt_min=60, pro_tag=24)
        e = mit_standard({"risiko": {"standard_markt": "krypto"}, "backtest": {"faltungen": 2}})
        with mock.patch.dict("kitrader.strategie.STRATEGIEN", {"beobachter": Beobachter}):
            wf = walk_forward("beobachter", werte, e, "TEST", "krypto", gitter={})
        test_von = wf["test"].von
        im_test = [g for g in gesehen if g[0] >= test_von]
        erste = next(g for g in im_test if g[0] == test_von)
        self.assertGreater(erste[1], 24)                                # schon die erste Testkerze sieht den Verlauf davor
        self.assertTrue(all(g[2] <= g[0] for g in gesehen))             # nie Kerzen aus der Zukunft
        # ohne Vorgeschichte sieht die erste Testkerze nur sich selbst
        gesehen.clear()
        with mock.patch.dict("kitrader.strategie.STRATEGIEN", {"beobachter": Beobachter}):
            wf2 = walk_forward("beobachter", werte, e, "TEST", "krypto", gitter={}, vorgeschichte=False)
        erste2 = next(g for g in gesehen if g[0] == wf2["test"].von)
        self.assertEqual(erste2[1], 1)


class WalkForwardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kerzen = zufallskerzen(tage=20, seed=3, start=datetime(2026, 2, 2, 14, 30, tzinfo=UTC))   # Börsenzeit New York, Winter
        cls.einst = mit_standard({"risiko": {"standard_markt": "us"}})
        cls.wf = walk_forward("ausbruch", cls.kerzen, cls.einst, "TEST", "us")

    def test_zeitlich_getrennt(self):
        wf = self.wf
        training, test = wf["training"], wf["test"]
        self.assertEqual(training.von, self.kerzen[0].t)
        self.assertGreaterEqual(test.von, training.bis)
        self.assertEqual(test.von.hour * 60 + test.von.minute, 14 * 60 + 30)       # Teilung an einem Tagesanfang
        self.assertTrue(all(h.ausstieg_zeit < test.von for h in training.trades))
        self.assertTrue(all(h.einstieg_zeit >= test.von for h in test.trades))
        anteil = sum(1 for k in self.kerzen if k.t < test.von) / len(self.kerzen)
        self.assertAlmostEqual(anteil, 0.7, delta=0.05)

    def test_parameter_stammen_nur_aus_dem_training(self):
        wf = self.wf
        split = next(i for i, k in enumerate(self.kerzen) if k.t == wf["test"].von)
        from kitrader.strategie import Eroeffnungsausbruch, strategie_holen
        beste, bestes = {}, None
        for rk in Eroeffnungsausbruch.gitter["range_kerzen"]:
            for crv in Eroeffnungsausbruch.gitter["crv"]:
                k = backtest(strategie_holen("ausbruch", self.einst, range_kerzen=rk, crv=crv),
                             self.kerzen[:split], self.einst, "TEST", "us").kennzahlen()
                if k["trades"] >= 5 and (bestes is None or k["netto"] > bestes):
                    beste, bestes = {"range_kerzen": rk, "crv": crv}, k["netto"]
        self.assertEqual(wf["parameter"], beste)
        nur_test = backtest(strategie_holen("ausbruch", self.einst, **beste), self.kerzen[split:], self.einst, "TEST", "us")
        self.assertAlmostEqual(wf["test"].kennzahlen()["netto"], nur_test.kennzahlen()["netto"])
        self.assertEqual(len(wf["test"].trades), len(nur_test.trades))

    def test_ausgabe(self):
        text = tabelle_walk_forward(self.wf)
        self.assertIn("TEST, zurückgehalten", text)
        self.assertIn("Training", text)
        self.assertIn("Kein Erfolgsversprechen", text)
        d = als_dict(self.wf)
        self.assertIn("kennzahlen", d["test"])
        json.dumps(d, default=str)

    def test_mehrere_faltungen(self):
        e = mit_standard({"risiko": {"standard_markt": "us"}, "backtest": {"faltungen": 2}})
        wf = walk_forward("ausbruch", self.kerzen, e, "TEST", "us")
        self.assertEqual(len(wf["faelle"]), 2)
        self.assertGreaterEqual(wf["test"].von, wf["training"].bis)

    def test_zu_wenig_daten(self):
        with self.assertRaises(ValueError):
            walk_forward("ausbruch", zufallskerzen(tage=1), self.einst, "TEST", "us")


if __name__ == "__main__":
    unittest.main()
