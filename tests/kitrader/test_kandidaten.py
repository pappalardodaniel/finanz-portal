"""Signal-Logik der Kandidaten-Strategien (strategie/kandidaten.py). Erfundene Kerzen, kein Netz."""
import itertools
import unittest
from datetime import datetime, timedelta, timezone

from kitrader.broker.basis import Kerze, Kurs
from kitrader.einstellungen import mit_standard
from kitrader.risiko import Lage
from kitrader.strategie import (STRATEGIEN, BollingerRsi, Donchian, MaTrend, Momentum, VolumenAusbruch, signal_zu_order,
                                strategie_holen)
from kitrader.strategie.beispiele import _atr
from kitrader.strategie.kandidaten import KANDIDATEN, rsi

from .daten import zufallskerzen

UTC = timezone.utc
T0 = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)


def k(i, o, h, l, c, v=1000):
    return Kerze(T0 + timedelta(minutes=5 * i), o, h, l, c, v)


def lage(positionen=None):
    return Lage(jetzt=T0 + timedelta(hours=30), gesamtwert=10000.0, einstellungen=mit_standard({}), symbol="X",
                positionen=positionen or {}, kurs=Kurs("X", 99.0, 101.0, 100.0, T0))


def flach(n, preis=100.0, v=1000):
    return [k(i, preis, preis + 1, preis - 1, preis, v) for i in range(n)]


def aus_schluessen(schluesse, v=1000):
    return [k(i, c, c + 0.5, c - 0.5, c, v) for i, c in enumerate(schluesse)]


class RsiTest(unittest.TestCase):
    def test_nur_gewinne_und_nur_verluste(self):
        self.assertEqual(rsi([float(i) for i in range(20)]), 100.0)
        self.assertAlmostEqual(rsi([float(20 - i) for i in range(20)]), 0.0)

    def test_abwechselnd_ist_50(self):
        # 14 Änderungen: 7 mal +1, 7 mal -1 -> Schnitt Gewinn = Schnitt Verlust = 0,5 -> RSI genau 50
        self.assertAlmostEqual(rsi([10.0, 11.0] * 7 + [10.0]), 50.0)

    def test_zu_wenig_werte_und_stillstand(self):
        self.assertIsNone(rsi([1.0] * 14, 14))
        self.assertEqual(rsi([5.0] * 20), 50.0)


class DonchianTest(unittest.TestCase):
    def setUp(self):
        self.s = Donchian()                                   # kanal 20, stop 2 ATR, crv 2

    def test_long_ausbruch_mit_atr_stop_und_ziel(self):
        kerzen = flach(25) + [k(25, 100, 102.5, 100, 102)]
        sig = self.s.bewerten(kerzen, lage())
        self.assertEqual(sig.richtung, "long")
        atr = _atr(kerzen, 14)
        self.assertEqual(sig.einstieg, 102)
        self.assertAlmostEqual(sig.stop, 102 - 2.0 * atr)
        self.assertAlmostEqual(sig.ziel, 102 + 2.0 * (2.0 * atr))      # crv 2: Ziel = Einstieg + 2 mal Risiko
        self.assertIn("20-Kerzen-Hoch 101", sig.begruendung)

    def test_short_ausbruch(self):
        sig = self.s.bewerten(flach(25) + [k(25, 100, 100.5, 97, 98)], lage())
        self.assertEqual(sig.richtung, "short")
        self.assertGreater(sig.stop, sig.einstieg)
        self.assertLess(sig.ziel, sig.einstieg)

    def test_kein_signal_im_kanal_oder_mit_position_oder_zu_wenig_kerzen(self):
        self.assertIsNone(self.s.bewerten(flach(25) + [k(25, 100, 100.9, 99.1, 100.5)], lage()))
        self.assertIsNone(self.s.bewerten(flach(25) + [k(25, 100, 102.5, 100, 102)], lage({"X": 1.0})))
        self.assertIsNone(self.s.bewerten(flach(10) + [k(10, 100, 102.5, 100, 102)], lage()))

    def test_der_ausbruch_zaehlt_nur_ueber_dem_hoch_der_vorherigen_kerzen(self):
        # die Ausbruchskerze selbst gehört nicht zum Kanal: Schluss gleich dem alten Hoch ist noch kein Ausbruch
        self.assertIsNone(self.s.bewerten(flach(25) + [k(25, 100, 103, 100, 101)], lage()))

    def test_parameter_aendern_das_ergebnis(self):
        kerzen = flach(25) + [k(25, 100, 102.5, 100, 102)]
        eng = Donchian(stop_atr=1.0, crv=1.0).bewerten(kerzen, lage())
        weit = Donchian(stop_atr=3.0, crv=1.0).bewerten(kerzen, lage())
        self.assertGreater(eng.stop, weit.stop)


