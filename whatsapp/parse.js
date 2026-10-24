/**
 * Parsing des commandes WhatsApp.
 *
 * Module séparé de index.js pour être testable sans démarrer le client
 * WhatsApp (ni le serveur HTTP).
 *
 * Sécurité 2FA : "!2fa <n>" ne soumet RIEN. Il faut une confirmation
 * explicite ("!2fa <n> confirm"). "!2fa reset" réarme le bot après un
 * déblocage manuel du compte École Directe.
 */

const VALID_CMDS = ['notes', 'moyennes', 'recent', 'check', 'sync', 'agenda', 'devoirs', 'help', '2fa', 'messages'];
const VALID_2FA_ACTIONS = ['confirm', 'cancel'];

/**
 * @param {string} text - texte brut du message (avec ou sans "!" initial)
 * @returns {null | {cmd: string, args: string[], error: string|null}}
 *   null si le message n'est pas une commande ;
 *   error non nul si la commande est mal formée.
 */
function parseCommand(text) {
    if (typeof text !== 'string') {
        return null;
    }
    const trimmed = text.trim();
    if (!trimmed.startsWith('!')) {
        return null;
    }

    const parts = trimmed.slice(1).split(/\s+/);
    const cmd = (parts[0] || '').toLowerCase();

    if (!VALID_CMDS.includes(cmd)) {
        return {
            cmd,
            args: [],
            error: '❓ Commande inconnue. Tapez *!help* pour la liste des commandes.'
        };
    }

    const args = [];

    if (cmd === '2fa') {
        const sub = (parts[1] || '').toLowerCase();
        if (sub === 'reset') {
            args.push('reset');
        } else {
            const n = parseInt(parts[1], 10);
            // Le numéro doit être un entier strict >= 1 : "-1", "3.5" ou
            // "03x" sont refusés (parseInt est trop permissif).
            if (!Number.isInteger(n) || n < 1 || String(n) !== parts[1].trim()) {
                return {
                    cmd,
                    args: [],
                    error: '❓ Réponse invalide. Usage : *!2fa <numéro>* ' +
                        '[confirm|cancel] ou *!2fa reset*'
                };
            }
            args.push(String(n));
            // Action optionnelle : confirm / cancel.
            const action = (parts[2] || '').toLowerCase();
            if (action) {
                if (!VALID_2FA_ACTIONS.includes(action)) {
                    return {
                        cmd,
                        args: [],
                        error: '❓ Action invalide. Usage : *!2fa <numéro>* ' +
                            '[confirm|cancel]'
                    };
                }
                args.push(action);
            }
        }
    }

    return { cmd, args, error: null };
}

module.exports = { parseCommand, VALID_CMDS, VALID_2FA_ACTIONS };

/**
 * Retourne true si le texte est une requete en langage naturel
 * (c'est-a-dire qui ne commence pas par le prefixe de commande "!").
 */
function isNaturalLanguage(text) {
    if (typeof text !== 'string') {
        return false;
    }
    return text.trim().length > 0 && !text.trim().startsWith('!');
}

module.exports.isNaturalLanguage = isNaturalLanguage;
