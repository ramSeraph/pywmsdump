import json
import logging

from pathlib import Path

from graphlib import TopologicalSorter

import numpy as np
import shapely
from shapely.geometry import shape, mapping
from shapely import unary_union

from geoindex_rs import rtree as rt

logger = logging.getLogger(__name__)

def fix_if_required(p):
    if p.is_valid:
        return p
    p = p.buffer(0)
    if not p.is_valid:
        logger.error('found invalid polygon')
    return p


def get_polygons(feat):
    s = shape(feat['geometry'])
    if s.geom_type not in ['Polygon', 'MultiPolygon']:
        return None
    s = fix_if_required(s)
    if s.geom_type == 'Polygon':
        return [s]
    ps = list(s.geoms)
    return [ fix_if_required(p) for p in ps ]

class FileReader:
    def __init__(self, fname,
                 maintain_map=True,
                 use_offset=False):
        self.file = fname
        self.count = 0
        self.maintain_map = maintain_map
        self.use_offset = use_offset
        self.idx_map = {}
        self.offset_map = {}
        self.idx_arr = []
        self.tree = None

    def iter_features(self, f):
        while True:
            line = f.readline()
            if line == '':
                break
            feat = json.loads(line)
            self.count += 1
            yield feat, f.tell()

    def __iter__(self):
        with open(self.file, 'r') as f:
            for feat, _ in self.iter_features(f):
                yield feat

    def populate_spatial_index(self):
        logger.info('creating index')
        xmin = []
        ymin = []
        xmax = []
        ymax = []
        with open(self.file, 'r') as f:
            if self.maintain_map and self.use_offset:
                self.offset_map[self.count] = 0
            for feat, pos in self.iter_features(f):
                if self.count % BSIZE == 0:
                    logger.info(f'{self.count} records collected to add to index')
                ps = get_polygons(feat)
                if ps is None:
                    continue
                for pi,p in enumerate(ps):
                    if self.maintain_map:
                        idx = (self.count - 1, pi)
                        self.idx_arr.append(idx)
                        if not self.use_offset:
                            self.idx_map[idx] = p
                        else:
                            self.offset_map[self.count] = pos
                    b = p.bounds
                    xmin.append(b[0])
                    ymin.append(b[1])
                    xmax.append(b[2])
                    ymax.append(b[3])
        
        xmin = np.array(xmin, dtype=np.float32)
        ymin = np.array(ymin, dtype=np.float32)
        xmax = np.array(xmax, dtype=np.float32)
        ymax = np.array(ymax, dtype=np.float32)
        
        builder = rt.RTreeBuilder(num_items=len(xmin))
        builder.add(xmin, ymin, xmax, ymax)
        tree = builder.finish()
        self.tree = tree

    def get_contained_polygons(self, s):
        if self.tree is None:
            raise Exception('index not built')

        b = s.bounds
        results = rt.search(self.tree, *b)
        results = results.to_pylist()
        out = []
        for i in results:
            mi, mpi = self.idx_arr[i]
            mp = self.get(mi, mpi)
            if shapely.contains_properly(s, mp):
                out.append((mi, mpi, mp))
        return out

    def get(self, n, pi):
        if n >= self.count:
            return None

        if not self.maintain_map:
            return None

        if not self.use_offset:
            return self.idx_map[(n,pi)]

        with open(self.file, 'r') as f:
            f.seek(self.offset_map[n])
            line = f.readline()
            feat = json.loads(line)

            ps = get_polygons(feat)
            if ps is None:
                return None

            if pi >= len(ps):
                return None

            return ps[pi]

BSIZE = 10000

def deserialize_enclosing_map(json_str):
    data = json.loads(json_str)

    enclosing_map = {}
    for k, v in data.items():
        parts = k.split('_')
        enclosing_map[(int(parts[0]), int(parts[1]))] = [ tuple(m) for m in v ]

    return enclosing_map

def serialize_enclosing_map(enclosing_map):
    out = {}
    for k, v in enclosing_map.items():
        out[f'{k[0]}_{k[1]}'] = v
    return json.dumps(out)

