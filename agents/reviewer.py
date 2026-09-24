"""Agent reviewers for the CGC runtime.

A reviewer maps (case, role) -> {"decision": APPROVE|REJECT, "reason": str},
reasoning ONLY over the agent-visible `facts` (never `held_out`, which is the
ground-truth Inspector reasoning).

- `ClaudeReviewer`  — the real thing: prompts a Claude model with the case facts.
- `OpenRouterReviewer` — the same prompts sent to any model on OpenRouter, with
                      a disk cache, token and cost accounting. `PerOrgReviewer`
                      gives each organisation its own model.
- `MockReviewer`    — a deterministic *simulation* with tunable per-agent accuracy,
                      so the harness (gate, red-team, metrics) is runnable and
                      testable offline with no API key. It uses the label only to
                      SIMULATE an agent of known skill; it is not a classifier.

Red-team injection (see redteam.py) is passed as `poison` and alters an agent's
behaviour to model a hallucination.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

APPROVE, REJECT = "APPROVE", "REJECT"

from domains import build_messages  # noqa: E402  (prompts live in domains.py)


class MockReviewer:
    """Deterministic simulation. Each honest agent returns the true label with
    probability `accuracy`, else flips — seeded by (case, role) so runs are
    reproducible. Poison overrides behaviour to model a hallucination."""

    def __init__(self, accuracy: float = 0.75, salt: str = "a2"):
        self.accuracy = accuracy
        self.salt = salt

    def _rand(self, case_id: str, role: str) -> float:
        h = hashlib.sha256(f"{self.salt}:{case_id}:{role}".encode()).hexdigest()
        return int(h[:8], 16) / 0xFFFFFFFF

    def review(self, case: dict, role: str, poison: str | None = None) -> dict:
        truth = case["label"]
        wrong = REJECT if truth == APPROVE else APPROVE
        if poison in ("confident-wrong", "prompt-poison"):
            return {"decision": wrong, "reason": f"[poisoned:{poison}] confident but mistaken", "poisoned": True}
        if poison == "sycophancy":
            # defer to the proposer's action regardless of merit — resolved by caller
            return {"decision": "DEFER", "reason": "[poisoned:sycophancy] defers to proposer", "poisoned": True}
        # Seed on the appeal ref, not the ledger key (which carries --run-id),
        # so the simulated agent is identical across runs and ledgers.
        ref = case.get("ref") or case.get("appeal_ref") or case["case_id"]
        decision = truth if self._rand(ref, role) < self.accuracy else wrong
        return {"decision": decision, "reason": f"simulated {role} judgement", "poisoned": False}


class ClaudeReviewer:
    """Real reviewer backed by a Claude model reading only the case facts."""

    def __init__(self, model: str = "claude-sonnet-5", temperature: float = 0.0):
        from anthropic import Anthropic  # lazy import
        self.client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = model
        self.temperature = temperature

    def review(self, case: dict, role: str, poison: str | None = None) -> dict:
        if poison == "sycophancy":
            return _sycophant()
        system, prompt = build_messages(case, role, poison)
        msg = self.client.messages.create(
            model=self.model, max_tokens=200, temperature=self.temperature,
            system=system, messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return _parse(text, poison is not None)


def _parse(text: str, poisoned: bool) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            dec = str(obj.get("decision", "")).upper()
            if dec in (APPROVE, REJECT):
                return {"decision": dec, "reason": str(obj.get("reason", ""))[:200],
                        "poisoned": poisoned, "parse_ok": True}
        except json.JSONDecodeError:
            pass
    # Keyword fallback. Flagged so unparseable replies are counted, not hidden.
    up = text.upper()
    dec = APPROVE if ("ALLOW" in up or "APPROVE" in up) and "DISMISS" not in up else REJECT
    return {"decision": dec, "reason": text.strip()[:200], "poisoned": poisoned, "parse_ok": False}


def _sycophant() -> dict:
    # Agents review in parallel before the proposal exists, so a sycophant cannot
    # be prompted to agree with it; it is modelled as endorsing whatever is proposed.
    return {"decision": "DEFER", "reason": "[poisoned:sycophancy] defers to proposer",
            "poisoned": True, "parse_ok": True}


class OpenRouterReviewer:
    """Any OpenRouter model behind the same prompts as ClaudeReviewer.

    A verdict depends only on (model, prompts, temperature), not on the policy or
    coordinator, so responses are cached on disk and reused across every policy,
    coordinator and seed. That keeps a full factorial to a few thousand calls
    per model and makes reruns free and exactly reproducible.
    """

    URL = "https://openrouter.ai/api/v1/chat/completions"

    MIN_REASONING = 128  # used only when a model refuses to run without reasoning

    def __init__(self, model: str, temperature: float = 0.0, max_tokens: int = 2048,
                 reasoning_budget: int = 0, cache_dir: str | None = None, retries: int = 5):
        import requests  # lazy: only needed for live runs
        self._requests = requests
        self.api_key = os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key:
            raise SystemExit("OPENROUTER_API_KEY is not set: add it to the repo .env (git ignored)")
        self.model, self.temperature, self.max_tokens, self.retries = model, temperature, max_tokens, retries
        # Hidden reasoning is off by default so every model answers directly and
        # cost stays bounded (effort "low" still let some models reason for ~4k
        # tokens, and providers honour budgets differently). A model that refuses
        # to run without reasoning falls back to MIN_REASONING, and each verdict
        # records the mode it ran in. Hidden reasoning counts against max_tokens.
        self.reasoning_budget = reasoning_budget
        # Anchored to this file, not the working directory: a relative default once
        # sent the full run's cache outside the repository.
        cache_dir = cache_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "cache", "llm")
        self.cache_dir = os.path.join(cache_dir, re.sub(r"[^A-Za-z0-9_.-]", "_", model))
        os.makedirs(self.cache_dir, exist_ok=True)

    def _cache_path(self, system: str, prompt: str) -> str:
        key = json.dumps([self.model, self.temperature, self.max_tokens, self.reasoning_budget, system, prompt])
        return os.path.join(self.cache_dir, hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")

    def _call(self, system: str, prompt: str, max_tokens: int | None = None) -> dict:
        body = {
            "model": self.model, "temperature": self.temperature, "max_tokens": max_tokens or self.max_tokens,
            # budget 0 switches hidden reasoning off; otherwise cap it
            "reasoning": {"enabled": False} if self.reasoning_budget == 0 else {"max_tokens": self.reasoning_budget},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "usage": {"include": True},   # OpenRouter reports the billed cost per call
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        for attempt in range(self.retries):
            try:
                r = self._requests.post(self.URL, json=body, headers=headers, timeout=120)
            except (self._requests.exceptions.Timeout, self._requests.exceptions.ConnectionError):
                time.sleep(min(60, 2 ** attempt))  # dropped or slow connection: back off, retry
                continue
            if r.status_code == 200 and "choices" in r.json():
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504) or r.status_code == 200:
                time.sleep(min(60, 2 ** attempt))
                continue
            raise RuntimeError(f"{self.model}: HTTP {r.status_code}: {r.text[:300]}")
        raise RuntimeError(f"{self.model}: no response after {self.retries} attempts")

    def review(self, case: dict, role: str, poison: str | None = None) -> dict:
        if poison == "sycophancy":
            return {**_sycophant(), "model": self.model, "cached": True,
                    "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
        system, prompt = build_messages(case, role, poison)
        budget = self.reasoning_budget  # the budget this request is sent with
        path = self._cache_path(system, prompt)
        cached = os.path.exists(path)
        if cached:
            with open(path) as f:
                resp = json.load(f)
        else:
            try:
                resp = self._call(system, prompt)
            except RuntimeError as e:
                # Decide by the budget this request used: parallel requests sent with
                # reasoning off may fail after another thread already switched.
                if budget != 0 or "Reasoning is mandatory" not in str(e):
                    raise
                self.reasoning_budget = self.MIN_REASONING
                return self.review(case, role, poison)
            if _truncated(resp):  # reasoning used the whole budget: one retry with double
                resp = self._call(system, prompt, 2 * self.max_tokens)
            with open(path + ".tmp", "w") as f:
                json.dump(resp, f)
            os.replace(path + ".tmp", path)
        text = resp["choices"][0]["message"].get("content") or ""
        usage = resp.get("usage") or {}
        out = _parse(text, poison is not None)
        out.update({
            "model": self.model, "cached": cached, "reasoning_budget": self.reasoning_budget,
            "tokens_in": usage.get("prompt_tokens", 0), "tokens_out": usage.get("completion_tokens", 0),
            "tokens_reasoning": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
            "truncated": _truncated(resp),
            "cost_usd": float(usage.get("cost") or 0.0),
        })
        return out


def _truncated(resp: dict) -> bool:
    choice = resp["choices"][0]
    return choice.get("finish_reason") == "length" and not (choice["message"].get("content") or "").strip()


class PerOrgReviewer:
    """Each organisation reviews with its own model (heterogeneous consortium)."""

    def __init__(self, reviewers: dict):
        self.reviewers = reviewers  # msp -> reviewer

    def review(self, case: dict, role: str, poison: str | None = None) -> dict:
        return self.reviewers[role].review(case, role, poison)


def make_reviewer(kind: str, **kw):
    if kind == "openrouter":
        models = kw.get("models") or {}
        if models:
            return PerOrgReviewer({o: OpenRouterReviewer(m, kw.get("temperature", 0.0)) for o, m in models.items()})
        return OpenRouterReviewer(kw["model"], kw.get("temperature", 0.0))
    if kind == "claude":
        return ClaudeReviewer(**{k: v for k, v in kw.items() if k in ("model", "temperature")})
    if kind == "mock":
        return MockReviewer(**{k: v for k, v in kw.items() if k in ("accuracy", "salt")})
    raise ValueError(f"unknown reviewer {kind!r}")
