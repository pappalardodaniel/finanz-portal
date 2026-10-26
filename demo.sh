#!/bin/zsh
# kitrader im Demo-Betrieb: derselbe Code mit erfundenen Daten, eigenem Datenordner und eigenen Ports (8822/8823/8824).
# Das echte kitrader (8812/8813/8814) sammelt dabei ungestört weiter.
#
#   ./demo.sh start | stop | status | neu        Dienste starten, stoppen, Stand zeigen, Demo-Daten neu erzeugen
#   ./demo.sh szene ausbruch_auf MUSW --prozent 2 --minuten 3 [--in 30]
#   ./demo.sh szene arten                         alle Szenen und Werte
#   ./demo.sh telegram                            Demo-Bot einrichten (Token vorher in ~/.config/kitrader/markt-demo/telegram.env)
#   ./demo.sh terminal                            Demo ohne Hintergrunddienste im Terminal (wie python -m kitrader.markt demo)
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
export KITRADER_MARKT_DATEN="$HOME/.local/share/kitrader/markt-demo" KITRADER_MARKT_KONFIG="$HOME/.config/kitrader/markt-demo" KITRADER_MARKT_PORT=8822 KITRADER_MARKT_DEMO=1 PYTHONPATH="$PWD/src"
DIENSTE=(com.kitrader.markt-demo com.kitrader.markt-demo-analysen com.kitrader.markt-demo-agent)
case "$1" in
  start)
    mkdir -p "$KITRADER_MARKT_DATEN/analysen" "$KITRADER_MARKT_KONFIG"
    [ -f "$KITRADER_MARKT_DATEN/analysen/ki.json" ] || echo '{"routinen": false}' > "$KITRADER_MARKT_DATEN/analysen/ki.json"   # keine täglichen Claude-Läufe in der Demo
    for d in $DIENSTE; do launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$d.plist 2>/dev/null || launchctl kickstart -k gui/$(id -u)/$d; done
    echo "Demo läuft: http://localhost:8822/  (erster Start baut etwa eine Minute lang die Historie auf)";;
  stop)
    for d in $DIENSTE; do launchctl bootout gui/$(id -u)/$d 2>/dev/null; done; echo "Demo gestoppt";;
  status)
    for d in $DIENSTE; do launchctl print gui/$(id -u)/$d 2>/dev/null | grep -m1 "state =" | sed "s/^/$d: /" || echo "$d: aus"; done;;
  neu)
    "$0" stop; rm -rf "$KITRADER_MARKT_DATEN"; "$0" start;;
  terminal)
    $PY -m kitrader.markt demo;;
  telegram)
    $PY -m kitrader.markt.analysen telegram;;
  szene)
    shift; if [ "$1" = "arten" ] || [ -z "$1" ]; then $PY -m kitrader.markt demo arten; else $PY -m kitrader.markt demo szene "$@"; fi;;
  *) sed -n 2,9p "$0";;
esac
