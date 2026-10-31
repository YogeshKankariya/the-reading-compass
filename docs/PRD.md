# Product Requirements Document (PRD)

## The Reading Compass

### AI-Powered Personal Reading Companion

**Document Version:** 1.0\
**Status:** Initial Draft -- Approved for Architecture Design\
**Product Type:** Full-stack AI-powered web application

------------------------------------------------------------------------

## 1. Product Overview

The Reading Compass is an AI-powered personalized reading platform
designed to help users discover books, organize their reading lists,
identify their next book, explore unfamiliar authors and subjects, and
develop structured reading journeys.

The platform combines recommendation algorithms, natural language
processing, user preference modelling and LLM-powered assistance to
provide recommendations that evolve with the reader.

### Product Vision

To become a personal reading companion that helps everyone discover what
to read, understand why to read it, and continuously expand their
knowledge through personalized reading experiences.

### Core Value Proposition

Move beyond conventional book recommendations by understanding the
reader's interests, goals, preferences, reading history and evolving
intellectual curiosity.

------------------------------------------------------------------------

## 2. Problem Statement

Readers frequently experience difficulties when deciding what to read.

The primary problems include:

1.  **Recommendation Overload:** Too many books and authors make
    choosing the next book difficult.
2.  **Unorganized Reading Lists:** Readers maintain large TBR lists
    without knowing which book to prioritize.
3.  **Lack of Personalization:** Conventional recommendations often rely
    heavily on popularity, genre or similarity.
4.  **Difficulty Discovering New Subjects:** Readers struggle to find
    suitable starting points when exploring unfamiliar topics.
5.  **Lack of Structured Learning:** Readers interested in learning a
    subject do not know which books to read first and how to progress.
6.  **Recommendation Repetition:** Recommendation systems can repeatedly
    suggest similar books without encouraging exploration.

The Reading Compass aims to address these problems through personalized
recommendations and structured reading journeys.

------------------------------------------------------------------------

## 3. Target Audience

The platform is intended for readers across different experience levels.

### Primary User Segments

**A. Beginner Readers** - Want to develop a reading habit. - Need
beginner-friendly recommendations. - Prefer accessible books and guided
reading paths.

**B. Casual Readers** - Read occasionally. - Want recommendations based
on interests and mood. - Need help selecting their next book.

**C. Regular and Avid Readers** - Maintain large reading lists. - Want
to discover new authors and genres. - Need intelligent TBR
prioritization.

**D. Knowledge-Oriented Readers** - Read for learning and personal
development. - Explore subjects such as entrepreneurship, economics,
psychology, philosophy, history and science. - Want structured reading
journeys.

User segments are descriptive, not fixed personality categories.

------------------------------------------------------------------------

## 4. Product Goals

### Primary Goals

1.  Provide genuinely personalized book recommendations.
2.  Help users decide what to read next.
3.  Generate structured reading journeys based on learning goals.
4.  Intelligently organize existing TBR lists.
5.  Encourage discovery of unfamiliar authors and subjects.
6.  Build an evolving reader profile using explicit preferences and
    behavioural feedback.

### Engineering Goals

1.  Implement a hybrid recommendation engine.
2.  Integrate semantic search and vector embeddings.
3.  Build a scalable and maintainable backend.
4.  Implement grounded LLM-powered assistance.
5.  Evaluate recommendation quality using established metrics.
6.  Establish automated testing and CI/CD.
7.  Deploy a production-accessible web application.

### Product Success Goals

-   Acquire an initial group of 20--30 beta users.
-   Measure recommendation relevance and user satisfaction.
-   Encourage users to return for subsequent recommendations.
-   Validate whether personalized reading journeys provide meaningful
    value.

------------------------------------------------------------------------

## 5. Functional Requirements

### FR-01: User Authentication

-   Users can register and log in.
-   Users can manage their profiles.
-   User data must be isolated and protected.
-   Support secure session management.

### FR-02: Reader Onboarding

Collect information including: - Reading experience. - Favourite genres
and subjects. - Favourite authors and books. - Reading objectives. -
Preferred writing style. - Preferred difficulty. - Preferred book
length. - Reading frequency. - Interest in exploring unfamiliar
subjects.

The onboarding system should allow users to update their preferences
later.

### FR-03: Book Discovery

-   Search books by title, author, genre and subject.
-   View book descriptions and metadata.
-   Discover books through categories.
-   View related books and authors.
-   Save books to the personal library.

### FR-04: Personalized Recommendations

-   Generate recommendations using reader profiles.
-   Consider reading history and preferences.
-   Consider current goals and context.
-   Display recommendation explanations.
-   Support feedback such as relevant, irrelevant and not interested.
-   Avoid recommending books already completed unless explicitly
    requested.

### FR-05: Personal Library

Support the following statuses: - Want to Read. - Currently Reading. -
Completed. - Abandoned.

Allow users to maintain personal ratings, reviews and notes.

### FR-06: TBR Intelligence

-   Accept books from the user's TBR list.
-   Rank books according to personal preferences and goals.
-   Consider difficulty and subject prerequisites.
-   Explain why a book should be prioritized.
-   Generate alternative reading sequences.

### FR-07: Reading Journey Generator

-   Accept a subject or learning goal.
-   Identify relevant subtopics.
-   Retrieve suitable books.
-   Organize books into progressive stages.
-   Consider difficulty and prerequisites.
-   Explain the purpose of each stage.
-   Track journey progress.

### FR-08: Reading Assistant

Provide natural-language interaction for: - Book discovery. - Book
comparisons. - Subject exploration. - Recommendation refinement. -
Reading journey assistance.

The assistant must use verified catalogue information and must not
fabricate bibliographic details.