class BollingerRsiTest(unittest.TestCase):
    def setUp(self):
        self.s = BollingerRsi()
        self.ruhig = [k(i, 100, 100.4, 99.6, 100.2 if i % 2 else 99.8) for i in range(60)]

    def test_long_unter_dem_unteren_band_mit_ziel_mitte(self):
        fall = [k(60 + j, 100 - j, 100 - j + 0.2, 100 - j - 1.2, 99 - j) for j in range(6)]       # Schluss 99 ... 94
        kerzen = self.ruhig + fall
        sig = self.s.bewerten(kerzen, lage())
        self.assertEqual(sig.richtung, "long")
        mitte = sum(x.c for x in kerzen[-20:]) / 20
        self.assertAlmostEqual(sig.ziel, mitte)                       # Ziel = Mittelwert der letzten 20 Schlusskurse
        self.assertLess(sig.stop, sig.einstieg)
        self.assertGreater(sig.ziel, sig.einstieg)
        self.assertIn("RSI", sig.begruendung)

    def test_short_spiegelbildlich(self):
        auf = [k(60 + j, 100 + j, 100 + j + 1.2, 100 + j - 0.2, 101 + j) for j in range(6)]
        sig = self.s.bewerten(self.ruhig + auf, lage())
        self.assertEqual(sig.richtung, "short")
        self.assertLess(sig.ziel, sig.einstieg)

    def test_kein_signal_ohne_bandbruch_oder_mit_position(self):
        sanft = [k(60 + j, 100, 100.4, 99.6, 100 - 0.05 * j) for j in range(6)]
        self.assertIsNone(self.s.bewerten(self.ruhig + sanft, lage()))
        fall = [k(60 + j, 100 - j, 100 - j + 0.2, 100 - j - 1.2, 99 - j) for j in range(6)]
        self.assertIsNone(self.s.bewerten(self.ruhig + fall, lage({"X": 1.0})))

    def test_strenger_rsi_filter_sperrt_das_signal(self):
        fall = [k(60 + j, 100 - j, 100 - j + 0.2, 100 - j - 1.2, 99 - j) for j in range(6)]          # RSI etwa 21
        self.assertIsNone(BollingerRsi(rsi_max=10.0).bewerten(self.ruhig + fall, lage()))

    def test_zu_wenig_kerzen(self):
        self.assertIsNone(self.s.bewerten(self.ruhig[:30], lage()))

    def test_ohne_schwankung_kein_signal(self):
        self.assertIsNone(self.s.bewerten([k(i, 100, 100, 100, 100) for i in range(70)], lage()))


