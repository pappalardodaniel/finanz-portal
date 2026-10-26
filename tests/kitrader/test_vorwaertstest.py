"""Vorwärtstest der KI: Handelsrechnung von Hand, Antwortprüfung, Lauf auf nachgebildeten Kursen (Ziel, Stop, Ablauf, Lücke,
Fehler), Auswertung, Fehleralarm, Selbsttest, Technikprüfung, Treiber. Alles offline: Fake-Treiber, nachgebildete Kurse."""
import contextlib
import io
import json
import math
import random
import stat
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from kitrader import ki
from kitrader import vorwaertstest as vt
from kitrader.broker.basis import Kerze, Kurs

UTC = timezone.utc
START = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
GRENZEN = {15: (0.05, 0.5), 60: (0.05, 0.9)}


# ----------------------------------------------------------------------------- Hilfen

class SkriptQuelle:
    """Kurse nach Drehbuch: `preis(minuten_seit_start)` ergibt die Mitte; feste Spanne zwischen Bid und Ask."""

    def __init__(self, uhr, preis, spanne: float = 0.02):
        self.uhr, self.preis, self.spanne, self.t0 = uhr, preis, spanne, uhr()
        self.ausfall = False

    def mitte(self, symbol, t=None):
        return self.preis((vt.zu_utc(t or self.uhr()) - self.t0).total_seconds() / 60)

    def kurs(self, symbol):
        if self.ausfall:
            raise vt.QuoteFehler("Ausfall")
        m = self.mitte(symbol)
        return Kurs(symbol, m - self.spanne / 2, m + self.spanne / 2, m, self.uhr() - timedelta(seconds=5))

    def kerzen(self, symbol, takt, anzahl):
        minuten = {"1m": 1, "5m": 5}[takt]
        p0 = self.preis(0)
        jetzt = vt.zu_utc(self.uhr())
        return [Kerze(jetzt - timedelta(minutes=minuten * (anzahl - i)), p0 * (1 + 0.0001 * math.sin(i)), p0 * 1.0003, p0 * 0.9997,
                      p0 * (1 + 0.0001 * math.sin(i + 1)), 1.0) for i in range(anzahl)]


def antwort(richtung="steigt", p=0.7, ziel=100.3, stop=99.7, hebel=2, richtung60=None, ziel60=None, stop60=None):
    def eins(r, z, s):
        d = {"richtung": r, "wahrscheinlichkeit": p, "einstieg": "markt", "hebel": hebel,
             "begruendung": {"kurs": "Kurs steigt", "historie": "kein Beitrag", "weltlage": "kein Beitrag"}}
        d.update(ziel=None if r == "seitwaerts" else z, stop=None if r == "seitwaerts" else s)
        return d
    return json.dumps({"m15": eins(richtung, ziel, stop), "m60": eins(richtung60 or richtung, ziel60 or ziel, stop60 or stop)})


def lauf(tmp, preis, treiber, dauer=1, nachlauf=None, symbole=("X/EUR",), **opt):
    """Ein Lauf auf einem Drehbuch. Gibt (Test, Uhr, Quelle, Prompts) zurück; der Aufrufer schließt `t.schliessen()`."""
    uhr = vt.SimUhr(START)
    quelle = SkriptQuelle(uhr, preis)
    prompts = []

    def fragen(prompt):
        prompts.append(prompt)
        return treiber(prompt) if callable(treiber) else treiber
    standard = dict(symbole=symbole, quelle=quelle, treiber=fragen, uhr=uhr, schlafen=uhr.schlafen, einst={}, parallel=False,
                    schritt_s=30, tick_s=60, ausgabe=lambda s: None, lernen=False, regeln=False,
                    kosten_faktor=0.0, trend_serie=0, kontext_geber=lambda j: ("OSIRIS-Test", "", "Historie-Test"))
    standard.update(opt)
    t = vt.Vorwaertstest(tmp, **standard)
    t.zusammen = t.laufen(dauer_min=dauer, nachlauf_min=nachlauf)
    return t, uhr, quelle, prompts


def zeilen(t, sql, *par):
    return [dict(z) for z in t.db.execute(sql, par)]


class MitOrdner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def neu(self, *a, **k):
        t, uhr, quelle, prompts = lauf(self.dir, *a, **k)
        self.addCleanup(t.schliessen)
        return t, uhr, quelle, prompts


# ----------------------------------------------------------------------------- Rechnung von Hand

class RechnungTest(unittest.TestCase):
    def test_long_gewinn(self):
        # Kauf 100, Verkauf 101, Gebühr 0,4 % je Seite: Brutto +1 %; Gebühren 0,4 % auf 100 und 0,4 % auf 101 = 0,4 + 0,404 = 0,804 %
        r = vt.handel_ergebnis("long", 100, 101, 0.4)
        self.assertAlmostEqual(r["vor_1x"], 0.01, places=12)
        self.assertAlmostEqual(r["nach_1x"], 0.01 - 0.004 - 0.00404, places=12)       # +0,196 %
        self.assertAlmostEqual(vt.handel_ergebnis("long", 100, 101, 0.4, hebel=3)["nach_hebel"], 3 * 0.00196, places=12)

    def test_short_gewinn(self):
        # Verkauf 100, Rückkauf 99: Brutto +1 %; Gebühren 0,4 + 0,396 = 0,796 %
        r = vt.handel_ergebnis("short", 100, 99, 0.4)
        self.assertAlmostEqual(r["vor_1x"], 0.01, places=12)
        self.assertAlmostEqual(r["nach_1x"], 0.01 - 0.004 - 0.00396, places=12)       # +0,204 %

    def test_verluste(self):
        r = vt.handel_ergebnis("long", 100, 98, 0.4, hebel=3)
        self.assertAlmostEqual(r["vor_1x"], -0.02, places=12)
        self.assertAlmostEqual(r["nach_1x"], -0.02 - 0.004 - 0.00392, places=12)      # -2,792 %
        self.assertAlmostEqual(r["nach_hebel"], -3 * 0.02792, places=12)
        r = vt.handel_ergebnis("short", 100, 102, 0.4)
        self.assertAlmostEqual(r["vor_1x"], -0.02, places=12)
        self.assertAlmostEqual(r["nach_1x"], -0.02 - 0.004 - 0.00408, places=12)      # -2,808 %

    def test_ohne_gebuehr_und_totalverlust(self):
        self.assertAlmostEqual(vt.handel_ergebnis("long", 100, 110, 0)["nach_1x"], 0.10)
        self.assertEqual(vt.handel_ergebnis("long", 100, 50, 0.4, hebel=3)["vor_hebel"], -1.0)       # nie mehr als der Einsatz
        self.assertEqual(vt.handel_ergebnis("short", 100, 300, 0, hebel=2)["nach_hebel"], -1.0)

    def test_falsche_eingaben(self):
        with self.assertRaises(ValueError):
            vt.handel_ergebnis("kaufen", 100, 101, 0.4)
        with self.assertRaises(ValueError):
            vt.handel_ergebnis("long", 0, 101, 0.4)

    def test_spiegelung_long_short(self):
        # Fällt der Kurs, gewinnt Short genau so viel, wie Long verliert (vor Gebühren, bei gleichem Wertverhältnis)
        a, b = vt.handel_ergebnis("long", 100, 97, 0), vt.handel_ergebnis("short", 100, 97, 0)
        self.assertAlmostEqual(a["vor_1x"], -b["vor_1x"])

    def test_richtung_von_aenderung(self):
        s = 0.05
        self.assertEqual(vt.richtung_von_aenderung(0.05, s), "steigt")
        self.assertEqual(vt.richtung_von_aenderung(0.0499, s), "seitwaerts")
        self.assertEqual(vt.richtung_von_aenderung(0, s), "seitwaerts")
        self.assertEqual(vt.richtung_von_aenderung(-0.0499, s), "seitwaerts")
        self.assertEqual(vt.richtung_von_aenderung(-0.05, s), "faellt")
        self.assertEqual(vt.seite_von_richtung("steigt"), "long")
        self.assertEqual(vt.seite_von_richtung("faellt"), "short")
        self.assertIsNone(vt.seite_von_richtung("seitwaerts"))

    def test_pruefe_trade_long(self):
        # Long wird zum Bid verkauft
        self.assertEqual(vt.pruefe_trade("long", 101, 99, 100.5, 100.6, False), None)
        self.assertEqual(vt.pruefe_trade("long", 101, 99, 101.2, 101.3, False), ("ziel", 101))          # Ziel: zum Zielkurs
        self.assertEqual(vt.pruefe_trade("long", 101, 99, 98.0, 98.1, False), ("stop", 98.0))           # Stop: zum beobachteten Kurs
        self.assertEqual(vt.pruefe_trade("long", 101, 99, 100.0, 100.1, True), ("ablauf", 100.0))       # Ablauf: Bid
        self.assertEqual(vt.pruefe_trade("long", 101, 99, 100.9, 101.5, False), None)                   # nur das Ask über dem Ziel zählt nicht

    def test_pruefe_trade_short(self):
        # Short wird zum Ask zurückgekauft
        self.assertEqual(vt.pruefe_trade("short", 99, 101, 99.4, 99.5, False), None)
        self.assertEqual(vt.pruefe_trade("short", 99, 101, 98.5, 98.9, False), ("ziel", 99))
        self.assertEqual(vt.pruefe_trade("short", 99, 101, 101.5, 101.8, False), ("stop", 101.8))
        self.assertEqual(vt.pruefe_trade("short", 99, 101, 100.0, 100.1, True), ("ablauf", 100.1))
        self.assertEqual(vt.pruefe_trade("short", 99, 101, 98.0, 99.5, False), None)                    # nur das Bid unter dem Ziel zählt nicht

    def test_erwartung(self):
        self.assertAlmostEqual(vt.erwartung_prozent(0.7, 1.0, 0.5), 0.7 - 0.15)
        self.assertAlmostEqual(vt.erwartung_prozent(0.5, 0.4, 0.4), 0.0)

    def test_wilson(self):
        lo, hi = vt.wilson(5, 10)
        self.assertAlmostEqual(lo, 0.2366, places=3)
        self.assertAlmostEqual(hi, 0.7634, places=3)
        lo, hi = vt.wilson(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.2775, places=3)
        self.assertAlmostEqual(vt.wilson(10, 10)[0], 0.7225, places=3)
        self.assertEqual(vt.wilson(0, 0), (0.0, 1.0))

    def test_unterschied(self):
        d, lo, hi = vt.unterschied_bereich(8, 10, 2, 10)
        self.assertAlmostEqual(d, 0.6)
        self.assertGreater(lo, 0)
        d, lo, hi = vt.unterschied_bereich(5, 10, 5, 10)
        self.assertAlmostEqual(d, 0)
        self.assertLess(lo, 0)
        self.assertGreater(hi, 0)


