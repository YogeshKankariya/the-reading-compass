# System Architecture

## The Reading Compass

### AI-Powered Personal Reading Companion

**Document Version:** 1.0\
**Status:** Draft -- For Review\
**Related Document:** `docs/PRD.md`\
**Architecture Style:** Modular Monolith\
**Primary Backend:** Python + FastAPI\
**Primary Database:** PostgreSQL + pgvector

------------------------------------------------------------------------

## 1. Purpose

This document defines the proposed technical architecture for The
Reading Compass. It describes the major system components, their
responsibilities, interactions, data flows, deployment approach,
security considerations and architectural decisions.

It is intended to guide implementation and keep the frontend, backend,
recommendation engine, AI integrations and data layer consistent.

This is an initial architecture for the MVP. It deliberately avoids
premature distributed-system complexity. Components should have clear
boundaries so they can be extracted or replaced later if real usage
justifies doing so.

## 2. Architecture Goals

The architecture should:

1.  Support the functional and non-functional requirements in `PRD.md`.
2.  Keep recommendation logic separate from LLM-generated language.
3.  Support explainable, testable recommendation decisions.
4.  Maintain a reliable catalogue of books, authors, subjects and
    editions.
5.  Capture user interactions to improve personalization.
6.  Support goal-oriented reading journeys and prerequisite-aware
    sequencing.
7.  Allow external book-data and LLM providers to be replaced.
8.  Be affordable and straightforward to deploy for an initial beta.
9.  Provide clear module boundaries without requiring microservices.

### Non-goals for the MVP

The first release will not require: - Microservices or Kubernetes. - A
separate vector database. - A dedicated graph database. - Real-time
collaborative filtering. - A complex event-streaming platform. - A
native mobile application. - Distributed model serving.

These may be reconsidered when measurable product or operational needs
arise.

## 3. Architecture Overview

The MVP uses a layered modular-monolith architecture.

``` mermaid
flowchart TB
    U[Reader] --> FE[React Web Application]
    FE --> API[FastAPI Application]

    API --> AUTH[Authentication and User Module]
    API --> BOOKS[Book Catalogue Module]
    API --> LIB[Personal Library Module]
    API --> REC[Recommendation Module]
    API --> JOURNEY[Reading Journey Module]
    API --> ASSIST[Reading Assistant Module]

    AUTH --> DB[(PostgreSQL)]
    BOOKS --> DB
    LIB --> DB
    REC --> DB
    JOURNEY --> DB
    ASSIST --> DB

    REC --> EMB[Embedding and Ranking Components]
    JOURNEY --> GRAPH[Subject and Prerequisite Logic]
    ASSIST --> LLM[LLM Provider Adapter]

    EMB --> VDB[(pgvector)]
    GRAPH --> DB

    BOOKS --> PROVIDERS[Book Data Provider Adapters]
    PROVIDERS --> GB[Google Books API]
    PROVIDERS --> OL[Open Library API]

    API -. optional background jobs .-> WORKER[Background Worker]
    WORKER --> DB
    WORKER --> EMB
```

### Main architectural layers

1.  **Presentation layer:** React application and client-side state.
2.  **API layer:** FastAPI routes, request validation and response
    serialization.
3.  **Application/service layer:** Use cases and business workflows.
4.  **Domain layer:** Core entities, recommendation rules and
    reading-journey logic.
5.  **Infrastructure layer:** Database access, external APIs, embedding
    models, LLM providers and caching.

The domain and application logic should not depend directly on a
particular external provider.

## 4. Component Responsibilities

### 4.1 Frontend -- React

Responsibilities: - User registration and login screens. - Reader
onboarding and preference management. - Book search and discovery. -
Recommendation display and feedback controls. - Personal library and TBR
management. - Reading journey creation and progress views. - Reading
insights. - Conversational reading-assistant interface when introduced.

Frontend principles: - Use reusable components and feature-oriented
organization. - Keep API communication in a dedicated service layer. -
Do not implement recommendation scoring in the browser. - Treat backend
responses as authoritative. - Handle loading, empty, validation and
error states. - Protect authenticated routes while enforcing
authorization on the backend.

### 4.2 API Layer -- FastAPI

Responsibilities: - Expose versioned HTTP APIs. - Validate incoming
requests and outgoing response schemas. - Resolve the authenticated
user. - Apply authorization and rate limits. - Call application
services. - Return consistent errors and status codes.

