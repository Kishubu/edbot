#!/usr/bin/env python3
"""
Tests unitaires des garde-fous 2FA (sécurité du compte École Directe).

AUCUN appel réseau : le client HTTP est simulé (monkeypatch), aucun appel à
l'API École Directe n'est effectué.

Lancement :  python3 -m pytest test_twofa_guards.py -v
       ou : python3 test_twofa_guards.py
"""
import base64
import os
import sys
import tempfile
import time

# ISOLATION : les tests ne doivent jamais écrire dans le vrai state.json.
# On redirige STATE_PATH vers un fichier temporaire AVANT l'import d'edbot.
_TMP_STATE = os.path.join(tempfile.mkdtemp(prefix="edbot-test-"), "state.json")
os.environ["EDBOT_STATE_PATH"] = _TMP_STATE

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import edbot as ed
import edbot_cmd as ec

assert ed.STATE_PATH == _TMP_STATE, f"state non isolé : {ed.STATE_PATH}"


#  Utilitaires

def b64(s):
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


class FakeED:
    """Faux client École Directe : aucun accès réseau."""

    def __init__(self, reponses=None):
        # reponses : liste de tuples (status, payload) renvoyés par submit_2fa
        self.appels = []
        self.reponses = list(reponses or [])
        self.accounts = [{"id": "A1", "prenom": "Test"}]

    def submit_2fa(self, token, chosen_raw):
        self.appels.append((token, chosen_raw))
        if self.reponses:
            return self.reponses.pop(0)
        return "ok", self.accounts

    def ensure_logged_in(self):
        return "ok", self.accounts

    def fetch_notes(self, eleve_id):
        return {}


def etat(**kw):
    base = {"cn": None, "cv": None, "twofa_answers": {}, "seen_notes": [],
            "last_poll": 0, "initialized": False, "tg_offset": 0}
    base.update(kw)
    return base


#  Tests des helpers

def test_cooldown_defaut_24h():
    # Le cooldown par défaut est de 24 h, SAUF si la config.ini du poste
    # définit explicitement twofa_cooldown_hours. On neutralise la config
    # pour tester le défaut de façon déterministe.
    s = etat()
    cache = ed._CONFIG_CACHE
    try:
        ed._CONFIG_CACHE = None
        cp = ed.configparser.ConfigParser()
        cp.add_section("ecoledirecte")
        ed._CONFIG_CACHE = cp
        assert ed.twofa_cooldown(s) == 24 * 3600, ed.twofa_cooldown(s)
    finally:
        ed._CONFIG_CACHE = cache


def test_cooldown_depuis_state():
    s = etat(twofa_cooldown_seconds=3600)
    assert ed.twofa_cooldown(s) == 3600


def test_echec_arme_cooldown_et_purge_la_reponse():
    s = etat(twofa_answers={"Q": b64("bonne")})
    s["twofa_current_question"] = "Q"
    ed.twofa_echec(s)
    assert ed.twofa_cooldown_actif(s) is True
    assert ed.twofa_fige(s) is True
    assert s["twofa_failures"] == 1
    # La réponse fautive doit être purgée : elle ne sera pas rejouée.
    assert s["twofa_answers"].get("Q") is None


def test_reponse_memorisee_hors_propositions_non_rejouee():
    s = etat(twofa_answers={"Q": b64("Marie")})
    props = [b64("Paul"), b64("Jean")]
    assert ed.twofa_trouver_reponse(s, "Q", props) is None


def test_reponse_memorisee_normalisee_rejouee():
    # ED peut renvoyer la même réponse avec un accent/casse différent.
    s = etat(twofa_answers={"Q": b64("marie")})
    props = [b64("Paul"), b64("  Marie  ")]
    trouve = ed.twofa_trouver_reponse(s, "Q", props)
    assert trouve is not None
    assert ed.prop_norm(trouve) == ed.prop_norm(b64("marie"))


def test_ensure_logged_in_fige_si_cooldown():
    s = etat()
    ed.twofa_echec(s)
    sess = _sess_avec_reponse(s, 200)
    status, msg = sess.ensure_logged_in()
    assert status == "fige", (status, msg)
    # Aucun appel réseau : rien n'est envoyé à ED.
    assert sess.calls == []


def test_ensure_logged_in_fige_si_bloque():
    s = etat()
    ed.twofa_bloquer(s)
    sess = _sess_avec_reponse(s, 200)
    status, msg = sess.ensure_logged_in()
    assert status == "fige"
    assert sess.calls == []


