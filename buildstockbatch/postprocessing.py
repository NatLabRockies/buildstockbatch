# -*- coding: utf-8 -*-

"""
buildstockbatch.postprocessing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A module containing utility functions for postprocessing

:author: Noel Merket, Rajendra Adhikari
:copyright: (c) 2018 by The Alliance for Sustainable Energy
:license: BSD-3
"""
import boto3
import botocore.exceptions
import dask.bag as db
from dask.distributed import performance_report
import dask
import dask.dataframe as dd
from functools import partial
import gzip
import json
import logging
import math
import numpy as np
import pandas as pd
from pathlib import Path
import pyarrow as pa
from pyarrow import parquet
import random
import re
import tempfile
import time
import sys
from buildstockbatch.utils import get_annual_publishing_functions, get_data_dict_annual_ts_schema
import polars as pl
from collections import defaultdict


logger = logging.getLogger(__name__)

MAX_PARQUET_MEMORY = 1000  # maximum size (MB) of the parquet file in memory when combining multiple parquets
MAX_REPLACE_FILES = 9999  # maximum number of files to replace in s3 when using --replace_existing. We don't
# want to automatically delete large number of files using current API for two reasons:
# 1. It is inefficient
# 2. It is easy to make mistakes and wipe out a significant run
MAX_STR_LEN = 100000  # some strings such as eplusout_err and step_errors can be very long, truncate to this length


def read_data_point_out_json(fs, reporting_measures, filename):
    try:
        with fs.open(filename, "r") as f:
            d = json.load(f)
        if not d:
            return None
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    else:
        sim_out_report = "ReportSimulationOutput"
        if "SimulationOutputReport" in d:
            sim_out_report = "SimulationOutputReport"

        if sim_out_report not in d:
            d[sim_out_report] = {"applicable": False}
        for reporting_measure in reporting_measures:
            if reporting_measure not in d:
                d[reporting_measure] = {"applicable": False}
        return d


def to_camelcase(x):
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", x)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def flatten_datapoint_json(reporting_measures, d):
    new_d = {}
    cols_to_keep = {"ApplyUpgrade": ["upgrade_name", "applicable"]}
    for k1, k2s in cols_to_keep.items():
        for k2 in k2s:
            new_d[f"{k1}.{k2}"] = d.get(k1, {}).get(k2)

    # copy over all the key and values from BuildExistingModel
    col1 = "BuildExistingModel"
    for k, v in d.get(col1, {}).items():
        new_d[f"{col1}.{k}"] = v

    # if there is no units_represented key, default to 1
    # TODO @nmerket @rajeee is there a way to not apply this to Commercial jobs? It doesn't hurt, but it is weird for us
    units = int(new_d.get(f"{col1}.units_represented", 1))
    new_d[f"{col1}.units_represented"] = units
    sim_out_report = "SimulationOutputReport"
    if "ReportSimulationOutput" in d:
        sim_out_report = "ReportSimulationOutput"
    col2 = sim_out_report
    for k, v in d.get(col2, {}).items():
        new_d[f"{col2}.{k}"] = v

    # additional reporting measures
    if sim_out_report == "ReportSimulationOutput":
        reporting_measures += ["ReportUtilityBills"]
        reporting_measures += ["UpgradeCosts"]
    for col in reporting_measures:
        for k, v in d.get(col, {}).items():
            new_d[f"{col}.{k}"] = v

    new_d["building_id"] = new_d["BuildExistingModel.building_id"]
    del new_d["BuildExistingModel.building_id"]

    return new_d


def read_out_osw(fs, filename):
    try:
        with fs.open(filename, "r") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    else:
        out_d = {}
        keys_to_copy = ["started_at", "completed_at", "completed_status"]
        for key in keys_to_copy:
            out_d[key] = d.get(key, None)
        if "eplusout_err" in d and "EnergyPlus Terminated" in d["eplusout_err"]:
            out_d["eplusout_err"] = d["eplusout_err"][:MAX_STR_LEN]
        else:
            out_d["eplusout_err"] = ""
        step_errors = []
        for step in d.get("steps", []):
            measure_dir_name = step["measure_dir_name"]
            if measure_dir_name == "BuildExistingModel":
                out_d["building_id"] = step["arguments"]["building_id"]

            # Collect error messages from any failed steps.
            if result := step.get("result"):
                if result.get("step_result", "Success") != "Success":
                    step_errors.append({"measure_dir_name": measure_dir_name, "step_errors": result.get("step_errors")})

        if step_errors:
            out_d["step_failures"] = json.dumps(step_errors)[:MAX_STR_LEN]

        return out_d


