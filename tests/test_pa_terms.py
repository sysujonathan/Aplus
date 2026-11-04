import io

import pytest
from PIL import Image

from workbench.pa_terms import matching, selected, graph
from workbench.store import Store
from gui.growth_bridge import GrowthBridge


def png():
    out = io.BytesIO()
    Image.new('RGB', (32, 24), 'navy').save(out, 'PNG')
    return out.getvalue()


def test_vocabulary_alias_and_graph():
    assert matching('H2') == matching('High 2') == ['brooks-high-2']
    assert matching('GAP H2') == []
    assert 'brooks-breakout' in matching('break')
    with pytest.raises(ValueError):
        selected(['arbitrary'])
    with pytest.raises(ValueError):
        selected([], required=True)
    assert graph([]) == {'nodes': [], 'edges': []}
    g = graph([{'topic_ids': ['brooks-high-2', 'brooks-pullback']},
               {'topic_ids': ['brooks-high-2']}, {'topic_ids': ['cont-h2']}])
    assert {n['id']: n['case_count'] for n in g['nodes']} == {'brooks-high-2': 2, 'brooks-pullback': 1}
    assert g['edges'] == [{'source': 'brooks-high-2', 'target': 'brooks-pullback', 'count': 1}]


def test_existing_schema_and_backup_preserve_terms_and_drafts(tmp_path):
    store = Store(tmp_path / 'one')
    old = store.pa_save_case([{'name': 'old.png', 'data': png()}])['case']
    case = store.pa_save_case([{'name': 'H2.png', 'data': png()}],
                             {'topic_ids': ['brooks-high-2']}, allow_duplicate=True)['case']
    assert store.pa_case(old['id'])['topic_ids'] == []
    assert store.pa_search('h2')[0]['id'] == case['id']
    assert 'Brooks 标准术语' in store.pa_search('High 2')[0]['match_reasons']
    archive = tmp_path / 'backup.zip'
    store.pa_backup(archive)
    restored = Store(tmp_path / 'two')
    restored.pa_restore(archive)
    assert len(restored.pa_search('h2')) == 1
    assert len(restored.pa_search()) == 2
    assert restored.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value'] == '9'


def test_formal_save_requires_term_but_draft_remains_fast(tmp_path):
    bridge = GrowthBridge(Store(tmp_path))
    token = bridge._stage([{'name': 'chart.png', 'data': png()}])['pending'][0]['token']
    assert not bridge.save_capture([token], {}, {'formal': True})['ok']
    assert token in bridge._pending
    assert bridge.save_capture([token], {}, {})['ok']
