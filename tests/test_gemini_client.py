import pytest
from pydantic import BaseModel

from src.intelligence.gemini_client import GeminiIntelligenceClient


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
    assert client.model == "gemini-2.5-pro"


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
