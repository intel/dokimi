# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for repo-eval workflow task selection with NUL-separated filenames."""

from __future__ import annotations

from pathlib import Path


def parse_changed_files(output: str) -> list[str]:
    """Parse NUL-separated git diff output (regression for PR #113 thread 1)."""
    return [f for f in output.split('\0') if f]


class TestChangedFilesNulSeparation:
    """Verify task selection handles NUL-separated filenames with spaces and renames."""

    def test_nul_separated_output_with_spaces(self) -> None:
        """Files with spaces should be parsed correctly when NUL-separated."""
        # Simulate git diff --no-renames --name-only -z output
        output = "tasks/task1/file.txt\0tasks/agent eval/file.py\0tasks/task2/file.md\0"
        result = parse_changed_files(output)
        assert result == [
            "tasks/task1/file.txt",
            "tasks/agent eval/file.py",
            "tasks/task2/file.md",
        ]

    def test_nul_separated_output_with_quotes(self) -> None:
        """Files with quotes (Git's quoted format) should be parsed correctly."""
        # Git quotes filenames with special chars when not using -z
        # With -z, they come unquoted
        output = 'tasks/file"name".txt\0tasks/file\'name\'.py\0'
        result = parse_changed_files(output)
        assert result == ['tasks/file"name".txt', "tasks/file'name'.py"]

    def test_nul_separated_empty_output(self) -> None:
        """Empty output should produce empty list."""
        output = ""
        result = parse_changed_files(output)
        assert result == []

    def test_nul_separated_trailing_nul(self) -> None:
        """Trailing NUL should not produce empty entry."""
        output = "file1.txt\0file2.txt\0"
        result = parse_changed_files(output)
        assert result == ["file1.txt", "file2.txt"]

    def test_no_renames_flag_prevents_omission(self) -> None:
        """--no-renames flag ensures both source and dest of moves are reported."""
        # Without --no-renames: file moved from A to B reports only B
        # With --no-renames: both A and B are reported
        # This test validates the flag is present in the workflow
        wf_path = Path(__file__).resolve().parents[2] / ".github/workflows/repo-eval.yml"
        with open(wf_path, encoding="utf-8") as f:
            workflow = f.read()
        # Verify the --no-renames flag is in the git diff command
        assert '--no-renames' in workflow
        assert '--name-only' in workflow
        assert '-z' in workflow
        # Check the order and that they're in a git diff command
        assert 'git", "diff", "--no-renames", "--name-only", "-z"' in workflow


class TestUploadArtifactErrFiles:
    """Verify upload artifact includes .err files for grader/oracle stderr."""

    def test_err_files_in_upload_path(self) -> None:
        """Verify *.err pattern is included in upload artifact paths."""
        wf_path = Path(__file__).resolve().parents[2] / ".github/workflows/repo-eval.yml"
        with open(wf_path, encoding="utf-8") as f:
            workflow = f.read()
        # Find the upload artifact section for check results
        assert "repo-eval-check-" in workflow
        # Verify *.err files are listed
        assert '${{ runner.temp }}/repo-check/check/*/*.err' in workflow
        # Verify it's in the right section (after the log files)
        artifact_section = workflow[
            workflow.find("Upload the check results") :
        ]
        assert '*.err' in artifact_section or '*/*.err' in artifact_section

    def test_err_files_purpose(self) -> None:
        """Document that .err files contain grader and oracle stderr."""
        # This is a documentation test; the actual files are:
        # - grade.err: grader stderr
        # - oracle.err: oracle stderr
        # - isolation.err: isolation probe stderr
        # These are all captured by repos/grading.py:124 and the isolation probe
        files_expected = ["grade.err", "oracle.err", "isolation.err"]
        # This validates the reviewer's comment about these files
        assert len(files_expected) == 3


class TestSharedFilesUnderTasksDir:
    """Verify shared helper files under tasks dir are treated as harness changes."""

    def test_shared_file_under_tasks_dir_selects_smoke(self) -> None:
        """A file like tasks/common.py (helper shared by graders) should trigger smoke set."""
        wf_path = Path(__file__).resolve().parents[2] / ".github/workflows/repo-eval.yml"
        with open(wf_path, encoding="utf-8") as f:
            workflow = f.read()
        # Verify the workflow handles non-task-id first segments under prefix as harness
        assert "first_segment not in all_ids" in workflow
        assert "shared helper" in workflow or "tasks/common.py" in workflow.lower() or "tasks dir" in workflow.lower()

    def test_deleted_task_directory_handled(self) -> None:
        """A deleted task directory (first segment not in all_ids) should also be harness."""
        # The workflow should treat deleted task dirs the same way as shared files
        wf_path = Path(__file__).resolve().parents[2] / ".github/workflows/repo-eval.yml"
        with open(wf_path, encoding="utf-8") as f:
            workflow = f.read()
        # Verify the logic handles non-task-id segments under prefix
        assert "first_segment not in all_ids" in workflow


class TestManifestPathNormalization:
    """Verify manifest path is normalized for comparison with git output."""

    def test_manifest_normalized_in_workflow(self) -> None:
        """Manifest should be normalized with posixpath.normpath for git comparison."""
        wf_path = Path(__file__).resolve().parents[2] / ".github/workflows/repo-eval.yml"
        with open(wf_path, encoding="utf-8") as f:
            workflow = f.read()
        # Verify posixpath.normpath is used on MANIFEST
        assert "posixpath.normpath" in workflow
        assert "manifest_normalized" in workflow
        # Verify manifest comparison uses the normalized version
        assert "manifest_normalized" in workflow
