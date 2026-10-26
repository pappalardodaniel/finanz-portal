"""Speicher der langen Reihen: eigene SQLite-Datei historie.db im Ordner KITRADER_DATEN.

Tabellen: historie (Werte), historie_stand (Herkunft und Umfang je Reihe), datenhinweise (Stolpersteine für Analysen).
Schutzregel: Eine Reihe wird nur ersetzt, wenn der neue Abruf nicht dünner ist als der alte (siehe schreiben()).
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
from datetime import datetime, timezone

from . import zeit

MIN_ANTEIL = 0.9          # neuer Abruf muss mindestens so viel Prozent der alten Anzahl haben
MAX_RUECKSTAND_TAGE = 45  # und darf höchstens so viele Tage früher enden als der alte Bestand

SCHEMA = """
CREATE TABLE IF NOT EXISTS historie (reihe TEXT, datum TEXT, wert REAL, PRIMARY KEY (reihe, datum)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS historie_stand (reihe TEXT PRIMARY KEY, quelle TEXT, abgerufen TEXT, anzahl INTEGER,
    erstes TEXT, letztes TEXT, hinweis TEXT);
CREATE TABLE IF NOT EXISTS datenhinweise (thema TEXT PRIMARY KEY, hinweis TEXT);
"""

DATENHINWEISE = {
    "historie": "Tabelle historie (reihe, datum, wert): lange Reihen aus verschiedenen Quellen, Herkunft je Reihe in historie_stand. "
                "Reihen aus verschiedenen Quellen sind nicht ohne Weiteres verkettbar (andere Zusammensetzung, andere Zeitpunkte, andere Einheit). "
                "Lücken können echt sein (Börse geschlossen) oder fehlende Daten; sie werden nicht gefüllt.",
    "historie.shiller": "Shiller-Reihen sind Monatsmittel der Tagesschlusskurse (Datum = 1. des Monats); der jüngste Monat ist vorläufig. "
                        "Ein Monatsmittel enthält auch Tage nach einem Ereignis im selben Monat. Dividenden und Gewinne der letzten Monate fehlen meist.",
    "historie.dow": "Dow Jones täglich (MeasuringWorth): vor 1896 der einzelne Dow Jones Average, bis 1914 die erste DJIA, angeglichen. "
                    "Die NYSE war vom 31.07. bis 11.12.1914 geschlossen: Lücke ohne Handel, kein Datenfehler.",
    "historie.uk": "Bank of England (A millennium of macroeconomic data): Aktienindex monatlich bis 2017-04, Leitzins bis 2017; danach keine Fortschreibung.",
    "historie.btc": "Bitcoin (Coin Metrics, PriceUSD, CC BY-NC 4.0): Tageswert ab 2010-07-18. Ersatz btc_blockchain (Blockchain.com, ab 2010-08-18); dort bedeutet Wert 0 kein Preis und wird übersprungen.",
    "historie.edelmetalle_monat": "Weltbank Pink Sheet (gold_monat, silber_monat, platin_monat): Monatsmittel in USD je Feinunze, Datum = 1. des Monats; Gold und Platin auf ganze Dollar, Silber auf 0,1 Dollar gerundet.",
    "historie.ereignisse": "Ereignisliste (ereignisse.json) mit Quelle je Ereignis. Der Beginn ist bei genauigkeit monat/quartal/jahr ein Platzhalter-Tag "
                           "(1. des Monats bzw. Jahres). Ereignisse überschneiden sich, ihre Renditen sind nicht unabhängig.",
}


def datenordner() -> pathlib.Path:
    """KITRADER_DATEN (Standard ~/.local/share/kitrader); HISTORIE_DATEN gilt als älterer Name weiter."""
    roh = os.environ.get("KITRADER_DATEN") or os.environ.get("HISTORIE_DATEN") or "~/.local/share/kitrader"
    return pathlib.Path(roh).expanduser()


class Speicher:
    def __init__(self, ort: pathlib.Path | str | None = None):
        ort = pathlib.Path(ort) if ort else datenordner()
        ort.mkdir(parents=True, exist_ok=True)
        self.datei = ort / "historie.db"
        self.db = sqlite3.connect(self.datei, timeout=30, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout = 30000")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = NORMAL")
        self.db.executescript(SCHEMA)
        self.db.executemany("INSERT OR REPLACE INTO datenhinweise VALUES (?, ?)", DATENHINWEISE.items())

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------------------ Schreiben

    def schreiben(self, reihe: str, zeilen, quelle: str, hinweis: str = "", erzwingen: bool = False) -> dict:
        """Ersetzt die Reihe durch zeilen [(datum, wert), …], sofern die Schutzregel es erlaubt.

        Abgelehnt wird (der alte Bestand bleibt), wenn der neue Abruf
          - weniger als 90 % der alten Anzahl hat,
          - später beginnt als der alte Bestand,
          - oder mehr als 45 Tage früher endet als der alte Bestand (z. B. veraltete Ersatzquelle).
        erzwingen=True hebt die Regel auf. Rückgabe: {ersetzt, grund, anzahl, erstes, letztes, anzahl_alt}.
        """
        daten = {d: float(w) for d, w in zeilen}
        neu = sorted(daten.items())
        alt = self.db.execute("SELECT COUNT(*), MIN(datum), MAX(datum) FROM historie WHERE reihe = ?", (reihe,)).fetchone()
        anzahl_alt = alt[0]
        antwort = {"ersetzt": False, "grund": "", "anzahl": len(neu), "anzahl_alt": anzahl_alt,
                   "erstes": neu[0][0] if neu else "", "letztes": neu[-1][0] if neu else ""}
        if not neu:
            antwort["grund"] = "keine Werte geliefert, alter Bestand bleibt"
            return antwort
        if anzahl_alt and not erzwingen:
            if len(neu) < MIN_ANTEIL * anzahl_alt:
                antwort["grund"] = (f"nur {len(neu)} Werte statt bisher {anzahl_alt} (weniger als {MIN_ANTEIL:.0%}), "
                                    "alter Bestand bleibt")
                return antwort
            if neu[0][0] > alt[1]:
                antwort["grund"] = f"beginnt erst {neu[0][0]} statt {alt[1]}, alter Bestand bleibt"
                return antwort
            if zeit.tage_zwischen(neu[-1][0], alt[2]) > MAX_RUECKSTAND_TAGE:
                antwort["grund"] = f"endet schon {neu[-1][0]} statt {alt[2]}, alter Bestand bleibt"
                return antwort
        jetzt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("DELETE FROM historie WHERE reihe = ?", (reihe,))
            self.db.executemany("INSERT INTO historie VALUES (?, ?, ?)", [(reihe, d, w) for d, w in neu])
            self.db.execute("INSERT OR REPLACE INTO historie_stand VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (reihe, quelle, jetzt, len(neu), neu[0][0], neu[-1][0], hinweis))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        antwort["ersetzt"] = True
        return antwort

    # ------------------------------------------------------------------------------ Abfragen

    def werte(self, reihe: str, von: str | None = None, bis: str | None = None) -> list[tuple[str, float]]:
        """Werte (datum, wert) aufsteigend; von/bis einschließlich, auch als 'JJJJ' oder 'JJJJ-MM' erlaubt."""
        sql, par = "SELECT datum, wert FROM historie WHERE reihe = ?", [reihe]
        if von:
            sql, par = sql + " AND datum >= ?", par + [von]
        if bis:
            sql, par = sql + " AND substr(datum, 1, ?) <= ?", par + [len(bis), bis]
        return [(d, w) for d, w in self.db.execute(sql + " ORDER BY datum", par)]

    def stand(self) -> list[dict]:
        spalten = ["reihe", "quelle", "abgerufen", "anzahl", "erstes", "letztes", "hinweis"]
        return [dict(zip(spalten, z)) for z in self.db.execute("SELECT * FROM historie_stand ORDER BY reihe")]
