# Testing Strategy

**Project:** The Reading Compass  
**Document version:** 1.1  
**Status:** Finalized for MVP testing  
**Last updated:** 1 November 2026

---

## 1. Purpose

This document defines how The Reading Compass will be tested throughout development and before beta release. It covers backend logic, API behavior, database operations, recommendation quality, AI integration, frontend behavior, security, performance, and continuous integration.

The goal is to catch defects early, protect important product behavior, and ensure recommendations are useful, explainable, and reliable.

Testing should be practical for an individual or small student team. The MVP should have a strong automated test foundation without creating an unnecessarily complex testing infrastructure.


---

## 2. Confirmed testing decisions

The following choices are confirmed for the MVP.

| Area | Confirmed decision | MVP implementation |
|---|---|---|
| Backend testing | **Balanced coverage** | Aim for 75% overall backend coverage and 85% for critical business logic. |
| Frontend testing | **Component testing** | Test important React components, forms, and user interactions. |
| End-to-end testing | **Playwright** | Automate the highest-value user journeys. |
| Recommendation evaluation | **Small manually reviewed dataset** | Start with approximately 30–50 cases and evaluate relevance, ranking, diversity, and constraint violations. |
| GitHub Actions | **Separate workflows** | Keep backend, frontend, and deployment smoke tests in separate workflow files. |
| Deployment smoke tests | **Automatic after deployment** | Run basic health and API checks automatically after each deployment. |

These are practical MVP targets. Revisit them if test runtime, team size, product scope, or beta usage changes substantially.

---

## 3. Testing principles

1. **Test critical business logic first.** Recommendation ranking, eligibility, user data ownership, journey prerequisites, and reading progress must be reliable.
2. **Automate repeatable checks.** Unit tests, API tests, linting, type checks, and database checks should run consistently.
3. **Keep tests deterministic.** Mock external APIs and LLM calls in the normal CI test suite.
4. **Test failure paths.** Invalid input, unavailable providers, empty catalogues, and database errors matter as much as successful requests.
5. **Evaluate quality, not only correctness.** Recommendation relevance and AI groundedness need dedicated evaluation sets and human review.
6. **Test at multiple levels.** Unit, integration, API, end-to-end, and non-functional testing have different purposes.
7. **Protect user data.** Authorization and privacy tests are release-critical.
8. **Avoid premature infrastructure.** Start with pytest and GitHub Actions; add specialized tooling only when it provides clear value.

---

## 4. Testing scope

| Area | What must be tested |
|---|---|
| Backend services | Business rules, validation, error handling, service interactions |
| REST API | Request/response schemas, status codes, authentication, authorization, pagination |
| Database | Constraints, relationships, migrations, transactions, indexes, pgvector behavior |
| Recommendation engine | Candidate retrieval, scoring, filtering, diversity, TBR prioritization |
| Reading journeys | Topic mapping, prerequisites, sequence validity, persistence |
| AI integration | Structured output validation, grounded explanations, fallback behavior |
| Frontend | Component behavior, forms, navigation, loading/error/empty states |
| External integrations | Google Books/Open Library adapters, provider errors, rate limits |
| Security | Authentication, ownership checks, input validation, abuse controls |
| Performance | API latency, search latency, embedding inference, expected beta load |

---

## 5. Testing levels

### 4.1 Unit testing

Tests one function or class in isolation.

Examples:
- Recommendation score calculation.
- MMR/diversity selection.
- TBR prioritization rules.
- Reading progress calculations.
- Subject prerequisite validation.
- Input normalization.
- Pydantic schema validation.
- AI fallback selection.

**Primary tool:** `pytest`

Unit tests should be fast, deterministic, and independent of live services.

### 4.2 Integration testing

Tests whether multiple application components work together.

Examples:
- FastAPI service + PostgreSQL repository.
- User library update + interaction record creation.
- Recommendation request + candidate retrieval + ranking.
- Journey generation output + subject mapping + prerequisite validation.
- Local embedding generation + pgvector persistence and retrieval.

Use a dedicated test database or isolated test schema. Tests must not modify development or production data.

### 4.3 API testing

Tests the externally visible HTTP contract.

