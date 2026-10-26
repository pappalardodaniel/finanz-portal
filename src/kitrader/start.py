"""Alles in einem Befehl:  python3 -m kitrader start [--modus papier|demo|echt]

Ein Prozess, mehrere Fäden („Teile“), jeder unter eigener Aufsicht:

  Autopilot       handelt (Standard: Papier, kein Geld); im Dashboard (Reiter Autopilot) lässt er sich im Papierbetrieb stoppen und starten. Echtgeld wie immer nur mit modus = "echt" in den Einstellungen
                  UND der Freigabedatei von heute; das wird vor dem Start geprüft, sonst startet gar nichts.
  Dashboard       das Markt-Dashboard im Browser (Hauptoberfläche, Port 8812, nur 127.0.0.1): Kurse, Charts mit Papierhandel, Trends, KI,
                  Weltlage und Autopilot; dazu als eigene Prozesse der Analysen-Dienst und, wenn Node da ist, der Claude-Agent.
                  Die kleine Oberfläche (Port 8850) gibt es nur auf Wunsch (--alte-oberflaeche).
  Telegram-Wache  „stop“ / „status“ aus dem eigenen Chat (nur wenn Telegram eingerichtet ist)
  Zeitplan        OSIRIS-Weltlage alle 6 Stunden, Nachrichten-Radar und Welt-Rotation alle 2 Stunden, Morgenlage täglich
                  zur eingestellten Uhrzeit ([zeitplan])

Stürzt ein Teil ab, laufen die anderen weiter; er wird nach einer Pause neu gestartet (Pause wächst bis 5 Minuten) und der
Absturz kommt ins Journal, ins Terminal und (zu Beginn einer Pannenserie) per Telegram aufs Handy. Strg+C beendet alles
sauber. Ein zweiter Start in demselben Datenordner wird verweigert (Sperrdatei `start.lock`).
"""
from __future__ import annotations

import contextlib
import copy
import importlib
import importlib.util
import json
import logging
import os
import shutil
import signal
import sys
import threading
import time
import tomllib
import traceback
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import fcntl
except ImportError:                      # Windows: dann nur Prüfung über die Prozessnummer
    fcntl = None

from . import betrieb as _betrieb
from . import einstellungen as _einst
from . import ki as _ki
from . import osiris as _osiris
from . import symbole as _symbole
from . import telegram as _telegram
from .autopilot import Abbruch, bauen, modus_pruefen
from .hilfen import jetzt_utc, zu_utc
from .journal import Journal
from .strategie import STRATEGIEN

log = logging.getLogger("kitrader.start")

SPERRDATEI = "start.lock"
BASIS_PAUSE_S = 10.0                     # erste Pause vor dem Neustart eines Teils
MAX_PAUSE_S = 300.0
STABIL_S = 600.0                         # lief ein Teil so lange, beginnt die Pausenfolge wieder bei 10 s
BEENDEN_FRIST_S = 30.0
ZEITPLAN_TAKT_S = 20.0
OSIRIS_WIEDERHOLUNG = timedelta(minutes=30)       # nach einem Fehlschlag
NACHRICHTEN_WIEDERHOLUNG = timedelta(minutes=30)  # nach einem Fehlschlag (Nachrichten und Welt)
MORGENLAGE_WIEDERHOLUNG = timedelta(minutes=30)
MORGENLAGE_VERSUCHE = 3                  # je Tag
VORGABE_KOSTEN = {"gebuehr_fest": 0.0, "gebuehr_prozent": 0.80, "schlupf_prozent": 0.02, "spread_prozent": 0.05}  # Kraken Pro, Stufe 1
MENGENSCHRITT = {"BTC": 0.0001, "XBT": 0.0001, "ETH": 0.001}


# ---------------------------------------------------------------- Doppelstart verhindern

def _lebt(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError, ValueError, TypeError):
        return True
    return True


class Sperre:
    """Sperrdatei `start.lock` im Datenordner. Unter macOS/Linux hält der Prozess eine Dateisperre (flock): sie verschwindet
    von selbst, wenn der Prozess endet (auch bei einem Absturz), es bleibt also nie eine „tote“ Sperre zurück."""

    def __init__(self, ordner: Path):
        self.pfad = Path(ordner) / SPERRDATEI
        self._fd: int | None = None

    @staticmethod
    def _lesen(fd_oder_pfad) -> dict:
        try:
            if isinstance(fd_oder_pfad, int):
                os.lseek(fd_oder_pfad, 0, os.SEEK_SET)
                roh = os.read(fd_oder_pfad, 4096)
            else:
                roh = Path(fd_oder_pfad).read_bytes()[:4096]
            daten = json.loads(roh.decode("utf-8") or "{}")
            return daten if isinstance(daten, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def belegt_von(ordner: Path) -> dict | None:
        """Infos des laufenden Starts (pid, seit, modus, adresse) oder None, wenn keiner läuft."""
        pfad = Path(ordner) / SPERRDATEI
        if not pfad.is_file():
            return None
        if fcntl is None:
            info = Sperre._lesen(pfad)
            return info if info.get("pid") and _lebt(info["pid"]) and info["pid"] != os.getpid() else None
        fd = os.open(pfad, os.O_RDWR)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return Sperre._lesen(fd) or {"pid": None}
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None
        finally:
            os.close(fd)

    def holen(self, info: dict) -> None:
        """Sperre nehmen oder mit klarem Text abbrechen (Abbruch)."""
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.pfad, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                alt = self._lesen(fd)
                if alt.get("pid") and alt["pid"] != os.getpid() and _lebt(alt["pid"]):
                    raise BlockingIOError
        except (BlockingIOError, OSError):
            alt = self._lesen(fd)
            os.close(fd)
            raise Abbruch(self.text_belegt(alt, self.pfad)) from None
        self._fd = fd
        self.schreiben(info)

    def schreiben(self, info: dict) -> None:
        if self._fd is None:
            return
        os.ftruncate(self._fd, 0)
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, json.dumps({"pid": os.getpid(), **info}, ensure_ascii=False).encode("utf-8"))

    def freigeben(self) -> None:
        if self._fd is None:
            return
        with contextlib.suppress(OSError):
            os.ftruncate(self._fd, 0)
        with contextlib.suppress(OSError):
            os.close(self._fd)                       # gibt die Sperre frei
        self._fd = None

    @staticmethod
    def text_belegt(info: dict, pfad: Path) -> str:
        seit = f" seit {info['seit']}" if info.get("seit") else ""
        wo = f" Die Oberfläche: {info['adresse']}" if info.get("adresse") else ""
        pid = f" (Prozess {info['pid']})" if info.get("pid") else ""
        return (f"kitrader läuft schon{seit}{pid}.{wo}\nEin zweiter Start würde dieselben Orders doppelt senden und wird verweigert. "
                "Beenden mit Strg+C in dem Fenster, in dem es läuft, oder:  python3 -m kitrader start --beenden\n"
                f"(Sperrdatei: {pfad})")


