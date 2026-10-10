"""Generate readable bilingual staff-controlled seed PDFs; development helper only."""

from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

font = Path("C:/Windows/Fonts/arial.ttf")
if not font.exists():
    raise SystemExit("Set this helper's font path to a local Arabic-capable TrueType font")
pdfmetrics.registerFont(TTFont("Business", str(font)))
texts = {
    "en": [
        "Salma Studio",
        "Fictional Egyptian AI and software agency",
        "We help businesses define requirements for custom software and AI assistants.",
        "Staff review project scope and booking preferences before confirming any arrangement.",
    ],
    "ar": [
        "سلمى ستوديو",
        "وكالة مصرية افتراضية للذكاء الاصطناعي والبرمجيات",
        "بنساعد الشركات تحدد متطلبات البرامج المخصصة ومساعدين الذكاء الاصطناعي.",
        "فريقنا بيراجع نطاق المشروع وتفضيلات المواعيد قبل تأكيد أي اتفاق.",
    ],
}
for language, paragraphs in texts.items():
    path = Path("data") / f"business-{language}.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(595, 842))
    pdf.setTitle(f"Salma Studio — staff business knowledge ({language})")
    pdf.setFillColorRGB(0.08, 0.27, 0.21)
    y = 740
    for index, paragraph in enumerate(paragraphs):
        size = 28 if index == 0 else 14
        pdf.setFont("Business", size)
        for line in simpleSplit(paragraph, "Business", size, 460):
            if language == "ar":
                pdf.drawRightString(527, y, get_display(arabic_reshaper.reshape(line)))
            else:
                pdf.drawString(68, y, line)
            y -= size * 1.7
        y -= 22
    pdf.setStrokeColorRGB(0.82, 0.88, 0.80)
    pdf.line(68, 105, 527, 105)
    pdf.setFont("Business", 9)
    pdf.setFillColorRGB(0.45, 0.55, 0.44)
    if language == "ar":
        pdf.drawRightString(
            527, 84, get_display(arabic_reshaper.reshape("معرفة معتمدة من الفريق · وكالة افتراضية"))
        )
    else:
        pdf.drawString(68, 84, "Staff-controlled seed · fictional business")
    pdf.save()
    print(f"Created {path}")
