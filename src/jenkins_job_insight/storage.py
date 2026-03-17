"""SQLite storage for analysis results."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
from simple_logger.logger import get_logger

logger = get_logger(name=__name__, level=os.environ.get("LOG_LEVEL", "INFO"))

DB_PATH = Path(os.getenv("DB_PATH", "/data/results.db"))
REPORTS_DIR = DB_PATH.parent / "reports"


async def init_db() -> None:
    """Initialize the database schema.

    Creates the results table if it does not exist.
    """
    logger.info(f"Initializing database at {DB_PATH}")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS results (
                job_id TEXT PRIMARY KEY,
                jenkins_url TEXT,
                status TEXT,
                result_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                test_name TEXT NOT NULL,
                child_job_name TEXT NOT NULL DEFAULT '',
                child_build_number INTEGER NOT NULL DEFAULT 0,
                comment TEXT NOT NULL,
                error_signature TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_comments_job_id ON comments (job_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_comments_test_name ON comments (test_name)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_comments_error_signature ON comments (error_signature)"
        )
        await db.execute("""
            CREATE TABLE IF NOT EXISTS failure_reviews (
                job_id TEXT NOT NULL,
                test_name TEXT NOT NULL,
                child_job_name TEXT NOT NULL DEFAULT '',
                child_build_number INTEGER NOT NULL DEFAULT 0,
                reviewed BOOLEAN DEFAULT 0,
                username TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (job_id, test_name, child_job_name, child_build_number)
            )
        """)

        # Migration: add child_build_number to existing tables
        # (needed when upgrading from versions without this column)
        logger.info("Running database migrations...")
        for table in ("comments", "failure_reviews"):
            cursor = await db.execute(f"PRAGMA table_info({table})")
            columns = {row[1] for row in await cursor.fetchall()}
            if "child_build_number" not in columns:
                await db.execute(
                    f"ALTER TABLE {table} ADD COLUMN child_build_number INTEGER NOT NULL DEFAULT 0"
                )
                logger.info(f"Migration: added child_build_number column to {table}")
            else:
                logger.debug(
                    f"Migration: {table} already has child_build_number column"
                )

        # Migration: add username to comments and failure_reviews
        for table in ("comments", "failure_reviews"):
            cursor = await db.execute(f"PRAGMA table_info({table})")
            columns = {row[1] for row in await cursor.fetchall()}
            if "username" not in columns:
                await db.execute(
                    f"ALTER TABLE {table} ADD COLUMN username TEXT NOT NULL DEFAULT ''"
                )
                logger.info(f"Migration: added username column to {table}")

        # Migration: add error_signature to comments table
        cursor = await db.execute("PRAGMA table_info(comments)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "error_signature" not in columns:
            await db.execute(
                "ALTER TABLE comments ADD COLUMN error_signature TEXT NOT NULL DEFAULT ''"
            )
            logger.info("Migration: added error_signature column to comments")
        else:
            logger.debug("Migration: comments already has error_signature column")

        # Migration: rebuild failure_reviews with correct 4-column PRIMARY KEY
        # ALTER TABLE cannot change PKs in SQLite, so we need a full rebuild
        cursor = await db.execute("PRAGMA table_info(failure_reviews)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "child_build_number" in columns:
            # Check if PK includes child_build_number by inspecting table SQL
            cursor = await db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='failure_reviews'"
            )
            create_sql = (await cursor.fetchone())[0]
            if (
                "child_build_number" not in create_sql.split("PRIMARY KEY")[1]
                if "PRIMARY KEY" in create_sql
                else ""
            ):
                logger.info(
                    "Migration: rebuilding failure_reviews table with 4-column PRIMARY KEY"
                )
                await db.execute(
                    "ALTER TABLE failure_reviews RENAME TO failure_reviews_old"
                )
                await db.execute("""
                    CREATE TABLE failure_reviews (
                        job_id TEXT NOT NULL,
                        test_name TEXT NOT NULL,
                        child_job_name TEXT NOT NULL DEFAULT '',
                        child_build_number INTEGER NOT NULL DEFAULT 0,
                        reviewed BOOLEAN DEFAULT 0,
                        username TEXT NOT NULL DEFAULT '',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (job_id, test_name, child_job_name, child_build_number)
                    )
                """)
                await db.execute("""
                    INSERT INTO failure_reviews (job_id, test_name, child_job_name, child_build_number, reviewed, username, updated_at)
                    SELECT job_id, test_name, child_job_name, child_build_number, reviewed, COALESCE(username, ''), updated_at
                    FROM failure_reviews_old
                """)
                await db.execute("DROP TABLE failure_reviews_old")
                logger.info("Migration: failure_reviews table rebuilt successfully")

        # failure_history: denormalized table for fast history queries
        await db.execute("""
            CREATE TABLE IF NOT EXISTS failure_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                job_name TEXT NOT NULL,
                build_number INTEGER NOT NULL,
                test_name TEXT NOT NULL,
                error_message TEXT NOT NULL DEFAULT '',
                error_signature TEXT NOT NULL DEFAULT '',
                classification TEXT NOT NULL DEFAULT '',
                child_job_name TEXT NOT NULL DEFAULT '',
                child_build_number INTEGER NOT NULL DEFAULT 0,
                analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_fh_test_name ON failure_history (test_name)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_fh_error_signature ON failure_history (error_signature)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_fh_job_name ON failure_history (job_name)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_fh_analyzed_at ON failure_history (analyzed_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_fh_job_test ON failure_history (job_name, test_name)"
        )

        await db.commit()

    # Backfill failure_history from existing results (runs once when table is empty)
    await backfill_failure_history()


