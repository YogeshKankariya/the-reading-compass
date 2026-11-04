# AI Integration

**Project:** The Reading Compass  
**Document version:** 1.1  
**Status:** Finalized for MVP AI design  
**Last updated:** 1 November 2026

---

## 1. Purpose

This document defines how AI capabilities fit into The Reading Compass: which tasks use a large language model (LLM), which use embeddings, how outputs are validated, how AI services integrate with the backend, and how reliability, privacy, cost, and quality are managed.

The objective is to make the reading experience more natural and useful without making core product behavior dependent on unpredictable model output.

### Guiding principle

**AI assists; application logic decides.**

The LLM may interpret a reader's intent, help structure a reading journey, and explain recommendations. Deterministic application services remain responsible for catalogue truth, eligibility rules, recommendation scoring, ranking, diversity constraints, validation, and persistence.

The system must not rely on an LLM to invent books, authors, ISBNs, ratings, availability, or other catalogue facts.

---

## 2. Confirmed AI integration decisions

The following choices are confirmed for the MVP and should guide implementation.

| Area | Decision | MVP direction |
|---|---|---|
| LLM provider strategy | **Single provider** | Start with one selected LLM provider behind an adapter. Keep the interface replaceable, but do not build multi-provider orchestration initially. |
| Embedding deployment | **Local** | Run a local Sentence Transformers embedding model. Benchmark and pin the model revision before production use. |
| AI feature scope | **Core** | Include intent parsing, journey generation, recommendation explanations, and a basic reading assistant. |
| RAG scope | **Structured retrieval** | Retrieve trusted, structured application data from PostgreSQL. Defer document chunking and document RAG. |
| Recommendation explanations | **LLM-generated** | Generate natural-language explanations grounded in deterministic reason codes and verified metadata. |
| Intent parsing fallback | **Keyword search** | If intent parsing fails or produces an unusable result, fall back to basic keyword/metadata search. Ask for clarification when the fallback cannot produce useful results. |

These are MVP architecture decisions, not permanent technology commitments. Provider and model selection should be validated against quality, latency, operational complexity, and cost before deployment.

---

## 3. AI responsibility boundaries

The system uses three distinct mechanisms.

### 3.1 LLM responsibilities

The LLM may:
- Parse natural-language requests into structured reading preferences and constraints.
- Convert broad learning goals into proposed topics and stages.
- Generate recommendation explanations using supplied reason codes and verified book metadata.
- Answer basic reading-related questions using retrieved application context.
- Help users refine goals and reading preferences conversationally.

The LLM must not:
- Make the final recommendation ranking decision.
- Override user exclusions, safety rules, or backend eligibility checks.
- Create fictitious catalogue records or claim unverified facts.
- Directly read or write the database.
- Execute SQL, shell commands, arbitrary tools, or external network requests.

### 3.2 Embedding responsibilities

Embeddings support:
- Semantic representation of book descriptions, subjects, and reader interests.
- Semantic candidate retrieval.
- Similarity calculations between reader interests and catalogue items.

Embeddings do not independently decide whether a book is appropriate, available, eligible, or the best final recommendation.

### 3.3 Deterministic application responsibilities

Backend services own:
- Authentication and authorization.
- Input validation and normalization.
- Catalogue and user-data retrieval.
- Recommendation candidate generation, scoring, ranking, and diversification.
- Journey prerequisite validation and sequencing.
- Feedback persistence and profile updates.
- Output validation and persistence.

---

## 4. High-level architecture

```text
React + Tailwind Frontend
          |
          v
      FastAPI API
          |
          v
   Application Services
          |
          +------------------------------+
          |                              |
          v                              v
   AI Orchestration Layer          Domain Services
          |                              |
          +-- LLM Adapter                +-- Catalogue
          |       |                      +-- User Profile
          |       v                      +-- Library / TBR
          |   Single LLM Provider        +-- Recommendation Engine
          |                              +-- Reading Journeys
          +-- Local Embedding Adapter
          |       |
          |       v
          |   Sentence Transformers
          |
          +-- Prompt / Schema Registry
          +-- Output Validation
          +-- Policy and Safety Checks
                         |
                         v
                PostgreSQL + pgvector
```

