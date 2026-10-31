from kipu.replace.render import fit_digits, format_date, spell_email


def test_helpers() -> None:
    assert fit_digits("1.107.360.039", "1107252808") == "1.107.252.808"
    assert fit_digits("+57 321 252 4068", "3001234567") == "+57 300 123 4567"
    assert format_date("1998-11-11", "dmy") == "11/11/1998"
    assert spell_email("szambrano@gmail.com", False) == "szambrano arroba gmail punto com"
