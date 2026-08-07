import os
import tempfile
import unittest
from pathlib import Path

from clean_my_codex.platform_security import (
    PERMISSION_MODEL,
    POSIX_PERMISSIONS,
    file_mode,
    is_link_like,
    private_directory,
    private_file,
    set_private_mode,
)


class PlatformSecurityTests(unittest.TestCase):
    def test_permission_model_matches_the_operating_system(self):
        expected = "posix-owner-mode" if os.name == "posix" else "account-profile-acl"
        self.assertEqual(PERMISSION_MODEL, expected)

    def test_link_like_rejects_symbolic_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("test", encoding="utf-8")
            link = root / "link"
            try:
                link.symlink_to(target)
            except (NotImplementedError, OSError):
                self.skipTest("Symbolic links are unavailable for this account")
            self.assertTrue(is_link_like(link))
            self.assertFalse(private_file(link))

    def test_private_paths_use_the_platform_security_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            root.mkdir(mode=0o700)
            value = root / "token"
            value.write_text("capability", encoding="utf-8")
            set_private_mode(root, 0o700)
            set_private_mode(value, 0o600)

            self.assertTrue(private_directory(root))
            self.assertTrue(private_file(value))
            if POSIX_PERMISSIONS:
                self.assertEqual(file_mode(value, 0), 0o600)
            else:
                self.assertEqual(file_mode(value, 0o600), 0o600)


if __name__ == "__main__":
    unittest.main()
