"""Generate a synthetic ruled Chinese product PDF for packaged regression checks."""

from __future__ import annotations

import io


def synthetic_catalog_pdf() -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    writer = PdfWriter()
    page = writer.add_blank_page(width=640, height=840)
    system_info = DictionaryObject(
        {
            NameObject("/Registry"): TextStringObject("Adobe"),
            NameObject("/Ordering"): TextStringObject("GB1"),
            NameObject("/Supplement"): NumberObject(4),
        }
    )
    descendant = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType0"),
            NameObject("/BaseFont"): NameObject("/STSong-Light"),
            NameObject("/CIDSystemInfo"): system_info,
            NameObject("/DW"): NumberObject(1000),
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/STSong-Light"),
            NameObject("/Encoding"): NameObject("/UniGB-UCS2-H"),
            NameObject("/DescendantFonts"): ArrayObject([descendant]),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    xs = [20, 60, 150, 350, 450, 550, 620]
    ys = [780, 740, 700, 660]
    commands = ["0.6 w"]
    for x in xs:
        commands.append(f"{x} 660 m {x} 780 l S")
    for y in ys:
        commands.append(f"20 {y} m 620 {y} l S")
    rows = [
        ["行号", "商品编号", "商品名称", "规格", "储存条件", "产地"],
        ["1", "00009", "演示面粉", "25kg/包", "常温", "示例产地"],
        ["2", "00009", "演示面粉", "5kg/包", "常温", "示例产地"],
    ]
    for index, row in enumerate(rows):
        for column, text in enumerate(row):
            encoded = text.encode("utf-16-be").hex()
            commands.append(f"BT /F1 10 Tf {xs[column]+3} {ys[index]-24} Td <{encoded}> Tj ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
