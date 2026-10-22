#!/usr/bin/env python3
"""
Bot Telegram — notes EcoleDirecte.

- Commandes à la demande (/notes, /moyennes, /recent, /check) + notifications
  automatiques des nouvelles notes et des changements de moyenne générale
  (polling configurable).
- Double authentification gérée via boutons Telegram ; la réponse est mémorisée
  et rejouée automatiquement si la même question revient.
- Accès restreint à un seul chat Telegram.
- Zéro dépendance externe (stdlib uniquement).

Config : config.ini (à côté de ce fichier).
"""

import os
import sys
import json
import time
import base64
import html
import configparser
import http.cookiejar
import urllib.request
import urllib.parse
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
# Surchargeable par env : en conteneur, config.ini et state.json vivent dans
# un dossier monté à part (pas HERE), pour que le tmp+os.replace() de
# save_state() reste sur le même système de fichiers que sa cible (sinon
# OSError: Invalid cross-device link entre la couche writable du conteneur et
# le bind-mount).
CONFIG_PATH = os.environ.get("EDBOT_CONFIG_PATH") or os.path.join(HERE, "config.ini")
STATE_PATH = os.environ.get("EDBOT_STATE_PATH") or os.path.join(HERE, "state.json")

ED_BASE = "https://api.ecoledirecte.com"
ED_VERSION = "4.100.1"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


# --------------------------------------------------------------------------- #
#  Config & state
# --------------------------------------------------------------------------- #

# Cache de la config, pour que les helpers de garde-fous 2FA (appelés
# hors de la classe Bot) puissent lire les options [ecoledirecte].
_CONFIG_CACHE = None


def load_config():
    global _CONFIG_CACHE
    if not os.path.exists(CONFIG_PATH):
        sys.exit(f"Config introuvable : {CONFIG_PATH}")
    cp = configparser.ConfigParser()
    cp.read(CONFIG_PATH)
    _CONFIG_CACHE = cp
    return cp


def _config_opt(section, option, defaut=None):
    """Lit une option de config (renvoie `defaut` si absente)."""
    cp = _CONFIG_CACHE
    if cp is None:
        try:
            cp = load_config()
        except SystemExit:
            return defaut
    try:
        return cp.get(section, option)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return defaut


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"cn": None, "cv": None, "twofa_answers": {},
            "seen_notes": [], "last_poll": 0, "initialized": False,
            "tg_offset": 0  # legacy Telegram
        }


# --------------------------------------------------------------------------- #
#  Garde-fous 2FA
#
#  EcoleDirecte verrouille le compte parental après UNE SEULE mauvaise
#  réponse à la question de sécurité. D'où les règles suivantes, appliquées
#  partout (bot Telegram, bot WhatsApp, script CLI) :
#
#   1. une réponse mémorisée n'est rejouée QUE si elle figure encore dans les
#      propositions proposées (ED change l'ordre et le contenu des
#      propositions ; une réponse hors liste compte comme fausse) ;
#   2. aucune soumission automatique pendant le polling : une réponse posée
#      par un humain, ou rejouée avec certitude, uniquement ;
#   3. après un échec, un cooldown long interdit toute nouvelle soumission ;
#   4. un compte détecté bloqué gèle définitivement le bot.
# --------------------------------------------------------------------------- #

def norm2fa(text):
    """Normalise un texte pour comparer deux propositions 2FA
    (casse, accents, espaces insécables, apostrophes typographiques)."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("\u2019", "'").replace("\u00a0", " ")
    return " ".join(s.casefold().split())


def prop_norm(raw):
    """Proposition 2FA décodée et normalisée."""
    return norm2fa(_b64d(raw))


# Délais (secondes) — surchargeables via config.ini [ecoledirecte].
COOLDOWN_DEFAUT = 24 * 3600       # 24 h après un échec de réponse 2FA
BLOCAGE_DEFAUT = 7 * 24 * 3600    # 7 jours après un compte détecté bloqué


def twofa_cooldown(state, defaut=None):
    """Durée de cooldown après un échec 2FA (secondes).

    Priorité : state > config.ini [ecoledirecte] twofa_cooldown_hours
    > valeur par défaut (24 h)."""
    if defaut is None:
        defaut = COOLDOWN_DEFAUT
    v = state.get("twofa_cooldown_seconds")
    if v:
        return int(v)
    cfg = _config_opt("ecoledirecte", "twofa_cooldown_hours")
    if cfg:
        try:
            return int(float(cfg) * 3600)
        except ValueError:
            pass
    return int(defaut)


def twofa_bloque(state):
    """Le compte a été détecté bloqué : on ne tente plus rien.

    Le blocage est définitif : il ne se lève qu'au `reset` manuel
    (`/2fa reset` sur Telegram, `!2fa reset` sur WhatsApp), car le compte
    École Directe reste verrouillé côté serveur tant qu'il n'a pas été
    débloqué par un parent."""
    return bool(state.get("twofa_bloque"))


def twofa_duree_blocage(state):
    """Durée de blocage configurée (secondes), 0 = illimité.

    Sert uniquement à rappeler à l'utilisateur de vérifier le compte
    École Directe : le blocage n'expire pas tout seul."""
    v = state.get("twofa_blocage_seconds")
    if v:
        return int(v)
    cfg = _config_opt("ecoledirecte", "twofa_blocage_hours")
    if cfg:
        try:
            return int(float(cfg) * 3600)
        except ValueError:
            pass
    return 0


