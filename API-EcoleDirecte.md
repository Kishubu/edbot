# API École Directe — référence non officielle

17/10/2026

## Avertissement

Cette API n'est ni publique ni documentée par son éditeur : c'est celle qu'appelle le site web en arrière-plan. Ce document décrit ce qui fonctionne réellement dans le client edbot, version d'API `4.100.1`, testé en octobre 2026 avec un compte parent.

- Usage personnel, sur son propre compte uniquement.
- Aucune garantie de stabilité : une route ou un champ peut changer sans préavis.
- Les champs listés ici sont ceux exploités par edbot ; les réponses en contiennent beaucoup d'autres.

## Généralités

Toutes les routes sont des `POST` sur `https://api.ecoledirecte.com/v3/….awp`, avec la version d'API en paramètre `v` et le verbe en paramètre `verbe`.

**Corps de requête** : la chaîne `data=` suivie d'un objet JSON, envoyée en `text/plain` (pas en JSON pur, pas en form-urlencoded).

```
POST /v3/eleves/1234/notes.awp?verbe=get&v=4.100.1
Content-Type: text/plain
X-Token: <jeton>

data={"anneeScolaire":""}
```

**En-têtes attendus** : le serveur filtre les clients qui ne ressemblent pas au site.

| En-tête | Valeur | Quand |
| --- | --- | --- |
| `User-Agent` | un navigateur courant (Chrome) | toujours |
| `Origin` | `https://www.ecoledirecte.com` | toujours |
| `Referer` | `https://www.ecoledirecte.com/` | toujours |
| `X-Requested-With` | `XMLHttpRequest` | toujours |
| `X-Token` | jeton de session | après connexion |
| `X-Gtk` | valeur du cookie `GTK` | au login |
| `2fa-token` | jeton de double authentification | pendant la 2FA |

**Réponse** : l'état réel est dans le champ code du corps JSON, pas dans le statut HTTP.

```
{"code": 200, "token": "<nouveau jeton>", "message": "", "data": { … }}
```

- `code` : 200 si succès, sinon voir *Codes de retour*.
- `token` : le jeton peut être renouvelé à chaque réponse ; toujours reprendre le dernier reçu.
- **Année scolaire** : format `2026-2027`, qui bascule au 1er septembre.

## Authentification

La connexion se fait en deux appels : récupération du cookie `GTK`, puis login, qui renvoie le jeton `X-Token` et la liste des comptes.

### 1. Cookie GTK

```
GET /v3/login.awp?gtk=1&v=4.100.1
```

La réponse pose un cookie `GTK`. Le conserver (cookie jar) et renvoyer sa valeur dans l'en-tête `X-Gtk` du login.

### 2. Login

```
POST /v3/login.awp?v=4.100.1
X-Gtk: <valeur du cookie GTK>

data={"identifiant":"…","motdepasse":"…","isReLogin":false,"uuid":"","fa":[]}
```

| `code` | Signification | Suite |
| --- | --- | --- |
| 200 | connecté | lire `token` et `data.accounts` |
| 250 | double authentification requise | voir section suivante |
| 505 | identifiant ou mot de passe refusé | voir *Codes de retour* |

`fa` reçoit le couple `{"cn","cv"}` obtenu lors d'une double authentification précédente : il évite de repasser la question à chaque connexion.

### 3. Comptes et élèves

`data.accounts[0]` décrit le compte connecté.

| Champ | Contenu |
| --- | --- |
| `typeCompte` | `1` ou `2` = compte parent ; sinon compte élève |
| `id` | identifiant de la famille, utilisé par la messagerie |
| `profile.eleves[]` | les enfants : `id`, `prenom`, … |

Avec un compte élève, l'élève est le compte lui-même. L'`id` d'un élève sert dans les routes `/v3/eleves/{id}/…`.

## Double authentification

Quand le login renvoie `code` 250, le serveur pose une question à choix multiples (année de naissance, ville…). Une bonne réponse donne un couple `cn`/`cv` réutilisable aux connexions suivantes.

Le jeton à utiliser est celui de l'en-tête de réponse `2fa-token` du login, à défaut le champ `token`. Il part dans `X-Token` **et** `2fa-token`.

