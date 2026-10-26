"""Journal: SQLite `journal.db` mit jeder Entscheidung und jeder Einschätzung.

1. Entscheidungen: Signal, Risikoprüfung (mit Gründen), Order, Ausführung und Ergebnis, jeweils mit Zeit.
2. Einschätzungen: eine Aussage wie "AAPL steigt in 1d" (von der KI oder von dir). Nach Ablauf des
   Horizonts wird sie automatisch gegen den echten Kurs ausgewertet. Die Trefferquote je Horizont zeigt
   ehrlich, ob die Einschätzungen mehr taugen als Raten. Immer mit n (Anzahl) lesen: wenige Fälle sagen nichts.
3. Ereignisse: Start, Not-Aus, Tagesabschluss, Fehler (reines Protokoll).
4. Tagesstand: Startwert und Sperre je Tag (für Tagesverlustgrenze).
5. Eigene Positionen: Symbole, die der Autopilot eröffnet hat, samt Gewinnziel, das der Broker nicht als Order führt.
   Damit weiß der Autopilot auch nach einem Neustart, welche Positionen ihm gehören und welches Ziel er überwacht.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import einstellungen as _einst
from .broker.basis import symbol_schluessel
from .hilfen import jetzt_utc, zu_utc

RICHTUNGEN = ("steigt", "faellt", "seitwaerts")
HORIZONTE = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "1d": timedelta(days=1),
             "1w": timedelta(weeks=1), "1M": timedelta(days=30), "3M": timedelta(days=91),
             "1J": timedelta(days=365)}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entscheidungen (
    id INTEGER PRIMARY KEY AUTOINCREMENT, zeit TEXT, modus TEXT, symbol TEXT, strategie TEXT,
    signal TEXT, pruefung_ok INTEGER, gruende TEXT, ord TEXT, gesendet INTEGER DEFAULT 0,
    order_id TEXT, ausfuehrung TEXT, ergebnis REAL, notiz TEXT
);
CREATE TABLE IF NOT EXISTS einschaetzungen (
    id INTEGER PRIMARY KEY AUTOINCREMENT, zeit TEXT, wert TEXT, richtung TEXT, horizont TEXT, begruendung TEXT,
    preis_bei_abgabe REAL, faellig TEXT, status TEXT DEFAULT 'offen', preis_bei_ablauf REAL,
    aenderung_prozent REAL, treffer INTEGER, ausgewertet TEXT
);
CREATE TABLE IF NOT EXISTS ereignisse (id INTEGER PRIMARY KEY AUTOINCREMENT, zeit TEXT, art TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS tagesstand (datum TEXT PRIMARY KEY, startwert REAL, gesperrt INTEGER DEFAULT 0, grund TEXT);
CREATE TABLE IF NOT EXISTS eigene (
    schluessel TEXT, modus TEXT, symbol TEXT, richtung TEXT, ziel REAL, order_id TEXT, seit TEXT,
    PRIMARY KEY (schluessel, modus)
);
"""


def _json(x) -> str | None:
    if x is None:
        return None
    if is_dataclass(x):
        x = asdict(x)
    return json.dumps(x, ensure_ascii=False, default=str)


def _iso(z: datetime) -> str:
    return zu_utc(z).isoformat()


