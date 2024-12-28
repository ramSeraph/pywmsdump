import json

from unittest import TestCase
from pathlib import Path

from wmsdump.georss_helper import extract_features

class TestPrecision(TestCase):
    def setUp(self):
        pass

    def load_file(self, fname):
        script_dir = Path(__file__).parent
        file = script_dir / 'samples' / fname
        return file.read_text()

    def load_jsonl_file(self, fname):
        txt = self.load_file(fname)
        lines = txt.split('\n')
        lines = [ l for l in lines if l.strip() != '' ]
        return [ json.loads(l) for l in lines ]

    def match_output(self, inp_fname, outp_fname):
        xml_txt = self.load_file(inp_fname)
        expected_feats = self.load_jsonl_file(outp_fname)

        feats = extract_features(xml_txt)

        self.assertEqual(feats, expected_feats)

    def test_point(self):
        self.match_output('point.xml', 'point.geojsonl')

    def test_linestring(self):
        self.match_output('linestring.xml', 'linestring.geojsonl')

    def test_polygon(self):
        self.match_output('polygon.xml', 'polygon.geojsonl')

    def test_point_single(self):
        self.match_output('point_single.xml', 'point_single.geojsonl')

    def test_empty_feed(self):
        self.match_output('empty_feed.xml', 'empty_feed.geojsonl')
