"""Telegram: nachgebildeter Telegram-Server. Nur die erlaubte Chat-ID löst den Not-Aus aus, Meldungen des Autopiloten,
Einrichten, und: der Token taucht nirgends in Ausgaben auf (Logs, stdout, stderr, Fehlertexte). Alles offline."""
import contextlib
import io
import json
import logging
import os
import re
import stat
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from kitrader import betrieb, telegram
from kitrader.__main__ import main
from kitrader.autopilot import Abbruch, Autopilot
from kitrader.broker.basis import Kerze, Order
from kitrader.broker.papier import PapierBroker
from kitrader.einstellungen import mit_standard
from kitrader.journal import Journal
from kitrader.kursquelle import WiedergabeQuelle
from kitrader.strategie import Signal, Strategie

from .daten import UTC, Markt, Uhr

START = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)         # ein Montag
CHAT = 4711
TOKEN = "123456789:AAFakeTokenNurFuerDenTest_abcdefghijk"


class FakeTelegram:
    """Kleiner Telegram-Server: getMe, getUpdates (mit offset), sendMessage; merkt sich alles. Fehler einstellbar."""

    def __init__(self, token=TOKEN):
        self.token = token
        self.updates: list[dict] = []
        self.gesendet: list[dict] = []
        self.anfragen: list[tuple[str, dict]] = []
        self.fehler_status: int | None = None          # alle Methoden antworten damit
        self.letzter_offset = 0
        self._id = 100
        self._sperre = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def antwort(self, status, daten):
                body = json.dumps(daten).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                daten = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                m = re.match(r"^/bot([^/]+)/(\w+)$", self.path)
                with fake._sperre:
                    fake.anfragen.append((self.path, daten))
                if not m or m.group(1) != fake.token:
                    return self.antwort(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
                methode = m.group(2)
                if fake.fehler_status:
                    # absichtlich mit der Adresse (und damit dem Token) in der Beschreibung
                    return self.antwort(fake.fehler_status, {"ok": False, "error_code": fake.fehler_status,
                                                             "description": f"Fehler bei {self.path}"})
                if methode == "getMe":
                    return self.antwort(200, {"ok": True, "result": {"id": 1, "is_bot": True, "username": "kitrader_test_bot"}})
                if methode == "sendMessage":
                    with fake._sperre:
                        fake.gesendet.append(daten)
                    return self.antwort(200, {"ok": True, "result": {"message_id": len(fake.gesendet)}})
                if methode == "getUpdates":
                    offset = daten.get("offset") or 0
                    with fake._sperre:
                        fake.letzter_offset = max(fake.letzter_offset, offset)
                        neu = [u for u in fake.updates if u["update_id"] >= offset]
                    if not neu:
                        time.sleep(min(daten.get("timeout", 0), 0.05))
                    return self.antwort(200, {"ok": True, "result": neu})
                return self.antwort(404, {"ok": False, "error_code": 404, "description": f"Not Found: {self.path}"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._faden = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self._faden.start()

    def stoppen(self):
        self.server.shutdown()
        self.server.server_close()
        self._faden.join(5)

    def nachricht(self, chat_id, text, datum=None, typ="private", von_bot=False, vorname="Dana", benutzer="dana"):
        self._id += 1
        chat = {"id": chat_id, "type": typ, "first_name": vorname, "username": benutzer}
        self.updates.append({"update_id": self._id, "message": {
            "message_id": self._id, "date": int(datum if datum is not None else time.time()), "chat": chat,
            "from": {"id": chat_id, "is_bot": von_bot, "first_name": vorname}, "text": text}})

    def texte(self) -> list[str]:
        return [d["text"] for d in self.gesendet]

    def alle_texte(self) -> str:
        return "\n".join(self.texte())


class Mitschnitt:
    """Fängt alles ab, was irgendwo ausgegeben würde: Logs, stdout, stderr."""

    def __init__(self):
        self.log = io.StringIO()
        self.out, self.err = io.StringIO(), io.StringIO()
        self._handler = logging.StreamHandler(self.log)
        self._logger = logging.getLogger("kitrader")
        self._stack = contextlib.ExitStack()

    def starten(self):
        self._logger.addHandler(self._handler)
        self._alt = self._logger.level
        self._logger.setLevel(logging.DEBUG)
        self._stack.enter_context(contextlib.redirect_stdout(self.out))
        self._stack.enter_context(contextlib.redirect_stderr(self.err))

    def stoppen(self):
        self._stack.close()
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._alt)

    def alles(self) -> str:
        return self.log.getvalue() + self.out.getvalue() + self.err.getvalue()


class TelegramBasis(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ordner = Path(self._d.name) / "daten"
        self.ordner.mkdir()
        self.konfig = Path(self._d.name) / "konfig"
        self.konfig.mkdir()
        env = mock.patch.dict(os.environ, {"KITRADER_DATEN": str(self.ordner), "KITRADER_KONFIG": str(self.konfig / "einstellungen.toml")})
        env.start()
        self.addCleanup(env.stop)
        for name in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
            os.environ.pop(name, None)
        self.fake = FakeTelegram()
        self.addCleanup(self.fake.stoppen)
        abstand = mock.patch.object(telegram, "MIN_ABSTAND_S", 0)
        abstand.start()
        self.addCleanup(abstand.stop)
        telegram._boten.clear()
        self.addCleanup(telegram._boten.clear)
        self.einst = mit_standard({"telegram": {"api_url": self.fake.url, "poll_s": 1, "zeitlimit_s": 3, "max_alter_s": 600,
                                                "ablehnungen_alle_s": 600},
                                   "osiris": {"automatisch": False}})
        self.mitschnitt = Mitschnitt()
        self.mitschnitt.starten()
        self.addCleanup(self._token_nirgends)
        self.addCleanup(self.mitschnitt.stoppen)         # läuft zuerst (Aufräumen in umgekehrter Reihenfolge)

    def _token_nirgends(self):
        alles = self.mitschnitt.alles()
        self.assertNotIn(TOKEN, alles)
        self.assertNotIn(TOKEN.split(":")[1], alles)

    def einrichten(self, chat=CHAT):
        telegram.zugang_speichern(self.konfig / "telegram.env", {"TELEGRAM_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": str(chat)})

    def client(self):
        return telegram.Telegram(TOKEN, self.fake.url, 3)

    def bote(self):
        return telegram.Bote(self.client(), str(CHAT), 0)


class ZugangTest(TelegramBasis):
    def test_speichern_mit_rechten_600_und_lesen(self):
        datei = self.konfig / "telegram.env"
        datei.write_text("# mein Bot\nANDERES=bleibt\nTELEGRAM_CHAT_ID=alt\n")
        telegram.zugang_speichern(datei, {"TELEGRAM_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "4711"})
        self.assertEqual(stat.S_IMODE(datei.stat().st_mode), 0o600)
        text = datei.read_text()
        self.assertIn("# mein Bot", text)
        self.assertIn("ANDERES=bleibt", text)
        self.assertEqual(text.count("TELEGRAM_CHAT_ID"), 1)
        z = telegram.zugang_lesen(umgebung={}, datei=datei)
        self.assertEqual((z.token, z.chat_id, z.bereit), (TOKEN, "4711", True))
        self.assertNotIn(TOKEN, repr(z))
        self.assertNotIn(TOKEN, str(z))
        self.assertEqual(telegram.rechte_hinweis(datei), "")
        datei.chmod(0o644)
        self.assertIn("chmod 600", telegram.rechte_hinweis(datei))

    def test_ohne_datei_nicht_bereit(self):
        self.assertFalse(telegram.zugang_lesen(umgebung={}, datei=self.konfig / "gibt-es-nicht.env").bereit)

    def test_quellen_zeilenformen(self):
        datei = self.konfig / "t.env"
        datei.write_text(f"export TELEGRAM_TOKEN='{TOKEN}'\nTELEGRAM_CHAT_ID = \"-100200\"\n")
        z = telegram.zugang_lesen(umgebung={}, datei=datei)
        self.assertEqual((z.token, z.chat_id), (TOKEN, "-100200"))


class MeldenTest(TelegramBasis):
    def test_melden_sendet_an_den_erlaubten_chat(self):
        self.einrichten()
        self.assertTrue(telegram.melden("Hallo Welt", self.einst, warten=True))
        self.assertEqual(self.fake.gesendet, [{"chat_id": "4711", "text": "Hallo Welt", "disable_web_page_preview": True}])

    def test_lange_texte_werden_geteilt(self):
        self.client().senden(CHAT, "x" * 9000)
        self.assertEqual([len(t) for t in self.fake.texte()], [3900, 3900, 1200])

    def test_ohne_einrichtung_oder_ausgeschaltet_passiert_nichts(self):
        self.assertFalse(telegram.melden("x", self.einst, warten=True))
        self.einrichten()
        aus = mit_standard({"telegram": {"aktiv": False, "api_url": self.fake.url}})
        self.assertFalse(telegram.melden("x", aus, warten=True))
        self.assertEqual(self.fake.anfragen, [])

    def test_ausfall_blockiert_nicht_und_wird_nur_protokolliert(self):
        self.einrichten()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        tot = f"http://127.0.0.1:{s.getsockname()[1]}"                  # Port, auf dem niemand lauscht
        s.close()
        einst = mit_standard({"telegram": {"api_url": tot, "zeitlimit_s": 2}})
        start = time.monotonic()
        self.assertTrue(telegram.melden("Nachricht ins Leere", einst))   # angenommen, geht im Hintergrund raus
        self.assertLess(time.monotonic() - start, 0.5)                   # blockiert nie
        bote = next(iter(telegram._boten.values()))
        bote.leeren(5)
        self.assertEqual(bote.fehlgeschlagen, 1)
        self.assertIn("nicht erreichbar", self.mitschnitt.log.getvalue())

    def test_fehlerantworten_enthalten_den_token_nicht(self):
        for status in (400, 401, 403, 404, 429, 500):
            self.fake.fehler_status = status
            with self.assertRaises(telegram.TelegramFehler) as k:
                self.client().senden(CHAT, "x")
            self.assertNotIn(TOKEN, str(k.exception), status)
            self.assertNotIn("AAFakeToken", str(k.exception), status)
            self.assertIn(str(status), str(k.exception))
        self.fake.fehler_status = None
        # falscher Token: Antwort 401, der falsche Token steht auch nicht im Fehler
        with self.assertRaises(telegram.TelegramFehler) as k:
            telegram.Telegram("999:GeheimerFalscherToken_abcdefghijklmnop", self.fake.url, 3).get_me()
        self.assertNotIn("GeheimerFalscherToken", str(k.exception))

    def test_unbrauchbarer_token_leckt_nicht_ueber_ungueltige_adresse(self):
        schlecht = "12 34:AAGeheim\nZeilenumbruch_im_Token_abcdef"
        with self.assertRaises(telegram.TelegramFehler) as k:
            telegram.Telegram(schlecht, self.fake.url, 3).get_me()
        self.assertNotIn("AAGeheim", str(k.exception))
        self.assertNotIn("Zeilenumbruch", str(k.exception))
        self.assertIsNone(k.exception.__cause__)
        self.assertTrue(k.exception.__suppress_context__)

    def test_wiederholung_bei_429(self):
        antworten = iter([429, 200])
        original = self.client().aufruf
        calls = []

        def aufruf(methode, daten=None, zeitlimit_s=None):
            calls.append(methode)
            if next(antworten) == 429:
                fehler = telegram.TelegramFehler("Telegram meldet Fehler 429: Too Many Requests")
                fehler.code, fehler.wartezeit = 429, 0
                raise fehler
            return original(methode, daten, zeitlimit_s)
        bote = self.bote()
        bote.client.aufruf = aufruf
        bote.melden("nochmal")
        self.assertTrue(bote.leeren(5))
        self.assertEqual((bote.gesendet, calls), (1, ["sendMessage", "sendMessage"]))


class WacheTest(TelegramBasis):
    def setUp(self):
        super().setUp()
        self.uhr = Uhr()
        self.markt = Markt(self.uhr)
        self.markt.setze("ABC", 99.9, 100.0)
        self.einst = mit_standard({**{k: v for k, v in self.einst.items()},
                                   "kosten": {"gebuehr_fest": 1.0, "gebuehr_prozent": 0.0, "schlupf_prozent": 0.0},
                                   "autopilot": {"symbole": ["ABC"]}})       # der Not-Aus fasst nur eigene Symbole an
        self.bauer = lambda: PapierBroker(self.markt, self.einst, self.ordner / "papier.db", uhr=self.uhr)
        b = self.bauer()
        b.senden(Order("ABC", "kauf", 10, stop_loss=95.0, take_profit=110.0))
        b.beenden()
        self.notaus_aufrufe = 0

        def notaus():
            self.notaus_aufrufe += 1
            return betrieb.notaus_ausloesen(self.einst, self.ordner, "papier", quelle="Telegram", bauer=self.bauer, melden=False)
        self.wache = telegram.Wache(self.client(), str(CHAT), self.einst, self.ordner, "papier", notaus=notaus,
                                    status=lambda: betrieb.status_text(betrieb.uebersicht(self.einst, self.ordner, "papier", self.bauer)))

    def position_offen(self):
        b = self.bauer()
        try:
            return bool(b.positionen())
        finally:
            b.beenden()

    def test_stop_aus_dem_erlaubten_chat_loest_den_notaus_aus(self):
        self.fake.nachricht(CHAT, "Stop")
        self.assertEqual(self.wache.schritt(), 1)
        self.assertTrue((self.ordner / "NOTAUS").is_file())
        self.assertFalse(self.position_offen())
        antwort = self.fake.alle_texte()
        self.assertEqual({d["chat_id"] for d in self.fake.gesendet}, {str(CHAT)})
        self.assertIn("NOT-AUS ausgelöst (Telegram)", antwort)
        self.assertIn("glattgestellt: ABC +10", antwort)
        self.assertIn("storniert", antwort)
        self.assertIn("notaus --aufheben", antwort)
        j = Journal(self.ordner / "journal.db", self.einst)
        self.addCleanup(j.beenden)
        self.assertTrue(any("Telegram" in e["text"] for e in j.ereignisse() if e["art"] == "notaus"))

    def test_notaus_ohne_alles_laesst_fremde_positionen_in_ruhe(self):
        self.markt.setze("FREMD", 49.9, 50.0)
        b = self.bauer()
        b.senden(Order("FREMD", "kauf", 3, stop_loss=45.0))
        b.beenden()
        e = betrieb.notaus_ausloesen(self.einst, self.ordner, "papier", quelle="Telegram", bauer=self.bauer, melden=False)
        self.assertTrue(e["ok"], e)
        self.assertFalse(e["alles"])
        self.assertFalse(self.position_offen_von("ABC"))
        self.assertTrue(self.position_offen_von("FREMD"))
        text = betrieb.notaus_text(e, "Telegram")
        self.assertIn("fremd, nicht angefasst: Position FREMD +3", text)
        self.assertIn("NICHT angefasst", text)
        self.assertIn("notaus --alles", text)
        # mit alles=True das ganze Konto
        e = betrieb.notaus_ausloesen(self.einst, self.ordner, "papier", quelle="Kommandozeile", bauer=self.bauer, melden=False,
                                     alles=True)
        self.assertTrue(e["alles"])
        self.assertFalse(self.position_offen_von("FREMD"))
        self.assertNotIn("NICHT angefasst", betrieb.notaus_text(e))

    def position_offen_von(self, symbol):
        b = self.bauer()
        try:
            return any(p.symbol == symbol for p in b.positionen())
        finally:
            b.beenden()

    def test_schreibweisen_von_stop(self):
        for text in ("stop", "STOP", "  Stopp  ", "/stop", "/stop@kitrader_test_bot", "/Stopp"):
            self.assertEqual(telegram.befehl_aus(text), "stop", text)
        for text in ("stop bitte", "stoppen", "nicht stop", "halt", "", None, "stop!", "alles stop", "hallo", "/start"):
            self.assertIsNone(telegram.befehl_aus(text), text)
        self.assertEqual(telegram.befehl_aus("Status"), "status")

    def test_fremde_chat_id_wird_ignoriert_ohne_antwort(self):
        self.fake.nachricht(999, "stop", vorname="Fremder")
        self.fake.nachricht(999, "status")
        self.fake.nachricht(-100123, "stop", typ="group")              # fremde Gruppe
        self.fake.nachricht(CHAT, "stop", von_bot=True)                # Bot im erlaubten Chat
        self.assertEqual(self.wache.schritt(), 0)
        self.assertFalse((self.ordner / "NOTAUS").exists())
        self.assertTrue(self.position_offen())
        self.assertEqual(self.fake.gesendet, [])                       # auch nicht antworten
        self.assertEqual(self.wache.fremde, 4)

    def test_anderes_aus_dem_erlaubten_chat_wird_ignoriert(self):
        for text in ("hallo", "wie geht's", "stop alles bitte", "/start", "kaufe 10 AAPL"):
            self.fake.nachricht(CHAT, text)
        self.assertEqual(self.wache.schritt(), 0)
        self.assertEqual(self.fake.gesendet, [])
        self.assertFalse((self.ordner / "NOTAUS").exists())
        self.assertEqual(self.notaus_aufrufe, 0)

    def test_status(self):
        self.fake.nachricht(CHAT, "status")
        self.assertEqual(self.wache.schritt(), 1)
        text = self.fake.alle_texte()
        self.assertIn("Betriebsart PAPIER", text)
        self.assertIn("Gesamtwert", text)
        self.assertIn("Offene Positionen: 1", text)
        self.assertIn("Offene Orders: 2", text)
        self.assertFalse((self.ordner / "NOTAUS").exists())
        self.assertTrue(self.position_offen())

    def test_zu_alte_stop_nachricht_wird_nicht_ausgefuehrt(self):
        self.fake.nachricht(CHAT, "stop", datum=time.time() - 3600)
        self.assertEqual(self.wache.schritt(), 0)
        self.assertFalse((self.ordner / "NOTAUS").exists())
        self.assertIn("NICHT ausgeführt", self.fake.alle_texte())

    def test_befehl_wird_nur_einmal_ausgefuehrt(self):
        self.fake.nachricht(CHAT, "stop")
        self.wache.schritt()
        self.wache.schritt()
        self.wache.schritt()
        self.assertEqual(self.notaus_aufrufe, 1)
        self.assertGreater(self.fake.letzter_offset, 100)              # bestätigt

    def test_notaus_fehler_wird_gemeldet(self):
        def kaputt():
            raise RuntimeError("Broker brennt")
        self.wache._notaus = kaputt
        self.fake.nachricht(CHAT, "stop")
        self.assertEqual(self.wache.schritt(), 1)
        self.assertIn("NOT-AUS FEHLGESCHLAGEN", self.fake.alle_texte())
        self.assertIn("Broker brennt", self.fake.alle_texte())

    def test_schleife_im_hintergrund_reagiert_und_ueberlebt_ausfaelle(self):
        halt = threading.Event()
        self.wache.pause_min_s = 0.05
        faden = threading.Thread(target=self.wache.lauf, args=(halt,), daemon=True)
        self.fake.fehler_status = 500                                  # Telegram macht Probleme
        faden.start()
        time.sleep(0.4)
        self.assertTrue(faden.is_alive())
        self.fake.fehler_status = None
        self.fake.nachricht(CHAT, "stop")
        for _ in range(100):
            if (self.ordner / "NOTAUS").exists():
                break
            time.sleep(0.05)
        halt.set()
        faden.join(5)
        self.assertFalse(faden.is_alive())
        self.assertTrue((self.ordner / "NOTAUS").is_file())
        self.assertFalse(self.position_offen())
        self.assertIn("Telegram-Wache", self.mitschnitt.log.getvalue())


class Immer(Strategie):
    """Long-Signal mit 1 Euro Stop und 5 Euro Ziel, sobald 3 Kerzen da sind und nichts offen ist."""
    name = "immer"

    def bewerten(self, kerzen, lage):
        if lage.menge() or len(kerzen) < 3:
            return None
        c = kerzen[-1].c
        return Signal("long", c, c - 1.0, c + 5.0, 0.9, "Test")


class Nie(Strategie):
    name = "nie"

    def bewerten(self, kerzen, lage):
        return None


def flach(preise, start=START, takt_min=5):
    return [Kerze(start + timedelta(minutes=takt_min * i), p, p, p, p, 1000) for i, p in enumerate(preise)]


class WacheBefehlTest(TelegramBasis):
    def test_wache_im_vordergrund_antwortet_auf_status_und_endet_mit_halt(self):
        self.einrichten()
        zeilen: list[str] = []
        halt = threading.Event()
        faden = threading.Thread(target=telegram.wache_laufen, args=(self.einst, self.ordner, "papier", zeilen.append, halt), daemon=True)
        self.fake.nachricht(CHAT, "status")
        self.fake.nachricht(12345, "status")                            # Fremder bekommt nichts
        faden.start()
        for _ in range(100):
            if any("Betriebsart PAPIER" in t and t.startswith("kitrader") for t in self.fake.texte()):
                break
            time.sleep(0.05)
        halt.set()
        faden.join(5)
        self.assertFalse(faden.is_alive())
        texte = self.fake.texte()
        self.assertTrue(texte[0].startswith("Telegram-Wache gestartet"), texte)
        self.assertTrue(any(t.startswith("kitrader - Betriebsart PAPIER") for t in texte), texte)
        self.assertEqual(len(texte), 2)
        self.assertEqual({d["chat_id"] for d in self.fake.gesendet}, {str(CHAT)})
        self.assertIn("'stop'", zeilen[0])


def _still_schliessen(objekt):
    try:
        objekt.beenden()
    except Exception:
        pass


def autopilot_bauen(test, preise, strategie, **teil):
    """Autopilot im Papierbetrieb mit Wiedergabe (wie in test_autopilot)."""
    e = {"maerkte": {"TEST": "krypto"},
         "kosten": {"gebuehr_fest": 1.0, "gebuehr_prozent": 0.0, "schlupf_prozent": 0.0, "spread_prozent": 0.0},
         "risiko": {"standard_markt": "krypto"},
         "autopilot": {"symbole": ["TEST"], "takt": "5m", "pause_s": 0, "sicherheit_min": 0.5},
         "telegram": {"api_url": test.fake.url, "poll_s": 1, "zeitlimit_s": 3, "ablehnungen_alle_s": 600},
         "osiris": {"automatisch": False}}
    for k, v in teil.items():
        e.setdefault(k, {}).update(v)
    test.einst = mit_standard(e)
    test.quelle = WiedergabeQuelle({"TEST": preise if isinstance(preise[0], Kerze) else flach(preise)}, "5m", 0.0)
    test.broker = PapierBroker(test.quelle.kurs, test.einst, ":memory:", kerzengeber=test.quelle.kerzen, uhr=test.quelle.jetzt)
    test.journal = Journal(":memory:", test.einst, test.quelle.jetzt)
    for objekt in (test.broker, test.journal):               # main() schließt sie ggf. schon selbst
        test.addCleanup(_still_schliessen, objekt)
    test.ausgabe = []
    test.ap = Autopilot(test.broker, test.quelle, strategie, test.journal, test.einst, test.ordner, "papier", ["TEST"],
                        uhr=test.quelle.jetzt, warten=test.quelle.weiter, ausgabe=test.ausgabe.append)
    return test.ap


class BeobachterTest(TelegramBasis):
    def beobachten(self, preise, strategie, **teil):
        ap = autopilot_bauen(self, preise, strategie, **teil)
        self.bote_ = self.bote()
        self.beob = telegram.beobachten(ap, self.einst, self.ordner, bote=self.bote_)
        self.beob.starten()
        return ap

    def fertig(self):
        self.beob.beenden()
        self.assertTrue(self.bote_.leeren(5))

    def test_start_ausfuehrung_trade_und_stopp(self):
        ap = self.beobachten([100, 100, 100, 100, 100, 98, 98, 98], Immer(), risiko={"tagesverlust_max": 50})
        ap.laufen()
        self.fertig()
        t = self.fake.texte()
        self.assertTrue(t[0].startswith("Autopilot gestartet. Betriebsart PAPIER (kein echtes Geld), Strategie immer, Symbole TEST"))
        self.assertTrue(any(x.startswith("[PAPIER] Order ausgefuehrt: kauf 10 TEST @ 100") and "Stop 99" in x for x in t), t)
        self.assertTrue(any(x.startswith("[PAPIER] Ergebnis: Trade TEST netto -") for x in t), t)
        self.assertTrue(t[-1].startswith("Autopilot gestoppt. Betriebsart PAPIER"), t[-1])
        self.assertIn("Schutz-Stops", t[-1])
        self.assertGreaterEqual(len(self.journal.entscheidungen()), 1)  # das Journal funktioniert unverändert
        self.assertEqual(self.journal.entscheidungen()[0]["gesendet"], 1)

    def test_ablehnungen_werden_zusammengefasst_und_tagesverlust_gemeldet(self):
        preise = [100, 100, 100, 98.5, 98.5, 98.5, 98.5, 98.5, 98.5, 98.5]
        ap = self.beobachten(preise, Immer(), risiko={"tagesverlust_max": 5})
        ap.laufen()
        self.fertig()
        t = self.fake.texte()
        self.assertTrue(any("TAGESVERLUSTGRENZE erreicht" in x and "Keine neuen Positionen bis morgen" in x for x in t), t)
        sammel = [x for x in t if "Risikoprüfung:" in x and "abgelehnt" in x]
        self.assertEqual(len(sammel), 1, "Ablehnungen kommen als eine Nachricht, nicht einzeln")
        self.assertRegex(sammel[0], r"\d+ Order\(s\) abgelehnt")
        self.assertRegex(sammel[0], r"- \d+x TEST kauf: .*Tagesverlust")
        abgelehnt = [e for e in self.journal.entscheidungen(100) if e["pruefung_ok"] == 0]
        self.assertIn(f"{len(abgelehnt)} Order(s)", sammel[0])

    def test_notaus_im_autopiloten_wird_gemeldet(self):
        ap = self.beobachten([100] * 12, Immer())
        for _ in range(3):
            ap.schritt()
            self.quelle.weiter()
        (self.ordner / "NOTAUS").write_text("x")
        ap.schritt()
        (self.ordner / "NOTAUS").unlink()
        ap.schritt()
        self.fertig()
        t = "\n".join(self.fake.texte())
        self.assertIn("NOT-AUS im Autopiloten", t)
        self.assertIn("glattgestellt", t)
        self.assertIn("Not-Aus aufgehoben", t)

    def test_tagesabschluss_wird_gemeldet(self):
        start = START.replace(hour=9, minute=0)
        kerzen = flach([100] * 22, start=start)
        e = {"maerkte": {"TEST": "boerse"}, "handelszeiten": {"boerse": {"zeitzone": "UTC", "von": "09:00", "bis": "10:30", "tage": [0, 1, 2, 3, 4]}}}
        ap = autopilot_bauen(self, kerzen, Nie(), **e)
        self.bote_ = self.bote()
        self.beob = telegram.beobachten(ap, self.einst, self.ordner, bote=self.bote_)
        self.beob.starten()
        ap.laufen()
        self.fertig()
        t = [x for x in self.fake.texte() if "Tagesabschluss" in x]
        self.assertEqual(len(t), 1, self.fake.texte())
        self.assertRegex(t[0], r"Gesamtwert .* EUR, Tagesergebnis")

    def test_stoppgrund_abbruch(self):
        ap = self.beobachten([100] * 5, Nie())
        ap.schritt = mock.Mock(side_effect=Abbruch("Freigabe abgelaufen."))
        ap.laufen()
        self.fertig()
        self.assertIn("Autopilot beendet: Freigabe abgelaufen.", self.fake.texte()[-1])

    def test_beobachter_nutzt_den_haken_und_umhuellt_das_journal_nicht(self):
        ap = self.beobachten([100] * 4, Nie())
        self.assertIn(self.beob.ereignis, ap.beobachter)
        for name in ("entscheidung", "ausfuehrung", "ergebnis_zu_order", "ereignis"):
            self.assertNotIn(name, vars(ap.journal), f"Journal.{name} darf nicht umhüllt sein")
            self.assertIs(getattr(ap.journal, name).__func__, getattr(Journal, name))
        self.fertig()

    def test_ziel_tagesverlust_warnung_und_ende_kommen_ueber_den_haken(self):
        ap = self.beobachten([100] * 4, Nie())
        ap.laufen(max_schritte=1)                                      # echtes Ende über den Haken
        ap._ereignis("ziel_erreicht", symbol="XBTEUR", ziel=52000.0, preis=52010.0, text="XBTEUR: Gewinnziel 52000 erreicht")
        ap._ereignis("tagesverlust", text="Tagesverlust -6,00 erreicht Grenze -5,00", schliessen=True)
        ap._ereignis("warnung", art="fremde_position", text="Fremde Position AAPL +3 wird nicht angefasst")
        self.fertig()
        t = "\n".join(self.fake.texte())
        self.assertIn("[PAPIER] GEWINNZIEL erreicht: XBTEUR: Gewinnziel 52000 erreicht", t)
        self.assertIn("TAGESVERLUSTGRENZE erreicht: Tagesverlust -6,00", t)
        self.assertIn("Alle eigenen Positionen werden glattgestellt", t)
        self.assertIn("[PAPIER] WARNUNG: Fremde Position AAPL", t)
        self.assertEqual(sum(x.startswith("Autopilot gestoppt") for x in self.fake.texte()), 1)   # Ende nicht doppelt

    def test_tagesverlust_im_autopiloten_stellt_glatt_und_meldet_es(self):
        preise = [100, 100, 100, 98, 98, 98, 98, 98]
        ap = self.beobachten(preise, Nie(), risiko={"tagesverlust_max": 5})
        self.broker.senden(Order("TEST", "kauf", 10, stop_loss=50.0))      # unrealisierter Verlust bei Kurs 98: -20
        ap.laufen()
        self.fertig()
        t = self.fake.texte()
        self.assertTrue(any("TAGESVERLUSTGRENZE erreicht" in x and "glattgestellt" in x for x in t), t)
        self.assertEqual(self.broker.positionen(), [])

    def test_telegram_ausfall_stoert_den_autopiloten_nicht(self):
        ap = autopilot_bauen(self, [100, 100, 100, 100, 100, 98, 98, 98], Immer(), risiko={"tagesverlust_max": 50})
        bote = mock.Mock()
        bote.melden.side_effect = RuntimeError("Telegram kaputt")
        beob = telegram.Beobachter(ap, self.einst, self.ordner, bote)
        beob.starten()
        ap.laufen()                                                    # läuft durch
        beob.beenden()
        self.assertEqual(len(self.broker.trades()), 1)
        self.assertGreaterEqual(len(self.journal.entscheidungen()), 1)
        self.assertIn("Telegram-Beobachter", self.mitschnitt.log.getvalue())

    def test_ohne_einrichtung_gibt_es_keinen_beobachter(self):
        ap = autopilot_bauen(self, [100] * 3, Nie())
        self.assertIsNone(telegram.beobachten(ap, self.einst, self.ordner))
        self.einrichten()
        self.assertIsNotNone(telegram.beobachten(ap, self.einst, self.ordner))
        aus = mit_standard({**self.einst, "telegram": {**self.einst["telegram"], "aktiv": False}})
        self.assertIsNone(telegram.beobachten(ap, aus, self.ordner))


class AutopilotBefehlTest(TelegramBasis):
    """Die Einbindung in __main__.cmd_autopilot, ohne autopilot.py zu ändern."""

    def konfig_schreiben(self, wache=False):
        (self.konfig / "einstellungen.toml").write_text(
            f'[telegram]\napi_url = "{self.fake.url}"\nwache = {"true" if wache else "false"}\npoll_s = 1\nzeitlimit_s = 3\n'
            '[osiris]\nautomatisch = false\n')

    def test_befehl_autopilot_meldet_start_und_stopp(self):
        self.einrichten()
        self.konfig_schreiben()
        ap = autopilot_bauen(self, [100] * 4, Nie())
        with mock.patch("kitrader.__main__.bauen", return_value=ap):
            self.assertEqual(main(["autopilot", "--schritte", "3"]), 0)
        t = self.fake.texte()
        self.assertTrue(t[0].startswith("Autopilot gestartet. Betriebsart PAPIER"), t)
        self.assertTrue(t[-1].startswith("Autopilot gestoppt."), t)
        self.assertIn("TELEGRAM aktiv", self.mitschnitt.out.getvalue())

    def test_wiedergabe_meldet_nichts(self):
        self.einrichten()
        self.konfig_schreiben()
        csv = Path(self._d.name) / "k.csv"
        csv.write_text("t,o,h,l,c,v\n" + "\n".join(f"{1772445000 + 300 * i},100,100,100,100,1000" for i in range(6)))
        self.assertEqual(main(["autopilot", "--wiedergabe", str(csv), "--markt", "krypto", "--schritte", "2", "--symbole", "TEST"]), 0)
        self.assertEqual(self.fake.gesendet, [])

    def test_wache_im_autopiloten_loest_notaus_per_nachricht_aus(self):
        self.einrichten()
        self.konfig_schreiben(wache=True)
        ap = autopilot_bauen(self, [100] * 4, Nie())
        self.fake.nachricht(CHAT, "stop")
        self.fake.nachricht(777, "stop")                               # fremder Chat: ohne Wirkung

        def warten(_):
            for _ in range(100):                                       # läuft, bis die Wache die Datei angelegt hat
                if (self.ordner / "NOTAUS").exists():
                    return False
                time.sleep(0.05)
            return False
        ap.warten = warten
        with mock.patch("kitrader.__main__.bauen", return_value=ap):
            self.assertEqual(main(["autopilot"]), 0)
        self.assertTrue((self.ordner / "NOTAUS").is_file())
        t = self.fake.alle_texte()
        self.assertIn("NOT-AUS ausgelöst (Telegram)", t)
        self.assertIn("Not-Aus per Telegram: schreibe 'stop'", t)
        self.assertEqual({d["chat_id"] for d in self.fake.gesendet}, {str(CHAT)})
        self.assertIn("Wache hört", self.mitschnitt.out.getvalue())

    def test_befehle_ohne_einrichtung_geben_klare_meldung(self):
        for aktion in ("test", "wache"):
            self.assertEqual(main(["telegram", aktion]), 2)
        self.assertIn("einrichten", self.mitschnitt.err.getvalue())
        self.assertEqual(self.fake.anfragen, [])


class EinrichtenTest(TelegramBasis):
    def lauf(self, antworten, token=TOKEN, warte_s=0.5, **kw):
        ausgabe: list[str] = []
        eingaben = iter(antworten)
        try:
            ergebnis = telegram.einrichten(self.einst, self.konfig / "telegram.env", eingabe=lambda frage: (ausgabe.append(frage), next(eingaben))[1],
                                           geheim=lambda frage: token, ausgabe=ausgabe.append, warte_s=warte_s, **kw)
        finally:
            self.text = "\n".join(ausgabe)
            self.assertNotIn(TOKEN, self.text)
        return ergebnis

    def test_einrichten_findet_chat_id_speichert_und_sendet_test(self):
        self.fake.nachricht(CHAT, "hallo", vorname="Dana")
        self.assertEqual(self.lauf(["j"]), 0)
        datei = self.konfig / "telegram.env"
        self.assertEqual(stat.S_IMODE(datei.stat().st_mode), 0o600)
        z = telegram.zugang_lesen(umgebung={}, datei=datei)
        self.assertEqual((z.token, z.chat_id), (TOKEN, str(CHAT)))
        self.assertIn("Telegram ist eingerichtet", self.fake.alle_texte())
        self.assertEqual({d["chat_id"] for d in self.fake.gesendet}, {str(CHAT)})
        self.assertIn("@kitrader_test_bot", self.text)
        self.assertGreater(self.fake.letzter_offset, 100)              # abgeholte Nachrichten bestätigt
        self.assertTrue(telegram.melden("danach", self.einst, warten=True))   # und es funktioniert gleich

    def test_nein_speichert_nichts(self):
        self.fake.nachricht(CHAT, "hallo")
        with self.assertRaises(telegram.TelegramFehler):
            self.lauf(["n"])
        self.assertFalse((self.konfig / "telegram.env").exists())
        self.assertEqual(self.fake.gesendet, [])

    def test_mehrere_absender_man_waehlt_selbst(self):
        self.fake.nachricht(999, "ich bin ein Fremder", vorname="Fremder", benutzer="fremder")
        self.fake.nachricht(CHAT, "hallo", vorname="Dana")
        self.assertEqual(self.lauf(["2"]), 0)
        self.assertEqual(telegram.zugang_lesen(umgebung={}, datei=self.konfig / "telegram.env").chat_id, str(CHAT))

    def test_gruppen_werden_nicht_angeboten(self):
        self.fake.nachricht(-100999, "hallo Gruppe", typ="group")
        with self.assertRaises(telegram.TelegramFehler) as k:
            self.lauf(["j"], warte_s=0.3)
        self.assertIn("Keine Nachricht", str(k.exception))

    def test_ohne_nachricht_klare_meldung(self):
        with self.assertRaises(telegram.TelegramFehler) as k:
            self.lauf([], warte_s=0.3)
        self.assertIn("Keine Nachricht erhalten", str(k.exception))
        self.assertFalse((self.konfig / "telegram.env").exists())

    def test_falsches_tokenformat(self):
        with self.assertRaises(telegram.TelegramFehler):
            self.lauf([], token="das ist kein token")
        self.assertEqual(self.fake.anfragen, [])

    def test_vorhandene_chat_id_bleibt_ohne_neu(self):
        self.einrichten(chat=555)
        self.assertEqual(self.lauf([]), 0)
        self.assertEqual(telegram.zugang_lesen(umgebung={}, datei=self.konfig / "telegram.env").chat_id, "555")
        self.assertEqual({d["chat_id"] for d in self.fake.gesendet}, {"555"})

    def test_befehl_einrichten_ueber_main(self):
        self.fake.nachricht(CHAT, "hallo")
        (self.konfig / "einstellungen.toml").write_text(f'[telegram]\napi_url = "{self.fake.url}"\n')
        with mock.patch("builtins.input", return_value="j"), mock.patch("getpass.getpass", return_value=TOKEN):
            self.assertEqual(main(["telegram", "einrichten"]), 0)
        self.assertTrue(telegram.zugang_lesen(umgebung={}, datei=self.konfig / "telegram.env").bereit)


class NotausBefehlTest(TelegramBasis):
    def test_notaus_per_kommandozeile_meldet_auch_per_telegram(self):
        self.einrichten()
        (self.konfig / "einstellungen.toml").write_text(f'[telegram]\napi_url = "{self.fake.url}"\n')
        self.assertEqual(main(["notaus"]), 0)
        self.assertTrue((self.ordner / "NOTAUS").is_file())
        self.assertIn("NOT-AUS per Kommandozeile ausgelöst", self.fake.alle_texte())


if __name__ == "__main__":
    unittest.main()
