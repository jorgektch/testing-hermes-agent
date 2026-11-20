"""Create honest synthetic illustrations and valid PDF fact sheets (not real photos)."""
from __future__ import annotations
import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / 'shared' / 'catalog.json').read_text())
OUT = ROOT / 'frontend' / 'public' / 'demo-assets'
OUT.mkdir(parents=True, exist_ok=True)
PALETTES = [('#263f4a', '#5caea0', '#d4d7bd'), ('#3f5556', '#d0aa77', '#b9d0c7'),
            ('#453d4e', '#ae8da4', '#ddd1c3'), ('#314840', '#86ab8e', '#e4c9a2'),
            ('#42504b', '#76b8b4', '#ded6bb'), ('#3f514d', '#c9a86c', '#d8d8bb')]


def svg_for(unit: dict, variant: int, ordinal: int) -> str:
    bg, accent, warm = PALETTES[(ordinal * 2 + variant) % len(PALETTES)]
    code = escape(unit['unit_id'])
    label = escape(unit['commercial_name'].upper()[:32])
    if unit['property_type'] == 'Apartment':
        architecture = f'''
          <rect x="180" y="106" width="590" height="427" rx="4" fill="#162a33" stroke="{warm}" stroke-opacity=".35" stroke-width="4"/>
          <rect x="230" y="70" width="500" height="40" fill="#1e3039"/>
          <rect x="217" y="134" width="22" height="360" fill="{accent}" opacity=".36"/>
          <rect x="709" y="134" width="22" height="360" fill="{accent}" opacity=".36"/>
          {''.join(f'<rect x="{290 + c * 133}" y="{160 + r * 108}" width="91" height="76" rx="2" fill="{warm}" opacity="{.8 if (c+r)%2==0 else .45}"/>' for r in range(3) for c in range(3))}
          <rect x="418" y="427" width="117" height="107" fill="#31544f" stroke="{warm}" stroke-opacity=".6"/>
          <path d="M180 290h590M180 398h590" stroke="{accent}" stroke-opacity=".6" stroke-width="5"/>
        '''
    else:
        architecture = f'''
          <path d="M123 258L482 96 854 258" fill="#263e40" stroke="{warm}" stroke-width="12" stroke-linejoin="round"/>
          <rect x="163" y="258" width="652" height="293" fill="#283b40" stroke="{warm}" stroke-opacity=".45" stroke-width="5"/>
          <rect x="208" y="306" width="140" height="168" fill="{accent}" opacity=".72"/>
          <rect x="408" y="306" width="140" height="168" fill="{warm}" opacity=".63"/>
          <rect x="611" y="309" width="126" height="242" fill="#152c31" stroke="{accent}" stroke-width="7"/>
          <path d="M163 463h652" stroke="{warm}" stroke-opacity=".5" stroke-width="8"/>
          <circle cx="120" cy="491" r="79" fill="{accent}" opacity=".45"/>
          <circle cx="859" cy="499" r="92" fill="{accent}" opacity=".37"/>
        '''
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" viewBox="0 0 1200 760" role="img" aria-label="Synthetic architectural concept illustration, not a property photo">
    <defs><linearGradient id="sky" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{bg}"/><stop offset="1" stop-color="#111f29"/></linearGradient>
    <pattern id="grid" width="58" height="58" patternUnits="userSpaceOnUse"><path d="M58 0H0V58" fill="none" stroke="white" stroke-opacity=".07"/></pattern></defs>
    <rect width="1200" height="760" fill="url(#sky)"/><rect width="1200" height="760" fill="url(#grid)"/>
    <circle cx="984" cy="112" r="174" fill="{accent}" opacity=".10"/>
    <g transform="translate({30 if variant == 0 else 68} 2) scale(.95)">{architecture}</g>
    <rect x="0" y="620" width="1200" height="140" fill="#101a22" fill-opacity=".95"/>
    <text x="67" y="674" fill="{warm}" font-family="Arial,sans-serif" font-weight="bold" font-size="29" letter-spacing="2">{label}</text>
    <text x="68" y="710" fill="{accent}" font-family="Arial,sans-serif" font-size="17" letter-spacing="3">{code}   /   SYNTHETIC CONCEPT {variant + 1}   /   NOT A REAL PHOTO</text>
    <text x="1034" y="78" text-anchor="middle" fill="#d7eee5" opacity=".7" font-family="Arial,sans-serif" font-size="16">EVERGREEN PARK</text></svg>'''


def build_pdf(file: Path, title: str, subtitle: str, rows: list[tuple[str, str]], highlights: list[str]):
    doc = SimpleDocTemplate(str(file), pagesize=letter, topMargin=58, bottomMargin=54,
                            leftMargin=52, rightMargin=52, title=title)
    styles = {
        'title': ParagraphStyle('Title', fontName='Helvetica-Bold', fontSize=23, leading=28, textColor=colors.HexColor('#16382d'), spaceAfter=11),
        'body': ParagraphStyle('Body', fontName='Helvetica', fontSize=10, leading=16, textColor=colors.HexColor('#3c5358')),
        'label': ParagraphStyle('Label', fontName='Helvetica-Bold', fontSize=10, leading=16, textColor=colors.HexColor('#193e33')),
    }
    blocks = [Paragraph('EVERGREEN PARK RESIDENCES', styles['label']), Spacer(1, 22),
              Paragraph(escape(title), styles['title']), Paragraph(escape(subtitle), styles['body']), Spacer(1, 28),
              HRFlowable(width='100%', thickness=1, color=colors.HexColor('#8bbca4')), Spacer(1, 20)]
    table = Table([[k, str(v)] for k,v in rows], colWidths=[175, 300], hAlign='LEFT')
    table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'), ('FONTNAME', (1,0), (1,-1), 'Helvetica'),
        ('FONTSIZE',(0,0),(-1,-1),10),('TEXTCOLOR',(0,0),(0,-1),colors.HexColor('#365f51')),
        ('TEXTCOLOR',(1,0),(1,-1),colors.HexColor('#253942')),('ROWBACKGROUNDS',(0,0),(-1,-1),[colors.HexColor('#f0f5f2'),colors.white]),
        ('BOTTOMPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,-1),12),
    ]))
    blocks += [table, Spacer(1, 24), Paragraph('KEY HIGHLIGHTS', styles['label']), Spacer(1, 7)]
    for highlight in highlights:
        blocks += [Paragraph('&bull; ' + escape(highlight), styles['body']), Spacer(1, 5)]
    blocks += [Spacer(1, 36), HRFlowable(width='100%', thickness=1, color=colors.HexColor('#d5e4db')), Spacer(1, 12),
               Paragraph('<b>DEMO ONLY</b> — Fictitious project, synthetic descriptions and illustration. '
                         'This document is not a property floor plan, legal offer, or construction drawing. '
                         'Financing options are only discussed with a human advisor during an onsite visit.', styles['body'])]
    doc.build(blocks)


for i, unit in enumerate(CATALOG['properties']):
    for j, gallery in enumerate(unit['media']['gallery_urls']):
        (OUT / Path(gallery).name).write_text(svg_for(unit,j,i), encoding='utf-8')
    media_pdf = unit['media']['floorplan_brochure_pdf']
    if media_pdf:
        build_pdf(OUT / Path(media_pdf).name, unit['commercial_name'],
                  f"Synthetic information sheet · {unit['unit_id']} · This is not a construction plan.",
                  [('Unit ID', unit['unit_id']), ('Property type', unit['property_type']),
                   ('Bedrooms', str(unit['bedrooms'])), ('Bathrooms', str(unit['bathrooms'])),
                   ('Built area', f"{unit['area_sqm']} m²"), ('Price (USD)', f"${unit['price_usd']:,}"),
                   ('Available', unit['available_from_date']), ('Listing status', unit['status'])],
                  unit['highlights'])
project = CATALOG['project_info']
build_pdf(OUT / Path(project['general_brochure_pdf']).name,
          project['name'], 'Synthetic project information brochure',
          [('Developer', project['developer']),
           ('Location', f"{project['location']['district']}, {project['location']['city']}, {project['location']['country']}"),
           ('Constructed homes', str(len(CATALOG['properties']))),
           ('Sales advisors', str(len(CATALOG['sales_advisors'])))],
          project['amenities'])
print('Assets generated:', *sorted(p.name for p in OUT.iterdir()), sep='\n  ')