def read_simulation_outputs(fs, reporting_measures, sim_dir, upgrade_id, building_id):
    """Read the simulation outputs and return as a dict

    :param fs: filesystem to read from
    :type fs: fsspec filesystem
    :param reporting_measures: a list of reporting measure to pull results from
    :type reporting_measures: list[str]
    :param sim_dir: path to simulation output directory
    :type sim_dir: str
    :param upgrade_id: id for upgrade, 0 for baseline, 1, 2...
    :type upgrade_id: int
    :param building_id: building id
    :type building_id: int
    :return: dpout [dict]
    """

    dpout = read_data_point_out_json(fs, reporting_measures, f"{sim_dir}/run/data_point_out.json")
    if dpout is None:
        dpout = {}
    else:
        dpout = flatten_datapoint_json(reporting_measures, dpout)
    out_osw = read_out_osw(fs, f"{sim_dir}/out.osw")
    if out_osw:
        dpout.update(out_osw)
    dpout["upgrade"] = upgrade_id
    dpout["building_id"] = building_id
    return dpout


def write_dataframe_as_parquet(df, fs, filename, schema=None):
    tbl = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    with fs.open(filename, "wb") as f:
        parquet.write_table(tbl, f)


def clean_up_results_df(df: pl.LazyFrame, cfg, schema: dict[str, pl.DataType], keep_upgrade_id=False):
    schema = schema.copy()  # avoid modifying the original schema
    cols_to_remove = (
        "build_existing_model.weight",
        "simulation_output_report.weight",
        "build_existing_model.workflow_json",
        "simulation_output_report.upgrade_name",
    )
    df = df.drop(cols_to_remove, strict=False)
    df = df.with_columns(
        pl.col("started_at", "completed_at").str.strptime(pl.Datetime(time_unit="ms"), "%Y%m%dT%H%M%SZ")
    )
    reference_scenarios = dict(
        [(str(i), x.get("reference_scenario", "")) for i, x in enumerate(cfg.get("upgrades", []), 1)]
    )
    df = df.with_columns(
        pl.col("upgrade").cast(pl.String).replace(reference_scenarios).alias("apply_upgrade.reference_scenario")
    )

    # standardize the column orders
    first_few_cols = [
        "building_id",
        "started_at",
        "completed_at",
        "completed_status",
        "apply_upgrade.applicable",
        "apply_upgrade.upgrade_name",
        "apply_upgrade.reference_scenario",
    ]
    current_schema = df.collect_schema()
    all_cols = current_schema.names()
    if keep_upgrade_id:
        first_few_cols.insert(1, "upgrade")
    if "job_id" in all_cols:
        first_few_cols.insert(2, "job_id")

    build_existing_model_cols = sorted([col for col in all_cols if col.startswith("build_existing_model")])
    sim_output_report_cols = sorted([col for col in all_cols if col.startswith("simulation_output_report")])
    report_sim_output_cols = sorted([col for col in all_cols if col.startswith("report_simulation_output")])
    upgrade_costs_cols = sorted([col for col in all_cols if col.startswith("upgrade_costs")])
    sorted_cols = (
        first_few_cols
        + build_existing_model_cols
        + sim_output_report_cols
        + report_sim_output_cols
        + upgrade_costs_cols
    )

    remaining_cols = sorted(set(all_cols).difference(sorted_cols))
    sorted_cols += remaining_cols

    # these columns are freshly created/updated so can't use passed schema
    for col in ["started_at", "completed_at", "apply_upgrade.reference_scenario"]:
        schema[col] = current_schema[col]
    if missing_cols := set(schema.keys()).difference(sorted_cols):
        string_missing_cols = [col for col in missing_cols if schema[col] == pl.String]
        other_missing_cols = [col for col in missing_cols if schema[col] != pl.String]
        logger.info(f"Missing columns {string_missing_cols} filled with ''")
        logger.info(f"Missing columns {other_missing_cols} filled with None")
        df = df.with_columns([pl.lit("").alias(col) for col in string_missing_cols])
        df = df.with_columns([pl.lit(None).alias(col) for col in other_missing_cols])
    df = df.with_columns([pl.col(c).cast(schema[c]) for c in sorted_cols])
    return df


