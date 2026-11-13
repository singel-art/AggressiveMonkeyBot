# Protocole de la règle H7 — dérive après annonce

**Statut : PROPOSITION du 13 novembre 2026. Pas encore figé, rien n'a été mesuré.**
Il sera figé à la validation de l'utilisateur, puis ne se modifiera plus.

## Origine de l'idée

- Lardic et Mignon (2003), résumé seul lu : sur les actions françaises, les annonces sont « progressivement
  reflétées » dans les cours.
- B. Johnson, *Algorithmic Trading & DMA* (2010), chapitre 14.5, lu : sur les taux et les devises, la réaction
  aux annonces américaines est presque immédiate ; sur les devises elle dure 5 à 10 minutes et se stabilise
  en moins d'une heure ; le spread s'élargit et la volatilité reste élevée 30 minutes ou plus.

Aucune de ces sources ne porte sur l'or ni ne donne de règle de trading. La littérature laisse attendre un
effet faible : l'essentiel du mouvement a lieu dans la première minute.

## Question

Après la première minute de réaction à une grande annonce américaine, l'or continue-t-il dans le même sens
d'un montant supérieur au coût ?

## Annonces retenues

Calendrier MT5 importé, heure réelle de publication : emploi non agricole, CPI m/m, PPI m/m, ventes au détail
m/m, PIB t/t, PMI manufacturier ISM, décision de taux de la Fed. Plusieurs annonces à la même minute comptent
pour une seule.

## Règle

1. Bougie de réaction : la bougie M1 qui ouvre à l'heure de l'annonce.
2. Signal si son corps (clôture moins ouverture, en valeur absolue) vaut au moins `K` fois l'ATR M1 connu à la
   bougie précédente. Sens du signal : celui de la bougie.
3. Entrée à l'ouverture de la bougie suivante, dans le sens du signal.
4. Stop : retour complet de la réaction, c'est-à-dire l'extrême opposé de la bougie de réaction.
5. Sortie à la clôture après `D` minutes si le stop n'est pas touché. Pas d'objectif.

Coût : spread réel de la bougie d'entrée (achat) ou de sortie (vente), sans filtre de spread. Les précisions
d'exécution du protocole H4-H6 s'appliquent (trou de cotation, marché fermé).

Grille : `K` = 1 ou 2 ; `D` = 5, 10 ou 30 minutes. 6 configurations, pas une de plus.

## Périodes et critère

Identiques au protocole H4-H6 : développement 2023-2024, validation 2025, test final 2026 verrouillé.
Au moins 100 trades, gain net moyen positif borne basse de l'intervalle à 95 % comprise, positif dans chaque
moitié avec au moins 30 trades. Environ 160 annonces sur deux ans : une configuration peut échouer faute de
trades, ce qui compte comme un échec.

## Ce que l'étude ne dira pas

- C'est le moment où l'exécution réelle est la plus mauvaise (spread, glissement, refus d'ordre). Les bougies
  M1 l'ignorent en grande partie : un résultat positif devra être confirmé sur ticks avant toute suite.
- Quelques trades par semaine au plus : ce n'est pas un scalper permanent.

## Écarts

Aucun à ce jour.
