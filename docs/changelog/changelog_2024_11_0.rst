====================
v2024.11.0 Changelog
====================

.. changelog::
    :version: v2024.11.0
    :released: 2024-11-05


    .. change::
        :tags: general
        :pullreq: 421

        Refactor docker_base to use inversion of control so that it can more strongly and easily ensure consistency
        between various implementations (GCP implementation to come). This also includes teasing apart the several batch
        prep steps (weather, assets, and jobs) into their own methods so they can each be more easily understood,
        shared, and maintained.

    .. change::
        :tags: general
        :pullreq: 422

        Refactor AWS code so it can be shared by the upcoming GCP implementation.

    .. change::
        :tags: general, bugfix
        :pullreq: 426

        A bugfix for gracefully handling empty data_point_out.json files.

    .. change::
        :tags: aws, feature
        :pullreq: 345

        Major update to get AWS Batch run environment working.

    .. change::
        :tags: general
        :pullreq: 435

        Add helper to log a summary of how many simulations succeeded and failed at the end of a job.

    .. change::
        :tags: general, feature
        :pullreq: 437

        Add a ``step_failures`` section to json results files with error messages from OpenStudio simulations.

    .. change::
        :tags: general
        :pullreq: 436

        Clean up handling of weather files in GCP/AWS implementations: only upload files that are required,
        and fail with clearer messaging if any files are missing.

    .. change::
        :tags: general, feature
        :pullreq: 423

        Add GCP implementation.

    .. change::
        :tags: bugfix, schema
        :pullreq: 450

        Requires ``os_version`` and ``os_sha`` in the project file.

    .. change::
        :tags: general
        :pullreq: 456

        Refactor WorkflowGenerator.

    .. change::
        :tags: general, feature
        :pullreq: 458

        Add a new version (2024.07.19) of the Residential HPXML Workflow Generator that
        changes UpgradeCosts from reporting measure to a regular measure. ReportHPXMLOutput
        is no longer called. This feature is created to support the corresponding update in
        ResStock (https://github.com/NREL/resstock/pull/1253)

        To facilitate the creation of new version, the workflow generator code base is refactored
        to have one folder for each version. New yaml schema (v0.4) is created that defines a
        `version` key for the `workflow_generator` block. The base class loads the appropriate
        version of the workflow_generator based on the version key. If version key is missing
        (when using older schema), it is defaulted to the oldest available version (2024.07.18).

    .. change::
        :tags: general, feature
        :pullreq: 461

        Add a new version (2024.07.20) of the Residential HPXML Workflow Generator that
        exposes optional ``include_annual_bills`` (defaults to true) and
        ``include_monthly_bills`` (defaults to false) arguments for reporting annual
        and monthly utility bill outputs, respectively.

    .. change::
        :tags: general, bugfix
        :pullreq: 464

        Stop creating dask _metadata files for the timeseries parquet files since it crashes the
        postprocessing.

    .. change::
        :tags: bugfix, hpc
        :pullreq: 467

        Updates the bind mount for apptainer to include the whole resources
        directory instead of just the hpxml-measures directory. Makes it work
        with newer versions of ResStock.

    .. change::
        :tags: general, bugfix
        :pullreq: 466

        Add ``--continue_upload`` flag to allow only uploading the missing files to s3.

    .. change::
        :tags: general
        :pullreq: 472

        Remove support for Python 3.8, 3.9, and 3.10. Add support for Python 3.12.

    .. change::
        :tags: general
        :pullreq: 472

        Remove eagle related code and references since eagle is retired.

    .. change::
        :tags: general
        :pullreq: 465

        Append schedules to the timeseries files.