def get_cols(fs, filepath):
    with fs.open(filepath, "rb") as f:
        schema = parquet.read_schema(f)
    return set(schema.names)


def read_results_json(fs, filename, all_cols=None):
    with fs.open(filename, "rb") as f1:
        with gzip.open(f1, "rt", encoding="utf-8") as f2:
            dpouts = json.load(f2)
    df = pd.DataFrame(dpouts)
    df["job_id"] = int(re.search(r"results_job(\d+)\.json\.gz", filename).group(1))
    if all_cols is not None:
        for missing_col in set(all_cols).difference(df.columns.values):
            df[missing_col] = None
    # Sorting is needed to ensure all dfs have same column order. Dask will fail otherwise.
    df = df.reindex(sorted(df.columns), axis=1).convert_dtypes(dtype_backend="pyarrow")
    return df


def get_schema_dict(fs, filename):
    sch = parquet.read_schema(filename)
    sch_dict = {name: type for name, type in zip(sch.names, sch.types)}
    return sch_dict


def merge_schema_dicts(dict1, dict2):
    new_dict = dict(dict1)
    for col, dtype2 in dict2.items():
        dtype1 = new_dict.get(col)
        if col not in new_dict or dtype1 == pa.null():
            new_dict[col] = dtype2
    return new_dict


def read_enduse_timeseries_parquet(fs, all_cols, src_path, bldg_id):
    src_filename = f"{src_path}/bldg{bldg_id:07}.parquet"
    with fs.open(src_filename, "rb") as f:
        df = pd.read_parquet(f, engine="pyarrow")
    df["building_id"] = bldg_id
    df = df.reindex(columns=all_cols)  # fills missing cols with nan
    df.set_index("building_id", inplace=True)
    return df


def concat_and_normalize(fs, all_cols, src_path, dst_path, partition_columns, indx, bldg_ids, partition_vals):
    dfs = []
    for bldg_id in sorted(bldg_ids):
        df = read_enduse_timeseries_parquet(fs, all_cols, src_path, bldg_id)
        dfs.append(df)
    df = pd.concat(dfs)
    del dfs

    dst_filepath = dst_path
    for col, val in zip(partition_columns, partition_vals):
        folder_name = f"{col}={val}"
        dst_filepath = f"{dst_filepath}/{folder_name}"

    fs.makedirs(dst_filepath, exist_ok=True)
    dst_filename = f"{dst_filepath}/group{indx}.parquet"
    with fs.open(dst_filename, "wb") as f:
        df.to_parquet(f, index=True)
    return len(bldg_ids)


def get_null_cols(df):
    sch = pa.Schema.from_pandas(df)
    null_cols = []
    for col, dtype in zip(sch.names, sch.types):
        if dtype == pa.null():
            null_cols.append(col)
    return null_cols


def correct_schema(cur_schema_dict, df):
    sch = pa.Schema.from_pandas(df)
    sch_dict = {name: type for name, type in zip(sch.names, sch.types)}
    unresolved = []
    for col, dtype in sch_dict.items():
        if dtype == pa.null():
            if col in cur_schema_dict:
                indx = sch.get_field_index(col)
                sch = sch.set(indx, pa.field(col, cur_schema_dict.get(col)))
            else:
                unresolved.append(col)
    return sch, unresolved


def split_into_groups(total_size, max_group_size):
    """
    Splits an integer into sum of integers (returned as an array) each not exceeding max_group_size
    e.g. split_into_groups(10, 3) = [3, 3, 2, 2]
    """
    if total_size == 0:
        return []
    total_groups = math.ceil(total_size / max_group_size)
    min_elements_per_group = math.floor(total_size / total_groups)
    split_array = [min_elements_per_group] * total_groups
    remainder = total_size - min_elements_per_group * total_groups
    assert 0 <= remainder < len(split_array)
    for i in range(remainder):
        split_array[i] += 1
    return split_array


