# Übergabe kitrader – Stand 26.10.2026

## Kurzfassung

**Schnellstart:** Doppelklick auf `kitrader-starten.command` (Mac) bzw. `PYTHONPATH=src python3 -m kitrader start`: Autopilot (Papier), Oberfläche im Browser mit Reiter **Live**, Telegram-Wache und Zeitplan (OSIRIS, Nachrichten-Radar, Welt-Rotation, Morgenlage) in einem Prozess; Strg+C beendet alles. Siehe „Alles in einem Befehl“ unten.

kitrader ist eine eigene, neue Trading-Software: Daytrading über Interactive Brokers und Kraken
(zuerst Papier, dann Demokonto, Echtgeld nur mit Freigabe), lange Kurshistorie seit 1709, Ereignisliste
seit 1902, Weltlage aus OSIRIS, KI-Einschätzung kurz/mittel/lang mit gemessener Trefferquote,
Oberfläche im Browser und Not-Aus per Telegram. Eigener Code, nichts aus ic-delta übernommen.

**Bausteine (Stand 26.10.2026):**
- **Broker:** Interactive Brokers (Client Portal Gateway) und Kraken, dazu das Papierkonto; Risikoprüfung mit Pflicht-Stop, Tagesverlustgrenze und Not-Aus.
- **Autopilot** (papier / demo / echt nur mit Freigabedatei) und **KI-Strategie** (long und short; Aufrufe je Tag standardmäßig unbegrenzt, `max_aufrufe_pro_tag` begrenzt sie).
- **Morgenlage** (KI kurz/mittel/lang je Watchlist-Wert) und **KI-Einschätzung** mit Journal und Trefferquote.
- **Historie** seit 1709 mit **342 Ereignissen** und der **Muster-Engine** (Reaktion vieler Märkte, Ähnlichkeitssuche, Krypto–Aktien-Zusammenhang).
- **OSIRIS** (Weltlage), **Nachrichten-Radar** mit 45 Feeds plus GDELT und **Welt-Rotation** (Gewinner und Verlierer nach Region und Anlageklasse); beide laufen unter `start` alle 2 Stunden.
- **Oberfläche** im Browser mit Reiter **Live**; **Telegram** optional (Meldungen, „stop“, „status“); **Start per Doppelklick** (`kitrader-starten.command`).
- **Vorwärtstest** der KI mit Lernschleife sowie **Strategie-Labor** und **Langfrist-Labor**.

**Geprüft in der Cloud:** 1055 Tests grün (kitrader), 118 (markt, 1 übersprungen) und 45 (JS-Indikatoren) (Stand 26.10.2026, nach den letzten Dashboard-Punkten; vorher 1011 nach dem Zeitplan für Nachrichten und Welt; ältere Zahlen in den Abschnitten unten gelten für ihren Zeitpunkt) (`PYTHONPATH=src python3 -m unittest discover -s tests/kitrader -t .`), Oberfläche im echten Browser (Chromium) und eine echte KI-Einschätzung (beides am 26.10.2026, Einzelheiten unten).
**Nicht geprüft:** echte Broker-Konten, echter Telegram-Bot, die Oberfläche in deinem eigenen Browser, FRED und Stooq (aus der Cloud gesperrt).
**Kein Erfolgsnachweis:** Die zwei Strategien sind Beispiele, und das Strategie-Labor (unten, `STRATEGIE-BERICHT.md`) hat sie samt fünf weiteren Ansätzen auf echten Kraken-Kursen geprüft: nach Kosten keiner im Plus, keiner erkennbar besser als Zufall. Ob irgendetwas Geld verdient, zeigt erst ein länger geprüfter Ansatz im Papierhandel.

## Was drin ist

| Teil | Datei | Stand |
|---|---|---|
| Broker-Schnittstelle, Papier-Broker | `src/kitrader/broker/basis.py`, `papier.py` | fertig, getestet |
| Interactive Brokers (Client Portal Gateway) | `broker/ibkr.py`, Anleitung `broker/BROKER.md` | gegen nachgebildeten Server getestet |
| Kraken (Spot, Margin optional, `nur_pruefen` Standard an) | `broker/kraken.py` | Signatur = offizielles Doku-Beispiel |
| Risikoprüfung (Tagesverlust, Größe, Pflicht-Stop, Kursalter, Not-Aus …) | `risiko.py` | fertig, Sicherheitsprüfung mit 9 behobenen Fehlern |
| Strategien (Beispiele und 5 Kandidaten), Backtest mit zurückgehaltenem Teil, Maker/Taker-Gebühren | `strategie/`, `backtest.py` | fertig |
| Echte Kerzen holen (öffentliche Kraken-API) | `daten.py` | fertig, an echter API geprüft |
| Strategie-Labor (Walk-forward, Halten, Zufall) und Bericht | `labor.py`, `STRATEGIE-BERICHT.md` | fertig, 90 Tage BTC/EUR + ETH/EUR gerechnet |
| Journal mit Trefferquote je Horizont | `journal.py` | fertig |
| Autopilot (papier / demo / echt mit Freigabedatei) | `autopilot.py` | fertig |
| Kurshistorie, Ereignisliste (342), Ereignisstudie, Muster-Engine (viele Märkte, Ähnlichkeit, Krypto–Aktien) | `historie/` | fertig, echte Daten geladen (siehe unten); Muster-Engine im Abschnitt „Muster-Engine“ |
| OSIRIS-Weltlage | `osiris.py` | echter Abruf von osirisai.live erfolgreich |
| KI-Einschätzung (Treiber `claude_cli` über dein Abo, `anthropic`, `aus`) | `ki.py` | echte KI-Einschätzung erfolgreich am 26.10.2026 (`claude_cli`, BTC/EUR mit echten Kraken-Tageskerzen, 23 s, gültiges JSON für kurz/mittel/lang, im Journal); `anthropic` nur gegen nachgebildeten Server |
| Oberfläche (nur 127.0.0.1, Schlüssel-Schutz, Not-Aus-Knopf) | `oberflaeche/` | im echten Browser geprüft am 26.10.2026 (Chromium: alle Reiter, Chart mit Ereignismarkern, Studie, Weltlage, Not-Aus, 390 px, hell und dunkel, fremder Text als `<script>` bleibt Text); in deinem eigenen Browser noch ansehen |
| Telegram (Meldungen, „stop“ = Not-Aus, „status“) | `telegram.py` | gegen nachgebildeten Server getestet |
| KI handelt selbst: Strategie `ki` | `strategie/ki_strategie.py` | getestet mit Fake-Treiber; echte Probe 26.10.2026 (3 KI-Aufrufe, kein Signal), siehe Abschnitt unten |
| Morgenlage (KI kurz/mittel/lang je Watchlist-Wert, OSIRIS-Veränderung, Trefferquote) | `lage.py`, `python3 -m kitrader morgenlage` | echt mit `--treiber aus` gelaufen; mit KI nur im Test |

Bedienung: `README.md`, Einzelheiten `src/kitrader/LIESMICH.md`, Historie `src/kitrader/historie/HISTORIE.md`.

### Prüfung unter echten Bedingungen (26.10.2026, Cloud)

