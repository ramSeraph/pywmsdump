import json

from unittest import TestCase
from pathlib import Path

from wmsdump.georss_helper import georss_extract_features
from wmsdump.errors import LayerMissingException, GeoRSSUnsupportedException

class TestGeorssParsing(TestCase):
    def setUp(self):
        pass

    def load_file(self, fname):
        script_dir = Path(__file__).parent
        file = script_dir / 'samples' / fname
        return file.read_text()

    def load_jsonl_file(self, fname):
        txt = self.load_file(fname)
        lines = txt.split('\n')
        lines = [ line for line in lines if line.strip() != '' ]
        return [ json.loads(line) for line in lines ]

    def match_output(self, inp_fname, outp_fname):
        expected_feats = self.load_jsonl_file(outp_fname)

        xml_txt = self.load_file(inp_fname)
        feats = georss_extract_features(xml_txt)

        self.assertEqual(feats, expected_feats)

    def test_point(self):
        self.match_output('georss_point.xml', 'georss_point.geojsonl')

    def test_linestring(self):
        self.match_output('georss_linestring.xml', 'georss_linestring.geojsonl')

    def test_polygon(self):
        self.match_output('georss_polygon.xml', 'georss_polygon.geojsonl')

    def test_point_single(self):
        self.match_output('georss_point_single.xml', 'georss_point_single.geojsonl')

    def test_empty_feed(self):
        self.match_output('georss_empty_feed.xml', 'georss_empty_feed.geojsonl')

    def test_layer_missing(self):
        xml_txt = self.load_file('layer_missing.xml')
        with self.assertRaises(LayerMissingException):
            georss_extract_features(xml_txt)

    def test_georss_no_support(self):
        xml_txt = self.load_file('georss_no_support.xml')
        with self.assertRaises(GeoRSSUnsupportedException):
            georss_extract_features(xml_txt)
