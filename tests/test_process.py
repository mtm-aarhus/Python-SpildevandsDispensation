"""robot_framework/process.py end to end: queue element in, row out - with OpenOrchestrator,
the web services and the database faked."""

import importlib
import json
import sys
import types
import unittest
from datetime import datetime
from unittest import mock

from robot_framework import adresse, database
from tests.fakes import SERVICES, FakeServices
from tests.test_ansoegning_database import XFLOW_GAMMEL


def _stub_openorchestrator():
    """process.py imports OpenOrchestrator (and, via exceptions.py, PIL) - neither is in
    tests/.venv, and only their names are needed here."""
    stubs = {
        "OpenOrchestrator": types.ModuleType("OpenOrchestrator"),
        "OpenOrchestrator.orchestrator_connection": types.ModuleType("OpenOrchestrator.orchestrator_connection"),
        "OpenOrchestrator.orchestrator_connection.connection": types.ModuleType("connection"),
        "OpenOrchestrator.database": types.ModuleType("OpenOrchestrator.database"),
        "OpenOrchestrator.database.queues": types.ModuleType("queues"),
        "PIL": types.ModuleType("PIL"),
    }
    stubs["OpenOrchestrator.orchestrator_connection.connection"].OrchestratorConnection = object
    stubs["OpenOrchestrator.database.queues"].QueueElement = object
    stubs["OpenOrchestrator.database.queues"].QueueStatus = object
    stubs["PIL"].ImageGrab = None
    return mock.patch.dict(sys.modules, stubs)


def _load_process():
    """Import process.py with the stubs in place; afterwards sys.modules is as it was (so the
    other test modules never see the stubs), and process.py keeps what it imported."""
    with _stub_openorchestrator():
        for name in ("robot_framework.process", "robot_framework.exceptions", "robot_framework.error_screenshot"):
            sys.modules.pop(name, None)
        process = importlib.import_module("robot_framework.process")
        exceptions = importlib.import_module("robot_framework.exceptions")
    return process, exceptions.BusinessError


class FakeOrchestrator:
    def __init__(self):
        self.logs = []
        self.credentials = {
            "Adressevælger": types.SimpleNamespace(username=SERVICES["adressevaelger_url"] + "/",
                                                   password=SERVICES["adressevaelger_token"]),
            "DatafordelerAPI": types.SimpleNamespace(username=SERVICES["datafordeler_url"],
                                                     password=SERVICES["datafordeler_api_key"]),
            "SpildevandsSQLUser": types.SimpleNamespace(username="spildevand", password="hemmeligt"),
        }
        self.constants = {
            "SpildevandsSQLURL": types.SimpleNamespace(value="mtm-sql-01.database.windows.net"),
        }

    def get_credential(self, name):
        return self.credentials[name]

    def get_constant(self, name):
        return self.constants[name]

    def log_info(self, text):
        self.logs.append(text)

    def log_trace(self, text):
        pass


class ProcessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process, cls.BusinessError = _load_process()

    def setUp(self):
        self.oc = FakeOrchestrator()
        self.fake = FakeServices()
        self.conn = mock.Mock()
        self.cursor = self.conn.cursor.return_value
        self.cursor.fetchall.return_value = []

    def run_process(self, payload):
        element = types.SimpleNamespace(data=json.dumps(payload, ensure_ascii=False))
        with mock.patch.object(adresse.requests, "request", side_effect=self.fake), \
                mock.patch.object(database, "connect", return_value=self.conn) as connect:
            self.process.process(self.oc, element)
        return connect

    def inserts(self):
        return [c.args for c in self.cursor.execute.call_args_list if c.args[0] == database.INSERT_SQL]

    def test_writes_the_row_spatialmap_shows(self):
        connect = self.run_process(XFLOW_GAMMEL)

        connect.assert_called_once_with("mtm-sql-01.database.windows.net", "spildevand", "hemmeligt")
        ((sql, *params),) = self.inserts()
        row = dict(zip(database.INSERT_COLUMNS, params))
        lon, lat = adresse.utm32_to_wgs84(576226.37, 6228684.5)
        self.assertEqual(row, {
            "AddedBy": "xflow-oo",
            "ExternalID": "123456",
            "DatoAnsoegning": datetime(2026, 10, 5),
            "Vejnavn": "Agrivej",
            "Husnummer": "11",
            "Postnummer": "8240",
            "Bynavn": "Risskov",
            "Ejendomsnummer": "54321",
            "Matrikelnummer": "12ab",
            "Laengdegrad": lon,
            "Breddegrad": lat,
            "TrappeskakterAntal": "1",
            "TrappeskakterAreal": "2,16",
            "LyskasserAntal": "0",
            "LyskasserAreal": "",
        })
        self.conn.commit.assert_called_once()
        self.conn.close.assert_called_once()
        # the credential's trailing slash is not doubled into the path
        self.assertTrue(all("//husnumre" not in c["url"] for c in self.fake.calls))

    def test_unknown_address_is_a_business_error_and_touches_no_database(self):
        payload = [{**XFLOW_GAMMEL[0], "Vejnavn": "Agervej"}]
        with self.assertRaisesRegex(self.BusinessError, "ExternalID 123456: Adresse findes ikke i Aarhus Kommune"):
            self.run_process(payload)
        self.conn.cursor.assert_not_called()

    def test_bad_data_is_a_business_error(self):
        with self.assertRaises(self.BusinessError):
            self.run_process([{**XFLOW_GAMMEL[0], "DatoAnsoegning": None}])

    def test_a_replayed_element_is_skipped(self):
        self.cursor.fetchall.return_value = [
            (41, "Agrivej", "11", datetime(2026, 10, 5), "1", "2,16", "0", ""),
        ]
        self.run_process(XFLOW_GAMMEL)
        self.assertEqual(self.inserts(), [])
        self.assertTrue(any("Id 41" in line for line in self.oc.logs))
        self.conn.commit.assert_called_once()

    def test_database_error_rolls_nothing_in_and_closes(self):
        self.cursor.execute.side_effect = [None, RuntimeError("Login failed")]
        with self.assertRaisesRegex(RuntimeError, "Login failed"):
            self.run_process(XFLOW_GAMMEL)
        self.conn.commit.assert_not_called()
        self.conn.close.assert_called_once()


class QueueFrameworkTest(unittest.TestCase):
    def test_business_errors_are_not_retried(self):
        with open("robot_framework/queue_framework.py", encoding="utf-8") as source:
            text = source.read()
        retry_loop = text[text.index("for attempt in range"):text.index("set_queue_element_status")]
        self.assertLess(retry_loop.index("except BusinessError"), retry_loop.index("except Exception"))


if __name__ == "__main__":
    unittest.main()
