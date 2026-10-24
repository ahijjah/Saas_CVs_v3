"""Human-readable words for the CSV export, in English and Arabic. The on-screen words live in the frontend; the same status keys are used."""
from __future__ import annotations

from services.matrix import registry as R

STATUS = {
    "en": {
        "eligible": "Eligible", "not_eligible": "Not Eligible", "review_required": "Review Required", "not_assessed": "Not Assessed",
        "met": "Met", "partially_met": "Partially Met", "not_met": "Not Met", "unclear": "Unclear", "yes": "Yes", "no": "No",
        "passed": "Passed", "failed": "Failed",
        "scored": "Scored", "not_scored": "Not scored", "legacy": "Legacy score", "none": "Not scored",
        "item_level_v2": "Item-level", "legacy_category": "Legacy category",
    },
    "ar": {
        "eligible": "مؤهل", "not_eligible": "غير مؤهل", "review_required": "مطلوب مراجعة", "not_assessed": "لم يُقيَّم",
        "met": "مستوفى", "partially_met": "مستوفى جزئيًا", "not_met": "غير مستوفى", "unclear": "غير واضح", "yes": "نعم", "no": "لا",
        "passed": "اجتاز", "failed": "لم يجتز",
        "scored": "تم التقييم", "not_scored": "بدون درجة", "legacy": "درجة سابقة", "none": "بدون درجة",
        "item_level_v2": "على مستوى البنود", "legacy_category": "على مستوى الفئات",
    },
}
FLAG = {
    "en": {"unclear_criteria": "Unclear criteria", "conflicting_evidence": "Conflicting evidence", "evidence_not_found": "Evidence not found in CV",
           "low_confidence": "Low confidence", "processing_error": "Processing error", "unresolved_review": "Unresolved review"},
    "ar": {"unclear_criteria": "معايير غير واضحة", "conflicting_evidence": "أدلة متعارضة", "evidence_not_found": "الدليل غير موجود في السيرة الذاتية",
           "low_confidence": "ثقة منخفضة", "processing_error": "خطأ في المعالجة", "unresolved_review": "مراجعة غير محسومة"},
}
SUMMARY = {
    "en": {"met": "Met", "partially_met": "Partially Met", "not_met": "Not Met", "unclear": "Unclear", "yes": "Yes", "no": "No",
           "passed": "Passed", "failed": "Failed", "review_required": "Review Required", "not_assessed": "Not Assessed", "total": "Total"},
    "ar": {"met": "مستوفى", "partially_met": "مستوفى جزئيًا", "not_met": "غير مستوفى", "unclear": "غير واضح", "yes": "نعم", "no": "لا",
           "passed": "اجتاز", "failed": "لم يجتز", "review_required": "مطلوب مراجعة", "not_assessed": "لم يُقيَّم", "total": "الإجمالي"},
}
WORKFLOW = {
    "en": {"awaiting_review": "Awaiting Review", "under_review": "Under Review", "shortlisted": "Shortlisted", "interviewing": "Interviewing",
           "offer_made": "Offer Made", "hired": "Hired", "rejected": "Rejected", "withdrawn": "Withdrawn", "on_hold": "On Hold"},
    "ar": {"awaiting_review": "بانتظار المراجعة", "under_review": "قيد المراجعة", "shortlisted": "في القائمة المختصرة", "interviewing": "المقابلة",
           "offer_made": "تم تقديم عرض", "hired": "تم التوظيف", "rejected": "مرفوض", "withdrawn": "منسحب", "on_hold": "معلّق"},
}
META = {
    "en": {"title": "Final Candidate Matrix", "job": "Job", "client": "Client", "organization": "Organization", "exported": "Exported (UTC)",
           "filters": "Filters", "sort": "Sort", "rows": "Candidates", "none": "none", "note": "The overall score is one decision-support element and not a ranking."},
    "ar": {"title": "مصفوفة المرشحين النهائية", "job": "الوظيفة", "client": "العميل", "organization": "المنظمة", "exported": "تاريخ التصدير (UTC)",
           "filters": "المرشحات", "sort": "الترتيب", "rows": "المرشحون", "none": "لا يوجد", "note": "الدرجة الإجمالية عنصر واحد لدعم القرار وليست ترتيبًا."},
}
COLUMN_TITLE_FALLBACK = {c.id: (c.label_en, c.label_ar) for c in R.STATIC}


def status(lang: str, key: str | None) -> str:
    table = STATUS["ar" if lang == "ar" else "en"]
    return table.get(key or "", key or "")


def workflow(lang: str, key: str | None) -> str:
    return WORKFLOW["ar" if lang == "ar" else "en"].get(key or "", (key or "").replace("_", " ").title())
