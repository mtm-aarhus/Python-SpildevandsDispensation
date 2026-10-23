"""Writes the row to the table SpatialMap's theme "Dispensation – kælderskakt/lyskasse" reads
(Azure SQL mtm-sql-01, database ApiDataStorage) - the same table the old Azure API wrote to."""

import pyodbc

from robot_framework import config

TABLE = "dbo.DispensationsansoegningSpildevandTrappeskaktLyskasser"

# AddedTimestamp: the old API wrote DateTime.Now on the App Service, which runs in UTC.
# Geometri: a point, x = longitude, y = latitude, SRID 4326 - as the old API stored DAWA's.
INSERT_SQL = f"""
INSERT INTO {TABLE} (
    AddedBy, ExternalID, AddedTimestamp, DatoAnsoegning,
    Vejnavn, Husnummer, Postnummer, Bynavn, Ejendomsnummer, Matrikelnummer, Geometri,
    TrappeskakterAntal, TrappeskakterAreal, LyskasserAntal, LyskasserAreal
) VALUES (
    ?, ?, SYSUTCDATETIME(), ?,
    ?, ?, ?, ?, ?, ?, geometry::Point(?, ?, 4326),
    ?, ?, ?, ?
)
"""

INSERT_COLUMNS = (
    "AddedBy", "ExternalID", "DatoAnsoegning",
    "Vejnavn", "Husnummer", "Postnummer", "Bynavn", "Ejendomsnummer", "Matrikelnummer",
    "Laengdegrad", "Breddegrad",
    "TrappeskakterAntal", "TrappeskakterAreal", "LyskasserAntal", "LyskasserAreal",
)

# What makes a row "the same application again": a queue element that is run twice (say the
# robot stopped between commit and marking it done) must not put a second pin on the map.
SAME_COLUMNS = (
    "Vejnavn", "Husnummer", "DatoAnsoegning",
    "TrappeskakterAntal", "TrappeskakterAreal", "LyskasserAntal", "LyskasserAreal",
)
EXISTING_SQL = f"SELECT Id, {', '.join(SAME_COLUMNS)} FROM {TABLE} WHERE ExternalID = ?"


def _odbc_value(value: str) -> str:
    """Braces let a password hold ; and = - a } inside is written twice."""
    return "{" + str(value).replace("}", "}}") + "}"


def connect(server: str, username: str, password: str) -> pyodbc.Connection:
    return pyodbc.connect(
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER={server.strip()};"
        f"DATABASE={config.SQL_DATABASE};"
        f"UID={_odbc_value(username)};"
        f"PWD={_odbc_value(password)};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;",
        autocommit=False,
    )


def find_same(cursor, row: dict) -> int | None:
    """Id of a row already holding this application, if any."""
    if not row["ExternalID"]:
        return None
    cursor.execute(EXISTING_SQL, row["ExternalID"])
    for existing in cursor.fetchall():
        if all(existing[i + 1] == row[column] for i, column in enumerate(SAME_COLUMNS)):
            return existing[0]
    return None


def insert(cursor, row: dict) -> None:
    cursor.execute(INSERT_SQL, *[row[column] for column in INSERT_COLUMNS])
