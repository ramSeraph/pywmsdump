from unittest import TestCase
from wmsdump import truncate_geometry

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

class TestPrecision(TestCase):
    def setUp(self):
        self.point_inp = { 'type': 'Point', 'coordinates': p1 }
        self.point_outp_3 = { 'type': 'Point', 'coordinates': t_p1 }

        self.multipoint_inp = { 'type': 'MultiPoint', 'coordinates': [p2, p3] }
        self.multipoint_outp_3 = { 'type': 'MultiPoint', 'coordinates': [t_p2, t_p3] }

        self.linestring_inp = { 'type': 'LineString', 'coordinates': [p2, p3, p4] }
        self.linestring_outp_3 = { 'type': 'LineString', 'coordinates': [t_p2, t_p3, t_p4] }

        self.multilinestring_inp = { 'type': 'MultiLineString', 'coordinates': [[p2, p3, p4], [p4, p5]] }

        self.multilinestring_outp_3 = { 'type': 'MultiLineString', 'coordinates': [[t_p2, t_p3, t_p4], [t_p4, t_p5]] }


        self.polygon_inp = { 'type': 'Polygon', 'coordinates': [[p2, p3, p4, p2], [p1, p2, p3, p1]] }
        self.polygon_outp_3 = { 'type': 'Polygon', 'coordinates': [[t_p2, t_p3, t_p4, t_p2], [t_p1, t_p2, t_p3, t_p1]] }

        self.multipolygon_inp = { 'type': 'MultiPolygon', 'coordinates': [[[p2, p3, p4, p2], [p1, p2, p3, p1]],
                                                                          [[p3, p4, p2, p3]]]}
        self.multipolygon_outp_3 = { 'type': 'MultiPolygon', 'coordinates': [[[t_p2, t_p3, t_p4, t_p2], [t_p1, t_p2, t_p3, t_p1]],
                                                                             [[t_p3, t_p4, t_p2, t_p3]]]}

        self.multigeom_inp = {
            'type': 'GeometryCollection',
            'geometries': [
                {'type': 'Point', 'coordinates': p1},
                {'type': 'LineString', 'coordinates': [p2, p3]},
                {'type': 'MultiPoint', 'coordinates': [p4, p5]}, 
            ]
        }
        self.multigeom_outp_3 = {
            'type': 'GeometryCollection',
            'geometries': [
                {'type': 'Point', 'coordinates': t_p1},
                {'type': 'LineString', 'coordinates': [t_p2, t_p3]},
                {'type': 'MultiPoint', 'coordinates': [t_p4, t_p5]},
            ]
        }
        self.maxDiff = None

    def get_inp(self, inp):
        new_inp = {}
        new_inp.update(inp)
        return new_inp

    def test_none(self):
        inp = None
        truncate_geometry(inp, 3)
        self.assertEqual(inp, None)

    def test_minus_one(self):
        inp = self.get_inp(self.point_inp)
        truncate_geometry(inp, -1)
        self.assertDictEqual(inp, self.point_inp)

    def test_no_trunc(self):
        inp = self.get_inp(self.point_inp)
        truncate_geometry(inp, 8)
        self.assertDictEqual(inp, self.point_inp)

    def test_point(self):
        inp = self.get_inp(self.point_inp)
        truncate_geometry(inp, 3)
        self.assertDictEqual(inp, self.point_outp_3)

    def test_linestring(self):
        inp = self.get_inp(self.linestring_inp)
        truncate_geometry(inp, 3)
        self.assertDictEqual(inp, self.linestring_outp_3)

    def test_polygon(self):
        inp = self.get_inp(self.polygon_inp)
        truncate_geometry(inp, 3)
        self.assertDictEqual(inp, self.polygon_outp_3)

    def test_multipoint(self):
        inp = self.get_inp(self.multipoint_inp)
        truncate_geometry(inp, 3)
        self.assertDictEqual(inp, self.multipoint_outp_3)

    def test_multilinestring(self):
        inp = self.get_inp(self.multilinestring_inp)
        truncate_geometry(inp, 3)
        self.assertDictEqual(inp, self.multilinestring_outp_3)

    def test_multipolygon(self):
        inp = self.get_inp(self.multipolygon_inp)
        truncate_geometry(inp, 3)
        self.assertDictEqual(inp, self.multipolygon_outp_3)


    def test_multigeom(self):
        inp = self.get_inp(self.multigeom_inp)
        truncate_geometry(inp, 3)
        self.assertDictEqual(inp, self.multigeom_outp_3)
