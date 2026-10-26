"""Kommandozeile: python -m kitrader.historie <Befehl>

  reihen                     Verzeichnis der Reihen (Quelle, Start, automatisch oder von Hand)
  laden [ID …]               Reihen abrufen und speichern (ohne ID: alle automatischen)
  pruefen                    Bestand prüfen: fehlt, beginnt später, veraltet, größte Lücke
  einlesen ID DATEI          Datei von Hand einlesen (CSV, xlsx, xls), z. B. für gold und dax
  werte ID                   gespeicherte Werte zeigen
  ereignisse                 Ereignisliste (Krisen, Geld- und Handelspolitik, Rezessionen …)
  studie EREIGNIS_ID         Was machte die Reihe nach dem Ereignis? Oder: studie --art ART
  muster EREIGNIS_ID         Reaktion vieler Märkte (Dow, DAX, Bitcoin, Gold, Öl …) auf ein Ereignis, mit Vergleich zu normalen Zeiträumen
  muster --art ART           dasselbe für alle Ereignisse einer Art (z. B. unruhen), optional --region
  aehnlich "TEXT"            frühere Ereignisse, die zur Lage passen, mit Begründung und Mehrmarkt-Reaktion
  zusammenhang               Krypto (Bitcoin) gegen Aktien (Dow): rollierende Korrelation, aktuell, historisch, in Stressphasen
Daten liegen in $KITRADER_DATEN (Standard ~/.local/share/kitrader, älterer Name HISTORIE_DATEN gilt weiter), Einzelheiten in HISTORIE.md.
"""
from __future__ import annotations

import argparse
import sys

from . import ereignisse as ereig
from .einlesen import EinlesenFehler, einlesen
from .laden import laden
from . import muster as mu
from .pruefen import pruefen
from .reihen import REIHEN
from .speicher import Speicher, datenordner
from .studie import StudienFehler, studie, studie_art


def zahl(x: float, stellen: int = 2) -> str:
    return f"{x:,.{stellen}f}".replace(",", "\0").replace(".", ",").replace("\0", ".")


def zahl_kurz(x: float) -> str:
    """Bis zu 4 Nachkommastellen, ohne überflüssige Nullen."""
    t = f"{x:,.4f}".rstrip("0").rstrip(".")
    return t.replace(",", "\0").replace(".", ",").replace("\0", ".")


def prozent(r: float | None, stellen: int = 1) -> str:
    return "–" if r is None else f"{r * 100:+.{stellen}f} %".replace(".", ",")


def tabelle(kopf: list[str], zeilen: list[list], links: tuple[int, ...] = (0,)) -> None:
    zeilen = [[str(z) for z in r] for r in zeilen]
    breit = [max(len(kopf[i]), *(len(r[i]) for r in zeilen)) if zeilen else len(kopf[i]) for i in range(len(kopf))]

    def zeile(r):
        return "  ".join(r[i].ljust(breit[i]) if i in links else r[i].rjust(breit[i]) for i in range(len(kopf))).rstrip()
    print(zeile(kopf))
    print("  ".join("-" * b for b in breit))
    for r in zeilen:
        print(zeile(r))


# ---------------------------------------------------------------------------------------------- Befehle

def befehl_reihen(_):
    tabelle(["ID", "Titel", "Takt", "Start", "Abruf", "Quelle"],
            [[r.id, r.titel, r.takt, r.start, "von Hand" if r.lader == "hand" else "automatisch", r.quelle] for r in REIHEN.values()],
            links=(0, 1, 2, 3, 4, 5))
    print("\nBedingungen und Hinweise je Quelle: HISTORIE.md")