# ----------------------------------------------------------------------------- Antwort der KI prüfen

class AntwortTest(unittest.TestCase):
    def pruefen(self, text, bid=99.99, ask=100.01):
        return vt.pruefe_vorhersagen(text, bid, ask, GRENZEN)

    def aendere(self, **felder):
        d = json.loads(antwort())
        d["m15"].update(felder)
        return json.dumps(d)

    def test_gueltig(self):
        e = self.pruefen(antwort())
        self.assertEqual(e[15]["richtung"], "steigt")
        self.assertEqual(e[60]["ziel"], 100.3)
        self.assertEqual(e[15]["begruendung"]["kurs"], "Kurs steigt")
        self.assertIsInstance(e[15]["hebel"], int)

    def test_mit_markdown_zaun(self):
        self.assertEqual(self.pruefen("```json\n" + antwort() + "\n```")[15]["richtung"], "steigt")

    def test_seitwaerts_ohne_ziel(self):
        e = self.pruefen(antwort("seitwaerts", p=0.5))
        self.assertIsNone(e[15]["ziel"])
        d = json.loads(antwort("seitwaerts"))
        del d["m15"]["hebel"]
        self.assertEqual(self.pruefen(json.dumps(d))[15]["hebel"], 1)

    def test_short(self):
        e = self.pruefen(antwort("faellt", ziel=99.7, stop=100.3))
        self.assertEqual(e[60]["richtung"], "faellt")

    def fehler(self, text):
        with self.assertRaises(ki.AntwortUngueltig) as cm:
            self.pruefen(text)
        return str(cm.exception)

    def test_kein_json(self):
        self.assertIn("JSON", self.fehler("Hier ist meine Antwort: " + antwort()))
        self.assertIn("Objekt", self.fehler("[1, 2]"))
        self.assertIn("fehlt", self.fehler("{}"))

    def test_seite_von_ziel_und_stop(self):
        self.assertIn("richtigen Seite", self.fehler(antwort("steigt", ziel=99.7, stop=100.3)).replace("über und der Stop unter", "richtigen Seite"))
        self.assertIn("faellt", self.fehler(antwort("faellt", ziel=100.3, stop=99.7)))

    def test_abstand(self):
        self.assertIn("entfernt", self.fehler(antwort(ziel=102.0, stop=99.7)))            # Ziel 2 % weg, erlaubt bis 0,5 %
        self.assertIn("entfernt", self.fehler(antwort(ziel=100.3, stop=100.0)))           # Stop praktisch am Kurs
        self.assertIn("entfernt", self.fehler(antwort(ziel=100.011, stop=99.7)))          # Ziel unter der Mindestweite

    def test_felder(self):
        self.assertIn("richtung", self.fehler(self.aendere(richtung="hoch")))
        self.assertIn("wahrscheinlichkeit", self.fehler(self.aendere(wahrscheinlichkeit=1.2)))
        self.assertIn("wahrscheinlichkeit", self.fehler(self.aendere(wahrscheinlichkeit=True)))
        self.assertIn("wahrscheinlichkeit", self.fehler(self.aendere(wahrscheinlichkeit="0.7")))
        self.assertIn("hebel", self.fehler(self.aendere(hebel=4)))
        self.assertIn("hebel", self.fehler(self.aendere(hebel=1.5)))
        self.assertIn("hebel", self.fehler(self.aendere(hebel=0)))
        self.assertIn("einstieg", self.fehler(self.aendere(einstieg=100.0)))
        self.assertIn("begruendung", self.fehler(self.aendere(begruendung="nur Text")))
        self.assertIn("begruendung", self.fehler(self.aendere(begruendung={"kurs": "x y z", "historie": "x y z"})))
        self.assertIn("Preise", self.fehler(self.aendere(ziel="hoch")))
        self.assertIn("Preise", self.fehler(self.aendere(stop=None)))

    def test_alle_fehler_zusammen(self):
        text = self.fehler(self.aendere(hebel=9, richtung="hoch"))
        self.assertIn("hebel", text)
        self.assertIn("richtung", text)


# ----------------------------------------------------------------------------- Prompt

class PromptTest(unittest.TestCase):
    def prompt(self, lektionen=""):
        return vt.prompt_vorhersage("BTC/EUR", "## Kontext\nX", vt.STANDARD_SCHWELLEN, 0.4, {15: (0.05, 0.8), 60: (0.05, 1.5)}, 0.03, lektionen)

    def test_nutzt_grundregeln_aus_ki(self):
        p = self.prompt()
        self.assertIn(ki.REGELN.format(wert="BTC/EUR"), p)
        for stueck in ("m15", "m60", "0,05 %", "0,10 %", "Ask", "Bid", "0,40 %", "0,80 %", "ganze Zahl 1 bis 3", "KONTEXT\n## Kontext"):
            self.assertIn(stueck, p)

    def test_lektionen_nur_wenn_vorhanden(self):
        self.assertNotIn("LEKTIONEN", self.prompt())
        text = vt.lektionen_block(2, [{"regel": "Stop weiter weg setzen.", "begruendung": "Stops zu eng", "belege": 4}], 30, vt.Regeln())
        p = self.prompt(text)
        self.assertIn("LEKTIONEN AUS DEINEN BISHERIGEN VORHERSAGEN (Version 2, gelernt aus 30", p)
        self.assertIn("1. Stop weiter weg setzen. (Begründung: Stops zu eng; Belege: 4)", p)
        self.assertLess(p.index("LEKTIONEN"), p.index("KONTEXT\n"))

    def test_ki_prompt_unveraendert_nutzbar(self):
        # ki.prompt_bauen nutzt dieselben Regeln; nichts doppelt
        e = vt._einst.mit_standard({})
        self.assertIn(ki.REGELN.format(wert="ABC"), ki.prompt_bauen("ABC", "K", e))

    def test_kontext_kennzeichnet_laufende_kerze(self):
        uhr = vt.SimUhr(START)
        q = vt.SimQuelle(uhr, ("X/EUR",), start_preis=100)
        k1, k5 = q.kerzen("X/EUR", "1m", 120), q.kerzen("X/EUR", "5m", 288)
        quote = vt.quote_aus_kurs(q.kurs("X/EUR"), uhr())
        ctx = vt.kontext_vorhersage("X/EUR", uhr(), quote, k1, k5, "OSIRIS-Text", "Hinweis X", "Historie-Text")
        for stueck in ("## 1-Minuten-Kerzen", "## 5-Minuten-Kerzen", "OSIRIS-Text", "Hinweis X", "Historie-Text", "Bid", "Ask"):
            self.assertIn(stueck, ctx)
        self.assertIn("Takt 1m", ctx)
        self.assertIn("Takt 5m", ctx)

    def test_grenzen(self):
        lo, hi = vt.abstand_grenzen(15, 0.03)
        self.assertEqual(lo, vt.ZIEL_MIN_PCT)
        self.assertAlmostEqual(hi, 6 * 0.03 * math.sqrt(15))     # das Sechsfache der Schwankung des Horizonts (0,70 %)
        self.assertAlmostEqual(vt.abstand_grenzen(15, 0.001)[1], 0.5)       # bei sehr ruhigem Markt mindestens 0,5 %
        self.assertEqual(vt.abstand_grenzen(60, 5.0)[1], 5.0)    # nie über die harte Obergrenze
        self.assertGreater(vt.abstand_grenzen(60, None)[1], 0)


# ----------------------------------------------------------------------------- Lauf: Ziel, Stop, Ablauf (von Hand nachgerechnet)

