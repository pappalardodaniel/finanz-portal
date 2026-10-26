"""Papier-Broker: Markt, Limit, Stop, OCO, Short, Gebühren, FIFO. Offline, nur erfundene Kurse."""
import tempfile
import unittest
from pathlib import Path

from kitrader.broker.basis import BrokerFehler, Order
from kitrader.broker.papier import PapierBroker
from kitrader.einstellungen import mit_standard

from .daten import Markt, Uhr


def einst(kosten=None, papier=None):
    return mit_standard({"kosten": {"gebuehr_fest": 1.0, "gebuehr_prozent": 0.0, "schlupf_prozent": 0.0, **(kosten or {})},
                         "papier": papier or {}})


class PapierTest(unittest.TestCase):
    def neu(self, **kw):
        self.uhr = Uhr()
        self.markt = Markt(self.uhr)
        self.markt.setze("ABC", 99.9, 100.0)
        self.b = PapierBroker(self.markt, einst(**kw), ":memory:", uhr=self.uhr)
        self.addCleanup(self.b.beenden)
        return self.b

    def setUp(self):
        self.neu()

    # ---- Marktorders, Schlupf, Gebühren

    def test_marktkauf_zum_ask_mit_gebuehr(self):
        st = self.b.senden(Order("ABC", "kauf", 10))
        self.assertEqual(st.status, "ausgefuehrt")
        self.assertAlmostEqual(st.preis, 100.0)
        self.assertAlmostEqual(self.b.konto().guthaben, 10000 - 1000 - 1)
        p = self.b.positionen()[0]
        self.assertEqual((p.symbol, p.menge, p.einstand), ("ABC", 10, 100.0))
        self.assertAlmostEqual(self.b.konto().gesamtwert, 8999 + 10 * 99.95)

    def test_verkauf_schliesst_und_rechnet_trade(self):
        self.b.senden(Order("ABC", "kauf", 10))
        self.markt.setze("ABC", 105.0, 105.1)
        st = self.b.senden(Order("ABC", "verkauf", 10))
        self.assertAlmostEqual(st.preis, 105.0)
        self.assertEqual(self.b.positionen(), [])
        self.assertAlmostEqual(self.b.konto().guthaben, 8999 + 1050 - 1)
        t = self.b.trades()
        self.assertEqual(len(t), 1)
        self.assertAlmostEqual(t[0]["brutto"], 50.0)
        self.assertAlmostEqual(t[0]["gebuehren"], 2.0)
        self.assertAlmostEqual(t[0]["netto"], 48.0)

    def test_schlupf_macht_marktorders_schlechter(self):
        b = self.neu(kosten={"schlupf_prozent": 1.0})
        self.assertAlmostEqual(b.senden(Order("ABC", "kauf", 1)).preis, 101.0)      # Ask 100 + 1 %
        self.assertAlmostEqual(b.senden(Order("ABC", "verkauf", 1)).preis, 99.9 * 0.99)

    def test_prozentgebuehr(self):
        b = self.neu(kosten={"gebuehr_fest": 0.0, "gebuehr_prozent": 0.1})
        b.senden(Order("ABC", "kauf", 10))                    # Umsatz 1000, 0,1 % = 1,00
        self.assertAlmostEqual(b.konto().guthaben, 10000 - 1000 - 1.0)

    def test_fest_plus_prozent(self):
        b = self.neu(kosten={"gebuehr_fest": 2.0, "gebuehr_prozent": 0.5})
        b.senden(Order("ABC", "kauf", 10))
        self.assertAlmostEqual(b.konto().guthaben, 10000 - 1000 - (2.0 + 5.0))

    # ---- Limit, Stop, Stop-Limit

    def test_limit_kauf_wartet_und_fuellt_beim_aktualisieren(self):
        st = self.b.senden(Order("ABC", "kauf", 10, "limit", limit=98.0))
        self.assertEqual(st.status, "offen")
        self.assertEqual(self.b.aktualisieren(), [])
        self.markt.setze("ABC", 98.5, 98.6)
        self.assertEqual(self.b.aktualisieren(), [])          # Ask 98,6 über Limit 98
        self.markt.setze("ABC", 97.4, 97.5)
        gef = self.b.aktualisieren()
        self.assertEqual([g.id for g in gef], [st.id])
        self.assertAlmostEqual(gef[0].preis, 97.5)            # zum Ask, nicht schlechter als das Limit

    def test_limit_sofort_ausfuehrbar(self):
        st = self.b.senden(Order("ABC", "kauf", 1, "limit", limit=101.0))
        self.assertEqual(st.status, "ausgefuehrt")
        self.assertAlmostEqual(st.preis, 100.0)

    def test_limit_verkauf(self):
        self.b.senden(Order("ABC", "kauf", 10))
        st = self.b.senden(Order("ABC", "verkauf", 10, "limit", limit=103.0))
        self.assertEqual(st.status, "offen")
        self.markt.setze("ABC", 103.4, 103.5)
        self.assertAlmostEqual(self.b.aktualisieren()[0].preis, 103.4)

    def test_stop_verkauf_loest_aus(self):
        self.b.senden(Order("ABC", "kauf", 10))
        st = self.b.senden(Order("ABC", "verkauf", 10, "stop", stop=95.0))
        self.markt.setze("ABC", 96.0, 96.1)
        self.assertEqual(self.b.aktualisieren(), [])
        self.markt.setze("ABC", 94.8, 94.9)
        gef = self.b.aktualisieren()
        self.assertEqual(gef[0].id, st.id)
        self.assertAlmostEqual(gef[0].preis, 94.8)            # zum Markt nach dem Auslösen (Lücke inklusive)
        self.assertEqual(self.b.positionen(), [])

    def test_stop_kauf_loest_aus(self):
        st = self.b.senden(Order("ABC", "kauf", 5, "stop", stop=105.0))
        self.assertEqual(st.status, "offen")
        self.markt.setze("ABC", 105.4, 105.5)
        self.assertAlmostEqual(self.b.aktualisieren()[0].preis, 105.5)

    def test_stop_limit(self):
        self.b.senden(Order("ABC", "kauf", 10))
        st = self.b.senden(Order("ABC", "verkauf", 10, "stop_limit", stop=95.0, limit=94.0))
        self.markt.setze("ABC", 93.0, 93.1)                   # ausgelöst, aber unter dem Limit
        self.assertEqual(self.b.aktualisieren(), [])
        self.markt.setze("ABC", 94.2, 94.3)                   # Auslösung bleibt bestehen, Limit erreicht
        gef = self.b.aktualisieren()
        self.assertEqual(gef[0].id, st.id)
        self.assertAlmostEqual(gef[0].preis, 94.2)

    def test_stornieren(self):
        st = self.b.senden(Order("ABC", "kauf", 10, "limit", limit=90.0))
        self.b.stornieren(st.id)
        self.assertEqual(self.b.orders(), [])
        self.assertEqual(self.b.orders(nur_offene=False)[0].status, "storniert")
        with self.assertRaises(BrokerFehler):
            self.b.stornieren(st.id)
        with self.assertRaises(BrokerFehler):
            self.b.stornieren("gibt-es-nicht")

    # ---- OCO

    def test_oco_take_profit_loescht_stop(self):
        self.b.senden(Order("ABC", "kauf", 10, stop_loss=95.0, take_profit=110.0))
        offene = self.b.orders()
        self.assertEqual(sorted(o.order.art for o in offene), ["limit", "stop"])
        self.assertTrue(all(o.order.seite == "verkauf" and o.order.menge == 10 for o in offene))
        self.markt.setze("ABC", 111.0, 111.1)
        gef = self.b.aktualisieren()
        self.assertEqual(len(gef), 1)
        self.assertAlmostEqual(gef[0].preis, 111.0)
        self.assertEqual(self.b.orders(), [])
        stornierte = [o for o in self.b.orders(nur_offene=False) if o.status == "storniert"]
        self.assertEqual(len(stornierte), 1)
        self.assertIn("OCO", stornierte[0].meldung)
        self.assertAlmostEqual(self.b.trades()[0]["brutto"], 110.0)

    def test_oco_stop_loescht_take_profit(self):
        self.b.senden(Order("ABC", "kauf", 10, stop_loss=95.0, take_profit=110.0))
        self.markt.setze("ABC", 94.0, 94.1)
        gef = self.b.aktualisieren()
        self.assertEqual(len(gef), 1)
        self.assertAlmostEqual(gef[0].preis, 94.0)
        self.assertEqual(self.b.orders(), [])
        self.assertAlmostEqual(self.b.trades()[0]["brutto"], -60.0)

    def test_schutzorders_erst_nach_ausfuehrung_der_eroeffnung(self):
        self.b.senden(Order("ABC", "kauf", 10, "limit", limit=90.0, stop_loss=85.0, take_profit=100.0))
        self.assertEqual(len(self.b.orders()), 1)             # nur die Limit-Order, noch keine Schutzorders
        self.markt.setze("ABC", 89.0, 89.5)
        self.b.aktualisieren()
        self.assertEqual(sorted(o.order.art for o in self.b.orders()), ["limit", "stop"])

    def test_schutzorder_eroeffnet_nie_eine_gegenposition(self):
        self.b.senden(Order("ABC", "kauf", 10, stop_loss=95.0))
        self.b.senden(Order("ABC", "verkauf", 10))            # von Hand glattgestellt, Stop bleibt zurück
        self.markt.setze("ABC", 94.0, 94.1)
        self.assertEqual(self.b.aktualisieren(), [])
        self.assertEqual(self.b.positionen(), [])
        self.assertEqual(self.b.orders(), [])

    # ---- Short

    def test_short_mit_oco_und_gewinn(self):
        st = self.b.senden(Order("ABC", "verkauf", 10, stop_loss=105.0, take_profit=90.0))
        self.assertEqual(st.status, "ausgefuehrt")
        self.assertAlmostEqual(st.preis, 99.9)
        self.assertEqual(self.b.positionen()[0].menge, -10)
        self.assertEqual(sorted((o.order.seite, o.order.art) for o in self.b.orders()), [("kauf", "limit"), ("kauf", "stop")])
        self.markt.setze("ABC", 89.0, 89.1)
        gef = self.b.aktualisieren()
        self.assertAlmostEqual(gef[0].preis, 89.1)            # Rückkauf zum Ask
        t = self.b.trades()[0]
        self.assertEqual(t["richtung"], "short")
        self.assertAlmostEqual(t["brutto"], (99.9 - 89.1) * 10)
        self.assertAlmostEqual(t["netto"], (99.9 - 89.1) * 10 - 2.0)

    def test_short_stop_buy(self):
        self.b.senden(Order("ABC", "verkauf", 10, stop_loss=105.0))
        self.markt.setze("ABC", 106.0, 106.1)
        gef = self.b.aktualisieren()
        self.assertAlmostEqual(gef[0].preis, 106.1)
        self.assertAlmostEqual(self.b.trades()[0]["brutto"], (99.9 - 106.1) * 10)

    def test_short_nicht_erlaubt(self):
        b = self.neu(papier={"short_erlaubt": False})
        st = b.senden(Order("ABC", "verkauf", 1))
        self.assertEqual(st.status, "abgelehnt")
        self.assertIn("Short", st.meldung)
        self.assertEqual(b.positionen(), [])

    def test_kaufkraft(self):
        st = self.b.senden(Order("ABC", "kauf", 200))          # 20.000 bei 10.000 Gesamtwert
        self.assertEqual(st.status, "abgelehnt")
        self.assertIn("Kaufkraft", st.meldung)
        self.assertAlmostEqual(self.b.konto().guthaben, 10000)

    # ---- FIFO

    def test_fifo_ergebnis_je_trade(self):
        self.b.senden(Order("ABC", "kauf", 10))                        # 10 @ 100
        self.markt.setze("ABC", 109.9, 110.0)
        self.b.senden(Order("ABC", "kauf", 10))                        # 10 @ 110
        self.markt.setze("ABC", 120.0, 120.1)
        self.b.senden(Order("ABC", "verkauf", 15))                     # 15 @ 120
        t = self.b.trades()
        self.assertEqual(len(t), 2)
        # erste 10 aus dem ersten Kauf: Brutto 200, Gebühren 1 (Einstieg) + 1 * 10/15 (Ausstieg)
        self.assertAlmostEqual(t[0]["menge"], 10)
        self.assertAlmostEqual(t[0]["brutto"], 200.0)
        self.assertAlmostEqual(t[0]["gebuehren"], 1 + 10 / 15)
        # 5 aus dem zweiten Kauf: Brutto 50, Gebühren 0,5 + 1 * 5/15
        self.assertAlmostEqual(t[1]["menge"], 5)
        self.assertAlmostEqual(t[1]["brutto"], 50.0)
        self.assertAlmostEqual(t[1]["gebuehren"], 0.5 + 5 / 15)
        self.assertAlmostEqual(sum(x["netto"] for x in t), 250 - 3 + 0.5)   # 0,5 Einstiegsgebühr steckt noch im Rest
        p = self.b.positionen()[0]
        self.assertEqual((p.menge, p.einstand), (5, 110.0))

    def test_positionsumkehr_long_zu_short(self):
        self.b.senden(Order("ABC", "kauf", 10))
        self.markt.setze("ABC", 110.0, 110.1)
        self.b.senden(Order("ABC", "verkauf", 15))
        p = self.b.positionen()[0]
        self.assertEqual((p.menge, p.einstand), (-5, 110.0))
        self.assertEqual(len(self.b.trades()), 1)

    # ---- Sonstiges

    def test_ohne_kurs_wird_nichts_angenommen(self):
        with self.assertRaises(BrokerFehler):
            self.b.senden(Order("XYZ", "kauf", 1))
        self.assertEqual(self.b.orders(nur_offene=False), [])

    def test_konto_nutzt_gespeicherten_kurs_wenn_quelle_ausfaellt(self):
        self.b.senden(Order("ABC", "kauf", 10))
        self.markt.kurse.clear()          # zuletzt bekannter Kurs ist der Ausführungskurs 100
        self.assertAlmostEqual(self.b.konto().gesamtwert, 8999 + 10 * 100.0)

    def test_zustand_ueberlebt_neustart(self):
        with tempfile.TemporaryDirectory() as d:
            pfad = Path(d) / "papier.db"
            a = PapierBroker(self.markt, einst(), pfad, uhr=self.uhr)
            a.senden(Order("ABC", "kauf", 10, stop_loss=95.0))
            a.beenden()
            b = PapierBroker(self.markt, einst(), pfad, uhr=self.uhr)
            self.assertEqual(b.positionen()[0].menge, 10)
            self.assertAlmostEqual(b.konto().guthaben, 8999)
            self.assertEqual(len(b.orders()), 1)
            b.beenden()

    def test_alles_schliessen(self):
        self.b.senden(Order("ABC", "kauf", 10, stop_loss=95.0))
        self.b.senden(Order("ABC", "kauf", 5, "limit", limit=80.0))
        schritte = self.b.alles_schliessen()
        self.assertTrue(any("glattgestellt" in s for s in schritte))
        self.assertEqual(self.b.orders(), [])
        self.assertEqual(self.b.positionen(), [])

    def test_alles_schliessen_nur_fuer_die_genannten_symbole(self):
        self.markt.setze("XYZ", 49.9, 50.0)
        self.b.senden(Order("ABC", "kauf", 10, stop_loss=95.0))
        self.b.senden(Order("XYZ", "kauf", 4, stop_loss=45.0))
        schritte = self.b.alles_schliessen(symbole=["abc"])               # Schreibweise egal (symbol_schluessel)
        self.assertEqual([(p.symbol, p.menge) for p in self.b.positionen()], [("XYZ", 4)])
        self.assertEqual([o.order.symbol for o in self.b.orders()], ["XYZ"])      # sein Stop bleibt stehen
        self.assertTrue(any(x.startswith("glattgestellt: ABC") for x in schritte), schritte)
        self.assertIn("fremd, nicht angefasst: Position XYZ +4", schritte)
        self.assertTrue(any(x.startswith("fremd, nicht angefasst: Order") and "XYZ" in x for x in schritte), schritte)
        self.assertFalse(any(x.startswith("FEHLER") for x in schritte))       # "fremd" ist kein Fehler: Not-Aus gilt als erledigt

    def test_alles_schliessen_ohne_symbole_gilt_fuer_das_ganze_konto(self):
        self.markt.setze("XYZ", 49.9, 50.0)
        self.b.senden(Order("ABC", "kauf", 10))
        self.b.senden(Order("XYZ", "kauf", 4))
        self.b.alles_schliessen()
        self.assertEqual(self.b.positionen(), [])
        self.b.senden(Order("ABC", "kauf", 10))
        self.assertEqual(self.b.alles_schliessen(symbole=[])[0:1], ["fremd, nicht angefasst: Position ABC +10"])   # leere Liste = nichts eigenes

    def test_tagesorder_verfaellt_gtc_nicht(self):
        a = self.b.senden(Order("ABC", "kauf", 1, "limit", limit=50.0))
        g = self.b.senden(Order("ABC", "kauf", 1, "limit", limit=50.0, gueltig="gtc"))
        self.uhr.weiter(days=1)
        self.b.aktualisieren()
        status = {o.id: o.status for o in self.b.orders(nur_offene=False)}
        self.assertEqual((status[a.id], status[g.id]), ("storniert", "offen"))

    def test_tagesergebnis(self):
        self.b.senden(Order("ABC", "kauf", 10))
        self.uhr.weiter(days=1)
        k = self.b.konto()            # neuer Tag: Startwert 9999 (Bargeld 8999 + 10 x letzter Kurs 100)
        self.assertAlmostEqual(k.tagesergebnis, 8999 + 10 * 99.95 - 9999)
        self.markt.setze("ABC", 119.9, 120.0)
        self.assertAlmostEqual(self.b.konto().tagesergebnis, 8999 + 10 * 119.95 - 9999)

    def test_zuruecksetzen(self):
        self.b.senden(Order("ABC", "kauf", 10))
        self.b.zuruecksetzen()
        self.assertEqual(self.b.positionen(), [])
        self.assertAlmostEqual(self.b.konto().guthaben, 10000)



