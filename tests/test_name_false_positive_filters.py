from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault


def _redact(
    text: str,
    previous_assistant_message: str | None = None,
    non_name_allowlist: list[str] | None = None,
) -> str:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()
    return engine.redact(
        text,
        vault,
        previous_assistant_message=previous_assistant_message,
        non_name_allowlist=non_name_allowlist,
    ).redacted_text


def test_does_not_redact_known_false_positive_phrases() -> None:
    cases = [
        "Hi, I am a realtor and I would like to know if you have two story homes available with first floor full bathroom and bedroom",
        "Yes",
        "We are interested in finding a lot in the wing field area to build",
        "Shadow Hills is too far out.",
        "does this link I with a sale agent",
        "In Windsor",
    ]

    for case in cases:
        assert _redact(case) == case


def test_still_redacts_explicit_name_intro() -> None:
    text = "My name is Carol Baynes"
    assert _redact(text) == "My name is <fn_1> <ln_1>"


def test_single_word_reply_is_not_name_without_assistant_name_prompt() -> None:
    assert _redact("Windsor") == "Windsor"


def test_single_word_reply_redacts_when_assistant_asks_for_name() -> None:
    prompt = "Thanks. What is your first name?"
    assert _redact("Jinbad", previous_assistant_message=prompt) == "<fn_1>"


def test_first_name_prompt_redacts_name_after_affirmative_period() -> None:
    prompt = "Thanks! Is that your phone number? And what's your first name so I can pass this along?"
    assert _redact("Yes. Luis", previous_assistant_message=prompt) == "Yes. <fn_1>"


def test_first_name_prompt_redacts_name_after_affirmative_dash() -> None:
    prompt = "What's your first name?"
    assert _redact("Yes - Tim", previous_assistant_message=prompt) == "Yes - <fn_1>"


def test_first_name_prompt_redacts_name_after_affirmative_comma() -> None:
    prompt = "What's your first name?"
    assert _redact("Yup, Jeff", previous_assistant_message=prompt) == "Yup, <fn_1>"


def test_runtime_non_name_allowlist_blocks_city_and_community_terms() -> None:
    assert _redact("In Windsor", non_name_allowlist=["Windsor"]) == "In Windsor"
    assert _redact("Shadow Hills", non_name_allowlist=["Shadow Hills"]) == "Shadow Hills"


def test_prompted_last_name_reply_redacts_leading_token() -> None:
    prompt = "Thanks, Don! What's your last name?"
    text = "Otis, do you have a phone contact for them?"
    assert _redact(text, previous_assistant_message=prompt) == "<ln_1>, do you have a phone contact for them?"


def test_prompted_last_name_reply_does_not_redact_sentence_starter() -> None:
    prompt = "I have your first name. Last name? Email or phone number?"
    text = "View plans single level Allen"
    assert _redact(text, previous_assistant_message=prompt) == text


def test_prompted_first_and_last_name_reply_redacts_both_name_tokens() -> None:
    prompt = "Can I please get your first and last name?"
    text = "Bri Rios, my email is bri@example.com"
    assert _redact(text, previous_assistant_message=prompt) == "<fn_1> <ln_1>, my email is <em_1>"


def test_prompted_first_and_last_name_reply_redacts_name_before_inline_email() -> None:
    prompt = "Can I please get your first and last name?"
    text = "Betty Johnson mrsj9534@msn.com"
    assert _redact(text, previous_assistant_message=prompt) == "<fn_1> <ln_1> <em_1>"


def test_prompted_first_and_last_name_request_does_not_redact_no_name_reply() -> None:
    prompt = "Can I please get your first and last name?"
    text = "No name, please, just email me."
    assert _redact(text, previous_assistant_message=prompt) == text


def test_prompted_first_and_last_name_request_does_not_redact_city_state_reply() -> None:
    prompt = "Can I please get your first and last name?"
    text = "Windsor California"
    assert _redact(text, previous_assistant_message=prompt, non_name_allowlist=["Windsor"]) == text


