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


        Added eplusout_err column in the results_csv to provide visibility into Energplus warnings and errors. Especially useful
        for failed simulations.
