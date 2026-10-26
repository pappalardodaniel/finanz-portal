"""Langfristiges Labor: Tages-, Wochen- und Monatsregeln über Jahrzehnte (python3 -m kitrader labor lang ...).

Beantwortet die Frage: Schlägt eine einfache, bekannte Regel (gleitender Durchschnitt, Momentum, Bewertung per CAPE, Trend + Bewertung)
nach Kosten das Kaufen und Halten oder senkt sie den größten Rückgang deutlich? Der Rechenkern steht in
`strategie/langfrist.py`; hier werden die Reihen aus dem Paket `kitrader.historie` (SQLite im Datenordner) geladen, die Aufgaben
(parallel) gerechnet, die Ereignisauswertung angehängt und alles als Text, Markdown und JSON ausgegeben.

Reihen (alle aus `historie.db`, vorher mit `python3 -m kitrader.historie laden ...` holen):
- sp       S&P (Shiller): nominaler Gesamtertragsindex = `shiller_real_tr` (real, mit Dividenden) mal `shiller_cpi`; CAPE aus `shiller_cape`;
           der jüngste, vorläufige Monat wird weggelassen. Monatsmittel der Tageskurse (!), 1871 bis heute.
- dow      Dow Jones täglich, nur Kurs (ohne Dividenden), Samstagshandel vor 1952 entfernt, ab 1885.
- dow_div  derselbe Dow mit einer Dividenden-NÄHERUNG: täglich die S&P-Dividendenrendite des Vormonats (`shiller_dividende` / `shiller_sp`).
- btc      Bitcoin in US-Dollar täglich (Coin Metrics), ab 2010.
- gold     Gold in US-Dollar, Monatsmittel (Weltbank), ab 1960 (bis 1971 fester Preis).
- uk       UK-Aktienindex monatlich (Bank of England, verkettet), 1709 bis 2017, ohne Dividenden.

Bargeld wächst entweder mit 0 % ("null") oder mit dem 10-Jahres-Zins der USA aus `shiller_zins10` des Vormonats ("zins10"). Der
10-Jahres-Zins liegt meist über dem echten Geldmarktzins, ist also eine großzügige Obergrenze für Bargeld. UK nur mit 0 %.

Kosten je Wechsel (Kauf oder Verkauf, auf den gesamten Wert): Aktien, Indexfonds und Gold 0,1 %, Bitcoin 0,5 % (inklusive Spread).
"""
from __future__ import annotations

import bisect
import json
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from .hilfen import prozent, tabelle, zahl
from .strategie.langfrist import Plan, Reihe, empfindlichkeit, pruefe_reihe

HINWEIS = ("Vergangenheit sagt nichts sicher über die Zukunft. Kein Erfolgsversprechen und keine Anlageberatung. "
           "Die Reihen sind Monatsmittel bzw. verkettete Indizes mit eigenen Schwächen (siehe LANGFRIST-BERICHT.md).")

REIHEN_IDS = ("sp", "dow", "dow_div", "btc", "gold", "uk")
BAR_ARTEN = ("null", "zins10")
BAR_TEXT = {"null": "Bargeld 0 %", "zins10": "Bargeld = 10-Jahres-Zins (großzügig)"}
KOSTEN_AKTIE = 0.1        # Prozent je Wechsel
KOSTEN_KRYPTO = 0.5

PLAENE: dict[str, Plan] = {
    "sp": Plan("S&P 500, nominal mit Dividenden (Shiller, Monatsmittel)", 10, (5, 6, 8, 10, 12), 12, (3, 6, 9, 12), 1910, 10,
               (("1910 bis 1949", "", "1949-12-31"), ("1950 bis 1999", "1950-01-01", "1999-12-31"), ("ab 2000", "2000-01-01", "")),
               mit_cape=True, einheit="Monate"),
    "dow": Plan("Dow Jones täglich, nur Kurs", 200, (50, 100, 150, 200, 250), 252, (63, 126, 189, 252), 1920, 10,
                (("1920 bis 1949", "", "1949-12-31"), ("1950 bis 1999", "1950-01-01", "1999-12-31"), ("ab 2000", "2000-01-01", ""))),
    "dow_div": Plan("Dow Jones täglich, Kurs plus Dividenden-Näherung", 200, (50, 100, 150, 200, 250), 252, (63, 126, 189, 252), 1920, 10,
                    (("1920 bis 1949", "", "1949-12-31"), ("1950 bis 1999", "1950-01-01", "1999-12-31"), ("ab 2000", "2000-01-01", ""))),
    "btc": Plan("Bitcoin täglich (US-Dollar)", 200, (50, 100, 150, 200, 250), 365, (90, 180, 270, 365), 2016, 2,
                (("2016 bis 2019", "2016-01-01", "2019-12-31"), ("2020 bis 2022", "2020-01-01", "2022-12-31"), ("ab 2023", "2023-01-01", ""))),
    "gold": Plan("Gold, Monatsmittel (US-Dollar)", 10, (5, 6, 8, 10, 12), 12, (3, 6, 9, 12), 1980, 10,
                 (("1980 bis 1999", "", "1999-12-31"), ("ab 2000", "2000-01-01", "")),
                 einheit="Monate"),
    "uk": Plan("UK-Aktien, Monatswerte 1709 bis 2017 (ohne Dividenden)", 10, (5, 6, 8, 10, 12), 12, (3, 6, 9, 12), 1750, 20,
               (("1750 bis 1799", "", "1799-12-31"), ("1800 bis 1949", "1800-01-01", "1949-12-31"), ("ab 1950", "1950-01-01", "")),
               einheit="Monate"),
}
BENOETIGT = {"sp": ("shiller_real_tr", "shiller_cpi", "shiller_cape"), "dow": ("dow",),
             "dow_div": ("dow", "shiller_dividende", "shiller_sp"), "btc": ("btc",), "gold": ("gold_monat",), "uk": ("uk_aktien",)}
