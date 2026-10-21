const { Client, LocalAuth } = require('whatsapp-web.js');
const express = require('express');
const qrcode = require('qrcode');
const { execFile } = require('child_process');
const path = require('path');

const APP_DIR = path.resolve(__dirname, '..');
const PORT = process.env.PORT || 3000;
const WHATSAPP_GROUP = process.env.WHATSAPP_GROUP || 'Ecoledirect';

let qrDataUrl = null;
let clientReady = false;
let targetGroupId = null;

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
const client = new Client({
    authStrategy: new LocalAuth({ clientId: 'edbot-whatsapp' }),
    puppeteer: {
        headless: true,
        executablePath: '/usr/bin/chromium',
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--no-first-run',
            '--no-zygote',
            '--single-process',
            '--disable-gpu',
            '--disable-features=site-per-process'
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

    try {
        const chats = await client.getChats();
        const group = chats.find(c => c.isGroup && c.name === WHATSAPP_GROUP);
        if (group) {
            targetGroupId = group.id._serialized;
            console.log('[WA] Groupe cible :', WHATSAPP_GROUP, '(' + targetGroupId + ')');
        } else {
            const noms = chats.filter(c => c.isGroup).map(c => c.name).join(', ') || '(aucun)';
            console.log('[WA] Groupe "' + WHATSAPP_GROUP + '" introuvable. Groupes : ' + noms);
        }
    } catch (e) {
        console.error('[WA] Erreur recherche groupe :', e.message);
    }
});

client.on('authenticated', () => {
    console.log('[WA] Authentification WhatsApp réussie.');
});

client.on('auth_failure', (msg) => {
    console.error('[WA] Échec d\'authentification WhatsApp :', msg);
});

client.on('message', async (msg) => {
    if (!msg.from || !msg.from.includes('@g.us')) {
        return;
    }

    if (targetGroupId && msg.from !== targetGroupId) {
        return;
    }

    if (!targetGroupId) {
        try {
            const chat = await client.getChatById(msg.from);
            if (chat.isGroup && chat.name === WHATSAPP_GROUP) {
                targetGroupId = msg.from;
                console.log('[WA] Groupe cible découvert :', WHATSAPP_GROUP, '(' + targetGroupId + ')');
            } else {
                return;
            }
        } catch (e) {
            console.error('[WA] Impossible de vérifier le groupe :', e.message);
            return;
        }
    }

    const text = (msg.body || '').trim();
    if (!text.startsWith('!')) {
        return;
    }

    const parts = text.slice(1).split(/\s+/);
    const cmd = parts[0].toLowerCase();
    const validCmds = ['notes', 'moyennes', 'recent', 'check', 'sync', 'help'];

    if (!validCmds.includes(cmd)) {
        await msg.reply('❓ Commande inconnue. Tapez *!help* pour la liste des commandes.');
        return;
    }

    if (cmd === 'help') {
        const helpText =
            '*🎓 Edbot — Notes EcoleDirecte*\n\n' +
            '*!notes* — moyennes + dernières notes\n' +
            '*!moyennes* — moyennes par matière\n' +
            '*!recent* — 10 dernières notes\n' +
            '*!check* — forcer une vérification immédiate\n' +
            '*!sync* — archiver les nouveaux documents\n\n' +
            'Le bot répond uniquement dans le groupe *' + WHATSAPP_GROUP + '*';
        await msg.reply(helpText);
        return;
    }

    await msg.reply('🔎 Traitement de la commande *!' + cmd + '*…');

    execFile(
        'python3',
        [path.join(APP_DIR, 'edbot_cmd.py'), cmd],
        {
            cwd: APP_DIR,
            timeout: 180000,
            env: { ...process.env }
        },
        (error, stdout, stderr) => {
            if (error) {
                console.error('[WA] Erreur exécution Python :', error);
                msg.reply('⚠️ Erreur lors de l\'exécution de la commande.');
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
            msg.reply(reply);
        }
    );
});

client.initialize();

console.log('[WA] Démarrage du client WhatsApp (whatsapp-web.js)…');
