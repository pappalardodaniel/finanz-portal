"""Reiter „Live“ und Morgenlage der Bedienoberfläche: Schnittstellen mit Schlüsselschutz, Kerzen, eigene Trades aus Papierkonto
und Journal, KI-Einschätzung, Morgenlage-Datei. Alles offline, nur lesend."""
import json
import re
import sqlite3
import time
import unittest
from datetime import datetime, timedelta, timezone

from kitrader.broker.basis import BrokerFehler, Kerze, Kurs, Order
from kitrader.journal import Journal
from kitrader.oberflaeche import live
from kitrader.risiko import Ergebnis

from .daten import MONTAG
from .test_oberflaeche import ServerBasis

UTC = timezone.utc


def kerzen_5m(n=40, ende=None):
    ende = ende or datetime.now(UTC).replace(second=0, microsecond=0)
    start = ende - timedelta(minutes=5 * n)
    return [Kerze(start + timedelta(minutes=5 * i), 100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100.2 + i * 0.1, 10) for i in range(n)]


class FalscheQuelle:
    """Kursquelle für den Test: zählt die Abrufe, kann ausfallen."""

    def __init__(self, kerzen=None):
        self.kerzen_liste = kerzen if kerzen is not None else kerzen_5m()
        self.kerzen_abrufe = 0
        self.kurs_abrufe = 0
        self.kerzen_fehler = None
        self.kurs_fehler = None

    def kerzen(self, symbol, takt, anzahl):
        self.kerzen_abrufe += 1
        if self.kerzen_fehler:
            raise BrokerFehler(self.kerzen_fehler)
        return self.kerzen_liste[-anzahl:]

    def kurs(self, symbol):
        self.kurs_abrufe += 1
        if self.kurs_fehler:
            raise BrokerFehler(self.kurs_fehler)
        return Kurs(symbol, 104.0, 104.2, 104.1, datetime.now(UTC))


class LiveBasis(ServerBasis):
    def setUp(self):
        super().setUp()
        self.quelle = FalscheQuelle()
        self.app.kursquelle = lambda: self.quelle
        self.app._live_quelle = None
        self.app._live_cache.clear()

    def chart(self, symbol="ABC", takt="5m", **kw):
        return self.json("GET", f"/api/live/chart?symbol={symbol}&takt={takt}", **kw)


class SchutzTest(LiveBasis):
    PFADE = ("/api/live/symbole", "/api/live/chart?symbol=ABC&takt=5m", "/api/morgenlage")

    def test_ohne_schluessel_verboten(self):
        for pfad in self.PFADE:
            self.assertEqual(self.anfrage("GET", pfad, schluessel=False)[0], 403, pfad)
            self.assertEqual(self.anfrage("GET", pfad, schluessel="falsch")[0], 403, pfad)

    def test_fremder_host_und_origin_verboten(self):
        for pfad in self.PFADE:
            self.assertEqual(self.anfrage("GET", pfad, host="boese.example")[0], 403, pfad)
            self.assertEqual(self.anfrage("GET", pfad, origin="http://boese.example")[0], 403, pfad)

    def test_mit_schluessel_erlaubt(self):
        for pfad in self.PFADE:
            self.assertEqual(self.anfrage("GET", pfad)[0], 200, pfad)

    def test_nur_lesend_schreiben_geht_nicht(self):
        for pfad in self.PFADE:
            self.assertEqual(self.anfrage("POST", pfad.split("?")[0], daten={})[0], 404, pfad)

    def test_abrufe_aendern_nichts(self):
        self.position_eroeffnen()
        b = self.bauer()
        vorher = [(s.id, s.status) for s in b.orders(nur_offene=False)]
        for pfad in self.PFADE:
            self.anfrage("GET", pfad)
        self.assertEqual([(s.id, s.status) for s in b.orders(nur_offene=False)], vorher)
        b.beenden()

    def test_seite_hat_reiter_live_ohne_fremde_adressen(self):
        text = self.anfrage("GET", "/", schluessel=False, origin=None)[2].decode("utf-8")
        self.assertIn('data-tab="live"', text)
        self.assertIn(">Live</button>", text)
        self.assertIn('id="tab-live"', text)
        self.assertNotRegex(text, r"<script[^>]*\ssrc=")                          # keine Fremdbibliothek, kein CDN
        self.assertNotRegex(text, r"(?<!www\.w3\.org/2000/svg)https?://(?!www\.w3\.org)")


