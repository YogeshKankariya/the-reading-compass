# Development Roadmap

**Project:** The Reading Compass  
**Document version:** 1.1  
**Status:** Finalized for MVP implementation  
**Last updated:** 1 November 2026

---

## 1. Purpose

This roadmap turns the product requirements, architecture, database design, recommendation engine, API specification, AI integration, and testing strategy into an actionable implementation sequence.

It defines:
- Development phases and their dependencies.
- Major tasks and deliverables.
- Milestones and acceptance criteria.
- Testing and documentation expectations.
- MVP release and beta preparation.

The roadmap is designed for a focused individual or small team. It prioritizes a working, testable product over building every planned feature at once.

---

## 2. Confirmed roadmap decisions

| Area | Confirmed decision | Implementation direction |
|---|---|---|
| Implementation order | **Backend-first** | Establish backend and database foundations, then deliver features in backend-led vertical slices. |
| Catalogue data | **Hybrid** | Use Google Books and Open Library adapters alongside a small curated seed dataset. |
| Frontend timing | **Early** | Set up React early and integrate workflows as backend APIs become available. |
| Deployment timing | **Early** | Deploy a minimal working environment early, then deploy and verify milestone increments. |
| Beta scope | **Complete core experience** | Require all core workflows, security checks, testing, and monitoring before inviting beta users. |
| Timeline | **Milestone-based** | Avoid speculative calendar deadlines; estimate durations as actual development velocity becomes clear. |
| Development schedule | **Alongside academics** | Keep the plan flexible, work in manageable increments, and reserve time for testing and debugging. |

These decisions keep the project manageable alongside academic responsibilities. Milestone completion is determined by exit criteria, not a calendar date.

---

## 3. Product delivery target

### MVP outcome

Deliver a usable web application where a reader can:
1. Create an account and sign in.
2. Set reading interests, preferences, and goals.
3. Discover and search for books.
4. Maintain a personal library and TBR list.
5. Receive personalized, explainable recommendations.
6. Track reading progress and provide feedback.
7. Generate and edit a goal-oriented reading journey.
8. Use a basic AI reading assistant grounded in their application data.

### Initial beta

The initial beta target is approximately **20–30 users**. The beta should validate whether readers find the recommendations relevant, diverse, understandable, and useful enough to return to the product.

### MVP non-goals

Defer the following unless beta evidence makes them necessary:
- Microservices.
- Collaborative filtering that requires a substantial interaction dataset.
- A graph database.
- Multi-provider LLM routing.
- Document chunking and general-purpose document RAG.
- AI-generated catalogue metadata enrichment.
- Autonomous agents that execute arbitrary tools.
- Native mobile applications.
- Complex social, community, or gamification features.

---

## 4. Delivery principles

1. **Build in vertical slices.** Each phase should produce functionality that can be run and tested.
2. **Keep the architecture modular.** Start with a modular monolith and clear service boundaries.
3. **Establish data quality early.** Recommendation quality depends on reliable catalogue metadata and subject relationships.
4. **Keep AI bounded.** The LLM interprets and explains; deterministic backend services validate, rank, and persist.
5. **Test alongside implementation.** Do not postpone testing until the end.
6. **Deploy before the final phase.** Establish a working deployment early enough to expose configuration and infrastructure problems.
7. **Measure before optimizing.** Benchmark search, embeddings, and recommendation quality using representative data.
8. **Protect the MVP boundary.** Add features only when they support the defined beta outcome.

---

## 5. Technology baseline

The current planned stack is:

