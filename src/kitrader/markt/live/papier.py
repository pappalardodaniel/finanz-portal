"""Papierhandel: simulierte Orders gegen den laufenden Kursstand. Kein echtes Geld, keine Orders an einen Broker.

Orders entstehen nur in kitrader Charts (papier.js). Ausgeführt wird hier im Kern, weil nur er Kurse und Orderbuch
hat und nur er Börsendaten abruft:
  - Markt-Orders laufen durch das Orderbuch (Xetra und Binance je 5 Stufen); ohne Buch zum besten Kurs des
    Hauptplatzes, ohne Kurs zum letzten Abschluss.
  - Limit-Orders werden ausgeführt, sobald der Durchschnittspreis durch das Buch das Limit einhält.
  - Stopp-Orders werden zur Markt-Order, sobald der letzte Abschluss den Stopp erreicht.
Standard: nur long (verkaufen bis zum Bestand), nur ganze Orders (keine Teilausführung), feste Gebühr je Order.
Erweitert (Reiter kitrader Charts, Karte Papierhandel): Short (`short=True`: Verkauf ohne Bestand, Kauf deckt ein), Pflicht-Stop und
optionales Ziel (`stop`, `ziel`: werden nach der Ausführung überwacht und schließen die Position zum Markt), Prüfung jeder Eröffnung durch
`kitrader.risiko` (dieselben Regeln wie beim Autopiloten; Ablehnungsgründe stehen in der Antwort) und `schliessen` für ganze Positionen.
Deutsche Kurse sind 15 Minuten verzögert: ausgeführt wird zum Datenstand, nicht zum Echtzeitkurs.

Nach jeder Ausführung geht eine Meldung an Telegram (über den Analysen-Dienst), nach jedem abgeschlossenen
Trade bewertet Claude ihn (Agent auf Port 8814) für das Journal.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..modell import iso_aus_ns
from ..quellen.referenz import ezb_kurs
from .zustand import HAUPTPLATZ, Zustand

UTC = timezone.utc
WAEHRUNG = {"DE": "EUR", "US": "USD", "KRYPTO": "USD", "ROHSTOFFE": "USD"}
ZEICHEN = {"EUR": "€", "USD": "$"}
# Wie alt der Datenstand höchstens sein darf, damit eine Order ausgeführt wird (DE: 15 Min. Verzögerung plus Puffer)
FRISCH_NS = {"DE": 35 * 60 * 10**9, "US": 20 * 60 * 10**9, "KRYPTO": 5 * 60 * 10**9, "ROHSTOFFE": 5 * 60 * 10**9}
# Reguläre Handelszeit je Markt (Ortszeit der Börse, nur werktags); Krypto handelt immer.
# Abends handelt Xetra nur im dünnen Retail-Buch, das reguläre Buch ist dann leer oder veraltet.
HANDELSZEIT = {"DE": (ZoneInfo("Europe/Berlin"), (9, 0), (17, 30)), "US": (ZoneInfo("America/New_York"), (9, 30), (16, 0))}
VOREINSTELLUNGEN = {"start_eur": 50_000.0, "start_usd": 50_000.0, "gebuehr": 1.0}
from .. import ports
ANALYSEN = ports.url(ports.ANALYSEN)
AGENT = ports.url(ports.AGENT)

SCHEMA = """
CREATE TABLE IF NOT EXISTS papier_orders (nr INTEGER PRIMARY KEY, id TEXT, symbol TEXT, name TEXT, markt TEXT,
    seite TEXT, art TEXT, menge REAL, limit_preis REAL, stopp_preis REAL, notiz TEXT, status TEXT, erstellt TEXT,
    ausgeloest TEXT, ausgefuehrt TEXT, datenzeit TEXT, preis REAL, gebuehr REAL, hinweis TEXT,
    stop REAL, ziel REAL, short INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS papier_schutz (id TEXT PRIMARY KEY, symbol TEXT, richtung TEXT, stop REAL, ziel REAL, nr INTEGER, gesetzt TEXT);
CREATE TABLE IF NOT EXISTS papier_journal (nr INTEGER PRIMARY KEY, art TEXT, bezug INTEGER, zeit TEXT, text TEXT,
    modell TEXT, kosten REAL);
"""
# Journal: art "eigen" schreibt der Nutzer selbst, alle anderen Claude (woche, tag, frage, trade, fehler)
JFELDER = ("nr", "art", "bezug", "zeit", "text", "modell", "kosten", "symbol", "titel", "geaendert")
FELDER = ("nr", "id", "symbol", "name", "markt", "seite", "art", "menge", "limit_preis", "stopp_preis", "notiz", "status",
          "erstellt", "ausgeloest", "ausgefuehrt", "datenzeit", "preis", "gebuehr", "hinweis", "stop", "ziel", "short")
SCHUTZ_FELDER = ("id", "symbol", "richtung", "stop", "ziel", "nr", "gesetzt")
RISIKO_MARKT = {"DE": "xetra", "US": "us", "KRYPTO": "krypto", "ROHSTOFFE": "krypto"}     # Markt im Sinn von kitrader.risiko (Handelszeiten)
_EPS = 1e-9


def _jetzt() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _preis(p: float | None, waehrung: str = "") -> str:
    if p is None:
        return "–"
    s = f"{p:,.{3 if p < 10 else 2}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {ZEICHEN.get(waehrung, '')}".strip()


def _menge(m: float) -> str:
    return f"{m:g}".replace(".", ",")


def _post(url: str, daten: dict, timeout: int) -> dict:
    anfrage = urllib.request.Request(url, data=json.dumps(daten).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(anfrage, timeout=timeout) as r:
            return json.load(r)
    except Exception as e:
        return {"fehler": f"{type(e).__name__}: {e}"[:200]}


def durch_das_buch(stufen: list, menge: float) -> tuple[float, float] | None:
    """Durchschnittspreis und schlechtester Preis, wenn menge durch die Stufen [(preis, menge, …)] läuft.
    None, wenn das Buch leer ist; reicht die Tiefe nicht, wird der Rest zum letzten Preis gerechnet."""
    if not stufen:
        return None
    rest, kosten, letzter = menge, 0.0, stufen[0][0]
    for s in stufen:
        n = min(rest, s[1])
        kosten += n * s[0]
        rest -= n
        letzter = s[0]
        if rest <= 0:
            break
    kosten += max(rest, 0) * letzter
    return kosten / menge, letzter


def in_handelszeit(markt: str, ts_ns: int) -> bool:
    if markt not in HANDELSZEIT:
        return True
    zone, von, bis = HANDELSZEIT[markt]
    d = datetime.fromtimestamp(ts_ns / 1e9, zone)
    return d.weekday() < 5 and von <= (d.hour, d.minute) < bis


class Papier(threading.Thread):
    def __init__(self, zustand: Zustand):
        super().__init__(daemon=True, name="papier")
        self.z = zustand
        self.s = zustand.speicher
        self.lock = threading.RLock()
        with self.s.lock:
            self.s.db.executescript(SCHEMA)
            spalten = {z[1] for z in self.s.db.execute("PRAGMA table_info(papier_journal)")}
            for s in ("symbol", "titel", "geaendert"):                # Journal als Tagebuch (21.10.2026)
                if s not in spalten:
                    self.s.db.execute(f"ALTER TABLE papier_journal ADD COLUMN {s} TEXT")
            spalten = {z[1] for z in self.s.db.execute("PRAGMA table_info(papier_orders)")}
            for s, typ in (("stop", "REAL"), ("ziel", "REAL"), ("short", "INTEGER DEFAULT 0")):   # Short, Stop und Ziel
                if s not in spalten:
                    self.s.db.execute(f"ALTER TABLE papier_orders ADD COLUMN {s} {typ}")
        # Einstellungen und Datenordner der Handelsteile für die Risikoprüfung: () -> (einst, ordner); das Dashboard setzt sie
        self.kitrader_quelle = None

    # --- Einstellungen -----------------------------------------------------------------------------
    def einstellungen(self) -> dict:
        werte = dict(VOREINSTELLUNGEN)
        for k, v in self.s._x("SELECT schluessel, wert FROM einstellungen WHERE schluessel LIKE 'papier_%'"):
            werte[k.removeprefix("papier_")] = json.loads(v)
        return werte

    def einstellen(self, **werte) -> dict:
        for k, v in werte.items():
            if k in VOREINSTELLUNGEN and float(v) >= 0:
                self.s._x("INSERT OR REPLACE INTO einstellungen VALUES (?, ?)", ("papier_" + k, json.dumps(float(v))))
        return self.einstellungen()

    def zuruecksetzen(self) -> dict:
        with self.lock:
            self.s._x("DELETE FROM papier_orders")
            self.s._x("DELETE FROM papier_schutz")
            self.s._x("DELETE FROM papier_journal")
            self.s._x("INSERT OR REPLACE INTO einstellungen VALUES (?, ?)", ("papier_seit", json.dumps(_jetzt())))
        return {"ok": True}

    # --- Lesen -------------------------------------------------------------------------------------
    def orders(self, wo: str = "", werte: tuple = ()) -> list[dict]:
        return [dict(zip(FELDER, z)) for z in self.s._x(f"SELECT {', '.join(FELDER)} FROM papier_orders {wo} ORDER BY nr", werte)]

    def journal(self, q: str = "", wer: str = "", symbol: str = "", n: int | None = None) -> list[dict]:
        """Einträge, neueste zuerst; q sucht in Text, Frage und Kürzel, wer = du | claude."""
        wo, werte = [], []
        for wort in q.split():
            wo.append("(text LIKE ? OR titel LIKE ? OR symbol LIKE ?)")
            werte += [f"%{wort}%"] * 3
        if wer == "du":
            wo.append("art = 'eigen'")
        elif wer == "claude":
            wo.append("art != 'eigen'")
        if symbol:
            wo.append("symbol = ?")
            werte.append(symbol)
        sql = (f"SELECT {', '.join(JFELDER)} FROM papier_journal {'WHERE ' + ' AND '.join(wo) if wo else ''} ORDER BY nr DESC"
               + (f" LIMIT {int(n)}" if n else ""))
        return [dict(zip(JFELDER, z)) for z in self.s._x(sql, tuple(werte))]

    def abrechnung(self) -> dict:
        """Positionen und abgeschlossene Trades nach FIFO aus den ausgeführten Orders, Guthaben je Währung.
        Ein Verkauf schließt Long-Lose und eröffnet, wenn er darüber hinausgeht (Short-Order), ein Short-Los; ein Kauf deckt Short-Lose
        ein, der Rest ist ein Long-Los. Short-Positionen haben einen negativen Wert (Verbindlichkeit), das Depot bleibt Guthaben plus Werte."""
        e = self.einstellungen()
        kasse = {"EUR": e["start_eur"], "USD": e["start_usd"]}
        lose: dict[str, list] = {}           # Wert -> offene Teile [menge (negativ = short), preis, gebuehr_je_stueck, nr, zeit, notiz]
        trades = []
        ausgefuehrt = sorted(self.orders("WHERE status = 'ausgefuehrt'"), key=lambda o: (o["ausgefuehrt"] or "", o["nr"]))
        for o in ausgefuehrt:
            w = WAEHRUNG[o["markt"]]
            kauf = o["seite"] == "kauf"
            kasse[w] += (-1 if kauf else 1) * o["menge"] * o["preis"] - o["gebuehr"]
            rest, einstand, gebuehren, kaeufe, eroeffnet, notizen = o["menge"], 0.0, 0.0, [], None, []
            for los in lose.get(o["id"], []):
                if rest <= 0:
                    break
                if (los[0] < 0) != kauf:                  # ein Kauf deckt Shorts ein, ein Verkauf schließt Longs
                    continue
                n = min(rest, abs(los[0]))
                einstand += n * los[1]
                gebuehren += n * los[2]
                los[0] += n if los[0] < 0 else -n
                rest -= n
                kaeufe.append(los[3])
                eroeffnet = eroeffnet or los[4]
                if los[5]:
                    notizen.append(los[5])
            lose[o["id"]] = [x for x in lose.get(o["id"], []) if abs(x[0]) > 1e-12]
            menge = o["menge"] - max(rest, 0)
            if menge > 0:
                gebuehren += o["gebuehr"] * menge / o["menge"]
                ergebnis = ((einstand - o["preis"] * menge) if kauf else (o["preis"] * menge - einstand)) - gebuehren
                trades.append({"nr": o["nr"], "id": o["id"], "symbol": o["symbol"], "name": o["name"], "markt": o["markt"], "waehrung": w,
                               "richtung": "short" if kauf else "long",
                               "menge": menge, "einstieg": einstand / menge, "ausstieg": o["preis"], "eroeffnet": eroeffnet,
                               "geschlossen": o["datenzeit"] or o["ausgefuehrt"], "gebuehren": round(gebuehren, 2),
                               "ergebnis": round(ergebnis, 2), "ergebnis_pct": round(ergebnis / einstand * 100, 2) if einstand else None,
                               "kaeufe": kaeufe, "notiz_kauf": " · ".join(dict.fromkeys(notizen)), "notiz_verkauf": o["notiz"] or ""})
            if rest > 1e-12:                           # der Rest eröffnet eine Position: Kauf = long, Verkauf (Short-Order) = short
                lose.setdefault(o["id"], []).append([rest if kauf else -rest, o["preis"], o["gebuehr"] / o["menge"], o["nr"],
                                                     o["datenzeit"] or o["ausgefuehrt"], o["notiz"]])
        positionen = []
        with self.z.lock:
            kurse = {w.id: (w.letzter, iso_aus_ns(w.zeit) if w.zeit else None) for w in self.z.werte.values()}
        for id_, teile in lose.items():
            signiert = sum(t[0] for t in teile)
            if abs(signiert) <= 1e-12:
                continue
            short, menge = signiert < 0, abs(signiert)
            o = next(x for x in ausgefuehrt if x["id"] == id_)
            einstand = sum(abs(t[0]) * t[1] for t in teile) / menge
            kurs, zeit = kurse.get(id_, (None, None))
            wert = (kurs or einstand) * menge * (-1 if short else 1)
            diff = (einstand - kurs) if short else ((kurs - einstand) if kurs else None)
            positionen.append({"id": id_, "symbol": o["symbol"], "name": o["name"], "markt": o["markt"], "waehrung": WAEHRUNG[o["markt"]],
                               "richtung": "short" if short else "long",
                               "menge": menge, "einstand": einstand, "kurs": kurs, "datenzeit": zeit, "wert": round(wert, 2),
                               "ergebnis": round(diff * menge, 2) if kurs else None,
                               "ergebnis_pct": round(diff / einstand * 100, 2) if kurs else None,
                               "seit": teile[0][4]})
        konto = {}
        for w in ("EUR", "USD"):
            pos = [p for p in positionen if p["waehrung"] == w]
            tr = [t for t in trades if t["waehrung"] == w]
            start = e["start_eur" if w == "EUR" else "start_usd"]
            wert = sum(p["wert"] for p in pos)
            konto[w] = {"start": start, "guthaben": round(kasse[w], 2), "positionen": round(wert, 2), "depot": round(kasse[w] + wert, 2),
                        "ergebnis": round(kasse[w] + wert - start, 2), "ergebnis_pct": round((kasse[w] + wert - start) / start * 100, 2) if start else None,
                        "realisiert": round(sum(t["ergebnis"] for t in tr), 2),
                        "unrealisiert": round(sum(p["ergebnis"] or 0 for p in pos), 2)}
        gewinner = sum(1 for t in trades if t["ergebnis"] > 0)
        return {"konto": konto, "positionen": positionen, "trades": list(reversed(trades)),
                "statistik": {"trades": len(trades), "gewinner": gewinner, "verlierer": len(trades) - gewinner,
                              "trefferquote": round(gewinner / len(trades) * 100) if trades else None}}

    def uebersicht(self) -> dict:
        a = self.abrechnung()
        with self.z.lock:
            handelbar = {w.symbol: {"id": w.id, "name": w.name, "markt": w.markt, "waehrung": WAEHRUNG[w.markt], "kurs": w.letzter}
                         for w in self.z.werte.values()}
        alle = self.orders()
        e = self.einstellungen()
        return {**a, "offen": [o for o in alle if o["status"] == "offen"],
                "verlauf": list(reversed([o for o in alle if o["status"] != "offen"]))[:200],
                "journal": self.journal()[:100], "einstellungen": {k: e[k] for k in VOREINSTELLUNGEN}, "seit": e.get("seit"), "fx": e.get("fx"),
                "handelbar": handelbar, "schutz": self.schutz(), "stand": _jetzt()}

    # --- Orders ------------------------------------------------------------------------------------
    @staticmethod
    def _zahl(x, name: str, pflicht: bool = False):
        if x is None or x == "":
            if pflicht:
                raise ValueError(f"{name} fehlt")
            return None
        try:
            v = float(x)
        except (TypeError, ValueError):
            raise ValueError(f"{name} ist keine Zahl") from None
        if not v > 0 or v != v or v in (float("inf"),):
            raise ValueError(f"{name} muss größer als null sein")
        return v

    def aufgeben(self, symbol: str, seite: str, art: str, menge, limit=None, stopp=None, notiz: str = "", stop=None, ziel=None,
                 short=False, pruefen=False, **_) -> dict:
        """Order aufgeben. `stop`/`ziel`: Schutz-Stop und Gewinnziel der neuen Position (werden nach der Ausführung überwacht);
        `short`: Verkauf ohne Bestand eröffnet eine Short-Position; `pruefen` (immer bei Short, Stop oder Ziel): jede Eröffnung geht durch
        `kitrader.risiko`, bei Ablehnung steht `gruende` in der Antwort. Ohne diese Angaben gilt das alte Verhalten (nur long)."""
        with self.z.lock:
            w = next((x for x in self.z.werte.values() if x.symbol == symbol or x.id == symbol), None)
        if w is None:
            return {"fehler": f"{symbol} ist nicht auf der Watchlist; Papierhandel geht nur mit Werten der Watchlist."}
        if seite not in ("kauf", "verkauf") or art not in ("markt", "limit", "stopp"):
            return {"fehler": "Seite oder Orderart unbekannt"}
        if short and seite != "verkauf":
            return {"fehler": "Short heißt: verkaufen ohne Bestand. Zum Eindecken einer Short-Position einfach kaufen."}
        try:
            menge = float(menge)
            limit = float(limit) if art == "limit" else None
            stopp = float(stopp) if art == "stopp" else None
        except (TypeError, ValueError):
            return {"fehler": "Stückzahl oder Preis ist keine Zahl"}
        try:
            stop, ziel = self._zahl(stop, "Der Stop"), self._zahl(ziel, "Das Ziel")
        except ValueError as e:
            return {"fehler": str(e)}
        if menge <= 0 or (w.markt not in ("KRYPTO", "ROHSTOFFE") and menge != int(menge)):
            return {"fehler": "Aktien nur in ganzen Stück, Menge größer als null"}
        if (art == "limit" and not limit or limit is not None and limit <= 0) or (art == "stopp" and not stopp or stopp is not None and stopp <= 0):
            return {"fehler": "Für Limit- und Stopp-Orders einen Preis angeben"}
        with self.lock:
            pos = next((p for p in self.abrechnung()["positionen"] if p["id"] == w.id), None)
            lang = pos["menge"] if pos and pos["richtung"] == "long" else 0.0
            kurz = pos["menge"] if pos and pos["richtung"] == "short" else 0.0
            if seite == "verkauf":
                reserviert = sum(o["menge"] for o in self.orders("WHERE status = 'offen' AND seite = 'verkauf' AND id = ?", (w.id,)))
                if short:
                    if lang > _EPS:
                        return {"fehler": f"Es gibt eine Long-Position ({_menge(lang)}): erst schließen, dann Short eröffnen."}
                elif menge > lang - reserviert + _EPS:
                    return {"fehler": f"Nur long: Bestand {_menge(lang)}, davon {_menge(reserviert)} schon in offenen Verkaufsorders"
                                      + (" (für Short: „Short“ wählen)" if kurz <= _EPS and lang <= _EPS else "")}
            elif kurz > _EPS:
                eingedeckt = sum(o["menge"] for o in self.orders("WHERE status = 'offen' AND seite = 'kauf' AND id = ?", (w.id,)))
                if menge > kurz - eingedeckt + _EPS:
                    return {"fehler": f"Short-Position {_menge(kurz)}: Kaufen deckt sie höchstens ein (davon {_menge(eingedeckt)} schon in offenen Kauforders)."}
            eroeffnung = (seite == "verkauf" and short) or (seite == "kauf" and kurz <= _EPS)
            if eroeffnung and (pruefen or short or stop or ziel):
                r = self.risiko(w, seite, art, menge, limit, stopp, stop, ziel)
                if not r["ok"]:
                    return {"fehler": "Abgelehnt von der Risikoprüfung: " + "; ".join(r["gruende"]), "gruende": r["gruende"], "pruefung": r}
            self.s._x("INSERT INTO papier_orders (id, symbol, name, markt, seite, art, menge, limit_preis, stopp_preis, notiz, status, erstellt,"
                      " stop, ziel, short) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'offen', ?, ?, ?, ?)",
                      (w.id, w.symbol, w.name, w.markt, seite, art, menge, limit, stopp, (notiz or "").strip()[:500], _jetzt(),
                       stop if eroeffnung else None, ziel if eroeffnung else None, 1 if short else 0))
            nr = self.s._x("SELECT max(nr) FROM papier_orders")[0][0]
        self.pruefen()
        return {"order": self.orders("WHERE nr = ?", (nr,))[0]}

    # --- Risikoprüfung (kitrader.risiko) und Schließen --------------------------------------------
    def _kitrader(self) -> tuple[dict, object]:
        from ... import einstellungen as _einst
        if self.kitrader_quelle:
            einst, ordner = self.kitrader_quelle()
            return _einst.mit_standard(einst), ordner
        return _einst.mit_standard({}), None

    def risiko(self, w, seite: str, art: str, menge: float, limit=None, stopp=None, stop=None, ziel=None) -> dict:
        """Prüft eine Eröffnung mit `kitrader.risiko.pruefen` und rechnet die Kennzahlen dazu. {"ok", "gruende", "preis", "risiko", ...}.
        Die Lage entsteht aus dem Papierdepot der Währung des Werts: Gesamtwert = Depot, Tagesergebnis = heute geschlossene Trades,
        Alter des Kurses nach Markt (deutsche Kurse sind 15 Minuten verzögert), Handelszeiten und Not-Aus wie beim Autopiloten."""
        from ...broker.basis import Kurs, Order
        from ...hilfen import jetzt_utc
        from ... import risiko as _risiko
        einst, ordner = self._kitrader()
        einst = {**einst, "risiko": {**einst["risiko"], "kurs_max_alter_s": FRISCH_NS[w.markt] / 1e9},
                 "maerkte": {**einst["maerkte"], w.symbol: RISIKO_MARKT[w.markt]}}
        wahrung = WAEHRUNG[w.markt]
        a = self.abrechnung()
        heute = _jetzt()[:10]
        positionen = {p["symbol"]: p["menge"] * (-1 if p["richtung"] == "short" else 1) for p in a["positionen"]}
        platz = w.plaetze.get(HAUPTPLATZ[w.markt], {})
        zeit = datetime.fromtimestamp(w.zeit / 1e9, UTC) if w.zeit else jetzt_utc()
        kurs = Kurs(w.symbol, platz.get("bid"), platz.get("ask"), w.letzter, zeit, waehrung=wahrung) if (w.letzter or platz.get("ask")) and w.zeit else None
        konto = a["konto"][wahrung]
        orders_heute = sum(1 for o in self.orders("WHERE status != 'storniert'") if (o["erstellt"] or "")[:10] == heute and WAEHRUNG[o["markt"]] == wahrung)
        ergebnis_heute = sum(t["ergebnis"] for t in a["trades"] if t["waehrung"] == wahrung and (t["geschlossen"] or "")[:10] == heute)
        lage = _risiko.Lage(jetzt=jetzt_utc(), gesamtwert=konto["depot"], einstellungen=einst, symbol=w.symbol, guthaben=konto["guthaben"],
                            positionen=positionen, tagesergebnis=ergebnis_heute, orders_heute=orders_heute, kurs=kurs, datenordner=ordner,
                            konto_waehrung=wahrung, wechselkurs=1.0)
        order_art = {"markt": "markt", "limit": "limit", "stopp": "stop"}[art]
        try:
            order = Order(w.symbol, seite, menge, order_art, limit=limit, stop=stopp, stop_loss=stop, take_profit=ziel)
        except ValueError as e:
            return {"ok": False, "gruende": [str(e)]}
        erg = _risiko.pruefen(order, lage)
        preis = _risiko.einstiegspreis(order, kurs)
        r = einst["risiko"]
        gesamt = konto["depot"]
        d = {"ok": erg.ok, "gruende": list(erg.gruende), "preis": preis, "waehrung": wahrung, "gesamtwert": gesamt,
             "risiko_max": gesamt * r["risiko_pro_trade_prozent"] / 100, "risiko_pro_trade_prozent": r["risiko_pro_trade_prozent"],
             "position_max_prozent": r["position_max_prozent"], "stop_pflicht": bool(r["stop_loss_pflicht"]), "short_erlaubt": bool(r["short_erlaubt"]),
             "risiko": None, "risiko_prozent": None, "wert": None, "position_prozent": None, "crv": None}
        if preis:
            d["wert"] = menge * preis
            d["position_prozent"] = d["wert"] / gesamt * 100 if gesamt > 0 else None
            if stop:
                d["risiko"] = abs(preis - stop) * menge
                d["risiko_prozent"] = d["risiko"] / gesamt * 100 if gesamt > 0 else None
                if ziel and abs(preis - stop) > 0:
                    d["crv"] = abs(ziel - preis) / abs(preis - stop)
        return d

    def pruefung(self, symbol: str, seite: str = "kauf", art: str = "markt", menge=None, limit=None, stopp=None, stop=None, ziel=None,
                 short=False, **_) -> dict:
        """Nur prüfen und rechnen (Vorschau in der Karte Papierhandel), nichts wird aufgegeben."""
        with self.z.lock:
            w = next((x for x in self.z.werte.values() if x.symbol == symbol or x.id == symbol), None)
        if w is None:
            return {"fehler": f"{symbol} ist nicht auf der Watchlist"}
        try:
            menge = self._zahl(menge, "Die Menge", pflicht=True)
            stop, ziel = self._zahl(stop, "Der Stop"), self._zahl(ziel, "Das Ziel")
            limit, stopp = self._zahl(limit, "Das Limit"), self._zahl(stopp, "Der Stopp")
        except ValueError as e:
            return {"fehler": str(e)}
        if seite not in ("kauf", "verkauf") or art not in ("markt", "limit", "stopp") or (art == "limit" and not limit) or (art == "stopp" and not stopp):
            return {"fehler": "Seite, Orderart oder Preis unvollständig"}
        pos = next((p for p in self.abrechnung()["positionen"] if p["id"] == w.id), None)
        schliesst = pos is not None and ((seite == "verkauf" and pos["richtung"] == "long" and not short) or (seite == "kauf" and pos["richtung"] == "short"))
        if schliesst:
            return {"ok": True, "gruende": [], "schliesst": True}
        return {**self.risiko(w, seite, art, menge, limit if art == "limit" else None, stopp if art == "stopp" else None, stop, ziel), "schliesst": False}

    def schliessen(self, symbol: str, notiz: str = "Position geschlossen", **_) -> dict:
        """Stellt die ganze Position zum Markt glatt (offene Orders des Werts werden vorher storniert, der Schutz entfällt mit der Ausführung)."""
        with self.z.lock:
            w = next((x for x in self.z.werte.values() if x.symbol == symbol or x.id == symbol), None)
        if w is None:
            return {"fehler": f"{symbol} ist nicht auf der Watchlist"}
        with self.lock:
            pos = next((p for p in self.abrechnung()["positionen"] if p["id"] == w.id), None)
            if pos is None:
                return {"fehler": f"Zu {w.symbol} gibt es keine offene Position."}
            for o in self.orders("WHERE status = 'offen' AND id = ?", (w.id,)):
                self.s._x("UPDATE papier_orders SET status = 'storniert', ausgefuehrt = ? WHERE nr = ?", (_jetzt(), o["nr"]))
            self.s._x("INSERT INTO papier_orders (id, symbol, name, markt, seite, art, menge, notiz, status, erstellt, short)"
                      " VALUES (?, ?, ?, ?, ?, 'markt', ?, ?, 'offen', ?, 0)",
                      (w.id, w.symbol, w.name, w.markt, "verkauf" if pos["richtung"] == "long" else "kauf", pos["menge"], (notiz or "")[:500], _jetzt()))
            nr = self.s._x("SELECT max(nr) FROM papier_orders")[0][0]
        self.pruefen()
        return {"order": self.orders("WHERE nr = ?", (nr,))[0]}

    # --- Schutz: Stop und Ziel der offenen Positionen ----------------------------------------------
    def schutz(self) -> list[dict]:
        return [dict(zip(SCHUTZ_FELDER, z)) for z in self.s._x(f"SELECT {', '.join(SCHUTZ_FELDER)} FROM papier_schutz ORDER BY gesetzt")]

    def _schutz_pflegen(self, o: dict) -> None:
        """Nach einer Ausführung: Stop und Ziel einer eröffneten Position merken, bei geschlossener Position löschen."""
        pos = next((p for p in self.abrechnung()["positionen"] if p["id"] == o["id"]), None)
        if pos is None:
            self.s._x("DELETE FROM papier_schutz WHERE id = ?", (o["id"],))
        elif o.get("stop") or o.get("ziel"):
            self.s._x("INSERT OR REPLACE INTO papier_schutz VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (o["id"], o["symbol"], pos["richtung"], o.get("stop"), o.get("ziel"), o["nr"], _jetzt()))

    def _schutz_pruefen(self, jetzt: int) -> None:
        """Stop oder Ziel erreicht: die Position wird mit einer Markt-Order geschlossen (läuft danach wie jede andere Order)."""
        for sch in self.schutz():
            with self.z.lock:
                w = self.z.werte.get(sch["id"])
                if w is None or not w.letzter or not w.zeit or jetzt - w.zeit > FRISCH_NS[w.markt] or not in_handelszeit(w.markt, w.zeit):
                    continue
                kurs = w.letzter
            pos = next((p for p in self.abrechnung()["positionen"] if p["id"] == sch["id"]), None)
            if pos is None:
                self.s._x("DELETE FROM papier_schutz WHERE id = ?", (sch["id"],))
                continue
            lang = pos["richtung"] == "long"
            grund = None
            if sch["stop"] and (kurs <= sch["stop"] if lang else kurs >= sch["stop"]):
                grund = f"Stop {_preis(sch['stop'])} erreicht (Kurs {_preis(kurs)})"
            elif sch["ziel"] and (kurs >= sch["ziel"] if lang else kurs <= sch["ziel"]):
                grund = f"Ziel {_preis(sch['ziel'])} erreicht (Kurs {_preis(kurs)})"
            if not grund or self.orders("WHERE status = 'offen' AND id = ? AND notiz LIKE 'Schutz:%'", (sch["id"],)):
                continue
            self.s._x("INSERT INTO papier_orders (id, symbol, name, markt, seite, art, menge, notiz, status, erstellt, short)"
                      " VALUES (?, ?, ?, ?, ?, 'markt', ?, ?, 'offen', ?, 0)",
                      (w.id, w.symbol, w.name, w.markt, "verkauf" if lang else "kauf", pos["menge"], "Schutz: " + grund, _jetzt()))

    def stornieren(self, nr: int) -> dict:
        with self.lock:
            o = self.orders("WHERE nr = ?", (nr,))
            if not o or o[0]["status"] != "offen":
                return {"fehler": "Order ist nicht mehr offen"}
            self.s._x("UPDATE papier_orders SET status = 'storniert', ausgefuehrt = ? WHERE nr = ?", (_jetzt(), nr))
        return {"nr": nr}

    def journal_schreiben(self, art: str, text: str, bezug: int | None = None, modell: str = "", kosten: float | None = None,
                          symbol: str | None = None, titel: str | None = None) -> dict:
        self.s._x("INSERT INTO papier_journal (art, bezug, zeit, text, modell, kosten, symbol, titel) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (art, bezug, _jetzt(), text.strip(), modell, kosten, symbol or None, (titel or "").strip()[:500] or None))
        nr = self.s._x("SELECT max(nr) FROM papier_journal")[0][0]
        return {"ok": True, "eintrag": self.eintrag_lesen(nr)}

    def eintrag_lesen(self, nr: int) -> dict | None:
        z = self.s._x(f"SELECT {', '.join(JFELDER)} FROM papier_journal WHERE nr = ?", (nr,))
        return dict(zip(JFELDER, z[0])) if z else None

    def eintrag(self, text: str = "", symbol: str = "", nr: int | None = None, **_) -> dict:
        """Eigener Eintrag des Nutzers: neu oder (mit nr) geändert. Claudes Einträge bleiben, wie sie geschrieben wurden."""
        text = str(text or "").strip()
        if not text:
            return {"fehler": "Der Eintrag ist leer"}
        if len(text) > 5000:
            return {"fehler": "Höchstens 5.000 Zeichen"}
        symbol = str(symbol or "").strip() or None
        if nr is None:
            return self.journal_schreiben("eigen", text, symbol=symbol)
        alt = self.eintrag_lesen(int(nr))
        if not alt or alt["art"] != "eigen":
            return {"fehler": "Nur eigene Einträge lassen sich ändern"}
        self.s._x("UPDATE papier_journal SET text = ?, symbol = ?, geaendert = ? WHERE nr = ?", (text, symbol, _jetzt(), int(nr)))
        return {"ok": True, "eintrag": self.eintrag_lesen(int(nr))}

    def eintrag_loeschen(self, nr: int) -> dict:
        alt = self.eintrag_lesen(nr)
        if not alt or alt["art"] != "eigen":
            return {"fehler": "Nur eigene Einträge lassen sich löschen"}
        self.s._x("DELETE FROM papier_journal WHERE nr = ?", (nr,))
        return {"nr": nr}

    # --- Ausführung --------------------------------------------------------------------------------
    def _markt(self, w, seite: str, menge: float) -> tuple[float, str] | None:
        """Ausführungspreis einer Markt-Order und ein Hinweis, wie er zustande kam; None ohne Kurs."""
        if w.markt in ("DE", "KRYPTO"):
            k = (HAUPTPLATZ[w.markt], w.id, "regulaer")
            stufen = self.z.buch.stufen(k, "A" if seite == "kauf" else "B", 5)
            r = durch_das_buch(stufen, menge)
            if r:
                tiefe = sum(s[1] for s in stufen)
                return r[0], "durch das Orderbuch" + (" · Buch zu dünn, Rest zum letzten Stufenpreis" if tiefe < menge else "")
        p = w.plaetze.get(HAUPTPLATZ[w.markt], {})
        bester = p.get("ask" if seite == "kauf" else "bid")
        if bester:
            return bester, "zum besten Kurs" + (" (IEX)" if w.markt == "US" else "")
        if w.letzter:
            return w.letzter, "zum letzten Abschluss (kein Kurs im Buch)"
        return None

    def _ausfuehren(self, o: dict, preis: float, hinweis: str, w) -> None:
        gebuehr = self.einstellungen()["gebuehr"]
        if o["seite"] == "kauf":
            guthaben = self.abrechnung()["konto"][WAEHRUNG[o["markt"]]]["guthaben"]
            if o["menge"] * preis + gebuehr > guthaben + 1e-6:
                self.s._x("UPDATE papier_orders SET status = 'abgelehnt', ausgefuehrt = ?, hinweis = ? WHERE nr = ?",
                          (_jetzt(), f"nicht genug Guthaben ({_preis(guthaben, WAEHRUNG[o['markt']])})", o["nr"]))
                return
        elif not o.get("short"):
            bestand = next((p["menge"] for p in self.abrechnung()["positionen"] if p["id"] == o["id"] and p["richtung"] == "long"), 0)
            if o["menge"] > bestand + 1e-9:
                self.s._x("UPDATE papier_orders SET status = 'abgelehnt', ausgefuehrt = ?, hinweis = ? WHERE nr = ?",
                          (_jetzt(), f"Bestand reicht nicht ({_menge(bestand)})", o["nr"]))
                return
        if o["markt"] == "DE":
            hinweis += " · Datenstand 15 Min. verzögert"
        self.s._x("UPDATE papier_orders SET status = 'ausgefuehrt', ausgefuehrt = ?, datenzeit = ?, preis = ?, gebuehr = ?, hinweis = ? WHERE nr = ?",
                  (_jetzt(), iso_aus_ns(w.zeit) if w.zeit else _jetzt(), round(preis, 6), gebuehr, hinweis, o["nr"]))
        self._schutz_pflegen(o)
        threading.Thread(target=self._nach_ausfuehrung, args=(o["nr"],), daemon=True).start()

    def pruefen(self) -> None:
        jetzt = time.time_ns()
        with self.lock:
            self._schutz_pruefen(jetzt)
            for o in self.orders("WHERE status = 'offen'"):
                with self.z.lock:
                    w = self.z.werte.get(o["id"])
                    if w is None:
                        self.s._x("UPDATE papier_orders SET status = 'abgelehnt', ausgefuehrt = ?, hinweis = ? WHERE nr = ?",
                                  (_jetzt(), "nicht mehr auf der Watchlist", o["nr"]))
                        continue
                    if not w.zeit or jetzt - w.zeit > FRISCH_NS[w.markt] or not in_handelszeit(w.markt, w.zeit):
                        if o["hinweis"] != "wartet auf Handel":
                            self.s._x("UPDATE papier_orders SET hinweis = 'wartet auf Handel' WHERE nr = ?", (o["nr"],))
                        continue                                       # außerhalb der Handelszeit: Order wartet
                    kauf = o["seite"] == "kauf"
                    art = o["art"]
                    if art == "stopp":
                        if not w.letzter or (w.letzter < o["stopp_preis"] if kauf else w.letzter > o["stopp_preis"]):
                            continue
                        if not o["ausgeloest"]:
                            self.s._x("UPDATE papier_orders SET ausgeloest = ? WHERE nr = ?", (_jetzt(), o["nr"]))
                        art = "markt"
                    r = self._markt(w, o["seite"], o["menge"])
                    if r is None:
                        continue
                    preis, hinweis = r
                    if art == "limit":
                        if kauf and preis > o["limit_preis"] or not kauf and preis < o["limit_preis"]:
                            continue                                   # Limit noch nicht erreicht: Order bleibt offen
                        hinweis = "Limit erreicht, " + hinweis
                    elif o["art"] == "stopp":
                        hinweis = f"Stopp {_preis(o['stopp_preis'])} ausgelöst, " + hinweis
                    self._ausfuehren(o, preis, hinweis, w)

    # --- Meldungen und Journal -------------------------------------------------------------------
    def _nach_ausfuehrung(self, nr: int) -> None:
        o = self.orders("WHERE nr = ?", (nr,))[0]
        w = WAEHRUNG[o["markt"]]
        art = {"markt": "Markt", "limit": f"Limit {_preis(o['limit_preis'])}", "stopp": f"Stopp {_preis(o['stopp_preis'])}"}[o["art"]]
        zeilen = [f"🧾 <b>Papierhandel</b>: {'Kauf' if o['seite'] == 'kauf' else 'Short' if o.get('short') else 'Verkauf'} {_menge(o['menge'])} {o['symbol']} zu {_preis(o['preis'], w)}",
                  f"{o['name']} · {art} · Gebühr {_preis(o['gebuehr'], w)}"]
        trade = next((t for t in self.abrechnung()["trades"] if t["nr"] == nr), None)
        if trade:
            zeilen.append(f"Ergebnis {'+' if trade['ergebnis'] >= 0 else ''}{_preis(trade['ergebnis'], w)} ({trade['ergebnis_pct']:+.2f} %)".replace(".", ",", 1))
        if o["markt"] == "DE":
            zeilen.append("<i>Datenstand 15 Min. verzögert</i>")
        _post(ANALYSEN + "/api/telegram/senden", {"text": "\n".join(zeilen)}, 20)
        if trade:
            self._bewerten(trade)

    def _bewerten(self, t: dict) -> None:
        """Claude bewertet einen abgeschlossenen Trade fürs Journal."""
        text = ("Abgeschlossener Papier-Trade (nur simuliert): " + json.dumps(t, ensure_ascii=False)
                + "\nZeiten sind UTC (ISO). Bewerte ihn für das Journal.")
        antwort = _post(AGENT + "/papier", {"art": "trade", "text": text}, 300)
        if antwort.get("text"):
            self.journal_schreiben("trade", antwort["text"], t["nr"], antwort.get("modell", ""), antwort.get("kosten"), t["symbol"])
            _post(ANALYSEN + "/api/telegram/senden", {"text": f"📓 <b>Journal</b> · {t['symbol']}\n" + antwort["text"][:3500]}, 20)
        else:
            self.journal_schreiben("fehler", "Keine Bewertung: " + str(antwort.get("fehler", "leer")), t["nr"], symbol=t["symbol"])

    # --- Wechselkurs: EZB-Referenzkurs (Dollar je Euro), damit das Depot einen Gesamtwert in Euro hat ------------
    _fx_versuch = 0.0

    def fx_auffrischen(self) -> None:
        if time.time() - self._fx_versuch < 6 * 3600:
            return
        self._fx_versuch = time.time()
        try:
            k = list(ezb_kurs("USD", 1))[-1]
            self.s._x("INSERT OR REPLACE INTO einstellungen VALUES (?, ?)",
                      ("papier_fx", json.dumps({"eurusd": float(k["kurs"]), "datum": k["datum"], "quelle": "EZB"})))
        except Exception:
            self._fx_versuch = time.time() - 6 * 3600 + 600          # in 10 Minuten noch einmal

    def run(self) -> None:
        while True:
            try:
                self.fx_auffrischen()
                self.pruefen()
                self.z.melden("papier", geprueft=_jetzt(), fehler=None)
            except Exception as e:
                self.z.melden("papier", fehler=f"{type(e).__name__}: {str(e)[:120]}")
            time.sleep(2)