class GleichzeitigerZugriffTest(unittest.TestCase):
    """Oberfläche, Telegram und Autopilot öffnen dieselbe papier.db in eigenen Verbindungen. Während der eine Kurse holt
    (Netz), schreibt der andere (Kauf, Verkauf, Not-Aus). Das darf weder einen halben Stand anzeigen noch die Schreiber sperren."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pfad = Path(self.tmp.name) / "papier.db"
        self.uhr = Uhr()
        self.markt = Markt(self.uhr)
        self.markt.setze("ABC", 99.9, 100.0)
        self.markt.setze("DEF", 49.9, 50.0)
        self.schreiber = PapierBroker(self.markt, einst(), self.pfad, uhr=self.uhr)
        self.addCleanup(self.schreiber.beenden)
        self.schreiber.db.execute("PRAGMA busy_timeout=100")          # eine Sperre soll hier schnell auffallen
        self.schreiber.senden(Order("ABC", "kauf", 10))

    def leser(self, beim_kurs):
        """Leser, dessen Kursquelle bei jedem Abruf erst `beim_kurs(symbol)` ausführt (das Netz dauert, der andere schreibt)."""
        def kurs(symbol):
            beim_kurs(symbol)
            return self.markt(symbol)
        leser = PapierBroker(kurs, einst(), self.pfad, uhr=self.uhr)
        self.addCleanup(leser.beenden)
        return leser

    def test_konto_zeigt_keinen_halben_stand_wenn_mitten_in_der_bewertung_verkauft_wird(self):
        vorher = self.schreiber.konto().gesamtwert                    # 8999 + 10 * 99,95
        getan = []

        def verkaufen(symbol):
            if not getan:
                getan.append(self.schreiber.senden(Order("ABC", "verkauf", 10)))

        wert = self.leser(verkaufen).konto().gesamtwert
        self.assertEqual(len(getan), 1)
        nachher = self.schreiber.konto().gesamtwert                   # 8999 + 999 - 1
        self.assertAlmostEqual(vorher, 9998.5)
        self.assertAlmostEqual(nachher, 9997.0)
        # entweder der Stand vor oder der nach dem Verkauf, aber nie Erlös und Position zugleich (rund 10.996)
        self.assertTrue(abs(wert - vorher) < 0.01 or abs(wert - nachher) < 0.01, f"Gesamtwert {wert}")

    def test_lesen_haelt_waehrend_der_kursabfrage_keine_schreibsperre(self):
        self.schreiber.senden(Order("DEF", "kauf", 10))
        fehler = []

        def schreiben(symbol):
            if symbol == "DEF":                                       # der erste Kurs (ABC) ist schon geholt und gespeichert
                try:
                    self.schreiber.senden(Order("ABC", "verkauf", 10))
                except Exception as e:                                # sqlite3.OperationalError: database is locked
                    fehler.append(e)

        leser = self.leser(schreiben)
        leser.konto()
        self.assertEqual(fehler, [])
        fehler.clear()
        self.schreiber.senden(Order("ABC", "kauf", 10))
        leser.positionen()
        self.assertEqual(fehler, [])


if __name__ == "__main__":
    unittest.main()
