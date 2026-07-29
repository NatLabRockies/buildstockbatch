=====================
Development Changelog
=====================

.. changelog::
    :version: development
    :released: It has not been released

    .. change::
        :tags: general, feature
        :pullreq: 101
        :tickets: 101

        This is an example change. Please copy and paste it - for valid tags please refer to ``conf.py`` in the docs
        directory. ``pullreq`` should be set to the appropriate pull request number and ``tickets`` to any related
        github issues. These will be automatically linked in the documentation.


    .. change::
        :tags: WorkflowGenerator, feature
        :pullreq: 480

        This PR adds a new version of the WorkflowGenerator for ResStock and ComStock that passes the buildstock_directory
        argument to BuildExistingModel and ApplyUpgrade measure. This is in support of the change in ResStock
        (and potentially in ComStock) to get rid of the lib folder.


    .. change::
        :tags: WorkflowGenerator, feature
        :pullreq: 483

        This PR adds the necessary copying of project_direcotry in HPC so that BSB can work in Kestrel.


    .. change::
        :tags: general, feature
        :pullreq: 490

        When running ``buildstock_local``, especially in CI, it is useful to be able to use minimal disk space.
        This PR adds a ``low-disk`` flag to use minimal disk space.


    .. change::
        :tags: postprocessing, feature
        :pullreq: 492


        Added support for publishing annual results in the postprocessing step. When enabled via the ``publish_annual_results``
        configuration option, the system will generate additional processed results in both CSV and Parquet formats.
        For resstock projects, this functionality leverages the ``resstockpostproc`` module's publishing functions.

    .. change::
        :tags: postprocessing, feature
        :pullreq: 499


        Added eplusout_err column in the results_csv to provide visibility into EnergyPlus warnings and errors. Especially useful
        for failed simulations.

    .. change::
        :tags: postprocessing, feature
        :pullreq: 500

        Added support for replacing existing results in s3 for buildstock_local. When --replace_existing is passed to buildstock_local,
        it will replace existing results in s3.

    .. change::
        :tags: postprocessing, feature
        :pullreq: 501

        Introduce a bunch of changes originating from issues in SDR run.
        1. Intermediate files are not cleaned up after postprocessing. This allows for re-running postprocessing.
        2. eplusout_err and step_failures are truncated to 100000 chars. eplusout_err is only collected if E+ terminated.
        3. Default for include_annual_emission_end_uses is changed to False.
        4. Default for timeseries_num_decimal_places is changed to 5.
        5. When SimulationOutputReport or ReportSimulationOutput is both missing in data_point_out.json (perhaps due to failure),
        add a key of for ReportSimulationOutput instead of SimulationOutputReport as the later is outdated.

    .. change::
        :tags: general, feature
        :pullreq: 517

        Adds ``include_annual_foo`` arguments to the Residential HPXML Workflow Generator.

    .. change::
        :tags: aws, feature
        :pullreq: 521

        Adds optional ``aws.base_dockerfile`` and ``aws.base_target`` configuration options. When set,
        buildstockbatch builds the specified Dockerfile from the buildstock directory (e.g. ComStock's
        ``build/Dockerfile``) and uses the resulting image as the base for the buildstockbatch Docker
        image, in place of the stock ``nrel/openstudio`` image. This allows running projects like
        ComStock, whose simulation environment includes additional python dependencies and custom gems,
        on AWS without publishing their image to a registry.

    .. change::
        :tags: aws, gcp, bugfix
        :pullreq: 521

        Fixes the cloud Dockerfile so buildstockbatch is actually present in the built image. The
        python venv was previously created in the image's working directory, ``/var/simdata/openstudio``,
        which the ``nrel/openstudio`` base image declares as a ``VOLUME`` -- files written there during
        the build are discarded, so ``python3 -m buildstockbatch...`` failed inside the container. The
        venv now lives at ``/buildstock-batch/.venv``, the AWS/GCP job commands reference its interpreter
        explicitly, and uv no longer installs a bare ``python3.11`` shim that would shadow a base image's
        own ``python3.11`` (which, for ComStock, has PySAM and other measure dependencies installed).
        Also adds ``dask-scheduler``/``dask-worker`` compatibility shims, since dask_cloudprovider
        launches Fargate postprocessing containers with those legacy commands, which modern
        distributed no longer installs.

    .. change::
        :tags: aws, bugfix
        :pullreq: 521

        Applies the ``aws.tags`` configuration to AWS resources that previously weren't tagged:
        IAM roles, the Batch instance profile, the ECR repository, and the Batch security group.
        Job definitions now set ``propagateTags`` so the tags also reach the ECS tasks launched
        for each simulation job.
