#!/bin/sh
set -e

# Le conteneur est censé démarrer en root : on corrige les permissions puis on
# bascule en EDBOT_UID:EDBOT_GID via gosu. Mais si Docker a été configuré avec
# `user:`, le conteneur démarre déjà en non-root : les corrections de
# permissions échouent (EACCES) et gosu ne peut pas changer d'utilisateur.
# On rend donc chaque étape tolérante et on n'exécute gosu que si c'est utile.
IS_ROOT=0
if [ "$(id -u)" = "0" ]; then
    IS_ROOT=1
fi

# --- Créer un HOME inscriptible (requis par Chromium/crashpad en non-root) ---
# Sans cela, crashpad echoue avec "--database is required" et Chromium crash.
HOME_DIR=/app/.home
mkdir -p "$HOME_DIR/.config" "$HOME_DIR/.cache" "$HOME_DIR/.local/share" 2>/dev/null || true
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
# Le chemin réel du partage documents est /data/EcoleDirecte (cf. docker-compose
# et edsync.py : ARCHIVE_ROOT). /app/documents n'existe pas dans l'image :
# le chown sur ce chemin ne touchait donc jamais les fichiers de l'hôte.
chown -R "$EDBOT_UID:$EDBOT_GID" /app/state /data/EcoleDirecte 2>/dev/null || true
chmod -R u+rwX /data/EcoleDirecte 2>/dev/null || true

# --- Déverrouiller le profil Chromium (évite le verrou "profile in use") ---
if [ -d /app/.wwebjs_auth ]; then
    find /app/.wwebjs_auth -name "*.lock" -delete 2>/dev/null || true
    find /app/.wwebjs_auth -name "SingletonLock" -delete 2>/dev/null || true
    find /app/.wwebjs_auth -name "SingletonCookie" -delete 2>/dev/null || true
    find /app/.wwebjs_auth -name "SingletonSocket" -delete 2>/dev/null || true
fi

# --- Repertoires necessaires au client WhatsApp (inscriptibles) ---
# .wwebjs_cache : cache web local de whatsapp-web.js. S'il n'est pas
# inscriptible, l'ecriture echoue en EACCES et interrompt l'injection de
# window.WWebJS : l'evenement 'ready' n'est alors jamais emis.
CACHE_DIR=/app/.wwebjs_cache
mkdir -p "$CACHE_DIR" 2>/dev/null || true
chown -R "$EDBOT_UID:$EDBOT_GID" "$CACHE_DIR" 2>/dev/null || true
chmod -R u+rwX "$CACHE_DIR" 2>/dev/null || true

# --- Passer à l'utilisateur non-root pour exécuter node ---
# gosu a besoin des privilèges root pour changer d'utilisateur (setgroups).
# En non-root, on l'exécute directement : le process herite alors de l'uid courant.
if [ "$IS_ROOT" = "1" ]; then
    exec gosu "$EDBOT_UID:$EDBOT_GID" "$@"
fi
exec "$@"