def befehl_laden(a):
    ergebnisse = laden(a.ids or None, melden=lambda t: print(t, flush=True), erzwingen=a.erzwingen)
    print()
    kurz = lambda t: t if len(t) <= 90 else t[:89] + "…"                       # lange Meldungen folgen unter der Tabelle
    tabelle(["ID", "Status", "Anzahl", "Erstes", "Letztes", "Meldung"],
            [[e.reihe, e.status, e.anzahl or "", e.erstes, e.letztes, kurz(e.meldung)] for e in ergebnisse],
            links=(0, 1, 3, 4, 5))
    lang: dict[str, list[str]] = {}
    for e in ergebnisse:
        if len(e.meldung) > 90:
            lang.setdefault(e.meldung, []).append(e.reihe)
    for text, ids in lang.items():
        print(f"\nMeldung zu {', '.join(ids)}:\n  {text}")
    fehler = sum(e.status == "Fehler" for e in ergebnisse)
    geschuetzt = sum(e.status == "geschützt" for e in ergebnisse)
    print(f"\n{sum(e.status == 'geladen' for e in ergebnisse)} geladen, {geschuetzt} durch Schutzregel behalten, {fehler} Fehler "
          f"(Datenordner: {datenordner()})")
    if fehler:
        sys.exit(1)


def befehl_pruefen(_):
    zeilen = []
    for p in pruefen():
        l = p["luecke"]
        if l and l["groesse"] > (1 if l["einheit"] == "Tage" else 0):        # 1 Tag bzw. 0 fehlende Monate = lückenlos
            luecke = f"{l['groesse']} {'Tag' if l['groesse'] == 1 else l['einheit']} ({l['von']} bis {l['bis']})"
        else:
            luecke = "keine" if l else "–"
        zeilen.append([p["id"], p["status"] + (f" (endet {p['endet']})" if p["endet"] and p["anzahl"] else ""),
                       p["anzahl"] or "", p["erstes"], p["letztes"], luecke])
    tabelle(["ID", "Status", "Anzahl", "Erstes", "Letztes", "Größte Lücke"], zeilen, links=(0, 1, 3, 4, 5))
    print("\nLücken werden nur angezeigt, nicht bewertet: Börsenschließungen und fehlende Quellenmonate sehen gleich aus.")


def befehl_einlesen(a):
    try:
        e = einlesen(a.id, a.datei, a.datum_spalte, a.wert_spalte, a.blatt, erzwingen=a.erzwingen)
    except (EinlesenFehler, FileNotFoundError, LookupError) as f:
        sys.exit(f"Fehler: {f}")
    print(f"Datei {a.datei}" + (f", Blatt {e['blatt']}" if e["blatt"] != "csv" else ""))
    print(f"Datum aus: {e['datum_spalte']} | Wert aus: {e['wert_spalte']}")
    print(f"{e['anzahl']} Werte von {e['erstes']} bis {e['letztes']}")
    if e["ersetzt"]:
        print(f"Reihe {e['reihe']} gespeichert.")
    else:
        sys.exit(f"Reihe {e['reihe']} NICHT ersetzt, Schutzregel: {e['grund']} (mit --erzwingen trotzdem übernehmen)")


def befehl_werte(a):
    with Speicher() as s:
        w = s.werte(a.id, a.von, a.bis)
    if not w:
        sys.exit(f"Keine Werte für {a.id} (geladen? python3 -m kitrader.historie laden {a.id})")
    zeige = w[-a.n:] if a.n else w
    r = REIHEN.get(a.id)
    print(f"{a.id}" + (f": {r.titel} ({r.einheit})" if r else "") + f" | {len(w)} Werte, {w[0][0]} bis {w[-1][0]}"
          + (f" | gezeigt: die letzten {len(zeige)}" if len(zeige) < len(w) else ""))
    tabelle(["Datum", "Wert"], [[d, zahl_kurz(v)] for d, v in zeige], links=(0,))


def befehl_ereignisse(a):
    liste = ereig.ereignisse(a.art, a.region, a.von, a.bis, a.suche)
    tabelle(["Beginn", "Genauigkeit", "Art", "Region", "Titel", "ID"],
            [[e["beginn"], e["genauigkeit"], e["art"], e["region"], e["titel"], e["id"]] for e in liste],
            links=(0, 1, 2, 3, 4, 5))
    arten = sorted({e["art"] for e in ereig.alle()})
    print(f"\n{len(liste)} von {len(ereig.alle())} Ereignissen. Arten: {', '.join(arten)}")


