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

La section `[mail]` a été supprimée ; le transfert par mail n'est plus supporté.

## Lancement

```sh
docker compose up -d --build
docker compose logs -f
```

Au premier passage, le bot mémorise les notes existantes sans les annoncer, puis envoie « Surveillance initialisée ». Seules les nouveautés seront notifiées ensuite.

## Découpage WhatsApp

Si le contenu affiché dépasse 3700 caractères, le message est automatiquement coupé en plusieurs parties pour respecter la limite de WhatsApp.