def test_ensure_logged_in_reponse_memorisee_refusee_ne_redispose_pas_la_question():
    """Si la réponse mémorisée est refusée, on ne repose PAS la question
    à l'utilisateur (il risquerait de rejouer la même réponse)."""
    s = etat(twofa_answers={"Q": b64("Marie")})
    sess = ed.EDSession("u", "p", s)
    calls = []

    def fake_post(pathname, payload, token=None, use_gtk=False, fa_token=None):
        calls.append(pathname)
        if "doubleauth.awp?verbe=get" in pathname:
            return {"code": 200, "token": "T", "data": {
                "question": b64("Q"),
                "propositions": [b64("Marie"), b64("Paul")]}}
        if "doubleauth.awp" in pathname:
            # Réponse refusée par ED.
            return {"code": 505, "message": "mauvaise reponse"}
        return {"code": 200, "token": "T", "data": {"accounts": []}}

    sess.post = fake_post
    sess._do_login = lambda fa=None: {"code": 250, "token": "T"}
    status, msg = sess.ensure_logged_in()
    assert status == "fige", (status, msg)
    # Aucune nouvelle question n'est présentée.
    assert ed.twofa_fige(s) is True
    # La réponse fautive a été purgée.
    assert s["twofa_answers"].get("Q") is None


    # ED peut renvoyer la même réponse avec un accent/casse différent.
    s = etat(twofa_answers={"Q": b64("marie")})
    props = [b64("Paul"), b64("  Marie  ")]
    trouve = ed.twofa_trouver_reponse(s, "Q", props)
    assert trouve is not None
    assert ed.prop_norm(trouve) == ed.prop_norm(b64("marie"))


def test_blocage_detecte_est_definitif():
    s = etat()
    ed.twofa_bloquer(s)
    assert ed.twofa_bloque(s) is True

#  Tests de submit_2fa (client simulé)

def _sess_avec_reponse(state, code, message="", raise_exc=None):
    """Crée une EDSession dont le réseau est simulé."""
    sess = ed.EDSession("u", "p", state)
    calls = []

    def fake_post(pathname, payload, token=None, use_gtk=False, fa_token=None):
        calls.append((pathname, payload))
        if raise_exc:
            raise raise_exc
        if code == 200 and "doubleauth" in pathname:
            return {"code": 200, "data": {"cn": "CN", "cv": "CV"}}
        return {"code": code, "message": message}

    sess.post = fake_post
    sess._do_login = lambda fa=None: {
        "code": 200, "token": "T", "data": {"accounts": [{"id": "A1"}]}}
    sess.calls = calls
    return sess


def test_submit_refus_arme_cooldown_et_ne_memorise_pas():
    s = etat(twofa_answers={"Q": b64("faux")})
    s["twofa_current_question"] = "Q"
    sess = _sess_avec_reponse(s, 505, "mauvaise reponse")
    status, msg = sess.submit_2fa("tok", b64("faux"))
    assert status == "refus", (status, msg)
    assert ed.twofa_fige(s) is True
    assert s["twofa_answers"].get("Q") is None
    assert s.get("cn") is None


def test_submit_erreur_reseau_n_arme_pas_le_cooldown():
    s = etat()
    sess = _sess_avec_reponse(s, 0, raise_exc=OSError("reseau HS"))
    status, msg = sess.submit_2fa("tok", b64("x"))
    assert status == "erreur", (status, msg)
    # Une panne réseau n'est PAS une mauvaise réponse.
    assert ed.twofa_fige(s) is False
    assert s.get("twofa_failures") in (None, 0)


def test_submit_refuse_si_compte_bloque():
    s = etat()
    ed.twofa_bloquer(s)
    sess = _sess_avec_reponse(s, 200)
    status, msg = sess.submit_2fa("tok", b64("x"))
    assert status == "fige"
    # Aucun appel réseau ne doit avoir été fait.
    assert sess.calls == []


def test_submit_refuse_si_cooldown_actif():
    s = etat()
    ed.twofa_echec(s)
    sess = _sess_avec_reponse(s, 200)
    status, msg = sess.submit_2fa("tok", b64("x"))
    assert status == "fige"
    assert sess.calls == []

#  Tests du chemin WhatsApp / CLI (edbot_cmd.cmd_2fa)

def test_cmd_2fa_exige_confirmation():
    s = etat(pending_2fa={"question": "Q", "token": "tok",
                          "propositions": [b64("A"), b64("B")],
                          "command": "notes"})
    fake = FakeED()
    out = ec.cmd_2fa(fake, s, 1)
    assert "Confirmation requise" in out, out
    # Aucune soumission à ED tant que la confirmation n'est pas donnée.
    assert fake.appels == []


def test_cmd_2fa_confirm_envoie_et_memorise():
    s = etat(pending_2fa={"question": "Q", "token": "tok",
                          "propositions": [b64("A"), b64("B")],
                          "command": "notes"})
    fake = FakeED([("ok", [{"id": "A1", "prenom": "Test"}])])
    out1 = ec.cmd_2fa(fake, s, 2)          # demande de confirmation
    assert fake.appels == []
    out2 = ec.cmd_2fa(fake, s, 2, "confirm")  # confirmation -> envoi
    assert len(fake.appels) == 1
    assert fake.appels[0][1] == b64("B")
    assert s["twofa_answers"]["Q"] == b64("B")
    assert "Vérification réussie" in out2