def _horizont_name(h) -> str:
    return "Tief ≤ 12 Mon." if h == "tief" else f"{h} Monat" + ("e" if h != 1 else "")


def befehl_studie(a):
    try:
        if a.art:
            _ausgabe_art(studie_art(a.art, a.reihe, a.region, auch_grobe=a.grobe))
        elif a.ereignis_id:
            _ausgabe_einzel(studie(a.ereignis_id, a.reihe))
        else:
            sys.exit("Fehler: Ereignis-ID oder --art angeben (Liste: ereignisse)")
    except (StudienFehler, KeyError) as f:
        sys.exit(f"Fehler: {f.args[0] if f.args else f}")


def _ausgabe_einzel(s: dict) -> None:
    e, r = s["ereignis"], REIHEN.get(s["reihe"])
    print(f"Ereignis  {e['titel']}  [{e['id']}]")
    print(f"Beginn    {e['beginn']} (Genauigkeit: {e['genauigkeit']})" + (f", Ende {e['ende']}" if e.get("ende") else ""))
    print(f"Art       {e['art']} | Region {e['region']}")
    print(f"Quelle    {e['quelle']['name']}")
    print(f"Reihe     {s['reihe']}" + (f": {r.titel} ({r.einheit})" if r else ""))
    if s["start"] is None:
        print("\nKeine Auswertung möglich.")
    else:
        st = s["start"]
        print(f"Startwert {zahl(st['wert'])} am {st['datum']} (letzter bekannter Wert am oder vor dem Beginn)\n")
        zeilen = []
        for h, x in s["horizonte"].items():
            if x is None or x["rendite"] is None:
                zeilen.append([_horizont_name(h), x["ziel"] if x else "", "", "", "noch nicht erreicht"])
            else:
                zeilen.append([_horizont_name(h), x["ziel"], x["datum"], zahl(x["wert"]), prozent(x["rendite"])])
        tabelle(["Horizont", "Zieltag", "Wert vom", "Wert", "Rendite"], zeilen, links=(0, 1, 2))
        t = s["tief"]
        kein = " (kein Rückgang unter den Startwert)" if t["rendite"] == 0 else ""
        print(f"\nGrößter Rückgang binnen 12 Monaten: {prozent(t['rendite'])} am {t['datum']} (Wert {zahl(t['wert'])}){kein}")
        er = s["erholung"]
        if er["erholt"]:
            print(f"Erholung: Startwert wieder erreicht am {er['datum']}, {er['tage']} Tage nach dem Beginn "
                  f"({er['tage_seit_tief']} Tage nach dem Tief)")
        else:
            print(f"Erholung: nicht erholt (Startwert bis {er['bis']} nicht wieder erreicht)")
        if s["ende"]:
            print(f"Von Beginn bis Ende ({s['ende']['ende']}): {prozent(s['ende']['rendite'])} (Wert {zahl(s['ende']['wert'])} vom {s['ende']['datum']})")
    for h in s["hinweise"]:
        print(f"Hinweis: {h}")
    print("Rückblick auf eine einzelne Episode, keine Vorhersage.")


