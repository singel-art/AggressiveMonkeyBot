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
| `tests/` | 84 tests sur des réponses réelles enregistrées, sans appel réseau |
| `.github/workflows/tests.yml` | Intégration continue : tests sous Linux et Windows, Python 3.11 à 3.13 |

Ajouter une source revient à écrire une classe avec deux méthodes, `check()` et `collect(since)`, et à
l'inscrire dans `connectors/__init__.py`. La panne d'un connecteur n'arrête pas les autres.

## Modèle de données

**`observations`** : une ligne par version d'un chiffre. Clé : série, période, date de version.
Une ligne n'est jamais modifiée ni supprimée. Une ligne incohérente est refusée avec une erreur.

Quatre notions sont tenues séparées :

| Colonne | Sens |
| --- | --- |
| `obs_date` | **Période économique** mesurée : le jour, ou le 1er du mois pour une série mensuelle |
| `vintage_date` | **Date de la version** dans la source |
| `available_at` | **Meilleure estimation** de l'instant où le chiffre a été connu |
| `available_at_safe` | **Borne sûre** : instant à partir duquel un backtest a le droit de voir le chiffre |

Trois colonnes qualifient ces dates :

| Colonne | Valeurs |
| --- | --- |
| `available_basis` | `vintage` (date de version ALFRED), `observed` (instant où notre collecteur a vu la valeur), `rule` (règle prudente appliquée à un historique chargé après coup) |
| `time_quality` | `observed` (instant mesuré) ou `estimated` (heure supposée) |
| `release_kind` | `initial` (première valeur publiée pour la période), `revision` (valeur modifiée par une version ultérieure), `backfill` (valeur déjà ancienne au premier chargement : on ignore si elle est initiale ou révisée) |

**`events`** : calendrier. `time_basis` vaut `published` quand l'heure vient de la source (communiqués et
discours de la Fed, calendrier MT5 en mode « heure exacte ») et `estimated` dans tous les autres cas : 14 h à
New York pour une décision du FOMC à venir, 8 h 30 pour une statistique. Une heure publiée n'est jamais
remplacée par une estimation. `advance_notice` vaut `scheduled` ou `unscheduled`. `first_seen_at` n'est
jamais réécrit.

**`fetch_log`** : chaque appel, réussi ou non, avec l'empreinte SHA-256 de la réponse et son chemin.

Une base créée avant ce schéma est refusée au démarrage, avec la consigne de la reconstruire.

## Prévention du look-ahead

### Publications et révisions

- La valeur initiale et chaque révision sont des lignes distinctes. `first_releases()` ne renvoie que les
  lignes `initial` ; `revisions()` donne l'historique complet d'une période.
- La toute première version chargée d'une série est marquée `backfill` : elle contient des périodes déjà
  publiées, dont la valeur d'origine est inconnue. Elle n'est jamais présentée comme une première publication.
- **Une date de version ALFRED n'est pas assimilée à une publication.** C'est le jour où FRED a enregistré la
  version. Elle est classée d'après son contenu : si elle apporte une période nouvelle, c'est un événement
  `data_release` ; si elle ne fait que modifier le passé, c'est un événement `data_revision` de faible
  importance. Sur les données réelles depuis 2015, 10 versions sont dans ce second cas (8 pour le CPI, dont
  la révision annuelle de février, 1 pour l'emploi, 1 pour le PCE).

### Heures estimées

L'heure d'une version ALFRED (8 h 30 à New York) est une estimation non vérifiée. Deux lectures existent :

| Lecture | Règle | Usage |
| --- | --- | --- |
| Prudente, par défaut | Le chiffre n'est visible qu'à `available_at_safe`, soit la fin du jour de la version à New York | Backtests, contexte |
| Estimée, sur demande (`safe=False`, option `--estimated`) | Le chiffre est visible à l'heure supposée | Analyse seulement |

Conséquence assumée : le jour d'une publication, le contexte garde l'ancien chiffre jusqu'au lendemain.
C'est le prix à payer tant que les heures réelles ne sont pas importées du calendrier MT5.

### Événements

- Le résultat d'un événement (`actual`, `revised_previous`) est masqué tant que son heure prévue n'est pas
  passée. La prévision reste visible.
- Un événement non annoncé à l'avance n'apparaît pas avant d'avoir eu lieu. C'est le cas des discours (le
  flux de la Fed ne les liste qu'une fois prononcés) et des communiqués du FOMC publiés hors de l'heure
  habituelle (3 décisions d'urgence depuis 2015).
- Un instant sans fuseau horaire est refusé par le code.

### Règles de disponibilité de l'historique quotidien

| Série | Règle appliquée à l'historique chargé après coup |
| --- | --- |
| Rendements, taux réel, fed funds | Jour ouvré suivant à 18 h (New York) |
| Indice large du dollar | Cinq jours ouvrés plus tard |
| Fourchette cible de la Fed | Le jour même à 0 h (New York) |
| Rapport de la CFTC (positions du mardi) | Samedi suivant à 15 h 30 (New York) |
| BLS | Instant de notre collecte : jamais utilisable avant |

En collecte régulière, ces règles sont remplacées par l'instant réellement observé.

### Limites connues

- L'heure de 8 h 30 reste une estimation pour les événements du calendrier. Si une publication a eu lieu à
  une autre heure, la fenêtre de blocage autour de l'annonce est mal placée. Un seul recoupement a été fait :
  le CPI du 10 novembre 2026 figure à 13 h 30 UTC sur le calendrier public MQL5, comme l'estimation.
- Une date de version ALFRED peut être postérieure à la publication officielle. L'erreur va alors dans le
  sens prudent pour les valeurs, mais place l'événement au mauvais jour.
- Les jours fériés ne sont pas connus : la règle des taux quotidiens peut être trop tôt d'un jour ouvré
  autour d'un férié.
- L'historique quotidien chargé après coup (36 189 lignes) est la version actuelle des séries. Une révision
  ancienne d'un taux ou de l'indice du dollar serait invisible.
- Avant la première version chargée (1er janvier 2015 par défaut), aucune donnée macro n'est réputée connue,
  et les 24 premières périodes de chaque série macro n'ont pas de valeur initiale établie.
- Les prévisions du consensus n'existent pas encore dans le stockage.
- Une réunion du FOMC ajoutée au calendrier en cours d'année apparaîtrait comme connue à l'avance.

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
- Une réponse de refus (quota, clé invalide) est distinguée d'une panne et signalée telle quelle.
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

Calendrier MT5 : la procédure de compilation, d'export et de validation est dans `docs/MT5_CALENDAR.md`.
