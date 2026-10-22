#!/usr/bin/env python3
"""
Pré-remplir la réponse à la question de sécurité EcoleDirecte, sans Telegram.

Se connecte avec les identifiants de config.ini, affiche la question et les
propositions, enregistre le choix dans state.json (twofa_answers + cn/cv).
Le bot rejouera ensuite cette réponse tout seul si la même question revient.

    docker compose run --rm edbot python3 ed2fa.py

ATTENTION : une seule mauvaise réponse verrouille le compte École Directe.
Ce script demande donc une double confirmation avant l'envoi, et respecte
le cooldown / l'état de blocage éventuellement en cours.
"""
import edbot as ed


def main():
    cfg = ed.load_config()
    state = ed.load_state()
    sess = ed.EDSession(cfg["ecoledirecte"]["identifiant"],
                        cfg["ecoledirecte"]["motdepasse"], state)

    # Garde-fou : rien n'est envoyé si un cooldown est armé ou si le compte
    # est bloqué. Il faut d'abord déblocquer le compte et lancer
    # `edbot_cmd.py 2fa reset` (ou /2fa reset sur Telegram).
    if ed.twofa_fige(state):
        raise SystemExit(ed.twofa_message_fige(state))

    status, payload = sess.ensure_logged_in()
    if status == "fige":
        raise SystemExit(ed.twofa_message_fige(state))
    if status == "ok":
        ed.save_state(state)
        print("Connexion OK : aucune question posée (réponse déjà connue).")
        return
    if status != "2fa":
        raise SystemExit(f"Échec : {payload}")
    print("Question :", payload["question"])
    props = payload["propositions"]
    for i, p in enumerate(props, 1):
        print(f"  {i}. {ed._b64d(p)}")
    idx = int(input("Numéro de la bonne réponse : ")) - 1
    if not 0 <= idx < len(props):
        raise SystemExit("Choix invalide.")
    # Double confirmation : une seule mauvaise réponse verrouille le compte.
    label = ed._b64d(props[idx])
    rep = input(f"Confirmer « {label} » ? (oui/non) : ").strip().lower()
    if rep not in ("oui", "o", "yes", "y"):
        raise SystemExit("Annulé : aucune réponse n'a été envoyée.")
    rep = input("Dernière confirmation : tapez OUI en majuscules : ").strip()
    if rep != "OUI":
        raise SystemExit("Annulé : aucune réponse n'a été envoyée.")

    status, msg = sess.submit_2fa(payload["token"], props[idx])
    if status == "erreur":
        # Panne réseau : ce n'est pas une mauvaise réponse, rien n'est gelé.
        raise SystemExit(f"Erreur réseau (aucune réponse retenue) : {msg}")
    if status == "fige":
        raise SystemExit(ed.twofa_message_fige(state))
    if status == "refus":
        # Réponse fausse : elle n'est PAS mémorisée, cooldown armé.
        raise SystemExit(f"Réponse refusée : {msg}\n\n"
                         "Aucune réponse n'a été mémorisée. Nouvelle tentative "
                         "possible après le cooldown.")
    if status != "ok":
        raise SystemExit(f"Réponse refusée : {msg}")
    ed.twofa_memoriser(state, payload["question"], props[idx])
    ed.save_state(state)
    print("Réponse enregistrée dans state.json.")


if __name__ == "__main__":
    main()
