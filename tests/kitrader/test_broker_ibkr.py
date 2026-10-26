"""IBKR-Anbindung gegen ein nachgebildetes Client Portal Gateway (lokaler Test-Server, kein Konto, kein Netz).

Die Antworten sind den Beispielen der IBKR-Doku nachgebildet (https://www.interactivebrokers.com/docs/web-api/).
Ob ein echtes Gateway genauso antwortet, kann dieser Test nicht zeigen.
"""
import json
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from kitrader.broker import ibkr
from kitrader.broker.basis import BrokerFehler, Order
from kitrader.broker.ibkr import IbkrBroker, ssl_kontext

KONTO = "DU1234567"
PRAEFIX = "/v1/api"


def erfolg(n=1, status="PreSubmitted", erste=1001):
    return [{"order_id": str(erste + i), "order_status": status, "encrypt_message": "1"} for i in range(n)]


def rueckfrage(ids, text, rid="a12b34c5-d678-9e01-2f34-56789b12cd3e"):
    return [{"id": rid, "message": [text], "isSuppressed": False, "messageIds": ids}]


class GatewayAttrappe:
    def __init__(self, tls=None, adresse="127.0.0.1"):
        self.angemeldet = True
        self.status_401 = False
        self.competing = False
        self.konto = KONTO
        self.konten = None
        self.http_fehler = None             # (pfad-Ende, Code)
        self.anfragen: list[dict] = []
        self.leere_snapshots = 1
        self.snapshot_modus = "normal"      # normal | schluss | halt | leer
        self.snapshot_aufrufe = 0
        self.order_antwort = None           # Funktion(body) -> JSON, sonst Standard
        self.antworten_reply: list = []     # Antworten auf /iserver/reply/... der Reihe nach
        self.orders_erster_aufruf_leer = True
        self.orders_aufrufe = 0
        self.storno_antwort = None
        self.history_zeilen = 6
        self.ohne_zeit = False              # Snapshot ohne "_updated" (Kurszeit unbekannt)
        self.vertragswaehrung = {265598: "USD"}   # conid -> Währung (Vertragsdaten)
        attrappe = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def antwort(self, objekt, code=200):
                daten = json.dumps(objekt).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(daten)))
                    self.end_headers()
                    self.wfile.write(daten)
                except OSError:
                    pass

            def verarbeiten(self, methode):
                teile = urllib.parse.urlsplit(self.path)
                abfrage = dict(urllib.parse.parse_qsl(teile.query))
                laenge = int(self.headers.get("Content-Length") or 0)
                roh = self.rfile.read(laenge).decode() if laenge else ""
                body = json.loads(roh) if roh else None
                pfad = teile.path[len(PRAEFIX):] if teile.path.startswith(PRAEFIX) else teile.path
                attrappe.anfragen.append({"methode": methode, "pfad": pfad, "abfrage": abfrage, "body": body,
                                          "kopf": {k.lower(): v for k, v in self.headers.items()}})
                if attrappe.http_fehler and pfad.endswith(attrappe.http_fehler[0]):
                    return self.antwort({"error": "Testfehler"}, attrappe.http_fehler[1])
                if attrappe.status_401:
                    return self.antwort({"error": "not authenticated"}, 401)
                a = attrappe
                if pfad == "/iserver/auth/status":
                    return self.antwort({"authenticated": a.angemeldet, "competing": a.competing, "connected": a.angemeldet,
                                         "established": a.angemeldet, "message": ""})
                if pfad == "/tickle":
                    return self.antwort({"session": "x", "ssoExpires": 460311, "iserver": {"authStatus": {"authenticated": True}}})
                if pfad == "/iserver/accounts":
                    konten = a.konten if a.konten is not None else [a.konto]
                    return self.antwort({"accounts": konten, "selectedAccount": konten[0] if konten else None,
                                         "isPaper": a.konto.startswith("DU")})
                if pfad == "/portfolio/accounts":
                    return self.antwort([{"id": a.konto, "accountId": a.konto, "currency": "EUR",
                                          "type": "DEMO" if a.konto.startswith("DU") else "INDIVIDUAL"}])
                if pfad == f"/portfolio/{a.konto}/summary":
                    def feld(x, w="EUR"):
                        return {"amount": x, "currency": w, "isNull": False, "timestamp": 1702582422000, "severity": 0}
                    return self.antwort({"totalcashvalue": feld(98000.5), "netliquidation": feld(100250.25),
                                         "buyingpower": feld(392000.0), "availablefunds": feld(97000.0),
                                         "excessliquidity": feld(96000.0), "grosspositionvalue": feld(2250.0)})
                if pfad == "/iserver/account/pnl/partitioned":
                    return self.antwort({"upnl": {f"{a.konto}.Core": {"rowType": 1, "dpl": -125.5, "nl": 1, "upl": 2}}})
                if pfad == f"/portfolio/{a.konto}/positions/0":
                    return self.antwort([
                        {"acctId": a.konto, "conid": 265598, "contractDesc": "AAPL", "ticker": "AAPL", "position": 10.0,
                         "mktPrice": 190.5, "avgCost": 185.25, "avgPrice": 185.2, "currency": "USD"},
                        {"acctId": a.konto, "conid": 8314, "contractDesc": "IBM", "ticker": "IBM", "position": -5.0,
                         "mktPrice": 160.0, "avgCost": 165.0, "currency": "USD"},
                        {"acctId": a.konto, "conid": 1, "contractDesc": "ALT", "ticker": "ALT", "position": 0.0,
                         "mktPrice": 1.0, "avgCost": 1.0, "currency": "USD"}])
                if pfad.startswith(f"/portfolio/{a.konto}/positions/"):
                    return self.antwort([])
                if pfad == "/iserver/secdef/search":
                    return self.antwort(SUCHE.get(abfrage.get("symbol"), []))
                if pfad.startswith("/iserver/contract/") and pfad.endswith("/info"):
                    conid = int(pfad.split("/")[3])
                    if conid not in a.vertragswaehrung:
                        return self.antwort({"error": "unbekannt"}, 404)
                    return self.antwort({"con_id": conid, "currency": a.vertragswaehrung[conid], "instrument_type": "STK"})
                if pfad == "/iserver/marketdata/snapshot":
                    a.snapshot_aufrufe += 1
                    conid = int(abfrage["conids"])
                    if a.snapshot_modus == "leer" or a.snapshot_aufrufe <= a.leere_snapshots:
                        return self.antwort([{"conid": conid, "conidEx": str(conid)}])
                    werte = {"normal": {"31": "168.42", "84": "168.41", "86": "168.43"}, "schluss": {"31": "C168.42"},
                             "halt": {"31": "H168.42", "84": "168.41", "86": "168.43"}}[a.snapshot_modus]
                    if conid == DEVISEN_CONID:
                        werte = {"31": "1.0999", "84": "1.0998", "86": "1.1000"}
                    zusatz = {"88": "1,300", "conid": conid, "conidEx": str(conid), "server_id": "q1"}
                    if not a.ohne_zeit:
                        zusatz["_updated"] = 1712596911593
                    return self.antwort([dict(werte, **zusatz)])
                if pfad == "/iserver/marketdata/history":
                    zeilen = [{"o": 100 + i, "c": 101 + i, "h": 102 + i, "l": 99 + i, "v": 1000.5 + i,
                               "t": 1747229400000 + i * 300000} for i in range(a.history_zeilen)]
                    zeilen.reverse()            # absichtlich durcheinander: der Broker muss sortieren
                    return self.antwort({"symbol": "AAPL", "data": zeilen, "points": len(zeilen)})
                if pfad == f"/iserver/account/{a.konto}/orders" and methode == "POST":
                    if a.order_antwort:
                        return self.antwort(a.order_antwort(body))
                    return self.antwort(erfolg(len(body["orders"])))
                if pfad.startswith("/iserver/reply/"):
                    return self.antwort(a.antworten_reply.pop(0) if a.antworten_reply else erfolg(1))
                if pfad == "/iserver/account/orders":
                    a.orders_aufrufe += 1
                    if a.orders_erster_aufruf_leer and a.orders_aufrufe == 1:
                        return self.antwort({"orders": [], "snapshot": False})
                    return self.antwort({"orders": LIVE_ORDERS, "snapshot": True})
                if pfad.startswith(f"/iserver/account/{a.konto}/order/") and methode == "DELETE":
                    return self.antwort(a.storno_antwort or {"msg": "Request was submitted", "order_id": 987654, "conid": 265598,
                                                             "account": a.konto})
                self.antwort({"error": f"unbekannter Pfad {pfad}"}, 404)

            def do_GET(self):
                self.verarbeiten("GET")

            def do_POST(self):
                self.verarbeiten("POST")

            def do_DELETE(self):
                self.verarbeiten("DELETE")

        self.server = ThreadingHTTPServer((adresse, 0), Handler)
        self.server.daemon_threads = True
        if tls:
            kontext = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            kontext.load_cert_chain(*tls)
            self.server.socket = kontext.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        self.url = f"{'https' if tls else 'http'}://{adresse}:{self.port}{PRAEFIX}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def schliessen(self):
        self.server.shutdown()
        self.server.server_close()

    def aufrufe(self, methode, pfad):
        return [a for a in self.anfragen if a["methode"] == methode and a["pfad"] == pfad]


