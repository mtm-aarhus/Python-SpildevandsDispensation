"""robot_framework/adresse.py: finding the address, matrikel- and ejendomsnummer, the point."""

import traceback
import unittest
from unittest import mock

import requests
from pyproj import Transformer

from robot_framework import adresse
from tests.fakes import AGRIVEJ_11, SERVICES, FakeResponse, FakeServices, hit, husnummer_detail


def patched(fake):
    return mock.patch.object(adresse.requests, "request", side_effect=fake)


class Utm32ToWgs84Test(unittest.TestCase):
    def test_matches_pyproj_all_over_aarhus_kommune(self):
        transformer = Transformer.from_crs(25832, 4326, always_xy=True)
        for x in range(555000, 600001, 5000):
            for y in range(6200000, 6250001, 5000):
                with self.subTest(x=x, y=y):
                    lon, lat = adresse.utm32_to_wgs84(x, y)
                    ref_lon, ref_lat = transformer.transform(x, y)
                    # 1e-9 degrees is about 0.1 mm
                    self.assertAlmostEqual(lon, ref_lon, delta=1e-9)
                    self.assertAlmostEqual(lat, ref_lat, delta=1e-9)

    def test_matches_pyproj_at_the_edges_of_denmark(self):
        transformer = Transformer.from_crs(25832, 4326, always_xy=True)
        for x, y in [(441000, 6049000), (893000, 6125000), (590000, 6402000), (870000, 6130000)]:
            with self.subTest(x=x, y=y):
                lon, lat = adresse.utm32_to_wgs84(x, y)
                ref_lon, ref_lat = transformer.transform(x, y)
                self.assertAlmostEqual(lon, ref_lon, delta=1e-8)
                self.assertAlmostEqual(lat, ref_lat, delta=1e-8)

    def test_agrivej_11_lands_in_risskov(self):
        lon, lat = adresse.utm32_to_wgs84(576226.37, 6228684.5)
        self.assertTrue(10.2 < lon < 10.3, lon)
        self.assertTrue(56.1 < lat < 56.25, lat)


class FindHusnummerTest(unittest.TestCase):
    def setUp(self):
        self.egaa_1 = "0a3f5096-ddf7-32b8-e044-0003ba298018"
        self.egaa_10a = "0a3f5096-de19-32b8-e044-0003ba298018"
        self.fake = FakeServices(
            soeg=[
                hit(self.egaa_1, "Skolevej", "1", "Skolevej 1, 8250 Egå"),
                hit(self.egaa_10a, "Skolevej", "10A", "Skolevej 10A, 8250 Egå"),
                hit("0a3f5096-de1f-32b8-e044-0003ba298018", "Skolevej", "11", "Skolevej 11, 8250 Egå"),
            ],
            husnumre={
                self.egaa_1: husnummer_detail(self.egaa_1, "Skolevej", "1", "8250", "Egå"),
                self.egaa_10a: husnummer_detail(self.egaa_10a, "Skolevej", "10A", "8250", "Egå"),
            },
        )

    def test_takes_the_exact_number_among_the_prefix_hits(self):
        with patched(self.fake):
            found = adresse.find_husnummer("Skolevej", "1", SERVICES)
        self.assertEqual(found["id_lokalid"], self.egaa_1)

    def test_case_and_spaces_do_not_matter(self):
        with patched(self.fake):
            self.assertEqual(adresse.find_husnummer("  skolevej ", "10 a", SERVICES)["id_lokalid"], self.egaa_10a)

    def test_searches_aarhus_only_with_room_for_every_hit(self):
        with patched(self.fake):
            adresse.find_husnummer("Skolevej", "10 a", SERVICES)
        params = self.fake.calls[0]["params"]
        self.assertEqual(params["tekst"], "Skolevej 10a")
        self.assertEqual(params["kommunekode"], "0751")
        self.assertEqual(params["maksimum"], 200)
        self.assertEqual(params["token"], SERVICES["adressevaelger_token"])

    def test_no_exact_number_is_unknown(self):
        with patched(self.fake), self.assertRaisesRegex(adresse.UkendtAdresse, "findes ikke i Aarhus Kommune"):
            adresse.find_husnummer("Skolevej", "2", SERVICES)

    def test_a_similar_street_is_not_taken(self):
        # Adressevælger's search is fuzzy; "Agervej 11" must not become "Agrivej 11"
        fake = FakeServices(soeg=[hit(AGRIVEJ_11, "Agrivej", "11", "Agrivej 11, 8240 Risskov")])
        with patched(fake), self.assertRaises(adresse.UkendtAdresse):
            adresse.find_husnummer("Agervej", "11", SERVICES)

    def test_same_address_in_two_towns_is_not_guessed(self):
        self.fake.soeg.append(hit("ffff0000-0000-0000-0000-000000000001", "Skolevej", "1", "Skolevej 1, 8330 Beder"))
        with patched(self.fake), self.assertRaisesRegex(adresse.UkendtAdresse, "flere steder.*Egå.*Beder"):
            adresse.find_husnummer("Skolevej", "1", SERVICES)

    def test_empty_input_is_unknown_without_asking(self):
        with patched(self.fake), self.assertRaises(adresse.UkendtAdresse):
            adresse.find_husnummer("Skolevej", "  ", SERVICES)
        self.assertEqual(self.fake.calls, [])

    def test_outside_aarhus_is_unknown(self):
        self.fake.husnumre[self.egaa_1] = husnummer_detail(self.egaa_1, "Skolevej", "1", kommune="0740")
        with patched(self.fake), self.assertRaisesRegex(adresse.UkendtAdresse, "findes ikke i Aarhus Kommune"):
            adresse.find_husnummer("Skolevej", "1", SERVICES)