- **Oberfläche** im echten Chromium mit Papierkonto (echte Kraken-Kurse), Journal, echter OSIRIS-Lage und geladener Historie
  (Dow, Bitcoin): keine Konsolenfehler; Not-Aus (Abbrechen, Escape, Bestätigen: Datei `NOTAUS`, Banner, Positionen der eigenen
  Symbole glatt, fremde bleiben); 390 px ohne waagrechtes Scrollen der Seite. Behoben: Dunkelmodus (ECHTGELD-Fläche, Banner,
  Links, Dialogknöpfe), schmale Fenster (Auswahlfeld, Datum, Tabellen, lange KI-Texte), Studie nach Reihenwechsel, Achsenbeschriftung.
- **Fehler im Papier-Broker** (dabei gefunden, `broker/papier.py`): Gesamtwert und Positionen wurden in getrennten Abfragen gelesen,
  dazwischen lag die Kursabfrage; löste jemand gleichzeitig (Not-Aus aus der Oberfläche, Autopilot) eine Order aus, zeigte das Konto
  kurz Erlös und Position zugleich (+710 € statt −2 €) bzw. einen Scheinverlust, der die Tagesverlustgrenze hätte auslösen können.
  Außerdem hielt die Bewertung die Schreibsperre über die Netzabfragen (`database is locked` beim gleichzeitigen Schreiben). Behoben, zwei Tests.
- **KI** `claude_cli` einmal aufgerufen, erfolgreich (23 s). Die Antwort ist ein Denkanstoß: „steigt“ (3M) bei 0,37, obwohl die KI
  „faellt“ ebenso hoch ansetzte; der Prompt verlangt jetzt die wahrscheinlichste Richtung. Die laufende (unfertige) Kraken-Tageskerze wird im Kontext gekennzeichnet.
- Bleibt offen: Oberfläche und KI auf dem eigenen Rechner ansehen; bei sehr dichten Ereignisstrichen (ab 2000 im Dow-Chart) per Jahr/Art filtern.

### Kurshistorie: in der Cloud tatsächlich geladen

| Reihe | Werte | Zeitraum | Quelle |
|---|---|---|---|
| S&P, Dividende, Gewinn, CPI, Zins 10 J., CAPE (Shiller) | je ~1.870 | 1871-01 bis 2026-10 (CAPE ab 1881) | shillerdata.com |
| Dow Jones täglich | 38.654 | 16.02.1885 bis 22.10.2026 | MeasuringWorth |
| UK-Aktien monatlich / Leitzins täglich | 3.697 / 67.518 | 1709-04 bis 2017-04 / 1833 bis 2017 | Bank of England |
| Bitcoin täglich | 5.943 | 18.07.2010 bis 24.10.2026 | Coin Metrics (CC BY-NC 4.0) |
| Gold, Silber, Platin monatlich | je 801 | 1960-01 bis 2026-09 | Weltbank „Pink Sheet“ |

Nicht geladen: 13 FRED-Reihen (Zinsen, Inflation, Öl, VIX, EUR/USD), Silber/Platin/Palladium täglich (Stooq),
Gold täglich ab 1978, DAX ab 1948 – siehe To-do.

### Ereignisliste

342 Ereignisse von 1902 bis Oktober 2026 (davon 19 der neuen Art `unruhen`, siehe „Muster-Engine“) in sechs Gebieten (Kriege 96, Rohstoffe und Pandemien 67,
Finanz- und Bankenkrisen 45, Zinsen und Währung 45, Rezessionen 37, Politik und Handel 33). Jede mit Quelle,
5 davon Wikipedia. Link-Prüfung aus der Cloud: 252 von 262 Links antworten mit 200, 10 sperren die Cloud
(SEC, IWF, FRASER). Bekannte Lücken: z. B. Lockerbie 1988, Oklahoma City 1995, Gründung EWS 1979,
Smithsonian-Abkommen 1971, Ruanda 1994, Libanonkrieg 2006 (keine prüfbare Quelle gefunden).

## Strategie-Labor (26.10.2026)

**Was:** Fünf übliche Ansätze als **Kandidaten** (`strategie/kandidaten.py`: Donchian-Ausbruch, Bollinger/RSI, gleitende Durchschnitte mit Trendfilter, Momentum, Volumen-Ausbruch) plus die zwei
Beispiele, geprüft auf 90 Tagen **echter Kraken-Kurse** (BTC/EUR und ETH/EUR; 5 Minuten, 15 Minuten, 1 Stunde), mit den echten Kraken-Pro-Gebühren (Stufe 1: Taker 0,80 %, Maker 0,40 %),
Walk-forward in 4 Faltungen (Parameter nur aus früheren Daten, Bericht nur über die Testteile), Vergleich mit Kaufen und Halten und mit 200 Zufallsläufen. Bei unter 30 Trades keine Aussage.
Vollständiger, für Laien geschriebener Bericht mit Tabellen, Warnungen und Wiederholungsanleitung: **`STRATEGIE-BERICHT.md`** (Repo-Wurzel).

**Kernergebnis:**

- Nach Kosten ist **kein einziger Ansatz im Plus** (19 von 42 Kombinationen hatten genug Trades für eine Aussage; 2.012 Trades insgesamt).
  Ergebnis vor Kosten 498 €, Kosten 31.461 €. Ein Signal brachte im Schnitt 0,005 % je Trade vor Kosten, die Kosten betrugen 1,60 % je Trade.
- Besser als 95 % der Zufallsläufe: 1 von 19, allein durch Zufall zu erwarten etwa 1,0; im Mittel schnitten 49 % der Zufallsläufe besser ab (bei reinem Raten 50 %) – also **kein erkennbarer Vorteil** gegenüber blindem Einstieg.
- Gegenproben: mit Stufe-6-Gebühren (0,25 % / 0,12 %) 0 von 19 im Plus; ganz ohne Kosten 14 von 24 im Plus, aber 0 besser als 95 % der Zufallsläufe (das kleine Plus ist von Zufalls-Einstiegen im selben Markt nicht zu unterscheiden).
- **Empfehlung:** Keiner der sieben Ansätze kommt als Gewinnstrategie in den Papierhandel. Papierhandel nur als Technik-Test (Orders, Stops, Journal, Not-Aus) ohne Gewinnerwartung; Echtgeld nicht auf dieser Grundlage.
  Nächste sinnvolle Frage: seltener handeln (Tageskerzen, Haltedauer Tage bis Wochen) und mehr Daten (180 bis 365 Tage).
- **Schwächen** (Details im Bericht): nur 90 Tage und eine Marktphase, kleines Parametergitter, nur Long und Daytrading, Backtest ist kein Echtbetrieb, Gebühren nur Stufe 1/Stufe 6.

**Befehle:**

```
PYTHONPATH=src python3 -m kitrader daten kraken BTC/EUR --takt 5m 15m 1h --tage 90   # Kerzen holen (BTC/EUR ca. 1.600 Abrufe, gut 30 Min.; ETH/EUR ca. 900); setzt nach Abbruch fort
PYTHONPATH=src python3 -m kitrader labor --ausgabe labor.json --markdown labor.md      # Labor mit den Kraken-Standardgebühren
PYTHONPATH=src python3 -m kitrader labor --taker 0.25 --maker 0.12                      # Gegenprobe Stufe 6 (--taker 0 --maker 0 --spread 0 --schlupf 0: ohne Kosten)
```

