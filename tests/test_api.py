import gc
import io
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from fastapi.testclient import TestClient

import ragapp.interfaces.api as api_module


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.projects_root = patch("ragapp.settings.PROJECTS_ROOT", self.temp.name)
        self.projects_root.start()
        api_module.app.dependency_overrides[api_module._current_user] = lambda: "api-user"
        self.client = TestClient(api_module.app)

    def tearDown(self):
        self.client.close()
        api_module.app.dependency_overrides.clear()
        gc.collect()
        self.projects_root.stop()
        self.temp.cleanup()

    def test_api_requires_authentication(self):
        api_module.app.dependency_overrides.clear()
        response = self.client.get("/api/projects")

        self.assertEqual(response.status_code, 401)

    def test_project_and_file_manager_endpoints(self):
        created = self.client.post("/api/projects", json={"project_id": "demo"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["project_id"], "demo")

        uploaded = self.client.post(
            "/api/projects/demo/files/upload",
            params={"area": "workspace"},
            files=[("files", ("notes.txt", b"hello", "text/plain"))],
        )
        self.assertEqual(uploaded.status_code, 200)
        self.assertEqual(uploaded.json()["count"], 1)

        listing = self.client.get(
            "/api/projects/demo/files",
            params={"area": "workspace", "recursive": True},
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["entries"][0]["path"], "notes.txt")

        contents = self.client.get(
            "/api/projects/demo/files/content",
            params={"area": "workspace", "relative_path": "notes.txt"},
        )
        self.assertEqual(contents.status_code, 200)
        self.assertEqual(contents.json()["content"], "hello")

    def test_projects_are_isolated_by_authenticated_user(self):
        self.client.post("/api/projects", json={"project_id": "private"})
        api_module.app.dependency_overrides[api_module._current_user] = lambda: "other-user"

        response = self.client.get("/api/projects/private/overview")

        self.assertEqual(response.status_code, 404)

    def test_settings_response_never_returns_stored_api_key(self):
        self.client.post("/api/projects", json={"project_id": "keys"})
        response = self.client.put(
            "/api/projects/keys/settings",
            json={
                "provider": "gemini",
                "model": "model",
                "api_key": "secret-value",
                "fallback": [],
                "limits": {},
                "features": {},
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("secret-value", response.text)
        self.assertEqual(response.json()["provider"]["configured_api_key_slots"], ["gemini"])

    def test_zip_upload_rejects_duplicate_and_traversal_entries(self):
        self.client.post("/api/projects", json={"project_id": "zip"})
        for names in (("same.txt", "same.txt"), ("../outside.txt",)):
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as zipped:
                for name in names:
                    zipped.writestr(name, "content")
            response = self.client.post(
                "/api/projects/zip/files/extract-zip",
                files={"archive": ("files.zip", archive.getvalue(), "application/zip")},
            )
            self.assertEqual(response.status_code, 400)

        listing = self.client.get(
            "/api/projects/zip/files",
            params={"area": "workspace", "recursive": True},
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["entries"], [])

    def test_history_rejects_malformed_commit_hash(self):
        self.client.post("/api/projects", json={"project_id": "history"})
        for commit in ("not-a-hash", "a" * 40):
            response = self.client.get(
                "/api/projects/history/history/revision",
                params={"commit": commit, "path": "notes.txt"},
            )
            self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