The AI orchestration layer coordinates model calls. It must not become a second business-logic layer or bypass existing application services.

### 4.1 Proposed backend module structure

```text
backend/app/
├── ai/
│   ├── providers/
│   │   ├── llm_provider.py
│   │   └── embedding_provider.py
│   ├── prompts/
│   │   ├── intent_parser.py
│   │   ├── journey_planner.py
│   │   ├── recommendation_explainer.py
│   │   └── reading_assistant.py
│   ├── schemas/
│   │   ├── intent.py
│   │   ├── journey.py
│   │   └── explanation.py
│   ├── services/
│   │   ├── intent_service.py
│   │   ├── journey_ai_service.py
│   │   ├── explanation_service.py
│   │   └── assistant_service.py
│   ├── validation.py
│   └── orchestration.py
```

This is a proposed logical structure. It can be adjusted to match the final backend package layout.

---

## 5. LLM provider integration

### 5.1 MVP approach: one provider behind an adapter

Select one LLM provider for the initial release. All application code must call it through an internal interface rather than importing provider-specific SDKs throughout the codebase.

Benefits:
- Smaller implementation and testing surface.
- Easier configuration and monitoring.
- Reduced operational complexity during the beta.
- A clear migration path if the selected provider becomes unsuitable.

Do not implement automatic provider failover or multi-provider routing in the MVP. A controlled provider replacement can be implemented later through the adapter.

### 5.2 Provider interface

A conceptual interface:

```python
from typing import Protocol, TypeVar

T = TypeVar("T")

class LLMProvider(Protocol):
    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_schema: type[T],
        temperature: float = 0.2,
        max_output_tokens: int = 800,
    ) -> T:
        ...

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.5,
        max_output_tokens: int = 800,
    ) -> str:
        ...
```

The actual implementation should:
- Load credentials from environment variables or a secrets manager.
- Set connection and request timeouts.
- Apply bounded output-token limits.
- Normalize provider errors into application-level exceptions.
- Record provider, model, latency, token usage where available, and request outcome.
- Avoid logging raw personal data or full prompts by default.

### 5.3 Model selection

Choose the provider/model using a small evaluation set covering:
- Structured intent extraction.
- Journey topic planning.
- Recommendation explanation quality.
- Basic assistant groundedness.
- Hindi/English and other language needs relevant to beta users, if supported by the product.

Do not select a model based only on benchmark claims. Test actual Reading Compass tasks. Recheck model availability, terms, quotas, and pricing at implementation time because these can change.

---

## 6. Local embedding integration

### 6.1 MVP approach

Use a locally hosted Sentence Transformers model for embedding generation. The initial candidate is:

`sentence-transformers/all-MiniLM-L6-v2`

It produces 384-dimensional embeddings, matching the current database design.

This is a candidate baseline, not an unconditional production commitment. Benchmark it on representative book descriptions, subjects, and reader-interest text before pinning it for deployment.

### 6.2 Embedding interface

```python
from typing import Protocol

class EmbeddingProvider(Protocol):
    def embed_text(self, text: str) -> list[float]:
        ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...
```

The implementation should:
- Load the model once per application process or dedicated worker.
- Batch catalogue embedding generation where possible.
- Normalize text consistently before embedding.
- Validate vector dimension and numeric values.
- Store model name, model revision, embedding dimension, and generation metadata.
- Re-embed affected records when the model revision changes.

### 6.3 Operational considerations

Local inference avoids a per-request hosted embedding dependency but still consumes CPU/RAM and may increase deployment resource requirements. Benchmark:
- Model load time.
- Single-text and batch latency.
- Memory footprint.
- Throughput under expected beta load.
- Retrieval quality on a manually reviewed query set.

If the deployment environment cannot support local inference reliably, revisit the deployment decision explicitly rather than silently introducing a hosted dependency.

---

## 7. Prompt design and versioning