| Layer | Technology |
|---|---|
| Frontend | React + Tailwind CSS |
| Frontend testing | Vitest, React Testing Library, Playwright |
| Backend | Python + FastAPI |
| Database | PostgreSQL |
| Vector search | pgvector |
| Text search | PostgreSQL full-text search + pg_trgm |
| ORM/data access | SQLAlchemy (or the selected documented data-access layer) |
| Migrations | Alembic if using SQLAlchemy |
| Recommendation/ML | Python, scikit-learn, Sentence Transformers |
| Embeddings | Local 384-dimensional Sentence Transformers model, subject to benchmark |
| LLM | One selected provider behind an adapter |
| Backend tests | pytest |
| CI/CD | GitHub Actions |
| Frontend hosting | Vercel |
| Backend hosting | Render or Railway |
| Database hosting | Managed PostgreSQL with required extensions |
| Optional cache | Redis, only if a demonstrated need arises |

Before implementation, verify exact library versions and hosting support. Do not add optional infrastructure merely because it appears in the architecture.

---

## 6. Phase overview

| Phase | Focus | Exit milestone |
|---|---|---|
| 0 | Repository and development foundation | Project runs locally with quality checks |
| 1 | Backend foundation and database | Migrations, health checks, and database access work |
| 2 | Authentication and reader profile | Users can register, sign in, and save preferences |
| 3 | Catalogue and discovery | Books can be imported, normalized, searched, and viewed |
| 4 | Personal library and goals | Users can manage TBR, progress, ratings, and goals |
| 5 | Recommendation engine | Personalized, filtered, diverse recommendations work |
| 6 | AI integration and journeys | Core AI workflows work with validation and fallbacks |
| 7 | Frontend product experience | Critical reader workflows work end to end |
| 8 | Hardening, deployment, and beta | Production-like deployment passes release checks |

Some phases can overlap once their dependencies are stable. The order identifies the recommended sequence, not a requirement to finish every task in one uninterrupted block.

---

## 7. Phase 0 — Repository and development foundation

### Objective

Create a clean, reproducible development environment before implementing product features.

### Tasks

- Review and commit the approved planning documents under `docs/`.
- Confirm the repository name and default branch.
- Add or verify `.gitignore` and license.
- Create the agreed high-level repository structure.
- Add a root README with project overview and setup placeholders.
- Define environment-variable conventions and provide `.env.example` files.
- Ensure real secrets are never committed.
- Choose and document Python and Node.js versions.
- Set up backend and frontend dependency management.
- Add basic formatting and linting configuration.
- Add initial GitHub Actions workflows for basic checks.
- Create a minimal FastAPI health endpoint and minimal React application shell.
- Prepare a minimal development/staging deployment path early, before all product features exist.

### Deliverables

- Repository structure.
- Project README and setup instructions.
- Backend and frontend run locally.
- `.env.example` files with placeholder values only.
- Initial CI checks.

### Exit criteria

- A new developer can follow the README and start both applications.
- No credentials or local environment files are committed.
- Basic lint/build checks run successfully.
- The repository has a clear contribution and branch workflow.

---

## 8. Phase 1 — Backend foundation and database

### Objective

Establish the backend structure, database connectivity, schema migrations, and shared infrastructure.

### Tasks

- Set up FastAPI application configuration.
- Define API versioning and common response/error conventions.
- Establish modular backend package boundaries.
- Configure SQLAlchemy and database sessions, if using SQLAlchemy.
- Configure Alembic migrations.
- Provision local PostgreSQL with `vector` and `pg_trgm` extensions.
- Implement the finalized database schema in migration-sized steps.
- Add repository/data-access patterns where useful.
- Add structured logging and request IDs.
- Add `/health` and readiness checks.
- Create test database configuration and reusable pytest fixtures.
- Add initial PostgreSQL-backed integration tests.

### Deliverables

- Working backend application.
- PostgreSQL schema and migrations.
- Database connection and health checks.
- Test database and initial integration test setup.

### Exit criteria

- Migrations apply from an empty database.
- Required PostgreSQL extensions are available.
- Application can connect, query, and close database sessions safely.
- Health/readiness endpoints return meaningful status.
- CI can run database-backed tests.

---

## 9. Phase 2 — Authentication and reader profile

### Objective

Allow users to create accounts and establish the profile used by personalization.

### Tasks

