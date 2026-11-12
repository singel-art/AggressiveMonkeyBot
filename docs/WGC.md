# Fichiers du World Gold Council

## Règles du projet

Décisions du 12 novembre 2026, tirées des conditions d'utilisation du WGC :

- **Aucune requête automatique vers gold.org.** Le connecteur `wgc` ne fait aucun appel réseau. Un test le
  vérifie.
- **Dépôt manuel.** Vous téléchargez les fichiers avec votre compte Goldhub, puis vous les déposez dans
  `data/inbox/wgc/`.
- **Aucune redistribution.** Les fichiers restent dans `data/`, exclu du dépôt. Les tests n'embarquent aucun
  extrait réel : ils fabriquent des classeurs de même mise en page, aux valeurs fictives.
- **Aucune version écrasée.** Chaque fichier déposé est archivé sous un nom qui contient son empreinte.
- **Aucun usage commercial** sans autorisation écrite du WGC et des fournisseurs tiers cités dans les
  classeurs (Bloomberg, ICE, Metals Focus, FMI, MCX, Shanghai Gold Exchange).

## Utilisation

```bash
python -m market_intelligence collect --only wgc
```

Chaque fichier de `data/inbox/wgc/` est archivé dans `data/raw/wgc/`. Les classeurs reconnus sont lus ; les
autres (PDF, autres classeurs) sont archivés sans lecture. Un classeur reconnu dont la mise en page a
changé est refusé : il reste dans le dossier de dépôt et rien n'est importé.

## Formats acceptés

Un classeur est reconnu par ses feuilles, pas par son nom de fichier. Format : `.xlsx`.

### Flux des ETF (`etf_flows`)

Feuilles requises : `Holdings by month`, `Demand by month`, `Fund flows by month`.

| Élément attendu | Position |
| --- | --- |
| Ligne 1 | Commence par `ticker` ; un identifiant de fonds par colonne à partir de la colonne F |
| Ligne 6 | `Date`, `Gold, US$/oz`, `Ounces`, `Tonnes`, `Value (USD)`, puis un nom de fonds par colonne |
| Lignes suivantes | Une ligne par fin de mois ; la colonne A est une date |

| Série produite | Origine |
| --- | --- |
| `WGC_ETF_HOLDINGS_TONNES` | Colonne `Tonnes` de `Holdings by month` |
| `WGC_ETF_HOLDINGS_USD` | Colonne `Value (USD)` de `Holdings by month` |
| `WGC_ETF_DEMAND_TONNES_SUM` | **Dérivée** : somme des colonnes de fonds de `Demand by month` |
| `WGC_ETF_FLOWS_USD_MN_SUM` | **Dérivée** : somme des colonnes de fonds de `Fund flows by month` |

### Achats et ventes des banques centrales (`cb_changes`)

Feuilles requises : `Monthly` et `Annual`.

| Élément attendu | Position |
| --- | --- |
| Ligne 1 de `Monthly` | `Country`, `Comments`, puis une date de début de mois par colonne |
| Lignes suivantes | Un pays par ligne ; valeur en tonnes, ou cellule vide en l'absence de donnée |

| Série produite | Origine |
| --- | --- |
| `WGC_CB_NET_TONNES_SUM` | **Dérivée** : somme des pays renseignés, par mois |
| `WGC_CB_REPORTING_COUNT` | **Dérivée** : nombre de pays renseignés, par mois |

Les derniers mois sont incomplets : sur le fichier de novembre 2026, 100 pays sont renseignés pour juillet,
80 pour août et 60 pour septembre. La somme de ces mois est provisoire ; le contexte le signale.

### Primes locales (`gold_premiums`)

Feuilles requises : `Chinese premiums-discounts`, `Indian premiums-discounts`.

| Élément attendu | Position |
| --- | --- |
| Ligne 4 | Titre commençant par `Chinese Premium/Discount (US$/oz)` ou `Indian Premium/Discount (US$/oz)` |
| À partir de la ligne 6 | Colonne A : date (cellule de date ou texte `AAAA-MM-JJ`). Colonne B : valeur |

Séries produites : `WGC_PREMIUM_CHINA_USD_OZ`, `WGC_PREMIUM_INDIA_USD_OZ`. Ce sont des moyennes mobiles sur
5 jours : la valeur d'un jour contient les quatre précédents.

### Règles communes

- Une cellule vide ou textuelle n'est jamais convertie en zéro.
- Les sommes ne portent que sur les cellules renseignées.
- Une date en double dans une série fait refuser le classeur.

## Classeurs archivés mais non lus

Prix moyens depuis 1978, tables Gold Demand Trends, production minière, stocks hors-sol, réserves
trimestrielles, classement des réserves, ventes CBGA. Leur fréquence annuelle ou trimestrielle les rend sans
usage pour le contexte actuel ; leur lecture peut être ajoutée sur demande.

## Disponibilité temporelle

Un classeur est un instantané : il ne dit pas quand chaque chiffre a été publié.

| Champ | Valeur |
| --- | --- |
| `vintage_date` | Date de dernière modification inscrite dans le classeur (date d'édition) |
| `available_at` | Instant où nous avons reçu le fichier. La date de publication réelle est inconnue et n'est pas inventée |
| `release_kind` | `backfill` pour tout le premier fichier ; `initial` ou `revision` pour les éditions suivantes |

Conséquence : aucune valeur WGC n'est visible dans une lecture antérieure à la réception du fichier. Ces
séries décrivent le fond du marché. Elles sont exposées dans la section `gold_market` du contexte et
n'entrent pas dans le biais macro. Un historique de versions ne se construit qu'en déposant chaque nouvelle
édition.

## État

| Élément | État |
| --- | --- |
| Lecture des trois classeurs | Testée hors réseau sur des classeurs fabriqués, et lue en local sur les vrais fichiers de l'archive du 12 novembre 2026 |
| Contrôle de cohérence | Sur les vrais fichiers, la somme des variations par fonds égale la variation du total des avoirs |
| Deuxième édition réelle | Non testée : une seule édition de chaque classeur est disponible |
