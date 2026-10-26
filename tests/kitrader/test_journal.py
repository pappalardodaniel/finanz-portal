"""Journal: Entscheidungen, Einschätzungen, automatische Auswertung, Trefferquote je Horizont."""
import unittest
from datetime import datetime, timedelta, timezone

from kitrader.broker.basis import Order, OrderStand
from kitrader.einstellungen import mit_standard
from kitrader.journal import Journal
from kitrader.risiko import Ergebnis

from .daten import MONTAG, Uhr


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.uhr = Uhr(MONTAG)
        self.j = Journal(":memory:", mit_standard({}), self.uhr)
        self.addCleanup(self.j.beenden)

    # ---- Eigene Positionen und Ziele

    def test_eigene_positionen_und_ziel_ueberleben_einen_neustart(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / "j.db", mit_standard({}), self.uhr)
            j.eigene_merken("BTC/EUR", "papier", "long", 52000.0, "O1")
            j.eigene_merken("XBTEUR", "demo", "short")                   # andere Betriebsart, anderes Konto
            j.beenden()
            j = Journal(Path(d) / "j.db", mit_standard({}), self.uhr)
            self.addCleanup(j.beenden)
            e = j.eigene("papier")
            self.assertEqual([(x["symbol"], x["richtung"], x["ziel"], x["order_id"]) for x in e],
                             [("BTC/EUR", "long", 52000.0, "O1")])
            self.assertEqual([x["symbol"] for x in j.eigene("demo")], ["XBTEUR"])

    def test_eigene_gleiches_symbol_unter_anderem_namen_ersetzt_den_eintrag(self):
        self.j.eigene_merken("BTC/EUR", "papier", "long", 50000.0)
        self.j.eigene_merken("XBTEUR", "papier", "long", 51000.0)         # derselbe Schlüssel (symbol_schluessel)
        self.assertEqual([x["ziel"] for x in self.j.eigene("papier")], [51000.0])

    def test_ziel_loeschen_und_eigene_entfernen(self):
        self.j.eigene_merken("ABC", "papier", "long", 110.0)
        self.j.ziel_loeschen("abc", "papier")
        e = self.j.eigene("papier")
        self.assertEqual((len(e), e[0]["ziel"]), (1, None))
        self.j.eigene_entfernen("ABC", "papier")
        self.assertEqual(self.j.eigene("papier"), [])

    # ---- Entscheidungen

    def test_entscheidung_von_signal_bis_ergebnis(self):
        order = Order("ABC", "kauf", 5, stop_loss=95.0)
        eid = self.j.entscheidung("papier", "ABC", "ausbruch", {"richtung": "long"}, Ergebnis(True), order, "Test")
        self.j.ausfuehrung(eid, OrderStand("P000001", order, "ausgefuehrt", 5, 100.0, MONTAG))
        self.assertTrue(self.j.ergebnis_zu_order("P000001", -12.5, "Stop"))
        self.j.ergebnis(eid, 2.5)                                    # Ergebnisse addieren sich
        e = self.j.entscheidungen()[0]
        self.assertEqual((e["symbol"], e["modus"], e["pruefung_ok"], e["order_id"]), ("ABC", "papier", 1, "P000001"))
        self.assertAlmostEqual(e["ergebnis"], -10.0)
        self.assertTrue(e["zeit"].startswith("2026-03-02T10:00"))
        self.assertIn('"stop_loss": 95.0', e["ord"])
        self.assertIn("ausgefuehrt", e["ausfuehrung"])

    def test_abgelehnte_entscheidung_mit_gruenden(self):
        eid = self.j.entscheidung("papier", "ABC", "x", None, Ergebnis(False, ["Stop-Loss fehlt", "Short ist nicht erlaubt"]),
                                  Order("ABC", "verkauf", 1))
        e = self.j.entscheidungen()[0]
        self.assertEqual((e["id"], e["pruefung_ok"], e["gesendet"]), (eid, 0, 0))
        self.assertIn("Stop-Loss fehlt", e["gruende"])

    def test_orders_heute_zaehlt_nur_gesendete(self):
        a = self.j.entscheidung("papier", "ABC", "x", None, Ergebnis(True), Order("ABC", "kauf", 1))
        self.j.entscheidung("papier", "ABC", "x", None, Ergebnis(False, ["x"]), Order("ABC", "kauf", 1))
        self.j.ausfuehrung(a, OrderStand("P1", Order("ABC", "kauf", 1), "ausgefuehrt"))
        self.assertEqual(self.j.orders_heute("2026-03-02"), 1)
        self.assertEqual(self.j.orders_heute("2026-03-03"), 0)
        self.assertEqual(self.j.orders_heute("2026-03-02", "demo"), 0)

    def test_tagesstand_und_sperre(self):
        self.assertEqual(self.j.tagesstand("2026-03-02"), {})
        self.assertEqual(self.j.tagesstand("2026-03-02", 10000.0)["startwert"], 10000.0)
        self.assertEqual(self.j.tagesstand("2026-03-02", 99.0)["startwert"], 10000.0)      # bleibt
        self.j.tag_sperren("2026-03-02", "Verlust")
        self.assertEqual(self.j.tagesstand("2026-03-02")["gesperrt"], 1)

    # ---- Einschätzungen

    def test_eingabe_wird_geprueft(self):
        with self.assertRaises(ValueError):
            self.j.einschaetzung("AAPL", "hoch", "1d")
        with self.assertRaises(ValueError):
            self.j.einschaetzung("AAPL", "steigt", "2d")

    def test_auswertung_und_trefferquote_je_horizont(self):
        self.j.einschaetzung("AAPL", "steigt", "1d", "a", 100.0)        # +2 %  -> Treffer (Schwelle 1 %)
        self.j.einschaetzung("AAPL", "faellt", "1d", "b", 100.0)        # +2 %  -> kein Treffer
        self.j.einschaetzung("AAPL", "seitwaerts", "1d", "c", 100.0)    # +2 %  -> kein Treffer (Bewegung über der Schwelle)
        self.j.einschaetzung("AAPL", "seitwaerts", "1d", "d", 100.0)    # +2 %  -> kein Treffer
        self.j.einschaetzung("MSFT", "faellt", "1w", "e", 200.0)        # -3 %  -> Treffer (Schwelle 2 %)
        self.j.einschaetzung("MSFT", "steigt", "1M", "f", 200.0)        # noch nicht fällig
        preise = {"AAPL": 102.0, "MSFT": 194.0}
        self.uhr.weiter(days=8)
        fertig = self.j.auswerten(lambda wert, zeit: preise[wert])
        self.assertEqual(len(fertig), 5)
        q = self.j.trefferquote()
        self.assertEqual((q["1d"]["n"], q["1d"]["treffer"]), (4, 1))
        self.assertAlmostEqual(q["1d"]["quote"], 25.0)
        self.assertEqual((q["1w"]["n"], q["1w"]["treffer"], q["1w"]["quote"]), (1, 1, 100.0))
        self.assertEqual((q["1M"]["n"], q["1M"]["quote"], q["1M"]["offen"]), (0, None, 1))
        self.assertEqual(set(q), {"1h", "4h", "1d", "1w", "1M", "3M", "1J"})        # alle Horizonte, n immer sichtbar

    def test_seitwaerts_treffer(self):
        self.j.einschaetzung("AAPL", "seitwaerts", "1d", "", 100.0)
        self.uhr.weiter(days=2)
        self.j.auswerten(lambda w, z: 100.4)
        self.assertEqual(self.j.trefferquote()["1d"]["treffer"], 1)

    def test_schwelle_ist_einstellbar(self):
        j = Journal(":memory:", mit_standard({"journal": {"schwellen": {"1d": 3.0}}}), self.uhr)
        self.addCleanup(j.beenden)
        j.einschaetzung("AAPL", "steigt", "1d", "", 100.0)
        self.uhr.weiter(days=2)
        j.auswerten(lambda w, z: 102.0)                                 # +2 % reichen bei Schwelle 3 % nicht
        self.assertEqual(j.trefferquote()["1d"]["treffer"], 0)

    def test_nicht_abgelaufene_und_unbekannter_kurs_bleiben_offen(self):
        self.j.einschaetzung("AAPL", "steigt", "4h", "", 100.0)
        self.uhr.weiter(hours=3)
        self.assertEqual(self.j.auswerten(lambda w, z: 110.0), [])
        self.uhr.weiter(hours=2)
        self.assertEqual(self.j.auswerten(lambda w, z: None), [])        # Kurs nicht verfügbar: später nochmal
        self.assertEqual(self.j.trefferquote()["4h"]["offen"], 1)
        self.assertEqual(len(self.j.auswerten(lambda w, z: 110.0)), 1)
        self.assertEqual(self.j.trefferquote()["4h"]["offen"], 0)

    def test_der_kurs_wird_zum_faelligkeitszeitpunkt_erfragt(self):
        gefragt = []
        self.j.einschaetzung("AAPL", "steigt", "1h", "", 100.0)
        self.uhr.weiter(hours=5)
        self.j.auswerten(lambda w, z: gefragt.append(z) or 101.0)
        self.assertEqual(gefragt, [MONTAG + timedelta(hours=1)])

    def test_ohne_startkurs_nicht_auswertbar(self):
        self.j.einschaetzung("AAPL", "steigt", "1h", "")
        self.uhr.weiter(hours=2)
        self.assertEqual(self.j.auswerten(lambda w, z: 1.0), [])
        self.assertEqual(self.j.trefferquote()["1h"]["n"], 0)
        self.assertEqual(self.j.trefferquote()["1h"]["offen"], 0)


if __name__ == "__main__":
    unittest.main()
