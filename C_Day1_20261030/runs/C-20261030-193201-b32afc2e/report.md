# C environment and reference checks

Run ID: `C-20261030-193201-b32afc2e`
Completed: 2026-10-30T19:32:01.874+08:00

These are real local test runs. No model API was called. The passing code is the manual's provided reference answer.

| Copy | Passed | Failed | Exit code | Status |
| --- | ---: | ---: | ---: | --- |
| bad | 3 | 1 | 1 | failed |
| reference | 4 | 0 | 0 | passed |

Validation checks:

- PASS: bad_counts_are_3_passed_1_failed
- PASS: bad_exit_code_is_1
- PASS: bad_failure_is_1900_only
- PASS: reference_counts_are_4_passed_0_failed
- PASS: reference_exit_code_is_0
- PASS: no_errors_skips_or_missing_junit
- PASS: original_files_unchanged
- PASS: both_test_copies_unchanged
- PASS: bad_code_copy_unchanged
- PASS: reference_code_matches_provided_reference

See manifest.json for commands, source hashes and artifact paths. Each ToolRun points to its real test log.
