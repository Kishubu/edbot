#!/bin/sh
set -e

# --- Créer un HOME inscriptible (requis par Chromium/crashpad en non-root) ---
# Sans cela, crashpad echoue avec "--database is required" et Chromium crash.
HOME_DIR=/app/.home
mkdir -p "$HOME_DIR/.config" "$HOME_DIR/.cache" "$HOME_DIR/.local/share"
chown -R "$EDBOT_UID:$EDBOT_GID" "$HOME_DIR" 2>/dev/null || true
chmod -R u+rwX "$HOME_DIR" 2>/dev/null || true
export HOME="$HOME_DIR"
export XDG_CONFIG_HOME="$HOME_DIR/.config"
export XDG_CACHE_HOME="$HOME_DIR/.cache"
export XDG_DATA_HOME="$HOME_DIR/.local/share"

# --- Corrections de permissions au démarrage (root) ---
if [ -d /app/.wwebjs_auth ]; then
    chown -R "$EDBOT_UID:$EDBOT_GID" /app/.wwebjs_auth 2>/dev/null || true
    chmod -R u+rwX /app/.wwebjs_auth 2>/dev/null || true
fi
chown -R "$EDBOT_UID:$EDBOT_GID" /app/state /app/documents 2>/dev/null || true

# --- Déverrouiller le profil Chromium (évite le verrou "profile in use") ---
if [ -d /app/.wwebjs_auth ]; then
    find /app/.wwebjs_auth -name "*.lock" -delete 2>/dev/null || true
    find /app/.wwebjs_auth -name "SingletonLock" -delete 2>/dev/null || true
    find /app/.wwebjs_auth -name "SingletonCookie" -delete 2>/dev/null || true
    find /app/.wwebjs_auth -name "SingletonSocket" -delete 2>/dev/null || true
fi

# --- Passer à l'utilisateur non-root pour exécuter node ---
exec gosu "$EDBOT_UID:$EDBOT_GID" "$@"