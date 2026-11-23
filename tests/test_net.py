import pytest
from datetime import date
from net.main import update_env, get_homework_for_week,  save_file, last_current_next_weeks, year_and_week, sanitize_filename
from db.models import homework_files


def make_homework(id=555, lesson_date="2026-11-10", check_at="2026-11-12", text="§42, №43.4", files=None, links=None):
    if files is None:
        files = []
    if links is None:
        links = []
    return {
        "id":  id,
        "lesson_date": lesson_date,
        "check_at": check_at,
        "text": text,
        "files": files,
        "links": links,
    }


def make_lesson(event_id=1, subject="Алгебра", lesson_date="2026-11-10", lesson_number=1, created_homework_id=555):
    return {
        "lesson_event_id": event_id,
        "subject": subject,
        "lesson_date": lesson_date,
        "lesson_number": lesson_number,
        "created_homework_id": created_homework_id,
    }


def make_payload(lessons=None, homeworks=None):
    return {
        "schedule": lessons,
        "homeworks": homeworks
    }


def make_payload_for_weeks(weeks=None, data=None):
    if weeks is None:
        weeks = [46]
    return {week: d for week, d in zip(weeks, data)}


def test_update_env_edit_lines(tmp_path):
    first = "TEST_DATA=123ABC"
    second = "ANOTHER_DATA=321XYZ"
    first_name = "TEST_DATA"
    first_edited = "987NEW"
    comment = "#alalalla"
    with open(tmp_path / ".env", "w", encoding="utf-8") as f:
        f.write(comment + '\n')
        f.write(first + '\n')
        f.write(second + '\n')

    update_env({first_name: first_edited}, tmp_path / ".env")

    expected_value = "#alalalla\nTEST_DATA=987NEW\nANOTHER_DATA=321XYZ\n"
    with open(tmp_path / ".env", "r", encoding="utf-8") as f:
        val = f.read()
        assert expected_value == val, (expected_value, val)


def test_update_env_add_lines(tmp_path):
    initial_data = "#alalalla\nTEST_DATA=987NEW\nANOTHER_DATA=321XYZ\n"
    new_data = {"ADDED_DATA": "5678"}
    expected_data = "#alalalla\nTEST_DATA=987NEW\nANOTHER_DATA=321XYZ\nADDED_DATA=5678\n"
    with open(tmp_path/".env", "w", encoding="utf-8") as f:
        f.write(initial_data)

    update_env(new_data, tmp_path / ".env")

    with open(tmp_path/".env", "r", encoding="utf-8") as f:
        data = f.read()
    assert data == expected_data


def test_update_env_create_env_file(tmp_path):
    data = {"TEST_DATA": "TEST", "ANOTHER_DATA": "123ABC"}
    expected_data = "TEST_DATA=TEST\nANOTHER_DATA=123ABC\n"
    update_env(data, tmp_path/".env")

    with open(tmp_path/".env", "r", encoding="utf-8") as f:
        assert f.read() == expected_data


class FakeContent():
    def __init__(self, chunks=None):
        self.chunks = chunks

    async def iter_chunked(self, n):
        for chunk in self.chunks:
            yield chunk


class FakeResponse():
    def __init__(self, data, week, token, chunks=None):
        self.data = data
        self.week = week
        self.token = token
        self.content = FakeContent(chunks)
        self.set_status()

    def set_status(self):
        if not self.token:
            self.status = 200
            return
        if self.token.access_token == "old_token":
            self.status = 401
        elif self.token.access_token == "new_token":
            self.status = 200
        else:
            self.status = 404

    def raise_for_status(self):
        if self.status >= 400:
            raise Exception()
        return None

    async def json(self):
        return self.data[self.week]


class FakeFileResponse():
    def __init__(self, chunks):
        self.chunks = chunks
        self.content = FakeContent(chunks)

    def raise_for_status(self):
        return None


class FakeObject():
    def __init__(self, data=None, week=46, token=None, chunks=None):
        self.data = data
        self.week = week
        self.token = token
        self.chunks = chunks

    async def __aenter__(self):
        if self.week is None:
            return FakeFileResponse(self.chunks)
        return FakeResponse(data=self.data, week=self.week, token=self.token)

    async def __aexit__(self, exc_type, exc, tb):
        return None


class FakeHttpSession():
    def __init__(self, data=None, token=None):
        self.data = data
        self.token = token
        self.content = FakeContent()
        self.history = []

    def get(self, url, headers=None):
        a = url.split("&week=")
        s = ''
        for c in a[1]:
            try:
                int(c)
                s += c
            except ValueError:
                break
        week = int(s)
        self.history.append(week)
        return FakeObject(data=self.data, week=week, token=self.token)


class FakeFileSession():
    def __init__(self, url,
                 chunks=None):
        self.chunks = chunks
        self.url = url
        self.request_url = None

    def get(self, url):
        self.request_url = url
        return FakeObject(week=None, chunks=self.chunks)


class FakeToken():
    def __init__(self, token="new_token"):
        self.access_token = token

    async def refresh(self, session):
        self.access_token = "new_token"


async def test_get_db_object_from_data():
    data = make_payload(lessons=[make_lesson()], homeworks=[make_homework()])
    weeks = make_payload_for_weeks(data=[data])
    session = FakeHttpSession(data=weeks)
    token = FakeToken()
    ans = await get_homework_for_week(2026, 46, token, session, {"Алгебра": 15})
    assert len(ans) == 1
    assert ans[0].kiasuo_id == 555
    assert ans[0].subject_id == 15
    assert ans[0].text == "§42, №43.4"
    assert ans[0].check_at == date(2026, 11, 12)
    assert ans[0].lesson_date == date(2026, 11, 10)