def test_prompted_first_name_does_not_redact_plan_keyword() -> None:
    prompt = "Can I grab your first name?"
    assert _redact("Plan 1", previous_assistant_message=prompt) == "Plan 1"


def test_city_plus_state_phrase_is_not_name() -> None:
    assert _redact("Windsor California", non_name_allowlist=["Windsor"]) == "Windsor California"


def test_city_plus_state_abbreviation_phrase_is_not_name() -> None:
    assert _redact("Windsor CA", non_name_allowlist=["Windsor"]) == "Windsor CA"


def test_direction_state_city_phrase_is_not_name() -> None:
    assert _redact("Northern Nevada Sparks", non_name_allowlist=["Sparks"]) == "Northern Nevada Sparks"


def test_direction_state_abbreviation_city_phrase_is_not_name() -> None:
    assert _redact("Northern NV Sparks", non_name_allowlist=["Sparks"]) == "Northern NV Sparks"


def test_service_phrase_is_not_name() -> None:
    text = "I am local and provide service in Zephyr Cove And work for others in the community."
    assert _redact(text) == text


def test_weak_i_am_intro_does_not_redact_sentence_fragment() -> None:
    text = "I am talking with Jessica right now"
    assert _redact(text) == text


def test_prompted_name_reply_does_not_redact_sentence_openers() -> None:
    prompt = "Can I grab your last name?"
    assert _redact("For plan elevation C", previous_assistant_message=prompt) == "For plan elevation C"
    assert _redact("Already working with Jessica", previous_assistant_message=prompt) == "Already working with Jessica"


def test_prompted_name_reply_does_not_redact_all_caps_topic_phrases() -> None:
    prompt = "Can I grab your last name?"
    assert _redact("PROPERTY TAXES", previous_assistant_message=prompt) == "PROPERTY TAXES"
    assert _redact("HOMEOWNERS INSUREANCE", previous_assistant_message=prompt) == "HOMEOWNERS INSUREANCE"


def test_state_phrase_is_not_redacted_as_name() -> None:
    assert _redact("South Carolina") == "South Carolina"


def test_this_is_domain_noun_is_not_name() -> None:
    assert _redact("This is condo") == "This is condo"


def test_phone_with_plus_separators_is_detected() -> None:
    assert _redact("My phone is 803+747+6306") == "My phone is <ph_1>"


def test_repeat_detected_name_is_redacted_again_in_same_vault_thread() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()
    first = engine.redact("My name is Nancy Seawright", vault).redacted_text
    second = engine.redact("Thank you, Nancy Seawright and referrals.", vault).redacted_text
    assert first == "My name is <fn_1> <ln_1>"
    assert second == "Thank you, <fn_1> <ln_1> and referrals."


def test_all_caps_i_am_common_word_phrase_is_not_redacted() -> None:
    text = "I AM TOLD THEY WILL NO LONGER BUILD THIS HOME"
    assert _redact(text) == text


def test_yes_county_state_phrase_is_not_redacted() -> None:
    assert _redact("Yes Screven County Ga.") == "Yes Screven County Ga."


def test_contact_info_prompt_treats_name_plus_phone_as_name_reply() -> None:
    prompt = "Can I grab your contact info so someone reaches out?"
    assert _redact("Reather Cooper 912-425-2102", previous_assistant_message=prompt) == "<fn_1> <ln_1> <ph_1>"


def test_full_name_prompt_with_lowercase_last_name_and_phone_redacts_both_name_tokens() -> None:
    prompt = "What can I use to reach you? Please share your first and last name and phone."
    assert _redact("Kadyzia young 4782020388", previous_assistant_message=prompt) == "<fn_1> <ln_1> <ph_1>"