def _validate_child_identifier_pairing(
    child_job_name: str, child_build_number: int
) -> None:
    """Validate that child_job_name and child_build_number are either both set or both empty."""
    if child_build_number < 0:
        raise ValueError("child_build_number must not be negative")
    if child_job_name and child_build_number <= 0:
        raise ValueError(
            "child_build_number must be positive when child_job_name is set"
        )
    if not child_job_name and child_build_number > 0:
        raise ValueError("child_job_name is required when child_build_number is set")


async def add_comment(
    job_id: str,
    test_name: str,
    comment: str,
    child_job_name: str = "",
    child_build_number: int = 0,
    error_signature: str = "",
    username: str = "",
) -> int:
    """Add a comment to a test failure."""
    _validate_child_identifier_pairing(child_job_name, child_build_number)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO comments (job_id, test_name, child_job_name, child_build_number, comment, error_signature, username) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                test_name,
                child_job_name,
                child_build_number,
                comment,
                error_signature,
                username,
            ),
        )
        await db.commit()
        return cursor.lastrowid


async def get_comments_for_job(job_id: str) -> list[dict]:
    """Get all comments for a specific job."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, job_id, test_name, child_job_name, child_build_number, comment, error_signature, username, created_at "
            "FROM comments WHERE job_id = ? ORDER BY created_at ASC",
            (job_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def set_reviewed(
    job_id: str,
    test_name: str,
    reviewed: bool,
    child_job_name: str = "",
    child_build_number: int = 0,
    username: str = "",
) -> None:
    """Set or update the reviewed state for a test failure."""
    _validate_child_identifier_pairing(child_job_name, child_build_number)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO failure_reviews (job_id, test_name, child_job_name, child_build_number, reviewed, username, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (job_id, test_name, child_job_name, child_build_number, reviewed, username),
        )
        await db.commit()


async def get_reviews_for_job(job_id: str) -> dict[str, dict]:
    """Get all review states for a specific job."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT test_name, child_job_name, child_build_number, reviewed, username, updated_at "
            "FROM failure_reviews WHERE job_id = ?",
            (job_id,),
        )
        rows = await cursor.fetchall()
        result = {}
        for row in rows:
            if row["child_job_name"] != "":
                key = f"{row['child_job_name']}#{row['child_build_number']}::{row['test_name']}"
            else:
                key = row["test_name"]
            result[key] = {
                "reviewed": bool(row["reviewed"]),
                "username": row["username"],
                "updated_at": row["updated_at"],
            }
        return result