class MaTrendTest(unittest.TestCase):
    def setUp(self):
        self.s = MaTrend(schnell=3, langsam=6, filter=10, stop_atr=2.0, crv=3.0)

    def test_kreuzung_nach_oben_ueber_dem_trendfilter_ist_long(self):
        sig = self.s.bewerten(aus_schluessen([100] * 16 + [102]), lage())
        self.assertEqual(sig.richtung, "long")
        self.assertIn("3/6", sig.begruendung)
        self.assertAlmostEqual((sig.ziel - sig.einstieg), 3.0 * (sig.einstieg - sig.stop))

    def test_trendfilter_blockiert_kreuzung_gegen_den_trend(self):
        # Kurs zuvor von 100 auf 90 gefallen, kleine Gegenbewegung auf 91 kreuzt zwar, liegt aber unter dem Trendfilter
        self.assertIsNone(self.s.bewerten(aus_schluessen([100] * 10 + [90] * 6 + [91]), lage()))

    def test_kreuzung_nach_unten_unter_dem_trendfilter_ist_short(self):
        sig = self.s.bewerten(aus_schluessen([100] * 16 + [98]), lage())
        self.assertEqual(sig.richtung, "short")

    def test_keine_kreuzung_kein_signal(self):
        self.assertIsNone(self.s.bewerten(aus_schluessen([100] * 17), lage()))
        self.assertIsNone(self.s.bewerten(aus_schluessen([100 + i for i in range(17)]), lage()))   # Trend, aber keine frische Kreuzung

    def test_gegenkreuzung_beendet_die_long_position_mit_flat(self):
        kerzen = aus_schluessen([100] * 14 + [100, 100, 100, 100, 99, 98])
        sig = self.s.bewerten(kerzen, lage({"X": 1.0}))
        self.assertEqual(sig.richtung, "flat")
        self.assertIsNone(self.s.bewerten(kerzen, lage({"X": -1.0})))        # ein Short bleibt bei fallenden Kursen offen
        self.assertEqual(self.s.bewerten(aus_schluessen([100] * 14 + [100, 100, 100, 100, 101, 102]),
                                         lage({"X": -1.0})).richtung, "flat")
        self.assertIsNone(self.s.bewerten(aus_schluessen([100] * 14 + [100, 100, 100, 100, 101, 102]), lage({"X": 1.0})))

    def test_schnell_muss_kleiner_als_langsam_sein(self):
        self.assertIsNone(MaTrend(schnell=8, langsam=8, filter=10).bewerten(aus_schluessen([100] * 16 + [102]), lage()))

    def test_zu_wenig_kerzen(self):
        self.assertIsNone(self.s.bewerten(aus_schluessen([100] * 8), lage()))


class MomentumTest(unittest.TestCase):
    def setUp(self):
        self.kerzen = [k(i, 100 + i - 0.8, 100 + i + 0.2, 100 + i - 1, 100 + i) for i in range(30)]      # steigt um 1 je Kerze

    def test_starker_anstieg_ueber_n_kerzen_ist_long(self):
        sig = Momentum(n=5, schwelle_atr=3.0).bewerten(self.kerzen, lage())
        atr = _atr(self.kerzen, 14)
        self.assertEqual(sig.richtung, "long")
        self.assertAlmostEqual(sig.stop, 129 - 2.0 * atr)
        self.assertIn(f"+{5 / atr:.1f} ATR in 5 Kerzen", sig.begruendung)

    def test_zu_schwacher_anstieg_oder_position_kein_signal(self):
        self.assertIsNone(Momentum(n=5, schwelle_atr=30.0).bewerten(self.kerzen, lage()))
        self.assertIsNone(Momentum(n=5, schwelle_atr=3.0).bewerten(self.kerzen, lage({"X": 1.0})))

    def test_fall_ist_short(self):
        fallend = [k(i, 130 - i + 0.8, 130 - i + 1, 130 - i - 0.2, 130 - i) for i in range(30)]
        sig = Momentum(n=5, schwelle_atr=3.0).bewerten(fallend, lage())
        self.assertEqual(sig.richtung, "short")

    def test_letzte_kerze_muss_in_die_richtung_schliessen(self):
        kerzen = self.kerzen[:-1] + [k(29, 129.5, 129.8, 127.0, 128.0)]       # Kurs insgesamt hoch, letzte Kerze aber rot
        self.assertIsNone(Momentum(n=5, schwelle_atr=1.0).bewerten(kerzen, lage()))


