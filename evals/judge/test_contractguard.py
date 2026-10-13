"""Optional live checks for the contract-specific judge; no manual labels are required."""

from __future__ import annotations

import pytest

from evals.helpers import HAS_KEY
from waku.config import Settings
from waku.loop.models import get_client
from waku.ops.contractguard_judge import judge_review

pytestmark = pytest.mark.skipif(not HAS_KEY, reason='Contract judge requires provider access')


def test_judge_recognizes_history_as_insufficient_current_evidence():
    settings = Settings()
    source = 'The parties shall exchange monthly progress reports.'
    findings = [{'claim': 'Current liability is unlimited.',
                 'basis': 'A different historical contract had unlimited liability.'}]
    assessment = judge_review(get_client(settings), settings.small_model, source,
                              {'role': 'supplier'}, findings)
    assert assessment['criteria']['evidence_support']['score'] <= .5
    assert assessment['criteria']['memory_use']['score'] <= .5
    assert assessment['calibration'] == 'not_calibrated'
