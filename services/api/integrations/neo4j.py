"""Neo4j AuraDB client over the Query API v2 (HTTPS, no driver dependency).

POST https://<host>/db/<database>/query/v2   Authorization: Basic base64(user:password)
Body {"statement": "...", "parameters": {...}}  ->  {"data": {"fields": [...], "values": [[...]]}}

Credentials come from NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD (+ optional NEO4J_DATABASE).
Statements are always parameterized; values are never interpolated into Cypher. If the graph is
not configured or fails, a typed ApiError is raised so callers can report it honestly.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import urllib.error
import urllib.request

from models.common import ApiError
from services import secrets

log = logging.getLogger("gtmos.neo4j")


def _cfg() -> tuple[str, str, str, str] | None:
    uri = secrets.provider_key("NEO4J_URI")
    user = secrets.provider_key("NEO4J_USERNAME") or "neo4j"
    password = secrets.provider_key("NEO4J_PASSWORD")
    if not uri or not password:
        return None
    return uri, user, password, secrets.provider_key("NEO4J_DATABASE") or ""


def configured() -> bool:
    return _cfg() is not None


def endpoint(uri: str, database: str) -> str:
    """neo4j+s://abc.databases.neo4j.io -> https://abc.databases.neo4j.io/db/neo4j/query/v2"""
    uri = uri.strip().rstrip("/")
    m = re.match(r"^(neo4j|bolt)(\+s|\+ssc)?://(?P<host>[^/:]+)(:\d+)?$", uri)
    if m:
        base = f"https://{m.group('host')}"
    elif uri.startswith(("https://", "http://")):
        base = uri
    else:
        raise ApiError(503, "graph_not_configured", "NEO4J_URI is not a valid Neo4j URI")
    return f"{base}/db/{database}/query/v2"


_resolved_db: dict[str, str] = {}   # per-process cache: configured URI -> database that worked


def _aura_instance_id(uri: str) -> str | None:
    m = re.match(r"^[a-z0-9+.\-]+://(?P<id>[a-z0-9]+)\.databases\.neo4j\.io", uri.strip(), re.I)
    return m.group("id").lower() if m else None


def _candidates(uri: str, explicit: str) -> list[str]:
    """Databases to try. An explicit NEO4J_DATABASE is authoritative; otherwise Aura
    instances may expose either `neo4j` or the instance id as the database name."""
    if explicit:
        return [explicit]
    cached = _resolved_db.get(uri)
    if cached:
        return [cached]
    out = ["neo4j"]
    inst = _aura_instance_id(uri)
    if inst:
        out.append(inst)
    return out


def _http_post(url: str, headers: dict, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - URL built from configured https host
        return json.loads(resp.read())


def run(statement: str, parameters: dict | None = None, *, timeout: float = 10) -> list[dict]:
    """Execute one Cypher statement and return rows as dicts keyed by column name."""
    cfg = _cfg()
    if not cfg:
        raise ApiError(503, "graph_not_configured", "Neo4j is not configured (NEO4J_URI / NEO4J_PASSWORD missing)")
    uri, user, password, database = cfg
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    headers = {"authorization": f"Basic {token}", "content-type": "application/json",
               "accept": "application/json"}
    body = {"statement": " ".join(statement.split()), "parameters": parameters or {}}
    candidates = _candidates(uri, database)
    data = None
    for i, db in enumerate(candidates):
        try:
            data = _http_post(endpoint(uri, db), headers, body, timeout)
            _resolved_db[uri] = db
            break
        except ApiError:
            raise
        except urllib.error.HTTPError as exc:
            code = _error_code(exc)
            log.warning("neo4j call failed: HTTP %s %s", exc.code, code)
            if exc.code == 404 and code.endswith("DatabaseNotFound") and i + 1 < len(candidates):
                continue   # try the next candidate database name
            if exc.code in (401, 403):
                raise ApiError(503, "graph_not_configured", "Neo4j rejected the configured credentials") from None
            if exc.code == 400:   # our statement/parameters were rejected: a bug, not an outage
                raise ApiError(502, "graph_query_failed", f"Neo4j rejected the query ({code})") from None
            raise ApiError(502, "graph_unavailable", "The graph database is unavailable") from None
        except Exception as exc:
            log.warning("neo4j call failed: %s", type(exc).__name__)
            raise ApiError(502, "graph_unavailable", "The graph database is unavailable") from None

    if data.get("errors"):
        raise ApiError(502, "graph_query_failed", f"Neo4j rejected the query ({data['errors'][0].get('code', '?')})")
    payload = data.get("data") or {}
    fields = payload.get("fields", [])
    return [dict(zip(fields, row)) for row in payload.get("values", [])]


def _error_code(exc: urllib.error.HTTPError) -> str:
    try:
        return json.loads(exc.read())["errors"][0]["code"]
    except Exception:
        return "unknown"
