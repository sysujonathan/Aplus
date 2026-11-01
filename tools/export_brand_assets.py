"""Export the approved-direction vector geometry; never modify runtime icons.

Build-only dependencies: resvg-py==0.5.0 and Pillow. Run from any directory.
"""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, __version__ as pillow_version
import resvg_py

ROOT=Path(__file__).resolve().parents[1]
BRAND=ROOT/'assets/brand'
NS='http://www.w3.org/2000/svg'
ET.register_namespace('',NS)
SIZES=(16,24,32,48,64,128,256,512,1024)
ICO_SIZES=SIZES[:7]
INK='#111214';PAPER='#FFFFFF';ACCENT='#FFA578'


def svg_file(name,viewbox,content):
    # Construct plain XML once, without duplicate namespace declarations.
    svg=ET.Element('svg',dict(viewBox=viewbox,xmlns=NS))
    ET.SubElement(svg,'title').text='APLUS '+name
    svg.extend(content)
    result=ET.tostring(svg,encoding='unicode')
    path=BRAND/(name+'.svg');path.write_text(result+'\n',encoding='utf-8',newline='\n')
    return result


def export():
    source=ET.parse(BRAND/'master.svg').getroot()
    groups={g.attrib['id']:g for g in source.findall('.//{'+NS+'}g') if 'id' in g.attrib}
    def group(kind,color,transform=None,mono=False):
        node=deepcopy(groups[kind]);node.attrib.pop('id',None)
        node.set('fill',color)
        if transform:node.set('transform',transform)
        for element in node.iter():
            # All exported brand geometry is self-contained paths, never text.
            element.tag=element.tag.rsplit('}',1)[-1]
            role=element.attrib.pop('data-role',None)
            if role:element.set('fill',color if mono or role=='ink' else ACCENT)
        return node
    variants={}
    for name,color,mono in [('symbol-light',INK,False),('symbol-dark',PAPER,False),
                             ('symbol-black',INK,True),('symbol-white',PAPER,True)]:
        variants[name]=svg_file(name,'-27 -18 504 504',[group('symbol',color,mono=mono)])
    # Small-size geometry is unchanged; no undocumented alternate optical master.
    rect=ET.Element('rect',dict(width='512',height='512',rx='112',fill=INK))
    variants['app-icon']=svg_file('app-icon','0 0 512 512',
        [rect,group('symbol',PAPER,'translate(101 95) scale(.69)')])
    for theme,color in [('light',INK),('dark',PAPER)]:
        variants['wordmark-'+theme]=svg_file('wordmark-'+theme,'-8 -8 666 110',[group('wordmark',color)])
        variants['lockup-vertical-'+theme]=svg_file('lockup-vertical-'+theme,'0 0 760 700',
            [group('symbol',color,'translate(155 30)'),group('wordmark',color,'translate(55 565)')])
        variants['lockup-horizontal-'+theme]=svg_file('lockup-horizontal-'+theme,'0 0 1020 260',
            [group('symbol',color,'translate(28 16) scale(.48)'),group('wordmark',color,'translate(315 87)')])
    raster=BRAND/'png';raster.mkdir(exist_ok=True)
    for name in ('symbol-light','symbol-dark','symbol-black','symbol-white','app-icon'):
        for size in SIZES:
            (raster/f'{name}-{size}.png').write_bytes(resvg_py.svg_to_bytes(
                svg_string=variants[name],width=size,height=size,skip_system_fonts=True))
    app=Image.open(raster/'app-icon-1024.png')
    app.save(BRAND/'aplus.ico',format='ICO',sizes=[(n,n) for n in ICO_SIZES])
    # Delivery contact sheet from generated vectors only, never from the reference bitmap.
    sheet=Image.new('RGB',(1000,660),'#F4F3EF');draw=ImageDraw.Draw(sheet)
    for x,name,bg in [(30,'symbol-light','#F4F3EF'),(360,'symbol-dark','#15191E'),(690,'app-icon','#F4F3EF')]:
        draw.rounded_rectangle((x,20,x+280,300),radius=20,fill=bg)
        frame=Image.open(io.BytesIO(resvg_py.svg_to_bytes(svg_string=variants[name],width=230,height=230)))
        sheet.paste(frame,(x+25,45),frame)
    lockup=Image.open(io.BytesIO(resvg_py.svg_to_bytes(svg_string=variants['lockup-horizontal-light'],width=700)))
    sheet.paste(lockup,(145,320),lockup)
    draw.text((30,527),'Native sizes: 16 / 24 / 32 / 48 / 64 px (no upscale)',fill=INK)
    x=30
    for size in ICO_SIZES[:5]:
        frame=Image.open(raster/f'app-icon-{size}.png')
        sheet.paste(frame,(x,560),frame);x+=size+30
    sheet.save(BRAND/'preview.png')
    files={str(p.relative_to(BRAND)).replace('\\','/'):{'bytes':p.stat().st_size,
        'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(BRAND.rglob('*'))
        if p.is_file() and p.name!='manifest.json'}
    manifest=dict(asset_version='2026.11.01-v1',status='approved_asset_baseline',
        reference_sha256=['22e1653fb7f4d627673c305ca9b5193204ce1cead702a0823fdf24c6aaddb03c',
                          'f0860ae90a71fb0849403ee46b4ff81286f01d9cb11c9572ef0d298b26e6bbdc'],
        palette=dict(ink=INK,paper=PAPER,apricot=ACCENT),palette_status='approved_by_delegated_review_2026-11-01',
        geometry='Hand reconstructed PA and outlined APLUS from selected reference; no external font',
        tools={'resvg-py':'0.5.0','Pillow':pillow_version},png_sizes=SIZES,ico_sizes=ICO_SIZES,files=files)
    (BRAND/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(f'Exported {len(files)} brand files. Production icon_aplus.ico was not changed.')


if __name__=='__main__':export()
