"""Reihe von Hand einlesen: CSV (Trenner ; , oder Tab, Dezimalkomma), .xlsx und .xls.

Gedacht für Quellen, die automatische Abrufe nicht erlauben (Gold: World Gold Council, DAX: HU Berlin).
Die Kopfzeile wird gesucht, Datum- und Wertspalte automatisch oder per Name bzw. Nummer (1 = erste Spalte) gewählt.
Erkannte Datumsformate: JJJJ-MM-TT, JJJJ/MM/TT, TT.MM.JJJJ, M/T/JJJJ, JJJJ-MM, JJJJMmm und Excel-Seriennummern.
"""
from __future__ import annotations

import csv
import io
import pathlib
import re
from datetime import date

from . import tabellen, zeit
from .reihen import REIHEN
from .speicher import Speicher

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_LEER = {"", "-", "–", "na", "n/a", "#n/a", ".", "nd", "nan", "null", "none"}


class EinlesenFehler(Exception):
    pass


# ---------------------------------------------------------------------------------------------- Zellen

def datum_zelle(z, serie: bool = False) -> str | None:
    """Datum einer Zelle als ISO-Text oder None. serie=True erlaubt Excel-Seriennummern (Zahl zwischen 1 und 80000)."""
    if z is None or isinstance(z, bool):
        return None
    try:
        if isinstance(z, (int, float)):
            return zeit.excel_datum(z) if serie and 1 <= z <= 80000 else None
        s = str(z).strip()
        m = (re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?", s) or re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", s))
        if m:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        m = re.fullmatch(r"(\d{4})(?:-|M|/)(\d{1,2})", s)
        if m:
            return date(int(m[1]), int(m[2]), 1).isoformat()
        m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s.*)?", s)
        if m:
            return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s.*)?", s)
        if m:
            return date(int(m[3]), int(m[1]), int(m[2])).isoformat()
        if serie and re.fullmatch(r"\d{1,5}(?:[.,]0+)?", s):
            return datum_zelle(float(s.replace(",", ".")), True)
    except (ValueError, OverflowError):
        return None
    return None


def zahl_zelle(z) -> float | None:
    """Zahl aus einer Zelle; versteht 1234,56 und 1.234,56 sowie 1,234.56. Ein einzelnes Komma gilt als Dezimalkomma."""
    if isinstance(z, bool) or z is None:
        return None
    if isinstance(z, (int, float)):
        return float(z) if z == z else None
    s = str(z).strip().replace(" ", "").replace(" ", "")
    if s.casefold() in _LEER:
        return None
    if "," in s and "." in s:
        dez = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dez == "," else ",", "").replace(dez, ".")
    elif "," in s:
        s = s.replace(",", ".") if s.count(",") == 1 else s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------- Dateien

def _text_lesen(roh: bytes) -> str:
    for kodierung in ("utf-8-sig", "cp1252"):
        try:
            return roh.decode(kodierung)
        except UnicodeDecodeError:
            continue
    return roh.decode("latin-1")


def trenner_finden(text: str) -> str:
    """Trenner ; Tab oder , nach der Zahl gleich langer Zeilen; bei Gleichstand gewinnt ;, dann Tab."""
    zeilen = [z for z in text.splitlines() if z.strip()][:60]
    beste, punkte = ";", 0
    for t in (";", "\t", ","):
        felder = [len(next(csv.reader([z], delimiter=t))) for z in zeilen]
        haeufig = max(set(felder), key=felder.count) if felder else 0
        p = felder.count(haeufig) if haeufig >= 2 else 0
        if p > punkte:
            beste, punkte = t, p
    return beste


def _gitter_aus_blatt(b: dict) -> list[list]:
    if not b:
        return []
    zeilen, spalten = max(z for z, _ in b) + 1, max(s for _, s in b) + 1
    return [[b.get((z, s)) for s in range(spalten)] for z in range(zeilen)]


