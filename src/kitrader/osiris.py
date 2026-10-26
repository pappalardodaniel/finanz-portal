"""OSIRIS: Weltlage aus den offenen Live-Feeds von https://osirisai.live (Open Source, MIT, selbst hostbar).

`abrufen()` holt die für Märkte brauchbaren Feeds (höflich: eigener User-Agent, Pause zwischen den Abrufen,
nicht öfter als nötig) und speichert einen Schnappschuss in `osiris.db` (Datenordner).
`lage()` fasst den letzten Schnappschuss kompakt zusammen und nennt die Veränderung gegenüber dem vorigen.
Es steht nur drin, was die Feeds liefern; Einstufungen wie "HIGH" oder "CRITICAL" stammen von OSIRIS und
sind als solche gekennzeichnet. Eine Deutung der Lage findet hier nicht statt.

Genutzte Endpunkte (alle ohne Schlüssel, Doku https://osirisai.live/docs, Abschnitt "API Reference"):
  Geopolitical:        /api/conflicts, /api/gdelt, /api/country-risk
  Media & Markets:     /api/news, /api/markets, /api/scm-suppliers
  Surveillance & Inf.: /api/maritime (Häfen, Engpässe wie Meerengen)
  Earth & Environment: /api/earthquakes, /api/weather, /api/radar
  Cyber Threat:        /api/cyber-threats
  System:              /api/stats
Hinweis: /api/gdelt liefert auf osirisai.live laut Feld `source` derzeit GDACS-Katastrophenmeldungen (UN/EU),
keine GDELT-Konfliktereignisse; die Zusammenfassung nennt die Quelle so, wie der Feed sie angibt.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import einstellungen as _einst
from .hilfen import jetzt_utc, zahl, zu_utc

USER_AGENT = "kitrader/0.1 (privates Lernprojekt; urllib)"
MAX_BYTES = 8_000_000          # größere Antworten werden abgelehnt (der größte genutzte Feed hat rund 0,2 MB)

# Name -> Pfad. Bewusst nicht dabei: Flugzeuge, Satelliten, Kameras, Brände, Frontlinien (sehr groß, ohne Marktbezug).
FEEDS: dict[str, str] = {
    "stats": "/api/stats",
    "konflikte": "/api/conflicts",
    "vorfaelle": "/api/gdelt",
    "laenderrisiko": "/api/country-risk",
    "nachrichten": "/api/news",
    "maerkte": "/api/markets",
    "schifffahrt": "/api/maritime",
    "erdbeben": "/api/earthquakes",
    "unwetter": "/api/weather",
    "cyber": "/api/cyber-threats",
    "netzausfaelle": "/api/radar",
    "lieferketten": "/api/scm-suppliers",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schnappschuss (id INTEGER PRIMARY KEY AUTOINCREMENT, zeit TEXT NOT NULL,
    adresse TEXT, feeds_ok INTEGER, feeds_fehler INTEGER);
CREATE TABLE IF NOT EXISTS feed (schnappschuss_id INTEGER NOT NULL, name TEXT NOT NULL, pfad TEXT, status INTEGER,
    bytes INTEGER, daten BLOB, fehler TEXT, PRIMARY KEY (schnappschuss_id, name));
"""

_ART = {"earthquake": "Erdbeben", "weather": "Sturm/Wetter", "wildfire": "Waldbrand", "drought": "Dürre",
        "flood": "Flut", "volcano": "Vulkan", "tsunami": "Tsunami"}

# Grobe Einteilung nach Koordinaten (Rechtecke, erste Übereinstimmung gilt). Nur zum Zählen, keine Grenzen.
_REGIONEN = [("Naher Osten", 12, 38, 34, 60), ("Europa", 36, 72, -25, 40), ("Afrika", -35, 37, -20, 52),
             ("Ozeanien", -50, -11, 110, 180), ("Asien", -11, 78, 40, 180), ("Nordamerika", 7, 84, -170, -52),
             ("Südamerika", -56, 13, -82, -34)]


class OsirisFehler(RuntimeError):
    """OSIRIS nicht erreichbar oder unbrauchbare Antwort. Text ist für Menschen lesbar."""


