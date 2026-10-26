"""Excel-Dateien ohne Zusatzpakete lesen: .xlsx (Zip mit XML) und altes .xls (OLE2 mit BIFF8).

Gelesen werden nur Zellwerte (Zahlen und Texte), keine Formate. Datumszellen kommen als Excel-Zahl
(Tage seit 1899-12-30); die Umrechnung macht der jeweilige Lader, weil jede Quelle ihr Datum anders ablegt.
Ergebnis je Blatt: {(zeile, spalte): wert}, beide ab 0 gezählt.
"""
from __future__ import annotations

import io
import re
import struct
import zipfile
import xml.etree.ElementTree as ET

Blatt = dict[tuple[int, int], object]

# --------------------------------------------------------------------------------------------- xlsx

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _spalte_nr(buchstaben: str) -> int:
    n = 0
    for b in buchstaben:
        n = n * 26 + ord(b) - 64
    return n - 1


def xlsx_blaetter(daten: bytes) -> list[str]:
    z = zipfile.ZipFile(io.BytesIO(daten))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    return [s.get("name") for s in wb.iter(_NS + "sheet")]


def xlsx_blatt(daten: bytes, name_beginnt: str) -> Blatt:
    """Das erste Blatt, dessen Name mit name_beginnt anfängt."""
    z = zipfile.ZipFile(io.BytesIO(daten))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rid = next((s.get(_REL) for s in wb.iter(_NS + "sheet") if s.get("name", "").startswith(name_beginnt)), None)
    if rid is None:
        raise LookupError(f"Blatt „{name_beginnt}…“ nicht gefunden")
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    ziel = next(r.get("Target") for r in rels if r.get("Id") == rid)
    pfad = ziel.lstrip("/") if ziel.startswith("/") else "xl/" + ziel
    texte = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_NS + "si"):
            texte.append("".join(t.text or "" for t in si.iter(_NS + "t")))
    blatt: Blatt = {}
    for _, el in ET.iterparse(z.open(pfad)):
        if el.tag != _NS + "c":
            continue
        ref, typ = el.get("r", ""), el.get("t")
        m = re.match(r"([A-Z]+)(\d+)", ref)
        v = el.find(_NS + "v")
        if m and (v is not None or typ == "inlineStr"):
            if typ == "s":
                wert = texte[int(v.text)]
            elif typ == "inlineStr":
                wert = "".join(t.text or "" for t in el.iter(_NS + "t"))
            elif typ in ("str", "e"):
                wert = v.text or ""
            elif typ == "b":
                wert = v.text == "1"
            else:
                try:
                    wert = float(v.text)
                except (TypeError, ValueError):
                    wert = v.text
            blatt[(int(m.group(2)) - 1, _spalte_nr(m.group(1)))] = wert
        el.clear()
    return blatt


# ---------------------------------------------------------------------------------------------- xls

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENDE = 0xFFFFFFFE


def _ole_strom(daten: bytes, name: str) -> bytes:
    """Liest einen Datenstrom (z. B. „Workbook“) aus einer OLE2-Datei."""
    if daten[:8] != _OLE:
        raise ValueError("keine alte Excel-Datei (OLE2-Kennung fehlt)")
    ssz = 1 << struct.unpack_from("<H", daten, 0x1E)[0]
    mssz = 1 << struct.unpack_from("<H", daten, 0x20)[0]
    n_fat, erstes_dir = struct.unpack_from("<II", daten, 0x2C)
    grenze, erstes_mfat, n_mfat, erstes_difat, n_difat = struct.unpack_from("<IIIII", daten, 0x38)

    def sektor(n: int) -> bytes:
        return daten[(n + 1) * ssz:(n + 2) * ssz]

    difat = list(struct.unpack_from("<109I", daten, 0x4C))
    s = erstes_difat
    for _ in range(n_difat):
        werte = struct.unpack(f"<{ssz // 4}I", sektor(s))
        difat.extend(werte[:-1])
        s = werte[-1]
    fat: list[int] = []
    for s in difat[:n_fat]:
        fat.extend(struct.unpack(f"<{ssz // 4}I", sektor(s)))

    def kette(start: int, fat_: list[int], lesen) -> bytes:
        teile, s, schritte = [], start, 0
        while s < 0xFFFFFFFA and schritte <= len(fat_):
            teile.append(lesen(s))
            s, schritte = fat_[s], schritte + 1
        return b"".join(teile)

    verzeichnis = kette(erstes_dir, fat, sektor)
    eintraege = []
    for i in range(0, len(verzeichnis), 128):
        e = verzeichnis[i:i + 128]
        laenge = struct.unpack_from("<H", e, 64)[0]
        typ, start, groesse = e[66], struct.unpack_from("<I", e, 116)[0], struct.unpack_from("<I", e, 120)[0]
        eintraege.append((e[:max(laenge - 2, 0)].decode("utf-16-le", errors="replace"), typ, start, groesse))
    wurzel = next(e for e in eintraege if e[1] == 5)
    for n, typ, start, groesse in eintraege:
        if typ == 2 and n.lower() == name.lower():
            if groesse >= grenze:
                return kette(start, fat, sektor)[:groesse]
            mini = kette(wurzel[2], fat, sektor)                    # kleine Ströme liegen im Mini-Strom der Wurzel
            mfat: list[int] = []
            if n_mfat:
                roh = kette(erstes_mfat, fat, sektor)
                mfat = list(struct.unpack(f"<{len(roh) // 4}I", roh))
            return kette(start, mfat, lambda k: mini[k * mssz:(k + 1) * mssz])[:groesse]
    raise LookupError(f"Strom „{name}“ nicht gefunden")


