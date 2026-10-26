# LANGFRIST-BERICHT – schlagen einfache Trend- und Bewertungsregeln über Jahrzehnte das Kaufen und Halten?

**Ergebnis in einem Satz:** Auf Reihen von bis zu 155 Jahren (S&P), 141 Jahren (Dow), 66 Jahren (Gold) und knapp 11 Jahren Testzeit (Bitcoin) schlug nach Kosten keine einfache Regel (Trend, Momentum, CAPE-Bewertung, Trend plus Bewertung) das Kaufen und Halten verlässlich bei der Rendite (mit Bargeld ohne Zins nur 5 von 27 Läufen, vier davon bei Gold, einer bei Bitcoin), aber Trend und Momentum senkten den größten Rückgang meist um ein Drittel bis die Hälfte und die Schwankung um ein Fünftel bis ein Drittel, was je nach Annahme für das Bargeld etwa 0 bis 3 Prozentpunkte Rendite im Jahr kostete – und fast alles davon hängt an wenigen Krisenphasen (Dow bis 1949, Gold 1980 bis 1999, Bitcoin bis 2022).

*Stand: 26.10.2026. Geschrieben für Leser ohne Programmierkenntnisse. Keine Anlageberatung und kein Erfolgsversprechen: Eine Rechnung über die Vergangenheit sagt nichts sicher über die Zukunft. Alle Zahlen stammen aus dem Befehl `python3 -m kitrader labor lang` (Startwert 1, 500 Zufallsläufe); die Rohdaten liegen nicht im Repo.*

## 1. Worum es ging

Der erste Bericht (`STRATEGIE-BERICHT.md`) hat gezeigt: Sieben einfache Daytrading-Ansätze verlieren nach Kosten alle und sind nicht besser als Zufall. Hier geht es um die nächste Frage: Gibt es auf **Tages-, Monats- und Jahrzehnte-Basis** eine einfache, bekannte Regel, die **nach Kosten Kaufen und Halten schlägt** oder das **Risiko (den größten Rückgang)** deutlich senkt?

Kleines Wörterbuch:

- **Kaufen und Halten** – einmal kaufen und liegen lassen (keine Kosten angesetzt).
- **Bargeld** – Zeit außerhalb des Marktes. Es wächst entweder mit 0 % oder mit dem 10-Jahres-Zins der USA (siehe Abschnitt 3).
- **Größter Rückgang** – der tiefste Fall vom bisherigen Höchststand des Vermögens bis zum folgenden Tiefpunkt.
- **Längste Zeit unter Wasser** – wie viele Jahre es dauerte, bis das Vermögen nach einem Höchststand wieder darüber lag.
- **Pp** – Prozentpunkte pro Jahr (10,1 % gegen 8,4 % sind 1,7 Pp).
- **Wechsel** – jeder Kauf und jeder Verkauf. Bei jedem Wechsel fallen Kosten an.

## 2. Datenbasis

Alle Reihen kommen aus dem Paket `kitrader.historie` (frei zugängliche Quellen, nur privater Gebrauch; Bedingungen in `src/kitrader/historie/HISTORIE.md`). Geladen am 26.10.2026 mit `python3 -m kitrader.historie laden dow shiller_sp shiller_real_tr shiller_cape shiller_zins10 btc gold_monat uk_aktien` und zusätzlich `shiller_cpi shiller_dividende` (nötig für nominale Werte und die Dividenden-Näherung).

| Reihe | Quelle | Zeitraum und Anzahl | Was man wissen muss |
|---|---|---|---|
| **S&P 500** (Kürzel `sp`) | Robert Shiller: „Real Total Return Price“ mal US-Verbraucherpreise = nominaler **Gesamtertragsindex** (mit wiederangelegten Dividenden); Signale auf dem Kursindex `shiller_sp`; CAPE `shiller_cape` | 1871-01 bis 2026-09, 1.869 Monate (der vorläufige Oktober 2026 ist weggelassen); CAPE erst ab 1881 | **Monatsmittel** der Tageskurse, keine Schlusskurse: Die Werte sind geglättet und nicht genau so handelbar. Gewinne (für das CAPE) sind rückwirkend geschätzt. |
| **Dow Jones** (`dow`) | Samuel Williamson, MeasuringWorth | 1885-02-16 bis 2026-10-23; 38.655 Werte, davon 3.172 Samstage (Börse bis 1952 auch samstags) entfernt, bleiben 35.483 Handelstage | **Nur Kurs, ohne Dividenden.** Börse 31.07. bis 11.12.1914 geschlossen (echte Lücke). Zusammensetzung des Dow änderte sich laufend. |
| **Dow + Dividenden-Näherung** (`dow_div`) | wie oben, dazu täglich die Dividendenrendite des **S&P** vom Vormonat (`shiller_dividende` durch `shiller_sp`) | wie oben | Das ist eine **Annahme**, keine Messung: Die echte Dow-Dividendenrendite war anders. Sie zeigt aber, wie stark das Fehlen der Dividenden das Bild verschiebt (Halten: 6,0 % gegen 10,0 % im Jahr). |
| **Bitcoin** (`btc`) | Coin Metrics (`PriceUSD`, CC BY-NC 4.0, Namensnennung „Coin Metrics“), US-Dollar | 2010-07-18 bis 2026-10-25, 5.944 Tage; getestet ab 2016 | Anfangs ein winziger Markt (Preis 2010: 0,09 Dollar). Nur wenige große Zyklen. |
| **Gold** (`gold`) | Weltbank, Pink Sheet, Monatsmittel in US-Dollar | 1960-01 bis 2026-09, 801 Monate; getestet ab 1980 | **Fester Preis bis 1971** (35 Dollar), danach frei. Monatsmittel. |
| **UK-Aktien** (`uk`) | Bank of England, „A millennium of macroeconomic data“, verkettet | 1709-04 bis 2017-04, 3.697 Monate; getestet ab 1750 | **Ohne Dividenden**, aus vielen Quellen zusammengesetzt. Die Monatsschwankung liegt 1750 bis 1950 bei etwa 2,0 bis 2,6 %, nach 1950 bei 4,4 %: ältere Werte sind sehr wahrscheinlich geglättet. Endet 2017. |

Weitere Eigenheiten der Daten: Der amerikanische und der britische Markt gehören zu den erfolgreichen der Geschichte (man testet hier das, was überlebt hat). Steuern sind nicht gerechnet; in Deutschland löst jeder Verkauf Steuer auf den Gewinn aus, was häufiges Wechseln zusätzlich verteuert.

## 3. Wie getestet wurde

**Die Regeln** (übliche, bekannte Bausteine, nichts Neues erfunden). Alle sind „im Markt oder in Bargeld“, nie gehebelt, nie short:

| Regel | Bedeutung | Fester Lehrbuchwert | Gitter für die Einstellung |
|---|---|---|---|
| **Trend** | investiert, wenn der Kurs über dem Durchschnitt der letzten n Werte liegt, sonst Bargeld | 200 Tage (Dow, Bitcoin); 10 Monate (S&P, Gold, UK) | Tage 50/100/150/200/250; Monate 5/6/8/10/12 |
| **Momentum** (Zeitreihen-Momentum) | investiert, wenn der Kurs höher ist als vor n Werten | 12 Monate (252 Handelstage beim Dow, 365 Tage bei Bitcoin) | 3/6/9/12 Monate (63/126/189/252 Tage; Bitcoin 90/180/270/365) |
| **CAPE fest** (nur S&P) | investiert, solange das CAPE (Kurs durch zehnjährigen Gewinndurchschnitt) unter einer festen Schwelle liegt | – | Schwelle 15/20/25/30/35 |
| **CAPE relativ** (nur S&P) | investiert, solange das heutige CAPE nicht zu den teuersten (1 minus q) aller bis dahin bekannten Werte gehört (erst nach 20 Jahren Vorgeschichte) | – | q = 0,6/0,7/0,8/0,9 |
| **Trend + CAPE** (nur S&P) | beides muss stimmen: Trend (10 Monate) **und** CAPE relativ | Trend 10 Monate | q wie oben |

**Keine Zukunftsdaten.** Ein Signal darf nur Kurse bis zum Vortag bzw. Vormonat benutzen; **ausgeführt wird zum nächsten Kurs**, und erst danach zählt die Rendite. Beispiel Monatsregel: Entscheidung aus den Werten bis März, Kauf oder Verkauf zum April-Kurs, es zählt die Veränderung von April auf Mai. Die Tests im Repo prüfen das: Schneidet man alle späteren Daten ab, ändert sich kein Signal; verändert man die Zukunft, bleibt die Parameterwahl früherer Blöcke gleich. Als Gegenprobe wurde auch gerechnet, was passiert, wenn man am selben Kurs handelt, der das Signal gab („ohne Verzögerung“, Abschnitt 4.5).

**Kosten.** Je Wechsel (Kauf oder Verkauf) wird ein Anteil des **gesamten** Vermögens abgezogen: **0,1 %** für Aktien, Indexfonds und Gold (üblicher ETF-Handel inklusive Spread), **0,5 %** für Bitcoin (inklusive Spread). Das sind heutige Werte; früher (vor 1975, kein Indexfonds, feste Provisionen) war Handeln deutlich teurer, die Zahlen aus alten Jahrzehnten sind für Wechsel-Regeln also eher zu günstig gerechnet. Abschnitt 4.5 zeigt dieselben Regeln bei 0,05 % bis 2 %.

**Bargeld** wächst in zwei Varianten, beide werden immer gezeigt: **0 %** (vorsichtig für die Regeln) und **10-Jahres-Zins der USA** aus `shiller_zins10`, jeweils der Zins des Vormonats, nach Kalendertagen (großzügig: der echte Geldmarktzins lag meist darunter, in den frühen 1980ern aber darüber; die Wahrheit liegt zwischen den beiden Varianten). Einen Kurzfristzins (T-Bill) ab 1934 gibt es im Paket (`us_zins3m`), er wurde hier nicht benutzt. Für UK gibt es keinen Zins im Bestand, dort nur 0 %.

**Walk-forward über Jahrzehnte.** Die Parameter werden für jeden Testblock **nur aus den Daten davor** gewählt (wachsendes Trainingsfenster, Maßstab: höchstes Endvermögen nach Kosten). Die Testblöcke werden aneinandergesetzt und gemeinsam berichtet: S&P ab 1910 und Dow ab 1920 in 10-Jahres-Blöcken, Gold ab 1980 in 10-Jahres-Blöcken, Bitcoin ab 2016 in 2-Jahres-Blöcken, UK ab 1750 in 20-Jahres-Blöcken. Daneben stehen die **festen Lehrbuchregeln** ohne jede Einstellung, im selben Fenster und zusätzlich über die gesamte Reihe. Teilzeiträume: S&P und Dow bis 1949, 1950 bis 1999 und ab 2000; Gold 1980 bis 1999 und ab 2000; Bitcoin 2016 bis 2019, 2020 bis 2022 und ab 2023; UK bis 1799, 1800 bis 1949 und ab 1950.

**Zufallsvergleich.** Die Spannen „investiert“ und „Bargeld“ der echten Regel werden in zufälliger Reihenfolge neu angeordnet (500 Läufe). Anzahl der Wechsel und Anteil der Zeit im Markt bleiben **genau gleich**, nur der Zeitpunkt ist zufällig; dieselben Kosten. Die Spalte „Zufall besser“ nennt zwei Prozentzahlen: Anteil der Zufallsläufe mit mindestens so hoher **Rendite** / mit höchstens so großem **Rückgang**. Kleine Zahlen heißen: Das Timing der Regel war besser als blindes Umordnen. **Das ist etwas anderes als besser als Kaufen und Halten.** Gegenprobe des Aufbaus: Auf 60 künstlichen Zufallsreihen ohne jede Struktur (120 Jahre monatlich, Trend 10 Monate) schlug die Regel nur 3-mal (5 %) mehr als 95 % der Zufallsläufe, der mittlere Anteil lag bei 52 %, und die Regel lag im Schnitt 2,0 Pp unter Halten – der Aufbau erfindet also keinen Vorteil. (Ein kleiner Test im Repo prüft dasselbe mit 10 Reihen; das Skript für die 60 steht in Abschnitt 8.)

**Wie viele Tests?** Im Walk-forward-Fenster stehen 50 Zeilen (6 Reihen, je zwei Bargeld-Varianten außer UK, je Variante 4 Zeilen, beim S&P 7); sie sind nicht unabhängig (Dow und Dow mit Dividenden teilen die Kurse, die beiden Bargeld-Varianten dieselben Signale).

## 4. Ergebnisse

### 4.1 Übersicht

Spalten „gegen Halten“: Unterschied der Rendite pro Jahr nach Kosten zu Kaufen und Halten im selben Fenster, links Bargeld 0 %, rechts Bargeld mit 10-Jahres-Zins; negativ = schlechter als Halten. „Größter Rückgang“ der Regel steht bei Bargeld 0 % (mit Zins etwas niedriger).

