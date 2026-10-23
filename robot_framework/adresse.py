"""Address lookups that replace DAWA (api.dataforsyningen.dk, 410 Gone since 2026-10-01).

The Azure API this robot replaces (mtm-apiservices, github.com/mtm-aarhus/mtm-endpoints)
asked DAWA for "vejnavn + husnr in Aarhus Kommune" and stored what it found. Now:
- the husnummer comes from Klimadatastyrelsens Adressevælger,
- matrikelnummer and the ESR ejendomsnummer from Datafordeleren (GraphQL),
- the WGS84 point (SRID 4326, as DAWA gave it) from the husnummer's UTM32 adgangspunkt.

Kept free of OpenOrchestrator so tests/ can run it without a robot setup.
"""

import math
import re
from datetime import datetime, timezone

import requests

AARHUS_KOMMUNEKODE = "0751"
SAFE_ID_RE = re.compile(r"^[0-9A-Za-z-]+$")
TIMEOUT = 60

# Adressevælger's search is a prefix search ("Skolevej 1" also gives 10A, 11, ...), and it
# refuses more than 200 hits; that is far more than one street number in one kommune needs.
SOEG_MAKSIMUM = 200


class UkendtAdresse(Exception):
    """The address can't be placed in Aarhus Kommune - a business error, not worth a retry."""


