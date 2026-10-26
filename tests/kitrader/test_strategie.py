"""Strategien und Umwandlung Signal -> Order. Erfundene Kerzen."""
import unittest
from datetime import datetime, timedelta, timezone

from kitrader.broker.basis import Kerze, Kurs
from kitrader.einstellungen import mit_standard
from kitrader.risiko import Lage
from kitrader.strategie import Eroeffnungsausbruch, Signal, VwapRueckkehr, signal_zu_order, strategie_holen

UTC = timezone.utc
T0 = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)


def k(i, o, h, l, c, v=1000):
    return Kerze(T0 + timedelta(minutes=5 * i), o, h, l, c, v)


def lage(positionen=None):
    return Lage(jetzt=T0 + timedelta(hours=3), gesamtwert=10000.0, einstellungen=mit_standard({}), symbol="ABC",
                positionen=positionen or {}, kurs=Kurs("ABC", 100.9, 101.0, 100.95, T0))


def spanne():
    """Sechs Kerzen zwischen 99 und 101."""
    return [k(i, 100, 101, 99, 100) for i in range(6)]


class AusbruchTest(unittest.TestCase):
    def setUp(self):
        self.s = Eroeffnungsausbruch()

    def test_kein_signal_waehrend_der_spanne(self):
        self.assertIsNone(self.s.bewerten(spanne()[:5], lage()))
        self.assertIsNone(self.s.bewerten(spanne(), lage()))

    def test_long_ausbruch(self):
        sig = self.s.bewerten(spanne() + [k(6, 100.5, 101.8, 100.4, 101.5)], lage())
        self.assertEqual(sig.richtung, "long")
        self.assertEqual((sig.einstieg, sig.stop), (101.5, 99))
        self.assertAlmostEqual(sig.ziel, 101.5 + 2.0 * 2.5)            # crv 2
        self.assertTrue(0 <= sig.sicherheit <= 1)
        self.assertIn("Ausbruch", sig.begruendung)

    def test_short_ausbruch(self):
        sig = self.s.bewerten(spanne() + [k(6, 99.5, 99.6, 98.2, 98.5)], lage())
        self.assertEqual(sig.richtung, "short")
        self.assertEqual(sig.stop, 101)
        self.assertAlmostEqual(sig.ziel, 98.5 - 2.0 * 2.5)

    def test_kein_signal_innerhalb_der_spanne(self):
        self.assertIsNone(self.s.bewerten(spanne() + [k(6, 100, 100.8, 99.2, 100.5)], lage()))

    def test_nur_der_erste_ausbruch_des_tages(self):
        kerzen = spanne() + [k(6, 100.5, 101.8, 100.4, 101.5), k(7, 101.5, 102.5, 101.4, 102.0)]
        self.assertIsNone(self.s.bewerten(kerzen, lage()))

    def test_keine_neue_position_wenn_schon_eine_offen_ist(self):
        self.assertIsNone(self.s.bewerten(spanne() + [k(6, 100.5, 101.8, 100.4, 101.5)], lage({"ABC": 5})))

    def test_neuer_tag_beginnt_neu(self):
        gestern = spanne() + [k(6, 100.5, 101.8, 100.4, 101.5)]
        morgen = [Kerze(x.t + timedelta(days=1), x.o, x.h, x.l, x.c, x.v) for x in spanne()]
        self.assertIsNone(self.s.bewerten(gestern + morgen, lage()))

    def test_zu_schmale_spanne(self):
        eng = [k(i, 100, 100.05, 99.95, 100) for i in range(6)]
        self.assertIsNone(self.s.bewerten(eng + [k(6, 100, 100.3, 100, 100.2)], lage()))


class VwapTest(unittest.TestCase):
    def kerzen(self, ende):
        flach = [k(i, 100, 100.25, 99.75, 100) for i in range(20)]
        return flach + ende

    def test_long_rueckkehr(self):
        fall = [k(20, 100, 100.0, 98.0, 98.2), k(21, 98.2, 98.3, 96.5, 96.6), k(22, 96.6, 96.7, 95.0, 95.2),
                k(23, 95.2, 95.8, 95.0, 95.7)]                       # letzte Kerze dreht nach oben
        kerzen = self.kerzen(fall)
        sig = VwapRueckkehr().bewerten(kerzen, lage())
        self.assertIsNotNone(sig)
        self.assertEqual(sig.richtung, "long")
        tag = kerzen
        vwap = sum((x.h + x.l + x.c) / 3 * x.v for x in tag) / sum(x.v for x in tag)
        self.assertAlmostEqual(sig.ziel, vwap)
        self.assertLess(sig.stop, sig.einstieg)
        self.assertLess(sig.einstieg, sig.ziel)

    def test_short_rueckkehr_spiegelbildlich(self):
        steig = [k(20, 100, 102.0, 100.0, 101.8), k(21, 101.8, 103.5, 101.7, 103.4), k(22, 103.4, 105.0, 103.3, 104.8),
                 k(23, 104.8, 105.0, 104.2, 104.3)]                  # letzte Kerze dreht nach unten
        sig = VwapRueckkehr().bewerten(self.kerzen(steig), lage())
        self.assertEqual(sig.richtung, "short")
        self.assertGreater(sig.stop, sig.einstieg)
        self.assertGreater(sig.einstieg, sig.ziel)

    def test_kein_signal_ohne_umkehrkerze(self):
        fall = [k(20, 100, 100.0, 98.0, 98.2), k(21, 98.2, 98.3, 96.5, 96.6), k(22, 96.6, 96.7, 95.0, 95.2),
                k(23, 95.2, 95.3, 94.0, 94.2)]                        # weiter fallend
        self.assertIsNone(VwapRueckkehr().bewerten(self.kerzen(fall), lage()))

    def test_kein_signal_in_ruhiger_lage_oder_mit_zu_wenig_kerzen(self):
        self.assertIsNone(VwapRueckkehr().bewerten(self.kerzen([k(20, 100, 100.25, 99.75, 100.1)]), lage()))
        self.assertIsNone(VwapRueckkehr().bewerten([k(i, 100, 101, 99, 100) for i in range(5)], lage()))


