# Protocole des règles d'entrée H4 à H6

**Statut : FIGÉ le 13 novembre 2026, après validation de l'utilisateur et avant toute mesure.**
Il ne se modifie plus. Toute déviation est notée dans « Écarts ».

## Ce qui est déjà connu au moment d'écrire

L'étude H1 à H3 a été lue sur 2023-2024 : entrer dans le sens d'une impulsion M1 ne paie pas le spread, et le
mouvement brut moyen après l'impulsion est proche de zéro, plutôt négatif. La règle H6 ci-dessous (retour
après excès) est inspirée de ce constat : elle n'est donc pas indépendante des données de développement.
C'est la validation sur 2025, jamais regardée, qui tranchera.

## Règles communes

| Élément | Choix |
| --- | --- |
| Données | Bougies M1 XAUUSD exportées de MT5, règle horaire `utc+2/+3_heure_ete_UE` (Écart 1 du protocole H1-H3) |
| Mesures | Couche technique `src/amb_research/technical.py`, inchangée |
| Entrée | À l'ouverture de la bougie M1 qui suit le signal. Achat à l'ask (bid + spread), vente au bid |
| Position | Une seule à la fois, toutes règles confondues à l'intérieur d'une même règle |
| Stop | 1,0 fois l'ATR M15 connu au signal, fixe, jamais déplacé |
| Objectif | `R` fois la distance du stop |
| Même bougie | Si stop et objectif sont touchés dans la même bougie M1, le stop est compté |
| Filtres | Pas d'entrée si `spread_ratio` > 2, ni de 30 minutes avant à 5 minutes après une annonce majeure |
| Coût | Spread de la bougie d'entrée ou de sortie. Ni commission ni slippage à ce stade |

## Précisions d'exécution, fixées avant la mesure

- Les niveaux de stop et d'objectif sont posés à partir du prix d'ouverture (bid) de la bougie d'entrée.
- Si une bougie ouvre au-delà du stop (trou de cotation), la sortie se fait à ce prix d'ouverture, plus
  défavorable. Un objectif dépassé par un trou est compté au niveau de l'objectif, pas mieux.
- Durée maximale : sortie à la clôture de la première bougie M1 qui atteint la durée ; si le marché était
  fermé entre-temps, sortie à l'ouverture suivante.
- Pas d'entrée si la bougie qui suit le signal ouvre plus de 5 minutes après lui (fermeture du marché).
- Un nouveau signal n'est accepté qu'à partir de la bougie de sortie du trade précédent.
- Un trade encore ouvert à la fin de la période n'est pas compté.
- « Au cours des 30 dernières minutes » (H4) : les 30 dernières bougies M1, bougie du signal comprise.
- H5 : seule compte la première clôture de la journée au-delà de chaque borne, entre 08h00 et 11h00 heure de
  Londres. Si les filtres la refusent, il n'y a pas d'entrée de ce côté ce jour-là.
- Les moitiés de période sont coupées au milieu du calendrier (1er janvier 2024 pour le développement).

## Les trois règles

### H4 — Repli dans la tendance

Achat si : `trend_h1` = +1 et `trend_h4` = +1 ; le RSI M5 est passé sous le seuil `S` au cours des
30 dernières minutes ; la bougie M1 clôture au-dessus du plus haut de la bougie M1 précédente. Vente symétrique
(RSI au-dessus de 100 − `S`). Sortie au plus tard après 120 minutes.

Grille : `S` = 30 ou 40 ; `R` = 1 ou 2. 4 configurations.

### H5 — Cassure du range asiatique à Londres

Pendant les 3 heures qui suivent l'ouverture de Londres : achat à la première clôture M1 au-dessus de
`asia_high`, vente à la première clôture sous `asia_low`. Une seule entrée par jour. Sortie au plus tard
après 240 minutes.

Grille : avec ou sans filtre `trend_h1` dans le sens de la cassure ; `R` = 1 ou 2. 4 configurations.

### H6 — Retour après excès

Vente si : `move_15` ≥ +`K` (hausse de K fois l'ATR M1 en 15 bougies) et RSI M5 ≥ 80. Achat symétrique
(`move_15` ≤ −`K` et RSI M5 ≤ 20). Objectif `R` = 1.

Grille : `K` = 4 ou 6 ; sortie au plus tard après 15 ou 60 minutes. 4 configurations.

Total : 12 configurations, pas une de plus.

## Mesures

Par configuration : nombre de trades, part de gagnants, gain moyen net par trade avec intervalle de confiance
à 95 % (rééchantillonnage, 2 000 tirages, graine fixe), profit factor net, pire série de pertes, résultat par
moitié de période et par année.

## Périodes

Identiques au protocole H1-H3 : développement 2023-2024, validation 2025, test final 2026 verrouillé.

## Critère de passage

Sur le développement, une configuration passe si :

- elle compte au moins 100 trades ;
- son gain moyen net est positif, borne basse de l'intervalle à 95 % comprise ;
- son gain moyen net est positif dans chacune des deux moitiés, avec au moins 30 trades par moitié.

Elle n'est retenue que si elle passe aussi, inchangée, sur la validation. Avec 12 configurations, une peut
passer par hasard : c'est le rôle de la validation de l'écarter.

## Ce que l'étude ne dira pas

- Stop et objectif sont simulés sur des bougies M1, pas sur des ticks : à confirmer dans le testeur MT5.
- Pas de sortie dynamique, pas de slippage, pas de commission.
- Une règle qui passe autorise à construire un prototype, rien de plus.

## Écarts

Aucun à ce jour.