async def get_review_status(job_id: str) -> dict:
    """Get review summary for a job (used by dashboard)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT result_json FROM results WHERE job_id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        total_failures = 0
        if row and row[0]:
            try:
                result_data = json.loads(row[0])
                total_failures = count_all_failures(result_data)
            except (json.JSONDecodeError, TypeError, AttributeError):
                total_failures = 0

        cursor = await db.execute(
            "SELECT COUNT(*) FROM failure_reviews WHERE job_id = ? AND reviewed = 1",
            (job_id,),
        )
        reviewed_count = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM comments WHERE job_id = ?", (job_id,)
        )
        comment_count = (await cursor.fetchone())[0]

        return {
            "total_failures": total_failures,
            "reviewed_count": reviewed_count,
            "comment_count": comment_count,
        }


async def get_historical_comments(
    test_names: list[str] | None = None,
    error_signatures: list[str] | None = None,
    exclude_job_id: str | None = None,
) -> list[dict]:
    """Get historical comments for similar failures across jobs.

    Matches by test name OR by error signature.
    No arbitrary limit -- returns all matching comments.
    """
    conditions: list[str] = []
    params: list[str] = []

    if test_names:
        placeholders = ",".join("?" for _ in test_names)
        conditions.append(f"test_name IN ({placeholders})")
        params.extend(test_names)

    if error_signatures:
        placeholders = ",".join("?" for _ in error_signatures)
        conditions.append(f"error_signature IN ({placeholders})")
        params.extend(error_signatures)

    if not conditions:
        return []

    where = " OR ".join(conditions)
    if exclude_job_id:
        where = f"({where}) AND job_id != ?"
        params.append(exclude_job_id)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"SELECT id, job_id, test_name, child_job_name, child_build_number, comment, error_signature, username, created_at "
            f"FROM comments WHERE {where} ORDER BY created_at DESC",
            params,
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def save_result(
    job_id: str,
    jenkins_url: str,
    status: str,
    result: dict | None = None,
) -> None:
    """Save or update an analysis result.

    Args:
        job_id: Unique identifier for the analysis job.
        jenkins_url: URL of the analyzed Jenkins build.
        status: Current status of the analysis.
        result: Optional result data to store.
    """
    logger.debug(f"Saving result for job_id: {job_id} (status: {status})")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO results (job_id, jenkins_url, status, result_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                job_id,
                jenkins_url,
                status,
                json.dumps(result) if result is not None else None,
            ),
        )
        await db.commit()


async def update_status(
    job_id: str,
    status: str,
    result: dict | None = None,
) -> None:
    """Update the status of an existing analysis result.

    Unlike save_result, this uses UPDATE to preserve the original created_at timestamp.
    Only updates result_json when result is explicitly provided.

    Args:
        job_id: Unique identifier for the analysis job.
        status: New status for the analysis.
        result: Optional result data to store. When None, result_json is not modified.
    """
    logger.debug(f"Updating status for job_id: {job_id} (status: {status})")
    async with aiosqlite.connect(DB_PATH) as db:
        if result is not None:
            cursor = await db.execute(
                """
                UPDATE results SET status = ?, result_json = ?
                WHERE job_id = ?
                """,
                (status, json.dumps(result), job_id),
            )
        else:
            cursor = await db.execute(
                """
                UPDATE results SET status = ?
                WHERE job_id = ?
                """,
                (status, job_id),
            )
        if cursor.rowcount == 0:
            logger.warning(f"update_status: no row found for job_id={job_id}")
        await db.commit()


async def get_result(job_id: str) -> dict | None:
    """Retrieve an analysis result by job ID.

    Args:
        job_id: Unique identifier for the analysis job.

    Returns:
        Result dictionary if found, None otherwise.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM results WHERE job_id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        if row:
            return {
                "job_id": row["job_id"],
                "jenkins_url": row["jenkins_url"],
                "status": row["status"],
                "result": json.loads(row["result_json"])
                if row["result_json"]
                else None,
                "created_at": row["created_at"],
            }
        return None


async def list_results(limit: int = 50) -> list[dict]:
    """List recent analysis results.

    Args:
        limit: Maximum number of results to return.

    Returns:
        List of result summary dictionaries.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT job_id, jenkins_url, status, created_at
            FROM results
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


def count_all_failures(result_data: dict) -> int:
    """Count all failures including those in nested child job analyses.

    Walks the top-level ``failures`` list, then recursively counts failures in
    ``child_job_analyses`` (top-level key) and ``failed_children`` (nested key
    inside each child).

    Args:
        result_data: Parsed result dictionary from result_json.

    Returns:
        Total number of failures across all levels.
    """
    count = len(result_data.get("failures", []))
    for child in result_data.get("child_job_analyses", []):
        count += _count_child_failures_recursive(child)
    return count


def _count_child_failures_recursive(child: dict) -> int:
    """Recursively count failures in a child job analysis dict.

    Each child has a ``failures`` list and a ``failed_children`` list that can
    nest arbitrarily deep.

    Args:
        child: A single child job analysis dictionary.

    Returns:
        Total number of failures for this child and its descendants.
    """
    count = len(child.get("failures", []))
    for nested in child.get("failed_children", []):
        count += _count_child_failures_recursive(nested)
    return count


def _extract_failures_for_history(
    result_data: dict,
    job_id: str,
    job_name: str,
    build_number: int,
) -> list[tuple]:
    """Extract all failures from result_data into flat tuples for insertion.

    Walks top-level failures and recursively walks child_job_analyses
    and nested failed_children, using the same traversal as
    count_all_failures().

    Args:
        result_data: Parsed result dictionary from result_json.
        job_id: The job identifier.
        job_name: Top-level job name.
        build_number: Top-level build number.

    Returns:
        List of tuples ready for INSERT:
        (job_id, job_name, build_number, test_name, error_message,
         error_signature, classification, child_job_name, child_build_number)
    """
    rows: list[tuple] = []

    # Top-level failures (no child context)
    for f in result_data.get("failures", []):
        analysis = f.get("analysis", {})
        if isinstance(analysis, str):
            classification = ""
        else:
            classification = analysis.get("classification", "")
        rows.append(
            (
                job_id,
                job_name,
                build_number,
                f.get("test_name", ""),
                f.get("error", ""),
                f.get("error_signature", ""),
                classification,
                "",  # child_job_name
                0,  # child_build_number
            )
        )

    # Child job analyses (recursive)
    for child in result_data.get("child_job_analyses", []):
        _extract_child_failures_for_history(child, job_id, job_name, build_number, rows)

    return rows


def _extract_child_failures_for_history(
    child: dict,
    job_id: str,
    job_name: str,
    build_number: int,
    rows: list[tuple],
) -> None:
    """Recursively extract failures from a child job analysis dict.

    Args:
        child: A single child job analysis dictionary.
        job_id: The top-level job identifier.
        job_name: Top-level job name.
        build_number: Top-level build number.
        rows: Accumulator list for insertion tuples.
    """
    child_job = child.get("job_name", "")
    child_build = child.get("build_number", 0)

    for f in child.get("failures", []):
        analysis = f.get("analysis", {})
        if isinstance(analysis, str):
            classification = ""
        else:
            classification = analysis.get("classification", "")
        rows.append(
            (
                job_id,
                job_name,
                build_number,
                f.get("test_name", ""),
                f.get("error", ""),
                f.get("error_signature", ""),
                classification,
                child_job,
                child_build,
            )
        )

    for nested in child.get("failed_children", []):
        _extract_child_failures_for_history(
            nested, job_id, job_name, build_number, rows
        )


async def populate_failure_history(job_id: str, result_data: dict) -> None:
    """Populate failure_history from a completed analysis result.

    Extracts all failures (top-level and nested children) and inserts
    them into the failure_history table. Idempotent: skips if rows
    already exist for this job_id.

    Args:
        job_id: Unique identifier for the analysis job.
        result_data: Parsed result dictionary from result_json.
    """
    job_name = result_data.get("job_name", "")
    build_number = result_data.get("build_number", 0)

    rows = _extract_failures_for_history(result_data, job_id, job_name, build_number)
    if not rows:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        # Idempotency check: skip if already populated for this job_id
        cursor = await db.execute(
            "SELECT COUNT(*) FROM failure_history WHERE job_id = ?",
            (job_id,),
        )
        existing_count = (await cursor.fetchone())[0]
        if existing_count > 0:
            logger.debug(
                f"failure_history already populated for job_id={job_id}, skipping"
            )
            return

        await db.executemany(
            """
            INSERT INTO failure_history
                (job_id, job_name, build_number, test_name, error_message,
                 error_signature, classification, child_job_name, child_build_number)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await db.commit()
        logger.info(
            f"Populated failure_history with {len(rows)} rows for job_id={job_id}"
        )