Routes should remain thin. Business logic belongs in services or domain
modules, not directly in route handlers.

### 4.3 Authentication and User Module

Responsibilities: - Registration and login. - Password hashing and
verification. - Token or session lifecycle. - User profile management. -
Reader onboarding. - User preference updates.

All user-specific database operations must be scoped to the
authenticated user. A user-supplied `user_id` must never be trusted as
proof of ownership.

### 4.4 Book Catalogue Module

Responsibilities: - Store canonical book/work records and edition
records. - Store authors, genres, subjects and relationships. - Search
and retrieve book metadata. - Normalize provider-specific metadata. -
Deduplicate records where possible. - Track source and provenance of
imported metadata. - Trigger embedding generation when relevant
descriptive metadata changes.

The catalogue should distinguish a conceptual **work** from a particular
**edition**. A work can have multiple editions, ISBNs, publishers,
formats and translations. Recommendations should normally target works;
a user's library may optionally point to a specific edition.

External provider records should map to internal identifiers. Provider
IDs should be stored as external identifiers, not used as the
application's primary keys.

### 4.5 Personal Library Module

Responsibilities: - Add and remove books from a user's library. - Track
Want to Read, Currently Reading, Completed and Abandoned statuses. -
Store ratings, reviews, notes and progress where supported. - Maintain a
history of relevant status changes. - Emit or record interaction signals
for personalization.

A user's library entry is separate from the global book catalogue.

### 4.6 Recommendation Module

Responsibilities: - Build or retrieve a reader representation from
profile and interaction data. - Retrieve candidate books. - Calculate
content similarity and contextual features. - Apply eligibility
filters. - Rank candidates. - Diversify the final result set. - Return
recommendation scores and structured reason codes. - Record
recommendation impressions and outcomes.

The recommendation module is the authority for which books are selected
and their ranking. An LLM may explain selected results but must not
silently replace the ranking algorithm.

### 4.7 Reading Journey Module

Responsibilities: - Accept a learning goal and constraints. - Identify
relevant subjects and subtopics. - Retrieve candidate books from the
catalogue. - Use subject relationships and prerequisite information. -
Sequence books into stages. - Persist the journey and its ordered
items. - Track progress and allow user edits.

For the MVP, subject relationships can be stored in relational tables
and processed with application logic or NetworkX. A graph database is
not required initially.

### 4.8 Reading Assistant / AI Module

Responsibilities: - Interpret natural-language requests. - Extract
structured intent and constraints. - Call catalogue search and
recommendation services. - Compare retrieved book records. - Generate
explanations grounded in retrieved metadata and recommendation
results. - Return validated structured responses.

The assistant should use a provider adapter so the LLM can be changed
without changing business logic.

The assistant must not: - Invent book titles, authors, ISBNs or
publication details. - Claim a book covers a topic without supporting
catalogue information. - Override user preferences or library status. -
Directly write arbitrary database records based on unvalidated model
output.

### 4.9 Data and Persistence Layer

PostgreSQL is the system of record for users, profiles, catalogue
metadata, library entries, interactions, recommendations and reading
journeys.

pgvector stores and indexes embedding vectors in PostgreSQL. This avoids
operating a separate vector database in the MVP.

Redis is optional and should only be introduced when caching, rate
limiting or job coordination needs justify the additional service.

## 5. Recommendation Request Flow

The following describes a typical personalized recommendation request.

``` mermaid
sequenceDiagram
    actor Reader
    participant UI as React UI
    participant API as FastAPI
    participant Profile as Profile Service
    participant Rec as Recommendation Engine
    participant DB as PostgreSQL/pgvector

    Reader->>UI: Request book recommendations
    UI->>API: POST recommendation request
    API->>API: Authenticate and validate request
    API->>Profile: Load reader profile and signals
    Profile->>DB: Fetch preferences and interactions
    DB-->>Profile: Profile data
    Profile-->>Rec: Reader representation and constraints
    Rec->>DB: Retrieve eligible candidate books and vectors
    DB-->>Rec: Candidate records and similarity results
    Rec->>Rec: Filter, score, rank and diversify
    Rec->>DB: Store recommendation log and reason codes
    Rec-->>API: Ranked recommendations
    API-->>UI: Structured recommendation response
    UI-->>Reader: Display books and explanations
```

### Recommendation flow rules

1.  Authenticate the user before loading their profile.
2.  Exclude ineligible books, such as books the user has completed,
    unless the request says otherwise.
