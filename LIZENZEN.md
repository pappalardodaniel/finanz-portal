# Lizenzen und fremde Bestandteile

kitrader enthält Code, der aus einem Open-Source-Projekt unter der MIT-Lizenz übernommen und umgebaut wurde.
Die MIT-Lizenz verlangt, dass ihr Hinweis erhalten bleibt. Er steht hier.

## Übernommener Code (MIT)

Betroffen sind die Marktdatenschicht, das Dashboard, die Analysen, kitrader Charts samt Indikatoren und Zeichenwerkzeugen,
der KI-Agent und die zugehörigen Tests und Dokumente:

- `src/kitrader/markt/`
- `agent/`
- `tests/markt/`, `tests/js/`
- `docs/markt/`
- `demo.sh`

Lizenztext:

```
MIT License

Copyright (c) 2026 Niko Sarge

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Die Lizenz gilt nur für den Code. Marktdaten, die mit diesem Code abgerufen werden, unterliegen den Bedingungen der
jeweiligen Quelle (`python3 -m kitrader.markt quellen` zeigt sie mit Link).

## Lightweight Charts™ 4.2.0

- Ort: `src/kitrader/markt/dashboard/static/charts/lib/lightweight-charts.standalone.production.js` (unverändert)
- Copyright © 2024 TradingView, Inc. (https://www.tradingview.com/)
- Lizenz: Apache License 2.0, Text in `src/kitrader/markt/dashboard/static/charts/lib/LICENSE-lightweight-charts.txt`
  (die Datei bleibt neben der Bibliothek)
- Hinweis zur Namensnennung: kitrader Charts nennt TradingView in der Fußzeile und im Lizenzdialog.

## Bei der Installation geladen (nicht im Repo)

- Python: `websockets` (optional für Live-Kurse), siehe `pyproject.toml`.
- Agent: `@anthropic-ai/claude-agent-sdk`, `playwright-core`, `ws`, `zod`, siehe `agent/package.json`.
- Schriften der Oberfläche kommen von Google Fonts (SIL Open Font License).

Sie stehen unter ihren eigenen Lizenzen.

## Nur zur Entwicklung, nicht mitgeliefert

- **TA-Lib** (BSD-Lizenz, https://ta-lib.org/) dient in `tests/markt/werkzeuge/referenz_talib.py` als unabhängige Referenz
  für die Indikator-Tests. Es wird nicht mitgeliefert und vom Produkt nicht verwendet.

Alle Indikatoren, Zeichenebenen, das Mini-Script und die Orderflow-Auswertung sind eigener Code
(Quellen der Formeln in `docs/markt/INDIKATOREN.md`).
