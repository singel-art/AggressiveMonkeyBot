# Architecture du module Market Intelligence

## Principe

Python collecte, date, stocke et résume. MQL5 trade. Les deux ne se parlent que par des fichiers.
L'Expert Advisor ne fait aucune requête Internet et n'attend jamais Python : si le fichier de contexte
est absent ou périmé, il applique sa règle de prudence et continue de gérer ses positions.

```
 Sources publiques                Python : market_intelligence                      MetaTrader 5
 -----------------                ----------------------------                      ------------
 FRED / ALFRED  ─┐                                                                  
 Federal Reserve ├─► connecteurs ─► archive brute (data/raw, immuable)              
 BLS (API)       │        │                                                         
 CFTC            ┘        ▼                                                         
                    stockage point-in-time (SQLite)                                  
 Fichiers WGC ─► inbox ──►│                                                         
 Export MT5 ───► inbox ──►│◄──────────── AMB_ExportCalendar.mq5 (calendrier du terminal)
                          ▼                                                         
                    build_context(asof)                                              
                          │                                                         
                          ├─► context.json        (complet, pour l'analyse)          
                          ├─► context_mt5.csv ───────────────────────────────────►  Expert Advisor
                          └─► events_mt5.csv  ───────────────────────────────────►  (lecture seule)
```

## Organisation du code

| Fichier | Rôle |
| --- | --- |
| `src/market_intelligence/http.py` | Client HTTP commun : essais répétés, délai entre appels, archivage de chaque réponse, journal de chaque appel, secrets expurgés |
| `src/market_intelligence/store.py` | Stockage SQLite : observations versionnées, événements, journal des collectes, requêtes « tel que connu à l'instant t » |
| `src/market_intelligence/catalog.py` | Liste des séries suivies, avec leur règle de disponibilité et leur durée de validité |
| `src/market_intelligence/connectors/` | Un fichier par source : `fred`, `fed`, `bls`, `cftc`, `wgc`, `mt5_calendar` |
| `src/market_intelligence/context.py` | Construit le contexte à un instant donné et écrit les fichiers pour l'EA |
| `src/market_intelligence/cli.py` | Commandes `audit`, `collect`, `context`, `asof`, `status` |
| `mt5/Scripts/AMB_ExportCalendar.mq5` | Exporte le calendrier économique du terminal |
| `tests/` | 27 tests sur des réponses réelles enregistrées, sans appel réseau |

Ajouter une source revient à écrire une classe avec deux méthodes, `check()` et `collect(since)`, et à
l'inscrire dans `connectors/__init__.py`. La panne d'un connecteur n'arrête pas les autres.

## Modèle de données

**`observations`** : une ligne par version d'un chiffre. Clé : série, date d'observation, date de version.
Une ligne n'est jamais modifiée ni supprimée.

| Colonne | Sens |
| --- | --- |
| `obs_date` | Période mesurée (le jour, ou le 1er du mois pour une série mensuelle) |
| `vintage_date` | Date de la version |
| `available_at` | Instant UTC à partir duquel ce chiffre était connu. Toute requête de backtest filtre dessus |
| `available_basis` | Origine de cette date, voir ci-dessous |
| `fetch_id` | Renvoi vers l'appel qui a rapporté la donnée, donc vers le fichier brut archivé |

**`events`** : calendrier. `time_basis` vaut `published` quand l'heure vient de la source (communiqués,
discours, calendrier MT5) et `convention` quand elle est déduite de l'usage (14 h à New York pour une
décision du FOMC, 8 h 30 pour une statistique). `first_seen_at` n'est jamais réécrit.

**`fetch_log`** : chaque appel, réussi ou non, avec l'empreinte SHA-256 de la réponse et son chemin.

## Prévention du look-ahead

| `available_basis` | Séries | Comment la date est établie |
| --- | --- | --- |
| `vintage` | CPI, PCE, emploi, chômage | Date de publication fournie par ALFRED, à 8 h 30 heure de New York |
| `observed` | Toutes, en collecte régulière | Instant où notre collecteur a vu la valeur pour la première fois |
| `rule` | Historique quotidien récupéré après coup | Règle prudente : jour ouvré suivant à 18 h (New York) pour les taux, cinq jours ouvrés pour l'indice du dollar, samedi pour le rapport de la CFTC |

Trois règles s'y ajoutent :

- La première publication et chaque révision sont des lignes distinctes. `first_releases()` donne ce que
  le marché a vu le jour même, `revisions()` l'historique d'un chiffre.
- Le résultat d'un événement (`actual`) est masqué tant que son heure prévue n'est pas passée.
- Un instant sans fuseau horaire est refusé par le code.

Limites connues :

- L'heure de 8 h 30 est une convention. Une publication décalée serait datée trop tôt de quelques heures
  le jour même. L'import du calendrier MT5 corrigera ce point avec des heures réelles.
