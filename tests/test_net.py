from net.main import update_env


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
