"""Morgenlage (lage.py): Aufbau, Text, JSON-Datei, Telegram-Kürzung, Fehler je Wert, Kommandozeile. Offline, Fake-Treiber."""
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from kitrader import einstellungen as einst_modul
from kitrader import ki, lage, osiris, telegram
from kitrader.__main__ import main
from kitrader.einstellungen import mit_standard
from kitrader.journal import Journal

from .daten import MONTAG, Uhr
from .test_ki import FakeHistorie, FakeTreiber, GUT, osiris_lage, tageskerzen
from .test_osiris import OsirisServer

UTC = timezone.utc


def lage_mit_veraenderung() -> osiris.Lage:
    """OSIRIS-Lage mit Textabschnitt „Veränderung“ (wie ihn osiris._text erzeugt)."""
    text = ("OSIRIS-Lage, Stand 2026-03-02 09:00 UTC (Quelle test).\nKonfliktzonen: Stufe war: Ukraine.\n\n"
            "Veränderung seit dem Schnappschuss von 2026-03-02 03:00 UTC (vor 6 h):\n"
            "  - Vorfälle gesamt +3 (jetzt 40); keine neuen Orange/Rot\n  - Konfliktzone Iran: Stufe tension → war\n"
            "  - Märkte seit dem letzten Schnappschuss (≥ 0,5 %): Gold 0,70 %")
    return osiris.Lage(MONTAG - timedelta(hours=1), MONTAG - timedelta(hours=7), text, osiris_lage().daten)


