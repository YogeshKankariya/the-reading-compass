# API Specification — The Reading Compass

**Document version:** 1.1  
**Status:** Finalized for MVP API design  
**Last updated:** 2026-11-01

---

## 1. Purpose

This document defines the REST API contract for **The Reading Compass**. It describes how the React frontend communicates with the FastAPI backend and how backend services expose account, catalogue, library, recommendation, and reading-journey functionality.

The API is designed around the approved MVP architecture:

- React + Tailwind CSS frontend.
- Python + FastAPI backend.
- PostgreSQL as the primary database.
- pgvector for semantic retrieval.
- PostgreSQL full-text and trigram search.
- A modular-monolith backend with clearly separated service modules.
- An LLM integration for selected natural-language tasks, not for authoritative catalogue data or final recommendation ranking.

This document is a contract proposal. Exact route names and payloads can be adjusted during implementation, but changes should be reflected here and covered by API tests.

## 2. Confirmed API design decisions

The following choices were confirmed during review and are the approved MVP API defaults:

| Area | Final decision | Implementation direction |
|---|---|---|
| Authentication | Bearer access token + secure HttpOnly refresh cookie | Send access tokens in the `Authorization` header; keep refresh credentials in a Secure, HttpOnly, SameSite cookie with rotation and CSRF protection appropriate to the deployment |
| Pagination | Mixed pagination | Use cursor pagination for user library/activity and other changing collections; use offset pagination for catalogue browsing where it is convenient |
| TBR prioritization | Reuse `POST /recommendations` | Set `request_type` to `tbr_priority`; enforce server-side that candidates come only from the user's TBR |
| Recommendation feedback | Generic feedback endpoint | Use `POST /recommendations/{request_id}/feedback` with a validated feedback value and optional reason |
| Journey generation | Synchronous for MVP | Create and return the journey in the request-response cycle, with strict timeouts and bounded work |
| Recommendation scores | Expose final score and component breakdown | Return score details for transparency/debugging, while making clear that scores are ranking signals, not probabilities or book-quality ratings |

These decisions finalize the API design direction. Security-sensitive authentication details still require careful implementation and testing; the choice of token transport does not by itself guarantee a secure session.

## 2. API design principles

1. Use REST conventions and resource-oriented URLs.
2. Use JSON for request and response bodies, except OAuth redirects and supported upload flows.
3. Use UUIDs as public resource identifiers.
4. Use ISO 8601 timestamps in UTC.
5. Validate all input at the API boundary with Pydantic schemas.
6. Enforce authentication and ownership checks for private resources.
7. Keep recommendation ranking logic inside backend services.
8. Return stable error formats and meaningful HTTP status codes.
9. Use pagination for potentially large collections.
10. Never expose password hashes, OAuth tokens, internal prompts, or private profile calculations.
11. Version the API under `/api/v1`.
12. Keep external book-provider APIs behind backend adapters; the frontend should not depend directly on provider-specific response formats.

## 3. Base URL and versioning

Local development:

```text
http://localhost:8000/api/v1
```

Production:

```text
https://<api-domain>/api/v1
```

The production hostname will be selected during deployment. Do not hardcode it into frontend source; use an environment variable such as `VITE_API_BASE_URL`.

Use a new API version for breaking contract changes. Additive optional fields generally do not require a new version.

## 4. Authentication and authorization

### 4.1 Authentication methods

The MVP supports:

- Email/password registration and login.
- Google OAuth sign-in.
- Access-token-based authentication for protected API requests.

The exact OAuth flow should use a trusted OAuth library/provider SDK and validate provider tokens server-side. Never trust a user ID or email supplied by the browser without verifying the authentication token.

### 4.2 Bearer access token + secure refresh cookie

Protected API requests send:

```http
Authorization: Bearer <access_token>
```

The short-lived access token is held by the frontend in memory where practical and attached to protected API requests. The refresh credential is stored in a `Secure`, `HttpOnly`, appropriately `SameSite` cookie and is never exposed to frontend JavaScript.

The backend validates the access token, resolves the user identity, and places the authenticated user context on the request.

Production requirements:

- Use short-lived access tokens.
- Rotate refresh tokens on use and detect reuse where supported.
- Store refresh-token hashes or session identifiers server-side so sessions can be revoked.
- Apply CSRF protection to cookie-authenticated refresh/logout endpoints as required by the chosen cookie and origin configuration.
- Set explicit allowed origins and credential behavior in CORS.
- Never store long-lived refresh tokens in browser local storage.
- Define logout, expiry, revocation, and multi-device session behavior.

