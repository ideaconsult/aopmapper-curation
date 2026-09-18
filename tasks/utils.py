from xsdata.models.datatype import XmlDateTime
from enum import Enum
import json
from aopkb import Data
from pydantic import BaseModel
import requests
import re
from bs4 import BeautifulSoup
import unicodedata


suffix_t = [
            "description", "value", "biological_plausibility",
            "emperical_support_linkage", "uncertainties_or_inconsistencies",
            "evidence_supporting_taxonomic_applicability", "references",
            "biological_organization_level", "measurement_methodology",
            "known_modulating_factors",
            "name", "short_name", "title"]

_exact_match = ["casrn", "jchem_inchi_key","indigo_inchi_key", "indigo_inchi_key_t", "dsstox_id", "aopid",
                "synonym", "synonyms", "molecular_initiating_event","adverse_outcome",
                "creation_timestamp", "last_modification_timestamp",
                "upstream_aopid", "downstream_aopid", "upstream_ke",
                "downstream_ke"]


def get_kid(lookup, id, qname="key-event-reference"):
    if id is None:
        return None
    _kid = lookup.get(qname, {}).get(id, {}).get("wiki-id", None)
    #print(id, qname, _kid)
    return _kid


def _generate_prefix(ref):
    base = re.sub(r"-reference$", "", ref)
    return "".join(p[0].upper() for p in base.split("-"))


BIOLOGICAL_TERM_ITEMS = ("biological_object", "biological_process", "biological_action")


def build_wiki_id_lookup(data):
    """UUID -> {"wiki-id": ...} lookup for key events and biological object/process/action
    terms, keyed the way get_kid() expects (qname -> uuid -> {"wiki-id": ...}).

    Same resolution tasks/ke_graph.py uses: key events get their public "KE123" id from
    the <key-event-reference id="uuid" aop-wiki-id="123">-style cross references in
    data.vendor_specific.any_element; biological object/process/action terms get theirs
    from their own source/source-id (or name, for actions, which have no ontology source).
    """
    lookup = {}
    for e in data.vendor_specific.any_element:
        qname = e["qname"].split("}")[-1]
        eid = e["attributes"]["id"]
        lookup.setdefault(qname, {})[eid] = {
            "wiki-id": "{}{}".format(_generate_prefix(qname), e["attributes"].get("aop-wiki-id")),
        }

    for item in BIOLOGICAL_TERM_ITEMS:
        qname = "{}-reference".format(item.replace("_", "-"))
        for term in getattr(data, item, []):
            source_id = (term.source_id or "").strip() or None
            source = (term.source or "").strip() or None
            if item == "biological_action":
                wiki_id = term.name
            elif source_id is not None:
                wiki_id = f"{source}_{source_id}" if (source and re.match(r"\d", source_id)) \
                    else source_id.replace(":", "_")
            else:
                wiki_id = None
            lookup.setdefault(qname, {})[term.id] = {"wiki-id": wiki_id, "name": term.name}

    return lookup

# Custom encoder for Enums
class EnumEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.value  # Return the string value of the enum
        return super().default(obj)


def transform_keys(data):
    """Appends '_s' to all string keys in a nested dictionary."""
    if isinstance(data, dict):
        # Process dictionary keys, ignoring 'id'
        return {
            "{}{}".format(k, get_suffix(k, v)) if k != "id" else k: transform_keys(v)
            for k, v in data.items()
        }
    elif isinstance(data, list):
        # Process list elements recursively
        return [transform_keys(item) for item in data]
    else:
        # Return value as-is
        return data
    

def get_suffix(k, v, exact_match=_exact_match):
    if isinstance(v, dict):
        return ""
    else:
        return "_s" if k in exact_match else "_t"


