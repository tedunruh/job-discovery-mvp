"""The tailored resume as files (SC-72, slice 3): a PDF and a Word document, made from the resume's structure after the
changes the person kept (tailor.apply_changes). Plain on purpose, so applicant-tracking systems read them: one column,
standard headings, real text, no images or tables.

The PDF uses the built-in Helvetica (fpdf2, no font files); characters Helvetica can't draw are swapped for a close one
(curly quotes, dashes, bullets) or "?". The Word file is written directly as its zip of XML parts (the standard library
only), with real bullets and heading styles, so it opens and edits normally.

Nothing here calls a model or the network."""
import io
import re
import unicodedata
import zipfile
from xml.sax.saxutils import escape

from fpdf import FPDF

SWAPS = {"‘": "'", "’": "'", "‚": ",", "“": '"', "”": '"', "–": "-", "—": "-",
         "−": "-", "•": "·", "●": "·", "▪": "·", "…": "...", " ": " ",
         " ": " ", "​": ""}
INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def sections(structure):
    """The resume as an ordered list: ("header", [lines]) then (heading, blocks) per section. A block is
    ("role", title/company line, place/dates line, [bullets]), ("para", text) or ("line", text)."""
    out = [("", [("line", x) for x in structure.get("header") or []])]
    if structure.get("summary"):
        out.append(("Summary", [("para", structure["summary"])]))
    if structure.get("experience"):
        blocks = []
        for role in structure["experience"]:
            head = ", ".join(x for x in (role["title"], role["company"]) if x)
            meta = " · ".join(x for x in (role["location"], role["dates"]) if x)
            blocks.append(("role", head, meta, role["bullets"]))
        out.append(("Experience", blocks))
    if structure.get("skills"):
        out.append(("Skills", [("para", ", ".join(structure["skills"]))]))
    if structure.get("education"):
        out.append(("Education", [("line", x) for x in structure["education"]]))
    for section in structure.get("other") or []:
        out.append((section["heading"], [("line", x) for x in section["lines"]]))
    return out


def file_stem(structure, company):
    """A safe file name: the person's name (the header's first line) and the company, e.g. "ada-lovelace-northwind"."""
    def slug(text):
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    header = structure.get("header") or []
    name = slug(header[0]) if header and "@" not in header[0] and not re.search(r"\d{3}", header[0]) else ""
    return "-".join(x for x in (name, "resume", slug(company)) if x)


# --- PDF ----------------------------------------------------------------------------------------------------------------

def _latin1(text):
    text = "".join(SWAPS.get(c, c) for c in text)
    return unicodedata.normalize("NFC", text).encode("latin-1", "replace").decode("latin-1")


# Word lays text out as the font's own line height (Arial: 1.15 x the size) times the style's "auto" spacing (1.1 here),
# with paragraph gaps from the styles below. The PDF uses the very same numbers (the same sizes, gaps and indents as
# STYLES and `_p` further down), so the two files break at the same places and have the same number of pages.
LINE = 1.15 * 1.1


