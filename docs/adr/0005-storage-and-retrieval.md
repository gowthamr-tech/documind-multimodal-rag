# ADR 0005 — Vector and lexical storage

- **Status:** Accepted (2026-11-18)
- **Decision:** **PostgreSQL 16 + pgvector** (HNSW, `halfvec` where dims allow) and generated `tsvector` columns with GIN. Fusion via **Reciprocal Rank Fusion**. Rerank with a local cross-encoder (`BAAI/bge-reranker-base`, configurable). An `embedding_spaces` registry plus per-space partial HNSW indexes let several embedding models coexist. A `VectorStore` interface keeps Pinecone pluggable.
- **Caveat:** `ts_rank_cd` is **not BM25**. It is labelled "lexical" and benchmarked against `bm25s`. The upgrade path is ParadeDB `pg_search`.
- **Consequences:** One transactional store with RLS. Filtered ANN needs `hnsw.iterative_scan` and measured recall.
