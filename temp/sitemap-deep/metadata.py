"""Build the actual OQA sitemap from this test database as its metadata admin."""

import json
from pathlib import Path
import sys

if env.cr.dbname != "oqa_sitemap_deep":
    raise RuntimeError("Expected the isolated sitemap sandbox.")

root = Path("C:/Users/maido/odoo-test")
sys.path.insert(0, str(root))
from oqa import sitemap


def read_metadata(model, method, args=None, kwargs=None):
    kwargs = dict(kwargs or {})
    records = env[model].sudo().with_context(**kwargs.pop("context", {}))
    if method == "read":
        return records.browse(args[0]).read(**kwargs)
    if method in {"search_read", "get_views"}:
        return getattr(records, method)(*(args or []), **kwargs)
    raise ValueError(f"Unexpected metadata operation: {method}")


data = sitemap.build(read_metadata, "Metadata admin", "19.0")
path = root / "temp/sitemap-deep/admin-sitemap.json"
path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
