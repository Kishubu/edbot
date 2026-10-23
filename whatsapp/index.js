const express = require('express');
const qrcode = require('qrcode');
const { execFile } = require('child_process');
const path = require('path');

const APP_DIR = path.resolve(__dirname, '..');
const PORT = process.env.PORT || 3000;
const WHATSAPP_GROUP = process.env.WHATSAPP_GROUP || 'Ecoledirect';
const BOT_TAG = '🤖 ';

let qrDataUrl = null;
let clientReady = false;
let targetGroupId = null;

// --- Parsing des commandes (module séparé pour être testable) ---
const { parseCommand } = require('./parse');

// --- Serveur HTTP pour exposer le QR code ---
const app = express();

app.get('/qr', (_req, res) => {
    if (!qrDataUrl) {
        return res.status(404).send('QR code non encore généré.');
    }
    res.send('<!DOCTYPE html><html><head><meta charset="utf-8"><title>Edbot WhatsApp — QR Code</title></head><body style="font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f5f5f5"><div style="background:#fff;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,0.12);padding:32px;text-align:center"><h1 style="margin:0 0 8px;font-size:20px;color:#1a1a1a">📱 Edbot WhatsApp — Lier l\'appareil</h1><p style="color:#666;font-size:14px;margin:16px 0">Ouvrez WhatsApp → Paramètres → Appareils connectés → Lier un appareil</p><img src="' + qrDataUrl + '" alt="QR Code WhatsApp" style="border:1px solid #ddd;background:#fff;padding:16px;border-radius:12px;max-width:320px"/><p style="margin-top:20px;color:#666;font-size:13px">Le QR code se régénère à chaque démarrage.</p></div></body></html>');
});

app.get('/status', (_req, res) => {
    res.json({ ready: clientReady, hasQr: !!qrDataUrl, group: WHATSAPP_GROUP, groupId: targetGroupId });
});

app.listen(PORT, () => {
    console.log('[HTTP] Serveur sur le port ' + PORT + ' — QR code : http://localhost:' + PORT + '/qr');
});

// --- Client WhatsApp ---
const { Client, LocalAuth } = require('whatsapp-web.js');
const client = new Client({
    authStrategy: new LocalAuth({ clientId: 'edbot-whatsapp' }),
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36',
    webVersionCache: {
        type: 'local',
        path: path.join(APP_DIR, '.wwebjs_cache')
    },
    puppeteer: {
        headless: true,
        executablePath: '/usr/bin/chromium',
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--no-first-run',
            '--disable-gpu',
            '--disable-features=site-per-process,BlinkGenPropertyTrees,AudioServiceOutOfProcess,VideoCapture,GpuProcess,NetworkService,Translate,OverlayScrollbar,PrintPreview,MediaFoundation,AudioService,IsolatedOrigins',
            '--disable-ipc-flooding-protection',
            '--disable-background-networking',
            '--disable-sync',
            '--disable-extensions',
            '--disable-background-timer-throttling',
            '--disable-renderer-backgrounding'
        ]
    }
});

client.on('qr', (qr) => {
    console.log('[WA] QR code reçu, génération de l\'image…');
    qrcode.toDataURL(qr, (err, url) => {
        if (err) {
            console.error('[WA] Erreur génération QR :', err);
            return;
        }
        qrDataUrl = url;
        console.log('[WA] QR code disponible sur http://localhost:' + PORT + '/qr');
    });
});

client.on('ready', async () => {
    console.log('[WA] Client WhatsApp prêt !');
    clientReady = true;
    qrDataUrl = null;
    await findTargetGroup();
});