def get_partitioned_bldg_groups(partition_df, partition_columns, files_per_partition):
    """
    Returns intelligent grouping of building_ids by partition columns.
    1. Group the building_ids by partition columns. For each group, say (CO, Jefferson), we have a list of building
       ids. The total number of such groups is ngroups
    2. Concatenate those list to get bldg_id_list, which will have all the bldg_ids but ordered such that that
       buildings belonging to the same group are close together.
    3. Split the list of building in each group in 1 to multiple subgroups so that total number of buildings
       in each subgroup is less than or equal to files_per_partition. This will give the bldg_id_groups (list of
       list) used to read the dataframe. The buildings within the inner list will be concatenated.
       len(bldg_id_groups) is equal to number of such concatenation, and eventually, number of output parquet files.
    """
    total_building = len(partition_df)
    if partition_columns:
        bldg_id_list_df = partition_df.reset_index().groupby(partition_columns)["building_id"].apply(list)
        ngroups = len(bldg_id_list_df)
        bldg_id_list = bldg_id_list_df.sum()
        nfiles_in_each_group = [nfiles for nfiles in bldg_id_list_df.map(lambda x: len(x))]
        files_groups = [split_into_groups(n, files_per_partition) for n in nfiles_in_each_group]
        flat_groups = [n for group in files_groups for n in group]  # flatten list of list into a list (maintain order)
    else:
        # no partitioning by a column. Just put buildings into groups of files_per_partition
        ngroups = 1
        bldg_id_list = list(partition_df.index)
        flat_groups = split_into_groups(total_building, files_per_partition)

    cum_files_count = np.cumsum(flat_groups)
    assert cum_files_count[-1] == total_building
    cur_index = 0
    bldg_id_groups = []
    for indx in cum_files_count:
        bldg_id_groups.append(bldg_id_list[cur_index:indx])
        cur_index = indx

    return bldg_id_groups, bldg_id_list, ngroups


def get_upgrade_list(cfg):
    upgrade_start = 1 if cfg["baseline"].get("skip_sims", False) else 0
    upgrade_end = len(cfg.get("upgrades", [])) + 1
    return list(range(upgrade_start, upgrade_end))


def write_metadata_files(fs, parquet_root_dir, partition_columns):
    df = dd.read_parquet(parquet_root_dir, filesystem=fs)
    sch = pa.Schema.from_pandas(df._meta_nonempty)
    parquet.write_metadata(sch, f"{parquet_root_dir}/_common_metadata", filesystem=fs)
    logger.info(f"Written _common_metadata to {parquet_root_dir}")


