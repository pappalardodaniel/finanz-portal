# STRATEGIE-BERICHT – taugen einfache Daytrading-Regeln nach Kosten?

**Ergebnis in einem Satz:** Auf 90 Tagen echter Kraken-Kurse (BTC/EUR und ETH/EUR) war nach Kosten **keiner** der 7 getesteten Handelsansätze im Plus: Von 19 Kombinationen (Ansatz, Paar, Takt) mit genug Trades (mindestens 30) verlor jede Geld, kein Ansatz war erkennbar besser als zufällige Einstiege (im Mittel schnitten 49 % der Zufallsläufe besser ab, bei reinem Raten wären es 50 %), und über alle 42 Kombinationen standen 31.461 € Kosten nur 498 € Ergebnis vor Kosten gegenüber.

*Stand: 26.10.2026. Geschrieben für Leser ohne Programmierkenntnisse. Keine Anlageberatung und kein Erfolgsversprechen: Eine
Rechnung über die Vergangenheit sagt nichts sicher über die Zukunft, und die meisten Privatanleger verlieren beim Daytrading Geld.*

## 1. Worum es ging

kitrader bringt zwei Beispiel-Strategien mit. Hier wurden fünf weitere, übliche Handelsansätze als **Kandidaten** dazugebaut und alle sieben
auf **echten Kursen** von Kraken geprüft. Die Frage ist bewusst nüchtern: *Bleibt nach Gebühren und Spreads überhaupt etwas übrig – und ist das
besser, als hätte man zu zufälligen Zeitpunkten gekauft?* Dazu kam der Vergleich mit dem einfachsten Gegenstück, **Kaufen und Halten**.

## 2. Datenbasis

Quelle: öffentliche Kraken-API (`/0/public/Trades`, ohne Schlüssel). Kraken liefert fertige Kerzen nur für die letzten 720 Kerzen je Takt (bei 5 Minuten gut zweieinhalb Tage),
deshalb wurden die einzelnen Abschlüsse geholt (BTC/EUR 1,62 Mio. in 1.621 Abrufen, ETH/EUR 0,90 Mio. in 900 Abrufen) und selbst zu Kerzen verdichtet. Gegenprobe mit Krakens
eigenen 5-Minuten-Kerzen: 41 von 42 Kerzen identisch, eine mit 0,0001 BTC Volumenabweichung. Abgerufen am 26.10.2026 mit höflichem Tempo
(1 Sekunde Pause nach jedem Abruf; BTC/EUR und ETH/EUR liefen zeitweise gleichzeitig, zusammen also bis etwa 1,5 Abrufe pro Sekunde, im Schnitt etwa 1).

| Paar | Takt | Kerzen | Lücken | Zeitraum (UTC) | Kurs (Start -> Ende) |
|---|---|---:|---:|---|---|
| BTC/EUR | 5m | 25.930 | 4 | 28.07.2026 bis 26.10.2026 | 55.679 -> 71.285 (28 %) |
| BTC/EUR | 15m | 8.644 | 0 | 28.07.2026 bis 26.10.2026 | 55.679 -> 71.448 (28 %) |
| BTC/EUR | 1h | 2.161 | 0 | 28.07.2026 bis 26.10.2026 | 55.679 -> 71.448 (28 %) |
| ETH/EUR | 5m | 25.916 | 15 | 28.07.2026 bis 26.10.2026 | 1.652 -> 2.069 (25 %) |
| ETH/EUR | 15m | 8.643 | 0 | 28.07.2026 bis 26.10.2026 | 1.652 -> 2.069 (25 %) |
| ETH/EUR | 1h | 2.160 | 0 | 28.07.2026 bis 26.10.2026 | 1.652 -> 2.065 (25 %) |

"Lücken" sind Kerzen, in denen es keinen einzigen Abschluss gab (zum Beispiel bei einer Wartung). Sie wurden nicht erfunden, sondern fehlen. Im Datensatz
fanden sich keine unplausiblen Kerzen (Hoch/Tief passen zu Eröffnung/Schluss) und keine Kurssprünge über 3 % zwischen zwei Kerzen.

## 3. Kostenannahmen

| Posten | Annahme | Quelle / Begründung |
|---|---|---|
| Taker-Gebühr | **0,80 %** je Ausführung (Marktorders und Stop-Loss) | Kraken Pro, niedrigste Stufe (Tier 1, ab 0 USD 30-Tage-Volumen), kraken.com/features/fee-schedule, abgerufen am 26.10.2026 |
| Maker-Gebühr | **0,40 %** (nur das Gewinnziel als Limit-Order) | dieselbe Seite |
| Spread | 0,05 % insgesamt (je Seite 0,025 %) | bewusst vorsichtig: am 26.10.2026 gemessen etwa 0,0001 % (BTC/EUR) und 0,006 % (ETH/EUR) |
| Schlupf | 0,02 % bei Marktorders und Stops | Annahme für Einsätze von rund 1.000 € |
| Feste Gebühr | 0 € | Kraken erhebt keine |

Annahmen im Einzelnen: Jeder Einstieg ist eine Marktorder (Taker). Ein Stop-Loss wird als Marktorder ausgeführt (Taker, mit Schlupf). Nur das Gewinnziel ist
eine Limit-Order und zahlt die günstigere Maker-Gebühr, füllt aber erst, wenn der Kurs es um den halben Spread durchläuft. **Eine Runde (kaufen und
wieder verkaufen) kostet damit rund 1,6 % (zwei Taker) bis 1,2 % (Taker plus Maker), zuzüglich etwa 0,09 % für Spread und Schlupf** – bei einem Einsatz von 1.000 € sind das
12 bis 17 €. Im Schnitt der aussagefähigen Kombinationen lagen die Kosten bei 1,60 % je Trade.

