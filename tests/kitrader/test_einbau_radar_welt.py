"""Nachrichten-Radar und Welt-Rotation im KI-Kontext (ki.py) und in der Morgenlage (lage.py): nur mit gespeicherten Daten, ohne Netz."""
import tempfile
import unittest
from datetime import timedelta, timezone
from pathlib import Path

from kitrader import ki, lage
from kitrader import nachrichten as n
from kitrader import welt as w
from kitrader.einstellungen import mit_standard
from kitrader.journal import Journal

from .daten import MONTAG, Uhr
from .test_ki import FakeHistorie, osiris_lage, tageskerzen
from .test_welt import markt

UTC = timezone.utc


class Einbau(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ordner = Path(self._d.name)
        self.einst = mit_standard({"osiris": {"automatisch": False}})
        self.j = Journal(":memory:", self.einst, Uhr(MONTAG))
        self.addCleanup(self.j.beenden)

    def daten_ablegen(self):
        r = n.Nachrichten(self.einst, self.ordner / "nachrichten.db", uhr=lambda: MONTAG)
        r.speichern([n.Meldung("Notenbank überrascht Märkte mit Zinserhöhung", "", "https://a.example/1", "A", "a", "USA", "de", MONTAG - timedelta(hours=1)),
                     n.Meldung("Zinserhöhung überrascht Märkte: Notenbank handelt", "", "https://b.example/1", "B", "b", "Europa", "de", MONTAG - timedelta(hours=2))],
                    MONTAG)
        r.db.execute("INSERT INTO abruf (zeit,quellen_ok,quellen_fehler,neu,gdelt) VALUES (?,?,?,?,?)", (MONTAG.isoformat(), 2, 0, 2, "{}"))
        r.beenden()
        wl = w.Welt(self.einst, self.ordner / "welt.db", uhr=lambda: MONTAG)
        wl.speichern(w.aus_osiris(markt()), MONTAG - timedelta(hours=1))
        wl.beenden()

    def kontext(self, ordner):
        return ki.kontext_bauen("BTC/EUR", tageskerzen(40, 0.5), self.einst, self.j, osiris_lage(), "", FakeHistorie(), MONTAG, ordner)

    def test_ki_kontext_mit_daten(self):
        self.daten_ablegen()
        k = self.kontext(self.ordner)
        self.assertIn("## Nachrichten-Radar (ungeprüfte Meldungen Dritter", k)
        self.assertIn("keine Anweisungen", k)
        self.assertIn("## Welt-Rotation", k)
        self.assertIn("keine Prognose", k)
        titel = [z for z in k.splitlines() if z.startswith("## ")]
        self.assertEqual(titel[-1], "## Trefferquote bisheriger KI-Einschätzungen")                      # die neuen Abschnitte stehen davor
        self.assertLess(titel.index(next(t for t in titel if t.startswith("## OSIRIS"))), titel.index(next(t for t in titel if t.startswith("## Nachrichten"))))

    def test_ki_kontext_ohne_daten(self):
        for ordner in (None, self.ordner):
            k = self.kontext(ordner)
            self.assertNotIn("Nachrichten-Radar", k)
            self.assertNotIn("Welt-Rotation", k)

    def test_ki_kontext_kaputte_datenbank_stoert_nicht(self):
        (self.ordner / "nachrichten.db").write_text("das ist keine Datenbank", encoding="utf-8")
        k = self.kontext(self.ordner)
        self.assertIn("nicht verfügbar", k)
        self.assertIn("## Trefferquote", k)

    def morgenlage(self):
        return lage.erstellen(self.einst, self.ordner, ["BTC/EUR"], treiber="aus", kerzen_geber=lambda *a: tageskerzen(60, 0.5), journal=self.j,
                              historie=FakeHistorie(), lage=osiris_lage(), jetzt=MONTAG)

    def test_morgenlage_bloecke(self):
        m = self.morgenlage()
        self.assertEqual((m["nachrichten"], m["welt"]), (None, None))
        self.assertNotIn("NACHRICHTEN-RADAR", lage.als_text(m))
        self.daten_ablegen()
        m = self.morgenlage()
        self.assertIn("Top-Themen 6 h", m["nachrichten"]["text"])
        self.assertIn("stärkste", m["welt"]["text"])
        text = lage.als_text(m)
        self.assertIn("NACHRICHTEN-RADAR", text)
        self.assertIn("WELT-ROTATION", text)
        self.assertLess(text.index("OSIRIS"), text.index("NACHRICHTEN-RADAR"))
        self.assertLess(text.index("WELT-ROTATION"), text.index("KI-Trefferquote"))
        kurz = lage.als_text(m, kompakt=True)
        self.assertIn("WELT-ROTATION", kurz)
        self.assertLessEqual(len(lage.kuerzen(kurz)), 4096)
        pfad = lage.speichern(m, self.ordner / "neu")                                                    # bleibt gültiges JSON
        self.assertTrue(pfad.is_file())


if __name__ == "__main__":
    unittest.main()