def _ausgabe_art(s: dict) -> None:
    kopf = f"Studie Art „{s['art']}“" + (f", Region {s['region']}" if s["region"] else "")
    print(kopf + f" | Reihe: {s['reihe'] or 'Standard (dow ab 1885-02-16, davor shiller_sp)'}")
    print(f"{s['n_ereignisse']} Ereignisse in der Liste, {len(s['eintraege'])} ausgewertet\n")
    zeilen = []
    for x in s["eintraege"]:
        er = x["erholung"]
        zeilen.append([x["ereignis"]["beginn"], x["ereignis"]["titel"][:42], x["reihe"]]
                      + [prozent(x["horizonte"][h]["rendite"] if x["horizonte"][h] else None) for h in (1, 3, 6, 12)]
                      + [prozent(x["tief"]["rendite"]), f"{er['tage']} T." if er["erholt"] else "nicht erholt"])
    if zeilen:
        tabelle(["Beginn", "Ereignis", "Reihe", "1 Mon.", "3 Mon.", "6 Mon.", "12 Mon.", "Tief ≤12 M.", "Erholung"],
                zeilen, links=(0, 1, 2, 8))
    print("\nKennzahlen (Rendite ab Beginn; Tief = größter Rückgang binnen 12 Monaten):")
    zeilen = []
    for h, k in s["statistik"].items():
        if k["median"] is None:
            zeilen.append([_horizont_name(h), k["n"], "keine Aussage", "", f"(n < {s['min_n']})"])
        else:
            zeilen.append([_horizont_name(h), k["n"], prozent(k["median"]), prozent(k["mittel"]),
                           "–" if h == "tief" else f"{k['anteil_negativ'] * 100:.0f} %"])
    tabelle(["Horizont", "n", "Median", "Mittel", "Anteil negativ"], zeilen, links=(0,))
    if s["ausgelassen"]:
        print("\nNicht ausgewertet:")
        for i, grund in s["ausgelassen"]:
            print(f"  {i}: {grund}")
    print("\nEreignisse überschneiden sich zeitlich, die Werte sind nicht unabhängig. Rückblick, keine Vorhersage.")
    if len(s["reihen"]) > 1:
        print(f"Achtung: gemischte Reihen ({', '.join(s['reihen'])}); mit --reihe eine einzige wählen.")



# ---------------------------------------------------------------------------------------------- Muster-Engine

def _mz(x, art: str = "rendite") -> str:
    return mu._pz(x, art)


def _pzell(st: dict, art: str) -> str:
    """Einzelereignis: Veränderung und Perzentil in der Basis (P50 = normal, * = ungewöhnlich)."""
    if st["n"] == 0:
        return "–"
    stern = "*" if st["auffaellig"] else ""
    return f"{_mz(st['werte'][0], art)} P{st['perzentil']:.0f}{stern}"


def _ausgabe_einzel_muster(res: dict) -> None:
    e = res["ereignis"]
    print(f"Ereignis  {e['titel']}  [{e['id']}]")
    print(f"Beginn    {e['beginn']} (Genauigkeit: {e['genauigkeit']})" + (f", Ende {e['ende']}" if e.get("ende") else "")
          + f" | Art {e['art']} | Region {e['region']}")
    print(f"Quelle    {e['quelle']['name']}")
    print("Veränderung ab dem letzten Wert am oder vor dem Beginn. P = Perzentil unter allen Starttagen derselben Reihe "
          "(P50 = normal; * = unter P5 oder über P95, also ungewöhnlich).\n")
    zeilen, basiszeilen = [], []
    for g in res["gruppen"]:
        if not g["faelle"]:
            continue
        f = g["faelle"][0]
        zeilen.append([g["name"], f["start"][0]]
                      + [_pzell(g["stat"][k], g["art"]) if k in g["stat"] else "" for k, _, _, _ in mu.HORIZONTE]
                      + [_pzell(g["stat_tief"], g["art"]), _pzell(g["stat_hoch"], g["art"])])
        basiszeilen.append([g["name"], f"{g['basis']['von'][:4]}–{g['basis']['bis'][:4]}", g["basis"]["n_start"]]
                           + [("" if k not in g["stat"] else f"{_mz(g['stat'][k]['basis']['median'], g['art'])} / "
                               + ("–" if g['stat'][k]['basis']['anteil_negativ'] is None else f"{g['stat'][k]['basis']['anteil_negativ'] * 100:.0f} %"))
                              for k, _, _, _ in mu.HORIZONTE])
    if zeilen:
        tabelle(["Markt", "Start (Wert vom)"] + [h[1] for h in mu.HORIZONTE] + ["Tief ≤3 Mon.", "Hoch ≤3 Mon."], zeilen, links=(0, 1))
        print("\nZum Vergleich, normale Zeiträume derselben Reihe (Median / Anteil negativ aller Starttage):")
        tabelle(["Markt", "Zeitraum", "Starttage"] + [h[1] for h in mu.HORIZONTE], basiszeilen, links=(0, 1))
    else:
        print("Keine Reihe deckt das Ereignis ab.")
    _fuss_muster(res)


