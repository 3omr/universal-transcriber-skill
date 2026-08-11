import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "universal-transcriber"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

from module_registry import configured_slide, discover_modules, resolve_module


class ModuleRegistryTests(unittest.TestCase):
    def test_alias_selects_only_its_module_and_resolves_configured_slide(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            modules = workspace / "modules"
            for module_id, display_name, alias in (
                ("toxo", "Toxicology", "سموم"),
                ("ent", "ENT", "انف واذن"),
            ):
                module_root = modules / module_id
                lecture = module_root / "Lecture"
                lecture.mkdir(parents=True)
                (lecture / "slides.pptx").touch()
                payload = {
                    "schema_version": 1,
                    "module_id": module_id,
                    "display_name": display_name,
                    "aliases": [alias],
                    "notebook": {"id": f"{module_id}-notebook", "title": display_name},
                    "output": {"emoji": "📚"},
                    "lecture_slides": {"recording.mp3": "Lecture/slides.pptx"},
                }
                (module_root / "module.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )

            selected = resolve_module(discover_modules(workspace), "سموم")

            self.assertEqual(selected.module_id, "toxo")
            self.assertEqual(
                configured_slide(selected, "recording.mp3"),
                modules / "toxo" / "Lecture" / "slides.pptx",
            )


if __name__ == "__main__":
    unittest.main()
