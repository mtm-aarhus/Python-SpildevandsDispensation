"""Compares the robot's lookups with what DAWA gave the old Azure API, row by row.

Run it once before going live (and again if Datafordeleren changes): it is the only check
of the Datafordeler half (matrikel- and ejendomsnummer), which the unit tests can only fake.

1. In SSMS on ApiDataStorage, run this and copy the single cell it returns into
   tests/dawa_raekker.json (gitignored - real addresses):

       SELECT (
           SELECT TOP 60 Id, ExternalID, DatoAnsoegning, AddedTimestamp, Vejnavn, Husnummer,
                  Postnummer, Bynavn, Ejendomsnummer, Matrikelnummer,
                  Geometri.STX AS X, Geometri.STY AS Y,
                  TrappeskakterAntal, TrappeskakterAreal, LyskasserAntal, LyskasserAreal
           FROM dbo.DispensationsansoegningSpildevandTrappeskaktLyskasser
           WHERE AddedBy = 'ditmerflex'
           ORDER BY Id DESC
           FOR JSON PATH
       ) AS raekker;

2. From the repo root:

       tests\\.venv\\Scripts\\python.exe tests\\sammenlign_med_dawa.py

   It asks for the Datafordeler API key (the password of the OO credential DatafordelerAPI);
   it is neither shown nor saved. The output holds no keys.

First run, 2026-10-23, on the last 60 rows: address, postnr, by, matrikelnummer and point
(< 1 m) as DAWA on all 60. DAWA had given "0" as ejendomsnummer on all of them; BBR has the
real number, and the script now counts that apart instead of calling it a difference. To
check BBR's number against DAWA's real ones, change the WHERE to
AddedBy = 'ditmerflex' AND Ejendomsnummer <> '0'.
(Datafordeleren refuses GraphQL introspection, so field names can't be looked up from here.)
"""

import getpass
import json
import math
import pathlib
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from robot_framework import adresse  # noqa: E402  pylint: disable=wrong-import-position

RAEKKER = REPO / "tests" / "dawa_raekker.json"
DATAFORDELER_URL = "https://graphql.datafordeler.dk/flexible/v3"
ADRESSEVAELGER_URL = "https://adressevaelger.dk"
SHARED_ADRESSEVAELGER_TOKEN = "adressevaelger123"


def meters_between(lon1, lat1, lon2, lat2) -> float:
    dx = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2)) * 111320
    dy = (lat2 - lat1) * 110540
    return math.hypot(dx, dy)


def same(old, new) -> bool:
    return str(old or "").strip().casefold() == str(new or "").strip().casefold()


def dawa_had_none(value) -> bool:
    """DAWA's newer rows hold "0" as ejendomsnummer - that is no number to compare with."""
    return str(value or "").strip() in ("", "0")


def main():
    if not RAEKKER.exists():
        sys.exit(f"Mangler {RAEKKER} - se forklaringen øverst i denne fil.")
    rows = json.loads(RAEKKER.read_text(encoding="utf-8-sig").strip())

    api_key = getpass.getpass("Datafordeler API-nøgle (password på OO-credential DatafordelerAPI): ").strip()
    token = getpass.getpass(f"Adressevælger-token (Enter = det fælles {SHARED_ADRESSEVAELGER_TOKEN!r}): ").strip()
    services = {
        "adressevaelger_url": ADRESSEVAELGER_URL,
        "adressevaelger_token": token or SHARED_ADRESSEVAELGER_TOKEN,
        "datafordeler_url": DATAFORDELER_URL,
        "datafordeler_api_key": api_key,
    }

    diffs = Counter()
    identical = 0
    print(f"\n{len(rows)} rækker fra DAWA-tiden:\n")
    for row in rows:
        label = f"Id {row['Id']} {row['Vejnavn']} {row['Husnummer']}"
        try:
            found = adresse.resolve(row["Vejnavn"], row["Husnummer"], services)
        except adresse.UkendtAdresse as error:
            diffs["ikke fundet"] += 1
            print(f"  FEJL {label}: {error}")
            continue
        except RuntimeError as error:
            diffs["opslag fejlede"] += 1
            print(f"  FEJL {label}: {error}")
            if diffs["opslag fejlede"] >= 3 and identical == 0:
                print("  (stopper - samme fejl hver gang)")
                break
            continue

        notes = []
        if dawa_had_none(row.get("Ejendomsnummer")):
            diffs["ejendomsnummer: DAWA havde 0, BBR har et"] += bool(found["Ejendomsnummer"])
            row = {**row, "Ejendomsnummer": found["Ejendomsnummer"]}
        for column in ("Vejnavn", "Husnummer", "Postnummer", "Bynavn", "Matrikelnummer", "Ejendomsnummer"):
            if not same(row.get(column), found[column]):
                diffs[column] += 1
                notes.append(f"{column} {row.get(column)!r} -> {found[column]!r}")
        if row.get("X") is not None and row.get("Y") is not None:
            distance = meters_between(row["X"], row["Y"], found["Laengdegrad"], found["Breddegrad"])
            if distance > 1:
                diffs["punkt > 1 m"] += 1
                notes.append(f"punkt flyttet {distance:.1f} m")
        if notes:
            print(f"  DIFF {label}: " + "; ".join(notes))
        else:
            identical += 1
            print(f"  ok   {label}")

    print(f"\n{identical} af {len(rows)} som DAWA (adresse, postnr, by, matrikel-, ejendomsnummer, punkt < 1 m).")
    for what, count in diffs.most_common():
        print(f"  {what}: {count}")


if __name__ == "__main__":
    main()