def preprocess_timestamps(data):
    """Recursively process all 'last_modification_timestamp' fields."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key in ["last_modification_timestamp", "creation_timestamp"] and isinstance(value, str):
                try:
                    data[key] = XmlDateTime.from_string(value) 
                except ValueError:
                    pass  # Skip if conversion fails
            else:
                preprocess_timestamps(value)  # Recurse into nested structures
    elif isinstance(data, list):
        for item in data:
            preprocess_timestamps(item)


def transform_for_solr(model):
    """Transforms Pydantic model to Solr-compatible format."""
    def clean_value(value):
        """Cleans value by removing trailing '\n' if present."""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, Enum):
            return value.value  # Get the string value of the Enum        
        return value

    def process_data(data):
        """Recursively process data to handle nested models and clean values."""
        if isinstance(data, dict):
            return {
                k: process_data(v) for k, v in data.items()
                if v is not None and v != "" and v != [] and v != {} # Ignore None & empty values
            }
        elif isinstance(data, list):
            return [process_data(item) for item in data]  # Handle lists
        elif isinstance(data, BaseModel):
            return process_data(data.model_dump())  # Convert nested models
        else:
            return clean_value(data)  # Clean the value

    # Start processing from the model
    return process_data(model.model_dump())            


def clean_html(html_data: str) -> str:
    if html_data == "":
        return html_data    
    """Remove all HTML tags and return plain readable text."""
    soup = BeautifulSoup(html_data, "lxml")
    text = soup.get_text(separator="\n", strip=True)
    return text.replace('\xa0', ' ')


def clean_node_to_text(item, tags: str) -> str:
    for tag in tags:
        _txt = item.get(tag, None)
        if _txt is not None:
            try:
                item[tag] = clean_html(_txt)
            except Exception as x:
                print(x)
    return item


def extract_doi(text):
    # Regular expression for detecting DOIs
    doi_pattern = r'\b10\.[0-9]{4,9}/[-._;()/:A-Z0-9]+(?:[A-Z0-9]|\b)\b'
    do_i = re.findall(doi_pattern, text, re.IGNORECASE)
    # Remove duplicates by converting the list to a set
    unique_dois = set(do_i)
    return list(unique_dois)

def get_top_ontology_terms_via_tagger(text, solr_tagger , auth):
    url = f'{solr_tagger}/tag'  # Replace with your Solr URL and core name
    headers = {
        'Content-Type': 'text/plain',  # Content type expected by the tagger handler
    }
    # Prepare data to send to Solr Tagger (plain text)
    data = text  # The text you want to tag
    params = {
        'overlaps': 'LONGEST_DOMINANT_RIGHT',  # Overlap handling option
        'tagsLimit': 5000,  # Max tags to return
        'fl': 'id,name,ontology,Parents',  # Return relevant fields from the response
        'wt': 'json',  # Response format (JSON)
        'indent': 'on',  # Pretty printing the response (optional)
        #'fq': 'ontology:("bao" OR "enm" OR "chmo" OR "gracious_guidelines" OR "envo" OR "biomodels")'
    }
    # Send the request to Solr's Tagger handler
    response = requests.post(url, params=params, data=data, headers=headers, auth=auth)
    if response.status_code == 200:
        response_data = response.json()
        # Extract ontology terms from the Solr response
        ontology_terms = []
        #for tag in response_data.get('tags',[]):
        #    print(tag)
        for doc in response_data.get('response',{}).get('docs',[]):
            # check for generic assays
            if doc["id"] in ["npo#NPO_1408", "OBI_0000070", "EFO_0001444", "Thesaurus.owl#C60819","Thesaurus.owl#C16341", "Thesaurus.owl#C37990", "Thesaurus.owl#C117062", "Thesaurus.owl#C25209"]:
                continue
            print(doc["id"],doc["name"], doc["ontology"])
            ontology_terms.append({"id" : doc["id"], "name" : doc["name"][0], "ontology" : doc["ontology"][0]})          
        return ontology_terms
    return None


def clean_assay_name(name):
    return name.upper().replace("FlOURESCENCE", "FlUORESCENCE").replace("FLOWCYTOMETRY","FLOW CYTOMETRY").replace(" STAINING","").replace(" STAIN","").replace("EXAMINATION","").replace("ASSESSMENT","").replace(" ANALYSES","").replace(" ANALYSIS"," ").replace(" OBSERVATION","").replace("/","").replace("-","").replace(" KIT", "").replace(" TESTS", "").replace(" TEST", "").replace(" ASSAYS", "").replace(" ASSAY", "").strip()


def clean_for_solr(name):
    # Unicode normalization (e.g., Greek letters, accents)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()

    name = name.upper()
    name = re.sub(r"\bFLOURESCENCE\b", "FLUORESCENCE", name,  flags=re.IGNORECASE)
    name = re.sub(r"FLOWCYTOMETRY", "FLOW CYTOMETRY", name)
    name = re.sub(r"ENZYME-LINKED IMMUNOSORBENT ASSAY (ELISA)", "ELISA", name)
    name = re.sub(r"MICRO ARRAY", "MICROARRAY", name)
    name = re.sub(r"MICRO-ARRAY", "MICROARRAY", name)
    name = re.sub(r"Q-PCR", "QPCR", name)
    # Apply the most specific substitutions first
    if "TEER" in name:
        name = "TEER assay"
    name = re.sub(r"FLUORESCENCE RECOVERY AFTER PHOTOBLEACHING", "FLUORESCENCE RECOVERY AFTER PHOTOBLEACHING (FRAP)", name)
    name = re.sub(r"FLUORESCENCE ENERGY TRANSFER (FRET) SPECTROSCOPY", "FLUORESCENCE RESONANCE ENERGY TRANSFER (FRET)", name)
    name = re.sub(r"FRET", "FLUORESCENCE RESONANCE ENERGY TRANSFER (FRET)", name)
    name = re.sub(r"POST-LABELING","POST LABELLING",name)
    name = re.sub(r"3D SPHEROID INVASION", "3D SPHEROID MIGRATION", name)
    name = re.sub(r"AMPLEXRED", "AMPLEX RED", name)
    name = re.sub(r"ATOMIC FORCE MICROSCOPY", "ATOMIC FORCE MICROSCOPY (AFM)", name)
    name = re.sub(r"CELL COUNTING ASSAY","CELL COUNTS ASSAY", name)
    name = re.sub(r"CHROMATIN IMMUNOPRECIPITATION", "CHROMATIN IMMUNOPRECIPITATION (CHIP)", name)
    name = re.sub(r"CHIP ASSAY","CHROMATIN IMMUNOPRECIPITATION (CHIP)", name)
    name = re.sub(r"CHIPS","CHROMATIN IMMUNOPRECIPITATION (CHIP)", name)

    name = re.sub(r"CO-IMMUNOPRECIPITATION","CO-IMMUNOPRECIPITATION (CO-IP)", name)
    name = re.sub(r"DYNAMIC ENERGY BUDGET (DEB) MODELS", "DYNAMIC ENERGY BUDGET MODEL", name)
    name = re.sub(r"EDU ASSAY", "EDU BIOLABELING ASSAY", name)
    name = re.sub(r"EDU INCORPORATION ASSAY","EDU BIOLABELING ASSAY", name)
    name = re.sub(r"ELISAS","ELISA", name)
    name = re.sub(r"ENZYME-LINKED IMMUNOSORBENT ASSAY (ELISA)", "ELISA", name)
    name = re.sub(r"ENZYME-LINKED IMMUNOSORBENT ASSAY", "ELISA", name)
    name = re.sub(r"ENZYME-LINKED IMMUNOSORBENT ASSAY (ELISA)", "ELISA", name)
    name = re.sub(r"KI-67 IMMUNOHISTOCHEMISTRY","KI67 ASSAY", name)
    name = re.sub(r"KI-67 IMMUNOFLUORESCENT STAINING","KI67 ASSAY", name)
    name = re.sub(r"LDH CYTOTOXICITY ASSAY","LDH ASSAY", name)
    name = re.sub(r"LDH LEAKAGE ASSAY","LDH ASSAY", name)
    name = re.sub(r"LENTIVIRAL NF-ΚBGFP REPORTER WITH FLOW CYTOMETRY",
                  "LENTIVIRAL NF-KB GFP REPORTER WITH FLOW CYTOMETRY", name)
    name = re.sub(r"LIQUID CHROMATOGRAPHY/MASS SPECTROMETRY",
                  "LIQUID CHROMATOGRAPHY MASS SPECTROMETRY (LC-MS)",name)
    name = re.sub(r"GSH:GSSG", "GSH/GSSG", name)
    name = re.sub(r"HAEMOCYTOMETER", "HAEMATOCYTOMETER", name)
    name = re.sub(r"HEMAGGLUTINATION ASSAY", "HEMAGGLUTINATION ASSAY, HAI", name)
    name = re.sub(r"MICRO ELECTRODE", "MICROELECTRODE", name)
    name = re.sub(r"MN ASSAY", "MICRONUCLEUS ASSAY", name)
    name = re.sub(r"ASSAYS", "ASSAY", name)
    name = re.sub(r"MRI OR CT SCANS", "MRI/CT SCANS", name)
    name = re.sub(r"OPTICAL COHERENCE TOMOGRAPHY", "OPTICAL COHERENCE TOMOGRAPHY (OCT)", name)
    name = re.sub(r"PATCH-CLAMP", "PATCH CLAMP", name)
    name = re.sub(r"PATCH CLAMPING", "PATCH CLAMP", name)
    name = re.sub(r"BLOTTING", "BLOT", name)
    name = re.sub(r"PROPIDIUM IODIDE (PI) STAINING", "PROPIDIUM IODIDE STAINING", name)
    name = re.sub(r"PROTEOMICS", "PROTEOMIC", name)
    name = re.sub(r"QUANTITATIVE RTPCR", "qRT-PCR", name)
    name = re.sub(r"QUANTITATIVE REAL-TIME RT-PCR", "qRT-PCR", name)
    name = re.sub(r"Real-time reverse transcription-polymerase chain reaction (qRT-PCR)","qRT-PCR", name,  flags=re.IGNORECASE)
    name = re.sub(r"Quantitative reverse transcription-polymerase chain reaction (RT-PCR)", "qRT-PCR", name,  flags=re.IGNORECASE)
    name = re.sub(r"Real-time quantitative reverse transcription polymerase chain reaction (qRT-PCR)", "qRT-PCR", name,  flags=re.IGNORECASE)

    name = re.sub(r"RADIOIMMUNOASSAY", "RADIOIMMUNO ASSAY", name)
    name = re.sub(r"RADIO-LABELED", "RADIO LABELED", name)
    name = re.sub(r"REAL-TIME PCR", "RT-PCR", name)
    name = re.sub(r"REAL-TIME POLYMERASE CHAIN REACTION", "RT-PCR", name)
    name = re.sub(r"CENTRIFUGE", "CENTRIFUGATION", name)
    name = re.sub(r"RNASEQ", "RNA-SEQ", name)
    if name == "TBARS":
        name = "TBARS (THIOBARBITURIC ACID REACTIVE SUBSTANCE)"
    if name == "TRANSMISSION ELECTRON MICROSCOPY":
        name = "TRANSMISSION ELECTRON MICROSCOPY (TEM)"

    name = re.sub(r"BIOLUMINES[CE]N[SC]E IMAGING", "BIOLUMINESCENCE IMAGING (BLI)", name, flags=re.IGNORECASE)
    name = re.sub(r"BRDU INCORPORATION ASSAY", "BRDU ASSAY", name, flags=re.IGNORECASE)
    name = re.sub(r"BROMO-DEOXYURIDINE \(BRDU\) INCORPORATION", "BRDU ASSAY", name, flags=re.IGNORECASE)
    name = re.sub(r"BROMODEOXYURIDINE \(BRDU\) LABELLING", "BRDU LABELLING", name, flags=re.IGNORECASE)


    name = re.sub(r"[/-]", " ", name)
    name = re.sub(r"(?<=\s)(ASSAYS?|TEST|METHOD|STUDIES|TECHNIQUE|RECORDINGS?|APPROACHES|METHODS|DETECTION|EXAMINATION|VISUALIZATION|OBSERVATION|KITS?|MEASUREMENT|DETECTION|FORMATION|QUANTIFICATION|MONITORING|EVALUATION|ANALYSIS|ANALYSES|ASSESSMENT)\b", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name