| Reihe (Walk-forward-Fenster) | Halten: Rendite p. a. / größter Rückgang | Trend fest: gegen Halten (Bargeld 0 % / Zins) | Trend fest: größter Rückgang | Momentum fest: gegen Halten (Bargeld 0 % / Zins) | Momentum fest: größter Rückgang | Trend Walk-forward: gegen Halten (0 % / Zins) | Momentum Walk-forward: gegen Halten (0 % / Zins) |
|---|---:|---:|---:|---:|---:|---:|---:|
| S&P 500 (nominal, mit Dividenden) (1910 bis 2026) | 10,1 % / 82 % | -1,7 Pp / +0,0 Pp | 52 % | -2,6 Pp / -1,0 Pp | 44 % | -1,4 Pp / +0,3 Pp | -1,5 Pp / +0,2 Pp |
| Dow täglich (nur Kurs) (1920 bis 2026) | 6,0 % / 89 % | -0,4 Pp / +1,2 Pp | 57 % | -1,1 Pp / +0,5 Pp | 46 % | -1,1 Pp / +0,2 Pp | -1,0 Pp / +0,7 Pp |
| Dow täglich (Kurs + Dividenden-Näherung) (1920 bis 2026) | 10,0 % / 87 % | -2,0 Pp / -0,3 Pp | 56 % | -2,7 Pp / -1,1 Pp | 46 % | -2,9 Pp / -1,0 Pp | -2,6 Pp / -0,8 Pp |
| Bitcoin täglich (2016 bis 2026) | 62,1 % / 84 % | -3,0 Pp / -1,2 Pp | 65 % | -0,2 Pp / +1,2 Pp | 83 % | -4,7 Pp / -2,8 Pp | +6,2 Pp / +7,7 Pp |
| Gold (Monatsmittel) (1980 bis 2026) | 4,9 % / 62 % | +0,6 Pp / +3,6 Pp | 29 % | +0,0 Pp / +2,9 Pp | 44 % | +0,8 Pp / +3,7 Pp | +0,5 Pp / +3,3 Pp |
| UK-Aktien (ohne Dividenden) (1750 bis 2017) | 2,7 % / 71 % | -0,2 Pp / - | 44 % | -0,0 Pp / - | 31 % | -0,2 Pp / - | -0,2 Pp / - |

Kurzfazit des Befehls über alle 50 Zeilen:

- Rendite höher als Halten: **18 von 50** – bei Bargeld 0 % nur **5 von 27** (viermal Gold, einmal Bitcoin-Momentum mit Walk-forward), bei Bargeld mit Zins 13 von 23: beim S&P und Dow nur knapp (bis +1,2 Pp, beim Dow nur Kurs ein Artefakt, siehe 4.2), bei Gold und Bitcoin deutlicher.
- Größter Rückgang mindestens ein Drittel kleiner als bei Halten: **37 von 50**; Schwankung kleiner: **50 von 50**.
- Beides zugleich (mehr Rendite **und** ein Drittel weniger Rückgang): 14 von 50 – sieben bei Gold, vier beim Dow nur Kurs (Artefakt), drei beim S&P (nur mit Bargeld-Zins und höchstens +0,3 Pp).
- Zufallsvergleich: Rendite besser als 95 % der Zufallsläufe in 32 von 50 Zeilen, Rückgang in 30 von 50 (allein durch Zufall wären etwa 2,5 zu erwarten). Das Timing der Regeln ist also meist **besser als zufälliges Umordnen** – aber das genügt meist nicht, um die Rendite der Zeit außerhalb des Marktes auszugleichen.

### 4.2 Aktienindizes: S&P und Dow

S&P: Walk-forward-Fenster mit Bargeld 0 % und mit Bargeld-Zins, dann die Teilzeiträume. Dow: nur Kurs und mit Dividenden-Näherung (Bargeld 0 %).

**S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) - Bargeld 0 %**
*Walk-forward-Fenster: 1909-12-01 bis 2026-09-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | real p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 10,1 % | 6,8 % | - | 14,7 % | 81,8 % | 15,3 | 0 | 100 % | - |
| Trend (fest 10) | 8,4 % | 5,2 % | -1,7 Pp | 10,0 % | 51,5 % | 7,2 | 146 | 67 % | 1 % / 8 % |
| Momentum (fest 12) | 7,5 % | 4,3 % | -2,6 Pp | 10,4 % | 44,1 % | 8,3 | 96 | 68 % | 16 % / 4 % |
| Trend (Walk-forward) | 8,6 % | 5,4 % | -1,4 Pp | 9,9 % | 38,5 % | 7,9 | 122 | 67 % | 1 % / 0 % |
| Momentum (Walk-forward) | 8,6 % | 5,3 % | -1,5 Pp | 10,5 % | 40,3 % | 6,2 | 116 | 67 % | 1 % / 1 % |
| CAPE fest (Walk-forward) | 9,7 % | 6,5 % | -0,3 Pp | 13,8 % | 78,1 % | 6,8 | 23 | 90 % | 14 % / 31 % |
| CAPE relativ (Walk-forward) | 7,1 % | 3,9 % | -3,0 Pp | 12,7 % | 69,7 % | 8,0 | 35 | 66 % | 20 % / 33 % |
| Trend + CAPE (Walk-forward) | 5,6 % | 2,5 % | -4,4 Pp | 8,0 % | 26,5 % | 14,3 | 121 | 42 % | 2 % / 1 % |

**S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) - Bargeld = 10-Jahres-Zins (großzügig)**
*Walk-forward-Fenster: 1909-12-01 bis 2026-09-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | real p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 10,1 % | 6,8 % | - | 14,7 % | 81,8 % | 15,3 | 0 | 100 % | - |
| Trend (fest 10) | 10,1 % | 6,8 % | +0,0 Pp | 9,9 % | 47,0 % | 6,8 | 146 | 67 % | 1 % / 6 % |
| Momentum (fest 12) | 9,1 % | 5,9 % | -1,0 Pp | 10,3 % | 40,4 % | 7,9 | 96 | 68 % | 14 % / 1 % |
| Trend (Walk-forward) | 10,3 % | 7,0 % | +0,3 Pp | 9,8 % | 34,3 % | 7,8 | 122 | 67 % | 1 % / 0 % |
| Momentum (Walk-forward) | 10,2 % | 7,0 % | +0,2 Pp | 10,4 % | 38,8 % | 5,7 | 116 | 67 % | 0 % / 1 % |
| CAPE fest (Walk-forward) | 9,8 % | 6,6 % | -0,2 Pp | 13,6 % | 78,1 % | 6,8 | 27 | 87 % | 20 % / 25 % |
| CAPE relativ (Walk-forward) | 7,9 % | 4,7 % | -2,2 Pp | 11,9 % | 69,7 % | 5,7 | 21 | 55 % | 36 % / 52 % |
| Trend + CAPE (Walk-forward) | 8,3 % | 5,1 % | -1,8 Pp | 7,9 % | 25,0 % | 4,2 | 123 | 42 % | 3 % / 0 % |

**S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) - Bargeld 0 %**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 1910 bis 1949 | 1950 bis 1999 | ab 2000 |
|---|---:|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | 7,0 % / 82 % | 13,4 % / 39 % | 8,4 % / 49 % |
| Trend (fest 10) | -0,4 Pp, Rückgang 52 % (Halten 82 %) | -3,5 Pp, Rückgang 26 % (Halten 39 %) | -0,2 Pp, Rückgang 19 % (Halten 49 %) |
| Momentum (fest 12) | -2,2 Pp, Rückgang 44 % (Halten 82 %) | -4,1 Pp, Rückgang 26 % (Halten 39 %) | -0,3 Pp, Rückgang 19 % (Halten 49 %) |
| Trend (Walk-forward) | -0,4 Pp, Rückgang 38 % (Halten 82 %) | -2,9 Pp, Rückgang 27 % (Halten 39 %) | -0,3 Pp, Rückgang 19 % (Halten 49 %) |
| Momentum (Walk-forward) | +0,4 Pp, Rückgang 40 % (Halten 82 %) | -3,7 Pp, Rückgang 26 % (Halten 39 %) | -0,4 Pp, Rückgang 19 % (Halten 49 %) |
| CAPE fest (Walk-forward) | +1,1 Pp, Rückgang 78 % (Halten 82 %) | -1,1 Pp, Rückgang 39 % (Halten 39 %) | -1,1 Pp, Rückgang 49 % (Halten 49 %) |
| CAPE relativ (Walk-forward) | -0,1 Pp, Rückgang 70 % (Halten 82 %) | -3,9 Pp, Rückgang 39 % (Halten 39 %) | -5,7 Pp, Rückgang 45 % (Halten 49 %) |
| Trend + CAPE (Walk-forward) | -0,7 Pp, Rückgang 22 % (Halten 82 %) | -6,3 Pp, Rückgang 26 % (Halten 39 %) | -6,6 Pp, Rückgang 13 % (Halten 49 %) |

Was die Zahlen zeigen:

- **Trend 10 Monate, fest, Bargeld 0 %:** 8,4 % statt 10,1 % im Jahr (real 5,2 % statt 6,8 %), dafür größter Rückgang 51,5 % statt 81,8 %, Schwankung 10,0 % statt 14,7 %, längste Zeit unter Wasser 7,2 statt 15,3 Jahre; 146 Wechsel in 116,7 Jahren (etwa 1,25 pro Jahr), 67 % der Zeit investiert. Mit Bargeld-Zins verschwindet der Renditeabstand ganz (+0,0 Pp), und der Rückgang sinkt auf 47 %.
- **Momentum 12 Monate** liegt mit −2,6 Pp (Bargeld 0 %) bzw. −1,0 Pp (Zins) etwas hinter dem Trend; **Walk-forward** wählte beim Trend in **allen** zwölf Blöcken die längste Option (12 Monate, den Rand des Gitters), beim Momentum immer 9 Monate. Das Ergebnis liegt damit nahe an der festen Regel (Unterschied Walk-forward minus fest über alle 22 Vergleiche: Median +0,1 Pp, kleinster Wert −1,7 Pp, größter +6,5 Pp bei Bitcoin).
- **Teilzeiträume:** bis 1949 fast gleich viel Rendite (−0,4 Pp) bei deutlich kleinerem Rückgang (52 % statt 82 %); 1950 bis 1999, den Jahrzehnten mit 13,4 % Jahresrendite, kostete der Trend 3,5 Pp; ab 2000 −0,2 Pp bei 19 % statt 49 % Rückgang. Der Schutz entstand also in den großen Einbrüchen, bezahlt wurde in den langen Aufschwüngen.
- **CAPE fest** (Schwelle in jedem Block 30): −0,3 Pp, aber **kein Schutz** (Rückgang 78 %), nur 23 Wechsel, 90 % der Zeit investiert; **CAPE relativ**: −3,0 Pp bei ebenfalls hohem Rückgang (70 %). **Trend + CAPE** senkte den Rückgang auf 26,5 %, war aber nur 42 % der Zeit investiert und verlor 4,4 Pp im Jahr; ab 2000 −6,6 Pp. Die Bewertung allein hat die Einbrüche also nicht abgefangen, und in Kombination mit dem Trend kostet sie mehr Rendite, als sie Schutz bringt.

**Dow Jones täglich, nur Kurs - Bargeld 0 %**
*Walk-forward-Fenster: 1919-12-31 bis 2026-10-23 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 6,0 % | - | 17,9 % | 89,2 % | 25,2 | 0 | 100 % | - |
| Trend (fest 200) | 5,5 % | -0,4 Pp | 11,3 % | 57,5 % | 17,7 | 668 | 68 % | 1 % / 8 % |
| Momentum (fest 252) | 4,9 % | -1,1 Pp | 12,3 % | 46,2 % | 19,8 | 474 | 69 % | 9 % / 0 % |
| Trend (Walk-forward) | 4,9 % | -1,1 Pp | 11,6 % | 43,0 % | 17,6 | 939 | 67 % | 2 % / 0 % |
| Momentum (Walk-forward) | 5,0 % | -1,0 Pp | 11,6 % | 41,0 % | 13,7 | 791 | 66 % | 2 % / 0 % |

**Dow Jones täglich, Kurs plus Dividenden-Näherung - Bargeld 0 %**
*Walk-forward-Fenster: 1919-12-31 bis 2026-10-23 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 10,0 % | - | 17,9 % | 87,1 % | 15,5 | 0 | 100 % | - |
| Trend (fest 200) | 8,0 % | -2,0 Pp | 11,3 % | 56,4 % | 14,4 | 668 | 68 % | 1 % / 8 % |
| Momentum (fest 252) | 7,4 % | -2,7 Pp | 12,3 % | 45,5 % | 13,0 | 474 | 69 % | 14 % / 0 % |
| Trend (Walk-forward) | 7,2 % | -2,9 Pp | 11,7 % | 40,3 % | 13,9 | 887 | 67 % | 8 % / 0 % |
| Momentum (Walk-forward) | 7,5 % | -2,6 Pp | 11,6 % | 39,5 % | 10,4 | 791 | 66 % | 3 % / 0 % |

**Dow Jones täglich, Kurs plus Dividenden-Näherung - Bargeld 0 %**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 1920 bis 1949 | 1950 bis 1999 | ab 2000 |
|---|---:|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | 7,9 % / 87 % | 12,6 % / 41 % | 7,8 % / 52 % |
| Trend (fest 200) | +2,3 Pp, Rückgang 56 % (Halten 87 %) | -2,7 Pp, Rückgang 23 % (Halten 41 %) | -5,3 Pp, Rückgang 46 % (Halten 52 %) |
| Momentum (fest 252) | +0,9 Pp, Rückgang 43 % (Halten 87 %) | -4,1 Pp, Rückgang 46 % (Halten 41 %) | -4,0 Pp, Rückgang 43 % (Halten 52 %) |
| Trend (Walk-forward) | +1,4 Pp, Rückgang 31 % (Halten 87 %) | -4,1 Pp, Rückgang 38 % (Halten 41 %) | -5,3 Pp, Rückgang 40 % (Halten 52 %) |
| Momentum (Walk-forward) | +1,1 Pp, Rückgang 36 % (Halten 87 %) | -3,9 Pp, Rückgang 38 % (Halten 41 %) | -4,1 Pp, Rückgang 36 % (Halten 52 %) |

