"""Tests for voicetodo.decompose.decompose_rules."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from voicetodo.decompose import decompose_rules


CASES = [
    # (transcript, expected todos)
    (
        "I need to buy milk and pick up the kids",
        ["Buy milk", "Pick up the kids"],
    ),
    (
        "Remember to call mom about Saturday and don't forget to renew the car registration",
        ["Call mom about Saturday", "Renew the car registration"],
    ),
    (
        "First, finish the report. Second, email it to John. Third, schedule the meeting for Tuesday.",
        ["Finish the report", "Email it to John", "Schedule the meeting for Tuesday"],
    ),
    (
        "Buy bread",
        ["Buy bread"],
    ),
    (
        "okay so um I gotta finish the slides and then send them to Sarah",
        ["Finish the slides", "Send them to Sarah"],
    ),
    (
        "todo: pick up dry cleaning. task: book dentist appointment.",
        ["Pick up dry cleaning", "Book dentist appointment"],
    ),
    (
        "I'll call the plumber tomorrow, and I should also order more dog food",
        ["Call the plumber tomorrow", "Order more dog food"],
    ),
    (
        "",
        [],
    ),
    (
        "Hmm, the weather is nice today.",  # no actionable intent -> single fallback todo
        ["The weather is nice today"],
    ),
    (
        "Pay the electric bill before Friday and review pull request 42",
        ["Pay the electric bill before Friday", "Review pull request 42"],
    ),
    (
        "I'm going to clean the garage this weekend",
        ["Clean the garage this weekend"],
    ),
    (
        "Make sure I water the plants. Also, schedule the oil change.",
        ["Water the plants", "Schedule the oil change"],
    ),
    (
        "One more thing, send the invoice to Acme Corp",
        ["Send the invoice to Acme Corp"],
    ),
]


def main():
    failures = []
    for transcript, expected in CASES:
        got = decompose_rules(transcript)
        if got != expected:
            failures.append((transcript, expected, got))

    for transcript, expected, got in failures:
        print("FAIL:")
        print(f"  input:    {transcript!r}")
        print(f"  expected: {expected}")
        print(f"  got:      {got}")
        print()

    print(f"{len(CASES) - len(failures)}/{len(CASES)} passed")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
