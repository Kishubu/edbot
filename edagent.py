#!/usr/bin/env python3
"""
Agent d'orchestration pour Edbot (mode langage naturel via WhatsApp).

Principes :
- Recoit un texte utilisateur en langage naturel (ex: "Quel est ma derniere
  note en math ?").
- Interroge le LLM Venice.ai pour traduire l'intention en commande
  edbot_cmd.py (notes, recent, check, sync, messages, 2fa, help).
- Le LLM repond avec un JSON structure : { cmd, args, reponse_utilisateur,
  attend_saisie_utilisateur }.
- Si le LLM demande une commande, edagent lance edbot_cmd.py via subprocess
  et retourne le resultat brut a l'appelant (whatsapp/index.js).
- Si le LLM repond directement (pas de commande), edagent retourne sa reponse
  en langage naturel.
- Gere le cycle 2FA : si edbot_cmd.py signale une verification de securite,
  edagent stocke l'etat pending_2fa dans state.json et reformule la question
  pour l'utilisateur. La reponse suivante de l'utilisateur est interpretee
  comme une reponse 2FA et la commande initiale est rejouee.

Usage (depuis WhatsApp/index.js) :
    python3 edagent.py "<texte utilisateur>"
Affiche sur stdout :
    - Soit le texte a renvoyer a l'utilisateur (deja formate en francais)
    - Soit une ligne JSON speciale si une attente de saisie 2FA est necessaire
      (voir ATTEND_2FA_PREFIX).
"""

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import edbot as ed
import edllm

# --- Constantes -----------------------------------------------------------

EDBOT_CMD = os.path.join(HERE, "edbot_cmd.py")

# Prefixe emis sur stdout quand l'agent attend une saisie utilisateur
# (ex: reponse a une question de securite 2FA). Le bot WhatsApp doit alors
# ne PAS executer de commande Python, juste afficher le texte suivant.
ATTEND_2FA_PREFIX = "__EDAGENT_ATTEND_2FA__"

# Commandes qui peuvent declencher une 2FA et donc etre rejouees apres.
REPLAYABLE_CMDS = ("notes", "recent", "check", "sync", "messages")


def load_state():
    return ed.load_state()


def save_state(state):
    ed.save_state(state)


def build_system_prompt():
    """Construit le prompt system decrivant les commandes disponibles."""
    return (
        "Tu es l'assistant du bot Edbot EcoleDirecte sur WhatsApp. "
        "L'utilisateur t'ecrit en francais en langage naturel. "
        "Ton but est de traduire son intention en une des commandes suivantes "
        "et de repondre en francais de maniere naturelle et concise.\n\n"
        "Commandes disponibles :\n"
        "- notes : afficher les moyennes et dernieres notes d'un eleve\n"
        "- recent : afficher les dernieres notes\n"
        "- check : verifier immediatement s'il y a de nouvelles notes\n"
        "- sync : archiver les nouveaux documents (bulletins, factures, etc.)\n"
        "- messages : afficher les derniers messages EcoleDirecte\n"
        "- 2fa : repondre a une question de verification de securite (numero de proposition)\n"
        "- help : afficher l'aide avec la liste des commandes\n\n"
        "Si aucune action precise n'est demandee, reponds directement sans commande (cmd: null).\n"
        "Reponds avec un JSON valide uniquement : {\"cmd\": ..., \"args\": [], \"reponse_utilisateur\": \"...\"}.\n"
    )


def _call_llm(user_text):
    """Interroge le LLM et renvoie un dict {cmd, args, reponse_utilisateur}."""
    try:
        api_key = edllm.get_api_key()
    except Exception:
        api_key = None
    if not api_key:
        return {
            "cmd": None,
            "args": [],
            "reponse_utilisateur": (
                "Je ne suis pas configure pour utiliser l'assistant IA : "
                "la clef API Venice.ai est manquante. "
                "Utilise les commandes directes !notes, !moyennes, !recent, etc."
            ),
        }

    try:
        raw = edllm.chat(build_system_prompt(), user_text)
    except Exception as e:
        return {
            "cmd": None,
            "args": [],
            "reponse_utilisateur": (
                "Desole, l'assistant IA est temporairement indisponible "
                "(%s). Utilise les commandes directes !notes, !moyennes, !recent, etc."
            ) % str(e),
        }

    json_text = _extract_json(raw)
    if json_text is None:
        return {
            "cmd": None,
            "args": [],
            "reponse_utilisateur": raw.strip() or "Je n'ai pas compris ta demande.",
        }

    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return {
            "cmd": None,
            "args": [],
            "reponse_utilisateur": raw.strip() or "Je n'ai pas compris ta demande.",
        }

    cmd = data.get("cmd")
    args = data.get("args", []) or []
    if not isinstance(args, list):
        args = [str(args)]
    reponse = data.get("reponse_utilisateur") or ""
    if not reponse:
        reponse = "Je traite ta demande..."

    return {"cmd": cmd, "args": args, "reponse_utilisateur": reponse}