async def backfill_failure_history() -> None:
    """Backfill failure_history from existing completed results.

    Runs once at startup when the failure_history table is empty but
    the results table has completed rows. Uses the same extraction
    logic as populate_failure_history().
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Check if failure_history already has data
        cursor = await db.execute("SELECT COUNT(*) FROM failure_history")
        count = (await cursor.fetchone())[0]
        if count > 0:
            logger.info("failure_history already has data, skipping backfill")
            return

        # Get all completed results with result_json
        cursor = await db.execute(
            "SELECT job_id, result_json FROM results WHERE status = 'completed' AND result_json IS NOT NULL"
        )
        rows = await cursor.fetchall()

    if not rows:
        logger.info("No completed results to backfill into failure_history")
        return

    logger.info(f"Backfilling failure_history from {len(rows)} completed results")
    backfilled = 0
    for job_id, result_json_str in rows:
        try:
            result_data = json.loads(result_json_str)
            await populate_failure_history(job_id, result_data)
            backfilled += 1
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            logger.debug(f"Skipping backfill for job_id={job_id}: {exc}")

    logger.info(f"Backfill complete: processed {backfilled}/{len(rows)} results")


async def get_test_history(
    test_name: str,
    limit: int = 20,
    job_name: str = "",
    exclude_job_id: str = "",
) -> dict:
    """Get pass/fail history for a specific test.

    Args:
        test_name: Full test name to look up.
        limit: Maximum number of recent runs to return.
        job_name: Optional filter by job name.
        exclude_job_id: Exclude results from this job ID.

    Returns:
        Dict with test_name, total_runs, failures, passes, failure_rate,
        first_seen, last_seen, last_classification, classifications,
        recent_runs, comments, is_flaky, consecutive_failures, note.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Build optional job_name filter
        job_filter = ""
        params: list = [test_name]
        if job_name:
            job_filter = " AND job_name = ?"
            params.append(job_name)
        if exclude_job_id:
            job_filter += " AND job_id != ?"
            params.append(exclude_job_id)

        # Failure count
        cursor = await db.execute(
            f"SELECT COUNT(*) FROM failure_history WHERE test_name = ?{job_filter}",
            params,
        )
        failures = (await cursor.fetchone())[0]

        if failures == 0:
            return {
                "test_name": test_name,
                "total_runs": 0,
                "failures": 0,
                "passes": 0,
                "failure_rate": 0.0,
                "first_seen": None,
                "last_seen": None,
                "last_classification": "",
                "classifications": {},
                "recent_runs": [],
                "comments": [],
                "is_flaky": False,
                "consecutive_failures": 0,
                "note": "No failure records found for this test.",
            }

        # First and last seen
        cursor = await db.execute(
            f"SELECT MIN(analyzed_at), MAX(analyzed_at) FROM failure_history WHERE test_name = ?{job_filter}",
            params,
        )
        row = await cursor.fetchone()
        first_seen = row[0]
        last_seen = row[1]

        # Last classification (most recent failure)
        cursor = await db.execute(
            f"SELECT classification FROM failure_history WHERE test_name = ?{job_filter} ORDER BY analyzed_at DESC LIMIT 1",
            params,
        )
        last_classification = (await cursor.fetchone())[0] or ""

        # Classification breakdown
        cursor = await db.execute(
            f"SELECT classification, COUNT(*) FROM failure_history WHERE test_name = ?{job_filter} GROUP BY classification",
            params,
        )
        classifications = {}
        for row in await cursor.fetchall():
            if row[0]:
                classifications[row[0]] = row[1]

        # Recent runs (failures only, since we only track failures)
        cursor = await db.execute(
            f"""SELECT job_id, job_name, build_number, error_message, error_signature,
                       classification, child_job_name, child_build_number, analyzed_at
                FROM failure_history WHERE test_name = ?{job_filter}
                ORDER BY analyzed_at DESC LIMIT ?""",
            params + [limit],
        )
        recent_runs = [dict(row) for row in await cursor.fetchall()]

        # Consecutive failures (count from most recent backwards)
        consecutive_failures = 0
        for run in recent_runs:
            if run.get("classification"):
                consecutive_failures += 1
            else:
                break
        # If all runs are failures, count them all
        if consecutive_failures == 0 and recent_runs:
            consecutive_failures = len(recent_runs)

        # Estimate total runs from distinct job_ids in results table
        if job_name:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM results WHERE result_json LIKE ?",
                (f'%"job_name": "{job_name}"%',),
            )
        else:
            cursor = await db.execute("SELECT COUNT(*) FROM results")
        total_runs = (await cursor.fetchone())[0]
        passes = max(0, total_runs - failures)
        failure_rate = failures / total_runs if total_runs > 0 else 0.0

        # Flaky detection
        is_flaky = total_runs >= 5 and 0.2 <= failure_rate <= 0.8

        # Collect error signatures for comment lookup
        signatures = {
            r["error_signature"] for r in recent_runs if r.get("error_signature")
        }

        # Comments related to this test (by test name or error signature)
        comment_conditions = ["test_name = ?"]
        comment_params: list = [test_name]
        if signatures:
            placeholders = ",".join("?" for _ in signatures)
            comment_conditions.append(f"error_signature IN ({placeholders})")
            comment_params.extend(signatures)

        comment_where = " OR ".join(comment_conditions)
        cursor = await db.execute(
            f"SELECT comment, username, created_at FROM comments WHERE {comment_where} ORDER BY created_at DESC",
            comment_params,
        )
        comments = [dict(row) for row in await cursor.fetchall()]

    return {
        "test_name": test_name,
        "total_runs": total_runs,
        "failures": failures,
        "passes": passes,
        "failure_rate": round(failure_rate, 4),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "last_classification": last_classification,
        "classifications": classifications,
        "recent_runs": recent_runs,
        "comments": comments,
        "is_flaky": is_flaky,
        "consecutive_failures": consecutive_failures,
        "note": "Pass count is estimated from total analyzed builds minus recorded failures.",
    }


