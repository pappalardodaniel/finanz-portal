"""Abruf der Reihen: reine Parser je Quelle (Bytes -> {reihe_id: [(datum_iso, wert), …]}) und laden().

Die Parser kennen kein Netz und sind einzeln testbar. laden() holt je Quelle einmal, schreibt in den Speicher,
sammelt Fehler je Quelle und macht mit den übrigen weiter.
"""
from __future__ import annotations

import html
import http.client
import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import netz, tabellen, zeit
from .reihen import REIHEN, Reihe
from .speicher import Speicher

SHILLER_START_URL = "https://shillerdata.com/"
SHILLER_ERSATZ_URL = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"
MW_URL = ("https://www.measuringworth.com/datasets/DJA/export.php?monthStartD=2&dayStartD=16&yearStartD=1885"
          "&monthEndD={m}&dayEndD={t}&yearEndD={j}")
BOE_URL = ("https://www.bankofengland.co.uk/-/media/boe/files/statistics/research-datasets/"
           "a-millennium-of-macroeconomic-data-for-the-uk.xlsx")
BLOCKCHAIN_URL = "https://api.blockchain.info/charts/market-price?timespan=all&format=json&sampled=false"
COINMETRICS_URL = ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=PriceUSD"
                   "&frequency=1d&start_time=2010-07-01&page_size=10000&paging_from=start")
COINMETRICS_MAX_SEITEN = 20
WELTBANK_SEITE = "https://www.worldbank.org/en/research/commodity-markets"
WELTBANK_DATEI = "CMO-Historical-Data-Monthly.xlsx"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}"
STOOQ_URL = "https://stooq.com/q/d/l/?s={feld}&i=d&apikey={schluessel}"
STOOQ_SCHLUESSEL_SEITE = "https://stooq.com/q/d/?s=spy.us&get_apikey"
STOOQ_SCHLUESSEL_DATEI = "~/.config/kitrader/stooq.env"
STOOQ_ANLEITUNG = (f"Stooq verlangt seit März 2026 einen kostenlosen API-Schlüssel. So kommst du an ihn: Seite {STOOQ_SCHLUESSEL_SEITE} "
                   f"im Browser öffnen, den Anweisungen dort folgen und den Schlüssel kopieren. Danach entweder "
                   f"`export STOOQ_APIKEY=DEIN_SCHLÜSSEL` setzen oder in die Datei {STOOQ_SCHLUESSEL_DATEI} die Zeile "
                   f"STOOQ_APIKEY=DEIN_SCHLÜSSEL schreiben (Datei nicht ins Repo legen).")
MAX_FOLGE = 3                    # so viele Abrufe in Folge dürfen scheitern, dann wird die Quelle aufgegeben


class QuellFehler(Exception):
    """Die Quelle lieferte nicht, was erwartet wurde (anderes Layout, kein CSV, Sperre …)."""


# ---------------------------------------------------------------------------------------------- Parser

def _zahl(x) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x) if x == x else None


def _norm(x) -> str:
    return " ".join(x.split()).casefold() if isinstance(x, str) else ""


_SHILLER_SPALTE = {"P": 1, "D": 2, "E": 3, "CPI": 4, "GS10": 6, "Real Total Return Price": 9, "CAPE": 12}


