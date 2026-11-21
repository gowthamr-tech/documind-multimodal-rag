# ADR 0007 — Agent architecture

- **Status:** Accepted (2026-11-18)
- **Decision:** **LangGraph** `StateGraph` with a supervisor that classifies intent using structured output plus rules (no open-ended LLM loops). Conditional edges are pure functions of the state, so they are unit-testable. Agents run only when needed: Classification, Retrieval, Table, Chart, Reasoning, Verification, Response. Arithmetic runs in a deterministic `Decimal` calculator tool. Citations are resolved on the server. `PostgresSaver` checkpointing, `RetryPolicy`, `recursion_limit=25`, ≤2 verification passes, token/cost/time budgets. `interrupt()` handles sensitive operations.
- **Consequences:** More explicit code than an autonomous multi-agent framework, in exchange for predictability, testability and bounded cost.
