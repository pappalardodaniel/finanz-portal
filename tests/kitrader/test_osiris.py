"""OSIRIS: Abruf, Schnappschuss, Lage und Veränderung (offen: nachgebildeter Server auf 127.0.0.1, kein Internet)."""
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from kitrader import osiris
from kitrader.__main__ import main
from kitrader.einstellungen import mit_standard

from .daten import MONTAG, Uhr


def feed_daten(variante: int = 1) -> dict[str, dict]:
    """Erfundene Antworten im Aufbau der echten Feeds (Pfad -> Antwort). Variante 2 ist eine spätere Lage."""
    v2 = variante == 2
    ereignisse = [
        {"id": "gdacs-0", "lat": 32.5, "lng": 94.0, "type": "earthquake", "url": "http://gdacs.example/eq1",
         "name": "Red earthquake (Magnitude 7M, Depth:18km) in China 22/10/2026 14:33 UTC"},
        {"id": "gdacs-1", "lat": 14.8, "lng": -178.0, "type": "weather", "url": "http://gdacs.example/tc1",
         "name": "Orange notification for tropical cyclone ABC-26."},
        {"id": "gdacs-2", "lat": -22.0, "lng": 123.0, "type": "wildfire", "url": "http://gdacs.example/wf1",
         "name": "Green forest fire notification in Australia"},
        {"id": "gdacs-3", "lat": -16.7, "lng": 48.7, "type": "drought", "url": "http://gdacs.example/dr1",
         "name": "Drought is on going in Madagascar"},
    ]
    meldungen = [
        {"id": "m1", "title": "Raketenangriff auf Hafenstadt", "summary": "", "published": "2026-03-02T09:30:00.000Z",
         "source_name": "Quelle Eins", "bloc": "western", "lean": "Broadcaster", "risk_score": 9, "also_reported_by": ["x"]},
        {"id": "m2", "title": "Ein ruhiger Tag an der Börse in Frankfurt mit wenig Umsatz und keinen Nachrichten", "summary": "",
         "published": "2026-03-02T09:50:00.000Z", "source_name": "Quelle Zwei", "bloc": "regional", "risk_score": 1, "also_reported_by": []},
        {"id": "m3", "title": "Alt", "summary": "Sehr alte Meldung", "published": "2026-02-20T09:00:00.000Z",
         "source_name": "Quelle Drei", "bloc": "russian", "risk_score": 10, "also_reported_by": []},
    ]
    if v2:
        ereignisse.append({"id": "gdacs-4", "lat": 36.0, "lng": 140.0, "type": "earthquake", "url": "http://gdacs.example/eq2",
                           "name": "Orange earthquake (Magnitude 6.5M) in Japan"})
        meldungen.append({"id": "m4", "title": "Neue Sperrung der Meerenge gemeldet", "summary": "", "published": "2026-03-02T11:40:00.000Z",
                          "source_name": "Quelle Vier", "bloc": "independent", "risk_score": 7, "also_reported_by": []})
    return {
        "/api/stats": {"stats": {"flights": 10, "incidents": len(ereignisse)}, "timestamp": "2026-03-02T10:00:00Z"},
        "/api/conflicts": {"zones": [
            {"id": "ukraine", "label": "UKRAINE WAR", "severity": "war", "headlineCount": 0},
            {"id": "red-sea", "label": "RED SEA THREAT", "severity": "high", "headlineCount": 2}],
            "zoneSource": "curated", "timestamp": "2026-03-02T10:00:00Z"},
        "/api/gdelt": {"events": ereignisse, "total": len(ereignisse), "source": "GDACS RSS API"},
        "/api/country-risk": {"countries": [{"code": "UA", "risk_score": 85, "risk_level": "CRITICAL", "tags": ["active_conflict"],
                                             "quake_magnitude": 0}, {"code": "JP", "risk_score": 30, "risk_level": "LOW", "quake_magnitude": 6.5}],
                              "methodology": {"basis": "editorial"}, "open_exchanges": 3},
        "/api/news": {"news": meldungen, "total": len(meldungen)},
        "/api/markets": {"indices": {"VIX": {"price": 35.0 if v2 else 19.0, "change_percent": 1.5, "market_open": True},
                                     "S&P 500": {"price": 7000.0, "change_percent": -0.2}},
                         "oil": {"Brent Crude": {"price": 103.0 if v2 else 100.0, "change_percent": 3.0}},
                         "stocks": {}, "commodities": {}, "crypto": {}, "fx": {}, "scm_alerts": []},
        "/api/maritime": {"chokepoints": [{"name": "Strait of Hormuz", "risk": "CRITICAL" if v2 else "HIGH", "traffic": "21M bpd oil | LIVE SHIPS: 0"},
                                          {"name": "Panama Canal", "risk": "LOW", "traffic": "5% world trade"}],
                          "ports": [{"name": "Shanghai", "congestion": "NORMAL"},
                                    {"name": "Rotterdam", "congestion": "HIGH" if v2 else "NORMAL", "dwell_time": "4 Days"}],
                          "total_ships": 0},
        "/api/earthquakes": {"earthquakes": [{"id": "q1", "magnitude": 7.1, "place": "Nahe Testort", "time": 1772445000000, "tsunami": 1},
                                             {"id": "q2", "magnitude": 4.6, "place": "Irgendwo", "time": 1772445600000, "tsunami": 0}]
                             + ([{"id": "q3", "magnitude": 5.4, "place": "Neu", "time": 1772450000000, "tsunami": 0}] if v2 else [])},
        "/api/weather": {"events": [{"title": "Hurricane Test", "severity": "high"}, {"title": "Regen", "severity": "low"}]},
        "/api/cyber-threats": {"threats": [{"id": "CVE-1", "name": "Test Lücke", "vendor": "Firma", "severity": "CRITICAL", "date": "2026-03-01"}],
                               "stats": {"threat_level": "CRITICAL", "active_cves": 1}},
        "/api/radar": {"outages": [{"country": "KW"}, {"country": "VE"}]},
        "/api/scm-suppliers": {"suppliers": [{"name": "A"}], "critical_count": 0, "degraded_signals": []},
    }


