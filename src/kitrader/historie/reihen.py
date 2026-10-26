"""Verzeichnis der langen Kursreihen: was es gibt, woher es kommt, ab wann, unter welchen Bedingungen.

`start` ist das erwartete erste Datum laut Quelle. `python3 -m kitrader.historie pruefen` vergleicht es mit dem,
was tatsächlich geladen wurde. `abruf` = "auto" (Programm lädt selbst) oder "hand" (Datei einmal selbst
herunterladen und mit `python3 -m kitrader.historie einlesen` übernehmen, weil die Quelle automatische Abrufe sperrt
oder ein Konto verlangt). Die Daten selbst gehören nicht ins Repo (Bedingungen der Quellen).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reihe:
    id: str
    titel: str
    einheit: str
    takt: str            # "tag" oder "monat"
    start: str           # erwartetes erstes Datum (ISO)
    lader: str           # Name des Abrufers in laden.py; "hand" = nur einlesen
    quelle: str
    url: str             # Seite der Quelle mit Bedingungen
    lizenz: str
    hinweis: str = ""
    endet: str = ""      # Reihe wird nicht mehr fortgeschrieben (ISO), sonst leer
    feld: str = ""       # Spalte bzw. Kürzel innerhalb der Quelle


_SHILLER = dict(lader="shiller", takt="monat", start="1871-01-01", quelle="Robert J. Shiller, Online Data",
                url="https://shillerdata.com/", lizenz="frei zugänglich; Quelle nennen, privater Gebrauch")
_FRED = dict(lader="fred", quelle="Federal Reserve Bank of St. Louis, FRED", url="https://fred.stlouisfed.org/",
             lizenz="frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch")
_BOE = dict(lader="boe", quelle="Bank of England, A millennium of macroeconomic data (v3.1)",
            url="https://www.bankofengland.co.uk/statistics/research-datasets",
            lizenz="Bank of England; Quelle nennen, privater Gebrauch")
_WELTBANK = dict(
    quelle="World Bank, Commodity Price Data (The Pink Sheet), monatlich",
    url="https://www.worldbank.org/en/research/commodity-markets",
    lizenz="Weltbank „Terms of use for Datasets“: Standard CC BY 4.0 (https://datacatalog.worldbank.org/public-licenses#cc-by); "
           "Namensnennung „World Bank Commodity Price Data (The Pink Sheet)“, Änderungen kenntlich machen",
    hinweis="Monatsmittel in nominalen US-Dollar je Feinunze (troy oz), Datum = 1. des Monats; die Datei nennt Monate wie „1960M01“. "
            "Die Weltbank rundet Gold und Platin auf ganze Dollar, Silber auf 0,1 Dollar. Der jüngste Monat kann sich noch ändern.")
_STOOQ = dict(
    quelle="Stooq", url="https://stooq.com/", lizenz="Bedingungen von Stooq, privater Gebrauch",
    hinweis="Braucht seit März 2026 einen kostenlosen API-Schlüssel (STOOQ_APIKEY oder ~/.config/kitrader/stooq.env). "
            "Startdatum noch nicht geprüft; Stooq sperrt Abrufe aus Rechenzentren.")


def _r(**k) -> Reihe:
    return Reihe(**k)


REIHEN: dict[str, Reihe] = {r.id: r for r in [
    # --- USA, Aktien und Bewertung (Shiller, monatlich, Kurs = Monatsmittel der Tagesschlusskurse)
    _r(id="shiller_sp", titel="S&P Composite (Shiller)", einheit="Index", feld="P", **_SHILLER,
       hinweis="Monatsmittel der Tagesschlusskurse; der jüngste Monat ist vorläufig."),
    _r(id="shiller_dividende", titel="S&P Dividende je Anteil (Shiller)", einheit="USD", feld="D", **_SHILLER,
       hinweis="Die letzten Monate fehlen meist, bis S&P die Zahlen meldet."),
    _r(id="shiller_gewinn", titel="S&P Gewinn je Anteil (Shiller)", einheit="USD", feld="E", **_SHILLER,
       hinweis="Die letzten Monate fehlen meist, bis S&P die Zahlen meldet."),
    _r(id="shiller_cpi", titel="US-Verbraucherpreise (Shiller)", einheit="Index", feld="CPI", **_SHILLER),
    _r(id="shiller_zins10", titel="US-Zins 10 Jahre (Shiller, GS10)", einheit="%", feld="GS10", **_SHILLER),
    _r(id="shiller_cape", titel="CAPE, zyklisch bereinigtes KGV (Shiller)", einheit="Verhältnis", feld="CAPE", **_SHILLER,
       hinweis="Beginnt erst 1881, weil zehn Jahre Gewinne nötig sind."),
    _r(id="shiller_real_tr", titel="S&P real, mit Dividenden (Shiller)", einheit="Index", feld="Real Total Return Price", **_SHILLER),

    # --- Dow Jones täglich (MeasuringWorth)
    _r(id="dow", titel="Dow Jones Average / DJIA, täglich", einheit="Index", takt="tag", start="1885-02-16",
       lader="measuringworth", quelle="Samuel H. Williamson, MeasuringWorth",
       url="https://www.measuringworth.com/datasets/DJA/",
       lizenz="frei zugänglich; Zitierpflicht: „Daily Closing Value of the Dow Jones Average, 1885 to Present“, privater Gebrauch",
       hinweis="Vor dem 07.10.1896 der einzelne Dow Jones Average, bis 30.07.1914 die erste DJIA, angeglichen. "
               "NYSE vom 31.07. bis 11.12.1914 geschlossen (echte Lücke)."),

    # --- Großbritannien (Bank of England, endet 2017)
    _r(id="uk_aktien", titel="UK-Aktienindex monatlich ab 1709 (verkettet)", einheit="Index (10.04.1962 = 100)", takt="monat",
       start="1709-04-01", endet="2017-04-01", feld="M13 Spalte X", **_BOE,
       hinweis="Verkettet aus Neal, Gayer/Rostow/Schwarz, Smith/Horne, Bankers' Magazine, Actuaries und FTSE All-Share."),
    _r(id="uk_leitzins", titel="Bank Rate / Leitzins UK, täglich", einheit="%", takt="tag", start="1833-01-01",
       endet="2017-12-31", feld="D1 Spalte C", **_BOE,
       hinweis="Bank Rate 1833–1972, Minimum Lending Rate 1972–1981 usw. laut Blatt D1."),

    # --- USA, Zinsen, Preise, Konjunktur (FRED)
    _r(id="us_rezession", titel="US-Rezession laut NBER (1 = Rezession)", einheit="0/1", takt="monat", start="1854-12-01", feld="USREC", **_FRED),
    _r(id="us_leitzins", titel="US-Leitzins (Federal Funds Rate)", einheit="%", takt="monat", start="1954-07-01", feld="FEDFUNDS", **_FRED),
    _r(id="us_zins10_tag", titel="US-Zins 10 Jahre, täglich", einheit="%", takt="tag", start="1962-01-02", feld="DGS10", **_FRED),
    _r(id="us_zins3m", titel="US-Zins 3 Monate (T-Bill)", einheit="%", takt="monat", start="1934-01-01", feld="TB3MS", **_FRED),
    _r(id="us_zinskurve", titel="US-Zinskurve 10 J. minus 2 J.", einheit="Prozentpunkte", takt="tag", start="1976-06-01", feld="T10Y2Y", **_FRED),
    _r(id="us_cpi", titel="US-Verbraucherpreise (CPI-U, unbereinigt)", einheit="Index", takt="monat", start="1913-01-01", feld="CPIAUCNS", **_FRED),
    _r(id="us_arbeitslos", titel="US-Arbeitslosenquote", einheit="%", takt="monat", start="1948-01-01", feld="UNRATE", **_FRED),
    _r(id="us_m2", titel="US-Geldmenge M2", einheit="Mrd. USD", takt="monat", start="1959-01-01", feld="M2SL", **_FRED),
    _r(id="oel_wti_monat", titel="Ölpreis WTI, monatlich", einheit="USD je Barrel", takt="monat", start="1946-01-01", feld="WTISPLC", **_FRED),
    _r(id="oel_wti", titel="Ölpreis WTI, täglich", einheit="USD je Barrel", takt="tag", start="1986-01-02", feld="DCOILWTICO", **_FRED,
       hinweis="Am 20.04.2020 negativ (−36,98); das ist kein Datenfehler."),
    _r(id="vix", titel="VIX Volatilitätsindex", einheit="Index", takt="tag", start="1990-01-02", feld="VIXCLS", **_FRED),
    _r(id="eurusd", titel="Euro in US-Dollar", einheit="USD je EUR", takt="tag", start="1999-01-04", feld="DEXUSEU", **_FRED),
    _r(id="de_zins10", titel="Deutschland, Zins langfristig (OECD)", einheit="%", takt="monat", start="1956-05-01",
       feld="IRLTLT01DEM156N", **_FRED, hinweis="OECD-Reihe über FRED; Startdatum ungeprüft."),

    # --- Krypto
    _r(id="btc", titel="Bitcoin in US-Dollar, täglich (Coin Metrics)", einheit="USD", takt="tag", start="2010-07-18",
       lader="coinmetrics", feld="PriceUSD", quelle="Coin Metrics, Community Network Data (PriceUSD)",
       url="https://coinmetrics.io/community-network-data/",
       lizenz="CC BY-NC 4.0 (https://creativecommons.org/licenses/by-nc/4.0/): Namensnennung „Coin Metrics“, nur nicht kommerziell",
       hinweis="Referenzkurs von Coin Metrics, Tageswert mit Zeitstempel 00:00 UTC, ab dem 18.07.2010 (Mt. Gox startet am 17.07.2010). "
               "Bei Abweichungen gilt Coin Metrics als Hauptquelle; zum Vergleich gibt es btc_blockchain."),
    _r(id="btc_blockchain", titel="Bitcoin in US-Dollar, täglich (Blockchain.com, Ersatz)", einheit="USD", takt="tag",
       start="2010-08-18", lader="blockchain", quelle="Blockchain.com Charts API",
       url="https://www.blockchain.com/explorer/charts/market-price",
       lizenz="Nutzungsbedingungen von Blockchain.com, privater Gebrauch",
       hinweis="Ersatzquelle zu btc. Mittelwert großer Börsen. Frei abrufbar erst ab 18.08.2010."),

    # --- Edelmetalle monatlich ab 1960 (Weltbank, Pink Sheet)
    _r(id="gold_monat", titel="Gold in US-Dollar je Feinunze, Monatsmittel", einheit="USD je Feinunze", takt="monat",
       start="1960-01-01", lader="weltbank", feld="Gold", **_WELTBANK),
    _r(id="silber_monat", titel="Silber in US-Dollar je Feinunze, Monatsmittel", einheit="USD je Feinunze", takt="monat",
       start="1960-01-01", lader="weltbank", feld="Silver", **_WELTBANK),
    _r(id="platin_monat", titel="Platin in US-Dollar je Feinunze, Monatsmittel", einheit="USD je Feinunze", takt="monat",
       start="1960-01-01", lader="weltbank", feld="Platinum", **_WELTBANK),

    # --- Edelmetalle täglich (Stooq; braucht seit März 2026 einen kostenlosen API-Schlüssel, in der Cloud gesperrt)
    _r(id="silber", titel="Silber in US-Dollar je Unze", einheit="USD", takt="tag", start="2000-01-03", lader="stooq", feld="xagusd", **_STOOQ),
    _r(id="platin", titel="Platin in US-Dollar je Unze", einheit="USD", takt="tag", start="2000-01-03", lader="stooq", feld="xptusd", **_STOOQ),
    _r(id="palladium", titel="Palladium in US-Dollar je Unze", einheit="USD", takt="tag", start="2000-01-03", lader="stooq", feld="xpdusd", **_STOOQ),

    # --- von Hand einzulesen
    _r(id="gold", titel="Gold in US-Dollar je Unze, täglich", einheit="USD", takt="tag", start="1978-12-29", lader="hand",
       quelle="World Gold Council, Goldhub", url="https://www.gold.org/goldhub/data/gold-prices",
       lizenz="Bedingungen des World Gold Council (Konto), privater Gebrauch",
       hinweis="Datei einmal selbst herunterladen, dann: python3 -m kitrader.historie einlesen gold DATEI --wert-spalte USD"),
    _r(id="dax", titel="DAX, Rückrechnung ab 1948", einheit="Index", takt="monat", start="1948-01-01", lader="hand",
       quelle="Prof. Richard Stehle, Humboldt-Universität zu Berlin", url="https://www.wiwi.hu-berlin.de/de/professuren/bwl/bb/data",
       lizenz="wissenschaftliche Daten; Bedingungen auf der Seite, privater Gebrauch",
       hinweis="Seite sperrt automatische Abrufe (Bot-Schutz). Datei im Browser laden, dann: python3 -m kitrader.historie einlesen dax DATEI"),
]}


def lader_gruppen() -> dict[str, list[Reihe]]:
    g: dict[str, list[Reihe]] = {}
    for r in REIHEN.values():
        g.setdefault(r.lader, []).append(r)
    return g
