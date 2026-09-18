from xsdata_pydantic.bindings import XmlParser
from pydantic import ValidationError
from aopkb import Data

# + tags=["parameters"]
upstream = ["get_xsd"]
product = None
aop_xml = None
version = None
# -

xml_file_path = aop_xml[str(version)]["sources"]["xml_file_path"]
 
parser = XmlParser()
try:
    with open(xml_file_path, "rb") as file:
        tmp = parser.parse(file, Data)
except ValidationError as e:
    print("Validation failed:")
    print(e.json())  # full structured error details
    raise
except Exception as e:
    print("Other error:", e)
    raise

tmp_json = tmp.model_dump_json(indent=4, exclude_none=True)

with open(product["json"], "w", encoding="utf-8") as f:
    f.write(tmp_json)
