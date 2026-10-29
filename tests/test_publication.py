"""Publication checks use synthetic identifiers and never touch runtime data."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.check_publication import check, main, path_problem, scan_content


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            dir=Path(__file__).parent, prefix=".publication-test-",
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, path, content):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")

    def test_default_manifest_skips_build_and_local_agent_output(self):
        synthetic_token = "gh" + "p_" + "x" * 36
        self.write("main.py", "print('hello')\n")
        self.write("main.build/debug.c", synthetic_token)
        self.write("dist/readme.txt", synthetic_token)
        self.write(".claude/settings.json", synthetic_token)
        self.write("tests/.tmp/debug.txt", synthetic_token)
        self.assertEqual(check(self.root), (1, []))

    def test_private_artifacts_are_rejected_even_inside_source_directory(self):
        for path in ("database/accounts.db", "config/crypto.key", "ui/.env.local",
                     "tests/session.har", "main.build/debug.c", "dist/release.zip"):
            with self.subTest(path=path):
                self.assertEqual(path_problem(path), "local/private or generated artifact")
        self.write("database/accounts.db", "local private contents")
        _, findings = check(self.root)
        self.assertEqual([item.category for item in findings], ["local/private or generated artifact"])

    def test_detects_profiles_tokens_and_private_keys_with_line_numbers(self):
        token = "github" + "_pat_" + "q" * 40
        profile = "C:/" + "Users/" + "synthetic-person/desktop"
        key_header = "-----BEGIN " + "PRIVATE KEY-----"
        findings = scan_content("core/example.py", f"{profile}\n{token}\n{key_header}".encode())
        self.assertEqual({(item.category, item.line) for item in findings}, {
            ("personal profile path", 1), ("access token", 2), ("private key", 3),
        })

    def test_placeholder_paths_and_documentation_emails_are_allowed(self):
        content = ("%USERPROFILE%/.config/campus_login/\n"
                   "C:/" + "Users/<username>/app\n"
                   "C:/" + "Users/username/app\n"
                   "contact: " + "demo@" + "example.com\n"
                   "password = 'your_password'\n")
        self.assertEqual(scan_content("README.md", content.encode()), [])

    def test_fixture_passwords_are_allowed_but_tokens_in_tests_are_rejected(self):
        self.assertEqual(scan_content("tests/test_auth.py", b"password = 'synthetic-password'"), [])
        self.assertEqual(scan_content("campus-login-cli/tests/test_auth.py",
                                      b"password = 'synthetic-password'"), [])
        token = "gh" + "p_" + "y" * 36
        self.assertEqual(scan_content("tests/test_auth.py", token.encode())[0].category, "access token")
        self.assertEqual(scan_content("core/auth.py", b"password = 'synthetic-password'")[0].category,
                         "credential literal")

    def test_cli_does_not_print_matched_values(self):
        private_email = "synthetic-person@" + "private-domain.invalid"
        self.write("main.py", private_email)
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["--root", str(self.root)])
        self.assertEqual(result, 1)
        self.assertIn("main.py:1: email address", output.getvalue())
        self.assertNotIn(private_email, output.getvalue())

    def test_staged_mode_scans_index_instead_of_working_tree(self):
        self.write("main.py", "safe working-tree contents")
        staged_token = ("gh" + "p_" + "z" * 36).encode()
        with patch("scripts.check_publication.git_output", side_effect=[b"main.py\0", staged_token]) as git:
            count, findings = check(self.root, staged=True)
        self.assertEqual(count, 1)
        self.assertEqual([item.category for item in findings], ["access token"])
        self.assertEqual(git.call_args_list[1].args, (self.root, "show", ":main.py"))

    def test_outside_paths_and_non_manifest_sources_are_rejected(self):
        self.assertEqual(path_problem(""), "path outside repository")
        self.assertEqual(path_problem("../outside.py"), "path outside repository")
        self.assertEqual(path_problem("C:/outside.py"), "path outside repository")
        self.assertEqual(path_problem("random.txt"), "outside publication manifest")
        with self.assertRaises(ValueError):
            check(self.root, ["../outside.py"])

    def test_restored_cli_workflow_and_license_are_in_manifest(self):
        self.write("campus-login-cli/campus_login_cli.py", "password = os.environ.get('PASSWORD')")
        self.write(".github/workflows/build.yml", "token: ${{ github.token }}")
        self.write("LICENSE", "Copyright (c) 2026 PublicContributor")
        self.assertEqual(check(self.root), (3, []))

    def test_explicit_generated_path_is_reported_without_reading_contents(self):
        self.write("main.build/debug.c", "local debug data")
        count, findings = check(self.root, ["main.build"])
        self.assertEqual(count, 1)
        self.assertEqual([item.category for item in findings], ["local/private or generated artifact"])


if __name__ == "__main__":
    unittest.main()