EREIGNIS_ARTEN = ("krieg", "bankenkrise", "zinsschock", "pandemie")
EREIGNIS_REIHEN = ("dow", "shiller_sp")
KOSTEN_LISTE_AKTIE = [0.05, 0.1, 0.3, 0.5, 1.0]
KOSTEN_LISTE_KRYPTO = [0.25, 0.5, 1.0, 2.0]


class LangFehler(ValueError):
    """Das langfristige Labor kann nicht laufen (fehlende Reihen, falsche Angaben). Der Text ist für Menschen lesbar."""


# ---------------------------------------------------------------- Reihen bauen

class Monatswerte:
    """Monatswerte unter dem Schlüssel 'JJJJ-MM'. `bis(schluessel)` gibt den Wert des letzten Monats bis einschließlich Schlüssel."""

    def __init__(self, paare):
        self.schluessel = [d[:7] for d, _ in paare]
        self.werte = [w for _, w in paare]

    def bis(self, schluessel: str):
        i = bisect.bisect_right(self.schluessel, schluessel) - 1
        return self.werte[i] if i >= 0 else None

    def fuer(self, datum: str):
        """Wert des Vormonats; im allerersten Monat der Reihe (kein Vormonat bekannt) der Wert dieses Monats, davor None."""
        w = self.bis(vormonat(datum))
        if w is None and datum[:7] == self.schluessel[0]:
            return self.werte[0]
        return w


def vormonat(datum: str) -> str:
    """'2026-01-15' -> '2025-12' (der Monat davor, damit nur bekannte Zinsen und Dividenden benutzt werden)."""
    j, m = int(datum[:4]), int(datum[5:7])
    return f"{j - 1:04d}-12" if m == 1 else f"{j:04d}-{m - 1:02d}"


def bar_renditen(daten: list[str], zins: Monatswerte | None) -> list[float]:
    """Bar-Rendite je Periode (Datum j-1 bis j): (1 + Zins/100) hoch (Tage/365), mit dem Zins des Vormonats; ohne Zins 0."""
    out = [0.0]
    for a, b in zip(daten, daten[1:]):
        if zins is None:
            out.append(0.0)
            continue
        y = zins.fuer(a)
        if y is None:
            raise LangFehler(f"Kein Zins für {vormonat(a)} vorhanden (shiller_zins10 beginnt 1871)")
        tage = (date.fromisoformat(b) - date.fromisoformat(a)).days
        out.append((1 + y / 100) ** (tage / 365) - 1)
    return out


def mit_dividenden(daten: list[str], kurs: list[float], rendite: Monatswerte) -> list[float]:
    """Gesamtertragsindex aus Kursen: je Periode wächst er mit dem Kurs und der Dividendenrendite des Vormonats (Jahreswert, Tage/365)."""
    tr = [kurs[0]]
    for j in range(1, len(kurs)):
        dy = rendite.fuer(daten[j - 1])
        if dy is None:
            raise LangFehler(f"Keine Dividendenrendite für {vormonat(daten[j - 1])}")
        tage = (date.fromisoformat(daten[j]) - date.fromisoformat(daten[j - 1])).days
        tr.append(tr[-1] * kurs[j] / kurs[j - 1] * (1 + dy * tage / 365))
    return tr


def ohne_samstag(paare):
    """Dow: Samstagshandel (bis 1952) entfernen, damit '200 Werte' etwa 200 Handelstage der Fünf-Tage-Woche sind."""
    return [(d, w) for d, w in paare if date.fromisoformat(d).weekday() != 5]


def _lesen(speicher, reihe_id: str) -> list[tuple[str, float]]:
    w = speicher.werte(reihe_id)
    if not w:
        raise LangFehler(f"Die Reihe {reihe_id} fehlt im Datenordner. Zuerst: python3 -m kitrader.historie laden {reihe_id}")
    return w


