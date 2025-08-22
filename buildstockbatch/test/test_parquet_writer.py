# test_streaming_parquet_writer.py
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import polars as pl
from buildstockbatch.streaming_parquet_writer import StreamingParquetWriters


def _get_sample_pa_table(n=3, order=("x", "y")) -> pl.DataFrame:
    data = {"x": list(range(n)), "y": [1.0] * n}
    cols = {k: data[k] for k in order}
    return pl.DataFrame(cols)


@pytest.mark.parametrize("batch_size", [1, 2])
def test_rotation_single_path(tmp_path: Path, batch_size: int):
    schema = _get_sample_pa_table().schema
    w = StreamingParquetWriters(
        base_path=tmp_path,
        number_of_dataframes_per_file=2,
        base_name="job57_annual_results",
        batch_size=batch_size,
        polars_schema=schema,
    )
    rel = "up00/"

    w.write(rel, _get_sample_pa_table())  # row-group 1 -> -000.parquet
    w.write(rel, _get_sample_pa_table())  # row-group 2 -> still -000.parquet, rotate ready
    w.write(rel, _get_sample_pa_table())  # row-group 1 -> -001.parquet
    w.close_all()
    p0 = tmp_path / "up00" / "job57_annual_results-000.parquet"
    p1 = tmp_path / "up00" / "job57_annual_results-001.parquet"
    assert p0.exists()
    assert p1.exists()

    pf0 = pq.ParquetFile(p0)
    pf1 = pq.ParquetFile(p1)
    assert pf0.num_row_groups == 2 if batch_size == 1 else 1
    assert pf1.num_row_groups == 1


@pytest.mark.parametrize("batch_size", [1, 2])
def test_multiple_paths_independent_rotation(tmp_path: Path, batch_size: int):
    schema = _get_sample_pa_table().schema
    w = StreamingParquetWriters(
        base_path=tmp_path,
        number_of_dataframes_per_file=2,
        base_name="job_ts",
        batch_size=batch_size,
        polars_schema=schema,
    )

    rel1 = "up00/state=AK/"  # no .parquet, should be auto-added
    rel2 = "up00/state=TX/"

    # AK: write 1 group
    w.write(rel1, _get_sample_pa_table())
    # TX: write 3 groups (rotates after 2)
    w.write(rel2, _get_sample_pa_table())
    w.write(rel2, _get_sample_pa_table())
    w.write(rel2, _get_sample_pa_table())

    ak0 = tmp_path / "up00" / "state=AK" / "job_ts-000.parquet"
    ak1 = tmp_path / "up00" / "state=AK" / "job_ts-001.parquet"
    tx0 = tmp_path / "up00" / "state=TX" / "job_ts-000.parquet"
    tx1 = tmp_path / "up00" / "state=TX" / "job_ts-001.parquet"
    w.close_all()

    assert ak0.exists() and not ak1.exists()
    assert tx0.exists() and tx1.exists()

    assert pq.ParquetFile(ak0).num_row_groups == 1
    assert pq.ParquetFile(tx0).num_row_groups == 2 if batch_size == 1 else 1
    assert pq.ParquetFile(tx1).num_row_groups == 1


@pytest.mark.parametrize("batch_size", [2, 5])
def test_flush_on_close_writes_tail(tmp_path: Path, batch_size: int):
    schema = _get_sample_pa_table().schema
    w = StreamingParquetWriters(
        base_path=tmp_path,
        number_of_dataframes_per_file=100,
        base_name="job_tail",
        batch_size=batch_size,
        polars_schema=schema,
    )
    rel = "up00/"

    # Write fewer than batch_size → nothing flushed yet
    for _ in range(batch_size - 1):
        w.write(rel, _get_sample_pa_table())
    w.close_all()

    # First file may be unsuffixed or "-000" depending on your convention
    p = tmp_path / "up00" / "job_tail-000.parquet"
    assert p.exists(), "Expected first part to exist"
    assert pq.ParquetFile(p).num_row_groups == 1  # tail flushed into one row group


def test_no_empty_next_part_after_exact_boundary(tmp_path: Path):
    """After exactly n_df_per_file writes, closing should NOT leave an empty next part."""
    schema = _get_sample_pa_table().schema
    w = StreamingParquetWriters(
        base_path=tmp_path,
        number_of_dataframes_per_file=2,
        base_name="job_exact",
        batch_size=2,
        polars_schema=schema,
    )
    rel = "up00/"
    w.write(rel, _get_sample_pa_table())
    w.write(rel, _get_sample_pa_table())  # reaches exact boundary
    w.close_all()

    p0 = tmp_path / "up00" / "job_exact-000.parquet"
    p1 = tmp_path / "up00" / "job_exact-001.parquet"
    assert p0.exists()
    assert not p1.exists()  # everything should be fit in p0


@pytest.mark.parametrize("form", ["no_trailing", "trailing"])
def test_trailing_slash_normalization(tmp_path: Path, form: str):
    schema = _get_sample_pa_table().schema
    w = StreamingParquetWriters(
        base_path=tmp_path,
        number_of_dataframes_per_file=3,
        base_name="job_norm",
        batch_size=1,
        polars_schema=schema,
    )
    rel = "up00/state=AK" + ("/" if form == "trailing" else "")
    rel_opposite = "up00/state=AK" + ("" if form == "trailing" else "/")

    w.write(rel, _get_sample_pa_table())
    w.write(rel_opposite, _get_sample_pa_table())  # opposite form
    w.close_all()

    p_000 = tmp_path / "up00" / "state=AK" / "job_norm-000.parquet"
    assert p_000.exists()
    # two writes to the same file with batch_size=1 → two row groups
    assert pq.ParquetFile(p_000).num_row_groups == 2


def test_invalid_params_raise(tmp_path: Path):
    schema = _get_sample_pa_table().schema
    with pytest.raises(ValueError):
        StreamingParquetWriters(
            base_path=tmp_path,
            number_of_dataframes_per_file=2,
            base_name="bad",
            batch_size=3,  # > n_df_per_file
            polars_schema=schema,
        )


def test_batching_coalesces_row_groups(tmp_path: Path):
    schema = _get_sample_pa_table().schema
    w = StreamingParquetWriters(
        base_path=tmp_path,
        number_of_dataframes_per_file=100,
        base_name="job_batch",
        batch_size=3,
        polars_schema=schema,
    )
    rel = "up00/"
    for _ in range(5):  # 3 + 2 → two row groups total
        w.write(rel, _get_sample_pa_table())
    w.close_all()

    p_000 = tmp_path / "up00" / "job_batch-000.parquet"
    assert p_000.exists()
    assert pq.ParquetFile(p_000).num_row_groups == 2
