"""Einstellungen laden und die Kommandozeile (offline, mit eigenem Datenordner)."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kitrader import einstellungen as e
from kitrader.__main__ import main

from .daten import zufallskerzen


class EinstellungenTest(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.dir = Path(self._d.name)

    def test_beispieldatei_entspricht_den_standardwerten(self):
        self.assertEqual(e.laden(e.beispiel_pfad()), e.STANDARD)

    def test_ohne_datei_gelten_standardwerte(self):
        self.assertEqual(e.laden(self.dir / "gibtsnicht.toml"), e.STANDARD)

    def test_teilweise_datei_ueberschreibt_nur_das_genannte(self):
        p = self.dir / "e.toml"
        p.write_text('[risiko]\npositionen_max = 1\n[handelszeiten.us]\nvon = "10:00"\n[maerkte]\nSAP = "xetra"\n')
        d = e.laden(p)
        self.assertEqual(d["risiko"]["positionen_max"], 1)
        self.assertEqual(d["risiko"]["tagesverlust_max"], e.STANDARD["risiko"]["tagesverlust_max"])
        self.assertEqual((d["handelszeiten"]["us"]["von"], d["handelszeiten"]["us"]["bis"]), ("10:00", "16:00"))
        self.assertEqual(d["maerkte"], {"SAP": "xetra"})
        self.assertEqual(e.STANDARD["risiko"]["positionen_max"], 3)          # Standardwerte bleiben unberührt

    def test_pfad_aus_umgebungsvariable(self):
        p = self.dir / "x.toml"
        p.write_text('waehrung = "USD"\n')
        with mock.patch.dict(os.environ, {"KITRADER_KONFIG": str(p)}):
            self.assertEqual(e.laden()["waehrung"], "USD")

    def test_datenordner_aus_umgebungsvariable(self):
        with mock.patch.dict(os.environ, {"KITRADER_DATEN": str(self.dir / "daten")}):
            self.assertEqual(e.datenordner(), self.dir / "daten")
            self.assertTrue((self.dir / "daten").is_dir())

    def test_fehler_sind_lesbar(self):
        p = self.dir / "k.toml"
        p.write_text("das ist [ kein toml")
        with self.assertRaises(e.EinstellungsFehler):
            e.laden(p)
        p.write_text('modus = "wild"\n')
        with self.assertRaises(e.EinstellungsFehler):
            e.laden(p)
        p.write_text("[risiko]\ntagesverlust_max = -5\n")
        with self.assertRaises(e.EinstellungsFehler):
            e.laden(p)

    def test_neue_sicherheitseinstellungen_und_ihre_pruefung(self):
        self.assertTrue(e.STANDARD["risiko"]["tagesverlust_schliessen"])
        self.assertEqual(e.STANDARD["wechselkurse"], {})
        p = self.dir / "w.toml"
        p.write_text('[risiko]\ntagesverlust_schliessen = false\n[wechselkurse]\nUSD = 0.92\n')
        d = e.laden(p)
        self.assertFalse(d["risiko"]["tagesverlust_schliessen"])
        self.assertEqual(d["wechselkurse"], {"USD": 0.92})
        for text in ('[wechselkurse]\nUSD = 0\n', '[wechselkurse]\nUSD = "viel"\n', '[risiko]\ntagesverlust_schliessen = "ja"\n'):
            p.write_text(text)
            with self.assertRaises(e.EinstellungsFehler, msg=text):
                e.laden(p)

    def test_standard_ist_vorsichtig_und_papier(self):
        self.assertEqual(e.STANDARD["modus"], "papier")
        self.assertFalse(e.STANDARD["risiko"]["short_erlaubt"])
        self.assertTrue(e.STANDARD["risiko"]["stop_loss_pflicht"])

    def test_mit_standard_aendert_nichts_mehrfach(self):
        a = e.mit_standard({"risiko": {"positionen_max": 7}})
        self.assertEqual(e.mit_standard(a), a)


class KommandozeileTest(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.dir = Path(self._d.name)
        (self.dir / "e.toml").write_text('kursquelle = "wiedergabe"\n')       # keine Verbindung ins Netz
        patch = mock.patch.dict(os.environ, {"KITRADER_DATEN": str(self.dir / "daten"),
                                             "KITRADER_KONFIG": str(self.dir / "e.toml")})
        patch.start()
        self.addCleanup(patch.stop)

    def lauf(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def csv(self, tage=8, name="k.csv"):
        p = self.dir / name
        zeilen = ["t,o,h,l,c,v"] + [f"{k.t.isoformat()},{k.o:.4f},{k.h:.4f},{k.l:.4f},{k.c:.4f},{k.v}"
                                    for k in zufallskerzen(tage=tage, seed=5)]
        p.write_text("\n".join(zeilen) + "\n")
        return str(p)

    def test_konto_positionen_orders_papier(self):
        code, out, _ = self.lauf("konto")
        self.assertEqual(code, 0)
        self.assertIn("10.000,00", out)
        self.assertIn("kein echtes Geld", out)
        self.assertIn("Keine offenen Positionen", self.lauf("positionen")[1])
        self.assertIn("Keine Orders", self.lauf("orders")[1])
        code, out, _ = self.lauf("papier")
        self.assertEqual(code, 0)
        self.assertIn("Papierkonto", out)
        self.assertIn("Noch keine abgeschlossenen Trades", out)

    def test_notaus_und_aufheben(self):
        code, out, _ = self.lauf("notaus")
        self.assertEqual(code, 0)
        self.assertIn("NOT-AUS", out)
        self.assertTrue((self.dir / "daten" / "NOTAUS").exists())
        self.assertEqual(self.lauf("notaus", "--aufheben")[0], 0)
        self.assertFalse((self.dir / "daten" / "NOTAUS").exists())

    def test_notaus_fasst_ohne_alles_nur_eigene_symbole_an(self):
        from kitrader.broker.basis import Order
        from kitrader.broker.papier import PapierBroker
        from kitrader.einstellungen import mit_standard
        from kitrader.journal import Journal
        from .daten import Markt, Uhr
        daten = self.dir / "daten"
        daten.mkdir(parents=True, exist_ok=True)
        (self.dir / "e.toml").write_text('kursquelle = "wiedergabe"\n[autopilot]\nsymbole = ["ABC"]\n')
        uhr = Uhr()
        markt = Markt(uhr)
        for symbol, preis in (("ABC", 100.0), ("FREMD", 50.0), ("JOURNAL", 20.0)):
            markt.setze(symbol, preis - 0.1, preis)
        bauer = lambda *a: PapierBroker(markt, mit_standard({}), daten / "papier.db", uhr=uhr)   # noqa: E731
        b = bauer()
        for symbol, menge, stop in (("ABC", 5, 95.0), ("FREMD", 3, 45.0), ("JOURNAL", 2, 15.0)):
            b.senden(Order(symbol, "kauf", menge, stop_loss=stop))
        b.beenden()
        j = Journal(daten / "journal.db", mit_standard({}))               # vom Autopiloten eröffnet (Neustart mit anderen Symbolen)
        j.eigene_merken("JOURNAL", "papier", "long")
        j.beenden()
        with mock.patch("kitrader.__main__._broker", side_effect=bauer):
            code, out, _ = self.lauf("notaus")
        self.assertEqual(code, 0)
        self.assertIn("glattgestellt: ABC +5", out)
        self.assertIn("glattgestellt: JOURNAL +2", out)
        self.assertIn("fremd, nicht angefasst: Position FREMD +3", out)
        self.assertIn("Fremde Orders und Positionen bleiben unberührt", out)
        self.assertNotIn("ACHTUNG --alles", out)
        b = bauer()
        self.addCleanup(b.beenden)
        self.assertEqual([(p.symbol, p.menge) for p in b.positionen()], [("FREMD", 3)])
        self.assertEqual([o.order.symbol for o in b.orders()], ["FREMD"])
        # --alles: das ganze Konto, mit deutlicher Warnung
        with mock.patch("kitrader.__main__._broker", side_effect=lambda *a: b):
            code, out, _ = self.lauf("notaus", "--alles")
        self.assertEqual(code, 0)
        self.assertIn("ACHTUNG --alles", out)
        self.assertIn("auch solche, die nicht von kitrader stammen", out)
        self.assertIn("glattgestellt: FREMD +3", out)
        self.assertEqual(b.positionen(), [])
        self.assertEqual(b.orders(), [])

    def test_einschaetzung_und_trefferquote(self):
        code, out, _ = self.lauf("einschaetzung", "AAPL", "steigt", "1d", "weil", "--preis", "100")
        self.assertEqual(code, 0)
        code, out, _ = self.lauf("journal", "--trefferquote")
        self.assertEqual(code, 0)
        for wort in ("Horizont", "1h", "1J", "n", "noch offen"):
            self.assertIn(wort, out)
        self.assertIn("Das Journal ist noch leer", self.lauf("journal")[1])

    def test_backtest_mit_testteil(self):
        code, out, _ = self.lauf("backtest", "ausbruch", self.csv(), "--markt", "krypto")
        self.assertEqual(code, 0)
        self.assertIn("TEST, zurückgehalten", out)
        self.assertIn("Anzahl Trades", out)
        self.assertIn("Kein Erfolgsversprechen", out)

    def test_backtest_json_und_ohne_teilung(self):
        import json
        code, out, _ = self.lauf("backtest", "vwap", self.csv(), "--markt", "krypto", "--json")
        self.assertEqual(code, 0)
        self.assertIn("test", json.loads(out[out.index("{"):]))
        code, out, _ = self.lauf("backtest", "vwap", self.csv(), "--markt", "krypto", "--ohne-teilung")
        self.assertIn("ohne zurückgehaltenen Testteil", out)

    def test_backtest_fehler(self):
        code, _, err = self.lauf("backtest", "gibtsnicht", self.csv())
        self.assertEqual(code, 2)
        self.assertIn("Unbekannte Strategie", err)
        code, _, err = self.lauf("backtest", "ausbruch", str(self.dir / "fehlt.csv"))
        self.assertEqual(code, 2)

    def test_echt_wird_ohne_freigabe_verweigert(self):
        code, out, err = self.lauf("autopilot", "--modus", "echt", "--symbole", "AAPL")
        self.assertEqual(code, 2)
        self.assertIn("ECHTGELD VERWEIGERT", err)

    def test_autopilot_wiedergabe_ende_zu_ende(self):
        code, out, err = self.lauf("autopilot", "--wiedergabe", self.csv(tage=4), "--markt", "krypto",
                                   "--symbole", "TEST", "--strategie", "ausbruch")
        self.assertEqual(code, 0, err)
        self.assertIn("BETRIEBSART: PAPIER", out)
        self.assertIn("WIEDERGABE", out)
        self.assertIn("Zwischenstand", out)
        self.assertTrue((self.dir / "daten" / "wiedergabe" / "papier.db").exists())
        self.assertFalse((self.dir / "daten" / "papier.db").exists())          # echtes Papierkonto bleibt unberührt


if __name__ == "__main__":
    unittest.main()
