"""Opt-in two-profile local recipient tier between dedicated SimpleX profiles.

Run only inside a prepared campaign environment: a local messaging server the
two dedicated profiles alone use, pinned core and provider checkouts, and the
pinned official client SDK. The tier fails rather than skips when it is
selected without a complete campaign environment; a started run reports
recipient-side observations from a second, independently controlled profile.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.live, pytest.mark.timeout(900)]

PATH = Path(__file__).resolve().parents[1] / "scripts/qualify_two_profile_recipient.py"
spec = importlib.util.spec_from_file_location("simplex_two_profile_harness", PATH)
assert spec is not None and spec.loader is not None
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def _campaign_environment() -> dict[str, str]:
    """Collect the operator-provided campaign environment, refusing gaps."""
    required = {
        "MEDRE_SIMPLEX_TIER_SERVER": "local server address",
        "MEDRE_SIMPLEX_TIER_CORE_COMMIT": "expected core revision",
        "MEDRE_SIMPLEX_TIER_PROVIDER_COMMIT": "expected provider revision",
        "MEDRE_SIMPLEX_TIER_ROOT": "private campaign directory",
    }
    values = {}
    for name, meaning in required.items():
        value = os.environ.get(name)
        if not value:
            raise AssertionError(
                f"The two-profile tier requires {meaning} via {name}; "
                "refusing to run or skip without a complete campaign environment"
            )
        values[name] = value
    return values


async def test_two_profile_local_recipient_tier(tmp_path):
    """One campaign execution covers all four two-profile cases."""
    import secrets

    environment = _campaign_environment()
    root = Path(environment["MEDRE_SIMPLEX_TIER_ROOT"])
    run_path = root / f"run-{secrets.token_hex(4)}"
    print(f"Private two-profile campaign: {run_path}")
    record, failures = await harness.run_campaign(
        server=environment["MEDRE_SIMPLEX_TIER_SERVER"],
        root=run_path,
        expected_core_commit=environment["MEDRE_SIMPLEX_TIER_CORE_COMMIT"],
        expected_provider_commit=environment["MEDRE_SIMPLEX_TIER_PROVIDER_COMMIT"],
    )
    assert (run_path / "verdict.json").exists()
    refusals = harness.evaluate(
        record,
        expected_core_commit=environment["MEDRE_SIMPLEX_TIER_CORE_COMMIT"],
        expected_provider_commit=environment["MEDRE_SIMPLEX_TIER_PROVIDER_COMMIT"],
    )
    assert refusals == (), list(refusals)
    assert failures == [], list(failures)
    assert record["recipient_worker"]["ready_ns"] is not None
    cases = record["cases"]
    assert len(cases["group_send_observed"]["observations"]) == 1
    assert (
        cases["group_send_observed"]["observations"][0]["text_sha256"]
        == cases["group_send_observed"]["sent_text_sha256"]
    )
    assert cases["group_send_observed"]["sibling_observations"] == []
    assert len(cases["native_quote_target"]["observations"]) == 1
    assert cases["restart_continuation"]["new_sends"] == 0
    assert cases["transient_worker_admission"]["journal_entry"] is not None
