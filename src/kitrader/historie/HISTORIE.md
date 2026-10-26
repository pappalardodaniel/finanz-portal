# Historie: lange Kurs- und Zinsreihen, Ereignisse, Ereignisstudie

Dieses Paket (`kitrader.historie`) ist **eigenständig**: Es braucht nur Python ab 3.11 und die Standardbibliothek und benutzt
keinen Code aus anderen Teilen des Repos. Es lädt lange Reihen (Aktien ab 1709, Dow Jones ab 1885, Zinsen, Preise, Bitcoin …) aus
frei zugänglichen Quellen, speichert sie in einer eigenen SQLite-Datei, kennt eine Liste von Ereignissen (Krisen, Zinsschocks,
Rezessionen …) und rechnet, was eine Reihe nach einem Ereignis tat.

**Das Repo enthält nur Code, keine Marktdaten.** Wer die Daten abruft, akzeptiert die Bedingungen der jeweiligen Quelle (Spalte
„Bedingung“ unten) und ist selbst dafür verantwortlich. Alles hier ist privater Rückblick, keine Anlageberatung und keine Vorhersage.

## Schnellstart

```bash
export PYTHONPATH=src                       # im Repo-Ordner
python3 -m kitrader.historie reihen        # was es gibt
python3 -m kitrader.historie laden         # alle automatischen Reihen abrufen
python3 -m kitrader.historie pruefen       # was ist angekommen, was fehlt, wo sind Lücken
python3 -m kitrader.historie studie 1929-boersenkrach
python3 -m kitrader.historie studie --art bankenkrise
python3 -m kitrader.historie muster --art unruhen            # viele Märkte, Vergleich mit normalen Zeiträumen
python3 -m kitrader.historie aehnlich "Ausschreitungen in Frankreich"
python3 -m kitrader.historie zusammenhang                    # Bitcoin gegen Dow
```

Die Daten liegen in `$KITRADER_DATEN/historie.db` (Standard `~/.local/share/kitrader/historie.db`; der ältere Name `HISTORIE_DATEN` gilt weiter, wenn `KITRADER_DATEN` fehlt), nie im Repo. SQLite im WAL-Modus,
`busy_timeout` 30 s. Tabellen: `historie (reihe, datum, wert)`, `historie_stand (reihe, quelle, abgerufen, anzahl, erstes, letztes, hinweis)`
und `datenhinweise (thema, hinweis)` mit den Stolpersteinen aus diesem Dokument, damit spätere Auswertungen (auch durch eine KI) sie mitlesen.

## Reihen

Abruf „automatisch“: `laden` holt die Reihe selbst. Abruf „von Hand“: die Quelle verlangt ein Konto oder sperrt Programme; die Datei
einmal im Browser laden und mit `einlesen` übernehmen. Das Startdatum ist das laut Quelle erwartete erste Datum; `pruefen` vergleicht es mit
dem, was geladen wurde.