def _extract_json(text):
    """Extrait le premier bloc JSON valide d'un texte (accolades balancees)."""
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _run_edbot_cmd(cmd, args):
    """Execute edbot_cmd.py avec la commande donnee et renvoie (stdout, erreur)."""
    python_cmd = ["python3", EDBOT_CMD, cmd] + [str(a) for a in args]
    try:
        proc = subprocess.run(
            python_cmd,
            cwd=HERE,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return None, "Le traitement a pris trop de temps (plus de 3 minutes)."
    except FileNotFoundError:
        return None, "Impossible de trouver edbot_cmd.py."
    except Exception as e:  # noqa: BLE001
        return None, "Erreur lors de l'execution : %s" % str(e)

    if proc.returncode != 0:
        output = proc.stdout.strip()
        if not output:
            output = "Erreur inconnue (code %d)." % proc.returncode
        if proc.stderr.strip():
            output += "\n(detail : %s)" % proc.stderr.strip()
        return None, output

    return proc.stdout.strip(), None


def _is_2fa_required(output):
    """Detecte si edbot_cmd.py demande une verification de securite 2FA."""
    if not output:
        return False
    return output.startswith("🔐") or "Vérification de sécurité" in output


def _extract_2fa_question(output):
    """Extrait la question et les propositions d'un output 2FA."""
    lines = output.splitlines()
    question = ""
    propositions = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("🔐"):
            continue
        if not stripped:
            continue
        if not question and not re.match(r"^\d+\.\s", stripped):
            question = stripped
            continue
        m = re.match(r"^(\d+)\.\s(.+)$", stripped)
        if m:
            propositions.append((int(m.group(1)), m.group(2).strip()))
    return question, propositions


def handle(user_text, state=None):
    """
    Point d'entree principal. Traite un texte utilisateur et renvoie le texte
    a renvoyer sur WhatsApp (deja formate, sans le BOT_TAG).

    Si une attente de 2FA est detectee, renvoie une ligne speciale :
        __EDAGENT_ATTEND_2FA__
    suivi du texte a afficher, et l'appelant doit stocker l'etat pending_2fa.
    """
    if state is None:
        state = load_state()

    # --- Cas 1 : l'utilisateur repond a une question de securite 2FA en attente ---
    pending = state.get("pending_2fa")
    if pending:
        return _handle_2fa_response(user_text, pending, state)

    # --- Cas 2 : langage naturel -> LLM decide commande ---
    llm = _call_llm(user_text)
    cmd = llm.get("cmd")
    args = llm.get("args", [])
    reponse_intermediaire = llm.get("reponse_utilisateur", "")

    if not cmd:
        # Le LLM repond directement (pas de commande).
        return reponse_intermediaire or "Je n'ai pas compris ta demande."

    # On execute la commande demandee.
    output, err = _run_edbot_cmd(cmd, args)

    if err:
        # Erreur d'execution : on la renvoie telle quelle a l'utilisateur.
        return reponse_intermediaire + "\n\n" + err if reponse_intermediaire else err

    # --- Cas 2a : 2FA requise par edbot_cmd.py ---
    if _is_2fa_required(output):
        question, propositions = _extract_2fa_question(output)
        # Sauvegarde l'etat pour pouvoir rejouer la commande apres reponse.
        pending = {
            "question": question,
            "propositions": propositions,
            "token": None,  # edbot_cmd.py gere le token en interne
            "command": cmd if cmd in REPLAYABLE_CMDS else "notes",
            "args": args,
        }
        state["pending_2fa"] = pending
        save_state(state)

        # Reformule la question pour l'utilisateur en francais naturel.
        lignes = [reponse_intermediaire] if reponse_intermediaire else []
        lignes.append("🔐 École Directe demande une verification de securite :")
        if question:
            lignes.append("")
            lignes.append(question)
        if propositions:
            lignes.append("")
            for num, val in propositions:
                lignes.append("%d. %s" % (num, val))
        lignes.append("")
        lignes.append("Reponds avec le numero de la bonne reponse (ex: 2).")

        return ATTEND_2FA_PREFIX + "\n" + "\n".join(lignes)

    # --- Cas 2b : commande executee avec succes ---
    if output:
        return reponse_intermediaire + "\n\n" + output if reponse_intermediaire else output
    return reponse_intermediaire or "Aucune réponse."


def _handle_2fa_response(user_text, pending, state):
    """Traite la reponse de l'utilisateur a une question de securite 2FA."""
    m = re.match(r"^\s*(\d+)\s*$", user_text)
    if not m:
        return (
            "Merci de repondre avec le numero de la bonne proposition "
            "(ex: 2)."
        )

    idx = int(m.group(1))
    # Rejoue la commande 2fa avec confirmation.
    output, err = _run_edbot_cmd("2fa", [str(idx), "confirm"])

    if err:
        return "Desole, une erreur est survenue : %s" % err

    # Supprime l'etat pending : la commande est terminee.
    state.pop("pending_2fa", None)
    save_state(state)

    return output or "✅ Verification reussie."


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: edagent.py \"<texte utilisateur>\"")
        sys.exit(1)
    user_text = sys.argv[1]
    print(handle(user_text))
