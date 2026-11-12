"""Catalogue des series suivies.

Deux familles :
- 'market' : series quotidiennes, non revisees en pratique (taux, dollar).
  Leur date de disponibilite vient d'une regle prudente ou de l'observation en direct.
- 'macro'  : series mensuelles revisees (inflation, emploi). Leur historique est
  reconstruit version par version a partir d'ALFRED, avec la date de chaque publication.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class Series:
    series_id: str
    title: str
    theme: str
    kind: str  # 'market' ou 'macro'
    unit: str
    # market : delai de mise a disposition, en jours ouvres apres la date d'observation,
    # et heure (New York) a laquelle la valeur est consideree connue ce jour-la.
    lag_business_days: int = 1
    available_time_et: time = time(18, 0)
    # macro : heure usuelle de publication (New York). C'est une convention, pas une mesure.
    release_time_et: time = time(8, 30)
    fred_release_id: int | None = None
    max_age_days: int = 7  # au-dela, la valeur est declaree perimee dans le contexte


FRED_SERIES: tuple[Series, ...] = (
    # --- Taux et politique monetaire
    Series("DGS10", "Rendement du Treasury a 10 ans", "rates", "market", "%", max_age_days=6),
    Series("DGS2", "Rendement du Treasury a 2 ans", "rates", "market", "%", max_age_days=6),
    Series("DFII10", "Rendement reel a 10 ans (TIPS)", "rates", "market", "%", max_age_days=6),
    Series("T10YIE", "Inflation anticipee a 10 ans (point mort)", "rates", "market", "%", max_age_days=6),
    Series("DFF", "Taux effectif des fonds federaux", "policy", "market", "%", max_age_days=6),
    Series("DFEDTARU", "Fourchette cible de la Fed, borne haute", "policy", "market", "%",
           lag_business_days=0, available_time_et=time(0, 0), max_age_days=6),
    Series("DFEDTARL", "Fourchette cible de la Fed, borne basse", "policy", "market", "%",
           lag_business_days=0, available_time_et=time(0, 0), max_age_days=6),
    # --- Dollar
    Series("DTWEXBGS", "Indice large du dollar (Fed)", "dollar", "market", "indice",
           lag_business_days=5, max_age_days=14),
    # --- Inflation
    Series("CPIAUCSL", "CPI, tous postes, corrige des variations saisonnieres", "inflation", "macro",
           "indice", fred_release_id=10, max_age_days=50),
    Series("CPILFESL", "CPI hors alimentation et energie", "inflation", "macro", "indice",
           fred_release_id=10, max_age_days=50),
    Series("PCEPI", "Indice des prix PCE", "inflation", "macro", "indice",
           fred_release_id=54, max_age_days=50),
    Series("PCEPILFE", "Indice des prix PCE hors alimentation et energie", "inflation", "macro",
           "indice", fred_release_id=54, max_age_days=50),
    # --- Emploi
    Series("PAYEMS", "Emploi salarie non agricole (NFP)", "labor", "macro", "milliers",
           fred_release_id=50, max_age_days=50),
    Series("UNRATE", "Taux de chomage", "labor", "macro", "%", fred_release_id=50, max_age_days=50),
)

SERIES_BY_ID = {s.series_id: s for s in FRED_SERIES}

# Publications suivies dans le calendrier FRED (identifiant de "release" -> libelle, importance).
FRED_RELEASES: dict[int, tuple[str, str]] = {
    10: ("Consumer Price Index (CPI)", "high"),
    50: ("Employment Situation (NFP, chomage)", "high"),
    54: ("Personal Income and Outlays (PCE)", "high"),
    53: ("Gross Domestic Product", "medium"),
}

# Series BLS utilisees pour recouper les chiffres FRED (meme donnee, source d'origine).
BLS_CROSSCHECK: dict[str, str] = {
    "CUSR0000SA0": "CPIAUCSL",
    "CES0000000001": "PAYEMS",
    "LNS14000000": "UNRATE",
}
