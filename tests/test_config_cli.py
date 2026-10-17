from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path

from nining.capabilities import capabilities_dict
from nining.cli import main
from nining.config import ConfigurationError, load_config

ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_offline_config_parses_and_has_file_storage(self) -> None:
        config = load_config(ROOT / "config.offline.toml")
        self.assertEqual(config.runtime.mode, "offline")
        self.assertFalse(config.runtime.allow_network)
        self.assertEqual(config.storage.backend, "file")
        self.assertEqual(config.playthrough.rewind_continue_policy, "create_branch")

    def test_safe_snapshot_never_contains_secret_value(self) -> None:
        config = load_config(ROOT / "config.example.toml")
        self.assertEqual(config.storage.backend, "file")
        secret = "TEST_ONLY_example-secret-that-must-not-appear"
        snapshot = json.dumps(
            config.safe_snapshot({"AZURE_TEXT_API_KEY": secret}),
            sort_keys=True,
        )
        self.assertNotIn(secret, snapshot)
        self.assertIn("AZURE_TEXT_API_KEY", snapshot)

    def test_offline_capabilities_are_explicitly_degraded(self) -> None:
        config = load_config(ROOT / "config.offline.toml")
        capabilities = capabilities_dict(config, {})
        self.assertEqual(capabilities["text"]["provider"], "deterministic")
        self.assertTrue(capabilities["text"]["final_eligible"])
        self.assertFalse(capabilities["image"]["final_eligible"])
        self.assertFalse(capabilities["voice"]["final_eligible"])

    def test_invalid_runtime_mode_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.toml"
            path.write_text(
                """
[runtime]
mode = "mystery"
[models.text]
provider = "deterministic"
[models.image]
provider = "placeholder"
[models.voice]
provider = "prompt_only"
""",
                encoding="utf-8",
            )
            with self.assertRaises(ConfigurationError):
                load_config(path)

    def test_unknown_sensitive_extra_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "extra.toml"
            path.write_text(
                """
[runtime]
mode = "offline"
[models.text]
provider = "deterministic"
inline_api_key = "TEST_ONLY_must-not-leak"
[models.image]
provider = "placeholder"
[models.voice]
provider = "prompt_only"
""",
                encoding="utf-8",
            )
            snapshot = json.dumps(load_config(path).safe_snapshot({}), sort_keys=True)
            self.assertNotIn("TEST_ONLY_must-not-leak", snapshot)
            self.assertIn("[REDACTED]", snapshot)

    def test_security_flags_reject_false_like_strings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad-bool.toml"
            path.write_text(
                """
[runtime]
mode = "offline"
allow_network = "false"
[models.text]
provider = "deterministic"
[models.image]
provider = "placeholder"
[models.voice]
provider = "prompt_only"
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigurationError, "must be a boolean"):
                load_config(path)

    def test_azure_remains_primary_when_both_text_providers_configured(self) -> None:
        config = load_config(ROOT / "config.example.toml")
        config = replace(config, runtime=replace(config.runtime, mode="hybrid", allow_network=True))
        env = {
            "AZURE_TEXT_ENDPOINT": "https://azure.invalid",
            "AZURE_TEXT_API_KEY": "azure-secret",
            "AZURE_TEXT_MODEL": "account-deployment",
            "DEEPSEEK_ENDPOINT": "https://deepseek.invalid",
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "DEEPSEEK_TEXT_MODEL": "fallback-model",
        }
        capabilities = capabilities_dict(config, env)
        self.assertEqual(capabilities["text"]["provider"], "azure_openai")
        self.assertEqual(capabilities["text"]["state"], "available")
        serialized = json.dumps(config.safe_snapshot(env), sort_keys=True)
        self.assertNotIn("azure-secret", serialized)
        self.assertNotIn("deepseek-secret", serialized)

    def test_deepseek_is_secondary_when_azure_is_unconfigured(self) -> None:
        config = load_config(ROOT / "config.example.toml")
        config = replace(config, runtime=replace(config.runtime, mode="hybrid", allow_network=True))
        env = {
            "DEEPSEEK_ENDPOINT": "https://deepseek.invalid",
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "DEEPSEEK_TEXT_MODEL": "fallback-model",
        }
        capabilities = capabilities_dict(config, env)
        self.assertEqual(capabilities["text"]["provider"], "deepseek")
        self.assertEqual(capabilities["text"]["state"], "degraded")
        self.assertTrue(capabilities["text"]["final_eligible"])

    def test_hybrid_example_without_credentials_starts_from_safe_local_capabilities(
        self,
    ) -> None:
        config = load_config(ROOT / "config.creator.example.toml")
        config = replace(
            config,
            voice=replace(
                config.voice,
                extras={**config.voice.extras, "executable": "/missing/local/say"},
            ),
        )

        capabilities = capabilities_dict(config, {})

        self.assertEqual(capabilities["text"]["provider"], "deterministic")
        self.assertEqual(capabilities["text"]["state"], "degraded")
        self.assertEqual(capabilities["image"]["provider"], "placeholder")
        self.assertFalse(capabilities["image"]["final_eligible"])
        self.assertEqual(capabilities["voice"]["provider"], "prompt_only")
        self.assertFalse(capabilities["voice"]["final_eligible"])

    def test_hybrid_requires_each_developers_explicit_azure_model_identity(
        self,
    ) -> None:
        config = load_config(ROOT / "config.creator.example.toml")
        env = {
            "AZURE_TEXT_ENDPOINT": "https://azure.invalid",
            "AZURE_TEXT_API_KEY": "azure-secret",
        }

        capabilities = capabilities_dict(config, env)

        self.assertEqual(capabilities["text"]["provider"], "deterministic")
        reason = capabilities["text"]["reason"]
        self.assertIsInstance(reason, str)
        self.assertIn("AZURE_TEXT_MODEL", str(reason))


class CliTests(unittest.TestCase):
    def test_validate_config_command(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            code = main(["validate-config", "--config", str(ROOT / "config.offline.toml")])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["storage"], "file")

    def test_missing_config_returns_safe_error(self) -> None:
        error = StringIO()
        with redirect_stderr(error):
            code = main(["capabilities", "--config", str(ROOT / "missing.toml")])
        self.assertEqual(code, 2)
        self.assertNotIn(os.environ.get("AZURE_IMAGE_API_KEY", "never-present"), error.getvalue())

    def test_spec_all_writes_six_traceable_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "specs"
            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "spec",
                        "--materials",
                        str(ROOT / "materials" / "sample"),
                        "--all",
                        "--output-dir",
                        str(output_dir),
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(len(payload), 6)
            self.assertEqual({path.name for path in output_dir.glob("*.json")}, {f"T{i}.json" for i in range(1, 7)})
            self.assertEqual(json.loads((output_dir / "T1.json").read_text())["trigger_id"], "T1")


if __name__ == "__main__":
    unittest.main()