- Implement email/password registration and login.
- Implement Google OAuth according to the API specification.
- Implement access-token validation.
- Implement secure refresh-cookie handling and refresh-token rotation.
- Implement session revocation/logout.
- Add authorization dependencies for protected endpoints.
- Implement reader profile CRUD.
- Implement explicit reading preferences:
  - Favorite and excluded subjects.
  - Preferred genres.
  - Difficulty/experience preferences.
  - Reading interests and goals.
- Add validation and ownership tests.
- Build basic frontend sign-up, login, and profile forms.

### Deliverables

- Authentication endpoints.
- User profile and preference endpoints.
- Protected frontend routes.
- Tests for session handling and cross-user access.

### Exit criteria

- A user can register, sign in, update preferences, and sign out.
- Protected endpoints reject unauthenticated requests.
- One user cannot access another user's profile.
- Refresh-token and cookie behavior matches the API/security design.

---

## 10. Phase 3 — Catalogue and book discovery

### Objective

Create a reliable book catalogue and allow users to find books.

### Tasks

- Implement book work, edition, author, genre, and subject data models.
- Implement catalogue provider adapters for Google Books and Open Library as appropriate.
- Normalize external provider responses into internal schemas.
- Store external identifiers and data provenance.
- Handle duplicate works and edition mapping.
- Define which fields are trusted, optional, or provider-sourced.
- Implement catalogue search:
  - Exact title/author matching.
  - PostgreSQL full-text search.
  - Trigram matching for typo tolerance.
  - Filters for subject, genre, and other supported metadata.
- Add pagination and search tests.
- Add a repeatable catalogue import/seed command.
- Create a small curated seed catalogue for local development and recommendation evaluation.

### Deliverables

- Normalized catalogue.
- External catalogue adapters.
- Search and book-detail endpoints.
- Seed/import process.

### Exit criteria

- A user can search for books and open a book detail page.
- Duplicate provider records do not create uncontrolled duplicate works.
- Missing provider metadata does not break search or display.
- Search works when one external provider is unavailable, using already stored catalogue data.

---

## 11. Phase 4 — Personal library, progress, and goals

### Objective

Give readers a place to organize books and record reading activity.

### Tasks

- Implement add/remove book from personal library.
- Implement TBR, currently reading, completed, and other supported statuses.
- Implement rating on the agreed 1–5 star scale.
- Implement page-based and percentage progress.
- Validate page counts and progress boundaries.
- Implement reading goals and goal progress.
- Implement interaction/feedback event recording.
- Ensure all user-owned data is scoped by authenticated user.
- Build frontend library, TBR, book-status, progress, and goal views.
- Add API and service tests.

### Deliverables

- Personal library APIs and UI.
- TBR management.
- Reading progress and rating.
- Reading goals.

### Exit criteria

- A user can add a book to TBR, move it to currently reading, update progress, and mark it completed.
- Progress values remain consistent and within valid bounds.
- Ratings follow the 1–5 scale.
- Library and goal data are private to their owner.

---

## 12. Phase 5 — Recommendation engine

### Objective

Deliver the deterministic recommendation system that forms the core product value.

### Tasks

- **Milestone 5.1 — User Interest Profile Foundation (Completed):**
  - Profile configuration, internal schemas, pure Rocchio/blending vector mathematics, signal extraction and precedence, topic affinity calculation, transactional orchestration (`pg_advisory_xact_lock`, freshness fingerprinting), and FastAPI background refresh tasks.
