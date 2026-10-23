# edbot — Notes EcoleDirecte

edbot surveille un compte parent École Directe et envoie les nouvelles notes par canal WhatsApp.

## Canal principal : WhatsApp

Le bot WhatsApp (via `whatsapp/index.js` + `docker-compose.yml`) reçoit les commandes suivantes :

- `!notes` — Affiche les 100 dernières notes (depuis l'API ED, limite 100)
- `!moyennes` — Moyennes par matière
- `!recent` — 10 dernières notes
- `!check` — Force une vérification immédiate des notes
- `!messages` — Affiche le dernier message EcoleDirecte (API /messages.awp)
- `!sync` — Archive les nouveaux documents

## Installation

```sh
cd edubot/edbot
mkdir -p state documents
cp config.ini.example state/config.ini
chmod 600 state/config.ini
```

## Configuration

Le fichier `config.ini.example` contient les sections suivantes :

- `[ecoledirecte]` — Identifiants du compte parent (obligatoire)
- `[whatsapp]` — Nom du groupe WhatsApp surveillé (obligatoire)
- `[notifications]` — Fréquence de vérification, alertes panne (obligatoire)
- `[llm]` — Clé API Venice.ai et réglages du mode agentique (optionnel)

La section `[mail]` a été supprimée ; le transfert par mail n'est plus supporté.

## Mode agentique — langage naturel (WhatsApp)

Edbot peut recevoir des requêtes en langage naturel sur WhatsApp, sans préfixe `!` :
- _"Quel est ma dernière note en math ?"_
- _"Ma moyenne en espagnol ?"_
- _"Bonjour"_

L'agent LLM (via Venice.ai, modèle `nvidia-nemotron-3-nano-30b-a3b`) traduit l'intention en commande `edbot_cmd.py` correspondante (`notes`, `moyennes`, `recent`, `check`, `sync`, `messages`) et reformule la réponse en français naturel.

### Configuration

Dans `state/config.ini`, ajouter la section :

```ini
[llm]
venice_api_key = VOTRE_CLE_API_VENICE
venice_base_url = https://api.venice.ai/api/v1
venice_model = nvidia-nemotron-3-nano-30b-a3b
temperature = 0.3
agent_enabled = true
```

La clé peut aussi être définie via la variable d'environnement `VENICE_API_KEY`.

### Fonctionnement

1. Tout message texte dans le groupe surveillé qui ne commence pas par `!` est envoyé à l'agent LLM.
2. L'agent décide s'il faut exécuter une commande `edbot_cmd.py` ou répondre directement.
3. La réponse est préfixée par `🤖 ` (BOT_TAG) pour éviter que le bot ne réponde à ses propres messages (pas de boucle infinie).
4. Si École Directe demande une vérification de sécurité (2FA), l'agent pose la question à l'utilisateur et rejoue la commande initiale après réception de la réponse.

Les commandes `!xxx` directes conservent leur comportement inchangé.


## Lancement

```sh
docker compose up -d --build
docker compose logs -f
```

Au premier passage, le bot mémorise les notes existantes sans les annoncer, puis envoie « Surveillance initialisée ». Seules les nouveautés seront notifiées ensuite.

## Découpage WhatsApp

Si le contenu affiché dépasse 3700 caractères, le message est automatiquement coupé en plusieurs parties pour respecter la limite de WhatsApp.
