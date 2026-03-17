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
