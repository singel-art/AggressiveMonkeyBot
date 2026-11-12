"""Politique temporelle du projet.

Decision du projet (12 novembre 2026) : politique `balanced`, avec une marge de
securite de 15 minutes sur les heures de publication estimees.

Trois lectures d'un meme historique sont possibles :

- 'balanced' (defaut) : une valeur dont l'heure est estimee devient visible
  15 minutes apres cette heure. Une valeur dont l'instant a ete observe par
  notre collecteur est visible a cet instant.
- 'strict' : une valeur dont l'heure est estimee n'est visible qu'a la fin de
  son jour de disponibilite a New York. A utiliser pour mesurer la sensibilite
  d'un backtest aux heures non verifiees.
- 'estimate' : l'heure estimee, sans marge. Analyse seulement.

Limite de 'balanced', a garder en tete : la marge couvre un retard de quelques
minutes, pas une heure de publication fausse. Si une statistique supposee a
8 h 30 est sortie a 10 h, elle serait vue trop tot. Comparer un backtest en
'balanced' et en 'strict' mesure l'effet de ce risque.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from .timeutil import ET, iso, local_to_utc, parse_iso

ESTIMATED_TIME_MARGIN = timedelta(minutes=15)
DEFAULT_POLICY = "balanced"
COLUMNS = {"balanced": "available_at_balanced", "strict": "available_at_strict",
           "estimate": "available_at"}


def column(policy: str) -> str:
    try:
        return COLUMNS[policy]
    except KeyError:
        raise ValueError(f"politique temporelle inconnue : {policy!r} (attendu : {', '.join(COLUMNS)})") from None


def bounds(available_at: str, time_quality: str) -> tuple[str, str]:
    """(instant 'balanced', instant 'strict') pour une disponibilite donnee."""
    if time_quality in ("observed", "published"):
        # Instant mesure par notre collecteur, ou heure publiee par une source : pas de marge.
        return available_at, available_at
    moment: datetime = parse_iso(available_at)
    local_day = moment.astimezone(ET).date()
    end_of_day = local_to_utc(local_day + timedelta(days=1), time(0, 0), ET)
    return iso(moment + ESTIMATED_TIME_MARGIN), iso(max(end_of_day, moment + ESTIMATED_TIME_MARGIN))
