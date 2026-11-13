# Protocole de l'étude des impulsions M1 (hypothèses H1 à H3)

Protocole figé le 13 novembre 2026, **avant** réception de toute donnée de prix. Il ne se modifie plus une
fois les données vues. Toute déviation est notée dans la section « Écarts » en bas de ce document.

## Question

Après une impulsion M1 sur XAUUSD, le prix continue-t-il dans le même sens d'un montant supérieur au coût
d'exécution ? Si la réponse est non dans toutes les configurations, l'entrée « momentum M1 » est abandonnée.

## Données

| Élément | Choix |
| --- | --- |
| Source | Bougies M1 exportées de MetaTrader 5 par `mt5/Scripts/AMB_ExportBars.mq5` |
| Symbole | XAUUSD, sur le compte du broker visé |
| Prix | Bid (convention MT5). L'ask est reconstitué par bid + spread |
| Coût | Spread de la bougie d'entrée (achat) ou de sortie (vente). Commission non comptée à ce stade |
| Heures | Converties en UTC. La règle de conversion est déduite des publications de l'emploi américain, dont l'heure réelle vient du calendrier MT5 ; elle est refusée si elle explique moins de 90 % des cas |

Le spread d'une bougie M1 est une approximation du spread réellement payé. Une conclusion favorable devra
être confirmée sur des ticks réels avant tout développement de robot.

## Signal

Une impulsion haussière est une bougie M1 qui :

1. clôture au-dessus du plus haut des `N` bougies précédentes ;
2. a un corps (clôture moins ouverture) d'au moins `k` fois l'ATR 14 de la bougie précédente.

L'impulsion baissière est symétrique. Tout est connu à la clôture de la bougie signal. L'entrée se fait à
l'ouverture de la bougie suivante. Après un signal retenu, les suivants sont ignorés pendant 60 bougies :
une seule position à la fois.

## Grille, fixée d'avance

| Paramètre | Valeurs |
| --- | --- |
| `N` (bougies de référence) | 5, 10, 20 |
| `k` (multiple d'ATR) | 0,5 ; 1,0 ; 1,5 |
| Horizon de sortie | 1, 5, 15 et 60 minutes |

Soit 9 signaux et 4 horizons : 36 configurations, pas une de plus.

## Mesures

Pour chaque configuration : nombre de signaux, mouvement brut moyen dans le sens du signal, coût moyen,
résultat net moyen avec intervalle de confiance à 95 % (rééchantillonnage, 2 000 tirages, graine fixe), part
des signaux gagnants après coût.

| Hypothèse | Découpage |
| --- | --- |
| H1 continuation | Ensemble des signaux, et chaque moitié de la période |
| H2 contexte | Par nombre d'unités de temps alignées avec le signal (0 à 3) : M15 et H1 (EMA 20 contre EMA 50), H4 (EMA 50 contre EMA 200), calculées sur des bougies déjà clôturées |
| H3 heures | Par heure UTC de l'entrée |
| Annonces | Signaux à plus de 30 minutes de tout événement majeur du calendrier |

## Périodes

| Période | Dates | Usage |
| --- | --- | --- |
| Développement | 2023 et 2024 | Seule période regardée pour juger H1 à H3 |
| Validation | 2025 | Confirmation des configurations retenues, sans les modifier |
| Test final | 2026 | **Verrouillée.** Un seul passage, après gel complet, avec `--unlock-final-test` |

Si l'historique M1 du broker ne remonte pas à 2023, les dates seront révisées **avant** tout calcul, et le
changement noté ci-dessous.

## Critère de passage

Une configuration passe si, sur la période de développement :

- elle compte au moins 100 signaux ;
- son mouvement brut moyen vaut au moins 2 fois son coût moyen ;
- son mouvement brut moyen est positif dans chacune des deux moitiés, avec au moins 30 signaux par moitié.

Avec 36 configurations, quelques-unes peuvent passer par hasard. Une configuration n'est retenue que si elle
passe aussi, inchangée, sur la validation.

## Garde-fous

- Chaque exécution est inscrite dans `data/research/registry.jsonl`, réussie ou non.
- Le test final est refusé sans option explicite, et refusé une seconde fois.
- Les rapports restent dans `data/`, hors dépôt.
- Aucune statistique n'est publiée dans ce document tant qu'elle n'a pas été mesurée.

## Obtenir les bougies

1. Copier `mt5/Scripts/AMB_ExportBars.mq5` dans `MQL5\Scripts`, le compiler dans MetaEditor (F7).
2. Dans MT5, menu Outils, Options, Graphiques : mettre « Barres max dans le graphique » sur illimité, puis
   ouvrir un graphique XAUUSD en M1 et remonter dans le temps pour forcer le téléchargement de l'historique.
3. Glisser le script sur le graphique. Il écrit deux fichiers dans `Common\Files` :
   `amb_bars_XAUUSD_M1.csv` et `amb_bars_XAUUSD_M1.meta.csv`.
4. Les déposer tous les deux dans `data/inbox/bars/`. Ils ne sont pas versionnés.

Le script n'a pas été compilé par son auteur : en cas d'erreur de compilation, transmettre le message.
La profondeur de l'historique M1 dépend du broker ; le journal du script indique la première date obtenue.

## Lancer l'étude

```bash
python -m amb_research.impulse_study --bars data/inbox/bars/amb_bars_XAUUSD_M1.csv --store data/market_intelligence.sqlite --period development
```

## Ce que l'étude ne dit pas

- Elle mesure une sortie à horizon fixe, pas une sortie dynamique ni un stop.
- Elle ignore le slippage, la commission et les refus d'ordre.
- Un résultat positif n'est pas une stratégie : c'est l'autorisation de construire le prototype V1.

## Écarts

### Écart 1, 13 novembre 2026 : règle de conversion horaire imposée

Avant tout calcul de résultat. Calibrage automatique sur les 44 publications de l'emploi américain de
l'historique complet : meilleure règle `utc+2/+3_heure_ete_UE`, 88,6 % d'accord, donc refusée par le seuil de
90 %. Les 5 désaccords datent tous de 2025-2026 et donnent des écarts impossibles pour un serveur (-6 h,
-3,5 h, +2,5 h, et deux cas de ±1 h, jours de publication décalée ou autre événement plus ample) : la
détection par « bougie la plus ample » a pris une autre bougie. Sur la seule période de développement
(2023-2024), 23 publications : règle UE 23/23 (100 %), règle US 21/23 (91,3 %), UTC+2 fixe 43,5 %,
UTC+3 fixe 56,5 %. La règle est donc imposée avec `--offset-rule utc+2/+3_heure_ete_UE`. Seuls des
horodatages ont été examinés pour ce choix, aucune statistique de prix ni de performance. Le seuil de 90 %
et le reste du protocole ne changent pas.
