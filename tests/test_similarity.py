import pytest

from antigravity_reviewer.similarity import (
    _stem,
    identifiers,
    is_near_duplicate,
    signature,
    title_tokens,
)

NEAR_DUPLICATE_TITLE_PAIRS = [
    (
        "Asynchronous call to createOrder is not awaited",
        "Asynchronous createOrder call is not awaited",
    ),
    (
        "Potential crash when saving an order without a discount",
        "Crash when saving an order without a discount",
    ),
    (
        "Exceptions are swallowed in `capture` method",
        "All exceptions are silently swallowed in `capture` method",
    ),
    (
        "Hardcoded fallback API key in source code",
        "Hardcoded fallback API key",
    ),
    (
        "API key is logged in plain text",
        "API key is leaked to logs",
    ),
    (
        "Floating point numbers used for currency calculations",
        "Currency calculations should use integers, not floats",
    ),
    (
        "Asynchronous createOrder call is not awaited",
        "Promise returned from `createOrder` is not awaited",
    ),
]


def _predicate(file_a, line_a, title_a, file_b, line_b, title_b):
    return is_near_duplicate(
        signature(file_a, line_a, title_a), signature(file_b, line_b, title_b)
    )


@pytest.mark.parametrize("title_a,title_b", NEAR_DUPLICATE_TITLE_PAIRS)
@pytest.mark.parametrize("line_a,line_b", [(10, 10), (10, 11)])
def test_known_near_duplicates_from_mr1(title_a, title_b, line_a, line_b):
    assert _predicate("app/orders.py", line_a, title_a, "app/orders.py", line_b, title_b)


def test_different_problems_at_the_same_line_are_not_merged():
    assert not _predicate(
        "app/api.py",
        20,
        "API call does not handle errors",
        "app/api.py",
        20,
        "API endpoint for discount lookup does not exist",
    )


def test_sql_injection_pair_is_blocked_by_line_delta_despite_high_jaccard():
    a = "SQL injection vulnerability in `find_discount`"
    b = "SQL injection vulnerability in `find_orders_with_discount`"
    # Confirm the titles really would clear the token-overlap bar on their own.
    tokens_a, tokens_b = title_tokens(a), title_tokens(b)
    shared = tokens_a & tokens_b
    jaccard = len(shared) / len(tokens_a | tokens_b)
    assert jaccard >= 0.25

    assert not _predicate("app/orders.py", 31, a, "app/orders.py", 39, b)


def test_different_findings_at_line_delta_one_are_not_merged():
    assert not _predicate(
        "app/orders.py",
        10,
        "Potential crash if discount code is invalid",
        "app/orders.py",
        11,
        "Race condition when updating discount redemption count",
    )


@pytest.mark.parametrize("title_a,title_b", NEAR_DUPLICATE_TITLE_PAIRS[:1])
def test_same_titles_in_different_files_are_not_merged(title_a, title_b):
    assert not _predicate("app/a.py", 10, title_a, "app/b.py", 10, title_b)


def test_near_duplicate_requires_both_lines_resolved():
    title = "Asynchronous call to createOrder is not awaited"
    sig_no_line = signature("app/orders.py", None, title)
    sig_with_line = signature("app/orders.py", 10, title)
    assert not is_near_duplicate(sig_no_line, sig_with_line)
    assert not is_near_duplicate(sig_with_line, sig_no_line)


def test_title_tokens_maps_awaited_and_await_to_same_token():
    assert title_tokens("awaited") == title_tokens("await")


def test_ed_suffix_enables_await_pair_detection_from_mr3_run2():
    # GitLab MR !3 run 2: these were concrete duplicates that the missing "ed" suffix let through.
    assert _predicate(
        "app/orders.py", 20,
        "Asynchronous payment authorization call is not awaited",
        "app/orders.py", 20,
        "Missing await on async call to payment service"
    )

    assert _predicate(
        "app/orders.py", 20,
        "Asynchronous call to createOrder is not awaited",
        "app/orders.py", 20,
        "Missing await on async call"
    )


def test_float_vs_currency_pair_now_detected_in_far_window():
    # Same file, lines 37 and 40: delta 3 is beyond NEAR_DELTA but within
    # FAR_DELTA, and the token sets are subset-strength (overlap 1.0), so
    # the far window catches this rather than missing it as before.
    assert _predicate(
        "app/orders.py", 37,
        "Floating point numbers used for currency calculations",
        "app/orders.py", 40,
        "Using float for currency calculations"
    )


