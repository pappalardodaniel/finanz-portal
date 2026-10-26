"""Einstellungen: Standardwerte, Laden aus TOML, Datenordner.

Reihenfolge: Standardwerte -> Datei `~/.config/kitrader/einstellungen.toml` (oder Pfad aus KITRADER_KONFIG).
Die Datei muss nur die Werte enthalten, die geändert werden sollen. Schlüssel (API-Zugänge) gehören
nie ins Repo, sondern in diese Datei oder in Umgebungsvariablen.
"""
from __future__ import annotations

import copy
import os
import re
import tomllib
from pathlib import Path

from . import symbole as _symbole

MODI = ("papier", "demo", "echt")
KI_TREIBER = ("claude_cli", "anthropic", "aus")
LIVE_TAKTE = ("1m", "5m", "15m", "1h", "1d")


class EinstellungsFehler(ValueError):
    """Einstellungen sind unlesbar oder unsinnig. Text ist für Menschen lesbar."""


STANDARD: dict = {
    # Betriebsart: papier (Simulation), demo (Demokonto des Brokers), echt (echtes Geld, braucht Freigabe)
    "modus": "papier",
    "waehrung": "EUR",
    # Welcher echte Broker liefert Kurse bzw. Demo/Echt-Konto: "ibkr" oder "kraken"
    "broker": "ibkr",
    # Woher die Kurse für den Papierhandel kommen: "ibkr", "kraken" oder "wiedergabe" (nur Testdaten)
    "kursquelle": "ibkr",
    # Anbindung Interactive Brokers (Client Portal Gateway läuft lokal). Leere Werte = Standard der Anbindung.
    "ibkr": {"basis_url": "", "konto_id": ""},      # konto_id des Demokontos beginnt bei IBKR mit "DU"
    "kraken": {"waehrung": "EUR", "spot_paare": [], "hebel": 0},   # hebel 0 = kein Margin/Short
    "papier": {
        "startguthaben": 10000.0,
        "short_erlaubt": True,      # der Papier-Broker darf leerverkaufen (die Risikoprüfung ist strenger)
        "hebel": 1.0,               # Summe aller Positionen darf Gesamtwert x hebel nicht übersteigen
    },
    "kosten": {                     # gelten für Papierhandel und Backtest
        "gebuehr_fest": 1.0,        # je Ausführung, in Kontowährung
        "gebuehr_prozent": 0.05,    # je Ausführung, in % vom Umsatz
        "schlupf_prozent": 0.02,    # Marktorders/Stops werden um diesen Anteil schlechter ausgeführt
        "spread_prozent": 0.05,     # nur Backtest: Abstand Bid/Ask insgesamt in % vom Kurs
    },
    "risiko": {
        "tagesverlust_max": 100.0,          # absolut, in Kontowährung
        "tagesverlust_max_prozent": 1.0,    # in % vom Gesamtwert (es gilt, was zuerst erreicht wird)
        # Harte Grenze: bei Erreichen (realisiert plus unrealisiert) stellt der Autopilot alle EIGENEN Positionen glatt
        # und sperrt neue bis zum nächsten Tag. false = nur sperren, offene Positionen behalten ihren Stop.
        "tagesverlust_schliessen": True,
        "position_max_prozent": 10.0,       # Wert einer Position in % vom Gesamtwert
        "positionen_max": 3,                # gleichzeitig offene Positionen
        "orders_max_pro_tag": 20,
        "stop_loss_pflicht": True,          # jede Eröffnung braucht einen Stop-Loss
        "risiko_pro_trade_prozent": 0.5,    # (Einstieg - Stop) x Menge in % vom Gesamtwert
        "short_erlaubt": False,
        "kurs_max_alter_s": 60,             # ältere Kurse: kein Handel
        "keine_eroeffnung_min_vor_schluss": 15,   # in den letzten Minuten vor Handelsschluss nichts Neues
        "standard_markt": "us",             # Markt für Symbole, die unter `maerkte` nicht stehen
        "mengenschritt": 1.0,               # kleinste Menge (Aktien 1, Krypto z. B. 0.0001)
    },
    # Wechselkurse als Reserve, wenn der Broker keinen liefert: Wert einer Einheit der Währung in Kontowährung,
    # z. B. USD = 0.92 bei Kontowährung EUR. Ohne Kurs (Broker oder hier) wird in fremder Währung nicht eröffnet.
    "wechselkurse": {},
    # Symbol -> Markt, z. B. SAP = "xetra". Nicht genannte Symbole gelten als `standard_markt`.
    "maerkte": {},
    # Mengenschritt je Symbol, z. B. XBTEUR = 0.0001
    "mengenschritte": {},
    # Handelszeiten je Markt (Ortszeit der Börse). tage: 0 = Montag ... 6 = Sonntag. immer = true: rund um die Uhr.
    "handelszeiten": {
        "us": {"zeitzone": "America/New_York", "von": "09:30", "bis": "16:00", "tage": [0, 1, 2, 3, 4]},
        "xetra": {"zeitzone": "Europe/Berlin", "von": "09:00", "bis": "17:30", "tage": [0, 1, 2, 3, 4]},
        "krypto": {"immer": True},
    },
    "strategie": {
        "name": "ausbruch",
        "ausbruch": {"range_kerzen": 6, "crv": 2.0, "min_breite_prozent": 0.2},
        "vwap": {"abstand_atr": 2.0, "stop_atr": 1.0, "atr_laenge": 14, "min_kerzen": 20, "min_crv": 1.0},
    },
    "backtest": {
        "startkapital": 10000.0,
        "train_anteil": 0.7,        # erster Teil der Daten zum Einstellen, Rest ist zurückgehalten (Test)
        "faltungen": 1,             # Walk-forward-Abschnitte im Testteil
        "tage_pro_jahr": 252,       # für das annualisierte Sharpe (Krypto: 365)
        "tagesende_flat": True,     # Daytrading: am Tagesende alles schließen
        "kerzen_fenster": 500,      # so viele Kerzen sieht die Strategie höchstens
        "min_trades_training": 5,   # Einstellungen mit weniger Trades im Training zählen nicht
    },
    "journal": {
        # Mindestbewegung in %, ab der eine Einschätzung "steigt"/"fällt" als Treffer zählt
        # (bei "seitwaerts": Bewegung muss kleiner als die Schwelle bleiben)
        "schwellen": {"1h": 0.3, "4h": 0.5, "1d": 1.0, "1w": 2.0, "1M": 4.0, "3M": 7.0, "1J": 12.0},
    },
    # Weltlage aus OSIRIS (osiris.py). `adresse` ändern, wenn du OSIRIS selbst hostest.
    "osiris": {
        "adresse": "https://osirisai.live",
        "pause_s": 1.5,             # Pause zwischen zwei Feed-Abrufen (höflich)
        "zeitlimit_s": 30,          # je Abruf
        "mindestabstand_s": 300,    # jüngere Schnappschüsse werden nicht erneut geholt (Feeds ändern sich nicht schneller)
        "behalten_tage": 30,        # ältere Schnappschüsse werden gelöscht
        "automatisch": True,        # die KI-Einschätzung holt veraltete Daten selbst (sonst nur gespeicherte)
    },
    # KI-Einschätzung (ki.py). Die KI sendet nie Orders.
    "ki": {
        "treiber": "claude_cli",    # "claude_cli" (lokale Claude-CLI, dein Abo), "anthropic" (API-Schlüssel) oder "aus"
        "modell": "claude-sonnet-5-5",          # nur für "anthropic"
        "anthropic_url": "https://api.anthropic.com/v1/messages",
        "claude_befehl": "claude",  # Name oder Pfad der Claude-CLI
        "claude_modell": "sonnet",  # Modell der Claude-CLI (--model; "sonnet", "opus" oder volle Modell-ID); "" = Standardmodell der CLI
        "zeitlimit_s": 240,
        "kerzen_im_kontext": 30,    # so viele letzte Kerzen stehen einzeln im Kontext
        "historie_reihe": "",       # nicht mehr wirksam für den Muster-Abschnitt der KI (Muster-Engine); nur noch für den älteren Aufbau der Ereignisstudie
        "osiris_max_alter_h": 6,    # ältere OSIRIS-Daten werden (bei osiris.automatisch) neu geholt
        # Optionaler Filter im Autopiloten: ein Eröffnungssignal gegen die kurzfristige KI-Richtung wird verworfen.
        # Die Risikoprüfung gilt trotzdem immer.
        "filter": False,
        "filter_min_wahrscheinlichkeit": 0.55,  # so sicher muss die KI sein, damit sie ein Signal verwirft
        "filter_max_alter_h": 6,    # so lange gilt eine KI-Richtung für den Filter, dann wird neu gefragt
    },
    # Bedienoberfläche im Browser (oberflaeche/). Bindet immer nur an 127.0.0.1.
    "oberflaeche": {
        "port": 8850,
        "browser_oeffnen": True,    # beim Start den Browser öffnen
        "live_takt": "5m",          # Kerzenlänge im Reiter „Live“: 1m, 5m, 15m, 1h oder 1d
        "live_kerzen": 96,          # so viele Kerzen zeigt der Chart (20 bis 300)
    },
    # Zeitplan für `python3 -m kitrader start` (start.py). Zeitzone und Uhrzeit nur für die Morgenlage.
    "zeitplan": {
        "morgenlage": "08:00",      # täglich zu dieser Uhrzeit (HH:MM, Ortszeit von `zeitzone`); "" = aus
        "zeitzone": "Europe/Berlin",
        "osiris_alle_h": 6,         # OSIRIS-Weltlage so oft neu holen (Stunden); 0 = aus
        "nachrichten_alle_h": 2,    # Nachrichten-Radar (nachrichten.py: 45 Feeds und GDELT) so oft neu holen (Stunden); 0 = aus
        "welt_alle_h": 2,           # Welt-Rotation (welt.py: Schnappschuss der Märkte) so oft neu holen (Stunden); 0 = aus
    },
    # Watchlist: für diese Werte schätzt die Morgenlage (lage.py) kurz/mittel/lang ein; der Reiter „Live“ zeigt sie neben autopilot.symbole.
    "watchlist": {"werte": ["BTC/EUR", "ETH/EUR"]},
    # Symbol-Eingabe (symbole.py): BTC, Bitcoin, XBT/EUR ... werden zu BTC/EUR. Ohne Gegenwährung in der Eingabe gilt diese.
    "symbole": {"gegenwaehrung": "EUR"},
    # Telegram (telegram.py). Token und Chat-ID stehen in ~/.config/kitrader/telegram.env, nie hier.
    "telegram": {
        "aktiv": True,              # Meldungen senden (nur wenn telegram.env eingerichtet ist)
        "wache": False,             # im Autopiloten mitlaufen und auf "stop" / "status" aus dem erlaubten Chat hören
        "api_url": "https://api.telegram.org",
        "zeitlimit_s": 10,          # je Anfrage an Telegram
        "poll_s": 25,               # Wartezeit je Long-Polling-Anfrage der Wache
        "max_alter_s": 600,         # ältere Nachrichten ("stop" von gestern) löst die Wache nicht mehr aus
        "ablehnungen_alle_s": 600,  # Ablehnungen der Risikoprüfung werden so lange gesammelt und zusammen gemeldet
    },
    "autopilot": {
        "symbole": [],
        "takt": "5m",
        "pause_s": 30,
        "sicherheit_min": 0.0,          # Signale mit geringerer Sicherheit (0-1) werden nur protokolliert; 0 = alle
        "nur_daytrading": True,         # vor Handelsschluss alles glattstellen
        "tagesabschluss_min_vor_schluss": 5,
        "ohne_schutz": "schliessen",    # Position ohne Schutz-Stop: "schliessen" oder "warnen"
        "fehler_max": 5,                # so viele Fehler in Folge, dann bricht der Autopilot ab
        "kerzen_fenster": 500,
    },
}


