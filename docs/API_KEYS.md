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

Le chemin de code qui envoie `BLS_API_KEY` n'a pas pu être testé, faute de clé.

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
- `.gitignore` exclut `data/` et `.env`.
