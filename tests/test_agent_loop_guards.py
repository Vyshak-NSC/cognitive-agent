import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ragapp.agent.context_selector import (
    ContextSelector,
    _requests_chat_cognition_compilation,
    _requests_source_recompile,
)
from ragapp.agent.loop import (
    _persist_state_updates,
    _read_workspace_snapshot,
    _successful_project_mutation,
)
from ragapp.tools.project_files import build_project_file_tools
from ragapp.core.retrieval import RetrievalStore


class AgentLoopGuardTests(unittest.TestCase):
    def test_semantic_intent_search_routes_unrecognized_knowledge_question(self):
        class FakeSemanticIndex:
            last_instance = None

            def __init__(self, store):
                self.synced = {}
                self.searched = []
                self.embedded = []
                type(self).last_instance = self

            def sync(self, namespace, records):
                self.synced[namespace] = records

            def embed_query(self, query):
                self.embedded.append(query)
                return [0.1, 0.2]

            def search(self, namespace, query=None, limit=8, min_score=0.0, query_vector=None):
                self.searched.append(namespace)
                if namespace == "runtime_intents":
                    return [{"id": "cognition", "score": 0.82}]
                if namespace == "prompt_modules":
                    return [{"id": "cognition", "score": 0.8}]
                return []

            def item_vector(self, namespace, item_id):
                return None

        tools = [
            SimpleNamespace(name=name, description=name, parameters={})
            for name in (
                "search_cognition_metadata",
                "request_cognition_context",
                "get_entity_metadata",
                "load_entities",
                "list_project_files",
                "read_project_text",
            )
        ]
        store = SimpleNamespace(exists=lambda: False)
        query = "Which ones match the silver-feathered description we discussed?"

        with patch("ragapp.agent.context_selector.SemanticIndex", FakeSemanticIndex):
            selection = ContextSelector(store, tools).select(query)

        self.assertEqual(selection["intent"], "cognition")
        self.assertEqual(selection["mode"], "semantic")
        self.assertEqual(FakeSemanticIndex.last_instance.embedded, [query])
        self.assertIn("runtime_intents", FakeSemanticIndex.last_instance.searched)
        self.assertTrue(
            any(record["id"] == "cognition" for record in FakeSemanticIndex.last_instance.synced["runtime_intents"])
        )
        self.assertEqual(selection["tool_names"], [tool.name for tool in tools])

    def test_project_file_tool_schemas_match_handler_defaults(self):
        tools = {tool.name: tool for tool in build_project_file_tools()}

        self.assertNotIn("area", tools["list_project_files"].parameters.get("required", []))
        self.assertEqual(
            tools["list_project_files"].parameters["properties"]["area"]["enum"],
            ["workspace", "source"],
        )
        self.assertNotIn("area", tools["read_project_text"].parameters["required"])
        self.assertEqual(
            tools["read_project_text"].parameters["required"],
            ["relative_path"],
        )

    def test_cognition_search_paginates_complete_tagged_result_sets(self):
        records = [
            {
                "id": f"item-{index}",
                "name": f"Item {index}",
                "tags": ["shared classification"],
            }
            for index in range(7)
        ]

        class FakeStore:
            def _all_kind_records(self, kind):
                return records if kind == "entity" else []

            def master_metadata(self):
                return {
                    "entities": {
                        record["id"]: {"name": record["name"], "tags": record["tags"]}
                        for record in records
                    }
                }

        class FakeSemanticIndex:
            def __init__(self, store):
                pass

            def sync(self, namespace, items):
                pass

            def search(self, *args, **kwargs):
                return []

        retrieval = RetrievalStore(FakeStore())
        with patch("ragapp.core.retrieval.SemanticIndex", FakeSemanticIndex):
            first = retrieval.search_metadata_candidates("shared classification", limit=3)
            second = retrieval.search_metadata_candidates(
                "shared classification",
                limit=3,
                offset=first["next_offset"],
            )
            third = retrieval.search_metadata_candidates(
                "shared classification",
                limit=3,
                offset=second["next_offset"],
            )

        self.assertEqual(len(first["candidates"]), 3)
        self.assertTrue(first["has_more"])
        self.assertTrue(second["has_more"])
        self.assertFalse(third["has_more"])
        self.assertEqual(
            [item["id"] for page in (first, second, third) for item in page["candidates"]],
            [record["id"] for record in records],
        )

    def test_project_mutation_gate_requires_a_known_success_status(self):
        self.assertFalse(
            _successful_project_mutation(
                [{"tool": "create_project_file", "result": {}}]
            )
        )
        self.assertFalse(
            _successful_project_mutation(
                [{"tool": "edit_project_text", "result": {"status": "unchanged"}}]
            )
        )
        self.assertTrue(
            _successful_project_mutation(
                [{"tool": "create_project_file", "result": {"status": "created"}}]
            )
        )
        self.assertTrue(
            _successful_project_mutation(
                [
                    {
                        "tool": "propose_source_edit",
                        "result": {
                            "status": "proposed",
                            "draft_id": "draft-1",
                            "review_status": "pending",
                        },
                    }
                ]
            )
        )
        self.assertFalse(
            _successful_project_mutation(
                [
                    {
                        "tool": "propose_source_edit",
                        "result": {
                            "status": "proposed",
                            "review_status": "pending",
                        },
                    }
                ]
            )
        )

    def test_source_recompile_is_not_classified_as_chat_cognition_creation(self):
        for query in (
            "Compile cognition from source.",
            "Recompile cognition.",
            "Recompile the project.",
        ):
            with self.subTest(query=query):
                self.assertTrue(_requests_source_recompile(query))
                self.assertFalse(_requests_chat_cognition_compilation(query))

        chat_query = "Compile cognition from these character descriptions."
        self.assertFalse(_requests_source_recompile(chat_query))
        self.assertTrue(_requests_chat_cognition_compilation(chat_query))

    def test_workspace_snapshot_returns_none_when_file_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "draft.txt").write_text("draft", encoding="utf-8")
            store = SimpleNamespace(workspace=workspace)

            with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
                self.assertIsNone(_read_workspace_snapshot(store, "draft.txt"))

    def test_workspace_snapshot_replaces_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "draft.txt").write_bytes(b"draft \xff")
            store = SimpleNamespace(workspace=workspace)

            self.assertEqual(
                _read_workspace_snapshot(store, "draft.txt"),
                "draft \ufffd",
            )

    def test_rejected_state_update_is_not_counted_as_applied(self):
        cognition = SimpleNamespace(exists=lambda: True)
        state = {
            "deltas": [
                {
                    "operation": "create_entity",
                    "permanence": "permanent",
                    "entity": "mira",
                }
            ]
        }
        calls = []

        with patch(
            "ragapp.cognition.merge.merge_deltas",
            return_value={
                "applied": [],
                "rejected": [{"delta": state["deltas"][0], "error": "invalid"}],
            },
        ):
            applied = _persist_state_updates(
                [json.dumps(state)],
                cognition,
                "project",
                calls,
            )

        self.assertEqual(applied, 0)

    def test_type_error_in_cascading_mutation_does_not_fall_back_to_plain_merge(self):
        cognition = SimpleNamespace(exists=lambda: True)
        state = {
            "deltas": [
                {
                    "permanence": "permanent",
                    "entity": "mira",
                    "field": "age",
                    "new": 30,
                }
            ]
        }
        calls = []

        with (
            patch(
                "ragapp.cognition.merge.apply_semantic_mutation",
                side_effect=TypeError("invalid semantic transaction"),
            ),
            patch("ragapp.cognition.merge.merge_deltas") as merge_deltas,
        ):
            applied = _persist_state_updates(
                [json.dumps(state)],
                cognition,
                "project",
                calls,
            )

        self.assertEqual(applied, 0)
        merge_deltas.assert_not_called()
        self.assertIn("invalid semantic transaction", calls[-1]["result"]["error"])


if __name__ == "__main__":
    unittest.main()
