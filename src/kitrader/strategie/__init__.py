"""Strategien: Schnittstelle, Beispiele und Kandidaten. Neue Strategien in STRATEGIEN eintragen."""
from .basis import Signal, Strategie, signal_zu_order
from .beispiele import Eroeffnungsausbruch, VwapRueckkehr
from .kandidaten import KANDIDATEN, BollingerRsi, Donchian, MaTrend, Momentum, VolumenAusbruch
from .ki_strategie import KiStrategie

STRATEGIEN: dict[str, type[Strategie]] = {
    Eroeffnungsausbruch.name: Eroeffnungsausbruch,
    VwapRueckkehr.name: VwapRueckkehr,
    # Kandidaten fürs Strategie-Labor (STRATEGIE-BERICHT.md): ungeprüft, kein Erfolgsversprechen
    **{klasse.name: klasse for klasse in KANDIDATEN},
    # KI handelt selbst (ki_strategie.py): nur im Autopiloten, Papierbetrieb zuerst
    KiStrategie.name: KiStrategie,
}


def strategie_holen(name: str, einstellungen: dict | None = None, **ueberschreibungen) -> Strategie:
    """Baut eine Strategie; Parameter aus [strategie.<name>] der Einstellungen, darüber `ueberschreibungen`."""
    if name not in STRATEGIEN:
        raise ValueError(f"Unbekannte Strategie {name!r}. Vorhanden: {', '.join(sorted(STRATEGIEN))}")
    parameter = dict((einstellungen or {}).get("strategie", {}).get(name, {}))
    parameter.update(ueberschreibungen)
    return STRATEGIEN[name](**parameter)


__all__ = ["Signal", "Strategie", "signal_zu_order", "STRATEGIEN", "strategie_holen",
           "Eroeffnungsausbruch", "VwapRueckkehr", "KANDIDATEN", "Donchian", "BollingerRsi", "MaTrend", "Momentum",
           "VolumenAusbruch", "KiStrategie"]
