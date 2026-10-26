"""Vorwärtstest: Lernen während des Laufs - Lektionen (Nachbesprechung), feste Anpassungen ohne KI, Fehlerprüfung zuerst,
Wirkung je Lektionen-Version mit Kontrollrunden. Alles offline mit Fake-Treibern auf nachgebildeten Kursen."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from kitrader import ki
from kitrader import vorwaertstest as vt

UTC = timezone.utc
T0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)

LEKTION = {"regel": "Setze den Stop weiter weg als 0,3 %.", "begruendung": "Stops wurden oft vor der Wende ausgelöst", "belege": 5}


# ----------------------------------------------------------------------------- Hilfen

def trade(i=0, h=15, p=0.6, erw=0.5, kosten=0.8, nach=-0.01, gewinn=-10.0, gesperrt=""):
    return {"horizont": h, "p": p, "erwartung": erw, "kosten": kosten, "nach_1x": nach, "gewinn": gewinn, "gesperrt": gesperrt,
            "ausstieg": T0 + timedelta(minutes=i)}


def viele(n, start=0, **kw):
    return [trade(start + i, **kw) for i in range(n)]


def fall(**kw):
    """Ein ausgewerteter Fall (Vorhersage plus Trade) wie aus faelle_laden."""
    f = {"id": 1, "symbol": "BTC/EUR", "horizont_min": 15, "abgabe": T0.isoformat(), "status": "ausgewertet", "richtung": "steigt",
         "eingetreten": "steigt", "treffer": 1, "wahrscheinlichkeit": 0.7, "rueck_pct": None, "schwelle_pct": 0.05, "aenderung_pct": 0.2,
         "begruendung": json.dumps({"kurs": "Aufwärtstrend", "historie": "kein Beitrag", "weltlage": "ruhig"}),
         "t_id": 1, "t_status": "ablauf", "t_seite": "long", "t_hebel": 1, "t_gebuehr_pct": 0.4, "t_einstieg": 100.0, "t_ziel": 101.0,
         "t_stop": 99.5, "t_vor_1x": 0.002, "t_nach_1x": -0.006, "t_gewinn": -6.0, "t_best": 0.004, "t_schlecht": -0.001, "t_gesperrt": ""}
    f.update(kw)
    return f


class Lehrer:
    """Fake-Treiber: Vorhersagen vom Hellseher oder Immer-falsch, Nachbesprechungen mit festen Lektionen."""

    def __init__(self, quelle, uhr, falsch=False, lektionen=(LEKTION,), debrief=None):
        self.inner = vt.hellseher(quelle, uhr, vt.STANDARD_SCHWELLEN, falsch)
        self.lektionen, self.debrief_antwort = list(lektionen), debrief
        self.prompts, self.debriefs = [], []

    def __call__(self, prompt):
        if "Du bist der Trainer" in prompt:
            self.debriefs.append(prompt)
            if self.debrief_antwort is not None:
                return self.debrief_antwort
            return json.dumps({"lektionen": self.lektionen, "veraltet": "nichts"})
        self.prompts.append(prompt)
        return self.inner(prompt)


class MitOrdner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def sim(self, falsch=False, dauer=120, **opt):
        self.meldungen = []
        self.lehrer = None
        lektionen, debrief = opt.pop("lektionen", (LEKTION,)), opt.pop("debrief", None)

        def fabrik(q, u):
            self.lehrer = Lehrer(q, u, falsch, lektionen, debrief)
            return self.lehrer
        standard = dict(lernen=True, regeln=True, lern_abstand=20, lern_neu=6, kontrollrunde=3, ausgabe=self.meldungen.append)
        standard.update(opt)
        t, _, _ = vt.sim_lauf(self.dir, fabrik, dauer_min=dauer, **standard)
        self.addCleanup(t.schliessen)
        return t

    def zeilen(self, t, sql, *par):
        return [dict(z) for z in t.db.execute(sql, par)]


# ----------------------------------------------------------------------------- Feste Anpassungen (rein gerechnet)

class RegelnTest(unittest.TestCase):
    def test_tor(self):
        r = vt.Regeln()
        self.assertEqual(r.tor(15, 0.4, -1.0, 0.8), "")                           # alles aus: nichts gesperrt
        r = vt.Regeln(min_p=0.6, k_kosten=1.0, pausiert=[60])
        self.assertEqual(r.tor(15, 0.7, 1.0, 0.8), "")
        self.assertEqual(r.tor(15, 0.55, 1.0, 0.8), "p")
        self.assertEqual(r.tor(15, 0.7, 0.5, 0.8), "k")                           # Erwartung 0,5 % < 1 x 0,8 % Kosten
        self.assertEqual(r.tor(60, 0.7, 1.0, 0.8), "pause")
        self.assertEqual(r.tor(60, 0.5, 0.1, 0.8), "pause,p,k")

    def test_json_und_beschreibung(self):
        r = vt.Regeln(0.6, 1.5, 2, [15], {"min_p": "2026-01-05T10:00:00+00:00"})
        r2 = vt.Regeln.aus_json(r.zu_json())
        self.assertEqual((r2.min_p, r2.k_kosten, r2.hebel_max, r2.pausiert, r2.geaendert), (0.6, 1.5, 2, [15], r.geaendert))
        self.assertEqual(len(r.beschreibung()), 4)
        self.assertEqual(vt.Regeln().beschreibung(), [])
        self.assertEqual(vt.Regeln.aus_json(None).hebel_max, 3)

    def test_zu_wenig_faelle_aendern_nichts(self):
        r = vt.Regeln()
        self.assertEqual(vt.regeln_anpassen(r, viele(3, p=0.5, nach=-0.02), T0), [])
        self.assertEqual((r.min_p, r.k_kosten, r.hebel_max, r.pausiert), (0.0, 0.0, 3, []))

    def test_min_wahrscheinlichkeit_anheben(self):
        r = vt.Regeln()
        trades = viele(6, p=0.5, erw=2.0, nach=-0.01)                              # Band 0 bis 0,55 verliert
        log = vt.regeln_anpassen(r, trades, T0 + timedelta(hours=1))
        self.assertEqual(r.min_p, 0.55)
        self.assertEqual([(x[0], x[1], x[2], x[4]) for x in log if x[0] == "min_wahrscheinlichkeit"], [("min_wahrscheinlichkeit", "0.0", "0.55", 6)])
        self.assertIn("min_p", r.geaendert)
        # sofort danach nichts mehr: die alten Fälle zählen nicht noch einmal (frische Belege)
        self.assertEqual(vt.regeln_anpassen(r, trades, T0 + timedelta(hours=2)), [])
        self.assertEqual(r.min_p, 0.55)

    def test_hohe_wahrscheinlichkeit_hebt_nicht_an(self):
        r = vt.Regeln()
        vt.regeln_anpassen(r, viele(8, p=0.9, erw=2.0, nach=0.01, gewinn=10), T0)
        self.assertEqual(r.min_p, 0.0)

    def test_min_wahrscheinlichkeit_zurueck_wenn_gesperrte_besser_waeren(self):
        r = vt.Regeln(min_p=0.55, geaendert={"min_p": T0.isoformat()})
        schatten = viele(6, start=10, p=0.5, erw=2.0, nach=0.01, gewinn=10, gesperrt="p")
        log = vt.regeln_anpassen(r, schatten, T0 + timedelta(hours=1))
        self.assertEqual(r.min_p, 0.0)
        self.assertIn("zurückgenommen", log[0][3])

    def test_min_wahrscheinlichkeit_bleibt_wenn_gesperrte_auch_verlieren(self):
        r = vt.Regeln(min_p=0.55, geaendert={"min_p": T0.isoformat()})
        schatten = viele(6, start=10, p=0.5, erw=2.0, nach=-0.01, gesperrt="p")
        vt.regeln_anpassen(r, schatten, T0 + timedelta(hours=1))
        self.assertEqual(r.min_p, 0.55)

    def test_rücknahme_nur_wenn_erlaubte_nicht_besser_sind(self):
        r = vt.Regeln(min_p=0.55, geaendert={"min_p": T0.isoformat()})
        trades = viele(6, start=10, p=0.5, nach=0.002, gesperrt="p") + viele(6, start=20, p=0.7, nach=0.01)      # die erlaubten sind besser
        vt.regeln_anpassen(r, trades, T0 + timedelta(hours=1))
        self.assertEqual(r.min_p, 0.55)

    def test_nur_frische_faelle_zaehlen(self):
        r = vt.Regeln(geaendert={"min_p": (T0 + timedelta(minutes=30)).isoformat()})
        alt = viele(10, p=0.5, nach=-0.02)                                         # vor der letzten Änderung abgerechnet
        vt.regeln_anpassen(r, alt, T0 + timedelta(hours=1))
        self.assertEqual(r.min_p, 0.0)

    def test_erwartung_gegen_kosten(self):
        r = vt.Regeln()
        # Erwartung 0,2 % < 0,5 x 0,8 % Kosten; verliert im Mittel -> k von 0 auf 0,5
        log = vt.regeln_anpassen(r, viele(6, p=0.9, erw=0.2, nach=-0.01), T0)
        self.assertEqual(r.k_kosten, 0.5)
        self.assertTrue(any(x[0] == "erwartung_gegen_kosten" for x in log))
        # Trades mit Erwartung über der Schwelle werden nicht ausgesperrt
        r2 = vt.Regeln()
        vt.regeln_anpassen(r2, viele(6, p=0.9, erw=1.5, nach=-0.01), T0)
        self.assertEqual(r2.k_kosten, 0.0)

    def test_erwartung_zurueck(self):
        r = vt.Regeln(k_kosten=0.5, geaendert={"k_kosten": T0.isoformat()})
        vt.regeln_anpassen(r, viele(6, start=5, p=0.9, erw=0.2, nach=0.01, gewinn=10, gesperrt="k"), T0 + timedelta(hours=1))
        self.assertEqual(r.k_kosten, 0.0)

    def test_hebel_senken_und_zurueck(self):
        r = vt.Regeln()
        log = vt.regeln_anpassen(r, viele(6, p=0.9, erw=2.0, nach=-0.01, gewinn=-20), T0)
        self.assertEqual(r.hebel_max, 2)
        self.assertTrue(any(x[0] == "hebel_max" and x[1] == "3" and x[2] == "2" for x in log))
        # frische Verluste -> weiter auf 1; nie unter 1
        vt.regeln_anpassen(r, viele(6, start=100, p=0.9, erw=2.0, nach=-0.01, gewinn=-20), T0 + timedelta(hours=3))
        self.assertEqual(r.hebel_max, 1)
        vt.regeln_anpassen(r, viele(6, start=300, p=0.9, erw=2.0, nach=-0.01, gewinn=-20), T0 + timedelta(hours=9))
        self.assertEqual(r.hebel_max, 1)
        # Gewinne mit niedrigem Hebel -> wieder eine Stufe hoch
        vt.regeln_anpassen(r, viele(6, start=600, p=0.9, erw=2.0, nach=0.01, gewinn=10), T0 + timedelta(hours=20))
        self.assertEqual(r.hebel_max, 2)

    def test_hebel_ignoriert_gesperrte(self):
        r = vt.Regeln()
        vt.regeln_anpassen(r, viele(6, p=0.9, erw=2.0, nach=-0.01, gewinn=-20, gesperrt="pause"), T0)
        self.assertEqual(r.hebel_max, 3)

    def test_schlechteren_horizont_pausieren(self):
        r = vt.Regeln()
        trades = viele(6, h=15, nach=-0.012, p=0.9, erw=2.0, gewinn=0) + viele(6, start=10, h=60, nach=-0.002, p=0.9, erw=2.0, gewinn=0)
        log = vt.regeln_anpassen(r, trades, T0)
        self.assertEqual(r.pausiert, [15])
        self.assertTrue(any(x[0] == "horizont_pausiert" and x[2] == "15" for x in log))
        # nie beide: solange einer pausiert ist, wird der andere nicht auch pausiert
        vt.regeln_anpassen(r, viele(6, start=100, h=60, nach=-0.05, p=0.9, erw=2.0, gewinn=0), T0 + timedelta(hours=3))
        self.assertEqual(r.pausiert, [15])

    def test_kein_pausieren_ohne_vergleich_oder_bei_gleichstand(self):
        r = vt.Regeln()
        vt.regeln_anpassen(r, viele(8, h=15, nach=-0.05, p=0.9, erw=2.0, gewinn=0), T0)
        self.assertEqual(r.pausiert, [])                                            # nur ein Horizont hat Daten
        vt.regeln_anpassen(r, viele(6, h=15, nach=-0.01, p=0.9, erw=2.0, gewinn=0) + viele(6, start=10, h=60, nach=-0.01, p=0.9, erw=2.0, gewinn=0), T0)
        self.assertEqual(r.pausiert, [])
        vt.regeln_anpassen(r, viele(6, h=15, nach=0.01, p=0.9, erw=2.0, gewinn=0) + viele(6, start=10, h=60, nach=0.002, p=0.9, erw=2.0, gewinn=0), T0)
        self.assertEqual(r.pausiert, [])                                            # der schlechtere ist im Plus

    def test_pause_aufheben(self):
        r = vt.Regeln(pausiert=[15], geaendert={"pause": T0.isoformat()})
        trades = viele(6, start=5, h=15, nach=0.01, gesperrt="pause", p=0.9, erw=2.0)
        log = vt.regeln_anpassen(r, trades, T0 + timedelta(hours=1))
        self.assertEqual(r.pausiert, [])
        self.assertIn("zurückgenommen", log[0][3])
        r = vt.Regeln(pausiert=[15], geaendert={"pause": T0.isoformat()})
        vt.regeln_anpassen(r, viele(6, start=5, h=15, nach=-0.01, gesperrt="pause", p=0.9, erw=2.0), T0 + timedelta(hours=1))
        self.assertEqual(r.pausiert, [15])


# ----------------------------------------------------------------------------- Lektionen: Prüfung und Prompt

class LektionenTest(unittest.TestCase):
    def antwort(self, lektionen):
        return json.dumps({"lektionen": lektionen, "veraltet": "x"})

    def test_gueltig(self):
        e = vt.pruefe_lektionen(self.antwort([LEKTION, {**LEKTION, "regel": "Handle nur bei Bewegung über 0,8 %."}]))
        self.assertEqual(len(e["lektionen"]), 2)
        self.assertEqual(e["veraltet"], "x")
        self.assertEqual(vt.pruefe_lektionen(self.antwort([]))["lektionen"], [])
        self.assertEqual(vt.pruefe_lektionen('{"lektionen": []}')["veraltet"], "")
        self.assertEqual(len(vt.pruefe_lektionen("```json\n" + self.antwort([LEKTION]) + "\n```")["lektionen"]), 1)

    def test_hoechstens_acht(self):
        self.assertEqual(len(vt.pruefe_lektionen(self.antwort([LEKTION] * 8))["lektionen"]), 8)
        with self.assertRaises(ki.AntwortUngueltig) as cm:
            vt.pruefe_lektionen(self.antwort([LEKTION] * 9))
        self.assertIn("höchstens 8", str(cm.exception))

    def test_ungueltig(self):
        for kaputt in ("kein json", "[]", '{"x": 1}', self.antwort("text"), self.antwort([{**LEKTION, "belege": True}]),
                       self.antwort([{**LEKTION, "belege": -1}]), self.antwort([{**LEKTION, "belege": 1.5}]),
                       self.antwort([{**LEKTION, "regel": "kurz"}]), self.antwort([{**LEKTION, "begruendung": ""}]), self.antwort([5]),
                       json.dumps({"lektionen": [LEKTION], "veraltet": 3})):
            with self.assertRaises(ki.AntwortUngueltig, msg=kaputt):
                vt.pruefe_lektionen(kaputt)

    def test_block_fuer_den_prompt(self):
        self.assertEqual(vt.lektionen_block(0, [], 0, vt.Regeln()), "")
        text = vt.lektionen_block(3, [LEKTION], 40, vt.Regeln(min_p=0.6, hebel_max=2))
        self.assertIn("Version 3, gelernt aus 40", text)
        self.assertIn("Setze den Stop weiter weg", text)
        self.assertIn("FESTE REGELN DES SYSTEMS", text)
        self.assertIn("Mindest-Wahrscheinlichkeit 0,60", text)
        self.assertIn("Hebel höchstens 2", text)
        nur_regeln = vt.lektionen_block(0, [], 0, vt.Regeln(pausiert=[15]))
        self.assertNotIn("LEKTIONEN AUS", nur_regeln)
        self.assertIn("Horizont 15 Minuten pausiert", nur_regeln)

    def test_nachbesprechungs_prompt(self):
        p = vt.prompt_nachbesprechung("STAT-TEXT", "FALL-TEXT", [LEKTION], vt.Regeln(hebel_max=2), 0.4, 33)
        for stueck in ("Du bist der Trainer", "höchstens 8", "STAT-TEXT", "FALL-TEXT", "Setze den Stop weiter weg", "Hebel höchstens 2", "33 ausgewertete",
                       "Unter 2 Belegen keine Lektion", "ersetzt die bisherigen Lektionen", '"lektionen"'):
            self.assertIn(stueck, p)


# ----------------------------------------------------------------------------- Statistik und typische Fehler

class FehlerbilderTest(unittest.TestCase):
    def test_zaehler(self):
        faelle = [
            fall(id=1, t_status="stop", eingetreten="steigt"),                                   # Stop zu eng: Richtung stimmte am Ende
            fall(id=2, t_status="stop", eingetreten="faellt", treffer=0),                        # Stop zu Recht
            fall(id=3, t_status="ablauf", eingetreten="steigt"),                                 # Ziel zu weit: richtig, aber Ziel nicht erreicht
            fall(id=4, t_status="ablauf", eingetreten="faellt", treffer=0, t_vor_1x=-0.01, t_nach_1x=-0.02),   # Richtung falsch
            fall(id=5, t_status="ablauf", eingetreten="seitwaerts", treffer=0, t_vor_1x=0.003, t_nach_1x=-0.005),   # Kosten fressen es
            fall(id=6, t_status="ziel", t_ziel=100.5, eingetreten="steigt"),                     # Ziel 0,5 % < 0,8 % Kosten
        ]
        fb = vt.fehlerbilder(faelle)
        self.assertEqual(fb["n_trades"], 6)
        self.assertEqual(fb["stop_zu_eng"], (1, 2))
        self.assertEqual(fb["ziel_zu_weit"], (1, 3))
        self.assertEqual(fb["richtung_falsch"], (2, 6))                                         # Fall 2 und 4
        self.assertEqual(fb["bewegung_kleiner_kosten"], (5, 6))                                  # vor Gebühren im Plus, danach nicht: Fälle 1, 2, 3, 5, 6
        self.assertEqual(fb["ziel_unter_kosten"], (1, 6))

    def test_trend_und_hebel(self):
        faelle = [
            fall(id=1, rueck_pct=0.3, richtung="steigt", treffer=1),                              # mit dem Trend, Treffer
            fall(id=2, rueck_pct=0.3, richtung="steigt", treffer=0, eingetreten="faellt"),        # mit dem Trend, kein Treffer
            fall(id=3, rueck_pct=-0.3, richtung="steigt", treffer=0, eingetreten="faellt"),       # gegen den Trend
            fall(id=4, rueck_pct=0.01, richtung="steigt"),                                        # kein Trend (unter der Schwelle)
            fall(id=5, t_hebel=3, t_gewinn=-30.0), fall(id=6, t_hebel=2, t_gewinn=10.0), fall(id=7, t_hebel=1, t_gewinn=-5.0),
            fall(id=8, status="offen"),
        ]
        fb = vt.fehlerbilder(faelle)
        self.assertEqual(fb["mit_trend"], (1, 2))
        self.assertEqual(fb["gegen_trend"], (0, 1))
        self.assertEqual(fb["hebel_hoch"], (2, -20.0))
        self.assertEqual(fb["hebel_eins"], (5, -29.0))
        self.assertEqual(fb["n_trades"], 7)                                                      # der offene Fall zählt nicht

    def test_statistik_text(self):
        faelle = [fall(id=i, t_status="stop" if i % 2 else "ablauf", abgabe=(T0 + timedelta(minutes=i)).isoformat()) for i in range(1, 9)]
        text, fb = vt.lern_statistik_text(faelle, 60, 0.4)
        for stueck in ("Fenster: die letzten 8", "15 min: n=8", "Trefferquote nach Regel", "Richtungstreffer", "Typische Fehler", "Stop zu eng: 4 von 4",
                       "Ziel zu weit", "Richtung falsch", "Bewegung kleiner als die Kosten", "Gegen den Trend", "Hebel 2 bis 3", "0,80 %"):
            self.assertIn(stueck, text)
        text5, _ = vt.lern_statistik_text(faelle, 5, 0.4)
        self.assertIn("die letzten 5", text5)

    def test_faelle_text(self):
        faelle = [fall(id=1, t_status="stop", t_gesperrt="p"), fall(id=2, status="offen")]
        text = vt.faelle_text(faelle)
        for stueck in ("BTC/EUR 15 min", "sagte steigt", "kurs: Aufwärtstrend", "Passiert: Kurs +0.200 %", "Treffer ja", "Trade endete mit stop", "[gesperrt: p]"):
            self.assertIn(stueck, text)
        self.assertEqual(text.count("\n"), 0)                                                    # der offene Fall fehlt
        self.assertIn("noch keine ausgewerteten", vt.faelle_text([]))


# ----------------------------------------------------------------------------- Der Lauf lernt

class LernLaufTest(MitOrdner):
    def test_nachbesprechung_lektionen_und_kontrollrunden(self):
        t = self.sim()
        versionen = self.zeilen(t, "SELECT * FROM lektionen_versionen ORDER BY version")
        self.assertGreaterEqual(len(versionen), 2)
        self.assertEqual([v["version"] for v in versionen], list(range(1, len(versionen) + 1)))
        self.assertEqual(json.loads(versionen[0]["lektionen"]), [{**LEKTION, "vorlaeufig": False}])
        # höchstens ein KI-Aufruf je Nachbesprechung
        self.assertEqual(len(self.lehrer.debriefs), len(versionen))
        self.assertEqual(self.zeilen(t, "SELECT COUNT(*) n FROM aufrufe WHERE art='nachbesprechung'")[0]["n"], len(versionen))
        # Abstand: frühestens alle 20 Minuten, nie vor 6 neuen Fällen
        zeiten = [vt._zeit(v["zeit"]) for v in versionen]
        for a, b in zip(zeiten, zeiten[1:]):
            self.assertGreaterEqual((b - a).total_seconds(), 20 * 60)
        # Die Nachbesprechung bekommt Statistik, Fälle und (ab Version 2) die bisherigen Lektionen
        for i, p in enumerate(self.lehrer.debriefs):
            for stueck in ("STATISTIK", "Typische Fehler", "EINZELFÄLLE", "kurs: Selbsttest (kurs)", "Passiert: Kurs"):
                self.assertIn(stueck, p)
            self.assertEqual("Setze den Stop weiter weg" in p.split("STATISTIK")[0], i > 0)
        # Jeder spätere Vorhersage-Prompt enthält die Lektionen - außer in den Kontrollrunden
        vor = self.zeilen(t, "SELECT * FROM vorhersagen WHERE horizont_min=15 ORDER BY id")
        mit = [p for p in self.lehrer.prompts if "LEKTIONEN AUS DEINEN BISHERIGEN VORHERSAGEN" in p]
        ohne = [p for p in self.lehrer.prompts if "LEKTIONEN AUS DEINEN BISHERIGEN VORHERSAGEN" not in p]
        self.assertGreater(len(mit), 3)
        self.assertTrue(any("Version 2" in p for p in mit))
        kontrolle = [v for v in vor if v["kontrolle"]]
        spaeter = [v for v in vor if v["lektionen_version"] >= 1]
        self.assertTrue(kontrolle)
        self.assertEqual(len(kontrolle), sum(1 for v in spaeter if v["runde"] % 3 == 0))
        self.assertTrue(all(v["runde"] % 3 == 0 for v in kontrolle))
        self.assertEqual(len(mit), len(spaeter) - len(kontrolle))
        # vor der ersten Nachbesprechung gab es nichts zu lernen: Version 0, keine Kontrolle
        erste = [v for v in vor if v["lektionen_version"] == 0]
        self.assertTrue(erste and not any(v["kontrolle"] for v in erste))
        self.assertEqual([v["lektionen_version"] for v in vor], sorted(v["lektionen_version"] for v in vor))
        self.assertEqual(len(ohne) + len(mit), len(self.lehrer.prompts))

    def test_wirkung_je_version_in_der_auswertung(self):
        t = self.sim()
        a = vt.analyse(t.db, laeufe=20)
        namen = [z["name"] for z in a["lernen"]["zeilen"]]
        self.assertEqual(namen[0], "ohne Lektionen")
        self.assertIn("Version 1", namen)
        self.assertEqual(namen[-1], "Kontrolle")
        self.assertEqual(sum(z["reg"]["n"] for z in a["lernen"]["zeilen"]), a["n_ausgewertet"])
        self.assertIsNotNone(a["lernen"]["vergleich_kontrolle"])
        self.assertIsNotNone(a["lernen"]["vorher_nachher"])
        text = vt.bericht_bauen(t.db, a, None, vt.technik_pruefen(t.db)).text()
        for stueck in ("Wirkung je Lektionen-Version", "Kontrolle", "Aktuelle Lektionen (Version", "Setze den Stop weiter weg", "Achtung Überanpassung",
                       "Kontrollrunden", "Vorzeichen"):
            self.assertIn(stueck, text)

    def test_vorlaeufige_lektion_wird_gekennzeichnet_und_bei_unveraenderten_belegen_gestrichen(self):
        t = self.sim(lektionen=({**LEKTION, "belege": 2},), lern_neu=4, lern_abstand=15)
        versionen = self.zeilen(t, "SELECT * FROM lektionen_versionen ORDER BY version")
        self.assertGreaterEqual(len(versionen), 3)
        erste, zweite = json.loads(versionen[0]["lektionen"]), json.loads(versionen[1]["lektionen"])
        self.assertEqual([l["vorlaeufig"] for l in erste], [True])
        self.assertEqual(zweite, [])                                                              # gleiche Belegzahl: nicht bestätigt
        self.assertIn("vorläufig, nicht bestätigt", versionen[1]["veraltet"])
        self.assertTrue(any("VORLÄUFIG, noch zu bestätigen" in p for p in self.lehrer.prompts))
        self.assertTrue(any("VORLÄUFIG" in d for d in self.lehrer.debriefs[1:]))                  # die Nachbesprechung bekommt sie zur Prüfung
        self.assertTrue(any("vorläufig" in m for m in self.meldungen))

    def test_lektionen_unter_mindestzahl_werden_verworfen(self):
        t = self.sim(lektionen=({**LEKTION, "belege": 1},), lern_neu=4)
        self.assertTrue(self.lehrer.debriefs)
        for v in self.zeilen(t, "SELECT * FROM lektionen_versionen"):
            self.assertEqual(json.loads(v["lektionen"]), [])
        self.assertTrue(any("weniger als 2 Belegen verworfen" in m for m in self.meldungen))

    def test_ohne_kontrollrunden(self):
        t = self.sim(kontrollrunde=0)
        self.assertEqual(self.zeilen(t, "SELECT COUNT(*) n FROM vorhersagen WHERE kontrolle=1")[0]["n"], 0)
        mit = [p for p in self.lehrer.prompts if "LEKTIONEN AUS" in p]
        spaeter = self.zeilen(t, "SELECT COUNT(*) n FROM vorhersagen WHERE horizont_min=15 AND lektionen_version >= 1")[0]["n"]
        self.assertGreater(spaeter, 3)
        self.assertEqual(len(mit), spaeter)                                                       # jeder spätere Prompt hat die Lektionen

    def test_lernen_aus_und_regeln_aus(self):
        t = self.sim(lernen=False, regeln=False)
        self.assertEqual(self.lehrer.debriefs, [])
        self.assertEqual(self.zeilen(t, "SELECT COUNT(*) n FROM lektionen_versionen")[0]["n"], 0)
        self.assertEqual(self.zeilen(t, "SELECT COUNT(*) n FROM anpassungen")[0]["n"], 0)
        self.assertFalse(any("LEKTIONEN" in p for p in self.lehrer.prompts))
        self.assertEqual(self.zeilen(t, "SELECT COUNT(*) n FROM trades WHERE gesperrt != ''")[0]["n"], 0)

    def test_abstand_und_mindestzahl_neuer_faelle(self):
        t = self.sim(lern_abstand=500)
        self.assertEqual(self.lehrer.debriefs, [])
        self.assertEqual(self.zeilen(t, "SELECT COUNT(*) n FROM lektionen_versionen")[0]["n"], 0)

    def test_mindestzahl_neuer_faelle(self):
        t = self.sim(lern_neu=500, lern_abstand=1)
        self.assertEqual(self.lehrer.debriefs, [])

    def test_ungueltige_lektionen_ein_aufruf_alte_bleiben(self):
        t = self.sim(debrief="das ist kein JSON", dauer=70)
        self.assertGreaterEqual(len(self.lehrer.debriefs), 1)
        self.assertEqual(self.zeilen(t, "SELECT COUNT(*) n FROM lektionen_versionen")[0]["n"], 0)
        fehler = self.zeilen(t, "SELECT * FROM aufrufe WHERE art='nachbesprechung'")
        self.assertEqual(len(fehler), len(self.lehrer.debriefs))                              # ein Aufruf je Nachbesprechung, keine Nachfrage
        self.assertTrue(all(f["fehler"] and f["versuche"] == 1 for f in fehler))
        self.assertTrue(any("ohne brauchbare Antwort" in m for m in self.meldungen))
        self.assertGreater(self.zeilen(t, "SELECT COUNT(*) n FROM vorhersagen")[0]["n"], 10)   # der Lauf ging weiter

    def test_zustand_ueberlebt_den_neustart(self):
        t = self.sim(falsch=True)
        regeln, version = t.regeln.zu_json(), t._lektionen[0]
        self.assertGreaterEqual(version, 1)
        t.schliessen()
        t2 = vt.Vorwaertstest(self.dir, einst={}, quelle=object(), treiber=lambda p: "", ausgabe=lambda s: None)
        self.addCleanup(t2.schliessen)
        self.assertEqual(t2.regeln.zu_json(), regeln)
        self.assertEqual(t2._lektionen[0], version)
        self.assertEqual(t2._lektionen[1], [{**LEKTION, "vorlaeufig": False}])


class FesteRegelnLaufTest(MitOrdner):
    def test_schlechte_ergebnisse_loesen_feste_anpassungen_aus(self):
        t = self.sim(falsch=True, lernen=False)
        log = self.zeilen(t, "SELECT * FROM anpassungen ORDER BY id")
        self.assertTrue(log)
        arten = {x["art"] for x in log}
        self.assertIn("hebel_max", arten)
        self.assertIn("erwartung_gegen_kosten", arten)
        r = vt.Regeln.aus_json(vt._meta_lesen(t.db, "regeln"))
        self.assertLess(r.hebel_max, 3)
        self.assertGreater(r.k_kosten, 0)
        # Jede Anpassung ist begründet und nennt n >= 4
        self.assertTrue(all(x["grund"] and x["n"] >= 4 for x in log))
        self.assertTrue(any("Feste Anpassung" in m for m in self.meldungen))
        # Gesperrte Trades laufen als Schatten weiter (werden abgerechnet), zählen aber nicht im Papier-Konto
        gesperrt = self.zeilen(t, "SELECT * FROM trades WHERE gesperrt != ''")
        self.assertTrue(gesperrt)
        self.assertTrue(all(g["gesperrt"] in ("k", "p", "pause", "pause,k", "p,k", "pause,p", "pause,p,k") for g in gesperrt))
        self.assertTrue(any(g["status"] in vt.ABGERECHNET for g in gesperrt))
        a = vt.analyse(t.db, laeufe=5)
        echt = sum(a["horizonte"][h]["trades"]["echt"]["n"] for h in vt.HORIZONTE)
        schatten = sum(a["horizonte"][h]["trades"]["schatten"]["n"] for h in vt.HORIZONTE)
        self.assertGreater(schatten, 0)
        self.assertEqual(echt, self.zeilen(t, "SELECT COUNT(*) n FROM trades WHERE gesperrt = '' AND status IN ('ziel','stop','ablauf')")[0]["n"])
        # Der Hebel eines Trades ist nie höher als die damals gültige Grenze
        self.assertTrue(all(x["hebel"] <= x["hebel_ki"] for x in self.zeilen(t, "SELECT hebel, hebel_ki FROM trades")))
        self.assertTrue(any(x["hebel"] < x["hebel_ki"] for x in self.zeilen(t, "SELECT hebel, hebel_ki FROM trades")))

    def test_gute_ergebnisse_aendern_nichts_hartes(self):
        # Ohne Gebühren ist der Hellseher klar im Plus. (Mit Gebühren verliert selbst er auf diesen ruhigen Kursen: dann sind die Anpassungen richtig.)
        t = self.sim(falsch=False, lernen=False, gebuehr=0.0)
        r = vt.Regeln.aus_json(vt._meta_lesen(t.db, "regeln"))
        self.assertEqual((r.min_p, r.hebel_max, r.pausiert), (0.0, 3, []))
        self.assertEqual(self.zeilen(t, "SELECT COUNT(*) n FROM trades WHERE gesperrt != ''")[0]["n"], 0)

    def test_bericht_nennt_die_anpassungen(self):
        t = self.sim(falsch=True, lernen=False)
        a = vt.analyse(t.db, laeufe=5)
        text = vt.bericht_bauen(t.db, a, None, vt.technik_pruefen(t.db)).text()
        self.assertIn("Feste Regeln (ohne KI) aktuell", text)
        self.assertIn("hebel_max", text)
        self.assertIn("gesperrt (nur beobachtet)", text)


class FehlerPruefungZuerstTest(MitOrdner):
    def test_technikfehler_stoppt_das_lernen(self):
        original = vt.handel_ergebnis

        def kaputt(seite, einstieg, ausstieg, gebuehr_pct, hebel=1.0):          # Vorzeichenfehler: Long und Short vertauscht
            return original("short" if seite == "long" else "long", einstieg, ausstieg, gebuehr_pct, hebel)
        with mock.patch.object(vt, "handel_ergebnis", kaputt):
            t = self.sim()
        self.assertEqual(self.lehrer.debriefs, [])                                  # kein Lernen aus kaputten Daten
        self.assertEqual(self.zeilen(t, "SELECT COUNT(*) n FROM lektionen_versionen")[0]["n"], 0)
        self.assertEqual(self.zeilen(t, "SELECT COUNT(*) n FROM anpassungen")[0]["n"], 0)
        pruefungen = self.zeilen(t, "SELECT * FROM pruefungen")
        self.assertTrue(pruefungen and not any(p["ok"] for p in pruefungen))
        self.assertTrue(any("TECHNIKFEHLER GEFUNDEN" in m for m in self.meldungen))
        self.assertTrue(any("Vorzeichen:" in m for m in self.meldungen))
        # und die Auswertung warnt ebenfalls
        a = vt.analyse(t.db, laeufe=5)
        text = vt.bericht_bauen(t.db, a, None, vt.technik_pruefen(t.db)).text()
        self.assertIn("FEHLER GEFUNDEN", text)

    def test_sehr_schlechte_trefferquote_prueft_zuerst_die_technik(self):
        t = self.sim(falsch=True, dauer=90)
        pruefungen = self.zeilen(t, "SELECT * FROM pruefungen")
        self.assertTrue(pruefungen)
        self.assertTrue(any("Trefferquote sehr schlecht" in p["anlass"] for p in pruefungen))
        self.assertTrue(all(p["ok"] for p in pruefungen))                           # der Fake ist falsch, die Technik nicht
        self.assertTrue(any("Trefferquote sehr schlecht" in m and "prüfe zuerst die Technik" in m for m in self.meldungen))
        self.assertTrue(any("Technikprüfung: Zeit: OK" in m for m in self.meldungen))
        self.assertGreaterEqual(len(self.lehrer.debriefs), 1)                       # ohne Technikfehler wird danach trotzdem gelernt

    def test_gute_trefferquote_loest_keine_warnung_aus(self):
        t = self.sim(falsch=False, dauer=90)
        pruefungen = self.zeilen(t, "SELECT * FROM pruefungen")
        self.assertTrue(pruefungen)
        self.assertFalse(any("sehr schlecht" in p["anlass"] for p in pruefungen))
        self.assertFalse(any("sehr schlecht" in m for m in self.meldungen))


if __name__ == "__main__":
    unittest.main()
