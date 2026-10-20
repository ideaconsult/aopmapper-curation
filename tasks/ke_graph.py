from tasks.utils import preprocess_timestamps
import json
from aopkb import Data
from tasks.utils import transform_keys, transform_for_solr, get_kid
from collections import defaultdict
import re


# + tags=["parameters"]
upstream = ["parse_pydantic"]
product = None
item = None
version = None
# -


def generate_prefix(ref):
    if ref == "aop-reference":
        return "AOP"  # Custom prefix for "aop-reference"
    
    # Remove '-reference' suffix
    base = re.sub(r"-reference$", "", ref)
    # Split by hyphen and take the first letter of each part
    parts = base.split("-")
    return ''.join(p[0].upper() for p in parts)


json_data = []
with open(upstream["parse_pydantic"]["json"], 'r', encoding="utf-8") as file:
    json_data = json.load(file)

preprocess_timestamps(json_data)

data = Data.model_validate(json_data)
# lookup = {e["attributes"]["id"]: e["attributes"]["aop-wiki-id"] for e in data.vendor_specific.any_element}
# Initialize a defaultdict to group by local qname
lookup = defaultdict(dict)
ke_links = defaultdict(lambda: {
    "upstream": [],
    "downstream": [],
    "indirect_upstream": [],
    "indirect_downstream": [],
    "MIE" : [],
    "adverse_outcome" : [],
    "title" : ""
})

for e in data.vendor_specific.any_element:
    # Extract local qname (e.g., strip namespace)
    qname = e["qname"].split("}")[-1]
    eid = e["attributes"]["id"]
    lookup[qname][eid] = {
        "wiki-id": "{}{}".format(generate_prefix(qname),
                                 e["attributes"].get("aop-wiki-id")),
        "text": e.get("text", ""),
        "downstream_ss" : [],
        "upstream_ss" : []
    }


# Convert to regular dict and pretty-print
lookup = dict(lookup)

for item in ["biological_object", "biological_process",
              "biological_action", "taxonomy"]:
    qname = "{}-reference".format(item.replace("_", "-"))
    for bo in getattr(data, item, []):
        _bo = transform_for_solr(bo)
        _bo = transform_keys(_bo)
        source_id = _bo.get("source_id_t", None)
        source = _bo.get("source_t", None)
        starts_with_digit = bool(re.match(r"\d", source_id))
        if item == "biological_action":
            _bo["wiki-id"] = _bo.get("name_t")
        else:
            if source_id is not None:
                if starts_with_digit and source is not None:
                    _bo["wiki-id"] = f"{source}_{source_id}"
                else:
                    _bo["wiki-id"] = source_id.replace(":","_")
        # _bo["type_s"] = item
    
        for key in _bo:
            lookup[qname][_bo["id"]][key] = _bo[key]

for ke in data.key_event:
    lookup["key-event-reference"][ke.id]["title"] = ke.title

for aop in data.aop:
    _ke = transform_for_solr(aop)
    _ke = transform_keys(_ke)
    aop_id = get_kid(lookup, _ke["id"], "aop-reference")
    for s in _ke.get("aop_stressors", {}).get("aop_stressor_t",[]):
        lookup["stressor-reference"][s["stressor_id_t"]]["downstream_ss"].append(aop_id)

    #for _ke in _ke.get("key_event", {}):
        #print(_ke)
        # ke_id = get_kid(lookup, _ke["id"], "key_event-reference")
        # ke_links[ke_id]["title_t"] = _ke[ke_id]["title_t"]

    for _ker in _ke.get("key_event_relationships", {}).get("relationship_t",[]):
        ker_id = get_kid(lookup, _ker["id"], "key-event-relationship-reference")
        ker_lookup = lookup["key-event-relationship-reference"][_ker["id"]]
        adjacency = _ker["adjacency_t"]
        _key = f"KER_{adjacency}"
        if _key not in ker_lookup:
            ker_lookup[_key] = []
        ker_lookup[_key].append(aop_id)
    for _mie in _ke.get("molecular_initiating_event_s", {}):
        mie_id = get_kid(lookup, _mie["key_event_id_t"], "key-event-reference")
        ke_links[mie_id]["upstream"].append(aop_id)
        ke_links[mie_id]["MIE"].append(aop_id)
    for _ao in _ke.get("adverse_outcome_s", {}):
        ao_id = get_kid(lookup, _ao["key_event_id_t"], "key-event-reference")
        ke_links[ao_id]["adverse_outcome"].append(aop_id)


for stressor in data.stressor:
    _ke = transform_for_solr(stressor)
    _ke = transform_keys(_ke)
    s_id = get_kid(lookup, _ke["id"], "stressor-reference")
    for s in _ke.get("chemicals", {}).get("chemical_initiator_t",[]):
        lookup["chemical-reference"][s["chemical_id_t"]]["downstream_ss"].append(s_id)



for ker in data.key_event_relationship:
    _ke = transform_for_solr(ker)
    _ke = transform_keys(_ke)
    uuid_up = _ke.get("title", {}).get("upstream_id_t", None)
    up = get_kid(lookup, uuid_up,  qname="key-event-reference")
    uuid_down = _ke.get("title", {}).get("downstream_id_t", None)
    down = get_kid(lookup, uuid_down,  qname="key-event-reference")
    
    up_title = lookup["key-event-reference"].get(uuid_up, {}).get("title", "")
    down_title = lookup["key-event-reference"].get(uuid_down, {}).get("title", "")
    lookup["key-event-relationship-reference"][_ke["id"]]["title"] = f"{up_title} leads to {down_title}"

    if "KER_adjacent" in lookup["key-event-relationship-reference"][ker.id]:
        upstream = "upstream"
        downstream = "downstream"
    else:
        upstream = "indirect_upstream"
        downstream = "indirect_downstream"

    ke_links[up][downstream].append(down)
    ke_links[down][upstream].append(up)


with open(product["data"], "w") as file:
    json.dump(ke_links, file, indent=4)  # `indent` makes it readable

lookup["verison"] = str(version)
with open(product["lookup"], "w") as file:
    json.dump(lookup, file, indent=4) 


# The legacy name-only assay dump (resources/measurement_methodology.json) used to be
# relabelled by wiki-id here for the retired assay2solr.py. The Solr spine now sources
# assays from the cross-model consensus (tasks/assay_consensus2solr.py), so nothing
# consumes that output any more and the step is gone.