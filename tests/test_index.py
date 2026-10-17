"""DESIGN 12.3: incremental offsets, corruption, replacement and locking."""

import os
import subprocess

from tests.helpers import AgentmemCase, INDEX_FIELDS, json_line


class IncrementalIndexTests(AgentmemCase):
    def test_report_no_changes_and_quiet_mode(self):
        self.seed_claude("RepeatedIndexMarker")
        first = self.index()
        self.assertEqual(first["messages_added"], 1)
        self.assertTrue(first["complete"])
        again = self.index()
        self.assertEqual(again["messages_added"], 0)
        self.assertEqual(again["files_changed"], 0)
        self.assertEqual(again["skipped_lines"], 0)
        quiet = self.cli("index", "--quiet")
        self.assertEqual(quiet.stdout, "")
        self.assertEqual(len(self.search("RepeatedIndexMarker")), 1)

    def test_append_adds_only_new_messages(self):
        original = self.claude("AppendEvidenceMarker first", uuid="first")
        path = self.write_records(self.claude_path(), [original])
        self.assertEqual(self.index()["messages_added"], 1)
        self.write_records(path, [original, self.claude("AppendEvidenceMarker second", uuid="second")], append=True)
        self.assertEqual(self.index()["messages_added"], 1)
        self.assertEqual(len(self.search("AppendEvidenceMarker")), 2)
        self.assertEqual(self.index()["messages_added"], 0)

    def test_partial_utf8_line_waits_for_final_newline(self):
        path = self.seed_claude("CompleteLineMarker 已完整")
        unfinished = json_line(self.claude("IncompleteLineMarker 记忆", uuid="partial"))
        # Split inside a UTF-8 character, not merely at an ASCII boundary.
        split = unfinished.index("忆".encode("utf-8")) + 1
        with path.open("ab") as stream:
            stream.write(unfinished[:split])
        first = self.index()
        self.assertEqual(first["messages_added"], 1)
        self.assertEqual(first["skipped_lines"], 0)
        self.assertEqual(self.search("IncompleteLineMarker"), [])
        with path.open("ab") as stream:
            stream.write(unfinished[split:-1])
        second = self.index()
        self.assertEqual(second["messages_added"], 0)
        self.assertEqual(second["skipped_lines"], 0)
        self.assertEqual(self.search("IncompleteLineMarker"), [])
        with path.open("ab") as stream:
            stream.write(b"\n")
        self.assertEqual(self.index()["messages_added"], 1)
        self.assertEqual(len(self.search("IncompleteLineMarker")), 1)

    def test_bad_complete_json_is_counted_blank_lines_are_not(self):
        path = self.claude_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            json_line(self.claude("ValidBeforeMarker", uuid="before"))
            + b"{not valid json}\n\n"
            + json_line(self.claude("ValidAfterMarker", uuid="after"))
        )
        report = self.index()
        self.assertEqual(report["skipped_lines"], 1)
        self.assertEqual(report["messages_added"], 2)
        self.assertEqual(len(self.search("ValidBeforeMarker")), 1)
        self.assertEqual(len(self.search("ValidAfterMarker")), 1)
        self.assertEqual(self.index()["skipped_lines"], 0)

    def test_truncation_resets_offset_without_duplicates_or_deletion(self):
        kept = self.claude("TruncationMarker retained", uuid="retained")
        path = self.write_records(self.claude_path(), [
            self.claude("TruncationMarker archived " + "padding " * 200, uuid="archived"), kept,
        ])
        self.assertEqual(self.index()["messages_added"], 2)
        previous = path.stat()
        self.write_records(path, [kept, self.claude("TruncationMarker new", uuid="new")])
        self.assertEqual(path.stat().st_ino, previous.st_ino)
        self.assertLess(path.stat().st_size, previous.st_size)
        self.assertEqual(self.index()["messages_added"], 1)
        self.assertEqual(len(self.search("TruncationMarker")), 3)
        self.assertEqual(self.index()["messages_added"], 0)

    def test_inode_replacement_restarts_even_when_file_is_larger(self):
        old = self.claude("ReplacementMarker old", uuid="old")
        path = self.write_records(self.claude_path(), [old])
        self.assertEqual(self.index()["messages_added"], 1)
        previous = path.stat()
        replacement = path.with_suffix(".replacement")
        # New data is BEFORE the old offset; a mere append read would miss it.
        self.write_records(replacement, [self.claude("ReplacementMarker new", uuid="new"), old])
        os.replace(replacement, path)
        self.assertNotEqual(path.stat().st_ino, previous.st_ino)
        self.assertGreater(path.stat().st_size, previous.st_size)
        self.assertEqual(self.index()["messages_added"], 1)
        self.assertEqual(len(self.search("ReplacementMarker")), 2)
        self.assertEqual(self.index()["messages_added"], 0)

    def test_deleted_source_keeps_archived_messages(self):
        path = self.seed_claude("ArchivedSourceMarker")
        self.index()
        ref = self.search("ArchivedSourceMarker")[0]["ref"]
        path.unlink()
        self.assertEqual(self.index()["messages_added"], 0)
        self.assertEqual(len(self.search("ArchivedSourceMarker")), 1)
        self.assertIn("ArchivedSourceMarker", self.session_text(ref))

    def test_two_concurrent_index_processes_do_not_crash_or_duplicate(self):
        expected = 240
        for file_number in range(4):
            self.write_records(self.claude_path(f"concurrent-{file_number}.jsonl"), [
                self.claude(f"ConcurrentEvidenceMarker record{number}", uuid=f"uuid-{number}",
                            session_id=f"concurrent-session-{file_number}")
                for number in range(file_number * 60, (file_number + 1) * 60)
            ])
        # Both Popen calls occur before either communicate/wait call.
        processes = [self.start_cli("index"), self.start_cli("index")]
        reports = []
        for process in processes:
            try:
                stdout, stderr = process.communicate(timeout=35)
            except subprocess.TimeoutExpired:
                self.fail("Concurrent index did not finish within 35 seconds")
            self.assertEqual(process.returncode, 0, f"stdout={stdout}\nstderr={stderr}")
            report = self.parse_json(stdout)
            self.assertTrue(INDEX_FIELDS <= report.keys(), report)
            reports.append(report)
        self.assertEqual(sum(report["messages_added"] for report in reports), expected)
        rows = self.search("ConcurrentEvidenceMarker", limit=expected + 1)
        self.assertEqual(len(rows), expected)
        self.assertEqual(len({row["msg_id"] for row in rows}), expected)
        self.assertEqual(len({row["ref"] for row in rows}), expected)
        self.assertEqual(self.index()["messages_added"], 0)
