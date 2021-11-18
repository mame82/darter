# CORE: Logic to fully parse an individual snapshot, given its two blobs

from io import BytesIO
from struct import unpack
import re
from bisect import bisect

from .read import *
from .constants import *
from .clusters import make_cluster_handlers
from .data.type_data import make_type_data
from .data.base_objects import init_base_objects


class ParseError(Exception):
    def __init__(self, data_offset, message):
        self.data_offset = data_offset
        self.message = message


def parse_features(features):
    # FIXME: handle quotes correctly
    result = {}
    for token in features.split(' '):
        token = re.fullmatch(r'(no-)?"?(.+)"?', token)
        if token.group(2) in result:
            raise Exception('Duplicate features')
        result[token.group(2)] = not token.group(1)
    return result

format_cid = lambda cid: \
    kClassId[cid] if type(cid) is int and 0 <= cid < kNumPredefinedCids else repr(cid)

unob_string = lambda str: str.x['unob'] if 'unob' in str.x else str.x['value']

# FIXME: throw parseerror if:
# if instructions / rodata is needed and not present,
# if Bytecode and KernelProgramInfo appear if precompiled
# if utf-16 decoding fails
# if read methods fail

class VMObject:
    def __init__(self, s, ref, cluster, x):
        self.ref = ref
        self.x = x
        self.cluster = cluster
        self.src = []
        self.s = s
    def is_base(self):
        return type(self.ref) is int and self.ref < self.s.num_base_objects+1
    def is_own(self):
        return type(self.ref) is int and self.ref >= self.s.num_base_objects+1
    def is_cid(self, *names):
        return any(self.cluster['cid'] == kkClassId[name] for name in names)
    is_array = lambda self: self.is_cid('Array', 'ImmutableArray')
    is_string = lambda self: self.is_cid('OneByteString', 'TwoByteString')
    is_instance = lambda self: self.is_cid('Instance') or ( \
        type(self.cluster['cid']) is int and self.cluster['cid'] >= kNumPredefinedCids )
    is_baseobject = lambda self: self.cluster['cid'] == 'BaseObject'
    is_null = lambda self: self.ref == 1
    def values(self):
        if self.ref == 4:
            return []
        if self.is_cid('GrowableObjectArray'):
            assert self.x['length'].is_cid('Mint') and self.x['data'].is_array()
            length = self.x['length'].x['value']
            values = self.x['data'].values()
            assert length <= len(values)
            return values[:length]
        if self.is_array():
            return list(self.x['value'])
        raise Exception("Not an array")
    def __str__(self):
        x = self.x
        if self.is_baseobject():
            return '<base{}>{}'.format('' if self.ref in {1,9,10} else (' ' + x['type']), x['value'])
        content = format_cid(self.cluster['cid'])
        if self.is_instance():
            content = 'Instance'
        if self.is_string():
            content = repr(x['value'])
        extra = self.get_extra_fields()
        content += '' if extra is None else '({})'.format(extra)
        return '{base}{1}->{0}'.format(self.ref, content, base="<base>" if self.is_base() else "")
    def get_extra_fields(self):
        x = self.x
        resolve_mint = lambda x: x.x['value'] if x.is_cid('Mint') else x
        resolve_string = lambda x: repr(unob_string(x)) if x.is_string() else x
        if self.is_string():
            return repr(x['unob']) if 'unob' in x else None
        if self.is_cid('Mint', 'Double'):
            return x['value']
        if self.is_array():
            return len(x['value'])
        if self.is_cid('GrowableObjectArray'):
            return '{}, {}'.format(resolve_mint(x['length']), x['data'])
        if self.is_cid('UnlinkedCall'):
            return resolve_string(x['target_name'])
        if self.is_instance():
            return x['_class']
        if self.is_cid('Type'):
            th = [ x['_class'] ]
            if not x['arguments'].is_null():
                th.append(x['arguments'])
            return ', '.join(str(x) for x in th)
        if self.is_cid('Class'):
            lib = x['library']
            if lib.is_cid('Library'):
                if lib.x['url'].x['value'] == 'dart:core':
                    return resolve_string(x['name'])
                lib = resolve_string(lib.x['url'])
            return '{}, {}'.format(lib, resolve_string(x['name']))
        if self.is_cid('Function'):
            name = resolve_string(x['name'])
            if x['name'].is_string() and x['name'].x['value'] == '<anonymous closure>':
                name = 'closure'
            params = x['parameter_names']
            params = 'EXT' if params.ref == 12 else len(params.values())
            return '{}, {}'.format(name, params)
        if self.is_cid('Field'):
            v = x['value']
            descr = 'at +{}'.format(v.x['value']) if v.is_cid('Mint') else v
            t = x['type'] if x['type'].is_baseobject() else x['type'].x['_class']
            return '{}, {}, {}'.format(resolve_string(x['name']), descr, t)
        if self.is_cid('Library', 'Script'):
            return resolve_string(x['url'])
    def describe(self):
        ''' Like str(), but gives full info about its location in the code '''
        location = self.locate()
        if not location: return str(self)
        return '{}{{ {} }}'.format(self, ' '.join(str(x) for x in location))
    def locate(self):
        ''' Returns list of 'parent' objects, where the first item is the
            immediate parent, and so on. Items can also be strings describing
            position inside the parent object. List may be empty or None if unknown. '''
        x = self.x
        with_next = lambda p: [p] + (lambda x: [] if x is None else x)(p.locate())
        if self.is_cid('Code'):
            res = with_next(x['owner'])
            if x['owner'].is_cid('Class') and x['owner'].x['allocation_stub'] is self:
                res = [ '<alloc>' ] + res
            if x['owner'].is_null():
                srcs = [ x for x in self.src if x[0].ref == 'root' ]
                if len(srcs) == 1: return [ srcs[0][-1] ]
            return res
        if self.is_cid('Function'):
            if x['data'].is_cid('ClosureData'):
                return with_next(x['data'].x['parent_function'])
            return with_next(x['owner'])
        if self.is_cid('PatchClass'):
            if x['origin_class'] == x['patched_class']:
                return with_next(x['origin_class'])
        if self.is_cid('Field'):
            return with_next(x['owner'])
        if self.is_cid('Class'):
            return []
    def __repr__(self):
        return self.__str__()


