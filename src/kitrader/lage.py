"""Morgenlage: für jeden Wert der Watchlist die KI-Einschätzung kurz/mittel/lang, dazu OSIRIS-Veränderungen und die
bisherige KI-Trefferquote. Ausgabe als Text (Terminal, Telegram) und als JSON-Datei `morgenlage.json` im Datenordner,
die die Oberfläche lesen kann.

    python3 -m kitrader morgenlage [--watchlist BTC/EUR,ETH/EUR] [--treiber aus|claude_cli|anthropic]

Je Wert ein KI-Aufruf (Treiber `claude_cli`: etwa 25 Sekunden, über dein Claude-Abo). Mit `--treiber aus` kostet es nichts:
dann gibt es nur Kurs, Veränderungen, OSIRIS und Trefferquote, aber keine Einschätzung. Jede gültige KI-Antwort kommt wie
sonst ins Journal (Trefferquote). Die KI sendet nie Orders; das ist eine Lageübersicht, keine Anlageberatung.

Aufbau von `morgenlage.json` (Version 1):
  erstellt, treiber, takt, hinweis,
  werte: [{wert, preis, kerzen_bis, laufend (letzte Kerze noch nicht abgeschlossen), aenderung_prozent: {"1": .., "7": .., "30": ..},
           atr_prozent,
           prognosen: {kurz|mittel|lang: {horizont, richtung, wahrscheinlichkeit, begruendung, gegenargumente,
                                          was_widerlegt, journal_id}},
           warnungen: [..], fehler: ""}],
  osiris: {stand, vorher, alter_h, veraenderung (Text), hinweis},
  nachrichten: {stand, text} oder null (Nachrichten-Radar aus nachrichten.db, falls vorhanden),
  welt: {stand, text} oder null (Welt-Rotation aus welt.db, falls vorhanden),
  trefferquote: {"1d"|"3M"|"1J": {n, treffer, quote (% oder null), offen}}
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import einstellungen as _einst
from . import ki as _ki
from . import nachrichten as _nachrichten
from . import osiris as _osiris
from . import symbole as _symbole
from . import telegram as _telegram
from . import welt as _welt
from .autopilot import Abbruch
from .broker.basis import Kerze
from .hilfen import jetzt_utc, takt_dauer, zahl, zu_utc
from .journal import Journal
from .kursquelle import quelle_aus_einstellungen

DATEI = "morgenlage.json"
VERSION = 1
HINWEIS = ("Einschätzungen der KI sind Denkanstöße mit gemessener Trefferquote, keine Gewissheit und keine Anlageberatung. "
           "Die KI sendet nie Orders.")
FENSTER = (1, 7, 30)                       # Änderung über so viele Kerzen (bei Tageskerzen also Tage)
GEKUERZT = "\n… (gekürzt, vollständig in morgenlage.json und im Terminal)"
TEXT_KI = 160                              # so viele Zeichen der KI-Begründung stehen im Text


def watchlist(einst: dict, angabe: str | None = None) -> list[str]:
    """Werte der Watchlist: `angabe` (Komma getrennt, z. B. von der Kommandozeile) oder `[watchlist] werte`."""
    roh = [w.strip() for w in angabe.split(",")] if angabe else list(einst["watchlist"]["werte"])
    werte = _symbole.kanonisch_liste([w for w in roh if w], einst)      # BTC, Bitcoin, XBT/EUR ... -> BTC/EUR
    if not werte:
        raise ValueError("Die Watchlist ist leer (--watchlist A,B oder [watchlist] werte in den Einstellungen).")
    return werte


def _aenderungen(kerzen: list[Kerze]) -> dict[str, float | None]:
    letzter = kerzen[-1].c
    erg: dict[str, float | None] = {}
    for f in FENSTER:
        erg[str(f)] = None if f >= len(kerzen) or not kerzen[-1 - f].c else (letzter / kerzen[-1 - f].c - 1) * 100
    return erg


def _prognose_dict(p: _ki.Prognose) -> dict:
    return {"horizont": p.horizont, "richtung": p.richtung, "wahrscheinlichkeit": p.wahrscheinlichkeit,
            "begruendung": p.begruendung, "gegenargumente": p.gegenargumente, "was_widerlegt": p.was_widerlegt,
            "journal_id": p.journal_id}


def trefferquote(journal: Journal) -> dict[str, dict]:
    """Je Horizont n, Treffer, Quote in % (None ohne ausgewertete Fälle) und offen, nur für KI-Einschätzungen."""
    erg = {}
    alle = _ki.ki_trefferquote(journal)
    for _, h in _ki.PROGNOSEN:                               # nur die drei Horizonte, die die KI einschätzt
        e = alle[h]
        erg[h] = {"n": e["n"], "treffer": e["treffer"], "quote": e["treffer"] / e["n"] * 100 if e["n"] else None,
                  "offen": e["offen"]}
    return erg


def osiris_veraenderung(lage: _osiris.Lage) -> str:
    """Der Abschnitt „Veränderung“ aus dem OSIRIS-Text (gegenüber dem vorigen Schnappschuss)."""
    i = lage.text.find("Veränderung")
    return lage.text[i:].strip() if i >= 0 else "Veränderung: nicht verfügbar."


def erstellen(einst: dict, ordner: Path, werte: list[str], treiber=None, takt: str = "1d", anzahl: int = 250,
              kerzen_geber: Callable[[str, str, int], list[Kerze]] | None = None, lage: _osiris.Lage | None = None,
              journal: Journal | None = None, historie=None, jetzt: datetime | None = None) -> dict:
    """Baut die Morgenlage als Wörterbuch (das ist zugleich der Inhalt der JSON-Datei). Fehler bei einem Wert (keine
    Kerzen, KI nicht erreichbar, ungültige Antwort) stehen bei diesem Wert unter "fehler"; die anderen laufen weiter."""
    einst = _einst.mit_standard(einst)
    jetzt = zu_utc(jetzt or jetzt_utc())
    name, frage = _ki._treiber_waehlen(treiber, einst)
    geber = kerzen_geber or quelle_aus_einstellungen(einst).kerzen
    hinweis = ""
    if lage is None:
        lage, hinweis = _ki._osiris_lage(einst, ordner, jetzt)
    eigenes = journal is None
    journal = journal or Journal(ordner / "journal.db", einst)
    try:
        eintraege = []
        for wert in werte:
            e: dict = {"wert": wert, "preis": None, "kerzen_bis": None, "laufend": False, "aenderung_prozent": {},
                       "atr_prozent": None, "prognosen": {}, "warnungen": [], "fehler": ""}
            eintraege.append(e)
            try:
                kerzen = sorted(geber(wert, takt, anzahl), key=lambda k: k.t)
                if len(kerzen) < 2:
                    raise _ki.KiFehler(f"zu wenige Kerzen für {wert} ({len(kerzen)})")
            except Exception as fehler:                      # Kursquelle weg, Symbol unbekannt ...
                e["fehler"] = f"Keine Kerzen: {fehler}"
                continue
            kz = _ki.kennzahlen(kerzen)
            e.update(preis=kerzen[-1].c, kerzen_bis=zu_utc(kerzen[-1].t).isoformat(), aenderung_prozent=_aenderungen(kerzen),
                     atr_prozent=kz["atr_prozent"],
                     laufend=zu_utc(kerzen[-1].t) + takt_dauer(takt) > jetzt)    # letzte Kerze noch nicht abgeschlossen (Kraken liefert sie mit)
            if frage is None:
                continue                                      # Treiber „aus“: keine Einschätzung, keine Kosten
            try:
                ei = _ki.einschaetzen(wert, kerzen, treiber=treiber, einstellungen=einst, journal=journal, lage=lage,
                                      historie=historie, jetzt=jetzt, ordner=ordner)
                e["prognosen"] = {n: _prognose_dict(p) for n, p in ei.prognosen.items()}
                e["warnungen"] = list(ei.warnungen)
            except _ki.KiFehler as fehler:
                e["fehler"] = f"KI: {fehler}"
        return {
            "version": VERSION, "erstellt": jetzt.isoformat(), "treiber": name, "takt": takt, "hinweis": HINWEIS,
            "werte": eintraege,
            "osiris": {"stand": lage.stand.isoformat() if lage.stand else None,
                       "vorher": lage.vorher.isoformat() if lage.vorher else None,
                       "alter_h": None if lage.stand is None else max(0.0, lage.alter(jetzt).total_seconds() / 3600),
                       "veraenderung": osiris_veraenderung(lage), "hinweis": hinweis},
            "nachrichten": _nachrichten.morgen_block(einst, ordner, jetzt),
            "welt": _welt.morgen_block(einst, ordner, jetzt),
            "trefferquote": trefferquote(journal),
        }
    finally:
        if eigenes:
            journal.beenden()


# ----------------------------------------------------------------------------- Text

def _vorzeichen(x: float | None) -> str:
    return "-" if x is None else f"{x:+.1f} %".replace(".", ",")


def _kuerzen_zeile(text: str, n: int) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def als_text(m: dict, kompakt: bool = False) -> str:
    """Lesbarer Text. `kompakt`: kürzere Begründungen und höchstens 12 Zeilen OSIRIS-Veränderung (für Telegram)."""
    t = datetime.fromisoformat(m["erstellt"])
    tag = m["takt"] == "1d"
    z = [f"MORGENLAGE {t:%Y-%m-%d %H:%M} UTC (Kerzen {m['takt']}, KI-Treiber {m['treiber']})"]
    for w in m["werte"]:
        z.append("")
        if w["preis"] is None:
            z.append(f"{w['wert']}: {w['fehler']}")
            continue
        a = w["aenderung_prozent"]
        einheit = "T" if tag else "K"
        z.append(f"{w['wert']}  {zahl(w['preis'], 4 if w['preis'] < 10 else 2)}  "
                 + ", ".join(f"{f} {einheit} {_vorzeichen(a.get(f))}" for f in ("1", "7", "30"))
                 + (" (letzte Kerze läuft noch)" if w.get("laufend") else "")
                 + (f", Schwankung (ATR) {zahl(w['atr_prozent'])} %" if w["atr_prozent"] is not None else ""))
        if w["fehler"]:
            z.append(f"  {w['fehler']}")
        if m["treiber"] == "aus":
            z.append("  KI aus: keine Einschätzung (mit --treiber claude_cli ein Aufruf je Wert).")
        for name, titel in (("kurz", "kurz (1 Tag)"), ("mittel", "mittel (3 Monate)"), ("lang", "lang (1 Jahr)")):
            p = w["prognosen"].get(name)
            if p:
                z.append(f"  {titel:<18} {p['richtung'].upper()} {p['wahrscheinlichkeit'] * 100:.0f} %: "
                         + _kuerzen_zeile(p["begruendung"], TEXT_KI if not kompakt else 110))
        for warnung in w["warnungen"]:
            z.append(f"  Hinweis: {_kuerzen_zeile(warnung, 200)}")
    o = m["osiris"]
    z.append("")
    if o["stand"] is None:
        z.append("OSIRIS: kein Schnappschuss gespeichert (python3 -m kitrader osiris abrufen).")
    else:
        z.append(f"OSIRIS, Stand {datetime.fromisoformat(o['stand']):%Y-%m-%d %H:%M} UTC (Alter {zahl(o['alter_h'], 1)} h):")
        zeilen = o["veraenderung"].splitlines()
        if kompakt and len(zeilen) > 13:
            zeilen = zeilen[:13] + [f"  … {len(zeilen) - 13} weitere Zeilen"]
        z += zeilen
    if o["hinweis"]:
        z.append(o["hinweis"])
    for titel, schluessel in (("NACHRICHTEN-RADAR (ungeprüfte Meldungen, python3 -m kitrader nachrichten radar)", "nachrichten"),
                              ("WELT-ROTATION (python3 -m kitrader welt)", "welt")):
        block = m.get(schluessel)
        if block:
            stand = datetime.fromisoformat(block["stand"])
            z += ["", f"{titel}, Stand {stand:%Y-%m-%d %H:%M} UTC:", *block["text"].splitlines()[:6 if kompakt else 12]]
    z.append("")
    z.append("KI-Trefferquote (Journal, nur KI-Einschätzungen):")
    for h, name in (("1d", "kurz"), ("3M", "mittel"), ("1J", "lang")):
        q = m["trefferquote"][h]
        quote = "noch keine" if q["quote"] is None else f"{q['quote']:.0f} %"
        z.append(f"  {name} ({h}): ausgewertet n={q['n']}, Treffer {q['treffer']}, Quote {quote}, noch offen {q['offen']}")
    z.append("  (Unter 30 ausgewerteten Fällen je Horizont ist die Quote kaum aussagekräftig.)")
    z += ["", m["hinweis"]]
    return "\n".join(z)


def kuerzen(text: str, grenze: int = _telegram.MAX_TEXT) -> str:
    """Kürzt auf höchstens `grenze` Zeichen (Telegram: 4096, hier 3900 mit Reserve), am Zeilenende, mit Vermerk."""
    if len(text) <= grenze:
        return text
    rest = grenze - len(GEKUERZT)
    schnitt = text.rfind("\n", 0, rest)
    return text[:schnitt if schnitt > rest // 2 else rest].rstrip() + GEKUERZT


# ----------------------------------------------------------------------------- Datei, Telegram, Befehl

def speichern(m: dict, ordner: Path) -> Path:
    """Schreibt `morgenlage.json` im Datenordner (erst eine Hilfsdatei, dann ersetzen: die Oberfläche liest nie Halbes)."""
    ziel = Path(ordner) / DATEI
    ziel.parent.mkdir(parents=True, exist_ok=True)
    hilf = ziel.with_name(DATEI + ".tmp")
    hilf.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(hilf, ziel)
    return ziel


def melden(m: dict, einst: dict) -> bool:
    """Schickt die kompakte Morgenlage per Telegram (gekürzt auf Telegram-Länge). False: Telegram nicht eingerichtet."""
    return _telegram.melden(kuerzen(als_text(m, kompakt=True)), einst, warten=True)


def ausfuehren(einst: dict, ordner: Path, werte: list[str] | None = None, treiber=None, takt: str = "1d", anzahl: int = 250,
               mit_telegram: bool = True, mit_datei: bool = True, **kw) -> tuple[dict, Path | None, bool]:
    """Alles in einem: erstellen, speichern, melden. Für den Zeitplan (z. B. `start`). Gibt (Morgenlage, Pfad der JSON-Datei,
    ob Telegram sie angenommen hat) zurück. Fehler bei einem Wert stehen im Ergebnis; ein unbrauchbarer Treiber wirft KiFehler."""
    m = erstellen(einst, ordner, werte or watchlist(einst), treiber=treiber, takt=takt, anzahl=anzahl, **kw)
    pfad = speichern(m, ordner) if mit_datei else None
    return m, pfad, (melden(m, einst) if mit_telegram else False)


def argumente(p) -> None:
    p.add_argument("--watchlist", metavar="A,B,…", help="Werte, Komma getrennt (Standard: [watchlist] werte, z. B. BTC/EUR,ETH/EUR)")
    p.add_argument("--treiber", choices=_einst.KI_TREIBER,
                   help="KI-Treiber (Standard aus den Einstellungen); aus = kein KI-Aufruf, keine Kosten")
    p.add_argument("--takt", default="1d", help="Kerzenlänge der Kursquelle (Standard 1d)")
    p.add_argument("--anzahl", type=int, default=250, help="Anzahl Kerzen je Wert (Standard 250)")
    p.add_argument("--ohne-telegram", action="store_true", help="nichts an Telegram senden")
    p.add_argument("--ohne-datei", action="store_true", help="morgenlage.json nicht schreiben")


def laufen(args, einst: dict, ordner: Path) -> int:
    try:
        m, pfad, gemeldet = ausfuehren(einst, ordner, watchlist(einst, args.watchlist), args.treiber, args.takt, args.anzahl,
                                       mit_telegram=not args.ohne_telegram, mit_datei=not args.ohne_datei)
    except (_ki.KiFehler, ValueError) as e:
        raise Abbruch(str(e)) from None
    print(als_text(m))
    print()
    if pfad:
        print(f"JSON für die Oberfläche: {pfad}")
    if not args.ohne_telegram:
        print("Telegram: " + ("gesendet." if gemeldet else "nicht gesendet (nicht eingerichtet oder aus)."))
    return 0