def test_full_name_prompt_with_first_name_and_email_redacts_first_name_and_email() -> None:
    prompt = "Please share your first and last name and the best way to reach you."
    assert _redact("Angela wbarno2010@gmail.com", previous_assistant_message=prompt) == "<fn_1> <em_1>"


def test_first_name_plus_contact_is_reused_on_follow_up_in_same_vault() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()
    prompt = "Please share your first and last name and the best way to reach you."
    first = engine.redact(
        "Angela wbarno2010@gmail.com",
        vault,
        previous_assistant_message=prompt,
    ).redacted_text
    follow_up = engine.redact(
        "Angela",
        vault,
        previous_assistant_message="Thanks, Angela! What's your last name?",
    ).redacted_text
    assert first == "<fn_1> <em_1>"
    assert follow_up == "<fn_1>"


def test_i_am_not_sure_phrase_is_not_redacted() -> None:
    assert _redact("I am not sure.") == "I am not sure."


def test_first_name_prompt_with_name_plus_phone_redacts_name_and_phone() -> None:
    prompt = "What's your first name?"
    assert _redact("Lynn 925.963.1940", previous_assistant_message=prompt) == "<fn_1> <ph_1>"


def test_last_name_labelled_reply_redacts_last_name_without_prompt() -> None:
    assert _redact("Eason last name") == "<ln_1> last name"


def test_repeat_name_matches_compact_and_spaced_variants() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()
    prompt = "What's your first name and best phone number?"
    first = engine.redact("AgentDan, 682.360.6692", vault, previous_assistant_message=prompt).redacted_text
    second = engine.redact("Agent Dan", vault, previous_assistant_message="What's your full name?").redacted_text
    assert first == "<fn_1>, <ph_1>"
    assert second == "<fn_1>"


def test_contact_info_reference_without_your_does_not_force_name_redaction() -> None:
    prompt = "I can connect you with our main team to get the right sales associate contact info. What works best for you?"
    assert _redact("Email", previous_assistant_message=prompt) == "Email"


def test_direction_plus_city_phrase_is_not_name() -> None:
    assert _redact("North Dallas") == "North Dallas"


def test_im_sentence_fragment_is_not_redacted() -> None:
    assert _redact("I'm wanting land as well") == "I'm wanting land as well"
    assert _redact("I'm ready") == "I'm ready"
    assert _redact("I'm contractor and would like to become a trade partner") == (
        "I'm contractor and would like to become a trade partner"
    )
    assert _redact("I am manly curious about specific incentives") == (
        "I am manly curious about specific incentives"
    )
    assert _redact("I am hoping to learn more about incentives") == (
        "I am hoping to learn more about incentives"
    )


def test_my_name_intro_keeps_name_only_and_not_im_sentence_phrase() -> None:
    text = "Hi, my name is David, I'm reaching out to you concerning vacant lots"
    assert _redact(text) == "Hi, my name is <fn_1>, I'm reaching out to you concerning vacant lots"


def test_full_name_prompt_csv_response_redacts_both_names_and_email() -> None:
    prompt = "First name? Last name? Best contact: email or phone?"
    assert _redact("Sekhar, Reddy, ylsreddy@gmail.com", previous_assistant_message=prompt) == (
        "<fn_1> <ln_1>, <em_1>"
    )


def test_location_cue_with_allowlisted_city_is_not_name() -> None:
    assert _redact("Near Denton", non_name_allowlist=["Denton"]) == "Near Denton"
    assert _redact("Which location is close to Denton", non_name_allowlist=["Denton"]) == (
        "Which location is close to Denton"
    )
    assert _redact("I want to see dewberry near 15 miles from Denton", non_name_allowlist=["Denton"]) == (
        "I want to see dewberry near 15 miles from Denton"
    )


def test_allowlisted_community_city_compound_phrase_is_not_name() -> None:
    text = "Paloma Ranch Estates New Fairview"
    assert _redact(text, non_name_allowlist=["Paloma Ranch", "New Fairview"]) == text