class SymboleTest(LiveBasis):
    def test_symbole_aus_autopilot_und_watchlist_ohne_doppelte(self):
        self.app.einst["autopilot"]["symbole"] = ["BTC/EUR", "ABC"]
        self.app.einst["watchlist"]["werte"] = ["XBTEUR", "ETH/EUR"]
        status, d = self.json("GET", "/api/live/symbole")
        self.assertEqual(status, 200)
        self.assertEqual(d["symbole"], ["BTC/EUR", "ABC", "ETH/EUR"])
        self.assertEqual(d["takt"], "5m")
        self.assertIn("1h", d["takte"])
        self.assertEqual(d["aktualisierung_s"], 30)

    def test_unbekanntes_symbol_und_takt_abgelehnt(self):
        self.assertEqual(self.chart("ZZZ")[0], 400)
        self.assertEqual(self.chart("ABC", "7m")[0], 400)
        self.assertEqual(self.quelle.kerzen_abrufe, 0)

    def test_symbol_wird_wie_eingestellt_zurueckgegeben(self):
        self.app.einst["autopilot"]["symbole"] = ["BTC/EUR"]
        status, d = self.chart("XBTEUR")
        self.assertEqual((status, d["symbol"]), (200, "BTC/EUR"))


class ChartTest(LiveBasis):
    def test_kerzen_kurs_und_leere_listen(self):
        status, d = self.chart()
        self.assertEqual(status, 200)
        self.assertEqual(len(d["kerzen"]), 40)
        t, o, h, l, c, v = d["kerzen"][0]
        self.assertIsInstance(t, int)
        self.assertLessEqual(l, o)
        self.assertGreaterEqual(h, c)
        self.assertEqual(d["takt_s"], 300)
        self.assertEqual(d["kurs"]["mitte"], 104.1)
        self.assertEqual(d["trades"], [])
        self.assertIsNone(d["einschaetzung"])
        self.assertEqual((d["kurs_fehler"], d["fehler"]), ("", []))

    def test_anzahl_begrenzt_die_kerzen(self):
        _, d = self.json("GET", "/api/live/chart?symbol=ABC&takt=5m&anzahl=25")
        self.assertEqual(len(d["kerzen"]), 25)

    def test_zwischenspeicher_schont_die_kursquelle(self):
        self.chart()
        self.chart()
        self.assertEqual((self.quelle.kerzen_abrufe, self.quelle.kurs_abrufe), (1, 1))
        self.chart(takt="15m")
        self.assertEqual(self.quelle.kerzen_abrufe, 2)

    def test_ohne_kerzen_502_mit_klarem_text(self):
        self.quelle.kerzen_fehler = "Kraken nicht erreichbar"
        status, d = self.chart()
        self.assertEqual(status, 502)
        self.assertIn("Kraken nicht erreichbar", d["fehler"])

    def test_kurs_fehlt_chart_bleibt(self):
        self.quelle.kurs_fehler = "kein Kurs"
        status, d = self.chart()
        self.assertEqual(status, 200)
        self.assertIsNone(d["kurs"])
        self.assertIn("kein Kurs", d["kurs_fehler"])
        self.assertEqual(len(d["kerzen"]), 40)

    def test_papier_offene_position_mit_stop_und_ziel(self):
        self.position_eroeffnen()                                  # 10 ABC, Stop 95, Ziel 110
        _, d = self.chart()
        self.assertEqual(len(d["trades"]), 1)
        t = d["trades"][0]
        self.assertEqual((t["status"], t["richtung"], t["menge"], t["stop"], t["ziel"]), ("offen", "long", 10, 95.0, 110.0))
        self.assertAlmostEqual(t["einstieg"]["preis"], 100.0, delta=0.5)
        self.assertIsNone(t["ausstieg"])

    def test_papier_abgeschlossener_trade_durch_ziel_hat_stop_und_ziel(self):
        self.position_eroeffnen()
        b = self.bauer()
        self.markt.setze("ABC", 110.2, 110.4)                      # Ziel 110 erreicht
        self.uhr.weiter(minutes=5)
        b.aktualisieren()
        b.beenden()
        _, d = self.chart()
        t = d["trades"][0]
        self.assertEqual((t["status"], t["richtung"]), ("zu", "long"))
        self.assertEqual((t["stop"], t["ziel"]), (95.0, 110.0))
        self.assertGreater(t["ausstieg"]["zeit"], t["einstieg"]["zeit"])
        self.assertGreater(t["netto"], 0)
        self.assertLess(t["netto"], t["brutto"])                   # nach Gebühren

    def test_papier_von_hand_geschlossen_ohne_stop_und_ziel(self):
        self.position_eroeffnen()
        b = self.bauer()
        self.uhr.weiter(minutes=1)
        b.senden(Order("ABC", "verkauf", 10))
        b.beenden()
        _, d = self.chart()
        self.assertEqual([(t["status"], t["stop"], t["ziel"]) for t in d["trades"]], [("zu", None, None)])

    def test_zwei_positionen_behalten_ihre_eigenen_linien(self):
        b = self.bauer()
        b.senden(Order("ABC", "kauf", 5, stop_loss=95.0, take_profit=110.0))
        self.uhr.weiter(minutes=5)
        self.markt.setze("ABC", 101.0, 101.1)
        b.senden(Order("ABC", "kauf", 5, stop_loss=98.0, take_profit=104.0))
        b.beenden()
        _, d = self.chart()
        self.assertEqual(sorted((t["stop"], t["ziel"]) for t in d["trades"]), [(95.0, 110.0), (98.0, 104.0)])

    def test_fremdes_symbol_nicht_in_den_trades(self):
        self.app.einst["autopilot"]["symbole"] = ["ABC", "DEF"]
        self.markt.setze("DEF", 50.0, 50.1)
        b = self.bauer()
        b.senden(Order("DEF", "kauf", 3, stop_loss=45.0))
        b.beenden()
        self.assertEqual(self.chart("ABC")[1]["trades"], [])
        self.assertEqual(len(self.chart("DEF")[1]["trades"]), 1)

    def test_ki_einschaetzung_letzte_je_horizont(self):
        j = Journal(self.ordner / "journal.db", self.einst)
        alt = datetime.now(UTC) - timedelta(days=2)
        neu = datetime.now(UTC) - timedelta(minutes=30)
        for zeit, wahr, richtung in ((alt, 0.9, "faellt"), (neu, 0.6, "steigt")):
            for h in ("1d", "3M", "1J"):
                j.einschaetzung("ABC", richtung, h, f"[KI] Grund | Wahrscheinlichkeit {wahr:.2f} | Treiber test", 100.0, zeit=zeit)
        j.einschaetzung("ABC", "faellt", "1d", "eigene Meinung ohne Marke", 100.0)                        # keine KI
        j.einschaetzung("XYZ", "faellt", "1d", "[KI] anderes Symbol | Wahrscheinlichkeit 0.50", 100.0)
        j.beenden()
        _, d = self.chart()
        e = d["einschaetzung"]
        self.assertEqual([(p["horizont"], p["richtung"], p["wahrscheinlichkeit"]) for p in e["prognosen"]],
                         [("1d", "steigt", 0.6), ("3M", "steigt", 0.6), ("1J", "steigt", 0.6)])
        self.assertIn(e["alter_min"], range(29, 33))
        self.assertEqual(e["preis"], 100.0)