def laufenden_beenden(ordner: Path, ausgabe: Callable[[str], None] = print, frist_s: float = 40.0) -> int:
    """`start --beenden`: schickt dem laufenden Start das Beenden-Signal und wartet, bis er weg ist."""
    info = Sperre.belegt_von(ordner)
    if info is None:
        ausgabe("kitrader läuft gerade nicht (keine aktive Sperre im Datenordner).")
        return 0
    pid = info.get("pid")
    if not pid:
        ausgabe("Ein Start läuft, aber seine Prozessnummer ist nicht lesbar. Bitte im Fenster mit Strg+C beenden.")
        return 1
    ausgabe(f"Beende kitrader (Prozess {pid}) ...")
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError as e:
        ausgabe(f"Das Signal ging nicht durch ({e}). Bitte im Fenster, in dem kitrader läuft, Strg+C drücken.")
        return 1
    ende = time.monotonic() + frist_s
    while time.monotonic() < ende:
        if Sperre.belegt_von(ordner) is None:
            ausgabe("kitrader ist beendet.")
            return 0
        time.sleep(0.5)
    ausgabe(f"kitrader reagiert nach {frist_s:.0f} Sekunden noch nicht. Bitte im Fenster mit Strg+C beenden (zweimal = sofort).")
    return 1


# ---------------------------------------------------------------- Teile unter Aufsicht

class TeilEnde(Exception):
    """Der Teil kann dauerhaft nicht laufen (z. B. falsche Einstellung): kein Neustart, der Text erklärt es."""


@dataclass
class Teil:
    name: str
    lauf: Callable[[threading.Event], None]            # blockiert bis `halt` gesetzt wird (oder wirft)
    anhalten: Callable[[], None] | None = None         # weckt `lauf` auf, wenn es nicht auf `halt` hört
    beschreibung: str = ""
    frist_s: float = BEENDEN_FRIST_S                   # so lange wartet das Beenden auf diesen Teil
    status: str = "wartet"                             # wartet | laeuft | pause | beendet | aufgegeben | gestoppt
    fehler: str = ""
    neustarts: int = 0
    faden: threading.Thread | None = field(default=None, repr=False)


class Start:
    """Hält die Teile am Laufen. `melden(text)` geht ins Terminal, ins Journal und (bei Pannen) aufs Handy."""

    def __init__(self, einst: dict, ordner: Path, teile: list[Teil], ausgabe: Callable[[str], None] = print,
                 telegram: Callable[[str], object] | None = None, journal: Callable[[str, str], None] | None = None,
                 basis_pause_s: float = BASIS_PAUSE_S, max_pause_s: float = MAX_PAUSE_S, stabil_s: float = STABIL_S,
                 uhr: Callable[[], float] = time.monotonic, halt: threading.Event | None = None):
        self.einst, self.ordner = einst, Path(ordner)
        self.teile = teile
        self.ausgabe = ausgabe
        self._telegram = telegram if telegram is not None else (lambda text: _telegram.melden(text, einst))
        self._journal = journal if journal is not None else self._journal_schreiben
        self.basis_pause_s, self.max_pause_s, self.stabil_s = basis_pause_s, max_pause_s, stabil_s
        self.uhr = uhr
        self.halt = halt or threading.Event()
        self._sperre_ausgabe = threading.Lock()

    # ---- Meldungen

    def _journal_schreiben(self, art: str, text: str) -> None:
        try:
            j = Journal(self.ordner / "journal.db", self.einst)
            try:
                j.ereignis(art, text)
            finally:
                j.beenden()
        except Exception as e:                       # das Journal darf den Betrieb nie stören
            log.warning("Journal: Ereignis nicht vermerkt (%s)", e)

    def melden(self, text: str, art: str = "start", telegram: bool = False, journal: bool = True) -> None:
        with self._sperre_ausgabe:
            self.ausgabe(f"[{datetime.now():%H:%M:%S}] {text}")
        if journal:
            self._journal(art, text)
        if telegram:
            try:
                self._telegram(f"kitrader: {text}")
            except Exception as e:
                log.warning("Telegram: Meldung nicht gesendet (%s)", e)

    # ---- Aufsicht

    def _fehler_notieren(self, name: str) -> None:
        """Die Einzelheiten eines Absturzes (Traceback) kommen in `start-fehler.log` im Datenordner, nicht ins Terminal."""
        try:
            with open(self.ordner / "start-fehler.log", "a", encoding="utf-8") as f:
                f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} {name}\n{traceback.format_exc()}\n")
        except OSError:
            pass

    def pause(self, fehlversuche: int) -> float:
        """Pause vor dem Neustart: 10 s, 20 s, 40 s ... höchstens 5 Minuten."""
        return min(self.max_pause_s, self.basis_pause_s * 2 ** max(0, fehlversuche - 1))

    def _ueberwachen(self, teil: Teil) -> None:
        fehlversuche = 0
        while not self.halt.is_set():
            teil.status, beginn = "laeuft", self.uhr()
            try:
                teil.lauf(self.halt)
                if self.halt.is_set():
                    break
                grund = "hat sich von selbst beendet"
            except (TeilEnde, Abbruch) as e:
                teil.status, teil.fehler = "aufgegeben", str(e)
                self.melden(f"{teil.name} läuft nicht: {e}", art="teil_ende", telegram=True)
                return
            except Exception as e:
                grund = f"{type(e).__name__}: {e}"
                self._fehler_notieren(teil.name)
            if self.halt.is_set():
                break
            fehlversuche = 1 if self.uhr() - beginn >= self.stabil_s else fehlversuche + 1
            pause = self.pause(fehlversuche)
            teil.neustarts += 1
            teil.status, teil.fehler = "pause", grund
            self.melden(f"{teil.name} ist ausgefallen ({grund}). Neustart in {pause:.0f} s (Neustart Nr. {teil.neustarts}); "
                        "die anderen Teile laufen weiter (Einzelheiten: start-fehler.log im Datenordner).", art="teil_absturz",
                        telegram=fehlversuche in (1, 3) or fehlversuche % 10 == 0)
            if self.halt.wait(pause):
                break
        teil.status = "gestoppt"

    def starten(self) -> None:
        for teil in self.teile:
            teil.faden = threading.Thread(target=self._ueberwachen, args=(teil,), name=f"kitrader-{teil.name}", daemon=True)
            teil.faden.start()

    def beenden(self, frist_s: float = BEENDEN_FRIST_S) -> list[str]:
        """Alle Teile anhalten und warten. Gibt die Namen der Teile zurück, die nicht rechtzeitig fertig wurden."""
        self.halt.set()
        for teil in self.teile:
            if teil.anhalten:
                try:
                    teil.anhalten()
                except Exception as e:
                    log.warning("%s: Anhalten fehlgeschlagen (%s)", teil.name, e)
        ende = time.monotonic() + frist_s
        haengend = []
        for teil in self.teile:
            if teil.faden is not None:
                teil.faden.join(max(0.1, min(teil.frist_s, ende - time.monotonic())))
                if teil.faden.is_alive():
                    haengend.append(teil.name)
        return haengend

    def zustand(self) -> list[tuple[str, str, str]]:
        return [(t.name, t.status, t.fehler) for t in self.teile]


# ---------------------------------------------------------------- Voreinstellungen ohne eigene Einstellungsdatei

