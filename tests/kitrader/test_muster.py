"""Muster-Engine (kitrader.historie.muster): Reaktionsrechnung, Basisvergleich, Ähnlichkeit, Korrelation, Text für die KI und Befehle.

Alles offline mit erfundenen Reihen; die Rechnungen sind von Hand nachgerechnet (Zahlen stehen in den Kommentaren).
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from kitrader.historie import cli, ereignisse as ereig, muster
from kitrader.historie.speicher import Speicher


def tage(start: str, n: int, wochenende: bool = True) -> list[str]:
    """n aufeinanderfolgende Tage ab start; mit wochenende=False nur Montag bis Freitag."""
    d = date.fromisoformat(start)
    erg = []
    while len(erg) < n:
        if wochenende or d.weekday() < 5:
            erg.append(d.isoformat())
        d += timedelta(days=1)
    return erg


def linear(start: str, n: int, w0: float = 100.0, schritt: float = 1.0, wochenende: bool = True, reihe: str = "dow", art: str = "rendite"):
    return muster.Verlauf(reihe, reihe, [(d, w0 + i * schritt) for i, d in enumerate(tage(start, n, wochenende))], art)


def ereignis(id_="e1", beginn="2020-01-10", genauigkeit="tag", art="unruhen", region="Frankreich", titel="Test", beschreibung="Beschreibung"):
    return {"id": id_, "beginn": beginn, "ende": None, "genauigkeit": genauigkeit, "art": art, "region": region, "titel": titel,
            "beschreibung": beschreibung, "quelle": {"name": "n", "url": "http://x", "abgerufen": "2026-10-26"}}


class Rechnen(unittest.TestCase):
    def test_reaktion_auf_linearer_reihe_von_hand(self):
        # Wert = 100 + Tagesnummer ab 2020-01-01 (alle Kalendertage). Beginn 2020-01-10 = Nr. 9 -> 109.
        v = linear("2020-01-01", 200)
        r = muster.reaktion(v, "2020-01-10")
        self.assertEqual(r["start"], ("2020-01-10", 109.0))
        self.assertAlmostEqual(r["h"]["1T"], 110 / 109 - 1)           # 2020-01-11
        self.assertAlmostEqual(r["h"]["1W"], 116 / 109 - 1)           # 2020-01-17 = Nr. 16
        self.assertAlmostEqual(r["h"]["1M"], 140 / 109 - 1)           # 2020-02-10 = Nr. 40
        self.assertAlmostEqual(r["h"]["3M"], 200 / 109 - 1)           # 2020-04-10 = Nr. 100 (2020 ist ein Schaltjahr)
        self.assertEqual(r["tief"], 0.0)                              # steigt nur: kein Rückgang
        self.assertAlmostEqual(r["hoch"], 200 / 109 - 1)

    def test_tief_und_hoch_im_fenster(self):
        w = [(d, 100.0) for d in tage("2020-01-01", 120)]
        w[15] = (w[15][0], 90.0)                                      # 2020-01-16: -10 % gegenüber 100
        w[60] = (w[60][0], 130.0)                                     # 2020-03-01: +30 %
        w[110] = (w[110][0], 50.0)                                    # 2020-04-20: liegt nach den 3 Monaten (Ziel 2020-04-10), zählt nicht
        v = muster.Verlauf("dow", "dow", w)
        r = muster.reaktion(v, "2020-01-10")
        self.assertAlmostEqual(r["tief"], -0.10)
        self.assertAlmostEqual(r["hoch"], 0.30)

    def test_wochenende_der_naechste_handelstag_zaehlt_als_ein_tag(self):
        # nur Montag bis Freitag; Beginn Samstag 2021-03-13: Start = Freitag 2021-03-12, "1 Tag" = Montag 2021-03-15
        v = linear("2021-03-01", 60, wochenende=False)
        d = v.d
        r = muster.reaktion(v, "2021-03-13")
        i = d.index("2021-03-12")
        self.assertEqual(r["start"], ("2021-03-12", v.w[i]))
        self.assertAlmostEqual(r["h"]["1T"], v.w[i + 1] / v.w[i] - 1)
        self.assertEqual(d[i + 1], "2021-03-15")
        self.assertAlmostEqual(r["h"]["1W"], v.w[d.index("2021-03-19")] / v.w[i] - 1)       # Zieltag Samstag 20.03.: letzter Wert Freitag 19.03.

    def test_nicht_abgedecktes_ziel_ist_none_und_reihe_vor_dem_start_ebenso(self):
        v = linear("2020-01-01", 30)                                   # endet 2020-01-30
        r = muster.reaktion(v, "2020-01-20")
        self.assertIsNotNone(r["h"]["1W"])
        self.assertIsNone(r["h"]["1M"])                                # Zieltag 2020-02-20 liegt mehr als 5 Tage nach dem Ende
        self.assertIsNone(r["h"]["3M"])
        self.assertIsNone(r["tief"])
        self.assertIsNone(muster.reaktion(v, "2019-12-01"))            # Reihe beginnt später
        self.assertIsNone(muster.reaktion(v, "2020-03-01"))            # Reihe endet lange vorher

    def test_monatsreihe_kennt_nur_monate(self):
        w = [(f"2020-{m:02d}-01", 100.0 + m) for m in range(1, 13)]
        v = muster.Verlauf("gold", "gold_monat", w)
        self.assertEqual(v.takt, "monat")
        r = muster.reaktion(v, "2020-02-15")
        self.assertEqual(set(r["h"]), {"1M", "3M"})
        self.assertAlmostEqual(r["h"]["1M"], 103 / 102 - 1)            # Märzmittel gegen Februarmittel
        self.assertAlmostEqual(r["h"]["3M"], 105 / 102 - 1)

    def test_differenz_fuer_zinsen(self):
        v = linear("2020-01-01", 200, w0=2.0, schritt=0.01, reihe="us_zins10_tag", art="differenz")
        r = muster.reaktion(v, "2020-01-10")
        self.assertAlmostEqual(r["h"]["1W"], 0.07)                     # 7 Tage * 0,01 Prozentpunkte
        self.assertAlmostEqual(r["h"]["1M"], 0.31)

    def test_negativer_oder_null_startwert_wird_nicht_als_rendite_gerechnet(self):
        w = [(d, 10.0) for d in tage("2020-01-01", 40)]
        w[9] = (w[9][0], -5.0)                                         # 2020-01-10 negativ (wie WTI am 20.04.2020)
        self.assertIsNone(muster.reaktion(muster.Verlauf("oel", "oel_wti", w), "2020-01-10"))

    def test_basis_enthaelt_jeden_starttag_wie_im_ereignisfall(self):
        v = linear("2020-01-01", 20)                                   # Werte 100 .. 119
        b = muster.basis(v)
        eintag = b["h"]["1T"]
        self.assertEqual(len(eintag), 19)                              # der letzte Tag hat keinen Folgewert
        self.assertAlmostEqual(eintag[0], 119 / 118 - 1)               # sortiert: kleinste Rendite zuerst (höchster Startwert mit Folgewert: 118)
        self.assertAlmostEqual(eintag[-1], 101 / 100 - 1)
        self.assertEqual(len(b["h"]["1W"]), 18)                        # Starttage 0..17: endet die Reihe bis zu 5 Tage vor dem Zieltag, gilt er noch als abgedeckt (wie in der Ereignisstudie)
        self.assertEqual(b["h"]["1M"], [])                             # 20 Tage reichen für keinen Monat
        self.assertEqual(b["n_start"], 20)
        # derselbe Rechenweg: Basiswert für Starttag 2020-01-03 == Reaktion mit Beginn 2020-01-03
        self.assertIn(muster.reaktion(v, "2020-01-03")["h"]["1W"], b["h"]["1W"])

    def test_basis_tief_hoch_gleitendes_fenster_gegen_einfache_rechnung(self):
        werte = [(d, 100 + ((i * 37) % 23) - (i % 7) * 3.0) for i, d in enumerate(tage("2020-01-01", 300))]
        v = muster.Verlauf("dow", "dow", werte)
        b = muster.basis(v)
        erwartet_tief, erwartet_hoch = [], []
        for i, (d, w0) in enumerate(werte):
            from kitrader.historie import zeit
            ziel = zeit.monate_addieren(d, 3)
            if not v.abgedeckt(ziel):
                continue
            fenster = [w for dd, w in werte[i + 1:] if dd <= ziel]
            if not fenster:
                continue
            erwartet_tief.append(min(0.0, min(fenster) / w0 - 1))
            erwartet_hoch.append(max(0.0, max(fenster) / w0 - 1))
        self.assertEqual(len(b["tief"]), len(erwartet_tief))
        for a, e in zip(b["tief"], sorted(erwartet_tief)):
            self.assertAlmostEqual(a, e)
        for a, e in zip(b["hoch"], sorted(erwartet_hoch)):
            self.assertAlmostEqual(a, e)

    def test_perzentil_mit_gleichen_werten(self):
        self.assertEqual(muster.perzentil([1, 2, 3, 4], 0), 0.0)
        self.assertEqual(muster.perzentil([1, 2, 3, 4], 5), 100.0)
        self.assertEqual(muster.perzentil([1, 2, 3, 4], 2.5), 50.0)
        self.assertEqual(muster.perzentil([1, 2, 2, 3], 2), 50.0)      # zwei Gleiche zählen halb: (1 + 3) / 2 / 4

    def test_stat_ehrlichkeit_unter_n5_nur_einzelwerte(self):
        vergleich = [i / 100 for i in range(-50, 50)]
        s = muster._stat([0.30, 0.02, -0.4], vergleich, "t")
        self.assertEqual(s["n"], 3)
        self.assertIsNone(s["median"])
        self.assertIsNone(s["anteil_negativ"])
        self.assertEqual(s["werte"], [0.30, 0.02, -0.4])
        e = muster._stat([0.485], vergleich, "t")                       # n = 1: Perzentil des Einzelwerts (99 von 100 Werten liegen darunter)
        self.assertAlmostEqual(e["perzentil"], 99.0)
        self.assertEqual(e["auffaellig"], "höher")
        self.assertIsNone(muster._stat([0.0], vergleich, "t")["auffaellig"])

    def test_stat_ab_n5_median_basis_und_zufallsvergleich(self):
        vergleich = [i / 100 for i in range(-50, 50)]                  # gleichmäßig von -0,50 bis +0,49: Median -0,005, 50 % negativ
        hoch = muster._stat([0.40, 0.42, 0.45, 0.47, 0.49], vergleich, "t")
        self.assertEqual(hoch["n"], 5)
        self.assertAlmostEqual(hoch["median"], 0.45)
        self.assertEqual(hoch["anteil_negativ"], 0.0)
        self.assertEqual(hoch["basis"]["anteil_negativ"], 0.5)
        self.assertAlmostEqual(hoch["perzentil_median"], 95.5)
        self.assertEqual(hoch["auffaellig"], "höher")                  # Median von fünf Zufallstagen liegt fast nie so hoch
        self.assertLessEqual(hoch["zufall_oben"], 0.05)
        mitte = muster._stat([-0.2, -0.1, 0.0, 0.1, 0.2], vergleich, "t")
        self.assertIsNone(mitte["auffaellig"])
        tief = muster._stat([-0.5, -0.49, -0.47, -0.45, -0.4], vergleich, "t")
        self.assertEqual(tief["auffaellig"], "tiefer")
        # wiederholbar (fester Startwert aus dem Namen)
        self.assertEqual(muster._stat([-0.2, -0.1, 0.0, 0.1, 0.2], vergleich, "t")["zufall_unten"], mitte["zufall_unten"])


class Menge(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.s = Speicher(Path(self._d.name))
        self.addCleanup(self.s.close)
        muster._BASIS.clear()
        self.addCleanup(muster._BASIS.clear)

    def laden(self, reihe, start, n, **kw):
        v = linear(start, n, **kw)
        self.s.schreiben(reihe, list(zip(v.d, v.w)), "Test")

    def test_mehrmarkt_mit_fehlenden_und_nicht_abgedeckten_reihen(self):
        self.laden("dow", "2020-01-01", 300)
        self.laden("btc", "2020-03-01", 200)                           # beginnt nach dem Ereignis
        res = muster.muster_menge([ereignis(beginn="2020-01-10")], self.s)
        namen = {g["gruppe"]: g for g in res["gruppen"]}
        self.assertEqual(set(namen), {"dow", "btc"})
        self.assertEqual(len(namen["dow"]["faelle"]), 1)
        self.assertEqual(namen["btc"]["faelle"], [])
        self.assertEqual(namen["btc"]["ohne"], [("e1", "Reihe beginnt erst 2020-03-01")])
        self.assertIn("dax", [k for k, _ in res["nicht_geladen"]])
        self.assertIn("vix", [k for k, _ in res["nicht_geladen"]])
        st = namen["dow"]["stat"]["1W"]
        self.assertEqual(st["n"], 1)
        self.assertAlmostEqual(st["werte"][0], 116 / 109 - 1)
        self.assertIsNotNone(st["perzentil"])

    def test_genauigkeit_steuert_die_horizonte(self):
        self.laden("dow", "2020-01-01", 300)
        res = muster.muster_menge([ereignis("tag", "2020-01-10", "tag"), ereignis("monat", "2020-01-01", "monat"),
                                   ereignis("jahr", "2020-01-01", "jahr")], self.s, ["dow"])
        g = res["gruppen"][0]
        f = {x["id"]: x for x in g["faelle"]}
        self.assertEqual(set(f), {"tag", "monat"})                     # "jahr" nur mit grobe=True
        self.assertIsNotNone(f["tag"]["h"]["1T"])
        self.assertIsNone(f["monat"]["h"]["1T"])                       # bei Monatsgenauigkeit sind Tag und Woche sinnlos
        self.assertIsNone(f["monat"]["h"]["1W"])
        self.assertIsNotNone(f["monat"]["h"]["1M"])
        self.assertEqual(res["ausgelassen"], [("jahr", "Beginn nur auf jahr genau")])
        mit = muster.muster_menge([ereignis("jahr", "2020-01-01", "jahr")], self.s, ["dow"], grobe=True)
        self.assertEqual(len(mit["gruppen"][0]["faelle"]), 1)

    def test_menge_ab_fuenf_hat_kennzahlen_darunter_nur_einzelfaelle(self):
        self.laden("dow", "2020-01-01", 400)
        liste = [ereignis(f"e{i}", f"2020-02-{i + 1:02d}") for i in range(5)]
        g = muster.muster_menge(liste, self.s, ["dow"])["gruppen"][0]
        self.assertEqual(g["stat"]["1M"]["n"], 5)
        self.assertIsNotNone(g["stat"]["1M"]["median"])
        g4 = muster.muster_menge(liste[:4], self.s, ["dow"])["gruppen"][0]
        self.assertEqual(g4["stat"]["1M"]["n"], 4)
        self.assertIsNone(g4["stat"]["1M"]["median"])
        self.assertEqual(len(g4["stat"]["1M"]["werte"]), 4)

    def test_unbekannte_gruppe_wird_gemeldet(self):
        with self.assertRaises(muster.MusterFehler):
            muster.muster_menge([ereignis()], self.s, ["gibtsnicht"])
        # Reihen-IDs sind erlaubt und wählen genau diese Reihe
        self.laden("gold_monat", "2020-01-01", 1)
        self.assertEqual(muster.gruppe_waehlen("gold_monat")[:3], ("gold", "Gold", ("gold_monat",)))

    def test_gold_vor_1971_und_bitcoin_vor_2014_bekommen_einen_hinweis(self):
        w = [(f"{j}-{m:02d}-01", 35.0) for j in range(1965, 1970) for m in range(1, 13)]
        self.s.schreiben("gold_monat", w, "Test")
        res = muster.muster_menge([ereignis(beginn="1968-05-03")], self.s, ["gold"])
        self.assertTrue(any("Goldpreis" in h for h in res["hinweise"]))


class Aehnlichkeit(unittest.TestCase):
    def test_merkmale_aus_text_deutsch_und_englisch(self):
        m = muster.merkmale("Ausschreitungen in Frankreich")
        self.assertEqual(list(m["arten"]), ["unruhen"])
        self.assertEqual(m["regionen"], ["Frankreich"])
        en = muster.merkmale("Riots in France after police shooting, Paris")
        self.assertIn("unruhen", en["arten"])
        self.assertEqual(en["regionen"], ["Frankreich"])
        self.assertIn("rohstoffschock", muster.merkmale("Oil price spike after Hormuz blockade")["arten"])
        self.assertIn("Naher Osten", muster.merkmale("Oil price spike after Hormuz blockade in Iran")["regionen"])
        self.assertEqual(muster.merkmale("Zinserhöhung der EZB")["arten"].keys(), {"geldpolitik"})
        self.assertEqual(muster.arten_aus_text("nichts Besonderes heute"), {})

    def test_region_des_ereignisses(self):
        self.assertEqual(muster.regionen_des_ereignisses("Westeuropa"), {"Europa"})
        self.assertEqual(muster.regionen_des_ereignisses("Naher Osten / Iran"), {"Naher Osten"})
        self.assertEqual(muster.regionen_des_ereignisses("USA/China"), {"USA", "Asien"})
        self.assertEqual(muster.regionen_des_ereignisses("Welt"), set())

    def test_rangfolge_gruende_und_ausklammern_junger_ereignisse(self):
        liste = [
            ereignis("a", "2005-10-27", art="unruhen", region="Frankreich", titel="Unruhen in den Vorstädten", beschreibung="Ausschreitungen nachts"),
            ereignis("b", "2011-08-06", art="unruhen", region="Vereinigtes Königreich", titel="Krawalle in London", beschreibung="Plünderungen"),
            ereignis("c", "2008-09-15", art="finanzkrise", region="USA", titel="Lehman", beschreibung="Pleite"),
            ereignis("d", "2018-11-17", art="konflikt", region="Frankreich", titel="Streit", beschreibung="Grenze"),
            ereignis("e", "2026-10-17", art="unruhen", region="Frankreich", titel="Heutige Proteste", beschreibung="Proteste"),
            ereignis("f", "1990-01-01", art="rezession", region="Japan", titel="Rezession", beschreibung="Konjunktur"),
        ]
        with mock.patch.object(muster.ereig, "alle", return_value=liste):
            res = muster.aehnliche("Ausschreitungen in Frankreich", n=10, mit_reaktion=False, heute="2026-10-26")
        ids = [t["ereignis"]["id"] for t in res["treffer"]]
        self.assertEqual(ids[0], "a")                                   # gleiche Art + gleiche Region + Stichwort
        self.assertIn("b", ids)                                         # gleiche Art + Großregion
        self.assertIn("d", ids)                                         # gleiche Region, andere Art (2,0 Punkte)
        self.assertNotIn("c", ids)
        self.assertNotIn("f", ids)
        self.assertNotIn("e", ids)                                      # 9 Tage alt: ausgeklammert, nicht still gelöscht
        self.assertEqual([x["id"] for x in res["ausgeklammert"]], ["e"])
        a = res["treffer"][0]
        self.assertIn("gleiche Art (unruhen)", a["gruende"])
        self.assertIn("gleiche Region (Frankreich)", a["gruende"])
        self.assertTrue(any(g.startswith("Stichwort:") and "ausschreitungen" in g for g in a["gruende"]))
        b = next(t for t in res["treffer"] if t["ereignis"]["id"] == "b")
        self.assertIn("gleiche Großregion (Europa)", b["gruende"])
        self.assertGreater(a["punkte"], b["punkte"])
        # Punkte von Hand: a = 3 (Art) + 2 (Region) + Stichwort; b = 3 + 1 (Großregion), kein Stichwort
        self.assertAlmostEqual(b["punkte"], 4.0)

    def test_ohne_erkennbare_merkmale_klare_meldung(self):
        with self.assertRaises(muster.MusterFehler):
            muster.aehnliche("Der Himmel ist blau", mit_reaktion=False)

    def test_verwandte_art_gibt_teilpunkte(self):
        liste = [ereignis("k", "2000-01-01", art="konflikt", region="Welt", titel="Konflikt", beschreibung="x")]
        with mock.patch.object(muster.ereig, "alle", return_value=liste):
            res = muster.aehnliche("Krieg in der Ukraine", mit_reaktion=False, heute="2026-10-26", min_punkte=1.0)
        self.assertEqual(len(res["treffer"]), 1)
        self.assertAlmostEqual(res["treffer"][0]["punkte"], 3 * 0.6 + 0.5)          # verwandte Art + weltweites Ereignis
        self.assertIn("verwandte Art (konflikt ~ krieg)", res["treffer"][0]["gruende"])

    def test_merkmale_aus_osiris_lage(self):
        daten = {"konflikte": {"zonen": [{"name": "UKRAINE WAR", "stufe": "war"}, {"name": "Sudan", "stufe": "high"}]},
                 "laenderrisiko": {"top": [{"land": "FR", "wert": 80, "stufe": "high", "merkmale": ["civil unrest"]}, {"land": "XX", "wert": 70, "merkmale": []}]},
                 "nachrichten": {"wichtig": [{"titel": "Riots and looting in Paris, protesters clash with police"}], "neueste": []}}
        m = muster.merkmale(daten)
        self.assertEqual(m["arten"]["krieg"], 1.0)
        self.assertEqual(m["arten"]["unruhen"], 0.5)                    # Meldungen sind ungeprüft: erst ab zwei Treffern, halbes Gewicht
        self.assertIn("Frankreich", m["regionen"])
        self.assertIn("Ukraine/Russland", m["regionen"])
        # eine einzelne Schlagzeile mit nur einem Stichwort zählt nicht
        schwach = muster.merkmale({"nachrichten": {"wichtig": [{"titel": "Protest vor der Botschaft"}], "neueste": []}})
        self.assertNotIn("unruhen", schwach["arten"])


def korrelierte_reihen(speicher, faktor=2.0, n=260):
    """Dow nur Mo-Fr; Bitcoin an allen Tagen, an Handelstagen mit der faktor-fachen Rendite (also Korrelation 1)."""
    d = tage("2021-01-04", n, wochenende=False)
    renditen = [0.0] + [0.01 * (((i * 7) % 11) - 5) / 5 for i in range(1, n)]       # -1 % bis +1 %
    renditen[50], renditen[120], renditen[121] = -0.025, -0.04, -0.035              # drei Stresstage
    dow, w = [], 1000.0
    for r in renditen:
        w *= 1 + r
        dow.append(w)
    btc, b, alle = [], 500.0, {}
    for dd, r in zip(d, renditen):
        b *= 1 + faktor * r
        alle[dd] = b
    kalender = tage(d[0], (date.fromisoformat(d[-1]) - date.fromisoformat(d[0])).days + 1)
    letzter = None
    for k in kalender:
        letzter = alle.get(k, letzter)
        btc.append((k, letzter))
    speicher.schreiben("dow", list(zip(d, dow)), "Test")
    speicher.schreiben("btc", btc, "Test")
    return renditen


class Korrelation(unittest.TestCase):
    def test_pearson_von_hand(self):
        self.assertAlmostEqual(muster.korrelation([1, 2, 3, 4], [2, 4, 6, 8]), 1.0)
        self.assertAlmostEqual(muster.korrelation([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertAlmostEqual(muster.korrelation([1, 2, 3], [1, 3, 2]), 0.5)           # sxy 1, sxx 2, syy 2
        self.assertIsNone(muster.korrelation([1, 1, 1], [1, 2, 3]))                      # keine Schwankung
        self.assertIsNone(muster.korrelation([1, 2], [1, 2]))                            # zu wenige Paare

    def test_gemeinsame_renditen_ueber_das_wochenende(self):
        aktien = [("2021-03-12", 100.0), ("2021-03-15", 102.0), ("2021-03-16", 101.0)]
        krypto = [(d, v) for d, v in zip(["2021-03-12", "2021-03-13", "2021-03-14", "2021-03-15", "2021-03-16"], [50.0, 55.0, 60.0, 45.0, 54.0])]
        p = muster.gemeinsame_renditen(aktien, krypto)
        self.assertEqual([x[0] for x in p], ["2021-03-15", "2021-03-16"])
        self.assertAlmostEqual(p[0][1], 0.02)                       # Freitag bis Montag
        self.assertAlmostEqual(p[0][2], 45 / 50 - 1)                # Krypto über dasselbe Intervall, nicht nur Sonntag bis Montag
        self.assertAlmostEqual(p[1][2], 54 / 45 - 1)
        # fehlt ein Kryptowert, entfällt das Paar
        self.assertEqual(len(muster.gemeinsame_renditen(aktien, krypto[:3])), 0)

    def test_rollierende_korrelation_von_hand(self):
        x = [1, 2, 3, 4]
        y = [1, 3, 2, 5]
        paare = [(f"2020-01-0{i + 1}", a, b) for i, (a, b) in enumerate(zip(x, y))]
        r = muster.rollierende_korrelation(paare, 3)
        self.assertEqual([d for d, _ in r], ["2020-01-03", "2020-01-04"])
        self.assertAlmostEqual(r[0][1], 0.5)                          # x 1,2,3 gegen y 1,3,2
        # x 2,3,4 gegen y 3,2,5: sxy = 2, sxx = 2, syy = 14/3 -> 2 / sqrt(2 * 14/3) = 0,6547
        self.assertAlmostEqual(r[1][1], 2 / (2 * 14 / 3) ** 0.5)
        self.assertEqual(muster.rollierende_korrelation(paare, 5), [])

    def test_zusammenhang_ende_zu_ende(self):
        with tempfile.TemporaryDirectory() as t, Speicher(Path(t)) as s:
            r = korrelierte_reihen(s)
            z = muster.zusammenhang(s)
        self.assertEqual(z["paare"], 259)
        for w in (30, 90):
            self.assertAlmostEqual(z["fenster"][w]["aktuell"], 1.0, places=6)           # Bitcoin-Rendite = 2 x Dow-Rendite
            self.assertAlmostEqual(z["fenster"][w]["verteilung"]["median"], 1.0, places=6)
        s2 = z["stress_tage"][-0.02]
        self.assertEqual(s2["n"], 3)                                                      # Dow -2,5 %, -4 %, -3,5 %
        self.assertAlmostEqual(s2["krypto_median"], -0.07, places=9)                      # Bitcoin doppelt so stark: -5 %, -8 %, -7 %, Median -7 %
        self.assertEqual(s2["krypto_anteil_negativ"], 1.0)
        self.assertIsNone(s2["korrelation"])                                              # n < 5: keine Kennzahl
        self.assertEqual(z["stress_tage"][-0.03]["n"], 2)
        self.assertEqual(z["stress_phasen"]["n_stress"] + z["stress_phasen"]["n_sonst"], 259)

    def test_gegenlaeufige_reihe_und_fehlende_reihen(self):
        with tempfile.TemporaryDirectory() as t, Speicher(Path(t)) as s:
            korrelierte_reihen(s, faktor=-1.0)
            z = muster.zusammenhang(s)
            self.assertAlmostEqual(z["fenster"][90]["aktuell"], -1.0, places=6)
        with tempfile.TemporaryDirectory() as t, Speicher(Path(t)) as s:
            with self.assertRaisesRegex(muster.MusterFehler, "nicht geladen"):
                muster.zusammenhang(s)
        with tempfile.TemporaryDirectory() as t, Speicher(Path(t)) as s:
            s.schreiben("shiller_sp", [("2020-01-01", 1.0)], "T")
            s.schreiben("btc", [("2020-01-01", 1.0)], "T")
            with self.assertRaisesRegex(muster.MusterFehler, "Tagesreihe"):
                muster.zusammenhang(s, aktien="shiller_sp")


class KiText(unittest.TestCase):
    def test_kontext_text_kompakt_mit_n_basis_und_hinweis(self):
        with tempfile.TemporaryDirectory() as t, Speicher(Path(t)) as s:
            korrelierte_reihen(s)
            muster._BASIS.clear()
            text = muster.kontext_text(text="Ausschreitungen in Frankreich", speicher=s, heute="2026-10-26")
        self.assertLessEqual(len(text), 3000)
        self.assertIn("Frühere Ereignisse, die zur Lage passen", text)
        self.assertIn("Gelbwesten", text)
        self.assertIn("n=", text)
        self.assertIn("Basis", text)
        self.assertIn("Krypto–Aktien", text)
        self.assertIn("garantiert nichts", text)
        self.assertIn("Nicht geladen", text)                                        # fehlende Reihen werden genannt, nicht erfunden

    def test_zeichengrenze_wird_eingehalten_und_wichtiges_bleibt(self):
        with tempfile.TemporaryDirectory() as t, Speicher(Path(t)) as s:
            korrelierte_reihen(s)
            text = muster.kontext_text(text="Ausschreitungen in Frankreich", speicher=s, heute="2026-10-26", max_zeichen=900)
        self.assertLessEqual(len(text), 900)
        self.assertIn("garantiert nichts", text)

    def test_ohne_daten_und_ohne_merkmale_kein_absturz(self):
        with tempfile.TemporaryDirectory() as t, Speicher(Path(t)) as s:
            text = muster.kontext_text(speicher=s)
        self.assertIn("Keine Ereignisart oder Region", text)
        self.assertIn("garantiert nichts", text)
        with tempfile.TemporaryDirectory() as t, Speicher(Path(t)) as s:
            text = muster.kontext_text(text="Ausschreitungen in Frankreich", speicher=s, heute="2026-10-26")
        self.assertIn("Keine Kursreihe geladen", text)


class Ereignisliste(unittest.TestCase):
    def test_unruhen_belegt_und_geprueft(self):
        liste = ereig.ereignisse(art="unruhen")
        self.assertGreaterEqual(len(liste), 12)
        self.assertLessEqual(len(liste), 20)
        self.assertEqual(ereig.pruefe_ereignisse(), [])
        wiki = [e for e in liste if "wikipedia" in e["quelle"]["url"]]
        self.assertLessEqual(len(wiki), 0.2 * len(liste))
        for e in liste:
            self.assertEqual(e["genauigkeit"], "tag", e["id"])                      # nur Ereignisse mit belegtem Tag
            self.assertTrue(e["quelle"]["url"].startswith("http"), e["id"])
            self.assertTrue(e["beschreibung"].strip(), e["id"])
        ids = {e["id"] for e in liste}
        for muss in ("1968-mai-unruhen-frankreich", "2005-banlieue-unruhen-frankreich", "2011-08-06-england-ausschreitungen",
                     "2018-11-17-gelbwesten-frankreich", "2021-01-06-kapitol-sturm", "2023-06-27-frankreich-unruhen-nahel"):
            self.assertIn(muss, ids)


class Befehle(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ort = Path(self._d.name)
        with Speicher(self.ort) as s:
            for reihe, start, n, kw in (("dow", "2017-01-02", 1800, dict(wochenende=False)), ("btc", "2017-01-02", 2500, {})):
                v = linear(start, n, **kw)
                s.schreiben(reihe, list(zip(v.d, v.w)), "Test")
        muster._BASIS.clear()
        self.addCleanup(muster._BASIS.clear)

    def lauf(self, *argv):
        puffer = io.StringIO()
        with mock.patch.dict(os.environ, {"KITRADER_DATEN": str(self.ort)}), contextlib.redirect_stdout(puffer):
            try:
                cli.main(list(argv))
            except SystemExit as e:
                return puffer.getvalue(), e.code
        return puffer.getvalue(), 0

    def test_muster_ereignis(self):
        aus, code = self.lauf("muster", "2018-11-17-gelbwesten-frankreich")
        self.assertEqual(code, 0)
        self.assertIn("Gelbwesten", aus)
        self.assertIn("Dow Jones", aus)
        self.assertIn("Bitcoin", aus)
        self.assertIn("normale Zeiträume", aus)
        self.assertIn("Nicht geladen", aus)
        self.assertIn("garantiert nichts", aus)

    def test_muster_art_und_region(self):
        aus, code = self.lauf("muster", "--art", "unruhen", "--region", "Frankreich")
        self.assertEqual(code, 0)
        self.assertIn("Muster Art „unruhen“, Region Frankreich", aus)
        self.assertIn("n=", aus)
        self.assertIn("Einzelfälle", aus)
        self.assertIn("Anteil der Fälle mit Rückgang", aus)
        aus, code = self.lauf("muster", "--art", "gibtsnicht")
        self.assertNotEqual(code, 0)

    def test_aehnlich(self):
        with mock.patch.object(muster.zeit, "heute", return_value=date(2026, 10, 26)):
            aus, code = self.lauf("aehnlich", "Ausschreitungen in Frankreich")
        self.assertEqual(code, 0)
        self.assertIn("Arten unruhen", aus)
        self.assertIn("Regionen Frankreich", aus)
        self.assertIn("gleiche Art (unruhen)", aus)
        self.assertIn("Ausgeklammert", aus)
        aus, code = self.lauf("aehnlich", "Der Himmel ist blau")
        self.assertNotEqual(code, 0)

    def test_zusammenhang_ohne_daten_klare_meldung(self):
        with tempfile.TemporaryDirectory() as t:
            self.ort = Path(t)
            aus, code = self.lauf("zusammenhang")
        self.assertNotEqual(code, 0)

    def test_zusammenhang(self):
        with Speicher(self.ort) as s:
            korrelierte_reihen(s)
        aus, code = self.lauf("zusammenhang")
        self.assertEqual(code, 0, aus)
        self.assertIn("Korrelation der Tagesrenditen", aus)
        self.assertIn("30 Handelstage", aus)
        self.assertIn("Stressphasen", aus)