Die Kerzen liegen im Datenordner (`kerzen/`), **nie im Repo**. Neu: `backtest.py` kennt getrennte Maker-/Taker-Gebühren (`kosten.gebuehr_maker_prozent`), einen Vorlauf vor dem Testabschnitt (`handel_ab`, im Walk-forward
standardmäßig an) und speichert Stop-/Ziel-Abstände je Trade. Tests: 596 grün zum Stand dieses Abschnitts (76 neue für Kerzen holen, Kandidaten, Labor, Backtest-Erweiterungen, darunter eine Positivkontrolle auf künstlichen Kursen).

## Labor langfristig (26.10.2026)

**Was:** Trend (gleitender Durchschnitt: 200 Tage bzw. 10 Monate), Zeitreihen-Momentum (12 Monate), Bewertung per CAPE (fest, relativ) und Trend + Bewertung auf **Jahrzehnten echter Kurse**: S&P (Shiller, nominal mit Dividenden, ab 1871), Dow täglich (ab 1885; nur Kurs und mit Dividenden-Näherung), Bitcoin täglich (ab 2010, getestet ab 2016), Gold monatlich (ab 1960, getestet ab 1980), UK-Aktien monatlich (1709 bis 2017). Signale nur aus Daten bis zum Vortag/Vormonat, Ausführung zum nächsten Kurs, Kosten 0,1 % (Bitcoin 0,5 %) je Wechsel, Bargeld mit 0 % und mit 10-Jahres-Zins, Walk-forward (Parameter nur aus Daten vor dem Testblock), Zufallsvergleich (Spannen umgeordnet: gleiche Wechselzahl und Marktquote), Teilzeiträume, Gegenproben (Kosten, Verzögerung, künstliche Zufallsreihen), Ereignisauswertung (Kriege, Bankenkrisen, Zinsschocks, Pandemien) mit `historie.studie_art`.
Ausführlicher, für Laien geschriebener Bericht: **`LANGFRIST-BERICHT.md`** (Repo-Wurzel).

**Kernergebnis:**

- **Keine Regel schlägt Kaufen und Halten verlässlich bei der Rendite.** Im Walk-forward-Fenster höher als Halten: 18 von 50 Zeilen, bei Bargeld 0 % nur 5 von 27 (viermal Gold, einmal Bitcoin-Momentum mit Walk-forward); S&P und Dow (mit Dividenden) mit Regeln zwischen −0,3 und −4,4 Pp pro Jahr bei Bargeld 0 %, bei Bargeld-Zins höchstens +0,3 Pp.
- **Risiko senken können sie:** Größter Rückgang mit dem 10-Monats-/200-Tage-Trend: S&P 82 % auf 52 %, Dow 87 % auf 56 %, Gold 62 % auf 29 %, UK 71 % auf 44 %, Bitcoin 84 % auf 65 %; Schwankung in allen 50 Zeilen kleiner. Preis: etwa 0 bis 3 Pp Rendite pro Jahr, rund 1 Wechsel im Jahr; der Vorsprung steckt in wenigen Krisen (Dow bis 1949, Gold 1980 bis 1999, Bitcoin bis 2022), danach oft Rückstand. Gegen lange Flauten (UK 30 bis 39 Jahre unter Wasser) schützen sie kaum.
- Das Timing ist meist **besser als zufälliges Umordnen** (Rendite in 32, Rückgang in 30 von 50 Zeilen besser als 95 % der Zufallsläufe; erwartet etwa 2,5), aber das heißt nicht „besser als Halten“. CAPE-Bewertung schützte allein nicht und kostete in Kombination mit dem Trend mehr Rendite (−4,4 Pp), als sie Schutz brachte.
- Annahmen wirken stärker als die Regel: Bargeld-Zins (bis ca. 2 Pp), Handel am selben statt am nächsten Kurs (S&P-Trend −1,8 gegen −0,0 Pp; Bitcoin-Momentum 8,6 Pp), Dividenden (Dow 4 Pp), Kosten (Bitcoin bei 1 % je Wechsel −11,3 Pp).
- **Empfehlung:** Keine Gewinnstrategie für den Papierhandel. Einziger Kandidat als *Risikoschutz* (nicht Renditebringer): Trend 10 Monate auf einen breiten Aktienindex, monatlich geprüft, Handel zum Folgemonatskurs. Der Autopilot stellt am Tagesende glatt, eine Monatsregel bräuchte eine eigene Betriebsart (nicht gebaut), und der Papierhandel liefert bei etwa einem Wechsel im Jahr in Wochen keine Beobachtung.
- **Schwächen** (Details im Bericht): Shiller-Reihen sind Monatsmittel (Zeitpunkte verschoben), CAPE-Gewinne rückwirkend, Dow ohne Dividenden (Näherung ist eine Annahme), UK-Reihe vor 1950 geglättet und ohne Dividenden, Gold bis 1971 fester Preis, nur US/UK (erfolgreiche Märkte), Steuern nicht gerechnet, 10-Jahres-Zins als Bargeld ist großzügig, 50 Zeilen = Mehrfachtests; Walk-forward-Auswahl landet beim Trend am Rand des Gitters.

**Befehle:**

```
PYTHONPATH=src python3 -m kitrader.historie laden dow shiller_sp shiller_real_tr shiller_cape shiller_zins10 shiller_cpi shiller_dividende btc gold_monat uk_aktien
PYTHONPATH=src python3 -m kitrader labor lang --markdown tabellen.md          # alle Reihen, 500 Zufallsläufe, etwa 35 Sekunden auf 4 Kernen; JSON unter <Datenordner>/labor/lang.json
PYTHONPATH=src python3 -m kitrader labor lang --reihen sp gold --kosten-aktie 0.3 --zufall 1000
```

Code: `strategie/langfrist.py` (Signale, Kosten, Kennzahlen, Walk-forward, Zufallsvergleich), `labor_lang.py` (Reihen laden, Ereignisse, Ausgabe, Befehl). Daten (Datenordner) nie im Repo. Tests: 641 grün (45 neue in `tests/kitrader/test_langfrist.py`: Signale von Hand, kein Blick in die Zukunft, Kosten an einem von Hand nachgerechneten Beispiel, Walk-forward nur aus Daten davor, Zufallsvergleich mit Hellseher-Kontrolle und Kalibrierung auf Zufallsreihen, Kommandozeile).

## KI handelt selbst und Morgenlage (26.10.2026)

**Was:** Strategie `ki` (`strategie/ki_strategie.py`, Anleitung in `LIESMICH.md`, Abschnitt „KI handelt selbst“) und der Befehl `morgenlage` (`lage.py`).
Die KI sendet nie Orders: sie liefert ein Signal (long bei „steigt“, short bei „faellt“ nur mit `risiko.short_erlaubt`, ab Mindestwahrscheinlichkeit 0,6,
nur wenn der ATR-Weg zum Ziel die Kosten 3-fach übersteigt), die Risikoprüfung bleibt vor jeder Order. Zwischenspeicher je Symbol (Standard 30 Minuten, Tageskerzen 6 Stunden),
Kostenbremse `max_aufrufe_pro_tag` (60) und `max_aufrufe`; KI-Fehler oder ungültige Antwort = kein Signal. Einschalten: `[autopilot] symbole`, `[strategie] name = "ki"`,
Papierbetrieb. Backtest mit echter KI wird verweigert (die KI kennt die Vergangenheit); getestet wird nur mit Fake-Treiber. Kleine Änderung am Autopiloten:
`Autopilot.__init__` ruft `strategie.anbinden(journal, datenordner, modus, einst)`, wenn die Strategie das hat. Short: `ausbruch`, `vwap` und die fünf Kandidaten meldeten schon Long und Short;
gehandelt wird Short nur mit `risiko.short_erlaubt = true` (nichts geändert). Neu in den Einstellungen: `[watchlist] werte` (Standard BTC/EUR, ETH/EUR).