### 4.3 Authorization rules

- Public endpoints may be accessed without a session.
- A user can read and modify only their own profile, preferences, library, interactions, recommendation history, and journeys.
- Catalogue reads may be public; catalogue writes are restricted to trusted ingestion/admin processes.
- Admin endpoints must use a separate role/permission check.
- Ownership must be checked on the server for every private resource ID.
- Never rely on a frontend-hidden button as an authorization control.

### 4.4 Authentication endpoints

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/register` | Public | Create email/password account |
| POST | `/auth/login` | Public | Authenticate with email/password |
| POST | `/auth/refresh` | Refresh credential | Issue a new access token |
| POST | `/auth/logout` | Authenticated | Revoke/clear session |
| GET | `/auth/google` | Public | Begin Google OAuth flow |
| GET | `/auth/google/callback` | OAuth state | Complete Google OAuth flow |
| GET | `/auth/me` | Authenticated | Return current user summary |

Example registration request:

```json
{
  "email": "reader@example.com",
  "password": "strong-password",
  "display_name": "Alex"
}
```

Example current-user response:

```json
{
  "id": "8a3d7a6e-4a2a-4a3e-8d55-7e55c1a0d001",
  "email": "reader@example.com",
  "display_name": "Alex",
  "email_verified": false
}
```

The example IDs and user details are illustrative only.

## 5. Common request and response conventions

### 5.1 Content type

Requests with JSON bodies use:

```http
Content-Type: application/json
Accept: application/json
```

### 5.2 Pagination

The MVP uses a deliberate mixed strategy:

- **Cursor pagination** for user library, interactions/activity, recommendation history, and other collections that can change frequently.
- **Offset pagination** for catalogue browsing and search results where page-number navigation is useful and the result set is reasonably stable.

Cursor example:

```http
GET /library?status=want_to_read&limit=20&cursor=<opaque-cursor>
```

Cursor response:

```json
{
  "items": [],
  "page": {
    "limit": 20,
    "next_cursor": null,
    "has_more": false
  }
}
```

Offset example:

```http
GET /books?limit=20&offset=40
```

Offset response:

```json
{
  "items": [],
  "page": {
    "limit": 20,
    "offset": 40,
    "total": 125,
    "has_more": true
  }
}
```

Keep pagination consistent within each resource family. Validate maximum limits and use a stable sort order; offset pagination can skip or repeat items when catalogue records change between requests.

### 5.3 Sorting and filtering

Use documented query parameters, for example:

- `status`
- `query`
- `genre_id`
- `subject_id`
- `language`
- `sort`
- `limit`
- `cursor`

Validate allowed sort fields and directions. Never interpolate untrusted sort expressions directly into SQL.

### 5.4 Standard error format

Use a consistent error envelope:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The request contains invalid fields.",
    "details": [
      {
        "field": "rating",
        "issue": "Must be between 1 and 5."
      }
    ],
    "request_id": "req_01JEXAMPLE"
  }
}
```

Do not expose stack traces, SQL details, secrets, or provider credentials.

## 6. Endpoint overview

| Domain | Main endpoints |
|---|---|
| Authentication | `/auth/*` |
| User profile | `/users/me`, `/users/me/preferences` |
| Catalogue | `/books`, `/books/{work_id}`, `/authors`, `/genres`, `/subjects` |
| Search | `/search` |
| Personal library | `/library`, `/library/{entry_id}` |
| Reading goals | `/goals`, `/goals/{goal_id}` |
| Interactions | `/interactions` |
| Recommendations | `/recommendations`, `/recommendations/{request_id}` |
| Feedback | `/recommendations/{request_id}/feedback` |
| Reading journeys | `/journeys`, `/journeys/{journey_id}`, nested stage actions |
| Health | `/health`, `/ready` |

## 7. User profile and preferences

### 7.1 Get current profile

```http
GET /users/me
```

Response:

```json
{
  "id": "8a3d7a6e-4a2a-4a3e-8d55-7e55c1a0d001",
  "display_name": "Alex",
  "reader_level": "beginner",
  "preferred_language": "en",
  "created_at": "2026-11-01T10:00:00Z"
}
```

### 7.2 Update current profile

```http
PATCH /users/me
```

Request:

