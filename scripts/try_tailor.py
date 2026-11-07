"""Try tailoring once against the real API (needs ANTHROPIC_API_KEY in .env.local). Saves nothing; touches no database.

    python scripts/try_tailor.py                                  # a built-in fake resume and a fake posting
    python scripts/try_tailor.py path/to/cv.pdf posting.txt       # your resume and a posting's text (both are sent to Anthropic)
    python scripts/try_tailor.py --effort medium                  # how hard Sonnet thinks: low (default), medium, high

It runs the two calls a tailoring makes, the same code the dashboard runs: the small model reads the PDF into
structure and the line-by-line check against the PDF's text runs; then the main model proposes edits and the accuracy
check sorts them. It prints what each call cost at the listed rates, the changes that survived and how many edits the
check threw away, so you can set TAILOR_MONTHLY_CAP_USD from a real number. Run it once before relying on tailoring
in production, and again if a model, the effort or a prompt changes."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(".env.local")

from dashboard import fit, tailor  # noqa: E402
from scripts.try_resume_read import fake_resume_pdf  # noqa: E402

FAKE_POSTING = """Staff Product Designer, Design Systems at Juniper Labs (remote, United States)

Juniper Labs makes scheduling software used by clinics. Our design system is shared by four product teams and we are
looking for a designer to lead it as it grows.

What you'll do
- Own the design system: components, tokens and documentation, in Figma and in code.
- Partner with engineers on component APIs, and with product designers who use the system every day.
- Run accessibility audits against WCAG and make fixes part of the components.
- Mentor other designers and help teams adopt the system without slowing them down.

What we're looking for
- Experience leading a design system across several products.
- Strong Figma skills and comfort with prototyping.
- Working knowledge of React and front-end development.
- Experience with accessibility and inclusive design.
- Experience in healthcare or another regulated field is a plus.

Pay range $190,000 to $230,000 a year. We are an equal opportunity employer."""
FAKE_REQUIREMENTS = [
    {"label": "Leading a design system", "terms": ["design system", "component library"]},
    {"label": "Figma", "terms": ["figma"]},
    {"label": "Working knowledge of React", "terms": ["react"]},
    {"label": "Accessibility", "terms": ["accessibility", "wcag"]},
    {"label": "Mentoring designers", "terms": ["mentor", "mentoring"]},
    {"label": "Healthcare experience", "terms": ["healthcare"]},
]


def show(label, result, kind):
    print(f"\n{label}")
    print(f"  model        {result.model}")
    print(f"  tokens       {result.input_tokens:,} in, {result.output_tokens:,} out")
    print(f"  cost         ${tailor.cost_usd(result.input_tokens, result.output_tokens, kind):.6f} (at the {tailor.PRICE_VERSION} rates)")
    print(f"  request id   {result.request_id}")


def main():
    args = [a for a in sys.argv[1:]]
    if "--effort" in args:
        at = args.index("--effort")
        tailor.EFFORT = args[at + 1]
        del args[at:at + 2]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("No ANTHROPIC_API_KEY in .env.local. Add it, then run this again.")
    if args:
        content = open(args[0], "rb").read()
        posting = open(args[1]).read() if len(args) > 1 else FAKE_POSTING
        print(f"Using {args[0]} ({len(content):,} bytes) and {'the posting in ' + args[1] if len(args) > 1 else 'the fake posting'}. "
              "They are sent to Anthropic; nothing is saved.")
    else:
        content, posting = fake_resume_pdf(), FAKE_POSTING
        print("Using the built-in fake resume and posting. Nothing is saved.")
    print(f"Tailoring model {tailor.MODEL} at effort {tailor.EFFORT}.")
    resume_text = fit.pdf_text(content)

    try:
        read = tailor.read_structure(content)
    except tailor.TailorError as e:
        sys.exit(f"The structure read failed ({e.kind}).\nWhat the API said: {e.__context__ if e.__context__ else 'nothing: the call was not made'}")
    show("1. Reading the resume into structure", read, "structure")
    structure = tailor.clean_structure(read.fields)
    matches = tailor.check_structure(structure, resume_text)
    roles = structure["experience"]
    print(f"  found        summary {'yes' if structure['summary'] else 'no'}, {len(roles)} roles, "
          f"{sum(len(r['bullets']) for r in roles)} bullets, {len(structure['skills'])} skills")
    print(f"  line check   {'every line is in the resume' if matches else 'FAILED: a line is not in the resume, so a real run would stop here'}")
    if not matches:
        sys.exit(1)

    evidence_text = fit.evidence(resume_text, structure["skills"], [])
    missing, patterns = tailor.missing_requirements(FAKE_REQUIREMENTS, evidence_text)
    try:
        result = tailor.tailor_call(structure, posting, FAKE_REQUIREMENTS, missing)
    except tailor.TailorError as e:
        sys.exit(f"The tailoring call failed ({e.kind}).\nWhat the API said: {e.__context__ if e.__context__ else 'nothing: the call was not made'}")
    show("2. Tailoring", result, "tailor")
    changes, rejected = tailor.build_changes(structure, result.fields, resume_text, patterns)
    proposed = len(result.fields.get("rewrites") or []) + len(result.fields.get("bullet_order") or []) + \
        (1 if (result.fields.get("skills_order") or {}).get("skills") else 0)
    print(f"  proposed     {proposed} edits; {len(changes)} passed the accuracy check, {rejected} were thrown away")
    print(f"  not added    {missing}")
    kept_ids = {c["target"] for c in changes}
    items, _ = tailor.item_index(structure)
    for edit in result.fields.get("rewrites") or []:
        if edit.get("id") not in kept_ids:
            print(f"\n  THROWN AWAY: {edit.get('id')}")
            print(f"    before  {items.get(edit.get('id'), {}).get('text', '(no such line)')}")
            print(f"    after   {edit.get('after')}")
    for change in changes:
        print(f"\n  {change['label']}" + (f" ({change['where']})" if change["where"] else ""))
        print(f"    before  {change['before']}")
        print(f"    after   {change['after']}")
        print(f"    why     {change['why']}")

    spent = tailor.cost_usd(read.input_tokens, read.output_tokens, "structure") + tailor.cost_usd(result.input_tokens, result.output_tokens, "tailor")
    again = tailor.cost_usd(result.input_tokens, result.output_tokens, "tailor")
    print(f"\nThis run cost ${spent:.4f}. A person's first tailoring costs about that; later ones reuse the structure (about ${again:.4f}).")
    print(f"Ten tailorings by one person: about ${spent + 9 * again:.2f}. At the default Scout-wide cap of ${tailor.DEFAULT_MONTHLY_CAP_USD:.0f} a month that is "
          f"about {int(tailor.DEFAULT_MONTHLY_CAP_USD / again)} tailorings.")


if __name__ == "__main__":
    main()
