import json
import logging

from pathlib import Path

from graphlib import TopologicalSorter

import shapely
from shapely.geometry import shape, mapping
from shapely import prepare, unary_union
from rtree import index

logger = logging.getLogger(__name__)

def fix_if_required(p):
    if p.is_valid:
        return p
    p = p.buffer(0)
    if not p.is_valid:
        logger.error('found invalid polygon')
    return p


class FileReader:
    def __init__(self, fname,
                 use_offset=True):
        self.file = fname
        self.count = 0
        self.use_offset = use_offset
        self.idx_map = {}

    def __iter__(self):
        with open(self.file, 'r') as f:
            if self.use_offset:
                self.idx_map[self.count] = 0
            while True:
                line = f.readline()
                self.count += 1
                if self.use_offset:
                    self.idx_map[self.count] = f.tell()
                if line == '':
                    break
                feat = json.loads(line)
                yield feat

    def get(self, n, pi):
        if n >= self.count:
            return None
        if not self.use_offset:
            return None

        with open(self.file, 'r') as f:
            f.seek(self.idx_map[n])
            line = f.readline()
            feat = json.loads(line)
            s = shape(feat['geometry'])
            if s.geom_type not in ['Polygon', 'MultiPolygon']:
                return None

            ps = []
            if s.geom_type == 'Polygon':
                ps.append(s)
            else:
                ps.extend(list(s.geoms))

            if pi >= len(ps):
                return None

            mp = ps[pi]
            mp = fix_if_required(mp)
            if not mp.is_valid:
                props = feat['properties']
                logger.info(f'invalid geometry even after buffer for {n=}, {pi=}, {props=}')
 
            return mp

BSIZE = 10000

def idx_gen(reader, full_poly_map):
    logger.info('adding features to index')
    for feat in reader:
        s = shape(feat['geometry'])
        if s.geom_type not in ['Polygon', 'MultiPolygon']:
            continue
        ps = []
        if s.geom_type == 'Polygon':
            ps.append(s)
        else:
            ps.extend(list(s.geoms))

        i = reader.count - 1
        tot_count = 0
        if (i + 1) % BSIZE == 0:
            logger.info(f'added {i + 1} features')
        for pi, p in enumerate(ps):
            p = fix_if_required(p)
            if not p.is_valid:
                props = feat['properties']
                logger.error(f'invalid geometry even after buffer for {props}')
            if not reader.use_offset:
                full_poly_map[(i,pi)] = p
            yield (tot_count, p.bounds, (i,pi))
            tot_count += 1

def deserialize_hole_map(json_str):
    data = json.loads(json_str)
    hole_map = {}
    for k, v in data.items():
        parts = k.split('_')
        hole_map[(int(parts[0]), int(parts[1]))] = [ tuple(m) for m in v ]

    return hole_map

def serialize_hole_map(hole_map):
    out = {}
    for k, v in hole_map.items():
        out[f'{k[0]}_{k[1]}'] = v
    return json.dumps(out)

def get_poly_map_from_file(inp_fname, all_polys):
    pmap = {}
    r = FileReader(inp_fname, use_offset=False)
    logger.info('Collecting polygons involved in corrections')
    for feat in r:
        s = shape(feat['geometry'])
        if s.geom_type not in ['Polygon', 'MultiPolygon']:
            continue
        ps = []
        if s.geom_type == 'Polygon':
            ps.append(s)
        else:
            ps.extend(list(s.geoms))

        i = r.count - 1
        if (i + 1) % BSIZE == 0:
            logger.info(f'read {i + 1} features')

        for pi, p in enumerate(ps):
            if (i, pi) not in all_polys:
                continue
            p = fix_if_required(p)
            if not p.is_valid:
                props = feat['properties']
                logger.error(f'invalid geometry even after buffer for {props}')
            pmap[(i,pi)] = p

    return pmap


def get_hole_map_file(inp_fname):
    return Path(f'{inp_fname}.hole_map.json')