def _request(method: str, url: str, service: str, secret: str, **kwargs) -> requests.Response:
    """requests puts the whole URL (token/apiKey included) in its error messages, which would
    end up in the OO log - so raise our own, with the secret blanked out."""
    try:
        response = requests.request(method, url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as error:
        raise RuntimeError(f"{service} svarer ikke: {type(error).__name__}") from None
    if not response.ok:
        text = response.text[:500].replace(secret, "***") if secret else response.text[:500]
        raise RuntimeError(f"{service} HTTP {response.status_code}: {text}")
    return response


def adressevaelger_get(services: dict, path: str, params: dict) -> dict:
    response = _request(
        "GET",
        f"{services['adressevaelger_url']}{path}",
        "Adressevælger",
        services["adressevaelger_token"],
        params={**params, "token": services["adressevaelger_token"]},
    )
    return response.json() or {}


def _vejnavn_key(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def _husnummer_key(value) -> str:
    return "".join(str(value or "").split()).casefold()


def find_husnummer(vejnavn: str, husnummer: str, services: dict) -> dict:
    """The DAR husnummer for an exact street name + house number in Aarhus Kommune.

    DAWA matched vejnavn and husnr exactly, so do the same with the search hits; "10 a"
    still finds 10A.
    """
    vej_key, nr_key = _vejnavn_key(vejnavn), _husnummer_key(husnummer)
    if not vej_key or not nr_key:
        raise UkendtAdresse(f"Vejnavn og husnummer skal begge være udfyldt (fik {vejnavn!r}, {husnummer!r})")

    tekst = f"{' '.join(str(vejnavn).split())} {''.join(str(husnummer).split())}"
    search = adressevaelger_get(
        services,
        "/husnumre/soeg",
        {"tekst": tekst, "kommunekode": AARHUS_KOMMUNEKODE, "maksimum": SOEG_MAKSIMUM},
    )
    hits = {
        f["id"]: f
        for f in search.get("fund") or []
        if f.get("type") == "husnummer"
        and f.get("id")
        and _vejnavn_key(f.get("vejnavn")) == vej_key
        and _husnummer_key(f.get("husnummer")) == nr_key
    }
    if not hits:
        raise UkendtAdresse(f"Adresse findes ikke i Aarhus Kommune: {tekst}")
    if len(hits) > 1:
        titler = "; ".join(sorted(h.get("titel") or h["id"] for h in hits.values()))
        raise UkendtAdresse(f"{tekst} findes flere steder i Aarhus Kommune: {titler}")

    husnummer_id = next(iter(hits))
    if not SAFE_ID_RE.match(husnummer_id):
        raise RuntimeError(f"Uventet husnummer-id fra Adressevælger: {husnummer_id!r}")
    detail = adressevaelger_get(services, f"/husnumre/{husnummer_id}", {}).get("husnummer")
    if not detail:
        raise RuntimeError(f"Adressevælger gav intet husnummer for id {husnummer_id}")
    if (detail.get("navngivenvejkommunedel") or {}).get("kommune") != AARHUS_KOMMUNEKODE:
        raise UkendtAdresse(f"Adresse findes ikke i Aarhus Kommune: {tekst}")
    return detail


def datafordeler_query(services: dict, query: str) -> dict:
    response = _request(
        "POST",
        services["datafordeler_url"],
        "Datafordeler GraphQL",
        services["datafordeler_api_key"],
        params={"apiKey": services["datafordeler_api_key"]},
        json={"query": query},
        headers={"Content-Type": "application/json"},
    )
    payload = response.json() or {}
    if payload.get("errors"):
        raise RuntimeError(f"Datafordeler GraphQL-fejl: {payload['errors'][0].get('message')}")
    return payload.get("data") or {}


def datafordeler_nodes(services: dict, entity: str, where: str, fields: str) -> list[dict]:
    """One bitemporal lookup "as of now"; Datafordeleren wants both times on every query."""
    ts = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
    query = f"""
    query {{
      {entity}(
        registreringstid: "{ts}"
        virkningstid: "{ts}"
        first: 10
        where: {{ {where} }}
      ) {{
        nodes {{ {fields} }}
      }}
    }}
    """
    return (datafordeler_query(services, query).get(entity) or {}).get("nodes") or []


def _by_id(local_id: str, entity: str) -> str:
    if not SAFE_ID_RE.match(local_id):
        raise RuntimeError(f"Uventet {entity}-id: {local_id!r}")
    return f'id_lokalId: {{ eq: "{local_id}" }}'


def lookup_jordstykke(husnummer_id: str, services: dict) -> dict:
    """husnummer -> jordstykke -> matrikelnummer + BFE (replaces DAWA's adgangsadresse.matrikelnr).

    Same chain as Python-XFlowToFilarkivCase, which matched DAWA on 49 of 50 addresses.
    """
    husnumre = datafordeler_nodes(services, "DAR_Husnummer", _by_id(husnummer_id, "DAR_Husnummer"),
                                  "id_lokalId jordstykke")
    jordstykke_id = next((str(h["jordstykke"]) for h in husnumre if h.get("jordstykke")), None)
    if not jordstykke_id:
        return {"matrikelnummer": None, "bfenummer": None}

    jordstykker = datafordeler_nodes(services, "MAT_Jordstykke", _by_id(jordstykke_id, "MAT_Jordstykke"),
                                     "id_lokalId matrikelnummer samletFastEjendomLokalId status")
    if not jordstykker:
        return {"matrikelnummer": None, "bfenummer": None}
    jordstykke = next(
        (j for j in jordstykker if str(j.get("status") or "").lower() == "gældende"),
        jordstykker[0],
    )

    bfenummer = None
    sfe_id = str(jordstykke.get("samletFastEjendomLokalId") or "")
    if sfe_id:
        sfe = datafordeler_nodes(services, "MAT_SamletFastEjendom", _by_id(sfe_id, "MAT_SamletFastEjendom"),
                                 "id_lokalId BFEnummer")
        bfenummer = next((s["BFEnummer"] for s in sfe if s.get("BFEnummer") is not None), None)

    return {"matrikelnummer": jordstykke.get("matrikelnummer"), "bfenummer": bfenummer}


def lookup_esr_ejendomsnummer(bfenummer, services: dict) -> str | None:
    """The kommunale (ESR) ejendomsnummer DAWA gave as adgangsadresse.esrejendomsnr.

    BBR keeps it on the ejendomsrelation for the property's BFE number. In its last years DAWA
    only gave "0" here (all of the last 60 rows in the table, checked 2026-10-23), so rows from
    this robot hold the real number where the old API's newer rows hold "0".
    """
    try:
        bfe = int(bfenummer)
    except (TypeError, ValueError):
        return None
    if bfe <= 0:
        return None

    relationer = datafordeler_nodes(services, "BBR_Ejendomsrelation", f"bfeNummer: {{ eq: {bfe} }}",
                                    "bfeNummer ejendomsnummer status")
    numre = [str(r["ejendomsnummer"]).strip() for r in relationer if r.get("ejendomsnummer") not in (None, "")]
    return numre[0] if numre else None


# ETRS89 / UTM zone 32N (EPSG:25832) -> geographic (EPSG:4326, which ETRS89 equals at map scale).
# Krüger's series for the inverse transverse Mercator; tests/ checks it against pyproj.
_A = 6378137.0
_F = 1 / 298.257222101  # GRS80
_N = _F / (2 - _F)
_RECTIFYING_RADIUS = _A / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64)
_BETA = (
    _N / 2 - 2 * _N**2 / 3 + 37 * _N**3 / 96 - _N**4 / 360,
    _N**2 / 48 + _N**3 / 15 - 437 * _N**4 / 1440,
    17 * _N**3 / 480 - 37 * _N**4 / 840,
    4397 * _N**4 / 161280,
)
_DELTA = (
    2 * _N - 2 * _N**2 / 3 - 2 * _N**3 + 116 * _N**4 / 45,
    7 * _N**2 / 3 - 8 * _N**3 / 5 - 227 * _N**4 / 45,
    56 * _N**3 / 15 - 136 * _N**4 / 35,
    4279 * _N**4 / 630,
)
_K0 = 0.9996
_FALSE_EASTING = 500000.0
_CENTRAL_MERIDIAN = math.radians(9.0)


def utm32_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """(easting, northing) in EPSG:25832 -> (longitude, latitude) in degrees."""
    xi = y / (_K0 * _RECTIFYING_RADIUS)
    eta = (x - _FALSE_EASTING) / (_K0 * _RECTIFYING_RADIUS)
    xi_p = xi - sum(b * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, b in enumerate(_BETA, 1))
    eta_p = eta - sum(b * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, b in enumerate(_BETA, 1))
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    lat = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_DELTA, 1))
    lon = _CENTRAL_MERIDIAN + math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return math.degrees(lon), math.degrees(lat)


def resolve(vejnavn: str, husnummer: str, services: dict) -> dict:
    """Everything the old API stored about the address, under the table's column names."""
    found = find_husnummer(vejnavn, husnummer, services)

    punkt = (found.get("adgangspunkt") or {}).get("koordinater") or {}
    if punkt.get("x") is None or punkt.get("y") is None:
        raise RuntimeError(f"Husnummer {found.get('id_lokalid')} har intet adgangspunkt")
    lon, lat = utm32_to_wgs84(float(punkt["x"]), float(punkt["y"]))

    jordstykke = lookup_jordstykke(str(found.get("id_lokalid") or ""), services)
    postnummer = found.get("postnummer") or {}
    return {
        "HusnummerId": found.get("id_lokalid"),
        "Vejnavn": found.get("vejnavn"),
        "Husnummer": found.get("husnummertekst"),
        "Postnummer": postnummer.get("postnr"),
        "Bynavn": postnummer.get("navn"),
        "Matrikelnummer": jordstykke["matrikelnummer"],
        "Ejendomsnummer": lookup_esr_ejendomsnummer(jordstykke["bfenummer"], services),
        "Laengdegrad": lon,
        "Breddegrad": lat,
    }