Die Kraken-Gebühren sinken mit dem eigenen 30-Tage-Handelsvolumen. Wer häufig handelt, rutscht darum nach einigen Wochen in eine höhere Stufe: ab 100.000 USD
(Tier 6) kostet es 0,12 % Maker und 0,25 % Taker. Diesen Fall zeigt Abschnitt 6.4 als Gegenprobe. Nicht abgebildet sind Steuern, Einzahlungs- und Auszahlungskosten,
Margin-Gebühren (Short-Verkäufe wären bei Kraken Margin-Geschäfte, deshalb wurde nur *Long* getestet), Teilausführungen, Ausfälle und die Tatsache, dass eine Limit-Order in
der Wirklichkeit nicht sicher füllt.

## 4. Die getesteten Ansätze

Alle sieben sind einfache, in Büchern übliche Regeln. Die fünf Kandidaten sind **ungeprüft** (`src/kitrader/strategie/kandidaten.py`); die zwei Beispiele gab es schon. "ATR" ist die
mittlere Schwankungsbreite einer Kerze. Stop-Loss und Gewinnziel sind in ATR bzw. als Vielfaches des Risikos gesetzt. Die Spalte rechts zeigt, was im Training
durchprobiert wurde.

| Name | Art | Idee | Im Training durchprobiert |
|---|---|---|---|
| `ausbruch` | Beispiel | Eröffnungs-Ausbruch: Die ersten Kerzen des (UTC-)Tages bilden eine Spanne; Schluss darüber = Kauf, Stop am unteren Ende. | range_kerzen 3, 6, 12; crv 1.0, 1.5, 2.0 (9 Kombinationen) |
| `vwap` | Beispiel | Rückkehr zum Tages-Durchschnittspreis (VWAP): Kauf, wenn der Kurs weit darunter liegt und die Kerze nach oben dreht. | abstand_atr 1.5, 2.0, 2.5; stop_atr 1.0, 1.5 (6 Kombinationen) |
| `donchian` | Kandidat | Donchian-Ausbruch: Kauf, wenn eine Kerze über dem Hoch der letzten N Kerzen schließt; Stop in ATR darunter. | kanal 20, 55; stop_atr 1.5, 3.0; crv 2.0, 4.0 (8 Kombinationen) |
| `bollinger_rsi` | Kandidat | Rückkehr zur Mitte: Kauf unter dem unteren Bollinger-Band bei niedrigem RSI; Ziel ist der Mittelwert. | abw 1.5, 2.0, 2.5; rsi_max 25.0, 35.0; stop_atr 1.5, 3.0 (12 Kombinationen) |
| `ma_trend` | Kandidat | Gleitende Durchschnitte: Kauf, wenn der schnelle den langsamen von unten kreuzt und der Kurs über dem Trendfilter liegt; Ausstieg bei Rückkreuzung. | schnell 8, 12; langsam 24, 48; stop_atr 2.0, 3.0 (8 Kombinationen) |
| `momentum` | Kandidat | Momentum: Kauf nach einem starken Anstieg über N Kerzen (gemessen in ATR), wenn die letzte Kerze im Plus schließt. | n 12, 36; schwelle_atr 4.0, 8.0; stop_atr 1.5, 3.0 (8 Kombinationen) |
| `volumen` | Kandidat | Volumen-Ausbruch: wie Donchian, aber nur bei mindestens 2- bis 3,5-fachem Umsatz der Ausbruchskerze. | kanal 20, 50; vol_faktor 2.0, 3.5; stop_atr 1.5, 3.0 (8 Kombinationen) |

## 5. So wurde getestet

- **Walk-forward statt Schönrechnen.** Die 90 Tage wurden zeitlich geteilt: die ersten 40 % (rund 36 Tage) zum Einstellen,
  die restlichen 60 % in **4 Testabschnitten (Faltungen)** zu je rund 14 Tagen. Für jeden Abschnitt wählt das Programm die Parameter **nur
  aus den Daten davor** (bestes Netto-Ergebnis nach Kosten, mindestens 10 Trades) und rechnet den Abschnitt danach genau einmal. **Alle Tabellen unten zählen nur diese Testabschnitte zusammen.**
  Das Einstellen auf den Trainingsdaten schönt deren Ergebnis; es wird nirgends berichtet. Die Strategie sieht die Kerzen vor dem Abschnitt als Verlauf (damit ein 100-Kerzen-Durchschnitt nicht in jedem Abschnitt von vorn anfängt),
  handelt aber erst im Abschnitt, und nie sieht sie Kerzen aus der Zukunft.
- **Echte Regeln.** Jede Order geht durch dieselbe Risikoprüfung wie im Live-Betrieb: höchstens 10 % des Kapitals je Position, Risiko je Trade höchstens 0,5 %, Pflicht-Stop, Tagesverlustgrenze 1 % (die Standardgrenzen von kitrader; die eigene Einstellungsdatei liest das Labor bewusst nicht, damit das Ergebnis wiederholbar bleibt). Startkapital 10.000 €.
  Nur *Long*. Daytrading: Am Ende jedes UTC-Tages wird alles glattgestellt. Ein Signal am Ende einer Kerze wird zum Eröffnungskurs der nächsten ausgeführt. Berühren Stop und Ziel dieselbe Kerze, gilt der Stop.
- **Kaufen und Halten** über denselben Testzeitraum (zum Eröffnungskurs der ersten, Verkauf zum Schlusskurs der letzten Kerze), mit denselben Kosten, hier als 1.000 € Einsatz gerechnet (ein Trade der Kandidaten ist ebenfalls etwa 1.000 € groß).
- **Zufall** (200 Läufe): Jeder echte Trade eines Kandidaten bekommt einen Zufalls-Zwilling mit **gleichem Einsatz, gleichem Stop- und Ziel-Abstand, gleichen Ausstiegsregeln und gleichen Kosten** – nur der Einstieg
  liegt an einer zufälligen Kerze des Testzeitraums. Gezeigt wird der **Anteil der Zufallsläufe, die mindestens so gut abschneiden wie der Kandidat**. Hoher Anteil (zum Beispiel 50 %) heißt: kein erkennbarer Vorteil gegenüber Raten.
  Unter 5 % wäre auffällig, kommt bei vielen Tests aber auch durch Zufall vor.