def tabellen_lesen(roh: bytes, endung: str, blatt: str | None) -> list[tuple[str, list[list]]]:
    """[(Blattname, Zeilen)] einer Datei; bei Excel nur Blätter, deren Name mit blatt beginnt (ohne Angabe: alle)."""
    if roh[:2] == b"PK" or endung in (".xlsx", ".xlsm"):
        namen = tabellen.xlsx_blaetter(roh)
        wahl = [n for n in namen if blatt is None or n.casefold().startswith(blatt.casefold())]
        if not wahl:
            raise EinlesenFehler(f"Blatt „{blatt}“ nicht gefunden. Vorhanden: {', '.join(namen)}")
        return [(n, _gitter_aus_blatt(tabellen.xlsx_blatt(roh, n))) for n in wahl]
    if roh[:8] == _OLE or endung == ".xls":
        alle = tabellen.xls_lesen(roh)
        wahl = [n for n in alle if blatt is None or n.casefold().startswith(blatt.casefold())]
        if not wahl:
            raise EinlesenFehler(f"Blatt „{blatt}“ nicht gefunden. Vorhanden: {', '.join(alle)}")
        return [(n, _gitter_aus_blatt(alle[n])) for n in wahl]
    text = _text_lesen(roh)
    zeilen = [[(c.strip() or None) for c in z] for z in csv.reader(io.StringIO(text), delimiter=trenner_finden(text))]
    return [("csv", zeilen)]


# ---------------------------------------------------------------------------------------------- Auswerten

def _name_suchen(zeilen: list[list], name: str, von: int, bis: int, rueckwaerts: bool) -> int | None:
    """Spaltennummer (ab 0) der Kopfzelle, die name entspricht (gleich, dann Anfang, dann enthalten)."""
    ziel = " ".join(name.split()).casefold()
    reihenfolge = range(bis - 1, von - 1, -1) if rueckwaerts else range(von, bis)
    for modus in (0, 1, 2):
        for z in reihenfolge:
            for s, c in enumerate(zeilen[z]):
                t = " ".join(c.split()).casefold() if isinstance(c, str) else ""
                if t and ((modus == 0 and t == ziel) or (modus == 1 and t.startswith(ziel)) or (modus == 2 and ziel in t)):
                    return s
    return None


def _datenstart(zeilen: list[list], spalte: int, serie: bool, ab: int = 0) -> int | None:
    """Erste Zeile ab ab, nach der in der Spalte mindestens drei Datumswerte in Folge stehen."""
    lauf = 0
    for i in range(ab, len(zeilen)):
        z = zeilen[i][spalte] if spalte < len(zeilen[i]) else None
        lauf = lauf + 1 if datum_zelle(z, serie) else 0
        if lauf >= 3:
            return i - 2
    return None


def _ganzzahlig(z) -> bool:
    return isinstance(z, (int, float)) and abs(z - round(z)) < 1e-9


