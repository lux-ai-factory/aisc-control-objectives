"""shown_error: an error as a reader may see it.

The same cases run in apps/qualification/services/agents/tests/test_shown_error.py against that
app's copy, which is byte-identical to src/aisc_control_objectives/errors.py; only MODULE differs between the
two files. Every key below is made up.
"""
from __future__ import annotations

import importlib
import time

import pytest

MODULE = "aisc_control_objectives.errors"

JUDGE_KEY = "lb-judge-0123456789abcdef"
#: split: a whole AWS key id literal trips test/unit/secrets.test.ts
AWS_KEY_ID = "AKIA" + "ABCDEFGHIJKLMNOP"


@pytest.fixture
def shown_error():
    return importlib.import_module(MODULE).shown_error


def test_a_json_quoted_key_loses_its_value(shown_error):
    out = shown_error('the provider refused {"api_key": "' + JUDGE_KEY + '", "model": "m"}', 300)
    assert JUDGE_KEY not in out and "[redacted]" in out
    assert '"model": "m"' in out


def test_an_authorization_token_header_loses_its_value(shown_error):
    out = shown_error("sent Authorization: Token abcdef0123456789 and was refused", 300)
    assert "abcdef0123456789" not in out
    assert out.startswith("sent Authorization: [redacted]")


def test_an_aws_access_key_id_is_taken_out(shown_error):
    out = shown_error(f"S3 said no to {AWS_KEY_ID} today", 300)
    assert "AKIA" not in out and "[redacted]" in out


def test_a_bearer_a_query_key_and_a_url_password_are_taken_out(shown_error):
    out = shown_error("GET https://u:pw-0123456789@x/v1?key=AIzaSyA-not-real-0123456789 "
                      "Bearer abcDEF0123456789ghiJKL", 300)
    for secret in ("pw-0123456789", "AIzaSyA", "abcDEF0123456789ghiJKL"):
        assert secret not in out


def test_a_long_error_is_cut_to_the_limit(shown_error):
    out = shown_error("word " * 1000, 300)
    assert len(out) <= 300 and out.endswith("...")
    assert len(shown_error("word " * 1000, 50)) <= 50


def test_a_short_error_is_kept_as_it_reads(shown_error):
    assert shown_error("the model  answered\nnothing", 300) == "the model answered nothing"
    assert shown_error("", 300) == ""


def test_a_long_error_is_cut_before_it_is_scrubbed_so_it_takes_no_time(shown_error):
    # the patterns are super-quadratic on long input: 'a-' * 8000 took 8.6 s uncut
    started = time.monotonic()
    out = shown_error("a-" * 50_000, 300)
    assert time.monotonic() - started < 1 and len(out) <= 300
