from gui.growth_bridge import GrowthBridge
from workbench.store import Store
from tests.test_pa_shared import Cloud, Drive, make_case


def test_random_qualified_only_and_recent_survives_restart(tmp_path):
    store = Store(tmp_path)
    bridge = GrowthBridge(store)
    bridge._drive = Drive(Cloud(), 'a')
    good = [make_case(store, color)[0] for color in ('red', 'blue', 'green')]
    bad = make_case(store, 'gray')[0]
    owner = store.rows("SELECT value FROM meta WHERE key='pa_local_owner'")[0]['value']
    store.execute('UPDATE meta SET value=? WHERE key=?', ('{broken', f"pa_trial_setup:{owner}:{bad['id']}"))
    chosen = [bridge.random_trial('private')['case_id'] for _ in range(3)]
    assert len(set(chosen)) == 3
    assert set(chosen) == {c['id'] for c in good}
    other = GrowthBridge(Store(tmp_path))
    other._drive = bridge._drive
    assert other.random_trial('private')['case_id'] == chosen[0]
    assert bridge.random_trial('public') == {'ok': True, 'case_id': None, 'eligible': 0}
    assert not bridge.random_trial('other')['ok']


def test_public_random_personal_history_and_contributions_are_separate(tmp_path):
    store = Store(tmp_path)
    a = GrowthBridge(store)
    cloud = Cloud()
    a._drive = Drive(cloud, 'a')
    c, _ = make_case(store)
    published = a.publish_case(c['id'])
    assert published['published']
    assert a.refresh_shared()['ok']
    chosen = a.random_trial('public')
    assert chosen['case_id'] == published['case_id']
    public = a.get_case(chosen['case_id'])['case']
    assert a.save_trial(public['id'], dict(judgment='wait', depth='quick', details={'presentation': public['trial_setup']}))['ok']
    assert a.mark_studied(public['id'])['ok']
    boot = a.bootstrap()
    assert boot['stats']['public_cases'] == boot['stats']['contributions'] == boot['stats']['trials'] == boot['stats']['studied_cases'] == 1
    b = GrowthBridge(Store(tmp_path))
    b._drive = Drive(cloud, 'b')
    assert b.refresh_shared()['ok']
    boot = b.bootstrap()
    assert boot['stats']['public_cases'] == 1
    assert boot['stats']['contributions'] == boot['stats']['trials'] == boot['stats'].get('studied_cases', 0) == 0
    assert not b.get_case(public['id'])['case']['trials']