def _tief_mischen(basis: dict, zusatz: dict) -> dict:
    ergebnis = copy.deepcopy(basis)
    for k, v in zusatz.items():
        if isinstance(v, dict) and isinstance(ergebnis.get(k), dict):
            ergebnis[k] = _tief_mischen(ergebnis[k], v)
        else:
            ergebnis[k] = copy.deepcopy(v)
    return ergebnis


def mit_standard(teil: dict | None) -> dict:
    """Ergänzt ein (evtl. unvollständiges) dict um die Standardwerte. Mehrfaches Anwenden ändert nichts."""
    return _tief_mischen(STANDARD, teil or {})


def datenordner() -> Path:
    """Ordner für papier.db, journal.db, NOTAUS und ECHTGELD_FREIGABE (wird bei Bedarf angelegt)."""
    pfad = Path(os.environ.get("KITRADER_DATEN") or Path.home() / ".local" / "share" / "kitrader")
    pfad.mkdir(parents=True, exist_ok=True)
    return pfad


def konfig_pfad() -> Path:
    return Path(os.environ.get("KITRADER_KONFIG") or Path.home() / ".config" / "kitrader" / "einstellungen.toml")


def beispiel_pfad() -> Path:
    return Path(__file__).with_name("einstellungen.beispiel.toml")