def test_keyed_names_field_redacts_first_and_last_initial_plus_contact() -> None:
    text = "Names:Providence N,email:ntenyi@gmail.com,phone number 7262236383"
    assert _redact(text) == "Names:<fn_1> <ln_1>,email:<em_1>,phone number <ph_1>"


def test_prompted_last_name_after_first_name_capture_uses_ln_token() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()
    first_prompt = "What's your first name?"
    last_prompt = "Thanks, Olujoke! I have your first name. Last name? Email or phone number?"
    first = engine.redact("Olujoke", vault, previous_assistant_message=first_prompt).redacted_text
    last = engine.redact("Adeleke", vault, previous_assistant_message=last_prompt).redacted_text
    assert first == "<fn_1>"
    assert last == "<ln_1>"


def test_full_name_prompt_redacts_name_before_company_phrase_and_email() -> None:
    prompt = "What's the best way to reach you? I need your first name, last name, and either email or phone number."
    text = "Kathy Chruscielski KC IStudios photography@gmail.com"
    assert _redact(text, previous_assistant_message=prompt) == "<fn_1> <ln_1> KC IStudios <em_1>"


def test_full_name_prompt_redacts_lowercase_last_name_before_hyphen_email() -> None:
    prompt = "I need: First name, Last name, Email or phone."
    text = "Michelle pozas- Michellepozas@gmail.com"
    assert _redact(text, previous_assistant_message=prompt) == "<fn_1> <ln_1>- <em_1>"


def test_this_is_full_name_with_company_context_redacts_name_only() -> None:
    text = "This is Robbin Smith with Keller Williams Realty."
    assert _redact(text) == "This is <fn_1> <ln_1> with Keller Williams Realty."


def test_clients_two_full_names_are_both_redacted() -> None:
    text = "I'll be bringing clients Breann Wills and Jerad Leonard to visit."
    redacted = _redact(text)
    assert "Breann" not in redacted
    assert "Wills" not in redacted
    assert "Jerad" not in redacted
    assert "Leonard" not in redacted


def test_full_name_prompt_accepts_one_letter_last_initial() -> None:
    prompt = "Your name (first and last) and best contact method (email or phone)."
    assert _redact("tom w", previous_assistant_message=prompt) == "<fn_1> <ln_1>"


def test_first_name_prompt_accepts_three_part_name_with_middle_initial() -> None:
    prompt = "Got it! What's your first name?"
    assert _redact("Rebecca H Powell", previous_assistant_message=prompt) == "<fn_1> <mn1_1> <ln_1>"


def test_first_name_prompt_accepts_three_part_name_with_dotted_middle_initial() -> None:
    prompt = "Got it! What's your first name?"
    assert _redact("Rebecca H. Powell", previous_assistant_message=prompt) == "<fn_1> <mn1_1> <ln_1>"


def test_full_name_prompt_accepts_lowercase_three_part_name() -> None:
    prompt = "Quick info needed: your first and last name, plus email or phone."
    assert _redact("lauren caryk bunker", previous_assistant_message=prompt) == "<fn_1> <mn1_1> <ln_1>"


def test_full_name_prompt_accepts_unicode_last_name() -> None:
    prompt = "Please share your first and last name and the best way to reach you."
    assert _redact("Nancy Damián", previous_assistant_message=prompt) == "<fn_1> <ln_1>"


def test_last_name_prompt_with_known_first_and_contact_redacts_last_name_token() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()
    first_prompt = "What's your first name?"
    last_prompt = "Thanks, Linda! I have your first name. I need last name and email or phone number."
    first = engine.redact("Linda", vault, previous_assistant_message=first_prompt).redacted_text
    follow_up = engine.redact(
        "Linda Mendoza lindamendoza1144@gmail.com 4699850049 desoto texas",
        vault,
        previous_assistant_message=last_prompt,
    ).redacted_text
    assert first == "<fn_1>"
    assert follow_up == "<fn_1> <ln_1> <em_1> <ph_1> desoto texas"