```json
{
  "display_name": "Alex",
  "reader_level": "casual",
  "preferred_language": "en"
}
```

Only explicitly supported editable fields should be accepted. Ignore no unknown fields silently; reject them or apply a documented forward-compatibility policy.

### 7.3 Get preferences

```http
GET /users/me/preferences
```

Response:

```json
{
  "items": [
    {
      "id": "preference-uuid",
      "type": "subject",
      "value": "psychology",
      "weight": 0.9,
      "source": "onboarding"
    }
  ]
}
```

### 7.4 Replace or update preferences

```http
PUT /users/me/preferences
```

Request:

```json
{
  "items": [
    {
      "type": "subject",
      "value": "psychology",
      "weight": 0.9
    },
    {
      "type": "genre",
      "value": "nonfiction",
      "weight": 0.8
    }
  ]
}
```

Use `PUT` when the request replaces the full preference set. If partial mutation is required, define a separate `PATCH` or item-level endpoint. Preference values must be validated against supported controlled vocabularies where applicable.

## 8. Book catalogue and search

### 8.1 List and filter books

```http
GET /books?genre_id=<uuid>&language=en&limit=20
```

Response:

```json
{
  "items": [
    {
      "id": "work-uuid",
      "title": "Example Book",
      "authors": [
        {
          "id": "author-uuid",
          "name": "Example Author"
        }
      ],
      "genres": ["Nonfiction"],
      "subjects": ["Psychology"],
      "language": "en",
      "cover_image_url": null,
      "metadata_status": "verified"
    }
  ],
  "page": {
    "limit": 20,
    "next_cursor": null,
    "has_more": false
  }
}
```

### 8.2 Get book details

```http
GET /books/{work_id}
```

Return work-level information and available edition summaries. Do not confuse work identity with edition-specific page count, ISBN, format, or publication date.

### 8.3 Search

```http
GET /search?q=behavioral%20economics&type=books&limit=10
```

Search may combine:

- PostgreSQL full-text search.
- pg_trgm fuzzy matching.
- Semantic retrieval where useful.
- Structured filters.

Example response:

```json
{
  "query": "behavioral economics",
  "items": [
    {
      "work": {
        "id": "work-uuid",
        "title": "Example Book",
        "authors": ["Example Author"]
      },
      "match_type": ["semantic", "full_text"],
      "relevance": 0.91
    }
  ]
}
```

The relevance value is an internal search score, not a public quality rating. Do not imply that search relevance is a book's objective quality.