def parse_shiller(daten: bytes) -> dict[str, list]:
    """Shiller ie_data.xls, Blatt „Data“. Datum 1871.01 = Januar 1871, 2026.1 = Oktober (Nachkommastellen = Monat)."""
    blaetter = tabellen.xls_lesen(daten)
    name = next((n for n in blaetter if n.casefold().startswith("data")), None)
    if name is None:
        raise QuellFehler("Shiller: Layout geändert (Blatt „Data“ fehlt)")
    b = blaetter[name]
    kopf = {(7, 0): "date", (7, 1): "p", (7, 2): "d", (7, 3): "e", (7, 4): "cpi", (7, 12): "cape"}
    for ort, soll in kopf.items():
        if _norm(b.get(ort)) != soll:
            raise QuellFehler(f"Shiller: Layout geändert (Zelle Zeile {ort[0] + 1}, Spalte {ort[1] + 1} ist "
                              f"{b.get(ort)!r}, erwartet {soll!r})")
    if "gs10" not in _norm(b.get((7, 6))):
        raise QuellFehler("Shiller: Layout geändert (Spalte 7 ist nicht GS10)")
    if " ".join(_norm(b.get((r, 9))) for r in range(4, 8)) != "real total return price":
        raise QuellFehler("Shiller: Layout geändert (Spalte 10 ist nicht „Real Total Return Price“)")
    ids = {r.id: _SHILLER_SPALTE[r.feld] for r in REIHEN.values() if r.lader == "shiller"}
    aus: dict[str, list] = {i: [] for i in ids}
    letzte = max(z for z, _ in b)
    for z in range(8, letzte + 1):
        v = _zahl(b.get((z, 0)))
        if v is None:
            continue                                   # Leerzeilen und die Textzeile am Ende
        jahr = int(v)
        monat = round((v - jahr) * 100)
        if not (1800 <= jahr <= 2200 and 1 <= monat <= 12):
            continue
        datum = f"{jahr:04d}-{monat:02d}-01"
        for i, sp in ids.items():
            w = _zahl(b.get((z, sp)))                  # 'NA' und leere Zellen fallen weg
            if w is not None:
                aus[i].append((datum, w))
    if not aus["shiller_sp"]:
        raise QuellFehler("Shiller: keine Werte gefunden")
    return aus


_MW_ZEILE = re.compile(r'^\s*"?(\d{1,2})/(\d{1,2})/(\d{4})"?\s*[,;]\s*"?(-?\d+(?:\.\d+)?)"?\s*$')


def parse_measuringworth(daten: bytes) -> dict[str, list]:
    """MeasuringWorth-Export: Hinweiszeilen, Zitierzeile, dann "M/T/JJJJ","Wert"."""
    text = daten.decode("utf-8-sig", errors="replace")
    zeilen = []
    for z in text.splitlines():
        m = _MW_ZEILE.match(z)
        if m:
            mo, t, j, w = m.groups()
            try:
                zeilen.append((zeit.tag(f"{int(j):04d}-{int(mo):02d}-{int(t):02d}").isoformat(), float(w)))
            except ValueError:
                continue
    if not zeilen:
        raise QuellFehler(f"MeasuringWorth: keine Datenzeilen, Antwort beginnt mit {text[:80]!r}")
    return {"dow": zeilen}


_MONATE = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def parse_boe_aktien(daten: bytes) -> list:
    """Blatt M13, Spalte X (Index 23): verketteter Aktienindex. Das Jahr steht nicht in jeder Zeile."""
    b = tabellen.xlsx_blatt(daten, "M13")
    out, jahr = [], None
    for z in range(4, max(z for z, _ in b) + 1):
        j = _zahl(b.get((z, 0)))
        if j is not None:
            jahr = int(j)
        mon = b.get((z, 1))
        w = _zahl(b.get((z, 23)))
        if jahr is None or w is None or not isinstance(mon, str) or mon.strip()[:3].casefold() not in _MONATE:
            continue
        out.append((f"{jahr:04d}-{_MONATE.index(mon.strip()[:3].casefold()) + 1:02d}-01", w))
    if not out:
        raise QuellFehler("Bank of England: Blatt M13 ohne Werte in Spalte X (Layout geändert?)")
    return out


def parse_boe_zins(daten: bytes) -> list:
    """Blatt D1, Spalte 0 Datum (Text TT/MM/JJJJ oder Excel-Zahl), Spalte 2 Zinssatz; Kopf vor Zeile 8."""
    b = tabellen.xlsx_blatt(daten, "D1.")
    out = []
    for z in range(8, max(z for z, _ in b) + 1):
        w = _zahl(b.get((z, 2)))
        d = b.get((z, 0))
        if w is None or d is None:
            continue
        try:
            if isinstance(d, str):
                m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", d)
                if not m:
                    continue
                iso = zeit.tag(f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}").isoformat()
            else:
                iso = zeit.excel_datum(_zahl(d))
        except (ValueError, TypeError, OverflowError):
            continue
        out.append((iso, w))
    if not out:
        raise QuellFehler("Bank of England: Blatt D1 ohne Zinswerte (Layout geändert?)")
    return out


