"""Strategie-Labor: Halten, Zufallsvergleich, Aufgabe, Studie und Kommandozeile. Erfundene Kerzen, kein Netz."""
import contextlib
import io
import json
import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from kitrader import labor
from kitrader.__main__ import main
from kitrader.backtest import backtest
from kitrader.broker.basis import Kerze
from kitrader.einstellungen import mit_standard
from kitrader.strategie import Signal, Strategie

from .daten import zufallskerzen

UTC = timezone.utc
T0 = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)
KOSTEN = {"gebuehr_prozent": 0.8, "spread_prozent": 0.05, "schlupf_prozent": 0.02}


def kerzen_preise(preise, minuten=60):
    return [Kerze(T0 + timedelta(minutes=minuten * i), p, p, p, p, 1.0) for i, p in enumerate(preise)]


class HaltenTest(unittest.TestCase):
    def test_kosten_beim_kaufen_und_verkaufen(self):
        h = labor.halten(kerzen_preise([100.0, 101.0, 105.0, 110.0]), KOSTEN, einsatz=1000.0)
        self.assertAlmostEqual(h["aenderung_prozent"], 10.0)
        je_seite = 0.008 + 0.0005 / 2 + 0.0002
        self.assertAlmostEqual(h["netto_prozent"], ((110 * (1 - je_seite)) / (100 * (1 + je_seite)) - 1) * 100)
        self.assertAlmostEqual(h["netto_euro"], h["netto_prozent"] * 10)
        self.assertEqual((h["preis_start"], h["preis_ende"]), (100.0, 110.0))

    def test_groesster_rueckgang(self):
        h = labor.halten(kerzen_preise([100.0, 120.0, 90.0, 110.0]), KOSTEN)
        self.assertAlmostEqual(h["max_rueckgang_prozent"], 25.0)           # von 120 auf 90

    def test_ohne_kerzen_ein_klarer_fehler(self):
        with self.assertRaises(labor.LaborFehler):
            labor.halten([], KOSTEN)


def kosten_voll(**kw):
    return {"gebuehr_fest": 0.0, "gebuehr_prozent": 0.8, "gebuehr_maker_prozent": 0.4, "spread_prozent": 0.05,
            "schlupf_prozent": 0.02, **kw}


class SchritteSkript(Strategie):
    """Long-Signal nach der Kerze `signal` (= wenn `signal + 1` Kerzen gesehen sind), auf Wunsch "flat" nach `halt` Kerzen."""
    name = "schritte"

    def __init__(self, a, stop, ziel, halt=None, kerzen=None):
        super().__init__()
        self.a, self.stop, self.ziel, self.halt, self.alle = a, stop, ziel, halt, kerzen

    def bewerten(self, kerzen, lage):
        n = len(kerzen)
        if lage.menge() == 0 and n == self.a + 1:
            ref = self.alle[self.a + 1].o                       # Einstiegskurs der nächsten Kerze
            return Signal("long", ref, ref * (1 - self.stop), None if self.ziel is None else ref * (1 + self.ziel), 0.9, "test")
        if lage.menge() != 0 and self.halt is not None and n == self.a + 1 + self.halt:
            return Signal("flat", begruendung="test")
        return None


