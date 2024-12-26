import re

import xmltodict

from bs4 import BeautifulSoup

from .errors import handle_error

def get_points(vals):
    points = []
    curr_p = []
    for c in vals.split(' '):
        if len(curr_p) == 2:
            curr_p.reverse()
            points.append(curr_p)
            curr_p = []
        curr_p.append(float(c))
    if len(curr_p) == 2:
        curr_p.reverse()
        points.append(curr_p)
        curr_p = []
    return points

def get_props(content):
    # TODO: check that the data is indeed in html
    soup = BeautifulSoup(content, 'lxml')
    lis = soup.find_all('li')
    props = {}
    for li in lis:
        txt = li.text.strip()
        parts = txt.split(':', 1)
        if len(parts) != 2:
            return { 'unparsed': content }
        props[parts[0]] = parts[1].strip()
    return props


def extract_feature(entry):
    title = entry.get('title', None)
    content = entry.get('content', {}).get('#text', '')
    props = get_props(content)

    where = entry['georss:where']
    if 'georss:polygon' in where:
        points = get_points(where['georss:polygon'])
        geom = { 'type': 'Polygon', 'coordinates': [points] }
    elif 'georss:line' in where:
        points = get_points(where['georss:line'])
        geom = { 'type': 'LineString', 'coordinates': points }
    elif 'georss:point' in where:
        points = get_points(where['georss:point'])
        geom = { 'type': 'Point', 'coordinates': points[0] }
    else:
        raise Exception(f'unexpected content in {where}')

    return { 'type': 'Feature', 'id': title, 'geometry': geom, 'properties': props }


def combine_geoms(geoms):
    g_types = set()

    for g in geoms:
        g_types.add(g['type'])
    if len(g_types) > 1:
        return { 'type': 'GeometryCollection', 'geometries': geoms }

    g_type = g_types.pop()
    out_type = None
    if g_type not in ['Point', 'LineString', 'Polygon']:
        raise Exception(f'Unexpected geom type: {g_type}')
    out_type = 'Multi' + g_type

    coords = [ g['coordinates'] for g in geoms ]
    return { 'type': out_type, 'coordinates': coords }



def combine_features(feats):
    by_fid = {}
    for feat in feats:
        fid = feat.get('id', None)
        if fid not in by_fid:
            by_fid[fid] = []
        by_fid[fid].append(feat)

    new_feats = []
    for fid, f_feats in by_fid.items():
        if fid is None:
            new_feats.extend(f_feats)
            continue

        if len(f_feats) == 1:
            new_feats.extend(f_feats)
            continue

        non_empty_props = [ f['properties'] for f in f_feats if f['properties'] != {} ]
        if len(non_empty_props) > 1:
            new_feats.extend(f_feats)
            continue

        props = non_empty_props[0] if len(non_empty_props) > 0 else {}
        new_geom = combine_geoms([ f['geometry'] for f in f_feats ])
        new_feat = { 'type': 'Feature', 'id': fid, 'properties': props, 'geometry': new_geom }
        new_feats.append(new_feat)

    return new_feats


def extract_features(xml_text):
    # deal with some xml/unicode messups
    # TODO: add a test case for this?
    xml_text = re.sub(r'&#([a-zA-Z0-9]+);?', r'[#\1;]', xml_text)

    data = xmltodict.parse(xml_text)

    handle_error(data)

    if 'feed' not in data:
       raise Exception('no feed in data')

    feed = data['feed']
    if 'entry' not in feed:
        return []

    entries = feed['entry']
    if type(entries) is not list:
        entries = [ entries ]

    feats = []
    for entry in entries:
        feat = extract_feature(entry)
        feats.append(feat)

    # sometimes multipolygons show up as multiple seperate polygon features with the same id
    # combine them into one feature where possible
    # the assumption here is that.. they show up in the same batch and that
    # the properties dict for the split pieces is empty
    # TODO: how are polygons with inner rings represented?
    feats = combine_features(feats)

    return feats