class Basis(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ordner = Path(self._d.name)
        self.einst = mit_standard({"osiris": {"automatisch": False}, "ki": {"treiber": "claude_cli"}})
        self.j = Journal(":memory:", self.einst, Uhr(MONTAG))
        self.addCleanup(self.j.beenden)
        self.angefragt: list[tuple] = []

    def geber(self, wert, takt, anzahl):
        self.angefragt.append((wert, takt, anzahl))
        if wert == "KAPUTT":
            raise RuntimeError("Kursquelle weg")
        return tageskerzen(60, 0.5 if wert != "FALLEND" else -0.5)

    def bauen(self, werte=("BTC/EUR", "ETH/EUR"), treiber=None, **kw):
        kw.setdefault("lage", lage_mit_veraenderung())
        return lage.erstellen(self.einst, self.ordner, list(werte), treiber=treiber, kerzen_geber=self.geber, journal=self.j,
                              historie=FakeHistorie(), jetzt=MONTAG, **kw)


class ErstellenTest(Basis):
    def test_aufbau_mit_einschaetzungen_osiris_und_trefferquote(self):
        t = FakeTreiber(GUT)
        m = self.bauen(treiber=t)
        self.assertEqual((m["version"], m["takt"], m["treiber"]), (1, "1d", "eigener"))
        self.assertEqual([w["wert"] for w in m["werte"]], ["BTC/EUR", "ETH/EUR"])
        self.assertEqual(len(t.prompts), 2)                                       # ein KI-Aufruf je Wert
        w = m["werte"][0]
        self.assertEqual(set(w["prognosen"]), {"kurz", "mittel", "lang"})
        self.assertEqual((w["prognosen"]["kurz"]["richtung"], w["prognosen"]["kurz"]["wahrscheinlichkeit"]), ("steigt", 0.6))
        self.assertEqual(w["prognosen"]["lang"]["richtung"], "faellt")
        self.assertIsNotNone(w["prognosen"]["kurz"]["journal_id"])
        self.assertEqual(w["fehler"], "")
        k = tageskerzen(60, 0.5)
        self.assertAlmostEqual(w["preis"], k[-1].c)
        self.assertAlmostEqual(w["aenderung_prozent"]["1"], (k[-1].c / k[-2].c - 1) * 100)
        self.assertAlmostEqual(w["aenderung_prozent"]["30"], (k[-1].c / k[-31].c - 1) * 100)
        self.assertEqual(self.angefragt[0], ("BTC/EUR", "1d", 250))
        self.assertTrue(m["osiris"]["veraenderung"].startswith("Veränderung seit dem Schnappschuss"))
        self.assertIn("Konfliktzone Iran: Stufe tension → war", m["osiris"]["veraenderung"])
        self.assertNotIn("Konfliktzonen: Stufe war", m["osiris"]["veraenderung"])      # nur der Veränderungsteil
        self.assertAlmostEqual(m["osiris"]["alter_h"], 1.0)
        self.assertEqual(m["trefferquote"]["1d"], {"n": 0, "treffer": 0, "quote": None, "offen": 2})   # beide Werte offen
        self.assertEqual(list(m["trefferquote"]), ["1d", "3M", "1J"])
        self.assertEqual(len(self.j.einschaetzungen()), 6)                        # alles im Journal, wie bei der Einzel-Einschätzung

    def test_trefferquote_zaehlt_nur_ki_und_nennt_quote(self):
        for i, treffer in enumerate((True, True, False)):
            nr = self.j.einschaetzung("X", "steigt", "1d", f"{ki.KI_MARKE} Test", 100.0, zeit=MONTAG - timedelta(days=5 + i))
        self.j.einschaetzung("X", "steigt", "1d", "eigene Meinung", 100.0, zeit=MONTAG - timedelta(days=9))
        preise = iter((102.0, 103.0, 99.0, 150.0))
        self.j.auswerten(lambda w, f: next(preise), MONTAG)
        q = lage.trefferquote(self.j)["1d"]
        self.assertEqual((q["n"], q["treffer"]), (3, 2))                          # die eigene Meinung zählt nicht
        self.assertAlmostEqual(q["quote"], 200 / 3)

    def test_fehler_bei_einem_wert_stoppt_die_anderen_nicht(self):
        t = FakeTreiber(GUT)
        m = self.bauen(werte=("KAPUTT", "BTC/EUR"), treiber=t)
        self.assertIn("Keine Kerzen: Kursquelle weg", m["werte"][0]["fehler"])
        self.assertIsNone(m["werte"][0]["preis"])
        self.assertEqual(m["werte"][1]["fehler"], "")
        self.assertEqual(len(t.prompts), 1)
        t = FakeTreiber(ki.KiFehler("CLI weg"), GUT)
        m = self.bauen(treiber=t)
        self.assertEqual(m["werte"][0]["fehler"], "KI: CLI weg")
        self.assertEqual(m["werte"][0]["prognosen"], {})
        self.assertIsNotNone(m["werte"][0]["preis"])                              # Kurs und Änderung bleiben
        self.assertEqual(set(m["werte"][1]["prognosen"]), {"kurz", "mittel", "lang"})
        t = FakeTreiber("kein JSON")
        m = self.bauen(werte=("BTC/EUR",), treiber=t)
        self.assertIn("auch nach einer Nachfrage ungültig", m["werte"][0]["fehler"])

    def test_treiber_aus_fragt_nichts_und_speichert_nichts(self):
        m = self.bauen(treiber="aus")
        self.assertEqual(m["treiber"], "aus")
        self.assertTrue(all(w["prognosen"] == {} and w["fehler"] == "" and w["preis"] for w in m["werte"]))
        self.assertEqual(self.j.einschaetzungen(), [])
        self.assertIn("KI aus", lage.als_text(m))

    def test_watchlist(self):
        self.assertEqual(lage.watchlist(self.einst), ["BTC/EUR", "ETH/EUR"])
        self.assertEqual(lage.watchlist(self.einst, "A, B,A,,C"), ["A", "B", "C"])
        e = mit_standard({"watchlist": {"werte": ["SAP"]}})
        self.assertEqual(lage.watchlist(e), ["SAP"])
        with self.assertRaises(ValueError):
            lage.watchlist(mit_standard({"watchlist": {"werte": []}}))
        with self.assertRaises(einst_modul.EinstellungsFehler):
            einst_modul.pruefen(mit_standard({"watchlist": {"werte": "BTC/EUR"}}))


class TextTest(Basis):
    def test_text_hat_alle_teile(self):
        m = self.bauen(treiber=FakeTreiber(GUT))
        text = lage.als_text(m)
        self.assertIn("MORGENLAGE 2026-03-02 10:00 UTC (Kerzen 1d, KI-Treiber eigener)", text)
        self.assertRegex(text, r"BTC/EUR  \d+[.,]\d+  1 T \+[\d,]+ %, 7 T \+[\d,]+ %, 30 T \+[\d,]+ %, Schwankung \(ATR\)")
        self.assertIn("kurz (1 Tag)       STEIGT 60 %: Grund kurz", text)
        self.assertIn("mittel (3 Monate)  SEITWAERTS 50 %", text)
        self.assertIn("lang (1 Jahr)      FAELLT 40 %", text)
        self.assertIn("OSIRIS, Stand 2026-03-02 09:00 UTC (Alter 1,0 h)", text)
        self.assertIn("Konfliktzone Iran: Stufe tension → war", text)
        self.assertIn("kurz (1d): ausgewertet n=0, Treffer 0, Quote noch keine, noch offen 2", text)
        self.assertIn("keine Anlageberatung", text)

    def test_fehlerzeile_statt_wert_und_fehlendes_osiris(self):
        m = self.bauen(werte=("KAPUTT",), treiber="aus", lage=osiris.Lage(None, None, "OSIRIS: Es ist noch kein Schnappschuss gespeichert."))
        text = lage.als_text(m)
        self.assertIn("KAPUTT: Keine Kerzen: Kursquelle weg", text)
        self.assertIn("OSIRIS: kein Schnappschuss gespeichert", text)

    def test_laufende_kerze_wird_gekennzeichnet(self):
        # tageskerzen enden am Tag vor MONTAG; Jetzt = MONTAG 10:00: die letzte Kerze (1. März) ist abgeschlossen
        self.assertFalse(self.bauen(werte=("A",), treiber="aus")["werte"][0]["laufend"])
        m = lage.erstellen(self.einst, self.ordner, ["A"], treiber="aus", kerzen_geber=self.geber, journal=self.j,
                           lage=lage_mit_veraenderung(), jetzt=MONTAG - timedelta(hours=20))
        self.assertTrue(m["werte"][0]["laufend"])
        self.assertIn("letzte Kerze läuft noch", lage.als_text(m))

    def test_kompakt_ist_kuerzer(self):
        m = self.bauen(treiber=FakeTreiber({n: {**p, "begruendung": "x " * 400} for n, p in GUT.items()}))
        m["osiris"]["veraenderung"] = "Veränderung seit ...:\n" + "\n".join(f"  - Zeile {i}" for i in range(60))
        voll, kompakt = lage.als_text(m), lage.als_text(m, kompakt=True)
        self.assertLess(len(kompakt), len(voll))
        self.assertIn("… 48 weitere Zeilen", kompakt)
        self.assertIn("…", kompakt)


class KuerzenTest(Basis):
    def test_kurzer_text_bleibt(self):
        self.assertEqual(lage.kuerzen("abc"), "abc")

    def test_langer_text_wird_am_zeilenende_gekuerzt_mit_vermerk(self):
        text = "\n".join(f"Zeile {i:04d} " + "x" * 50 for i in range(200))
        k = lage.kuerzen(text)
        self.assertLessEqual(len(k), telegram.MAX_TEXT)
        self.assertLessEqual(telegram.MAX_TEXT, 4096)
        self.assertTrue(k.endswith(lage.GEKUERZT))
        letzte = k[:-len(lage.GEKUERZT)].splitlines()[-1]
        self.assertRegex(letzte, r"^Zeile \d{4} x{50}$")                          # keine halbe Zeile
        self.assertLessEqual(len(lage.kuerzen("y" * 10000, 500)), 500)            # auch ohne Zeilenumbruch

    def test_melden_sendet_gekuerzten_kompakttext_ueber_telegram_melden(self):
        m = self.bauen(werte=tuple(f"W{i}" for i in range(40)), treiber=FakeTreiber(GUT))
        gesendet = []
        with mock.patch.object(telegram, "melden", side_effect=lambda text, einst=None, warten=False: gesendet.append(text) or True):
            self.assertTrue(lage.melden(m, self.einst))
        self.assertEqual(len(gesendet), 1)
        self.assertLessEqual(len(gesendet[0]), telegram.MAX_TEXT)
        self.assertTrue(gesendet[0].endswith(lage.GEKUERZT))                      # 40 Werte passen nicht in eine Nachricht
        self.assertTrue(gesendet[0].startswith("MORGENLAGE"))

    def test_melden_ohne_telegram_ist_false_und_wirft_nicht(self):
        m = self.bauen(werte=("BTC/EUR",), treiber="aus")
        with mock.patch.dict(os.environ, {"KITRADER_DATEN": str(self.ordner)}), \
                mock.patch.object(telegram, "zugang_lesen", return_value=telegram.Zugang("", "")):
            self.assertFalse(lage.melden(m, self.einst))


class DateiTest(Basis):
    def test_json_datei_im_datenordner_ist_gueltig_und_vollstaendig(self):
        m = self.bauen(treiber=FakeTreiber(GUT))
        pfad = lage.speichern(m, self.ordner / "neu")
        self.assertEqual(pfad, self.ordner / "neu" / "morgenlage.json")
        gelesen = json.loads(pfad.read_text(encoding="utf-8"))
        self.assertEqual(gelesen, m)
        self.assertEqual(set(gelesen), {"version", "erstellt", "treiber", "takt", "hinweis", "werte", "osiris", "nachrichten", "welt",
                                       "trefferquote"})
        self.assertEqual(set(gelesen["werte"][0]), {"wert", "preis", "kerzen_bis", "laufend", "aenderung_prozent", "atr_prozent", "prognosen",
                                                    "warnungen", "fehler"})
        self.assertEqual(set(gelesen["werte"][0]["prognosen"]["kurz"]),
                         {"horizont", "richtung", "wahrscheinlichkeit", "begruendung", "gegenargumente", "was_widerlegt", "journal_id"})
        self.assertEqual(list((self.ordner / "neu").iterdir()), [pfad])           # keine Hilfsdatei übrig
        m["werte"] = []
        lage.speichern(m, self.ordner / "neu")                                    # überschreibt
        self.assertEqual(json.loads(pfad.read_text())["werte"], [])

    def test_ausfuehren_erstellt_speichert_und_meldet(self):
        gesendet = []
        with mock.patch.object(telegram, "melden", side_effect=lambda text, einst=None, warten=False: gesendet.append(text) or True):
            m, pfad, ok = lage.ausfuehren(self.einst, self.ordner, ["BTC/EUR"], FakeTreiber(GUT), kerzen_geber=self.geber,
                                          lage=lage_mit_veraenderung(), journal=self.j, historie=FakeHistorie(), jetzt=MONTAG)
            self.assertTrue(ok)
            self.assertTrue(pfad.is_file())
            self.assertEqual(len(gesendet), 1)
            _, pfad2, ok2 = lage.ausfuehren(self.einst, self.ordner, ["BTC/EUR"], "aus", mit_telegram=False, mit_datei=False,
                                            kerzen_geber=self.geber, lage=lage_mit_veraenderung(), journal=self.j, jetzt=MONTAG)
            self.assertEqual((pfad2, ok2), (None, False))
            self.assertEqual(len(gesendet), 1)


class KommandozeileTest(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.dir = Path(self._d.name)
        cli = self.dir / "claude"
        cli.write_text("#!/usr/bin/env python3\nimport json\nprint(json.dumps({'is_error': False, 'result': json.dumps(%r)}))\n" % (GUT,))
        cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
        self.server = OsirisServer()
        self.addCleanup(self.server.stop)
        (self.dir / "e.toml").write_text(f'[osiris]\nadresse = "{self.server.adresse}"\npause_s = 0\n[ki]\nclaude_befehl = "{cli}"\n'
                                         '[watchlist]\nwerte = ["AAA", "BBB"]\n[telegram]\naktiv = false\n')
        for name, wert in (("KITRADER_DATEN", self.dir / "daten"), ("KITRADER_KONFIG", self.dir / "e.toml")):
            patch = mock.patch.dict(os.environ, {name: str(wert)})
            patch.start()
            self.addCleanup(patch.stop)
        quelle = mock.Mock()
        quelle.kerzen.side_effect = lambda wert, takt, anzahl: tageskerzen(60)
        patch = mock.patch.object(lage, "quelle_aus_einstellungen", return_value=quelle)
        patch.start()
        self.addCleanup(patch.stop)

    def lauf(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_morgenlage_mit_ki_text_datei_und_journal(self):
        code, out, err = self.lauf("morgenlage", "--ohne-telegram")
        self.assertEqual(code, 0, err)
        self.assertIn("MORGENLAGE", out)
        self.assertIn("AAA  ", out)
        self.assertIn("BBB  ", out)
        self.assertEqual(out.count("STEIGT 60 %"), 2)
        self.assertIn("JSON für die Oberfläche:", out)
        daten = json.loads((self.dir / "daten" / "morgenlage.json").read_text(encoding="utf-8"))
        self.assertEqual([w["wert"] for w in daten["werte"]], ["AAA", "BBB"])
        self.assertEqual(len(self.server.anfragen), len(osiris.FEEDS))             # OSIRIS wurde einmal (nicht je Wert) geholt
        code, out, _ = self.lauf("journal", "--trefferquote")
        self.assertEqual(code, 0)

    def test_treiber_aus_und_eigene_watchlist_ohne_ki_aufruf(self):
        code, out, err = self.lauf("morgenlage", "--treiber", "aus", "--watchlist", "XYZ", "--ohne-telegram", "--ohne-datei")
        self.assertEqual(code, 0, err)
        self.assertIn("XYZ  ", out)
        self.assertIn("KI aus", out)
        self.assertNotIn("JSON für die Oberfläche", out)
        self.assertFalse((self.dir / "daten" / "morgenlage.json").exists())

    def test_telegram_nicht_eingerichtet_wird_gemeldet(self):
        code, out, _ = self.lauf("morgenlage", "--treiber", "aus")
        self.assertEqual(code, 0)
        self.assertIn("Telegram: nicht gesendet", out)

    def test_leere_watchlist_ist_eine_klare_fehlermeldung(self):
        (self.dir / "e.toml").write_text(f'[osiris]\nadresse = "{self.server.adresse}"\npause_s = 0\n[watchlist]\nwerte = []\n')
        code, _, err = self.lauf("morgenlage", "--treiber", "aus")
        self.assertEqual(code, 2)
        self.assertIn("Watchlist ist leer", err)


if __name__ == "__main__":
    unittest.main()