class SignalOrderTest(unittest.TestCase):
    def test_flat_gibt_keine_order(self):
        self.assertIsNone(signal_zu_order(Signal("flat"), "ABC", lage()))

    def test_long_order_aus_risiko(self):
        # Ask 101, Stop 99 -> Abstand 2, Risiko 0,5 % von 10.000 = 50 -> 25 Stück; Deckel 10 % = 1000/101 -> 9
        o = signal_zu_order(Signal("long", 101.5, 99.0, 106.0, 0.6, "Test"), "ABC", lage())
        self.assertEqual((o.seite, o.art, o.menge), ("kauf", "markt", 9))
        self.assertEqual((o.stop_loss, o.take_profit, o.notiz), (99.0, 106.0, "Test"))

    def test_short_order_rechnet_mit_bid(self):
        o = signal_zu_order(Signal("short", 100.0, 102.0, 95.0), "ABC", lage())
        self.assertEqual(o.seite, "verkauf")
        self.assertEqual(o.menge, 9)                                   # 1000 / 100,9 = 9,9 -> 9

    def test_weiter_stop_fuehrt_zu_kleiner_menge(self):
        o = signal_zu_order(Signal("long", 101.0, 51.0, None), "ABC", lage())    # Abstand 50 -> 1 Stück
        self.assertEqual(o.menge, 1)

    def test_menge_null_gibt_keine_order(self):
        self.assertIsNone(signal_zu_order(Signal("long", 101.0, 0.5, None), "ABC", lage()))   # Abstand 100,5 -> 0,49
        self.assertIsNone(signal_zu_order(Signal("long", 101.0, None, None), "ABC", lage()))   # ohne Stop

    def test_menge_rechnet_kurse_in_kontowaehrung_um(self):
        # Ask 101 USD, Stop 99 USD, 1 USD = 2 EUR: Risiko 50 EUR / (2 x 2) = 12; Deckel 1000 EUR / 202 = 4
        l = lage()
        l.wechselkurs, l.konto_waehrung = 2.0, "EUR"
        o = signal_zu_order(Signal("long", 101.5, 99.0, 106.0), "ABC", l)
        self.assertEqual(o.menge, 4)
        l.einstellungen = mit_standard({"risiko": {"position_max_prozent": 100}})
        self.assertEqual(signal_zu_order(Signal("long", 101.5, 99.0, 106.0), "ABC", l).menge, 12)

    def test_ohne_wechselkurs_gibt_es_keine_order(self):
        l = lage()
        l.wechselkurs = None
        self.assertIsNone(signal_zu_order(Signal("long", 101.5, 99.0, 106.0), "ABC", l))

    def test_mengenschritt_je_symbol(self):
        l = lage()
        l.einstellungen = mit_standard({"mengenschritte": {"ABC": 0.01}})
        o = signal_zu_order(Signal("long", 101.0, 51.5, None), "ABC", l)   # Risiko 50 / Abstand 49,5 = 1,0101
        self.assertAlmostEqual(o.menge, 1.01)


class FabrikTest(unittest.TestCase):
    def test_strategie_holen(self):
        s = strategie_holen("ausbruch", mit_standard({"strategie": {"ausbruch": {"crv": 3.0}}}))
        self.assertEqual(s.p["crv"], 3.0)
        self.assertEqual(strategie_holen("vwap").name, "vwap")
        self.assertEqual(strategie_holen("ausbruch", crv=1.5).p["crv"], 1.5)

    def test_fehler(self):
        with self.assertRaises(ValueError):
            strategie_holen("gibtsnicht")
        with self.assertRaises(ValueError):
            strategie_holen("ausbruch", unsinn=1)
        with self.assertRaises(ValueError):
            Signal("quer")
        with self.assertRaises(ValueError):
            Signal("long", sicherheit=1.5)


if __name__ == "__main__":
    unittest.main()