def bauen(speicher, reihe_id: str, bar_art: str, kosten_aktie: float = KOSTEN_AKTIE, kosten_krypto: float = KOSTEN_KRYPTO) -> Reihe:
    """Baut eine Reihe mit Bar-Rendite `bar_art` ('null' oder 'zins10'). Kosten in Prozent je Wechsel."""
    if reihe_id not in PLAENE:
        raise LangFehler(f"Unbekannte Reihe {reihe_id!r}. Vorhanden: {', '.join(REIHEN_IDS)}")
    if bar_art not in BAR_ARTEN:
        raise LangFehler(f"Unbekannte Bargeld-Art {bar_art!r}. Vorhanden: {', '.join(BAR_ARTEN)}")
    if reihe_id == "uk" and bar_art != "null":
        raise LangFehler("Für UK-Aktien gibt es keinen Zins im Datenbestand: nur Bargeld 0 %")
    zins = Monatswerte(_lesen(speicher, "shiller_zins10")) if bar_art == "zins10" else None
    kosten = (kosten_krypto if reihe_id == "btc" else kosten_aktie) / 100
    cape = preis = signal_kurs = None
    if reihe_id == "sp":
        real = _lesen(speicher, "shiller_real_tr")[:-1]            # jüngster Monat ist vorläufig
        cpi = dict(_lesen(speicher, "shiller_cpi"))
        daten = [d for d, _ in real if d in cpi]
        real_d = dict(real)
        kurs = [real_d[d] * cpi[d] for d in daten]
        preis = [cpi[d] for d in daten]
        cape_d = dict(_lesen(speicher, "shiller_cape"))
        cape = [cape_d.get(d) for d in daten]
        kursindex = dict(_lesen(speicher, "shiller_sp"))                 # Signale auf dem Kursindex (ohne Dividenden)
        signal_kurs = [kursindex[d] for d in daten]
    elif reihe_id in ("dow", "dow_div"):
        paare = ohne_samstag(_lesen(speicher, "dow"))
        daten, kurs = [d for d, _ in paare], [w for _, w in paare]
        if reihe_id == "dow_div":
            div, sp = dict(_lesen(speicher, "shiller_dividende")), _lesen(speicher, "shiller_sp")
            rendite = Monatswerte([(d, div[d] / w) for d, w in sp if d in div])
            signal_kurs = kurs                                           # Signale auf dem Kurs (ohne Dividenden-Näherung)
            kurs = mit_dividenden(daten, kurs, rendite)
    else:
        quelle = {"btc": "btc", "gold": "gold_monat", "uk": "uk_aktien"}[reihe_id]
        paare = _lesen(speicher, quelle)
        if reihe_id == "btc":
            paare = [(d, w) for d, w in paare if w > 0]
        daten, kurs = [d for d, _ in paare], [w for _, w in paare]
    return Reihe(reihe_id, daten, kurs, bar_renditen(daten, zins), kosten, preis, cape, signal_kurs)


# ---------------------------------------------------------------- Ereignisse

def basis_verteilung(speicher, reihe_id: str, horizonte=(1, 3, 6, 12)) -> dict:
    """Zum Vergleich: Rendite über h Monate und tiefster Stand binnen 12 Monaten, gestartet am Ersten jedes Monats
    (letzter Wert am oder vor diesem Tag), über die ganze Reihe. Gleiche Definition wie in `historie.studie`."""
    from .historie import zeit
    import statistics
    w = speicher.werte(reihe_id)
    d = [x[0] for x in w]
    v = [x[1] for x in w]
    letzte = d[-1]
    start_j, start_m = int(d[0][:4]), int(d[0][5:7])
    renditen = {h: [] for h in horizonte}
    tiefs = []
    j, m = start_j, start_m
    while True:
        tag = f"{j:04d}-{m:02d}-01"
        i0 = bisect.bisect_right(d, tag) - 1
        if i0 >= 0:
            for h in horizonte:
                ziel = zeit.monate_addieren(tag, h)
                if ziel <= letzte:
                    i1 = bisect.bisect_right(d, ziel) - 1
                    if i1 > i0:
                        renditen[h].append(v[i1] / v[i0] - 1)
            ziel12 = zeit.monate_addieren(tag, 12)
            if ziel12 <= letzte:
                fenster = v[bisect.bisect_right(d, tag):bisect.bisect_right(d, ziel12)]
                tiefs.append(min(0.0, min(fenster) / v[i0] - 1) if fenster else 0.0)
        m += 1
        if m > 12:
            j, m = j + 1, 1
        if f"{j:04d}-{m:02d}-01" > letzte:
            break

    def stat(xs):
        return {"n": len(xs), "median": statistics.median(xs), "mittel": statistics.fmean(xs),
                "anteil_negativ": sum(x < 0 for x in xs) / len(xs)}

    out = {str(h): stat(renditen[h]) for h in horizonte}
    out["tief"] = stat(tiefs)
    return out