def _fuss_muster(res: dict) -> None:
    ohne = {}
    for g in res["gruppen"]:
        for i, grund in g["ohne"]:
            ohne.setdefault(g["name"], grund)
    if ohne:
        print("\nNicht gerechnet (Reihe deckt das Ereignis nicht ab): " + "; ".join(f"{n} ({gr})" for n, gr in ohne.items()))
    if res["nicht_geladen"]:
        print("Nicht geladen (python3 -m kitrader.historie laden …): " + "; ".join(n for _, n in res["nicht_geladen"]))
    if res.get("ausgelassen"):
        print("Nicht ausgewertet: " + "; ".join(f"{i} ({g})" for i, g in res["ausgelassen"]))
    if any(g["takt"] == "monat" for g in res["gruppen"]):
        print("(M) = Monatsmittel: der Wert des Beginnmonats enthält auch Tage nach dem Beginn; nur 1 und 3 Monate sind aussagekräftig.")
    for h in res.get("hinweise", []):
        print("Hinweis: " + h)
    print("Rückblick auf einzelne, im Nachhinein ausgewählte Episoden; gleichzeitige andere Ereignisse (Zinsen, Krisen, Handelsstreit) wirken mit, "
          "Ursache und Wirkung sind nicht belegt. Geschichte grenzt ein und garantiert nichts.")


def _ausgabe_menge_muster(res: dict, kopf: str) -> None:
    liste = res["ereignisse"]
    gerechnet = {f["id"] for g in res["gruppen"] for f in g["faelle"]}
    print(kopf + f" | {len(liste)} Ereignisse, {len(gerechnet)} von mindestens einer Reihe abgedeckt\n")
    if not res["gruppen"]:
        print("Keine Reihe geladen.")
        _fuss_muster(res)
        return
    hk = [(k, n) for k, n, _, _ in mu.HORIZONTE] + [("tief", "Tief ≤3 Mon."), ("hoch", "Hoch ≤3 Mon.")]

    def st(g, k):
        return g["stat_tief"] if k == "tief" else g["stat_hoch"] if k == "hoch" else g["stat"].get(k)
    print(f"Median der Veränderung ab Beginn (n = Zahl der Fälle; unter n = {res['min_n']} keine Kennzahlen, nur Einzelfälle unten):")
    zeilen = []
    for g in res["gruppen"]:
        zeilen.append([g["name"]] + ["" if st(g, k) is None else ("–" if st(g, k)["n"] == 0 else
                       f"n={st(g, k)['n']} keine Kennzahl" if st(g, k)["median"] is None else f"{_mz(st(g, k)['median'], g['art'])} (n={st(g, k)['n']})")
                       for k, _ in hk])
    tabelle(["Markt"] + [n for _, n in hk], zeilen, links=(0,))
    print("\nAnteil der Fälle mit Rückgang / Anteil in allen normalen Zeiträumen derselben Reihe:")
    zeilen = []
    for g in res["gruppen"]:
        zeilen.append([g["name"]] + [("" if st(g, k) is None or k in ("tief", "hoch") else "–" if st(g, k)["anteil_negativ"] is None else
                       f"{st(g, k)['anteil_negativ'] * 100:.0f} % / {st(g, k)['basis']['anteil_negativ'] * 100:.0f} %") for k, _ in hk])
    tabelle(["Markt"] + [n for _, n in hk], zeilen, links=(0,))
    print("\nPerzentil des Medians unter allen normalen Zeiträumen (P50 = normal); höher*/tiefer* = in einem Zufallsvergleich (1000 Stichproben "
          "mit gleich vielen zufälligen Starttagen) war der Median höchstens in 5 % so hoch bzw. so tief:")
    zeilen = []
    for g in res["gruppen"]:
        zeilen.append([g["name"]] + [("" if st(g, k) is None else "–" if st(g, k)["perzentil_median"] is None else
                       f"P{st(g, k)['perzentil_median']:.0f}" + (f" {st(g, k)['auffaellig']}*" if st(g, k)["auffaellig"] else "")) for k, _ in hk])
    tabelle(["Markt"] + [n for _, n in hk], zeilen, links=(0,))
    # Einzelfälle
    gruppen = [g for g in res["gruppen"] if g["faelle"]]
    for k, name in (("1W", "1 Woche"), ("1M", "1 Monat"), ("3M", "3 Monaten")):
        spalten = [g for g in gruppen if k in g["stat"]]
        if not spalten:
            continue
        ids = [e for e in liste if e["id"] in gerechnet]
        if len(ids) > 30:
            continue
        print(f"\nEinzelfälle, Veränderung nach {name}:")
        zeilen = []
        for e in ids:
            zeile = [e["beginn"], e["titel"][:40]]
            for g in spalten:
                f = next((f for f in g["faelle"] if f["id"] == e["id"]), None)
                zeile.append("" if f is None else _mz(f["h"].get(k), g["art"]))
            zeilen.append(zeile)
        tabelle(["Beginn", "Ereignis"] + [g["name"] for g in spalten], zeilen, links=(0, 1))
    _fuss_muster(res)
    print("Die Fälle überschneiden sich zeitlich und sind nicht unabhängig, der Zufallsvergleich zieht dagegen unabhängige Starttage. "
          "Bei so vielen Zellen fällt allein durch Zufall etwa jede zwanzigste auf (Mehrfachvergleiche).")


