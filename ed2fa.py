#!/usr/bin/env python3
"""
Pré-remplir la réponse à la question de sécurité EcoleDirecte, sans Telegram.

Se connecte avec les identifiants de config.ini, affiche la question et les
propositions, enregistre le choix dans state.json (twofa_answers + cn/cv).
Le bot rejouera ensuite cette réponse tout seul si la même question revient.

    docker compose run --rm edbot python3 ed2fa.py
"""
import edbot as ed


def main():
    cfg = ed.load_config()
    state = ed.load_state()
    sess = ed.EDSession(cfg["ecoledirecte"]["identifiant"],
                        cfg["ecoledirecte"]["motdepasse"], state)
    status, payload = sess.ensure_logged_in()
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
    status, msg = sess.submit_2fa(payload["token"], props[idx])
    if status != "ok":
        raise SystemExit(f"Réponse refusée : {msg}")
    state.setdefault("twofa_answers", {})[payload["question"]] = props[idx]
    ed.save_state(state)
    print("Réponse enregistrée dans state.json.")


if __name__ == "__main__":
    main()
