import pytest
from pydantic import BaseModel

from src.intelligence.gemini_client import (
    PREFERRED_MODELS,
    GeminiIntelligenceClient,
    clean_json_text,
    negotiate_active_model,
)


class DummySchema(BaseModel):
    verdict: str
    score: float


class MockModelResponse:
    def __init__(self, text: str):
        self.text = text


class MockModels:
    def __init__(self, response_text: str):
        self._response_text = response_text
        self.last_contents = None
        self.last_config = None

    def generate_content(self, model: str, contents: str, config=None):
        self.last_contents = contents
        self.last_config = config
        return MockModelResponse(self._response_text)


class MockGenaiClient:
    def __init__(self, response_text: str = '{"verdict": "START", "score": 22.5}'):
        self.models = MockModels(response_text)


def test_gemini_client_init_default_pro():
    mock_sdk = MockGenaiClient()
    client = GeminiIntelligenceClient(mock_client=mock_sdk)
    assert client.model in PREFERRED_MODELS


def test_gemini_client_custom_model():
    mock_sdk = MockGenaiClient()
    client = GeminiIntelligenceClient(model="custom-pro-model", mock_client=mock_sdk)
    assert client.model == "custom-pro-model"


def test_generate_structured_success():
    mock_sdk = MockGenaiClient('{"verdict": "WIN", "score": 99.0}')
    client = GeminiIntelligenceClient(mock_client=mock_sdk)

    result = client.generate_structured(
        prompt="Analyze matchup",
        response_schema=DummySchema,
    )
    assert isinstance(result, DummySchema)
    assert result.verdict == "WIN"
    assert result.score == 99.0
    assert mock_sdk.models.last_contents == "Analyze matchup"


def test_generate_text_success():
    mock_sdk = MockGenaiClient("Detailed scouting report text")
    client = GeminiIntelligenceClient(mock_client=mock_sdk)

    text = client.generate_text("Generate report")
    assert text == "Detailed scouting report text"


def test_generate_structured_error_handling():
    class ErrorModels:
        def generate_content(self, *args, **kwargs):
            raise RuntimeError("Rate limit exceeded")

    class ErrorClient:
        models = ErrorModels()

    client = GeminiIntelligenceClient(mock_client=ErrorClient())
    with pytest.raises(RuntimeError, match="Rate limit exceeded"):
        client.generate_structured("prompt", DummySchema)


def test_missing_api_key_raises_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="GEMINI_API_KEY not found"):
        GeminiIntelligenceClient()


def test_validate_model_success():
    mock_sdk = MockGenaiClient("pong")
    client = GeminiIntelligenceClient(mock_client=mock_sdk)
    assert client.validate_model() is True


def test_validate_model_failure():
    class FailingModels:
        def generate_content(self, *args, **kwargs):
            raise ConnectionError("Endpoint unreachable")

    class FailingClient:
        models = FailingModels()

    client = GeminiIntelligenceClient(mock_client=FailingClient())
    assert client.validate_model() is False


def test_clean_json_text():
    assert clean_json_text('{"verdict": "START"}') == '{"verdict": "START"}'
    assert (
        clean_json_text('```json\n{"verdict": "START"}\n```') == '{"verdict": "START"}'
    )
    assert clean_json_text('```\n{"verdict": "START"}\n```') == '{"verdict": "START"}'
    assert clean_json_text('  {"verdict": "START"}  \n') == '{"verdict": "START"}'


def test_negotiate_active_model_fallback_when_target_unavailable():
    class SelectiveModels:
        def generate_content(self, model, contents, config=None):
            if "gemini-3" in model:
                raise ConnectionError("404 Model Not Found in us-central1")
            return MockModelResponse("ok")

    class SelectiveClient:
        models = SelectiveModels()

    selected, diag = negotiate_active_model(force=True, mock_client=SelectiveClient())
    assert selected == "gemini-2.5-pro"
    assert diag["auto_upgrade_active"] is False
    assert diag["candidates"]["gemini-3.1-pro-preview"]["available"] is False
    assert "404" in diag["candidates"]["gemini-3.1-pro-preview"]["status"]
    assert diag["candidates"]["gemini-2.5-pro"]["available"] is True


def test_negotiate_active_model_upgrades_when_target_available():
    class AllAvailableModels:
        def generate_content(self, model, contents, config=None):
            return MockModelResponse("ok")

    class AllAvailableClient:
        models = AllAvailableModels()

    selected, diag = negotiate_active_model(force=True, mock_client=AllAvailableClient())
    assert selected == "gemini-3.1-pro-preview"
    assert diag["auto_upgrade_active"] is True
    assert diag["candidates"]["gemini-3.1-pro-preview"]["available"] is True
    assert diag["candidates"]["gemini-2.5-pro"]["available"] is True