def suche(conid, symbol, firma, boerse):
    return {"conid": str(conid), "companyHeader": f"{firma} - {boerse}", "companyName": firma, "symbol": symbol,
            "description": boerse, "sections": [{"secType": "STK"}, {"secType": "OPT", "months": "MAR24", "exchange": "SMART"}]}


DEVISEN_CONID = 12087792
SUCHE = {
    "EUR": [{"conid": str(DEVISEN_CONID), "companyHeader": "EUR.USD", "companyName": "European Monetary Union Euro",
             "symbol": "EUR", "description": "IDEALPRO", "sections": [{"secType": "CASH"}]}],
    "AAPL": [suche(265598, "AAPL", "APPLE INC", "NASDAQ")],
    "SAP": [suche(11111, "SAP", "SAP SE ADR", "NYSE"), suche(22222, "SAP", "SAP SE", "IBIS")],
    "ZWEI": [suche(33333, "ZWEI", "ZWEI AG", "IBIS"), suche(44444, "ZWEI", "ZWEI PLC", "LSE")],
    "NYSE2": [suche(55555, "NYSE2", "EINS", "NYSE"), suche(66666, "NYSE2", "ZWEI", "NASDAQ")],
    "SPAREN": [{"conid": "77777", "symbol": "SPAREN", "description": "NYSE", "companyHeader": "x", "sections": [{"secType": "OPT"}]}],
}