- **Milestone 5.2 — Candidate Generation & Retrieval (100 unique eligible candidate target):**
  - **5.2.1 Retrieval Foundation & Contracts:** Retrieval configuration, `CandidateBook` & retrieval contracts, retrieval-source provenance definitions, 100-candidate target configuration, eligibility policy contracts, and retrieval service interfaces.
  - **5.2.2 Semantic Candidate Retrieval:** PostgreSQL pgvector cosine-distance retrieval using `UserInterestProfile.interest_vector`, exact model/revision compatibility, active embedding validation, and bounded retrieval limits.
  - **5.2.3 Metadata Retrieval & Cold Start:** Deterministic genre, subject, and supported explicit author matching, inferred topic matching, and cold-start retrieval when `interest_vector` is `NULL`.
  - **5.2.4 Candidate Merging & Eligibility:** Channel merging with `work_id` deduplication and full provenance preservation, independent eligibility rules, incomplete catalogue safety, and bounded retrieval expansion when filtering reduces pool size below 100.
  - **5.2.5 Retrieval Orchestration & API Integration:** End-to-end retrieval orchestration targeting 100 eligible works, deterministic ordering/pagination, internal FastAPI dependency injection wiring (no temporary public candidate-preview endpoint; public endpoint follows in Milestone 5.3), and structured candidate sets.
  - **5.2.6 Testing & Performance Validation:** Unit and PostgreSQL/pgvector integration tests, cold-start and sparse-catalogue scenarios, concurrent retrieval, `EXPLAIN` query-plan/index verification, and latency/recall benchmarking.
- Integrate the local embedding model behind the embedding interface.
- Benchmark the candidate 384-dimensional model on representative book and query text.
- Store embeddings with model/revision metadata.
- Implement semantic candidate retrieval.
- Combine semantic, text, and metadata candidate sources.
- Implement hard filtering for exclusions and eligibility.
- Implement the initial weighted scoring baseline.
- Implement MMR-based diversity and soft author/genre limits.
- Implement TBR prioritization using the existing recommendation service contract.
- Generate deterministic reason codes for recommendations.
- Add ranking and invariant unit tests.
- Build the initial 30–50 case manually reviewed evaluation dataset.
- Measure Precision@K, NDCG@K, diversity, coverage, novelty, and constraint violations.
- Record baseline results before tuning weights.

### Deliverables

- Recommendation candidate retrieval.
- Deterministic scoring and ranking.
- Diversity selection.
- TBR prioritization.
- Recommendation reason codes.
- Evaluation dataset and baseline report.

### Exit criteria

- Recommendations are personalized using explicit preferences and available behavioral signals.
- Exclusions and eligibility rules are enforced by backend logic.
- Ranking is deterministic for fixed inputs and configuration.
- Diversity behavior is tested.
- Recommendation quality has been reviewed on the initial evaluation dataset.
- The LLM is not responsible for final ranking.

---

## 13. Phase 6 — AI integration and reading journeys

### Objective

Add the agreed AI capabilities while preserving deterministic backend control.

### Tasks

- Implement the single LLM provider adapter.
- Add configuration, timeouts, output limits, and normalized provider errors.
- Implement prompt and schema versioning.
- Implement Pydantic validation for model outputs.
- Add structured intent parsing.
- Connect intent parsing to catalogue retrieval and the recommendation engine.
- Add keyword/metadata fallback when intent parsing fails.
- Implement LLM-generated explanations grounded in reason codes and verified metadata.
- Add deterministic explanation fallback.
- Implement journey topic-outline generation.
- Map generated topics to controlled subject records.
- Validate prerequisites and sequence with backend logic.
- Return journeys as editable drafts before saving.
- Implement the basic reading assistant using structured PostgreSQL retrieval.
- Add mocked provider tests for valid output, invalid output, timeout, quota, and fallback.
- Add privacy, prompt-injection, and authorization checks.

### Deliverables

- LLM adapter and prompt registry.
- Natural-language intent parsing.
- Grounded recommendation explanations.
- Journey outline generation and validation.
- Basic reading assistant.
- AI fallback and evaluation tests.

### Exit criteria

- AI workflows return schema-valid outputs or safe fallbacks.
- Intent failure falls back to keyword/metadata search.
- Recommendation explanations cannot alter ranking or invent unsupported facts.
- Journey prerequisite rules are enforced by backend services.
- Assistant context is limited to authorized structured records.
- Core catalogue and library functionality remains usable if the LLM provider is unavailable.

