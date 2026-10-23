#!/usr/bin/env python3
"""
Wrapper CLI pour les commandes EcoleDirecte (utilisé par le bot WhatsApp).

Usage: python3 edbot_cmd.py <commande>

Commandes disponibles :
  notes    — moyennes + dernières notes
  moyennes — moyennes par matière
  recent   — 10 dernières notes
  check    — forcer une vérification immédiate des nouvelles notes
  sync     — archiver les nouveaux documents (bulletins, factures, etc.)

Ce module importe `edbot` (logique métier existante) et réutilise
`EDSession` pour se connecter à l'API École Directe. Aucun envoi Telegram
n'est effectué : le résultat est affiché sur la sortie standard pour être
relayé par le bot WhatsApp.
"""
import sys
import os
import time
import html

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import edbot as ed


def strip_html(text):
    """Convertit le formatage HTML Telegram en texte simple compatible WhatsApp."""
    if not text:
        return ''
    text = text.replace('<b>', '*').replace('</b>', '*')
    text = text.replace('<i>', '_').replace('</i>', '_')
    text = text.replace('&#x27;', "'").replace('&quot;', '"')
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    return text


def fmt_simple_note(n):
    """Formatage minimal d'une note pour WhatsApp."""
    eleve = n.get('eleve', '')
    matiere = n.get('matiere', '')
    note = n.get('note', '')
    coef = n.get('coefficient', '')
    date = n.get('date', '')
    ligne = f'*{matiere}* : {note}'
    if coef:
        ligne += f' (coef {coef})'
    if date:
        ligne += f' — {date}'
    if eleve:
        ligne = f'[{eleve}] ' + ligne
    return ligne


def cmd_notes(sess, accounts, kind):
    """Retourne les notes / moyennes / récentes pour tous les élèves."""
    eleves = ed.eleves_from_accounts(accounts)
    if not eleves:
        return 'Aucun élève trouvé.'

    lines = []
    for el in eleves:
        data = sess.fetch_notes(el.get('id'))
        if not data:
            lines.append(f'Notes indisponibles pour {el.get("prenom", "?")}.')
            continue

        prenom = el.get('prenom', '')
        if kind == 'moyennes':
            lines.append(strip_html(ed.fmt_moyennes(el, data)))
        elif kind == 'recent':
            lines.append(strip_html(ed.fmt_recent(el, data)))
        else:  # notes complètes
            lines.append(strip_html(ed.fmt_moyennes(el, data)))
            lines.append(strip_html(ed.fmt_recent(el, data, limit=100)))

    return '\n\n'.join(lines)


def cmd_check(sess, accounts):
    """Vérifie les nouvelles notes et renvoie le résultat."""
    eleves = ed.eleves_from_accounts(accounts)
    seen = set(sess.state.get('seen_notes', []))
    first_run = not sess.state.get('initialized')
    new_count = 0
    lines = []

    for el in eleves:
        data = sess.fetch_notes(el.get('id'))
        if data is None:
            lines.append(f'Notes indisponibles pour {el.get("prenom", "?")}.')
            continue
        for n in data.get('notes', []):
            nid = str(n.get('id'))
            if nid in seen:
                continue
            if not ed.has_note(n):
                continue
            seen.add(nid)
            if not first_run:
                lines.append(f'🆕 Nouvelle note — {el.get("prenom", "")}\n{strip_html(ed.fmt_note_line(n))}')
                new_count += 1
        # Rejoue la détection de changement de moyenne générale (sans notification)
        
        # Logique de _check_moyenne_generale adaptée (sans Telegram)
        eid = str(el.get("id"))
        known = sess.state.setdefault("moyennes", {})
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
            print(f"📈 Moyenne générale changeante pour {el.get('prenom', '?')} : {mg} (was {old}, sur {sur})")


    sess.state['seen_notes'] = sorted(seen)
    sess.state['initialized'] = True
    sess.state['last_poll'] = int(time.time())
    ed.save_state(sess.state)

    if first_run:
        lines.append('✅ Surveillance initialisée. Tu seras notifié des prochaines nouvelles notes.')
    elif new_count:
        lines.insert(0, f'✅ Vérification terminée — {new_count} nouvelle(s) note(s).')
    else:
        lines.insert(0, '✅ Vérification terminée — aucune nouvelle note.')

    return '\n\n'.join(lines)


