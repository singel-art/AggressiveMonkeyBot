# Couche technique

Code : `src/amb_research/technical.py`. Tests : `tests/test_technical.py`.

Elle calcule, pour chaque bougie M1, ce que le marché montre à cet instant sur plusieurs unités de temps.
Elle **mesure** ; elle ne contient aucune règle d'entrée ou de sortie et ne dit pas si une mesure est utile.
Toute règle qui s'en servira devra être écrite et figée dans un protocole avant d'être mesurée.

## Règle anti-triche

La valeur d'une mesure à la bougie M1 `i` n'utilise que ce qui est connu à la clôture de cette bougie :

- une bougie M5, M15, H1 ou H4 n'est utilisée qu'une fois terminée ;
- un sommet ou un creux n'apparaît qu'une fois confirmé par les 2 bougies qui le suivent ;
- le range asiatique n'est connu qu'à l'ouverture de Londres.

Un test coupe l'historique à plusieurs endroits et vérifie qu'aucune mesure passée ne change.

## Mesures

| Famille | Mesures | Détail |
| --- | --- | --- |
| Tendance | `trend_m15`, `trend_h1`, `trend_h4`, `trend_strength_*`, `trend_sum` | Signe de EMA rapide − EMA lente (20/50 en M15 et H1, 50/200 en H4) ; force en ATR de l'unité ; somme de −3 à +3 |
| Structure | `structure_m5/m15/h1`, `swing_high_*`, `swing_low_*` | Dernier sommet et creux confirmés ; +1 depuis la dernière clôture au-dessus du sommet, −1 sous le creux |
| Volatilité | `atr_m1`, `atr_m15`, `vol_ratio` | `vol_ratio` : ATR M15 rapporté à sa moyenne sur 20 jours (1 = habituel) |
| Spread | `spread`, `spread_ratio` | Spread de la bougie rapporté à sa moyenne sur 1 jour |
| Momentum | `rsi_m5`, `move_5`, `move_15` | RSI 14 de Wilder en M5 ; variation sur 5 et 15 bougies M1, en ATR M1 |
| Session | `session` | Asie, Londres, Londres+New York, New York (08h00-17h00 heure locale, changements d'heure compris) |
| Niveaux | `prev_day_high/low/close`, `asia_high/low` | Journée de trading précédente (bascule à 17h00 de New York) ; range avant l'ouverture de Londres |
| Annonces | `minutes_to_event`, `minutes_since_event` | Annonces majeures du calendrier MT5 importé dans la base |

## Voir les mesures à un instant passé

```bash
python -m amb_research.technical --bars data/inbox/bars/amb_bars_XAUUSD_M1.csv --offset-rule "utc+2/+3_heure_ete_UE" --store data/market_intelligence.sqlite --at 2024-06-12T12:35
```

La période 2026 (test final) est refusée.

## Limites

- Les bougies supérieures sont reconstruites à partir du M1 et alignées sur l'heure UTC. Les H4 peuvent
  différer de celles du graphique MT5, alignées sur l'heure du serveur.
- Les paramètres (périodes des EMA, largeur des sommets, fenêtres) sont des choix courants, pas des valeurs
  optimisées. Ils ne doivent pas être ajustés après avoir vu des résultats.
- Vérifié sur des bougies fabriquées (19 tests) et lancé une fois sur l'export réel pour un instant de 2024.
  Les valeurs n'ont pas été comparées à celles des indicateurs de MetaTrader.
- Le futur robot devra recalculer les mêmes mesures en MQL5. Cette version MQL5 n'existe pas.
