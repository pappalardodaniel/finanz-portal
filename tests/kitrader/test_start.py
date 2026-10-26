"""`python3 -m kitrader start`: Sperrdatei, Teile unter Aufsicht (Absturz, Neustart, Beenden), Voreinstellungen, Zeitplan mit
gestellter Uhr, Autopilot-Teil mit Attrappe, Oberfläche als Teil, Startausgabe und ein echter Prozess mit Strg+C. Alles offline."""
import io
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from kitrader import einstellungen as _einst
from kitrader import ki as _ki
from kitrader import start
from kitrader.autopilot import Abbruch
from kitrader.einstellungen import mit_standard

UTC = timezone.utc
SRC = str(Path(__file__).resolve().parents[2] / "src")


def warte_bis(bedingung, frist=5.0):
    ende = time.monotonic() + frist
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.01)
    return bedingung()


class Ordner(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ordner = Path(self._d.name)


# ---------------------------------------------------------------- Sperrdatei

class SperreTest(Ordner):
    def test_zweiter_start_wird_verweigert_mit_klarem_text(self):
        a = start.Sperre(self.ordner)
        a.holen({"seit": "2026-10-26 08:00:00", "modus": "papier", "adresse": "http://127.0.0.1:8850/"})
        self.addCleanup(a.freigeben)
        b = start.Sperre(self.ordner)
        with self.assertRaises(Abbruch) as f:
            b.holen({"seit": "später"})
        text = str(f.exception)
        self.assertIn("läuft schon", text)
        self.assertIn("2026-10-26 08:00:00", text)
        self.assertIn("http://127.0.0.1:8850/", text)
        self.assertIn(str(os.getpid()), text)
        self.assertIn("start --beenden", text)

    def test_nach_dem_freigeben_geht_es_wieder(self):
        a = start.Sperre(self.ordner)
        a.holen({"modus": "papier"})
        self.assertEqual(start.Sperre.belegt_von(self.ordner)["modus"], "papier")
        a.freigeben()
        self.assertIsNone(start.Sperre.belegt_von(self.ordner))
        b = start.Sperre(self.ordner)
        b.holen({"modus": "demo"})
        b.freigeben()

    def test_liegengebliebene_datei_ohne_lebenden_prozess_blockiert_nicht(self):
        (self.ordner / start.SPERRDATEI).write_text(json.dumps({"pid": 999999999, "seit": "gestern"}), encoding="utf-8")
        self.assertIsNone(start.Sperre.belegt_von(self.ordner))
        a = start.Sperre(self.ordner)
        a.holen({})
        a.freigeben()

    def test_belegt_von_ohne_datei(self):
        self.assertIsNone(start.Sperre.belegt_von(self.ordner))

    def test_beenden_ohne_laufenden_start(self):
        zeilen = []
        self.assertEqual(start.laufenden_beenden(self.ordner, zeilen.append), 0)
        self.assertIn("läuft gerade nicht", zeilen[0])

    def test_beenden_schickt_sigterm_und_wartet(self):
        a = start.Sperre(self.ordner)
        a.holen({"seit": "x"})
        self.addCleanup(a.freigeben)
        zeilen = []
        with mock.patch.object(start.os, "kill", side_effect=lambda pid, sig: a.freigeben()) as kill:
            self.assertEqual(start.laufenden_beenden(self.ordner, zeilen.append, frist_s=3), 0)
        kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
        self.assertIn("beendet", zeilen[-1])

    def test_beenden_meldet_wenn_der_start_nicht_reagiert(self):
        a = start.Sperre(self.ordner)
        a.holen({})
        self.addCleanup(a.freigeben)
        zeilen = []
        with mock.patch.object(start.os, "kill"):
            self.assertEqual(start.laufenden_beenden(self.ordner, zeilen.append, frist_s=0.6), 1)
        self.assertIn("reagiert", zeilen[-1])


# ---------------------------------------------------------------- Teile unter Aufsicht

def dauerlauf(halt):
    halt.wait()


class AufsichtTest(Ordner):
    def start_bauen(self, teile, **kw):
        self.meldungen: list[str] = []
        self.telegram: list[str] = []
        self.journal: list[tuple] = []
        s = start.Start(mit_standard({}), self.ordner, teile, ausgabe=self.meldungen.append, telegram=self.telegram.append,
                        journal=lambda art, text: self.journal.append((art, text)),
                        basis_pause_s=kw.pop("basis_pause_s", 0.02), max_pause_s=kw.pop("max_pause_s", 0.2), **kw)
        self.addCleanup(lambda: (s.halt.set(), s.beenden(2)))
        return s

    def test_alle_teile_laufen_und_enden_auf_befehl(self):
        laeuft = {"a": False, "b": False}

        def teil_lauf(name):
            def lauf(halt):
                laeuft[name] = True
                halt.wait()
                laeuft[name] = False
            return lauf
        s = self.start_bauen([start.Teil("A", teil_lauf("a")), start.Teil("B", teil_lauf("b"))])
        s.starten()
        self.assertTrue(warte_bis(lambda: all(laeuft.values())))
        self.assertEqual({t.status for t in s.teile}, {"laeuft"})
        self.assertEqual(s.beenden(5), [])
        self.assertFalse(any(laeuft.values()))
        self.assertEqual({t.status for t in s.teile}, {"gestoppt"})

    def test_abgestuerzter_teil_reisst_die_anderen_nicht_mit_und_wird_neu_gestartet(self):
        anlaeufe = []
        andere = threading.Event()

        def wackelig(halt):
            anlaeufe.append(time.monotonic())
            if len(anlaeufe) < 3:
                raise RuntimeError("Kursquelle weg")
            halt.wait()

        def stabil(halt):
            andere.set()
            halt.wait()
        s = self.start_bauen([start.Teil("Wackler", wackelig), start.Teil("Stabil", stabil)])
        s.starten()
        self.assertTrue(warte_bis(lambda: len(anlaeufe) >= 3))
        self.assertTrue(andere.is_set())
        self.assertEqual(s.teile[1].status, "laeuft")
        self.assertEqual(s.teile[1].neustarts, 0)
        self.assertEqual(s.teile[0].neustarts, 2)
        text = "\n".join(self.meldungen)
        self.assertIn("Wackler ist ausgefallen (RuntimeError: Kursquelle weg)", text)
        self.assertIn("Neustart Nr. 2", text)
        self.assertIn("die anderen Teile laufen weiter", text)
        # Journal bei jedem Absturz, Telegram nur zu Beginn einer Pannenserie (1., 3., 10., 20. ...)
        self.assertEqual([a for a, _ in self.journal].count("teil_absturz"), 2)
        self.assertEqual(len(self.telegram), 1)
        self.assertIn("Wackler ist ausgefallen", self.telegram[0])

    def test_teil_der_sich_von_selbst_beendet_wird_neu_gestartet(self):
        anlaeufe = []

        def kurz(halt):
            anlaeufe.append(1)
            if len(anlaeufe) >= 2:
                halt.wait()
        s = self.start_bauen([start.Teil("Kurz", kurz)])
        s.starten()
        self.assertTrue(warte_bis(lambda: len(anlaeufe) >= 2))
        self.assertIn("hat sich von selbst beendet", "\n".join(self.meldungen))

    def test_dauerhafter_fehler_wird_nicht_neu_gestartet(self):
        anlaeufe = []

        def falsch(halt):
            anlaeufe.append(1)
            raise start.TeilEnde("Keine Symbole eingestellt")
        s = self.start_bauen([start.Teil("Autopilot", falsch), start.Teil("Rest", dauerlauf)])
        s.starten()
        self.assertTrue(warte_bis(lambda: s.teile[0].status == "aufgegeben"))
        time.sleep(0.2)
        self.assertEqual(len(anlaeufe), 1)
        self.assertEqual(s.teile[1].status, "laeuft")
        self.assertIn("Autopilot läuft nicht: Keine Symbole eingestellt", "\n".join(self.meldungen))
        self.assertEqual(len(self.telegram), 1)

    def test_abbruch_des_autopiloten_ist_dauerhaft(self):
        def verweigert(halt):
            raise Abbruch("ECHTGELD VERWEIGERT")
        s = self.start_bauen([start.Teil("Autopilot", verweigert)])
        s.starten()
        self.assertTrue(warte_bis(lambda: s.teile[0].status == "aufgegeben"))
        self.assertEqual(s.teile[0].neustarts, 0)

    def test_pausen_wachsen_bis_zum_hoechstwert(self):
        s = start.Start(mit_standard({}), self.ordner, [], basis_pause_s=10, max_pause_s=300)
        self.assertEqual([s.pause(n) for n in range(1, 8)], [10, 20, 40, 80, 160, 300, 300])

    def test_lange_gelaufener_teil_beginnt_die_pausenfolge_neu(self):
        zeiten = iter([0, 1000, 1000, 1001, 1001, 1002, 1002, 1003, 1003, 1004] + [1004] * 50)
        anlaeufe = []

        def lauf(halt):
            anlaeufe.append(1)
            if len(anlaeufe) >= 3:
                halt.wait()
            raise RuntimeError("x")
        s = self.start_bauen([start.Teil("T", lauf)], uhr=lambda: next(zeiten), basis_pause_s=0.01, stabil_s=500)
        s.starten()
        self.assertTrue(warte_bis(lambda: len(anlaeufe) >= 3))
        self.assertIn("Neustart in 0 s", "\n".join(self.meldungen))

    def test_haengender_teil_wird_gemeldet_und_haelt_das_beenden_nicht_auf(self):
        freigabe = threading.Event()

        def haengt(halt):
            freigabe.wait(10)                                   # hört nicht auf `halt`
        s = self.start_bauen([start.Teil("Haenger", haengt, frist_s=0.2), start.Teil("Brav", dauerlauf)])
        s.starten()
        self.assertTrue(warte_bis(lambda: s.teile[0].status == "laeuft"))
        beginn = time.monotonic()
        self.assertEqual(s.beenden(5), ["Haenger"])
        self.assertLess(time.monotonic() - beginn, 2)
        freigabe.set()

    def test_anhalten_weckt_einen_teil_der_nicht_auf_halt_hoert(self):
        wecker = threading.Event()
        s = self.start_bauen([start.Teil("Server", lambda halt: wecker.wait(10), anhalten=wecker.set)])
        s.starten()
        self.assertTrue(warte_bis(lambda: s.teile[0].status == "laeuft"))
        self.assertEqual(s.beenden(5), [])

    def test_fehler_im_journal_oder_telegram_stoert_nicht(self):
        s = start.Start(mit_standard({}), self.ordner, [], ausgabe=lambda t: None,
                        telegram=mock.Mock(side_effect=OSError("kein Netz")), journal=mock.Mock(side_effect=OSError("voll")))
        with self.assertRaises(OSError):                        # ein kaputtes Journal-Hilfsmittel wird nicht verschluckt ...
            s.melden("x")
        s2 = start.Start(mit_standard({}), self.ordner, [], ausgabe=lambda t: None, telegram=mock.Mock(side_effect=OSError("kein Netz")),
                         journal=lambda a, t: None)
        with self.assertLogs("kitrader.start", "WARNING"):
            s2.melden("x", telegram=True)                       # ... Telegram dagegen schon


# ---------------------------------------------------------------- Autopilot als Teil (Attrappe)

class FalscherAutopilot:
    def __init__(self, ende=None):
        self.beobachter = []
        self.strategie = SimpleNamespace(name="x")
        self.journal = mock.Mock()
        self.broker = mock.Mock()
        self.warten = None
        self.ende = ende
        self.durchlaeufe = 0

    def laufen(self):
        while self.warten(0.01):                                # wie der echte Autopilot: wartet über `warten` und hört auf Halt
            self.durchlaeufe += 1
            if self.ende:
                for b in self.beobachter:
                    b("ende", self.ende)
                return
        for b in self.beobachter:
            b("ende", {"grund": "gestoppt", "text": ""})


class AutopilotTeilTest(Ordner):
    def teil(self, ap):
        einst = mit_standard({})
        self.aufrufe = []

        def bauer(modus, e, ordner, symbole, ausgabe=print, strategie=None):
            self.aufrufe.append((modus, symbole, strategie))
            return ap
        self.patch = mock.patch.object(start._telegram, "beobachten", return_value=None)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        return start.autopilot_teil(einst, self.ordner, "papier", None, "donchian", bauer=bauer)

    def test_laeuft_bis_halt_und_schliesst_alles(self):
        ap = FalscherAutopilot()
        teil = self.teil(ap)
        halt = threading.Event()
        faden = threading.Thread(target=teil.lauf, args=(halt,))
        faden.start()
        self.assertTrue(warte_bis(lambda: ap.warten is not None))
        time.sleep(0.1)
        halt.set()
        faden.join(5)
        self.assertFalse(faden.is_alive())
        self.assertEqual(self.aufrufe, [("papier", None, "donchian")])
        ap.journal.beenden.assert_called_once()
        ap.broker.beenden.assert_called_once()

    def test_abbruch_des_autopiloten_ist_dauerhaft(self):
        ap = FalscherAutopilot({"grund": "abbruch", "text": "Keine Symbole eingestellt"})
        teil = self.teil(ap)
        with self.assertRaises(start.TeilEnde) as f:
            teil.lauf(threading.Event())
        self.assertIn("Keine Symbole", str(f.exception))
        ap.journal.beenden.assert_called_once()

    def test_zu_viele_fehler_in_folge_fuehren_zum_neustart(self):
        ap = FalscherAutopilot({"grund": "fehler_folge", "text": "nach 5 Fehlern in Folge abgebrochen"})
        teil = self.teil(ap)
        with self.assertRaises(RuntimeError) as f:
            teil.lauf(threading.Event())
        self.assertNotIsInstance(f.exception, start.TeilEnde)
        self.assertIn("5 Fehlern", str(f.exception))

    def test_absturz_im_autopiloten_wird_weitergereicht_und_aufgeraeumt(self):
        ap = FalscherAutopilot()
        ap.laufen = mock.Mock(side_effect=ValueError("kaputt"))
        teil = self.teil(ap)
        with self.assertRaises(ValueError):
            teil.lauf(threading.Event())
        ap.journal.beenden.assert_called_once()
        ap.broker.beenden.assert_called_once()

    def test_abbruch_beim_bauen_ist_dauerhaft_fuer_die_aufsicht(self):
        def bauer(*a, **k):
            raise Abbruch("ECHTGELD VERWEIGERT")
        teil = start.autopilot_teil(mit_standard({}), self.ordner, "echt", bauer=bauer)
        s = start.Start(mit_standard({}), self.ordner, [teil], ausgabe=lambda t: None, telegram=lambda t: None, journal=lambda a, t: None)
        s.starten()
        self.addCleanup(s.beenden, 2)
        self.assertTrue(warte_bis(lambda: teil.status == "aufgegeben"))
        self.assertEqual(teil.neustarts, 0)


# ---------------------------------------------------------------- Oberfläche als Teil

class OberflaecheTeilTest(Ordner):
    def test_laeuft_antwortet_und_wird_beendet(self):
        ob = start.OberflaecheTeil(mit_standard({}), self.ordner, port=0, ausweichen=False)
        teil = ob.teil()
        s = start.Start(mit_standard({}), self.ordner, [teil], ausgabe=lambda t: None, telegram=lambda t: None, journal=lambda a, t: None)
        s.starten()
        self.assertTrue(warte_bis(lambda: ob._laeuft.is_set()))
        with urllib.request.urlopen(ob.adresse, timeout=5) as a:
            self.assertEqual(a.status, 200)
            self.assertIn(b"kitrader", a.read())
        self.assertEqual(s.beenden(10), [])
        with self.assertRaises(OSError):
            urllib.request.urlopen(ob.adresse, timeout=2)

    def test_belegter_port_nimmt_den_naechsten_oder_bricht_klar_ab(self):
        sperrer = socket.socket()
        sperrer.bind(("127.0.0.1", 0))
        sperrer.listen()
        self.addCleanup(sperrer.close)
        port = sperrer.getsockname()[1]
        einst = mit_standard({})
        app, server = start._server_bauen(einst, self.ordner, port, ausweichen=True)
        server.server_close()
        self.assertIn(app.port, range(port + 1, port + 10))
        with self.assertRaises(Abbruch) as f:
            start._server_bauen(einst, self.ordner, port, ausweichen=False)
        self.assertIn(f"Port {port}", str(f.exception))


# ---------------------------------------------------------------- Telegram-Wache als Teil

class WacheTeilTest(Ordner):
    def test_ohne_telegram_kein_teil(self):
        self.assertIsNone(start.wache_teil(mit_standard({"telegram": {"aktiv": False}}), self.ordner, "papier"))
        with mock.patch.object(start._telegram, "zugang_lesen", return_value=SimpleNamespace(bereit=False)):
            self.assertIsNone(start.wache_teil(mit_standard({}), self.ordner, "papier"))

    def test_mit_client_laeuft_die_wache_und_endet_auf_halt(self):
        client = mock.Mock()
        client.aktualisierungen.return_value = []
        teil = start.wache_teil(mit_standard({"telegram": {"poll_s": 1}}), self.ordner, "papier", client=client, chat_id="42")
        self.assertEqual(teil.name, "Telegram-Wache")
        halt = threading.Event()
        faden = threading.Thread(target=teil.lauf, args=(halt,))
        faden.start()
        self.assertTrue(warte_bis(lambda: client.aktualisierungen.called))
        halt.set()
        faden.join(5)
        self.assertFalse(faden.is_alive())


# ---------------------------------------------------------------- Voreinstellungen

class VorgabenTest(unittest.TestCase):
    def test_ohne_eigene_einstellungen_laeuft_der_papierbetrieb_mit_kraken(self):
        e, notizen = start.vorgaben_anwenden(mit_standard({}), {})
        self.assertEqual(e["kursquelle"], "kraken")
        self.assertEqual(e["autopilot"]["symbole"], ["BTC/EUR", "ETH/EUR"])
        self.assertEqual(e["maerkte"], {"BTC/EUR": "krypto", "ETH/EUR": "krypto"})
        self.assertEqual(e["mengenschritte"], {"BTC/EUR": 0.0001, "ETH/EUR": 0.001})
        self.assertEqual(e["kosten"]["gebuehr_prozent"], 0.80)
        self.assertEqual(e["kosten"]["gebuehr_fest"], 0.0)
        self.assertEqual(len(notizen), 3)
        self.assertEqual(_einst.pruefen(e), e)

    def test_eigene_werte_haben_vorrang(self):
        roh = {"kursquelle": "ibkr", "kosten": {"gebuehr_prozent": 0.1}}
        basis = mit_standard({**roh, "autopilot": {"symbole": ["AAPL"]}})
        e, notizen = start.vorgaben_anwenden(basis, {**roh, "autopilot": {"symbole": ["AAPL"]}})
        self.assertEqual((e["kursquelle"], e["autopilot"]["symbole"], e["kosten"]["gebuehr_prozent"]), ("ibkr", ["AAPL"], 0.1))
        self.assertEqual((e["maerkte"], e["mengenschritte"], notizen), ({}, {}, []))

    def test_eigene_maerkte_und_mengenschritte_bleiben(self):
        basis = mit_standard({"maerkte": {"xbteur": "us"}, "mengenschritte": {"btc/eur": 0.5}, "kursquelle": "kraken"})
        e, _ = start.vorgaben_anwenden(basis, {"kursquelle": "kraken", "maerkte": {"xbteur": "us"}})
        self.assertEqual(e["maerkte"]["xbteur"], "us")
        self.assertEqual(e["mengenschritte"]["btc/eur"], 0.5)
        self.assertNotIn("BTC/EUR", e["mengenschritte"])

    def test_symbole_von_der_kommandozeile(self):
        e, _ = start.vorgaben_anwenden(mit_standard({}), {}, ["SOL/EUR"])
        self.assertEqual(e["autopilot"]["symbole"], ["SOL/EUR"])
        self.assertEqual(e["maerkte"], {"SOL/EUR": "krypto"})
        self.assertEqual(e["mengenschritte"], {"SOL/EUR": 0.01})

    def test_original_bleibt_unveraendert(self):
        basis = mit_standard({})
        start.vorgaben_anwenden(basis, {})
        self.assertEqual(basis["autopilot"]["symbole"], [])
        self.assertEqual(basis["kursquelle"], "ibkr")


# ---------------------------------------------------------------- Zeitplan mit gestellter Uhr

class UhrStellen:
    def __init__(self, start_zeit):
        self.jetzt = start_zeit

    def __call__(self):
        return self.jetzt

    def weiter(self, **kw):
        self.jetzt += timedelta(**kw)


class ZeitplanTest(Ordner):
    def bauen(self, beginn, osiris_stand=None, morgenlage_fehler=None, nachrichten_stand=None, welt_stand=None, **zeitplan):
        zeitplan.setdefault("nachrichten_alle_h", 0)         # die neuen Aufträge sind nur in den eigenen Tests an
        zeitplan.setdefault("welt_alle_h", 0)
        self.uhr = UhrStellen(beginn)
        self.meldungen: list[tuple[str, dict]] = []
        self.osiris_aufrufe = 0
        self.nachrichten_aufrufe = self.welt_aufrufe = 0
        self.nachrichten_fehler = self.welt_fehler = None
        self.lage_aufrufe: list = []
        self.osiris_fehler = None
        self.lage_fehler = morgenlage_fehler

        def osiris():
            self.osiris_aufrufe += 1
            if self.osiris_fehler:
                raise self.osiris_fehler
            return {"neu": True, "feeds": {"a": {"fehler": ""}, "b": {"fehler": "HTTP 500"}}}

        def nachrichten():
            self.nachrichten_aufrufe += 1
            if self.nachrichten_fehler:
                raise self.nachrichten_fehler
            return {"neu": True, "neue_meldungen": 7, "quellen": {"a": {"fehler": ""}, "b": {"fehler": "HTTP 500"}}}

        def welt():
            self.welt_aufrufe += 1
            if self.welt_fehler:
                raise self.welt_fehler
            return {"neu": True, "zeilen": 30, "quellen": {"OSIRIS /api/markets": "ok, 30 Märkte", "Kraken BTC/EUR": "HTTP 500"}}

        def lage(treiber):
            self.lage_aufrufe.append(treiber)
            fehler = self.lage_fehler(treiber) if callable(self.lage_fehler) else self.lage_fehler
            if fehler:
                raise fehler
            return ({}, self.ordner / "morgenlage.json", False)
        einst = mit_standard({"zeitplan": zeitplan})
        return start.Zeitplan(einst, self.ordner, uhr=self.uhr, melden=lambda text, **kw: self.meldungen.append((text, kw)),
                              osiris_stand=lambda: osiris_stand, osiris_abrufen=osiris, morgenlage=lage,
                              nachrichten_stand=lambda: nachrichten_stand, nachrichten_abrufen=nachrichten,
                              welt_stand=lambda: welt_stand, welt_abrufen=welt)

    def texte(self):
        return "\n".join(t for t, _ in self.meldungen)

    def test_osiris_sofort_ohne_schnappschuss_dann_alle_6_stunden(self):
        z = self.bauen(datetime(2026, 10, 26, 12, 0, tzinfo=UTC), morgenlage="")
        self.assertEqual(z.schritt(), ["osiris"])
        self.assertEqual(z.schritt(), [])
        self.uhr.weiter(hours=5, minutes=59)
        self.assertEqual(z.schritt(), [])
        self.uhr.weiter(minutes=2)
        self.assertEqual(z.schritt(), ["osiris"])
        self.assertEqual(self.osiris_aufrufe, 2)
        self.assertIn("1 mit Fehler: b", self.texte())

    def test_osiris_wartet_nach_einem_frischen_schnappschuss(self):
        jetzt = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
        z = self.bauen(jetzt, osiris_stand=jetzt - timedelta(hours=1), morgenlage="")
        self.assertEqual(z.schritt(), [])
        self.uhr.weiter(hours=5, minutes=1)
        self.assertEqual(z.schritt(), ["osiris"])

    def test_osiris_fehlschlag_wird_gemeldet_und_nach_30_minuten_wiederholt(self):
        z = self.bauen(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
        self.osiris_fehler = OSError("kein Netz")
        z.schritt()
        self.assertIn("OSIRIS-Abruf fehlgeschlagen (OSError: kein Netz)", self.texte())
        self.uhr.weiter(minutes=29)
        z.schritt()
        self.assertEqual(self.osiris_aufrufe, 1)
        self.uhr.weiter(minutes=2)
        self.osiris_fehler = None
        z.schritt()
        self.assertEqual(self.osiris_aufrufe, 2)

    def test_nachrichten_und_welt_alle_2_stunden(self):
        z = self.bauen(datetime(2026, 10, 26, 12, 0, tzinfo=UTC), morgenlage="", osiris_alle_h=0, nachrichten_alle_h=2, welt_alle_h=2)
        self.assertEqual(z.schritt(), ["nachrichten", "welt"])
        self.assertEqual(z.schritt(), [])
        self.uhr.weiter(hours=1, minutes=59)
        self.assertEqual(z.schritt(), [])
        self.uhr.weiter(minutes=2)
        self.assertEqual(z.schritt(), ["nachrichten", "welt"])
        self.assertEqual((self.nachrichten_aufrufe, self.welt_aufrufe), (2, 2))
        self.assertIn("Nachrichten neu abgerufen (7 neue Meldungen aus 2 Feeds, 1 mit Fehler)", self.texte())
        self.assertIn("Welt neuer Schnappschuss (30 Märkte, 1 Quellen mit Fehler: Kraken BTC/EUR)", self.texte())
        self.assertIn("Nachrichten alle 2 Std., Welt alle 2 Std.", z.beschreibung())

    def test_nachrichten_und_welt_fehlschlag_wird_gemeldet_und_nach_30_minuten_wiederholt(self):
        z = self.bauen(datetime(2026, 10, 26, 12, 0, tzinfo=UTC), morgenlage="", osiris_alle_h=0, nachrichten_alle_h=2, welt_alle_h=2)
        self.nachrichten_fehler = OSError("kein Netz")
        z.schritt()
        self.assertIn("Nachrichten-Abruf fehlgeschlagen (OSError: kein Netz)", self.texte())
        self.uhr.weiter(minutes=29)
        z.schritt()
        self.assertEqual((self.nachrichten_aufrufe, self.welt_aufrufe), (1, 1))
        self.uhr.weiter(minutes=2)
        self.nachrichten_fehler = None
        self.assertEqual(z.schritt(), ["nachrichten"])                  # Welt kam sofort durch und wartet noch
        self.assertEqual((self.nachrichten_aufrufe, self.welt_aufrufe), (2, 1))

    def test_nachrichten_und_welt_warten_nach_frischem_stand_und_sind_bei_null_aus(self):
        jetzt = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
        z = self.bauen(jetzt, morgenlage="", osiris_alle_h=0, nachrichten_alle_h=2, welt_alle_h=2,
                       nachrichten_stand=jetzt - timedelta(minutes=30), welt_stand=jetzt - timedelta(hours=3))
        self.assertEqual(z.schritt(), ["welt"])
        self.uhr.weiter(hours=1, minutes=31)
        self.assertEqual(z.schritt(), ["nachrichten"])
        aus = self.bauen(jetzt, morgenlage="", osiris_alle_h=0)
        self.assertEqual(aus.schritt(), [])
        self.assertIn("Nachrichten aus, Welt aus", aus.beschreibung())

    def test_fehlendes_modul_wird_einmal_gemeldet_und_uebersprungen(self):
        z = self.bauen(datetime(2026, 10, 26, 12, 0, tzinfo=UTC), morgenlage="", osiris_alle_h=0, nachrichten_alle_h=2, welt_alle_h=0)
        self.nachrichten_fehler = FileNotFoundError("nachrichten.py ist noch nicht vorhanden")
        z.schritt()
        self.uhr.weiter(hours=3)
        z.schritt()
        self.assertEqual(self.nachrichten_aufrufe, 1)
        self.assertEqual(self.texte().count("Nachrichten übersprungen: nachrichten.py ist noch nicht vorhanden"), 1)

    def test_osiris_aus_bei_null(self):
        z = self.bauen(datetime(2026, 10, 26, 12, 0, tzinfo=UTC), morgenlage="", osiris_alle_h=0)
        self.assertEqual(z.schritt(), [])
        self.assertIn("OSIRIS aus", z.beschreibung())

    def test_morgenlage_zur_berliner_uhrzeit_im_winter(self):
        # 26.10.2026: MEZ = UTC+1, also 08:00 Berlin = 07:00 UTC
        z = self.bauen(datetime(2026, 10, 26, 6, 59, tzinfo=UTC), osiris_alle_h=0)
        self.assertEqual(z.schritt(), [])
        self.uhr.weiter(minutes=1)
        self.assertEqual(z.schritt(), ["morgenlage"])
        self.assertEqual(self.lage_aufrufe, [None])
        self.assertIn("Morgenlage fertig", self.texte())
        self.uhr.weiter(hours=3)
        self.assertEqual(z.schritt(), [])                              # heute schon erledigt
        self.uhr.weiter(hours=20)                                      # 27.10. 06:00 UTC
        self.assertEqual(z.schritt(), [])
        self.uhr.weiter(hours=1)
        self.assertEqual(z.schritt(), ["morgenlage"])
        self.assertEqual(len(self.lage_aufrufe), 2)

    def test_morgenlage_zur_berliner_uhrzeit_im_sommer(self):
        # 1.7.2026: MESZ = UTC+2, also 08:00 Berlin = 06:00 UTC
        z = self.bauen(datetime(2026, 7, 1, 5, 59, tzinfo=UTC), osiris_alle_h=0)
        self.assertEqual(z.schritt(), [])
        self.uhr.weiter(minutes=1)
        self.assertEqual(z.schritt(), ["morgenlage"])

    def test_eigene_uhrzeit_und_zeitzone(self):
        z = self.bauen(datetime(2026, 10, 26, 13, 29, tzinfo=UTC), osiris_alle_h=0, morgenlage="09:30", zeitzone="America/New_York")
        self.assertEqual(z.schritt(), [])                              # 09:29 New York (EDT bis 1.11.: UTC-4)
        self.uhr.weiter(minutes=1)
        self.assertEqual(z.schritt(), ["morgenlage"])

    def test_nach_der_uhrzeit_gestartet_holt_die_morgenlage_nach_ausser_sie_ist_schon_da(self):
        z = self.bauen(datetime(2026, 10, 26, 15, 0, tzinfo=UTC), osiris_alle_h=0)
        self.assertEqual(z.schritt(), ["morgenlage"])
        # eine heute nach 08:00 Berlin geschriebene Datei zählt als erledigt
        datei = self.ordner / "morgenlage.json"
        datei.write_text("{}", encoding="utf-8")
        z2 = self.bauen(datetime.now(UTC), osiris_alle_h=0, morgenlage="00:00")
        z2.morgenlage_datei = datei
        self.assertEqual(z2.schritt(), [])
        self.assertEqual(self.lage_aufrufe, [])

    def test_ohne_lage_py_wird_einmal_gemeldet_und_nicht_wiederholt(self):
        z = self.bauen(datetime(2026, 10, 26, 8, 0, tzinfo=UTC), osiris_alle_h=0, morgenlage_fehler=FileNotFoundError("lage.py ist noch nicht vorhanden"))
        z.schritt()
        self.assertIn("Morgenlage übersprungen: lage.py ist noch nicht vorhanden", self.texte())
        self.uhr.weiter(hours=2)
        z.schritt()
        self.assertEqual(len(self.lage_aufrufe), 1)
        self.assertEqual(self.texte().count("übersprungen"), 1)

    def test_ki_nicht_erreichbar_dann_morgenlage_ohne_ki(self):
        z = self.bauen(datetime(2026, 10, 26, 8, 0, tzinfo=UTC), osiris_alle_h=0,
                       morgenlage_fehler=lambda treiber: _ki.KiFehler("Claude-CLI fehlt") if treiber is None else None)
        z.schritt()
        self.assertEqual(self.lage_aufrufe, [None, "aus"])
        self.assertIn("ohne KI-Einschätzung", self.texte())
        self.assertIn("Morgenlage fertig", self.texte())

    def test_fehlschlag_wird_dreimal_im_30_Minuten_Abstand_versucht(self):
        z = self.bauen(datetime(2026, 10, 26, 8, 0, tzinfo=UTC), osiris_alle_h=0, morgenlage_fehler=RuntimeError("kaputt"))
        for _ in range(8):
            z.schritt()
            self.uhr.weiter(minutes=31)
        self.assertEqual(len(self.lage_aufrufe), 3)
        self.assertIn("Für heute aufgegeben", self.texte())
        letzte = [kw for t, kw in self.meldungen if "aufgegeben" in t][0]
        self.assertTrue(letzte["telegram"])                            # erst die letzte Panne geht aufs Handy

    def test_morgenlage_aus_bei_leerer_uhrzeit(self):
        z = self.bauen(datetime(2026, 10, 26, 8, 0, tzinfo=UTC), osiris_alle_h=0, morgenlage="")
        self.assertEqual(z.schritt(), [])
        self.assertIn("Morgenlage aus", z.beschreibung())

    def test_unbekannte_zeitzone_faellt_auf_ortszeit_zurueck(self):
        z = self.bauen(datetime(2026, 10, 26, 8, 0, tzinfo=UTC), zeitzone="Mars/Olympus")
        self.assertIn("nicht gefunden", z.zone_text)

    def test_einstellungen_werden_geprueft(self):
        for falsch in ({"morgenlage": "8 Uhr"}, {"morgenlage": "25:00"}, {"osiris_alle_h": -1}, {"osiris_alle_h": True},
                       {"nachrichten_alle_h": -1}, {"welt_alle_h": True}, {"welt_alle_h": 200}):
            with self.assertRaises(_einst.EinstellungsFehler, msg=falsch):
                _einst.pruefen(mit_standard({"zeitplan": falsch}))
        for gut in ({"morgenlage": ""}, {"morgenlage": "07:05"}, {"osiris_alle_h": 0}, {"osiris_alle_h": 0.5},
                    {"nachrichten_alle_h": 0}, {"welt_alle_h": 2}):
            _einst.pruefen(mit_standard({"zeitplan": gut}))

    def test_lauf_endet_auf_halt(self):
        z = self.bauen(datetime(2026, 10, 26, 12, 0, tzinfo=UTC), morgenlage="")
        halt = threading.Event()
        faden = threading.Thread(target=z.lauf, args=(halt,))
        faden.start()
        self.assertTrue(warte_bis(lambda: self.osiris_aufrufe == 1))
        halt.set()
        faden.join(5)
        self.assertFalse(faden.is_alive())


# ---------------------------------------------------------------- Startausgabe und Befehl

class AusgabeTest(unittest.TestCase):
    def test_grosse_betriebsart(self):
        for wort in ("PAPIER", "DEMO", "ECHT"):
            zeilen = start.gross_schreiben(wort).split("\n")
            self.assertEqual(len(zeilen), 5)
            self.assertGreater(max(len(z) for z in zeilen), 20)
        self.assertIn("kein echtes Geld", start.betriebsart_block("papier"))
        self.assertIn("ECHTES GELD", start.betriebsart_block("echt"))
        self.assertIn("!!!!", start.betriebsart_block("echt"))

    def test_startausgabe_nennt_adresse_laufendes_und_fehlendes(self):
        e = mit_standard({"autopilot": {"symbole": ["BTC/EUR"]}})
        text = start.startausgabe("papier", e, Path("/daten"), "http://127.0.0.1:8850/",
                                  [start.Teil("Autopilot", dauerlauf, beschreibung="Papierkonto"), start.Teil("Zeitplan", dauerlauf)],
                                  ["Telegram ist nicht eingerichtet"], ["Kursquelle Kraken"])
        for erwartet in ("BETRIEBSART PAPIER", "http://127.0.0.1:8850/", "Läuft", "Autopilot", "Zeitplan", "Fehlt", "Telegram ist nicht eingerichtet",
                         "Vorgaben", "Kursquelle Kraken", "/daten", "Strg+C", "Not-Aus", "BTC/EUR"):
            self.assertIn(erwartet, text)

    def test_fehlendes_nennt_telegram_und_claude(self):
        e = mit_standard({"ki": {"claude_befehl": "gibt-es-nicht-xyz"}})
        with mock.patch.object(start._telegram, "zugang_lesen", return_value=SimpleNamespace(bereit=False)):
            fehlt = "\n".join(start.fehlendes(e, "papier", lambda: "kraken ist nicht erreichbar (kein Netz)"))
        self.assertIn("telegram einrichten", fehlt)
        self.assertIn("gibt-es-nicht-xyz", fehlt)
        self.assertIn("Kurse: kraken ist nicht erreichbar", fehlt)


class BefehlTest(Ordner):
    def args(self, **kw):
        return SimpleNamespace(**{"modus": None, "port": None, "nicht_oeffnen": True, "ohne_oberflaeche": True, "symbole": None,
                                  "strategie": None, "beenden": False, **kw})

    def test_echtgeld_ohne_einstellung_und_freigabe_startet_gar_nichts(self):
        ausgabe = []
        with self.assertRaises(Abbruch) as f:
            start.befehl(self.args(modus="echt"), mit_standard({}), self.ordner, ausgabe.append, halt=threading.Event())
        self.assertIn("ECHTGELD VERWEIGERT", str(f.exception))
        self.assertEqual(ausgabe, [])
        self.assertIsNone(start.Sperre.belegt_von(self.ordner))
        # Einstellung vorhanden, aber ohne Freigabedatei: ebenfalls verweigert
        with self.assertRaises(Abbruch) as f:
            start.befehl(self.args(modus="echt"), mit_standard({"modus": "echt"}), self.ordner, ausgabe.append, halt=threading.Event())
        self.assertIn("Freigabedatei", str(f.exception))

    def test_unbekannte_betriebsart(self):
        with self.assertRaises(Abbruch):
            start.befehl(self.args(modus="boerse"), mit_standard({}), self.ordner, lambda t: None, halt=threading.Event())

    def test_start_mit_attrappen_ausgabe_sperre_und_ende(self):
        zeilen: list[str] = []
        laeuft = threading.Event()

        def teile(e, ordner, s):
            def lauf(halt):
                laeuft.set()
                halt.wait()
            return [start.Teil("Autopilot", lauf, beschreibung="Papierkonto"), start.Teil("Zeitplan", dauerlauf)]
        halt = threading.Event()
        ergebnis = []
        with mock.patch.object(start._telegram, "zugang_lesen", return_value=SimpleNamespace(bereit=False)), \
                mock.patch.object(start, "roh_einstellungen", return_value={}), mock.patch.object(start, "quelle_testen", return_value=None):
            faden = threading.Thread(target=lambda: ergebnis.append(start.befehl(
                self.args(), mit_standard({}), self.ordner, zeilen.append, halt=halt, teile_bauer=teile)))
            faden.start()
            self.assertTrue(laeuft.wait(5))
            info = start.Sperre.belegt_von(self.ordner)
            self.assertEqual((info["pid"], info["modus"]), (os.getpid(), "papier"))
            with self.assertRaises(Abbruch):                           # zweiter Start im selben Ordner
                start.befehl(self.args(), mit_standard({}), self.ordner, lambda t: None, halt=threading.Event(), teile_bauer=teile)
            halt.set()
            faden.join(10)
        self.assertEqual(ergebnis, [0])
        self.assertIsNone(start.Sperre.belegt_von(self.ordner))
        text = "\n".join(zeilen)
        for erwartet in ("BETRIEBSART PAPIER", "Läuft", "Autopilot", "Zeitplan", "Fehlt", "Telegram ist nicht eingerichtet", "Strg+C",
                         "kitrader ist beendet"):
            self.assertIn(erwartet, text)
        journal = (self.ordner / "journal.db")
        self.assertTrue(journal.is_file())                              # Start und Ende stehen im Journal

    def test_modus_wird_fuer_oberflaeche_und_notaus_gesetzt(self):
        gesehen = {}

        def teile(e, ordner, s):
            gesehen["modus"] = e["modus"]
            gesehen["kursquelle"] = e["kursquelle"]
            gesehen["symbole"] = list(e["autopilot"]["symbole"])
            return [start.Teil("T", dauerlauf)]
        halt = threading.Event()
        with mock.patch.object(start._telegram, "zugang_lesen", return_value=SimpleNamespace(bereit=False)), \
                mock.patch.object(start, "roh_einstellungen", return_value={}), mock.patch.object(start, "quelle_testen", return_value=None):
            faden = threading.Thread(target=start.befehl, args=(self.args(modus="demo", symbole=["AAPL"]), mit_standard({}), self.ordner,
                                                              lambda t: None, halt, teile))
            faden.start()
            self.assertTrue(warte_bis(lambda: "modus" in gesehen))
            halt.set()
            faden.join(10)
        self.assertEqual(gesehen, {"modus": "demo", "kursquelle": "ibkr", "symbole": ["AAPL"]})      # Vorgaben nur im Papierbetrieb


# ---------------------------------------------------------------- Ein echter Prozess mit Strg+C

class ProzessTest(Ordner):
    """Startet `python3 -m kitrader start` wirklich: Der Autopilot kann hier nicht laufen (Kursquelle ohne Daten) und
    stürzt ab, die Oberfläche läuft weiter; Strg+C (SIGINT) beendet alles sauber, SIGTERM (`--beenden`) ebenso."""

    def starten(self, symbole='["ABC"]'):
        konfig = self.ordner / "e.toml"
        konfig.write_text(f'kursquelle = "wiedergabe"\n[autopilot]\nsymbole = {symbole}\n[telegram]\naktiv = false\n'
                          '[zeitplan]\nmorgenlage = ""\nosiris_alle_h = 0\nnachrichten_alle_h = 0\nwelt_alle_h = 0\n[oberflaeche]\nbrowser_oeffnen = false\n', encoding="utf-8")
        markt = self.ordner / "markt"
        markt.mkdir()
        (markt / "konfig").mkdir()
        (markt / "konfig" / "einstellungen.toml").write_text('eingerichtet = "2026-10-26T09:00:00Z"\n', encoding="utf-8")
        # Das Dashboard läuft im Demo-Betrieb mit eigenem Datenordner: keine Abrufe im Netz, nichts im Heimordner
        umgebung = {**os.environ, "KITRADER_DATEN": str(self.ordner), "KITRADER_KONFIG": str(konfig), "PYTHONPATH": SRC, "NO_COLOR": "1",
                    "KITRADER_MARKT_DEMO": "1", "KITRADER_MARKT_DATEN": str(markt), "KITRADER_MARKT_KONFIG": str(markt / "konfig")}
        p = subprocess.Popen([sys.executable, "-m", "kitrader", "start", "--port", "0", "--nicht-oeffnen", "--ohne-hilfsdienste"], env=umgebung,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(self.ordner))
        self.addCleanup(lambda: p.poll() is None and p.kill())
        self.assertTrue(warte_bis(lambda: (self.ordner / start.SPERRDATEI).is_file() and (self.ordner / start.SPERRDATEI).stat().st_size > 20, 20))
        info = {}
        self.assertTrue(warte_bis(lambda: info.update(json.loads((self.ordner / start.SPERRDATEI).read_text() or "{}")) or info.get("adresse"), 20))
        return p, info, umgebung

    def absturz_im_journal(self) -> bool:
        try:
            db = sqlite3.connect(str(self.ordner / "journal.db"))
            try:
                return db.execute("SELECT COUNT(*) FROM ereignisse WHERE art='teil_absturz'").fetchone()[0] > 0
            finally:
                db.close()
        except sqlite3.Error:
            return False

    def test_abgestuerzter_autopilot_oberflaeche_laeuft_weiter_und_strg_c_beendet_alles(self):
        p, info, _ = self.starten()
        self.assertEqual(info["pid"], p.pid)
        self.assertTrue(warte_bis(self.absturz_im_journal, 20))
        with urllib.request.urlopen(info["adresse"], timeout=10) as a:
            self.assertEqual(a.status, 200)
        p.send_signal(signal.SIGINT)
        ausgabe, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0, ausgabe)
        for erwartet in ("BETRIEBSART PAPIER", info["adresse"], "Autopilot ist ausgefallen", "die anderen Teile laufen weiter",
                         "kitrader ist beendet"):
            self.assertIn(erwartet, ausgabe)
        self.assertIsNone(start.Sperre.belegt_von(self.ordner))
        with self.assertRaises(OSError):
            urllib.request.urlopen(info["adresse"], timeout=2)

    def test_dashboard_ist_die_hauptoberflaeche_mit_den_handelsteilen(self):
        p, info, _ = self.starten()
        basis = info["adresse"].rstrip("/")
        with urllib.request.urlopen(basis + "/api/k/info", timeout=10) as a:
            i = json.loads(a.read())
        self.assertEqual((i["modus"], i["echtes_geld"], i["autopilot_steuerbar"]), ("papier", False, True))
        with urllib.request.urlopen(basis + "/api/k/autopilot", timeout=20) as a:
            ap = json.loads(a.read())
        self.assertIn(ap["status"], ("laeuft", "pause", "wartet"))               # läuft mit dem Start; stürzt hier ab (Kursquelle ohne Daten)
        self.assertEqual(ap["uebersicht"]["modus"], "papier")
        with urllib.request.urlopen(basis + "/", timeout=10) as a:
            seite = a.read().decode("utf-8")
        for reiter in ('data-ziel="trends"', 'data-ziel="ki"', 'data-ziel="weltlage"', 'data-ziel="autopilot"'):
            self.assertIn(reiter, seite)
        # Schreiben nur mit Kopf X-Kitrader und JSON
        anfrage = urllib.request.Request(basis + "/api/k/notaus", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as f:
            urllib.request.urlopen(anfrage, timeout=10)
        self.assertEqual(f.exception.code, 403)
        fremd = urllib.request.Request(basis + "/api/k/notaus", data=b"{}", method="POST",
                                       headers={"Content-Type": "application/json", "X-Kitrader": "1", "Origin": "http://boese.example"})
        with self.assertRaises(urllib.error.HTTPError) as f:
            urllib.request.urlopen(fremd, timeout=10)
        self.assertEqual(f.exception.code, 403)
        self.assertFalse((self.ordner / "NOTAUS").exists())
        # Stopp und Start des Autopiloten im Papierbetrieb
        def post(pfad):
            r = urllib.request.Request(basis + pfad, data=b"{}", method="POST", headers={"Content-Type": "application/json", "X-Kitrader": "1"})
            with urllib.request.urlopen(r, timeout=30) as a:
                return json.loads(a.read())
        self.assertFalse(post("/api/k/autopilot/stopp")["laeuft"])
        self.assertEqual(post("/api/k/autopilot/stopp")["hinweis"], "Der Autopilot lief nicht.")
        self.assertTrue(post("/api/k/autopilot/start")["gestartet"])
        p.send_signal(signal.SIGINT)
        ausgabe, _ = p.communicate(timeout=40)
        self.assertEqual(p.returncode, 0, ausgabe)

    def test_start_beenden_per_sigterm(self):
        p, info, umgebung = self.starten()
        r = subprocess.run([sys.executable, "-m", "kitrader", "start", "--beenden"], env=umgebung, capture_output=True, text=True, timeout=60,
                           cwd=str(self.ordner))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("beendet", r.stdout)
        ausgabe, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0, ausgabe)
        self.assertIn("kitrader ist beendet", ausgabe)

    def test_ohne_symbole_laeuft_nur_der_rest_und_die_ausgabe_sagt_es(self):
        p, info, _ = self.starten(symbole="[]")
        with urllib.request.urlopen(info["adresse"], timeout=10) as a:
            self.assertEqual(a.status, 200)
        p.send_signal(signal.SIGINT)
        ausgabe, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0, ausgabe)
        self.assertIn("Autopilot: keine Symbole eingestellt", ausgabe)
        self.assertNotIn("ausgefallen", ausgabe)

    def test_zweiter_prozess_wird_verweigert(self):
        p, info, umgebung = self.starten()
        r = subprocess.run([sys.executable, "-m", "kitrader", "start", "--nicht-oeffnen"], env=umgebung, capture_output=True, text=True,
                           timeout=60, cwd=str(self.ordner))
        self.assertEqual(r.returncode, 2)
        self.assertIn("läuft schon", r.stderr)
        p.send_signal(signal.SIGINT)
        p.communicate(timeout=30)


if __name__ == "__main__":
    unittest.main()