- **Unter 30 Trades im Testteil gibt es keine Aussage.** Diese Zeilen stehen zur Transparenz in den Tabellen (Spalte "Aussage" = nein), werden aber nirgends gedeutet.

Spalten der Tabellen: *Netto* = Ergebnis nach allen Kosten; *Brutto* = Ergebnis vor Kosten (zu Mittelkursen); *Kosten* = Gebühren plus Spread und Schlupf; *Kosten/Brutto* = Kostenanteil am Brutto-Ergebnis ("Brutto ≤ 0" heißt: schon vor
den Kosten kein Gewinn); *Treffer* = Anteil der Gewinn-Trades; *Profitfaktor* = Summe der Gewinne durch Summe der Verluste (unter 1 = Verlust); *Max. Rückgang* = größter Rückgang des Kontos im Testzeitraum;
*Sharpe* = Gewinn im Verhältnis zur Schwankung (jährlich gerechnet; negativ = Verlust); *Zufall besser* = siehe oben.

## 6. Ergebnisse (nur Testabschnitte)

### 6.1 Gesamtbild

Kurzfazit: 42 Kombinationen (Kandidat, Paar, Takt), davon 19 mit mindestens 30 Test-Trades (nur dort ist eine Aussage erlaubt).

- Nach Kosten im Plus: 0 von 19. Schon vor Kosten im Plus: 7. Besser als Kaufen und Halten (Netto-Euro): 0.
- Besser als 95 % der Zufallsläufe: 1 (allein durch Zufall zu erwarten: etwa 1,0).
- Alle Kombinationen zusammen: Brutto 497,67 €, Kosten 31.460,86 €.
- Bestes Netto-Ergebnis mit Aussage: -498,06 € (BTC/EUR 15m volumen).

| Takt | Kombinationen | davon mit Aussage (≥ 30 Trades) | davon nach Kosten im Plus | Trades | Ergebnis vor Kosten | Kosten | Ergebnis nach Kosten |
|---|---:|---:|---:|---:|---:|---:|---:|
| 5m | 14 | 11 | 0 | 1.336 | 100,07 € | 20.767,31 € | -20.667,24 € |
| 15m | 14 | 8 | 0 | 489 | 228,99 € | 7.687,11 € | -7.458,12 € |
| 1h | 14 | 0 | 0 | 187 | 168,61 € | 3.006,44 € | -2.837,83 € |

In der Summe über alle 42 Kombinationen (2.012 Trades) standen 497,67 € Ergebnis vor Kosten 31.460,86 € Kosten gegenüber, macht -30.963,19 € nach Kosten.
Je Trade brachte ein Signal im Schnitt 0,005 % vor Kosten, die Kosten betrugen 1,60 % (Mittel über die Kombinationen mit Aussage).
Training: In keiner von 168 Faltungen mit Trades war schon das *beste* Ergebnis im Training nach Kosten im Plus; im Testabschnitt waren 6 von 168 Faltungen im Plus.
In 8 von 168 Faltungen fand das Training keine Einstellung mit genug Trades; dort gelten die Standardwerte des Ansatzes (Takt 5m: 0, 15m: 0, 1h: 8).

### 6.2 Tabellen je Paar und Takt

**BTC/EUR, Takt 5m** – Testzeitraum 2026-09-03 bis 2026-10-26 (53 Tage, 4 Faltungen)

| Kandidat | Trades | Netto € | Brutto € | Kosten € | Kosten/Brutto | Treffer | Profitfaktor | Max. Rückgang | Sharpe | Zufall besser | Aussage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| ausbruch | 21 | -284,07 | 29,55 | 313,62 | über 999 % | 10 % | 0,01 | 2,8 % | -11,96 | - | nein |
| vwap | 200 | -3.074,71 | -24,84 | 3.049,87 | Brutto ≤ 0 | 0 % | 0,00 | 30,7 % | -26,10 | 77 % | ja |
| donchian | 134 | -2.083,11 | 41,28 | 2.124,39 | über 999 % | 8 % | 0,03 | 20,8 % | -33,70 | 24 % | ja |
| bollinger_rsi | 51 | -823,47 | -10,76 | 812,71 | Brutto ≤ 0 | 0 % | 0,00 | 8,2 % | -13,69 | 70 % | ja |
| ma_trend | 132 | -2.117,99 | -15,01 | 2.102,99 | Brutto ≤ 0 | 0 % | 0,00 | 21,2 % | -28,57 | 72 % | ja |
| momentum | 8 | -123,88 | 2,05 | 125,93 | über 999 % | 12 % | 0,02 | 1,2 % | -5,42 | - | nein |
| volumen | 94 | -1.459,17 | -4,35 | 1.454,82 | Brutto ≤ 0 | 3 % | 0,01 | 14,6 % | -29,16 | 47 % | ja |
| *Kaufen und Halten* | 1 | 50,34 | 68,24 | 17,90 | - | - | - | 8,7 % | - | - | - |

**BTC/EUR, Takt 15m** – Testzeitraum 2026-09-03 bis 2026-10-26 (53 Tage, 4 Faltungen)

