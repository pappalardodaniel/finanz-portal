"""Autopilot: Kerzen holen -> Strategie -> Risikoprüfung -> Broker -> Journal.

Betriebsarten:
  papier  Papier-Broker (Simulation) mit echten Kursen. Standard.
  demo    Demokonto des echten Brokers.
  echt    Echtes Geld. Nur wenn in den Einstellungen modus = "echt" steht UND im Datenordner die Datei
          ECHTGELD_FREIGABE liegt, die das heutige Datum enthält (die Freigabe gilt also nur einen Tag).

Jede Order geht durch risiko.pruefen(). Der Not-Aus (Datei NOTAUS im Datenordner) storniert die Orders und stellt
die Positionen der EIGENEN Symbole glatt (broker.alles_schliessen(symbole=...)); fremde Positionen werden nur gemeldet,
nie angefasst. Danach wird nichts mehr gehandelt, bis die Datei gelöscht ist.

Eigene Symbole: `autopilot.symbole` (bzw. --symbole) plus alles, was der Autopilot eröffnet hat (Journal, Tabelle `eigene`).

Beobachter-Haken: `Autopilot.beobachter` ist eine Liste von Funktionen `f(art, daten)`; sie werden bei den Ereignissen
`EREIGNISSE` gerufen. Fehler in einem Beobachter werden nur protokolliert und erreichen den Handel nie.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from . import einstellungen as _einst
from .broker.basis import Broker, BrokerFehler, Order, symbol_schluessel
from .broker.papier import PapierBroker
from .hilfen import jetzt_utc, takt_dauer, zahl, zu_utc
from .journal import Journal
from .kursquelle import BrokerQuelle, WiedergabeQuelle, broker_aus_einstellungen, quelle_aus_einstellungen
from .risiko import (NOTAUS_DATEI, Ergebnis, Lage, markt_status, markt_von_symbol, pruefen, tagesverlust_erreicht,
                     tagesverlust_grenze, wechselkurs_fehlt_text, wechselkurs_gueltig)
from .strategie import Strategie, signal_zu_order, strategie_holen

log_modul = logging.getLogger("kitrader.autopilot")

FREIGABE_DATEI = "ECHTGELD_FREIGABE"
_EPS = 1e-9
SCHONFRIST = timedelta(minutes=2)   # so lange bleibt ein frisch vermerkter Eintrag, auch wenn die Positionsliste noch alt ist
FEHLT_BIS_STREICHEN = 3             # so oft in Folge muss eine Position fehlen, bevor ihr Eintrag gestrichen wird (Listen haken mal)
WECHSELKURS_GUELTIG = timedelta(seconds=60)

# Ereignisse für die Beobachter: (art, daten). `daten` enthält immer "modus".
#   start         strategie, symbole
#   ende          grund (gestoppt | abbruch | fehler_folge | tastatur | fehler), text, stand
#   entscheidung  id, symbol, richtung, ok (None = nicht geprüft), gruende, notiz
#   ablehnung     symbol, seite, gruende (Risikoprüfung oder fehlender Wechselkurs hat abgelehnt)
#   ausfuehrung   symbol, stand (OrderStand), entscheidung_id
#   ergebnis      symbol, text, betrag (abgeschlossener Trade, netto)
#   tagesverlust  text, schliessen (True = die eigenen Positionen werden glattgestellt)
#   notaus        text, schritte, aufgehoben (True = Not-Aus aufgehoben), vollstaendig
#   tagesabschluss text
#   ziel_erreicht symbol, ziel, preis, text
#   warnung       art (ohne_schutz | fremde_position), text
EREIGNISSE = ("start", "ende", "entscheidung", "ablehnung", "ausfuehrung", "ergebnis", "tagesverlust", "notaus",
              "tagesabschluss", "ziel_erreicht", "warnung")


class Abbruch(RuntimeError):
    """Der Autopilot darf so nicht starten. Text ist für Menschen lesbar."""


def modus_pruefen(modus: str, einst: dict, datenordner: Path, heute: date | None = None) -> str:
    """Prüft die Betriebsart. `echt` nur mit modus = "echt" in den Einstellungen UND Freigabedatei von heute."""
    if modus not in _einst.MODI:
        raise Abbruch(f"Unbekannte Betriebsart {modus!r}. Erlaubt: {', '.join(_einst.MODI)}")
    if modus != "echt":
        return modus
    if einst["modus"] != "echt":
        raise Abbruch("ECHTGELD VERWEIGERT: In den Einstellungen steht modus = "
                      f"\"{einst['modus']}\". Für echtes Geld muss dort bewusst modus = \"echt\" stehen.")
    datei = Path(datenordner) / FREIGABE_DATEI
    if not datei.is_file():
        raise Abbruch(f"ECHTGELD VERWEIGERT: Die Freigabedatei {datei} fehlt. Zum Freigeben für heute die Datei "
                      "anlegen und das heutige Datum (JJJJ-MM-TT) hineinschreiben.")
    heute = heute or date.today()
    text = datei.read_text(encoding="utf-8", errors="replace")
    if heute.isoformat() not in text and heute.strftime("%d.%m.%Y") not in text:
        raise Abbruch(f"ECHTGELD VERWEIGERT: Die Freigabedatei {datei} enthält nicht das heutige Datum "
                      f"({heute.isoformat()}). Freigaben gelten nur für den Tag, der in der Datei steht.")
    return modus


def banner(modus: str, einst: dict) -> str:
    r = einst["risiko"]
    grenzen = (f"Grenzen: Tagesverlust max. {zahl(r['tagesverlust_max'])} {einst['waehrung']} / "
               f"{zahl(r['tagesverlust_max_prozent'])} %, Position max. {zahl(r['position_max_prozent'])} %, "
               f"Risiko je Trade {zahl(r['risiko_pro_trade_prozent'])} %, höchstens {r['positionen_max']} Positionen, "
               f"Short {'erlaubt' if r['short_erlaubt'] else 'verboten'}")
    if modus == "echt":
        balken = "!" * 78
        kopf = [balken, "!!  KITRADER AUTOPILOT  -  BETRIEBSART: ECHT  -  ECHTES GELD  !!".center(78),
                "Orders werden mit echtem Geld ausgeführt. Not-Aus: Datei NOTAUS im Datenordner.".center(78), balken]
    elif modus == "demo":
        kopf = ["=" * 78, "KITRADER AUTOPILOT  -  BETRIEBSART: DEMO  -  Demokonto, kein echtes Geld".center(78), "=" * 78]
    else:
        kopf = ["=" * 78, "KITRADER AUTOPILOT  -  BETRIEBSART: PAPIER  -  Simulation, kein echtes Geld".center(78), "=" * 78]
    return "\n".join(kopf + [grenzen])


def _schlafen(sekunden: float) -> bool:
    time.sleep(sekunden)
    return True


class Autopilot:
    def __init__(self, broker: Broker, quelle, strategie: Strategie, journal: Journal, einstellungen: dict,
                 datenordner: Path, modus: str = "papier", symbole: list[str] | None = None,
                 uhr: Callable[[], datetime] | None = None, warten: Callable[[float], bool] | None = None,
                 ausgabe: Callable[[str], None] = print, heute: date | None = None,
                 beobachter: list[Callable[[str, dict], None]] | None = None):
        self.heute = heute                       # nur für Tests: festes Datum für die Freigabeprüfung
        self.broker, self.quelle, self.strategie, self.journal = broker, quelle, strategie, journal
        self.einst = _einst.mit_standard(einstellungen)
        self.datenordner = Path(datenordner)
        self.modus = modus
        self._freigabe_pruefen()                 # Doppelt hält besser: auch ein direkt gebauter Autopilot braucht sie
        if self.modus != "echt" and not (broker.papier or getattr(broker, "nur_pruefen", False)):
            # Ein echter Broker in papier/demo würde echte Orders senden (bauen() prüft das nur für demo).
            raise Abbruch(f"Betriebsart {self.modus}, aber der Broker {broker.name} meldet ein Echtgeld-Konto "
                          "(kein Papier, kein Demokonto, nicht 'nur prüfen'). Abbruch.")
        cfg = self.einst["autopilot"]
        self.cfg = cfg
        self.symbole = list(symbole if symbole is not None else cfg["symbole"])
        self.takt = cfg["takt"]
        self.uhr = uhr or jetzt_utc
        self.warten = warten or _schlafen
        self.ausgabe = ausgabe
        self.datum: str | None = None
        self.startwert = 0.0
        self.gesperrt = False
        self.fehler_folge = 0
        self._bewertet: dict[str, datetime] = {}
        self._notaus_fertig = False
        self._notaus_aktiv = False
        self._abschluss_datum: str | None = None
        self._war_offen = False
        # Schon vorhandene Trades gehören zu früheren Läufen und stehen im Journal: sonst würde jeder Neustart
        # ihr Ergebnis noch einmal im Journal aufaddieren.
        self._letzter_trade = max((t["id"] for t in broker.trades()), default=0) if hasattr(broker, "trades") else 0
        self._schritt_fehler: list[Exception] = []
        self._geschlossen: set[str] = set()      # in diesem Durchlauf schon glattgestellte Symbole
        self.beobachter: list[Callable[[str, dict], None]] = list(beobachter or [])
        self._fremde_gemeldet: set[str] = set()  # fremde Positionen, die schon gemeldet wurden (einmal je Lauf)
        self._fehlt_seit: dict[str, int] = {}    # Symbolschlüssel -> so oft in Folge keine Position gesehen
        self._wechselkurse: dict[tuple[str, str], tuple[datetime, float | None]] = {}
        anbinden = getattr(strategie, "anbinden", None)
        if callable(anbinden):                   # z. B. die KI-Strategie: braucht Journal, Datenordner und Betriebsart
            anbinden(journal, self.datenordner, self.modus, self.einst)

    def _freigabe_pruefen(self) -> None:
        """Echtgeld: Freigabe bei jedem Durchlauf neu prüfen (Datei gelöscht oder Datum abgelaufen = Ende)."""
        if self.modus == "echt":
            modus_pruefen("echt", self.einst, self.datenordner, self.heute)

    # ------------------------------------------------------------ Ausgabe

    def log(self, text: str) -> None:
        self.ausgabe(f"[{zu_utc(self.uhr()):%Y-%m-%d %H:%M:%S}] {text}")

    def _ereignis(self, ereignis: str, /, **daten) -> None:
        """Ruft die Beobachter. Ein Fehler darin wird nur protokolliert und erreicht den Handel nie."""
        daten["modus"] = self.modus
        for beobachter in list(self.beobachter):
            try:
                beobachter(ereignis, dict(daten))
            except Exception as e:
                log_modul.warning("Beobachter: Ereignis %s nicht verarbeitet (%s: %s)", ereignis, type(e).__name__, e)

    def _entscheidung(self, symbol: str, signal, pruefung, order, notiz: str = "") -> int:
        """Journal-Eintrag zu einer Entscheidung plus Ereignisse `entscheidung` (und `ablehnung` bei abgelehnter Order)."""
        eid = self.journal.entscheidung(self.modus, symbol, self.strategie.name, signal, pruefung, order, notiz)
        richtung = getattr(signal, "richtung", None) or (None if order is None else
                                                          ("long" if order.seite == "kauf" else "short"))
        self._ereignis("entscheidung", id=eid, symbol=symbol, richtung=richtung,
                       ok=None if pruefung is None else pruefung.ok,
                       gruende=[] if pruefung is None else list(pruefung.gruende), notiz=notiz)
        if pruefung is not None and not pruefung.ok:
            self._ereignis("ablehnung", symbol=symbol, seite=getattr(order, "seite", ""), gruende=list(pruefung.gruende))
        return eid

    def _ausfuehrung(self, eid: int, stand, symbol: str) -> None:
        self.journal.ausfuehrung(eid, stand)
        self._ereignis("ausfuehrung", symbol=symbol, stand=stand, entscheidung_id=eid)

    # ------------------------------------------------------------ Hauptschleife

    def laufen(self, max_schritte: int | None = None) -> None:
        ende: dict = {"grund": "gestoppt", "text": "", "stand": ""}
        try:
            self._laufen(max_schritte, ende)
        except BaseException as e:
            if ende["grund"] == "gestoppt":
                if isinstance(e, KeyboardInterrupt):
                    ende.update(grund="tastatur", text="per Tastatur gestoppt")
                else:
                    ende.update(grund="fehler", text=f"{type(e).__name__}: {e}")
            raise
        finally:
            self._ereignis("ende", **ende)

    def _laufen(self, max_schritte: int | None, ende: dict) -> None:
        self.ausgabe(banner(self.modus, self.einst))
        self.journal.ereignis("start", f"Autopilot gestartet, Betriebsart {self.modus}, Strategie "
                                       f"{self.strategie.name}, Symbole {self.symbole}")
        self._ereignis("start", strategie=getattr(self.strategie, "name", "?"), symbole=list(self.symbole))
        self.log(f"Strategie {self.strategie.name}, Symbole {', '.join(self.symbole) or '(keine)'}, Takt {self.takt}")
        if not self.symbole:
            raise Abbruch("Keine Symbole eingestellt (autopilot.symbole in den Einstellungen oder --symbole).")
        n = 0
        try:
            while True:
                try:
                    self.schritt()
                    self.fehler_folge = 0
                except KeyboardInterrupt:
                    raise
                except Abbruch as e:
                    self.log(f"AUTOPILOT BEENDET: {e} Offene Positionen behalten ihre Schutz-Stops beim Broker.")
                    self.journal.ereignis("abbruch", str(e))
                    ende.update(grund="abbruch", text=str(e))
                    break
                except Exception as e:       # Fehler dürfen den Autopiloten nicht unkontrolliert beenden
                    self.fehler_folge += 1
                    self.log(f"FEHLER ({self.fehler_folge}/{self.cfg['fehler_max']}): {type(e).__name__}: {e}")
                    self.journal.ereignis("fehler", f"{type(e).__name__}: {e}")
                    if self.fehler_folge >= self.cfg["fehler_max"]:
                        self.log("Zu viele Fehler in Folge: Autopilot bricht ab. Offene Positionen behalten ihre "
                                 "Schutz-Stops beim Broker; bitte Konto prüfen (python3 -m kitrader positionen).")
                        ende.update(grund="fehler_folge", text=f"nach {self.fehler_folge} Fehlern in Folge abgebrochen (siehe Journal)")
                        break
                n += 1
                if max_schritte is not None and n >= max_schritte:
                    break
                if not self.warten(self.cfg["pause_s"]):
                    break
        except KeyboardInterrupt:
            self.log("Autopilot per Tastatur beendet. Offene Positionen bleiben mit ihren Schutz-Stops bestehen.")
            ende.update(grund="tastatur", text="per Tastatur gestoppt")
        ende["stand"] = self.tagesbericht("Zwischenstand") or ""

    def schritt(self) -> None:
        """Ein Durchlauf: Not-Aus prüfen, Schutz-Stops prüfen, Strategie je Symbol, Tagesabschluss."""
        jetzt = zu_utc(self.uhr())
        self._freigabe_pruefen()
        self._tag_beginnen(jetzt)
        if self._notaus_pruefen():
            return
        self._schritt_fehler = []
        self._geschlossen = set()
        if hasattr(self.broker, "aktualisieren"):
            for stand in self.broker.aktualisieren():
                self.log(f"Order ausgeführt: {stand.id} {stand.order.seite} {stand.order.menge:g} "
                         f"{stand.order.symbol} @ {zahl(stand.preis, 4)} ({stand.order.notiz or stand.order.art})")
            self._ergebnisse_nachtragen()
        self._versuchen("Eigene Positionen", self._eigene_aufraeumen, jetzt)
        self._versuchen("Tagesverlust", self._tagesverlust_pruefen, jetzt)
        self._schutz_pruefen()
        self._versuchen("Gewinnziele", self._ziele_pruefen, jetzt)
        offen = False
        for symbol in self.symbole:
            try:
                status = markt_status(markt_von_symbol(symbol, self.einst), jetzt, self.einst)
            except ValueError as e:
                self.log(f"{symbol}: Handelszeiten nicht prüfbar ({e})")
                continue
            if not status.offen:
                continue
            offen = True
            self._war_offen = True
            if self.cfg["nur_daytrading"] and status.minuten_bis_schluss is not None \
                    and status.minuten_bis_schluss <= self.cfg["tagesabschluss_min_vor_schluss"]:
                if self._versuchen(symbol, self._position, symbol):
                    self._versuchen(symbol, self._glattstellen, symbol, "Tagesabschluss vor Handelsschluss", jetzt)
                continue
            self._versuchen(symbol, self._symbol_bearbeiten, symbol, jetzt)
        self._versuchen("Tagesverlust", self._gesperrt_schliessen, jetzt)    # die Grenze kann erst eben erreicht worden sein
        if not offen and self._war_offen and self._abschluss_datum != self.datum:
            self._abschluss_datum = self.datum
            self.tagesbericht("Tagesabschluss")
            self._war_offen = False
        self._einschaetzungen_auswerten(jetzt)
        if self._schritt_fehler:             # alle anderen Symbole sind drangekommen; der Fehler zählt trotzdem
            raise self._schritt_fehler[0]

    def _versuchen(self, name: str, aktion, *args):
        """Führt einen Teil des Durchlaufs aus. Ein Fehler bei einem Symbol (z. B. keine Kerzen) darf nicht verhindern,
        dass die anderen Symbole ihren Schutz-Stop, ihren Tagesabschluss und ihre Glattstellung bekommen."""
        try:
            return aktion(*args)
        except (KeyboardInterrupt, Abbruch):
            raise
        except Exception as e:
            self.log(f"{name}: FEHLER {type(e).__name__}: {e}")
            self._schritt_fehler.append(e)
            return None

    # ------------------------------------------------------------ Tag und Not-Aus

    def _tag_beginnen(self, jetzt: datetime) -> None:
        datum = jetzt.date().isoformat()
        if datum == self.datum:
            return
        if self.datum is not None and self._abschluss_datum != self.datum:
            self.tagesbericht("Tagesabschluss")
        konto = self.broker.konto()
        stand = self.journal.tagesstand(datum, konto.gesamtwert)
        self.datum, self.startwert, self.gesperrt = datum, stand["startwert"], bool(stand["gesperrt"])
        self._war_offen = False
        self._bewertet.clear()
        self.log(f"Neuer Tag {datum}: Startwert {zahl(self.startwert)} {konto.waehrung}"
                 + (" (Tagesverlustgrenze war schon erreicht: keine neuen Positionen)" if self.gesperrt else ""))

    def _notaus_pruefen(self) -> bool:
        aktiv = (self.datenordner / NOTAUS_DATEI).exists()
        if not aktiv:
            if self._notaus_aktiv:
                self.log("Not-Aus aufgehoben (Datei NOTAUS gelöscht). Der Handel läuft wieder.")
                self.journal.ereignis("notaus_ende", "NOTAUS-Datei gelöscht")
                self._ereignis("notaus", aufgehoben=True, text="Not-Aus aufgehoben, der Handel läuft wieder.",
                               schritte=[], vollstaendig=True)
            self._notaus_aktiv, self._notaus_fertig = False, False
            return False
        if not self._notaus_aktiv:
            self._notaus_aktiv = True
            self.ausgabe("#" * 78)
            self.ausgabe("#  NOT-AUS AKTIV: Orders werden storniert, die eigenen Positionen glattgestellt.".ljust(77) + "#")
            self.ausgabe("#" * 78)
        if not self._notaus_fertig:
            schritte = self.broker.alles_schliessen(symbole=sorted(self._eigene_symbole()))
            for s in schritte:
                self.log("Not-Aus: " + s)
            self.journal.ereignis("notaus", "; ".join(schritte) or "nichts zu tun")
            self._notaus_fertig = not any(s.startswith("FEHLER") for s in schritte)
            self._ereignis("notaus", aufgehoben=False, text="; ".join(schritte) or "nichts zu tun", schritte=list(schritte),
                           vollstaendig=self._notaus_fertig)
            if not self._notaus_fertig:
                self.log("Not-Aus unvollständig, nächster Versuch im nächsten Durchlauf.")
        return True

    def tagesbericht(self, titel: str) -> str | None:
        try:
            konto = self.broker.konto()
        except BrokerFehler as e:
            self.log(f"{titel}: Konto nicht lesbar ({e})")
            return None
        ergebnis = konto.gesamtwert - self.startwert
        text = (f"{titel} {self.datum}: Gesamtwert {zahl(konto.gesamtwert)} {konto.waehrung}, "
                f"Tagesergebnis {zahl(ergebnis)}, Orders heute {self.journal.orders_heute(self.datum or '', self.modus)}")
        self.log(text)
        self.journal.ereignis("tagesabschluss" if titel == "Tagesabschluss" else "zwischenstand", text)
        if titel == "Tagesabschluss":
            self._ereignis("tagesabschluss", text=text)
        return text

    # ------------------------------------------------------------ Eigene Symbole und Positionen

    def _eigene_symbole(self) -> set[str]:
        """Symbole, die dem Autopiloten gehören: die eingestellten plus alles, was er eröffnet hat (Journal)."""
        return set(self.symbole) | {e["symbol"] for e in self.journal.eigene(self.modus)}

    def _eigene_schluessel(self) -> set[str]:
        return {symbol_schluessel(s) for s in self._eigene_symbole()}

    def _eigene_merken(self, order: Order, stand) -> None:
        """Nach einer Eröffnung: Symbol als eigenes vermerken, samt Ziel, falls der Broker es nicht als Order führt."""
        if stand.status not in ("offen", "teilweise", "ausgefuehrt"):
            return
        ziel = None
        fuehrt = getattr(self.broker, "fuehrt_ziel_als_order", None)
        if order.take_profit is not None and not (callable(fuehrt) and fuehrt(order)):
            ziel = order.take_profit
        self.journal.eigene_merken(order.symbol, self.modus, "long" if order.seite == "kauf" else "short", ziel, stand.id)

    def _eigene_aufraeumen(self, jetzt: datetime) -> None:
        """Einträge streichen, deren Position weg ist und zu denen keine Order mehr offen ist (nach einer Schonfrist,
        weil Positionslisten manchmal nachhinken)."""
        eintraege = self.journal.eigene(self.modus)
        if not eintraege:
            return
        positionen = [p for p in self.broker.positionen() if abs(p.menge) > _EPS]
        offene = self.broker.orders(nur_offene=True)
        for e in eintraege:
            k = symbol_schluessel(e["symbol"])
            if any(symbol_schluessel(p.symbol) == k for p in positionen) \
                    or any(symbol_schluessel(o.order.symbol) == k and o.status in ("offen", "teilweise") for o in offene):
                self._fehlt_seit.pop(k, None)
                continue
            self._fehlt_seit[k] = self._fehlt_seit.get(k, 0) + 1
            jung = bool(e["seit"]) and jetzt - zu_utc(datetime.fromisoformat(e["seit"])) < SCHONFRIST
            if not jung and self._fehlt_seit[k] >= FEHLT_BIS_STREICHEN:
                self.journal.eigene_entfernen(e["symbol"], self.modus)
                self._fehlt_seit.pop(k, None)

    def _fremde_melden(self, p) -> None:
        """Fremde Position (nicht in den eigenen Symbolen): einmal je Lauf melden, nie anfassen."""
        k = symbol_schluessel(p.symbol)
        if k in self._fremde_gemeldet:
            return
        self._fremde_gemeldet.add(k)
        text = (f"Fremde Position {p.symbol} {p.menge:+g} (nicht in autopilot.symbole, nicht vom Autopiloten eröffnet): "
                "wird nicht angefasst, ihr Schutz-Stop wird nicht geprüft. Tipp: ein eigenes Konto oder Unterkonto für kitrader.")
        self.log("HINWEIS: " + text)
        self.journal.ereignis("fremde_position", text)
        self._ereignis("warnung", art="fremde_position", text=text)

    def _markt_offen(self, symbol: str, jetzt: datetime) -> bool:
        try:
            return markt_status(markt_von_symbol(symbol, self.einst), jetzt, self.einst).offen
        except ValueError:
            return False

    # ------------------------------------------------------------ Tagesverlust (harte Grenze)

    def _sperren(self, lage: Lage) -> None:
        """Tagesverlustgrenze erreicht: Tag sperren (Journal, Log, Beobachter). Nur einmal je Tag."""
        self.gesperrt = lage.tag_gesperrt = True
        grund = f"Tagesverlust {zahl(lage.tagesergebnis)} erreicht Grenze -{zahl(tagesverlust_grenze(lage))}"
        schliessen = bool(self.einst["risiko"]["tagesverlust_schliessen"])
        self.journal.tag_sperren(self.datum, grund)
        self.journal.ereignis("tagesverlust", grund)
        self.log("TAGESVERLUSTGRENZE ERREICHT: " + grund + ". Keine neuen Positionen bis morgen."
                 + (" Alle eigenen Positionen werden glattgestellt." if schliessen else ""))
        self._ereignis("tagesverlust", text=grund, schliessen=schliessen)

    def _tagesverlust_pruefen(self, jetzt: datetime) -> None:
        """Bei jedem Durchlauf (nicht nur bei neuen Kerzen): realisiert plus unrealisiert gegen die Grenze prüfen."""
        konto = self.broker.konto()
        lage = Lage(jetzt=jetzt, gesamtwert=konto.gesamtwert, einstellungen=self.einst,
                    tagesergebnis=konto.gesamtwert - self.startwert, tag_gesperrt=self.gesperrt)
        if not self.gesperrt and tagesverlust_erreicht(lage):
            self._sperren(lage)
        self._gesperrt_schliessen(jetzt)

    def _gesperrt_schliessen(self, jetzt: datetime) -> None:
        """Ist der Tag wegen der Verlustgrenze gesperrt, werden die eigenen Positionen glattgestellt (und bei einem
        Fehlschlag in jedem Durchlauf erneut versucht). Fremde Positionen bleiben unberührt."""
        if not (self.gesperrt and self.einst["risiko"]["tagesverlust_schliessen"]):
            return
        eigene = self._eigene_schluessel()
        for symbol in dict.fromkeys(p.symbol for p in self.broker.positionen() if abs(p.menge) > _EPS):
            if symbol_schluessel(symbol) in eigene and self._markt_offen(symbol, jetzt):
                self._versuchen(symbol, self._glattstellen, symbol, "Tagesverlustgrenze erreicht", jetzt)

    # ------------------------------------------------------------ Gewinnziele, die der Broker nicht führt

    def _ziele_pruefen(self, jetzt: datetime) -> None:
        """Ziele, die der Broker nicht als Order führt (Kraken): bei Erreichen glattstellen (inkl. Stornieren des Stops)."""
        for e in self.journal.eigene(self.modus):
            ziel, symbol = e["ziel"], e["symbol"]
            if ziel is None or not self._markt_offen(symbol, jetzt):
                continue
            menge = self._position(symbol)
            long = e["richtung"] == "long"
            if (long and menge <= _EPS) or (not long and menge >= -_EPS):
                continue                                   # die Position gibt es (noch) nicht oder nicht mehr
            kurs = self.broker.kurs(symbol)
            preis = (kurs.bid if long else kurs.ask)
            preis = kurs.letzter if preis is None else preis
            if preis is None or not ((long and preis >= ziel) or (not long and preis <= ziel)):
                continue
            text = f"{symbol}: Gewinnziel {ziel:g} erreicht (Kurs {preis:g})"
            self.log(text + ": Position wird glattgestellt")
            if self._versuchen(symbol, self._glattstellen, symbol, f"Gewinnziel {ziel:g} erreicht", jetzt):
                self.journal.ereignis("ziel_erreicht", text)
                self._ereignis("ziel_erreicht", symbol=symbol, ziel=ziel, preis=preis, text=text)

    # ------------------------------------------------------------ Lage, Positionen, Schutz

    def _position(self, symbol: str) -> float:
        return sum(p.menge for p in self.broker.positionen() if symbol_schluessel(p.symbol) == symbol_schluessel(symbol))

    def _wechselkurs(self, kurs_waehrung: str, konto_waehrung: str) -> float | None:
        """Wert einer Einheit Kurswährung in Kontowährung: 1.0 bei gleicher oder unbekannter Währung, sonst vom Broker,
        ersatzweise aus der Einstellung `wechselkurse`. None = kein Kurs (dann keine Eröffnung)."""
        von = (kurs_waehrung or "").strip().upper()
        nach = (konto_waehrung or self.einst["waehrung"]).strip().upper()
        if not von or von == nach:
            return 1.0
        jetzt = zu_utc(self.uhr())
        gemerkt = self._wechselkurse.get((von, nach))
        if gemerkt and jetzt - gemerkt[0] < WECHSELKURS_GUELTIG:
            return gemerkt[1]
        wk = None
        vom_broker = getattr(self.broker, "wechselkurs", None)
        if callable(vom_broker):
            try:
                wk = vom_broker(von, nach)
            except BrokerFehler as e:
                self.log(f"Wechselkurs {von}->{nach} vom Broker nicht lesbar ({e})")
        if not wechselkurs_gueltig(wk):
            wk = {str(k).upper(): v for k, v in self.einst["wechselkurse"].items()}.get(von)
        wk = float(wk) if wechselkurs_gueltig(wk) else None
        self._wechselkurse[(von, nach)] = (jetzt, wk)
        return wk

    def _lage(self, symbol: str, jetzt: datetime, mit_orders: bool = True) -> Lage:
        konto = self.broker.konto()
        positionen: dict[str, float] = {}
        for p in self.broker.positionen():           # mehrere Einträge zum selben Symbol (z. B. Margin und Spot) addieren
            positionen[p.symbol] = positionen.get(p.symbol, 0.0) + p.menge
        kurs = self.broker.kurs(symbol)
        offene, unbekannt = [], False
        if mit_orders:                               # offene Eröffnungsorders zählen bei den Eröffnungsregeln mit
            try:
                offene = self.broker.orders(nur_offene=True)
            except BrokerFehler as e:
                unbekannt = True                     # nicht lesbar: keine Eröffnung (Schließen bleibt möglich)
                self.log(f"{symbol}: offene Orders nicht lesbar ({e})")
        lage = Lage(jetzt=jetzt, gesamtwert=konto.gesamtwert, einstellungen=self.einst, symbol=symbol,
                    guthaben=konto.guthaben, positionen=positionen,
                    tagesergebnis=konto.gesamtwert - self.startwert,
                    orders_heute=self.journal.orders_heute(self.datum or jetzt.date().isoformat(), self.modus),
                    kurs=kurs, tag_gesperrt=self.gesperrt, datenordner=self.datenordner,
                    offene_orders=offene, orders_unbekannt=unbekannt, konto_waehrung=konto.waehrung,
                    wechselkurs=self._wechselkurs(kurs.waehrung, konto.waehrung))
        if not self.gesperrt and tagesverlust_erreicht(lage):
            self._sperren(lage)                      # das Glattstellen folgt am Ende des Durchlaufs
        return lage

    def _schutz_pruefen(self) -> None:
        """Jede eigene Position braucht einen Schutz-Stop beim Broker. Fremde Positionen werden nur gemeldet."""
        positionen = [p for p in self.broker.positionen() if p.menge]
        if not positionen:
            return
        eigene = self._eigene_schluessel()
        offene = self.broker.orders(nur_offene=True)
        for p in positionen:
            if symbol_schluessel(p.symbol) not in eigene:
                self._fremde_melden(p)
                continue
            gegen = "verkauf" if p.menge > 0 else "kauf"
            abgedeckt = sum(o.order.menge - o.ausgefuehrt for o in offene
                            if o.order.symbol == p.symbol and o.order.seite == gegen
                            and o.order.art in ("stop", "stop_limit"))
            if abgedeckt + 1e-9 < abs(p.menge) * 0.999:
                text = f"{p.symbol} {p.menge:+g} hat keinen ausreichenden Schutz-Stop (gedeckt: {abgedeckt:g})"
                self.log("WARNUNG: " + text)
                self.journal.ereignis("ohne_schutz", text)
                self._ereignis("warnung", art="ohne_schutz", text=text)
                if self.cfg["ohne_schutz"] == "schliessen":
                    self._versuchen(p.symbol, self._glattstellen, p.symbol, "Position ohne Schutz-Stop", self.uhr())

    def _glattstellen(self, symbol: str, grund: str, jetzt: datetime) -> bool:
        """Offene Orders des Symbols stornieren, Position zum Markt schließen (mit Risikoprüfung). True = Order gesendet."""
        if symbol in self._geschlossen:
            return False                 # schon in diesem Durchlauf geschlossen; die Positionsliste des Brokers kann noch alt sein
        menge = self._position(symbol)
        if not menge:
            return False
        order = Order(symbol, "verkauf" if menge > 0 else "kauf", abs(menge), notiz=grund)
        lage = self._lage(symbol, zu_utc(jetzt), mit_orders=False)
        pruefung = pruefen(order, lage)
        eid = self._entscheidung(symbol, None, pruefung, order, grund)
        if not pruefung.ok:
            self.log(f"{symbol}: Glattstellen ({grund}) abgelehnt: {'; '.join(pruefung.gruende)}")
            return False
        for o in self.broker.orders(nur_offene=True):
            if symbol_schluessel(o.order.symbol) == symbol_schluessel(symbol):
                self.broker.stornieren(o.id)
        stand = self.broker.senden(order)
        self._geschlossen.add(symbol)
        self._ausfuehrung(eid, stand, symbol)
        self.journal.ziel_loeschen(symbol, self.modus)           # die Position wird geschlossen: kein Ziel mehr zu überwachen
        self.log(f"{symbol}: glattgestellt ({grund}), {stand.status} @ {zahl(stand.preis, 4)}")
        self._ergebnisse_nachtragen()
        return True

    def _ergebnisse_nachtragen(self) -> None:
        """Papier-Broker: Trade-Ergebnisse (FIFO, netto) ins Journal zur auslösenden Entscheidung schreiben."""
        if not hasattr(self.broker, "trades"):
            return
        for t in self.broker.trades():
            if t["id"] <= self._letzter_trade:
                continue
            self._letzter_trade = t["id"]
            oid = t["order_id"]
            for s in self.broker.orders(nur_offene=False):
                if s.id == oid and " zu P" in s.order.notiz:         # Schutzorder: Ergebnis zum Eröffnungs-Eintrag
                    oid = s.order.notiz.rsplit(" zu ", 1)[1]
            self.journal.ergebnis_zu_order(oid, t["netto"], f"Trade {t['symbol']} netto")
            self._ereignis("ergebnis", symbol=t["symbol"], text=f"Trade {t['symbol']} netto", betrag=t["netto"],
                           order_id=oid)

    # ------------------------------------------------------------ Strategie

    def _symbol_bearbeiten(self, symbol: str, jetzt: datetime) -> None:
        dauer = takt_dauer(self.takt)
        kerzen = self.quelle.kerzen(symbol, self.takt, int(self.cfg["kerzen_fenster"]))
        kerzen = [k for k in kerzen if zu_utc(k.t) + dauer <= jetzt]      # nur abgeschlossene Kerzen
        if not kerzen or self._bewertet.get(symbol) == kerzen[-1].t:
            return                                                       # nichts Neues seit dem letzten Mal
        self._bewertet[symbol] = kerzen[-1].t
        alter = jetzt - (zu_utc(kerzen[-1].t) + dauer)
        if dauer < timedelta(days=1) and alter > max(3 * dauer, timedelta(minutes=15)):
            # z. B. gleich nach Börsenöffnung oder bei verspäteten Daten: die letzte Kerze ist von gestern. Ein Signal aus
            # ihr würde jetzt zum aktuellen Kurs gehandelt, obwohl es zur Lage von damals gehört.
            self.log(f"{symbol}: letzte abgeschlossene Kerze ({zu_utc(kerzen[-1].t):%Y-%m-%d %H:%M}) ist zu alt, "
                     "nicht bewertet; warte auf neue Daten")
            return
        lage = self._lage(symbol, jetzt)
        signal = self.strategie.bewerten(kerzen, lage)
        if signal is None:
            return
        if signal.richtung == "flat":
            if lage.menge(symbol):
                self._glattstellen(symbol, signal.begruendung or "Strategie: flat", jetzt)
            return
        if lage.menge(symbol):
            return                                                       # keine Aufstockung
        if signal.sicherheit < self.cfg["sicherheit_min"]:
            self._entscheidung(symbol, signal, None, None,
                               f"Sicherheit {signal.sicherheit:.2f} unter {self.cfg['sicherheit_min']}: nicht gehandelt")
            return
        if not wechselkurs_gueltig(lage.wechselkurs):          # ohne Wechselkurs lassen sich Menge und Risiko nicht rechnen
            grund = wechselkurs_fehlt_text(lage)
            self._entscheidung(symbol, signal, Ergebnis(False, [grund]), None, "nicht gehandelt: " + grund)
            self.log(f"{symbol}: Signal {signal.richtung} abgelehnt: {grund}")
            return
        order = signal_zu_order(signal, symbol, lage)
        if order is None:
            self._entscheidung(symbol, signal, None, None,
                               "Menge 0 (Stop zu nah oder zu weit, Konto zu klein): nicht gehandelt")
            self.log(f"{symbol}: Signal {signal.richtung} ohne handelbare Menge")
            return
        pruefung = pruefen(order, lage)
        eid = self._entscheidung(symbol, signal, pruefung, order)
        if not pruefung.ok:
            self.log(f"{symbol}: Signal {signal.richtung} abgelehnt: {'; '.join(pruefung.gruende)}")
            return
        stand = self.broker.senden(order)
        self._ausfuehrung(eid, stand, symbol)
        self._eigene_merken(order, stand)
        self.log(f"{symbol}: {signal.richtung} {order.menge:g} Stück, Stop {order.stop_loss:g}, Ziel "
                 f"{order.take_profit if order.take_profit is None else format(order.take_profit, 'g')} -> "
                 f"{stand.status} @ {zahl(stand.preis, 4)} ({signal.begruendung})")

    # ------------------------------------------------------------ Einschätzungen

    def _kurs_zu_zeit(self, wert: str, faellig: datetime) -> float | None:
        jetzt = zu_utc(self.uhr())
        try:
            if jetzt - faellig <= timedelta(minutes=10):
                return self.quelle.kurs(wert).mitte
            for k in reversed(self.quelle.kerzen(wert, "1h", 500)):      # sonst Schlusskurs der Stundenkerze
                if zu_utc(k.t) <= faellig:
                    return k.c
        except BrokerFehler:
            return None
        return None

    def _einschaetzungen_auswerten(self, jetzt: datetime) -> None:
        for e in self.journal.auswerten(self._kurs_zu_zeit, jetzt):
            self.log(f"Einschätzung #{e['id']} {e['wert']} {e['richtung']} in {e['horizont']}: "
                     f"{e['aenderung_prozent']:+.2f} % -> {'Treffer' if e['treffer'] else 'kein Treffer'}")


def bauen(modus: str, einst: dict, datenordner: Path | None = None, symbole: list[str] | None = None,
          wiedergabe: WiedergabeQuelle | None = None, ausgabe: Callable[[str], None] = print,
          heute: date | None = None, strategie: str | None = None) -> Autopilot:
    """Baut den Autopiloten für eine Betriebsart. Wirft Abbruch, wenn die Betriebsart nicht erlaubt ist."""
    datenordner = Path(datenordner) if datenordner else _einst.datenordner()
    modus = modus_pruefen(modus, einst, datenordner, heute)
    if modus == "papier":
        quelle = wiedergabe if wiedergabe is not None else quelle_aus_einstellungen(einst)
        broker: Broker = PapierBroker(quelle.kurs, einst, datenordner / "papier.db", kerzengeber=quelle.kerzen,
                                      uhr=wiedergabe.jetzt if wiedergabe else None,
                                      wechselkursgeber=getattr(quelle, "wechselkurs", None))
    else:
        broker = broker_aus_einstellungen(einst, demo=(modus == "demo"))
        quelle = BrokerQuelle(broker)
        nur_pruefen = bool(getattr(broker, "nur_pruefen", False))
        if modus == "demo" and not (broker.papier or nur_pruefen):
            raise Abbruch("Betriebsart demo, aber der Broker meldet ein Echtgeld-Konto. Abbruch. "
                          "Bitte ein Demokonto einstellen (IBKR: ibkr.konto_id beginnt mit DU).")
        if modus == "echt" and (broker.papier or nur_pruefen):
            raise Abbruch("Betriebsart echt, aber der Broker meldet ein Demokonto bzw. 'nur prüfen'. Abbruch.")
    s = strategie_holen(strategie or einst["strategie"]["name"], einst)
    journal = Journal(datenordner / "journal.db", einst, uhr=wiedergabe.jetzt if wiedergabe else None)
    return Autopilot(broker, quelle, s, journal, einst, datenordner, modus, symbole,
                     uhr=wiedergabe.jetzt if wiedergabe else None,
                     warten=wiedergabe.weiter if wiedergabe else None, ausgabe=ausgabe, heute=heute)
