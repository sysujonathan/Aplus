import base64
import io
import struct

from PIL import Image
import pytest

from gui.chart_snapshot import png_dib


def test_chart_png_is_a_dib_with_original_dimensions_and_no_file_header():
    image = Image.new('RGB', (320, 180), '#2962ff')
    output = io.BytesIO()
    image.save(output, format='PNG')
    data = png_dib('data:image/png;base64,'+base64.b64encode(output.getvalue()).decode())
    assert struct.unpack_from('<Iii', data) == (40, 320, 180)
    assert data[:2] != b'BM'


@pytest.mark.parametrize('value', ['file:///private.png', 'data:text/plain;base64,QQ==',
    'data:image/png;base64,not-base64'])
def test_snapshot_accepts_only_explicit_png_payloads(value):
    with pytest.raises(ValueError):
        png_dib(value)