def roh_einstellungen() -> dict:
    """Nur das, was in der eigenen Einstellungsdatei steht (ohne Standardwerte), um Voreinstellungen von Eigenem zu trennen."""
    pfad = _einst.konfig_pfad()
    try:
        with open(pfad, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _mengenschritt(symbol: str) -> float:
    basis = symbol.upper().replace("-", "/").split("/")[0].replace("_", "")
    return MENGENSCHRITT.get(basis, 0.01)


def vorgaben_anwenden(einst: dict, roh: dict, symbole: list[str] | None = None) -> tuple[dict, list[str]]:
    """Papierbetrieb ohne eigene Einstellungen soll sofort laufen: Kursquelle Kraken (öffentlich, ohne Konto), die Watchlist
    als Symbole, Krypto-Handelszeiten (rund um die Uhr) und passende Mengenschritte, Kosten wie bei Kraken Pro. Nur, was die
    eigene Einstellungsdatei nicht selbst festlegt. Gibt (Einstellungen, Liste der angewandten Vorgaben) zurück."""
    e = copy.deepcopy(einst)
    notizen: list[str] = []
    if "kursquelle" not in roh:
        e["kursquelle"] = "kraken"
        notizen.append("Kursquelle Kraken (öffentliche Kurse, kein Konto nötig)")
    if symbole:
        e["autopilot"]["symbole"] = _symbole.kanonisch_liste(symbole, e)       # BTC, Bitcoin, XBT/EUR ... -> BTC/EUR
    if e["kursquelle"] == "kraken":
        if not e["autopilot"]["symbole"] and e["watchlist"]["werte"]:
            e["autopilot"]["symbole"] = list(e["watchlist"]["werte"])
            notizen.append("Symbole = Watchlist (" + ", ".join(e["autopilot"]["symbole"]) + ")")
        for s in e["autopilot"]["symbole"]:
            if not any(s.upper() == k.upper() for k in e["maerkte"]):
                e["maerkte"][s] = "krypto"
            if not any(s.upper() == k.upper() for k in e["mengenschritte"]):
                e["mengenschritte"][s] = _mengenschritt(s)
        if "kosten" not in roh:
            e["kosten"].update(VORGABE_KOSTEN)
            notizen.append("Gebühren wie Kraken Pro (0,80 % je Ausführung), weil du keine eigenen Kosten eingestellt hast")
    return e, notizen


# ---------------------------------------------------------------- Teil: Autopilot

def _schliessen(ap) -> None:
    for objekt in (getattr(ap, "journal", None), getattr(ap, "broker", None)):
        if objekt is None:
            continue
        with contextlib.suppress(Exception):
            (objekt.beenden)()


def autopilot_teil(einst: dict, ordner: Path, modus: str, symbole: list[str] | None = None, strategie: str | None = None,
                   ausgabe: Callable[[str], None] = print, bauer: Callable | None = None) -> Teil:
    """Der Autopilot als Teil. Alles (Papierkonto, Journal) wird im eigenen Faden geöffnet und dort wieder geschlossen
    (SQLite-Verbindungen gehören zu einem Faden). Telegram-Meldungen kommen über den Beobachter-Haken; die Wache läuft als eigener Teil."""
    einst_ohne_wache = copy.deepcopy(einst)
    einst_ohne_wache["telegram"]["wache"] = False

    def lauf(halt: threading.Event) -> None:
        ap = (bauer or bauen)(modus, einst, ordner, symbole, ausgabe=ausgabe, strategie=strategie)
        ende: dict = {}
        beobachter = None
        try:
            ap.beobachter.append(lambda art, daten: ende.update(daten) if art == "ende" else None)
            ap.warten = lambda sekunden: not halt.wait(sekunden)
            if einst["ki"]["filter"]:
                try:
                    ap.strategie = _ki.KiFilter(ap.strategie, einst, ap.journal, modus, ordner=ordner)
                except _ki.KiFehler as e:
                    raise TeilEnde(str(e)) from None
            beobachter = _telegram.beobachten(ap, einst_ohne_wache, ordner)
            if beobachter:
                beobachter.starten()
            fehler = None
            try:
                ap.laufen()
            except BaseException as e:
                fehler = e
                raise
            finally:
                if beobachter:
                    beobachter.beenden(fehler)
        finally:
            _schliessen(ap)
        if halt.is_set():
            return
        text = ende.get("text") or "der Autopilot hat sich beendet"
        if ende.get("grund") == "abbruch":
            raise TeilEnde(text)                        # Einstellung oder Freigabe: ein Neustart würde nichts ändern
        raise RuntimeError(text)                        # z. B. zu viele Fehler in Folge (Netz weg): nach einer Pause neu

    return Teil("Autopilot", lauf, beschreibung={"papier": "Papierkonto (Simulation, kein echtes Geld)", "demo": "Demokonto des Brokers",
                                                 "echt": "ECHTES GELD"}.get(modus, modus))


class AutopilotSteuerung:
    """Autopilot per Knopfdruck starten und stoppen (Reiter „Autopilot“ im Dashboard) und im Auge behalten.

    Der Autopilot läuft als Teil unter einem eigenen kleinen `Start`: stürzt er ab, wird er nach einer Pause neu gestartet
    (wie in `kitrader start`); Stopp hält ihn an, ein neuer Start ist danach wieder möglich. Die Steuerung sendet selbst keine
    Orders; gehandelt wird allein vom Autopiloten, mit Risikoprüfung. Echtgeld braucht weiter Einstellung und Freigabedatei
    (`modus_pruefen`); das Dashboard startet von sich aus nur den Papierbetrieb."""

    def __init__(self, einst: dict, ordner: Path, modus: str, symbole: list[str] | None = None, strategie: str | None = None,
                 ausgabe: Callable[[str], None] = print, bauer: Callable | None = None, melden: Callable[..., None] | None = None):
        self.einst, self.ordner, self.modus = einst, Path(ordner), modus
        self.ausgabe, self.melden = ausgabe, melden
        self.teil = autopilot_teil(einst, ordner, modus, symbole, strategie, ausgabe=ausgabe, bauer=bauer)
        self.start: Start | None = None
        self.seit: datetime | None = None
        self._sperre = threading.Lock()

    def laeuft(self) -> bool:
        return self.start is not None and not self.start.halt.is_set() and self.teil.status in ("laeuft", "pause")

    def status(self) -> dict:
        t = self.teil
        gestartet = self.start is not None and not self.start.halt.is_set()
        return {"laeuft": self.laeuft(), "gestartet": gestartet, "status": t.status if gestartet else ("gestoppt" if self.start else "nicht gestartet"),
                "fehler": t.fehler, "neustarts": t.neustarts, "seit": self.seit.isoformat(timespec="seconds") if self.seit and gestartet else None,
                "beschreibung": t.beschreibung}

    def starten(self) -> dict:
        with self._sperre:
            if self.start is not None and not self.start.halt.is_set():
                raise ValueError("Der Autopilot läuft schon.")
            if not self.einst["autopilot"]["symbole"]:
                raise ValueError("Es sind keine Symbole eingestellt (autopilot.symbole in den Einstellungen oder die Watchlist).")
            try:
                modus_pruefen(self.modus, self.einst, self.ordner)
            except Abbruch as e:
                raise ValueError(str(e)) from None
            self.teil.status, self.teil.fehler, self.teil.neustarts = "wartet", "", 0
            self.start = Start(self.einst, self.ordner, [self.teil], ausgabe=self.ausgabe)
            if self.melden:
                self.start.melden = self.melden
            self.seit = datetime.now(timezone.utc)
            self.start.starten()
        return {**self.status(), "hinweis": f"Autopilot gestartet ({self.teil.beschreibung})."}

    def stoppen(self) -> dict:
        with self._sperre:
            if self.start is None or self.start.halt.is_set():
                return {**self.status(), "hinweis": "Der Autopilot lief nicht."}
            haengend = self.start.beenden(frist_s=15.0)
            self.teil.status = "gestoppt"
        return {**self.status(), "hinweis": "Autopilot gestoppt." + (f" Noch nicht fertig: {', '.join(haengend)}." if haengend else "")}


# ---------------------------------------------------------------- Teil: Oberfläche

def _server_bauen(einst: dict, ordner: Path, port: int | None, ausweichen: bool):
    from .oberflaeche import server as _server
    erster = _server.app_port(einst, port)
    letzter = None
    for p in ([erster] if (not ausweichen or erster == 0) else range(erster, erster + 10)):
        try:
            return _server.bauen(einst, ordner, p)
        except OSError as e:
            letzter = e
    raise Abbruch(f"Der Port {erster} ist nicht frei ({letzter}). Läuft die Oberfläche schon? Anderen Port wählen: "
                  "python3 -m kitrader start --port 8851")


class OberflaecheTeil:
    def __init__(self, einst: dict, ordner: Path, port: int | None = None, ausweichen: bool = True, bauer: Callable | None = None):
        self.einst, self.ordner = einst, ordner
        self._bauer = bauer or _server_bauen
        self.app, self.server = self._bauer(einst, ordner, port, ausweichen)     # früh bauen: der Port ist dann sicher frei
        self.port = self.app.port
        self.adresse = f"http://127.0.0.1:{self.port}/"
        self._laeuft = threading.Event()

    def lauf(self, halt: threading.Event) -> None:
        if self.server is None:                                        # Neustart nach einem Ausfall: gleicher Port
            self.app, self.server = self._bauer(self.einst, self.ordner, self.port, False)
        server = self.server
        try:
            self._laeuft.set()
            server.serve_forever(poll_interval=0.2)
        finally:
            self._laeuft.clear()
            with contextlib.suppress(Exception):
                server.server_close()
            self.server = None

    def anhalten(self) -> None:
        server = self.server
        if server is not None and self._laeuft.is_set():
            threading.Thread(target=server.shutdown, daemon=True).start()   # shutdown wartet auf serve_forever

    def teil(self) -> Teil:
        return Teil("Oberfläche", self.lauf, self.anhalten, beschreibung="Bedienoberfläche im Browser", frist_s=5.0)


class DashboardTeil:
    """Das Markt-Dashboard (Hauptoberfläche, Port 8812) im selben Prozess: Kurse, Charts, Papierhandel und die Reiter der
    Handelsteile (Trends, KI, Weltlage, Autopilot). Die Sammler im Dashboard laufen ab dem Bauen; der Server wird als Teil überwacht."""

    def __init__(self, einst: dict, ordner: Path, port: int | None = None, steuerung: AutopilotSteuerung | None = None,
                 hosts: str = "127.0.0.1", ausweichen: bool = True):
        from .markt import ports as _ports
        from .markt.dashboard import server as _dashboard
        self.einst, self.ordner, self.steuerung, self.hosts = einst, ordner, steuerung, hosts
        erster = _ports.DASHBOARD if port is None else port
        self._dashboard = _dashboard
        letzter = None
        self.dash = None
        for p in ([erster] if (not ausweichen or erster == 0) else range(erster, erster + 10)):
            try:
                self.dash = _dashboard.bauen(p, hosts, einst, ordner, steuerung=steuerung, vorbereitet=True)
                break
            except OSError as e:
                letzter = e
        if self.dash is None:
            raise Abbruch(f"Der Port {erster} ist nicht frei ({letzter}). Läuft das Dashboard schon? Anderen Port wählen: "
                          "python3 -m kitrader start --port 8830")
        self.port = self.dash.port
        self.adresse = f"http://127.0.0.1:{self.port}/"
        self._laeuft = threading.Event()

    def lauf(self, halt: threading.Event) -> None:
        if self.dash is None:                                          # Neustart nach einem Ausfall: gleicher Port
            self.dash = self._dashboard.bauen(self.port, self.hosts, self.einst, self.ordner, steuerung=self.steuerung, vorbereitet=True)
        d = self.dash
        try:
            self._laeuft.set()
            d.dienen(halt, ausgabe=lambda *_: None)
        finally:
            self._laeuft.clear()
            for s in d.server:
                with contextlib.suppress(Exception):
                    s.server_close()
            self.dash = None

    def anhalten(self) -> None:
        if self.dash is not None and self._laeuft.is_set():
            self.dash.anhalten()

    def teil(self) -> Teil:
        return Teil("Dashboard", self.lauf, self.anhalten, beschreibung="Markt-Dashboard im Browser (Hauptoberfläche)", frist_s=5.0)


def dienst_teil(name: str, befehl: list[str], umgebung: dict | None = None, cwd: Path | None = None, beschreibung: str = "") -> Teil:
    """Ein Hilfsdienst als eigener Prozess (Analysen, Claude-Agent), wird mit dem Start beendet."""
    import subprocess
    proz: dict = {}

    def lauf(halt: threading.Event) -> None:
        p = subprocess.Popen(befehl, cwd=cwd, env={**os.environ, **(umgebung or {})}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proz["p"] = p
        try:
            while not halt.wait(1.0):
                if p.poll() is not None:
                    raise RuntimeError(f"beendet mit Code {p.returncode}")
        finally:
            if p.poll() is None:
                p.terminate()
                with contextlib.suppress(Exception):
                    p.wait(8)
                if p.poll() is None:
                    p.kill()

    return Teil(name, lauf, beschreibung=beschreibung, frist_s=12.0)


def hilfsdienste(port: int, host: str = "127.0.0.1") -> list[Teil]:
    """Analysen (Port+1, Reiter „Analysen“ und Charts) und, wenn Node und `agent/node_modules` da sind, der Claude-Agent (Port+2)."""
    quelle = Path(__file__).resolve().parent.parent
    umgebung = {"KITRADER_MARKT_PORT": str(port), "PYTHONPATH": os.pathsep.join(filter(None, [str(quelle), os.environ.get("PYTHONPATH", "")]))}
    teile = [dienst_teil("Analysen", [sys.executable, "-m", "kitrader.markt.analysen", "--port", str(port + 1), "--host", host], umgebung,
                         beschreibung=f"Analysen und Alarme (Port {port + 1})")]
    agent = quelle.parent / "agent"
    node = shutil.which("node")
    if node and (agent / "node_modules").exists():
        teile.append(dienst_teil("Claude-Agent", [node, "server.mjs"], {**umgebung, "PORT": str(port + 2), "HOST": host}, agent,
                                 beschreibung=f"Claude als Chat im Dashboard (Port {port + 2})"))
    return teile


# ---------------------------------------------------------------- Teil: Telegram-Wache

def wache_teil(einst: dict, ordner: Path, modus: str, ausgabe: Callable[[str], None] = print,
               client=None, chat_id: str | None = None) -> Teil | None:
    """Die Wache (Not-Aus per „stop“) als eigener Teil, damit sie auch weiterläuft, wenn der Autopilot ausfällt.
    None, wenn Telegram aus oder nicht eingerichtet ist."""
    if not einst["telegram"]["aktiv"]:
        return None
    if client is None:
        zugang = _telegram.zugang_lesen()
        if not zugang.bereit:
            return None
        client, chat_id = _telegram.client_aus(einst, zugang), zugang.chat_id
    wache = _telegram.Wache(client, chat_id, einst, ordner, modus, ausgabe=lambda text: ausgabe(text))
    return Teil("Telegram-Wache", lambda halt: wache.lauf(halt), lambda: wache.fertig_warten(15),
                beschreibung="'stop' = Not-Aus, 'status' = Übersicht", frist_s=3.0)


# ---------------------------------------------------------------- Teil: Zeitplan

def _zone(name: str) -> tuple[tzinfo, str]:
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, ValueError, OSError):
        lokal = datetime.now().astimezone().tzinfo or timezone.utc
        return lokal, f"Ortszeit dieses Rechners (Zeitzone {name!r} nicht gefunden)"


class _Abruf:
    """Ein wiederkehrender Abruf des Zeitplans nach dem Muster von OSIRIS: alle `intervall` (0 = aus), beim ersten Mal erst, wenn
    der gespeicherte Stand so alt ist; bei einem Fehlschlag Meldung und neuer Versuch nach `NACHRICHTEN_WIEDERHOLUNG`. Fehlt das Modul
    (FileNotFoundError), wird der Auftrag einmal gemeldet und übersprungen."""

    def __init__(self, name: str, intervall: timedelta, stand: Callable[[], datetime | None], abrufen: Callable[[], dict],
                 auswerten: Callable[[dict], str], melden: Callable[..., None]):
        self.name, self.intervall, self.melden = name, intervall, melden
        self._stand, self._abrufen, self._auswerten = stand, abrufen, auswerten
        self.naechster: datetime | None = None
        self.uebersprungen = False

    def pruefen(self, jetzt: datetime) -> str | None:
        if self.intervall <= timedelta(0) or self.uebersprungen:       # alle_h = 0: aus
            return None
        if self.naechster is None:
            try:
                stand = self._stand()
            except Exception:                                          # kein Stand lesbar (auch: Modul fehlt): gleich abrufen
                stand = None
            self.naechster = zu_utc(stand) + self.intervall if stand else jetzt
        if jetzt < self.naechster:
            return None
        try:
            erg = self._abrufen()
        except FileNotFoundError as e:
            self.uebersprungen = True
            self.melden(f"Zeitplan: {self.name} übersprungen: {e}.", art="zeitplan")
            return self.name.lower()
        except Exception as e:
            self.naechster = jetzt + NACHRICHTEN_WIEDERHOLUNG
            self.melden(f"Zeitplan: {self.name}-Abruf fehlgeschlagen ({type(e).__name__}: {e}). Neuer Versuch in 30 Minuten.", art="zeitplan")
            return self.name.lower()
        self.naechster = jetzt + self.intervall
        self.melden(f"Zeitplan: {self.name} " + (self._auswerten(erg) if isinstance(erg, dict) else "abgerufen") +
                    f". Nächster Abruf in {self.intervall.total_seconds() / 3600:g} Std.", art="zeitplan")
        return self.name.lower()


def _modul(name: str):
    try:
        return importlib.import_module(f"kitrader.{name}")
    except ImportError:
        raise FileNotFoundError(f"{name}.py ist noch nicht vorhanden") from None


def _nachrichten_text(erg: dict) -> str:
    if not erg.get("neu", True):
        return "ist noch frisch"
    quellen = erg.get("quellen", {})
    schlecht = [n for n, q in quellen.items() if q.get("fehler")]
    return (f"neu abgerufen ({erg.get('neue_meldungen', 0)} neue Meldungen aus {len(quellen)} Feeds"
            + (f", {len(schlecht)} mit Fehler" if schlecht else "") + ")")


def _welt_text(erg: dict) -> str:
    if not erg.get("neu", True):
        return "ist noch frisch"
    schlecht = [n for n, t in erg.get("quellen", {}).items() if not str(t).startswith("ok")]
    return f"neuer Schnappschuss ({erg.get('zeilen', 0)} Märkte" + (f", {len(schlecht)} Quellen mit Fehler: {', '.join(schlecht)}" if schlecht else "") + ")"


class Zeitplan:
    """OSIRIS alle `zeitplan.osiris_alle_h` Stunden, Nachrichten-Radar (`nachrichten_alle_h`) und Welt-Rotation (`welt_alle_h`) und die
    Morgenlage täglich um `zeitplan.morgenlage` (Ortszeit von `zeitplan.zeitzone`). Die Uhr ist austauschbar (Tests). Fällt ein Auftrag
    aus, kommt eine Meldung und später ein neuer Versuch."""

    def __init__(self, einst: dict, ordner: Path, uhr: Callable[[], datetime] = jetzt_utc,
                 melden: Callable[..., None] | None = None, osiris_stand: Callable[[], datetime | None] | None = None,
                 osiris_abrufen: Callable[[], dict] | None = None, morgenlage: Callable[[str], tuple] | None = None,
                 morgenlage_datei: Path | None = None, nachrichten_stand: Callable[[], datetime | None] | None = None,
                 nachrichten_abrufen: Callable[[], dict] | None = None, welt_stand: Callable[[], datetime | None] | None = None,
                 welt_abrufen: Callable[[], dict] | None = None):
        self.einst, self.ordner = einst, Path(ordner)
        z = einst["zeitplan"]
        self.uhr = uhr
        self.melden = melden or (lambda text, **kw: print(text))
        self.zone, self.zone_text = _zone(z["zeitzone"])
        self.osiris_intervall = timedelta(hours=float(z["osiris_alle_h"]))
        self.morgenlage_zeit = z["morgenlage"]
        self._osiris_stand = osiris_stand or self._osiris_stand_lesen
        self._osiris_abrufen = osiris_abrufen or self._osiris_abrufen_echt
        self._morgenlage = morgenlage or self._morgenlage_echt
        self.morgenlage_datei = Path(morgenlage_datei) if morgenlage_datei else self.ordner / "morgenlage.json"
        self.osiris_naechster: datetime | None = None
        self.nachrichten = _Abruf("Nachrichten", timedelta(hours=float(z["nachrichten_alle_h"])), nachrichten_stand or self._nachrichten_stand_lesen,
                                  nachrichten_abrufen or self._nachrichten_abrufen_echt, _nachrichten_text, self.melden)
        self.welt = _Abruf("Welt", timedelta(hours=float(z["welt_alle_h"])), welt_stand or self._welt_stand_lesen,
                           welt_abrufen or self._welt_abrufen_echt, _welt_text, self.melden)
        self._ml_versuche: dict[str, tuple[int, datetime]] = {}      # Datum -> (Versuche, Zeit des letzten)
        self._ml_erledigt: str | None = None
        self._ml_hinweis_tag: str | None = None

    # ---- OSIRIS

    def _osiris_stand_lesen(self) -> datetime | None:
        o = _osiris.Osiris(self.einst, self.ordner / "osiris.db")
        try:
            return o.lage().stand
        finally:
            o.beenden()

    def _osiris_abrufen_echt(self) -> dict:
        o = _osiris.Osiris(self.einst, self.ordner / "osiris.db")
        try:
            return o.abrufen(erzwingen=False)
        finally:
            o.beenden()

    def _osiris_pruefen(self, jetzt: datetime) -> str | None:
        if self.osiris_intervall <= timedelta(0):                      # osiris_alle_h = 0: aus
            return None
        if self.osiris_naechster is None:
            try:
                stand = self._osiris_stand()
            except Exception:
                stand = None
            self.osiris_naechster = zu_utc(stand) + self.osiris_intervall if stand else jetzt
        if jetzt < self.osiris_naechster:
            return None
        try:
            erg = self._osiris_abrufen()
        except Exception as e:
            self.osiris_naechster = jetzt + OSIRIS_WIEDERHOLUNG
            self.melden(f"Zeitplan: OSIRIS-Abruf fehlgeschlagen ({type(e).__name__}: {e}). Neuer Versuch in 30 Minuten.", art="zeitplan")
            return "osiris"
        self.osiris_naechster = jetzt + self.osiris_intervall
        feeds = erg.get("feeds", {}) if isinstance(erg, dict) else {}
        schlecht = [n for n, f in feeds.items() if f.get("fehler")]
        self.melden("Zeitplan: OSIRIS-Weltlage " + ("neu abgerufen" if erg.get("neu", True) else "ist noch frisch") +
                    (f" ({len(feeds)} Feeds" + (f", {len(schlecht)} mit Fehler: {', '.join(schlecht)}" if schlecht else "") + ")" if feeds else "") +
                    f". Nächster Abruf in {self.osiris_intervall.total_seconds() / 3600:g} Std.", art="zeitplan")
        return "osiris"

    # ---- Nachrichten-Radar und Welt-Rotation

    def _nachrichten_stand_lesen(self) -> datetime | None:
        n = _modul("nachrichten").Nachrichten(self.einst, self.ordner / "nachrichten.db")
        try:
            return n.letzter_abruf()
        finally:
            n.beenden()

    def _nachrichten_abrufen_echt(self) -> dict:
        n = _modul("nachrichten").Nachrichten(self.einst, self.ordner / "nachrichten.db")
        try:
            return n.abrufen(erzwingen=False)
        finally:
            n.beenden()

    def _welt_stand_lesen(self) -> datetime | None:
        w = _modul("welt").Welt(self.einst, self.ordner / "welt.db", historie_pfad=self.ordner / "historie.db")
        try:
            return w.letzter_stand()
        finally:
            w.beenden()

    def _welt_abrufen_echt(self) -> dict:
        w = _modul("welt").Welt(self.einst, self.ordner / "welt.db", historie_pfad=self.ordner / "historie.db")
        try:
            return w.abrufen(erzwingen=False)
        finally:
            w.beenden()

    # ---- Morgenlage

    def morgenlage_geplant(self, jetzt: datetime) -> datetime | None:
        """Der heutige Zeitpunkt der Morgenlage (UTC) oder None, wenn sie aus ist."""
        if not self.morgenlage_zeit:
            return None
        lokal = zu_utc(jetzt).astimezone(self.zone)
        stunde, minute = (int(x) for x in self.morgenlage_zeit.split(":"))
        return lokal.replace(hour=stunde, minute=minute, second=0, microsecond=0).astimezone(timezone.utc)

    def _morgenlage_echt(self, treiber: str | None = None) -> tuple:
        try:
            lage = importlib.import_module("kitrader.lage")
        except ImportError:
            raise FileNotFoundError("lage.py ist noch nicht vorhanden") from None
        ausfuehren = getattr(lage, "ausfuehren", None)
        if not callable(ausfuehren):
            raise FileNotFoundError("lage.py hat keine Funktion ausfuehren(einst, ordner)")
        return ausfuehren(self.einst, self.ordner, treiber=treiber)

    def _morgenlage_pruefen(self, jetzt: datetime) -> str | None:
        geplant = self.morgenlage_geplant(jetzt)
        if geplant is None or jetzt < geplant:
            return None
        tag = geplant.astimezone(self.zone).date().isoformat()
        if self._ml_erledigt == tag:
            return None
        try:                                              # heute nach der Uhrzeit schon geschrieben (z. B. vor einem Neustart)?
            if datetime.fromtimestamp(self.morgenlage_datei.stat().st_mtime, tz=timezone.utc) >= geplant:
                self._ml_erledigt = tag
                return None
        except OSError:
            pass
        versuche, zuletzt = self._ml_versuche.get(tag, (0, jetzt - MORGENLAGE_WIEDERHOLUNG))
        if versuche >= MORGENLAGE_VERSUCHE or jetzt - zuletzt < MORGENLAGE_WIEDERHOLUNG:
            return None
        self._ml_versuche[tag] = (versuche + 1, jetzt)
        self.melden(f"Zeitplan: Morgenlage {self.morgenlage_zeit} ({self.zone_text}) wird erstellt ...", art="zeitplan")
        try:
            try:
                ergebnis = self._morgenlage(None)
            except _ki.KiFehler as e:                     # KI nicht erreichbar: die Lage ohne KI ist besser als keine
                self.melden(f"Zeitplan: Morgenlage ohne KI-Einschätzung, weil die KI nicht geht ({e})", art="zeitplan")
                ergebnis = self._morgenlage("aus")
        except FileNotFoundError as e:
            if self._ml_hinweis_tag != tag:
                self._ml_hinweis_tag = tag
                self.melden(f"Zeitplan: Morgenlage übersprungen: {e}.", art="zeitplan")
            self._ml_versuche[tag] = (MORGENLAGE_VERSUCHE, jetzt)
            return "morgenlage"
        except Exception as e:
            self.melden(f"Zeitplan: Morgenlage fehlgeschlagen ({type(e).__name__}: {e})."
                        + (" Neuer Versuch in 30 Minuten." if versuche + 1 < MORGENLAGE_VERSUCHE else " Für heute aufgegeben."),
                        art="zeitplan", telegram=versuche + 1 >= MORGENLAGE_VERSUCHE)
            return "morgenlage"
        self._ml_erledigt = tag
        pfad = ergebnis[1] if isinstance(ergebnis, tuple) and len(ergebnis) > 1 else None
        self.melden(f"Zeitplan: Morgenlage fertig" + (f" ({pfad})" if pfad else "") + ".", art="zeitplan")
        return "morgenlage"

    # ---- Schleife

    def schritt(self, jetzt: datetime | None = None) -> list[str]:
        """Führt aus, was fällig ist; gibt die Namen der ausgeführten Aufträge zurück."""
        jetzt = zu_utc(jetzt or self.uhr())
        return [n for n in (self._osiris_pruefen(jetzt), self.nachrichten.pruefen(jetzt), self.welt.pruefen(jetzt),
                            self._morgenlage_pruefen(jetzt)) if n]

    def lauf(self, halt: threading.Event) -> None:
        while not halt.is_set():
            self.schritt()
            halt.wait(ZEITPLAN_TAKT_S)

    def beschreibung(self) -> str:
        teile = [f"OSIRIS alle {self.osiris_intervall.total_seconds() / 3600:g} Std." if self.osiris_intervall > timedelta(0) else "OSIRIS aus"]
        teile.append(f"Nachrichten alle {self.nachrichten.intervall.total_seconds() / 3600:g} Std." if self.nachrichten.intervall > timedelta(0) else "Nachrichten aus")
        teile.append(f"Welt alle {self.welt.intervall.total_seconds() / 3600:g} Std." if self.welt.intervall > timedelta(0) else "Welt aus")
        teile.append(f"Morgenlage täglich {self.morgenlage_zeit} ({self.zone_text})" if self.morgenlage_zeit else "Morgenlage aus")
        return ", ".join(teile)

    def teil(self) -> Teil:
        return Teil("Zeitplan", self.lauf, beschreibung=self.beschreibung(), frist_s=3.0)


# ---------------------------------------------------------------- Startausgabe

_GLYPHEN = {
    "P": [" ____  ", "|  _ \\ ", "| |_) |", "|  __/ ", "|_|    "],
    "A": ["    _    ", "   / \\   ", "  / _ \\  ", " / ___ \\ ", "/_/   \\_\\"],
    "I": [" ___ ", "|_ _|", " | | ", " | | ", "|___|"],
    "E": [" _____ ", "| ____|", "|  _|  ", "| |___ ", "|_____|"],
    "R": [" ____  ", "|  _ \\ ", "| |_) |", "|  _ < ", "|_| \\_\\"],
    "D": [" ____  ", "|  _ \\ ", "| | | |", "| |_| |", "|____/ "],
    "M": [" __  __ ", "|  \\/  |", "| |\\/| |", "| |  | |", "|_|  |_|"],
    "O": ["  ___  ", " / _ \\ ", "| | | |", "| |_| |", " \\___/ "],
    "C": ["  ____ ", " / ___|", "| |    ", "| |___ ", " \\____|"],
    "H": [" _   _ ", "| | | |", "| |_| |", "|  _  |", "|_| |_|"],
    "T": [" _____ ", "|_   _|", "  | |  ", "  | |  ", "  |_|  "],
}


def gross_schreiben(wort: str) -> str:
    return "\n".join("".join(_GLYPHEN[b][z] for b in wort).rstrip() for z in range(5))


def _farbe(text: str, code: str) -> str:
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
        return f"\033[{code}m{text}\033[0m"
    return text


def betriebsart_block(modus: str) -> str:
    wort = {"papier": "PAPIER", "demo": "DEMO", "echt": "ECHT"}[modus]
    gross = gross_schreiben(wort)
    if modus == "echt":
        balken = "!" * 78
        return "\n".join([_farbe(balken, "1;31"), _farbe(gross, "1;31"),
                          _farbe("  BETRIEBSART ECHT  -  ECHTES GELD  -  Orders werden mit echtem Geld ausgeführt!", "1;31"), _farbe(balken, "1;31")])
    farbe, text = ("1;34", "Simulation mit echten Kursen - kein echtes Geld") if modus == "papier" else \
        ("1;33", "Demokonto des Brokers - kein echtes Geld")
    return "\n".join([_farbe("=" * 78, farbe), _farbe(gross, farbe), _farbe("  BETRIEBSART " + wort + "  -  " + text, farbe), _farbe("=" * 78, farbe)])


def startausgabe(modus: str, einst: dict, ordner: Path, adresse: str | None, teile: list[Teil], fehlt: list[str],
                 vorgaben: list[str]) -> str:
    z = [betriebsart_block(modus), ""]
    ap = einst["autopilot"]
    z.append(f"  Dashboard    {adresse or '(aus)'}" + ("   (öffnet sich im Browser)" if adresse and einst["oberflaeche"]["browser_oeffnen"] else ""))
    laeuft = [f"{t.name}" + (f"   {t.beschreibung}" if t.beschreibung else "") for t in teile]
    z.append("  Läuft        " + ("\n               ".join(laeuft) if laeuft else "nichts"))
    z.append("  Handel       Strategie " + einst["strategie"]["name"] + ", Symbole " + (", ".join(ap["symbole"]) or "(keine)") + ", Takt " + ap["takt"]
             + ", Kursquelle " + einst["kursquelle"])
    if fehlt:
        z.append("  Fehlt        " + "\n               ".join(fehlt))
    if vorgaben:
        z.append("  Vorgaben     " + "\n               ".join(vorgaben) + "\n               (weil nichts anderes eingestellt ist; eigene Werte in "
                 + str(_einst.konfig_pfad()) + ")")
    z.append(f"  Datenordner  {ordner}")
    z.append("  Not-Aus      roter Knopf im Dashboard (Reiter Autopilot)  oder  python3 -m kitrader notaus")
    z.append("  Beenden      Strg+C in diesem Fenster (alles wird sauber gestoppt)  oder  python3 -m kitrader start --beenden")
    z.append("")
    z.append("  Keine Anlageberatung, kein Erfolgsversprechen. Die mitgelieferten Strategien verlieren nach Kosten Geld (STRATEGIE-BERICHT.md);")
    z.append("  der Papierhandel ist ein Technik-Test.")
    return "\n".join(z)


def fehlendes(einst: dict, modus: str, quelle_pruefen: Callable[[], str | None] | None = None) -> list[str]:
    """Was zum vollen Betrieb noch fehlt (jeweils mit dem Befehl zum Beheben)."""
    fehlt = []
    t = einst["telegram"]
    if not t["aktiv"]:
        fehlt.append("Telegram ist in den Einstellungen ausgeschaltet: keine Meldungen aufs Handy, kein Not-Aus per 'stop'")
    elif not _telegram.zugang_lesen().bereit:
        fehlt.append("Telegram ist nicht eingerichtet: keine Meldungen aufs Handy, kein Not-Aus per 'stop'  ->  python3 -m kitrader telegram einrichten")
    if einst["zeitplan"]["morgenlage"] and importlib.util.find_spec("kitrader.lage") is None:
        fehlt.append("Morgenlage: lage.py ist noch nicht vorhanden, der Zeitplan überspringt sie")
    if einst["ki"]["treiber"] == "claude_cli" and shutil.which(einst["ki"]["claude_befehl"]) is None:
        fehlt.append(f"KI: die Claude-CLI '{einst['ki']['claude_befehl']}' wurde nicht gefunden: Morgenlage und KI-Einschätzungen laufen ohne KI")
    if quelle_pruefen:
        problem = quelle_pruefen()
        if problem:
            fehlt.append("Kurse: " + problem)
    return fehlt


def quelle_testen(einst: dict, frist_s: float = 8.0) -> str | None:
    """Ein Testabruf bei der Kursquelle (höchstens `frist_s` Sekunden). None = erreichbar, sonst ein Satz mit dem Problem."""
    symbole = einst["autopilot"]["symbole"] or einst["watchlist"]["werte"]
    if not symbole:
        return None
    erg: list = []

    def abrufen():
        try:
            from .kursquelle import quelle_aus_einstellungen
            erg.append(quelle_aus_einstellungen(einst).kurs(symbole[0]))
        except Exception as e:
            erg.append(e)

    faden = threading.Thread(target=abrufen, daemon=True)
    faden.start()
    faden.join(frist_s)
    if faden.is_alive():
        return f"{einst['kursquelle']} antwortet nicht innerhalb von {frist_s:.0f} Sekunden (kein Netz?)"
    if isinstance(erg[0], Exception):
        return f"{einst['kursquelle']} ist nicht erreichbar ({erg[0]})"
    return None


# ---------------------------------------------------------------- Der Befehl

def _signale(halt: threading.Event, ausgabe: Callable[[str], None]) -> None:
    if threading.current_thread() is not threading.main_thread():
        return

    def behandeln(nummer, rahmen):
        if halt.is_set():
            ausgabe("\nZweites Signal: kitrader wird sofort beendet.")
            os._exit(130)
        ausgabe("\nBeende kitrader ... (noch einmal Strg+C = sofort)")
        halt.set()

    signal.signal(signal.SIGINT, behandeln)
    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGTERM, behandeln)


