"""Papier-Broker: Simulation mit echten (oder erfundenen) Kursen, aber ohne echtes Geld.

- Kurse kommen von einem `kursgeber` (Funktion Symbol -> Kurs), z. B. ein echter Broker im Nur-Lese-Betrieb.
- Marktorders laufen zum Ask (Kauf) bzw. Bid (Verkauf) plus Schlupf. Limit-, Stop- und Stop-Limit-Orders
  werden bei jedem `aktualisieren()` gegen den aktuellen Kurs geprüft.
- Angehängte Stop-Loss/Take-Profit werden nach der Ausführung als OCO-Paar angelegt (eine löscht die andere).
  Schutzorders können nur verkleinern, nie eine Gegenposition eröffnen.
- Gebühren: fest + Prozent vom Umsatz. Ergebnis je Trade nach FIFO.
- Der gesamte Zustand liegt in SQLite (`papier.db`), das Konto überlebt also einen Neustart.

Teilausführungen gibt es nicht: eine Order wird ganz oder gar nicht ausgeführt.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .. import einstellungen as _einst
from ..hilfen import jetzt_utc, zu_utc
from .basis import Broker, BrokerFehler, Konto, Kurs, Kerze, Order, OrderStand, Position

_EPS = 1e-9

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (schluessel TEXT PRIMARY KEY, wert TEXT);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY, symbol TEXT, seite TEXT, menge REAL, art TEXT, limit_preis REAL, stop_preis REAL,
    stop_loss REAL, take_profit REAL, gueltig TEXT, kennung TEXT, notiz TEXT,
    status TEXT, ausgefuehrt REAL DEFAULT 0, preis REAL, zeit TEXT, meldung TEXT DEFAULT '',
    erstellt TEXT, eltern TEXT, rolle TEXT, oco TEXT, ausgeloest INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, menge REAL, preis REAL, zeit TEXT, gebuehr_offen REAL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, richtung TEXT, menge REAL,
    einstieg_preis REAL, ausstieg_preis REAL, einstieg_zeit TEXT, ausstieg_zeit TEXT,
    brutto REAL, gebuehren REAL, netto REAL, order_id TEXT
);
CREATE TABLE IF NOT EXISTS kurse (symbol TEXT PRIMARY KEY, kurs REAL, zeit TEXT);
"""


def _iso(z: datetime) -> str:
    return zu_utc(z).isoformat()