def ereignis_auswertung(speicher, arten=EREIGNIS_ARTEN, reihen=EREIGNIS_REIHEN) -> dict:
    """Wie entwickelten sich Dow bzw. S&P (Monatsmittel) nach Kriegen, Bankenkrisen, Zinsschocks und Pandemien?
    Nutzt `kitrader.historie.studie.studie_art` (n steht immer dabei; bei n < 5 gibt es keine Kennzahlen)."""
    from .historie import studie
    out: dict = {"basis": {r: basis_verteilung(speicher, r) for r in reihen}, "zeilen": []}
    for reihe in reihen:
        for art in arten:
            try:
                s = studie.studie_art(art, reihe=reihe, speicher=speicher)
            except studie.StudienFehler as e:
                out["zeilen"].append({"reihe": reihe, "art": art, "fehler": str(e)})
                continue
            out["zeilen"].append({"reihe": reihe, "art": art, "n_ereignisse": s["n_ereignisse"], "n_gerechnet": len(s["eintraege"]),
                                  "ausgelassen": len(s["ausgelassen"]), "statistik": {str(h): {k: v for k, v in st.items() if k != "werte"}
                                                                                     for h, st in s["statistik"].items()},
                                  "einzel_12m": [(e["ereignis"]["id"], e["horizonte"][12]["rendite"] if e["horizonte"][12] else None)
                                                 for e in s["eintraege"]]})
    return out


# ---------------------------------------------------------------- Studie

def _aufgabe(reihe: Reihe, reihe_id: str, bar_art: str, laeufe: int, seed: int, verzoegerung: int) -> dict:
    r = pruefe_reihe(reihe, PLAENE[reihe_id], laeufe, seed, verzoegerung)
    r.update(reihe=reihe_id, bar=bar_art)
    return r


def studie(speicher, reihen: list[str], laeufe: int = 500, seed: int = 1, kosten_aktie: float = KOSTEN_AKTIE,
           kosten_krypto: float = KOSTEN_KRYPTO, prozesse: int | None = None, mit_ereignissen: bool = True,
           mit_empfindlichkeit: bool = True, fortschritt=print) -> dict:
    """Rechnet alle gewählten Reihen mit beiden Bargeld-Arten und gibt {"einstellungen", "ergebnisse", "empfindlichkeit", "ereignisse", "daten"} zurück."""
    for r in reihen:
        if r not in PLAENE:
            raise LangFehler(f"Unbekannte Reihe {r!r}. Vorhanden: {', '.join(REIHEN_IDS)}")
    jobs, daten, empf = [], [], []
    for rid in reihen:
        for bar in (("null",) if rid == "uk" else BAR_ARTEN):
            reihe = bauen(speicher, rid, bar, kosten_aktie, kosten_krypto)
            if bar == "null":
                daten.append({"reihe": rid, "titel": PLAENE[rid].titel, "von": reihe.daten[0], "bis": reihe.daten[-1], "n": len(reihe)})
            jobs.append((reihe, rid, bar))
    prozesse = prozesse or min(4, os.cpu_count() or 1)
    fortschritt(f"{len(jobs)} Aufgaben auf {prozesse} Prozessen ...")
    if prozesse <= 1 or len(jobs) == 1:
        ergebnisse = []
        for k, (reihe, rid, bar) in enumerate(jobs):
            ergebnisse.append(_aufgabe(reihe, rid, bar, laeufe, seed + 1000 * k, 1))
            fortschritt(f"  fertig: {rid} / {BAR_TEXT[bar]}")
    else:
        with ProcessPoolExecutor(max_workers=prozesse) as pool:
            futs = [pool.submit(_aufgabe, reihe, rid, bar, laeufe, seed + 1000 * k, 1) for k, (reihe, rid, bar) in enumerate(jobs)]
            ergebnisse = []
            for (reihe, rid, bar), f in zip(jobs, futs):
                ergebnisse.append(f.result())
                fortschritt(f"  fertig: {rid} / {BAR_TEXT[bar]}")
    if mit_empfindlichkeit:
        for reihe, rid, bar in jobs:
            liste = KOSTEN_LISTE_KRYPTO if rid == "btc" else KOSTEN_LISTE_AKTIE
            for z in empfindlichkeit(reihe, PLAENE[rid], [c / 100 for c in liste]):
                z.update(reihe=rid, bar=bar)
                empf.append(z)
    return {"erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "einstellungen": {"kosten_aktie_prozent": kosten_aktie, "kosten_krypto_prozent": kosten_krypto, "zufallslaeufe": laeufe,
                              "seed": seed, "verzoegerung": 1, "bar_arten": list(BAR_ARTEN)},
            "daten": daten, "ergebnisse": ergebnisse, "empfindlichkeit": empf,
            "ereignisse": ereignis_auswertung(speicher) if mit_ereignissen else None}


# ---------------------------------------------------------------- Ausgabe

def _p(x, stellen=1):
    return "-" if x is None else prozent(x * 100, stellen)


def _pp(x, stellen=1):
    """Unterschied in Prozentpunkten mit Vorzeichen."""
    return "-" if x is None else ("+" if x >= 0 else "") + zahl(x * 100, stellen) + " Pp"


def _jahre(x):
    return "-" if x is None else zahl(x, 1)