LIVE_ORDERS = [
    {"acct": KONTO, "conid": 265598, "orderId": 1234568790, "ticker": "AAPL", "side": "BUY", "totalSize": 10.0,
     "filledQuantity": 4.0, "remainingQuantity": 6.0, "status": "Submitted", "origOrderType": "LIMIT",
     "orderDesc": "Buy 10 Limit 190.00, GTC", "price": "190.00", "avgPrice": "189.9", "lastExecutionTime_r": 1702317649000},
    {"acct": KONTO, "conid": 265598, "orderId": 1234568791, "ticker": "AAPL", "side": "SELL", "totalSize": 10.0,
     "filledQuantity": 0.0, "status": "PreSubmitted", "origOrderType": "STOP", "orderDesc": "Sell 10 Stop 180.00, GTC",
     "price": "180.00"},
    {"acct": KONTO, "conid": 8314, "orderId": 1234568792, "ticker": "IBM", "side": "SELL", "totalSize": 5.0,
     "filledQuantity": 5.0, "remainingQuantity": 0.0, "status": "Filled", "origOrderType": "MARKET", "avgPrice": "161.5",
     "orderDesc": "Sold 5 Market, GTC", "lastExecutionTime_r": 1702317649000},
    {"acct": "DU9999999", "conid": 1, "orderId": 5, "ticker": "FREMD", "side": "BUY", "totalSize": 1.0, "status": "Submitted",
     "origOrderType": "MARKET"},
]


class GatewayBasis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gw = GatewayAttrappe()

    @classmethod
    def tearDownClass(cls):
        cls.gw.schliessen()

    def setUp(self):
        gw = self.gw
        gw.anfragen.clear()
        gw.angemeldet, gw.status_401, gw.competing, gw.konto, gw.konten = True, False, False, KONTO, None
        gw.http_fehler, gw.leere_snapshots, gw.snapshot_modus, gw.snapshot_aufrufe = None, 1, "normal", 0
        gw.order_antwort, gw.antworten_reply, gw.storno_antwort = None, [], None
        gw.orders_erster_aufruf_leer, gw.orders_aufrufe, gw.history_zeilen = True, 0, 6
        gw.ohne_zeit, gw.vertragswaehrung = False, {265598: "USD"}
        self.broker = self.neu()

    def neu(self, **kw):
        kw.setdefault("snapshot_pause", 0.0)
        return IbkrBroker(self.gw.url, ratenbegrenzung=False, **kw)


class Sitzung(GatewayBasis):
    def test_gateway_laeuft_nicht(self):
        b = IbkrBroker("https://localhost:9/v1/api", ratenbegrenzung=False, timeout=2)
        with self.assertRaises(BrokerFehler) as c:
            b.konto()
        text = str(c.exception)
        self.assertIn("Client Portal Gateway", text)
        self.assertIn("https://localhost:5000", text)
        self.assertIn("run.sh", text)

    def test_nicht_angemeldet(self):
        self.gw.angemeldet = False
        with self.assertRaisesRegex(BrokerFehler, "nicht angemeldet.*Browser"):
            self.broker.konto()

    def test_nicht_angemeldet_per_http_401(self):
        self.gw.status_401 = True
        with self.assertRaisesRegex(BrokerFehler, "nicht angemeldet.*Browser"):
            self.broker.kurs("AAPL")

    def test_konkurrierende_sitzung(self):
        self.gw.competing = True
        with self.assertRaisesRegex(BrokerFehler, "konkurrierende Sitzung"):
            self.broker.konto()

    def test_zu_viele_anfragen(self):
        self.gw.http_fehler = ("/iserver/auth/status", 429)
        with self.assertRaisesRegex(BrokerFehler, "zu viele Anfragen"):
            self.broker.konto()

    def test_serverfehler_zeigt_den_text(self):
        self.gw.http_fehler = ("/iserver/accounts", 500)
        with self.assertRaisesRegex(BrokerFehler, "500.*Testfehler"):
            self.broker.konto()

    def test_keine_konten(self):
        self.gw.konten = []
        with self.assertRaisesRegex(BrokerFehler, "keine handelbaren Konten"):
            self.broker.konto()

    def test_fremdes_konto(self):
        with self.assertRaisesRegex(BrokerFehler, "gehört nicht zur Anmeldung"):
            self.neu(konto_id="DU0000001").konto()

    def test_reihenfolge_und_user_agent(self):
        self.broker.konto()
        pfade = [a["pfad"] for a in self.gw.anfragen]
        self.assertEqual(pfade[:3], ["/iserver/auth/status", "/iserver/accounts", "/portfolio/accounts"])
        self.assertTrue(all(a["kopf"]["user-agent"].startswith("kitrader") for a in self.gw.anfragen))
        self.assertEqual([a["methode"] for a in self.gw.anfragen[:3]], ["POST", "GET", "GET"])

    def test_pruefung_wird_nicht_vor_jedem_aufruf_wiederholt(self):
        self.broker.konto()
        self.broker.positionen()
        self.assertEqual(len(self.gw.aufrufe("POST", "/iserver/auth/status")), 1)

    def test_tickle(self):
        self.assertIn("session", self.broker.tickle())
        self.assertEqual(self.gw.aufrufe("POST", "/tickle")[0]["methode"], "POST")

    def test_sitzung_halten_im_hintergrund(self):
        ende = self.broker.sitzung_halten(intervall=0.05)
        try:
            frist = time.time() + 3
            while len(self.gw.aufrufe("POST", "/tickle")) < 3 and time.time() < frist:
                time.sleep(0.02)
        finally:
            ende.set()
        self.assertGreaterEqual(len(self.gw.aufrufe("POST", "/tickle")), 3)
        self.assertIsNone(self.broker.letzter_tickle_fehler)

    def test_mindestabstaende_werden_eingehalten(self):
        b = IbkrBroker(self.gw.url, ratenbegrenzung=True)
        zeit, pausen = [100.0], []
        with mock.patch.object(ibkr.time, "monotonic", lambda: zeit[0]), \
                mock.patch.object(ibkr.time, "sleep", lambda s: (pausen.append(round(s, 2)), zeit.__setitem__(0, zeit[0] + s))):
            b._warte("/tickle")
            b._warte("/tickle")          # 1 Aufruf/s
            b._warte("/portfolio/accounts")
            b._warte("/portfolio/accounts")  # 1 Aufruf/5 s
        # 1 s zwischen zwei /tickle, 0,11 s globaler Abstand (10 Anfragen/s), 5 s zwischen zwei /portfolio/accounts
        self.assertEqual(pausen, [1.0, 0.11, 5.0])