async function findTargetGroup() {
    try {
        const found = await client.pupPage.evaluate((wanted) => {
            const models = window
                .require('WAWebCollections')
                .Chat.getModelsArray();
            const model = models.find(
                (m) =>
                    m.id &&
                    String(m.id._serialized || '').includes('@g.us') &&
                    m.attributes &&
                    m.attributes.name === wanted
            );
            if (!model) return null;
            return {
                id: model.id._serialized,
                name: model.attributes.name
            };
        }, WHATSAPP_GROUP);

        if (found && found.id) {
            targetGroupId = found.id;
            console.log('[WA] Groupe cible :', found.name, '(' + targetGroupId + ')');
        } else {
            console.log('[WA] Groupe "' + WHATSAPP_GROUP + '" introuvable.');
        }
    } catch (e) {
        console.error('[WA] Erreur recherche groupe :', e && e.message ? e.message : e);
    }
}

client.on('authenticated', () => {
    console.log('[WA] Authentification WhatsApp réussie.');
});

client.on('auth_failure', (msg) => {
    console.error('[WA] Échec d\'authentification WhatsApp :', msg);
});

async function getChatNameInPage(chatId) {
    try {
        return await client.pupPage.evaluate((id) => {
            const wid = window.require('WAWebWidFactory').createWid(id);
            const model =
                window.require('WAWebCollections').Chat.get(wid);
            if (!model) return '';
            return model.attributes
                ? model.attributes.name || model.attributes.formattedTitle || ''
                : '';
        }, chatId);
    } catch (e) {
        console.error('[WA] Impossible de lire le nom du chat :', e && e.message ? e.message : e);
        return '';
    }
}

client.on('message_create', async (msg) => {
    const body = (msg.body || '').trim();
    const chatId = (msg.fromMe ? msg.to : msg.from) || msg.from || '';
    console.log('[WA][MSG] chat=' + chatId + ' | from=' + (msg.from || '?') +
        ' | fromMe=' + msg.fromMe + ' | body="' + body + '"');
    if (body.startsWith(BOT_TAG)) {
        console.log('[WA][MSG] ignoré (message émis par le bot)');
        return;
    }

    if (!chatId.includes('@g.us')) {
        console.log('[WA][MSG] ignoré (pas un groupe)');
        return;
    }

    if (targetGroupId && chatId !== targetGroupId) {
        console.log('[WA][MSG] ignoré (groupe différent de la cible ' + targetGroupId + ')');
        return;
    }

    if (!targetGroupId) {
        const chatNom = await getChatNameInPage(chatId);
        if (chatNom === WHATSAPP_GROUP) {
            targetGroupId = chatId;
            console.log('[WA] Groupe cible découvert :', WHATSAPP_GROUP, '(' + targetGroupId + ')');
        } else {
            console.log('[WA][MSG] ignoré (chat "' + chatNom + '" ne correspond pas à "' + WHATSAPP_GROUP + '"');
            return;
        }
    }

    const text = (msg.body || '').trim();
    if (!text) {
        return;
    }

    // Indiquer que le bot est en train d'ecrire (prise en compte du message)
    try {
        const chat = await msg.getChat();
        await chat.sendStateTyping();
    } catch (e) {
        console.error('[WA] Erreur envoi etat typing :', e && e.message ? e.message : e);
    }

    // --- Commande directe !xxx (comportement existant) ---
    if (text.startsWith('!')) {
        const parsed = parseCommand(text);
        if (!parsed) {
            return;
        }
        const { cmd, args, error } = parsed;
        if (error) {
            await msg.reply(BOT_TAG + error);
            return;
        }
        await _runDirectCommand(msg, cmd, args);
        return;
    }

    // --- Langage naturel -> agent LLM (edagent.py) ---
    await _runNaturalLanguage(msg, text);
});