class VolumenAusbruchTest(unittest.TestCase):
    def setUp(self):
        self.s = VolumenAusbruch()                           # Kanal 20, Faktor 2,5

    def test_ausbruch_mit_hohem_umsatz_ist_long(self):
        sig = self.s.bewerten(flach(25) + [k(25, 100, 102.5, 100, 102, 3000)], lage())
        self.assertEqual(sig.richtung, "long")
        self.assertIn("3.0-fachem Umsatz", sig.begruendung)

    def test_ausbruch_ohne_umsatz_kein_signal(self):
        self.assertIsNone(self.s.bewerten(flach(25) + [k(25, 100, 102.5, 100, 102, 1500)], lage()))
        self.assertIsNone(self.s.bewerten(flach(25, v=0) + [k(25, 100, 102.5, 100, 102, 3000)], lage()))

    def test_umsatz_ohne_ausbruch_kein_signal(self):
        self.assertIsNone(self.s.bewerten(flach(25) + [k(25, 100, 100.9, 99.1, 100.5, 5000)], lage()))

    def test_short_und_position(self):
        sig = self.s.bewerten(flach(25) + [k(25, 100, 100.5, 97, 98, 4000)], lage())
        self.assertEqual(sig.richtung, "short")
        self.assertIsNone(self.s.bewerten(flach(25) + [k(25, 100, 102.5, 100, 102, 3000)], lage({"X": 1.0})))


class KandidatenInsgesamtTest(unittest.TestCase):
    def test_alle_registriert_und_als_kandidat_gekennzeichnet(self):
        namen = {klasse.name for klasse in KANDIDATEN}
        self.assertEqual(namen, {"donchian", "bollinger_rsi", "ma_trend", "momentum", "volumen"})
        for klasse in KANDIDATEN:
            self.assertIs(STRATEGIEN[klasse.name], klasse)
            self.assertIn("KANDIDAT", klasse.beschreibung)
            self.assertIn("kein Erfolgsversprechen", klasse.beschreibung)

    def test_gitter_nutzt_nur_bekannte_parameter_und_laesst_sich_bauen(self):
        for klasse in KANDIDATEN:
            self.assertTrue(set(klasse.gitter) <= set(klasse.parameter), klasse.name)
            for werte in itertools.product(*klasse.gitter.values()):
                klasse(**dict(zip(klasse.gitter, werte)))
            strategie_holen(klasse.name)

    def test_unbekannter_parameter_wird_abgelehnt(self):
        with self.assertRaises(ValueError):
            Donchian(quatsch=1)

    def test_signale_sind_auf_zufallskursen_immer_in_sich_stimmig(self):
        """Eigenschaft: Stop liegt auf der Verlustseite, Ziel auf der Gewinnseite, Sicherheit in 0..1, nie ein Absturz."""
        kerzen = zufallskerzen(tage=6, seed=11, takt_min=5)
        l = lage()
        gesehen = 0
        for klasse in KANDIDATEN:
            for werte in itertools.product(*klasse.gitter.values()):
                s = klasse(**dict(zip(klasse.gitter, werte)))
                for i in range(60, len(kerzen)):
                    sig = s.bewerten(kerzen[max(0, i - 499):i + 1], l)
                    if sig is None or sig.richtung == "flat":
                        continue
                    gesehen += 1
                    self.assertTrue(0 <= sig.sicherheit <= 1, (klasse.name, sig))
                    if sig.richtung == "long":
                        self.assertLess(sig.stop, sig.einstieg, (klasse.name, sig))
                        self.assertGreater(sig.ziel, sig.einstieg, (klasse.name, sig))
                    else:
                        self.assertGreater(sig.stop, sig.einstieg, (klasse.name, sig))
                        self.assertLess(sig.ziel, sig.einstieg, (klasse.name, sig))
        self.assertGreater(gesehen, 50)                           # die Prüfung hat wirklich Signale gesehen

    def test_signal_wird_zu_order_mit_risikogrenze(self):
        sig = Donchian().bewerten(flach(25) + [k(25, 100, 102.5, 100, 102)], lage())
        order = signal_zu_order(sig, "X", lage())
        self.assertEqual(order.seite, "kauf")
        self.assertEqual(order.stop_loss, sig.stop)
        self.assertGreater(order.menge, 0)


if __name__ == "__main__":
    unittest.main()