class Konto(GatewayBasis):
    def test_demokonto(self):
        k = self.broker.konto()
        self.assertEqual((k.waehrung, k.guthaben, k.gesamtwert, k.kaufkraft), ("EUR", 98000.5, 100250.25, 392000.0))
        self.assertEqual(k.tagesergebnis, -125.5)
        self.assertTrue(k.papier)
        self.assertTrue(self.broker.papier)
        self.assertEqual(self.broker.konto_id, KONTO)
        self.assertEqual(k.zusatz["verfuegbar"], 97000.0)

    def test_echtes_konto_ist_nicht_papier(self):
        self.gw.konto = "U7654321"
        b = self.neu()
        self.assertFalse(b.papier)               # vor der Anmeldung: vorsichtshalber Echtgeld
        self.assertFalse(b.konto().papier)

    def test_konto_id_im_konstruktor_setzt_papier_sofort(self):
        self.assertTrue(self.neu(konto_id="DU1234567").papier)
        self.assertFalse(self.neu(konto_id="U1234567").papier)

    def test_positionen(self):
        ps = self.broker.positionen()
        self.assertEqual([(p.symbol, p.menge, p.einstand, p.kurs, p.waehrung) for p in ps],
                         [("AAPL", 10.0, 185.2, 190.5, "USD"), ("IBM", -5.0, 165.0, 160.0, "USD")])   # ohne Null-Position
        self.assertAlmostEqual(ps[0].ergebnis, 53.0)
        self.assertAlmostEqual(ps[1].ergebnis, 25.0)         # Short im Gewinn
        self.assertEqual(self.gw.aufrufe("GET", f"/portfolio/{KONTO}/positions/0")[0]["pfad"], f"/portfolio/{KONTO}/positions/0")