**Echte Probe (26.10.2026, Cloud):** ein Durchlauf `autopilot --strategie ki --wiedergabe` (Papier, eigener Datenordner im Scratchpad) auf den letzten 300 echten Kraken-5m-Kerzen von BTC/EUR
(25.10. 05:10 bis 26.10. 06:05 UTC), Treiber `claude_cli`, `abstand_min = 240`, `max_aufrufe = 3`, OSIRIS echt geholt. Ergebnis: **3 echte KI-Aufrufe, alle drei Male „seitwaerts“ für 1 Tag
(0,62; 0,62; 0,55), also kein Signal, 0 Orders, 0 Trades**; Gesamtwert unverändert 10.000,00 EUR. Die KI begründete es mit der niedrigen Stundenschwankung (rund 0,2 %, hochgerechnet etwa 1 % am Tag, so groß wie die
1-%-Schwelle des Journals für „steigt/faellt in 1d“). Im Journal stehen 9 Einschätzungen (3 je Aufruf, Marke `[KI]`) und 3 Vermerke. Nebenbefund: ohne geladene Kursreihe (`historie laden`) fehlen der KI die Ereignismuster, das sagte sie selbst.
**Heißt:** Die Kette (Zwischenspeicher, Aufrufgrenze, echte CLI, OSIRIS, Journal) läuft; ob die KI je handelbare Signale liefert, zeigt erst der Papierhandel über Wochen. Dass ein Signal samt Stop und Ziel bis in den Papier-Broker kommt, zeigte nur
ein Trockenlauf mit nachgebauter CLI (fester Antwort „steigt 0,7“, kostet nichts): 1 Long mit Stop und Ziel, die Kostenschwelle verwarf zwei der drei Anfragen („Weg 0,96 % gegen Kosten 0,39 %“). Das war kein Test der KI.

**Morgenlage echt (`--treiber aus`, keine KI-Kosten, Kraken-Tageskerzen, OSIRIS zweimal echt geholt):** BTC/EUR 71.389,20 (1 T +0,0 %, 7 T −2,2 %, 30 T −3,7 %), ETH/EUR 2.067,46 (7 T −3,6 %, 30 T −12,7 %); OSIRIS-Veränderung gegenüber
dem Schnappschuss zwei Minuten davor („Vorfälle +0, Märkte keine Änderung ≥ 0,5 %“); Trefferquote „noch keine“. `morgenlage.json` wurde geschrieben (Aufbau im Kopf von `lage.py`; die Oberfläche liest sie). Telegram nicht eingerichtet, daher
„nicht gesendet“; die Kürzung auf Telegram-Länge ist nur getestet. **Die Morgenlage mit echter KI (ein Aufruf je Wert) wurde nicht gelaufen** (nur die Kette im Test mit Fake-Treiber und Fake-CLI). Aus Python für Zeitpläne: `lage.ausfuehren(einst, ordner)`.

**Tests:** `tests/kitrader/test_ki_strategie.py` (36 Tests: Signal long/short/keins, Mindestwahrscheinlichkeit an der Grenze, Kostenschwelle und Erwartung, Short verboten, KI-Fehler, Zwischenspeicher, Aufrufgrenzen, Verdichten, Umkehr, Autopilot-Ablauf, Backtest nur mit Fake) und `tests/kitrader/test_lage.py` (19 Tests: Aufbau, Text, JSON-Datei, Telegram-Kürzung, Fehler je Wert, Kommandozeile mit Fake-CLI und nachgebautem OSIRIS-Server).

**Offen / ehrliche Grenzen:** Kein Beleg, dass die KI-Strategie nach Kosten Geld verdient (der Strategie-Bericht sagt für einfache Regeln das Gegenteil). Die 1-%-Schwelle von „1d“ lässt die KI in ruhigen Phasen „seitwaerts“ sagen: dann wird kaum gehandelt; bei Kraken-Taker 0,8 % besteht praktisch kein Daytrading-Signal die Kostenschwelle. Die KI-Wahrscheinlichkeit ist nicht kalibriert und wird für „Ziel vor Stop“ genommen. Keine automatische Bremse bei schlechter KI-Trefferquote (nur die Risikoprüfung und die Aufrufgrenzen); eine „KI-Bremse“ aus dem Journal wäre ein sinnvoller nächster Baustein. Papierhandel: erst `ki.filter` und `strategie.name = "ki"` nicht zugleich einschalten (doppelte Anfragen), Wochen laufen lassen, Trefferquote ab 30 Fällen je Horizont prüfen. In der Wiedergabe sieht die KI die OSIRIS-Lage von heute.

## Alles in einem Befehl (26.10.2026)

**Was:** `python3 -m kitrader start [--modus papier|demo|echt]` (Code `start.py`) und für den Mac die Doppelklick-Dateien `kitrader-starten.command` / `kitrader-stoppen.command` (ausführbar,
prüfen Python ≥ 3.11 und installieren nie etwas). Ein Prozess, vier Teile in eigenen Fäden unter Aufsicht: **Autopilot** (Papier ist Standard; Echtgeld-Regeln unverändert: `echt` nur mit `modus = "echt"` in den
Einstellungen und Freigabedatei von heute, geprüft vor dem Start, sonst startet nichts), **Oberfläche** (127.0.0.1, Port 8850 oder der nächste freie), **Telegram-Wache** (nur wenn Telegram eingerichtet ist),
**Zeitplan** (OSIRIS alle 6 Std., Nachrichten-Radar und Welt-Rotation alle 2 Std., Morgenlage täglich 08:00 Europe/Berlin über `lage.ausfuehren`; `[zeitplan]` in den Einstellungen). Stürzt ein Teil ab, laufen die anderen weiter; er wird nach Pause (10 s bis 5 Min.) neu gestartet,
der Ausfall steht in Terminal, Journal (`teil_absturz`), `start-fehler.log` und (zu Beginn einer Pannenserie) per Telegram. Dauerhafte Fehler (`Abbruch`) werden gemeldet, nicht wiederholt. Doppelstart: Sperrdatei `start.lock` (flock).
Ohne eigene Einstellungsdatei nimmt `start` im **Papierbetrieb** Kraken-Kurse, BTC/EUR + ETH/EUR rund um die Uhr und Kraken-Pro-Gebühren (0,80 %) und sagt das beim Start unter „Vorgaben“; in `demo`/`echt` setzt es nichts voreingestellt.
**Oberfläche:** neuer Reiter **Live** (Kerzenchart je Symbol als eigenes SVG, ohne Fremdbibliothek: eigene Trades als ▲/▼ und Kreis, Stop/Ziel als Linien, aktuelle Position, letzte KI-Einschätzung je Horizont, alle 30 s) und die **Morgenlage** oben auf der
Übersicht; Schnittstellen `/api/live/symbole`, `/api/live/chart`, `/api/morgenlage` (nur lesend, gleicher Schlüssel-, Host- und Origin-Schutz wie alle anderen). Einzelheiten: `src/kitrader/LIESMICH.md`, Abschnitt „Alles starten“.

