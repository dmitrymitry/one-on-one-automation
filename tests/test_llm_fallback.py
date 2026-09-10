from types import SimpleNamespace

import pytest

from app.llm_analyzer import LLMAnalyzer, RawHostTasks, _status_code


class FakeError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"http {code}")
        self.code = code


ANSWER = '{"tasks": [{"task": "зробити", "deadline": ""}]}'


class FakeModels:
    """Answers only for (model, key) pairs in `serves`; the rest raise `code`."""

    def __init__(self, serves: set, code: int, key: str, log: list) -> None:
        self.serves = serves
        self.code = code
        self.key = key
        self.log = log

    def generate_content(self, model, contents, config):
        self.log.append((model, self.key))
        if model not in self.serves and (model, self.key) not in self.serves:
            raise FakeError(self.code)
        return SimpleNamespace(text=ANSWER)


def make_analyzer(
    serves: set,
    code: int = 503,
    chain: str = "strong,mid,weak",
    keys: str = "k1",
):
    analyzer = LLMAnalyzer.__new__(LLMAnalyzer)
    analyzer.provider = "gemini"
    key_list = [k.strip() for k in keys.split(",") if k.strip()]
    analyzer.settings = SimpleNamespace(
        gemini_model=chain,
        gemini_api_key=keys,
        gemini_model_list=[m.strip() for m in chain.split(",") if m.strip()],
        gemini_api_key_list=key_list,
    )
    log: list = []
    analyzer.client = {
        key: SimpleNamespace(models=FakeModels(serves, code, key, log)) for key in key_list
    }
    return analyzer, SimpleNamespace(tried=log)


def test_strongest_model_is_used_when_available() -> None:
    analyzer, models = make_analyzer({"strong", "mid", "weak"})

    analyzer._generate_gemini("p", RawHostTasks)

    assert [m for m, _ in models.tried] == ["strong"]


def test_falls_through_to_the_next_model_on_503() -> None:
    analyzer, models = make_analyzer({"weak"})

    result = analyzer._generate_gemini("p", RawHostTasks)

    assert [m for m, _ in models.tried] == ["strong", "mid", "weak"]
    assert result.tasks[0].task == "зробити"


def test_quota_exhaustion_also_falls_through() -> None:
    """429 on a preview model must not kill the follow-up."""
    analyzer, models = make_analyzer({"mid"}, code=429)

    analyzer._generate_gemini("p", RawHostTasks)

    assert [m for m, _ in models.tried] == ["strong", "mid"]


def test_bad_request_is_not_hidden_by_falling_back() -> None:
    """A 400 is our bug; degrading silently would bury it."""
    analyzer, models = make_analyzer(set(), code=400)

    with pytest.raises(FakeError):
        analyzer._generate_gemini("p", RawHostTasks)

    assert [m for m, _ in models.tried] == ["strong"]


def test_all_models_down_raises_with_the_chain_named() -> None:
    analyzer, models = make_analyzer(set())

    with pytest.raises(RuntimeError, match="strong"):
        analyzer._generate_gemini("p", RawHostTasks)

    assert [m for m, _ in models.tried] == ["strong", "mid", "weak"]


def test_status_code_is_read_from_either_attribute() -> None:
    assert _status_code(FakeError(503)) == 503
    assert _status_code(SimpleNamespace(response=SimpleNamespace(status_code=429))) == 429
    assert _status_code(ValueError("no status here")) is None


def test_quota_error_rotates_to_the_next_key() -> None:
    """429 is a per-project wall: another project's key clears it."""
    analyzer, models = make_analyzer({("strong", "k2")}, code=429, keys="k1,k2")

    analyzer._generate_gemini("p", RawHostTasks)

    # Same model, second key — not a downgrade to the weaker model.
    assert models.tried == [("strong", "k1"), ("strong", "k2")]


def test_overload_skips_the_remaining_keys() -> None:
    """503 means the model is busy for everyone; only a weaker model helps."""
    analyzer, models = make_analyzer({"mid"}, code=503, keys="k1,k2")

    analyzer._generate_gemini("p", RawHostTasks)

    assert models.tried == [("strong", "k1"), ("mid", "k1")]


def test_every_model_and_key_exhausted_names_the_key_count() -> None:
    analyzer, models = make_analyzer(set(), code=429, chain="strong,mid", keys="k1,k2")

    with pytest.raises(RuntimeError, match="2 key"):
        analyzer._generate_gemini("p", RawHostTasks)

    assert models.tried == [
        ("strong", "k1"),
        ("strong", "k2"),
        ("mid", "k1"),
        ("mid", "k2"),
    ]