def _kopf(real: bool) -> list[str]:
    return (["Regel", "Rendite p. a.", "real p. a.", "gegen Halten", "Schwankung", "Größter Rückgang", "Längste Zeit unter Wasser (Jahre)",
             "Wechsel", "Investiert", "Zufall besser (Rendite / Rückgang)"] if real else
            ["Regel", "Rendite p. a.", "gegen Halten", "Schwankung", "Größter Rückgang", "Längste Zeit unter Wasser (Jahre)",
             "Wechsel", "Investiert", "Zufall besser (Rendite / Rückgang)"])


def _zeilen(fenster: dict) -> list[list]:
    """Tabellenzeilen eines Fensters: zuerst Halten, dann die Regeln."""
    h = fenster["halten"]
    real = "rendite_pa_real" in h
    zeilen = []

    def basis(name, k, vs, z):
        zeile = [name, _p(k["rendite_pa"])]
        if real:
            zeile.append(_p(k.get("rendite_pa_real")))
        zeile += [_pp(k["rendite_pa"] - vs["rendite_pa"]) if vs is not None else "-", _p(k["schwankung"]), _p(k["max_rueckgang"]),
                  _jahre(k["unter_wasser_jahre"]), "-" if k["wechsel"] is None else k["wechsel"],
                  "-" if k["investiert"] is None else _p(k["investiert"], 0)]
        zeile.append("-" if z is None else f"{zahl(z['anteil_besser_rendite'], 0)} % / {zahl(z['anteil_besser_rueckgang'], 0)} %")
        return zeile

    zeilen.append(basis("Kaufen und Halten", h, None, None))
    for z in fenster["zeilen"]:
        zeilen.append(basis(z["regel"], z["kennzahlen"], h, z["zufall"]))
    return zeilen, real


def _teile_zeilen(fenster: dict) -> tuple[list[str], list[list]]:
    """Teilzeiträume: je Regel und Zeitraum Unterschied der Rendite p. a. zu Halten und größter Rückgang (Regel / Halten)."""
    namen = []
    for z in fenster["zeilen"]:
        for t in z["teile"]:
            if t["name"] not in namen:
                namen.append(t["name"])
    zeilen = []
    h_zeile = ["Kaufen und Halten (Rendite p. a. / Rückgang)"]
    h_teile = {}
    for z in fenster["zeilen"][:1]:
        for t in z["teile"]:
            h_teile[t["name"]] = t["halten"]
    h_zeile += ["-" if h_teile.get(n) is None else f"{_p(h_teile[n]['rendite_pa'])} / {_p(h_teile[n]['max_rueckgang'], 0)}" for n in namen]
    zeilen.append(h_zeile)
    for z in fenster["zeilen"]:
        row = [z["regel"]]
        t_by = {t["name"]: t for t in z["teile"]}
        for n in namen:
            t = t_by.get(n)
            if t is None or t["regel"] is None:
                row.append("-")
            else:
                row.append(f"{_pp(t['regel']['rendite_pa'] - t['halten']['rendite_pa'])}, Rückgang {_p(t['regel']['max_rueckgang'], 0)} "
                           f"(Halten {_p(t['halten']['max_rueckgang'], 0)})")
        zeilen.append(row)
    return ["Regel"] + namen, zeilen


def _md(kopf, zeilen) -> str:
    out = ["| " + " | ".join(kopf) + " |", "|" + "|".join(["---"] + ["---:"] * (len(kopf) - 1)) + "|"]
    out += ["| " + " | ".join(str(c) for c in z) + " |" for z in zeilen]
    return "\n".join(out)


def _abschnitte(ergebnis: dict, md: bool):
    """Erzeugt (Überschrift, Tabelle) für jedes Ergebnis; md=True als Markdown, sonst als Text."""
    teile = []
    for r in ergebnis["ergebnisse"]:
        titel = f"{r['titel']} - {BAR_TEXT[r['bar']]}"
        for schluessel, text in (("fenster_a", "Walk-forward-Fenster"), ("fenster_b", "gesamte Reihe, nur feste Regeln")):
            f = r[schluessel]
            zeilen, real = _zeilen(f)
            kopf = _kopf(real)
            kopf_text = (f"{titel}\n{text}: {f['von']} bis {f['bis']} (Kosten {zahl(r['kosten'] * 100, 2)} % je Wechsel)")
            teile.append((schluessel, kopf_text, (_md(kopf, zeilen) if md else tabelle(kopf, zeilen, links=(0,)))))
        kopf_t, zeilen_t = _teile_zeilen(r["fenster_a"])
        teile.append(("teile", f"{titel}\nTeilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)",
                      _md(kopf_t, zeilen_t) if md else tabelle(kopf_t, zeilen_t, links=(0,))))
    return teile