- Beim **Dow nur Kurs** wirkt der Trend (200 Tage) mit Bargeld-Zins sogar besser als Halten (+1,2 Pp). Das ist ein **Artefakt**: Halten bekommt dort keine Dividenden (6,0 % im Jahr), das Bargeld aber Zinsen. Mit der **Dividenden-Näherung** (Halten 10,0 %) liegt dieselbe Regel bei Bargeld 0 % −2,0 Pp zurück, bei Zins −0,3 Pp; Momentum −2,7 bzw. −1,1 Pp; keine der vier Dow-Regeln schlägt Halten.
- **Risiko:** 200-Tage-Trend senkt den größten Rückgang von 87 % auf 56 %, die Schwankung von 17,9 % auf 11,3 %; die Walk-forward-Regeln erreichen 40 bzw. 39,5 % Rückgang.
- **Teilzeiträume (Dow mit Dividenden, Bargeld 0 %):** bis 1949 +2,3 Pp (Trend fest) – der Zeitraum umfasst 1929 bis 1932; 1950 bis 1999 −2,7 Pp; ab 2000 −5,3 Pp bei 46 % statt 52 % Rückgang. Woran das liegt (z. B. an schnellen Erholungen wie 2009 und 2020), wurde nicht untersucht.
- Die Walk-forward-Auswahl landete beim Dow ab 1960 bei **250 Tagen** (längste Option) und beim Momentum bei 126 Tagen.

### 4.3 Bitcoin (täglich, getestet ab 2016, Kosten 0,5 % je Wechsel)

