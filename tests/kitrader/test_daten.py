"""Kerzen aus Abschlüssen (daten.py): Verdichten, seitenweises Holen, Fortsetzen, Ratenbegrenzung. Alles mit nachgebildeten Antworten."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kitrader import daten
from kitrader.__main__ import main
from kitrader.backtest import kerzen_aus_csv

T0 = 1_790_000_000 - 1_790_000_000 % 3600        # eine volle Stunde (UTC)


def abschluss(t, preis, menge=1.0, nr=0):
    return [f"{preis:.1f}", f"{menge:.8f}", float(t), "b", "m", "", nr]


class FalscherKraken:
    """Gibt Abschlüsse seitenweise heraus wie /0/public/Trades (since = Nanosekunden-Marke = Zeit des letzten Abschlusses)."""

    def __init__(self, abschluesse, jetzt=None):
        self.alle = abschluesse
        self.aufrufe = []
        self.fehler = []                    # Ausnahmen, die vor den nächsten Antworten geworfen werden
        self.paar_name = "XBTEUR"

    def __call__(self, methode, parameter):
        self.aufrufe.append((methode, dict(parameter)))
        if self.fehler:
            raise self.fehler.pop(0)
        if methode == "AssetPairs":
            if "pair" not in parameter:          # ganze Paarliste (für Vorschläge)
                return {"XXBTZEUR": {"altname": "XBTEUR", "wsname": "XBT/EUR", "base": "XXBT", "quote": "ZEUR"},
                        "XETHZEUR": {"altname": "ETHEUR", "wsname": "ETH/EUR", "base": "XETH", "quote": "ZEUR"}}
            if parameter["pair"] != "XBTEUR":
                raise daten.DatenFehler("Kraken lehnt die Anfrage ab: EQuery:Unknown asset pair")
            return {"XXBTZEUR": {"altname": "XBTEUR", "wsname": "XBT/EUR"}}
        assert methode == "Trades"
        seit = int(parameter["since"]) / 1e9
        rest = [a for a in self.alle if a[2] > seit]
        seite = rest[:daten.SEITE]
        letzte = int(seite[-1][2] * 1e9) if seite else int(parameter["since"])
        return {"XXBTZEUR": seite, "last": str(letzte)}


class VerdichterTest(unittest.TestCase):
    def test_ohlcv_einer_kerze(self):
        v = daten.Verdichter(300)
        v.zufuegen([abschluss(T0 + 10, 100, 1), abschluss(T0 + 60, 105, 2), abschluss(T0 + 120, 98, 0.5),
                    abschluss(T0 + 299, 101, 1.5)])
        self.assertEqual(v.fertig, [])                      # die laufende Kerze ist noch nicht fertig
        v.zufuegen([abschluss(T0 + 300, 102, 1)])
        self.assertEqual(v.fertig, [(T0, 100.0, 105.0, 98.0, 101.0, 5.0)])
        self.assertEqual(v.offen, [T0 + 300, 102.0, 102.0, 102.0, 102.0, 1.0])

    def test_kerzengrenzen_und_luecken(self):
        v = daten.Verdichter(300)
        v.zufuegen([abschluss(T0, 100), abschluss(T0 + 600, 110), abschluss(T0 + 900, 120), abschluss(T0 + 1200, 125)])
        self.assertEqual([k[0] for k in v.fertig], [T0, T0 + 600, T0 + 900])
        self.assertEqual(v.luecken(), 1)                    # die Kerze bei T0 + 300 fehlt (kein Abschluss)

    def test_stundenkerze_aus_denselben_abschluessen(self):
        v = daten.Verdichter(3600)
        v.zufuegen([abschluss(T0 + 5 * 60 * i, 100 + i, 1) for i in range(13)])     # 13 Abschlüsse über eine Stunde
        self.assertEqual(len(v.fertig), 1)
        t, o, h, l, c, vol = v.fertig[0]
        self.assertEqual((t, o, h, l, c, vol), (T0, 100.0, 111.0, 100.0, 111.0, 12.0))

    def test_unsinnige_preise_werden_uebergangen(self):
        v = daten.Verdichter(300)
        v.zufuegen([abschluss(T0, 100), abschluss(T0 + 1, 0.0), abschluss(T0 + 2, 99)])
        self.assertEqual(v.offen[1:5], [100.0, 100.0, 99.0, 99.0])

    def test_verspaeteter_abschluss_geht_in_die_laufende_kerze(self):
        v = daten.Verdichter(300)
        v.zufuegen([abschluss(T0 + 310, 100), abschluss(T0 + 100, 90)])
        self.assertEqual(v.fertig, [])
        self.assertEqual(v.offen[3], 90.0)


class HolenTest(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ordner = Path(self._d.name)
        self.schlaf = []
        # 2 Stunden Abschlüsse alle 20 Sekunden, Preis steigt; jetzt = 2 Stunden später
        self.abschluesse = [abschluss(T0 + 1 + 20 * i, 100 + i * 0.1, 0.5, i + 1) for i in range(360)]
        self.jetzt = T0 + 2 * 3600 + 30

    def holen(self, kraken, **kw):
        args = dict(holen=kraken, schlafen=self.schlaf.append, jetzt=lambda: self.jetzt, pause=1.0, ausgabe=lambda *_: None)
        args.update(kw)
        return daten.holen_kraken("BTC/EUR", 2 / 24, ["5m", "1h"], self.ordner, **args)

    def test_holt_seitenweise_und_schreibt_lesbare_csv(self):
        with mock.patch.object(daten, "SEITE", 100):
            k = FalscherKraken(self.abschluesse)
            r = self.holen(k)
        trades = [a for a in k.aufrufe if a[0] == "Trades"]
        self.assertEqual(len(trades), 4)                    # 360 Abschlüsse in Seiten zu 100: 100, 100, 100, 60
        self.assertEqual(r["abrufe"], 4)
        self.assertEqual(r["abschluesse"], 360)
        self.assertTrue(r["vollstaendig"])
        self.assertEqual(r["altname"], "XBTEUR")
        self.assertEqual(self.schlaf, [1.0, 1.0, 1.0])      # Pause nach jedem Abruf außer dem letzten
        kerzen = kerzen_aus_csv(r["dateien"]["5m"])         # Format wie das Backtest-Programm es liest
        self.assertEqual(len(kerzen), 23)                   # 120 Minuten = 24 Kerzen, die laufende wird verworfen
        self.assertEqual((kerzen[0].o, kerzen[0].c), (100.0, 100.0 + 0.1 * 14))
        self.assertAlmostEqual(kerzen[0].v, 0.5 * 15)
        stunden = kerzen_aus_csv(r["dateien"]["1h"])
        self.assertEqual(len(stunden), 1)                   # die zweite Stunde ist noch nicht abgeschlossen
        self.assertEqual(r["luecken"], {"5m": 0, "1h": 0})

    def test_start_liegt_auf_voller_stunde_und_since_in_nanosekunden(self):
        with mock.patch.object(daten, "SEITE", 100):
            k = FalscherKraken(self.abschluesse)
            self.holen(k)
        erster = next(p for m, p in k.aufrufe if m == "Trades")
        self.assertEqual(int(erster["since"]) % (3600 * 10 ** 9), 0)
        self.assertEqual(int(erster["since"]), (self.jetzt - 2 * 3600) // 3600 * 3600 * 10 ** 9)

    def test_max_abrufe_haelt_an_und_derselbe_befehl_setzt_fort(self):
        with mock.patch.object(daten, "SEITE", 100):
            k = FalscherKraken(self.abschluesse)
            r1 = self.holen(k, max_abrufe=2)
            self.assertFalse(r1["vollstaendig"])
            self.assertEqual(r1["abrufe"], 2)
            r2 = self.holen(k)
        self.assertTrue(r2["vollstaendig"])
        self.assertEqual(r2["abrufe"], 4)                   # 2 + 2 weitere, nichts doppelt
        self.assertEqual(r2["abschluesse"], 360)
        k2 = FalscherKraken(self.abschluesse)
        with mock.patch.object(daten, "SEITE", 100):
            ganz = self.holen(k2, neu=True, speichern_alle=1)
        self.assertEqual(daten.csv_lesen(r2["dateien"]["5m"]), daten.csv_lesen(ganz["dateien"]["5m"]))
        self.assertFalse((self.ordner / "kerzen" / ".zustand_kraken_XBTEUR.json").exists())   # fertig: kein Zwischenstand mehr

    def test_zwischenstand_mit_anderen_tagen_wird_nicht_fortgesetzt(self):
        with mock.patch.object(daten, "SEITE", 100):
            k = FalscherKraken(self.abschluesse)
            self.holen(k, max_abrufe=2)
            meldungen = []
            k2 = FalscherKraken(self.abschluesse)
            r = daten.holen_kraken("BTC/EUR", 1 / 24, ["5m", "1h"], self.ordner, holen=k2, schlafen=self.schlaf.append,
                                   jetzt=lambda: self.jetzt, pause=1.0, ausgabe=meldungen.append)
        self.assertTrue(any("passt nicht" in m for m in meldungen))
        self.assertTrue(r["vollstaendig"])
        self.assertEqual(r["abschluesse"], 180)                 # nur die letzte Stunde, von vorn gezählt

    def test_ratenbegrenzung_macht_laenger_pause_und_wiederholt(self):
        k = FalscherKraken(self.abschluesse)
        k.fehler = [daten._Wiederholbar("Kraken meldet Ratenbegrenzung", streng=True)]
        with mock.patch.object(daten, "SEITE", 1000):
            r = self.holen(k)
        self.assertTrue(r["vollstaendig"])
        self.assertEqual(self.schlaf[0], 30.0)              # nach dem AssetPairs-Aufruf wird beim ersten Fehler 30 s gewartet
        self.assertEqual(r["abschluesse"], 360)

    def test_netzfehler_wird_wiederholt_und_dann_aufgegeben(self):
        k = FalscherKraken(self.abschluesse)
        k.fehler = [daten._Wiederholbar("Kraken nicht erreichbar")] * 2
        r = self.holen(k)
        self.assertTrue(r["vollstaendig"])
        self.assertEqual(self.schlaf[:2], [5.0, 10.0])
        k = FalscherKraken(self.abschluesse)
        k.fehler = [daten._Wiederholbar("Kraken nicht erreichbar")] * 10
        with self.assertRaises(daten.DatenFehler) as ctx:
            self.holen(k)
        self.assertIn("nach 6 Versuchen aufgegeben", str(ctx.exception))

    def test_unbekanntes_paar_und_falsche_angaben(self):
        k = FalscherKraken(self.abschluesse)
        with self.assertRaises(daten.DatenFehler):
            daten.holen_kraken("FOO/EUR", 1, ["5m"], self.ordner, holen=k, schlafen=self.schlaf.append, ausgabe=lambda *_: None)
        with self.assertRaises(daten.DatenFehler):
            daten.holen_kraken("BTC/EUR", 0, ["5m"], self.ordner, holen=k, ausgabe=lambda *_: None)
        with self.assertRaises(daten.DatenFehler):
            daten.holen_kraken("BTC/EUR", 1, ["7m"], self.ordner, holen=k, ausgabe=lambda *_: None)

    def test_leere_antwort_beendet_ohne_endlosschleife(self):
        k = FalscherKraken([])
        r = self.holen(k)
        self.assertEqual((r["abrufe"], r["kerzen"]), (1, {"5m": 0, "1h": 0}))
        self.assertIsNone(r["von"])

    def test_namen(self):
        self.assertEqual(daten.paar_normal("btc/eur"), "XBTEUR")
        self.assertEqual(daten.paar_normal("ETH-EUR"), "ETHEUR")
        self.assertEqual(daten.paar_normal("Bitcoin"), "XBTEUR")
        self.assertEqual(daten.paar_normal("BTC"), "XBTEUR")

    def test_kurzer_name_und_vorschlag(self):
        k = FalscherKraken(self.abschluesse)
        r = daten.holen_kraken("Bitcoin", 1, ["5m"], self.ordner, holen=k, schlafen=self.schlaf.append, ausgabe=lambda *_: None,
                               jetzt=lambda: self.jetzt)
        self.assertEqual(r["altname"], "XBTEUR")
        with self.assertRaises(daten.DatenFehler) as ctx:
            daten.holen_kraken("Etherum", 1, ["5m"], self.ordner, holen=k, schlafen=self.schlaf.append, ausgabe=lambda *_: None)
        self.assertIn("ETH/EUR", str(ctx.exception))


class KommandozeileTest(unittest.TestCase):
    def test_daten_kraken_schreibt_in_den_datenordner(self):
        with tempfile.TemporaryDirectory() as d:
            k = FalscherKraken([abschluss(T0 + 1 + 20 * i, 100 + i * 0.1, 0.5, i + 1) for i in range(360)])
            with mock.patch.dict(os.environ, {"KITRADER_DATEN": d, "KITRADER_KONFIG": str(Path(d) / "keine.toml")}), \
                    mock.patch.object(daten, "http_holen", k), mock.patch.object(daten.time, "sleep"), \
                    mock.patch.object(daten.time, "time", return_value=T0 + 2 * 3600 + 30), \
                    contextlib.redirect_stdout(io.StringIO()) as aus:
                code = main(["daten", "kraken", "BTC/EUR", "--tage", "0.08333", "--takt", "5m"])
            self.assertEqual(code, 0)
            self.assertTrue((Path(d) / "kerzen" / "kraken_XBTEUR_5m.csv").is_file())
            self.assertIn("Abschlüsse", aus.getvalue())
            self.assertIn("privaten Gebrauch", aus.getvalue())

    def test_unbekanntes_paar_gibt_klare_fehlermeldung(self):
        with tempfile.TemporaryDirectory() as d:
            k = FalscherKraken([])
            fehler = io.StringIO()
            with mock.patch.dict(os.environ, {"KITRADER_DATEN": d, "KITRADER_KONFIG": str(Path(d) / "keine.toml")}), \
                    mock.patch.object(daten, "http_holen", k), mock.patch.object(daten.time, "sleep"), \
                    contextlib.redirect_stderr(fehler):
                code = main(["daten", "kraken", "FOO/EUR", "--tage", "1"])
            self.assertEqual(code, 2)
            self.assertIn("Fehler:", fehler.getvalue())


if __name__ == "__main__":
    unittest.main()