Examples:
- Correct response schema and status code.
- Missing or invalid access token.
- Access to another user's library or journey.
- Invalid pagination cursor.
- Invalid rating or progress values.
- Empty search results.
- Rate-limit behavior where implemented.

**Primary tools:** FastAPI `TestClient` or HTTPX with pytest.

### 4.4 End-to-end testing

Tests complete user workflows through the frontend and backend.

Priority MVP workflows:
1. Register or sign in.
2. Complete reader profile and preferences.
3. Search for a book.
4. Add a book to the TBR.
5. Request personalized recommendations.
6. Open a recommendation and understand why it was suggested.
7. Update reading progress and rating.
8. Generate and edit a reading journey.

End-to-end tests should cover the highest-value user journeys rather than every small UI variation.

**Selected tool:** Playwright.

Automate these high-value journeys first. Expand coverage only when workflows stabilize; the MVP does not need a browser test for every minor UI variation.

### 4.5 Manual exploratory testing

Manual testing remains useful for:
- Recommendation usefulness and diversity.
- Reading journey quality.
- Explanation clarity.
- Mobile/responsive layout.
- Accessibility and usability.
- Unexpected user behavior not captured by scripted tests.

Record reproducible defects with steps, expected behavior, actual behavior, and relevant logs/screenshots.

---

## 6. Backend testing strategy

### 5.1 Service and domain tests

Test core services independently of HTTP routes.

| Module | Important test cases |
|---|---|
| Authentication | Password validation, token/session behavior, refresh rotation, revoked session |
| User profile | Create, read, update, invalid preferences |
| Catalogue | Work/edition mapping, duplicate external IDs, missing metadata |
| Search | Exact match, partial match, typo tolerance, semantic fallback |
| User library | Add/remove book, valid status transitions, duplicate handling |
| Reading progress | Page bounds, percentage bounds, completed status |
| Goals | Valid target, dates, progress updates |
| Recommendations | Filters, score components, deterministic ranking, empty candidates |
| TBR priority | User-owned TBR only, priority ordering, exclusion rules |
| Journeys | Valid stages, prerequisite order, invalid subject references |
| Feedback | Valid event types, ownership, duplicate/idempotency behavior |

### 5.2 API route tests

For each endpoint, cover:
- Successful request.
- Missing required fields.
- Invalid field types or values.
- Unauthenticated request.
- Authenticated request without permission.
- Missing resource.
- Relevant conflict or duplicate case.
- Internal dependency failure.

Do not test only status codes. Assert meaningful response fields and that database state changes are correct.

### 5.3 Authentication and authorization

These tests are release-critical.

Verify:
- Passwords are never stored in plaintext.
- Access tokens are rejected when invalid or expired.
- Refresh-token rotation works as designed.
- Revoked sessions cannot refresh.
- Protected routes reject unauthenticated requests.
- A user cannot read or modify another user's profile, library, goals, recommendations, or journeys.
- Refresh-cookie and CSRF protections match the final API design.
- CORS allows only configured frontend origins.

---

## 7. Database testing strategy

Use PostgreSQL for integration tests because SQLite does not reproduce all PostgreSQL behavior, particularly pgvector, trigram search, and PostgreSQL-specific constraints.

Test:
- Migrations apply cleanly from an empty database.
- Constraints reject invalid references and values.
- Foreign-key relationships behave as expected.
- Unique constraints prevent duplicate identities and external IDs where required.
- Transactions roll back correctly after failure.
- Deleting or deactivating records follows intended retention rules.
- Search indexes and pgvector queries return valid results.
- Test fixtures are isolated and cleaned up.

The CI environment should provide a PostgreSQL service with the required extensions enabled, including `vector` and `pg_trgm`.

---

## 8. Recommendation engine evaluation

Recommendation quality is a central product capability and needs more than ordinary unit tests.

### 7.1 Unit and invariant tests

Test:
- Each score component is calculated correctly.
- Configured weights are applied consistently.
- Excluded topics and unavailable books are filtered.
- Books already owned or completed follow the product's defined rules.
- Diversity logic reduces excessive author/genre concentration.
- MMR selection is deterministic for fixed inputs and settings.
- TBR prioritization only considers the user's own TBR.
- Missing optional metadata does not crash ranking.
- Empty candidate sets return a valid empty result.

