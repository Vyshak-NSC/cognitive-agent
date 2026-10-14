from __future__ import annotations

import pytest

from cpa.providers import RequestError
from cpa.providers.adapters.base import with_key_failover


def quota(exc: Exception) -> bool:
    return "quota" in str(exc)


def test_first_working_key_wins_and_later_keys_are_untouched():
    tried = []

    def attempt(key, is_last):
        tried.append((key, is_last))
        return key

    assert with_key_failover(["a", "b"], attempt, quota) == "a"
    assert tried == [("a", False)]


def test_rotates_on_key_problems_and_marks_the_last_key():
    tried = []

    def attempt(key, is_last):
        tried.append((key, is_last))
        if key != "c":
            raise RuntimeError("quota exceeded")
        return "ok"

    assert with_key_failover(["a", "b", "c"], attempt, quota) == "ok"
    assert tried == [("a", False), ("b", False), ("c", True)]


def test_other_errors_do_not_burn_through_keys():
    tried = []

    def attempt(key, _):
        tried.append(key)
        raise ValueError("bad request")

    with pytest.raises(ValueError):
        with_key_failover(["a", "b"], attempt, quota)
    assert tried == ["a"]


def test_every_key_exhausted_says_so():
    def attempt(key, _):
        raise RuntimeError("quota exceeded")

    with pytest.raises(RequestError, match="All 2 configured API keys"):
        with_key_failover(["a", "b"], attempt, quota)


def test_a_single_key_raises_its_own_error():
    def attempt(key, _):
        raise RuntimeError("quota exceeded")

    with pytest.raises(RuntimeError, match="quota"):
        with_key_failover(["only"], attempt, quota)