Prompts must be task-specific, concise, testable, and version-controlled.

Each prompt should define:
1. The model's limited role.
2. The trusted context it may use.
3. The expected output schema.
4. Prohibited behavior.
5. How to handle missing or conflicting information.

Suggested prompt registry:

| Prompt | Responsibility |
|---|---|
| `intent_parser_v1` | Extract structured reading intent |
| `journey_planner_v1` | Propose topic outline and learning stages |
| `recommendation_explainer_v1` | Explain already-ranked recommendations |
| `reading_assistant_v1` | Answer basic questions using retrieved context |

Record prompt version with each AI operation where practical. Prompt changes should be evaluated against regression examples before release.

### 7.1 Treat inputs as untrusted

User text, book descriptions, imported metadata, and retrieved content are data, not instructions. Prompts must explicitly tell the model not to follow instructions found inside retrieved content.

Never place secrets, access tokens, or unnecessary personal data in prompts.

---

## 8. Structured output and validation

Model output must be treated as untrusted input.

For structured tasks:
1. Request schema-constrained output where the provider supports it.
2. Parse the response into a Pydantic model.
3. Validate types, lengths, enumerations, and numeric ranges.
4. Map free-text subjects to controlled subject IDs where required.
5. Validate referenced books and users against database records and authorization rules.
6. Reject or safely repair invalid output.
7. Retry at most once for a recoverable formatting/schema failure.
8. Return a safe application error or fallback if validation still fails.

Example intent schema:

```python
from pydantic import BaseModel, Field

class ReadingIntent(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    topics: list[str] = Field(default_factory=list, max_length=10)
    goals: list[str] = Field(default_factory=list, max_length=5)
    preferred_difficulty: str | None = None
    excluded_topics: list[str] = Field(default_factory=list, max_length=20)
```

The real schema should use controlled enums or IDs for fields that affect backend decisions. Free-text model output must not be treated as a trusted database identifier.

---

## 9. MVP AI workflows

### 9.1 Natural-language recommendation request

Example user request:

> “I want to understand personal finance, starting from the basics, and I prefer practical books.”

Flow:

1. Frontend sends the request to the FastAPI recommendation endpoint.
2. Intent service calls the selected LLM through the provider adapter.
3. LLM returns a structured intent object.
4. Backend validates and normalizes the intent.
5. Recommendation engine retrieves candidates using semantic, metadata, and text search.
6. Deterministic filters remove ineligible or excluded books.
7. Recommendation engine calculates scores, ranks, and diversifies candidates.
8. Explanation service receives the final selected books, verified metadata, and reason codes. Reason codes are validated against the controlled reason codes registry (`RECOMMENDATION_ENGINE.md` §15.1) stored on `recommendation_items`.
9. LLM generates concise explanations strictly grounded in that supplied information without inventing external claims.
10. Backend validates the explanations and returns the recommendations.

**Important:** The LLM explains the result; it does not choose or reorder the final recommendations.

### 9.2 Reading journey generation

Example user goal:

> “Create a beginner-to-intermediate journey to learn economics.”

Flow:

1. LLM proposes a structured topic outline and stages.
2. Backend validates the output schema and limits.
3. Proposed topics are mapped to the application's controlled subject taxonomy.
4. Backend checks prerequisite relationships in the subject graph.
5. A topological ordering or equivalent deterministic validation checks that prerequisites are respected.
6. Recommendation engine selects books for each stage.
7. Backend checks availability in the catalogue and removes invalid references.
8. Journey is returned as an editable draft.
9. User confirms or edits it before it is saved.

The LLM may propose a learning sequence, but the backend validates and enforces prerequisite rules.

### 9.3 Basic reading assistant

The assistant should support focused tasks such as:
- Explaining a book's catalogue description.
- Helping refine reading goals.
- Explaining why a recommendation appeared.
- Helping users navigate their TBR and reading journeys.
- Suggesting ways to approach a topic or reading plan.