def test_cmd_2fa_refus_ne_memorise_pas_et_gele():
    s = etat(pending_2fa={"question": "Q", "token": "tok",
                          "propositions": [b64("A"), b64("B")],
                          "command": "notes"})
    fake = FakeED([("refus", "mauvaise reponse")])
    ec.cmd_2fa(fake, s, 1)
    out = ec.cmd_2fa(fake, s, 1, "confirm")
    assert "Réponse refusée" in out, out
    assert s["twofa_answers"].get("Q") is None
    assert ed.twofa_fige(s) is True


def test_cmd_2fa_bloque_ne_soumet_pas():
    s = etat(pending_2fa={"question": "Q", "token": "tok",
                          "propositions": [b64("A")], "command": "notes"})
    ed.twofa_bloquer(s)
    fake = FakeED()
    out = ec.cmd_2fa(fake, s, 1, "confirm")
    assert fake.appels == []
    assert "bloqué" in out.lower(), out


def test_cmd_2fa_erreur_reseau_ne_gele_pas():
    s = etat(pending_2fa={"question": "Q", "token": "tok",
                          "propositions": [b64("A")], "command": "notes"})
    fake = FakeED([("erreur", "reseau HS")])
    ec.cmd_2fa(fake, s, 1)
    out = ec.cmd_2fa(fake, s, 1, "confirm")
    assert ed.twofa_fige(s) is False
    assert s["twofa_answers"].get("Q") is None


def test_cmd_2fa_confirm_mauvais_numero_refuse():
    s = etat(pending_2fa={"question": "Q", "token": "tok",
                          "propositions": [b64("A"), b64("B")],
                          "command": "notes"})
    fake = FakeED()
    ec.cmd_2fa(fake, s, 1)          # choix 1 annoncé
    out = ec.cmd_2fa(fake, s, 2, "confirm")  # on confirme le choix 2
    assert fake.appels == []


def test_cmd_2fa_index_hors_bornes_refuse():
    """Un numéro hors limites (0, négatif, trop grand) ne soumet rien."""
    s = etat(pending_2fa={"question": "Q", "token": "tok",
                          "propositions": [b64("A"), b64("B")],
                          "command": "notes"})
    fake = FakeED()
    for idx in (0, -1, 3, 99):
        out = ec.cmd_2fa(fake, s, idx, "confirm")
        assert fake.appels == [], (idx, fake.appels)
        assert "invalide" in out.lower(), (idx, out)


def test_cmd_2fa_confirm_sans_selection_prealable_refuse():
    """`!2fa 1 confirm` sans avoir d'abord choisi ne soumet rien et
    donne une consigne lisible (pas de « None » dans le message)."""
    s = etat(pending_2fa={"question": "Q", "token": "tok",
                          "propositions": [b64("A")], "command": "notes"})
    fake = FakeED()
    out = ec.cmd_2fa(fake, s, 1, "confirm")
    assert fake.appels == [], fake.appels
    assert "None" not in out, out
    assert "confirmation" in out.lower(), out
    assert "!2fa 1" in out, out




def test_cmd_2fa_cancel_envoie_rien():
    s = etat(pending_2fa={"question": "Q", "token": "tok",
                          "propositions": [b64("A")], "command": "notes"})
    fake = FakeED()
    out = ec.cmd_2fa(fake, s, 1, "cancel")
    assert fake.appels == [], fake.appels
    assert "annul" in out.lower(), out

def test_cmd_messages_aucun_eleve():
    """!messages avec aucun élève retourne un message approprié."""
    from edbot import ed
    from edbot_cmd import cmd_messages
    import json
    
    # Mock accounts without eleves
    class MockSess:
        state = {}
    
    result = cmd_messages(MockSess(), [])
    assert 'Aucun élève' in result or 'aucun' in result.lower()


def test_cmd_messages_reussi():
    """!messages rÇrécupère le dernier message avec succès."""
    """Ce test vérifie la structure du cmd_messages mais nécessite un vrai compte ED."""
    pass


#  Tests du bot Telegram (handle_2fa_choice / handle_2fa_confirm)



def test_cmd_messages_aucun_eleve():
    """!messages avec aucun élève retourne un message approprié."""
    from edbot import ed
    from edbot_cmd import cmd_messages
    import json

    # Mock accounts without eleves
    class MockSess:
        state = {}

    result = cmd_messages(MockSess(), [])
    assert 'Aucun élève' in result or 'aucun' in result.lower()


def test_cmd_messages_reussi():
    """!messages rÇrécupère le dernier message avec succès."""
    pass

