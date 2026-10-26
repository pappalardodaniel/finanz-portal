"""Kraken-Anbindung gegen einen lokalen Test-Server (kein Netz, keine echten Schlüssel).

Der Test-Server prüft die Signatur und die Nonce wie Kraken, mit einer eigenen, unabhängigen Umsetzung der
offiziellen Beispiel-Funktion aus der Kraken-Doku (https://docs.kraken.com/api/docs/guides/spot-rest-auth).
"""
import base64
import hashlib
import hmac
import json
import os
import stat
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from kitrader.broker import kraken
from kitrader.broker.basis import BrokerFehler, Order
from kitrader.broker.kraken import KrakenBroker, Ratenzaehler, Zugang, lade_einstellungen, signatur

# Beispielwerte aus der offiziellen Kraken-Doku (öffentlich, kein echter Schlüssel)
DOKU_GEHEIMNIS = "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="
DOKU_NONCE = "1616492376594"
DOKU_DATEN = {"nonce": DOKU_NONCE, "ordertype": "limit", "pair": "XBTUSD", "price": 37500, "type": "buy", "volume": 1.25}
DOKU_SIGNATUR = "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="

TEST_SCHLUESSEL = "TESTSCHLUESSEL-NICHT-ECHT"


def doku_signatur(urlpath, data, secret):
    """Die Beispielfunktion aus der Kraken-Doku, unverändert (Python-Beispiel)."""
    import urllib.parse as up
    encoded = (str(data["nonce"]) + up.urlencode(data)).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


PAAR_XBT = {"altname": "XBTEUR", "wsname": "XBT/EUR", "base": "XXBT", "quote": "ZEUR", "pair_decimals": 1,
            "lot_decimals": 8, "ordermin": "0.0001", "costmin": "0.5", "tick_size": "0.1",
            "leverage_buy": [2, 3], "leverage_sell": [2, 3], "status": "online"}
PAAR_ETH = dict(PAAR_XBT, altname="ETHEUR", wsname="ETH/EUR", base="XETH", ordermin="0.01", pair_decimals=2, tick_size="0.01")
PAAR_EURUSD = dict(PAAR_XBT, altname="EURUSD", wsname="EUR/USD", base="ZEUR", quote="ZUSD", ordermin="0.5", pair_decimals=5,
                   tick_size="0.00001")

OFFENE_ORDERS = {"open": {
    "OAAAAA-11111-AAAAAA": {"status": "open", "opentm": 1700000000.5, "vol": "0.01000000", "vol_exec": "0.00400000",
                            "price": "49990.0", "descr": {"pair": "XBTEUR", "type": "buy", "ordertype": "limit",
                                                          "price": "50000.0", "price2": "0", "order": "buy 0.01 XBTEUR @ limit 50000.0",
                                                          "close": "close position @ stop loss 49000.0"}},
    "OBBBBB-22222-BBBBBB": {"status": "open", "opentm": 1700000100, "vol": "0.5", "vol_exec": "0",
                            "descr": {"pair": "ETHEUR", "type": "sell", "ordertype": "stop-loss", "price": "1900.00",
                                      "price2": "0", "order": "sell 0.5 ETHEUR @ stop loss 1900.00"}}}}
GESCHLOSSENE_ORDERS = {"closed": {
    "OCCCCC-33333-CCCCCC": {"status": "closed", "opentm": 1699990000, "closetm": 1699990005, "vol": "0.02", "vol_exec": "0.02",
                            "price": "48000.0", "descr": {"pair": "XBTEUR", "type": "buy", "ordertype": "market", "price": "0",
                                                          "price2": "0", "order": "buy 0.02 XBTEUR @ market"}}}, "count": 1}