**Bitcoin täglich (US-Dollar) - Bargeld 0 %**
*Walk-forward-Fenster: 2015-12-31 bis 2026-10-25 (Kosten 0,50 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 62,1 % | - | 66,4 % | 83,8 % | 3,0 | 0 | 100 % | - |
| Trend (fest 200) | 59,1 % | -3,0 Pp | 52,3 % | 65,1 % | 2,9 | 66 | 62 % | 6 % / 7 % |
| Momentum (fest 365) | 61,9 % | -0,2 Pp | 59,2 % | 82,5 % | 3,1 | 23 | 73 % | 14 % / 45 % |
| Trend (Walk-forward) | 57,4 % | -4,7 Pp | 48,0 % | 69,3 % | 2,3 | 201 | 57 % | 1 % / 14 % |
| Momentum (Walk-forward) | 68,4 % | +6,2 Pp | 56,6 % | 70,2 % | 3,0 | 31 | 73 % | 5 % / 4 % |

**Bitcoin täglich (US-Dollar) - Bargeld 0 %**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 2016 bis 2019 | 2020 bis 2022 | ab 2023 |
|---|---:|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | 101,6 % / 84 % | 32,1 % / 77 % | 51,0 % / 53 % |
| Trend (fest 200) | +23,7 Pp, Rückgang 65 % (Halten 84 %) | +6,8 Pp, Rückgang 55 % (Halten 77 %) | -28,3 Pp, Rückgang 38 % (Halten 53 %) |
| Momentum (fest 365) | -17,7 Pp, Rückgang 77 % (Halten 84 %) | +36,0 Pp, Rückgang 53 % (Halten 77 %) | -13,8 Pp, Rückgang 33 % (Halten 53 %) |
| Trend (Walk-forward) | -18,8 Pp, Rückgang 69 % (Halten 84 %) | +23,2 Pp, Rückgang 62 % (Halten 77 %) | -15,3 Pp, Rückgang 36 % (Halten 53 %) |
| Momentum (Walk-forward) | +0,1 Pp, Rückgang 70 % (Halten 84 %) | +40,3 Pp, Rückgang 53 % (Halten 77 %) | -14,6 Pp, Rückgang 36 % (Halten 53 %) |

- **Trend 200 Tage (fest)** verliert 3,0 Pp (Bargeld 0 %) gegen Halten (59,1 % statt 62,1 %), senkt aber Schwankung (52,3 % statt 66,4 %) und Rückgang (65 % statt 84 %). In den Teilzeiträumen: 2016 bis 2019 +23,7 Pp, 2020 bis 2022 +6,8 Pp, **ab 2023 −28,3 Pp**.
- **Momentum fest (365 Tage)**: −0,2 Pp, kaum Schutz (Rückgang 83 %). **Momentum mit Walk-forward** (in allen sechs Blöcken 270 Tage gewählt) liegt **+6,2 Pp** vorn (68,4 %), mit Bargeld-Zins +7,7 Pp; im Zufallsvergleich 5 % / 4 %. Aber: Der Unterschied zwischen 270 und 365 Tagen sind rund 6 Pp – das Ergebnis hängt an einem Parameter, den die Trainingsdaten 2011 bis 2015 nahelegten, und ab 2023 lag auch diese Regel −14,6 Pp zurück. Das ist ein einzelner Treffer unter vielen Tests mit nur wenigen Marktzyklen, kein Beleg.
- Der Trend mit Walk-forward (immer 50 Tage) wechselte **201-mal** und verlor 4,7 Pp.
- Kosten sind bei Bitcoin der große Hebel: Trend 200 fest bei 1 % je Wechsel −11,3 Pp, bei 2 % −22,3 Pp (Abschnitt 4.5).

### 4.4 Gold (Monatsmittel, getestet ab 1980) und UK-Aktien (1750 bis 2017)

**Gold, Monatsmittel (US-Dollar) - Bargeld 0 %**
*Walk-forward-Fenster: 1979-12-01 bis 2026-09-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 4,9 % | - | 15,1 % | 62,1 % | 26,3 | 0 | 100 % | - |
| Trend (fest 10) | 5,6 % | +0,6 Pp | 12,3 % | 29,4 % | 15,2 | 69 | 57 % | 2 % / 0 % |
| Momentum (fest 12) | 4,9 % | +0,0 Pp | 12,5 % | 43,6 % | 25,9 | 66 | 58 % | 8 % / 24 % |
| Trend (Walk-forward) | 5,7 % | +0,8 Pp | 12,4 % | 32,6 % | 15,8 | 57 | 58 % | 2 % / 3 % |
| Momentum (Walk-forward) | 5,4 % | +0,5 Pp | 12,3 % | 35,9 % | 16,0 | 68 | 58 % | 3 % / 8 % |

**Gold, Monatsmittel (US-Dollar) - Bargeld 0 %**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 1980 bis 1999 | ab 2000 |
|---|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | -2,3 % / 62 % | 10,7 % / 39 % |
| Trend (fest 10) | +3,7 Pp, Rückgang 24 % (Halten 62 %) | -1,9 Pp, Rückgang 29 % (Halten 39 %) |
| Momentum (fest 12) | +2,0 Pp, Rückgang 37 % (Halten 62 %) | -1,6 Pp, Rückgang 22 % (Halten 39 %) |
| Trend (Walk-forward) | +3,8 Pp, Rückgang 24 % (Halten 62 %) | -1,7 Pp, Rückgang 33 % (Halten 39 %) |
| Momentum (Walk-forward) | +4,3 Pp, Rückgang 24 % (Halten 62 %) | -2,7 Pp, Rückgang 36 % (Halten 39 %) |

**UK-Aktien, Monatswerte 1709 bis 2017 (ohne Dividenden) - Bargeld 0 %**
*Walk-forward-Fenster: 1749-12-01 bis 2017-04-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 2,7 % | - | 10,2 % | 70,9 % | 28,2 | 0 | 100 % | - |
| Trend (fest 10) | 2,5 % | -0,2 Pp | 7,3 % | 43,7 % | 30,0 | 429 | 60 % | 0 % / 2 % |
| Momentum (fest 12) | 2,7 % | -0,0 Pp | 7,4 % | 31,0 % | 29,6 | 260 | 61 % | 0 % / 0 % |
| Trend (Walk-forward) | 2,5 % | -0,2 Pp | 7,3 % | 44,0 % | 30,8 | 405 | 59 % | 0 % / 3 % |
| Momentum (Walk-forward) | 2,5 % | -0,2 Pp | 7,3 % | 38,0 % | 38,9 | 389 | 60 % | 0 % / 0 % |

**UK-Aktien, Monatswerte 1709 bis 2017 (ohne Dividenden) - Bargeld 0 %**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 1750 bis 1799 | 1800 bis 1949 | ab 1950 |
|---|---:|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | 0,1 % / 43 % | 1,7 % / 52 % | 7,1 % / 71 % |
| Trend (fest 10) | +0,5 Pp, Rückgang 28 % (Halten 43 %) | +0,1 Pp, Rückgang 41 % (Halten 52 %) | -1,6 Pp, Rückgang 44 % (Halten 71 %) |
| Momentum (fest 12) | +0,9 Pp, Rückgang 23 % (Halten 43 %) | +0,6 Pp, Rückgang 29 % (Halten 52 %) | -2,1 Pp, Rückgang 31 % (Halten 71 %) |
| Trend (Walk-forward) | +0,7 Pp, Rückgang 31 % (Halten 43 %) | +0,1 Pp, Rückgang 43 % (Halten 52 %) | -1,4 Pp, Rückgang 44 % (Halten 71 %) |
| Momentum (Walk-forward) | +0,6 Pp, Rückgang 32 % (Halten 43 %) | +0,1 Pp, Rückgang 38 % (Halten 52 %) | -1,5 Pp, Rückgang 33 % (Halten 71 %) |

- **Gold:** Trend 10 Monate (fest) +0,6 Pp bei Bargeld 0 % (5,6 % statt 4,9 %), größter Rückgang **29 % statt 62 %**, längste Zeit unter Wasser 15,2 statt 26,3 Jahre; mit Bargeld-Zins +3,6 Pp. Der ganze Vorsprung kommt aus **einer** Phase: 1980 bis 1999 (Halten: −2,3 % im Jahr; der Trend +3,7 Pp bei Bargeld 0 % und **+9,1 Pp** bei Bargeld-Zins, als der 10-Jahres-Zins im Schnitt bei 8,6 % lag). Ab 2000 verliert der Trend gegen Halten (−1,9 Pp bzw. −0,9 Pp). Das ist eine einzige große Baisse in einer Hochzinszeit, die sich nicht wiederholen muss.
- **UK-Aktien (ohne Dividenden, Bargeld 0 %):** Trend −0,2 Pp, Momentum −0,0 Pp, Rückgang 44 % bzw. 31 % statt 71 %, Schwankung 7,3 % statt 10,2 %. Die Regeln helfen in den älteren Jahrhunderten etwas (bis 1799 +0,5 bis +0,9 Pp), ab 1950 kosten sie 1,4 bis 2,1 Pp. Die **längste Zeit unter Wasser blieb bei 30 bis 39 Jahren** (Halten 28 Jahre): Vor langen Flauten schützt der Trend nicht. Wegen der geglätteten Altdaten (Abschnitt 2) sind die frühen Zahlen mit Vorsicht zu lesen.

### 4.5 Gegenproben: Kosten und Ausführungszeitpunkt

Rendite pro Jahr der **festen** Regel minus Halten über die gesamte Reihe, bei verschiedenen Kosten je Wechsel; zuletzt die Lehrbuchannahme „ohne Verzögerung“ (Handel am selben Kurs, der das Signal gab, mit den Standardkosten).

S&P 500, nominal mit Dividenden (Shiller, Monatsmittel): Rendite p. a. der festen Regel minus Halten, bei verschiedenen Kosten je Wechsel
| Bargeld | Regel | 0,05 % | 0,10 % | 0,30 % | 0,50 % | 1,00 % | ohne Verzögerung (Kosten wie Standard) | Halten p. a. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Bargeld 0 % | Trend (fest 10) | -1,7 Pp | -1,8 Pp | -2,1 Pp | -2,4 Pp | -3,1 Pp | -0,0 Pp | 9,4 % |
| Bargeld 0 % | Momentum (fest 12) | -2,3 Pp | -2,3 Pp | -2,5 Pp | -2,6 Pp | -3,1 Pp | -0,8 Pp | 9,4 % |
| Bargeld = 10-Jahres-Zins | Trend (fest 10) | +0,0 Pp | -0,1 Pp | -0,4 Pp | -0,6 Pp | -1,4 Pp | +1,7 Pp | 9,4 % |
| Bargeld = 10-Jahres-Zins | Momentum (fest 12) | -0,6 Pp | -0,6 Pp | -0,8 Pp | -1,0 Pp | -1,4 Pp | +0,9 Pp | 9,4 % |

Dow Jones täglich, Kurs plus Dividenden-Näherung: Rendite p. a. der festen Regel minus Halten, bei verschiedenen Kosten je Wechsel
| Bargeld | Regel | 0,05 % | 0,10 % | 0,30 % | 0,50 % | 1,00 % | ohne Verzögerung (Kosten wie Standard) | Halten p. a. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Bargeld 0 % | Trend (fest 200) | -1,9 Pp | -2,2 Pp | -3,7 Pp | -5,1 Pp | -8,7 Pp | -1,7 Pp | 9,5 % |
| Bargeld 0 % | Momentum (fest 252) | -2,4 Pp | -2,6 Pp | -3,5 Pp | -4,5 Pp | -6,8 Pp | -2,6 Pp | 9,5 % |
| Bargeld = 10-Jahres-Zins | Trend (fest 200) | -0,1 Pp | -0,5 Pp | -2,0 Pp | -3,5 Pp | -7,1 Pp | +0,0 Pp | 9,5 % |
| Bargeld = 10-Jahres-Zins | Momentum (fest 252) | -0,7 Pp | -1,0 Pp | -1,9 Pp | -2,9 Pp | -5,2 Pp | -1,0 Pp | 9,5 % |

Bitcoin täglich (US-Dollar): Rendite p. a. der festen Regel minus Halten, bei verschiedenen Kosten je Wechsel
| Bargeld | Regel | 0,25 % | 0,50 % | 1,00 % | 2,00 % | ohne Verzögerung (Kosten wie Standard) | Halten p. a. |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bargeld 0 % | Trend (fest 200) | -2,6 Pp | -5,5 Pp | -11,3 Pp | -22,3 Pp | -5,6 Pp | 76,4 % |
| Bargeld 0 % | Momentum (fest 365) | -14,2 Pp | -15,0 Pp | -16,7 Pp | -19,9 Pp | -6,4 Pp | 76,4 % |
| Bargeld = 10-Jahres-Zins | Trend (fest 200) | -0,7 Pp | -3,7 Pp | -9,5 Pp | -20,7 Pp | -3,8 Pp | 76,4 % |
| Bargeld = 10-Jahres-Zins | Momentum (fest 365) | -12,9 Pp | -13,7 Pp | -15,4 Pp | -18,6 Pp | -5,0 Pp | 76,4 % |

- **Kosten:** Bei den Standardkosten sind die Abstände moderat, bei hohen Kosten werden sie groß. Beispiel S&P Trend, Bargeld 0 %: −1,8 Pp bei 0,1 %, −3,1 Pp bei 1 %; Dow mit Dividenden: bis −8,7 Pp bei 1 %. Weil früher Handeln teurer war, sind die alten Jahrzehnte für die Regeln eher zu günstig gerechnet.
- **Ausführungszeitpunkt:** Handelt man am selben Kurs, der das Signal gab, sieht der S&P-Trend aus wie Halten (−0,0 Pp bei Bargeld 0 %) oder sogar besser (+1,7 Pp mit Zins). Mit dem verlangten „nächsten Kurs“ sind es −1,8 bzw. −0,1 Pp. Bei Monatsmitteln (Shiller) ist die Lehrbuchannahme besonders trügerisch: Der Monatswert enthält auch Tage nach dem Zeitpunkt, an dem die Entscheidung fiel, und ist kein Preis, zu dem man handeln konnte. Bei den Monatsreihen kostet die Verzögerung 0,4 bis 1,8 Pp (S&P 1,5 bis 1,8, UK 0,4 bis 1,1, Gold 0,6 bis 0,7), beim Dow 0,0 bis 0,6 Pp, beim Bitcoin-Trend 0 Pp, beim Bitcoin-Momentum (365 Tage) aber **8,6 Pp** (−15,0 gegen −6,4 Pp über die ganze Reihe ab 2011). Schöne Werte aus der Literatur („10-Monats-Durchschnitt“) fallen im Alltag also eher schwächer aus, und manche Regeln hängen stark an diesem Detail.

## 5. Ereignisauswertung: Dow und S&P nach Kriegen, Bankenkrisen, Zinsschocks und Pandemien

Mit `kitrader.historie.studie_art` aus der Ereignisliste (323 Ereignisse). Zelle: **Median / Mittel / Anteil der Fälle mit Verlust**; „n“ = ausgewertete Ereignisse von allen der Art (Ereignisse mit nur Jahres- oder Quartalsgenauigkeit und solche vor Beginn der Reihe fehlen; bei n unter 5 gäbe es keine Kennzahlen). Die letzte Spalte ist der tiefste Stand innerhalb von 12 Monaten gegenüber dem Start. Die Zeilen „alle Monate“ sind der Vergleich: dieselbe Rechnung für den Ersten jedes Monats der ganzen Reihe.

Zelle: Median / Mittel / Anteil negativ. Reihe dow = Dow täglich (ab 1885), shiller_sp = S&P-Kurs, Monatsmittel (ab 1871).
| Reihe | Ereignisart | n | 1 Monat | 3 Monate | 6 Monate | 12 Monate | Tiefstand binnen 12 Monaten |
|---|---:|---:|---:|---:|---:|---:|---:|
| dow | krieg | 46 von 47 | 1,2 % / 0,6 % / 38 % | 3,2 % / 1,5 % / 34 % | 4,8 % / 4,1 % / 26 % | 9,5 % / 8,9 % / 35 % | -6,3 % / -9,8 % / 91 % |
| dow | bankenkrise | 17 von 19 | 1,7 % / -0,5 % / 47 % | 3,8 % / 3,1 % / 35 % | -0,3 % / 1,0 % / 53 % | 7,4 % / 2,1 % / 41 % | -12,3 % / -21,6 % / 88 % |
| dow | zinsschock | 7 von 9 | 1,1 % / 0,2 % / 44 % | -2,6 % / -0,8 % / 62 % | 0,4 % / -1,0 % / 43 % | 7,6 % / 3,9 % / 29 % | -10,3 % / -10,0 % / 100 % |
| dow | pandemie | 14 von 17 | 1,7 % / 0,4 % / 40 % | 5,9 % / 3,4 % / 27 % | 6,6 % / 8,7 % / 14 % | 14,1 % / 16,9 % / 0 % | -4,6 % / -9,3 % / 93 % |
| shiller_sp | krieg | 46 von 47 | 1,7 % / 0,8 % / 28 % | 1,9 % / 2,0 % / 36 % | 4,2 % / 3,6 % / 34 % | 8,7 % / 7,3 % / 33 % | -4,4 % / -7,8 % / 72 % |
| shiller_sp | bankenkrise | 17 von 19 | 1,3 % / -0,3 % / 47 % | 3,5 % / 3,7 % / 29 % | 6,2 % / 0,3 % / 47 % | 18,1 % / 2,6 % / 35 % | -5,9 % / -18,2 % / 59 % |
| shiller_sp | zinsschock | 7 von 9 | -0,8 % / -0,3 % / 56 % | 1,3 % / -0,8 % / 38 % | -1,4 % / -0,7 % / 57 % | 14,5 % / 7,2 % / 29 % | -4,7 % / -6,1 % / 100 % |
| shiller_sp | pandemie | 14 von 17 | 2,6 % / 2,8 % / 20 % | 4,7 % / 4,2 % / 20 % | 7,6 % / 10,0 % / 14 % | 16,7 % / 21,3 % / 0 % | -0,2 % / -3,2 % / 50 % |
| dow | alle Monate (Vergleich) | 1695 | 0,9 % / 0,6 % / 41 % | 2,0 % / 1,8 % / 40 % | 3,9 % / 3,5 % / 37 % | 7,0 % / 7,3 % / 34 % | -7,1 % / -10,8 % / 92 % |
| shiller_sp | alle Monate (Vergleich) | 1869 | 0,7 % / 0,5 % / 41 % | 1,9 % / 1,5 % / 39 % | 3,7 % / 3,1 % / 37 % | 7,3 % / 6,5 % / 35 % | -3,6 % / -7,3 % / 71 % |

Was man daraus ablesen kann – und was nicht:

- **Kriege** (n = 46): Dow-Median nach 12 Monaten +9,5 % (alle Monate +7,0 %), 35 % im Minus (alle Monate 34 %); tiefster Stand binnen 12 Monaten im Median −6,3 % (alle Monate −7,1 %). Beim S&P ähnlich (+8,7 % gegen +7,3 %). Nichts Auffälliges.
- **Bankenkrisen** (n = 17): Dow-Median nach 12 Monaten +7,4 %, aber das **Mittel nur +2,1 %** (alle Monate +7,3 %); tiefster Stand binnen 12 Monaten im Median **−12,3 %** (alle Monate −7,1 %). Bankenkrisen brachten also tiefere Einbrüche, aber keinen sicheren Kursverlust nach einem Jahr.
- **Zinsschocks** (n = 7): nach 3 Monaten Dow-Median −2,6 % (62 % im Minus), tiefster Stand −10,3 %, nach 12 Monaten wieder +7,6 %. Mit n = 7 ist das Anekdote, keine Statistik.
- **Pandemien** (n = 14): 12-Monats-Median +14,1 %, kein Fall im Minus. **Vorsicht:** Die Art enthält mehrere Einträge zu COVID-19 (Dezember 2019, Januar und März 2020, Mai 2023) sowie Meldungen wie „WHO beendet die Gesundheitsnotlage“; die 14 sind weit von 14 unabhängigen Pandemien entfernt.
- **Grundsätzlich:** Die Ereignisse überschneiden sich (Kriege und Krisen laufen gleichzeitig), ihre Renditen sind nicht unabhängig, die Beginn-Daten sind teils nur monatsgenau, und beim S&P sind es Monatsmittel. Die Unterschiede zu „alle Monate“ sind klein gegenüber der Streuung. Die Zahlen beschreiben die Vergangenheit; sie ergeben keine Handelsregel.

## 6. Einordnung – nur was die Zahlen zeigen

1. **Keine Regel schlägt Halten verlässlich bei der Rendite.** Bei den Aktienindizes (S&P, Dow mit Dividenden) liegen alle Regeln bei Bargeld 0 % zurück (−0,3 bis −4,4 Pp); mit der großzügigen Zins-Annahme liegen die besten bei +0,3 Pp (S&P, Walk-forward), also im Rauschen. Vorne lagen Gold (knapp bei Bargeld 0 %: +0,0 bis +0,8 Pp) und Bitcoin-Momentum mit Walk-forward. Beides sind einzelne Treffer, die an einer Phase hängen.
2. **Trend und Momentum senken das Risiko deutlich.** Größter Rückgang und Schwankung waren mit der festen Trendregel in allen 6 Reihen kleiner (Rückgang S&P 82 % auf 52 %, Dow 87 % auf 56 %, Gold 62 % auf 29 %, UK 71 % auf 44 %, Bitcoin 84 % auf 65 %). Das war besser als zufälliges Umordnen (Rückgang in 30 von 50 Zeilen besser als 95 % der Zufallsläufe). Bezahlt wird mit entgangener Rendite in den langen Aufschwüngen und mit vielen Wechseln. Der Schutz vor **langen** Flauten ist dagegen schwach: UK blieb 30 bis 39 Jahre unter Wasser, der Dow 10 bis 18 Jahre.
3. **Die Wirkung steckt in wenigen Krisen.** Dow bis 1949 (darin 1929 bis 1932), Gold 1980 bis 1999, Bitcoin bis 2022. In den Teilzeiträumen davor und danach kehrt sich der Vorsprung oft um. Wer die Regel nutzt, nimmt in Kauf, in „normalen“ Jahrzehnten (S&P 1950 bis 1999: −3,5 Pp) zurückzuliegen.
4. **Bewertung (CAPE) taugt hier nicht als Ausstiegssignal.** Allein hat sie die Einbrüche nicht gemildert, in Kombination mit dem Trend nahm sie mehr Rendite, als sie Schutz brachte.
5. **Annahmen entscheiden mehr als die Regel:** Bargeld mit Zins oder ohne (bis ca. 2 Pp), Ausführung am selben oder am nächsten Kurs (bei Monatsreihen bis 1,8 Pp, bei Bitcoin-Momentum 8,6 Pp), Dividenden ja oder nein (Dow: 4 Pp), Kosten (bei Bitcoin der größte Posten).
6. **Überanpassung:** 50 Zeilen sind getestet worden, dazu Gitter von 4 bis 5 Parametern je Regel; ein Treffer wie der Bitcoin-Wert entsteht dabei leicht. Die Walk-forward-Auswahl beim Trend landete immer am Rand des Gitters (S&P 12 Monate, Dow 250 Tage ab 1960, Bitcoin 50 Tage, Gold 12 Monate), beim Momentum in der Mitte (S&P 9 Monate, Bitcoin 270 Tage, Dow 126 Tage) und blieb von Block zu Block fast immer gleich. Der Rand heißt: Ein größeres Gitter hätte andere Werte liefern können. Die Gegenprobe mit künstlichen Zufallsreihen (Abschnitt 3) zeigt, dass der Aufbau selbst keinen Vorteil erfindet.
7. **Datenqualität:** Shiller-Reihen sind Monatsmittel und verschieben die Zeitpunkte (siehe 4.5); das CAPE benutzt rückwirkend geschätzte Gewinne; der Dow ist ohne Dividenden und die Näherung eine Annahme; die UK-Reihe ist verkettet, ohne Dividenden und vor 1950 sehr glatt; Gold bis 1971 hat einen festen Preis; Bitcoin hat nur wenige Zyklen. Fremdwährungseffekte, Steuern und eine Zeit ohne Indexfonds sind nicht abgebildet. Alles ist ein Rückblick auf die USA und Großbritannien, zwei sehr erfolgreiche Märkte.
8. **Aus dem Bericht folgt nicht**, dass Trendregeln „nichts bringen“ – sie senken Schwankung und Rückgang spürbar. Es folgt auch nicht, dass sie in der Zukunft dasselbe tun.

## 7. Empfehlung für den Papierhandel

- **Keine dieser Regeln als Gewinnstrategie einplanen.** Die Frage „Rendite über Halten“ ist nach Kosten mit Nein zu beantworten, und das Daytrading-Ergebnis aus `STRATEGIE-BERICHT.md` bleibt bestehen.
- **Einzig sinnvoller Kandidat – als Risikoschutz, nicht als Renditebringer:** die Trendregel „Kurs über dem 10-Monats-Durchschnitt, sonst Bargeld“ auf einen breiten Aktienindex, monatlich geprüft, mit Handel zum Kurs des Folgemonats. Erwartung laut Zahlen (S&P): etwa ein Drittel weniger größter Rückgang und ein Viertel bis ein Drittel weniger Schwankung, dafür 0 bis 3 Pp weniger Rendite im Jahr, etwa 1 Wechsel pro Jahr (146 in 117 Jahren). Voraussetzung: Man will das Risiko bewusst gegen Rendite tauschen und akzeptiert Jahrzehnte, in denen Halten besser war. Für Gold gilt dasselbe, für Bitcoin und für Bewertungsregeln nicht.
- **Praktisch:** Der Autopilot ist heute für Daytrading gebaut und stellt am Tagesende glatt. Eine Monatsregel bräuchte eine eigene Betriebsart mit Positionen über Wochen und Monate – das ist **nicht gebaut**. Außerdem liefert ein Papierhandel bei etwa einem Wechsel im Jahr in 4 bis 8 Wochen praktisch keine Beobachtung; er könnte nur den Ablauf (Bestellung, Kosten, Monatsroutine) prüfen, **nicht** den Nutzen. Der Nutzen steht nur im Rückblick, und der ist oben zu sehen.
- **Vor einem Test festlegen** (schriftlich): Soll das Ziel „Rückgang höchstens X %“ sein, was darf es an Rendite kosten (z. B. höchstens 2 Pp im Jahr), und ab wann bricht man ab.

## 8. Befehle zum Wiederholen

```bash
cd /home/user/finanz-portal && export PYTHONPATH=src
export KITRADER_DATEN=/pfad/zum/datenordner            # nie im Repo
python3 -m kitrader.historie laden dow shiller_sp shiller_real_tr shiller_cape shiller_zins10 shiller_cpi shiller_dividende btc gold_monat uk_aktien
python3 -m kitrader.historie pruefen
python3 -m kitrader labor lang --zufall 500 --seed 1 --markdown LANGFRIST-TABELLEN.md      # alle Reihen, ca. 35 Sekunden auf 4 Kernen
python3 -m kitrader labor lang --reihen sp gold --kosten-aktie 0.3                          # nur einige Reihen, andere Kosten
python3 -m kitrader labor lang --aus-json $KITRADER_DATEN/labor/lang.json                  # nichts rechnen, nur zeigen
python3 -m unittest discover -s tests/kitrader -t .                                         # Tests (641)
```

Gegenprobe mit künstlichen Zufallsreihen (60 Reihen, 120 Jahre monatlich, Trend 10 Monate; Ergebnis hier: 3 Treffer, Mittel 52 %, −2,0 Pp):

```python
import random, math
from kitrader.strategie import langfrist as lf
daten = [f"{1900 + i // 12}-{i % 12 + 1:02d}-01" for i in range(12 * 120)]
treffer, anteile, abstand = 0, [], []
for seed in range(60):
    rng = random.Random(1000 + seed); k = [100.0]
    for _ in range(len(daten) - 1): k.append(k[-1] * math.exp(rng.gauss(0.0065, 0.043)))
    r = lf.Reihe("zufall", daten, k, [0.0025] * len(daten), 0.001)
    pos = lf.positionen(lf.signal_gleitender_durchschnitt(k, 10), 1)
    a = r.index_ab("1930-01-01") + 1
    z = lf.zufallsvergleich(r, pos, a, len(r) - 1, laeufe=200, seed=seed)
    h, s = lf.kennzahlen_halten(r, a, len(r) - 1), lf.kennzahlen_fenster(r, pos, a, len(r) - 1)
    anteile.append(z["anteil_besser_rendite"]); abstand.append(s["rendite_pa"] - h["rendite_pa"]); treffer += z["anteil_besser_rendite"] < 5
print(treffer, sum(anteile) / 60, 100 * sum(abstand) / 60)
```

Code: `src/kitrader/strategie/langfrist.py` (Signale, Kosten, Kennzahlen, Walk-forward, Zufallsvergleich), `src/kitrader/labor_lang.py` (Reihen laden, Ereignisse, Ausgabe, Befehl `labor lang`), Tests `tests/kitrader/test_langfrist.py` (45 Tests; u. a. Signal von Hand, kein Blick in die Zukunft, Kosten an einem von Hand nachgerechneten Beispiel, Walk-forward nur aus Daten davor).

## Anhang: alle Tabellen (unverändert aus dem Befehl)

Für jede Reihe und jede Bargeld-Variante: Walk-forward-Fenster, gesamte Reihe (nur feste Regeln), Teilzeiträume; danach Kosten/Verzögerung, Ereignisse, gewählte Parameter, Kurzfazit.

### Übersicht

| Reihe (Walk-forward-Fenster) | Halten: Rendite p. a. / größter Rückgang | Trend fest: gegen Halten (Bargeld 0 % / Zins) | Trend fest: größter Rückgang | Momentum fest: gegen Halten (Bargeld 0 % / Zins) | Momentum fest: größter Rückgang | Trend Walk-forward: gegen Halten (0 % / Zins) | Momentum Walk-forward: gegen Halten (0 % / Zins) |
|---|---:|---:|---:|---:|---:|---:|---:|
| S&P 500 (nominal, mit Dividenden) (1910 bis 2026) | 10,1 % / 82 % | -1,7 Pp / +0,0 Pp | 52 % | -2,6 Pp / -1,0 Pp | 44 % | -1,4 Pp / +0,3 Pp | -1,5 Pp / +0,2 Pp |
| Dow täglich (nur Kurs) (1920 bis 2026) | 6,0 % / 89 % | -0,4 Pp / +1,2 Pp | 57 % | -1,1 Pp / +0,5 Pp | 46 % | -1,1 Pp / +0,2 Pp | -1,0 Pp / +0,7 Pp |
| Dow täglich (Kurs + Dividenden-Näherung) (1920 bis 2026) | 10,0 % / 87 % | -2,0 Pp / -0,3 Pp | 56 % | -2,7 Pp / -1,1 Pp | 46 % | -2,9 Pp / -1,0 Pp | -2,6 Pp / -0,8 Pp |
| Bitcoin täglich (2016 bis 2026) | 62,1 % / 84 % | -3,0 Pp / -1,2 Pp | 65 % | -0,2 Pp / +1,2 Pp | 83 % | -4,7 Pp / -2,8 Pp | +6,2 Pp / +7,7 Pp |
| Gold (Monatsmittel) (1980 bis 2026) | 4,9 % / 62 % | +0,6 Pp / +3,6 Pp | 29 % | +0,0 Pp / +2,9 Pp | 44 % | +0,8 Pp / +3,7 Pp | +0,5 Pp / +3,3 Pp |
| UK-Aktien (ohne Dividenden) (1750 bis 2017) | 2,7 % / 71 % | -0,2 Pp / - | 44 % | -0,0 Pp / - | 31 % | -0,2 Pp / - | -0,2 Pp / - |

**S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) - Bargeld 0 %**
*Walk-forward-Fenster: 1909-12-01 bis 2026-09-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | real p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 10,1 % | 6,8 % | - | 14,7 % | 81,8 % | 15,3 | 0 | 100 % | - |
| Trend (fest 10) | 8,4 % | 5,2 % | -1,7 Pp | 10,0 % | 51,5 % | 7,2 | 146 | 67 % | 1 % / 8 % |
| Momentum (fest 12) | 7,5 % | 4,3 % | -2,6 Pp | 10,4 % | 44,1 % | 8,3 | 96 | 68 % | 16 % / 4 % |
| Trend (Walk-forward) | 8,6 % | 5,4 % | -1,4 Pp | 9,9 % | 38,5 % | 7,9 | 122 | 67 % | 1 % / 0 % |
| Momentum (Walk-forward) | 8,6 % | 5,3 % | -1,5 Pp | 10,5 % | 40,3 % | 6,2 | 116 | 67 % | 1 % / 1 % |
| CAPE fest (Walk-forward) | 9,7 % | 6,5 % | -0,3 Pp | 13,8 % | 78,1 % | 6,8 | 23 | 90 % | 14 % / 31 % |
| CAPE relativ (Walk-forward) | 7,1 % | 3,9 % | -3,0 Pp | 12,7 % | 69,7 % | 8,0 | 35 | 66 % | 20 % / 33 % |
| Trend + CAPE (Walk-forward) | 5,6 % | 2,5 % | -4,4 Pp | 8,0 % | 26,5 % | 14,3 | 121 | 42 % | 2 % / 1 % |

