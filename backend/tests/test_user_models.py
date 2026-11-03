import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.base import Base
from backend.db.models import (
    User,
    UserAuthIdentity,
    UserPreference,
    UserProfile,
)


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database and session for user model testing."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


class TestUserMetadataAndDefinitions:
    """Verify metadata registration, table names, and column definitions for user models."""

    def test_user_models_registered_in_metadata(self):
        expected_tables = {
            "users",
            "user_auth_identities",
            "user_profiles",
            "user_preferences",
        }
        assert expected_tables.issubset(set(Base.metadata.tables.keys()))

    def test_users_table_columns_and_constraints(self):
        table = Base.metadata.tables["users"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "email",
            "display_name",
            "password_hash",
            "email_verified_at",
            "status",
            "created_at",
            "updated_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["email"].nullable is False
        assert table.columns["display_name"].nullable is False
        assert table.columns["password_hash"].nullable is True
        assert table.columns["email_verified_at"].nullable is True
        assert table.columns["status"].nullable is False
        assert table.columns["created_at"].nullable is False
        assert table.columns["updated_at"].nullable is False

        # Email unique index
        index_names = {idx.name for idx in table.indexes if "email" in [c.name for c in idx.columns]}
        assert len(index_names) >= 1

    def test_user_auth_identities_columns_and_constraints(self):
        table = Base.metadata.tables["user_auth_identities"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "user_id",
            "provider",
            "provider_subject",
            "provider_email",
            "created_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["user_id"].nullable is False
        assert table.columns["provider"].nullable is False
        assert table.columns["provider_subject"].nullable is False
        assert table.columns["provider_email"].nullable is True
        assert table.columns["created_at"].nullable is False

        # Foreign key to users.id with CASCADE
        fks = list(table.columns["user_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "users.id"
        assert fks[0].ondelete == "CASCADE"

        # Unique constraint on (provider, provider_subject)
        unique_constraints = [
            tuple(col.name for col in uc.columns)
            for uc in table.constraints
            if hasattr(uc, "columns") and uc != table.primary_key
        ]
        assert ("provider", "provider_subject") in unique_constraints

    def test_user_profiles_columns_and_constraints(self):
        table = Base.metadata.tables["user_profiles"]
        col_names = {c.name for c in table.columns}
        expected = {
            "user_id",
            "reader_level",
            "preferred_language",
            "bio",
            "created_at",
            "updated_at",
        }
        assert col_names == expected
        # user_id is both primary key and foreign key
        assert table.columns["user_id"].primary_key is True
        fks = list(table.columns["user_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "users.id"
        assert fks[0].ondelete == "CASCADE"

        assert table.columns["reader_level"].nullable is False
        assert table.columns["preferred_language"].nullable is False
        assert table.columns["bio"].nullable is True
        assert table.columns["created_at"].nullable is False
        assert table.columns["updated_at"].nullable is False

    def test_user_preferences_columns_and_constraints(self):
        table = Base.metadata.tables["user_preferences"]
        col_names = {c.name for c in table.columns}
        expected = {
            "id",
            "user_id",
            "preference_type",
            "preference_value",
            "preference_weight",
            "source",
            "created_at",
            "updated_at",
        }
        assert col_names == expected
        assert table.columns["id"].primary_key is True
        assert table.columns["user_id"].nullable is False
        assert table.columns["preference_type"].nullable is False
        assert table.columns["preference_value"].nullable is False
        assert table.columns["preference_weight"].nullable is True
        assert table.columns["source"].nullable is False

        # Foreign key to users.id with CASCADE
        fks = list(table.columns["user_id"].foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "users.id"
        assert fks[0].ondelete == "CASCADE"

        # Unique constraint on (user_id, preference_type, preference_value)
        unique_constraints = [
            tuple(col.name for col in uc.columns)
            for uc in table.constraints
            if hasattr(uc, "columns") and uc != table.primary_key
        ]
        assert ("user_id", "preference_type", "preference_value") in unique_constraints

        # Check constraint on preference_weight
        check_names = {ck.name for ck in table.constraints if hasattr(ck, "sqltext")}
        assert "ck_user_preferences_preference_weight" in check_names


class TestUserORMAndRelationships:
    """Verify ORM operations, relationships, and cascades for user models."""

    def test_uuid_primary_key_generation(self, db_session: Session):
        user = User(email="reader@example.com", display_name="Reader")
        profile = UserProfile(user=user)
        identity = UserAuthIdentity(
            user=user,
            provider="google",
            provider_subject="google-sub-12345",
            provider_email="reader@example.com",
        )
        pref = UserPreference(
            user=user,
            preference_type="genre",
            preference_value="sci-fi",
        )
        db_session.add_all([user, profile, identity, pref])
        db_session.flush()

        assert isinstance(user.id, uuid.UUID)
        assert profile.user_id == user.id
        assert isinstance(identity.id, uuid.UUID)
        assert isinstance(pref.id, uuid.UUID)
        db_session.commit()

    def test_user_profile_relationship_and_cascade(self, db_session: Session):
        user = User(email="profile_test@example.com", display_name="Profile User")
        profile = UserProfile(
            user=user,
            reader_level="avid",
            preferred_language="en",
            bio="Lifelong bookworm.",
        )
        db_session.add(user)
        db_session.commit()

        reloaded_user = db_session.get(User, user.id)
        assert reloaded_user.profile is not None
        assert reloaded_user.profile.reader_level == "avid"
        assert reloaded_user.profile.preferred_language == "en"
        assert reloaded_user.profile.bio == "Lifelong bookworm."
        assert reloaded_user.profile.user.id == user.id

        # Deleting user cascades to UserProfile
        user_id = user.id
        db_session.delete(reloaded_user)
        db_session.commit()

        assert db_session.get(UserProfile, user_id) is None

    def test_user_auth_identities_relationship_and_cascade(self, db_session: Session):
        user = User(
            email="oauth_user@example.com",
            display_name="OAuth Reader",
            password_hash=None,  # OAuth-only user has null password_hash
        )
        google_id = UserAuthIdentity(
            user=user,
            provider="google",
            provider_subject="g-10101",
            provider_email="oauth_user@example.com",
        )
        password_id = UserAuthIdentity(
            user=user,
            provider="password",
            provider_subject="oauth_user@example.com",
        )
        db_session.add(user)
        db_session.commit()

        reloaded_user = db_session.get(User, user.id)
        assert len(reloaded_user.auth_identities) == 2
        providers = {ident.provider for ident in reloaded_user.auth_identities}
        assert providers == {"google", "password"}

        # Deleting user cascades to UserAuthIdentity
        google_ident_id = google_id.id
        db_session.delete(reloaded_user)
        db_session.commit()

        assert db_session.get(UserAuthIdentity, google_ident_id) is None

    def test_user_preferences_relationship_and_cascade(self, db_session: Session):
        user = User(email="pref_user@example.com", display_name="Pref User")
        pref1 = UserPreference(
            user=user,
            preference_type="genre",
            preference_value="historical-fiction",
            preference_weight=Decimal("0.850"),
            source="user_edit",
        )
        pref2 = UserPreference(
            user=user,
            preference_type="subject",
            preference_value="world-war-2",
            preference_weight=Decimal("0.900"),
            source="onboarding",
        )
        db_session.add(user)
        db_session.commit()

        reloaded_user = db_session.get(User, user.id)
        assert len(reloaded_user.preferences) == 2
        types = {p.preference_type for p in reloaded_user.preferences}
        assert types == {"genre", "subject"}

        pref1_id = pref1.id
        db_session.delete(reloaded_user)
        db_session.commit()

        assert db_session.get(UserPreference, pref1_id) is None

    def test_duplicate_email_rejected(self, db_session: Session):
        u1 = User(email="duplicate@example.com", display_name="User 1")
        db_session.add(u1)
        db_session.commit()

        engine = db_session.bind
        with Session(engine) as session2:
            u2 = User(email="duplicate@example.com", display_name="User 2")
            session2.add(u2)
            with pytest.raises(IntegrityError):
                session2.commit()

    def test_duplicate_auth_identity_rejected(self, db_session: Session):
        u1 = User(email="user1@example.com", display_name="User 1")
        u2 = User(email="user2@example.com", display_name="User 2")
        ident1 = UserAuthIdentity(
            user=u1,
            provider="google",
            provider_subject="common-google-sub",
        )
        db_session.add_all([u1, u2, ident1])
        db_session.commit()

        engine = db_session.bind
        with Session(engine) as session2:
            ident2 = UserAuthIdentity(
                user_id=u2.id,
                provider="google",
                provider_subject="common-google-sub",
            )
            session2.add(ident2)
            with pytest.raises(IntegrityError):
                session2.commit()

    def test_duplicate_user_preference_rejected(self, db_session: Session):
        user = User(email="dup_pref@example.com", display_name="User")
        pref1 = UserPreference(
            user=user,
            preference_type="genre",
            preference_value="mystery",
        )
        db_session.add_all([user, pref1])
        db_session.commit()

        engine = db_session.bind
        with Session(engine) as session2:
            pref2 = UserPreference(
                user_id=user.id,
                preference_type="genre",
                preference_value="mystery",
            )
            session2.add(pref2)
            with pytest.raises(IntegrityError):
                session2.commit()

    def test_security_repr_does_not_leak_password_hash(self):
        user = User(
            id=uuid.uuid4(),
            email="secret@example.com",
            display_name="Secret",
            password_hash="$2b$12$e80yqX394e1uK9SecretSaltAndHashValue",
        )
        user_repr = repr(user)
        assert "secret@example.com" not in user_repr
        assert "SecretSaltAndHashValue" not in user_repr
        assert "password" not in user_repr.lower()

    def test_model_reprs(self):
        uid = uuid.uuid4()
        user = User(id=uid, email="test@example.com", display_name="Tester", status="active")
        assert str(uid) in repr(user)
        assert "test@example.com" not in repr(user)
        assert "active" in repr(user)

        ident = UserAuthIdentity(
            id=uuid.uuid4(),
            user_id=uid,
            provider="google",
            provider_subject="sub123",
        )
        assert "google" in repr(ident)
        assert "sub123" not in repr(ident)
        assert str(uid) in repr(ident)

        profile = UserProfile(
            user_id=uid,
            reader_level="avid",
            preferred_language="en",
        )
        assert "avid" in repr(profile)
        assert "en" in repr(profile)

        pref = UserPreference(
            id=uuid.uuid4(),
            user_id=uid,
            preference_type="genre",
            preference_value="fantasy",
        )
        assert "genre" in repr(pref)
        assert "fantasy" not in repr(pref)
