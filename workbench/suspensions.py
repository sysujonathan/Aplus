"""Reviewed public announcements, not inferred halts or synthetic price bars.

This small evidence catalogue is deliberately NOT a whole-market halt service.
Unknown gaps continue to fail validation. Dates use [halt, resume) semantics.
"""
from datetime import date, timedelta
import hashlib
import json

CNINFO = 'https://static.cninfo.com.cn/finalpage/'
ZMD = 'https://www.zmd.com.cn/mtsc/uploads/StockExchangeFile/'
PUBLIC_HALTS = {
    'sh.603920': [
        ('2023-11-16','2023-11-21',[
            'https://epaper.stcn.com/con/202311/21/content_2557440.html',
            'https://epaper.stcn.com/att/202311/21/68835331-3e57-483b-928c-5ab791f3ee22.pdf']),
    ],
    'sh.601198': [
        ('2025-11-20','2025-12-18',[CNINFO+'2025-12-18/1224883494.PDF']),
    ],
    'sz.000906': [
        ('2017-11-10','2017-11-14',[ZMD+'20240112164816888.PDF']),
        ('2019-10-14','2019-10-28',[CNINFO+'2019-10-26/1207022236.PDF']),
        ('2020-06-15','2020-06-22',[ZMD+'20240112132526524.PDF']),
        ('2021-03-15','2021-03-22',[CNINFO+'2021-03-20/1209418870.PDF']),
        ('2021-05-18','2021-05-24',[CNINFO+'2021-05-18/1209996021.PDF',CNINFO+'2021-05-24/1210047115.PDF']),
    ],
    'sz.002259': [
        ('2016-11-07','2016-12-29',[CNINFO+'2016-12-29/1202970651.PDF']),
        ('2017-09-05','2017-09-19',[CNINFO+'2017-09-19/1203981994.PDF']),
        ('2018-01-22','2018-06-15',[CNINFO+'2018-03-13/1204469547.PDF',CNINFO+'2018-06-15/1205060952.PDF']),
        ('2018-10-08','2018-10-09',[CNINFO+'2018-10-08/1205479283.PDF']),
        ('2019-04-30','2019-05-06',['https://disc.static.szse.cn/download/disc/disk01/finalpage/2019-04-30/433a7303-a1b2-4c93-9335-3302c092204c.PDF']),
        ('2021-09-23','2021-09-24',['https://disc.static.szse.cn/download/disc/disk02/finalpage/2021-09-23/29138e5f-8129-4002-983b-dff56c27ded5.PDF']),
        ('2025-05-19','2025-05-20',[CNINFO+'2025-05-19/1223573337.PDF']),
    ],
}

# The issuer explicitly states no resumption before delisting, not a guessed
# open-ended interval. Do not extend an ordinary temporary halt this way.
NO_RESUMPTION = {'sh.601198': ('2026-09-15', CNINFO+'2026-09-08/1225552616.PDF')}
EVIDENCE_VERSION = hashlib.sha256(json.dumps([PUBLIC_HALTS,NO_RESUMPTION],sort_keys=True).encode()).hexdigest()[:16]


def announcement_evidence(code, start, end):
    days, receipts = set(), []
    for halt, resume, urls in PUBLIC_HALTS.get(code,[]):
        if halt > end or resume <= start:
            continue
        current = date.fromisoformat(max(halt,start))
        last = min(date.fromisoformat(resume)-timedelta(days=1),date.fromisoformat(end))
        while current <= last:
            days.add(current.isoformat())
            current += timedelta(days=1)
        receipts.append(dict(halt=halt,resume=resume,urls=list(urls),verified='2026-10-09'))
    if code in NO_RESUMPTION:
        halt, url = NO_RESUMPTION[code]
        if halt <= end:
            current = date.fromisoformat(max(halt,start))
            while current <= date.fromisoformat(end):
                days.add(current.isoformat())
                current += timedelta(days=1)
            receipts.append(dict(halt=halt,resume=None,urls=[url],verified='2026-10-09',
                                 no_resumption=True))
    return sorted(days), receipts