def test_full_name_prompt_does_not_redact_location_detail_line() -> None:
    prompt = "To connect you with our team, I need your name, email or phone, and which community or home."
    assert _redact("under Argyle ISD", previous_assistant_message=prompt) == "under Argyle ISD"


def test_plan_context_reply_uses_allowlist_and_keeps_floor_plan_name() -> None:
    prompt = "What floor plan interests you most?"
    text = "Carolina III"
    assert _redact(text, previous_assistant_message=prompt, non_name_allowlist=["Carolina III"]) == text


def test_plan_context_fuzzy_match_keeps_nearby_floor_plan_name() -> None:
    prompt = "Want to see available Redbud II homes or explore other plans?"
    text = "Cypress IO"
    assert _redact(text, previous_assistant_message=prompt, non_name_allowlist=["Cypress II"]) == text


def test_location_context_short_reply_is_not_treated_as_name() -> None:
    prompt = "Want to explore other communities, or shall I connect you with our team?"
    assert _redact("Grand Prairie", previous_assistant_message=prompt) == "Grand Prairie"


def test_contact_lookup_phrase_is_not_treated_as_user_name() -> None:
    text = "EMAIL ADDRESS FOR MATT CONEDERA"
    assert _redact(text) == text


def test_location_context_does_not_disable_explicit_name_intro() -> None:
    prompt = "Which community are you interested in?"
    text = "My name is John Smith"
    assert _redact(text, previous_assistant_message=prompt) == "My name is <fn_1> <ln_1>"


def test_business_signature_company_name_is_not_redacted() -> None:
    assert _redact("Pro Page Profiles") == "Pro Page Profiles"


def test_full_name_prompt_accepts_lowercase_two_token_name() -> None:
    prompt = "What's your first name and last name?"
    assert _redact("brittany moronta", previous_assistant_message=prompt) == "<fn_1> <ln_1>"


def test_full_name_prompt_with_city_first_token_and_phone_redacts_name() -> None:
    prompt = "First name? Last name? Best contact method—email or phone?"
    text = "Justin Serene, phone, 5303005285"
    assert _redact(text, previous_assistant_message=prompt, non_name_allowlist=["Justin"]) == (
        "<fn_1> <ln_1>, phone, <ph_1>"
    )


def test_name_correction_reply_redacts_updated_last_name_near_match() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()
    first_prompt = "What's your first and last name?"
    correction_prompt = "Thanks, Dean! What's your preferred contact method—email or phone number?"
    first = engine.redact("Dean Frew", vault, previous_assistant_message=first_prompt).redacted_text
    correction = engine.redact("Dean Free", vault, previous_assistant_message=correction_prompt).redacted_text
    assert first == "<fn_1> <ln_1>"
    assert correction == "<fn_1> <ln_1>"


def test_name_correction_single_word_last_name_restores_ln_token() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()
    first_prompt = "What's your first and last name?"
    correction_prompt = "What's your preferred contact method—email or phone number?"
    initial = engine.redact("Dean Frew", vault, previous_assistant_message=first_prompt).redacted_text
    corrected = engine.redact("Dean Free", vault, previous_assistant_message=correction_prompt).redacted_text
    last_only = engine.redact("Frew", vault, previous_assistant_message=correction_prompt).redacted_text
    assert initial == "<fn_1> <ln_1>"
    assert corrected == "<fn_1> <ln_1>"
    assert last_only == "<ln_1>"


def test_name_and_best_contact_prompt_redacts_name_plus_phone() -> None:
    prompt = "What's your name and best contact info? I can have our team reach out."
    assert _redact("Geetha 4699610539", previous_assistant_message=prompt) == "<fn_1> <ph_1>"


def test_affirmative_name_phone_reply_redacts_name_tokens() -> None:
    text = "Yes janeth Vega 214-868-5050"
    assert _redact(text) == "Yes <fn_1> <ln_1> <ph_1>"


