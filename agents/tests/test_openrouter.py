"""OpenRouterReviewer with a fake HTTP layer: no network, no spend."""
import json

import pytest

import reviewer as rv

CASE = {"case_id": "C/1", "appeal_ref": "C/1", "label": "APPROVE",
        "facts": {"header_bullets": "x", "development": "y", "applicable_policies": ["P1"]}}


class FakeRequests:
    import requests as _real
    exceptions = _real.exceptions

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(json)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        return type("R", (), {"status_code": status, "json": lambda self=None: body,
                              "raise_for_status": lambda self=None: None})()


def ok(decision, cost=0.001):
    return 200, {"choices": [{"message": {"content": json.dumps({"decision": decision, "reason": "r"})}}],
                 "usage": {"prompt_tokens": 900, "completion_tokens": 20, "cost": cost}}


@pytest.fixture
def reviewer(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(rv.time, "sleep", lambda s: None)
    r = rv.OpenRouterReviewer("vendor/model", cache_dir=str(tmp_path))
    return r


def test_parses_verdict_and_accounts_tokens(reviewer):
    reviewer._requests = FakeRequests([ok("REJECT", 0.002)])
    v = reviewer.review(CASE, "Org2MSP")
    assert (v["decision"], v["parse_ok"], v["cached"]) == ("REJECT", True, False)
    assert (v["tokens_in"], v["tokens_out"], v["cost_usd"]) == (900, 20, 0.002)


def test_second_identical_call_is_served_from_cache(reviewer):
    fake = FakeRequests([ok("APPROVE")])
    reviewer._requests = fake
    a = reviewer.review(CASE, "Org1MSP")
    b = reviewer.review(CASE, "Org1MSP")
    assert len(fake.calls) == 1 and b["cached"] and a["decision"] == b["decision"]
    assert b["cost_usd"] == a["cost_usd"]       # cost of the original call is kept


def test_poison_changes_the_prompt_and_the_cache_key(reviewer):
    fake = FakeRequests([ok("APPROVE"), ok("REJECT")])
    reviewer._requests = fake
    reviewer.review(CASE, "Org1MSP")
    reviewer.review(CASE, "Org1MSP", "prompt-poison")
    assert len(fake.calls) == 2
    assert "senior counsel" in fake.calls[1]["messages"][1]["content"]


def test_sycophant_defers_without_a_call(reviewer):
    fake = FakeRequests([])
    reviewer._requests = fake
    assert reviewer.review(CASE, "Org3MSP", "sycophancy")["decision"] == "DEFER" and not fake.calls


def test_retries_rate_limits_then_succeeds(reviewer):
    reviewer._requests = FakeRequests([(429, {}), (200, {"error": "busy"}), ok("APPROVE")])
    assert reviewer.review(CASE, "Org1MSP")["decision"] == "APPROVE"


def test_unparseable_reply_is_flagged(reviewer):
    reviewer._requests = FakeRequests([(200, {"choices": [{"message": {"content": "The appeal should be allowed."}}],
                                              "usage": {}})])
    v = reviewer.review(CASE, "Org1MSP")
    assert v["decision"] == "APPROVE" and v["parse_ok"] is False


def test_truncated_reasoning_reply_is_retried_with_a_bigger_budget(reviewer):
    cut = (200, {"choices": [{"finish_reason": "length", "message": {"content": ""}}],
                 "usage": {"completion_tokens": 4096, "completion_tokens_details": {"reasoning_tokens": 4096}}})
    fake = FakeRequests([cut, ok("REJECT")])
    reviewer._requests = fake
    v = reviewer.review(CASE, "Org2MSP")
    assert v["decision"] == "REJECT" and v["parse_ok"] and not v["truncated"]
    assert fake.calls[1]["max_tokens"] == 2 * fake.calls[0]["max_tokens"]
    assert fake.calls[0]["reasoning"] == {"enabled": False}


def test_model_that_requires_reasoning_falls_back_to_minimum(reviewer):
    mandatory = (400, {"error": {"message": "Reasoning is mandatory for this endpoint and cannot be disabled."}})
    fake = FakeRequests([mandatory, ok("APPROVE")])
    fake_post = fake.post
    def post(url, json=None, headers=None, timeout=None):
        r = fake_post(url, json=json, headers=headers, timeout=timeout)
        r.text = str(r.json())
        return r
    fake.post = post
    reviewer._requests = fake
    v = reviewer.review(CASE, "Org1MSP")
    assert fake.calls[0]["reasoning"] == {"enabled": False}
    assert fake.calls[1]["reasoning"] == {"max_tokens": reviewer.MIN_REASONING}
    assert v["decision"] == "APPROVE" and v["reasoning_budget"] == reviewer.MIN_REASONING


def test_network_timeout_is_retried(reviewer):
    import requests
    reviewer._requests = FakeRequests([requests.exceptions.ReadTimeout("slow"),
                                       requests.exceptions.ConnectionError("reset"), ok("REJECT")])
    assert reviewer.review(CASE, "Org2MSP")["decision"] == "REJECT"