def cmd_sync(sess, accounts):
    """Archive les nouveaux documents EcoleDirecte (via edsync)."""
    import edsync
    try:
        res = edsync.run_sync(sess, accounts, log=lambda m: None)
    except Exception as e:
        return f'⚠️ Sync échouée : {e}'

    new = res['new']
    lines = [
        '✅ *Sync EcoleDirecte terminée*',
        f'📁 {len(new)} nouveau(x) · {res["existing"]} déjà présents · {res["total"]} au total'
    ]
    if new:
        groups = {}
        for d in new:
            groups.setdefault((d.get('eleve', 'Famille'), d['annee'], d['categorie']), []).append(d)
        for (eleve, annee, cat), docs in sorted(groups.items(), reverse=True):
            label = edsync.CAT_DIRS.get(cat, cat)
            lines.append(f'\n{eleve} — {annee} — {label} ({len(docs)})')
            for d in docs[:20]:
                lines.append(f'  • {d.get("date", "")} — {d.get("libelle", "")}')
    else:
        lines.append('\nAucun nouveau document.')
    if res['inscriptions']:
        lines.append(f'\nℹ️ {res["inscriptions"]} doc(s) d\'inscription non téléchargeables.')
    if res['errors']:
        lines.append(f'\n⚠️ {len(res["errors"])} erreur(s) :')
        for e in res['errors'][:5]:
            lines.append(f'  • {e}')

    return '\n'.join(lines)


HELP_TEXT = (
    '*🎓 Edbot — Notes EcoleDirecte*\n\n'
    '*!notes* — moyennes + dernières notes\n'
    '*!moyennes* — moyennes par matière\n'
    '*!recent* — 10 dernières notes\n'
    '*!check* — forcer une vérification immédiate\n'
    '*!sync* — archiver les nouveaux documents\n'
    '*!2fa <n>* — répondre à la question de sécurité '
    '(confirmation exigée : *!2fa <n> confirm*)\n'
    '*!2fa reset* — réarmer la vérification de sécurité '
    '(après déblocage manuel du compte ED)'
    '*!messages* — dernier message EcoleDirecte\\n'
)

