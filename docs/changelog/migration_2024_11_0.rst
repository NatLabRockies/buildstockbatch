.. |version| replace:: v2024.11.0

=======================================
What's new in buildstockbatch |version|
=======================================

.. admonition:: About this Document

    This document describes changes between buildstockbatch version 2023.11.0 and
    buildstockbatch version |version|

Major Changes
=============

Below are the major changes in this release.

Eagle is retired
----------------

This version should be backwards compatible with previous versions of
buildstockbatch except for being able to run in Eagle. Since Eagle is retired, BuildStockBatch no
longer supports buildstock_eagle command. If you are just comming upto speed with Kestrel, you can
use your existing yaml file by replacing 'eagle' with 'kestrel' and using buildstock_kestrel
command instead of buildstock_eagle command. See how to run in Kestrel :ref:`kestrel-run`.

BuildStockBatch uses Python 3.11
--------------------------------

BuildStockBatch now uses Python 3.11. If you are using a version of Python
older than 3.11 in your local buildstockbatch environment, you will need to upgrade the python
version or create a new environment with python 3.11 (or 3.12).

Timeseries files include schedules
----------------------------------

In ResStock runs, the schedules are now appended to the timeseries files.

WorkflowGenerator is versioned
------------------------------

`The workflow generator needs a version specification. <https://github.com/NREL/resstock/blob/v3.3.0/project_national/national_baseline.yml#L17>`_
Older yaml files without version specification will default to version 2024.07.18 (the assigned version before versioning was introduced).
If you are using a custom branch of ResStock, you should use the same version of the workflow generator as that in the ResStock branch
it is based on.

Google Cloud Platform (GCP) support
-----------------------------------

BuildStockBatch now supports GCP. See :ref:`gcp-config` and  :ref:`gcp-run`.


See :doc:`changelog_2024_11_0` for full details of what changed.


