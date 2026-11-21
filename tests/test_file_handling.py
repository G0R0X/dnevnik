import zipfile

import pytest

from file_handling.main import (
    ATTACHMENT_TEXT_LIMIT,
    find_pages_by_targets,
    get_attachment_path,
    get_page_units,
    get_pages_for_paragraphs,
    get_unit_ranges,
    limit_attachment_text,
    normalize_target,
    pdf_to_printed,
    printed_to_pdf,
    read_attachment,
)


@pytest.mark.parametrize("value, expected", [
    ("№43.4", "43.4"),
    ("  #12  ", "12"),
    ("Упражнение 22", "22"),
    ("Упр. 7", "7"),
    ("Ex. 11", "11"),
    ("Задание 9", "9"),
    ("Task 3", "3"),
    ("43.4", "43.4"),
    ("Вопросы №3", "вопросы №3"),
])
def test_normalize_target(value, expected):
    assert normalize_target(value) == expected


@pytest.mark.parametrize("book, printed, pdf", [
    ("english/1", 32, 33),
    ("english/1", 34, 34),
    ("english/1", 68, 67),
    ("english/1", 238, 232),
    ("algebra/1", 42, 42),
    ("informatics/1", 7, 9),
])
def test_printed_to_pdf(book, printed, pdf):
    assert printed_to_pdf(book, printed) == pdf


def test_printed_to_pdf_without_structure_returns_same_page():
    assert printed_to_pdf("no/such/book", 5) == 5


@pytest.mark.parametrize("book, pdf, printed", [
    ("english/1", 33, 32),
    ("english/1", 34, 34),
    ("english/1", 232, 238),
    ("algebra/1", 320, 320),
    ("informatics/1", 9, 7),
])
def test_pdf_to_printed(book, pdf, printed):
    assert pdf_to_printed(book, pdf) == printed


@pytest.mark.parametrize("pdf", [4, 34, 100, 232, 250])
def test_pdf_to_printed_is_inverse_of_printed_to_pdf(pdf):
    assert printed_to_pdf("english/1", pdf_to_printed("english/1", pdf)) == pdf


def test_pdf_to_printed_without_structure_returns_same_page():
    assert pdf_to_printed("no/such/book", 5) == 5


def test_paragraph_pages_include_boundary_by_default():
    pages = get_pages_for_paragraphs("algebra/1", [42])
    assert pages[0] == 320
    assert pages[-1] == 331
    assert len(pages) == 12


def test_paragraph_pages_without_boundary_stop_before_next_paragraph():
    pages = get_pages_for_paragraphs("algebra/1", [42], include_boundary=False)
    assert pages[-1] == 330
    assert len(pages) == 11


def test_boundary_page_is_exactly_next_paragraph_start():
    assert get_pages_for_paragraphs(
        "algebra/1", [42])[-1] == get_pages_for_paragraphs("algebra/1", [43])[0]


def test_paragraphs_on_same_page_give_same_range():
    assert get_pages_for_paragraphs(
        "history/1", [2]) == get_pages_for_paragraphs("history/1", [3])


def test_paragraphs_on_same_page_are_not_duplicated():
    assert get_pages_for_paragraphs(
        "history/1", [2, 3]) == get_pages_for_paragraphs("history/1", [2])


def test_last_paragraph_ends_at_book_end():
    pages = get_pages_for_paragraphs("algebra/1", [52])
    assert pages[0] == 398
    assert pages[-1] == 478


def test_unknown_paragraph_gives_empty_list():
    assert get_pages_for_paragraphs("algebra/1", [999]) == []


def test_unknown_book_gives_empty_list():
    assert get_pages_for_paragraphs("no/such/book", [1]) == []


def test_unit_ranges_follow_paragraph_headers():
    assert get_unit_ranges("english/1") == [
        ("Unit One", 4, 58),
        ("Unit Two", 59, 122),
        ("Unit Three", 123, 176),
        ("Unit Four", 177, 250),
    ]


def test_book_without_units_has_no_ranges():
    assert get_unit_ranges("algebra/1") == []


