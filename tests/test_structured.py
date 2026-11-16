import pytest
from pydantic import BaseModel, ValidationError

from epaper_goodnews import structured
from epaper_goodnews.structured import parse_structured


class Payload(BaseModel):
    value: int


class Boom(RuntimeError):
    pass


def prose_instead_of_json() -> ValidationError:
    """The error the SDK raises when the model answers in prose (as on 2026-11-03)."""
    try:
        Payload.model_validate_json("We need output JSON all the items.")
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")


class FakeClient:
    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0
        self.responses = self

    def parse(self, **kwargs):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Response:
    id = "r"
    status = "completed"
    output: list = []
    usage = None
    output_parsed = Payload(value=7)


def call(client):
    return parse_structured(
        client=client,
        model="m",
        prompt="p",
        text_format=Payload,
        reasoning_effort=None,
        max_output_tokens=100,
        error_cls=Boom,
        label="test",
    )


def test_malformed_output_is_requested_again():
    client = FakeClient(prose_instead_of_json(), Response())
    _, parsed = call(client)
    assert parsed.value == 7
    assert client.calls == 2


def test_gives_up_after_repeated_malformed_output():
    attempts = structured.MALFORMED_OUTPUT_RETRIES + 1
    client = FakeClient(*[prose_instead_of_json() for _ in range(attempts)])
    with pytest.raises(Boom, match="did not match the schema"):
        call(client)
    assert client.calls == attempts


def test_api_errors_are_not_retried_here():
    # The SDK already retries transport errors; a second layer would multiply the wait.
    client = FakeClient(ConnectionError("down"), Response())
    with pytest.raises(Boom, match="call failed"):
        call(client)
    assert client.calls == 1
