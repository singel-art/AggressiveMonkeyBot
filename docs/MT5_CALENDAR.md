# Calendrier économique MT5 : compilation, export et validation

Le calendrier intégré à MetaTrader 5 est la seule source gratuite du projet qui fournit l'heure exacte, la
prévision, la valeur publiée et la valeur précédente révisée.

## État réel

| Élément | État |
| --- | --- |
| Script `mt5/Scripts/AMB_ExportCalendar.mq5` (version 1.20) | Compilé et exécuté par l'utilisateur : un export réel a été reçu le 12 novembre 2026. Je n'ai pas assisté à la compilation |
| Lecteur Python `mt5_calendar` | Testé hors réseau, et essayé sur l'export réel : 42 510 lignes lues, aucune refusée |
| Export réel du 12 novembre 2026 | Importé en local. Il n'est pas versionné : il reste dans `data/` |

## Ce que l'export réel a montré

Export du 12 novembre 2026 : 42 510 lignes, du 1er janvier 2015 au 10 février 2027, terminal en français.

| Constat | Détail |
| --- | --- |
| Heures correctes | 90 communiqués du FOMC comparés aux heures publiées par la Fed : 90 identiques, été comme hiver |
| Noms traduits | Les événements portent des noms français. Ils sont donc reconnus par leur identifiant (`event_id`), jamais par leur nom |
| Dates de publication | Emploi : 141 sur 141 identiques à ALFRED. CPI : 140 communes, 1 côté ALFRED seulement (18 décembre 2025). PCE : 139 sur 139 |
| Heure de 8 h 30 fausse pour le PCE | 11 publications à 10 h, heure de New York. L'heure réelle remplace désormais l'estimation |
| 3 heures douteuses | Emploi du 20 novembre 2025, PCE des 26 janvier et 29 février 2024 : indiqués à 7 h 30. L'heure estimée, plus tardive, est conservée |
| Prévisions | Aucune avant mai 2017. Ensuite, environ 85 % des événements majeurs en ont une. Jamais pour la décision de taux de la Fed |
| Jours fériés | 116 jours fériés américains, utilisés pour la règle des jours ouvrés |
| Positions de la CFTC | 623 publications hebdomadaires pour l'or, avec leur heure réelle |

## Ce que l'import fait des heures réelles

- Les lignes des séries CPI, PCE, emploi et chômage publiées un jour couvert par le calendrier passent de
  l'heure estimée à l'heure réelle (`time_quality = published`), sans marge. Sur la base du 12 novembre 2026 :
  5 015 lignes sur 5 904.
- Les rapports de la CFTC sont appariés à leur publication par la valeur (position nette non commerciale,
  au dixième de millier de contrats près) : 613 rapports sur 618. Les 5 autres gardent la règle du samedi.
- Une heure réelle antérieure à l'estimation n'est jamais appliquée.
- Dans le contexte, la ligne de synthèse d'une publication est masquée quand le calendrier la détaille.

Après une reconstruction de la base, redéposer l'export dans `data/inbox/mt5_calendar/` : la commande
`collect` traite la Fed, puis le calendrier, puis FRED, dans cet ordre.

```bash
python -m market_intelligence verify
```

Cette commande recoupe les dates de publication d'ALFRED avec celles du calendrier.

## Vérifications statiques faites sans MetaTrader

- Accolades, parenthèses et crochets équilibrés.
- Seules les fonctions et les champs du calendrier relus dans la documentation MQL5 sont utilisés.
- Une valeur absente est toujours écrite vide, jamais remplacée par un nombre.
- L'en-tête écrit par le script est exactement celui que le lecteur attend.
- Corrections issues de la relecture : directive `#property strict` retirée (propre à MQL4) ; lecture par
  tranches d'un an au lieu d'une demande unique sur onze ans, qui risquait de dépasser le délai du
  calendrier ; export annulé si le terminal n'est pas connecté ; fichier partiel supprimé en cas d'échec.

Ces vérifications ne remplacent pas une compilation : une erreur de type ou de syntaxe propre à MQL5 peut
subsister.

## Points encore ouverts

1. Avertissements éventuels du compilateur : à me transmettre si MetaEditor en affiche.
2. Les 3 heures douteuses de 7 h 30 : à confirmer ou infirmer auprès d'une autre source.
3. Un export fait sur un terminal dans une autre langue ou chez un autre broker n'a pas été essayé.
4. Les prévisions exportées sont celles que le terminal conserve aujourd'hui ; rien ne garantit qu'elles
   n'ont pas été modifiées entre leur annonce et la publication.

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
| Heure des communiqués du FOMC (identifiant 840050002) comparée aux heures publiées par la Fed | Fichier refusé si un écart existe |

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