def _empfindlichkeit_text(ergebnis: dict, md: bool) -> list[str]:
    zeilen = ergebnis.get("empfindlichkeit") or []
    if not zeilen:
        return []
    out = []
    schluessel = []
    for z in zeilen:
        k = (z["reihe"], z["bar"], z["regel"])
        if k not in schluessel:
            schluessel.append(k)
    for rid in dict.fromkeys(k[0] for k in schluessel):
        sub = [z for z in zeilen if z["reihe"] == rid and z["verzoegerung"] == 1]
        kosten = sorted({z["kosten"] for z in sub})
        kopf = ["Bargeld", "Regel"] + [f"{zahl(c * 100, 2)} %" for c in kosten] + ["ohne Verzögerung (Kosten wie Standard)", "Halten p. a."]
        rows = []
        for (r, bar, regel) in [k for k in schluessel if k[0] == rid]:
            zs = {z["kosten"]: z for z in sub if z["bar"] == bar and z["regel"] == regel}
            basis_c = min(kosten, key=lambda c: abs(c - (KOSTEN_KRYPTO if rid == "btc" else KOSTEN_AKTIE) / 100))
            null = [z for z in zeilen if z["reihe"] == rid and z["bar"] == bar and z["regel"] == regel and z["verzoegerung"] == 0
                    and abs(z["kosten"] - basis_c) < 1e-12]
            h = zs[basis_c]["halten_pa"]
            rows.append([BAR_TEXT[bar].replace(" (großzügig)", ""), regel] + [_pp(zs[c]["rendite_pa"] - zs[c]["halten_pa"]) for c in kosten]
                        + [_pp(null[0]["rendite_pa"] - null[0]["halten_pa"]) if null else "-", _p(h)])
        titel = PLAENE[rid].titel
        out.append(f"{titel}: Rendite p. a. der festen Regel minus Halten, bei verschiedenen Kosten je Wechsel")
        out.append(_md(kopf, rows) if md else tabelle(kopf, rows, links=(0, 1)))
        out.append("")
    return out


def _ereignis_text(ergebnis: dict, md: bool) -> list[str]:
    e = ergebnis.get("ereignisse")
    if not e:
        return []
    kopf = ["Reihe", "Ereignisart", "n", "1 Monat", "3 Monate", "6 Monate", "12 Monate", "Tiefstand binnen 12 Monaten"]

    def zelle(st):
        if st["median"] is None:
            return f"n={st['n']}: keine Kennzahl"
        return f"{_p(st['median'])} / {_p(st['mittel'])} / {_p(st['anteil_negativ'], 0)}"

    zeilen = []
    for z in e["zeilen"]:
        if "fehler" in z:
            zeilen.append([z["reihe"], z["art"], 0, "-", "-", "-", "-", z["fehler"]])
            continue
        s = z["statistik"]
        zeilen.append([z["reihe"], z["art"], f"{s['12']['n']} von {z['n_ereignisse']}"] + [zelle(s[str(h)]) for h in (1, 3, 6, 12)]
                      + [zelle(s["tief"])])
    for reihe, b in e["basis"].items():
        zeilen.append([reihe, "alle Monate (Vergleich)", b["1"]["n"]] + [zelle(b[str(h)]) for h in (1, 3, 6, 12)] + [zelle(b["tief"])])
    return ["Zelle: Median / Mittel / Anteil negativ. Reihe dow = Dow täglich (ab 1885), shiller_sp = S&P-Kurs, Monatsmittel (ab 1871).",
            _md(kopf, zeilen) if md else tabelle(kopf, zeilen, links=(0, 1))]


def faltungen_text(ergebnis: dict) -> list[str]:
    """Gewählte Parameter je Testblock (zeigt, wie stabil die Einstellung ist)."""
    out = []
    for r in ergebnis["ergebnisse"]:
        if r["bar"] != "null":
            continue
        for regel, fs in r["faltungen"].items():
            out.append(f"{r['titel']} / {regel}: " + ", ".join(f"{f['von'][:4]}={f['parameter']}" for f in fs))
    return out


KURZ = {"sp": "S&P 500 (nominal, mit Dividenden)", "dow": "Dow täglich (nur Kurs)", "dow_div": "Dow täglich (Kurs + Dividenden-Näherung)",
        "btc": "Bitcoin täglich", "gold": "Gold (Monatsmittel)", "uk": "UK-Aktien (ohne Dividenden)"}
UEBERSICHT_KOPF = ["Reihe (Walk-forward-Fenster)", "Halten: Rendite p. a. / größter Rückgang", "Trend fest: gegen Halten (Bargeld 0 % / Zins)",
                   "Trend fest: größter Rückgang", "Momentum fest: gegen Halten (Bargeld 0 % / Zins)", "Momentum fest: größter Rückgang",
                   "Trend Walk-forward: gegen Halten (0 % / Zins)", "Momentum Walk-forward: gegen Halten (0 % / Zins)"]


