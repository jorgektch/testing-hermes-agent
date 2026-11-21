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


def _chunks(text: str, max_units: int, charset: str) -> list[str]:
    """Word-aware chunks; do not split an HTTPS link or lose any words."""
    paragraphs = re.split(r'\s+', text.strip())
    parts: list[str] = []
    current = ''
    for word in paragraphs:
        if not word:
            continue
        candidate = f'{current} {word}' if current else word
        if units(candidate, charset) <= max_units:
            current = candidate
            continue
        if current:
            parts.append(current)
            current = ''
        if units(word, charset) <= max_units:
            current = word
        elif word.startswith('https://'):
            # Splitting an URL would make it unusable; reject as an explicit
            # validation error rather than shipping a broken link.
            raise ValueError('Approved link exceeds one SMS segment.')
        else:
            chunk = ''
            for char in word:
                if units(chunk + char, charset) > max_units:
                    parts.append(chunk)
                    chunk = char
                else:
                    chunk += char
            current = chunk
    if current:
        parts.append(current)
    return parts


def outgoing_segments(text: str) -> list[str]:
    plain = normalize(text)
    if not plain:
        raise ValueError('Cannot send an empty SMS.')
    charset = encoding(plain)
    # Each outgoing bubble is at most one SMS segment. If the original response
    # requires multipart, use the conservative concatenated-segment budget.
    single = 160 if charset == 'GSM-7' else 70
    if units(plain, charset) <= single:
        return [plain]
    return _chunks(plain, 153 if charset == 'GSM-7' else 67, charset)