**Geprüft (26.10.2026, Cloud):** 781 Tests grün (zuvor 641 plus die der anderen Teile; 85 neu in `tests/kitrader/test_start.py` und `test_live.py`: Aufsicht mit Attrappen, Absturz und Neustart, Sperrdatei, Zeitplan mit gestellter Uhr (Winter-/Sommerzeit, Berlin und New York), Autopilot-Teil, Oberfläche als Teil,
Schutz der neuen Schnittstellen, Trades aus Papierkonto und Journal, ein echter Prozess mit Strg+C und `--beenden`). **Echte Probe:** `start` im Papierbetrieb mit echten Kraken-Kursen und echter Morgenlage (KI über `claude_cli`), Doppelstart verweigert, `--beenden` ok;
Reiter Live und Übersicht im echten Chromium (hell, dunkel, 390 px, keine Konsolenfehler). Die Trades im Bildschirmfoto wurden **von Hand** (Skript, echte Kraken-Kurse, Papierkonto und Journal wie beim Autopiloten) angelegt, weil die Strategie in der Probezeit kein Signal gab.
**Nicht geprüft:** der Doppelklick auf dem Mac selbst (Gatekeeper, Terminal), Telegram-Wache und Meldungen im echten Telegram (in der Probe nicht eingerichtet), `demo` und `echt` mit `start`, Dauerbetrieb über Tage.
**Offen:** `lage.ausfuehren` ruft im Zeitplan die KI je Wert (etwa 25 s, Claude-Abo); wer das nicht will, setzt `[zeitplan] morgenlage = ""`. Bei mehreren gleichzeitig offenen Positionen im selben Symbol kann die FIFO-Buchung des Papierkontos die Stop-/Ziel-Zuordnung eines abgeschlossenen Trades im Chart verschieben.

## Muster-Engine (26.10.2026)

**Was:** `historie/muster.py` beantwortet „Wie wirkte Weltgeschehen dieser Art auf verschiedene Märkte, und war das ungewöhnlich?“ aus der Geschichte (Rückblick, eingrenzend, keine Prognose). Einzelheiten, Rechenregeln und Grenzen: `src/kitrader/historie/HISTORIE.md`, Abschnitt „Muster-Engine“.
- `python3 -m kitrader.historie muster EREIGNIS_ID` / `muster --art unruhen [--region Frankreich]`: je Markt (Dow, S&P, DAX, UK, Bitcoin, Gold, Silber, Platin, Öl, VIX, EUR/USD, US-Zins) Veränderung nach 1 Tag, 1 Woche, 1 Monat, 3 Monaten, Tief/Hoch binnen 3 Monaten, **verglichen mit allen normalen Zeiträumen derselben Reihe** (Perzentil, Anteil negativ, Zufallsvergleich), n immer dabei, unter n = 5 nur Einzelfälle.
- `aehnlich "Ausschreitungen in Frankreich"`: frühere Ereignisse nach Art, Region, Stichwörtern (deutsch/englisch), mit Begründung und Mehrmarkt-Reaktion; Ereignisse der letzten 14 Tage werden ausgeklammert und genannt.
- `zusammenhang`: Bitcoin gegen Dow, rollierende Korrelation der Tagesrenditen (30/90 Handelstage), aktuell, Verteilung seit 2010, je Jahr, an Tagen mit Dow-Verlust ab 2/3 %, in Rückgangsphasen.
- **Neue Ereignisart `unruhen`:** 19 Ereignisse (Mai 1968 bis 17.10.2026), alle mit am 26.10.2026 geöffneter Quelle, die das Datum nennt; keine Wikipedia-Quelle. Liste und Hinweise in HISTORIE.md.
- **In der KI:** `ki.historie_text` (Abschnitt „Ereignisse und Muster aus der Historie“) liefert jetzt höchstens 3.000 Zeichen aus `muster.kontext_text`: ähnliche Ereignisse, Mehrmarkt-Reaktion gegen die Basis, Krypto–Aktien-Zusammenhang, Hinweis „Geschichte grenzt ein, garantiert nichts“. `ki.historie_text(daten, einst, historie=None)` hat dieselbe Signatur wie zuvor (der Vorwärtstest ruft sie so auf); Ersatzobjekte mit nur `studie_art` bekommen den alten Text.
- **Tests:** `tests/kitrader/test_muster.py` (37 Tests, erfundene Reihen, von Hand nachgerechnet) und angepasste Tests in `test_ki.py`.

**Offen / ehrliche Grenzen:** In der Cloud sind nur Dow, S&P (Shiller), UK-Aktien, Bitcoin, Gold (Monatsmittel) und US-Zins 10 J. (Shiller, Monatsmittel) geladen. Es fehlen (nur auf dem Mac ladbar bzw. von Hand): **VIX, Öl WTI, EUR/USD, US-Zins täglich (FRED)**, **Silber/Platin täglich (Stooq mit Schlüssel)**, **Gold täglich und DAX (von Hand einlesen)**; solange sie fehlen, nennt die Ausgabe sie unter „Nicht geladen“ und rechnet ohne sie. Die Ähnlichkeitssuche ist einfach (Stichwortlisten, keine Sprachmodelle) und kann Fremdes treffen; Stichwortlisten und Regionen stehen in `muster.py` (`ART_WOERTER`, `REGIONEN`) und lassen sich ergänzen. Die Reaktion ist kein Beleg für Ursache und Wirkung (gleichzeitige Zinsschritte, Handelsstreit, Krisen). Das Satz-Beispiel „Muster … gelten für eine US-Aktienreihe“ in `ki.REGELN` (Prompt) stimmt für den neuen Muster-Abschnitt nicht mehr ganz (jetzt mehrere Märkte); `LIESMICH.md` (Abschnitt KI-Kontext) beschreibt den alten Muster-Abschnitt noch. Beide gehören einem anderen Arbeitsstrang und wurden nicht angefasst.

## Nachrichten-Radar und Welt-Rotation (26.10.2026)

