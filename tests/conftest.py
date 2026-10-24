import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--regenerate-goldens",
        action="store_true",
        help="Overwrite the golden images with what the renderers draw now; review the diff before committing",
    )
