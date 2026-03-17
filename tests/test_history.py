"""Tests for failure history storage and query functions."""

from pathlib import Path
from unittest.mock import patch

import pytest

from jenkins_job_insight import storage


@pytest.fixture
async def setup_test_db(temp_db_path: Path):
    """Set up a test database with the path patched."""
    with patch.object(storage, "DB_PATH", temp_db_path):
        await storage.init_db()
        yield temp_db_path


class TestFailureHistoryTable:
    async def test_failure_history_table_exists(self, setup_test_db, temp_db_path):
        import aiosqlite

        async with aiosqlite.connect(temp_db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='failure_history'"
            )
            row = await cursor.fetchone()
            assert row is not None, "failure_history table should exist"

    async def test_failure_history_indexes_exist(self, setup_test_db, temp_db_path):
        import aiosqlite

        expected_indexes = [
            "idx_fh_test_name",
            "idx_fh_error_signature",
            "idx_fh_job_name",
            "idx_fh_analyzed_at",
            "idx_fh_job_test",
        ]
        async with aiosqlite.connect(temp_db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
            rows = await cursor.fetchall()
            existing = {row[0] for row in rows}
            for idx_name in expected_indexes:
                assert idx_name in existing, f"Index {idx_name} should exist"


class TestPopulateFailureHistory:
    async def test_populate_from_top_level_failures(self, setup_test_db):
        """Test populating from a result with top-level failures."""
        with patch.object(storage, "DB_PATH", setup_test_db):
            result_data = {
                "job_name": "ocp-4.16-e2e",
                "build_number": 247,
                "failures": [
                    {
                        "test_name": "tests.network.TestDNS.test_lookup",
                        "error": "DNS resolution failed",
                        "error_signature": "sig-abc123",
                        "analysis": {
                            "classification": "PRODUCT BUG",
                            "details": "DNS service is down",
                        },
                    },
                    {
                        "test_name": "tests.storage.TestPV.test_create",
                        "error": "PV creation timeout",
                        "error_signature": "sig-def456",
                        "analysis": {
                            "classification": "CODE ISSUE",
                            "details": "Test timeout too short",
                        },
                    },
                ],
                "child_job_analyses": [],
            }
            await storage.populate_failure_history("job-1", result_data)

            import aiosqlite

            async with aiosqlite.connect(setup_test_db) as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM failure_history WHERE job_id = ?",
                    ("job-1",),
                )
                count = (await cursor.fetchone())[0]
                assert count == 2

                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM failure_history WHERE job_id = ? ORDER BY test_name",
                    ("job-1",),
                )
                rows = await cursor.fetchall()
                row0 = dict(rows[0])
                assert row0["test_name"] == "tests.network.TestDNS.test_lookup"
                assert row0["job_name"] == "ocp-4.16-e2e"
                assert row0["build_number"] == 247
                assert row0["error_signature"] == "sig-abc123"
                assert row0["classification"] == "PRODUCT BUG"
                assert row0["child_job_name"] == ""
                assert row0["child_build_number"] == 0

    async def test_populate_from_child_job_analyses(self, setup_test_db):
        """Test populating from a result with child job failures."""
        with patch.object(storage, "DB_PATH", setup_test_db):
            result_data = {
                "job_name": "pipeline-main",
                "build_number": 100,
                "failures": [],
                "child_job_analyses": [
                    {
                        "job_name": "child-e2e",
                        "build_number": 50,
                        "failures": [
                            {
                                "test_name": "tests.TestA.test_one",
                                "error": "Assertion failed",
                                "error_signature": "sig-child1",
                                "analysis": {
                                    "classification": "CODE ISSUE",
                                    "details": "...",
                                },
                            },
                        ],
                        "failed_children": [],
                    },
                ],
            }
            await storage.populate_failure_history("job-2", result_data)

            import aiosqlite

            async with aiosqlite.connect(setup_test_db) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM failure_history WHERE job_id = ?",
                    ("job-2",),
                )
                rows = await cursor.fetchall()
                assert len(rows) == 1
                row = dict(rows[0])
                assert row["test_name"] == "tests.TestA.test_one"
                assert row["child_job_name"] == "child-e2e"
                assert row["child_build_number"] == 50
                assert row["job_name"] == "pipeline-main"

    async def test_populate_from_nested_failed_children(self, setup_test_db):
        """Test populating from deeply nested child job failures."""
        with patch.object(storage, "DB_PATH", setup_test_db):
            result_data = {
                "job_name": "pipeline-main",
                "build_number": 200,
                "failures": [],
                "child_job_analyses": [
                    {
                        "job_name": "mid-pipeline",
                        "build_number": 10,
                        "failures": [],
                        "failed_children": [
                            {
                                "job_name": "leaf-job",
                                "build_number": 5,
                                "failures": [
                                    {
                                        "test_name": "tests.Deep.test_nested",
                                        "error": "deep error",
                                        "error_signature": "sig-deep",
                                        "analysis": {
                                            "classification": "PRODUCT BUG",
                                            "details": "...",
                                        },
                                    },
                                ],
                                "failed_children": [],
                            },
                        ],
                    },
                ],
            }
            await storage.populate_failure_history("job-3", result_data)

            import aiosqlite

            async with aiosqlite.connect(setup_test_db) as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM failure_history WHERE job_id = ?",
                    ("job-3",),
                )
                count = (await cursor.fetchone())[0]
                assert count == 1

    async def test_populate_idempotent(self, setup_test_db):
        """Calling populate twice should not create duplicate rows."""
        with patch.object(storage, "DB_PATH", setup_test_db):
            result_data = {
                "job_name": "ocp-4.16-e2e",
                "build_number": 247,
                "failures": [
                    {
                        "test_name": "tests.TestA.test_one",
                        "error": "err",
                        "error_signature": "sig-1",
                        "analysis": {"classification": "CODE ISSUE", "details": "..."},
                    },
                ],
                "child_job_analyses": [],
            }
            await storage.populate_failure_history("job-idem", result_data)
            await storage.populate_failure_history("job-idem", result_data)

            import aiosqlite

            async with aiosqlite.connect(setup_test_db) as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM failure_history WHERE job_id = ?",
                    ("job-idem",),
                )
                count = (await cursor.fetchone())[0]
                assert count == 1

    async def test_populate_empty_failures(self, setup_test_db):
        """Passing a result with no failures should be a no-op."""
        with patch.object(storage, "DB_PATH", setup_test_db):
            result_data = {
                "job_name": "ocp-4.16-e2e",
                "build_number": 1,
                "failures": [],
                "child_job_analyses": [],
            }
            await storage.populate_failure_history("job-empty", result_data)

            import aiosqlite

            async with aiosqlite.connect(setup_test_db) as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM failure_history WHERE job_id = ?",
                    ("job-empty",),
                )
                count = (await cursor.fetchone())[0]
                assert count == 0