async def search_by_signature(signature: str, exclude_job_id: str = "") -> dict:
    """Find all tests that failed with the same error signature.

    Args:
        signature: Error signature hash to search for.
        exclude_job_id: Exclude results from this job ID.

    Returns:
        Dict with signature, total_occurrences, unique_tests, tests list,
        last_classification, and comments.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Build optional exclude filter
        exclude_filter = ""
        base_params: list = [signature]
        if exclude_job_id:
            exclude_filter = " AND job_id != ?"
            base_params.append(exclude_job_id)

        # Total occurrences
        cursor = await db.execute(
            f"SELECT COUNT(*) FROM failure_history WHERE error_signature = ?{exclude_filter}",
            base_params,
        )
        total_occurrences = (await cursor.fetchone())[0]

        if total_occurrences == 0:
            return {
                "signature": signature,
                "total_occurrences": 0,
                "unique_tests": 0,
                "tests": [],
                "last_classification": "",
                "comments": [],
            }

        # Tests with this signature and their occurrence counts
        cursor = await db.execute(
            f"SELECT test_name, COUNT(*) as occurrences FROM failure_history "
            f"WHERE error_signature = ?{exclude_filter} GROUP BY test_name ORDER BY occurrences DESC",
            base_params,
        )
        tests = [dict(row) for row in await cursor.fetchall()]
        unique_tests = len(tests)

        # Last classification
        cursor = await db.execute(
            f"SELECT classification FROM failure_history "
            f"WHERE error_signature = ?{exclude_filter} ORDER BY analyzed_at DESC LIMIT 1",
            base_params,
        )
        last_classification = (await cursor.fetchone())[0] or ""

        # Comments related to this signature
        cursor = await db.execute(
            "SELECT comment, username, created_at FROM comments "
            "WHERE error_signature = ? ORDER BY created_at DESC",
            (signature,),
        )
        comments = [dict(row) for row in await cursor.fetchall()]

    return {
        "signature": signature,
        "total_occurrences": total_occurrences,
        "unique_tests": unique_tests,
        "tests": tests,
        "last_classification": last_classification,
        "comments": comments,
    }


async def get_job_stats(job_name: str, exclude_job_id: str = "") -> dict:
    """Get aggregate statistics for a specific job name.

    Args:
        job_name: The job name to get statistics for.
        exclude_job_id: Exclude results from this job ID.

    Returns:
        Dict with job_name, total_builds_analyzed, builds_with_failures,
        overall_failure_rate, most_common_failures, and recent_trend.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Build optional exclude filter
        exclude_filter = ""
        exclude_params: list = []
        if exclude_job_id:
            exclude_filter = " AND job_id != ?"
            exclude_params = [exclude_job_id]

        # Total builds analyzed (from results table, matching job_name in JSON)
        cursor = await db.execute(
            "SELECT COUNT(*) FROM results WHERE result_json LIKE ?",
            (f'%"job_name": "{job_name}"%',),
        )
        total_builds = (await cursor.fetchone())[0]

        if total_builds == 0:
            return {
                "job_name": job_name,
                "total_builds_analyzed": 0,
                "builds_with_failures": 0,
                "overall_failure_rate": 0.0,
                "most_common_failures": [],
                "recent_trend": "stable",
            }

        # Builds with failures (distinct job_ids in failure_history for this job)
        cursor = await db.execute(
            f"SELECT COUNT(DISTINCT job_id) FROM failure_history WHERE job_name = ?{exclude_filter}",
            [job_name] + exclude_params,
        )
        builds_with_failures = (await cursor.fetchone())[0]

        overall_failure_rate = (
            builds_with_failures / total_builds if total_builds > 0 else 0.0
        )

        # Most common failures
        cursor = await db.execute(
            f"SELECT test_name, COUNT(*) as count, classification "
            f"FROM failure_history WHERE job_name = ?{exclude_filter} "
            f"GROUP BY test_name ORDER BY count DESC LIMIT 10",
            [job_name] + exclude_params,
        )
        most_common = [dict(row) for row in await cursor.fetchall()]

        # Recent trend: compare last 7 days vs previous 7 days
        now = datetime.now()
        seven_days_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        fourteen_days_ago = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")

        cursor = await db.execute(
            f"SELECT COUNT(DISTINCT job_id) FROM failure_history "
            f"WHERE job_name = ? AND analyzed_at >= ?{exclude_filter}",
            [job_name, seven_days_ago] + exclude_params,
        )
        recent_failures = (await cursor.fetchone())[0]

        cursor = await db.execute(
            f"SELECT COUNT(DISTINCT job_id) FROM failure_history "
            f"WHERE job_name = ? AND analyzed_at >= ? AND analyzed_at < ?{exclude_filter}",
            [job_name, fourteen_days_ago, seven_days_ago] + exclude_params,
        )
        previous_failures = (await cursor.fetchone())[0]

        if recent_failures < previous_failures:
            recent_trend = "improving"
        elif recent_failures > previous_failures:
            recent_trend = "worsening"
        else:
            recent_trend = "stable"

    return {
        "job_name": job_name,
        "total_builds_analyzed": total_builds,
        "builds_with_failures": builds_with_failures,
        "overall_failure_rate": round(overall_failure_rate, 4),
        "most_common_failures": most_common,
        "recent_trend": recent_trend,
    }