def test_known_misses_are_documented():
    # Same file, same line 17: overlap 0.40 and jaccard 0.22, both under thresholds.
    # This is the same problem as above (float vs currency) but falls outside the overlap/jaccard rules.
    assert not _predicate(
        "app/orders.py", 17,
        "In-memory redemption counter is not durable or safe for concurrency",
        "app/orders.py", 17,
        "In-memory dictionary for redemptions is not scalable or persistent"
    )


def test_identifiers_returns_empty_for_titles_without_code_symbols():
    assert identifiers("Floating point numbers used for currency calculations") == set()
    assert identifiers("SQL injection in the API key handler") == set()


def test_identifiers_finds_inner_capital_word():
    assert identifiers("Asynchronous call to createOrder is not awaited") == {"createorder"}


def test_identifiers_finds_backticked_span():
    assert identifiers(
        "SQL injection vulnerability in `find_orders_with_discount`"
    ) == {"find_orders_with_discount"}


def test_stem_collapses_logs_logged_log_to_same_token():
    assert _stem("logs") == _stem("logged") == _stem("log")


def test_stem_collapses_call_and_calls_to_same_token():
    assert _stem("call") == _stem("calls")


# (label, file, line_a, title_a, line_b, title_b, note)
LABELED_CASES = [
    # --- must merge: same problem -------------------------------------------
    (True, "models.py", 37, "Floating point numbers used for currency calculations",
     40, "Using float for currency calculations", "MR!3 float drift 37/40"),
    (True, "models.py", 37, "Floating point numbers used for currency calculations",
     43, "Floating point numbers should not be used for currency", "MR!3 float drift 37/43"),
    (True, "client.py", 38, "API key is logged",
     43, "API key leaked in logs", "MR!3 log drift"),
    (True, "main.py", 65, "Asynchronous payment authorization call is not awaited",
     65, "Missing await on async call to payment service", "MR!3 await pair"),
    (True, "cart.js", 38, "Asynchronous call to createOrder is not awaited",
     38, "Missing await on async call", "MR!3 await pair"),
    (True, "repository.py", 31, "SQL injection vulnerability",
     31, "SQL injection vulnerability in `find_discount`", "MR!1 vague vs named, same line"),
    (True, "client.py", 57, "Exceptions are swallowed in `capture` method",
     58, "All exceptions are silently swallowed", "MR!1 adjacent, one names capture"),
    (True, "repository.py", 68, "Potential crash when saving an order without a discount",
     68, "Crash when saving an order without a discount", "MR!1 reworded"),
    (True, "client.py", 19, "Hardcoded fallback API key in source code",
     19, "Hardcoded fallback API key", "MR!1 truncated restatement"),
    (True, "main.py", 65, "Asynchronous payment authorization is not awaited",
     65, "Asynchronous call to payment authorization is not awaited", "MR!1 reworded"),
    (True, "models.py", 37, "Floating point numbers used for currency calculations",
     37, "Currency calculations should use integers, not floats", "MR!1 inverted phrasing"),

    # --- must NOT merge: different problems ----------------------------------
    (False, "repository.py", 31, "SQL injection vulnerability in `find_discount`",
     39, "SQL injection vulnerability in `find_orders_with_discount`", "two sinks, named"),
    (False, "repository.py", 31, "SQL injection vulnerability",
     39, "SQL injection vulnerability in `find_orders_with_discount`", "vague vs named, 8 apart"),
    (False, "api.js", 27, "Frontend calls a non-existent API endpoint for discounts",
     27, "Missing error handling for API request", "MR!3 two real bugs, same line"),
    (False, "api.js", 27, "API call does not handle errors",
     27, "API endpoint for discount lookup does not exist", "MR!1 two real bugs, same line"),
    (False, "main.py", 55, "Potential crash if discount code is invalid",
     56, "Race condition when updating discount redemption count", "adjacent, unrelated"),
    (False, "main.py", 55, "Crash when discount code is not found",
     56, "Race condition when updating discount redemption count", "adjacent, unrelated"),
    (False, "main.py", 56, "Race condition when updating discount redemption count",
     56, "In-memory redemption counter is not safe for concurrent use", "related but distinct"),
]


@pytest.mark.parametrize(
    "want,path,line_a,title_a,line_b,title_b,note",
    LABELED_CASES,
    ids=[case[-1] for case in LABELED_CASES],
)
def test_labeled_corpus_from_mr1_and_mr3(want, path, line_a, title_a, line_b, title_b, note):
    got = _predicate(path, line_a, title_a, path, line_b, title_b)
    assert got == want, note
