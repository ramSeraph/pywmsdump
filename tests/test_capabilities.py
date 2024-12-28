import json

from unittest import TestCase
from pathlib import Path

from wmsdump.capabilities import parse_capabilities
from wmsdump.errors import WFSUnsupportedException

class TestGeorssParsing(TestCase):
    def setUp(self):
        pass

    def load_file(self, fname):
        script_dir = Path(__file__).parent
        file = script_dir / 'samples' / fname
        return file.read_text()

    def load_json_file(self, fname):
        txt = self.load_file(fname)
        return json.loads(txt)

    def test_wms_capabilities_bug(self):
        expected = self.load_json_file('wms_capabilities.json')
        xml_txt = self.load_file('wms_capabilities.xml')

        layer_list = []
        service_info = {}
        parse_capabilities('WMS', xml_txt, layer_list, service_info)

        self.assertEqual(layer_list, expected['layer_list'])
        self.assertEqual(service_info, expected['service_info'])

    def test_wms_capabilities_sax_cleanup_bug(self):
        expected = self.load_json_file('wms_capabilities_sax_cleanup_bug.json')
        xml_txt = self.load_file('wms_capabilities_sax_cleanup_bug.xml')

        layer_list = []
        service_info = {}
        parse_capabilities('WMS', xml_txt, layer_list, service_info)

        self.assertEqual(layer_list, expected['layer_list'])
        self.assertEqual(service_info, expected['service_info'])

    def test_wms_capabilities_incomplete(self):
        expected = self.load_json_file('wms_capabilities_incomplete.json')
        xml_txt = self.load_file('wms_capabilities_incomplete.xml')

        layer_list = []
        service_info = {}
        with self.assertRaises(Exception):
            parse_capabilities('WMS', xml_txt, layer_list, service_info)

        self.assertEqual(layer_list, expected['layer_list'])
        self.assertEqual(service_info, expected['service_info'])

    def test_wfs_capabilities(self):
        expected = self.load_json_file('wfs_capabilities.json')
        xml_txt = self.load_file('wfs_capabilities.xml')

        layer_list = []
        service_info = {}
        parse_capabilities('WFS', xml_txt, layer_list, service_info)

        self.assertEqual(layer_list, expected['layer_list'])
        self.assertEqual(service_info, expected['service_info'])

    def test_wfs_disabled_ows_namespace(self):
        xml_txt = self.load_file('wfs_disabled_ows_namespace.xml')

        layer_list = []
        service_info = {}
        with self.assertRaises(WFSUnsupportedException):
            parse_capabilities('WFS', xml_txt, layer_list, service_info)

        self.assertEqual(layer_list, [])
        self.assertEqual(service_info, {})
