#!/usr/bin/env python3
"""
Client minimal vers l'API Venice.ai (stdlib uniquement, urllib).

- Lit la clef API depuis la variable d'env VENICE_API_KEY, ou depuis la
  section [llm] du config.ini (option venice_api_key).
- Fonction chat(system_prompt, user_message, model, temperature) -> texte brut.
- Gestion d'erreur : timeout, reponse vide, code HTTP != 200.

Aucune dependance externe requise.
"""

import json
import os
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("EDBOT_CONFIG_PATH") or os.path.join(HERE, "config.ini")

DEFAULT_BASE_URL = "https://api.venice.ai/api/v1"
DEFAULT_MODEL = "nvidia-nemotron-3-nano-30b-a3b"
DEFAULT_TEMPERATURE = 0.3
TIMEOUT_SECONDS = 60


def _read_config_api_key():
    """Tente de lire venice_api_key depuis la section [llm] du config.ini."""
    import configparser

    cp = configparser.ConfigParser()
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        cp.read(CONFIG_PATH)
        if cp.has_section("llm") and cp.has_option("llm", "venice_api_key"):
            return cp.get("llm", "venice_api_key").strip()
    except (configparser.Error, OSError):
        pass
    return None


def get_api_key():
    """Recupere la clef API Venice (env prioritaire sur config.ini)."""
    key = os.environ.get("VENICE_API_KEY", "").strip()
    if key:
        return key
    return _read_config_api_key()


def get_config():
    """Recupere la configuration LLM (model, temperature, base_url)."""
    import configparser

    cp = configparser.ConfigParser()
    base_url = DEFAULT_BASE_URL
    model = DEFAULT_MODEL
    temperature = DEFAULT_TEMPERATURE

    if os.path.exists(CONFIG_PATH):
        try:
            cp.read(CONFIG_PATH)
            if cp.has_section("llm"):
                if cp.has_option("llm", "venice_base_url"):
                    base_url = cp.get("llm", "venice_base_url").strip() or DEFAULT_BASE_URL
                if cp.has_option("llm", "venice_model"):
                    model = cp.get("llm", "venice_model").strip() or DEFAULT_MODEL
                if cp.has_option("llm", "temperature"):
                    try:
                        temperature = float(cp.get("llm", "temperature"))
                    except ValueError:
                        temperature = DEFAULT_TEMPERATURE
        except (configparser.Error, OSError):
            pass

    return {"base_url": base_url, "model": model, "temperature": temperature}


def chat(system_prompt, user_message, model=None, temperature=None, api_key=None):
    """
    Envoie une requete de chat a l'API Venice.ai et retourne le texte de la
    reponse de l'assistant.

    Lève une RuntimeError en cas d'erreur reseau, de code HTTP != 200 ou de
    reponse vide/mal formee.
    """
    cfg = get_config()
    model = model or cfg["model"]
    temperature = temperature if temperature is not None else cfg["temperature"]
    base_url = cfg["base_url"].rstrip("/")
    url = base_url + "/chat/completions"

    key = api_key or get_api_key()
    if not key:
        raise RuntimeError(
            "Clef API Venice absente. Definissez VENICE_API_KEY ou "
            "[llm] venice_api_key dans config.ini."
        )

    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            if resp.status != 200:
                body = resp.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    "API Venice retourne HTTP %s : %s" % (resp.status, body)
                )
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(
            "Erreur API Venice HTTP %s : %s" % (e.code, body)
        )
    except urllib.error.URLError as e:
        raise RuntimeError("Erreur reseau vers Venice.ai : %s" % (e,))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("Erreur inattendue lors de l'appel Venice.ai : %s" % (e,))

    if not raw or not raw.strip():
        raise RuntimeError("Reponse vide de l'API Venice.ai.")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError("Reponse non-JSON de Venice.ai : %s" % (e,))

    choices = parsed.get("choices")
    if not choices or not isinstance(choices, list):
        raise RuntimeError("Reponse Venice.ai sans 'choices' utilisable.")

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if not content or not content.strip():
        raise RuntimeError("Contenu de la reponse Venice.ai vide.")

    return content.strip()
