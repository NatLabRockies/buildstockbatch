"""Unit tests for the residential stratified sampler's configuration defaults."""

import yaml

from buildstockbatch.sampler.residential_stratified import ResidentialStratifiedSampler
from buildstockbatch.utils import ContainerRuntime


class _FakeParent:
    """The two attributes BuildStockSampler.__init__ reads from its owner."""

    CONTAINER_RUNTIME = ContainerRuntime.LOCAL_OPENSTUDIO

    def __init__(self, project_filename, output_dir):
        self.project_filename = str(project_filename)
        self.output_dir = str(output_dir)
        self.project_dir = str(project_filename.parent)


def _make_sampler(tmp_path, **kwargs):
    tmp_path.mkdir(parents=True, exist_ok=True)
    project_filename = tmp_path / "project.yml"
    project_filename.write_text("")
    parent = _FakeParent(project_filename, tmp_path / "out")
    return ResidentialStratifiedSampler(parent, n_datapoints=100, **kwargs), tmp_path / "sampler_config.yaml"


def test_default_segment_vars_are_allocation_keys(tmp_path):
    # Geometry Floor Area Bin flows downhill through the TSVs and is invisible to the allocator,
    # so it must not split segments; the defaults must match ResStock's sampler_config.yaml.
    sampler, config_path = _make_sampler(tmp_path)
    config = yaml.safe_load(config_path.read_text())
    assert config["segment_vars"] == [
        "Federal Poverty Level",
        "Geometry Building Type RECS",
        "Vintage",
        "Heating Fuel",
        "Sampling Region",
    ]
    assert "Geometry Floor Area Bin" not in config["segment_vars"]
    assert config["segment_selection_sample_size"] == 10000000
    assert config["num_samples_per_segment"] == 12
    assert sampler.sampler_config == str(config_path)


def test_explicit_args_override_defaults(tmp_path):
    _, config_path = _make_sampler(
        tmp_path,
        segment_vars=["Vintage", "Heating Fuel", "Sampling Region"],
        segment_selection_sample_size=5000000,
        num_samples_per_segment=10,
    )
    config = yaml.safe_load(config_path.read_text())
    assert config["segment_vars"] == ["Vintage", "Heating Fuel", "Sampling Region"]
    assert config["segment_selection_sample_size"] == 5000000
    assert config["num_samples_per_segment"] == 10


def test_default_segment_vars_are_not_shared_between_instances(tmp_path):
    sampler_a, _ = _make_sampler(tmp_path / "a")
    sampler_b, _ = _make_sampler(tmp_path / "b")
    assert isinstance(ResidentialStratifiedSampler.DEFAULT_SEGMENT_VARS, tuple)
    assert sampler_a.sampler_config != sampler_b.sampler_config