@dataclass
class Lage:
    stand: datetime | None            # Zeit des Schnappschusses (None: noch keiner gespeichert)
    vorher: datetime | None           # Zeit des vorigen Schnappschusses, falls vorhanden
    text: str                         # fertige Zusammenfassung (für die KI und die Kommandozeile)
    daten: dict = field(default_factory=dict)

    def alter(self, jetzt: datetime | None = None) -> timedelta | None:
        return None if self.stand is None else zu_utc(jetzt or jetzt_utc()) - self.stand


# ------------------------------------------------------------------------------------------ Abruf

def _http_holen(url: str, timeout: float) -> tuple[int, bytes, str]:
    """GET mit eigenem User-Agent. Gibt (Status, Inhalt, Fehlertext) zurück; wirft nichts."""
    anfrage = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(anfrage, timeout=timeout) as r:
            inhalt = r.read(MAX_BYTES + 1)
            if len(inhalt) > MAX_BYTES:
                return r.status, b"", "Antwort zu groß"
            return r.status, inhalt, ""
    except urllib.error.HTTPError as e:
        return e.code, b"", f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, b"", str(getattr(e, "reason", e)) or type(e).__name__


class Osiris:
    def __init__(self, einstellungen: dict | None = None, pfad: str | Path | None = None,
                 uhr: Callable[[], datetime] | None = None, schlafen: Callable[[float], None] = time.sleep,
                 holen: Callable[[str, float], tuple[int, bytes, str]] = _http_holen):
        self.einst = _einst.mit_standard(einstellungen) if einstellungen is not None else _einst.laden()
        self.cfg = self.einst["osiris"]
        self.uhr, self.schlafen, self.holen = uhr or jetzt_utc, schlafen, holen
        self.db = sqlite3.connect(str(pfad if pfad is not None else _einst.datenordner() / "osiris.db"))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def beenden(self) -> None:
        self.db.commit()
        self.db.close()

    def abrufen(self, erzwingen: bool = False) -> dict:
        """Holt alle Feeds nacheinander (mit Pause) und speichert einen Schnappschuss.
        Ist der letzte jünger als `mindestabstand_s`, wird nichts abgerufen (Ausnahme: erzwingen).
        Gibt {"id", "zeit", "neu", "feeds": {name: {"status", "bytes", "fehler"}}} zurück.
        Fällt jeder Feed aus, wird OsirisFehler geworfen und nichts gespeichert."""
        letzter = self._schnappschuss(0)
        if letzter and not erzwingen:
            alter = (zu_utc(self.uhr()) - letzter["zeit"]).total_seconds()
            if alter < self.cfg["mindestabstand_s"]:
                return {"id": letzter["id"], "zeit": letzter["zeit"], "neu": False, "feeds": self._feedstatus(letzter["id"])}
        basis = self.cfg["adresse"].rstrip("/")
        ergebnisse: dict[str, dict] = {}
        for i, (name, pfad) in enumerate(FEEDS.items()):
            if i:
                self.schlafen(self.cfg["pause_s"])
            status, inhalt, fehler = self.holen(basis + pfad, self.cfg["zeitlimit_s"])
            daten = None
            if status == 200:
                try:
                    json.loads(inhalt)
                    daten = zlib.compress(inhalt, 6)
                except ValueError:
                    fehler = "keine gültige JSON-Antwort"
            elif not fehler:
                fehler = f"HTTP {status}"
            ergebnisse[name] = {"pfad": pfad, "status": status, "bytes": len(inhalt), "daten": daten, "fehler": fehler}
        ok = sum(e["daten"] is not None for e in ergebnisse.values())
        if ok == 0:
            raise OsirisFehler(f"OSIRIS ({basis}) lieferte keinen einzigen Feed: "
                               + "; ".join(f"{n}: {e['fehler']}" for n, e in list(ergebnisse.items())[:3]))
        zeit = zu_utc(self.uhr())
        c = self.db.execute("INSERT INTO schnappschuss (zeit,adresse,feeds_ok,feeds_fehler) VALUES (?,?,?,?)",
                            (zeit.isoformat(), basis, ok, len(ergebnisse) - ok))
        for name, e in ergebnisse.items():
            self.db.execute("INSERT INTO feed VALUES (?,?,?,?,?,?,?)",
                            (c.lastrowid, name, e["pfad"], e["status"], e["bytes"], e["daten"], e["fehler"] or None))
        self._aufraeumen(zeit)
        self.db.commit()
        return {"id": c.lastrowid, "zeit": zeit, "neu": True,
                "feeds": {n: {"status": e["status"], "bytes": e["bytes"], "fehler": e["fehler"]} for n, e in ergebnisse.items()}}

    def _aufraeumen(self, jetzt: datetime) -> None:
        grenze = (jetzt - timedelta(days=self.cfg["behalten_tage"])).isoformat()
        alt = [z[0] for z in self.db.execute("SELECT id FROM schnappschuss WHERE zeit < ?", (grenze,))]
        for i in alt:
            self.db.execute("DELETE FROM feed WHERE schnappschuss_id=?", (i,))
            self.db.execute("DELETE FROM schnappschuss WHERE id=?", (i,))

    # ------------------------------------------------------------------------------------ Lesen

    def _schnappschuss(self, rueckwaerts: int) -> dict | None:
        """0 = neuester, 1 = der davor."""
        z = self.db.execute("SELECT * FROM schnappschuss ORDER BY id DESC LIMIT 1 OFFSET ?", (rueckwaerts,)).fetchone()
        return None if z is None else {"id": z["id"], "zeit": datetime.fromisoformat(z["zeit"]),
                                       "adresse": z["adresse"]}

    def _feedstatus(self, sid: int) -> dict:
        return {z["name"]: {"status": z["status"], "bytes": z["bytes"], "fehler": z["fehler"] or ""}
                for z in self.db.execute("SELECT name,status,bytes,fehler FROM feed WHERE schnappschuss_id=?", (sid,))}

    def feeds(self, sid: int) -> dict[str, dict]:
        """Die gespeicherten (entpackten) Feeds eines Schnappschusses; fehlgeschlagene fehlen."""
        aus = {}
        for z in self.db.execute("SELECT name,daten FROM feed WHERE schnappschuss_id=? AND daten IS NOT NULL", (sid,)):
            aus[z["name"]] = json.loads(zlib.decompress(z["daten"]))
        return aus

    def lage(self) -> Lage:
        """Zusammenfassung des letzten Schnappschusses samt Veränderung zum vorigen (ohne Netzzugriff)."""
        neu = self._schnappschuss(0)
        if neu is None:
            return Lage(None, None, "OSIRIS: Es ist noch kein Schnappschuss gespeichert "
                                    "(python3 -m kitrader osiris abrufen).")
        v_neu = verdichten(self.feeds(neu["id"]))
        alt = self._schnappschuss(1)
        v_alt = verdichten(self.feeds(alt["id"])) if alt else None
        text = _text(v_neu, v_alt, neu["zeit"], alt["zeit"] if alt else None, neu["adresse"])
        return Lage(neu["zeit"], alt["zeit"] if alt else None, text, v_neu)


