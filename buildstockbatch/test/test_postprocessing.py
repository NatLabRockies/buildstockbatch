from fsspec.implementations.local import LocalFileSystem
import gzip
import json
import logging
import os
import pathlib
import re
import tarfile
import pytest
import shutil
import sys
from unittest.mock import patch, MagicMock

from buildstockbatch import postprocessing
from buildstockbatch.base import BuildStockBatchBase
from buildstockbatch.utils import get_project_configuration, read_csv

postprocessing.performance_report = MagicMock()


def test_report_additional_results_csv_columns(basic_residential_project_file):
    reporting_measures = ["ReportingMeasure1", "ReportingMeasure2"]
    project_filename, results_dir = basic_residential_project_file(
        raw=True, update_args={"reporting_measures": reporting_measures}
    )

    fs = LocalFileSystem()

    results_dir = pathlib.Path(results_dir)
    sim_out_dir = results_dir / "simulation_output"

    dpouts2 = []
    for filename in sim_out_dir.rglob("data_point_out.json"):
        with filename.open("rt", encoding="utf-8") as f:
            dpout = json.load(f)

        sim_dir = str(filename.parent.parent)
        upgrade_id = int(re.search(r"up(\d+)", sim_dir).group(1))
        building_id = int(re.search(r"bldg(\d+)", sim_dir).group(1))

        if dpout:  # Only for successful sims
            dpout["ReportingMeasure1"] = {"column_1": 1, "column_2": 2}
            dpout["ReportingMeasure2"] = {"column_3": 3, "column_4": 4}

        with filename.open("wt", encoding="utf-8") as f:
            json.dump(dpout, f)

        dpouts2.append(postprocessing.read_simulation_outputs(fs, reporting_measures, sim_dir, upgrade_id, building_id))

    with gzip.open(sim_out_dir / "results_job0.json.gz", "wt", encoding="utf-8") as f:
        json.dump(dpouts2, f)

    cfg = get_project_configuration(project_filename)

    postprocessing.combine_results(fs, results_dir, cfg, do_timeseries=False)

    for upgrade_id in (0, 1):
        df = read_csv(str(results_dir / "results_csvs" / f"results_up{upgrade_id:02d}.csv.gz"))
        assert "Measure Failed" in df[df["building_id"] == 3]["step_failures"].iloc[0]
        assert "EnergyPlus Failed with Error: Building ID is 3" in df[df["building_id"] == 3]["eplusout_err"].iloc[0]
        df = df[df["building_id"] != 3]
        assert (df["reporting_measure1.column_1"] == 1).all()
        assert (df["reporting_measure1.column_2"] == 2).all()
        assert (df["reporting_measure2.column_3"] == 3).all()
        assert (df["reporting_measure2.column_4"] == 4).all()


def test_empty_results_assertion(basic_residential_project_file, capsys):
    project_filename, results_dir = basic_residential_project_file({})

    fs = LocalFileSystem()
    results_dir = pathlib.Path(results_dir)
    sim_out_dir = results_dir / "simulation_output"
    shutil.rmtree(sim_out_dir)  # no results
    cfg = get_project_configuration(project_filename)

    with pytest.raises(ValueError, match=r"No simulation results found to post-process"):
        assert postprocessing.combine_results(fs, results_dir, cfg, do_timeseries=False)


def test_large_parquet_combine(basic_residential_project_file):
    # Test a simulated scenario where the individual timeseries parquet are larger than the max memory per partition
    # allocated for the parquet file combining.

    project_filename, results_dir = basic_residential_project_file()

    with patch.object(BuildStockBatchBase, "weather_dir", None), patch.object(
        BuildStockBatchBase, "get_dask_client"
    ), patch.object(BuildStockBatchBase, "results_dir", results_dir), patch.object(
        postprocessing, "MAX_PARQUET_MEMORY", 1
    ):  # set the max memory to just 1MB
        bsb = BuildStockBatchBase(project_filename)
        bsb.process_results()  # this would raise exception if the postprocessing could not handle the situation