def test_in_flight_failover_structured():
    calls = []

    class FailoverModels:
        def generate_content(self, model, contents, config=None):
            calls.append(model)
            if model == "gemini-3.1-pro-preview":
                raise RuntimeError("503 Service Unavailable")
            return MockModelResponse('{"verdict": "FAILOVER_OK", "score": 88.0}')

    class FailoverClient:
        models = FailoverModels()

    client = GeminiIntelligenceClient(model="gemini-3.1-pro-preview", mock_client=FailoverClient())
    res = client.generate_structured("test prompt", DummySchema)
    assert res.verdict == "FAILOVER_OK"
    assert res.score == 88.0
    assert calls == ["gemini-3.1-pro-preview", "gemini-2.5-pro"]
    assert client.model == "gemini-2.5-pro"


def test_in_flight_failover_text():
    calls = []

    class FailoverModels:
        def generate_content(self, model, contents, config=None):
            calls.append(model)
            if model == "gemini-3.1-pro-preview":
                raise RuntimeError("Model not found or permission denied")
            return MockModelResponse("Failover text response")

    class FailoverClient:
        models = FailoverModels()

    client = GeminiIntelligenceClient(model="gemini-3.1-pro-preview", mock_client=FailoverClient())
    res = client.generate_text("test prompt")
    assert res == "Failover text response"
    assert calls == ["gemini-3.1-pro-preview", "gemini-2.5-pro"]
    assert client.model == "gemini-2.5-pro"


def test_429_retry_structured_succeeds_on_second_attempt(monkeypatch):
    """Test that generate_structured retries on 429 and succeeds when the API recovers."""
    import src.intelligence.gemini_client as gc

    # Speed up test by reducing backoff
    monkeypatch.setattr(gc, "INITIAL_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(gc, "BACKOFF_MULTIPLIER", 1.0)

    call_count = [0]

    class RetryModels:
        def generate_content(self, model, contents, config=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("429 RESOURCE_EXHAUSTED. Please try again later.")
            return MockModelResponse('{"verdict": "RETRY_OK", "score": 42.0}')

    class RetryClient:
        models = RetryModels()

    client = GeminiIntelligenceClient(model="gemini-2.5-pro", mock_client=RetryClient())
    res = client.generate_structured("test prompt", DummySchema)
    assert res.verdict == "RETRY_OK"
    assert call_count[0] == 2  # First attempt failed, second succeeded


def test_429_retry_text_succeeds_on_third_attempt(monkeypatch):
    """Test that generate_text retries on 429 multiple times and succeeds."""
    import src.intelligence.gemini_client as gc

    monkeypatch.setattr(gc, "INITIAL_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(gc, "BACKOFF_MULTIPLIER", 1.0)

    call_count = [0]

    class RetryModels:
        def generate_content(self, model, contents, config=None):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError("Resource exhausted. Please try again later.")
            return MockModelResponse("Success after retries")

    class RetryClient:
        models = RetryModels()

    client = GeminiIntelligenceClient(model="gemini-2.5-pro", mock_client=RetryClient())
    res = client.generate_text("test prompt")
    assert res == "Success after retries"
    assert call_count[0] == 3


def test_429_exhausted_retries_falls_to_backup_model(monkeypatch):
    """Test that after exhausting 429 retries on primary, it fails over to backup model."""
    import src.intelligence.gemini_client as gc

    monkeypatch.setattr(gc, "INITIAL_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(gc, "BACKOFF_MULTIPLIER", 1.0)
    monkeypatch.setattr(gc, "MAX_RETRIES_429", 2)

    calls = []

    class RetryModels:
        def generate_content(self, model, contents, config=None):
            calls.append(model)
            if model == "gemini-3.1-pro-preview":
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
            return MockModelResponse('{"verdict": "BACKUP_OK", "score": 99.0}')

    class RetryClient:
        models = RetryModels()

    client = GeminiIntelligenceClient(model="gemini-3.1-pro-preview", mock_client=RetryClient())
    res = client.generate_structured("test prompt", DummySchema)
    assert res.verdict == "BACKUP_OK"
    # Should have retried 3 times on primary (initial + 2 retries), then succeeded on backup
    primary_calls = [c for c in calls if c == "gemini-3.1-pro-preview"]
    assert len(primary_calls) == 3
    assert calls[-1] == "gemini-2.5-pro"
    assert client.model == "gemini-2.5-pro"


def test_is_rate_limit_error_detection():
    """Test the _is_rate_limit_error static helper correctly identifies 429 errors."""
    assert GeminiIntelligenceClient._is_rate_limit_error(RuntimeError("429 RESOURCE_EXHAUSTED"))
    assert GeminiIntelligenceClient._is_rate_limit_error(RuntimeError("Resource exhausted. Please try again."))
    assert GeminiIntelligenceClient._is_rate_limit_error(RuntimeError("Error code 429: rate limit"))
    assert not GeminiIntelligenceClient._is_rate_limit_error(RuntimeError("404 Model not found"))
    assert not GeminiIntelligenceClient._is_rate_limit_error(RuntimeError("500 Internal Server Error"))

