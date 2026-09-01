from django.test import SimpleTestCase

from gst_tally.tally.version_transport import format_tally_version, parse_tally_version, select_transport


class VersionParsingTests(SimpleTestCase):
    def test_parses_major_minor_from_common_forms(self):
        for value, expected in (("6.6", (6, 6)), ("7.0", (7, 0)), ("7.1", (7, 1)),
                                 ("7.2", (7, 2)), ("8.0", (8, 0)), ("10.0", (10, 0)),
                                 ("7", (7, 0)), ("Release 7.1.2", (7, 1)), (7.1, (7, 1))):
            with self.subTest(value=value):
                self.assertEqual(parse_tally_version(value), expected)

    def test_blank_or_unparseable_returns_none(self):
        for value in (None, "", "   ", "unknown"):
            with self.subTest(value=value):
                self.assertIsNone(parse_tally_version(value))

    def test_format_round_trips(self):
        self.assertEqual(format_tally_version(parse_tally_version("7.1")), "7.1")
        self.assertEqual(format_tally_version(None), "")


class SemanticVersionComparisonTests(SimpleTestCase):
    def test_ten_point_zero_is_not_lexically_less_than_seven_point_one(self):
        # A naive string comparison ("10.0" < "7.1") gets this wrong.
        self.assertGreater(parse_tally_version("10.0"), parse_tally_version("7.1"))


class TransportRouterAcceptanceTests(SimpleTestCase):
    def test_version_acceptance_matrix(self):
        # PART 27 acceptance table, verbatim.
        expected = {"6.6": "XML", "7.0": "XML", "7.1": "JSON",
                    "7.2": "JSON", "8.0": "JSON", "10.0": "JSON"}
        for version, transport in expected.items():
            with self.subTest(version=version):
                self.assertEqual(select_transport(parse_tally_version(version)), transport)

    def test_unknown_version_never_guesses_a_transport(self):
        self.assertIsNone(select_transport(None))