class ZufallsweltTest(unittest.TestCase):
    """Die schnelle Rechnung muss Trade für Trade dasselbe Netto ergeben wie der Backtest selbst."""

    def test_stimmt_mit_dem_backtest_ueberein(self):
        kerzen = zufallskerzen(tage=4, seed=21, takt_min=5, pro_tag=288, tagesdrift=0.0006)
        for variante, kosten in enumerate([kosten_voll(), kosten_voll(gebuehr_maker_prozent=0.1, spread_prozent=0.2, schlupf_prozent=0.1),
                                           kosten_voll(gebuehr_fest=1.0, gebuehr_prozent=0.3, gebuehr_maker_prozent=0.3)]):
            e = mit_standard({"kosten": kosten, "risiko": {"standard_markt": "krypto", "mengenschritt": 1e-9,
                                                           "position_max_prozent": 10.0}})
            welt = labor.Zufallswelt(kerzen, kosten)
            rng = random.Random(variante)
            gesehen = {"stop": 0, "ziel": 0, "tagesende": 0, "signal": 0}
            for _ in range(70):
                a = rng.choice(welt.signalkerzen)
                stop = rng.choice([0.0015, 0.004, 0.01])
                ziel = rng.choice([None, 0.003, 0.008, 0.02])
                halt = rng.choice([None, 2, 15])
                r = backtest(SchritteSkript(a, stop, ziel, halt, kerzen), kerzen, e, "TEST", "krypto")
                if not r.trades:
                    continue                                            # Risikoprüfung hat die Order abgelehnt: kein Vergleich
                h = r.trades[0]
                gesehen[h.grund] += 1
                halt_k = max(1, round((h.ausstieg_zeit - h.einstieg_zeit).total_seconds() / 300)) if h.grund == "signal" else None
                netto = welt.handel(a, h.menge * h.einstieg, h.stop_abstand, h.ziel_abstand, halt_k)
                self.assertAlmostEqual(netto, h.netto, places=7, msg=(variante, a, stop, ziel, halt, h.grund))
            for grund in gesehen:                                       # alle Ausstiegsarten wurden wirklich verglichen
                self.assertGreater(gesehen[grund], 0, (variante, gesehen))

    def test_trade_speichert_stop_und_ziel_abstand(self):
        kerzen = zufallskerzen(tage=2, seed=2, takt_min=5, pro_tag=288)
        e = mit_standard({"kosten": kosten_voll(), "risiko": {"standard_markt": "krypto", "mengenschritt": 1e-9}})
        h = backtest(SchritteSkript(10, 0.004, 0.008, None, kerzen), kerzen, e, "TEST", "krypto").trades[0]
        self.assertAlmostEqual(h.stop_abstand, 0.004)
        self.assertAlmostEqual(h.ziel_abstand, 0.008)
        h2 = backtest(SchritteSkript(10, 0.004, None, None, kerzen), kerzen, e, "TEST", "krypto").trades[0]
        self.assertIsNone(h2.ziel_abstand)


class ZufallTest(unittest.TestCase):
    def setUp(self):
        self.kosten = kosten_voll()
        self.einst = mit_standard({"kosten": self.kosten, "risiko": {"standard_markt": "krypto", "mengenschritt": 1e-9}})

    def trades(self, kerzen, anzahl=4, stop=0.004, ziel=0.008):
        welt = labor.Zufallswelt(kerzen, self.kosten)
        alle = []
        for a in welt.signalkerzen[10::97][:anzahl]:
            alle += backtest(SchritteSkript(a, stop, ziel, None, kerzen), kerzen, self.einst, "TEST", "krypto").trades
        return alle

    def test_bei_gleichbleibendem_kurs_bleiben_nur_die_kosten(self):
        kerzen = kerzen_preise([100.0] * 600, minuten=5)
        trades = []
        for a in (5, 100, 200, 300):
            trades += backtest(SchritteSkript(a, 0.004, 0.008, None, kerzen), kerzen, self.einst, "TEST", "krypto").trades
        self.assertEqual(len(trades), 4)
        z = labor.zufallsvergleich(trades, kerzen, 300, self.kosten, laeufe=40, seed=3)
        erwartet = sum(t.netto for t in trades)
        self.assertAlmostEqual(z["median"], erwartet, places=6)         # jeder Zufalls-Trade zahlt dieselben Kosten
        self.assertAlmostEqual(z["p5"], z["p95"])
        self.assertEqual(z["anteil_besser"], 100.0)                     # ≥ gilt: alle Läufe sind gleich gut

    def test_gleicher_startwert_gleiches_ergebnis(self):
        kerzen = zufallskerzen(tage=6, seed=8, takt_min=5, pro_tag=288)
        trades = self.trades(kerzen)
        a = labor.zufallsvergleich(trades, kerzen, 300, self.kosten, 60, seed=5)
        self.assertEqual(a, labor.zufallsvergleich(trades, kerzen, 300, self.kosten, 60, seed=5))
        self.assertNotEqual(a["mittel"], labor.zufallsvergleich(trades, kerzen, 300, self.kosten, 60, seed=6)["mittel"])

    def test_anteil_besser_an_den_enden(self):
        kerzen = zufallskerzen(tage=6, seed=8, takt_min=5, pro_tag=288)
        trades = self.trades(kerzen)
        import dataclasses
        sehr_gut = [dataclasses.replace(t, brutto=t.brutto + 1e9) for t in trades]
        sehr_schlecht = [dataclasses.replace(t, brutto=t.brutto - 1e9) for t in trades]
        self.assertEqual(labor.zufallsvergleich(sehr_gut, kerzen, 300, self.kosten, 50)["anteil_besser"], 0.0)
        self.assertEqual(labor.zufallsvergleich(sehr_schlecht, kerzen, 300, self.kosten, 50)["anteil_besser"], 100.0)

    def test_steigender_markt_zufalls_longs_gewinnen_brutto(self):
        kerzen = kerzen_preise([100.0 * 1.0004 ** i for i in range(576)], minuten=5)          # stetig steigend, keine Stops
        trades = []
        for a in (5, 150, 300):
            trades += backtest(SchritteSkript(a, 0.05, 0.5, None, kerzen), kerzen, self.einst, "TEST", "krypto").trades
        z = labor.zufallsvergleich(trades, kerzen, 300, kosten_voll(gebuehr_prozent=0.0, gebuehr_maker_prozent=0.0, spread_prozent=0.0,
                                                                     schlupf_prozent=0.0), 50, seed=1)
        self.assertGreater(z["p5"], 0)

    def test_ohne_trades_short_oder_zu_wenige_kerzen_klare_fehler(self):
        kerzen = zufallskerzen(tage=3, seed=8, takt_min=5, pro_tag=288)
        with self.assertRaises(labor.LaborFehler):
            labor.zufallsvergleich([], kerzen, 300, self.kosten)
        trades = self.trades(kerzen, anzahl=1)
        import dataclasses
        with self.assertRaises(labor.LaborFehler):
            labor.zufallsvergleich([dataclasses.replace(trades[0], richtung="short")], kerzen, 300, self.kosten)
        with self.assertRaises(labor.LaborFehler):
            labor.zufallsvergleich([dataclasses.replace(trades[0], stop_abstand=None)], kerzen, 300, self.kosten)
        with self.assertRaises(labor.LaborFehler):
            labor.zufallsvergleich(trades, kerzen[:1], 300, self.kosten)