async def get_flaky_tests(
    min_runs: int = 5,
    min_rate: float = 0.2,
    max_rate: float = 0.8,
    job_name: str = "",
    exclude_job_id: str = "",
) -> list[dict]:
    """Find tests with intermittent pass/fail behavior.

    A test is considered flaky if its failure rate is between min_rate and
    max_rate across at least min_runs builds.

    Args:
        min_runs: Minimum number of builds a test must appear in.
        min_rate: Minimum failure rate to be considered flaky.
        max_rate: Maximum failure rate to be considered flaky.
        job_name: Optional filter by job name.
        exclude_job_id: Exclude results from this job ID.

    Returns:
        List of dicts with test_name, failure_rate, total_runs, failures,
        passes, last_status, and job_names.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Get total builds count for estimating pass rate
        if job_name:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM results WHERE result_json LIKE ?",
                (f'%"job_name": "{job_name}"%',),
            )
        else:
            cursor = await db.execute("SELECT COUNT(*) FROM results")
        total_builds = (await cursor.fetchone())[0]

        if total_builds < min_runs:
            return []

        # Get failure counts per test
        job_filter = ""
        params: list = []
        if job_name:
            job_filter = " WHERE job_name = ?"
            params.append(job_name)
        if exclude_job_id:
            job_filter += " AND job_id != ?" if job_filter else " WHERE job_id != ?"
            params.append(exclude_job_id)

        cursor = await db.execute(
            f"SELECT test_name, COUNT(*) as failures, "
            f"GROUP_CONCAT(DISTINCT job_name) as job_names "
            f"FROM failure_history{job_filter} "
            f"GROUP BY test_name",
            params,
        )
        rows = await cursor.fetchall()

        flaky_tests = []
        for row in rows:
            failures = row["failures"]
            failure_rate = failures / total_builds if total_builds > 0 else 0.0
            passes = total_builds - failures

            if total_builds >= min_runs and min_rate <= failure_rate <= max_rate:
                # Get last status (most recent failure entry)
                exclude_sub = ""
                sub_params: list = [row["test_name"]]
                if job_name:
                    exclude_sub += " AND job_name = ?"
                    sub_params.append(job_name)
                if exclude_job_id:
                    exclude_sub += " AND job_id != ?"
                    sub_params.append(exclude_job_id)
                cursor = await db.execute(
                    f"SELECT classification FROM failure_history "
                    f"WHERE test_name = ?{exclude_sub} "
                    f"ORDER BY analyzed_at DESC LIMIT 1",
                    sub_params,
                )
                last_row = await cursor.fetchone()
                last_status = last_row[0] if last_row else ""

                flaky_tests.append(
                    {
                        "test_name": row["test_name"],
                        "failure_rate": round(failure_rate, 4),
                        "total_runs": total_builds,
                        "failures": failures,
                        "passes": passes,
                        "last_status": last_status,
                        "job_names": row["job_names"].split(",")
                        if row["job_names"]
                        else [],
                    }
                )

        # Sort by failure rate descending
        flaky_tests.sort(key=lambda x: x["failure_rate"], reverse=True)
        return flaky_tests


async def get_regressions(
    days: int = 7,
    min_previous_passes: int = 3,
    job_name: str = "",
    exclude_job_id: str = "",
) -> list[dict]:
    """Find tests that recently started failing.

    A regression is a test whose first recorded failure occurred within
    the lookback period.

    Args:
        days: Number of days to look back for new failures.
        min_previous_passes: Minimum previous passing builds required
            (set to 0 to include all new failures).
        job_name: Optional filter by job name.
        exclude_job_id: Exclude results from this job ID.

    Returns:
        List of dicts with test_name, first_failure, first_failure_job,
        consecutive_failures, previous_passes, classification, and
        error_signature.
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Build optional job_name filter
        job_filter = ""
        params: list = [cutoff]
        if job_name:
            job_filter = " AND job_name = ?"
            params.append(job_name)
        if exclude_job_id:
            job_filter += " AND job_id != ?"
            params.append(exclude_job_id)

        # Find tests whose first failure is within the lookback window
        cursor = await db.execute(
            f"SELECT test_name, MIN(analyzed_at) as first_failure, "
            f"COUNT(*) as consecutive_failures "
            f"FROM failure_history "
            f"WHERE analyzed_at >= ?{job_filter} "
            f"GROUP BY test_name "
            f"HAVING MIN(analyzed_at) >= ?",
            params + [cutoff],
        )
        candidates = await cursor.fetchall()

        regressions = []
        for candidate in candidates:
            test_name = candidate["test_name"]

            # Check that the test has no failures before the cutoff window
            check_params: list = [test_name, cutoff]
            check_filter = ""
            if job_name:
                check_filter += " AND job_name = ?"
                check_params.append(job_name)
            if exclude_job_id:
                check_filter += " AND job_id != ?"
                check_params.append(exclude_job_id)
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM failure_history "
                f"WHERE test_name = ? AND analyzed_at < ?{check_filter}",
                check_params,
            )
            old_failures = (await cursor.fetchone())[0]
            if old_failures > 0:
                # Not a new regression, it failed before the window too
                continue

            # Estimate previous passes (total builds minus failures)
            if job_name:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM results WHERE result_json LIKE ?",
                    (f'%"job_name": "{job_name}"%',),
                )
            else:
                cursor = await db.execute("SELECT COUNT(*) FROM results")
            total_builds = (await cursor.fetchone())[0]
            previous_passes = max(0, total_builds - candidate["consecutive_failures"])

            if previous_passes < min_previous_passes:
                continue

            # Get classification and error_signature from first failure
            first_failure_params: list = [test_name]
            first_failure_filter = ""
            if job_name:
                first_failure_filter += " AND job_name = ?"
                first_failure_params.append(job_name)
            if exclude_job_id:
                first_failure_filter += " AND job_id != ?"
                first_failure_params.append(exclude_job_id)
            cursor = await db.execute(
                f"SELECT classification, error_signature, job_name as first_failure_job "
                f"FROM failure_history WHERE test_name = ?{first_failure_filter} "
                f"ORDER BY analyzed_at ASC LIMIT 1",
                first_failure_params,
            )
            detail = await cursor.fetchone()

            regressions.append(
                {
                    "test_name": test_name,
                    "first_failure": candidate["first_failure"],
                    "first_failure_job": detail["first_failure_job"] if detail else "",
                    "consecutive_failures": candidate["consecutive_failures"],
                    "previous_passes": previous_passes,
                    "classification": detail["classification"] if detail else "",
                    "error_signature": detail["error_signature"] if detail else "",
                }
            )

        # Sort by consecutive_failures descending
        regressions.sort(key=lambda x: x["consecutive_failures"], reverse=True)
        return regressions


