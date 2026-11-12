# AggressiveMonkeyBot — module Market Intelligence

Fondation de données du projet : collecte des données macroéconomiques réelles, stockage
« point-in-time » pour les backtests, et production d'un contexte horodaté et sourcé que le futur
Expert Advisor lira dans un fichier.

Ce module ne contient aucune stratégie de trading et aucune donnée fictive.
Code dans `src/market_intelligence/`, script MetaTrader dans `mt5/Scripts/`, tests dans `tests/`.

## Démarrage

```bash
pip install -e .
```

Cette commande installe le paquet `market_intelligence` depuis `src/` et sa dépendance `requests`.

```bash
python -m market_intelligence audit
```

```bash
python -m market_intelligence collect --since 2015-01-01
```

```bash
python -m market_intelligence context
```

Interroger le passé, par exemple le taux de chômage tel qu'il était connu le 17 décembre 2025 :

```bash
python -m market_intelligence asof UNRATE 2025-12-17
```

Reconstituer le contexte complet à un instant passé :

```bash
python -m market_intelligence context --asof 2025-12-10T18:45:00Z --print-only
```

Lancer les tests :

```bash
pip install -e ".[dev]"
```

```bash
python -m pytest -q
```

## Documentation

- `MONKEY_ENVIRONMENT.md` : règles du projet.
- `docs/SOURCES.md` : audit des sources, accès, coûts et limites.
- `docs/API_KEYS.md` : clés et comptes, et où les placer.
- `docs/ARCHITECTURE.md` : organisation, modèle de données, prévention du look-ahead, fichiers produits
  pour l'EA, comportement en cas de panne.

## État

| Élément | État |
| --- | --- |
| Connecteurs FRED / ALFRED, Fed, BLS, CFTC | Testés sur les sources réelles le 12 novembre 2026 |
| Stockage point-in-time et requêtes « tel que connu à » | Testé sur de vraies révisions |
| Génération du contexte, en direct et à une date passée | Testée |
| World Gold Council | Archivage des fichiers déposés à la main. Lecture des colonnes à écrire sur un vrai fichier |
| Calendrier MT5 (script MQL5 et import) | Écrit, non compilé et non testé : MetaTrader n'est pas disponible dans l'environnement cloud |
| Lecture du contexte par l'Expert Advisor | À écrire avec l'EA. Le format des fichiers est fixé |

Le dossier `data/` (base SQLite, réponses brutes archivées, contexte) n'est pas versionné. Il se
reconstruit avec la commande `collect`.
