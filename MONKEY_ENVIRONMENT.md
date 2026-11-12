# AggressiveMonkeyBot — environnement Monkey

## 1. Identité et objectif

AggressiveMonkeyBot est un projet de recherche et de développement d'un robot de trading algorithmique sur XAUUSD (or) pour MetaTrader 5.

- Exécution du trading : Expert Advisor natif en MQL5 dans MetaTrader 5.
- Recherche quantitative, collecte et préparation des données : Python.
- Environnement Monkey : développement, analyse, tests et automatisation des tâches de recherche.
- Déploiement ultérieur : terminal MT5 sur un VPS Windows adapté. Ne pas confondre l'environnement cloud Monkey avec le terminal MT5 d'exécution.

Le dépôt GitHub est privé. Ne jamais publier de données de compte, identifiants, mots de passe, clés API, fichiers de configuration contenant des secrets ou historiques personnels.

## 2. Principes stratégiques non négociables

- Instrument initial : XAUUSD uniquement.
- Entrées rapides principalement sur M1, avec confirmations possibles sur M15, H1 et H4.
- Stratégie directionnelle : BUY dans un contexte haussier confirmé, SELL dans un contexte baissier confirmé.
- Une seule position ouverte à la fois.
- Environ une opportunité par heure pendant les périodes actives est un objectif indicatif, jamais un quota. Ne jamais forcer une entrée.
- Sorties dynamiques : surveiller la progression favorable, la structure, la volatilité et la perte réelle de momentum.
- Une position gagnante peut être conservée plusieurs heures ou toute la journée si la structure et le contexte le justifient.
- Distinguer un retracement normal du bruit de marché d'un véritable affaiblissement.
- Le contexte macroéconomique sert à contextualiser les marchés et à filtrer les périodes risquées ; il ne doit pas être présenté comme un signal M1 autonome sans validation.
- Interdictions : martingale, grille, multiplication des positions, augmentation de taille pour récupérer une perte, élargissement du risque initial.
- Le stop initial doit être défini techniquement. Le risque ne doit jamais être augmenté après l'entrée.

Ces principes décrivent l'intention du projet, pas une stratégie déjà validée ou rentable.

## 3. Architecture attendue

Séparer clairement les composants :

1. `research/` : exploration, hypothèses, notebooks et analyses Python.
2. `data/` : données brutes, intermédiaires et nettoyées, avec provenance et horodatages. Ne pas versionner de gros fichiers de données ou de données privées sans nécessité.
3. `src/` : code Python réutilisable pour collecte, normalisation, stockage et analyse.
4. `mt5/` : code MQL5, Expert Advisors, scripts d'export et outils MetaTrader.
5. `tests/` : tests unitaires, intégration, qualité des données et tests temporels.
6. `docs/` : architecture, sources, décisions, procédures et résultats.
7. `config/` : exemples de configuration sans secrets. Les vraies valeurs sensibles doivent être injectées par variables d'environnement ou gestionnaire de secrets.

Ne pas créer artificiellement toute cette structure si le projet existant n'a pas encore été audité. Commencer par inventorier les fichiers existants et proposer un plan avant toute refonte.

## 4. Environnement Python

- Utiliser une version Python stable compatible avec les dépendances retenues.
- Isoler les dépendances dans un environnement virtuel lorsque l'environnement le permet.
- Épingler les versions dans un fichier de dépendances après validation.
- Préférer les bibliothèques maintenues et minimiser les dépendances.
- Dépendances potentielles à évaluer selon les besoins : `requests`, `pandas`, `numpy`, `pytest`, et une bibliothèque de stockage telle que `SQLAlchemy` si justifiée.
- Ne pas installer de paquet sans expliquer son utilité.
- Ajouter des tests et une procédure de reproduction des résultats.

## 5. Accès réseau — domaines à prévoir

Vérifier l'accès nécessaire au cas par cas dans les paramètres réseau Monkey. Ne pas supposer qu'un domaine est accessible simplement parce que son domaine parent est autorisé. N'ajouter que les hôtes réellement utilisés.

Sources publiques envisagées :

- FRED / ALFRED : `fred.stlouisfed.org`, `api.stlouisfed.org` si l'API est utilisée.
- Federal Reserve : `www.federalreserve.gov`.
- BLS : `www.bls.gov`, `api.bls.gov`.
- CFTC : `www.cftc.gov`.
- World Gold Council : `www.gold.org`.

Hôtes supplémentaires réellement utilisés par le module Market Intelligence (constat du 12 novembre 2026) :

- `alfred.stlouisfed.org` : versions datées des séries FRED (ALFRED). Indispensable au point-in-time.
- `publicreporting.cftc.gov` : API publique de la CFTC (rapport Commitments of Traders). `www.cftc.gov` n'est pas utilisé par le code.

Les domaines nus (`federalreserve.gov`, `bls.gov`, `cftc.gov`, `gold.org`) ne servent aucune donnée : seuls les sous-domaines exacts comptent. Détail dans `docs/SOURCES.md`.

Développement et dépendances :

- GitHub : `github.com`, `api.github.com`.
- Python Package Index : `pypi.org`, `files.pythonhosted.org`.

Ces domaines sont une liste de référence, pas une instruction d'ouvrir largement le réseau. Les gestionnaires de paquets doivent rester activés si les dépendances Python sont installées depuis PyPI.

