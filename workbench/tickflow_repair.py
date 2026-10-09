"""Explicit repair of a missing/corrupt TickFlow file, never provider price mixing."""
from .market import validate_bars, verify_dataset
from .store import digest
from .sources import coverage_state, ADJUSTMENTS


def restore_corrupt_snapshot(store, provider, code, start, end, job=None, source='tickflow'):
    """Restore only bytes provably identical to the registered immutable snapshot.

    Changed provider history cannot silently stand in for a lost old snapshot.
    Preserve damaged bytes for recovery before restoring the original hash.
    """
    states=coverage_state(store,code,source)
    rows=(store.rows('SELECT * FROM datasets WHERE id=?',(states[0]['dataset_id'],)) if states else
          store.rows("SELECT * FROM datasets WHERE source=? AND code=? "
                     "AND adjustment=? AND timeframe='daily' "
                     "ORDER BY end DESC,created DESC,rowid DESC LIMIT 1",(source,code,ADJUSTMENTS[source])))
    if not rows:
        if states:
            raise ValueError('补拉所引用的行情快照不存在；请核对覆盖记录')
        return
    record=rows[0]
    if (record['source']!=source or record['code']!=code or
        record['adjustment']!=ADJUSTMENTS[source] or record['timeframe']!='daily'):
        raise ValueError('补拉快照来源或身份不符，未修改文件')
    path=(store.root/record['path']).resolve()
    if not path.is_relative_to((store.root/'market').resolve()):
        raise ValueError('行情文件路径不属于行情目录，未修改文件')
    try:
        verify_dataset(store,record['id'],record=record)
        return
    except (OSError,ValueError):
        pass
    frame=validate_bars(provider.fetch(code,start,end))
    original=frame[frame.date.between(record['start'],record['end'])]
    content=original.to_csv(index=False,float_format='%.12g',lineterminator='\n').encode('utf-8')
    if digest(content)!=record['sha256']:
        raise ValueError('补拉无法重建原快照校验值；供应商历史可能变化，未覆盖损坏文件，需人工核对')
    if path.is_file():
        damaged=path.read_bytes()
        backup=f"quarantine/{source}/{record['id']}-{digest(damaged)[:16]}.bin"
        if (store.root/backup).exists() and (store.root/backup).read_bytes()!=damaged:
            raise ValueError('损坏文件备份名称冲突，未覆盖文件')
        store.write_artifact(backup,damaged,job)
    else:
        backup=None
    store.write_artifact(record['path'],content,job)
    verify_dataset(store,record['id'],record=record)
    store.event(job,'恢复原校验值一致的行情快照',source=source,dataset=record['id'],backup=backup)