---

## 14. Phase 7 — Frontend product experience

### Objective

Connect the backend capabilities into a coherent and understandable reader experience. The frontend foundation is established early in Phase 0, then screens and workflows are implemented progressively as backend contracts become available. This phase completes the integrated product experience rather than starting frontend development from scratch.

### Main screens

- Landing and authentication.
- Onboarding and reader profile.
- Home/dashboard.
- Catalogue search and book details.
- Personalized recommendations and explanation details.
- Personal library and TBR.
- Reading progress and goals.
- Reading journey builder and journey view.
- Basic AI assistant interface.
- Account and privacy settings.

### Tasks

- Establish frontend routing and API client.
- Implement shared layout and reusable components.
- Implement loading, error, empty, and retry states.
- Connect screens to the finalized API contracts.
- Handle authentication and session refresh safely.
- Ensure accessible labels, keyboard navigation, and responsive layouts.
- Add Vitest and React Testing Library component tests.
- Add Playwright tests for critical workflows.
- Conduct manual usability testing with a small group.

### Deliverables

- Integrated React application.
- Responsive core screens.
- Component tests.
- Critical end-to-end workflows.

### Exit criteria

A test user can complete the main journey without developer intervention:

1. Register/sign in.
2. Set reading preferences.
3. Search and open a book.
4. Add books to TBR.
5. Receive recommendations and understand why they were selected.
6. Update reading progress.
7. Generate and edit a reading journey.

---

## 15. Phase 8 — Hardening, deployment, and beta

### Objective

Harden the early deployment into a stable, observable release and invite the initial beta cohort. The first minimal deployment should already have been exercised during earlier milestones.

### Tasks

- Configure production environment variables and secrets.
- Deploy frontend to Vercel.
- Deploy FastAPI backend to the selected hosting provider.
- Provision managed PostgreSQL with `vector` and `pg_trgm`.
- Configure allowed origins, secure cookies, HTTPS, and CORS.
- Apply migrations safely.
- Configure logging and basic error monitoring.
- Configure database backup and recovery procedures.
- Add separate GitHub Actions backend, frontend, and deployment smoke-test workflows.
- Run automatic smoke tests after deployment.
- Test graceful degradation when LLM or catalogue providers fail.
- Run security checks and dependency scanning.
- Benchmark API, search, recommendation, and embedding latency.
- Review privacy and third-party AI processing disclosures.
- Create test accounts and beta onboarding instructions.
- Invite approximately 20–30 beta users in a controlled rollout.
- Collect structured feedback and prioritize defects.

### Deliverables

- Deployed application.
- Production-like database and configuration.
- Automated CI/CD and deployment smoke tests.
- Monitoring and backup approach.
- Beta onboarding and feedback process.
- Release readiness report.

### Exit criteria

- Critical user workflows work in the deployed environment.
- No known critical or high-severity security issues remain.
- Database backup and recovery approach is documented and tested to an appropriate level.
- Provider failures do not take down non-AI core functionality.
- Smoke tests pass after deployment.
- Beta users can report feedback and defects through a defined process.

---

## 16. Milestone checklist

| Milestone | Evidence of completion |
|---|---|
| M0 — Development foundation | Repository, local setup, README, CI basics |
| M1 — Backend and database | Migrations, PostgreSQL extensions, health checks |
| M2 — User accounts | Registration, login, profile, preferences |
| M3 — Book catalogue | Normalized records, import, search, details |
| M4 — Reader workspace | Library, TBR, progress, ratings, goals |
| M5 — Recommendations | Personalized ranking, diversity, evaluation baseline |
| M6 — AI features | Intent, explanations, journeys, assistant, fallbacks |
| M7 — Complete product flow | Integrated frontend and critical E2E tests |
| M8 — Beta release | Deployment, monitoring, smoke tests, beta onboarding |

A milestone is complete when its exit criteria are met, not merely when code has been written.

---

## 17. Dependency map

