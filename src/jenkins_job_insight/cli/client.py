"""HTTP client for the jenkins-job-insight REST API."""

from typing import Any

import httpx


class JJIError(Exception):
    """Error from the JJI API or connection failure."""

    def __init__(self, status_code: int = 0, detail: str = ""):
        self.status_code = status_code
        self.detail = detail
        msg = f"HTTP {status_code}" if status_code else "Connection error"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


class JJIClient:
    """Synchronous client for the jenkins-job-insight REST API.

    All methods return parsed JSON dicts. Raises JJIError on HTTP
    errors or connection failures.

    Args:
        server_url: Base URL of the JJI server (required).
        timeout: Request timeout in seconds.
        username: Username sent as a cookie for authenticated actions.
    """

    def __init__(
        self,
        server_url: str,
        timeout: float = 30.0,
        username: str = "",
    ):
        self.server_url = server_url.rstrip("/")
        cookies = {}
        if username:
            cookies["jji_username"] = username
        self._client = httpx.Client(
            base_url=self.server_url,
            timeout=timeout,
            cookies=cookies,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        accept_statuses: tuple[int, ...] = (200,),
    ) -> Any:
        """Send an HTTP request and return parsed JSON.

        Args:
            method: HTTP method (GET, POST, DELETE, PUT).
            path: URL path (e.g. "/health").
            params: Query parameters.
            json: JSON body for POST/PUT.
            accept_statuses: HTTP status codes treated as success.

        Returns:
            Parsed JSON response.

        Raises:
            JJIError: On HTTP errors or connection failures.
        """
        # Strip None values from params
        if params:
            params = {k: v for k, v in params.items() if v is not None and v != ""}

        try:
            response = self._client.request(method, path, params=params, json=json)
        except httpx.TimeoutException as exc:
            raise JJIError(
                status_code=0,
                detail=f"Request timed out: {exc}",
            ) from exc
        except httpx.RequestError as exc:
            raise JJIError(
                status_code=0,
                detail=f"Cannot connect to {self.server_url}: {exc}",
            ) from exc

        if response.status_code not in accept_statuses:
            detail = ""
            try:
                body = response.json()
                detail = body.get("detail", str(body))
            except (ValueError, KeyError):
                detail = response.text
            raise JJIError(
                status_code=response.status_code,
                detail=detail,
            )

        return response.json()

    # -- Health ---------------------------------------------------------------

    def health(self) -> dict:
        """Check server health. GET /health"""
        return self._request("GET", "/health")

    # -- Results --------------------------------------------------------------

    def list_results(self, limit: int = 50) -> list[dict]:
        """List recent analyzed jobs. GET /results?limit="""
        return self._request("GET", "/results", params={"limit": limit})

    def get_result(self, job_id: str) -> dict:
        """Get a stored result by job_id. GET /results/{job_id}"""
        return self._request("GET", f"/results/{job_id}")

    def delete_job(self, job_id: str) -> dict:
        """Delete a job and all related data. DELETE /results/{job_id}"""
        return self._request("DELETE", f"/results/{job_id}")

    # -- Analysis -------------------------------------------------------------

    def analyze(
        self,
        job_name: str,
        build_number: int,
        sync: bool = False,
        **kwargs,
    ) -> dict:
        """Submit a Jenkins job for analysis. POST /analyze?sync=

        Args:
            job_name: Jenkins job name.
            build_number: Build number to analyze.
            sync: If True, wait for result. If False, return immediately.
            **kwargs: Additional fields for the AnalyzeRequest body.

        Returns:
            Queued status (async) or full result (sync).
        """
        body = {"job_name": job_name, "build_number": build_number, **kwargs}
        accept = (200,) if sync else (202,)
        return self._request(
            "POST",
            "/analyze",
            params={"sync": str(sync).lower()},
            json=body,
            accept_statuses=accept,
        )

    # -- History --------------------------------------------------------------

    def get_test_history(
        self,
        test_name: str,
        limit: int = 20,
        job_name: str = "",
        exclude_job_id: str = "",
    ) -> dict:
        """Get pass/fail history for a test. GET /history/test/{test_name}"""
        return self._request(
            "GET",
            f"/history/test/{test_name}",
            params={
                "limit": limit,
                "job_name": job_name,
                "exclude_job_id": exclude_job_id,
            },
        )

    def search_by_signature(
        self,
        signature: str,
        exclude_job_id: str = "",
    ) -> dict:
        """Find tests by error signature. GET /history/search?signature="""
        return self._request(
            "GET",
            "/history/search",
            params={"signature": signature, "exclude_job_id": exclude_job_id},
        )

    def get_job_stats(
        self,
        job_name: str,
        exclude_job_id: str = "",
    ) -> dict:
        """Get job-level statistics. GET /history/stats/{job_name}"""
        return self._request(
            "GET",
            f"/history/stats/{job_name}",
            params={"exclude_job_id": exclude_job_id},
        )

    def get_trends(
        self,
        period: str = "daily",
        days: int = 30,
        job_name: str = "",
        exclude_job_id: str = "",
    ) -> dict:
        """Get failure rate trends. GET /history/trends"""
        return self._request(
            "GET",
            "/history/trends",
            params={
                "period": period,
                "days": days,
                "job_name": job_name,
                "exclude_job_id": exclude_job_id,
            },
        )

    def get_all_failures(
        self,
        search: str = "",
        job_name: str = "",
        classification: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Get paginated failure history. GET /history/failures"""
        return self._request(
            "GET",
            "/history/failures",
            params={
                "search": search,
                "job_name": job_name,
                "classification": classification,
                "limit": limit,
                "offset": offset,
            },
        )

    # -- Classifications ------------------------------------------------------

    def classify_test(
        self,
        test_name: str,
        classification: str,
        job_id: str,
        reason: str = "",
        job_name: str = "",
        references: str = "",
        child_build_number: int = 0,
    ) -> dict:
        """Classify a test. POST /history/classify"""
        body = {
            "test_name": test_name,
            "classification": classification,
            "job_id": job_id,
            "reason": reason,
            "job_name": job_name,
            "references": references,
            "child_build_number": child_build_number,
        }
        return self._request(
            "POST", "/history/classify", json=body, accept_statuses=(201,)
        )

    def get_classifications(
        self,
        test_name: str = "",
        classification: str = "",
        job_name: str = "",
        parent_job_name: str = "",
        job_id: str = "",
    ) -> dict:
        """Get test classifications. GET /history/classifications"""
        return self._request(
            "GET",
            "/history/classifications",
            params={
                "test_name": test_name,
                "classification": classification,
                "job_name": job_name,
                "parent_job_name": parent_job_name,
                "job_id": job_id,
            },
        )

    # -- Comments -------------------------------------------------------------

    def get_comments(self, job_id: str) -> dict:
        """Get comments and reviews for a job. GET /results/{job_id}/comments"""
        return self._request("GET", f"/results/{job_id}/comments")

    def add_comment(
        self,
        job_id: str,
        test_name: str,
        comment: str,
        child_job_name: str = "",
        child_build_number: int = 0,
    ) -> dict:
        """Add a comment to a test failure. POST /results/{job_id}/comments"""
        body: dict = {
            "test_name": test_name,
            "comment": comment,
        }
        if child_job_name:
            body["child_job_name"] = child_job_name
            body["child_build_number"] = child_build_number
        return self._request(
            "POST",
            f"/results/{job_id}/comments",
            json=body,
            accept_statuses=(201,),
        )

    def delete_comment(self, job_id: str, comment_id: int) -> dict:
        """Delete a comment. DELETE /results/{job_id}/comments/{comment_id}"""
        return self._request("DELETE", f"/results/{job_id}/comments/{comment_id}")

    # -- Review ---------------------------------------------------------------

    def get_review_status(self, job_id: str) -> dict:
        """Get review summary for a job. GET /results/{job_id}/review-status"""
        return self._request("GET", f"/results/{job_id}/review-status")

    # -- Bug Creation ---------------------------------------------------------

    def preview_github_issue(
        self,
        job_id: str,
        test_name: str,
        child_job_name: str = "",
        child_build_number: int = 0,
    ) -> dict:
        """Preview a GitHub issue. POST /results/{job_id}/preview-github-issue"""
        body: dict = {"test_name": test_name}
        if child_job_name:
            body["child_job_name"] = child_job_name
            body["child_build_number"] = child_build_number
        return self._request(
            "POST", f"/results/{job_id}/preview-github-issue", json=body
        )

    def preview_jira_bug(
        self,
        job_id: str,
        test_name: str,
        child_job_name: str = "",
        child_build_number: int = 0,
    ) -> dict:
        """Preview a Jira bug. POST /results/{job_id}/preview-jira-bug"""
        body: dict = {"test_name": test_name}
        if child_job_name:
            body["child_job_name"] = child_job_name
            body["child_build_number"] = child_build_number
        return self._request("POST", f"/results/{job_id}/preview-jira-bug", json=body)

    def create_github_issue(
        self,
        job_id: str,
        test_name: str,
        title: str,
        body: str,
        child_job_name: str = "",
        child_build_number: int = 0,
    ) -> dict:
        """Create a GitHub issue. POST /results/{job_id}/create-github-issue"""
        payload: dict = {
            "test_name": test_name,
            "title": title,
            "body": body,
        }
        if child_job_name:
            payload["child_job_name"] = child_job_name
            payload["child_build_number"] = child_build_number
        return self._request(
            "POST",
            f"/results/{job_id}/create-github-issue",
            json=payload,
            accept_statuses=(201,),
        )

    def create_jira_bug(
        self,
        job_id: str,
        test_name: str,
        title: str,
        body: str,
        child_job_name: str = "",
        child_build_number: int = 0,
    ) -> dict:
        """Create a Jira bug. POST /results/{job_id}/create-jira-bug"""
        payload: dict = {
            "test_name": test_name,
            "title": title,
            "body": body,
        }
        if child_job_name:
            payload["child_job_name"] = child_job_name
            payload["child_build_number"] = child_build_number
        return self._request(
            "POST",
            f"/results/{job_id}/create-jira-bug",
            json=payload,
            accept_statuses=(201,),
        )

    def override_classification(
        self,
        job_id: str,
        test_name: str,
        classification: str,
        child_job_name: str = "",
        child_build_number: int = 0,
    ) -> dict:
        """Override classification. PUT /results/{job_id}/override-classification"""
        payload: dict = {
            "test_name": test_name,
            "classification": classification,
        }
        if child_job_name:
            payload["child_job_name"] = child_job_name
            payload["child_build_number"] = child_build_number
        return self._request(
            "PUT", f"/results/{job_id}/override-classification", json=payload
        )