class SecretsStayOutOfTheLogTest(unittest.TestCase):
    def test_connection_error_does_not_carry_the_token(self):
        url = f"https://adressevaelger.test/husnumre/soeg?tekst=x&token={SERVICES['adressevaelger_token']}"
        error = requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {url}")
        with mock.patch.object(adresse.requests, "request", side_effect=error):
            try:
                adresse.find_husnummer("Agrivej", "11", SERVICES)
            except RuntimeError:
                logged = traceback.format_exc()
            else:
                self.fail("no error")
        self.assertNotIn(SERVICES["adressevaelger_token"], logged)
        self.assertIn("Adressevælger svarer ikke: ConnectionError", logged)

    def test_http_error_text_is_redacted(self):
        body = f"bad key {SERVICES['datafordeler_api_key']}"
        with mock.patch.object(adresse.requests, "request", return_value=FakeResponse(None, 401, body)):
            with self.assertRaises(RuntimeError) as caught:
                adresse.datafordeler_query(SERVICES, "query { x }")
        self.assertNotIn(SERVICES["datafordeler_api_key"], str(caught.exception))
        self.assertIn("HTTP 401", str(caught.exception))

    def test_graphql_errors_raise(self):
        answer = FakeResponse({"errors": [{"message": "The field `ejendomsnummer` does not exist"}]})
        with mock.patch.object(adresse.requests, "request", return_value=answer):
            with self.assertRaisesRegex(RuntimeError, "ejendomsnummer"):
                adresse.datafordeler_query(SERVICES, "query { x }")


class ResolveTest(unittest.TestCase):
    def test_everything_the_old_api_stored(self):
        fake = FakeServices()
        with patched(fake):
            found = adresse.resolve("Agrivej", "11", SERVICES)

        lon, lat = adresse.utm32_to_wgs84(576226.37, 6228684.5)
        self.assertEqual(found, {
            "HusnummerId": AGRIVEJ_11,
            "Vejnavn": "Agrivej",
            "Husnummer": "11",
            "Postnummer": "8240",
            "Bynavn": "Risskov",
            "Matrikelnummer": "12ab",
            "Ejendomsnummer": "54321",
            "Laengdegrad": lon,
            "Breddegrad": lat,
        })

    def test_graphql_lookups_are_as_of_now_and_by_the_right_keys(self):
        fake = FakeServices()
        with patched(fake):
            adresse.resolve("Agrivej", "11", SERVICES)

        for entity in ("DAR_Husnummer", "MAT_Jordstykke", "MAT_SamletFastEjendom", "BBR_Ejendomsrelation"):
            with self.subTest(entity=entity):
                (query,) = fake.queries(entity)
                self.assertIn("registreringstid:", query)
                self.assertIn("virkningstid:", query)
        self.assertIn(f'id_lokalId: {{ eq: "{AGRIVEJ_11}" }}', fake.queries("DAR_Husnummer")[0])
        self.assertIn('id_lokalId: { eq: "1234567" }', fake.queries("MAT_Jordstykke")[0])
        self.assertIn("bfeNummer: { eq: 100200 }", fake.queries("BBR_Ejendomsrelation")[0])
        post = next(c for c in fake.calls if c["method"] == "POST")
        self.assertEqual(post["params"], {"apiKey": SERVICES["datafordeler_api_key"]})

    def test_no_jordstykke_leaves_matrikel_and_ejendomsnummer_empty(self):
        fake = FakeServices()
        fake.graphql["DAR_Husnummer"] = [{"id_lokalId": AGRIVEJ_11, "jordstykke": None}]
        with patched(fake):
            found = adresse.resolve("Agrivej", "11", SERVICES)
        self.assertIsNone(found["Matrikelnummer"])
        self.assertIsNone(found["Ejendomsnummer"])
        self.assertEqual(fake.queries("BBR_Ejendomsrelation"), [])

    def test_prefers_the_current_jordstykke(self):
        fake = FakeServices()
        fake.graphql["MAT_Jordstykke"] = [
            {"id_lokalId": "1234567", "matrikelnummer": "12aa", "status": "Historisk", "samletFastEjendomLokalId": "1"},
            {"id_lokalId": "1234567", "matrikelnummer": "12ab", "status": "Gældende", "samletFastEjendomLokalId": "7654321"},
        ]
        with patched(fake):
            self.assertEqual(adresse.resolve("Agrivej", "11", SERVICES)["Matrikelnummer"], "12ab")

    def test_no_adgangspunkt_is_an_error(self):
        fake = FakeServices()
        fake.husnumre[AGRIVEJ_11]["husnummer"]["adgangspunkt"] = None
        with patched(fake), self.assertRaisesRegex(RuntimeError, "adgangspunkt"):
            adresse.resolve("Agrivej", "11", SERVICES)

    def test_odd_ids_never_reach_a_graphql_query(self):
        fake = FakeServices()
        fake.graphql["DAR_Husnummer"] = [{"id_lokalId": AGRIVEJ_11, "jordstykke": '1" } }) { x'}]
        with patched(fake), self.assertRaisesRegex(RuntimeError, "Uventet MAT_Jordstykke-id"):
            adresse.resolve("Agrivej", "11", SERVICES)


if __name__ == "__main__":
    unittest.main()
