"""Small Polish/English prompt parsing helpers (numbers, dimensions, colours, counts)."""
from __future__ import annotations

import re
import unicodedata

NUM = r"(\d+(?:[.,]\d+)?)"
WORD_NUM = {"jeden": 1, "jedna": 1, "jedno": 1, "one": 1, "dwa": 2, "dwie": 2, "dwoma": 2, "two": 2, "trzy": 3,
            "three": 3, "cztery": 4, "czterema": 4, "four": 4, "pięć": 5, "piec": 5, "five": 5, "sześć": 6,
            "szesc": 6, "six": 6, "osiem": 8, "ośmioma": 8, "eight": 8}

COLORS = {
    # stem: hex
    "czerwon": "#c0121c", "red": "#c0121c", "bordow": "#6d0f1a", "burgund": "#6d0f1a",
    "czarn": "#121214", "black": "#121214", "biał": "#f2f2f0", "bial": "#f2f2f0", "white": "#f2f2f0",
    "niebiesk": "#1f5fbf", "blue": "#1f5fbf", "granat": "#1b2a4a", "navy": "#1b2a4a", "błękit": "#6fb3e8",
    "zielon": "#2f8a3a", "green": "#2f8a3a", "żółt": "#f2c014", "zolt": "#f2c014", "yellow": "#f2c014",
    "pomarańcz": "#f07018", "pomarancz": "#f07018", "orange": "#f07018", "fiolet": "#6a3bb5", "purple": "#6a3bb5",
    "różow": "#e86aa8", "rozow": "#e86aa8", "pink": "#e86aa8", "szar": "#7a7f86", "grey": "#7a7f86", "gray": "#7a7f86",
    "grafit": "#2b2f36", "graphite": "#2b2f36", "srebrn": "#b9bec4", "silver": "#b9bec4", "złot": "#d4a93a",
    "zlot": "#d4a93a", "gold": "#d4a93a", "brąz": "#6b4226", "braz": "#6b4226", "brown": "#6b4226",
    "turkus": "#1fb5ad", "teal": "#1fb5ad", "beż": "#d8c3a0", "bez": "#d8c3a0", "beige": "#d8c3a0",
    "limonk": "#9bd22a", "lime": "#9bd22a", "miedzian": "#b8733a", "copper": "#b8733a",
}
DARK = ("ciemn", "dark", "głębok")
LIGHT = ("jasn", "light", "pastel")


def norm(s: str) -> str:
    return unicodedata.normalize("NFC", s.lower())


def to_f(s: str) -> float:
    return float(s.replace(",", "."))


def shade(hex_: str, f: float) -> str:
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    if f < 1:
        r, g, b = (int(c * f) for c in (r, g, b))
    else:
        r, g, b = (int(c + (255 - c) * (f - 1)) for c in (r, g, b))
    return "#%02x%02x%02x" % (r, g, b)


def find_colors(text: str) -> list[tuple[int, str]]:
    """[(position, hex)] for every colour word, with dark/light modifiers applied."""
    t = norm(text)
    out = []
    for m in re.finditer(r"[\wąćęłńóśźż#]+", t):
        w = m.group(0)
        if re.fullmatch(r"#[0-9a-f]{6}", w):
            out.append((m.start(), w))
            continue
        for stem, hx in COLORS.items():
            if w.startswith(stem):
                before = t[max(0, m.start() - 14):m.start()]
                if any(d in before for d in DARK):
                    hx = shade(hx, 0.55)
                elif any(lt in before for lt in LIGHT):
                    hx = shade(hx, 1.45)
                out.append((m.start(), hx))
                break
    return out


def color_near(text: str, keywords: tuple[str, ...], window: int = 40) -> str | None:
    t = norm(text)
    cols = find_colors(t)
    best = None
    for kw in keywords:
        for m in re.finditer(kw, t):
            for pos, hx in cols:
                d = abs(pos - m.start())
                if d <= window and (best is None or d < best[0]):
                    best = (d, hx)
    return best[1] if best else None


def first_color(text: str) -> str | None:
    c = find_colors(text)
    return c[0][1] if c else None


def dims3(text: str) -> tuple[float, float, float] | None:
    t = norm(text)
    m = re.search(NUM + r"\s*(?:mm)?\s*[x×*]\s*" + NUM + r"\s*(?:mm)?\s*[x×*]\s*" + NUM, t) or \
        re.search(NUM + r"\s*na\s*" + NUM + r"\s*na\s*" + NUM, t)
    if not m:
        return None
    f = 10.0 if re.search(r"\d\s*cm\b", t) else 1.0
    return tuple(to_f(g) * f for g in m.groups())  # type: ignore[return-value]


def dims2(text: str) -> tuple[float, float] | None:
    m = re.search(NUM + r"\s*(?:mm)?\s*[x×*]\s*" + NUM, norm(text))
    return (to_f(m.group(1)), to_f(m.group(2))) if m else None


def mm_after(text: str, pattern: str) -> float | None:
    m = re.search(pattern + r"[^\d]{0,25}?" + NUM + r"\s*(mm|cm|m)?\b", norm(text))
    if not m:
        return None
    v = to_f(m.group(1))
    return v * {"cm": 10, "m": 1000}.get(m.group(2) or "", 1)


def count_of(text: str, noun: str) -> int | None:
    t = norm(text)
    m = re.search(r"(\d+|" + "|".join(WORD_NUM) + r")\s+(?:\w+\s+){0,2}" + noun, t)
    if not m:
        return None
    g = m.group(1)
    return int(g) if g.isdigit() else WORD_NUM[g]


def has(text: str, *words: str) -> bool:
    t = norm(text)
    return any(re.search(w, t) for w in words)


def delta_mm(text: str) -> tuple[str, float] | None:
    """('inc'|'dec'|'set', value_mm) for edits like 'zwiększ wysokość o 5 mm' / 'ustaw na 40 mm'."""
    t = norm(text)
    m = re.search(r"\bo\s+" + NUM + r"\s*(mm|cm)?", t) or re.search(r"\bby\s+" + NUM + r"\s*(mm|cm)?", t)
    if m:
        v = to_f(m.group(1)) * (10 if m.group(2) == "cm" else 1)
        if has(t, "zmniejsz", "obniż", "skróć", "zwęż", "decrease", "reduce", "lower", "shorten", "mniejsz"):
            return ("dec", v)
        return ("inc", v)
    m = re.search(r"(?:\bna\b|\bdo\b|\bto\b|=)\s*" + NUM + r"\s*(mm|cm)?", t)
    if m:
        return ("set", to_f(m.group(1)) * (10 if m.group(2) == "cm" else 1))
    return None