def cmd_messages(sess, accounts):
    """Récupère la liste des derniers messages EcoleDirecte via l'API /messages.awp.

    Affiche les 5 derniers messages. L'ED marque les messages comme lus
    à la lecture.
    """
    from edbot import ED_BASE, ED_VERSION, UA
    import urllib.request
    import json

    eleves = ed.eleves_from_accounts(accounts)
    if not eleves:
        return 'Aucun élève trouvé.'
    el = eleves[0]
    eleve_id = str(el.get("id"))

    # L'API École Directe pour la messagerie du compte parent utilise
    # l'ID de la FAMILLE (account parent, typeCompte 1 ou 2), pas l'ID élève.
    famille_id = None
    if accounts and accounts[0].get("typeCompte") in ("1", "2"):
        famille_id = str(accounts[0].get("id"))
    if not famille_id:
        famille_id = eleve_id  # fallback si pas de compte parent dispo

    # --- DEBUG temporaire ---
    acc0 = accounts[0] if accounts else {}
    print(f"[DEBUG cmd_messages] typeCompte={acc0.get('typeCompte')!r} (type={type(acc0.get('typeCompte')).__name__})", file=sys.stderr)
    print(f"[DEBUG cmd_messages] accounts[0].id={acc0.get('id')!r}", file=sys.stderr)
    print(f"[DEBUG cmd_messages] famille_id={famille_id} eleve_id={eleve_id} token={sess.token!r}", file=sys.stderr)

    # API réelle (documentée dans API-EcoleDirecte.md) :
    # POST /v3/familles/{idFamille}/messages.awp?force=false&typeRecuperation=received&...
    # data = {"anneeMessages": "2026-2027"}
    # Les messages sont dans data.messages.received[]
    url = (
        f"{ED_BASE}/v3/familles/{famille_id}/messages.awp"
        "?force=false&typeRecuperation=received&idClasseur=0&orderBy=date"
        "&order=desc&query=&onlyRead=&page=0&itemsPerPage=100&getAll=0&verbe=get&v=4.100.1"
    )
    payload = {"anneeMessages": "2026-2027"}
    body = "data=" + json.dumps(payload)
    try:
        req = urllib.request.Request(
            url,
            data=body.encode(),
            method="POST",
            headers={
                "User-Agent": UA,
                "Content-Type": "text/plain",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://www.ecoledirecte.com",
                "Referer": "https://www.ecoledirecte.com/",
                "X-Token": sess.token or "",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            print(f"[DEBUG cmd_messages] raw API response: {raw[:2000]}", file=sys.stderr)
            data = json.loads(raw)
    except Exception as e:
        return f'⚠️ Erreur récupération messages : {e}'

    # La réponse est : {"code":200, "token":..., "data": {"messages": {"received": [...]}}}
    data_block = data.get("data") or {}
    messages_blob = data_block.get("messages") or {}
    received_list = messages_blob.get("received") or []
    print(f"[DEBUG cmd_messages] type(data)=dict data_keys={list(data.keys())}", file=sys.stderr)
    print(f"[DEBUG cmd_messages] nombre de messages reçus: {len(received_list)}", file=sys.stderr)
    if not received_list:
        return 'Aucun message nouveau.'

    # Afficher les 5 derniers messages (les plus récents en premier)
    MAX_DISPLAY = 5
    recent = received_list[-MAX_DISPLAY:][::-1]
    parts = []
    parts.append(
        "📨 *" + str(len(received_list)) + " message(s) au total - affichage des " + str(len(recent)) + " plus récents*"
    )
    for i, last in enumerate(recent):
        date = last.get("date", "")
        frm = last.get("from") or {}
        prenom = str(frm.get("prenom", ""))
        nom = str(frm.get("nom", ""))
        expediteur = (f"{frm.get('civilite','')} {prenom} {nom}").strip() or str(frm.get("nom", "inconnu"))
        subject = last.get("subject", "")
        entry = "\n\n--- Message " + str(i + 1) + " ---\n📅 " + str(date) + "\nDe : " + expediteur + "\n" + str(subject)
        parts.append(entry)
    result = "".join(parts)
    max_len = 3700
    if len(result) > max_len:
        result = result[:max_len] + "\n… (truncated)"
    return result


def _duree(secs):
    """Durée lisible (identique à edbot._duree mais en local)."""
    secs = max(0, int(secs))
    if secs < 3600:
        return f"{secs // 60} min"
    if secs < 48 * 3600:
        return f"{secs // 3600} h {secs % 3600 // 60:02d}"
    return f"{secs // 86400} j {secs % 86400 // 3600} h"


def _cooldown_reste(state):
    """Temps restant de cooldown, en texte."""
    until = int(state.get('twofa_cooldown_until') or 0)
    return _duree(until - int(time.time()))


def _fige_texte(state):
    """Message (format WhatsApp) expliquant pourquoi rien n'est envoyé."""
    if ed.twofa_bloque(state):
        return ('⛔ *Compte École Directe bloqué*\n\n'
                'Le compte a été verrouillé par École Directe.\n\n'
                'Aucune tentative n\'est faite tant que tu n\'as pas débloqué '
                'le compte et réinitialisé la protection, puis lancé '
                '*!2fa reset*.')
    return ('⏳ *Vérification de sécurité en pause*\n\n'
            f'Une réponse a été refusée récemment : nouvelle tentative possible '
            f'dans {_cooldown_reste(state)}.\n\n'
            'Le compte se bloque sur une seule mauvaise réponse : on ne tente '
            'plus rien tout seul.')


def cmd_2fa(sess, state, index, action=None):
    """Répond à la question de sécurité en attente et rejoue la commande.

    L'index (1-based) correspond à la proposition affichée par le bot.

    Garde-fous (le compte se bloque sur UNE seule mauvaise réponse) :
      - aucune soumission si un cooldown est armé ou si le compte est bloqué ;
      - confirmation explicite obligatoire (`!2fa <n> confirm`) ;
      - une réponse refusée n'est jamais mémorisée.

    `action` vaut None (1er envoi : on demande confirmation), "confirm"
    (la réponse est validée et envoyée) ou "cancel" (abandon).
    """
    pending = state.get('pending_2fa')
    if not pending:
        return '❓ Aucune vérification de sécurité en attente.'

    # Garde-fou 1 : cooldown / compte bloqué -> rien n'est envoyé.
    if ed.twofa_fige(state):
        return _fige_texte(state)

    props = pending.get('propositions', [])
    if not (1 <= index <= len(props)):
        return f'❓ Choix invalide (1 à {len(props)}).'

    chosen_raw = props[index - 1]
    label = ed._b64d(chosen_raw) if chosen_raw else ''

    # Annulation : aucune réponse n'est envoyée, la question est purgée.
    if action == 'cancel':
        state.pop('pending_2fa', None)
        ed.save_state(state)
        return '↩️ Vérification de sécurité annulée : aucune réponse ' \
               'n\'a été envoyée.'

    # Garde-fou 2 : confirmation explicite. Sans elle, un simple
    # « !2fa 1 » suffirait à bloquer le compte.
    if action != 'confirm':
        pending['confirmed'] = True
        pending['pending_index'] = index
        ed.save_state(state)
        return (f'⚠️ *Confirmation requise*\n\n'
                f'Réponse sélectionnée : *{label}*\n\n'
                f'Une seule mauvaise réponse verrouille le compte École Directe.\n'
                f'Relance avec *!2fa {index} confirm* pour valider, '
                f'!*2fa {index} cancel* pour annuler.')

    # La confirmation porte sur un numéro : on ne valide que si c'est
    # exactement celui qui a été annoncé.
    if pending.get('pending_index') != index:
        pending['confirmed'] = False
        ed.save_state(state)
        attendu = pending.get('pending_index')
        if attendu is None:
            # Aucune confirmation en attente : on rappelle la marche à
            # suivre au lieu d'annoncer un « None ».
            return (f'❓ Aucune confirmation en attente.\n'
                    f'Reprends par *!2fa {index}* pour sélectionner la '
                    f'réponse, puis *!2fa {index} confirm* pour valider.')
        return (f'❓ Confirmation attendue pour le choix '
                f'{attendu}. '
                f'Reprends par *!2fa {attendu} confirm*.')

    state.pop('pending_2fa', None)
    status, payload = sess.submit_2fa(pending['token'], chosen_raw)

    if status == 'erreur':
        # Panne réseau : ce n'est pas une mauvaise réponse, rien n'est gelé.
        ed.save_state(state)
        return f'⚠️ {payload}'

    if status == 'fige':
        ed.save_state(state)
        return _fige_texte(state)

    if status == 'refus':
        # Mauvaise réponse : elle n'est PAS mémorisée, cooldown armé.
        # Défense en profondeur : on arme le cooldown ici aussi si la
        # session ne l'a pas fait.
        if not ed.twofa_cooldown_actif(state) and not ed.twofa_bloque(state):
            ed.twofa_echec(state)
        state.get('twofa_answers', {}).pop(pending['question'], None)
        ed.save_state(state)
        return (f'⚠️ *Réponse refusée* : {payload}\n\n'
                f'⏳ Aucune nouvelle tentative automatique pendant '
                f'{_cooldown_reste(state)}.')

    if status != 'ok':
        ed.save_state(state)
        return f'⚠️ Réponse refusée : {payload}'

    # On mémorise la réponse pour la prochaine fois.
    ed.twofa_memoriser(state, pending['question'], chosen_raw)
    ed.save_state(state)

    resume_cmd = pending.get('command', 'notes')
    accounts = payload if isinstance(payload, list) else \
        payload.get('data', {}).get('accounts', [])
    if resume_cmd in ('notes', 'moyennes', 'recent'):
        body = cmd_notes(sess, accounts, resume_cmd)
    elif resume_cmd == 'check':
        body = cmd_check(sess, accounts)
    elif resume_cmd == 'sync':
        body = cmd_sync(sess, accounts)
    else:
        body = '✅ Vérification réussie.'

    return '✅ Vérification réussie.\n\n' + body


def main():
    if len(sys.argv) < 2:
        print('Usage: edbot_cmd.py <commande>')
        print(HELP_TEXT)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    cfg = ed.load_config()
    state = ed.load_state()

    sess = ed.EDSession(
        cfg['ecoledirecte']['identifiant'],
        cfg['ecoledirecte']['motdepasse'],
        state
    )

    # --- Commande de réponse au 2FA (cas particulier : geree avant login) ---
    if cmd == '2fa':
        args = sys.argv[2:]
        # `!2fa reset` : réarme la vérification de sécurité (après un
        # déblocage manuel du compte École Directe).
        if args and args[0].lower() == 'reset':
            state.pop('twofa_cooldown_until', None)
            state.pop('twofa_bloque', None)
            state.pop('twofa_bloque_since', None)
            state['twofa_failures'] = 0
            state['twofa_answers'] = {}
            state['cn'] = None
            state['cv'] = None
            state.pop('pending_2fa', None)
            ed.save_state(state)
            print('✅ Vérification de sécurité réarmée.\n\n'
                  'Les réponses mémorisées ont été effacées : la prochaine '
                  'connexion reposera la question, et la réponse sera '
                  'demandée par confirmation explicite.')
            return
        try:
            idx = int(args[0]) if args else 0
        except ValueError:
            idx = 0
        if idx < 1:
            print('❓ Réponse invalide. Usage : !2fa <numéro> '
                  '[confirm|cancel]')
            sys.exit(1)
        action = None
        if len(args) > 1:
            action = args[1].strip().lower()
            if action not in ('confirm', 'cancel'):
                print('❓ Action invalide. Usage : !2fa <numéro> '
                      '[confirm|cancel]')
                sys.exit(1)
        print(cmd_2fa(sess, state, idx, action))
        return

    status, payload = sess.ensure_logged_in()
    print(f"[DEBUG cmd_messages] ensure_logged_in -> status={status!r} payload={str(payload)[:200]}", file=sys.stderr)
    if status == 'fige':
        # Cooldown 2FA ou compte bloqué : rien n'est envoyé à ED.
        print(_fige_texte(state))
        sys.exit(0)
    if status == '2fa':
        # On memorise la question en cours et la commande a rejouer, puis on
        # demande a l'utilisateur de repondre avec !2fa <numero>.
        state['pending_2fa'] = {
            'question': payload['question'],
            'propositions': payload['propositions'],
            'token': payload['token'],
            'command': cmd if cmd in ('notes', 'moyennes', 'recent', 'check', 'sync') else 'notes',
        }
        ed.save_state(state)
        lignes = ['🔐 *Vérification de sécurité École Directe*', '',
                  payload['question'], '']
        for i, p in enumerate(payload['propositions'], 1):
            try:
                valeur = ed._b64d(p)
            except Exception:
                valeur = p
            lignes.append(f'{i}. {valeur}')
        lignes += ['', 'Réponds avec *!2fa <numéro>* (numéro de la bonne '
                   'réponse).', '',
                   '⚠️ Une seule mauvaise réponse verrouille le compte : une '
                   'confirmation te sera demandée avant tout envoi.']
        print('\n'.join(lignes))
        sys.exit(0)

    if status == 'auth':
        print(f'🔑 {payload}')
        sys.exit(1)

    if status != 'ok':
        print(f'❌ Connexion École Directe échouée : {payload}')
        sys.exit(1)

    ed.save_state(state)
    # ensure_logged_in() renvoie la LISTE des comptes (et non un dict) :
    # on accepte les deux formes pour rester tolérant.
    if isinstance(payload, list):
        accounts = payload
    else:
        accounts = (payload or {}).get('data', {}).get('accounts', [])

    if cmd in ('notes', 'moyennes', 'recent'):
        result = cmd_notes(sess, accounts, cmd)
    elif cmd == 'messages':
        result = cmd_messages(sess, accounts)
    elif cmd == 'check':
        result = cmd_check(sess, accounts)
    elif cmd == 'sync':
        result = cmd_sync(sess, accounts)
    elif cmd in ('help', '--help', '-h'):
        result = HELP_TEXT
    else:
        result = '❓ Commande inconnue.\n\n' + HELP_TEXT

    print(result)


if __name__ == '__main__':
    main()