def parse_boe(daten: bytes) -> dict[str, list]:
    return {"uk_aktien": parse_boe_aktien(daten), "uk_leitzins": parse_boe_zins(daten)}


def parse_blockchain(daten: bytes) -> dict[str, list]:
    """Blockchain.com: {"values": [{"x": Unix-Sekunden, "y": Preis}, …]}; Wert 0 heißt kein Preis."""
    try:
        werte = json.loads(daten)["values"]
    except (ValueError, KeyError, TypeError) as e:
        raise QuellFehler(f"Blockchain.com: keine gültige Antwort ({e}); Anfang {daten[:80]!r}")
    out = []
    for p in werte:
        x, y = _zahl(p.get("x")), _zahl(p.get("y"))
        if x is None or y is None or y <= 0:
            continue
        out.append((datetime.fromtimestamp(x, timezone.utc).date().isoformat(), y))
    if not out:
        raise QuellFehler("Blockchain.com: keine Preise in der Antwort")
    return {"btc_blockchain": out}


def _iso_tag(zeitstempel) -> str | None:
    """'2010-07-18T00:00:00.000000000Z' -> '2010-07-18' (nur wenn es wirklich ein Datum ist)."""
    t = zeitstempel[:10] if isinstance(zeitstempel, str) else ""
    return t if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t) else None


def parse_coinmetrics_seite(daten: bytes) -> tuple[list, str | None]:
    """Coin Metrics, eine Seite: {"data": [{"time": …, "PriceUSD": "0.08584"}, …], "next_page_url": …}.

    Rückgabe: (Zeilen, URL der nächsten Seite oder None). Fehlende oder nicht positive Preise werden übersprungen.
    """
    try:
        antwort = json.loads(daten)
        zeilen = antwort["data"]
        weiter = antwort.get("next_page_url") or None
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise QuellFehler(f"Coin Metrics: keine gültige Antwort ({e}); Anfang {daten[:80]!r}")
    out = []
    for p in zeilen:
        d = _iso_tag(p.get("time")) if isinstance(p, dict) else None
        try:
            w = float(p.get("PriceUSD")) if d else None
        except (TypeError, ValueError):
            w = None
        if d and w is not None and w == w and w > 0:
            out.append((d, w))
    return out, weiter


def parse_coinmetrics(daten: bytes) -> dict[str, list]:
    out, _ = parse_coinmetrics_seite(daten)
    if not out:
        raise QuellFehler("Coin Metrics: keine Preise in der Antwort")
    return {"btc": out}


_WB_MONAT = re.compile(r"^(\d{4})M(\d{2})$")
_WB_SPALTEN = {"gold": "gold_monat", "silver": "silber_monat", "platinum": "platin_monat"}


