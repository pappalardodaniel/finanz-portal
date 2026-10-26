"""Welt-Rotation offen getestet: erfundene OSIRIS-Antworten, Kraken-Kerzen und Schnappschüsse, kein Netz."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from kitrader import welt as w
from kitrader.__main__ import main
from kitrader.broker.basis import Kerze
from kitrader.einstellungen import EinstellungsFehler, mit_standard
from kitrader.historie.speicher import Speicher

UTC = timezone.utc
JETZT = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def q(preis, vortag=None, spark=None, offen=True, zeit=None):
    return {"price": preis, "prev_close": vortag if vortag is not None else preis, "market_open": offen, "spark": spark or [],
            "time": int((zeit or JETZT).timestamp()), "change_percent": 0.0}


def markt(**ueberschreibung):
    """Erfundene Antwort im Aufbau von /api/markets."""
    spark_us = [100 + i for i in range(21)]                         # 100 … 120, Schluss 120
    m = {"indices": {"S&P 500": q(120, 119, spark_us), "DAX": q(99, 100, [100] * 20 + [99]), "Euro Stoxx 50": q(101, 100, [100] * 21),
                     "Nikkei 225": q(90, 100, [100] * 15 + [95, 94, 93, 92, 91, 90], offen=False), "Hang Seng": q(110, 105, [100] * 21),
                     "FTSE 100": q(100, 100, [100] * 21), "VIX": q(20, 18, [15] * 20 + [20]), "US 10Y": q(4.5, 4.4, [4.0] * 20 + [4.5]),
                     "Dollar Index": q(101, 100, [100] * 21)},
         "stocks": {"RTX": q(200, 190, [190] * 21)},
         "oil": {"Brent Crude": q(110, 100, [100] * 21), "WTI Crude": q(100, 100, [100] * 21), "Natural Gas": q(3, 3, [3] * 21)},
         "commodities": {"Gold": q(4000, 4000, [4000] * 21), "Silver": q(50, 50, [50] * 21), "Copper": q(6, 6, [6] * 21),
                         "Wheat": q(600, 600, [600] * 21), "Corn": q(450, 450, [450] * 21)},
         "crypto": {"Bitcoin": q(80000, 80000, [80000] * 31), "Ethereum": q(2300, 2300, [2300] * 31), "Solana": q(100, 100, [100] * 31)},
         "fx": {"EUR/USD": q(1.10, 1.10, [1.10] * 21), "GBP/USD": q(1.30, 1.30, [1.30] * 21), "USD/JPY": q(153, 150, [150] * 21),
                "USD/CNY": q(7.0, 7.0, [7.0] * 21)}, "scm_alerts": [], "count": 33}
    m.update(ueberschreibung)
    return m


def kerzen(schluesse, ende=JETZT):
    return [Kerze(ende - timedelta(days=len(schluesse) - 1 - i), c, c, c, c, 1.0) for i, c in enumerate(schluesse)]


class Eingang(unittest.TestCase):
    def test_osiris_zeilen(self):
        z = {r["schluessel"]: r for r in w.aus_osiris(markt())}
        self.assertEqual(len(z), 25)
        self.assertEqual((z["DAX"]["region"], z["DAX"]["klasse"], z["DAX"]["vortag"]), ("Deutschland", "Aktien", 100))
        self.assertEqual((z["US 10Y"]["art"], z["VIX"]["art"], z["USD/JPY"]["vorzeichen"]), ("rendite", "risiko", -1))
        self.assertEqual((z["RTX"]["region"], z["RTX"]["klasse"]), ("USA", "Rüstung/Tech-Aktien USA"))
        self.assertEqual(z["Bitcoin"]["klasse"], "Krypto")
        self.assertFalse(z["Nikkei 225"]["offen"])

    def test_unbrauchbares_wird_uebersprungen(self):
        z = w.aus_osiris({"indices": {"X": {"price": None}, "Y": "kaputt", "DAX": q(99, 100)}, "oil": [], "fx": None})
        self.assertEqual([r["name"] for r in z], ["DAX"])

    def test_kerzen(self):
        z = w.aus_kerzen("BTC/EUR", kerzen([100, 101, 102]))
        self.assertEqual((z["preis"], z["vortag"], z["spark"], z["quelle"]), (102, 101, [100, 101, 102], "Kraken"))
        self.assertIsNone(w.aus_kerzen("BTC/EUR", kerzen([100])))


class Rotation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.uhr = [JETZT]

    def bauen(self, antwort=None, kraken=("BTC/EUR", "ETH/EUR"), **extra):
        antwort = markt() if antwort is None else antwort

        def holen(url, timeout):
            self.url = url
            return 200, json.dumps(antwort).encode(), ""

        def geber(paar, takt, anzahl):
            return kerzen([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110]) if paar == "BTC/EUR" else (_ for _ in ()).throw(RuntimeError("Paar unbekannt"))
        wlt = w.Welt(mit_standard({"welt": {"kraken": list(kraken), "historie": False, **extra}}), Path(self.tmp.name) / "welt.db",
                     uhr=lambda: self.uhr[0], holen=holen, kerzen_geber=geber)
        self.addCleanup(wlt.beenden)
        return wlt

    def test_abrufen_speichert_und_wiederholt_nicht(self):
        wlt = self.bauen()
        erg = wlt.abrufen()
        self.assertTrue(erg["neu"])
        self.assertTrue(self.url.endswith("/api/markets"))
        self.assertEqual(erg["quellen"]["Kraken BTC/EUR"], "ok")
        self.assertIn("unbekannt", erg["quellen"]["Kraken ETH/EUR"])                    # ein Paar fällt aus, der Rest zählt
        self.assertEqual(erg["zeilen"], 25 + 1)
        self.assertFalse(wlt.abrufen()["neu"])
        self.uhr[0] += timedelta(minutes=11)
        self.assertTrue(wlt.abrufen()["neu"])
        self.assertTrue(wlt.abrufen(erzwingen=True)["neu"])

    def test_nichts_erreichbar(self):
        wlt = w.Welt(mit_standard({"welt": {"kraken": [], "historie": False}}), Path(self.tmp.name) / "x.db", uhr=lambda: JETZT,
                     holen=lambda u, t: (0, b"", "Verbindung abgelehnt"))
        self.addCleanup(wlt.beenden)
        with self.assertRaisesRegex(w.WeltFehler, "Verbindung abgelehnt"):
            wlt.abrufen()
        self.assertIsNone(wlt.rotation()["stand"])
        self.assertIn("kein Schnappschuss", wlt.rotation()["text"])

    def test_aenderungen_aus_dem_feed(self):
        wlt = self.bauen()
        wlt.abrufen()
        r = wlt.rotation()
        reg = {x["region"]: x for x in r["regionen"]}
        usa = reg["USA"]["aend"]
        self.assertAlmostEqual(usa["1T"], (120 / 119 - 1) * 100)
        self.assertAlmostEqual(usa["1W"], (120 / 115 - 1) * 100)                         # 5 Handelstage zurück: spark[-6] = 115
        self.assertAlmostEqual(usa["1M"], 20.0)                                          # Beginn der Reihe: 100
        self.assertAlmostEqual(reg["Deutschland"]["aend"]["1T"], -1.0)
        self.assertAlmostEqual(reg["Japan"]["aend"]["1W"], (90 / 95 - 1) * 100)          # spark[-6] = 95
        self.assertEqual(reg["USA"]["herkunft"]["1W"], "feed")

    def test_relative_staerke_gegen_den_schnitt(self):
        wlt = self.bauen()
        wlt.abrufen()
        r = wlt.rotation()
        reg = {x["region"]: x for x in r["regionen"]}
        mittel = sum(x["aend"]["1M"] for x in r["regionen"] if x["region"] != "Schwellenländer") / r["weltschnitt"]["n"]
        self.assertEqual(r["weltschnitt"]["n"], 6)
        self.assertAlmostEqual(r["weltschnitt"]["1M"], mittel)
        self.assertAlmostEqual(reg["USA"]["rel"]["1M"], 20.0 - mittel)
        self.assertAlmostEqual(reg["Europa"]["rel"]["1M"], 1.0 - mittel)

    def test_rendite_in_basispunkten_und_vorzeichen(self):
        wlt = self.bauen()
        wlt.abrufen()
        r = wlt.rotation()
        ind = {i["name"]: i for i in r["indikatoren"]}
        self.assertAlmostEqual(ind["US 10Y"]["aend"]["1T"], 10.0)                        # 4,5 - 4,4 = 0,1 Prozentpunkte = 10 bp
        self.assertAlmostEqual(ind["US 10Y"]["aend"]["1M"], 50.0)
        self.assertAlmostEqual(ind["VIX"]["aend"]["1T"], (20 / 18 - 1) * 100)
        waehrung = next(k for k in r["klassen"] if k["klasse"] == "Währungen gg. USD")
        self.assertIn("USD/JPY", waehrung["mitglieder"])
        yen = next(e for e in r["einzel"] if e["name"] == "USD/JPY")
        self.assertAlmostEqual(yen["aend"]["1T"], (150 / 153 - 1) * 100)                  # USD/JPY steigt, der Yen verliert
        self.assertNotIn("VIX", [e["name"] for e in r["einzel"]])                         # Anzeigewerte stehen nicht in der Rangliste
        self.assertNotIn("US 10Y", [e["name"] for e in r["einzel"]])

    def test_kraken_ersetzt_osiris_krypto(self):
        wlt = self.bauen()
        wlt.abrufen()
        r = wlt.rotation()
        krypto = next(k for k in r["klassen"] if k["klasse"] == "Krypto")
        self.assertIn("BTC/EUR", krypto["mitglieder"])
        self.assertNotIn("Bitcoin", krypto["mitglieder"])
        self.assertIn("Ethereum", krypto["mitglieder"])                                   # kein Kraken-Ersatz geliefert: OSIRIS bleibt
        btc = next(e for e in r["einzel"] if e["name"] == "BTC/EUR")
        self.assertAlmostEqual(btc["aend"]["1T"], (110 / 109 - 1) * 100)
        self.assertAlmostEqual(btc["aend"]["1W"], (110 / 103 - 1) * 100)                  # Krypto: 7 Tage zurück

    def test_fehlende_maerkte_werden_genannt(self):
        wlt = self.bauen()
        wlt.abrufen()
        r = wlt.rotation()
        fehlt = " | ".join(r["fehlend"])
        for name in ("Indien", "Brasilien", "Schwellenländer"):
            self.assertIn(name, fehlt)
        self.assertNotIn("USA (", fehlt)
        self.assertIn("Nicht verfügbar", r["text"])
        self.assertEqual([x["region"] for x in r["regionen"] if x["region"] == "Schwellenländer"], [])    # nur China da: kein Mittel

    def test_schwellenlaender_mit_zusatzreihen(self):
        antwort = markt()
        antwort["indices"]["Nifty 50"] = q(130, 100, [100] * 21)
        antwort["indices"]["Bovespa"] = q(110, 100, [100] * 21)
        wlt = self.bauen(antwort, leitindizes={"Indien": "Nifty 50", "Brasilien": "Bovespa"})
        wlt.abrufen()
        r = wlt.rotation()
        regionen = {x["region"] for x in r["regionen"]}
        self.assertTrue({"Indien", "Brasilien", "Schwellenländer"} <= regionen)
        se = next(x for x in r["regionen"] if x["region"] == "Schwellenländer")
        china, indien, brasilien = (next(x for x in r["regionen"] if x["region"] == n)["aend"]["1M"] for n in ("China", "Indien", "Brasilien"))
        self.assertAlmostEqual(se["aend"]["1M"], (china + indien + brasilien) / 3)
        self.assertEqual(r["weltschnitt"]["n"], 8)
        self.assertNotIn("Indien", " | ".join(r["fehlend"]))

    def test_eigene_schnappschuesse_haben_vorrang(self):
        wlt = self.bauen()
        wlt.abrufen()                                                               # 26.10.2026 12:00
        frueher = [dict(z) for z in w.aus_osiris(markt())]
        for z in frueher:
            z["preis"] = z["preis"] / 2 if z["schluessel"] == "S&P 500" else z["preis"]    # S&P stand vor 7 Tagen bei 60
        wlt.speichern(frueher, JETZT - timedelta(days=7))
        wlt.speichern([dict(z, preis=z["preis"] * 0.9) if z["schluessel"] == "S&P 500" else dict(z) for z in w.aus_osiris(markt())], JETZT - timedelta(days=30))
        wlt.db.execute("UPDATE stand SET zeit=? WHERE zeit=?", (JETZT.isoformat(timespec="seconds"), JETZT.isoformat(timespec="seconds")))
        r = wlt.rotation()
        usa = next(x for x in r["regionen"] if x["region"] == "USA")
        self.assertAlmostEqual(usa["aend"]["1W"], 100.0)                                   # 120 gegen 60
        self.assertAlmostEqual(usa["aend"]["1M"], (120 / 108 - 1) * 100)
        self.assertEqual((usa["herkunft"]["1W"], usa["herkunft"]["1M"]), ("eigen", "eigen"))
        dax = next(x for x in r["regionen"] if x["region"] == "Deutschland")
        self.assertEqual(dax["herkunft"]["1W"], "eigen")                                  # gleicher Zeitpunkt für alle Märkte

    def test_toleranz_zu_alter_schnappschuss_zaehlt_nicht(self):
        wlt = self.bauen()
        wlt.abrufen()
        wlt.speichern(w.aus_osiris(markt()), JETZT - timedelta(days=12))                   # weder um 7 noch um 30 Tage nah genug
        usa = next(x for x in wlt.rotation()["regionen"] if x["region"] == "USA")
        self.assertEqual((usa["herkunft"]["1W"], usa["herkunft"]["1M"]), ("feed", "feed"))

    def test_veraltete_kurse_zaehlen_nicht(self):
        alt = markt()
        alt["indices"]["Nikkei 225"] = q(90, 100, [100] * 21, offen=False, zeit=JETZT - timedelta(days=9))
        wlt = self.bauen(alt)
        wlt.abrufen()
        r = wlt.rotation()
        self.assertNotIn("Japan", [x["region"] for x in r["regionen"]])
        self.assertTrue(any("Nikkei 225" in h and "nicht in die Schnitte" in h for h in r["hinweise"]))
        self.assertEqual(r["weltschnitt"]["n"], 5)

    def test_umschichtung_nur_ueber_der_schwelle(self):
        wlt = self.bauen()
        wlt.abrufen()
        r = wlt.rotation()
        self.assertTrue(any(u.startswith("1 Monat, Aktienmärkte: von Japan") and "nach USA" in u for u in r["umschichtung"]))
        ruhig = markt(indices={k: q(100, 100, [100] * 21) for k in ("S&P 500", "DAX", "Euro Stoxx 50", "Nikkei 225", "Hang Seng", "FTSE 100")})
        w2 = self.bauen(ruhig)
        w2.abrufen(erzwingen=True)
        r2 = w2.rotation()
        self.assertFalse([u for u in r2["umschichtung"] if "Aktienmärkte" in u])
        self.assertIn("Beobachtung", r["text"].replace("reine Beobachtung", "Beobachtung"))
        self.assertIn("kein Nachweis von Geldflüssen", r["text"])

    def test_rangliste(self):
        wlt = self.bauen()
        wlt.abrufen()
        r = wlt.rotation()
        namen = [n for n, _ in r["rang"]["1T"]["gewinner"]]
        self.assertEqual(namen[:2], ["Brent Crude", "RTX"])                                 # +10 % und +5,26 % sind die größten Tagesgewinne
        self.assertEqual(r["rang"]["1T"]["verlierer"][0][0], "Nikkei 225")                  # -10 %
        self.assertLessEqual(len(r["rang"]["1W"]["gewinner"]), 3)

    def test_text(self):
        wlt = self.bauen()
        wlt.abrufen()
        r = wlt.rotation()
        self.assertIn("Region", r["text"])
        self.assertIn("rel 1 W", r["text"])
        self.assertIn("US 10Y", r["text"])
        self.assertIn("bp", r["text"])
        kurz = w.als_text(r, kompakt=True)
        self.assertLess(len(kurz), 2600)
        self.assertIn("keine Prognose", kurz)
        self.assertIn("Regionen (1 T/1 W/1 M in %", kurz)

    def test_historie_als_reserve_und_zusatz(self):
        ordner = Path(self.tmp.name)
        with Speicher(ordner) as sp:
            tage = [(JETZT - timedelta(days=i)).date().isoformat() for i in range(40, -1, -1)]
            sp.schreiben("dow", [(d, 40000 + 10 * i) for i, d in enumerate(tage)], "Test")
            sp.schreiben("vix", [(d, 15 + 0.1 * i) for i, d in enumerate(tage)], "Test")
        antwort = markt()
        del antwort["indices"]["VIX"]
        wlt = w.Welt(mit_standard({"welt": {"kraken": []}}), ordner / "welt.db", uhr=lambda: JETZT, holen=lambda u, t: (200, json.dumps(antwort).encode(), ""),
                     historie_pfad=ordner / "historie.db")
        self.addCleanup(wlt.beenden)
        wlt.abrufen()
        r = wlt.rotation()
        dow = next(e for e in r["einzel"] if e["name"] == "Dow Jones")                       # Zusatz (immer)
        self.assertAlmostEqual(dow["aend"]["1T"], (40400 / 40390 - 1) * 100)
        self.assertEqual(dow["quelle"], "Historie")
        vix = next(i for i in r["indikatoren"] if i["name"] == "VIX")                        # Ersatz, weil der Feed keinen VIX hat
        self.assertEqual(vix["quelle"], "Historie")
        self.assertIsNotNone(vix["aend"]["1M"])
        self.assertNotIn("Dow Jones", [x["index"] for x in r["regionen"] if x["region"] == "USA"])     # der Leitindex bleibt S&P 500
        self.assertIn("Historie", r["quellen"])

    def test_einstellungen_geprueft(self):
        with self.assertRaises(EinstellungsFehler):
            w.einstellungen_lesen({"welt": {"kraken": "BTC/EUR"}})
        with self.assertRaises(EinstellungsFehler):
            w.einstellungen_lesen({"welt": {"reihen": [{"name": "x"}]}})
        with self.assertRaises(EinstellungsFehler):
            w.einstellungen_lesen({"welt": {"behalten_tage": -1}})


class Einbau(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"KITRADER_DATEN": self.tmp.name, "KITRADER_KONFIG": str(Path(self.tmp.name) / "keine.toml")})
        p.start()
        self.addCleanup(p.stop)

    def schnappschuss(self):
        wlt = w.Welt(None, Path(self.tmp.name) / "welt.db", historie_pfad=Path(self.tmp.name) / "nirgends.db")
        wlt.speichern(w.aus_osiris(markt()), datetime.now(UTC))
        wlt.beenden()

    def lauf(self, *argv):
        aus, fehler = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(aus), contextlib.redirect_stderr(fehler):
            code = main(list(argv))
        return code, aus.getvalue(), fehler.getvalue()

    def test_befehl_ohne_daten(self):
        code, _, fehler = self.lauf("welt", "--ohne-abruf")
        self.assertEqual(code, 2)
        self.assertIn("kein Schnappschuss", fehler)

    def test_befehl_zeigt_tabelle_und_json(self):
        self.schnappschuss()
        code, aus, _ = self.lauf("welt", "--ohne-abruf")
        self.assertEqual(code, 0)
        self.assertIn("WELT-ROTATION", aus)
        self.assertIn("Hang Seng", aus)
        code, aus, _ = self.lauf("welt", "--ohne-abruf", "--json")
        self.assertEqual(json.loads(aus)["weltschnitt"]["n"], 6)

    def test_morgenlage_und_kontext(self):
        self.assertIsNone(w.morgen_block(mit_standard({}), Path(self.tmp.name)))
        self.schnappschuss()
        b = w.morgen_block(mit_standard({}), Path(self.tmp.name))
        self.assertIn("1 Woche: stärkste", b["text"])
        self.assertLess(len(b["text"]), 900)
        rot = w.rotation_lesen(mit_standard({}), Path(self.tmp.name))
        self.assertIn("Welt-Rotation" if "Welt-Rotation" in w.kontext_text(rot) else "WELT-ROTATION", w.kontext_text(rot))


if __name__ == "__main__":
    unittest.main()