class OsirisServer:
    """Nachgebildeter OSIRIS auf 127.0.0.1. `antworten`: Pfad -> (Status, Bytes); `anfragen`: (Pfad, User-Agent)."""

    def __init__(self, variante: int = 1):
        self.anfragen: list[tuple[str, str]] = []
        self.antworten: dict[str, tuple[int, bytes]] = {}
        self.setze(variante)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                server.anfragen.append((self.path, self.headers.get("User-Agent", "")))
                status, inhalt = server.antworten.get(self.path, (404, b'{"error":"nicht vorhanden"}'))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(inhalt)))
                self.end_headers()
                self.wfile.write(inhalt)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.adresse = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def setze(self, variante: int) -> None:
        self.antworten = {p: (200, json.dumps(d).encode()) for p, d in feed_daten(variante).items()}

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class OsirisTestBasis(unittest.TestCase):
    def setUp(self):
        self.server = OsirisServer()
        self.addCleanup(self.server.stop)
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.dir = Path(self._d.name)
        self.uhr = Uhr(MONTAG)
        self.pausen: list[float] = []
        self.einst = mit_standard({"osiris": {"adresse": self.server.adresse, "pause_s": 0.25}})
        self.o = osiris.Osiris(self.einst, self.dir / "osiris.db", uhr=self.uhr, schlafen=self.pausen.append)
        self.addCleanup(self.o.beenden)


