# CLUSTERS: Stores the deserialization logic for every kind of cluster (used by CORE)

from io import BytesIO
from struct import unpack
import re

from .read import *
from .constants import *
from .other import parse_code_source_map

def make_cluster_handlers(s):
    # Unpack any properties from Snapshot here, to make the dependencies clear

    parse_rodata = s.parse_rodata
    parse_csm = s.parse_csm
    rodata = s.rodata
    rodata_offset = s.rodata_offset

    kind = s.kind
    includes_code = s.includes_code
    includes_bytecode = s.includes_bytecode

    is_precompiled = s.is_precompiled
    is_product = s.is_product
    is_64 = s.is_64

    kObjectAlignmentLog2 = s.kObjectAlignmentLog2
    raw_instance_size_in_words = s.raw_instance_size_in_words

    allocref = s.allocref
    readref = s.readref
    storeref = s.storeref

    warning = s.warning

    # Base handlers

    class Handler:
        do_read_from = True
        def __init__(self, cid):
            pass
    
    class SimpleHandler(Handler):
        def alloc(self, f, cluster):
            for _ in range(readuint(f)): allocref(cluster, {})
    
    class LengthHandler(Handler):
        def alloc(self, f, cluster):
            count = readuint(f)
            print("count: {}".format(count))
            for _ in range(count): 
                length = readuint(f)
                # print("length: {}".format(length))
                allocref(cluster, { 'length': length })
    
    class RODataHandler(Handler):
        do_read_from = False
        
        # https://github.com/dart-lang/sdk/blob/4c8a4f0d7ad055fa7dea5e80862cd2074f4454d3/runtime/vm/clustered_snapshot.cc#L2782-L2795
        def alloc(self, f, cluster):
            count = readuint(f)
            
            #for _ in range(count):
            #    allocref(cluster, { 'offset': readuint(f), 'shared': True }) # FIXME implement
            print("RODataHandler: count={}".format(count))
            running_offset = 0
            for i in range(count):
                running_offset += readuint(f) << kObjectAlignmentLog2
                # print("'RODataDeserializationCluster::ReadAlloc': d->GetObjectAt({})".format(running_offset))
                object_val = self.try_parse_object(running_offset)
                # print("parsed_object {}: {}".format(i, object_val))
                allocref(cluster, object_val)
                
            # if cluster CID == kStringCid: https://github.com/dart-lang/sdk/blob/4c8a4f0d7ad055fa7dea5e80862cd2074f4454d3/runtime/vm/clustered_snapshot.cc#L615-L629
            if cluster['cid'] == kkClassId['String']:
                # BuildCanonicalSetFromLayout
                if cluster['is_canonical']:
                    table_length = readuint(f)
                    first_element = readuint(f)
                    print("first_element={} table_length={}".format(first_element, table_length))

                    table=list([0 for _ in range(table_length)])

                    table_pos = first_element
                    for i in range(count):
                        table_FillGap=readuint(f)
                        table_pos += table_FillGap
                        r = cluster["refs"][i]
                        table[table_pos] = r
                        table_pos += 1
                        #print("fillgap length {}: {} , {}".format(i, table_FillGap, r))
                    # print("table_pos: {}".format(table_pos))

        def try_parse_object(self, offset):
            if not parse_rodata: return { 'offset': rodata_offset + offset }
            rodata.seek(offset)
            return self.parse_object(rodata)
        def fill(self, f, x, ref): pass

    # Handlers

    class HandlerStore:
        class TypedData(Handler):
            do_read_from = False
            type_associations = {
                'Int8': (1, 'b'),
                'Uint8': (1, 'B'),
                'Int16': (2, 'h'),
                'Uint16': (2, 'H'),
                'Int32': (4, 'i'),
                'Uint32': (4, 'I'),
                'Int64': (8, 'q'),
                'Uint64': (8, 'Q'),
            }
            def __init__(self, cid, external=False):
                m = re.fullmatch('(External)?TypedData(.+)Array', kClassId[cid])
                self.external = bool(m.group(1))
                element_size, parse_char = self.type_associations[m.group(2)]
                elem = lambda f: unpack('<' + parse_char, f.read(element_size))[0]
                self.parse_func = lambda f, count: [ elem(f) for _ in range(count) ]
                # Optimization: if Uint8 array, we can just read bytes
                if parse_char == 'B': self.parse_func = lambda f, count: f.read(count)
            
            def alloc(self, f, cluster):
                return (SimpleHandler if self.external else LengthHandler).alloc(self, f, cluster)
            
            def fill(self, f, x, ref):
                count = readuint(f)
                if self.external:
                    while f.tell() % kDataSerializationAlignment != 0: f.read(1)
                else:
                    x['canonical'] = read1(f)
                x['value'] = self.parse_func(f, count)

        class Class(Handler):
            def alloc(self, f, cluster):
                for _ in range(readuint(f)):
                    allocref(cluster, { 'cid': readcid(f), 'predefined': True })
                for _ in range(readuint(f)):
                    allocref(cluster, { 'predefined': False })

            def fill(self, f, x, ref):
                cid = readcid(f)
                if x['predefined'] and cid != x['cid']:
                    warning('Predefined class has different CID (alloc={}, fill={})'.format(x['cid'], cid))
                if not x['predefined'] and cid < kNumPredefinedCids:
                    warning('CID is predefined')
                x['cid'] = cid
                
                if (not is_precompiled) and (kind != kkKind['kFullAOT']):
                    x['binary_declaration'] = readuint(f, 32)
                
                # these two should be discarded if (predefined and IsInternalVMdefinedClassId)
                x['instance_size_in_words'] = readint(f, 32)
                x['next_field_offset_in_words'] = readint(f, 32)

                x['type_arguments_field_offset_in_words'] = readint(f, 32)
                x['num_type_arguments'] = readint(f, 16)
                x['num_native_fields'] = readuint(f, 16)
                x['token_pos'] = readtokenposition(f)
                x['end_token_pos'] = readtokenposition(f)
                x['state_bits'] = readuint(f, 32)

        class Instance(Handler):
            do_read_from = False

            # https://github.com/dart-lang/sdk/blob/4c8a4f0d7ad055fa7dea5e80862cd2074f4454d3/runtime/vm/clustered_snapshot.cc#L3639
            def alloc(self, f, cluster):
                count = readuint(f)
                # print("!!Instance deserialization - count: {}".format(count))
                cluster['next_field_offset_in_words'] = readCint(f, 32)
                cluster['instance_size_in_words'] = readCint(f, 32)
                for _ in range(count): allocref(cluster, {})
            
            
            def fill(self, f, x, ref):
                x['canonical'] = read1(f)
                count = ref.cluster['next_field_offset_in_words'] - raw_instance_size_in_words
                x['fields'] = [ readref(f, (ref, 'fields', n)) for n in range(count) ]

        class Type(Handler):
            def alloc(self, f, cluster):
                canonical_items = readuint(f)
                for i in range(canonical_items + readuint(f)):
                    allocref(cluster, { 'canonical': i < canonical_items })
            def fill(self, f, x, ref):
                x['token_pos'] = readtokenposition(f)
                x['type_state'] = readint(f, 8)

        class Mint(Handler):
            do_read_from = False
            def alloc(self, f, cluster):
                for _ in range(readuint(f)):
                    allocref(cluster, { 'value': readint(f, 64) })
            def fill(self, f, x, ref): pass

        class PatchClass(SimpleHandler):
            def fill(self, f, x, ref):
                if (not is_precompiled) and (kind != kkKind['kFullAOT']):
                    x['library_kernel_offset'] = readint(f, 32)

        class Function(SimpleHandler):
            def fill(self, f, x, ref):
                if not is_precompiled:
                    if kind == kkKind['kFullJIT']:
                        storeref(f, x, 'unoptimized_code', ref)
                    if includes_bytecode:
                        storeref(f, x, 'bytecode', ref)
                if includes_code:
                    storeref(f, x, 'code', ref)
                if kind == kkKind['kFullJIT']:
                    storeref(f, x, 'ic_data_array', ref)
                
                if (not is_precompiled) and (kind != kkKind['kFullAOT']):
                    x['token_pos'] = readtokenposition(f)
                    x['end_token_pos'] = readtokenposition(f)
                    x['binary_declaration'] = readuint(f, 32)
                x['packed_fields'] = readuint(f, 32)
                x['kind_tag'] = readuint(f, 64) # FIXME it should be 32

        class ClosureData(SimpleHandler):
            def fill(self, f, x, ref): pass

        class SignatureData(SimpleHandler):
            def fill(self, f, x, ref): pass

        class Field(SimpleHandler):
            def fill(self, f, x, ref):
                if kind != kkKind['kFullAOT']:
                    x['token_pos'] = readtokenposition(f)
                    x['end_token_pos'] = readtokenposition(f)
                    x['guarded_cid'] = readcid(f)
                    x['is_nullable'] = readcid(f)
                    x['static_type_exactness_state'] = readint(f,8)
                    if not is_precompiled:
                        x['binary_declaration'] = readuint(f,32)
                x['kind_bits'] = readuint(f,16)

        class Script(SimpleHandler):
            def fill(self, f, x, ref):
                x['line_offset'] = readint(f,32)
                x['col_offset'] = readint(f,32)
                x['kind'] = readint(f,8)
                x['kernel_script_index'] = readint(f,32)

        class Library(SimpleHandler):
            def fill(self, f, x, ref):
                x['index'] = readint(f,32)
                x['num_imports'] = readuint(f,16)
                x['load_state'] = readint(f,8)
                x['is_dart_scheme'] = read1(f)
                x['debuggable'] = read1(f)
                if not is_precompiled:
                    x['binary_declaration'] = readuint(f,32)

        class Code(Handler):
            # clusterd_sanpshot.cc#L2107
            def alloc(self, f, cluster):
                count = readuint(f)
                for i in range(count): 
                    state_bits = readint(f, bits=32)
                    #print("code {} statebits: {}".format(i, hex(state_bits)))
                    allocref(cluster, {'state_bits': state_bits})
                deferred_count = readuint(f)
                for i in range(deferred_count): 
                    state_bits = readint(f, bits=32)
                    print("code {} statebits: {}".format(i, hex(state_bits)))
                    allocref(cluster, {})

            def fill(self, f, x, ref):
                # x['state_bits'] = readint(f, 32)
                pass

        class FunctionType(SimpleHandler):
            # clusterd_sanpshot.cc#L2107

            def fill(self, f, x, ref):
                pass

        class ObjectPool(LengthHandler):
            do_read_from = False
            def fill(self, f, x, ref):
                def read_entry(n):
                    e = decode_object_entry_type_bits(readuint(f,8))
                    if e['type'] in {kkEntryType['kNativeEntryData'], kkEntryType['kTaggedObject']}:
                        e['raw_obj'] = readref(f, (ref, 'entries', n, 'raw_obj'))
                    elif e['type'] in {kkEntryType['kImmediate']}:
                        e['raw_value'] = readint(f)
                    elif e['type'] in {kkEntryType['kNativeFunction'], kkEntryType['kNativeFunctionWrapper']}:
                        pass
                    else:
                        warning('Unknown entry type {}...'.format(e['type']))
                    return e
                x['entries'] = [read_entry(n) for n in range(readuint(f))]

        class ExceptionHandlers(LengthHandler):
            do_read_from = False
            def fill(self, f, x, ref):
                count = readuint(f)
                storeref(f, x, 'handled_types_data', ref)
                def read_info():
                    i = {}
                    i['handler_pc_offset'] = readuint(f,32)
                    i['outer_try_index'] = readint(f,16)
                    i['needs_stacktrace'] = readint(f,8)
                    i['has_catch_all'] = readint(f,8)
                    i['is_generated'] = readint(f,8)
                    return i
                x['entries'] = [read_info() for _ in range(count)]

        class UnlinkedCall(SimpleHandler):
            def fill(self, f, x, ref): pass

        class MegamorphicCache(SimpleHandler):
            def fill(self, f, x, ref):
                x['filled_entry_count'] = readint(f, 32)

        class SubtypeTestCache(SimpleHandler):
            def fill(self, f, x, ref): pass

        class UnhandledException(SimpleHandler):
            def fill(self, f, x, ref): pass

        class TypeArguments(LengthHandler):
            do_read_from = False
            def fill(self, f, x, ref):
                count = readuint(f)
                x['canonical'] = read1(f)
                x['hash'] = readint(f, 32)
                storeref(f, x, 'instantiations', ref)
                x['types'] = [ readref(f, (ref, 'types', n)) for n in range(count) ]

        class TypeRef(SimpleHandler):
            def fill(self, f, x, ref): pass

        class TypeParameter(SimpleHandler):
            def fill(self, f, x, ref):
                x['parameterized_class_id'] = readint(f, 32)
                x['token_pos'] = readtokenposition(f)
                x['index'] = readint(f, 16)
                x['flags'] = readuint(f, 8)

        class Closure(SimpleHandler):
            def fill(self, f, x, ref): pass

        class Double(SimpleHandler):
            do_read_from = False
            def fill(self, f, x, ref):
                x['canonical'] = read1(f)
                x['value'] = readdouble(f)

        class GrowableObjectArray(SimpleHandler):
            def fill(self, f, x, ref): pass

        class StackTrace(SimpleHandler):
            def fill(self, f, x, ref): pass

        class Array(LengthHandler):
            do_read_from = False
            def fill(self, f, x, ref):
                count = readuint(f)
                x['canonical'] = read1(f)
                storeref(f, x, 'type_arguments', ref)
                x['value'] = [ readref(f, (ref, 'value', n)) for n in range(count) ]

        class Namespace(SimpleHandler):
            def fill(self, f, x, ref): pass

        class KernelProgramInfo(SimpleHandler):
            def fill(self, f, x, ref):
                x['kernel_binary_version'] = readuint(f, 32)

        class ContextScope(LengthHandler):
            do_read_from = False
            def fill(self, f, x, ref):
                length = readuint(f)
                x['implicit'] = read1(f)
                def read_variable_desc(src):
                    x = {}
                    x['declaration_token_pos'] = readuint(f)
                    x['token_pos'] = readuint(f)
                    storeref(f, x, 'name', src)
                    storeref(f, x, 'is_final', src)
                    storeref(f, x, 'is_const', src)
                    storeref(f, x, 'value_or_type', src)
                    x['context_index'] = readuint(f)
                    x['context_level'] = readuint(f)
                    return x
                x['variables'] = [ read_variable_desc((ref, 'variables', i)) for i in range(length) ]

        class ICData(SimpleHandler):
            def fill(self, f, x, ref):
                if not is_precompiled:
                    x['deopt_id'] = readint(f, 32)
                x['state_bits'] = readint(f, 32)

        class LibraryPrefix(SimpleHandler):
            def fill(self, f, x, ref):
                x['num_imports'] = readuint(f, 16)
                x['deferred_load'] = read1(f)

        class RegExp(SimpleHandler):
            def fill(self, f, x, ref):
                x['num_one_byte_registers'] = readint(f, 32)
                x['num_two_byte_registers'] = readint(f, 32)
                x['type_flags'] = readint(f, 8)

        class WeakProperty(SimpleHandler):
            def fill(self, f, x, ref): pass
        
        if includes_code:
            class String(RODataHandler):
                # runtime/vm/clustered_snapshot#L2778 --> Deserializer::GetObjectAt --> ImageReader::GetObjectAt
                # --> UntaggedObject::FromAddress (f is BytesIO pointing to rodata, already seeked to correct offset address)
                def parse_object(self, f):
                    addr = f.tell()
                    # UntaggedObject::FromAddress (/runtime/vm/raw_object.h)
                    objPtr = addr + kHeapObjectTag
                    # v1,length,v3 = unpack('<LLL', f.read(12)) # SMI values
                    v1 = read_smi(f)
                    length = read_smi(f)
                    v3 = read_smi(f)
                    value = f.read(length)

                    res = {'objPtr': objPtr, 'v1': v1, 'length': length, 'v3': v3, 'value': value}
                    print("parsed 'String': {}".format(res))
                    return res

            class OneByteString(RODataHandler):
                def parse_object(self, f):
                    if is_64:
                        tags, hash_, length = unpack('<LLQ', f.read(16))
                    else:
                        tags, length, hash_ = unpack('<LLL', f.read(12))
                    value = "".join(chr(x) for x in f.read(length//2))
                    return { 'tags': tags, 'hash': hash_, 'value': value }
            class TwoByteString(RODataHandler):
                def parse_object(self, f):
                    if is_64:
                        tags, hash_, length = unpack('<LLQ', f.read(16))
                    else:
                        tags, length, hash_ = unpack('<LLL', f.read(12))
                    value = f.read(length).decode('utf-16-le')
                    return { 'tags': tags, 'hash': hash_, 'value': value }
        else:
            # FIXME: verify this works
            class OneByteString(LengthHandler):  # type: ignore
                do_read_from = False
                def fill(self, f, x, ref):
                    length = readuint(f)
                    x['canonical'] = read1(f)
                    x['hash'] = readuint(f, 32)
                    x['value'] = "".join(chr(x) for x in f.read(length))
            class TwoByteString(LengthHandler):  # type: ignore
                do_read_from = False
                def fill(self, f, x, ref):
                    length = readuint(f)
                    x['canonical'] = read1(f)
                    x['hash'] = readuint(f, 32)
                    x['value'] = f.read(length * 2).decode('utf-16-le')

        class PcDescriptors(RODataHandler):
            def parse_object(self, f):
                if is_64:
                    tags, _, length = unpack('<LLQ', f.read(16))
                else:
                    tags, length = unpack('<LL', f.read(8))
                return { 'tags': tags, 'data': f.read(length) }

        class CodeSourceMap(RODataHandler):
            def parse_object(self, f):
                if is_64:
                    tags, _, length = unpack('<LLQ', f.read(16))
                else:
                    tags, length = unpack('<LL', f.read(8))
                data = f.read(length)
                if not parse_csm:
                    return { 'tags': tags, 'data': data }
                return { 'tags': tags, 'ops': parse_code_source_map(data) }

        class StackMap(RODataHandler):
            def parse_object(self, f):
                tags = unpack('<L', f.read(4))[0]
                if is_64: f.read(4)
                pc_offset, length, slow_path_bit_count = unpack('<IHH', f.read(8))
                bits = []
                while length > 0:
                    c = f.read(1)[0]
                    for i in range(8):
                        if length == 0: break
                        bits.append(bool((c >> i) & 1))
                        length -= 1
                return { 'tags': tags, 'pc_offset': pc_offset, 'bits': bits, 'slow_path_bit_count': slow_path_bit_count }

        # Doesn't really exist, but used for parsing roots
        class ObjectStore(SimpleHandler):
            def fill(self, f, x, ref): pass

    return HandlerStore