| Kandidat | Trades | Netto € | Brutto € | Kosten € | Kosten/Brutto | Treffer | Profitfaktor | Max. Rückgang | Sharpe | Zufall besser | Aussage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| ausbruch | 19 | -218,18 | 44,28 | 262,46 | 593 % | 0 % | 0,00 | 2,2 % | -11,26 | - | nein |
| vwap | 53 | -812,98 | 32,27 | 845,25 | über 999 % | 2 % | 0,00 | 8,1 % | -13,03 | 36 % | ja |
| donchian | 54 | -905,04 | -20,35 | 884,70 | Brutto ≤ 0 | 9 % | 0,05 | 9,1 % | -25,03 | 49 % | ja |
| bollinger_rsi | 16 | -276,82 | -21,15 | 255,67 | Brutto ≤ 0 | 0 % | 0,00 | 2,8 % | -9,14 | - | nein |
| ma_trend | 40 | -646,03 | -6,93 | 639,10 | Brutto ≤ 0 | 5 % | 0,02 | 6,5 % | -16,74 | 46 % | ja |
| momentum | 20 | -237,08 | 69,06 | 306,14 | 443 % | 25 % | 0,07 | 2,4 % | -9,94 | - | nein |
| volumen | 37 | -498,06 | 65,20 | 563,26 | 864 % | 19 % | 0,07 | 5,0 % | -13,98 | 5 % | ja |
| *Kaufen und Halten* | 1 | 52,74 | 70,69 | 17,94 | - | - | - | 8,6 % | - | - | - |

**BTC/EUR, Takt 1h** – Testzeitraum 2026-09-03 bis 2026-10-26 (53 Tage, 4 Faltungen)

| Kandidat | Trades | Netto € | Brutto € | Kosten € | Kosten/Brutto | Treffer | Profitfaktor | Max. Rückgang | Sharpe | Zufall besser | Aussage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| ausbruch | 28 | -392,54 | 56,71 | 449,25 | 792 % | 14 % | 0,15 | 4,2 % | -10,74 | - | nein |
| vwap | 1 | -13,92 | 2,99 | 16,91 | 565 % | 0 % | 0,00 | 0,1 % | -2,60 | - | nein |
| donchian | 14 | -190,24 | 41,32 | 231,56 | 560 % | 14 % | 0,23 | 2,1 % | -5,80 | - | nein |
| bollinger_rsi | 14 | -204,78 | 17,84 | 222,62 | über 999 % | 0 % | 0,00 | 2,0 % | -8,53 | - | nein |
| ma_trend | 9 | -175,76 | -28,72 | 147,03 | Brutto ≤ 0 | 11 % | 0,02 | 1,8 % | -7,35 | - | nein |
| momentum | 12 | -187,74 | 5,46 | 193,20 | über 999 % | 17 % | 0,17 | 1,9 % | -6,23 | - | nein |
| volumen | 16 | -180,99 | 66,39 | 247,38 | 373 % | 25 % | 0,22 | 2,2 % | -6,83 | - | nein |
| *Kaufen und Halten* | 1 | 52,74 | 70,69 | 17,94 | - | - | - | 8,6 % | - | - | - |

**ETH/EUR, Takt 5m** – Testzeitraum 2026-09-03 bis 2026-10-26 (53 Tage, 4 Faltungen)

| Kandidat | Trades | Netto € | Brutto € | Kosten € | Kosten/Brutto | Treffer | Profitfaktor | Max. Rückgang | Sharpe | Zufall besser | Aussage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| ausbruch | 21 | -301,24 | 12,22 | 313,47 | über 999 % | 5 % | 0,02 | 3,0 % | -11,93 | - | nein |
| vwap | 195 | -2.992,31 | -27,44 | 2.964,87 | Brutto ≤ 0 | 0 % | 0,00 | 29,9 % | -24,88 | 74 % | ja |
| donchian | 128 | -2.032,28 | 7,80 | 2.040,09 | über 999 % | 10 % | 0,05 | 20,3 % | -29,56 | 46 % | ja |
| bollinger_rsi | 53 | -741,24 | 75,22 | 816,46 | über 999 % | 11 % | 0,05 | 7,4 % | -11,66 | 11 % | ja |
| ma_trend | 127 | -1.881,15 | 99,30 | 1.980,46 | über 999 % | 4 % | 0,04 | 18,8 % | -24,69 | 10 % | ja |
| momentum | 70 | -1.115,77 | -27,65 | 1.088,12 | Brutto ≤ 0 | 4 % | 0,01 | 11,2 % | -22,80 | 74 % | ja |
| volumen | 102 | -1.636,84 | -57,31 | 1.579,52 | Brutto ≤ 0 | 5 % | 0,01 | 16,4 % | -29,66 | 70 % | ja |
| *Kaufen und Halten* | 1 | -14,20 | 2,60 | 16,80 | - | - | - | 17,1 % | - | - | - |

**ETH/EUR, Takt 15m** – Testzeitraum 2026-09-03 bis 2026-10-26 (53 Tage, 4 Faltungen)

| Kandidat | Trades | Netto € | Brutto € | Kosten € | Kosten/Brutto | Treffer | Profitfaktor | Max. Rückgang | Sharpe | Zufall besser | Aussage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| ausbruch | 28 | -279,37 | 117,19 | 396,56 | 338 % | 14 % | 0,11 | 2,8 % | -10,27 | - | nein |
| vwap | 56 | -902,40 | -6,99 | 895,41 | Brutto ≤ 0 | 4 % | 0,01 | 9,0 % | -11,99 | 68 % | ja |
| donchian | 48 | -843,77 | -53,68 | 790,09 | Brutto ≤ 0 | 6 % | 0,08 | 8,7 % | -18,06 | 62 % | ja |
| bollinger_rsi | 26 | -348,28 | 54,12 | 402,40 | 744 % | 27 % | 0,13 | 3,6 % | -7,57 | - | nein |
| ma_trend | 38 | -515,40 | 74,04 | 589,43 | 796 % | 18 % | 0,07 | 5,2 % | -13,17 | 4 % | ja |
| momentum | 16 | -282,63 | -26,92 | 255,72 | Brutto ≤ 0 | 12 % | 0,03 | 2,8 % | -9,43 | - | nein |
| volumen | 38 | -692,08 | -91,16 | 600,92 | Brutto ≤ 0 | 16 % | 0,10 | 7,1 % | -13,53 | 79 % | ja |
| *Kaufen und Halten* | 1 | -14,54 | 2,26 | 16,80 | - | - | - | 16,7 % | - | - | - |

**ETH/EUR, Takt 1h** – Testzeitraum 2026-09-03 bis 2026-10-26 (53 Tage, 4 Faltungen)

