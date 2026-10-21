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
            lines.append(strip_html(ed.fmt_recent(el, data, limit=15)))

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
        sess._check_moyenne_generale(el, data)

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
    '*!sync* — archiver les nouveaux documents'
)


def cmd_2fa(sess, state, index):
    """Répond à la question de sécurité en attente et rejoue la commande.

    L'index (1-based) correspond à la proposition affichée par le bot.
    """
    pending = state.get('pending_2fa')
    if not pending:
        return '❓ Aucune vérification de sécurité en attente.'

    props = pending.get('propositions', [])
    if not (1 <= index <= len(props)):
        return f'❓ Choix invalide (1 à {len(props)}).'

    chosen_raw = props[index - 1]
    status, payload = sess.submit_2fa(pending['token'], chosen_raw)
    if status != 'ok':
        state.pop('pending_2fa', None)
        ed.save_state(state)
        return f'⚠️ Réponse refusée : {payload}'

    # On mémorise la réponse pour la prochaine fois.
    state.setdefault('twofa_answers', {})[pending['question']] = chosen_raw
    state.pop('pending_2fa', None)
    ed.save_state(state)

    resume_cmd = pending.get('command', 'notes')
    accounts = payload.get('data', {}).get('accounts', [])
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
        try:
            idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        except ValueError:
            print('❓ Réponse invalide. Usage : !2fa <numéro>')
            sys.exit(1)
        print(cmd_2fa(sess, state, idx))
        return

    status, payload = sess.ensure_logged_in()
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
        lignes += ['', 'Réponds avec *!2fa <numéro>* (numéro de la bonne réponse).']
        print('\n'.join(lignes))
        sys.exit(0)

    if status != 'ok':
        print(f'❌ Connexion École Directe échouée : {payload}')
        sys.exit(1)

    ed.save_state(state)
    accounts = payload.get('data', {}).get('accounts', [])

    if cmd in ('notes', 'moyennes', 'recent'):
        result = cmd_notes(sess, accounts, cmd)
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
