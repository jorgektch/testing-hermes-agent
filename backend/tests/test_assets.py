from pathlib import Path
from app import domain


ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / 'frontend' / 'public'


def test_all_catalog_media_references_resolve():
    catalog = domain.read_catalog()
    links = [catalog['project_info']['general_brochure_pdf']]
    for unit in catalog['properties']:
        links.extend(unit['media']['gallery_urls'])
        if unit['media']['floorplan_brochure_pdf']:
            links.append(unit['media']['floorplan_brochure_pdf'])
    assert len(links) == 10
    for link in links:
        assert link.startswith('/demo-assets/')
        assert (PUBLIC / link.lstrip('/')).is_file(), link
        assert (PUBLIC / link.lstrip('/')).stat().st_size > 500
