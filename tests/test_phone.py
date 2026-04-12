from core.phone import normalize_phone


def test_argentina_removes_extra_9():
    # WhatsApp sandbox sends 549 + 10 digits, API expects 54 + 10 digits
    assert normalize_phone("5491112345678") == "541112345678"
    assert normalize_phone("5492944123456") == "542944123456"


def test_argentina_correct_format_unchanged():
    # 54 + 10 digits = already correct format
    assert normalize_phone("541112345678") == "541112345678"


def test_non_argentina_unchanged():
    # Brazil
    assert normalize_phone("5511987654321") == "5511987654321"
    # USA
    assert normalize_phone("12125551234") == "12125551234"


def test_empty_string():
    assert normalize_phone("") == ""
