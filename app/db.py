"""
Client REST Supabase léger basé sur httpx.

Pourquoi pas supabase-py ? La lib officielle force un regex JWT sur la clé,
ce qui empêche d'utiliser les nouvelles clés au format sb_secret_... De plus,
on n'utilise qu'une infime partie de son API. Un client REST minimal suffit
largement et garde la même interface (sb.table().insert().execute() etc.)
pour ne pas avoir à modifier le reste du code.

Couverture :
  - sb.table(name).insert(data).execute()
  - sb.table(name).upsert(data, on_conflict="col1,col2").execute()
  - sb.table(name).update(data).eq("col", val).execute()
  - sb.table(name).select("cols").eq("col", val).limit(n).execute()
  - sb.storage.from_(bucket).upload(path, file, file_options={...})
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx

from app.config import settings


# -------------------- Query builder --------------------


class _QueryResult:
    """Mimique de l'objet retourné par supabase-py."""
    def __init__(self, data: Any):
        if data is None:
            self.data = []
        elif isinstance(data, list):
            self.data = data
        else:
            self.data = [data]


class _RestQuery:
    def __init__(self, client: "SupabaseClient", table: str):
        self._client = client
        self._table = table
        self._method: str = "GET"
        self._params: dict[str, str] = {}
        self._body: Any = None
        self._extra_headers: dict[str, str] = {}

    # ---- mutations ----
    def insert(self, data: Any, *, returning: str = "representation") -> "_RestQuery":
        self._method = "POST"
        self._body = data if isinstance(data, list) else [data]
        self._extra_headers["Prefer"] = f"return={returning}"
        return self

    def upsert(
        self,
        data: Any,
        *,
        on_conflict: str | None = None,
        returning: str = "representation",
    ) -> "_RestQuery":
        self._method = "POST"
        self._body = data if isinstance(data, list) else [data]
        prefer = [f"return={returning}", "resolution=merge-duplicates"]
        self._extra_headers["Prefer"] = ",".join(prefer)
        if on_conflict:
            self._params["on_conflict"] = on_conflict
        return self

    def update(self, data: dict, *, returning: str = "representation") -> "_RestQuery":
        self._method = "PATCH"
        self._body = data
        self._extra_headers["Prefer"] = f"return={returning}"
        return self

    def delete(self) -> "_RestQuery":
        self._method = "DELETE"
        return self

    # ---- read ----
    def select(self, columns: str = "*") -> "_RestQuery":
        self._method = "GET"
        self._params["select"] = columns
        return self

    # ---- filters ----
    def eq(self, column: str, value: Any) -> "_RestQuery":
        self._params[column] = f"eq.{value}"
        return self

    def neq(self, column: str, value: Any) -> "_RestQuery":
        self._params[column] = f"neq.{value}"
        return self

    def in_(self, column: str, values: list[Any]) -> "_RestQuery":
        """PostgREST IN filter : ?column=in.(val1,val2,val3)"""
        if not values:
            # Filtre impossible côté SQL — on garde un filtre qui matche rien
            self._params[column] = "eq.__never_matches__"
            return self
        serialized = ",".join(str(v) for v in values)
        self._params[column] = f"in.({serialized})"
        return self

    def limit(self, n: int) -> "_RestQuery":
        self._params["limit"] = str(n)
        return self

    def order(self, column: str, *, desc: bool = False) -> "_RestQuery":
        self._params["order"] = f"{column}.{'desc' if desc else 'asc'}"
        return self

    # ---- terminal ----
    def execute(self) -> _QueryResult:
        url = f"{self._client.base_url}/rest/v1/{self._table}"
        headers = {**self._client._auth_headers, **self._extra_headers}
        if self._body is not None and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        r = self._client._http.request(
            self._method,
            url,
            params=self._params,
            json=self._body,
            headers=headers,
        )
        if r.status_code >= 400:
            # On lève une exception parlante avec le corps de la réponse
            raise RuntimeError(
                f"Supabase REST {self._method} {self._table} failed: "
                f"{r.status_code} {r.text[:500]}"
            )

        try:
            data = r.json() if r.content else None
        except Exception:
            data = None
        return _QueryResult(data)


# -------------------- Storage --------------------


class _StorageBucket:
    def __init__(self, client: "SupabaseClient", bucket: str):
        self._client = client
        self._bucket = bucket

    def upload(
        self,
        path: str,
        file: bytes,
        file_options: dict | None = None,
    ) -> dict:
        url = f"{self._client.base_url}/storage/v1/object/{self._bucket}/{path}"
        opts = file_options or {}
        content_type = opts.get("content-type") or opts.get("contentType") \
            or "application/octet-stream"
        headers = {
            **self._client._auth_headers,
            "Content-Type": content_type,
        }
        if str(opts.get("upsert", "")).lower() == "true":
            headers["x-upsert"] = "true"

        r = self._client._http.post(url, content=file, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(
                f"Supabase Storage upload failed: "
                f"{r.status_code} {r.text[:500]}"
            )
        return r.json() if r.content else {}


class _Storage:
    def __init__(self, client: "SupabaseClient"):
        self._client = client

    def from_(self, bucket: str) -> _StorageBucket:
        return _StorageBucket(self._client, bucket)


# -------------------- Client racine --------------------


class SupabaseClient:
    def __init__(self, url: str, key: str):
        self.base_url = url.rstrip("/")
        self.key = key
        self._auth_headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
        }
        self._http = httpx.Client(timeout=30.0)
        self.storage = _Storage(self)

    def table(self, name: str) -> _RestQuery:
        return _RestQuery(self, name)

    def close(self) -> None:
        self._http.close()


@lru_cache(maxsize=1)
def get_supabase() -> SupabaseClient:
    return SupabaseClient(settings.supabase_url, settings.supabase_secret_key)
