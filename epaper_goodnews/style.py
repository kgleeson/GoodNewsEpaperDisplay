"""House illustration style, applied to every generated image regardless of subject.

The look is reproduced from images gpt-image-1-mini made in late 2025. Newer models default to
polished, detailed rendering, so the style is pinned two ways: a concrete written description
and, when enabled, a few of those original images sent as style references.
"""

from __future__ import annotations

from pathlib import Path

REFERENCE_DIR = Path(__file__).parent / "style_refs"

STYLE_PROMPT = (
    "STYLE: a hand-painted watercolour-and-ink editorial cartoon — warm, colourful and slightly naive, like an "
    "illustration scanned from an old European children's magazine or illustrated newspaper. Not a polished "
    "modern picture book. "
    "PAINT: chunky, expressive watercolour and gouache washes with mottled, uneven pigment, slightly rough "
    "brushwork, dry-brush edges and organic colour bleeding; visible ivory paper grain; the painting fades out "
    "into an irregular, unpainted ivory border at the edges. "
    "LINES: slightly irregular hand-drawn outlines in a darker shade of each shape's own colour — dark green "
    "around a green jumper, deep orange-brown around skin, dark red around a red top. Not uniform black, not "
    "crisp or perfect. "
    "PEOPLE: friendly, simplified cartoon people with rounded faces, small expressive eyes made of a dark dot or "
    "short stroke, simple noses and mouths, rosy cheeks, slightly exaggerated proportions and charming "
    "imperfections. Faces read clearly but are drawn with just a few marks — not detailed, not anatomically "
    "realistic, not Disney or Pixar, not chibi. "
    "COLOUR: strong, cheerful colour — warm golden yellow, burnt orange, terracotta red, leafy green, turquoise, "
    "dusty teal and creamy ivory; warm yellow-orange washes balanced by cooler turquoise and green areas. Not pale, "
    "not pastel. "
    "LIGHT: no realistic or cinematic lighting, no smooth gradients or airbrushing, no 3D rendering; shadows are "
    "just a darker wash. "
    "COMPOSITION: a compact, lively scene with large, recognisable foreground subjects, a loosely painted "
    "background and slightly flattened perspective; simple, readable storytelling. "
    "AVOID: delicate washed-out watercolour, polished children's-book or Disney/Pixar rendering, vector art, "
    "photorealism, detailed faces, cinematic lighting and the generic AI-illustration look. Its charm is its "
    "simplicity and imperfection — do not make it more refined. "
)

# Practical limits of the 800x480 7-colour panel, learned from calibration.
DISPLAY_NOTES = (
    "DISPLAY: it is shown small, on an 800x480 colour e-paper screen. Keep silhouettes clear, important subjects "
    "large and distinct from the background, and shapes simple rather than finely detailed; avoid small, dark "
    "objects with fine detail or emblems. Red and orange look almost identical on this screen and skin is "
    "orange-pink, so don't put red or orange clothing, scarves or hair directly against a face. "
    "Text is fine only where it naturally belongs in the scene (a sign, a cheque, a shirt); never add a "
    "headline, caption or title over the image."
)

REFERENCE_PREAMBLE = (
    "The attached images are STYLE REFERENCES ONLY. Paint a completely new illustration of the subject described "
    "below in exactly their visual treatment — the same paint handling, outlines, people and faces, colour, paper "
    "and level of detail. Do not copy their subjects, people, text, layout or composition. "
)


def reference_images() -> list[Path]:
    """The style reference images shipped with the package, in a stable order."""
    return sorted(REFERENCE_DIR.glob("*.png"))
