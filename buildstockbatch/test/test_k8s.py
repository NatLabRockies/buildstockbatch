"""Tests for the Kubernetes backend (buildstockbatch.k8s)."""

import gzip
import json
import logging
import pathlib
import tarfile
from types import SimpleNamespace
from unittest.mock import MagicMock

from fsspec.implementations.memory import MemoryFileSystem
from kubernetes.client.rest import ApiException
import pytest
import urllib3
import yaml

from buildstockbatch.base import BuildStockBatchBase
from buildstockbatch.cloud.docker_base import DockerBatchBase
from buildstockbatch.exc import ValidationError
from buildstockbatch.k8s import k8s
from buildstockbatch.k8s.k8s import K8sBatch

K8S_CFG = {
    "job_identifier": "bsb-test",
    "kube_context": "test-admin@test-cluster",
    "namespace": "bsb",
    "image_repository": "registry.example.org/bsb/buildstockbatch",
    "image_pull_secret": "regcred",
    "batch_array_size": 4,
    "parallelism": 2,
    "s3": {
        "endpoint_url": "https://s3.example.org:6780",
        "bucket": "bsb-bucket",
        "prefix": "runs/test",
        "credentials_secret": "bsb-s3",
    },
    "job_environment": {"cpus": 1, "memory_mib": 2048, "minutes_per_sim": 2},
    "postprocessing_environment": {"cpus": 4, "memory_mib": 16384},
}


@pytest.fixture
def memfs(mocker):
    fs = MemoryFileSystem()
    fs.store.clear()
    fs.pseudo_dirs.clear()
    fs.pseudo_dirs.append("")
    mocker.patch.object(k8s, "s3fs_for", return_value=fs)
    yield fs
    fs.store.clear()


@pytest.fixture
def k8s_project(basic_residential_project_file):
    project_filename, _ = basic_residential_project_file({"schema_version": "0.6"})
    with open(project_filename) as f:
        base_cfg = yaml.safe_load(f)
    base_cfg["workflow_generator"]["version"] = "latest"  # required by schema v0.6

    def _make(k8s_overrides={}):
        with open(project_filename, "w") as f:
            yaml.dump({**base_cfg, "k8s": {**K8S_CFG, **k8s_overrides}}, f)
        return project_filename

    return _make


@pytest.fixture
def batch(k8s_project, mocker):
    mocker.patch("buildstockbatch.cloud.docker_base.docker.DockerClient")
    mocker.patch.object(k8s.k8s_config, "load_kube_config")
    mocker.patch.object(k8s.k8s_client, "BatchV1Api")
    mocker.patch.object(K8sBatch, "POLL_SECONDS", 0)
    return K8sBatch(k8s_project())


def job_status(succeeded=0, failed=0, condition=None):
    conditions = [SimpleNamespace(type=condition, status="True")] if condition else None
    return SimpleNamespace(
        status=SimpleNamespace(succeeded=succeeded, failed=failed, active=0, conditions=conditions, failed_indexes=None)
    )


def test_schema_accepts_k8s_section(k8s_project):
    assert BuildStockBatchBase.validate_project_schema(k8s_project())


@pytest.mark.parametrize("required_key", ["parallelism", "kube_context"])
def test_schema_requires_safety_settings(k8s_project, required_key):
    cfg = {k: v for k, v in K8S_CFG.items() if k != required_key}
    project_filename = k8s_project()
    with open(project_filename) as f:
        full = yaml.safe_load(f)
    full["k8s"] = cfg
    with open(project_filename, "w") as f:
        yaml.dump(full, f)
    with pytest.raises(ValueError, match=required_key):
        BuildStockBatchBase.validate_project_schema(project_filename)


@pytest.mark.parametrize("bad_id", ["Upper", "ends-with-", "x" * 51, "under_score"])
def test_schema_rejects_bad_job_identifier(k8s_project, bad_id):
    with pytest.raises(ValueError):
        BuildStockBatchBase.validate_project_schema(k8s_project({"job_identifier": bad_id}))


def test_job_identifier_override_is_validated(k8s_project, mocker):
    mocker.patch("buildstockbatch.cloud.docker_base.docker.DockerClient")
    mocker.patch.object(k8s.k8s_config, "load_kube_config")
    mocker.patch.object(k8s.k8s_client, "BatchV1Api")
    with pytest.raises(ValidationError):
        K8sBatch(k8s_project(), job_identifier="Bad_ID")
    assert K8sBatch(k8s_project(), job_identifier="other-run").image_uri.endswith(":other-run")


def test_paths(batch):
    assert batch.output_dir == "bsb-bucket/runs/test"
    assert batch.results_dir == "bsb-bucket/runs/test/results"
    assert batch.image_uri == "registry.example.org/bsb/buildstockbatch:bsb-test"
    k8s.k8s_config.load_kube_config.assert_called_once_with(context="test-admin@test-cluster")