| Kandidat | Trades | Netto € | Brutto € | Kosten € | Kosten/Brutto | Treffer | Profitfaktor | Max. Rückgang | Sharpe | Zufall besser | Aussage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| ausbruch | 23 | -386,35 | -13,74 | 372,61 | Brutto ≤ 0 | 13 % | 0,09 | 4,0 % | -11,32 | - | nein |
| vwap | 1 | -15,94 | 0,97 | 16,91 | über 999 % | 0 % | 0,00 | 0,2 % | -2,60 | - | nein |
| donchian | 13 | -275,80 | -57,28 | 218,52 | Brutto ≤ 0 | 15 % | 0,13 | 2,8 % | -7,13 | - | nein |
| bollinger_rsi | 21 | -250,91 | 80,97 | 331,88 | 410 % | 24 % | 0,20 | 2,6 % | -6,10 | - | nein |
| ma_trend | 12 | -191,33 | 2,01 | 193,34 | über 999 % | 8 % | 0,11 | 1,9 % | -7,08 | - | nein |
| momentum | 11 | -169,15 | 2,86 | 172,01 | über 999 % | 18 % | 0,07 | 1,7 % | -7,41 | - | nein |
| volumen | 12 | -202,39 | -9,16 | 193,22 | Brutto ≤ 0 | 25 % | 0,10 | 2,0 % | -7,57 | - | nein |
| *Kaufen und Halten* | 1 | -16,03 | 0,74 | 16,77 | - | - | - | 16,3 % | - | - | - |

*Die Zeile "Kaufen und Halten" rechnet einen einmaligen Einsatz von 1.000 € über den ganzen Testzeitraum; die Kandidaten setzen bei jedem Trade rund 1.000 € ein, handeln aber nur zeitweise. Beide Zahlen sind deshalb nur grob vergleichbar. "Zufall besser" ist nur bei mindestens 30 Trades angegeben.*

### 6.3 Kaufen und Halten

| Paar | Testzeitraum | Kurs | Preisänderung | nach Kosten | größter Rückgang unterwegs |
|---|---|---|---:|---|---:|
| BTC/EUR | 03.09.2026 bis 26.10.2026 (53 Tage) | 66.731 -> 71.285 | 6,8 % | 5,0 % (50,34 € auf 1.000 €) | 8,7 % |
| ETH/EUR | 03.09.2026 bis 26.10.2026 (53 Tage) | 2.064 -> 2.069 | 0,3 % | -1,4 % (-14,20 € auf 1.000 €) | 17,1 % |

### 6.4 Gegenproben: niedrigere Gebühren und gar keine Kosten

Zwei Zusatzläufe mit denselben Daten und demselben Verfahren (Parameter wieder nur aus dem Training gewählt), nur andere Kosten:

| Lauf | Kosten | Kombinationen mit Aussage | davon im Plus nach Kosten | vor Kosten im Plus | besser als Halten | besser als 95 % der Zufallsläufe | Ø "Zufall besser" | Netto der Kandidaten / Zufall (Median), Summe | Brutto / Kosten (alle 42) |
|---|---|---|---:|---:|---:|---|---:|---|---|
| A: Kraken Stufe 1 (Hauptlauf) | Taker 0,80 %, Maker 0,40 %, Spread 0,05 %, Schlupf 0,02 % | 19 von 42 | 0 | 7 | 0 | 1 (Zufall: ca. 1,0) | 49 % | -25.774 € / -25.933 € | 497,67 € / 31.460,86 € |
| B: Kraken Stufe 6 (ab 100.000 USD Volumen) | Taker 0,25 %, Maker 0,12 %, Spread 0,05 %, Schlupf 0,02 % | 19 von 42 | 0 | 8 | 0 | 0 (Zufall: ca. 1,0) | 47 % | -9.728 € / -9.843 € | 518,60 € / 11.815,77 € |
| C: ohne jede Kosten | Taker 0,00 %, Maker 0,00 %, Spread 0,00 %, Schlupf 0,00 % | 24 von 42 | 14 | 14 | 9 | 0 (Zufall: ca. 1,2) | 48 % | 120 € / 143 € | 463,49 € / 0,00 € |

*Ø "Zufall besser" = Mittelwert über die Kombinationen mit Aussage; bei einem Signal ohne jede Information erwartet man 50 %. "Netto der Kandidaten / Zufall" = Summe der Netto-Ergebnisse der Kombinationen mit Aussage, daneben die Summe der Mediane ihrer Zufalls-Zwillinge.*

Lauf B zeigt, was ein sehr aktiver Händler nach Wochen bei höherem Handelsvolumen zahlen würde (0,25 % Taker / 0,12 % Maker). Lauf C ist ein Gedankenexperiment ohne Gebühren, Spread und Schlupf:
Er zeigt, ob die Signale *überhaupt* etwas wissen, bevor Kosten ins Spiel kommen.

- Takt 5m: ohne Kosten 12 Kombination(en) mit Aussage, davon 7 im Plus und 0 besser als 95 % der Zufallsläufe; mit Stufe-6-Gebühren 0 von 11 im Plus.
- Takt 15m: ohne Kosten 11 Kombination(en) mit Aussage, davon 6 im Plus und 0 besser als 95 % der Zufallsläufe; mit Stufe-6-Gebühren 0 von 8 im Plus.
- Takt 1h: ohne Kosten 1 Kombination(en) mit Aussage, davon 1 im Plus und 0 besser als 95 % der Zufallsläufe; mit Stufe-6-Gebühren keine Kombination mit Aussage.

Je Zelle: Netto-Euro / Trades / Anteil Zufallsläufe, die besser waren. In Klammern (nur Netto-Euro / Trades): weniger als 30 Trades, keine Aussage.

**Lauf B – Kraken Stufe 6 (Taker 0,25 %, Maker 0,12 %, Spread 0,05 %, Schlupf 0,02 %):**