class AbrufTest(OsirisTestBasis):
    def test_abrufen_holt_alle_feeds_hoeflich_und_speichert(self):
        erg = self.o.abrufen()
        self.assertTrue(erg["neu"])
        self.assertEqual({p for p, _ in self.server.anfragen}, set(osiris.FEEDS.values()))
        self.assertEqual(len(self.server.anfragen), len(osiris.FEEDS))              # jeder Feed genau einmal
        self.assertTrue(all("kitrader" in ua and "Python-urllib" not in ua for _, ua in self.server.anfragen))
        self.assertEqual(self.pausen, [0.25] * (len(osiris.FEEDS) - 1))             # Pause zwischen den Abrufen
        self.assertTrue(all(f["status"] == 200 and not f["fehler"] for f in erg["feeds"].values()))
        gespeichert = self.o.feeds(erg["id"])
        self.assertEqual(gespeichert["maerkte"]["oil"]["Brent Crude"]["price"], 100.0)
        self.assertEqual(set(gespeichert), set(osiris.FEEDS))

    def test_mindestabstand_und_erzwingen(self):
        self.o.abrufen()
        n = len(self.server.anfragen)
        self.uhr.weiter(seconds=100)
        zweiter = self.o.abrufen()
        self.assertFalse(zweiter["neu"])
        self.assertEqual(len(self.server.anfragen), n)                              # nichts neu geholt
        self.assertTrue(self.o.abrufen(erzwingen=True)["neu"])
        self.assertEqual(len(self.server.anfragen), 2 * n)
        self.uhr.weiter(seconds=400)
        self.assertTrue(self.o.abrufen()["neu"])                                    # älter als mindestabstand_s

    def test_ausgefallene_feeds_werden_vermerkt_der_rest_gespeichert(self):
        self.server.antworten["/api/news"] = (503, b"")
        self.server.antworten["/api/radar"] = (200, b"<html>kein json</html>")
        erg = self.o.abrufen()
        self.assertEqual(erg["feeds"]["nachrichten"]["fehler"], "HTTP 503")
        self.assertIn("JSON", erg["feeds"]["netzausfaelle"]["fehler"])
        gespeichert = self.o.feeds(erg["id"])
        self.assertNotIn("nachrichten", gespeichert)
        self.assertIn("maerkte", gespeichert)
        text = self.o.lage().text
        self.assertIn("Nicht abrufbar in diesem Schnappschuss: nachrichten, netzausfaelle", text)
        self.assertNotIn("Meldungen:", text)

    def test_alles_ausgefallen_wirft_und_speichert_nichts(self):
        self.server.antworten.clear()
        with self.assertRaises(osiris.OsirisFehler) as c:
            self.o.abrufen()
        self.assertIn("keinen einzigen Feed", str(c.exception))
        self.assertIsNone(self.o.lage().stand)

    def test_nicht_erreichbarer_server(self):
        self.server.stop()
        o = osiris.Osiris(mit_standard({"osiris": {"adresse": self.server.adresse, "zeitlimit_s": 2}}), self.dir / "x.db",
                          schlafen=lambda s: None)
        self.addCleanup(o.beenden)
        with self.assertRaises(osiris.OsirisFehler):
            o.abrufen()

    def test_alte_schnappschuesse_werden_geloescht(self):
        self.o.cfg["behalten_tage"] = 2
        a = self.o.abrufen()["id"]
        self.uhr.weiter(days=3)
        b = self.o.abrufen()["id"]
        self.assertEqual(self.o.feeds(a), {})                                       # a ist weg
        self.assertIn("maerkte", self.o.feeds(b))


class LageTest(OsirisTestBasis):
    def test_ohne_schnappschuss(self):
        lage = self.o.lage()
        self.assertIsNone(lage.stand)
        self.assertIn("noch kein Schnappschuss", lage.text)

    def test_lage_nennt_fakten_mit_zeit_und_quelle(self):
        self.o.abrufen()
        lage = self.o.lage()
        t = lage.text
        self.assertEqual(lage.stand, MONTAG)
        self.assertIn("Stand 2026-03-02 10:00 UTC", t)
        self.assertIn(self.server.adresse, t)
        self.assertIn("4 aktuelle Meldungen", t)                                    # Vorfälle
        self.assertIn("Erdbeben 1", t)
        self.assertIn("Asien 1 (Erdbeben 1)", t)                                    # Region nach Koordinaten
        self.assertIn("Ozeanien 1 (Waldbrand 1)", t)
        self.assertIn("[Rot] Asien: Red earthquake", t)
        self.assertIn("[Orange]", t)
        self.assertIn("Strait of Hormuz: HIGH", t)                                  # Engpass mit OSIRIS-Einstufung
        self.assertIn("Stufe „war“: UKRAINE WAR", t)
        self.assertIn("02.03. 09:30 UTC | Quelle Eins (western) | Score 9 | Raketenangriff auf Hafenstadt", t)
        self.assertNotIn("Sehr alte Meldung", t)                                    # älter als 48 h vor dem Feed-Ende: nicht unter den Top
        self.assertIn("Brent Crude 100,00 (3,00 %)", t)
        self.assertIn("M7.1 Nahe Testort", t)
        self.assertIn("Tsunami-Flag", t)
        self.assertIn("kein früherer Schnappschuss", t)
        # keine Deutung: nichts Wertendes wie "bullisch", "Kaufsignal", "Gefahr für die Börse"
        for wort in ("bullisch", "bärisch", "Kaufsignal", "Verkaufssignal", "empfehle"):
            self.assertNotIn(wort, t)

    def test_veraenderung_gegenueber_dem_vorigen_schnappschuss(self):
        self.o.abrufen()
        self.server.setze(2)
        self.uhr.weiter(hours=2)
        self.o.abrufen()
        lage = self.o.lage()
        t = lage.text
        self.assertEqual(lage.vorher, MONTAG)
        self.assertIn("Veränderung seit dem Schnappschuss von 2026-03-02 10:00 UTC (vor 2,0 h)", t)
        self.assertIn("Vorfälle gesamt +1 (jetzt 5)", t)
        self.assertIn("neu Orange/Rot: Orange earthquake (Magnitude 6.5M) in Japan", t)
        self.assertIn("Meldungen: 1 neu im Feed, davon 1 mit Score ≥ 5", t)
        self.assertIn("Engpass Strait of Hormuz: HIGH → CRITICAL", t)
        self.assertIn("Hafen Rotterdam: NORMAL → HIGH", t)
        self.assertIn("Brent Crude 3,00 %", t)
        self.assertIn("Erdbeben M≥5 neu: 1", t)

    def test_unveraenderte_lage_meldet_nichts_neues(self):
        self.o.abrufen()
        self.uhr.weiter(hours=1)
        self.o.abrufen()
        t = self.o.lage().text
        self.assertIn("Vorfälle gesamt +0", t)
        self.assertIn("keine neuen Orange/Rot", t)
        self.assertIn("Meldungen: 0 neu im Feed", t)
        self.assertIn("(≥ 0,5 %): keine", t)

    def test_modulfunktionen_und_alter(self):
        osiris.abrufen(self.einst, pfad=self.dir / "m.db", uhr=self.uhr, schlafen=lambda s: None)
        lage = osiris.lage(self.einst, pfad=self.dir / "m.db", uhr=self.uhr)
        self.assertEqual(lage.alter(MONTAG + timedelta(hours=3)), timedelta(hours=3))