### 7.2 Offline evaluation dataset

Start with approximately **30–50 manually reviewed evaluation cases**. Each case should include representative reader profiles, goals, constraints, candidate books, and human relevance judgments. Cover different reader types, difficulty preferences, topics, and explicit exclusions; expand the dataset as catalogue and beta feedback grow.

Each case should include:
- Reader profile and stated interests.
- Goal or natural-language request.
- Relevant constraints and exclusions.
- Candidate books.
- Human relevance judgments or expected topic matches.

Start with a manageable set, then expand as catalogue and beta feedback grow.

### 7.3 Suggested evaluation metrics

| Metric | What it measures |
|---|---|
| Precision@K | How many top-K recommendations are relevant |
| Recall@K | How many known relevant items appear in top K |
| NDCG@K | Whether more relevant books appear higher in the ranking |
| Catalog coverage | How much of the catalogue receives exposure |
| Intra-list diversity | How different recommended books are from one another |
| Novelty | Whether recommendations introduce less obvious but relevant discoveries |
| Constraint violation rate | How often recommendations break explicit user constraints |

No single metric should define recommendation quality. A high relevance score is not sufficient if recommendations are repetitive or violate user constraints.

### 7.4 Baseline and iteration

The initial weighted scoring formula is a baseline hypothesis. Record its configuration and evaluate it before tuning weights.

For each change:
1. Run the same evaluation dataset.
2. Compare relevance and diversity metrics.
3. Inspect representative ranking changes.
4. Check for regressions in exclusions and constraints.
5. Document the reason for accepting or rejecting the change.

Do not tune solely against a tiny dataset or optimize metrics at the expense of actual reader usefulness.

---

## 9. AI integration testing

Normal CI tests must not depend on live LLM API calls.

### 8.1 Mocked provider tests

Mock the LLM provider to test:
- Valid structured intent output.
- Missing or malformed fields.
- Invalid subject or difficulty values.
- Timeout and provider error.
- Quota/rate-limit response.
- One bounded retry.
- Fallback to keyword/metadata search.
- Clarification when fallback results are not useful.

### 8.2 Recommendation explanation tests

Verify:
- Explanations use only supplied book metadata and reason codes.
- Unsupported claims are rejected or omitted.
- Explanations do not change recommendation order.
- A provider failure returns deterministic reason-code-based text.
- Empty or missing metadata is handled safely.

### 8.3 Journey generation tests

Verify:
- Output matches the expected schema.
- Topic labels map to controlled subject IDs.
- Invalid subject references are rejected.
- Prerequisites are validated by backend logic.
- The journey is returned as an editable draft.
- Failed generation does not persist a partial journey.

### 8.4 Embedding tests

Verify:
- Output dimension matches the configured 384 dimensions.
- Values are finite numbers.
- Empty or oversized input is handled.
- Batch output count matches input count.
- Model loading errors are handled.
- Stored vectors identify the model/revision that generated them.

### 8.5 Optional live-provider checks

A separate, manually triggered or scheduled integration test may call the real provider to detect configuration changes. It should:
- Use a dedicated test key and controlled prompt.
- Avoid real user data.
- Have strict token and cost limits.
- Be excluded from the default pull-request test suite.
- Never make ordinary CI success depend on provider availability.

---

## 10. Frontend testing strategy

Use a lightweight test setup for the React application.

**Selected approach:** Component testing with Vitest and React Testing Library.

Suggested tools:
- Vitest for test execution.
- React Testing Library for component behavior.
- Playwright for selected end-to-end workflows.

Test:
- Form validation and submission.
- Authentication state and protected routes.
- Recommendation cards and explanation display.
- Search results, pagination, and empty states.
- TBR add/remove interactions.
- Reading progress updates.
- Journey stage rendering and editing.
- Loading, error, and retry states.
- Responsive behavior on common viewport sizes.

Prefer testing visible user behavior rather than implementation details or internal component state.

---

## 11. External integration testing

External services include book catalogue providers and the selected LLM provider.

