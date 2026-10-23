"""Fake Adressevælger + Datafordeleren for the tests - answers like the real ones did when
probed on 2026-10-23 (Agrivej 11, 8240 Risskov; Skolevej in Egå)."""

import json
import re
from urllib.parse import urlparse

SERVICES = {
    "adressevaelger_url": "https://adressevaelger.test",
    "adressevaelger_token": "token-hemmelig-123",
    "datafordeler_url": "https://graphql.datafordeler.test/flexible/v3",
    "datafordeler_api_key": "noegle-hemmelig-456",
}

AGRIVEJ_11 = "0a3f5095-ddec-32b8-e044-0003ba298018"


def husnummer_detail(husnummer_id=AGRIVEJ_11, vejnavn="Agrivej", husnr="11", postnr="8240",
                     by="Risskov", kommune="0751", x=576226.37, y=6228684.5):
    return {
        "status": "ok",
        "husnummer": {
            "id_lokalid": husnummer_id,
            "husnummertekst": husnr,
            "vejnavn": vejnavn,
            "adgangspunkt": {"koordinater": {"x": x, "y": y}},
            "postnummer": {"navn": by, "postnr": postnr},
            "navngivenvejkommunedel": {"kommune": kommune, "vejkode": "0084"},
        },
    }


def hit(husnummer_id, vejnavn, husnr, titel):
    return {"type": "husnummer", "id": husnummer_id, "titel": titel, "vejnavn": vejnavn, "husnummer": husnr}


class FakeResponse:
    def __init__(self, payload=None, status=200, text=None):
        self._payload = payload
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return self._payload


class FakeServices:
    """Stands in for requests.request. `soeg` and `husnumre` are what Adressevælger knows,
    `graphql` maps an entity name to the nodes it returns."""

    def __init__(self, soeg=None, husnumre=None, graphql=None):
        self.soeg = soeg if soeg is not None else [hit(AGRIVEJ_11, "Agrivej", "11", "Agrivej 11, 8240 Risskov")]
        self.husnumre = husnumre if husnumre is not None else {AGRIVEJ_11: husnummer_detail()}
        self.graphql = graphql if graphql is not None else {
            "DAR_Husnummer": [{"id_lokalId": AGRIVEJ_11, "jordstykke": "1234567"}],
            "MAT_Jordstykke": [{"id_lokalId": "1234567", "matrikelnummer": "12ab", "status": "Gældende",
                                "samletFastEjendomLokalId": "7654321"}],
            "MAT_SamletFastEjendom": [{"id_lokalId": "7654321", "BFEnummer": 100200}],
            "BBR_Ejendomsrelation": [{"bfeNummer": 100200, "ejendomsnummer": 54321, "status": "7"}],
        }
        self.calls = []

    def __call__(self, method, url, timeout=None, params=None, json=None, headers=None):
        self.calls.append({"method": method, "url": url, "params": params or {}, "json": json})
        path = urlparse(url).path
        if method == "GET" and path == "/husnumre/soeg":
            return FakeResponse({"status": "ok", "beskrivelse": "", "fund": self.soeg})
        if method == "GET" and path.startswith("/husnumre/"):
            husnummer_id = path.rsplit("/", 1)[1]
            return FakeResponse(self.husnumre.get(husnummer_id, {"status": "ok", "husnummer": None}))
        if method == "POST" and url == SERVICES["datafordeler_url"]:
            entity = re.search(r"query\s*{\s*(\w+)\(", json["query"]).group(1)
            return FakeResponse({"data": {entity: {"nodes": self.graphql.get(entity, [])}}})
        raise AssertionError(f"Unexpected request: {method} {url}")

    def queries(self, entity):
        return [c["json"]["query"] for c in self.calls if c["json"] and f"{entity}(" in c["json"]["query"]]