def _rk(rk: int) -> float:
    if rk & 2:
        v = float(rk >> 2 if rk < 0x80000000 else (rk >> 2) - (1 << 30))
    else:
        v = struct.unpack("<d", struct.pack("<II", 0, rk & 0xFFFFFFFC))[0]
    return v / 100 if rk & 1 else v


class _Teile:
    """Liest Daten, die über SST- und CONTINUE-Datensätze verteilt sind."""

    def __init__(self, teile: list[bytes]):
        self.teile, self.i, self.pos = teile, 0, 0

    def _weiter(self) -> None:
        if self.pos >= len(self.teile[self.i]):
            self.i, self.pos = self.i + 1, 0

    def bytes_(self, n: int) -> bytes:
        out = b""
        while n > 0:
            self._weiter()
            t = self.teile[self.i][self.pos:self.pos + n]
            out, self.pos, n = out + t, self.pos + len(t), n - len(t)
        return out

    def zeichen(self, anzahl: int, breit: bool) -> str:
        out = []
        while anzahl > 0:
            if self.pos >= len(self.teile[self.i]):           # Wechsel in einen CONTINUE-Satz: neues Kennbyte
                self.i, self.pos = self.i + 1, 0
                breit = bool(self.teile[self.i][0] & 1)
                self.pos = 1
            breite = 2 if breit else 1
            frei = (len(self.teile[self.i]) - self.pos) // breite
            k = min(anzahl, frei)
            roh = self.teile[self.i][self.pos:self.pos + k * breite]
            out.append(roh.decode("utf-16-le" if breit else "latin-1"))
            self.pos, anzahl = self.pos + k * breite, anzahl - k
        return "".join(out)


def _sst(teile: list[bytes]) -> list[str]:
    r = _Teile(teile)
    _, eindeutig = struct.unpack("<II", r.bytes_(8))
    texte = []
    for _ in range(eindeutig):
        anzahl, kenn = struct.unpack("<HB", r.bytes_(3))
        laeufe = struct.unpack("<H", r.bytes_(2))[0] if kenn & 8 else 0
        zusatz = struct.unpack("<I", r.bytes_(4))[0] if kenn & 4 else 0
        texte.append(r.zeichen(anzahl, bool(kenn & 1)))
        r.bytes_(4 * laeufe + zusatz)
    return texte


def _saetze(strom: bytes, ab: int = 0):
    p = ab
    while p + 4 <= len(strom):
        typ, laenge = struct.unpack_from("<HH", strom, p)
        yield p, typ, strom[p + 4:p + 4 + laenge]
        p += 4 + laenge


def xls_lesen(daten: bytes) -> dict[str, Blatt]:
    """Alle Arbeitsblätter einer .xls-Datei (BIFF8, Excel 97 bis 2003)."""
    strom = _ole_strom(daten, "Workbook") if daten[:8] == _OLE else daten
    blaetter_def, sst_teile, in_sst = [], [], False
    for p, typ, d in _saetze(strom):
        if typ == 0x0085:                                        # BOUNDSHEET: Name und Lage des Blatts
            ab, art = struct.unpack_from("<I", d)[0], d[5]
            n, kenn = d[6], d[7]
            name = d[8:8 + n * 2].decode("utf-16-le") if kenn & 1 else d[8:8 + n].decode("latin-1")
            if art == 0:
                blaetter_def.append((name, ab))
        elif typ == 0x00FC:
            sst_teile, in_sst = [d], True
        elif typ == 0x003C and in_sst:
            sst_teile.append(d)
        else:
            in_sst = False
        if typ == 0x000A:                                        # EOF des Arbeitsmappen-Teils
            break
    texte = _sst(sst_teile) if sst_teile else []
    out: dict[str, Blatt] = {}
    for name, ab in blaetter_def:
        blatt: Blatt = {}
        for _, typ, d in _saetze(strom, ab):
            if typ == 0x0203:                                    # NUMBER
                z, s = struct.unpack_from("<HH", d)
                blatt[(z, s)] = struct.unpack_from("<d", d, 6)[0]
            elif typ == 0x027E:                                  # RK
                z, s = struct.unpack_from("<HH", d)
                blatt[(z, s)] = _rk(struct.unpack_from("<I", d, 6)[0])
            elif typ == 0x00BD:                                  # MULRK: mehrere RK in einer Zeile
                z, s = struct.unpack_from("<HH", d)
                for k in range((len(d) - 6) // 6):
                    blatt[(z, s + k)] = _rk(struct.unpack_from("<I", d, 4 + 6 * k + 2)[0])
            elif typ == 0x00FD:                                  # LABELSST
                z, s = struct.unpack_from("<HH", d)
                blatt[(z, s)] = texte[struct.unpack_from("<I", d, 6)[0]]
            elif typ == 0x0006:                                  # FORMULA: nur gespeichertes Zahlergebnis
                z, s = struct.unpack_from("<HH", d)
                if d[12:14] != b"\xff\xff":
                    blatt[(z, s)] = struct.unpack_from("<d", d, 6)[0]
            elif typ == 0x0204:                                  # LABEL (selten in BIFF8)
                z, s = struct.unpack_from("<HH", d)
                n, kenn = struct.unpack_from("<HB", d, 6)
                blatt[(z, s)] = d[9:9 + 2 * n].decode("utf-16-le") if kenn & 1 else d[9:9 + n].decode("latin-1")
            elif typ == 0x000A:
                break
        out[name] = blatt
    return out