MetaTrader 5 : le calendrier intégré et les données du terminal sont accessibles depuis le terminal MT5 local/VPS selon les fonctionnalités du courtier. L'environnement Monkey ne doit pas être supposé avoir accès au terminal MT5 ni à ses fichiers locaux.

## 6. Secrets et variables d'environnement

- Ne jamais écrire une clé API, un mot de passe, un token GitHub ou des identifiants de courtier dans le code, le dépôt, les notebooks, les logs ou ce document.
- Utiliser la section « Secrets réseau » de Monkey pour les secrets nécessaires aux connexions réseau, et les variables d'environnement pour la configuration non sensible.
- Créer un fichier `.env.example` avec uniquement des noms de variables et des valeurs fictives si le projet en a besoin.
- Ajouter `.env`, clés privées, fichiers de comptes, exports sensibles et données personnelles au `.gitignore`.
- Ne jamais demander ni stocker les identifiants de connexion au compte de trading dans les outils de recherche.
- Les clés API gratuites ou payantes ne doivent être ajoutées qu'après validation de la source, de ses conditions d'utilisation et du besoin réel.

## 7. Qualité des données macroéconomiques — règle essentielle

Toute donnée utilisée pour une analyse historique doit être disponible à la date simulée.

Pour chaque observation ou événement, conserver si possible :
- la période économique concernée ;
- la date et l'heure de publication réelle ;
- la date et l'heure à laquelle l'information est devenue accessible ;
- la valeur initialement publiée ;
- les révisions ultérieures, chacune avec son propre horodatage de disponibilité ;
- la source, l'URL ou l'identifiant de série ;
- le fuseau horaire et la qualité de l'horodatage.

Ne pas confondre date de période, date de publication, date de récupération et date de révision. Ne pas utiliser une valeur révisée dans un backtest antérieur à sa disponibilité. Séparer les publications économiques des mises à jour/révisions de séries. Toute heure estimée doit être explicitement marquée comme estimée et ne doit pas être présentée comme une heure officielle.

Sources envisagées : FRED/ALFRED, Federal Reserve, BLS, CFTC, World Gold Council et calendrier économique MT5. Respecter les limites d'accès, licences et conditions d'utilisation de chaque source. Ne pas contourner les restrictions d'un site.

## 8. Backtests et validation

Aucune stratégie ne doit être déclarée performante sur la base d'un seul backtest.

- Utiliser des données de qualité suffisante, idéalement des ticks réalistes pour le scalping.
- Inclure spread variable, commissions, swaps si pertinents, slippage et latence.
- Séparer strictement développement, validation et période finale hors échantillon.
- Éviter le look-ahead bias, le survivorship bias et toute fuite temporelle.
- Tester les changements de régime et plusieurs périodes de marché.
- Comparer au minimum : technique seule ; technique + filtre macro ; technique + macro + confirmation multi-timeframe.
- Rapporter nombre de trades, profit factor, espérance nette, drawdown, taux de réussite, ratio gain/perte, exposition et sensibilité aux coûts.
- Conserver les paramètres, versions de données, commits et configurations exactes de chaque test.
- Ne jamais inventer de résultats. Distinguer résultats mesurés, hypothèses et estimations.
- Un résultat historique ne garantit pas les performances futures.

## 9. Gestion du risque et sécurité d'exécution

Avant toute exécution réelle, spécifier et tester :
- risque maximal par position ;
- perte maximale journalière ;
- drawdown maximal ;
- limites de spread et de slippage ;
- nombre maximal de trades et de pertes consécutives ;
- comportement en cas de données manquantes, connexion perdue, cotations obsolètes ou redémarrage ;
- arrêt d'urgence manuel et automatique ;
- reprise après incident sans ordre dupliqué.

La taille de position doit être calculée à partir du stop technique et du risque autorisé. Ne jamais déplacer un stop pour augmenter le risque ni conserver une position perdante sans invalidation définie.

Aucun passage en réel sans validation explicite de l'utilisateur.

## 10. Méthode de travail de l'agent

À chaque session :

1. Lire ce document et le README s'il existe.
2. Examiner l'état réel du dépôt, les branches, les fichiers et les tests avant de modifier quoi que ce soit.
3. Distinguer ce qui existe réellement de ce qui est seulement prévu.
4. Ne pas prétendre avoir exécuté un test, téléchargé une donnée ou vérifié une source sans preuve.
5. Proposer un plan court et ordonné avant une modification importante.
6. Faire des changements petits, ciblés et réversibles.
7. Ne jamais écraser ou supprimer un fichier existant sans inspection et justification.
8. Ajouter ou mettre à jour les tests associés à chaque changement.
9. Résumer les fichiers modifiés, commandes exécutées, résultats observés et limites restantes.
10. Ne jamais lancer de trading réel ni modifier un compte ou un VPS sans autorisation explicite.

## 11. État initial du projet

Le dépôt GitHub vient d'être créé. Son contenu doit être inspecté avant de conclure que le code, les connecteurs ou les tests existent.

La première tâche est un audit de l'environnement et des fichiers disponibles, puis un plan de travail priorisé. Le bot de scalping lui-même n'est pas considéré comme développé ni validé à ce stade.