def befehl_muster(a):
    reihen = a.reihen.split(",") if a.reihen else None
    try:
        if a.art:
            res = mu.muster_art(a.art, a.region, reihen=reihen, grobe=a.grobe)
            _ausgabe_menge_muster(res, f"Muster Art „{a.art}“" + (f", Region {a.region}" if a.region else ""))
        elif a.ereignis_id:
            _ausgabe_einzel_muster(mu.muster_ereignis(a.ereignis_id, reihen=reihen))
        else:
            sys.exit("Fehler: Ereignis-ID oder --art angeben (Liste: ereignisse)")
    except (mu.MusterFehler, KeyError) as f:
        sys.exit(f"Fehler: {f.args[0] if f.args else f}")


def befehl_aehnlich(a):
    try:
        res = mu.aehnliche(a.text, n=a.n, reihen=a.reihen.split(",") if a.reihen else None, min_alter_tage=a.min_alter)
    except mu.MusterFehler as f:
        sys.exit(f"Fehler: {f}")
    m = res["merkmale"]
    print(f"Ähnlichkeitssuche zu: „{a.text}“")
    print("Erkannt:  Arten " + (", ".join(m["arten"]) or "keine") + " | Regionen " + (", ".join(m["regionen"]) or "keine"))
    if res["ausgeklammert"]:
        print(f"Ausgeklammert (jünger als {res['min_alter_tage']} Tage, Verlauf noch nicht belegt): "
              + "; ".join(f"{e['beginn']} {e['titel']}" for e in res["ausgeklammert"]))
    print()
    if not res["treffer"]:
        print("Keine früheren Ereignisse mit genug Übereinstimmung (mindestens Art oder Region und 2 Punkte).")
        return
    tabelle(["Rang", "Beginn", "Ereignis", "Punkte", "Begründung"],
            [[i, t["ereignis"]["beginn"], t["ereignis"]["titel"][:50], f"{t['punkte']:.1f}".replace(".", ","), "; ".join(t["gruende"])]
             for i, t in enumerate(res["treffer"], 1)], links=(0, 1, 2, 4))
    print("\nPunkte: gleiche Art 3 (verwandt bis 1,8), gleiche Region 2 (Großregion 1), Stichwörter je 0,5 mal Seltenheit (höchstens 3).\n")
    if res["muster"]:
        print(f"Für die Reaktion zählen nur Treffer mit mindestens {res['auswahl_anteil'] * 100:.0f} % der besten Punktzahl ({res['n_muster']} von {res['n']}).\n")
        _ausgabe_menge_muster(res["muster"], f"Mehrmarkt-Reaktion der {res['n_muster']} ähnlichsten Ereignisse")


