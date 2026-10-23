# Python-SpildevandsDispensation

Puts approved applications for *dispensation fra kravet om separering af spildevand for
trappeskakt og lyskasser* on the map in SpatialMap (theme *Dispensation – kælderskakt/lyskasse*
under Vand og Natur → Spildevand).

It replaces API on the Azure App Service
`mtm-apiservices` ([mtm-endpoints](https://github.com/mtm-aarhus/mtm-endpoints)), which stopped
working when DAWA closed on 2026-10-01. Every call after that got *"Adresse findes ikke i
Aarhus Kommune"*.

```
Xflow (dataaflevering "DispensationSpildevand til Spatial Suite")
  → OOAPI  POST   (APIKEY_XFLOWSpildevand)
  → OpenOrchestrator queue "SpildevandsDispensation"
  → this robot:  Adressevælger (husnummer, point)  +  Datafordeleren (matrikel-, ejendomsnummer)
  → Azure SQL mtm-sql-01 / ApiDataStorage / dbo.DispensationsansoegningSpildevandTrappeskaktLyskasser
  → SpatialMap (unchanged)
```

The robot writes the same row the old API wrote, so SpatialMap needs no change: address as
found, ESR ejendomsnummer, matrikelnummer and a WGS84 point (SRID 4326). Only `AddedBy`
differs: `xflow-oo` instead of `ditmerflex`, so you can tell the two apart.

What becomes of a queue element:

| Case | Result |
|---|---|
| Address found | Row inserted, element DONE |
| Same application already in the table (same ExternalID, address, date and values) | Skipped, element DONE, logged with the existing Id |
| Street + number not in Aarhus Kommune, or in more than one place | **Business error**: element FAILED, error mail, no retry |
| Missing or unreadable `DatoAnsoegning` / bad JSON | Business error, as above |
| Adressevælger, Datafordeleren or SQL down | Retried once (`QUEUE_ATTEMPTS`), then FAILED |

Address errors used to show up in Xflow's *Dataafleverings fejl*. Now Xflow always gets a 201
from OOAPI, and errors show up as FAILED elements on the queue, plus the error mail.

## OpenOrchestrator

- **Queue trigger** on `SpildevandsDispensation`, process = this repository.
- **Credentials:**
  - `Adressevælger` and `DatafordelerAPI` (already exist, shared with Python-XFlowToFilarkivCase).
  - `SpildevandsSQLUser`: the SQL user below.
- **Constants:**
  - `SpildevandsSQLURL` = the SQL server.
  - `Error Email`.

The SQL user lives in ApiDataStorage itself (a contained user, no server login). Run this in
ApiDataStorage, not in master:

```sql
CREATE USER [SpildevandsRobot] WITH PASSWORD = '<long random password>';
GRANT SELECT, INSERT ON dbo.DispensationsansoegningSpildevandTrappeskaktLyskasser TO [SpildevandsRobot];
```

The database name (`ApiDataStorage`) is in `robot_framework/config.py`.

## OOAPI

One line in OOAPI's `web.config`, then recycle the app pool (no code change):

```xml
<environmentVariable name="OO_APIKEY_XFLOW" value="<at least 32 random characters>" />
```

## Xflow

In the dataaflevering *DispensationSpildevand til Spatial Suite*:

- **URL:** `https://oomtm.aarhuskommune.dk/oo/queue`, method Post.
- **Autentifikation:** none (the old Basic login is not needed any more).
- **Headers:** `X-API-Key` = the value of `OO_APIKEY_XFLOW`.
- **Body:** an object, not a list, because OOAPI wants an object:

```json
{
  "queue_name": "SpildevandsDispensation",
  "reference": "@@blanketId",
  "created_by": "Xflow",
  "data": {
    "Vejnavn": "@@vejnavn",
    "Husnummer": "@@husnummer",
    "TrappeskakterAntal": "@@trappeskakterAntal",
    "TrappeskakterAreal": "@@trappeskakterAreal",
    "LyskasserAntal": "@@lyskasserAntal",
    "LyskasserAreal": "@@lyskasserAreal",
    "ExternalID": "@@blanketId",
    "DatoAnsoegning": "@@datoAnsoegning"
  }
}
```

The robot also accepts the old list shape inside `data`, and field names in any case.

## Before going live: compare with DAWA

The unit tests fake Datafordeleren. `tests/sammenlign_med_dawa.py` is the real check: it looks
up the last 60 rows DAWA wrote and prints what differs (matrikel-, ejendomsnummer, the point
to within 1 m). How to run it is described at the top of the file.

## Putting an element on the queue by hand

For example the applications that failed while DAWA was gone. **Every element writes a real
row that SpatialMap shows.**

```powershell
$key = Read-Host "OO_APIKEY_XFLOW"
$body = @{
  queue_name = "SpildevandsDispensation"; reference = "<blanketId>"; created_by = "manual"
  data = @{ Vejnavn = "<vej>"; Husnummer = "<nr>"; TrappeskakterAntal = "<…>"; TrappeskakterAreal = "<…>"
            LyskasserAntal = "<…>"; LyskasserAreal = "<…>"; ExternalID = "<blanketId>"; DatoAnsoegning = "<yyyy-mm-dd>" }
} | ConvertTo-Json -Depth 3
Invoke-RestMethod -Method Post -Uri "https://oomtm.aarhuskommune.dk/oo/queue" -Headers @{ "X-API-Key" = $key } `
  -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

## Tests

```
py -3 tests\opsaet_testmiljoe.py
tests\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

They cover `adresse.py`, `ansoegning.py`, `database.py` and `process.py`, with OpenOrchestrator,
the web services and the database faked. `adresse.utm32_to_wgs84` is checked against pyproj,
which is only needed in the tests.

## Robot framework

Built on the OpenOrchestrator queue framework (see `Robot-Queue-Framework.svg`). One change:
a `BusinessError` is never retried.
