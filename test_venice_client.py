#!/usr/bin/env python3
"""Tests unitaires pour edllm.py (client Venice.ai).

Tous les appels reseau sont mockes : aucun appel reellement effectue.
"""

import json
import unittest
from unittest.mock import patch, MagicMock

import edllm


class FakeResponse:
    """Mock d'un objet de reponse urllib (contexte gestionnaire)."""

    def __init__(self, status, body_bytes):
        self.status = status
        self._body = body_bytes

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestVeniceClient(unittest.TestCase):

    def setUp(self):
        edllm._CONFIG_CACHE = None  # pas de cache dans ce module, mais securite

    @patch("edllm.get_api_key", return_value="fake-key")
    @patch("urllib.request.urlopen")
    def test_chat_success(self, mock_urlopen, mock_api_key):
        """Une reponse HTTP 200 valide retourne le contenu du message."""
        payload = {
            "choices": [
                {"message": {"content": "Bonjour, comment puis-je t'aider ?"}}
            ]
        }
        mock_urlopen.return_value = FakeResponse(
            200, json.dumps(payload).encode("utf-8")
        )

        result = edllm.chat("Tu es un assistant.", "Dis bonjour")

        self.assertEqual(result, "Bonjour, comment puis-je t'aider ?")
        # Verifier que l'en-tete Authorization est presente
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        self.assertIn("Authorization", req.headers)
        self.assertEqual(req.headers["Authorization"], "Bearer fake-key")
        self.assertEqual(req.method, "POST")

    @patch("edllm.get_api_key", return_value=None)
    def test_chat_no_api_key(self, mock_api_key):
        """Sans cle API, edllm.chat leve une RuntimeError."""
        with self.assertRaises(RuntimeError) as ctx:
            edllm.chat("system", "user")
        self.assertIn("Clef API Venice absente", str(ctx.exception))

    @patch("edllm.get_api_key", return_value="fake-key")
    @patch("urllib.request.urlopen")
    def test_chat_http_error(self, mock_urlopen, mock_api_key):
        """Un code HTTP != 200 leve une RuntimeError."""
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://api.venice.ai/api/v1/chat/completions",
            401,
            "Unauthorized",
            {},
            None,
        )

        with self.assertRaises(RuntimeError) as ctx:
            edllm.chat("system", "user")
        self.assertIn("HTTP 401", str(ctx.exception))

    @patch("edllm.get_api_key", return_value="fake-key")
    @patch("urllib.request.urlopen")
    def test_chat_empty_response(self, mock_urlopen, mock_api_key):
        """Une reponse vide leve une RuntimeError."""
        mock_urlopen.return_value = FakeResponse(200, b"")

        with self.assertRaises(RuntimeError) as ctx:
            edllm.chat("system", "user")
        self.assertIn("vide", str(ctx.exception))

    @patch("edllm.get_api_key", return_value="fake-key")
    @patch("urllib.request.urlopen")
    def test_chat_empty_content(self, mock_urlopen, mock_api_key):
        """Une reponse JSON sans contenu de message leve une RuntimeError."""
        payload = {"choices": [{"message": {"content": ""}}]}
        mock_urlopen.return_value = FakeResponse(
            200, json.dumps(payload).encode("utf-8")
        )

        with self.assertRaises(RuntimeError) as ctx:
            edllm.chat("system", "user")
        self.assertIn("vide", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
