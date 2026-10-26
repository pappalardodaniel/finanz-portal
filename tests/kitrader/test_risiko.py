"""Risikoregeln: jede Regel einzeln, bestanden und abgelehnt. Reine Funktionen, offline."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kitrader.broker.basis import Kurs, Order, OrderStand
from kitrader.einstellungen import mit_standard
from kitrader.risiko import (Lage, ausstehende_eroeffnungen, handelstag, ist_eroeffnung, markt_status, markt_von_symbol,
                             mengenschritt, pruefen, positionsgroesse, tagesverlust_erreicht, tagesverlust_grenze)

UTC = timezone.utc
JETZT = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)       # Montag, 10:00 in New York (Winterzeit)


def einst(risiko=None, **rest):
    return mit_standard({"risiko": {"standard_markt": "krypto", **(risiko or {})}, **rest})


def lage(risiko=None, kurs_alter=0, **kw):
    teil = {"jetzt": JETZT, "gesamtwert": 10000.0, "einstellungen": einst(risiko), "symbol": "ABC",
            "kurs": Kurs("ABC", 99.9, 100.0, 99.95, JETZT - timedelta(seconds=kurs_alter))}
    teil.update(kw)
    return Lage(**teil)


def kauf(**kw):
    teil = {"menge": 9, "stop_loss": 98.0}
    teil.update(kw)
    return Order("ABC", "kauf", teil.pop("menge"), **teil)


def offen(symbol, seite, menge, art="limit", status="offen", ausgefuehrt=0.0, **kw):
    """Eine beim Broker offene, noch nicht (ganz) ausgeführte Order."""
    return OrderStand("X1", Order(symbol, seite, menge, art, **kw), status, ausgefuehrt)


class RegelTest(unittest.TestCase):
    def ok(self, order, l):
        e = pruefen(order, l)
        self.assertTrue(e.ok, e.gruende)

    def abgelehnt(self, order, l, stichwort):
        e = pruefen(order, l)
        self.assertFalse(e.ok, "sollte abgelehnt werden")
        self.assertTrue(any(stichwort in g for g in e.gruende), f"{stichwort!r} nicht in {e.gruende}")

    def test_grundfall_besteht(self):
        e = pruefen(kauf(), lage())
        self.assertTrue(e.ok)
        self.assertEqual(e.gruende, [])

    # ---- Tagesverlust

    def test_tagesverlust_absolut(self):
        self.ok(kauf(), lage(tagesergebnis=-99.0))
        self.abgelehnt(kauf(), lage(tagesergebnis=-100.0), "Tagesverlustgrenze")

    def test_tagesverlust_prozent(self):
        # 1 % von 5000 = 50 liegt unter der absoluten Grenze 100
        l = lage(gesamtwert=5000.0, tagesergebnis=-50.0)
        self.assertAlmostEqual(tagesverlust_grenze(l), 50.0)
        self.assertTrue(tagesverlust_erreicht(l))
        self.abgelehnt(kauf(menge=4), l, "Tagesverlustgrenze")
        self.ok(kauf(menge=4), lage(gesamtwert=5000.0, tagesergebnis=-49.0))

    def test_tagesverlust_sperre_bleibt_bis_zum_naechsten_tag(self):
        self.abgelehnt(kauf(), lage(tagesergebnis=0.0, tag_gesperrt=True), "heute schon erreicht")

    def test_schliessen_trotz_tagesverlust_erlaubt(self):
        l = lage(tagesergebnis=-500.0, tag_gesperrt=True, positionen={"ABC": 9})
        self.ok(Order("ABC", "verkauf", 9), l)

    # ---- Positionsgröße, Anzahl, Orders je Tag

    def test_positionsgroesse_maximal(self):
        self.ok(kauf(menge=10, stop_loss=99.0), lage())                    # 1000 = genau 10 %
        self.abgelehnt(kauf(menge=11, stop_loss=99.5), lage(), "Position")

    def test_position_zaehlt_bestehende_menge_mit(self):
        self.abgelehnt(kauf(menge=5, stop_loss=99.0), lage(positionen={"ABC": 6}), "Position")

    def test_positionen_max(self):
        voll = {"AAA": 1, "BBB": 1, "CCC": -1}
        self.abgelehnt(kauf(), lage(positionen=voll), "offene Positionen")
        self.ok(kauf(), lage(positionen={"AAA": 1, "BBB": 1}))
        self.ok(kauf(menge=1, stop_loss=99.0), lage(positionen={**voll, "ABC": 1}))   # Aufstocken ändert die Anzahl nicht

    def test_orders_max_pro_tag(self):
        self.ok(kauf(), lage(orders_heute=19))
        self.abgelehnt(kauf(), lage(orders_heute=20), "Orders heute")

    # ---- Stop-Loss, Risiko je Trade

    def test_stop_loss_pflicht(self):
        self.abgelehnt(Order("ABC", "kauf", 9), lage(), "Stop-Loss fehlt")
        self.ok(Order("ABC", "kauf", 9), lage(risiko={"stop_loss_pflicht": False}))

    def test_stop_auf_falscher_seite(self):
        self.abgelehnt(kauf(stop_loss=102.0), lage(), "falschen Seite")

    def test_risiko_je_trade(self):
        # erlaubt: 0,5 % von 10.000 = 50. Bei Stop 95 und 9 Stück: 45. Bei Stop 94: 54.
        self.ok(kauf(stop_loss=95.0), lage())
        self.abgelehnt(kauf(stop_loss=94.0), lage(), "Risiko je Trade")

    def test_risiko_je_trade_rechnet_mit_limitpreis(self):
        o = Order("ABC", "kauf", 9, "limit", limit=99.0, stop_loss=94.0)    # Abstand 5 x 9 = 45
        self.ok(o, lage())

    # ---- Short

    def test_short_nur_wenn_erlaubt(self):
        verkauf = Order("ABC", "verkauf", 9, stop_loss=102.0)
        self.abgelehnt(verkauf, lage(), "Short")
        self.ok(verkauf, lage(risiko={"short_erlaubt": True}))

    def test_verkauf_einer_long_position_ist_kein_short(self):
        self.ok(Order("ABC", "verkauf", 9), lage(positionen={"ABC": 9}))

    def test_umkehr_long_zu_short_ist_eine_eroeffnung(self):
        o = Order("ABC", "verkauf", 10, stop_loss=102.0)
        l = lage(positionen={"ABC": 4})
        self.assertTrue(ist_eroeffnung(o, l))
        self.abgelehnt(o, l, "Short")

    def test_short_stop_muss_ueber_dem_kurs_liegen(self):
        l = lage(risiko={"short_erlaubt": True})
        self.abgelehnt(Order("ABC", "verkauf", 9, stop_loss=98.0), l, "falschen Seite")

    def test_take_profit_muss_auf_der_gewinnseite_liegen(self):
        # Long bei Ask 100: Ziel 90 würde sofort als Verkauf mit Verlust ausgeführt (Fehler vorher: kein Befund)
        self.abgelehnt(kauf(take_profit=90.0), lage(), "Take-Profit")
        self.abgelehnt(kauf(take_profit=100.0), lage(), "Take-Profit")
        self.ok(kauf(take_profit=104.0), lage())
        # Short bei Bid 99,9: Ziel darüber ist falsch
        l = lage(risiko={"short_erlaubt": True})
        self.abgelehnt(Order("ABC", "verkauf", 9, stop_loss=102.0, take_profit=110.0), l, "Take-Profit")
        self.ok(Order("ABC", "verkauf", 9, stop_loss=102.0, take_profit=95.0), l)

    def test_take_profit_wird_beim_schliessen_nicht_geprueft(self):
        self.ok(Order("ABC", "verkauf", 9, take_profit=1.0), lage(positionen={"ABC": 9}))

    def test_nan_stop_loss_oder_preis_wird_abgelehnt(self):
        # Mit NaN sind alle Vergleiche falsch: früher ging so eine Order durch die Prüfung.
        nan = float("nan")
        self.abgelehnt(kauf(stop_loss=nan), lage(), "keine gültige Zahl")
        self.abgelehnt(kauf(take_profit=nan), lage(), "keine gültige Zahl")
        kaputt = Kurs("ABC", nan, nan, nan, JETZT)
        self.abgelehnt(kauf(), lage(kurs=kaputt), "kein Preis")

    def test_symbol_unter_anderem_namen_des_brokers(self):
        # Kraken meldet "XBTEUR", in den Einstellungen steht "BTC/EUR": Position, Markt und Mengenschritt müssen passen.
        l = lage(positionen={"XBTEUR": 0.5})
        self.assertEqual(l.menge("BTC/EUR"), 0.5)
        self.assertEqual(l.menge("btceur"), 0.5)
        self.assertEqual(l.menge("ETHEUR"), 0.0)
        e = mit_standard({"maerkte": {"BTC/EUR": "krypto"}, "mengenschritte": {"BTC/EUR": 0.0001},
                          "risiko": {"standard_markt": "us"}})
        self.assertEqual(markt_von_symbol("XBTEUR", e), "krypto")
        self.assertEqual(markt_von_symbol("AAPL", e), "us")
        self.assertEqual(mengenschritt("XBTEUR", e), 0.0001)

    # ---- Kurs

    def test_veralteter_kurs(self):
        self.ok(kauf(), lage(kurs_alter=60))
        self.abgelehnt(kauf(), lage(kurs_alter=61), "veraltet")

    def test_veralteter_kurs_sperrt_auch_das_schliessen(self):
        self.abgelehnt(Order("ABC", "verkauf", 9), lage(kurs_alter=300, positionen={"ABC": 9}), "veraltet")

    def test_ohne_kurs_kein_handel(self):
        self.abgelehnt(kauf(), lage(kurs=None), "kein Kurs")

    # ---- Kurszeit unbekannt

    def test_kurs_ohne_echte_zeit_erlaubt_keine_eroeffnung(self):
        # Quelle ohne Zeitstempel (IBKR ohne "_updated"): die Abrufzeit sieht frisch aus, das Alter ist aber unbekannt.
        geschaetzt = Kurs("ABC", 99.9, 100.0, 99.95, JETZT, zeit_geschaetzt=True)
        self.abgelehnt(kauf(), lage(kurs=geschaetzt), "Kurszeit unbekannt")
        self.ok(Order("ABC", "verkauf", 9), lage(kurs=geschaetzt, positionen={"ABC": 9}))     # Schließen bleibt erlaubt

    def test_kurs_mit_echter_zeit_und_alte_kurse_ohne_kennzeichen_bleiben_wie_bisher(self):
        self.assertFalse(Kurs("ABC", 99.9, 100.0, 99.95, JETZT).zeit_geschaetzt)               # rückwärtsverträglich
        self.ok(kauf(), lage())

    # ---- offene, noch nicht ausgeführte Eröffnungsorders

    def test_offene_eroeffnungsorder_zaehlt_bei_der_positionsgroesse(self):
        l = lage(offene_orders=[offen("ABC", "kauf", 6, limit=90.0)])
        self.abgelehnt(kauf(menge=5, stop_loss=99.0), l, "schon offen bestellt")             # (6 + 5) x 100 = 1100 > 1000
        self.ok(kauf(menge=4, stop_loss=99.0), l)                                            # genau 10 %
        self.ok(kauf(menge=5, stop_loss=99.0), lage())                                       # ohne offene Order erlaubt

    def test_offene_eroeffnungsorder_zaehlt_beim_risiko(self):
        # 0,5 % von 10.000 = 50; Abstand 2: höchstens 25 Stück in Summe
        l = lage(risiko={"position_max_prozent": 100}, offene_orders=[offen("ABC", "kauf", 15, limit=90.0)])
        self.abgelehnt(kauf(menge=11, stop_loss=98.0), l, "Risiko je Trade")
        self.ok(kauf(menge=10, stop_loss=98.0), l)

    def test_offene_eroeffnungsorder_zaehlt_bei_der_anzahl_positionen(self):
        voll = {"AAA": 1, "BBB": 1}
        self.abgelehnt(kauf(), lage(positionen=voll, offene_orders=[offen("CCC", "kauf", 1, limit=5.0)]), "offene Positionen")
        self.ok(kauf(), lage(positionen=voll))
        # die eigene offene Order zählt nicht gegen das eigene Symbol (die Größe prüft die Menge)
        self.ok(kauf(menge=1, stop_loss=99.0), lage(positionen=voll, offene_orders=[offen("ABC", "kauf", 1, limit=90.0)]))

    def test_schutzorders_und_ziele_gegen_eine_position_zaehlen_nicht(self):
        schutz = [offen("ABC", "verkauf", 9, "stop", stop=95.0), offen("ABC", "verkauf", 9, limit=110.0)]
        self.assertEqual(ausstehende_eroeffnungen(lage(positionen={"ABC": 9}, offene_orders=schutz)), {})
        # Short-Position: Stop und Ziel sind Käufe
        short = [offen("ABC", "kauf", 9, "stop", stop=105.0), offen("ABC", "kauf", 9, limit=90.0)]
        self.assertEqual(ausstehende_eroeffnungen(lage(positionen={"ABC": -9}, offene_orders=short)), {})
        # dieselbe Stop-Order ohne Position wäre ein Short: sie zählt als Eröffnung (auch bei der Anzahl der Positionen)
        waise = [offen("ZZZ", "verkauf", 9, "stop", stop=95.0)]
        self.assertEqual(ausstehende_eroeffnungen(lage(offene_orders=waise)), {"ZZZ": {"kauf": 0.0, "verkauf": 9.0}})
        self.abgelehnt(kauf(), lage(positionen={"AAA": 1, "BBB": 1}, offene_orders=waise), "offene Positionen")

    def test_nur_offene_rest_menge_zaehlt(self):
        teil = offen("ABC", "kauf", 10, status="teilweise", ausgefuehrt=4.0, limit=90.0)
        fertig = [offen("ABC", "kauf", 10, status="ausgefuehrt", ausgefuehrt=10.0, limit=90.0),
                  offen("ABC", "kauf", 10, status="storniert", limit=90.0)]
        self.assertEqual(ausstehende_eroeffnungen(lage(offene_orders=[teil] + fertig)), {"ABC": {"kauf": 6.0, "verkauf": 0.0}})

    def test_offene_orders_nicht_lesbar_keine_eroeffnung_aber_schliessen_geht(self):
        self.abgelehnt(kauf(), lage(orders_unbekannt=True), "nicht lesbar")
        self.ok(Order("ABC", "verkauf", 9), lage(orders_unbekannt=True, positionen={"ABC": 9}))

    # ---- Währung

    def test_fremde_waehrung_ohne_wechselkurs_keine_eroeffnung(self):
        usd = Kurs("ABC", 99.9, 100.0, 99.95, JETZT, waehrung="USD")
        self.abgelehnt(kauf(), lage(kurs=usd, konto_waehrung="EUR", wechselkurs=None), "kein Wechselkurs USD -> EUR")
        self.abgelehnt(kauf(), lage(kurs=usd, konto_waehrung="EUR", wechselkurs=float("nan")), "kein Wechselkurs")
        self.ok(Order("ABC", "verkauf", 9), lage(kurs=usd, konto_waehrung="EUR", wechselkurs=None, positionen={"ABC": 9}))
        self.ok(kauf(), lage(kurs=usd, konto_waehrung="EUR", wechselkurs=0.9))

    def test_wechselkurs_rechnet_positionsgroesse_und_risiko_um(self):
        usd = Kurs("ABC", 99.9, 100.0, 99.95, JETZT, waehrung="USD")
        zwei = dict(kurs=usd, konto_waehrung="EUR", wechselkurs=2.0)          # 1 USD = 2 EUR
        self.ok(kauf(menge=5, stop_loss=99.0), lage(**zwei))                  # 5 x 100 x 2 = 1000 = 10 %
        self.abgelehnt(kauf(menge=6, stop_loss=99.0), lage(**zwei), "Position")
        self.ok(kauf(menge=6, stop_loss=99.0), lage(kurs=usd, konto_waehrung="EUR", wechselkurs=1.0))    # ohne Umrechnung wäre 6 nur knapp zu viel
        # Risiko: Abstand 2 USD = 4 EUR je Stück, erlaubt 50 EUR -> höchstens 12 Stück
        weit = dict(zwei, risiko={"position_max_prozent": 100})
        self.ok(kauf(menge=12, stop_loss=98.0), lage(**weit))
        self.abgelehnt(kauf(menge=13, stop_loss=98.0), lage(**weit), "Risiko je Trade")
        # ein schwacher USD (0,5 EUR): dieselbe Menge ist weniger riskant
        schwach = dict(weit, wechselkurs=0.5)
        self.ok(kauf(menge=25, stop_loss=98.0), lage(**dict(schwach, risiko={"position_max_prozent": 100})))

    def test_ohne_waehrungsangabe_gilt_die_kontowaehrung(self):
        self.ok(kauf(), lage(konto_waehrung="EUR"))                           # Kurs.waehrung leer, wechselkurs Standard 1.0

    # ---- Handelszeiten

    def zeiten(self):
        return {"maerkte": {"ABC": "boerse"},
                "handelszeiten": {"boerse": {"zeitzone": "UTC", "von": "09:00", "bis": "17:00", "tage": [0, 1, 2, 3, 4]}}}

    def lage_zu(self, zeit):
        e = mit_standard({"risiko": {"standard_markt": "krypto"}, **self.zeiten()})
        return lage(jetzt=zeit, einstellungen=e, kurs=Kurs("ABC", 99.9, 100.0, 99.95, zeit))

    def test_handelszeiten(self):
        self.ok(kauf(), self.lage_zu(datetime(2026, 3, 2, 12, 0, tzinfo=UTC)))
        self.abgelehnt(kauf(), self.lage_zu(datetime(2026, 3, 2, 8, 59, tzinfo=UTC)), "geschlossen")
        self.abgelehnt(kauf(), self.lage_zu(datetime(2026, 3, 2, 17, 0, tzinfo=UTC)), "geschlossen")
        self.abgelehnt(kauf(), self.lage_zu(datetime(2026, 3, 7, 12, 0, tzinfo=UTC)), "geschlossen")   # Samstag

    def test_keine_eroeffnung_kurz_vor_schluss(self):
        spaet = self.lage_zu(datetime(2026, 3, 2, 16, 50, tzinfo=UTC))
        self.abgelehnt(kauf(), spaet, "Handelsschluss")
        self.ok(Order("ABC", "verkauf", 9), Lage(**{**vars(spaet), "positionen": {"ABC": 9}}))   # Schließen bleibt erlaubt
        self.ok(kauf(), self.lage_zu(datetime(2026, 3, 2, 16, 44, tzinfo=UTC)))

    def test_krypto_rund_um_die_uhr(self):
        self.ok(kauf(), lage(jetzt=datetime(2026, 3, 7, 3, 0, tzinfo=UTC),
                             kurs=Kurs("ABC", 99.9, 100.0, 99.95, datetime(2026, 3, 7, 3, 0, tzinfo=UTC))))

    def test_unbekannter_markt_wird_abgelehnt(self):
        self.abgelehnt(kauf(), lage(risiko={"standard_markt": "mars"}), "Handelszeiten")

    def test_boersenzeit_mit_sommerzeit(self):
        us = mit_standard({})
        # 2026: US-Sommerzeit ab 8. März. 13:30 UTC ist im Winter 08:30 (zu), im Sommer 09:30 (offen).
        self.assertFalse(markt_status("us", datetime(2026, 3, 2, 13, 30, tzinfo=UTC), us).offen)
        self.assertTrue(markt_status("us", datetime(2026, 3, 9, 13, 30, tzinfo=UTC), us).offen)
        self.assertEqual(handelstag("us", datetime(2026, 3, 9, 1, 0, tzinfo=UTC), us).isoformat(), "2026-03-08")

    # ---- Not-Aus

    def test_notaus_flag(self):
        self.abgelehnt(kauf(), lage(notaus=True), "Not-Aus")
        self.abgelehnt(Order("ABC", "verkauf", 9), lage(notaus=True, positionen={"ABC": 9}), "Not-Aus")

    def test_notaus_datei(self):
        with tempfile.TemporaryDirectory() as d:
            l = lage(datenordner=Path(d))
            self.ok(kauf(), l)
            (Path(d) / "NOTAUS").write_text("x")
            self.abgelehnt(kauf(), l, "Not-Aus")
            (Path(d) / "NOTAUS").unlink()
            self.ok(kauf(), l)

    def test_alle_gruende_werden_gesammelt(self):
        e = pruefen(Order("ABC", "verkauf", 500), lage(kurs_alter=999, orders_heute=99))
        self.assertGreaterEqual(len(e.gruende), 4)


class PositionsgroesseTest(unittest.TestCase):
    def test_aus_risiko(self):
        # 0,5 % von 10.000 = 50 Risiko, Abstand 2 -> 25 Stück; Positionswert 2500 = 25 % (ohne Deckel)
        self.assertEqual(positionsgroesse(10000, 0.5, 100, 98), 25)

    def test_deckel_durch_maximale_positionsgroesse(self):
        self.assertEqual(positionsgroesse(10000, 0.5, 100, 98, position_max_prozent=10), 10)

    def test_short_gleich_gerechnet(self):
        self.assertEqual(positionsgroesse(10000, 0.5, 100, 102), 25)

    def test_abrunden_auf_mengenschritt(self):
        self.assertEqual(positionsgroesse(10000, 0.5, 100, 97), 16)                 # 16,67 -> 16
        self.assertAlmostEqual(positionsgroesse(10000, 0.5, 100, 97, schritt=0.5), 16.5)
        self.assertAlmostEqual(positionsgroesse(1000, 0.5, 30000, 29000, schritt=0.0001), 0.005)

    def test_wechselkurs_rechnet_in_kontowaehrung(self):
        # 50 EUR Risiko, Abstand 2 USD = 4 EUR je Stück -> 12 Stück; ohne Umrechnung wären es 25
        self.assertEqual(positionsgroesse(10000, 0.5, 100, 98, wechselkurs=2.0), 12)
        self.assertEqual(positionsgroesse(10000, 0.5, 100, 98), 25)
        # Deckel: 10 % von 10.000 = 1000 EUR, ein Stück kostet 200 EUR -> 5 Stück
        self.assertEqual(positionsgroesse(10000, 0.5, 100, 98, position_max_prozent=10, wechselkurs=2.0), 5)

    def test_ungueltiger_wechselkurs_gibt_null(self):
        for wk in (0.0, -1.0, float("nan"), float("inf")):
            self.assertEqual(positionsgroesse(10000, 0.5, 100, 98, wechselkurs=wk), 0.0, wk)

    def test_nan_gibt_null(self):
        nan = float("nan")
        self.assertEqual(positionsgroesse(10000, 0.5, nan, 99.0, 10.0), 0.0)
        self.assertEqual(positionsgroesse(10000, 0.5, 100.0, nan, 10.0), 0.0)
        self.assertEqual(positionsgroesse(nan, 0.5, 100.0, 99.0, 10.0), 0.0)

    def test_null_wenn_sinnlos(self):
        self.assertEqual(positionsgroesse(10000, 0.5, 100, 100), 0)      # kein Abstand
        self.assertEqual(positionsgroesse(10000, 0.5, 100, 0), 0)        # 0,5 Stück -> 0
        self.assertEqual(positionsgroesse(0, 0.5, 100, 98), 0)
        self.assertEqual(positionsgroesse(-5, 0.5, 100, 98), 0)

    def test_groesse_erfuellt_die_risikopruefung(self):
        menge = positionsgroesse(10000, 0.5, 100.0, 97.3, 10)
        self.assertGreater(menge, 0)
        self.assertTrue(pruefen(Order("ABC", "kauf", menge, stop_loss=97.3),
                                lage(kurs=Kurs("ABC", 99.9, 100.0, 99.95, JETZT))).ok)


if __name__ == "__main__":
    unittest.main()