def to_pdf(structure):
    """The resume as PDF bytes, laid out like `to_docx` (same fonts, sizes, line height, gaps and margins)."""
    pdf = FPDF(unit="pt", format="Letter")
    pdf.set_margins(54, 50, 54)   # 0.75 in at the sides, 50 pt top and bottom, like the Word page margins
    pdf.set_auto_page_break(True, 50)
    pdf.set_title("Resume")
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin

    def room(points):
        """Start a new page when `points` more would not fit (a heading never ends a page alone, as in Word)."""
        if pdf.get_y() + points > pdf.page_break_trigger:
            pdf.add_page()

    def para(value, size=10, style="", before=0, after=3, left=0):
        if before and pdf.get_y() > pdf.t_margin + 1:   # Word drops the space above a paragraph at the top of a page
            pdf.ln(before)
        pdf.set_font("Helvetica", style, size)
        pdf.set_x(pdf.l_margin + left)
        pdf.multi_cell(width - left, size * LINE, _latin1(value), align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(after)

    for n, (heading, blocks) in enumerate(sections(structure)):
        if not blocks:
            continue
        if n == 0:   # the header: name big, then contact lines
            for k, (_, line) in enumerate(blocks):
                para(line, 18 if k == 0 else 9.5, "B" if k == 0 else "", after=2 if k == 0 else 3)
            continue
        room(10 + 10 * LINE + 6.5 + 14 * LINE)
        if pdf.get_y() > pdf.t_margin + 1:
            pdf.ln(10)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_x(pdf.l_margin)
        top = pdf.get_y()
        pdf.cell(width, 10 * LINE, _latin1(heading.upper()), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(120, 120, 120)
        pdf.set_line_width(0.5)
        rule = top + 10 * LINE   # the rule sits under the heading's line, as Word's paragraph border does
        pdf.line(pdf.l_margin, rule, pdf.w - pdf.r_margin, rule)
        pdf.set_y(rule + 0.5 + 5)
        for block in blocks:
            if block[0] == "role":
                _, head, meta, bullets = block
                if head:
                    room(4 + 10.5 * LINE + 9.5 * LINE + 10 * LINE)
                    para(head, 10.5, "B", before=4, after=0)
                if meta:
                    para(meta, 9.5)
                for bullet in bullets:
                    room(10 * LINE)
                    pdf.set_font("Helvetica", "", 10)
                    top = pdf.get_y()
                    pdf.set_fill_color(0, 0, 0)
                    pdf.ellipse(pdf.l_margin + 6, top + 10 * LINE / 2 - 1.4, 2.8, 2.8, style="F")   # Word's bullet, drawn
                    pdf.set_xy(pdf.l_margin + 18, top)
                    pdf.multi_cell(width - 18, 10 * LINE, _latin1(bullet), align="L", new_x="LMARGIN", new_y="NEXT")
                    pdf.ln(1.5)
            else:
                para(block[1])
    return bytes(pdf.output())


# --- Word ---------------------------------------------------------------------------------------------------------------

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
    '</Types>')
ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '</Relationships>')
DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
    '</Relationships>')
FONT = '<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>'
STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles {W}>'
    f'<w:docDefaults><w:rPrDefault><w:rPr>{FONT}<w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="60" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:spacing w:after="40"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:keepNext/><w:pBdr><w:bottom w:val="single" w:sz="4" w:space="1" w:color="787878"/></w:pBdr>'
    '<w:spacing w:before="200" w:after="100"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:caps/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:keepNext/><w:spacing w:before="80" w:after="0"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:spacing w:after="30"/></w:pPr></w:style>'
    '</w:styles>')
NUMBERING = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:numbering {W}>'
    '<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/>'
    '<w:pPr><w:ind w:left="360" w:hanging="260"/></w:pPr></w:lvl></w:abstractNum>'
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
    '</w:numbering>')


def _p(value, style="", bullet=False, size=None):
    clean = escape(INVALID_XML.sub("", value))
    props = (f'<w:pStyle w:val="{style}"/>' if style else "") + \
            ('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if bullet else "")
    run = f'<w:rPr><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>' if size else ""
    return (f'<w:p><w:pPr>{props}</w:pPr><w:r>{run}<w:t xml:space="preserve">{clean}</w:t></w:r></w:p>')


def to_docx(structure):
    """The resume as the bytes of a Word (.docx) file."""
    body = []
    for n, (heading, blocks) in enumerate(sections(structure)):
        if not blocks:
            continue
        if n == 0:
            for k, (_, line) in enumerate(blocks):
                body.append(_p(line, "Title") if k == 0 else _p(line, size=19))
            continue
        body.append(_p(heading, "Heading1"))
        for block in blocks:
            if block[0] == "role":
                _, head, meta, bullets = block
                if head:
                    body.append(_p(head, "Heading2"))
                if meta:
                    body.append(_p(meta, size=19))
                body.extend(_p(b, "ListParagraph", bullet=True) for b in bullets)
            else:
                body.append(_p(block[1]))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document {W}><w:body>{"".join(body)}'
                '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1000" w:right="1080" w:bottom="1000" w:left="1080" '
                'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr></w:body></w:document>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", DOC_RELS)
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/numbering.xml", NUMBERING)
    return out.getvalue()