| Kandidat | BTC/EUR 5m | BTC/EUR 15m | BTC/EUR 1h | ETH/EUR 5m | ETH/EUR 15m | ETH/EUR 1h |
|---|---:|---:|---:|---:|---:|---:|
| `ausbruch` | (-67 / 21) | (-118 / 26) | (-100 / 28) | (-116 / 26) | (-18 / 28) | (-150 / 22) |
| `vwap` | -1.334 / 242 / 73 % | -263 / 53 / 28 % | (-3 / 1) | -1.272 / 236 / 62 % | -332 / 59 / 54 % | (-5 / 1) |
| `donchian` | -711 / 134 / 24 % | -331 / 54 / 52 % | (-40 / 14) | -715 / 128 / 46 % | -353 / 55 / 60 % | (-134 / 13) |
| `bollinger_rsi` | -295 / 51 / 61 % | (-120 / 16) | (-99 / 18) | -226 / 55 / 9 % | (-85 / 26) | (-19 / 25) |
| `ma_trend` | -769 / 134 / 68 % | -230 / 40 / 49 % | (-67 / 8) | -615 / 127 / 12 % | -260 / 58 / 11 % | (-54 / 11) |
| `momentum` | (-42 / 8) | (-37 / 20) | (-62 / 12) | -408 / 70 / 77 % | (-116 / 16) | (-57 / 11) |
| `volumen` | -514 / 94 / 47 % | -129 / 37 / 5 % | (-2 / 15) | -612 / 102 / 74 % | -357 / 47 / 82 % | (-61 / 11) |

**Lauf C – ohne jede Kosten:**

| Kandidat | BTC/EUR 5m | BTC/EUR 15m | BTC/EUR 1h | ETH/EUR 5m | ETH/EUR 15m | ETH/EUR 1h |
|---|---:|---:|---:|---:|---:|---:|
| `ausbruch` | (31 / 22) | (40 / 27) | (57 / 28) | (73 / 29) | (98 / 28) | (-26 / 22) |
| `vwap` | -1 / 428 / 78 % | 31 / 104 / 27 % | (3 / 1) | 23 / 338 / 62 % | 8 / 133 / 56 % | (1 / 1) |
| `donchian` | -38 / 192 / 66 % | 45 / 70 / 18 % | (10 / 23) | 40 / 206 / 32 % | -44 / 96 / 55 % | (-72 / 26) |
| `bollinger_rsi` | 72 / 247 / 12 % | (-31 / 24) | (3 / 23) | 23 / 316 / 60 % | 62 / 37 / 8 % | (116 / 25) |
| `ma_trend` | -16 / 220 / 74 % | -18 / 61 / 64 % | (2 / 15) | 84 / 133 / 20 % | 54 / 63 / 15 % | (28 / 11) |
| `momentum` | 3 / 302 / 57 % | 33 / 35 / 20 % | (-7 / 15) | -7 / 247 / 70 % | -111 / 93 / 94 % | 14 / 32 / 26 % |
| `volumen` | 19 / 125 / 42 % | -1 / 65 / 40 % | (30 / 18) | -85 / 134 / 88 % | -70 / 49 / 73 % | (-13 / 11) |

## 7. Einordnung


**Die Kosten sind das Problem, nicht (nur) die Signale.** Eine Runde kostet bei Kraken Stufe 1 rund 1,3 bis 1,7 %. Eine einzelne Kerze schwankt dagegen im Mittel nur um
0,08 % (BTC/EUR, 5 Minuten; Hoch bis Tief, mittlere Kerze) bzw. 0,37 % (BTC/EUR, 1 Stunde). Ein Trade müsste also ein Vielfaches
einer typischen Kerzenbewegung einfangen (bei 5 Minuten rund das 19-Fache), nur um seine Kosten wieder hereinzuholen. Die Signale brachten im Schnitt 0,005 % je Trade vor Kosten; die Kosten betrugen 1,60 %. Entsprechend
verlor jede Kombination mit Aussage Geld; insgesamt standen 497,67 € Ergebnis vor Kosten 31.460,86 € Kosten gegenüber.
Das Verhältnis Kosten zu Ergebnis vor Kosten liegt bei etwa 63 zu 1.

**Zufall.** Von 19 aussagefähigen Kombinationen waren 1 besser als 95 % der Zufallsläufe; bei 19 Tests rechnet man allein durch Zufall mit etwa 1,0.
Das ist **kein Hinweis auf einen echten Vorteil**.
Auffällig (unter 5 %) war: ETH/EUR 15m ma_trend (Zufall besser: 4 %, Netto -515,40 €, Brutto 74,04 €). Das ist nach Kosten ein Verlust. Im Gesamtbild schnitten die Zufalls-Zwillinge im Mittel zu 49 % besser ab als die Kandidaten (bei einem Signal ohne jede Information wären es 50 %), und die Summe der Netto-Ergebnisse
aller aussagefähigen Kandidaten (-25.774 €) lag fast genau auf der Summe der Zufalls-Mediane (-25.933 €). Mit anderen Worten: **Ob man nach diesen Regeln oder zu zufälligen Zeiten einsteigt, ändert das Ergebnis kaum – beides verliert die Kosten.**
Die Kontrolle an künstlichen Zufallsreihen (Anhang) zeigt außerdem, dass einzelne Werte unter 5 % ohne jeden echten Vorteil vorkommen.

**Haben die Signale ohne Kosten überhaupt etwas gewusst?** Im kostenfreien Lauf C waren 14 von 24 Kombinationen mit Aussage im Plus, zusammen 120 € auf 3.726 Trades (0,003 % je Trade bei 1.000 € Einsatz).
Doch keine einzige war besser als 95 % der Zufallsläufe (allein durch Zufall zu erwarten: etwa 1,2), im Mittel schnitten 48 % der Zufallsläufe besser ab, und die Summe der Zufalls-Mediane (143 €) liegt bei der Summe der Kandidaten (120 €).
Das kleine Plus ohne Kosten ist also von zufälligen Einstiegen im selben Markt nicht zu unterscheiden (in einem Zeitraum mit steigenden Kursen verdient jede Long-Position ein wenig).
Bei den Stufe-6-Gebühren (Lauf B, 0,25 % Taker / 0,12 % Maker) waren 0 von 19 Kombinationen mit Aussage nach Kosten im Plus (Kosten 11.816 € gegenüber 519 € Ergebnis vor Kosten).

