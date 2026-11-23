# -*- coding: utf-8 -*-

"""
buildstockbatch.k8s
~~~~~~~~~~~~~~~
Run BuildStock batches as Kubernetes Jobs, with inputs and results on any S3-compatible object store
(for example Ceph RGW on OpenStack, MinIO, or AWS S3).

* Simulations run as one Indexed Job; each pod reads ``JOB_COMPLETION_INDEX`` to pick its batch of sims.
* Postprocessing runs as a second, single-pod Job.
* Pods get S3 credentials from a Kubernetes Secret named in the project file.

:copyright: (c) 2026 by The Alliance for Sustainable Energy
:license: BSD-3
"""

import argparse
from dask.distributed import Client as DaskClient
import gzip
import json
import logging
import logging.config
import os
import pathlib
import re
import tarfile
import time

from kubernetes import client as k8s_client, config as k8s_config
from kubernetes.client.rest import ApiException
from s3fs import S3FileSystem
import tqdm

from buildstockbatch import postprocessing
from buildstockbatch.cloud import docker_base
from buildstockbatch.cloud.docker_base import DockerBatchBase
from buildstockbatch.exc import ValidationError
from buildstockbatch.utils import get_project_configuration, log_error_details

logger = logging.getLogger(__name__)

# Leaves room for the "-pp" suffix of the postprocessing Job within the 63-character name limit.
JOB_IDENTIFIER_RE = r"^[a-z]([a-z0-9-]{0,48}[a-z0-9])?$"
CONTAINER_SIM_DIR = pathlib.Path("/var/simdata/openstudio")


def s3fs_for(endpoint_url):
    """S3 filesystem for an S3-compatible endpoint. Credentials come from the standard AWS chain
    (``AWS_ACCESS_KEY_ID``/``AWS_SECRET_ACCESS_KEY`` or ``AWS_PROFILE``)."""
    return S3FileSystem(endpoint_url=endpoint_url)