3.  Retrieve candidates from the internal catalogue.
4.  Calculate ranking scores using the configured recommendation
    strategy.
5.  Apply diversity and deduplication rules.
6.  Return stable book identifiers, scores and reason codes.
7.  Store enough information to reproduce or audit why a recommendation
    was shown.

LLM-generated natural-language explanations can be added after ranking.
The system should retain deterministic reason codes even if the
explanation provider is unavailable.

## 6. Reading Journey Generation Flow

``` mermaid
flowchart TD
    A[User submits learning goal] --> B[Validate goal and constraints]
    B --> C[Extract structured topics and level]
    C --> D[Resolve topics against subject catalogue]
    D --> E[Retrieve candidate books]
    E --> F[Evaluate relevance and difficulty]
    F --> G[Apply prerequisite relationships]
    G --> H[Sequence books into stages]
    H --> I[Validate sequence and catalogue records]
    I --> J[Persist journey and ordered books]
    J --> K[Return journey to user]
```

The goal interpretation step may use an LLM, but the output must be
converted into a validated structure. Retrieval, eligibility checks and
persistence remain controlled by application code.

The sequence should communicate uncertainty. If prerequisite metadata is
incomplete, the system should present the order as a suggested learning
path rather than an objectively correct sequence.

## 7. Book Data Ingestion and Enrichment Flow

1.  A user or scheduled process requests a book search or import.
2.  The relevant provider adapter calls an external book-data API.
3.  Provider responses are mapped into an internal normalized schema.
4.  Records are matched against existing works and editions.
5.  New records or metadata updates are persisted with source
    information.
6.  If the title, description, subjects or other embedding inputs
    change, the book is marked for embedding generation.
7.  Embeddings are generated and stored with the model name and version.
8.  Search and recommendation services can use the updated record.

### Provider integration principles

-   Keep provider-specific response parsing inside adapters.
-   Apply timeouts, retries with backoff and rate limits.
-   Cache appropriate provider responses.
-   Respect provider terms, attribution requirements and usage policies.
-   Do not assume every provider supplies complete or consistent
    metadata.
-   Preserve source attribution and retrieval timestamps.
-   Provide graceful fallback when a provider is unavailable.

## 8. Data Architecture

### Main data domains

  -----------------------------------------------------------------------
  Domain                              Example records
  ----------------------------------- -----------------------------------
  Identity                            Users, credentials, sessions

  Reader profile                      Goals, preferences, reading level

  Catalogue                           Works, editions, authors, genres,
                                      subjects

  Library                             User-book status, ratings, notes

  Behaviour                           Views, clicks, saves, dismissals,
                                      completions

  Recommendations                     Request context, candidates,
                                      scores, reason codes

  Reading journeys                    Goals, stages, ordered books,
                                      progress

  AI operations                       Provider, model version, request
                                      metadata, usage and errors
  -----------------------------------------------------------------------

### Data ownership rules

-   The catalogue owns canonical book and author records.
-   The library owns a user's relationship with a book.
-   The profile module owns explicit reader preferences.
-   The interaction module owns behavioural events.
-   The recommendation module owns ranking outputs and recommendation
    logs.
-   The journey module owns journey structure and progress.

### Embedding lifecycle

Each embedding record should include: - Internal book/work identifier. -
Vector value. - Embedding model identifier. - Model version or
configuration. - Text or metadata version used to generate it. -
Generation timestamp.

Embeddings must be regenerated when their source text changes or when
the embedding model changes. Do not compare vectors generated by
incompatible models as if they were in the same vector space.

## 9. API and Module Boundaries

The backend should expose versioned endpoints under `/api/v1`.

Suggested module boundaries:

  Module              Owns
  ------------------- ------------------------------------------
  `auth`              Registration, login and identity
  `users`             User account and profile
  `catalogue`         Books, authors, editions and subjects
  `library`           User-book relationships
  `interactions`      Behavioural signals and feedback
  `recommendations`   Candidate retrieval, ranking and logs
  `journeys`          Goal decomposition and reading sequences
  `assistant`         Natural-language orchestration
  `integrations`      External providers and adapters

Detailed endpoint contracts belong in `API_SPECIFICATION.md`, not this
architecture document.

Modules should communicate through service interfaces rather than
importing one another's internal database models indiscriminately.

## 10. Background Processing

Some operations may be too expensive or slow for a synchronous request:

