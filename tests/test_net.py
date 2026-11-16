from datetime import date
from net.main import update_env, get_homework_for_week


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


class FakeResponse():
    def __init__(self, data, week):
        self.data = data
        self.week = week
    status = 200

    def raise_for_status(self):
        return True

    async def json(self):
        return self.data[self.week]


class FakeObject():
    def __init__(self, data=None, week=46):
        self.data = data
        self.week = 46

    async def __aenter__(self):
        return FakeResponse(data=self.data, week=self.week)

    async def __aexit__(self, exc_type, exc, tb):
        return None


class FakeHttpSession():
    def __init__(self, data=None):
        self.data = data

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
        return FakeObject(data=self.data, week=week)


class FakeToken():
    access_token = "token"


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
    data = make_payload([make_lesson(event_id=1), make_lesson(event_id=2, lesson_date="2026-11-17", created_homework_id=666)],
                        [make_homework(id=555), make_homework(id=666, lesson_date="2026-11-17", check_at="2026-11-19")])
    weeks = make_payload_for_weeks([46, 47], data=[data])
    session = FakeHttpSession(data=weeks)
    token = FakeToken()
    ans = await get_homework_for_week(2026, 46, token, session, {"Алгебра": 15})
    assert len(ans) == 2
