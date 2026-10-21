#!/bin/sh
set -e

# --- Créer un session bus dbus accessible (requis par Chromium/Puppeteer) ---
dbus-daemon --session --fork --address=unix:path=/tmp/dbus-session 2>/dev/null || true
chmod 777 /tmp/dbus-session 2>/dev/null || true
export DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/dbus-session

# --- Corrections de permissions au démarrage (root) ---
if [ -d /app/.wwebjs_auth ]; then
    chown -R "$EDBOT_UID:$EDBOT_GID" /app/.wwebjs_auth 2>/dev/null || true
fi
chown -R "$EDBOT_UID:$EDBOT_GID" /app/state /app/documents 2>/dev/null || true

# --- Passer à l'utilisateur non-root pour exécuter node ---
exec gosu "$EDBOT_UID:$EDBOT_GID" "$@"