def get_poly_map_from_file(inp_fname, involved_poly_idxs):
    pmap = {}
    r = FileReader(inp_fname, maintain_map=False)
    logger.info('Collecting polygons involved in corrections')
    for feat in r:
        ps = get_polygons(feat)
        if ps is None:
            continue

        i = r.count - 1
        if (i + 1) % BSIZE == 0:
            logger.info(f'read {i + 1} features')

        for pi, p in enumerate(ps):
            if (i, pi) not in involved_poly_idxs:
                continue
            if not p.is_valid:
                props = feat['properties']
                logger.error(f'invalid geometry even after buffer for {props}')
            pmap[(i,pi)] = p

    return pmap


def get_enclosing_map_file(inp_fname):
    return Path(f'{inp_fname}.enclosing_map.json')

def get_enclosing_map(inp_fname, use_offset):
    enclosing_map_file = get_enclosing_map_file(inp_fname)
    if enclosing_map_file.exists():
        enclosing_map = deserialize_enclosing_map(enclosing_map_file.read_text())
        involved_poly_idxs = set()

        for k, v in enclosing_map.items():
            involved_poly_idxs.add(k)
            for m in v:
                involved_poly_idxs.add(m)
        pmap = get_poly_map_from_file(inp_fname, involved_poly_idxs)
        return enclosing_map, pmap

    r1 = FileReader(inp_fname, use_offset=use_offset)
    r1.populate_spatial_index()

    pmap = {}
    enclosing_map = {}
    r2 = FileReader(inp_fname, maintain_map=False)
    logger.info('Looking for polygons enclosing other polygons')
    for feat in r2:
        i = r2.count - 1
        if (i + 1) % BSIZE == 0:
            logger.info(f'done handling {i + 1} features checking for enclosings')

        ps = get_polygons(feat)
        if ps is None:
            continue

        for pi, p in enumerate(ps):
            p = fix_if_required(p)
            contained_items = r1.get_contained_polygons(p)
            for item in contained_items:
                mi, mpi, mp = item
                h = (i,pi)
                if h not in pmap:
                    pmap[h] = p
                if h not in enclosing_map:
                    enclosing_map[h] = []
                enclosing_map[h].append((mi,mpi))
                pmap[(mi,mpi)] = mp

    enclosing_map_file.write_text(serialize_enclosing_map(enclosing_map))
    return enclosing_map, pmap

def prune_enclosing_map(enclosing_map):
    reverse_enclosing_map = {}
    for k, v in enclosing_map.items():
        for c in v:
            if c not in reverse_enclosing_map:
                reverse_enclosing_map[c] = set()
            reverse_enclosing_map[c].add(k)
    for k in enclosing_map.keys():
        enclosing_map[k] = set(enclosing_map[k])

    new_enclosing_map = {}
    for k, v in enclosing_map.items():
        new_enclosing_map[k] = []
        for c in v:
            parents = reverse_enclosing_map.get(c, set())
            parent_already_there = False
            for p in parents:
                if p in v:
                    parent_already_there = True
                    break
            if not parent_already_there:
                new_enclosing_map[k].append(c)

    return new_enclosing_map

def get_replacements(enclosing_map, pmap):
    logger.info('pruning enclosing map to only keep closest enclosing polygons in hierarchy')
    enclosing_map = prune_enclosing_map(enclosing_map)

    logger.info('topologically sorting enclosing map to determine order of hole punching')
    topo_sorter = TopologicalSorter()
    for k,v in enclosing_map.items():
        for c in v:
            topo_sorter.add(c,k)

    ordering = {}
    count = 0
    for n in topo_sorter.static_order():
        ordering[n] = count
        count += 1

    sorted_keys = sorted(enclosing_map.keys(), key=lambda x: ordering[x])

    replacements = {}
    count = 0
    logger.info('creating replacement polygons with holes')
    for k in sorted_keys:
        count += 1
        if count % BSIZE == 0:
            logger.info(f'created {count} replacement polygons')
        holes = enclosing_map[k]
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
        r3 = FileReader(inp_fname, maintain_map=False)
        count = 0
        for feat in r3:
            ps = get_polygons(feat)
            if ps is not None:
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

    enclosing_map, pmap = get_enclosing_map(inp_fname, use_offset)
    logger.info(f'{len(enclosing_map)} polygons affected')

    replacements = get_replacements(enclosing_map, pmap)

    write_fixed_file(outp_fname, inp_fname, replacements)

    if keep_map_file:
        return

    enclosing_map_file = get_enclosing_map_file(inp_fname)
    if enclosing_map_file.exists():
        enclosing_map_file.unlink()