def pruefen(einst: dict) -> dict:
    """Grobe Plausibilitätsprüfung; wirft EinstellungsFehler."""
    if einst["modus"] not in MODI:
        raise EinstellungsFehler(f"modus muss einer von {MODI} sein, nicht {einst['modus']!r}")
    r = einst["risiko"]
    for schluessel in ("tagesverlust_max", "tagesverlust_max_prozent", "position_max_prozent",
                       "risiko_pro_trade_prozent", "kurs_max_alter_s"):
        if not isinstance(r[schluessel], (int, float)) or r[schluessel] < 0:
            raise EinstellungsFehler(f"risiko.{schluessel} muss eine Zahl >= 0 sein")
    if not isinstance(r["tagesverlust_schliessen"], bool):
        raise EinstellungsFehler("risiko.tagesverlust_schliessen muss true oder false sein")
    for waehrung, kurs in einst["wechselkurse"].items():
        if isinstance(kurs, bool) or not isinstance(kurs, (int, float)) or not 0 < kurs < float("inf"):
            raise EinstellungsFehler(f"wechselkurse.{waehrung} muss eine Zahl > 0 sein")
    if einst["ki"]["treiber"] not in KI_TREIBER:
        raise EinstellungsFehler(f"ki.treiber muss einer von {KI_TREIBER} sein, nicht {einst['ki']['treiber']!r}")
    if not 0 <= einst["ki"]["filter_min_wahrscheinlichkeit"] <= 1:
        raise EinstellungsFehler("ki.filter_min_wahrscheinlichkeit muss zwischen 0 und 1 liegen")
    if not 0 < einst["backtest"]["train_anteil"] < 1:
        raise EinstellungsFehler("backtest.train_anteil muss zwischen 0 und 1 liegen")
    port = einst["oberflaeche"]["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise EinstellungsFehler("oberflaeche.port muss eine ganze Zahl zwischen 0 und 65535 sein")
    o = einst["oberflaeche"]
    if o["live_takt"] not in LIVE_TAKTE:
        raise EinstellungsFehler(f"oberflaeche.live_takt muss einer von {LIVE_TAKTE} sein, nicht {o['live_takt']!r}")
    if isinstance(o["live_kerzen"], bool) or not isinstance(o["live_kerzen"], int) or not 20 <= o["live_kerzen"] <= 300:
        raise EinstellungsFehler("oberflaeche.live_kerzen muss eine ganze Zahl zwischen 20 und 300 sein")
    z = einst["zeitplan"]
    if not isinstance(z["morgenlage"], str) or (z["morgenlage"] and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", z["morgenlage"])):
        raise EinstellungsFehler("zeitplan.morgenlage muss eine Uhrzeit wie \"08:00\" sein (oder \"\" für aus)")
    if isinstance(z["osiris_alle_h"], bool) or not isinstance(z["osiris_alle_h"], (int, float)) or not 0 <= z["osiris_alle_h"] <= 168:
        raise EinstellungsFehler("zeitplan.osiris_alle_h muss eine Zahl zwischen 0 (aus) und 168 (Stunden) sein")
    for schluessel in ("nachrichten_alle_h", "welt_alle_h"):
        if isinstance(z[schluessel], bool) or not isinstance(z[schluessel], (int, float)) or not 0 <= z[schluessel] <= 168:
            raise EinstellungsFehler(f"zeitplan.{schluessel} muss eine Zahl zwischen 0 (aus) und 168 (Stunden) sein")
    w = einst["watchlist"]["werte"]
    if not isinstance(w, list) or not all(isinstance(x, str) and x.strip() for x in w):
        raise EinstellungsFehler("watchlist.werte muss eine Liste von Symbolen sein, z. B. [\"BTC/EUR\", \"ETH/EUR\"]")
    gw = einst["symbole"]["gegenwaehrung"]
    if not isinstance(gw, str) or not re.fullmatch(r"[A-Za-z]{3,4}", gw.strip()):
        raise EinstellungsFehler("symbole.gegenwaehrung muss ein Währungscode wie \"EUR\" oder \"USD\" sein")
    einst["symbole"]["gegenwaehrung"] = gw.strip().upper()
    ap = einst["autopilot"]["symbole"]
    if not isinstance(ap, list) or not all(isinstance(x, str) and x.strip() for x in ap):
        raise EinstellungsFehler("autopilot.symbole muss eine Liste von Symbolen sein, z. B. [\"BTC/EUR\"]")
    try:                    # BTC, Bitcoin, XBT/EUR ... -> BTC/EUR (bei Kraken als Kursquelle; Aktien bleiben unverändert)
        einst["watchlist"]["werte"] = _symbole.kanonisch_liste(w, einst)
        einst["autopilot"]["symbole"] = _symbole.kanonisch_liste(ap, einst)
    except _symbole.SymbolFehler as e:
        raise EinstellungsFehler(f"Symbol in den Einstellungen (watchlist.werte oder autopilot.symbole): {e}") from None
    t = einst["telegram"]
    for schluessel in ("zeitlimit_s", "poll_s", "max_alter_s", "ablehnungen_alle_s"):
        if isinstance(t[schluessel], bool) or not isinstance(t[schluessel], (int, float)) or t[schluessel] <= 0:
            raise EinstellungsFehler(f"telegram.{schluessel} muss eine Zahl > 0 sein")
    url = str(t["api_url"])
    if not (url.startswith("https://") or url.startswith(("http://127.0.0.1", "http://localhost"))):
        raise EinstellungsFehler("telegram.api_url muss mit https:// beginnen (http:// nur für 127.0.0.1/localhost)")
    return einst


def laden(pfad: str | Path | None = None) -> dict:
    """Standardwerte plus Datei. Fehlt die Datei, gelten die Standardwerte."""
    pfad = Path(pfad) if pfad else konfig_pfad()
    teil: dict = {}
    if pfad.is_file():
        try:
            with open(pfad, "rb") as f:
                teil = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise EinstellungsFehler(f"{pfad} ist keine gültige TOML-Datei: {e}") from None
    return pruefen(mit_standard(teil))
