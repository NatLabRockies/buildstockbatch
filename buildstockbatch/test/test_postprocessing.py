from fsspec.implementations.local import LocalFileSystem
import gzip
import json
import logging
import os
import pathlib
import re
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
        assert (
            "EnergyPlus Terminated with Error: Building ID is 3" in df[df["building_id"] == 3]["eplusout_err"].iloc[0]
        )
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
        def publish_baseline_annual_results(base):
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


@pytest.mark.parametrize(
    "scenario",
    [
        {"replace_existing": True, "continue_upload": False, "should_raise_error": False},
        {"replace_existing": False, "continue_upload": False, "should_raise_error": True},
        {"replace_existing": False, "continue_upload": True, "should_raise_error": False},
    ],
)
def test_replace_existing(basic_residential_project_file, mocker, scenario):
    """Test the replace_existing functionality."""
    # Set up a mock S3 environment
    mocker.patch("s3fs.S3FileSystem", new=mocker.MagicMock())
    mocker.patch("boto3.resource", new=mocker.MagicMock())

    # Create a basic residential project file
    project_filename, results_dir = basic_residential_project_file(
        {"postprocessing": {"aws": {"s3": {"bucket": "dummy", "prefix": "dummy"}}}}
    )

    # Mock the necessary objects
    mocker.patch.object(BuildStockBatchBase, "weather_dir", None)
    mocker.patch.object(BuildStockBatchBase, "get_dask_client")
    mocker.patch.object(BuildStockBatchBase, "results_dir", results_dir)

    # Create an instance of BuildStockBatchBase
    bsb = BuildStockBatchBase(project_filename)

    # Create some dummy result files
    results_path = pathlib.Path(results_dir)
    sim_out_dir = results_path / "simulation_output"
    sim_out_dir.mkdir(parents=True, exist_ok=True)
    (sim_out_dir / "results_job0.json.gz").touch()
    parquet_dir = results_path / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)

    # Mock the S3 filesystem and existing files
    mock_s3_resource = mocker.patch("boto3.resource").return_value
    mock_bucket = mock_s3_resource.Bucket.return_value
    mock_objects_filter_return_value = mocker.MagicMock()
    mock_objects_filter_return_value.delete.return_value = None
    mock_bucket.objects.filter.return_value = mock_objects_filter_return_value
    mock_objects_filter_return_value.__iter__.return_value = [mocker.MagicMock(key="dummy/path/results.csv.gz")]

    if scenario["should_raise_error"]:
        with pytest.raises(FileExistsError):
            bsb.upload_results(
                aws_conf=bsb.cfg.get("postprocessing", {}).get("aws", {}),
                output_dir=results_dir,
                results_dir=results_dir,
                buildstock_csv_filename=None,
                replace_existing=scenario["replace_existing"],
                continue_upload=scenario["continue_upload"],
            )
    else:
        bsb.upload_results(
            aws_conf=bsb.cfg.get("postprocessing", {}).get("aws", {}),
            output_dir=results_dir,
            results_dir=results_dir,
            buildstock_csv_filename=None,
            replace_existing=scenario["replace_existing"],
            continue_upload=scenario["continue_upload"],
        )
        if scenario["replace_existing"]:
            # Assert that the mock S3's rm method was called, which means files are being replaced
            mock_bucket.objects.filter.return_value.delete.assert_called()
        else:
            # Assert that the mock S3's rm method was not called
            mock_bucket.objects.filter.return_value.delete.assert_not_called()


def test_trim_step_errors():
    from buildstockbatch.postprocessing import trim_step_errors

    # Python traceback
    python_tb = (
        "Output:\nTraceback (most recent call last):\n"
        '  File "/app/.venv/lib/click/core.py", line 10, in main\n'
        "    cli()\n"
        '  File "/long/path/ochre/utils/hpxml.py", line 837, in parse_hvac\n'
        '    name = hvac["HeatPumpType"]\n'
        "KeyError: 'HeatingSystemType'"
    )
    result = trim_step_errors([python_tb])
    assert len(result) == 1
    assert "KeyError: 'HeatingSystemType'" in result[0]
    assert 'File "ochre/utils/hpxml.py"' in result[0]  # path shortened
    assert "Traceback" not in result[0]
    assert ".venv" not in result[0]  # skips venv frames
    assert "/long/path" not in result[0]  # absolute path shortened

    # Ruby trace: first line + last frame (path shortened to last 3 components)
    ruby_err = (
        "Measure Failed with Error: X\n"
        "/long/path/measures/ResStockArguments/measure.rb:423:in `run'\n"
        "/long/path/measures/BuildExistingModel/measure.rb:333:in `run'"
    )
    result = trim_step_errors([ruby_err])
    assert "Measure Failed with Error: X" in result[0]
    assert "BuildExistingModel/measure.rb:333" in result[0]
    assert "/long/path/measures" not in result[0]  # absolute path is shortened

    # Short error stays as-is
    assert trim_step_errors(["OCHRE simulation failed"]) == ["OCHRE simulation failed"]

    # Empty list
    assert trim_step_errors([]) == []

    # Two errors differing only by building ID should be identical after normalization
    python_tb_template = (
        "Output:\nTraceback (most recent call last):\n"
        '  File "/path/up00/bldg{bldg}/run/ochre/utils/base.py", line 41, in load_csv\n'
        "    return pd.read_csv(file_name, **kwargs)\n"
        "FileNotFoundError: [Errno 2] No such file or directory: "
        "'/path/up00/bldg{bldg}/run/in.schedules.csv'"
    )
    err1 = python_tb_template.format(bldg="3727956")
    err2 = python_tb_template.format(bldg="2543739")
    result = trim_step_errors([err1, err2])
    assert result[0] == result[1]  # identical after normalization
    assert "bldg{ID}" in result[0]
    assert "up{NN}" in result[0]
    assert "3727956" not in result[0]
    assert "2543739" not in result[1]
