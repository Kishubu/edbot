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
def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit(f"Config introuvable : {CONFIG_PATH}")
    cp = configparser.ConfigParser()
    cp.read(CONFIG_PATH)
    return cp


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"cn": None, "cv": None, "twofa_answers": {},
            "seen_notes": [], "last_poll": 0, "initialized": False,
            "tg_offset": 0}


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
        -> ("error", message)
        """
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
            # Réponse déjà connue ? -> on tente direct.
            saved = self.state.get("twofa_answers", {}).get(question)
            if saved:
                res = self.submit_2fa(token, saved)
                if res[0] == "ok":
                    return res
                # sinon on retombe sur la demande interactive
            return "2fa", {"question": question, "propositions": props,
                           "token": token}

        if code == 505:
            return "auth", "Identifiant ou mot de passe incorrect (505)."
        return "error", f"Échec connexion (code {code}) : {body.get('message')}"

    def submit_2fa(self, token, chosen_raw):
        ans = self.post(f"/v3/connexion/doubleauth.awp?verbe=post&v={ED_VERSION}",
                        {"choix": chosen_raw}, token=token, fa_token=token)
        if ans.get("code") != 200:
            return "error", f"Réponse 2FA refusée (code {ans.get('code')})"
        cn = ans["data"]["cn"]
        cv = ans["data"]["cv"]
        self.state["cn"] = cn
        self.state["cv"] = cv
        body = self._do_login(fa=[{"cn": cn, "cv": cv}])
        if body.get("code") == 505:
            return "auth", "Identifiant ou mot de passe incorrect (505)."
        if body.get("code") != 200:
            return "error", f"Re-login échoué (code {body.get('code')})"
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
class Telegram:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}"

    def call(self, method, params=None, timeout=35):
        data = urllib.parse.urlencode(params or {}).encode()
        req = urllib.request.Request(f"{self.base}/{method}", data=data)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # réseau, timeout...
            return {"ok": False, "error": str(e)}

    def get_updates(self, offset, timeout=30):
        return self.call("getUpdates",
                         {"offset": offset, "timeout": timeout},
                         timeout=timeout + 5)

    def send(self, chat_id, text, reply_markup=None):
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": "true"}
        if reply_markup:
            params["reply_markup"] = json.dumps(reply_markup)
        # Telegram limite à 4096 caractères -> on tronçonne.
        for chunk in _split(text, 3900):
            params["text"] = chunk
            self.call("sendMessage", params)
            params.pop("reply_markup", None)

    def answer_callback(self, cb_id, text=""):
        self.call("answerCallbackQuery", {"callback_query_id": cb_id, "text": text})


def _split(text, size):
    if len(text) <= size:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > size:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        parts.append(cur)
    return parts


# --------------------------------------------------------------------------- #
#  Bot
# --------------------------------------------------------------------------- #
class Bot:
    def __init__(self):
        self.cfg = load_config()
        self.state = load_state()
        self.tg = Telegram(self.cfg["telegram"]["bot_token"].strip())
        self.allowed = {c.strip() for c in
                        str(self.cfg["telegram"].get("allowed_chats", "")).split(",")
                        if c.strip()}
        nc = str(self.cfg["telegram"].get("notify_chat", "")).strip()
        if not nc and self.allowed:
            nc = sorted(self.allowed)[0]
        self.notify_chat = nc
        self.interval = int(self.cfg["notifications"].get(
            "poll_interval_minutes", "60")) * 60
        self.outage_alert = int(float(self.cfg["notifications"].get(
            "outage_alert_hours", "24")) * 3600)
        self.auth_alert = int(float(self.cfg["notifications"].get(
            "auth_alert_minutes", "60")) * 60)
        self.ed = EDSession(self.cfg["ecoledirecte"]["identifiant"],
                            self.cfg["ecoledirecte"]["motdepasse"], self.state)
        self.pending = None  # 2FA en cours : {question, propositions, token, resume}

    # -- helpers --
    def is_allowed(self, chat_id):
        return not self.allowed or str(chat_id) in self.allowed

    def notify(self, text, markup=None):
        if self.notify_chat:
            self.tg.send(self.notify_chat, text, markup)

    def _notify_error_once(self, text):
        """Signale une erreur de connexion une seule fois par erreur DISTINCTE
        — pas à chaque tentative. Sans ça, un échec persistant (ex. code 535
        "établissement indisponible") spamme le même message toutes les ~30 s
        indéfiniment, une tentative par tour de boucle."""
        if self.state.get("last_error") == text:
            return
        self.state["last_error"] = text
        self.notify(text)

    # -- indisponibilité EcoleDirecte --
    def _outage(self, text, chat_id=None):
        """EcoleDirecte ne répond pas ou répond en erreur (réseau, HTTP 5xx,
        code 535 « établissement indisponible », code inconnu...). Ces pannes
        sont fréquentes et se résorbent seules : on les journalise toujours,
        mais on ne prévient sur Telegram qu'au-delà de `outage_alert_hours`
        (24 h par défaut) d'échecs ininterrompus, une seule fois, puis au
        retour. Une commande manuelle (chat_id) reçoit en revanche toujours
        une réponse immédiate.

        Compte aussi comme une tentative (last_poll) : sans ça, le polling
        automatique réessaierait à chaque tour de la boucle principale (~30 s)
        au lieu de respecter l'intervalle configuré."""
        now = int(time.time())
        since = self.state.get("outage_since") or now
        self.state["outage_since"] = since
        self.state["last_poll"] = now
        print(f"EcoleDirecte en échec (depuis {_duree(now - since)}) : {text}",
              flush=True)
        if chat_id:
            self.tg.send(chat_id, "⚠️ EcoleDirecte ne répond pas, réessaie plus "
                                  "tard.\n" + html.escape(text))
        if now - since >= self.outage_alert and not self.state.get("outage_notified"):
            self.state["outage_notified"] = True
            self.notify(f"⚠️ <b>EcoleDirecte en échec depuis {_duree(now - since)}</b>\n\n"
                        f"Dernière erreur : {html.escape(text)}\n\n"
                        "Notes et messages ne sont plus suivis en attendant. "
                        "Je te préviens dès que ça revient.")
        save_state(self.state)

    def _outage_over(self):
        since = self.state.pop("outage_since", None)
        if since is None:
            return
        notified = self.state.pop("outage_notified", False)
        took = _duree(int(time.time()) - since)
        print(f"EcoleDirecte de nouveau joignable (échecs pendant {took}).", flush=True)
        if notified:
            self.notify(f"✅ EcoleDirecte de nouveau joignable (coupure de {took}).")
        save_state(self.state)

    # -- connexion + 2FA --
    def login_or_prompt(self, resume, chat_id=None):
        """Connexion ; si 2FA -> envoie le QCM. resume: callable() à rejouer.
        chat_id : commande manuelle, qui mérite une réponse même en cas de
        panne passagère."""
        try:
            status, payload = self.ed.ensure_logged_in()
        except Exception as e:
            self._outage(f"connexion : {e}", chat_id)
            return None
        if status == "ok":
            if self.state.get("last_error") or "auth_since" in self.state:
                if self.state.get("auth_since"):
                    print("EcoleDirecte accepte de nouveau l'authentification.", flush=True)
                self.state["last_error"] = None
                self.state.pop("auth_since", None)
                if self.state.pop("auth_notified", False):
                    self.notify("✅ EcoleDirecte accepte de nouveau la connexion.")
                save_state(self.state)
            return payload
        if status == "2fa":
            self.pending = {**payload, "resume": resume}
            kb = {"inline_keyboard": [
                [{"text": _b64d(p), "callback_data": f"fa:{i}"}]
                for i, p in enumerate(payload["propositions"])]}
            self.notify("🔐 <b>Vérification de sécurité EcoleDirecte</b>\n\n"
                        + html.escape(payload["question"]), kb)
            return None
        if status == "auth":
            # Mot de passe refusé. ED renvoie parfois un 505 isolé (la nuit
            # surtout) alors que la tentative suivante passe : on ne prévient
            # sur Telegram que si le refus persiste `auth_alert_minutes`
            # (60 par défaut). Une commande manuelle a sa réponse immédiate.
            now = int(time.time())
            since = self.state.get("auth_since") or now
            self.state["auth_since"] = since
            print(f"EcoleDirecte refuse l'authentification (depuis "
                  f"{_duree(now - since)}) : {payload}", flush=True)
            if now - since >= self.auth_alert and not self.state.get("auth_notified"):
                self.state["auth_notified"] = True
                self.notify(f"🔑 <b>EcoleDirecte refuse la connexion depuis "
                            f"{_duree(now - since)}</b>\n\n{html.escape(str(payload))}")
            elif chat_id:
                self.tg.send(chat_id, f"🔑 {html.escape(str(payload))}")
            self.state["last_poll"] = int(time.time())
            save_state(self.state)
            return None
        self._outage(str(payload), chat_id)
        return None

    def handle_2fa_choice(self, idx, cb_id):
        if not self.pending:
            self.tg.answer_callback(cb_id, "Aucune question en attente.")
            return
        p = self.pending
        if idx < 0 or idx >= len(p["propositions"]):
            self.tg.answer_callback(cb_id, "Choix invalide.")
            return
        chosen_raw = p["propositions"][idx]
        self.tg.answer_callback(cb_id, "Validation…")
        try:
            status, payload = self.ed.submit_2fa(p["token"], chosen_raw)
        except Exception as e:
            status, payload = "error", f"EcoleDirecte ne répond pas : {e}"
        if status != "ok":
            print(f"Vérification de sécurité en échec : {payload}", flush=True)
            self.notify(f"⚠️ {html.escape(str(payload))}")
            self.pending = None
            return
        # On mémorise la réponse pour la prochaine fois.
        self.state.setdefault("twofa_answers", {})[p["question"]] = chosen_raw
        save_state(self.state)
        self.notify("✅ Vérification réussie.")
        resume = p["resume"]
        self.pending = None
        if resume:
            resume(payload)  # payload = accounts

    # -- actions notes --
    def cmd_notes(self, chat_id, kind):
        def resume(accounts):
            self._send_notes(chat_id, accounts, kind)
        accounts = self.login_or_prompt(resume, chat_id)
        if accounts is not None:
            self._send_notes(chat_id, accounts, kind)

    def _send_notes(self, chat_id, accounts, kind):
        eleves = eleves_from_accounts(accounts)
        if not eleves:
            self.tg.send(chat_id, "Aucun élève trouvé.")
            return
        for el in eleves:
            data = self.ed.fetch_notes(el.get("id"))
            if not data:
                self.tg.send(chat_id, f"Notes indisponibles pour "
                                      f"{html.escape(el.get('prenom',''))}.")
                continue
            if kind == "moyennes":
                self.tg.send(chat_id, fmt_moyennes(el, data))
            elif kind == "recent":
                self.tg.send(chat_id, fmt_recent(el, data))
            else:  # notes complètes = moyennes + récentes
                self.tg.send(chat_id, fmt_moyennes(el, data))
                self.tg.send(chat_id, fmt_recent(el, data, limit=15))

    # -- sync documents vers le partage --
    def cmd_sync(self, chat_id):
        def resume(accounts):
            self._run_sync(chat_id, accounts)
        accounts = self.login_or_prompt(resume, chat_id)
        if accounts is not None:
            self._run_sync(chat_id, accounts)

    def _run_sync(self, chat_id, accounts):
        import edsync  # import tardif : évite l'import circulaire à l'init
        try:
            res = edsync.run_sync(self.ed, accounts, log=lambda m: None)
        except Exception as e:
            self.tg.send(chat_id, "⚠️ Sync échouée : " + html.escape(str(e)))
            return
        new = res["new"]
        lines = ["<b>✅ Sync EcoleDirecte terminée</b>",
                 "📁 <b>%d</b> nouveau(x) · %d déjà présents · %d au total"
                 % (len(new), res["existing"], res["total"])]
        if new:
            groups = {}
            for d in new:
                groups.setdefault((d.get("eleve", "Famille"), d["annee"], d["categorie"]), []).append(d)
            for (eleve, annee, cat), docs in sorted(groups.items(), reverse=True):
                label = edsync.CAT_DIRS.get(cat, cat)
                lines.append("\n<b>%s — %s — %s</b> (%d)"
                             % (html.escape(eleve), html.escape(annee),
                                html.escape(label), len(docs)))
                for d in docs[:20]:
                    lines.append("  • %s — %s"
                                 % (html.escape(str(d.get("date") or "")),
                                    html.escape(str(d.get("libelle") or ""))))
        else:
            lines.append("\nAucun nouveau document.")
        if res["inscriptions"]:
            lines.append("\nℹ️ %d doc(s) d'inscription non téléchargeables "
                         "(à signer) — voir NON_ARCHIVES.txt." % res["inscriptions"])
        if res["errors"]:
            lines.append("\n⚠️ %d erreur(s) :" % len(res["errors"]))
            for e in res["errors"][:5]:
                lines.append("  • " + html.escape(e))
        self.tg.send(chat_id, "\n".join(lines))

    # -- polling nouvelles notes --
    def poll_new_notes(self):
        def resume(accounts):
            self._check_new(accounts)
        accounts = self.login_or_prompt(resume)
        if accounts is not None:
            self._check_new(accounts)

    def cmd_check(self, chat_id):
        """Vérification demandée explicitement : contrairement au polling
        automatique (silencieux si rien de nouveau, pour ne pas spammer
        toutes les heures), une demande manuelle mérite toujours une réponse
        — sinon le seul message visible reste "Vérification en cours…", qui
        donne l'impression que le bot est bloqué."""
        def resume(accounts):
            self._finish_check(chat_id, accounts)
        accounts = self.login_or_prompt(resume, chat_id)
        if accounts is not None:
            self._finish_check(chat_id, accounts)

    def _finish_check(self, chat_id, accounts):
        new_count = self._check_new(accounts, chat_id)
        if new_count is None:
            return  # échec déjà signalé par _outage
        if new_count:
            self.tg.send(chat_id, f"✅ Vérification terminée — {new_count} "
                                  f"nouvelle(s) note(s).")
        else:
            self.tg.send(chat_id, "✅ Vérification terminée — aucune nouvelle note.")

    def _check_new(self, accounts, chat_id=None):
        """Nouvelles notes + transfert des messages. Retourne le nombre de
        nouvelles notes, ou None si EcoleDirecte a échoué en cours de route
        (une exception ici remontait jusqu'à la boucle principale sans mettre
        à jour last_poll : nouvelle tentative toutes les ~35 s, cas du 503 du
        28/09/2026)."""
        try:
            new_count = self._check_new_notes(accounts)
            ed_errors = self._forward_mail(accounts)
        except Exception as e:
            self._outage(str(e), chat_id)
            return None
        if ed_errors:
            self._outage("transfert messagerie : " + "; ".join(ed_errors), chat_id)
            return new_count
        self._outage_over()
        return new_count

    def _check_new_notes(self, accounts):
        eleves = eleves_from_accounts(accounts)
        seen = set(self.state.get("seen_notes", []))
        first_run = not self.state.get("initialized")
        new_count = 0
        for el in eleves:
            data = self.ed.fetch_notes(el.get("id"))
            if data is None:
                raise RuntimeError(f"notes de {el.get('prenom', '?')} indisponibles")
            for n in data.get("notes", []):
                nid = str(n.get("id"))
                if nid in seen:
                    continue
                # Pas encore de note (compétences seules) : ni annoncé, ni
                # marqué vu — sinon la note saisie ensuite passe inaperçue
                # (cas du 29/09/2026 : « /0 » annoncé, puis 9/10 jamais).
                if not has_note(n):
                    continue
                seen.add(nid)
                if not first_run:
                    self.notify("🆕 <b>Nouvelle note — "
                                f"{html.escape(el.get('prenom',''))}</b>\n\n"
                                + fmt_note_line(n))
                    new_count += 1
            self._check_moyenne_generale(el, data)
        self.state["seen_notes"] = sorted(seen)
        self.state["initialized"] = True
        self.state["last_poll"] = int(time.time())
        save_state(self.state)
        if first_run:
            self.notify("✅ Surveillance initialisée. Tu seras notifié des "
                        "prochaines nouvelles notes.")
        return new_count

    def _check_moyenne_generale(self, el, data):
        """Annonce tout changement de la moyenne générale d'une période
        (onglet « Moyennes » d'ED : recalculée par l'établissement, cf.
        dateCalcul). Premier passage pour un élève = mémorisation seule.
        Seule la moyenne de l'élève déclenche ; classe/min/max sont joints."""
        eid = str(el.get("id"))
        known = self.state.setdefault("moyennes", {})
        first_run = eid not in known
        prev = known.setdefault(eid, {})
        for p in data.get("periodes", []):
            if p.get("annuel"):
                continue
            ens = p.get("ensembleMatieres", {}) or {}
            mg = ens.get("moyenneGenerale")
            if mg in (None, ""):
                continue
            pid = str(p.get("idPeriode") or p.get("periode"))
            old = prev.get(pid)
            prev[pid] = mg
            if first_run or old == mg:
                continue
            sur = (data.get("parametrage") or {}).get("moyenneSur") or 20
            self.notify(fmt_moyenne_generale(el, p, ens, old, sur))

    # -- transfert de la messagerie vers une boîte mail --
    def _forward_mail(self, accounts):
        """Retourne les erreurs côté EcoleDirecte (traitées comme une
        indisponibilité par l'appelant). Les erreurs d'envoi du mail (SMTP,
        mot de passe Gmail) sont, elles, signalées tout de suite. Une
        exception (liste des messages illisible) remonte à l'appelant."""
        if not self.cfg.has_section("mail"):
            return []
        import edmail  # import tardif, comme edsync
        res = edmail.run_forward(self.ed, accounts, self.state,
                                 self.cfg["mail"], log=lambda m: print(m, flush=True))
        errors = res["errors"]
        if res["initialized"]:
            self.notify("📧 Transfert de la messagerie EcoleDirecte activé : "
                        "les prochains messages reçus seront envoyés par mail.")
        # Dédup propre au transfert : `last_error` est remis à zéro à chaque
        # connexion réussie, il ne protégerait pas d'un échec SMTP persistant
        # (même message d'erreur toutes les heures).
        text = "\n".join(errors)
        if text:
            print("Envoi mail en échec :", text, flush=True)
        if text and self.state.get("last_mail_error") != text:
            self.notify("⚠️ Transfert messagerie EcoleDirecte, envoi du mail "
                        "impossible :\n" + html.escape(text))
        self.state["last_mail_error"] = text or None
        save_state(self.state)
        return res["ed_errors"]

    # -- dispatch --
    def handle_update(self, upd):
        if "callback_query" in upd:
            cb = upd["callback_query"]
            chat_id = cb["message"]["chat"]["id"]
            if not self.is_allowed(chat_id):
                return
            data = cb.get("data", "")
            if data.startswith("fa:"):
                self.handle_2fa_choice(int(data[3:]), cb["id"])
            return

        msg = upd.get("message") or upd.get("edited_message")
        if not msg or "text" not in msg:
            return
        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()
        cmd = text.split()[0].lstrip("/").split("@")[0].lower()

        if cmd in ("start", "help"):
            self.tg.send(chat_id,
                         "🎓 <b>Bot Notes EcoleDirecte</b>\n\n"
                         "/notes — moyennes + dernières notes\n"
                         "/moyennes — moyennes par matière\n"
                         "/recent — 10 dernières notes\n"
                         "/check — forcer une vérification\n"
                         "/sync — archiver les nouveaux documents sur le partage")
            return

        if not self.is_allowed(chat_id):
            self.tg.send(chat_id, "⛔ Accès non autorisé.")
            return

        if cmd == "notes":
            self.cmd_notes(chat_id, "notes")
        elif cmd == "moyennes":
            self.cmd_notes(chat_id, "moyennes")
        elif cmd == "recent":
            self.cmd_notes(chat_id, "recent")
        elif cmd == "check":
            self.tg.send(chat_id, "🔎 Vérification en cours…")
            self.cmd_check(chat_id)
        elif cmd == "sync":
            self.tg.send(chat_id, "🔄 Synchronisation des documents…")
            self.cmd_sync(chat_id)
        else:
            self.tg.send(chat_id, "Commande inconnue. /help")

    # -- boucle principale --
    def run(self):
        print("Bot démarré.", flush=True)
        offset = self.state.get("tg_offset", 0)
        while True:
            try:
                # Polling horaire des notes
                if time.time() - self.state.get("last_poll", 0) >= self.interval \
                        and not self.pending:
                    self.poll_new_notes()

                res = self.tg.get_updates(offset, timeout=30)
                if res.get("ok"):
                    for upd in res["result"]:
                        offset = upd["update_id"] + 1
                        self.state["tg_offset"] = offset
                        try:
                            self.handle_update(upd)
                        except Exception as e:
                            print("Erreur handle_update:", e, flush=True)
                    save_state(self.state)
                else:
                    time.sleep(3)
            except Exception as e:
                print("Erreur boucle:", e, flush=True)
                time.sleep(5)


if __name__ == "__main__":
    Bot().run()
