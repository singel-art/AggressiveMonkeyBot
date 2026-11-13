# AggressiveMonkeyBot
Bot de trading algorithmique sur XAUUSD avec MT5. Combine scalping M1, analyse technique multi-timeframe, contexte macroéconomique, gestion dynamique des positions et du risque. Développé en MQL5 et Python, avec backtests rigoureux et validation des stratégies.

## État du dépôt

- `MONKEY_ENVIRONMENT.md` : règles et environnement du projet.
- `docs/MARKET_INTELLIGENCE.md` : module de collecte des données macroéconomiques (Python), avec son état réel.
- `docs/research/H1_H3_PROTOCOL.md` : protocole de l'étude statistique des impulsions M1, figé avant toute donnée.
- `docs/research/H1_H3_PROTOCOL.md` : étude des impulsions M1. Résultat sur 2023-2024 : 0 configuration sur 36 ne passe le critère ; cette entrée est écartée.
- `docs/TECHNICAL_LAYER.md` : couche technique (tendance, structure, volatilité, momentum, sessions, niveaux). Elle mesure, sans règle de trading.
- Le robot de trading (Expert Advisor MQL5) n'est pas encore développé.