def test_direction_suffix_phrase_is_not_name() -> None:
    assert _redact("Kreymer East") == "Kreymer East"


def test_common_west_surname_still_redacts() -> None:
    assert _redact("John West") == "<fn_1> <ln_1>"


def test_signature_tail_name_with_brokerage_cue_is_redacted() -> None:
    text = "Hi there. Can you tell me your current buyer incentives for Willow Wood? Robie Dodson C21 Judge Fite"
    assert _redact(text) == (
        "Hi there. Can you tell me your current buyer incentives for Willow Wood? <fn_1> <ln_1> C21 Judge Fite"
    )


def test_signature_tail_name_after_sentence_is_redacted() -> None:
    text = "Good afternoon. I just submitted a warranty request. I just want to confirm that it was received. Dennis Larkin"
    assert _redact(text) == (
        "Good afternoon. I just submitted a warranty request. I just want to confirm that it was received. <fn_1> <ln_1>"
    )


def test_signature_tail_name_after_dash_is_redacted() -> None:
    text = "Good afternoon. I just submitted a warranty request and want to confirm it was received - Dennis Larkin"
    assert _redact(text) == (
        "Good afternoon. I just submitted a warranty request and want to confirm it was received - <fn_1> <ln_1>"
    )


def test_signature_tail_name_in_parentheses_is_redacted() -> None:
    text = "Good afternoon. I just submitted a warranty request and want to confirm it was received (Dennis Larkin)"
    assert _redact(text) == (
        "Good afternoon. I just submitted a warranty request and want to confirm it was received (<fn_1> <ln_1>)"
    )


def test_form_labelled_name_line_is_redacted_deterministically() -> None:
    text = "Your name (first & last): Yash Bhimani"
    assert _redact(text) == "Your name (first & last): <fn_1> <ln_1>"


def test_realtor_pair_intro_redacts_both_name_pairs() -> None:
    text = (
        "Hedy LeBlanc, Ebby Halliday, Realtors.  Are there any move-in or spec homes soon "
        "to be ready in Mosaic Community, Celina"
    )
    assert _redact(text) == (
        "<fn_1> <ln_1>, <mn1_1> <mn2_1>, Realtors.  Are there any move-in or spec homes soon "
        "to be ready in Mosaic Community, Celina"
    )


def test_first_name_prompt_does_not_redact_sentence_starter_questions() -> None:
    prompt = "What's your first name?"
    assert _redact("List the finishes used in the 2200sqft", previous_assistant_message=prompt) == (
        "List the finishes used in the 2200sqft"
    )
    assert _redact("Are these finishes builder finishes or high end", previous_assistant_message=prompt) == (
        "Are these finishes builder finishes or high end"
    )
    assert _redact("why this house is not showing in your website?", previous_assistant_message=prompt) == (
        "why this house is not showing in your website?"
    )
    assert _redact("Can you tell me if its under construction", previous_assistant_message=prompt) == (
        "Can you tell me if its under construction"
    )
    assert _redact("great Can you also find out the approx price please", previous_assistant_message=prompt) == (
        "great Can you also find out the approx price please"
    )


def test_hello_assistant_name_is_not_redacted() -> None:
    assert _redact("Hello Mia") == "Hello Mia"


def test_form_label_alt_requires_colon_or_dash() -> None:
    assert _redact("First name is fine") == "First name is fine"
    assert _redact("First name: Tess") == "First name: <fn_1>"


def test_i_am_limiting_information_phrase_is_not_redacted() -> None:
    text = "email: kc6845640@gmail.com. When they reach me, I will provide more details. I am limiting information I'm giving you now"
    assert _redact(text).endswith("I am limiting information I'm giving you now")


