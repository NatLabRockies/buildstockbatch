.. |version| replace:: development

=======================================
What's new in buildstockbatch |version|
=======================================

.. admonition:: About this Document

    This document describes changes between buildstockbatch version 2022.11.0 and
    buildstockbatch version |version|

General
=======

This version should be backwards compatible with previous versions of
buildstockbatch.

See :doc:`changelog_dev` for details of this change.

New Flags
=========
A new flag ``low-disk`` has been added to ``buildstock_local`` to delete unused results and minimize hard-drive space used by buildstockbatch.

.. code-block:: text

  buildstock_local --help

  usage: buildstock_local [-h] [-j J] [-m] [--low-disk] [--postprocessonly | --uploadonly | --continue_upload | --validateonly | --samplingonly] project_filename

  positional arguments:
    project_filename

  options:
    -h, --help           show this help message and exit
    -j J                 Number of parallel simulations. Default: all cores.
    -m, --measures_only  Only apply the measures, but don't run simulations. Useful for debugging.
    --low-disk           Delete unused simulation result files immediately after processing to save disk space.
    --postprocessonly    Only do postprocessing, useful for when the simulations are already done
    --uploadonly         Only upload to S3, useful when postprocessing is already done. Ignores the upload flag in yaml. Errors out if files already exists in s3
    --continue_upload    Continue uploading to S3, useful when previous upload was interrupted.
    --validateonly       Only validate the proj

Schema Updates
==============

Added the ``publish_annual_results`` configuration option to the postprocessing section in project yaml. When set to ``true``, additional processed annual results will be generated in both CSV and Parquet formats. This functionality, which only works for ResStock, leverages the ``resstockpostproc`` module's publishing functions. The processed results are stored in a ``results_csvs_pub`` directory and a ``pub_annual`` subdirectory within the parquet directory. Default is ``false``.
