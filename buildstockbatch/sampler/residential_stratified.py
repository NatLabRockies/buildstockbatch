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
    def __init__(
        self,
        parent,
        n_datapoints,
        segment_vars=[
            "Federal Poverty Level",
            "Geometry Floor Area Bin",
            "Geometry Building Type RECS",
            "Vintage",
            "Heating Fuel",
            "Sampling Region",
        ],
        segment_selection_sample_size=10000000,
        num_samples_per_segment=8,
    ):
        """Residential Stratified Sampler

        :param parent: BuildStockBatchBase object
        :type parent: BuildStockBatchBase (or subclass)
        :param n_datapoints: number of datapoints to sample
        :type n_datapoints: int
        """
        super().__init__(parent)
        self.validate_args(self.parent().project_filename, n_datapoints=n_datapoints)
        self.n_datapoints = n_datapoints
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
