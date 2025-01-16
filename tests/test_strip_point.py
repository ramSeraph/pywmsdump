from unittest import TestCase
from wmsdump.kml_helper import tranform_geo_collection

p1 = [77.89824947, 21.91597942]
t_p1 = [77.898, 21.916] 

p2 = [77.898237, 21.9153487]
t_p2 = [77.898, 21.915]

p3 = [77.8982494, 21.9151794]
t_p3 = [77.898, 21.915]

p4 = [77.8981754, 21.9150513]
t_p4 = [77.898, 21.915]

p5 = [77.8980558, 21.9151142]
t_p5 = [77.898, 21.915]

class TestMultiGeomStrip(TestCase):
    def setUp(self):

        self.maxDiff = None

    def check_expected(self, inp, expected):
        outp = tranform_geo_collection(inp, True)
        self.assertDictEqual(outp, expected)

    def check_no_changes(self, inp):
        self.check_expected(inp, inp)

    def test_none(self):
        outp = tranform_geo_collection(None, True)
        self.assertEqual(outp, None)

    def test_point(self):
        inp = { 'type': 'Point', 'coordinates': p1 }
        self.check_no_changes(inp)

    def test_linestring(self):
        inp = { 'type': 'LineString', 'coordinates': [p2, p3, p4] }
        self.check_no_changes(inp)

    def test_polygon(self):
        inp = { 'type': 'Polygon', 'coordinates': [[p2, p3, p4, p2], [p1, p2, p3, p1]] }
        self.check_no_changes(inp)

    def test_multipoint(self):
        inp = { 'type': 'MultiPoint', 'coordinates': [p2, p3] }
        self.check_no_changes(inp)

    def test_multilinestring(self):
        inp = { 'type': 'MultiLineString', 'coordinates': [[p2, p3, p4], [p4, p5]] }
        self.check_no_changes(inp)

    def test_multipolygon(self):
        inp = { 'type': 'MultiPolygon', 'coordinates': [[[p2, p3, p4, p2], [p1, p2, p3, p1]],
                                                        [[p3, p4, p2, p3]]]}
        self.check_no_changes(inp)

    def test_multigeom_all_points(self):

        inp = {
            'type': 'GeometryCollection',
            'geometries': [
                {'type': 'Point', 'coordinates': p1},
                {'type': 'Point', 'coordinates': p2},
                {'type': 'Point', 'coordinates': p3}, 
            ]
        }
        expected_outp = {
            'type': 'MultiPoint',
            'coordinates': [p1,p2,p3]
        }

        self.check_expected(inp, expected_outp)

    def test_multigeom_all_linestrings(self):

        inp = {
            'type': 'GeometryCollection',
            'geometries': [
                {'type': 'LineString', 'coordinates': [p1, p2]},
                {'type': 'LineString', 'coordinates': [p3, p4]},
                {'type': 'LineString', 'coordinates': [p4, p5]}, 
            ]
        }
        expected_outp = {
            'type': 'MultiLineString',
            'coordinates': [[p1,p2],[p3,p4],[p4,p5]]
        }

        self.check_expected(inp, expected_outp)

    def test_multigeom_one_point(self):
        inp = {
            'type': 'GeometryCollection',
            'geometries': [
                {'type': 'Point', 'coordinates': p1},
            ]
        }
        expected_outp = {
            'type': 'Point',
            'coordinates': p1
        }
        self.check_expected(inp, expected_outp)

    def test_multigeom_one_point_other_diff(self):
        inp = {
            'type': 'GeometryCollection',
            'geometries': [
                {'type': 'Point', 'coordinates': p1},
                {'type': 'LineString', 'coordinates': [p3, p4]},
                {'type': 'Polygon', 'coordinates': [[p2, p3, p4, p2], [p1, p2, p3, p1]]}
            ]
        }
        self.check_no_changes(inp)

    def test_multigeom_one_point_other_same_line(self):
        inp = {
            'type': 'GeometryCollection',
            'geometries': [
                {'type': 'Point', 'coordinates': p1},
                {'type': 'LineString', 'coordinates': [p3, p4]},
                {'type': 'LineString', 'coordinates': [p2, p3]},
            ]
        }
        expected_outp = {
            'type': 'MultiLineString', 'coordinates': [[p3, p4],[p2, p3]]
        }
        self.check_expected(inp, expected_outp)
 
    def test_multigeom_one_point_other_same_polygon(self):
        inp = {
            'type': 'GeometryCollection',
            'geometries': [
                {'type': 'Point', 'coordinates': p1},
                {'type': 'Polygon', 'coordinates': [[p3, p4, p5, p3]]},
                {'type': 'Polygon', 'coordinates': [[p1, p2, p3, p1],[p2, p3, p4, p2]]},
            ]
        }
        expected_outp = {
            'type': 'MultiPolygon',
            'coordinates': [[[p3, p4, p5, p3]],[[p1, p2, p3, p1],[p2, p3, p4, p2]]]
        }
        self.check_expected(inp, expected_outp)

    def test_multigeom_one_point_other_single_line(self):
        inp = {
            'type': 'GeometryCollection',
            'geometries': [
                {'type': 'Point', 'coordinates': p1},
                {'type': 'LineString', 'coordinates': [p3, p4]},
            ]
        }
        expected_outp = {
            'type': 'LineString', 'coordinates': [p3, p4]
        }
        self.check_expected(inp, expected_outp)
 
    def test_multigeom_one_point_other_same_polygon(self):
        inp = {
            'type': 'GeometryCollection',
            'geometries': [
                {'type': 'Point', 'coordinates': p1},
                {'type': 'Polygon', 'coordinates': [[p3, p4, p5, p3]]},
            ]
        }
        expected_outp = {
            'type': 'Polygon',
            'coordinates': [[p3, p4, p5, p3]]
        }
        self.check_expected(inp, expected_outp)