For routine tests:
- Mock HTTP responses.
- Test success, empty response, malformed response, timeout, rate limit, and server error.
- Verify provider-specific responses are normalized into internal schemas.
- Verify duplicate records and missing fields are handled safely.
- Ensure secrets are not included in logs or error responses.

Maintain a small optional integration suite for real catalogue-provider calls. Keep it separate from the deterministic CI suite.

---

## 12. Security and privacy testing

At minimum, test:
- Authentication and authorization on every protected resource family.
- Cross-user IDOR attempts.
- SQL injection and unsafe query construction.
- XSS-safe rendering of user-supplied and imported text.
- Input length and payload limits.
- Rate limiting for authentication and AI endpoints.
- Secure cookie flags and CSRF behavior.
- CORS configuration.
- Secret handling and accidental secret logging.
- Safe error responses that do not expose stack traces or internal configuration.
- Prompt injection attempts embedded in user text or retrieved catalogue descriptions.

Use dependency vulnerability scanning in CI where practical. Security tests should be repeated before beta deployment.

---

## 13. Performance and reliability testing

The first release should focus on realistic beta-scale performance rather than premature large-scale load testing.

Measure:
- Common API response latency.
- Catalogue search latency.
- Recommendation generation latency.
- Local embedding model load and inference time.
- PostgreSQL query performance.
- LLM task latency and timeout frequency.
- Behavior under concurrent requests.

Test:
- Empty and large result sets.
- Pagination over realistic data volumes.
- Concurrent updates to the same user's reading progress where relevant.
- Provider timeouts.
- Database connection failures.
- Recovery after temporary dependency failure.

Set concrete latency and concurrency targets after measuring the first working implementation and expected beta environment.

---

## 14. Test data and fixtures

Use synthetic or deliberately anonymized data.

Recommended fixture groups:
- A small catalogue with books, works, editions, authors, genres, and subjects.
- Subject prerequisite relationships, including valid and invalid sequences.
- Multiple users with separate libraries and preferences.
- TBR lists and reading progress examples.
- Goals, recommendations, and journeys.
- Mocked LLM responses for valid and invalid cases.
- Representative reader queries for recommendation evaluation.

Fixtures should be reusable, predictable, and isolated between tests. Never use real beta-user personal data in automated test fixtures.

---

## 15. Continuous integration with GitHub Actions

### 15.1 Workflow organization

Use separate GitHub Actions workflows for clarity and independent execution.

```text
.github/
└── workflows/
    ├── backend-tests.yml
    ├── frontend-tests.yml
    └── deployment-smoke-tests.yml
```

### 15.2 Backend workflow

On every pull request:
1. Check out repository.
2. Set up supported Python version.
3. Install backend dependencies.
4. Run formatting/lint checks.
5. Run static type checks if configured.
6. Run backend unit tests.
7. Start PostgreSQL with required extensions.
8. Apply migrations.
9. Run backend integration and API tests.
10. Upload test and coverage reports.

Frontend checks should run in the same workflow or a separate frontend workflow:
- Install dependencies.
- Run lint.
- Run type checking/build.
- Run component tests.

### 15.3 Frontend workflow

On every pull request and push to the main branch, install dependencies, run lint and type checks, build the application, and run Vitest/React Testing Library component tests.

### 15.4 Deployment smoke-test workflow

Run automatically after each successful deployment. Wait for the deployment to become available, call the health endpoint, verify database connectivity through a safe health check, and run non-destructive API smoke checks using a dedicated test account or test environment. Do not run destructive tests against production data.

### 15.5 Main branch workflow

On pushes to the main branch:
- Run the same required checks as pull requests.
- Build backend and frontend artifacts.
- Optionally run a small smoke-test suite against a test deployment.

### 15.6 Deployment smoke-test details

After deployment, verify:
- Health endpoint responds.
- Application can connect to the database.
- Authentication endpoint is reachable.
- Catalogue search returns a valid response.
- Recommendation endpoint responds with a valid schema.
- A basic AI feature responds or degrades gracefully when the provider is unavailable.

Smoke tests must use test accounts and non-destructive operations. Do not run destructive tests against production data.

### 14.4 Example workflow layout

