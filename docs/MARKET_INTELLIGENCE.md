# AggressiveMonkeyBot — module Market Intelligence

Fondation de données du projet : collecte des données macroéconomiques réelles, stockage
« point-in-time » pour les backtests, et production d'un contexte horodaté et sourcé que le futur
Expert Advisor lira dans un fichier.

Ce module ne contient aucune stratégie de trading et aucune donnée fictive.
Code dans `src/market_intelligence/`, script MetaTrader dans `mt5/Scripts/`, tests dans `tests/`.

## Démarrage

```bash
pip install -r requirements.txt
```

```bash
pip install --no-deps -e .
```

La première commande installe les versions exactes testées, la seconde le paquet `market_intelligence`
depuis `src/`. Python 3.11 ou plus récent.

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
pip install -r requirements-dev.txt
```

```bash
python -m pytest -q
```

## Documentation

- `MONKEY_ENVIRONMENT.md` : règles du projet.
- `docs/MT5_CALENDAR.md` : compilation, export et validation du calendrier MT5.
- `docs/WGC.md` : formats acceptés, colonnes attendues et règles d'usage des fichiers WGC.
- `docs/SOURCES.md` : audit des sources, accès, coûts et limites.
- `docs/API_KEYS.md` : clés et comptes, et où les placer.
- `docs/ARCHITECTURE.md` : organisation, modèle de données, prévention du look-ahead, fichiers produits
  pour l'EA, comportement en cas de panne.

## État

| Élément | État |
| --- | --- |
| Connecteurs FRED / ALFRED, Fed, CFTC | Testés sur les sources réelles le 12 novembre 2026, avant et après la fiabilisation |
| Connecteur BLS | Sans clé : a fonctionné puis quota journalier atteint. Avec clé : validé en réel le 13 novembre 2026 (clé acceptée, 64 observations collectées). Recoupement FRED / BLS d'octobre 2026 : CPI (337,476), emploi (159 293) et chômage (4,1) concordent. Clé factice refusée, absence de clé = quota atteint |
| Stockage point-in-time : publications initiales, révisions, borne sûre | Testé sur de vraies révisions, avec des tests anti-look-ahead dédiés |
| Génération du contexte, en direct et à une date passée | Testée |
| World Gold Council | Lecture de trois classeurs déposés à la main, sans réseau. Testée sur des classeurs fabriqués et lue en local sur les vrais fichiers |
| Politique temporelle `balanced` (marge de 15 minutes) | Appliquée par défaut, testée avec `strict` et `estimate` |
| Bascule vers les rendements de la Fed | Testée hors réseau. Non observée en conditions réelles : FRED répondait |
| Calendrier MT5 | Export réel du 12 novembre 2026 lu sans ligne refusée ; heures recoupées avec la Fed. L'export n'est pas versionné |
| Heures réelles de publication | Appliquées aux séries macro et aux rapports de la CFTC à partir du calendrier |
| Biais macro | Calculé et marqué « non validé ». Ne doit peser dans aucune décision de trading |
| Lecture du contexte par l'Expert Advisor | À écrire avec l'EA. Le format des fichiers est fixé |

Le dossier `data/` (base SQLite, réponses brutes archivées, contexte) n'est pas versionné. Il se
reconstruit avec la commande `collect`.
