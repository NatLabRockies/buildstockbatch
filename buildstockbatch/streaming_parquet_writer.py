# streaming_parquet_writer.py
from __future__ import annotations
from pathlib import Path
import atexit, signal, sys, weakref

import pyarrow as pa
import pyarrow.parquet as pq
import polars as pl


class SingleWriter:
    def __init__(
        self, polars_schema: dict, path: Path, base_name: str, number_of_dataframes_per_file: int, batch_size: int
    ):
        empty_df = pl.DataFrame(schema=polars_schema)
        self.schema = empty_df.to_arrow().schema

        self.path = path
        self.base_name = base_name
        self.part = -1  # file part number (will be incremented 0, 1, 2, ...)
        self.count = 0  # number of dataframes written by this writer to current file
        self.n_df_per_file = number_of_dataframes_per_file
        self.pq_writer = self._start_new_file_part()
        self.batch_size = batch_size
        self.buffer: list[pa.Table] = []

    def _get_file_path(self):
        return self.path / f"{self.base_name}-{self.part:03d}.parquet"

    def _start_new_file_part(self):
        self.part += 1
        self.count = 0
        path = self._get_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return pq.ParquetWriter(where=str(path), schema=self.schema)

    def write(self, df: pl.DataFrame):
        table = df.to_arrow()
        if self.pq_writer is None:
            self.pq_writer = self._start_new_file_part()
        self.buffer.append(table)
        if len(self.buffer) >= self.batch_size:
            self.flush()
        self.count += 1
        if self.count >= self.n_df_per_file:
            self.flush()
            self.pq_writer.close()
            self.pq_writer = None

    def flush(self):
        if self.buffer:
            if self.pq_writer is None:
                self.pq_writer = self._start_new_file_part()
            batch_df = pa.concat_tables(self.buffer)
            self.pq_writer.write_table(batch_df)
            self.buffer.clear()

    def close(self):
        self.flush()
        if self.pq_writer is not None:
            self.pq_writer.close()


class StreamingParquetWriters:
    _instances: "weakref.WeakSet[StreamingParquetWriters]" = weakref.WeakSet()
    _signals_installed: bool = False

    def __init__(
        self,
        base_path: str | Path,
        base_name: str,
        number_of_dataframes_per_file: int,
        batch_size: int,  # These many dataframes are held in buffer and written as a single row group
        polars_schema: dict,
    ):
        self.base_path = Path(base_path)
        self.n_df_per_file = int(number_of_dataframes_per_file)
        self.batch_size = int(batch_size)
        if self.batch_size > self.n_df_per_file:
            raise ValueError("batch_size must be less than or equal to number_of_dataframes_per_file")
        self.base_name = base_name
        self.polars_schema = polars_schema
        self.path2writer: dict[str, SingleWriter] = {}
        StreamingParquetWriters._instances.add(self)
        self._install_signals()

    # public API
    def write(self, rel_path: str, df: pl.DataFrame) -> None:
        rel_path = rel_path.rstrip("/")
        path = self.base_path / rel_path
        key = str(path / f"{self.base_name}.parquet")
        writer = self.path2writer.get(key)
        if writer is None:
            writer = SingleWriter(
                polars_schema=self.polars_schema,
                path=path,
                base_name=self.base_name,
                number_of_dataframes_per_file=self.n_df_per_file,
                batch_size=self.batch_size,
            )
            self.path2writer[key] = writer
        writer.write(df)

    def close_all(self) -> None:
        for writer in self.path2writer.values():
            writer.close()
        self.path2writer.clear()

    @classmethod
    def _install_signals(cls) -> None:
        # Install signal handlers to close all writers on SIGTERM and SIGINT (such as timeout)
        if cls._signals_installed:
            return

        def _shutdown(sig, _frm):
            for inst in list(cls._instances):
                try:
                    inst.close_all()
                except:
                    pass
            sys.exit(128 + sig)

        atexit.register(lambda: [_inst.close_all() for _inst in list(cls._instances)])
        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)
        cls._signals_installed = True
