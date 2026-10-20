import pandas as pd
import pytest

from workbench.chart_indicators import price_source
from workbench.chart_workspace import document, validate_document


@pytest.mark.parametrize('source,expected', [('open',8),('high',14),('low',6),('close',12),
    ('volume',100),('hl2',10),('hlc3',32/3),('ohlc4',10)])
def test_display_price_sources_match_chart_formula_and_survive_validation(source, expected):
    frame = pd.DataFrame(dict(open=[8],high=[14],low=[6],close=[12],volume=[100]))
    assert price_source(frame, source).iloc[0] == pytest.approx(expected)
    value = document();value['settings']['ema_source'] = source
    assert validate_document(value)['settings']['ema_source'] == source


def test_unknown_display_source_is_rejected():
    with pytest.raises(ValueError):
        price_source(pd.DataFrame(), 'invented')
