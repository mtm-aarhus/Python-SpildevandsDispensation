"""robot_framework/ansoegning.py and database.py: from queue data to the INSERT."""

import json
import re
import unittest
from datetime import datetime
from unittest import mock

from robot_framework import ansoegning, config, database

# Xflow's dataaflevering template, as it was POSTed to the Azure API (a one-element list)
XFLOW_GAMMEL = [{
    "Vejnavn": "Agrivej",
    "Husnummer": "11",
    "TrappeskakterAntal": "1",
    "TrappeskakterAreal": "2,16",
    "LyskasserAntal": "0",
    "LyskasserAreal": "",
    "ExternalID": "123456",
    "DatoAnsoegning": "2026-10-05",
}]

FUNDET = {
    "HusnummerId": "0a3f5095-ddec-32b8-e044-0003ba298018",
    "Vejnavn": "Agrivej",
    "Husnummer": "11",
    "Postnummer": "8240",
    "Bynavn": "Risskov",
    "Matrikelnummer": "12ab",
    "Ejendomsnummer": "54321",
    "Laengdegrad": 10.21,
    "Breddegrad": 56.19,
}


class ParsePayloadTest(unittest.TestCase):
    def test_the_old_xflow_list(self):
        (item,) = ansoegning.parse_payload(json.dumps(XFLOW_GAMMEL))
        self.assertEqual(item["Vejnavn"], "Agrivej")
        self.assertEqual(item["TrappeskakterAreal"], "2,16")
        self.assertEqual(item["LyskasserAreal"], "")
        self.assertEqual(item["DatoAnsoegning"], datetime(2026, 10, 5))

    def test_a_single_object_with_other_casing(self):
        data = json.dumps({"externalid": "9", "VEJNAVN": "Agrivej", "husnummer": 11, "datoansoegning": "2026-10-05"})
        (item,) = ansoegning.parse_payload(data)
        self.assertEqual((item["ExternalID"], item["Vejnavn"], item["Husnummer"]), ("9", "Agrivej", "11"))
        self.assertIsNone(item["LyskasserAntal"])

    def test_dates(self):
        for text, expected in [
            ("2026-10-05", datetime(2026, 10, 5)),
            ("2026-10-05T00:00:00", datetime(2026, 10, 5)),
            ("2026-10-05T13:45:10.123", datetime(2026, 10, 5, 13, 45, 10, 123000)),
            ("2026-10-05T00:00:00+02:00", datetime(2026, 10, 5)),
            ("2026-10-05T00:00:00Z", datetime(2026, 10, 5)),
            ("05-10-2026", datetime(2026, 10, 5)),
            ("05.10.2026", datetime(2026, 10, 5)),
        ]:
            with self.subTest(text=text):
                self.assertEqual(ansoegning.parse_dato(text), expected)

    def test_bad_input_is_a_business_error(self):
        for data in ["", "not json", "[]", "[1]", json.dumps([{**XFLOW_GAMMEL[0], "DatoAnsoegning": ""}]),
                     json.dumps([{**XFLOW_GAMMEL[0], "DatoAnsoegning": "i går"}])]:
            with self.subTest(data=data), self.assertRaises(ansoegning.UgyldigAnsoegning):
                ansoegning.parse_payload(data)

    def test_the_row_takes_the_address_as_found(self):
        (item,) = ansoegning.parse_payload(json.dumps([{**XFLOW_GAMMEL[0], "Vejnavn": "agrivej"}]))
        row = ansoegning.build_row(item, FUNDET, "xflow-oo")
        self.assertEqual(row["Vejnavn"], "Agrivej")
        self.assertEqual(row["AddedBy"], "xflow-oo")
        self.assertEqual(set(row), set(database.INSERT_COLUMNS))


class InsertTest(unittest.TestCase):
    def setUp(self):
        (item,) = ansoegning.parse_payload(json.dumps(XFLOW_GAMMEL))
        self.row = ansoegning.build_row(item, FUNDET, "xflow-oo")

    def test_one_value_per_placeholder(self):
        self.assertEqual(database.INSERT_SQL.count("?"), len(database.INSERT_COLUMNS))

    def test_columns_and_values_line_up(self):
        columns = re.search(r"\((.*?)\)\s*VALUES", database.INSERT_SQL, re.S).group(1)
        columns = [c.strip() for c in columns.split(",")]
        values = re.search(r"VALUES\s*\((.*)\)\s*$", database.INSERT_SQL, re.S).group(1)
        values = [v.strip() for v in re.split(r",(?![^(]*\))", values)]
        self.assertEqual(len(columns), len(values))
        self.assertEqual(values[columns.index("AddedTimestamp")], "SYSUTCDATETIME()")
        self.assertEqual(values[columns.index("Geometri")], "geometry::Point(?, ?, 4326)")
        # every other column takes its own value, in INSERT_COLUMNS order
        plain = [c for c, v in zip(columns, values) if v == "?"]
        expected = [c for c in database.INSERT_COLUMNS if c not in ("Laengdegrad", "Breddegrad")]
        self.assertEqual(plain, expected)

    def test_point_gets_longitude_then_latitude(self):
        cursor = mock.Mock()
        database.insert(cursor, self.row)
        sql, *params = cursor.execute.call_args.args
        before_point = sql[: sql.index("geometry::Point(")].count("?")
        self.assertEqual(params[before_point:before_point + 2], [10.21, 56.19])

    def test_same_application_is_found(self):
        cursor = mock.Mock()
        cursor.fetchall.return_value = [
            (7, "Agrivej", "11", datetime(2026, 10, 5), "1", "2,16", "0", "999"),  # other values
            (8, "Agrivej", "11", datetime(2026, 10, 5), "1", "2,16", "0", ""),
        ]
        self.assertEqual(database.find_same(cursor, self.row), 8)
        self.assertEqual(cursor.execute.call_args.args[1], "123456")

    def test_other_values_are_not_the_same(self):
        cursor = mock.Mock()
        cursor.fetchall.return_value = [(7, "Agrivej", "11", datetime(2026, 9, 1), "1", "2,16", "0", "")]
        self.assertIsNone(database.find_same(cursor, self.row))

    def test_no_external_id_never_matches(self):
        cursor = mock.Mock()
        self.assertIsNone(database.find_same(cursor, {**self.row, "ExternalID": None}))
        cursor.execute.assert_not_called()

    def test_connection_string(self):
        with mock.patch.object(database.pyodbc, "connect") as connect:
            database.connect(" mtm-sql-01.database.windows.net\n", "spildevand", "pa;ss}word")
        conn_str = connect.call_args.args[0]
        self.assertIn("SERVER=mtm-sql-01.database.windows.net;", conn_str)
        self.assertIn(f"DATABASE={config.SQL_DATABASE};", conn_str)
        self.assertIn("UID={spildevand};", conn_str)
        self.assertIn("PWD={pa;ss}}word};", conn_str)
        self.assertIn("Encrypt=yes;", conn_str)
        self.assertEqual(connect.call_args.kwargs, {"autocommit": False})


if __name__ == "__main__":
    unittest.main()
