from demo import greet


def test_normal_name() -> None:
    assert greet("Sean") == "Hello, Sean"


def test_empty_name() -> None:
    assert greet("") == "Hello, Guest"


def test_whitespace_name() -> None:
    assert greet("   ") == "Hello, Guest"