class HandelLaufTest(MitOrdner):
    GEB = 0.004

    def test_long_ziel(self):
        # Kurs 100 (Bid 99,99 / Ask 100,01), springt nach 7 Minuten auf 100,4: Bid 100,39 >= Ziel 100,3 -> Ziel, Ausstieg zum Zielkurs
        t, *_ = self.neu(lambda m: 100.0 if m < 7 else 100.4, antwort("steigt"))
        for z in zeilen(t, "SELECT * FROM trades ORDER BY horizont_min"):
            self.assertEqual((z["status"], z["seite"], z["hebel"], z["einstieg"], z["ausstieg"]), ("ziel", "long", 2, 100.01, 100.3))
            vor = 100.3 / 100.01 - 1
            nach = vor - self.GEB - self.GEB * (100.3 / 100.01)
            self.assertAlmostEqual(z["vor_1x"], vor, places=12)
            self.assertAlmostEqual(z["nach_1x"], nach, places=12)                    # -0,511 %: auch ein Treffer-Ziel verliert nach Gebühren
            self.assertAlmostEqual(z["nach_hebel"], 2 * nach, places=12)
            self.assertAlmostEqual(z["gewinn"], 1000 * 2 * nach, places=9)
            self.assertAlmostEqual(z["best"], 100.39 / 100.01 - 1, places=12)
        v = zeilen(t, "SELECT * FROM vorhersagen ORDER BY horizont_min")
        self.assertEqual([x["status"] for x in v], ["ausgewertet", "ausgewertet"])
        self.assertEqual([x["eingetreten"] for x in v], ["steigt", "steigt"])         # +0,4 % in beiden Horizonten
        self.assertEqual([x["treffer"] for x in v], [1, 1])

    def test_long_stop(self):
        t, *_ = self.neu(lambda m: 100.0 if m < 7 else 99.5, antwort("steigt"))
        for z in zeilen(t, "SELECT * FROM trades"):
            self.assertEqual(z["status"], "stop")
            self.assertEqual(z["ausstieg"], 99.49)                                    # beobachteter Bid, schlechter als der Stop 99,7
            self.assertAlmostEqual(z["vor_1x"], 99.49 / 100.01 - 1, places=12)
            self.assertLess(z["nach_1x"], z["vor_1x"])
        self.assertEqual([x["eingetreten"] for x in zeilen(t, "SELECT * FROM vorhersagen")], ["faellt", "faellt"])
        self.assertEqual([x["treffer"] for x in zeilen(t, "SELECT * FROM vorhersagen")], [0, 0])

    def test_short_ziel_und_stop(self):
        t, *_ = self.neu(lambda m: 100.0 if m < 7 else 99.5, antwort("faellt", ziel=99.7, stop=100.3))
        for z in zeilen(t, "SELECT * FROM trades"):
            self.assertEqual((z["status"], z["seite"], z["einstieg"], z["ausstieg"]), ("ziel", "short", 99.99, 99.7))   # Short: Einstieg Bid
            vor = 1 - 99.7 / 99.99
            self.assertAlmostEqual(z["vor_1x"], vor, places=12)
            self.assertAlmostEqual(z["nach_1x"], vor - self.GEB - self.GEB * (99.7 / 99.99), places=12)
        self.assertEqual([x["treffer"] for x in zeilen(t, "SELECT * FROM vorhersagen")], [1, 1])
        self.tearDown(), self.setUp()
        t, *_ = self.neu(lambda m: 100.0 if m < 7 else 100.5, antwort("faellt", ziel=99.7, stop=100.3))
        for z in zeilen(t, "SELECT * FROM trades"):
            self.assertEqual((z["status"], z["ausstieg"]), ("stop", 100.51))            # Short steigt zum Ask aus
            self.assertAlmostEqual(z["vor_1x"], 1 - 100.51 / 99.99, places=12)

    def test_ablauf_bei_stillem_kurs(self):
        t, *_ = self.neu(lambda m: 100.0, antwort("steigt"))
        z15, z60 = zeilen(t, "SELECT * FROM trades ORDER BY horizont_min")
        for z in (z15, z60):
            self.assertEqual((z["status"], z["ausstieg"]), ("ablauf", 99.99))           # Ablauf zum Bid; der Spread kostet
            self.assertAlmostEqual(z["vor_1x"], 99.99 / 100.01 - 1, places=12)
        self.assertEqual(z15["ausstieg_zeit"], z15["faellig"])                           # Auswertung genau zum Ablauf
        self.assertEqual(z60["ausstieg_zeit"], z60["faellig"])
        v = zeilen(t, "SELECT * FROM vorhersagen ORDER BY horizont_min")
        self.assertEqual([x["eingetreten"] for x in v], ["seitwaerts", "seitwaerts"])   # 0,00 % unter der Schwelle
        self.assertEqual([x["treffer"] for x in v], [0, 0])                             # steigt gesagt, nichts passiert

    def test_seitwaerts_trifft_ohne_trade(self):
        t, *_ = self.neu(lambda m: 100.0, antwort("seitwaerts", p=0.5))
        self.assertEqual(zeilen(t, "SELECT COUNT(*) n FROM trades")[0]["n"], 0)
        self.assertEqual([x["treffer"] for x in zeilen(t, "SELECT * FROM vorhersagen")], [1, 1])

    def test_zeit_und_kurs_bei_abgabe(self):
        t, uhr, *_ = self.neu(lambda m: 100.0, antwort("steigt"))
        v = zeilen(t, "SELECT * FROM vorhersagen ORDER BY horizont_min")[0]
        self.assertEqual((v["bid"], v["ask"], v["mitte"], v["kurs_frage"]), (99.99, 100.01, 100.0, 100.0))
        self.assertEqual(v["abgabe"], START.isoformat())
        self.assertEqual(vt._zeit(v["faellig"]), START + timedelta(minutes=15))
        self.assertEqual(len(v["kontext_pruefsumme"]), 16)
        a = zeilen(t, "SELECT * FROM aufrufe")[0]
        self.assertEqual(a["art"], "vorhersage")
        self.assertEqual(a["kontext_pruefsumme"], v["kontext_pruefsumme"])
        self.assertIn("## 1-Minuten-Kerzen", vt.zlib.decompress(a["kontext_z"]).decode())

    def test_gebuehr_080_und_hebel_nur_ergebnis(self):
        t, *_ = self.neu(lambda m: 100.0 if m < 7 else 100.4, antwort("steigt", hebel=3), gebuehr=0.8)
        z = zeilen(t, "SELECT * FROM trades")[0]
        f = 0.008
        nach = (100.3 / 100.01 - 1) - f - f * (100.3 / 100.01)
        self.assertAlmostEqual(z["nach_1x"], nach, places=12)
        self.assertAlmostEqual(z["nach_hebel"], 3 * nach, places=12)
        self.assertEqual(z["gebuehr_pct"], 0.8)

    def test_trade_wird_verworfen_wenn_stop_schon_ueberschritten(self):
        # Die KI sah 100,0; bis zum Einstieg (Treiber braucht Zeit) fällt der Kurs unter den Stop
        uhr_ref = []

        def preis(m):
            return 100.0 if m < 0.2 else 99.5

        def treiber(prompt):
            uhr_ref[0].schlafen(30)
            return antwort("steigt", stop=99.7)
        uhr = vt.SimUhr(START)
        uhr_ref.append(uhr)
        quelle = SkriptQuelle(uhr, preis)
        t = vt.Vorwaertstest(self.dir, symbole=("X/EUR",), quelle=quelle, treiber=treiber, uhr=uhr, schlafen=uhr.schlafen, einst={}, parallel=False,
                             ausgabe=lambda s: None, lernen=False, regeln=False, kontext_geber=lambda j: ("O", "", "H"))
        self.addCleanup(t.schliessen)
        t.laufen(dauer_min=1)
        tr = zeilen(t, "SELECT * FROM trades")
        self.assertEqual([x["status"] for x in tr], ["verworfen", "verworfen"])
        self.assertEqual(zeilen(t, "SELECT COUNT(*) n FROM vorhersagen WHERE status='ausgewertet'")[0]["n"], 2)   # die Richtung zählt trotzdem


# ----------------------------------------------------------------------------- Robustheit