def twofa_cooldown_actif(state, maintenant=None):
    """Une soumission 2FA est-elle interdite pour l'instant ?"""
    until = state.get("twofa_cooldown_until") or 0
    if not until:
        return False
    maintenant = maintenant or int(time.time())
    return maintenant < int(until)


def twofa_fige(state):
    """Le bot est-il gelé (blocage détecté ou cooldown en cours) ?"""
    return twofa_bloque(state) or twofa_cooldown_actif(state)


def twofa_message_fige(state, maintenant=None):
    """Message expliquant pourquoi aucune soumission 2FA n'est possible."""
    if twofa_bloque(state):
        return ("⛔ <b>Compte École Directe bloqué</b>\n\n"
                "Le compte a été verrouillé par École Directe (mauvaise réponse à "
                "la question de sécurité).\n\n"
                "Aucune tentative n'est faite tant que tu n'as pas débloqué le "
                "compte et réinitialisé la protection :\n"
                "<code>/2fa reset</code>")
    until = int(state.get("twofa_cooldown_until") or 0)
    maintenant = maintenant or int(time.time())
    reste = max(0, until - maintenant)
    return ("⏳ <b>Vérification de sécurité en pause</b>\n\n"
            f"Une réponse a été refusée récemment : nouvelle tentative possible "
            f"dans {_duree(reste)}.\n\n"
            "Le compte se bloque sur une seule mauvaise réponse : on ne tente "
            "plus rien tout seul.")


def twofa_trouver_reponse(state, question, propositions):
    """Cherche dans `propositions` la réponse mémorisée pour `question`.

    Renvoie la proposition brute (base64) ou None.

    Sécurité : si la réponse mémorisée n'est PLUS proposée par le serveur,
    elle est considérée comme fausse et n'est PAS jouée (une réponse hors
    liste = blocage du compte)."""
    answers = state.get("twofa_answers") or {}
    saved = answers.get(question)
    if not saved:
        return None
    cible = prop_norm(saved)
    for raw in propositions or []:
        if prop_norm(raw) == cible:
            return raw
    print(f"Réponse 2FA mémorisée absente des propositions proposées "
          f"(question : {question!r}) : elle ne sera PAS rejouée.", flush=True)
    return None


def twofa_memoriser(state, question, raw):
    """Mémorise une réponse 2FA validée."""
    state.setdefault("twofa_answers", {})[question] = raw


def twofa_echec(state, cooldown=None):
    """Enregistre un échec de réponse 2FA et arme le cooldown.

    Purge aussi la réponse fautive mémorisée : elle ne doit JAMAIS être
    rejouée, sinon le compte resterait bloqué."""
    cooldown = cooldown or twofa_cooldown(state)
    now = int(time.time())
    state["twofa_cooldown_until"] = now + cooldown
    state["twofa_failures"] = int(state.get("twofa_failures") or 0) + 1
    # Purge de la réponse fautive (question courante en priorité).
    question = state.get("twofa_current_question")
    answers = state.get("twofa_answers")
    if isinstance(answers, dict) and question:
        answers.pop(question, None)
    state["last_error"] = "Réponse à la question de sécurité refusée par " \
                          "École Directe. Nouvelle tentative dans " \
                          f"{_duree(cooldown)}."
    save_state(state)
    print(f"Réponse 2FA refusée : cooldown de {_duree(cooldown)} "
          f"(échecs : {state['twofa_failures']}).", flush=True)


def twofa_reussite(state):
    """Réinitialise le compteur d'échecs après une réponse acceptée."""
    state["twofa_failures"] = 0
    state.pop("twofa_cooldown_until", None)
    state.pop("twofa_bloque", None)
    state.pop("twofa_bloque_since", None)


