import json

import pandas as pd
import pytest

from tests.test_workbench import store
from workbench.security_evidence import load_identity_facts, read_baostock_dated_directory
from workbench.gap_review import review_evidence


def test_bundled_identity_data_preserves_existing_public_facts():
    facts = load_identity_facts()
    assert facts['sh.601313'][0] == '2018-02-28'
    assert facts['sz.002525'][0] == '2011-04-11'
    assert all(value[2].startswith('https://') for value in facts.values())


@pytest.mark.parametrize('change', ['duplicate', 'date', 'url', 'code', 'version'])
def test_invalid_identity_fact_file_rejected(store, change):
    fact = dict(code='sh.600000', effective_from='2026-10-09', reason='test', evidence='https://example.com/evidence')
    payload = dict(version=1, facts=[fact])
    if change=='duplicate': payload['facts'].append(fact.copy())
    if change=='date': fact['effective_from']='2026-13-09'
    if change=='url': fact['evidence']='file:///private'
    if change=='code': fact['code']='../prices.csv'
    if change=='version': payload['version']=2
    path=store.write_artifact('fixture-facts.json', json.dumps(payload).encode())
    with pytest.raises(ValueError): load_identity_facts(path)


@pytest.mark.parametrize('source', ['baostock', 'tickflow', 'tencent'])
def test_exact_baostock_status_origin_never_uses_selected_source_or_other_day(store, source):
    for name, status in [('directories/2026-10-09.csv', '0'),
                         ('sources/tencent/directories/2026-10-09.csv', '1'),
                         ('sources/tickflow/directories/2026-10-09.csv', '1')]:
        store.write_artifact(name, pd.DataFrame(dict(code=['sh.600000'], tradeStatus=[status])).to_csv(index=False).encode())
    ev = review_evidence(store, '2026-10-09', source)
    assert ev['statuses']['sh.600000']['tradeStatus']=='0'
    assert ev['status_path']==str(store.root/'directories/2026-10-09.csv')
    assert read_baostock_dated_directory(store, '2026-10-08')[0]=={}
