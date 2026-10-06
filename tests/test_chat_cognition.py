import json
import gc
import tempfile
import unittest
from unittest.mock import patch

from ragapp.agent.context_selector import (
    _looks_like_cognition_mutation,
    _requests_chat_cognition_compilation,
)
from ragapp.agent.loop import _build_system_instruction, _semantic_change_text
from ragapp.cognition.store import CognitionStore
from ragapp.tools.compile_tools import recompile_source


class ChatAuthoredCognitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.projects_root = patch("ragapp.settings.PROJECTS_ROOT", self.tmp.name)
        self.projects_root.start()
        self.store = CognitionStore("user", "project")
        self.store.ensure_initialized()

    def tearDown(self):
        self.store._db = None
        self.store._document_index = None
        gc.collect()
        self.projects_root.stop()
        self.tmp.cleanup()

    def test_explicit_character_and_condition_requests_are_cognition_mutations(self):
        self.assertTrue(
            _looks_like_cognition_mutation(
                "Create a character named Mira with silver eyes and define her condition."
            )
        )
        self.assertTrue(
            _looks_like_cognition_mutation(
                "Can you define a condition for character Mira?"
            )
        )
        self.assertTrue(
            _looks_like_cognition_mutation(
                "Add this condition to the project canon."
            )
        )
        self.assertTrue(
            _looks_like_cognition_mutation(
                "Create cognition from this chat."
            )
        )
        self.assertTrue(
            _requests_chat_cognition_compilation(
                "Compile cognition from these character descriptions."
            )
        )
        self.assertFalse(
            _requests_chat_cognition_compilation(
                "Change Mira's age to 30."
            )
        )
        self.assertFalse(
            _looks_like_cognition_mutation(
                "How do I create a condition in this code?"
            )
        )
        self.assertFalse(
            _looks_like_cognition_mutation(
                "Add a condition to the input validation code."
            )
        )

    def test_chat_cognition_uses_document_compiler_schema_and_persists_records(self):
        extraction = {
            "project_type": "fiction",
            "entities": {
                "mira": {
                    "type": "character",
                    "name": "Mira",
                    "description_addition": "A healer with silver eyes.",
                    "tags": ["healer"],
                    "knowledge_additions": {"appearance": ["Silver eyes."]},
                    "attributes": {"eye_color": {"value": "silver", "summary": "Her eyes are silver."}},
                    "relationship_ids": ["mira_tor_siblings"],
                },
                "tor": {
                    "type": "character",
                    "name": "Tor",
                    "description_addition": "Mira's brother.",
                    "relationship_ids": ["mira_tor_siblings"],
                },
            },
            "relationships": [{
                "id": "mira_tor_siblings",
                "type": "sibling",
                "source": "mira",
                "target": "tor",
                "description_addition": "Mira and Tor are siblings.",
            }],
            "events": [],
            "locations": [],
            "concepts": [{
                "id": "moon_sleep",
                "name": "Moon-sleep",
                "type": "condition",
                "description_addition": "A condition experienced by Mira.",
                "related_entity_ids": ["mira"],
            }],
            "definitions": [],
            "knowledge": [],
        }
        with (
            patch("ragapp.cognition.merge._semantic_context", return_value=[]),
            patch(
                "ragapp.cognition.merge._semantic_generator",
                return_value=lambda prompt: (
                    json.dumps(extraction)
                    if "CHAT-BASED COGNITION COMPILATION" in prompt
                    and '"relationship_ids"' in prompt
                    else self.fail("Chat generation did not use the canonical cognition schema.")
                ),
            ),
            patch.object(self.store, "commit_authoritative_change", return_value="test-commit"),
            patch("ragapp.core.metadata_sync.sync_store"),
        ):
            from ragapp.cognition.merge import apply_semantic_mutation

            result = apply_semantic_mutation(
                self.store,
                "Create cognition from this chat: Mira has silver eyes and Tor is her brother.",
                source_label="user:project",
                chat_compilation=True,
            )

        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["mode"], "chat_cognition_compilation")
        self.assertIn("mira", self.store.master_metadata()["entities"])
        entity = self.store._read_entity("mira")
        self.assertEqual(entity["name"], "Mira")
        self.assertEqual(entity["attributes"]["eye_color"][-1]["value"], "silver")
        self.assertIn("tor", self.store.master_metadata()["entities"])
        relation = self.store._read_object("relationship", "mira_tor_siblings")
        self.assertEqual(relation["type"], "sibling")
        self.assertEqual(
            {participant["id"] for participant in relation["participants"]},
            {"mira", "tor"},
        )
        condition = self.store._read_object("concept", "moon_sleep")
        self.assertEqual(condition["related_entity_ids"], ["mira"])
        self.assertEqual(condition["type"], "condition")
        self.assertTrue(entity["provenance"])

    def test_empty_semantic_plan_is_not_reported_as_applied(self):
        with (
            patch("ragapp.cognition.merge._semantic_context", return_value=[]),
            patch(
                "ragapp.cognition.merge._semantic_generator",
                return_value=lambda _prompt: json.dumps(
                    {"operations": [], "affected": [], "explanation": "No-op"}
                ),
            ),
            patch.object(self.store, "commit_authoritative_change", return_value="test-commit"),
        ):
            from ragapp.cognition.merge import apply_semantic_mutation

            with self.assertRaisesRegex(RuntimeError, "no cognition was changed"):
                apply_semantic_mutation(self.store, "Create a new character.")

    def test_preapplied_mutation_is_not_requested_again_from_the_model(self):
        prompt = _build_system_instruction([], cognition_mutation_preapplied=True)

        self.assertIn("Do not emit a <STATE_UPDATE> block or repeat the mutation", prompt)
        self.assertNotIn("emit exactly one <STATE_UPDATE>", prompt)

    def test_chat_cognition_request_receives_prior_chat_text(self):
        change = _semantic_change_text(
            "Create cognition from this chat.",
            [
                {"role": "user", "content": "Create three characters for a fantasy story."},
                {
                    "role": "assistant",
                    "content": "Mira is a healer with silver eyes. Tor is her brother.",
                },
                {"role": "user", "content": "Give Mira a condition called moon-sleep."},
            ],
        )

        self.assertIn("USER REQUEST:\nCreate cognition from this chat", change)
        self.assertIn("[ASSISTANT]\nMira is a healer with silver eyes. Tor is her brother.", change)
        self.assertIn("[USER]\nGive Mira a condition called moon-sleep.", change)

    def test_recompile_failure_is_not_wrapped_as_success(self):
        with (
            patch(
                "ragapp.tools.compile_tools._source_pairs",
                return_value=[("source", "notes.txt")],
            ),
            patch(
                "ragapp.cognition.compiler.compile_project",
                return_value={"status": "error", "error": "parser failed"},
            ),
        ):
            result = recompile_source(self.store)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["compiler_status"], "error")
        self.assertEqual(result["error"], "parser failed")

    def test_recompile_reports_when_compiler_changed_no_canonical_records(self):
        with (
            patch(
                "ragapp.tools.compile_tools._source_pairs",
                return_value=[("source", "notes.txt")],
            ),
            patch(
                "ragapp.cognition.compiler.compile_project",
                return_value={"status": "compiled", "entities": 0, "relationships": 0},
            ),
        ):
            result = recompile_source(self.store)

        self.assertEqual(result["status"], "compiled")
        self.assertEqual(result["canonical_records_created"], 0)
        self.assertEqual(result["canonical_records_updated"], 0)


if __name__ == "__main__":
    unittest.main()
