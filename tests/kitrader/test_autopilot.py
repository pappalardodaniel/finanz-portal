"""Autopilot: Freigabe-Sperre für echt, Ablauf mit Wiedergabe, Schutz-Stops, Tagesverlust, Not-Aus, Tagesabschluss."""
import dataclasses
import logging
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from kitrader.autopilot import FREIGABE_DATEI, Abbruch, Autopilot, banner, bauen, modus_pruefen
from kitrader.broker.basis import BrokerFehler, Kerze, Kurs, Order, OrderStand, Position
from kitrader.broker.papier import PapierBroker
from kitrader.einstellungen import mit_standard
from kitrader.journal import Journal
from kitrader.kursquelle import WiedergabeQuelle
from kitrader.strategie import Signal, Strategie

UTC = timezone.utc
START = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
HEUTE = date(2026, 3, 2)


class Immer(Strategie):
    """Long-Signal mit 1 Euro Stop und 5 Euro Ziel, sobald 3 Kerzen da sind und nichts offen ist."""
    name = "immer"

    def bewerten(self, kerzen, lage):
        if lage.menge() or len(kerzen) < 3:
            return None
        c = kerzen[-1].c
        return Signal("long", c, c - 1.0, c + 5.0, 0.9, "Test")


class Nie(Strategie):
    name = "nie"

    def bewerten(self, kerzen, lage):
        return None


def flach(preise, start=START, takt_min=5):
    return [Kerze(start + timedelta(minutes=takt_min * i), p, p, p, p, 1000) for i, p in enumerate(preise)]