def twofa_bloquer(state):
    """Gèle définitivement le bot : le compte est bloqué par École Directe."""
    state["twofa_bloque"] = True
    state["twofa_bloque_since"] = int(time.time())
    state.pop("twofa_cooldown_until", None)
    save_state(state)


# Marqueurs d'un message serveur annonçant un BLOCAGE DU COMPTE (et non
# simplement une réponse fausse). La recherche se fait sur le texte
# normalisé : « bloqué » et « bloque » doivent être reconnus tous les deux,
# et les accents/casse ignorés.
MOTS_BLOCAGE = ("bloqu", "bloc", "verrouill", "suspend", "desactiv")


def twofa_message_bloquante(message):
    """Le message renvoyé par École Directe annonce-t-il un blocage ?

    École Directe répond par exemple « Compte bloqué pour trop de
    tentatives » ou « Accès suspendu ». Un simple test « bloc » ne
    détectait ni « bloqué » ni « bloque » : un vrai blocage n'était donc
    repéré que comme une mauvaise réponse (cooldown 24 h au lieu d'un
    gel définitif), et le bot réessayait indéfiniment.
    """
    texte = norm2fa(message)
    return any(m in texte for m in MOTS_BLOCAGE)


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)
    try:
        os.chmod(STATE_PATH, 0o600)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