@pytest.mark.parametrize("keep_individual_timeseries", [True, False])
def test_keep_individual_timeseries(keep_individual_timeseries, basic_residential_project_file, mocker):
    project_filename, results_dir = basic_residential_project_file(
        {"postprocessing": {"keep_individual_timeseries": keep_individual_timeseries}}
    )

    mocker.patch.object(BuildStockBatchBase, "weather_dir", None)
    mocker.patch.object(BuildStockBatchBase, "get_dask_client")
    mocker.patch.object(BuildStockBatchBase, "results_dir", results_dir)
    bsb = BuildStockBatchBase(project_filename)
    bsb.process_results()

    results_path = pathlib.Path(results_dir)
    simout_path = results_path / "simulation_output"
    assert len(list(simout_path.glob("results_job*.json.gz"))) == 0

    ts_path = simout_path / "timeseries"
    assert ts_path.exists() == keep_individual_timeseries


def test_upgrade_missing_ts(basic_residential_project_file, mocker, caplog):
    caplog.set_level(logging.WARNING, logger="buildstockbatch.postprocessing")

    project_filename, results_dir = basic_residential_project_file()
    results_path = pathlib.Path(results_dir)
    for filename in (results_path / "simulation_output" / "timeseries" / "up01").glob("*.parquet"):
        os.remove(filename)

    mocker.patch.object(BuildStockBatchBase, "weather_dir", None)
    mocker.patch.object(BuildStockBatchBase, "get_dask_client")
    mocker.patch.object(BuildStockBatchBase, "results_dir", results_dir)
    bsb = BuildStockBatchBase(project_filename)
    bsb.process_results()

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelname == "WARNING"
    assert record.message == "There are no timeseries files for upgrade1."


def test_publish_annual_results(basic_residential_project_file, mocker):
    """Test that when publish_annual_results is True, the expected folders and files are created."""
    # Create project with schema v0.6 and publish_annual_results set to True
    project_filename, results_dir = basic_residential_project_file(
        {"schema_version": "0.6", "postprocessing": {"publish_annual_results": True}}
    )

    # Mock necessary objects for testing
    mocker.patch.object(BuildStockBatchBase, "weather_dir", None)
    mocker.patch.object(BuildStockBatchBase, "get_dask_client")
    mocker.patch.object(BuildStockBatchBase, "results_dir", results_dir)

    # Create a simple mock module and add it to sys.modules
    class MockResstockpostproc:
        @staticmethod
        def publish_baseline_annual_results(failed_bldgs, base):
            # Simply rename columns with pub_ prefix
            cols = base.collect_schema().names()
            rename_map = {col: f"pub_{col}" for col in cols}
            return base.rename(rename_map)

        @staticmethod
        def publish_upgrade_annual_results(failed_bldgs, base, upgrade, upgrade_num):
            # Simply rename columns with pub_ prefix
            cols = upgrade.collect_schema().names()
            rename_map = {col: f"pub_{col}" for col in cols}
            return upgrade.rename(rename_map)

    # Add the mock module to sys.modules
    original_resstockpostproc = sys.modules.get("resstockpostproc")
    sys.modules["resstockpostproc"] = MockResstockpostproc
    try:
        # Create and run the BuildStockBatchBase instance
        bsb = BuildStockBatchBase(project_filename)
        bsb.process_results()

        # Check that the expected directories and files exist
        results_path = pathlib.Path(results_dir)
    finally:
        # Restore the original state of sys.modules
        if original_resstockpostproc is not None:
            sys.modules["resstockpostproc"] = original_resstockpostproc
        else:
            del sys.modules["resstockpostproc"]
    # Check for results_csvs_pub folder with CSV files
    results_csvs_pub_path = results_path / "results_csvs_pub"
    assert results_csvs_pub_path.exists(), "results_csvs_pub folder should exist"
    assert len(list(results_csvs_pub_path.glob("*.csv.gz"))) > 0, "results_csvs_pub should contain CSV files"

    # Check for pub_annual folder inside parquet_dir with files
    parquet_dir = results_path / "parquet"
    pub_annual_path = parquet_dir / "pub_annual"
    assert pub_annual_path.exists(), "pub_annual folder should exist inside parquet_dir"
    assert len(list(pub_annual_path.rglob("*.parquet"))) > 0, "pub_annual should contain parquet files"

    # Verify the structure - there should be upgrade=X folders inside pub_annual
    upgrade_folders = list(pub_annual_path.glob("upgrade=*"))
    assert len(upgrade_folders) > 0, "pub_annual should contain upgrade folders"

    # Check each upgrade folder has the expected parquet files
    for upgrade_folder in upgrade_folders:
        upgrade_id = int(upgrade_folder.name.split("=")[1])
        expected_file = upgrade_folder / f"results_up{upgrade_id:02d}.parquet"
        assert expected_file.exists(), f"Expected parquet file missing for {upgrade_folder.name}"
