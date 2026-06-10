import os
import pathlib
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from buildstockbatch.exc import ValidationError
from buildstockbatch.sampler.residential_stratified import ResidentialStratifiedSampler
from buildstockbatch.utils import ContainerRuntime


def _make_parent(container_runtime, project_dir, buildstock_dir=None, output_dir=None):
    parent = MagicMock()
    parent.project_filename = "test_project.yml"
    parent.cfg = {"project_directory": "project_resstock_national"}
    parent.CONTAINER_RUNTIME = container_runtime
    parent.project_dir = project_dir
    parent.buildstock_dir = buildstock_dir
    parent.output_dir = output_dir
    parent.docker_image = "buildstockbatch:latest"
    parent.apptainer_image = "/path/to/image.sif"
    return parent


def test_residential_stratified_validate_args():
    assert ResidentialStratifiedSampler.validate_args("dummy_project.yml", n_datapoints=1)
    assert ResidentialStratifiedSampler.validate_args("dummy_project.yml", n_datapoints=1000)


@pytest.mark.parametrize("n_datapoints", ["1000", 1000.5])
def test_residential_stratified_validate_args_non_integer(n_datapoints):
    with pytest.raises(ValidationError, match="n_datapoints needs to be an integer"):
        ResidentialStratifiedSampler.validate_args("dummy_project.yml", n_datapoints=n_datapoints)


@pytest.mark.parametrize("n_datapoints", [0, -1])
def test_residential_stratified_validate_args_non_positive(n_datapoints):
    with pytest.raises(ValidationError, match="n_datapoints need to be >= 1"):
        ResidentialStratifiedSampler.validate_args("dummy_project.yml", n_datapoints=n_datapoints)


def test_residential_stratified_validate_args_missing_required():
    with pytest.raises(ValidationError, match="The following sampler arguments are required"):
        ResidentialStratifiedSampler.validate_args("dummy_project.yml")


def test_residential_stratified_validate_args_unknown_arg():
    with pytest.raises(ValidationError, match="Unknown argument for sampler"):
        ResidentialStratifiedSampler.validate_args("dummy_project.yml", n_datapoints=10, foo="bar")


def test_residential_stratified_initialization():
    parent = _make_parent(ContainerRuntime.LOCAL_OPENSTUDIO, project_dir="/tmp/project")
    sampler = ResidentialStratifiedSampler(parent, n_datapoints=100)
    assert sampler.n_datapoints == 100
    assert sampler.parent() == parent


@pytest.mark.parametrize(
    "container_runtime,sampler_method",
    [
        (ContainerRuntime.DOCKER, "_run_sampling_docker"),
        (ContainerRuntime.APPTAINER, "_run_sampling_apptainer"),
        (ContainerRuntime.LOCAL_OPENSTUDIO, "_run_sampling_local"),
    ],
)
def test_residential_stratified_run_sampling_dispatch(container_runtime, sampler_method):
    parent = _make_parent(container_runtime, project_dir="/tmp/project", output_dir="/tmp/output")
    sampler = ResidentialStratifiedSampler(parent, n_datapoints=100)

    with patch.object(sampler, sampler_method) as mocked_method:
        sampler.run_sampling()
        mocked_method.assert_called_once()


def test_residential_stratified_run_sampling_local():
    with tempfile.TemporaryDirectory() as tmpdir:
        buildstock_dir = os.path.join(tmpdir, "buildstock")
        resources_dir = os.path.join(buildstock_dir, "resources")
        os.makedirs(resources_dir)

        project_dir = os.path.join(tmpdir, "project")
        os.makedirs(os.path.join(project_dir, "housing_characteristics"))

        with open(os.path.join(resources_dir, "buildstock.csv"), "w") as f:
            f.write("building_id\n1\n")

        parent = _make_parent(
            ContainerRuntime.LOCAL_OPENSTUDIO,
            project_dir=project_dir,
            buildstock_dir=buildstock_dir,
        )
        sampler = ResidentialStratifiedSampler(parent, n_datapoints=350)

        with patch("buildstockbatch.sampler.residential_stratified.subprocess") as subprocess_mock:
            result = sampler._run_sampling_local()

        subprocess_mock.run.assert_called_once()
        args = subprocess_mock.run.call_args[0][0]
        assert args[0] == "python"
        assert any("run_sampler.py" in arg for arg in args)
        assert "sample" in args
        assert "-n" in args
        assert "350" in args
        assert pathlib.Path(result).exists()


def test_residential_stratified_run_sampling_apptainer():
    with tempfile.TemporaryDirectory() as tmpdir:
        buildstock_dir = os.path.join(tmpdir, "buildstock")
        os.makedirs(os.path.join(buildstock_dir, "resources"))

        output_dir = os.path.join(tmpdir, "output")
        os.makedirs(os.path.join(output_dir, "housing_characteristics"))

        parent = _make_parent(
            ContainerRuntime.APPTAINER,
            project_dir="/tmp/project",
            buildstock_dir=buildstock_dir,
            output_dir=output_dir,
        )
        sampler = ResidentialStratifiedSampler(parent, n_datapoints=2000)

        with patch("buildstockbatch.sampler.residential_stratified.subprocess") as subprocess_mock:
            result = sampler._run_sampling_apptainer()

        subprocess_mock.run.assert_called_once()
        args = subprocess_mock.run.call_args[0][0]
        assert args[0] == "apptainer"
        assert "exec" in args
        assert "python" in args
        assert any("run_sampler.py" in arg for arg in args)
        assert "-n" in args
        assert "2000" in args
        assert result == sampler.csv_path


def test_residential_stratified_run_sampling_docker():
    with tempfile.TemporaryDirectory() as tmpdir:
        buildstock_dir = os.path.join(tmpdir, "buildstock")
        resources_dir = os.path.join(buildstock_dir, "resources")
        os.makedirs(resources_dir)

        project_dir = os.path.join(tmpdir, "project")
        os.makedirs(os.path.join(project_dir, "housing_characteristics"))

        with open(os.path.join(resources_dir, "buildstock.csv"), "w") as f:
            f.write("building_id\n1\n")

        parent = _make_parent(
            ContainerRuntime.DOCKER,
            project_dir=project_dir,
            buildstock_dir=buildstock_dir,
        )
        sampler = ResidentialStratifiedSampler(parent, n_datapoints=5000)

        with patch("buildstockbatch.sampler.residential_stratified.docker") as docker_mock:
            docker_client_mock = MagicMock()
            docker_mock.DockerClient.from_env.return_value = docker_client_mock
            docker_client_mock.containers.run.return_value = b"Sampling completed"

            result = sampler._run_sampling_docker()

        docker_mock.DockerClient.from_env.assert_called_once()
        docker_client_mock.containers.run.assert_called_once()
        args = docker_client_mock.containers.run.call_args[0][1]
        assert "python" in args
        assert "samplers/stratified/sampler/run_sampler.py" in args
        assert "-n" in args
        assert "5000" in args
        assert result == sampler.csv_path
