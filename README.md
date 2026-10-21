# edbot — installation

Bot Telegram qui surveille un compte parent École Directe :

- **automatique**, toutes les 5 minutes : nouvelles notes, changement de moyenne générale, nouveaux messages de l'école (transférés par mail, facultatif) ;
- **à la demande** : `/notes`, `/moyennes`, `/recent`, `/check` (vérifier tout de suite), `/sync` (télécharger les documents : bulletins, factures… rangés par enfant, catégorie et année).

Python standard, aucune dépendance, aucun port ouvert. API utilisée : celle, non officielle, du site École Directe (voir la doc d'API jointe). Usage personnel, sur son propre compte.

## Prérequis

- Une machine allumée en permanence avec Docker et Docker Compose (NAS, Raspberry Pi, serveur…).
- Un compte Telegram.
- Facultatif : un compte Gmail pour le transfert des messages.

## 1. Créer le bot Telegram

1. Dans Telegram, ouvrir **@BotFather**, envoyer `/newbot`, choisir un nom.
2. Noter le **jeton** fourni (`123456789:AA…`).
3. Ouvrir la conversation avec le nouveau bot et lui envoyer `/start` (il ne répondra pas encore).
4. Récupérer son **id de chat** : ouvrir dans un navigateur
   `https://api.telegram.org/bot<JETON>/getUpdates`
   et relever le nombre dans `"chat":{"id":…}`. Pour un groupe, ajouter le bot au groupe, y écrire un message, recharger la page (l'id d'un groupe est négatif).

Faire cette étape **avant** de démarrer le bot : une fois lancé, c'est lui qui consomme ces messages et la page reste vide.

## 2. Installer

```sh
tar xzf edbot-livrable.tar.gz && cd edbot-livrable
mkdir -p state documents
cp config.ini.example state/config.ini
chmod 600 state/config.ini
```

Remplir `state/config.ini` : identifiants École Directe, jeton du bot, id du chat dans `allowed_chats`. Supprimer la section `[mail]` si le transfert par mail n'est pas voulu.

Le conteneur tourne avec l'uid/gid `1000:1000` par défaut. Si l'utilisateur propriétaire des dossiers est différent :

```sh
id -u; id -g          # noter les deux valeurs
echo "EDBOT_UID=$(id -u)" >  .env
echo "EDBOT_GID=$(id -g)" >> .env
```

Dans tous les cas, `state/` et `documents/` doivent appartenir à cet utilisateur.

## 3. La question de sécurité (double authentification)

À la première connexion, École Directe pose une question à choix multiples (année de naissance, ville…). La réponse est mémorisée dans `state/state.json` et rejouée automatiquement ensuite. Trois façons de la donner, au choix.

**A. Par Telegram (le plus simple).** Démarrer le bot (étape 4) : il envoie la question avec un bouton par réponse possible. Toucher la bonne, c'est fini.

**B. En ligne de commande, avant le premier démarrage.**

```sh
docker compose build
docker compose run --rm edbot python3 ed2fa.py
```

Le script affiche la question et les propositions numérotées, demande le numéro de la bonne réponse et l'enregistre dans `state/state.json`.

**C. À la main, si la question est déjà connue.** Créer `state/state.json` sur le modèle de `state.json.example` :

```json
{
  "twofa_answers": {
    "Quelle est votre année de naissance ?": "MTk4NQ=="
  }
}
```

- La **clé** est le texte exact de la question, au caractère près (majuscules, espaces, point d'interrogation compris). Le plus sûr est de la recopier depuis le message Telegram ou la sortie de `ed2fa.py`.
- La **valeur** est la réponse encodée en base64, comme École Directe l'envoie : `printf '%s' '1985' | base64` donne `MTk4NQ==`.
- L'exemple ci-dessus est fictif. École Directe pose plusieurs questions différentes au fil du temps ; on peut en mettre plusieurs, une par ligne.
- `chmod 600 state/state.json` ensuite.

Une réponse fausse ou une question inconnue n'est pas bloquante : le bot repose alors la question sur Telegram.

## 4. Démarrer

```sh
docker compose up -d --build
docker compose logs -f
```

Au premier passage, le bot mémorise les notes et messages existants **sans** les annoncer, puis envoie « Surveillance initialisée ». Seules les nouveautés seront notifiées ensuite. Lancer `/sync` une fois pour récupérer l'historique des documents dans `documents/`.

## Fonctionnement au quotidien

- École Directe a souvent de courtes pannes, surtout la nuit (mot de passe refusé à tort, base de données indisponible). Le bot les écrit dans son journal et ne prévient que si cela dure : 1 h pour un refus de mot de passe, 24 h pour une panne, réglables dans `[notifications]`. Un message confirme le retour à la normale.
- Lire un message par le bot le marque comme lu dans l'application École Directe.
- Les devoirs évalués uniquement en compétences sont ignorés ; la note est annoncée quand elle est saisie.

## Mise à jour, arrêt

```sh
docker compose up -d --build   # après avoir remplacé les .py
docker compose down            # arrêt (state/ et documents/ sont conservés)
```

## Fichiers

| Fichier | Rôle |
| --- | --- |
| `edbot.py` | bot Telegram, client École Directe, notes et moyennes |
| `edsync.py` | téléchargement des documents (`/sync`) |
| `edmail.py` | transfert de la messagerie par mail |
| `ed2fa.py` | saisie de la question de sécurité en ligne de commande |
| `API-EcoleDirecte.md` | référence de l'API utilisée par le bot |
| `state/config.ini` | configuration et secrets (à créer, 600) |
| `state/state.json` | mémoire du bot : notes vues, réponses de sécurité, jeton… (créé par le bot, 600) |
| `documents/` | documents téléchargés, avec `manifest.json` |

## Sécurité

- `config.ini` et `state.json` contiennent des identifiants : ne jamais les partager ni les versionner.
- Toujours renseigner `allowed_chats` : sinon n'importe qui trouvant le bot peut lire les notes.
- Si l'API change (tout se met à échouer d'un coup), le premier suspect est la version `ED_VERSION` en tête de `edbot.py` : la remplacer par celle visible dans les requêtes du site (outils de développement du navigateur, onglet Réseau, paramètre `v=`).
