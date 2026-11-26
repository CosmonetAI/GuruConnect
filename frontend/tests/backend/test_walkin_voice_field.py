from types import SimpleNamespace

import pytest

from recorder.walkin_routes import (
    VOICE_FIELD_EXTRACTION_FIELD_RULES,
    _completion_text,
    _email_value,
    _explicit_dialogue_value,
    _phone_digits,
    _phone_from_dialogue,
    _walkin_dialogue_fields,
    _walkin_extracted_fields,
    _voice_value,
)


def test_voice_name_accepts_field_named_json_key():
    assert _voice_value("name", '{"name":"Akshat Saxena"}', "Akshat Saxena") == "Akshat Saxena"


def test_all_individual_fields_share_one_extraction_model():
    assert "Latin/English" in VOICE_FIELD_EXTRACTION_FIELD_RULES["name"]
    assert "ASCII" in VOICE_FIELD_EXTRACTION_FIELD_RULES["phone"]
    assert "ASCII" in VOICE_FIELD_EXTRACTION_FIELD_RULES["email"]


def test_voice_value_accepts_content_parts():
    message = SimpleNamespace(content=[{"type": "output_text", "text": '{"value":"Akshat Saxena"}'}])
    assert _voice_value("name", _completion_text(message), "") == "Akshat Saxena"


def test_phone_normalization_expands_double_and_triple_digits():
    assert _phone_digits("nine eight seven double six triple five four three two one") == "987665554321"
    assert _phone_digits("नौ आठ सात डबल छः पाँच चार तीन दो एक") == "9876654321"
    assert _phone_digits("ಒಂಬತ್ತು ಎಂಟು ಏಳು ಡಬಲ್ ಆರು ಐದು ನಾಲ್ಕು ಮೂರು ಎರಡು ಒಂದು") == "9876654321"


def test_phone_and_email_prefer_exact_transcript_values():
    assert _voice_value("phone", '{"value":"1111111111"}', "My phone is 9876543210") == "9876543210"
    assert _email_value("My email is akshat.saxena@example.com") == "akshat.saxena@example.com"
    assert _voice_value("email", '{"value":"wrong@example.com"}', "akshat at example dot com") == "akshat@example.com"
    assert _voice_value("email", '{"value":"अक्षत@उदाहरण.कॉम"}', "") == ""


def test_dialogue_fallback_keeps_email_username_out_of_name_and_reads_phone_company():
    dialogue = "My name is Akshat Saxena. My phone number is 7840-0034. My email is akshay2211@gmail.com. My company is Cosmonet."
    assert _explicit_dialogue_value(dialogue, r"my\s+name|name") == "Akshat Saxena"
    assert _phone_from_dialogue(dialogue) == "78400034"
    assert _explicit_dialogue_value(dialogue, r"company|organisation|organization") == "Cosmonet"


def test_dialogue_fallback_reads_answer_after_question_and_discards_hesitation():
    assert _explicit_dialogue_value("What is your name? Saumishri Patil", r"my\s+name|name") == "Saumishri Patil"
    assert _explicit_dialogue_value("and my organization name is, uh, Groove", r"company|organisation|organization") == "Groove"


def test_walkin_preview_normalizes_live_fields_and_keeps_newest_value():
    extractions = [
        {"status": "complete", "data": {"customerName": "Akshat Saxena", "phone": "nine three double one eight five four seven double zero", "email": "akshat.saxena@example.com"}},
        {"status": "complete", "data": {"customerName": "Old Name", "phone": "1111111111"}},
    ]
    fields = _walkin_extracted_fields(extractions, {})
    assert fields["customerName"] == "Akshat Saxena"
    assert fields["mobileNumber"] == "9311854700"
    assert fields["email"] == "akshat.saxena@example.com"


def test_complete_registration_sentence_fills_all_grounded_fields():
    dialogue = """Hi, my name is Akshin Saxena, and my phone number is seven eight four zero double eight three four eight four
and my email address is akshatsaksena2211@gmail.com and my organization
My name is Groove and my project requirement is something like
with-- and my project requirement is I need fans and bulbs for a home project"""
    assert _walkin_dialogue_fields(dialogue) == {
        "customerName": "Akshin Saxena",
        "mobileNumber": "7840883484",
        "email": "akshatsaksena2211@gmail.com",
        "companyName": "Groove",
        "projectName": "I need fans and bulbs for a home project",
    }


def test_answer_only_name_and_natural_project_phrasing_are_supported():
    dialogue = "Lakshad Saxena. My phone number is nine three double one eight five four seven double zero. My company name is Groove and uh, for my project, I need some fans and some bulbs"
    assert _walkin_dialogue_fields(dialogue) == {
        "customerName": "Lakshad Saxena",
        "mobileNumber": "9311854700",
        "companyName": "Groove",
        "projectName": "I need some fans and some bulbs",
    }


@pytest.mark.parametrize(
    ("field", "raw", "spoken", "expected"),
    [
        ("phone", '{"mobileNumber":"+91 9876543210"}', "", "919876543210"),
        ("email", '{"email":"akshat@example.com"}', "", "akshat@example.com"),
        ("customerType", '{"customer_type":"vendor"}', "", "vendor"),
    ],
)
def test_voice_value_accepts_field_aliases(field, raw, spoken, expected):
    assert _voice_value(field, raw, spoken) == expected