**Was:** `nachrichten.py` liest weltweit offene RSS/Atom-Feeds und die freie GDELT-DOC-API, speichert nur Titel, Kurztext (≤ 400 Zeichen), Link, Quelle, Zeit, Sprache (`nachrichten.db`), führt Dubletten zusammen, ordnet per Stichwortliste Länder, Anlageklassen, Branchen und
Ereignisarten zu und verdichtet die letzten 24 Stunden zu einem Radar (≤ 3.000 Zeichen: Top-Themen mit Zahl unabhängiger Häuser und Regionen, neue Themen, Ereignisarten mit Tempo, betroffene Märkte, GDELT-Stimmung). `welt.py` rechnet aus OSIRIS `/api/markets`, Kraken-Tageskerzen und dem Historie-Speicher
die **Rotation**: Regionen und Anlageklassen über 1 Tag/1 Woche/1 Monat, absolut und relativ zum Schnitt der Leitindizes, mit Hinweis „von … nach …“ nur als Zahlenbeobachtung (`welt.db`, Schnappschuss je Abruf). Beides steht im KI-Kontext (`ki.py`: Abschnitte „Nachrichten-Radar“ und „Welt-Rotation“ vor der Trefferquote) und als Kurzblock
in der Morgenlage (`morgenlage.json`: `nachrichten`, `welt`); nur mit gespeicherten Daten, ohne Netz. Befehle: `python3 -m kitrader nachrichten abrufen|radar|suche "TEXT"`, `python3 -m kitrader welt`. Einzelheiten: `src/kitrader/LIESMICH.md` (Abschnitte „Nachrichten-Radar“, „Welt-Rotation“).

**Quellen (alle am 26.10.2026 echt abgerufen, HTTP 200, lesbar, frisch):** 45 Feeds von 38 Häusern: Deutschland 8, USA 6, Großbritannien 5, Europa 4, China 4 (CGTN ×2, SCMP ×2), Indien 3, Brasilien 2, Lateinamerika 1, Naher Osten 1 (Al Jazeera), Afrika 2, Japan 3, Asien-Pazifik 3, Fachdienste 3.
**Ging nicht:** Xinhua (Feed liefert nur Einträge von 2017/2018), NHK (letzter Eintrag August 2026), China Daily (404), Arab News (403), Gulf News (404), IWF (403), BIZ (404), Kitco (404), Yahoo Finance (404), Korea Herald (kein gültiges XML), Kyodo (404), RT (EU-sanktioniert, Verbindung abgebrochen); TASS lieferte Daten, blieb aber wegen ungeprüftem
Sanktionsstatus draußen. allAfrica und G1 brachen bei einzelnen Abrufen ab (ein zweiter Versuch ist eingebaut). Ein Feed, der antwortet, aber nur alte Einträge hat, wird beim Abruf als „veraltet“ markiert.
**GDELT:** Das Antwortformat von `timelinetone` wurde in der Cloud an einer echten Antwort geprüft (18 Werte gelesen) und die Artikelabfrage an den Feldern einer echten Antwort (url, title, seendate, domain, language, sourcecountry); der volle Abruf (7 Artikelabfragen, 11 Tonreihen, 6 s Pause) scheiterte
in der Cloud meist an **HTTP 429** („eine Anfrage alle 5 Sekunden“, die Adresse der Cloud wird offenbar von vielen geteilt). Der Abruf wiederholt zweimal mit längerer Wartezeit und gibt dann nur GDELT auf, die Feeds laufen weiter. Auf dem Mac sollte es gehen; `nachrichten abrufen` zeigt je Anfrage Status und Fehler.
**Welt-Rotation, was fehlt:** Indien, Brasilien und Schwellenländer (der OSIRIS-Feed hat keinen Index dort; Zusatzreihe über `[welt] reihen` und `historie einlesen`, siehe LIESMICH). China = nur Hang Seng (Hongkong). Leitindizes in Landeswährung.

**Offene Grenzen (ehrlich):** Die Zuordnung ist Stichwortzählung: kein Verständnis, keine Verneinung, fremdsprachige Titel nur über Abfragehinweise und Eigennamen; gleiche Geschichten in verschiedenen Sprachen werden oft nicht zu einem Thema. dpa-Texte in mehreren Zeitungen zählen als mehrere Häuser. „Quellen in N Stunden“ misst Aufmerksamkeit,
nicht Wahrheit. Das Radar hat keine Trefferquote; ob es der KI nützt, zeigt erst der Vorwärtstest. Unter `start` laufen `nachrichten abrufen` und `welt` alle 2 Stunden von selbst (`[zeitplan] nachrichten_alle_h`, `welt_alle_h`; 0 = aus), dann wächst auch die eigene Historie der Rotation.
**Tests:** `tests/kitrader/test_nachrichten.py`, `test_welt.py`, `test_einbau_radar_welt.py` (62 Tests: nachgebildete RSS/Atom/RDF- und GDELT-Antworten, Zuordnung, Dubletten, Pausen und 429, Radar-Länge, Rotation mit erfundenen Schnappschüssen von Hand nachgerechnet, Historie-Reserve, Einbau in KI-Kontext und Morgenlage).

## Prompt für die lokale CLI

```
Richte die Trading-Software kitrader auf diesem Mac ein und bring sie im Papierbetrieb zum Laufen.

Regeln:
- Nichts installieren, keine Systemeinstellung ändern, nichts an Dritte senden, ohne mich vorher zu fragen.
- Kein echtes Geld. Nur Papier- und Demobetrieb.
- Schlüssel und Token nie anzeigen, nur unter ~/.config/kitrader/ mit chmod 600 ablegen, nie ins Repo.
- Vor jeder Aussage "fertig" oder "läuft" die Prüfausgabe zeigen.
- Wo ich selbst etwas tun muss (Anmeldungen, Schlüssel holen, Dateien im Browser laden), gib mir eine kurze Schritt-für-Schritt-Anleitung und warte auf mich.
- Bei Abweichungen gelten die Befehle in README.md, UEBERGABE.md und src/kitrader/LIESMICH.md.

Schritte:
1. Code holen: github.com/pappalardodaniel/finanz-portal (privat), Branch claude/hallo-4n6qhj, nach ~/kitrader. Gibt es den Ordner schon: git pull.
2. Python prüfen: python3 --version muss mindestens 3.11 sein. Sonst fragen, ob ich das offizielle Installationsprogramm von python.org nehmen will.
3. Tests: cd ~/kitrader && PYTHONPATH=src python3 -m unittest discover -s tests/kitrader -t . – alle müssen grün sein.
4. Einstellungen: src/kitrader/einstellungen.beispiel.toml nach ~/.config/kitrader/einstellungen.toml kopieren: Betriebsart papier, Watchlist BTC/EUR und ETH/EUR (frag mich nach weiteren Werten), KI-Treiber claude_cli (mein Claude-Max-Abo), keine Begrenzung der KI-Aufrufe, Short erlaubt im Papierbetrieb, Kosten wie Kraken. Zeig mir die Datei.
5. Daten: PYTHONPATH=src python3 -m kitrader.historie laden, danach "pruefen" zeigen (FRED geht vom Mac aus). Stooq-Schlüssel und DAX-Datei (HU Berlin), optional Gold täglich (LBMA): Anleitung für mich, dann einlesen. Links der Ereignisliste prüfen.
6. python3 -m kitrader osiris abrufen, python3 -m kitrader nachrichten abrufen, python3 -m kitrader welt – Ausgaben zeigen.
7. KI-Test: python3 -m kitrader einschaetzung BTC/EUR – Ergebnis zeigen.
8. Telegram nur, wenn ich es will (sonst überspringen; Not-Aus und Status gehen auch über meine Claude-Remote-Sitzung).
9. Start: kitrader-starten.command ausführbar machen und starten (beim ersten Mal ggf. Rechtsklick > Öffnen). Prüfen, dass http://127.0.0.1:8850 läuft und alle Reiter Daten zeigen.
10. Dauerbetrieb: fragen, ob der Mac wach bleiben soll (caffeinate) und ob kitrader beim Anmelden automatisch starten soll – nur nach meinem Ja.
11. Vorwärtstest mit Lernschleife über 24 Stunden im Hintergrund starten (python3 -m kitrader.vorwaertstest laufen, siehe LIESMICH.md) und erklären, wie ich die Auswertung abrufe.
12. Broker-Demo nur nach meinem Ja (src/kitrader/broker/BROKER.md): Interactive-Brokers-Demokonto mit Client Portal Gateway (Java – vorher fragen), Kraken-Schlüssel ohne Auszahlungsrecht; dann konto, positionen, orders mit --modus demo zeigen.
13. Offene Punkte aus UEBERGABE.md abarbeiten, soweit sie keine Entscheidung von mir brauchen, mit Tests; den Rest als Liste vorlegen.
14. Nur nach meiner Bestätigung: Git-Verlauf ohne die drei alten ic-delta-Commits neu anlegen (ein Commit mit dem heutigen Stand) und mit --force-with-lease auf denselben Branch hochladen.

Bericht einmal am Ende: was läuft (mit Ausgaben), was ich noch tun muss, was offen ist.
```