def test_page_units_cover_the_book_text():
    units = get_page_units("english/1")
    assert units[4] == "Unit One"
    assert units[60] == "Unit Two"
    assert units[250] == "Unit Four"
    assert len(units) == 247


def test_target_found_once_without_unit():
    assert find_pages_by_targets("english/1", ["71"]) == ([122], [])


def test_repeated_target_is_ambiguous_without_unit():
    pages, missing = find_pages_by_targets("english/1", ["22"])
    assert pages == []
    assert missing == ["22"]


def test_repeated_target_is_found_with_unit():
    assert find_pages_by_targets(
        "english/1", ["22"], unit="Unit Two") == ([81], [])


def test_unknown_unit_gives_nothing():
    assert find_pages_by_targets(
        "english/1", ["22"], unit="Unit Nine") == ([], ["22"])


def test_unknown_target_is_reported_missing():
    assert find_pages_by_targets("english/1", ["999"]) == ([], ["999"])


def test_book_without_index_reports_all_targets_missing():
    assert find_pages_by_targets("chemistry/1", ["22"]) == ([], ["22"])


def test_target_by_number_uses_index():
    pages, missing = find_pages_by_targets("algebra/1", ["43.4"])
    assert missing == []
    assert 336 in pages


def test_target_with_number_sign_is_found():
    assert find_pages_by_targets("algebra/1", ["№43.4"])[1] == []


def test_target_by_block_and_number():
    pages, missing = find_pages_by_targets("algebra/1", ["Вопросы №3"])
    assert missing == []
    assert 18 in pages


def test_short_text_is_stripped_only():
    assert limit_attachment_text("  короткий текст  ") == "короткий текст"


def test_text_exactly_at_limit_is_not_cut():
    text = "a" * ATTACHMENT_TEXT_LIMIT
    assert limit_attachment_text(text) == text


def test_long_text_is_cut_and_marked():
    text = "b" * (ATTACHMENT_TEXT_LIMIT * 3)
    assert limit_attachment_text(text) == (
        "b" * ATTACHMENT_TEXT_LIMIT + "\n[текст файла обрезан]")


def test_reads_utf8_text_file(tmp_path):
    path = tmp_path / "task.txt"
    path.write_text("Задание 5. Прочитать §12.", encoding="utf-8")
    assert read_attachment(path) == ("Задание 5. Прочитать §12.", {})


def test_reads_cp1251_text_file(tmp_path):
    path = tmp_path / "task.txt"
    path.write_bytes("Задание 5".encode("cp1251"))
    assert read_attachment(path) == ("Задание 5", {})


def test_image_is_returned_as_bytes(tmp_path):
    path = tmp_path / "scan.png"
    path.write_bytes(b"\x89PNG fake")
    assert read_attachment(path) == ("", {"scan.png": b"\x89PNG fake"})


def test_doc_is_not_supported(tmp_path):
    path = tmp_path / "old.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0")
    assert read_attachment(path) == ("", {})


def test_docx_text_is_extracted(tmp_path):
    path = tmp_path / "task.docx"
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>Задание 5.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t></w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Прочитать §12.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    assert read_attachment(path) == ("Задание 5.\nПрочитать §12.", {})


def test_attachment_path_found(tmp_path, monkeypatch):
    monkeypatch.setattr("file_handling.main.HOMEWORK_DIR", tmp_path)
    folder = tmp_path / "7"
    folder.mkdir()
    saved = folder / "scan.pdf"
    saved.write_bytes(b"pdf")
    assert get_attachment_path(7) == saved


def test_attachment_path_missing_folder(tmp_path, monkeypatch):
    monkeypatch.setattr("file_handling.main.HOMEWORK_DIR", tmp_path)
    assert get_attachment_path(7) is None


def test_attachment_path_empty_folder(tmp_path, monkeypatch):
    monkeypatch.setattr("file_handling.main.HOMEWORK_DIR", tmp_path)
    (tmp_path / "7").mkdir()
    assert get_attachment_path(7) is None