class Snapshot:
    """
    This is the core snapshot parser. It can only parse one snapshot,
    that is, a 'data' blob with an optional 'instructions' blob.
    
        - If the snapshot is a VM snapshot, you should pass `vm=True`
        - Otherwise, you should provide the parsed VM snapshot as `base=<Snapshot object>`
    
    Typical usage is constructing an instance and then calling the `parse`
    method to perform the actual parsing. Most of the parsed information is
    in `refs` and `clusters`.
    """

    def __init__(self, data, instructions=None, vm=False, base=None,
        data_offset=0, instructions_offset=0, print_level=4,
        strict=True, parse_rodata=True, parse_csm=True, build_tables=True):
        """ Initialize a parser.
        
        Main arguments
        --------------

        data -- The data blob.
        instructions -- The instructions blob (if present).
        vm -- True if this is a VM snapshot; False if isolate snapshot (default).
        base -- Base snapshot, which should always be passed if vm=False. If not passed, the core base objects are used.
            IMPORTANT: The base will be poisoned, you should discard it after passing it here.

        Parsing behaviour
        -----------------

        strict -- If strict mode is enabled (default True); in strict mode, inconsistency warnings become errors.
        parse_rodata -- Enables / disables parsing of memory structures. The layout of memory structures is decided
            by the compiler, can also vary between archs, so if parsing fails you can try disabling it. This causes
            the following dictionaries:

                - CodeSourceMap, PcDescriptors and StackMap objects, if present
                - OneByteString / TwoByteString objects (for AppJIT and AppAOT snapshots)
                - instructions / active_instructions field of Code objects, if present
            
            To be empty except for an `offset` field pointing where they are located.
        parse_csm -- Enables / disabling parsing code source maps using parse_code_source_map().
            If disabled, code source maps will contain a 'data' field with the encoded bytecode, instead of 'ops'.
            This option has no effect if parse_rodata is False.
        build_tables -- Calls build_tables() at the end of the parsing, which populates some convenience data
            about the snapshot. Disable this if it fails for some reason.

        Reporting parameters
        --------------------

        data_offset -- When reporting an offset into the data blob, this value will be added to it.
        instructions_offset -- When reporting an offset into the instructions blob, this value will be added to it.
        print_level -- Maximum message level to print: -1 nothing, 0 error, 1 warning, 2 notice, 3 info (default), 4 debug
        """
        self.data = BytesIO(data)
        self.data_offset = data_offset
        self.instructions = None if instructions is None else BytesIO(instructions)
        self.instructions_offset = instructions_offset
        self.vm = vm
        self.base = base

        self.print_level = print_level
        self.show_debug = print_level >= 4
        self.strict = strict
        self.parse_rodata = parse_rodata
        self.parse_csm = parse_csm
        self.do_build_tables = build_tables
    
    def parse(self):
        ''' Parse the snapshot. '''
        self.parse_header()
        self.initialize_settings()
        self.initialize_clusters()
        self.initialize_references()
        
        self.info('Reading allocation clusters...')
        self.clusters = [ self.read_cluster() for _ in range(self.num_clusters) ]
        if self.refs['next']-1 != self.num_objects:
            self.warning('Expected {} total objects, produced {}'.format(self.num_objects, self.refs['next']-1))

        self.info('Reading fill clusters...')
        for cluster in self.clusters:
            self.read_fill_cluster(cluster)

        self.info('Reading roots...')
        root = self.refs['root'] = VMObject(self, 'root', {'handler': 'ObjectStore', 'cid': 'ObjectStore'}, {})
        if self.vm:
            self.storeref(self.data, root.x, 'symbol_table', root)
            if self.includes_code:
                root.x['_stubs'] = [ self.readref(self.data, (root, '_stubs', n)) for n in kStubCodeList ]
            self.enforce_section_marker()
        else:
            self.read_fill_cluster(root.cluster, [root])

        self.info('Snapshot parsed.')
        if self.data.tell() != self.length + 4:
            self.warning('Snapshot should end at 0x{:x} but we are at 0x{:x}'.format(self.length + 4, self.data.tell()))

        self.link_cids()
        if self.do_build_tables:
            self.build_tables()
        return self

    
    # REPORTING #

    def p(self, level, message, show_offset=True, offset=None):
        if self.print_level < level:
            return
        if show_offset:
            offset = self.data.tell() if offset is None else offset
            message = '[{:08x}]: {}'.format(self.data_offset + offset, message)
        print(message)
    
    def debug(self, message, *args, **kwargs):
        self.p(4, 'DEBUG: {}'.format(message), *args, **kwargs)

    def info(self, message, *args, **kwargs):
        self.p(3, 'INFO: {}'.format(message), *args, **kwargs)
    
    def notice(self, message, *args, **kwargs):
        self.p(2, 'NOTICE: {}'.format(message), *args, **kwargs)

    def warning(self, message, *args, **kwargs):
        if self.strict:
            self.p(1, 'WARN: An inconsistency was found; failing. Pass strict=False to treat inconsistencies as warnings and continue parsing.', *args, **kwargs)
            raise ParseError(self.data_offset + self.data.tell(), message)
        self.p(1, 'WARN: {}'.format(message), *args, **kwargs)


    # HEADER PARSING & INITIALIZATION #

    def parse_header(self):
        ''' This method parses the header of a snapshot, checks the magic and does
        some extra steps to prepare for actual parsing:
        1. If the snapshot contains a 'rodata section', then `self.rodata` is populated
           with a BytesIO for this extra data.
        2. Checks that `self.data` matches or exceeds the length in the snapshot header,
           and then truncates `self.data` to that length.
        '''
        f = self.data

        self.magic_value, self.length, self.kind = unpack('<Iqq', f.read(4+8+8))
        if self.magic_value != MAGIC_VALUE:
            self.warning('Invalid magic value: {:08x}'.format(self.magic_value))
        self.p(1, "[Header]\n  length = {}\n  kind = {} {}\n".format(self.length, self.kind, kKind[self.kind]), show_offset=False)

        self.includes_code = self.kind in {kkKind['kFullJIT'], kkKind['kFullAOT']}
        self.includes_bytecode = self.kind in {kkKind['kFull'], kkKind['kFullJIT']}

        # Check length, set rodata if needed, truncate
        data_end = 4 + self.length
        if len(f.getbuffer()) < data_end:
            self.warning('Data blob should be at least {} bytes, got {}'.format(data_end, len(f.getbuffer())))
        if self.includes_code:
            rodata_offset = ((data_end - 1) // kMaxPreferredCodeAlignment + 1) * kMaxPreferredCodeAlignment
            if len(f.getbuffer()) < rodata_offset:
                self.warning('The rodata section is not present')
            self.rodata_offset = self.data_offset + rodata_offset
            self.rodata = BytesIO(f.getbuffer()[rodata_offset:])
        elif len(f.getbuffer()) > data_end:
            self.notice('There are {} excess bytes at the end of the data blob'.format(len(f.getbuffer()) - data_end), offset=data_end)
        f.truncate(data_end)

        # Parse rest of header
        self.version = f.read(32).decode('ascii')
        if self.version != EXPECTED_VERSION:
            self.warning('Version ({}) doesn\'t match with the one this parser was made for'.format(self.version))
        self.features = parse_features(readcstr(f).decode('ascii'))
        self.p(1, "[Snapshot header]\n  version = {}\n  features = {}\n".format(repr(self.version), repr(self.features)), show_offset=False)

        # Check that header matches with base, if passed
        if self.base and (self.base.version != self.version or 
                self.base.kind != self.kind or self.base.features != self.features):
            self.warning("Snapshot header doesn't match with base snapshot!")

        # Parse counts
        self.num_base_objects, self.num_objects, self.num_clusters, self.code_order_length = (readuint(f) for _ in range(4))
        self.p(1, "  base objects: {}\n  objects: {}\n  clusters: {}\n  code order length = {}\n".format(
            self.num_base_objects, self.num_objects, self.num_clusters, self.code_order_length), show_offset=False)

    # FIXME: let user override settings, too
    def initialize_settings(self):
        ''' Detect settings / flags from parsed header '''
        # detect arch
        ARCHS = { 'x64': True, 'ia32': False, 'arm64': True, 'arm': False }
        arch = [ x for x in self.features if x.split('-')[0] in ARCHS ]
        if len(arch) != 1:
            raise ParseError("Can't determine arch in {}".format(self.features))
        self.arch = arch[0]
        self.is_64 = ARCHS[self.arch.split('-')[0]]

        # detect mode
        self.is_debug = self.features.get('debug', False)
        self.is_product = self.features.get('product', False)
        self.is_precompiled = self.kind == kkKind['kFullAOT'] and self.is_product  # FIXME

        # other settings and constants (FIXME)
        kObjectAlignment = 2 * (8 if self.is_64 else 4)
        self.kObjectAlignmentLog2 = kObjectAlignment.bit_length()-1
        self.raw_instance_size_in_words = 1

    def initialize_clusters(self):
        ''' Initialize cluster type data and handlers '''
        types, mappings = make_type_data(self.is_precompiled, self.is_product)

        def remove_fields(fields, to_remove):
            assert to_remove.issubset(set(f[1] for f in fields))
            return [f for f in fields if f[1] not in to_remove]
        
        for name, fields in types.items():
            mapping = mappings.get(name)
            if not (mapping is None or type(mapping) is bool):
                last_field = mapping[{ kkKind[n]: i for i, n in enumerate(['kFull', 'kFullJIT', 'kFullAOT']) }[self.kind]]
                idx = next(filter(lambda x: x[1][1] == last_field, enumerate(fields)))[0]
                fields = fields[:idx+1]
            
            if name == 'ClosureData' and self.kind == kkKind['kFullAOT']:
                fields = remove_fields(fields, {'context_scope'})
            if name == 'Code':
                if not self.is_precompiled and self.kind != kkKind['kFullJIT']:
                    fields = remove_fields(fields, {'deopt_info_array', 'static_calls_target_table'})
            
            types[name] = fields
        self.types = types

        # Initialize clusters
        self.handlers = make_cluster_handlers(self)


    # REFS HANDLING #

    def initialize_references(self):
        base = self.base
        exp_base_objects = self.num_base_objects

        # copy refs from base, posion them to be ours
        if base:
            base_objects = base.refs['next']-1
            self.base_clusters = list(self.base.clusters)
            # refs is a dict from int to VMObject,
            # except for 'next' key which just stores next ID to be assigned
            self.refs = { 'next': min(base_objects, exp_base_objects) + 1 }
            for i in range(1, self.refs['next']):
                ref = self.refs[i] = base.refs[i]
                ref.s = self
        else:
            init_base_objects(VMObject, self, self.includes_code)
            base_objects = self.refs['next']-1

        # fill any missing refs
        if base_objects != exp_base_objects:
            self.notice('Snapshot expected {} base objects, but the provided base has {}'.format(exp_base_objects, base_objects))
        tmp_cluster = { 'handler': 'UnknownBase', 'cid': 'unknown' }
        while self.refs['next']-1 < exp_base_objects: self.allocref(tmp_cluster, {})

    def allocref(self, cluster, x):
        if 'refs' not in cluster:
            cluster['refs'] = []
        ref = VMObject(self, self.refs['next'], cluster, x)
        self.refs[ref.ref] = ref
        self.refs['next'] += 1
        cluster['refs'].append(ref)

    def readref(self, f, source):
        r = readuint(f)
        if r not in self.refs:
            self.warning('Code referenced a non-existent ref, a broken ref is returned')
            return { 'broken': r }
        self.refs[r].src.append(source)
        return self.refs[r]

    def storeref(self, f, x, name, src):
        if not (type(src) is tuple): src = (src,)
        x[name] = self.readref(f, src + (name,))


    # MAIN PARSING LOGIC #

    def read_cluster(self):
        ''' Reads the alloc section of a new cluster '''
        cid = readcid(self.data)

        self.debug('reading cluster with cid={}'.format(format_cid(cid)))

        if cid >= kNumPredefinedCids:
            handler = 'Instance'
        elif isTypedData(cid) or isExternalTypedData(cid):
            handler = 'TypedData'
        elif isTypedDataView(cid):
            handler = 'TypedDataView'
        elif cid == kkClassId['ImmutableArray']:
            handler = 'Array'
        else:
            handler = kClassId[cid]
        cluster = { 'handler': handler, 'cid': cid }
        if not hasattr(self.handlers, handler):
            raise ParseError(self.data_offset + self.data.tell(), 'Cluster "{}" still not implemented'.format(handler))
        getattr(self.handlers, handler)(cid).alloc(self.data, cluster)
        
        if self.is_debug:
            serializers_next_ref_index = readint(f, 32)
            self.warning('next_ref doesn\'t match, expected {} but got {}'.format(serializers_next_ref_index, refs['next']))
        return cluster

    def read_fill_cluster(self, cluster, refs=None):
        ''' Reads the fill section of the passed cluster '''
        f = self.data
        cid, name = cluster['cid'], cluster['handler']
        self.debug('reading cluster with cid={}'.format(format_cid))
        handler = getattr(self.handlers, name)(cid)
        if refs is None: refs = cluster['refs']
        for ref in refs:
            if self.show_debug: self.debug('  reading ref {}'.format(ref.ref))
            assert ref.cluster == cluster
            if handler.do_read_from:
                if name in {'Closure', 'GrowableObjectArray'}:
                    ref.x['canonical'] = read1(f)
                if name == 'Code':
                    ref.x['instructions'] = self.read_instructions()
                    if not self.is_precompiled and self.kind == kkKind['kFullJIT']:
                        ref.x['active_instructions'] = self.read_instructions()
                for _, fname, _ in self.types[cluster['handler']]:
                    if self.show_debug: self.debug('    reading field {}'.format(fname))
                    self.storeref(f, ref.x, fname, ref)
            if self.show_debug: self.debug('    reading fill')
            handler.fill(f, ref.x, ref)
        self.enforce_section_marker()

    def read_instructions(self):
        ''' Reads RawInstructions object '''
        offset = readint(self.data, 32)
        if offset < 0:
            offset = -offset # FIXME: implement
            self.notice('Base instructions not implemented yet, returning empty object')
            return None
        if not self.parse_rodata:
            return { 'offset': self.instructions_offset + offset }
        f = self.instructions
        f.seek(offset)

        if self.is_64:
            tags, _, size_and_flags, unchecked_entrypoint_pc_offset = unpack('<LLLL', f.read(16))
            # 16 0xCC bytes observed on x64, looks like a sentinel or something?
            # on ARM64 it is 00... 20 D4 FFFF FFFF
            f.read(16)
        else:
            tags, size_and_flags, unchecked_entrypoint_pc_offset, _ = unpack('<LLLL', f.read(16))
        size, flags = size_and_flags & ((1 << 31) - 1), size_and_flags >> 31
        data_addr = self.instructions_offset + f.tell() # for disassembling in another program
        data = f.read(size)
        return {
            'tags': tags,
            'flags': { 'single_entry': flags & 1 },
            'unchecked_entrypoint_pc_offset': unchecked_entrypoint_pc_offset,
            'data': data,
            'data_addr': data_addr,
        }

    def enforce_section_marker(self):
        if not self.is_debug: return
        offset = self.data.tell()
        section_marker = readint(self.data, 32)
        if section_marker != kSectionMarker:
            raise ParseError(self.data_offset + offset, 'Section marker doesn\'t match')


    # CID LINKING #

    def link_cids(self):
        ''' This method builds a CID-to-VMObject table, and then manually inserts references
            from things that reference a CID (Instance, Type and predefined Class) to their original Class. '''
        # Build class table, and link predefined Class objects
        self.classes = {}
        for i in range(1, self.refs['next']):
            r = self.refs[i]
            if (r.cluster['cid'] == 'BaseObject' and r.x['type'] == 'Class') or r.is_cid('Class'):
                if r.x['cid'] in self.classes:
                    self.notice('Duplicated class with CID {}'.format(r.x['cid']))
                self.classes[r.x['cid']] = r

        # Logic to reference a CID from a ref
        broken_refs = False
        def reference_cid(ref, cid):
            if cid not in self.classes:
                nonlocal broken_refs
                broken_refs = True
                ref.x['_class'] = None
                return
            ref.x['_class'] = self.classes[cid]
            self.classes[cid].src.append((ref, '_class'))

        # Link references from Instance and Type objects
        for i in range(1, self.refs['next']):
            r = self.refs[i]
            if r.is_instance():
                reference_cid(r, r.cluster['cid'])
            if r.is_cid('Type'):
                cid = r.x['type_class_id']
                reference_cid(r, cid.x['value'] if cid.is_cid('Mint') else None)

        if broken_refs:
            self.notice('There were broken or invalid CID references; None has been set as _class')


    # CONVENIENCE API #

    getrefs = lambda self, name: self.clrefs.get(name, [])

    def build_tables(self):
        self.clrefs = {}
        for c in self.base_clusters + self.clusters:
            n = format_cid(c['cid'])
            if n not in self.clrefs: self.clrefs[n] = []
            self.clrefs[n] += c['refs']

        self.strings_refs = self.getrefs('OneByteString') + self.getrefs('TwoByteString')
        self.strings = { ref.x['value']: ref for ref in self.strings_refs }
        if len(self.strings) != len(self.strings_refs):
            self.notice('There are {} duplicate strings.'.format(len(self.strings_refs) - len(self.strings)))

        self.scripts_lib = {}
        for l in self.getrefs('Library'):
            for r in l.x['owned_scripts'].x['data'].x['value']:
                if r.ref == 1: continue
                if r.ref in self.scripts_lib:
                    self.notice('Script {} owned by multiple libraries, this should not happen'.format(l))
                self.scripts_lib[r.ref] = l

        # FIXME: register active_instructions too, if present
        self.entry_points = {}
        for c in self.getrefs('Code'):
            ep = self.get_entry_points(c.x['instructions'])
            for k, v in ep.items():
                self.entry_points[k] = (c, v)

        key = lambda x: x.x['instructions']['data_addr']
        self.code_objs = sorted(self.getrefs('Code'), key=key)
        self.code_addrs = list(map(key, self.code_objs))

        # Consistency checks
        if len(self.scripts_lib) != len(self.getrefs('Script')):
            self.notice('There are {} scripts but only {} are associated to a library'.format(len(self.getrefs('Script')), len(self.scripts_lib)))
        for c in self.getrefs('Class'):
            if c.x['library'] != self.scripts_lib[c.x['script'].ref]:
                self.notice('Class {} does not have matching script / library'.format(c))
        for a, code, b in zip(self.code_addrs, self.code_objs, self.code_addrs[1:]):
            assert a + len(code.x['instructions']['data']) < b  # code areas shouldn't overlap

    def search_address(self, addr):
        '''
        Given a PC (instruction) address this returns (code, offset),
        where `code` is the Code object it falls into, and `offset`
        is the offset from the start of the code. If the address doesn't
        belong to any Code zone, None is returned.
        '''
        pos = bisect(self.code_addrs, addr)
        if pos == 0: return
        code = self.code_objs[pos - 1]
        offset = addr - code.x['instructions']['data_addr']
        if offset < len(code.x['instructions']['data']):
            return code, offset

    def get_entry_points(self, instr, offset=False):
        kind = { 'kFullJIT': 0, 'kFullAOT': 1 }[kKind[self.kind][0]]
        mono, poly = kEntryOffsets[self.arch.split('-')[0]][kind]

        ep = { mono: { 'polymorphic': False, 'checked': True } }
        if not instr['flags']['single_entry']:
            ep[poly] = { 'polymorphic': True, 'checked': True }
        if instr['unchecked_entrypoint_pc_offset']:
            ep = { **ep, **{ k+instr['unchecked_entrypoint_pc_offset']: { **v, 'checked': False } for k, v in ep.items() } }
        #assert all(0 <= k < len(instr['data']) for k in ep) <- fails on ARM64, on a bunch of 4 or 8-byte instructions
        # FIXME: some instructions are called on its data_addr directly... add 0 to entry_points if it doesn't exist
        # FIXME: handle special stubs, like
        #   write_barrier_wrappers_stub
        #     ARM:   r0..r4 r6..r8 sb, 6 instructions each
        #     ARM64: x0..x14 x19..x25, 8 instructions each
        return ep if offset else { k+instr['data_addr']: v for k, v in ep.items() }