- Les jours fériés ne sont pas connus : la règle des taux quotidiens peut être trop tôt d'un jour ouvré
  autour d'un férié. La collecte régulière remplace cette règle par l'instant observé.
- Avant la première date de version chargée (1er janvier 2015 par défaut), aucune donnée macro n'est
  réputée connue.
- Les prévisions du consensus n'existent pas encore dans le stockage.
- En reconstitution historique, un discours apparaît comme événement à venir même si son annonce a été
  tardive. Son contenu, lui, n'est jamais utilisé.

## Fichiers produits pour l'Expert Advisor

`context_mt5.csv`, séparateur `;`, ASCII, colonnes `key;value;status;source` :

| Clé | Contenu |
| --- | --- |
| `generated_at_epoch`, `valid_until_epoch` | Heure de génération et limite de validité (30 minutes), en secondes Unix |
| `context_status` | `ok`, `degraded` ou `unavailable` |
| `risk_level` | `normal`, `elevated` (moins de 24 h avant un événement majeur), `high` (moins de 4 h), `blackout` (30 minutes avant ou après), `unknown` |
| `next_high_impact_epoch`, `_title`, `_time_basis` | Prochain événement majeur |
| `gold_macro_bias`, `_score`, `_validated` | `bullish`, `bearish`, `neutral` ou `unavailable` ; `_validated` vaut 0 |
| `us10y_*`, `us2y_*`, `real10y_*`, `broad_index_*` | Niveau et variations sur 5 et 20 observations |

`events_mt5.csv` liste les événements des sept prochains jours avec leur heure Unix. L'EA peut ainsi
calculer lui-même ses fenêtres de blocage à chaque tick, sans dépendre du rythme de Python.

Les fichiers sont écrits dans un fichier temporaire puis renommés : l'EA ne lit jamais un fichier partiel.

## Comportement attendu de l'Expert Advisor

| Situation | Décision |
| --- | --- |
| Fichier absent, illisible, ou heure courante au-delà de `valid_until_epoch` | Contexte indisponible : aucune nouvelle entrée. Les positions ouvertes restent gérées normalement |
| `risk_level` = `unknown` ou `blackout` | Aucune nouvelle entrée |
| Une ligne a un `status` différent de `ok` | Sa valeur est vide : la confirmation correspondante compte comme neutre |
| `gold_macro_bias_validated` = 0 | Le biais est journalisé mais ne pèse dans aucune décision |

## Pannes de sources

- Un appel en échec est réessayé trois fois, sauf refus explicite (400, 401, 403, 404, 410).
- Il est inscrit au journal et lève une erreur. Aucune valeur de remplacement n'est produite.
- Le stockage sert de cache : le contexte continue d'être construit avec les dernières valeurs, chacune
  avec son âge. Au-delà de sa durée de validité, une valeur passe à `stale` et sort des calculs.
- Le biais n'est publié que si deux composantes au moins sont exploitables.
- Si le calendrier n'a pas été rafraîchi depuis 48 heures, `risk_level` passe à `unknown`.
- Les rendements du H.15 de la Fed sont stockés en double des séries FRED, pour disposer d'une source
  de secours. La bascule automatique entre les deux reste à écrire.

## Le biais macro

Le biais additionne trois votes sur 20 observations : baisse du taux réel à 10 ans, baisse du dollar,
baisse du taux à 2 ans, chacune comptant pour un vote haussier sur l'or, et inversement. Deux votes
nets donnent `bullish` ou `bearish`.

C'est une lecture mécanique de relations admises. Les seuils ne sont pas calibrés et rien ne montre à
ce stade que ce biais améliore un résultat de trading. Il sera testé comme l'hypothèse H5 du document
d'analyse, sur la période de validation, avant de recevoir le moindre poids.

## Exploitation sur le PC ou le VPS

```bat
pip install -e .
python -m market_intelligence collect --since 2015-01-01
python -m market_intelligence context --out "%APPDATA%\MetaQuotes\Terminal\Common\Files\AMB"
```

Planifier ensuite, avec le Planificateur de tâches de Windows, la collecte une fois par heure et la
génération du contexte toutes les quinze minutes. La première collecte prend environ deux minutes, les
suivantes une vingtaine de secondes.

Calendrier MT5 : compiler `AMB_ExportCalendar.mq5` dans MetaEditor, le lancer sur un graphique, copier
le fichier `Common\Files\amb_calendar_USD.csv` dans `data\inbox\mt5_calendar\`, puis relancer `collect`.
Contrôle à faire sur le premier export : les lignes « Nonfarm Payrolls » doivent indiquer 12:30 ou
13:30 dans la colonne `time_utc`. Une autre heure signale un décalage horaire à corriger.