def abrufen(einstellungen: dict | None = None, erzwingen: bool = False, **kw) -> dict:
    o = Osiris(einstellungen, **kw)
    try:
        return o.abrufen(erzwingen)
    finally:
        o.beenden()


def lage(einstellungen: dict | None = None, **kw) -> Lage:
    o = Osiris(einstellungen, **kw)
    try:
        return o.lage()
    finally:
        o.beenden()


# ------------------------------------------------------------------------------------- Verdichten

def _region(lat, lng) -> str:
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return "unbekannt"
    for name, a, b, c, d in _REGIONEN:
        if a <= lat <= b and c <= lng <= d:
            return name
    return "Ozean/Sonstige"


def _kurz(text, n: int) -> str:
    """Leerraum glätten und bei n Zeichen mit … abschneiden."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def _parse(iso) -> datetime | None:
    try:
        return zu_utc(datetime.fromisoformat(str(iso).replace("Z", "+00:00")))
    except ValueError:
        return None


def _ms_zu_iso(ms) -> str:
    try:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _zahl(x, default=0.0) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else default


def _liste(d, schluessel) -> list:
    x = d.get(schluessel) if isinstance(d, dict) else None
    return [e for e in x if isinstance(e, dict)] if isinstance(x, list) else []


def verdichten(f: dict[str, dict]) -> dict:
    """Macht aus den rohen Feeds eines Schnappschusses eine kleine, vergleichbare Struktur.
    Schlüssel mit führendem Unterstrich dienen nur dem Vergleich und erscheinen nicht im Text."""
    f = {n: d for n, d in f.items() if isinstance(d, dict)}          # was kein Objekt ist, gilt als nicht abgerufen
    v: dict = {"fehlend": [n for n in FEEDS if n not in f]}

    # Vorfälle (Feed /api/gdelt)
    ereignisse = _liste(f.get("vorfaelle"), "events")
    if "vorfaelle" in f:
        regionen: dict[str, Counter] = {}
        arten: Counter = Counter()
        wichtig, schluessel = [], set()
        for e in ereignisse:
            art = _ART.get(e.get("type"), str(e.get("type") or "sonstige"))
            arten[art] += 1
            regionen.setdefault(_region(e.get("lat"), e.get("lng")), Counter())[art] += 1
            name = str(e.get("name") or "")
            schluessel.add(e.get("url") or name)
            m = re.match(r"(Orange|Red)\b", name)
            if m:
                wichtig.append({"stufe": {"Orange": "Orange", "Red": "Rot"}[m.group(1)], "art": art, "text": _kurz(name, 170), "ort": _region(e.get("lat"), e.get("lng")),
                                "schluessel": e.get("url") or name})
        v["vorfaelle"] = {"gesamt": len(ereignisse), "quelle": f["vorfaelle"].get("source", ""), "arten": dict(arten),
                          "regionen": {r: dict(c) for r, c in regionen.items()}, "wichtig": wichtig, "_schluessel": sorted(schluessel)}

    # Konfliktzonen (kuratierte Liste, Feed /api/conflicts)
    if "konflikte" in f:
        zonen = _liste(f["konflikte"], "zones")
        v["konflikte"] = {"zonen": [{"id": z.get("id"), "name": z.get("label"), "stufe": z.get("severity"),
                                     "meldungen": z.get("headlineCount", 0)} for z in zonen],
                          "quelle": f["konflikte"].get("zoneSource", "")}

    # Länderrisiko (redaktionelle Einstufung von OSIRIS)
    if "laenderrisiko" in f:
        laender = sorted(_liste(f["laenderrisiko"], "countries"), key=lambda c: -_zahl(c.get("risk_score")))
        meth = f["laenderrisiko"].get("methodology") or {}
        v["laenderrisiko"] = {"top": [{"land": c.get("code"), "wert": _zahl(c.get("risk_score")), "stufe": c.get("risk_level"),
                                       "merkmale": c.get("tags") or []} for c in laender[:6]],
                              "basis": meth.get("basis", ""), "erdbeben": [c.get("code") for c in laender if _zahl(c.get("quake_magnitude")) > 0],
                              "boersen_offen": f["laenderrisiko"].get("open_exchanges")}

    # Meldungen (Telegram-OSINT und Medien, Feed /api/news)
    if "nachrichten" in f:
        meldungen = _liste(f["nachrichten"], "news")
        lager = Counter(str(m.get("bloc") or "?") for m in meldungen)
        zeiten = sorted(str(m.get("published") or "") for m in meldungen if m.get("published"))
        ende = zeiten[-1] if zeiten else ""
        grenze = (_parse(ende) or datetime(1970, 1, 1, tzinfo=timezone.utc)) - timedelta(hours=48)

        def eintrag(m):
            titel = _kurz(m.get("title"), 400)
            if len(titel) < 40 and m.get("summary"):
                titel = f"{titel} – {_kurz(m['summary'], 400)}"
            return {"id": m.get("id"), "zeit": m.get("published"), "quelle": m.get("source_name") or m.get("source"),
                    "lager": m.get("bloc"), "richtung": m.get("lean"), "titel": _kurz(titel, 190), "wert": int(_zahl(m.get("risk_score"))),
                    "mehrfach": len(m.get("also_reported_by") or [])}
        neu_zuerst = sorted(meldungen, key=lambda m: str(m.get("published") or ""), reverse=True)
        jung = [m for m in neu_zuerst if (_parse(m.get("published")) or grenze) >= grenze]
        oben = sorted(jung, key=lambda m: -_zahl(m.get("risk_score")))[:8]      # stabil: bei gleichem Score neuere zuerst
        neueste = [m for m in jung if m not in oben][:4]
        v["nachrichten"] = {"gesamt": len(meldungen), "lager": dict(lager), "von": min(zeiten) if zeiten else "", "bis": ende,
                            "hoch": sum(_zahl(m.get("risk_score")) >= 5 for m in meldungen),
                            "wichtig": [eintrag(m) for m in oben], "neueste": [eintrag(m) for m in neueste],
                            "_ids": sorted(str(m.get("id")) for m in meldungen),
                            "_hoch_ids": sorted(str(m.get("id")) for m in meldungen if _zahl(m.get("risk_score")) >= 5)}

    # Engpässe und Häfen (Feed /api/maritime)
    if "schifffahrt" in f:
        s = f["schifffahrt"]
        v["schifffahrt"] = {
            "engpaesse": [{"name": c.get("name"), "risiko": c.get("risk"), "verkehr": c.get("traffic")} for c in _liste(s, "chokepoints")],
            "haefen_gesamt": len(_liste(s, "ports")),
            "haefen_auffaellig": [{"name": p.get("name"), "stau": p.get("congestion"), "wartezeit": p.get("dwell_time")}
                                  for p in _liste(s, "ports") if str(p.get("congestion") or "NORMAL").upper() != "NORMAL"],
            "schiffe_live": s.get("total_ships")}

    # Märkte (Feed /api/markets)
    if "maerkte" in f:
        gruppen: dict[str, list] = {}
        for gruppe in ("indices", "stocks", "oil", "commodities", "crypto", "fx"):
            for name, q in (f["maerkte"].get(gruppe) or {}).items():
                if isinstance(q, dict) and isinstance(q.get("price"), (int, float)):
                    gruppen.setdefault(gruppe, []).append({"name": name, "preis": q["price"], "aenderung": q.get("change_percent"),
                                                           "offen": q.get("market_open")})
        v["maerkte"] = {"gruppen": gruppen, "scm_warnungen": len(f["maerkte"].get("scm_alerts") or [])}

    # Erdbeben (USGS über OSIRIS)
    if "erdbeben" in f:
        beben = sorted(_liste(f["erdbeben"], "earthquakes"), key=lambda b: -_zahl(b.get("magnitude")))
        v["erdbeben"] = {"gesamt": len(beben), "ab_5": sum(_zahl(b.get("magnitude")) >= 5 for b in beben),
                         "staerkste": [{"magnitude": b.get("magnitude"), "ort": b.get("place"), "zeit": _ms_zu_iso(b.get("time")),
                                        "tsunami": bool(b.get("tsunami"))} for b in beben[:3]],
                         "_ids": sorted(str(b.get("id")) for b in beben if _zahl(b.get("magnitude")) >= 5)}

    # Unwetter und Naturereignisse (NASA EONET / GDACS)
    if "unwetter" in f:
        ev = _liste(f["unwetter"], "events")
        v["unwetter"] = {"gesamt": len(ev), "hoch": [_kurz(e.get("title"), 80) for e in ev if e.get("severity") == "high"][:5],
                              "hoch_gesamt": sum(e.get("severity") == "high" for e in ev)}

    # Cyber (CISA KEV)
    if "cyber" in f:
        th = _liste(f["cyber"], "threats")
        st = f["cyber"].get("stats") if isinstance(f["cyber"].get("stats"), dict) else {}
        v["cyber"] = {"stufe": st.get("threat_level"), "aktiv": st.get("active_cves"), "kritisch": sum(t.get("severity") == "CRITICAL" for t in th),
                      "neueste": [{"id": t.get("id"), "name": _kurz(t.get("name"), 90), "hersteller": t.get("vendor"), "datum": t.get("date")}
                                  for t in th[:3]], "_ids": sorted(str(t.get("id")) for t in th)}

    # Netzausfälle (IODA) und Lieferketten
    if "netzausfaelle" in f:
        aus = _liste(f["netzausfaelle"], "outages")
        v["netzausfaelle"] = {"gesamt": len(aus), "laender": sorted({str(a.get("country") or a.get("code")) for a in aus})}
    if "lieferketten" in f:
        l = f["lieferketten"]
        v["lieferketten"] = {"gesamt": len(_liste(l, "suppliers")), "kritisch": l.get("critical_count"),
                             "gestoert": len(l.get("degraded_signals") or [])}
    return v


# ------------------------------------------------------------------------------------------ Text

def _z(iso) -> str:
    """ISO-Zeit kurz als 'TT.MM. HH:MM UTC'."""
    try:
        return _parse(iso).strftime("%d.%m. %H:%M UTC")
    except AttributeError:
        return "?"


def _dauer(a: datetime, b: datetime) -> str:
    sek = abs((zu_utc(a) - zu_utc(b)).total_seconds())
    return f"{sek / 3600:.1f} h".replace(".", ",") if sek >= 5400 else f"{round(sek / 60)} min"


def _kette(zaehler: dict, n: int | None = None) -> str:
    teile = sorted(zaehler.items(), key=lambda x: -x[1])
    return ", ".join(f"{k} {w}" for k, w in (teile[:n] if n else teile))


def _text(v: dict, alt: dict | None, zeit: datetime, zeit_alt: datetime | None, adresse: str) -> str:
    z: list[str] = [f"OSIRIS-Lage, Stand {zu_utc(zeit).strftime('%Y-%m-%d %H:%M UTC')} (Quelle {adresse}; Einstufungen wie "
                    "Stufen, Risiko oder Score sind OSIRIS-eigen, nicht von uns)."]
    if v["fehlend"]:
        z.append("Nicht abrufbar in diesem Schnappschuss: " + ", ".join(v["fehlend"]) + ".")

    if "vorfaelle" in v:
        x = v["vorfaelle"]
        z.append(f"\nVorfälle (Feed „{x['quelle'] or 'gdelt'}“): {x['gesamt']} aktuelle Meldungen; nach Art: {_kette(x['arten'])}.")
        z.append("Je Gebiet (grobe Einteilung nach Koordinaten): " + "; ".join(
            f"{r} {sum(c.values())} ({_kette(c, 3)})" for r, c in sorted(x["regionen"].items(), key=lambda t: -sum(t[1].values()))) + ".")
        if x["wichtig"]:
            z.append(f"Davon mit Alarmstufe Orange/Rot laut GDACS ({len(x['wichtig'])}):")
            z += [f"  - [{w['stufe']}] {w['ort']}: {w['text']}" for w in x["wichtig"][:8]]

    if "konflikte" in v:
        x = v["konflikte"]
        krieg = [q["name"] for q in x["zonen"] if q["stufe"] == "war"]
        andere = [f"{q['name']} ({q['stufe']})" for q in x["zonen"] if q["stufe"] != "war"]
        z.append(f"\nKonfliktzonen (feste Liste von OSIRIS, Feld zoneSource „{x['quelle'] or '?'}“, {len(x['zonen'])} Zonen): Stufe „war“: {', '.join(krieg) or 'keine'}; "
                 f"weitere: {', '.join(andere) or 'keine'}. Zugeordnete aktuelle Schlagzeilen: "
                 f"{sum(q['meldungen'] or 0 for q in x['zonen'])}.")

    if "laenderrisiko" in v:
        x = v["laenderrisiko"]
        z.append(f"\nLänderrisiko laut OSIRIS (Basis: {x['basis'] or '?'}, keine geprüfte Prognose), höchste: " + "; ".join(
            f"{c['land']} {zahl(c['wert'], 0)} {c['stufe']}" for c in x["top"]) + "."
                 + (f" Börsen geöffnet: {x['boersen_offen']}." if x["boersen_offen"] is not None else "")
                 + (f" Erdbeben M4.5+ in letzter Zeit in: {', '.join(x['erdbeben'])}." if x["erdbeben"] else ""))

    if "nachrichten" in v:
        x = v["nachrichten"]
        z.append(f"\nMeldungen: {x['gesamt']} im Feed ({_z(x['von'])} bis {_z(x['bis'])}), nach Lager: {_kette(x['lager'])}. "
                 f"Score ≥ 5 (OSIRIS zählt nur Schlüsselwörter, keine Bewertung): {x['hoch']}. "
                 "Lager = von OSIRIS angegebene Ausrichtung der Quelle; die Meldungen sind Quellenangaben, nicht geprüft.")
        z.append("Höchster Score in den letzten 48 h:")
        z += [f"  - {_z(m['zeit'])} | {m['quelle']} ({m['lager']}) | Score {m['wert']} | {m['titel']}" for m in x["wichtig"]]
        if x["neueste"]:
            z.append("Neueste weitere:")
            z += [f"  - {_z(m['zeit'])} | {m['quelle']} ({m['lager']}) | {m['titel']}" for m in x["neueste"]]

    if "schifffahrt" in v:
        x = v["schifffahrt"]
        z.append(f"\nEngpässe und Seeverkehr (Risikostufe ist OSIRIS-eigen; Schiffe mit Live-Ortung im ganzen Feed: {x['schiffe_live']}):")
        z += [f"  - {c['name']}: {c['risiko']}; {c['verkehr']}" for c in x["engpaesse"]]
        z.append("Häfen mit Stau/Auffälligkeit: " + (", ".join(f"{p['name']} {p['stau']}" for p in x["haefen_auffaellig"])
                                                    or f"keine (von {x['haefen_gesamt']} Häfen alle NORMAL)") + ".")

    if "maerkte" in v:
        namen = {"indices": "Indizes", "stocks": "Rüstung/Technik", "oil": "Energie", "commodities": "Rohstoffe", "crypto": "Krypto", "fx": "Devisen"}
        z.append("\nMärkte laut OSIRIS (letzter Kurs, Tagesänderung in % laut Feed):")
        for g, liste in v["maerkte"]["gruppen"].items():
            z.append(f"  {namen[g]}: " + "; ".join(
                f"{q['name']} {zahl(q['preis'], 4 if q['preis'] < 10 else 2)}" + ("" if q["aenderung"] is None else f" ({zahl(q['aenderung'], 2)} %)")
                for q in liste))

    if "erdbeben" in v:
        x = v["erdbeben"]
        z.append(f"\nErdbeben (USGS): {x['gesamt']} aktuelle, davon M≥5: {x['ab_5']}. Stärkste: " + "; ".join(
            f"M{b['magnitude']} {b['ort']} ({_z(b['zeit'])}{', Tsunami-Flag' if b['tsunami'] else ''})" for b in x["staerkste"]) + ".")
    if "unwetter" in v:
        x = v["unwetter"]
        z.append(f"Unwetter/Naturereignisse (NASA EONET/GDACS): {x['gesamt']}" + (f"; Schwere „high“ ({x['hoch_gesamt']}), erste: {'; '.join(x['hoch'])}" if x["hoch"] else "") + ".")
    if "cyber" in v:
        x = v["cyber"]
        z.append(f"Cyber (CISA-KEV-Liste über OSIRIS): Stufe laut OSIRIS {x['stufe']}, {x['kritisch']} der {len(x['_ids'])} neuesten als CRITICAL; neueste: "
                 + "; ".join(f"{t['id']} {t['hersteller']} ({t['datum']})" for t in x["neueste"]) + ".")
    if "netzausfaelle" in v:
        x = v["netzausfaelle"]
        z.append(f"Internetausfälle (IODA): {x['gesamt']}" + (f" in {', '.join(x['laender'])}" if x["laender"] else "") + ".")
    if "lieferketten" in v:
        x = v["lieferketten"]
        z.append(f"Lieferketten (OSIRIS-Lieferantenliste): {x['gesamt']} Lieferanten, kritisch markiert: {x['kritisch']}, gestörte Signale: {x['gestoert']}.")

    # Veränderung gegenüber dem vorigen Schnappschuss
    if alt is None or zeit_alt is None:
        z.append("\nVeränderung: kein früherer Schnappschuss zum Vergleich.")
    else:
        z.append(f"\nVeränderung seit dem Schnappschuss von {zu_utc(zeit_alt).strftime('%Y-%m-%d %H:%M UTC')} (vor {_dauer(zeit, zeit_alt)}):")
        aend: list[str] = []
        if "vorfaelle" in v and "vorfaelle" in alt:
            d = v["vorfaelle"]["gesamt"] - alt["vorfaelle"]["gesamt"]
            neu_w = [w for w in v["vorfaelle"]["wichtig"] if w["schluessel"] not in {a["schluessel"] for a in alt["vorfaelle"]["wichtig"]}]
            aend.append(f"Vorfälle gesamt {d:+d} (jetzt {v['vorfaelle']['gesamt']})" + (f"; neu Orange/Rot: " + "; ".join(w["text"][:100] for w in neu_w[:4]) if neu_w else "; keine neuen Orange/Rot"))
        if "nachrichten" in v and "nachrichten" in alt:
            neu_ids = set(v["nachrichten"]["_ids"]) - set(alt["nachrichten"]["_ids"])
            neu_h = set(v["nachrichten"]["_hoch_ids"]) - set(alt["nachrichten"]["_hoch_ids"])
            aend.append(f"Meldungen: {len(neu_ids)} neu im Feed, davon {len(neu_h)} mit Score ≥ 5")
        if "konflikte" in v and "konflikte" in alt:
            vorher = {q["id"]: q["stufe"] for q in alt["konflikte"]["zonen"]}
            for q in v["konflikte"]["zonen"]:
                if q["id"] in vorher and vorher[q["id"]] != q["stufe"]:
                    aend.append(f"Konfliktzone {q['name']}: Stufe {vorher[q['id']]} → {q['stufe']}")
        if "schifffahrt" in v and "schifffahrt" in alt:
            vorher = {c["name"]: c["risiko"] for c in alt["schifffahrt"]["engpaesse"]}
            for c in v["schifffahrt"]["engpaesse"]:
                if c["name"] in vorher and vorher[c["name"]] != c["risiko"]:
                    aend.append(f"Engpass {c['name']}: {vorher[c['name']]} → {c['risiko']}")
            vorher = {p["name"]: p["stau"] for p in alt["schifffahrt"]["haefen_auffaellig"]}
            for p in v["schifffahrt"]["haefen_auffaellig"]:
                if vorher.get(p["name"]) != p["stau"]:
                    aend.append(f"Hafen {p['name']}: {vorher.get(p['name'], 'NORMAL')} → {p['stau']}")
        if "maerkte" in v and "maerkte" in alt:
            vorher = {q["name"]: q["preis"] for g in alt["maerkte"]["gruppen"].values() for q in g}
            bew = []
            for g in v["maerkte"]["gruppen"].values():
                for q in g:
                    p0 = vorher.get(q["name"])
                    if p0 and abs(q["preis"] / p0 - 1) * 100 >= 0.5:
                        bew.append(f"{q['name']} {zahl((q['preis'] / p0 - 1) * 100, 2)} %")
            aend.append("Märkte seit dem letzten Schnappschuss (≥ 0,5 %): " + (", ".join(bew) if bew else "keine"))
        if "erdbeben" in v and "erdbeben" in alt:
            neu_b = set(v["erdbeben"]["_ids"]) - set(alt["erdbeben"]["_ids"])
            aend.append(f"Erdbeben M≥5 neu: {len(neu_b)}")
        if "cyber" in v and "cyber" in alt:
            aend.append(f"Neue Einträge in der Cyber-Liste: {len(set(v['cyber']['_ids']) - set(alt['cyber']['_ids']))}")
        z += ["  - " + a for a in aend] or ["  - nichts Vergleichbares"]
    return "\n".join(z)