def test_contact_prompt_acknowledgements_are_not_redacted() -> None:
    prompt = "Would you like me to grab your contact info so they can send you detailed lot specs?"
    assert _redact("Sure", previous_assistant_message=prompt) == "Sure"
    assert _redact("either is good", previous_assistant_message=prompt) == "either is good"
    assert _redact("no im good thanks", previous_assistant_message=prompt) == "no im good thanks"


def test_first_name_prompt_with_inline_email_and_phone_redacts_first_name() -> None:
    prompt = "Please provide your first name, email, and phone number."
    text = "John, john@example.com 123-465-7890"
    assert _redact(text, previous_assistant_message=prompt) == "<fn_1>, <em_1> <ph_1>"


def test_last_name_labelled_reply_without_colon_is_still_redacted() -> None:
    assert _redact("Last name Jonson") == "Last name <ln_1>"


def test_contact_preference_reply_is_not_parsed_as_name() -> None:
    prompt = "Would you prefer I send those details to your email or phone so you can share them with your clients?"
    assert _redact("here is fine", previous_assistant_message=prompt) == "here is fine"


def test_contact_then_name_follow_up_redacts_single_name_reply() -> None:
    prompt = (
        "Got it! Is that your phone number, or did you mean something else? "
        "Just want to make sure I have it right before I grab your name."
    )
    assert _redact("Santosh", previous_assistant_message=prompt) == "<fn_1>"


def test_phone_with_parenthetical_name_redacts_parenthetical_name() -> None:
    text = "Sure 682-230-1450 (Nick)"
    assert _redact(text) == "Sure <ph_1> (<fn_1>)"


def test_first_name_prompt_with_parenthetical_alias_redacts_both_names() -> None:
    prompt = "Great! I'll get your details so the team can reach out. What's your first name?"
    assert _redact("Debbie (Debra)", previous_assistant_message=prompt) == "<fn_1> (<fn_2>)"


def test_first_name_prompt_with_dash_alias_redacts_both_names() -> None:
    prompt = "What's your first name?"
    assert _redact("Jon - Jonathan", previous_assistant_message=prompt) == "<fn_1> - <fn_2>"


def test_first_name_prompt_with_short_for_alias_redacts_both_names() -> None:
    prompt = "What's your first name?"
    assert _redact("Jim - short for James", previous_assistant_message=prompt) == "<fn_1> - short for <fn_2>"


def test_blossom_ai_phrase_is_not_redacted() -> None:
    assert _redact("Blossom AI") == "Blossom AI"


def test_yes_last_name_is_phrase_redacts_only_actual_last_name() -> None:
    prompt = "I need your last name and email."
    text = "Yes last name is Khanal. Email is barishakhanal@gmail.com"
    assert _redact(text, previous_assistant_message=prompt) == "Yes last name is <ln_1>. Email is <em_1>"


def test_business_name_lines_are_not_redacted() -> None:
    assert _redact("Corporate Class Crew") == "Corporate Class Crew"
    assert _redact("Crystal Care Janitorial") == "Crystal Care Janitorial"


def test_incentive_ts_phrase_is_not_redacted() -> None:
    assert _redact("Incentive TS") == "Incentive TS"


def test_move_in_ready_all_caps_phrase_is_not_redacted() -> None:
    assert _redact("MOVE IN READY") == "MOVE IN READY"


def test_yes_would_like_phrase_is_not_redacted_as_name() -> None:
    text = "Yes would like to look at the home. Have someone call me Monday 775.544.6095"
    assert _redact(text) == "Yes would like to look at the home. Have someone call me Monday <ph_1>"


def test_sorry_typo_phrase_is_not_redacted_as_name() -> None:
    text = "Sorry typo 717 682 -6642"
    assert _redact(text) == "Sorry typo <ph_1>"


def test_typo_name_intro_my_nae_is_is_redacted() -> None:
    text = "yes I need to go to my break but here is my number 306 5961914 my nae is Edwibn"
    redacted = _redact(text)
    assert "<ph_1>" in redacted
    assert "<fn_1>" in redacted