async def get_trends(
    period: str = "daily",
    days: int = 30,
    job_name: str = "",
    exclude_job_id: str = "",
) -> dict:
    """Get failure rate over time grouped by period.

    Args:
        period: Grouping period, either "daily" or "weekly".
        days: Number of days to look back.
        job_name: Optional filter by job name.
        exclude_job_id: Exclude results from this job ID.

    Returns:
        Dict with period and data list, where each entry has date,
        failures, unique_tests, total_tests, and failure_rate.
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    if period == "weekly":
        date_expr = "strftime('%Y-W%W', analyzed_at)"
    else:
        date_expr = "DATE(analyzed_at)"

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Build optional job_name filter
        job_filter = ""
        params: list = [cutoff]
        if job_name:
            job_filter = " AND job_name = ?"
            params.append(job_name)
        if exclude_job_id:
            job_filter += " AND job_id != ?"
            params.append(exclude_job_id)

        cursor = await db.execute(
            f"SELECT {date_expr} as date, "
            f"COUNT(*) as failures, "
            f"COUNT(DISTINCT test_name) as unique_tests "
            f"FROM failure_history "
            f"WHERE analyzed_at >= ?{job_filter} "
            f"GROUP BY {date_expr} "
            f"ORDER BY date ASC",
            params,
        )
        rows = await cursor.fetchall()

        data = []
        for row in rows:
            data.append(
                {
                    "date": row["date"],
                    "failures": row["failures"],
                    "unique_tests": row["unique_tests"],
                    "total_tests": row["failures"],
                    "failure_rate": 0.0,
                }
            )

    return {
        "period": period,
        "data": data,
    }


async def list_results_for_dashboard(limit: int = 500) -> list[dict]:
    """List recent analysis results with summary data for dashboard display.

    Unlike list_results, this function also extracts key fields from result_json
    for any row that has a stored result (job_name, build_number, failure_count).
    Returns at most ``limit`` results; pagination is handled client-side.

    Args:
        limit: Maximum number of results to return.

    Returns:
        List of result dictionaries enriched with summary data from result_json.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT r.job_id, r.jenkins_url, r.status, r.result_json, r.created_at,
                (SELECT COUNT(*) FROM failure_reviews fr
                 WHERE fr.job_id = r.job_id AND fr.reviewed = 1) AS reviewed_count,
                (SELECT COUNT(*) FROM comments c
                 WHERE c.job_id = r.job_id) AS comment_count
            FROM results r
            ORDER BY r.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            entry: dict = {
                "job_id": row["job_id"],
                "jenkins_url": row["jenkins_url"],
                "status": row["status"],
                "created_at": row["created_at"],
                "reviewed_count": row["reviewed_count"],
                "comment_count": row["comment_count"],
            }
            if row["result_json"]:
                try:
                    result_data = json.loads(row["result_json"])
                    entry["job_name"] = result_data.get("job_name", "")
                    entry["build_number"] = result_data.get("build_number", "")
                    entry["failure_count"] = count_all_failures(result_data)
                    child_jobs = result_data.get("child_job_analyses", [])
                    if child_jobs:
                        entry["child_job_count"] = len(child_jobs)
                except (json.JSONDecodeError, TypeError, AttributeError):
                    logger.debug(f"Failed to parse result_json for job {row['job_id']}")
            results.append(entry)
        return results


async def save_html_report(job_id: str, html_content: str) -> Path:
    """Save an HTML report to disk.

    Args:
        job_id: Unique identifier for the analysis job.
        html_content: The HTML report content.

    Returns:
        Path to the saved HTML file.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{job_id}.html"
    report_path.write_text(html_content, encoding="utf-8")
    logger.debug(f"Saved HTML report for job_id: {job_id} at {report_path}")
    return report_path


async def get_html_report(job_id: str) -> str | None:
    """Read an HTML report from disk.

    Args:
        job_id: Unique identifier for the analysis job.

    Returns:
        HTML content as string, or None if not found.
    """
    report_path = REPORTS_DIR / f"{job_id}.html"
    if report_path.exists():
        return report_path.read_text(encoding="utf-8")
    return None
