"""
Gemini client wrapper using google-genai SDK.
Defaults to Gemini Pro (gemini-2.5-pro) for high-grade analytical reasoning,
with native Pydantic structured output support and zero-cost test mockability.
"""

import logging
import os
from typing import Any, Optional, Type, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from src.config import get_gemini_api_key, get_gemini_model
from src.intelligence.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

PREFERRED_MODELS: list[str] = [
    "gemini-3.1-pro",  # Primary target (auto-upgrades as soon as available on Vertex AI)
    "gemini-2.5-pro",  # Proven stable fallback currently active
]

_NEGOTIATED_MODEL: Optional[str] = None
_MODEL_PROBE_CACHE: dict[str, Any] = {}


def clean_json_text(raw_text: str) -> str:
    """Clean markdown code block wrappers and whitespace from JSON response."""
    text = raw_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip() or "{}"


def get_negotiated_model() -> Optional[str]:
    """Return the currently negotiated active model, if any."""
    return _NEGOTIATED_MODEL


def set_negotiated_model(model: Optional[str]) -> None:
    """Explicitly set the active negotiated model (useful for testing)."""
    global _NEGOTIATED_MODEL
    _NEGOTIATED_MODEL = model


def negotiate_active_model(
    force: bool = False,
    api_key: Optional[str] = None,
    mock_client: Optional[Any] = None,
) -> tuple[str, dict[str, Any]]:
    """Probe candidate models in preference order (gemini-3.1-pro -> gemini-2.5-pro).

    Returns:
        (active_model, diagnostics_dict)
    """
    global _NEGOTIATED_MODEL, _MODEL_PROBE_CACHE
    if not force and _NEGOTIATED_MODEL is not None and _MODEL_PROBE_CACHE:
        return _NEGOTIATED_MODEL, _MODEL_PROBE_CACHE

    diagnostics: dict[str, Any] = {
        "preferred_target": PREFERRED_MODELS[0],
        "candidates": {},
    }

    forced_model = os.environ.get("GEMINI_MODEL")
    candidate_list = list(PREFERRED_MODELS)
    if forced_model and forced_model not in ("auto", ""):
        candidate_list = [forced_model] + [m for m in PREFERRED_MODELS if m != forced_model]

    selected_model: Optional[str] = None

    for candidate in candidate_list:
        try:
            probe_client = GeminiIntelligenceClient(
                api_key=api_key, model=candidate, mock_client=mock_client
            )
            if probe_client.validate_model():
                diagnostics["candidates"][candidate] = {
                    "available": True,
                    "status": "200 OK",
                }
                if selected_model is None:
                    selected_model = candidate
                    if candidate == PREFERRED_MODELS[0]:
                        logger.info(
                            "🚀 Target model %s is available and selected as primary!", candidate
                        )
                    else:
                        logger.info(
                            "Selected model %s (target %s not yet available)",
                            candidate,
                            PREFERRED_MODELS[0],
                        )
            else:
                diagnostics["candidates"][candidate] = {
                    "available": False,
                    "status": probe_client.last_validation_error or "validation_returned_empty",
                }
        except Exception as e:
            diagnostics["candidates"][candidate] = {
                "available": False,
                "status": str(e),
            }

    if selected_model is None:
        selected_model = PREFERRED_MODELS[-1]
        logger.warning(
            "No candidate model passed validation probe. Defaulting to %s.", selected_model
        )

    _NEGOTIATED_MODEL = selected_model
    diagnostics["active_model"] = selected_model
    diagnostics["auto_upgrade_active"] = selected_model == PREFERRED_MODELS[0]
    _MODEL_PROBE_CACHE = diagnostics

    return selected_model, diagnostics


