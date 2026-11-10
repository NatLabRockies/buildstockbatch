from fsspec.implementations.local import LocalFileSystem
import gzip
import json
import logging
import os
import pathlib
import re
import pytest
import shutil
from unittest.mock import patch, MagicMock

from buildstockbatch import postprocessing
from buildstockbatch.base import BuildStockBatchBase
from buildstockbatch.exc import ValidationError
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
    """publish_annual_results invokes the resstockpostproc pipeline with the files bsb wrote."""
    project_filename, results_dir = basic_residential_project_file(
        {
            "schema_version": "0.6",
            "postprocessing": {"publish_annual_results": True, "publication": {"seed": 99}},
        }
    )

    mocker.patch.object(BuildStockBatchBase, "weather_dir", None)
    mocker.patch.object(BuildStockBatchBase, "get_dask_client")
    mocker.patch.object(BuildStockBatchBase, "results_dir", results_dir)
    mock_pipeline = mocker.MagicMock()
    mocker.patch.object(postprocessing, "get_annual_publishing_pipeline", return_value=mock_pipeline)
    # The fixture project uses the residential_quota sampler; pretend the results
    # have all the columns the quota publication path needs
    mocker.patch.object(postprocessing, "_missing_quota_publication_columns", return_value=[])

    bsb = BuildStockBatchBase(project_filename)
    bsb.process_results()

    assert mock_pipeline.call_count == 1
    args, kwargs = mock_pipeline.call_args
    assert args[0] == f"{results_dir}/parquet"
    assert args[1] == f"{results_dir}/publication"
    assert kwargs["sampler_type"] == "quota"
    assert kwargs["baseline_file"] == f"{results_dir}/parquet/baseline/results_up00.parquet"
    assert kwargs["upgrade_files"] == [f"{results_dir}/parquet/upgrades/upgrade=1/results_up01.parquet"]
    assert kwargs["allocation_seed"] == 99

    # The old partial-publication outputs must no longer be written
    results_path = pathlib.Path(results_dir)
    assert not (results_path / "results_csvs_pub").exists()
    assert not (results_path / "parquet" / "pub_annual").exists()


def test_publish_annual_results_skips_when_quota_columns_missing(basic_residential_project_file, mocker, caplog):
    """Missing quota-required columns produce a warning and skip publication only."""
    caplog.set_level(logging.WARNING, logger="buildstockbatch.postprocessing")
    project_filename, results_dir = basic_residential_project_file(
        {"schema_version": "0.6", "postprocessing": {"publish_annual_results": True}}
    )

    mocker.patch.object(BuildStockBatchBase, "weather_dir", None)
    mocker.patch.object(BuildStockBatchBase, "get_dask_client")
    mocker.patch.object(BuildStockBatchBase, "results_dir", results_dir)
    mock_pipeline = mocker.MagicMock()
    mocker.patch.object(postprocessing, "get_annual_publishing_pipeline", return_value=mock_pipeline)
    mocker.patch.object(
        postprocessing, "_missing_quota_publication_columns", return_value=["weight", "in.sampling_region_id"]
    )

    bsb = BuildStockBatchBase(project_filename)
    bsb.process_results()  # must complete despite skipping publication

    mock_pipeline.assert_not_called()
    assert any(
        "Skipping publish_annual_results" in record.message and "weight" in record.message for record in caplog.records
    )
    # Regular outputs are unaffected
    assert (pathlib.Path(results_dir) / "results_csvs" / "results_up00.csv.gz").exists()


def test_publish_annual_results_unsupported_filesystem(basic_residential_project_file, mocker):
    """Non-local, non-S3 filesystems (e.g. GCS) are rejected with a clear error."""
    project_filename, results_dir = basic_residential_project_file(
        {"schema_version": "0.6", "postprocessing": {"publish_annual_results": True}}
    )
    mocker.patch.object(postprocessing, "get_annual_publishing_pipeline", return_value=mocker.MagicMock())
    cfg = get_project_configuration(project_filename)

    class FakeGCSFileSystem:
        pass

    with pytest.raises(NotImplementedError, match="local and S3"):
        postprocessing.publish_annual_results(FakeGCSFileSystem(), results_dir, cfg)


def test_publish_annual_results_unsupported_sampler(basic_residential_project_file, mocker):
    project_filename, results_dir = basic_residential_project_file(
        {"schema_version": "0.6", "postprocessing": {"publish_annual_results": True}}
    )
    mocker.patch.object(postprocessing, "get_annual_publishing_pipeline", return_value=mocker.MagicMock())
    cfg = get_project_configuration(project_filename)
    cfg["sampler"]["type"] = "commercial_sobol"

    with pytest.raises(ValueError, match="not supported by publish_annual_results"):
        postprocessing.publish_annual_results(LocalFileSystem(), results_dir, cfg)


def test_validate_publish_annual_results_not_enabled(basic_residential_project_file):
    # Not enabled: passes regardless of other settings
    project_filename, _ = basic_residential_project_file({"baseline": {"skip_sims": True}})
    assert BuildStockBatchBase.validate_publish_annual_results(project_filename)


def test_validate_publish_annual_results_ok(basic_residential_project_file, mocker):
    # Enabled with everything satisfied (resstockpostproc presence mocked)
    mocker.patch("importlib.util.find_spec", return_value=object())
    project_filename, _ = basic_residential_project_file(
        {"schema_version": "0.6", "postprocessing": {"publish_annual_results": True}}
    )
    assert BuildStockBatchBase.validate_publish_annual_results(project_filename)


def test_validate_publish_annual_results_skip_sims(basic_residential_project_file, mocker):
    mocker.patch("importlib.util.find_spec", return_value=object())
    project_filename, _ = basic_residential_project_file(
        {
            "schema_version": "0.6",
            "baseline": {"skip_sims": True},
            "postprocessing": {"publish_annual_results": True},
        }
    )
    with pytest.raises(ValidationError, match="skip_sims"):
        BuildStockBatchBase.validate_publish_annual_results(project_filename)


def test_validate_publish_annual_results_unsupported_sampler(basic_residential_project_file, mocker):
    mocker.patch("importlib.util.find_spec", return_value=object())
    project_filename, _ = basic_residential_project_file(
        {
            "schema_version": "0.6",
            "sampler": {"type": "commercial_sobol", "args": {}},
            "postprocessing": {"publish_annual_results": True},
        }
    )
    with pytest.raises(ValidationError, match="not supported by publish_annual_results"):
        BuildStockBatchBase.validate_publish_annual_results(project_filename)


def test_validate_publish_annual_results_missing_package(basic_residential_project_file, mocker):
    mocker.patch("importlib.util.find_spec", return_value=None)
    project_filename, _ = basic_residential_project_file(
        {"schema_version": "0.6", "postprocessing": {"publish_annual_results": True}}
    )
    with pytest.raises(ValidationError, match="resstockpostproc"):
        BuildStockBatchBase.validate_publish_annual_results(project_filename)


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
