# ADR 0009 — Model providers

- **Status:** Accepted (2026-11-18)
- **Decision:** All model access goes through interfaces in `app/providers/` (`ChatModel`, `VisionModel`, `EmbeddingProvider`, `Reranker`). The **default LLM/VLM provider is Gemini** (one key covers chat, vision and the Gemini-embedding evaluation). OpenAI and Anthropic adapters are planned behind the same interface. The **default embedding model is local** (`BAAI/bge-small-en-v1.5`), so ingestion and retrieval run and are tested without any API key. Model IDs come from config, never hardcoded.
- **Consequences:** Live LLM/VLM paths are untested until `GEMINI_API_KEY` is provided. Those tests are marked `live` and reported as not run.