class VerdichtenTest(unittest.TestCase):
    def test_kaputte_und_leere_feeds_werfen_nicht(self):
        for roh in ({}, {n: {} for n in osiris.FEEDS}, {n: [] for n in osiris.FEEDS},
                    {"maerkte": {"indices": {"VIX": "kaputt", "X": {"price": "abc"}}}, "vorfaelle": {"events": [None, 3, {"lat": "x"}]},
                     "nachrichten": {"news": [{"published": "gestern", "risk_score": "viel"}]},
                     "erdbeben": {"earthquakes": [{"magnitude": None, "time": "x"}]}}):
            v = osiris.verdichten(roh)
            osiris._text(v, v, MONTAG, MONTAG - timedelta(hours=1), "http://x")      # darf nicht abstürzen

    def test_region_nach_koordinaten(self):
        self.assertEqual(osiris._region(48.5, 31.2), "Europa")
        self.assertEqual(osiris._region(26.5, 56.2), "Naher Osten")
        self.assertEqual(osiris._region(-33.9, 151.2), "Ozeanien")
        self.assertEqual(osiris._region(35.7, 139.7), "Asien")
        self.assertEqual(osiris._region(40.7, -74.0), "Nordamerika")
        self.assertEqual(osiris._region(0.0, -150.0), "Ozean/Sonstige")
        self.assertEqual(osiris._region(None, 5), "unbekannt")


class KommandozeileTest(OsirisTestBasis):
    def setUp(self):
        super().setUp()
        (self.dir / "e.toml").write_text(f'[osiris]\nadresse = "{self.server.adresse}"\npause_s = 0\n')
        patch = mock.patch.dict(os.environ, {"KITRADER_DATEN": str(self.dir / "daten"), "KITRADER_KONFIG": str(self.dir / "e.toml")})
        patch.start()
        self.addCleanup(patch.stop)

    def lauf(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_osiris_abrufen_und_lage(self):
        code, out, _ = self.lauf("osiris", "lage")
        self.assertIn("noch kein Schnappschuss", out)
        code, out, err = self.lauf("osiris", "abrufen")
        self.assertEqual(code, 0, err)
        self.assertIn("Schnappschuss #1", out)
        self.assertIn("nachrichten", out)
        code, out, _ = self.lauf("osiris", "abrufen")
        self.assertIn("jünger als 300 s", out)
        code, out, _ = self.lauf("osiris", "lage")
        self.assertEqual(code, 0)
        self.assertIn("OSIRIS-Lage, Stand", out)
        code, out, _ = self.lauf("osiris", "lage", "--json")
        self.assertEqual(json.loads(out)["schifffahrt"]["engpaesse"][0]["name"], "Strait of Hormuz")

    def test_abrufen_ohne_server_gibt_lesbaren_fehler(self):
        self.server.antworten.clear()
        code, _, err = self.lauf("osiris", "abrufen")
        self.assertEqual(code, 2)
        self.assertIn("keinen einzigen Feed", err)


if __name__ == "__main__":
    unittest.main()