### FR-09: Reading Insights

Provide: - Books completed. - Current reading progress. - Genre
distribution. - Author discovery. - Reading history. - Reading goals and
progress.

### FR-10: Recommendation Feedback

Capture: - Recommendation views. - Book clicks. - TBR additions. -
Ratings. - Rejections. - Completed books.

Use this information to improve personalization.

------------------------------------------------------------------------

## 6. Non-Functional Requirements

### Performance

-   Optimize common API responses.
-   Cache suitable frequently accessed data.
-   Avoid generating embeddings repeatedly.
-   Keep recommendation generation responsive.

### Security

-   Secure password storage.
-   Authentication and authorization.
-   Input validation.
-   Rate limiting.
-   Secure secret management.
-   Protection against common web vulnerabilities.

### Scalability

-   Modular backend architecture.
-   Efficient database queries.
-   Background processing for expensive operations when required.
-   Replaceable AI and embedding providers.

### Maintainability

-   Modular source code.
-   Documented APIs.
-   Automated tests.
-   Database migrations.
-   Consistent logging and error handling.

### Reliability

-   Graceful handling of unavailable external APIs.
-   Validated LLM outputs.
-   Database backup strategy.
-   Monitoring and error reporting.

------------------------------------------------------------------------

## 7. Recommendation Engine Requirements

The recommendation engine is a core intellectual component of the
product.

It should progressively support:

1.  Content-based filtering.
2.  Semantic similarity using embeddings.
3.  User preference modelling.
4.  Context-aware ranking.
5.  Collaborative filtering when sufficient interaction data becomes
    available.
6.  Diversity-aware recommendations.

### Recommendation Signals

-   Content similarity.
-   Goal relevance.
-   Reading difficulty.
-   Writing-style preferences.
-   User ratings.
-   Previous interactions.
-   Exploration preference.

### Evaluation Metrics

-   Precision@K.
-   Recall@K.
-   NDCG@K.
-   Catalog Coverage.
-   Recommendation Diversity.
-   User Acceptance Rate.

The initial implementation must establish a baseline before introducing
more sophisticated algorithms.

------------------------------------------------------------------------

## 8. Reading Journey Requirements

Reading journeys should represent progressive learning experiences.

The system must:

1.  Interpret the user's learning objective.
2.  Identify relevant subject areas.
3.  Identify prerequisite concepts.
4.  Retrieve suitable books.
5.  Rank books based on suitability.
6.  Generate a logical reading sequence.
7.  Explain why each book is included.
8.  Allow users to modify their journeys.

A knowledge graph or graph-based representation may be used to model
relationships between concepts, subjects and books.

------------------------------------------------------------------------

## 9. MVP Scope

### Included in Version 1.0

-   Authentication.
-   Reader onboarding.
-   Book catalogue and search.
-   Personal library.
-   TBR management.
-   Content-based recommendation engine.
-   Embedding-based semantic search.
-   Context-aware ranking.
-   Recommendation explanations.
-   TBR reading order.
-   Goal-based reading journeys.
-   Basic feedback collection.
-   Deployment and monitoring.

### Deferred to Future Versions

-   Advanced collaborative filtering.
-   Conversational reading assistant.
-   Advanced reading analytics.
-   Social reading communities.
-   Book clubs and shared reading lists.
-   Advanced knowledge graph exploration.
-   Mobile applications.
-   External bookstore purchasing integrations.

------------------------------------------------------------------------

## 10. External Integrations

Initial integrations to investigate:

-   Google Books API -- Book discovery and metadata.
-   Open Library API -- Book, author and edition metadata.
-   Sentence Transformers -- Text embeddings.
-   LLM provider -- Natural-language understanding and recommendation
    explanations.

All integrations must be abstracted so providers can be replaced.

------------------------------------------------------------------------

## 11. Proposed Technology Stack

  Layer                 Technology
  --------------------- -------------------------------
  Frontend              React + Tailwind CSS
  Backend               Python + FastAPI
  Database              PostgreSQL
  Vector Search         pgvector
  Machine Learning      scikit-learn
  Embeddings            Sentence Transformers
  LLM                   Gemini / Gemma or alternative
  Cache                 Redis (optional initially)
  Testing               Pytest
  Version Control       Git + GitHub
  CI/CD                 GitHub Actions
  Frontend Deployment   Vercel
  Backend Deployment    Render / Railway

------------------------------------------------------------------------

## 12. Success Criteria

### Initial Beta Targets

-   20--30 initial users.
-   Successful onboarding and profile creation.
-   Successful book search and library management.
-   Recommendations generated without relying solely on an LLM.
-   Users can create and follow reading journeys.
-   Feedback can be recorded and analyzed.
-   Recommendation quality can be evaluated against a baseline.

These are initial product targets, not achieved results.

------------------------------------------------------------------------

## 13. Product Principles

1.  Personalization over popularity.
2.  Explainable recommendations over black-box results.
3.  Discovery beyond existing interests.
4.  Verified book information over generated assumptions.
5.  Learning progression over random book collections.
6.  User feedback over assumptions about preferences.
7.  Useful functionality over unnecessary AI features.

------------------------------------------------------------------------

## 14. Future Product Direction

Potential future capabilities:

-   Adaptive learning journeys.
-   Personalized author discovery.
-   Cross-domain reading recommendations.
-   Reading companion with conversational memory.
-   Knowledge maps generated from reading history.
-   Book notes and knowledge management.
-   Community-driven recommendation signals.

------------------------------------------------------------------------

## 15. Document Status

**Current Status:** Initial PRD draft approved for architecture design.

This PRD is the primary reference for product scope and functional
expectations.
