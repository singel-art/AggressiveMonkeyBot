# Audit des sources de données

Audit réalisé le 12 novembre 2026 depuis l'environnement cloud « Monkey », par des requêtes réelles.
Depuis la fiabilisation, la commande `audit` n'interroge plus Trading Economics ni CME : leur statut ci-dessous
est le constat du 12 novembre 2026.
Le rapport brut de la commande `audit` est dans `docs/audit_2026-11-12.json`.
Rejouer l'audit : `python -m market_intelligence audit`.

## Constat sur l'environnement

La liste d'hôtes autorisés ne fonctionne que par nom exact. Les domaines nus ajoutés au départ
(`federalreserve.gov`, `bls.gov`, `cftc.gov`, `gold.org`...) ne servent aucune donnée : ils redirigent
vers des sous-domaines qui étaient refusés par le proxy (HTTP 403). Les sous-domaines suivants ont été
ajoutés pendant l'audit, avec votre accord : `www.federalreserve.gov`, `api.bls.gov`, `www.bls.gov`,
`download.bls.gov`, `www.cftc.gov`, `publicreporting.cftc.gov`, `www.gold.org`, `www.cmegroup.com`,
`api.tradingeconomics.com`, `www.mql5.com`, `alfred.stlouisfed.org`.

`pip install` fonctionne dans cet environnement en passant le proxy explicitement :
`pip install --proxy "$HTTPS_PROXY" -r requirements.txt`.

## Tableau des sources

| Source | Données utiles | Accès constaté | Clé / coût | Limites | Usage dans le module |
| --- | --- | --- | --- | --- | --- |
| **FRED** `fred.stlouisfed.org/graph/fredgraph.csv` | Taux 10 ans, 2 ans, taux réel, inflation anticipée, fed funds, fourchette cible, indice large du dollar | OK, CSV | Aucune clé, gratuit | Dernière version seulement. Le site coupe les requêtes dont le `User-Agent` est personnalisé. Séries quotidiennes publiées avec un jour ouvré de retard, l'indice du dollar avec environ une semaine | Connecteur `fred`, séries « market » |
| **ALFRED** `alfred.stlouisfed.org` | Toutes les versions passées de CPI, CPI sous-jacent, PCE, PCE sous-jacent, emploi (NFP), chômage, avec la date de chaque version | OK, CSV, jusqu'à 12 versions par requête | Aucune clé, gratuit | Date de version sans heure. Ce n'est pas forcément une date de publication : le module classe chaque version en publication ou révision d'après son contenu | Connecteur `fred`, séries « macro » : base du point-in-time |
| **FRED, calendrier** `fred.stlouisfed.org/releases/calendar` | Dates et heures des prochaines publications (CPI, emploi, PCE, PIB) | OK, page HTML | Aucune clé | Quelques semaines d'avance seulement. Heure affichée en heure de Chicago | Connecteur `fred`, événements à venir |
| **FRED, API** `api.stlouisfed.org` | Mêmes données en JSON officiel, dates de publication, versions | Joignable, répond HTTP 400 sans clé | Clé gratuite | Quota par minute, à lire dans la documentation de l'API | Non utilisée : les accès sans clé couvrent le besoin actuel |
| **Federal Reserve** `www.federalreserve.gov` | Calendrier du FOMC (2021 à 2027), communiqués avec heure réelle, discours avec orateur, rendements H.15 | OK : HTML, JSON, CSV | Aucune clé, gratuit | Le calendrier ne remonte qu'à 2021 ; les communiqués datés couvrent l'historique antérieur. Les discours ne sont listés qu'une fois prononcés | Connecteur `fed` |
| **BLS, API** `api.bls.gov` | CPI, emploi, chômage, à la source | OK en version 2, sans clé | Clé gratuite recommandée | Sans clé, le quota journalier a été atteint le jour de l'audit après quelques requêtes : l'API a répondu « daily threshold reached ». Ni date de publication, ni versions passées | Connecteur `bls`, pour recouper FRED |
| **BLS, site** `www.bls.gov`, `download.bls.gov` | Calendrier officiel des publications, fichiers complets | Refusé : HTTP 403, « bot activity » | Sans objet | Le BLS bloque les robots qui ne suivent pas sa politique d'accès | Non utilisé |
| **CFTC** `publicreporting.cftc.gov` | Rapport hebdomadaire Commitments of Traders sur l'or du COMEX : positions des gérants, positions ouvertes | OK, API JSON | Aucune clé, gratuit | Hebdomadaire : positions du mardi publiées le vendredi | Connecteur `cftc` |
| **World Gold Council** `www.gold.org` | Réserves et achats des banques centrales, flux des ETF or | Pages publiques OK ; fichiers de données refusés (HTTP 403) sans connexion | Compte Goldhub gratuit | Téléchargement manuel. Conditions de réutilisation à lire avant toute diffusion | Connecteur `wgc` : archivage des fichiers déposés. Lecture des colonnes à écrire sur un vrai fichier |
| **Trading Economics** `api.tradingeconomics.com` | Calendrier avec prévisions | Refusé : HTTP 410, « the guest account has been discontinued » | Abonnement payant | Le site web est derrière une protection anti-robot | Non intégré |
| **CME Group** `www.cmegroup.com` | Probabilités FedWatch, données des contrats à terme | Refusé : HTTP 403, collecte automatique interdite par les conditions d'utilisation | Offres de données payantes | Aucune API gratuite | Non intégré. Substitut : rendement à 2 ans |
| **Calendrier MQL5** (terminal MT5) | Heure exacte, prévision, valeur publiée, précédente et révisée de chaque événement | Fonctions natives du terminal ; la page web `www.mql5.com/en/economic-calendar` est lisible mais n'est pas une API | Gratuit avec MT5 | Lisible uniquement depuis le terminal, et pas dans le Strategy Tester | Script `mt5/Scripts/AMB_ExportCalendar.mq5` + connecteur `mt5_calendar`. Script jamais compilé : voir `docs/MT5_CALENDAR.md` |
| **MetaTrader5** (paquet Python, PyPI) | Prix, ticks, historique des ordres depuis Python | Paquet présent sur PyPI pour Windows uniquement ; aucune version Linux | Gratuit | Exige un terminal MT5 installé sur la même machine Windows | À utiliser sur votre PC ou le VPS, pas dans l'environnement cloud |