async def test_get_db_object_from_data_with_unknown_subject():
    data = make_payload([make_lesson()],  [make_homework()])
    weeks = make_payload_for_weeks(data=[data])
    session = FakeHttpSession(data=weeks)
    token = FakeToken()
    ans = await get_homework_for_week(2026, 46, token, session, {"Химия": 55})
    assert ans == []


async def test_get_db_object_from_data_without_lesson():
    data = make_payload(
        [make_lesson(created_homework_id=111)], [make_homework()])
    weeks = make_payload_for_weeks(data=[data])
    session = FakeHttpSession(data=weeks)
    token = FakeToken()
    ans = await get_homework_for_week(2026, 46, token, session, {"Алгебра": 15})
    assert ans == []


async def test_skips_duplicate_homework():
    data = make_payload([make_lesson(event_id=1, created_homework_id=555, lesson_number=1), make_lesson(
        event_id=2,  created_homework_id=556, lesson_number=2)], [make_homework(id=555),  make_homework(id=556)])
    weeks = make_payload_for_weeks(data=[data])
    session = FakeHttpSession(data=weeks)
    token = FakeToken()
    ans = await get_homework_for_week(2026, 46, token, session, {"Алгебра": 15})
    assert len(ans) == 1
    assert ans[0].kiasuo_id == 555


async def test_get_db_object_from_data_with_links_and_files():
    data = make_payload([make_lesson()], [make_homework(files=[
                        {"url": "/f/1", "title": "task.pdf"}],  links=[{"url": "https://test_url.com", "title": "Правила"}])])
    weeks = make_payload_for_weeks(data=[data])
    session = FakeHttpSession(data=weeks)
    token = FakeToken()
    ans = await get_homework_for_week(2026, 46, token, session, {"Алгебра": 15})
    files = ans[0].homework_files
    links = ans[0].homework_links
    assert len(files) == 1
    assert len(links) == 1
    assert files[0].title == "task.pdf"
    assert links[0].url == "https://test_url.com"


async def test_loads_another_week():
    data1 = make_payload([make_lesson(event_id=1, created_homework_id=1111)], [make_homework(id=1111), make_homework(
        id=666, lesson_date="2026-11-17", check_at="2026-11-19")])
    data2 = make_payload(
        [make_lesson(event_id=2, lesson_date="2026-11-17",  created_homework_id=666)], [])
    weeks = make_payload_for_weeks([46, 47], data=[data1]+[data2])
    session = FakeHttpSession(data=weeks)
    token = FakeToken()
    ans = await get_homework_for_week(2026, 46, token, session, {"Алгебра": 15})
    by_id = {h.kiasuo_id: h for h in ans}
    assert sorted(by_id) == [666, 1111]
    assert by_id[666].lesson_date == date(2026, 11, 17)


async def test_refresh():
    data = make_payload(lessons=[make_lesson()], homeworks=[make_homework()])
    weeks = make_payload_for_weeks(data=[data])
    token = FakeToken(token="old_token")
    session = FakeHttpSession(data=weeks, token=token)
    ans = await get_homework_for_week(2026, 46, token, session, {"Алгебра": 15})
    assert len(ans) == 1
    assert ans[0].kiasuo_id == 555
    assert token.access_token == "new_token"


async def test_downolads_week_once():
    data1 = make_payload([], [make_homework(id=1111,  lesson_date="2026-11-18", text="first"), make_homework(
        id=666, lesson_date="2026-11-17", text="second")])
    data2 = make_payload(
        [make_lesson(event_id=2, lesson_date="2026-11-17",  created_homework_id=666), make_lesson(event_id=3, lesson_date="2026-11-18",  created_homework_id=1111)], [])
    weeks = make_payload_for_weeks([46, 47], data=[data1]+[data2])
    session = FakeHttpSession(data=weeks)
    token = FakeToken()
    ans = await get_homework_for_week(2026, 46, token, session, {"Алгебра": 15})
    assert len(ans) == 2
    assert session.history.count(47) == 1
    assert session.history.count(46) == 1


def test_default_weeks():
    assert last_current_next_weeks(date(2026, 12, 31)) == [
        (2026, 52), (2026, 53), (2027, 1)]
    assert last_current_next_weeks(date(2027, 3, 10)) == [
        (2027, 9), (2027, 10), (2027, 11)]
    weeks = last_current_next_weeks()
    assert len(weeks) == 3
    assert year_and_week(date.today()) in weeks


async def test_save_file(tmp_path, monkeypatch):
    monkeypatch.setattr("net.main.HOMEWORK_DIR", tmp_path)
    file = homework_files.HomeworkFile(
        id=7, homework_id=1, url='/f/1', title="test.pdf")

    session = FakeFileSession(url='/f/1', chunks=[b"abc",  b'test'])
    await save_file(file, session)
    assert (tmp_path / "7" / "test.pdf").read_bytes() == b"abctest"
    assert session.request_url == "https://diaryapi.kiasuo.ru/f/1"


@pytest.mark.parametrize("raw, expected", [
    ("task.pdf", "task.pdf"),
    ("../../etc/passwd", "passwd"),
    ("my file?.pdf", "my_file_.pdf"),
])
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("raw", ["", ".", ".."])
def test_sanitize_filename_rejects_bad_names(raw):
    with pytest.raises(ValueError):
        sanitize_filename(raw)


async def test_big_file(tmp_path, monkeypatch):
    monkeypatch.setattr("net.main.HOMEWORK_DIR", tmp_path)
    file = homework_files.HomeworkFile(
        id=7, homework_id=1, url="/f/1", title="big.bin")
    session = FakeFileSession(
        chunks=[b"x" * 1_000_000] * 21, url="/f/1")   # 21 МБ
    with pytest.raises(ValueError):
        await save_file(file, session)
    assert not (tmp_path / "7" / "big.bin").exists()