def parse_weltbank(daten: bytes) -> dict[str, list]:
    """Weltbank Pink Sheet, Blatt „Monthly Prices“: Kopf (Zeile 5) mit Rohstoffnamen, darunter die Einheit, Datum „1960M01“.

    Gold, Silber (Silver) und Platin (Platinum) werden über den Kopf gefunden und müssen „($/troy oz)“ sein.
    Zellen mit „…“ oder anderem Text bedeuten: kein Wert, und werden übersprungen. Datum = 1. des Monats.
    """
    try:
        b = tabellen.xlsx_blatt(daten, "Monthly Prices")
    except LookupError:
        raise QuellFehler("Weltbank: Layout geändert (Blatt „Monthly Prices“ fehlt)")
    except Exception as e:
        raise QuellFehler(f"Weltbank: Datei nicht lesbar ({fehlertext(e)})")
    zeilen = sorted({z for z, _ in b})
    breite = max((s for _, s in b), default=0) + 1
    kopf_zeile = next((z for z in zeilen[:15] if sum(_norm(b.get((z, s))) in _WB_SPALTEN for s in range(breite)) == 3), None)
    if kopf_zeile is None:
        raise QuellFehler("Weltbank: Layout geändert (Kopfzeile mit Gold, Silver und Platinum nicht gefunden)")
    spalte = {}
    for s in range(breite):
        name = _norm(b.get((kopf_zeile, s)))
        if name in _WB_SPALTEN:
            einheit = _norm(b.get((kopf_zeile + 1, s)))
            if "troy oz" not in einheit:
                raise QuellFehler(f"Weltbank: Einheit von {name} ist {b.get((kopf_zeile + 1, s))!r}, erwartet „($/troy oz)“")
            spalte[_WB_SPALTEN[name]] = s
    aus: dict[str, list] = {i: [] for i in spalte}
    for z in zeilen:
        m = _WB_MONAT.match(str(b.get((z, 0), "")).strip())
        if not m or not 1 <= int(m.group(2)) <= 12:
            continue
        datum = f"{m.group(1)}-{m.group(2)}-01"
        for i, s in spalte.items():
            w = _zahl(b.get((z, s)))
            if w is not None and w > 0:
                aus[i].append((datum, w))
    fehlend = [i for i, v in aus.items() if not v]
    if fehlend:
        raise QuellFehler(f"Weltbank: keine Werte für {', '.join(fehlend)}")
    return aus


def parse_fred(daten: bytes, feld: str) -> list:
    """FRED fredgraph.csv: Kopf observation_date,ID (früher DATE,ID); '.' = fehlt."""
    zeilen = None
    text = daten.decode("utf-8-sig", errors="replace")
    for kopf in ("observation_date", "date"):
        if text.casefold().lstrip().startswith(kopf):
            zeilen = [z for z in text.splitlines() if z.strip()]
    if zeilen is None:
        raise QuellFehler(f"FRED {feld}: keine CSV-Antwort, beginnt mit {text[:80]!r}")
    spalten = zeilen[0].split(",")
    if len(spalten) < 2 or spalten[1].strip().upper() != feld.upper():
        raise QuellFehler(f"FRED {feld}: Kopfzeile {zeilen[0]!r} passt nicht")
    out = []
    for z in zeilen[1:]:
        t = z.split(",")
        if len(t) < 2 or t[1].strip() in ("", ".") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", t[0].strip()):
            continue
        try:
            out.append((t[0].strip(), float(t[1])))
        except ValueError:
            continue
    if not out:
        raise QuellFehler(f"FRED {feld}: keine Werte")
    return out


def parse_stooq(daten: bytes, feld: str = "") -> list:
    """Stooq-CSV Date,Open,High,Low,Close[,Volume]. Alles andere (No data, Tageslimit, HTML) wird klar gemeldet."""
    text = daten.decode("utf-8-sig", errors="replace").strip()
    zeilen = text.splitlines()
    if not zeilen or "," not in zeilen[0] or not zeilen[0].casefold().startswith(("date", "datum")):
        if re.search(r"api[ _-]?key", text, re.IGNORECASE):
            raise QuellFehler(f"Stooq {feld}: Stooq verlangt oder lehnt den API-Schlüssel ab. {STOOQ_ANLEITUNG}")
        raise QuellFehler(f"Stooq {feld}: keine CSV-Antwort, sondern {text[:100]!r}")
    kopf = [k.strip().casefold() for k in zeilen[0].split(",")]
    sp = kopf.index("close") if "close" in kopf else (kopf.index("zamkniecie") if "zamkniecie" in kopf else 4)
    out = []
    for z in zeilen[1:]:
        t = z.split(",")
        if len(t) <= sp or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", t[0].strip()):
            continue
        try:
            w = float(t[sp])
        except ValueError:
            continue
        if w > 0:
            out.append((t[0].strip(), w))
    if not out:
        raise QuellFehler(f"Stooq {feld}: CSV ohne Kurse")
    return out