class JournalTradesTest(unittest.TestCase):
    def setUp(self):
        self.j = Journal(":memory:", None)
        self.addCleanup(self.j.beenden)

    def handel(self, seite, menge, preis, minute, stop=None, ziel=None, modus="demo", symbol="XBTEUR"):
        order = Order(symbol, seite, menge, stop_loss=stop, take_profit=ziel)
        zeit = MONTAG + timedelta(minutes=minute)
        eid = self.j.entscheidung(modus, symbol, "t", None, Ergebnis(True, []), order)
        from kitrader.broker.basis import OrderStand
        self.j.ausfuehrung(eid, OrderStand(f"o{eid}", order, "ausgefuehrt", menge, preis, zeit))

    def test_fifo_paarung_und_offener_rest(self):
        self.handel("kauf", 2, 100.0, 0, stop=95.0, ziel=110.0)
        self.handel("verkauf", 1, 105.0, 10)
        trades = live.journal_trades(self.j, "BTC/EUR", "demo")
        zu = [t for t in trades if t["status"] == "zu"]
        offen = [t for t in trades if t["status"] == "offen"]
        self.assertEqual(len(zu), 1)
        self.assertEqual((zu[0]["richtung"], zu[0]["menge"], zu[0]["brutto"], zu[0]["stop"], zu[0]["ziel"]), ("long", 1, 5.0, 95.0, 110.0))
        self.assertEqual((offen[0]["menge"], offen[0]["stop"], offen[0]["ziel"], offen[0]["ausstieg"]), (1, 95.0, 110.0, None))
        self.assertEqual(zu[0]["ausstieg"]["zeit"] - zu[0]["einstieg"]["zeit"], 600)

    def test_short_und_umkehr(self):
        self.handel("verkauf", 1, 100.0, 0)
        self.handel("kauf", 1, 90.0, 5)
        t = live.journal_trades(self.j, "XBTEUR", "demo")
        self.assertEqual([(x["richtung"], x["brutto"]) for x in t], [("short", 10.0)])

    def test_andere_betriebsart_und_nicht_ausgefuehrtes_zaehlen_nicht(self):
        self.handel("kauf", 1, 100.0, 0, modus="papier")
        order = Order("XBTEUR", "kauf", 1)
        eid = self.j.entscheidung("demo", "XBTEUR", "t", None, Ergebnis(True, []), order)
        from kitrader.broker.basis import OrderStand
        self.j.ausfuehrung(eid, OrderStand("x", order, "abgelehnt", 0, None, MONTAG, "nein"))
        self.assertEqual(live.journal_trades(self.j, "XBTEUR", "demo"), [])


