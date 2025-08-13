# %%
import json
import polars as pl
from buildstockbatch import postprocessing
from buildstockbatch.utils import get_data_dict_schema
import pathlib

with open("results_job0.json", "r") as f:
    dpouts = json.load(f)

completed_jobs = []
for dpout in dpouts:
    dpout = {postprocessing.to_camelcase(key): value for key, value in dpout.items()}
    dpout["job_id"] = 0  # Used by downstream code. For local run, job_id is always zero.
    upgrade_id = dpout["upgrade"]
    building_id = dpout["building_id"]
    dp_df = pl.from_dict(dpout)
    full_schema = get_data_dict_schema("residential", dp_df.columns)
    dp_df = dp_df.with_columns([pl.col(col).cast(dtype) for col, dtype in full_schema.items()])
    pathlib.Path(f"../annual/up{upgrade_id:02d}").mkdir(parents=True, exist_ok=True)
    dp_df.write_parquet(f"../annual/up{upgrade_id:02d}/bldg{building_id:07d}.parquet")
    print(f"Wrote annual/up{upgrade_id:02d}/bldg{building_id:07d}.parquet")
    completed_jobs.append((upgrade_id, building_id))

with open("../completed_jobs.json", "w") as f:
    json.dump(completed_jobs, f)
