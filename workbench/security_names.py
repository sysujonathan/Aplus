"""Single-security display metadata, independent of market/scanning eligibility."""
import re
import urllib.request

from .market import code_of

MAX_RESPONSE = 16384


def parse_security_name(text, code):
    code = code_of(code)
    symbol = code.replace('.', '')
    matches = re.findall(r'v_((?:sh|sz|bj)\d{6})\s*=\s*"([^"\r\n]*)"', text)
    values = [raw.split('~') for key, raw in matches if key == symbol]
    if len(values) != 1 or len(values[0]) < 3 or values[0][2] != code.split('.')[1]:
        raise ValueError('未返回匹配代码的证券名称')
    name = values[0][1].strip()
    if not name or len(name) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError('证券名称为空或格式无效')
    return name


def fetch_security_name(code, *, opener=None):
    """One bounded, direct request; never download history or write a Store."""
    code = code_of(code)
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request('https://qt.gtimg.cn/q=' + code.replace('.', ''),
        headers={'Referer':'https://gu.qq.com/', 'User-Agent':'Mozilla/5.0'})
    with opener.open(request, timeout=8) as response:
        body = response.read(MAX_RESPONSE + 1)
    if len(body) > MAX_RESPONSE:
        raise ValueError('名称查询响应过大')
    return parse_security_name(body.decode('gbk'), code)
