"""LadybugDB-backed GraphSearchBackend implementation.

See docs/requirement-specification/search/03-graph-search.md and ADR-00032/00033/00034/00036/
00037 for the design this implements. DDL/Cypher syntax verified interactively against
ladybug 0.20.4 (the version resolved by uv.lock), not merely assumed from Kuzu-era
documentation - see the "Verification note" at the top of
docs/superpowers/plans/2026-09-20-graph-search-backend.md for what was confirmed and why:

- AsyncConnection.execute(...) already thread-pools internally (wraps a sync Connection.execute
  via loop.run_in_executor) - no anyio.to_thread wrapper needed here.
- A MAP(STRING, STRING) column cannot be written from a raw Python dict query parameter
  (binder error); Cypher's map(CAST(... AS STRING[]), CAST(... AS STRING[])) builtin, fed by two
  parallel key/value parameter lists, is the working pattern - the CAST is required even for an
  empty list (uncast empty lists fail to resolve a type at all).
- CREATE ... -[e:Related {...}]-> ... requires each endpoint to be matched with an explicit node
  table label (Pointer or Concept) - Related spans four FROM/TO combinations, so an unlabeled
  match is ambiguous at CREATE time (though not at read time).
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import ladybug as lb
from ladybug.query_result import QueryResult

from prioris_mcp.errors import MetadataConflictError, NotFoundError
from prioris_mcp.graph.backend import EdgeId, GraphSearchBackend, MatchMode, NodeId, RefType

_SCHEMA_DDL: tuple[str, ...] = (
    """CREATE NODE TABLE IF NOT EXISTS Pointer(
        id STRING PRIMARY KEY,
        ref_type STRING,
        ref_id STRING,
        metadata MAP(STRING, STRING),
        created_at STRING,
        updated_at STRING
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Concept(
        id STRING PRIMARY KEY,
        label STRING,
        aliases STRING[],
        description STRING,
        metadata MAP(STRING, STRING),
        created_at STRING,
        updated_at STRING
    )""",
    """CREATE REL TABLE IF NOT EXISTS Related(
        FROM Pointer TO Pointer,
        FROM Pointer TO Concept,
        FROM Concept TO Pointer,
        FROM Concept TO Concept,
        id STRING,
        relation_type STRING,
        weight DOUBLE,
        metadata MAP(STRING, STRING),
        created_at STRING,
        updated_at STRING,
        MANY_MANY
    )""",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _metadata_to_cypher_params(metadata: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Serialize a metadata dict into parallel key/JSON-value lists for Cypher's map() builtin."""
    keys = list(metadata.keys())
    values = [json.dumps(metadata[key]) for key in keys]
    return keys, values


def _metadata_from_row(raw: dict[str, str]) -> dict[str, Any]:
    """Deserialize a MAP(STRING, STRING) column's raw {key: json_string} into real values."""
    return {key: json.loads(value) for key, value in raw.items()}


def _merge_metadata(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Per-key merge: same value for an existing key is a no-op, a differing value raises.

    Raises:
        MetadataConflictError: naming every key present in both dicts with a differing value.
    """
    conflicts = sorted(key for key in new if key in existing and existing[key] != new[key])
    if conflicts:
        raise MetadataConflictError(f"conflicting metadata key(s): {', '.join(conflicts)}")
    return {**existing, **new}


class LadybugSearchBackend(GraphSearchBackend):
    """One corpus-wide graph at `path`, backed by a LadybugDB embedded Cypher database."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = lb.Database(str(self._path))
        schema_conn = lb.Connection(self._db)
        try:
            for ddl in _SCHEMA_DDL:
                schema_conn.execute(ddl)
        finally:
            schema_conn.close()
        self._conn = lb.AsyncConnection(self._db)

    async def upsert_pointer(self, ref_type: RefType, ref_id: str, *, metadata: dict[str, Any] | None = None) -> NodeId:
        existing = await self._conn.execute(
            "MATCH (p:Pointer {ref_type: $ref_type, ref_id: $ref_id}) RETURN p.id AS id, p.metadata AS metadata",
            {"ref_type": ref_type, "ref_id": ref_id},
        )
        # execute() is typed to return QueryResult | list[QueryResult] because a semicolon-separated
        # multi-statement query yields a list; every query in this file is a single statement, so this
        # is always a lone QueryResult - narrow it explicitly rather than suppressing the type error.
        assert isinstance(existing, QueryResult)
        rows = list(existing.rows_as_dict())
        now = _now_iso()
        if rows:
            node_id = rows[0]["id"]
            if metadata:
                merged = _merge_metadata(_metadata_from_row(rows[0]["metadata"]), metadata)
                keys, values = _metadata_to_cypher_params(merged)
                await self._conn.execute(
                    "MATCH (p:Pointer {id: $id}) "
                    "SET p.metadata = map(CAST($keys AS STRING[]), CAST($values AS STRING[])), p.updated_at = $ts",
                    {"id": node_id, "keys": keys, "values": values, "ts": now},
                )
            return node_id
        node_id = str(uuid.uuid4())
        keys, values = _metadata_to_cypher_params(metadata or {})
        await self._conn.execute(
            "CREATE (p:Pointer {id: $id, ref_type: $ref_type, ref_id: $ref_id, "
            "metadata: map(CAST($keys AS STRING[]), CAST($values AS STRING[])), "
            "created_at: $ts, updated_at: $ts})",
            {"id": node_id, "ref_type": ref_type, "ref_id": ref_id, "keys": keys, "values": values, "ts": now},
        )
        return node_id

    # The methods below are declared abstract on GraphSearchBackend but not yet implemented on
    # this backend - Python's ABC machinery blocks instantiating a subclass that leaves *any*
    # abstract method unoverridden (not just when all are left abstract), so each needs a stub
    # override here to keep LadybugSearchBackend instantiable for this task's own tests. Each
    # `raise NotImplementedError` is excluded from the coverage gate by the pre-existing
    # `exclude_lines` entry in pyproject.toml's `[tool.coverage.report]`. Filled in incrementally
    # by later tasks in this same plan: create_concept/update_concept/delete_node (Task 4),
    # get_node/get_edge (Task 5), create_edge/update_edge/delete_edge (Task 6),
    # neighbors/subgraph (Task 7), find_concepts/list_concepts/export_graph (Task 8).

    async def create_concept(
        self,
        label: str,
        *,
        aliases: list[str] | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> NodeId:
        node_id = str(uuid.uuid4())
        now = _now_iso()
        keys, values = _metadata_to_cypher_params(metadata or {})
        await self._conn.execute(
            "CREATE (c:Concept {id: $id, label: $label, aliases: $aliases, description: $description, "
            "metadata: map(CAST($keys AS STRING[]), CAST($values AS STRING[])), created_at: $ts, updated_at: $ts})",
            {
                "id": node_id,
                "label": label,
                "aliases": aliases or [],
                "description": description,
                "keys": keys,
                "values": values,
                "ts": now,
            },
        )
        return node_id

    async def _get_concept_row(self, node_id: NodeId) -> dict:
        result = await self._conn.execute(
            "MATCH (c:Concept {id: $id}) "
            "RETURN c.label AS label, c.aliases AS aliases, c.description AS description, c.metadata AS metadata",
            {"id": node_id},
        )
        assert isinstance(result, QueryResult)  # see the narrowing note on upsert_pointer above
        rows = list(result.rows_as_dict())
        if not rows:
            raise NotFoundError(f"Concept not found: {node_id}")
        row = rows[0]
        assert isinstance(row, dict)  # rows_as_dict() rows are dict[str, Any], not the list[Any] row variant
        return row

    async def update_concept(
        self,
        node_id: NodeId,
        *,
        label: str | None = None,
        aliases: list[str] | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        current = await self._get_concept_row(node_id)
        new_label = label if label is not None else current["label"]
        new_aliases = aliases if aliases is not None else current["aliases"]
        new_description = description if description is not None else current["description"]
        if metadata:
            merged_metadata = _merge_metadata(_metadata_from_row(current["metadata"]), metadata)
        else:
            merged_metadata = _metadata_from_row(current["metadata"])
        keys, values = _metadata_to_cypher_params(merged_metadata)
        await self._conn.execute(
            "MATCH (c:Concept {id: $id}) SET c.label = $label, c.aliases = $aliases, "
            "c.description = $description, c.metadata = map(CAST($keys AS STRING[]), CAST($values AS STRING[])), "
            "c.updated_at = $ts",
            {
                "id": node_id,
                "label": new_label,
                "aliases": new_aliases,
                "description": new_description,
                "keys": keys,
                "values": values,
                "ts": _now_iso(),
            },
        )

    async def delete_node(self, node_id: NodeId) -> None:
        existing = await self._conn.execute("MATCH (n {id: $id}) RETURN n.id AS id", {"id": node_id})
        assert isinstance(existing, QueryResult)  # see the narrowing note on upsert_pointer above
        if not list(existing.rows_as_dict()):
            raise NotFoundError(f"Node not found: {node_id}")
        await self._conn.execute("MATCH (n {id: $id}) DETACH DELETE n", {"id": node_id})

    async def create_edge(
        self,
        from_id: NodeId,
        to_id: NodeId,
        relation_type: str,
        *,
        weight: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EdgeId:
        raise NotImplementedError

    async def update_edge(
        self,
        edge_id: EdgeId,
        *,
        relation_type: str | None = None,
        weight: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError

    async def delete_edge(self, edge_id: EdgeId) -> None:
        raise NotImplementedError

    async def get_node(self, node_id: NodeId) -> dict:
        raise NotImplementedError

    async def get_edge(self, edge_id: EdgeId) -> dict:
        raise NotImplementedError

    async def neighbors(
        self,
        node_id: NodeId,
        *,
        direction: Literal["out", "in", "both"] = "both",
        relation_type: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        raise NotImplementedError

    async def subgraph(self, node_ids: list[NodeId], *, depth: int = 1, relation_type: str | None = None) -> dict:
        raise NotImplementedError

    async def find_concepts(self, query: str, *, limit: int = 20) -> list[dict]:
        raise NotImplementedError

    async def list_concepts(
        self,
        *,
        text: str | None = None,
        match: MatchMode = "contains",
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        raise NotImplementedError

    async def export_graph(self) -> dict:
        raise NotImplementedError