def tabelle_auswerten(zeilen: list[list], datum_spalte=None, wert_spalte=None) -> dict:
    """Findet Datum- und Wertspalte und liest alle Zeilen. Rückgabe: {daten, datum_name, wert_name, start}."""
    if not zeilen:
        raise EinlesenFehler("Blatt ist leer")
    spalten = max(len(z) for z in zeilen)
    zeilen = [z + [None] * (spalten - len(z)) for z in zeilen]
    # Datumsspalte
    if datum_spalte is not None:
        if isinstance(datum_spalte, int) or str(datum_spalte).isdigit():
            dc = int(datum_spalte) - 1
            if not 0 <= dc < spalten:
                raise EinlesenFehler(f"Datum-Spalte {datum_spalte} gibt es nicht (Datei hat {spalten} Spalten)")
        else:
            dc = _name_suchen(zeilen, str(datum_spalte), 0, min(len(zeilen), 80), False)
            if dc is None:
                raise EinlesenFehler(f"Datum-Spalte „{datum_spalte}“ nicht gefunden")
        kandidaten = [dc]
    else:
        kandidaten = list(range(spalten))
    dc = start = None
    for serie in (False, True):
        for c in kandidaten:
            if serie and datum_spalte is None:
                werte = [zeilen[i][c] for i in range(len(zeilen)) if isinstance(zeilen[i][c], (int, float))]
                if not werte or not all(_ganzzahlig(w) and 5000 <= w <= 80000 for w in werte[:5]):
                    continue                                       # nur ganze Zahlen im Bereich 1913 bis 2118
            s = _datenstart(zeilen, c, serie)
            if s is not None:
                dc, start = c, s
                break
        if dc is not None:
            break
    if dc is None:
        raise EinlesenFehler("keine Datumsspalte gefunden (erwartet z. B. JJJJ-MM-TT, TT.MM.JJJJ, M/T/JJJJ, JJJJ-MM)")
    serie = datum_zelle(zeilen[start][dc], False) is None
    # Wertspalte
    if wert_spalte is not None:
        if isinstance(wert_spalte, int) or str(wert_spalte).isdigit():
            wc = int(wert_spalte) - 1
            if not 0 <= wc < spalten:
                raise EinlesenFehler(f"Wert-Spalte {wert_spalte} gibt es nicht (Datei hat {spalten} Spalten)")
        else:
            wc = _name_suchen(zeilen, str(wert_spalte), max(0, start - 40), start, True)
            if wc is None:
                raise EinlesenFehler(f"Wert-Spalte „{wert_spalte}“ über den Daten nicht gefunden")
    else:
        daten_zeilen = [z for z in zeilen[start:] if datum_zelle(z[dc], serie)]
        wc = None
        for c in range(spalten):
            if c != dc and sum(zahl_zelle(z[c]) is not None for z in daten_zeilen) >= 0.8 * len(daten_zeilen):
                wc = c
                break
        if wc is None:
            raise EinlesenFehler("keine Wertspalte mit Zahlen gefunden (bitte --wert-spalte angeben)")
    daten = {}
    for z in zeilen[start:]:
        d, w = datum_zelle(z[dc], serie), zahl_zelle(z[wc])
        if d and w is not None:
            daten[d] = w
    if not daten:
        raise EinlesenFehler("keine Zeilen mit Datum und Zahl gefunden")

    def name(c):
        kopf = zeilen[start - 1][c] if start > 0 else None
        return kopf.strip() if isinstance(kopf, str) and kopf.strip() else f"Spalte {c + 1}"
    return {"daten": sorted(daten.items()), "datum_name": name(dc), "wert_name": name(wc), "start": start}


def einlesen(reihe_id: str, pfad, datum_spalte=None, wert_spalte=None, blatt: str | None = None,
             speicher: Speicher | None = None, erzwingen: bool = False) -> dict:
    """Liest eine Datei und speichert sie als Reihe. Spalten per Name oder Nummer (ab 1). Rückgabe: Kennzahlen und Schreibergebnis."""
    if reihe_id not in REIHEN:
        raise EinlesenFehler(f"unbekannte Reihe „{reihe_id}“. Bekannt: {', '.join(REIHEN)}")
    pfad = pathlib.Path(pfad)
    roh = pfad.read_bytes()
    gefunden, fehler = None, []
    for name, zeilen in tabellen_lesen(roh, pfad.suffix.lower(), blatt):
        try:
            gefunden = (name, tabelle_auswerten(zeilen, datum_spalte, wert_spalte))
            break
        except EinlesenFehler as e:
            fehler.append(f"{name}: {e}")
    if gefunden is None:
        raise EinlesenFehler("; ".join(fehler))
    name, t = gefunden
    eigener = speicher is None
    speicher = speicher or Speicher()
    try:
        hinweis = f"von Hand eingelesen: {pfad.name}" + (f", Blatt {name}" if name != "csv" else "") + f", Spalte {t['wert_name']}"
        s = speicher.schreiben(reihe_id, t["daten"], f"Datei {pfad.name}", hinweis, erzwingen=erzwingen)
    finally:
        if eigener:
            speicher.close()
    return {"reihe": reihe_id, "blatt": name, "datum_spalte": t["datum_name"], "wert_spalte": t["wert_name"],
            "anzahl": s["anzahl"], "erstes": s["erstes"], "letztes": s["letztes"], "ersetzt": s["ersetzt"], "grund": s["grund"]}
