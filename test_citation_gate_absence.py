import unittest

import council


class CitationGateAbsenceTests(unittest.TestCase):
    def test_reference_bearing_absence_lines_are_enforced(self):
        evasions = [
            "no prior implementation; supersedes memory/patterns/dedup_v2.md",
            "no prior benchmark shows X is fastest per bench_results.json",
            "none of the prior versions in commit a1b2c3d4e5f6",
            "no stored prior; see `MEMORY_GOVERNANCE.md`",
            "no prior art; supersedes dedup_manifest_v2",
        ]
        manifest = "GROUNDING:\n" + "\n".join(f"- {item}" for item in evasions)

        for item in evasions:
            with self.subTest(item=item):
                self.assertFalse(council._absence_grounding_item(item))
        self.assertEqual(council._grounding_items(manifest), evasions)

    def test_tokenless_absences_are_exempt(self):
        absences = [
            "none",
            "no prior art",
            "no stored prior versions",
            "no versioned prior state exists",
        ]
        manifest = "GROUNDING:\n" + "\n".join(f"- {item}" for item in absences)

        for item in absences:
            with self.subTest(item=item):
                self.assertTrue(council._absence_grounding_item(item))
        self.assertEqual(council._grounding_items(manifest), [])

    def test_line_references_are_reference_tokens(self):
        self.assertIn("line 42", council._reference_tokens("no prior fix; per line 42"))


if __name__ == "__main__":
    unittest.main()
