/**
 * Tests du parsing des commandes WhatsApp (2FA).
 *
 * Aucun accès réseau : seul le module `parse.js` est chargé, pas le client
 * WhatsApp.
 *
 * Lancement : node whatsapp/test_parse.js
 */
const assert = require('assert');
const { parseCommand } = require('./parse');

let ok = 0;
let ko = 0;

function test(name, fn) {
    try {
        fn();
        console.log('  OK   ' + name);
        ok++;
    } catch (e) {
        console.log('  FAIL ' + name + ' : ' + (e && e.message ? e.message : e));
        ko++;
    }
}

// --- 2FA : confirmation obligatoire -------------------------------------

test('!2fa 3 est parsé sans action (demande de confirmation)', () => {
    const r = parseCommand('!2fa 3');
    assert.strictEqual(r.cmd, '2fa');
    assert.deepStrictEqual(r.args, ['3']);
    assert.strictEqual(r.error, null);
});

test('!2fa 3 confirm est parsé avec l\'action confirm', () => {
    const r = parseCommand('!2fa 3 confirm');
    assert.strictEqual(r.cmd, '2fa');
    assert.deepStrictEqual(r.args, ['3', 'confirm']);
    assert.strictEqual(r.error, null);
});

test('!2fa 3 cancel est parsé avec l\'action cancel', () => {
    const r = parseCommand('!2fa 3 cancel');
    assert.deepStrictEqual(r.args, ['3', 'cancel']);
    assert.strictEqual(r.error, null);
});

test('!2fa reset est parsé comme reset', () => {
    const r = parseCommand('!2fa reset');
    assert.strictEqual(r.cmd, '2fa');
    assert.deepStrictEqual(r.args, ['reset']);
    assert.strictEqual(r.error, null);
});

test('!2fa RESET (majuscules) est accepté', () => {
    const r = parseCommand('!2fa RESET');
    assert.deepStrictEqual(r.args, ['reset']);
    assert.strictEqual(r.error, null);
});

test('!2fa 3 CONFIRM (majuscules) est accepté', () => {
    const r = parseCommand('!2fa 3 CONFIRM');
    assert.deepStrictEqual(r.args, ['3', 'confirm']);
    assert.strictEqual(r.error, null);
});

// --- 2FA : cas invalides ------------------------------------------------

test('!2fa abc est refusé', () => {
    const r = parseCommand('!2fa abc');
    assert.ok(r.error, 'une erreur est attendue');
    assert.ok(r.error.includes('invalide'), r.error);
});

test('!2fa (sans numéro) est refusé', () => {
    const r = parseCommand('!2fa');
    assert.ok(r.error, 'une erreur est attendue');
});

test('!2fa 3 validation est refusée (action inconnue)', () => {
    const r = parseCommand('!2fa 3 validation');
    assert.ok(r.error, 'une erreur est attendue');
    assert.ok(r.error.includes('Action invalide'), r.error);
});

test('!2fa 3.5 est refusé (numéro non entier)', () => {
    const r = parseCommand('!2fa 3.5');
    assert.ok(r.error, 'une erreur est attendue');
});

test('!2fa -1 est refusé (numéro négatif)', () => {
    const r = parseCommand('!2fa -1');
    assert.ok(r.error, 'une erreur est attendue');
});

// --- autres commandes ----------------------------------------------------

test('!notes est parsé', () => {
    const r = parseCommand('!notes');
    assert.strictEqual(r.cmd, 'notes');
    assert.deepStrictEqual(r.args, []);
});

test('!help est parsé', () => {
    const r = parseCommand('!help');
    assert.strictEqual(r.cmd, 'help');
    assert.strictEqual(r.error, null);
});

test('commande inconnue renvoie une erreur', () => {
    const r = parseCommand('!inconnu');
    assert.ok(r.error, 'une erreur est attendue');
    assert.ok(r.error.includes('inconnue'), r.error);
});

test('un message sans ! n\'est pas une commande', () => {
    assert.strictEqual(parseCommand('bonjour'), null);
    assert.strictEqual(parseCommand(''), null);
    assert.strictEqual(parseCommand(null), null);
});

console.log('\n' + ok + '/' + (ok + ko) + ' tests réussis.');
process.exit(ko === 0 ? 0 : 1);
