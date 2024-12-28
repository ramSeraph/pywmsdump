import json

from unittest import TestCase
from pathlib import Path

from wmsdump.kml_helper import kml_extract_features
from wmsdump.errors import LayerMissingException, KMLUnsupportedException


class TestKMLParsing(TestCase):
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
        feats = kml_extract_features(xml_txt)
        #with open('out.jsonl', 'w') as f:
        #    for feat in feats:
        #        f.write(json.dumps(feat))
        #        f.write('\n')

        self.assertEqual(feats, expected_feats)

    def test_kml_no_features(self):
        self.match_output('kml_no_features.xml', 'kml_no_features.geojsonl')

    def test_kml_one_multigeometry(self):
        self.match_output('kml_one_multigeometry.xml', 'kml_one_multigeometry.geojsonl')

    def test_layer_missing(self):
        xml_txt = self.load_file('layer_missing.xml')
        with self.assertRaises(LayerMissingException):
            kml_extract_features(xml_txt)

    def test_kml_not_supported_1(self):
        xml_txt = self.load_file('kml_no_support_1.xml')
        with self.assertRaises(KMLUnsupportedException):
            kml_extract_features(xml_txt)

    def test_kml_not_supported_2(self):
        xml_txt = self.load_file('kml_no_support_2.xml')
        with self.assertRaises(KMLUnsupportedException):
            kml_extract_features(xml_txt)