# ---------------------------------------------------------------------------------------------- Abruf

@dataclass
class Ergebnis:
    reihe: str
    status: str            # geladen | geschützt | Fehler | von Hand
    anzahl: int = 0
    erstes: str = ""
    letztes: str = ""
    meldung: str = ""


@dataclass
class _Abruf:
    holen: object
    melden: object
    pause: float
    heute: object


@dataclass
class _Lieferung:
    zeilen: dict
    fehler: dict
    warnung: str = ""
    urls: dict | None = None


def _ohne_schluessel(text: str) -> str:
    return re.sub(r"(?<=[?&])(api[_-]?key=)[^&\s'\"]+", r"\1***", text, flags=re.IGNORECASE)


def fehlertext(e: BaseException) -> str:
    """Kurzer Fehlertext; ein API-Schlüssel in einer URL wird nie mit ausgegeben."""
    if isinstance(e, QuellFehler):
        return _ohne_schluessel(str(e))
    if hasattr(e, "code") and hasattr(e, "url"):
        return _ohne_schluessel(f"HTTP {e.code} bei {e.url}")
    return _ohne_schluessel(f"{type(e).__name__}: {e}")


def _shiller(reihen, a: _Abruf) -> _Lieferung:
    warnung, url = "", ""
    try:
        seite = a.holen(SHILLER_START_URL).decode("utf-8", errors="replace")
        m = re.search(r'href="([^"]*ie_data\.xls[^"]*)"', seite)
        if not m:
            raise QuellFehler("Link zu ie_data.xls auf shillerdata.com nicht gefunden")
        url = html.unescape(m.group(1))
        url = "https:" + url if url.startswith("//") else url
        a.melden(f"Shiller: lade {url.split('?')[0]}")
        zeilen = parse_shiller(a.holen(url))
    except Exception as e:
        a.melden("Shiller: Hauptquelle fehlgeschlagen, versuche die veraltete Yale-Datei")
        url = SHILLER_ERSATZ_URL
        try:
            zeilen = parse_shiller(a.holen(url))
        except Exception as e2:
            raise QuellFehler(f"shillerdata.com: {fehlertext(e)}; Ersatz Yale: {fehlertext(e2)}")
        warnung = (f"Ersatzquelle Yale (veraltet, endet etwa 2023-09), weil shillerdata.com versagte: {fehlertext(e)}")
    return _Lieferung(zeilen, {}, warnung, {i: url for i in zeilen})


def _measuringworth(reihen, a: _Abruf) -> _Lieferung:
    h = a.heute
    url = MW_URL.format(m=h.month, t=h.day, j=h.year)
    a.melden("MeasuringWorth: lade Dow Jones täglich")
    return _Lieferung(parse_measuringworth(a.holen(url)), {}, "", {"dow": url})


def _boe(reihen, a: _Abruf) -> _Lieferung:
    ids = {r.id for r in reihen}
    a.melden("Bank of England: lade Datei (rund 27 MB)")
    daten = a.holen(BOE_URL)
    zeilen, fehler = {}, {}
    for i, f in (("uk_aktien", parse_boe_aktien), ("uk_leitzins", parse_boe_zins)):
        if i in ids:
            try:
                zeilen[i] = f(daten)
            except Exception as e:
                fehler[i] = fehlertext(e)
    return _Lieferung(zeilen, fehler, "", {i: BOE_URL for i in ids})


def _blockchain(reihen, a: _Abruf) -> _Lieferung:
    a.melden("Blockchain.com: lade Bitcoin-Preis (Ersatzquelle)")
    return _Lieferung(parse_blockchain(a.holen(BLOCKCHAIN_URL)), {}, "", {"btc_blockchain": BLOCKCHAIN_URL})