class MorgenlageTest(LiveBasis):
    def test_ohne_datei(self):
        status, d = self.json("GET", "/api/morgenlage")
        self.assertEqual((status, d), (200, {"vorhanden": False}))

    def test_mit_datei_wird_der_inhalt_unbesehen_weitergegeben(self):
        inhalt = {"version": 1, "erstellt": "2026-10-26T07:00:00+00:00", "werte": [{"wert": "ABC", "preis": 1.5}]}
        (self.ordner / "morgenlage.json").write_text(json.dumps(inhalt), encoding="utf-8")
        status, d = self.json("GET", "/api/morgenlage")
        self.assertEqual(status, 200)
        self.assertTrue(d["vorhanden"])
        self.assertEqual(d["daten"], inhalt)
        self.assertLess(d["alter_h"], 0.1)

    def test_kaputte_und_riesige_datei(self):
        datei = self.ordner / "morgenlage.json"
        datei.write_text("{kaputt", encoding="utf-8")
        d = live.morgenlage(self.ordner)
        self.assertFalse(d["vorhanden"])
        self.assertIn("nicht lesbar", d["fehler"])
        datei.write_text(" " * (live.MORGENLAGE_MAX_BYTES + 10), encoding="utf-8")
        self.assertIn("zu groß", live.morgenlage(self.ordner)["fehler"])


class AbgebrochenTest(LiveBasis):
    def test_browser_weg_macht_keinen_traceback(self):
        """Lädt der Browser neu, während eine Antwort unterwegs ist, bricht die Verbindung ab: das ist kein Fehler (und soll im
        Terminal von `start` keine Seiten Traceback erzeugen)."""
        import io
        from contextlib import redirect_stderr
        ausgabe = io.StringIO()
        with redirect_stderr(ausgabe):
            for fehler in (BrokenPipeError(), ConnectionResetError()):
                try:
                    raise fehler
                except OSError:
                    self.server.handle_error(None, ("127.0.0.1", 1))
        self.assertEqual(ausgabe.getvalue(), "")
        with redirect_stderr(ausgabe):
            try:
                raise ValueError("echter Fehler")
            except ValueError:
                self.server.handle_error(None, ("127.0.0.1", 1))
        self.assertIn("echter Fehler", ausgabe.getvalue())


class SeiteTest(LiveBasis):
    def test_fremde_texte_bleiben_text(self):
        """Die Seite fügt Inhalte nur über textContent ein (nie innerHTML): Meldungen, KI und Morgenlage kommen von außen."""
        text = self.anfrage("GET", "/", schluessel=False, origin=None)[2].decode("utf-8")
        self.assertNotIn("innerHTML", text)
        self.assertNotIn("insertAdjacentHTML", text)
        self.assertNotIn("eval(", text)
        self.assertIsNotNone(re.search(r"function liveChart", text))


if __name__ == "__main__":
    unittest.main()
