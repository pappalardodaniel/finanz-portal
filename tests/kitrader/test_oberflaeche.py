"""Bedienoberfläche: Server im Thread. Seite, Schutz gegen fremde Seiten (Schlüssel, Origin, Host), Not-Aus mit
Papier-Broker, Journal, Historie, KI. Alles offline."""
import http.client
import json
import os
import re
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from kitrader import betrieb
from kitrader import oberflaeche
from kitrader.broker.basis import Kerze, Order
from kitrader.broker.papier import PapierBroker
from kitrader.einstellungen import mit_standard
from kitrader.historie.speicher import Speicher
from kitrader.journal import Journal
from kitrader.oberflaeche.server import ausduennen, kuerzen
from kitrader.risiko import Ergebnis

from .daten import MONTAG, Markt, Uhr

KI_ANTWORT = {n: {"richtung": r, "wahrscheinlichkeit": w, "begruendung": f"Grund {n}", "gegenargumente": f"Dagegen {n}",
                  "was_widerlegt": f"Falsch, wenn {n}"}
              for n, r, w in (("kurz", "steigt", 0.6), ("mittel", "seitwaerts", 0.5), ("lang", "faellt", 0.4))}


def tageskerzen(n=40):
    start = MONTAG - timedelta(days=n)
    return [Kerze(start + timedelta(days=i), 100 + i * 0.5, 101 + i * 0.5, 99 + i * 0.5, 100 + i * 0.5, 1000) for i in range(n)]