```text
Repository foundation
        |
        v
Backend + PostgreSQL foundation
        |
        v
Authentication + reader profile
        |
        v
Catalogue + search
        |
        +----------------------+
        |                      |
        v                      v
Personal library          Catalogue embeddings
+ goals                       |
        |                      |
        +----------+-----------+
                   |
                   v
          Recommendation engine
                   |
          +--------+---------+
          |                  |
          v                  v
   AI explanations     Journey generation
          |                  |
          +--------+---------+
                   |
                   v
          Integrated frontend
                   |
                   v
       Testing + deployment hardening
                   |
                   v
               Beta release
```

Some foundational frontend work can happen earlier, but the complete recommendation and AI user flows depend on the backend contracts and data being available.

---

## 18. Suggested working rhythm

The project will be developed **alongside academic responsibilities**, so the schedule should remain flexible rather than assuming full-time availability.

- Work in small, clearly scoped tasks.
- Prefer one meaningful backend-led vertical slice at a time after the initial foundations.
- Keep tasks small enough to test and review independently.
- Commit working increments rather than accumulating large uncommitted changes.
- Update documentation when an architectural decision changes.
- Reserve time in each development cycle for tests, debugging, and cleanup.
- Review progress at the end of each milestone.
- Avoid daily quotas that create pressure during examinations or academic deadlines.

Use milestone-based planning. Estimate phase duration only after repository setup and the first working vertical slice reveal the actual development pace.

---

## 19. Git and contribution workflow

Suggested lightweight workflow:

1. Keep `main` in a working, releasable state.
2. Create a short-lived feature branch for each task or cohesive change.
3. Make focused commits with descriptive messages.
4. Run relevant tests locally before opening a pull request.
5. Use pull requests for review, even when working individually, to preserve a clear change history.
6. Require GitHub Actions checks to pass before merging.
7. Update README or relevant docs when setup, APIs, or architecture changes.

Example branch names:

```text
feature/authentication
feature/book-catalogue-search
feature/user-library
feature/recommendation-ranking
feature/reading-journeys
fix/progress-validation
```

---

## 20. Scope control and change management

When a new idea arises, evaluate it against these questions:

1. Does it support a defined MVP user workflow?
2. Is it required for beta validation?
3. Does it introduce a new service or operational dependency?
4. Can it be delivered safely without delaying core functionality?
5. Can it be deferred until user feedback validates the need?

Record significant changes in the relevant planning document. Avoid changing the architecture simply to adopt a new tool or framework.

---

## 21. Final implementation guidance

The roadmap's major sequencing and delivery choices are finalized. During implementation:

- Backend and database foundations come first; implement backend-led vertical slices after the foundation is stable.
- Use Google Books and Open Library adapters plus a small curated seed dataset. Normalize external API responses behind internal catalogue interfaces.
- Set up React early and connect each feature incrementally as its backend API becomes available.
- Deploy a minimal working environment early, then deploy and smoke-test meaningful milestone increments.
- Do not invite beta users until the complete core reader experience, security checks, tests, monitoring, and release-readiness requirements are satisfied.
- Keep the timeline milestone-based and flexible alongside academics. Estimate time using observed development pace rather than speculative calendar commitments.

Operational details such as hosting plans, provider credentials, deployment environments, and milestone dates can be selected during implementation without changing these confirmed principles.

## 22. Summary

The Reading Compass will be built in controlled milestones: repository foundation, backend and database, authentication, catalogue, personal library, recommendation engine, AI features, frontend integration, and deployment/beta hardening.

The critical dependency is reliable catalogue and reader data before recommendation quality can be meaningfully evaluated. AI features follow the deterministic recommendation and data foundations, and the LLM remains outside final ranking and business-rule enforcement.

The target is a stable beta for approximately 20–30 users, with core reader workflows, explainable recommendations, goal-oriented journeys, testing, monitoring, and graceful failure behavior. The roadmap should be refined using actual implementation progress and beta evidence rather than speculative deadlines.
