import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from control_center import models


class ModelArtifactTests(unittest.TestCase):
    def check_workspace(self, external, reasoning_effort="low"):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "app"
            artifacts = (base / "isolated" if external else root / "data") / "artifacts"

            def completed(command, **kwargs):
                self.assertIn(f'model_reasoning_effort="{reasoning_effort}"', command)
                output = Path(command[command.index("-o") + 1])
                output.write_text(json.dumps({"value": "ok"}))
                event = {"type": "turn.completed", "usage": {"input_tokens": 10}}
                return subprocess.CompletedProcess(command, 0, json.dumps(event), "")

            with patch.object(models, "ROOT", root), patch.object(models, "ARTIFACTS", artifacts), patch.object(models.subprocess, "run", side_effect=completed):
                result, meta = models.run_model("Fixture", models.object_schema({"value": models.STRING}), "test-model", "test", reasoning_effort=reasoning_effort)
            self.assertEqual(result, {"value": "ok"})
            recorded = Path(meta["artifact"])
            self.assertEqual(recorded.is_absolute(), external)
            self.assertTrue((root / recorded / "output.json").exists())
            self.assertEqual(meta["usage"], {"input_tokens": 10})
            self.assertEqual(meta["reasoning_effort"], reasoning_effort)

    def test_normal_workspace_keeps_relative_artifact_path(self):
        self.check_workspace(False)

    def test_isolated_workspace_preserves_resolvable_artifact_path(self):
        self.check_workspace(True)

    def test_explicit_reasoning_effort_is_recorded(self):
        self.check_workspace(True, "medium")


if __name__ == "__main__":
    unittest.main()