class K8sBatch(DockerBatchBase):
    DEFAULT_TASK_CPUS = 1
    DEFAULT_TASK_MEMORY_MIB = 2048
    DEFAULT_MINUTES_PER_SIM = 3
    DEFAULT_RETRIES_PER_TASK = 2
    DEFAULT_PP_CPUS = 2
    DEFAULT_PP_MEMORY_MIB = 8192
    POLL_SECONDS = 10

    def __init__(self, project_filename, job_identifier=None, missing_only=False):
        super().__init__(project_filename, missing_only)
        self.k8s_cfg = self.cfg["k8s"]
        self.job_identifier = job_identifier or self.k8s_cfg["job_identifier"]
        if not re.match(JOB_IDENTIFIER_RE, self.job_identifier):
            raise ValidationError(
                f"Job identifier '{self.job_identifier}' must be at most 50 characters, start with a letter, "
                "and contain only lowercase letters, numbers, and hyphens."
            )
        self.namespace = self.k8s_cfg.get("namespace", "default")
        s3_cfg = self.k8s_cfg["s3"]
        self.s3_endpoint_url = s3_cfg["endpoint_url"]
        self.s3_bucket = s3_cfg["bucket"]
        self.s3_prefix = s3_cfg["prefix"].strip("/")
        self.batch_array_size = self.k8s_cfg["batch_array_size"]

        k8s_config.load_kube_config(context=self.k8s_cfg.get("kube_context"))
        self.batch_api = k8s_client.BatchV1Api()

    @staticmethod
    def validate_project(project_file):
        super(K8sBatch, K8sBatch).validate_project(project_file)
        cfg = get_project_configuration(project_file)
        if "k8s" not in cfg:
            raise ValidationError('Project config must contain a "k8s" section')

    @property
    def docker_image(self):
        return "nrel/buildstockbatch"

    @property
    def image_uri(self):
        return f"{self.k8s_cfg['image_repository']}:{self.job_identifier}"

    @property
    def output_dir(self):
        return f"{self.s3_bucket}/{self.s3_prefix}"

    @property
    def results_dir(self):
        return f"{self.output_dir}/results"

    @property
    def postprocessing_job_name(self):
        return f"{self.job_identifier}-pp"

    def get_fs(self):
        return s3fs_for(self.s3_endpoint_url)

    def check_output_dir(self):
        """Refuse to start a new batch on top of results from an earlier one."""
        fs = self.get_fs()
        existing = fs.glob(f"{self.results_dir}/simulation_output/results_job*.json.gz")
        if existing:
            raise ValidationError(
                f"Results already exist at s3://{self.results_dir}/simulation_output (e.g. {existing[0]}). "
                "Use a different k8s.s3.prefix, delete them, or run with --missingonly."
            )

    def push_image(self):
        """Tag and push the local image. Uses the credentials from ``docker login <registry>``."""
        repo = self.k8s_cfg["image_repository"]
        image = self.docker_client.images.get(self.docker_image)
        image.tag(repo, tag=self.job_identifier)
        logger.info(f"Pushing {self.image_uri}")
        last_status = None
        for line in self.docker_client.images.push(repo, tag=self.job_identifier, stream=True, decode=True):
            if "error" in line:
                raise RuntimeError(f"Failed to push {self.image_uri}: {line['error']}")
            if line.get("status") not in (None, last_status):
                last_status = line["status"]
                logger.debug(last_status)

    def upload_batch_files_to_cloud(self, tmppath):
        """Implements :func:`DockerBase.upload_batch_files_to_cloud`"""
        logger.info(f"Uploading batch files to s3://{self.output_dir}")
        self.get_fs().put(f"{tmppath}/", f"{self.output_dir}/", recursive=True)

    def copy_files_at_cloud(self, files_to_copy):
        """Implements :func:`DockerBase.copy_files_at_cloud`"""
        if not files_to_copy:
            return
        weather = f"{self.output_dir}/weather"
        self.get_fs().copy(
            [f"{weather}/{src}" for src, _ in files_to_copy], [f"{weather}/{dst}" for _, dst in files_to_copy]
        )

    def _job_manifest(
        self, name, env, cpus, memory_mib, completions=1, parallelism=None, indexed=False, deadline_s=None
    ):
        """Build a ``batch/v1`` Job manifest for the image pushed by :func:`push_image`."""
        labels = {"app.kubernetes.io/name": "buildstockbatch", "buildstockbatch/job-identifier": self.job_identifier}
        env = {
            "S3_ENDPOINT_URL": self.s3_endpoint_url,
            "S3_BUCKET": self.s3_bucket,
            "S3_PREFIX": self.s3_prefix,
            **env,
        }
        container = {
            "name": "buildstockbatch",
            "image": self.image_uri,
            "command": ["python3", "-m", "buildstockbatch.k8s.k8s"],
            "env": [{"name": k, "value": str(v)} for k, v in env.items()],
            "envFrom": [{"secretRef": {"name": self.k8s_cfg["s3"]["credentials_secret"]}}],
            "resources": {
                "requests": {"cpu": str(cpus), "memory": f"{memory_mib}Mi"},
                "limits": {"memory": f"{memory_mib}Mi"},
            },
        }
        pod_spec = {"restartPolicy": "Never", "containers": [container]}
        if deadline_s:
            pod_spec["activeDeadlineSeconds"] = int(deadline_s)
        if pull_secret := self.k8s_cfg.get("image_pull_secret"):
            pod_spec["imagePullSecrets"] = [{"name": pull_secret}]
        spec = {
            "completions": completions,
            "parallelism": parallelism or completions,
            "template": {"metadata": {"labels": labels}, "spec": pod_spec},
        }
        retries = self.k8s_cfg.get("retries_per_task", self.DEFAULT_RETRIES_PER_TASK)
        if indexed:
            spec["completionMode"] = "Indexed"
            spec["backoffLimitPerIndex"] = retries
        else:
            spec["backoffLimit"] = retries
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": name, "namespace": self.namespace, "labels": labels},
            "spec": spec,
        }

    def _create_job(self, manifest):
        try:
            self.batch_api.create_namespaced_job(self.namespace, manifest)
        except ApiException as e:
            if e.status == 409:
                raise ValidationError(
                    f"Job {manifest['metadata']['name']} already exists in namespace {self.namespace}. "
                    "Run with --clean first, or use a different job identifier."
                ) from e
            raise
        logger.info(f"Created Job {manifest['metadata']['name']} in namespace {self.namespace}")

    def _wait_for_job(self, name, total, desc):
        """Poll a Job until it completes or fails. Returns the final ``V1JobStatus``."""
        done_last = 0
        with tqdm.tqdm(desc=desc, total=total, unit="task") as progress:
            while True:
                status = self.batch_api.read_namespaced_job_status(name, self.namespace).status
                done = status.succeeded or 0
                progress.update(done - done_last)
                done_last = done
                progress.set_postfix_str(f"active={status.active or 0} failed={status.failed or 0}")
                conditions = {c.type: c.status for c in (status.conditions or [])}
                if conditions.get("Failed") == "True":
                    raise RuntimeError(
                        f"Job {name} failed (failed indexes: {status.failed_indexes or 'n/a'}). "
                        f"See `kubectl -n {self.namespace} logs -l job-name={name}`. "
                        "After --clean, rerun with --missingonly to retry the missing batches."
                    )
                if conditions.get("Complete") == "True":
                    return status
                time.sleep(self.POLL_SECONDS)

    def start_batch_job(self, batch_info):
        """Implements :func:`DockerBase.start_batch_job`"""
        if self.missing_only:
            # Task i of this Job reruns the task on line i of missing_tasks.txt.
            job_count = self.find_missing_tasks(batch_info.job_count)
            if not job_count:
                raise ValidationError(f"There are no tasks to retry. All {batch_info.job_count} results are present.")
        else:
            job_count = batch_info.job_count

        job_env = self.k8s_cfg.get("job_environment", {})
        minutes_per_sim = job_env.get("minutes_per_sim", self.DEFAULT_MINUTES_PER_SIM)
        manifest = self._job_manifest(
            self.job_identifier,
            env={"JOB_TYPE": "SIMULATE", "MISSING_ONLY": str(self.missing_only)},
            cpus=job_env.get("cpus", self.DEFAULT_TASK_CPUS),
            memory_mib=job_env.get("memory_mib", self.DEFAULT_TASK_MEMORY_MIB),
            completions=job_count,
            parallelism=self.k8s_cfg.get("parallelism"),
            indexed=True,
            # Ten minutes of overhead for downloads and uploads, plus the expected simulation time.
            deadline_s=60 * (10 + batch_info.n_sims_per_job * minutes_per_sim),
        )
        self._create_job(manifest)
        logger.info(
            f"Simulations: {batch_info.n_sims:,} in {job_count:,} tasks. "
            f"Results: s3://{self.results_dir}/simulation_output. "
            "You may interrupt this script; the Job keeps running but postprocessing will not start."
        )
        self._wait_for_job(self.job_identifier, job_count, "Running Simulations")

    @classmethod
    def run_task(cls, task_index, bucket, prefix, endpoint_url, missing_only, sim_dir=CONTAINER_SIM_DIR):
        """Run one batch of simulations. Called inside a pod of the simulation Job."""
        fs = s3fs_for(endpoint_url)
        base = f"{bucket}/{prefix}"
        sim_dir = pathlib.Path(sim_dir)
        sim_dir.mkdir(parents=True, exist_ok=True)

        if missing_only:
            task_index = int(fs.cat(f"{base}/results/missing_tasks.txt").decode().split()[task_index])
            logger.info(f"Rerunning task {task_index}")

        logger.info("Extracting assets")
        assets_file = sim_dir.parent / "assets.tar.gz"
        fs.get(f"{base}/assets.tar.gz", str(assets_file))
        with tarfile.open(assets_file, "r") as tar_f:
            tar_f.extractall(sim_dir)
        os.remove(assets_file)

        cfg = json.loads(fs.cat(f"{base}/config.json"))

        jobs_file = sim_dir.parent / "jobs.tar.gz"
        fs.get(f"{base}/jobs.tar.gz", str(jobs_file))
        with tarfile.open(jobs_file, "r") as tar_f:
            jobs_d = json.load(tar_f.extractfile(f"jobs/job{task_index:05d}.json"))
        os.remove(jobs_file)
        logger.debug(f"Number of simulations = {len(jobs_d['batch'])}")

        weather_dir = sim_dir / "weather"
        weather_dir.mkdir(exist_ok=True)
        for epw_filename in docker_base.determine_weather_files_needed_for_job(sim_dir, jobs_d):
            epw_filename = os.path.basename(epw_filename)
            (weather_dir / epw_filename).write_bytes(gzip.decompress(fs.cat(f"{base}/weather/{epw_filename}.gz")))

        cls.run_simulations(cfg, task_index, jobs_d, sim_dir, fs, base)

    @staticmethod
    def _do_timeseries(cfg):
        wfg_args = cfg["workflow_generator"].get("args", {})
        if cfg["workflow_generator"]["type"] == "residential_hpxml":
            freq = wfg_args.get("simulation_output_report", {}).get("timeseries_frequency", "none")
            return freq != "none"
        return "timeseries_csv_export" in wfg_args

    def process_results(self, skip_combine=False, use_dask_cluster=True):
        """Run ``combine_results`` in a postprocessing Job, then log a summary of the results."""
        if not skip_combine:
            pp_env = self.k8s_cfg.get("postprocessing_environment", {})
            cpus = pp_env.get("cpus", self.DEFAULT_PP_CPUS)
            memory_mib = pp_env.get("memory_mib", self.DEFAULT_PP_MEMORY_MIB)
            manifest = self._job_manifest(
                self.postprocessing_job_name,
                env={
                    "JOB_TYPE": "POSTPROCESS",
                    "RESULTS_DIR": self.results_dir,
                    "DO_TIMESERIES": str(self._do_timeseries(self.cfg)),
                    "PP_CPUS": cpus,
                    "PP_MEMORY_MIB": memory_mib,
                },
                cpus=cpus,
                memory_mib=memory_mib,
            )
            self._create_job(manifest)
            self._wait_for_job(self.postprocessing_job_name, 1, "Postprocessing")
        self.log_summary()

    @staticmethod
    def run_combine_results(bucket, prefix, endpoint_url, results_dir, do_timeseries, cpus, memory_mib):
        """Run ``combine_results``. Called inside the postprocessing pod."""
        fs = s3fs_for(endpoint_url)
        cfg = json.loads(fs.cat(f"{bucket}/{prefix}/config.json"))
        # Size the local dask cluster to the pod's resources, not the node's.
        DaskClient(n_workers=cpus, threads_per_worker=1, memory_limit=f"{memory_mib // cpus}MiB")
        postprocessing.combine_results(fs, results_dir, cfg, do_timeseries=do_timeseries)

    def clean(self):
        """Delete the simulation and postprocessing Jobs and their pods. Files in S3 are kept."""
        for name in (self.job_identifier, self.postprocessing_job_name):
            try:
                self.batch_api.delete_namespaced_job(name, self.namespace, propagation_policy="Background")
                logger.info(f"Deleted Job {name}")
            except ApiException as e:
                if e.status != 404:
                    raise


