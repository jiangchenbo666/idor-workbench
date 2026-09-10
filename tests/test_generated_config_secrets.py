"""Generated helper artifacts must not leak runtime credentials."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from idor_workbench.views.api import _write_generated_config


class GeneratedConfigSecretTests(TestCase):
    def test_generated_config_contains_password_reference_not_password(self) -> None:
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            _write_generated_config(
                folder,
                {
                    "base_url": "https://test.example",
                    "roles": [
                        {"name": "security-admin", "username": "alice", "password": "do-not-export"},
                    ],
                    "auth": {},
                    "endpoints": [],
                },
            )
            exported = (folder / "generated_config.py").read_text(encoding="utf-8")

        self.assertNotIn("do-not-export", exported)
        self.assertRegex(exported, r"IDOR_ROLE_SECURITY_ADMIN_[0-9A-F]{8}_PASSWORD")