class KrakenAttrappe:
    """Minimaler Kraken: antwortet im dokumentierten Format und prüft Schlüssel, Signatur und Nonce."""

    def __init__(self, geheimnis=DOKU_GEHEIMNIS):
        self.geheimnis = geheimnis
        self.anfragen: list[dict] = []     # {"pfad", "daten", "kopf"}
        self.fehler: dict[str, list] = {}  # pfad-Ende -> error-Liste, die statt der normalen Antwort kommt
        self.verzoegerung = 0.0
        self.letzte_nonce = 0
        self.trade_alter = 5.0             # so viele Sekunden her ist der letzte Handel
        self.antworten = {
            "Balance": {"ZEUR": "1000.5000", "XXBT": "0.5000000000", "ZUSD": "0.0000"},
            "TradeBalance": {"eb": "6000.0000", "tb": "1000.5000", "m": "500.0", "n": "100.0", "c": "1000.0", "v": "1100.0",
                             "e": "1100.5", "mf": "600.5", "mfo": "550.0"},
            "OpenOrders": OFFENE_ORDERS,
            "ClosedOrders": GESCHLOSSENE_ORDERS,
            "OpenPositions": [{"pair": "XXBTZEUR", "positions": "1", "type": "buy", "leverage": "2.0", "cost": "1000.0",
                               "fee": "1.0", "vol": "0.02", "vol_closed": "0.0", "margin": "500.0", "value": "1100.0",
                               "net": "100.0"}],
            "QueryOrders": {"OTEST-00001-MARKET": {"status": "closed", "vol": "0.01", "vol_exec": "0.01", "price": "50010.0",
                                                   "opentm": 1700000000, "closetm": 1700000001,
                                                   "descr": {"pair": "XBTEUR", "type": "buy", "ordertype": "market",
                                                             "price": "0", "price2": "0", "order": "buy 0.01 XBTEUR @ market"}}},
            "Ticker": {"XXBTZEUR": {"a": ["50010.0", "1", "1.000"], "b": ["50000.0", "1", "1.000"], "c": ["50005.0", "0.1"]}},
            # Trades: Zeile = [Preis, Menge, Zeit, Seite, Art, Misc, ID]; die Zeit wird erst bei der Anfrage gesetzt
            "Trades": lambda abfrage: {"XXBTZEUR": [["50004.0", "0.2", time.time() - 60, "s", "m", "", 1],
                                                    ["50005.0", "0.1", time.time() - attrappe.trade_alter, "b", "m", "", 2]],
                                       "last": "1700000000000000000"},
            "OHLC": {"XXBTZEUR": [[1700000000, "50000.0", "50100.0", "49900.0", "50050.0", "50020.0", "1.5", 10],
                                  [1700000060, "50050.0", "50200.0", "50000.0", "50150.0", "50100.0", "2.5", 12],
                                  [1700000120, "50150.0", "50160.0", "50100.0", "50110.0", "50130.0", "0.5", 3]],
                     "last": 1700000060},
        }
        attrappe = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _antworten(self, objekt):
                daten = json.dumps(objekt).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(daten)))
                    self.end_headers()
                    self.wfile.write(daten)
                except OSError:
                    pass

            def do_GET(self):
                teile = urllib.parse.urlsplit(self.path)
                abfrage = dict(urllib.parse.parse_qsl(teile.query))
                attrappe.anfragen.append({"pfad": teile.path, "daten": abfrage, "kopf": {k.lower(): v for k, v in self.headers.items()}})
                name = teile.path.rsplit("/", 1)[-1]
                if name in attrappe.fehler:
                    return self._antworten({"error": attrappe.fehler[name], "result": {}})
                if name == "AssetPairs":
                    pair = abfrage.get("pair", "").upper()
                    if pair in ("XBTEUR", "XXBTZEUR"):
                        return self._antworten({"error": [], "result": {"XXBTZEUR": PAAR_XBT}})
                    if pair in ("ETHEUR", "XETHZEUR"):
                        return self._antworten({"error": [], "result": {"XETHZEUR": PAAR_ETH}})
                    if pair in ("EURUSD", "ZEURZUSD"):
                        return self._antworten({"error": [], "result": {"ZEURZUSD": PAAR_EURUSD}})
                    return self._antworten({"error": ["EQuery:Unknown asset pair"]})
                antwort = attrappe.antworten[name]
                self._antworten({"error": [], "result": antwort(abfrage) if callable(antwort) else antwort})

            def do_POST(self):
                laenge = int(self.headers.get("Content-Length", 0))
                roh = self.rfile.read(laenge).decode()
                daten = dict(urllib.parse.parse_qsl(roh))
                pfad = urllib.parse.urlsplit(self.path).path
                attrappe.anfragen.append({"pfad": pfad, "daten": daten, "kopf": {k.lower(): v for k, v in self.headers.items()}})
                if attrappe.verzoegerung:
                    time.sleep(attrappe.verzoegerung)
                if self.headers.get("API-Key") != TEST_SCHLUESSEL:
                    return self._antworten({"error": ["EAPI:Invalid key"]})
                erwartet = doku_signatur(pfad, dict(urllib.parse.parse_qsl(roh)), attrappe.geheimnis)
                if self.headers.get("API-Sign") != erwartet:
                    return self._antworten({"error": ["EAPI:Invalid signature"]})
                nonce = int(daten["nonce"])
                if nonce <= attrappe.letzte_nonce:
                    return self._antworten({"error": ["EAPI:Invalid nonce"]})
                attrappe.letzte_nonce = nonce
                name = pfad.rsplit("/", 1)[-1]
                if name in attrappe.fehler:
                    return self._antworten({"error": attrappe.fehler[name], "result": {}})
                if name == "AddOrder":
                    art = f"{daten['type']} {daten['volume']} {daten['pair']} @ {daten['ordertype']} {daten.get('price', '')}".strip()
                    descr = {"order": art}
                    if "close[ordertype]" in daten:
                        descr["close"] = f"close position @ {daten['close[ordertype]']} {daten['close[price]']}"
                    if daten.get("validate") == "true":
                        return self._antworten({"error": [], "result": {"descr": descr}})
                    txid = "OTEST-00001-MARKET" if daten["ordertype"] == "market" else "OTEST-00002-LIMIT"
                    return self._antworten({"error": [], "result": {"descr": descr, "txid": [txid]}})
                if name == "CancelOrder":
                    return self._antworten({"error": [], "result": {"count": 1 if daten["txid"] in attrappe.antworten["OpenOrders"]["open"] else 0}})
                self._antworten({"error": [], "result": attrappe.antworten[name]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def schliessen(self):
        self.server.shutdown()
        self.server.server_close()

    def privat(self, name):
        return [a for a in self.anfragen if a["pfad"].endswith("/private/" + name)]


class Signatur(unittest.TestCase):
    def test_doku_beispiel_mit_dict(self):
        """Das offizielle Beispiel der Kraken-Doku: Pfad, Nonce, Daten, Geheimnis -> erwartete API-Sign."""
        daten = urllib.parse.urlencode(DOKU_DATEN)
        self.assertEqual(daten, "nonce=1616492376594&ordertype=limit&pair=XBTUSD&price=37500&type=buy&volume=1.25")
        self.assertEqual(signatur("/0/private/AddOrder", daten, DOKU_NONCE, DOKU_GEHEIMNIS), DOKU_SIGNATUR)

    def test_doku_beispiel_wie_gesendet(self):
        daten = "nonce=1616492376594&ordertype=limit&pair=XBTUSD&price=37500&type=buy&volume=1.25"
        self.assertEqual(signatur("/0/private/AddOrder", daten, int(DOKU_NONCE), DOKU_GEHEIMNIS), DOKU_SIGNATUR)

    def test_unabhaengige_doku_funktion_stimmt_ueberein(self):
        self.assertEqual(doku_signatur("/0/private/AddOrder", dict(DOKU_DATEN), DOKU_GEHEIMNIS), DOKU_SIGNATUR)

    def test_andere_daten_andere_signatur(self):
        daten = urllib.parse.urlencode(dict(DOKU_DATEN, volume=2))
        self.assertNotEqual(signatur("/0/private/AddOrder", daten, DOKU_NONCE, DOKU_GEHEIMNIS), DOKU_SIGNATUR)

    def test_nonce_steigt_streng(self):
        werte = [kraken._naechste_nonce() for _ in range(2000)]
        self.assertEqual(werte, sorted(set(werte)))


class KrakenBasis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kraken = KrakenAttrappe()

    @classmethod
    def tearDownClass(cls):
        cls.kraken.schliessen()

    def setUp(self):
        self.kraken.anfragen.clear()
        self.kraken.fehler.clear()
        self.kraken.verzoegerung = 0.0
        self.kraken.trade_alter = 5.0
        self.broker = self.neu()

    def setze_antwort(self, name, wert):
        """Ersetzt eine Antwort der Attrappe für diesen Test."""
        alt = self.kraken.antworten[name]
        self.kraken.antworten[name] = wert
        self.addCleanup(self.kraken.antworten.__setitem__, name, alt)

    def neu(self, **kw):
        kw.setdefault("nur_pruefen", False)
        return KrakenBroker(Zugang(TEST_SCHLUESSEL, DOKU_GEHEIMNIS), basis_url=self.kraken.url, ratenbegrenzung=False, **kw)


class Abfragen(KrakenBasis):
    def test_konto(self):
        k = self.broker.konto()
        self.assertEqual((k.waehrung, k.guthaben, k.gesamtwert), ("EUR", 1000.5, 6100.0))
        self.assertEqual(k.kaufkraft, 1000.5)             # ohne Hebel: Bargeld
        self.assertFalse(k.papier)
        self.assertEqual(k.zusatz["bestaende"], {"ZEUR": 1000.5, "XXBT": 0.5})
        self.assertFalse(k.zusatz["nur_pruefen"])
        self.assertEqual(self.kraken.privat("TradeBalance")[0]["daten"]["asset"], "ZEUR")
        # jede private Anfrage trägt Schlüssel und Signatur im Kopf, nie im Body
        for a in self.kraken.privat("Balance") + self.kraken.privat("TradeBalance"):
            self.assertEqual(a["kopf"]["api-key"], TEST_SCHLUESSEL)
            self.assertNotIn(TEST_SCHLUESSEL, json.dumps(a["daten"]))

    def test_konto_mit_hebel_nutzt_freie_margin(self):
        self.assertEqual(self.neu(hebel=2).konto().kaufkraft, 550.0)

    def test_positionen_margin_und_spot(self):
        ps = self.neu(spot_paare=("XBTEUR",)).positionen()
        self.assertEqual([p.symbol for p in ps], ["XBTEUR", "XBTEUR"])
        margin, spot = ps
        self.assertEqual((margin.menge, margin.einstand, margin.kurs, margin.waehrung), (0.02, 50000.0, 55000.0, "EUR"))
        self.assertAlmostEqual(margin.ergebnis, 100.0)
        self.assertEqual((spot.menge, spot.kurs, spot.einstand), (0.5, 50005.0, 50005.0))   # Einstand unbekannt -> Kurs
        d = self.kraken.privat("OpenPositions")[0]["daten"]
        self.assertEqual((d["docalcs"], d["consolidation"]), ("true", "market"))

    def test_spot_bestaende_zaehlen_nur_fuer_angegebene_paare(self):
        self.assertEqual([p.symbol for p in self.broker.positionen()], ["XBTEUR"])   # nur die Margin-Position
        self.assertEqual(self.kraken.privat("Balance"), [])

    def test_short_margin_position_hat_negative_menge(self):
        alt = self.kraken.antworten["OpenPositions"]
        self.kraken.antworten["OpenPositions"] = [dict(alt[0], type="sell", vol="0.04", vol_closed="0.01", cost="1500.0", value="1400.0")]
        try:
            p = self.broker.positionen()[0]
        finally:
            self.kraken.antworten["OpenPositions"] = alt
        self.assertAlmostEqual(p.menge, -0.03)
        self.assertAlmostEqual(p.einstand, 37500.0)

    def test_kurs(self):
        k = self.broker.kurs("BTC/EUR")                    # BTC wird zu XBT, "/" fällt weg
        self.assertEqual((k.bid, k.ask, k.letzter, k.mitte), (50000.0, 50010.0, 50005.0, 50005.0))
        self.assertEqual(self.kraken.anfragen[0]["daten"]["pair"], "XBTEUR")
        ticker = [a for a in self.kraken.anfragen if a["pfad"].endswith("Ticker")][0]
        self.assertEqual(ticker["daten"]["pair"], "XBTEUR")

    def test_kurszeit_ist_die_zeit_des_letzten_handels(self):
        # Der Ticker hat keine Zeit; echte Zeit liefert /0/public/Trades. Sie wird genommen und der Kurs gilt als echt datiert.
        self.kraken.trade_alter = 7.0
        k = self.broker.kurs("XBTEUR")
        self.assertFalse(k.zeit_geschaetzt)
        self.assertEqual(k.waehrung, "EUR")
        alter = (datetime.now(timezone.utc) - k.zeit).total_seconds()
        self.assertTrue(6 <= alter < 15, alter)
        trades = [a for a in self.kraken.anfragen if a["pfad"].endswith("Trades")][0]
        self.assertEqual((trades["daten"]["pair"], trades["daten"]["count"]), ("XBTEUR", "1"))

    def test_kurszeit_ohne_handelsdaten_ist_nur_geschaetzt(self):
        # Fällt Trades aus oder ist leer, kennt niemand das Alter des Kurses: kennzeichnen statt die Abrufzeit zu glauben.
        self.kraken.fehler["Trades"] = ["EService:Unavailable"]
        k = self.broker.kurs("XBTEUR")
        self.assertTrue(k.zeit_geschaetzt)
        self.assertEqual((k.bid, k.ask), (50000.0, 50010.0))
        del self.kraken.fehler["Trades"]
        self.setze_antwort("Trades", {"XXBTZEUR": [], "last": "0"})
        self.assertTrue(self.broker.kurs("XBTEUR").zeit_geschaetzt)

    def test_wechselkurs_ueber_ein_kraken_paar(self):
        self.setze_antwort("Ticker", lambda q: {"ZEURZUSD": {"a": ["1.2000", "1", "1"], "b": ["1.1998", "1", "1"], "c": ["1.1999", "1"]}})
        self.assertAlmostEqual(self.broker.wechselkurs("EUR", "USD"), 1.1999)            # Paar EURUSD
        self.assertAlmostEqual(self.broker.wechselkurs("USD", "EUR"), 1 / 1.1999)        # Paar USDEUR gibt es nicht: umgekehrt
        self.assertEqual(self.broker.wechselkurs("EUR", "EUR"), 1.0)
        self.assertIsNone(self.broker.wechselkurs("EUR", "JPY"))                         # kein Paar, kein Kurs, nichts raten
        ticker = [a for a in self.kraken.anfragen if a["pfad"].endswith("Ticker")][0]
        self.assertEqual(ticker["daten"]["pair"], "EURUSD")

    def test_kerzen(self):
        ks = self.broker.kerzen("XBTEUR", "5m", 2)
        self.assertEqual(len(ks), 2)
        self.assertEqual((ks[0].o, ks[0].h, ks[0].l, ks[0].c, ks[0].v), (50050.0, 50200.0, 50000.0, 50150.0, 2.5))
        self.assertLess(ks[0].t, ks[1].t)
        self.assertEqual(ks[1].t.tzinfo.utcoffset(None).total_seconds(), 0)
        ohlc = [a for a in self.kraken.anfragen if a["pfad"].endswith("OHLC")][0]
        self.assertEqual(ohlc["daten"]["interval"], "5")
        with self.assertRaises(BrokerFehler):
            self.broker.kerzen("XBTEUR", "7m")

    def test_take_profit_order_ist_kein_schutz_stop(self):
        # Früher galt jede Order mit Auslösepreis als "stop": ein Gewinnziel hätte dem Autopiloten einen Schutz-Stop vorgetäuscht.
        alt = self.kraken.antworten["OpenOrders"]
        ziel = lambda typ: {"status": "open", "opentm": 1700000100, "vol": "0.5", "vol_exec": "0", "descr": {
            "pair": "ETHEUR", "type": "sell", "ordertype": typ, "price": "2100.00", "price2": "2090.00",
            "order": f"sell 0.5 ETHEUR @ {typ} 2100.00"}}
        self.kraken.antworten["OpenOrders"] = {"open": {"OT1": ziel("take-profit"), "OT2": ziel("take-profit-limit"),
                                                        "OT3": ziel("trailing-stop"), "OT4": ziel("stop-loss-limit")}}
        try:
            o = {x.id: x for x in self.broker.orders()}
        finally:
            self.kraken.antworten["OpenOrders"] = alt
        self.assertEqual((o["OT1"].order.art, o["OT1"].order.limit, o["OT1"].order.stop), ("limit", 2100.0, None))
        self.assertEqual((o["OT2"].order.art, o["OT2"].order.limit), ("limit", 2090.0))
        self.assertEqual(o["OT3"].order.art, "stop")                    # trailing-stop schützt weiterhin
        self.assertEqual(o["OT4"].order.art, "stop_limit")

    def test_orders_offen_und_alle(self):
        offen = self.broker.orders()
        self.assertEqual([o.id for o in offen], ["OAAAAA-11111-AAAAAA", "OBBBBB-22222-BBBBBB"])
        a, b = offen
        self.assertEqual((a.status, a.ausgefuehrt, a.preis, a.order.art, a.order.limit, a.order.seite),
                         ("teilweise", 0.004, 49990.0, "limit", 50000.0, "kauf"))
        self.assertIn("stop loss 49000.0", a.meldung)
        self.assertEqual((b.status, b.order.art, b.order.stop, b.order.seite), ("offen", "stop", 1900.0, "verkauf"))
        alle = self.broker.orders(nur_offene=False)
        self.assertEqual(len(alle), 3)
        self.assertEqual(alle[2].status, "ausgefuehrt")
        self.assertEqual(self.kraken.privat("ClosedOrders")[0]["daten"]["without_count"], "true")


class Senden(KrakenBasis):
    def test_limit_mit_stop_loss_wird_gerundet_und_angehaengt(self):
        o = Order("XBTEUR", "kauf", 0.123456789, "limit", limit=50000.123456, stop_loss=49000.04, gueltig="gtc")
        s = self.broker.senden(o)
        d = self.kraken.privat("AddOrder")[0]["daten"]
        self.assertEqual((d["pair"], d["type"], d["ordertype"]), ("XBTEUR", "buy", "limit"))
        self.assertEqual((d["volume"], d["price"]), ("0.12345678", "50000.1"))          # Menge abgerundet, Preis auf tick_size
        self.assertEqual((d["close[ordertype]"], d["close[price]"]), ("stop-loss", "49000.0"))
        self.assertNotIn("validate", d)
        self.assertNotIn("leverage", d)
        self.assertNotIn("timeinforce", d)
        self.assertTrue(d["cl_ord_id"].startswith("kt") and len(d["cl_ord_id"]) <= 18)
        self.assertEqual((s.id, s.status), ("OTEST-00002-LIMIT", "offen"))
        self.assertIn("stop-loss", s.meldung)

    def test_nur_take_profit(self):
        self.broker.senden(Order("XBTEUR", "kauf", 0.01, "limit", limit=50000, take_profit=52000, gueltig="gtc"))
        d = self.kraken.privat("AddOrder")[0]["daten"]
        self.assertEqual((d["close[ordertype]"], d["close[price]"]), ("take-profit", "52000.0"))

    def test_stop_loss_und_take_profit_nur_stop_loss_wird_angehaengt(self):
        s = self.broker.senden(Order("XBTEUR", "kauf", 0.01, "limit", limit=50000, stop_loss=49000, take_profit=52000, gueltig="gtc"))
        d = self.kraken.privat("AddOrder")[0]["daten"]
        self.assertEqual(d["close[ordertype]"], "stop-loss")
        self.assertEqual(len([k for k in d if k.startswith("close[")]), 2)
        self.assertIn("Take-Profit NICHT angehängt", s.meldung)

    def test_ziel_wird_nur_ohne_stop_loss_als_order_gefuehrt(self):
        # Mit Stop-Loss hängt Kraken nur den Stop an: das Ziel muss der Autopilot selbst überwachen.
        b = self.broker
        self.assertFalse(b.fuehrt_ziel_als_order(Order("XBTEUR", "kauf", 0.01, stop_loss=49000, take_profit=52000)))
        self.assertTrue(b.fuehrt_ziel_als_order(Order("XBTEUR", "kauf", 0.01, take_profit=52000)))
        self.assertTrue(b.fuehrt_ziel_als_order(Order("XBTEUR", "kauf", 0.01, stop_loss=49000)))     # kein Ziel: nichts zu führen

    def test_unsinnige_schutzpreise_werden_nicht_gesendet(self):
        with self.assertRaises(BrokerFehler):
            self.broker.senden(Order("XBTEUR", "kauf", 0.01, "limit", limit=50000, stop_loss=53000, take_profit=52000))
        self.assertEqual(self.kraken.privat("AddOrder"), [])

    def test_marktorder_fragt_den_status_ab(self):
        s = self.broker.senden(Order("XBTEUR", "kauf", 0.01, gueltig="gtc"))
        d = self.kraken.privat("AddOrder")[0]["daten"]
        self.assertEqual(d["ordertype"], "market")
        self.assertNotIn("price", d)
        self.assertEqual((s.id, s.status, s.ausgefuehrt, s.preis), ("OTEST-00001-MARKET", "ausgefuehrt", 0.01, 50010.0))
        self.assertEqual(self.kraken.privat("QueryOrders")[0]["daten"]["txid"], "OTEST-00001-MARKET")

    def test_stop_und_stop_limit(self):
        self.broker.senden(Order("XBTEUR", "verkauf", 0.01, "stop", stop=48000, gueltig="gtc"))
        self.broker.senden(Order("XBTEUR", "verkauf", 0.01, "stop_limit", stop=48000, limit=47900, gueltig="gtc"))
        a, b = [x["daten"] for x in self.kraken.privat("AddOrder")]
        self.assertEqual((a["ordertype"], a["price"]), ("stop-loss", "48000.0"))
        self.assertEqual((b["ordertype"], b["price"], b["price2"]), ("stop-loss-limit", "48000.0", "47900.0"))

    def test_tagesorder_verfaellt_um_mitternacht_utc(self):
        self.broker.senden(Order("XBTEUR", "kauf", 0.01, "limit", limit=50000))
        d = self.kraken.privat("AddOrder")[0]["daten"]
        self.assertEqual(d["timeinforce"], "GTD")
        self.assertEqual(int(d["expiretm"]) % 86400, 0)
        self.assertGreater(int(d["expiretm"]), time.time())

    def test_kein_short_ohne_hebel(self):
        self.broker.senden(Order("XBTEUR", "verkauf", 0.01, "limit", limit=51000, gueltig="gtc"))
        self.assertNotIn("leverage", self.kraken.privat("AddOrder")[0]["daten"])

    MARGIN_LONG = {"pair": "XXBTZEUR", "type": "buy", "vol": "0.02", "vol_closed": "0.0", "cost": "1000.0", "value": "1100.0"}
    MARGIN_SHORT = dict(MARGIN_LONG, type="sell")

    def hebel_lage(self, spot="0.5", positionen=()):
        """Spot-Bestand an XBT und offene Margin-Positionen der Attrappe für diesen Test."""
        self.setze_antwort("Balance", dict(self.kraken.antworten["Balance"], XXBT=spot))
        self.setze_antwort("OpenPositions", list(positionen))

    def hebel_von(self, seite, menge, **kw):
        self.kraken.anfragen.clear()
        self.neu(hebel=2).senden(Order("XBTEUR", seite, menge, "limit", limit=51000 if seite == "verkauf" else 49000,
                                       gueltig="gtc", **kw))
        return self.kraken.privat("AddOrder")[0]["daten"].get("leverage")

    def test_short_mit_hebel(self):
        self.hebel_lage(spot="0.0")                                  # kein Bestand: der Verkauf eröffnet einen Short
        self.assertEqual(self.hebel_von("verkauf", 0.01), "2")
        self.assertEqual(self.kraken.privat("AddOrder")[0]["daten"]["type"], "sell")

    def test_verkauf_von_spot_bestand_bekommt_nie_hebel(self):
        # Früher bekam jede Order den Hebel: der Verkauf von Bestand hätte einen Short ZUSÄTZLICH zum Bestand eröffnet.
        self.hebel_lage(spot="0.5")
        self.assertIsNone(self.hebel_von("verkauf", 0.5))            # genau der Bestand
        self.assertIsNone(self.hebel_von("verkauf", 0.01))

    def test_verkauf_ueber_den_bestand_hinaus_wird_nicht_gesendet(self):
        self.hebel_lage(spot="0.5")
        self.kraken.anfragen.clear()
        with self.assertRaisesRegex(BrokerFehler, "Spot-Bestand"):
            self.neu(hebel=2).senden(Order("XBTEUR", "verkauf", 0.6, "limit", limit=51000, gueltig="gtc"))
        self.assertEqual(self.kraken.privat("AddOrder"), [])

    def test_kauf_bekommt_nur_zum_schliessen_eines_margin_shorts_hebel(self):
        self.hebel_lage(spot="0.0")
        self.assertIsNone(self.hebel_von("kauf", 0.01))              # normaler Spot-Kauf, keine Margin-Long-Position
        self.hebel_lage(spot="0.0", positionen=[self.MARGIN_SHORT])
        self.assertEqual(self.hebel_von("kauf", 0.02), "2")          # schließt den Short
        self.kraken.anfragen.clear()
        with self.assertRaisesRegex(BrokerFehler, "Long-Position"):  # größer als der Short: würde Long eröffnen
            self.neu(hebel=2).senden(Order("XBTEUR", "kauf", 0.05, "limit", limit=49000, gueltig="gtc"))
        self.assertEqual(self.kraken.privat("AddOrder"), [])

    def test_verkauf_schliesst_margin_long_mit_hebel(self):
        self.hebel_lage(spot="0.0", positionen=[self.MARGIN_LONG])
        self.assertEqual(self.hebel_von("verkauf", 0.02), "2")

    def test_ohne_hebel_gibt_es_nie_hebel_und_keine_zusatzabfragen(self):
        self.hebel_lage(spot="0.0")
        self.kraken.anfragen.clear()
        self.neu().senden(Order("XBTEUR", "verkauf", 0.01, "limit", limit=51000, gueltig="gtc"))
        self.assertNotIn("leverage", self.kraken.privat("AddOrder")[0]["daten"])
        self.assertEqual(self.kraken.privat("OpenPositions") + self.kraken.privat("Balance"), [])

    def test_alles_schliessen_nur_fuer_die_genannten_symbole(self):
        schritte = self.neu(nur_pruefen=True).alles_schliessen(symbole=["ETHEUR"])
        self.assertEqual([c["daten"]["txid"] for c in self.kraken.privat("CancelOrder")], ["OBBBBB-22222-BBBBBB"])
        self.assertTrue(any("fremd, nicht angefasst: Order OAAAAA-11111-AAAAAA XBTEUR" in x for x in schritte), schritte)
        self.assertTrue(any("fremd, nicht angefasst: Position XBTEUR" in x for x in schritte), schritte)
        self.assertEqual(self.kraken.privat("AddOrder"), [])         # die einzige Position (XBTEUR) ist fremd

    def test_unter_mindestmenge_wird_gar_nicht_gesendet(self):
        with self.assertRaisesRegex(BrokerFehler, "Minimum"):
            self.broker.senden(Order("XBTEUR", "kauf", 0.00001, "limit", limit=50000))
        self.assertEqual(self.kraken.privat("AddOrder"), [])

    def test_unbekanntes_paar(self):
        with self.assertRaisesRegex(BrokerFehler, "Handelspaar"):
            self.broker.senden(Order("FOOEUR", "kauf", 1, "limit", limit=1))

    def test_nur_pruefen_sendet_validate_und_fuehrt_nichts_aus(self):
        b = self.neu(nur_pruefen=True)
        s = b.senden(Order("XBTEUR", "kauf", 0.01, "limit", limit=50000, stop_loss=49000, gueltig="gtc"))
        d = self.kraken.privat("AddOrder")[0]["daten"]
        self.assertEqual(d["validate"], "true")
        self.assertEqual(s.status, "storniert")                # kein offener Auftrag entstanden
        self.assertTrue(s.id.startswith("GEPRUEFT-"))
        self.assertIn("NUR GEPRÜFT", s.meldung)
        self.assertEqual(self.kraken.privat("QueryOrders"), [])
        b.stornieren(s.id)                                      # nichts zu stornieren, kein Aufruf
        self.assertEqual(self.kraken.privat("CancelOrder"), [])

    def test_nur_pruefen_ist_ohne_ausdrueckliche_abschaltung_an(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ):
            for name in [n for n in os.environ if n.startswith("KRAKEN_")]:
                del os.environ[name]
            datei = Path(tmp) / "kraken.env"
            datei.write_text(f"KRAKEN_API_KEY={TEST_SCHLUESSEL}\nKRAKEN_API_SECRET={DOKU_GEHEIMNIS}\n")
            datei.chmod(0o600)
            b = KrakenBroker(basis_url=self.kraken.url, ratenbegrenzung=False, datei=datei)
            self.assertTrue(b.nur_pruefen)
            b.senden(Order("XBTEUR", "kauf", 0.01, "limit", limit=50000, gueltig="gtc"))
            self.assertEqual(self.kraken.privat("AddOrder")[0]["daten"]["validate"], "true")

    def test_stornieren(self):
        self.broker.stornieren("OAAAAA-11111-AAAAAA")
        self.assertEqual(self.kraken.privat("CancelOrder")[0]["daten"]["txid"], "OAAAAA-11111-AAAAAA")
        with self.assertRaisesRegex(BrokerFehler, "keine Order"):
            self.broker.stornieren("OXXXXX-00000-XXXXXX")

    def test_alles_schliessen_mit_nur_pruefen(self):
        schritte = self.neu(nur_pruefen=True).alles_schliessen()
        self.assertIn("nur_pruefen", schritte[0])
        self.assertEqual(len(self.kraken.privat("CancelOrder")), 2)
        self.assertEqual(self.kraken.privat("AddOrder")[0]["daten"]["validate"], "true")


class Fehlerfaelle(KrakenBasis):
    def test_kraken_fehler_werden_uebersetzt(self):
        self.kraken.fehler["AddOrder"] = ["EOrder:Insufficient funds"]
        with self.assertRaises(BrokerFehler) as c:
            self.broker.senden(Order("XBTEUR", "kauf", 0.01, "limit", limit=50000, gueltig="gtc"))
        self.assertIn("Guthaben", str(c.exception))
        self.assertIn("EOrder:Insufficient funds", str(c.exception))

    def test_mehrere_fehler_und_warnungen(self):
        self.kraken.fehler["Balance"] = ["WGeneral:Hinweis", "EGeneral:Permission denied", "EService:Unavailable"]
        with self.assertRaises(BrokerFehler) as c:
            self.broker.konto()
        text = str(c.exception)
        self.assertIn("Berechtigung", text)
        self.assertIn("nicht verfügbar", text)
        self.assertNotIn("Hinweis", text)                        # W-Meldungen sind nur Warnungen

    def test_unbekannter_fehler_mit_nummer_nimmt_den_passenden_anfang(self):
        self.kraken.fehler["Balance"] = ["EGeneral:Internal error:5"]
        with self.assertRaisesRegex(BrokerFehler, "Interner Fehler"):
            self.broker.konto()
        self.kraken.fehler["Balance"] = ["EFoo:Bar"]
        with self.assertRaisesRegex(BrokerFehler, r"\[EFoo:Bar\]"):
            self.broker.konto()

    def test_falscher_schluessel_und_falsches_geheimnis(self):
        b = KrakenBroker(Zugang("FALSCH", DOKU_GEHEIMNIS), nur_pruefen=False, basis_url=self.kraken.url, ratenbegrenzung=False)
        with self.assertRaisesRegex(BrokerFehler, "Schlüssel"):
            b.konto()
        anderes = base64.b64encode(b"ein ganz anderes geheimnis").decode()
        b = KrakenBroker(Zugang(TEST_SCHLUESSEL, anderes), nur_pruefen=False, basis_url=self.kraken.url, ratenbegrenzung=False)
        with self.assertRaisesRegex(BrokerFehler, "Signatur"):
            b.konto()

    def test_geheimnisse_stehen_nie_in_texten(self):
        b = KrakenBroker(Zugang("FALSCH", DOKU_GEHEIMNIS), nur_pruefen=False, basis_url=self.kraken.url, ratenbegrenzung=False)
        with self.assertRaises(BrokerFehler) as c:
            b.konto()
        for text in (str(c.exception), repr(b), repr(b._zugang), str(b._zugang)):
            self.assertNotIn(DOKU_GEHEIMNIS, text)
            self.assertNotIn("FALSCH", text)

    def test_kein_schluessel_vorhanden(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ):
            for name in [n for n in os.environ if n.startswith("KRAKEN_")]:
                del os.environ[name]
            b = KrakenBroker(basis_url=self.kraken.url, ratenbegrenzung=False, datei=Path(tmp) / "gibt-es-nicht.env")
            with self.assertRaisesRegex(BrokerFehler, "kraken.env"):
                b.konto()
            self.assertEqual(b.kurs("XBTEUR").letzter, 50005.0)   # öffentliche Daten gehen auch ohne Schlüssel

    def test_server_nicht_erreichbar(self):
        b = KrakenBroker(Zugang(TEST_SCHLUESSEL, DOKU_GEHEIMNIS), nur_pruefen=False, basis_url="http://127.0.0.1:9",
                         ratenbegrenzung=False, timeout=2)
        with self.assertRaisesRegex(BrokerFehler, "nicht erreichbar"):
            b.konto()

    def test_zeitueberschreitung_bei_addorder_meldet_unklaren_ausgang(self):
        self.kraken.verzoegerung = 1.0
        b = self.neu(timeout=0.3)
        with self.assertRaises(BrokerFehler) as c:
            b.senden(Order("XBTEUR", "kauf", 0.01, "limit", limit=50000, gueltig="gtc"))
        self.assertIn("unklar", str(c.exception))
        self.assertIn("cl_ord_id", str(c.exception))
        self.assertEqual(len(self.kraken.privat("AddOrder")), 1)  # kein automatischer zweiter Versuch


class Einstellungen(unittest.TestCase):
    def schreibe(self, tmp, text, modus=0o600):
        datei = Path(tmp) / "kraken.env"
        datei.write_text(text)
        datei.chmod(modus)
        return datei

    def test_datei_wird_gelesen(self):
        with tempfile.TemporaryDirectory() as tmp:
            datei = self.schreibe(tmp, "# Kommentar\nexport KRAKEN_API_KEY='abc'\nKRAKEN_API_SECRET=\"def\"\n\n")
            e = lade_einstellungen(datei, umgebung={})
            self.assertEqual((e["zugang"].schluessel, e["zugang"].geheimnis, e["nur_pruefen"]), ("abc", "def", True))

    def test_nur_pruefen_nur_bei_ausdruecklichem_nein_aus(self):
        with tempfile.TemporaryDirectory() as tmp:
            for text, erwartet in (("0", False), ("false", False), ("Nein", False), ("aus", False),
                                   ("1", True), ("ja", True), ("vielleicht", True), ("", True)):
                datei = self.schreibe(tmp, f"KRAKEN_API_KEY=a\nKRAKEN_API_SECRET=b\nKRAKEN_NUR_PRUEFEN={text}\n")
                self.assertEqual(lade_einstellungen(datei, umgebung={})["nur_pruefen"], erwartet, text)

    def test_umgebung_hat_vorrang(self):
        with tempfile.TemporaryDirectory() as tmp:
            datei = self.schreibe(tmp, "KRAKEN_API_KEY=a\nKRAKEN_API_SECRET=b\nKRAKEN_NUR_PRUEFEN=0\n")
            e = lade_einstellungen(datei, umgebung={"KRAKEN_API_KEY": "x", "KRAKEN_NUR_PRUEFEN": "1"})
            self.assertEqual((e["zugang"].schluessel, e["zugang"].geheimnis, e["nur_pruefen"]), ("x", "b", True))

    def test_nur_umgebungsvariablen(self):
        e = lade_einstellungen(Path("/gibt/es/nicht.env"), umgebung={"KRAKEN_API_KEY": "x", "KRAKEN_API_SECRET": "y"})
        self.assertEqual((e["zugang"].schluessel, e["nur_pruefen"]), ("x", True))
        self.assertIsNone(lade_einstellungen(Path("/gibt/es/nicht.env"), umgebung={})["zugang"])

    @unittest.skipUnless(os.name == "posix", "Dateirechte nur unter Linux/macOS")
    def test_zu_offene_dateirechte_werden_abgelehnt(self):
        with tempfile.TemporaryDirectory() as tmp:
            datei = self.schreibe(tmp, "KRAKEN_API_KEY=a\nKRAKEN_API_SECRET=b\n", modus=0o644)
            with self.assertRaisesRegex(BrokerFehler, "chmod 600"):
                lade_einstellungen(datei, umgebung={})

    def test_zugang_zeigt_nichts_in_repr(self):
        z = Zugang("geheim-a", "geheim-b")
        self.assertNotIn("geheim", repr(z) + str(z))


class Ratenbegrenzung(unittest.TestCase):
    def test_wartet_wenn_zaehler_voll(self):
        zeit = [100.0]
        pausen = []

        def schlafen(s):
            pausen.append(s)
            zeit[0] += s

        z = Ratenzaehler(3, 1.0, uhr=lambda: zeit[0], schlafen=schlafen)
        for _ in range(3):
            z.warte()
        self.assertEqual(pausen, [])
        z.warte()                          # vierter Aufruf: Zähler 3 + 1 > 3, eine Sekunde Abbau nötig
        self.assertEqual(pausen, [1.0])
        zeit[0] += 10                      # lange Pause: Zähler ist wieder leer
        z.warte(2)
        self.assertEqual(pausen, [1.0])

    def test_sperre_nach_rate_limit_fehler(self):
        zeit = [0.0]
        pausen = []
        z = Ratenzaehler(15, 0.33, uhr=lambda: zeit[0], schlafen=lambda s: pausen.append(s))
        z.sperre(10)
        z.warte()
        self.assertGreaterEqual(pausen[0], 10)

    def test_stufen_entsprechen_der_doku(self):
        self.assertEqual(Ratenzaehler.STUFEN, {"starter": (15, 0.33), "intermediate": (20, 0.5), "pro": (20, 1.0)})


if __name__ == "__main__":
    unittest.main()
