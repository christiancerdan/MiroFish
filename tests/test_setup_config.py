import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("setup_config", Path(__file__).resolve().parents[1] / "scripts/setup_config.py")
setup_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup_config)


class SetupConfigTests(unittest.TestCase):
    def test_private_random_key_and_existing_file_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / ".env"
            self.assertTrue(setup_config.create_config(destination))
            contents = destination.read_text()
            key = next(line.split("=", 1)[1] for line in contents.splitlines() if line.startswith("MIROFISH_ACCESS_KEY="))
            self.assertGreaterEqual(len(key), 32)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            self.assertFalse(setup_config.create_config(destination))
            self.assertEqual(destination.read_text(), contents)
            second = Path(directory) / "second.env"
            setup_config.create_config(second)
            self.assertNotIn(key, second.read_text())

    @unittest.skipIf(os.name == "nt", "Symlink creation requires privileges on Windows")
    def test_existing_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "private.txt"
            target.write_text("preserve")
            link = Path(directory) / ".env"
            link.symlink_to(target)
            self.assertFalse(setup_config.create_config(link))
            self.assertEqual(target.read_text(), "preserve")
