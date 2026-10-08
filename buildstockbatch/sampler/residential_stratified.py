"""
buildstockbatch.sampler.residential_stratified
~~~~~~~~~~~~~~~
This object contains the code required for generating the set of simulations to execute

:author: Joe Robertson
:copyright: (c) 2020 by The Alliance for Sustainable Energy
:license: BSD-3
"""

import docker
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import time
import yaml

from .base import BuildStockSampler
from buildstockbatch.exc import ValidationError

logger = logging.getLogger(__name__)


class ResidentialStratifiedSampler(BuildStockSampler):
    # Defaults match ResStock's samplers/stratified/sampler/sampler_config.yaml. The segment
    # variables are the characteristics the allocation step joins on; Geometry Floor Area Bin
    # flows downhill through the TSVs and is invisible to the allocator, so it must not split
    # segments.
    DEFAULT_SEGMENT_VARS = (
        "Federal Poverty Level",
        "Geometry Building Type RECS",
        "Vintage",
        "Heating Fuel",
        "Sampling Region",
    )
    DEFAULT_SEGMENT_SELECTION_SAMPLE_SIZE = 10000000
    DEFAULT_NUM_SAMPLES_PER_SEGMENT = 12

    def __init__(
        self,
        parent,
        n_datapoints,
        segment_vars=None,
        segment_selection_sample_size=DEFAULT_SEGMENT_SELECTION_SAMPLE_SIZE,
        num_samples_per_segment=DEFAULT_NUM_SAMPLES_PER_SEGMENT,
    ):
        """Residential Stratified Sampler

        :param parent: BuildStockBatchBase object
        :type parent: BuildStockBatchBase (or subclass)
        :param n_datapoints: number of datapoints to sample
        :type n_datapoints: int
        :param segment_vars: parameter for sampling written to sampler_config.yaml; defaults to
            DEFAULT_SEGMENT_VARS
        :type segment_vars: list[str]
        :param segment_selection_sample_size: parameter for sampling written to sampler_config.yaml
        :type segment_selection_sample_size: int
        :param num_samples_per_segment: parameter for sampling written to sampler_config.yaml
        :type num_samples_per_segment: int
        """
        super().__init__(parent)
        self.validate_args(self.parent().project_filename, n_datapoints=n_datapoints)
        self.n_datapoints = n_datapoints
        if segment_vars is None:
            segment_vars = list(self.DEFAULT_SEGMENT_VARS)
        self.sampler_config = self.create_sampler_config(
            os.path.dirname(self.parent().project_filename),
            segment_vars,
            segment_selection_sample_size,
            num_samples_per_segment,
        )

    @classmethod
    def validate_args(cls, project_filename, **kw):
        expected_args = set(["n_datapoints"])
        for k, v in kw.items():
            expected_args.discard(k)
            if k == "n_datapoints":
                if not isinstance(v, int):
                    raise ValidationError("n_datapoints needs to be an integer")
                if v <= 0:
                    raise ValidationError("n_datapoints need to be >= 1")
            elif k == "segment_vars":
                pass
            elif k == "segment_selection_sample_size":
                pass
            elif k == "num_samples_per_segment":
                pass
            else:
                raise ValidationError(f"Unknown argument for sampler: {k}")
        if len(expected_args) > 0:
            raise ValidationError("The following sampler arguments are required: " + ", ".join(expected_args))
        return True

    @classmethod
    def create_sampler_config(self, folderpath, segment_vars, segment_selection_sample_size, num_samples_per_segment):
        data = {}
        data["segment_vars"] = segment_vars
        data["segment_selection_sample_size"] = segment_selection_sample_size
        data["num_samples_per_segment"] = num_samples_per_segment
        filename = pathlib.Path(folderpath) / "sampler_config.yaml"
        with open(filename, "w") as file:
            yaml.dump(data, file)
        return str(filename)

    def _run_sampling_docker(self):
        docker_client = docker.DockerClient.from_env()
        tick = time.time()
        extra_kws = {}
        if sys.platform.startswith("linux"):
            extra_kws["user"] = f"{os.getuid()}:{os.getgid()}"
        container_output = docker_client.containers.run(
            self.parent().docker_image,
            [
                "python",
                "samplers/stratified/sampler/run_sampler.py",
                "sample",
                "-p",
                self.cfg["project_directory"],
                "-n",
                str(self.n_datapoints),
                "-c",
                self.sampler_config,
                "-o",
                "buildstock.csv",
            ],
            remove=True,
            volumes={self.buildstock_dir: {"bind": "/var/simdata/openstudio", "mode": "rw"}},
            name="buildstock_sampling",
            **extra_kws,
        )
        tick = time.time() - tick
        for line in container_output.decode("utf-8").split("\n"):
            logger.debug(line)
        logger.debug("Sampling took {:.1f} seconds".format(tick))
        destination_filename = self.csv_path
        if os.path.exists(destination_filename):
            os.remove(destination_filename)
        shutil.move(
            os.path.join(self.buildstock_dir, "resources", "buildstock.csv"),
            destination_filename,
        )
        config_filename = pathlib.Path(self.sampler_config)
        if config_filename.exists():
            os.remove(config_filename)
        return destination_filename

    def _run_sampling_apptainer(self):
        args = [
            "python",
            "samplers/stratified/sampler/run_sampler.py",
            "sample",
            "-p",
            self.cfg["project_directory"],
            "-n",
            str(self.n_datapoints),
            "-c",
            self.sampler_config,
            "-o",
            self.csv_path,
        ]
        logger.debug(f"Starting sampling with command: {' '.join(args)}")
        subprocess.run(args, check=True, cwd=self.buildstock_dir)
        logger.debug("Sampling completed.")
        config_filename = pathlib.Path(self.sampler_config)
        if config_filename.exists():
            os.remove(config_filename)
        return self.csv_path

    def _run_sampling_local(self):
        subprocess.run(
            [
                "python",
                str(pathlib.Path("samplers", "stratified", "sampler", "run_sampler.py")),
                "sample",
                "-p",
                self.cfg["project_directory"],
                "-n",
                str(self.n_datapoints),
                "-c",
                self.sampler_config,
                "-o",
                "buildstock.csv",
            ],
            cwd=self.buildstock_dir,
            check=True,
        )
        destination_filename = pathlib.Path(self.csv_path)
        if destination_filename.exists():
            os.remove(destination_filename)
        shutil.move(
            pathlib.Path(self.buildstock_dir, "resources", "buildstock.csv"),
            destination_filename,
        )
        config_filename = pathlib.Path(self.sampler_config)
        if config_filename.exists():
            os.remove(config_filename)
        return destination_filename