def befehl_zusammenhang(a):
    try:
        z = mu.zusammenhang(aktien=a.aktien, krypto=a.krypto)
    except mu.MusterFehler as f:
        sys.exit(f"Fehler: {f}")
    k = lambda x: "–" if x is None else f"{x:+.2f}".replace(".", ",")
    print(f"Zusammenhang {z['krypto']} und {z['aktien']}: Korrelation der Tagesrenditen, {z['paare']} gemeinsame Handelstage {z['von']} bis {z['bis']}\n")
    zeilen = []
    for w, f in z["fenster"].items():
        v, vs = f["verteilung"], f["verteilung_seit"]
        zeilen.append([f"{w} Handelstage", k(f["aktuell"]), f["datum"], f"P{f['perzentil_aktuell']:.0f}", k(v["min"]), k(v["p10"]), k(v["p25"]), k(v["median"]),
                       k(v["p75"]), k(v["p90"]), k(v["max"]), f"{v['anteil_positiv'] * 100:.0f} %", k(vs.get("median")) if vs.get("n") else "–"])
    tabelle(["Fenster", "aktuell", "Stand", "Perzentil", "Min", "10 %", "25 %", "Median", "75 %", "90 %", "Max", "Anteil >0", f"Median seit {mu.JAHR_SEIT[:4]}"],
            zeilen, links=(0, 2))
    print("Perzentil = Platz des aktuellen Werts in allen Fenstern der Geschichte (P50 = mittlere Verflechtung).\n")
    jahre = sorted({j for f in z["fenster"].values() for j in f["jahre"]})
    ws = list(z["fenster"])
    print("Median der rollierenden Korrelation je Jahr:")
    tabelle(["Jahr"] + [f"{w} T" for w in ws], [[j] + [k(z["fenster"][w]["jahre"].get(j)) for w in ws] for j in jahre], links=(0,))
    print("\nStressphasen:")
    zeilen = []
    for schw, s in z["stress_tage"].items():
        zeilen.append([f"Tage mit Verlust {abs(schw) * 100:.0f} % und mehr ({z['aktien']})", s["n"],
                       "–" if s["krypto_median"] is None else prozent(s["krypto_median"]), "–" if s["krypto_mittel"] is None else prozent(s["krypto_mittel"]),
                       "–" if s["krypto_anteil_negativ"] is None else f"{s['krypto_anteil_negativ'] * 100:.0f} %",
                       f"{s['alle_anteil_negativ'] * 100:.0f} %", k(s["korrelation"])])
    tabelle(["Tage", "n", f"{z['krypto']} Median", "Mittel", "Anteil negativ", "an allen Tagen", "Korrelation an diesen Tagen"], zeilen, links=(0,))
    sp = z["stress_phasen"]
    print(f"\nRückgangsphasen ({z['aktien']} mindestens {sp['schwelle'] * 100:.0f} % unter dem Hoch der letzten 250 Handelstage): "
          f"{sp['n_stress']} Tage, Korrelation {k(sp['korrelation_stress'])}; übrige {sp['n_sonst']} Tage {k(sp['korrelation_sonst'])}.")
    for h in z["hinweise"]:
        print("Hinweis: " + h)