**S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) - Bargeld 0 %**
*gesamte Reihe, nur feste Regeln: 1872-02-01 bis 2026-09-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | real p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 9,4 % | 7,1 % | - | 14,0 % | 81,8 % | 15,3 | 0 | 100 % | - |
| Trend (fest 10) | 7,6 % | 5,3 % | -1,8 Pp | 9,4 % | 51,5 % | 11,6 | 208 | 64 % | 0 % / 14 % |
| Momentum (fest 12) | 7,1 % | 4,8 % | -2,3 Pp | 9,7 % | 44,1 % | 8,3 | 126 | 64 % | 5 % / 5 % |

**S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) - Bargeld 0 %**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 1910 bis 1949 | 1950 bis 1999 | ab 2000 |
|---|---:|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | 7,0 % / 82 % | 13,4 % / 39 % | 8,4 % / 49 % |
| Trend (fest 10) | -0,4 Pp, Rückgang 52 % (Halten 82 %) | -3,5 Pp, Rückgang 26 % (Halten 39 %) | -0,2 Pp, Rückgang 19 % (Halten 49 %) |
| Momentum (fest 12) | -2,2 Pp, Rückgang 44 % (Halten 82 %) | -4,1 Pp, Rückgang 26 % (Halten 39 %) | -0,3 Pp, Rückgang 19 % (Halten 49 %) |
| Trend (Walk-forward) | -0,4 Pp, Rückgang 38 % (Halten 82 %) | -2,9 Pp, Rückgang 27 % (Halten 39 %) | -0,3 Pp, Rückgang 19 % (Halten 49 %) |
| Momentum (Walk-forward) | +0,4 Pp, Rückgang 40 % (Halten 82 %) | -3,7 Pp, Rückgang 26 % (Halten 39 %) | -0,4 Pp, Rückgang 19 % (Halten 49 %) |
| CAPE fest (Walk-forward) | +1,1 Pp, Rückgang 78 % (Halten 82 %) | -1,1 Pp, Rückgang 39 % (Halten 39 %) | -1,1 Pp, Rückgang 49 % (Halten 49 %) |
| CAPE relativ (Walk-forward) | -0,1 Pp, Rückgang 70 % (Halten 82 %) | -3,9 Pp, Rückgang 39 % (Halten 39 %) | -5,7 Pp, Rückgang 45 % (Halten 49 %) |
| Trend + CAPE (Walk-forward) | -0,7 Pp, Rückgang 22 % (Halten 82 %) | -6,3 Pp, Rückgang 26 % (Halten 39 %) | -6,6 Pp, Rückgang 13 % (Halten 49 %) |

**S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) - Bargeld = 10-Jahres-Zins (großzügig)**
*Walk-forward-Fenster: 1909-12-01 bis 2026-09-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | real p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 10,1 % | 6,8 % | - | 14,7 % | 81,8 % | 15,3 | 0 | 100 % | - |
| Trend (fest 10) | 10,1 % | 6,8 % | +0,0 Pp | 9,9 % | 47,0 % | 6,8 | 146 | 67 % | 1 % / 6 % |
| Momentum (fest 12) | 9,1 % | 5,9 % | -1,0 Pp | 10,3 % | 40,4 % | 7,9 | 96 | 68 % | 14 % / 1 % |
| Trend (Walk-forward) | 10,3 % | 7,0 % | +0,3 Pp | 9,8 % | 34,3 % | 7,8 | 122 | 67 % | 1 % / 0 % |
| Momentum (Walk-forward) | 10,2 % | 7,0 % | +0,2 Pp | 10,4 % | 38,8 % | 5,7 | 116 | 67 % | 0 % / 1 % |
| CAPE fest (Walk-forward) | 9,8 % | 6,6 % | -0,2 Pp | 13,6 % | 78,1 % | 6,8 | 27 | 87 % | 20 % / 25 % |
| CAPE relativ (Walk-forward) | 7,9 % | 4,7 % | -2,2 Pp | 11,9 % | 69,7 % | 5,7 | 21 | 55 % | 36 % / 52 % |
| Trend + CAPE (Walk-forward) | 8,3 % | 5,1 % | -1,8 Pp | 7,9 % | 25,0 % | 4,2 | 123 | 42 % | 3 % / 0 % |

**S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) - Bargeld = 10-Jahres-Zins (großzügig)**
*gesamte Reihe, nur feste Regeln: 1872-02-01 bis 2026-09-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | real p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 9,4 % | 7,1 % | - | 14,0 % | 81,8 % | 15,3 | 0 | 100 % | - |
| Trend (fest 10) | 9,3 % | 7,0 % | -0,1 Pp | 9,3 % | 47,0 % | 6,8 | 208 | 64 % | 0 % / 10 % |
| Momentum (fest 12) | 8,7 % | 6,4 % | -0,6 Pp | 9,7 % | 40,4 % | 7,9 | 126 | 64 % | 5 % / 3 % |

