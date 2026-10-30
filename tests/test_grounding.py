from __future__ import annotations

from offsyde.nl2sql.grounding import is_grounded, render_rows

COLS = ["name", "goals", "xg"]
ROWS = [["Victor Boniface", 14, 15.97], ["Florian Wirtz", 11, 8.47]]


def test_numbers_from_rows_are_grounded():
    assert is_grounded("Boniface scored 14 goals, Wirtz 11.", COLS, ROWS)
    assert is_grounded("Boniface had 15.97 xG.", COLS, ROWS)
    assert is_grounded("Boniface had about 16 xG.", COLS, ROWS) is False  # 16 is not in the data


def test_invented_numbers_are_rejected():
    assert not is_grounded("Boniface scored 17 goals.", COLS, ROWS)
    assert not is_grounded("Wirtz scored 11 goals in 38 matches.", COLS, ROWS)


def test_numbers_from_question_and_sql_are_allowed():
    assert is_grounded("Top 2 scorers: Boniface and Wirtz.", COLS, ROWS, question="top 2 scorers")
    assert is_grounded("After minute 85 there were 3 goals.", ["n"], [[3]], sql="... minute >= 85")


def test_row_count_and_list_numbering_are_allowed():
    assert is_grounded("1. Boniface 14\n2. Wirtz 11", COLS, ROWS)
    assert is_grounded("There are 2 players.", COLS, ROWS)


def test_german_decimal_comma_and_thousands():
    assert is_grounded("Der Wert beträgt 15,97.", COLS, ROWS)
    assert is_grounded("Es waren 129.715 Ereignisse.", ["n"], [[129715]])
    assert is_grounded("There were 129,715 events.", ["n"], [[129715]])


def test_percentages_from_fractions():
    assert is_grounded("A completion rate of 92%.", ["rate"], [[0.92]])


def test_dates_in_string_cells_count():
    assert is_grounded("It happened on 2024-05-18.", ["match_date"], [["2024-05-18"]])


def test_answer_without_numbers_is_fine():
    assert is_grounded("Victor Boniface.", COLS, ROWS)


def test_render_rows_fallbacks():
    assert render_rows(["n"], [[28]]) == "n: 28"
    assert render_rows(["n"], []) == "No matching data found."
    assert render_rows(["n"], [], lang="de") == "Keine passenden Daten gefunden."
    text = render_rows(COLS, ROWS)
    assert "name: Victor Boniface, goals: 14, xg: 15.97" in text
    many = render_rows(["a"], [[i] for i in range(30)], limit=5)
    assert many.endswith("(+25 more)")