# ---------------------------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m kitrader.historie", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="befehl", required=True)
    sub.add_parser("reihen", help="Verzeichnis der Reihen").set_defaults(f=befehl_reihen)
    p = sub.add_parser("laden", help="Reihen abrufen")
    p.add_argument("ids", nargs="*", metavar="ID")
    p.add_argument("--erzwingen", action="store_true", help="Schutzregel aufheben (alte Daten auch bei dünnerem Abruf ersetzen)")
    p.set_defaults(f=befehl_laden)
    sub.add_parser("pruefen", help="Bestand prüfen").set_defaults(f=befehl_pruefen)
    p = sub.add_parser("einlesen", help="Datei von Hand einlesen")
    p.add_argument("id", metavar="ID")
    p.add_argument("datei")
    p.add_argument("--datum-spalte", help="Name oder Nummer (ab 1); ohne Angabe wird sie gesucht")
    p.add_argument("--wert-spalte", help="Name oder Nummer (ab 1); ohne Angabe die erste Spalte mit Zahlen")
    p.add_argument("--blatt", help="Excel: Blattname (oder Anfang davon)")
    p.add_argument("--erzwingen", action="store_true", help="Schutzregel aufheben")
    p.set_defaults(f=befehl_einlesen)
    p = sub.add_parser("werte", help="gespeicherte Werte zeigen")
    p.add_argument("id", metavar="ID")
    p.add_argument("--von")
    p.add_argument("--bis")
    p.add_argument("--n", type=int, default=20, help="die letzten n Werte zeigen (0 = alle), Standard 20")
    p.set_defaults(f=befehl_werte)
    p = sub.add_parser("ereignisse", help="Ereignisliste")
    p.add_argument("--art")
    p.add_argument("--region")
    p.add_argument("--von", help="JJJJ, JJJJ-MM oder JJJJ-MM-TT")
    p.add_argument("--bis")
    p.add_argument("--suche", help="Text in Titel, Beschreibung oder ID")
    p.set_defaults(f=befehl_ereignisse)
    p = sub.add_parser("studie", help="Ereignisstudie")
    p.add_argument("ereignis_id", nargs="?", metavar="EREIGNIS_ID")
    p.add_argument("--art", help="alle Ereignisse einer Art zusammen auswerten")
    p.add_argument("--reihe", help="Standard: dow ab 1885-02-16, davor shiller_sp")
    p.add_argument("--region")
    p.add_argument("--grobe", action="store_true", help="auch Ereignisse mit Genauigkeit quartal/jahr mitrechnen")
    p.set_defaults(f=befehl_studie)
    p = sub.add_parser("muster", help="Reaktion vieler Märkte auf ein Ereignis oder eine Ereignisart")
    p.add_argument("ereignis_id", nargs="?", metavar="EREIGNIS_ID")
    p.add_argument("--art", help="alle Ereignisse einer Art zusammen auswerten (z. B. unruhen)")
    p.add_argument("--region")
    p.add_argument("--reihen", help="nur diese Märkte, kommagetrennt (dow,btc,gold,oel,vix,zins10 …)")
    p.add_argument("--grobe", action="store_true", help="auch Ereignisse mit Genauigkeit quartal/jahr mitrechnen (nur 1 und 3 Monate)")
    p.set_defaults(f=befehl_muster)
    p = sub.add_parser("aehnlich", help="frühere Ereignisse, die zu einem Text oder zur Lage passen")
    p.add_argument("text", metavar="TEXT")
    p.add_argument("-n", type=int, default=10, help="höchstens so viele Treffer (Standard 10)")
    p.add_argument("--reihen", help="nur diese Märkte, kommagetrennt")
    p.add_argument("--min-alter", type=int, default=14, help="Ereignisse der letzten N Tage ausklammern (Standard 14)")
    p.set_defaults(f=befehl_aehnlich)
    p = sub.add_parser("zusammenhang", help="Krypto gegen Aktien: rollierende Korrelation der Tagesrenditen")
    p.add_argument("--aktien", default="dow", help="Tagesreihe der Aktien (Standard dow)")
    p.add_argument("--krypto", default="btc", help="Krypto-Reihe (Standard btc)")
    p.set_defaults(f=befehl_zusammenhang)
    a = ap.parse_args(argv)
    a.f(a)