**Kaufen und Halten.** Im Testzeitraum veränderte sich BTC/EUR um 6,8 % und ETH/EUR um 0,3 % (nach Kosten 5,0 % bzw. -1,4 %),
mit größten Rückgängen unterwegs von 8,7 % bzw. 17,1 %. Besser als Halten (Netto-Euro) war 0 von 19 Kombinationen mit Aussage.
Wichtig: In einer anderen Marktphase (starker Anstieg oder Absturz) sähe der Halten-Vergleich völlig anders aus. Im ganzen 90-Tage-Zeitraum stiegen BTC/EUR um 28 % und ETH/EUR um 25 %;
wäre der Testabschnitt in diese Aufwärtsphase gefallen, hätte Halten deutlich vorn gelegen (die Kandidaten sind nur zeitweise investiert und stellen jeden Abend glatt).

**Unterschiede nach Takt.** Je kürzer der Takt, desto mehr Trades und desto mehr Kosten: 5 Minuten 1.336 Trades und 20.767 € Kosten, 15 Minuten 489 und 7.687 €, 1 Stunde 187 und 3.006 €.
Auf 1 Stunde hat in 90 Tagen kein Kandidat 30 Test-Trades erreicht: Dort gibt es keine Aussage (die Zahlen sind ebenfalls alle negativ, aber nicht belastbar).


## 8. Warnungen


1. **Kurzer Zeitraum.** 90 Tage, davon rund 53 Testtage, sind eine einzige Marktphase. Kurse stiegen im ganzen Zeitraum bei BTC/EUR um 28 %, bei ETH/EUR um 25 %;
   im Testabschnitt selbst 6,8 % bzw. 0,3 %. In einem Bärenmarkt, einem Crash oder einer ruhigen Seitwärtsphase können Ergebnisse anders aussehen.
2. **Überanpassung und Mehrfachtests.** Die Parameter wurden auf nur rund 36 bis 76 Tagen ausgesucht (je Faltung mehr). 42 Kombinationen wurden gerechnet; wer so oft testet, findet durch Zufall
   ein paar "auffällige" (an künstlichen Zufallsreihen gezeigt, Anhang). Deshalb zählt nur, was in mehreren Paaren und Takten wiederkehrt, und selbst das nur als Hinweis.
3. **Realitätslücke.** Der Test nimmt an, dass Marktorders sofort zum Kurs plus Schlupf laufen, dass Stops wie angenommen ausgeführt werden und dass ein Gewinnziel füllt, sobald der Kurs es durchläuft. In der Wirklichkeit füllen Limit-Orders oft nicht,
   Stops rutschen bei schnellen Märkten stärker, und Ausfälle (Verbindung, Wartung der Börse, rund 19 Lücken in den 5-Minuten-Daten) kommen dazu. Echte Ergebnisse fallen eher schlechter aus.
4. **Nur ein Ausschnitt der Möglichkeiten.** Nur *Long*, nur Daytrading (am UTC-Tagesende wird glattgestellt, also auch mitten in einer Bewegung), nur zwei Paare, nur Kraken, nur einfache Regeln mit kleinem Parametergitter, nur Kerzen als Eingabe (keine Nachrichten, kein Orderbuch).
   Dass hier nichts gefunden wurde, heißt nicht, dass es *nirgends* etwas zu finden gibt, sondern dass diese einfachen Regeln diese Kosten nicht tragen.
5. **Gebühren ändern sich.** Verwendet wurde die Seite vom 26.10.2026. Höhere Handelsvolumina senken die Gebühr (Lauf B), das ist aber nur für sehr aktive Händler relevant und ändert das Bild hier nicht grundlegend.
6. **Technik.** Der Code ist getestet (Tests laufen offline mit erfundenen Daten, die Kontrollen im Anhang bestätigen die Rechenwege), Fehler lassen sich aber nie ausschließen. Die Kerzen sind aus den Abschlüssen selbst verdichtet (41 von 42 Kerzen stimmen mit Krakens eigenen überein).
7. **Keine Anlageberatung.** Dies ist eine technische Prüfung eines privaten Werkzeugs, keine Empfehlung zu handeln.


## 9. Empfehlung


**Keiner der sieben Ansätze (die fünf Kandidaten und die zwei Beispiele) kommt als Gewinnstrategie in den Papierhandel.** Begründung: nach Kosten in jeder aussagefähigen Kombination im Minus,
ohne erkennbaren Vorteil gegenüber zufälligen Einstiegen, und weit entfernt von den Schwellen, die in `UEBERGABE.md` für Echtgeld vorgeschlagen sind (mindestens 100 Trades, nach Kosten im Plus, begrenzter Rückgang).
Papierhandel kostet nichts, aber er würde hier nur bestätigen, was die Gebührenrechnung schon zeigt; vier bis acht Wochen Papierhandel eines Verlierers geben keine neue Erkenntnis.

| Ansatz | Papierhandel? | Warum |
|---|---|---|
| `donchian`, `bollinger_rsi`, `ma_trend`, `momentum`, `volumen` | **nein** | nach Kosten im Minus, kein Vorteil gegenüber Zufall erkennbar |
| `ausbruch`, `vwap` (die Beispiele) | **nein** | dasselbe; `vwap` handelt am häufigsten (506 Trades in allen 6 Kombinationen) und verliert dadurch am meisten an Kosten (-7.812 € netto) |

**Was man stattdessen tun kann:**

