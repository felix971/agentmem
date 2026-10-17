"""DESIGN 12.4 / section 5: all secrets here are synthetic fixtures.

Stored text is checked via the public session transcript (each message <2000
characters), not by coupling tests to an internal SQLite schema. Search checks
use the secret VALUE, not generic words like 'Bearer'/'password': AND-to-OR
fallback can legitimately match those nonsecret words after redaction.
"""

import re

from tests.helpers import AgentmemCase


class RedactionTests(AgentmemCase):
    def assert_redacted(self, kind, text, secret_values, *, expected_fragments=()):
        self.assertLess(len(text), 1800, "Fixture must fit in the CLI transcript body")
        self.write_records(self.claude_path(), [self.claude("RedactionCaseMarker\n" + text)])
        self.write_records(self.codex_path(), [self.codex_meta(), self.codex_message("RedactionCaseMarker\n" + text)])
        self.assertEqual(self.index()["messages_added"], 2)
        rows = self.search("RedactionCaseMarker")
        self.assertEqual({row["source"] for row in rows}, {"claude", "codex"})
        self.assertEqual(len(rows), 2)
        for row in rows:
            transcript = self.session_text(row["ref"])
            self.assertIn(f"[REDACTED:{kind}]", transcript)
            for fragment in expected_fragments:
                self.assertIn(fragment, transcript)
            for value in secret_values:
                self.assertNotIn(value, transcript)
        for value in secret_values:
            with self.subTest(kind=kind, secret=value):
                self.assertEqual(self.search(value), [], "Secret value remained searchable")

    def test_private_key(self):
        value = "SYNTHETICPRIVATEKEYPAYLOADFORTESTONLY"
        text = f"-----BEGIN RSA PRIVATE KEY-----\n{value}\n-----END RSA PRIVATE KEY-----"
        self.assertIsNotNone(re.fullmatch(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", text))
        self.assert_redacted("private_key", text, [value], expected_fragments=["[REDACTED:private_key]"])

    def test_anthropic_key_before_openai_pattern(self):
        value = "sk-ant-" + "TestOnly_123-" * 3
        self.assertIsNotNone(re.fullmatch(r"sk-ant-[A-Za-z0-9_\-]{20,}", value))
        self.assert_redacted("anthropic_key", value, [value])

    def test_openai_plain_and_project_keys(self):
        values = ["sk-" + "SyntheticOpenAI1234567890", "sk-proj-" + "SyntheticProject1234567890"]
        for value in values:
            self.assertIsNotNone(re.fullmatch(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}", value))
        self.assert_redacted("openai_key", "\n".join(values), values)

    def test_all_github_prefixes_and_fine_grained_pat(self):
        values = ["gh" + prefix + "_" + "SyntheticGithub123" * 2 for prefix in "pousr"]
        values.append("github_pat_" + "Synthetic_Github_123" * 2)
        for value in values:
            self.assertIsNotNone(re.fullmatch(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})", value))
        self.assert_redacted("github_token", "\n".join(values), values)

    def test_aws_key(self):
        value = "AKIA1234567890ABCDEF"
        self.assertIsNotNone(re.fullmatch(r"AKIA[0-9A-Z]{16}", value))
        self.assert_redacted("aws_key", value, [value])

    def test_all_slack_token_prefixes(self):
        values = ["xox" + prefix + "-SyntheticToken12345" for prefix in "abprs"]
        for value in values:
            self.assertIsNotNone(re.fullmatch(r"xox[abprs]-[A-Za-z0-9\-]{10,}", value))
        self.assert_redacted("slack_token", "\n".join(values), values)

    def test_google_key(self):
        value = "AIza" + ("SyntheticTestOnly_" * 3)[:35]
        self.assertIsNotNone(re.fullmatch(r"AIza[0-9A-Za-z_\-]{35}", value))
        self.assert_redacted("google_key", value, [value])

    def test_huggingface_token(self):
        value = "hf_" + "SyntheticHF12345" * 2
        self.assertIsNotNone(re.fullmatch(r"hf_[A-Za-z0-9]{30,}", value))
        self.assert_redacted("hf_token", value, [value])

    def test_jwt(self):
        value = "eyJ" + "SyntheticHeader123" + ".SyntheticPayload123.SyntheticSignature123"
        self.assertIsNotNone(re.fullmatch(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", value))
        self.assert_redacted("jwt", value, [value])

    def test_case_insensitive_bearer(self):
        value = "SyntheticBearerValue0123456789+/="
        self.assertIsNotNone(re.fullmatch(r"(?i)bearer\s+[A-Za-z0-9._~+/=\-]{20,}", "bEaReR " + value))
        self.assert_redacted("bearer", "bEaReR " + value, [value])

    def test_assignment_aliases_preserve_keys_and_separators(self):
        keys = ("password", "passwd", "pwd", "secret", "api_key", "api-key", "apikey",
                "access_token", "access-token", "accesstoken", "auth_token", "auth-token", "authtoken", "PASSWORD")
        values = [f"SyntheticSecretValue{number:03d}" for number in range(len(keys))]
        lines = [f"{key} = {value}" for key, value in zip(keys, values)]
        expected = [f"{key} = [REDACTED:assignment]" for key in keys]
        self.assert_redacted("assignment", "\n".join(lines), values, expected_fragments=expected)

    def test_assignment_quoted_values(self):
        values = ["SyntheticQuotedValue123", "SyntheticDoubleQuoted123"]
        self.assert_redacted("assignment", f"password: '{values[0]}'\napi-key=\"{values[1]}\"", values,
                             expected_fragments=["password:", "api-key="])