### Einzelheiten (in dieser Reihenfolge)

1. **Holen und prüfen:** Branch `claude/hallo-4n6qhj` aus `github.com/pappalardodaniel/finanz-portal` holen.
   Python ≥ 3.11 nötig (`python3 --version`); wenn älter: **erst fragen**, nichts selbst installieren.
   Tests laufen lassen (Befehl oben), Ergebnis zeigen.
2. **Historie laden:** `PYTHONPATH=src python3 -m kitrader.historie laden`, danach `… pruefen` und die Tabelle zeigen.
   FRED sollte vom Mac aus gehen.
3. **Stooq (Silber, Platin, Palladium täglich):** Der Nutzer holt den kostenlosen Schlüssel im Browser
   (`https://stooq.com/q/d/?s=spy.us&get_apikey`) und legt `~/.config/kitrader/stooq.env` mit `STOOQ_APIKEY=…` an
   (chmod 600). Dann `laden silber platin palladium`. **Ungeprüft:** ob der URL-Parameter `apikey` heißt
   (Konstante `STOOQ_URL` in `historie/laden.py`); bei Fehler den richtigen Namen ermitteln und anpassen.
4. **DAX ab 1948:** Datei der Rückrechnung von Prof. Stehle im Browser laden
   (`https://www.wiwi.hu-berlin.de/de/professuren/bwl/bb/daten/dax/Stocks`), dann
   `python3 -m kitrader.historie einlesen dax DATEI` und die erkannten Spalten prüfen.
   **Gold täglich ab 1978:** nur noch im LBMA-Portal (`https://portal.lbma.org.uk/`, kostenlos für private Zwecke
   nach Selbstauskunft); danach `einlesen gold DATEI`. Monatswerte ab 1960 sind schon da.
5. **Links nachprüfen:** die 10 aus der Cloud gesperrten Quellen der Ereignisliste vom Mac aus abrufen.
6. **OSIRIS und KI** (in der Cloud erfolgreich, auf dem Mac noch einmal): `python3 -m kitrader osiris abrufen`, `… osiris lage`, dann eine Einschätzung
   `python3 -m kitrader einschaetzung SPY` (Treiber `claude_cli`, nutzt das Claude-Abo; ein Aufruf hat etwa 15.000 Zeichen Kontext, rund 25 s).
7. **Alles starten und ansehen** (Oberfläche in der Cloud im echten Chromium geprüft, auf dem Mac im eigenen Browser noch nicht): Doppelklick auf `kitrader-starten.command` bzw. `python3 -m kitrader start`; Reiter Übersicht, Live, Journal ansehen; beenden mit Strg+C. Nur die Oberfläche: `python3 -m kitrader oberflaeche`.
8. **Telegram:** Bot bei BotFather anlegen, `python3 -m kitrader telegram einrichten`, `telegram test`,
   im Papierbetrieb „stop“ ausprobieren.
9. **Broker-Demo:** IBKR-Demokonto und Client Portal Gateway (braucht Java – **erst fragen**), Kraken-Schlüssel
   **ohne Auszahlungsrecht**; dann `--modus demo` mit `konto`, `positionen`, `orders`. Anleitung `broker/BROKER.md`.
10. **Entscheidungen unten:** a und c bis i sind **umgesetzt** (mit Tests, 514 grün; Kurzbeschreibung in der Tabelle). **Offen** bleiben
    b (IBKR-Schutz-Stops auf `GTC`: erst im Demokonto prüfen, dann umstellen) und j (Echtgeld-Broker ohne Freigabe nur für
    Lesen und Not-Aus; Empfehlung: so lassen, ist in `LIESMICH.md` dokumentiert). Beim Broker-Demo (Punkt 9) zusätzlich prüfen,
    was neu und nur gegen nachgebildete Server getestet ist: IBKR-Währung (`/iserver/contract/{conid}/info`) und Devisenpaar,
    Kraken `Trades` (Kurszeit) und `leverage` nur für Short/Schließen (`broker/BROKER.md`, Abschnitt 3).
11. **Papierhandel 4 bis 8 Wochen:** `python3 -m kitrader start` (oder nur `autopilot --modus papier`) mit echten Kursen; der Rechner muss laufen, im Papierbetrieb werden Stops nur bei laufendem kitrader geprüft. **Nach dem Strategie-Bericht (siehe oben) kommt keine der sieben Strategien dafür als Gewinnstrategie in Frage**; der Papierhandel dient bis auf Weiteres nur dem Technik-Test (Orders, Stops, Journal, Not-Aus, Telegram).
    Vorher schriftlich festlegen, ab welchen Zahlen Echtgeld erlaubt ist (z. B. mindestens 100 Trades,
    nach Kosten im Plus, maximaler Rückgang unter X %). Wöchentlich Journal und Trefferquote ansehen.
12. **Echtgeld erst danach**, mit kleinen Grenzen und **eigenem IBKR-Konto oder Unterkonto nur für kitrader**
    (der Not-Aus fasst ohne `--alles` nur eigene Symbole an; ein eigenes Konto macht die Frage gegenstandslos).

## Offen

- **OpenAI-kompatibler KI-Treiber** (Gemini, OpenRouter, lokale Modelle über Ollama) ist noch nicht gebaut; bisher gibt es `claude_cli`, `anthropic` und `aus`.
- **GDELT-Volllauf ungeprüft:** der Nachrichten-Radar mit allen GDELT-Abfragen und Pausen ist in der Cloud nicht in voller Länge gelaufen (GDELT begrenzt Anfragen; siehe Abschnitt „Nachrichten-Radar und Welt-Rotation“).
- **Vorwärtstest-Ergebnis aus der Cloud** folgt separat.
- Weiteres steht je Abschnitt unter „Offen“ bzw. „Offene Grenzen“ und in den Entscheidungen b und j unten.

### Erledigt: letzte offene Punkte im Dashboard (26.10.2026)

