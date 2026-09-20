---
icon: lucide/network
---

# Graph search

[Full-text search](01-full-text-search.md) and [Vector search](02-vector-search.md) both answer "what matches this query" — lexically or semantically — over content already fetched into storage. Neither answers a structurally different question: "how are these things *related*." A concept discussed across several documents, a chunk that cites another, a human-asserted connection between two ideas — none of that is a similarity relationship a query string can retrieve; it is an edge someone (human or LLM, through the same MCP write path) asserted exists. This chapter is `GraphSearchBackend`: a single-layer knowledge graph over PriorisMCP's corpus, and the algorithm layer built on top of it.

Not to be confused with [Discovery](../02-discovery.md): the graph, like full-text and vector search, only ever represents content and connections already inside this project's storage — it has no reach into external sources.

## `GraphSearchBackend` is a pluggable interface

`GraphSearchBackend` follows the same swappable-backend pattern as `EmbeddingBackend`/`VectorSearchBackend`: "which engine stores/queries the graph" is injected behind an interface, not hard-wired, so a future swap to a remote/hosted graph database does not require touching anything that depends on the interface. Every capability the interface exposes is a typed method with typed parameters and a typed return value — there is no way to reach past the interface into engine-specific query syntax (see [No raw Cypher escape hatch, for now](#no-raw-cypher-escape-hatch-for-now) below).


## Concrete engine: LadybugDB, a native embedded Cypher engine

[LadybugDB](https://github.com/LadybugDB/ladybug) — see [ADR-00032](../ADR/00032-graph-engine-ladybugdb.md) for the full comparison against `graphqlite`, `agentflare-ai/sqlite-graph`, upstream KuzuDB, `Vela-Engineering/kuzu`, and a hosted graph database. It is an MIT-licensed, community-governed fork of KuzuDB implementing openCypher natively against its own columnar storage, rather than transpiling Cypher onto a relational schema the way the rejected SQLite-extension alternatives did. Embedded, single directory per database, no server process — a second embedded persistence engine alongside SQLite, with no cross-engine transactional atomicity between them (accepted; nothing in this design needs a graph write and a chunk/embedding write to commit together).

Its property-graph model requires an upfront schema (node/relationship tables with typed columns) — unlike Neo4j's schema-optional model. Its `MAP` type — a dictionary with one key type and one value type, not required to share the same keys across rows — is what makes an open per-node/per-edge metadata bag possible without an Entity-Attribute-Value (EAV) workaround; see [Metadata merge-on-write](#metadata-merge-on-write-conflict-raises-not-silent-overwrite-or-merge) below for how this project actually uses it.

## Storage layout: one corpus-wide graph, not per-document or per-note

`GraphSearchBackend` persists to its own directory, `PRIORIS_MCP_GRAPH_DIR` (env var, same pattern as `PRIORIS_MCP_STORAGE_DIR`/`PRIORIS_MCP_VECTOR_DIR`, defaulting to a sibling of both under the XDG data home), at `<grouping-dir>/graph.ladybug`.

Unlike `VectorSearchBackend`'s two corpus-wide instances (documents, notes — [ADR-00020](../ADR/00020-vector-corpus-topology-two-instances.md)), there is exactly **one** graph instance, not split by content type. A `Pointer` node's `ref_type` (`chunk`/`document`/`note` — see below) already spans every content kind within the single graph, so there is no document-vs-notes topology question to resolve the way there was for vectors: one corpus-wide graph covering every `Pointer`/`Concept` node and every edge between them, regardless of what a given `Pointer` happens to reference.

## Node shape: two typed tables, `Pointer` and `Concept`

See [ADR-00033](../ADR/00033-graph-node-shape-pointer-concept-tables.md) for the full reasoning. Two node tables:

- **`Pointer`** — links something that already has a home elsewhere in PriorisMCP: `ref_type` (`chunk`/`document`/`note`), `ref_id` (the referenced entity's existing id). Unique on `(ref_type, ref_id)` — a write targeting an existing pair upserts onto the same node.
- **`Concept`** — owns its own content: `label` (required), `aliases` (list of alternate names), `description` (optional).
- Both: `id` (server-minted UUID, not LadybugDB's internal node id), `created_at`, `updated_at`, and an open `metadata` (`MAP`) bag.

No `kind` discriminator field exists in storage — table membership answers "pointer or concept" structurally. A caller reading a node still needs to tell the two apart, though; see [Reads and traversal](#reads-and-traversal-get_node-get_edge-neighbors-subgraph) below for how that surfaces at the API boundary without reintroducing a stored discriminator.

```python
NodeId = str  # server-minted UUID
RefType = Literal["chunk", "document", "note"]

async def upsert_pointer(
    self, ref_type: RefType, ref_id: str, *, metadata: dict[str, Any] | None = None
) -> NodeId:
    """Create or return the Pointer node for (ref_type, ref_id).

    metadata (if given) merges onto any existing Pointer's metadata - see Metadata
    merge-on-write below.
    """

async def create_concept(
    self, label: str, *, aliases: list[str] | None = None, description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> NodeId:
    """Always creates a new Concept node - no upsert/dedup by label. Use find_concepts first
    if avoiding a duplicate matters (dedup is the caller's judgment, not this method's)."""

async def update_concept(
    self, node_id: NodeId, *, label: str | None = None, aliases: list[str] | None = None,
    description: str | None = None, metadata: dict[str, Any] | None = None,
) -> None:
    """Partial update: an unset (None) parameter leaves that field unchanged. There is no
    mechanism to clear an already-set field back to empty - only to overwrite it - an
    accepted limitation, not an oversight.

    metadata (if given) merges under the policy in Metadata merge-on-write below.
    Raises NotFoundError if node_id does not exist or is not a Concept.
    """

async def delete_node(self, node_id: NodeId) -> None:
    """Delete a node and cascade-delete every edge incident to it. Raises NotFoundError if
    node_id does not exist.

    Cascading is the only sound option: an edge left pointing at a deleted node is a dangling
    reference nothing else in this design tolerates (see Edge shape below, which validates
    both endpoints exist at edge-creation time) - erroring on delete-with-edges instead would
    just force every caller to manually delete incident edges first, for no benefit.
    """
```

## Edge shape: one generic relationship table

See [ADR-00034](../ADR/00034-graph-edge-shape-generic-relationship-table.md) for the full reasoning. One relationship table, declared across all four `Pointer`/`Concept` `FROM`/`TO` combinations, at LadybugDB's default `MANY_MANY` multiplicity. Columns: `id` (server-minted UUID), `relation_type` (free text), `weight` (optional numeric), `metadata` (open `MAP`), `created_at`, `updated_at`. No node-pair uniqueness — LadybugDB relationship identity is an internally-generated edge id, not `(source, target, relation_type)`, so this is a true multigraph: independently-authored relationships (human vs. LLM-suggested, or two different `relation_type` labels for what two actors consider the same connection) coexist rather than colliding.

Every node/edge is written only through this same interface — nothing is auto-derived by an in-server extraction pass. That does not rule out edges that are *themselves* partially auto-derived (e.g. an OpenAlex citation-derived link, or an LLM-suggested concept relation): those are ordinary edges whose `metadata` records provenance, written through the same path as everything else, with human review being a plain `update_edge`/`delete_edge` call on that same row — no separate override/patch layer reconciling strata.

```python
EdgeId = str  # server-minted UUID

async def create_edge(
    self, from_id: NodeId, to_id: NodeId, relation_type: str, *,
    weight: float | None = None, metadata: dict[str, Any] | None = None,
) -> EdgeId:
    """Create a new edge. Validates both from_id and to_id exist first and raises
    NotFoundError if either does not - a dangling edge referencing a nonexistent node is never
    created, matching delete_node's cascade above."""

async def update_edge(
    self, edge_id: EdgeId, *, relation_type: str | None = None, weight: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Partial update, same semantics as update_concept. relation_type is mutable: it is a
    free-text property column on the one generic relationship table, not a schema-level
    Cypher relationship type the way it would be if LadybugDB required a separate declared
    table per relation - there is no structural reason correcting a mislabeled relation_type
    (e.g. "discussed_in" -> "introduced_in" once a writer reads more carefully) should mean
    delete-and-recreate, losing the edge's own id/created_at for what is, in substance, the
    same kind of correction as editing a Concept's label.

    metadata (if given) merges under the same policy as node writes. Raises NotFoundError if
    edge_id does not exist.
    """

async def delete_edge(self, edge_id: EdgeId) -> None:
    """Delete an edge. Raises NotFoundError if it does not exist. No cascade concern - nothing
    else in this design references an edge by id except the edge's own row."""
```

## Metadata merge-on-write: conflict raises, not silent overwrite or merge

See [ADR-00037](../ADR/00037-graph-metadata-merge-conflict-policy.md). Every method above that writes `metadata` onto a node or edge that may already have some (`upsert_pointer`, `update_concept`, `update_edge`) merges **per-key**: a key present in both existing and new metadata with the same value is a no-op; a differing value raises `MetadataConflictError` (new, in `errors.py`, mapping to a new `metadata_conflict` error code) naming the conflicting key(s) rather than silently picking a winner. Keys unique to either side pass through untouched. A caller hitting the error must re-issue the write with an explicitly resolved value.

Value type mapping is handled in `prioris_mcp` code, not delegated to the engine: LadybugDB's `MAP` needs one fixed value type per column ([ADR-00032](../ADR/00032-graph-engine-ladybugdb.md)), so every metadata value is stored as `json.dumps(value)` and read back via `json.loads(...)` — not a naive `str(value)`, which would be lossy and ambiguous (`str(True)` → `"True"` is indistinguishable from an actual string `"True"`; `json.dumps(True)` → `"true"` round-trips cleanly).


## No raw Cypher escape hatch, for now

See [ADR-00036](../ADR/00036-graph-no-raw-cypher-escape-hatch.md). Nothing in `GraphSearchBackend` accepts a raw query string in LadybugDB's own query language — every capability above and below is its own typed method. This is explicitly reversible: a raw-query escape hatch can be added later, additively, without unwinding anything already built, if a genuine need for arbitrary querying emerges that the fixed method set cannot express.

## Reads and traversal: `get_node`, `get_edge`, `neighbors`, `subgraph`

```python
async def get_node(self, node_id: NodeId) -> dict:
    """Raises NotFoundError if node_id does not exist. Returns a dict carrying a computed
    "kind": "pointer" | "concept" key - synthesized at read time from which table matched,
    never stored (Node shape above keeps storage itself free of any discriminator field) -
    plus that kind's own fields (ref_type/ref_id for a pointer, label/aliases/description for
    a concept), id, metadata, created_at, updated_at.
    """

async def get_edge(self, edge_id: EdgeId) -> dict:
    """Raises NotFoundError if edge_id does not exist."""

async def neighbors(
    self, node_id: NodeId, *, direction: Literal["out", "in", "both"] = "both",
    relation_type: str | None = None, offset: int = 0, limit: int = 50,
) -> list[dict]:
    """Each result pairs the connecting edge with the neighboring node - {"edge": {...},
    "node": {...}} - rather than returning bare neighbor ids and forcing a second get_edge
    round trip per result.
    """

async def subgraph(self, node_ids: list[NodeId], *, depth: int = 1, relation_type: str | None = None) -> dict:
    """Every node/edge within `depth` hops of any given seed id, deduplicated, optionally
    restricted to one relation_type. Returns {"nodes": [...], "edges": [...]} - the raw
    material GraphAlgorithms materializes into a NetworkX MultiDiGraph (see Graph algorithm
    backend below). Depth-bounded by construction - there is no unbounded whole-graph fetch.
    """
```

## Concept dedup: `find_concepts`, fuzzy string matching, not exact or embedding-based

See [ADR-00038](../ADR/00038-graph-concept-dedup-fuzzy-matching.md). The calling LLM has no visibility into what `Concept` nodes already exist before deciding to create one, so `find_concepts` exists to surface plausible existing candidates for the LLM itself to judge — it is a recall-oriented candidate surfacer, not a duplicate-blocking gate; the dedup decision stays with the caller.

```python
async def find_concepts(self, query: str, *, limit: int = 20) -> list[dict]:
    """rapidfuzz-scored candidates ranked by similarity, most-similar first, against every
    existing Concept's label+aliases. Catches typos/near-string variants (e.g. "gradient
    checkpointing" vs "gradient check-pointing") - not genuine synonyms in different words or
    cross-lingual duplicates, which no string-similarity approach reaches. See Browsing the
    concept vocabulary below for the fallback that covers that gap.
    """
```

## Browsing the concept vocabulary: `list_concepts`

A resource, not a tool — a pure read with no side effects, mirroring `read_markdown_resource`'s paging convention (`research://{provider}/{identifier}/{format}/markdown{?offset,limit,page}`). Exposed at `research://graph/concepts{?text,match,offset,limit}`. Where `find_concepts` narrows by fuzzy string similarity, `list_concepts` lets the calling LLM browse the *entire* concept vocabulary unfiltered — the fallback for suspected synonym or cross-lingual duplicates that fuzzy matching structurally cannot catch.

```python
MatchMode = Literal["starts_with", "contains", "ends_with"]

async def list_concepts(
    self, *, text: str | None = None, match: MatchMode = "contains",
    offset: int = 0, limit: int = 50,
) -> list[dict]:
    """Unfiltered by default - browses every Concept node, paginated, newest first. `match`
    only takes effect when `text` is given, matching case-insensitively against `label` only -
    alias-matching is a deferred follow-up; `find_concepts`'s fuzzy matching already covers
    aliases for the caller-driven-dedup use case; `text=None` lists everything regardless of
    `match`. `limit` is clamped to PRIORIS_MCP_GRAPH_CONCEPTS_MAX_LIMIT regardless of what the
    caller requests.
    """
```

`PRIORIS_MCP_GRAPH_CONCEPTS_MAX_LIMIT` (new env var, same `env.int`+`Range` pattern as `PRIORIS_MCP_DISCOVERY_MAX_RESULTS`) defaults to 500, `Range(min=1, max=1000)` — a safety ceiling against a pathological unfiltered call, not the expected per-page size (default per-call `limit` stays 50, matching other search defaults).

## Diagnostic and visualization export: `export_graph`

A resource, not a tool — the same pure-read rationale as `list_concepts` above. Exposed at `research://graph/export{?format}`. Where every other read in this chapter returns a bounded, purpose-scoped slice of the graph, `export_graph` returns the entire corpus-wide graph, unfiltered — for opening in an external visualization tool (Gephi, Cytoscape) or a one-off diagnostic dump. It is not scoped to a seed set the way `subgraph()` is: a caller wanting a bounded neighborhood already has `subgraph()` (`research_graph_query`) for that, so `export_graph` deliberately does not duplicate that scoping — it exists for a different intent, an explicit, deliberately-invoked whole-graph dump, not something an agentic loop could stumble into unexpectedly.

```python
GraphExportFormat = Literal["cypher_json", "graphml"]

async def export_graph(self) -> dict:
    """Every node and every edge in the graph, unfiltered - the same {"nodes": [...],
    "edges": [...]} shape as subgraph(), but with no seeds/depth bound. Unlike the "no
    unbounded whole-graph algorithm run" rule in Algorithm surface below (which guards
    against unpredictable algorithmic cost), this is a deliberate, explicitly-invoked
    diagnostic/export path, not something an agentic loop could stumble into by accident.
    """
```

The resource formats that raw dict on request via a `format: GraphExportFormat = "cypher_json"` query parameter. `cypher_json` returns the dict as-is, the same shape every other JSON-returning read in this chapter already uses. `graphml` serializes it as GraphML XML via NetworkX's `write_graphml`, reusing `GraphAlgorithms`' existing raw-dict-to-`MultiDiGraph` materialization step — directly openable in Gephi/Cytoscape without a client-side conversion step.

## Graph algorithm backend: NetworkX

See [ADR-00035](../ADR/00035-graph-algorithms-networkx.md). Algorithms run over an in-memory subgraph materialized via `subgraph()` above, built into a NetworkX `MultiDiGraph` — directed and multi-edge, matching the storage model's own directedness and multigraph nature (Edge shape, above) rather than collapsing either away. Node/edge `metadata` carries over as NetworkX attribute dicts, so algorithms can filter or weight by provenance without any schema change.

This lives in a separate class, `GraphAlgorithms`, not as abstract methods on `GraphSearchBackend` itself — one shared implementation works over `subgraph()`'s output regardless of which concrete `GraphSearchBackend` is plugged in, so there is nothing engine-specific for a given backend implementation to reimplement:

```python
class GraphAlgorithms:
    """NetworkX-backed algorithms over a materialized subgraph, independent of the storage
    engine. Not an ABC - one implementation works for any GraphSearchBackend."""

    def __init__(self, backend: GraphSearchBackend) -> None: ...
```

NetworkX was chosen over rustworkx (faster, but this project's local-first single-user scale does not need the performance, and NetworkX's broader algorithm coverage/community outweighs raw speed here - rustworkx's close API parity keeps a later swap cheap if that changes) and over engine-native algorithms (would tie algorithm availability to whichever engine currently backs `GraphSearchBackend`, undermining the same portability goal the engine choice itself is built around).

## Algorithm surface: seven fixed methods, not `shortest_path` alone and not the full NetworkX catalog

See [ADR-00039](../ADR/00039-graph-algorithm-surface-fixed-set.md). NetworkX's own algorithm reference spans 30+ families, most with no plausible use case over a `Pointer`/`Concept` prior-art graph — so `GraphAlgorithms` exposes a fixed, named set of seven, not a generic passthrough:

```python
async def betweenness_centrality(
    self, node_ids: list[NodeId], *, depth: int = 1, relation_type: str | None = None
) -> dict[NodeId, float]: ...

async def pagerank(
    self, node_ids: list[NodeId], *, depth: int = 1, relation_type: str | None = None
) -> dict[NodeId, float]: ...

async def communities(
    self, node_ids: list[NodeId], *, depth: int = 1, relation_type: str | None = None, seed: int | None = None
) -> list[list[NodeId]]:
    """Louvain (networkx.algorithms.community.louvain_communities) - the modern standard,
    ships in NetworkX core, fast; chosen over greedy_modularity_communities (older, slower)
    and girvan_newman (expensive, does not scale). Stochastic (seedable), an accepted tradeoff
    for exploratory clustering rather than a reproducibility requirement.
    """

async def paths(
    self, from_id: NodeId, to_id: NodeId, *, max_depth: int, max_paths: int = 20,
    relation_type: str | None = None,
) -> list[list[dict]]:
    """all_simple_paths(cutoff=max_depth) - every simple path up to the cutoff, sorted
    shortest-first, then truncated to max_paths. Deliberately not a single shortest_path:
    collapsing to one "best" route discards other paths that may carry independently valuable
    semantic information for prior-art discovery. Each returned path is a hop list, the same
    {"edge", "node"} shape as neighbors() above.
    """

async def reachable(
    self, node_id: NodeId, *, direction: Literal["out", "in"] = "out", max_depth: int,
    relation_type: str | None = None,
) -> list[NodeId]:
    """descendants/ancestors, depth-bounded - open-ended "what does exploring outward from
    this node eventually touch," with no specific target required at all.
    """

async def steiner_tree(
    self, node_ids: list[NodeId], *, max_depth: int, relation_type: str | None = None
) -> dict:
    """The minimal tree connecting an entire set of nodes at once (not just a pair) -
    networkx.algorithms.approximation.steiner_tree, a polynomial-time 2-approximation (exact
    Steiner tree is NP-hard). Uses edge weight if present, else unweighted. Returns {"nodes":
    [...], "edges": [...]}, the same shape as subgraph().
    """

async def predict_links(
    self, node_ids: list[NodeId], *, max_depth: int, top_k: int = 20, relation_type: str | None = None
) -> list[dict]:
    """Adamic-Adar (networkx.adamic_adar_index) over non-adjacent pairs within the
    materialized neighborhood only, ranked [{"from_id", "to_id", "score"}] - suggests
    candidate edges for a human/LLM to review and formalize via create_edge, the same
    partial-auto-derivation-then-human-review pattern Edge shape above already establishes.
    Chosen over plain Jaccard/preferential attachment: Adamic-Adar down-weights shared
    neighbors that are themselves high-degree "hub" concepts (e.g. "machine learning" would be
    a shared neighbor of almost everything and should not count as strong evidence), and is the
    standard baseline in citation-network link prediction, a close domain match.
    """
```

Every method here is depth-bounded off an explicit seed set (or a pair, for `paths`) and most accept a `relation_type` filter via `subgraph()` — there is no unbounded whole-graph algorithm run exposed anywhere, keeping cost predictable regardless of corpus size.

## Tool grouping: three op-dispatched tools, not one tool per operation or one mega-tool

See [ADR-00040](../ADR/00040-graph-tool-grouping-op-dispatch.md). The 19 operations above (7 node/edge writes, 5 reads, 7 algorithms) are exposed as three tools, each dispatched by an `op: Literal[...]` parameter validated at runtime against that op's actual requirements (the same `InvalidRequestError` idiom `research_search_fetched` already uses for cross-field validation):

- **`research_graph_write`** — `op: Literal["upsert_pointer", "create_concept", "update_concept", "delete_node", "create_edge", "update_edge", "delete_edge"]`. `destructiveHint: True` (it carries the delete ops).
- **`research_graph_query`** — `op: Literal["get_node", "get_edge", "neighbors", "find_concepts", "subgraph"]`. `readOnlyHint: True`.
- **`research_graph_analyze`** — `op: Literal["betweenness_centrality", "pagerank", "communities", "paths", "reachable", "steiner_tree", "predict_links"]`. `readOnlyHint: True`.

Each tool's return type is a discriminated union keyed on `op` (Pydantic `Field(discriminator="op")`), not a loose untyped envelope — every op's payload stays strongly typed end to end, giving `ty type-check` real narrowing instead of an unchecked `result: Any`.

```python
class GraphWriteResult(BaseModel):
    op: Literal["upsert_pointer", "create_concept", "update_concept", "delete_node",
                "create_edge", "update_edge", "delete_edge"]
    id: str  # NodeId or EdgeId depending on op
```

```python
class PointerNode(BaseModel):
    kind: Literal["pointer"]
    id: NodeId
    ref_type: RefType
    ref_id: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str

class ConceptNode(BaseModel):
    kind: Literal["concept"]
    id: NodeId
    label: str
    aliases: list[str]
    description: str | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str

GraphNode = Annotated[PointerNode | ConceptNode, Field(discriminator="kind")]

class GraphEdge(BaseModel):
    id: EdgeId
    from_id: NodeId
    to_id: NodeId
    relation_type: str
    weight: float | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str

class GraphHop(BaseModel):
    edge: GraphEdge
    node: GraphNode

class ConceptMatch(BaseModel):
    node: ConceptNode
    score: float

class NodeResult(BaseModel):
    op: Literal["get_node"]
    node: GraphNode

class EdgeResult(BaseModel):
    op: Literal["get_edge"]
    edge: GraphEdge

class NeighborsResult(BaseModel):
    op: Literal["neighbors"]
    matches: list[GraphHop]

class ConceptMatchesResult(BaseModel):
    op: Literal["find_concepts"]
    matches: list[ConceptMatch]

class SubgraphResult(BaseModel):
    op: Literal["subgraph"]
    nodes: list[GraphNode]
    edges: list[GraphEdge]

GraphQueryResult = Annotated[
    NodeResult | EdgeResult | NeighborsResult | ConceptMatchesResult | SubgraphResult,
    Field(discriminator="op"),
]
```

```python
class CentralityResult(BaseModel):
    op: Literal["betweenness_centrality", "pagerank"]
    scores: dict[NodeId, float]

class CommunitiesResult(BaseModel):
    op: Literal["communities"]
    communities: list[list[NodeId]]

class PathsResult(BaseModel):
    op: Literal["paths"]
    paths: list[list[GraphHop]]

class ReachableResult(BaseModel):
    op: Literal["reachable"]
    node_ids: list[NodeId]

class SteinerTreeResult(BaseModel):
    op: Literal["steiner_tree"]
    nodes: list[GraphNode]
    edges: list[GraphEdge]

class PredictedLink(BaseModel):
    from_id: NodeId
    to_id: NodeId
    score: float

class PredictedLinksResult(BaseModel):
    op: Literal["predict_links"]
    predictions: list[PredictedLink]

GraphAnalyzeResult = Annotated[
    CentralityResult | CommunitiesResult | PathsResult | ReachableResult
    | SteinerTreeResult | PredictedLinksResult,
    Field(discriminator="op"),
]
```

## `research_graph_*` are new tools, not a `mode` on `research_search_fetched`

See [ADR-00041](../ADR/00041-graph-tools-not-search-fetched-mode.md). `research_search_fetched`'s `mode` (`fts`/`vector`/`hybrid`, [ADR-00024](../ADR/00024-search-composition-mode-no-server-fusion.md)) works because every mechanism it dispatches to shares one contract: a query string in, a uniform ranked-matches list out. Graph operations share none of that — `get_node` returns one node, not a ranked list; `neighbors` returns edge+node pairs; `subgraph` returns a whole node/edge set; only `find_concepts` is even query-shaped, and it exists for write-time dedup, not retrieval. This is the same reasoning [ADR-00016](../ADR/00016-research-discovery-new-tool-not-mode.md) already used to make `research_discovery` its own tool rather than a `research_search_fetched` mode: a structurally incompatible result shape gets its own tool, not a forced fit into an existing one.

A caller wanting both lexical/semantic search and graph traversal issues separate tool calls — composing them is left to whatever orchestrates tool calls on the client side, not built into the server.