class RobustheitTest(MitOrdner):
    def test_ki_fehler_wird_protokolliert_und_der_lauf_geht_weiter(self):
        zaehler = []

        def treiber(prompt):
            zaehler.append(1)
            if len(zaehler) == 1:
                raise ki.KiFehler("CLI abgestürzt")
            return antwort("steigt")
        meldungen = []
        t, *_ = self.neu(lambda m: 100.0, treiber, dauer=7, symbole=("X/EUR",), ausgabe=meldungen.append)
        self.assertEqual(len(zeilen(t, "SELECT * FROM aufrufe WHERE fehler IS NOT NULL")), 1)
        self.assertIn("CLI abgestürzt", zeilen(t, "SELECT fehler FROM aufrufe WHERE fehler IS NOT NULL")[0]["fehler"])
        self.assertEqual(len(zeilen(t, "SELECT * FROM vorhersagen")), 2)               # Runde 2 (nach 5 Minuten) lief
        self.assertTrue(any("CLI abgestürzt" in m for m in meldungen))

    def test_nachfrage_bei_ungueltiger_antwort(self):
        antworten = iter(["Kein JSON", antwort("steigt")])
        t, _, _, prompts = self.neu(lambda m: 100.0, lambda p: next(antworten), dauer=1)
        self.assertEqual(len(prompts), 2)
        self.assertIn("DEINE VORIGE ANTWORT WAR UNGÜLTIG", prompts[1])
        a = zeilen(t, "SELECT * FROM aufrufe")[0]
        self.assertEqual((a["versuche"], a["fehler"]), (2, None))
        self.assertEqual(len(zeilen(t, "SELECT * FROM vorhersagen")), 2)

    def test_zweimal_ungueltig_gibt_auf(self):
        t, _, _, prompts = self.neu(lambda m: 100.0, "Kein JSON", dauer=1, nachlauf=0)
        self.assertEqual(len(prompts), 2)
        self.assertEqual(len(zeilen(t, "SELECT * FROM vorhersagen")), 0)
        self.assertIn("ungültig", zeilen(t, "SELECT fehler FROM aufrufe")[0]["fehler"])

    def test_grenze_fuer_ki_aufrufe(self):
        t, _, _, prompts = self.neu(lambda m: 100.0, antwort("seitwaerts", p=0.5), dauer=30, nachlauf=0, max_ki_aufrufe=2)
        self.assertEqual(len(prompts), 2)
        self.assertEqual(len(zeilen(t, "SELECT * FROM vorhersagen")), 4)               # zwei Runden, dann Grenze
        self.assertIn("Grenze", zeilen(t, "SELECT fehler FROM aufrufe WHERE fehler IS NOT NULL")[0]["fehler"])

    def test_kein_netz_waehrend_der_frage(self):
        def preis(m):
            return 100.0
        uhr = vt.SimUhr(START)
        quelle = SkriptQuelle(uhr, preis)
        quelle.ausfall = True
        t = vt.Vorwaertstest(self.dir, symbole=("X/EUR",), quelle=quelle, treiber=lambda p: antwort(), uhr=uhr, schlafen=uhr.schlafen, einst={},
                             parallel=False, ausgabe=lambda s: None, lernen=False, regeln=False, kontext_geber=lambda j: ("O", "", "H"))
        self.addCleanup(t.schliessen)
        z = t.laufen(dauer_min=1)
        self.assertEqual(z["vorhersagen"], 0)
        self.assertIn("Ausfall", zeilen(t, "SELECT fehler FROM aufrufe")[0]["fehler"])

    def test_kurslücke_nimmt_trades_aus_der_wertung(self):
        # Kein Kurs von Minute 3 bis 25: die Überwachung war unterbrochen -> Trades 'luecke'; das 15-Minuten-Ende fällt in die Lücke
        uhr = vt.SimUhr(START)
        quelle = SkriptQuelle(uhr, lambda m: 100.0)

        def schlafen(s):
            uhr.schlafen(s)
            m = (uhr() - START).total_seconds() / 60
            quelle.ausfall = 3 <= m < 25
        t = vt.Vorwaertstest(self.dir, symbole=("X/EUR",), quelle=quelle, treiber=lambda p: antwort(), uhr=uhr, schlafen=schlafen, einst={},
                             parallel=False, schritt_s=30, ausgabe=lambda s: None, lernen=False, regeln=False,
                             kontext_geber=lambda j: ("O", "", "H"))
        self.addCleanup(t.schliessen)
        t.laufen(dauer_min=1)
        self.assertEqual({x["status"] for x in zeilen(t, "SELECT * FROM trades")}, {"luecke"})
        v = {x["horizont_min"]: x["status"] for x in zeilen(t, "SELECT * FROM vorhersagen")}
        self.assertEqual(v[15], "luecke")             # der Auswertungskurs kam 10 Minuten zu spät
        self.assertEqual(v[60], "ausgewertet")        # 60 Minuten: der Kurs ist wieder da
        a = vt.analyse(t.db, laeufe=10)
        self.assertEqual((a["n_ausgewertet"], a["n_luecke"]), (1, 1))

    def test_strg_c_beendet_sauber_und_laesst_offenes_offen(self):
        uhr = vt.SimUhr(START)
        quelle = SkriptQuelle(uhr, lambda m: 100.0)
        aufrufe = []

        def schlafen(s):
            aufrufe.append(1)
            if len(aufrufe) > 4:
                raise KeyboardInterrupt
            uhr.schlafen(s)
        t = vt.Vorwaertstest(self.dir, symbole=("X/EUR",), quelle=quelle, treiber=lambda p: antwort(), uhr=uhr, schlafen=schlafen, einst={},
                             parallel=False, schritt_s=30, ausgabe=lambda s: None, lernen=False, regeln=False,
                             kontext_geber=lambda j: ("O", "", "H"))
        self.addCleanup(t.schliessen)
        z = t.laufen(dauer_min=60)
        self.assertTrue(z["unterbrochen"])
        self.assertEqual((z["vorhersagen"], z["offen"], z["ausgewertet"]), (2, 2, 0))
        self.assertEqual({x["status"] for x in zeilen(t, "SELECT * FROM trades")}, {"offen"})

    def test_ohne_nachlauf_bleibt_offen(self):
        t, *_ = self.neu(lambda m: 100.0, antwort("steigt"), dauer=1, nachlauf=0)
        self.assertEqual({x["status"] for x in zeilen(t, "SELECT * FROM vorhersagen")}, {"offen"})
        self.assertEqual(t.zusammen["offen"], 2)

    def test_nachlauf_wertet_alles_aus(self):
        t, *_ = self.neu(lambda m: 100.0, antwort("steigt"), dauer=1)
        self.assertEqual(t.zusammen["offen"], 0)
        self.assertEqual(t.zusammen["ausgewertet"], 2)

    def test_neuer_lauf_nimmt_zu_spaete_faelle_aus_der_wertung(self):
        t, *_ = self.neu(lambda m: 100.0, antwort("steigt"), dauer=1, nachlauf=0)
        t.schliessen()
        uhr = vt.SimUhr(START + timedelta(hours=5))
        quelle = SkriptQuelle(uhr, lambda m: 100.0)
        t2 = vt.Vorwaertstest(self.dir, symbole=("X/EUR",), quelle=quelle, treiber=lambda p: antwort("seitwaerts"), uhr=uhr, schlafen=uhr.schlafen,
                              einst={}, parallel=False, ausgabe=lambda s: None, lernen=False, regeln=False, kontext_geber=lambda j: ("O", "", "H"))
        self.addCleanup(t2.schliessen)
        t2.laufen(dauer_min=1)
        st = {x["status"] for x in zeilen(t2, "SELECT * FROM vorhersagen WHERE abgabe < ?", (START + timedelta(hours=1)).isoformat())}
        self.assertEqual(st, {"luecke"})
        self.assertEqual({x["status"] for x in zeilen(t2, "SELECT * FROM trades WHERE einstieg_zeit < ?", (START + timedelta(hours=1)).isoformat())},
                         {"luecke"})

    def test_parallel_mit_hilfsfaeden_und_echter_uhr(self):
        uhr = vt.jetzt_utc
        quelle = SkriptQuelle(uhr, lambda m: 100.0)
        quelle.uhr = uhr

        def treiber(prompt):
            import time
            time.sleep(0.2)
            return antwort("steigt")
        t = vt.Vorwaertstest(self.dir, symbole=("A/EUR", "B/EUR"), quelle=quelle, treiber=treiber, uhr=uhr, einst={}, parallel=True, schritt_s=0.05,
                             tick_s=0.3, ausgabe=lambda s: None, lernen=False, regeln=False, kontext_geber=lambda j: ("O", "", "H"))
        self.addCleanup(t.schliessen)
        z = t.laufen(dauer_min=0.02, nachlauf_min=0)
        self.assertEqual(z["vorhersagen"], 4)
        self.assertEqual({x["symbol"] for x in zeilen(t, "SELECT * FROM vorhersagen")}, {"A/EUR", "B/EUR"})


# ----------------------------------------------------------------------------- Feste Sperren: Kostensperre und Trendbremse

def ab(minuten_zurueck, richtung="faellt", eingetreten="steigt", aend=0.3):
    """Eine ausgewertete Vorhersage für die Trendbremse (Auswertung `minuten_zurueck` Minuten vor T0)."""
    return {"richtung": richtung, "eingetreten": eingetreten, "aenderung_pct": aend, "ablauf": START - timedelta(minutes=minuten_zurueck)}


