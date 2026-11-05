import math
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str

    # Profile generation: Embedding model configuration
    PROFILE_EMBEDDING_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"
    PROFILE_EMBEDDING_DIMENSION: int = 384
    PROFILE_EMBEDDING_MODEL_REVISION: str | None = None

    # Profile generation: Mathematical hyperparameters
    PROFILE_RECENCY_HALF_LIFE_DAYS: float = 180.0
    PROFILE_NEGATIVE_DISCOUNT_ALPHA: float = 0.25
    PROFILE_BLENDING_M0: float = 5.0
    PROFILE_NEAR_ZERO_EPSILON: float = 1e-6

    # Profile generation: Refresh triggers
    PROFILE_REFRESH_THRESHOLD: int = 5

    # Profile generation: Initial signal weights
    PROFILE_WEIGHT_RATING_5: float = 1.00
    PROFILE_WEIGHT_RATING_4: float = 0.75
    PROFILE_WEIGHT_RATING_3: float = 0.10
    PROFILE_WEIGHT_RATING_2: float = -0.40
    PROFILE_WEIGHT_RATING_1: float = -0.80
    PROFILE_WEIGHT_RECOMMENDATION_HELPFUL: float = 0.50
    PROFILE_WEIGHT_RECOMMENDATION_NOT_HELPFUL: float = -0.40
    PROFILE_WEIGHT_SAVE: float = 0.60
    PROFILE_WEIGHT_COMPLETE: float = 0.50
    PROFILE_WEIGHT_START: float = 0.30
    PROFILE_WEIGHT_PROGRESS: float = 0.25
    PROFILE_WEIGHT_ABANDON: float = -0.20
    PROFILE_WEIGHT_DISMISS: float = -0.15
    PROFILE_WEIGHT_RECOMMENDATION_CLICK: float = 0.15
    PROFILE_WEIGHT_CLICK: float = 0.10
    PROFILE_WEIGHT_VIEW: float = 0.05

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @field_validator("PROFILE_EMBEDDING_DIMENSION")
    @classmethod
    def validate_embedding_dimension(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("PROFILE_EMBEDDING_DIMENSION must be positive")
        return v

    @field_validator("PROFILE_RECENCY_HALF_LIFE_DAYS")
    @classmethod
    def validate_half_life(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError("PROFILE_RECENCY_HALF_LIFE_DAYS must be a finite positive number")
        return v

    @field_validator("PROFILE_BLENDING_M0")
    @classmethod
    def validate_m0(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError("PROFILE_BLENDING_M0 must be a finite positive number")
        return v

    @field_validator("PROFILE_NEAR_ZERO_EPSILON")
    @classmethod
    def validate_epsilon(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError("PROFILE_NEAR_ZERO_EPSILON must be a finite positive number")
        return v

    @field_validator("PROFILE_REFRESH_THRESHOLD")
    @classmethod
    def validate_refresh_threshold(cls, v: int) -> int:
        if v < 1:
            raise ValueError("PROFILE_REFRESH_THRESHOLD must be at least 1")
        return v

    @field_validator("PROFILE_NEGATIVE_DISCOUNT_ALPHA")
    @classmethod
    def validate_negative_discount_alpha(cls, v: float) -> float:
        if not math.isfinite(v) or not (0.0 <= v <= 1.0):
            raise ValueError("PROFILE_NEGATIVE_DISCOUNT_ALPHA must be a finite number between 0.0 and 1.0")
        return v

    @field_validator(
        "PROFILE_WEIGHT_RATING_5",
        "PROFILE_WEIGHT_RATING_4",
        "PROFILE_WEIGHT_RATING_3",
        "PROFILE_WEIGHT_RATING_2",
        "PROFILE_WEIGHT_RATING_1",
        "PROFILE_WEIGHT_RECOMMENDATION_HELPFUL",
        "PROFILE_WEIGHT_RECOMMENDATION_NOT_HELPFUL",
        "PROFILE_WEIGHT_SAVE",
        "PROFILE_WEIGHT_COMPLETE",
        "PROFILE_WEIGHT_START",
        "PROFILE_WEIGHT_PROGRESS",
        "PROFILE_WEIGHT_ABANDON",
        "PROFILE_WEIGHT_DISMISS",
        "PROFILE_WEIGHT_RECOMMENDATION_CLICK",
        "PROFILE_WEIGHT_CLICK",
        "PROFILE_WEIGHT_VIEW",
    )
    @classmethod
    def validate_signal_weights(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Signal weights must be finite numbers (not NaN or infinity)")
        return v

    @field_validator("PROFILE_EMBEDDING_MODEL_REVISION")
    @classmethod
    def validate_model_revision(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            return None
        return v

    def get_signal_weights(self) -> dict[str, float]:
        """Return a mapping of interaction signal identifiers to their configured weights."""
        return {
            "rate_5": self.PROFILE_WEIGHT_RATING_5,
            "rate_4": self.PROFILE_WEIGHT_RATING_4,
            "rate_3": self.PROFILE_WEIGHT_RATING_3,
            "rate_2": self.PROFILE_WEIGHT_RATING_2,
            "rate_1": self.PROFILE_WEIGHT_RATING_1,
            "recommendation_helpful": self.PROFILE_WEIGHT_RECOMMENDATION_HELPFUL,
            "recommendation_not_helpful": self.PROFILE_WEIGHT_RECOMMENDATION_NOT_HELPFUL,
            "save": self.PROFILE_WEIGHT_SAVE,
            "complete": self.PROFILE_WEIGHT_COMPLETE,
            "start": self.PROFILE_WEIGHT_START,
            "progress": self.PROFILE_WEIGHT_PROGRESS,
            "abandon": self.PROFILE_WEIGHT_ABANDON,
            "dismiss": self.PROFILE_WEIGHT_DISMISS,
            "recommendation_click": self.PROFILE_WEIGHT_RECOMMENDATION_CLICK,
            "click": self.PROFILE_WEIGHT_CLICK,
            "view": self.PROFILE_WEIGHT_VIEW,
        }


settings = Settings()
