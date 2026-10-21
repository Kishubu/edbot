FROM node:20-slim

# Dépendances système : Python3 + Chromium + dbus (Puppeteer pour whatsapp-web.js)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    chromium \
    dbus \
    dbus-x11 \
    gosu \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    lsb-release \
    wget \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Fichiers Python (logique métier École Directe)
COPY edbot.py edsync.py edmail.py ed2fa.py edbot_cmd.py ./
COPY config.ini.example state.json.example ./
COPY API-EcoleDirecte.md README.md ./

# Dépendances Node.js (bot WhatsApp)
COPY whatsapp/package.json ./whatsapp/
RUN cd whatsapp && npm install

# Code du bot WhatsApp
COPY whatsapp/index.js ./whatsapp/

# Dossiers de données
RUN mkdir -p state documents .wwebjs_auth .home && chmod 777 .wwebjs_auth

# Configuration
ENV EDBOT_CONFIG_PATH=/app/state/config.ini
ENV EDBOT_STATE_PATH=/app/state/state.json
ENV TZ=Europe/Paris
ENV WHATSAPP_GROUP=Ecoledirect
ENV PORT=3000
# Valeurs par défaut pour les permissions (surchargeables par --user ou docker-compose)
ENV EDBOT_UID=1000
ENV EDBOT_GID=1000
# HOME inscriptible : indispensable pour chromium/crashpad en utilisateur non-root
ENV HOME=/app/.home

EXPOSE 3000

# Script d'entrée pour corriger les permissions avant de lancer node
COPY entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["node", "whatsapp/index.js"]
