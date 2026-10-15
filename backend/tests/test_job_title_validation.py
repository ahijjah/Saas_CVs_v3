"""
Job title validation — bilingual regression tests.

Covers:
- Valid Arabic titles accepted
- Valid English titles accepted
- Mixed Arabic/English titles accepted
- Symbols-only rejected
- Numbers-only rejected
- Placeholder/gibberish rejected
- Repeated characters rejected
- All-identical-words rejected
"""

import pytest
from services.job_description_quality import validate_job_title


def _ok(title: str) -> None:
    valid, msg = validate_job_title(title)
    assert valid, f"Expected '{title}' to be VALID, got error: {msg}"


def _bad(title: str) -> None:
    valid, msg = validate_job_title(title)
    assert not valid, f"Expected '{title}' to be INVALID, but it was accepted"


# ── Valid Arabic titles ───────────────────────────────────────────────────────

class TestValidArabicTitles:
    def test_accountant(self):          _ok("محاسب")
    def test_hr_manager(self):          _ok("مدير موارد بشرية")
    def test_admin_assistant(self):     _ok("مساعد إداري")
    def test_project_coordinator(self): _ok("منسق مشاريع")
    def test_software_engineer(self):   _ok("مهندس برمجيات")
    def test_customer_service(self):    _ok("موظف خدمة عملاء")
    def test_sales_rep(self):           _ok("مندوب مبيعات")
    def test_nurse(self):               _ok("ممرضة")
    def test_driver(self):              _ok("سائق")
    def test_receptionist(self):        _ok("موظف استقبال")
    def test_finance_director(self):    _ok("مدير مالي")
    def test_data_analyst(self):        _ok("محلل بيانات")


# ── Valid English titles ──────────────────────────────────────────────────────

class TestValidEnglishTitles:
    def test_sales_assistant(self):     _ok("Sales Assistant")
    def test_accountant(self):          _ok("Accountant")
    def test_driver(self):              _ok("Driver")
    def test_hr_manager(self):          _ok("HR Manager")
    def test_software_engineer(self):   _ok("Software Engineer")
    def test_cashier(self):             _ok("Cashier")
    def test_customer_service(self):    _ok("Customer Service Representative")
    def test_senior_dev(self):          _ok("Senior Software Developer")
    def test_ceo(self):                 _ok("Chief Executive Officer")
    def test_intern(self):              _ok("Marketing Intern")


# ── Valid mixed Arabic/English titles ────────────────────────────────────────

class TestValidMixedTitles:
    def test_it_manager(self):          _ok("مدير IT")
    def test_hr_coordinator(self):      _ok("HR منسق")
    def test_english_in_arabic(self):   _ok("محاسب CPA")


# ── Invalid: symbols / numbers only ──────────────────────────────────────────

class TestInvalidSymbolsNumbers:
    def test_numbers_only(self):        _bad("12345")
    def test_symbols_only(self):        _bad("!!!---###")
    def test_dashes_only(self):         _bad("-------")
    def test_dots_only(self):           _bad(".......")
    def test_spaces_only(self):         _bad("     ")
    def test_mixed_symbols_numbers(self): _bad("123 !!!  456")


# ── Invalid: placeholders ─────────────────────────────────────────────────────

class TestInvalidPlaceholders:
    def test_test(self):                _bad("test")
    def test_test_with_number(self):    _bad("test1")
    def test_sample(self):              _bad("sample")
    def test_demo(self):                _bad("demo")
    def test_job(self):                 _bad("job")
    def test_new_job(self):             _bad("new job")
    def test_title(self):               _bad("title")
    def test_position(self):            _bad("position")
    def test_vacancy(self):             _bad("vacancy")
    def test_na(self):                  _bad("n/a")
    def test_tbd(self):                 _bad("tbd")
    def test_arabic_test(self):         _bad("اختبار")
    def test_arabic_sample(self):       _bad("تجربة")
    def test_arabic_vacancy(self):      _bad("منصب")
    def test_arabic_new_job(self):      _bad("وظيفة جديدة")


# ── Invalid: repeated characters ─────────────────────────────────────────────

class TestInvalidRepeatedChars:
    def test_aaa(self):                 _bad("aaa")
    def test_zzz(self):                 _bad("zzz")
    def test_repeated_in_word(self):    _bad("aaabbb")
    def test_xxx(self):                 _bad("xxx")


# ── Invalid: all identical words ─────────────────────────────────────────────

class TestInvalidAllIdenticalWords:
    def test_job_job(self):             _bad("engineer engineer")
    def test_word_repeated_three(self): _bad("manager manager manager")


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_string(self):        _bad("")
    def test_single_char(self):         _bad("a")
    def test_single_arabic_char(self):  _bad("م")
    def test_two_letter_english(self):  _ok("IT")
    def test_two_letter_arabic(self):   _ok("مح")
    def test_leading_trailing_spaces(self):
        valid, _ = validate_job_title("  محاسب  ")
        assert valid