def get_hole_map(inp_fname, use_offset):
    hole_map_file = get_hole_map_file(inp_fname)
    if hole_map_file.exists():
        hole_map = deserialize_hole_map(hole_map_file.read_text())
        all_polys = set()
        for k, v in hole_map.items():
            all_polys.add(k)
            for m in v:
                all_polys.add(m)
        pmap = get_poly_map_from_file(inp_fname, all_polys)
        return hole_map, pmap

    full_poly_map = {}
    r1 = FileReader(inp_fname, use_offset=use_offset)
    idx = index.Index(idx_gen(r1, full_poly_map))

    pmap = {}
    hole_map = {}
    r2 = FileReader(inp_fname, use_offset=False)
    logger.info('Looking for holes')
    for feat in r2:
        i = r2.count - 1
        if (i + 1) % BSIZE == 0:
            logger.info(f'done handling {i + 1} features checking for holes')

        s = shape(feat['geometry'])
        if s.geom_type not in ['Polygon', 'MultiPolygon']:
            continue
        ps = []
        if s.geom_type == 'Polygon':
            ps.append(s)
        else:
            ps.extend(list(s.geoms))

        for pi, p in enumerate(ps):
            items = list(idx.intersection(s.bounds, objects='raw'))
            for item in items:
                mi, mpi = item
                if use_offset:
                    mp = r1.get(mi, mpi)
                else:
                    mp = full_poly_map[(mi,mpi)]

                if shapely.contains_properly(p, mp):
                    h = (i,pi)
                    if h not in pmap:
                        if not p.is_valid:
                            p = p.buffer(0)
                        pmap[h] = p
                    if h not in hole_map:
                        hole_map[h] = []
                    hole_map[h].append((mi,mpi))
                    pmap[(mi,mpi)] = mp
    hole_map_file.write_text(serialize_hole_map(hole_map))
    return hole_map, pmap

def prune_hole_map(hole_map):
    reverse_hole_map = {}
    for k, v in hole_map.items():
        for c in v:
            if c not in reverse_hole_map:
                reverse_hole_map[c] = []
            reverse_hole_map[c].append(k)

    new_hole_map = {}
    for k, v in hole_map.items():
        new_hole_map[k] = []
        for c in v:
            parents = reverse_hole_map.get(c, [])
            parent_already_there = False
            for p in parents:
                if p in v:
                    parent_already_there = True
                    break
            if not parent_already_there:
                new_hole_map[k].append(c)

    return new_hole_map

def get_replacements(hole_map, pmap):
    logger.info('processing hole map')
    hole_map = prune_hole_map(hole_map)

    topo_sorter = TopologicalSorter()
    for k,v in hole_map.items():
        for c in v:
            topo_sorter.add(c,k)

    ordering = {}
    count = 0
    for n in topo_sorter.static_order():
        ordering[n] = count
        count += 1

    sorted_keys = sorted(hole_map.keys(), key=lambda x: ordering[x])

    replacements = {}
    count = 0
    logger.info('creating replacement polygons')
    for k in sorted_keys:
        count += 1
        if count % BSIZE == 0:
            logger.info(f'created {count} replacement polygons')
        holes = hole_map[k]
        p = pmap[k]
        hps = [ pmap[h] for h in holes ]
        hp_union = unary_union(hps)
        hp_union = fix_if_required(hp_union)
        p = p - hp_union
        p = fix_if_required(p)
        if p.geom_type not in ['Polygon', 'MultiPolygon']:
            logger.error(f'Unexpected shape type {p.geom_type}')
        replacements[k] = p

    return replacements

def write_fixed_file(outp_fname, inp_fname, replacements):
    logger.info(f'writing features to {outp_fname}')
    with open(outp_fname, 'w') as f:
        r3 = FileReader(inp_fname, use_offset=True)
        count = 0
        for feat in r3:
            s = shape(feat['geometry'])
            if s.geom_type in [ 'Polygon', 'MultiPolygon' ]:
                ps = []
                if s.geom_type == 'Polygon':
                    ps.append(s)
                else:
                    ps.extend(list(s.geoms))

                has_changes = False
                out = []
                for pi in range(len(ps)):
                    if (count,pi) in replacements:
                        out.append(replacements[(count,pi)])
                        has_changes = True
                    else:
                        out.append(ps[pi])

                if has_changes:
                    new_g = unary_union(out)
                    new_g = fix_if_required(new_g)
                    feat['geometry'] = mapping(new_g)

            count += 1
            if count % BSIZE == 0:
                logger.info(f'wrote {count} features')
            f.write(json.dumps(feat))
            f.write('\n')


def punch_holes(inp_fname, outp_fname, use_offset=True, keep_map_file=False):

    hole_map, pmap = get_hole_map(inp_fname, use_offset)
    logger.info(f'{len(hole_map)} polygons affected')

    replacements = get_replacements(hole_map, pmap)

    write_fixed_file(outp_fname, inp_fname, replacements)

    hole_map_file = get_hole_map_file(inp_fname)

    if keep_map_file:
        return
    if hole_map_file.exists():
        hole_map_file.unlink()
