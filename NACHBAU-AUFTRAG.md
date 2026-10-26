# Auftrag: kitrader als vollständiger Nachbau von ic-delta plus eigene Teile

## Ziel

Ein eigenes Tool **kitrader**, das genauso aussieht und alles kann wie ic-delta (Oberfläche, iC Charts mit
Indikatoren und Zeichenwerkzeugen, Analysen, Orderbuch, Papierhandel, KI-Agent, Einrichtung, Demo), aber als
**eigener Code**: aus dem Code von ic-delta umgebaut und neu aufgebaut, nicht als eingehängtes fremdes Repo,
ohne den Git-Verlauf von ic-delta, ohne den Namen „iC Delta“ und ohne Verweise auf Niko. Dazu kommen alle
Teile, die schon in `src/kitrader/` stecken.

Vorlage zum Lesen (nur als Quelle, nicht ins Repo einhängen): `https://github.com/M-37-AI/ic-delta`.

## Regeln

- Nichts installieren, ohne zu fragen (für den KI-Agenten von ic-delta wird Node.js gebraucht).
- Kein echtes Geld. Schlüssel nie ins Repo, nur unter `~/.config/kitrader/` mit chmod 600.
- In Etappen arbeiten; nach jeder Etappe Tests laufen lassen, im Browser prüfen, committen und pushen
  (Branch `claude/hallo-4n6qhj`) und mir kurz die Prüfausgabe zeigen. Nie „fertig“ ohne Prüfausgabe.
- Die MIT-Lizenz von ic-delta verlangt, dass der Lizenzhinweis bei übernommenem Code erhalten bleibt:
  den Text in eine Datei `LIZENZEN.md` legen (rechtlich nötig, sonst nirgends sichtbar).

## Etappen

1. **Nachbau der Basis:** den Code von ic-delta in das Paket `kitrader` übernehmen und umbauen
   (Paket `marktdaten` → Module unter `kitrader`, Namen „iC Delta“/„iC Charts“ → „kitrader“/„kitrader Charts“,
   Texte wie „Was Niko gerade sieht“ → „Was du gerade siehst“). Gleiche Oberfläche, gleiche Reiter, gleiche
   Funktionen. Vorhandene kitrader-Module (Broker, Risiko, Autopilot, Journal, Historie, Muster, OSIRIS,
   Nachrichten, Welt-Rotation, KI, Vorwärtstest, Labor) bleiben und werden nicht doppelt gebaut.
   Prüfen: alle Tests von ic-delta und kitrader grün, Oberfläche startet und sieht aus wie ic-delta.
2. **Deine Teile in die Oberfläche:**
   - Reiter **Trends**: Welt-Rotation (Gewinner/Verlierer nach Region und Anlageklasse), Nachrichten-Radar,
     „ähnliche Fälle früher“ aus der Muster-Engine mit der Reaktion von Aktien, Gold, Bitcoin.
   - **KI-Einschätzung kurz/mittel/lang** (aus `kitrader/ki.py`) im Agenten-Panel und als Anzeige am Chart.
   - **Weltlage** (OSIRIS), **lange Historie** mit Ereignissen als Marker im Chart, Ereignisstudie per Klick.
   - **Papierhandel** in den Charts um Short, Stop, Ziel und die Risikoprüfung erweitern; Order-Fenster und
     „Position schließen“.
   - **Autopilot** mit Betriebsart Papier/Demo/Echt, Broker Interactive Brokers und Kraken, Not-Aus-Knopf,
     Journal mit Trefferquote.
   - **Symbole** robust erkennen: „BTC“, „Bitcoin“, „XBT“, „BTCEUR“, „BTC-EUR“ → BTC/EUR (Kraken nennt
     Bitcoin XBT; Paare über `/0/public/AssetPairs` auflösen).
3. **KI-Agent:** der Agent von ic-delta nutzt die Claude-CLI mit meinem Max-Abo; er bekommt zusätzlich
   Werkzeuge für Trends, Nachrichten, Weltlage, Muster, Historie, KI-Einschätzung und Autopilot-Status.
4. **Prüfung im Browser:** alle Reiter mit echten Kraken-Kursen im Papierbetrieb durchgehen, eine
   Papier-Order mit Stop setzen und wieder schließen, KI-Einschätzung für BTC abrufen, Trends ansehen.
5. **Aufräumen:** README auf Deutsch für mich (Start per Doppelklick `kitrader-starten.command`),
   UEBERGABE.md aktualisieren.

Bericht am Ende einmal: was läuft (mit Ausgaben und Bildschirmfotos), was offen ist.