class SperrenRechnungTest(unittest.TestCase):
    def test_mindest_ziel(self):
        # 2 x (2 x 0,40 % + 0,05 % Spread) = 1,70 %
        self.assertAlmostEqual(vt.mindest_ziel_prozent(0.4, 0.05, 2.0), 1.70)
        self.assertEqual(vt.mindest_ziel_prozent(0.4, 0.05, 0.0), 0.0)                 # Faktor 0 = aus
        self.assertAlmostEqual(vt.spread_prozent(99.99, 100.01), 0.02, places=6)

    def test_grenzen_lassen_die_mindest_zielweite_zu(self):
        self.assertAlmostEqual(vt.abstand_grenzen(15, 0.001)[1], 0.5)                 # ohne Kostensperre wie bisher
        self.assertAlmostEqual(vt.abstand_grenzen(15, 0.001, 1.7)[1], 1.7 * 1.25)
        self.assertEqual(vt.abstand_grenzen(15, 0.001, 9.0)[1], vt.ABSTAND_MAX_PCT[15])

    def test_trendbremse_serie(self):
        serie = [ab(1), ab(6), ab(11)]                                                  # neueste zuerst: dreimal faellt falsch, Kurs +0,3 %
        bis = vt.trendbremse_bis(serie, "faellt", START, 3, 30, 0.15)
        self.assertEqual(bis, START - timedelta(minutes=1) + timedelta(minutes=30))
        self.assertIsNone(vt.trendbremse_bis(serie, "steigt", START, 3, 30, 0.15))     # die andere Richtung bleibt frei
        self.assertIsNone(vt.trendbremse_bis(serie[:2], "faellt", START, 3, 30, 0.15))  # erst ab der dritten
        self.assertIsNone(vt.trendbremse_bis(serie, "faellt", START, 0, 30, 0.15))     # aus

    def test_trendbremse_ende_nach_30_minuten(self):
        serie = [ab(1), ab(6), ab(11)]
        self.assertIsNotNone(vt.trendbremse_bis(serie, "faellt", START + timedelta(minutes=28), 3, 30, 0.15))
        self.assertIsNone(vt.trendbremse_bis(serie, "faellt", START + timedelta(minutes=29), 3, 30, 0.15))   # 30 Minuten nach der Auswertung

    def test_trendbremse_braucht_klare_gegenbewegung_in_folge(self):
        self.assertIsNone(vt.trendbremse_bis([ab(1, aend=0.10), ab(6), ab(11)], "faellt", START, 3, 30, 0.15))           # nicht klar
        self.assertIsNone(vt.trendbremse_bis([ab(1, eingetreten="seitwaerts", aend=0.0), ab(6), ab(11)], "faellt", START, 3, 30, 0.15))
        self.assertIsNone(vt.trendbremse_bis([ab(1), ab(6, "steigt", "steigt", 0.3), ab(11)], "faellt", START, 3, 30, 0.15))   # Treffer/andere Richtung dazwischen
        # steigt gegen fallenden Kurs ebenso
        s = [ab(1, "steigt", "faellt", -0.3), ab(6, "steigt", "faellt", -0.4), ab(11, "steigt", "faellt", -0.2)]
        self.assertIsNotNone(vt.trendbremse_bis(s, "steigt", START, 3, 30, 0.15))

    def test_vorlaeufige_lektionen_bestaetigen_oder_streichen(self):
        l = {"regel": "Sage seitwaerts bei engem Ziel.", "begruendung": "x", "belege": 2}
        neu, weg = vt.lektionen_markieren([l], [], 4)
        self.assertEqual((neu, weg), ([{**l, "vorlaeufig": True}], []))
        alt = neu
        bestaetigt, weg = vt.lektionen_markieren([{**l, "belege": 3}], alt, 4)                  # mehr Belege: bleibt, noch vorläufig
        self.assertEqual((len(bestaetigt), weg, bestaetigt[0]["vorlaeufig"]), (1, [], True))
        gesichert, weg = vt.lektionen_markieren([{**l, "belege": 4}], alt, 4)                   # ab 4 Belegen gesichert
        self.assertEqual((gesichert[0]["vorlaeufig"], weg), (False, []))
        behalten, weg = vt.lektionen_markieren([l], alt, 4)                                      # unverändert: nicht bestätigt, entfällt
        self.assertEqual((behalten, len(weg)), ([], 1))

    def test_lektionen_unter_mindestzahl_werden_aussortiert(self):
        text = json.dumps({"lektionen": [{"regel": "Setze das Ziel weiter weg.", "begruendung": "weil", "belege": 1},
                                         {"regel": "Sage seitwaerts bei engem Ziel.", "begruendung": "weil", "belege": 2}], "veraltet": ""})
        e = vt.pruefe_lektionen(text, 2)
        self.assertEqual([l["belege"] for l in e["lektionen"]], [2])
        self.assertEqual(e["zu_wenig_belege"], 1)
        self.assertEqual(len(vt.pruefe_lektionen(text)["lektionen"]), 2)                       # ohne Mindestzahl wie bisher

    def test_prompt_nennt_kosten_und_mindest_zielweite(self):
        p = vt.prompt_vorhersage("BTC/EUR", "KONTEXT", vt.STANDARD_SCHWELLEN, 0.4, {15: (0.05, 2.2), 60: (0.05, 3.0)}, 0.03, "", 0.05, 2.0)
        for stueck in ("FESTE KOSTENSPERRE", "0,80 %", "Spread zwischen Bid und Ask von 0,05 %", "zusammen etwa 0,85 %", "mindestens 2,0 x Kosten",
                       "mindestens 1,70 % vom Einstiegskurs", "Ein Ziel, das näher liegt als die Kosten, ist selbst bei Treffer ein Verlust"):
            self.assertIn(stueck, p)
        self.assertNotIn("kein Trade möglich", p)
        p2 = vt.prompt_vorhersage("BTC/EUR", "KONTEXT", vt.STANDARD_SCHWELLEN, 0.4, {15: (0.05, 1.0), 60: (0.05, 3.0)}, 0.03, "", 0.05, 2.0)
        self.assertIn("Bei m15 liegt diese Mindest-Zielweite über dem erlaubten Abstand", p2)
        p3 = vt.prompt_vorhersage("BTC/EUR", "KONTEXT", vt.STANDARD_SCHWELLEN, 0.4, GRENZEN, 0.03, "", 0.05, 0.0, "FESTE REGEL DES SYSTEMS (aktuell)\n- Trendbremse: X")
        self.assertNotIn("FESTE KOSTENSPERRE", p3)
        self.assertIn("- Trendbremse: X", p3)


class KostensperreLaufTest(MitOrdner):
    def test_zu_nahes_ziel_wird_gesperrt_und_als_schatten_weitergerechnet(self):
        # Ziel 0,3 % < 2 x (0,80 % + 0,02 % Spread): nur Richtung, kein echter Trade; Schatten-Trade läuft bis zum Ziel
        t, *_ = self.neu(lambda m: 100.0 if m < 7 else 100.4, antwort("steigt"), kosten_faktor=2.0)
        trades = zeilen(t, "SELECT * FROM trades")
        self.assertEqual(len(trades), 2)
        for z in trades:
            self.assertEqual((z["gesperrt"], z["status"]), ("ks", "ziel"))
            self.assertIn("gesperrt: Ziel kleiner als Kosten", z["notiz"])
            self.assertIn("1,64 %", z["notiz"])
        self.assertEqual([x["treffer"] for x in zeilen(t, "SELECT * FROM vorhersagen")], [1, 1])      # Richtung wird trotzdem gewertet
        a = vt.analyse(t.db, laeufe=5)
        self.assertEqual(sum(a["horizonte"][h]["trades"]["echt"]["n"] for h in vt.HORIZONTE), 0)
        self.assertEqual(a["gesperrt"]["ks"]["n"], 2)
        self.assertEqual(a["gesperrt"]["ks"]["ziel"], 2)
        self.assertLess(a["gesperrt"]["ks"]["nach_1x"], 0)                                              # auch bei Treffer ein Verlust
        text = vt.bericht_bauen(t.db, a, None, vt.technik_pruefen(t.db)).text()
        self.assertIn("Gesperrte Trades (nur beobachtet, Schatten-Rechnung)", text)
        self.assertIn("ks Ziel kleiner als Kosten", text)

    def test_weites_ziel_wird_gehandelt(self):
        t, *_ = self.neu(lambda m: 100.0 if m < 7 else 102.3, antwort("steigt", ziel=102.0, stop=99.0), kosten_faktor=2.0)
        trades = zeilen(t, "SELECT * FROM trades")
        self.assertEqual([(z["gesperrt"], z["status"], z["notiz"]) for z in trades], [("", "ziel", None)] * 2)
        self.assertEqual(vt.analyse(t.db, laeufe=5)["gesperrt"], {})

    def test_faktor_null_schaltet_ab_und_prompt_nennt_die_sperre(self):
        t, _, _, prompts = self.neu(lambda m: 100.0, antwort("steigt"), kosten_faktor=0.0)
        self.assertEqual(zeilen(t, "SELECT COUNT(*) n FROM trades WHERE gesperrt != ''")[0]["n"], 0)
        self.assertNotIn("FESTE KOSTENSPERRE", prompts[0])
        self.tearDown(), self.setUp()
        t, _, _, prompts = self.neu(lambda m: 100.0, antwort("seitwaerts"), kosten_faktor=2.0)
        self.assertIn("FESTE KOSTENSPERRE", prompts[0])
        self.assertIn("mindestens 1,64 % vom Einstiegskurs", prompts[0])                               # 2 x (0,80 % + 0,02 % Spread)


