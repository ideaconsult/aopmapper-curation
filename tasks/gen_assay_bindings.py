"""
gen_assay_bindings.py — Ploomber task: xsdata-pydantic bindings for the assay XML.

Generates aopkb/methods2aop.py from resources/methods2aop_schema_proposal.xsd (the
Methods2AOP types plus the top-level <assays> container). aopkb/aop.py (from aop.xsd,
tasks/get_xsd.py) is not touched.

xsdata names the module after the schema file and `--package aopkb` would also rewrite
aopkb/__init__.py (hand-maintained), so generation runs in a temporary directory and only
the one module is copied in.

Ploomber:
  upstream: none
  product:  {"nb", "methods2aop"}
  params:   proposal_xsd
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

# + tags=["parameters"]
upstream = None
product = None
proposal_xsd: str = "resources/methods2aop_schema_proposal.xsd"
# -

xsd = Path(proposal_xsd).resolve()
with tempfile.TemporaryDirectory() as tmp:
    subprocess.run(["xsdata", "generate", str(xsd), "--output", "pydantic",
                    "--package", "aopkb"], check=True, cwd=tmp)
    out = Path(product["methods2aop"])
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(tmp) / "aopkb" / f"{xsd.stem}.py", out)
print(f"wrote {product['methods2aop']}")
