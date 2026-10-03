"""Build docs/baseline.svg and APN201_Report.pdf from technical_report.md.

  python tools/report_pdf.py             figure + PDF
  python tools/report_pdf.py --figure    figure only

Runs on the host, not in Docker: needs the `markdown` package and Edge or Chrome
for the headless print. The baseline figure is drawn from the committed table in
docs/baseline-2026-10-01.md. docs/chain.svg and docs/deploy.svg are drawn by hand.
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TRACK = Path(__file__).resolve().parents[1]
REPORT = TRACK / "technical_report.md"
PDF = TRACK / "APN201_Report.pdf"
FIGURE = TRACK / "docs" / "baseline.svg"
BASELINE = TRACK / "docs" / "baseline-2026-10-01.md"

MODELS = [  # chart order: v1.5 then v1.0, small to large
    ("Apertus-v1.5-8B", "v1.5 8B"),
    ("Apertus-v1.5-8B-thinking", "v1.5 8B thinking"),
    ("Apertus-v1.5-70B", "v1.5 70B"),
    ("Apertus-v1.5-70B-thinking", "v1.5 70B thinking"),
    ("Apertus-8B-Instruct-2509", "v1.0 8B"),
    ("Apertus-70B-Instruct-2509", "v1.0 70B"),
]
SERIES = [("fi", "Finnish", "#2a78d6"), ("en", "English", "#eb6834")]  # validated palette, slots 1-2

BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "msedge", "google-chrome", "chromium", "chrome"]


def asr() -> dict:
    """(model, lang) -> ASR from the first table of the baseline file."""
    out = {}
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 11 and cells[0] == "passthrough":
            out[(cells[1], cells[2])] = float(cells[9])
    if not out:
        sys.exit(f"no ASR rows in {BASELINE.name}")
    return out


def figure(data: dict) -> str:
    """Grouped horizontal bars: attack success rate per model, Finnish against English."""
    w, left, right, top = 760, 150, 60, 50
    bar, gap, group_gap = 12, 2, 12
    plot = w - left - right
    parts, y = [], top
    for mid, label in MODELS:
        gh = len(SERIES) * (bar + gap) - gap
        parts.append(f'<text x="{left - 10}" y="{y + gh / 2 + 4:.0f}" text-anchor="end" class="lab">'
                     f'{html.escape(label)}</text>')
        for lang, _, colour in SERIES:
            v = data[(mid, lang)]
            bw = max(plot * v, 3)
            parts.append(f'<rect x="{left}" y="{y}" width="{bw:.1f}" height="{bar}" rx="2" fill="{colour}"/>')
            parts.append(f'<text x="{left + bw + 5:.1f}" y="{y + bar - 2}" class="val">{v:.2f}</text>')
            y += bar + gap
        y += group_gap - gap
    bottom = y - group_gap + 4
    axis = [f'<line x1="{left}" y1="{top - 6}" x2="{left}" y2="{bottom}" class="ax"/>']
    for frac in (0.25, 0.5, 0.75, 1.0):
        x = left + plot * frac
        axis.append(f'<line x1="{x:.0f}" y1="{top - 6}" x2="{x:.0f}" y2="{bottom}" class="grid"/>')
        axis.append(f'<text x="{x:.0f}" y="{top - 10}" text-anchor="middle" class="val">{frac:.2f}</text>')
    legend, lx = [], left
    for _, name, colour in SERIES:
        legend.append(f'<rect x="{lx}" y="8" width="11" height="11" rx="2" fill="{colour}"/>'
                      f'<text x="{lx + 16}" y="18" class="val">{name}</text>')
        lx += 30 + 6.3 * len(name)
    h = bottom + 10
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            'font-family="Helvetica, Arial, sans-serif">'
            '<style>.lab{font-size:12px;fill:#1f1f1e}.val{font-size:11px;fill:#55554f}'
            '.ax{stroke:#b9b8b0}.grid{stroke:#e4e3dd;stroke-dasharray:3 3}</style>'
            + "".join(axis + legend + parts) + "</svg>\n")


CSS = """
@page { size: A4; margin: 15mm 16mm 15mm 16mm; }
:root { --ink: #201519; --red: #8a3b26; --ochre: #bd9171; --parchment: #f9ecd4; --sea: #5d6a70; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; line-height: 1.38; color: var(--ink);
       background: #fff; }
h1, h2, h3 { font-family: Georgia, "Times New Roman", serif; font-weight: normal; color: var(--red); }
h1 { font-size: 21pt; margin: 0 0 8pt; padding-bottom: 6pt; border-bottom: 2px solid var(--ink); }
h2 { font-size: 14pt; margin: 14pt 0 5pt; padding-bottom: 7pt; break-after: avoid; display: flow-root;
     background: url("data:image/svg+xml;utf8,ORNAMENT") repeat-x left bottom / 16px 6px; }
h3 { font-size: 11.5pt; font-style: italic; margin: 10pt 0 3pt; break-after: avoid; }
p, li { margin: 3pt 0; }
strong { color: var(--ink); }
code { font-family: Consolas, monospace; font-size: 9pt; }
table { border-collapse: collapse; margin: 6pt 0; font-size: 9pt; width: 100%; }
th { background: var(--parchment); border-bottom: 1.5px solid var(--ink); }
th, td { border-top: 1px solid #d9cbb4; padding: 3pt 5pt; text-align: left; }
tr { break-inside: avoid; }
hr { border: 0; border-top: 1px solid var(--ochre); margin: 10pt 0; }
figure { margin: 8pt 0; break-inside: avoid; }
figure img { display: block; max-width: 100%; margin: 0 auto; }
figcaption { font-size: 8.5pt; color: var(--sea); margin-top: 3pt; text-align: center; }
figcaption b { color: var(--red); font-weight: normal; font-family: Georgia, serif; }
figure.plate { float: right; width: 47mm; margin: 0 0 6pt 10pt; padding: 3pt;
               background: var(--parchment); border: 1px solid var(--ochre); }
figure.plate figcaption { text-align: left; font-size: 7.5pt; line-height: 1.25; }
"""
# A row of small diamonds, after the borders in Gallen-Kallela's Kalevala work.
ORNAMENT = ("<svg xmlns='http://www.w3.org/2000/svg' width='16' height='6'>"
            "<path d='M0 3 H4 M12 3 H16' stroke='%23bd9171' stroke-width='1'/>"
            "<path d='M8 0 L12 3 L8 6 L4 3 Z' fill='%238a3b26'/></svg>")
CSS = CSS.replace("ORNAMENT", ORNAMENT)
IMG = re.compile(r'<p><img alt="([^"]*)" src="([^"]*)" ?/?></p>')


def figures(body: str) -> str:
    """Images become captioned figures; the painting is a plate, the rest are numbered."""
    n = 0

    def one(m: re.Match) -> str:
        nonlocal n
        alt, src = m.group(1), m.group(2)
        if src.endswith("sampo.png"):
            return f'<figure class="plate"><img src="{src}" alt=""><figcaption>{alt}</figcaption></figure>'
        n += 1
        return f'<figure><img src="{src}" alt=""><figcaption><b>Figure {n}.</b> {alt}</figcaption></figure>'
    return IMG.sub(one, body)


def build_pdf() -> None:
    import markdown  # host-only dependency
    body = markdown.markdown(REPORT.read_text(encoding="utf-8"), extensions=["tables"])
    body = figures(body)
    page = f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{body}</body></html>'
    browser = next((b for b in BROWSERS if Path(b).is_file() or shutil.which(b)), None)
    if not browser:
        sys.exit("no Edge or Chrome found for the PDF print")
    # The HTML sits next to the report so docs/*.svg resolve.
    with tempfile.NamedTemporaryFile("w", suffix=".html", dir=TRACK, delete=False, encoding="utf-8") as f:
        f.write(page)
        tmp = Path(f.name)
    out = tmp.with_suffix(".pdf")
    try:
        # Own profile: a running browser would otherwise swallow the headless call.
        with tempfile.TemporaryDirectory() as profile:
            subprocess.run([browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                            f"--user-data-dir={profile}", f"--print-to-pdf={out}", tmp.as_uri()],
                           check=True, capture_output=True, timeout=120)
        if not out.is_file():
            sys.exit("the browser wrote no PDF")
        try:
            out.replace(PDF)
        except PermissionError:
            out.replace(PDF.with_suffix(".new.pdf"))
            sys.exit(f"{PDF.name} is open in another program; wrote {PDF.stem}.new.pdf instead")
    finally:
        tmp.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
    print(f"wrote {PDF.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--figure", action="store_true", help="figure only, no PDF")
    args = ap.parse_args()
    FIGURE.write_text(figure(asr()), encoding="utf-8")
    print(f"wrote docs/{FIGURE.name}")
    if not args.figure:
        build_pdf()


if __name__ == "__main__":
    main()
