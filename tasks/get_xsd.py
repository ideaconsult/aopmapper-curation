import requests 
import subprocess
import os.path

# + tags=["parameters"]
upstream = None
product = None
aop_xml = None
version = None
# -

xsd_url = aop_xml[str(version)]["sources"]["xsd_url"]

schema_file = product["xsd"]
response = requests.get(xsd_url)
if os.path.isfile(schema_file):
    print(schema_file)
else:
    with open(schema_file, "wb") as file:
        file.write(response.content)

# https://xsdata.readthedocs.io/en/latest/codegen/intro/
command = ["xsdata", "generate", schema_file,
           "--output", "pydantic",
           "--package", "aopkb"]

try:
    # Run the xsdata command
    subprocess.run(command, check=True)
    print("Pydantic models generated successfully!")
except subprocess.CalledProcessError as e:
    print(f"Error occurred while generating models: {e}")