class Journal:
    def __init__(self, pfad: str | Path | None = None, einstellungen: dict | None = None,
                 uhr: Callable[[], datetime] | None = None):
        self.einst = _einst.mit_standard(einstellungen) if einstellungen is not None else _einst.laden()
        self.uhr = uhr or jetzt_utc
        self.db = sqlite3.connect(str(pfad if pfad is not None else _einst.datenordner() / "journal.db"))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def beenden(self) -> None:
        """Datenbank schließen."""
        self.db.commit()
        self.db.close()

    # ------------------------------------------------------------ Entscheidungen

    def entscheidung(self, modus: str, symbol: str, strategie: str = "", signal=None, pruefung=None,
                     order=None, notiz: str = "") -> int:
        """Legt eine Entscheidung an. `pruefung` ist ein risiko.Ergebnis (ok, gruende) oder None."""
        c = self.db.execute(
            "INSERT INTO entscheidungen (zeit,modus,symbol,strategie,signal,pruefung_ok,gruende,ord,notiz) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (_iso(self.uhr()), modus, symbol, strategie, _json(signal),
             None if pruefung is None else int(pruefung.ok),
             None if pruefung is None else json.dumps(pruefung.gruende, ensure_ascii=False),
             _json(order), notiz))
        self.db.commit()
        return c.lastrowid

    def ausfuehrung(self, entscheidung_id: int, stand) -> None:
        """Trägt die Antwort des Brokers (OrderStand) ein."""
        self.db.execute("UPDATE entscheidungen SET gesendet=1, order_id=?, ausfuehrung=? WHERE id=?",
                        (stand.id, _json({"status": stand.status, "ausgefuehrt": stand.ausgefuehrt,
                                          "preis": stand.preis, "zeit": stand.zeit, "meldung": stand.meldung}),
                         entscheidung_id))
        self.db.commit()

    def ergebnis(self, entscheidung_id: int, betrag: float, notiz: str = "") -> None:
        """Addiert `betrag` (netto, nach Kosten) zum Ergebnis der Entscheidung (Teilschließungen summieren sich)."""
        self.db.execute("UPDATE entscheidungen SET ergebnis=COALESCE(ergebnis,0)+?, notiz=TRIM(COALESCE(notiz,'') || ?) WHERE id=?",
                        (betrag, (" " + notiz) if notiz else "", entscheidung_id))
        self.db.commit()

    def ergebnis_zu_order(self, order_id: str, betrag: float, notiz: str = "") -> bool:
        """Trägt das Ergebnis bei der Entscheidung ein, die zu dieser Order gehört (z. B. Schutz-Stop)."""
        z = self.db.execute("SELECT id FROM entscheidungen WHERE order_id=? ORDER BY id DESC LIMIT 1",
                            (order_id,)).fetchone()
        if z:
            self.ergebnis(z["id"], betrag, notiz)
        return z is not None

    def entscheidungen(self, anzahl: int = 20) -> list[dict]:
        return [dict(z) for z in self.db.execute("SELECT * FROM entscheidungen ORDER BY id DESC LIMIT ?", (anzahl,))][::-1]

    def orders_heute(self, datum: str, modus: str | None = None) -> int:
        """Anzahl gesendeter Orders an einem Tag (UTC-Datum JJJJ-MM-TT)."""
        sql = "SELECT COUNT(*) FROM entscheidungen WHERE gesendet=1 AND substr(zeit,1,10)=?"
        args: list = [datum]
        if modus:
            sql += " AND modus=?"
            args.append(modus)
        return self.db.execute(sql, args).fetchone()[0]

    # ------------------------------------------------------------ Ereignisse und Tagesstand

    def ereignis(self, art: str, text: str) -> None:
        self.db.execute("INSERT INTO ereignisse (zeit,art,text) VALUES (?,?,?)", (_iso(self.uhr()), art, text))
        self.db.commit()

    def ereignisse(self, anzahl: int = 20) -> list[dict]:
        return [dict(z) for z in self.db.execute("SELECT * FROM ereignisse ORDER BY id DESC LIMIT ?", (anzahl,))][::-1]

    def tagesstand(self, datum: str, startwert: float | None = None) -> dict:
        """Startwert und Sperre eines Tages; mit `startwert` wird der Tag angelegt, falls er noch fehlt."""
        z = self.db.execute("SELECT * FROM tagesstand WHERE datum=?", (datum,)).fetchone()
        if z is None and startwert is not None:
            self.db.execute("INSERT INTO tagesstand (datum,startwert) VALUES (?,?)", (datum, startwert))
            self.db.commit()
            z = self.db.execute("SELECT * FROM tagesstand WHERE datum=?", (datum,)).fetchone()
        return dict(z) if z else {}

    def tag_sperren(self, datum: str, grund: str) -> None:
        self.db.execute("UPDATE tagesstand SET gesperrt=1, grund=? WHERE datum=?", (grund, datum))
        self.db.commit()

    # ------------------------------------------------------------ Eigene Positionen und Ziele

    def eigene_merken(self, symbol: str, modus: str, richtung: str, ziel: float | None = None,
                      order_id: str = "") -> None:
        """Vermerkt: der Autopilot hat in diesem Symbol eröffnet (`richtung` "long"/"short"). `ziel`: Gewinnziel, das der
        Broker nicht als Order führt und das der Autopilot selbst überwacht (sonst None). Ersetzt einen früheren Eintrag."""
        self.db.execute("INSERT OR REPLACE INTO eigene (schluessel,modus,symbol,richtung,ziel,order_id,seit) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (symbol_schluessel(symbol), modus, symbol, richtung, ziel, order_id, _iso(self.uhr())))
        self.db.commit()

    def eigene(self, modus: str) -> list[dict]:
        """Vom Autopiloten eröffnete Symbole dieser Betriebsart (mit Richtung, Ziel, Zeit)."""
        return [dict(z) for z in self.db.execute("SELECT * FROM eigene WHERE modus=? ORDER BY seit", (modus,))]

    def ziel_loeschen(self, symbol: str, modus: str) -> None:
        self.db.execute("UPDATE eigene SET ziel=NULL WHERE schluessel=? AND modus=?", (symbol_schluessel(symbol), modus))
        self.db.commit()

    def eigene_entfernen(self, symbol: str, modus: str) -> None:
        self.db.execute("DELETE FROM eigene WHERE schluessel=? AND modus=?", (symbol_schluessel(symbol), modus))
        self.db.commit()

    # ------------------------------------------------------------ Einschätzungen

    def einschaetzung(self, wert: str, richtung: str, horizont: str, begruendung: str = "",
                      preis_bei_abgabe: float | None = None, zeit: datetime | None = None) -> int:
        """Hält eine Einschätzung fest, z. B. ("AAPL", "steigt", "1d", "...", 231.5)."""
        if richtung not in RICHTUNGEN:
            raise ValueError(f"richtung muss {RICHTUNGEN} sein, nicht {richtung!r}")
        if horizont not in HORIZONTE:
            raise ValueError(f"horizont muss {tuple(HORIZONTE)} sein, nicht {horizont!r}")
        zeit = zeit or self.uhr()
        c = self.db.execute(
            "INSERT INTO einschaetzungen (zeit,wert,richtung,horizont,begruendung,preis_bei_abgabe,faellig) "
            "VALUES (?,?,?,?,?,?,?)",
            (_iso(zeit), wert, richtung, horizont, begruendung, preis_bei_abgabe, _iso(zu_utc(zeit) + HORIZONTE[horizont])))
        self.db.commit()
        return c.lastrowid

    def bewerte_treffer(self, richtung: str, horizont: str, aenderung_prozent: float) -> bool:
        """Regel: steigt = Plus von mindestens der Schwelle; faellt = Minus von mindestens der Schwelle;
        seitwaerts = Bewegung bleibt unter der Schwelle (Betrag)."""
        schwelle = self.einst["journal"]["schwellen"][horizont]
        if richtung == "steigt":
            return aenderung_prozent >= schwelle
        if richtung == "faellt":
            return aenderung_prozent <= -schwelle
        return abs(aenderung_prozent) < schwelle

    def auswerten(self, kurs_zu_zeit: Callable[[str, datetime], float | None],
                  jetzt: datetime | None = None) -> list[dict]:
        """Wertet alle abgelaufenen, offenen Einschätzungen aus. `kurs_zu_zeit(wert, faellig)` liefert den
        Kurs zum Ablaufzeitpunkt oder None (dann bleibt die Einschätzung offen und wird später erneut versucht).
        Gibt die neu ausgewerteten zurück."""
        jetzt = zu_utc(jetzt or self.uhr())
        fertig = []
        for z in self.db.execute("SELECT * FROM einschaetzungen WHERE status='offen' ORDER BY id").fetchall():
            faellig = datetime.fromisoformat(z["faellig"])
            if faellig > jetzt or not z["preis_bei_abgabe"]:
                if faellig <= jetzt and not z["preis_bei_abgabe"]:     # ohne Startpreis nicht auswertbar
                    self.db.execute("UPDATE einschaetzungen SET status='unbewertbar', ausgewertet=? WHERE id=?",
                                    (_iso(jetzt), z["id"]))
                continue
            preis = kurs_zu_zeit(z["wert"], faellig)
            if preis is None:
                continue
            aenderung = (preis / z["preis_bei_abgabe"] - 1) * 100
            treffer = self.bewerte_treffer(z["richtung"], z["horizont"], aenderung)
            self.db.execute("UPDATE einschaetzungen SET status='ausgewertet', preis_bei_ablauf=?, "
                            "aenderung_prozent=?, treffer=?, ausgewertet=? WHERE id=?",
                            (preis, aenderung, int(treffer), _iso(jetzt), z["id"]))
            fertig.append({"id": z["id"], "wert": z["wert"], "richtung": z["richtung"], "horizont": z["horizont"],
                           "aenderung_prozent": aenderung, "treffer": treffer})
        self.db.commit()
        return fertig

    def einschaetzungen(self, nur_offene: bool = False) -> list[dict]:
        sql = "SELECT * FROM einschaetzungen" + (" WHERE status='offen'" if nur_offene else "") + " ORDER BY id"
        return [dict(z) for z in self.db.execute(sql)]

    def trefferquote(self) -> dict[str, dict]:
        """Je Horizont: n (ausgewertete Einschätzungen), treffer, quote in % (None bei n = 0), offen.
        Gibt alle Horizonte zurück, auch solche ohne Fälle, damit n immer sichtbar ist."""
        erg = {h: {"n": 0, "treffer": 0, "quote": None, "offen": 0} for h in HORIZONTE}
        for z in self.db.execute("SELECT horizont, status, treffer FROM einschaetzungen"):
            e = erg[z["horizont"]]
            if z["status"] == "ausgewertet":
                e["n"] += 1
                e["treffer"] += z["treffer"]
            elif z["status"] == "offen":
                e["offen"] += 1
        for e in erg.values():
            e["quote"] = e["treffer"] / e["n"] * 100 if e["n"] else None
        return erg