def test_simulation_job_manifest(batch):
    m = batch._job_manifest(
        "bsb-test",
        env={"JOB_TYPE": "SIMULATE"},
        cpus=1,
        memory_mib=2048,
        completions=4,
        parallelism=2,
        indexed=True,
        deadline_s=960,
    )
    spec = m["spec"]
    assert m["metadata"] == {"name": "bsb-test", "namespace": "bsb", "labels": spec["template"]["metadata"]["labels"]}
    assert spec["completionMode"] == "Indexed"
    assert spec["completions"] == 4 and spec["parallelism"] == 2
    assert spec["backoffLimitPerIndex"] == K8sBatch.DEFAULT_RETRIES_PER_TASK
    assert "backoffLimit" not in spec
    pod = spec["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["activeDeadlineSeconds"] == 960
    assert pod["imagePullSecrets"] == [{"name": "regcred"}]
    (container,) = pod["containers"]
    assert container["image"] == batch.image_uri
    assert container["command"] == ["python3", "-m", "buildstockbatch.k8s.k8s"]
    assert container["envFrom"] == [{"secretRef": {"name": "bsb-s3"}}]
    assert container["resources"]["requests"] == {"cpu": "1", "memory": "2048Mi"}
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env == {
        "S3_ENDPOINT_URL": "https://s3.example.org:6780",
        "S3_BUCKET": "bsb-bucket",
        "S3_PREFIX": "runs/test",
        "JOB_TYPE": "SIMULATE",
    }


def test_start_batch_job_creates_and_waits(batch):
    api = batch.batch_api
    api.read_namespaced_job_status.side_effect = [
        job_status(succeeded=1),
        job_status(succeeded=4, condition="Complete"),
    ]
    batch.start_batch_job(DockerBatchBase.BatchInfo(n_sims=10, n_sims_per_job=3, job_count=4))

    ns, manifest = api.create_namespaced_job.call_args.args
    assert ns == "bsb"
    assert manifest["spec"]["completions"] == 4
    assert manifest["spec"]["parallelism"] == 2
    assert manifest["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] == "Always"
    # 10 minutes overhead + 3 sims * 2 minutes per sim
    assert manifest["spec"]["template"]["spec"]["activeDeadlineSeconds"] == 60 * 16
    assert api.read_namespaced_job_status.call_count == 2


def test_start_batch_job_missing_only_reruns_missing_count(batch, mocker, caplog):
    caplog.set_level(logging.INFO)
    batch.missing_only = True
    mocker.patch.object(K8sBatch, "find_missing_tasks", return_value=2)
    batch.batch_api.read_namespaced_job_status.return_value = job_status(succeeded=2, condition="Complete")
    batch.start_batch_job(DockerBatchBase.BatchInfo(n_sims=10, n_sims_per_job=3, job_count=4))
    manifest = batch.batch_api.create_namespaced_job.call_args.args[1]
    assert manifest["spec"]["completions"] == 2
    env = {e["name"]: e["value"] for e in manifest["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["MISSING_ONLY"] == "True"
    assert "Retrying 2 of 4 tasks" in caplog.text


def test_start_batch_job_missing_only_nothing_to_do(batch, mocker):
    batch.missing_only = True
    mocker.patch.object(K8sBatch, "find_missing_tasks", return_value=0)
    with pytest.raises(ValidationError):
        batch.start_batch_job(DockerBatchBase.BatchInfo(n_sims=10, n_sims_per_job=3, job_count=4))
    batch.batch_api.create_namespaced_job.assert_not_called()


def test_failed_job_raises(batch):
    batch.batch_api.read_namespaced_job_status.return_value = job_status(failed=3, condition="Failed")
    with pytest.raises(RuntimeError, match="--missingonly"):
        batch._wait_for_job("bsb-test", 4, "Running Simulations")


@pytest.mark.parametrize(
    "error", [ApiException(status=503), ApiException(status=0), urllib3.exceptions.ProtocolError("reset")]
)
def test_wait_for_job_retries_transient_errors(batch, error):
    batch.batch_api.read_namespaced_job_status.side_effect = [error, job_status(succeeded=4, condition="Complete")]
    assert batch._wait_for_job("bsb-test", 4, "Running Simulations").succeeded == 4


def test_wait_for_job_raises_client_errors(batch):
    batch.batch_api.read_namespaced_job_status.side_effect = ApiException(status=404)
    with pytest.raises(ApiException):
        batch._wait_for_job("bsb-test", 4, "Running Simulations")


def test_existing_job_gives_clear_error(batch):
    batch.batch_api.create_namespaced_job.side_effect = ApiException(status=409)
    with pytest.raises(ValidationError, match="already exists"):
        batch._create_job(batch._job_manifest("bsb-test", {}, 1, 1024))


def test_clean_ignores_missing_jobs(batch):
    batch.batch_api.delete_namespaced_job.side_effect = [None, ApiException(status=404)]
    batch.clean()
    names = [c.args[0] for c in batch.batch_api.delete_namespaced_job.call_args_list]
    assert names == ["bsb-test", "bsb-test-pp"]


def test_process_results_runs_postprocessing_job(batch, mocker):
    mocker.patch.object(K8sBatch, "log_summary")
    batch.batch_api.read_namespaced_job_status.return_value = job_status(succeeded=1, condition="Complete")
    batch.process_results()
    manifest = batch.batch_api.create_namespaced_job.call_args.args[1]
    assert manifest["metadata"]["name"] == "bsb-test-pp"
    assert "completionMode" not in manifest["spec"]
    env = {e["name"]: e["value"] for e in manifest["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["JOB_TYPE"] == "POSTPROCESS"
    assert env["RESULTS_DIR"] == "bsb-bucket/runs/test/results"
    assert env["DO_TIMESERIES"] == "True"  # fixture uses hourly timeseries
    assert (env["PP_CPUS"], env["PP_MEMORY_MIB"]) == ("4", "16384")
    K8sBatch.log_summary.assert_called_once()


@pytest.mark.parametrize(
    "wfg,expected",
    [
        ({"type": "residential_hpxml", "args": {}}, False),
        ({"type": "residential_hpxml", "args": {"simulation_output_report": {"timeseries_frequency": "none"}}}, False),
        ({"type": "residential_hpxml", "args": {"simulation_output_report": {"timeseries_frequency": "hourly"}}}, True),
        ({"type": "commercial_default", "args": {"timeseries_csv_export": {}}}, True),
        ({"type": "commercial_default"}, False),
    ],
)
def test_do_timeseries(wfg, expected):
    assert K8sBatch._do_timeseries({"workflow_generator": wfg}) is expected


def test_upload_and_copy_files(batch, memfs, tmp_path):
    (tmp_path / "weather").mkdir()
    (tmp_path / "weather" / "a.epw.gz").write_bytes(b"A")
    (tmp_path / "config.json").write_text("{}")
    batch.upload_batch_files_to_cloud(tmp_path)
    assert memfs.cat("bsb-bucket/runs/test/config.json") == b"{}"
    assert memfs.cat("bsb-bucket/runs/test/weather/a.epw.gz") == b"A"

    batch.copy_files_at_cloud([("a.epw.gz", "b.epw.gz")])
    assert memfs.cat("bsb-bucket/runs/test/weather/b.epw.gz") == b"A"
    batch.copy_files_at_cloud([])  # no-op


def test_check_output_dir(batch, memfs):
    batch.check_output_dir()  # nothing there yet
    memfs.pipe("bsb-bucket/runs/test/results/simulation_output/results_job0.json.gz", b"x")
    with pytest.raises(ValidationError, match="already exist"):
        batch.check_output_dir()


def _write_tar(path, arcname, data):
    src = path.parent / f"src_{arcname.replace('/', '_')}"
    src.write_bytes(data)
    with tarfile.open(path, "w:gz") as tf:
        tf.add(src, arcname=arcname)
    return path.read_bytes()


@pytest.mark.parametrize("missing_only,task_index,expected_job", [(False, 1, 1), (True, 1, 3)])
def test_run_task(memfs, mocker, tmp_path, missing_only, task_index, expected_job):
    base = "bkt/pre"
    jobs_d = {"job_num": expected_job, "n_datapoints": 5, "batch": [[1, None]]}
    with tarfile.open(tmp_path / "jobs.tar.gz", "w:gz") as tf:
        for i in range(4):
            f = tmp_path / f"job{i:05d}.json"
            f.write_text(json.dumps({**jobs_d, "job_num": i}))
            tf.add(f, arcname=f"jobs/job{i:05d}.json")
    memfs.pipe(f"{base}/jobs.tar.gz", (tmp_path / "jobs.tar.gz").read_bytes())
    memfs.pipe(f"{base}/assets.tar.gz", _write_tar(tmp_path / "assets.tar.gz", "lib/marker.txt", b"asset"))
    memfs.pipe(f"{base}/config.json", json.dumps({"cfg": 1}).encode())
    memfs.pipe(f"{base}/weather/G1.epw.gz", gzip.compress(b"epw-data"))
    memfs.pipe(f"{base}/results/missing_tasks.txt", b"0\n3\n")
    mocker.patch.object(k8s.docker_base, "determine_weather_files_needed_for_job", return_value={"G1.epw"})
    run_sims = mocker.patch.object(K8sBatch, "run_simulations")

    sim_dir = tmp_path / "simdata" / "openstudio"
    K8sBatch.run_task(task_index, "bkt", "pre", "https://unused", missing_only, sim_dir=sim_dir)

    cfg, job_id, got_jobs_d, got_sim_dir, fs, output_path = run_sims.call_args.args
    assert cfg == {"cfg": 1}
    assert job_id == expected_job and got_jobs_d["job_num"] == expected_job
    assert pathlib.Path(got_sim_dir) == sim_dir and fs is memfs and output_path == base
    assert (sim_dir / "lib" / "marker.txt").read_bytes() == b"asset"
    assert (sim_dir / "weather" / "G1.epw").read_bytes() == b"epw-data"
    assert not (sim_dir.parent / "assets.tar.gz").exists()


def test_run_in_pod_dispatch(mocker):
    run_task = mocker.patch.object(K8sBatch, "run_task")
    run_pp = mocker.patch.object(K8sBatch, "run_combine_results")
    common = {"S3_BUCKET": "b", "S3_PREFIX": "p", "S3_ENDPOINT_URL": "https://e"}

    assert k8s.run_in_pod({}) is False
    assert k8s.run_in_pod({**common, "JOB_TYPE": "SIMULATE", "JOB_COMPLETION_INDEX": "7", "MISSING_ONLY": "False"})
    run_task.assert_called_once_with(7, "b", "p", "https://e", False)
    env = {**common, "JOB_TYPE": "POSTPROCESS", "RESULTS_DIR": "b/p/results", "DO_TIMESERIES": "True"}
    assert k8s.run_in_pod({**env, "PP_CPUS": "4", "PP_MEMORY_MIB": "16384"})
    run_pp.assert_called_once_with("b", "p", "https://e", "b/p/results", True, 4, 16384)


def test_run_combine_results_sizes_dask_to_pod(memfs, mocker):
    memfs.pipe("b/p/config.json", b'{"a": 1}')
    dask = mocker.patch.object(k8s, "DaskClient")
    combine = mocker.patch.object(k8s.postprocessing, "combine_results")
    K8sBatch.run_combine_results("b", "p", "https://e", "b/p/results", True, 4, 16384)
    dask.assert_called_once_with(n_workers=4, threads_per_worker=1, memory_limit="4096MiB")
    combine.assert_called_once_with(memfs, "b/p/results", {"a": 1}, do_timeseries=True)


def test_push_image_raises_on_error(batch):
    batch.docker_client.images.push = MagicMock(return_value=[{"status": "Pushing"}, {"error": "denied"}])
    with pytest.raises(RuntimeError, match="denied"):
        batch.push_image()
    batch.docker_client.images.get.return_value.tag.assert_called_once_with(
        "registry.example.org/bsb/buildstockbatch", tag="bsb-test"
    )


def job(condition=None):
    return job_status(condition=condition)


@pytest.mark.parametrize(
    "sim, pp, postprocess_only, error",
    [
        (None, None, False, None),
        (job(), None, False, "bsb-test already exists"),
        (job("Complete"), None, False, "bsb-test already exists"),
        (None, job(), False, "bsb-test-pp already exists"),
        (job(), None, True, "still running"),
        (job("Complete"), None, True, None),
        (job("Failed"), None, True, None),
        (None, None, True, None),
        (job("Complete"), job(), True, "bsb-test-pp already exists"),
    ],
)
def test_check_jobs(batch, sim, pp, postprocess_only, error):
    """check_jobs stops a run that would overwrite inputs a live Job still reads."""
    jobs = {"bsb-test": sim, "bsb-test-pp": pp}

    def read(name, namespace):
        if jobs[name] is None:
            raise ApiException(status=404)
        return jobs[name]

    batch.batch_api.read_namespaced_job.side_effect = read
    if error:
        with pytest.raises(ValidationError, match=error):
            batch.check_jobs(postprocess_only)
    else:
        batch.check_jobs(postprocess_only)


def test_main_checks_jobs_before_building(k8s_project, mocker):
    """A Job conflict stops main() before the image is built or pushed."""
    mocker.patch.object(k8s, "run_in_pod", return_value=False)
    mocker.patch.object(K8sBatch, "validate_project")
    mocker.patch.object(K8sBatch, "__init__", return_value=None)
    mocker.patch.object(K8sBatch, "check_jobs", side_effect=ValidationError("exists"))
    build = mocker.patch.object(K8sBatch, "build_image")
    mocker.patch("sys.argv", ["buildstock_k8s", k8s_project(), "--postprocessonly"])
    with pytest.raises(ValidationError):
        k8s.main()
    K8sBatch.check_jobs.assert_called_once_with(postprocess_only=True)
    build.assert_not_called()