For each request:
1. Identify the authenticated user.
2. Retrieve only the minimum relevant records from PostgreSQL.
3. Enforce ownership and authorization checks before constructing context.
4. Pass the question and bounded context to the LLM.
5. Validate the response and return it.

The assistant must not claim access to a user's records unless those records were actually retrieved.

### 9.4 Metadata enrichment

Automated metadata enrichment is **not part of the MVP AI scope**. The MVP should rely on trusted catalogue sources and provenance tracking. A later phase may use models to suggest tags or descriptions, but any generated metadata must be marked as unverified until validated.

---

## 10. Retrieval-augmented generation (RAG)

### 10.1 MVP: structured PostgreSQL retrieval

The MVP will use structured retrieval rather than a general document-RAG pipeline.

Relevant context may include:
- Book works and editions.
- Authors, genres, and subjects.
- User preferences and reading profile.
- User library and TBR.
- Reading goals and journeys.
- Recommendation results and reason codes.

The backend retrieves the required records using normal application queries and, where useful, pgvector similarity search. The resulting context is passed to the LLM in a bounded, clearly labelled format.

### 10.2 Why document RAG is deferred

The initial product does not require ingestion, chunking, indexing, and retrieval of a large collection of external documents to deliver its core features.

Deferring document RAG:
- Keeps the MVP architecture smaller.
- Avoids unnecessary ingestion and data-quality work.
- Reduces storage and operational complexity.
- Keeps answers grounded in the product's own verified structured data.

A later version may add document RAG for licensed reading materials, user-owned notes, or trusted educational resources. That phase will require source attribution, access control, chunking strategy, and evaluation for retrieval quality.

---

## 11. Recommendation explanations

Recommendation explanations will be LLM-generated, but strictly grounded in deterministic recommendation output.

### Input supplied to the explanation service

- Selected book ID.
- Verified title and author.
- Relevant catalogue description or subject metadata.
- Deterministic reason codes.
- Relevant user-stated goal or preference, only when needed.
- A short explanation length limit.

Reason codes (drawn strictly from the controlled reason registry; see `RECOMMENDATION_ENGINE.md` §15.1 and `DATABASE_SCHEMA.md` §9.2):
- `MATCHES_CURRENT_GOAL`: Directly addresses the user's active reading goal.
- `MATCHES_FAVORITE_SUBJECT`: Aligns with explicit reader subject preferences.
- `SIMILAR_TO_HIGHLY_RATED_BOOK`: High semantic similarity to a book rated 4 or 5 by the user.
- `BEGINNER_FRIENDLY`: Calibrated for reader's introductory proficiency level.
- `INTRODUCES_NEW_AUTHOR`: Controlled discovery signal for an unread author.
- `COMPLEMENTS_TBR`: High thematic synergy with books on user's TBR list.
- `TOPIC_RELEVANCE`: Strong keyword or topic match with the user prompt.
- `HIGH_BOOK_RATING`: Well-regarded work within the reader's interest cluster.
- `POPULAR_IN_GENRE`: Strong exemplar within candidate genre.
- `NEXT_STEP_IN_SUBJECT_JOURNEY`: Logical subsequent milestone in a structured reading sequence (supported future reason code; deferred to Milestone 4B with Learning Journeys).

### Rules

The LLM must:
- Explain only the supplied reasons.
- Avoid claims not supported by metadata or reason codes.
- Not imply that a recommendation is objectively the best book.
- Not invent reviews, awards, ratings, popularity, or book contents.
- Produce concise, reader-friendly language.

If explanation generation fails, the backend should return a deterministic fallback explanation based on reason-code templates. Recommendations must remain usable even when the LLM is unavailable.

---

## 12. Failure handling and fallbacks