### Lire la question

```
POST /v3/connexion/doubleauth.awp?verbe=get&v=4.100.1
X-Token: <jeton 2FA>
2fa-token: <jeton 2FA>

data={}
```

- `data.question` : la question, encodée en base64 (UTF-8).
- `data.propositions[]` : les réponses possibles, elles aussi en base64.

### Répondre

```
POST /v3/connexion/doubleauth.awp?verbe=post&v=4.100.1
X-Token: <jeton 2FA>
2fa-token: <jeton 2FA>

data={"choix":"<proposition choisie, telle que reçue en base64>"}
```

En cas de succès, `data.cn` et `data.cv` sont renvoyés. Refaire alors le login avec `"fa":[{"cn":"…","cv":"…"}]` pour obtenir le vrai jeton de session.

**À conserver** : le couple `cn`/`cv`, et la réponse choisie indexée par la question. Si la même question revient, la réponse peut être rejouée sans intervention humaine.

## Notes et moyennes

Une seule route renvoie à la fois toutes les notes de l'année et les moyennes par période.

```
POST /v3/eleves/{idEleve}/notes.awp?verbe=get&v=4.100.1
X-Token: <jeton>

data={"anneeScolaire":""}
```

`anneeScolaire` vide = année en cours.

### `data.notes[]` : une entrée par devoir

| Champ | Exemple | Remarque |
| --- | --- | --- |
| `id` | `48211` | stable : sert à détecter les nouvelles notes |
| `libelleMatiere` | `MATHEMATIQUES` | |
| `devoir` | `Interrogation 2` | intitulé du devoir |
| `valeur` | `9` | vide si pas encore noté |
| `noteSur` | `10` | `0` si devoir évalué en compétences seules |
| `coef` | `1` | |
| `date` | `2026-09-29` | date du devoir |
| `elementsProgramme[]` | | niveaux de maîtrise (compétences) |

**Piège** : un devoir peut apparaître d'abord avec ses compétences seules (`valeur` vide, `noteSur` à 0), puis recevoir sa note plus tard sous le même `id`. Ne pas le marquer « vu » tant que la note n'est pas là, sinon elle passe inaperçue.

### `data.periodes[]` : trimestres et moyennes

| Champ | Contenu |
| --- | --- |
| `idPeriode`, `periode` | identifiant et libellé (« 1er Trimestre ») |
| `annuel` | `true` pour la période « Année » |
| `ensembleMatieres.moyenneGenerale` | moyenne générale de l'élève |
| `ensembleMatieres.moyenneClasse` / `moyenneMin` / `moyenneMax` | moyenne, minimum et maximum de la classe |
| `ensembleMatieres.dateCalcul` | date du dernier calcul par l'établissement |
| `ensembleMatieres.disciplines[]` | `discipline`, `moyenne`, `groupeMatiere` (ligne de regroupement) |

- Les moyennes sont des chaînes avec virgule (`"17,09"`) : convertir avant tout calcul.
- Elles ne bougent pas à chaque note : l'établissement les recalcule, en général la nuit.
- Les périodes « Relevé » ont une moyenne générale vide.
- `data.parametrage.moyenneSur` donne le barème (20 par défaut).

## Documents

Les documents (bulletins, factures, pièces administratives) se listent par année scolaire, puis se téléchargent un par un.

### Lister

```
POST /v3/familledocuments.awp?verbe=get&archive=&v=4.100.1
X-Token: <jeton>

data={}
```

`archive` vide = année en cours ; `archive=2024-2025` = une année passée. Remonter année par année jusqu'à tomber sur des années vides.

| Clé de `data` | Contenu |
| --- | --- |
| `notes` | bulletins |
| `factures` | factures |
| `administratifs` | documents administratifs |
| `viescolaire` | vie scolaire |
| `inscriptions` | documents d'inscription, non téléchargeables par cette route |

