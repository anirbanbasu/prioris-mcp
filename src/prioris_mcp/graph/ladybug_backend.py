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
- SKIP/LIMIT must be inlined into the query text, not bound as $offset/$limit query parameters -
  with parameters, SKIP is silently ignored and every page returns the same first N rows.
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
        # ladybug defaults buffer_pool_size to ~80% of system memory and reserves an 8TB mmap
        # region per Database when unset. PriorisMCP now constructs one per test via server.py's
        # __init__, and enough concurrently-live instances exhaust memory/address space. Both
        # bounds are ladybug's documented knobs for this; 128MiB is ample for an embedded
        # metadata graph.
        self._db = lb.Database(str(self._path), buffer_pool_size=128 * 1024**2, max_db_size=1 * 1024**3)
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
        from_node = await self.get_node(from_id)  # raises NotFoundError if missing
        to_node = await self.get_node(to_id)  # raises NotFoundError if missing
        from_label = "Pointer" if from_node["kind"] == "pointer" else "Concept"
        to_label = "Pointer" if to_node["kind"] == "pointer" else "Concept"
        edge_id = str(uuid.uuid4())
        now = _now_iso()
        keys, values = _metadata_to_cypher_params(metadata or {})
        await self._conn.execute(
            f"MATCH (a:{from_label} {{id: $from_id}}), (b:{to_label} {{id: $to_id}}) "
            "CREATE (a)-[e:Related {id: $id, relation_type: $relation_type, weight: $weight, "
            "metadata: map(CAST($keys AS STRING[]), CAST($values AS STRING[])), "
            "created_at: $ts, updated_at: $ts}]->(b)",
            {
                "from_id": from_id,
                "to_id": to_id,
                "id": edge_id,
                "relation_type": relation_type,
                "weight": weight,
                "keys": keys,
                "values": values,
                "ts": now,
            },
        )
        return edge_id

    async def update_edge(
        self,
        edge_id: EdgeId,
        *,
        relation_type: str | None = None,
        weight: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        current = await self.get_edge(edge_id)  # raises NotFoundError if missing
        new_relation_type = relation_type if relation_type is not None else current["relation_type"]
        new_weight = weight if weight is not None else current["weight"]
        merged_metadata = _merge_metadata(current["metadata"], metadata) if metadata else current["metadata"]
        keys, values = _metadata_to_cypher_params(merged_metadata)
        await self._conn.execute(
            "MATCH ()-[e:Related {id: $id}]->() SET e.relation_type = $relation_type, e.weight = $weight, "
            "e.metadata = map(CAST($keys AS STRING[]), CAST($values AS STRING[])), e.updated_at = $ts",
            {
                "id": edge_id,
                "relation_type": new_relation_type,
                "weight": new_weight,
                "keys": keys,
                "values": values,
                "ts": _now_iso(),
            },
        )

    async def delete_edge(self, edge_id: EdgeId) -> None:
        await self.get_edge(edge_id)  # raises NotFoundError if missing
        await self._conn.execute("MATCH ()-[e:Related {id: $id}]->() DELETE e", {"id": edge_id})

    async def get_node(self, node_id: NodeId) -> dict:
        result = await self._conn.execute(
            "MATCH (n {id: $id}) RETURN n.id AS id, label(n) AS kind, properties(n) AS props", {"id": node_id}
        )
        assert isinstance(result, QueryResult)  # see the narrowing note on upsert_pointer above
        rows = list(result.rows_as_dict())
        if not rows:
            raise NotFoundError(f"Node not found: {node_id}")
        row = rows[0]
        assert isinstance(row, dict)  # rows_as_dict() rows are dict[str, Any], not the list[Any] row variant
        kind = row["kind"].lower()
        props = row["props"]
        base = {
            "id": row["id"],
            "kind": kind,
            "metadata": _metadata_from_row(props["metadata"]),
            "created_at": props["created_at"],
            "updated_at": props["updated_at"],
        }
        if kind == "pointer":
            base["ref_type"] = props["ref_type"]
            base["ref_id"] = props["ref_id"]
        else:
            base["label"] = props["label"]
            base["aliases"] = props["aliases"]
            base["description"] = props["description"]
        return base

    async def get_edge(self, edge_id: EdgeId) -> dict:
        result = await self._conn.execute(
            "MATCH (a)-[e:Related {id: $id}]->(b) "
            "RETURN e.id AS id, a.id AS from_id, b.id AS to_id, e.relation_type AS relation_type, "
            "e.weight AS weight, e.metadata AS metadata, e.created_at AS created_at, e.updated_at AS updated_at",
            {"id": edge_id},
        )
        assert isinstance(result, QueryResult)  # see the narrowing note on upsert_pointer above
        rows = list(result.rows_as_dict())
        if not rows:
            raise NotFoundError(f"Edge not found: {edge_id}")
        row = rows[0]
        assert isinstance(row, dict)  # rows_as_dict() rows are dict[str, Any], not the list[Any] row variant
        row["metadata"] = _metadata_from_row(row["metadata"])
        return row

    @staticmethod
    def _direction_pattern(direction: str) -> str:
        if direction == "out":
            return "-[e:Related]->"
        if direction == "in":
            return "<-[e:Related]-"
        return "-[e:Related]-"

    async def neighbors(
        self,
        node_id: NodeId,
        *,
        direction: Literal["out", "in", "both"] = "both",
        relation_type: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        pattern = self._direction_pattern(direction)
        where_clause = " WHERE e.relation_type = $relation_type" if relation_type is not None else ""
        params: dict[str, Any] = {"id": node_id}
        if relation_type is not None:
            params["relation_type"] = relation_type
        # SKIP/LIMIT must be inlined rather than passed as $offset/$limit query params - with
        # parameters, this ladybug version silently ignores SKIP and returns the same first page
        # every time (confirmed interactively); int() casting keeps this injection-safe despite
        # the string interpolation.
        result = await self._conn.execute(
            f"MATCH (a {{id: $id}}){pattern}(b){where_clause} "
            "RETURN e.id AS edge_id, a.id AS a_id, b.id AS node_id "
            f"ORDER BY b.id, e.id SKIP {int(offset)} LIMIT {int(limit)}",
            params,
        )
        assert isinstance(result, QueryResult)  # see the narrowing note on upsert_pointer above
        rows = list(result.rows_as_dict())
        hops = []
        for row in rows:
            edge = await self.get_edge(row["edge_id"])
            node = await self.get_node(row["node_id"])
            hops.append({"edge": edge, "node": node})
        return hops

    async def subgraph(self, node_ids: list[NodeId], *, depth: int = 1, relation_type: str | None = None) -> dict:
        reach_params: dict[str, Any] = {"seed_ids": node_ids}
        if relation_type is not None:
            reach_pattern = f"[:Related*0..{int(depth)} {{relation_type: $relation_type}}]"
            reach_params["relation_type"] = relation_type
        else:
            reach_pattern = f"[:Related*0..{int(depth)}]"
        reach_result = await self._conn.execute(
            f"MATCH (seed) WHERE seed.id IN $seed_ids "
            f"OPTIONAL MATCH (seed)-{reach_pattern}-(m) "
            "WITH seed, m WHERE m IS NOT NULL "
            "RETURN DISTINCT m.id AS id",
            reach_params,
        )
        assert isinstance(reach_result, QueryResult)  # see the narrowing note on upsert_pointer above
        reached_ids = {row["id"] for row in reach_result.rows_as_dict()}
        all_ids = reached_ids | set(node_ids)
        if not all_ids:
            return {"nodes": [], "edges": []}
        id_list = sorted(all_ids)
        nodes = [await self.get_node(node_id) for node_id in id_list]
        where_clause = "a.id IN $ids AND b.id IN $ids"
        params: dict[str, Any] = {"ids": id_list}
        if relation_type is not None:
            where_clause += " AND e.relation_type = $relation_type"
            params["relation_type"] = relation_type
        edge_result = await self._conn.execute(
            f"MATCH (a)-[e:Related]->(b) WHERE {where_clause} RETURN e.id AS edge_id", params
        )
        assert isinstance(edge_result, QueryResult)  # see the narrowing note on upsert_pointer above
        edges = [await self.get_edge(row["edge_id"]) for row in edge_result.rows_as_dict()]
        return {"nodes": nodes, "edges": edges}

    async def find_concepts(self, query: str, *, limit: int = 20) -> list[dict]:
        from rapidfuzz import fuzz, process

        result = await self._conn.execute("MATCH (c:Concept) RETURN c.id AS id, c.label AS label, c.aliases AS aliases")
        assert isinstance(result, QueryResult)  # see the narrowing note on upsert_pointer above
        rows = list(result.rows_as_dict())
        candidates: list[tuple[str, str]] = []
        for row in rows:
            candidates.append((row["id"], row["label"]))
            candidates.extend((row["id"], alias) for alias in row["aliases"])
        scored = process.extract(
            query, {i: text for i, (_, text) in enumerate(candidates)}, scorer=fuzz.WRatio, limit=len(candidates)
        )
        best_score_by_node_id: dict[str, float] = {}
        for _, score, idx in scored:
            node_id = candidates[idx][0]
            if node_id not in best_score_by_node_id or score > best_score_by_node_id[node_id]:
                best_score_by_node_id[node_id] = score
        ranked = sorted(best_score_by_node_id.items(), key=lambda item: item[1], reverse=True)[:limit]
        return [{"node": await self.get_node(node_id), "score": score} for node_id, score in ranked]

    async def list_concepts(
        self,
        *,
        text: str | None = None,
        match: MatchMode = "contains",
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict]:
        where_clause = ""
        params: dict[str, Any] = {}
        if text is not None:
            operator = {"starts_with": "STARTS WITH", "contains": "CONTAINS", "ends_with": "ENDS WITH"}[match]
            where_clause = f" WHERE lower(c.label) {operator} lower($text)"
            params["text"] = text
        # SKIP/LIMIT must be inlined rather than passed as $offset/$limit query params - see the
        # note on neighbors() above; int() casting keeps this injection-safe despite the string
        # interpolation. ORDER BY also appends c.id as a deterministic tiebreak, since created_at
        # values can collide within the same timestamp resolution during pagination.
        result = await self._conn.execute(
            f"MATCH (c:Concept){where_clause} "
            "RETURN c.id AS id, c.label AS label, c.aliases AS aliases, c.description AS description, "
            "c.metadata AS metadata, c.created_at AS created_at, c.updated_at AS updated_at "
            f"ORDER BY c.created_at DESC, c.id SKIP {int(offset)} LIMIT {int(limit)}",
            params,
        )
        assert isinstance(result, QueryResult)  # see the narrowing note on upsert_pointer above
        concepts: list[dict] = []
        for row in result.rows_as_dict():
            assert isinstance(row, dict)  # rows_as_dict() rows are dict[str, Any], not the list[Any] row variant
            row["metadata"] = _metadata_from_row(row["metadata"])
            row["kind"] = "concept"
            concepts.append(row)
        return concepts

    async def export_graph(self) -> dict:
        node_result = await self._conn.execute("MATCH (n) RETURN n.id AS id")
        assert isinstance(node_result, QueryResult)  # see the narrowing note on upsert_pointer above
        node_ids = [row["id"] for row in node_result.rows_as_dict()]
        nodes = [await self.get_node(node_id) for node_id in node_ids]
        edge_result = await self._conn.execute("MATCH ()-[e:Related]->() RETURN e.id AS id")
        assert isinstance(edge_result, QueryResult)  # see the narrowing note on upsert_pointer above
        edges = [await self.get_edge(row["id"]) for row in edge_result.rows_as_dict()]
        return {"nodes": nodes, "edges": edges}