class GeminiIntelligenceClient:
    """Client for generating fantasy football intelligence via Gemini Pro."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        mock_client: Optional[Any] = None,
    ) -> None:
        """Initialize the Gemini client.

        Args:
            api_key: Optional API key. If not provided, loaded from env via get_gemini_api_key().
            model: Optional model name. If not provided, loaded via get_gemini_model() (default: gemini-2.5-pro).
            mock_client: Optional mock client for testing without API keys.
        """
        if model is not None:
            self.model = model
        elif _NEGOTIATED_MODEL is not None:
            self.model = _NEGOTIATED_MODEL
        else:
            self.model = get_gemini_model()
        self._mock_client = mock_client

        if mock_client is not None:
            self._client = mock_client
        elif os.environ.get("USE_VERTEX_AI", "false").lower() == "true" or os.environ.get(
            "K_SERVICE"
        ):
            # On Cloud Run (K_SERVICE is set automatically), use Vertex AI with IAM credentials
            project = os.environ.get("GCP_PROJECT", "gen-lang-client-0581555372")
            region = os.environ.get("GCP_REGION", "us-central1")
            logger.info(
                "Connecting to Gemini Pro via Vertex AI in project %s (%s)", project, region
            )
            self._client = genai.Client(vertexai=True, project=project, location=region)
        else:
            resolved_key = api_key or get_gemini_api_key()
            self._client = genai.Client(api_key=resolved_key)
        self.last_validation_error: Optional[str] = None

    def generate_structured(
        self,
        prompt: str,
        response_schema: Type[T],
        system_instruction: str = SYSTEM_PROMPT,
        temperature: float = 0.2,
    ) -> T:
        """Generate structured output adhering to a Pydantic schema using Gemini Pro.

        Args:
            prompt: Task-specific prompt and data payload.
            response_schema: The Pydantic model class to constrain the output.
            system_instruction: High-level analyst persona instructions.
            temperature: Sampling temperature (default 0.2 for deterministic analytical reasoning).

        Returns:
            An instance of response_schema populated with Gemini's structured response.
        """
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=temperature,
        )

        models_to_try = [self.model]
        if self.model == PREFERRED_MODELS[0] and PREFERRED_MODELS[-1] not in models_to_try:
            models_to_try.append(PREFERRED_MODELS[-1])

        last_err: Optional[Exception] = None
        for model_id in models_to_try:
            try:
                response = self._client.models.generate_content(
                    model=model_id,
                    contents=prompt,
                    config=config,
                )
                raw_text = clean_json_text(response.text or "{}")
                validated = response_schema.model_validate_json(raw_text)
                if model_id != self.model:
                    logger.warning(
                        "In-flight failover succeeded using backup model %s (primary was %s)",
                        model_id,
                        self.model,
                    )
                    self.model = model_id
                return validated
            except Exception as e:
                last_err = e
                if model_id != models_to_try[-1]:
                    logger.warning(
                        "Primary model %s failed (%s). Attempting in-flight failover to %s...",
                        model_id,
                        e,
                        models_to_try[-1],
                    )
                else:
                    logger.error("Gemini API request failed (%s): %s", model_id, e)

        if last_err:
            raise last_err
        raise RuntimeError("No models available to process request")

    def generate_text(
        self,
        prompt: str,
        system_instruction: str = SYSTEM_PROMPT,
        temperature: float = 0.3,
    ) -> str:
        """Generate unstructured text analysis.

        Args:
            prompt: The user prompt.
            system_instruction: System prompt.
            temperature: Sampling temperature.

        Returns:
            The raw text generated by the model.
        """
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
        )

        models_to_try = [self.model]
        if self.model == PREFERRED_MODELS[0] and PREFERRED_MODELS[-1] not in models_to_try:
            models_to_try.append(PREFERRED_MODELS[-1])

        last_err: Optional[Exception] = None
        for model_id in models_to_try:
            try:
                response = self._client.models.generate_content(
                    model=model_id,
                    contents=prompt,
                    config=config,
                )
                if model_id != self.model:
                    logger.warning(
                        "In-flight failover succeeded using backup model %s for text (primary was %s)",
                        model_id,
                        self.model,
                    )
                    self.model = model_id
                return response.text or ""
            except Exception as e:
                last_err = e
                if model_id != models_to_try[-1]:
                    logger.warning(
                        "Primary model %s failed (%s). Attempting in-flight failover to %s...",
                        model_id,
                        e,
                        models_to_try[-1],
                    )
                else:
                    logger.error("Gemini text generation failed (%s): %s", model_id, e)

        if last_err:
            raise last_err
        return ""

    def validate_model(self) -> bool:
        """Probe the configured model with a minimal prompt to verify accessibility.

        Returns:
            True if the model is reachable and generates content, False otherwise.
        """
        try:
            config = types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=500,
            )
            response = self._client.models.generate_content(
                model=self.model,
                contents="ping",
                config=config,
            )
            has_candidates = bool(getattr(response, "candidates", None))
            has_text = getattr(response, "text", None) is not None
            if response and (has_candidates or has_text):
                logger.info("Gemini model %s validated successfully", self.model)
                return True
            logger.warning("Gemini model %s returned empty response during validation", self.model)
            return False
        except Exception as e:
            self.last_validation_error = str(e)
            logger.error(f"Gemini model validation failed for {self.model}: {e}")
            return False