class Kurse(GatewayBasis):
    def test_kurs_nach_vorlauf(self):
        k = self.broker.kurs("AAPL")
        self.assertEqual((k.bid, k.ask, k.letzter, k.symbol), (168.41, 168.43, 168.42, "AAPL"))
        self.assertAlmostEqual(k.mitte, 168.42)
        self.assertEqual(k.zeit.isoformat(), "2024-04-08T17:21:51.593000+00:00")
        schnappschuesse = self.gw.aufrufe("GET", "/iserver/marketdata/snapshot")
        self.assertEqual(len(schnappschuesse), 2)            # erster Aufruf liefert noch nichts
        self.assertEqual(schnappschuesse[0]["abfrage"], {"conids": "265598", "fields": "31,84,86"})

    def test_kurs_mit_zeit_ist_nicht_geschaetzt_und_hat_die_waehrung_des_instruments(self):
        k = self.broker.kurs("AAPL")
        self.assertFalse(k.zeit_geschaetzt)
        self.assertEqual(k.waehrung, "USD")
        self.assertEqual(len(self.gw.aufrufe("GET", "/iserver/contract/265598/info")), 1)
        self.broker.kurs("AAPL")
        self.assertEqual(len(self.gw.aufrufe("GET", "/iserver/contract/265598/info")), 1)    # zwischengespeichert

    def test_kurs_ohne_updated_hat_nur_eine_geschaetzte_zeit(self):
        # Ohne "_updated" kennt niemand das Alter des Kurses: kennzeichnen (die Risikoprüfung erlaubt dann keine Eröffnung).
        self.gw.ohne_zeit = True
        k = self.broker.kurs("AAPL")
        self.assertTrue(k.zeit_geschaetzt)
        self.assertEqual((k.bid, k.ask), (168.41, 168.43))
        self.assertLess(abs((datetime.now(timezone.utc) - k.zeit).total_seconds()), 30)      # nur die Abrufzeit

    def test_unlesbare_vertragsdaten_machen_die_waehrung_unbekannt_statt_den_kurs_zu_blockieren(self):
        self.gw.vertragswaehrung = {}
        k = self.broker.kurs("AAPL")
        self.assertEqual(k.waehrung, "")                                 # Schließen muss weiter möglich sein
        self.assertAlmostEqual(k.mitte, 168.42)

    def test_wechselkurs_ueber_das_devisenpaar(self):
        self.assertAlmostEqual(self.broker.wechselkurs("EUR", "USD"), 1.0999)            # EUR.USD: 1 EUR in USD
        self.assertAlmostEqual(self.broker.wechselkurs("USD", "EUR"), 1 / 1.0999)        # umgekehrt: Kehrwert
        self.assertEqual(self.broker.wechselkurs("EUR", "eur"), 1.0)
        self.assertIsNone(self.broker.wechselkurs("EUR", "CHF"))                         # kein Paar: nichts raten
        suche = self.gw.aufrufe("GET", "/iserver/secdef/search")[0]
        self.assertEqual(suche["abfrage"], {"symbol": "EUR", "secType": "CASH"})

    def test_schlusskurs_des_vortags_ist_kein_kurs(self):
        self.gw.snapshot_modus = "schluss"
        with self.assertRaisesRegex(BrokerFehler, "Kein aktueller Kurs"):
            self.neu(snapshot_versuche=3).kurs("AAPL")

    def test_nie_daten(self):
        self.gw.snapshot_modus = "leer"
        with self.assertRaisesRegex(BrokerFehler, "Kein aktueller Kurs.*Marktdaten-Abo"):
            self.neu(snapshot_versuche=3).kurs("AAPL")
        self.assertEqual(len(self.gw.aufrufe("GET", "/iserver/marketdata/snapshot")), 3)

    def test_handelsstopp(self):
        self.gw.snapshot_modus = "halt"
        with self.assertRaisesRegex(BrokerFehler, "ausgesetzt"):
            self.broker.kurs("AAPL")

    def test_conid_direkt_ohne_suche(self):
        self.broker.kurs("265598")
        self.assertEqual(self.gw.aufrufe("GET", "/iserver/secdef/search"), [])

    def test_kerzen_sortiert_und_gekuerzt(self):
        ks = self.broker.kerzen("AAPL", "5m", 4)
        self.assertEqual(len(ks), 4)
        self.assertEqual([k.o for k in ks], [102, 103, 104, 105])          # die letzten vier, älteste zuerst
        self.assertEqual((ks[0].h, ks[0].l, ks[0].c, ks[0].v), (104, 101, 103, 1002.5))
        self.assertEqual(ks[0].t.isoformat(), "2025-05-14T13:40:00+00:00")  # 1747229400000 ms + 2 * 5 min
        self.assertEqual(ks[1].t.tzinfo.utcoffset(None).total_seconds(), 0)
        self.assertEqual(len(self.broker.kerzen("AAPL", "5m", 100)), 6)    # weniger vorhanden als gewünscht

    def test_kerzen_abfrage_je_takt(self):
        erwartet = {"1m": ("2d", "1min"), "5m": ("5d", "5min"), "15m": ("13d", "15min"), "1h": ("10w", "1h"), "1d": ("2y", "1d")}
        for takt, (periode, balken) in erwartet.items():
            self.gw.anfragen.clear()
            self.broker.kerzen("AAPL", takt, 300)
            a = self.gw.aufrufe("GET", "/iserver/marketdata/history")[0]["abfrage"]
            self.assertEqual((a["period"], a["bar"], a["conid"], a["outsideRth"]), (periode, balken, "265598", "false"), takt)

    def test_kerzen_fehler(self):
        with self.assertRaises(BrokerFehler):
            self.broker.kerzen("AAPL", "7m")
        with self.assertRaises(BrokerFehler):
            self.broker.kerzen("AAPL", "1m", 0)
        self.gw.history_zeilen = 0
        with self.assertRaisesRegex(BrokerFehler, "keine Kerzen"):
            self.broker.kerzen("AAPL", "1m", 10)