| Failure | Required behavior |
|---|---|
| LLM timeout | Apply a bounded retry only where safe; then use deterministic fallback or return a retryable error. |
| Intent output invalid | Validate, retry once, then fall back to keyword/metadata search. |
| Intent unclear | Attempt keyword/metadata search; ask the user to clarify if useful results cannot be found. |
| LLM provider unavailable or quota exceeded | Keep non-AI catalogue, library, and recommendation functionality available; disable or degrade optional AI features gracefully. |
| Explanation generation fails | Return deterministic reason-code-based explanation. |
| Local embedding model unavailable | Use stored embeddings if compatible; otherwise use full-text, trigram, and metadata retrieval. |
| Vector search unavailable | Fall back to PostgreSQL full-text, trigram, and metadata search. |
| Journey generation times out | Return a retryable error; do not save a partial or invalid journey. |
| Structured output fails validation | Reject unsafe output; do not persist it. |

Retries must be bounded to avoid loops, duplicate operations, and unexpected cost.

---

## 13. Performance and cost controls

The MVP should be designed to keep model usage predictable.

- Use deterministic search and business logic wherever possible.
- Call the LLM only for tasks where natural-language understanding or generation adds value.
- Keep prompts and retrieved context concise.
- Set request timeouts and maximum output-token limits.
- Use a small, task-appropriate model after evaluation.
- Cache stable catalogue embeddings and avoid recomputing them on every request.
- Batch embedding generation for catalogue imports and updates.
- Cache only outputs that are safe to reuse and whose inputs/version are represented in the cache key.
- Apply per-user and global rate limits.
- Record usage and introduce budget alerts before beta launch.
- Provide a configuration switch to disable non-essential AI features.

Do not hard-code provider prices or free-tier assumptions. Review current provider terms and pricing during deployment planning.

---

## 14. Security, privacy, and responsible use

- Send only the minimum user information required for a task.
- Never send passwords, refresh tokens, API keys, or other secrets to an LLM.
- Enforce authentication, authorization, and record ownership before retrieval.
- Treat user prompts and retrieved text as untrusted content.
- Validate all model outputs before use or persistence.
- Do not permit model output to execute SQL, shell commands, or arbitrary network requests.
- Keep provider credentials in environment variables or a secrets manager.
- Avoid logging full prompts and private reading history unless necessary and disclosed.
- Document relevant third-party processing and privacy behavior for users.
- Provide graceful handling when a user declines an optional AI-powered feature.

AI-generated reading suggestions and explanations should be presented as assistance, not as authoritative claims.

---

## 15. Observability and logging

For each AI operation, record operational metadata where available:

- Feature/task name.
- Prompt version.
- Provider and model identifier.
- Embedding model and revision, where relevant.
- Request latency.
- Input/output token usage, if provided by the LLM provider.
- Validation success or failure.
- Retry and fallback occurrence.
- Error category.
- Estimated cost, when reliable provider usage data is available.

Do not log secrets or unnecessary personal content. Use request IDs to connect application logs without exposing sensitive user data.

---

## 16. AI evaluation strategy

Evaluation should use a small, version-controlled test set before beta and expand as real feedback becomes available.

| Capability | Evaluation criteria |
|---|---|
| Intent parsing | Correct extraction of topic, goal, difficulty, exclusions, and constraints |
| Journey generation | Valid structure, topic coverage, prerequisite consistency, useful progression |
| Recommendation explanations | Faithfulness to reason codes, factual grounding, clarity, conciseness |
| Basic assistant | Relevance, groundedness, correct use of retrieved user context, safe handling of unknowns |
| Embeddings | Retrieval relevance on representative book and reader-interest queries |
| Fallbacks | Correct behavior during timeout, invalid output, provider outage, and unavailable vector search |

Use a combination of:
- Unit tests for schemas, validators, and fallback logic.
- Integration tests with mocked provider responses.
- A small manually reviewed evaluation set.
- Regression tests for representative prompts.
- Limited beta feedback and issue tracking.

Do not rely exclusively on an LLM judging another LLM. Human review should be used for a sample of outputs, especially journey quality and explanation faithfulness.

---

## 17. Implementation sequence

### Phase 1 — Foundations
- Define provider interfaces and configuration.
- Select one LLM provider and implement its adapter.
- Integrate the local embedding model.
- Pin and document the selected embedding model revision.
- Add Pydantic schemas, output validation, timeouts, and error normalization.
- Add basic AI operation logging.

