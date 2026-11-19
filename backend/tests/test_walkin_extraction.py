from recorder.walkin_routes import _phone_digits, _walkin_dialogue_fields


def test_registration_dialogue_splits_adjacent_answers_and_normalizes_phone():
    dialogue = (
        "Hi, my name is Akshay Saxena. My phone number is seven eight four zero "
        "eight eight three four eight four. My email id is akshay@example.com. "
        "My organization name is Groovy and our project requirement is for some "
        "ceiling fans, uh, some hand fans."
    )

    assert _walkin_dialogue_fields(dialogue) == {
        "customerName": "Akshay Saxena",
        "mobileNumber": "7840883484",
        "email": "akshay@example.com",
        "companyName": "Groovy",
        "projectName": "some ceiling fans, uh, some hand fans",
    }


def test_indian_country_prefix_is_not_put_into_local_phone_field():
    assert _phone_digits("+91 7840883484") == "7840883484"


def test_source_transcript_can_supply_fields_when_translation_is_missing():
    turns = [
        {
            "english_text": None,
            "source_text": "My name is Akshay Saxena and my phone number is seven eight four zero eight eight three four eight four.",
        },
        {
            "english_text": None,
            "source_text": "My organization name is Groovy and our project requirement is ceiling fans.",
        },
    ]

    dialogue = "\n".join(turn["english_text"] or turn["source_text"] for turn in turns)
    fields = _walkin_dialogue_fields(dialogue)
    assert fields["customerName"] == "Akshay Saxena"
    assert fields["mobileNumber"] == "7840883484"
    assert fields["companyName"] == "Groovy"
    assert fields["projectName"] == "ceiling fans"


def test_disfluent_requirement_list_and_literal_email_are_preserved():
    dialogue = (
        "Hi, my name is Akshat Saxena. My phone number is 7840883484. "
        "My email address is akshat2211@gmail.com. We need around uh, 20, 23 ceiling fans, "
        "some hand pumps and some switches for our organization."
    )

    fields = _walkin_dialogue_fields(dialogue)

    assert fields["email"] == "akshat2211@gmail.com"
    assert fields["projectName"] == "around uh, 20, 23 ceiling fans, some hand pumps and some switches for our organization"
