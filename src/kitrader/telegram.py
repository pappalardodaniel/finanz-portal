"""Telegram: Meldungen vom Autopiloten und Not-Aus per Nachricht.

Zugang (nie ins Repo, nie in eine Ausgabe): Datei `telegram.env` neben der Einstellungsdatei
(`~/.config/kitrader/telegram.env`, `chmod 600`) mit den Zeilen
    TELEGRAM_TOKEN=123456:ABC...        (vom BotFather)
    TELEGRAM_CHAT_ID=123456789          (dein Chat mit dem Bot)
Einrichten: `python3 -m kitrader telegram einrichten`.

* `melden(text)` sendet im Hintergrund; Fehler werden nur protokolliert und halten nie den Handel auf.
* `beobachten(autopilot, ...)` trägt sich in den Beobachter-Haken des Autopiloten ein (`Autopilot.beobachter`, das
  Journal bleibt unberührt) und meldet Start/Stopp, Ausführungen, Ablehnungen der Risikoprüfung (zusammengefasst),
  Tagesverlustgrenze, erreichte Gewinnziele, Not-Aus, Tagesabschluss.
* `Wache` fragt per Long-Polling ab. Nur Nachrichten aus der erlaubten Chat-ID zählen. "stop" löst den Not-Aus
  aus, "status" schickt eine kurze Übersicht; alles andere wird ignoriert.
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import betrieb
from . import einstellungen as _einst
from .hilfen import zahl

log = logging.getLogger("kitrader.telegram")

MAX_TEXT = 3900                       # Telegram erlaubt 4096 Zeichen je Nachricht
MIN_ABSTAND_S = 1.0                   # Telegram mag nicht mehr als etwa eine Nachricht pro Sekunde je Chat
STOP_WOERTER = ("stop", "stopp")
STATUS_WOERTER = ("status",)
TOKEN_FORM = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")


class TelegramFehler(RuntimeError):
    """Telegram nicht erreichbar, nicht eingerichtet oder abgelehnt. Der Text enthält nie den Token."""


# ---------------------------------------------------------------- Zugang

def zugang_datei() -> Path:
    return _einst.konfig_pfad().with_name("telegram.env")


@dataclass
class Zugang:
    token: str = field(default="", repr=False)       # nie in Ausgaben
    chat_id: str = ""

    @property
    def bereit(self) -> bool:
        return bool(self.token and self.chat_id)

    def __repr__(self) -> str:
        return f"Zugang(token={'***' if self.token else ''}, chat_id={self.chat_id!r})"

    __str__ = __repr__


def _zeilen_lesen(datei: Path) -> dict[str, str]:
    werte: dict[str, str] = {}
    try:
        text = datei.read_text(encoding="utf-8")
    except OSError:
        return werte
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#"):
            continue
        name, _, wert = zeile.removeprefix("export ").partition("=")
        werte[name.strip()] = wert.strip().strip("\"'")
    return werte


def zugang_lesen(umgebung: dict | None = None, datei: Path | None = None) -> Zugang:
    """TELEGRAM_TOKEN und TELEGRAM_CHAT_ID aus der Umgebung, sonst aus telegram.env."""
    umgebung = os.environ if umgebung is None else umgebung
    aus_datei = _zeilen_lesen(datei or zugang_datei())
    token = (umgebung.get("TELEGRAM_TOKEN") or aus_datei.get("TELEGRAM_TOKEN") or "").strip()
    chat = (umgebung.get("TELEGRAM_CHAT_ID") or aus_datei.get("TELEGRAM_CHAT_ID") or "").strip()
    return Zugang(token, chat)


def rechte_hinweis(datei: Path) -> str:
    """Hinweis, wenn andere Benutzer die Zugangsdatei lesen könnten (sonst leer)."""
    try:
        if datei.stat().st_mode & 0o077:
            return f"Die Datei {datei} ist für andere lesbar. Bitte: chmod 600 {datei}"
    except OSError:
        pass
    return ""


def zugang_speichern(datei: Path, werte: dict[str, str]) -> None:
    """Schreibt/ergänzt Zeilen NAME=WERT, behält andere Zeilen, Rechte 600 (auch bei neuer Datei von Anfang an)."""
    datei.parent.mkdir(parents=True, exist_ok=True)
    vorhanden = datei.read_text(encoding="utf-8").splitlines() if datei.is_file() else []
    neu, gesetzt = [], set()
    for zeile in vorhanden:
        name = zeile.strip().removeprefix("export ").partition("=")[0].strip()
        if name in werte:
            if name not in gesetzt:
                neu.append(f"{name}={werte[name]}")
                gesetzt.add(name)
        else:
            neu.append(zeile)
    neu += [f"{n}={w}" for n, w in werte.items() if n not in gesetzt]
    temp = datei.with_name(datei.name + ".neu")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(neu) + "\n")
    os.chmod(temp, 0o600)
    os.replace(temp, datei)


def _maskieren(text: str, token: str = "") -> str:
    """Entfernt den Token und jede Bot-Adresse aus einem Text, bevor er irgendwo ausgegeben wird."""
    if token:
        text = text.replace(token, "***")
    return re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot***", text)


# ---------------------------------------------------------------- Anfragen an Telegram

class Telegram:
    """Dünner Client für die Bot-API (sendMessage, getUpdates, getMe)."""

    def __init__(self, token: str, api_url: str = "https://api.telegram.org", zeitlimit_s: float = 10):
        self._token = token
        self.api_url = api_url.rstrip("/")
        self.zeitlimit_s = zeitlimit_s
        host = urllib.parse.urlsplit(self.api_url).hostname or ""
        # Lokale Testserver nie über einen Proxy ansprechen
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) \
            if host in ("127.0.0.1", "localhost") else urllib.request.build_opener()

    def __repr__(self) -> str:
        return f"Telegram(api_url={self.api_url!r})"

    def aufruf(self, methode: str, daten: dict | None = None, zeitlimit_s: float | None = None):
        try:
            url = f"{self.api_url}/bot{self._token}/{methode}"
            anfrage = urllib.request.Request(url, data=json.dumps(daten or {}).encode("utf-8"), method="POST",
                                             headers={"Content-Type": "application/json", "User-Agent": "kitrader"})
            with self._opener.open(anfrage, timeout=zeitlimit_s or self.zeitlimit_s) as r:
                inhalt = r.read(2_000_000)
        except urllib.error.HTTPError as e:
            beschreibung, wartezeit = "", None
            try:
                antwort = json.loads(e.read(100_000).decode("utf-8", "replace"))
                beschreibung = str(antwort.get("description", ""))
                wartezeit = (antwort.get("parameters") or {}).get("retry_after")
            except (ValueError, AttributeError):
                pass
            fehler = TelegramFehler(_maskieren(f"Telegram meldet Fehler {e.code}: {beschreibung or e.reason}", self._token))
            fehler.code, fehler.wartezeit = e.code, wartezeit            # type: ignore[attr-defined]
            raise fehler from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TelegramFehler(_maskieren(f"Telegram nicht erreichbar: {getattr(e, 'reason', e)}", self._token)) from None
        except Exception as e:                  # z. B. ungültige Adresse: solche Meldungen enthalten die Adresse, also den Token
            raise TelegramFehler(f"Telegram-Anfrage fehlgeschlagen ({type(e).__name__}); stimmt der Token in telegram.env?") from None
        try:
            antwort = json.loads(inhalt.decode("utf-8"))
        except ValueError:
            raise TelegramFehler("Telegram hat keine gültige Antwort geliefert.") from None
        if not antwort.get("ok"):
            raise TelegramFehler(_maskieren(f"Telegram lehnt ab: {antwort.get('description', 'ohne Begründung')}", self._token))
        return antwort.get("result")

    def get_me(self) -> dict:
        return self.aufruf("getMe")

    def senden(self, chat_id: str | int, text: str) -> None:
        """Sendet Klartext (ohne Formatierung); lange Texte werden in Teile geschnitten."""
        text = text or "(leer)"
        teile = [text[i:i + MAX_TEXT] for i in range(0, len(text), MAX_TEXT)]
        for teil in teile:
            self.aufruf("sendMessage", {"chat_id": chat_id, "text": teil, "disable_web_page_preview": True})

    def aktualisierungen(self, offset: int | None = None, warte_s: int = 0) -> list[dict]:
        daten: dict = {"timeout": int(warte_s), "allowed_updates": ["message"]}
        if offset is not None:
            daten["offset"] = offset
        return self.aufruf("getUpdates", daten, zeitlimit_s=warte_s + self.zeitlimit_s) or []


def client_aus(einst: dict, zugang: Zugang) -> Telegram:
    t = einst["telegram"]
    return Telegram(zugang.token, t["api_url"], t["zeitlimit_s"])


# ---------------------------------------------------------------- Senden im Hintergrund

class Bote:
    """Schickt Nachrichten aus einer Warteschlange in einem eigenen Faden. `melden` kehrt sofort zurück."""

    def __init__(self, client: Telegram, chat_id: str, min_abstand_s: float | None = None, maximal: int = 200):
        self.client, self.chat_id = client, str(chat_id)
        self.min_abstand_s = MIN_ABSTAND_S if min_abstand_s is None else min_abstand_s
        self._q: queue.Queue = queue.Queue(maxsize=maximal)
        self._faden: threading.Thread | None = None
        self._sperre = threading.Lock()
        self.gesendet = 0
        self.fehlgeschlagen = 0

    def melden(self, text: str) -> bool:
        with self._sperre:
            if self._faden is None or not self._faden.is_alive():
                self._faden = threading.Thread(target=self._arbeit, name="kitrader-telegram", daemon=True)
                self._faden.start()
        try:
            self._q.put_nowait(text)
            return True
        except queue.Full:
            log.warning("Telegram: Warteschlange voll, Nachricht verworfen")
            return False

    def _arbeit(self) -> None:
        while True:
            text = self._q.get()
            try:
                self._senden(text)
                self.gesendet += 1
            except Exception as e:
                self.fehlgeschlagen += 1
                log.warning("Telegram: Nachricht nicht gesendet (%s)", e)
            finally:
                self._q.task_done()
            time.sleep(self.min_abstand_s)

    def _senden(self, text: str) -> None:
        try:
            self.client.senden(self.chat_id, text)
        except TelegramFehler as e:
            warte = getattr(e, "wartezeit", None)
            if getattr(e, "code", None) == 429 and isinstance(warte, (int, float)):
                time.sleep(min(float(warte), 30))
                self.client.senden(self.chat_id, text)           # ein zweiter Versuch
            else:
                raise

    def leeren(self, frist_s: float = 10) -> bool:
        """Wartet höchstens `frist_s`, bis alles gesendet ist. True, wenn die Warteschlange leer ist."""
        ende = time.monotonic() + frist_s
        while self._q.unfinished_tasks and time.monotonic() < ende:
            time.sleep(0.02)
        return not self._q.unfinished_tasks


_boten: dict[tuple, Bote] = {}
_boten_sperre = threading.Lock()


def _bote_fuer(einst: dict) -> Bote | None:
    t = einst["telegram"]
    if not t["aktiv"]:
        return None
    z = zugang_lesen()
    if not z.bereit:
        return None
    schluessel = (z.token, z.chat_id, t["api_url"])
    with _boten_sperre:
        if schluessel not in _boten:
            _boten[schluessel] = Bote(client_aus(einst, z), z.chat_id)
        return _boten[schluessel]


def melden(text: str, einst: dict | None = None, warten: bool = False) -> bool:
    """Schickt `text` in den erlaubten Chat. Gibt True zurück, wenn die Nachricht angenommen wurde (False: Telegram
    ist nicht eingerichtet oder aus). Wirft nie und blockiert nie (mit warten=True höchstens `zeitlimit_s`)."""
    try:
        einst = einst if einst is not None else _einst.laden()
        bote = _bote_fuer(einst)
        if bote is None:
            return False
        angenommen = bote.melden(text)
        if warten:
            bote.leeren(einst["telegram"]["zeitlimit_s"] + 2)
        return angenommen
    except Exception as e:
        log.warning("Telegram: Meldung fehlgeschlagen (%s)", _maskieren(str(e)))
        return False


def leeren(frist_s: float = 10) -> None:
    """Vor dem Programmende: ausstehende Meldungen noch senden."""
    ende = time.monotonic() + frist_s
    with _boten_sperre:
        boten = list(_boten.values())
    for b in boten:
        b.leeren(max(0.0, ende - time.monotonic()))


# ---------------------------------------------------------------- Beobachter für den Autopiloten

class Beobachter:
    """Meldet die Ereignisse des Autopiloten per Telegram. Er hängt sich über den Haken `Autopilot.beobachter` ein
    (Funktion `ereignis(art, daten)`); das Journal wird nicht angefasst. Fehler im Beobachter werden verschluckt: der
    Autopilot ruft ihn ohnehin geschützt auf, der Handel läuft immer weiter."""

    def __init__(self, ap, einst: dict, ordner: Path, bote: Bote, wache: "Wache | None" = None):
        self.ap, self.einst, self.ordner, self.bote, self.wache = ap, einst, Path(ordner), bote, wache
        self.waehrung = einst["waehrung"]
        self._abgelehnt: list[tuple[str, str, tuple[str, ...]]] = []
        self._seit: float | None = None
        self._sperre = threading.Lock()
        self._halt = threading.Event()
        self._faden: threading.Thread | None = None
        self._wache_faden: threading.Thread | None = None
        self._ende_gemeldet = False
        self._beendet = False

    def _melden(self, text: str) -> None:
        try:
            self.bote.melden(text)
        except Exception as e:                       # Telegram darf den Handel nie stören
            log.warning("Telegram-Beobachter: Meldung nicht gesendet (%s)", _maskieren(str(e)))

    # ---- Einhängen

    def starten(self) -> None:
        self.ap.beobachter.append(self.ereignis)
        self._faden = threading.Thread(target=self._sammeln, name="kitrader-telegram-sammler", daemon=True)
        self._faden.start()
        if self.wache is not None:
            self._wache_faden = threading.Thread(target=self.wache.lauf, args=(self._halt,), name="kitrader-telegram-wache",
                                                 daemon=True)
            self._wache_faden.start()

    # ---- Ereignisse des Autopiloten

    def _modus_text(self) -> str:
        m = betrieb.MODUS_TEXT.get(self.ap.modus, self.ap.modus)
        return m + (" (ECHTES GELD)" if self.ap.modus == "echt" else " (kein echtes Geld)")

    def _kurz(self) -> str:
        return betrieb.MODUS_TEXT.get(self.ap.modus, self.ap.modus)

    def ereignis(self, art: str, daten: dict) -> None:
        behandler = getattr(self, "_ev_" + art, None)
        if behandler is None:
            return
        try:
            behandler(daten)
        except Exception as e:
            log.warning("Telegram-Beobachter: Ereignis %s nicht verarbeitet (%s)", art, e)

    def _ev_start(self, d: dict) -> None:
        wache = " Not-Aus per Telegram: schreibe 'stop'." if self.wache is not None else ""
        self._melden(f"Autopilot gestartet. Betriebsart {self._modus_text()}, Strategie {d.get('strategie', '?')}, "
                     f"Symbole {', '.join(d.get('symbole') or []) or '(keine)'}.{wache}")

    def _ev_ablehnung(self, d: dict) -> None:
        with self._sperre:
            self._abgelehnt.append((d.get("symbol", ""), d.get("seite", ""), tuple(d.get("gruende") or ())))
            if self._seit is None:
                self._seit = time.monotonic()

    def _ev_ausfuehrung(self, d: dict) -> None:
        stand = d["stand"]
        o = stand.order
        z = [f"Order {stand.status}: {o.seite} {o.menge:g} {o.symbol}" + (f" @ {zahl(stand.preis, 4)}" if stand.preis else "")]
        zusatz = []
        if o.stop_loss is not None:
            zusatz.append(f"Stop {o.stop_loss:g}")
        if o.take_profit is not None:
            zusatz.append(f"Ziel {o.take_profit:g}")
        if zusatz:
            z.append(", ".join(zusatz))
        if o.notiz:
            z.append(f"({o.notiz})")
        if stand.meldung and stand.status in ("abgelehnt", "storniert"):
            z.append(f"Meldung: {stand.meldung}")
        self._melden(f"[{self._kurz()}] " + " ".join(z))

    def _ev_ergebnis(self, d: dict) -> None:
        self._melden(f"[{self._kurz()}] Ergebnis: {d.get('text') or d.get('order_id')} {zahl(d['betrag'])} {self.waehrung}")

    def _ev_tagesverlust(self, d: dict) -> None:
        zusatz = " Alle eigenen Positionen werden glattgestellt." if d.get("schliessen") else ""
        self._melden(f"[{self._kurz()}] TAGESVERLUSTGRENZE erreicht: {d.get('text', '')}. "
                     f"Keine neuen Positionen bis morgen.{zusatz}")

    def _ev_ziel_erreicht(self, d: dict) -> None:
        self._melden(f"[{self._kurz()}] GEWINNZIEL erreicht: {d.get('text', '')}. Position wurde glattgestellt.")

    def _ev_notaus(self, d: dict) -> None:
        if d.get("aufgehoben"):
            self._melden(f"[{self._kurz()}] Not-Aus aufgehoben, der Handel läuft wieder.")
            return
        self.ablehnungen_melden()
        self._melden(f"[{self._kurz()}] NOT-AUS im Autopiloten: {d.get('text', '')}")

    def _ev_tagesabschluss(self, d: dict) -> None:
        self.ablehnungen_melden()
        self._melden(f"[{self._kurz()}] {d.get('text', '')}")

    def _ev_warnung(self, d: dict) -> None:
        self._melden(f"[{self._kurz()}] WARNUNG: {d.get('text', '')}")

    def _ev_ende(self, d: dict) -> None:
        """Letzte Meldung des Autopiloten mit Grund und letztem Stand."""
        self._ende_gemeldet = True
        self.ablehnungen_melden()
        grund, text = d.get("grund"), d.get("text", "")
        if grund == "abbruch":
            wort = f"beendet: {text}"
        elif grund == "fehler_folge":
            wort = text
        elif grund == "tastatur":
            wort = "per Tastatur gestoppt"
        elif grund == "fehler":
            wort = f"durch Fehler beendet: {text}"
        else:
            wort = "gestoppt"
        self._melden(self._stopp_text(wort, d.get("stand", "")))

    def _stopp_text(self, wort: str, stand: str = "") -> str:
        text = f"Autopilot {wort}. Betriebsart {self._modus_text()}."
        if stand:
            text += f"\n{stand}"
        text += "\nOffene Positionen behalten ihre Schutz-Stops beim Broker."
        return _maskieren(text)

    # ---- Ablehnungen zusammenfassen

    def ablehnungen_melden(self) -> None:
        with self._sperre:
            liste, self._abgelehnt, self._seit = self._abgelehnt, [], None
        if not liste:
            return
        gruppen: Counter = Counter()
        symbole: dict[tuple, set] = {}
        for symbol, seite, gruende in liste:
            gruppen[gruende] += 1
            symbole.setdefault(gruende, set()).add(f"{symbol} {seite}".strip())
        z = [f"[{betrieb.MODUS_TEXT.get(self.ap.modus, self.ap.modus)}] Risikoprüfung: {len(liste)} Order(s) abgelehnt."]
        for gruende, n in gruppen.most_common(6):
            z.append(f"- {n}x {', '.join(sorted(symbole[gruende]))}: {'; '.join(gruende)[:300]}")
        if len(gruppen) > 6:
            z.append(f"... und {len(gruppen) - 6} weitere Gründe")
        self._melden("\n".join(z))

    def _sammeln(self) -> None:
        intervall = float(self.einst["telegram"]["ablehnungen_alle_s"])
        while not self._halt.wait(min(5.0, intervall)):
            with self._sperre:
                faellig = self._seit is not None and time.monotonic() - self._seit >= intervall
            if faellig:
                self.ablehnungen_melden()

    # ---- Ende

    def beenden(self, ausnahme: BaseException | None = None) -> None:
        """Nach `laufen()`: Rest melden, Hintergrundfäden anhalten, Warteschlange leeren. Hat der Autopilot sein Ende
        nicht selbst gemeldet (Ereignis `ende`), kommt die Stopp-Nachricht von hier."""
        if self._beendet:
            return
        self._beendet = True
        self._halt.set()
        if self.wache is not None:
            self.wache.fertig_warten(15)             # läuft gerade ein Not-Aus per Telegram, erst fertig werden lassen
        self.ablehnungen_melden()
        if not self._ende_gemeldet:
            if isinstance(ausnahme, KeyboardInterrupt):
                wort = "per Tastatur gestoppt"
            elif ausnahme is not None:
                wort = f"durch Fehler beendet: {type(ausnahme).__name__}: {ausnahme}"
            else:
                wort = "gestoppt"
            self._melden(self._stopp_text(wort))
        try:
            self.bote.leeren(self.einst["telegram"]["zeitlimit_s"] + 3)
        except Exception as e:
            log.warning("Telegram-Beobachter: Warteschlange nicht geleert (%s)", _maskieren(str(e)))


def beobachten(ap, einst: dict, ordner: Path, bote: Bote | None = None, wache_client: Telegram | None = None) -> Beobachter | None:
    """Beobachter für einen gebauten Autopiloten, oder None, wenn Telegram aus oder nicht eingerichtet ist.
    Danach `.starten()`; nach dem Lauf `.beenden()`. Mit `[telegram] wache = true` läuft die Wache als Faden mit."""
    t = einst["telegram"]
    if bote is None:
        bote = _bote_fuer(einst)
        if bote is None:
            return None
    wache = None
    if t["wache"]:
        z = zugang_lesen()
        client = wache_client or (client_aus(einst, z) if z.bereit else None)
        if client is not None:
            wache = Wache(client, bote.chat_id, einst, Path(ordner), ap.modus)
    return Beobachter(ap, einst, ordner, bote, wache)


# ---------------------------------------------------------------- Wache: Not-Aus per Nachricht

def befehl_aus(text: str | None) -> str | None:
    """'stop', '/stop', 'Stopp', '/stop@meinbot' -> 'stop'; 'status' -> 'status'; alles andere None."""
    t = (text or "").strip().lower().lstrip("/").split("@", 1)[0].strip()
    if t in STOP_WOERTER:
        return "stop"
    if t in STATUS_WOERTER:
        return "status"
    return None


class Wache:
    """Hört per Long-Polling auf Nachrichten. Nur der erlaubte Chat zählt. `stop` = Not-Aus, `status` = Übersicht."""

    def __init__(self, client: Telegram, chat_id: str, einst: dict, ordner: Path, modus: str,
                 notaus: Callable[[], dict] | None = None, status: Callable[[], str] | None = None,
                 uhr: Callable[[], float] = time.time, ausgabe: Callable[[str], None] | None = None):
        self.client, self.chat_id, self.einst, self.ordner, self.modus = client, str(chat_id), einst, Path(ordner), modus
        self.cfg = einst["telegram"]
        self.uhr = uhr
        self.ausgabe = ausgabe or (lambda text: None)
        self._notaus = notaus or (lambda: betrieb.notaus_ausloesen(einst, self.ordner, modus, quelle="Telegram", melden=False))
        self._status = status or (lambda: betrieb.status_text(betrieb.uebersicht(einst, self.ordner, modus)))
        self.offset: int | None = None
        self.pause_min_s = 2.0              # nach einem Fehler; wächst bis 60 s
        self._beschaeftigt = threading.Lock()     # gehalten, solange ein Befehl (Not-Aus!) ausgeführt wird
        self.fremde = 0                     # ignorierte Nachrichten fremder Chats (nur gezählt, nie beantwortet)

    def schritt(self, warte_s: int = 0) -> int:
        """Eine Abfrage. Gibt die Zahl der ausgeführten Befehle zurück."""
        ausgefuehrt = 0
        for u in self.client.aktualisierungen(self.offset, warte_s):
            uid = u.get("update_id")
            if isinstance(uid, int):
                self.offset = max(self.offset or 0, uid + 1)     # sofort bestätigen: nie zweimal ausführen
            if self._behandeln(u.get("message") or {}):
                ausgefuehrt += 1
        return ausgefuehrt

    def _behandeln(self, m: dict) -> bool:
        chat = m.get("chat") or {}
        if str(chat.get("id", "")) != self.chat_id or (m.get("from") or {}).get("is_bot"):
            if m:
                self.fremde += 1
            return False
        befehl = befehl_aus(m.get("text"))
        if befehl is None:
            return False
        alter = self.uhr() - float(m.get("date") or 0)
        if alter > self.cfg["max_alter_s"]:
            if befehl == "stop":
                self._antworten(f"Deine Nachricht 'stop' ist {alter / 60:.0f} Minuten alt und wurde NICHT ausgeführt. "
                                "Schreibe 'stop' noch einmal, wenn du den Not-Aus jetzt willst.")
            return False
        with self._beschaeftigt:
            if befehl == "stop":
                self.ausgabe("Telegram: 'stop' empfangen - Not-Aus wird ausgeführt")
                try:
                    ergebnis = self._notaus()
                    antwort = betrieb.notaus_text(ergebnis, "Telegram")
                except Exception as e:
                    antwort = f"NOT-AUS FEHLGESCHLAGEN: {_maskieren(str(e))}. Bitte sofort am Rechner oder beim Broker prüfen!"
                self._antworten(antwort)
                return True
            try:
                self._antworten(self._status())
            except Exception as e:
                self._antworten(f"Status nicht lesbar: {_maskieren(str(e))}")
            return True

    def fertig_warten(self, frist_s: float) -> bool:
        """Wartet höchstens `frist_s`, bis ein gerade laufender Befehl fertig ist."""
        if self._beschaeftigt.acquire(timeout=frist_s):
            self._beschaeftigt.release()
            return True
        return False

    def _antworten(self, text: str) -> None:
        try:
            self.client.senden(self.chat_id, text)
        except TelegramFehler as e:
            log.warning("Telegram: Antwort nicht gesendet (%s)", e)

    def lauf(self, halt: threading.Event) -> None:
        """Schleife bis `halt` gesetzt wird. Fehler (Netz, 409 durch eine zweite Wache ...) führen zu wachsenden Pausen."""
        pause = 0.0
        letzte_meldung = ""
        while not halt.is_set():
            try:
                self.schritt(int(self.cfg["poll_s"]))
                pause, letzte_meldung = 0.0, ""
            except TelegramFehler as e:
                pause = min(60.0, max(self.pause_min_s, pause * 2))
                if str(e) != letzte_meldung:
                    letzte_meldung = str(e)
                    log.warning("Telegram-Wache: %s (neuer Versuch in %.0f s)", e, pause)
                halt.wait(pause)
            except Exception as e:
                pause = min(60.0, max(self.pause_min_s, pause * 2))
                log.warning("Telegram-Wache: unerwarteter Fehler %s (neuer Versuch in %.0f s)", _maskieren(str(e)), pause)
                halt.wait(pause)


def wache_laufen(einst: dict, ordner: Path, modus: str, ausgabe: Callable[[str], None] = print,
                 halt: threading.Event | None = None) -> None:
    """`python3 -m kitrader telegram wache`: läuft im Vordergrund bis Strg+C."""
    z = zugang_lesen()
    if not z.bereit:
        raise TelegramFehler(f"Telegram ist nicht eingerichtet. Zuerst: python3 -m kitrader telegram einrichten "
                             f"(Zugangsdatei: {zugang_datei()})")
    client = client_aus(einst, z)
    wache = Wache(client, z.chat_id, einst, ordner, modus, ausgabe=ausgabe)
    ausgabe(f"Telegram-Wache läuft (Betriebsart {betrieb.MODUS_TEXT.get(modus, modus)}). Schreibe in deinem Chat 'stop' für den "
            "Not-Aus oder 'status' für eine Übersicht. Beenden mit Strg+C.")
    try:
        client.senden(z.chat_id, f"Telegram-Wache gestartet (Betriebsart {betrieb.MODUS_TEXT.get(modus, modus)}). "
                                 "'stop' = Not-Aus, 'status' = Übersicht.")
    except TelegramFehler as e:
        ausgabe(f"Hinweis: Startnachricht nicht gesendet ({e})")
    halt = halt or threading.Event()
    try:
        wache.lauf(halt)
    except KeyboardInterrupt:
        halt.set()
        ausgabe("Telegram-Wache beendet.")


# ---------------------------------------------------------------- Einrichten

def einrichten(einst: dict, datei: Path | None = None, eingabe: Callable[[str], str] | None = None,
               geheim: Callable[[str], str] | None = None, ausgabe: Callable[[str], None] = print,
               neu: bool = False, warte_s: float = 120) -> int:
    """Führt durch die Einrichtung: Token abfragen (falls nötig), auf deine erste Nachricht warten, Chat-ID speichern,
    Testnachricht senden. Der Token wird nie ausgegeben."""
    datei = datei or zugang_datei()
    eingabe, geheim = eingabe or input, geheim or getpass.getpass
    z = zugang_lesen(umgebung={}, datei=datei)
    if not z.token:
        ausgabe("Noch kein Token. Den bekommst du vom BotFather in Telegram (siehe LIESMICH.md, Abschnitt Telegram).")
        token = geheim("Token einfügen (wird nicht angezeigt): ").strip()
        if not TOKEN_FORM.match(token):
            raise TelegramFehler("Das sieht nicht nach einem Bot-Token aus (erwartet: 123456789:ABC...).")
        z.token = token
    client = client_aus(einst, z)
    ich = client.get_me()
    name = ich.get("username", "dein Bot")
    ausgabe(f"Bot gefunden: @{name}")
    if z.chat_id and not neu:
        ausgabe(f"Chat-ID ist schon eingetragen ({z.chat_id}). Mit --neu kannst du sie neu ermitteln.")
    else:
        ausgabe(f"Öffne Telegram, suche @{name}, tippe auf START und schreibe eine Nachricht (z. B. 'hallo').\n"
                f"Ich warte bis zu {int(warte_s)} Sekunden darauf ...")
        gefunden: dict[str, str] = {}
        letzte_id = None
        ende = time.monotonic() + warte_s
        while time.monotonic() < ende and not gefunden:
            for u in client.aktualisierungen(None, 5):
                letzte_id = max(letzte_id or 0, u.get("update_id") or 0)
                chat = (u.get("message") or {}).get("chat") or {}
                if chat.get("type") == "private" and chat.get("id") is not None:
                    absender = " ".join(str(chat.get(k)) for k in ("first_name", "last_name") if chat.get(k)) or "ohne Namen"
                    if chat.get("username"):
                        absender += f" (@{chat['username']})"
                    gefunden[str(chat["id"])] = absender
        if not gefunden:
            raise TelegramFehler("Keine Nachricht erhalten. Hast du dem Bot geschrieben? Ist die Wache gerade aktiv "
                                 "(sie würde die Nachrichten abholen)? Dann erneut versuchen.")
        ids = list(gefunden)
        if len(ids) == 1:
            wahl = ids[0]
            ausgabe(f"Nachricht von: {gefunden[wahl]}")
            if eingabe("Bist du das? Nur dieser Chat darf später den Not-Aus auslösen. [j/N] ").strip().lower() not in ("j", "ja", "y", "yes"):
                raise TelegramFehler("Abgebrochen. Es wurde nichts gespeichert.")
        else:
            ausgabe("Es haben mehrere Personen dem Bot geschrieben:")
            for i, k in enumerate(ids, 1):
                ausgabe(f"  {i}) {gefunden[k]}")
            nummer = eingabe("Welche bist du? Nummer eingeben: ").strip()
            if not nummer.isdigit() or not 1 <= int(nummer) <= len(ids):
                raise TelegramFehler("Ungültige Auswahl. Es wurde nichts gespeichert.")
            wahl = ids[int(nummer) - 1]
        z.chat_id = wahl
        if letzte_id is not None:
            client.aktualisierungen(letzte_id + 1, 0)            # abgeholte Nachrichten bestätigen
    zugang_speichern(datei, {"TELEGRAM_TOKEN": z.token, "TELEGRAM_CHAT_ID": z.chat_id})
    ausgabe(f"Gespeichert in {datei} (Rechte 600).")
    hinweis = rechte_hinweis(datei)
    if hinweis:
        ausgabe("Hinweis: " + hinweis)
    client.senden(z.chat_id, "kitrader: Telegram ist eingerichtet. 'status' zeigt eine Übersicht, 'stop' löst den Not-Aus aus "
                             "(wenn die Wache läuft: python3 -m kitrader telegram wache).")
    ausgabe("Testnachricht gesendet. Fertig.")
    return 0