def uebersicht(ergebnis: dict) -> tuple[list[str], list[list]]:
    """Haupttabelle: je Reihe Halten und die vier Trend-/Momentum-Regeln (Unterschied der Rendite p. a. zu Halten bei beiden Bargeld-Arten)."""
    zeilen = []
    for rid in dict.fromkeys(r["reihe"] for r in ergebnis["ergebnisse"]):
        je = {r["bar"]: r for r in ergebnis["ergebnisse"] if r["reihe"] == rid}
        a = je["null"]["fenster_a"]
        h = a["halten"]

        def regel(bar, name):
            return None if bar not in je else next(z for z in je[bar]["fenster_a"]["zeilen"] if z["regel"].startswith(name))

        def delta(name):
            teile = []
            for bar in ("null", "zins10"):
                z = regel(bar, name)
                teile.append("-" if z is None else _pp(z["kennzahlen"]["rendite_pa"] - je[bar]["fenster_a"]["halten"]["rendite_pa"]))
            return " / ".join(teile)

        zeilen.append([f"{KURZ[rid]} ({je['null'].get('erstes_testjahr', a['von'][:4])} bis {a['bis'][:4]})", f"{_p(h['rendite_pa'])} / {_p(h['max_rueckgang'], 0)}",
                       delta("Trend (fest"), _p(regel("null", "Trend (fest")["kennzahlen"]["max_rueckgang"], 0),
                       delta("Momentum (fest"), _p(regel("null", "Momentum (fest")["kennzahlen"]["max_rueckgang"], 0),
                       delta("Trend (Walk"), delta("Momentum (Walk")])
    return UEBERSICHT_KOPF, zeilen


def zusammenfassung(ergebnis: dict) -> dict:
    """Zählt über alle Zeilen der Walk-forward-Fenster (Reihe x Bargeld x Regel; die Zeilen sind nicht unabhängig, z. B. kommen Dow und
    Dow mit Dividenden-Näherung aus denselben Kursen). Zufallstreffer: Regeln, die in weniger als 5 % der Zufallsläufe übertroffen werden."""
    zeilen = [(r, z) for r in ergebnis["ergebnisse"] for z in r["fenster_a"]["zeilen"]]
    n = len(zeilen)
    mit_zufall = [(r, z) for r, z in zeilen if z["zufall"]]

    def besser(r, z):
        return z["kennzahlen"]["rendite_pa"] > r["fenster_a"]["halten"]["rendite_pa"]

    def ruhiger(r, z, anteil=1 / 3):
        return z["kennzahlen"]["max_rueckgang"] <= (1 - anteil) * r["fenster_a"]["halten"]["max_rueckgang"]

    return {"zeilen": n, "schlagen_halten_rendite": sum(besser(r, z) for r, z in zeilen),
            "schlagen_halten_rendite_bargeld_null": sum(besser(r, z) for r, z in zeilen if r["bar"] == "null"),
            "zeilen_bargeld_null": sum(1 for r, _ in zeilen if r["bar"] == "null"),
            "rueckgang_ein_drittel_kleiner": sum(ruhiger(r, z) for r, z in zeilen),
            "schwankung_kleiner": sum(z["kennzahlen"]["schwankung"] < r["fenster_a"]["halten"]["schwankung"] for r, z in zeilen),
            "beides": sum(besser(r, z) and ruhiger(r, z) for r, z in zeilen),
            "mit_zufallsvergleich": len(mit_zufall),
            "zufall_rendite_unter_5": sum(z["zufall"]["anteil_besser_rendite"] < 5 for _, z in mit_zufall),
            "zufall_rueckgang_unter_5": sum(z["zufall"]["anteil_besser_rueckgang"] < 5 for _, z in mit_zufall),
            "zufallstreffer_erwartet": 0.05 * len(mit_zufall)}


def zusammenfassung_text(ergebnis: dict) -> str:
    z = zusammenfassung(ergebnis)
    return "\n".join([
        f"Kurzfazit: {z['zeilen']} Zeilen (Reihe, Bargeld-Art, Regel) im Walk-forward-Fenster; die Zeilen sind nicht unabhängig.",
        f"  Rendite p. a. höher als Halten: {z['schlagen_halten_rendite']} von {z['zeilen']} "
        f"(bei Bargeld 0 %: {z['schlagen_halten_rendite_bargeld_null']} von {z['zeilen_bargeld_null']}).",
        f"  Größter Rückgang mindestens ein Drittel kleiner als bei Halten: {z['rueckgang_ein_drittel_kleiner']} von {z['zeilen']}; "
        f"Schwankung kleiner: {z['schwankung_kleiner']}; beides (mehr Rendite UND ein Drittel weniger Rückgang): {z['beides']}.",
        f"  Zufallsvergleich ({z['mit_zufallsvergleich']} Zeilen): Rendite besser als 95 % der Zufallsläufe in {z['zufall_rendite_unter_5']}, "
        f"Rückgang besser als 95 % in {z['zufall_rueckgang_unter_5']} (allein durch Zufall zu erwarten: etwa {zahl(z['zufallstreffer_erwartet'], 1)})."])