1. *Papierhandel nur als Technik-Test* (Orders, Stop-Loss, Journal, Not-Aus und Telegram im Zusammenspiel prüfen) darf mit einem beliebigen Ansatz laufen, am besten mit einem, der selten handelt (zum Beispiel Donchian auf 1 Stunde): **ohne jede Gewinnerwartung**. Das Ergebnis dieser Simulation sagt über die Strategie nichts Neues.
2. *Seltener handeln.* Die Kosten sind je Trade fix; weniger Trades mit größeren Bewegungen (Tageskerzen, Haltedauer von Tagen bis Wochen) sind die naheliegende nächste Frage. Das braucht längere Kursreihen (kitrader hat Bitcoin täglich seit 2010 in der Historie) und ein Labor, das Haltedauern über Nacht rechnet; dieser Bericht prüft es nicht.
3. *Mehr Daten, gleiche Kandidaten.* Mit 180 bis 365 Tagen (Befehle in Abschnitt 10) würden mehr Marktphasen und mehr Trades einfließen. Erwartung angesichts der Kostenrelation: das Bild ändert sich nicht, aber es wäre belastbarer.
4. *Eine Strategie erst dann in den Papierhandel, wenn sie hier* auf mindestens zwei Paaren und mehreren Faltungen nach Kosten im Plus ist, bei mindestens 30 Test-Trades **und** besser als 95 % der Zufallsläufe. Das hat bisher keine Kombination geschafft.
5. *Echtgeld:* nicht auf Grundlage dieser Ergebnisse.


## 10. Den Test später mit mehr Daten wiederholen

Alles läuft mit der Python-Standardbibliothek, im Projektordner (`PYTHONPATH=src` entfällt nach `pip install -e .`). Die Kerzen liegen im Datenordner (`KITRADER_DATEN`, Standard `~/.local/share/kitrader`), **nie im Repo**.

```bash
# 1. Kerzen holen (für 90 Tage brauchte BTC/EUR 1.621 Abrufe, gut eine halbe Stunde; ETH/EUR 900; 180 Tage etwa das Doppelte). Bricht der Lauf ab, setzt derselbe Befehl fort; --neu beginnt von vorn.
PYTHONPATH=src python3 -m kitrader daten kraken BTC/EUR --takt 5m 15m 1h --tage 180
PYTHONPATH=src python3 -m kitrader daten kraken ETH/EUR --takt 5m 15m 1h --tage 180

# 2. Labor mit den Kraken-Standardgebühren (Stufe 1); schreibt JSON und die Tabellen für den Bericht
PYTHONPATH=src python3 -m kitrader labor --ausgabe labor_A.json --markdown labor_A.md

# 3. Gegenproben: höhere Gebührenstufe (Stufe 6) und ganz ohne Kosten
PYTHONPATH=src python3 -m kitrader labor --taker 0.25 --maker 0.12 --ausgabe labor_B.json
PYTHONPATH=src python3 -m kitrader labor --taker 0 --maker 0 --spread 0 --schlupf 0 --ausgabe labor_C.json

# 4. Gespeicherte Ergebnisse nur anzeigen (rechnet nichts)
PYTHONPATH=src python3 -m kitrader labor --aus-json labor_A.json
```

Wichtige Schalter: `--paare`, `--takte`, `--strategien NAME …`, `--faltungen`, `--train-anteil`, `--zufall` (Zahl der Zufallsläufe), `--seed` (gleiche Daten und gleicher Startwert ergeben dasselbe Ergebnis), `--prozesse`.
Prüft man die **aktuellen** Gebühren, vorher auf kraken.com/features/fee-schedule nachsehen und `--taker`/`--maker` anpassen. Je länger der Zeitraum, desto aussagekräftiger (180 Tage dauern beim Holen etwa doppelt so lange wie 90). Das Labor rechnet immer als Daytrading (am UTC-Tagesende glatt); für Haltedauern über Nacht müsste es erweitert werden
(nicht Teil dieses Berichts; einzelne Ansätze lassen sich mit `python3 -m kitrader backtest NAME DATEI.csv` und `backtest.tagesende_flat = false` in den Einstellungen schon jetzt so ausprobieren).

## Anhang

**Kontrollen des Labors.** Damit man dem Verfahren selbst trauen kann, wurde es an künstlichen Kursen geprüft (Test `KontrolleTest` in `tests/kitrader/test_labor.py` und einmalige Läufe, jeweils ohne Kosten):
(1) Mit eingebauter Trendfortsetzung (jede Kursbewegung setzt sich teilweise fort) erkennt das Labor den Vorteil: Donchian +436 € und Momentum +357 € wobei in beiden Fällen kein einziger Zufallslauf besser war (54 Testtage, 5-Minuten-Takt);
der gegenläufige Ansatz (Bollinger/RSI) verliert dort, wie es sein muss. (2) Bei reinem Zufall (8 künstliche Zufallsreihen, ebenfalls ohne Kosten) lag der Anteil "Zufall besser" im Mittel bei 41 % (Donchian), 58 % (Momentum) und 59 % (Bollinger/RSI),
einzelne Werte reichten von 4 % bis 96 %: ein Wert unter 5 % tritt also auch ohne echten Vorteil gelegentlich auf. (3) Die schnelle Rechnung für die Zufalls-Zwillinge stimmt Trade für Trade mit dem Backtest überein (Test `ZufallsweltTest`, alle Ausstiegsarten).
(4) Die Kerzen aus Abschlüssen stimmen mit Krakens eigenen Kerzen überein (41 von 42).

**Quellen.** Gebühren: kraken.com/features/fee-schedule (Seite "Fee Structures", Tabelle "Spot Crypto", Tier 1: Maker 0,40 %, Taker 0,80 %; Tier 6: 0,12 % / 0,25 %), abgerufen am 26.10.2026.
Kurse: `https://api.kraken.com/0/public/Trades` und `/0/public/OHLC` (öffentlich, ohne Schlüssel). Die Daten sind nur für den privaten Gebrauch (Nutzungsbedingungen von Kraken beachten) und dürfen nicht veröffentlicht werden.
