"""Historie-Paket: Parser, Excel-Leser, Speicher mit Schutzregel, Prüfung, Einlesen von Hand, Ereignisse, Studie.

Läuft offline und nur mit erfundenen Mini-Daten. Der Datenordner ist ein temporärer Ordner.
"""
import contextlib
import io
import json
import os
import pathlib
import statistics
import struct
import tempfile
import unittest
import urllib.error
import zipfile
from datetime import date
from unittest import mock
from xml.sax.saxutils import escape

from kitrader.historie import cli, ereignisse as ereig, laden as lad, studie as stu, tabellen, zeit
from kitrader.historie.einlesen import EinlesenFehler, einlesen
from kitrader.historie.pruefen import pruefen
from kitrader.historie.reihen import REIHEN
from kitrader.historie.speicher import Speicher, datenordner


# ------------------------------------------------------------------------------------------ Bauhilfen

def _spalte(n):
    s, n = "", n + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def baue_xlsx(blaetter: dict) -> bytes:
    """Kleine .xlsx im Speicher: {Blattname: {(zeile, spalte): wert}}, Texte als Shared Strings."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    texte, puffer = [], io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as zf:
        sheets, rels = "", ""
        for i, (name, zellen) in enumerate(blaetter.items(), 1):
            zeilen = {}
            for (r, c), v in sorted(zellen.items()):
                if isinstance(v, str):
                    if v not in texte:
                        texte.append(v)
                    z = f'<c r="{_spalte(c)}{r + 1}" t="s"><v>{texte.index(v)}</v></c>'
                else:
                    z = f'<c r="{_spalte(c)}{r + 1}"><v>{v!r}</v></c>'
                zeilen.setdefault(r, []).append(z)
            daten = "".join(f'<row r="{r + 1}">{"".join(zs)}</row>' for r, zs in sorted(zeilen.items()))
            zf.writestr(f"xl/worksheets/sheet{i}.xml", f'<worksheet xmlns="{ns}"><sheetData>{daten}</sheetData></worksheet>')
            sheets += f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
            rels += f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>'
        zf.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets>{sheets}</sheets></workbook>')
        zf.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        zf.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}">' + "".join(f"<si><t>{escape(t)}</t></si>" for t in texte) + "</sst>")
    return puffer.getvalue()


def _satz(typ, daten=b""):
    return struct.pack("<HH", typ, len(daten)) + daten


def baue_xls(blaetter: dict) -> bytes:
    """Roher BIFF8-Strom ohne OLE-Hülle: BOF, BOUNDSHEET, SST, EOF und je Blatt NUMBER/RK/MULRK/LABELSST.

    Zellwerte: str -> LABELSST, float -> NUMBER, ("rk", wert, geteilt_durch_100) -> RK, ("mulrk", [ints]) in einer Zeile ab Spalte.
    """
    texte = []
    for zellen in blaetter.values():
        for v in zellen.values():
            if isinstance(v, str) and v not in texte:
                texte.append(v)
    bof = _satz(0x0809, struct.pack("<HHHH", 0x0600, 0x0005, 0, 0) + b"\0" * 8)
    sst = _satz(0x00FC, struct.pack("<II", len(texte), len(texte)) + b"".join(
        struct.pack("<HB", len(t), 0) + t.encode("latin-1") for t in texte))

    def kopf(offsets):
        teile = [bof]
        for (name, _), ab in zip(blaetter.items(), offsets):
            teile.append(_satz(0x0085, struct.pack("<I", ab) + b"\0\0" + bytes([len(name), 0]) + name.encode("latin-1")))
        teile += [sst, _satz(0x000A)]
        return b"".join(teile)

    def blatt(zellen):
        teile = [bof]
        for (z, s), v in sorted(zellen.items()):
            if isinstance(v, str):
                teile.append(_satz(0x00FD, struct.pack("<HHHI", z, s, 0, texte.index(v))))
            elif isinstance(v, tuple) and v[0] == "rk":
                rk = (int(round(v[1] * (100 if v[2] else 1))) << 2) | 2 | (1 if v[2] else 0)
                teile.append(_satz(0x027E, struct.pack("<HHHI", z, s, 0, rk)))
            elif isinstance(v, tuple) and v[0] == "mulrk":
                teile.append(_satz(0x00BD, struct.pack("<HH", z, s) + b"".join(
                    struct.pack("<HI", 0, (n << 2) | 2) for n in v[1]) + struct.pack("<H", s + len(v[1]) - 1)))
            else:
                teile.append(_satz(0x0203, struct.pack("<HHH", z, s, 0) + struct.pack("<d", v)))
        teile.append(_satz(0x000A))
        return b"".join(teile)

    laenge = len(kopf([0] * len(blaetter)))
    offsets, rest = [], b""
    for zellen in blaetter.values():
        offsets.append(laenge + len(rest))
        rest += blatt(zellen)
    return kopf(offsets) + rest


def shiller_blatt(mit_fehler=False):
    """Erfundenes Blatt „Data“ im Layout der Shiller-Datei (Kopf in Zeile 8, Daten ab Zeile 9)."""
    b = {(4, 9): "Real", (5, 9): "Total", (6, 9): "Return"}
    for sp, t in enumerate(["Date", "P", "D", "E", "CPI", "Fraction", "Rate GS10", "Real", "Real", "Price", "Real", "Real", "CAPE"]):
        b[(7, sp)] = t
    if mit_fehler:
        b[(7, 12)] = "KGV"
    zeilen = [(1871.01, 4.44, 0.26, 0.4, 12.46, 1871.04, 5.32, 119.6, 7.0, 119.6, 10.7, 10.7, "NA"),
              (1871.1, 4.74, 0.26, 0.4, 12.9, 1871.79, 5.3, 120.0, 7.0, 121.0, 10.7, 10.7, "NA"),
              (2026.1, 7722.72, 0.0, 0.0, 335.77, 2026.79, 5.24, 7722.72, 0.0, 5268025.5, 0.0, 0.0, 40.71)]
    for z, werte in enumerate(zeilen, 8):
        for sp, v in enumerate(werte):
            if v == 0.0 and sp in (2, 3):
                continue                                       # leere Zellen (Dividende und Gewinn der letzten Monate)
            b[(z, sp)] = v
    b[(11, 0)] = "Oct price is Oct 2nd close"                  # Textzeile am Ende
    b[(11, 5)] = 1.0
    return b


MW = (b'"Observations before October 7, 1896 are from the single Dow Jones Average and has been adjusted. "\n\n'
      b'"Citation: Samuel H. Williamson, \'Daily Closing Value of the Dow Jones Average, 1885 to Present,\' MeasuringWorth, 2026. "\n'
      b'"Date","DJIA"\n"2/16/1885","30.9226"\n"2/17/1885","31.3365"\n"10/22/2026","52867.98"\n')


class Grundlage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ort = pathlib.Path(self.tmp.name)
        self.s = Speicher(self.ort / "daten")

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def datei(self, name, inhalt):
        p = self.ort / name
        p.write_bytes(inhalt if isinstance(inhalt, bytes) else inhalt.encode("utf-8"))
        return p


# ------------------------------------------------------------------------------------------ Excel-Leser

class ExcelLeser(unittest.TestCase):
    def test_xlsx_blatt_und_blattnamen(self):
        b = baue_xlsx({"Erstes": {(0, 0): "Kopf"}, "M13. Test": {(0, 0): "x", (1, 2): 3.5, (2, 23): 7.0}})
        self.assertEqual(tabellen.xlsx_blaetter(b), ["Erstes", "M13. Test"])
        blatt = tabellen.xlsx_blatt(b, "M13")
        self.assertEqual(blatt, {(0, 0): "x", (1, 2): 3.5, (2, 23): 7.0})
        with self.assertRaises(LookupError):
            tabellen.xlsx_blatt(b, "Gibt es nicht")

    def test_xls_alle_zellarten(self):
        strom = baue_xls({"Eins": {(0, 0): "Datum", (0, 1): 2.5, (1, 0): ("rk", 12.0, False), (1, 1): ("rk", 1.25, True),
                                   (2, 0): ("mulrk", [5, 6, 7]), (3, 3): "Datum"},
                          "Zwei": {(0, 0): 99.0}})
        x = tabellen.xls_lesen(strom)
        self.assertEqual(list(x), ["Eins", "Zwei"])
        self.assertEqual(x["Eins"], {(0, 0): "Datum", (0, 1): 2.5, (1, 0): 12.0, (1, 1): 1.25,
                                     (2, 0): 5.0, (2, 1): 6.0, (2, 2): 7.0, (3, 3): "Datum"})
        self.assertEqual(x["Zwei"], {(0, 0): 99.0})


# ------------------------------------------------------------------------------------------ Parser

class Parser(unittest.TestCase):
    def test_shiller_datum_und_spalten(self):
        aus = lad.parse_shiller(baue_xls({"Disclaimer": {(0, 0): "x"}, "Data": shiller_blatt()}))
        sp = aus["shiller_sp"]
        # 1871.01 = Januar, 1871.1 = Oktober (nicht Januar), 2026.1 = Oktober 2026
        self.assertEqual([d for d, _ in sp], ["1871-01-01", "1871-10-01", "2026-10-01"])
        self.assertEqual(sp[-1][1], 7722.72)
        self.assertEqual(aus["shiller_cape"], [("2026-10-01", 40.71)])            # 'NA' fällt weg
        self.assertEqual([d for d, _ in aus["shiller_dividende"]], ["1871-01-01", "1871-10-01"])   # leere Zellen fallen weg
        self.assertEqual(aus["shiller_real_tr"][0], ("1871-01-01", 119.6))
        self.assertEqual(aus["shiller_zins10"][1], ("1871-10-01", 5.3))
        self.assertEqual(aus["shiller_cpi"][0], ("1871-01-01", 12.46))

    def test_shiller_layout_geaendert(self):
        with self.assertRaisesRegex(lad.QuellFehler, "Layout geändert"):
            lad.parse_shiller(baue_xls({"Data": shiller_blatt(mit_fehler=True)}))

    def test_measuringworth(self):
        aus = lad.parse_measuringworth(MW)["dow"]
        self.assertEqual(aus, [("1885-02-16", 30.9226), ("1885-02-17", 31.3365), ("2026-10-22", 52867.98)])
        with self.assertRaisesRegex(lad.QuellFehler, "keine Datenzeilen"):
            lad.parse_measuringworth(b"<html>Fehler</html>")

    def test_blockchain_ohne_nullwerte(self):
        roh = json.dumps({"status": "ok", "values": [{"x": 1230940800, "y": 0.0}, {"x": 1282089600, "y": 0.0646},
                                                     {"x": 1282176000, "y": 0.0707}]}).encode()
        self.assertEqual(lad.parse_blockchain(roh)["btc_blockchain"], [("2010-08-18", 0.0646), ("2010-08-19", 0.0707)])
        with self.assertRaises(lad.QuellFehler):
            lad.parse_blockchain(b"Rate limited")

    def test_coinmetrics(self):
        roh = json.dumps({"data": [
            {"asset": "btc", "time": "2010-07-18T00:00:00.000000000Z", "PriceUSD": "0.08584"},
            {"asset": "btc", "time": "2010-07-19T00:00:00.000000000Z"},                              # kein Preis
            {"asset": "btc", "time": "2010-07-20T00:00:00.000000000Z", "PriceUSD": "0.0"},          # kein Preis
            {"asset": "btc", "time": "2010-07-21T00:00:00.000000000Z", "PriceUSD": "0.0505"},
            {"asset": "btc", "time": "kaputt", "PriceUSD": "5"}]}).encode()
        self.assertEqual(lad.parse_coinmetrics(roh)["btc"], [("2010-07-18", 0.08584), ("2010-07-21", 0.0505)])
        self.assertEqual(lad.parse_coinmetrics_seite(roh)[1], None)
        mit_weiter = json.dumps({"data": [], "next_page_url": "https://x/seite2"}).encode()
        self.assertEqual(lad.parse_coinmetrics_seite(mit_weiter), ([], "https://x/seite2"))
        for schlecht in (b"Rate limited", b'{"error": {"type": "unauthorized"}}', json.dumps({"data": []}).encode()):
            with self.assertRaisesRegex(lad.QuellFehler, "Coin Metrics"):
                lad.parse_coinmetrics(schlecht)

    def test_weltbank(self):
        kopf = {(0, 0): "World Bank Commodity Price Data (The Pink Sheet)", (4, 1): "Crude oil, average", (4, 2): "Gold",
                (4, 3): "Platinum", (4, 4): "Silver", (4, 5): "Zinc", (5, 1): "($/bbl)", (5, 2): "($/troy oz)", (5, 3): "($/troy oz)",
                (5, 4): "($/troy oz)", (5, 5): "($/mt)"}
        zeilen = {(6, 0): "1960M01", (6, 1): 1.6, (6, 2): 35.0, (6, 3): 84.0, (6, 4): 0.9, (6, 5): 261.0,
                  (7, 0): "1960M02", (7, 2): 35.0, (7, 3): "…", (7, 4): 0.9,
                  (8, 0): "2026M09", (8, 2): 4319.0, (8, 3): 1784.0, (8, 4): 64.6,
                  (9, 0): "Anmerkung am Ende", (9, 2): 5.0}
        aus = lad.parse_weltbank(baue_xlsx({"Mismatch Details": {(0, 0): "x"}, "Monthly Prices": {**kopf, **zeilen}}))
        self.assertEqual(aus["gold_monat"], [("1960-01-01", 35.0), ("1960-02-01", 35.0), ("2026-09-01", 4319.0)])
        self.assertEqual(aus["silber_monat"], [("1960-01-01", 0.9), ("1960-02-01", 0.9), ("2026-09-01", 64.6)])
        self.assertEqual(aus["platin_monat"], [("1960-01-01", 84.0), ("2026-09-01", 1784.0)])      # „…“ fällt weg
        ohne_platin = {k: v for k, v in kopf.items() if v != "Platinum"}
        verrutscht = {**kopf, (5, 4): "($/kg)"}
        for blaetter, muster in (({"Monthly Prices": {**ohne_platin, **zeilen}}, "Kopfzeile"),
                                 ({"Monthly Prices": {**verrutscht, **zeilen}}, "Einheit"),
                                 ({"Anderes Blatt": {**kopf, **zeilen}}, "Monthly Prices"),
                                 ({"Monthly Prices": dict(kopf)}, "keine Werte")):
            with self.assertRaisesRegex(lad.QuellFehler, muster):
                lad.parse_weltbank(baue_xlsx(blaetter))
        with self.assertRaisesRegex(lad.QuellFehler, "nicht lesbar"):
            lad.parse_weltbank(b"kein Excel")

    def test_fred(self):
        csv = b"observation_date,USREC\n1854-12-01,1\n1855-01-01,.\n1855-02-01,0\n"
        self.assertEqual(lad.parse_fred(csv, "USREC"), [("1854-12-01", 1.0), ("1855-02-01", 0.0)])
        self.assertEqual(lad.parse_fred(b"DATE,USREC\n1854-12-01,1\n", "USREC"), [("1854-12-01", 1.0)])   # alter Kopf
        with self.assertRaises(lad.QuellFehler):
            lad.parse_fred(b"<html>blocked</html>", "USREC")
        with self.assertRaises(lad.QuellFehler):
            lad.parse_fred(b"observation_date,ANDERE\n1854-12-01,1\n", "USREC")

    def test_stooq(self):
        csv = b"Date,Open,High,Low,Close,Volume\n2000-01-03,1,2,0.5,1.5,10\n2000-01-04,1,2,0.5,1.7,10\n"
        self.assertEqual(lad.parse_stooq(csv, "xagusd"), [("2000-01-03", 1.5), ("2000-01-04", 1.7)])
        self.assertEqual(lad.parse_stooq(b"Date,Open,High,Low,Close\n2000-01-03,1,2,0.5,1.5\n")[0][1], 1.5)
        for antwort in (b"No data", b"Exceeded the daily hits limit", b"<html>Bot</html>", b""):
            with self.assertRaisesRegex(lad.QuellFehler, "Stooq"):
                lad.parse_stooq(antwort, "xagusd")
        with self.assertRaisesRegex(lad.QuellFehler, "API-Schlüssel.*STOOQ_APIKEY"):
            lad.parse_stooq(b"<html>Please request your API key: get_apikey</html>", "xagusd")

    def test_stooq_schluessel_aus_umgebung_und_datei(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = pathlib.Path(td.name)
        nichts = d / "gibt-es-nicht.env"
        self.assertEqual(lad.stooq_schluessel({}, nichts), "")
        self.assertEqual(lad.stooq_schluessel({"STOOQ_APIKEY": " abc123 "}, nichts), "abc123")
        for inhalt, soll in (("# Stooq\nSTOOQ_APIKEY=xyz789\n", "xyz789"), ("export STOOQ_APIKEY='quoted'\n", "quoted"),
                             ('STOOQ_APIKEY="doppelt"', "doppelt"), ("nurderschluessel\n", "nurderschluessel"),
                             ("ANDERES=1\n", ""), ("", "")):
            f = d / "stooq.env"
            f.write_text(inhalt)
            self.assertEqual(lad.stooq_schluessel({}, f), soll, inhalt)
        f.write_text("STOOQ_APIKEY=ausdatei\n")
        self.assertEqual(lad.stooq_schluessel({"STOOQ_APIKEY": "umgebung"}, f), "umgebung")      # Umgebung gewinnt

    def test_boe(self):
        m13 = {(4, 0): 1709.0, (4, 1): "Jan", (5, 1): "Feb", (6, 1): "Mar", (7, 0): 1709.0, (7, 1): "Apr", (7, 2): 100.0, (7, 23): 1.75,
               (8, 1): "May", (8, 23): 1.8, (9, 1): "Dec", (9, 23): 1.9, (10, 0): 1710.0, (10, 1): "Jan", (10, 23): 2.0}
        d1 = {(7, 0): "Date", (8, 0): "01/01/1833", (8, 2): 4.0, (9, 0): "02/01/1833", (9, 2): 4.5, (10, 0): float(12419), (10, 2): 5.0,
              (11, 0): "Text", (11, 2): "kein Wert"}
        b = baue_xlsx({"Front page": {(0, 0): "x"}, "M13. Mthly share prices 1709+ ": m13, "D1. Official Interest Rates": d1})
        aus = lad.parse_boe(b)
        self.assertEqual(aus["uk_aktien"], [("1709-04-01", 1.75), ("1709-05-01", 1.8), ("1709-12-01", 1.9), ("1710-01-01", 2.0)])
        self.assertEqual(aus["uk_leitzins"], [("1833-01-01", 4.0), ("1833-01-02", 4.5), (zeit.excel_datum(12419), 5.0)])


# ------------------------------------------------------------------------------------------ Speicher

class SpeicherTests(Grundlage):
    def test_ordner_aus_umgebung(self):
        with mock.patch.dict(os.environ, {"KITRADER_DATEN": str(self.ort / "x")}):
            self.assertEqual(datenordner(), self.ort / "x")
            with Speicher() as s:
                self.assertEqual(s.datei, self.ort / "x" / "historie.db")
        self.assertTrue((self.ort / "x" / "historie.db").exists())

    def test_aelterer_name_und_standard(self):
        sauber = {k: v for k, v in os.environ.items() if k not in ("KITRADER_DATEN", "HISTORIE_DATEN")}
        with mock.patch.dict(os.environ, sauber, clear=True):
            self.assertEqual(datenordner(), pathlib.Path("~/.local/share/kitrader").expanduser())
            os.environ["HISTORIE_DATEN"] = str(self.ort / "alt")
            self.assertEqual(datenordner(), self.ort / "alt")
            os.environ["KITRADER_DATEN"] = str(self.ort / "neu")
            self.assertEqual(datenordner(), self.ort / "neu")

    def test_tabellen_und_hinweis(self):
        tab = {r[0] for r in self.s.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertLessEqual({"historie", "historie_stand", "datenhinweise"}, tab)
        self.assertEqual(self.s.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.s.db.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
        self.assertIn("nicht ohne Weiteres verkettbar",
                      self.s.db.execute("SELECT hinweis FROM datenhinweise WHERE thema = 'historie'").fetchone()[0])

    def test_schreiben_werte_stand(self):
        zeilen = [(f"2000-01-{t:02d}", float(t)) for t in range(1, 11)]
        r = self.s.schreiben("dow", zeilen, "Test", "Hinweis")
        self.assertTrue(r["ersetzt"])
        self.assertEqual(self.s.werte("dow"), zeilen)
        self.assertEqual(self.s.werte("dow", von="2000-01-03", bis="2000-01-05"), zeilen[2:5])
        self.assertEqual(len(self.s.werte("dow", bis="2000")), 10)                # bis als Jahr: einschließlich
        st = self.s.stand()[0]
        self.assertEqual((st["reihe"], st["anzahl"], st["erstes"], st["letztes"], st["quelle"], st["hinweis"]),
                         ("dow", 10, "2000-01-01", "2000-01-10", "Test", "Hinweis"))
        self.assertTrue(st["abgerufen"].endswith("Z"))

    def test_schutzregel(self):
        alt = [(f"2000-01-{t:02d}", float(t)) for t in range(1, 21)]
        self.s.schreiben("btc", alt, "alt")
        # zu dünn: 17 von 20 sind 85 %
        r = self.s.schreiben("btc", alt[:17], "neu")
        self.assertFalse(r["ersetzt"])
        self.assertIn("alter Bestand bleibt", r["grund"])
        self.assertEqual(len(self.s.werte("btc")), 20)
        self.assertEqual(self.s.stand()[0]["quelle"], "alt")
        # genau 90 % reicht (18 von 20, gleicher Anfang)
        self.assertTrue(self.s.schreiben("btc", alt[:18], "neu90")["ersetzt"])
        self.assertEqual(len(self.s.werte("btc")), 18)
        # später beginnen: abgelehnt, auch bei gleicher Anzahl
        spaeter = [(f"2000-02-{t:02d}", 1.0) for t in range(1, 19)]
        r = self.s.schreiben("btc", spaeter, "spaeter")
        self.assertFalse(r["ersetzt"])
        self.assertIn("beginnt erst", r["grund"])
        # früher beginnen und mehr Werte: erlaubt
        mehr = [(f"1999-12-{t:02d}", 1.0) for t in range(1, 29)] + alt[:18]
        self.assertTrue(self.s.schreiben("btc", mehr, "mehr")["ersetzt"])
        # viel früher enden (veraltete Ersatzquelle): abgelehnt
        r = self.s.schreiben("btc", mehr[:28] + [("2000-01-02", 1.0)] * 1 + [(f"1999-12-{t:02d}", 2.0) for t in range(1, 5)], "kurz")
        self.assertFalse(r["ersetzt"])
        # erzwingen hebt die Regel auf
        self.assertTrue(self.s.schreiben("btc", alt[:3], "erzwungen", erzwingen=True)["ersetzt"])
        self.assertEqual(len(self.s.werte("btc")), 3)
        # leerer Abruf ersetzt nie
        self.assertFalse(self.s.schreiben("btc", [], "leer")["ersetzt"])
        self.assertEqual(len(self.s.werte("btc")), 3)

    def test_andere_reihen_bleiben(self):
        self.s.schreiben("a", [("2000-01-01", 1.0)], "q")
        self.s.schreiben("b", [("2000-01-01", 2.0), ("2000-01-02", 3.0)], "q")
        self.s.schreiben("a", [("2000-01-01", 5.0)], "q")
        self.assertEqual(self.s.werte("b"), [("2000-01-01", 2.0), ("2000-01-02", 3.0)])
        self.assertEqual(self.s.werte("a"), [("2000-01-01", 5.0)])

    def test_zweite_verbindung_im_wal(self):
        self.s.schreiben("a", [("2000-01-01", 1.0)], "q")
        with Speicher(self.ort / "daten") as zweiter:
            self.assertEqual(zweiter.werte("a"), [("2000-01-01", 1.0)])


# ------------------------------------------------------------------------------------------ laden() mit falschem Abruf

class LadenTests(Grundlage):
    def holen_attrappe(self, antworten):
        def holen(url, **kw):
            for teil, antwort in antworten.items():
                if teil in url:
                    if isinstance(antwort, Exception):
                        raise antwort
                    return antwort
            raise AssertionError(f"unerwarteter Abruf {url}")
        return holen

    def test_fehler_je_quelle_der_rest_laeuft(self):
        bc = json.dumps({"values": [{"x": 1282089600, "y": 0.0646}]}).encode()
        holen = self.holen_attrappe({"measuringworth": MW, "blockchain": bc, "stooq.com": b"Exceeded the daily hits limit"})
        meldungen = []
        with mock.patch.object(lad, "stooq_schluessel", return_value="GEHEIM123"):
            erg = lad.laden(["dow", "btc_blockchain", "silber", "gold"], speicher=self.s, holen=holen, melden=meldungen.append,
                            pause=0, heute=date(2026, 10, 25))
        e = {x.reihe: x for x in erg}
        self.assertEqual([x.reihe for x in erg], ["dow", "btc_blockchain", "silber", "gold"])
        self.assertEqual((e["dow"].status, e["dow"].anzahl, e["dow"].erstes, e["dow"].letztes), ("geladen", 3, "1885-02-16", "2026-10-22"))
        self.assertEqual(e["btc_blockchain"].status, "geladen")
        self.assertEqual(e["silber"].status, "Fehler")
        self.assertIn("daily hits limit", e["silber"].meldung)
        self.assertEqual(e["gold"].status, "von Hand")
        self.assertEqual(len(self.s.werte("dow")), 3)
        self.assertEqual(self.s.werte("silber"), [])
        self.assertTrue(meldungen)

    def test_coinmetrics_folgt_next_page_url(self):
        seite1 = json.dumps({"data": [{"time": "2010-07-18T00:00:00.000000000Z", "PriceUSD": "0.08584"}],
                             "next_page_url": "https://community-api.coinmetrics.io/v4/seite2"}).encode()
        seite2 = json.dumps({"data": [{"time": "2010-07-19T00:00:00.000000000Z", "PriceUSD": "0.0808"}]}).encode()
        urls = []

        def holen(url, **kw):
            urls.append(url)
            return seite2 if url.endswith("seite2") else seite1
        erg = lad.laden(["btc"], speicher=self.s, holen=holen)
        self.assertEqual((erg[0].status, erg[0].anzahl, erg[0].erstes, erg[0].letztes), ("geladen", 2, "2010-07-18", "2010-07-19"))
        self.assertIn("assets=btc&metrics=PriceUSD&frequency=1d&start_time=2010-07-01", urls[0])
        self.assertEqual(len(urls), 2)
        self.assertIn("Coin Metrics", self.s.stand()[0]["quelle"])

    def test_weltbank_liest_link_von_der_seite(self):
        wb = baue_xlsx({"Monthly Prices": {(4, 1): "Gold", (4, 2): "Platinum", (4, 3): "Silver", (5, 1): "($/troy oz)",
                                           (5, 2): "($/troy oz)", (5, 3): "($/troy oz)", (6, 0): "1960M01", (6, 1): 35.0,
                                           (6, 2): 84.0, (6, 3): 0.9}})
        seite = (b'<a href="https://thedocs.worldbank.org/en/doc/abc123-0050012026/related/CMO-Historical-Data-Annual.xlsx">A</a>'
                 b'<a href="/en/doc/74e8be41-0050012026/related/CMO-Historical-Data-Monthly.xlsx">M</a>')
        urls = []

        def holen(url, **kw):
            urls.append(url)
            return wb if url.endswith(".xlsx") else seite
        erg = lad.laden(["gold_monat", "silber_monat", "platin_monat"], speicher=self.s, holen=holen)
        self.assertEqual([(x.reihe, x.status, x.anzahl) for x in erg],
                         [("gold_monat", "geladen", 1), ("silber_monat", "geladen", 1), ("platin_monat", "geladen", 1)])
        self.assertEqual(urls, ["https://www.worldbank.org/en/research/commodity-markets",
                                "https://www.worldbank.org/en/doc/74e8be41-0050012026/related/CMO-Historical-Data-Monthly.xlsx"])
        self.assertEqual(self.s.werte("silber_monat"), [("1960-01-01", 0.9)])
        # kein Link auf der Seite: klare Meldung statt Absturz
        erg = lad.laden(["gold_monat"], speicher=self.s, holen=lambda u, **k: b"<html>nichts</html>")
        self.assertEqual(erg[0].status, "Fehler")
        self.assertIn("Link zu CMO-Historical-Data-Monthly.xlsx", erg[0].meldung)

    def test_stooq_ohne_schluessel_gibt_anleitung(self):
        aufrufe = []
        with mock.patch.dict(os.environ, {"STOOQ_APIKEY": ""}), \
                mock.patch.object(lad, "STOOQ_SCHLUESSEL_DATEI", str(self.ort / "fehlt.env")):
            erg = lad.laden(["silber", "platin"], speicher=self.s, holen=lambda u, **k: aufrufe.append(u))
        self.assertEqual([x.status for x in erg], ["Fehler", "Fehler"])
        self.assertEqual(aufrufe, [])                                    # ohne Schlüssel wird gar nicht erst abgerufen
        for wort in ("STOOQ_APIKEY", "stooq.env", "get_apikey", "März 2026"):
            self.assertIn(wort, erg[0].meldung)

    def test_stooq_mit_schluessel_und_schluessel_bleibt_geheim(self):
        csv = b"Date,Open,High,Low,Close,Volume\n2000-01-03,1,2,0.5,1.5,10\n"
        urls = []

        def holen(url, **kw):
            urls.append(url)
            return csv
        with mock.patch.dict(os.environ, {"STOOQ_APIKEY": "GE HEIM&1"}):
            erg = lad.laden(["silber"], speicher=self.s, holen=holen)
        self.assertEqual(erg[0].status, "geladen")
        self.assertEqual(urls, ["https://stooq.com/q/d/l/?s=xagusd&i=d&apikey=GE%20HEIM%261"])
        quelle = self.s.stand()[0]["quelle"]
        self.assertNotIn("HEIM", quelle)
        self.assertIn("apikey=***", self.s.stand()[0]["quelle"])
        # HTTP-Fehler: die URL mit Schlüssel darf nicht in der Meldung stehen
        fehler = urllib.error.HTTPError("https://stooq.com/q/d/l/?s=xagusd&i=d&apikey=GEHEIM", 403, "Forbidden", {}, None)
        with mock.patch.dict(os.environ, {"STOOQ_APIKEY": "GEHEIM"}):
            erg = lad.laden(["platin"], speicher=self.s, holen=lambda u, **k: (_ for _ in ()).throw(fehler))
        self.assertIn("HTTP 403", erg[0].meldung)
        self.assertNotIn("GEHEIM", erg[0].meldung)

    def test_datum_im_mw_aufruf(self):
        urls = []
        lad.laden(["dow"], speicher=self.s, holen=lambda u, **k: urls.append(u) or MW, heute=date(2026, 3, 9))
        self.assertIn("monthEndD=3&dayEndD=9&yearEndD=2026", urls[0])
        self.assertIn("yearStartD=1885", urls[0])

    def test_verbindungsfehler_und_abbruch_nach_drei(self):
        aufrufe = []

        def holen(url, **kw):
            aufrufe.append(url)
            raise OSError("Remote end closed connection")
        erg = lad.laden(["us_rezession", "us_leitzins", "us_zins10_tag", "us_zins3m", "us_cpi"], speicher=self.s, holen=holen, pause=0)
        self.assertEqual([x.status for x in erg], ["Fehler"] * 5)
        self.assertEqual(len(aufrufe), 3)
        self.assertIn("Remote end closed", erg[0].meldung)
        self.assertIn("nicht versucht", erg[3].meldung)

    def test_ganze_quelle_faellt_aus(self):
        erg = lad.laden(["shiller_sp", "dow"], speicher=self.s, pause=0,
                        holen=lambda u, **k: MW if "measuringworth" in u else (_ for _ in ()).throw(OSError("kein Netz")))
        self.assertEqual({x.reihe: x.status for x in erg}, {"shiller_sp": "Fehler", "dow": "geladen"})
        self.assertIn("kein Netz", erg[0].meldung)

    def test_schutzregel_beim_laden(self):
        self.s.schreiben("dow", [(f"1900-01-{t:02d}", 1.0) for t in range(1, 29)], "alt")
        erg = lad.laden(["dow"], speicher=self.s, holen=lambda u, **k: MW)
        self.assertEqual(erg[0].status, "geschützt")
        self.assertIn("Schutzregel", erg[0].meldung)
        self.assertEqual(len(self.s.werte("dow")), 28)

    def test_unbekannte_reihe(self):
        with self.assertRaisesRegex(ValueError, "unbekannte Reihe"):
            lad.laden(["gibtsnicht"], speicher=self.s)

    def test_shiller_ersatzquelle_mit_warnung(self):
        xls = baue_xls({"Data": shiller_blatt()})

        def holen(url, **kw):
            if "shillerdata.com" in url:
                return b'<a href="//img1.wsimg.com/x/ie_data.xls?ver=1">Download</a>'
            if "wsimg.com" in url:
                raise OSError("weg")
            if "econ.yale.edu" in url:
                return xls
            raise AssertionError(url)
        erg = lad.laden(["shiller_sp"], speicher=self.s, holen=holen)
        self.assertEqual(erg[0].status, "geladen")
        self.assertIn("Ersatzquelle Yale", erg[0].meldung)
        self.assertIn("Ersatzquelle Yale", self.s.stand()[0]["hinweis"])


# ------------------------------------------------------------------------------------------ pruefen

class PruefenTests(Grundlage):
    def test_status_und_luecken(self):
        self.s.schreiben("dow", [("1885-02-16", 1), ("1885-02-17", 2), ("1885-02-24", 3), ("1885-02-26", 4)], "q")   # ok
        self.s.schreiben("shiller_sp", [("1872-03-01", 1), ("1872-04-01", 2), ("1872-08-01", 3)], "q")        # später, veraltet
        self.s.schreiben("btc", [("2010-10-01", 1), ("2010-10-02", 2)], "q")                                   # später, veraltet
        self.s.schreiben("uk_aktien", [("1709-04-01", 1), ("1709-05-01", 2)], "q")                             # endet gesetzt
        erg = {p["id"]: p for p in pruefen(self.s, heute=date(1885, 3, 5))}
        self.assertEqual(len(erg), len(REIHEN))
        self.assertEqual(erg["dow"]["status"], "ok")
        self.assertEqual(erg["dow"]["luecke"], {"groesse": 7, "einheit": "Tage", "von": "1885-02-17", "bis": "1885-02-24"})
        self.assertEqual(erg["shiller_sp"]["status"], "beginnt später als erwartet; veraltet")
        self.assertEqual(erg["shiller_sp"]["luecke"]["groesse"], 3)               # Mai, Juni, Juli fehlen
        self.assertEqual(erg["shiller_sp"]["luecke"]["einheit"], "fehlende Monate")
        self.assertEqual(erg["uk_aktien"]["status"], "ok")                         # endet 2017: nie veraltet
        spaeter = {p["id"]: p["status"] for p in pruefen(self.s, heute=date(2010, 12, 31))}
        self.assertEqual(spaeter["btc"], "beginnt später als erwartet; veraltet")        # Start 2010-07-18, Daten ab 2010-10-01
        self.assertEqual(erg["us_cpi"]["status"], "fehlt")
        self.assertEqual((erg["us_cpi"]["anzahl"], erg["us_cpi"]["luecke"]), (0, None))

    def test_grenzen_veraltet(self):
        self.s.schreiben("vix", [("2026-10-01", 1.0), ("2026-10-02", 2.0)], "q")           # Start 1990: später
        self.s.schreiben("us_cpi", [("1913-01-01", 1.0), ("1913-02-01", 2.0)], "q")
        p10 = {p["id"]: p["status"] for p in pruefen(self.s, heute=date(2026, 10, 12))}   # 10 Tage: noch nicht veraltet
        self.assertNotIn("veraltet", p10["vix"])
        p11 = {p["id"]: p["status"] for p in pruefen(self.s, heute=date(2026, 10, 13))}
        self.assertIn("veraltet", p11["vix"])
        self.assertIn("veraltet", p11["us_cpi"])                                          # Monatsreihe: über 75 Tage


# ------------------------------------------------------------------------------------------ einlesen

class EinlesenTests(Grundlage):
    def test_csv_dezimalkomma_semikolon(self):
        csv = ("Goldpreis Auszug\nStand: erfunden\n\nDatum;USD (AM);EUR (AM)\n"
               "02.01.2020;1.524,35;1.360,10\n03.01.2020;1.552,00;1.380,00\n06.01.2020;1.569,5;1.400,25\nQuelle: erfunden\n")
        p = self.datei("gold.csv", csv)
        e = einlesen("gold", p, wert_spalte="USD", speicher=self.s)
        self.assertEqual((e["anzahl"], e["erstes"], e["letztes"], e["wert_spalte"], e["datum_spalte"]), (3, "2020-01-02", "2020-01-06", "USD (AM)", "Datum"))
        self.assertEqual(self.s.werte("gold"), [("2020-01-02", 1524.35), ("2020-01-03", 1552.0), ("2020-01-06", 1569.5)])
        # zweite Wertspalte per Nummer (ab 1), Schutzregel gleicher Anzahl lässt Ersetzen zu
        e = einlesen("gold", p, datum_spalte=1, wert_spalte=3, speicher=self.s)
        self.assertEqual(self.s.werte("gold")[0], ("2020-01-02", 1360.10))
        self.assertEqual(e["wert_spalte"], "EUR (AM)")
        # ohne Angabe: erste Spalte mit Zahlen
        einlesen("gold", p, speicher=self.s)
        self.assertEqual(self.s.werte("gold")[0][1], 1524.35)
        self.assertIn("von Hand eingelesen: gold.csv", self.s.stand()[0]["hinweis"])

    def test_csv_komma_und_amerikanisches_datum(self):
        csv = 'Date,Close\n"1/2/2020","1,524.35"\n"1/3/2020","1,552.00"\n"1/6/2020",1569.5\n'
        e = einlesen("gold", self.datei("g.csv", csv), speicher=self.s)
        self.assertEqual(self.s.werte("gold"), [("2020-01-02", 1524.35), ("2020-01-03", 1552.0), ("2020-01-06", 1569.5)])
        self.assertEqual(e["wert_spalte"], "Close")

    def test_tab_monat_und_namen(self):
        txt = "Monat\tDAX\n1948-01\t100,5\n1948-02\t101,25\n1948-03\t99,75\n"
        einlesen("dax", self.datei("dax.txt", txt), datum_spalte="monat", wert_spalte="dax", speicher=self.s)
        self.assertEqual(self.s.werte("dax"), [("1948-01-01", 100.5), ("1948-02-01", 101.25), ("1948-03-01", 99.75)])

    def test_xlsx_mit_seriennummern(self):
        blatt = {(0, 0): "Hinweis", (2, 0): "Date", (2, 1): "USD", (2, 2): "EUR"}
        for i, (serie, usd) in enumerate([(43831, 1500.5), (43832, 1510.0), (43833, 1520.25)], 3):
            blatt[(i, 0)], blatt[(i, 1)], blatt[(i, 2)] = float(serie), usd, usd * 0.9
        b = baue_xlsx({"Inhalt": {(0, 0): "nur Text"}, "Daily": blatt})
        e = einlesen("gold", self.datei("g.xlsx", b), speicher=self.s)
        self.assertEqual(e["blatt"], "Daily")
        self.assertEqual(self.s.werte("gold")[0], ("2020-01-01", 1500.5))
        e = einlesen("gold", self.datei("g.xlsx", b), wert_spalte="EUR", blatt="dai", speicher=self.s)
        self.assertAlmostEqual(self.s.werte("gold")[2][1], 1368.225)
        with self.assertRaisesRegex(EinlesenFehler, "nicht gefunden"):
            einlesen("gold", self.datei("g.xlsx", b), blatt="Nope", speicher=self.s)

    def test_xls(self):
        blatt = {(0, 0): "Datum", (0, 1): "Wert"}
        for i, (d, w) in enumerate([("31.12.1999", 100.0), ("31.01.2000", 101.0), ("29.02.2000", 99.5)], 1):
            blatt[(i, 0)], blatt[(i, 1)] = d, w
        einlesen("dax", self.datei("d.xls", baue_xls({"Tabelle1": blatt})), speicher=self.s)
        self.assertEqual(self.s.werte("dax"), [("1999-12-31", 100.0), ("2000-01-31", 101.0), ("2000-02-29", 99.5)])

    def test_fehler(self):
        with self.assertRaisesRegex(EinlesenFehler, "unbekannte Reihe"):
            einlesen("nix", self.datei("a.csv", "a;b\n"), speicher=self.s)
        with self.assertRaisesRegex(EinlesenFehler, "Datumsspalte"):
            einlesen("gold", self.datei("b.csv", "a;b\nx;1\ny;2\nz;3\n"), speicher=self.s)
        with self.assertRaisesRegex(EinlesenFehler, "nicht gefunden"):
            einlesen("gold", self.datei("c.csv", "Datum;Wert\n2020-01-01;1\n2020-01-02;2\n2020-01-03;3\n"), wert_spalte="Gibt es nicht", speicher=self.s)

    def test_schutzregel_beim_einlesen(self):
        voll = "Datum;Wert\n" + "".join(f"2020-01-{t:02d};{t}\n" for t in range(1, 21))
        einlesen("gold", self.datei("v.csv", voll), speicher=self.s)
        duenn = "Datum;Wert\n" + "".join(f"2020-01-{t:02d};{t}\n" for t in range(1, 6))
        e = einlesen("gold", self.datei("d.csv", duenn), speicher=self.s)
        self.assertFalse(e["ersetzt"])
        self.assertEqual(len(self.s.werte("gold")), 20)
        self.assertTrue(einlesen("gold", self.datei("d.csv", duenn), speicher=self.s, erzwingen=True)["ersetzt"])


# ------------------------------------------------------------------------------------------ Ereignisse

class EreignisTests(unittest.TestCase):
    def test_ausgelieferte_liste_ist_in_ordnung(self):
        self.assertEqual(ereig.pruefe_ereignisse(), [])
        self.assertGreater(len(ereig.alle()), 100)

    def test_pruefung_findet_fehler(self):
        gut = {"id": "a", "beginn": "2000-01-01", "ende": None, "genauigkeit": "tag", "art": "x", "region": "Welt", "titel": "T",
               "beschreibung": "B", "quelle": {"name": "N", "url": "https://x", "abgerufen": "2026-10-25"}}
        self.assertEqual(ereig.pruefe_ereignisse([gut]), [])

        def mit(**k):
            return {**gut, **k}
        faelle = {
            "doppelt": ([gut, mit()], "id doppelt"),
            "datum": ([mit(beginn="2000-13-01")], "kein ISO-Datum"),
            "datum2": ([mit(beginn="2000-1-1")], "kein ISO-Datum"),
            "ende": ([mit(ende="2000-02-30")], "ende"),
            "genau": ([mit(genauigkeit="stunde")], "genauigkeit"),
            "url": ([mit(quelle={"name": "N", "url": "ftp://x", "abgerufen": "2026-10-25"})], "http"),
            "pflicht": ([mit(titel="")], "Pflichtfeld titel"),
            "reihenfolge": ([mit(id="b", beginn="2001-01-01"), gut], "nicht nach beginn sortiert"),
            "vorher": ([mit(ende="1999-01-01")], "ende liegt vor beginn"),
        }
        for name, (liste, text) in faelle.items():
            with self.subTest(name):
                self.assertTrue(any(text in p for p in ereig.pruefe_ereignisse(liste)), ereig.pruefe_ereignisse(liste))

    def test_filter(self):
        k = ereig.ereignisse(art="bankenkrise", von="1929", bis="1931-12")
        self.assertEqual([e["id"] for e in k], ["1930-us-bankenpanik", "1931-creditanstalt", "1931-deutsche-bankenkrise"])
        self.assertEqual([e["id"] for e in ereig.ereignisse(suche="Creditanstalt")], ["1931-creditanstalt"])
        self.assertTrue(all("Deutschland" in e["region"] for e in ereig.ereignisse(region="deutschland")))
        self.assertEqual(ereig.ereignis("1929-boersenkrach")["beginn"], "1929-10-24")
        with self.assertRaises(KeyError):
            ereig.ereignis("gibts-nicht")


# ------------------------------------------------------------------------------------------ Studie

class StudieTests(Grundlage):
    def reihe_1929(self):
        self.s.schreiben("dow", [("1929-10-23", 99.0), ("1929-10-24", 100.0), ("1929-11-13", 60.0), ("1929-11-24", 80.0),
                                 ("1930-01-24", 90.0), ("1930-04-24", 110.0), ("1930-10-24", 105.0), ("1931-10-24", 150.0)], "erfunden")

    def test_einzelstudie(self):
        self.reihe_1929()
        s = stu.studie("1929-boersenkrach", speicher=self.s)
        self.assertEqual(s["reihe"], "dow")                                     # Standard ab 1885-02-16
        self.assertEqual(s["start"], {"datum": "1929-10-24", "wert": 100.0})
        r = {h: s["horizonte"][h]["rendite"] for h in stu.HORIZONTE}
        self.assertAlmostEqual(r[1], -0.20)
        self.assertAlmostEqual(r[3], -0.10)
        self.assertAlmostEqual(r[6], 0.10)
        self.assertAlmostEqual(r[12], 0.05)
        self.assertEqual(s["horizonte"][1]["ziel"], "1929-11-24")
        self.assertEqual(s["tief"]["datum"], "1929-11-13")
        self.assertAlmostEqual(s["tief"]["rendite"], -0.40)
        self.assertTrue(s["tief"]["fenster_vollstaendig"])
        er = s["erholung"]
        self.assertEqual(er["datum"], "1930-04-24")
        self.assertEqual(er["tage"], zeit.tage_zwischen("1929-10-24", "1930-04-24"))
        self.assertEqual(er["tage_seit_tief"], zeit.tage_zwischen("1929-11-13", "1930-04-24"))
        self.assertEqual(s["ende"]["ende"], ereig.ereignis("1929-boersenkrach")["ende"])   # Ende laut Liste

    def test_standardreihe_und_fehlende_reihe(self):
        self.assertEqual(stu.standardreihe("1885-02-16"), "dow")
        self.assertEqual(stu.standardreihe("1885-02-15"), "shiller_sp")
        with self.assertRaisesRegex(stu.StudienFehler, "nicht geladen"):
            stu.studie("1929-boersenkrach", speicher=self.s)
        with self.assertRaisesRegex(stu.StudienFehler, "unbekannte Reihe"):
            stu.studie("1929-boersenkrach", reihe="xyz", speicher=self.s)

    def test_reihe_endet_zu_frueh_und_nicht_erholt(self):
        v = stu._Verlauf("dow", [("2000-01-03", 100.0), ("2000-02-03", 90.0), ("2000-04-03", 80.0), ("2000-07-03", 85.0)])
        e = {"id": "t", "beginn": "2000-01-03", "ende": "2000-07-03", "genauigkeit": "tag", "art": "x", "region": "Welt", "titel": "T",
             "beschreibung": "", "quelle": {}}
        s = stu._rechnen(e, v)
        self.assertAlmostEqual(s["horizonte"][1]["rendite"], -0.10)
        self.assertAlmostEqual(s["horizonte"][6]["rendite"], -0.15)
        self.assertIsNone(s["horizonte"][12]["rendite"])                        # Reihe reicht nicht so weit
        self.assertFalse(s["tief"]["fenster_vollstaendig"])
        self.assertEqual(s["erholung"], {"erholt": False, "bis": "2000-07-03"})
        self.assertAlmostEqual(s["ende"]["rendite"], -0.15)
        self.assertTrue(any("12 Monate" in h for h in s["hinweise"]))

    def test_kein_rueckgang_und_vor_der_reihe(self):
        v = stu._Verlauf("dow", [("2000-01-03", 100.0), ("2000-02-03", 120.0), ("2001-02-03", 130.0)])
        e = {"id": "t", "beginn": "2000-01-03", "ende": None, "genauigkeit": "monat", "art": "x", "region": "Welt", "titel": "T",
             "beschreibung": "", "quelle": {}}
        s = stu._rechnen(e, v)
        self.assertEqual(s["tief"]["rendite"], 0.0)
        self.assertEqual(s["erholung"]["tage"], 0)
        self.assertTrue(any("monat" in h for h in s["hinweise"]))
        frueh = stu._rechnen({**e, "beginn": "1990-01-01"}, v)
        self.assertIsNone(frueh["start"])

    def synthetische_dow(self):
        d, i, zeilen = date(1885, 2, 16), 0, []
        while d < date(2026, 6, 1):
            zeilen.append((d.isoformat(), 100 + 0.01 * i + 30 * ((i * 7919) % 101) / 100))
            d, i = date.fromordinal(d.toordinal() + 10), i + 1
        self.s.schreiben("dow", zeilen, "erfunden")
        return zeilen

    def test_studie_art(self):
        self.synthetische_dow()
        s = stu.studie_art("bankenkrise", speicher=self.s)
        ids = [x["ereignis"]["id"] for x in s["eintraege"]]
        self.assertIn("2023-svb", ids)
        self.assertNotIn("1980-savings-and-loan-krise", ids)                    # Genauigkeit jahr: nicht mitgerechnet
        self.assertIn("1980-savings-and-loan-krise", [i for i, _ in s["ausgelassen"]])
        k = s["statistik"][12]
        self.assertEqual(k["n"], len(ids))
        self.assertGreaterEqual(k["n"], stu.MIN_N)
        werte = [x["horizonte"][12]["rendite"] for x in s["eintraege"]]
        self.assertAlmostEqual(k["median"], statistics.median(werte))
        self.assertAlmostEqual(k["mittel"], statistics.fmean(werte))
        self.assertAlmostEqual(k["anteil_negativ"], sum(w < 0 for w in werte) / len(werte))
        mit_groben = stu.studie_art("bankenkrise", speicher=self.s, auch_grobe=True)
        grob = [e for e in ereig.ereignisse(art="bankenkrise") if e["genauigkeit"] in ("quartal", "jahr")]
        self.assertGreaterEqual(len(grob), 2)
        self.assertEqual(len(mit_groben["eintraege"]), len(ids) + len(grob))

    def test_studie_art_wenig_ereignisse_keine_kennzahlen(self):
        self.synthetische_dow()
        s = stu.studie_art("bankenkrise", region="Deutschland", speicher=self.s)
        self.assertEqual(s["statistik"][12]["n"], 2)
        self.assertIsNone(s["statistik"][12]["median"])
        self.assertEqual(len(s["statistik"][12]["werte"]), 2)                   # Einzelwerte bleiben
        with self.assertRaisesRegex(stu.StudienFehler, "keine Ereignisse"):
            stu.studie_art("gibtsnicht", speicher=self.s)

    def test_studie_art_ohne_geladene_reihe(self):
        s = stu.studie_art("bankenkrise", speicher=self.s)
        self.assertEqual(s["eintraege"], [])
        self.assertTrue(s["ausgelassen"])
        self.assertTrue(any("nicht geladen" in g for _, g in s["ausgelassen"]))


# ------------------------------------------------------------------------------------------ Kommandozeile

class KommandozeileTests(Grundlage):
    def lauf(self, *argv):
        puffer = io.StringIO()
        with mock.patch.dict(os.environ, {"KITRADER_DATEN": str(self.ort / "daten")}), contextlib.redirect_stdout(puffer):
            try:
                cli.main(list(argv))
            except SystemExit as e:
                return puffer.getvalue(), e.code
        return puffer.getvalue(), 0

    def test_befehle(self):
        aus, code = self.lauf("reihen")
        self.assertIn("shiller_sp", aus)
        self.assertEqual(code, 0)
        aus, _ = self.lauf("pruefen")
        self.assertIn("fehlt", aus)
        aus, _ = self.lauf("ereignisse", "--art", "bankenkrise", "--von", "2023")
        self.assertIn("2023-svb", aus)
        csv = self.datei("g.csv", "Datum;Wert\n2020-01-01;1,5\n2020-01-02;2,5\n2020-01-03;3,5\n")
        aus, code = self.lauf("einlesen", "gold", str(csv))
        self.assertIn("3 Werte von 2020-01-01 bis 2020-01-03", aus)
        aus, _ = self.lauf("werte", "gold", "--n", "2")
        self.assertIn("3,5", aus)
        self.assertNotIn("2020-01-01 ", aus.split("\n", 2)[2])
        aus, code = self.lauf("studie", "1929-boersenkrach")
        self.assertIn("nicht geladen", str(code))                              # sys.exit("Fehler: …")

    def test_studie_ausgabe(self):
        StudieTests.reihe_1929(self)
        aus, code = self.lauf("studie", "1929-boersenkrach")
        self.assertEqual(code, 0)
        self.assertIn("-20,0 %", aus)
        self.assertIn("-40,0 %", aus)
        self.assertIn("Erholung", aus)
        aus, _ = self.lauf("studie", "--art", "bankenkrise")
        self.assertIn("keine Aussage", aus)


if __name__ == "__main__":
    unittest.main()