### Phase 2 — Core AI features
- Implement structured natural-language intent parsing.
- Connect intent parsing to deterministic recommendation retrieval and ranking.
- Implement LLM-generated recommendation explanations grounded in reason codes.
- Implement journey outline generation with backend subject and prerequisite validation.
- Add the basic reading assistant using structured PostgreSQL retrieval.

### Phase 3 — Reliability and evaluation
- Add keyword/metadata fallback for intent parsing.
- Add deterministic explanation fallback.
- Add provider failure and timeout tests.
- Build the initial evaluation dataset.
- Benchmark local embedding latency, memory, and retrieval relevance.
- Add rate limits and usage/budget monitoring.

### Deferred beyond MVP
- Multiple LLM providers and automatic provider routing.
- Document chunking and general-purpose document RAG.
- AI-generated catalogue metadata enrichment.
- Advanced agentic workflows and autonomous tool execution.
- Fine-tuning or custom model training unless evaluation demonstrates a clear need.

---

## 18. Testing requirements

At minimum, tests should verify:

1. Provider adapter returns valid structured output for a valid response.
2. Invalid or malformed model output is rejected.
3. Recoverable schema failures trigger no more than one retry.
4. Intent parsing failure invokes keyword/metadata fallback.
5. Unhelpful fallback results lead to a clarification response.
6. Recommendation explanations cannot introduce unsupported catalogue claims.
7. LLM-generated journey topics are mapped to controlled subjects.
8. Journey prerequisite constraints are validated by backend logic.
9. Assistant retrieval is restricted to the authenticated user's records.
10. Provider timeouts and quota errors do not break non-AI endpoints.
11. Local embeddings have the expected dimension and contain finite numeric values.
12. Vector-search failure activates the supported text/metadata fallback.

Provider calls should be mocked in automated tests. Keep a small number of optional integration tests for the real provider, separated from the default fast test suite so routine CI does not depend on external API availability.

---

## 19. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Hallucinated book or author facts | Use verified catalogue data; constrain explanations to supplied metadata and reason codes. |
| Poor intent extraction | Validate structured output and fall back to keyword/metadata search. |
| Invalid journey sequence | Enforce subject mappings and prerequisite rules in backend services. |
| Provider outage or quota exhaustion | Graceful degradation and deterministic functionality. |
| Local embedding resource constraints | Benchmark deployment footprint and retain text-search fallback. |
| Prompt injection through retrieved content | Treat retrieved text as untrusted data and constrain model responsibilities. |
| Unexpected model cost or latency | Token limits, timeouts, rate limits, usage monitoring, and feature switches. |
| Model or prompt regression | Version models/prompts and maintain a regression evaluation set. |

---

## 20. Implementation parameters to finalize during development

The architectural decisions above are finalized for the MVP. The following operational parameters should be selected through implementation and benchmarking:

- Exact LLM provider and model.
- LLM timeout, retry policy, and output-token limits per task.
- Exact embedding model revision and deployment resource requirements.
- Prompt versions and evaluation examples.
- Maximum context size for assistant requests.
- Rate limits and budget-alert thresholds.
- Cache policy and invalidation rules.
- Logging retention and privacy settings.
- Performance targets for intent parsing, explanations, and journey generation.

These parameters should be documented in environment/configuration and deployment documentation rather than embedded throughout application code.

---

## 21. Final MVP design summary

The Reading Compass will use **one LLM provider behind a replaceable adapter** and **locally generated 384-dimensional embeddings**. Its core AI features will be natural-language intent parsing, reading journey generation, grounded recommendation explanations, and a basic reading assistant.

The MVP will use **structured PostgreSQL retrieval**, not a separate document-RAG pipeline. Recommendation retrieval, scoring, ranking, diversity, catalogue validation, and journey prerequisite enforcement remain deterministic backend responsibilities. If intent parsing fails, the application will fall back to keyword and metadata search, asking the user to clarify when that fallback cannot provide useful results.

This design keeps the product AI-assisted without making its core reliability dependent on model behavior.