def befehl(args, einst: dict, ordner: Path, ausgabe: Callable[[str], None] = print, halt: threading.Event | None = None,
           teile_bauer: Callable | None = None) -> int:
    """`python3 -m kitrader start`. `halt` und `teile_bauer` sind für Tests (kein Signal, eigene Teile)."""
    ordner = Path(ordner)
    if getattr(args, "beenden", False):
        return laufenden_beenden(ordner, ausgabe)
    modus = args.modus or einst["modus"]
    if modus not in _einst.MODI:
        raise Abbruch(f"Unbekannte Betriebsart {modus!r}. Erlaubt: {', '.join(_einst.MODI)}")
    modus_pruefen(modus, einst, ordner)               # Echtgeld nur mit Einstellung UND Freigabedatei von heute, sonst startet nichts
    sperre = Sperre(ordner)
    sperre.holen({"seit": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "modus": modus})
    try:
        if not logging.getLogger().handlers:
            logging.basicConfig(level=logging.WARNING, format="[%(asctime)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
        return _laufen(args, einst, ordner, modus, sperre, ausgabe, halt or threading.Event(), teile_bauer, signale=halt is None)
    finally:
        sperre.freigeben()
        _telegram.leeren(5)


def _laufen(args, einst, ordner, modus, sperre, ausgabe, halt, teile_bauer, signale: bool = True) -> int:
    vorgaben: list[str] = []
    e = copy.deepcopy(einst)
    if modus == "papier":
        e, vorgaben = vorgaben_anwenden(einst, roh_einstellungen(), getattr(args, "symbole", None))
    elif getattr(args, "symbole", None):
        e["autopilot"]["symbole"] = _symbole.kanonisch_liste(args.symbole, e)
    e["modus"] = modus                                 # die Oberfläche und der Not-Aus beziehen sich auf diese Betriebsart
    teile: list[Teil] = []
    start = Start(e, ordner, teile, ausgabe=ausgabe, halt=halt)
    ob = None
    steuerung = None
    fehlt_vorab: list[str] = []
    if teile_bauer:                                   # Tests: eigene Teile statt der echten
        teile[:] = teile_bauer(e, ordner, start)
    else:
        if e["autopilot"]["symbole"]:
            steuerung = AutopilotSteuerung(e, ordner, modus, None, getattr(args, "strategie", None), ausgabe=ausgabe, melden=start.melden)
        else:
            fehlt_vorab.append("Autopilot: keine Symbole eingestellt (autopilot.symbole in den Einstellungen oder --symbole): es wird nichts gehandelt")
        if getattr(args, "alte_oberflaeche", False):      # die kleine Oberfläche (Port 8850) nur auf Wunsch, zusätzlich zum Dashboard
            alte = OberflaecheTeil(e, ordner, None, ausweichen=True)
            teile.append(alte.teil())
            ausgabe(f"Kleine Bedienoberfläche (optional): {alte.adresse}")
        if not getattr(args, "ohne_oberflaeche", False):
            ob = DashboardTeil(e, ordner, getattr(args, "port", None), steuerung, hosts=getattr(args, "host", None) or "127.0.0.1",
                               ausweichen=getattr(args, "port", None) is None)
            teile.append(ob.teil())
            if not getattr(args, "ohne_hilfsdienste", False):
                teile += hilfsdienste(ob.port)
        wache = wache_teil(e, ordner, modus, ausgabe=ausgabe)
        if wache:
            teile.append(wache)
        teile.append(Zeitplan(e, ordner, melden=start.melden).teil())
    sperre.schreiben({"seit": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "modus": modus,
                      "adresse": ob.adresse if ob else None})
    fehlt = fehlt_vorab + fehlendes(e, modus, (lambda: quelle_testen(e)) if modus == "papier" and not teile_bauer else None)
    ausgabe(startausgabe(modus, e, ordner, ob.adresse if ob else None, ([steuerung.teil] if steuerung else []) + teile, fehlt, vorgaben))
    if signale:
        _signale(halt, ausgabe)
    start.melden(f"kitrader start: Betriebsart {modus.upper()}, " + ", ".join(t.name for t in ([steuerung.teil] if steuerung else []) + teile), art="start")
    start.starten()
    if steuerung:
        try:
            steuerung.starten()
        except ValueError as fehler:
            start.melden(f"Autopilot startet nicht: {fehler}", art="teil_ende", telegram=True)
    if ob and e["oberflaeche"]["browser_oeffnen"] and not getattr(args, "nicht_oeffnen", False):
        threading.Timer(1.0, lambda: webbrowser.open(ob.adresse)).start()
    try:
        while not halt.wait(0.5):
            pass
    except KeyboardInterrupt:
        halt.set()
    haengend = start.beenden()
    if steuerung and "Noch nicht fertig" in steuerung.stoppen()["hinweis"]:
        haengend.append("Autopilot")
    if haengend:
        ausgabe("Noch nicht fertig, wird mit dem Programm beendet: " + ", ".join(haengend) + ".")
    start.melden("kitrader start: beendet.", art="ende")
    ausgabe("kitrader ist beendet."
            + (" Hinweis: Im Papierbetrieb werden Stops und Ziele nur geprüft, solange kitrader läuft." if modus == "papier" else
               " Offene Positionen behalten ihre Schutz-Stops beim Broker."))
    return 0


def argumente(p) -> None:
    p.add_argument("--modus", choices=_einst.MODI, help="Betriebsart (Standard: aus den Einstellungen, sonst papier). "
                                                         "echt nur mit modus = \"echt\" in den Einstellungen UND Freigabedatei von heute")
    p.add_argument("--port", type=int, help="Port des Dashboards (Standard 8812; ist er belegt, wird der nächste freie genommen)")
    p.add_argument("--host", help="Adressen des Dashboards, kommagetrennt (Standard 127.0.0.1; mit Tailscale z. B. 127.0.0.1,<Tailscale-IP>)")
    p.add_argument("--nicht-oeffnen", action="store_true", help="den Browser nicht selbst öffnen")
    p.add_argument("--ohne-oberflaeche", action="store_true", help="ohne Dashboard starten")
    p.add_argument("--ohne-hilfsdienste", action="store_true", help="ohne Analysen-Dienst und Claude-Agent (nur das Dashboard)")
    p.add_argument("--alte-oberflaeche", action="store_true", help="zusätzlich die kleine Bedienoberfläche (Port 8850) starten")
    p.add_argument("--symbole", nargs="+", help="Symbole des Autopiloten (Standard: autopilot.symbole, sonst die Watchlist)")
    p.add_argument("--strategie", choices=sorted(STRATEGIEN), help="Strategie des Autopiloten (Standard aus den Einstellungen)")
    p.add_argument("--beenden", action="store_true", help="den laufenden Start in diesem Datenordner beenden")
