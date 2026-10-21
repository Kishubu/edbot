#!/bin/sh
set -e

# --- Démarrer dbus (requis par Chromium/Puppeteer dans Docker) ---
mkdir -p /run/dbus
dbus-daemon --system 2>/dev/null || true

# --- Corrections de permissions au démarrage ---

# Dossier de session whatsapp-web.js (créé au runtime, appartient à root après npm install)
if [ -d /app/.wwebjs_auth ]; then
    chown -R "$EDBOT_UID:$EDBOT_GID" /app/.wwebjs_auth 2>/dev/null || true
fi

# Dossiers de données montés en volume
chown -R "$EDBOT_UID:$EDBOT_GID" /app/state /app/documents 2>/dev/null || true

exec "$@"