class Symbole(GatewayBasis):
    def test_suche_und_zwischenspeicher(self):
        self.assertEqual(self.broker._conid("aapl"), 265598)
        self.assertEqual(self.broker._conid("AAPL"), 265598)
        s = self.gw.aufrufe("GET", "/iserver/secdef/search")
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["abfrage"], {"symbol": "AAPL", "secType": "STK"})

    def test_us_boerse_hat_vorrang_wenn_eindeutig(self):
        self.assertEqual(self.broker._conid("SAP"), 11111)

    def test_mehrdeutig_ist_ein_fehler(self):
        for symbol in ("ZWEI", "NYSE2"):
            with self.assertRaisesRegex(BrokerFehler, "nicht eindeutig.*conid"):
                self.broker._conid(symbol)

    def test_unbekannt_oder_keine_aktie(self):
        for symbol in ("GIBTSNICHT", "SPAREN"):          # SPAREN hat nur eine Optionen-Sektion, keine Aktie
            with self.assertRaisesRegex(BrokerFehler, "nicht gefunden"):
                self.broker._conid(symbol)

    def test_kennung_hat_vorrang(self):
        self.assertEqual(self.broker._conid("IRGENDWAS", "12345"), 12345)
        with self.assertRaisesRegex(BrokerFehler, "conid"):
            self.broker._conid("AAPL", "kein-int")

    def test_positionen_fuellen_den_zwischenspeicher(self):
        self.broker.positionen()
        self.gw.anfragen.clear()
        self.assertEqual(self.broker._conid("IBM"), 8314)
        self.assertEqual(self.gw.aufrufe("GET", "/iserver/secdef/search"), [])