def csv_schreiben(pfad: Path, kerzen):
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as f:
        f.write("t,o,h,l,c,v\n")
        for k in kerzen:
            f.write(f"{k.t.strftime('%Y-%m-%dT%H:%M:%SZ')},{k.o},{k.h},{k.l},{k.c},{k.v}\n")


class StudieTest(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ordner = Path(self._d.name)
        # 20 Tage erfundene 15-Minuten-Kerzen (64 je Tag), als Datei im Format von `daten kraken`
        self.kerzen = zufallskerzen(tage=20, seed=4, takt_min=15, pro_tag=64)
        csv_schreiben(labor.kerzendatei(self.ordner, "BTC/EUR", "15m"), self.kerzen)
        self.einst = labor.labor_einstellungen(faltungen=2, min_trades_training=3)

    def test_aufgabe_liefert_json_taugliches_ergebnis_nur_ueber_die_testteile(self):
        pfad = labor.kerzendatei(self.ordner, "BTC/EUR", "15m")
        r = labor.aufgabe(str(pfad), "donchian", "XBTEUR", self.einst, 30, 1)
        json.dumps(r)                                                       # darf nicht an inf/Datumswerten scheitern
        self.assertEqual(len(r["faltungen"]), 2)
        im_test = sum(1 for k in self.kerzen if k.t.isoformat() >= r["test_von"])
        self.assertAlmostEqual(im_test, len(self.kerzen) * 0.6, delta=64)   # 60 % der Kerzen sind Testteil (Teilung an Tagesgrenzen)
        self.assertGreater(r["test_von"], self.kerzen[0].t.isoformat())
        self.assertEqual(r["kennzahlen"]["trades"], sum(f["test_trades"] for f in r["faltungen"]))
        self.assertAlmostEqual(r["kennzahlen"]["netto"], sum(f["test_netto"] for f in r["faltungen"]), places=6)
        self.assertEqual(r["aussage"], r["kennzahlen"]["trades"] >= 30)
        self.assertIn("preis_start", r["halten"])
        if r["kennzahlen"]["trades"]:
            self.assertEqual(r["zufall"]["laeufe"], 30)
        self.assertAlmostEqual(r["kosten_gesamt"], r["kennzahlen"]["gebuehren"] + r["kennzahlen"]["spread_schlupf"])

    def test_kosten_sind_die_von_kraken(self):
        k = self.einst["kosten"]
        self.assertEqual((k["gebuehr_prozent"], k["gebuehr_maker_prozent"], k["gebuehr_fest"]), (0.8, 0.4, 0.0))
        self.assertEqual(self.einst["backtest"]["tage_pro_jahr"], 365)
        self.assertFalse(self.einst["risiko"]["short_erlaubt"])             # nur Long, wie in der Risikoprüfung

    def test_studie_mit_einem_prozess_und_tabellen(self):
        e = labor.studie(self.ordner, ["BTC/EUR"], ["15m"], ["donchian", "ma_trend"], self.einst, 20, 1, prozesse=1,
                         fortschritt=lambda *_: None)
        self.assertEqual([r["strategie"] for r in e["ergebnisse"]], ["donchian", "ma_trend"])
        self.assertEqual(e["daten"][0]["kerzen"], len(self.kerzen))
        text = labor.als_text(e)
        self.assertIn("donchian", text)
        self.assertIn("Kaufen und Halten", text)
        md = labor.als_markdown(e)
        self.assertIn("| Kandidat | Trades | Netto €", md)
        self.assertIn("*Kaufen und Halten*", md)

    def test_fehlende_datei_nennt_den_befehl_zum_holen(self):
        with self.assertRaises(labor.LaborFehler) as ctx:
            labor.studie(self.ordner, ["ETH/EUR"], ["5m"], ["donchian"], self.einst, 5, 1, prozesse=1)
        self.assertIn("daten kraken ETH/EUR", str(ctx.exception))

    def test_unbekannte_strategie(self):
        with self.assertRaises(labor.LaborFehler):
            labor.studie(self.ordner, ["BTC/EUR"], ["15m"], ["gibtsnicht"], self.einst, 5, 1, prozesse=1)

    def test_weniger_als_30_trades_ist_keine_aussage(self):
        e = labor.studie(self.ordner, ["BTC/EUR"], ["15m"], ["bollinger_rsi"], self.einst, 10, 1, prozesse=1, fortschritt=lambda *_: None)
        r = e["ergebnisse"][0]
        if r["kennzahlen"]["trades"] < 30:
            self.assertFalse(r["aussage"])
            self.assertEqual(labor._zeile(r)[-2:], ["-", "nein"])
            self.assertIn("keine Aussage", labor._urteil(r))


class ZusammenfassungTest(unittest.TestCase):
    @staticmethod
    def r(trades, netto, brutto, halten_netto, besser):
        return {"paar": "BTC/EUR", "takt": "5m", "strategie": "x", "aussage": trades >= 30, "kosten_gesamt": brutto - netto,
                "kennzahlen": {"trades": trades, "netto": netto, "vor_kosten": brutto}, "halten": {"netto_euro": halten_netto},
                "zufall": {"anteil_besser": besser}}

    def test_zaehlt_nur_kombinationen_mit_aussage(self):
        e = {"ergebnisse": [self.r(40, -100, 20, -30, 90), self.r(35, 50, 120, -30, 3), self.r(10, 500, 600, -30, 1),
                            self.r(50, -5, 60, 10, 4.5)]}
        z = labor.zusammenfassung(e)
        self.assertEqual((z["kombinationen"], z["mit_aussage"]), (4, 3))
        self.assertEqual(z["im_plus"], 1)                       # der Kandidat mit 10 Trades zählt nicht
        self.assertEqual(z["brutto_im_plus"], 3)
        self.assertEqual(z["besser_als_halten"], 1)             # Netto 50 > -30 (der mit -5 liegt unter +10, der mit -100 unter -30)
        self.assertEqual(z["besser_als_95_prozent_zufall"], 2)
        self.assertAlmostEqual(z["zufallstreffer_erwartet"], 0.15)
        self.assertEqual(z["bestes_netto"][0], 50)
        text = labor.zusammenfassung_text(e)
        self.assertIn("Nach Kosten im Plus: 1 von 3", text)

    def test_leeres_ergebnis(self):
        z = labor.zusammenfassung({"ergebnisse": []})
        self.assertEqual((z["mit_aussage"], z["bestes_netto"]), (0, None))


def kuenstliche_kerzen(phi, seed, n, sigma=0.0012):
    """Künstliche 15-Minuten-Kerzen: Rendite = phi mal die Vorrendite plus Rauschen. phi > 0 baut eine Trendfortsetzung ein."""
    rng = random.Random(seed)
    t, p, r_alt, out = datetime(2026, 6, 1, tzinfo=UTC), 50000.0, 0.0, []
    for i in range(n):
        r = phi * r_alt + rng.gauss(0, sigma)
        r_alt = r
        o, c = p, p * (1 + r)
        h, l = max(o, c) * (1 + abs(rng.gauss(0, 0.0004))), min(o, c) * (1 - abs(rng.gauss(0, 0.0004)))
        out.append(Kerze(t + timedelta(minutes=15 * i), o, h, l, c, 10 * (1 + abs(r) / sigma * 2) * rng.uniform(0.7, 1.3)))
        p = c
    return out


class KontrolleTest(unittest.TestCase):
    """Das Labor muss einen echten Vorteil erkennen, wenn einer eingebaut ist, und bei reinem Zufall keinen melden."""

    def lauf(self, phi, strategie):
        with tempfile.TemporaryDirectory() as d:
            pfad = labor.kerzendatei(Path(d), "SYN/EUR", "15m")
            csv_schreiben(pfad, kuenstliche_kerzen(phi, 1, 96 * 40))
            kosten = {"gebuehr_prozent": 0, "gebuehr_maker_prozent": 0, "spread_prozent": 0, "schlupf_prozent": 0}
            e = labor.labor_einstellungen(faltungen=2, min_trades_training=5, kosten=kosten)
            return labor.aufgabe(str(pfad), strategie, "SYNEUR", e, 100, 1)

    def test_eingebaute_trendfortsetzung_wird_erkannt(self):
        for strategie in ("momentum", "donchian"):
            r = self.lauf(0.35, strategie)
            self.assertGreater(r["kennzahlen"]["netto"], 0, strategie)
            self.assertLess(r["zufall"]["anteil_besser"], 10, strategie)       # klar besser als blindes Einsteigen

    def test_reiner_zufall_ergibt_keinen_auffaelligen_vorteil(self):
        for strategie in ("momentum", "donchian"):
            r = self.lauf(0.0, strategie)
            self.assertGreater(r["zufall"]["anteil_besser"], 5, strategie)


class KommandozeileTest(unittest.TestCase):
    def test_labor_schreibt_json_und_markdown_und_zeigt_tabelle(self):
        with tempfile.TemporaryDirectory() as d:
            ordner = Path(d)
            csv_schreiben(labor.kerzendatei(ordner, "BTC/EUR", "15m"), zufallskerzen(tage=16, seed=9, takt_min=15, pro_tag=64))
            aus = io.StringIO()
            md = ordner / "t.md"
            with mock.patch.dict(os.environ, {"KITRADER_DATEN": d, "KITRADER_KONFIG": str(ordner / "keine.toml")}), \
                    contextlib.redirect_stdout(aus):
                code = main(["labor", "--paare", "BTC/EUR", "--takte", "15m", "--strategien", "donchian", "volumen",
                             "--zufall", "10", "--faltungen", "2", "--prozesse", "1", "--markdown", str(md)])
            self.assertEqual(code, 0)
            self.assertIn("Kaufen und Halten", aus.getvalue())
            self.assertIn("Kein Erfolgsversprechen", aus.getvalue())
            ergebnis = json.loads((ordner / "labor" / "ergebnis.json").read_text(encoding="utf-8"))
            self.assertEqual(len(ergebnis["ergebnisse"]), 2)
            self.assertEqual(ergebnis["einstellungen"]["kosten"]["gebuehr_prozent"], 0.8)
            self.assertTrue(md.read_text(encoding="utf-8").startswith("**BTC/EUR, Takt 15m**"))
            # nur anzeigen, nichts rechnen
            aus2 = io.StringIO()
            with mock.patch.dict(os.environ, {"KITRADER_DATEN": d, "KITRADER_KONFIG": str(ordner / "keine.toml")}), \
                    contextlib.redirect_stdout(aus2):
                code = main(["labor", "--aus-json", str(ordner / "labor" / "ergebnis.json")])
            self.assertEqual(code, 0)
            self.assertIn("donchian", aus2.getvalue())

    def test_fehlende_daten_geben_fehlermeldung_statt_absturz(self):
        with tempfile.TemporaryDirectory() as d:
            fehler = io.StringIO()
            with mock.patch.dict(os.environ, {"KITRADER_DATEN": d, "KITRADER_KONFIG": str(Path(d) / "keine.toml")}), \
                    contextlib.redirect_stderr(fehler), contextlib.redirect_stdout(io.StringIO()):
                code = main(["labor", "--paare", "BTC/EUR", "--takte", "5m", "--prozesse", "1"])
            self.assertEqual(code, 2)
            self.assertIn("daten kraken", fehler.getvalue())


if __name__ == "__main__":
    unittest.main()
