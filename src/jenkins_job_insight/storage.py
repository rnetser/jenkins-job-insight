"""SQLite storage for analysis results."""

import json
import os
from datetime import datetime, timedelta, timezone
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

        # Ensure test_classifications table exists before running migrations.
        # On a fresh DB the table may not exist yet, so CREATE TABLE must come
        # before any ALTER TABLE migrations.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS test_classifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_name TEXT NOT NULL,
                job_name TEXT NOT NULL DEFAULT '',
                parent_job_name TEXT NOT NULL DEFAULT '',
                classification TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                references_info TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '',
                job_id TEXT NOT NULL DEFAULT '',
                child_build_number INTEGER NOT NULL DEFAULT 0,
                visible INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migration: add parent_job_name to test_classifications table
        cursor = await db.execute("PRAGMA table_info(test_classifications)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "parent_job_name" not in columns:
            await db.execute(
                "ALTER TABLE test_classifications ADD COLUMN parent_job_name TEXT NOT NULL DEFAULT ''"
            )
            logger.info(
                "Migration: added parent_job_name column to test_classifications"
            )
        else:
            logger.debug(
                "Migration: test_classifications already has parent_job_name column"
            )

        # Migration: add references_info to test_classifications table
        cursor = await db.execute("PRAGMA table_info(test_classifications)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "references_info" not in columns:
            await db.execute(
                "ALTER TABLE test_classifications ADD COLUMN references_info TEXT NOT NULL DEFAULT ''"
            )
            logger.info(
                "Migration: added references_info column to test_classifications"
            )
        else:
            logger.debug(
                "Migration: test_classifications already has references_info column"
            )

        # Migration: add job_id to test_classifications table
        cursor = await db.execute("PRAGMA table_info(test_classifications)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "job_id" not in columns:
            await db.execute(
                "ALTER TABLE test_classifications ADD COLUMN job_id TEXT NOT NULL DEFAULT ''"
            )
            logger.info("Migration: added job_id column to test_classifications")
        else:
            logger.debug("Migration: test_classifications already has job_id column")

        # Migration: add visible column to test_classifications table
        cursor = await db.execute("PRAGMA table_info(test_classifications)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "visible" not in columns:
            await db.execute(
                "ALTER TABLE test_classifications ADD COLUMN visible INTEGER NOT NULL DEFAULT 1"
            )
            logger.info("Migration: added visible column to test_classifications")
        else:
            logger.debug("Migration: test_classifications already has visible column")

        # Migration: add child_build_number to test_classifications table
        cursor = await db.execute("PRAGMA table_info(test_classifications)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "child_build_number" not in columns:
            await db.execute(
                "ALTER TABLE test_classifications ADD COLUMN child_build_number INTEGER NOT NULL DEFAULT 0"
            )
            logger.info(
                "Migration: added child_build_number column to test_classifications"
            )
        else:
            logger.debug(
                "Migration: test_classifications already has child_build_number column"
            )

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

        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tc_test_name ON test_classifications (test_name)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tc_classification ON test_classifications (classification)"
        )

        await db.commit()

    # Backfill failure_history from existing results (runs once when table is empty).
    # This runs synchronously in the lifespan hook, which means the server does not
    # accept requests until it finishes.  This is acceptable because:
    #  1. The backfill only runs once — when failure_history is empty but results exist.
    #  2. Subsequent startups skip it instantly (the table is no longer empty).
    #  3. The expected data volume (hundreds to low-thousands of results) completes
    #     in under a second on typical hardware.
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
    logger.debug(
        f"add_comment: job_id={job_id}, test_name={test_name}, comment_length={len(comment)}, username={username}"
    )
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


async def delete_comment(comment_id: int, username: str, job_id: str = "") -> bool:
    """Delete a comment. Username check is a UI courtesy, not security.

    This project has no authentication — all users are trusted.
    See issue #55 for future auth plans.

    Returns True if deleted, False if not found.
    """
    logger.debug(
        f"delete_comment: comment_id={comment_id}, username={username}, job_id={job_id}"
    )
    async with aiosqlite.connect(DB_PATH) as db:
        # Build query with optional scoping filters
        query = "DELETE FROM comments WHERE id = ?"
        params: list = [comment_id]
        if username:
            query += " AND username = ?"
            params.append(username)
        if job_id:
            query += " AND job_id = ?"
            params.append(job_id)
        cursor = await db.execute(query, params)
        await db.commit()
        deleted = cursor.rowcount > 0
        logger.debug(f"delete_comment: comment_id={comment_id}, deleted={deleted}")
        return deleted


async def get_comments_for_job(job_id: str) -> list[dict]:
    """Get all comments for a specific job."""
    logger.debug(f"get_comments_for_job: job_id={job_id}")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, job_id, test_name, child_job_name, child_build_number, comment, error_signature, username, created_at "
            "FROM comments WHERE job_id = ? ORDER BY created_at ASC",
            (job_id,),
        )
        rows = await cursor.fetchall()
        result = [dict(row) for row in rows]
        logger.debug(f"get_comments_for_job: job_id={job_id}, count={len(result)}")
        return result


async def set_reviewed(
    job_id: str,
    test_name: str,
    reviewed: bool,
    child_job_name: str = "",
    child_build_number: int = 0,
    username: str = "",
) -> None:
    """Set or update the reviewed state for a test failure."""
    logger.debug(
        f"set_reviewed: job_id={job_id}, test_name={test_name}, reviewed={reviewed}, username={username}"
    )
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
    logger.debug(f"get_reviews_for_job: job_id={job_id}")
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
        logger.debug(f"get_reviews_for_job: job_id={job_id}, count={len(result)}")
        return result


async def get_review_status(job_id: str) -> dict:
    """Get review summary for a job (used by dashboard)."""
    logger.debug(f"get_review_status: job_id={job_id}")
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

        logger.debug(
            f"get_review_status: job_id={job_id}, total_failures={total_failures}, reviewed_count={reviewed_count}, comment_count={comment_count}"
        )
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
    logger.debug(
        f"get_historical_comments: test_names_count={len(test_names) if test_names else 0}, signatures_count={len(error_signatures) if error_signatures else 0}, exclude_job_id={exclude_job_id}"
    )
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
        result = [dict(row) for row in rows]
        logger.debug(f"get_historical_comments: count={len(result)}")
        return result


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
    logger.debug(f"get_result: job_id={job_id}")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM results WHERE job_id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        if row:
            logger.debug(
                f"get_result: job_id={job_id}, found=True, status={row['status']}"
            )
            return {
                "job_id": row["job_id"],
                "jenkins_url": row["jenkins_url"],
                "status": row["status"],
                "result": json.loads(row["result_json"])
                if row["result_json"]
                else None,
                "created_at": row["created_at"],
            }
        logger.debug(f"get_result: job_id={job_id}, found=False")
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


def _failure_to_history_row(
    failure: dict,
    job_id: str,
    job_name: str,
    build_number: int,
    child_job_name: str = "",
    child_build_number: int = 0,
    analyzed_at: str = "",
) -> tuple:
    """Convert a single failure dict to a failure_history row tuple.

    Args:
        analyzed_at: Timestamp for when the job was originally analyzed.
            If empty, the DB column default (CURRENT_TIMESTAMP) is used.
    """
    analysis = failure.get("analysis", {})
    classification = (
        "" if isinstance(analysis, str) else analysis.get("classification", "")
    )
    return (
        job_id,
        job_name,
        build_number,
        failure.get("test_name", ""),
        failure.get("error", ""),
        failure.get("error_signature", ""),
        classification,
        child_job_name,
        child_build_number,
        analyzed_at,
    )


def _extract_failures_for_history(
    result_data: dict,
    job_id: str,
    job_name: str,
    build_number: int,
    analyzed_at: str = "",
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
        analyzed_at: Original analysis timestamp from results.created_at.
            Used during backfill to preserve historical chronology.

    Returns:
        List of tuples ready for INSERT:
        (job_id, job_name, build_number, test_name, error_message,
         error_signature, classification, child_job_name, child_build_number, analyzed_at)
    """
    rows: list[tuple] = []

    # Top-level failures (no child context)
    for f in result_data.get("failures", []):
        rows.append(
            _failure_to_history_row(
                f, job_id, job_name, build_number, analyzed_at=analyzed_at
            )
        )

    # Child job analyses (recursive)
    for child in result_data.get("child_job_analyses", []):
        _extract_child_failures_for_history(
            child, job_id, job_name, build_number, rows, analyzed_at=analyzed_at
        )

    return rows


def _extract_child_failures_for_history(
    child: dict,
    job_id: str,
    job_name: str,
    build_number: int,
    rows: list[tuple],
    analyzed_at: str = "",
) -> None:
    """Recursively extract failures from a child job analysis dict.

    Args:
        child: A single child job analysis dictionary.
        job_id: The top-level job identifier.
        job_name: Top-level job name.
        build_number: Top-level build number.
        rows: Accumulator list for insertion tuples.
        analyzed_at: Original analysis timestamp for historical chronology.
    """
    child_job = child.get("job_name", "")
    child_build = child.get("build_number", 0)

    for f in child.get("failures", []):
        rows.append(
            _failure_to_history_row(
                f,
                job_id,
                job_name,
                build_number,
                child_job,
                child_build,
                analyzed_at=analyzed_at,
            )
        )

    for nested in child.get("failed_children", []):
        _extract_child_failures_for_history(
            nested, job_id, job_name, build_number, rows, analyzed_at=analyzed_at
        )


async def populate_failure_history(
    job_id: str, result_data: dict, analyzed_at: str = ""
) -> None:
    """Populate failure_history from a completed analysis result.

    Extracts all failures (top-level and nested children) and inserts
    them into the failure_history table. Idempotent: skips if rows
    already exist for this job_id.

    Args:
        job_id: Unique identifier for the analysis job.
        result_data: Parsed result dictionary from result_json.
        analyzed_at: Original analysis timestamp (results.created_at).
            Used during backfill to preserve historical chronology.
            If empty, the DB default (CURRENT_TIMESTAMP) is used.
    """
    logger.debug(f"populate_failure_history: job_id={job_id}")
    job_name = result_data.get("job_name", "")
    build_number = result_data.get("build_number", 0)

    rows = _extract_failures_for_history(
        result_data, job_id, job_name, build_number, analyzed_at=analyzed_at
    )
    if not rows:
        logger.debug(
            f"populate_failure_history: job_id={job_id}, no failures to insert"
        )
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

        # Use analyzed_at when provided (backfill), otherwise let the DB default apply
        if analyzed_at:
            await db.executemany(
                """
                INSERT INTO failure_history
                    (job_id, job_name, build_number, test_name, error_message,
                     error_signature, classification, child_job_name, child_build_number, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        else:
            await db.executemany(
                """
                INSERT INTO failure_history
                    (job_id, job_name, build_number, test_name, error_message,
                     error_signature, classification, child_job_name, child_build_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                # Strip the analyzed_at field (last element) when not backfilling
                [row[:-1] for row in rows],
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
        # Find completed results that are NOT yet in failure_history.
        # This makes the backfill resumable: if it crashes mid-way,
        # remaining jobs are picked up on next startup.
        cursor = await db.execute(
            "SELECT r.job_id, r.result_json, r.created_at FROM results r "
            "LEFT JOIN failure_history fh ON r.job_id = fh.job_id "
            "WHERE r.status = 'completed' AND r.result_json IS NOT NULL AND fh.job_id IS NULL"
        )
        rows = await cursor.fetchall()

    if not rows:
        logger.info(
            "All completed results already in failure_history, nothing to backfill"
        )
        return

    logger.info(f"Backfilling failure_history from {len(rows)} missing results")
    backfilled = 0
    for job_id, result_json_str, created_at in rows:
        try:
            result_data = json.loads(result_json_str)
            # Skip completed results with zero failures — they have nothing to
            # insert into failure_history, so without this guard the LEFT JOIN
            # would find them "missing" on every startup and reprocess them.
            if count_all_failures(result_data) == 0:
                continue
            # Use the original created_at timestamp to preserve historical chronology
            await populate_failure_history(
                job_id, result_data, analyzed_at=created_at or ""
            )
            backfilled += 1
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            logger.debug(f"Skipping backfill for job_id={job_id}: {exc}")

    logger.info(f"Backfill complete: processed {backfilled}/{len(rows)} results")


async def _get_failure_stats(
    db: aiosqlite.Connection,
    job_filter: str,
    params: list,
) -> tuple[int, str | None, str | None, str]:
    """Return (failure_count, first_seen, last_seen, last_classification).

    Args:
        db: Open aiosqlite connection with row_factory set.
        job_filter: SQL fragment for optional job_name/exclude_job_id filtering.
        params: Bind parameters matching the job_filter placeholders
                (first element is always test_name).
    """
    # Failure count — count distinct builds (job_ids) where this test
    # failed, not raw rows. A test can fail multiple times in different
    # child jobs within the same build, and counting rows would inflate
    # the failure count relative to total_runs (which counts builds).
    cursor = await db.execute(
        f"SELECT COUNT(DISTINCT job_id) FROM failure_history WHERE test_name = ?{job_filter}",
        params,
    )
    failures = (await cursor.fetchone())[0]

    if failures == 0:
        return 0, None, None, ""

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

    return failures, first_seen, last_seen, last_classification


async def _get_classification_breakdown(
    db: aiosqlite.Connection,
    job_filter: str,
    params: list,
) -> dict[str, int]:
    """Return a dict mapping classification labels to their counts.

    Args:
        db: Open aiosqlite connection with row_factory set.
        job_filter: SQL fragment for optional job_name/exclude_job_id filtering.
        params: Bind parameters matching the job_filter placeholders
                (first element is always test_name).
    """
    cursor = await db.execute(
        f"SELECT classification, COUNT(*) FROM failure_history WHERE test_name = ?{job_filter} GROUP BY classification",
        params,
    )
    classifications: dict[str, int] = {}
    for row in await cursor.fetchall():
        if row[0]:
            classifications[row[0]] = row[1]
    return classifications


async def _get_related_comments(
    db: aiosqlite.Connection,
    test_name: str,
    signatures: set[str],
    exclude_job_id: str,
) -> list[dict]:
    """Return comments related to a test by name or error signature.

    Args:
        db: Open aiosqlite connection with row_factory set.
        test_name: Full test name to look up.
        signatures: Set of error_signature hashes from recent runs.
        exclude_job_id: Exclude comments from this job ID.
    """
    comment_conditions = ["test_name = ?"]
    comment_params: list = [test_name]
    if signatures:
        placeholders = ",".join("?" for _ in signatures)
        comment_conditions.append(f"error_signature IN ({placeholders})")
        comment_params.extend(signatures)

    comment_where = " OR ".join(comment_conditions)
    if exclude_job_id:
        comment_where = f"({comment_where}) AND job_id != ?"
        comment_params.append(exclude_job_id)
    cursor = await db.execute(
        f"SELECT comment, username, created_at FROM comments WHERE {comment_where} ORDER BY created_at DESC",
        comment_params,
    )
    return [dict(row) for row in await cursor.fetchall()]


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
        recent_runs, comments, consecutive_failures, note.
    """
    logger.debug(
        f"get_test_history: test_name={test_name}, limit={limit}, job_name={job_name}"
    )
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

        failures, first_seen, last_seen, last_classification = await _get_failure_stats(
            db, job_filter, params
        )

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
                "consecutive_failures": 0,
                "note": "No failure records found for this test.",
            }

        classifications = await _get_classification_breakdown(db, job_filter, params)

        # Recent runs (failures only, since we only track failures)
        cursor = await db.execute(
            f"""SELECT job_id, job_name, build_number, error_message, error_signature,
                       classification, child_job_name, child_build_number, analyzed_at
                FROM failure_history WHERE test_name = ?{job_filter}
                ORDER BY analyzed_at DESC LIMIT ?""",
            [*params, limit],
        )
        recent_runs = [dict(row) for row in await cursor.fetchall()]

        # Consecutive failures — every row in recent_runs is a failure
        # (failure_history only records failures), so count them directly.
        # NOTE: Without pass tracking we cannot detect an intervening
        # successful build that would break the streak. The count here
        # represents the number of recent consecutive failure records.
        # Adding pass tracking is deferred to a future enhancement.
        consecutive_failures = len(recent_runs)

        # Count only completed results for the denominator so that
        # pending/running/failed analyses don't inflate total_runs.
        if job_name:
            total_query = (
                "SELECT COUNT(DISTINCT job_id) FROM results "
                "WHERE status = 'completed' "
                "AND json_extract(result_json, '$.job_name') = ?"
            )
            total_params: list = [job_name]
        else:
            # Without job_name filtering, pass count cannot be accurately derived.
            # failure_history only records failures, not total test executions,
            # so total_runs == failures and passes would always be 0 (100% failure).
            total_query = None
            total_params = []
        if total_query is not None:
            if exclude_job_id:
                total_query += " AND job_id != ?"
                total_params.append(exclude_job_id)
            cursor = await db.execute(total_query, total_params)
            total_runs = (await cursor.fetchone())[0]
            passes = max(0, total_runs - failures)
            failure_rate = round(failures / total_runs, 4) if total_runs > 0 else 0.0
        else:
            total_runs = failures
            passes = None
            failure_rate = None

        # Collect error signatures for comment lookup
        signatures = {
            r["error_signature"] for r in recent_runs if r.get("error_signature")
        }

        comments = await _get_related_comments(
            db, test_name, signatures, exclude_job_id
        )

    logger.debug(
        f"get_test_history: test_name={test_name}, failures={failures}, passes={passes}, recent_runs={len(recent_runs)}"
    )
    note = (
        "Pass count is estimated from total analyzed builds minus recorded failures."
        if passes is not None
        else "Pass/fail stats unavailable without job_name — failure_history only records failures."
    )
    return {
        "test_name": test_name,
        "total_runs": total_runs,
        "failures": failures,
        "passes": passes,
        "failure_rate": failure_rate,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "last_classification": last_classification,
        "classifications": classifications,
        "recent_runs": recent_runs,
        "comments": comments,
        "consecutive_failures": consecutive_failures,
        "note": note,
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
    logger.debug(
        f"search_by_signature: signature={signature}, exclude_job_id={exclude_job_id}"
    )
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
        comments_query = (
            "SELECT comment, username, created_at FROM comments "
            "WHERE error_signature = ?"
        )
        comments_params: list[str] = [signature]
        if exclude_job_id:
            comments_query += " AND job_id != ?"
            comments_params.append(exclude_job_id)
        comments_query += " ORDER BY created_at DESC"
        cursor = await db.execute(comments_query, comments_params)
        comments = [dict(row) for row in await cursor.fetchall()]

    logger.debug(
        f"search_by_signature: signature={signature}, total_occurrences={total_occurrences}, unique_tests={unique_tests}"
    )
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
    logger.debug(f"get_job_stats: job_name={job_name}, exclude_job_id={exclude_job_id}")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Build optional exclude filter
        exclude_filter = ""
        exclude_params: list = []
        if exclude_job_id:
            exclude_filter = " AND job_id != ?"
            exclude_params = [exclude_job_id]

        # Total completed builds — count from results table (not failure_history)
        # so that builds with zero failures are included in the denominator.
        # Uses json_extract to match job_name stored in result_json.
        total_builds_query = (
            "SELECT COUNT(DISTINCT job_id) FROM results "
            "WHERE status = 'completed' AND "
            "json_extract(result_json, '$.job_name') = ?"
        )
        total_builds_params: list = [job_name]
        if exclude_job_id:
            total_builds_query += " AND job_id != ?"
            total_builds_params.append(exclude_job_id)
        cursor = await db.execute(total_builds_query, total_builds_params)
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
        # GROUP BY test_name, classification to avoid non-deterministic
        # classification values when a test has been reclassified over time.
        cursor = await db.execute(
            f"SELECT test_name, COUNT(*) as count, classification "
            f"FROM failure_history WHERE job_name = ?{exclude_filter} "
            f"GROUP BY test_name, classification ORDER BY count DESC LIMIT 10",
            [job_name, *exclude_params],
        )
        most_common = [dict(row) for row in await cursor.fetchall()]

        # Recent trend: compare last 7 days vs previous 7 days
        now = datetime.now(tz=timezone.utc)
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
    logger.debug(f"get_trends: period={period}, days={days}, job_name={job_name}")
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # Failures are bucketed by failure_history.analyzed_at; build totals are
    # bucketed by results.created_at.  These two clocks can differ slightly
    # (analysis completes after the result row is created), so a build near a
    # bucket boundary may land in adjacent buckets for the two series.  The
    # mismatch is small relative to daily/weekly bucket widths and is acceptable.
    if period == "weekly":
        fh_date_expr = "strftime('%Y-W%W', analyzed_at)"
        results_date_expr = "strftime('%Y-W%W', created_at)"
    else:
        fh_date_expr = "DATE(analyzed_at)"
        results_date_expr = "DATE(created_at)"

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
            f"SELECT {fh_date_expr} as date, "
            f"COUNT(DISTINCT job_id) as failures, "
            f"COUNT(DISTINCT test_name) as unique_tests "
            f"FROM failure_history "
            f"WHERE analyzed_at >= ?{job_filter} "
            f"GROUP BY {fh_date_expr} "
            f"ORDER BY date ASC",
            params,
        )
        rows = await cursor.fetchall()

        # Get per-bucket build counts from completed results only.
        # Count directly from results (no JOIN through failure_history)
        # so that clean builds are included in the denominator.
        if job_name:
            bucket_query = (
                f"SELECT {results_date_expr} as date, COUNT(DISTINCT r.job_id) as total "
                f"FROM results r "
                f"WHERE r.status = 'completed' AND r.created_at >= ? "
                f"AND json_extract(r.result_json, '$.job_name') = ?"
            )
            bucket_params: list = [cutoff, job_name]
        else:
            bucket_query = (
                f"SELECT {results_date_expr} as date, COUNT(DISTINCT r.job_id) as total "
                f"FROM results r "
                f"WHERE r.status = 'completed' AND r.created_at >= ?"
            )
            bucket_params = [cutoff]
        if exclude_job_id:
            bucket_query += " AND r.job_id != ?"
            bucket_params.append(exclude_job_id)
        bucket_query += f" GROUP BY {results_date_expr}"
        cursor = await db.execute(bucket_query, bucket_params)
        builds_per_bucket: dict[str, int] = {}
        for brow in await cursor.fetchall():
            builds_per_bucket[brow[0]] = brow[1]

        # Build a lookup from the failure rows so zero-failure buckets
        # (present in builds_per_bucket but absent from failure_history)
        # are emitted with failures=0 and failure_rate=0.0.
        failure_map: dict[str, dict] = {}
        for row in rows:
            failure_map[row["date"]] = {
                "failures": row["failures"],
                "unique_tests": row["unique_tests"],
            }

        # Iterate over all build buckets to include clean-build dates.
        all_dates = sorted(failure_map.keys() | builds_per_bucket.keys())
        data = []
        for date in all_dates:
            bucket_total = builds_per_bucket.get(date, 0)
            finfo = failure_map.get(date, {"failures": 0, "unique_tests": 0})
            failures_count = finfo["failures"]
            data.append(
                {
                    "date": date,
                    "failures": failures_count,
                    "unique_tests": finfo["unique_tests"],
                    "total_tests": bucket_total,
                    "failure_rate": round(failures_count / bucket_total, 4)
                    if bucket_total > 0
                    else 0.0,
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


async def get_parent_job_name_for_test(test_name: str, job_id: str = "") -> str:
    """Look up the parent pipeline job name for a test from failure_history.

    Args:
        test_name: The test name to look up.
        job_id: When provided, scopes the lookup to a specific analysis job
                to avoid cross-job leakage.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if job_id:
            query = (
                "SELECT job_name FROM failure_history "
                "WHERE test_name = ? AND job_id = ? "
                "ORDER BY analyzed_at DESC LIMIT 1"
            )
            params: tuple = (test_name, job_id)
        else:
            query = (
                "SELECT job_name FROM failure_history "
                "WHERE test_name = ? "
                "ORDER BY analyzed_at DESC LIMIT 1"
            )
            params = (test_name,)
        cursor = await db.execute(query, params)
        row = await cursor.fetchone()
        return row[0] if row else ""


async def set_test_classification(
    test_name: str,
    classification: str,
    *,
    job_id: str,
    reason: str = "",
    job_name: str = "",
    parent_job_name: str = "",
    created_by: str = "",
    references: str = "",
    child_build_number: int = 0,
    visible: int = 1,
) -> int:
    """Set a classification for a test (e.g., FLAKY, REGRESSION).

    Can be set by the AI during analysis or by humans.

    Args:
        job_id: Required — scopes the classification to a specific analysis job.
        visible: Whether the classification is immediately visible.
            Set to 0 during AI analysis; revealed after analysis completes.
    """
    if not job_id or not job_id.strip():
        raise ValueError("job_id is required for test classification")
    logger.debug(
        f"set_test_classification: test_name={test_name}, classification={classification}, "
        f"parent_job_name={parent_job_name}, job_id={job_id}, created_by={created_by}, visible={visible}"
    )
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO test_classifications (test_name, job_name, parent_job_name, classification, reason, references_info, created_by, job_id, child_build_number, visible) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                test_name,
                job_name,
                parent_job_name,
                classification,
                reason,
                references,
                created_by,
                job_id,
                child_build_number,
                visible,
            ),
        )

        # Mirror classification into failure_history so that filters on
        # failure_history.classification (used by get_all_failures, get_test_history)
        # reflect manual/AI reclassifications from test_classifications.
        # Only mirror when visible=1 to prevent hidden AI classifications from
        # leaking into failure_history before analysis completes.
        # When visible=0, make_classifications_visible() handles the mirror.
        if visible:
            await db.execute(
                "UPDATE failure_history SET classification = ? "
                "WHERE test_name = ? AND child_job_name = ? AND child_build_number = ? AND job_id = ?",
                [classification, test_name, job_name, child_build_number, job_id],
            )

        await db.commit()
        return cursor.lastrowid


async def get_test_classifications(
    test_name: str = "",
    classification: str = "",
    job_name: str = "",
    parent_job_name: str = "",
    job_id: str = "",
) -> list[dict]:
    """Get visible test classifications.

    Only returns classifications with visible=1. During AI analysis,
    classifications are created with visible=0 and revealed after
    analysis completes via make_classifications_visible().
    """
    logger.debug(
        f"get_test_classifications: test_name={test_name!r}, classification={classification!r}, "
        f"job_name={job_name!r}, parent_job_name={parent_job_name!r}, job_id={job_id!r}"
    )
    conditions = ["tc.visible = 1"]
    params: list[str] = []

    if test_name:
        conditions.append("tc.test_name = ?")
        params.append(test_name)
    if classification:
        conditions.append("tc.classification = ?")
        params.append(classification)
    if job_name:
        conditions.append("tc.job_name = ?")
        params.append(job_name)
    if parent_job_name:
        conditions.append("tc.parent_job_name = ?")
        params.append(parent_job_name)
    if job_id:
        conditions.append("tc.job_id = ?")
        params.append(job_id)

    where = " AND ".join(conditions)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"SELECT tc.id, tc.test_name, tc.job_name, tc.parent_job_name, tc.classification, "
            f"tc.reason, tc.references_info, tc.created_by, tc.job_id, tc.child_build_number, tc.created_at "
            f"FROM test_classifications tc "
            f"WHERE {where} "
            f"ORDER BY tc.created_at DESC",
            params,
        )
        rows = await cursor.fetchall()
        result = [dict(row) for row in rows]
        logger.debug(f"get_test_classifications: count={len(result)}")
        return result


async def make_classifications_visible(job_id: str) -> None:
    """Make all classifications for a job visible after analysis completes.

    Also mirrors classifications into failure_history. The mirror is deferred
    from set_test_classification (which creates rows with visible=0 during
    analysis) to here so that failure_history doesn't leak hidden AI labels.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Fetch hidden classifications before flipping so we can mirror them.
        # ORDER BY created_at DESC ensures latest-wins when deduplicating
        # by (test_name, job_name, child_build_number) below.
        cursor = await db.execute(
            "SELECT test_name, job_name, child_build_number, classification "
            "FROM test_classifications WHERE job_id = ? AND visible = 0 "
            "ORDER BY created_at DESC",
            (job_id,),
        )
        all_rows = await cursor.fetchall()

        # Deduplicate: keep only the latest classification per key
        # (latest-wins, since we ordered by created_at DESC).
        seen: set[tuple[str, str, int]] = set()
        rows = []
        for row in all_rows:
            key = (row["test_name"], row["job_name"], row["child_build_number"])
            if key not in seen:
                seen.add(key)
                rows.append(row)

        await db.execute(
            "UPDATE test_classifications SET visible = 1 WHERE job_id = ? AND visible = 0",
            (job_id,),
        )

        # Mirror each newly-visible classification into failure_history
        for row in rows:
            await db.execute(
                "UPDATE failure_history SET classification = ? "
                "WHERE test_name = ? AND child_job_name = ? AND child_build_number = ? AND job_id = ?",
                [
                    row["classification"],
                    row["test_name"],
                    row["job_name"],
                    row["child_build_number"],
                    job_id,
                ],
            )

        await db.commit()
    logger.debug(
        f"make_classifications_visible: job_id={job_id}, mirrored={len(rows)} classifications"
    )


async def get_all_failures(
    search: str = "",
    job_name: str = "",
    classification: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Get paginated failure history with optional filters.

    Returns dict with 'failures' list and 'total' count.

    Args:
        search: Free-text search across test_name, error_message, and job_name.
        job_name: Exact match filter on job_name column.
        classification: Exact match filter on classification column.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip for pagination.

    Returns:
        Dict with ``failures`` (list of row dicts) and ``total`` (int).
    """
    logger.debug(
        f"get_all_failures: search={search!r}, job_name={job_name!r}, classification={classification!r}, limit={limit}, offset={offset}"
    )
    conditions: list[str] = []
    params: list[str | int] = []

    if search:
        conditions.append(
            "(test_name LIKE ? OR error_message LIKE ? OR job_name LIKE ?)"
        )
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    if job_name:
        conditions.append("job_name = ?")
        params.append(job_name)
    if classification:
        conditions.append("classification = ?")
        params.append(classification)

    where = " AND ".join(conditions) if conditions else "1=1"

    async with aiosqlite.connect(DB_PATH) as db:
        # Get total count
        cursor = await db.execute(
            f"SELECT COUNT(*) FROM failure_history WHERE {where}",
            params,
        )
        total = (await cursor.fetchone())[0]

        # Get paginated results
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"SELECT id, job_id, job_name, build_number, test_name, error_message, "
            f"error_signature, classification, child_job_name, child_build_number, analyzed_at "
            f"FROM failure_history WHERE {where} ORDER BY analyzed_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        rows = await cursor.fetchall()
        logger.debug(f"get_all_failures: total={total}, returned={len(rows)}")
        return {
            "failures": [dict(row) for row in rows],
            "total": total,
        }


async def delete_job(job_id: str) -> bool:
    """Delete an analyzed job and all its related data."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Delete from all related tables
        await db.execute("DELETE FROM comments WHERE job_id = ?", (job_id,))
        await db.execute("DELETE FROM failure_reviews WHERE job_id = ?", (job_id,))
        await db.execute("DELETE FROM failure_history WHERE job_id = ?", (job_id,))
        await db.execute("DELETE FROM test_classifications WHERE job_id = ?", (job_id,))
        cursor = await db.execute("DELETE FROM results WHERE job_id = ?", (job_id,))
        job_existed = cursor.rowcount > 0
        await db.commit()

        # Delete cached HTML report (best-effort — a race with another
        # remover or a transient OS error should not turn a successful
        # DB delete into a 500).
        try:
            report_path = REPORTS_DIR / f"{job_id}.html"
            report_path.unlink(missing_ok=True)
        except OSError:
            logger.debug(
                f"delete_job: failed to remove cached report for job_id={job_id}"
            )

        return job_existed


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
