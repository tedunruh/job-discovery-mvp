"""Try the resume reader once against the real API (needs ANTHROPIC_API_KEY in .env.local). Saves nothing.

    python scripts/try_resume_read.py                 # reads a built-in fake resume
    python scripts/try_resume_read.py path/to/cv.pdf  # reads your file (it is sent to Anthropic)

Prints what the model returned, the tokens used and what the call cost at the listed rates. Run it once after
adding the key, before relying on resume reading in production, and again if the model or prompt changes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(".env.local")

from dashboard import resume as rs  # noqa: E402

FAKE_RESUME_LINES = [
    "Jordan Example",
    "Product Design Lead",
    "Experience",
    "Design Lead, Northwind Health, 2022 to now",
    "Led a team of five designers on the patient app. Ran research and shipped a redesign that cut",
    "support calls by a fifth. Owned the design system and hired two designers.",
    "Senior Product Designer, Fieldstone, 2018 to 2022",
    "Designed onboarding and billing flows for a B2B platform. Built prototypes in Figma and tested",
    "them with customers every sprint.",
    "Product Designer, Brightside Studio, 2015 to 2018",
    "Skills",
    "Figma, prototyping, user research, design systems, accessibility, workshop facilitation",
]


def fake_resume_pdf():
    """A small valid one-page PDF made of the lines above (no extra libraries needed)."""
    text = "BT /F1 11 Tf 56 780 Td 16 TL " + " ".join(
        "(" + line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") ' " for line in FAKE_RESUME_LINES
    ) + "ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(text)} >>\nstream\n{text}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("No ANTHROPIC_API_KEY in .env.local. Add it, then run this again.")
    if len(sys.argv) > 1:
        content = open(sys.argv[1], "rb").read()
        print(f"Reading {sys.argv[1]} ({len(content):,} bytes). It is sent to Anthropic; nothing is saved.")
    else:
        content = fake_resume_pdf()
        print("Reading the built-in fake resume. Nothing is saved.")
    rs.check_file(content)
    try:
        result = rs.read_resume(content)
    except rs.ResumeError as e:
        # The app turns every API failure into a short message for the person; here we want the real cause.
        cause = e.__context__
        sys.exit(f"The read failed ({e.kind}).\nWhat the API said: {cause if cause else 'nothing: the call was not made'}")
    fields = rs.normalize_fields(result.fields)
    print(f"\nmodel          {result.model}")
    print(f"tokens         {result.input_tokens:,} in, {result.output_tokens:,} out")
    print(f"cost           ${rs.cost_usd(result.input_tokens, result.output_tokens):.6f} (at the {rs.PRICE_VERSION} rates)")
    print(f"request id     {result.request_id}")
    print(f"\ntitles         {fields['titles']}")
    print(f"level          {fields['level']}  ({fields['level_note']})")
    print(f"skills         {fields['skills']}")


if __name__ == "__main__":
    main()