def combine_results(fs, results_dir, cfg):
    """Combine the results of the batch simulations.

    :param fs: fsspec filesystem (currently supports local, s3, gcs)
    :type fs: fsspec filesystem
    :param results_dir: directory where results are stored and written
    :type results_dir: str
    :param cfg: project configuration (contents of yaml file)
    :type cfg: dict
    """
    sim_output_dir = f"{results_dir}/simulation_output"
    results_csvs_dir = f"{results_dir}/results_csvs"
    parquet_dir = f"{results_dir}/parquet"
    results_csvs_pub_dir = None
    parquet_pub_dir = f"{parquet_dir}/pub_annual"
    publish_baseline, publish_upgrade = None, None  # metadata transform
    dirs = [results_csvs_dir]
    if cfg.get("postprocessing", {}).get("publish_annual_results", False):
        results_csvs_pub_dir = f"{results_dir}/results_csvs_pub"
        dirs.append(results_csvs_pub_dir)
        publish_baseline, publish_upgrade = get_annual_publishing_functions(cfg)

    # create the postprocessing results directories
    for dr in dirs:
        if not fs.exists(dr):
            fs.makedirs(dr, exist_ok=True)

    annual_schema, ts_schema = get_data_dict_annual_ts_schema(cfg)
    # Results "CSV"
    annual_results_files = fs.glob(f"{sim_output_dir}/annual/*/*.parquet")
    if not annual_results_files:
        raise ValueError(f"No simulation results found to post-process in {sim_output_dir}")

    baseline_failed_bldgs = set()
    if cfg.get("postprocessing", {}).get("publish_annual_results", False):
        logger.info("Collecting all the failed simulations buildings")

        def get_failed_bldg_ids(filename):
            df = pl.scan_parquet(filename)
            failed_df = (
                df.filter(~pl.col("completed_status").is_in(["Success", "Invalid"])).select("building_id").collect()
            )
            if len(failed_df) == 0:
                return None
            failed_bldg = failed_df["building_id"].item()
            return failed_bldg

        baseline_files = fs.glob(f"{sim_output_dir}/annual/up00/*.parquet")
        baseline_failed_bldgs = db.from_sequence(baseline_files).map(get_failed_bldg_ids).compute()
        baseline_failed_bldgs = {bldg for bldg in baseline_failed_bldgs if bldg is not None}
        logger.info(
            f"Found {len(baseline_failed_bldgs)} failed simulations in baseline."
            f"Replacing upgrade failures with baseline (i.e. treating them as upgrade not applied)."
            f"These are the failed building ids: {', '.join(f'{k}' for k in baseline_failed_bldgs)}"
        )

    upgrade_list = get_upgrade_list(cfg)
    logger.info(f"Will postprocess the following upgrades {upgrade_list}")
    base_df_lazy = pl.LazyFrame()
    for upgrade_id in upgrade_list:
        logger.info(f"Processing upgrade {upgrade_id}. ")
        df = pl.read_parquet(
            f"{sim_output_dir}/annual/up{upgrade_id:02d}/*.parquet", missing_columns="insert", schema=annual_schema
        )  # use eager read to avoid hitting file system multiple times
        # find the length of the df
        df_len = df.select(pl.len()).item()
        logger.info(f"Found {df_len} rows for upgrade {upgrade_id}.")
        lazy_df = clean_up_results_df(df.lazy(), cfg, keep_upgrade_id=True, schema=annual_schema)
        df = lazy_df.sort("building_id").collect()
        if (publish_baseline is not None) and (publish_upgrade is not None):
            if upgrade_id == 0:
                pub_df_lazy: pl.LazyFrame = publish_baseline(df.lazy())
                base_df_lazy = pub_df_lazy
            else:
                pub_df_lazy = publish_upgrade(baseline_failed_bldgs, base_df_lazy, df.lazy(), upgrade_num=upgrade_id)
            pub_df_len = pub_df_lazy.select(pl.len()).collect().item()
            logger.info(f"Got {pub_df_len} pub_df rows for upgrade {upgrade_id}.")
            csv_filename = f"{results_csvs_pub_dir}/results_up{upgrade_id:02d}.csv.gz"
            logger.info(f"Writing {csv_filename}")
            with fs.open(csv_filename, "wb") as f:
                with gzip.open(f, "wb") as gf:  # Use wb here because polars writes in binary mode to file
                    pub_df_lazy.sink_csv(gf, line_terminator="\n")

            dir = f"{parquet_pub_dir}/upgrade={upgrade_id}"
            pub_df_lazy = pub_df_lazy.drop("upgrade")
            fs.makedirs(dir, exist_ok=True)
            parquet_filename = f"{dir}/results_up{upgrade_id:02d}.parquet"
            logger.info(f"Writing {parquet_filename}")
            pub_df_lazy.sink_parquet(parquet_filename, statistics=False)

        # Write CSV
        csv_filename = f"{results_csvs_dir}/results_up{upgrade_id:02d}.csv.gz"
        logger.info(f"Writing {csv_filename}")
        with fs.open(csv_filename, "wb") as f:
            with gzip.open(f, "wb") as gf:
                df.write_csv(gf, line_terminator="\n")

        # Write Parquet
        if upgrade_id == 0:
            results_parquet_dir = f"{parquet_dir}/baseline"
        else:
            results_parquet_dir = f"{parquet_dir}/upgrades/upgrade={upgrade_id}"
        df = df.drop("upgrade")  # upgrade column is created using hive partitioning
        fs.makedirs(results_parquet_dir, exist_ok=True)
        parquet_filename = f"{results_parquet_dir}/results_up{upgrade_id:02d}.parquet"
        logger.info(f"Writing {parquet_filename}")
        df.write_parquet(parquet_filename, statistics=False)

    logger.info("All aggregation completed. ")