def _zeit(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


class PapierBroker(Broker):
    name = "papier"
    papier = True

    def __init__(self, kursgeber: Callable[[str], Kurs], einstellungen: dict | None = None,
                 db_pfad: str | Path | None = None,
                 kerzengeber: Callable[[str, str, int], list[Kerze]] | None = None,
                 uhr: Callable[[], datetime] | None = None,
                 wechselkursgeber: Callable[[str, str], float | None] | None = None):
        self.einst = _einst.mit_standard(einstellungen) if einstellungen is not None else _einst.laden()
        self.kosten = self.einst["kosten"]
        self.cfg = self.einst["papier"]
        self.kursgeber = kursgeber
        self.kerzengeber = kerzengeber
        self.wechselkursgeber = wechselkursgeber      # Devisenkurse von der Kursquelle (z. B. IBKR), falls sie welche hat
        self.uhr = uhr or jetzt_utc
        if db_pfad is None:
            db_pfad = _einst.datenordner() / "papier.db"
        self.db = sqlite3.connect(str(db_pfad))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        if self._meta("guthaben") is None:
            self._setze_meta("guthaben", float(self.cfg["startguthaben"]))
            self._setze_meta("naechste_id", 1)
            self.db.commit()

    # ------------------------------------------------------------ Zustand

    def _meta(self, schluessel: str):
        z = self.db.execute("SELECT wert FROM meta WHERE schluessel=?", (schluessel,)).fetchone()
        return None if z is None else z["wert"]

    def _setze_meta(self, schluessel: str, wert) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (schluessel, str(wert)))

    @property
    def _guthaben(self) -> float:
        return float(self._meta("guthaben"))

    def _neue_id(self) -> str:
        n = int(self._meta("naechste_id"))
        self._setze_meta("naechste_id", n + 1)
        return f"P{n:06d}"

    def beenden(self) -> None:
        """Datenbank schließen."""
        self.db.commit()
        self.db.close()

    def zuruecksetzen(self) -> None:
        """Löscht Orders, Positionen und Trades und beginnt neu mit dem Startguthaben."""
        for tabelle in ("meta", "orders", "lots", "trades", "kurse"):
            self.db.execute(f"DELETE FROM {tabelle}")
        self._setze_meta("guthaben", float(self.cfg["startguthaben"]))
        self._setze_meta("naechste_id", 1)
        self.db.commit()

    def _lots(self, symbol: str | None = None) -> list[sqlite3.Row]:
        if symbol is None:
            return self.db.execute("SELECT * FROM lots ORDER BY id").fetchall()
        return self.db.execute("SELECT * FROM lots WHERE symbol=? ORDER BY id", (symbol,)).fetchall()

    def _menge(self, symbol: str) -> float:
        return sum(l["menge"] for l in self._lots(symbol))

    def _letzter_kurs(self, symbol: str) -> float | None:
        z = self.db.execute("SELECT kurs FROM kurse WHERE symbol=?", (symbol,)).fetchone()
        return None if z is None else z["kurs"]

    def _bewertungskurs(self, symbol: str, frisch: bool = True) -> float | None:
        """Mittelkurs für die Bewertung; fällt auf den zuletzt gespeicherten Kurs zurück."""
        if frisch:
            try:
                k = self._kurs_holen(symbol)
                if k.mitte is not None:
                    return k.mitte
            except BrokerFehler:
                pass
        return self._letzter_kurs(symbol)

    def _kurs_holen(self, symbol: str) -> Kurs:
        try:
            kurs = self.kursgeber(symbol)
        except BrokerFehler:
            raise
        except Exception as e:  # Kursquelle kaputt: verständlich melden
            raise BrokerFehler(f"Kein Kurs für {symbol}: {e}") from e
        if kurs.mitte is not None:
            self.db.execute("INSERT OR REPLACE INTO kurse VALUES (?,?,?)", (symbol, kurs.mitte, _iso(kurs.zeit)))
        return kurs

    def _symbole_mit_position(self) -> list[str]:
        return [z["symbol"] for z in self.db.execute("SELECT DISTINCT symbol FROM lots ORDER BY symbol")]

    def _gesamtwert(self, frisch: bool = False) -> tuple[float, float]:
        """(Gesamtwert, Summe der Positionsbeträge).

        Guthaben und Positionen werden in **einer** Abfrage gelesen: Oberfläche, Telegram und Autopilot öffnen dieselbe
        Datenbank in eigenen Verbindungen, und während die Kurse geholt werden (Netz), kann ein anderer kaufen, verkaufen
        oder den Not-Aus ausführen. Zwei getrennte Abfragen zeigten dann Erlös und Position zugleich. Mit `frisch` wird die
        Schreibsperre, die das Speichern des Kurses öffnet, vor dem nächsten Netzabruf wieder freigegeben."""
        zeilen = self.db.execute(
            "SELECT m.wert AS guthaben, l.symbol, l.menge, l.preis FROM meta m LEFT JOIN lots l "
            "WHERE m.schluessel='guthaben' ORDER BY l.symbol, l.id").fetchall()
        guthaben = float(zeilen[0]["guthaben"])
        mengen: dict[str, float] = {}
        letzter_preis: dict[str, float] = {}
        for z in zeilen:
            if z["symbol"] is not None:
                mengen[z["symbol"]] = mengen.get(z["symbol"], 0.0) + z["menge"]
                letzter_preis[z["symbol"]] = z["preis"]
        wert = brutto = 0.0
        for s, m in mengen.items():
            if abs(m) < _EPS:
                continue
            k = self._bewertungskurs(s, frisch)
            if frisch:
                self.db.commit()
            if k is None:
                k = letzter_preis[s]
            wert += m * k
            brutto += abs(m * k)
        return guthaben + wert, brutto

    # ------------------------------------------------------------ Tageswechsel

    def _tag_pruefen(self) -> None:
        heute = zu_utc(self.uhr()).date().isoformat()
        if self._meta("tag") != heute:
            self._setze_meta("tag", heute)
            self._setze_meta("tagesstart_wert", self._gesamtwert()[0])
            # Tagesorders verfallen über Nacht (Schutzorders sind immer gtc)
            self.db.execute("UPDATE orders SET status='storniert', meldung='Tagesorder verfallen' "
                            "WHERE status='offen' AND gueltig='tag' AND rolle IS NULL")
            self.db.commit()

    # ------------------------------------------------------------ Broker-Schnittstelle

    def konto(self) -> Konto:
        self._tag_pruefen()
        gesamt, brutto = self._gesamtwert(frisch=True)
        self.db.commit()
        start = float(self._meta("tagesstart_wert") or gesamt)
        gebuehren = sum(t["gebuehren"] for t in self.db.execute("SELECT gebuehren FROM trades"))
        return Konto(self.einst["waehrung"], self._guthaben, gesamt,
                     kaufkraft=max(0.0, gesamt * self.cfg["hebel"] - brutto),
                     tagesergebnis=gesamt - start, papier=True,
                     zusatz={"startguthaben": self.cfg["startguthaben"], "gebuehren_realisiert": gebuehren})

    def positionen(self) -> list[Position]:
        ergebnis = []
        for s in self._symbole_mit_position():
            lots = self._lots(s)
            menge = sum(l["menge"] for l in lots)
            if abs(menge) < _EPS:
                continue
            einstand = sum(abs(l["menge"]) * l["preis"] for l in lots) / sum(abs(l["menge"]) for l in lots)
            ergebnis.append(Position(s, menge, einstand, self._bewertungskurs(s), self.einst["waehrung"]))
            self.db.commit()          # keine Schreibsperre über die nächste Kursabfrage halten (siehe `_gesamtwert`)
        self.db.commit()
        return ergebnis

    def orders(self, nur_offene: bool = True) -> list[OrderStand]:
        sql = "SELECT * FROM orders" + (" WHERE status IN ('offen','teilweise')" if nur_offene else "") + " ORDER BY rowid"
        return [self._stand(z) for z in self.db.execute(sql)]

    def kurs(self, symbol: str) -> Kurs:
        k = self._kurs_holen(symbol)
        self.db.commit()
        return k

    def wechselkurs(self, von: str, nach: str) -> float | None:
        if von.upper() != nach.upper() and self.wechselkursgeber is not None:
            try:
                return self.wechselkursgeber(von, nach)
            except BrokerFehler:
                return None
        return super().wechselkurs(von, nach)

    def kerzen(self, symbol: str, takt: str = "1m", anzahl: int = 300) -> list[Kerze]:
        if self.kerzengeber is None:
            raise BrokerFehler("Der Papier-Broker hat keine eigene Kerzenquelle (kerzengeber fehlt)")
        return self.kerzengeber(symbol, takt, anzahl)

    def trades(self) -> list[dict]:
        """Abgeschlossene Trades mit FIFO-Ergebnis (brutto, Gebühren, netto)."""
        return [dict(z) for z in self.db.execute("SELECT * FROM trades ORDER BY id")]

    # ------------------------------------------------------------ Orders

    def _stand(self, z: sqlite3.Row) -> OrderStand:
        o = Order(z["symbol"], z["seite"], z["menge"], z["art"], z["limit_preis"], z["stop_preis"],
                  z["stop_loss"], z["take_profit"], z["gueltig"], z["kennung"], z["notiz"] or "")
        return OrderStand(z["id"], o, z["status"], z["ausgefuehrt"] or 0.0, z["preis"], _zeit(z["zeit"]), z["meldung"] or "")

    def _stand_id(self, oid: str) -> OrderStand:
        return self._stand(self.db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())

    def senden(self, order: Order) -> OrderStand:
        self._tag_pruefen()
        kurs = self._kurs_holen(order.symbol)      # ohne Kurs wird nichts angenommen
        oid = self._neue_id()
        jetzt = _iso(self.uhr())
        self.db.execute(
            "INSERT INTO orders (id,symbol,seite,menge,art,limit_preis,stop_preis,stop_loss,take_profit,gueltig,"
            "kennung,notiz,status,erstellt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'offen', ?)",
            (oid, order.symbol, order.seite, order.menge, order.art, order.limit, order.stop, order.stop_loss,
             order.take_profit, order.gueltig, order.kennung, order.notiz, jetzt))
        self._versuche(self.db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone(), kurs)
        self.db.commit()
        return self._stand_id(oid)

    def stornieren(self, order_id: str) -> None:
        z = self.db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if z is None:
            raise BrokerFehler(f"Order {order_id} gibt es nicht")
        if z["status"] != "offen":
            raise BrokerFehler(f"Order {order_id} ist nicht mehr offen (Status: {z['status']})")
        self.db.execute("UPDATE orders SET status='storniert', meldung='vom Nutzer storniert' WHERE id=?", (order_id,))
        self.db.commit()

    def aktualisieren(self) -> list[OrderStand]:
        """Prüft alle offenen Orders gegen die aktuellen Kurse. Gibt die dabei ausgeführten Orders zurück.
        Wird vom Autopiloten regelmäßig aufgerufen (und in Tests nach jedem neuen Kurs)."""
        self._tag_pruefen()
        gefuellt: list[OrderStand] = []
        ids = [z["id"] for z in self.db.execute(
            "SELECT id FROM orders WHERE status='offen' ORDER BY (rolle='stop_loss') DESC, rowid")]
        kurse: dict[str, Kurs] = {}
        for oid in ids:
            z = self.db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
            if z["status"] != "offen":       # inzwischen per OCO gelöscht
                continue
            try:
                if z["symbol"] not in kurse:
                    kurse[z["symbol"]] = self._kurs_holen(z["symbol"])
            except BrokerFehler:
                continue                      # ohne Kurs bleibt die Order offen
            if self._versuche(z, kurse[z["symbol"]]):
                gefuellt.append(self._stand_id(oid))
        self.db.commit()
        return gefuellt

    # ------------------------------------------------------------ Ausführungslogik

    def _seitenpreis(self, seite: str, kurs: Kurs) -> float:
        p = kurs.ask if seite == "kauf" else kurs.bid
        if p is None:
            p = kurs.letzter if kurs.letzter is not None else kurs.mitte
        if p is None:
            raise BrokerFehler(f"Kurs für {kurs.symbol} ohne Preis")
        return p

    def _schlupf(self, seite: str, preis: float) -> float:
        s = self.kosten["schlupf_prozent"] / 100
        return preis * (1 + s) if seite == "kauf" else preis * (1 - s)

    def _versuche(self, z: sqlite3.Row, kurs: Kurs) -> bool:
        """Prüft, ob die offene Order jetzt ausgeführt wird, und führt sie ggf. aus. True = ausgeführt."""
        seite, art = z["seite"], z["art"]
        p = self._seitenpreis(seite, kurs)
        menge = z["menge"]

        # Schutzorders nur verkleinernd: Position prüfen
        if z["rolle"]:
            pos = self._menge(z["symbol"])
            passend = pos > _EPS if seite == "verkauf" else pos < -_EPS
            if not passend:
                self._schliesse_gruppe(z, "Position bereits geschlossen")
                return False
            menge = min(menge, abs(pos))

        preis = None
        if art == "markt":
            preis = self._schlupf(seite, p)
        elif art == "limit":
            if (seite == "kauf" and p <= z["limit_preis"] + _EPS) or (seite == "verkauf" and p >= z["limit_preis"] - _EPS):
                preis = p
        else:   # stop oder stop_limit
            ausgeloest = bool(z["ausgeloest"])
            stop = z["stop_preis"]
            if not ausgeloest and ((seite == "kauf" and p >= stop - _EPS) or (seite == "verkauf" and p <= stop + _EPS)):
                ausgeloest = True
                self.db.execute("UPDATE orders SET ausgeloest=1 WHERE id=?", (z["id"],))
            if ausgeloest:
                if art == "stop":
                    preis = self._schlupf(seite, p)
                elif (seite == "kauf" and p <= z["limit_preis"] + _EPS) or (seite == "verkauf" and p >= z["limit_preis"] - _EPS):
                    preis = p
        if preis is None:
            return False

        if not z["rolle"]:
            grund = self._unzulaessig(z["symbol"], seite, menge, preis)
            if grund:
                self.db.execute("UPDATE orders SET status='abgelehnt', meldung=? WHERE id=?", (grund, z["id"]))
                return False
        self._fuellen(z, menge, preis, kurs.zeit)
        return True

    def _unzulaessig(self, symbol: str, seite: str, menge: float, preis: float) -> str | None:
        """Gründe, warum der Papier-Broker selbst eine Order ablehnt (Short, Kaufkraft)."""
        alt = self._menge(symbol)
        neu = alt + (menge if seite == "kauf" else -menge)
        if neu < -_EPS and not self.cfg["short_erlaubt"]:
            return "abgelehnt: Short ist im Papier-Broker nicht erlaubt"
        gesamt, brutto = self._gesamtwert()
        k_alt = self._letzter_kurs(symbol) or preis
        brutto_neu = brutto - abs(alt) * k_alt + abs(neu) * preis
        if brutto_neu > brutto + _EPS and brutto_neu > gesamt * self.cfg["hebel"] + _EPS:
            return (f"abgelehnt: Kaufkraft reicht nicht (Positionen {brutto_neu:.2f} "
                    f"> Gesamtwert {gesamt:.2f} x Hebel {self.cfg['hebel']:g})")
        return None

    def _gebuehr(self, menge: float, preis: float) -> float:
        return self.kosten["gebuehr_fest"] + self.kosten["gebuehr_prozent"] / 100 * menge * preis

    def _fuellen(self, z: sqlite3.Row, menge: float, preis: float, kurszeit: datetime) -> None:
        zeit = _iso(self.uhr())
        symbol, seite = z["symbol"], z["seite"]
        signiert = menge if seite == "kauf" else -menge
        gebuehr = self._gebuehr(menge, preis)
        self.db.execute("UPDATE meta SET wert=? WHERE schluessel='guthaben'",
                        (str(self._guthaben - signiert * preis - gebuehr),))

        # FIFO: gegenläufige Lots zuerst auflösen
        rest = abs(signiert)
        for lot in self._lots(symbol):
            if rest <= _EPS:
                break
            if lot["menge"] * signiert >= 0:
                continue
            nimm = min(rest, abs(lot["menge"]))
            vorzeichen = 1 if lot["menge"] > 0 else -1
            einstiegsgebuehr = lot["gebuehr_offen"] * nimm / abs(lot["menge"])
            ausstiegsgebuehr = gebuehr * nimm / menge
            brutto = (preis - lot["preis"]) * vorzeichen * nimm
            gebuehren = einstiegsgebuehr + ausstiegsgebuehr
            self.db.execute(
                "INSERT INTO trades (symbol,richtung,menge,einstieg_preis,ausstieg_preis,einstieg_zeit,ausstieg_zeit,"
                "brutto,gebuehren,netto,order_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (symbol, "long" if vorzeichen > 0 else "short", nimm, lot["preis"], preis, lot["zeit"], zeit,
                 brutto, gebuehren, brutto - gebuehren, z["id"]))
            neu_menge = lot["menge"] - vorzeichen * nimm
            if abs(neu_menge) <= _EPS:
                self.db.execute("DELETE FROM lots WHERE id=?", (lot["id"],))
            else:
                self.db.execute("UPDATE lots SET menge=?, gebuehr_offen=? WHERE id=?",
                                (neu_menge, lot["gebuehr_offen"] - einstiegsgebuehr, lot["id"]))
            rest -= nimm
        if rest > _EPS:   # Rest eröffnet bzw. vergrößert eine Position
            self.db.execute("INSERT INTO lots (symbol,menge,preis,zeit,gebuehr_offen) VALUES (?,?,?,?,?)",
                            (symbol, rest if signiert > 0 else -rest, preis, zeit, gebuehr * rest / menge))

        self.db.execute("INSERT OR REPLACE INTO kurse VALUES (?,?,?)", (symbol, preis, _iso(kurszeit)))
        self.db.execute("UPDATE orders SET status='ausgefuehrt', ausgefuehrt=?, preis=?, zeit=?, menge=?, "
                        "meldung=? WHERE id=?",
                        (menge, preis, zeit, menge, f"Gebühr {gebuehr:.2f}", z["id"]))

        if z["rolle"]:      # Schutzorder ausgeführt: Gegenorder (OCO) löschen
            self._schliesse_gruppe(z, "OCO: Gegenorder ausgeführt")
        elif z["stop_loss"] is not None or z["take_profit"] is not None:
            self._schutz_anlegen(z, menge)

    def _schutz_anlegen(self, eltern: sqlite3.Row, menge: float) -> None:
        gegen = "verkauf" if eltern["seite"] == "kauf" else "kauf"
        for rolle, art, feld in (("stop_loss", "stop", "stop_loss"), ("take_profit", "limit", "take_profit")):
            wert = eltern[feld]
            if wert is None:
                continue
            self.db.execute(
                "INSERT INTO orders (id,symbol,seite,menge,art,limit_preis,stop_preis,gueltig,kennung,notiz,status,"
                "erstellt,eltern,rolle,oco) VALUES (?,?,?,?,?,?,?,'gtc',?,?, 'offen', ?,?,?,?)",
                (self._neue_id(), eltern["symbol"], gegen, menge, art, wert if art == "limit" else None,
                 wert if art == "stop" else None, eltern["kennung"], f"{rolle} zu {eltern['id']}",
                 _iso(self.uhr()), eltern["id"], rolle, eltern["id"]))

    def _schliesse_gruppe(self, z: sqlite3.Row, grund: str) -> None:
        """Storniert alle noch offenen Orders der OCO-Gruppe (die gerade ausgeführte ist schon abgehakt)."""
        self.db.execute("UPDATE orders SET status='storniert', meldung=? WHERE oco=? AND status='offen'",
                        (grund, z["oco"]))
