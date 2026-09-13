#!/usr/bin/env python3
"""Turn the current GitHub avatar into the profile's animated ASCII portrait."""
import base64
import os
import sys

from PIL import Image, ImageEnhance, ImageOps

RAMP = " .:-=+*#%@"
COLS = 72
ROW_RATIO = 0.48
FONT_SIZE = 12.9
CHAR_W = 7.74
LINE_H = 15
PAD = 14


def ascii_lines(path):
    image = Image.open(path).convert("L")
    image = ImageOps.autocontrast(image, cutoff=1)
    image = ImageEnhance.Contrast(image).enhance(1.35)
    rows = int(COLS * image.height / image.width * ROW_RATIO)
    image = image.resize((COLS, rows), Image.Resampling.LANCZOS)
    pixels = list(image.getdata())
    lines = []
    for row in range(rows):
        line = "".join(
            RAMP[min(len(RAMP) - 1, pixels[row * COLS + col] * len(RAMP) // 256)]
            for col in range(COLS)
        ).rstrip()
        lines.append(line)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def build_svg(lines):
    font_path = os.path.join(os.path.dirname(__file__), "fonts", "jbmono-ramp.woff2")
    with open(font_path, "rb") as font_file:
        font = base64.b64encode(font_file.read()).decode("ascii")
    width = int(COLS * CHAR_W + PAD * 2)
    height = len(lines) * LINE_H + PAD * 2
    family = ("JBMono,ui-monospace,SFMono-Regular,Menlo,Consolas,"
              "&apos;Liberation Mono&apos;,monospace")
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="{family}">',
        '<style>'
        '@font-face{font-family:JBMono;font-style:normal;font-weight:400;'
        f'src:url(data:font/woff2;base64,{font}) format("woff2")}}'
        '.a{fill:#6d28d9}@media(prefers-color-scheme:dark){.a{fill:#a78bfa}}'
        '</style>',
    ]
    for index, line in enumerate(lines):
        y = PAD + index * LINE_H
        delay = index * 0.07
        width_px = max(len(line), 1) * CHAR_W
        safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parts.append(
            f'<clipPath id="c{index}"><rect x="{PAD}" y="{y}" height="{LINE_H}" width="0">'
            f'<animate attributeName="width" from="0" to="{width_px:.1f}" begin="{delay:.2f}s" '
            'dur="0.10s" fill="freeze"/></rect></clipPath>'
            f'<g clip-path="url(#c{index})"><text xml:space="preserve" x="{PAD}" '
            f'y="{y + 11.2:.1f}" class="a" font-size="{FONT_SIZE}">{safe}</text></g>'
            f'<rect y="{y + 1}" width="6" height="12" class="a" opacity="0">'
            f'<animate attributeName="x" from="{PAD}" to="{PAD + width_px:.1f}" '
            f'begin="{delay:.2f}s" dur="0.10s" fill="freeze"/>'
            f'<set attributeName="opacity" to="0.8" begin="{delay:.2f}s"/>'
            f'<set attributeName="opacity" to="0" begin="{delay + 0.10:.2f}s"/></rect>'
        )
    parts.append('</svg>')
    return "".join(parts)


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: make_ascii_portrait.py AVATAR [OUTPUT]")
    output = sys.argv[2] if len(sys.argv) == 3 else "ascii.svg"
    svg = build_svg(ascii_lines(sys.argv[1]))
    with open(output, "w", encoding="utf-8") as output_file:
        output_file.write(svg)
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
