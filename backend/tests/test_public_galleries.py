"""Unit media URL and single-role access contract."""
import os
import re
from pathlib import Path

from app import domain

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / 'frontend' / 'public'


def test_gallery_url_matches_approved_images():
    catalog = domain.read_catalog()
    expected = {'A-201': 2, 'A-204': 2, 'H-101': 0, 'H-104': 2}
    for unit in catalog['properties']:
        media = unit['media']
        count = len(media['gallery_urls'])
        assert count == expected[unit['unit_id']]
        assert media['gallery_url'] == (f"/properties/{unit['unit_id']}/gallery" if count else None)
        for path in media['gallery_urls']:
            assert re.fullmatch(r'/demo-assets/[A-Za-z0-9_.-]+\.svg', path)
            assert (PUBLIC / path.lstrip('/')).is_file()


def test_only_unit_scoped_pdfs_are_published():
    catalog = domain.read_catalog()
    project_brochure = catalog['project_info']['general_brochure_pdf']
    for unit in catalog['properties']:
        media = unit['media']
        documents = media['documents']
        assert (bool(documents)) == bool(media['floorplan_brochure_pdf'])
        for doc in documents:
            assert doc['scope'] == 'unit'
            assert doc['unit_id'] == unit['blackpenguin_unit_id']
            assert doc['url'].endswith('.pdf')
            assert doc['url'] != project_brochure
            assert (PUBLIC / doc['url'].lstrip('/')).is_file()
    assert not next(unit for unit in catalog['properties'] if unit['unit_id'] == 'A-204')['media']['documents']


def test_hermes_media_tool_only_returns_unit_files_and_single_url(monkeypatch):
    monkeypatch.setenv('DEMO_PUBLIC_BASE_URL', 'https://gallery.example.test')
    data = domain.unit_media('A-201')
    assert data['gallery_url'] == 'https://gallery.example.test/properties/A-201/gallery'
    assert data['image_count'] == 2
    assert 'gallery_urls' not in data  # Hermes must share the consolidated viewer.
    assert len(data['documents']) == 1
    assert data['documents'][0]['url'].endswith('/BP-A201-floorplan.pdf')
    assert all('bp-project-overview' not in str(value) for value in data.values())
    no_pdf = domain.unit_media('A-204')
    assert no_pdf['documents'] == [] and not no_pdf['unit_pdf_available']
    no_photo = domain.unit_media('H-101')
    assert no_photo['gallery_url'] is None and not no_photo['gallery_available']
    assert 'error' in domain.unit_media('invalid-unit')


def test_public_gallery_does_not_require_login_and_never_lists_other_units():
    page = (ROOT / 'frontend' / 'src' / 'app' / 'properties' / '[unitId]' / 'gallery' / 'page.tsx').read_text()
    assert "fetch('/api/catalog'" in page
    assert 'find(item => item.unit_id.toLowerCase()' in page
    assert 'approvedImage' in page
    assert '/api/auth' not in page
    modal = (ROOT / 'frontend' / 'src' / 'app' / 'page.tsx').read_text()
    assert 'View gallery (' in modal
    assert 'project.general_brochure_pdf&&' not in modal
    assert 'Image {i+1}' not in modal