**S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) - Bargeld = 10-Jahres-Zins (großzügig)**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 1910 bis 1949 | 1950 bis 1999 | ab 2000 |
|---|---:|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | 7,0 % / 82 % | 13,4 % / 39 % | 8,4 % / 49 % |
| Trend (fest 10) | +1,2 Pp, Rückgang 47 % (Halten 82 %) | -1,4 Pp, Rückgang 25 % (Halten 39 %) | +0,8 Pp, Rückgang 19 % (Halten 49 %) |
| Momentum (fest 12) | -0,6 Pp, Rückgang 40 % (Halten 82 %) | -2,2 Pp, Rückgang 26 % (Halten 39 %) | +0,7 Pp, Rückgang 19 % (Halten 49 %) |
| Trend (Walk-forward) | +1,2 Pp, Rückgang 34 % (Halten 82 %) | -0,8 Pp, Rückgang 25 % (Halten 39 %) | +0,7 Pp, Rückgang 19 % (Halten 49 %) |
| Momentum (Walk-forward) | +2,1 Pp, Rückgang 39 % (Halten 82 %) | -1,7 Pp, Rückgang 26 % (Halten 39 %) | +0,6 Pp, Rückgang 19 % (Halten 49 %) |
| CAPE fest (Walk-forward) | +1,1 Pp, Rückgang 78 % (Halten 82 %) | -1,5 Pp, Rückgang 39 % (Halten 39 %) | +0,1 Pp, Rückgang 49 % (Halten 49 %) |
| CAPE relativ (Walk-forward) | +0,2 Pp, Rückgang 70 % (Halten 82 %) | -3,0 Pp, Rückgang 39 % (Halten 39 %) | -4,3 Pp, Rückgang 13 % (Halten 49 %) |
| Trend + CAPE (Walk-forward) | +1,1 Pp, Rückgang 22 % (Halten 82 %) | -3,1 Pp, Rückgang 25 % (Halten 39 %) | -3,7 Pp, Rückgang 12 % (Halten 49 %) |

**Dow Jones täglich, nur Kurs - Bargeld 0 %**
*Walk-forward-Fenster: 1919-12-31 bis 2026-10-23 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 6,0 % | - | 17,9 % | 89,2 % | 25,2 | 0 | 100 % | - |
| Trend (fest 200) | 5,5 % | -0,4 Pp | 11,3 % | 57,5 % | 17,7 | 668 | 68 % | 1 % / 8 % |
| Momentum (fest 252) | 4,9 % | -1,1 Pp | 12,3 % | 46,2 % | 19,8 | 474 | 69 % | 9 % / 0 % |
| Trend (Walk-forward) | 4,9 % | -1,1 Pp | 11,6 % | 43,0 % | 17,6 | 939 | 67 % | 2 % / 0 % |
| Momentum (Walk-forward) | 5,0 % | -1,0 Pp | 11,6 % | 41,0 % | 13,7 | 791 | 66 % | 2 % / 0 % |

**Dow Jones täglich, nur Kurs - Bargeld 0 %**
*gesamte Reihe, nur feste Regeln: 1886-02-12 bis 2026-10-23 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 5,2 % | - | 17,8 % | 89,2 % | 25,2 | 0 | 100 % | - |
| Trend (fest 200) | 4,8 % | -0,5 Pp | 11,4 % | 57,5 % | 17,7 | 962 | 65 % | 1 % / 4 % |
| Momentum (fest 252) | 4,4 % | -0,8 Pp | 12,3 % | 46,2 % | 19,8 | 624 | 66 % | 5 % / 0 % |

**Dow Jones täglich, nur Kurs - Bargeld 0 %**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 1920 bis 1949 | 1950 bis 1999 | ab 2000 |
|---|---:|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | 2,1 % / 89 % | 8,5 % / 45 % | 5,9 % / 54 % |
| Trend (fest 200) | +4,9 Pp, Rückgang 57 % (Halten 89 %) | -1,4 Pp, Rückgang 24 % (Halten 45 %) | -4,7 Pp, Rückgang 46 % (Halten 54 %) |
| Momentum (fest 252) | +3,6 Pp, Rückgang 43 % (Halten 89 %) | -2,7 Pp, Rückgang 46 % (Halten 45 %) | -3,4 Pp, Rückgang 44 % (Halten 54 %) |
| Trend (Walk-forward) | +4,5 Pp, Rückgang 34 % (Halten 89 %) | -2,6 Pp, Rückgang 39 % (Halten 45 %) | -4,7 Pp, Rückgang 43 % (Halten 54 %) |
| Momentum (Walk-forward) | +3,8 Pp, Rückgang 41 % (Halten 89 %) | -2,5 Pp, Rückgang 38 % (Halten 45 %) | -3,4 Pp, Rückgang 37 % (Halten 54 %) |

**Dow Jones täglich, nur Kurs - Bargeld = 10-Jahres-Zins (großzügig)**
*Walk-forward-Fenster: 1919-12-31 bis 2026-10-23 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 6,0 % | - | 17,9 % | 89,2 % | 25,2 | 0 | 100 % | - |
| Trend (fest 200) | 7,2 % | +1,2 Pp | 11,3 % | 54,6 % | 13,8 | 668 | 68 % | 1 % / 5 % |
| Momentum (fest 252) | 6,4 % | +0,5 Pp | 12,3 % | 43,6 % | 11,3 | 474 | 69 % | 6 % / 1 % |
| Trend (Walk-forward) | 6,2 % | +0,2 Pp | 11,6 % | 36,2 % | 11,3 | 1025 | 67 % | 3 % / 0 % |
| Momentum (Walk-forward) | 6,7 % | +0,7 Pp | 11,6 % | 38,0 % | 10,0 | 791 | 66 % | 1 % / 0 % |

**Dow Jones täglich, nur Kurs - Bargeld = 10-Jahres-Zins (großzügig)**
*gesamte Reihe, nur feste Regeln: 1886-02-12 bis 2026-10-23 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 5,2 % | - | 17,8 % | 89,2 % | 25,2 | 0 | 100 % | - |
| Trend (fest 200) | 6,4 % | +1,2 Pp | 11,4 % | 54,6 % | 13,8 | 962 | 65 % | 0 % / 5 % |
| Momentum (fest 252) | 6,0 % | +0,8 Pp | 12,3 % | 43,6 % | 11,3 | 624 | 66 % | 2 % / 0 % |

**Dow Jones täglich, nur Kurs - Bargeld = 10-Jahres-Zins (großzügig)**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 1920 bis 1949 | 1950 bis 1999 | ab 2000 |
|---|---:|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | 2,1 % / 89 % | 8,5 % / 45 % | 5,9 % / 54 % |
| Trend (fest 200) | +6,3 Pp, Rückgang 55 % (Halten 89 %) | +0,8 Pp, Rückgang 19 % (Halten 45 %) | -3,6 Pp, Rückgang 41 % (Halten 54 %) |
| Momentum (fest 252) | +5,0 Pp, Rückgang 43 % (Halten 89 %) | -0,7 Pp, Rückgang 44 % (Halten 45 %) | -2,5 Pp, Rückgang 40 % (Halten 54 %) |
| Trend (Walk-forward) | +6,0 Pp, Rückgang 32 % (Halten 89 %) | -1,1 Pp, Rückgang 36 % (Halten 45 %) | -3,7 Pp, Rückgang 35 % (Halten 54 %) |
| Momentum (Walk-forward) | +5,2 Pp, Rückgang 38 % (Halten 89 %) | -0,3 Pp, Rückgang 36 % (Halten 45 %) | -2,3 Pp, Rückgang 30 % (Halten 54 %) |

**Dow Jones täglich, Kurs plus Dividenden-Näherung - Bargeld 0 %**
*Walk-forward-Fenster: 1919-12-31 bis 2026-10-23 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 10,0 % | - | 17,9 % | 87,1 % | 15,5 | 0 | 100 % | - |
| Trend (fest 200) | 8,0 % | -2,0 Pp | 11,3 % | 56,4 % | 14,4 | 668 | 68 % | 1 % / 8 % |
| Momentum (fest 252) | 7,4 % | -2,7 Pp | 12,3 % | 45,5 % | 13,0 | 474 | 69 % | 14 % / 0 % |
| Trend (Walk-forward) | 7,2 % | -2,9 Pp | 11,7 % | 40,3 % | 13,9 | 887 | 67 % | 8 % / 0 % |
| Momentum (Walk-forward) | 7,5 % | -2,6 Pp | 11,6 % | 39,5 % | 10,4 | 791 | 66 % | 3 % / 0 % |

**Dow Jones täglich, Kurs plus Dividenden-Näherung - Bargeld 0 %**
*gesamte Reihe, nur feste Regeln: 1886-02-12 bis 2026-10-23 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 9,5 % | - | 17,8 % | 87,1 % | 15,5 | 0 | 100 % | - |
| Trend (fest 200) | 7,3 % | -2,2 Pp | 11,4 % | 56,4 % | 14,4 | 962 | 65 % | 1 % / 11 % |
| Momentum (fest 252) | 7,0 % | -2,6 Pp | 12,3 % | 45,5 % | 13,0 | 624 | 66 % | 8 % / 0 % |

**Dow Jones täglich, Kurs plus Dividenden-Näherung - Bargeld 0 %**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 1920 bis 1949 | 1950 bis 1999 | ab 2000 |
|---|---:|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | 7,9 % / 87 % | 12,6 % / 41 % | 7,8 % / 52 % |
| Trend (fest 200) | +2,3 Pp, Rückgang 56 % (Halten 87 %) | -2,7 Pp, Rückgang 23 % (Halten 41 %) | -5,3 Pp, Rückgang 46 % (Halten 52 %) |
| Momentum (fest 252) | +0,9 Pp, Rückgang 43 % (Halten 87 %) | -4,1 Pp, Rückgang 46 % (Halten 41 %) | -4,0 Pp, Rückgang 43 % (Halten 52 %) |
| Trend (Walk-forward) | +1,4 Pp, Rückgang 31 % (Halten 87 %) | -4,1 Pp, Rückgang 38 % (Halten 41 %) | -5,3 Pp, Rückgang 40 % (Halten 52 %) |
| Momentum (Walk-forward) | +1,1 Pp, Rückgang 36 % (Halten 87 %) | -3,9 Pp, Rückgang 38 % (Halten 41 %) | -4,1 Pp, Rückgang 36 % (Halten 52 %) |

**Dow Jones täglich, Kurs plus Dividenden-Näherung - Bargeld = 10-Jahres-Zins (großzügig)**
*Walk-forward-Fenster: 1919-12-31 bis 2026-10-23 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 10,0 % | - | 17,9 % | 87,1 % | 15,5 | 0 | 100 % | - |
| Trend (fest 200) | 9,7 % | -0,3 Pp | 11,3 % | 53,8 % | 10,2 | 668 | 68 % | 1 % / 9 % |
| Momentum (fest 252) | 9,0 % | -1,1 Pp | 12,3 % | 43,1 % | 7,4 | 474 | 69 % | 15 % / 1 % |
| Trend (Walk-forward) | 9,0 % | -1,0 Pp | 11,6 % | 35,9 % | 9,8 | 939 | 67 % | 3 % / 0 % |
| Momentum (Walk-forward) | 9,2 % | -0,8 Pp | 11,6 % | 35,9 % | 7,7 | 791 | 66 % | 3 % / 0 % |

**Dow Jones täglich, Kurs plus Dividenden-Näherung - Bargeld = 10-Jahres-Zins (großzügig)**
*gesamte Reihe, nur feste Regeln: 1886-02-12 bis 2026-10-23 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 9,5 % | - | 17,8 % | 87,1 % | 15,5 | 0 | 100 % | - |
| Trend (fest 200) | 9,0 % | -0,5 Pp | 11,4 % | 53,8 % | 10,2 | 962 | 65 % | 0 % / 11 % |
| Momentum (fest 252) | 8,6 % | -1,0 Pp | 12,3 % | 43,1 % | 7,4 | 624 | 66 % | 6 % / 0 % |

**Dow Jones täglich, Kurs plus Dividenden-Näherung - Bargeld = 10-Jahres-Zins (großzügig)**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 1920 bis 1949 | 1950 bis 1999 | ab 2000 |
|---|---:|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | 7,9 % / 87 % | 12,6 % / 41 % | 7,8 % / 52 % |
| Trend (fest 200) | +3,7 Pp, Rückgang 54 % (Halten 87 %) | -0,5 Pp, Rückgang 18 % (Halten 41 %) | -4,2 Pp, Rückgang 41 % (Halten 52 %) |
| Momentum (fest 252) | +2,3 Pp, Rückgang 42 % (Halten 87 %) | -2,0 Pp, Rückgang 43 % (Halten 41 %) | -3,0 Pp, Rückgang 39 % (Halten 52 %) |
| Trend (Walk-forward) | +3,5 Pp, Rückgang 30 % (Halten 87 %) | -1,9 Pp, Rückgang 36 % (Halten 41 %) | -4,3 Pp, Rückgang 34 % (Halten 52 %) |
| Momentum (Walk-forward) | +2,5 Pp, Rückgang 33 % (Halten 87 %) | -1,7 Pp, Rückgang 36 % (Halten 41 %) | -3,0 Pp, Rückgang 29 % (Halten 52 %) |

