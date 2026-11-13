# Protocole des règles H11 et H12 — l'or face au dollar

**Statut : PROPOSITION du 13 novembre 2026. Pas encore figé. Les données du dollar n'ont pas été reçues :
rien n'a été vu, rien n'a été mesuré.** Il sera figé à la validation de l'utilisateur, avant l'ouverture des
fichiers EURUSD et USDJPY.

## Pourquoi

66 configurations fondées sur le seul prix de l'or ont été mesurées sur 2023-2024 : aucune ne passe. L'or est
coté en dollars et baisse en général quand le dollar monte. Question nouvelle : le dollar bouge-t-il quelques
minutes avant l'or, assez pour payer le spread ?

Attente réaliste : ces marchés sont très surveillés et l'ajustement est en principe quasi instantané. Un
résultat nul est le plus probable.

## Données

Bougies M1 de XAUUSD, EURUSD et USDJPY exportées du même serveur MT5 par `AMB_ExportBars.mq5`, mêmes
horodatages, règle horaire `utc+2/+3_heure_ete_UE`. Seules les minutes où les trois instruments ont une
bougie sont utilisées.

Mouvement du dollar sur `M` minutes : moyenne de la variation de USDJPY et de l'opposé de la variation de
EURUSD, chacune divisée par son propre ATR 14 M1. Positif quand le dollar monte.
Mouvement de l'or sur `M` minutes : variation de XAUUSD divisée par son ATR 14 M1 (`move_M` de la couche
technique).

## Règles

Communes : entrée à l'ouverture de la bougie M1 suivante ; une position à la fois ; stop à 1 ATR M15 de l'or ;
pas d'objectif ; sortie après `D` minutes ; filtres de spread et d'annonces du protocole H4-H6 ; coût = spread
de l'or.

### H11 — L'or en retard sur le dollar

Le dollar a bougé d'au moins `K` sur `M` minutes et l'or n'a pas encore suivi (son mouvement dans le sens
attendu est inférieur à 1). Entrée sur l'or dans le sens attendu : vente si le dollar a monté, achat s'il a
baissé.

Grille : `M` = 5 ou 15 ; `K` = 2 ou 3 ; `D` = 15 ou 60. 8 configurations.

### H12 — Mouvement de l'or non confirmé par le dollar

L'or a bougé d'au moins 4 sur 15 minutes et le dollar n'a pas bougé dans le sens qui l'expliquerait (son
mouvement dans ce sens est inférieur à `C`). Entrée à contre-sens du mouvement de l'or.

Grille : `C` = 0 ou 1 ; `D` = 15 ou 60. 4 configurations.

Total : 12 configurations, pas une de plus.

## Périodes et critère

Identiques au protocole H4-H6 : développement 2023-2024, validation 2025, test final 2026 verrouillé.
Au moins 100 trades, gain net moyen positif borne basse de l'intervalle à 95 % comprise, positif dans chaque
moitié avec au moins 30 trades.

## Ce que l'étude ne dira pas

- Un décalage de quelques secondes entre instruments est invisible sur des bougies M1, et c'est là que les
  professionnels opèrent. Un résultat positif devra être confirmé sur ticks.
- Les cotations d'un serveur de démonstration ne sont pas celles d'un broker réel.

## Écarts

Aucun à ce jour.