- **Stop-/Ziel-Linien immer sichtbar:** `papier.js` setzt für die Kerzenreihe einen `autoscaleInfoProvider`, der Stop, Ziel und Einstand der offenen Position in die automatische Preisskala einschließt. Im Browser gemessen (Short MUSW, Stop 170, Ziel 130, Kurs 158,5): mit der Anpassung liegen Stop und Ziel bei 79 und 373 Pixeln im 480 Pixel hohen Chart, ohne sie bei −1129 und 3563 Pixeln. Wer die Preisachse von Hand zieht, behält seinen Ausschnitt.
- **Ereignis-Marker:** `static/charts/ereignisse.js`, Schalter „Ereignisse“ in der Kopfzeile (bei 1D und 1W automatisch an, sonst auf Wunsch; die Wahl merkt der Browser). Höchstens 60 Marker im sichtbaren Ausschnitt, wichtige Arten zuerst (`ART_RANG` in `kbruecke.py`); Ereignisse mit Beginn nur auf Quartal oder Jahr genau bekommen keinen Marker (der Tag wäre erfunden). Neu: Wochenkerzen (`1W`; Binance liefert sie selbst, Aktien werden aus Tageskerzen gebündelt und aus den Tageskerzen live nachgeführt). Mehrere Quellen teilen sich die Marker der Kerzenreihe (`setMarkerQuelle`: Skript, Papierhandel, Ereignisse).
- **Ein Depot:** Reiter Depot mit Gesamtsumme beider Papierkonten (in Euro, Dollar zum EZB-Kurs des manuellen Kontos), „Manuell (Charts)“ und „Autopilot (Papier)“ nebeneinander; der Autopilot-Block zeigt Positionen, offene Orders (auch Schutz-Stops und Ziele), abgeschlossene Trades und Ergebnis aus `papier.db` (`Bruecke.depot`, nur lesend, `/api/k/depot`).
- **Echter KI-Test über die Oberfläche:** Reiter KI, Wert „BTC“, Treiber `claude_cli`, Modell `sonnet`: eine echte Einschätzung für BTC/EUR in 54 s (Kurs 71.706 EUR): 1 Tag seitwärts 40 %, 3 Monate steigt 36 %, 1 Jahr steigt 40 %, mit Begründung, Gegenargumenten und Widerlegungsgrenze, im Journal. Dafür neu: `ki.claude_modell` (Standard `sonnet`), die CLI bekommt `--model`.
- **Bleibt offen:** die Ansicht in deinem eigenen Browser; bei geöffnetem Chart mit sehr vielen Ereignissen im Bild (Wochenkerzen über viele Jahre) zeigt der Schalter nur die 60 wichtigsten, zum Rest hineinzoomen.

## Entscheidungen (aus der Sicherheitsprüfung, Empfehlung jeweils die sicherere Variante)

| | Frage | Stand |
|---|---|---|
| a | Fehlt die Kurszeit (IBKR ohne `_updated`, Kraken immer Abrufzeit), gilt der Kurs dann als veraltet? | **umgesetzt.** `Kurs.zeit_geschaetzt` (Standard False, rückwärtsverträglich); `risiko.py` lehnt Eröffnungen mit so einem Kurs ab, Schließen bleibt erlaubt. IBKR setzt es ohne `_updated`; Kraken nimmt die Zeit des letzten Handels (`/0/public/Trades?count=1`, der Ticker hat keine Zeit) und setzt es nur, wenn die fehlt. |
| b | IBKR-Schutz-Stops laufen nur einen Tag (`DAY`) | **offen:** im Demokonto prüfen, dann `GTC` |
| c | Kraken hängt nur den Stop an, niemand nimmt das Ziel mit | **umgesetzt.** `Broker.fuehrt_ziel_als_order()` (Kraken: False, wenn ein Stop mitgeht); der Autopilot merkt sich das Ziel je Position im Journal (Tabelle `eigene`, übersteht Neustart), stellt bei Erreichen glatt und storniert den Stop; Meldung `ziel_erreicht`. |
| d | Schutz-Check und Not-Aus fassen alle Positionen des Kontos an | **umgesetzt.** Eigene Symbole = `autopilot.symbole` plus vom Autopiloten eröffnete (Journal); `alles_schliessen(symbole=…)`; fremde Positionen werden nur gemeldet. Not-Aus aus Oberfläche, Telegram und `notaus` ebenso; `notaus --alles` = ganzes Konto mit Warnung. `LIESMICH.md`: Rat eigenes Konto/Unterkonto. |
| e | Tagesverlustgrenze sperrt nur neue Positionen | **umgesetzt.** Prüfung in jedem Durchlauf (realisiert plus unrealisiert); bei Erreichen werden alle eigenen Positionen glattgestellt (bei Fehlschlag Wiederholung), Tag gesperrt; Einstellung `risiko.tagesverlust_schliessen = true` (Standard); Telegram über den Haken. |
| f | offene, noch nicht ausgeführte Eröffnungsorders zählen nicht mit | **umgesetzt.** `Lage.offene_orders`; zählen bei Positionszahl, Positionsgröße und Risiko mit (Schutz-Stops und Ziele gegen eine Position nicht); nicht lesbare offene Orders = keine Eröffnung. |
| g | Kraken-Hebel gilt auch für den Verkauf von Spot-Bestand | **umgesetzt.** `leverage` nur für Short eröffnen/vergrößern und für das Schließen einer Margin-Position; nie für Spot-Verkauf; unklare Mischfälle werden nicht gesendet. Ob Kraken zum Schließen `leverage` verlangt, ist unbestätigt. |
| h | Risiko rechnet ohne Währungsumrechnung | **umgesetzt.** `Kurs.waehrung`, `Lage.wechselkurs`; Größe und Risiko in Kontowährung; Kurs vom Broker (`wechselkurs()`: IBKR Devisenpaar, Kraken Paar wie EURUSD) vor Einstellung `[wechselkurse]`; ohne Kurs und bei abweichender Währung keine Eröffnung mit klarem Grund. |
| i | Autopilot hat keinen Rückruf-Haken (Telegram umhüllt das Journal) | **umgesetzt.** `Autopilot.beobachter` (`f(art, daten)`), Ereignisse `start, ende, entscheidung, ablehnung, ausfuehrung, ergebnis, tagesverlust, notaus, tagesabschluss, ziel_erreicht, warnung`; Fehler im Beobachter erreichen den Handel nie; `telegram.py` nutzt den Haken, das Journal wird nicht mehr umhüllt. |
| j | `konto/positionen/orders/notaus --modus echt` bauen einen Echtgeld-Broker ohne Freigabedatei | **offen** (Empfehlung: nur lesen bzw. Not-Aus erlauben, so lassen und dokumentieren; nicht geändert) |

## Hinweise

- **Keine Anlageberatung, kein Erfolgsversprechen.** Die meisten Privatanleger verlieren beim Daytrading Geld.
- **Daten nur privat:** Die Bedingungen der Quellen stehen in `historie/reihen.py` und `HISTORIE.md`; abgerufene Daten nicht veröffentlichen.
- **Steuer:** Gewinne sind steuerpflichtig; IBKR führt keine deutsche Abgeltungsteuer ab (Anlage KAP), Krypto-Gewinne
  innerhalb eines Jahres sind private Veräußerungsgeschäfte. Mit dem Steuerberater klären.
- **Schlüssel** nur unter `~/.config/kitrader/` mit chmod 600, nie im Repo.
