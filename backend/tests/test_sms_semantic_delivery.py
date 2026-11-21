"""SMS message vs. GSM transport segment regression tests."""
from app.sms import details, encoding, outgoing_segments


def test_gsm_segment_metrics_are_not_chat_bubbles():
    paragraph = 'A 2-bedroom house is offered for $245,000. ' * 6
    messages = outgoing_segments(paragraph)
    assert any(details(m)['segments'] > 1 for m in messages)
    assert len(messages) < sum(details(m)['segments'] for m in messages)
    assert all(details(m)['segments'] <= 2 for m in messages)


def test_never_cut_listing_spec_or_urls():
    src = (
        'I found two available houses that match your criteria: '
        '1. H-201 - Two-Bedroom Garden House - Price: $245,000 - Bedrooms: 2 - '
        'Bathrooms: 2 - Features: Open-plan kitchen, private backyard (18 m²), '
        '1 parking space - Status: Available for immediate move-in - '
        'View Gallery: https://demo.example.test/properties/H-201/gallery - '
        'Property Fact Sheet: https://demo.example.test/demo-assets/BP-H201-factsheet.pdf '
        '2. H-204 - Three-Bedroom Courtyard House - Price: $295,000 - Bedrooms: 3 - '
        'Bathrooms: 2 - Features: Open-plan kitchen, private courtyard (20 m²), '
        '1 parking space - Status: Available from December 15, 2026 - '
        'View Gallery: https://demo.example.test/properties/H-204/gallery - '
        'Property Fact Sheet: https://demo.example.test/demo-assets/BP-H204-factsheet.pdf '
        'Since you prefer a 2-bedroom house, H-201 would be the best fit. '
        'Would you like more details or to discuss visiting this property?'
    )
    messages = outgoing_segments(src)
    assert 2 <= len(messages) <= 6
    assert all(details(m)['segments'] <= 2 for m in messages)
    assert all('m²' not in m for m in messages)
    assert '18 sq m' in ' '.join(messages)
    assert all(not m.endswith(('18', 'View', 'Fact Sheet:',' -')) for m in messages)
    for url in (
        'https://demo.example.test/properties/H-201/gallery',
        'https://demo.example.test/demo-assets/BP-H201-factsheet.pdf',
        'https://demo.example.test/properties/H-204/gallery',
        'https://demo.example.test/demo-assets/BP-H204-factsheet.pdf',
    ):
        assert sum(url in m for m in messages) == 1
    assert any('Since you prefer' in m and 'Would you like' in m for m in messages)


def test_plain_short_answer_is_one_sms():
    assert outgoing_segments('The H-201 is $245,000. Would you like a visit?') == [
        'The H-201 is $245,000. Would you like a visit?'
    ]


def test_long_indivisible_url_is_not_cut():
    url='https://demo.example.test/gallery/'+('a'*325)
    messages=outgoing_segments(f'Gallery: {url}')
    assert len(messages) == 1
    assert messages[0].endswith(url)
    assert details(messages[0])['segments'] >= 3


def test_markdown_url_becomes_plain_complete_url():
    url='https://demo.example.test/gallery/H-201'
    chunks=outgoing_segments('Photos: [View gallery]('+url+')')
    assert chunks == ['Photos: View gallery: '+url]


def test_unicode_uses_utf16_length_and_square_meter_normalization():
    assert details('👋')['encoding'] == 'UCS-2'
    assert details('👋')['units'] == 2
    assert encoding(outgoing_segments('Area: 18 m².')[0]) == 'GSM-7'