class Senden(GatewayBasis):
    def auftraege(self):
        return self.gw.aufrufe("POST", f"/iserver/account/{KONTO}/orders")

    def test_bracket_mit_stop_loss_und_take_profit(self):
        o = Order("AAPL", "kauf", 10, stop_loss=99.5, take_profit=103.25, notiz="Test")
        s = self.broker.senden(o)
        body = self.auftraege()[0]["body"]
        eltern, stop, ziel = body["orders"]
        self.assertEqual((eltern["orderType"], eltern["side"], eltern["quantity"], eltern["tif"]), ("MKT", "BUY", 10, "DAY"))
        self.assertEqual((eltern["acctId"], eltern["conid"], eltern["listingExchange"], eltern["outsideRTH"]),
                         (KONTO, 265598, "SMART", False))
        self.assertTrue(eltern["cOID"])
        self.assertNotIn("price", eltern)
        self.assertNotIn("parentId", eltern)
        self.assertEqual((stop["orderType"], stop["side"], stop["price"], stop["quantity"], stop["parentId"]),
                         ("STP", "SELL", 99.5, 10, eltern["cOID"]))
        self.assertEqual((ziel["orderType"], ziel["side"], ziel["price"], ziel["parentId"]), ("LMT", "SELL", 103.25, eltern["cOID"]))
        self.assertNotIn("cOID", stop)                         # Kinder tragen keine eigene cOID
        self.assertNotIn("cOID", ziel)
        self.assertEqual((s.id, s.status), ("1001", "offen"))
        self.assertIn("1002", s.meldung)
        self.assertIn("1003", s.meldung)
        self.assertIs(s.order, o)

    def test_leerverkauf_mit_schutz_dreht_die_richtung_der_kinder(self):
        self.broker.senden(Order("AAPL", "verkauf", 4, "limit", limit=110, stop_loss=115, take_profit=100, gueltig="gtc"))
        eltern, stop, ziel = self.auftraege()[0]["body"]["orders"]
        self.assertEqual((eltern["side"], eltern["orderType"], eltern["price"], eltern["tif"]), ("SELL", "LMT", 110, "GTC"))
        self.assertEqual((stop["side"], ziel["side"], stop["tif"]), ("BUY", "BUY", "GTC"))

    def test_nur_stop_loss(self):
        self.broker.senden(Order("AAPL", "kauf", 1.5, "limit", limit=100, stop_loss=98))
        orders = self.auftraege()[0]["body"]["orders"]
        self.assertEqual([o["orderType"] for o in orders], ["LMT", "STP"])
        self.assertEqual(orders[0]["quantity"], 1.5)

    def test_ohne_schutz_nur_ein_ticket(self):
        s = self.broker.senden(Order("AAPL", "kauf", 3, "stop_limit", limit=101, stop=100))
        orders = self.auftraege()[0]["body"]["orders"]
        self.assertEqual(len(orders), 1)
        self.assertEqual((orders[0]["orderType"], orders[0]["price"], orders[0]["auxPrice"]), ("STOP_LIMIT", 101, 100))
        self.assertNotIn("Schutz", s.meldung)

    def test_unsinnige_schutzpreise_werden_nicht_gesendet(self):
        with self.assertRaises(BrokerFehler):
            self.broker.senden(Order("AAPL", "kauf", 1, stop_loss=105, take_profit=100))
        self.assertEqual(self.auftraege(), [])

    def test_harmlose_rueckfrage_wird_bestaetigt(self):
        self.gw.order_antwort = lambda body: rueckfrage(["o10151"], "Market orders can be risky.", "reply-1")
        self.gw.antworten_reply = [erfolg(3, "Submitted", 2001)]
        s = self.broker.senden(Order("AAPL", "kauf", 10, stop_loss=99, take_profit=103))
        antwort = self.gw.aufrufe("POST", "/iserver/reply/reply-1")
        self.assertEqual(len(antwort), 1)
        self.assertEqual(antwort[0]["body"], {"confirmed": True})
        self.assertEqual((s.id, s.status), ("2001", "offen"))

    def test_kette_harmloser_rueckfragen(self):
        self.gw.order_antwort = lambda body: rueckfrage(["o10152"], "Stop-Order-Risiko", "r1")
        self.gw.antworten_reply = [rueckfrage(["o10331"], "Stop-Order", "r2"), erfolg(1, "Submitted", 3001)]
        s = self.broker.senden(Order("AAPL", "kauf", 10))
        self.assertEqual(len(self.gw.aufrufe("POST", "/iserver/reply/r1")), 1)
        self.assertEqual(len(self.gw.aufrufe("POST", "/iserver/reply/r2")), 1)
        self.assertEqual(s.id, "3001")

    def test_unbekannte_rueckfrage_wird_nie_bestaetigt(self):
        text = 'The following order "BUY 100 AAPL NASDAQ.NMS @ 165.0" price exceeds \nthe Percentage constraint of 3%.\nAre you sure you want to submit this order?'
        for ids in (["o163"], ["o10151", "o163"], ["o354"], ["o383"], ["o451"], ["neu1"], []):
            self.gw.anfragen.clear()
            self.gw.order_antwort = lambda body, ids=ids: rueckfrage(ids, text, "reply-x")
            with self.assertRaises(BrokerFehler) as c:
                self.broker.senden(Order("AAPL", "kauf", 100, "limit", limit=165))
            self.assertIn("Percentage constraint", str(c.exception), ids)
            self.assertIn("nicht automatisch", str(c.exception))
            self.assertEqual([a for a in self.gw.anfragen if a["pfad"].startswith("/iserver/reply/")], [], ids)

    def test_endlose_rueckfragen_brechen_ab(self):
        self.gw.order_antwort = lambda body: rueckfrage(["o10151"], "immer wieder")
        self.gw.antworten_reply = [rueckfrage(["o10151"], "immer wieder")] * 10
        with self.assertRaisesRegex(BrokerFehler, "zu viele Rückfragen"):
            self.broker.senden(Order("AAPL", "kauf", 1))

    def test_fehlerantwort(self):
        self.gw.order_antwort = lambda body: {"error": "Order rejected: insufficient buying power"}
        with self.assertRaisesRegex(BrokerFehler, "insufficient buying power"):
            self.broker.senden(Order("AAPL", "kauf", 1))

    def test_server_rueckfrage_pending_submit(self):
        self.gw.order_antwort = lambda body: [{"order_id": "1645968998", "order_status": "PendingSubmit", "encrypt_message": "1"}]
        s = self.broker.senden(Order("AAPL", "kauf", 1, "limit", limit=350))
        self.assertEqual((s.id, s.status), ("1645968998", "offen"))
        self.assertIn("PendingSubmit", s.meldung)
        self.gw.order_antwort = lambda body: [{"orderId": 1, "reqId": "15138", "text": "Price Management Algo?",
                                                "options": ["Use on this order"], "type": "M"}]
        with self.assertRaisesRegex(BrokerFehler, "angehalten.*Price Management"):
            self.broker.senden(Order("AAPL", "kauf", 1, "limit", limit=350))

    def test_abgelehnte_order(self):
        self.gw.order_antwort = lambda body: [{"order_id": "7", "order_status": "Inactive"}]
        self.assertEqual(self.broker.senden(Order("AAPL", "kauf", 1)).status, "abgelehnt")

    def test_kein_echtgeld_ohne_ausdrueckliche_erlaubnis(self):
        self.gw.konto = "U7654321"
        with self.assertRaisesRegex(BrokerFehler, "kein Demokonto.*echtgeld_erlaubt"):
            self.neu().senden(Order("AAPL", "kauf", 1))
        self.assertEqual([a for a in self.gw.anfragen if a["methode"] == "POST" and a["pfad"].endswith("/orders")], [])
        s = self.neu(echtgeld_erlaubt=True).senden(Order("AAPL", "kauf", 1))
        self.assertEqual(s.id, "1001")
        self.assertEqual(self.gw.anfragen[-1]["pfad"], "/iserver/account/U7654321/orders")

    def test_kennung_wird_als_conid_benutzt(self):
        self.broker.senden(Order("EGAL", "kauf", 1, kennung="8314"))
        self.assertEqual(self.auftraege()[0]["body"]["orders"][0]["conid"], 8314)
        self.assertEqual(self.gw.aufrufe("GET", "/iserver/secdef/search"), [])

    def test_ausserhalb_der_handelszeit_nur_wenn_gewollt(self):
        self.neu(ausserhalb_handelszeit=True).senden(Order("AAPL", "kauf", 1, stop_loss=1))
        eltern, kind = self.auftraege()[0]["body"]["orders"]
        self.assertTrue(eltern["outsideRTH"])
        self.assertFalse(kind["outsideRTH"])