def _coinmetrics(reihen, a: _Abruf) -> _Lieferung:
    """Coin Metrics Community API: eine Seite reicht (5900+ Tage bei page_size 10000); next_page_url wird sicherheitshalber verfolgt."""
    url, zeilen, seiten = COINMETRICS_URL, [], 0
    while url:
        seiten += 1
        if seiten > COINMETRICS_MAX_SEITEN:
            raise QuellFehler(f"Coin Metrics: mehr als {COINMETRICS_MAX_SEITEN} Seiten, Abbruch")
        a.melden("Coin Metrics: lade Bitcoin-Preis" + (f" (Seite {seiten})" if seiten > 1 else ""))
        teil, url = parse_coinmetrics_seite(a.holen(url))
        zeilen += teil
    if not zeilen:
        raise QuellFehler("Coin Metrics: keine Preise in der Antwort")
    return _Lieferung({"btc": zeilen}, {}, "", {"btc": COINMETRICS_URL})


def _weltbank(reihen, a: _Abruf) -> _Lieferung:
    """Die Datei hat einen wechselnden Hash im Link; er wird jedes Mal von der Seite gelesen."""
    a.melden("Weltbank: suche Link zur Pink-Sheet-Datei")
    seite = a.holen(WELTBANK_SEITE).decode("utf-8", errors="replace")
    m = re.search(r'href="([^"]*' + re.escape(WELTBANK_DATEI) + r')"', seite)
    if not m:
        raise QuellFehler(f"Weltbank: Link zu {WELTBANK_DATEI} auf {WELTBANK_SEITE} nicht gefunden")
    url = urllib.parse.urljoin(WELTBANK_SEITE, html.unescape(m.group(1)))
    a.melden(f"Weltbank: lade {url}")
    zeilen = parse_weltbank(a.holen(url))
    return _Lieferung(zeilen, {}, "", {i: url for i in zeilen})


def stooq_schluessel(umgebung=None, datei: str | Path | None = None) -> str:
    """API-Schlüssel für Stooq: Umgebungsvariable STOOQ_APIKEY, sonst Zeile STOOQ_APIKEY=… in ~/.config/kitrader/stooq.env.

    Die Datei darf auch nur den Schlüssel enthalten; Kommentarzeilen (#), Anführungszeichen und „export “ werden toleriert.
    Leer, wenn keiner da ist.
    """
    wert = ((os.environ if umgebung is None else umgebung).get("STOOQ_APIKEY") or "").strip()
    if wert:
        return wert
    try:
        text = Path(datei or STOOQ_SCHLUESSEL_DATEI).expanduser().read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#"):
            continue
        if zeile.startswith("export "):
            zeile = zeile[7:].strip()
        name, gleich, rest = zeile.partition("=")
        if gleich and name.strip().upper() == "STOOQ_APIKEY":
            wert = rest.strip().strip("'\"").strip()
        elif not gleich:
            wert = zeile.strip("'\"").strip()
        if wert:
            return wert
    return ""


def _nacheinander(reihen, a: _Abruf, url_vorlage: str, parser, name: str, schluessel: str = "") -> _Lieferung:
    """Ein Abruf je Reihe, mit Pause dazwischen (FRED weist parallele Abrufe ab). Nach MAX_FOLGE Fehlern in Folge Abbruch.

    Ein Schlüssel kommt nur in die Abruf-URL; gespeichert und gemeldet wird die URL mit „***“ statt des Schlüssels.
    """
    zeilen, fehler, urls, folge = {}, {}, {}, 0
    for i, r in enumerate(reihen):
        if folge >= MAX_FOLGE:
            fehler[r.id] = f"nicht versucht, weil {MAX_FOLGE} Abrufe von {name} in Folge scheiterten"
            continue
        if i:
            time.sleep(a.pause)
        url = url_vorlage.format(id=r.feld, feld=r.feld, schluessel=urllib.parse.quote(schluessel, safe=""))
        zeige = url_vorlage.format(id=r.feld, feld=r.feld, schluessel="***")
        a.melden(f"{name}: lade {r.id} ({r.feld})")
        try:
            zeilen[r.id] = parser(a.holen(url), r.feld)
            urls[r.id], folge = zeige, 0
        except Exception as e:
            fehler[r.id], folge = fehlertext(e), folge + 1
    return _Lieferung(zeilen, fehler, "", urls)