-   Bulk book imports.
-   Embedding generation.
-   Embedding regeneration.
-   Catalogue enrichment.
-   Periodic analytics aggregation.
-   Large recommendation evaluation jobs.

For the earliest MVP, these can run as controlled management commands or
simple background tasks. Introduce a dedicated worker and queue when
asynchronous processing is needed.

Background jobs should be: - Idempotent where possible. - Retryable with
bounded attempts. - Observable through status and logs. - Safe to resume
after failure.

Do not add Redis or a task queue solely for architectural appearance.

## 11. Security and Privacy Architecture

### Authentication and authorization

-   Hash passwords using a modern password-hashing algorithm such as
    Argon2id or bcrypt.
-   Use secure token/session handling.
-   Enforce authorization in backend services.
-   Scope library, profile and journey data to the authenticated user.
-   Protect administrative and ingestion operations.

### Application security

-   Validate and constrain all inputs.
-   Use parameterized database queries or a safe ORM.
-   Apply rate limits to authentication, search and LLM-backed
    endpoints.
-   Configure CORS narrowly.
-   Store secrets in deployment environment variables or a secret
    manager.
-   Never commit `.env` files, API keys or credentials.
-   Avoid exposing internal errors or stack traces to clients.

### Privacy

-   Collect only data needed for product functionality.
-   Explain what reading activity is stored and how it affects
    recommendations.
-   Provide a way to delete or export user data as the product matures.
-   Avoid sending unnecessary personal information to external LLM
    providers.
-   Define retention rules for interaction and AI request logs.

### LLM safety and reliability

-   Treat user prompts and external metadata as untrusted input.
-   Validate structured model output before using it.
-   Do not let model output execute SQL or arbitrary tools.
-   Ground book claims in catalogue records.
-   Apply request limits and cost controls.
-   Provide fallback behaviour when the LLM provider is unavailable.

## 12. Reliability, Observability and Performance

### Logging

Use structured logs with request identifiers. Do not log passwords,
access tokens or unnecessary personal information.

### Monitoring

Track: - API latency and error rates. - Database query latency. -
External provider failures and rate limits. - Embedding-generation
failures. - LLM latency, token usage and errors. -
Recommendation-generation latency. - Background job failures.

### Performance strategy

-   Add indexes for common catalogue and user queries.
-   Use pgvector indexes only after selecting an appropriate distance
    metric and validating query performance.
-   Paginate book search and library results.
-   Cache only data with a clear invalidation strategy.
-   Avoid recomputing embeddings on every request.
-   Set timeouts for external APIs.
-   Establish performance baselines before optimizing prematurely.

### Graceful degradation

-   Book search should continue to work from the local catalogue when an
    external provider is down.
-   Recommendations should still return ranked results if LLM
    explanations fail.
-   Core library operations should not depend on an LLM.
-   A failed embedding job should be retryable without corrupting the
    catalogue.

## 13. Deployment Architecture

### Initial deployment

  -----------------------------------------------------------------------
  Component                           Proposed hosting
  ----------------------------------- -----------------------------------
  React frontend                      Vercel

  FastAPI backend                     Render or Railway

  PostgreSQL + pgvector               Managed PostgreSQL provider
                                      supporting pgvector

  Redis                               Optional managed instance

  Source control                      GitHub

  CI/CD                               GitHub Actions
  -----------------------------------------------------------------------

The selected PostgreSQL provider must explicitly support the pgvector
extension and the required version.

### Environments

Maintain separate configurations for: - Local development. - Test/CI. -
Production.

Use environment variables for configuration and secrets. Keep production
credentials separate from local and CI credentials.

### CI/CD expectations

On pull requests: 1. Install dependencies. 2. Run formatting and lint
checks. 3. Run unit tests. 4. Run integration tests where infrastructure
is available. 5. Validate migrations and application startup.

On approved merges to the deployment branch: 1. Build the application.
2. Deploy using the hosting provider's supported workflow. 3. Apply
migrations safely. 4. Run a health check. 5. Report deployment success
or failure.

## 14. Repository-Level Architecture

Proposed repository structure:

``` text
the-reading-compass/
├── docs/
│   ├── PRD.md
│   ├── SYSTEM_ARCHITECTURE.md
│   ├── DATABASE_SCHEMA.md
│   ├── RECOMMENDATION_ENGINE.md
│   ├── API_SPECIFICATION.md
│   ├── AI_INTEGRATION.md
│   ├── TESTING_STRATEGY.md
│   └── DEVELOPMENT_ROADMAP.md
│
├── frontend/
├── backend/
├── ml/
├── scripts/
├── .github/
│   └── workflows/
├── .gitignore
├── README.md
└── LICENSE
```

