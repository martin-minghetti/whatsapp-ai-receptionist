import re


def normalize_phone(phone: str) -> str:
    """
    Normalize phone numbers for WhatsApp API.

    Known quirk: WhatsApp sandbox sends Argentine numbers as 549XXXXXXXX
    but the API requires 54XXXXXXXX (without the extra 9).
    """
    if not phone:
        return phone

    # Argentina: 549 + 10 digits → 54 + 10 digits
    if re.match(r'^549\d{10}$', phone):
        return '54' + phone[3:]

    return phone