class TrendbremseLaufTest(MitOrdner):
    # Kurs steigt in 25 Minuten um 0,31 % (15-Minuten-Änderung 0,19 %, also klar); die KI sagt immer "faellt" (Ziel ca. 1,7 % weg, Stop darüber)
    @staticmethod
    def preis(m):
        return 100.0 + 0.0125 * min(m, 25)

    def test_dritter_fehlschlag_sperrt_die_richtung_fuer_das_symbol(self):
        meldungen = []
        t, *_ = self.neu(self.preis, antwort("faellt", ziel=98.3, stop=101.0), dauer=40, kosten_faktor=2.0, trend_serie=3, ausgabe=meldungen.append)
        trades = zeilen(t, "SELECT * FROM trades ORDER BY id")
        tb = [z for z in trades if "tb" in z["gesperrt"].split(",")]
        self.assertTrue(tb)
        self.assertEqual([z for z in trades if z["gesperrt"] and "tb" not in z["gesperrt"].split(",")], [])      # kein Zielabstand-Problem hier
        # vor der dritten falschen Vorhersage (Auswertung nach 25 Minuten) wird nichts gesperrt
        erste = min(z["einstieg_zeit"] for z in tb)
        self.assertGreaterEqual(erste, (START + timedelta(minutes=25)).isoformat())
        self.assertTrue(all(z["gesperrt"] == "" for z in trades if z["einstieg_zeit"] < (START + timedelta(minutes=25)).isoformat()))
        self.assertTrue(all(z["status"] in vt.ABGERECHNET and "Trendbremse" in z["notiz"] for z in tb))        # Schatten-Trade wird abgerechnet
        self.assertTrue(any("TRENDBREMSE" in m for m in meldungen))
        self.assertTrue(any("GESPERRT: tb (Trendbremse)" in m for m in meldungen))
        a = vt.analyse(t.db, laeufe=5)
        self.assertEqual(a["gesperrt"]["tb"]["n"], len(tb))
        self.assertIn("tb Trendbremse", vt.bericht_bauen(t.db, a, None, vt.technik_pruefen(t.db)).text())
        # Die Sperre endet von selbst und gilt nur für die Richtung
        spaet = vt.zu_utc(t.uhr()) + timedelta(hours=3)
        self.assertIsNone(t._trendbremse("X/EUR", "faellt", spaet))
        self.assertIsNone(t._trendbremse("X/EUR", "steigt", START + timedelta(minutes=30)))

    def test_andere_symbole_und_ausgeschaltet(self):
        t, *_ = self.neu(self.preis, antwort("faellt", ziel=98.3, stop=101.0), dauer=40, kosten_faktor=2.0, trend_serie=0)
        self.assertEqual(zeilen(t, "SELECT COUNT(*) n FROM trades WHERE gesperrt != ''")[0]["n"], 0)
        self.assertIsNone(t._trendbremse("X/EUR", "faellt", START + timedelta(minutes=30)))


# ----------------------------------------------------------------------------- Auswertung, Alarm, Selbsttest

def sim(tmp, falsch=False, seed=7, **opt):
    t, quelle, uhr = vt.sim_lauf(Path(tmp), lambda q, u: vt.hellseher(q, u, vt.STANDARD_SCHWELLEN, falsch), seed=seed, **opt)
    return t


class AuswertungTest(MitOrdner):
    def test_selbsttest_besteht(self):
        ok, zeilen_ = vt.selbsttest()
        self.assertTrue(ok, zeilen_)
        self.assertEqual(len(zeilen_), 2)
        self.assertIn("Hellseher", zeilen_[0])
        self.assertIn("Immer falsch", zeilen_[1])

    def test_hellseher_ergibt_100_prozent_ohne_alarm(self):
        t = sim(self.dir)
        self.addCleanup(t.schliessen)
        a = vt.analyse(t.db, laeufe=200)
        alle = a["horizonte"]["alle"]
        self.assertEqual(alle["reg"]["quote"], 1.0)
        self.assertEqual(alle["richt"]["quote"], 1.0)
        self.assertEqual(a["alarme"], [])
        self.assertEqual(a["horizonte"][15]["reg"]["n"] + a["horizonte"][60]["reg"]["n"], alle["reg"]["n"])
        self.assertGreater(alle["zufall"]["ki_besser"], 0.99)             # besser als fast jede Zufallsrichtung
        self.assertLess(alle["zufall"]["mittel"], 0.7)

    def test_immer_falsch_loest_alarm_aus(self):
        t = sim(self.dir, falsch=True)
        self.addCleanup(t.schliessen)
        a = vt.analyse(t.db, laeufe=200)
        self.assertEqual(a["horizonte"]["alle"]["reg"]["quote"], 0.0)
        self.assertEqual(a["horizonte"]["alle"]["richt"]["quote"], 0.0)
        self.assertTrue(a["alarme"])
        texte = " ".join(x[1] for x in a["alarme"])
        self.assertIn("unter 50 %", texte)
        self.assertIn("nahe 0 %", texte)
        b = vt.bericht_bauen(t.db, a, None, vt.technik_pruefen(t.db))
        self.assertIn("FEHLERVERDACHT: Richtung vertauscht?", b.text())
        self.assertIn("Vorzeichen", b.text())
        self.assertIn("Bid/Ask", b.text())
        self.assertIn("Zeit", b.text())

    def test_bericht_ohne_alarm(self):
        t = sim(self.dir)
        self.addCleanup(t.schliessen)
        a = vt.analyse(t.db, laeufe=100)
        b = vt.bericht_bauen(t.db, a, (True, ["Hellseher ok"]), vt.technik_pruefen(t.db))
        text, md = b.text(), b.markdown()
        self.assertNotIn("FEHLERVERDACHT", text)
        self.assertIn("Kein Fehleralarm", text)
        self.assertIn("Kraken-Taker-Gebühr 0,40 % je Seite (Hin- und Rückweg 0,80 %). Treffer-Schwellen: 15 min 0,05 %, 60 min 0,10 %.", text)
        self.assertIn("Einsatz je Trade 1.000 EUR", text)
        for stueck in ("Richtung: Trefferquote", "Zufallsrichtung", "Immer Long", "Trend der letzten Minuten", "Kalibrierung", "Papier-Trades",
                       "Abgerechnete Trades".lower(), "Ø Ergebnis nach Gebühren", "Grenzen dieser Messung", "Selbsttest der Auswertung bestanden"):
            self.assertIn(stueck.lower(), text.lower())
        self.assertIn("| Horizont | Verfahren |", md)
        self.assertIn("## Papier-Trades", md)

    def test_kurzfazit(self):
        t = sim(self.dir)
        self.addCleanup(t.schliessen)
        a = vt.analyse(t.db, laeufe=100)
        text = "\n".join(vt.kurzfazit(a))
        self.assertIn("besser als 95 % der Zufallsläufe", text)             # der Hellseher
        self.assertIn("Spielgeld-Ergebnis", text)
        kurz = vt.analyse(t.db, seit=(START + timedelta(hours=2, minutes=45)).isoformat(), laeufe=10)
        self.assertIn("zu wenige für eine Aussage", "\n".join(vt.kurzfazit(kurz)))
        self.assertIn("## Kurzfazit", vt.bericht_bauen(t.db, a, None, vt.technik_pruefen(t.db)).markdown())

    def test_zahlen_der_trades_stimmen_mit_den_zeilen(self):
        t = sim(self.dir)
        self.addCleanup(t.schliessen)
        a = vt.analyse(t.db, laeufe=10)
        for h in vt.HORIZONTE:
            e = a["horizonte"][h]["trades"]["echt"]
            rows = zeilen(t, "SELECT * FROM trades WHERE horizont_min=? AND status IN ('ziel','stop','ablauf')", h)
            self.assertEqual(e["n"], len(rows))
            self.assertAlmostEqual(e["nach_1x"], sum(r["nach_1x"] for r in rows) / len(rows), places=12)
            self.assertAlmostEqual(e["summe_nach_hebel_eur"], sum(r["gewinn"] for r in rows), places=9)
            self.assertEqual(e["ziel"] + e["stop"] + e["ablauf"], e["n"])

    def test_immer_long_und_fortsetzung(self):
        t = sim(self.dir)
        self.addCleanup(t.schliessen)
        a = vt.analyse(t.db, laeufe=10)
        s = a["horizonte"][15]
        v = zeilen(t, "SELECT * FROM vorhersagen WHERE horizont_min=15 AND status='ausgewertet'")
        self.assertEqual(s["immer_long"]["k"], sum(x["eingetreten"] == "steigt" for x in v))
        fort = sum(vt.richtung_von_aenderung(x["rueck_pct"], x["schwelle_pct"]) == x["eingetreten"] for x in v if x["rueck_pct"] is not None)
        self.assertEqual(s["fortsetzung"]["k"], fort)
        self.assertEqual(sum(sum(z.values()) for z in s["matrix"].values()), len(v))
        # Hellseher: nur die Diagonale der Matrix ist besetzt
        for r in vt.RICHTUNGEN:
            for e in vt.RICHTUNGEN:
                if r != e:
                    self.assertEqual(s["matrix"][r][e], 0)

    def test_vergleich_halten_hat_die_vier_verfahren(self):
        t = sim(self.dir)
        self.addCleanup(t.schliessen)
        a = vt.analyse(t.db, laeufe=50)
        namen = [z["name"] for z in a["horizonte"][60]["vergleich"]]
        self.assertEqual(len(namen), 4)
        self.assertTrue(any("Zufall" in n for n in namen) and any("Immer Long" in n for n in namen) and any("Trend" in n for n in namen))
        ki_zeile = a["horizonte"][60]["vergleich"][0]
        self.assertGreater(ki_zeile["vor"], 0)                              # der Hellseher gewinnt vor Gebühren
        zufall = a["horizonte"][60]["vergleich"][3]
        self.assertLessEqual(zufall["lo"], zufall["nach"])
        self.assertLessEqual(zufall["nach"], zufall["hi"])
        self.assertLess(zufall["nach"], ki_zeile["nach"])                    # Zufall schlechter als der Hellseher

    def test_kalibrierung_baender(self):
        t = sim(self.dir)
        self.addCleanup(t.schliessen)
        k = vt.analyse(t.db, laeufe=1)["horizonte"][15]["kalibrierung"]
        self.assertEqual(len(k), 1)                                         # alle Antworten haben p = 0,9
        self.assertEqual((k[0]["von"], k[0]["bis"]), (0.8, 1.0))
        self.assertAlmostEqual(k[0]["mittel_p"], 0.9)
        self.assertEqual(k[0]["quote"], 1.0)

    def test_zufallslaeufe(self):
        rng = random.Random(1)
        # Alle Richtungen gleich: jeder Lauf trifft genau so oft wie die KI
        q = vt.zufall_trefferquoten([(["steigt"] * 10, ["steigt"] * 6 + ["faellt"] * 4)], 50, rng)
        self.assertEqual(set(q), {0.6})
        # Ausgeglichen gemischt: Mittel nahe dem Erwarteten (50 % bei 5 steigt / 5 faellt, Ergebnis ebenso)
        q = vt.zufall_trefferquoten([(["steigt"] * 50 + ["faellt"] * 50, ["steigt"] * 50 + ["faellt"] * 50)], 500, rng)
        self.assertAlmostEqual(sum(q) / len(q), 0.5, delta=0.02)
        self.assertEqual(vt.zufall_trefferquoten([], 10, rng), [])

    def test_filter_modell_und_seit(self):
        t = sim(self.dir)
        self.addCleanup(t.schliessen)
        self.assertEqual(vt.analyse(t.db, modell="gibt-es-nicht", laeufe=1)["n_gesamt"], 0)
        spaet = (START + timedelta(hours=2)).isoformat()
        a = vt.analyse(t.db, seit=spaet, laeufe=1)
        self.assertTrue(0 < a["n_gesamt"] < vt.analyse(t.db, laeufe=1)["n_gesamt"])

    def test_leere_datenbank(self):
        db = vt.db_oeffnen(self.dir / "leer.db")
        self.addCleanup(db.close)
        a = vt.analyse(db, laeufe=10)
        self.assertEqual(a["n_gesamt"], 0)
        self.assertIn("Noch keine Vorhersagen", vt.bericht_bauen(db, a, None, vt.technik_pruefen(db)).text())