class ServerBasis(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ordner = Path(self._d.name)
        env = mock.patch.dict(os.environ, {"KITRADER_DATEN": str(self.ordner), "KITRADER_KONFIG": str(self.ordner / "e.toml")})
        env.start()
        self.addCleanup(env.stop)
        self.einst = mit_standard({"osiris": {"automatisch": False}, "kosten": {"gebuehr_fest": 1.0, "gebuehr_prozent": 0.0,
                                                                                  "schlupf_prozent": 0.0},
                                   "autopilot": {"symbole": ["ABC"]}})       # der Not-Aus fasst nur eigene Symbole an
        self.uhr = Uhr()
        self.markt = Markt(self.uhr)
        self.markt.setze("ABC", 99.9, 100.0)
        self.fragen_ki: list[str] = []

        def bauer():
            return PapierBroker(self.markt, self.einst, self.ordner / "papier.db", uhr=self.uhr)
        self.bauer = bauer

        def ki_treiber(prompt):
            self.fragen_ki.append(prompt)
            return json.dumps(KI_ANTWORT)
        self.app, self.server = oberflaeche.bauen(
            self.einst, self.ordner, port=0, broker_bauer=bauer, kursquelle=lambda: mock.Mock(kerzen=lambda *a: tageskerzen()),
            ki_treiber=ki_treiber)
        self.port = self.app.port
        self.faden = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.faden.start()
        self.addCleanup(self._stoppen)
        self.schluessel = self.app.schluessel

    def _stoppen(self):
        self.server.shutdown()
        self.server.server_close()
        self.faden.join(5)

    def position_eroeffnen(self):
        b = self.bauer()
        b.senden(Order("ABC", "kauf", 10, stop_loss=95.0, take_profit=110.0))
        b.beenden()

    def anfrage(self, methode, pfad, daten=None, kopf=None, schluessel=True, origin="ok", host=None):
        """Sendet eine Anfrage an den Server und gibt (Status, Kopf, Körper) zurück. Mit origin=None wird kein Origin gesendet."""
        h = {}
        if schluessel is True:
            h["X-Kitrader-Schluessel"] = self.schluessel
        elif schluessel:
            h["X-Kitrader-Schluessel"] = schluessel
        if origin == "ok":
            origin = f"http://127.0.0.1:{self.port}"
        if origin:
            h["Origin"] = origin
        if host:
            h["Host"] = host
        body = None
        if daten is not None:
            body = json.dumps(daten)
            h["Content-Type"] = "application/json"
        h.update(kopf or {})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            c.request(methode, pfad, body, h)
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def json(self, methode, pfad, **kw):
        status, kopf, body = self.anfrage(methode, pfad, **kw)
        return status, json.loads(body.decode("utf-8"))


class SeiteUndSchutzTest(ServerBasis):
    def test_get_liefert_die_seite_mit_schluessel_und_strengen_kopfzeilen(self):
        status, kopf, body = self.anfrage("GET", "/", schluessel=False, origin=None)
        text = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertTrue(kopf["Content-Type"].startswith("text/html"))
        self.assertIn(self.schluessel, text)
        self.assertNotIn("__SCHLUESSEL__", text)
        self.assertNotIn("__NONCE__", text)
        nonce = re.search(r"script-src 'nonce-([^']+)'", kopf["Content-Security-Policy"]).group(1)
        self.assertIn(f'<script nonce="{nonce}">', text)
        self.assertIn("frame-ancestors 'none'", kopf["Content-Security-Policy"])
        self.assertEqual(kopf["X-Frame-Options"], "DENY")
        self.assertEqual(kopf["Cache-Control"], "no-store")
        for reiter in ("Übersicht", "Journal", "Weltlage", "Historie", "KI"):
            self.assertIn(f">{reiter}</button>", text)
        self.assertIn("NOT-AUS", text)

    def test_rote_flaechen_und_links_sind_auch_im_dunkelmodus_lesbar(self):
        # Gefunden im echten Browser: im Dunkelmodus war var(--echt) ein helles Rosa (weiße Schrift darauf kaum lesbar),
        # und Links blieben dunkelblau auf dunklem Grund.
        text = self.anfrage("GET", "/", schluessel=False, origin=None)[2].decode("utf-8")
        for auswahl in (".modus.echt", "#notaus-banner"):
            regel = re.search(re.escape(auswahl) + r"\s*\{([^}]*)\}", text).group(1)
            self.assertIn("color: #fff", regel)
            self.assertRegex(regel, r"background: #[0-9a-f]{6}")           # feste dunkelrote Fläche, kein Farbwert je Thema
            self.assertNotIn("var(--echt)", regel.split("background")[1].split(";")[0])
        self.assertRegex(text, r"\ba \{ color: var\(--akzent\); \}")

    def test_nur_an_localhost_gebunden(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_keine_cors_freigabe(self):
        for pfad in ("/", "/api/info"):
            status, kopf, _ = self.anfrage("GET", pfad, origin="https://fremd.example")
            self.assertEqual(status, 403)
            self.assertNotIn("Access-Control-Allow-Origin", kopf)
        for origin in (None, "https://fremd.example", "ok"):        # Vorabfragen (OPTIONS) werden nie beantwortet
            status, kopf, _ = self.anfrage("OPTIONS", "/api/notaus", origin=origin)
            self.assertEqual(status, 501)
            self.assertNotIn("Access-Control-Allow-Origin", kopf)
        status, kopf, _ = self.anfrage("GET", "/api/info")           # auch die erlaubte Antwort trägt keine CORS-Kopfzeile
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", kopf)

    def test_fremder_host_wird_abgewiesen_dns_rebinding(self):
        for host in ("evil.example", f"evil.example:{self.port}", f"127.0.0.1.evil.example:{self.port}"):
            status, _, _ = self.anfrage("GET", "/", schluessel=False, origin=None, host=host)
            self.assertEqual(status, 403, host)
        status, _, _ = self.anfrage("GET", "/", schluessel=False, origin=None, host=f"localhost:{self.port}")
        self.assertEqual(status, 200)

    def test_api_braucht_den_schluessel(self):
        for schluessel in (False, "falsch", self.schluessel[:-1] + "x"):
            status, antwort = self.json("GET", "/api/uebersicht", schluessel=schluessel)
            self.assertEqual(status, 403)
            self.assertIn("Schlüssel", antwort["fehler"])
        status, antwort = self.json("GET", "/api/uebersicht")
        self.assertEqual(status, 200)
        self.assertEqual(antwort["modus_text"], "PAPIER")

    def test_post_ohne_schluessel_ist_verboten_und_tut_nichts(self):
        self.position_eroeffnen()
        status, antwort = self.json("POST", "/api/notaus", daten={}, schluessel=False)
        self.assertEqual(status, 403)
        status, antwort = self.json("POST", "/api/notaus", daten={}, schluessel="geraten")
        self.assertEqual(status, 403)
        self.assertFalse((self.ordner / "NOTAUS").exists())
        b = self.bauer()
        self.addCleanup(b.beenden)
        self.assertEqual(len(b.positionen()), 1)

    def test_post_mit_fremdem_origin_ist_verboten_auch_mit_richtigem_schluessel(self):
        self.position_eroeffnen()
        for origin in ("https://evil.example", "http://127.0.0.1:1", f"http://evil.example:{self.port}", "null"):
            status, antwort = self.json("POST", "/api/notaus", daten={}, origin=origin)
            self.assertEqual(status, 403, origin)
        status, antwort = self.json("POST", "/api/notaus", daten={}, origin=None)       # ohne Origin ebenfalls
        self.assertEqual(status, 403)
        status, antwort = self.json("POST", "/api/notaus", daten={}, host="evil.example")
        self.assertEqual(status, 403)
        status, antwort = self.json("POST", "/api/notaus", daten={}, kopf={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertFalse((self.ordner / "NOTAUS").exists())

    def test_post_verlangt_json(self):
        status, _, _ = self.anfrage("POST", "/api/notaus", daten=None, kopf={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        self.assertFalse((self.ordner / "NOTAUS").exists())

    def test_nur_der_papierweg_der_manuellen_order_existiert(self):
        # Unbekannte Wege bleiben 404; die Order-Wege gibt es nur unter /api/order/... und nur mit Schlüssel (und Origin).
        for pfad in ("/api/order", "/api/orders", "/api/senden", "/api/handeln"):
            status, _ = self.json("POST", pfad, daten={"symbol": "ABC", "seite": "kauf", "menge": 1})
            self.assertEqual(status, 404, pfad)
        status, _ = self.json("GET", "/api/notaus")                  # Not-Aus nur per POST
        self.assertEqual(status, 404)
        for pfad in ("/api/order/vorschau", "/api/order/senden", "/api/order/schliessen"):
            self.assertEqual(self.json("GET", pfad)[0], 404, pfad)    # nur POST
            self.assertEqual(self.json("POST", pfad, daten={}, schluessel=False)[0], 403, pfad)
            self.assertEqual(self.json("POST", pfad, daten={}, origin=None)[0], 403, pfad)
        quelltext = (Path(oberflaeche.server.__file__)).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"broker\.senden\(", quelltext))   # der Server sendet nie selbst: nur über manuell.py (Risikoprüfung)


class NotAusTest(ServerBasis):
    def test_notaus_mit_papier_broker_schliesst_alles_und_sperrt(self):
        self.position_eroeffnen()
        status, u = self.json("GET", "/api/uebersicht")
        self.assertEqual((u["notaus"]["aktiv"], len(u["positionen"]), len(u["orders"])), (False, 1, 2))   # Stop und Ziel
        status, e = self.json("POST", "/api/notaus", daten={})
        self.assertEqual(status, 200)
        self.assertTrue(e["ok"], e)
        self.assertEqual(e["modus"], "papier")
        schritte = "\n".join(e["schritte"])
        self.assertIn("storniert", schritte)
        self.assertIn("glattgestellt: ABC +10", schritte)
        self.assertTrue((self.ordner / "NOTAUS").is_file())
        b = self.bauer()
        self.addCleanup(b.beenden)
        self.assertEqual(b.positionen(), [])
        self.assertEqual(b.orders(), [])
        status, u = self.json("GET", "/api/uebersicht")
        self.assertTrue(u["notaus"]["aktiv"])
        self.assertTrue(any(p["art"] == "notaus" for p in u["protokoll"]))

    def test_notaus_meldet_fehler_ehrlich(self):
        def kaputt():
            raise RuntimeError("Broker weg")
        self.app.broker_bauer = kaputt
        status, e = self.json("POST", "/api/notaus", daten={})
        self.assertEqual(status, 200)
        self.assertFalse(e["ok"])
        self.assertTrue((self.ordner / "NOTAUS").is_file())            # gesperrt ist der Handel trotzdem
        self.assertTrue(any(s.startswith("FEHLER") for s in e["schritte"]))
        self.assertIn("Broker weg", e["fehler"])

    def test_notaus_kann_nicht_aufgehoben_werden(self):
        self.json("POST", "/api/notaus", daten={})
        for pfad in ("/api/notaus/aufheben", "/api/notaus_aufheben", "/api/aufheben"):
            status, _ = self.json("POST", pfad, daten={})
            self.assertEqual(status, 404)
        self.assertTrue((self.ordner / "NOTAUS").is_file())


class UebersichtTest(ServerBasis):
    def test_betriebsart_konto_und_grenzen(self):
        self.position_eroeffnen()
        status, u = self.json("GET", "/api/uebersicht")
        self.assertEqual((u["modus"], u["modus_text"], u["echtes_geld"]), ("papier", "PAPIER", False))
        b = self.bauer()
        self.addCleanup(b.beenden)
        self.assertAlmostEqual(u["konto"]["gesamtwert"], b.konto().gesamtwert, places=2)
        self.assertLess(u["konto"]["gesamtwert"], 10000)              # Gebühr bezahlt
        namen = {g["name"]: g for g in u["grenzen"]}
        self.assertEqual(namen["Offene Positionen"]["aktuell"], "1")
        self.assertEqual(namen["Offene Positionen"]["grenze"], "3")
        self.assertEqual(namen["Tagesverlust"]["status"], "ok")
        self.assertIn("Größte Position", namen)

    def test_tagesverlustgrenze_erreicht_wird_angezeigt(self):
        j = Journal(self.ordner / "journal.db", self.einst)
        datum = betrieb.jetzt_utc().date().isoformat()
        j.tagesstand(datum, 10500.0)                                 # Startwert deutlich über dem Gesamtwert
        j.tag_sperren(datum, "Test")
        j.beenden()
        status, u = self.json("GET", "/api/uebersicht")
        verlust = {g["name"]: g for g in u["grenzen"]}["Tagesverlust"]
        self.assertEqual(verlust["status"], "erreicht")
        self.assertTrue(u["tag_gesperrt"])

    def test_betriebsart_echt_ist_farbig_markiert_und_warnt_vor_demokonto(self):
        u = betrieb.uebersicht(mit_standard({"modus": "echt"}), self.ordner, bauer=self.bauer)
        self.assertEqual((u["modus_text"], u["echtes_geld"]), ("ECHTGELD", True))
        self.assertFalse(u["freigabe"]["ok"])                         # keine Freigabedatei
        self.assertTrue(any("Demokonto" in w for w in u["warnungen"]))
        u = betrieb.uebersicht(mit_standard({"modus": "demo"}), self.ordner, bauer=self.bauer)
        self.assertEqual(u["modus_text"], "DEMO")

    def test_broker_ausfall_laesst_betriebsart_und_notaus_stand_sichtbar(self):
        def kaputt():
            raise RuntimeError("keine Verbindung")
        u = betrieb.uebersicht(self.einst, self.ordner, bauer=kaputt)
        self.assertEqual(u["modus_text"], "PAPIER")
        self.assertIsNone(u["konto"])
        self.assertIn("keine Verbindung", u["fehler"][0])
        self.assertIn("notaus", u)


class JournalHistorieKiTest(ServerBasis):
    def test_journal_zeigt_gruende_und_trefferquote_mit_n(self):
        j = Journal(self.ordner / "journal.db", self.einst, self.uhr)
        j.entscheidung("papier", "ABC", "ausbruch", None, Ergebnis(False, ["Tagesverlustgrenze erreicht", "Markt geschlossen"]),
                       Order("ABC", "kauf", 5, notiz="x"), "abgelehnt")
        j.entscheidung("papier", "ABC", "ausbruch", None, Ergebnis(True), Order("ABC", "kauf", 5))
        j.einschaetzung("ABC", "steigt", "1d", "Test", 100.0, zeit=MONTAG - timedelta(days=3))
        j.auswerten(lambda w, z: 105.0, MONTAG)
        j.beenden()
        status, d = self.json("GET", "/api/journal?anzahl=10")
        self.assertEqual(status, 200)
        neu, alt = d["entscheidungen"]                               # neueste zuerst
        self.assertTrue(neu["pruefung"])
        self.assertFalse(alt["pruefung"])
        self.assertEqual(alt["gruende"], ["Tagesverlustgrenze erreicht", "Markt geschlossen"])
        eins = next(q for q in d["trefferquote"] if q["horizont"] == "1d")
        self.assertEqual((eins["n"], eins["treffer"], eins["quote"]), (1, 1, 100.0))
        self.assertEqual(next(q for q in d["trefferquote"] if q["horizont"] == "1J")["n"], 0)

    def test_historie_reihe_ereignisse_und_studie(self):
        with Speicher(self.ordner) as sp:
            tage = [(f"{j}-{m:02d}-15", 100.0 + (j - 1900) + m * 0.1) for j in range(1900, 1960) for m in range(1, 13)]
            sp.schreiben("dow", tage, "Testdaten")
        status, d = self.json("GET", "/api/historie/reihen")
        dow = next(r for r in d["reihen"] if r["id"] == "dow")
        self.assertEqual((dow["geladen"], dow["anzahl"]), (True, 720))
        status, d = self.json("GET", "/api/historie/reihe?id=dow&von=1930&bis=1935&max=900")
        self.assertEqual((status, d["anzahl_gesamt"], d["ausgeduennt"]), (200, 72, False))
        status, d = self.json("GET", "/api/historie/reihe?id=dow&max=100")
        self.assertTrue(d["ausgeduennt"])
        self.assertLessEqual(len(d["punkte"]), 110)
        self.assertEqual(d["punkte"][0][0], "1900-01-15")
        self.assertEqual(d["punkte"][-1][0], "1959-12-15")
        status, e = self.json("GET", "/api/historie/ereignisse")
        self.assertGreater(len(e["ereignisse"]), 100)
        self.assertIn("boersencrash", e["arten"])
        status, s = self.json("GET", "/api/historie/studie?id=1929-boersenkrach&reihe=dow")
        self.assertEqual(status, 200, s)
        self.assertIn("1", s["horizonte"])
        self.assertEqual(s["ereignis"]["id"], "1929-boersenkrach")
        self.assertEqual(self.json("GET", "/api/historie/studie?id=gibt-es-nicht")[0], 404)
        self.assertEqual(self.json("GET", "/api/historie/reihe?id=xyz")[0], 400)
        self.assertEqual(self.json("GET", "/api/historie/reihe?id=dow&von=abc")[0], 400)

    def test_lange_begruendung_wird_mit_punkten_gekuerzt_ohne_halbes_trennzeichen(self):
        # Gefunden im Browser: KI-Begründungen endeten mitten im Wort bzw. mit einem losen " |"
        self.assertEqual(kuerzen("kurz", 10), "kurz")
        lang = "[KI] " + "Wort " * 30 + "| Dagegen: x"
        k = kuerzen(lang, 20)
        self.assertEqual(len(k), 20)
        self.assertTrue(k.endswith("…"))
        self.assertEqual(kuerzen("a" * 5 + " | " + "b" * 30, 8), "aaaaa…")

    def test_ausduennen_behaelt_tiefs_und_hochs(self):
        werte = [(f"{1000 + i}", 100.0) for i in range(5000)]
        werte[2500] = ("2500", 1.0)
        werte[3333] = ("3333", 900.0)
        klein = ausduennen(werte, 200)
        self.assertLessEqual(len(klein), 210)
        self.assertIn(("2500", 1.0), klein)
        self.assertIn(("3333", 900.0), klein)
        self.assertEqual((klein[0], klein[-1]), (werte[0], werte[-1]))

    def test_weltlage_ohne_schnappschuss(self):
        status, d = self.json("GET", "/api/weltlage")
        self.assertEqual(status, 200)
        self.assertIsNone(d["stand"])
        self.assertIn("OSIRIS", d["text"])

    def test_ki_mit_treiber_aus_zeigt_nur_den_kontext(self):
        status, d = self.json("POST", "/api/ki", daten={"wert": "ABC", "treiber": "aus"})
        self.assertEqual(status, 200, d)
        self.assertEqual(d["prognosen"], {})
        self.assertIn("KONTEXT für ABC", d["text"])
        self.assertEqual(self.fragen_ki, [])                          # nichts gefragt
        j = Journal(self.ordner / "journal.db", self.einst)
        self.addCleanup(j.beenden)
        self.assertEqual(j.einschaetzungen(), [])

    def test_ki_mit_treiber_aus_den_einstellungen_speichert_ins_journal(self):
        status, d = self.json("POST", "/api/ki", daten={"wert": "ABC"})
        self.assertEqual(status, 200, d)
        self.assertEqual(set(d["prognosen"]), {"kurz", "mittel", "lang"})
        self.assertEqual(d["prognosen"]["kurz"]["richtung"], "steigt")
        self.assertEqual(len(self.fragen_ki), 1)
        j = Journal(self.ordner / "journal.db", self.einst)
        self.addCleanup(j.beenden)
        self.assertEqual(len(j.einschaetzungen()), 3)

    def test_ki_prueft_eingaben(self):
        for wert in ("", "A B", "x" * 40, "A;rm -rf", "<script>"):
            self.assertEqual(self.json("POST", "/api/ki", daten={"wert": wert})[0], 400, wert)
        self.assertEqual(self.json("POST", "/api/ki", daten={"wert": "ABC", "treiber": "anthropic"})[0], 400)

    def test_ki_ohne_kerzen_gibt_verstaendliche_meldung(self):
        def keine(*a):
            raise RuntimeError("Gateway nicht erreichbar")
        self.app.kursquelle = lambda: mock.Mock(kerzen=keine)
        status, d = self.json("POST", "/api/ki", daten={"wert": "ABC"})
        self.assertEqual(status, 502)
        self.assertIn("Gateway nicht erreichbar", d["fehler"])


if __name__ == "__main__":
    unittest.main()
