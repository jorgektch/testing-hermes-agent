"""Plain-text SMS projection for the Hermes demo (no carrier is contacted).

Hermes remains responsible for what to say. This module only normalizes
presentation and segments its final text without changing factual content.
"""
from __future__ import annotations

import re

# GSM-7 basic + extension characters. Extension chars consume two septets.
GSM_BASIC = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM_EXT = set("^{}\\[~]|€")


def normalize(text: str) -> str:
    """Remove Markdown syntax; preserve URLs and important original wording."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = text.translate(str.maketrans({'“': '"', '”': '"', '‘': "'", '’': "'", '—': '-', '–': '-', '…': '...', ' ': ' '}))
    # Transform links into visible full URLs; no Markdown link syntax in SMS.
    text = re.sub(r'\[([^\]]+)\]\((https://[^\s)]+)\)', lambda m: f'{m.group(1)}: {m.group(2)}', text)
    text = re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', text)
    text = re.sub(r'(?m)^\s*[-*]\s+', '- ', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'(?<!\w)\*([^*\n]+)\*(?!\w)', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Express square metres without the UCS-2 superscript; meaning is unchanged.
    text = text.replace('m²', 'sq m')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def encoding(text: str) -> str:
    return 'GSM-7' if all(c in GSM_BASIC or c in GSM_EXT for c in text) else 'UCS-2'


def units(text: str, charset: str) -> int:
    if charset == 'GSM-7':
        return sum(2 if c in GSM_EXT else 1 for c in text)
    # SMS UCS-2 counts UTF-16 code units, not Python code points.
    return len(text.encode('utf-16-le')) // 2


def details(text: str) -> dict:
    charset = encoding(text)
    length = units(text, charset)
    single = 160 if charset == 'GSM-7' else 70
    concat = 153 if charset == 'GSM-7' else 67
    if length <= single:
        count = 1
    else:
        count = (length + concat - 1) // concat
    return {'encoding': charset, 'units': length, 'segments': max(1, count),
            'single_limit': single, 'multipart_limit': concat}


# GSM-7 transport fragments (153 characters when concatenated) are NOT
# standalone conversations. They are reassembled by the recipient device.
# One stored message may be two physical SMS segments. A longer Hermes reply
# can be separated into natural, independent messages using real boundaries.
# Never cut a link, measurement, sentence, or field heading in the middle.
_MAX_LOGICAL_SMS_SEGMENTS = 2

# The boundary markers below are anchored to content structure instead of
# arbitrary word counts. Lookbehind deliberately excludes numbered unit list
# markers such as "2. H-204".
_NATURAL_BREAK = re.compile(
    r'\n+'
    r'|(?<!\d\.)(?<=[.!?])\s+(?=[A-Z])'
    r'|\s+(?=\d+\.\s+H-\d+\b)'
    r'|\s+(?=(?:View Gallery|Property Fact Sheet|Floor Plan):\s+https://)'
    r'|\s+(?=Since you\b)',
    re.IGNORECASE,
)
_FIELD_BREAK = re.compile(
    r'\s+-\s+(?=(?:Price|Bedrooms|Bathrooms|Features|Status|Availability|View Gallery|Property Fact Sheet):)',
    re.IGNORECASE,
)


def _natural_parts(plain: str) -> list[str]:
    parts = []
    for phrase in _NATURAL_BREAK.split(plain):
        phrase = phrase.strip(' \n')
        if not phrase:
            continue
        if details(phrase)['segments'] > _MAX_LOGICAL_SMS_SEGMENTS:
            # Field-like labels are a safe fallback for long real-estate lists.
            # Keep every label + its value, e.g. "Gallery: https://...".
            pieces = _FIELD_BREAK.split(phrase)
            if len(pieces) > 1:
                parts.extend(piece.strip(' \n-') for piece in pieces if piece.strip(' \n-'))
                continue
        parts.append(phrase.strip(' \n-'))
    return [p for p in parts if p]


def outgoing_segments(text: str) -> list[str]:
    """Return receiver-visible messages, never carrier-sized word fragments.

    Each item is a coherent message (normally up to two *transport* segments).
    If a single statement or HTTPS link does not have a safe internal boundary,
    preserve it as one multipart message rather than truncating or corrupting
    the content. Use details(message) for the real number of carrier segments.
    """
    plain = normalize(text)
    if not plain:
        raise ValueError('Cannot send an empty SMS.')
    if details(plain)['segments'] == 1:
        return [plain]
    blocks = _natural_parts(plain)
    messages = []
    current = ''
    for block in blocks:
        # Short label-like facts are joined with a separator, never concatenated
        # into an invalid "...Bedrooms: 2 Bathrooms: 2" run-on string.
        if current and block.lower().startswith(('since you ',)):
            messages.append(current)
            current = ''
        joiner = ('; ' if current and ':' in block and not current.endswith(':')
                  and not block.startswith('https://') else ' ')
        candidate = f'{current}{joiner}{block}' if current else block
        if current and details(candidate)['segments'] > _MAX_LOGICAL_SMS_SEGMENTS:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages
