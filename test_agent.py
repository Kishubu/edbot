#!/usr/bin/env python3
"""Tests unitaires pour edagent.py (agent d'orchestration).

Les appels reseau (edllm.chat) et les subprocess (edbot_cmd.py) sont mockes.
"""

import json
import unittest
from unittest.mock import patch, MagicMock

import edagent


class TestEdAgent(unittest.TestCase):

    def setUp(self):
        # Mock de la persistence d'etat pour ne pas ecrire sur le disque
        # et ne pas laisser un pending_2fa entre deux tests.
        self._save_patch = patch("edagent.save_state", return_value=None)
        self._load_patch = patch("edagent.load_state", return_value={})
        self._save_patch.start()
        self._load_patch.start()

    def tearDown(self):
        self._save_patch.stop()
        self._load_patch.stop()

    def _make_state(self, pending=None):
        state = {}
        if pending is not None:
            state["pending_2fa"] = pending
        return state

    @patch("edllm.get_api_key", return_value=None)
    def test_no_api_key(self, mock_api_key):
        """Sans cle API, l'agent retourne un message d'erreur en francais."""
        result = edagent.handle("Quel est ma moyenne en espagnol ?")
        self.assertIn("clef API Venice.ai est manquante", result)

    @patch("edllm.get_api_key", return_value="fake-key")
    @patch("edllm.chat")
    def test_llm_direct_response(self, mock_chat, mock_api_key):
        """Si le LLM repond sans commande (cmd: null), le texte est rendu tel quel."""
        mock_chat.return_value = (
            "Bonjour ! Je peux consulter tes notes, moyennes et dernieres notes."
        )
        result = edagent.handle("Bonjour")
        self.assertIn("Bonjour", result)
        self.assertIn("notes", result)

    @patch("edllm.get_api_key", return_value="fake-key")
    @patch("edllm.chat")
    @patch("edagent._run_edbot_cmd")
    def test_llm_command_success(self, mock_run_cmd, mock_chat, mock_api_key):
        """Si le LLM demande une commande, edbot_cmd.py est execute et son resultat rendu."""
        mock_chat.return_value = json.dumps({
            "cmd": "recent",
            "args": [],
            "reponse_utilisateur": "Je consulte tes dernieres notes...",
        })
        mock_run_cmd.return_value = ("🆕 Alice — 2 dernières notes\n\n<b>Maths</b> : 14/20", None)

        result = edagent.handle("Quel est ma dernière note en math ?")

        mock_run_cmd.assert_called_once_with("recent", [])
        self.assertIn("Je consulte tes dernieres notes...", result)
        self.assertIn("Maths", result)

    @patch("edllm.get_api_key", return_value="fake-key")
    @patch("edllm.chat")
    @patch("edagent._run_edbot_cmd")
    def test_llm_command_error(self, mock_run_cmd, mock_chat, mock_api_key):
        """Si edbot_cmd.py renvoie une erreur, elle est transmise a l'utilisateur."""
        mock_chat.return_value = json.dumps({
            "cmd": "notes",
            "args": [],
            "reponse_utilisateur": "Je regarde tes notes...",
        })
        mock_run_cmd.return_value = (None, "❌ Connexion École Directe échouée : identifiants invalides")

        result = edagent.handle("Mes notes")

        self.assertIn("Je regarde tes notes...", result)
        self.assertIn("identifiants invalides", result)

    @patch("edllm.get_api_key", return_value="fake-key")
    @patch("edllm.chat")
    @patch("edagent._run_edbot_cmd")
    def test_llm_2fa_required(self, mock_run_cmd, mock_chat, mock_api_key):
        """Une requete declenchant une 2FA retourne le prefixe ATTEND_2FA."""
        mock_chat.return_value = json.dumps({
            "cmd": "moyennes",
            "args": [],
            "reponse_utilisateur": "Je regarde tes moyennes...",
        })
        mock_run_cmd.return_value = (
            "🔐 *Vérification de sécurité École Directe*\n"
            "Quelle est votre ville de naissance ?\n"
            "1. Paris\n"
            "2. Lyon\n"
            "3. Marseille\n",
            None,
        )

        result = edagent.handle("Ma moyenne en espagnol ?", state={})

        self.assertTrue(result.startswith(edagent.ATTEND_2FA_PREFIX))
        self.assertIn("ville de naissance", result)
        self.assertIn("1. Paris", result)

    @patch("edllm.get_api_key", return_value="fake-key")
    @patch("edllm.chat")
    @patch("edagent._run_edbot_cmd")
    def test_2fa_response_success(self, mock_run_cmd, mock_chat, mock_api_key):
        """Une reponse a une question 2FA en attente rejoue la commande 2fa confirm."""
        pending = {
            "question": "Votre ville de naissance ?",
            "propositions": [(1, "Paris"), (2, "Lyon")],
            "command": "moyennes",
            "args": [],
        }
        mock_run_cmd.return_value = (
            "✅ Vérification réussie.\n\n<b>Espagnol</b> : 15/20",
            None,
        )

        state = {"pending_2fa": pending}
        result = edagent.handle("2", state=state)

        # L'etat pending_2fa doit avoir ete supprime
        self.assertNotIn("pending_2fa", state)
        mock_run_cmd.assert_called_once_with("2fa", ["2", "confirm"])
        self.assertIn("Espagnol", result)

    @patch("edllm.get_api_key", return_value="fake-key")
    @patch("edllm.chat")
    @patch("edagent._run_edbot_cmd")
    def test_2fa_response_invalid(self, mock_run_cmd, mock_chat, mock_api_key):
        """Une reponse non-numerique a une question 2FA est refusee."""
        pending = {
            "question": "Votre ville de naissance ?",
            "propositions": [(1, "Paris"), (2, "Lyon")],
            "command": "moyennes",
            "args": [],
        }
        state = {"pending_2fa": pending}
        result = edagent.handle("peut-être", state=state)

        self.assertIn("numero", result.lower())
        # L'etat pending doit etre conserve pour une nouvelle tentative
        self.assertIn("pending_2fa", state)

    @patch("edllm.get_api_key", return_value="fake-key")
    @patch("edllm.chat")
    def test_llm_invalid_json(self, mock_chat, mock_api_key):
        """Si le LLM retourne du texte non-JSON, il est traite comme reponse naturelle."""
        mock_chat.return_value = "Ceci n'est pas du JSON, juste du texte."
        result = edagent.handle("Dis quelque chose")
        self.assertEqual(result, "Ceci n'est pas du JSON, juste du texte.")


if __name__ == "__main__":
    unittest.main()