class FreigabeTest(unittest.TestCase):
    """echt nur mit modus = "echt" in den Einstellungen UND Freigabedatei mit heutigem Datum."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ordner = Path(self._d.name)
        self.datei = self.ordner / FREIGABE_DATEI

    def echt(self):
        return mit_standard({"modus": "echt"})

    def test_papier_und_demo_brauchen_keine_freigabe(self):
        self.assertEqual(modus_pruefen("papier", mit_standard({}), self.ordner, HEUTE), "papier")
        self.assertEqual(modus_pruefen("demo", mit_standard({}), self.ordner, HEUTE), "demo")

    def test_unbekannte_betriebsart(self):
        with self.assertRaises(Abbruch):
            modus_pruefen("live", mit_standard({}), self.ordner, HEUTE)

    def test_echt_verweigert_wenn_einstellung_nicht_echt(self):
        self.datei.write_text(HEUTE.isoformat())
        with self.assertRaises(Abbruch) as k:
            modus_pruefen("echt", mit_standard({"modus": "papier"}), self.ordner, HEUTE)
        self.assertIn("modus", str(k.exception))

    def test_echt_verweigert_ohne_datei(self):
        with self.assertRaises(Abbruch) as k:
            modus_pruefen("echt", self.echt(), self.ordner, HEUTE)
        self.assertIn(FREIGABE_DATEI, str(k.exception))

    def test_echt_verweigert_bei_falschem_datum(self):
        self.datei.write_text("2026-03-01\n")
        with self.assertRaises(Abbruch) as k:
            modus_pruefen("echt", self.echt(), self.ordner, HEUTE)
        self.assertIn("Datum", str(k.exception))
        self.datei.write_text("")
        with self.assertRaises(Abbruch):
            modus_pruefen("echt", self.echt(), self.ordner, HEUTE)

    def test_echt_erlaubt_mit_beidem(self):
        self.datei.write_text(f"Freigabe für {HEUTE.isoformat()} von mir\n")
        self.assertEqual(modus_pruefen("echt", self.echt(), self.ordner, HEUTE), "echt")
        self.datei.write_text("freigegeben am 02.03.2026")
        self.assertEqual(modus_pruefen("echt", self.echt(), self.ordner, HEUTE), "echt")

    def test_freigabe_gilt_nur_fuer_den_tag(self):
        self.datei.write_text(HEUTE.isoformat())
        with self.assertRaises(Abbruch):
            modus_pruefen("echt", self.echt(), self.ordner, HEUTE + timedelta(days=1))

    def test_bauen_verweigert_echt_bevor_etwas_entsteht(self):
        with self.assertRaises(Abbruch):
            bauen("echt", mit_standard({}), self.ordner)
        self.assertFalse((self.ordner / "journal.db").exists())
        self.assertFalse((self.ordner / "papier.db").exists())

    def test_demo_ohne_demokonto_wird_abgebrochen(self):
        # IBKR ohne Kontokennung "DU..." gilt als Echtgeld-Konto -> demo darf nicht starten (kein Netz nötig)
        with self.assertRaises(Abbruch) as k:
            bauen("demo", mit_standard({"broker": "ibkr"}), self.ordner)
        self.assertIn("Echtgeld", str(k.exception))

    def test_demo_mit_demokonto_baut(self):
        ap = bauen("demo", mit_standard({"broker": "ibkr", "ibkr": {"konto_id": "DU1234567"}}), self.ordner)
        self.addCleanup(ap.journal.beenden)
        self.assertEqual((ap.modus, ap.broker.name, ap.broker.papier), ("demo", "ibkr", True))

    def test_echt_mit_freigabe_baut_ohne_zu_verbinden(self):
        self.datei.write_text(HEUTE.isoformat())
        ap = bauen("echt", mit_standard({"modus": "echt", "broker": "ibkr", "ibkr": {"konto_id": "U1234567"}}),
                   self.ordner, heute=HEUTE)
        self.addCleanup(ap.journal.beenden)
        self.assertEqual((ap.modus, ap.broker.papier), ("echt", False))

    def test_echt_laeuft_nur_solange_die_freigabe_gilt(self):
        self.datei.write_text(HEUTE.isoformat())
        ap = bauen("echt", mit_standard({"modus": "echt", "broker": "ibkr", "ibkr": {"konto_id": "U1234567"}}),
                   self.ordner, ["AAPL"], heute=HEUTE, ausgabe=lambda text: None)
        self.addCleanup(ap.journal.beenden)
        ap.tagesbericht = lambda titel: None                 # kein Verbindungsversuch zum Gateway
        self.datei.unlink()                                  # Freigabe zurückgezogen
        ap.laufen(max_schritte=3)                            # darf keinen einzigen Broker-Aufruf machen und endet
        self.assertTrue(any(e["art"] == "abbruch" for e in ap.journal.ereignisse()))

    def test_direkt_gebauter_echt_autopilot_braucht_ebenfalls_die_freigabe(self):
        with self.assertRaises(Abbruch):
            Autopilot(None, None, Nie(), None, mit_standard({"modus": "echt"}), self.ordner, "echt", ["X"])

    def test_echt_mit_demokonto_wird_abgebrochen(self):
        self.datei.write_text(HEUTE.isoformat())
        with self.assertRaises(Abbruch):
            bauen("echt", mit_standard({"modus": "echt", "broker": "ibkr", "ibkr": {"konto_id": "DU1234567"}}),
                  self.ordner, heute=HEUTE)

    def test_direkt_gebauter_papier_autopilot_verweigert_einen_echtgeld_broker(self):
        # Ohne diese Sperre würde Autopilot(IbkrBroker(echtgeld_erlaubt=True), modus="papier") mit echtem Geld handeln.
        from types import SimpleNamespace
        echt = SimpleNamespace(name="ibkr", papier=False)
        geprueft = SimpleNamespace(name="kraken", papier=False, nur_pruefen=True)
        e = mit_standard({})
        j = Journal(":memory:", e)
        self.addCleanup(j.beenden)
        for modus in ("papier", "demo"):
            with self.assertRaises(Abbruch):
                Autopilot(echt, None, Nie(), j, e, self.ordner, modus, ["X"])
        Autopilot(geprueft, None, Nie(), j, e, self.ordner, "demo", ["X"])      # Kraken "nur prüfen" ist erlaubt

    def test_banner(self):
        e = mit_standard({})
        self.assertIn("ECHTES GELD", banner("echt", e))
        self.assertIn("kein echtes Geld", banner("papier", e))
        self.assertIn("Demokonto", banner("demo", e))
        self.assertIn("Tagesverlust", banner("papier", e))


class AblaufTest(unittest.TestCase):
    def aufbauen(self, preise, strategie, markt="krypto", **teil):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ordner = Path(self._d.name)
        e = {"maerkte": {"TEST": markt},
             "kosten": {"gebuehr_fest": 1.0, "gebuehr_prozent": 0.0, "schlupf_prozent": 0.0, "spread_prozent": 0.0},
             "risiko": {"standard_markt": "krypto"},
             "autopilot": {"symbole": ["TEST"], "takt": "5m", "pause_s": 0, "sicherheit_min": 0.5}}
        for k, v in teil.items():
            e.setdefault(k, {}).update(v)
        if markt == "boerse":
            e["handelszeiten"] = {"boerse": {"zeitzone": "UTC", "von": "09:00", "bis": "10:30", "tage": [0, 1, 2, 3, 4]}}
        self.einst = mit_standard(e)
        self.quelle = WiedergabeQuelle({"TEST": preise if isinstance(preise[0], Kerze) else flach(preise)}, "5m", 0.0)
        self.broker = PapierBroker(self.quelle.kurs, self.einst, ":memory:", kerzengeber=self.quelle.kerzen,
                                   uhr=self.quelle.jetzt)
        self.journal = Journal(":memory:", self.einst, self.quelle.jetzt)
        self.addCleanup(self.broker.beenden)
        self.addCleanup(self.journal.beenden)
        self.ausgabe: list[str] = []
        self.ap = Autopilot(self.broker, self.quelle, strategie, self.journal, self.einst, self.ordner, "papier",
                            ["TEST"], uhr=self.quelle.jetzt, warten=self.quelle.weiter, ausgabe=self.ausgabe.append)
        return self.ap

    def text(self):
        return "\n".join(self.ausgabe)

    def schritt(self, n=1):
        for _ in range(n):
            self.ap.schritt()
            self.quelle.weiter()

    # ---- Normalablauf

    def test_signal_wird_gehandelt_stop_loest_aus_und_alles_steht_im_journal(self):
        self.aufbauen([100, 100, 100, 100, 100, 98, 98, 98], Immer(), risiko={"tagesverlust_max": 5})
        self.ap.laufen()                                         # spielt alle Kerzen ab und endet von selbst
        self.assertIn("BETRIEBSART: PAPIER", self.text())
        self.assertEqual(self.broker.positionen(), [])
        t = self.broker.trades()
        self.assertEqual(len(t), 1)
        self.assertLess(t[0]["netto"], 0)                        # Stop bei 99 -> Ausführung bei 98
        e = self.journal.entscheidungen()[0]
        self.assertEqual((e["modus"], e["symbol"], e["strategie"], e["pruefung_ok"], e["gesendet"]),
                         ("papier", "TEST", "immer", 1, 1))
        self.assertAlmostEqual(e["ergebnis"], t[0]["netto"])     # Ergebnis steht bei der auslösenden Entscheidung
        self.assertIn('"richtung": "long"', e["signal"])
        self.assertIn('"stop_loss": 99.0', e["ord"])
        self.assertEqual(self.ap.fehler_folge, 0)

    def test_schutz_stop_ist_nach_dem_einstieg_da(self):
        self.aufbauen([100] * 6, Immer())
        self.schritt(3)
        self.assertEqual(self.broker.positionen()[0].menge, 10)          # 10 % von 10.000 bei Kurs 100
        arten = sorted(o.order.art for o in self.broker.orders())
        self.assertEqual(arten, ["limit", "stop"])

    def test_nur_eine_bewertung_je_kerze(self):
        self.aufbauen([100] * 6, Immer())
        self.ap.schritt()
        self.ap.schritt()                                        # gleiche Kerze noch einmal: nichts passiert
        self.assertEqual(len(self.journal.entscheidungen(100)), 0)
        self.schritt(3)
        n = len(self.journal.entscheidungen(100))
        self.ap.schritt()
        self.assertEqual(len(self.journal.entscheidungen(100)), n)

    def test_schwache_signale_werden_nur_protokolliert(self):
        class Schwach(Immer):
            def bewerten(self, kerzen, lage):
                s = super().bewerten(kerzen, lage)
                return None if s is None else Signal(s.richtung, s.einstieg, s.stop, s.ziel, 0.2, "schwach")
        self.aufbauen([100] * 5, Schwach())
        self.schritt(5)
        self.assertEqual(self.broker.positionen(), [])
        self.assertIn("Sicherheit", self.journal.entscheidungen()[0]["notiz"])

    # ---- Tagesverlust

    def test_tagesverlustgrenze_sperrt_neue_positionen(self):
        preise = [100, 100, 100, 98.5, 98.5, 98.5, 98.5, 98.5]
        self.aufbauen(preise, Immer(), risiko={"tagesverlust_max": 5})
        self.schritt(len(preise))
        self.assertEqual(len(self.broker.trades()), 1)           # Stop bei 99 -> Verlust ca. 15, danach gesperrt
        self.assertEqual(self.broker.positionen(), [])
        self.assertIn("TAGESVERLUSTGRENZE", self.text())
        self.assertEqual(self.journal.tagesstand("2026-03-02")["gesperrt"], 1)
        abgelehnt = [e for e in self.journal.entscheidungen(100) if e["pruefung_ok"] == 0]
        self.assertTrue(abgelehnt)
        self.assertIn("Tagesverlust", abgelehnt[0]["gruende"])

    def test_ohne_grenze_wird_weitergehandelt(self):
        preise = [100, 100, 100, 98.5, 98.5, 98.5, 98.5, 98.5]
        self.aufbauen(preise, Immer(), risiko={"tagesverlust_max": 1e9, "tagesverlust_max_prozent": 100})
        self.schritt(len(preise))
        self.assertEqual(len(self.broker.positionen()), 1)       # nach dem Stop wieder eingestiegen

    # ---- Schutz-Stops

    def test_position_ohne_schutz_stop_wird_geschlossen(self):
        self.aufbauen([100] * 4, Nie())
        self.broker.senden(Order("TEST", "kauf", 5))
        self.ap.schritt()
        self.assertEqual(self.broker.positionen(), [])
        self.assertIn("keinen ausreichenden Schutz-Stop", self.text())
        self.assertTrue(any(e["art"] == "ohne_schutz" for e in self.journal.ereignisse()))

    def test_position_ohne_schutz_stop_nur_warnen(self):
        self.aufbauen([100] * 4, Nie(), autopilot={"ohne_schutz": "warnen"})
        self.broker.senden(Order("TEST", "kauf", 5))
        self.ap.schritt()
        self.assertEqual(self.broker.positionen()[0].menge, 5)
        self.assertIn("WARNUNG", self.text())

    # ---- Not-Aus

    def test_notaus_schliesst_alles_und_sperrt_den_handel(self):
        self.aufbauen([100] * 12, Immer())
        self.schritt(3)
        self.assertEqual(len(self.broker.positionen()), 1)
        (self.ordner / "NOTAUS").write_text("test")
        self.schritt()
        self.assertEqual(self.broker.positionen(), [])
        self.assertEqual(self.broker.orders(), [])
        self.assertIn("NOT-AUS AKTIV", self.text())
        self.assertTrue(any(e["art"] == "notaus" for e in self.journal.ereignisse()))
        zahl = len(self.broker.orders(nur_offene=False))
        self.schritt(3)                                          # Strategie würde einsteigen, wird aber nicht gelassen
        self.assertEqual(len(self.broker.orders(nur_offene=False)), zahl)
        self.assertEqual(self.broker.positionen(), [])
        (self.ordner / "NOTAUS").unlink()
        self.schritt(2)
        self.assertIn("Not-Aus aufgehoben", self.text())
        self.assertEqual(len(self.broker.positionen()), 1)       # Handel läuft wieder

    def test_notaus_abgelehntes_glattstellen_wird_wiederholt(self):
        # Meldet der Broker die Glattstellung als "abgelehnt", ist der Not-Aus nicht fertig (früher: als Erfolg gezählt,
        # nie wiederholt, die Position blieb offen).
        self.aufbauen([100] * 12, Immer())
        self.schritt(3)
        self.assertEqual(len(self.broker.positionen()), 1)
        original = self.broker.senden
        versuche = []

        def ablehnen(order):
            if order.notiz == "Not-Aus" and not versuche:
                versuche.append(1)
                return OrderStand("X1", order, "abgelehnt", meldung="Test: abgelehnt")
            return original(order)
        self.broker.senden = ablehnen
        (self.ordner / "NOTAUS").write_text("test")
        self.schritt()
        self.assertEqual(len(self.broker.positionen()), 1)       # nicht geschlossen
        self.assertIn("FEHLER beim Glattstellen", self.text())
        self.assertIn("Not-Aus unvollständig", self.text())
        self.schritt()                                           # nächster Durchlauf versucht es erneut
        self.assertEqual(self.broker.positionen(), [])

    def test_notaus_ruft_alles_schliessen_des_brokers(self):
        self.aufbauen([100] * 4, Nie())
        aufrufe = []
        original = self.broker.alles_schliessen
        self.broker.alles_schliessen = lambda **kw: aufrufe.append(kw) or original(**kw)
        (self.ordner / "NOTAUS").write_text("x")
        self.schritt(3)
        self.assertEqual(len(aufrufe), 1)                        # einmal, nicht bei jedem Durchlauf
        self.assertEqual(aufrufe[0], {"symbole": ["TEST"]})      # nur die eigenen Symbole

    def test_risikopruefung_blockiert_auch_ohne_autopilot_schleife(self):
        from kitrader.risiko import Lage, pruefen
        self.aufbauen([100] * 3, Nie())
        (self.ordner / "NOTAUS").write_text("x")
        lage = self.ap._lage("TEST", self.quelle.jetzt())
        self.assertFalse(pruefen(Order("TEST", "kauf", 1, stop_loss=99.0), lage).ok)

    # ---- Handelsschluss

    def test_tagesabschluss_stellt_vor_handelsschluss_glatt(self):
        start = datetime(2026, 3, 2, 9, 30, tzinfo=UTC)
        kerzen = flach([100] * 14, start)                         # Börse 09:00-10:30 UTC
        self.aufbauen(kerzen, Immer(), markt="boerse")
        self.schritt(14)
        self.assertEqual(self.broker.positionen(), [])
        gl = [e for e in self.journal.entscheidungen(100) if "Tagesabschluss" in (e["notiz"] or "")]
        self.assertEqual(len(gl), 1)
        self.assertEqual(len(self.broker.trades()), 1)
        self.assertTrue(any(e["art"] == "tagesabschluss" for e in self.journal.ereignisse()))
        self.assertIn("Tagesabschluss 2026-03-02", self.text())
        # nach dem Schluss keine weiteren Orders
        n = len(self.broker.orders(nur_offene=False))
        self.assertGreaterEqual(n, 2)

    def test_keine_eroeffnung_kurz_vor_schluss(self):
        start = datetime(2026, 3, 2, 10, 5, tzinfo=UTC)          # erste Kerze endet 10:10 = 20 Min. vor Schluss
        self.aufbauen(flach([100] * 5, start), Immer(), markt="boerse")
        self.schritt(5)
        self.assertEqual(self.broker.trades(), [])
        self.assertEqual(self.broker.positionen(), [])


class MehrSymbolTest(unittest.TestCase):
    """Zwei Symbole an einer Börse (09:00-10:30 UTC), ein Papier-Broker, Wiedergabe-Kurse."""

    def aufbauen(self, strategie=None, **teil):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ordner = Path(self._d.name)
        e = {"maerkte": {"A": "boerse", "B": "boerse"},
             "kosten": {"gebuehr_fest": 1.0, "gebuehr_prozent": 0.0, "schlupf_prozent": 0.0, "spread_prozent": 0.0},
             "risiko": {"standard_markt": "boerse"},
             "handelszeiten": {"boerse": {"zeitzone": "UTC", "von": "09:00", "bis": "10:30", "tage": [0, 1, 2, 3, 4]}},
             "autopilot": {"symbole": ["A", "B"], "takt": "5m", "pause_s": 0, "sicherheit_min": 0.5}}
        for k, v in teil.items():
            e.setdefault(k, {}).update(v)
        self.einst = mit_standard(e)
        start = datetime(2026, 3, 2, 9, 30, tzinfo=UTC)
        self.quelle = WiedergabeQuelle({"A": flach([100] * 14, start), "B": flach([100] * 14, start)}, "5m", 0.0)
        self.broker = PapierBroker(self.quelle.kurs, self.einst, ":memory:", kerzengeber=self.quelle.kerzen,
                                   uhr=self.quelle.jetzt)
        self.journal = Journal(":memory:", self.einst, self.quelle.jetzt)
        self.addCleanup(self.broker.beenden)
        self.addCleanup(self.journal.beenden)
        self.ausgabe: list[str] = []
        self.ap = Autopilot(self.broker, self.quelle, strategie or Immer(), self.journal, self.einst, self.ordner,
                            "papier", ["A", "B"], uhr=self.quelle.jetzt, warten=self.quelle.weiter,
                            ausgabe=self.ausgabe.append)
        return self.ap

    def text(self):
        return "\n".join(self.ausgabe)

    def schritt(self, n=1):
        for _ in range(n):
            try:
                self.ap.schritt()
            finally:
                self.quelle.weiter()

    def test_fehler_bei_einem_symbol_blockiert_den_tagesabschluss_der_anderen_nicht(self):
        self.aufbauen()
        self.schritt(3)
        self.assertEqual(sorted(p.symbol for p in self.broker.positionen()), ["A", "B"])
        original = self.quelle.kurs

        def kurs(symbol):                        # A: Handel ausgesetzt, es gibt keinen Kurs
            if symbol == "A":
                raise BrokerFehler("Der Handel in A ist ausgesetzt (Halt).")
            return original(symbol)
        self.broker.kursgeber = kurs
        self.broker.kurs = lambda symbol: kurs(symbol)       # der Autopilot fragt den Kurs über den Broker
        while self.quelle.jetzt() < datetime(2026, 3, 2, 10, 25, tzinfo=UTC):     # bis 5 Minuten vor Schluss
            self.quelle.weiter()
        with self.assertRaises(BrokerFehler):                # der Fehler zählt weiter, geht aber nicht verloren
            self.ap.schritt()
        self.assertEqual([p.symbol for p in self.broker.positionen()], ["A"])    # B wurde trotzdem glattgestellt
        self.assertIn("A: FEHLER BrokerFehler", self.text())

    def test_fehler_bei_der_kerzenabfrage_eines_symbols_blockiert_die_anderen_nicht(self):
        self.aufbauen()
        original = self.quelle.kerzen

        def kerzen(symbol, takt="5m", anzahl=300):
            if symbol == "A":
                raise BrokerFehler("A: keine Daten")
            return original(symbol, takt, anzahl)
        self.quelle.kerzen = kerzen
        for _ in range(3):
            with self.assertRaises(BrokerFehler):
                self.ap.schritt()
            self.quelle.weiter()
        self.assertEqual([p.symbol for p in self.broker.positionen()], ["B"])    # früher: B kam nie dran

    def test_glattstellen_nur_einmal_je_durchlauf_auch_wenn_die_positionsliste_alt_ist(self):
        # Ohne Schutz-Stop schließt der Autopilot zuerst; kurz danach würde der Tagesabschluss noch einmal verkaufen,
        # wenn der Broker die Position noch zeigt (z. B. IBKR-Positionen aus dem Zwischenspeicher): Ergebnis wäre Short.
        self.aufbauen(Nie())
        while self.quelle.jetzt() < datetime(2026, 3, 2, 10, 25, tzinfo=UTC):
            self.quelle.weiter()
        self.broker.senden(Order("A", "kauf", 5))
        alt = self.broker.positionen()
        self.broker.positionen = lambda: alt                 # die Liste bleibt auf dem alten Stand
        self.ap.schritt()
        self.assertEqual(self.broker._menge("A"), 0)         # nicht -5
        verkaeufe = [o for o in self.broker.orders(nur_offene=False) if o.order.seite == "verkauf"]
        self.assertEqual(len(verkaeufe), 1)

    def test_mengen_zum_selben_symbol_werden_addiert(self):
        # Kraken: Margin-Position und Spot-Bestand heißen beide XBTEUR. Früher zählte nur der letzte Eintrag.
        self.aufbauen(Nie())
        alt = self.broker.positionen
        self.broker.positionen = lambda: [Position("A", 0.02, 50000.0), Position("A", 0.5, 50000.0)]
        lage = self.ap._lage("A", self.quelle.jetzt())
        self.assertAlmostEqual(lage.menge("A"), 0.52)

    def test_neustart_addiert_alte_trade_ergebnisse_nicht_noch_einmal(self):
        self.aufbauen(Nie())
        self.broker.senden(Order("A", "kauf", 5))
        verkauf = self.broker.senden(Order("A", "verkauf", 5))               # schließt: ein Trade mit Ergebnis
        eid = self.journal.entscheidung("papier", "A", "x", order=verkauf.order)
        self.journal.ausfuehrung(eid, verkauf)
        self.ap._ergebnisse_nachtragen()
        ergebnis = self.journal.entscheidungen(10)[0]["ergebnis"]
        self.assertIsNotNone(ergebnis)
        # Autopilot neu starten: gleiche Datenbanken, neues Objekt (früher wurde das Ergebnis dabei verdoppelt)
        neu = Autopilot(self.broker, self.quelle, Nie(), self.journal, self.einst, self.ordner, "papier", ["A", "B"],
                        uhr=self.quelle.jetzt, warten=self.quelle.weiter, ausgabe=self.ausgabe.append)
        neu.schritt()
        self.assertEqual(self.journal.entscheidungen(10)[0]["ergebnis"], ergebnis)


class SymbolNameTest(unittest.TestCase):
    """Der Broker nennt ein Symbol anders als die Einstellungen (Kraken: "BTC/EUR" -> Position "XBTEUR")."""

    def aufbauen(self):
        import dataclasses

        class Umbenennend(PapierBroker):
            """Wie Kraken/IBKR: Orders und Positionen laufen unter dem Namen des Brokers."""
            def senden(self, order):
                return super().senden(dataclasses.replace(order, symbol="XBTEUR"))

        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.ordner = Path(d.name)
        e = mit_standard({"maerkte": {"BTC/EUR": "krypto", "XBTEUR": "krypto"}, "risiko": {"standard_markt": "krypto"},
                          "kosten": {"gebuehr_fest": 1.0, "gebuehr_prozent": 0.0, "schlupf_prozent": 0.0},
                          "autopilot": {"symbole": ["BTC/EUR"], "takt": "5m", "pause_s": 0, "sicherheit_min": 0.0}})
        self.quelle = WiedergabeQuelle({"BTC/EUR": flach([100] * 10), "XBTEUR": flach([100] * 10)}, "5m", 0.0)
        self.broker = Umbenennend(self.quelle.kurs, e, ":memory:", kerzengeber=self.quelle.kerzen, uhr=self.quelle.jetzt)
        self.journal = Journal(":memory:", e, self.quelle.jetzt)
        self.addCleanup(self.broker.beenden)
        self.addCleanup(self.journal.beenden)
        self.ap = Autopilot(self.broker, self.quelle, Immer(), self.journal, e, self.ordner, "papier", ["BTC/EUR"],
                            uhr=self.quelle.jetzt, warten=self.quelle.weiter, ausgabe=lambda t: None)

    def test_offene_position_wird_unter_anderem_namen_erkannt(self):
        self.aufbauen()
        for _ in range(8):
            self.ap.schritt()
            self.quelle.weiter()
        self.assertEqual([(p.symbol, p.menge) for p in self.broker.positionen()], [("XBTEUR", 10.0)])   # früher: gestapelt
        eintritte = [o for o in self.broker.orders(nur_offene=False) if o.order.seite == "kauf"]
        self.assertEqual(len(eintritte), 1)

    def test_glattstellen_storniert_auch_die_stops_unter_dem_anderen_namen(self):
        self.aufbauen()
        for _ in range(4):
            self.ap.schritt()
            self.quelle.weiter()
        self.assertEqual(len(self.broker.orders()), 2)                       # Stop und Ziel stehen beim Broker
        self.ap._glattstellen("BTC/EUR", "Test", self.quelle.jetzt())
        self.assertEqual(self.broker.positionen(), [])
        self.assertEqual(self.broker.orders(), [])                            # sonst könnte ein Stop später short gehen


class VeralteteKerzeTest(unittest.TestCase):
    """Die Strategie darf nur Kerzen bewerten, die zur aktuellen Lage gehören."""

    def test_kerze_von_gestern_wird_nicht_bewertet(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        e = mit_standard({"maerkte": {"TEST": "krypto"}, "risiko": {"standard_markt": "krypto"},
                          "kosten": {"gebuehr_fest": 0.0, "gebuehr_prozent": 0.0, "schlupf_prozent": 0.0},
                          "autopilot": {"symbole": ["TEST"], "takt": "5m", "pause_s": 0, "sicherheit_min": 0.0}})
        gestern = flach([100] * 6, datetime(2026, 3, 1, 20, 0, tzinfo=UTC))       # letzte Kerze endet 20:30
        jetzt = [datetime(2026, 3, 2, 10, 2, tzinfo=UTC)]
        heute = flach([100] * 4, datetime(2026, 3, 2, 9, 45, tzinfo=UTC))         # 09:45-10:05, drei abgeschlossen
        liste = list(gestern)
        quelle = WiedergabeQuelle({"TEST": liste}, "5m", 0.0)
        quelle.jetzt = lambda: jetzt[0]
        quelle.kurs = lambda s: Kurs(s, 99.95, 100.05, 100.0, jetzt[0])
        quelle.kerzen = lambda s, t="5m", n=300: list(liste)
        broker = PapierBroker(quelle.kurs, e, ":memory:", kerzengeber=quelle.kerzen, uhr=lambda: jetzt[0])
        journal = Journal(":memory:", e, lambda: jetzt[0])
        self.addCleanup(broker.beenden)
        self.addCleanup(journal.beenden)
        ausgabe: list[str] = []
        ap = Autopilot(broker, quelle, Immer(), journal, e, Path(d.name), "papier", ["TEST"], uhr=lambda: jetzt[0],
                       warten=lambda s: True, ausgabe=ausgabe.append)
        ap.schritt()
        self.assertEqual(broker.positionen(), [])                    # früher: Kauf aufgrund der Kerzen von gestern
        self.assertIn("zu alt", "\n".join(ausgabe))
        ap.schritt()                                                  # dieselbe alte Kerze: keine zweite Meldung, kein Handel
        self.assertEqual(broker.positionen(), [])
        liste.extend(heute)                                           # frische Kerzen kommen an
        jetzt[0] = datetime(2026, 3, 2, 10, 6, tzinfo=UTC)
        ap.schritt()
        self.assertEqual(len(broker.positionen()), 1)


# ====================================================================== Sicherheitsverbesserungen (a, c-i)

class Einmal(Strategie):
    """Genau ein Signal (long oder short, Stop 1 daneben, Ziel 5 darüber bzw. darunter), danach nie wieder."""
    name = "einmal"

    def __init__(self, richtung="long"):
        super().__init__()
        self.richtung, self.fertig = richtung, False

    def bewerten(self, kerzen, lage):
        if self.fertig or lage.menge() or len(kerzen) < 3:
            return None
        self.fertig = True
        c = kerzen[-1].c
        if self.richtung == "long":
            return Signal("long", c, c - 1.0, c + 5.0, 0.9, "Test")
        return Signal("short", c, c + 1.0, c - 5.0, 0.9, "Test")


class OhneZielBroker(PapierBroker):
    """Wie Kraken: hängt nur den Stop an, das Ziel führt der Broker nicht als Order."""

    def fuehrt_ziel_als_order(self, order):
        return False

    def senden(self, order):
        return super().senden(dataclasses.replace(order, take_profit=None))


class SicherBasis(unittest.TestCase):
    """Papier-Broker mit mehreren Symbolen (Krypto, rund um die Uhr), Wiedergabe-Kurse, Beobachter-Mitschnitt."""

    def aufbauen(self, preise, strategie=None, symbole=("TEST",), broker_klasse=PapierBroker, kurs=None, **teil):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ordner = Path(self._d.name)
        e = {"kosten": {"gebuehr_fest": 1.0, "gebuehr_prozent": 0.0, "schlupf_prozent": 0.0, "spread_prozent": 0.0},
             "risiko": {"standard_markt": "krypto"},
             "autopilot": {"symbole": list(symbole), "takt": "5m", "pause_s": 0, "sicherheit_min": 0.5}}
        for k, v in teil.items():
            e.setdefault(k, {}).update(v)
        self.einst = mit_standard(e)
        self.quelle = WiedergabeQuelle({sym: flach(liste) for sym, liste in preise.items()}, "5m", 0.0)
        kursgeber = self.quelle.kurs
        if kurs is not None:                               # Kurs nachträglich verändern, z. B. Währung oder Zeitkennzeichen
            kursgeber = lambda symbol: kurs(self.quelle.kurs(symbol))   # noqa: E731
        self.kursgeber = kursgeber
        self.broker = broker_klasse(kursgeber, self.einst, ":memory:", kerzengeber=self.quelle.kerzen, uhr=self.quelle.jetzt)
        self.journal = Journal(":memory:", self.einst, self.quelle.jetzt)
        self.addCleanup(self.broker.beenden)
        self.addCleanup(self.journal.beenden)
        self.ausgabe: list[str] = []
        self.ereignisse: list[tuple[str, dict]] = []
        self.ap = self.autopilot(strategie or Nie(), list(symbole))
        return self.ap

    def autopilot(self, strategie, symbole):
        return Autopilot(self.broker, self.quelle, strategie, self.journal, self.einst, self.ordner, "papier", symbole,
                         uhr=self.quelle.jetzt, warten=self.quelle.weiter, ausgabe=self.ausgabe.append,
                         beobachter=[lambda art, daten: self.ereignisse.append((art, daten))])

    def schritt(self, n=1, ap=None):
        for _ in range(n):
            try:
                (ap or self.ap).schritt()
            finally:
                self.quelle.weiter()

    def text(self):
        return "\n".join(self.ausgabe)

    def arten(self):
        return [a for a, _ in self.ereignisse]

    def daten(self, art):
        return [d for a, d in self.ereignisse if a == art]

    def positionen(self):
        return {p.symbol: p.menge for p in self.broker.positionen()}


class KurszeitTest(SicherBasis):
    def test_kurs_ohne_echte_zeit_verhindert_eroeffnungen_aber_nicht_das_schliessen(self):
        geschaetzt = [False]
        self.aufbauen({"TEST": [100] * 12}, Immer(), kurs=lambda k: dataclasses.replace(k, zeit_geschaetzt=geschaetzt[0]))
        self.schritt(3)
        self.assertEqual(self.positionen(), {"TEST": 10})                  # mit echter Zeit: gehandelt
        geschaetzt[0] = True
        self.ap._glattstellen("TEST", "Test", self.quelle.jetzt())         # Schließen bleibt erlaubt
        self.assertEqual(self.positionen(), {})
        self.schritt(2)                                                    # Immer will wieder einsteigen, darf aber nicht
        self.assertEqual(self.positionen(), {})
        abgelehnt = [e for e in self.journal.entscheidungen(100) if e["pruefung_ok"] == 0]
        self.assertTrue(abgelehnt)
        self.assertIn("Kurszeit unbekannt", abgelehnt[-1]["gruende"])


class ZielTest(SicherBasis):
    """Ziele, die der Broker nicht als Order führt (Kraken), überwacht der Autopilot selbst."""

    LONG = [100, 100, 100, 100, 104, 104, 106, 106, 106, 106]       # Ziel 105 wird im 7. Durchlauf erreicht

    def test_ziel_wird_bei_erreichen_glattgestellt_und_der_stop_storniert(self):
        self.aufbauen({"TEST": self.LONG}, Einmal(), broker_klasse=OhneZielBroker)
        self.schritt(3)
        self.assertEqual(self.positionen(), {"TEST": 10})
        self.assertEqual([o.order.art for o in self.broker.orders()], ["stop"])    # der Broker führt nur den Stop
        eintrag = self.journal.eigene("papier")[0]
        self.assertEqual((eintrag["richtung"], eintrag["ziel"]), ("long", 105.0))
        self.schritt(3)                                                    # Kurs 104: Ziel noch nicht erreicht
        self.assertEqual(self.positionen(), {"TEST": 10})
        self.schritt(1)                                                    # Kurs 106
        self.assertEqual(self.positionen(), {})
        self.assertEqual(self.broker.orders(), [])                         # der Stop wurde mit storniert
        trade = self.broker.trades()[0]
        self.assertGreater(trade["netto"], 0)
        self.assertIn("Gewinnziel 105 erreicht", self.text())
        self.assertTrue(any(e["art"] == "ziel_erreicht" for e in self.journal.ereignisse()))
        d = self.daten("ziel_erreicht")
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]["symbol"], d[0]["ziel"], d[0]["preis"]), ("TEST", 105.0, 106.0))
        self.assertIsNone(self.journal.eigene("papier")[0]["ziel"])        # erledigt

    def test_ziel_ueberlebt_einen_neustart(self):
        self.aufbauen({"TEST": self.LONG}, Einmal(), broker_klasse=OhneZielBroker)
        self.schritt(4)
        neu = self.autopilot(Nie(), ["TEST"])                              # neues Objekt, nur Journal und Broker bleiben
        self.assertEqual(self.positionen(), {"TEST": 10})
        self.schritt(3, ap=neu)
        self.assertEqual(self.positionen(), {})
        self.assertIn("ziel_erreicht", self.arten())

    def test_short_ziel_liegt_unten(self):
        self.aufbauen({"TEST": [100, 100, 100, 100, 96, 94, 94, 94]}, Einmal("short"), broker_klasse=OhneZielBroker,
                      risiko={"short_erlaubt": True})
        self.schritt(3)
        self.assertEqual(self.positionen(), {"TEST": -10})
        eintrag = self.journal.eigene("papier")[0]
        self.assertEqual((eintrag["richtung"], eintrag["ziel"]), ("short", 95.0))
        self.schritt(3)
        self.assertEqual(self.positionen(), {})

    def test_ziel_das_der_broker_selbst_fuehrt_wird_nicht_doppelt_ueberwacht(self):
        self.aufbauen({"TEST": self.LONG}, Einmal())                       # normaler Papier-Broker: Stop und Ziel als Orders
        self.schritt(3)
        self.assertEqual(sorted(o.order.art for o in self.broker.orders()), ["limit", "stop"])
        self.assertIsNone(self.journal.eigene("papier")[0]["ziel"])
        self.schritt(5)
        self.assertEqual(self.positionen(), {})                            # das Ziel hat der Broker ausgeführt
        self.assertNotIn("ziel_erreicht", self.arten())

    def test_ziel_wird_nicht_ausgeloest_solange_die_position_fehlt(self):
        # Ein Eintrag ohne Position (z. B. Stop schon gegriffen) löst nichts aus, auch wenn der Kurs über dem Ziel liegt.
        self.aufbauen({"TEST": [100] * 3 + [110] * 6}, broker_klasse=OhneZielBroker)
        self.journal.eigene_merken("TEST", "papier", "long", 105.0, "X1")
        self.schritt(5)
        self.assertEqual(self.broker.orders(nur_offene=False), [])
        self.assertNotIn("ziel_erreicht", self.arten())
        self.assertEqual(self.journal.eigene("papier"), [])                # nach der Schonfrist aufgeräumt


class FremdeSymboleTest(SicherBasis):
    PREISE = {"TEST": [100] * 14, "FREMD": [50] * 14, "ANDERS": [60] * 14}

    def test_fremde_position_wird_nur_gemeldet_nie_angefasst(self):
        self.aufbauen(self.PREISE, Nie(), symbole=("TEST",))
        self.broker.senden(Order("TEST", "kauf", 5))                       # eigene Position ohne Schutz-Stop
        self.broker.senden(Order("FREMD", "kauf", 3))                      # fremde Position ohne Schutz-Stop
        self.schritt()
        self.assertEqual(self.positionen(), {"FREMD": 3})                  # nur die eigene wurde wegen fehlendem Stop geschlossen
        self.assertIn("Fremde Position FREMD +3", self.text())
        self.assertTrue(any(e["art"] == "fremde_position" for e in self.journal.ereignisse()))
        w = [d for d in self.daten("warnung") if d["art"] == "fremde_position"]
        self.assertEqual(len(w), 1)
        self.schritt(2)
        self.assertEqual(len([d for d in self.daten("warnung") if d["art"] == "fremde_position"]), 1)   # nur einmal je Lauf
        self.assertEqual(self.positionen(), {"FREMD": 3})

    def test_notaus_stellt_nur_eigene_positionen_glatt(self):
        self.aufbauen(self.PREISE, Nie(), symbole=("TEST",))
        self.broker.senden(Order("TEST", "kauf", 5, stop_loss=90.0))
        self.broker.senden(Order("FREMD", "kauf", 3, stop_loss=40.0))
        self.assertEqual(len(self.broker.orders()), 2)
        (self.ordner / "NOTAUS").write_text("x")
        self.schritt()
        self.assertEqual(self.positionen(), {"FREMD": 3})
        self.assertEqual([o.order.symbol for o in self.broker.orders()], ["FREMD"])    # sein Stop bleibt
        self.assertIn("Not-Aus: fremd, nicht angefasst: Position FREMD +3", self.text())
        n = self.daten("notaus")[0]
        self.assertEqual((n["aufgehoben"], n["vollstaendig"]), (False, True))

    def test_vom_autopiloten_eroeffnete_position_bleibt_auch_mit_anderen_symbolen_eigen(self):
        # Neustart mit anderen autopilot.symbole: die früher eröffnete Position gehört weiter dem Autopiloten.
        self.aufbauen(self.PREISE, Einmal(), symbole=("TEST",))
        self.schritt(3)
        self.assertEqual(self.positionen(), {"TEST": 10})
        self.broker.senden(Order("FREMD", "kauf", 3, stop_loss=40.0))
        anders = self.autopilot(Nie(), ["ANDERS"])                         # TEST steht nicht mehr in den Symbolen
        (self.ordner / "NOTAUS").write_text("x")
        self.schritt(1, ap=anders)
        self.assertEqual(self.positionen(), {"FREMD": 3})

    def test_eintraege_verschwinden_wenn_die_position_weg_ist(self):
        self.aufbauen(self.PREISE, Einmal(), symbole=("TEST",))
        self.schritt(3)
        self.assertEqual([e["symbol"] for e in self.journal.eigene("papier")], ["TEST"])
        self.ap._glattstellen("TEST", "Test", self.quelle.jetzt())
        self.schritt(1)                                                    # innerhalb der Schonfrist bleibt der Eintrag
        self.schritt(3)
        self.assertEqual(self.journal.eigene("papier"), [])


    def test_aussetzer_der_positionsliste_streicht_keinen_eintrag(self):
        # Nur weil die Liste einmal leer kommt (IBKR-Zwischenspeicher), darf eine eigene Position nicht plötzlich fremd werden.
        self.aufbauen(self.PREISE, Nie(), symbole=("ANDERS",))
        self.broker.senden(Order("TEST", "kauf", 5, stop_loss=90.0))
        self.journal.eigene_merken("TEST", "papier", "long")
        echt = self.broker.positionen
        self.schritt(3)
        self.broker.positionen = lambda: []
        self.schritt(2)                                                    # zwei Aussetzer in Folge
        self.broker.positionen = echt
        self.schritt(1)
        self.assertEqual([e["symbol"] for e in self.journal.eigene("papier")], ["TEST"])
        self.broker.positionen = lambda: []
        self.broker.orders = lambda nur_offene=True: []                    # (auch ihr Stop ist weg)
        self.schritt(3)                                                    # dreimal in Folge: die Position ist wirklich weg
        self.assertEqual(self.journal.eigene("papier"), [])


class TagesverlustTest(SicherBasis):
    PREISE = {"TEST": [100, 100, 100, 98, 98, 98, 98, 98], "FREMD": [50] * 8}

    def aufbauen_mit_position(self, preise=None, **teil):
        self.aufbauen(preise or self.PREISE, Nie(), symbole=("TEST",), risiko={"tagesverlust_max": 5, **teil.pop("risiko", {})},
                      **teil)
        self.broker.senden(Order("TEST", "kauf", 10, stop_loss=50.0))      # weit entfernter Stop: er greift nicht
        self.broker.senden(Order("FREMD", "kauf", 3, stop_loss=40.0))
        self.schritt(3)                                                    # Kurs 100: nichts passiert
        self.assertEqual(self.positionen(), {"TEST": 10, "FREMD": 3})

    def test_grenze_stellt_alle_eigenen_positionen_glatt_und_sperrt_den_tag(self):
        self.aufbauen_mit_position()
        self.schritt(1)                                                    # Kurs 98: unrealisiert -20, Grenze -5
        self.assertEqual(self.positionen(), {"FREMD": 3})                  # nur die eigene Position ist zu
        self.assertEqual(self.journal.tagesstand("2026-03-02")["gesperrt"], 1)
        self.assertIn("TAGESVERLUSTGRENZE", self.text())
        d = self.daten("tagesverlust")
        self.assertEqual(len(d), 1)
        self.assertTrue(d[0]["schliessen"])
        glatt = [e for e in self.journal.entscheidungen(100) if "Tagesverlustgrenze erreicht" in (e["notiz"] or "")]
        self.assertEqual(len(glatt), 1)

    def test_neue_positionen_bleiben_gesperrt(self):
        self.aufbauen_mit_position()
        self.ap.strategie = Immer()
        self.schritt(4)
        self.assertEqual(self.positionen(), {"FREMD": 3})
        abgelehnt = [e for e in self.journal.entscheidungen(100) if e["pruefung_ok"] == 0]
        self.assertTrue(abgelehnt and all("Tagesverlust" in e["gruende"] for e in abgelehnt))
        self.assertIn("ablehnung", self.arten())

    def test_ohne_schliessen_wird_nur_gesperrt(self):
        self.aufbauen_mit_position(risiko={"tagesverlust_schliessen": False})
        self.schritt(2)
        self.assertEqual(self.positionen(), {"TEST": 10, "FREMD": 3})      # offene Positionen behalten ihren Stop
        self.assertEqual(self.journal.tagesstand("2026-03-02")["gesperrt"], 1)
        self.assertFalse(self.daten("tagesverlust")[0]["schliessen"])

    def test_schliessen_ist_standard(self):
        self.assertTrue(mit_standard({})["risiko"]["tagesverlust_schliessen"])

    def test_grenze_wird_in_jedem_durchlauf_geprueft_nicht_nur_bei_neuen_kerzen(self):
        # Gleiche Kerze, neuer Kurs: die Verlustgrenze gilt trotzdem sofort (früher wurde sie nur bei einer neuen Kerze geprüft).
        self.aufbauen_mit_position(preise={"TEST": [100] * 8, "FREMD": [50] * 8})
        original = self.broker.kursgeber
        self.broker.kursgeber = lambda symbol: (dataclasses.replace(original(symbol), bid=97.0, ask=97.1)
                                                if symbol == "TEST" else original(symbol))
        self.ap.schritt()                                                  # keine neue Kerze, nur ein neuer Kurs
        self.assertEqual(self.positionen(), {"FREMD": 3})


class OffeneOrdersImAutopilotTest(SicherBasis):
    def test_offene_eroeffnungsorder_beim_broker_zaehlt_bei_der_positionsgroesse(self):
        self.aufbauen({"TEST": [100] * 12}, Immer())
        wartend = self.broker.senden(Order("TEST", "kauf", 10, "limit", limit=50.0, gueltig="gtc", stop_loss=40.0))
        self.schritt(3)                                                    # Immer will 10 Stück, 10 sind schon bestellt
        self.assertEqual(self.positionen(), {})
        abgelehnt = [e for e in self.journal.entscheidungen(100) if e["pruefung_ok"] == 0]
        self.assertTrue(abgelehnt)
        self.assertIn("schon offen bestellt", abgelehnt[-1]["gruende"])
        self.broker.stornieren(wartend.id)
        self.schritt(1)                                                    # neue Kerze, nichts mehr unterwegs
        self.assertEqual(self.positionen(), {"TEST": 10})

    def test_nicht_lesbare_offene_orders_verhindern_die_eroeffnung(self):
        self.aufbauen({"TEST": [100] * 12}, Immer())

        def kaputt(nur_offene=True):
            raise BrokerFehler("Orders nicht lesbar")
        self.broker.orders = kaputt
        self.schritt(3)                                                    # ohne Position fragt der Schutz-Check nicht nach Orders
        self.assertEqual(self.positionen(), {})
        self.assertIn("offene Orders nicht lesbar", self.text())
        abgelehnt = [e for e in self.journal.entscheidungen(100) if e["pruefung_ok"] == 0]
        self.assertTrue(abgelehnt)
        self.assertIn("offene Orders beim Broker nicht lesbar", abgelehnt[-1]["gruende"])


class WaehrungTest(SicherBasis):
    def usd(self, k):
        return dataclasses.replace(k, waehrung="USD")

    def test_fremde_waehrung_ohne_wechselkurs_keine_eroeffnung(self):
        self.aufbauen({"TEST": [100] * 8}, Immer(), kurs=self.usd)
        self.schritt(4)
        self.assertEqual(self.positionen(), {})
        e = self.journal.entscheidungen(10)[-1]
        self.assertEqual(e["pruefung_ok"], 0)
        self.assertIn("kein Wechselkurs USD -> EUR", e["gruende"])
        self.assertIn("kein Wechselkurs USD -> EUR", self.text())
        self.assertTrue(any("kein Wechselkurs" in " ".join(d["gruende"]) for d in self.daten("ablehnung")))

    def test_wechselkurs_aus_den_einstellungen_rechnet_menge_und_risiko_um(self):
        # 1 USD = 0,5 EUR: Kurs 100 USD = 50 EUR; Deckel 10 % von 10.000 EUR = 1000 EUR -> 20 Stück (ohne Umrechnung 10)
        self.aufbauen({"TEST": [100] * 8}, Immer(), kurs=self.usd, wechselkurse={"usd": 0.5})
        self.schritt(4)
        self.assertEqual(self.positionen(), {"TEST": 20})

    def test_wechselkurs_vom_broker_geht_vor_der_einstellung(self):
        self.aufbauen({"TEST": [100] * 8}, Immer(), kurs=self.usd, wechselkurse={"USD": 0.5})
        self.broker.wechselkursgeber = lambda von, nach: 0.25 if (von, nach) == ("USD", "EUR") else None
        self.schritt(4)
        self.assertEqual(self.positionen(), {"TEST": 40})                  # 1000 EUR / (100 x 0,25)

    def test_ohne_kurs_vom_broker_gilt_die_einstellung(self):
        self.aufbauen({"TEST": [100] * 8}, Immer(), kurs=self.usd, wechselkurse={"USD": 0.5})
        self.broker.wechselkursgeber = lambda von, nach: None
        self.schritt(4)
        self.assertEqual(self.positionen(), {"TEST": 20})

    def test_gleiche_oder_unbekannte_waehrung_bleibt_wie_bisher(self):
        self.aufbauen({"TEST": [100] * 8}, Immer(), kurs=lambda k: dataclasses.replace(k, waehrung="EUR"))
        self.schritt(4)
        self.assertEqual(self.positionen(), {"TEST": 10})


class BeobachterTest(SicherBasis):
    def test_start_entscheidung_ausfuehrung_ergebnis_und_ende(self):
        self.aufbauen({"TEST": [100, 100, 100, 100, 100, 98, 98, 98]}, Immer(), risiko={"tagesverlust_max": 50})
        self.ap.laufen()
        arten = self.arten()
        for erwartet in ("start", "entscheidung", "ausfuehrung", "ergebnis", "ende"):
            self.assertIn(erwartet, arten)
        self.assertEqual(arten[0], "start")
        self.assertEqual(arten[-1], "ende")
        self.assertEqual(self.daten("start")[0]["symbole"], ["TEST"])
        self.assertEqual(self.daten("start")[0]["strategie"], "immer")
        a = self.daten("ausfuehrung")[0]
        self.assertEqual((a["symbol"], a["stand"].order.seite, a["stand"].status), ("TEST", "kauf", "ausgefuehrt"))
        self.assertLess(self.daten("ergebnis")[0]["betrag"], 0)
        ende = self.daten("ende")[0]
        self.assertEqual((ende["grund"], ende["modus"]), ("gestoppt", "papier"))
        self.assertIn("Zwischenstand", ende["stand"])
        self.assertTrue(all(d["modus"] == "papier" for _, d in self.ereignisse))

    def test_notaus_wird_gemeldet_auch_das_aufheben(self):
        self.aufbauen({"TEST": [100] * 12}, Immer())
        self.schritt(3)
        (self.ordner / "NOTAUS").write_text("x")
        self.schritt(1)
        (self.ordner / "NOTAUS").unlink()
        self.schritt(1)
        n = self.daten("notaus")
        self.assertEqual([d["aufgehoben"] for d in n], [False, True])
        self.assertIn("glattgestellt", n[0]["text"])
        self.assertTrue(n[0]["vollstaendig"])

    def test_tagesabschluss_und_ablehnung_werden_gemeldet(self):
        self.aufbauen({"TEST": [100] * 3 + [98.5] * 11}, Immer(), risiko={"tagesverlust_max": 5})
        self.schritt(8)
        self.assertIn("tagesverlust", self.arten())
        self.assertIn("ablehnung", self.arten())
        ab = self.daten("ablehnung")[0]
        self.assertEqual((ab["symbol"], ab["seite"]), ("TEST", "kauf"))
        self.assertTrue(any("Tagesverlust" in g for g in ab["gruende"]))
        self.ap.tagesbericht("Tagesabschluss")
        self.assertEqual(len(self.daten("tagesabschluss")), 1)

    def test_fehler_im_beobachter_erreicht_den_handel_nie(self):
        self.aufbauen({"TEST": [100, 100, 100, 100, 100, 98, 98, 98]}, Immer(), risiko={"tagesverlust_max": 50})

        def kaputt(art, daten):
            raise RuntimeError("Beobachter kaputt")
        self.ap.beobachter.insert(0, kaputt)                               # der kaputte kommt zuerst, der gute danach
        with self.assertLogs("kitrader.autopilot", level="WARNING") as mitschnitt:
            self.ap.laufen()
        self.assertEqual(len(self.broker.trades()), 1)                     # gehandelt wie immer
        self.assertEqual(self.ap.fehler_folge, 0)
        self.assertIn("ende", self.arten())                                # der andere Beobachter bekam alles
        self.assertIn("Beobachter kaputt", "\n".join(mitschnitt.output))

    def test_ende_nennt_den_grund(self):
        self.aufbauen({"TEST": [100] * 8}, Nie())
        self.ap.schritt = mock.Mock(side_effect=Abbruch("Freigabe abgelaufen."))
        self.ap.laufen()
        d = self.daten("ende")[-1]
        self.assertEqual((d["grund"], d["text"]), ("abbruch", "Freigabe abgelaufen."))

        self.ap.schritt = mock.Mock(side_effect=KeyboardInterrupt())
        self.ap.laufen()
        self.assertEqual(self.daten("ende")[-1]["grund"], "tastatur")

        self.ap.schritt = mock.Mock(side_effect=BrokerFehler("weg"))
        self.ap.laufen()
        self.assertEqual(self.daten("ende")[-1]["grund"], "fehler_folge")

    def test_ende_auch_wenn_laufen_mit_einem_fehler_endet(self):
        self.aufbauen({"TEST": [100] * 8}, Nie(), symbole=("TEST",))
        self.ap.symbole = []
        with self.assertRaises(Abbruch):
            self.ap.laufen()
        d = self.daten("ende")[-1]
        self.assertEqual(d["grund"], "fehler")
        self.assertIn("Keine Symbole", d["text"])


if __name__ == "__main__":
    unittest.main()