def run_in_pod(environ):
    """Dispatch work inside a pod. Returns False when not running inside a buildstockbatch pod."""
    job_type = environ.get("JOB_TYPE")
    if job_type == "SIMULATE":
        K8sBatch.run_task(
            int(environ["JOB_COMPLETION_INDEX"]),
            environ["S3_BUCKET"],
            environ["S3_PREFIX"],
            environ["S3_ENDPOINT_URL"],
            environ.get("MISSING_ONLY") == "True",
        )
    elif job_type == "POSTPROCESS":
        K8sBatch.run_combine_results(
            environ["S3_BUCKET"],
            environ["S3_PREFIX"],
            environ["S3_ENDPOINT_URL"],
            environ["RESULTS_DIR"],
            environ.get("DO_TIMESERIES") == "True",
            int(environ["PP_CPUS"]),
            int(environ["PP_MEMORY_MIB"]),
        )
    else:
        return False
    return True


@log_error_details()
def main():
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": True,
            "formatters": {
                "defaultfmt": {
                    "format": "%(levelname)s:%(asctime)s:%(name)s:%(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "defaultfmt",
                    "level": "DEBUG",
                    "stream": "ext://sys.stdout",
                }
            },
            "loggers": {
                "__main__": {"level": "DEBUG", "propagate": True, "handlers": ["console"]},
                "buildstockbatch": {"level": "DEBUG", "propagate": True, "handlers": ["console"]},
            },
        }
    )
    if run_in_pod(os.environ):
        return

    parser = argparse.ArgumentParser()
    parser.add_argument("project_filename")
    parser.add_argument(
        "job_identifier",
        nargs="?",
        default=None,
        help="Optional override of k8s.job_identifier in your project file. Max 50 characters.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-c",
        "--clean",
        action="store_true",
        help="Delete the Kubernetes Jobs (cancelling them if still running). Files in S3 are kept.",
    )
    group.add_argument(
        "--validateonly",
        help="Only validate the project YAML file and references. Nothing is executed",
        action="store_true",
    )
    group.add_argument(
        "--postprocessonly",
        help="Only do postprocessing, useful for when the simulations are already done",
        action="store_true",
    )
    group.add_argument(
        "--missingonly",
        action="store_true",
        help="Only run batches of simulations that are missing from a previous job, then run post-processing. "
        "Assumes the project file is unchanged. Run --clean first to remove the previous Job.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output - includes DEBUG logs if set")
    args = parser.parse_args()
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)

    K8sBatch.validate_project(os.path.abspath(args.project_filename))
    if args.validateonly:
        return

    batch = K8sBatch(args.project_filename, args.job_identifier, missing_only=args.missingonly)
    if args.clean:
        batch.clean()
        return
    if not (args.missingonly or args.postprocessonly):
        batch.check_output_dir()
    batch.build_image("k8s")
    batch.push_image()
    if not args.postprocessonly:
        batch.run_batch()
    batch.process_results()


if __name__ == "__main__":
    main()