// --- Fonction auxiliaire : execution d'une commande directe !xxx ---
async function _runDirectCommand(msg, cmd, args) {
    // help est gere en direct (pas besoin de lancer edbot_cmd.py)
    if (cmd === 'help') {
        const helpText =
            '*\u27a3 Edbot — Notes EcoleDirecte*\n\n' +
            '*!notes* — moyennes + dernieres notes\n' +
            '*!moyennes* — moyennes par matiere\n' +
            '*!recent* — 10 dernieres notes\n' +
            '*!check* — forcer une verification immediate\n' +
            '*!messages* — dernier message EcoleDirecte\n' +
            '*!sync* — archiver les nouveaux documents\n' +
            '*!2fa <n>* — repondre a la verification de securite ' +
            '(confirmation exigée : *!2fa <n> confirm*)\n' +
            '*!2fa reset* — rearmer la verification de securite ' +
            '(après déblocage manuel du compte ED)\n\n' +
            'Le bot repond uniquement dans le groupe *' + WHATSAPP_GROUP + '*';
        await msg.reply(BOT_TAG + helpText);
        return;
    }

    await msg.reply(BOT_TAG + '🔎 Traitement de la commande *!' + cmd + '*…');

    const python_cmd = [path.join(APP_DIR, 'edbot_cmd.py'), cmd].concat(args);
    const opts = {
        cwd: APP_DIR,
        timeout: 180000,
        env: { ...process.env }
    };

    // Cas particulier : messages affiche un message intermediaire
    if (cmd === 'messages') {
        await msg.reply(BOT_TAG + '🔎 Récupération du dernier message EcoleDirecte…');
    }

    execFile(
        'python3',
        python_cmd,
        opts,
        (error, stdout, stderr) => {
            if (error) {
                console.error('[WA] Erreur exécution Python :', error);
                msg.reply(BOT_TAG + '⚠️ Erreur lors de l\'exécution de la commande.');
                return;
            }
            if (stderr) {
                console.error('[WA] Stderr Python :', stderr);
            }
            const output = stdout.trim() || 'Aucune réponse.';
            const maxLen = 3800;
            const reply = output.length > maxLen
                ? output.slice(0, maxLen) + '\n… (message tronqué)'
                : output;
            msg.reply(BOT_TAG + reply);
        }
    );
}

// --- Fonction auxiliaire : langage naturel via l'agent LLM ---
async function _runNaturalLanguage(msg, text) {
    const agentScript = path.join(APP_DIR, 'edagent.py');
    execFile(
        'python3',
        [agentScript, text],
        {
            cwd: APP_DIR,
            timeout: 60000,
            env: { ...process.env }
        },
        (error, stdout, stderr) => {
            if (error) {
                console.error('[WA] Erreur agent LLM :', error);
                msg.reply(BOT_TAG + '⚠️ L\'assistant IA est temporairement indisponible.');
                return;
            }
            if (stderr) {
                console.error('[WA] Stderr agent LLM :', stderr);
            }
            const output = (stdout || '').trim();
            if (!output) {
                msg.reply(BOT_TAG + 'Je n\'ai pas compris ta demande.');
                return;
            }

            // L'agent signale qu'il attend une reponse 2FA de l'utilisateur.
            if (output.startsWith('__EDAGENT_ATTEND_2FA__')) {
                const reponse2fa = output.slice('__EDAGENT_ATTEND_2FA__'.length).trim();
                msg.reply(BOT_TAG + reponse2fa);
                return;
            }

            msg.reply(BOT_TAG + output);
        }
    );
}

// --- Ecouteurs d'etat pour debogage ---
client.on('loading_screen', (percent, message) => {
    console.log('[WA][LOADING] ' + percent + '% - ' + message);
});

client.on('authenticated', () => {
    console.log('[WA] Authentification WhatsApp reussie.');
});

client.on('auth_failure', (msg) => {
    console.error('[WA] ECHEC authentification :', msg);
});

client.on('change:state', (state) => {
    console.log('[WA][STATE] Changement d etat :', state);
});

client.on('disconnected', (reason) => {
    console.log('[WA] Deconnecte :', reason);
});

client.on('error', (err) => {
    console.error('[WA] ERREUR client :', err && err.message ? err.message : err);
});

// --- Demarrage ---
(async () => {
    try {
        await client.initialize();
        console.log('[WA] Client initialise.');
    } catch (err) {
        console.error('[WA] ECHEC de l initialisation :', err && err.stack ? err.stack : err);
        process.exitCode = 1;
    }
})();

console.log('[WA] Demarrage du client WhatsApp (whatsapp-web.js)…');