### 8.4 Catalogue taxonomy

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/authors` | Search/list authors |
| GET | `/authors/{author_id}` | Author details and works |
| GET | `/genres` | Available genres |
| GET | `/subjects` | Available subjects |
| GET | `/subjects/{subject_id}` | Subject details and relationships |

Catalogue write endpoints are intentionally excluded from the public MVP API. Use protected ingestion/admin workflows.

## 9. Personal library

### 9.1 List library entries

```http
GET /library?status=currently_reading&limit=20
```

### 9.2 Add a book to the library

```http
POST /library
```

Request:

```json
{
  "work_id": "work-uuid",
  "edition_id": "edition-uuid",
  "status": "want_to_read"
}
```

`edition_id` is optional. If supplied, it must belong to the selected `work_id`.

Response: `201 Created` with the created library entry.

### 9.3 Get one library entry

```http
GET /library/{entry_id}
```

### 9.4 Update status, rating, or progress

```http
PATCH /library/{entry_id}
```

Request:

```json
{
  "status": "currently_reading",
  "current_page": 75,
  "total_pages": 300,
  "progress_percent": 25.0,
  "rating": null
}
```

Validation:

- Rating must be between 1.0 and 5.0 or null.
- Progress must be between 0 and 100.
- Page values must be non-negative.
- Current page cannot exceed total pages.
- When page values are supplied, the backend should calculate or validate the percentage.
- A rating should generally be accepted when the user has a meaningful basis for rating; the exact product rule can be finalized separately.

### 9.5 Remove a library entry

```http
DELETE /library/{entry_id}
```

Return `204 No Content` after successful deletion. Deleting a user's library entry must not delete the shared catalogue work.

## 10. Reading goals

### 10.1 Create a goal

```http
POST /goals
```

Request:

```json
{
  "title": "Understand personal finance",
  "description": "Build a beginner-level understanding of budgeting and investing.",
  "goal_type": "learn_subject",
  "target_date": null
}
```

### 10.2 List goals

```http
GET /goals?status=active
```

### 10.3 Get, update, and delete a goal

```http
GET /goals/{goal_id}
PATCH /goals/{goal_id}
DELETE /goals/{goal_id}
```

Deletion behavior for linked journeys must be explicit. Prefer soft deletion or a documented nullification policy where historical journey records need to remain available.

## 11. Recommendations

### 11.1 Generate recommendations

```http
POST /recommendations
```

Request:

```json
{
  "request_type": "next_read",
  "context": {
    "goal_id": "goal-uuid",
    "mood": null,
    "query": "Something practical about decision-making"
  },
  "filters": {
    "language": ["en"],
    "max_pages": 400,
    "difficulty": ["beginner", "intermediate"]
  },
  "limit": 10
}
```

Supported request types for the MVP:

- `next_read`
- `discover`
- `tbr_priority`
- `goal_based`
- `mood_based`
- `new_author`

The backend validates the request, loads the reader profile, generates candidates (wired internally via FastAPI dependency injection without a separate public candidate endpoint), applies filters, scores and ranks candidates, diversifies the list, creates reason codes, and persists the result. When evaluating `filters`, `language` matches `BookWork.original_language`, `max_pages` is edition-aware via `BookEdition.page_count`, and missing metadata does not exclude works unless the corresponding filter is explicitly requested.

### 11.2 Recommendation response

```json
{
  "request_id": "recommendation-request-uuid",
  "algorithm_version": "hybrid-v1",
  "items": [
    {
      "rank": 1,
      "work": {
        "id": "work-uuid",
        "title": "Example Book",
        "authors": ["Example Author"],
        "cover_image_url": null
      },
      "score": 0.8732,
      "score_breakdown": {
        "semantic": 0.91,
        "goal": 0.88,
        "topic": 0.84,
        "author": 0.70,
        "difficulty": 0.95,
        "novelty": 0.82
      },
      "reasons": [
        {
          "code": "MATCHES_CURRENT_GOAL",
          "label": "Matches your current learning goal"
        },
        {
          "code": "BEGINNER_FRIENDLY",
          "label": "Suitable for your selected reading level"
        }
      ]
    }
  ]
}
```

The MVP exposes the final ranking score and component breakdown. These are normalized ranking signals for transparency and debugging, not probabilities, confidence estimates, or objective book-quality ratings. The frontend should present human-readable explanations as the primary user-facing rationale; raw component scores may be kept in an expandable details view or developer mode.

### 11.3 Retrieve a previous recommendation run

```http
GET /recommendations/{request_id}
```

Only the owner of the request may retrieve it.

### 11.4 TBR prioritization

TBR prioritization reuses `POST /recommendations` with:

```json
{
  "request_type": "tbr_priority",
  "context": {
    "goal_id": "goal-uuid"
  },
  "limit": 10
}
```

For this request type, the backend must restrict candidates to the authenticated user's existing `want_to_read` entries. It must never silently add catalogue works outside the TBR list. If the user has no eligible TBR entries, return a valid empty result with a helpful message or documented result metadata.

## 12. Recommendation feedback and interactions

### 12.1 Submit feedback

```http
POST /recommendations/{request_id}/feedback
```

Request:

```json
{
  "work_id": "work-uuid",
  "feedback": "helpful",
  "reason": "Clear explanation and matched my reading level."
}
```

Request fields:
- `work_id`: UUID (required) — Identifies the recommended book work.
- `feedback`: string (required) — Selected feedback action (`"helpful" | "not_helpful" | "save" | "dismiss"`).
- `reason`: string or null (optional) — User-supplied explanation or context; may be omitted or null.

Supported feedback values:

- `helpful`
- `not_helpful`
- `save`
- `dismiss`

Validation and persistence behavior:
1. Validates that the work was included in the specified recommendation request (`recommendation_items`).
2. Updates the latest feedback state directly on `recommendation_items`:
   - `feedback_type`: `"helpful" | "not_helpful" | "save" | "dismiss"`
   - `feedback_reason`: optional user explanation (stored as null if omitted)
   - `feedback_at`: submission timestamp in UTC
3. Emits a corresponding immutable event into `user_interactions` (preserving the complete append-only event stream):
   - `"helpful"` &rarr; `interaction_type = "recommendation_helpful"`
   - `"not_helpful"` &rarr; `interaction_type = "recommendation_not_helpful"`
   - `"save"` &rarr; `interaction_type = "save"`
   - `"dismiss"` &rarr; `interaction_type = "dismiss"`
   - `context`: `{ "recommendation_request_id": "...", "reason": "..." }`
This dual architecture provides fast, direct access to the latest feedback status on `recommendation_items` for quality metrics and evaluations while preserving an append-only event stream in `user_interactions` to drive dynamic profile evolution.

### 12.2 Record an interaction

```http
POST /interactions
```

Request:

```json
{
  "work_id": "work-uuid",
  "interaction_type": "click",
  "context": {
    "source": "recommendation",
    "recommendation_request_id": "recommendation-request-uuid"
  }
}
```

The backend should validate interaction types and avoid trusting arbitrary client-supplied context. For important events such as progress, rating, or completion, prefer the library resource endpoints as the authoritative mutation path and create corresponding interaction events server-side.

## 13. Reading journeys

### 13.1 Generate a journey

```http
POST /journeys
```

Request:

```json
{
  "goal_id": "goal-uuid",
  "title": "Personal Finance Foundations",
  "reader_level": "beginner",
  "language": "en",
  "constraints": {
    "max_books_per_stage": 3
  }
}
```

Journey generation is synchronous for the MVP: the request returns the created journey and its stages in the same response. Keep generation bounded with request timeouts, maximum topic/stage/book counts, and graceful handling of an unavailable LLM or catalogue dependency. The backend may use an LLM to interpret the goal into structured topics, but it must validate the output against the subject catalogue. Subject prerequisite relationships and a DAG-based ordering process determine suggested stage order where reliable data exists. If generation cannot complete within the configured limit, return a documented retryable error; an asynchronous job model can be introduced later if real usage justifies it.

### 13.2 List journeys

```http
GET /journeys?status=active
```

### 13.3 Get journey details

```http
GET /journeys/{journey_id}
```

Return stages in order, their subject/topic, suggested books, status, and explanation.

### 13.4 Update journey

```http
PATCH /journeys/{journey_id}
```

Allow supported changes such as title, description, status, or user-edited stage order. Preserve a clear distinction between generated suggestions and user edits.

### 13.5 Update stage progress

```http
PATCH /journeys/{journey_id}/stages/{stage_id}
```

Request:

```json
{
  "status": "in_progress"
}
```

### 13.6 Regenerate or refresh suggestions

A future endpoint may allow a user to refresh book suggestions for a stage while preserving completed stages and user-pinned selections. This behavior should be designed before exposing it publicly.

## 14. Health and readiness endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Basic process liveness |
| GET | `/ready` | Readiness check including required dependencies |

`/health` should not reveal internal infrastructure details. `/ready` may check database connectivity and other critical dependencies but must not return credentials or sensitive connection information.

## 15. HTTP status codes

| Status | Use |
|---|---|
| 200 | Successful read or update |
| 201 | Resource created |
| 204 | Successful deletion with no response body |
| 400 | Invalid request semantics |
| 401 | Missing or invalid authentication |
| 403 | Authenticated but not authorized |
| 404 | Resource not found or not visible to caller |
| 409 | Conflict, such as duplicate library entry |
| 422 | Request validation failure |
| 429 | Rate limit exceeded |
| 500 | Unexpected server error |
| 503 | Required dependency temporarily unavailable |

For private resources, returning `404` rather than `403` may be appropriate where revealing resource existence would leak information. Apply one consistent policy.

## 16. Validation rules

At minimum, validate:

- Email format and password policy.
- UUID path and body fields.
- Supported enum values for statuses and request types.
- Rating range and configured step size.
- Page and percentage boundaries.
- Edition-to-work ownership consistency.
- Recommendation limit bounds.
- Maximum query and text lengths.
- Goal and journey ownership.
- Recommendation feedback membership.
- Preference values against controlled vocabularies where applicable.

Use Pydantic request and response models. Keep ORM/database models separate from public API schemas.

## 17. Rate limiting and abuse prevention

Apply sensible rate limits to:

- Registration and login.
- OAuth initiation/callback handling.
- Search requests.
- LLM-backed natural-language interpretation.
- Recommendation generation.
- Feedback and interaction ingestion.

Use stricter limits for expensive LLM or embedding operations. Add request timeouts, maximum input sizes, and cost controls. Do not rate-limit ordinary reads so aggressively that normal browsing becomes frustrating.

## 18. Security and privacy requirements

- Hash passwords with a modern password-hashing algorithm and appropriate parameters.
- Validate OAuth state and provider tokens server-side.
- Use secure transport (HTTPS) in production.
- Store secrets only in server-side environment/secret management.
- Apply ownership checks to all private resources.
- Use parameterized SQL/ORM queries.
- Sanitize or safely render user-provided text.
- Avoid logging access tokens, passwords, private notes, or raw sensitive prompts.
- Minimize personal data returned by endpoints.
- Define account deletion and retention behavior.
- Configure CORS with explicit allowed frontend origins.
- Protect cookie-based authentication against CSRF where applicable.

## 19. Backend module mapping

Suggested FastAPI router structure:

```text
backend/
└── app/
    ├── main.py
    ├── api/
    │   └── v1/
    │       ├── router.py
    │       └── endpoints/
    │           ├── auth.py
    │           ├── users.py
    │           ├── books.py
    │           ├── search.py
    │           ├── library.py
    │           ├── goals.py
    │           ├── recommendations.py
    │           ├── interactions.py
    │           ├── journeys.py
    │           └── health.py
    ├── schemas/
    ├── services/
    ├── repositories/
    ├── models/
    └── core/