def als_text(ergebnis: dict) -> str:
    kopf, zeilen = uebersicht(ergebnis)
    teile = ["== Übersicht: Trend und Momentum gegen Kaufen und Halten (Zins = Bargeld mit 10-Jahres-Zins) ==",
             tabelle(kopf, zeilen, links=(0,)), ""]
    for _, kopf, tab in _abschnitte(ergebnis, md=False):
        teile += [f"== {kopf} ==", tab, ""]
    emp = _empfindlichkeit_text(ergebnis, md=False)
    if emp:
        teile += ["== Empfindlichkeit: Kosten und Verzögerung ==", *emp]
    ez = _ereignis_text(ergebnis, md=False)
    if ez:
        teile += ["== Ereignisauswertung ==", *ez, ""]
    teile += ["== Gewählte Parameter je Testblock (Walk-forward) ==", *faltungen_text(ergebnis), "", zusammenfassung_text(ergebnis), "", HINWEIS]
    return "\n".join(teile)


def als_markdown(ergebnis: dict) -> str:
    kopf, zeilen = uebersicht(ergebnis)
    teile = ["### Übersicht", "", _md(kopf, zeilen), ""]
    for _, kopf, tab in _abschnitte(ergebnis, md=True):
        titel, _, rest = kopf.partition("\n")
        teile += [f"**{titel}**", f"*{rest}*", "", tab, ""]
    emp = _empfindlichkeit_text(ergebnis, md=True)
    if emp:
        teile += ["### Empfindlichkeit: Kosten und Verzögerung", "", *emp]
    ez = _ereignis_text(ergebnis, md=True)
    if ez:
        teile += ["### Ereignisauswertung", "", *ez, ""]
    teile += ["### Gewählte Parameter je Testblock (Walk-forward)", "", *[f"- {z}" for z in faltungen_text(ergebnis)], "",
              "### Kurzfazit", "", zusammenfassung_text(ergebnis), ""]
    return "\n".join(teile)


# ---------------------------------------------------------------- Kommandozeile

def argumente(s) -> None:
    s.add_argument("--reihen", nargs="+", default=list(REIHEN_IDS), metavar="ID",
                   help=f"Reihen (Standard alle): {', '.join(REIHEN_IDS)}")
    s.add_argument("--zufall", type=int, default=500, help="Zahl der Zufallsläufe je Regel (Standard 500)")
    s.add_argument("--seed", type=int, default=1, help="Startwert des Zufalls (gleiche Daten + gleicher Startwert = gleiches Ergebnis)")
    s.add_argument("--kosten-aktie", type=float, default=KOSTEN_AKTIE, help="Kosten je Wechsel in %% für Aktien, Dow, Gold (Standard 0.1)")
    s.add_argument("--kosten-krypto", type=float, default=KOSTEN_KRYPTO, help="Kosten je Wechsel in %% für Bitcoin (Standard 0.5)")
    s.add_argument("--prozesse", type=int, help="parallele Prozesse (Standard: bis zu 4)")
    s.add_argument("--ohne-ereignisse", action="store_true", help="die Ereignisauswertung weglassen")
    s.add_argument("--ohne-empfindlichkeit", action="store_true", help="die Kosten-/Verzögerungs-Gegenprobe weglassen")
    s.add_argument("--ausgabe", metavar="JSON", help="Ergebnisse als JSON speichern (Standard: <Datenordner>/labor/lang.json)")
    s.add_argument("--markdown", metavar="MD", help="Tabellen für den Bericht als Markdown speichern")
    s.add_argument("--aus-json", metavar="JSON", help="nichts rechnen, nur Tabellen aus einer gespeicherten JSON-Datei zeigen")


def befehl(args, einst, ordner) -> int:
    if args.aus_json:
        ergebnis = json.loads(Path(args.aus_json).read_text(encoding="utf-8"))
    else:
        if args.zufall < 0 or args.kosten_aktie < 0 or args.kosten_krypto < 0 or args.kosten_aktie >= 100 or args.kosten_krypto >= 100:
            raise LangFehler("zufall und Kosten dürfen nicht negativ sein, Kosten müssen unter 100 % liegen")
        from .historie.speicher import Speicher
        speicher = Speicher(Path(ordner))
        try:
            print(f"Langfristiges Labor: Kosten je Wechsel {args.kosten_aktie} % (Aktien, Dow, Gold), {args.kosten_krypto} % (Bitcoin); "
                  f"Zufall {args.zufall} Läufe.\nDas kann einige Minuten dauern.")
            ergebnis = studie(speicher, args.reihen, args.zufall, args.seed, args.kosten_aktie, args.kosten_krypto, args.prozesse,
                              not args.ohne_ereignisse, not args.ohne_empfindlichkeit)
        finally:
            speicher.close()
        ziel = Path(args.ausgabe) if args.ausgabe else Path(ordner) / "labor" / "lang.json"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(json.dumps(ergebnis, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nErgebnisse gespeichert: {ziel}\n")
    print(als_text(ergebnis))
    if args.markdown:
        Path(args.markdown).write_text(als_markdown(ergebnis), encoding="utf-8")
        print(f"\nMarkdown-Tabellen gespeichert: {args.markdown}")
    return 0
