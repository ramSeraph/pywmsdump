
from bs4 import BeautifulSoup

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


