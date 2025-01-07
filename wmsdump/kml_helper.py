import io
import re

import kml2geojson

from .errors import handle_error_xml
from .props_helper import get_props_from_html

def convert_kml_props(feat, keep_original):
    if 'properties' not in feat:
        return 

    props = feat['properties']
    if 'description' not in props:
        return

    desc = props['description']
    del props['description']

    new_props = get_props_from_html(desc)

    if keep_original:
        for k, v in feat['properties'].items():
            new_props[f'kml.{k}'] = v

    feat['properties'] = new_props

def merge_same_type_geoms(gtype, geoms):
    if len(geoms) == 1:
        return geoms[0]

    new_coords = []
    new_geom = { 'type': f'Multi{gtype}', 'coordinates': new_coords }
    for g in geoms:
        new_coords.append(g['coordinates'])
    return new_geom

def tranform_geo_collection(geom,
                            strip_singular_points_from_multi_geoms):
    if geom is None:
        return None

    if geom['type'] != 'GeometryCollection':
        return geom
    geoms = geom['geometries']
    tmap = {}
    for g in geoms:
        g_type = g['type']
        if g_type not in tmap:
            tmap[g_type] = []
        tmap[g_type].append(g)

    if len(tmap) == 1:
        gtype = list(tmap.keys())[0]
        if gtype not in ['LineString', 'Point', 'Polygon']:
            return geom
        return merge_same_type_geoms(gtype, tmap[gtype])

    if not strip_singular_points_from_multi_geoms:
        return geom

    points = tmap.get('Point', [])
    if len(points) != 1:
        return geom

    del tmap['Point']
    if len(tmap) != 1:
        return geom

    other_type = list(tmap.keys())[0]
    if other_type not in ['LineString', 'Polygon']:
        return geom

    return merge_same_type_geoms(other_type, tmap[other_type])


def kml_extract_features(xml_text,
                         strip_singular_points_from_multi_geoms,
                         keep_original_props):
    xml_text = re.sub(r'&#([a-zA-Z0-9]+);?', r'[#\1;]', xml_text)
    handle_error_xml(xml_text)
    fh = io.StringIO(xml_text)
    feature_collections = kml2geojson.main.convert(fh)
    data = feature_collections[0]
    feats = data['features']
    for feat in feats:
        convert_kml_props(feat, keep_original_props)
        feat['geometry'] = tranform_geo_collection(feat['geometry'],
                                                   strip_singular_points_from_multi_geoms)

    return feats