This is the intended structure. Directories should be created when their
implementation phase begins, not necessarily all at once.

## 15. Architectural Decisions

  -----------------------------------------------------------------------
  ID                      Decision                Rationale
  ----------------------- ----------------------- -----------------------
  ADR-001                 Use a modular monolith  Simpler development and
                          for MVP                 deployment with clear
                                                  internal boundaries

  ADR-002                 Use PostgreSQL as       Reliable relational
                          system of record        storage for product
                                                  data

  ADR-003                 Use pgvector initially  Semantic retrieval
                                                  without a separate
                                                  vector database

  ADR-004                 Keep recommendation     Reproducibility,
                          ranking separate from   evaluation and control
                          LLM                     

  ADR-005                 Use provider adapters   Replace external APIs
                                                  and models without
                                                  rewriting domain logic

  ADR-006                 Model works separately  Avoid duplicate
                          from editions           recommendations across
                                                  editions and
                                                  translations

  ADR-007                 Defer collaborative     Avoid unreliable
                          filtering               results with sparse
                                                  user interactions

  ADR-008                 Defer graph database    Relational subject
                                                  relationships are
                                                  sufficient for initial
                                                  scope

  ADR-009                 Make Redis optional     Avoid unnecessary
                                                  infrastructure and
                                                  operational cost

  ADR-010                 Use background          Keep initial deployment
                          processing selectively  simple while allowing
                                                  expensive jobs to move
                                                  out of request paths
  -----------------------------------------------------------------------

Decisions should be revisited when user volume, catalogue size, latency,
cost or feature requirements provide evidence that a change is needed.

## 16. Key Risks and Mitigations

  -----------------------------------------------------------------------
  Risk                                Mitigation
  ----------------------------------- -----------------------------------
  Incomplete or inconsistent book     Normalize, validate and preserve
  metadata                            source provenance

  Duplicate books and editions        Separate work and edition records;
                                      implement matching rules

  Sparse user interactions            Begin with content-based
                                      recommendations and explicit
                                      preferences

  Repetitive recommendations          Apply diversity-aware ranking and
                                      measure catalogue coverage

  LLM hallucinations                  Ground responses in retrieved
                                      records and validate output

  External API outages                Local catalogue, timeouts, retries
                                      and graceful fallback

  Embedding model changes             Store model versions and regenerate
                                      vectors consistently

  High inference/API cost             Cache, limit requests and use
                                      smaller models where appropriate

  User data exposure                  Enforce ownership checks, secure
                                      secrets and minimize logging
  -----------------------------------------------------------------------

## 17. Implementation Sequence

The architecture suggests the following order:

1.  Repository setup and backend foundation.
2.  PostgreSQL schema and migrations.
3.  Authentication and reader profiles.
4.  Book catalogue and provider adapters.
5.  Personal library and interaction tracking.
6.  Embedding generation and semantic retrieval.
7.  Content-based recommendation and ranking.
8.  TBR prioritization.
9.  Reading journey generation.
10. LLM-powered explanations and assistant.
11. Testing, observability and deployment.
12. Beta launch and recommendation evaluation.

The detailed task breakdown and dependencies will be maintained in
`DEVELOPMENT_ROADMAP.md`.

## 18. Open Questions

The following decisions require validation during implementation:

1.  Which managed PostgreSQL provider will host pgvector?
2.  Which book-data provider offers the best coverage and acceptable
    usage terms for the initial catalogue?
3.  Which embedding model gives an acceptable balance of relevance,
    latency, hosting cost and language support?
4.  Which LLM provider and model will be used for the first release?
5.  Will embeddings be generated synchronously for small updates or
    through a background worker?
6.  What catalogue size and beta traffic should the initial hosting
    configuration support?
7.  Which user data retention and deletion controls are required before
    public launch?

These questions should be resolved with small experiments and documented
decisions rather than assumptions.

------------------------------------------------------------------------

## 19. Document Status

**Current Status:** Draft for review.

**Depends on:** `PRD.md`

Before implementation: 1. Review the architecture and open questions. 2.
Confirm the MVP technology choices. 3. Resolve decisions that affect the
database model. 4. Finalize the database schema. 5. Translate the
architecture into implementation tasks.

This document is the technical reference for the initial architecture of
The Reading Compass.
