from tests.test_workbench import store, frame
from workbench.market import save_dataset
from workbench.scope import selected_boards, save_boards, scan_datasets
from workbench.store import Store, dumps, now


def test_scope_inherits_last_sync_and_persists_selection(store, frame):
    for code in ['sh.600000','sz.000001','sz.300750','sh.688001']:
        save_dataset(store,code,frame,'baostock','前复权')
    store.execute('INSERT INTO jobs(id,kind,status,created,spec) VALUES(?,?,?,?,?)',
                  ('sync','sync','completed',now(),dumps({'boards':['沪深主板']})))
    assert selected_boards(store)==['沪深主板']
    assert {r['code'] for r in scan_datasets(store,'baostock')}=={'sh.600000','sz.000001'}
    save_boards(store,['创业板'])
    reopened=Store(store.root)
    assert [r['code'] for r in scan_datasets(reopened,'baostock')]==['sz.300750']
    save_boards(reopened,[])
    assert scan_datasets(reopened,'baostock')==[]