def upload_results(
    aws_conf, output_dir, results_dir, buildstock_csv_filename, continue_upload=False, replace_existing=False
):
    logger.info("Uploading the parquet files to s3")

    output_folder_name = Path(output_dir).name
    parquet_dir = Path(results_dir).joinpath("parquet")
    ts_dir = parquet_dir / "timeseries"
    if not parquet_dir.is_dir():
        logger.error(f"{parquet_dir} does not exist. Please make sure postprocessing has been done.")
        raise FileNotFoundError(parquet_dir)

    all_files = []
    for file in parquet_dir.rglob("*.parquet"):
        all_files.append(file.relative_to(parquet_dir))
    for file in [*ts_dir.glob("_common_metadata"), *ts_dir.glob("_metadata")]:
        all_files.append(file.relative_to(parquet_dir))
    logger.info(f"{len(all_files)} parquet files will be uploaded.")
    s3_prefix = aws_conf.get("s3", {}).get("prefix", "").rstrip("/")
    s3_bucket = aws_conf.get("s3", {}).get("bucket", None)
    if not (s3_prefix and s3_bucket):
        logger.error("YAML file missing postprocessing:aws:s3:prefix and/or bucket entry.")
        return
    s3_prefix_output = s3_prefix + "/" + output_folder_name + "/"

    s3 = boto3.resource("s3")
    bucket = s3.Bucket(s3_bucket)
    existing_files = {f.key.removeprefix(s3_prefix_output) for f in bucket.objects.filter(Prefix=s3_prefix_output)}

    if len(existing_files) > 0:
        logger.info(f"There are already {len(existing_files)} files in the s3 folder {s3_bucket}/{s3_prefix_output}.")
        if not continue_upload and not replace_existing:
            raise FileExistsError("Either use --continue_upload or --replace_existing or delete files from s3")
        if replace_existing and len(existing_files) > MAX_REPLACE_FILES:
            raise FileExistsError(
                f"{len(existing_files)} files exist in s3://{s3_bucket}/{s3_prefix_output} folder."
                f"Can't replace more than {MAX_REPLACE_FILES} files."
            )
        if replace_existing:
            bucket.objects.filter(Prefix=s3_prefix_output).delete()
            logger.info(f"Deleted {len(existing_files)} files from s3://{s3_bucket}/{s3_prefix_output} folder.")
            logger.info(f"Now uploading all {len(all_files)} files.")
        else:
            all_files = [file for file in all_files if str(file) not in existing_files]
            logger.info(f"Only uploading the rest of the {len(all_files)} files")

    def upload_file(filepath, s3key=None):
        full_path = filepath if filepath.is_absolute() else parquet_dir.joinpath(filepath)
        s3 = boto3.resource("s3")
        bucket = s3.Bucket(s3_bucket)
        if s3key is None:
            s3key = Path(s3_prefix_output).joinpath(filepath).as_posix()
        bucket.upload_file(str(full_path), str(s3key))

    tasks = list(map(dask.delayed(upload_file), all_files))
    if buildstock_csv_filename is not None:
        buildstock_csv_filepath = Path(buildstock_csv_filename)
        if f"buildstock_csv/{buildstock_csv_filepath.name}" in existing_files:
            logger.info("Buildstock CSV already exists in s3.")
        elif buildstock_csv_filepath.exists():
            tasks.append(
                dask.delayed(upload_file)(
                    buildstock_csv_filepath,
                    f"{s3_prefix_output}buildstock_csv/{buildstock_csv_filepath.name}",
                )
            )
        else:
            logger.warning(f"{buildstock_csv_filename} doesn't exist, can't upload.")
    dask.compute(tasks)
    logger.info(f"Upload to S3 completed. The files are uploaded to: {s3_bucket}/{s3_prefix_output}")
    return s3_bucket, s3_prefix_output