## Ce que les sources gratuites ne fournissent pas

| Besoin | Situation | Piste |
| --- | --- | --- |
| Prévisions du consensus, avec historique | Aucune source gratuite accessible par programme, hors calendrier MT5 | Export du calendrier MT5. C'est la priorité pour tester l'effet des surprises économiques |
| Dollar et or en intrajournalier | FRED ne donne que du quotidien, avec retard | Prix du broker, exportés depuis MT5 |
| Flux des ETF et achats des banques centrales | Derrière un compte gratuit | Dépôt manuel mensuel des fichiers du World Gold Council |
| Probabilités de décision de la Fed | CME uniquement, collecte interdite | Rendement à 2 ans comme indicateur indirect |
| Calendrier officiel du BLS | Bloqué pour les robots | Calendrier FRED et calendrier MT5 |

Sources non testées car hors de la liste autorisée : Trésor américain (courbe des taux officielle),
BEA (PCE à la source), émetteurs d'ETF (avoirs quotidiens), FMI (réserves officielles).

## Données réellement collectées le 12 novembre 2026

- 20 séries rechargées après la fiabilisation, plus 3 séries BLS de contrôle lors de la première collecte.
- 6 séries macro avec leurs versions successives depuis 2015 : 5 904 lignes datées, dont 847 premières
  publications, 4 913 révisions et 144 valeurs d'historique de nature inconnue.
- Rendements, fourchette cible et dollar en quotidien depuis 2015 (36 189 lignes d'historique).
- Positions sur l'or du COMEX : 618 rapports hebdomadaires depuis 2015.
- 1 568 événements : 97 communiqués du FOMC (dont 3 hors calendrier) et 835 discours avec leur heure réelle,
  les 56 réunions du calendrier 2021-2027 (dont 9 à venir), 428 publications CPI, emploi, PCE et PIB, et
  10 révisions sans période nouvelle.
- Recoupement FRED / BLS sur octobre 2026, fait le matin avant l'épuisement du quota : CPI, emploi et
  chômage identiques.

Deux observations faites sur les données réelles, que le module traite sans rien inventer :

- Le CPI d'octobre 2025 n'existe pas dans la série. Le glissement annuel du CPI d'octobre 2026 est
  donc déclaré indisponible, au lieu d'être calculé sur un autre mois.
- Le taux de chômage de novembre 2025 a été publié à 4,6 % le 16 décembre 2025, puis révisé à 4,5 %
  le 9 janvier 2026. Les deux versions sont conservées et datées.