def test_last_name_prompt_allows_full_name_correction_phrase() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()
    first = engine.redact(
        "yes I need to go to my break but here is my number 306 5961914 my nae is Edwibn",
        vault,
    ).redacted_text
    corrected = engine.redact(
        "Edwin Malang my full name",
        vault,
        previous_assistant_message="Perfect! I've got your info. Quick question—what's your last name for the team's records?",
    ).redacted_text
    assert "<fn_1>" in first
    assert corrected == "<fn_1> <ln_1> my full name"


def test_street_suffix_phrase_with_terrace_is_not_redacted() -> None:
    assert _redact("Taskamanwa TERRACE") == "Taskamanwa TERRACE"


def test_first_name_prompt_with_existing_contact_accepts_two_token_lowercase_name() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()
    _ = engine.redact("chiragrealtor25@gmail.com", vault).redacted_text
    redacted = engine.redact(
        "chirag chaudhari",
        vault,
        previous_assistant_message="Got it! What's your first name so I can get those Brighton details sent over?",
    ).redacted_text
    assert redacted == "<fn_1> <ln_1>"


def test_first_name_prompt_with_affirmative_then_name_and_prose_redacts_name_only() -> None:
    prompt = "Thanks for sharing that! Just to confirm—is that the best number to reach you? And what's your first name?"
    text = "Yes Genevieve, I live currently in Idaho Twin Falls. So I’ll be looking to sell my home to relocate."
    assert _redact(text, previous_assistant_message=prompt) == (
        "Yes <fn_1>, I live currently in Idaho Twin Falls. So I’ll be looking to sell my home to relocate."
    )


def test_last_name_prompt_with_trailing_request_still_redacts_last_name() -> None:
    prompt = "What's your last name, just in case?"
    assert _redact("Doyon could you schedule for all of these", previous_assistant_message=prompt) == (
        "<ln_1> could you schedule for all of these"
    )


def test_and_is_not_captured_as_last_name_then_repeated() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()
    first_prompt = "What's your first name, and would you prefer I reach out via email or phone?"
    first = engine.redact(
        "Raymond and I can be reached @ 306 531-6571",
        vault,
        previous_assistant_message=first_prompt,
    ).redacted_text
    follow_up = engine.redact(
        "Probably at lease 3 bedrooms and a finished basement suite",
        vault,
    ).redacted_text
    assert first == "<fn_1> and I can be reached @ <ph_1>"
    assert follow_up == "Probably at lease 3 bedrooms and a finished basement suite"


def test_va_homebuying_context_phrase_is_not_name() -> None:
    assert _redact("also i am VA") == "also i am VA"


def test_my_name_is_with_prose_tail_redacts_name_only() -> None:
    text = (
        "My name is Dustin I work at Site Services of Nevada we do portable toilets. "
        "Who handels that for you in the Reno and surrounding areas?"
    )
    assert _redact(text).startswith("My name is <fn_1> I work at Site Services of Nevada")


def test_last_name_prompt_with_known_first_inside_longer_reply_redacts_ln() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()
    _ = engine.redact("Jackie", vault, previous_assistant_message="And your first name?").redacted_text
    redacted = engine.redact(
        "I'm a realtor Jackie Mead",
        vault,
        previous_assistant_message="Just for our records—what's your last name?",
    ).redacted_text
    assert redacted == "I'm a realtor <fn_1> <ln_1>"


def test_phone_then_two_token_name_redacts_both_name_tokens() -> None:
    assert _redact("4156909283 Stanley chia") == "<ph_1> <fn_1> <ln_1>"


def test_coordinated_name_phrase_is_still_redacted() -> None:
    text = "Good morning, I'd like to register my clients, Paul and kelly Townsend."
    assert _redact(text) == "Good morning, I'd like to register my clients, <fn_1> and <mn1_1> <ln_1>."
