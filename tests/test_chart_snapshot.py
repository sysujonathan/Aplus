import base64
import io
import struct

from PIL import Image
import pytest

from gui.chart_snapshot import png_dib, save_chart_png


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


def test_png_export_writes_only_to_the_path_selected_in_the_dialog(tmp_path):
    output = io.BytesIO()
    Image.new('RGB', (8, 8), '#2962ff').save(output, format='PNG')
    payload = 'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()
    path = tmp_path / 'explicit.png'
    assert save_chart_png(payload,'Aplus-600000-2026-11-04.png',lambda _:None)=={'ok':False}
    assert not path.exists()
    def choose(name):
        assert name=='Aplus-chart.png'
        return (str(path),)
    assert save_chart_png(payload,'../invalid.png',choose)=={'ok':True}
    assert path.read_bytes()==output.getvalue()