#  Client EcoleDirecte
# --------------------------------------------------------------------------- #
class EDSession:
    def __init__(self, user, password, state):
        self.user = user
        self.password = password
        self.state = state
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj))
        self.two_fa_token = None
        self.token = None

    def _gtk(self):
        for c in self.cj:
            if c.name == "GTK" and c.value:
                return c.value
        return None

    def fetch_gtk(self):
        req = urllib.request.Request(
            f"{ED_BASE}/v3/login.awp?gtk=1&v={ED_VERSION}", method="GET")
        req.add_header("User-Agent", UA)
        req.add_header("Origin", "https://www.ecoledirecte.com")
        req.add_header("Referer", "https://www.ecoledirecte.com/")
        self.opener.open(req, timeout=30).read()

    def post(self, pathname, payload, token=None, use_gtk=False, fa_token=None):
        body = "data=" + json.dumps(payload or {})
        req = urllib.request.Request(ED_BASE + pathname,
                                     data=body.encode(), method="POST")
        req.add_header("User-Agent", UA)
        req.add_header("Content-Type", "text/plain")
        req.add_header("X-Requested-With", "XMLHttpRequest")
        req.add_header("Origin", "https://www.ecoledirecte.com")
        req.add_header("Referer", "https://www.ecoledirecte.com/")
        if token:
            req.add_header("X-Token", token)
        if fa_token:
            req.add_header("2fa-token", fa_token)
        if use_gtk and self._gtk():
            req.add_header("X-Gtk", self._gtk())
        resp = self.opener.open(req, timeout=30)
        fa = resp.headers.get("2fa-token") or resp.headers.get("2FA-Token")
        if fa:
            self.two_fa_token = fa
        raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw)

    def _do_login(self, fa=None):
        self.fetch_gtk()
        payload = {"identifiant": self.user.strip(), "motdepasse": self.password,
                   "isReLogin": False, "uuid": "", "fa": fa or []}
        return self.post(f"/v3/login.awp?v={ED_VERSION}", payload, use_gtk=True)

    def ensure_logged_in(self):
        """
        -> ("ok", accounts)
        -> ("2fa", {"question": str, "propositions": [str], "token": str})
        -> ("fige", message)   -> cooldown 2FA ou compte bloqué : ne rien tenter
        -> ("error", message)
        """
        # Garde-fou 2 : après un échec de réponse 2FA, ou si le compte est
        # bloqué, on ne présente plus aucune question : ni rejeu automatique,
        # ni proposition à l'utilisateur (il risquerait de rejouer la même
        # réponse et de refaire bloquer le compte).
        if twofa_bloque(self.state):
            return "fige", twofa_message_fige(self.state)
        if twofa_cooldown_actif(self.state):
            return "fige", twofa_message_fige(self.state)

        fa = None
        if self.state.get("cn") and self.state.get("cv"):
            fa = [{"cn": self.state["cn"], "cv": self.state["cv"]}]
        body = self._do_login(fa=fa)
        code = body.get("code")

        if code == 200:
            self.token = body.get("token")
            return "ok", body.get("data", {}).get("accounts", [])

        if code == 250:  # 2FA requise
            token = self.two_fa_token or body.get("token")
            q = self.post(f"/v3/connexion/doubleauth.awp?verbe=get&v={ED_VERSION}",
                          {}, token=token, fa_token=token)
            if q.get("code") != 200:
                return "error", f"QCM indisponible (code {q.get('code')})"
            token = q.get("token") or token
            qd = q.get("data", {})
            question = _b64d(qd.get("question", ""))
            props = qd.get("propositions", [])
            # Mémorise la question courante : elle sert à purger la réponse
            # memorisée si elle s'avère fausse.
            self.state["twofa_current_question"] = question
            # Réponse déjà connue ET toujours proposée -> on tente direct.
            # (une réponse mémorisée absente des propositions n'est JAMAIS
            # rejouée : elle compterait comme une mauvaise réponse, donc
            # comme un blocage du compte.)
            saved = twofa_trouver_reponse(self.state, question, props)
            if saved:
                res = self.submit_2fa(token, saved)
                if res[0] == "ok":
                    return res
                if res[0] == "fige":
                    return res
                if res[0] == "erreur":
                    # Panne réseau : ce n'est pas une mauvaise réponse, le
                    # cooldown n'est pas armé. On remonte l'erreur telle quelle.
                    return "error", res[1]
                # Réponse refusée : on NE redemande pas la question à
                # l'utilisateur (risque de nouvelle mauvaise réponse).
                return "fige", res[1] + "\n\n" + twofa_message_fige(self.state)
            return "2fa", {"question": question, "propositions": props,
                           "token": token}

        if code == 505:
            return "auth", "Identifiant ou mot de passe incorrect (505)."
        return "error", f"Échec connexion (code {code}) : {body.get('message')}"

    def submit_2fa(self, token, chosen_raw):
        """Envoie une réponse à la question de sécurité.

        Renvoie :
          ("ok", accounts)
          ("refus", message)   -> réponse fausse : cooldown + alerte
          ("fige", message)    -> cooldown actif ou compte bloqué : rien n'est envoyé
          ("erreur", message)  -> erreur réseau / serveur : sans conséquence
        """
        # Garde-fou 1 : aucune soumission pendant un cooldown, ni si le
        # compte est bloqué. Une seule mauvaise réponse verrouille le compte.
        if twofa_bloque(self.state):
            return "fige", twofa_message_fige(self.state)
        if twofa_cooldown_actif(self.state):
            return "fige", twofa_message_fige(self.state)

        try:
            ans = self.post(
                f"/v3/connexion/doubleauth.awp?verbe=post&v={ED_VERSION}",
                {"choix": chosen_raw}, token=token, fa_token=token)
        except Exception as e:
            # Erreur réseau : la réponse n'a pas été traitée par le serveur,
            # ce n'est donc pas une mauvaise réponse. On n'arme PAS le cooldown.
            return "erreur", f"EcoleDirecte ne répond pas : {e}"

        if ans.get("code") != 200:
            # Le serveur a bien reçu la réponse et l'a refusée : c'est une
            # mauvaise réponse -> cooldown long, et purge de la réponse
            # mémorisée (elle est fausse).
            msg = str(ans.get("message") or "").lower()
            if twofa_message_bloquante(msg):
                twofa_bloquer(self.state)
                return "refus", ("⛔ Compte École Directe bloqué par le serveur "
                                 f"({ans.get('code')}) : {ans.get('message')}")
            self.state.get("twofa_answers", {}).pop(
                self.state.get("twofa_current_question", ""), None)
            twofa_echec(self.state)
            return "refus", (f"Réponse 2FA refusée (code {ans.get('code')}) : "
                             f"{ans.get('message')}")
        cn = ans["data"]["cn"]
        cv = ans["data"]["cv"]
        self.state["cn"] = cn
        self.state["cv"] = cv
        twofa_reussite(self.state)
        body = self._do_login(fa=[{"cn": cn, "cv": cv}])
        if body.get("code") == 505:
            return "auth", "Identifiant ou mot de passe incorrect (505)."
        if body.get("code") != 200:
            return "erreur", f"Re-login échoué (code {body.get('code')})"
        self.token = body.get("token")
        return "ok", body.get("data", {}).get("accounts", [])

    def fetch_notes(self, eleve_id):
        body = self.post(
            f"/v3/eleves/{eleve_id}/notes.awp?verbe=get&v={ED_VERSION}",
            {"anneeScolaire": ""}, token=self.token)
        self.token = body.get("token") or self.token
        if body.get("code") != 200:
            return None
        return body.get("data", {})