**Bitcoin täglich (US-Dollar) - Bargeld 0 %**
*Walk-forward-Fenster: 2015-12-31 bis 2026-10-25 (Kosten 0,50 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 62,1 % | - | 66,4 % | 83,8 % | 3,0 | 0 | 100 % | - |
| Trend (fest 200) | 59,1 % | -3,0 Pp | 52,3 % | 65,1 % | 2,9 | 66 | 62 % | 6 % / 7 % |
| Momentum (fest 365) | 61,9 % | -0,2 Pp | 59,2 % | 82,5 % | 3,1 | 23 | 73 % | 14 % / 45 % |
| Trend (Walk-forward) | 57,4 % | -4,7 Pp | 48,0 % | 69,3 % | 2,3 | 201 | 57 % | 1 % / 14 % |
| Momentum (Walk-forward) | 68,4 % | +6,2 Pp | 56,6 % | 70,2 % | 3,0 | 31 | 73 % | 5 % / 4 % |

**Bitcoin täglich (US-Dollar) - Bargeld 0 %**
*gesamte Reihe, nur feste Regeln: 2011-07-19 bis 2026-10-25 (Kosten 0,50 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 76,4 % | - | 80,2 % | 85,0 % | 3,2 | 0 | 100 % | - |
| Trend (fest 200) | 70,8 % | -5,5 Pp | 65,2 % | 78,2 % | 3,1 | 104 | 62 % | 4 % / 6 % |
| Momentum (fest 365) | 61,4 % | -15,0 Pp | 73,6 % | 85,0 % | 3,4 | 31 | 72 % | 42 % / 65 % |

**Bitcoin täglich (US-Dollar) - Bargeld 0 %**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 2016 bis 2019 | 2020 bis 2022 | ab 2023 |
|---|---:|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | 101,6 % / 84 % | 32,1 % / 77 % | 51,0 % / 53 % |
| Trend (fest 200) | +23,7 Pp, Rückgang 65 % (Halten 84 %) | +6,8 Pp, Rückgang 55 % (Halten 77 %) | -28,3 Pp, Rückgang 38 % (Halten 53 %) |
| Momentum (fest 365) | -17,7 Pp, Rückgang 77 % (Halten 84 %) | +36,0 Pp, Rückgang 53 % (Halten 77 %) | -13,8 Pp, Rückgang 33 % (Halten 53 %) |
| Trend (Walk-forward) | -18,8 Pp, Rückgang 69 % (Halten 84 %) | +23,2 Pp, Rückgang 62 % (Halten 77 %) | -15,3 Pp, Rückgang 36 % (Halten 53 %) |
| Momentum (Walk-forward) | +0,1 Pp, Rückgang 70 % (Halten 84 %) | +40,3 Pp, Rückgang 53 % (Halten 77 %) | -14,6 Pp, Rückgang 36 % (Halten 53 %) |

**Bitcoin täglich (US-Dollar) - Bargeld = 10-Jahres-Zins (großzügig)**
*Walk-forward-Fenster: 2015-12-31 bis 2026-10-25 (Kosten 0,50 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 62,1 % | - | 66,4 % | 83,8 % | 3,0 | 0 | 100 % | - |
| Trend (fest 200) | 61,0 % | -1,2 Pp | 52,3 % | 65,1 % | 2,9 | 66 | 62 % | 6 % / 10 % |
| Momentum (fest 365) | 63,4 % | +1,2 Pp | 59,2 % | 82,2 % | 3,1 | 23 | 73 % | 12 % / 42 % |
| Trend (Walk-forward) | 59,4 % | -2,8 Pp | 47,9 % | 68,5 % | 2,3 | 201 | 57 % | 1 % / 18 % |
| Momentum (Walk-forward) | 69,8 % | +7,7 Pp | 56,6 % | 70,2 % | 3,0 | 31 | 73 % | 5 % / 8 % |

**Bitcoin täglich (US-Dollar) - Bargeld = 10-Jahres-Zins (großzügig)**
*gesamte Reihe, nur feste Regeln: 2011-07-19 bis 2026-10-25 (Kosten 0,50 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 76,4 % | - | 80,2 % | 85,0 % | 3,2 | 0 | 100 % | - |
| Trend (fest 200) | 72,7 % | -3,7 Pp | 65,2 % | 78,0 % | 3,0 | 104 | 62 % | 5 % / 12 % |
| Momentum (fest 365) | 62,7 % | -13,7 Pp | 73,6 % | 85,0 % | 3,4 | 31 | 72 % | 36 % / 65 % |

**Bitcoin täglich (US-Dollar) - Bargeld = 10-Jahres-Zins (großzügig)**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 2016 bis 2019 | 2020 bis 2022 | ab 2023 |
|---|---:|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | 101,6 % / 84 % | 32,1 % / 77 % | 51,0 % / 53 % |
| Trend (fest 200) | +25,6 Pp, Rückgang 65 % (Halten 84 %) | +8,4 Pp, Rückgang 55 % (Halten 77 %) | -26,5 Pp, Rückgang 38 % (Halten 53 %) |
| Momentum (fest 365) | -17,0 Pp, Rückgang 76 % (Halten 84 %) | +37,5 Pp, Rückgang 53 % (Halten 77 %) | -11,8 Pp, Rückgang 32 % (Halten 53 %) |
| Trend (Walk-forward) | -17,0 Pp, Rückgang 68 % (Halten 84 %) | +24,8 Pp, Rückgang 62 % (Halten 77 %) | -13,0 Pp, Rückgang 34 % (Halten 53 %) |
| Momentum (Walk-forward) | +1,2 Pp, Rückgang 70 % (Halten 84 %) | +41,9 Pp, Rückgang 53 % (Halten 77 %) | -13,1 Pp, Rückgang 36 % (Halten 53 %) |

**Gold, Monatsmittel (US-Dollar) - Bargeld 0 %**
*Walk-forward-Fenster: 1979-12-01 bis 2026-09-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 4,9 % | - | 15,1 % | 62,1 % | 26,3 | 0 | 100 % | - |
| Trend (fest 10) | 5,6 % | +0,6 Pp | 12,3 % | 29,4 % | 15,2 | 69 | 57 % | 2 % / 0 % |
| Momentum (fest 12) | 4,9 % | +0,0 Pp | 12,5 % | 43,6 % | 25,9 | 66 | 58 % | 8 % / 24 % |
| Trend (Walk-forward) | 5,7 % | +0,8 Pp | 12,4 % | 32,6 % | 15,8 | 57 | 58 % | 2 % / 3 % |
| Momentum (Walk-forward) | 5,4 % | +0,5 Pp | 12,3 % | 35,9 % | 16,0 | 68 | 58 % | 3 % / 8 % |

**Gold, Monatsmittel (US-Dollar) - Bargeld 0 %**
*gesamte Reihe, nur feste Regeln: 1961-02-01 bis 2026-09-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 7,6 % | - | 15,3 % | 62,1 % | 26,3 | 0 | 100 % | - |
| Trend (fest 10) | 8,2 % | +0,6 Pp | 12,9 % | 29,4 % | 15,2 | 78 | 54 % | 0 % / 1 % |
| Momentum (fest 12) | 7,6 % | -0,0 Pp | 13,4 % | 43,6 % | 25,9 | 75 | 55 % | 0 % / 25 % |

**Gold, Monatsmittel (US-Dollar) - Bargeld 0 %**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 1980 bis 1999 | ab 2000 |
|---|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | -2,3 % / 62 % | 10,7 % / 39 % |
| Trend (fest 10) | +3,7 Pp, Rückgang 24 % (Halten 62 %) | -1,9 Pp, Rückgang 29 % (Halten 39 %) |
| Momentum (fest 12) | +2,0 Pp, Rückgang 37 % (Halten 62 %) | -1,6 Pp, Rückgang 22 % (Halten 39 %) |
| Trend (Walk-forward) | +3,8 Pp, Rückgang 24 % (Halten 62 %) | -1,7 Pp, Rückgang 33 % (Halten 39 %) |
| Momentum (Walk-forward) | +4,3 Pp, Rückgang 24 % (Halten 62 %) | -2,7 Pp, Rückgang 36 % (Halten 39 %) |

**Gold, Monatsmittel (US-Dollar) - Bargeld = 10-Jahres-Zins (großzügig)**
*Walk-forward-Fenster: 1979-12-01 bis 2026-09-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 4,9 % | - | 15,1 % | 62,1 % | 26,3 | 0 | 100 % | - |
| Trend (fest 10) | 8,5 % | +3,6 Pp | 12,3 % | 24,2 % | 8,6 | 69 | 57 % | 1 % / 1 % |
| Momentum (fest 12) | 7,8 % | +2,9 Pp | 12,5 % | 25,9 % | 8,4 | 66 | 58 % | 5 % / 4 % |
| Trend (Walk-forward) | 8,6 % | +3,7 Pp | 12,3 % | 25,5 % | 8,8 | 57 | 58 % | 1 % / 2 % |
| Momentum (Walk-forward) | 8,3 % | +3,3 Pp | 12,2 % | 29,2 % | 8,8 | 68 | 58 % | 1 % / 5 % |

**Gold, Monatsmittel (US-Dollar) - Bargeld = 10-Jahres-Zins (großzügig)**
*gesamte Reihe, nur feste Regeln: 1961-02-01 bis 2026-09-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 7,6 % | - | 15,3 % | 62,1 % | 26,3 | 0 | 100 % | - |
| Trend (fest 10) | 11,2 % | +3,6 Pp | 12,8 % | 24,2 % | 8,6 | 78 | 54 % | 0 % / 1 % |
| Momentum (fest 12) | 10,5 % | +2,9 Pp | 13,3 % | 25,9 % | 8,4 | 75 | 55 % | 0 % / 1 % |

**Gold, Monatsmittel (US-Dollar) - Bargeld = 10-Jahres-Zins (großzügig)**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 1980 bis 1999 | ab 2000 |
|---|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | -2,3 % / 62 % | 10,7 % / 39 % |
| Trend (fest 10) | +9,1 Pp, Rückgang 24 % (Halten 62 %) | -0,9 Pp, Rückgang 24 % (Halten 39 %) |
| Momentum (fest 12) | +7,2 Pp, Rückgang 26 % (Halten 62 %) | -0,7 Pp, Rückgang 21 % (Halten 39 %) |
| Trend (Walk-forward) | +9,2 Pp, Rückgang 24 % (Halten 62 %) | -0,8 Pp, Rückgang 25 % (Halten 39 %) |
| Momentum (Walk-forward) | +9,7 Pp, Rückgang 24 % (Halten 62 %) | -1,8 Pp, Rückgang 29 % (Halten 39 %) |

**UK-Aktien, Monatswerte 1709 bis 2017 (ohne Dividenden) - Bargeld 0 %**
*Walk-forward-Fenster: 1749-12-01 bis 2017-04-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 2,7 % | - | 10,2 % | 70,9 % | 28,2 | 0 | 100 % | - |
| Trend (fest 10) | 2,5 % | -0,2 Pp | 7,3 % | 43,7 % | 30,0 | 429 | 60 % | 0 % / 2 % |
| Momentum (fest 12) | 2,7 % | -0,0 Pp | 7,4 % | 31,0 % | 29,6 | 260 | 61 % | 0 % / 0 % |
| Trend (Walk-forward) | 2,5 % | -0,2 Pp | 7,3 % | 44,0 % | 30,8 | 405 | 59 % | 0 % / 3 % |
| Momentum (Walk-forward) | 2,5 % | -0,2 Pp | 7,3 % | 38,0 % | 38,9 | 389 | 60 % | 0 % / 0 % |

**UK-Aktien, Monatswerte 1709 bis 2017 (ohne Dividenden) - Bargeld 0 %**
*gesamte Reihe, nur feste Regeln: 1710-05-01 bis 2017-04-01 (Kosten 0,10 % je Wechsel)*

| Regel | Rendite p. a. | gegen Halten | Schwankung | Größter Rückgang | Längste Zeit unter Wasser (Jahre) | Wechsel | Investiert | Zufall besser (Rendite / Rückgang) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Kaufen und Halten | 2,5 % | - | 11,1 % | 76,3 % | 123,8 | 0 | 100 % | - |
| Trend (fest 10) | 2,2 % | -0,3 Pp | 8,4 % | 63,9 % | 97,8 | 498 | 59 % | 0 % / 18 % |
| Momentum (fest 12) | 2,4 % | -0,2 Pp | 8,5 % | 68,8 % | 103,8 | 310 | 61 % | 0 % / 30 % |

**UK-Aktien, Monatswerte 1709 bis 2017 (ohne Dividenden) - Bargeld 0 %**
*Teilzeiträume im Walk-forward-Fenster (Unterschied der Rendite p. a. zu Halten)*

| Regel | 1750 bis 1799 | 1800 bis 1949 | ab 1950 |
|---|---:|---:|---:|
| Kaufen und Halten (Rendite p. a. / Rückgang) | 0,1 % / 43 % | 1,7 % / 52 % | 7,1 % / 71 % |
| Trend (fest 10) | +0,5 Pp, Rückgang 28 % (Halten 43 %) | +0,1 Pp, Rückgang 41 % (Halten 52 %) | -1,6 Pp, Rückgang 44 % (Halten 71 %) |
| Momentum (fest 12) | +0,9 Pp, Rückgang 23 % (Halten 43 %) | +0,6 Pp, Rückgang 29 % (Halten 52 %) | -2,1 Pp, Rückgang 31 % (Halten 71 %) |
| Trend (Walk-forward) | +0,7 Pp, Rückgang 31 % (Halten 43 %) | +0,1 Pp, Rückgang 43 % (Halten 52 %) | -1,4 Pp, Rückgang 44 % (Halten 71 %) |
| Momentum (Walk-forward) | +0,6 Pp, Rückgang 32 % (Halten 43 %) | +0,1 Pp, Rückgang 38 % (Halten 52 %) | -1,5 Pp, Rückgang 33 % (Halten 71 %) |

### Empfindlichkeit: Kosten und Verzögerung

S&P 500, nominal mit Dividenden (Shiller, Monatsmittel): Rendite p. a. der festen Regel minus Halten, bei verschiedenen Kosten je Wechsel
| Bargeld | Regel | 0,05 % | 0,10 % | 0,30 % | 0,50 % | 1,00 % | ohne Verzögerung (Kosten wie Standard) | Halten p. a. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Bargeld 0 % | Trend (fest 10) | -1,7 Pp | -1,8 Pp | -2,1 Pp | -2,4 Pp | -3,1 Pp | -0,0 Pp | 9,4 % |
| Bargeld 0 % | Momentum (fest 12) | -2,3 Pp | -2,3 Pp | -2,5 Pp | -2,6 Pp | -3,1 Pp | -0,8 Pp | 9,4 % |
| Bargeld = 10-Jahres-Zins | Trend (fest 10) | +0,0 Pp | -0,1 Pp | -0,4 Pp | -0,6 Pp | -1,4 Pp | +1,7 Pp | 9,4 % |
| Bargeld = 10-Jahres-Zins | Momentum (fest 12) | -0,6 Pp | -0,6 Pp | -0,8 Pp | -1,0 Pp | -1,4 Pp | +0,9 Pp | 9,4 % |

Dow Jones täglich, nur Kurs: Rendite p. a. der festen Regel minus Halten, bei verschiedenen Kosten je Wechsel
| Bargeld | Regel | 0,05 % | 0,10 % | 0,30 % | 0,50 % | 1,00 % | ohne Verzögerung (Kosten wie Standard) | Halten p. a. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Bargeld 0 % | Trend (fest 200) | -0,1 Pp | -0,5 Pp | -1,9 Pp | -3,3 Pp | -6,7 Pp | +0,1 Pp | 5,2 % |
| Bargeld 0 % | Momentum (fest 252) | -0,6 Pp | -0,8 Pp | -1,7 Pp | -2,7 Pp | -4,9 Pp | -0,8 Pp | 5,2 % |
| Bargeld = 10-Jahres-Zins | Trend (fest 200) | +1,6 Pp | +1,2 Pp | -0,2 Pp | -1,7 Pp | -5,2 Pp | +1,7 Pp | 5,2 % |
| Bargeld = 10-Jahres-Zins | Momentum (fest 252) | +1,0 Pp | +0,8 Pp | -0,2 Pp | -1,1 Pp | -3,4 Pp | +0,8 Pp | 5,2 % |

Dow Jones täglich, Kurs plus Dividenden-Näherung: Rendite p. a. der festen Regel minus Halten, bei verschiedenen Kosten je Wechsel
| Bargeld | Regel | 0,05 % | 0,10 % | 0,30 % | 0,50 % | 1,00 % | ohne Verzögerung (Kosten wie Standard) | Halten p. a. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Bargeld 0 % | Trend (fest 200) | -1,9 Pp | -2,2 Pp | -3,7 Pp | -5,1 Pp | -8,7 Pp | -1,7 Pp | 9,5 % |
| Bargeld 0 % | Momentum (fest 252) | -2,4 Pp | -2,6 Pp | -3,5 Pp | -4,5 Pp | -6,8 Pp | -2,6 Pp | 9,5 % |
| Bargeld = 10-Jahres-Zins | Trend (fest 200) | -0,1 Pp | -0,5 Pp | -2,0 Pp | -3,5 Pp | -7,1 Pp | +0,0 Pp | 9,5 % |
| Bargeld = 10-Jahres-Zins | Momentum (fest 252) | -0,7 Pp | -1,0 Pp | -1,9 Pp | -2,9 Pp | -5,2 Pp | -1,0 Pp | 9,5 % |

Bitcoin täglich (US-Dollar): Rendite p. a. der festen Regel minus Halten, bei verschiedenen Kosten je Wechsel
| Bargeld | Regel | 0,25 % | 0,50 % | 1,00 % | 2,00 % | ohne Verzögerung (Kosten wie Standard) | Halten p. a. |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bargeld 0 % | Trend (fest 200) | -2,6 Pp | -5,5 Pp | -11,3 Pp | -22,3 Pp | -5,6 Pp | 76,4 % |
| Bargeld 0 % | Momentum (fest 365) | -14,2 Pp | -15,0 Pp | -16,7 Pp | -19,9 Pp | -6,4 Pp | 76,4 % |
| Bargeld = 10-Jahres-Zins | Trend (fest 200) | -0,7 Pp | -3,7 Pp | -9,5 Pp | -20,7 Pp | -3,8 Pp | 76,4 % |
| Bargeld = 10-Jahres-Zins | Momentum (fest 365) | -12,9 Pp | -13,7 Pp | -15,4 Pp | -18,6 Pp | -5,0 Pp | 76,4 % |

Gold, Monatsmittel (US-Dollar): Rendite p. a. der festen Regel minus Halten, bei verschiedenen Kosten je Wechsel
| Bargeld | Regel | 0,05 % | 0,10 % | 0,30 % | 0,50 % | 1,00 % | ohne Verzögerung (Kosten wie Standard) | Halten p. a. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Bargeld 0 % | Trend (fest 10) | +0,7 Pp | +0,6 Pp | +0,3 Pp | +0,1 Pp | -0,6 Pp | +1,2 Pp | 7,6 % |
| Bargeld 0 % | Momentum (fest 12) | +0,0 Pp | -0,0 Pp | -0,3 Pp | -0,5 Pp | -1,1 Pp | +0,7 Pp | 7,6 % |
| Bargeld = 10-Jahres-Zins | Trend (fest 10) | +3,6 Pp | +3,6 Pp | +3,3 Pp | +3,0 Pp | +2,4 Pp | +4,2 Pp | 7,6 % |
| Bargeld = 10-Jahres-Zins | Momentum (fest 12) | +2,9 Pp | +2,9 Pp | +2,6 Pp | +2,4 Pp | +1,7 Pp | +3,6 Pp | 7,6 % |

UK-Aktien, Monatswerte 1709 bis 2017 (ohne Dividenden): Rendite p. a. der festen Regel minus Halten, bei verschiedenen Kosten je Wechsel
| Bargeld | Regel | 0,05 % | 0,10 % | 0,30 % | 0,50 % | 1,00 % | ohne Verzögerung (Kosten wie Standard) | Halten p. a. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Bargeld 0 % | Trend (fest 10) | -0,2 Pp | -0,3 Pp | -0,6 Pp | -0,9 Pp | -1,8 Pp | +0,8 Pp | 2,5 % |
| Bargeld 0 % | Momentum (fest 12) | -0,1 Pp | -0,2 Pp | -0,4 Pp | -0,6 Pp | -1,1 Pp | +0,2 Pp | 2,5 % |

### Ereignisauswertung

Zelle: Median / Mittel / Anteil negativ. Reihe dow = Dow täglich (ab 1885), shiller_sp = S&P-Kurs, Monatsmittel (ab 1871).
| Reihe | Ereignisart | n | 1 Monat | 3 Monate | 6 Monate | 12 Monate | Tiefstand binnen 12 Monaten |
|---|---:|---:|---:|---:|---:|---:|---:|
| dow | krieg | 46 von 47 | 1,2 % / 0,6 % / 38 % | 3,2 % / 1,5 % / 34 % | 4,8 % / 4,1 % / 26 % | 9,5 % / 8,9 % / 35 % | -6,3 % / -9,8 % / 91 % |
| dow | bankenkrise | 17 von 19 | 1,7 % / -0,5 % / 47 % | 3,8 % / 3,1 % / 35 % | -0,3 % / 1,0 % / 53 % | 7,4 % / 2,1 % / 41 % | -12,3 % / -21,6 % / 88 % |
| dow | zinsschock | 7 von 9 | 1,1 % / 0,2 % / 44 % | -2,6 % / -0,8 % / 62 % | 0,4 % / -1,0 % / 43 % | 7,6 % / 3,9 % / 29 % | -10,3 % / -10,0 % / 100 % |
| dow | pandemie | 14 von 17 | 1,7 % / 0,4 % / 40 % | 5,9 % / 3,4 % / 27 % | 6,6 % / 8,7 % / 14 % | 14,1 % / 16,9 % / 0 % | -4,6 % / -9,3 % / 93 % |
| shiller_sp | krieg | 46 von 47 | 1,7 % / 0,8 % / 28 % | 1,9 % / 2,0 % / 36 % | 4,2 % / 3,6 % / 34 % | 8,7 % / 7,3 % / 33 % | -4,4 % / -7,8 % / 72 % |
| shiller_sp | bankenkrise | 17 von 19 | 1,3 % / -0,3 % / 47 % | 3,5 % / 3,7 % / 29 % | 6,2 % / 0,3 % / 47 % | 18,1 % / 2,6 % / 35 % | -5,9 % / -18,2 % / 59 % |
| shiller_sp | zinsschock | 7 von 9 | -0,8 % / -0,3 % / 56 % | 1,3 % / -0,8 % / 38 % | -1,4 % / -0,7 % / 57 % | 14,5 % / 7,2 % / 29 % | -4,7 % / -6,1 % / 100 % |
| shiller_sp | pandemie | 14 von 17 | 2,6 % / 2,8 % / 20 % | 4,7 % / 4,2 % / 20 % | 7,6 % / 10,0 % / 14 % | 16,7 % / 21,3 % / 0 % | -0,2 % / -3,2 % / 50 % |
| dow | alle Monate (Vergleich) | 1695 | 0,9 % / 0,6 % / 41 % | 2,0 % / 1,8 % / 40 % | 3,9 % / 3,5 % / 37 % | 7,0 % / 7,3 % / 34 % | -7,1 % / -10,8 % / 92 % |
| shiller_sp | alle Monate (Vergleich) | 1869 | 0,7 % / 0,5 % / 41 % | 1,9 % / 1,5 % / 39 % | 3,7 % / 3,1 % / 37 % | 7,3 % / 6,5 % / 35 % | -3,6 % / -7,3 % / 71 % |

### Gewählte Parameter je Testblock (Walk-forward)

- S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) / Trend: 1910=12, 1920=12, 1930=12, 1940=12, 1950=12, 1960=12, 1970=12, 1980=12, 1990=12, 2000=12, 2010=12, 2020=12
- S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) / Momentum: 1910=9, 1920=9, 1930=9, 1940=9, 1950=9, 1960=9, 1970=9, 1980=9, 1990=9, 2000=9, 2010=9, 2020=9
- S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) / CAPE fest: 1910=30, 1920=30, 1930=30, 1940=30, 1950=30, 1960=30, 1970=30, 1980=30, 1990=30, 2000=30, 2010=30, 2020=30
- S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) / CAPE relativ: 1910=0.8, 1920=0.8, 1930=0.9, 1940=0.7, 1950=0.6, 1960=0.9, 1970=0.9, 1980=0.9, 1990=0.9, 2000=0.9, 2010=0.9, 2020=0.9
- S&P 500, nominal mit Dividenden (Shiller, Monatsmittel) / Trend + CAPE: 1910=0.8, 1920=0.8, 1930=0.9, 1940=0.9, 1950=0.9, 1960=0.9, 1970=0.9, 1980=0.9, 1990=0.9, 2000=0.9, 2010=0.9, 2020=0.9
- Dow Jones täglich, nur Kurs / Trend: 1920=50, 1930=50, 1940=50, 1950=50, 1960=250, 1970=250, 1980=250, 1990=250, 2000=250, 2010=250, 2020=250
- Dow Jones täglich, nur Kurs / Momentum: 1920=126, 1930=126, 1940=126, 1950=126, 1960=126, 1970=126, 1980=126, 1990=126, 2000=126, 2010=126, 2020=126
- Dow Jones täglich, Kurs plus Dividenden-Näherung / Trend: 1920=100, 1930=50, 1940=50, 1950=50, 1960=250, 1970=250, 1980=250, 1990=250, 2000=250, 2010=250, 2020=250
- Dow Jones täglich, Kurs plus Dividenden-Näherung / Momentum: 1920=126, 1930=126, 1940=126, 1950=126, 1960=126, 1970=126, 1980=126, 1990=126, 2000=126, 2010=126, 2020=126
- Bitcoin täglich (US-Dollar) / Trend: 2016=50, 2018=50, 2020=50, 2022=50, 2024=50, 2026=50
- Bitcoin täglich (US-Dollar) / Momentum: 2016=270, 2018=270, 2020=270, 2022=270, 2024=270, 2026=270
- Gold, Monatsmittel (US-Dollar) / Trend: 1980=12, 1990=12, 2000=12, 2010=12, 2020=12
- Gold, Monatsmittel (US-Dollar) / Momentum: 1980=9, 1990=9, 2000=9, 2010=9, 2020=9
- UK-Aktien, Monatswerte 1709 bis 2017 (ohne Dividenden) / Trend: 1750=8, 1770=8, 1790=12, 1810=12, 1830=8, 1850=8, 1870=8, 1890=8, 1910=8, 1930=12, 1950=12, 1970=12, 1990=12, 2010=12
- UK-Aktien, Monatswerte 1709 bis 2017 (ohne Dividenden) / Momentum: 1750=3, 1770=3, 1790=12, 1810=9, 1830=3, 1850=9, 1870=9, 1890=12, 1910=12, 1930=12, 1950=12, 1970=9, 1990=9, 2010=9

### Kurzfazit

Kurzfazit: 50 Zeilen (Reihe, Bargeld-Art, Regel) im Walk-forward-Fenster; die Zeilen sind nicht unabhängig.
  Rendite p. a. höher als Halten: 18 von 50 (bei Bargeld 0 %: 5 von 27).
  Größter Rückgang mindestens ein Drittel kleiner als bei Halten: 37 von 50; Schwankung kleiner: 50; beides (mehr Rendite UND ein Drittel weniger Rückgang): 14.
  Zufallsvergleich (50 Zeilen): Rendite besser als 95 % der Zufallsläufe in 32, Rückgang besser als 95 % in 30 (allein durch Zufall zu erwarten: etwa 2,5).
