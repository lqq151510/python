"""Run independently with unittest; never connects to a database."""

import unittest

from sqlalchemy.engine import make_url

from tests.db_guard import require_disposable_database


class TestDatabaseGuard(unittest.TestCase):
    def test_accepts_matching_local_disposable_databases(self):
        for prefix in ("test", "validation"):
            name = f"devassist_{prefix}_012345abcdef"
            with self.subTest(prefix=prefix):
                require_disposable_database(
                    make_url(f"postgresql+psycopg://postgres@127.0.0.1:5678/{name}"),
                    name,
                )

    def test_refuses_unsafe_or_mismatched_targets(self):
        name = "devassist_test_012345abcdef"
        cases = [
            ("postgresql://postgres@localhost/app", name),
            (f"postgresql://postgres@localhost/{name}", None),
            (f"postgresql://postgres@localhost/{name}", "devassist_test_111111111111"),
            (f"postgresql://postgres@remote.example/{name}", name),
            (f"postgresql://postgres@localhost/{name}?host=remote.example", name),
            ("postgresql://postgres@localhost/devassist_test_app", "devassist_test_app"),
        ]
        for url, marker in cases:
            with self.subTest(url=url, marker=marker), self.assertRaises(RuntimeError):
                require_disposable_database(make_url(url), marker)


if __name__ == "__main__":
    unittest.main()
