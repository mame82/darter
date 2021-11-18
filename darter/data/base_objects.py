import re

from ..constants import kClassId, kkClassId, kStubCodeList, kCachedICDataArrayCount, kCachedDescriptorCount

class_cids = [ n for n in range(kkClassId['Class'], kkClassId['Instance']) if n != kkClassId['Error'] and n != kkClassId['CallSiteData'] ]
#class_cids += [ kkClassId['Dynamic'], kkClassId['Void'] ]

# tuples are (original_code, type, value[, additional_fields])
make_base_entries = lambda includes_code: [
    ("Object::null()", "Null", "null"),
    ("Object::sentinel().ptr()", "Null", "sentinel"),
    ("Object::transition_sentinel().ptr()", "Null", "transition_sentinel"),
    ("Object::empty_array().prt()", "Array", "<empty_array>"),
    ("Object::zero_array().prt()", "Array", "<zero_array>"),
    ("Object::dynamic_type().prt()", "Type", "<dynamic type>"),
    ("Object::void_type().prt()", "Type", "<void type>"),
    ("Object::empty_type_arguments().prt()", "TypeArguments", "[]"),
    ("Bool::True().prt()", "bool", "true"),
    ("Bool::False().prt()", "bool", "false"),
    ("Object::extractor_parameter_types().prt()", "Array", "<extractor parameter types>"),
    ("Object::extractor_parameter_names().prt()", "Array", "<extractor parameter names>"),
    ("Object::empty_context_scope().prt()", "ContextScope", "<empty>"),
    ("Object::empty_object_pool().ptr()", "ObjectPool", "<empty>"),
    ("Object::empty_compressed_stackmaps().ptr()", "CompressedStackMaps", "<empty>"),
    ("Object::empty_descriptors().prt()", "PcDescriptors", "<empty>"),
    ("Object::empty_var_descriptors().ptr()", "LocalVarDescriptors", "<empty>"),
    ("Object::empty_exception_handlers().ptr()", "ExceptionHandlers", "<empty>"),

    *( ("ArgumentsDescriptor::cached_args_descriptors_[i]", "ArgumentsDescriptor", "<cached arguments descriptor {}>".format(i)) for i in range(kCachedDescriptorCount) ),
    *( ("ICData::cached_icdata_arrays_[i]", "Array", "<empty icdata entries {}>".format(i)) for i in range(kCachedICDataArrayCount) ),

    ("SubtypeTestCache::cached_array_", "Array", "<empty subtype entries>"),

    *( ("table->At(k{}Cid)".format(kClassId[cid]), "Class", kClassId[cid], { 'cid': cid }) for cid in class_cids ), # Adapted

    ("table->At(kDynamicCid)", "Class", "dynamic", { 'cid': kkClassId['Dynamic'] }),
    ("table->At(kVoidCid)", "Class", "void", { 'cid': kkClassId['Void'] }),

    *( ( ("StubCode::EntryAt(i).prt()", "Code", "<stub code {}>".format(i)) for i in kStubCodeList ) if not includes_code else [] ),
]

def init_base_objects(Ref, snapshot, includes_code):
    tmp_cluster = { 'handler': 'BaseObject', 'cid': 'BaseObject' }
    entries = make_base_entries(includes_code)
    get_data = lambda e: { 'type': e[1], 'value': e[2], **(e[3] if len(e) > 3 else {}) }
    # ref 0 is illegal
    snapshot.refs = { i+1: Ref(snapshot, i+1, tmp_cluster, get_data(entry))
        for i, entry in enumerate(entries) }
    snapshot.refs['next'] = len(entries) + 1
    snapshot.base_clusters = []
