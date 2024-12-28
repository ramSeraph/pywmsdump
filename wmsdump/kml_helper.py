import io

import kml2geojson

from .errors import handle_error_xml
from .props_helper import get_props_from_html

def convert_kml_props(feat):
    if 'properties' not in feat:
        return 

    props = feat['properties']
    if 'description' not in props:
        return

    new_props = get_props_from_html(props['description'])

    feat['properties'] = new_props


def kml_extract_features(xml_text):
    handle_error_xml(xml_text)
    fh = io.StringIO(xml_text)
    feature_collections = kml2geojson.main.convert(fh)
    data = feature_collections[0]
    feats = data['features']
    for feat in feats:
        convert_kml_props(feat)

    return feats

