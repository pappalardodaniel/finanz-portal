"""Brücke vom Markt-Dashboard zu den Handelsteilen (markt/dashboard/kbruecke.py): Lesen, Fehler, Autopilot-Steuerung nur im Papierbetrieb."""
import tempfile
import unittest
from pathlib import Path

from kitrader.broker.basis import Order
from kitrader.broker.papier import PapierBroker
from kitrader.einstellungen import mit_standard
from kitrader.markt.dashboard.kbruecke import Bruecke
from kitrader.oberflaeche.server import Anfragefehler


class FalscheSteuerung:
    """Ersetzt AutopilotSteuerung: merkt sich nur, was verlangt wurde."""
    modus = "papier"

    def __init__(self, einst):
        self.einst, self.aufrufe = einst, []

    def status(self):
        return {"laeuft": bool(self.aufrufe) and self.aufrufe[-1] == "start", "status": "nicht gestartet", "fehler": "", "neustarts": 0}

    def starten(self):
        self.aufrufe.append("start")
        return {**self.status(), "hinweis": "gestartet"}

    def stoppen(self):
        self.aufrufe.append("stopp")
        return {**self.status(), "hinweis": "gestoppt"}


class BrueckeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ordner = Path(self.tmp.name)
        self.einst = mit_standard({"kursquelle": "wiedergabe", "watchlist": {"werte": ["BTC/EUR", "ETH/EUR"]}, "autopilot": {"symbole": ["BTC/EUR"]}})
        self.b = Bruecke(self.einst, self.ordner, steuerung=FalscheSteuerung(self.einst))

    def test_info_nennt_betriebsart_und_watchlist(self):
        i = self.b.get("info", {})
        self.assertEqual((i["modus"], i["modus_text"], i["echtes_geld"]), ("papier", "PAPIER", False))
        self.assertEqual(i["watchlist"], ["BTC/EUR", "ETH/EUR"])

    def test_leere_daten_sind_kein_fehler(self):
        t = self.b.get("trends", {})
        self.assertFalse(t["welt"]["vorhanden"])
        self.assertFalse(t["radar"]["vorhanden"])
        self.assertIn("python3 -m kitrader", t["welt"]["hinweis"])
        w = self.b.get("weltlage", {})
        self.assertFalse(w["vorhanden"])
        self.assertFalse(self.b.get("morgenlage", {})["vorhanden"])

    def test_autopilot_status_mit_grenzen_und_entscheidungen(self):
        a = self.b.get("autopilot", {})
        self.assertEqual(a["modus"], "papier")
        self.assertTrue(a["steuerbar"])
        self.assertEqual(a["symbole"], ["BTC/EUR"])
        self.assertIn("Tagesverlust", [g["name"] for g in a["uebersicht"]["grenzen"]])
        self.assertEqual(a["entscheidungen"], [])
        self.assertFalse(a["uebersicht"]["notaus"]["aktiv"])

    def test_start_und_stopp_gehen_an_die_steuerung(self):
        self.assertEqual(self.b.post("autopilot/start", {})["hinweis"], "gestartet")
        self.assertEqual(self.b.post("autopilot/stopp", {})["hinweis"], "gestoppt")
        self.assertEqual(self.b.steuerung.aufrufe, ["start", "stopp"])

    def test_ohne_papierbetrieb_kein_start_von_hier(self):
        e = mit_standard({**self.einst, "modus": "demo"})
        b = Bruecke(e, self.ordner)
        with self.assertRaises(Anfragefehler) as f:
            b.post("autopilot/start", {})
        self.assertEqual(f.exception.status, 403)
        self.assertIn("nur im Papierbetrieb", f.exception.text)
        self.assertFalse(b.get("info", {})["autopilot_steuerbar"])

    def test_aehnlich_braucht_ein_thema_und_unbekanntes_ist_404(self):
        with self.assertRaises(Anfragefehler) as f:
            self.b.get("aehnlich", {})
        self.assertEqual(f.exception.status, 400)
        with self.assertRaises(Anfragefehler) as f:
            self.b.get("gibt-es-nicht", {})
        self.assertEqual(f.exception.status, 404)
        with self.assertRaises(Anfragefehler) as f:
            self.b.post("order", {})
        self.assertEqual(f.exception.status, 404)

    def test_symbol_wird_erkannt(self):
        r = self.b.get("symbol", {"q": ["Bitcoin"]})
        self.assertTrue(r["erkannt"])
        self.assertEqual(r["symbol"], "BTC/EUR")
        r = self.b.get("symbol", {"q": ["a;b"]})
        self.assertFalse(r["erkannt"])

    def test_historie_ereignisse_sind_da(self):
        e = self.b.get("historie/ereignisse", {})
        self.assertGreater(len(e["ereignisse"]), 100)

    # ---- Ereignis-Marker im Chart

    def test_chart_ereignisse_nur_tag_und_monat_mit_rang_und_reihe(self):
        d = self.b.get("chart/ereignisse", {"symbol": ["BTCUSDT"], "art": ["krypto"], "von": ["2020-01-01"], "bis": ["2020-12-31"]})
        self.assertGreater(len(d["ereignisse"]), 3)
        for e in d["ereignisse"]:
            self.assertIn(e["genauigkeit"], ("tag", "monat"))
            self.assertTrue("2020-01-01" <= e["beginn"] <= "2020-12-31")
            self.assertIn("name", e["quelle"])
        corona = next(e for e in d["ereignisse"] if e["art"] == "boersencrash")
        self.assertLess(corona["rang"], next(e for e in d["ereignisse"] if e["art"] == "wahl")["rang"] if any(e["art"] == "wahl" for e in d["ereignisse"]) else 99)
        self.assertEqual(d["reihe"]["id"], "btc")
        self.assertTrue(d["reihe"]["eigene"])
        self.assertFalse(d["reihe"]["geladen"])                   # leerer Datenordner: Hinweis statt Fehler
        self.assertIn("kitrader.historie laden btc", d["reihe"]["hinweis"])

    def test_chart_reihe_eigene_oder_btc_bzw_dow(self):
        r = lambda sym, art: self.b.chart_reihe(sym, art)
        self.assertEqual((r("ETHUSDT", "krypto")["id"], r("ETHUSDT", "krypto")["eigene"]), ("btc", False))
        self.assertEqual((r("BTC/EUR", "krypto")["id"], r("BTC/EUR", "krypto")["eigene"]), ("btc", True))
        self.assertEqual((r("SAP", "aktie")["id"], r("SAP", "aktie")["eigene"]), ("dow", False))
        self.assertEqual((r("SPY", "aktie")["id"], r("GOLD", "aktie")["id"]), ("shiller_sp", "gold"))
        self.assertIn("ersatzweise", r("SAP", "aktie")["hinweis"])

    def test_chart_ereignisse_prueft_datum(self):
        with self.assertRaises(Anfragefehler) as f:
            self.b.get("chart/ereignisse", {"von": ["gestern"]})
        self.assertEqual(f.exception.status, 400)

    def test_chart_studie_ohne_geladene_reihe_gibt_ereignis_und_erklaerung(self):
        d = self.b.get("chart/studie", {"id": ["2020-corona-crash"], "symbol": ["BTCUSDT"], "art": ["krypto"]})
        self.assertEqual(d["ereignis"]["id"], "2020-corona-crash")
        self.assertTrue(d["ereignis"]["beschreibung"])
        self.assertIsNone(d["studie"])
        self.assertIn("nicht geladen", d["fehler"])
        with self.assertRaises(Anfragefehler) as f:
            self.b.get("chart/studie", {"id": ["gibt-es-nicht"]})
        self.assertEqual(f.exception.status, 404)

    # ---- Depot: Papierkonto des Autopiloten

    def test_depot_zeigt_konto_positionen_orders_und_trades_des_autopiloten(self):
        from tests.kitrader.daten import Markt, Uhr
        uhr = Uhr(); markt = Markt(uhr)
        markt.setze("ETH/EUR", 1999.0, 2000.0); markt.setze("BTC/EUR", 69990.0, 70000.0)
        bauer = lambda: PapierBroker(markt, self.einst, self.ordner / "papier.db", uhr=uhr)
        b = bauer()
        b.senden(Order("ETH/EUR", "kauf", 1.0, stop_loss=1900.0, take_profit=2200.0))
        markt.setze("ETH/EUR", 2209.0, 2210.0)
        b.aktualisieren()                                          # Ziel erreicht: ein abgeschlossener Trade
        b.senden(Order("BTC/EUR", "kauf", 0.05, stop_loss=66000.0, take_profit=76000.0))
        b.beenden()
        d = Bruecke(self.einst, self.ordner, broker_bauer=bauer).get("depot", {})
        self.assertTrue(d["vorhanden"])
        self.assertEqual((d["waehrung"], d["start"]), ("EUR", self.einst["papier"]["startguthaben"]))
        self.assertEqual([p["symbol"] for p in d["positionen"]], ["BTC/EUR"])
        self.assertEqual(sorted(o["art"] for o in d["orders"]), ["limit", "stop"])       # Schutz-Stop und Ziel der offenen Position
        self.assertEqual(len(d["trades"]), 1)
        self.assertEqual(d["trades"][0]["symbol"], "ETH/EUR")
        self.assertGreater(d["trades"][0]["netto"], 0)
        self.assertAlmostEqual(d["ergebnis"]["realisiert"], d["trades"][0]["netto"])
        self.assertEqual(d["statistik"], {"trades": 1, "gewinner": 1, "verlierer": 0, "trefferquote": 1.0})
        self.assertAlmostEqual(d["ergebnis"]["gesamt"], d["konto"]["gesamtwert"] - d["start"])

    def test_depot_ohne_konto_ist_leer_aber_kein_fehler(self):
        d = self.b.get("depot", {})
        self.assertEqual((d["positionen"], d["orders"], d["trades"]), ([], [], []))
        self.assertEqual(d["statistik"]["trades"], 0)
        self.assertIsNone(d["statistik"]["trefferquote"])


if __name__ == "__main__":
    unittest.main()