```text
.github/
└── workflows/
    ├── backend-tests.yml
    ├── frontend-tests.yml
    └── deployment-smoke-tests.yml
```

The exact number of workflow files can be adjusted. A single well-organized workflow is acceptable initially; split workflows when ownership or runtime makes it useful.

---

## 16. Coverage and quality gates

Coverage is a signal, not the sole measure of quality.

Confirmed starting targets:
- **Backend overall:** aim for at least 75% line coverage as the codebase matures.
- **Critical business logic:** aim for at least 85% line coverage.
- API authorization paths: explicit tests for every protected resource family.
- Recommendation invariants: explicit tests for every major rule.
- AI output validation and fallback paths: explicit tests for success and failure cases.

Do not write low-value tests merely to increase coverage. Review untested critical paths even when aggregate coverage looks healthy.

A pull request should not merge if:
- Required automated tests fail.
- Migrations fail.
- Lint or type checks fail where configured as required.
- A critical security or authorization test fails.
- A change breaks a documented API contract without an approved update.

---

## 17. Defect management

For each defect, record:
- Short title.
- Environment and application version.
- Steps to reproduce.
- Expected result.
- Actual result.
- Severity and priority.
- Logs or screenshots with secrets and personal data removed.
- Related test or issue, if available.

Fix the defect and add a regression test where practical.

Suggested severity:
- **Critical:** data exposure, account compromise, data loss, or complete core workflow outage.
- **High:** major core feature unusable or important results materially incorrect.
- **Medium:** feature partially works with a workaround.
- **Low:** minor visual, copy, or usability issue.

---

## 18. MVP release readiness checklist

Before inviting beta users, verify:

- [ ] Backend unit and integration tests pass.
- [ ] API contract and authorization tests pass.
- [ ] Database migrations work on a clean database.
- [ ] Recommendation engine invariants pass.
- [ ] Recommendation evaluation set has been reviewed.
- [ ] AI schema validation and fallback tests pass.
- [ ] LLM provider is not required for non-AI core functionality.
- [ ] Frontend critical workflows have been tested.
- [ ] Deployment smoke tests pass.
- [ ] No known critical or high-severity security defects remain.
- [ ] Error logging and basic monitoring are enabled.
- [ ] Test accounts and beta data are separated from production data.
- [ ] Privacy and third-party AI processing disclosures are ready.
- [ ] Backup and recovery approach is documented.

---

## 19. Implementation sequence

### Phase 1 — Testing foundation
- Configure pytest.
- Create test settings and environment variables.
- Add reusable fixtures and factories.
- Set up isolated PostgreSQL integration testing.
- Add GitHub Actions backend workflow.
- Add formatting/lint checks and coverage reporting.

### Phase 2 — Core backend tests
- Test authentication and authorization.
- Test profile, catalogue, library, goals, and progress services.
- Test recommendation scoring and filtering.
- Test journey prerequisite validation.
- Test API request and response contracts.

### Phase 3 — AI and recommendation evaluation
- Add mocked LLM provider tests.
- Test schema validation and fallback behavior.
- Test local embedding dimensions and retrieval.
- Build a manually reviewed recommendation evaluation dataset.
- Add explanation and journey quality checks.

### Phase 4 — Frontend and release checks
- Add component tests.
- Add critical Playwright user journeys.
- Add deployment smoke tests.
- Run security and performance checks.
- Complete beta release checklist.

---

## 20. Final MVP testing summary

The Reading Compass will use pytest for backend unit, integration, and API tests, with PostgreSQL-backed integration testing. Backend coverage targets are 75% overall and 85% for critical business logic. Frontend testing will use Vitest and React Testing Library for important components and interactions. Playwright will automate the highest-value end-to-end user journeys.

Recommendation quality will be evaluated with an initial 30–50 case manually reviewed dataset using relevance, ranking, diversity, and constraint metrics. AI tests will mock provider calls in routine CI and explicitly verify validation, groundedness, and fallback behavior.

GitHub Actions will use separate backend, frontend, and deployment smoke-test workflows. Basic smoke tests will run automatically after deployment. The strategy prioritizes correctness, user-data protection, recommendation usefulness, and graceful degradation over unnecessary tooling or inflated coverage numbers.