class OrdersUndStorno(GatewayBasis):
    def test_offene_orders(self):
        offen = self.broker.orders()
        self.assertEqual([o.id for o in offen], ["1234568790", "1234568791"])     # fremdes Konto und Gefüllte fehlen
        a, b = offen
        self.assertEqual((a.status, a.ausgefuehrt, a.preis, a.order.art, a.order.limit, a.order.seite, a.order.symbol),
                         ("teilweise", 4.0, 189.9, "limit", 190.0, "kauf", "AAPL"))
        self.assertEqual((b.status, b.order.art, b.order.stop, b.order.seite), ("offen", "stop", 180.0, "verkauf"))
        self.assertEqual(a.order.kennung, "265598")
        self.assertEqual(self.gw.orders_aufrufe, 2)                                  # erster Aufruf lädt nur

    def test_alle_orders(self):
        alle = self.broker.orders(nur_offene=False)
        self.assertEqual([o.status for o in alle], ["teilweise", "offen", "ausgefuehrt"])
        gefuellt = alle[2]
        self.assertEqual((gefuellt.order.art, gefuellt.preis, gefuellt.ausgefuehrt), ("markt", 161.5, 5.0))
        self.assertEqual(gefuellt.zeit.isoformat(), "2023-12-11T18:00:49+00:00")

    def test_stornieren(self):
        self.broker.stornieren("987654")
        a = self.gw.aufrufe("DELETE", f"/iserver/account/{KONTO}/order/987654")
        self.assertEqual(len(a), 1)
        self.assertIsNone(a[0]["body"])

    def test_stornieren_fehler(self):
        self.gw.storno_antwort = {"error": "OrderID 999 doesn't exist"}
        with self.assertRaisesRegex(BrokerFehler, "doesn't exist"):
            self.broker.stornieren("999")
        self.gw.storno_antwort = {"unerwartet": True}
        with self.assertRaisesRegex(BrokerFehler, "Unerwartete Antwort"):
            self.broker.stornieren("999")

    def test_alles_schliessen(self):
        schritte = self.broker.alles_schliessen()
        self.assertEqual(len(self.gw.aufrufe("DELETE", f"/iserver/account/{KONTO}/order/1234568790")), 1)
        text = "\n".join(schritte)
        self.assertIn("glattgestellt: AAPL +10", text)
        self.assertIn("glattgestellt: IBM -5", text)
        orders = [a["body"]["orders"][0] for a in self.gw.aufrufe("POST", f"/iserver/account/{KONTO}/orders")]
        self.assertEqual([(o["conid"], o["side"], o["quantity"]) for o in orders], [(265598, "SELL", 10), (8314, "BUY", 5)])


class Verschluesselung(unittest.TestCase):
    def test_pruefung_nur_fuer_lokale_adressen_aus(self):
        lokal = ["https://localhost:5000/v1/api", "https://127.0.0.1:5000/v1/api", "https://LOCALHOST:5000", "https://[::1]:5000/v1/api"]
        fremd = ["https://example.com/v1/api", "https://192.168.1.10:5000/v1/api", "https://localhost.evil.com:5000/v1/api",
                 "https://127.0.0.1.evil.com/v1/api", "https://localhost@evil.com/v1/api", "https://127.0.0.2:5000/v1/api",
                 "https://api.ibkr.com/v1/api", "https://notlocalhost:5000"]
        for url in lokal:
            k = ssl_kontext(url)
            self.assertEqual((k.verify_mode, k.check_hostname), (ssl.CERT_NONE, False), url)
        for url in fremd:
            k = ssl_kontext(url)
            self.assertEqual((k.verify_mode, k.check_hostname), (ssl.CERT_REQUIRED, True), url)

    @unittest.skipUnless(shutil.which("openssl"), "openssl nicht installiert")
    def test_echte_tls_verbindung_mit_selbstsigniertem_zertifikat(self):
        with tempfile.TemporaryDirectory() as tmp:
            zert, schluessel = str(Path(tmp) / "zert.pem"), str(Path(tmp) / "schluessel.pem")
            erzeugt = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", schluessel,
                                      "-out", zert, "-days", "1", "-subj", "/CN=localhost"], capture_output=True)
            if erzeugt.returncode != 0:
                self.skipTest("Testzertifikat konnte nicht erzeugt werden")
            lokal = GatewayAttrappe(tls=(zert, schluessel), adresse="127.0.0.1")
            fremd = GatewayAttrappe(tls=(zert, schluessel), adresse="127.0.0.2")
            try:
                for host in ("127.0.0.1", "localhost"):
                    b = IbkrBroker(f"https://{host}:{lokal.port}{PRAEFIX}", ratenbegrenzung=False)
                    self.assertEqual(b.pruefen()["konto"], KONTO, host)
                # gleiche Art Zertifikat, aber Adresse nicht lokal im Sinne der Regel: Prüfung bleibt an und schlägt fehl
                try:
                    b = IbkrBroker(fremd.url, ratenbegrenzung=False, timeout=5)
                    with self.assertRaisesRegex(BrokerFehler, "TLS-Fehler"):
                        b.pruefen()
                except OSError:
                    self.skipTest("127.0.0.2 ist hier nicht verfügbar")
                self.assertEqual(fremd.anfragen, [])             # es wurde nie ein HTTP-Request gesendet
            finally:
                lokal.schliessen()
                fremd.schliessen()


if __name__ == "__main__":
    unittest.main()
