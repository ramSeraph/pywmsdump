from pathlib import Path
from unittest import TestCase

from wmsdump.errors import LayerMissingException, handle_error_xml


class TestErrorParsing(TestCase):
    """Tests for error XML parsing from WMS/WFS operation responses."""

    def load_file(self, fname):
        script_dir = Path(__file__).parent
        file = script_dir / 'samples' / fname
        return file.read_text()

    def test_wfs_unknown_layer(self):
        """Error from GetFeature request with unknown layer."""
        xml_txt = self.load_file('wfs_unknown_layer.xml')

        with self.assertRaises(LayerMissingException):
            handle_error_xml(xml_txt)

    def test_wms_getfeatinfo_unknown_layer(self):
        """Error from GetFeatureInfo request with unknown layer."""
        xml_txt = self.load_file('wms_getfeatinfo_unknown_layer.xml')

        with self.assertRaises(LayerMissingException):
            handle_error_xml(xml_txt)
