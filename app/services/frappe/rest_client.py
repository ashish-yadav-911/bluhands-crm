"""HTTP client for one tenant's Frappe site.

This is the applier's primary transport, in preference to shelling out to
`bench`. Three reasons, in order of weight:

1. Frappe returns *structured, per-object* errors here (`exc_type` plus
   `_server_messages`). That is the difference between telling a customer
   "the build failed" and "the Service Visit doctype failed because its
   Customer link points at a doctype that doesn't exist". `bench` gives a
   traceback on stderr that would have to be scraped.
2. Requests go through the full document lifecycle -- validation, link
   checking, `on_update`. A spec that would render a broken form fails at
   apply time rather than when the customer first clicks it.
3. Applying a spec is hundreds of operations. Each `docker compose exec`
   costs roughly a second of container overhead on Windows; each HTTP call
   costs milliseconds.

Site selection is by Host header, not by hostname, so this connects to
127.0.0.1 and never depends on DNS resolving the tenant's domain.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.services.runtime import TenantRuntime


class FrappeApiError(RuntimeError):
    """A structured Frappe error. `exc_type` is Frappe's own exception name
    (ValidationError, PermissionError, DuplicateEntryError, ...), which is
    what callers should branch on rather than the HTTP status -- Frappe uses
    417 for most validation failures."""

    def __init__(self, status_code: int, exc_type: str, messages: list[str], raw: str):
        self.status_code = status_code
        self.exc_type = exc_type
        self.messages = messages
        self.raw = raw
        detail = "; ".join(messages) if messages else raw[:500]
        super().__init__(f"[{status_code} {exc_type or 'FrappeError'}] {detail}")


def _parse_server_messages(payload: dict[str, Any]) -> list[str]:
    """`_server_messages` is a JSON string containing a JSON list of JSON
    strings -- three levels of encoding, each of which can be absent or
    malformed. Never let parsing the error message become the error."""
    raw = payload.get("_server_messages")
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except (ValueError, TypeError):
        return [str(raw)]

    messages: list[str] = []
    for entry in entries if isinstance(entries, list) else [entries]:
        try:
            parsed = json.loads(entry)
            messages.append(str(parsed.get("message", entry)) if isinstance(parsed, dict) else str(parsed))
        except (ValueError, TypeError):
            messages.append(str(entry))
    return messages


class FrappeClient:
    def __init__(
        self,
        base_url: str,
        host_header: str,
        api_key: str,
        api_secret: str,
        timeout: float = 60.0,
    ):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "Host": host_header,
                "Authorization": f"token {api_key}:{api_secret}",
                "Accept": "application/json",
            },
        )

    @classmethod
    def for_runtime(cls, runtime: TenantRuntime, api_key: str, api_secret: str, **kwargs) -> "FrappeClient":
        return cls(
            base_url=runtime.rest_base_url,
            host_header=runtime.host_header,
            api_key=api_key,
            api_secret=api_secret,
            **kwargs,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "FrappeClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # --- plumbing -------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> Any:
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            raise FrappeApiError(
                status_code=response.status_code,
                exc_type=str(payload.get("exc_type", "")),
                messages=_parse_server_messages(payload),
                raw=response.text,
            )
        if not response.content:
            return None
        return response.json()

    # --- documents ------------------------------------------------------

    def ping(self) -> bool:
        return self._request("GET", "/api/method/ping").get("message") == "pong"

    def logged_user(self) -> str:
        return self._request("GET", "/api/method/frappe.auth.get_logged_user")["message"]

    def get_doc(self, doctype: str, name: str) -> dict | None:
        """Returns None when the document does not exist, so callers can use
        this directly as an existence check before deciding insert vs update."""
        try:
            return self._request("GET", f"/api/resource/{doctype}/{name}")["data"]
        except FrappeApiError as exc:
            if exc.status_code == 404 or exc.exc_type == "DoesNotExistError":
                return None
            raise

    def exists(self, doctype: str, name: str) -> bool:
        return self.get_doc(doctype, name) is not None

    def get_list(
        self,
        doctype: str,
        filters: list | None = None,
        fields: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit_page_length": limit}
        if filters is not None:
            params["filters"] = json.dumps(filters)
        if fields is not None:
            params["fields"] = json.dumps(fields)
        return self._request("GET", f"/api/resource/{doctype}", params=params)["data"]

    def insert(self, doctype: str, doc: dict) -> dict:
        return self._request("POST", f"/api/resource/{doctype}", json=doc)["data"]

    def update(self, doctype: str, name: str, doc: dict) -> dict:
        return self._request("PUT", f"/api/resource/{doctype}/{name}", json=doc)["data"]

    def delete(self, doctype: str, name: str) -> None:
        self._request("DELETE", f"/api/resource/{doctype}/{name}")

    def upsert(self, doctype: str, name: str, doc: dict) -> tuple[dict, str]:
        """Insert or update by deterministic name. Returns (doc, action)
        where action is "created" or "updated" -- the applier records that
        distinction because rollback may delete what it created but must
        never delete what it merely modified."""
        if self.exists(doctype, name):
            return self.update(doctype, name, doc), "updated"
        return self.insert(doctype, {**doc, "name": name}), "created"

    def call_method(self, dotted_path: str, params: dict | None = None, http_method: str = "GET") -> Any:
        if http_method.upper() == "GET":
            return self._request("GET", f"/api/method/{dotted_path}", params=params or {})
        return self._request("POST", f"/api/method/{dotted_path}", json=params or {})
