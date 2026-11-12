# Calendrier économique MT5 : compilation, export et validation

Le calendrier intégré à MetaTrader 5 est la seule source gratuite du projet qui fournit l'heure exacte, la
prévision, la valeur publiée et la valeur précédente révisée.

## État réel

| Élément | État |
| --- | --- |
| Script `mt5/Scripts/AMB_ExportCalendar.mq5` | Écrit et relu contre la documentation MQL5. **Jamais compilé ni exécuté** : aucun terminal MT5 n'est disponible dans l'environnement cloud |
| Lecteur Python `mt5_calendar` | Testé hors réseau sur des lignes au format de l'export. **Jamais essayé sur un export réel** |
| Compilation, exécution, premier export | **À faire par vous**, sur votre PC |

## Étapes à réaliser sur votre PC

1. Copier `mt5/Scripts/AMB_ExportCalendar.mq5` dans le dossier `MQL5\Scripts` du terminal
   (menu Fichier, Ouvrir le dossier des données).
2. Ouvrir le fichier dans MetaEditor et compiler (F7). Résultat attendu : 0 erreur.
   En cas d'erreur ou d'avertissement, me transmettre le message exact et le numéro de ligne.
3. Dans MT5, glisser le script sur un graphique. Paramètres par défaut : début 2015, 90 jours à venir,
   devise USD.
4. Lire l'onglet Experts : une ligne « AMB_ExportCalendar : N lignes écrites… » doit apparaître, avec
   l'écart serveur-UTC appliqué.
5. Récupérer `amb_calendar_USD.csv` dans le dossier commun (`…\MetaQuotes\Terminal\Common\Files`).
6. Le copier dans `data\inbox\mt5_calendar\`, puis lancer :

```bash
python -m market_intelligence collect --only fed,mt5_calendar
```

Le connecteur `fed` doit tourner avant : il fournit les heures de référence du contrôle ci-dessous.

## Contrôles automatiques à l'import

| Contrôle | Effet en cas d'échec |
| --- | --- |
| En-tête identique aux 15 colonnes attendues | Fichier refusé |
| Identifiants numériques et uniques, dates lisibles, nombres valides | Fichier refusé, avec le numéro de ligne |
| Cohérence entre `time_utc`, `time_server` et `utc_offset_s` | Fichier refusé |
| Heure des décisions de la Fed comparée aux communiqués datés de la Fed | Fichier refusé si un écart existe |

Un fichier refusé reste dans `data\inbox\mt5_calendar\` et rien n'est importé.

Le dernier contrôle vise un risque précis : le script convertit toutes les dates avec l'écart serveur-UTC
du jour de l'export. Si le serveur du broker change d'heure en été, les dates de l'autre saison sont
décalées d'une heure. Le contrôle le détecte ; la correction reste à écrire une fois le cas observé.

## Contrôles manuels sur le premier export

- Les lignes « Nonfarm Payrolls » doivent indiquer 12:30 ou 13:30 dans `time_utc`.
- La ligne « CPI m/m » du 10 novembre 2026 doit indiquer 13:30, valeur 0,4, prévision 0,5, précédent 0,6
  (valeurs affichées sur le calendrier public MQL5 le 12 novembre 2026).
- La colonne `time_mode` doit valoir `CALENDAR_TIMEMODE_DATETIME` pour les statistiques majeures.

## Colonnes de l'export

`value_id;event_id;time_utc;time_server;utc_offset_s;time_mode;currency;importance;name;actual;forecast;previous;revised_previous;unit;impact`

Une valeur absente est écrite vide et reste absente à l'import. Une heure n'est marquée `published` que si
`time_mode` indique une heure exacte ; sinon elle est `estimated`.

## Limites

- Le calendrier n'est pas disponible dans le Strategy Tester : c'est la raison de cet export.
- Les prévisions exportées sont celles que le terminal conserve aujourd'hui. Rien ne garantit qu'une
  prévision n'a pas été modifiée entre son annonce et la publication.
- Le nom « Fed Interest Rate Decision » utilisé pour le recoupement vient du calendrier public MQL5. S'il
  diffère dans votre terminal, le contrôle signalera « heures non recoupées ».