def _b64d(s):
    return base64.b64decode(s).decode("utf-8", "replace") if s else ""


def _duree(secs):
    secs = max(0, int(secs))
    if secs < 3600:
        return f"{secs // 60} min"
    if secs < 48 * 3600:
        return f"{secs // 3600} h {secs % 3600 // 60:02d}"
    return f"{secs // 86400} j {secs % 86400 // 3600} h"


def eleves_from_accounts(accounts):
    if not accounts:
        return []
    acc = accounts[0]
    if acc.get("typeCompte") in ("1", "2"):  # Famille
        return acc.get("profile", {}).get("eleves", [])
    return [acc]  # Élève


# --------------------------------------------------------------------------- #
#  Mise en forme
# --------------------------------------------------------------------------- #
def fmt_moyennes(eleve, data):
    prenom = eleve.get("prenom", "")
    out = [f"<b>📊 {html.escape(prenom)}</b>"]
    for p in data.get("periodes", []):
        if p.get("annuel"):
            continue
        ens = p.get("ensembleMatieres", {}) or {}
        mg = ens.get("moyenneGenerale")
        if mg in (None, ""):
            continue
        mc = ens.get("moyenneClasse")
        extra = f"  (classe {mc})" if mc not in (None, "") else ""
        out.append(f"\n<b>{html.escape(p.get('periode','Période'))}</b> — "
                   f"moy. générale <b>{html.escape(str(mg))}</b>{extra}")
        for d in ens.get("disciplines", []):
            if d.get("groupeMatiere"):
                continue
            moy = d.get("moyenne", "")
            if moy in (None, ""):
                continue
            out.append(f"  • {html.escape(d.get('discipline','?'))} : "
                       f"{html.escape(str(moy))}")
    return "\n".join(out)


def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def fmt_moyenne_generale(eleve, periode, ens, old, sur=20):
    mg = ens.get("moyenneGenerale")
    a, b = _num(old), _num(mg)
    if old in (None, ""):
        evol = "première moyenne de la période"
    elif a is not None and b is not None:
        arrow = "📈" if b > a else "📉"
        evol = f"{arrow} {html.escape(str(old))} → {html.escape(str(mg))} ({b - a:+.2f})".replace(".", ",")
    else:
        evol = f"{html.escape(str(old))} → {html.escape(str(mg))}"
    out = [f"📊 <b>Moyenne générale — {html.escape(eleve.get('prenom', ''))}</b>",
           f"{html.escape(periode.get('periode', 'Période'))}",
           "",
           f"<b>{html.escape(str(mg))} / {sur}</b>",
           evol]
    extra = [f"{lbl} {html.escape(str(ens.get(k)))}"
             for lbl, k in (("Classe", "moyenneClasse"), ("Min", "moyenneMin"),
                            ("Max", "moyenneMax"))
             if ens.get(k) not in (None, "")]
    if extra:
        out.append("Moy. " + " · ".join(extra))
    return "\n".join(out)


def has_note(n):
    """Un devoir peut n'être évalué qu'en compétences (niveaux de maîtrise,
    `elementsProgramme`) : `valeur` vide et `noteSur` à 0. C'est aussi l'état
    d'un devoir dont le professeur saisit les compétences avant la note.
    Seules les vraies notes sont annoncées."""
    return (str(n.get("valeur") or "").strip() != ""
            and str(n.get("noteSur") or "0").strip() not in ("", "0"))


def fmt_note_line(n):
    return (f"<b>{html.escape(str(n.get('libelleMatiere','')))}</b> : "
            f"{html.escape(str(n.get('valeur','')))}/"
            f"{html.escape(str(n.get('noteSur','')))} "
            f"(coef {html.escape(str(n.get('coef','')))})"
            f"\n<i>{html.escape(str(n.get('devoir','')))}</i> — "
            f"{html.escape(str(n.get('date','')))}")


def fmt_recent(eleve, data, limit=10):
    prenom = eleve.get("prenom", "")
    notes = sorted(filter(has_note, data.get("notes", [])),
                   key=lambda n: n.get("date", ""),
                   reverse=True)[:limit]
    if not notes:
        return f"<b>{html.escape(prenom)}</b> : aucune note."
    out = [f"<b>🆕 {html.escape(prenom)} — {len(notes)} dernières notes</b>"]
    for n in notes:
        out.append("\n" + fmt_note_line(n))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
#  API Telegram (stdlib)
# --------------------------------------------------------------------------- #



# --------------------------------------------------------------------------- #
#  Bot
# --------------------------------------------------------------------------- #
