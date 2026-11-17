"""Preview renderer.

Draws a simple SVG of the garment in its fabric colour. It stands in for a
real image model so the queue, budget, reuse and provenance can be shown end
to end without paying for GPU time. Swapping in a real model means replacing
`render` and keeping everything around it.
"""
import random
from html import escape

SILHOUETTES = {
    "tee": "M150 120 L205 100 Q240 125 275 100 L330 120 L370 190 L330 210 L320 185 L320 420 L160 420 L160 185 L150 210 L110 190 Z",
    "shirt": "M150 115 L210 95 L240 120 L270 95 L330 115 L385 330 L350 340 L320 200 L320 430 L160 430 L160 200 L130 340 L95 330 Z",
    "dress": "M185 110 L215 100 Q240 130 265 100 L295 110 L300 190 Q290 230 300 260 L360 470 L120 470 L180 260 Q190 230 180 190 Z",
    "trousers": "M165 100 L315 100 L330 470 L262 470 L240 210 L218 470 L150 470 Z",
}


def _shade(hex_colour: str, amount: float) -> str:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    f = lambda c: max(0, min(255, int(c + (255 - c) * amount if amount > 0 else c * (1 + amount))))
    return f"#{f(r):02x}{f(g):02x}{f(b):02x}"


def _details(garment: str, features: set[str], line: str) -> str:
    out = []
    if garment == "shirt":
        out.append(f'<line x1="240" y1="120" x2="240" y2="430" stroke="{line}" stroke-width="2"/>')
        out += [f'<circle cx="246" cy="{150 + i * 40}" r="3.5" fill="{line}"/>' for i in range(7)]
    if garment == "trousers":
        out.append(f'<rect x="165" y="100" width="150" height="22" fill="none" stroke="{line}" stroke-width="2"/>')
        if "zip_fly" in features:
            out.append(f'<path d="M240 122 L240 190 Q232 200 226 190" fill="none" stroke="{line}" stroke-width="2"/>')
        if "belt_loops" in features:
            out += [f'<rect x="{x}" y="96" width="6" height="30" fill="{line}"/>' for x in (180, 215, 260, 295)]
    if "chest_pocket" in features:
        out.append(f'<rect x="185" y="170" width="38" height="44" fill="none" stroke="{line}" stroke-width="2"/>')
    if "back_zip" in features:
        out.append(f'<line x1="240" y1="112" x2="240" y2="260" stroke="{line}" stroke-width="2" stroke-dasharray="4 3"/>')
    if "pockets" in features:
        out += [f'<path d="M{x} 280 q8 30 0 50" fill="none" stroke="{line}" stroke-width="2"/>' for x in (190, 290)]
    if "v_neck" in features:
        out.append(f'<path d="M210 103 L240 150 L270 103" fill="none" stroke="{line}" stroke-width="2"/>')
    return "".join(out)


def render(garment: str, fabric_colour: str, features: list[str], shot: str, seed: int) -> str:
    rnd = random.Random(seed)
    fset = set(features)
    path = SILHOUETTES.get(garment, SILHOUETTES["tee"])
    if garment == "tee" and "long_sleeve" in fset:
        path = "M150 120 L205 100 Q240 125 275 100 L330 120 L380 380 L345 388 L320 190 L320 420 L160 420 L160 190 L135 388 L100 380 Z"
    line = _shade(fabric_colour, -0.45)
    bg = {"flat_lay": "#efe9df", "on_model": "#e7e4ee", "detail": "#f3efe7"}.get(shot, "#efe9df")
    tilt = rnd.uniform(-6, 6) if shot == "flat_lay" else 0
    garment_svg = (f'<g transform="rotate({tilt:.1f} 240 290)">'
                   f'<path d="{path}" fill="{fabric_colour}" stroke="{line}" stroke-width="2.5" stroke-linejoin="round"/>'
                   f'{_details(garment, fset, line)}</g>')
    figure = ""
    if shot == "on_model":
        skin = rnd.choice(["#8d5a3b", "#c58c64", "#e2b892", "#5b3a29"])
        figure = (f'<circle cx="240" cy="62" r="34" fill="{skin}"/>'
                  f'<rect x="228" y="90" width="24" height="18" fill="{skin}"/>')
        if garment == "trousers":
            figure += f'<rect x="170" y="40" width="140" height="64" rx="20" fill="#f6f4f0" stroke="#ccc"/>'
    view = "150 130 180 225" if shot == "detail" else "0 0 480 600"
    weave = "".join(
        f'<line x1="0" y1="{y}" x2="480" y2="{y}" stroke="#000" stroke-opacity="0.025"/>' for y in range(0, 600, 6)
    )
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" width="480" height="600">'
            f'<rect x="0" y="0" width="480" height="600" fill="{bg}"/>{weave}{figure}{garment_svg}'
            f'<text x="16" y="584" font-family="monospace" font-size="11" fill="#777">'
            f'{escape(shot)} · preview render · seed {seed}</text></svg>')
