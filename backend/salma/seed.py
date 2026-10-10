from salma.db import Knowledge, Service, session


def seed(db):
    for row in [
        dict(id="consultation", name_en="Consultation", name_ar="استشارة", price=500),
        dict(
            id="custom",
            name_en="Custom AI/software project",
            name_ar="مشروع ذكاء اصطناعي أو برمجيات مخصص",
            price=None,
        ),
    ]:
        if not db.get(Service, row["id"]):
            db.add(Service(**row, currency="EGP"))
    for language, content in {
        "en": (
            "Salma Studio is a fictional Egyptian AI and software agency. "
            "We help businesses define requirements for custom software and AI assistants. "
            "Staff review project scope and booking preferences before confirming any arrangement."
        ),
        "ar": (
            "سلمى ستوديو وكالة مصرية افتراضية للذكاء الاصطناعي والبرمجيات. "
            "بنساعد الشركات تحدد متطلبات البرامج المخصصة ومساعدين الذكاء الاصطناعي. "
            "فريقنا بيراجع نطاق المشروع وتفضيلات المواعيد قبل تأكيد أي اتفاق."
        ),
    }.items():
        key = f"seed:{language}"
        if not db.get(Knowledge, key):
            db.add(
                Knowledge(
                    id=key,
                    document="Staff-approved fictional business seed",
                    page=1,
                    language=language,
                    text=content,
                )
            )


if __name__ == "__main__":
    with session() as db, db.begin():
        seed(db)
