"""A small .xlsx writer on the standard library.

An .xlsx file is a zip of XML parts. The workbook export needs only a handful
of them: a sheet per table, a style sheet with number formats, fills and
borders, column widths, frozen headings, filters and merged title rows. That
is a few hundred lines of XML, which is a better trade than a second runtime
dependency for a tool whose only one is Flask.

Formulas are written together with the value they come to, so anything that
reads a workbook without calculating it (a previewer, a script) still sees
the figure, and the workbook asks Excel to recalculate when it opens.
"""

import io
import math
import re
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Control characters are not allowed in XML at all, and an uploaded city name
# is exactly where one would turn up. Excel refuses the whole file over one.
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_BAD_SHEET_CHARS = re.compile(r"[\[\]\*\?/\\:]")

_DEFAULT_BORDER = "<border><left/><right/><top/><bottom/><diagonal/></border>"


def column_letter(index):
    """1 is A, 26 is Z, 27 is AA."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def cell_ref(row, col, absolute=False):
    if absolute:
        return f"${column_letter(col)}${row}"
    return f"{column_letter(col)}{row}"


def range_ref(first_row, first_col, last_row, last_col, absolute=False):
    return (
        cell_ref(first_row, first_col, absolute)
        + ":"
        + cell_ref(last_row, last_col, absolute)
    )


def _clean(value):
    return _ILLEGAL.sub("", str(value))


def _text(value):
    return escape(_clean(value))


def _attr(value):
    return escape(_clean(value), {'"': "&quot;"})


def _number(value):
    if isinstance(value, int):
        return str(value)
    return f"{value:.15g}"


class Formula:
    """A formula and the value it comes to. Both go into the cell."""

    def __init__(self, expression, cached=None):
        self.expression = expression.lstrip("=")
        self.cached = cached


class Sheet:
    def __init__(self, book, name):
        self.book = book
        self.name = name
        self.cells = {}
        self.widths = {}
        self.heights = {}
        self.merges = []
        self.frozen = None
        self.filter = None
        self.print_rows = None
        self.gridlines = True
        self.landscape = False

    def write(self, row, col, value, style=None):
        self.cells[(row, col)] = (value, self.book.style_index(style))

    def write_row(self, row, col, values, style=None):
        for offset, value in enumerate(values):
            self.write(row, col + offset, value, style)

    def width(self, col, characters):
        self.widths[col] = characters

    def height(self, row, points):
        self.heights[row] = points

    def merge(self, row, col, last_row, last_col):
        if (row, col) != (last_row, last_col):
            self.merges.append((row, col, last_row, last_col))

    def freeze(self, row, col):
        """Keep the rows above `row` and the columns left of `col` in view."""
        self.frozen = (row, col)

    def autofilter(self, row, col, last_row, last_col):
        self.filter = (row, col, last_row, last_col)

    def repeat_rows(self, first, last):
        """Rows printed at the top of every page."""
        self.print_rows = (first, last)

    def quoted_name(self):
        return "'" + self.name.replace("'", "''") + "'"

    # ------------------------------------------------------------- writing

    def _cell(self, row, col, value, style):
        ref = cell_ref(row, col)
        s = f' s="{style}"' if style else ""
        if isinstance(value, Formula):
            expression = _text(value.expression)
            cached = value.cached
            if isinstance(cached, bool):
                return f'<c r="{ref}"{s} t="b"><f>{expression}</f><v>{int(cached)}</v></c>'
            if isinstance(cached, (int, float)) and math.isfinite(cached):
                return f'<c r="{ref}"{s}><f>{expression}</f><v>{_number(cached)}</v></c>'
            if isinstance(cached, str):
                return f'<c r="{ref}"{s} t="str"><f>{expression}</f><v>{_text(cached)}</v></c>'
            return f'<c r="{ref}"{s}><f>{expression}</f></c>'
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            return f'<c r="{ref}"{s}/>' if style else ""
        if isinstance(value, bool):
            return f'<c r="{ref}"{s} t="b"><v>{int(value)}</v></c>'
        if isinstance(value, (int, float)):
            return f'<c r="{ref}"{s}><v>{_number(value)}</v></c>'
        return (
            f'<c r="{ref}"{s} t="inlineStr"><is>'
            f'<t xml:space="preserve">{_text(value)}</t></is></c>'
        )

    def _pane(self):
        if not self.frozen:
            return '<selection activeCell="A1" sqref="A1"/>'
        row, col = self.frozen
        split_rows, split_cols = row - 1, col - 1
        if split_rows and split_cols:
            active = "bottomRight"
        elif split_rows:
            active = "bottomLeft"
        else:
            active = "topRight"
        top_left = cell_ref(row, col)
        attributes = ""
        if split_cols:
            attributes += f' xSplit="{split_cols}"'
        if split_rows:
            attributes += f' ySplit="{split_rows}"'
        return (
            f'<pane{attributes} topLeftCell="{top_left}" activePane="{active}" state="frozen"/>'
            f'<selection pane="{active}" activeCell="{top_left}" sqref="{top_left}"/>'
        )

    def to_xml(self, selected=False):
        rows = {}
        for (row, col), (value, style) in self.cells.items():
            rows.setdefault(row, {})[col] = (value, style)
        for row in self.heights:
            rows.setdefault(row, {})

        if self.cells:
            last_row = max(row for row, _ in self.cells)
            last_col = max(col for _, col in self.cells)
            dimension = range_ref(1, 1, last_row, last_col)
        else:
            dimension = "A1"

        parts = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            f'<worksheet xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">',
            '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>',
            f'<dimension ref="{dimension}"/>',
            '<sheetViews><sheetView workbookViewId="0"'
            + ("" if self.gridlines else ' showGridLines="0"')
            + (' tabSelected="1"' if selected else "")
            + ">"
            + self._pane()
            + "</sheetView></sheetViews>",
            '<sheetFormatPr defaultRowHeight="15"/>',
        ]

        if self.widths:
            parts.append("<cols>")
            for col in sorted(self.widths):
                parts.append(
                    f'<col min="{col}" max="{col}" width="{self.widths[col]}" customWidth="1"/>'
                )
            parts.append("</cols>")

        parts.append("<sheetData>")
        for row in sorted(rows):
            height = self.heights.get(row)
            attributes = f' ht="{height}" customHeight="1"' if height else ""
            cells = "".join(
                self._cell(row, col, *rows[row][col]) for col in sorted(rows[row])
            )
            parts.append(f'<row r="{row}"{attributes}>{cells}</row>')
        parts.append("</sheetData>")

        if self.filter:
            parts.append(f'<autoFilter ref="{range_ref(*self.filter)}"/>')

        if self.merges:
            parts.append(f'<mergeCells count="{len(self.merges)}">')
            for merge in self.merges:
                parts.append(f'<mergeCell ref="{range_ref(*merge)}"/>')
            parts.append("</mergeCells>")

        parts.append(
            '<pageMargins left="0.4" right="0.4" top="0.55" bottom="0.55" '
            'header="0.3" footer="0.3"/>'
        )
        parts.append(
            '<pageSetup orientation="'
            + ("landscape" if self.landscape else "portrait")
            + '" fitToWidth="1" fitToHeight="0"/>'
        )
        parts.append(
            "<headerFooter><oddFooter>"
            + _text(f"&L&8{self.book.title or ''}&R&8Page &P of &N")
            + "</oddFooter></headerFooter>"
        )
        parts.append("</worksheet>")
        return "".join(parts)


class Workbook:
    """Sheets, and the named styles their cells refer to."""

    def __init__(self, title=None, author=None, font="Arial"):
        self.title = title
        self.author = author
        self.font = font
        self.sheets = []
        self._fonts = [self._font_xml(False, False, 10, "16150F")]
        self._fills = [
            '<fill><patternFill patternType="none"/></fill>',
            '<fill><patternFill patternType="gray125"/></fill>',
        ]
        self._borders = [_DEFAULT_BORDER]
        self._formats = {}
        self._xfs = ['<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>']
        self._styles = {}

    # -------------------------------------------------------------- sheets

    def add_sheet(self, name):
        name = _BAD_SHEET_CHARS.sub(" ", name).strip()[:31] or f"Sheet{len(self.sheets) + 1}"
        sheet = Sheet(self, name)
        self.sheets.append(sheet)
        return sheet

    # -------------------------------------------------------------- styles

    def add_style(
        self,
        name,
        *,
        bold=False,
        italic=False,
        size=10,
        color="16150F",
        fill=None,
        number=None,
        horizontal=None,
        vertical="top",
        wrap=False,
        indent=0,
        top=None,
        bottom=None,
    ):
        """Register a named cell style. Borders are (style, colour) pairs,
        colours are six hex digits."""
        font = self._intern(self._fonts, self._font_xml(bold, italic, size, color))
        fill_id = 0
        if fill:
            fill_id = self._intern(
                self._fills,
                f'<fill><patternFill patternType="solid"><fgColor rgb="FF{fill}"/>'
                f'<bgColor indexed="64"/></patternFill></fill>',
            )
        border = self._intern(self._borders, self._border_xml(top, bottom))
        number_id = self._format_id(number)

        alignment = f' vertical="{vertical}"'
        if horizontal:
            alignment += f' horizontal="{horizontal}"'
        if wrap:
            alignment += ' wrapText="1"'
        if indent:
            alignment += f' indent="{indent}"'

        xf = (
            f'<xf numFmtId="{number_id}" fontId="{font}" fillId="{fill_id}" '
            f'borderId="{border}" xfId="0" applyNumberFormat="1" applyFont="1" '
            f'applyFill="1" applyBorder="1" applyAlignment="1">'
            f"<alignment{alignment}/></xf>"
        )
        self._styles[name] = self._intern(self._xfs, xf)

    def style_index(self, name):
        if name is None:
            return 0
        return self._styles[name]

    def _font_xml(self, bold, italic, size, color):
        return (
            "<font>"
            + ("<b/>" if bold else "")
            + ("<i/>" if italic else "")
            + f'<sz val="{size}"/><color rgb="FF{color}"/>'
            + f'<name val="{_attr(self.font)}"/>'
            + '<family val="2"/></font>'
        )

    @staticmethod
    def _border_xml(top, bottom):
        def side(tag, spec):
            if not spec:
                return f"<{tag}/>"
            style, color = spec
            return f'<{tag} style="{style}"><color rgb="FF{color}"/></{tag}>'

        return (
            "<border><left/><right/>"
            + side("top", top)
            + side("bottom", bottom)
            + "<diagonal/></border>"
        )

    def _format_id(self, code):
        if not code:
            return 0
        if code not in self._formats:
            self._formats[code] = 164 + len(self._formats)
        return self._formats[code]

    @staticmethod
    def _intern(items, xml):
        if xml not in items:
            items.append(xml)
        return items.index(xml)

    def _styles_xml(self):
        formats = "".join(
            f'<numFmt numFmtId="{number}" formatCode="{_attr(code)}"/>'
            for code, number in self._formats.items()
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<styleSheet xmlns="{MAIN_NS}">'
            + (f'<numFmts count="{len(self._formats)}">{formats}</numFmts>' if self._formats else "")
            + f'<fonts count="{len(self._fonts)}">{"".join(self._fonts)}</fonts>'
            + f'<fills count="{len(self._fills)}">{"".join(self._fills)}</fills>'
            + f'<borders count="{len(self._borders)}">{"".join(self._borders)}</borders>'
            + '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            + f'<cellXfs count="{len(self._xfs)}">{"".join(self._xfs)}</cellXfs>'
            + '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            + "</styleSheet>"
        )

    # ------------------------------------------------------------- package

    def _workbook_xml(self):
        sheets = "".join(
            f'<sheet name="{_attr(sheet.name)}" sheetId="{index}" r:id="rId{index}"/>'
            for index, sheet in enumerate(self.sheets, start=1)
        )
        names = []
        for index, sheet in enumerate(self.sheets):
            if sheet.filter:
                names.append(
                    f'<definedName name="_xlnm._FilterDatabase" localSheetId="{index}" hidden="1">'
                    f"{_text(sheet.quoted_name())}!{range_ref(*sheet.filter, absolute=True)}"
                    "</definedName>"
                )
            if sheet.print_rows:
                first, last = sheet.print_rows
                names.append(
                    f'<definedName name="_xlnm.Print_Titles" localSheetId="{index}">'
                    f"{_text(sheet.quoted_name())}!${first}:${last}</definedName>"
                )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">'
            '<bookViews><workbookView xWindow="0" yWindow="0" windowWidth="28800" '
            'windowHeight="15600" activeTab="0"/></bookViews>'
            f"<sheets>{sheets}</sheets>"
            + (f"<definedNames>{''.join(names)}</definedNames>" if names else "")
            + '<calcPr calcId="191029" fullCalcOnLoad="1"/>'
            "</workbook>"
        )

    def to_bytes(self):
        if not self.sheets:
            raise ValueError("a workbook needs at least one sheet")

        count = len(self.sheets)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for index in range(1, count + 1)
        )
        parts = {
            "[Content_Types].xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                + overrides
                + '<Override PartName="/xl/styles.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                '<Override PartName="/docProps/core.xml" '
                'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                '<Override PartName="/docProps/app.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                "</Types>"
            ),
            "_rels/.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<Relationships xmlns="{PACKAGE_REL_NS}">'
                f'<Relationship Id="rId1" Type="{REL_NS}/officeDocument" Target="xl/workbook.xml"/>'
                f'<Relationship Id="rId2" Type="{PACKAGE_REL_NS}/metadata/core-properties" Target="docProps/core.xml"/>'
                f'<Relationship Id="rId3" Type="{REL_NS}/extended-properties" Target="docProps/app.xml"/>'
                "</Relationships>"
            ),
            "docProps/core.xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                f"<dc:title>{_text(self.title or '')}</dc:title>"
                f"<dc:creator>{_text(self.author or '')}</dc:creator>"
                f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
                f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified>'
                "</cp:coreProperties>"
            ),
            "docProps/app.xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
                'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
                f"<Application>{_text(self.author or 'Overlap')}</Application></Properties>"
            ),
            "xl/workbook.xml": self._workbook_xml(),
            "xl/_rels/workbook.xml.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<Relationships xmlns="{PACKAGE_REL_NS}">'
                + "".join(
                    f'<Relationship Id="rId{index}" Type="{REL_NS}/worksheet" '
                    f'Target="worksheets/sheet{index}.xml"/>'
                    for index in range(1, count + 1)
                )
                + f'<Relationship Id="rId{count + 1}" Type="{REL_NS}/styles" Target="styles.xml"/>'
                "</Relationships>"
            ),
            "xl/styles.xml": self._styles_xml(),
        }
        for index, sheet in enumerate(self.sheets, start=1):
            parts[f"xl/worksheets/sheet{index}.xml"] = sheet.to_xml(selected=index == 1)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, xml in parts.items():
                archive.writestr(path, xml.encode("utf-8"))
        return buffer.getvalue()
