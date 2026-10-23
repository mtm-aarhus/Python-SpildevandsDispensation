"""This module contains the main process of the robot.

One queue element = one approved application from Xflow's workflow "Ansøgning om dispensation
fra kravet om separering af spildevand for trappeskakt og lyskasser". The robot finds the
address, then writes the row SpatialMap shows - what POST /api/DispensationSpildevand-
TrappeskaktLyskasser on mtm-apiservices did until DAWA closed.
"""

from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection
from OpenOrchestrator.database.queues import QueueElement

from robot_framework import adresse
from robot_framework import ansoegning
from robot_framework import config
from robot_framework import database
from robot_framework.exceptions import BusinessError


def process(orchestrator_connection: OrchestratorConnection, queue_element: QueueElement | None = None) -> None:
    """Do the primary process of the robot."""
    orchestrator_connection.log_trace("Running process.")

    try:
        ansoegninger = ansoegning.parse_payload(queue_element.data)
    except ansoegning.UgyldigAnsoegning as error:
        raise BusinessError(str(error)) from error

    services = get_address_services(orchestrator_connection)
    rows = []
    for item in ansoegninger:
        try:
            found = adresse.resolve(item["Vejnavn"], item["Husnummer"], services)
        except adresse.UkendtAdresse as error:
            raise BusinessError(f"ExternalID {item['ExternalID']}: {error}") from error

        orchestrator_connection.log_info(
            f"ExternalID {item['ExternalID']}: {item['Vejnavn']} {item['Husnummer']} -> "
            f"{found['Vejnavn']} {found['Husnummer']}, {found['Postnummer']} {found['Bynavn']} "
            f"(matr.nr. {found['Matrikelnummer']}, ejd.nr. {found['Ejendomsnummer']})"
        )
        if not found["Matrikelnummer"] or not found["Ejendomsnummer"]:
            orchestrator_connection.log_info(
                f"ExternalID {item['ExternalID']}: Datafordeleren gav ikke både matrikel- og ejendomsnummer"
            )
        rows.append(ansoegning.build_row(item, found, config.ADDED_BY))

    server = orchestrator_connection.get_constant(config.SQL_SERVER_CONSTANT).value
    sql = orchestrator_connection.get_credential(config.SQL_CREDENTIAL)
    conn = database.connect(server, sql.username, sql.password)
    try:
        cursor = conn.cursor()
        for row in rows:
            existing_id = database.find_same(cursor, row)
            if existing_id is not None:
                orchestrator_connection.log_info(
                    f"ExternalID {row['ExternalID']} står allerede i tabellen (Id {existing_id}) - springes over"
                )
                continue
            database.insert(cursor, row)
        conn.commit()
    finally:
        conn.close()


def get_address_services(orchestrator_connection: OrchestratorConnection) -> dict:
    # Credentials hold URL (username) + token/API key (password).
    adressevaelger = orchestrator_connection.get_credential(config.ADRESSEVAELGER_CREDENTIAL)
    datafordeler = orchestrator_connection.get_credential(config.DATAFORDELER_CREDENTIAL)
    return {
        "adressevaelger_url": adressevaelger.username.rstrip("/"),
        "adressevaelger_token": adressevaelger.password,
        "datafordeler_url": datafordeler.username.rstrip("/"),
        "datafordeler_api_key": datafordeler.password,
    }