| ID | Titel | Takt | Start | Abruf | Quelle | Bedingung |
|---|---|---|---|---|---|---|
| `shiller_sp` | S&P Composite (Shiller) | monat | 1871-01-01 | automatisch | [Robert J. Shiller, Online Data](https://shillerdata.com/) | frei zugänglich; Quelle nennen, privater Gebrauch |
| `shiller_dividende` | S&P Dividende je Anteil (Shiller) | monat | 1871-01-01 | automatisch | [Robert J. Shiller, Online Data](https://shillerdata.com/) | frei zugänglich; Quelle nennen, privater Gebrauch |
| `shiller_gewinn` | S&P Gewinn je Anteil (Shiller) | monat | 1871-01-01 | automatisch | [Robert J. Shiller, Online Data](https://shillerdata.com/) | frei zugänglich; Quelle nennen, privater Gebrauch |
| `shiller_cpi` | US-Verbraucherpreise (Shiller) | monat | 1871-01-01 | automatisch | [Robert J. Shiller, Online Data](https://shillerdata.com/) | frei zugänglich; Quelle nennen, privater Gebrauch |
| `shiller_zins10` | US-Zins 10 Jahre (Shiller, GS10) | monat | 1871-01-01 | automatisch | [Robert J. Shiller, Online Data](https://shillerdata.com/) | frei zugänglich; Quelle nennen, privater Gebrauch |
| `shiller_cape` | CAPE, zyklisch bereinigtes KGV (Shiller) | monat | 1871-01-01 | automatisch | [Robert J. Shiller, Online Data](https://shillerdata.com/) | frei zugänglich; Quelle nennen, privater Gebrauch |
| `shiller_real_tr` | S&P real, mit Dividenden (Shiller) | monat | 1871-01-01 | automatisch | [Robert J. Shiller, Online Data](https://shillerdata.com/) | frei zugänglich; Quelle nennen, privater Gebrauch |
| `dow` | Dow Jones Average / DJIA, täglich | tag | 1885-02-16 | automatisch | [Samuel H. Williamson, MeasuringWorth](https://www.measuringworth.com/datasets/DJA/) | frei zugänglich; Zitierpflicht: „Daily Closing Value of the Dow Jones Average, 1885 to Present“, privater Gebrauch |
| `uk_aktien` | UK-Aktienindex monatlich ab 1709 (verkettet) (endet 2017-04) | monat | 1709-04-01 | automatisch | [Bank of England, A millennium of macroeconomic data (v3.1)](https://www.bankofengland.co.uk/statistics/research-datasets) | Bank of England; Quelle nennen, privater Gebrauch |
| `uk_leitzins` | Bank Rate / Leitzins UK, täglich (endet 2017-12) | tag | 1833-01-01 | automatisch | [Bank of England, A millennium of macroeconomic data (v3.1)](https://www.bankofengland.co.uk/statistics/research-datasets) | Bank of England; Quelle nennen, privater Gebrauch |
| `us_rezession` | US-Rezession laut NBER (1 = Rezession) | monat | 1854-12-01 | automatisch | [Federal Reserve Bank of St. Louis, FRED](https://fred.stlouisfed.org/) | frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch |
| `us_leitzins` | US-Leitzins (Federal Funds Rate) | monat | 1954-07-01 | automatisch | [Federal Reserve Bank of St. Louis, FRED](https://fred.stlouisfed.org/) | frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch |
| `us_zins10_tag` | US-Zins 10 Jahre, täglich | tag | 1962-01-02 | automatisch | [Federal Reserve Bank of St. Louis, FRED](https://fred.stlouisfed.org/) | frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch |
| `us_zins3m` | US-Zins 3 Monate (T-Bill) | monat | 1934-01-01 | automatisch | [Federal Reserve Bank of St. Louis, FRED](https://fred.stlouisfed.org/) | frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch |
| `us_zinskurve` | US-Zinskurve 10 J. minus 2 J. | tag | 1976-06-01 | automatisch | [Federal Reserve Bank of St. Louis, FRED](https://fred.stlouisfed.org/) | frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch |
| `us_cpi` | US-Verbraucherpreise (CPI-U, unbereinigt) | monat | 1913-01-01 | automatisch | [Federal Reserve Bank of St. Louis, FRED](https://fred.stlouisfed.org/) | frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch |
| `us_arbeitslos` | US-Arbeitslosenquote | monat | 1948-01-01 | automatisch | [Federal Reserve Bank of St. Louis, FRED](https://fred.stlouisfed.org/) | frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch |
| `us_m2` | US-Geldmenge M2 | monat | 1959-01-01 | automatisch | [Federal Reserve Bank of St. Louis, FRED](https://fred.stlouisfed.org/) | frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch |
| `oel_wti_monat` | Ölpreis WTI, monatlich | monat | 1946-01-01 | automatisch | [Federal Reserve Bank of St. Louis, FRED](https://fred.stlouisfed.org/) | frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch |
| `oel_wti` | Ölpreis WTI, täglich | tag | 1986-01-02 | automatisch | [Federal Reserve Bank of St. Louis, FRED](https://fred.stlouisfed.org/) | frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch |
| `vix` | VIX Volatilitätsindex | tag | 1990-01-02 | automatisch | [Federal Reserve Bank of St. Louis, FRED](https://fred.stlouisfed.org/) | frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch |
| `eurusd` | Euro in US-Dollar | tag | 1999-01-04 | automatisch | [Federal Reserve Bank of St. Louis, FRED](https://fred.stlouisfed.org/) | frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch |
| `de_zins10` | Deutschland, Zins langfristig (OECD) | monat | 1956-05-01 | automatisch | [Federal Reserve Bank of St. Louis, FRED](https://fred.stlouisfed.org/) | frei; einige Reihen gehören Dritten (Bedingungen je Reihe auf FRED), privater Gebrauch |
| `btc` | Bitcoin in US-Dollar, täglich (Coin Metrics) | tag | 2010-07-18 | automatisch | [Coin Metrics, Community Network Data (PriceUSD)](https://coinmetrics.io/community-network-data/) | CC BY-NC 4.0 (https://creativecommons.org/licenses/by-nc/4.0/): Namensnennung „Coin Metrics“, nur nicht kommerziell |
| `btc_blockchain` | Bitcoin in US-Dollar, täglich (Blockchain.com, Ersatz) | tag | 2010-08-18 | automatisch | [Blockchain.com Charts API](https://www.blockchain.com/explorer/charts/market-price) | Nutzungsbedingungen von Blockchain.com, privater Gebrauch |
| `gold_monat` | Gold in US-Dollar je Feinunze, Monatsmittel | monat | 1960-01-01 | automatisch | [World Bank, Commodity Price Data (The Pink Sheet), monatlich](https://www.worldbank.org/en/research/commodity-markets) | Weltbank „Terms of use for Datasets“: Standard CC BY 4.0 (https://datacatalog.worldbank.org/public-licenses#cc-by); Namensnennung „World Bank Commodity Price Data (The Pink Sheet)“, Änderungen kenntlich machen |
| `silber_monat` | Silber in US-Dollar je Feinunze, Monatsmittel | monat | 1960-01-01 | automatisch | [World Bank, Commodity Price Data (The Pink Sheet), monatlich](https://www.worldbank.org/en/research/commodity-markets) | Weltbank „Terms of use for Datasets“: Standard CC BY 4.0 (https://datacatalog.worldbank.org/public-licenses#cc-by); Namensnennung „World Bank Commodity Price Data (The Pink Sheet)“, Änderungen kenntlich machen |
| `platin_monat` | Platin in US-Dollar je Feinunze, Monatsmittel | monat | 1960-01-01 | automatisch | [World Bank, Commodity Price Data (The Pink Sheet), monatlich](https://www.worldbank.org/en/research/commodity-markets) | Weltbank „Terms of use for Datasets“: Standard CC BY 4.0 (https://datacatalog.worldbank.org/public-licenses#cc-by); Namensnennung „World Bank Commodity Price Data (The Pink Sheet)“, Änderungen kenntlich machen |
| `silber` | Silber in US-Dollar je Unze | tag | 2000-01-03 | automatisch | [Stooq](https://stooq.com/) | Bedingungen von Stooq, privater Gebrauch |
| `platin` | Platin in US-Dollar je Unze | tag | 2000-01-03 | automatisch | [Stooq](https://stooq.com/) | Bedingungen von Stooq, privater Gebrauch |
| `palladium` | Palladium in US-Dollar je Unze | tag | 2000-01-03 | automatisch | [Stooq](https://stooq.com/) | Bedingungen von Stooq, privater Gebrauch |
| `gold` | Gold in US-Dollar je Unze, täglich | tag | 1978-12-29 | von Hand | [World Gold Council, Goldhub](https://www.gold.org/goldhub/data/gold-prices) | Bedingungen des World Gold Council (Konto), privater Gebrauch |
| `dax` | DAX, Rückrechnung ab 1948 | monat | 1948-01-01 | von Hand | [Prof. Richard Stehle, Humboldt-Universität zu Berlin](https://www.wiwi.hu-berlin.de/de/professuren/bwl/bb/data) | wissenschaftliche Daten; Bedingungen auf der Seite, privater Gebrauch |

Je Quelle wird einmal abgerufen (Shiller und Bank of England liefern mehrere Reihen aus einer Datei). FRED wird Reihe für Reihe mit
Pause abgerufen, weil FRED parallele Abrufe abweist. Scheitert eine Quelle, laufen die anderen weiter; nach drei Fehlern in Folge
bei FRED oder Stooq werden die übrigen Reihen dieser Quelle nicht mehr versucht.

**Bitcoin.** `btc` kommt von Coin Metrics (Community API, Kennzahl `PriceUSD`, Tageswerte ab 2010-07-18, eine Seite mit `page_size=10000`;
ein `next_page_url` würde verfolgt). Lizenz **CC BY-NC 4.0**: nur nicht kommerziell, Namensnennung **„Coin Metrics“**. `btc_blockchain`
(Blockchain.com, ab 2010-08-18) bleibt als Ersatz und zum Vergleich, damit bei einem Ausfall oder einer Abweichung eine zweite Reihe da ist.

**Edelmetalle monatlich ab 1960.** `gold_monat`, `silber_monat` und `platin_monat` kommen aus der Datei `CMO-Historical-Data-Monthly.xlsx`
(Blatt „Monthly Prices“, „The Pink Sheet“) der Weltbank. Der Link enthält einen wechselnden Hash; `laden` liest ihn jedes Mal von
`https://www.worldbank.org/en/research/commodity-markets`. Die Spalten werden über den Kopf (Gold, Silver, Platinum, Einheit „$/troy oz“)
gefunden; stimmt der Kopf nicht, bricht der Abruf mit einer Meldung ab, statt falsche Spalten zu lesen. Werte sind **Monatsmittel in
nominalen US-Dollar je Feinunze**, das Datum ist der 1. des Monats (Quelle: „1960M01“). Die Datei selbst rundet Gold und Platin auf ganze
Dollar und Silber auf 0,1 Dollar; für Rückblicke über Jahrzehnte reicht das, für feine Vergleiche nicht. Bedingungen: Die Weltbank stellt
ihre Datensätze nach den „Terms of use for Datasets“ standardmäßig unter **CC BY 4.0** (Namensnennung, Änderungen kenntlich machen); die
Seite verlinkt sie unter „Terms of use for Datasets“ und „Data Access and Licensing“. Namensnennung: **World Bank Commodity Price Data
(The Pink Sheet)**. Die Bedingungen gelten für den Abruf, nicht für dieses Repo; die Daten selbst liegen nicht im Repo.

**Stooq braucht einen Schlüssel.** Seit März 2026 liefert Stooq CSV nur noch mit kostenlosem API-Schlüssel (siehe das Ticket
<https://github.com/pydata/pandas-datareader/issues/1012>). Den Schlüssel holt man im Browser auf
<https://stooq.com/q/d/?s=spy.us&get_apikey>. `laden` liest ihn aus der Umgebungsvariable `STOOQ_APIKEY` oder aus der Zeile
`STOOQ_APIKEY=…` in `~/.config/kitrader/stooq.env` (die Umgebung gewinnt; die Datei gehört nicht ins Repo, `*.env` ist ausgenommen).
Ohne Schlüssel ruft das Programm Stooq gar nicht erst ab und nennt diese Anleitung. Der Schlüssel wird als Parameter `apikey` an die
Abruf-URL gehängt und nie gespeichert oder in Meldungen gezeigt (dort steht `apikey=***`). **Ungeprüft:** Der Parametername `apikey` ist
aus dem Ticket nur vermutet (es nennt ihn nicht); er steht als Konstante `STOOQ_URL` in `laden.py` und lässt sich dort ändern.

Der Abruf nennt sich ehrlich `historie/0.1 (privates Lernprojekt; urllib)`. Die Bank of England lehnte Anfragen ab, deren User-Agent den
Text „Python-urllib“ enthält (HTTP 403); mit dem eigenen Namen geht es. Es wird nichts umgangen: Sperren, Bot-Schutz und Konten werden
gemeldet, nicht ausgetrickst.

**Stand der Prüfung:** Shiller, Dow Jones (MeasuringWorth), Bank of England, Blockchain.com, Coin Metrics und die Weltbank-Datei wurden
mit echten Abrufen getestet. FRED und Stooq ließen sich aus der Cloud-Umgebung, in der das Paket gebaut wurde, nicht erreichen
(Verbindungsabbruch). Ihre Parser sind nur mit erfundenen Beispielzeilen im dokumentierten Format getestet; bei Stooq ist zusätzlich
der Parametername des Schlüssels ungeprüft. Bitte vom eigenen Rechner einmal `laden` und `pruefen` laufen lassen.

## Befehle

| Befehl | Zweck |
|---|---|
| `reihen` | Verzeichnis der Reihen |
| `laden [ID …] [--erzwingen]` | Reihen abrufen und speichern; ohne ID alle automatischen |
| `pruefen` | Bestand gegen das Verzeichnis prüfen |
| `einlesen ID DATEI [--datum-spalte S] [--wert-spalte S] [--blatt B] [--erzwingen]` | Datei von Hand einlesen |
| `werte ID [--von D] [--bis D] [--n N]` | gespeicherte Werte zeigen (Standard: die letzten 20) |
| `ereignisse [--art A] [--region R] [--von D] [--bis D] [--suche T]` | Ereignisliste |
| `studie EREIGNIS_ID [--reihe ID]` | Studie zu einem Ereignis |
| `studie --art ART [--reihe ID] [--region R] [--grobe]` | Studie über alle Ereignisse einer Art |
| `muster EREIGNIS_ID [--reihen a,b]` | Reaktion vieler Märkte (Dow, S&P, DAX, UK, Bitcoin, Gold, Silber, Platin, Öl, VIX, EUR/USD, US-Zins) nach 1 Tag, 1 Woche, 1 Monat, 3 Monaten, mit Vergleich zu normalen Zeiträumen |
| `muster --art ART [--region R] [--reihen …] [--grobe]` | dasselbe für alle Ereignisse einer Art (z. B. `unruhen`) |
| `aehnlich "TEXT" [-n 10] [--min-alter 14]` | frühere Ereignisse, die zu Text oder Lage passen, mit Begründung und Mehrmarkt-Reaktion |
| `zusammenhang [--aktien dow] [--krypto btc]` | Bitcoin gegen Dow: rollierende Korrelation der Tagesrenditen (30/90 Handelstage), aktuell, historisch, in Stressphasen |

Aufruf immer als `python3 -m kitrader.historie <Befehl>`. Aus Python: `from kitrader.historie.laden import laden`,
`pruefen.pruefen()`, `einlesen.einlesen(...)`, `ereignisse.ereignisse(...)`, `studie.studie(...)`, `studie.studie_art(...)`,
`muster.muster_ereignis(...)`, `muster.muster_art(...)`, `muster.aehnliche(...)`, `muster.zusammenhang()`, `muster.kontext_text(...)`,
`speicher.Speicher().werte(reihe, von, bis)`.

## Schutzregel beim Speichern

Eine Reihe wird nur ersetzt, wenn der neue Abruf **mindestens 90 % der alten Anzahl** hat, **nicht später beginnt** und **nicht mehr als
45 Tage früher endet** als der alte Bestand. Sonst bleiben die alten Daten, und `laden` meldet „geschützt“ mit dem Grund. Das schützt vor
halb ausgelieferten Dateien, geänderten Quellen und veralteten Ersatzquellen (z. B. der Yale-Kopie der Shiller-Datei, die 2023-09 endet).
`--erzwingen` hebt die Regel bewusst auf.

## Prüfen

`pruefen` zeigt je Reihe aus dem Verzeichnis: `fehlt`, `ok`, `beginnt später als erwartet` (mehr als 31 Tage nach dem Startdatum) und/oder
`veraltet` (letztes Datum älter als 10 Tage bei Tagesreihen, 75 Tage bei Monatsreihen; nicht bei Reihen mit festem Ende), dazu Anzahl,
erstes und letztes Datum und die größte Lücke (Tagesreihen: Tage zwischen zwei Werten, Monatsreihen: fehlende Monate). Die Lücken werden
nur angezeigt, nicht bewertet: Eine Börsenschließung (Dow 31.07. bis 11.12.1914) sieht aus wie ein Datenloch.

## Von Hand einlesen: Gold und DAX

Zwei Reihen gibt es nicht automatisch:

- **Gold** (`gold`): World Gold Council, Goldhub, „Gold prices“ (Konto nötig, Bedingungen des WGC beachten). Excel-Datei laden, dann
  `python3 -m kitrader.historie einlesen gold DATEI.xlsx --wert-spalte USD`. Enthält die Datei mehrere Währungen und Blätter, mit
  `--blatt` und `--wert-spalte` wählen.
- **DAX ab 1948** (`dax`): Rückrechnung von Prof. Richard Stehle, Humboldt-Universität zu Berlin (Seite sperrt Programme, Datei im Browser
  laden), dann `python3 -m kitrader.historie einlesen dax DATEI`.

`einlesen` versteht CSV (Trenner `;`, `,` oder Tab werden erkannt; Dezimalkomma und Tausenderpunkt), `.xlsx` und `.xls`. Es sucht die Kopfzeile,
nimmt als Datum die erste Spalte mit Datumswerten und als Wert die erste Spalte mit Zahlen; mit `--datum-spalte` und `--wert-spalte` wählt man
per Name (Anfang genügt) oder Nummer (ab 1). Datumsformate: `JJJJ-MM-TT`, `TT.MM.JJJJ`, `M/T/JJJJ` (amerikanisch), `JJJJ-MM` (Monatsanfang)
und Excel-Seriennummern. Ein einzelnes Komma in einer Zahl gilt als Dezimalkomma (`1,234` ist 1,234). Das Ergebnis nennt die gewählten Spalten,
damit man falsche Wahlen sofort sieht. Die Schutzregel gilt auch hier.

## Ereignisliste

`ereignisse.json` (neben dem Code, eine Zeile je Ereignis) enthält 342 Ereignisse. Felder: `id`, `beginn` (ISO-Datum), `ende` (oder `null`),
`genauigkeit` (`tag`, `monat`, `quartal`, `jahr`), `art`, `region`, `titel`, `beschreibung` und `quelle` (`name`, `url`, `abgerufen`).
Bei Genauigkeit unter `tag` ist `beginn` ein Platzhalter (1. des Monats bzw. Jahres). Verteilung der Genauigkeit: jahr 5, monat 48, quartal 6, tag 283.
Arten: `bankenkrise` (19), `boersencrash` (10), `finanzkrise` (9), `geldpolitik` (15), `handel` (12), `inflation` (1), `konflikt` (34), `krieg` (47), `krypto` (4), `naturkatastrophe` (16), `pandemie` (17), `politik` (12), `regulierung` (4), `rezession` (37), `rohstoffschock` (24), `sanktionen` (3), `schuldenkrise` (3), `terror` (15), `unglueck` (10), `unruhen` (19), `waehrung` (16), `wahl` (6), `zinsschock` (9).

Abfrage: `ereignisse --art bankenkrise --von 1929 --bis 1935`, `--region Deutschland` (Teiltreffer, `USA` findet auch `USA/China`),
`--suche Lehman`. `pruefe_ereignisse()` prüft die Datei (Pflichtfelder, ISO-Daten, Genauigkeit, Quellen-URL mit `http`, doppelte IDs,
Sortierung nach Beginn); der Test ruft sie auf der ausgelieferten Datei auf. Die Beschreibungen sind knapp und mit einer Quelle belegt,
aber von Hand zusammengetragen: bitte bei wichtigen Aussagen die Quelle selbst lesen.

**Art `unruhen`** (Unruhen, Proteste, Generalstreiks; 19 Ereignisse von Mai 1968 bis Oktober 2026, alle mit Tagesgenauigkeit): Mai 68 Frankreich, Banlieue 2005,
Tunesien 17.12.2010, Ägypten 25.01.2011, England 06.08.2011, Occupy 17.09.2011, Gelbwesten 17.11.2018, Hongkong 09.06.2019, Chile 18.10.2019,
USA/Floyd 26.05.2020, Kapitol 06.01.2021, Sri Lanka 31.03.2022, Rentenreform Frankreich 19.01.2023, Frankreich/Nahel 27.06.2023, Kenia 18.06.2024,
Los Angeles 06.06.2025, Nepal 08.09.2025, Iran 28.12.2025, Frankreich 17.10.2026. **Quellen** (am 26.10.2026 geöffnet und auf das Datum geprüft):
amtliche oder zwischenstaatliche Stellen (Assemblée nationale, Sénat, französisches Außenministerium, UK Home Office und Foreign Office, US-Außenministerium,
US-Justizministerium, US-Repräsentantenhaus, Berufungsgericht, New York City Council, UN-Menschenrechtsrat/-büro, UN News, kenianische Menschenrechtskommission)
und das Archiv des französischen Rundfunkinstituts INA für Mai 68; **keine Wikipedia-Quelle** unter den neuen Ereignissen (insgesamt 5 von 342). Hinweise:
Beginn = Tag, an dem die Quelle den Ausbruch nennt (Chile: 17.10. Massenschwarzfahren, 18.10. Ausnahmezustand; Sri Lanka: erste Gewalt vor dem Privathaus des
Präsidenten, nicht der Höhepunkt am 09.07.; Iran: aus „neunter Protesttag“ in UN News vom 05.01.2026 abgeleitet). `ende` steht nur, wo die Quelle ein Ende der
Welle oder einen Wendepunkt nennt (Rücktritt, Räumung), sonst `null`. Bei Occupy ist `ende` die Räumung des Zeltlagers am 15.11.2011, nicht das Ende der Bewegung.
Nicht aufgenommen: „Bloquons tout“ (Frankreich, 10.09.2025; die Seite der Gendarmerie sperrte den Abruf, also nichts belegt), Bangladesch 2024 (OHCHR-Bericht geöffnet,
aus Platzgründen nicht aufgenommen), Indien 2026 (Startdatum der Proteste nicht belegt).

## Ereignisstudie

`studie EREIGNIS_ID` rechnet für ein Ereignis (Standardreihe: `dow` für Ereignisse ab 1885-02-16, davor `shiller_sp`):

- **Startwert:** letzter bekannter Wert am oder vor dem Beginn (das Datum des Werts wird genannt).
- **Rendite nach 1, 3, 6 und 12 Monaten:** Kalendermonate, Wert am oder vor dem Zieltag; reicht die Reihe nicht so weit, steht „noch nicht erreicht“.
- **Größter Rückgang binnen 12 Monaten:** tiefster Wert im Fenster gegenüber dem Startwert, mit Datum (ohne Rückgang: 0 %).
- **Erholung:** erstes Datum nach dem Tief mit Wert gleich oder über dem Startwert, mit Tagen seit Beginn und seit dem Tief; sonst „nicht erholt“.
- **Beginn bis Ende:** Rendite, wenn das Ereignis ein `ende` hat.

`studie --art ART` wertet alle Ereignisse einer Art aus und zeigt je Horizont **n, Median, Mittel und Anteil negativ**. Ehrlichkeitsregeln: n steht immer
dabei; bei **n < 5 gibt es keine Kennzahlen, nur die Einzelwerte** in der Tabelle darüber; Ereignisse mit Genauigkeit `quartal` oder `jahr`
werden nicht mitgerechnet (außer mit `--grobe`), weil ihr Beginn nur ein Platzhalter ist; Ereignisse überschneiden sich (2008 hat mehrere), ihre
Werte sind nicht unabhängig; ohne `--reihe` können Ereignisse vor und nach 1885 in verschiedenen Reihen gerechnet sein (steht in der Tabelle).
Das Ergebnis beschreibt die Vergangenheit und sagt nichts über die nächste Krise.

## Muster-Engine (`muster.py`)

Frage: Wie wirkte Weltgeschehen dieser Art in der Vergangenheit auf verschiedene Märkte, und war das ungewöhnlich? **Rückblick, der eingrenzt; er garantiert nichts.**

**Reaktion über viele Märkte** (`muster EREIGNIS_ID`, `muster --art ART`). Reihen (die erste geladene je Gruppe gilt): Dow (`dow`), S&P (`shiller_sp`, Monatsmittel),
DAX (`dax`), UK-Aktien (`uk_aktien`, endet 2017), Bitcoin (`btc`), Gold (`gold` täglich, sonst `gold_monat`), Silber, Platin, Öl WTI (`oel_wti`, sonst
`oel_wti_monat`), VIX, EUR/USD, US-Zins 10 J. (`us_zins10_tag`, sonst `shiller_zins10`; Veränderung in **Prozentpunkten**, bei allen anderen Rendite).
Nicht geladene Reihen werden ausgelassen und genannt; Reihen, die das Ereignis nicht abdecken (beginnen später, enden früher), stehen unter „Nicht gerechnet“.
- **Startwert:** letzter Wert am oder vor dem Beginn (Samstagsereignis: Freitagsschluss). **1 Tag** = erster Wert nach dem Start, **1 Woche** = 7 Kalendertage,
  **1 Monat** und **3 Monate** = Kalendermonate (Wert am oder vor dem Zieltag, mindestens aber der nächste Wert nach dem Start). Monatsreihen kennen nur 1 und 3 Monate
  (Monatsmittel: der Wert des Beginnmonats enthält auch Tage nach dem Beginn). Reicht die Reihe nicht bis zum Zieltag (mehr als 5 Tage vor, bei Monatsreihen
  31 Tage), steht „–“ bzw. „noch nicht erreicht“. **Tief/Hoch ≤3 Mon.:** größter Rückgang bzw. Anstieg gegenüber dem Start in den 3 Monaten (0, wenn es keinen gab).
- **Genauigkeit:** 1 Tag und 1 Woche nur bei Ereignissen mit Tagesgenauigkeit; Monatsgenauigkeit zählt nur für 1 und 3 Monate; Quartal/Jahr werden nur mit `--grobe` (wie Monat) gerechnet.
- **Vergleich mit „normalen“ Zeiträumen:** Für jede Reihe und jeden Horizont wird dieselbe Rechnung für **jeden Starttag der ganzen Reihe** gemacht (Dow: 38.655 Starttage seit
  1885, Bitcoin seit 2010 …). Daraus: **Perzentil** des Ereigniswerts bzw. des Medians (P50 = normal), **Anteil negativ** der Fälle gegen den Anteil in allen Zeiträumen und ein
  **Zufallsvergleich** (1000 Stichproben aus gleich vielen zufälligen Starttagen, fester Startwert, also wiederholbar): „höher*“ bzw. „tiefer*“ heißt, dass der Median höchstens in 5 % der
  Stichproben so hoch bzw. so tief lag. Die Basis ist die ganze Reihe (verschiedene Epochen, Bitcoin vor 2014 ein Winzmarkt, Gold bis 1971 fest; solche Fälle erhalten einen Hinweis).
- **n immer dabei; unter n = 5 nur Einzelfälle** (bei einem Ereignis Wert und Perzentil, bei 2 bis 4 Ereignissen die Einzelwerte). Einzelfalltabellen für 1 Woche, 1 Monat und 3 Monate
  stehen unter der Zusammenfassung.
- **Grenzen:** Die Fälle überschneiden sich zeitlich (2008, 2011, 2020 mit mehreren Ereignissen) und sind nicht unabhängig, der Zufallsvergleich zieht unabhängige Starttage und wird
  dadurch zu leicht „ungewöhnlich“. Bei 6 Reihen mal 6 Kennzahlen fällt allein durch Zufall etwa jede zwanzigste Zelle auf (Mehrfachvergleiche). Gleichzeitige andere Ereignisse
  (Zinsschritte, Handelsstreit, Krisen) wirken mit: Dass nach einem Ereignis etwas passierte, belegt nicht, dass es wegen des Ereignisses passierte. Die Auswahl der Ereignisse
  ist im Nachhinein getroffen (Überlebens- und Auswahlverzerrung).

**Ähnlichkeitssuche** (`aehnlich "TEXT"`, `muster.aehnliche(text_oder_merkmale_oder_lage, n=10)`). Einfache, nachvollziehbare Gewichtung, keine Fremdpakete: Aus dem Text (deutsch und englisch)
werden Ereignisart (Stichwortlisten je Art, Wortanfänge wie `ausschreit*`, `protest*`, `riot*`), Region/Land (Liste mit Synonymen, z. B. Frankreich/France/Paris) und übrige Stichwörter
erkannt. Punkte je früherem Ereignis: **gleiche Art 3** (verwandte Art 0,3 bis 0,6 mal 3, z. B. Krieg~Konflikt, Bankenkrise~Finanzkrise), **gleiche Region 2** (gleiche Großregion 1, weltweites
Ereignis 0,5), **Stichwörter** je 0,5 mal Seltenheit im Ereigniskorpus (höchstens 3; Ortsnamen zählen nur bei der Region). Ein Treffer braucht Art oder Region und mindestens 2 Punkte; die Begründung
steht dabei („gleiche Art (unruhen), gleiche Region (Frankreich), Stichwort: ausschreitungen“). **Ereignisse der letzten 14 Tage werden ausgeklammert und genannt** (die laufende Lage ist ihr
eigenes Vorbild nicht). Die Mehrmarkt-Reaktion wird über die Treffer mit mindestens der halben Bestpunktzahl gerechnet. Aus der OSIRIS-Lage (`Lage.daten`): Konfliktzonen mit Stufe „war“ (Arten
`krieg`, `konflikt`), Länderrisiko (Landescodes zu Regionen, Merkmale als Text), Schlagzeilen (ungeprüft: Art erst ab zwei verschiedenen Stichwort-Treffern, halbes Gewicht).

**Krypto und Aktien** (`zusammenhang`). Tagesrenditen von Bitcoin und Dow über dasselbe Intervall (von Handelstag zu Handelstag des Dow, Bitcoin zu denselben Stichtagen, also über
Wochenenden hinweg); Pearson-Korrelation der letzten 30 und 90 **Handelstage**, aktuell, als Verteilung über die ganze Geschichte (Min, Perzentile, Max), Median je Jahr und seit 2020. **Stress:** Tage
mit Dow-Verlust ab 2 % bzw. 3 % (n, Bitcoin Median, Mittel, Anteil negativ gegen alle Tage, Korrelation an diesen Tagen ab n = 5) und Rückgangsphasen (Dow mindestens 10 % unter dem Hoch der
letzten 250 Handelstage; Korrelation dort gegen sonst). Der S&P liegt nur als Monatsmittel vor, daher Dow. Korrelation ist kein Kausalbeleg und schwankt stark von Phase zu Phase.

**In der KI** (`kitrader.ki.historie_text`, Abschnitt „Ereignisse und Muster aus der Historie“): `muster.kontext_text(daten, arten, text)` baut höchstens 3.000 Zeichen: Hinweis auf die festen
Regeln, erkannte Merkmale, die fünf ähnlichsten früheren Ereignisse mit Begründung, die Reaktion der ähnlichsten (Dow, DAX, Bitcoin, Gold, Öl, VIX, Zins; 1 Woche, 1 Monat, 3 Monate; mit n,
Anteil negativ gegen die Basis und `*` bei Auffälligkeit; nicht geladene Reihen genannt), den Krypto-Aktien-Zusammenhang und den Hinweis, dass Geschichte eingrenzt und nichts garantiert.
Fehlen Reihen, steht das im Text (keine erfundenen Zahlen). Der erste Aufruf im Prozess rechnet die Basis der Reihen (rund 1 Sekunde), danach kommt sie aus dem Zwischenspeicher.
Einstellung `ki.historie_reihe` wirkt auf diesen Abschnitt nicht mehr (nur noch auf Ersatzobjekte der Ereignisstudie).

## Datenqualität: Stolpersteine

- **Shiller** liefert **Monatsmittel** der Tagesschlusskurse (Datum = 1. des Monats), keine Schlusskurse. Der Wert des Monats eines Ereignisses enthält
  Tage nach dem Ereignis; der jüngste Monat ist vorläufig. Dividenden und Gewinne der letzten Monate fehlen meist (daher oft „veraltet“).
  Das Datum der Quelle `2026.1` bedeutet Oktober (nicht Januar); das Programm liest die Nachkommastellen als Monat.
- **Bank of England** (`uk_aktien`, `uk_leitzins`) **endet 2017** und wird nicht fortgeschrieben; der Aktienindex ist aus vielen Quellen verkettet
  (Neal, Gayer/Rostow/Schwarz, Smith/Horne, Bankers' Magazine, Actuaries, FTSE All-Share).
- **Bitcoin** ist frei erst ab **18.08.2010** abrufbar; Nullwerte der Quelle (kein Preis) werden übersprungen. Der Preis ist ein Mittel großer Börsen.
- **Dow Jones:** vor dem 07.10.1896 der einzelne Dow Jones Average, bis 30.07.1914 die erste DJIA, angeglichen. Die NYSE war vom **31.07. bis 11.12.1914
  geschlossen**; die Lücke ist echt. Zitierpflicht: Samuel H. Williamson, „Daily Closing Value of the Dow Jones Average, 1885 to Present“, MeasuringWorth.
- **Verschiedene Quellen sind nicht ohne Weiteres verkettbar:** andere Zusammensetzung, andere Zeitpunkte (Monatsmittel gegen Tagesschluss),
  andere Einheiten und Basisjahre. Reihen nicht ungeprüft aneinanderhängen und Renditen nur innerhalb einer Reihe rechnen.
- **Ölpreis:** WTI war am 20.04.2020 negativ (−36,98); das ist kein Fehler.
- Lücken werden nie gefüllt oder geglättet.

## Tests

```bash
PYTHONPATH=src python3 -m unittest tests.kitrader.test_historie
```

Die Tests laufen offline mit erfundenen Mini-Daten (Excel-Dateien werden im Speicher gebaut) in einem temporären Datenordner.
Die Muster-Engine hat `tests/kitrader/test_muster.py` (erfundene Reihen; Reaktion, Basisvergleich, Zufallsvergleich, Ähnlichkeit, Korrelation von Hand nachgerechnet, KI-Text, Befehle).
