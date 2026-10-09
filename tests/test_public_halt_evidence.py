"""Reviewed whole-market census evidence never waives a resumption-day gap."""
from datetime import date, timedelta

import pandas as pd
import pytest

from tests.test_workbench import store
from workbench.suspensions import PUBLIC_HALTS, announcement_evidence
from workbench.history_quality import describe_history


@pytest.mark.parametrize('code,halt,resume', [
    (code, halt, resume)
    for code in ('sh.600058', 'sh.600707', 'sh.603029', 'sh.603822')
    for halt, resume, _ in PUBLIC_HALTS[code]
])
def test_reviewed_interval_boundaries_and_quality(store, code, halt, resume):
    before = (date.fromisoformat(halt)-timedelta(days=1)).isoformat()
    after = (date.fromisoformat(resume)+timedelta(days=1)).isoformat()
    days, receipts = announcement_evidence(code, before, after)
    assert before not in days and halt in days and resume not in days and after not in days
    assert receipts[0]['halt'] == halt and receipts[0]['resume'] == resume
    assert all(url.startswith('https://') for url in receipts[0]['urls'])
    calendar = dict(start=before, end=after,
                    trading_days=[before, halt, resume, after])
    real = pd.DataFrame(dict(date=[before, resume, after]))
    quality = describe_history(store, code, real, before, after, calendar)
    assert quality['complete'] and not quality['missing_dates']
    # The first real resumed session remains required, despite the announcement.
    missing_resume = describe_history(store, code, real[real.date != resume],
                                      before, after, calendar)
    assert missing_resume['missing_dates'] == [resume]
    # Actual bars on a certified whole-day halt conflict rather than silently pass.
    with pytest.raises(ValueError, match='停牌公告冲突'):
        describe_history(store, code, pd.DataFrame(dict(date=[before, halt, resume, after])),
                         before, after, calendar)


def test_other_unverified_interval_on_same_stock_remains_unknown(store):
    dates = ['2017-04-07', '2017-04-10', '2017-04-11', '2017-04-12']
    calendar = dict(start=dates[0], end=dates[-1], trading_days=dates)
    quality = describe_history(store, 'sh.603822', pd.DataFrame(dict(date=[dates[0], dates[-1]])),
                               dates[0], dates[-1], calendar)
    assert quality['missing_dates'] == dates[1:3] and not quality['complete']


def test_unknown_stock_never_inherits_another_issuers_halt():
    assert announcement_evidence('sh.600686', '2017-12-28', '2018-01-10') == ([], [])
