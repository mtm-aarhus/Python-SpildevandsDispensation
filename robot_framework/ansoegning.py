"""Reads the application Xflow puts on the queue and builds the table row from it.

Xflow's dataaflevering "DispensationSpildevand til Spatial Suite" sends the same fields it
used to POST to the Azure API, now as the queue element's data. The old API accepted a list
(the Xflow template sends a one-element list) and matched field names case-insensitively,
so both are accepted here too.

Kept free of OpenOrchestrator so tests/ can run it without a robot setup.
"""

import json
from datetime import datetime

FIELDS = (
    "ExternalID",
    "DatoAnsoegning",
    "Vejnavn",
    "Husnummer",
    "TrappeskakterAntal",
    "TrappeskakterAreal",
    "LyskasserAntal",
    "LyskasserAreal",
)

# Only as a fallback: the old API (System.Text.Json) took ISO 8601 alone, so that is what
# Xflow sends.
_DATO_FORMATER = ("%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M")


class UgyldigAnsoegning(Exception):
    """The queue element can't become a row - a business error, not worth a retry."""


def parse_payload(data: str | None) -> list[dict]:
    try:
        payload = json.loads(data or "")
    except json.JSONDecodeError as error:
        raise UgyldigAnsoegning(f"Køelementets data er ikke gyldig JSON: {error}") from None

    items = payload if isinstance(payload, list) else [payload]
    if not items:
        raise UgyldigAnsoegning("Køelementet indeholder ingen ansøgning")
    return [_normalize(item) for item in items]


def _normalize(item) -> dict:
    if not isinstance(item, dict):
        raise UgyldigAnsoegning(f"Forventede et JSON-objekt, fik {type(item).__name__}")

    by_key = {str(key).casefold(): value for key, value in item.items()}
    ansoegning = {}
    for field in FIELDS:
        value = by_key.get(field.casefold())
        ansoegning[field] = None if value is None else str(value).strip()

    ansoegning["DatoAnsoegning"] = parse_dato(ansoegning["DatoAnsoegning"])
    return ansoegning


def parse_dato(value: str | None) -> datetime:
    """The application date as it was entered; an offset, if any, is dropped, not converted."""
    if not value:
        raise UgyldigAnsoegning("DatoAnsoegning mangler")

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        for fmt in _DATO_FORMATER:
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        else:
            raise UgyldigAnsoegning(f"DatoAnsoegning kan ikke læses: {value!r}") from None

    return parsed.replace(tzinfo=None)


def build_row(ansoegning: dict, adresse: dict, added_by: str) -> dict:
    """One row of dbo.DispensationsansoegningSpildevandTrappeskaktLyskasser, as the old API
    filled it: the address as found (not as typed), the rest as sent."""
    return {
        "AddedBy": added_by,
        "ExternalID": ansoegning["ExternalID"],
        "DatoAnsoegning": ansoegning["DatoAnsoegning"],
        "Vejnavn": adresse["Vejnavn"],
        "Husnummer": adresse["Husnummer"],
        "Postnummer": adresse["Postnummer"],
        "Bynavn": adresse["Bynavn"],
        "Ejendomsnummer": adresse["Ejendomsnummer"],
        "Matrikelnummer": adresse["Matrikelnummer"],
        "Laengdegrad": adresse["Laengdegrad"],
        "Breddegrad": adresse["Breddegrad"],
        "TrappeskakterAntal": ansoegning["TrappeskakterAntal"],
        "TrappeskakterAreal": ansoegning["TrappeskakterAreal"],
        "LyskasserAntal": ansoegning["LyskasserAntal"],
        "LyskasserAreal": ansoegning["LyskasserAreal"],
    }