# ----------------------------------------------------------------------------- Technikprüfung: erkennt eingebaute Fehler

class TechnikTest(MitOrdner):
    def setUp(self):
        super().setUp()
        self.t = sim(self.dir)
        self.addCleanup(self.t.schliessen)
        self.db = self.t.db

    def pruefen(self):
        return vt.technik_pruefen(self.db)

    def test_saubere_daten(self):
        r = self.pruefen()
        self.assertTrue(r["ok"], r["fehler"])
        self.assertEqual(r["fehler"], [])
        self.assertGreater(r["n_trades"], 20)
        self.assertEqual(len(r["zeilen"]), 6)

    def erwarte(self, gruppe, sql, *par):
        self.db.execute(sql, par)
        self.db.commit()
        r = self.pruefen()
        self.assertFalse(r["ok"])
        self.assertTrue(any(f.startswith(gruppe + ":") for f in r["fehler"]), r["fehler"])
        return r

    def test_seite_vertauscht(self):
        self.erwarte("Vorzeichen", "UPDATE trades SET seite=CASE seite WHEN 'long' THEN 'short' ELSE 'long' END WHERE id=(SELECT MIN(id) FROM trades)")

    def test_einstieg_zum_falschen_kurs(self):
        # Long mit dem Bid statt dem Ask eingestiegen
        self.erwarte("Bid/Ask", "UPDATE trades SET einstieg=einstieg*0.9995 WHERE id=(SELECT MIN(id) FROM trades WHERE seite='long')")

    def test_ergebnis_mit_falschem_vorzeichen(self):
        self.erwarte("Vorzeichen", "UPDATE trades SET vor_1x=-vor_1x WHERE id=(SELECT MIN(id) FROM trades WHERE status='ablauf')")

    def test_zeitzone_fehlt(self):
        self.erwarte("Zeit", "UPDATE vorhersagen SET faellig=substr(faellig,1,19) WHERE id=(SELECT MIN(id) FROM vorhersagen)")

    def test_auswertung_vor_ablauf(self):
        self.erwarte("Zeit", "UPDATE vorhersagen SET ablauf_zeit=abgabe WHERE id=(SELECT MIN(id) FROM vorhersagen WHERE status='ausgewertet')")

    def test_kurszeit_in_der_zukunft(self):
        # Zeitzonenfehler: die Kurszeit liegt Stunden nach der Abgabe
        self.erwarte("Zeit", "UPDATE vorhersagen SET kurszeit='2026-01-05T15:00:00+00:00' WHERE id=(SELECT MIN(id) FROM vorhersagen)")

    def test_anderes_symbol(self):
        self.erwarte("Symbol", "UPDATE vorhersagen SET ablauf_mitte=ablauf_mitte*50 WHERE id=(SELECT MIN(id) FROM vorhersagen WHERE status='ausgewertet')")

    def test_ki_felder_falsch_herum(self):
        r = self.erwarte("KI-Felder", "UPDATE vorhersagen SET ziel=stop, stop=ziel WHERE id=(SELECT MIN(id) FROM vorhersagen WHERE richtung='steigt')")
        self.assertIn("richtigen Seite", " ".join(r["fehler"]))

    def test_auswertung_falsch_gespeichert(self):
        self.erwarte("Auswertung", "UPDATE vorhersagen SET treffer=1-treffer WHERE id=(SELECT MIN(id) FROM vorhersagen WHERE status='ausgewertet')")

    def test_bid_ask_gekreuzt(self):
        self.erwarte("Bid/Ask", "UPDATE vorhersagen SET bid=ask*1.01 WHERE id=(SELECT MIN(id) FROM vorhersagen)")


# ----------------------------------------------------------------------------- Kursquelle und CLI-Treiber

class QuelleTest(unittest.TestCase):
    def test_quote_aus_kurs(self):
        jetzt = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        q = vt.quote_aus_kurs(Kurs("X", 99.0, 101.0, 100.0, jetzt - timedelta(seconds=30)), jetzt)
        self.assertEqual((q.bid, q.ask, q.mitte, q.kurszeit), (99.0, 101.0, 100.0, jetzt - timedelta(seconds=30)))
        q = vt.quote_aus_kurs(Kurs("X", 99.0, 101.0, 100.0, jetzt, zeit_geschaetzt=True), jetzt)
        self.assertIsNone(q.kurszeit)
        for kurs in (Kurs("X", None, 101.0, 100.0, jetzt), Kurs("X", 101.0, 99.0, 100.0, jetzt), Kurs("X", 0.0, 1.0, 1.0, jetzt)):
            with self.assertRaises(vt.QuoteFehler):
                vt.quote_aus_kurs(kurs, jetzt)

    def test_kraken_quelle_wiederholt_einmal(self):
        class Broker:
            n = 0

            def kurs(self, s):
                self.n += 1
                if self.n == 1:
                    raise RuntimeError("Netz weg")
                return Kurs(s, 1.0, 2.0, 1.5, START)
        b = Broker()
        with mock.patch("kitrader.vorwaertstest.time.sleep"):
            self.assertEqual(vt.KrakenQuelle(b).kurs("X").bid, 1.0)
        self.assertEqual(b.n, 2)

        class Kaputt:
            def kurs(self, s):
                raise RuntimeError("immer kaputt")
        with mock.patch("kitrader.vorwaertstest.time.sleep"), self.assertRaises(RuntimeError):
            vt.KrakenQuelle(Kaputt()).kurs("X")

    def test_sim_quelle_ist_stetig_und_kennt_die_zukunft(self):
        uhr = vt.SimUhr(START)
        q = vt.SimQuelle(uhr, ("A/EUR", "B/EUR"), seed=3)
        self.assertNotEqual(q.mitte("A/EUR", START), q.mitte("B/EUR", START))
        k = q.kurs("A/EUR")
        self.assertAlmostEqual((k.bid + k.ask) / 2, q.mitte("A/EUR", START))
        self.assertLess(k.bid, k.ask)
        kerzen = q.kerzen("A/EUR", "5m", 10)
        self.assertEqual(len(kerzen), 10)
        self.assertTrue(all(a.t < b.t for a, b in zip(kerzen, kerzen[1:])))
        self.assertLessEqual(kerzen[-1].t, START)