def _fred(reihen, a: _Abruf) -> _Lieferung:
    return _nacheinander(reihen, a, FRED_URL, parse_fred, "FRED")


def _stooq(reihen, a: _Abruf) -> _Lieferung:
    schluessel = stooq_schluessel()
    if not schluessel:
        raise QuellFehler(f"Stooq: kein API-Schlüssel gefunden. {STOOQ_ANLEITUNG}")
    return _nacheinander(reihen, a, STOOQ_URL, parse_stooq, "Stooq", schluessel)


LADER = {"shiller": _shiller, "measuringworth": _measuringworth, "boe": _boe, "fred": _fred,
         "blockchain": _blockchain, "coinmetrics": _coinmetrics, "weltbank": _weltbank, "stooq": _stooq}


def laden(ids: list[str] | None = None, speicher: Speicher | None = None, holen=None, melden=None,
          pause: float = 1.5, heute=None, erzwingen: bool = False) -> list[Ergebnis]:
    """Lädt die Reihen (ohne ids: alle automatisch ladbaren) und schreibt sie in den Speicher.

    Je Quelle ein Abruf; scheitert eine Quelle, laufen die anderen weiter. Rückgabe: ein Ergebnis je Reihe.
    """
    if ids:
        unbekannt = [i for i in ids if i not in REIHEN]
        if unbekannt:
            raise ValueError(f"unbekannte Reihe(n): {', '.join(unbekannt)}. Bekannt: {', '.join(REIHEN)}")
        gewollt = list(dict.fromkeys(ids))
    else:
        gewollt = [i for i, r in REIHEN.items() if r.lader != "hand"]
    a = _Abruf(holen or netz.holen, melden or (lambda t: None), pause, heute or zeit.heute())
    eigener = speicher is None
    speicher = speicher or Speicher()
    ergebnisse: dict[str, Ergebnis] = {}
    try:
        gruppen: dict[str, list[Reihe]] = {}
        for i in gewollt:
            gruppen.setdefault(REIHEN[i].lader, []).append(REIHEN[i])
        for lader, reihen in gruppen.items():
            if lader == "hand":
                for r in reihen:
                    ergebnisse[r.id] = Ergebnis(r.id, "von Hand", meldung=f"nur von Hand: {r.hinweis}")
                continue
            try:
                lief = LADER[lader](reihen, a)
            except Exception as e:
                for r in reihen:
                    ergebnisse[r.id] = Ergebnis(r.id, "Fehler", meldung=fehlertext(e))
                a.melden(f"{lader}: FEHLER {fehlertext(e)}")
                continue
            for r in reihen:
                if r.id in lief.fehler:
                    ergebnisse[r.id] = Ergebnis(r.id, "Fehler", meldung=lief.fehler[r.id])
                    continue
                zeilen = lief.zeilen.get(r.id)
                if not zeilen:
                    ergebnisse[r.id] = Ergebnis(r.id, "Fehler", meldung="Quelle lieferte keine Werte für diese Reihe")
                    continue
                try:
                    url = (lief.urls or {}).get(r.id, r.url)
                    hinweis = " ".join(t for t in (lief.warnung, r.hinweis) if t)
                    s = speicher.schreiben(r.id, zeilen, f"{r.quelle} | {url}", hinweis, erzwingen=erzwingen)
                except Exception as e:
                    ergebnisse[r.id] = Ergebnis(r.id, "Fehler", meldung=fehlertext(e))
                    continue
                if s["ersetzt"]:
                    ergebnisse[r.id] = Ergebnis(r.id, "geladen", s["anzahl"], s["erstes"], s["letztes"], lief.warnung)
                else:
                    ergebnisse[r.id] = Ergebnis(r.id, "geschützt", s["anzahl"], s["erstes"], s["letztes"],
                                                "Schutzregel: " + s["grund"])
    finally:
        if eigener:
            speicher.close()
    return [ergebnisse[i] for i in gewollt]