def create_athena_tables(aws_conf, tbl_prefix, s3_bucket, s3_prefix):
    logger.info("Creating Athena tables using glue crawler")

    region_name = aws_conf.get("region_name", "us-west-2")
    db_name = aws_conf.get("athena", {}).get("database_name", None)
    role = aws_conf.get("athena", {}).get("glue_service_role", "service-role/AWSGlueServiceRole-default")
    max_crawling_time = aws_conf.get("athena", {}).get("max_crawling_time", 600)
    assert db_name, "athena:database_name not supplied"

    # Check that there are files in the s3 bucket before creating and running glue crawler
    s3 = boto3.resource("s3")
    bucket = s3.Bucket(s3_bucket)
    s3_path = f"s3://{s3_bucket}/{s3_prefix}"
    n_existing_files = len(list(bucket.objects.filter(Prefix=s3_prefix)))
    if n_existing_files == 0:
        logger.warning(f"There are no files in {s3_path}, Athena tables will not be created as intended")
        return

    glueClient = boto3.client("glue", region_name=region_name)
    crawlTarget = {
        "S3Targets": [{"Path": s3_path, "Exclusions": ["**_metadata", "**_common_metadata"], "SampleSize": 2}]
    }
    crawler_name = db_name + "_" + tbl_prefix
    tbl_prefix = tbl_prefix + "_"

    def create_crawler():
        glueClient.create_crawler(
            Name=crawler_name,
            Role=role,
            Targets=crawlTarget,
            DatabaseName=db_name,
            TablePrefix=tbl_prefix,
        )

    try:
        create_crawler()
    except glueClient.exceptions.AlreadyExistsException:
        logger.info(f"Deleting existing crawler: {crawler_name}. And creating new one.")
        glueClient.delete_crawler(Name=crawler_name)
        time.sleep(1)  # A small delay after deleting is required to prevent AlreadyExistsException again
        create_crawler()

    try:
        existing_tables = [x["Name"] for x in glueClient.get_tables(DatabaseName=db_name)["TableList"]]
    except glueClient.exceptions.EntityNotFoundException:
        existing_tables = []

    to_be_deleted_tables = [x for x in existing_tables if x.startswith(tbl_prefix)]
    if to_be_deleted_tables:
        logger.info(f"Deleting existing tables in db {db_name}: {to_be_deleted_tables}. And creating new ones.")
        glueClient.batch_delete_table(DatabaseName=db_name, TablesToDelete=to_be_deleted_tables)

    glueClient.start_crawler(Name=crawler_name)
    logger.info("Crawler started")
    start_time = time.time()
    elapsed_time = 0
    while elapsed_time < (3 * max_crawling_time):
        time.sleep(30)
        elapsed_time = time.time() - start_time
        crawler = glueClient.get_crawler(Name=crawler_name)["Crawler"]
        crawler_state = crawler["State"]
        logger.info(f"Crawler is {crawler_state}")
        if crawler_state == "RUNNING":
            if elapsed_time > max_crawling_time:
                logger.error("Crawler is taking too long. Aborting ...")
                glueClient.stop_crawler(Name=crawler_name)
        elif crawler_state == "STOPPING":
            logger.debug("Waiting for crawler to stop")
        else:
            assert crawler_state == "READY"
            metrics = glueClient.get_crawler_metrics(CrawlerNameList=[crawler_name])["CrawlerMetricsList"][0]
            logger.info(f"Crawler has completed running. It is {crawler_state}.")
            logger.info(
                f"TablesCreated: {metrics['TablesCreated']} "
                f"TablesUpdated: {metrics['TablesUpdated']} "
                f"TablesDeleted: {metrics['TablesDeleted']} "
            )
            break

    logger.info(f"Crawl {crawler['LastCrawl']['Status']}")
    logger.info(f"Deleting crawler {crawler_name}")
    try:
        glueClient.delete_crawler(Name=crawler_name)
    except botocore.exceptions.ClientError as error:
        logger.error(f"Could not delete crawler {crawler_name}. Please delete it manually from the AWS console.")
        raise error