class KontextStandardTest(MitOrdner):
    class FakeOsiris:
        abrufe = 0
        stand_alter_min = 90
        fehler = False

        def __init__(self, einst, pfad):
            pass

        def lage(self):
            return vt._osiris.Lage(vt.jetzt_utc() - timedelta(minutes=self.stand_alter_min), None, "OSIRIS-Fake", {"x": 1})

        def abrufen(self):
            type(self).abrufe += 1
            if self.fehler:
                raise vt._osiris.OsirisFehler("kein Netz")

        def beenden(self):
            pass

    def holen(self, automatisch=True, alter=90, fehler=False, welt=None):
        F = type("F", (self.FakeOsiris,), {"abrufe": 0, "stand_alter_min": alter, "fehler": fehler})
        einst = vt._einst.mit_standard({"osiris": {"automatisch": automatisch}})
        with mock.patch.object(vt._osiris, "Osiris", F), mock.patch.object(ki, "historie_text", return_value="HISTORIE-Fake"):
            if welt is not None:
                with mock.patch.object(ki, "_welt_abschnitte", welt, create=True):
                    return vt.kontext_standard(einst, self.dir, 30, vt.jetzt_utc()), F
            return vt.kontext_standard(einst, self.dir, 30, vt.jetzt_utc()), F

    def test_alte_lage_wird_neu_geholt(self):
        (text, hinweis, historie), F = self.holen(alter=90)
        self.assertEqual((F.abrufe, hinweis, historie), (1, "", "HISTORIE-Fake"))
        self.assertIn("OSIRIS-Fake", text)

    def test_junge_lage_nicht(self):
        (_, hinweis, _), F = self.holen(alter=10)
        self.assertEqual(F.abrufe, 0)

    def test_nicht_wenn_automatisch_aus(self):
        (_, _, _), F = self.holen(automatisch=False, alter=999)
        self.assertEqual(F.abrufe, 0)

    def test_osiris_fehler_wird_zum_hinweis(self):
        (text, hinweis, _), F = self.holen(alter=90, fehler=True)
        self.assertIn("kein Netz", hinweis)
        self.assertIn("OSIRIS-Fake", text)

    def test_welt_abschnitte_werden_angehaengt(self):
        (text, _, _), _ = self.holen(welt=lambda einst, ordner, jetzt: [("Nachrichten-Radar", "Meldung A"), ("Welt-Rotation", "Region B")])
        self.assertIn("### Nachrichten-Radar\nMeldung A", text)
        self.assertIn("### Welt-Rotation\nRegion B", text)

        def kaputt(*a):
            raise RuntimeError("Datenbank kaputt")
        (text, _, _), _ = self.holen(welt=kaputt)
        self.assertIn("nicht verfügbar (Datenbank kaputt)", text)


class CliTreiberTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def fake_cli(self, koerper: str) -> str:
        pfad = self.dir / "claude"
        pfad.write_text("#!/usr/bin/env python3\nimport sys, json, time\n" + textwrap.dedent(koerper))
        pfad.chmod(pfad.stat().st_mode | stat.S_IEXEC)
        return str(pfad)

    def test_modell_und_messwerte(self):
        cli = self.fake_cli("""
            a = sys.argv[1:]
            assert a[0] == "-p" and a[2:] == ["--model", "sonnet-test", "--output-format", "json", "--tools", ""], a
            print(json.dumps({"is_error": False, "result": "ANTWORT " + a[1], "total_cost_usd": 0.0123,
                              "usage": {"input_tokens": 3, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 50, "output_tokens": 77}}))
        """)
        text, m = vt.treiber_claude_cli("sonnet-test", 20, cli)("FRAGE")
        self.assertEqual(text, "ANTWORT FRAGE")
        self.assertEqual((m["tokens_ein"], m["tokens_cache_lesen"], m["tokens_cache_neu"], m["tokens_aus"], m["kosten_usd"]), (3, 100, 50, 77, 0.0123))
        self.assertGreaterEqual(m["dauer_s"], 0)

    def test_fehlerfaelle(self):
        with self.assertRaises(ki.KiFehler) as cm:
            vt.treiber_claude_cli("sonnet", 5, str(self.dir / "gibt-es-nicht"))("x")
        self.assertIn("nicht gefunden", str(cm.exception))
        cli = self.fake_cli("print(json.dumps({'is_error': True, 'result': 'Limit erreicht'})); sys.exit(1)")
        with self.assertRaises(ki.KiFehler) as cm:
            vt.treiber_claude_cli("sonnet", 5, cli)("x")
        self.assertIn("Limit erreicht", str(cm.exception))
        cli = self.fake_cli("print('kein json')")
        with self.assertRaises(ki.KiFehler):
            vt.treiber_claude_cli("sonnet", 5, cli)("x")
        cli = self.fake_cli("time.sleep(20)")
        with self.assertRaises(ki.KiFehler) as cm:
            vt.treiber_claude_cli("sonnet", 1, cli)("x")
        self.assertIn("nicht innerhalb", str(cm.exception))
        with self.assertRaises(ki.KiFehler):
            vt.treiber_claude_cli("sonnet", 5, "claude")("x" * (ki.MAX_PROMPT_BYTES + 1))

    def test_laufende_prozesse_beenden(self):
        vt.laufende_ki_beenden()                  # ohne laufende Prozesse: kein Fehler
        self.assertEqual(len(vt._PROZESSE), 0)


# ----------------------------------------------------------------------------- Kommandozeile

class KommandozeileTest(MitOrdner):
    def main(self, *argv):
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer), contextlib.redirect_stderr(puffer):
            rc = vt.main(["--daten", str(self.dir), *argv])
        return rc, puffer.getvalue()

    def test_selbsttest(self):
        rc, aus = self.main("selbsttest")
        self.assertEqual(rc, 0)
        self.assertIn("Selbsttest: BESTANDEN", aus)

    def test_auswerten_ohne_datenbank(self):
        rc, aus = self.main("auswerten")
        self.assertEqual(rc, 1)
        self.assertIn("Keine Datenbank", aus)

    def test_auswerten_schreibt_bericht(self):
        t = sim(self.dir, falsch=True)
        t.schliessen()
        rc, aus = self.main("auswerten", "--zufallslaeufe", "50")
        self.assertEqual(rc, 0)
        self.assertIn("FEHLERVERDACHT: Richtung vertauscht?", aus)
        self.assertIn("Selbsttest der Auswertung bestanden", aus)
        md = (self.dir / vt.BERICHT_NAME).read_text(encoding="utf-8")
        self.assertIn("# Vorwärtstest der KI", md)
        self.assertIn("FEHLERVERDACHT", md)

    def test_pruefen(self):
        t = sim(self.dir)
        t.schliessen()
        rc, aus = self.main("pruefen")
        self.assertEqual(rc, 0)
        self.assertIn("keine Fehler gefunden", aus)
        db = vt.db_oeffnen(self.dir / vt.DB_NAME)
        db.execute("UPDATE trades SET seite='short' WHERE id=(SELECT MIN(id) FROM trades WHERE seite='long')")
        db.commit()
        db.close()
        rc, aus = self.main("pruefen")
        self.assertEqual(rc, 1)
        self.assertIn("FEHLER GEFUNDEN", aus)

    def test_standardwerte_des_laufs(self):
        a = vt._parser().parse_args(["laufen"])
        self.assertEqual((a.takt, a.dauer, a.modell, a.gebuehr, a.symbole), (5, 60, "sonnet", 0.4, ["BTC/EUR", "ETH/EUR"]))
        self.assertEqual((a.lernen, a.regeln, a.lern_abstand, a.lern_neu, a.kontrollrunde), ("an", "an", 15, 4, 4))
        self.assertEqual((a.min_n_anpassung, a.min_belege_lektion, a.kosten_faktor, a.trend_serie, a.trend_sperre_min), (4, 2, 2.0, 3, 30))
        a = vt._parser().parse_args(["laufen", "--gebuehr", "0,8", "--modell", "opus", "--nachlauf", "0"])
        self.assertEqual((a.gebuehr, a.modell, a.nachlauf), (0.8, "opus", 0.0))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            vt._parser().parse_args(["laufen", "--gebuehr", "9"])

    def test_laufen_prueft_argumente_und_selbsttest(self):
        rc, aus = self.main("laufen", "--takt", "0")
        self.assertEqual(rc, 2)
        with mock.patch.object(vt, "selbsttest", return_value=(False, ["kaputt"])):
            rc, aus = self.main("laufen")
        self.assertEqual(rc, 1)
        self.assertIn("Selbsttest der Auswertung ist fehlgeschlagen", aus)

    def test_sperre_gegen_doppelten_lauf(self):
        try:
            import fcntl  # noqa: F401
        except ImportError:
            self.skipTest("kein fcntl")
        erste = vt._sperre_holen(self.dir)
        self.addCleanup(erste.close)
        with self.assertRaises(vt.VorwaertsFehler) as cm:
            vt._sperre_holen(self.dir)
        self.assertIn("läuft schon", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