```

This is a suggested organization, not a requirement to create every file immediately. Routers should remain thin: validate input, call services, and serialize responses. Business logic belongs in services; database access belongs in repositories or a consistent data-access layer.

## 20. API testing strategy

### 20.1 Unit tests

- Pydantic request/response validation.
- Service-level business rules.
- Permission and ownership policy helpers.
- Pagination parameter validation.
- Recommendation request-type validation.

### 20.2 Integration tests

- Registration/login and authenticated access.
- Google OAuth callback validation using test fixtures/mocks.
- CRUD operations for profile, preferences, library, and goals.
- Edition/work consistency.
- Search endpoint behavior.
- Recommendation generation and persistence.
- TBR-only candidate restriction.
- Feedback validation.
- Journey generation and stage updates.
- Account/resource ownership isolation.

### 20.3 Contract tests

- Validate response shapes against documented schemas.
- Test expected status codes.
- Test error envelope consistency.
- Verify optional fields and nullability.
- Ensure breaking changes are caught in CI.

### 20.4 Test environment

Use a dedicated test database. Never run destructive integration tests against production. Mock external book providers and LLM APIs in ordinary CI tests; add a small separately controlled smoke-test suite for real provider integration if required.

## 21. Implementation parameters to validate

The API design choices above are finalized. The following details remain implementation tasks:

1. Set access-token lifetime, refresh-token rotation, session revocation, and CSRF configuration.
2. Define maximum page sizes, stable sort keys, and cursor encoding.
3. Set recommendation score normalization and decide which score details appear in normal UI versus developer/debug views.
4. Set synchronous journey-generation timeouts and bounded output sizes; revisit asynchronous jobs if real usage requires them.
5. Align request and response schemas with the final database migrations and Pydantic models.
6. Generate OpenAPI documentation from implemented routes and compare it against this contract.

## 22. Review completion checklist

- [x] Authentication: bearer access token + secure HttpOnly refresh cookie
- [x] Pagination: cursor for user/activity collections; offset for catalogue browsing
- [x] TBR prioritization: reuse `POST /recommendations` with `request_type: "tbr_priority"`
- [x] Feedback: generic recommendation feedback endpoint
- [x] Journey generation: synchronous for MVP, with bounded execution
- [x] Recommendation scores: expose final score and component breakdown
- [x] API versioning and resource-oriented REST structure proposed
- [x] Authentication and authorization endpoints defined
- [x] Profile and preference endpoints defined
- [x] Catalogue and hybrid search endpoints defined
- [x] Personal library and progress endpoints defined
- [x] Goals, recommendations, feedback, and journey endpoints defined
- [x] Common error format and status codes defined
- [x] Security, privacy, rate limiting, and testing requirements documented
- [ ] Align final schemas with FastAPI/Pydantic implementation
- [ ] Generate OpenAPI documentation from implemented routes and compare against this contract

---

**Document owner:** The Reading Compass project  
**Related documents:** `PRD.md`, `SYSTEM_ARCHITECTURE.md`, `DATABASE_SCHEMA.md`, `RECOMMENDATION_ENGINE.md`  
