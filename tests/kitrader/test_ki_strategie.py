"""Strategie „ki“ (strategie/ki_strategie.py): Signal aus der KI-Einschätzung, Schwellen, Kosten, Zwischenspeicher,
Aufrufgrenzen und Ablauf im Autopiloten. Alles offline mit Fake-Treiber, keine echte KI."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kitrader import ki
from kitrader.autopilot import Autopilot
from kitrader.backtest import backtest
from kitrader.broker.basis import Kerze
from kitrader.broker.papier import PapierBroker
from kitrader.einstellungen import mit_standard
from kitrader.journal import Journal
from kitrader.kursquelle import WiedergabeQuelle
from kitrader.risiko import Lage
from kitrader.strategie import STRATEGIEN, KiStrategie, strategie_holen
from kitrader.strategie.beispiele import _atr
from kitrader.strategie.ki_strategie import KiStrategieFehler, kosten_prozent, verdichten

from .daten import MONTAG, Uhr, zufallskerzen
from .test_ki import FakeHistorie, FakeTreiber, GUT, osiris_lage

UTC = timezone.utc


def antwort(kurz=("steigt", 0.7), mittel=("seitwaerts", 0.5), lang=("faellt", 0.4)):
    return {n: {**GUT[n], "richtung": r, "wahrscheinlichkeit": w} for n, (r, w) in
            (("kurz", kurz), ("mittel", mittel), ("lang", lang))}


def kerzen5(n=300):
    """5-Minuten-Kerzen rund um die Uhr (Krypto), erfunden; reichen für die Stunden-ATR."""
    return zufallskerzen(tage=2, seed=7, takt_min=5, pro_tag=288)[:n]


def tageskerzen(n=60):
    return [Kerze(MONTAG.replace(hour=0) + timedelta(days=i), 100 + i, 103 + i, 98 + i, 101 + i, 1000) for i in range(n)]


class Basis(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ordner = Path(self._d.name)
        self.einst = mit_standard({"osiris": {"automatisch": False}, "ki": {"treiber": "claude_cli"}, "maerkte": {"XBT": "krypto"},
                                   "risiko": {"short_erlaubt": True}})
        self.journal = Journal(":memory:", self.einst, Uhr(MONTAG))
        self.addCleanup(self.journal.beenden)
        self.kerzen = kerzen5()

    def strategie(self, *antworten, binden=True, einst=None, **parameter):
        self.treiber = FakeTreiber(*(antworten or (antwort(),)))
        s = strategie_holen("ki", self.einst, treiber=self.treiber, osiris_lage=osiris_lage(), historie=FakeHistorie(),
                            **parameter)
        if binden:
            s.anbinden(self.journal, self.ordner, "papier", einst or self.einst)
        return s

    def lage(self, minuten=0, kerzen=None, positionen=None, einst=None, gesamtwert=10000.0):
        kerzen = kerzen or self.kerzen
        ende = kerzen[-1].t + timedelta(minutes=5)
        return Lage(jetzt=ende + timedelta(minutes=minuten), gesamtwert=gesamtwert, einstellungen=einst or self.einst,
                    symbol="XBT", positionen=positionen or {})

    def bewerte(self, s, minuten=0, **kw):
        return s.bewerten(kw.pop("kerzen", None) or self.kerzen, self.lage(minuten, **kw))

    def notizen(self):
        return [e["notiz"] for e in self.journal.entscheidungen(50)]


class SignalTest(Basis):
    def test_steigt_wird_long_mit_atr_stop_und_ziel_und_begruendung(self):
        s = self.strategie(antwort(kurz=("steigt", 0.7)))
        sig = self.bewerte(s)
        self.assertEqual(sig.richtung, "long")
        c = self.kerzen[-1].c
        atr = _atr(verdichten(self.kerzen, timedelta(hours=1)), 14)           # Stunden-ATR: die KI sieht Stundenkerzen
        self.assertEqual(sig.einstieg, c)
        self.assertAlmostEqual(c - sig.stop, 1.5 * atr)
        self.assertAlmostEqual(sig.ziel - c, 3.0 * atr)
        self.assertEqual(sig.sicherheit, 0.7)
        self.assertIn("Grund kurz", sig.begruendung)                          # Begründung der KI steht im Signal
        self.assertIn("steigt 0.70", sig.begruendung)

    def test_faellt_wird_short_wenn_erlaubt(self):
        s = self.strategie(antwort(kurz=("faellt", 0.75)))
        sig = self.bewerte(s)
        c = self.kerzen[-1].c
        self.assertEqual(sig.richtung, "short")
        self.assertGreater(sig.stop, c)
        self.assertLess(sig.ziel, c)

    def test_short_verboten_dann_kein_signal_und_vermerk(self):
        e = mit_standard({**self.einst, "risiko": {"short_erlaubt": False}})
        s = self.strategie(antwort(kurz=("faellt", 0.9)), einst=e)
        self.assertIsNone(s.bewerten(self.kerzen, self.lage(einst=e)))
        self.assertIn("Short ist verboten", self.notizen()[-1])
        self.assertIn("Short ist verboten", s.letzte["XBT"])

    def test_seitwaerts_gibt_kein_signal(self):
        s = self.strategie(antwort(kurz=("seitwaerts", 0.9)))
        self.assertIsNone(self.bewerte(s))
        self.assertIn("keine Richtung", self.notizen()[-1])

    def test_mindestwahrscheinlichkeit_genau_an_der_grenze(self):
        for w, erwartet in ((0.59, False), (0.6, True), (0.95, True)):
            s = self.strategie(antwort(kurz=("steigt", w)))
            self.assertEqual(self.bewerte(s) is not None, erwartet, w)
        self.assertIn("unter Mindestwahrscheinlichkeit 0.60", next(n for n in self.notizen() if "0.59" in n))
        s = self.strategie(antwort(kurz=("steigt", 0.7)), min_wahrscheinlichkeit=0.8)
        self.assertIsNone(self.bewerte(s))

    def test_mittel_nutzt_die_mittlere_einschaetzung(self):
        a = antwort(kurz=("steigt", 0.9), mittel=("faellt", 0.8))
        s = self.strategie(a, horizont="mittel")
        self.assertEqual(self.bewerte(s, kerzen=tageskerzen()).richtung, "short")
        s = self.strategie(a, horizont="kurz")
        self.assertEqual(self.bewerte(s).richtung, "long")

    def test_signal_geht_durch_signal_zu_order_und_risikopruefung(self):
        from kitrader.risiko import pruefen
        from kitrader.strategie import signal_zu_order
        from kitrader.broker.basis import Kurs
        s = self.strategie(antwort(kurz=("steigt", 0.8)))
        lage = self.lage()
        sig = s.bewerten(self.kerzen, lage)
        lage.kurs = Kurs("XBT", sig.einstieg * 0.9999, sig.einstieg * 1.0001, sig.einstieg, lage.jetzt)
        lage.datenordner = None
        order = signal_zu_order(sig, "XBT", lage)
        self.assertEqual((order.seite, order.stop_loss, order.take_profit), ("kauf", sig.stop, sig.ziel))
        self.assertTrue(pruefen(order, lage).ok)


class KostenTest(Basis):
    def test_kosten_aus_den_einstellungen_von_hand_gerechnet(self):
        e = mit_standard({"kosten": {"gebuehr_fest": 1.0, "gebuehr_prozent": 0.05, "schlupf_prozent": 0.02, "spread_prozent": 0.05}})
        # Stop 0,5 %: Risiko 0,5 % von 10.000 = 50 -> Wert 10.000, gedeckelt auf 10 % = 1.000 -> feste Gebühr 2/1000 = 0,2 %
        self.assertAlmostEqual(kosten_prozent(e, 10000.0, 0.5), 0.10 + 0.04 + 0.05 + 0.2)
        # Stop 10 %: Risikowert 500 -> nicht gedeckelt -> feste Gebühr 2/500 = 0,4 %
        self.assertAlmostEqual(kosten_prozent(e, 10000.0, 10.0), 0.10 + 0.04 + 0.05 + 0.4)
        self.assertEqual(kosten_prozent(e, 0.0, 1.0), float("inf"))

    def test_weg_muss_die_kosten_deutlich_uebersteigen(self):
        hohe = mit_standard({**self.einst, "kosten": {"gebuehr_prozent": 0.8}})      # Kraken Pro, niedrigste Stufe: Taker 0,8 %
        s = self.strategie(antwort(kurz=("steigt", 0.9)), einst=hohe)
        self.assertIsNone(s.bewerten(self.kerzen, self.lage(einst=hohe)))
        self.assertIn("Weg zu klein gegen die Kosten", self.notizen()[-1])
        ohne = mit_standard({**self.einst, "kosten": {"gebuehr_fest": 0, "gebuehr_prozent": 0, "schlupf_prozent": 0, "spread_prozent": 0}})
        s = self.strategie(antwort(kurz=("steigt", 0.9)), einst=ohne)
        self.assertIsNotNone(s.bewerten(self.kerzen, self.lage(einst=ohne)))

    def test_kostenfaktor_ist_einstellbar(self):
        atr = _atr(verdichten(self.kerzen, timedelta(hours=1)), 14)
        weg = 3.0 * atr / self.kerzen[-1].c * 100
        kosten = kosten_prozent(self.einst, 10000.0, 1.5 * atr / self.kerzen[-1].c * 100)
        faktor = weg / kosten
        self.assertGreater(faktor, 3.0)                                          # Standardkosten: das Signal besteht
        s = self.strategie(antwort(kurz=("steigt", 0.9)), kosten_faktor=faktor * 1.01)
        self.assertIsNone(self.bewerte(s))
        s = self.strategie(antwort(kurz=("steigt", 0.9)), kosten_faktor=faktor * 0.99)
        self.assertIsNotNone(self.bewerte(s))

    def test_negative_erwartung_nach_kosten_gibt_kein_signal(self):
        # Ziel nah, Stop weit, Wahrscheinlichkeit 0,6: 0,6 * 1 - 0,4 * 6 < 0 trotz kleiner Kosten
        s = self.strategie(antwort(kurz=("steigt", 0.6)), ziel_atr=1.0, stop_atr=6.0, kosten_faktor=0.0)
        self.assertIsNone(self.bewerte(s))
        self.assertIn("Erwartung nach Kosten nicht positiv", self.notizen()[-1])


class KiFehlerTest(Basis):
    def test_ki_nicht_erreichbar_dann_kein_signal_und_vermerk(self):
        s = self.strategie(ki.KiFehler("CLI weg"))
        self.assertIsNone(self.bewerte(s))
        self.assertIn("KI nicht verfügbar, kein Signal (CLI weg)", self.notizen()[-1])

    def test_ungueltige_antwort_auch_nach_nachfrage_dann_kein_signal(self):
        s = self.strategie("das ist kein JSON")
        self.assertIsNone(self.bewerte(s))
        self.assertEqual(len(self.treiber.prompts), 2)                           # ein Versuch plus eine Nachfrage
        self.assertIn("KI nicht verfügbar", self.notizen()[-1])

    def test_nach_einem_fehler_nicht_dauernd_nachfragen(self):
        s = self.strategie(ki.KiFehler("weg"), antwort())
        self.assertIsNone(self.bewerte(s, 0))
        self.assertIsNone(self.bewerte(s, 5))
        self.assertEqual(len(self.treiber.prompts), 1)                           # innerhalb von 10 Minuten nicht erneut
        self.assertIsNotNone(self.bewerte(s, 11))                                # danach neuer Versuch, jetzt klappt es
        self.assertEqual(len(self.treiber.prompts), 2)

    def test_ohne_treiber_aus_den_einstellungen_klare_meldung(self):
        e = mit_standard({**self.einst, "ki": {"treiber": "aus"}})
        s = strategie_holen("ki", e)
        with self.assertRaises(KiStrategieFehler) as f:
            s.anbinden(self.journal, self.ordner, "papier", e)
        self.assertIn("braucht einen KI-Treiber", str(f.exception))

    def test_backtest_mit_echter_ki_wird_verweigert(self):
        s = strategie_holen("ki", self.einst)                                    # ohne Fake-Treiber, nicht angebunden
        with self.assertRaises(KiStrategieFehler) as f:
            s.bewerten(self.kerzen, self.lage())
        self.assertIn("läuft nur im Autopiloten", str(f.exception))
        with self.assertRaises(ValueError):
            backtest(s, self.kerzen, self.einst, "XBT", "krypto")


class ZwischenspeicherTest(Basis):
    def test_innerhalb_von_30_minuten_wird_die_ki_nur_einmal_gefragt(self):
        s = self.strategie(antwort())
        self.bewerte(s, 0)
        for minuten in (5, 10, 29):
            self.bewerte(s, minuten)
        self.assertEqual(len(self.treiber.prompts), 1)
        self.bewerte(s, 30)
        self.assertEqual(len(self.treiber.prompts), 2)

    def test_tageskerzen_6_stunden(self):
        s = self.strategie(antwort())
        kerzen = tageskerzen()
        self.bewerte(s, 0, kerzen=kerzen)
        self.bewerte(s, 359, kerzen=kerzen)
        self.assertEqual(len(self.treiber.prompts), 1)
        self.bewerte(s, 360, kerzen=kerzen)
        self.assertEqual(len(self.treiber.prompts), 2)

    def test_abstand_ist_einstellbar_und_gilt_je_symbol(self):
        s = self.strategie(antwort(), abstand_min=120)
        self.bewerte(s, 0)
        self.bewerte(s, 100)
        self.assertEqual(len(self.treiber.prompts), 1)
        lage = self.lage(100)
        lage.symbol = "ETH"
        s.bewerten(self.kerzen, lage)                                            # anderes Symbol: eigener Zwischenspeicher
        self.assertEqual(len(self.treiber.prompts), 2)
        self.bewerte(s, 120)
        self.assertEqual(len(self.treiber.prompts), 3)

    def test_jede_einschaetzung_wird_nur_einmal_bewertet(self):
        s = self.strategie(antwort(kurz=("steigt", 0.8)))
        self.assertIsNotNone(self.bewerte(s, 0))
        self.assertIsNone(self.bewerte(s, 5))                                    # dieselbe Einschätzung: kein zweites Signal
        self.assertIsNone(self.bewerte(s, 10))
        self.assertIsNotNone(self.bewerte(s, 30))                                # neue Einschätzung: neues Signal

    def test_zu_wenige_kerzen_fragen_die_ki_gar_nicht(self):
        s = self.strategie(antwort())
        self.assertIsNone(s.bewerten(self.kerzen[:100], self.lage(kerzen=self.kerzen[:100])))   # keine 15 Stundenkerzen
        self.assertEqual(self.treiber.prompts, [])

    def test_einschaetzungen_landen_im_journal_mit_ki_marke(self):
        s = self.strategie(antwort())
        self.bewerte(s)
        z = self.journal.einschaetzungen()
        self.assertEqual({e["horizont"] for e in z}, {"1d", "3M", "1J"})
        self.assertTrue(all(e["begruendung"].startswith(ki.KI_MARKE) for e in z))


class KontextTest(Basis):
    def test_kurze_kerzen_werden_fuer_die_ki_zu_stundenkerzen(self):
        s = self.strategie(antwort())
        self.bewerte(s)
        self.assertIn("Takt 1h", self.treiber.prompts[0])
        s = self.strategie(antwort(), kontext_takt="")
        self.bewerte(s)
        self.assertIn("Takt 5m", self.treiber.prompts[0])

    def test_verdichten_ohlcv_und_ende_der_letzten_kerze(self):
        k = kerzen5(30)
        v = verdichten(k, timedelta(minutes=30))                                 # 6 Kerzen je Gruppe, 5 volle Gruppen
        self.assertEqual(len(v), 5)
        letzte = k[-6:]
        self.assertEqual((v[-1].o, v[-1].c, v[-1].h, v[-1].l), (letzte[0].o, letzte[-1].c, max(x.h for x in letzte), min(x.l for x in letzte)))
        self.assertAlmostEqual(v[-1].v, sum(x.v for x in letzte))
        self.assertEqual(v[-1].t + timedelta(minutes=30), k[-1].t + timedelta(minutes=5))      # endet mit der letzten Kerze
        self.assertEqual(verdichten(k[1:], timedelta(minutes=30))[0].t, v[1].t)                # unvollständige älteste Gruppe fällt weg
        self.assertEqual(len(verdichten(k[1:], timedelta(minutes=30))), 4)
        self.assertEqual(verdichten(k, timedelta(minutes=5)), k)                 # nicht kürzer als geliefert: unverändert
        self.assertEqual(verdichten(k[:1], timedelta(hours=1)), k[:1])


class PositionTest(Basis):
    def test_offene_position_gleiche_richtung_kein_neues_signal(self):
        s = self.strategie(antwort(kurz=("steigt", 0.9)))
        self.assertIsNone(self.bewerte(s, positionen={"XBT": 0.5}))

    def test_umkehr_mit_mindestwahrscheinlichkeit_schliesst(self):
        s = self.strategie(antwort(kurz=("faellt", 0.8)))
        sig = self.bewerte(s, positionen={"XBT": 0.5})
        self.assertEqual(sig.richtung, "flat")
        self.assertIn("schließen", sig.begruendung)
        s = self.strategie(antwort(kurz=("steigt", 0.8)))
        self.assertEqual(self.bewerte(s, positionen={"XBT": -0.5}).richtung, "flat")

    def test_umkehr_ohne_genug_wahrscheinlichkeit_oder_abgeschaltet_laesst_stop_und_ziel_gelten(self):
        s = self.strategie(antwort(kurz=("faellt", 0.55)))
        self.assertIsNone(self.bewerte(s, positionen={"XBT": 0.5}))
        s = self.strategie(antwort(kurz=("faellt", 0.9)), ausstieg_bei_umkehr=False)
        self.assertIsNone(self.bewerte(s, positionen={"XBT": 0.5}))


class GrenzenTest(Basis):
    def test_aufrufgrenze_je_lauf_zaehlt_auch_nachfragen(self):
        s = self.strategie("kein JSON", antwort(), max_aufrufe=2)
        self.assertIsNotNone(self.bewerte(s, 0))                                 # 1. Versuch ungültig, Nachfrage gültig: 2 Aufrufe
        self.assertEqual(s.aufrufe, 2)
        self.assertIsNone(self.bewerte(s, 31))                                   # Grenze erreicht: kein Aufruf, kein Signal
        self.assertEqual(s.aufrufe, 2)
        self.assertEqual(len(self.treiber.prompts), 2)
        self.assertIn("Aufrufgrenze", self.notizen()[-1])

    def test_aufrufgrenze_je_tag_gilt_nach_der_zeit_der_lage(self):
        s = self.strategie(antwort(), max_aufrufe_pro_tag=2, abstand_min=30)
        for minuten in (0, 30, 60):
            self.bewerte(s, minuten)
        self.assertEqual(len(self.treiber.prompts), 2)
        self.assertIn("für heute erreicht", self.notizen()[-1])
        self.bewerte(s, 24 * 60 + 90)                                            # nächster Kalendertag: wieder erlaubt
        self.assertEqual(len(self.treiber.prompts), 3)

    def test_ungueltige_parameter(self):
        for schlecht in ({"horizont": "lang"}, {"min_wahrscheinlichkeit": 1.5}, {"stop_atr": 0}, {"abstand_min": -1},
                         {"kontext_takt": "xyz"}, {"unbekannt": 1}):
            with self.assertRaises(ValueError, msg=schlecht):
                KiStrategie(**schlecht)

    def test_eingetragen_und_standardwerte(self):
        self.assertIs(STRATEGIEN["ki"], KiStrategie)
        p = KiStrategie().p
        self.assertEqual((p["horizont"], p["min_wahrscheinlichkeit"], p["abstand_min"]), ("kurz", 0.6, 0))


class AblaufTest(Basis):
    """Die Strategie im Autopiloten (Wiedergabe, Papier-Broker): Risikoprüfung, Journal, Stop und Ziel wie bei jeder Strategie."""

    def aufbauen(self, *antworten, einst_teil=None, **parameter):
        e = {"maerkte": {"XBT": "krypto"}, "osiris": {"automatisch": False}, "ki": {"treiber": "claude_cli"},
             "risiko": {"standard_markt": "krypto", "short_erlaubt": True, "tagesverlust_max": 10000, "tagesverlust_max_prozent": 100,
                        "risiko_pro_trade_prozent": 0.5, "mengenschritt": 0.0001},
             "autopilot": {"symbole": ["XBT"], "takt": "5m", "pause_s": 0, "kerzen_fenster": 500}}
        for k, v in (einst_teil or {}).items():
            e.setdefault(k, {}).update(v)
        self.einst = mit_standard(e)
        self.treiber = FakeTreiber(*(antworten or (antwort(),)))
        self.s = strategie_holen("ki", self.einst, treiber=self.treiber, osiris_lage=osiris_lage(), historie=FakeHistorie(),
                                 **parameter)
        self.quelle = WiedergabeQuelle({"XBT": self.kerzen}, "5m", 0.05, start=190)
        self.broker = PapierBroker(self.quelle.kurs, self.einst, ":memory:", kerzengeber=self.quelle.kerzen, uhr=self.quelle.jetzt)
        self.journal = Journal(":memory:", self.einst, self.quelle.jetzt)
        self.addCleanup(self.broker.beenden)
        self.addCleanup(self.journal.beenden)
        self.ausgabe: list[str] = []
        self.ap = Autopilot(self.broker, self.quelle, self.s, self.journal, self.einst, self.ordner, "papier", ["XBT"],
                            uhr=self.quelle.jetzt, warten=self.quelle.weiter, ausgabe=self.ausgabe.append)

    def test_autopilot_bindet_die_strategie_an_und_handelt_mit_stop_und_ziel(self):
        self.aufbauen(antwort(kurz=("steigt", 0.8)))
        self.ap.laufen(max_schritte=40)
        entscheidungen = [e for e in self.journal.entscheidungen(100) if e["strategie"] == "ki" and e["gesendet"]]
        self.assertTrue(entscheidungen, "die KI-Strategie hat nichts gehandelt")
        e = entscheidungen[0]
        self.assertEqual(e["pruefung_ok"], 1)
        signal, order = json.loads(e["signal"]), json.loads(e["ord"])
        self.assertEqual(signal["richtung"], "long")
        self.assertIsNotNone(order["stop_loss"])                                 # Pflicht-Stop durch die Risikoprüfung
        self.assertIsNotNone(order["take_profit"])
        self.assertIn("KI (kurz): steigt 0.80", signal["begruendung"])
        self.assertGreaterEqual(len(self.journal.einschaetzungen()), 3)          # Einschätzungen der KI im Journal
        self.assertEqual(len(self.treiber.prompts), 7)                           # 40 Kerzen = 200 Minuten bei 30 Minuten Abstand

    def test_risikopruefung_bleibt_vor_jeder_order_auch_bei_ki_signal(self):
        self.aufbauen(antwort(kurz=("steigt", 0.95)), einst_teil={"risiko": {"orders_max_pro_tag": 0}})
        self.ap.laufen(max_schritte=40)
        abgelehnt = [e for e in self.journal.entscheidungen(100) if e["strategie"] == "ki" and e["pruefung_ok"] == 0]
        self.assertTrue(abgelehnt)
        self.assertEqual(self.broker.trades(), [])
        self.assertEqual(self.broker.positionen(), [])

    def test_short_verboten_im_autopiloten_gibt_keine_order(self):
        self.aufbauen(antwort(kurz=("faellt", 0.95)), einst_teil={"risiko": {"short_erlaubt": False}})
        self.ap.laufen(max_schritte=40)
        self.assertEqual([e for e in self.journal.entscheidungen(100) if e["gesendet"]], [])
        self.assertTrue(any("Short ist verboten" in (n or "") for n in self.notizen()))

    def test_ki_fehler_im_autopiloten_ist_kein_absturz_und_kein_handel(self):
        self.aufbauen(ki.KiFehler("CLI weg"))
        self.ap.laufen(max_schritte=40)
        self.assertEqual(self.ap.fehler_folge, 0)
        self.assertEqual(self.broker.positionen(), [])
        self.assertTrue(any("KI nicht verfügbar" in (n or "") for n in self.notizen()))


class BacktestMitFakeTest(Basis):
    def test_backtest_mit_fake_treiber_ist_deterministisch(self):
        kerzen = zufallskerzen(tage=3, seed=11, takt_min=5, pro_tag=288)
        e = mit_standard({"maerkte": {"XBT": "krypto"}, "risiko": {"standard_markt": "krypto", "short_erlaubt": True},
                          "backtest": {"tagesende_flat": False}})
        ergebnisse = []
        for _ in range(2):
            s = strategie_holen("ki", e, treiber=FakeTreiber(antwort(kurz=("steigt", 0.8))), osiris_lage=osiris_lage(),
                                historie=FakeHistorie())
            erg = backtest(s, kerzen, e, "XBT", "krypto")
            ergebnisse.append([(t.einstieg_zeit, t.richtung, round(t.netto, 6)) for t in erg.trades])
        self.assertEqual(ergebnisse[0], ergebnisse[1])
        self.assertGreater(len(ergebnisse[0]), 0)


if __name__ == "__main__":
    unittest.main()
