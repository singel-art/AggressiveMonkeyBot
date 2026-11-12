# Clés API et comptes

Aucune clé n'est nécessaire pour collecter les taux, l'inflation, l'emploi, le calendrier de la Fed et les
positions sur l'or. Seul le recoupement avec le BLS gagne à en avoir une.
Aucune clé ne doit apparaître dans le code, dans un fichier du projet ou dans une conversation.

| Clé ou compte | Statut | Coût | À quoi elle sert | Où la mettre |
| --- | --- | --- | --- | --- |
| `BLS_API_KEY` | Recommandée | Gratuite, sur inscription auprès du BLS | Quota journalier plus large sur l'API du BLS. Sans clé, le quota a été épuisé le jour de l'audit après quelques requêtes | Variable d'environnement |
| `FRED_API_KEY` | Prévue, pas encore utilisée par le code | Gratuite, sur inscription auprès de la Fed de Saint-Louis | Accès à l'API officielle de FRED si les accès sans clé venaient à fermer | Variable d'environnement |
| Compte Goldhub (World Gold Council) | Nécessaire pour les fichiers ETF et banques centrales | Gratuit | Télécharger les fichiers à la main, puis les déposer dans `data/inbox/wgc/` | Aucun identifiant dans le projet |
| Trading Economics | Non intégré | Abonnement payant | Calendrier avec prévisions | Sans objet |
| CME Group | Non intégré | Offres payantes | Probabilités FedWatch | Sans objet |

Aucune requête réelle n'a été faite avec une clé BLS valide : aucune n'a encore été fournie. Dans
l'environnement cloud, la clé ne peut pas être injectée par les « secrets réseau », car le BLS l'attend dans le
corps de la requête et non dans un en-tête : le test réel se fera sur votre PC, avec le fichier `.env`. Le chemin avec clé est
couvert par des tests hors réseau (clé envoyée dans le corps de la requête, jamais dans l'URL ; refus pour clé
invalide ou quota ; clé masquée dans les messages et dans l'archive). La réponse réelle du BLS à une clé
invalide a été enregistrée pour ces tests.

## Fichier `.env`

Copier `.env.example` sous le nom `.env` à la racine du dépôt et y placer les valeurs. Ce fichier est exclu
du dépôt. Les variables d'environnement du système priment sur lui.

## Définir une variable d'environnement

Sous Windows (PC ou VPS), dans une invite de commandes, puis rouvrir le terminal :

```bat
setx BLS_API_KEY "votre_cle"
```

Sous Linux ou macOS, pour la session en cours :

```bash
export BLS_API_KEY="votre_cle"
```

## Protections en place

- `src/market_intelligence/config.py` lit les clés dans l'environnement, jamais dans un fichier du projet.
- Le journal des collectes et les noms de fichiers archivés sont expurgés des paramètres secrets
  (`api_key`, `registrationkey`, `token`...). Un test vérifie ce point.
- `.gitignore` exclut `data/`, `.env` et les fichiers de clés.
- Le BLS recopie la clé dans certaines réponses d'erreur : elle est masquée avant tout affichage et avant
  l'archivage de la réponse.
