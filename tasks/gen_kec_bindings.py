"""
gen_kec_bindings.py — Ploomber task: xsdata-pydantic bindings for the KEC consensus XML.

Generates aopkb/kec_consensus.py from resources/kec_consensus_schema.xsd (own target
namespace, the top-level <kec-consensus> container). aopkb/aop.py (from aop.xsd,
tasks/get_xsd.py) and aopkb/methods2aop.py (tasks/gen_assay_bindings.py) are not touched.

Same mechanism as gen_assay_bindings.py: xsdata names the module after the schema file and
`--package aopkb` would also rewrite aopkb/__init__.py (hand-maintained), so generation runs
in a temporary directory and only the one module is copied in.

Ploomber:
  upstream: none
  product:  {"nb", "kec_consensus"}
  params:   kec_xsd
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

# + tags=["parameters"]
upstream = None
product = None
kec_xsd: str = "resources/kec_consensus_schema.xsd"
# -

xsd = Path(kec_xsd).resolve()
with tempfile.TemporaryDirectory() as tmp:
    subprocess.run(["xsdata", "generate", str(xsd), "--output", "pydantic",
                    "--package", "aopkb"], check=True, cwd=tmp)
    out = Path(product["kec_consensus"])
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(tmp) / "aopkb" / f"{xsd.stem}.py", out)
print(f"wrote {product['kec_consensus']}")
