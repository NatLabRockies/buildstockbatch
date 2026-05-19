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
        :tags: postprocessing, feature, breaking
        :pullreq: 492


        **Updated** and **Breaking Change**: Refactored the ``publish_annual_results`` postprocessing feature to use the new
        ``export_metadata_and_annual_results`` function from ``resstockpostproc.process_bsb_results``. The new implementation:
        
        - Generates comprehensive processed results with geographic partitioning (national and by_state)
        - Creates new output structure: ``metadata_and_annual_results/`` and ``cached_simulation_outputs/``
        - Requires Python 3.12+ (BuildStockBatch now requires Python 3.12 globally)
        - Requires ``resstockpostproc`` package (install with ``pip install buildstockbatch[resstock]``)
        - **Breaking**: No longer creates ``results_csvs_pub`` and ``pub_annual`` directories
        
        The old inline publishing functions (``publish_baseline_annual_results`` and ``publish_upgrade_annual_results``) 
        are no longer used. The new approach runs as a separate postprocessing stage after the main combine_results loop.

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
