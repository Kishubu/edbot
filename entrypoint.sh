#!/bin/sh
set -e

# --- Démarrer dbus (requis par Chromium/Puppeteer dans Docker, doit être root) ---
mkdir -p /run/dbus
dbus-daemon --system 2>/dev/null || true

# --- Corrections de permissions au démarrage (root) ---
if [ -d /app/.wwebjs_auth ]; then
    chown -R "$EDBOT_UID:$EDBOT_GID" /app/.wwebjs_auth 2>/dev/null || true
fi
chown -R "$EDBOT_UID:$EDBOT_GID" /app/state /app/documents 2>/dev/null || true

# --- Passer à l'utilisateur non-root pour exécuter node ---
exec gosu "$EDBOT_UID:$EDBOT_GID" "$@"