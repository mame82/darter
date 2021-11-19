# CONSTANTS

import json
import os.path


EXPECTED_VERSION = '9cf77f4405212c45daf608e1cd646852'

MAGIC_VALUE = 0xdcdcf5f5

kMaxUint32 = 0xFFFFFFFF
kSectionMarker = 0xABAB

kMaxPreferredCodeAlignment = 32

# runtime/vm/pointer_tagging.h
kHeapObjectTag = 1

# as an exception, kClassId names are stripped of k- and -Cid (except items 2 and 3: kFreeListElement, kForwardingCorpse)
# https://github.com/dart-lang/sdk/blob/4c8a4f0d7ad055fa7dea5e80862cd2074f4454d3/runtime/vm/class_id.h
with open(os.path.join(os.path.dirname(__file__), 'data', 'class_ids_4c8a4f0d7ad055fa7dea5e80862cd2074f4454d3.json')) as f:
    kClassId = json.load(f)
kkClassId = { k: v for (v, k) in enumerate(kClassId) }
assert len(kClassId) == len(kkClassId)
# kNumPredefinedCids is not included in kClassIds
kNumPredefinedCids = len(kClassId)
kTypedDataInt8ArrayCid = kkClassId['TypedDataInt8Array']
kByteDataViewCid = kkClassId['ByteDataView']

kTypedDataCidRemainderInternal = 0
kTypedDataCidRemainderView = 1
kTypedDataCidRemainderExternal = 2

kDataSerializationAlignment = 8

kEntryType = [ 'kTaggedObject', 'kImmediate', 'kNativeFunction', 'kNativeFunctionWrapper', 'kNativeEntryData' ]
kkEntryType = { k: v for (v, k) in enumerate(kEntryType) }
decode_object_entry_type_bits = lambda x: { "patchable": not (x >> 7), "type": x & 0x7F }

__isBase = lambda x, r: \
    (kTypedDataInt8ArrayCid <= x < kByteDataViewCid) and (x - kTypedDataInt8ArrayCid) % 3 == r
isTypedData = lambda x: __isBase(x, kTypedDataCidRemainderInternal)
isTypedDataView = lambda x: __isBase(x, kTypedDataCidRemainderView) or x == kByteDataViewCid
isExternalTypedData = lambda x: __isBase(x, kTypedDataCidRemainderExternal)

# https://github.com/dart-lang/sdk/blob/4c8a4f0d7ad055fa7dea5e80862cd2074f4454d3/runtime/vm/snapshot.h#L24
kKind = [
    ('kFull', "Full snapshot of an application"),
    ('kFullCore', "Full snapshot of core libraries. Agnostic to null safety."),
    ('kFullJIT', "Full + JIT code"),
    ('kFullAOT', "Full + AOT code"),
    ('kNone', "gen_snapshot"),
    ('kInvalid', None),
]
kkKind = { k[0]: v for (v, k) in enumerate(kKind) }

kPcDescriptorKindBits = [
    ('deopt', 'Deoptimization continuation point.'),
    ('icCall', 'IC call.'),
    ('unoptStaticCall', 'Call to a known target via stub.'),
    ('runtimeCall', 'Runtime call.'),
    ('osrEntry', 'OSR entry point in unopt. code.'),
    ('rewind', 'Call rewind target address.'),
    ('other', None),
]
kkPcDescriptorKindBits = { k[0]: v for (v, k) in enumerate(kPcDescriptorKindBits) }

with open(os.path.join(os.path.dirname(__file__), 'data', 'stub_code_list.json')) as f:
    kStubCodeList = json.load(f)

with open(os.path.join(os.path.dirname(__file__), 'data', 'runtime_offsets.json')) as f:
    kRuntimeOffsets = json.load(f)

# runtime/vm/dart_entry.h
kCachedDescriptorCount = 32
# runtime/vm/object.h
kCachedICDataZeroArgTestedWithoutExactnessTrackingIdx = 0
kCachedICDataMaxArgsTestedWithoutExactnessTracking = 2
kCachedICDataOneArgWithExactnessTrackingIdx = kCachedICDataZeroArgTestedWithoutExactnessTrackingIdx + kCachedICDataMaxArgsTestedWithoutExactnessTracking + 1
kCachedICDataArrayCount = kCachedICDataOneArgWithExactnessTrackingIdx + 1


### Entry points

# tuples are (kMonomorphicEntryOffset<x>, kPolymorphicEntryOffset<x>)
kEntryOffsets = {
    'ia32': (
        (6, 34), # JIT
        (0, 0),  # AOT
    ),
    'x64': (
        (8, 40), # JIT
        (8, 32), # AOT
    ),
    'arm': (
        (0, 40), # JIT
        (0, 20), # AOT
    ),
    'arm64': (
        (8, 48), # JIT
        (8, 28), # AOT
    ),
    'dbc': (
        (0, 0),  # JIT
        (0, 0),  # AOT
    ),
}

### AppJIT blob wrapping

kAppJITMagic = 0xf6f6dcdc
kAppSnapshotPageSize = 4 * 1024

### AppAOT blob wrapping

kAppAOTSymbols = [
    '_kDartVmSnapshotData',
    '_kDartVmSnapshotInstructions',
    '_kDartIsolateSnapshotData',
    '_kDartIsolateSnapshotInstructions'
]