Chaque document porte `id`, `libelle`, `date` et `idEleve` (l'enfant concerné, utile avec plusieurs enfants).

### Télécharger

```
POST /v3/telechargement.awp?verbe=get&v=4.100.1&fichierId={id}
X-Token: <jeton>

data={"forceDownload":0}
```

Pour une année passée, ajouter `&archive=true&anneeArchive=2024-2025`. La réponse est le fichier brut ; le nom d'origine est dans `Content-Disposition`. Une pause d'environ 0,4 s entre deux téléchargements évite de ressembler à un aspirateur.

## Messagerie

La messagerie du compte parent se lit avec l'`id` de la famille (`accounts[0].id`). La messagerie élève (`/v3/eleves/{id}/messages`) répond 403 depuis un compte parent.

### Lister les messages reçus

```
POST /v3/familles/{idFamille}/messages.awp?force=false&typeRecuperation=received
     &idClasseur=0&orderBy=date&order=desc&query=&onlyRead=&page=0
     &itemsPerPage=100&getAll=0&verbe=get&v=4.100.1
X-Token: <jeton>

data={"anneeMessages":"2026-2027"}
```

Les messages sont dans `data.messages.received[]` : `id`, `subject`, `date` (`2026-10-15 08:12:00`), `from`, … Leur `id` est stable et sert à repérer les nouveaux.

### Lire un message

```
POST /v3/familles/{idFamille}/messages/{idMessage}.awp?verbe=get&mode=destinataire&v=4.100.1
X-Token: <jeton>

data={"anneeMessages":"2026-2027"}
```

| Champ de `data` | Contenu |
| --- | --- |
| `content` | corps HTML, encodé en base64 |
| `from` | `civilite`, `prenom`, `particule`, `nom`, `role`, `fonctionPersonnel` |
| `files[]` | pièces jointes : `id`, `libelle`, `type` |

- `role` : `A` administration, `P` professeur, `E` élève, `F` famille.
- **Effet de bord** : lire un message par l'API le marque comme lu dans l'application.
- Les images collées dans le message sont incluses dans le HTML en `data:image/png;base64`, parfois plusieurs mégaoctets.

### Télécharger une pièce jointe

Même route que les documents, avec deux paramètres de plus :

```
POST /v3/telechargement.awp?verbe=get&v=4.100.1&fichierId={id}
     &leTypeDeFichier={type, PIECE_JOINTE par défaut}&anneeMessages=2026-2027

data={"forceDownload":0}
```

Une réponse qui commence par `{` ou `<` est une erreur, pas un fichier.

## Codes de retour

Codes du champ `code` effectivement rencontrés, avec le comportement conseillé.

| `code` | Signification | Conduite |
| --- | --- | --- |
| 200 | succès | lire `data`, reprendre `token` |
| 250 | double authentification requise | voir *Double authentification* |
| 505 | identifiant ou mot de passe incorrect | souvent isolé la nuit alors que les identifiants sont bons : réessayer au tour suivant avant d'alerter |
| 535 | indisponibilité transitoire côté serveur | réessayer plus tard |
| 1 | « Impossible d'effectuer la connexion à la base de donnée » | panne serveur, quelques minutes en général |
| 403 | ressource interdite pour ce type de compte | ex. : messagerie élève depuis un compte parent |

Un `message` lisible accompagne en général les codes d'erreur.

## Bonnes pratiques

- **Cadence** : un passage toutes les 5 minutes suffit largement. Ne jamais réessayer en boucle serrée après un échec : un échec compte comme une tentative.
- **Une session par passage** : se connecter, enchaîner notes, documents et messagerie avec le même jeton, en reprenant le `token` de chaque réponse.
- **Détecter le nouveau par `id`**, jamais par drapeau « lu » ni par date. Au tout premier passage, mémoriser l'existant sans notifier.
- **Pannes** : 505, 535 et code 1 sont fréquents et brefs, surtout la nuit. Journaliser, et n'alerter qu'après une durée d'échec ininterrompu (1 h pour l'authentification, 24 h pour le reste dans edbot). Dédupliquer les messages d'erreur.
- **Version `v`** : copier celle du site (visible dans les requêtes du navigateur). Si tout se met à échouer d'un coup, c'est le premier suspect.
- **Secrets** : identifiants et couple `cn`/`cv` dans un fichier lisible par le seul utilisateur du bot.
