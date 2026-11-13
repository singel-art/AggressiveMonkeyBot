# Protocole des règles H8 à H10 — unités de temps M15 et H1

**Statut : FIGÉ le 13 novembre 2026, avant toute mesure.** L'utilisateur a délégué le choix de la suite
(« ce que tu penses le mieux ») ; le protocole est donc figé par son auteur. Il ne se modifie plus. Toute
déviation est notée dans « Écarts ».

## Pourquoi

54 configurations M1 ont été mesurées sur 2023-2024 (protocoles H1-H3, H4-H6, H7) : aucune ne gagne après le
spread. Le spread (environ 0,13 $) y représente une part trop grande du mouvement visé. Sur M15 et H1, le
mouvement visé est 5 à 10 fois plus grand pour le même spread.

Ce qui est déjà connu des données de développement au moment d'écrire : les résultats agrégés de ces
54 configurations M1, rien sur des règles H1. Les trois familles ci-dessous sont des classiques (suivi de
tendance par canal, repli dans la tendance, retour à la moyenne), pas des règles tirées de ces résultats.

## Règles communes

| Élément | Choix |
| --- | --- |
| Données | Bougies M1 XAUUSD de MT5, règle horaire `utc+2/+3_heure_ete_UE`. M15 et H1 reconstruites, alignées UTC |
| Signal | Évalué à la clôture d'une bougie M15 ou H1 terminée |
| Entrée | À l'ouverture de la bougie M1 suivante. Achat à l'ask, vente au bid |
| Position | Une seule à la fois par configuration |
| Stop | En multiples de l'ATR 14 H1 connu au signal, fixe, jamais déplacé |
| Simulation | Sur les bougies M1, mêmes précisions d'exécution que le protocole H4-H6 |
| Durée | En temps d'horloge : un week-end compte |
| Filtres | Pas d'entrée si `spread_ratio` > 2, ni de 30 minutes avant à 5 minutes après une annonce majeure |
| Coût | Spread de la bougie d'entrée ou de sortie. **Le swap de nuit n'est pas compté**, ni commission ni slippage |

## Les trois règles

### H8 — Cassure de canal H1 (suivi de tendance)

Achat quand une bougie H1 clôture au-dessus du plus haut des `N` bougies H1 précédentes ; vente symétrique.
Stop à 2 ATR H1. Pas d'objectif. Sortie après `D` heures.

Grille : `N` = 20 ou 55 ; `D` = 24 ou 72 heures. 4 configurations.

### H9 — Repli M15 dans la tendance H1 et H4

Achat si `trend_h1` = +1 et `trend_h4` = +1, si le RSI 14 M15 est passé sous `S` au cours des 8 dernières
bougies M15 (bougie du signal comprise), et si la bougie M15 clôture au-dessus du plus haut de la précédente.
Vente symétrique (RSI au-dessus de 100 − `S`). Stop à 1,5 ATR H1. Objectif `R` fois le stop. Sortie au plus
tard après 48 heures.

Grille : `S` = 30 ou 40 ; `R` = 1,5 ou 3. 4 configurations.

### H10 — Retour à la moyenne H1

Achat quand une bougie H1 clôture sous la moyenne des 20 dernières clôtures H1 moins `K` écarts-types ; vente
quand elle clôture au-dessus de la moyenne plus `K` écarts-types. Stop à 2 ATR H1. Objectif 1 fois le stop.
Sortie au plus tard après `D` heures.

Grille : `K` = 2 ou 2,5 ; `D` = 6 ou 24 heures. 4 configurations.

Total : 12 configurations, pas une de plus.

## Mesures, périodes, critère

Identiques au protocole H4-H6. Développement 2023-2024, validation 2025, test final 2026 verrouillé.
Une configuration passe si elle compte au moins 100 trades, si son gain net moyen est positif borne basse de
l'intervalle à 95 % comprise, et s'il est positif dans chaque moitié avec au moins 30 trades. Elle n'est
retenue que si elle passe aussi, inchangée, sur la validation.

## Ce que l'étude ne dira pas

- L'or a fortement monté en 2023-2024 : une règle qui achète souvent peut gagner pour cette seule raison.
  Le rapport sépare achats et ventes pour le voir.
- Le swap de nuit, non compté, pèse sur les positions gardées plusieurs jours.
- Une règle qui passe autorise un prototype, à confirmer dans le testeur MT5.

## Écarts

Aucun à ce jour.
